import { create } from "zustand";
import { ApiError, analyzeGithub, analyzeZip, fetchConfig, resolveRepo, streamChat, type ChatRequest } from "./lib/api";
import { buildIndex, retrieve, type RepoIndex } from "./lib/retrieval";
import { db, loadPrefs, savePrefs } from "./lib/storage";
import { splitFollowups, uid } from "./lib/text";
import type { ChatMeta, Message, Mode, RepoData, RepoEntry, ServerConfig } from "./lib/types";

export type PanelTab = "overview" | "files" | "symbols";

export interface Analyzing {
  stage: "resolve" | "download" | "index";
  source: string;
  startedAt: number;
  error?: string;
}

export interface Toast {
  id: string;
  text: string;
  tone: "info" | "error";
}

interface State {
  booted: boolean;
  config: ServerConfig | null;
  theme: "dark" | "light";
  mode: Mode;
  panelOpen: boolean;
  panelTab: PanelTab;
  sidebarOpen: boolean; // mobile drawer
  view: "home" | "chat";

  repos: RepoEntry[];
  chats: ChatMeta[];
  activeRepoId: string | null;
  activeChatId: string | null;
  repoData: RepoData | null;
  index: RepoIndex | null;
  loadingRepo: boolean;
  messagesByChat: Record<string, Message[]>;
  streaming: Record<string, boolean>;

  analyzing: Analyzing | null;
  openFile: { path: string; start?: number; end?: number } | null;
  draft: string;
  attachments: string[];
  toasts: Toast[];
  composerFocusTick: number;

  init(): Promise<void>;
  setTheme(t: "dark" | "light"): void;
  setMode(m: Mode): void;
  togglePanel(open?: boolean): void;
  setPanelTab(t: PanelTab): void;
  setSidebarOpen(o: boolean): void;
  goHome(): void;

  addRepoFromUrl(url: string): Promise<void>;
  addRepoFromZip(file: File): Promise<void>;
  cancelAnalysis(): void;
  selectRepo(id: string, chatId?: string | null): Promise<void>;
  removeRepo(id: string): Promise<void>;
  refreshRepo(): Promise<void>;

  newChat(): void;
  selectChat(id: string): Promise<void>;
  deleteChat(id: string): Promise<void>;
  send(text: string): void;
  stop(chatId?: string): void;
  regenerate(assistantId: string): void;
  editAndResend(userId: string, text: string): void;
  exportChat(): void;

  openFileAt(path: string, start?: number, end?: number): void;
  closeFile(): void;
  setDraft(s: string): void;
  addAttachment(p: string): void;
  removeAttachment(p: string): void;
  focusComposer(): void;
  toast(text: string, tone?: Toast["tone"]): void;
  dismissToast(id: string): void;
}

const CONTEXT_BUDGET: Record<Mode, number> = { fast: 100_000, deep: 330_000 };
const controllers = new Map<string, AbortController>();
let analysisController: AbortController | null = null;

const repoIdFor = (source: string, fullName: string, sha: string) =>
  source === "github" ? `gh:${fullName.toLowerCase()}` : `zip:${sha.slice(0, 16)}`;

function titleFrom(text: string) {
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= 56) return t;
  const cut = t.slice(0, 56);
  return cut.slice(0, cut.lastIndexOf(" ") > 30 ? cut.lastIndexOf(" ") : 56) + "…";
}

function entryFrom(data: RepoData, description = ""): RepoEntry {
  const r = data.repo;
  return {
    id: repoIdFor(r.source, r.full_name, r.sha),
    source: r.source,
    fullName: r.full_name,
    owner: r.owner,
    name: r.repo,
    ref: r.ref,
    sha: r.sha,
    htmlUrl: r.html_url,
    description: description || r.description || "",
    analyzedAt: Date.now(),
    files: data.analysis.stats.files,
    lines: data.analysis.stats.lines,
    topLanguage: data.analysis.languages[0]?.name ?? null,
  };
}

function repoMapForPrompt(data: RepoData, entry?: RepoEntry) {
  const desc = entry?.description || data.repo.description;
  if (!desc) return data.map;
  const [first, ...rest] = data.map.split("\n");
  return [first, `Description: ${desc}`, ...rest].join("\n");
}

