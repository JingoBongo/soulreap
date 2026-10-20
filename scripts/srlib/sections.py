"""Per-section staleness for the map's prose.

Each section that cites symbols ends with a basis line recording, for each cited
symbol, the fingerprints it was written against — structural, then body after a dot:

    <!-- basis@a1b2c3d: mercy.py::check_cycle=3fa1c09e2b.77c0e1a2f4 -->

`stamp` compares that to the current facts. A moved structural fingerprint names what
moved (signature, callees, hazards, literals); a moved body fingerprint alone says
`body changed`; a deleted symbol says so. Each puts a banner under the heading. The
basis is per anchor: a newly cited symbol is stamped as it appears, an already-cited
one keeps its recorded value (a pair stamped later carries its own commit, `=value@c2`),
removing the anchor of a stale claim leaves it stale, and only deleting the basis line
or `confirm` re-stamps the whole section. Fingerprints ignore lines, comments and docstrings, so formatting
never stales prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .anchors import FENCE, find_anchors

HEADING = re.compile(r"^#{1,6} ")
BASIS = re.compile(r"^<!-- basis@(?P<commit>\S+): (?P<pairs>.*?) -->\s*$")
BANNER = "> ⚠ stale"
BLOCK_OPEN = re.compile(r"^<!-- soulreap:\w+ -->")
BLOCK_CLOSE = re.compile(r"^<!-- /soulreap:\w+ -->")


@dataclass
class StampReport:
    """What one `stamp` pass found and did."""

    stale: list[dict] = field(default_factory=list)
    stamped: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def _split(text: str) -> tuple[list[str], list[list[str]]]:
    """Sections by heading — but a `#` line inside a code fence (a shell comment) or inside a
    generated `<!-- soulreap:x -->` block (render rewrites those, banner and all) is body text
    of the section around it, never a section of its own."""
    lines = text.split("\n")
    preamble: list[str] = []
    sections: list[list[str]] = []
    in_fence = in_block = False
    for line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
        elif BLOCK_OPEN.match(line):
            in_block = True
        elif BLOCK_CLOSE.match(line):
            in_block = False
        if HEADING.match(line) and not in_fence and not in_block:
            sections.append([line])
        elif sections:
            sections[-1].append(line)
        else:
            preamble.append(line)
    return preamble, sections


def _title(section: list[str]) -> str:
    return section[0].lstrip("#").strip()


def _parts(section: list[str]) -> tuple[list[str], dict | None]:
    """Body without banner and basis lines (trailing blanks trimmed), plus the parsed basis."""
    basis = None
    body = [section[0]]
    for line in section[1:]:
        match = BASIS.match(line)
        if match:
            pairs, commits = {}, {}
            for item in match["pairs"].split():
                key, _, value = item.partition("=")
                pairs[key], _, at = value.partition("@")  # `key=value@commit`: stamped later than the line
                commits[key] = at or match["commit"]
            basis = {"commit": match["commit"], "pairs": pairs, "commits": commits}
        elif not line.startswith(BANNER):
            body.append(line)
    while len(body) > 1 and not body[-1].strip():
        body.pop()
    return body, basis


def snapshot(row: dict) -> dict:
    return {
        "signature": row.get("signature", ""),
        "callees": sorted(row.get("callees") or []),
        "hazards": sorted(({"kind": h["kind"], "what": h["what"]} for h in row.get("hazards") or []),
                          key=lambda h: (h["kind"], h["what"])),
        "literals": sorted(f"{lit['kind']} {lit['text']}" for lit in row.get("literals") or []),
    }


def changes(old: dict | None, row: dict | None) -> list[str]:
    """Human description of how a cited symbol moved since its snapshot."""
    if row is None:
        return ["deleted"]
    if old is None:
        return ["changed"]
    new = snapshot(row)
    out = ["signature changed"] if old["signature"] != new["signature"] else []
    out += [f"+callee {c}" for c in sorted(set(new["callees"]) - set(old["callees"]))]
    out += [f"-callee {c}" for c in sorted(set(old["callees"]) - set(new["callees"]))]
    before = {(h["kind"], h["what"]) for h in old["hazards"]}
    after = {(h["kind"], h["what"]) for h in new["hazards"]}
    out += [f"+{k} {w}" for k, w in sorted(after - before)]
    out += [f"-{k} {w}" for k, w in sorted(before - after)]
    old_lit, new_lit = set(old.get("literals") or []), set(new["literals"])
    out += [f"+{lit}" for lit in sorted(new_lit - old_lit)]
    out += [f"-{lit}" for lit in sorted(old_lit - new_lit)]
    return out or ["changed"]


def _value(row: dict) -> str:
    """What a basis records for one symbol: `sfp`, or `sfp.bfp` when a body fingerprint exists."""
    return f"{row['sfp']}.{row['bfp']}" if row.get("bfp") else row["sfp"]


def _moved(key: str, recorded: str, table: dict[str, dict], snapshots: dict) -> list[str] | None:
    """How *key* moved since *recorded*; None when it did not. Structure first, then the soft tier."""
    row = table.get(key)
    if row is None:
        return ["deleted"]
    sfp, _, bfp = recorded.partition(".")
    if row["sfp"] != sfp:
        return changes(snapshots.get(f"{key}@{recorded}"), row)
    if bfp and row.get("bfp") and row["bfp"] != bfp:
        return ["body changed"]
    return None


def _basis_line(commit: str, pairs: dict[str, str], commits: dict[str, str] | None = None) -> str:
    """`<!-- basis@c1: key=value ... -->`; a pair stamped at another commit carries it as `value@c2`."""
    commits = commits or {}
    return f"<!-- basis@{commit}: " + " ".join(
        f"{k}={v}" + (f"@{commits[k]}" if commits.get(k, commit) != commit else "") for k, v in pairs.items()
    ) + " -->"


def stamp(text: str, table: dict[str, dict], snapshots: dict, commit: str, doc: str,
          force: frozenset[str] = frozenset()) -> tuple[str, StampReport]:
    """Re-stamp new or rewritten sections, banner stale ones, clear banners that no longer apply."""
    report = StampReport()
    preamble, sections = _split(text)
    rebuilt: list[list[str]] = []
    for section in sections:
        body, basis = _parts(section)
        cited = list(dict.fromkeys(a.key for a in find_anchors("\n".join(body[1:]))))
        title = _title(section)
        if not cited:
            rebuilt.append([*body, ""])
            continue
        known = [key for key in cited if key in table]
        report.unresolved += [key for key in cited if key not in table]
        if basis is None or title in force:
            pairs = {key: _value(table[key]) for key in known}
            for key, sfp in pairs.items():
                snapshots[f"{key}@{sfp}"] = snapshot(table[key])
            rebuilt.append([*body, _basis_line(commit, pairs), ""])
            report.stamped.append(f"{doc}#{title}")
            continue
        # The basis is per anchor: a newly cited symbol is stamped now, an already-cited one keeps the
        # value it was written against — adding a citation never clears a stale claim, and an
        # unresolved citation (in neither the table nor the basis) changes nothing. Only deleting the
        # basis line (a rewrite) or `confirm` re-stamps the whole section.
        recorded, commits = basis["pairs"], dict(basis["commits"])
        added = {key: _value(table[key]) for key in known if key not in recorded}
        for key, value in added.items():
            snapshots[f"{key}@{value}"] = snapshot(table[key])
            commits[key] = commit
        pairs = {key: recorded.get(key) or added[key] for key in cited if key in recorded or key in added}
        if added:
            report.stamped.append(f"{doc}#{title}")
        moved = {}
        for key, value in pairs.items():
            what = _moved(key, value, table, snapshots)
            if what:
                moved[key] = what
        # Deleting the anchor of a stale claim does not make the claim true: the pair stays until a
        # rewrite or `confirm`. A symbol deleted from the code whose anchor went too is the fix itself.
        for key, value in recorded.items():
            if key not in pairs and key in table:
                what = _moved(key, value, table, snapshots)
                if what:
                    pairs[key], moved[key] = value, [*what, "anchor removed"]
        tail = [_basis_line(basis["commit"], pairs, commits), ""]
        if moved:
            since = ", ".join(sorted({commits.get(key, basis["commit"]) for key in moved}))
            banner = f"{BANNER} (basis {since}): " + "; ".join(
                f"{key.split('::', 1)[1]}: {', '.join(what)}" for key, what in moved.items()
            )
            rebuilt.append([body[0], banner, *body[1:], *tail])
            report.stale.append({"doc": doc, "section": title, "changes": moved})
        else:
            rebuilt.append(body + tail)
    out = "\n".join(preamble + [line for section in rebuilt for line in section])
    return out.rstrip("\n") + "\n", report


def basis_keys(text: str) -> set[str]:
    """Every `key@sfp` recorded in *text*'s basis lines."""
    found: set[str] = set()
    for line in text.split("\n"):
        match = BASIS.match(line)
        if match:
            found |= {pair.split("@", 1)[0].replace("=", "@", 1) for pair in match["pairs"].split()}
    return found


def section_status(text: str) -> dict[str, dict]:
    """Heading → {stale, cites} for every section in *text*."""
    _, sections = _split(text)
    return {
        _title(s): {
            "stale": any(line.startswith(BANNER) for line in s[1:]),
            "cites": list(dict.fromkeys(a.key for a in find_anchors("\n".join(s[1:])))),
        }
        for s in sections
    }


def heading_count(text: str, heading: str) -> int:
    """How many sections of *text* carry this title — `confirm` refuses an ambiguous one."""
    return sum(1 for section in _split(text)[1] if _title(section) == heading)
