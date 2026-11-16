import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  BookMarked,
  Bug,
  Compass,
  Download,
  GitBranch,
  GitCommitHorizontal,
  LoaderCircle,
  Menu,
  Network,
  PanelRight,
  Play,
  RefreshCw,
  Route as RouteIcon,
  CircleAlert,
} from "lucide-react";
import { useStore } from "../store";
import { compact } from "../lib/text";
import type { Message } from "../lib/types";
import { Composer } from "./Composer";
import { Logo } from "./Logo";
import { MessageItem } from "./MessageItem";

const EMPTY: Message[] = [];

function Topbar() {
  const repo = useStore((s) => s.repoData?.repo);
  const stats = useStore((s) => s.repoData?.analysis.stats);
  const entry = useStore((s) => s.repos.find((r) => r.id === s.activeRepoId));
  const panelOpen = useStore((s) => s.panelOpen);
  const togglePanel = useStore((s) => s.togglePanel);
  const setSidebarOpen = useStore((s) => s.setSidebarOpen);
  const exportChat = useStore((s) => s.exportChat);
  const refreshRepo = useStore((s) => s.refreshRepo);
  const hasMessages = useStore((s) => !!(s.activeChatId && s.messagesByChat[s.activeChatId]?.length));
  if (!repo) return null;
  const commitUrl = repo.html_url ? `${repo.html_url}/tree/${repo.sha}` : null;
  return (
    <header className="topbar">
      <button type="button" className="icon-btn topbar__menu" aria-label="Open menu" onClick={() => setSidebarOpen(true)}>
        <Menu size={18} />
      </button>
      <h1 className="topbar__title">
        <BookMarked size={16} aria-hidden="true" />
        {repo.html_url ? (
          <a href={repo.html_url} target="_blank" rel="noopener noreferrer" title="Open on GitHub">
            {repo.full_name}
          </a>
        ) : (
          <span>{repo.full_name}</span>
        )}
      </h1>
      <div className="topbar__meta">
        {repo.ref && (
          <span className="pill pill--hide-sm" title="Branch">
            <GitBranch size={13} aria-hidden="true" />
            {repo.ref}
          </span>
        )}
        {commitUrl ? (
          <a className="pill mono pill--hide-sm" href={commitUrl} target="_blank" rel="noopener noreferrer" title="Analyzed commit">
            <GitCommitHorizontal size={13} aria-hidden="true" />
            {repo.sha.slice(0, 7)}
          </a>
        ) : (
          <span className="pill pill--hide-sm">Uploaded ZIP</span>
        )}
        {stats && (
          <span className="pill pill--hide-sm" title={`${stats.files} files, ${stats.lines.toLocaleString()} lines`}>
            <span className="dot" aria-hidden="true" />
            Indexed {compact(stats.text_files)} files
          </span>
        )}
      </div>
      <span className="topbar__spacer" />
      {entry?.source === "github" && (
        <button type="button" className="icon-btn" aria-label="Check for new commits" title="Check for new commits" onClick={() => refreshRepo()}>
          <RefreshCw size={16} />
        </button>
      )}
      <button type="button" className="icon-btn" aria-label="Export chat as Markdown" title="Export chat" disabled={!hasMessages} onClick={() => exportChat()}>
        <Download size={16} />
      </button>
      <button
        type="button"
        className="icon-btn"
        aria-label={panelOpen ? "Hide repository panel" : "Show repository panel"}
        aria-pressed={panelOpen}
        title="Repository panel"
        onClick={() => togglePanel()}
      >
        <PanelRight size={17} />
      </button>
    </header>
  );
}

