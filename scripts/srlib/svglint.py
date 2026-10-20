"""Geometry lint for the generated SVGs: crossings, bends, text clashes, through-box edges."""

from __future__ import annotations

import itertools
import re
from pathlib import Path

from .svggeom import _confusable_crossings, _edge_bends, _edges_of, _svg_geometry

# Flowcharts, where an edge crossing a node box is a defect. A sequence diagram is
# excluded on purpose: its lifelines pass through note boxes and activation bars by
# design, and flow.svg scores 32 while being the most readable picture of the four.
#
# packages.svg is generated rather than authored, so a warning on it is one the reader
# cannot act on by editing a file — but it is still reported, because it tells whoever
# maintains this skill that the drawer needs work, and it is the only way that fact
# surfaces at all.
_LINTED_FLOWCHARTS = ("architecture.svg", "packages.svg")


def _linted_flowcharts(diagrams: Path) -> list[str]:
    """Filenames whose geometry is worth measuring.

    ERDs are added by a glob: how many of them there are is not known in
    advance, and they are drawn by the same layered drawer as everything else
    here, so they need measuring the same way.
    """
    return (
        list(_LINTED_FLOWCHARTS)
        + sorted(p.name for p in diagrams.glob("erd-*.svg"))
        + sorted(p.name for p in diagrams.glob("structures-*.svg"))
    )


# Edge segments drawn through a node box. This is the gate, and it took three attempts
# to establish that honestly, so the record is worth keeping: the first two versions
# measured curves by their CONTROL POLYGON and counted subgraph frames and 5x12 colour
# swatches as nodes, which produced numbers that were pure artifact. With curves
# sampled, frames and decoration excluded, and sequence diagrams left out, it separates
# cleanly on real output — the authored topology and the generated context diagram score
# 0, the generated layering diagram 16. Ink ratio was tried as the gate and rejected: a
# bipartite fan scores 5.0 while being perfectly readable.
_THROUGH_BUDGET = 4


# Crossings per edge. This is the metric a reader actually reacts to, and leaving it out
# of the gate was the mistake that let a diagram ship looking like a tangle while
# scoring a clean 0 on edge-through-box: 11 crossings over 12 edges. Reordering the
# EDGE DECLARATIONS took the same graph to 2. Thresholds are heuristic and deliberately
# loose — observed 0.17 for the reordered topology, 0.33 for the generated layering
# diagram, 0.47 for a bipartite fan that reads fine, 0.50 for a badly ordered variant
# and 0.92 for the tangle.
_CROSSING_RATIO_BUDGET = 0.6


# Bends per edge. Second in the empirical ranking of what helps a reader, and generous
# here on purpose: a routed edge legitimately turns twice to leave a box, travel a
# corridor and arrive. Past four, the picture is a maze.
_BENDS_PER_EDGE_BUDGET = 4.0


def _svg_text_clashes(svg: str) -> int:
    """Pairs of text labels whose boxes overlap.

    The geometry metric compares paths with node rectangles and is completely blind to
    text, which is how a diagram shipped with two pairs of edge labels printed over each
    other and two more printed across a node box while every geometry number read clean.

    A label emitted twice at one position is one label: these drawers paint each string
    first as a paper-coloured stroke, to punch a hole in whatever passes behind it, then
    again as glyphs.
    """
    seen: set[tuple[float, float]] = set()
    labels: list[tuple[float, float, float, float]] = []
    for m in re.finditer(r'<text x="([\d.-]+)" y="([\d.-]+)"([^>]*)>([^<]*)', svg):
        x, y, attrs, text = float(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
        size_match = re.search(r'font-size="(\d+)"', attrs)
        if not size_match or not text.strip():
            continue
        size = float(size_match.group(1))
        key = (round(x, 1), round(y, 1))
        if key in seen:
            continue
        seen.add(key)
        # 0.62 em per glyph is close enough for a monospace stack at these sizes.
        # `x` is the CENTRE of a centred label, not its left edge: measured from the
        # left, every such box sits half a label to the right of the glyphs, which
        # invents a clash between a long label and a short one well clear of it.
        width = len(text) * size * 0.62
        left = x - width / 2 if 'text-anchor="middle"' in attrs else x
        labels.append((left, y, left + width, size))
    return sum(
        1
        for a, b in itertools.combinations(labels, 2)
        if abs(a[1] - b[1]) < min(a[3], b[3]) and a[0] < b[2] and b[0] < a[2]
    )


_HAND_ADVICE = (
    "For a hand-written diagram the biggest lever is the ORDER THE EDGES ARE DECLARED, "
    "which seeds the layout: group them by source, and within a source list the targets "
    "left-to-right as you want them placed. Then drop subgraphs, keep the graph near a "
    "tree, and put shared sinks in the bottom layer."
)


_DOT_ADVICE = (
    "This one is laid out by graphviz, so declaration order is not the lever — dot "
    "reorders every rank itself. What moves these numbers, measured: let dot choose "
    "which SIDE of a node an edge leaves (a pinned compass point made edges wrap "
    "around), prefer `splines=spline` over `curved` (which also drops routes), widen "
    "`ranksep` before `nodesep`, and for same-colour crossings give the graph more hues "
    "or fewer clusters — see `erd_dot.py`, where each of these was measured on two "
    "schemas."
)


def _lint_svg_geometry(diagrams: Path) -> list[str]:
    """Report a hand-written diagram whose drawn geometry is illegible."""
    warnings: list[str] = []
    for name in _linted_flowcharts(diagrams):
        svg = diagrams / name
        if not svg.exists():
            continue
        through, crossings, ink = _svg_geometry(
            svg.read_text(encoding="utf-8", errors="replace")
        )
        edge_count = max(len(_edges_of(svg.read_text(encoding="utf-8", errors="replace"))), 1)
        ratio = crossings / edge_count
        problems = []
        if through > _THROUGH_BUDGET:
            problems.append(f"{through} edges are drawn through a node box")
        if ratio > _CROSSING_RATIO_BUDGET:
            problems.append(
                f"{crossings} crossings over {edge_count} edges ({ratio:.2f} each)"
            )
        clashes = _svg_text_clashes(svg.read_text(encoding="utf-8", errors="replace"))
        if clashes:
            problems.append(f"{clashes} pairs of labels printed on top of each other")
        bends = _edge_bends(svg.read_text(encoding="utf-8", errors="replace"))
        if bends > edge_count * _BENDS_PER_EDGE_BUDGET:
            problems.append(f"{bends} bends over {edge_count} edges")
        # Advice has to match the drawer, or it sends the reader to a lever that is not
        # connected to anything: declaration order seeds the hand-written layout and
        # means nothing to dot, which reorders both ranks by its own crossing pass.
        by_dot = 'class="edge"' in svg.read_text(encoding="utf-8", errors="replace")
        advice = _DOT_ADVICE if by_dot else _HAND_ADVICE
        confusable = _confusable_crossings(svg.read_text(encoding="utf-8", errors="replace"))
        if confusable:
            problems.append(
                f"{confusable} crossings are between edges of nearly the same colour"
            )
        if problems:
            warnings.append(
                f"{name}: " + " and ".join(problems) + " — the picture will read as a "
                f"tangle (edges total {ink:.1f}x the canvas diagonal). " + advice
            )
    return warnings