export const useStore = create<State>((set, get) => {
  // ── streaming: buffer deltas and flush once per frame ─────────────────────
  const buffers = new Map<string, string>(); // msgId -> pending text
  let flushScheduled = false;
  const flush = () => {
    flushScheduled = false;
    if (!buffers.size) return;
    const pending = new Map(buffers);
    buffers.clear();
    set((s) => {
      const next = { ...s.messagesByChat };
      for (const [chatId, msgs] of Object.entries(next)) {
        if (!msgs.some((m) => pending.has(m.id))) continue;
        next[chatId] = msgs.map((m) =>
          pending.has(m.id) ? { ...m, content: m.content + pending.get(m.id)!, status: "streaming" } : m,
        );
      }
      return { messagesByChat: next };
    });
  };
  const scheduleFlush = () => {
    if (flushScheduled) return;
    flushScheduled = true;
    requestAnimationFrame(flush);
  };

  const patchMessage = (chatId: string, msgId: string, patch: Partial<Message>) =>
    set((s) => ({
      messagesByChat: {
        ...s.messagesByChat,
        [chatId]: (s.messagesByChat[chatId] ?? []).map((m) => (m.id === msgId ? { ...m, ...patch } : m)),
      },
    }));

  const persistChat = async (chatId: string) => {
    const msgs = get().messagesByChat[chatId];
    if (!msgs) return;
    const clean = msgs.map((m) =>
      m.status === "thinking" || m.status === "streaming" ? { ...m, status: "stopped" as const } : m,
    );
    await db.saveMessages(chatId, clean);
    const chats = get().chats.map((c) => (c.id === chatId ? { ...c, updatedAt: Date.now() } : c));
    set({ chats });
    await db.saveChats(chats);
  };

  const savePrefsNow = () => {
    const s = get();
    savePrefs({ theme: s.theme, mode: s.mode, panelOpen: s.panelOpen, activeRepoId: s.activeRepoId, activeChatId: s.activeChatId });
  };

  const applyTheme = (t: "dark" | "light") => {
    document.documentElement.dataset.theme = t;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", t === "dark" ? "#101115" : "#ffffff");
  };

  /** Ask (or re-ask) a question at position `at` in a chat, replacing anything after it. */
  const ask = (chatId: string, at: number, text: string, attachments: string[]) => {
    const s = get();
    const { index, repoData } = s;
    if (!index || !repoData || s.streaming[chatId]) return;
    const prior = (s.messagesByChat[chatId] ?? []).slice(0, at);
    const prevUser = [...prior].reverse().find((m) => m.role === "user")?.content ?? null;
    const mode = s.mode;
    const r = retrieve(index, text, prevUser, attachments, CONTEXT_BUDGET[mode]);
    const userMsg: Message = { id: uid(), role: "user", content: text, createdAt: Date.now(), ...(attachments.length ? { attachments } : {}) };
    const asst: Message = {
      id: uid(),
      role: "assistant",
      content: "",
      createdAt: Date.now(),
      status: "thinking",
      mode,
      modelLabel: s.config?.modes[mode]?.label,
      excerpts: r.context.map(({ path, start, end }) => ({ path, start, end })),
    };
    set((st) => ({ messagesByChat: { ...st.messagesByChat, [chatId]: [...prior, userMsg, asst] } }));

    const entry = s.repos.find((x) => x.id === s.activeRepoId);
    const history = [...prior, userMsg]
      .filter((m) => m.content.trim() && !(m.role === "assistant" && m.status === "error" && !m.content))
      .slice(-40)
      .map((m) => ({ role: m.role, content: m.role === "assistant" ? splitFollowups(m.content).body : m.content }));
    const body: ChatRequest = {
      mode,
      repo: { key: `${repoData.repo.full_name}@${repoData.repo.sha}`, map: repoMapForPrompt(repoData, entry) },
      messages: history,
      context: r.context.map(({ path, start, text, pinned }) => ({ path, start, text, ...(pinned ? { pinned } : {}) })),
    };
    void run(chatId, asst.id, body);
  };

  const run = async (chatId: string, msgId: string, body: ChatRequest) => {
    const ctrl = new AbortController();
    controllers.set(chatId, ctrl);
    set((s) => ({ streaming: { ...s.streaming, [chatId]: true } }));
    const t0 = performance.now();
    await streamChat(
      body,
      {
        onMeta: (m) => patchMessage(chatId, msgId, { modelLabel: m.label }),
        onDelta: (t) => {
          buffers.set(msgId, (buffers.get(msgId) ?? "") + t);
          scheduleFlush();
        },
        onDone: (d) => {
          flush();
          patchMessage(chatId, msgId, { status: "done", finish: d.finish_reason, elapsedMs: d.elapsed_ms ?? Math.round(performance.now() - t0) });
        },
        onError: (e: ApiError) => {
          flush();
          patchMessage(chatId, msgId, { status: "error", error: { code: e.code, message: e.message } });
        },
      },
      ctrl.signal,
    );
    flush();
    controllers.delete(chatId);
    set((s) => {
      const { [chatId]: _, ...rest } = s.streaming;
      return { streaming: rest };
    });
    await persistChat(chatId);
  };

  const activate = async (data: RepoData, entry: RepoEntry, chatId: string | null) => {
    set({ loadingRepo: true });
    await new Promise((r) => setTimeout(r, 0));
    const index = buildIndex(data);
    set({
      repoData: data,
      index,
      activeRepoId: entry.id,
      activeChatId: chatId,
      loadingRepo: false,
      view: "chat",
      openFile: null,
      attachments: [],
    });
    if (chatId && !get().messagesByChat[chatId]) {
      const msgs = await db.messages(chatId);
      set((s) => ({ messagesByChat: { ...s.messagesByChat, [chatId]: msgs } }));
    }
    savePrefsNow();
  };

  const finishAnalysis = async (data: RepoData, description = "") => {
    set({ analyzing: { ...get().analyzing!, stage: "index" } });
    await new Promise((r) => setTimeout(r, 30));
    const entry = entryFrom(data, description);
    data.repo.description = entry.description;
    const repos = [entry, ...get().repos.filter((r) => r.id !== entry.id)];
    set({ repos });
    await Promise.all([db.saveRepos(repos), db.saveRepoData(entry.id, data)]);
    await activate(data, entry, null);
    set({ analyzing: null });
    get().focusComposer();
  };

  return {
    booted: false,
    config: null,
    theme: "dark",
    mode: "fast",
    panelOpen: true,
    panelTab: "overview",
    sidebarOpen: false,
    view: "home",
    repos: [],
    chats: [],
    activeRepoId: null,
    activeChatId: null,
    repoData: null,
    index: null,
    loadingRepo: false,
    messagesByChat: {},
    streaming: {},
    analyzing: null,
    openFile: null,
    draft: "",
    attachments: [],
    toasts: [],
    composerFocusTick: 0,

    async init() {
      const prefs = loadPrefs();
      const wide = window.matchMedia("(min-width: 1180px)").matches;
      set({ theme: prefs.theme, mode: prefs.mode, panelOpen: prefs.panelOpen && wide });
      applyTheme(prefs.theme);
      fetchConfig().then((config) => set({ config })).catch(() => {});
      const [repos, chats] = await Promise.all([db.repos(), db.chats()]);
      set({ repos, chats });
      if (prefs.activeRepoId && repos.some((r) => r.id === prefs.activeRepoId)) {
        const chatOk = prefs.activeChatId && chats.some((c) => c.id === prefs.activeChatId && c.repoId === prefs.activeRepoId);
        await get().selectRepo(prefs.activeRepoId, chatOk ? prefs.activeChatId : null);
      }
      set({ booted: true });
    },

    setTheme(theme) {
      set({ theme });
      applyTheme(theme);
      savePrefsNow();
    },
    setMode(mode) {
      set({ mode });
      savePrefsNow();
    },
    togglePanel(open) {
      set((s) => ({ panelOpen: open ?? !s.panelOpen }));
      savePrefsNow();
    },
    setPanelTab(panelTab) {
      set({ panelTab, panelOpen: true, openFile: null });
    },
    setSidebarOpen(sidebarOpen) {
      set({ sidebarOpen });
    },
    goHome() {
      set({ view: "home", sidebarOpen: false, analyzing: get().analyzing?.error ? null : get().analyzing });
    },

    async addRepoFromUrl(url) {
      analysisController?.abort();
      const ctrl = (analysisController = new AbortController());
      set({ analyzing: { stage: "resolve", source: url.trim(), startedAt: Date.now() }, view: "home" });
      try {
        const meta = await resolveRepo(url, ctrl.signal);
        const id = repoIdFor("github", meta.full_name, meta.sha);
        const existing = get().repos.find((r) => r.id === id);
        if (existing && existing.sha === meta.sha) {
          const data = await db.repoData(id);
          if (data && data.version === "2") {
            set({ analyzing: null });
            await activate(data, existing, null);
            get().toast(`${meta.full_name} is already up to date.`);
            return;
          }
        }
        set({ analyzing: { ...get().analyzing!, stage: "download" } });
        const data = await analyzeGithub(meta, ctrl.signal);
        if (ctrl.signal.aborted) return;
        data.repo.ref = meta.ref;
        await finishAnalysis(data, meta.description);
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        set({ analyzing: { ...get().analyzing!, error: (e as Error).message } });
      }
    },

    async addRepoFromZip(file) {
      analysisController?.abort();
      const ctrl = (analysisController = new AbortController());
      const max = (get().config?.max_upload_mb ?? 4.3) * 1024 * 1024;
      set({ analyzing: { stage: "download", source: file.name, startedAt: Date.now() }, view: "home" });
      if (file.size > max) {
        set({ analyzing: { ...get().analyzing!, error: `That ZIP is ${(file.size / 1048576).toFixed(1)} MB. The limit is ${(max / 1048576).toFixed(1)} MB, so push the project to GitHub and paste the link instead.` } });
        return;
      }
      try {
        const data = await analyzeZip(file, ctrl.signal);
        if (ctrl.signal.aborted) return;
        await finishAnalysis(data);
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        set({ analyzing: { ...get().analyzing!, error: (e as Error).message } });
      }
    },

    cancelAnalysis() {
      analysisController?.abort();
      set({ analyzing: null });
    },

    async selectRepo(id, chatId = null) {
      const entry = get().repos.find((r) => r.id === id);
      if (!entry) return;
      if (get().activeRepoId === id && get().repoData) {
        set({ activeChatId: chatId, view: "chat", sidebarOpen: false, openFile: null });
        if (chatId) await get().selectChat(chatId);
        savePrefsNow();
        return;
      }
      set({ loadingRepo: true, sidebarOpen: false, activeRepoId: id, view: "chat" });
      const data = await db.repoData(id);
      if (!data || data.version !== "2") {
        set({ loadingRepo: false });
        if (entry.source === "github" && entry.htmlUrl) {
          await get().addRepoFromUrl(entry.htmlUrl + (entry.ref ? `/tree/${entry.ref}` : ""));
        } else {
          get().toast("This upload is no longer stored in your browser. Upload the ZIP again.", "error");
          set({ view: "home" });
        }
        return;
      }
      await activate(data, entry, chatId);
    },

    async removeRepo(id) {
      const chats = get().chats.filter((c) => c.repoId === id);
      for (const c of chats) {
        get().stop(c.id);
        await db.deleteMessages(c.id);
      }
      const repos = get().repos.filter((r) => r.id !== id);
      const remaining = get().chats.filter((c) => c.repoId !== id);
      set({ repos, chats: remaining });
      await Promise.all([db.saveRepos(repos), db.saveChats(remaining), db.deleteRepoData(id)]);
      if (get().activeRepoId === id) {
        set({ activeRepoId: null, activeChatId: null, repoData: null, index: null, view: "home" });
        savePrefsNow();
      }
    },

    async refreshRepo() {
      const entry = get().repos.find((r) => r.id === get().activeRepoId);
      if (!entry || entry.source !== "github" || !entry.htmlUrl) return;
      await get().addRepoFromUrl(entry.htmlUrl + (entry.ref ? `/tree/${entry.ref}` : ""));
    },

    newChat() {
      set({ activeChatId: null, view: get().repoData ? "chat" : "home", sidebarOpen: false, draft: "", attachments: [] });
      savePrefsNow();
      get().focusComposer();
    },

    async selectChat(id) {
      const chat = get().chats.find((c) => c.id === id);
      if (!chat) return;
      if (chat.repoId !== get().activeRepoId || !get().repoData) {
        await get().selectRepo(chat.repoId, id);
        return;
      }
      if (!get().messagesByChat[id]) {
        const msgs = await db.messages(id);
        set((s) => ({ messagesByChat: { ...s.messagesByChat, [id]: msgs } }));
      }
      set({ activeChatId: id, view: "chat", sidebarOpen: false });
      savePrefsNow();
    },

    async deleteChat(id) {
      get().stop(id);
      const chats = get().chats.filter((c) => c.id !== id);
      set((s) => {
        const { [id]: _, ...rest } = s.messagesByChat;
        return { chats, messagesByChat: rest, activeChatId: s.activeChatId === id ? null : s.activeChatId };
      });
      await Promise.all([db.saveChats(chats), db.deleteMessages(id)]);
      savePrefsNow();
    },

    send(text) {
      const s = get();
      const q = text.trim();
      if (!q || !s.repoData || !s.activeRepoId) return;
      let chatId = s.activeChatId;
      if (!chatId) {
        chatId = uid();
        const meta: ChatMeta = { id: chatId, repoId: s.activeRepoId, title: titleFrom(q), createdAt: Date.now(), updatedAt: Date.now() };
        const chats = [meta, ...s.chats];
        set((st) => ({ chats, activeChatId: chatId, messagesByChat: { ...st.messagesByChat, [chatId!]: [] } }));
        void db.saveChats(chats);
        savePrefsNow();
      }
      if (get().streaming[chatId]) return;
      const attachments = s.attachments;
      set({ draft: "", attachments: [] });
      ask(chatId, (get().messagesByChat[chatId] ?? []).length, q, attachments);
    },

    stop(chatId) {
      const id = chatId ?? get().activeChatId;
      if (!id) return;
      const ctrl = controllers.get(id);
      if (!ctrl) return;
      ctrl.abort();
      flush();
      set((s) => ({
        messagesByChat: {
          ...s.messagesByChat,
          [id]: (s.messagesByChat[id] ?? []).map((m) =>
            m.status === "thinking" || m.status === "streaming" ? { ...m, status: "stopped" } : m,
          ),
        },
      }));
    },

    regenerate(assistantId) {
      const chatId = get().activeChatId;
      if (!chatId) return;
      const msgs = get().messagesByChat[chatId] ?? [];
      const i = msgs.findIndex((m) => m.id === assistantId);
      const userIdx = i - 1;
      if (userIdx < 0 || msgs[userIdx].role !== "user") return;
      ask(chatId, userIdx, msgs[userIdx].content, msgs[userIdx].attachments ?? []);
    },

    editAndResend(userId, text) {
      const chatId = get().activeChatId;
      if (!chatId || !text.trim()) return;
      const msgs = get().messagesByChat[chatId] ?? [];
      const i = msgs.findIndex((m) => m.id === userId);
      if (i < 0) return;
      ask(chatId, i, text.trim(), msgs[i].attachments ?? []);
    },

    exportChat() {
      const s = get();
      const chat = s.chats.find((c) => c.id === s.activeChatId);
      const msgs = chat ? s.messagesByChat[chat.id] ?? [] : [];
      if (!chat || !msgs.length || !s.repoData) return;
      const r = s.repoData.repo;
      const lines = [
        `# ${chat.title}`,
        "",
        `Repository: ${r.full_name}${r.sha ? ` @ ${r.sha.slice(0, 7)}` : ""}  `,
        `Exported from CodeRepoReader on ${new Date().toLocaleString()}`,
        "",
      ];
      for (const m of msgs) {
        if (m.role === "user") lines.push(`## You`, "", m.content, "");
        else lines.push(`## CodeRepoReader${m.modelLabel ? ` (${m.modelLabel})` : ""}`, "", splitFollowups(m.content).body || "_No answer._", "");
      }
      const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${chat.title.replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-").slice(0, 60) || "chat"}.md`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      get().toast("Chat exported as Markdown.");
    },

    openFileAt(path, start, end) {
      if (!get().index?.files.has(path)) {
        get().toast(`${path} isn't in the analyzed files.`, "error");
        return;
      }
      set({ openFile: { path, start, end }, panelOpen: true, panelTab: "files" });
    },
    closeFile() {
      set({ openFile: null });
    },
    setDraft(draft) {
      set({ draft });
    },
    addAttachment(p) {
      if (!get().attachments.includes(p)) set({ attachments: [...get().attachments, p].slice(0, 8) });
    },
    removeAttachment(p) {
      set({ attachments: get().attachments.filter((x) => x !== p) });
    },
    focusComposer() {
      set((s) => ({ composerFocusTick: s.composerFocusTick + 1 }));
    },
    toast(text, tone = "info") {
      const id = uid();
      set((s) => ({ toasts: [...s.toasts.slice(-2), { id, text, tone }] }));
      setTimeout(() => get().dismissToast(id), tone === "error" ? 6000 : 3200);
    },
    dismissToast(id) {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    },
  };
});
