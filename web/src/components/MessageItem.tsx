import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronRight, CircleAlert, Copy, FileCode2, Pencil, RefreshCw, Zap, Brain } from "lucide-react";
import { useStore } from "../store";
import { citedPaths, splitFollowups } from "../lib/text";
import type { Message } from "../lib/types";
import { Logo } from "./Logo";
import { Markdown, useCopy } from "./Markdown";

function Thinking({ since, excerpts }: { since: number; excerpts: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const secs = Math.max(0, Math.floor((now - since) / 1000));
  return (
    <div className="thinking" role="status">
      <span className="thinking__text">{secs < 2 && excerpts ? `Reading ${excerpts} code excerpt${excerpts === 1 ? "" : "s"}` : "Thinking"}</span>
      {secs >= 3 && <span className="num">{secs}s</span>}
    </div>
  );
}

function Excerpts({ msg }: { msg: Message }) {
  const [open, setOpen] = useState(false);
  const openFileAt = useStore((s) => s.openFileAt);
  const ex = msg.excerpts ?? [];
  if (!ex.length) return null;
  const files = new Set(ex.map((e) => e.path)).size;
  return (
    <div className="searched">
      <button type="button" className="searched__toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <ChevronRight size={14} aria-hidden="true" />
        Read {ex.length} excerpt{ex.length === 1 ? "" : "s"} from {files} file{files === 1 ? "" : "s"}
      </button>
      {open && (
        <div className="searched__list">
          {ex.map((e) => (
            <button key={`${e.path}:${e.start}`} type="button" className="chip" onClick={() => openFileAt(e.path, e.start, e.end)} title={`Open ${e.path}`}>
              <FileCode2 size={13} aria-hidden="true" />
              <span className="chip__path">{e.path}</span>
              <span className="chip__lines">
                {e.start}–{e.end}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function UserMessage({ msg, canEdit }: { msg: Message; canEdit: boolean }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(msg.content);
  const [copied, copy] = useCopy();
  const editAndResend = useStore((s) => s.editAndResend);
  const openFileAt = useStore((s) => s.openFileAt);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (editing) {
      ref.current?.focus();
      ref.current?.setSelectionRange(text.length, text.length);
    }
  }, [editing]); // eslint-disable-line react-hooks/exhaustive-deps

  if (editing) {
    return (
      <form
        className="msg-edit"
        onSubmit={(e) => {
          e.preventDefault();
          setEditing(false);
          editAndResend(msg.id, text);
        }}
      >
        <label className="sr-only" htmlFor={`edit-${msg.id}`}>
          Edit your question
        </label>
        <textarea
          id={`edit-${msg.id}`}
          ref={ref}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setEditing(false);
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              setEditing(false);
              editAndResend(msg.id, text);
            }
          }}
        />
        <div className="msg-edit__row">
          <button type="button" className="btn" onClick={() => setEditing(false)}>
            Cancel
          </button>
          <button type="submit" className="btn btn--primary" disabled={!text.trim()}>
            Send
          </button>
        </div>
      </form>
    );
  }
  return (
    <div className="msg-user">
      {!!msg.attachments?.length && (
        <div className="msg-user__files">
          {msg.attachments.map((p) => (
            <button key={p} type="button" className="chip" onClick={() => openFileAt(p)} title={`Open ${p}`}>
              <FileCode2 size={13} aria-hidden="true" />
              <span className="chip__path">{p}</span>
            </button>
          ))}
        </div>
      )}
      <div className="msg-user__bubble">{msg.content}</div>
      <div className="msg-user__actions">
        <button type="button" className="icon-btn icon-btn--sm" aria-label={copied ? "Copied" : "Copy question"} title="Copy" onClick={() => copy(msg.content)}>
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
        {canEdit && (
          <button type="button" className="icon-btn icon-btn--sm" aria-label="Edit question" title="Edit and resend" onClick={() => { setText(msg.content); setEditing(true); }}>
            <Pencil size={14} />
          </button>
        )}
      </div>
    </div>
  );
}

function AssistantMessage({ msg, isLast }: { msg: Message; isLast: boolean }) {
  const regenerate = useStore((s) => s.regenerate);
  const send = useStore((s) => s.send);
  const openFileAt = useStore((s) => s.openFileAt);
  const busy = useStore((s) => (s.activeChatId ? !!s.streaming[s.activeChatId] : false));
  const files = useStore((s) => s.index?.files);
  const [copied, copy] = useCopy();
  const { body, followups } = useMemo(() => splitFollowups(msg.content), [msg.content]);
  const streaming = msg.status === "streaming" || msg.status === "thinking";
  const sources = useMemo(() => {
    if (streaming) return [];
    const ranges = new Map<string, { start: number; end: number }>();
    for (const m of body.matchAll(/\[((?:[\w@.+-]+\/)*[\w@.+-]+\.?[\w-]*):L(\d+)(?:\s*[-–]\s*L?(\d+))?\]/g)) {
      if (!ranges.has(m[1])) ranges.set(m[1], { start: +m[2], end: +(m[3] ?? m[2]) });
    }
    return citedPaths(body)
      .filter((p) => files?.has(p))
      .map((p) => ({ path: p, ...ranges.get(p)! }));
  }, [body, streaming, files]);

  const ModeIcon = msg.mode === "deep" ? Brain : Zap;
  return (
    <article className={`msg-ai${streaming ? " is-streaming" : ""}`} aria-busy={streaming}>
      <div className="msg-ai__avatar">
        <Logo size={28} />
      </div>
      <div className="msg-ai__body">
        <div className="msg-ai__head">
          <span>CodeRepoReader</span>
          {msg.modelLabel && (
            <span className="badge" title={msg.mode === "deep" ? "Deep mode" : "Fast mode"}>
              <ModeIcon size={11} aria-hidden="true" />
              {msg.modelLabel}
            </span>
          )}
        </div>
        <Excerpts msg={msg} />
        {msg.status === "thinking" && !body && <Thinking since={msg.createdAt} excerpts={msg.excerpts?.length ?? 0} />}
        {body && <Markdown text={body} streaming={streaming} />}
        {msg.status === "error" && (
          <div className="msg-error" role="alert">
            <CircleAlert size={16} aria-hidden="true" />
            <span className="msg-error__text">{msg.error?.message ?? "Something went wrong."}</span>
            {isLast && !busy && (
              <button type="button" className="btn" onClick={() => regenerate(msg.id)}>
                <RefreshCw size={14} aria-hidden="true" /> Retry
              </button>
            )}
          </div>
        )}
        {msg.status === "stopped" && <div className="msg-note">Stopped before the answer finished.</div>}
        {msg.status === "done" && (msg.finish === "length" || msg.finish === "time") && isLast && (
          <div className="msg-note">
            This answer reached its length limit.{" "}
            <button type="button" className="btn btn--ghost" style={{ height: 26, padding: "0 6px" }} onClick={() => send("Continue from exactly where you stopped.")}>
              Continue
            </button>
          </div>
        )}
        {!!sources.length && (
          <div className="sources">
            <span className="sources__label">Sources</span>
            {sources.slice(0, 8).map((s) => (
              <button key={s.path} type="button" className="chip" onClick={() => openFileAt(s.path, s.start, s.end)} title={`Open ${s.path}`}>
                <FileCode2 size={13} aria-hidden="true" />
                <span className="chip__path">{s.path}</span>
              </button>
            ))}
          </div>
        )}
        {!streaming && body && (
          <div className="msg-actions">
            <button type="button" className="icon-btn" aria-label={copied ? "Copied" : "Copy answer"} title="Copy answer" onClick={() => copy(body)}>
              {copied ? <Check size={16} /> : <Copy size={16} />}
            </button>
            {!busy && (
              <button type="button" className="icon-btn" aria-label="Regenerate answer" title="Regenerate" onClick={() => regenerate(msg.id)}>
                <RefreshCw size={16} />
              </button>
            )}
            {msg.elapsedMs ? <span className="msg-actions__meta num">{(msg.elapsedMs / 1000).toFixed(1)} s</span> : null}
          </div>
        )}
        {isLast && msg.status === "done" && !!followups.length && !busy && (
          <div className="followups">
            {followups.map((f) => (
              <button key={f} type="button" className="btn followup" onClick={() => send(f)}>
                {f}
              </button>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

function MessageItemImpl({ msg, isLast, canEdit }: { msg: Message; isLast: boolean; canEdit: boolean }) {
  return msg.role === "user" ? <UserMessage msg={msg} canEdit={canEdit} /> : <AssistantMessage msg={msg} isLast={isLast} />;
}

export const MessageItem = memo(MessageItemImpl);
