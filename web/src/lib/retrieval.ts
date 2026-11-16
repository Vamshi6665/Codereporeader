/**
 * Client-side retrieval.
 *
 * The whole repository (up to a few MB of source) lives in the browser. For every
 * question we rank code chunks with BM25 plus a few code-aware boosts and send only
 * the best ones to the server. This keeps each request small and cheap, scales with
 * the number of users for free, and lets answers draw on any file in the repo.
 */
import type { CodeSymbol, RepoData, RepoFile } from "./types";

export interface Chunk {
  path: string;
  start: number; // 1-based, inclusive
  end: number;
  tf: Map<string, number>;
  len: number;
  pathTokens: Set<string>;
  symbols: string[];
}

export interface RepoIndex {
  chunks: Chunk[];
  df: Map<string, number>;
  avgLen: number;
  files: Map<string, RepoFile>;
  lines: Map<string, string[]>;
  readme: string | null;
  important: string[]; // manifests + entry points
}

const STOP = new Set(
  ("the a an and or of to in on for with is are was be it this that these those how what where why when which who " +
    "does do did can could should would will i me my we our you your there here from by as at into about explain show " +
    "tell give list find use used using get set make code file files project repo repository function functions class")
    .split(" "),
);

export function tokenize(s: string): string[] {
  const out: string[] = [];
  const spaced = s.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2");
  for (const t of spaced.toLowerCase().split(/[^a-z0-9]+/)) {
    if (t.length > 1 && t.length < 40 && !STOP.has(t)) out.push(t);
  }
  return out;
}

const MIN_CHUNK = 30;
const MAX_CHUNK = 110;

function chunkBoundaries(nLines: number, symbolLines: number[]): [number, number][] {
  if (nLines <= MAX_CHUNK + 20) return [[1, nLines]];
  const cuts = new Set(symbolLines.filter((l) => l > 1 && l <= nLines));
  const out: [number, number][] = [];
  let start = 1;
  for (let line = 2; line <= nLines; line++) {
    const size = line - start;
    if ((cuts.has(line) && size >= MIN_CHUNK) || size >= MAX_CHUNK) {
      out.push([start, line - 1]);
      start = size >= MAX_CHUNK && !cuts.has(line) ? Math.max(line - 8, start + 1) : line; // small overlap on hard cuts
    }
  }
  if (start <= nLines) out.push([start, nLines]);
  return out;
}

export function buildIndex(data: RepoData): RepoIndex {
  const symbolsByFile = new Map<string, CodeSymbol[]>();
  for (const s of data.analysis.symbols) {
    const arr = symbolsByFile.get(s.file) ?? [];
    arr.push(s);
    symbolsByFile.set(s.file, arr);
  }
  const chunks: Chunk[] = [];
  const df = new Map<string, number>();
  const files = new Map<string, RepoFile>();
  const lines = new Map<string, string[]>();
  let total = 0;

  for (const f of data.files) {
    files.set(f.path, f);
    const ls = f.content.split("\n");
    lines.set(f.path, ls);
    const syms = symbolsByFile.get(f.path) ?? [];
    const pathTokens = new Set(tokenize(f.path));
    for (const [a, b] of chunkBoundaries(ls.length, syms.map((s) => s.line))) {
      const text = ls.slice(a - 1, b).join("\n");
      const toks = tokenize(text);
      for (const t of pathTokens) toks.push(t); // the path is part of every chunk's text
      const tf = new Map<string, number>();
      for (const t of toks) tf.set(t, (tf.get(t) ?? 0) + 1);
      for (const t of tf.keys()) df.set(t, (df.get(t) ?? 0) + 1);
      const chunkSyms = syms.filter((s) => s.line >= a && s.line <= b).map((s) => s.name.toLowerCase());
      chunks.push({ path: f.path, start: a, end: b, tf, len: toks.length, pathTokens, symbols: chunkSyms });
      total += toks.length;
    }
  }
  const readme = data.files.find((f) => /^readme(\.|$)/i.test(f.path))?.path ?? null;
  const important = [
    ...data.analysis.manifests.map((m) => m.file),
    ...data.analysis.entry_points.map((e) => e.path),
  ].filter((p, i, arr) => files.has(p) && arr.indexOf(p) === i);
  return { chunks, df, avgLen: chunks.length ? total / chunks.length : 1, files, lines, readme, important };
}

const OVERVIEW_RX =
  /\b(overview|tour|architecture|structure|high[- ]level|what (does|is) (this|the|it)|purpose|summar|getting started|how (do|to|can) (i |we )?(run|install|set ?up|deploy|start|build|use|test)|dependencies|tech stack|stack|diagram|onboard)/i;

export interface Retrieved {
  context: { path: string; start: number; end: number; text: string; pinned?: boolean }[];
  chars: number;
}

/**
 * Pick the most relevant code for a question.
 * @param question   the user's question
 * @param previous   the previous user question (adds weight for follow-ups like "what about errors there?")
 * @param pinned     files the user attached with @
 * @param budget     max characters of code to send
 */
