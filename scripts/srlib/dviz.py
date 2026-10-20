"""Dot-source diagram rendering through graphviz (pygraphviz + system libgvc).

All diagrams — generated (context, packages, ERDs, structures, observed, tasks)
and hand-written (architecture, flow) — are dot source and are drawn by graphviz
dot: obstacle-avoiding splines, its own crossing minimisation, proper cluster
frames. Mermaid has been removed from the outputs entirely.

The callers ask for ``(svg, unread)``; with dot source there is no "unread subset",
so the second element is always empty — malformed dot raises instead of drawing a
confidently wrong picture.
"""

from __future__ import annotations

try:
    import pygraphviz as pgv  # type: ignore
except ImportError:  # pragma: no cover - exercised only on machines without graphviz
    pgv = None

EDGE = "#8b949e"
BOX_FILL = "#f6f8fa"
BOX_STROKE = "#8b949e"


def dot_svg(text: str) -> tuple[str, list[str]]:
    """Render a dot-source diagram as SVG through graphviz."""
    if pgv is None:  # pragma: no cover - only where graphviz is missing entirely
        raise RuntimeError("pygraphviz is not importable — diagrams need graphviz")
    g = None
    try:
        g = pgv.AGraph(string=text)
        g.graph_attr.update(ranksep="0.9", nodesep="0.45", pad="0.25", bgcolor="white")
        g.node_attr.update(fontname="Helvetica", fontsize="13", color=BOX_STROKE,
                           fillcolor=BOX_FILL, style="filled,rounded", penwidth="1.4")
        g.edge_attr.update(color=EDGE, fontname="Helvetica", fontsize="10",
                           fontcolor=EDGE, arrowsize="0.75")
        g.layout(prog="dot")
        data = g.draw(format="svg")
    finally:
        if g is not None:
            g.close()
    return data.decode("utf-8", errors="replace"), []


def flow_svg(text: str) -> tuple[str, list[str]]:
    """Deprecated alias kept for callers that predate the dot migration."""
    return dot_svg(text)