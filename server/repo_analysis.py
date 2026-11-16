"""
repo_analysis.py
================
Static analysis over an in-memory ``VirtualRepo``.

Produces everything the app needs from one pass:

  * stats       — files, lines, languages, tests, TODOs
  * symbols     — classes / functions / methods / types with file + line
  * routes      — HTTP endpoints (Flask, FastAPI, Express, Spring, Next.js…)
  * frameworks  — detected from manifests (package.json, requirements, go.mod…)
  * entry points, architecture signals, largest files
  * repo map    — a compact text overview that is sent to the model on every turn
  * file pack   — the source files the browser indexes for retrieval

Python is parsed with ``ast``; other languages use line-anchored regexes, which are
fast and good enough for an outline (they can miss unusual formatting).
"""

from __future__ import annotations

import ast
import bisect
import json
import os
import re
import time
from collections import Counter, defaultdict

# ── Languages ─────────────────────────────────────────────────────────────────
EXT_LANG = {
    ".py": "Python", ".pyw": "Python", ".pyi": "Python", ".ipynb": "Jupyter Notebook",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".cs": "C#", ".fs": "F#", ".vb": "Visual Basic",
    ".c": "C", ".h": "C/C++ Header", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".m": "Objective-C",
    ".dart": "Dart", ".lua": "Lua", ".r": "R", ".jl": "Julia", ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang", ".hs": "Haskell", ".clj": "Clojure", ".elm": "Elm", ".zig": "Zig",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell", ".bat": "Batch",
    ".sql": "SQL", ".prisma": "Prisma", ".graphql": "GraphQL", ".gql": "GraphQL", ".proto": "Protobuf",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".sass": "SCSS", ".less": "Less",
    ".vue": "Vue", ".svelte": "Svelte", ".astro": "Astro",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".xml": "XML", ".ini": "INI",
    ".md": "Markdown", ".mdx": "Markdown", ".rst": "reStructuredText", ".tex": "TeX",
    ".tf": "Terraform", ".hcl": "HCL", ".dockerfile": "Dockerfile", ".gradle": "Groovy",
}
NAME_LANG = {"Dockerfile": "Dockerfile", "Makefile": "Makefile", "Jenkinsfile": "Groovy",
             "Gemfile": "Ruby", "Rakefile": "Ruby", "Procfile": "Procfile"}

# Languages that count as "code" (not docs/config) for headline numbers.
CODE_LANGS = {
    "Python", "Jupyter Notebook", "JavaScript", "TypeScript", "Java", "Kotlin", "Scala", "Groovy",
    "C#", "F#", "Visual Basic", "C", "C/C++ Header", "C++", "Go", "Rust", "Ruby", "PHP", "Swift",
    "Objective-C", "Dart", "Lua", "R", "Julia", "Elixir", "Erlang", "Haskell", "Clojure", "Elm", "Zig",
    "Shell", "PowerShell", "Batch", "SQL", "Vue", "Svelte", "Astro", "HTML", "CSS", "SCSS", "Less",
    "Terraform", "HCL", "Prisma", "GraphQL", "Protobuf", "Dockerfile", "Makefile",
}

COMMENT_PREFIX = {
    "Python": ("#",), "Shell": ("#",), "Ruby": ("#",), "R": ("#",), "YAML": ("#",), "TOML": ("#",),
    "Elixir": ("#",), "Terraform": ("#", "//"), "PowerShell": ("#",), "Makefile": ("#",),
    "Dockerfile": ("#",), "SQL": ("--",), "Lua": ("--",), "Haskell": ("--",),
}
C_STYLE = {"JavaScript", "TypeScript", "Java", "Kotlin", "Scala", "C#", "C", "C/C++ Header", "C++", "Go",
           "Rust", "PHP", "Swift", "Dart", "Groovy", "Objective-C", "Zig", "Prisma", "Proto", "CSS", "SCSS", "Less"}


def language_of(path: str):
    base = path.rsplit("/", 1)[-1]
    if base in NAME_LANG:
        return NAME_LANG[base]
    if base.lower().startswith("dockerfile"):
        return "Dockerfile"
    return EXT_LANG.get(os.path.splitext(base)[1].lower())


TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e)(/|$)|(^|/)(test_[^/]+|[^/]+_test\.\w+|[^/]+\.(test|spec)\.\w+)$", re.I)
DOC_RE = re.compile(r"(^|/)(docs?|documentation)(/|$)", re.I)


# ── Notebook → text ───────────────────────────────────────────────────────────
def notebook_to_text(raw: str) -> str:
    try:
        nb = json.loads(raw)
    except Exception:
        return raw[:20000]
    out = []
    for i, cell in enumerate(nb.get("cells", [])):
        src = cell.get("source", "")
        src = "".join(src) if isinstance(src, list) else str(src)
        kind = cell.get("cell_type", "code")
        out.append(f"# %% [{kind}] cell {i + 1}\n{src.rstrip()}\n")
    return "\n".join(out)


# ── Symbol extraction ─────────────────────────────────────────────────────────
class _LineIndex:
    def __init__(self, text):
        self.starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line(self, offset):
        return bisect.bisect_right(self.starts, offset)


