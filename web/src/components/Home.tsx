import { useEffect, useRef, useState } from "react";
import { BookMarked, Check, ChevronRight, CircleAlert, FileArchive, LoaderCircle, Menu, Search } from "lucide-react";
import { useStore } from "../store";
import { compact } from "../lib/text";
import { Logo } from "./Logo";

const EXAMPLES = ["pallets/flask", "psf/requests", "expressjs/express", "Vamshi6665/Codereporeader"];

const STAGES = [
  { key: "resolve", label: "Finding the latest commit" },
  { key: "download", label: "Downloading and analyzing the code" },
  { key: "index", label: "Building the search index" },
] as const;

function Progress() {
  const a = useStore((s) => s.analyzing)!;
  const cancel = useStore((s) => s.cancelAnalysis);
  const addRepoFromUrl = useStore((s) => s.addRepoFromUrl);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (a.error) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [a.error]);
  const stages = a.source.toLowerCase().endsWith(".zip") ? STAGES.slice(1) : STAGES;
  const current = stages.findIndex((s) => s.key === a.stage);
  const secs = Math.floor((now - a.startedAt) / 1000);
  return (
    <section className="progress" aria-live="polite">
      <div className="progress__title">
        {a.error ? <CircleAlert size={16} color="var(--danger)" aria-hidden="true" /> : <LoaderCircle size={16} className="spin" aria-hidden="true" />}
        <span>{a.source.replace(/^https?:\/\/(www\.)?github\.com\//, "")}</span>
        {!a.error && <span className="progress__time num">{secs}s</span>}
      </div>
      {a.error ? (
        <div className="progress__error" role="alert">
          <span>{a.error}</span>
        </div>
      ) : (
        <ol className="steps">
          {stages.map((s, i) => (
            <li key={s.key} className={`step${i < current ? " is-done" : i === current ? " is-active" : ""}`}>
              {i < current ? <Check size={16} aria-hidden="true" /> : i === current ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <span className="step__ring" aria-hidden="true" />}
              {s.label}
              {i === current && s.key === "download" && secs > 12 && <span style={{ color: "var(--text-3)", fontSize: 12.5 }}>Large repositories take up to a minute.</span>}
            </li>
          ))}
        </ol>
      )}
      <div className="progress__row">
        {a.error && !a.source.toLowerCase().endsWith(".zip") && (
          <button type="button" className="btn btn--primary" onClick={() => addRepoFromUrl(a.source)}>
            Try again
          </button>
        )}
        <button type="button" className="btn" onClick={cancel}>
          {a.error ? "Dismiss" : "Cancel"}
        </button>
      </div>
    </section>
  );
}

export function Home() {
  const analyzing = useStore((s) => s.analyzing);
  const repos = useStore((s) => s.repos);
  const addRepoFromUrl = useStore((s) => s.addRepoFromUrl);
  const addRepoFromZip = useStore((s) => s.addRepoFromZip);
  const selectRepo = useStore((s) => s.selectRepo);
  const setSidebarOpen = useStore((s) => s.setSidebarOpen);
  const maxMb = useStore((s) => s.config?.max_upload_mb ?? 4.3);
  const [url, setUrl] = useState("");
  const [over, setOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const urlInput = useRef<HTMLInputElement>(null);
  const busy = !!analyzing && !analyzing.error;

  useEffect(() => {
    urlInput.current?.focus();
  }, []);

  const submit = (value: string) => {
    const v = value.trim();
    if (!v || busy) return;
    void addRepoFromUrl(v);
  };

  return (
    <div className="home">
      <div className="topbar" style={{ borderBottom: 0 }}>
        <button type="button" className="icon-btn topbar__menu" aria-label="Open menu" onClick={() => setSidebarOpen(true)}>
          <Menu size={18} />
        </button>
      </div>
      <div className="home__inner">
        <div className="home__head">
          <Logo size={40} />
          <h1>Understand any codebase.</h1>
          <p className="home__lede">
            Paste a public GitHub link or upload a ZIP. Ask questions in plain English and get answers that cite the exact files and lines they come from.
          </p>
        </div>

        <form
          className="repo-input"
          onSubmit={(e) => {
            e.preventDefault();
            submit(url);
          }}
        >
          <Search size={18} aria-hidden="true" />
          <label htmlFor="repo-url" className="sr-only">
            GitHub repository link
          </label>
          <input
            id="repo-url"
            ref={urlInput}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://github.com/owner/repository"
            autoComplete="off"
            spellCheck={false}
            inputMode="url"
          />
          <button type="submit" className="btn btn--primary btn--lg" disabled={!url.trim() || busy}>
            Analyze
          </button>
        </form>

        {analyzing ? (
          <Progress />
        ) : (
          <>
            <div className="home__alt">
              <span>Try</span>
              <div className="examples">
                {EXAMPLES.map((e) => (
                  <button key={e} type="button" className="btn example" onClick={() => { setUrl(e); submit(e); }}>
                    {e}
                  </button>
                ))}
              </div>
            </div>
            <div
              className={`drop${over ? " is-over" : ""}`}
              onDragOver={(e) => {
                e.preventDefault();
                setOver(true);
              }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setOver(false);
                const f = e.dataTransfer.files?.[0];
                if (f) void addRepoFromZip(f);
              }}
            >
              <FileArchive size={22} aria-hidden="true" />
              <div className="drop__text">
                Private code? Drop a ZIP here.
                <small>Up to {maxMb} MB. It's analyzed in memory and never stored on the server.</small>
              </div>
              <button type="button" className="btn" onClick={() => fileInput.current?.click()}>
                Upload ZIP
              </button>
              <input
                ref={fileInput}
                type="file"
                accept=".zip,application/zip"
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void addRepoFromZip(f);
                  e.target.value = "";
                }}
              />
            </div>
          </>
        )}

        {!!repos.length && (
          <section>
            <p className="intro__label">Recent repositories</p>
            <div className="recent">
              {repos.slice(0, 6).map((r) => (
                <button key={r.id} type="button" className="recent__item" onClick={() => selectRepo(r.id)}>
                  <BookMarked size={16} aria-hidden="true" />
                  <span>
                    <span className="recent__name">{r.fullName}</span>
                    <span className="recent__sub" style={{ display: "block" }}>
                      {r.topLanguage ? `${r.topLanguage}, ` : ""}
                      {compact(r.files)} files, {compact(r.lines)} lines
                    </span>
                  </span>
                  <ChevronRight size={16} className="recent__go" aria-hidden="true" />
                </button>
              ))}
            </div>
          </section>
        )}
        <p className="panel__note" style={{ margin: 0 }}>
          Your chats and the analyzed code stay in this browser. Each question, with the code it needs, is sent to OpenAI to write the answer.
        </p>
      </div>
    </div>
  );
}
