"""What a map run reads, and what it must not leave out.

Budgeted coverage: facts cover every file for free; prose covers what this module
ranks important, under a line budget sized to one reader's context. A small repo
fits whole — reading everything once is the honest answer there. A bigger one is
read in score order, big files by their important symbols only, and everything
left over is named as not covered. The checklist is the hazards a map must cite.
"""

from __future__ import annotations

import math

from . import cards as _cards
from . import flow as _flow
from . import importance as _importance
from .facts import is_trivial
from .paths import is_test_path, matches_any
from .reach import AUX_DIRS, _fit, _reach_order, _used, default_roots, nearer  # noqa: F401

BUDGET_LINES = 3000
BIG_FILE_LINES = 400
MAX_CARDS = 8
CONFIG_FILE_LINES = 200
CONFIG_CATEGORIES = ("config", "manifest")
# Directories whose YAML/JSON is data about something else (captured API responses, fixtures,
# examples), not the repo's own configuration — eager-connection-platform's docs/connector-data
# filled every config slot with eleven of them.
NOT_CONFIG_DIRS = frozenset({"docs", "doc", "fixtures", "testdata", "test_data", "examples", "samples",
                             "data", "snapshots",
                             # an editor's or agent's own settings (more2win's .claude/settings.local.json)
                             ".claude", ".vscode", ".idea", ".cursor", ".devcontainer"})
CHECK_KINDS = ("swallow", "lock", "write", "external", "spawn")
ROUTE_GROUP = _cards.ROUTE_GROUP
# A symbol of a big file nothing nearer calls is read when it is in the repo's top quarter by
# importance (or an entry): hazard counts and raw fan-in predicted nothing in the study.
RANGE_COUNCIL = 0.75
# Past the root, a file is read where the nearer code calls it (whole when that is most of it):
# soulreap's CLI → mapplan → rank. Whole-file reads of every imported module spent the budget
# on walk.py and drawing code before plan's own ranking was reached.
WHOLE_SHARE = 0.8
# A file the running code imports is read whole up to this size: cutting a few dozen lines into
# ranges saves nothing, and its constants or types are often the point.
SMALL_WHOLE = 80


def _symbols(blob: dict):
    for fn in blob.get("functions") or []:
        yield fn["name"], fn
    for cls in blob.get("classes") or []:
        for method in cls.get("methods") or []:
            yield f"{cls['name']}.{method['name']}", method


def _checked(fn: dict) -> list[dict]:
    return [h for h in fn.get("hazards") or [] if h["kind"] in CHECK_KINDS]


def candidates(facts: dict, exclude=(), with_tests: bool = False) -> list[str]:
    """Code files a map may read: not schema-only, not tests (by default), not excluded, not trivial."""
    return sorted(
        rel for rel, blob in facts.items()
        if (blob or {}).get("lang") not in (None, "sql", "sqlite")
        and (with_tests or not is_test_path(rel))
        and not matches_any(rel, list(exclude))
        and not is_trivial(blob)
    )


def score(rel: str, blob: dict, entry_files: set[str], importance: float = 0.0) -> float:
    """How much a reader needs this file: entry, importers, state, concurrency, size, and how
    important its best symbol is (0..1, `importance.py`). Hazard and I/O counts are gone: in the
    study they did not predict what a reader needs."""
    importers = [p for p in blob.get("imported_by") or [] if not is_test_path(p)]
    value = 5.0 if rel in entry_files else 0.0
    value += 2.0 * len(importers)
    value += 3.0 if blob.get("stores") or (blob.get("schema") or {}).get("tables") else 0.0
    value += 2.0 if blob.get("concurrency") else 0.0
    value += min(2.0, (blob.get("lines") or 0) / 200)
    value += 10.0 * importance
    return round(value, 2)


