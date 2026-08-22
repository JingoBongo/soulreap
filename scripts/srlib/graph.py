"""Import graph: who depends on whom, and where the cycles are."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

_PY_SUFFIXES = (".py", ".pyi")
_JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_JS_INDEXES = tuple(f"/index{ext}" for ext in _JS_SUFFIXES)


@dataclass
class Graph:
    """Resolved intra-repository dependencies."""

    imports: dict[str, list[str]] = field(default_factory=dict)
    imported_by: dict[str, list[str]] = field(default_factory=dict)
    cycles: list[list[str]] = field(default_factory=list)
    # path -> {module as written: repo file it resolved to, or None}
    resolved: dict[str, dict[str, str | None]] = field(default_factory=dict)


def _python_candidates(module: str, from_path: str) -> list[str]:
    """Possible repo paths for a Python import statement."""
    if module.startswith("."):
        depth = len(module) - len(module.lstrip("."))
        rest = module[depth:]
        base = posixpath.dirname(from_path)
        for _ in range(depth - 1):
            base = posixpath.dirname(base)
        target = posixpath.join(base, rest.replace(".", "/")) if rest else base
        return [f"{target}{ext}" for ext in _PY_SUFFIXES] + [f"{target}/__init__.py"]

    parts = module.split(".")
    candidates: list[str] = []
    for start in range(len(parts)):
        stem = "/".join(parts[start:])
        candidates += [f"{stem}{ext}" for ext in _PY_SUFFIXES]
        candidates.append(f"{stem}/__init__.py")
        candidates += [f"src/{stem}.py", f"src/{stem}/__init__.py"]
    return candidates


def _js_candidates(module: str, from_path: str) -> list[str]:
    """Possible repo paths for a JS/TS import specifier."""
    if module.startswith("."):
        stem = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), module))
    elif module.startswith("@/") or module.startswith("~/"):
        stem = module[2:]
    else:
        return []
    stems = [stem, f"src/{stem}", f"app/{stem}"]
    out: list[str] = []
    for candidate in stems:
        out.append(candidate)
        out += [f"{candidate}{ext}" for ext in _JS_SUFFIXES]
        out += [f"{candidate}{index}" for index in _JS_INDEXES]
    return out


def _strongly_connected(edges: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan SCC, returning only components that form a real cycle."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    def strong_connect(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in edges.get(node, ()):
            if nxt not in index:
                strong_connect(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component = []
            while True:
                item = stack.pop()
                on_stack.discard(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1 or node in edges.get(node, ()):
                result.append(sorted(component))

    for node in sorted(edges):
        if node not in index:
            strong_connect(node)
    return result


def build_graph(facts: dict) -> Graph:
    """Resolve every import to a repo file when possible.

    *facts* maps repo-relative paths to FileFacts (or plain dicts with the
    same shape). Imports that resolve to nothing are third-party and dropped.
    """
    known = set(facts)
    graph = Graph()

    for path in sorted(known):
        entry = facts[path]
        lang = entry.lang if hasattr(entry, "lang") else entry.get("lang")
        raw_imports = entry.imports if hasattr(entry, "imports") else entry.get("imports", [])
        resolved: list[str] = []
        per_module: dict[str, str | None] = {}

        for item in raw_imports:
            module = item["module"] if isinstance(item, dict) else str(item)
            if lang == "python":
                candidates = _python_candidates(module, path)
            elif lang in {"typescript", "tsx", "javascript"}:
                candidates = _js_candidates(module, path)
            else:
                candidates = _js_candidates(module, path)
            per_module.setdefault(module, None)
            for candidate in candidates:
                if candidate in known and candidate != path:
                    per_module[module] = candidate
                    if candidate not in resolved:
                        resolved.append(candidate)
                    break

        graph.imports[path] = resolved
        graph.resolved[path] = per_module

    for path in known:
        graph.imported_by.setdefault(path, [])
    for path, targets in graph.imports.items():
        for target in targets:
            graph.imported_by[target].append(path)
    for path in graph.imported_by:
        graph.imported_by[path].sort()

    graph.cycles = _strongly_connected(graph.imports)
    return graph
