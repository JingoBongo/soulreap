"""Reading geometry back out of a drawn SVG: canvas, node boxes, edges, crossings, bends."""

from __future__ import annotations

import colorsys
import itertools
import re

from .svg import flatten_path as _flatten_path


def _without_defs(svg: str) -> str:
    """The drawn picture, with the definitions block removed.

    A drawer that colours its edges declares one <marker> per palette colour, and
    every marker carries a <path> of its own. Those are not edges: counted as
    edges they say nothing about the drawing and they divide the crossings ratio
    down, which is how a diagram reported as `18 crossings over 18 edges` fell
    silent at 0.60 the moment twelve arrowheads were defined above it.
    """
    return re.sub(r"<defs>.*?</defs>", "", svg, flags=re.DOTALL)


# Reading a drawing back. Two drawers write these files — the hand-written one here and
# graphviz — and they write different SVG. Graphviz puts `d` last (`<path fill="none"
# stroke="#2e22db" d="M.."/>`), draws node boxes as `<polygon>` rather than `<rect>`,
# sizes the canvas in `pt`, and frames a cluster with a `<path>` that is not an edge. A
# reader written for the first drawer therefore measured NOTHING on the second: zero
# edges, zero ink, zero crossings, and a gate that passed every generated picture by
# never looking at one. Everything below is written to read both.
_SVG_TAG = re.compile(r"<svg\b[^>]*>", re.S)


_SVG_SIZE = re.compile(r'\b(width|height)="([\d.]+)(?:pt|px)?"')


_PATH_TAG = re.compile(r"<path\b[^>]*?/?>", re.S)


_PATH_D = re.compile(r'\bd="([^"]+)"')


_PATH_STROKE = re.compile(r'\bstroke="#([0-9a-fA-F]{6})"')


_POLYGON_POINTS = re.compile(r'<polygon\b[^>]*?\bpoints="([^"]+)"', re.S)


_DRAWN_GROUP = re.compile(r'<g\b[^>]*class="(edge|node|cluster)"[^>]*>(.*?)</g>', re.S)


def _svg_canvas(svg: str) -> tuple[float, float]:
    """Canvas size off the `<svg>` tag itself.

    Searching the whole document for `width="..."` found `stroke-width="2"` in the
    first cluster frame, so every ink ratio on a graphviz drawing was computed against
    a canvas two units wide.
    """
    tag = _SVG_TAG.search(svg)
    if not tag:
        return 0.0, 0.0
    found = {key: float(value) for key, value in _SVG_SIZE.findall(tag.group(0))}
    return found.get("width", 0.0), found.get("height", 0.0)


def _drawn_groups(svg: str) -> dict[str, list[str]]:
    """`{"edge": [...], "node": [...], "cluster": [...]}` when the drawer says which is
    which, all empty when it does not."""
    groups: dict[str, list[str]] = {"edge": [], "node": [], "cluster": []}
    for kind, body in _DRAWN_GROUP.findall(svg):
        groups[kind].append(body)
    return groups


def _path_tags(svg: str) -> list[str]:
    return _PATH_TAG.findall(svg)


