"""Cross-file flow chains: what an entry point sets in motion, across files.

`facts.calls` is trimmed to names defined in the same file (facts.py trims it), so a
reader asking "what does this entry actually do" gets a one-file tree and no answer for
the part that crosses a boundary. This module resolves calls across files from the
sources with a light AST pass — direct names, `module.func`, `self.method` and typed
instances (`store = UpdatesStore(...)` → `store.sync_repo_names()`) — and marks what it
could not prove as `~heuristic` instead of guessing silently.

`flowtext` prints the chain (`flow`, and `flow --brief` — a flow card's spine); `closure` is what
`rank` and `impact` measure an entry point by; `entry_points` is what `plan` ranks from.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from .paths import is_test_path

MAX_DEPTH = 10
MAX_CHILDREN = 14

# Matched on the last dotted segment, mirroring commands.ENTRY_DECORATORS so the flow
# tool and the skeleton agree on what "reached from outside" means.
ENTRY_DECORATORS = {
    "route", "get", "post", "put", "patch", "delete", "head", "options", "websocket",
    "errorhandler", "before_request", "after_request", "before_app_request",
    "teardown_request", "exception_handler", "middleware", "on_event", "hookimpl",
    "task", "shared_task", "periodic_task", "actor", "scheduled", "cron", "job",
    "command", "group", "cli", "subcommand", "entrypoint",
    "fixture", "hookspec",
    # Dagster: assets, ops and the schedules/sensors that launch them are where a pipeline starts.
    "asset", "multi_asset", "graph_asset", "op", "graph", "schedule", "sensor", "asset_check",
}


def _qual(rel: str, name: str) -> str:
    return f"{rel}::{name}"


@dataclass
class FlowIndex:
    defs: dict[str, dict[str, tuple[str, int, int, str | None]]] = field(default_factory=dict)
    classes: dict[str, set[str]] = field(default_factory=dict)
    alias: dict[str, dict[str, str]] = field(default_factory=dict)
    scans: dict[str, BodyScan] = field(default_factory=dict)
    root: Path = Path(".")

    def files(self) -> list[str]:
        return sorted(self.defs)

    def anchor(self, rel: str, name: str) -> str:
        _kind, start, end, _cls = self.defs[rel][name]
        return f"{rel}::{name} [L{start}-L{end}]"


class BodyScan(ast.NodeVisitor):
    """Calls (name, line, source text of the call) and var -> class assignments."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []
        self.vartypes: dict[str, str] = {}
        self.looped: set[int] = set()  # lines of calls that run on every pass of a for/while
        self._loop = 0

    def _in_loop(self, *parts) -> None:
        self._loop += 1
        for part in parts:
            for node in part if isinstance(part, list) else [part]:
                self.visit(node)
        self._loop -= 1

    def visit_For(self, node: ast.For | ast.AsyncFor):
        self.visit(node.iter)  # evaluated once, before the loop
        self.visit(node.target)
        self._in_loop(node.body)
        for stmt in node.orelse:
            self.visit(stmt)

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While):
        self._in_loop(node.test, node.body)  # the test runs on every pass too
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_Call(self, node: ast.Call):
        fn = node.func
        name = None
        if isinstance(fn, ast.Name):
            name = fn.id
        elif isinstance(fn, ast.Attribute):
            parts = []
            cur = fn
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                parts.reverse()
                name = ".".join(parts)
        if name:
            try:
                text = ast.unparse(node)
            except Exception:
                text = name
            text = " ".join(text.split())
            if len(text) > 72:
                text = text[:69] + "..."
            self.calls.append((name, node.lineno, text))
            if self._loop:
                self.looped.add(node.lineno)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        t = None
        v = node.value
        if isinstance(v, ast.Call):
            f = v.func
            if isinstance(f, ast.Name):
                t = f.id
            elif isinstance(f, ast.Attribute):
                t = f.attr
        elif isinstance(v, ast.Name):
            t = v.id
        if t:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.vartypes[tgt.id] = t
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if isinstance(node.target, ast.Name) and node.annotation is not None:
            a = node.annotation
            if isinstance(a, ast.Name):
                self.vartypes[node.target.id] = a.id
            elif isinstance(a, ast.Attribute):
                self.vartypes[node.target.id] = a.attr
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg):
        a = node.annotation
        if a is not None:
            if isinstance(a, ast.Name):
                self.vartypes[node.arg] = a.id
            elif isinstance(a, ast.Attribute):
                self.vartypes[node.arg] = a.attr
        self.generic_visit(node)


