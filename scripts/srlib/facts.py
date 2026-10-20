"""Static facts extraction: what a source file contains, structurally.

Facts a parser can prove are never left to the model: imports, signatures,
line ranges, recursion, concurrency primitives, I/O targets, TODO markers.
"""

from __future__ import annotations

import ast
import re

from . import schema as schema_detectors
from .facts_ast import (
    CONST_VALUE_MAX,
    SNIPPET_MAX,
    _called_names,
    _class_fields,
    _collect_symbols,
    _describe_function,
    _doc_more,
    _find_cycles,
    _first_line,
    shape_groups,
)
from .facts_binaries import _binaries, _main_calls
from .facts_detect import _detect_concurrency, _detect_io, _is_stdlib, _safe_value, _todos
from .facts_model import FACTS_VERSION, FileFacts, is_trivial  # noqa: F401  (re-exported)
from .facts_treesitter import extract_treesitter
from .facts_ts import extract_ts
from .fingerprints import text_fingerprint
from .hazards import hazards_of, io_aliases, module_constants, module_names
from .stores import detect_stores
from .tasks import detect_tasks
from .walk import classify


def _docstring_ids(tree: ast.Module) -> set[int]:
    """id() of every docstring constant in the tree.

    A docstring showing an example schema is prose about a table, not a
    declaration of one. Turning it into a table would be fabricating exactly
    the kind of fact this module exists to avoid. Python comments never reach
    the AST at all, so `# CREATE TABLE ...` is already invisible without this.
    """
    found: set[int] = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", [])
        first = body[0] if body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def _python_schema(tree: ast.Module, source: str) -> dict:
    """The schema this file declares: DDL in literals plus ORM models.

    String literals are walked over the whole tree, not just the module body:
    `CREATE TABLE` inside `def _init_db()` is an entirely ordinary shape, and
    limiting the search to `tree.body` would miss it completely.
    """
    tables: list[dict] = []
    unread: list[dict] = []
    docstrings = _docstring_ids(tree)

    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "CREATE" in node.value.upper():
            found, missed = schema_detectors.detect_sql_ddl(node.value, "")
            offset = node.lineno - 1
            for table in found:
                table["line"] += offset
                table["end_line"] += offset
            for item in missed:
                item["line"] += offset
            tables += found
            unread += missed
        elif isinstance(node, ast.JoinedStr) and any(
            isinstance(part, ast.Constant) and isinstance(part.value, str) and "CREATE TABLE" in part.value.upper()
            for part in node.values
        ):
            unread.append({
                "line": node.lineno,
                "why": "DDL is built by an f-string; it cannot be read statically",
                "snippet": ast.unparse(node)[:SNIPPET_MAX],
            })

    orm_tables, orm_unread = schema_detectors.detect_sqlalchemy(tree, source)
    # Structures live in their own key, never among `tables`. Everything that draws a
    # database — `group_databases`, the ERD, the Schema section of a card — reads
    # `tables`, so a dataclass added there would appear inside a schema frame beside
    # rows that really exist in Postgres, and nothing on the picture would say which
    # was which.
    structures, structure_unread = schema_detectors.detect_structures(tree, source)
    block = {
        "tables": tables + orm_tables,
        "targets": schema_detectors.detect_targets(tree),
        "unread": unread + orm_unread + structure_unread,
    }
    # The key appears only on a file that declares something. Most files declare no
    # structure at all, and facts.json is committed: an empty list on every one of a
    # thousand entries is a thousand lines of nothing. Every reader goes through
    # `.get("structures") or []` for the same reason it goes through `.get("tables")`.
    if structures:
        block["structures"] = structures
    return block


