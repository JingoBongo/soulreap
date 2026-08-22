"""Static facts extraction: what a source file contains, structurally.

Facts a parser can prove are never left to the model: imports, signatures,
line ranges, recursion, concurrency primitives, I/O targets, TODO markers.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field

from .walk import classify

TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX|BUG)\b[:\-\s]*(.*)")
SQL_RE = re.compile(
    r"\b(SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE)\b",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s\"']+")
NODE_BUILTINS = {
    "assert", "buffer", "child_process", "cluster", "console", "crypto", "dgram",
    "dns", "events", "fs", "http", "http2", "https", "net", "os", "path", "perf_hooks",
    "process", "querystring", "readline", "stream", "string_decoder", "timers", "tls",
    "tty", "url", "util", "v8", "vm", "worker_threads", "zlib",
}


def _is_stdlib(module: str, lang: str | None) -> bool:
    """True when the module ships with the language runtime."""
    head = module.lstrip(".").split(".")[0].split("/")[0]
    if not head:
        return False
    if lang == "python":
        return head in sys.stdlib_module_names
    if module.startswith("node:"):
        return True
    return head in NODE_BUILTINS


SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|SALT|DSN)", re.IGNORECASE)

DB_MODULES = re.compile(
    r"^(asyncpg|psycopg2?|sqlalchemy|sqlmodel|supabase|pymongo|motor|databases"
    r"|peewee|tortoise|aiomysql|duckdb|qdrant_client|pinecone)"
)
HTTP_MODULES = re.compile(r"^(httpx|requests|aiohttp|urllib|http|websockets|grpc|openai|anthropic|boto3)")
QUEUE_MODULES = re.compile(r"^(pika|kafka|confluent_kafka|celery|nats|aio_pika|kombu)")
CACHE_MODULES = re.compile(r"^(redis|aioredis|aiocache|cachetools|diskcache|memcache)")
FS_MODULES = re.compile(r"^(shutil|tempfile|pathlib|glob|zipfile|tarfile)")

DB_CALLS = {
    "fetch", "fetchrow", "fetchval", "fetchall", "fetchone", "executemany",
    "execute", "commit", "rollback", "cursor", "upsert",
}
FS_CALLS = {"read_text", "write_text", "read_bytes", "write_bytes", "mkdir", "unlink", "rename", "iterdir", "rglob"}
THREAD_SYMBOLS = {"Thread", "ThreadPoolExecutor", "thread_map"}
PROCESS_SYMBOLS = {"Process", "ProcessPoolExecutor", "Popen", "fork"}
ASYNC_SYMBOLS = {
    "create_task", "gather", "as_completed", "wait_for", "run_in_executor",
    "TaskGroup", "to_thread", "ensure_future",
}
LOCK_SYMBOLS = {"Lock", "RLock", "Semaphore", "BoundedSemaphore", "Event", "Condition", "Barrier"}
QUEUE_SYMBOLS = {"Queue", "LifoQueue", "PriorityQueue", "SimpleQueue"}


@dataclass
class FileFacts:
    """Everything a parser can state about one file."""

    path: str
    lang: str | None = None
    sha: str = ""
    lines: int = 0
    doc: str | None = None
    imports: list[dict] = field(default_factory=list)
    constants: list[dict] = field(default_factory=list)
    classes: list[dict] = field(default_factory=list)
    functions: list[dict] = field(default_factory=list)
    todos: list[dict] = field(default_factory=list)
    concurrency: list[str] = field(default_factory=list)
    io: dict[str, list[str]] = field(default_factory=dict)
    recursive: list[str] = field(default_factory=list)
    parse_error: str | None = None

    def to_dict(self) -> dict:
        """Serializable form used by facts.json."""
        return {
            "path": self.path, "lang": self.lang, "sha": self.sha, "lines": self.lines,
            "doc": self.doc, "imports": self.imports, "constants": self.constants,
            "classes": self.classes, "functions": self.functions, "todos": self.todos,
            "concurrency": self.concurrency, "io": self.io, "recursive": self.recursive,
            "parse_error": self.parse_error,
        }


# ---------------------------------------------------------------- python ---


def _redact_url(text: str) -> str:
    """Drop query string and fragment.

    A URL literal in source can carry a hardcoded credential. Scheme, host and
    path are what a reader needs; everything after ? or # is where secrets live.
    """
    return re.split(r"[?#]", text, maxsplit=1)[0]


def _safe_value(name: str, value: str) -> str:
    """A constant's value, with anything that looks like a credential removed."""
    if SECRET_NAME_RE.search(name):
        return "<redacted>"
    return URL_RE.sub(lambda m: _redact_url(m.group(0)), value)


