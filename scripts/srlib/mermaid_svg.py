"""The hand-written diagrams as SVG, by parsing a defined subset of mermaid.

context and packages are generated from facts, so svg.py draws them from facts.
flow.mmd and architecture.mmd are written by the model, so there is nothing to
draw them from but the mermaid itself. This parses what it knows and refuses the
rest: a renderer that guessed at unknown syntax would draw a confidently wrong
picture, which is worse than no picture. It reports what it could not read and
writes nothing.
"""

from __future__ import annotations

import html
import re

from .svg import BOX, CW, EDGE, FONT, FS, INK, MUTED, PALETTE, PAPER

BAND = "#eef1f5"


def _frame_pad(depth: int) -> float:
    """How far a subgraph frame reaches past the boxes it wraps.

    An outer frame pads wider than the one nested inside it, so the two draw as
    separate rings rather than a second box over the same edge.
    """
    return 16 + (2 - min(depth, 2)) * 10


# The gap between two reserved band columns. It must clear two outermost frames
# reaching towards each other, or sibling subgraphs overlap and a node appears
# to sit inside a subgraph it is not in — at 58 the gcp and azure frames of a
# real topology crossed by 14px.
BAND_GAP = 2 * _frame_pad(0) + 12


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def wrap(text: str, limit: int = 34) -> list[str]:
    """Split on <br/> first — mermaid's own line break — then on width."""
    if "<br" in text:
        parts = re.split(r"<br\s*/?>", text)
        return [line for part in parts for line in wrap(part.strip(), limit)]
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > limit and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def document(width: float, height: float, title: str, body: str) -> str:
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="{FONT}">',
        f"<title>{esc(title)}</title>",
        f'<rect width="100%" height="100%" fill="{PAPER}"/>',
        f'<text x="20" y="30" font-size="{FS + 3}" fill="{INK}">{esc(title)}</text>',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{EDGE}"/></marker>'
        '<marker id="ao" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker>'
        + "".join(
            f'<marker id="m{i}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
            for i, c in enumerate(PALETTE)
        )
        + "</defs>",
        body, "</svg>",
    ])


def title_of(text: str) -> str:
    match = re.search(r"^title:\s*(.+)$", text, re.M)
    return match.group(1).strip() if match else "Diagram"


# ---------------------------------------------------------------- sequence ---

MSG = re.compile(r"^(\w+)\s*(-->>|->>|-->|->|-x|--x|-\)|--\))\s*(\w+)\s*:\s*(.*)$")
PARTICIPANT = re.compile(r"^participant\s+(\w+)(?:\s+as\s+(.+))?$")
NOTE = re.compile(r"^Note\s+(?:over|right of|left of)\s+([\w, ]+):\s*(.*)$", re.I)
BLOCK_OPEN = re.compile(r"^(loop|alt|opt|par|critical)\b\s*(.*)$")
BLOCK_ELSE = re.compile(r"^(else|and|option)\b\s*(.*)$")