def _ranges(blob: dict, entry_names: set[str], allot: int, council: dict[str, float]) -> list[list]:
    """The symbols of a big file worth reading: its entry points and its top quarter by importance
    (*council*: qual → 0..1 within the repo), most important first. Never more than *allot* lines."""
    weighted = []
    for qual, fn in _symbols(blob):
        weight = (5 if qual in entry_names else 0) + council.get(qual, 0.0)
        if qual in entry_names or council.get(qual, 0.0) >= RANGE_COUNCIL:
            weighted.append((weight, fn["start_line"], fn["end_line"], qual))
    weighted.sort(key=lambda row: (-row[0], row[1]))
    picked, used = [], 0
    for _weight, start, end, qual in weighted:
        size = end - start + 1
        if used + size > allot:
            if picked:
                continue
            end, size = start + allot - 1, allot  # one symbol bigger than the room: read its head
        picked.append([start, end, qual])
        used += size
    return sorted(picked)


def config_files(entries, exclude=(), with_tests: bool = False, cap: int = BUDGET_LINES // 10) -> list[dict]:
    """Small config and dependency files: where ports, intervals and switches actually live.

    Code alone left a map unable to say which port the UI serves on (mercy's config.yaml).
    Secrets are a different kind and never qualify; big generated files are skipped.
    """
    picked, used = [], 0
    pool = [
        e for e in entries or []
        if e.kind == "non_code" and e.category in CONFIG_CATEGORIES and 0 < e.lines <= CONFIG_FILE_LINES
        and (with_tests or not is_test_path(e.path)) and not matches_any(e.path, list(exclude))
        and not (NOT_CONFIG_DIRS | AUX_DIRS) & set(e.path.split("/")[:-1])  # evals/: an answer key
    ]
    for entry in sorted(pool, key=lambda e: (CONFIG_CATEGORIES.index(e.category), e.path)):
        if used + entry.lines > cap:
            continue
        picked.append({"file": entry.path, "lines": entry.lines})
        used += entry.lines
    return picked


def read_set(facts: dict, budget: int = BUDGET_LINES, exclude=(), with_tests: bool = False,
             entries=None, roots: list[str] | None = None, scores: dict | None = None) -> dict:
    """The files (or symbol ranges) one reader reads, under *budget* lines.

    *roots* are the files that run (`runroots.run_roots`, else `default_roots`): reading
    starts there and follows their imports. Without roots, files go in score order.
    Small config files (*entries* given) are read first and count against the budget.
    *scores* (`importance.score_symbols`, `file::Qual` → council) rank files and big-file ranges;
    without them they are computed from facts alone (no file text: no docs vote).
    """
    configs = config_files(entries, exclude, with_tests)
    config_lines = sum(c["lines"] for c in configs)
    read = _code_read_set(facts, budget - config_lines, exclude, with_tests, roots or [], scores)
    read["budget"] = budget
    read["configs"] = configs
    read["total_lines"] += config_lines
    return read


def _plan_file(facts: dict, rel: str, lines: int, room: int, depth: dict[str, int], roots: list[str],
               entries: list[dict], council: dict[str, float]) -> list[list] | None:
    """How to read one file: None (whole), ranges, or [] (not this run).

    A root, or a file nothing running imports, is read whole when it fits. A file the running
    code imports is read where the nearer code calls it (a small one whole even if only its
    constants are used). Any other big file: its entry and most important symbols.
    """
    allot = min(BIG_FILE_LINES, room)
    if not depth.get(rel) and lines <= BIG_FILE_LINES and lines <= room:
        return None
    if depth.get(rel):
        if lines <= min(SMALL_WHOLE, room):  # a few dozen lines: cutting them saves nothing
            return None
        ranges = _fit(_used(facts, rel, nearer(facts, depth, rel))[0], allot)
        if not ranges:  # reached, but used as names, not calls (settings, models, JSX components)
            if lines <= allot:
                return None
            ranges = _ranges(facts[rel], set(), allot, council) or [[1, allot, "(top of file)"]]
        return ranges
    else:
        names = {e["name"] for e in entries if e["file"] == rel}
        ranges = _ranges(facts[rel], names, allot, council)
    if not ranges and rel in roots:  # a root that is all module-level code: its top is the story
        ranges = [[1, min(lines, allot), "(top of file)"]]
    return ranges


def _code_read_set(facts: dict, budget: int, exclude, with_tests: bool, roots: list[str],
                   symbol_scores: dict | None) -> dict:
    pool = candidates(facts, exclude, with_tests)
    entries = _flow.entry_points(facts)
    entry_files = {e["file"] for e in entries}
    if symbol_scores is None:
        symbol_scores = _importance.score_symbols(None, facts, pool)
    council: dict[str, dict[str, float]] = {}
    for key, value in symbol_scores.items():
        rel, _, qual = key.partition("::")
        council.setdefault(rel, {})[qual] = value["council"]
    scores = {rel: score(rel, facts[rel], entry_files, max((council.get(rel) or {}).values(), default=0.0))
              for rel in pool}
    roots = [r for r in dict.fromkeys(roots) if r in facts]
    ranked, depth = _reach_order(facts, pool, roots, scores) if roots else (
        sorted(pool, key=lambda rel: (-scores[rel], rel)), {})
    total = sum(facts[rel].get("lines") or 0 for rel in ranked)

    def item(rel: str, lines: int, ranges) -> dict:
        return {"file": rel, "lines": lines, "ranges": ranges, "score": scores[rel]}

    if total <= budget:
        files = [item(rel, facts[rel].get("lines") or 0, None) for rel in ranked]
        return {"whole": True, "budget": budget, "total_lines": total, "files": files, "not_covered": []}
    files, used, left_out = [], 0, []
    for rel in ranked:
        lines, room = facts[rel].get("lines") or 0, budget - used
        ranges = _plan_file(facts, rel, lines, room, depth, roots, entries, council.get(rel) or {}) if room > 0 else []
        if ranges == []:
            left_out.append(rel)
            continue
        size = lines if ranges is None else sum(end - start + 1 for start, end, _ in ranges)
        if ranges is not None and size >= WHOLE_SHARE * lines and lines <= room:  # nearly all: whole
            ranges, size = None, lines
        files.append(item(rel, size, ranges))
        used += size
    return {"whole": False, "budget": budget, "total_lines": used, "files": files, "not_covered": left_out}


def checklist(facts: dict, read: dict) -> list[dict]:
    """Every CHECK_KINDS hazard on a symbol inside the read set."""
    items = []
    for entry in read["files"]:
        for qual, fn in _symbols(facts[entry["file"]]):
            if entry["ranges"] is not None and not any(
                start <= fn["start_line"] and fn["end_line"] <= end for start, end, _ in entry["ranges"]
            ):
                continue
            for hazard in _checked(fn):
                items.append({"symbol": f"{entry['file']}::{qual}", "kind": hazard["kind"],
                              "what": hazard["what"], "line": hazard["line"]})
        if entry["ranges"] is None:  # module-scope code is only "read" when the whole file is
            for hazard in facts[entry["file"]].get("module_hazards") or []:
                items.append({"symbol": f"{entry['file']}::__module__", "kind": hazard["kind"],
                              "what": hazard["what"], "line": hazard["line"]})
    return items


def card_count(read: dict) -> int:
    """K cards: one per 400 lines read, between 1 and MAX_CARDS."""
    return max(1, min(MAX_CARDS, math.ceil((read.get("total_lines") or 0) / BIG_FILE_LINES)))


def topics(facts: dict, read: dict, k: int, root=None, roots: list[dict] | None = None,
           scores: dict | None = None) -> list[dict]:
    """Proposed card subjects (`cards.choose`): each run root first, then what carries the repo."""
    if scores is None:
        scores = _importance.score_symbols(root, facts, [entry["file"] for entry in read["files"]])
    return _cards.choose(facts, read, k, scores, roots)["topics"]