def _first_line(text: str | None) -> str | None:
    if not text:
        return None
    stripped = text.strip().splitlines()
    return stripped[0].strip() if stripped else None


def _fmt_arg(arg: ast.arg, default: ast.expr | None = None) -> str:
    out = arg.arg
    if arg.annotation is not None:
        out += ": " + ast.unparse(arg.annotation)
    if default is not None:
        out += " = " + ast.unparse(default)
    return out


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Human-readable signature, annotations included."""
    a = node.args
    positional = list(a.posonlyargs) + list(a.args)
    padding: list[ast.expr | None] = [None] * (len(positional) - len(a.defaults))
    parts = [_fmt_arg(arg, dflt) for arg, dflt in zip(positional, padding + list(a.defaults), strict=True)]
    if a.posonlyargs:
        parts.insert(len(a.posonlyargs), "/")
    if a.vararg is not None:
        parts.append("*" + _fmt_arg(a.vararg))
    elif a.kwonlyargs:
        parts.append("*")
    for arg, dflt in zip(a.kwonlyargs, a.kw_defaults, strict=True):
        parts.append(_fmt_arg(arg, dflt))
    if a.kwarg is not None:
        parts.append("**" + _fmt_arg(a.kwarg))
    sig = f"{node.name}({', '.join(parts)})"
    if node.returns is not None:
        sig += " -> " + ast.unparse(node.returns)
    return sig


def _own_nodes(node: ast.AST):
    """Descendants of *node*, not descending into nested definitions."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(current))


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            target = sub.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _describe_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    return {
        "name": node.name,
        "signature": _signature(node),
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "is_generator": any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _own_nodes(node)),
        "decorators": [ast.unparse(d) for d in node.decorator_list],
        "start_line": node.lineno,
        "end_line": node.end_lineno or node.lineno,
        "doc": _first_line(ast.get_docstring(node)),
    }


def _find_cycles(graph: dict[str, set[str]]) -> set[str]:
    """Names that sit on a call cycle, self-calls included."""
    on_cycle: set[str] = set()
    colour: dict[str, int] = {}
    stack: list[str] = []

    def visit(name: str) -> None:
        colour[name] = 1
        stack.append(name)
        for nxt in graph.get(name, ()):  # deterministic enough for reporting
            if nxt == name:
                on_cycle.add(name)
            elif colour.get(nxt, 0) == 0:
                visit(nxt)
            elif colour.get(nxt) == 1:
                on_cycle.update(stack[stack.index(nxt):])
        stack.pop()
        colour[name] = 2

    for node in sorted(graph):
        if colour.get(node, 0) == 0:
            visit(node)
    return on_cycle


def _collect_symbols(tree: ast.AST) -> tuple[set[str], set[str], list[str]]:
    call_names: set[str] = set()
    attrs: set[str] = set()
    strings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                call_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                attrs.add(node.func.attr)
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)
        elif isinstance(node, ast.Name):
            call_names.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)
    return call_names, attrs, strings


def _detect_io(modules: list[str], names: set[str], attrs: set[str], strings: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}

    def note(kind: str, evidence: str) -> None:
        bucket = found.setdefault(kind, [])
        if evidence not in bucket and len(bucket) < 5:
            bucket.append(evidence)

    for module in modules:
        head = module.lstrip(".")
        if DB_MODULES.match(head):
            note("db", f"import {module}")
        if HTTP_MODULES.match(head):
            note("http", f"import {module}")
        if QUEUE_MODULES.match(head):
            note("queue", f"import {module}")
        if CACHE_MODULES.match(head):
            note("cache", f"import {module}")
        if FS_MODULES.match(head):
            note("fs", f"import {module}")

    for attr in sorted(attrs & DB_CALLS):
        note("db", f".{attr}()")
    for attr in sorted(attrs & FS_CALLS):
        note("fs", f".{attr}()")
    if "open" in names:
        note("fs", "open()")

    for text in strings:
        if SQL_RE.search(text):
            note("db", f"sql: {text.strip()[:60]}")
        url = URL_RE.search(text)
        if url:
            note("http", _redact_url(url.group(0))[:60])
    return found


