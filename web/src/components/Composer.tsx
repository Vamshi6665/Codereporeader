import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, AtSign, Brain, FileCode2, Square, X, Zap } from "lucide-react";
import { useStore } from "../store";

function fuzzyScore(path: string, q: string): number {
  if (!q) return 1;
  const p = path.toLowerCase();
  const base = p.split("/").pop()!;
  if (base.startsWith(q)) return 100 - base.length / 100;
  if (base.includes(q)) return 80 - base.length / 100;
  if (p.includes(q)) return 60 - p.length / 1000;
  let i = 0;
  for (const ch of p) if (ch === q[i]) i++;
  return i === q.length ? 20 - p.length / 1000 : 0;
}

export function Composer() {
  const draft = useStore((s) => s.draft);
  const setDraft = useStore((s) => s.setDraft);
  const attachments = useStore((s) => s.attachments);
  const addAttachment = useStore((s) => s.addAttachment);
  const removeAttachment = useStore((s) => s.removeAttachment);
  const mode = useStore((s) => s.mode);
  const setMode = useStore((s) => s.setMode);
  const send = useStore((s) => s.send);
  const stop = useStore((s) => s.stop);
  const busy = useStore((s) => (s.activeChatId ? !!s.streaming[s.activeChatId] : false));
  const index = useStore((s) => s.index);
  const repo = useStore((s) => s.repoData?.repo);
  const config = useStore((s) => s.config);
  const focusTick = useStore((s) => s.composerFocusTick);
  const ta = useRef<HTMLTextAreaElement>(null);
  const [mention, setMention] = useState<{ query: string; start: number } | null>(null);
  const [active, setActive] = useState(0);

  useLayoutEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, window.innerHeight * 0.38) + "px";
  }, [draft]);

  useEffect(() => {
    if (focusTick) ta.current?.focus();
  }, [focusTick]);

  const matches = useMemo(() => {
    if (!mention || !index) return [];
    const q = mention.query.toLowerCase();
    return [...index.files.keys()]
      .map((p) => ({ p, s: fuzzyScore(p, q) }))
      .filter((x) => x.s > 0 && !attachments.includes(x.p))
      .sort((a, b) => b.s - a.s || a.p.localeCompare(b.p))
      .slice(0, 8)
      .map((x) => x.p);
  }, [mention, index, attachments]);

  useEffect(() => setActive(0), [mention?.query]);

  const detectMention = (value: string, caret: number) => {
    const before = value.slice(0, caret);
    const m = /(^|\s)@([\w./-]*)$/.exec(before);
    setMention(m ? { query: m[2], start: caret - m[2].length - 1 } : null);
  };

  const pick = (path: string) => {
    if (!mention) return;
    const el = ta.current!;
    const caret = el.selectionStart;
    const next = draft.slice(0, mention.start) + draft.slice(caret);
    setDraft(next);
    addAttachment(path);
    setMention(null);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(mention.start, mention.start);
    });
  };

  const submit = () => {
    if (busy || !draft.trim()) return;
    setMention(null);
    send(draft);
  };

  const openPicker = () => {
    const el = ta.current!;
    const caret = el.selectionStart ?? draft.length;
    const needsSpace = caret > 0 && !/\s/.test(draft[caret - 1]);
    const next = draft.slice(0, caret) + (needsSpace ? " @" : "@") + draft.slice(caret);
    setDraft(next);
    const pos = caret + (needsSpace ? 2 : 1);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(pos, pos);
      detectMention(next, pos);
    });
  };

  const labels = config?.modes;
  return (
    <div className="composer-wrap">
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {mention && (
          <div className="mention" role="listbox" aria-label="Attach a file">
            <div className="mention__head">{mention.query ? `Files matching “${mention.query}”` : "Attach a file to this question"}</div>
            {matches.length ? (
              matches.map((p, i) => (
                <button
                  key={p}
                  type="button"
                  role="option"
                  aria-selected={i === active}
                  className={`mention__item${i === active ? " is-active" : ""}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(p)}
                >
                  <FileCode2 size={14} aria-hidden="true" />
                  <span className="mention__path">{p}</span>
                </button>
              ))
            ) : (
              <div className="mention__empty">No files match. Keep typing or press Esc.</div>
            )}
          </div>
        )}
        {!!attachments.length && (
          <div className="composer__files">
            {attachments.map((p) => (
              <span key={p} className="chip chip--x">
                <FileCode2 size={13} aria-hidden="true" />
                <span className="chip__path">{p}</span>
                <button type="button" className="icon-btn" aria-label={`Remove ${p}`} onClick={() => removeAttachment(p)}>
                  <X size={13} />
                </button>
              </span>
            ))}
          </div>
        )}
        <label htmlFor="composer" className="sr-only">
          Ask about this repository
        </label>
        <textarea
          id="composer"
          ref={ta}
          rows={1}
          value={draft}
          placeholder={repo ? `Ask about ${repo.repo}. Type @ to attach a file.` : "Ask about this repository"}
          onChange={(e) => {
            setDraft(e.target.value);
            detectMention(e.target.value, e.target.selectionStart);
          }}
          onClick={(e) => detectMention(draft, (e.target as HTMLTextAreaElement).selectionStart)}
          onKeyDown={(e) => {
            if (mention && matches.length) {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((a) => (a + 1) % matches.length);
                return;
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((a) => (a - 1 + matches.length) % matches.length);
                return;
              }
              if (e.key === "Enter" || e.key === "Tab") {
                e.preventDefault();
                pick(matches[active]);
                return;
              }
            }
            if (e.key === "Escape") {
              if (mention) setMention(null);
              else if (busy) stop();
              return;
            }
            if (e.key === "Backspace" && !draft && attachments.length) {
              removeAttachment(attachments[attachments.length - 1]);
              return;
            }
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          onBlur={() => setTimeout(() => setMention(null), 120)}
        />
        <div className="composer__row">
          <button type="button" className="btn" onClick={openPicker} title="Attach a file so the answer reads it in full">
            <AtSign size={14} aria-hidden="true" />
            <span>Add file</span>
          </button>
          <div className="seg" role="group" aria-label="Answer mode">
            <button type="button" aria-pressed={mode === "fast"} onClick={() => setMode("fast")} title={`Fast: quick answers${labels ? ` with ${labels.fast.label}` : ""}`}>
              <Zap size={12} aria-hidden="true" />
              Fast
            </button>
            <button type="button" aria-pressed={mode === "deep"} onClick={() => setMode("deep")} title={`Deep: reads more code and reasons longer${labels ? ` with ${labels.deep.label}` : ""}`}>
              <Brain size={12} aria-hidden="true" />
              Deep
            </button>
          </div>
          {busy ? (
            <button type="button" className="send send--stop" aria-label="Stop generating" title="Stop (Esc)" onClick={() => stop()}>
              <Square size={13} fill="currentColor" />
            </button>
          ) : (
            <button type="submit" className="send" aria-label="Send message" title="Send (Enter)" disabled={!draft.trim()}>
              <ArrowUp size={17} strokeWidth={2.2} />
            </button>
          )}
        </div>
      </form>
      <p className="composer__hint">
        {repo?.source === "github" ? (
          <>
            Answers cite {repo.ref ? `${repo.ref} at ` : ""}
            <span className="mono">{repo.sha.slice(0, 7)}</span>. Check important details in the code.
          </>
        ) : (
          "Answers cite the uploaded files. Check important details in the code."
        )}
      </p>
    </div>
  );
}
