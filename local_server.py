"""
Run CodeRepoReader locally the way Vercel serves it: the built frontend from dist/
plus the Flask API under /api.

    npm install && npm run build
    pip install -r requirements.txt
    export OPENAI_API_KEY=sk-...          # or use the free mock below
    python local_server.py                # http://localhost:5000

Frontend development with hot reload (two terminals):

    python local_server.py --api-only     # API on :5000
    npm run dev                           # UI on :5173, proxies /api to :5000

No API key? Use the mock model:

    python dev/mock_openai.py
    OPENAI_API_KEY=mock OPENAI_BASE_URL=http://localhost:8787/v1 python local_server.py
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "api"))

from flask import abort, request, send_from_directory  # noqa: E402

from index import app  # noqa: E402

DIST = os.path.join(ROOT, "dist")
API_ONLY = "--api-only" in sys.argv

# Apply the same response headers vercel.json sets in production (CSP, caching…).
with open(os.path.join(ROOT, "vercel.json"), encoding="utf-8") as fh:
    _HEADER_RULES = [(re.compile("^" + r["source"] + "$"), r["headers"]) for r in json.load(fh).get("headers", [])]


@app.after_request
def vercel_headers(resp):
    for rx, headers in _HEADER_RULES:
        if rx.match(request.path):
            for h in headers:
                if h["key"] != "Strict-Transport-Security":
                    resp.headers[h["key"]] = h["value"]
    return resp


if not API_ONLY:
    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def frontend(path):
        if path.startswith("api/"):
            abort(404)
        if not os.path.isdir(DIST):
            return ("dist/ not found. Run `npm install && npm run build` first, "
                    "or start with --api-only and use `npm run dev`."), 500
        full = os.path.join(DIST, path)
        if path and os.path.isfile(full):
            resp = send_from_directory(DIST, path)
            if path.startswith("assets/"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp
        return send_from_directory(DIST, "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"CodeRepoReader running at http://localhost:{port}" + (" (API only)" if API_ONLY else ""))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
