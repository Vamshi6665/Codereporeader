"""
llm.py
======
Prompt assembly and streaming for /api/chat.

Design goals
------------
* **Key stays on the server.** The OpenAI client is built only from environment
  variables; nothing in a request can change the key or the endpoint.
* **No visible token limits.** The browser retrieves the most relevant code for each
  question, the server enforces a per-mode budget, older turns are condensed instead
  of silently dropped, and when the model stops because it hit its output cap the
  server asks it to continue — inside the same stream, invisibly to the user.
* **Cache-friendly prompts.** The instructions and the repository map are the same
  for every turn in a repo, so they come first; OpenAI bills cached input at a
  fraction of the normal price.
* **Robust streaming.** The model call runs in a worker thread; the response
  generator sends heartbeats while the model is thinking, coalesces small deltas,
  and stops the upstream call if the user disconnects or presses Stop.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import time

# ── Configuration ─────────────────────────────────────────────────────────────
def _env(name, default):
    v = os.environ.get(name, "").strip()
    return v or default


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


API_KEY = _env("OPENAI_API_KEY", "")
BASE_URL = _env("OPENAI_BASE_URL", "") or None

def _label(model, explicit_env):
    """Human label for a model id, so the UI always names the model actually in use."""
    explicit = _env(explicit_env, "")
    if explicit:
        return explicit
    known = {"gpt-5.6-luna": "GPT-5.6 Luna", "gpt-5.6-terra": "GPT-5.6 Terra", "gpt-5.6-sol": "GPT-5.6 Sol"}
    return known.get(model, model)


_FAST_MODEL = _env("OPENAI_MODEL_FAST", _env("OPENAI_MODEL", "gpt-5.6-luna"))
_DEEP_MODEL = _env("OPENAI_MODEL_DEEP", "gpt-5.6-terra")

MODES = {
    "fast": {
        "model": _FAST_MODEL,
        "label": _label(_FAST_MODEL, "OPENAI_MODEL_FAST_LABEL"),
        "context_chars": _env_int("CONTEXT_CHARS_FAST", 110_000),
        "history_chars": _env_int("HISTORY_CHARS_FAST", 48_000),
        "max_output_tokens": _env_int("MAX_OUTPUT_TOKENS_FAST", 8_000),
        "reasoning_effort": _env("REASONING_EFFORT_FAST", "low"),
    },
    "deep": {
        "model": _DEEP_MODEL,
        "label": _label(_DEEP_MODEL, "OPENAI_MODEL_DEEP_LABEL"),
        "context_chars": _env_int("CONTEXT_CHARS_DEEP", 360_000),
        "history_chars": _env_int("HISTORY_CHARS_DEEP", 140_000),
        "max_output_tokens": _env_int("MAX_OUTPUT_TOKENS_DEEP", 16_000),
        "reasoning_effort": _env("REASONING_EFFORT_DEEP", "medium"),
    },
}
MAX_CONTINUATIONS = _env_int("MAX_CONTINUATIONS", 3)
STREAM_DEADLINE_S = _env_int("STREAM_DEADLINE_S", 280)      # Vercel Hobby caps a request at 300 s
CONTINUE_CUTOFF_S = _env_int("CONTINUE_CUTOFF_S", 200)      # don't start a continuation after this
HEARTBEAT_S = 8
FOLLOWUP_MARKER = "%%FOLLOWUPS%%"

SYSTEM_PROMPT = f"""You are CodeRepoReader, a senior software engineer and architect who helps people understand a codebase quickly and accurately.

You are given (1) a repository map with stats, the directory tree, routes and an outline of symbols, and (2) code excerpts retrieved for the current question. Every excerpt line starts with its real line number followed by "|".

