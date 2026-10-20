"""`impact`: what a change to a file or symbol reaches, including the map prose citing it."""

from __future__ import annotations

from pathlib import Path

from . import flow as _flow
from .paths import is_test_path
from .sections import section_status
from .storewrites import store_writes
from .symbols import file_symbols, resolve_symbol, symbol_table

# What only a current OVERVIEW has: an older skill's OVERVIEW is left alone and never counts as citing.
OVERVIEW_MARK = "\n## Not covered"


def is_current_overview(path: Path) -> bool:
    """True when *path* is an OVERVIEW this skill wrote (it has `## Not covered`)."""
    return path.exists() and OVERVIEW_MARK in path.read_text(encoding="utf-8")


def map_documents(out: Path | str) -> list[Path]:
    """The map's prose documents: a current OVERVIEW.md, then every card. Plan, status and render
    all read this list, so they agree on what the map cites."""
    out = Path(out)
    docs = [out / "OVERVIEW.md"] if is_current_overview(out / "OVERVIEW.md") else []
    return docs + sorted((out / "cards").glob("*.md"))


def citing(out: Path | str, key: str) -> list[dict]:
    """Every map section citing *key*, with its staleness."""
    out = Path(out)
    found = []
    for doc in map_documents(out):
        for title, info in section_status(doc.read_text(encoding="utf-8")).items():
            if key in info["cites"]:
                found.append({"doc": doc.relative_to(out).as_posix(), "section": title,
                              "status": "stale" if info["stale"] else "fresh"})
    return found


def impact(root: Path | str, out: Path | str, facts: dict, target: str) -> dict:
    """Dependents, tests, entry points, writes and citing sections for a file or symbol."""
    table = symbol_table(facts)
    if target in facts:
        keys = file_symbols(table, target)
        files = {target}
    else:
        keys = resolve_symbol(table, target)
        if len(keys) != 1:
            raise LookupError(keys)
        files = {table[keys[0]]["file"]}
    reached: set[str] = set()
    stack = list(files)
    while stack:
        for importer in (facts.get(stack.pop()) or {}).get("imported_by") or []:
            if importer not in reached and importer not in files:
                reached.add(importer)
                stack.append(importer)
    idx = _flow.build(Path(root), facts)
    targets = {(table[k]["file"], table[k]["name"]) for k in keys}
    entries = []
    for entry in _flow.entry_points(facts):
        if entry["name"] in idx.defs.get(entry["file"], {}) and targets & set(
            _flow.closure(idx, entry["file"], entry["name"])
        ):
            entries.append(f"{entry['file']}::{entry['name']}")
    return {
        "target": target,
        "symbols": keys,
        "dependents": sorted(p for p in reached if not is_test_path(p)),
        "tests": sorted(p for p in reached if is_test_path(p)),
        "entry_points": sorted(entries),
        "writes": sorted({f"{k}: {h['what']}" for k in keys for h in table[k]["hazards"] if h["kind"] == "write"}
                         | {f"{w['symbol']}: {w['what']} (store {w['store']})"
                            for w in store_writes(root, facts, idx) if w["symbol"] in keys}),
        "sections": [item for k in keys for item in citing(out, k)],
    }


def format_impact(result: dict) -> str:
    """Plain-text rendering for the CLI."""
    def block(title: str, items: list[str]) -> list[str]:
        return [f"{title}:"] + ([f"  {item}" for item in items] or ["  (none)"])

    lines = [f"impact of {result['target']}"]
    lines += block("symbols", result["symbols"])
    lines += block("dependents (production)", result["dependents"])
    lines += block("tests importing it", result["tests"])
    lines += block("entry points that reach it", result["entry_points"])
    lines += block("state it writes", result["writes"])
    lines += block("map sections citing it",
                   [f"{s['doc']}#{s['section']} [{s['status']}]" for s in result["sections"]])
    return "\n".join(lines)
