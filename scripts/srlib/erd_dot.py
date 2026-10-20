"""ERD drawn by Graphviz, when its wheel is present.

The hand-written drawer in `svg.py` lays out every other diagram here and keeps doing
so: context, packages and the hand-written flowcharts have no off-the-shelf engine
worth the dependency. A schema is the exception — it is the most standard diagram
there is, and `dot` has had thirty years of work put into it.

Every setting below was chosen by measuring drawings of one real ten-table schema and
one invented twenty-three-table one, not by taste:

- **columns in full, arrows from the foreign key to the primary key it references.**
  Folding to keys halves the ink, and a reader asked for the fields; the fields won.
- **`rankdir=TB` with `ratio=0.5`** — screens are landscape. Left-to-right ranking
  makes the picture *taller*, not wider: tables stack inside a rank and our boxes are
  tall. `ratio` widens without distorting — the SVG scale stays uniform.
- **tight `nodesep`/`ranksep`** — shrank the canvas by 7% with no extra crossings.
- **a section per declaring file, always, labelled with the file name.** Sections cost
  crossings (measured: +40% on ten tables, +57% on twenty-three) and buy the thing
  crossings cannot: knowing what belongs with what. That trade is what the empirical
  graph-drawing literature describes, and it is the one a reader asked for.
- **colours from the graph, not the alphabet** — see `graph_colours`.

`pygraphviz` 2.0 ships the Graphviz binaries inside its wheel, so this costs a pip
dependency and no system install. When the import fails — an unusual platform, a
stripped environment — `erd_dot_svg` returns None and the caller draws it itself.
"""

from __future__ import annotations

import html

from .erd_dot_common import (  # noqa: F401  (re-exported)
    FONT_SIZE,
    NODESEP,
    RANKDIR,
    RANKSEP,
    RATIO,
    SECTION_FONT_SIZE,
    SPLINES,
    TAG_SIZE,
    _best_routing,
    _is_key,
    _key_tag,
    _node_label,
    section_labels,
)
from .erd_dot_observed import observed_dot_svg, observed_groups  # noqa: F401  (re-exported)
from .erd_dot_structures import structure_groups, structure_slug, structures_dot_svg  # noqa: F401  (re-exported)
from .erd_dot_tasks import task_graph, tasks_dot_svg  # noqa: F401  (re-exported)
from .svg import PALETTE, graph_colours


