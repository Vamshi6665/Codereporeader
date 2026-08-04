"""
Deep Code Analyzer
==================
Performs language-aware static analysis on a project directory,
extracting architecture metrics, class/function counts, patterns, etc.
"""

import os
import re
import ast
import json
from pathlib import Path
from collections import defaultdict

SKIP_DIRS = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv',
    'dist', 'build', '.next', '.cache', '.idea', '.vscode',
    'coverage', '.pytest_cache', 'target', 'bin', 'obj'
}

SKIP_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.lock',
    '.sum', '.woff', '.ttf', '.eot', '.mp4', '.mp3', '.zip',
    '.tar', '.gz', '.pyc', '.pyo', '.class', '.o', '.so', '.dll'
}


def deep_analyze_code(project_path):
    """Run full deep analysis on a project directory."""
    stats = {
        'languages': {},
        'total_files': 0,
        'total_lines': 0,
        'total_blank_lines': 0,
        'total_comment_lines': 0,
        'python': {},
        'javascript': {},
        'java': {},
        'csharp': {},
        'generic': {},
        'dependencies': [],
        'entry_points': [],
        'config_files': [],
        'architecture_patterns': [],
        'file_breakdown': []
    }

    all_files = collect_all_files(project_path)
    stats['total_files'] = len(all_files)

    # Language breakdown
    lang_counter = defaultdict(int)
    for fp in all_files:
        ext = Path(fp).suffix.lower()
        lang = ext_to_language(ext)
        if lang:
            lang_counter[lang] += 1

    stats['languages'] = dict(lang_counter)

    # Line counts
    for fp in all_files:
        try:
            with open(fp, 'r', errors='ignore') as f:
                lines = f.readlines()
            total = len(lines)
            blank = sum(1 for l in lines if l.strip() == '')
            comment = count_comment_lines(lines, Path(fp).suffix.lower())
            stats['total_lines'] += total
            stats['total_blank_lines'] += blank
            stats['total_comment_lines'] += comment
            stats['file_breakdown'].append({
                'file': os.path.relpath(fp, project_path),
                'lines': total,
                'language': ext_to_language(Path(fp).suffix.lower()) or 'other'
            })
        except Exception:
            pass

    # Sort by lines descending
    stats['file_breakdown'].sort(key=lambda x: x['lines'], reverse=True)
    stats['file_breakdown'] = stats['file_breakdown'][:20]  # Top 20

    # Python-specific analysis
    py_files = [f for f in all_files if f.endswith('.py')]
    if py_files:
        stats['python'] = analyze_python(py_files, project_path)

    # JavaScript/TypeScript analysis
    js_files = [f for f in all_files if Path(f).suffix.lower() in ('.js', '.ts', '.jsx', '.tsx')]
    if js_files:
        stats['javascript'] = analyze_javascript(js_files, project_path)

    # Java analysis
    java_files = [f for f in all_files if f.endswith('.java')]
    if java_files:
        stats['java'] = analyze_java(java_files, project_path)

    # C# analysis
    cs_files = [f for f in all_files if f.endswith('.cs')]
    if cs_files:
        stats['csharp'] = analyze_csharp(cs_files, project_path)

    # Generic pattern detection (all languages)
    stats['generic'] = analyze_generic_patterns(all_files, project_path)

    # Dependency files
    stats['dependencies'] = detect_dependencies(project_path)

    # Entry points
    stats['entry_points'] = detect_entry_points(all_files, project_path)

    # Config files
    stats['config_files'] = detect_config_files(project_path)

    # Architecture patterns
    stats['architecture_patterns'] = detect_architecture_patterns(project_path, all_files)

    return stats


def collect_all_files(root):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext not in SKIP_EXTENSIONS:
                files.append(os.path.join(dirpath, fname))
    return files


def ext_to_language(ext):
    mapping = {
        '.py': 'Python', '.pyw': 'Python',
        '.js': 'JavaScript', '.mjs': 'JavaScript',
        '.ts': 'TypeScript', '.tsx': 'TypeScript',
        '.jsx': 'JavaScript',
        '.java': 'Java',
        '.cs': 'C#',
        '.cpp': 'C++', '.cc': 'C++', '.cxx': 'C++',
        '.c': 'C', '.h': 'C/C++ Header',
        '.go': 'Go',
        '.rs': 'Rust',
        '.rb': 'Ruby',
        '.php': 'PHP',
        '.swift': 'Swift',
        '.kt': 'Kotlin', '.kts': 'Kotlin',
        '.scala': 'Scala',
        '.r': 'R',
        '.sh': 'Shell', '.bash': 'Shell',
        '.ps1': 'PowerShell',
        '.html': 'HTML', '.htm': 'HTML',
        '.css': 'CSS', '.scss': 'SCSS', '.sass': 'SCSS', '.less': 'LESS',
        '.sql': 'SQL',
        '.yaml': 'YAML', '.yml': 'YAML',
        '.json': 'JSON',
        '.xml': 'XML',
        '.md': 'Markdown',
        '.toml': 'TOML',
        '.dockerfile': 'Dockerfile',
    }
    return mapping.get(ext)


