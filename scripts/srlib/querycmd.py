"""Query commands over fresh facts: `flow` (cross-file chain) and `find` (where is this name).

`skeleton` lives in skeleton.py and is re-exported here.
"""

from __future__ import annotations

from pathlib import Path

from . import flow as _flow
from . import flowtext
from .freshness import fresh_facts
from .reach import _symbols
from .skeleton import cmd_skeleton  # noqa: F401  (re-exported: the query commands live together)

FLOW_BRIEF_LINES = 40  # calls, not text lines: one anchor per call


def _paragraph(fn: dict) -> str:
    """A docstring's first paragraph as one line: its first sentence often wraps."""
    lines = [fn.get("doc") or ""]
    for line in fn.get("doc_more") or []:
        if not line.strip():
            break
        lines.append(line.strip())
    return " ".join(lines).strip()


def cmd_flow(root: Path | str, out: Path | str, target: str, dot: bool = False, brief: bool = False) -> int:
    """Print the cross-file chain of one entry point — the spine of a flow card.

    *brief* is `flowtext.chain_brief`: the entry's steps always, depth while it fits
    FLOW_BRIEF_LINES — on a big repo a full chain pasted into eight cards tripled the map,
    and a plain line cap cut the entry's own later steps (mercy lost half its loop).
    """
    root, out = Path(root), Path(out)
    facts = fresh_facts(root, out)["files"]
    rel, name = target.split("::", 1) if "::" in target else (target, "main")
    index = _flow.build(root, facts)
    if dot:
        text = flowtext.chain_dot(index, rel, name)
    elif brief:
        info = {f"{f}::{qual}": {"doc": _paragraph(fn), "hazards": fn.get("hazards"),
                                  "lines": fn["end_line"] - fn["start_line"] + 1}
                for f, blob in facts.items() for qual, fn in _symbols(blob or {})}
        text = flowtext.chain_brief(index, rel, name, FLOW_BRIEF_LINES, info)
    else:
        text = flowtext.chain_text(index, rel, name)
    print(text)
    return 0


def cmd_find(root: Path | str, out: Path | str, query: str, limit: int = 40) -> int:
    """Where is this name — callable, constant, class, store, table or file."""
    root, out = Path(root), Path(out)
    facts = fresh_facts(root, out)["files"]
    needle = query.lower()
    hits: list[str] = []
    for rel in sorted(facts):
        blob = facts[rel] or {}
        for fn in blob.get("functions") or []:
            if needle in fn["name"].lower():
                hits.append(f"{rel}:{fn['start_line']}  func {fn.get('signature') or fn['name']}")
        for cls in blob.get("classes") or []:
            if needle in cls["name"].lower():
                hits.append(f"{rel}:{cls['start_line']}  class {cls['name']}")
            for m in cls.get("methods") or []:
                if needle in m["name"].lower():
                    hits.append(f"{rel}:{m['start_line']}  method {cls['name']}.{m['name']}")
        for const in blob.get("constants") or []:
            if needle in const["name"].lower():
                hits.append(f"{rel}:{const['line']}  const {const['name']}")
        for store in blob.get("stores") or []:
            text = store.get("name", "") if isinstance(store, dict) else str(store)
            if needle in str(text).lower():
                hits.append(f"{rel}  store {text}")
        if needle in rel.lower():
            hits.append(f"{rel}  file")
    for line in hits[:limit]:
        print(line)
    tail = f", showing {limit}" if len(hits) > limit else ""
    print(f"# {len(hits)} match(es) for {query!r}{tail}")
    return 0