How to answer
- Lead with the direct answer, then the supporting detail. Match the depth to the question: a quick question gets a short answer; "explain", "walk me through" or "review" deserves structure.
- Ground every claim in the provided context. When something isn't in the context, say so plainly and name the files that would most likely answer it (the user can attach them with @). Never invent files, functions, endpoints, numbers or behavior.
- Cite code inline as [path:Lstart-Lend] (or [path:Lnum] for one line) using the exact paths and the line numbers shown in the excerpts. Cite the specific lines that support the claim, not whole files. Don't cite anything you weren't shown.
- For counts and metrics (number of classes, files, lines, languages, routes…), use the repository map's numbers and say they come from static analysis.
- Format with Markdown: short paragraphs, `##` headings only for long answers, numbered lists for sequences, bullets for sets, tables for comparisons. Put code in fenced blocks with a language tag and quote only the lines that matter.
- When a diagram helps (architecture, request flow, class relationships, data pipeline), include one ```mermaid block (flowchart, sequenceDiagram or classDiagram). Keep labels short and wrap any label containing spaces or symbols in double quotes.
- For reviews and improvements, be specific: point at the code, explain the risk or cost, and show the fix.
- Write for a smart engineer who is new to this codebase. Define project-specific terms once. No filler, no apologies.

Finish every answer with one final line in exactly this form (it is hidden from the user and turned into buttons):
{FOLLOWUP_MARKER} first follow-up question | second follow-up question | third follow-up question
The follow-ups must be short (under 60 characters), specific to this repository, and things you could answer from this codebase."""

CONTINUE_PROMPT = ("Your previous message was cut off by the output limit. Continue exactly where it stopped — "
                   "same formatting, mid-sentence or mid-code-block if needed. Do not repeat anything, "
                   "do not add a preamble, and end with the follow-ups line as instructed.")

_END = object()                    # end-of-stream sentinel
_unsupported: set = set()          # params this provider rejected; skipped on later calls
_unsupported_lock = threading.Lock()


class ChatError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def is_configured() -> bool:
    return bool(API_KEY)


def public_config() -> dict:
    return {
        "modes": {k: {"label": v["label"], "model": v["model"]} for k, v in MODES.items()},
        "configured": is_configured(),
    }


# ── Request validation & prompt building ──────────────────────────────────────
def _clean_text(s, limit):
    s = s if isinstance(s, str) else ""
    return s[:limit]


def strip_followups(text: str) -> str:
    i = text.find(FOLLOWUP_MARKER)
    return text[:i].rstrip() if i >= 0 else text


def validate(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ChatError("bad_request", "Request body must be JSON.")
    mode = payload.get("mode") if payload.get("mode") in MODES else "fast"
    cfg = MODES[mode]

    raw_msgs = payload.get("messages")
    if not isinstance(raw_msgs, list) or not raw_msgs:
        raise ChatError("bad_request", "No messages were sent.")
    msgs = []
    for m in raw_msgs[-80:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        content = _clean_text(m.get("content"), 60_000)
        if m["role"] == "assistant":
            content = strip_followups(content)
        if content.strip():
            msgs.append({"role": m["role"], "content": content})
    if not msgs or msgs[-1]["role"] != "user":
        raise ChatError("bad_request", "The last message must be a question from the user.")
    if len(msgs[-1]["content"]) > 20_000:
        raise ChatError("too_long", "That message is too long. Keep questions under 20,000 characters.")

    repo = payload.get("repo") if isinstance(payload.get("repo"), dict) else {}
    repo_map = _clean_text(repo.get("map"), 60_000)
    if not repo_map.strip():
        raise ChatError("no_repo", "No repository is loaded. Analyze a repository first.")

    chunks = []
    for c in (payload.get("context") or [])[:400]:
        if not isinstance(c, dict):
            continue
        path = _clean_text(c.get("path"), 400)
        text = _clean_text(c.get("text"), 200_000)
        try:
            start = max(1, int(c.get("start") or 1))
        except (TypeError, ValueError):
            start = 1
        if path and text:
            chunks.append({"path": path, "start": start, "text": text, "pinned": bool(c.get("pinned"))})

    return {
        "mode": mode,
        "cfg": cfg,
        "messages": msgs,
        "repo_map": repo_map,
        "repo_key": _clean_text(repo.get("key") or repo.get("full_name") or "", 200),
        "chunks": chunks,
    }


def _fence_lang(path):
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {"py": "python", "js": "javascript", "jsx": "jsx", "ts": "typescript", "tsx": "tsx", "md": "markdown",
            "yml": "yaml", "rb": "ruby", "rs": "rust", "kt": "kotlin", "cs": "csharp", "sh": "bash",
            "ipynb": "python"}.get(ext, ext)


def _format_context(chunks, budget):
    pinned = [c for c in chunks if c["pinned"]]
    rest = [c for c in chunks if not c["pinned"]]
    blocks, used, dropped = [], 0, 0
    for c in pinned + rest:
        lines = c["text"].split("\n")
        numbered = "\n".join(f"{c['start'] + i}| {ln}" for i, ln in enumerate(lines))
        end = c["start"] + len(lines) - 1
        tag = " — attached by the user" if c["pinned"] else ""
        block = f"=== {c['path']} (lines {c['start']}-{end}){tag} ===\n```{_fence_lang(c['path'])}\n{numbered}\n```"
        if used + len(block) > budget:
            if c["pinned"] and used < budget * 0.9:
                keep = max(0, budget - used - 200)
                block = block[:keep] + "\n… (truncated to fit)\n```"
            else:
                dropped += 1
                continue
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks), dropped


