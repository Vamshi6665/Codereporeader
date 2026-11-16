export const FOLLOWUP_MARKER = "%%FOLLOWUPS%%";

/** Split a model answer into the visible body and its follow-up questions. */
export function splitFollowups(text: string): { body: string; followups: string[] } {
  const i = text.indexOf(FOLLOWUP_MARKER);
  if (i < 0) {
    // While streaming, hide a partially written marker at the very end.
    for (let k = FOLLOWUP_MARKER.length - 1; k > 0; k--) {
      if (text.endsWith(FOLLOWUP_MARKER.slice(0, k))) return { body: text.slice(0, -k), followups: [] };
    }
    return { body: text, followups: [] };
  }
  const followups = text
    .slice(i + FOLLOWUP_MARKER.length)
    .split(/\||\n/)
    .map((s) => s.replace(/^[\s\-*\d.]+/, "").trim())
    .filter((s) => s.length > 2 && s.length < 140)
    .slice(0, 3);
  return { body: text.slice(0, i).trimEnd(), followups };
}

const CITE_RX = /\[((?:[\w@.+-]+\/)*[\w@.+-]+\.?[\w-]*):L(\d+)(?:\s*[-–]\s*L?(\d+))?\]/g;

/**
 * Turn [path:L10-L20] citations into markdown links (#cite=…) outside of code,
 * so the renderer can show them as clickable chips.
 */
export function linkCitations(md: string): string {
  const parts = md.split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g);
  return parts
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(CITE_RX, (_m, path: string, a: string, z?: string) => {
            const label = z && z !== a ? `${path}:${a}–${z}` : `${path}:${a}`;
            return `[${label}](#cite=${encodeURIComponent(path)}~${a}~${z ?? a})`;
          }),
    )
    .join("");
}

export function parseCiteHref(href: string): { path: string; start: number; end: number } | null {
  if (!href.startsWith("#cite=")) return null;
  let raw = href.slice(6);
  try {
    raw = decodeURIComponent(raw);
  } catch {
    /* keep raw */
  }
  const parts = raw.split("~");
  if (parts.length < 3) return null;
  const z = parts.pop()!;
  const a = parts.pop()!;
  const start = parseInt(a, 10) || 1;
  return { path: parts.join("~"), start, end: Math.max(start, parseInt(z, 10) || start) };
}

export function citedPaths(md: string): string[] {
  const out = new Set<string>();
  for (const m of md.matchAll(CITE_RX)) out.add(m[1]);
  return [...out];
}

export const fmt = (n: number) => n.toLocaleString("en-US");

export function compact(n: number) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1) + "M";
  if (n >= 10_000) return Math.round(n / 1000) + "k";
  return fmt(n);
}

export function timeGroup(ts: number): "Today" | "Yesterday" | "Previous 7 days" | "Older" {
  const d = new Date(ts);
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (ts >= startToday) return "Today";
  if (ts >= startToday - 86400000) return "Yesterday";
  if (ts >= startToday - 7 * 86400000) return "Previous 7 days";
  void d;
  return "Older";
}

export function uid() {
  return (crypto.randomUUID?.() ?? Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/-/g, "").slice(0, 16);
}

const EXT_HLJS: Record<string, string> = {
  py: "python", pyi: "python", ipynb: "python", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
  ts: "typescript", tsx: "typescript", mts: "typescript", java: "java", kt: "kotlin", kts: "kotlin", cs: "csharp",
  c: "c", h: "c", cpp: "cpp", cc: "cpp", hpp: "cpp", go: "go", rs: "rust", rb: "ruby", php: "php", swift: "swift",
  scala: "scala", sh: "bash", bash: "bash", zsh: "bash", ps1: "powershell", sql: "sql", html: "xml", htm: "xml",
  xml: "xml", svg: "xml", vue: "xml", svelte: "xml", css: "css", scss: "scss", less: "less", json: "json",
  yml: "yaml", yaml: "yaml", toml: "ini", ini: "ini", md: "markdown", mdx: "markdown", dockerfile: "dockerfile",
  makefile: "makefile", r: "r", lua: "lua", dart: "dart", graphql: "graphql", gql: "graphql", diff: "diff",
};

export function hljsLang(path: string): string | undefined {
  const base = path.split("/").pop()!.toLowerCase();
  if (base === "dockerfile" || base.startsWith("dockerfile.")) return "dockerfile";
  if (base === "makefile") return "makefile";
  const ext = base.includes(".") ? base.split(".").pop()! : "";
  return EXT_HLJS[ext];
}

/** Stable colors for the language bar (accent first, then neutral steps). */
export const LANG_COLORS = ["var(--accent)", "#6E7488", "#535868", "#43464F", "#363840", "#2C2E35"];
