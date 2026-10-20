"""Every symbol in the facts, addressable as `file::Qual`, plus the callers/writes queries.

The address is the anchor format the map uses (`[[file::Qual]]`), so a query
answer, a card citation and a staleness banner all name a symbol the same way.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import flow as _flow
from .hazards import fingerprint


def _row(rel: str, name: str, fn: dict, kind: str) -> dict:
    return {
        "file": rel, "name": name, "kind": kind,
        "start": fn["start_line"], "end": fn["end_line"],
        "signature": fn.get("signature", ""), "callees": list(fn.get("callees") or []),
        "hazards": list(fn.get("hazards") or []), "literals": list(fn.get("literals") or []),
        "sfp": fn.get("sfp") or "", "bfp": fn.get("bfp") or "",
    }


def symbol_table(facts: dict) -> dict[str, dict]:
    """`file::Qual` → row, for every function, class and method in *facts*."""
    table: dict[str, dict] = {}
    for rel, blob in sorted(facts.items()):
        blob = blob or {}
        for fn in blob.get("functions") or []:
            table[f"{rel}::{fn['name']}"] = _row(rel, fn["name"], fn, "function")
        for cls in blob.get("classes") or []:
            methods = cls.get("methods") or []
            digest = hashlib.sha1(
                repr((cls.get("bases"),
                      [(fld.get("name"), fld.get("type"), fld.get("default")) for fld in cls.get("fields") or []],
                      sorted(m.get("sfp") or "" for m in methods))).encode()
            ).hexdigest()[:10]
            table[f"{rel}::{cls['name']}"] = {
                "file": rel, "name": cls["name"], "kind": "class",
                "start": cls["start_line"], "end": cls["end_line"],
                "signature": f"class {cls['name']}({', '.join(cls.get('bases') or [])})",
                "callees": [], "hazards": [], "sfp": digest,
            }
            for method in methods:
                qual = f"{cls['name']}.{method['name']}"
                table[f"{rel}::{qual}"] = _row(rel, qual, method, "method")
        # Code that runs at import, outside any function. Kept for every parsed Python file, hazards or
        # not, so a citation of it outlives its last hazard (and goes stale, rather than broken).
        if blob.get("module_hazards") or (blob.get("lang") == "python" and not blob.get("parse_error")):
            hazards = blob.get("module_hazards") or []
            table[f"{rel}::__module__"] = {
                "file": rel, "name": "__module__", "kind": "module", "start": 1, "end": blob.get("lines") or 1,
                "signature": "", "callees": [], "hazards": hazards, "literals": [],
                "sfp": fingerprint("__module__", [], hazards), "bfp": "",
            }
    return table


def file_symbols(table: dict[str, dict], rel: str) -> list[str]:
    """Keys of *rel*'s symbols — its `__module__` row only when import-time code has hazards."""
    return sorted(k for k, row in table.items()
                  if row["file"] == rel and (row["kind"] != "module" or row["hazards"]))


def resolve_symbol(table: dict[str, dict], text: str) -> list[str]:
    """Keys matching *text*: an exact key, else every key whose qualname is or ends with it."""
    if text in table:
        return [text]
    return sorted(
        key for key, row in table.items()
        if row["name"] == text or row["name"].endswith(f".{text}") or key.endswith(f"::{text}")
    )


def callers(root: Path | str, facts: dict, key: str, idx=None) -> list[dict]:
    """Every call site that the chain resolver links to *key*, with its confidence.

    Pass *idx* (a `flow.build` result) when asking about several keys: building it parses every file.
    """
    idx = idx or _flow.build(Path(root), facts)
    rel, qual = key.split("::", 1)
    found = []
    for scan_key, scan in idx.scans.items():
        src, src_qual = scan_key.split("::", 1)
        cls = idx.defs[src][src_qual][3]
        for name, line, text in scan.calls:
            hit = _flow.resolve(idx, src, name, scan.vartypes, cls)
            if hit and (hit[0], hit[1]) == (rel, qual):
                found.append({"caller": scan_key, "line": line, "text": text, "confidence": hit[2]})
    return sorted(found, key=lambda c: (c["caller"], c["line"]))


def _mentions(what: str, name: str) -> bool:
    """`self._cache`, `_cache` and `CACHE` all name what a write hazard records."""
    if name.startswith("self."):
        return what == name
    return name in [part for part in re.split(r"[\s.:\[\]]+", what) if part]


def writes(table: dict[str, dict], name: str) -> list[dict]:
    """Every symbol that writes *name*: attribute, global, container, table — or an argument of
    that name it mutates in place (`arg_write`), which is how state passed down a call chain changes."""
    found = [
        {"symbol": key, "what": hazard["what"], "line": hazard["line"]}
        for key, row in table.items()
        for hazard in row["hazards"]
        if hazard["kind"] in ("write", "arg_write") and _mentions(hazard["what"], name)
    ]
    return sorted(found, key=lambda w: (w["symbol"], w["line"]))
