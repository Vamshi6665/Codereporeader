/** Persistence in the browser (IndexedDB via idb-keyval). Nothing is stored on the server. */
import { createStore, del, get, set } from "idb-keyval";
import type { ChatMeta, Message, RepoData, RepoEntry } from "./types";

const store = createStore("codereporeader", "kv");

const safe = async <T>(p: Promise<T>, fallback: T): Promise<T> => {
  try {
    return await p;
  } catch {
    return fallback;
  }
};

export const db = {
  repos: () => safe(get<RepoEntry[]>("meta:repos", store), undefined).then((v) => v ?? []),
  saveRepos: (r: RepoEntry[]) => safe(set("meta:repos", r, store), undefined),
  repoData: (id: string) => safe(get<RepoData>("repo:" + id, store), undefined),
  saveRepoData: (id: string, d: RepoData) => safe(set("repo:" + id, d, store), undefined),
  deleteRepoData: (id: string) => safe(del("repo:" + id, store), undefined),
  chats: () => safe(get<ChatMeta[]>("meta:chats", store), undefined).then((v) => v ?? []),
  saveChats: (c: ChatMeta[]) => safe(set("meta:chats", c, store), undefined),
  messages: (chatId: string) => safe(get<Message[]>("chat:" + chatId, store), undefined).then((v) => v ?? []),
  saveMessages: (chatId: string, m: Message[]) => safe(set("chat:" + chatId, m, store), undefined),
  deleteMessages: (chatId: string) => safe(del("chat:" + chatId, store), undefined),
};

export interface Prefs {
  theme: "dark" | "light";
  mode: "fast" | "deep";
  panelOpen: boolean;
  activeRepoId: string | null;
  activeChatId: string | null;
}

const PREFS_KEY = "crr:prefs";

export function loadPrefs(): Prefs {
  const defaults: Prefs = { theme: "dark", mode: "fast", panelOpen: true, activeRepoId: null, activeChatId: null };
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") };
  } catch {
    return defaults;
  }
}

export function savePrefs(p: Prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    /* storage disabled — preferences just won't persist */
  }
}