def _py_symbols(path, text, symbols, routes):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return False
    http_methods = {"get", "post", "put", "patch", "delete", "head", "options", "websocket", "route", "api_route"}

    def doc(node):
        try:
            d = ast.get_docstring(node)
        except Exception:
            d = None
        return (d or "").strip().split("\n")[0][:140]

    def route_from_decorators(fn, owner=None):
        for d in fn.decorator_list:
            if not isinstance(d, ast.Call) or not isinstance(d.func, ast.Attribute):
                continue
            attr = d.func.attr.lower()
            if attr not in http_methods or not d.args:
                continue
            first = d.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            methods = [attr.upper()] if attr not in ("route", "api_route") else ["GET"]
            for kw in d.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    ms = [e.value.upper() for e in kw.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                    methods = ms or methods
            for m in methods:
                routes.append({"method": m, "path": first.value, "handler": fn.name, "file": path, "line": fn.lineno})

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append({"name": node.name, "kind": "class", "file": path, "line": node.lineno,
                            "end": getattr(node, "end_lineno", node.lineno), "doc": doc(node)})
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append({"name": f"{node.name}.{sub.name}", "kind": "method", "file": path,
                                    "line": sub.lineno, "end": getattr(sub, "end_lineno", sub.lineno),
                                    "async": isinstance(sub, ast.AsyncFunctionDef), "doc": doc(sub)})
                    route_from_decorators(sub)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append({"name": node.name, "kind": "function", "file": path, "line": node.lineno,
                            "end": getattr(node, "end_lineno", node.lineno),
                            "async": isinstance(node, ast.AsyncFunctionDef), "doc": doc(node)})
            route_from_decorators(node)
    return True


_RX = {
    "js": [
        ("class", re.compile(r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)", re.M)),
        ("function", re.compile(r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*[<(]", re.M)),
        ("function", re.compile(r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*(?:async\s+)?(?:\([^)\n]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=>", re.M)),
        ("interface", re.compile(r"^[ \t]*(?:export\s+)?(?:declare\s+)?interface\s+([A-Za-z_$][\w$]*)", re.M)),
        ("type", re.compile(r"^[ \t]*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*(?:<[^>\n]*>)?\s*=", re.M)),
        ("enum", re.compile(r"^[ \t]*(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)", re.M)),
    ],
    "jvm": [
        ("class", re.compile(r"^[ \t]*(?:@\w+\s+)*(?:(?:public|private|protected|internal|abstract|final|sealed|static|open|data|partial)\s+)*class\s+(\w+)", re.M)),
        ("interface", re.compile(r"^[ \t]*(?:(?:public|private|protected|internal|sealed)\s+)*interface\s+(\w+)", re.M)),
        ("enum", re.compile(r"^[ \t]*(?:(?:public|private|protected|internal)\s+)*enum\s+(?:class\s+)?(\w+)", re.M)),
        ("record", re.compile(r"^[ \t]*(?:(?:public|private|protected|internal)\s+)*record\s+(\w+)", re.M)),
        ("method", re.compile(r"^[ \t]+(?:(?:public|private|protected|internal|static|final|abstract|override|async|virtual|synchronized)\s+)+[\w<>\[\],.? ]+\s+(\w+)\s*\([^;{]*\)\s*(?:throws [\w., ]+)?\s*\{", re.M)),
        ("function", re.compile(r"^[ \t]*(?:(?:public|private|internal|override|suspend|inline)\s+)*fun\s+(?:<[^>]+>\s*)?(?:[\w.]+\.)?(\w+)\s*\(", re.M)),
    ],
    "go": [
        ("method", re.compile(r"^func\s+\(\s*\w+\s+\*?(\w+)[^)]*\)\s+(\w+)\s*[\[(]", re.M)),
        ("function", re.compile(r"^func\s+(\w+)\s*[\[(]", re.M)),
        ("struct", re.compile(r"^type\s+(\w+)\s+struct\b", re.M)),
        ("interface", re.compile(r"^type\s+(\w+)\s+interface\b", re.M)),
    ],
    "rust": [
        ("function", re.compile(r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)", re.M)),
        ("struct", re.compile(r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?struct\s+(\w+)", re.M)),
        ("enum", re.compile(r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?enum\s+(\w+)", re.M)),
        ("trait", re.compile(r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?trait\s+(\w+)", re.M)),
    ],
    "ruby": [
        ("class", re.compile(r"^[ \t]*class\s+([A-Z][\w:]*)", re.M)),
        ("module", re.compile(r"^[ \t]*module\s+([A-Z][\w:]*)", re.M)),
        ("method", re.compile(r"^[ \t]*def\s+(?:self\.)?([\w?!=]+)", re.M)),
    ],
    "php": [
        ("class", re.compile(r"^[ \t]*(?:abstract\s+|final\s+)?class\s+(\w+)", re.M)),
        ("interface", re.compile(r"^[ \t]*interface\s+(\w+)", re.M)),
        ("function", re.compile(r"^[ \t]*(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+(\w+)", re.M)),
    ],
    "swift": [
        ("class", re.compile(r"^[ \t]*(?:(?:public|private|internal|open|final|fileprivate)\s+)*(?:class|actor)\s+(\w+)", re.M)),
        ("struct", re.compile(r"^[ \t]*(?:(?:public|private|internal|fileprivate)\s+)*struct\s+(\w+)", re.M)),
        ("protocol", re.compile(r"^[ \t]*(?:(?:public|private|internal)\s+)*protocol\s+(\w+)", re.M)),
        ("function", re.compile(r"^[ \t]*(?:(?:public|private|internal|open|static|override|fileprivate|@\w+)\s+)*func\s+(\w+)", re.M)),
    ],
    "dart": [
        ("class", re.compile(r"^[ \t]*(?:abstract\s+)?class\s+(\w+)", re.M)),
    ],
}
LANG_FAMILY = {
    "JavaScript": "js", "TypeScript": "js", "Vue": "js", "Svelte": "js", "Astro": "js",
    "Java": "jvm", "Kotlin": "jvm", "Scala": "jvm", "C#": "jvm", "Groovy": "jvm",
    "Go": "go", "Rust": "rust", "Ruby": "ruby", "PHP": "php", "Swift": "swift", "Dart": "dart",
}
_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "new", "else", "do", "try", "synchronized", "using", "lock", "foreach", "function"}

_ROUTE_RX = [
    # Express / Koa / Fastify / Hono: app.get('/x', …), router.post("/y"…)
    re.compile(r"\b(?:app|router|server|api|routes?|fastify|hono)\.(get|post|put|patch|delete|options|head|all)\(\s*['\"`]([^'\"`]+)['\"`]", re.I),
    # Spring: @GetMapping("/x"), @RequestMapping(value = "/x")
    re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\(\s*(?:value\s*=\s*|path\s*=\s*)?\{?\s*\"([^\"]*)\""),
    # ASP.NET: [HttpGet("x")] / [Route("x")]
    re.compile(r"\[(HttpGet|HttpPost|HttpPut|HttpPatch|HttpDelete|Route)\(\s*\"([^\"]*)\""),
    # Go: http.HandleFunc("/x", …), r.GET("/x", …), e.POST(...)
    re.compile(r"\b\w+\.(HandleFunc|Handle|GET|POST|PUT|PATCH|DELETE)\(\s*\"([^\"]+)\""),
]


def _regex_symbols(path, text, lang, symbols, routes):
    fam = LANG_FAMILY.get(lang)
    idx = None
    if fam:
        idx = _LineIndex(text)
        for kind, rx in _RX[fam]:
            for m in rx.finditer(text):
                if fam == "go" and kind == "method":
                    name = f"{m.group(1)}.{m.group(2)}"
                else:
                    name = m.group(1)
                if name in _KEYWORDS:
                    continue
                symbols.append({"name": name, "kind": kind, "file": path, "line": idx.line(m.start())})
    for rx in _ROUTE_RX:
        for m in rx.finditer(text):
            method = m.group(1).upper()
            method = {"HTTPGET": "GET", "HTTPPOST": "POST", "HTTPPUT": "PUT", "HTTPPATCH": "PATCH",
                      "HTTPDELETE": "DELETE", "HANDLEFUNC": "ANY", "HANDLE": "ANY", "REQUEST": "ANY",
                      "ROUTE": "ANY", "ALL": "ANY"}.get(method, method)
            idx = idx or _LineIndex(text)
            routes.append({"method": method, "path": m.group(2) or "/", "handler": "", "file": path,
                           "line": idx.line(m.start())})


def _file_routes(path, routes):
    """File-system routing (Next.js, SvelteKit, Vercel functions)."""
    p = path
    m = re.match(r"^(?:src/)?app/(.*)/route\.(?:t|j)sx?$", p)
    if m:
        routes.append({"method": "ANY", "path": "/" + re.sub(r"\(.*?\)/?", "", m.group(1)), "handler": "route handler", "file": p, "line": 1})
        return
    m = re.match(r"^(?:src/)?pages/api/(.*)\.(?:t|j)sx?$", p)
    if m:
        routes.append({"method": "ANY", "path": "/api/" + re.sub(r"/?index$", "", m.group(1)), "handler": "API route", "file": p, "line": 1})


# ── Manifests & frameworks ────────────────────────────────────────────────────
JS_FRAMEWORKS = {
    "next": "Next.js", "react": "React", "vue": "Vue", "nuxt": "Nuxt", "svelte": "Svelte",
    "@sveltejs/kit": "SvelteKit", "@angular/core": "Angular", "solid-js": "Solid", "astro": "Astro",
    "express": "Express", "fastify": "Fastify", "koa": "Koa", "@nestjs/core": "NestJS", "hono": "Hono",
    "vite": "Vite", "webpack": "webpack", "tailwindcss": "Tailwind CSS", "typescript": "TypeScript",
    "jest": "Jest", "vitest": "Vitest", "mocha": "Mocha", "@playwright/test": "Playwright", "cypress": "Cypress",
    "prisma": "Prisma", "@prisma/client": "Prisma", "mongoose": "Mongoose", "sequelize": "Sequelize",
    "typeorm": "TypeORM", "drizzle-orm": "Drizzle", "@supabase/supabase-js": "Supabase", "firebase": "Firebase",
    "electron": "Electron", "react-native": "React Native", "expo": "Expo", "three": "Three.js", "d3": "D3",
    "socket.io": "Socket.IO", "graphql": "GraphQL", "@apollo/client": "Apollo Client", "redux": "Redux",
    "@reduxjs/toolkit": "Redux Toolkit", "zustand": "Zustand", "@tanstack/react-query": "TanStack Query",
    "openai": "OpenAI SDK", "@anthropic-ai/sdk": "Anthropic SDK", "ai": "Vercel AI SDK", "langchain": "LangChain",
    "stripe": "Stripe", "axios": "Axios", "zod": "Zod", "sharp": "sharp", "puppeteer": "Puppeteer",
}
PY_FRAMEWORKS = {
    "flask": "Flask", "django": "Django", "fastapi": "FastAPI", "starlette": "Starlette", "streamlit": "Streamlit",
    "gradio": "Gradio", "pandas": "pandas", "numpy": "NumPy", "scikit-learn": "scikit-learn", "sklearn": "scikit-learn",
    "torch": "PyTorch", "tensorflow": "TensorFlow", "keras": "Keras", "transformers": "Hugging Face Transformers",
    "langchain": "LangChain", "llama-index": "LlamaIndex", "openai": "OpenAI SDK", "anthropic": "Anthropic SDK",
    "sqlalchemy": "SQLAlchemy", "pydantic": "Pydantic", "celery": "Celery", "pytest": "pytest", "requests": "Requests",
    "httpx": "HTTPX", "boto3": "AWS SDK (boto3)", "pyspark": "PySpark", "apache-airflow": "Airflow", "dbt-core": "dbt",
    "matplotlib": "Matplotlib", "seaborn": "seaborn", "plotly": "Plotly", "scipy": "SciPy", "xgboost": "XGBoost",
    "lightgbm": "LightGBM", "shap": "SHAP", "opencv-python": "OpenCV", "beautifulsoup4": "Beautiful Soup",
    "selenium": "Selenium", "scrapy": "Scrapy", "uvicorn": "Uvicorn", "gunicorn": "Gunicorn", "alembic": "Alembic",
    "psycopg2": "PostgreSQL (psycopg2)", "psycopg2-binary": "PostgreSQL (psycopg2)", "pymongo": "MongoDB (PyMongo)",
    "redis": "Redis", "supabase": "Supabase", "jupyter": "Jupyter", "polars": "Polars", "duckdb": "DuckDB",
}
OTHER_FRAMEWORK_RX = [
    ("go.mod", re.compile(r"github\.com/gin-gonic/gin"), "Gin"),
    ("go.mod", re.compile(r"github\.com/labstack/echo"), "Echo"),
    ("go.mod", re.compile(r"github\.com/gofiber/fiber"), "Fiber"),
    ("go.mod", re.compile(r"github\.com/gorilla/mux"), "Gorilla mux"),
    ("Cargo.toml", re.compile(r"^\s*tokio\s*=", re.M), "Tokio"),
    ("Cargo.toml", re.compile(r"^\s*actix-web\s*=", re.M), "Actix Web"),
    ("Cargo.toml", re.compile(r"^\s*axum\s*=", re.M), "Axum"),
    ("Cargo.toml", re.compile(r"^\s*serde\s*=", re.M), "Serde"),
    ("pom.xml", re.compile(r"spring-boot"), "Spring Boot"),
    ("build.gradle", re.compile(r"spring-boot|org\.springframework"), "Spring Boot"),
    ("build.gradle.kts", re.compile(r"spring-boot|org\.springframework"), "Spring Boot"),
    ("Gemfile", re.compile(r"""gem\s+['"]rails['"]"""), "Ruby on Rails"),
    ("Gemfile", re.compile(r"""gem\s+['"]sinatra['"]"""), "Sinatra"),
    ("composer.json", re.compile(r"laravel/framework"), "Laravel"),
    ("composer.json", re.compile(r"symfony/"), "Symfony"),
    ("pubspec.yaml", re.compile(r"^\s*flutter:", re.M), "Flutter"),
]
MANIFESTS = {
    "package.json": "Node.js (npm)", "requirements.txt": "Python (pip)", "pyproject.toml": "Python (pyproject)",
    "Pipfile": "Python (Pipenv)", "setup.py": "Python (setuptools)", "environment.yml": "Conda",
    "go.mod": "Go modules", "Cargo.toml": "Rust (Cargo)", "pom.xml": "Java (Maven)",
    "build.gradle": "Gradle", "build.gradle.kts": "Gradle (Kotlin)", "Gemfile": "Ruby (Bundler)",
    "composer.json": "PHP (Composer)", "pubspec.yaml": "Dart (pub)", "mix.exs": "Elixir (Mix)",
    "Package.swift": "Swift Package Manager", "deno.json": "Deno",
}


def _py_requirement_names(text):
    names = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if m:
            names.append(m.group(1).lower().replace("_", "-"))
    return names


def _pyproject_names(text):
    names = re.findall(r"""^\s*["']?([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[<>=~!\[;]|["'],?\s*$|\s*=\s*)""", text, re.M)
    return [n.lower().replace("_", "-") for n in names]


def detect_stack(texts: dict, paths: list):
    frameworks, manifests, deps = [], [], {}
    seen = set()

    def add(label):
        if label not in seen:
            seen.add(label)
            frameworks.append(label)

    for p in sorted(texts, key=lambda x: (x.count("/"), x)):
        base = p.rsplit("/", 1)[-1]
        if base in MANIFESTS and p.count("/") <= 3:
            manifests.append({"file": p, "type": MANIFESTS[base]})
        text = texts[p]
        if base == "package.json" and p.count("/") <= 3:
            try:
                pkg = json.loads(text)
            except Exception:
                continue
            names = list((pkg.get("dependencies") or {}).keys()) + list((pkg.get("devDependencies") or {}).keys())
            deps[p] = names[:60]
            for n in names:
                if n in JS_FRAMEWORKS:
                    add(JS_FRAMEWORKS[n])
        elif base in ("requirements.txt", "requirements-dev.txt", "requirements.in") or (base.startswith("requirements") and base.endswith(".txt")):
            names = _py_requirement_names(text)
            deps[p] = names[:60]
            for n in names:
                if n in PY_FRAMEWORKS:
                    add(PY_FRAMEWORKS[n])
        elif base in ("pyproject.toml", "Pipfile", "setup.py", "environment.yml"):
            names = _pyproject_names(text) if base != "setup.py" else re.findall(r"""['"]([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[<>=~!]|['"])""", text)
            names = [n.lower().replace("_", "-") for n in names]
            for n in names:
                if n in PY_FRAMEWORKS:
                    add(PY_FRAMEWORKS[n])
        for fname, rx, label in OTHER_FRAMEWORK_RX:
            if base == fname and rx.search(text):
                add(label)
        if base.endswith(".csproj") and "Microsoft.AspNetCore" in text:
            add("ASP.NET Core")

    all_paths = {p["path"] for p in paths}
    infra = []
    if any(p.rsplit("/", 1)[-1].lower().startswith("dockerfile") for p in all_paths):
        infra.append("Docker")
    if any(p.rsplit("/", 1)[-1] in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") for p in all_paths):
        infra.append("Docker Compose")
    if any(p.startswith(".github/workflows/") for p in all_paths):
        infra.append("GitHub Actions")
    if any(p.endswith(".tf") for p in all_paths):
        infra.append("Terraform")
    if "vercel.json" in all_paths:
        infra.append("Vercel")
    if "netlify.toml" in all_paths:
        infra.append("Netlify")
    if any(p.rsplit("/", 1)[-1] in ("serverless.yml", "serverless.yaml") for p in all_paths):
        infra.append("Serverless Framework")
    if any(re.search(r"(^|/)(k8s|kubernetes|helm|charts)/", p) for p in all_paths):
        infra.append("Kubernetes")
    if "Procfile" in all_paths:
        infra.append("Procfile (Heroku-style)")
    return frameworks, infra, manifests, deps


# ── Entry points ──────────────────────────────────────────────────────────────
ENTRY_NAMES = {
    "main.py": "Python entry script", "app.py": "Python app module", "manage.py": "Django management entry",
    "wsgi.py": "WSGI entry", "asgi.py": "ASGI entry", "server.py": "Server module", "run.py": "Run script",
    "__main__.py": "Package entry (python -m)", "index.js": "JS entry", "main.js": "JS entry",
    "server.js": "Node server", "app.js": "Node app", "index.ts": "TS entry", "main.ts": "TS entry",
    "server.ts": "Node server", "main.go": "Go main package", "main.rs": "Rust binary", "lib.rs": "Rust library root",
    "Program.cs": ".NET entry", "Main.java": "Java entry", "Application.java": "Spring Boot application",
    "main.tsx": "React entry", "main.jsx": "React entry", "App.tsx": "Root React component", "App.jsx": "Root React component",
    "main.dart": "Dart / Flutter entry", "index.html": "HTML entry page",
}


def detect_entry_points(texts: dict, paths: list):
    found = {}

    def add(path, reason, weight):
        if path not in found or found[path][1] < weight:
            found[path] = (reason, weight)

    for p in paths:
        path = p["path"]
        base = path.rsplit("/", 1)[-1]
        depth = path.count("/")
        if base in ENTRY_NAMES and depth <= 3:
            add(path, ENTRY_NAMES[base], 5 - depth)
    for path, text in texts.items():
        lang = language_of(path)
        if lang == "Python":
            if re.search(r"^if\s+__name__\s*==\s*['\"]__main__['\"]", text, re.M):
                add(path, "Runs as a script (__main__ guard)", 6)
            if re.search(r"^\s*\w+\s*=\s*(?:flask\.)?Flask\(", text, re.M):
                add(path, "Creates the Flask app", 8)
            if re.search(r"^\s*\w+\s*=\s*(?:fastapi\.)?FastAPI\(", text, re.M):
                add(path, "Creates the FastAPI app", 8)
            if re.search(r"^\s*import streamlit|^\s*from streamlit", text, re.M) and re.search(r"\bst\.(title|write|set_page_config)\(", text):
                add(path, "Streamlit app", 7)
        elif lang in ("JavaScript", "TypeScript"):
            if re.search(r"\.listen\(\s*(?:process\.env\.\w+|\d+|PORT|port)", text):
                add(path, "Starts an HTTP server", 7)
            if re.search(r"createRoot\(|ReactDOM\.render\(", text):
                add(path, "Mounts the React app", 7)
        elif lang == "Go" and re.search(r"^package main\b", text, re.M) and re.search(r"^func main\(\)", text, re.M):
            add(path, "Go main()", 8)
        elif lang in ("Java", "Kotlin") and re.search(r"public static void main\s*\(|^fun main\(", text, re.M):
            add(path, "main() method", 7)
            if "@SpringBootApplication" in text:
                add(path, "Spring Boot application", 9)
        if path.rsplit("/", 1)[-1] == "package.json" and path.count("/") <= 1:
            try:
                pkg = json.loads(text)
                if isinstance(pkg.get("main"), str):
                    add(pkg["main"].lstrip("./"), "package.json \"main\"", 6)
                scripts = pkg.get("scripts") or {}
                for s in ("dev", "start", "build"):
                    if s in scripts:
                        add(path, f"npm scripts: {', '.join(k for k in ('dev', 'start', 'build', 'test') if k in scripts)}", 4)
                        break
            except Exception:
                pass
    ranked = sorted(found.items(), key=lambda kv: (-kv[1][1], kv[0]))
    return [{"path": k, "reason": v[0]} for k, v in ranked[:12]]


# ── Architecture signals ──────────────────────────────────────────────────────
def detect_patterns(paths, frameworks, infra, routes, test_files):
    names = [p["path"].lower() for p in paths]
    dirs = set()
    for n in names:
        parts = n.split("/")[:-1]
        dirs.update(parts)

    def has_dir(*ks):
        return any(k in dirs for k in ks)

    out = []
    if routes:
        out.append(f"HTTP API ({len(routes)} route{'s' if len(routes) != 1 else ''} found)")
    if has_dir("models", "model") and has_dir("views", "view", "templates") and has_dir("controllers", "controller", "routes", "handlers"):
        out.append("MVC-style layout (models, views, controllers)")
    if has_dir("services", "service") or has_dir("repositories", "repository", "repos", "dao"):
        out.append("Layered architecture (services / repositories)")
    if has_dir("components") or any(f in frameworks for f in ("React", "Vue", "Svelte", "Angular", "Solid")):
        out.append("Component-based UI")
    if sum(1 for n in names if n.endswith("package.json")) > 2 and has_dir("packages", "apps"):
        out.append("Monorepo (multiple packages)")
    if has_dir("migrations", "alembic") or any(f in frameworks for f in ("Prisma", "SQLAlchemy", "TypeORM", "Sequelize", "Drizzle", "Mongoose")):
        out.append("Database / ORM layer")
    if has_dir("middleware", "middlewares"):
        out.append("Middleware pipeline")
    if has_dir("notebooks") or any(n.endswith(".ipynb") for n in names):
        out.append("Notebook-driven analysis")
    if any(f in frameworks for f in ("PyTorch", "TensorFlow", "scikit-learn", "Keras", "XGBoost", "LightGBM", "Hugging Face Transformers")):
        out.append("Machine learning pipeline")
    if any(f in frameworks for f in ("OpenAI SDK", "Anthropic SDK", "LangChain", "LlamaIndex", "Vercel AI SDK")):
        out.append("LLM / generative AI integration")
    if "Docker" in infra:
        out.append("Containerized")
    if "GitHub Actions" in infra:
        out.append("CI/CD with GitHub Actions")
    if any(i in infra for i in ("Vercel", "Netlify", "Serverless Framework")):
        out.append("Serverless deployment")
    if test_files:
        out.append(f"Automated tests ({test_files} test file{'s' if test_files != 1 else ''})")
    else:
        out.append("No automated tests found")
    return out


# ── Main entry ────────────────────────────────────────────────────────────────
def analyze(vr, time_budget_s: float = 90.0) -> dict:
    t0 = time.time()
    texts = vr.texts
    lang_files, lang_lines = Counter(), Counter()
    total_lines = blank = comment = code_lines = 0
    file_lines = {}
    todo = 0
    test_files = 0
    symbols, routes = [], []
    parse_failures = 0

    for p in vr.paths:
        path = p["path"]
        lang = language_of(path)
        if TEST_RE.search(path):
            test_files += 1
        if lang:
            lang_files[lang] += 1
        text = texts.get(path)
        if text is None:
            continue
        if lang == "Jupyter Notebook":
            text = notebook_to_text(text)
        lines = text.split("\n")
        n = len(lines)
        file_lines[path] = n
        total_lines += n
        b = sum(1 for ln in lines if not ln.strip())
        blank += b
        c = 0
        if lang in COMMENT_PREFIX:
            pre = COMMENT_PREFIX[lang]
            c = sum(1 for ln in lines if ln.lstrip().startswith(pre))
        elif lang in C_STYLE:
            c = sum(1 for ln in lines if ln.lstrip().startswith(("//", "/*", "*", "*/")))
        comment += c
        if lang:
            lang_lines[lang] += n
        if lang in CODE_LANGS:
            code_lines += n - b - c
        todo += len(re.findall(r"\b(?:TODO|FIXME|HACK|XXX)\b", text))

        if time.time() - t0 < time_budget_s:
            if lang == "Python":
                if not _py_symbols(path, text, symbols, routes):
                    parse_failures += 1
            elif lang:
                _regex_symbols(path, text, lang, symbols, routes)
        _file_routes(path, routes)

    frameworks, infra, manifests, deps = detect_stack(texts, vr.paths)
    entry_points = detect_entry_points(texts, vr.paths)
    patterns = detect_patterns(vr.paths, frameworks, infra, routes, test_files)

    kinds = Counter(s["kind"] for s in symbols)
    languages = [
        {"name": lang, "files": lang_files[lang], "lines": lang_lines.get(lang, 0)}
        for lang in sorted(lang_files, key=lambda k: (-lang_lines.get(k, 0), -lang_files[k]))
    ]
    largest = sorted(file_lines.items(), key=lambda kv: -kv[1])[:15]

    # Deduplicate routes (same method+path+file).
    seen, uniq_routes = set(), []
    for r in routes:
        key = (r["method"], r["path"], r["file"])
        if key not in seen:
            seen.add(key)
            uniq_routes.append(r)

    return {
        "stats": {
            "files": len(vr.paths),
            "text_files": len(texts),
            "lines": total_lines,
            "code_lines": max(code_lines, 0),
            "blank_lines": blank,
            "comment_lines": comment,
            "classes": kinds.get("class", 0) + kinds.get("struct", 0),
            "functions": kinds.get("function", 0),
            "methods": kinds.get("method", 0),
            "interfaces": kinds.get("interface", 0) + kinds.get("trait", 0) + kinds.get("protocol", 0),
            "symbols": len(symbols),
            "routes": len(uniq_routes),
            "test_files": test_files,
            "todos": todo,
            "python_parse_failures": parse_failures,
            "truncated": vr.truncated,
        },
        "languages": languages,
        "frameworks": frameworks,
        "infra": infra,
        "manifests": manifests,
        "dependencies": deps,
        "entry_points": entry_points,
        "patterns": patterns,
        "routes": uniq_routes[:400],
        "symbols": symbols[:8000],
        "largest_files": [{"path": p, "lines": n} for p, n in largest],
        "file_lines": file_lines,
        "elapsed_ms": int((time.time() - t0) * 1000),
    }


# ── Repo map (sent to the model on every turn) ────────────────────────────────
def _compact_tree(paths, max_lines=260, max_children=30):
    root = {}
    for p in paths:
        node = root
        parts = p["path"].split("/")
        for d in parts[:-1]:
            node = node.setdefault(d + "/", {})
        node[parts[-1]] = None

    lines = []

    def count_files(node):
        return sum(1 if v is None else count_files(v) for v in node.values())

    def walk(node, prefix, depth):
        items = sorted(node.items(), key=lambda kv: (kv[1] is None, kv[0].lower()))
        shown = items[:max_children]
        for i, (name, child) in enumerate(shown):
            if len(lines) >= max_lines:
                return
            last = i == len(shown) - 1 and len(items) <= max_children
            conn = "└── " if last else "├── "
            if child is None:
                lines.append(f"{prefix}{conn}{name}")
            elif depth >= 4:
                lines.append(f"{prefix}{conn}{name} ({count_files(child)} files)")
            else:
                lines.append(f"{prefix}{conn}{name}")
                walk(child, prefix + ("    " if last else "│   "), depth + 1)
        if len(items) > max_children and len(lines) < max_lines:
            rest = items[max_children:]
            n = sum(1 if v is None else count_files(v) for _, v in rest)
            lines.append(f"{prefix}└── … {len(rest)} more entries ({n} files)")

    walk(root, "", 1)
    if len(lines) >= max_lines:
        lines.append(f"… tree truncated ({len(paths)} paths in total)")
    return "\n".join(lines)


def build_repo_map(meta: dict, a: dict, paths: list, max_chars: int = 26000) -> str:
    s = a["stats"]
    out = []
    title = meta.get("full_name") or meta.get("name") or "repository"
    ref = meta.get("ref")
    sha = (meta.get("sha") or "")[:7]
    out.append(f"# Repository: {title}" + (f" (branch {ref}, commit {sha})" if ref else ""))
    if meta.get("description"):
        out.append(f"Description: {meta['description']}")
    out.append(f"Files: {s['files']} total, {s['text_files']} readable text files. Lines: {s['lines']:,} "
               f"(about {s['code_lines']:,} lines of code).")
    if a["languages"]:
        langs = ", ".join(f"{l['name']} ({l['files']} file{'s' if l['files'] != 1 else ''}, {l['lines']:,} lines)" for l in a["languages"][:10])
        out.append(f"Languages: {langs}.")
    out.append(f"Symbols: {s['classes']} classes/structs, {s['functions']} functions, {s['methods']} methods, "
               f"{s['interfaces']} interfaces/traits. Tests: {s['test_files']} test files. TODO/FIXME markers: {s['todos']}.")
    if a["frameworks"]:
        out.append("Frameworks and libraries: " + ", ".join(a["frameworks"]) + ".")
    if a["infra"]:
        out.append("Tooling and deployment: " + ", ".join(a["infra"]) + ".")
    if a["manifests"]:
        out.append("Dependency manifests: " + ", ".join(f"{m['file']} ({m['type']})" for m in a["manifests"][:10]) + ".")
    if a["entry_points"]:
        out.append("Likely entry points: " + "; ".join(f"{e['path']} ({e['reason']})" for e in a["entry_points"][:8]) + ".")
    if a["patterns"]:
        out.append("Architecture signals: " + "; ".join(a["patterns"]) + ".")
    if s.get("truncated"):
        out.append("Note: the repository is very large, so some files were not analyzed.")

    if a["routes"]:
        out.append("\n## HTTP routes")
        for r in a["routes"][:60]:
            h = f" → {r['handler']}" if r.get("handler") else ""
            out.append(f"- {r['method']} {r['path']}{h} ({r['file']}:{r['line']})")
        if len(a["routes"]) > 60:
            out.append(f"- … {len(a['routes']) - 60} more routes")

    out.append("\n## Directory tree")
    out.append(_compact_tree(paths))

    # Outline: top-level symbols per file, most important files first.
    by_file = defaultdict(list)
    for sym in a["symbols"]:
        if sym["kind"] != "method":
            by_file[sym["file"]].append(sym)
    entry = {e["path"] for e in a["entry_points"]}
    ranked = sorted(by_file, key=lambda f: (f not in entry, f.count("/"), -len(by_file[f]), f))
    out.append("\n## Outline (top-level symbols with line numbers)")
    body = "\n".join(out)
    budget = max_chars - len(body)
    outline = []
    for f in ranked:
        syms = by_file[f]
        names = ", ".join(f"{x['name']} L{x['line']}" for x in syms[:24])
        more = f", … +{len(syms) - 24}" if len(syms) > 24 else ""
        line = f"- {f}: {names}{more}"
        if budget - len(line) - 1 < 0:
            outline.append(f"- … outline truncated ({len(ranked) - len(outline)} more files)")
            break
        outline.append(line)
        budget -= len(line) + 1
    return body + "\n" + "\n".join(outline)


# ── File pack (what the browser indexes) ──────────────────────────────────────
DATA_EXTS = {".csv", ".tsv", ".jsonl", ".ndjson", ".log", ".txt", ".sql"}


def _priority(path, entry, manifests):
    base = path.rsplit("/", 1)[-1]
    lang = language_of(path)
    depth = path.count("/")
    if base.lower().startswith("readme") and depth == 0:
        return 1000
    if path in manifests or path in entry:
        return 900 - depth
    if base.lower().startswith("readme"):
        return 600 - depth
    score = 300
    if lang in CODE_LANGS:
        score = 500
    elif lang in ("Markdown", "reStructuredText"):
        score = 330
    elif lang in ("JSON", "YAML", "TOML", "INI", "XML"):
        score = 320
    if TEST_RE.search(path):
        score -= 140
    if DOC_RE.search(path):
        score -= 40
    if re.search(r"(^|/)(examples?|samples?|fixtures?|mocks?|__snapshots__)/", path, re.I):
        score -= 90
    if re.search(r"(^|/)(src|app|lib|api|server|backend|frontend|core|pkg|cmd|internal)/", path):
        score += 25
    return score - depth * 6


def build_file_pack(vr, a, max_total_chars=3_000_000, max_file_chars=160_000):
    entry = {e["path"] for e in a["entry_points"]}
    manifests = {m["file"] for m in a["manifests"]}
    order = sorted(vr.texts, key=lambda p: (-_priority(p, entry, manifests), len(vr.texts[p]), p))
    files, total, omitted = [], 0, 0
    for path in order:
        text = vr.texts[path]
        lang = language_of(path)
        note = None
        if lang == "Jupyter Notebook":
            text = notebook_to_text(text)
        ext = os.path.splitext(path)[1].lower()
        if ext in DATA_EXTS and len(text) > 12_000 and lang not in CODE_LANGS:
            text = "\n".join(text[:12_000].split("\n")[:-1])
            note = "data file: first lines only"
        if len(text) > max_file_chars:
            text = text[:max_file_chars]
            note = f"truncated to the first {max_file_chars // 1000}k characters"
        if total + len(text) > max_total_chars:
            omitted += 1
            continue
        total += len(text)
        files.append({"path": path, "lang": lang, "content": text, **({"note": note} if note else {})})
    return files, {"chars": total, "omitted_files": omitted}
