"""Import graph: who depends on whom, and where the cycles are."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from .graph_js import JsConfigs, js_candidates

_PY_SUFFIXES = (".py", ".pyi")


@dataclass
class Graph:
    """Resolved intra-repository dependencies."""

    imports: dict[str, list[str]] = field(default_factory=dict)
    imported_by: dict[str, list[str]] = field(default_factory=dict)
    cycles: list[list[str]] = field(default_factory=list)
    # path -> {module as written: repo file it resolved to, or None}
    resolved: dict[str, dict[str, str | None]] = field(default_factory=dict)


def _ancestor_prefixes(from_path: str) -> list[str]:
    """Directory prefixes to try, nearest first.

    A workspace member keeps its package under its own ``src/`` directory, so an
    absolute intra-package import (``from afas_proxy.config import ...``) names a
    path that exists only relative to that member — never relative to the repo
    root. Trying each ancestor of the importing file resolves it without hardcoding
    a layout. Nearest first, so a file binds to the ``config.py`` in its own package
    rather than a same-named one in a sibling member.
    """
    directory = posixpath.dirname(from_path)
    parts = directory.split("/") if directory else []
    return ["/".join(parts[:i]) for i in range(len(parts), -1, -1)]


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
    prefixes = _ancestor_prefixes(from_path)
    candidates: list[str] = []
    for start in range(len(parts)):
        stem = "/".join(parts[start:])
        for prefix in prefixes:
            for base in ((f"{prefix}/{stem}", f"{prefix}/src/{stem}") if prefix else (stem, f"src/{stem}")):
                candidates += [f"{base}{ext}" for ext in _PY_SUFFIXES]
                candidates.append(f"{base}/__init__.py")
    return candidates


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


def _string_ref_target(ref: str, from_path: str, known: set[str]) -> str | None:
    """The repo module a dotted string names, whole path only (never a trailing fragment), dropping
    trailing parts for a class path: `apps.webhooks.apps.WebhooksConfig` → apps/webhooks/apps.py."""
    parts = ref.split(".")
    for cut in range(len(parts), 1, -1):
        stem = "/".join(parts[:cut])
        for prefix in _ancestor_prefixes(from_path):
            for base in ((f"{prefix}/{stem}", f"{prefix}/src/{stem}") if prefix else (stem, f"src/{stem}")):
                for candidate in (f"{base}.py", f"{base}/__init__.py"):
                    if candidate in known and candidate != from_path:
                        return candidate
    return None


def build_graph(facts: dict, root: Path | str | None = None) -> Graph:
    """Resolve every import to a repo file when possible.

    *facts* maps repo-relative paths to FileFacts (or plain dicts with the
    same shape). Imports that resolve to nothing are third-party and dropped. *root*, when
    given, lets JS/TS aliases follow the repo's tsconfig/jsconfig `paths` and `baseUrl`.
    """
    known = set(facts)
    graph = Graph()
    configs = JsConfigs(root)

    for path in sorted(known):
        entry = facts[path]
        lang = entry.lang if hasattr(entry, "lang") else entry.get("lang")
        raw_imports = entry.imports if hasattr(entry, "imports") else entry.get("imports", [])
        resolved: list[str] = []
        per_module: dict[str, str | None] = {}

        for item in raw_imports:
            module = item["module"] if isinstance(item, dict) else str(item)
            candidates = (_python_candidates(module, path) if lang == "python"
                          else js_candidates(module, path, configs))
            per_module.setdefault(module, None)
            for candidate in candidates:
                if candidate in known and candidate != path:
                    per_module[module] = candidate
                    if candidate not in resolved:
                        resolved.append(candidate)
                    break
            if lang == "python" and isinstance(item, dict):
                # `from . import rank` / `from pkg import models`: a name that is itself a module
                # is a dependency on that module, not only on the package's __init__ — without
                # this, mapplan → rank was invisible and reach from soulreap's CLI never got there.
                for name in item.get("names") or []:
                    sub = f"{module}{'' if module.endswith('.') else '.'}{name}"
                    hit = next((c for c in _python_candidates(sub, path) if c in known and c != path), None)
                    if hit and hit not in resolved:
                        resolved.append(hit)

        if lang == "python":
            refs = entry.string_refs if hasattr(entry, "string_refs") else entry.get("string_refs", [])
            for ref in refs or []:
                hit = _string_ref_target(ref, path, known)
                if hit and hit not in resolved:
                    resolved.append(hit)

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
