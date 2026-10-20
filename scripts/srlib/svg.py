"""The generated diagrams as SVG, drawn from the same facts as the mermaid.

Mermaid renders only where a mermaid engine exists. SVG opens in any browser,
any image viewer, GitHub and a PDF, and being vector it zooms without limit —
which is the actual request behind "give me a high-resolution picture".

Nothing here parses mermaid: both pictures are built from facts.json through the
same helpers diagrams.py uses, so the two can never disagree. No network, because
an architecture diagram of a GDPR-scoped repo is not something to hand to a
rendering service.
"""

from __future__ import annotations

from .diagrams import (
    _hosts_from_io,
    _services_from_imports,
    context_depth,
    diagram_paths,
    group_key,
)
from .svg_base import (  # noqa: F401  (re-exported)
    BOX,
    CW,
    EDGE,
    FONT,
    FS,
    INK,
    MUTED,
    PAD,
    PALETTE,
    PAPER,
    ROW,
    box_width,
    draw_box,
    esc,
    palette_markers,
    svg_document,
)
from .svg_graph import drawing_score, flatten_path, graph_colours  # noqa: F401  (re-exported)
from .svg_layout import corridor, free_lanes, pack_columns, place_columns  # noqa: F401  (re-exported)
from .svg_packages import packages_svg  # noqa: F401  (re-exported)

# ----------------------------------------------------------------- context ---


def context_svg(facts: dict) -> str:
    """Packages on the left, the systems they reach on the right.

    Colour keys the SOURCE package, not the call kind: with 26 edges in two
    colours nothing can be traced, and "which of my packages talks to Graph" is
    the question this picture exists to answer. The kind survives as line style —
    solid for a plain HTTP call, dashed for anything else — and both are named in
    the legend. The right column is then reordered by the average height of each
    system's sources, which is the standard barycentre pass for cutting crossings.
    """
    paths = diagram_paths(facts)
    depth = context_depth(paths)
    edges: dict[tuple[str, str], str] = {}
    systems: dict[str, str] = {}
    for path in paths:
        blob = facts[path]
        for name, kind in _services_from_imports(blob.get("imports", [])) | _hosts_from_io(blob.get("io", {})):
            systems[name] = kind
            edges[(group_key(path, depth), name)] = kind

    packages = sorted({src for src, _ in edges})
    targets = sorted(systems)
    colour = {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(packages)}
    kinds = sorted(set(edges.values()))

    # Barycentre: put each system next to the packages that reach it, then let the
    # packages drift towards their systems. Two passes is enough at this size.
    for _ in range(4):
        rank = {name: i for i, name in enumerate(packages)}
        targets.sort(key=lambda s: (
            sum(rank[src] for (src, dst) in edges if dst == s) / max(1, sum(1 for (_, dst) in edges if dst == s)),
            s,
        ))
        trank = {name: i for i, name in enumerate(targets)}
        packages.sort(key=lambda p: (
            sum(trank[dst] for (src, dst) in edges if src == p) / max(1, sum(1 for (src, _) in edges if src == p)),
            p,
        ))

    lw = max((box_width(p) for p in packages), default=120)
    rw = max((box_width(s) for s in targets), default=120)
    gap = 260
    left_x, right_x = 24.0, 24.0 + lw + gap
    top = 56.0
    step = ROW + 12
    rows = max(len(packages), len(targets))
    height = top + rows * step + 86
    width = right_x + rw + 24

    ly = {p: top + i * step + (rows - len(packages)) * step / 2 for i, p in enumerate(packages)}
    ry = {s: top + i * step + (rows - len(targets)) * step / 2 for i, s in enumerate(targets)}

    body = []
    for (src, dst), kind in sorted(edges.items(), key=lambda kv: (packages.index(kv[0][0]), kv[0][1])):
        y1, y2 = ly[src] + ROW / 2, ry[dst] + ROW / 2
        x1, x2 = left_x + lw, right_x
        mid = (x1 + x2) / 2
        dash = "" if kind == "HTTP" else ' stroke-dasharray="6 4"'
        body.append(
            f'<path d="M{x1:.1f},{y1:.1f} C{mid:.1f},{y1:.1f} {mid:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" '
            f'fill="none" stroke="{colour[src]}" stroke-width="1.6" opacity="0.75"{dash}/>'
        )
    for name in packages:
        reach = sum(1 for (src, _) in edges if src == name)
        body.append(draw_box(left_x, ly[name], lw, name, f"reaches {reach}", accent=colour[name]))
    for name in targets:
        sources = sorted({src for (src, dst) in edges if dst == name})
        swatch = "".join(
            f'<rect x="{right_x - 10 - 7 * (len(sources) - i):.1f}" y="{ry[name] + 12:.1f}" width="5" height="12" '
            f'rx="1" fill="{colour[src]}"/>'
            for i, src in enumerate(sources)
        )
        body.append(swatch + draw_box(right_x, ry[name], rw, name))

    legend_y = height - 46
    body.append(f'<text x="24" y="{legend_y:.1f}" font-size="{FS - 2}" fill="{MUTED}">'
                f"left: packages in this repo · right: systems outside it · line colour = which package makes the call"
                f" · the chips beside a system repeat the colours that reach it</text>")
    lx = 24.0
    for kind in kinds:
        dash = "" if kind == "HTTP" else ' stroke-dasharray="6 4"'
        body.append(f'<line x1="{lx:.1f}" y1="{legend_y + 20:.1f}" x2="{lx + 26:.1f}" y2="{legend_y + 20:.1f}" '
                    f'stroke="{INK}" stroke-width="2"{dash}/>')
        body.append(f'<text x="{lx + 32:.1f}" y="{legend_y + 24:.1f}" font-size="{FS - 2}" '
                    f'fill="{INK}">{esc(kind)}</text>')
        lx += 40 + box_width(kind)

    return svg_document(width, height, "External systems this code talks to", "\n".join(body))


