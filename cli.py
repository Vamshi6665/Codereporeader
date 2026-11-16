#!/usr/bin/env python3
"""
CodeRepoReader CLI — the same analyzer and model pipeline as the web app, in a terminal.

    python cli.py                                   # analyze the current folder
    python cli.py path/to/project                   # analyze a local folder
    python cli.py https://github.com/owner/repo     # analyze a public GitHub repo
    python cli.py . --map                           # print the repository map sent to the model
    python cli.py . --ask "How does auth work?"     # stream an answer (needs OPENAI_API_KEY)
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import llm  # noqa: E402
from server.repo_analysis import analyze, build_file_pack, build_repo_map  # noqa: E402
from server.repo_source import RepoError, load_github, load_local_dir, resolve_github  # noqa: E402


def load(target):
    if target.startswith(("http://", "https://", "git@")) or (target.count("/") == 1 and not os.path.exists(target)):
        meta = resolve_github(target)
        print(f"Resolved {meta['full_name']} @ {meta['ref']} ({meta['sha'][:7]})", file=sys.stderr)
        return meta, load_github(meta["owner"], meta["repo"], meta["sha"])
    path = os.path.abspath(target)
    if not os.path.isdir(path):
        raise RepoError(f"Folder not found: {path}")
    vr = load_local_dir(path)
    return {"full_name": vr.name, "ref": None, "sha": ""}, vr


def main():
    ap = argparse.ArgumentParser(description="Analyze a codebase and ask questions about it.")
    ap.add_argument("target", nargs="?", default=".", help="local folder or GitHub URL (default: current folder)")
    ap.add_argument("--map", action="store_true", help="print the repository map")
    ap.add_argument("--ask", metavar="QUESTION", help="ask a question and stream the answer")
    ap.add_argument("--deep", action="store_true", help="use Deep mode")
    args = ap.parse_args()

    try:
        meta, vr = load(args.target)
    except RepoError as e:
        sys.exit(f"Error: {e.message}")

    a = analyze(vr)
    s = a["stats"]
    print(f"\n{meta['full_name']}")
    print(f"  {s['files']} files, {s['lines']:,} lines ({s['code_lines']:,} code)")
    print(f"  {s['classes']} classes, {s['functions']} functions, {s['methods']} methods, {s['routes']} HTTP routes")
    if a["languages"]:
        print("  Languages: " + ", ".join(f"{l['name']} ({l['lines']:,})" for l in a["languages"][:6]))
    if a["frameworks"]:
        print("  Stack: " + ", ".join(a["frameworks"] + a["infra"]))
    for e in a["entry_points"][:5]:
        print(f"  Entry: {e['path']} — {e['reason']}")

    repo_map = build_repo_map(meta, a, vr.paths)
    if args.map:
        print("\n" + repo_map)

    if args.ask:
        if not llm.is_configured():
            sys.exit("\nSet OPENAI_API_KEY to ask questions.")
        files, _ = build_file_pack(vr, a, max_total_chars=90_000 if not args.deep else 300_000)
        req = llm.validate({
            "mode": "deep" if args.deep else "fast",
            "repo": {"map": repo_map, "key": meta["full_name"]},
            "messages": [{"role": "user", "content": args.ask}],
            "context": [{"path": f["path"], "start": 1, "text": f["content"]} for f in files],
        })
        print()
        import json
        full, shown, hold = "", 0, len(llm.FOLLOWUP_MARKER)
        for event in llm.stream_chat(req):
            if event.startswith("event: delta"):
                full += json.loads(event.split("data: ", 1)[1])["t"]
                cut = full.find(llm.FOLLOWUP_MARKER)
                safe = cut if cut >= 0 else max(shown, len(full) - hold)
                sys.stdout.write(full[shown:safe])
                sys.stdout.flush()
                shown = safe
            elif event.startswith("event: error"):
                sys.exit("\nError: " + json.loads(event.split("data: ", 1)[1])["message"])
        if llm.FOLLOWUP_MARKER not in full:
            sys.stdout.write(full[shown:])
        print()


if __name__ == "__main__":
    main()
