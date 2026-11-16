import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { common, createLowlight } from "lowlight";
import { ArrowLeft, AtSign, Check, ChevronDown, ChevronRight, Copy, FileCode2, Folder, Search, X } from "lucide-react";
import { useStore, type PanelTab } from "../store";
import { compact, fmt, hljsLang } from "../lib/text";
import type { TreeEntry } from "../lib/types";
import { useCopy } from "./Markdown";

/* ── Highlighting helpers ─────────────────────────────────────────────── */
// The same highlighter (and grammars) that renders code in answers.
const lowlight = createLowlight(common);

type HNode = { type: string; value?: string; properties?: { className?: string[] }; children?: HNode[] };
function hastToHtml(n: HNode): string {
  if (n.type === "text") return escapeHtml(n.value ?? "");
  const inner = (n.children ?? []).map(hastToHtml).join("");
  if (n.type === "element") {
    const cls = n.properties?.className?.join(" ");
    return cls ? `<span class="${cls}">${inner}</span>` : `<span>${inner}</span>`;
  }
  return inner;
}

function escapeHtml(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Split highlighted HTML into lines, re-opening spans that cross line breaks. */
function splitHtmlLines(html: string): string[] {
  const out: string[] = [];
  const open: string[] = [];
  let cur = "";
  const re = /(<span[^>]*>)|(<\/span>)|(\n)|([^<\n]+|<)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html))) {
    if (m[1]) {
      open.push(m[1]);
      cur += m[1];
    } else if (m[2]) {
      open.pop();
      cur += m[2];
    } else if (m[3]) {
      out.push(cur + "</span>".repeat(open.length));
      cur = open.join("");
    } else cur += m[4];
  }
  out.push(cur + "</span>".repeat(open.length));
  return out;
}

const MAX_HIGHLIGHT_LINES = 4000;
const MAX_RENDER_LINES = 12000;

function FileViewer() {
  const file = useStore((s) => s.openFile)!;
  const repoFile = useStore((s) => s.index?.files.get(file.path));
  const closeFile = useStore((s) => s.closeFile);
  const togglePanel = useStore((s) => s.togglePanel);
  const addAttachment = useStore((s) => s.addAttachment);
  const focusComposer = useStore((s) => s.focusComposer);
  const toast = useStore((s) => s.toast);
  const [copied, copy] = useCopy();
  const scroller = useRef<HTMLDivElement>(null);

  const lines = useMemo(() => {
    if (!repoFile) return [];
    const src = repoFile.content;
    const lang = hljsLang(file.path);
    const n = src.split("\n").length;
    if (lang && n <= MAX_HIGHLIGHT_LINES && lowlight.registered(lang)) {
      try {
        return splitHtmlLines(hastToHtml(lowlight.highlight(lang, src) as unknown as HNode));
      } catch {
        /* fall through to plain text */
      }
    }
    return src.split("\n").map(escapeHtml);
  }, [repoFile, file.path]);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (file.start) {
      const target = el.querySelector<HTMLElement>(`[data-line="${file.start}"]`);
      if (target) el.scrollTop = Math.max(0, target.offsetTop - 80);
    } else el.scrollTop = 0;
  }, [file.path, file.start, lines]);

  if (!repoFile) return null;
  const start = file.start ?? 0;
  const end = file.end ?? file.start ?? 0;
  const shown = lines.slice(0, MAX_RENDER_LINES);
  return (
    <div className="viewer">
      <div className="viewer__head">
        <button type="button" className="icon-btn" aria-label="Back" title="Back" onClick={closeFile}>
          <ArrowLeft size={16} />
        </button>
        <span className="viewer__path" title={file.path}>
          <bdi>{file.path}</bdi>
        </span>
        <button type="button" className="icon-btn" aria-label={copied ? "Copied" : "Copy path"} title="Copy path" onClick={() => copy(file.path)}>
          {copied ? <Check size={15} /> : <Copy size={15} />}
        </button>
        <button type="button" className="icon-btn" aria-label="Close panel" title="Close" onClick={() => togglePanel(false)}>
          <X size={16} />
        </button>
      </div>
      <div className="viewer__bar">
        <span>{repoFile.lang ?? "Text"}</span>
        <span className="num">{fmt(lines.length)} lines</span>
        {repoFile.note && <span>{repoFile.note}</span>}
        <button
          type="button"
          className="btn"
          onClick={() => {
            addAttachment(file.path);
            focusComposer();
            toast(`${file.path.split("/").pop()} is attached to your next question.`);
          }}
        >
          <AtSign size={13} aria-hidden="true" /> Ask about this file
        </button>
      </div>
      <div className="viewer__code" ref={scroller} tabIndex={0} aria-label={`Contents of ${file.path}`}>
        {shown.map((html, i) => {
          const n = i + 1;
          const hl = start && n >= start && n <= end;
          return (
            <div key={i} className={`vline${hl ? " is-hl" : ""}`} data-line={n}>
              <span className="vline__n">{n}</span>
              <span className="vline__c" dangerouslySetInnerHTML={{ __html: html || " " }} />
            </div>
          );
        })}
        {lines.length > MAX_RENDER_LINES && <div className="viewer__more">Showing the first {fmt(MAX_RENDER_LINES)} lines.</div>}
      </div>
    </div>
  );
}