# `apps.webhooks.apps.WebhooksConfig`, `site_pkg.urls`: two or more identifier parts, nothing else.
DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")


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
    facts.doc_more = _doc_more(ast.get_docstring(tree))
    facts.binaries = _binaries(tree)
    hz_names, hz_io, hz_consts = module_names(tree), io_aliases(tree), module_constants(tree)
    # Code that runs at import — a network call or a swallowed error in module scope — has no symbol of
    # its own; it is reported as `file::__module__` so the checklist still sees it.
    top_level = ast.Module(body=[s for s in tree.body if not isinstance(
        s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))], type_ignores=[])
    facts.main_calls = _main_calls(tree, path)
    facts.module_hazards = [h for h in hazards_of(top_level, hz_names, hz_io, hz_consts)
                            if h["kind"] in ("swallow", "external", "spawn", "lock")]
    modules: list[str] = []
    call_graph: dict[str, set[str]] = {}
    callable_nodes: dict[str, ast.AST] = {}
    defined: set[str] = set()

    facts.string_refs = sorted({
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 120
        and DOTTED.fullmatch(node.value)
    })
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
                **({"aliases": aliases} if (aliases := {a.asname: a.name for a in node.names if a.asname}) else {}),
            })
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    value = ast.unparse(node.value) if node.value is not None else ""
                    facts.constants.append({
                        "name": target.id,
                        "value": _safe_value(target.id, value)[:CONST_VALUE_MAX],
                        "line": node.lineno,
                    })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.functions.append(_describe_function(node, hz_names, hz_io, hz_consts))
            callable_nodes[node.name] = node
            defined.add(node.name)
            call_graph[node.name] = _called_names(node)
        elif isinstance(node, ast.ClassDef):
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(_describe_function(item, hz_names, hz_io, hz_consts))
                    callable_nodes[item.name] = item
                    defined.add(item.name)
                    call_graph[item.name] = _called_names(item)
            facts.classes.append({
                "name": node.name,
                "bases": [ast.unparse(b) for b in node.bases],
                "decorators": [ast.unparse(d) for d in node.decorator_list],
                "start_line": node.lineno,
                "end_line": node.end_lineno or node.lineno,
                "doc": _first_line(ast.get_docstring(node)),
                "fields": _class_fields(node),
                "methods": methods,
            })

    # An import inside a function (or under a module-level `if`/`try`) is still a dependency:
    # config.py's lazy `import os_utils` made impact miss a real dependent. Recorded as `lazy`
    # so a reader can tell it from the module's import block.
    top_level = {id(node) for node in tree.body}
    for node in ast.walk(tree):
        if id(node) in top_level or not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.imports.append({"module": alias.name, "names": [], "line": node.lineno,
                                      "stdlib": _is_stdlib(alias.name, "python"), "lazy": True})
        else:
            module = "." * node.level + (node.module or "")
            aliases = {a.asname: a.name for a in node.names if a.asname}
            facts.imports.append({"module": module, "names": [a.name for a in node.names], "line": node.lineno,
                                  "stdlib": _is_stdlib(module, "python") if not node.level else False,
                                  "lazy": True, **({"aliases": aliases} if aliases else {})})

    # Who calls whom, inside this one file. The graph was already being built for
    # cycle detection and thrown away; keeping it is what lets a card separate the
    # entry points from the helpers they lean on, which on a file with a dozen
    # private helpers is the thing a reader is actually trying to work out.
    trimmed = {name: calls & defined for name, calls in call_graph.items()}
    facts.calls = {name: sorted(called) for name, called in sorted(trimmed.items())}
    facts.recursive = sorted(_find_cycles(trimmed))
    facts.shape_groups = shape_groups(callable_nodes)

    names, attrs, strings = _collect_symbols(tree)
    has_async = any(isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(tree))
    facts.io = _detect_io(modules, names, attrs, strings)
    facts.concurrency = _detect_concurrency(modules, names, attrs, has_async)
    facts.schema = _python_schema(tree, source)
    facts.stores = detect_stores(tree)
    facts.tasks = detect_tasks(tree)
    if not path.endswith("__init__.py"):  # a package's re-exports are its public API, kept
        _mark_reexports(tree, facts.imports)
    return facts


def _mark_reexports(tree: ast.Module, imports: list[dict]) -> None:
    """`from walk import walk` named only in `__all__` is a re-export, not a use: soulreap's CLI
    re-exported walk/facts/graph for old tests, which put them a step ahead of `build.py`, the
    file that calls them, and read 769 lines of them whole."""
    exported = {e.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
                and isinstance(node.value, (ast.List, ast.Tuple))
                for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    if not exported:
        return
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    annotations = [n.annotation for n in ast.walk(tree) if isinstance(n, (ast.arg, ast.AnnAssign))]
    annotations += [n.returns for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    used |= {word for a in annotations if isinstance(a, ast.Constant) and isinstance(a.value, str)
             for word in re.findall(r"[A-Za-z_]\w*", a.value)}  # `x: "Foo"` uses Foo
    for item in imports:
        bound = {original: alias for alias, original in (item.get("aliases") or {}).items()}
        local = [bound.get(n, n) for n in item.get("names") or []]
        if local and all(n in exported and n not in used for n in local):
            item["reexport"] = True


TS_LANGS = frozenset({"typescript", "tsx", "javascript"})


def _with_text_fingerprints(facts: FileFacts, source: str) -> FileFacts:
    """Give every non-Python callable an `sfp`, so prose citing it can go stale."""
    lines = source.splitlines()
    callables = list(facts.functions) + [m for cls in facts.classes for m in cls.get("methods") or []]
    for fn in callables:
        if not fn.get("sfp"):
            body = "\n".join(lines[fn["start_line"] - 1: fn["end_line"]])
            fn["sfp"] = text_fingerprint(fn.get("signature") or fn["name"], body)
    return facts


def extract_source(source: str, path: str) -> FileFacts:
    """Dispatch to the right extractor for *path*."""
    _, lang, _ = classify(path)
    if lang == "python":
        return extract_python(source, path)
    if lang in TS_LANGS:
        return _with_text_fingerprints(extract_ts(source, lang, path), source)
    if lang:
        return _with_text_fingerprints(extract_treesitter(source, lang, path), source)
    facts = FileFacts(path=path, lang=None, lines=source.count("\n") + (0 if source.endswith("\n") else 1))
    facts.todos = _todos(source)
    return facts