function Intro() {
  const data = useStore((s) => s.repoData);
  const entry = useStore((s) => s.repos.find((r) => r.id === s.activeRepoId));
  const send = useStore((s) => s.send);
  const configured = useStore((s) => s.config?.configured);
  const starters = useMemo(() => {
    if (!data) return [];
    const a = data.analysis;
    const list: { icon: typeof Compass; q: string; sub: string }[] = [
      { icon: Compass, q: "Give me a tour of this codebase", sub: "Purpose, structure and how the pieces connect" },
      { icon: Play, q: "How do I run this project locally?", sub: "Setup, dependencies and commands" },
      { icon: Network, q: "Draw the architecture as a diagram", sub: "Main components and how data flows" },
    ];
    if (a.routes.length) list.push({ icon: RouteIcon, q: "List the API endpoints and what each one does", sub: `${a.routes.length} route${a.routes.length === 1 ? "" : "s"} found` });
    else if (a.entry_points[0]) list.push({ icon: RouteIcon, q: `Walk me through ${a.entry_points[0].path}`, sub: a.entry_points[0].reason });
    list.push({ icon: Bug, q: "What are the riskiest parts of this code?", sub: "Bugs, security and maintainability" });
    if (a.stats.test_files === 0) list.push({ icon: Bug, q: "How would you add tests to this project?", sub: "No test files were found" });
    else list.push({ icon: Compass, q: "How is this project tested?", sub: `${a.stats.test_files} test files` });
    return list.slice(0, 6);
  }, [data]);
  if (!data) return null;
  const a = data.analysis;
  return (
    <div className="intro">
      <div className="intro__repo">
        <Logo size={40} />
        <div>
          <h2>{data.repo.full_name}</h2>
          {entry?.description ? <p>{entry.description}</p> : null}
          <div className="intro__tags">
            {a.languages.slice(0, 3).map((l) => (
              <span key={l.name} className="tag">
                {l.name}
              </span>
            ))}
            {a.frameworks.slice(0, 4).map((f) => (
              <span key={f} className="tag">
                {f}
              </span>
            ))}
            <span className="tag num">{compact(a.stats.lines)} lines</span>
          </div>
        </div>
      </div>
      {configured === false && (
        <div className="msg-error" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          <span className="msg-error__text">
            The AI service isn't set up on this deployment yet. Add <span className="mono">OPENAI_API_KEY</span> in Vercel's environment variables and redeploy.
          </span>
        </div>
      )}
      <div>
        <p className="intro__label">Start with a question</p>
        <div className="starters">
          {starters.map(({ icon: Icon, q, sub }) => (
            <button key={q} type="button" className="starter" onClick={() => send(q)}>
              <Icon size={16} aria-hidden="true" />
              <span>
                {q}
                <small>{sub}</small>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export function ChatView() {
  const loading = useStore((s) => s.loadingRepo);
  const hasRepo = useStore((s) => !!s.repoData);
  const chatId = useStore((s) => s.activeChatId);
  const messages = useStore((s) => (s.activeChatId ? s.messagesByChat[s.activeChatId] ?? EMPTY : EMPTY));
  const busy = useStore((s) => (s.activeChatId ? !!s.streaming[s.activeChatId] : false));
  const thread = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const lastCount = useRef(0);

  // Jump to the bottom when switching chats.
  useLayoutEffect(() => {
    stick.current = true;
    const el = thread.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chatId]);

  // Follow the stream while the reader is at the bottom; always follow a newly sent question.
  useLayoutEffect(() => {
    const el = thread.current;
    if (!el) return;
    if (messages.length > lastCount.current) stick.current = true;
    lastCount.current = messages.length;
    if (stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // Content can also grow after a render (diagrams, fonts): keep following if pinned.
  useEffect(() => {
    const el = thread.current;
    const box = inner.current;
    if (!el || !box || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (stick.current) el.scrollTop = el.scrollHeight;
    });
    ro.observe(box);
    return () => ro.disconnect();
  }, [hasRepo]);

  const onScroll = () => {
    const el = thread.current!;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stick.current = atBottom;
    setShowJump(!atBottom && messages.length > 0 && el.scrollHeight - el.scrollTop - el.clientHeight > 400);
  };

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant")?.id;
  const lastUser = [...messages].reverse().find((m) => m.role === "user")?.id;

  return (
    <>
      <Topbar />
      <div className="thread" ref={thread} onScroll={onScroll}>
        <div className="thread__inner" ref={inner}>
          {loading || !hasRepo ? (
            <div className="thinking" style={{ padding: "12vh 0", justifyContent: "center" }}>
              <LoaderCircle size={16} className="spin" aria-hidden="true" />
              Loading repository…
            </div>
          ) : messages.length === 0 ? (
            <Intro />
          ) : (
            messages.map((m) => <MessageItem key={m.id} msg={m} isLast={m.id === lastAssistant} canEdit={m.id === lastUser && !busy} />)
          )}
        </div>
      </div>
      {hasRepo && (
        <div className="composer-dock">
          {showJump && (
            <button
              type="button"
              className="btn jump"
              onClick={() => {
                stick.current = true;
                setShowJump(false);
                thread.current!.scrollTo({ top: thread.current!.scrollHeight, behavior: "smooth" });
              }}
            >
              <ArrowDown size={14} aria-hidden="true" /> Latest
            </button>
          )}
          <Composer />
        </div>
      )}
    </>
  );
}