/* ── Overview ─────────────────────────────────────────────────────────── */
function Overview() {
  const data = useStore((s) => s.repoData)!;
  const openFileAt = useStore((s) => s.openFileAt);
  const [allRoutes, setAllRoutes] = useState(false);
  const a = data.analysis;
  const s = a.stats;
  const totalLangLines = a.languages.reduce((t, l) => t + l.lines, 0) || 1;
  const langs = a.languages.filter((l) => l.lines > 0).slice(0, 5);
  const other = totalLangLines - langs.reduce((t, l) => t + l.lines, 0);
  const routes = allRoutes ? a.routes : a.routes.slice(0, 8);
  return (
    <div className="panel__body">
      <section className="panel__section">
        <h3>At a glance</h3>
        <div className="stats">
          <div className="stat">
            <div className="stat__v">{compact(s.files)}</div>
            <div className="stat__l">Files</div>
          </div>
          <div className="stat">
            <div className="stat__v">{compact(s.code_lines)}</div>
            <div className="stat__l">Lines of code</div>
          </div>
          <div className="stat">
            <div className="stat__v">{compact(s.functions + s.methods)}</div>
            <div className="stat__l">Functions and methods</div>
          </div>
          <div className="stat">
            <div className="stat__v">{compact(s.classes + s.interfaces)}</div>
            <div className="stat__l">Classes and types</div>
          </div>
        </div>
      </section>

      {!!langs.length && (
        <section className="panel__section">
          <h3>Languages</h3>
          <div className="langbar" aria-hidden="true">
            {langs.map((l, i) => (
              <span key={l.name} style={{ width: `${(l.lines / totalLangLines) * 100}%`, background: `var(--lang-${i + 1})` }} />
            ))}
            {other > 0 && <span style={{ flex: 1, background: "var(--lang-5)" }} />}
          </div>
          <ul className="legend">
            {langs.map((l, i) => (
              <li key={l.name}>
                <span className="legend__sw" style={{ background: `var(--lang-${i + 1})` }} aria-hidden="true" />
                {l.name}
                <span className="legend__v">{Math.max(1, Math.round((l.lines / totalLangLines) * 100))}%</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!!(a.frameworks.length || a.infra.length) && (
        <section className="panel__section">
          <h3>Stack</h3>
          <div className="tags">
            {[...a.frameworks, ...a.infra].map((f) => (
              <span key={f} className="tag">
                {f}
              </span>
            ))}
          </div>
        </section>
      )}

      {!!a.entry_points.length && (
        <section className="panel__section">
          <h3>Entry points</h3>
          <ul className="rows">
            {a.entry_points.slice(0, 6).map((e) => (
              <li key={e.path}>
                <button type="button" className="row" onClick={() => openFileAt(e.path)}>
                  <FileCode2 size={14} aria-hidden="true" />
                  <span className="row__main">
                    {e.path}
                    <span className="row__sub">{e.reason}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!!a.routes.length && (
        <section className="panel__section">
          <h3>
            HTTP routes <small className="num">{a.routes.length}</small>
          </h3>
          <ul className="rows">
            {routes.map((r, i) => (
              <li key={`${r.method}${r.path}${i}`}>
                <button type="button" className="row" onClick={() => openFileAt(r.file, r.line, r.line)} title={`${r.file}:${r.line}`}>
                  <span className="method">{r.method}</span>
                  <span className="row__main">{r.path}</span>
                </button>
              </li>
            ))}
          </ul>
          {a.routes.length > 8 && (
            <button type="button" className="btn btn--ghost" style={{ marginTop: 4, height: 28, padding: "0 6px" }} onClick={() => setAllRoutes((v) => !v)}>
              {allRoutes ? "Show fewer" : `Show all ${a.routes.length}`}
            </button>
          )}
        </section>
      )}

      {!!a.largest_files.length && (
        <section className="panel__section">
          <h3>Largest files</h3>
          <ul className="rows">
            {a.largest_files.slice(0, 6).map((f) => (
              <li key={f.path}>
                <button type="button" className="row" onClick={() => openFileAt(f.path)}>
                  <FileCode2 size={14} aria-hidden="true" />
                  <span className="row__main">{f.path}</span>
                  <span className="row__v">{fmt(f.lines)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!!a.patterns.length && (
        <section className="panel__section">
          <h3>Signals</h3>
          <ul className="signals">
            {a.patterns.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </section>
      )}

      <p className="panel__note">
        Numbers come from static analysis of {fmt(s.text_files)} readable files{data.pack.omitted_files ? `; ${fmt(data.pack.omitted_files)} large or low-priority files weren't loaded for search` : ""}.
        {s.truncated ? " The repository is very large, so part of it was skipped." : ""}
      </p>
    </div>
  );
}

/* ── Files ────────────────────────────────────────────────────────────── */
interface Node {
  name: string;
  path: string;
  children?: Map<string, Node>;
  entry?: TreeEntry;
}

function buildTree(entries: TreeEntry[]): Node {
  const root: Node = { name: "", path: "", children: new Map() };
  for (const e of entries) {
    const parts = e.path.split("/");
    let node = root;
    parts.forEach((part, i) => {
      const isFile = i === parts.length - 1;
      const path = parts.slice(0, i + 1).join("/");
      if (!node.children!.has(part)) node.children!.set(part, isFile ? { name: part, path, entry: e } : { name: part, path, children: new Map() });
      node = node.children!.get(part)!;
    });
  }
  return root;
}

const KIND_NOTE: Record<string, string> = {
  binary: "binary",
  large: "too large",
  generated: "generated",
  secret: "hidden",
  skipped: "skipped",
};

function TreeNode({ node, depth, filter }: { node: Node; depth: number; filter: string }) {
  const openFileAt = useStore((s) => s.openFileAt);
  const has = useStore((s) => s.index?.files);
  const [open, setOpen] = useState(depth < 1);
  const isOpen = open || !!filter;
  if (!node.children) {
    const readable = has?.has(node.path);
    const note = !readable ? KIND_NOTE[node.entry?.kind ?? ""] ?? "not loaded" : null;
    return (
      <li>
        <button type="button" className={`tree__node${readable ? "" : " is-dim"}`} onClick={() => readable && openFileAt(node.path)} aria-disabled={!readable}>
          <FileCode2 size={13} aria-hidden="true" />
          <span>{node.name}</span>
          {note && <small>{note}</small>}
        </button>
      </li>
    );
  }
  const kids = [...node.children.values()].sort((a, b) => Number(!a.children) - Number(!b.children) || a.name.localeCompare(b.name));
  return (
    <li>
      <button type="button" className="tree__node" aria-expanded={isOpen} onClick={() => setOpen((v) => !v)}>
        {isOpen ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
        <Folder size={13} aria-hidden="true" />
        <span>{node.name}</span>
      </button>
      {isOpen && (
        <ul>
          {kids.map((k) => (
            <TreeNode key={k.path} node={k} depth={depth + 1} filter={filter} />
          ))}
        </ul>
      )}
    </li>
  );
}

function Files() {
  const tree = useStore((s) => s.repoData!.tree);
  const [filter, setFilter] = useState("");
  const root = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return buildTree(f ? tree.filter((e) => e.path.toLowerCase().includes(f)).slice(0, 400) : tree);
  }, [tree, filter]);
  const kids = [...root.children!.values()].sort((a, b) => Number(!a.children) - Number(!b.children) || a.name.localeCompare(b.name));
  return (
    <div className="panel__body" style={{ gap: 12 }}>
      <div className="filter">
        <Search size={14} aria-hidden="true" />
        <label htmlFor="file-filter" className="sr-only">
          Filter files
        </label>
        <input id="file-filter" placeholder={`Filter ${fmt(tree.length)} files`} value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      <ul className="tree">
        {kids.map((k) => (
          <TreeNode key={k.path + filter} node={k} depth={0} filter={filter} />
        ))}
        {!kids.length && <li className="nav-empty">No files match.</li>}
      </ul>
    </div>
  );
}

/* ── Symbols ──────────────────────────────────────────────────────────── */
function Symbols() {
  const symbols = useStore((s) => s.repoData!.analysis.symbols);
  const openFileAt = useStore((s) => s.openFileAt);
  const [q, setQ] = useState("");
  const list = useMemo(() => {
    const f = q.trim().toLowerCase();
    const res = f ? symbols.filter((s) => s.name.toLowerCase().includes(f) || s.file.toLowerCase().includes(f)) : symbols;
    return res.slice(0, 300);
  }, [symbols, q]);
  return (
    <div className="panel__body" style={{ gap: 12 }}>
      <div className="filter">
        <Search size={14} aria-hidden="true" />
        <label htmlFor="sym-filter" className="sr-only">
          Search symbols
        </label>
        <input id="sym-filter" placeholder={`Search ${fmt(symbols.length)} symbols`} value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <ul className="rows">
        {list.map((s, i) => (
          <li key={`${s.file}:${s.line}:${i}`}>
            <button type="button" className="row" onClick={() => openFileAt(s.file, s.line, s.end && s.end - s.line < 80 ? s.end : s.line)} title={s.doc || undefined}>
              <span className="sym-kind">{s.kind}</span>
              <span className="row__main">
                {s.name}
                <span className="row__sub">
                  {s.file}:{s.line}
                </span>
              </span>
            </button>
          </li>
        ))}
        {!list.length && <li className="nav-empty">No symbols match.</li>}
      </ul>
      {symbols.length > 300 && list.length === 300 && <p className="panel__note">Showing the first 300. Search to narrow the list.</p>}
    </div>
  );
}

export function RepoPanel() {
  const tab = useStore((s) => s.panelTab);
  const setTab = useStore((s) => s.setPanelTab);
  const openFile = useStore((s) => s.openFile);
  const togglePanel = useStore((s) => s.togglePanel);
  const tabs: { id: PanelTab; label: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "files", label: "Files" },
    { id: "symbols", label: "Symbols" },
  ];
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && window.matchMedia("(max-width: 1179px)").matches) togglePanel(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePanel]);
  return (
    <aside className="panel" aria-label="Repository details">
      {openFile ? (
        <FileViewer />
      ) : (
        <>
          <div className="panel__head">
            <div className="tabs" role="tablist" aria-label="Repository views">
              {tabs.map((t) => (
                <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}>
                  {t.label}
                </button>
              ))}
            </div>
            <button type="button" className="icon-btn" aria-label="Close panel" title="Close" onClick={() => togglePanel(false)}>
              <X size={16} />
            </button>
          </div>
          {tab === "overview" ? <Overview /> : tab === "files" ? <Files /> : <Symbols />}
        </>
      )}
    </aside>
  );
}
