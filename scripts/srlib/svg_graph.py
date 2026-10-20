"""Colouring and measuring a drawn graph.

graph_colours hands each node a palette slot so nothing a reader must tell apart shares
one; flatten_path turns an SVG path into straight segments, and drawing_score uses it to
count the crossings a reader cannot untangle, all crossings and the ink, so a drawer can
keep the better of two candidate layouts.
"""

from __future__ import annotations

import colorsys
import re

from .svg_base import PALETTE


def graph_colours(names: list[str], neighbours: dict[str, list[str]]) -> dict[str, int]:
    """A palette slot per node, so that nothing a reader must tell apart shares one.

    Greedy colouring (Welsh-Powell: busiest node first) over the SQUARE of the graph —
    two nodes conflict when they are neighbours *or* share one. The map-colouring rule
    alone is not enough here: the pair a reader actually has to separate is two edges
    meeting at the same box, and their sources need not touch each other. On a real
    schema this costs six colours instead of three, and removes both of the crossings
    that were unreadable.

    Slots wrap when a graph is denser than the palette: a clique of twelve cannot be
    coloured with eight, and a drawing that raises instead of repeating a colour is
    worse than one that repeats.
    """
    conflicts: dict[str, set[str]] = {}
    for name in names:
        near = set(neighbours.get(name, ()))
        for other in list(near):
            near |= set(neighbours.get(other, ()))
        conflicts[name] = near - {name}

    slots: dict[str, int] = {}
    for name in sorted(names, key=lambda n: (-len(conflicts[n]), n)):
        taken = {slots[other] for other in conflicts[name] if other in slots}
        slots[name] = next(
            (slot for slot in range(len(PALETTE)) if slot not in taken),
            len(slots) % len(PALETTE),
        )
    return slots


def flatten_path(d: str) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """An SVG path's `d` as straight segments, with curves SAMPLED.

    A curve's coordinate list is its control polygon, which is not the shape on the
    page: the context drawer's `C mid,y1 mid,y2 x2,y2` has a polygon running the full
    height of the picture while the drawn curve stays inside the channel between the
    columns. Treating those points as a polyline both invents length that was never
    drawn and places the line where the curve never goes, so every geometry number
    taken that way is wrong. Sampling gives the real path.

    Handles M/L/H/V/C/Q and their relative forms, which is everything these renderers
    emit. An arc (A) is not sampled — no drawer here produces one — and its segment is
    approximated by the chord rather than dropped.
    """
    tokens = re.findall(r"([MmLlHhVvCcQqAaZz])|([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)", d)
    out: list[tuple[tuple[float, float], tuple[float, float]]] = []
    numbers: list[float] = []
    command = ""
    cursor = (0.0, 0.0)
    start = (0.0, 0.0)

    def cubic(p0, p1, p2, p3, steps: int = 16):
        pts = []
        for i in range(1, steps + 1):
            t = i / steps
            u = 1 - t
            pts.append((
                u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
            ))
        return pts

    def quad(p0, p1, p2, steps: int = 12):
        pts = []
        for i in range(1, steps + 1):
            t = i / steps
            u = 1 - t
            pts.append((
                u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
            ))
        return pts

    def emit(points: list[tuple[float, float]]) -> None:
        nonlocal cursor
        for point in points:
            if point != cursor:
                out.append((cursor, point))
            cursor = point

    def flush() -> None:
        nonlocal cursor, start, numbers
        rel = command.islower()
        base = cursor
        upper = command.upper()
        if upper == "M" and len(numbers) >= 2:
            cursor = (base[0] + numbers[0], base[1] + numbers[1]) if rel else (numbers[0], numbers[1])
            start = cursor
            emit([(cursor[0] + numbers[i], cursor[1] + numbers[i + 1]) if rel else (numbers[i], numbers[i + 1])
                  for i in range(2, len(numbers) - 1, 2)])
        elif upper == "L":
            emit([(cursor[0] + numbers[i], cursor[1] + numbers[i + 1]) if rel else (numbers[i], numbers[i + 1])
                  for i in range(0, len(numbers) - 1, 2)])
        elif upper == "H":
            for value in numbers:
                emit([(cursor[0] + value if rel else value, cursor[1])])
        elif upper == "V":
            for value in numbers:
                emit([(cursor[0], cursor[1] + value if rel else value)])
        elif upper == "C":
            for i in range(0, len(numbers) - 5, 6):
                c1, c2, end = ((numbers[i], numbers[i + 1]), (numbers[i + 2], numbers[i + 3]),
                               (numbers[i + 4], numbers[i + 5]))
                if rel:
                    c1 = (cursor[0] + c1[0], cursor[1] + c1[1])
                    c2 = (cursor[0] + c2[0], cursor[1] + c2[1])
                    end = (cursor[0] + end[0], cursor[1] + end[1])
                emit(cubic(cursor, c1, c2, end))
        elif upper == "Q":
            for i in range(0, len(numbers) - 3, 4):
                c1, end = (numbers[i], numbers[i + 1]), (numbers[i + 2], numbers[i + 3])
                if rel:
                    c1 = (cursor[0] + c1[0], cursor[1] + c1[1])
                    end = (cursor[0] + end[0], cursor[1] + end[1])
                emit(quad(cursor, c1, end))
        elif upper == "A":
            for i in range(0, len(numbers) - 6, 7):
                end = (numbers[i + 5], numbers[i + 6])
                emit([(cursor[0] + end[0], cursor[1] + end[1]) if rel else end])
        elif upper == "Z":
            emit([start])
        numbers = []

    for letter, number in tokens:
        if letter:
            if command:
                flush()
            command = letter
        else:
            numbers.append(float(number))
    if command:
        flush()
    return out


def drawing_score(svg: str) -> tuple[int, int, float]:
    """(crossings a reader cannot untangle, all crossings, ink) — lower is better.

    Measured on the finished drawing rather than on a model of it. A layout scored by
    straight lines between box centres looked equal or better and drew six crossings
    where the plain packing drew four: this drawer fans ports across a box edge and
    sends long edges down corridors, so what it paints is not what a straight-line
    model predicts.
    """
    edges = []
    for match in re.finditer(r'<path d="([^"]+)"[^>]*stroke="#([0-9a-fA-F]{6})"', svg):
        flattened = flatten_path(match.group(1))
        if not flattened:
            continue
        raw = match.group(2)
        red, green, blue = (int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
        edges.append((flattened, colorsys.rgb_to_hls(red, green, blue)[0] * 360))

    def crosses(a, b) -> bool:
        (x1, y1), (x2, y2) = a
        (x3, y3), (x4, y4) = b
        denom = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
        if abs(denom) < 1e-9:
            return False
        t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / denom
        u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / denom
        return 0.02 < t < 0.98 and 0.02 < u < 0.98

    crossings = confusable = 0
    for index, (first, hue_a) in enumerate(edges):
        for second, hue_b in edges[index + 1:]:
            if not any(crosses(one, other) for one in first for other in second):
                continue
            crossings += 1
            delta = abs(hue_a - hue_b) % 360
            if min(delta, 360 - delta) < 60.0:
                confusable += 1
    ink = sum(
        ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
        for edge, _hue in edges
        for a, b in edge
    )
    return confusable, crossings, ink