def _detect_concurrency(modules: list[str], names: set[str], attrs: set[str], has_async_def: bool) -> list[str]:
    tags: set[str] = set()
    symbols = names | attrs
    heads = {m.lstrip(".").split(".")[0] for m in modules}

    if symbols & THREAD_SYMBOLS or "threading" in heads:
        tags.add("threads")
    if symbols & PROCESS_SYMBOLS or {"multiprocessing", "subprocess"} & heads:
        tags.add("processes")
    if symbols & ASYNC_SYMBOLS or ("asyncio" in heads and has_async_def):
        tags.add("async")
    if symbols & LOCK_SYMBOLS:
        tags.add("locks")
    if symbols & QUEUE_SYMBOLS or "queue" in heads:
        tags.add("queues")
    return sorted(tags)


def _todos(source: str) -> list[dict]:
    out: list[dict] = []
    for number, line in enumerate(source.splitlines(), start=1):
        match = TODO_RE.search(line)
        if match:
            out.append({"kind": match.group(1), "line": number, "text": match.group(2).strip()[:120]})
    return out


def extract_python(source: str, path: str) -> FileFacts:
    """Facts for a Python file, via the stdlib AST."""
    facts = FileFacts(path=path, lang="python", lines=source.count("\n") + (0 if source.endswith("\n") else 1))
    facts.todos = _todos(source)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        facts.parse_error = f"{exc.msg} (line {exc.lineno})"
        return facts

    facts.doc = _first_line(ast.get_docstring(tree))
    modules: list[str] = []
    call_graph: dict[str, set[str]] = {}
    defined: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
                facts.imports.append({
                    "module": alias.name, "names": [], "line": node.lineno,
                    "stdlib": _is_stdlib(alias.name, "python"),
                })
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            modules.append(module)
            facts.imports.append({
                "module": module, "names": [a.name for a in node.names], "line": node.lineno,
                "stdlib": _is_stdlib(module, "python") if not node.level else False,
            })
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    value = ast.unparse(node.value) if node.value is not None else ""
                    facts.constants.append({
                        "name": target.id,
                        "value": _safe_value(target.id, value)[:80],
                        "line": node.lineno,
                    })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.functions.append(_describe_function(node))
            defined.add(node.name)
            call_graph[node.name] = _called_names(node)
        elif isinstance(node, ast.ClassDef):
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(_describe_function(item))
                    defined.add(item.name)
                    call_graph[item.name] = _called_names(item)
            facts.classes.append({
                "name": node.name,
                "bases": [ast.unparse(b) for b in node.bases],
                "decorators": [ast.unparse(d) for d in node.decorator_list],
                "start_line": node.lineno,
                "end_line": node.end_lineno or node.lineno,
                "doc": _first_line(ast.get_docstring(node)),
                "methods": methods,
            })

    trimmed = {name: calls & defined for name, calls in call_graph.items()}
    facts.recursive = sorted(_find_cycles(trimmed))

    names, attrs, strings = _collect_symbols(tree)
    has_async = any(isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(tree))
    facts.io = _detect_io(modules, names, attrs, strings)
    facts.concurrency = _detect_concurrency(modules, names, attrs, has_async)
    return facts


# ----------------------------------------------------------- tree-sitter ---

_IMPORT_NODES = {"import_statement", "import_declaration", "import_from_statement", "use_declaration", "import_spec"}
_FUNC_NODES = {
    "function_declaration", "function_definition", "method_definition",
    "method_declaration", "function_item",
}
_CLASS_NODES = {
    "class_declaration", "class_definition", "class_specifier", "struct_item",
    "interface_declaration", "enum_declaration",
}
_STRING_NODES = {"string", "string_literal", "interpreted_string_literal", "raw_string_literal"}

_IMPORT_RE = re.compile(r"""(?:from\s+['"]([^'"]+)['"]|import\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"])""")
_FUNC_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE)
_CLASS_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)", re.MULTILINE)


def _node_text(node) -> str:
    return node.text.decode("utf-8", errors="replace")


def _first_string(node) -> str | None:
    stack = list(node.children)
    while stack:
        current = stack.pop(0)
        if current.type in _STRING_NODES:
            return _node_text(current).strip("\"'`")
        stack.extend(current.children)
    return None


