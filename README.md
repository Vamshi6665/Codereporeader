# CodeRepoReader 🔍 — OpenAI SDK + Vercel

> AI-powered repository intelligence. Upload a project (ZIP) or paste a GitHub
> URL, then ask natural-language questions about its architecture, patterns,
> metrics, and code quality.

This version runs the chat through the **OpenAI Python SDK** and deploys as a
**Vercel serverless function**.

---

## What changed from the original

| Original (local Flask) | This version (Vercel) |
|---|---|
| Raw `requests` calls to GitHub Models REST API | **OpenAI SDK** (`from openai import OpenAI`), configurable `base_url` |
| Long-running local server (`python run.py`) | **Serverless function** under `api/`, routed by `vercel.json` |
| In-memory `analysis_cache` keyed by session | **Stateless** — analysis context is returned to the browser and sent back with each chat |
| `git clone` subprocess for GitHub URLs | **GitHub tarball API** download (no `git` binary, works on read-only FS) |
| Server-Sent-Events streaming | **Non-streaming** JSON response (reliable on Vercel) + client-side typing animation |

---

## Project layout

```
deepsynth-vercel/
├── api/
│   ├── index.py          # Flask app (the Vercel Python function) — all routes
│   ├── analyzer.py       # project structure + key-file collection
│   ├── code_analyzer.py  # deep static analysis (Python AST, JS/Java/C# regex)
│   └── github_fetch.py   # download + extract a repo tarball (replaces git clone)
├── templates/
│   └── index.html        # single-page UI
├── requirements.txt      # flask, requests, openai
├── vercel.json           # function config (60s max duration)
├── local_server.py       # run locally exactly as it runs on Vercel
├── cli.py                # optional CLI (also OpenAI-SDK based)
├── summarizer.py         # beginner-summary generator (OpenAI SDK)
└── .env.example          # documents the environment variables
```

---

## Deploy to Vercel

Vercel has native Flask support: it detects the `Flask` instance named `app` in
`api/index.py`, builds the whole app as a **single serverless function**, and
routes every request into it. No `builds`/`routes` config is needed — `vercel.json`
only sets the function's max duration.

### Option A — GitHub + Vercel dashboard (recommended)

1. Push this folder to a GitHub repository.
2. Go to [vercel.com/new](https://vercel.com/new) and import the repo.
3. Framework preset: **Other** (the included `vercel.json` handles everything).
4. Under **Settings → Environment Variables**, add:
   - `OPENAI_API_KEY` = your OpenAI key (so users don't have to paste one).
   - *(optional)* `OPENAI_MODEL` = e.g. `gpt-4o-mini`.
   - *(optional)* `OPENAI_BASE_URL` = an OpenAI-compatible endpoint (see below).
5. Click **Deploy**. Open the resulting URL.

### Option B — Vercel CLI

```bash
npm i -g vercel
cd deepsynth-vercel
vercel            # first deploy (preview)
vercel env add OPENAI_API_KEY      # paste your key
vercel --prod     # production deploy
```

---

## Run locally

```bash
cd deepsynth-vercel
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...                          # or paste it in the sidebar
python local_server.py
```

Open http://localhost:5000.

---

## Using a different provider

The OpenAI SDK's `base_url` is configurable, so any OpenAI-compatible endpoint
works. Set `OPENAI_BASE_URL` (env var) and use that provider's key:

- **OpenAI (default):** leave `OPENAI_BASE_URL` unset; key is `sk-...`.
- **GitHub Models (free tier):** `OPENAI_BASE_URL=https://models.github.ai/inference`,
  key = a GitHub Personal Access Token. Models are named like `openai/gpt-4o`.
- **Azure OpenAI / OpenRouter / local vLLM:** point `OPENAI_BASE_URL` at the
  endpoint and set the matching key.

---

## Notes & limits on Vercel

- **Upload size:** Vercel caps request bodies at ~4.5 MB, so ZIP uploads must be
  small. For larger projects, use the **GitHub URL** option — the server
  downloads the tarball itself and bypasses the request-body limit.
- **Function duration:** `vercel.json` sets `maxDuration: 60`. Large models on
  long prompts can be slow; `gpt-4o-mini` is a good default.
- **API key handling:** if `OPENAI_API_KEY` is set on the server, the sidebar key
  field is optional. A key pasted in the sidebar takes precedence for that request.
- **GitHub rate limits:** unauthenticated tarball downloads are limited per IP.
  Paste a GitHub token in the sidebar to raise the limit (or for private repos).

---

## Example questions

- "Explain the high-level architecture."
- "How many public classes are there?"
- "What design patterns are used?"
- "Any code quality issues or improvements?"
- "How do I run this project?"
