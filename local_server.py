"""
local_server.py — run CodeRepoReader locally exactly as it runs on Vercel.

    python local_server.py

Then open http://localhost:5000

Set your key first (or paste it in the sidebar):
    export OPENAI_API_KEY=sk-...        # macOS/Linux
    setx OPENAI_API_KEY "sk-..."        # Windows (new shell)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
from index import app  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"\n  CodeRepoReader running at http://localhost:{port}\n")
    app.run(debug=True, host="0.0.0.0", port=port)
