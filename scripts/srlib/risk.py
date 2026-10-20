"""Risk tiers for a set of symbol changes, and the tests to run — the judgement half of `delta`.

Rules, not a score: each finding names the rule that fired and the evidence, so a reader can
disagree with one finding without distrusting the rest. HIGH means likely breakage — the pre-commit
hook blocks on it — so what a reviewer should look at (a new hazard, a changed literal) is MED.
Fan-in counts production call sites only — a test calling a helper ten times does not make it hot.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from pathlib import Path

from . import flow as _flow
from .callindex import CallIndex, call_index
from .paths import is_test_path
from .sections import snapshot
from .symbols import symbol_table

TIERS = ("HIGH", "MED", "LOW")
# A call to a builtin moving in or out of a body is not a change in what the code talks to.
BUILTINS = frozenset(dir(builtins))
RISKY = frozenset({"swallow", "lock", "spawn", "external"})
STATE = frozenset({"write", "arg_write"})
HOT = 5          # production call sites that make a body change worth a second look
GROWTH = 3       # fan-in growth: at least this many more call sites…
GROWTH_RATIO = 2  # …and at least this many times as many


@dataclass
class Side:
    """One revision, ready to query: its facts, symbols, call edges and what entry points reach."""

    root: Path
    files: dict[str, dict]
    table: dict[str, dict]
    calls: CallIndex
    reach: dict[str, str]  # key -> the first entry point (by name) whose call chain reaches it


def side(root: Path | str, files: dict[str, dict]) -> Side:
    """Build a Side; *root* must hold the sources while the Side is being built."""
    root = Path(root)
    idx = _flow.build(root, files)
    reach: dict[str, str] = {}
    for entry in sorted(_flow.entry_points(files), key=lambda e: (e["file"], e["name"])):
        if entry["name"] not in idx.defs.get(entry["file"], {}):
            continue
        for rel, qual in _flow.closure(idx, entry["file"], entry["name"]):
            reach.setdefault(f"{rel}::{qual}", f"{entry['file']}::{entry['name']}")
    return Side(root, files, symbol_table(files), call_index(idx), reach)


def _fan_in(s: Side, key: str) -> int:
    return sum(1 for c in s.calls.callers.get(key, []) if not is_test_path(c["caller"].split("::", 1)[0]))


def _short(keys: list[str], cap: int = 3) -> str:
    return ", ".join(keys[:cap]) + (f" (+{len(keys) - cap})" if len(keys) > cap else "")


def _removed(key: str, base: Side, head: Side) -> list[tuple[str, str, str]]:
    last = key.split("::", 1)[1].split(".")[-1]
    still = sorted({c["caller"] for c in base.calls.callers.get(key, [])
                    if c["caller"] != key and c["caller"] in head.table
                    and last in head.calls.names.get(c["caller"], ())})
    if still:
        return [("HIGH", "removed-still-called", f"still called by {_short(still)}")]
    return [("LOW", "removed", "no remaining callers")]


def _modified(change: dict, base: Side, head: Side, changed: set[str]) -> list[tuple[str, str, str]]:
    key, what = change["key"], change["what"]
    old, new = snapshot(base.table[key]), snapshot(head.table[key])
    found: list[tuple[str, str, str]] = []
    if "signature changed" in what:
        callers = sorted({c["caller"] for c in head.calls.callers.get(key, [])})
        other = [c for c in callers if c.split("::", 1)[0] != change["file"]]
        stale = [c for c in other if c not in changed]
        if stale:
            found.append(("HIGH", "signature-cross-file", f"callers in other files not updated: {_short(stale)}"))
        elif other:
            found.append(("MED", "signature-updated", f"callers updated in this change: {_short(other)}"))
        else:
            found.append(("MED", "signature-local", f"{len(callers)} caller(s), all in {change['file']}"))
    before = {(h["kind"], h["what"]) for h in old["hazards"]}
    added = sorted({(h["kind"], h["what"]) for h in new["hazards"]} - before)
    risky = [f"+{k} {w}" for k, w in added if k in RISKY]
    state = [f"+{k} {w}" for k, w in added if k in STATE]
    if risky and key in head.reach:
        found.append(("MED", "new-hazard-reachable", f"{'; '.join(risky)} — reached from {head.reach[key]}"))
    elif risky or state:
        found.append(("MED", "new-hazard", "; ".join(risky + state)))
    lits = [f"+{x}" for x in sorted(set(new["literals"]) - set(old["literals"]))]
    lits += [f"-{x}" for x in sorted(set(old["literals"]) - set(new["literals"]))]
    if lits:
        found.append(("MED", "literal-changed", "; ".join(lits)))
    moved = [w for w in what if w.startswith(("+callee ", "-callee ")) and w.split(" ", 1)[1] not in BUILTINS]
    if moved:
        found.append(("MED", "callees-changed", "; ".join(moved)))
    hot = _fan_in(head, key)
    if hot >= HOT:
        found.append(("MED", "hot-body", f"{hot} call sites"))
    return found or [("LOW", "body", ", ".join(what))]


def _fan_in_growth(base: Side, head: Side) -> list[dict]:
    found = []
    for key in sorted(head.calls.callers):
        if is_test_path(key.split("::", 1)[0]):
            continue
        before, after = _fan_in(base, key), _fan_in(head, key)
        if after >= before + GROWTH and after >= GROWTH_RATIO * before:
            found.append({"tier": "MED", "rule": "fan-in-grew", "key": key, "detail": f"call sites {before} → {after}"})
    return found


def assess(changes: list[dict], base: Side, head: Side) -> list[dict]:
    """Findings for *changes*, HIGH first. Test files raise none: they are listed as changes, and
    their part in the report is the tests to run."""
    findings = []
    changed = {change["key"] for change in changes}
    for change in changes:
        if change["test"]:
            continue
        if change["status"] == "added":
            hits = [("LOW", "added", "added")]
        elif change["status"] == "removed":
            hits = _removed(change["key"], base, head)
        else:
            hits = _modified(change, base, head, changed)
        for tier, rule, detail in hits:
            findings.append({"tier": tier, "rule": rule,
                             "key": change["key"], "detail": detail})
    findings += _fan_in_growth(base, head)
    return sorted(findings, key=lambda f: (TIERS.index(f["tier"]), f["key"], f["rule"]))


def suggested_tests(changes: list[dict], head: Side) -> dict[str, list[str]]:
    """Test files calling a changed symbol (direct), then test files importing a changed file."""
    direct = {c["caller"].split("::", 1)[0]
              for change in changes if change["status"] == "modified"
              for c in head.calls.callers.get(change["key"], [])
              if is_test_path(c["caller"].split("::", 1)[0])}
    stack = [change["file"] for change in changes if not change["test"]]
    reached: set[str] = set(stack)
    while stack:
        for importer in (head.files.get(stack.pop()) or {}).get("imported_by") or []:
            if importer not in reached:
                reached.add(importer)
                stack.append(importer)
    return {"direct": sorted(direct),
            "transitive": sorted(p for p in reached if is_test_path(p) and p not in direct)}
