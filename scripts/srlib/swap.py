"""The reader's one veto: swap one card the score chose for one shortlist entry, with a reason.

The importance score picks cards as well as a reader who read the whole repo does, but the two
disagree in different places. After reading, the reader may overrule it once: any card except
what runs (those stay fixed), for an entry of the plan's shortlist. Recorded in plan.json, kept
by the next `plan` while both targets still exist, and reported by `render`.
"""

from __future__ import annotations

import re
from pathlib import Path

from .util import load_json, write_json


def slug(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", key).strip("-").lower()


def card_path(target: str) -> str:
    return f"cards/{slug(target)}.md"


def cmd_swap(out: Path | str, card: str, replacement: str, why: str, root: Path | str | None = None) -> dict:
    """Swap *card* (a topic target) for *replacement* (a shortlist target). LookupError when refused.
    With *root*, the read packet is rewritten so it names the cards as they now are."""
    path = Path(out) / ".soulreap" / "plan.json"
    plan = load_json(path, {})
    if not plan.get("topics"):
        raise LookupError(f"no plan at {path} — run `plan` first")
    if "fixed_cards" not in plan:
        raise LookupError("this plan predates card swaps — run `plan` again")
    if plan.get("swap"):
        done = plan["swap"]
        raise LookupError(f"one swap per plan: {done['out']} was already swapped for {done['in']}")
    if not why.strip():
        raise LookupError("say why in one line: --why \"…\"")
    targets = [t["target"] for t in plan["topics"]]
    fixed = plan.get("fixed_cards", 0)
    if card not in targets:
        raise LookupError(f"{card} is not a card of this plan; cards: {', '.join(targets[fixed:]) or '(none)'}")
    index = targets.index(card)
    if index < fixed:
        raise LookupError(f"{card} is what runs — its card stays; swap one of: {', '.join(targets[fixed:])}")
    shortlist = {t["target"]: t for t in plan.get("shortlist") or []}
    if replacement not in shortlist:
        raise LookupError(f"{replacement} is not on the shortlist: {', '.join(shortlist) or '(empty)'}")
    plan["topics"][index] = {"kind": shortlist[replacement]["kind"], "target": replacement}
    plan["shortlist"] = [t for t in plan["shortlist"] if t["target"] != replacement]
    plan["swap"] = {"out": card, "in": replacement, "kind": shortlist[replacement]["kind"], "why": why.strip()}
    write_json(path, plan)
    if root is not None:
        from .mapplan import _write_read_packet  # imported late: mapplan imports this module

        _write_read_packet(Path(root), Path(out), plan)
    return {"write": card_path(replacement), "instead_of": card_path(card), "why": why.strip()}


def reapply(topics: list[dict], fixed: int, shortlist: list[dict], previous: dict, known) -> tuple[list, dict | None]:
    """A new plan keeps the reader's earlier swap while both targets still exist (*known(target)*)."""
    swap = previous.get("swap")
    if not swap or not known(swap["in"]) or not known(swap["out"]):
        return topics, None
    targets = [t["target"] for t in topics]
    if swap["in"] in targets:  # chosen anyway: nothing is swapped, and the reader's swap is free again
        return topics, None
    kind = swap.get("kind") or ("flow" if "::" in swap["in"] else "component")
    index = targets.index(swap["out"]) if swap["out"] in targets[fixed:] else None
    if index is None:
        return topics, None
    topics = [*topics[:index], {"kind": kind, "target": swap["in"]}, *topics[index + 1:]]
    return topics, swap
