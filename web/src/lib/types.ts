export type Mode = "fast" | "deep";

export interface RepoMetaRaw {
  source: "github" | "upload";
  owner: string | null;
  repo: string;
  full_name: string;
  ref: string | null;
  sha: string;
  html_url: string | null;
  description?: string;
  stars?: number;
  default_branch?: string;
}

export interface CodeSymbol {
  name: string;
  kind: string;
  file: string;
  line: number;
  end?: number;
  doc?: string;
  async?: boolean;
}

export interface Route {
  method: string;
  path: string;
  handler: string;
  file: string;
  line: number;
}

export interface Analysis {
  stats: {
    files: number;
    text_files: number;
    lines: number;
    code_lines: number;
    blank_lines: number;
    comment_lines: number;
    classes: number;
    functions: number;
    methods: number;
    interfaces: number;
    symbols: number;
    routes: number;
    test_files: number;
    todos: number;
    truncated: boolean;
  };
  languages: { name: string; files: number; lines: number }[];
  frameworks: string[];
  infra: string[];
  manifests: { file: string; type: string }[];
  entry_points: { path: string; reason: string }[];
  patterns: string[];
  routes: Route[];
  symbols: CodeSymbol[];
  largest_files: { path: string; lines: number }[];
}

export interface TreeEntry {
  path: string;
  size: number;
  kind: "text" | "binary" | "large" | "generated" | "secret" | "skipped";
}

export interface RepoFile {
  path: string;
  lang: string | null;
  content: string;
  note?: string;
}

export interface RepoData {
  version: string;
  repo: RepoMetaRaw;
  analysis: Analysis;
  map: string;
  tree: TreeEntry[];
  files: RepoFile[];
  pack: { chars: number; omitted_files: number };
  elapsed_ms?: number;
}

/** What the sidebar keeps for each analyzed repository. */
export interface RepoEntry {
  id: string;
  source: "github" | "upload";
  fullName: string;
  owner: string | null;
  name: string;
  ref: string | null;
  sha: string;
  htmlUrl: string | null;
  description: string;
  analyzedAt: number;
  files: number;
  lines: number;
  topLanguage: string | null;
}

export interface ChatMeta {
  id: string;
  repoId: string;
  title: string;
  createdAt: number;
  updatedAt: number;
}

export interface Excerpt {
  path: string;
  start: number;
  end: number;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  status?: "thinking" | "streaming" | "done" | "error" | "stopped";
  mode?: Mode;
  modelLabel?: string;
  attachments?: string[];
  excerpts?: Excerpt[];
  error?: { code: string; message: string };
  finish?: string;
  elapsedMs?: number;
}

export interface ServerConfig {
  modes: Record<Mode, { label: string; model: string }>;
  configured: boolean;
  analyzer_version: string;
  max_upload_mb: number;
}
