"""
summarizer.py — beginner-friendly project summary via the OpenAI SDK.

This replaces the original GitHub Copilot SDK implementation. It uses the same
OpenAI client as the web app, so it honors:
    OPENAI_API_KEY   — your key
    OPENAI_MODEL     — default model (falls back to gpt-4o-mini)
    OPENAI_BASE_URL  — optional; point at any OpenAI-compatible endpoint
"""

import os


def build_prompt(project_info):
    files_section = ""
    for filename, content in project_info["files"].items():
        files_section += f"\n--- {filename} ---\n{content[:1500]}\n"

    return f"""
You are a friendly coding mentor who explains software projects to beginners.

Analyze this project called "{project_info['name']}" and write a clear,
encouraging summary for someone who is just learning to code.

PROJECT STRUCTURE:
{project_info['structure']}

KEY FILES:
{files_section}

Please respond in exactly this format (keep each section clear and simple):

WHAT IT DOES:
<2-3 sentences explaining what this project does in plain English, no jargon>

TECH STACK:
<bullet list of main languages, frameworks, and tools used with one-line explanation of each>

KEY FILES EXPLAINED:
<bullet list explaining what each important file does in one simple sentence>

HOW TO RUN IT:
<numbered step-by-step instructions to get this project running locally>

WHAT YOU CAN LEARN:
<bullet list of 3-5 concepts a beginner can learn from studying this project>

DIFFICULTY LEVEL:
<one of: Beginner / Intermediate / Advanced — with a 1-sentence reason why>
"""


def summarize_with_openai(project_info):
    """Send project info to the OpenAI SDK and return a beginner summary."""
    try:
        from openai import OpenAI
    except ImportError:
        return "❌ The 'openai' package is not installed. Run: pip install openai"

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return "❌ Set OPENAI_API_KEY first (export OPENAI_API_KEY=sk-...)."

    base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)

    prompt = build_prompt(project_info)
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.3,
        )
        return (completion.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        return f"❌ {type(e).__name__}: {e}"