def _condense_history(history, budget):
    """Keep the most recent turns verbatim; condense older ones into a short recap."""
    kept, used = [], 0
    for m in reversed(history):
        size = len(m["content"])
        if used + size > budget:
            break
        kept.append(m)
        used += size
    kept.reverse()
    older = history[: len(history) - len(kept)]
    recap = None
    if older:
        lines = []
        for m in older:
            text = re.sub(r"```.*?```", "[code]", m["content"], flags=re.S)
            text = re.sub(r"\s+", " ", text).strip()
            who = "User asked" if m["role"] == "user" else "You answered"
            lines.append(f"- {who}: {text[:280]}{'…' if len(text) > 280 else ''}")
        recap = "Earlier in this conversation (condensed):\n" + "\n".join(lines[-40:])
    # The kept history must start with a user turn for some providers.
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return kept, recap


def build_messages(req) -> list:
    cfg = req["cfg"]
    msgs = req["messages"]
    question = msgs[-1]["content"]
    history, recap = _condense_history(msgs[:-1], cfg["history_chars"])

    context, dropped = _format_context(req["chunks"], cfg["context_chars"])
    note = ""
    if dropped:
        note = f"\n(Note: {dropped} lower-ranked excerpts were left out to fit the budget.)"
    if context:
        user_turn = (f"Code excerpts retrieved for this question:\n\n{context}{note}\n\n"
                     f"---\nQuestion: {question}")
    else:
        user_turn = f"(No code excerpts matched this question; rely on the repository map.)\n\nQuestion: {question}"

    out = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": "Repository map:\n\n" + req["repo_map"]},
    ]
    if recap:
        out.append({"role": "system", "content": recap})
    out.extend(history)
    out.append({"role": "user", "content": user_turn})
    return out


# ── OpenAI call with graceful parameter fallback ──────────────────────────────
def _client():
    from openai import OpenAI
    kwargs = {"api_key": API_KEY, "max_retries": 2, "timeout": 120.0}
    if BASE_URL:
        kwargs["base_url"] = BASE_URL
    return OpenAI(**kwargs)


_PARAM_HINTS = {
    "reasoning_effort": ("reasoning_effort", "reasoning effort"),
    "max_completion_tokens": ("max_completion_tokens",),
    "prompt_cache_key": ("prompt_cache_key",),
    "stream_options": ("stream_options", "include_usage"),
}


def _create_stream(client, cfg, messages, cache_key):
    params = {"model": cfg["model"], "messages": messages, "stream": True}
    if "stream_options" not in _unsupported:
        params["stream_options"] = {"include_usage": True}
    if "max_completion_tokens" not in _unsupported:
        params["max_completion_tokens"] = cfg["max_output_tokens"]
    else:
        params["max_tokens"] = cfg["max_output_tokens"]
    if cfg.get("reasoning_effort") and cfg["reasoning_effort"] != "none" and "reasoning_effort" not in _unsupported:
        params["reasoning_effort"] = cfg["reasoning_effort"]
    if cache_key and BASE_URL is None and "prompt_cache_key" not in _unsupported:
        params["prompt_cache_key"] = cache_key

    from openai import BadRequestError
    for _ in range(5):
        try:
            return client.chat.completions.create(**params)
        except BadRequestError as e:
            msg = str(getattr(e, "message", "") or e).lower()
            removed = False
            for key, hints in _PARAM_HINTS.items():
                if (key in params) and any(h in msg for h in hints):
                    with _unsupported_lock:
                        _unsupported.add(key)
                    params.pop(key, None)
                    if key == "max_completion_tokens":
                        params["max_tokens"] = cfg["max_output_tokens"]
                    removed = True
            if not removed:
                raise
    raise ChatError("upstream_error", "The AI service rejected the request parameters.", 502)