def _top_defs(tree: ast.AST):
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node, None))
        elif isinstance(node, ast.ClassDef):
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((f"{node.name}.{m.name}", m, node.name))
    return out


# Languages whose callables already carry callees in facts (facts_ts): indexed from there, not re-parsed.
FACTS_LANGS = frozenset({"typescript", "tsx", "javascript"})


def _index_from_facts(idx: FlowIndex, rel: str, blob: dict, facts: dict) -> None:
    """Index a file from its facts: bare callee names, no call lines inside a loop known."""
    defs: dict[str, tuple[str, int, int, str | None]] = {}
    calls: dict[str, list[str]] = {}
    for fn in blob.get("functions") or []:
        defs[fn["name"]] = ("func", fn["start_line"], fn["end_line"], None)
        calls[fn["name"]] = fn.get("callees") or []
    for cls in blob.get("classes") or []:
        for method in cls.get("methods") or []:
            qual = f"{cls['name']}.{method['name']}"
            defs[qual] = ("method", method["start_line"], method["end_line"], cls["name"])
            calls[qual] = method.get("callees") or []
    idx.defs[rel] = defs
    idx.classes[rel] = {c["name"] for c in blob.get("classes") or []}
    amap: dict[str, str] = {}
    for imp in blob.get("imports") or []:
        if imp.get("target") in facts:
            bound = {original: alias for alias, original in (imp.get("aliases") or {}).items()}
            for name in imp.get("names") or []:
                amap[bound.get(name, name)] = imp["target"]
    idx.alias[rel] = amap
    for qual, names in calls.items():
        scan = BodyScan()
        # No call lines in facts: logic first (`groupByClient`), rendered components (`<Badge/>`) after.
        ordered = sorted(names, key=lambda n: (n[:1].isupper(), n))
        scan.calls = [(name, defs[qual][1], f"{name}()") for name in ordered]
        idx.scans[_qual(rel, qual)] = scan


def build(root: Path, facts: dict) -> FlowIndex:
    """Parse every code file once. Files that fail to parse are simply absent."""
    idx = FlowIndex(root=root)
    for rel, blob in sorted(facts.items()):
        if (blob or {}).get("lang") in FACTS_LANGS:
            _index_from_facts(idx, rel, blob, facts)
            continue
        if (blob or {}).get("lang") != "python" and not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        defs: dict[str, tuple[str, int, int, str | None]] = {}
        cls: set[str] = set()
        for qn, node, parent in _top_defs(tree):
            defs[qn] = ("method" if parent else "func", node.lineno, node.end_lineno or node.lineno, parent)
            if parent:
                cls.add(parent)
        idx.defs[rel] = defs
        idx.classes[rel] = cls

        amap: dict[str, str] = {}
        for imp in (blob or {}).get("imports", []):
            tgt = imp.get("target")
            if not tgt or tgt not in facts:
                continue
            names = imp.get("names") or []
            if names:
                for n in names:
                    amap[n] = tgt
            else:
                amap[imp["module"].split(".")[-1]] = tgt
        idx.alias[rel] = amap

        for qn, node, _parent in _top_defs(tree):
            scan = BodyScan()
            for stmt in node.body:
                scan.visit(stmt)
            idx.scans[_qual(rel, qn)] = scan
    return idx


def resolve(idx: FlowIndex, rel: str, name: str, vartypes: dict, cls: str | None):
    """-> (target_file, qualname, 'exact'|'heuristic') or None."""
    defs = idx.defs
    if name.startswith("self."):
        method = name.split(".", 1)[1]
        if cls and f"{cls}.{method}" in defs.get(rel, {}):
            return rel, f"{cls}.{method}", "exact"
        return None

    if "." not in name:
        if name in defs.get(rel, {}):
            return rel, name, "exact"
        tgt = idx.alias.get(rel, {}).get(name)
        if tgt and name in defs.get(tgt, {}):
            return tgt, name, "exact"
        return None

    obj, attr = name.split(".", 1)
    attr = attr.split(".")[-1]

    clsname = vartypes.get(obj)
    if clsname:
        owner = rel if clsname in idx.classes.get(rel, set()) else idx.alias.get(rel, {}).get(clsname)
        if owner and f"{clsname}.{attr}" in defs.get(owner, {}):
            return owner, f"{clsname}.{attr}", "exact"

    tgt = idx.alias.get(rel, {}).get(obj)
    if tgt:
        if attr in defs.get(tgt, {}):
            return tgt, attr, "exact"
        if f"{obj}.{attr}" in defs.get(tgt, {}):
            return tgt, f"{obj}.{attr}", "exact"
        return None

    if obj in idx.classes.get(rel, set()) and f"{obj}.{attr}" in defs.get(rel, {}):
        return rel, f"{obj}.{attr}", "exact"

    candidates = []
    for dep in sorted(set(idx.alias.get(rel, {}).values())):
        for qn in defs.get(dep, {}):
            if qn.split(".")[-1] == attr:
                candidates.append((dep, qn))
        for qn in defs.get(rel, {}):
            if qn.split(".")[-1] == attr:
                candidates.append((rel, qn))
    if len(candidates) == 1:
        return candidates[0][0], candidates[0][1], "heuristic"
    return None


