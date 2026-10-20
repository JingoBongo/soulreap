"""Shared Graphviz settings and the entity box, for every picture `erd_dot` draws.

The layout constants measured for the ERD (rank direction, ratio, spacing, font sizes), the
section-label helper, the HTML-table node label an entity or structure is drawn as, and
`_best_routing`, which draws a graph curved and straight and keeps the arcs unless they
cost. Nothing here draws a whole picture; the sibling modules do.
"""

from __future__ import annotations

import html
from pathlib import PurePosixPath

RANKDIR = "TB"
SPLINES = "spline"
RATIO = "0.5"          # height/width: 1:2, a landscape page
NODESEP = "0.22"
# An edge bows because the router has to get AROUND something, not because it has room to.
# That is the opposite of what more space sounds like it should do, and it was measured
# the wrong way round twice: at 1.4 the spline finds a clear straight run and takes it, at
# 1.1 it is nearly straight, and only at 0.55 — ranks packed tight — does the curve show
# on the page. Tight it is, which is also what the canvas measurements wanted before any
# of this.
RANKSEP = "0.55"
FONT_SIZE = 20
TAG_SIZE = 12
SECTION_FONT_SIZE = 24


def section_labels(paths: list[str]) -> dict[str, str]:
    """Path -> the shortest suffix that tells it apart from the others.

    A section is a file, so its label is that file's name — `roster.py` says where to
    look in a way `ROSTER` does not. `models.py` in three packages would draw three
    identical labels, so directories are added from the right until each is unique.
    """
    labels: dict[str, str] = {}
    for path in paths:
        parts = PurePosixPath(path).parts
        for depth in range(1, len(parts) + 1):
            candidate = "/".join(parts[-depth:])
            same = sum(
                1 for other in paths if "/".join(PurePosixPath(other).parts[-depth:]) == candidate
            )
            if same == 1:
                labels[path] = candidate
                break
        else:
            labels[path] = path
    return labels


def _key_tag(column: dict) -> str | None:
    """A table column's role: the key it is, if it is one. Already HTML-safe."""
    if column.get("pk"):
        return "PK"
    return "FK" if column.get("fk") else None


def _is_key(column: dict) -> bool:
    return bool(column.get("pk") or column.get("fk"))


def _node_label(name: str, table: dict, tag_of=_key_tag, emphasis=_is_key, header="#EEF0FF") -> str:
    """One entity as a Graphviz HTML table: a header, then a row per field.

    `tag_of`, `emphasis` and `header` are what let a structure reuse this box without
    pretending to be a table: it has no primary key to tag, it emphasises a different
    thing, and it gets its own header colour, so the two kinds of picture do not look
    alike at a glance. Whatever `tag_of` returns goes into the label unescaped — the
    column already holds `&#160;` — so a caller passing source text escapes it.
    """
    rows = ""
    for column in table.get("columns", []):
        tag = tag_of(column)
        shown = html.escape(column["name"])
        if emphasis(column):
            shown = f"<B>{shown}</B>"
        rows += (
            f'<TR><TD PORT="{html.escape(column["name"])}" ALIGN="LEFT">{shown}</TD>'
            f'<TD ALIGN="RIGHT"><FONT POINT-SIZE="{TAG_SIZE}" COLOR="#8A8A8A">{tag or "&#160;"}</FONT></TD></TR>'
        )
    return (
        '<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="5">'
        f'<TR><TD COLSPAN="2" BGCOLOR="{header}"><B>{html.escape(name)}</B></TD></TR>{rows}</TABLE>>'
    )


# Straight edges are hard to read exactly where a reader most needs them: two tables
# stacked one above the other, joined by a near-vertical line, and no way to see which
# row it lands on. `curved` bows the edge and answers that — but only while the router
# has room. Measured on both dogfood schemas:
#
#   10 tables   spline: 0 through-box, 7 crossings   curved: 0 through-box, 6 crossings
#   23 tables   spline: 0 through-box, 25 crossings  curved: 8 through-box, 22 crossings
#
# So neither setting wins outright; the winner depends on how crowded the page is, and
# the drawing decides — the same way the packages diagram picks its node placement.
#
# Curved is the default and has to be BEATEN, not merely tied, and that asymmetry is the
# point. What an arc buys is the one thing the metrics cannot see: with two tables stacked
# and a straight line between them, nothing on the page says which row the line ends at.
# Ranking purely on crossings threw the arcs away for a single crossing, every time.
STRAIGHT_COSTS_LESS_BY = 3
# And one edge may cross a box for it. The hard budget is four, so a single incident is
# well inside it, and it buys the thing the whole seam exists for.
CURVE_MAY_COST_THROUGH = 1


def _best_routing(graph) -> str:
    """Draw the graph curved, draw it straight, and keep the arcs unless they cost.

    Straight wins only when it puts fewer edges through a node box — the documented
    legibility gate — or saves more than a few crossings. Below that margin the arc is
    worth more than the crossing it costs, because it answers a question the crossing
    count is not measuring.
    """
    from .svggeom import _svg_geometry

    drawn = {}
    for splines in ("curved", "spline"):
        graph.graph_attr["splines"] = splines
        graph.layout(prog="dot")
        drawing = graph.draw(format="svg").decode("utf-8")
        through, crossings, _ink = _svg_geometry(drawing)
        drawn[splines] = (through, crossings, drawing)

    curved_through, curved_crossings, curved = drawn["curved"]
    straight_through, straight_crossings, straight = drawn["spline"]
    if curved_through > straight_through + CURVE_MAY_COST_THROUGH:
        return straight
    if curved_crossings > straight_crossings + STRAIGHT_COSTS_LESS_BY:
        return straight
    return curved
