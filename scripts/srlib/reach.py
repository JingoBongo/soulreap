"""Reach: what a reader needs first, measured from what the repository runs.

Run roots (`runroots`, else `default_roots`) are where reading starts; the import graph says
what they pull in and how far away each file is; and the call lists say which part of each
file the nearer code actually uses. `rank` turns this into the read set.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from . import flow as _flow
from .paths import is_test_path

# Without deploy files, a `__main__` guard here is a demo, an eval or an old prototype — a root
# only when nothing else in the repo runs (soulreap-poc's poc/ outranked its own CLI).
AUX_DIRS = frozenset({"poc", "evals", "eval", "examples", "example", "benchmarks", "bench", "demo", "demos",
                      "sandbox", "experiments", "playground", "docs", "doc"})
# How a Python app's entry file is usually named, when nothing declares how it runs (multi-ratio:
# a FastAPI `main.py` started by hand, no guard, no deploy file).
ENTRY_NAMES = ("main.py", "app.py", "__main__.py", "manage.py", "wsgi.py", "asgi.py", "server.py", "cli.py")


def _symbols(blob: dict):
    for fn in blob.get("functions") or []:
        yield fn["name"], fn
    for cls in blob.get("classes") or []:
        for method in cls.get("methods") or []:
            yield f"{cls['name']}.{method['name']}", method


def default_roots(facts: dict, fallback: bool = True) -> list[str]:
    """Run roots when no deploy file names any, strongest first: files with a `__main__` guard or
    console script outside demo/eval/prototype folders; else (*fallback*) conventionally named
    entry files (`main.py`, `app.py`, `manage.py`, …); else the demo/eval ones after all."""
    def aux(rel: str) -> bool:
        return bool(AUX_DIRS & set(rel.split("/")[:-1]))

    entries = [e for e in _flow.entry_points(facts) if e["kind"] in ("main_guard", "script")]
    primary = sorted({e["file"] for e in entries if not aux(e["file"])})
    if primary or not fallback:
        return primary
    named = sorted((rel for rel in facts if PurePosixPath(rel).name in ENTRY_NAMES and not aux(rel)
                    and not is_test_path(rel) and (facts[rel] or {}).get("lang") == "python"),
                   key=lambda rel: (rel.count("/"), rel))
    return named or sorted({e["file"] for e in entries})


def linked(facts: dict, links: dict[str, list[str]]) -> dict:
    """*facts* with each root's config-loaded modules (`runroots.config_links`) as extra deps."""
    if not links:
        return facts
    out = dict(facts)
    for owner, targets in links.items():
        targets = [t for t in targets if t in out]
        out[owner] = {**out[owner], "deps": sorted(set(out[owner].get("deps") or []) | set(targets))}
        for target in targets:
            out[target] = {**out[target], "imported_by": sorted(set(out[target].get("imported_by") or []) | {owner}),
                           "config_loaded": True}
    return out


def _reach_order(facts: dict, pool: list[str], roots: list[str],
                 scores: dict[str, float]) -> tuple[list[str], dict[str, int]]:
    """Pool files in the order a reader needs them: what the roots import, nearest first.

    Breadth-first over the import graph from every root at once; at each depth the roots take
    turns, so four deployed services each get their entry and first imports before any one of
    them gets its third level. A module the root loads through config (Dagster's defs.yaml) is
    what that root is for, so it leads its lane. Files nothing running imports come last, by score.
    Returns the order and each reached file's depth (imports away from its root).
    """
    in_pool = set(pool)
    depth = _depths(facts, roots)
    owner = depth.pop("__owner__")
    uses = {f: _used(facts, f, nearer(facts, depth, f))[1] for f in depth if f in in_pool}
    ordered: list[str] = []
    for level in sorted(set(depth.values())):
        lanes = [sorted((f for f in depth if depth[f] == level and owner[f] == r and f in in_pool),
                        key=lambda f: (not facts[f].get("config_loaded"), -uses[f], -scores[f], f)) for r in roots]
        while any(lanes):
            for lane in lanes:
                if lane:
                    ordered.append(lane.pop(0))
    return ordered + sorted((f for f in pool if f not in depth), key=lambda f: (-scores[f], f)), depth


