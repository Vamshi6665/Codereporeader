"""
repo_source.py
==============
Loads a repository into memory from one of three sources:

  * a public GitHub repository (resolved to an exact commit, downloaded as a tarball)
  * an uploaded ZIP archive
  * a local folder (used by the CLI)

Nothing is extracted to disk. Archives are read member by member, filtered, and
decoded into a ``VirtualRepo`` — a list of every path plus the text of the files
worth analyzing. That keeps the serverless function fast, avoids path-traversal
risks, and makes the analysis deterministic for a given commit (which is what lets
the CDN cache it).
"""

from __future__ import annotations

import io
import os
import re
import tarfile
import zipfile
from dataclasses import dataclass, field

import requests

GITHUB_API = "https://api.github.com"
CODELOAD = "https://codeload.github.com"
USER_AGENT = "CodeRepoReader/2.0 (+https://github.com/Vamshi6665/codereporeader)"

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_ARCHIVE_BYTES = int(os.environ.get("MAX_ARCHIVE_MB", "80")) * 1024 * 1024
MAX_PATHS = 25_000                    # paths listed in the tree
MAX_TEXT_FILE_BYTES = 400 * 1024      # larger files are listed but not read
MAX_TOTAL_TEXT_BYTES = 60 * 1024 * 1024

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "__pycache__",
    ".venv", "venv", "env", ".env", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "dist", "build", "out", ".next", ".nuxt", ".svelte-kit",
    ".cache", ".parcel-cache", ".turbo", ".vercel", ".idea", ".vscode",
    "coverage", "htmlcov", "target", "bin", "obj", ".gradle", "vendor",
    "Pods", "DerivedData", ".terraform", "site-packages", ".dart_tool",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".icns", ".webp", ".avif",
    ".tif", ".tiff", ".psd", ".ai", ".sketch", ".fig", ".pdf", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".tar", ".gz", ".tgz", ".bz2",
    ".xz", ".7z", ".rar", ".jar", ".war", ".ear", ".class", ".o", ".a", ".so",
    ".dylib", ".dll", ".exe", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".pyc", ".pyo", ".whl", ".egg", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".m4a", ".wav", ".ogg", ".flac", ".mov", ".avi", ".mkv",
    ".webm", ".wasm", ".pkl", ".pickle", ".npy", ".npz", ".h5", ".hdf5",
    ".onnx", ".pt", ".pth", ".ckpt", ".safetensors", ".parquet", ".feather",
    ".avro", ".orc", ".keystore", ".jks", ".p12", ".pfx", ".der", ".iso", ".dmg",
}

# Listed in the tree, never read: generated, huge, or useless to an LLM.
OPAQUE_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "poetry.lock",
    "Pipfile.lock", "Cargo.lock", "composer.lock", "Gemfile.lock", "go.sum",
    "mix.lock", "flake.lock", "pubspec.lock", "packages.lock.json", "uv.lock",
}

# Secrets must never be read, sent to a model, or cached at the edge.
SECRET_PATTERNS = re.compile(
    r"(^|/)(\.env(\..+)?|.*\.pem|.*\.key|id_rsa.*|id_ed25519.*|\.npmrc|\.pypirc|"
    r"credentials(\.json)?|.*secrets?\.(json|ya?ml|toml))$",
    re.IGNORECASE,
)
SAFE_ENV_EXAMPLES = re.compile(r"\.env\.(example|sample|template|dist)$", re.IGNORECASE)