def _map_error(e, model):
    from openai import (APIConnectionError, APITimeoutError, AuthenticationError, BadRequestError,
                        NotFoundError, PermissionDeniedError, RateLimitError, APIStatusError)
    if isinstance(e, ChatError):
        return {"code": e.code, "message": e.message}
    if isinstance(e, (AuthenticationError, PermissionDeniedError)):
        return {"code": "server_key", "message": "The AI service rejected this site's API key. The site owner needs to check OPENAI_API_KEY in Vercel."}
    if isinstance(e, RateLimitError):
        if "insufficient_quota" in str(e).lower() or "quota" in str(e).lower():
            return {"code": "quota", "message": "This site has used up its AI quota for now. Please try again later."}
        return {"code": "rate_limited", "message": "The AI service is busy right now. Wait a few seconds and try again."}
    if isinstance(e, NotFoundError):
        return {"code": "model_not_found", "message": f"The model “{model}” isn't available for this site's API key."}
    if isinstance(e, BadRequestError):
        msg = str(e).lower()
        if "context_length" in msg or "maximum context" in msg or "too many tokens" in msg:
            return {"code": "context_too_long", "message": "This question pulled in more code than the model accepts. Try a narrower question or Fast mode."}
        return {"code": "bad_request", "message": "The AI service couldn't process this request."}
    if isinstance(e, (APIConnectionError, APITimeoutError)):
        return {"code": "upstream_unreachable", "message": "Couldn't reach the AI service. Check your connection and try again."}
    if isinstance(e, APIStatusError):
        return {"code": "upstream_error", "message": f"The AI service returned an error ({e.status_code}). Try again."}
    return {"code": "internal", "message": "Something went wrong while generating the answer. Try again."}


def _sse(event, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_chat(req):
    """Generator of Server-Sent-Event strings for one chat turn."""
    cfg = req["cfg"]
    messages = build_messages(req)
    cache_key = "crr-" + hashlib.sha256((req["repo_key"] or req["repo_map"][:2000]).encode()).hexdigest()[:24]
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    t0 = time.time()

    def worker():
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
        produced_all = ""
        finish = None
        continuations = 0
        shrink_retry_done = False
        msgs = messages
        try:
            client = _client()
            while True:
                try:
                    stream = _create_stream(client, cfg, msgs, cache_key)
                except Exception as e:  # noqa: BLE001
                    m = _map_error(e, cfg["model"])
                    if m["code"] == "context_too_long" and not shrink_retry_done:
                        shrink_retry_done = True
                        req2 = dict(req, chunks=req["chunks"][: max(1, len(req["chunks"]) // 3)])
                        msgs = build_messages(req2) if not produced_all else msgs
                        continue
                    raise
                produced = ""
                try:
                    for chunk in stream:
                        if stop.is_set():
                            break
                        if getattr(chunk, "usage", None):
                            u = chunk.usage
                            usage_total["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
                            usage_total["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
                            details = getattr(u, "prompt_tokens_details", None)
                            usage_total["cached_tokens"] += (getattr(details, "cached_tokens", 0) or 0) if details else 0
                        if not chunk.choices:
                            continue
                        choice = chunk.choices[0]
                        delta = getattr(choice.delta, "content", None) if choice.delta else None
                        if delta:
                            produced += delta
                            q.put(("delta", delta))
                        if choice.finish_reason:
                            finish = choice.finish_reason
                        if time.time() - t0 > STREAM_DEADLINE_S:
                            finish = "time"
                            break
                finally:
                    try:
                        stream.close()
                    except Exception:  # noqa: BLE001
                        pass
                produced_all += produced
                if stop.is_set():
                    return
                if (finish == "length" and continuations < MAX_CONTINUATIONS
                        and time.time() - t0 < CONTINUE_CUTOFF_S and produced):
                    continuations += 1
                    q.put(("status", {"state": "continuing", "n": continuations}))
                    msgs = messages + [{"role": "assistant", "content": produced_all},
                                       {"role": "user", "content": CONTINUE_PROMPT}]
                    finish = None
                    continue
                break
            if not produced_all.strip():
                q.put(("error", {"code": "empty", "message": "The model returned an empty answer. Try again or switch modes."}))
                return
            q.put(("done", {"finish_reason": finish or "stop", "continuations": continuations,
                            "usage": usage_total, "model": cfg["model"],
                            "elapsed_ms": int((time.time() - t0) * 1000)}))
        except Exception as e:  # noqa: BLE001
            q.put(("error", _map_error(e, cfg["model"])))
        finally:
            q.put(_END)

    threading.Thread(target=worker, daemon=True).start()

    try:
        yield _sse("meta", {"mode": req["mode"], "model": cfg["model"], "label": cfg["label"]})
        pending = None
        while True:
            if pending is not None:
                item, pending = pending, None
            else:
                try:
                    item = q.get(timeout=HEARTBEAT_S)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
            if item is _END:
                break
            kind, data = item
            if kind == "delta":
                # Coalesce whatever else is already waiting into one event.
                parts = [data]
                while True:
                    try:
                        nxt = q.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is not _END and nxt[0] == "delta":
                        parts.append(nxt[1])
                    else:
                        pending = nxt
                        break
                yield _sse("delta", {"t": "".join(parts)})
                continue
            yield _sse(kind, data)
    finally:
        stop.set()
