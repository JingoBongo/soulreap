"""`slice`: the code around a symbol, packed into a token budget.

One call instead of skeleton → callers → flow → file reads. From the seeds, a breadth-first
walk over the call index in both directions (callees weigh more than callers) ranks the
neighbourhood; packing then shows seeds as source, the first hop as skeletons and the rest as
one line each, degrading an entry that does not fit, and names whatever is left on a closing
`# frontier` line so the reader knows what to ask for next.
"""

from __future__ import annotations

from pathlib import Path

from . import flow as _flow
from .callindex import CallIndex, call_index
from .paths import is_test_path
from .symbols import file_symbols, resolve_symbol, symbol_table

DEFAULT_BUDGET = 4000
MAX_HOPS = 3
WEIGHT = {"callee": 1.0, "caller": 0.8}
HAZARD_BONUS = 0.05
FRONTIER_CAP = 20
TIERS = ("source", "skeleton", "line")


def tokens(text: str) -> int:
    """Estimated tokens: ⌈chars / 4⌉."""
    return -(-len(text) // 4)


def slice_seeds(table: dict[str, dict], facts: dict, target: str) -> list[str]:
    """Seeds for *target*: every symbol of a file, or the one symbol it names.
    LookupError(candidates) when it names none or several."""
    keys = file_symbols(table, target) if target in facts else resolve_symbol(table, target)
    if not keys or (target not in facts and len(keys) != 1):
        raise LookupError(keys if target not in facts else [])
    return keys


def _rank(calls: CallIndex, table: dict[str, dict], seeds: list[str]) -> list[tuple[str, int]]:
    """(key, hop) for the seeds then the neighbourhood, best first."""
    hops = {s: 0 for s in seeds}
    score: dict[str, float] = {}
    layer = list(seeds)
    for hop in range(1, MAX_HOPS + 1):
        nxt = []
        for key in layer:
            near = [(k, "callee") for k in calls.callees.get(key, ())]
            near += [(c["caller"], "caller") for c in calls.callers.get(key, [])]
            for other, how in near:
                if other not in table or is_test_path(table[other]["file"]):
                    continue
                if other not in hops:
                    hops[other] = hop
                    nxt.append(other)
                if hops[other] == hop:
                    score[other] = max(score.get(other, 0.0), WEIGHT[how] / hop)
        layer = nxt
    rest = sorted(score, key=lambda k: (-(score[k] + HAZARD_BONUS * len(table[k]["hazards"])), k))
    return [(s, 0) for s in seeds] + [(k, hops[k]) for k in rest]


def _render(root: Path, row: dict, key: str, tier: str, lines_cache: dict[str, list[str]]) -> str:
    span = f"L{row['start']}-{row['end']}"
    if tier == "line":
        return f"- {key}  {span}  {row['signature']}".rstrip()
    head = f"## {key}  {span}"
    if tier == "source":
        if row["file"] not in lines_cache:
            try:
                text = (root / row["file"]).read_text(encoding="utf-8", errors="replace")
                lines_cache[row["file"]] = text.splitlines()
            except OSError:
                lines_cache[row["file"]] = []
        body = "\n".join(lines_cache[row["file"]][row["start"] - 1:row["end"]])
        return f"{head}\n```\n{body}\n```"
    out = [head, f"  {row['signature']}"] if row["signature"] else [head]
    if row["hazards"]:
        out.append("  hazards: " + "; ".join(f"{h['kind']} {h['what']} L{h['line']}" for h in row["hazards"]))
    if row.get("literals"):
        out.append("  literals: " + "; ".join(f"{lit['kind']} {lit['text']}" for lit in row["literals"]))
    return "\n".join(out)


def _frontier_line(names: list[str], shown: int) -> str:
    if not names:
        return "# frontier: none"
    more = len(names) - shown
    return "# frontier: " + ", ".join(names[:shown]) + (f" (+{more} more)" if more else "")


def build_slice(root: Path | str, facts: dict, seeds: list[str], budget: int = DEFAULT_BUDGET,
                label: str | None = None) -> dict:
    """Pack the seeds' neighbourhood into *budget* tokens (header and frontier line included)."""
    root = Path(root)
    table = symbol_table(facts)
    ranked = _rank(call_index(_flow.build(root, facts)), table, seeds)
    header = f"# slice {label or ', '.join(seeds)}  budget {budget}  used {budget}"  # widest `used`
    cache: dict[str, list[str]] = {}
    room = budget * 4 - len(header) - len("\n\n" + _frontier_line([], 0))
    if sum(len(_render(root, table[k], k, "line", cache)) + 2 for k, _ in ranked) > room:
        reserve = len(_frontier_line([k for k, _ in ranked[-FRONTIER_CAP:]], FRONTIER_CAP)) + len(" (+9999 more)")
        room -= min(reserve, budget)  # a quarter of the budget, in chars
    pieces: list[tuple[str, int, str, str]] = []
    frontier: list[str] = []
    for key, hop in ranked:
        start = 0 if hop == 0 else 1 if hop == 1 else 2
        for tier in TIERS[start:]:
            text = _render(root, table[key], key, tier, cache)
            if hop == 0 and tier == "skeleton" and start == 0:
                text += f"\n  source omitted ({table[key]['end'] - table[key]['start'] + 1} lines)"
            if len(text) + 2 <= room:
                pieces.append((key, hop, tier, text))
                room -= len(text) + 2
                break
        else:
            frontier.append(key)
    shown = min(len(frontier), FRONTIER_CAP)

    def assemble(used: int) -> str:
        top = f"# slice {label or ', '.join(seeds)}  budget {budget}  used {used}"
        return "\n\n".join([top, *(p[3] for p in pieces), _frontier_line(frontier, shown)]) + "\n"

    while tokens(assemble(budget)) > budget and (shown or pieces):
        if shown:
            shown -= 1
        else:
            frontier.insert(0, pieces.pop()[0])
    used = tokens(assemble(budget))
    for _ in range(3):  # `used` is printed in the header it measures
        text = assemble(used)
        if tokens(text) == used:
            break
        used = tokens(text)
    return {"text": text, "used": used, "frontier": frontier,
            "entries": [{"key": k, "hop": h, "tier": t} for k, h, t, _ in pieces]}
