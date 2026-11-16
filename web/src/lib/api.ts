import type { Mode, RepoData, RepoMetaRaw, ServerConfig } from "./types";

export class ApiError extends Error {
  code: string;
  status: number;
  retryAfter?: number;
  constructor(code: string, message: string, status = 0, retryAfter?: number) {
    super(message);
    this.code = code;
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

async function readError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    const e = body?.error ?? {};
    return new ApiError(e.code ?? "http_" + res.status, e.message ?? `Request failed (${res.status}).`, res.status, e.retry_after);
  } catch {
    if (res.status === 504) return new ApiError("timeout", "The server took too long to respond. Try again, or try a smaller repository.", 504);
    if (res.status === 413) return new ApiError("too_large", "That request was too large for the server.", 413);
    return new ApiError("http_" + res.status, `The server returned an error (${res.status}).`, res.status);
  }
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("network", "Couldn't reach the server. Check your connection and try again.");
  }
  if (!res.ok) throw await readError(res);
  return res.json() as Promise<T>;
}

export const ANALYZER_VERSION = "2";

export function fetchConfig() {
  return getJson<ServerConfig>("/api/config");
}

export function resolveRepo(url: string, signal?: AbortSignal) {
  return getJson<RepoMetaRaw & { description: string; stars: number }>(`/api/resolve?url=${encodeURIComponent(url)}`, signal);
}

export function analyzeGithub(meta: RepoMetaRaw, signal?: AbortSignal) {
  const q = new URLSearchParams({
    owner: meta.owner ?? "",
    repo: meta.repo,
    sha: meta.sha,
    ref: meta.ref ?? "",
    v: ANALYZER_VERSION,
  });
  return getJson<RepoData>(`/api/analyze?${q}`, signal);
}

export async function analyzeZip(file: File, signal?: AbortSignal): Promise<RepoData> {
  const form = new FormData();
  form.append("file", file);
  let res: Response;
  try {
    res = await fetch("/api/analyze/upload", { method: "POST", body: form, signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("network", "Couldn't reach the server. Check your connection and try again.");
  }
  if (!res.ok) throw await readError(res);
  return res.json();
}

export interface ChatRequest {
  mode: Mode;
  repo: { key: string; map: string };
  messages: { role: "user" | "assistant"; content: string }[];
  context: { path: string; start: number; text: string; pinned?: boolean }[];
}

export interface StreamHandlers {
  onMeta?: (m: { label: string; model: string; mode: Mode }) => void;
  onDelta: (text: string) => void;
  onStatus?: (s: { state: string }) => void;
  onDone: (d: { finish_reason: string; elapsed_ms: number }) => void;
  onError: (e: ApiError) => void;
}

/** POST /api/chat and parse the Server-Sent Events stream. */
export async function streamChat(body: ChatRequest, h: StreamHandlers, signal: AbortSignal) {
  let res: Response;
  try {
    res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    h.onError(new ApiError("network", "Couldn't reach the server. Check your connection and try again."));
    return;
  }
  if (!res.ok || !res.body) {
    h.onError(await readError(res));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finished = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let event = "message";
        const data: string[] = [];
        for (const line of raw.split("\n")) {
          if (line.startsWith(":")) continue;
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        }
        if (!data.length) continue;
        let payload: any;
        try {
          payload = JSON.parse(data.join("\n"));
        } catch {
          continue;
        }
        if (event === "delta") h.onDelta(payload.t ?? "");
        else if (event === "meta") h.onMeta?.(payload);
        else if (event === "status") h.onStatus?.(payload);
        else if (event === "done") {
          finished = true;
          h.onDone(payload);
        } else if (event === "error") {
          finished = true;
          h.onError(new ApiError(payload.code ?? "error", payload.message ?? "Something went wrong."));
        }
      }
    }
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    finished = true;
    h.onError(new ApiError("connection_lost", "The connection dropped before the answer finished. Retry to continue."));
  }
  if (!finished && !signal.aborted) {
    h.onError(new ApiError("connection_lost", "The answer was cut off by the connection. Retry to get the full answer."));
  }
}