def _extract_regex(source: str, path: str, lang: str | None) -> FileFacts:
    """Fallback used when no grammar is available. Never raises."""
    facts = FileFacts(path=path, lang=lang, lines=source.count("\n") + (0 if source.endswith("\n") else 1))
    facts.todos = _todos(source)
    seen: set[str] = set()
    for match in _IMPORT_RE.finditer(source):
        module = next(g for g in match.groups() if g)
        if module not in seen:
            seen.add(module)
            facts.imports.append({
                "module": module, "names": [], "line": source[:match.start()].count("\n") + 1,
                "stdlib": _is_stdlib(module, lang),
            })
    for match in _FUNC_RE.finditer(source):
        line = source[:match.start()].count("\n") + 1
        facts.functions.append({
            "name": match.group(1), "signature": match.group(0).strip(), "is_async": "async" in match.group(0),
            "is_generator": False, "decorators": [], "start_line": line, "end_line": line, "doc": None,
        })
    for match in _CLASS_RE.finditer(source):
        line = source[:match.start()].count("\n") + 1
        facts.classes.append({
            "name": match.group(1), "bases": [], "decorators": [], "start_line": line,
            "end_line": line, "doc": None, "methods": [],
        })
    return facts


def _self_recursive(source: str, functions: list[dict]) -> list[str]:
    """Names that appear called inside their own body.

    A textual check, not a call graph: enough to flag the recursion a reader
    needs to see, and it never claims recursion the source does not show.
    """
    lines = source.splitlines()
    found: list[str] = []
    for fn in functions:
        body = "\n".join(lines[fn["start_line"]: fn["end_line"]])
        if re.search(rf"\b{re.escape(fn['name'])}\s*\(", body):
            found.append(fn["name"])
    return sorted(set(found))


def extract_treesitter(source: str, lang: str, path: str) -> FileFacts:
    """Facts for a non-Python file, via tree-sitter with a regex fallback."""
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(lang)
    except Exception:
        return _extract_regex(source, path, lang)

    facts = FileFacts(path=path, lang=lang, lines=source.count("\n") + (0 if source.endswith("\n") else 1))
    facts.todos = _todos(source)
    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception as exc:
        facts.parse_error = str(exc)[:200]
        return facts

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        kind = node.type
        if kind in _IMPORT_NODES or (kind == "export_statement" and node.child_by_field_name("source")):
            # `export { X } from "./y"` is a dependency exactly like an import;
            # barrel files are made of nothing else.
            source_node = node.child_by_field_name("source")
            module = _node_text(source_node).strip("\"'`") if source_node else _first_string(node)
            if module:
                facts.imports.append({
                    "module": module, "names": [], "line": node.start_point[0] + 1,
                    "stdlib": _is_stdlib(module, lang),
                })
        elif kind in _FUNC_NODES or kind in _CLASS_NODES:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                record = {
                    "name": _node_text(name_node),
                    "signature": _node_text(node).split("{")[0].strip()[:160],
                    "is_async": _node_text(node).lstrip().startswith("async"),
                    "is_generator": False,
                    "decorators": [],
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "doc": None,
                }
                if kind in _FUNC_NODES:
                    facts.functions.append(record)
                else:
                    record.update({"bases": [], "methods": []})
                    facts.classes.append(record)
        elif kind == "lexical_declaration":
            text = _node_text(node)
            match = re.match(r"const\s+([A-Z][A-Z0-9_]*)\s*=\s*(.+)", text)
            if match:
                facts.constants.append({
                    "name": match.group(1),
                    "value": _safe_value(match.group(1), match.group(2).rstrip(";"))[:80],
                    "line": node.start_point[0] + 1,
                })
        stack.extend(node.children)

    modules = [i["module"] for i in facts.imports]
    lowered = source.lower()
    names = set(re.findall(r"\b(\w+)\s*\(", source))
    attrs = set(re.findall(r"\.(\w+)\s*\(", source))
    facts.io = _detect_io(modules, names, attrs, URL_RE.findall(source) + SQL_RE.findall(source))
    facts.concurrency = _detect_concurrency(modules, names, attrs, "async " in lowered)
    facts.functions.sort(key=lambda f: f["start_line"])
    facts.classes.sort(key=lambda c: c["start_line"])
    facts.imports.sort(key=lambda i: i["line"])
    facts.recursive = _self_recursive(source, facts.functions + [
        m for c in facts.classes for m in c.get("methods", [])
    ])
    return facts


def extract_source(source: str, path: str) -> FileFacts:
    """Dispatch to the right extractor for *path*."""
    _, lang, _ = classify(path)
    if lang == "python":
        return extract_python(source, path)
    if lang:
        return extract_treesitter(source, lang, path)
    facts = FileFacts(path=path, lang=None, lines=source.count("\n") + (0 if source.endswith("\n") else 1))
    facts.todos = _todos(source)
    return facts
