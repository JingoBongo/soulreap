"""Which cards a map gets, what the reader is pointed at, and the one card the reader may swap.

What runs gets the first cards (each run root, then what a root loads through config). The rest
go to what carries the repo by the importance score (`importance.py`, fitted on 95 repos read
whole) — one card per file first, a file registering ROUTE_GROUP routes as one router card, a
method standing for its class (the store, the handler — not `__init__` or `do_GET`). The
next few by the same score are the shortlist: after reading, the reader may swap one non-root
card for one of them (`swap`), because the score and a reader disagree in different places.
"""

from __future__ import annotations

from . import flow as _flow
from .reach import _symbols

# A file registering this many routes/tasks is one card (a router), not one card per route —
# nvm-forecast-app's eight cards were eight admin endpoints of one file.
ROUTE_GROUP = 3
SHORTLIST = 5
LOOK_HERE = 10
EXPLAINED_DEPTH = 2  # what a flow card explains: its entry and the steps `flow --brief` shows
REDUNDANT = 0.8  # a candidate this much inside what earlier cards explain waits its turn


def _root_topic(facts: dict, root: dict, covered) -> dict:
    """What a run root's card is about: the function it starts, else the file as a component."""
    rel, blob = root["file"], facts.get(root["file"]) or {}
    names = {name for name, _fn in _symbols(blob)}
    if root.get("symbol") in names and covered(rel, root["symbol"]):
        return {"kind": "flow", "target": f"{rel}::{root['symbol']}"}
    for target, name in _flow._main_targets(rel, blob, facts):  # `__main__.py` → cli.main
        if covered(target, name):
            return {"kind": "flow", "target": f"{target}::{name}"}
    return {"kind": "component", "target": rel}


def _coverage(facts: dict, read: dict):
    ranges = {entry["file"]: entry["ranges"] for entry in read["files"]}
    starts = {rel: {qual: fn["start_line"] for qual, fn in _symbols(facts.get(rel) or {})}
              for rel, spans in ranges.items() if spans is not None}

    def covered(rel: str, name: str) -> bool:
        """A flow card needs its entry's code in the read set — a whole file, or a range holding it."""
        if rel not in ranges:
            return False
        if ranges[rel] is None:
            return True
        start = starts[rel].get(name)
        return start is not None and any(s <= start <= end for s, end, _ in ranges[rel])

    return covered


def _routers(facts: dict, covered) -> set[str]:
    per_file: dict[str, int] = {}
    for e in _flow.entry_points(facts):
        if e["kind"] == "decorator" and covered(e["file"], e["name"]):
            per_file[e["file"]] = per_file.get(e["file"], 0) + 1
    return {rel for rel, n in per_file.items() if n >= ROUTE_GROUP}


def ranked(facts: dict, read: dict, scores: dict[str, dict]) -> list[str]:
    """Every symbol whose code the reader reads, most important first (`file::Qual`)."""
    covered = _coverage(facts, read)
    keys = [f"{entry['file']}::{qual}" for entry in read["files"]
            for qual, _fn in _symbols(facts.get(entry["file"]) or {}) if covered(entry["file"], qual)]
    return sorted(keys, key=lambda key: (-(scores.get(key) or {}).get("council", 0.0), key))


def explainer(root, facts: dict):
    """What a card explains, as {(file, qual)}: a flow card its entry and the steps two calls deep
    (what `flow --brief` shows); a component card the file's own callables."""
    idx = _flow.build(root, facts)

    def explains(topic: dict) -> set[tuple[str, str]]:
        rel, _, name = topic["target"].partition("::")
        if not name:
            return {(rel, qual) for qual, _fn in _symbols(facts.get(rel) or {})}
        if topic["kind"] == "component":  # a class card: its methods
            return {(rel, qual) for qual, _fn in _symbols(facts.get(rel) or {}) if qual.startswith(f"{name}.")}
        if name not in idx.defs.get(rel, {}):
            return {(rel, name)}
        return set(_flow.closure(idx, rel, name, max_depth=EXPLAINED_DEPTH))

    return explains


def choose(facts: dict, read: dict, k: int, scores: dict[str, dict], roots: list[dict] | None = None,
           explains=None) -> dict:
    """{topics, fixed, shortlist}: *k* card subjects (the first `fixed` are what runs and cannot be
    swapped), and the next SHORTLIST by importance the reader may swap one card for.

    With *explains* (`explainer`), a candidate whose chain the cards already picked explain
    (REDUNDANT of its lines) waits until nothing else is left: a slot spent twice explains nothing."""
    covered = _coverage(facts, read)
    in_read = [entry["file"] for entry in read["files"]]
    picked: list[dict] = []
    for item in roots or []:
        if item["file"] in in_read:
            topic = _root_topic(facts, item, covered)
            if topic not in picked:
                picked.append(topic)
    # What a root loads through config (Dagster's component) is that service's real work.
    picked += [t for t in ({"kind": "component", "target": rel} for rel in in_read if facts[rel].get("config_loaded"))
               if t not in picked]
    picked = picked[:k]
    fixed = len(picked)
    routers = _routers(facts, covered)
    stream: list[dict] = []
    for key in ranked(facts, read, scores):
        rel = key.split("::")[0]
        qual = key.split("::", 1)[1]
        if rel in routers:
            topic = {"kind": "component", "target": rel}
        elif "." in qual:  # a method's card is about its class: the unit a reader needs, not one method
            topic = {"kind": "component", "target": f"{rel}::{qual.split('.')[0]}"}
        else:
            topic = {"kind": "flow", "target": key}
        if topic not in stream and topic not in picked:
            stream.append(topic)
    sizes = {(rel, qual): fn["end_line"] - fn["start_line"] + 1
             for rel, blob in facts.items() for qual, fn in _symbols(blob or {})}
    lines = lambda keys: sum(sizes.get(key, 0) for key in keys)  # noqa: E731
    memo: dict[str, set] = {}

    def explained(topic: dict) -> set:
        if explains is None:
            return set()
        if topic["target"] not in memo:
            memo[topic["target"]] = explains(topic)
        return memo[topic["target"]]

    def redundant(topic: dict) -> bool:
        own = explained(topic)
        return bool(own) and lines(own & union) >= REDUNDANT * lines(own) > 0

    union: set = set().union(*(explained(t) for t in picked))
    carded = {t["target"].split("::")[0] for t in picked}
    # one card per file, not explained twice; then a second per file; then whatever is left
    for one_per_file, fresh_only in ((True, True), (False, True), (False, False)):
        for topic in stream:
            if len(picked) >= k:
                break
            rel = topic["target"].split("::")[0]
            if topic in picked or (one_per_file and rel in carded):
                continue
            if fresh_only and redundant(topic):
                continue
            picked.append(topic)
            carded.add(rel)
            union |= explained(topic)
    rest = [t for t in stream if t not in picked]  # what the cards do not already explain, first
    shortlist = ([t for t in rest if not redundant(t)] + [t for t in rest if redundant(t)])[:SHORTLIST]
    return {"topics": picked, "fixed": fixed, "shortlist": shortlist}


def look_here(facts: dict, read: dict, scores: dict[str, dict], n: int = LOOK_HERE) -> list[dict]:
    """The *n* functions that carry the repo, with the reasons the score saw."""
    return [{"symbol": key, "reasons": (scores.get(key) or {}).get("reasons") or []}
            for key in ranked(facts, read, scores)[:n]]