def count_comment_lines(lines, ext):
    count = 0
    in_block = False
    for line in lines:
        s = line.strip()
        if ext in ('.py',):
            if s.startswith('#'):
                count += 1
            elif s.startswith('"""') or s.startswith("'''"):
                count += 1
        elif ext in ('.js', '.ts', '.jsx', '.tsx', '.java', '.cs', '.cpp', '.c', '.go'):
            if s.startswith('//'):
                count += 1
            elif s.startswith('/*'):
                in_block = True
                count += 1
            elif in_block:
                count += 1
                if '*/' in s:
                    in_block = False
    return count


def analyze_python(py_files, root):
    result = {
        'total_files': len(py_files),
        'classes': [],
        'public_classes': 0,
        'private_classes': 0,
        'functions': [],
        'public_functions': 0,
        'private_functions': 0,
        'async_functions': 0,
        'decorators_used': defaultdict(int),
        'imports': defaultdict(int),
        'total_methods': 0,
        'abstract_classes': 0,
        'dataclasses': 0,
        'exceptions': 0,
        'complexity_hotspots': []
    }

    for fp in py_files:
        try:
            with open(fp, 'r', errors='ignore') as f:
                source = f.read()
            tree = ast.parse(source)
            rel = os.path.relpath(fp, root)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    is_private = node.name.startswith('_')
                    is_abstract = any(
                        (isinstance(d, ast.Name) and d.id in ('ABC', 'ABCMeta')) or
                        (isinstance(d, ast.Attribute) and d.attr in ('ABC', 'ABCMeta'))
                        for b in node.bases
                        for d in [b]
                    )
                    is_dataclass = any(
                        (isinstance(d, ast.Name) and d.id == 'dataclass') or
                        (isinstance(d, ast.Attribute) and d.attr == 'dataclass')
                        for d in node.decorator_list
                    )
                    is_exception = any(
                        (isinstance(b, ast.Name) and 'Error' in b.id or 'Exception' in b.id)
                        for b in node.bases
                    )

                    methods = [n for n in ast.walk(node) if isinstance(n, ast.FunctionDef) or isinstance(n, ast.AsyncFunctionDef)]
                    result['classes'].append({
                        'name': node.name,
                        'file': rel,
                        'line': node.lineno,
                        'methods': len(methods),
                        'private': is_private,
                        'abstract': is_abstract,
                        'dataclass': is_dataclass
                    })
                    result['total_methods'] += len(methods)
                    if is_private:
                        result['private_classes'] += 1
                    else:
                        result['public_classes'] += 1
                    if is_abstract:
                        result['abstract_classes'] += 1
                    if is_dataclass:
                        result['dataclasses'] += 1
                    if is_exception:
                        result['exceptions'] += 1

                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Only top-level functions (not methods)
                    is_async = isinstance(node, ast.AsyncFunctionDef)
                    is_private = node.name.startswith('_')
                    decorators = []
                    for d in node.decorator_list:
                        if isinstance(d, ast.Name):
                            decorators.append(d.id)
                            result['decorators_used'][d.id] += 1
                        elif isinstance(d, ast.Attribute):
                            decorators.append(d.attr)
                            result['decorators_used'][d.attr] += 1
                    result['functions'].append({
                        'name': node.name,
                        'file': rel,
                        'line': node.lineno,
                        'async': is_async,
                        'private': is_private,
                        'decorators': decorators
                    })
                    if is_async:
                        result['async_functions'] += 1
                    if is_private:
                        result['private_functions'] += 1
                    else:
                        result['public_functions'] += 1

                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            result['imports'][alias.name.split('.')[0]] += 1
                    else:
                        if node.module:
                            result['imports'][node.module.split('.')[0]] += 1

        except SyntaxError:
            pass
        except Exception:
            pass

    result['decorators_used'] = dict(result['decorators_used'])
    result['imports'] = dict(sorted(result['imports'].items(), key=lambda x: -x[1])[:20])
    result['total_classes'] = result['public_classes'] + result['private_classes']
    result['total_functions'] = result['public_functions'] + result['private_functions']
    return result


