"""
CodeRepoReader — Vercel serverless web app
==========================================
Single Flask WSGI function deployed on Vercel's @vercel/python runtime.

Key differences from the original Flask app:
  * Chat runs through the **OpenAI SDK** (`from openai import OpenAI`) instead of
    raw GitHub Models REST calls. `base_url` is configurable, so the same code
    works against OpenAI, GitHub Models, Azure OpenAI, or any OpenAI-compatible
    endpoint.
  * **Stateless**: serverless invocations don't share memory, so there is no
    server-side session cache. `/api/analyze` returns the analysis context to the
    browser, which sends it back with each `/api/chat` request.
  * GitHub URLs are fetched via the GitHub tarball API (no `git` binary needed).
  * Chat is **non-streaming** (one JSON response) for reliability on Vercel.

Local dev:  python local_server.py     (see project root)
"""

import os
import sys
import json
import shutil
import zipfile
import tempfile

# Make sibling modules importable whether run by Vercel or locally.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, request, jsonify

from analyzer import collect_project_info
from code_analyzer import deep_analyze_code
from github_fetch import download_repo_tarball

# ── Config (all overridable via environment variables) ─────────────────────────
# Default provider is OpenAI. To use GitHub Models instead, set:
#   OPENAI_BASE_URL=https://models.github.ai/inference
# and use a GitHub PAT as the API key.
DEFAULT_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").strip() or None
SERVER_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip() or None
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()

# Vercel serverless request bodies are capped (~4.5 MB). Keep uploads under that;
# larger projects should use the GitHub URL option (fetched server-side).
MAX_UPLOAD_BYTES = 4 * 1024 * 1024

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(ROOT_DIR, "templates")
app = Flask(__name__, template_folder=TEMPLATES_DIR)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + (512 * 1024)


@app.route("/")
def index():
    # Read the page directly (no Jinja) so it renders reliably in the serverless
    # bundle regardless of template-folder resolution.
    html_path = os.path.join(TEMPLATES_DIR, "index.html")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze_project():
    """Extract a project (upload or GitHub URL), analyze it, and return the
    full context so the (stateless) client can carry it into chat."""
    work_dir = None
    source_type = request.form.get("source_type", "upload")

    try:
        if source_type == "github":
            url = request.form.get("github_url", "").strip()
            if not url:
                return jsonify({"error": "No GitHub URL provided"}), 400
            token = request.form.get("api_key", "").strip() or None
            try:
                project_path, work_dir = download_repo_tarball(url, token=token)
            except ValueError as ve:
                return jsonify({"error": str(ve)}), 400

        elif source_type == "upload":
            if "project_zip" not in request.files:
                return jsonify({"error": "No file uploaded"}), 400
            f = request.files["project_zip"]
            if not f.filename.endswith(".zip"):
                return jsonify({"error": "Please upload a .zip file"}), 400

            work_dir = tempfile.mkdtemp(prefix="codereporeader-")
            zip_path = os.path.join(work_dir, "upload.zip")
            f.save(zip_path)
            with zipfile.ZipFile(zip_path, "r") as z:
                _safe_unzip(z, work_dir)
            os.remove(zip_path)

            entries = [e for e in os.listdir(work_dir) if not e.startswith(".")]
            if len(entries) == 1 and os.path.isdir(os.path.join(work_dir, entries[0])):
                project_path = os.path.join(work_dir, entries[0])
            else:
                project_path = work_dir
        else:
            return jsonify({"error": "Invalid source type"}), 400

        project_info = collect_project_info(project_path)
        code_stats = deep_analyze_code(project_path)

        # Trim file contents so the context carried by the browser stays small.
        trimmed_files = {}
        for name, content in list(project_info.get("files", {}).items())[:6]:
            trimmed_files[name] = content[:1500]

        # This whole object is stored client-side and echoed back on each chat.
        context = {
            "name": project_info["name"],
            "structure": project_info["structure"][:6000],
            "files": trimmed_files,
            "code_stats": code_stats,
        }

        return jsonify(
            {
                "context": context,
                "project_name": project_info["name"],
                "structure": project_info["structure"],
                "code_stats": code_stats,
                "file_count": len(project_info.get("files", {})),
            }
        )

    except Exception as e:  # noqa: BLE001 — surface any failure to the client
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


