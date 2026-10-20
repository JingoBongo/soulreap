"""Placing boxes: columns within a row, and corridors across rows.

place_columns and pack_columns decide the x of every box in a layered drawing, and
free_lanes / corridor find a vertical lane clear of boxes for an edge that spans a whole
row.
"""

from __future__ import annotations


def place_columns(
    rows: list[list[str]],
    widths: dict[str, float],
    neighbours: dict[str, list[str]],
    left: float = 24.0,
    gap: float = 40.0,
    passes: int = 4,
) -> dict[str, float]:
    """x per node: the layered algorithm's node-placement phase, which was missing.

    Layering and crossing minimisation decide only WHICH row a box sits in and in what
    ORDER. Packing each row from the left margin then leaves a lone table at x=24 while
    the table referencing it sits 1100px away, and the edge between them crosses the
    page — nvm-forecast's schema drew exactly that.

    ELK's default for this phase is Brandes-Köpf and `dot` uses a network simplex; both
    are heavy for thirty boxes. This is the simpler classic: a node wants the median x of
    its neighbours in the neighbouring rows, and the row is then projected back onto legal
    positions — left to right, then right to left — so the order the crossing pass chose
    survives and boxes never overlap.

    The result is a *candidate*, not the answer: `erd_svg` draws it alongside the plain
    packing and keeps whichever measures better once drawn.
    """
    x = pack_columns(rows, widths, left=left, gap=gap)

    def project(row: list[str], wanted: dict[str, float]) -> None:
        cursor = left
        for name in row:
            x[name] = max(cursor, wanted.get(name, x[name]) - widths[name] / 2)
            cursor = x[name] + widths[name] + gap
        for index in range(len(row) - 2, -1, -1):
            name, right = row[index], row[index + 1]
            x[name] = min(x[name], x[right] - gap - widths[name])
        cursor = left
        for name in row:  # the backward pass can push the head left of the margin
            x[name] = max(x[name], cursor)
            cursor = x[name] + widths[name] + gap

    for sweep in range(passes):
        for row in (rows if sweep % 2 == 0 else list(reversed(rows))):
            wanted: dict[str, float] = {}
            for name in row:
                anchors = sorted(
                    x[other] + widths[other] / 2
                    for other in neighbours.get(name, ())
                    if other in x and other not in row
                )
                if anchors:
                    middle = len(anchors) // 2
                    wanted[name] = (
                        anchors[middle]
                        if len(anchors) % 2
                        else (anchors[middle - 1] + anchors[middle]) / 2
                    )
            if wanted:
                project(row, wanted)

    shift = min(x.values(), default=left) - left
    return {name: value - shift for name, value in x.items()}


def pack_columns(
    rows: list[list[str]], widths: dict[str, float], left: float = 24.0, gap: float = 40.0
) -> dict[str, float]:
    """Every row packed from the left margin — the layout this drawer always had."""
    x: dict[str, float] = {}
    for row in rows:
        cursor = left
        for name in row:
            x[name] = cursor
            cursor += widths[name] + gap
    return x


def free_lanes(spans: list[tuple[float, float]], margin: float) -> list[tuple[float, float]]:
    """Vertical corridors across one row of boxes that no box occupies."""
    lanes: list[tuple[float, float]] = []
    cursor = 0.0
    for left, right in sorted(spans):
        if left - cursor >= 16:
            lanes.append((cursor, left))
        cursor = max(cursor, right)
    lanes.append((cursor, cursor + margin))
    return lanes


def corridor(rows: list[list[tuple[float, float]]], prefer: float, margin: float) -> float:
    """An x free of boxes in every row given, as near `prefer` as it gets.

    What an edge reaching past a whole row needs: these drawers place a box
    wherever the ordering pass puts it and then draw the edge straight, so an
    edge spanning two layers is drawn through whatever stands in the middle
    row. Sending it down a corridor instead is the only way to keep it off,
    short of routing every edge.

    Every row starts at the same left inset, so the left margin is free in all
    of them and there is always an answer; the search only decides how far out
    of its way the edge goes.
    """
    lanes: list[tuple[float, float]] | None = None
    for spans in rows:
        here = free_lanes(spans, margin)
        lanes = here if lanes is None else [
            (max(a, c), min(b, d))
            for a, b in lanes
            for c, d in here
            if min(b, d) - max(a, c) >= 16
        ]
    if not lanes:
        return 12.0
    low, high = min(lanes, key=lambda lane: abs((lane[0] + lane[1]) / 2 - prefer))
    return min(max(prefer, low + 8), high - 8)