class RepoError(Exception):
    """A user-facing problem (bad URL, repo not found, too large…)."""

    def __init__(self, message: str, status: int = 400, code: str = "bad_request"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


@dataclass
class VirtualRepo:
    name: str
    paths: list = field(default_factory=list)      # [{"path", "size", "kind"}]
    texts: dict = field(default_factory=dict)      # path -> str
    truncated: bool = False                        # hit MAX_PATHS / text limits

    def add(self, path: str, size: int, data_reader):
        """Register one archive member. ``data_reader`` returns bytes on demand."""
        if len(self.paths) >= MAX_PATHS:
            self.truncated = True
            return
        kind = classify(path, size)
        self.paths.append({"path": path, "size": size, "kind": kind})
        if kind != "text":
            return
        if self._text_bytes + size > MAX_TOTAL_TEXT_BYTES:
            self.truncated = True
            self.paths[-1]["kind"] = "skipped"
            return
        raw = data_reader()
        if raw is None:
            return
        if b"\x00" in raw[:8192]:
            self.paths[-1]["kind"] = "binary"
            return
        text = raw.decode("utf-8", errors="replace")
        if _looks_minified(path, text):
            self.paths[-1]["kind"] = "generated"
            return
        self.texts[path] = text.replace("\r\n", "\n")
        self._text_bytes += size

    _text_bytes: int = 0


def classify(path: str, size: int) -> str:
    base = path.rsplit("/", 1)[-1]
    ext = os.path.splitext(base)[1].lower()
    if SECRET_PATTERNS.search(path) and not SAFE_ENV_EXAMPLES.search(path):
        return "secret"
    if ext in BINARY_EXTENSIONS:
        return "binary"
    if base in OPAQUE_FILES:
        return "generated"
    if size > MAX_TEXT_FILE_BYTES:
        return "large"
    if base.endswith((".min.js", ".min.css", ".map", ".bundle.js", ".chunk.js")):
        return "generated"
    return "text"


def _looks_minified(path: str, text: str) -> bool:
    if not path.endswith((".js", ".css", ".mjs", ".cjs", ".json", ".svg", ".html")):
        return False
    lines = text.count("\n") + 1
    return len(text) > 20_000 and len(text) / lines > 500


def _skip_path(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in SKIP_DIRS for p in parts[:-1])


# ── GitHub ────────────────────────────────────────────────────────────────────
_GH_PATTERNS = [
    re.compile(r"^git@github\.com:(?P<o>[\w.-]+)/(?P<r>[\w.-]+?)(?:\.git)?/?$"),
    re.compile(
        r"^(?:https?://)?(?:www\.)?github\.com/(?P<o>[\w.-]+)/(?P<r>[\w.-]+?)(?:\.git)?"
        r"(?:/(?:tree|blob|commit)/(?P<ref>[^/?#]+)(?:/[^?#]*)?)?/?(?:[?#].*)?$"
    ),
    re.compile(r"^(?P<o>[\w.-]+)/(?P<r>[\w.-]+?)(?:\.git)?$"),  # owner/repo shorthand
]


def parse_github_url(url: str):
    """Return (owner, repo, ref_or_None) for any common GitHub URL form."""
    url = (url or "").strip()
    for pat in _GH_PATTERNS:
        m = pat.match(url)
        if m:
            ref = m.groupdict().get("ref")
            return m.group("o"), m.group("r"), ref
    raise RepoError("That doesn't look like a GitHub repository. Use a link like https://github.com/owner/repo.")


def _gh_headers(accept="application/vnd.github+json"):
    headers = {"Accept": accept, "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _gh_error(resp, owner, repo):
    if resp.status_code == 404:
        return RepoError(f"{owner}/{repo} wasn't found. Check the link, and note that private repositories aren't supported. Upload a ZIP instead.", 404, "not_found")
    if resp.status_code in (403, 429):
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining == "0" or resp.status_code == 429:
            return RepoError("GitHub's rate limit was reached. Try again in a few minutes.", 429, "github_rate_limited")
        return RepoError("GitHub refused the request for this repository.", 403, "github_forbidden")
    return RepoError(f"GitHub returned an error ({resp.status_code}). Try again shortly.", 502, "github_error")


def resolve_github(url: str) -> dict:
    """Resolve a GitHub URL to an exact commit SHA plus display metadata."""
    owner, repo, ref = parse_github_url(url)
    try:
        r = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=_gh_headers(), timeout=15)
    except requests.RequestException as e:
        raise RepoError(f"Couldn't reach GitHub: {e.__class__.__name__}", 502, "github_unreachable")
    if r.status_code != 200:
        raise _gh_error(r, owner, repo)
    info = r.json()
    if info.get("private"):
        raise RepoError("Private repositories aren't supported. Upload a ZIP of the code instead.", 403, "private_repo")
    owner, repo = info["owner"]["login"], info["name"]   # canonical casing
    ref = ref or info.get("default_branch") or "main"

    c = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/commits/{ref}",
        headers=_gh_headers("application/vnd.github.sha"),
        timeout=15,
    )
    if c.status_code == 422 or c.status_code == 404:
        raise RepoError(f"The branch or tag “{ref}” wasn't found in {owner}/{repo}.", 404, "ref_not_found")
    if c.status_code != 200:
        raise _gh_error(c, owner, repo)
    sha = c.text.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RepoError("GitHub returned an unexpected commit id.", 502, "github_error")

    return {
        "source": "github",
        "owner": owner,
        "repo": repo,
        "full_name": f"{owner}/{repo}",
        "ref": ref,
        "sha": sha,
        "default_branch": info.get("default_branch"),
        "description": info.get("description") or "",
        "stars": info.get("stargazers_count", 0),
        "html_url": info.get("html_url") or f"https://github.com/{owner}/{repo}",
        "size_kb": info.get("size", 0),
    }


