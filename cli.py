#!/usr/bin/env python3
"""
CodeRepoReader — CLI
====================
Analyze any GitHub/local project and generate a beginner-friendly summary
using the OpenAI SDK.

Usage:
    python cli.py                          # analyze current directory
    python cli.py /path/to/project         # analyze a local folder
    python cli.py https://github.com/...   # download + analyze a GitHub repo

Setup:
    export OPENAI_API_KEY=sk-...
    (optional) export OPENAI_MODEL=gpt-4o-mini
    (optional) export OPENAI_BASE_URL=https://models.github.ai/inference
"""

import os
import sys
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))

from analyzer import collect_project_info          # noqa: E402
from github_fetch import download_repo_tarball      # noqa: E402
from summarizer import summarize_with_openai        # noqa: E402


def print_banner():
    print(
        """
╔══════════════════════════════════════════════╗
║        🔍 CodeRepoReader — CLI                ║
║          Powered by the OpenAI SDK            ║
╚══════════════════════════════════════════════╝
"""
    )


def main():
    print_banner()

    work_dir = None
    if len(sys.argv) > 1:
        target = sys.argv[1]
        if target.startswith(("http://", "https://", "git@")):
            print("🌐  GitHub URL detected — downloading repo...\n")
            token = os.environ.get("GITHUB_TOKEN", "").strip() or None
            try:
                project_path, work_dir = download_repo_tarball(target, token=token)
            except Exception as e:  # noqa: BLE001
                print(f"❌  Download failed: {e}")
                return
        else:
            project_path = os.path.abspath(target)
    else:
        project_path = os.path.abspath(".")

    if not os.path.exists(project_path):
        print(f"❌  Path not found: {project_path}")
        return

    print(f"📁  Project: {project_path}\n")

    try:
        print("📊  Step 1: Analyzing project structure...\n")
        project_info = collect_project_info(project_path)
        print(f"  ✅ Found {len(project_info['files'])} key files\n")

        print("🤖  Step 2: Generating beginner summary with OpenAI...\n")
        summary = summarize_with_openai(project_info)

        if not summary.strip() or summary.startswith("❌"):
            print(f"\n{summary}\n")
            return

        print("\n" + "=" * 60)
        print("📘  BEGINNER-FRIENDLY PROJECT SUMMARY")
        print("=" * 60)
        print(summary)
        print("=" * 60 + "\n")

        out = f"{project_info['name']}_summary.md"
        with open(out, "w") as f:
            f.write(f"# {project_info['name']} — Beginner Summary\n\n{summary}")
        print(f"💾  Saved to: {out}\n")

    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
