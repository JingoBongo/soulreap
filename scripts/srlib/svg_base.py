"""The constants and primitives every SVG drawer here shares.

Font, box metrics, the ink/paper colours, the source-package PALETTE, and the small
pieces that draw with them: text escaping, box width and the box itself, the page
skeleton (svg_document) and the per-colour arrowhead markers.
"""

from __future__ import annotations

import html

FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
FS = 13          # font size
CW = FS * 0.60   # monospace advance width, so box widths are exact
PAD = 10
ROW = 34

INK = "#1b1f24"
MUTED = "#6a737d"
PAPER = "#ffffff"
BOX = "#f6f8fa"
EDGE = "#8b949e"
# One colour per source package, so a line can be traced back to its box.
# Six hues, 60 degrees apart, every pair of them. The first attempt used eight at 45
# degrees and guaranteed only that CONSECUTIVE slots were far apart — slots 3 and 5
# ended up 45 degrees from each other and drew log_hours and shifts as two greens, on
# a picture where both arrive at the same table side by side. Six is also all a graph
# of this kind needs: colouring the square of the schema graph took six.
# Order matters as much as the values: a greedy colouring hands out slot 0, then 1,
# then 2, so consecutive slots are what a small diagram actually uses. Listed in
# bit-reversed order, every consecutive pair is at least 90 degrees apart, and the
# four a typical picture needs (244, 64, 334, 154) are mutually at least 90 apart.
# Listed in wheel order they would have been 45 apart — closer than the 36-39 degree
# pairs that made nvm-forecast's crossings unreadable in the first place.
PALETTE = [
    "#2E22DB",  # 244° brand blue
    "#B7C213",  # 64° citron
    "#C213B7",  # 304° magenta
    "#13C21E",  # 124° green
    "#C21E13",  # 4° red
    "#13B7C2",  # 184° cyan
]


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def box_width(label: str) -> float:
    return len(label) * CW + 2 * PAD


def draw_box(x: float, y: float, w: float, label: str, sub: str = "", accent: str = INK) -> str:
    h = ROW if not sub else ROW + 14
    out = [
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="6" '
        f'fill="{BOX}" stroke="{accent}" stroke-width="1.2"/>',
        f'<text x="{x + PAD:.1f}" y="{y + 21:.1f}" font-family="{FONT}" font-size="{FS}" '
        f'fill="{INK}">{esc(label)}</text>',
    ]
    if sub:
        out.append(
            f'<text x="{x + PAD:.1f}" y="{y + 35:.1f}" font-family="{FONT}" font-size="{FS - 3}" '
            f'fill="{MUTED}">{esc(sub)}</text>'
        )
    return "\n".join(out)


def svg_document(width: float, height: float, title: str, body: str, defs: str = "") -> str:
    """The page every drawer here writes into.

    `defs` is for extra marker definitions. A drawer that colours its edges needs
    an arrowhead per colour — a shared grey head on a coloured line reads as a
    different edge arriving, which is worse than not colouring at all.
    """
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="{FONT}">',
        f"<title>{esc(title)}</title>",
        f'<rect width="100%" height="100%" fill="{PAPER}"/>',
        f'<text x="{PAD * 2}" y="30" font-size="{FS + 3}" fill="{INK}">{esc(title)}</text>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{EDGE}"/>'
        f"</marker>{defs}</defs>",
        body,
        "</svg>",
    ])


def palette_markers() -> str:
    """One arrowhead per palette colour, named `m<index>` as mermaid_svg names them."""
    return "".join(
        f'<marker id="m{index}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{colour}"/></marker>'
        for index, colour in enumerate(PALETTE)
    )