def _download(url, headers):
    resp = requests.get(url, headers=headers, stream=True, timeout=(10, 120), allow_redirects=True)
    if resp.status_code != 200:
        return resp, None
    buf = io.BytesIO()
    total = 0
    for chunk in resp.iter_content(chunk_size=256 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_ARCHIVE_BYTES:
            resp.close()
            raise RepoError(
                f"This repository is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB compressed, "
                "which is more than the analyzer can handle in one request.", 413, "repo_too_large")
        buf.write(chunk)
    return resp, buf.getvalue()


def load_github(owner: str, repo: str, sha: str) -> VirtualRepo:
    """Download the tarball for an exact commit and load it into memory."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        raise RepoError("A full 40-character commit SHA is required.", 400, "bad_sha")
    if not re.fullmatch(r"[\w.-]+", owner or "") or not re.fullmatch(r"[\w.-]+", repo or ""):
        raise RepoError("Invalid repository name.", 400, "bad_repo")

    # The analysis endpoint is CDN-cached and public, so never serve a private repository,
    # even if the server's GitHub token happens to have access to it.
    if os.environ.get("GITHUB_TOKEN", "").strip():
        try:
            info = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=_gh_headers(), timeout=15)
        except requests.RequestException as e:
            raise RepoError(f"Couldn't reach GitHub: {e.__class__.__name__}", 502, "github_unreachable")
        if info.status_code != 200:
            raise _gh_error(info, owner, repo)
        if info.json().get("private"):
            raise RepoError("Private repositories aren't supported. Upload a ZIP of the code instead.", 403, "private_repo")

    # codeload serves archives without spending REST API quota; fall back to the API.
    data = None
    try:
        _, data = _download(f"{CODELOAD}/{owner}/{repo}/tar.gz/{sha}", {"User-Agent": USER_AGENT})
    except requests.RequestException:
        data = None
    if data is None:
        try:
            resp, data = _download(f"{GITHUB_API}/repos/{owner}/{repo}/tarball/{sha}", _gh_headers())
        except requests.RequestException as e:
            raise RepoError(f"Couldn't download the repository: {e.__class__.__name__}", 502, "download_failed")
        if data is None:
            raise _gh_error(resp, owner, repo)
    return load_tar_bytes(data, name=repo)


def load_tar_bytes(data: bytes, name: str) -> VirtualRepo:
    vr = VirtualRepo(name=name)
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError:
        raise RepoError("The downloaded archive couldn't be read.", 502, "bad_archive")
    with tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        prefix = _common_prefix([m.name for m in members])
        for m in members:
            rel = _clean_rel(m.name, prefix)
            if not rel or _skip_path(rel):
                continue
            vr.add(rel, m.size, lambda m=m: _read_tar_member(tar, m))
    vr.paths.sort(key=lambda p: p["path"])
    return vr


def _read_tar_member(tar, member):
    f = tar.extractfile(member)
    return f.read() if f else None


def load_zip_bytes(data: bytes, name: str) -> VirtualRepo:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise RepoError("That file isn't a valid ZIP archive.", 400, "bad_zip")
    vr = VirtualRepo(name=name)
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if sum(i.file_size for i in infos) > 600 * 1024 * 1024:
            raise RepoError("The ZIP expands to more than 600 MB.", 413, "zip_too_large")
        prefix = _common_prefix([i.filename for i in infos])
        if prefix:
            vr.name = prefix.rstrip("/").split("/")[-1] or name
        for info in infos:
            rel = _clean_rel(info.filename, prefix)
            if not rel or _skip_path(rel) or rel.startswith("__MACOSX/"):
                continue
            vr.add(rel, info.file_size, lambda info=info: zf.read(info))
    vr.paths.sort(key=lambda p: p["path"])
    return vr


def load_local_dir(root: str) -> VirtualRepo:
    root = os.path.abspath(root)
    vr = VirtualRepo(name=os.path.basename(root))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                continue

            def reader(full=full):
                with open(full, "rb") as fh:
                    return fh.read()

            vr.add(rel, size, reader)
    return vr


def _common_prefix(names):
    """GitHub tarballs and most ZIPs wrap everything in one top-level folder."""
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(tops) == 1 and all("/" in n for n in names):
        return tops.pop() + "/"
    return ""


def _clean_rel(name: str, prefix: str) -> str:
    name = name.replace("\\", "/")
    if prefix and name.startswith(prefix):
        name = name[len(prefix):]
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return ""
    return "/".join(parts)