def sequence_svg(text: str) -> tuple[str, list[str]]:
    order: list[str] = []
    labels: dict[str, str] = {}
    events: list[dict] = []
    unread: list[str] = []
    depth = 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("%%", "---", "title:", "sequenceDiagram")):
            continue
        if (m := PARTICIPANT.match(line)):
            order.append(m.group(1))
            labels[m.group(1)] = (m.group(2) or m.group(1)).strip()
            continue
        if (m := BLOCK_OPEN.match(line)):
            events.append({"t": "open", "kind": m.group(1), "text": m.group(2), "depth": depth})
            depth += 1
            continue
        if (m := BLOCK_ELSE.match(line)):
            events.append({"t": "else", "text": m.group(2), "depth": depth - 1})
            continue
        if line == "end":
            depth = max(0, depth - 1)
            events.append({"t": "close", "depth": depth})
            continue
        if (m := NOTE.match(line)):
            who = [w.strip() for w in m.group(1).split(",")]
            events.append({"t": "note", "who": who, "text": m.group(2), "depth": depth})
            continue
        if (m := MSG.match(line)):
            for name in (m.group(1), m.group(3)):
                if name not in labels:
                    order.append(name)
                    labels[name] = name
            events.append({"t": "msg", "src": m.group(1), "dst": m.group(3),
                           "dashed": m.group(2).startswith("--"), "text": m.group(4), "depth": depth})
            continue
        unread.append(line)

    if not order or not events:
        return "", unread or ["no participants or messages found"]

    col_w = max(max(len(labels[p]) for p in order) * CW + 26, 150)
    gap = 46
    x = {p: 30 + i * (col_w + gap) + col_w / 2 for i, p in enumerate(order)}
    head_y, row_h = 56, 40
    body, y = [], head_y + 64

    open_stack: list[dict] = []
    frames: list[dict] = []
    for event in events:
        if event["t"] == "open":
            open_stack.append({"y": y - 18, "kind": event["kind"], "text": event["text"],
                               "depth": event["depth"], "cols": [], "elses": []})
            y += 26
        elif event["t"] == "else":
            if open_stack:
                open_stack[-1]["elses"].append((y - 16, event["text"]))
            y += 22
        elif event["t"] == "close":
            if open_stack:
                frame = open_stack.pop()
                frame["y2"] = y - 8
                frames.append(frame)
            y += 8
        elif event["t"] == "note":
            cols = [x[w] for w in event["who"] if w in x] or [x[order[0]]]
            lines = wrap(event["text"], 46)
            w = max(len(s) for s in lines) * CW + 24
            cx = sum(cols) / len(cols)
            body.append(f'<rect x="{cx - w / 2:.1f}" y="{y - 14:.1f}" width="{w:.1f}" '
                        f'height="{len(lines) * 16 + 10}" rx="3" fill="#fff8dc" stroke="{MUTED}"/>')
            for i, s in enumerate(lines):
                body.append(f'<text x="{cx:.1f}" y="{y + 2 + i * 16:.1f}" text-anchor="middle" '
                            f'font-size="{FS - 2}" fill="{INK}">{esc(s)}</text>')
            for frame in open_stack:
                frame["cols"] += cols
            y += len(lines) * 16 + 18
        else:
            x1, x2 = x[event["src"]], x[event["dst"]]
            for frame in open_stack:
                frame["cols"] += [x1, x2]
            lines = wrap(event["text"], 52)
            self_call = x1 == x2
            label_x = x1 + 52 if self_call else (x1 + x2) / 2
            anchor = "start" if self_call else "middle"
            for i, s in enumerate(lines):
                body.append(f'<text x="{label_x:.1f}" y="{y - 6 - (len(lines) - 1 - i) * 14:.1f}" '
                            f'text-anchor="{anchor}" font-size="{FS - 2}" fill="{INK}">{esc(s)}</text>')
            dash = ' stroke-dasharray="6 4"' if event["dashed"] else ""
            if x1 == x2:
                body.append(f'<path d="M{x1:.1f},{y:.1f} C{x1 + 40:.1f},{y:.1f} {x1 + 40:.1f},{y + 18:.1f} '
                            f'{x1 + 6:.1f},{y + 18:.1f}" fill="none" stroke="{INK}" stroke-width="1.3"{dash} '
                            f'marker-end="url(#ao)"/>')
                y += 24
            else:
                body.append(f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" stroke="{INK}" '
                            f'stroke-width="1.3"{dash} marker-end="url(#ao)"/>')
            y += row_h

    height = y + 30
    width = 30 + len(order) * (col_w + gap) + 20

    back = []
    for frame in sorted(frames, key=lambda f: f["depth"]):
        cols = frame["cols"] or [x[order[0]]]
        pad = 22 + frame["depth"] * 6
        fx1, fx2 = min(cols) - pad, max(cols) + pad
        back.append(f'<rect x="{fx1:.1f}" y="{frame["y"]:.1f}" width="{fx2 - fx1:.1f}" '
                    f'height="{frame["y2"] - frame["y"]:.1f}" rx="4" fill="none" stroke="{MUTED}" '
                    f'stroke-width="1" stroke-dasharray="3 3"/>')
        tag = f'{frame["kind"]} {frame["text"]}'.strip()
        back.append(f'<rect x="{fx1:.1f}" y="{frame["y"]:.1f}" width="{len(tag) * CW + 14:.1f}" height="17" '
                    f'fill="{BAND}" stroke="{MUTED}" stroke-width="0.8"/>')
        back.append(f'<text x="{fx1 + 7:.1f}" y="{frame["y"] + 13:.1f}" font-size="{FS - 3}" '
                    f'fill="{INK}">{esc(tag)}</text>')
        for ey, etext in frame["elses"]:
            back.append(f'<line x1="{fx1:.1f}" y1="{ey:.1f}" x2="{fx2:.1f}" y2="{ey:.1f}" stroke="{MUTED}" '
                        f'stroke-width="0.8" stroke-dasharray="3 3"/>')
            back.append(f'<text x="{fx1 + 7:.1f}" y="{ey - 4:.1f}" font-size="{FS - 3}" '
                        f'fill="{MUTED}">else {esc(etext)}</text>')

    lifelines = []
    for i, name in enumerate(order):
        cx = x[name]
        colour = PALETTE[i % len(PALETTE)]
        lifelines.append(f'<line x1="{cx:.1f}" y1="{head_y + 44:.1f}" x2="{cx:.1f}" y2="{height - 24:.1f}" '
                         f'stroke="{EDGE}" stroke-width="1" stroke-dasharray="4 4"/>')
        label = labels[name]
        w = len(label) * CW + 22
        lifelines.append(f'<rect x="{cx - w / 2:.1f}" y="{head_y + 12:.1f}" width="{w:.1f}" height="32" rx="5" '
                         f'fill="{BOX}" stroke="{colour}" stroke-width="1.4"/>')
        lifelines.append(f'<text x="{cx:.1f}" y="{head_y + 33:.1f}" text-anchor="middle" font-size="{FS}" '
                         f'fill="{INK}">{esc(label)}</text>')

    return document(width, height, title_of(text), "\n".join(back + lifelines + body)), unread


# --------------------------------------------------------------- flowchart ---

def flow_svg(text: str) -> tuple[str, list[str]]:
    """Render a mermaid flowchart as SVG. The drawing engine is graphviz dot
    (see srlib/dviz.py); every other documented aspect — the mermaid source
    syntax, the ``(svg, unread)`` contract, and the fact that render never
    regenerates hand-written flow/architecture — is unchanged."""
    from . import dviz
    return dviz.flow_svg(text)