def _polygon_bbox(points: str) -> tuple[float, float, float, float] | None:
    coordinates = []
    for pair in points.split():
        x, _, y = pair.partition(",")
        try:
            coordinates.append((float(x), float(y)))
        except ValueError:
            return None
    if not coordinates:
        return None
    xs = [c[0] for c in coordinates]
    ys = [c[1] for c in coordinates]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _node_boxes(svg: str) -> list[tuple[float, float, float, float]]:
    """Graphviz node shapes as boxes: one `<g class="node">` holds a polygon per cell
    of an HTML label, and the node is their union — the table, not its rows."""
    boxes = []
    for body in _drawn_groups(_without_defs(svg))["node"]:
        corners = [_polygon_bbox(points) for points in _POLYGON_POINTS.findall(body)]
        # A rounded box is a <path>, not a <polygon> — `style="rounded"` changes the
        # element, and the packages diagram is drawn entirely of rounded boxes.
        for tag in _path_tags(body):
            shape = _PATH_D.search(tag)
            if not shape:
                continue
            points = [point for segment in _flatten_path(shape.group(1)) for point in segment]
            if points:
                xs = [x for x, _ in points]
                ys = [y for _, y in points]
                corners.append((min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
        corners = [c for c in corners if c]
        if not corners:
            continue
        left = min(c[0] for c in corners)
        top = min(c[1] for c in corners)
        right = max(c[0] + c[2] for c in corners)
        bottom = max(c[1] + c[3] for c in corners)
        boxes.append((left, top, right - left, bottom - top))
    return boxes


def _edges_of(svg: str) -> list:
    """The drawn edges, as flattened polylines — the same set `_svg_geometry` measures.

    Inside the edge groups when the drawer marks them, because a graphviz cluster frame
    is a `<path>` too and counting five section frames as five edges divides the
    crossings ratio by two.
    """
    body = _without_defs(svg)
    groups = _drawn_groups(body)
    sources = groups["edge"] if (groups["edge"] or groups["node"]) else [body]
    found = [
        _flatten_path(match.group(1))
        for block in sources
        for tag in _path_tags(block)
        for match in [_PATH_D.search(tag)]
        if match
    ]
    return [edge for edge in found if edge]


def _svg_geometry(svg: str) -> tuple[int, int, float]:
    """(edges through a node box, edge crossings, ink ratio) for a rendered flowchart.

    Ink ratio is total edge length over the canvas diagonal, and it is the metric that
    actually separates a readable picture from a tangle: it rises when edges travel
    absurd distances, which is what a wide canvas full of long cross-diagram edges
    produces. Measured on one repo: a legible topology scored 2.7, a deliberately bad
    one 5.8, the generated bipartite context diagram 7.9. Crossings correlate but more
    weakly (3 vs 6), and edge-through-box turned out to be a poor discriminator once
    subgraph frames were correctly excluded (0 vs 1) — it is still reported, because
    when it IS high the picture is definitely unreadable, but it is not the gate.

    The bundled renderer lays nodes out in layers but draws every edge as a straight
    line with no obstacle avoidance, so a long edge is drawn *through* the boxes that
    lie between its ends. That is the single biggest cause of an illegible diagram and
    it cannot be seen in the .mmd source — only in what was drawn.
    """
    width, height = _svg_canvas(svg)
    boxes = [
        tuple(float(g) for g in m.groups())
        for m in re.finditer(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg
        )
    ]
    # Skip frames. A subgraph frame and the page background legitimately have edges
    # drawn across them — an edge reaching a node inside a subgraph MUST cross that
    # subgraph's border, and counting that made a legible 6-node diagram score as
    # spaghetti. A frame is recognised geometrically rather than by size: it is a rect
    # that contains another rect.
    def _is_frame(box: tuple, others: list[tuple]) -> bool:
        bx, by, bw, bh = box
        return any(
            ox > bx and oy > by and ox + ow < bx + bw and oy + oh < by + bh
            for ox, oy, ow, oh in others
            if (ox, oy, ow, oh) != box
        )

    boxes = [b for b in boxes if not _is_frame(b, boxes)]
    # Drop decoration. The context drawer places 5x12 colour chips inside the very
    # channel its edges run through, deliberately; counting them as nodes reported
    # every edge as passing through one.
    boxes = [b for b in boxes if b[2] >= 24 and b[3] >= 16]
    # Graphviz draws its node shapes as polygons; a cluster frame is a <g class="cluster">
    # and stays out, which is the same exclusion `_is_frame` makes geometrically above.
    boxes += _node_boxes(svg)

    edges = _edges_of(svg)
    segments = [seg for edge in edges for seg in edge]

    def crosses(seg: tuple[tuple[float, float], tuple[float, float]], box: tuple) -> bool:
        (x1, y1), (x2, y2) = seg
        bx, by, bw, bh = box
        return any(
            bx + 2 < x1 + t * (x2 - x1) < bx + bw - 2
            and by + 2 < y1 + t * (y2 - y1) < by + bh - 2
            for t in (i / 40 for i in range(41))
        )

    # Count (edge, box) incidents, never (segment, box). A flattened curve is ~16
    # segments, so counting segments multiplied one real crossing by the sampling
    # density: on a real repo the number sat at exactly 16 through three different
    # layouts, because it was 1-3 crossings times 16 samples and nothing to do with
    # the picture.
    def is_endpoint_box(edge: list, box: tuple) -> bool:
        """The box this edge starts at or ends at. An edge ends AT the box it points
        to — a drawer stopping a few pixels inside the border, or an arrowhead drawn
        over it, is not the edge passing through something on its way elsewhere, which
        is the only thing this metric is about."""
        bx, by, bw, bh = box
        pad = 4.0
        return any(
            bx - pad <= point[0] <= bx + bw + pad and by - pad <= point[1] <= by + bh + pad
            for point in (edge[0][0], edge[-1][1])
        )

    through = sum(
        1
        for edge in edges
        for box in boxes
        if not is_endpoint_box(edge, box) and any(crosses(seg, box) for seg in edge)
    )

    def intersects(p1: tuple, p2: tuple, p3: tuple, p4: tuple) -> bool:
        denom = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0])
        if abs(denom) < 1e-9:  # parallel or collinear
            return False
        t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / denom
        u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / denom
        return 0.02 < t < 0.98 and 0.02 < u < 0.98

    crossings = sum(
        1
        for a, b in itertools.combinations(segments, 2)
        if intersects(a[0], a[1], b[0], b[1])
    )

    diagonal = (width * width + height * height) ** 0.5
    ink = sum(
        ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a, b in segments
    )
    return through, crossings, (ink / diagonal if diagonal else 0.0)


