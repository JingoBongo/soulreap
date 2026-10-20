"""Which symbols changed between two fact sets, and how — the facts half of `delta`.

Symbols are paired by `file::Qual` within files whose content differs. What changed is
described the way a stale map section describes it (`sections.changes`): signature, ±callee,
±hazard, ±literal; a same-structure edit is `body changed`. A class is reported only for its own
shape (bases, fields) — its methods are reported themselves.
"""

from __future__ import annotations

from .paths import is_test_path
from .sections import changes, snapshot
from .symbols import symbol_table


def changed_files(base: dict[str, dict], head: dict[str, dict]) -> list[str]:
    """Files whose content differs, or that exist on one side only."""
    return sorted(p for p in set(base) | set(head)
                  if (base.get(p) or {}).get("sha") != (head.get(p) or {}).get("sha"))


def _class_fields(files: dict[str, dict], row: dict) -> list:
    for cls in (files.get(row["file"]) or {}).get("classes") or []:
        if cls["name"] == row["name"]:
            return [(f.get("name"), f.get("type"), f.get("default")) for f in cls.get("fields") or []]
    return []


def _what(old: dict, new: dict, base: dict[str, dict], head: dict[str, dict]) -> list[str]:
    """How *old* became *new*; empty when nothing a reader would care about moved."""
    if new["kind"] == "class":
        out = ["signature changed"] if old["signature"] != new["signature"] else []
        return out + (["fields changed"] if _class_fields(base, old) != _class_fields(head, new) else [])
    if old["sfp"] != new["sfp"]:
        return changes(snapshot(old), new)
    if old.get("bfp") and new.get("bfp") and old["bfp"] != new["bfp"]:
        return ["body changed"]
    return []


def symbol_changes(base: dict[str, dict], head: dict[str, dict]) -> list[dict]:
    """Added, removed and modified symbols in the files that differ, sorted by key."""
    files = changed_files(base, head)
    old = symbol_table({p: base[p] for p in files if p in base})
    new = symbol_table({p: head[p] for p in files if p in head})
    found = []
    for key in sorted(set(old) | set(new)):
        row = new.get(key) or old[key]
        if key not in new:
            status, what = "removed", ["deleted"]
        elif key not in old:
            status, what = "added", ["added"]
        else:
            status, what = "modified", _what(old[key], new[key], base, head)
            if not what:
                continue
        if row["kind"] == "module" and status != "modified" and not row["hazards"]:
            continue  # import-time code without hazards appearing or vanishing with its file
        found.append({"key": key, "file": row["file"], "status": status, "what": what,
                      "test": is_test_path(row["file"])})
    return found
