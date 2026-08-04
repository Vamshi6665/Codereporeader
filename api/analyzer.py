import os

IMPORTANT_FILES = [
    "README.md", "readme.md", "README.txt", "README.rst",
    "package.json", "requirements.txt", "setup.py", "setup.cfg",
    "pyproject.toml", "Makefile", "Dockerfile", "docker-compose.yml",
    "main.py", "app.py", "index.js", "index.ts", "server.py",
    ".env.example", "config.py", "settings.py"
]

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv",
    "venv", "dist", "build", ".next", ".cache",
    ".idea", ".vscode", "coverage", ".pytest_cache"
}

SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".lock", ".sum", ".woff", ".ttf", ".eot", ".mp4",
    ".mp3", ".zip", ".tar", ".gz", ".pyc", ".pyo"
}

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".rb", ".php", ".cs",
    ".swift", ".kt", ".sh", ".yaml", ".yml", ".toml", ".json"
}


def get_project_structure(root_dir, max_depth=3):
    """Build a tree view of the project structure."""
    lines = []

    def walk(path, depth, prefix=""):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.listdir(path))
        except PermissionError:
            return

        entries = [e for e in entries if e not in SKIP_DIRS]

        for i, entry in enumerate(entries):
            full_path = os.path.join(path, entry)
            is_last = (i == len(entries) - 1)
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{entry}")
            if os.path.isdir(full_path):
                extension = "    " if is_last else "│   "
                walk(full_path, depth + 1, prefix + extension)

    lines.append(os.path.basename(os.path.abspath(root_dir)) + "/")
    walk(root_dir, 1)
    return "\n".join(lines)


def read_important_files(root_dir, max_chars=2000):
    """Read contents of important files."""
    collected = {}

    # First pass: check for known important files
    for filename in IMPORTANT_FILES:
        filepath = os.path.join(root_dir, filename)
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", errors="ignore") as f:
                    content = f.read(max_chars)
                    collected[filename] = content
            except Exception:
                pass

    # Second pass: grab source files
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext in SKIP_EXTENSIONS or ext not in SOURCE_EXTENSIONS:
                continue
            if len(collected) >= 10:
                break
            filepath = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(filepath, root_dir)
            if rel_path not in collected:
                try:
                    with open(filepath, "r", errors="ignore") as f:
                        content = f.read(max_chars)
                        collected[rel_path] = content
                except Exception:
                    pass

    return collected


def collect_project_info(project_path):
    """Collect all project info into a single dict."""
    print(f"  📂 Reading project structure...")
    structure = get_project_structure(project_path)

    print(f"  📄 Reading key files...")
    files = read_important_files(project_path)

    return {
        "path": project_path,
        "name": os.path.basename(os.path.abspath(project_path)),
        "structure": structure,
        "files": files
    }
