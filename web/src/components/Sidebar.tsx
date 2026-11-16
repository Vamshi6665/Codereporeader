import { useEffect, useMemo, useRef, useState } from "react";
import { BookMarked, FileArchive, Keyboard, MessageSquare, Moon, Plus, Search, Sun, Trash2, X } from "lucide-react";
import { useStore } from "../store";
import { timeGroup } from "../lib/text";
import type { ChatMeta } from "../lib/types";
import { Logo } from "./Logo";

export const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const MOD = isMac ? "⌘" : "Ctrl";

function HelpDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="dialog" onClose={onClose} onClick={(e) => e.target === ref.current && onClose()} aria-labelledby="help-title">
      <div className="dialog__head">
        <h2 id="help-title">Shortcuts and privacy</h2>
        <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <div className="dialog__body">
        <table className="keys">
          <tbody>
            <tr>
              <td>Send</td>
              <td><kbd>Enter</kbd></td>
            </tr>
            <tr>
              <td>New line</td>
              <td><kbd>Shift</kbd><kbd>Enter</kbd></td>
            </tr>
            <tr>
              <td>Attach a file</td>
              <td><kbd>@</kbd></td>
            </tr>
            <tr>
              <td>Stop generating</td>
              <td><kbd>Esc</kbd></td>
            </tr>
            <tr>
              <td>New chat</td>
              <td><kbd>{MOD}</kbd><kbd>Shift</kbd><kbd>O</kbd></td>
            </tr>
            <tr>
              <td>Search chats</td>
              <td><kbd>{MOD}</kbd><kbd>K</kbd></td>
            </tr>
          </tbody>
        </table>
        <p>
          <b>Fast</b> answers quickly and reads the most relevant code. <b>Deep</b> reads about three times more code and reasons longer, for reviews and
          architecture questions.
        </p>
        <p style={{ marginBottom: 0 }}>
          Chats and analyzed repositories are saved only in this browser. The server keeps nothing. Each question, with the code excerpts it needs, is
          sent to OpenAI to write the answer.
        </p>
      </div>
    </dialog>
  );
}

function ChatRow({ c, active, live, sub }: { c: ChatMeta; active: boolean; live: boolean; sub?: string }) {
  const selectChat = useStore((s) => s.selectChat);
  const deleteChat = useStore((s) => s.deleteChat);
  return (
    <li className={`nav-item${active ? " is-active" : ""}`}>
      <button type="button" className="nav-item__btn" onClick={() => selectChat(c.id)} aria-current={active ? "page" : undefined}>
        {live ? <span className="nav-item__live" aria-label="Answering" /> : <MessageSquare size={15} aria-hidden="true" />}
        <span className="nav-item__text">
          <span className="nav-item__title">{c.title}</span>
          {sub && <span className="nav-item__sub">{sub}</span>}
        </span>
      </button>
      <button
        type="button"
        className="icon-btn icon-btn--sm nav-item__action"
        aria-label={`Delete chat “${c.title}”`}
        title="Delete chat"
        onClick={() => {
          if (confirm(`Delete “${c.title}”? This can't be undone.`)) void deleteChat(c.id);
        }}
      >
        <Trash2 size={14} />
      </button>
    </li>
  );
}