@app.route("/api/chat", methods=["POST"])
def chat():
    """Answer a question about the analyzed codebase using the OpenAI SDK."""
    try:
        from openai import OpenAI
        from openai import (
            APIStatusError,
            AuthenticationError,
            RateLimitError,
            APIConnectionError,
        )
    except ImportError:
        return (
            jsonify(
                {
                    "error": "The 'openai' package is not installed. "
                    "Add `openai` to requirements.txt and redeploy."
                }
            ),
            500,
        )

    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    context = data.get("context") or {}
    history = data.get("history") or []
    model = (data.get("model") or DEFAULT_MODEL).strip()
    is_first = bool(data.get("is_first_message", False))

    # API key: request value wins, otherwise fall back to a server-side env var.
    api_key = (data.get("api_key") or "").strip() or SERVER_API_KEY
    base_url = (data.get("base_url") or "").strip() or DEFAULT_BASE_URL

    if not user_message:
        return jsonify({"error": "Empty message"}), 400
    if not context:
        return jsonify({"error": "No project context. Please analyze a project first."}), 400
    if not api_key:
        return (
            jsonify(
                {
                    "error": "No API key provided. Enter your OpenAI API key in the "
                    "sidebar, or set OPENAI_API_KEY on the server."
                }
            ),
            400,
        )

    # Build the prompt. Full context on the first turn, compact afterward.
    code_stats = context.get("code_stats", {})
    if is_first or len(history) == 0:
        system_prompt = _system_prompt_full(context, code_stats)
    else:
        system_prompt = _system_prompt_compact(context, code_stats)

    messages = [{"role": "system", "content": system_prompt}]
    for msg in history[-6:]:
        role = msg.get("role")
        content = msg.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=1500,
            temperature=0.3,
        )
        reply = (completion.choices[0].message.content or "").strip()
        if not reply:
            return jsonify({"error": "The model returned an empty response. Try another model."}), 502
        return jsonify({"reply": reply, "model": model})

    except AuthenticationError:
        return jsonify({"error": "Invalid API key. Check the key in the sidebar."}), 401
    except RateLimitError:
        return jsonify({"error": "Rate limit reached. Wait a moment and try again, or switch models."}), 429
    except APIConnectionError as e:
        return jsonify({"error": f"Could not reach the API endpoint: {e}"}), 502
    except APIStatusError as e:
        detail = getattr(e, "message", str(e))
        return jsonify({"error": f"API error {e.status_code}: {detail}"}), 502
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


# ── Prompt builders ────────────────────────────────────────────────────────────
def _system_prompt_full(context, code_stats):
    files_section = ""
    for filename, content in list(context.get("files", {}).items())[:6]:
        files_section += f"\n### {filename}\n```\n{content[:1500]}\n```\n"
    stats_section = json.dumps(code_stats, indent=2)[:6000]
    return f"""You are an expert code architect analyzing the project "{context.get('name')}".

## PROJECT STRUCTURE
```
{context.get('structure', '')}
```

## CODE STATISTICS
```json
{stats_section}
```

## KEY FILES
{files_section}

Answer questions about this codebase precisely. Cite actual file names and class names. Use markdown formatting. Be concise."""


def _system_prompt_compact(context, code_stats):
    langs = ", ".join((code_stats.get("languages") or {}).keys()) or "unknown"
    return f"""You are an expert code architect analyzing "{context.get('name')}".

Project: {code_stats.get('total_files', '?')} files, {code_stats.get('total_lines', '?')} lines.
Languages: {langs}.
Structure summary:
{context.get('structure', '')[:800]}

Answer concisely using markdown. Cite file/class names when relevant."""


# ── Safety helper for zip extraction ───────────────────────────────────────────
def _safe_unzip(zf, dest):
    dest_abs = os.path.abspath(dest)
    for member in zf.namelist():
        target = os.path.abspath(os.path.join(dest, member))
        if not target.startswith(dest_abs + os.sep) and target != dest_abs:
            raise ValueError(f"Unsafe path in zip: {member}")
    zf.extractall(dest)


# Local execution (Vercel imports `app` directly and ignores this block).
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