def erd_dot_svg(database: dict) -> str | None:
    """One database as an SVG, or None when Graphviz is not installed."""
    try:
        import pygraphviz
    except Exception:  # any import failure means "draw it yourself"
        return None

    tables = {table["name"]: table for table in database.get("tables", [])}
    if not tables:
        return None

    neighbours: dict[str, list[str]] = {name: [] for name in tables}
    for name, table in tables.items():
        for column in table.get("columns", []):
            parent = (column.get("fk") or "").split(".")[0]
            if parent in tables and parent != name:
                neighbours[name].append(parent)
                neighbours[parent].append(name)
    slot = graph_colours(sorted(tables), neighbours)

    # Where the schema came from belongs in the caption. A migration is what somebody
    # meant to build and a model is what the application believes; a database file is
    # what is actually there, and a reader deciding whether to trust the picture is
    # asking exactly that.
    from_file = all(
        table.get("detector") == "sqlite_file" for table in database.get("tables", [])
    )
    provenance = (
        "read from the database file itself, not from a migration"
        if from_file else
        "a dashed frame is the file that declares those tables"
    )
    graph = pygraphviz.AGraph(
        directed=True, strict=False, rankdir=RANKDIR, splines=SPLINES, ratio=RATIO,
        nodesep=NODESEP, ranksep=RANKSEP, bgcolor="white", compound="true",
        fontsize=str(FONT_SIZE), fontname="Helvetica",
        label=f"Schema of database {database.get('name', '')} · "
              "an arrow runs from the foreign key to the column it references · "
              + provenance,
        labelloc="b", fontcolor="#666666",
    )
    graph.node_attr["fontsize"] = str(FONT_SIZE)
    graph.node_attr["fontname"] = "Helvetica"

    for name, table in sorted(tables.items()):
        graph.add_node(name, shape="plaintext", label=_node_label(name, table))

    # One section per declaring file, including a file that declares a single table:
    # a table outside every frame reads as "belongs nowhere", which is never true.
    owners: dict[str, list[str]] = {}
    for name, table in sorted(tables.items()):
        source = table.get("source")
        if source:
            owners.setdefault(source, []).append(name)
    labels = section_labels(sorted(owners))
    for source, names in sorted(owners.items()):
        graph.add_subgraph(
            names, name=f"cluster_{labels[source].replace('/', '_')}", label=labels[source],
            style="rounded,dashed", color="#9AA0B5", fontcolor="#5A6072",
            fontname="Courier", fontsize=str(SECTION_FONT_SIZE), labelloc="t", penwidth="2",
        )

    for name, table in sorted(tables.items()):
        for column in table.get("columns", []):
            parent, _, parent_column = (column.get("fk") or "").partition(".")
            if parent not in tables:
                continue
            edge = {
                "color": PALETTE[slot[name]],
                "penwidth": "1.7",
                "arrowhead": "crow",
                "arrowsize": "0.8",
                # The port is the ROW — the reader asked for arrows that land on the
                # field, and they do. The SIDE is dot's to choose: pinning east-to-west
                # made an edge leave the right of a table to reach one on its left and
                # wrap all the way back, which is the drawing graphviz reports as
                # `lost events people edge`. Measured on both dogfood schemas, letting
                # it choose is better on every count — 10 tables: 1 through-box and 7
                # crossings become 0 and 7; 23 tables: 10 and 33 become 0 and 25.
                "tailport": column["name"],
            }
            if parent_column:
                edge["headport"] = parent_column
            graph.add_edge(name, parent, **edge)

    return _best_routing(graph)


def context_dot_svg(edges: dict, systems: dict) -> str | None:
    """The context picture, drawn from the same edges the mermaid file states.

    Drawn here rather than by the hand-written drawer because that one reads facts
    directly and therefore knew only two of the five sources: on nvm-forecast it drew
    the generic "SQL database" node that the text had already dropped as redundant,
    and the skill promises the pair can never disagree.

    No `ratio` here, unlike the ERD: an edge label together with `ratio` segfaults this
    Graphviz build inside `gvFreeLayout`, and the label — which source found this system
    — is the only thing on a context edge worth reading. The graph is small enough that
    the aspect looks after itself.
    """
    try:
        import pygraphviz
    except Exception:  # any import failure means "draw nothing", the .mmd still stands
        return None
    if not edges:
        return None

    graph = pygraphviz.AGraph(
        directed=True, strict=False, rankdir="LR", splines="polyline",
        nodesep="0.3", ranksep="1.1", bgcolor="white", fontname="Helvetica", fontsize="14",
        label="What this repository talks to · the label on each arrow says how it was "
              "found: manifest and compose are declarations, config is a setting's name, "
              "import and literal are written in the source, binary is a program it runs",
        labelloc="b", fontcolor="#666666",
    )
    packages = sorted({source for source, _ in edges})
    slot = graph_colours(packages, {name: [] for name in packages})

    inside = graph.add_subgraph(
        name="cluster_repo", label="this repository", style="rounded,dashed",
        color="#9AA0B5", fontcolor="#5A6072", fontname="Courier", fontsize="16", labelloc="t",
    )
    for name in packages:
        inside.add_node(
            name, shape="box", style="rounded,filled", fillcolor="#EEF0FF",
            color=PALETTE[slot[name]], penwidth="1.6", fontname="Helvetica", fontsize="15",
        )
    for name in sorted(systems):
        graph.add_node(
            f"sys::{name}", label=name, shape="ellipse", style="filled", fillcolor="#F4EEE4",
            color="#8A8A8A", fontname="Helvetica", fontsize="15",
        )
    for (source, target), vias in sorted(edges.items()):
        graph.add_edge(
            source, f"sys::{target}",
            label=f"  {systems.get(target, 'external')} · {', '.join(sorted(vias))}  ",
            color=PALETTE[slot[source]], penwidth="1.6", fontsize="11", fontcolor="#5A6072",
            fontname="Helvetica",
        )
    graph.layout(prog="dot")
    return graph.draw(format="svg").decode("utf-8")