export function Sidebar() {
  const repos = useStore((s) => s.repos);
  const chats = useStore((s) => s.chats);
  const activeRepoId = useStore((s) => s.activeRepoId);
  const activeChatId = useStore((s) => s.activeChatId);
  const streaming = useStore((s) => s.streaming);
  const view = useStore((s) => s.view);
  const theme = useStore((s) => s.theme);
  const open = useStore((s) => s.sidebarOpen);
  const setTheme = useStore((s) => s.setTheme);
  const newChat = useStore((s) => s.newChat);
  const goHome = useStore((s) => s.goHome);
  const selectRepo = useStore((s) => s.selectRepo);
  const removeRepo = useStore((s) => s.removeRepo);
  const [q, setQ] = useState("");
  const [help, setHelp] = useState(false);

  const repoChats = useMemo(
    () => chats.filter((c) => c.repoId === activeRepoId).sort((a, b) => b.updatedAt - a.updatedAt),
    [chats, activeRepoId],
  );
  const groups = useMemo(() => {
    const g = new Map<string, ChatMeta[]>();
    for (const c of repoChats) {
      const k = timeGroup(c.updatedAt);
      g.set(k, [...(g.get(k) ?? []), c]);
    }
    return [...g.entries()];
  }, [repoChats]);
  const results = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return null;
    return chats.filter((c) => c.title.toLowerCase().includes(s)).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 40);
  }, [q, chats]);
  const repoName = (id: string) => repos.find((r) => r.id === id)?.fullName ?? "";

  return (
    <>
      <nav className={`sidebar${open ? " is-open" : ""}`} aria-label="Repositories and chats">
        <div className="sidebar__top">
          <button type="button" className="brand" onClick={goHome} title="Add a repository">
            <Logo />
            <span>CodeRepoReader</span>
          </button>
          <button type="button" className="btn new-chat" onClick={() => (activeRepoId ? newChat() : goHome())}>
            <Plus size={16} aria-hidden="true" />
            New chat
            <kbd aria-hidden="true">{MOD}⇧O</kbd>
          </button>
          <div className="search">
            <Search size={15} aria-hidden="true" />
            <label htmlFor="chat-search" className="sr-only">
              Search chats
            </label>
            <input id="chat-search" type="search" placeholder="Search chats" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setQ("")} />
            <kbd aria-hidden="true">{MOD} K</kbd>
          </div>
        </div>

        <div className="sidebar__scroll">
          {results ? (
            <div>
              <div className="nav-group__head">
                <span>
                  {results.length} result{results.length === 1 ? "" : "s"}
                </span>
              </div>
              <ul className="nav-list">
                {results.map((c) => (
                  <ChatRow key={c.id} c={c} active={c.id === activeChatId} live={!!streaming[c.id]} sub={repoName(c.repoId)} />
                ))}
                {!results.length && <li className="nav-empty">No chats match “{q}”.</li>}
              </ul>
            </div>
          ) : (
            <>
              <div>
                <div className="nav-group__head">
                  <span>Repositories</span>
                  <button type="button" className="icon-btn" aria-label="Add a repository" title="Add a repository" onClick={goHome}>
                    <Plus size={14} />
                  </button>
                </div>
                <ul className="nav-list">
                  {repos.map((r) => {
                    const active = r.id === activeRepoId && view === "chat";
                    return (
                      <li key={r.id} className={`nav-item${active ? " is-active" : ""}`}>
                        <button type="button" className="nav-item__btn" onClick={() => selectRepo(r.id)} aria-current={active ? "page" : undefined}>
                          {r.source === "upload" ? <FileArchive size={15} aria-hidden="true" /> : <BookMarked size={15} aria-hidden="true" />}
                          <span className="nav-item__text">
                            <span className="nav-item__title">{r.name}</span>
                            <span className="nav-item__sub">{r.source === "upload" ? "Uploaded ZIP" : `${r.owner}${r.ref ? ` on ${r.ref}` : ""}`}</span>
                          </span>
                        </button>
                        <button
                          type="button"
                          className="icon-btn icon-btn--sm nav-item__action"
                          aria-label={`Remove ${r.fullName}`}
                          title="Remove repository and its chats"
                          onClick={() => {
                            if (confirm(`Remove ${r.fullName} and all of its chats from this browser?`)) void removeRepo(r.id);
                          }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </li>
                    );
                  })}
                  {!repos.length && <li className="nav-empty">Analyze a repository to start.</li>}
                </ul>
              </div>

              {activeRepoId && (
                <div>
                  <div className="nav-group__head">
                    <span>Chats in this repository</span>
                  </div>
                  {groups.map(([label, list]) => (
                    <div key={label}>
                      <div className="nav-sub">{label}</div>
                      <ul className="nav-list">
                        {list.map((c) => (
                          <ChatRow key={c.id} c={c} active={c.id === activeChatId && view === "chat"} live={!!streaming[c.id]} />
                        ))}
                      </ul>
                    </div>
                  ))}
                  {!repoChats.length && <div className="nav-empty">No chats yet. Ask your first question.</div>}
                </div>
              )}
            </>
          )}
        </div>

        <div className="sidebar__foot">
          <button type="button" className="btn btn--ghost" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? <Sun size={15} aria-hidden="true" /> : <Moon size={15} aria-hidden="true" />}
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </button>
          <button type="button" className="icon-btn" aria-label="Shortcuts and privacy" title="Shortcuts and privacy" onClick={() => setHelp(true)}>
            <Keyboard size={16} />
          </button>
        </div>
      </nav>
      <HelpDialog open={help} onClose={() => setHelp(false)} />
    </>
  );
}