def _coloured_edges(svg: str) -> list[tuple[list, float]]:
    """Drawn edges paired with the hue of their stroke, in degrees."""
    body = _without_defs(svg)
    groups = _drawn_groups(body)
    sources = groups["edge"] if (groups["edge"] or groups["node"]) else [body]
    out: list[tuple[list, float]] = []
    for block in sources:
        for tag in _path_tags(block):
            shape, colour = _PATH_D.search(tag), _PATH_STROKE.search(tag)
            if not shape or not colour:
                continue
            edge = _flatten_path(shape.group(1))
            if not edge:
                continue
            raw = colour.group(1)
            red, green, blue = (int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
            out.append((edge, colorsys.rgb_to_hls(red, green, blue)[0] * 360))
    return out


# Two hues closer than this read as the same colour at the width an edge is drawn.
# Measured on nvm-forecast's ERD: the two crossings a reader could not untangle were
# 39 and 36 degrees apart (green/teal and teal/blue), the two they could were 141 and
# 163. The gap between those pairs is where this sits.
_CONFUSABLE_HUE_DEGREES = 60.0


def _confusable_crossings(svg: str, min_delta: float = _CONFUSABLE_HUE_DEGREES) -> int:
    """Crossings where colour cannot tell the two lines apart.

    A crossing is not a defect by itself — a schema with two hubs cannot avoid them.
    What makes one unreadable is having nothing at the intersection to say which line
    is which, and colour is the only thing there. So this counts the crossings that
    are actually ambiguous, which is the number worth driving to zero.
    """
    edges = _coloured_edges(svg)
    confusable = 0
    for (first, hue_a), (second, hue_b) in itertools.combinations(edges, 2):
        delta = abs(hue_a - hue_b) % 360
        if min(delta, 360 - delta) >= min_delta:
            continue
        crosses = any(_segments_cross(one, other) for one in first for other in second)
        # Crossing is not the only place a reader has to tell two lines apart: two edges
        # arriving at the same box land side by side, and that is where a human caught
        # what this check first missed — log_hours and shifts both point at people, in
        # two greens 45 degrees apart, without ever crossing.
        # Only the ARRIVING ends: two edges leaving one table share its box by design
        # and carry one colour on purpose, and nobody has to tell those apart. It is
        # the ends that land together, from different tables, that need separating.
        arrival_a, arrival_b = first[-1][1], second[-1][1]
        meets = abs(arrival_a[0] - arrival_b[0]) < 40 and abs(arrival_a[1] - arrival_b[1]) < 40
        if crosses or meets:
            confusable += 1
    return confusable


def _segments_cross(a: tuple, b: tuple) -> bool:
    """Proper crossing of two segments — endpoints touching do not count."""
    (x1, y1), (x2, y2) = a
    (x3, y3), (x4, y4) = b
    denom = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(denom) < 1e-9:
        return False
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / denom
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / denom
    return 0.02 < t < 0.98 and 0.02 < u < 0.98


def _edge_bends(svg: str) -> int:
    """Direction changes across every drawn edge.

    Second on the list of aesthetics that measurably help comprehension — after
    crossings, ahead of symmetry and edge length, which the same studies found do not
    matter. We had been counting only crossings, so a picture could trade one crossing
    for a dozen corners and score as an improvement.

    A turn counts when consecutive segments differ by more than ~15 degrees, which
    leaves a flattened curve (many tiny turns in the same direction) reading as the one
    bend a person sees rather than as sixteen.
    """
    import math

    bends = 0
    for edge, _hue in _coloured_edges(svg):
        previous = None
        for (x1, y1), (x2, y2) in edge:
            angle = math.atan2(y2 - y1, x2 - x1)
            if previous is not None:
                turn = abs(math.degrees(angle - previous))
                turn = min(turn, 360 - turn)
                if turn > 15:
                    bends += 1
            previous = angle
    return bends
