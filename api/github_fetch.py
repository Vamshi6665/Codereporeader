"""
github_fetch.py
===============
Downloads a public (or token-authorized) GitHub repository as a tarball and
extracts it to a temporary directory.

This replaces the original `git clone` approach, which does not work on Vercel's
serverless Python runtime (no `git` binary, read-only filesystem except /tmp).
Everything here runs over plain HTTPS via `requests`, so it works anywhere.
"""

import os
import re
import io
import tarfile
import tempfile

import requests

GITHUB_API = "https://api.github.com"


def parse_github_url(url):
    """
    Accepts forms like:
        https://github.com/owner/repo
        https://github.com/owner/repo.git
        https://github.com/owner/repo/tree/branch
        git@github.com:owner/repo.git
    Returns (owner, repo, ref_or_None).
    """
    url = url.strip()

    # git@github.com:owner/repo.git
    m = re.match(r"git@github\.com:([^/]+)/(.+?)(?:\.git)?/?$", url)
    if m:
        return m.group(1), m.group(2), None

    # https://github.com/owner/repo[/tree/ref][...]
    m = re.match(
        r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/tree/([^/]+))?/?$",
        url,
    )
    if m:
        return m.group(1), m.group(2), m.group(3)

    raise ValueError("Not a recognized GitHub URL. Use https://github.com/owner/repo")


def download_repo_tarball(url, token=None, max_bytes=60 * 1024 * 1024):
    """
    Download the repo tarball and extract it into a fresh temp dir.
    Returns the path to the extracted project root.

    `token` (optional) is a GitHub PAT — raises rate limits and enables private
    repos. `max_bytes` guards against huge repos filling /tmp.
    """
    owner, repo, ref = parse_github_url(url)

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "CodeRepoReader"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # If no ref was given in the URL, resolve the default branch.
    if not ref:
        r = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}", headers=headers, timeout=30
        )
        if r.status_code == 404:
            raise ValueError(f"Repository not found: {owner}/{repo}")
        if r.status_code == 403:
            raise ValueError(
                "GitHub API rate limit hit. Add a token in the sidebar to raise the limit."
            )
        r.raise_for_status()
        ref = r.json().get("default_branch", "main")

    tar_url = f"{GITHUB_API}/repos/{owner}/{repo}/tarball/{ref}"
    resp = requests.get(tar_url, headers=headers, stream=True, timeout=90)
    if resp.status_code == 404:
        raise ValueError(f"Could not download {owner}/{repo}@{ref} (not found).")
    if resp.status_code == 403:
        raise ValueError(
            "GitHub API rate limit hit. Add a token in the sidebar to raise the limit."
        )
    resp.raise_for_status()

    # Read the tarball into memory with a size cap.
    buf = io.BytesIO()
    total = 0
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                "Repository is too large to analyze on the serverless runtime "
                f"(> {max_bytes // (1024 * 1024)} MB). Try a smaller repo."
            )
        buf.write(chunk)
    buf.seek(0)

    tmp_dir = tempfile.mkdtemp(prefix="codereporeader-")
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        _safe_extractall(tar, tmp_dir)

    # GitHub tarballs extract into a single top folder: owner-repo-<sha>/
    entries = [e for e in os.listdir(tmp_dir) if not e.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(tmp_dir, entries[0])):
        return os.path.join(tmp_dir, entries[0]), tmp_dir
    return tmp_dir, tmp_dir


def _safe_extractall(tar, dest):
    """Extract a tarball, refusing any member that escapes the destination dir."""
    dest_abs = os.path.abspath(dest)
    for member in tar.getmembers():
        member_path = os.path.abspath(os.path.join(dest, member.name))
        if not member_path.startswith(dest_abs + os.sep) and member_path != dest_abs:
            raise ValueError(f"Unsafe path in archive: {member.name}")
    tar.extractall(dest)