def nearer(facts: dict, depth: dict[str, int], rel: str) -> list[str]:
    """The files importing *rel* that are closer to what runs than it is."""
    return [c for c in (facts.get(rel) or {}).get("imported_by") or [] if depth.get(c, depth[rel]) < depth[rel]]


def _used_deps(blob: dict) -> set[str]:
    """A file's deps minus those it only re-exports in `__all__` (`facts._mark_reexports`)."""
    imports = blob.get("imports") or []
    reexported = {i.get("target") for i in imports if i.get("reexport")}
    reexported -= {i.get("target") for i in imports if not i.get("reexport")}
    return set(blob.get("deps") or []) - reexported


def _depths(facts: dict, roots: list[str]) -> dict:
    """Imports away from the nearest root, for every file the roots reach (and `__owner__`: which root).

    Re-export-only edges (`_used_deps`) are followed last: they never make a file nearer than its
    real users do, but a facade (`api.py` re-exporting `impl.handler`) still reaches what it exports.
    """
    depth = {r: 0 for r in roots}
    owner = {r: r for r in roots}

    def spread(frontier: list[str], deps_of) -> None:
        while frontier:
            nxt = []
            for rel in frontier:  # through every file, even trivial re-exporting __init__s
                for dep in sorted(deps_of(facts.get(rel) or {})):
                    if dep not in depth and dep in facts:
                        depth[dep], owner[dep] = depth[rel] + 1, owner[rel]
                        nxt.append(dep)
            frontier = nxt

    spread(list(roots), _used_deps)
    spread(sorted(depth, key=lambda f: (depth[f], f)), lambda blob: blob.get("deps") or [])
    return {**depth, "__owner__": owner}


def _used(facts: dict, rel: str, callers: list[str]) -> tuple[list[list], int]:
    """Ranges of *rel* that *callers* (nearer files importing it) actually call, followed through
    *rel*'s own helpers; and how many distinct symbols the callers call directly.

    Names are matched only from files that import *rel*, so `load` in one module does not pull
    in every other module's `load`. A called class brings its whole body.
    """
    called = {name for c in callers if rel in ((facts.get(c) or {}).get("deps") or [])
              for _q, fn in _symbols(facts.get(c) or {}) for name in fn.get("callees") or []}
    blob = facts.get(rel) or {}
    spans = {q: (fn["start_line"], fn["end_line"], fn.get("callees") or []) for q, fn in _symbols(blob)}
    for cls in blob.get("classes") or []:
        spans[cls["name"]] = (cls["start_line"], cls["end_line"], [])
    by_name: dict[str, list[str]] = {}
    for qual in spans:
        by_name.setdefault(qual.split(".")[-1], []).append(qual)
    direct = {q for name in called for q in by_name.get(name, [])}
    # Routes and tasks a reached module registers are how the running app uses it: `main.py`
    # mounts `routes.router`, it never calls `list_workstreams` by name.
    direct |= {e["name"] for e in _flow.entry_points({rel: blob}) if e["kind"] == "decorator" and e["name"] in spans}
    picked, todo = set(direct), list(direct)
    while todo:  # the file's own helpers, as far as its own calls go
        for name in spans[todo.pop()][2]:
            for qual in by_name.get(name, []):
                if qual not in picked:
                    picked.add(qual)
                    todo.append(qual)
    inside = {q for q in picked for o in picked if o != q and spans[o][0] <= spans[q][0] and spans[q][1] <= spans[o][1]}
    ranges = sorted([spans[q][0], spans[q][1], q] for q in picked - inside)
    return ranges, len(direct)


def _fit(ranges: list[list], allot: int) -> list[list]:
    """Ranges in file order, kept while they fit *allot*; the first one is cut to fit if it must."""
    picked, used = [], 0
    for start, end, qual in ranges:
        size = end - start + 1
        if used + size > allot:
            if picked:
                continue
            end, size = start + allot - 1, allot
        picked.append([start, end, qual])
        used += size
    return picked