def _children(idx: FlowIndex, rel: str, name: str):
    scan = idx.scans.get(_qual(rel, name))
    if scan is None:
        return []
    cls = idx.defs[rel][name][3]
    seen = set()
    out = []
    for cname, line, text in scan.calls:
        r = resolve(idx, rel, cname, scan.vartypes, cls)
        if not r:
            continue
        key = (r[0], r[1])
        if key in seen:
            continue
        seen.add(key)
        out.append((*r, line, text))
    return out[:MAX_CHILDREN]


def closure(idx: FlowIndex, rel: str, name: str, max_depth: int = MAX_DEPTH):
    """Every node the chain from (rel, name) reaches, as {(file, qualname): depth}."""
    out: dict[tuple[str, str], int] = {(rel, name): 0}
    stack = [(rel, name, 0)]
    while stack:
        f, qn, depth = stack.pop()
        if depth >= max_depth:
            continue
        for trel, tqn, _conf, _line, _text in _children(idx, f, qn):
            key = (trel, tqn)
            if key not in out:
                out[key] = depth + 1
                stack.append((trel, tqn, depth + 1))
    return out


def entry_points(facts: dict) -> list[dict]:
    """Entry points: framework-registered callables, then `main`.

    Tests are never entries. A fixture with a decorator is not a flow of the system, and a
    seed is a proposal someone may take literally — `tests/conftest.py::fuse_server` under a
    `fork` gate had the plan telling the orchestrator to read test files as shared sources.
    """
    entries: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for rel, blob in sorted(facts.items()):
        if is_test_path(rel):
            continue
        for target, name in _main_targets(rel, blob or {}, facts):
            if (target, name) not in seen and not is_test_path(target):
                seen.add((target, name))
                entries.append({"file": target, "name": name, "kind": "main_guard", "decorator": None})
    for rel, blob in sorted(facts.items()):
        if is_test_path(rel):
            continue
        blob = blob or {}
        scripts = set(blob.get("script_entries") or [])
        for fn in blob.get("functions") or []:
            if (rel, fn["name"]) in seen:
                continue
            deco = _decorator(fn)
            if deco:
                entries.append({"file": rel, "name": fn["name"], "kind": "decorator", "decorator": deco})
            elif fn["name"] in scripts:
                entries.append({"file": rel, "name": fn["name"], "kind": "script", "decorator": None})
            elif fn["name"] in {"main", "__main__"}:
                entries.append({"file": rel, "name": fn["name"], "kind": "main", "decorator": None})
        for cls in blob.get("classes") or []:
            for m in cls.get("methods") or []:
                deco = _decorator(m)
                if deco:
                    entries.append({
                        "file": rel, "name": f"{cls['name']}.{m['name']}",
                        "kind": "decorator", "decorator": deco,
                    })
    entries.sort(key=lambda e: (e["file"], e["name"]))
    return entries


def _main_targets(rel: str, blob: dict, facts: dict) -> list[tuple[str, str]]:
    """Functions run by `python -m` or `python file.py`: called under `if __name__ == "__main__":`
    (or at the top of a `__main__.py`), in this file or imported into it."""
    local = {fn["name"] for fn in blob.get("functions") or []}
    found = []
    for name in blob.get("main_calls") or []:
        if name in local:
            found.append((rel, name))
            continue
        for imp in blob.get("imports") or []:
            target = imp.get("target")
            defined = {fn["name"] for fn in (facts.get(target) or {}).get("functions") or []}
            original = (imp.get("aliases") or {}).get(name, name)  # `from .cli import main as entry`
            if original in (imp.get("names") or []) and target in facts and original in defined:
                found.append((target, original))
    return found


def _decorator(item: dict) -> str | None:
    for decorator in item.get("decorators") or []:
        bare = decorator.lstrip("@").split("(")[0].strip()
        if bare.split(".")[-1] in ENTRY_DECORATORS:
            return bare
    return None
