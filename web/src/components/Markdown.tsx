import { memo, useEffect, useMemo, useState, type ReactNode } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { Check, Code2, Copy, FileCode2, Workflow } from "lucide-react";
import { useStore } from "../store";
import { linkCitations, parseCiteHref, uid } from "../lib/text";

type HastNode = { type: string; value?: string; tagName?: string; properties?: Record<string, unknown>; children?: HastNode[] };

function hastText(n?: HastNode): string {
  if (!n) return "";
  if (n.type === "text") return n.value ?? "";
  return (n.children ?? []).map(hastText).join("");
}

export function useCopy(timeout = 1600): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  const copy = (text: string) => {
    const done = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), timeout);
    };
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text) && done());
    else if (fallbackCopy(text)) done();
  };
  return [copied, copy];
}

function fallbackCopy(text: string) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  ta.remove();
  return ok;
}

function CodeBlock({ lang, raw, children }: { lang: string; raw: string; children: ReactNode }) {
  const [copied, copy] = useCopy();
  return (
    <div className="codeblock">
      <div className="codeblock__head">
        <span>{lang || "text"}</span>
        <span style={{ flex: 1 }} />
        <button type="button" className="icon-btn" aria-label={copied ? "Copied" : "Copy code"} title="Copy code" onClick={() => copy(raw)}>
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

let mermaidReady: Promise<typeof import("mermaid").default> | null = null;
function loadMermaid() {
  mermaidReady ??= import("mermaid").then((m) => m.default);
  return mermaidReady;
}

function Mermaid({ code, streaming }: { code: string; streaming: boolean }) {
  const theme = useStore((s) => s.theme);
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [showCode, setShowCode] = useState(false);
  const [copied, copy] = useCopy();

  useEffect(() => {
    if (streaming) return;
    let alive = true;
    (async () => {
      try {
        const mermaid = await loadMermaid();
        const dark = theme === "dark";
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
          suppressErrorRendering: true,
          fontFamily: "Geist Variable, Geist, system-ui, sans-serif",
          themeVariables: dark
            ? { background: "#17181d", primaryColor: "#1c1d23", primaryBorderColor: "#8b90ff", primaryTextColor: "#edeef2",
                secondaryColor: "#212336", tertiaryColor: "#17181d", lineColor: "#8e919d", textColor: "#edeef2",
                mainBkg: "#1c1d23", nodeBorder: "#8b90ff", clusterBkg: "#141519", clusterBorder: "#33353d",
                edgeLabelBackground: "#17181d", actorBkg: "#1c1d23", actorBorder: "#8b90ff", actorTextColor: "#edeef2",
                signalColor: "#b4b6c0", signalTextColor: "#edeef2", noteBkgColor: "#212336", noteTextColor: "#edeef2", fontSize: "14px" }
            : { background: "#f3f3f6", primaryColor: "#ffffff", primaryBorderColor: "#5e63f0", primaryTextColor: "#121317",
                secondaryColor: "#eeeffe", tertiaryColor: "#f3f3f6", lineColor: "#626673", textColor: "#121317",
                mainBkg: "#ffffff", nodeBorder: "#5e63f0", clusterBkg: "#fbfbfc", clusterBorder: "#d4d4db",
                edgeLabelBackground: "#f3f3f6", noteBkgColor: "#eeeffe", noteTextColor: "#121317", fontSize: "14px" },
        });
        const valid = await mermaid.parse(code, { suppressErrors: true });
        if (!valid) throw new Error("invalid");
        const { svg } = await mermaid.render("mmd-" + uid(), code);
        if (alive) {
          setSvg(svg);
          setFailed(false);
        }
      } catch {
        if (alive) setFailed(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [code, streaming, theme]);

  if (failed) {
    return (
      <CodeBlock lang="mermaid" raw={code}>
        <code>{code}</code>
      </CodeBlock>
    );
  }
  return (
    <figure className="diagram" style={{ margin: "0 0 14px" }}>
      <div className="diagram__head">
        <Workflow size={14} aria-hidden="true" />
        <span>Diagram</span>
        <span style={{ flex: 1 }} />
        <button type="button" className="icon-btn icon-btn--sm" aria-pressed={showCode} aria-label="Show diagram source" title="Show source" onClick={() => setShowCode((v) => !v)}>
          <Code2 size={14} />
        </button>
        <button type="button" className="icon-btn icon-btn--sm" aria-label={copied ? "Copied" : "Copy diagram source"} title="Copy source" onClick={() => copy(code)}>
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
      </div>
      {showCode ? (
        <pre style={{ margin: 0, padding: "12px 16px", fontFamily: "var(--mono)", fontSize: 13, overflowX: "auto" }}>{code}</pre>
      ) : (
        <div className="diagram__body">
          {svg ? <div role="img" aria-label="Diagram" dangerouslySetInnerHTML={{ __html: svg }} /> : <span className="diagram__wait">{streaming ? "The diagram appears when the answer finishes." : "Drawing the diagram…"}</span>}
        </div>
      )}
    </figure>
  );
}

function Cite({ path, start, end }: { path: string; start: number; end: number }) {
  const exists = useStore((s) => !!s.index?.files.has(path));
  const openFileAt = useStore((s) => s.openFileAt);
  const label = end > start ? `${start}–${end}` : `${start}`;
  const name = path.split("/").pop();
  if (!exists) return <span className="cite--missing">{path}:{label}</span>;
  return (
    <button type="button" className="chip cite" title={`Open ${path}, lines ${label}`} onClick={() => openFileAt(path, start, end)}>
      <FileCode2 size={12} aria-hidden="true" />
      <span className="chip__path">{name}</span>
      <span className="chip__lines">{label}</span>
    </button>
  );
}

const urlTransform = (url: string) => (url.startsWith("#cite=") ? url : defaultUrlTransform(url));
const rehypePlugins: any[] = [[rehypeHighlight, { detect: false, plainText: ["mermaid", "text", "txt", "plain", "output", "console"] }]];
const remarkPlugins = [remarkGfm];

function MarkdownImpl({ text, streaming = false }: { text: string; streaming?: boolean }) {
  const md = useMemo(() => linkCitations(text), [text]);
  const components = useMemo<Components>(
    () => ({
      a({ href, children }) {
        const cite = href ? parseCiteHref(href) : null;
        if (cite) return <Cite {...cite} />;
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
      pre({ node, children }) {
        const code = (node as unknown as HastNode)?.children?.find((c) => c.tagName === "code");
        const cls = ((code?.properties?.className as string[]) ?? []).join(" ");
        const lang = /language-([\w+#-]+)/.exec(cls)?.[1] ?? "";
        const raw = hastText(code).replace(/\n$/, "");
        if (lang === "mermaid") return <Mermaid code={raw} streaming={streaming} />;
        return (
          <CodeBlock lang={lang} raw={raw}>
            {children}
          </CodeBlock>
        );
      },
      table({ children }) {
        return (
          <div className="table-wrap">
            <table>{children}</table>
          </div>
        );
      },
    }),
    [streaming],
  );
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} urlTransform={urlTransform} components={components}>
        {md}
      </ReactMarkdown>
    </div>
  );
}

export const Markdown = memo(MarkdownImpl);
