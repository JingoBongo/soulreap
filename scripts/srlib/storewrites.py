"""Writes to a detected store: `vault.put(key, value)` is a write, though no hazard says so.

The hazard pass records assignments and in-place mutators (`append`, `pop`, …) — what a
store client does with its own verbs is a call, not a write, so `writes remote_repos_vault`
on fuse_core listed 2 symbols while the code had 17 `.put(` sites. The stores themselves
are already detected (stores.py: a named binding both read and written); this joins them
to the call sites the flow index already parses.

Resolved here, at query time, rather than as extraction hazards on purpose: a new hazard
changes the fingerprint of every function that calls `put`, so every map on disk would go
stale at once, and each call site would become one more checklist item to cite.
"""

from __future__ import annotations

from pathlib import Path

from . import flow as _flow
from .stores_fields import DROP, WRITE

# `pop` reads, but it also removes the key: a remover is a writer of the store.
STORE_WRITE_VERBS = frozenset(WRITE | DROP | {"pop"})
# A client's own wipe verbs vary (`delete_vault`, `drop_table`, `clear_all`): matched by prefix.
REMOVER_PREFIXES = ("delete_", "drop_", "purge_", "clear", "remove_", "flush")


def is_store_write(verb: str) -> bool:
    """A store method that adds, changes or removes what the store keeps."""
    return verb in STORE_WRITE_VERBS or verb.startswith(REMOVER_PREFIXES)


def _own_bindings(facts: dict) -> dict[str, dict[str, str]]:
    """file -> {binding: store name} for the stores each file declares."""
    return {
        rel: {s["binding"]: s["name"] for s in (blob or {}).get("stores") or [] if s.get("binding")}
        for rel, blob in facts.items()
        if (blob or {}).get("stores")
    }


def visible_bindings(facts: dict) -> dict[str, dict[str, str]]:
    """file -> {name as written in that file: store name}: its own stores, plus the ones it
    imports by name (`from app import vault`, `as v` included) or reaches through a module
    (`import app` → `app.vault`)."""
    own = _own_bindings(facts)
    seen: dict[str, dict[str, str]] = {}
    for rel, blob in facts.items():
        names = dict(own.get(rel, {}))
        for imp in (blob or {}).get("imports") or []:
            target = own.get(imp.get("target") or "")
            if not target:
                continue
            listed = imp.get("names") or []
            if listed:
                aliases = {orig: alias for alias, orig in (imp.get("aliases") or {}).items()}
                for name in listed:
                    if name in target:
                        names[aliases.get(name, name)] = target[name]
            else:
                module = imp["module"].split(".")[-1]
                for binding, store in target.items():
                    if not binding.startswith("self."):
                        names[f"{module}.{binding}"] = store
        if names:
            seen[rel] = names
    return seen


def store_writes(root: Path | str, facts: dict, idx=None) -> list[dict]:
    """Every call site that writes or removes through a store's own verbs.

    A nested function's call is reported on the top-level function or method enclosing
    it — the symbol a query or a citation can name.
    """
    visible = visible_bindings(facts)
    if not visible:
        return []
    idx = idx or _flow.build(Path(root), facts)
    found = []
    for scan_key, scan in idx.scans.items():
        names = visible.get(scan_key.split("::", 1)[0])
        if not names:
            continue
        for call, line, _text in scan.calls:
            binding, _, verb = call.rpartition(".")
            if binding in names and is_store_write(verb):
                found.append({"symbol": scan_key, "line": line, "what": f"{binding}.{verb}",
                              "store": names[binding], "binding": binding})
    return sorted(found, key=lambda w: (w["symbol"], w["line"]))


def names_store(hit: dict, name: str) -> bool:
    """Does *name* — a binding, an imported alias, or the store's own name — pick this hit?"""
    binding = hit["binding"]
    if name in (binding, hit["store"]):
        return True
    return not name.startswith("self.") and binding.endswith(f".{name}")


def merged_writes(root: Path | str, facts: dict, hazard_hits: list[dict], name: str) -> list[dict]:
    """`writes` hazards plus store-verb call sites for *name*, one row per (symbol, line)."""
    rows = {(h["symbol"], h["line"]): h for h in hazard_hits}
    for hit in store_writes(root, facts):
        if names_store(hit, name):
            rows.setdefault((hit["symbol"], hit["line"]), hit)
    return sorted(rows.values(), key=lambda w: (w["symbol"], w["line"]))