export function retrieve(index: RepoIndex, question: string, previous: string | null, pinned: string[], budget: number): Retrieved {
  const N = index.chunks.length || 1;
  const weights = new Map<string, number>();
  for (const t of tokenize(question)) weights.set(t, 1);
  if (previous) for (const t of tokenize(previous)) if (!weights.has(t)) weights.set(t, 0.45);

  // Identifiers and file names written literally in the question get a strong boost.
  const literal = new Set(
    (question.match(/[A-Za-z_][\w.]*[A-Za-z0-9_]/g) ?? [])
      .filter((w) => /[_.]|[a-z][A-Z]/.test(w) || w.length > 5)
      .map((w) => w.toLowerCase()),
  );
  const mentionedFiles = new Set<string>();
  const qLower = question.toLowerCase();
  for (const p of index.files.keys()) {
    const base = p.split("/").pop()!.toLowerCase();
    if (base.length > 3 && (qLower.includes(p.toLowerCase()) || new RegExp(`(^|[^\\w])${escapeRx(base)}($|[^\\w])`).test(qLower))) {
      mentionedFiles.add(p);
    }
  }
  const overview = OVERVIEW_RX.test(question);

  const k1 = 1.2;
  const b = 0.75;
  const scored: { c: Chunk; s: number }[] = [];
  for (const c of index.chunks) {
    let s = 0;
    for (const [t, w] of weights) {
      const f = c.tf.get(t);
      if (!f) continue;
      const df = index.df.get(t) ?? 1;
      const idf = Math.log(1 + (N - df + 0.5) / (df + 0.5));
      s += w * idf * ((f * (k1 + 1)) / (f + k1 * (1 - b + (b * c.len) / index.avgLen)));
      if (c.pathTokens.has(t)) s += 1.2 * w;
    }
    for (const sym of c.symbols) {
      const short = sym.split(".").pop()!;
      if (literal.has(sym) || literal.has(short)) s += 6;
    }
    if (mentionedFiles.has(c.path)) s += 9;
    if (/(^|\/)(tests?|__tests__|spec)\//i.test(c.path) && !/test/i.test(question)) s *= 0.6;
    if (s > 0) scored.push({ c, s });
  }
  scored.sort((x, y) => y.s - x.s);

  const picked: { path: string; start: number; end: number; pinned?: boolean }[] = [];
  let used = 0;
  const cost = (path: string, a: number, z: number) => (index.lines.get(path)?.slice(a - 1, z).join("\n").length ?? 0) + 60;

  // 1. Files attached with @ — whole file when it fits, otherwise its best chunks.
  for (const p of pinned) {
    const ls = index.lines.get(p);
    if (!ls) continue;
    const whole = cost(p, 1, ls.length);
    if (whole <= budget * 0.6) {
      picked.push({ path: p, start: 1, end: ls.length, pinned: true });
      used += whole;
    } else {
      for (const { c } of scored.filter((x) => x.c.path === p).slice(0, 6)) {
        const k = cost(c.path, c.start, c.end);
        if (used + k > budget * 0.6) break;
        picked.push({ path: c.path, start: c.start, end: c.end, pinned: true });
        used += k;
      }
      if (!picked.some((x) => x.path === p)) {
        const end = Math.min(ls.length, 400);
        picked.push({ path: p, start: 1, end, pinned: true });
        used += cost(p, 1, end);
      }
    }
  }

  // 2. Orientation material for broad questions.
  if (overview) {
    for (const p of [index.readme, ...index.important].filter(Boolean) as string[]) {
      const ls = index.lines.get(p)!;
      const end = Math.min(ls.length, p === index.readme ? 160 : 120);
      const k = cost(p, 1, end);
      if (used + k > budget * 0.45) continue;
      picked.push({ path: p, start: 1, end });
      used += k;
    }
  }

  // 3. Best-scoring chunks until the budget is spent.
  for (const { c } of scored) {
    if (used >= budget) break;
    if (picked.some((p) => p.path === c.path && p.start <= c.start && p.end >= c.end)) continue;
    const k = cost(c.path, c.start, c.end);
    if (used + k > budget) continue;
    picked.push({ path: c.path, start: c.start, end: c.end });
    used += k;
    if (picked.length > 160) break;
  }

  // Nothing matched (e.g. "hi"): fall back to orientation material.
  if (!picked.length) {
    for (const p of [index.readme, ...index.important].filter(Boolean) as string[]) {
      const ls = index.lines.get(p)!;
      const end = Math.min(ls.length, 120);
      picked.push({ path: p, start: 1, end });
      used += cost(p, 1, end);
      if (used > budget * 0.3) break;
    }
  }

  // Merge overlapping/adjacent ranges per file and order by file then line.
  const byFile = new Map<string, { start: number; end: number; pinned?: boolean }[]>();
  for (const p of picked) {
    const arr = byFile.get(p.path) ?? [];
    arr.push({ start: p.start, end: p.end, pinned: p.pinned });
    byFile.set(p.path, arr);
  }
  const context: Retrieved["context"] = [];
  let chars = 0;
  const pinnedFirst = [...byFile.keys()].sort((a, z) => Number(pinned.includes(z)) - Number(pinned.includes(a)));
  for (const path of pinnedFirst) {
    const ranges = byFile.get(path)!.sort((a, z) => a.start - z.start);
    const merged: { start: number; end: number; pinned?: boolean }[] = [];
    for (const r of ranges) {
      const last = merged[merged.length - 1];
      if (last && r.start <= last.end + 3) {
        last.end = Math.max(last.end, r.end);
        last.pinned = last.pinned || r.pinned;
      } else merged.push({ ...r });
    }
    const ls = index.lines.get(path)!;
    for (const r of merged) {
      const text = ls.slice(r.start - 1, r.end).join("\n");
      chars += text.length;
      context.push({ path, start: r.start, end: r.end, text, ...(r.pinned ? { pinned: true } : {}) });
    }
  }
  return { context, chars };
}

function escapeRx(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
