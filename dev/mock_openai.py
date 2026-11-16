"""
A tiny OpenAI-compatible server for local testing — no API key, no cost.

    python dev/mock_openai.py                       # listens on :8787
    OPENAI_API_KEY=mock OPENAI_BASE_URL=http://localhost:8787/v1 python local_server.py

It streams a realistic Markdown answer built from the code excerpts it receives,
with citations, a code block, a Mermaid diagram and follow-ups. Put these words in a
question to exercise edge cases:

    LONGANSWER   first response stops with finish_reason="length" (tests auto-continue)
    BUSY429      returns HTTP 429 (tests error handling)
    SLOWSTART    waits 20 s before the first token (tests heartbeats / "Thinking")
"""

import json
import os
import re
import time

from flask import Flask, Response, jsonify, request

app = Flask(__name__)
REJECT_EFFORT = os.environ.get("MOCK_REJECT_EFFORT") == "1"


def chunk(content=None, finish=None, usage=None):
    obj = {"id": "mock", "object": "chat.completion.chunk", "created": int(time.time()), "model": "mock",
           "choices": [] if usage else [{"index": 0, "delta": ({"content": content} if content else {}), "finish_reason": finish}]}
    if usage:
        obj["usage"] = usage
    return f"data: {json.dumps(obj)}\n\n"


def compose(messages):
    last = messages[-1]["content"]
    continuing = "was cut off by the output limit" in last
    question = last.split("Question:", 1)[-1].strip() if "Question:" in last else last
    blocks = re.findall(r"=== (.+?) \(lines (\d+)-(\d+)\)", last)
    path, a, b = (blocks[0] if blocks else ("README.md", "1", "10"))
    a, b = int(a), int(b)
    mid = min(b, a + 12)
    code = ""
    m = re.search(r"=== " + re.escape(path) + r" \(lines \d+-\d+\).*?\n```(\w*)\n(.*?)\n```", last, re.S)
    if m:
        lines = [re.sub(r"^\d+\| ", "", ln) for ln in m.group(2).split("\n")[:8]]
        code = f"```{m.group(1)}\n" + "\n".join(lines) + "\n```"
    others = ", ".join(f"[{p}:L{x}-L{y}]" for p, x, y in blocks[1:4])
    if continuing:
        return ("the rest of the explanation picks up exactly where it stopped, so nothing is lost.\n\n"
                "### Summary\n\nEverything above came from the retrieved excerpts.\n\n"
                "%%FOLLOWUPS%% Where are errors handled? | How is this tested? | What would you refactor first?")
    body = f"""Here's the short answer to **{question[:80]}**: the core logic lives in `{path}` [{path}:L{a}-L{mid}].

## How it fits together

1. The entry point loads configuration and wires the pieces together [{path}:L{a}].
2. Requests flow through the handlers shown below, which call into the helpers{(' (' + others + ')') if others else ''}.
3. Results are returned to the caller as JSON.

{code}

| Piece | Where | Role |
|---|---|---|
| Entry | `{path}` | Starts the app |
| Helpers | {len(blocks)} excerpts | Supporting logic |

```mermaid
flowchart LR
  A["Browser"] --> B["API route"]
  B --> C["Analyzer"]
  C --> D["Model"]
```
"""
    if "LONGANSWER" in question:
        return body + "\n\nThe answer keeps going with more detail, and "
    return body + "\n%%FOLLOWUPS%% How is configuration loaded? | Which functions are the riskiest? | Draw the request flow"


@app.post("/v1/chat/completions")
def completions():
    data = request.get_json()
    if REJECT_EFFORT and "reasoning_effort" in data:
        return jsonify({"error": {"message": "Unrecognized request argument supplied: reasoning_effort", "type": "invalid_request_error"}}), 400
    msgs = data["messages"]
    last = msgs[-1]["content"]
    question = last.rsplit("Question:", 1)[-1] if "Question:" in last else last
    if "BUSY429" in question:
        return jsonify({"error": {"message": "Rate limit reached", "type": "rate_limit_exceeded"}}), 429
    text = compose(msgs)
    cut = "LONGANSWER" in question and "was cut off" not in question

    def gen():
        if "SLOWSTART" in question:
            time.sleep(20)
        tokens = re.findall(r"\S+\s*|\s+", text)
        for i in range(0, len(tokens), 3):
            yield chunk("".join(tokens[i:i + 3]))
            time.sleep(0.03)
        yield chunk(finish="length" if cut else "stop")
        if data.get("stream_options", {}).get("include_usage"):
            yield chunk(usage={"prompt_tokens": sum(len(m["content"]) for m in msgs) // 4,
                               "completion_tokens": len(text) // 4,
                               "prompt_tokens_details": {"cached_tokens": 1024}})
        yield "data: [DONE]\n\n"

    return Response(gen(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 8787)), threaded=True)
