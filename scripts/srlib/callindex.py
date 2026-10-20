"""Every call in the repo resolved once: who each symbol calls, and who calls it.

`symbols.callers` answers for one key and walks every body to do it; `delta` and `slice` ask
about many keys, in both directions, so they build this once instead. Keys are the flow index's
`file::Qual`, the same addresses `symbol_table` uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import flow as _flow


@dataclass
class CallIndex:
    """Resolved call edges, both ways, plus the bare names each body calls (resolved or not)."""

    callees: dict[str, set[str]] = field(default_factory=dict)
    callers: dict[str, list[dict]] = field(default_factory=dict)
    names: dict[str, set[str]] = field(default_factory=dict)


def call_index(idx: _flow.FlowIndex) -> CallIndex:
    """Resolve every call site in *idx* once; `names` keeps the last dotted segment of each call,
    so code calling something that no longer exists still says what it calls."""
    out = CallIndex()
    for scan_key, scan in idx.scans.items():
        src, qual = scan_key.split("::", 1)
        cls = idx.defs[src][qual][3]
        out.callees.setdefault(scan_key, set())
        out.names.setdefault(scan_key, set())
        seen: set[tuple[str, int]] = set()
        for name, line, _text in scan.calls:
            out.names[scan_key].add(name.split(".")[-1])
            hit = _flow.resolve(idx, src, name, scan.vartypes, cls)
            if not hit:
                continue
            target = f"{hit[0]}::{hit[1]}"
            out.callees[scan_key].add(target)
            if (target, line) not in seen:
                seen.add((target, line))
                out.callers.setdefault(target, []).append({"caller": scan_key, "line": line, "confidence": hit[2]})
    for sites in out.callers.values():
        sites.sort(key=lambda c: (c["caller"], c["line"]))
    return out