# --------------------------------------------------------------- packages ---

# Fill by role, so the layer a box belongs to is legible before any arrow is traced.
# The border keeps the graph colour, which is what an arrow is followed by.
ROLE_FILL = {
    "entry": "#EEF0FF",       # cool, like the ERD header: where requests come in
    "core": "#FFFFFF",
    "foundation": "#F1F1F4",  # grey, the floor everything stands on
    "isolated": "#FAFAFA",
}
WEIGHT_PENWIDTH = ((10, "3.4"), (4, "2.4"), (1, "1.6"))


def _packages_penwidth(weight: int) -> str:
    """Three widths, not a continuous scale: a reader compares thicknesses only when
    they are far apart, and the number is on the label anyway."""
    for threshold, width in WEIGHT_PENWIDTH:
        if weight >= threshold:
            return width
    return "1.6"


def packages_dot_svg(graph: dict, display=lambda name: name) -> str | None:
    """The package layering, drawn by dot from the same graph the .mmd states.

    The last diagram still laid out by hand, and the worst one measured: on
    nvm-forecast the hand drawer produced 71 crossings over 42 edges and 15x the
    canvas diagonal in ink, a tangle by any reading. The same graph through `dot`
    is a Sugiyama layering with proper crossing minimisation — the thing thirty
    years of work went into and a few hundred lines of ours cannot match.

    No `ratio`, for the reason `context_dot_svg` gives: an edge label plus `ratio`
    segfaults this Graphviz build, and the label here is the coupling weight, which
    is half the diagram's title.
    """
    try:
        import pygraphviz
    except Exception:  # no wheel on this platform: the caller draws it instead
        return None
    labels, weights = graph.get("labels") or [], graph.get("weights") or {}
    if not weights:
        return None

    artifacts, roles = graph.get("artifacts") or set(), graph.get("roles") or {}
    dotted = (
        " · a dashed arrow is a mutual dependency between two packages, not an import "
        "cycle: no single pair of files imports each other"
        if artifacts else ""
    )
    picture = pygraphviz.AGraph(
        # `spline`, not the ERD's `curved`: dot refuses to place an edge LABEL under
        # curved and says so on stderr, and the label here is the coupling weight.
        directed=True, strict=False, rankdir=RANKDIR, splines="spline",
        nodesep="0.35", ranksep="0.8", bgcolor="white",
        fontname="Helvetica", fontsize="14", fontcolor="#666666", labelloc="b",
        label="Package layering and coupling weight · top to bottom, entry points "
              "above the foundations they stand on · an arrow's label is how many "
              "file-level imports it stands for" + dotted,
    )
    outgoing: dict[str, list[str]] = {name: [] for name in labels}
    for source, target in weights:
        outgoing.setdefault(source, []).append(target)
    slot = graph_colours(labels, outgoing)

    for name in labels:
        role = roles.get(name, "core")
        picture.add_node(
            name,
            label=f"<<b>{html.escape(display(name))}</b><br/>"
                  f'<font point-size="{TAG_SIZE}" color="#5A6072">{role}</font>>',
            shape="box", style="rounded,filled", fillcolor=ROLE_FILL.get(role, "#FFFFFF"),
            color=PALETTE[slot[name]], penwidth="1.8",
            fontname="Helvetica", fontsize=str(FONT_SIZE - 4), margin="0.22,0.12",
        )
    for (source, target), weight in sorted(weights.items()):
        noun = "import" if weight == 1 else "imports"
        picture.add_edge(
            source, target,
            label=f"  {weight} {noun}  ",
            style="dashed" if (source, target) in artifacts else "solid",
            color=PALETTE[slot[source]], penwidth=_packages_penwidth(weight),
            fontsize="11", fontcolor="#5A6072", fontname="Helvetica",
        )
    picture.layout(prog="dot")
    return picture.draw(format="svg").decode("utf-8")
