"""The package-layering diagram as SVG.

packages_svg draws the source packages as boxes in layers, with the file-level import
counts as weighted, per-source coloured edges.
"""

from __future__ import annotations

from .diagrams import _grouping_depth, diagram_paths, display_label, group_key
from .svg_base import FS, MUTED, PALETTE, PAPER, ROW, box_width, draw_box, palette_markers, svg_document
from .svg_graph import graph_colours
from .svg_layout import corridor


def packages_svg(facts: dict) -> str:
    """Import layering: entry points on top, foundations at the bottom."""
    paths = diagram_paths(facts)
    visible = set(paths)
    depth = _grouping_depth(paths)
    weights: dict[tuple[str, str], int] = {}
    for path in paths:
        for dep in facts[path].get("deps", []):
            if dep not in visible:
                continue
            pair = (group_key(path, depth), group_key(dep, depth))
            if pair[0] != pair[1]:
                weights[pair] = weights.get(pair, 0) + 1

    labels = sorted({n for pair in weights for n in pair} or {group_key(p, depth) for p in paths})
    incoming = {label: sum(1 for s, t in weights if t == label) for label in labels}
    outgoing = {label: sum(1 for s, t in weights if s == label) for label in labels}

    def role(label: str) -> str:
        into, out_of = incoming[label], outgoing[label]
        if not into and not out_of:
            return "isolated"
        return "entry" if not into else ("foundation" if not out_of else "core")

    # Longest-path layering, but only over a DAG. A mutual pair (A imports B and
    # B imports A, as onboarding_prevalidator and its rules/ do) makes plain
    # relaxation climb forever: it pushed one pair to layer 28 and left a screen
    # of blank paper in the middle of the picture. Keep the heavier direction for
    # layering and ignore the lighter one; both are still drawn.
    layering_edges = []
    for (source, target), weight in weights.items():
        back = weights.get((target, source))
        if back is not None and (back, target) > (weight, source):
            continue  # the reverse edge carries the layering for this pair
        layering_edges.append((source, target))

    layer = {label: 0 for label in labels}
    ceiling = max(len(labels) - 1, 0)
    for _ in range(len(labels)):
        changed = False
        for source, target in layering_edges:
            if layer[target] <= layer[source] and layer[target] < ceiling:
                layer[target] = min(layer[source] + 1, ceiling)
                changed = True
        if not changed:
            break

    by_layer: dict[int, list[str]] = {}
    for label in labels:
        by_layer.setdefault(layer[label], []).append(label)

    # Compact the layers actually used. The mutual-pair rule above keeps layering
    # to a DAG for PAIRS only — a cycle of three or more packages survives it, and
    # relaxation then drives every member to the ceiling. The layers in between
    # come out empty, and rows are placed by layer index, so the picture gets a
    # blank band: 282px of it on a four-package cycle, ~700px on nvm-forecast,
    # with every edge stretched across the gap.
    compacted = {index: ordinal for ordinal, index in enumerate(sorted(by_layer))}
    layer = {label: compacted[index] for label, index in layer.items()}
    by_layer = {compacted[index]: names for index, names in by_layer.items()}

    # Order each layer by the barycentre of its neighbours in the adjacent layers, the
    # same pass context_svg already does. Without it a layer is placed alphabetically,
    # so an edge from the far left of one layer to the far right of the next is drawn
    # over every box between them — 16 such incidents on a real repo.
    order = {
        label: position
        for index in sorted(by_layer)
        for position, label in enumerate(sorted(by_layer[index]))
    }
    for _ in range(4):
        for index in sorted(by_layer):
            def barycentre(name: str, index: int = index) -> tuple[float, str]:
                neighbours = [
                    order[other]
                    for (source, target) in weights
                    for other in ((target,) if source == name else (source,) if target == name else ())
                    if other in order and layer[other] != index
                ]
                return (
                    sum(neighbours) / len(neighbours) if neighbours else float(order[name]),
                    name,
                )

            by_layer[index].sort(key=barycentre)
            for position, label in enumerate(by_layer[index]):
                order[label] = position

    shown = {label: display_label(label) for label in labels}
    widths = {label: box_width(shown[label]) for label in labels}
    row_h = ROW + 14 + 46
    positions: dict[str, tuple[float, float]] = {}
    max_width = 0.0
    for index in sorted(by_layer):
        row = by_layer[index]
        total = sum(widths[n] for n in row) + 30 * (len(row) - 1)
        max_width = max(max_width, total)
        x = 24.0
        for name in row:
            positions[name] = (x, 56.0 + index * row_h)
            x += widths[name] + 30
    height = 56.0 + (max(by_layer) + 1) * row_h + 30
    width = max_width + 48

    box_h = ROW + 14

    def gutter(index: int, step: int) -> float:
        """Midway between row `index` and the next row in `step`'s direction."""
        return (56.0 + index * row_h + 56.0 + (index + step) * row_h + box_h) / 2

    # One colour per source package, so a line can be traced back to the box it
    # leaves — what context_svg already does, and what this picture needs most:
    # on nvm-forecast it drew 18 edges with 18 crossings, every one of them the
    # same grey, so at each crossing nothing said which line was which.
    package_neighbours: dict[str, list[str]] = {name: [] for name in positions}
    for source, target in weights:
        package_neighbours.setdefault(source, []).append(target)
        package_neighbours.setdefault(target, []).append(source)
    palette_slot = graph_colours(sorted(package_neighbours), package_neighbours)
    out_total = {name: sum(1 for s, _ in weights if s == name) for name in positions}
    in_total = {name: sum(1 for _, t in weights if t == name) for name in positions}
    out_slot: dict[str, int] = {}
    in_slot: dict[str, int] = {}

    body = []
    placed_labels: list[tuple[float, float]] = []
    for (source, target), weight in sorted(weights.items()):
        sx, sy = positions[source]
        tx, ty = positions[target]
        mutual = (target, source) in weights
        # Both directions of a mutual pair share a midpoint, so their labels used
        # to print on top of each other. Bow them apart and split the labels.
        bow = 26 if mutual and source > target else (-26 if mutual else 0)
        share = 0.34 if bow >= 0 else 0.66
        up = positions[source][1] > positions[target][1]

        # Fan the ends across the box edge rather than stacking every arrow on its
        # middle: three packages importing the same one drew that last stretch as
        # a single line, which is the stretch that says what imports what.
        out_slot[source] = out_slot.get(source, 0) + 1
        in_slot[target] = in_slot.get(target, 0) + 1
        x1 = sx + widths[source] * out_slot[source] / (out_total[source] + 1)
        x2 = tx + widths[target] * in_slot[target] / (in_total[target] + 1)
        y1 = sy + (0 if up else box_h)
        y2 = ty + (box_h if up else 0)

        colour = PALETTE[palette_slot[source]]
        step = -1 if up else 1
        if layer[source] == layer[target]:
            # Both ends in one row — which is where a cycle puts every one of its
            # members. Drawn like an inter-row edge, out of one box's bottom and up
            # into the other's top, it arcs across whatever package stands between
            # them: `frontend -> pages` went under `components` that way, in both
            # directions. Dip into the gutter below the row instead and come back up
            # to the other box's underside. A mutual pair takes two depths, so the
            # two arcs stay apart.
            rows_below = [index for index in by_layer if index > layer[source]]
            lane = (gutter(layer[source], 1) if rows_below
                    else positions[source][1] + box_h + 30)
            dip = lane - 14 if bow < 0 else lane
            y1 = y2 = positions[source][1] + box_h
            d = f"M{x1:.1f},{y1:.1f} C{x1:.1f},{dip:.1f} {x2:.1f},{dip:.1f} {x2:.1f},{y2:.1f}"
        elif abs(layer[source] - layer[target]) > 1:
            # Reaching past a whole row: into the gutter, down a corridor no box
            # occupies, and back up under the target.
            lane = corridor(
                [
                    [(positions[n][0], positions[n][0] + widths[n]) for n in by_layer[index]]
                    for index in sorted(by_layer)
                    if min(layer[source], layer[target]) < index < max(layer[source], layer[target])
                ],
                (x1 + x2) / 2,
                30,
            )
            near, far = gutter(layer[source], step), gutter(layer[target], -step)
            d = (f"M{x1:.1f},{y1:.1f} L{x1:.1f},{near:.1f} L{lane:.1f},{near:.1f} "
                 f"L{lane:.1f},{far:.1f} L{x2:.1f},{far:.1f} L{x2:.1f},{y2:.1f}")
        else:
            cy = (y1 + y2) / 2
            d = (f"M{x1:.1f},{y1:.1f} C{x1 + bow:.1f},{cy:.1f} {x2 + bow:.1f},{cy:.1f} "
                 f"{x2:.1f},{y2:.1f}")
        body.append(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="1.4" '
                    f'opacity="0.85" marker-end="url(#m{palette_slot[source]})"/>')
        lx = x1 + (x2 - x1) * share + bow + 8
        ly = y1 + (y2 - y1) * share
        # The label was "N imports" beside every edge. Measured on a real repo that put
        # two labels on top of each other twice and two more on top of a node box, and
        # a weight of 1 read "1 imports". The count alone is a quarter of the width, so
        # it fits between the boxes; the legend below says what the number means.
        # Nudge it clear of a box band and of any label already placed nearby.
        band = next(
            (
                (by, by + ROW + 14)
                for (bx, by) in positions.values()
                if by - 4 <= ly <= by + ROW + 18
            ),
            None,
        )
        if band is not None:
            ly = band[0] - 8 if ly < (band[0] + band[1]) / 2 else band[1] + 14
        text = str(weight)
        while any(abs(ly - py) < 12 and abs(lx - px) < 22 for px, py in placed_labels):
            ly -= 13
        placed_labels.append((lx, ly))
        body.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="{FS - 3}" fill="{PAPER}" '
            f'stroke="{PAPER}" stroke-width="3.2" stroke-linejoin="round" '
            f'paint-order="stroke">{text}</text>'
        )
        body.append(f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="{FS - 3}" '
                    f'fill="{MUTED}">{text}</text>')
    for name in labels:
        x, y = positions[name]
        body.append(draw_box(x, y, widths[name], shown[name], role(name)))
    body.append(f'<text x="24" y="{height - 12:.1f}" font-size="{FS - 2}" fill="{MUTED}">'
                f"read top to bottom: entry points depend downward on foundations · "
                f"the number on an edge is how many file-level imports it stands for</text>")
    return svg_document(
        width, height, "Package layering and coupling weight", "\n".join(body),
        defs=palette_markers(),
    )