def analyze_javascript(js_files, root):
    result = {
        'total_files': len(js_files),
        'classes': [],
        'public_classes': 0,
        'functions': 0,
        'arrow_functions': 0,
        'async_functions': 0,
        'exports': 0,
        'imports': 0,
        'react_components': 0,
        'hooks': 0,
    }

    class_re = re.compile(r'^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)', re.M)
    func_re = re.compile(r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)', re.M)
    arrow_re = re.compile(r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(', re.M)
    async_re = re.compile(r'\basync\s+function|\basync\s+\(', re.M)
    export_re = re.compile(r'\bexport\b', re.M)
    import_re = re.compile(r'\bimport\b', re.M)
    component_re = re.compile(r'(?:function|const)\s+([A-Z]\w+)', re.M)
    hook_re = re.compile(r'\buse[A-Z]\w+\s*\(', re.M)

    for fp in js_files:
        try:
            with open(fp, 'r', errors='ignore') as f:
                src = f.read()
            rel = os.path.relpath(fp, root)

            for m in class_re.finditer(src):
                result['classes'].append({'name': m.group(1), 'file': rel})
                result['public_classes'] += 1

            result['functions'] += len(func_re.findall(src))
            result['arrow_functions'] += len(arrow_re.findall(src))
            result['async_functions'] += len(async_re.findall(src))
            result['exports'] += len(export_re.findall(src))
            result['imports'] += len(import_re.findall(src))
            result['react_components'] += len(set(component_re.findall(src)))
            result['hooks'] += len(hook_re.findall(src))
        except Exception:
            pass

    return result


def analyze_java(java_files, root):
    result = {
        'total_files': len(java_files),
        'public_classes': 0,
        'private_classes': 0,
        'interfaces': 0,
        'abstract_classes': 0,
        'enums': 0,
        'annotations': 0,
        'public_methods': 0,
        'private_methods': 0,
        'classes': []
    }

    pub_class_re = re.compile(r'\bpublic\s+class\s+(\w+)', re.M)
    priv_class_re = re.compile(r'\bprivate\s+class\s+(\w+)', re.M)
    iface_re = re.compile(r'\binterface\s+(\w+)', re.M)
    abstract_re = re.compile(r'\babstract\s+class\s+(\w+)', re.M)
    enum_re = re.compile(r'\benum\s+(\w+)', re.M)
    annot_re = re.compile(r'^@\w+', re.M)
    pub_method_re = re.compile(r'\bpublic\s+\w[\w<>\[\]]*\s+\w+\s*\(', re.M)
    priv_method_re = re.compile(r'\bprivate\s+\w[\w<>\[\]]*\s+\w+\s*\(', re.M)

    for fp in java_files:
        try:
            with open(fp, 'r', errors='ignore') as f:
                src = f.read()
            rel = os.path.relpath(fp, root)

            for m in pub_class_re.finditer(src):
                result['classes'].append({'name': m.group(1), 'file': rel, 'type': 'public'})
                result['public_classes'] += 1
            for m in priv_class_re.finditer(src):
                result['classes'].append({'name': m.group(1), 'file': rel, 'type': 'private'})
                result['private_classes'] += 1
            result['interfaces'] += len(iface_re.findall(src))
            result['abstract_classes'] += len(abstract_re.findall(src))
            result['enums'] += len(enum_re.findall(src))
            result['annotations'] += len(annot_re.findall(src))
            result['public_methods'] += len(pub_method_re.findall(src))
            result['private_methods'] += len(priv_method_re.findall(src))
        except Exception:
            pass

    return result


def analyze_csharp(cs_files, root):
    result = {
        'total_files': len(cs_files),
        'public_classes': 0,
        'interfaces': 0,
        'abstract_classes': 0,
        'enums': 0,
        'public_methods': 0,
        'properties': 0,
        'namespaces': set(),
        'classes': []
    }

    for fp in cs_files:
        try:
            with open(fp, 'r', errors='ignore') as f:
                src = f.read()
            rel = os.path.relpath(fp, root)

            for m in re.finditer(r'\bpublic\s+(?:partial\s+)?class\s+(\w+)', src):
                result['classes'].append({'name': m.group(1), 'file': rel})
                result['public_classes'] += 1
            result['interfaces'] += len(re.findall(r'\binterface\s+\w+', src))
            result['abstract_classes'] += len(re.findall(r'\babstract\s+class\s+\w+', src))
            result['enums'] += len(re.findall(r'\benum\s+\w+', src))
            result['public_methods'] += len(re.findall(r'\bpublic\s+\w[\w<>\[\]]*\s+\w+\s*\(', src))
            result['properties'] += len(re.findall(r'\{\s*get\s*[;{]', src))
            for m in re.finditer(r'\bnamespace\s+([\w.]+)', src):
                result['namespaces'].add(m.group(1))
        except Exception:
            pass

    result['namespaces'] = list(result['namespaces'])
    return result


def analyze_generic_patterns(all_files, root):
    result = {
        'total_functions': 0,
        'total_classes': 0,
        'todo_count': 0,
        'fixme_count': 0,
        'hack_count': 0,
        'test_files': 0,
        'test_functions': 0,
    }

    for fp in all_files:
        try:
            name = os.path.basename(fp).lower()
            if 'test' in name or 'spec' in name:
                result['test_files'] += 1

            with open(fp, 'r', errors='ignore') as f:
                src = f.read()

            result['todo_count'] += len(re.findall(r'\bTODO\b', src, re.I))
            result['fixme_count'] += len(re.findall(r'\bFIXME\b', src, re.I))
            result['hack_count'] += len(re.findall(r'\bHACK\b', src, re.I))
            result['test_functions'] += len(re.findall(r'(?:def\s+test_|it\(|test\(|@Test)', src))
        except Exception:
            pass

    return result


def detect_dependencies(root):
    deps = []
    dep_files = {
        'requirements.txt': 'Python (pip)',
        'package.json': 'Node.js (npm)',
        'Pipfile': 'Python (pipenv)',
        'pyproject.toml': 'Python (poetry/build)',
        'pom.xml': 'Java (Maven)',
        'build.gradle': 'Java/Kotlin (Gradle)',
        'Cargo.toml': 'Rust (Cargo)',
        'go.mod': 'Go (modules)',
        'Gemfile': 'Ruby (bundler)',
        'composer.json': 'PHP (composer)',
    }
    for filename, desc in dep_files.items():
        fp = os.path.join(root, filename)
        if os.path.exists(fp):
            deps.append({'file': filename, 'type': desc})
    return deps


def detect_entry_points(all_files, root):
    entries = []
    entry_names = {'main.py', 'app.py', 'server.py', 'index.js', 'main.js',
                   'index.ts', 'main.ts', 'Main.java', 'Program.cs', 'main.go', 'main.rs'}
    for fp in all_files:
        name = os.path.basename(fp)
        if name in entry_names:
            entries.append(os.path.relpath(fp, root))
    return entries


def detect_config_files(root):
    configs = []
    config_names = [
        '.env', '.env.example', 'config.py', 'settings.py', 'config.json',
        'config.yaml', 'config.yml', '.eslintrc', '.prettierrc', 'tsconfig.json',
        'webpack.config.js', 'vite.config.js', 'babel.config.js', 'jest.config.js',
        'Dockerfile', 'docker-compose.yml', '.github', 'Makefile'
    ]
    for name in config_names:
        if os.path.exists(os.path.join(root, name)):
            configs.append(name)
    return configs


def detect_architecture_patterns(root, all_files):
    patterns = []
    names_lower = [os.path.relpath(f, root).lower() for f in all_files]

    def has_any(*keywords):
        return any(any(k in n for k in keywords) for n in names_lower)

    if has_any('model', 'view', 'controller'):
        patterns.append('MVC (Model-View-Controller)')
    if has_any('service', 'repository', 'controller'):
        patterns.append('Layered Architecture (Service/Repository pattern)')
    if has_any('router', 'routes', 'endpoint'):
        patterns.append('REST API / Routing')
    if has_any('test', 'spec'):
        patterns.append('Test Suite present')
    if os.path.exists(os.path.join(root, 'Dockerfile')):
        patterns.append('Docker / Containerized')
    if os.path.exists(os.path.join(root, '.github')):
        patterns.append('GitHub Actions / CI-CD')
    if has_any('component', 'components'):
        patterns.append('Component-based UI')
    if has_any('middleware'):
        patterns.append('Middleware pattern')
    if has_any('schema', 'migration', 'models'):
        patterns.append('Database / ORM layer')
    if has_any('event', 'listener', 'emitter', 'pubsub'):
        patterns.append('Event-driven / Pub-Sub')
    if has_any('async', 'asyncio', 'promise', 'await'):
        patterns.append('Asynchronous / Non-blocking I/O')

    return patterns
