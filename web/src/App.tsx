import { useEffect } from "react";
import { CircleAlert, Info, LoaderCircle, X } from "lucide-react";
import { useStore } from "./store";
import { ChatView } from "./components/ChatView";
import { Home } from "./components/Home";
import { RepoPanel } from "./components/RepoPanel";
import { Sidebar } from "./components/Sidebar";

function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const dismiss = useStore((s) => s.dismissToast);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast${t.tone === "error" ? " toast--error" : ""}`}>
          {t.tone === "error" ? <CircleAlert size={16} aria-hidden="true" /> : <Info size={16} aria-hidden="true" />}
          <span style={{ flex: 1 }}>{t.text}</span>
          <button type="button" className="icon-btn icon-btn--sm" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function App() {
  const booted = useStore((s) => s.booted);
  const view = useStore((s) => s.view);
  const hasRepo = useStore((s) => !!s.repoData || s.loadingRepo);
  const panelOpen = useStore((s) => s.panelOpen);
  const sidebarOpen = useStore((s) => s.sidebarOpen);
  const repoData = useStore((s) => s.repoData);

  useEffect(() => {
    void useStore.getState().init();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      const s = useStore.getState();
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        s.setSidebarOpen(true);
        requestAnimationFrame(() => document.getElementById("chat-search")?.focus());
      } else if (mod && e.shiftKey && e.key.toLowerCase() === "o") {
        e.preventDefault();
        if (s.activeRepoId) s.newChat();
        else s.goHome();
      } else if (e.key === "Escape" && s.sidebarOpen) {
        s.setSidebarOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const name = repoData?.repo.full_name;
    document.title = view === "chat" && name ? `${name} · CodeRepoReader` : "CodeRepoReader — chat with any codebase";
  }, [view, repoData]);

  if (!booted) {
    return (
      <div className="boot" role="status">
        <LoaderCircle size={20} className="spin" aria-label="Loading" />
      </div>
    );
  }

  const showPanel = view === "chat" && !!repoData && panelOpen;
  return (
    <div className="app">
      <Sidebar />
      <main className="main">{view === "chat" && hasRepo ? <ChatView /> : <Home />}</main>
      {showPanel && <RepoPanel />}
      <div
        className={`scrim${sidebarOpen || showPanel ? " is-on" : ""}`}
        onClick={() => {
          const s = useStore.getState();
          s.setSidebarOpen(false);
          if (window.matchMedia("(max-width: 1179px)").matches) s.togglePanel(false);
        }}
        aria-hidden="true"
      />
      <Toasts />
    </div>
  );
}
