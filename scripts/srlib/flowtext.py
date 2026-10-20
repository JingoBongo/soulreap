"""The chain as text: `flow` (a tree), `flow --brief` (a card's list of anchors), `flow --dot`.

Split from flow.py, which resolves calls; this module only lays out what it resolved.
"""

from __future__ import annotations

import re

from .flow import MAX_DEPTH, FlowIndex, _children, _qual, resolve


def chain_text(idx: FlowIndex, rel: str, name: str, max_depth: int = MAX_DEPTH) -> str:
    if rel not in idx.defs or name not in idx.defs[rel]:
        return f"# no such callable: {rel}::{name}"
    lines = [f"# flow chain from {rel}::{name}"]

    def walk(f: str, qn: str, depth: int, seen: set, bar: str, print_self: bool = True):
        if print_self:
            lines.append(f"{bar}{idx.anchor(f, qn)}")
        if depth >= max_depth:
            return
        kids = _children(idx, f, qn)
        for i, (trel, tqn, conf, line, text) in enumerate(kids):
            last = i == len(kids) - 1
            branch = "└─ " if last else "├─ "
            mark = "  ~heuristic" if conf == "heuristic" else ""
            target = idx.anchor(trel, tqn)
            if (trel, tqn) in seen:
                lines.append(f"{bar}{branch}↺ {text}{mark}  (again at L{line} → {target})")
                continue
            lines.append(f"{bar}{branch}{text}{mark}")
            lines.append(f"{bar}{'   ' if last else '│  '}→ {target}")
            walk(trel, tqn, depth + 1, seen | {(trel, tqn)}, bar + ("   " if last else "│  "), print_self=False)

    walk(rel, name, 0, {(rel, name)}, "")
    return "\n".join(lines)


GLOSS_MAX = 70
HELPER_MAX_LINES = 5  # a leaf this short with no hazard is a helper: folded into its caller's line


def _gloss(doc: str | None) -> str:
    """A docstring's first sentence, short enough to sit after an anchor."""
    first = (doc or "").strip().split("\n")[0]
    first = re.split(r"\.\s+(?=[A-Z])", first)[0].rstrip(".")  # "e.g. the" is not a sentence end
    if not first:
        return ""
    if len(first) < GLOSS_MAX:
        return first + "."
    return first[:GLOSS_MAX - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def chain_brief(idx: FlowIndex, rel: str, name: str, budget: int = 40, info: dict | None = None) -> str:
    """The spine of a flow card: a markdown list, one `[[anchor]]` per call, pasted as is.

    The entry's own steps are always all there — they are the story; depth is what gets cut.
    Deeper levels are added breadth-first while they fit the *budget*; a call whose callees
    did not fit says `+N below`. Each target appears once (a getter called from four places
    is one line, not four). `↻` marks a call made inside a for/while, `?` one matched by name
    only. No line spans: pasted outside a fence, the anchors are citations `render` keeps live.

    *info* (`{"file::qual": {"doc", "lines", "hazards"}}`) adds each step's docstring sentence
    after it, and folds a leaf below the entry's own steps that is tiny and hazard-free into
    `+N helper(s)` on its caller's line: `_b`, a 3-line bool parser, took a line like `revive`.
    """
    info = info or {}
    if rel not in idx.defs or name not in idx.defs[rel]:
        return f"no such callable: {rel}::{name}"

    def kids(node: dict) -> list[dict]:
        f, qn = node["key"]
        scan = idx.scans.get(_qual(f, qn))
        # `_children` keeps a target's first call site only; `load()` before the loop and again
        # inside it is still a call that runs on every pass.
        in_loop = set()
        if scan is not None:
            cls = idx.defs[f][qn][3]
            for cname, line, _text in scan.calls:
                hit = resolve(idx, f, cname, scan.vartypes, cls) if line in scan.looped else None
                if hit:
                    in_loop.add((hit[0], hit[1]))
        return [{"key": (trel, tqn), "maybe": conf == "heuristic", "loop": (trel, tqn) in in_loop,
                 "depth": node["depth"] + 1, "kids": [], "hidden": 0}
                for trel, tqn, conf, _line, _text in _children(idx, f, qn)]

    root = {"key": (rel, name), "depth": 0, "kids": [], "hidden": 0}
    root["kids"] = kids(root)
    shown = {root["key"], *(k["key"] for k in root["kids"])}
    count, queue = len(root["kids"]), list(root["kids"])
    while queue:
        node = queue.pop(0)
        if node["depth"] >= MAX_DEPTH:
            continue
        fresh = [k for k in kids(node) if k["key"] not in shown]
        if count + len(fresh) > budget:
            node["hidden"] = len(fresh)
            continue
        node["kids"], count = fresh, count + len(fresh)
        shown.update(k["key"] for k in fresh)
        queue.extend(fresh)

    lines = [f"Calls from [[{rel}::{name}]] — ↻ in a loop · ? matched by name only · +N more below:", ""]

    def facts_of(node: dict) -> dict:
        return info.get("::".join(node["key"])) or {}

    def helper(node: dict) -> bool:
        about = facts_of(node)
        return (node["depth"] >= 2 and not node["kids"] and not node["hidden"] and not about.get("hazards")
                and 0 < (about.get("lines") or 0) <= HELPER_MAX_LINES)

    def emit(node: dict, indent: str) -> None:
        for kid in node["kids"]:
            if helper(kid):
                continue
            mark = ("↻ " if kid["loop"] else "") + ("? " if kid["maybe"] else "")
            gloss = _gloss(facts_of(kid).get("doc"))
            folded = sum(helper(k) for k in kid["kids"])
            tail = [f"+{kid['hidden']} below"] if kid["hidden"] else []
            tail += [f"+{folded} helper{'s' if folded > 1 else ''}"] if folded else []
            text = f"{indent}- {mark}[[{kid['key'][0]}::{kid['key'][1]}]]"
            text += f" — {gloss}" if gloss else ""
            text += (" · " if gloss else "  ") + " · ".join(tail) if tail else ""
            lines.append(text)
            emit(kid, indent + "  ")

    emit(root, "")
    return "\n".join(lines)


def chain_dot(idx: FlowIndex, rel: str, name: str, max_depth: int = MAX_DEPTH) -> str:
    edges: list[tuple[str, str]] = []
    nodes: dict[str, str] = {}

    def walk(f: str, qn: str, depth: int, seen: set):
        nodes[f"{f}::{qn}"] = idx.anchor(f, qn)
        if depth >= max_depth:
            return
        for trel, tqn, _conf, _line, _text in _children(idx, f, qn):
            a, b = f"{f}::{qn}", f"{trel}::{tqn}"
            if (a, b) not in edges:
                edges.append((a, b))
            if (trel, tqn) in seen:
                continue
            walk(trel, tqn, depth + 1, seen | {(trel, tqn)})

    walk(rel, name, 0, {(rel, name)})
    out = ["digraph flow {", "  rankdir=LR;", '  node [shape=box, fontname="Helvetica", fontsize=10];']
    for key in nodes:
        out.append(f'  "{key}";')
    for a, b in edges:
        out.append(f'  "{a}" -> "{b}";')
    out.append("}")
    return "\n".join(out)
