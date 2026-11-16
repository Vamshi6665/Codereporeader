"""
CodeRepoReader API — one Flask app deployed as a Vercel Python function.

Routes
------
GET  /api/health            liveness + which optional features are configured (never secrets)
GET  /api/config            model labels for the UI
GET  /api/resolve?url=      GitHub URL -> exact commit SHA and display metadata
GET  /api/analyze?...&sha=  full analysis for one commit; immutable, so the CDN caches it
POST /api/analyze/upload    same analysis for an uploaded ZIP (never cached)
POST /api/chat              Server-Sent Events stream of the answer

The OpenAI key is read only from the environment. No request field can change the
key or the endpoint the server talks to.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import traceback
from urllib.parse import urlparse

# The shared code lives in /server (outside /api, so Vercel deploys only this one function).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, Response, jsonify, request, stream_with_context  # noqa: E402

from server import llm, ratelimit  # noqa: E402
from server.repo_analysis import analyze, build_file_pack, build_repo_map  # noqa: E402
from server.repo_source import RepoError, load_github, load_zip_bytes, resolve_github  # noqa: E402

ANALYZER_VERSION = "2"
MAX_UPLOAD_BYTES = int(4.3 * 1024 * 1024)       # Vercel caps request bodies at 4.5 MB
MAX_CHAT_BODY = 2 * 1024 * 1024
MAX_ANALYSIS_RESPONSE = 4_000_000               # Vercel caps response bodies at 4.5 MB
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()}

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 256 * 1024
LIMITS = ratelimit.limits_from_env()


# ── helpers ───────────────────────────────────────────────────────────────────
def error(code: str, message: str, status: int = 400, **extra):
    resp = jsonify({"error": {"code": code, "message": message, **extra}})
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


def client_ip() -> str:
    for h in ("x-real-ip", "x-vercel-forwarded-for", "x-forwarded-for"):
        v = request.headers.get(h, "")
        if v:
            return v.split(",")[0].strip()[:64]
    return request.remote_addr or "unknown"


def same_origin_ok() -> bool:
    """Block other websites from calling the API from their visitors' browsers."""
    origin = request.headers.get("Origin")
    if not origin:
        return True  # same-origin GETs, curl, server-to-server
    origin = origin.rstrip("/")
    if origin in ALLOWED_ORIGINS:
        return True
    host = (request.headers.get("x-forwarded-host") or request.host or "").lower()
    return urlparse(origin).netloc.lower() == host


def limited(bucket, limit, window):
    ok, retry = ratelimit.hit(bucket, client_ip(), limit, window)
    if ok:
        return None
    resp = error("rate_limited", f"You're sending requests too quickly. Try again in {retry} seconds.", 429, retry_after=retry)
    resp.headers["Retry-After"] = str(retry)
    return resp


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


@app.errorhandler(413)
def too_large(_e):
    return error("too_large", "That upload is larger than 4.3 MB. For bigger projects, push them to GitHub and paste the link.", 413)


@app.errorhandler(404)
def not_found(_e):
    return error("not_found", "No such API route.", 404)


@app.errorhandler(405)
def bad_method(_e):
    return error("method_not_allowed", "That method isn't supported on this route.", 405)


@app.errorhandler(Exception)
def unhandled(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return error("http_error", e.description or "Request failed.", e.code or 400)
    traceback.print_exc()
    return error("internal", "Something went wrong on the server. Try again.", 500)


# ── meta routes ───────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "ai_configured": llm.is_configured(),
        "github_token": bool(os.environ.get("GITHUB_TOKEN", "").strip()),
        "rate_limit_backend": ratelimit.backend(),
        "analyzer_version": ANALYZER_VERSION,
        "python": sys.version.split()[0],
    })


@app.get("/api/config")
def config():
    resp = jsonify({**llm.public_config(), "analyzer_version": ANALYZER_VERSION,
                    "limits": {k: v for k, v in LIMITS.items() if not k.startswith("global")},
                    "max_upload_mb": round(MAX_UPLOAD_BYTES / 1024 / 1024, 1)})
    resp.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"
    return resp