# ----------------------------------------------------------------------- erd ---

# Above this many tables, columns fold down to keys only. Box width drags long
# edges along with it, and long edges get drawn straight through other boxes —
# the same trouble display_label's packaging fold exists to avoid.
ERD_FOLD_ABOVE = 15
ERD_ROW = 18


def _erd_rows(table: dict, fold: bool) -> list[str]:
    """The lines inside a table's box: column, type, and key markers."""
    columns = table.get("columns", [])
    shown = [c for c in columns if c.get("pk") or c.get("fk")] if fold else list(columns)
    rows = []
    for column in shown:
        mark = " PK" if column.get("pk") else (" FK" if column.get("fk") else "")
        rows.append(f"{column['name']} : {column.get('type') or '?'}{mark}")
    hidden = len(columns) - len(shown)
    if hidden > 0:
        rows.append(f"+{hidden} columns")
    return rows


def erd_svg(database: dict, _columns: dict[str, float] | None = None) -> str:
    """One database's schema: a box per table, an edge per foreign key.

    Drawn from the same facts as the .mmd, so the picture and the text can
    never disagree — the same trick context and packages already use.
    """
    tables = sorted(database.get("tables", []), key=lambda t: t["name"])
    names = [table["name"] for table in tables]
    if len(names) != len(set(names)):
        # Same contract as erd_mermaid: silently merging two different tables
        # into one box would draw a confidently wrong picture.
        duplicate = next(name for name in names if names.count(name) > 1)
        raise ValueError(f"erd_svg: duplicate table name {duplicate!r} — call group_databases first")
    fold = len(tables) > ERD_FOLD_ABOVE
    known = {table["name"]: table for table in tables}

    rows = {table["name"]: _erd_rows(table, fold) for table in tables}
    widths = {
        name: max([box_width(name)] + [box_width(line) for line in lines])
        for name, lines in rows.items()
    }

    # Parent beneath child: the table being referenced sits a layer lower.
    depth: dict[str, int] = {table["name"]: 0 for table in tables}
    for _ in range(len(tables)):
        changed = False
        for table in tables:
            for column in table.get("columns", []):
                parent = (column.get("fk") or "").split(".")[0]
                if parent in known and parent != table["name"]:
                    want = depth[table["name"]] + 1
                    if depth[parent] < want:
                        depth[parent] = want
                        changed = True
        if not changed:
            break

    links = sorted({
        (table["name"], (column.get("fk") or "").split(".")[0])
        for table in tables
        for column in table.get("columns", [])
        if (column.get("fk") or "").split(".")[0] in known
        and (column.get("fk") or "").split(".")[0] != table["name"]
    })

    layers: dict[int, list[str]] = {}
    for name in sorted(depth, key=lambda n: (depth[n], n)):
        layers.setdefault(depth[name], []).append(name)

    # Sugiyama's ordering step, the pass mermaid_svg already runs on every
    # flowchart: each table moves to the average position of the tables it links
    # to, so a row lines up under the row referencing it. Alphabetical order is
    # not a layout — it put `import_batches` and `people` at opposite ends of
    # nvm-forecast's schema with a full-width arrow between them.
    neighbours: dict[str, list[str]] = {name: [] for name in known}
    for child, parent in links:
        neighbours[child].append(parent)
        neighbours[parent].append(child)

    for _ in range(8):
        position = {name: index for names in layers.values() for index, name in enumerate(names)}
        for names in layers.values():
            names.sort(key=lambda n: (
                sum(position[other] for other in neighbours[n]) / len(neighbours[n])
                if neighbours[n] else position[n],
                n,
            ))

    top, gap_x, gap_y = 56.0, 40.0, 34.0
    ordered_rows = [layers[index] for index in sorted(layers)]
    if _columns is None:
        # Draw both layouts and keep the one that measures better AS DRAWN. Scoring a
        # layout on its own model is a guess: by straight lines between box centres the
        # relaxed one looked equal or better, and it drew six crossings where packing
        # drew four, because the drawer fans ports and routes long edges down corridors.
        candidates = [
            pack_columns(ordered_rows, widths, left=24.0, gap=gap_x),
            place_columns(ordered_rows, widths, neighbours, left=24.0, gap=gap_x),
        ]
        return min((erd_svg(database, columns) for columns in candidates), key=drawing_score)

    placed: dict[str, tuple[float, float, float, float]] = {}
    y = top
    canvas = 0.0
    for names in ordered_rows:
        tallest = 0.0
        for name in names:
            height = ROW + len(rows[name]) * ERD_ROW + 10
            placed[name] = (_columns[name], y, widths[name], height)
            canvas = max(canvas, _columns[name] + widths[name] + gap_x)
            tallest = max(tallest, height)
        y += tallest + gap_y

    # Where each row really begins and ends. A horizontal run belongs in the
    # gutter BETWEEN two rows, never at the midpoint between the two boxes it
    # joins: a short box beside a tall one otherwise sends the run straight
    # through its neighbour, which is where three of nvm-forecast's five box
    # crossings came from.
    row_top = {index: min(placed[n][1] for n in names) for index, names in layers.items()}
    row_bottom = {
        index: max(placed[n][1] + placed[n][3] for n in names) for index, names in layers.items()
    }

    # A layer can end up empty: a table sitting at depth 2 is pushed to 3 by a
    # second child, and nothing is left at 2. Neighbouring rows are therefore
    # looked up in the list of layers that EXIST, never as index ± 1 — the
    # packages drawer crashed on exactly that arithmetic.
    order = sorted(layers)
    below = {index: order[position + 1] for position, index in enumerate(order[:-1])}
    above = {index: order[position - 1] for position, index in enumerate(order) if position}

    def row_spans(index: int) -> list[tuple[float, float]]:
        return [(placed[n][0], placed[n][0] + placed[n][2]) for n in layers[index]]

    # One palette colour per table, carried by its box border AND by every arrow
    # leaving it, so a line can be traced back to the table it comes from. A
    # single grey for every arrow says nothing at a crossing about which line is
    # which — the reason mermaid_svg colours by source too.
    palette_slot = graph_colours(sorted(known), neighbours)

    out_total: dict[str, int] = {}
    in_total: dict[str, int] = {}
    for child, parent in links:
        out_total[child] = out_total.get(child, 0) + 1
        in_total[parent] = in_total.get(parent, 0) + 1

    # Hand the slots out in the order the OTHER end sits on the page, not in name
    # order: two arrows leaving one table for tables on opposite sides otherwise
    # cross each other immediately, right where they leave the box.
    def middle(name: str) -> float:
        return placed[name][0] + placed[name][2] / 2

    out_slot = {
        edge: slot
        for name in known
        for slot, edge in enumerate(
            sorted((e for e in links if e[0] == name), key=lambda e: middle(e[1])), 1
        )
    }
    in_slot = {
        edge: slot
        for name in known
        for slot, edge in enumerate(
            sorted((e for e in links if e[1] == name), key=lambda e: middle(e[0])), 1
        )
    }

    body = []
    for child, parent in links:
        cx, cy, cw, ch = placed[child]
        px, py, pw, _ph = placed[parent]

        # Fan the ends across the box edge instead of stacking every arrow on the
        # middle of it. The last stretch before a box is the one a reader follows
        # to see what points at what, and three foreign keys into one table drew
        # that stretch as a single line.
        x1 = cx + cw * out_slot[(child, parent)] / (out_total[child] + 1)
        x2 = px + pw * in_slot[(child, parent)] / (in_total[parent] + 1)
        y1, y2 = cy + ch, py
        near = (row_bottom[depth[child]] + row_top[below[depth[child]]]) / 2

        if below[depth[child]] == depth[parent]:
            # Straight down the child's own column until clear of the row, THEN the
            # curve. Boxes in a row share a top but not a bottom, so a short table's
            # edge leaves higher than a tall neighbour's bottom — and a curve from
            # there is already travelling sideways while still level with that
            # neighbour, which is how `log_hours -> people` was drawn under `events`.
            # The drop itself is safe: inside the child's own width, its row holds no
            # other box.
            mid = (near + y2) / 2
            d = (f"M{x1:.1f},{y1:.1f} L{x1:.1f},{near:.1f} "
                 f"C{x1:.1f},{mid:.1f} {x2:.1f},{mid:.1f} {x2:.1f},{y2:.1f}")
        else:
            # Reaching past a whole row: drop into the gutter, travel down a
            # corridor no box occupies, and come back under the parent. Drawn
            # with corners rather than as a curve, because an edge that travels
            # should look like it travelled.
            lane = corridor(
                [row_spans(index) for index in order if depth[child] < index < depth[parent]],
                (x1 + x2) / 2,
                gap_x,
            )
            far = (row_bottom[above[depth[parent]]] + row_top[depth[parent]]) / 2
            d = (
                f"M{x1:.1f},{y1:.1f} L{x1:.1f},{near:.1f} L{lane:.1f},{near:.1f} "
                f"L{lane:.1f},{far:.1f} L{x2:.1f},{far:.1f} L{x2:.1f},{y2:.1f}"
            )

        body.append(
            f'<path d="{d}" fill="none" stroke="{PALETTE[palette_slot[child]]}" '
            f'stroke-width="1.6" opacity="0.85" marker-end="url(#m{palette_slot[child]})"/>'
        )

    for name in sorted(placed):
        # `bx`/`by`/`bw`/`bh`, never `x`/`y`/`width`/`height`: those names outside
        # hold the layout's own bottom-right corner, computed by the loop above.
        # An earlier version reused the name `y` here and clobbered it with
        # whichever box was drawn last alphabetically — the canvas height ended
        # up measured from a random table instead of the diagram's real bottom,
        # and actual content and the caption ran past the declared viewBox.
        bx, by, bw, bh = placed[name]
        accent = PALETTE[palette_slot[name]]
        body.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="6" '
            f'fill="{BOX}" stroke="{accent}" stroke-width="1.2"/>'
        )
        body.append(
            f'<text x="{bx + PAD:.1f}" y="{by + 21:.1f}" font-family="{FONT}" font-size="{FS}" '
            f'fill="{INK}">{esc(name)}</text>'
        )
        for line_index, line in enumerate(rows[name]):
            body.append(
                f'<text x="{bx + PAD:.1f}" y="{by + ROW + 12 + line_index * ERD_ROW:.1f}" '
                f'font-family="{FONT}" font-size="{FS - 2}" fill="{MUTED}">{esc(line)}</text>'
            )

    width = canvas + 24
    height = y + 30
    note = ("columns folded to keys only" if fold
            else "an arrow runs from the foreign key to the table it references")
    body.append(
        f'<text x="24" y="{height - 12:.1f}" font-size="{FS - 2}" fill="{MUTED}">{esc(note)}</text>'
    )
    return svg_document(
        width, height, f"Schema of database {database['name']}", "\n".join(body),
        defs=palette_markers(),
    )