# ── analysis ──────────────────────────────────────────────────────────────────
def _fit(payload: dict) -> str:
    """Serialize, dropping the lowest-priority file contents until under the response cap."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    files = payload["files"]
    while len(body.encode("utf-8")) > MAX_ANALYSIS_RESPONSE and files:
        drop = max(1, len(files) // 10)
        del files[-drop:]
        payload["pack"]["omitted_files"] += drop
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return body


def _build_payload(meta: dict, vr) -> dict:
    a = analyze(vr)
    repo_map = build_repo_map(meta, a, vr.paths)
    files, pack = build_file_pack(vr, a)
    a.pop("file_lines", None)
    for s in a["symbols"]:
        if s.get("doc"):
            s["doc"] = s["doc"][:120]
    return {
        "version": ANALYZER_VERSION,
        "repo": meta,
        "analysis": a,
        "map": repo_map,
        "tree": vr.paths[:15000],
        "files": files,
        "pack": pack,
    }


@app.get("/api/resolve")
def resolve():
    if (r := limited("resolve", LIMITS["analyze_per_10min"] * 3, 600)):
        return r
    url = request.args.get("url", "").strip()
    if not url:
        return error("bad_request", "Paste a GitHub repository link.")
    try:
        meta = resolve_github(url)
    except RepoError as e:
        return error(e.code, e.message, e.status)
    resp = jsonify(meta)
    resp.headers["Cache-Control"] = "public, max-age=0, s-maxage=60, stale-while-revalidate=300"
    return resp


@app.get("/api/analyze")
def analyze_github():
    owner = request.args.get("owner", "")
    repo = request.args.get("repo", "")
    sha = request.args.get("sha", "").lower()
    ref = request.args.get("ref", "")[:200]
    if not (re.fullmatch(r"[\w.-]{1,100}", owner) and re.fullmatch(r"[\w.-]{1,100}", repo)
            and re.fullmatch(r"[0-9a-f]{40}", sha)):
        return error("bad_request", "owner, repo and a 40-character sha are required.")
    if (r := limited("analyze", LIMITS["analyze_per_10min"], 600)):
        return r
    t0 = time.time()
    try:
        vr = load_github(owner, repo, sha)
    except RepoError as e:
        return error(e.code, e.message, e.status)
    meta = {"source": "github", "owner": owner, "repo": repo, "full_name": f"{owner}/{repo}",
            "ref": ref or None, "sha": sha, "html_url": f"https://github.com/{owner}/{repo}"}
    if not vr.texts:
        return error("empty_repo", "This repository has no readable source files.", 422)
    payload = _build_payload(meta, vr)
    payload["elapsed_ms"] = int((time.time() - t0) * 1000)
    body = _fit(payload)
    print(f"[analyze] {owner}/{repo}@{sha[:7]} files={len(vr.paths)} bytes={len(body)} ms={payload['elapsed_ms']}")
    resp = Response(body, mimetype="application/json")
    # One commit never changes, so let the CDN keep it (the URL carries the analyzer version).
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=31536000, stale-while-revalidate=86400"
    return resp


@app.post("/api/analyze/upload")
def analyze_upload():
    if not same_origin_ok():
        return error("forbidden_origin", "This API only accepts requests from its own site.", 403)
    if (r := limited("analyze", LIMITS["analyze_per_10min"], 600)):
        return r
    f = request.files.get("file")
    if not f or not f.filename:
        return error("bad_request", "Choose a .zip file to upload.")
    if not f.filename.lower().endswith(".zip"):
        return error("bad_zip", "Only .zip archives are supported.")
    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return too_large(None)
    name = re.sub(r"\.zip$", "", os.path.basename(f.filename), flags=re.I)[:100] or "project"
    t0 = time.time()
    try:
        vr = load_zip_bytes(data, name)
    except RepoError as e:
        return error(e.code, e.message, e.status)
    if not vr.texts:
        return error("empty_repo", "The ZIP has no readable source files.", 422)
    digest = hashlib.sha256(data).hexdigest()
    meta = {"source": "upload", "owner": None, "repo": vr.name, "full_name": vr.name,
            "ref": None, "sha": digest[:40], "html_url": None}
    payload = _build_payload(meta, vr)
    payload["elapsed_ms"] = int((time.time() - t0) * 1000)
    resp = Response(_fit(payload), mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── chat ──────────────────────────────────────────────────────────────────────
@app.post("/api/chat")
def chat():
    if not same_origin_ok():
        return error("forbidden_origin", "This API only accepts requests from its own site.", 403)
    if request.content_length and request.content_length > MAX_CHAT_BODY:
        return error("too_large", "This conversation is too large to send. Start a new chat.", 413)
    if not llm.is_configured():
        return error("not_configured", "The AI service isn't set up yet. The site owner needs to add OPENAI_API_KEY in Vercel and redeploy.", 503)
    for bucket, limit, window in (("chat_m", LIMITS["chat_per_minute"], 60), ("chat_d", LIMITS["chat_per_day"], 86400)):
        if (r := limited(bucket, limit, window)):
            return r
    if LIMITS["global_chat_per_day"]:
        ok, retry = ratelimit.hit("chat_global", "all", LIMITS["global_chat_per_day"], 86400)
        if not ok:
            return error("capacity", "The site has reached today's usage limit. Please come back tomorrow.", 429, retry_after=retry)

    try:
        req = llm.validate(request.get_json(silent=True))
    except llm.ChatError as e:
        return error(e.code, e.message, e.status)

    resp = Response(stream_with_context(llm.stream_chat(req)), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)
