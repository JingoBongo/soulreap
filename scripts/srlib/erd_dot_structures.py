"""Structures (dataclasses, pydantic models, NamedTuples) drawn by Graphviz.

One picture per declaring module: the grouping of structures worth drawing, the slug a
module path becomes in a filename, the field-row tag, and `structures_dot_svg`, which
reuses the ERD's entity box and routing from `erd_dot_common`.
"""

from __future__ import annotations

import html
import re

from .erd_dot_common import (
    FONT_SIZE,
    NODESEP,
    RANKDIR,
    RANKSEP,
    RATIO,
    SPLINES,
    _best_routing,
    _node_label,
)
from .svg import PALETTE, graph_colours

# ---------------------------------------------------------- structures ---
#
# The same four things — entity, fields, links, declaring file — read out of a
# class body instead of a table definition, so the same engine draws them. Two
# decisions were made here, and both are about what NOT to draw:
#
# **A separate picture, never the database's.** `structures-<module>.svg` beside
# `erd-<database>.svg`, with its own caption and its own header colour. Sharing a
# canvas was the obvious saving and it is exactly the mistake to avoid: a box on a
# schema diagram is a promise that a row exists somewhere, and a dataclass makes no
# such promise. A reader who cannot tell the contract from the object in memory is
# worse off than one with two pictures.
#
# **One picture per declaring module.** A link between two structures is a Python
# name resolved inside one file — that is all the parser can honestly see, since
# facts are extracted a file at a time and matching `Config` across modules by name
# alone would invent links between unrelated classes. So a component never spans
# files, and a per-module picture is the largest one that is not a lie. It also
# bounds the noise: a repository with two hundred dataclasses draws small pictures
# of the modules that group them, not one wall of boxes.
#
# **Only what is connected.** A structure that neither points at another nor is
# pointed at says nothing a diagram can show that its Entities row does not say
# better, and two hundred unconnected boxes are not a picture. Measured over 3003
# Python files across this machine's projects: 795 declared structures (521
# pydantic, 232 dataclass, 41 NamedTuple, 1 TypedDict), of which the connectivity
# rule keeps enough to draw in 76 modules, and the largest single drawing is 11
# boxes out of the 17 its module declares. Singletons stay in the card.

STRUCTURE_HEADER = "#F4EEE4"   # warm, against the ERD's cool #EEF0FF: not a schema
TYPE_MAX = 48                  # characters of annotation a field row shows


def structure_slug(path: str) -> str:
    """A module path turned into the filename piece of its diagram.

    The path, not the module's name: a path is unique in the repository by
    definition, so two diagrams cannot collide and no disambiguation pass is
    needed. Case is kept for the same reason — lowercasing would merge `User.py`
    and `user.py`, which coexist happily on Linux.
    """
    stem = path[: -len(".py")] if path.endswith(".py") else path
    return re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-") or "structures"


def structure_groups(facts: dict) -> dict[str, dict]:
    """Module path -> the structures in it worth drawing, or nothing.

    A structure reaches the picture only if it references another structure in the
    same file or is referenced by one. A self-reference (`parent: Node | None`) does
    not count: a lone box with a loop on it is a diagram of one class, which the
    card already prints in full.
    """
    groups: dict[str, dict] = {}
    for path in sorted(facts):
        declared = ((facts[path] or {}).get("schema") or {}).get("structures") or []
        by_name = {item["name"]: item for item in declared}
        linked: set[str] = set()
        for item in declared:
            for column in item.get("columns", []):
                target = column.get("fk")
                if target in by_name and target != item["name"]:
                    linked.add(item["name"])
                    linked.add(target)
        if len(linked) < 2:
            continue
        groups[path] = {
            "name": path,
            "source": path,
            "structures": [{**item, "source": path} for item in declared if item["name"] in linked],
        }
    return groups


def _structure_tag(column: dict) -> str | None:
    """The annotation as written, and `req` where a table would say `PK`.

    A structure has no primary key — nothing identifies one instance among many —
    so the tag column carries the one obligation a class body does state: a field
    with no default is a field the caller has to supply.

    The type rides here rather than on the edge it belongs to, for two reasons. It
    is where a nested cardinality survives: a crow's foot says "many" and stops,
    while `dict[str, list[LoggingConfig]]` is two containers deep. And an edge
    label is not an option at all — `label` or `xlabel` on an edge together with
    the graph's `ratio` aborts this Graphviz build outright (SIGSEGV inside
    gvFreeLayout, reproduced with and without ports, with and without splines).
    """
    written = column.get("type") or ""
    if len(written) > TYPE_MAX:
        # Measured on a real pydantic module: one
        # `Annotated[str, StringConstraints(strip_whitespace=True, min_length=3,
        # max_length=500)]` made its box more than twice as wide as every other box
        # on the picture, and pushed the fields of the rest into a narrow column.
        # The card prints the annotation in full; here it only has to be recognised.
        written = written[: TYPE_MAX - 1].rstrip() + "…"
    text = html.escape(written)
    if column.get("required"):
        return f"{text} req".strip()
    return text or None


def structures_dot_svg(group: dict) -> str | None:
    """One module's declared structures as an SVG, or None when Graphviz is absent.

    No fallback to the hand-written drawer: its caption and its tags say "database"
    and "PK", and a picture of dataclasses wearing those words is the one outcome
    this whole seam exists to prevent. Without the wheel there is simply no
    structures diagram, and the cards still carry every field.
    """
    try:
        import pygraphviz
    except Exception:  # any import failure means "no picture", never a wrong one
        return None

    structures = {item["name"]: item for item in group.get("structures", [])}
    if not structures:
        return None

    neighbours: dict[str, list[str]] = {name: [] for name in structures}
    for name, item in structures.items():
        for column in item.get("columns", []):
            target = column.get("fk")
            if target in structures and target != name:
                neighbours[name].append(target)
                neighbours[target].append(name)
    slot = graph_colours(sorted(structures), neighbours)

    path = group.get("name", "")
    graph = pygraphviz.AGraph(
        directed=True, strict=False, rankdir=RANKDIR, splines=SPLINES, ratio=RATIO,
        nodesep=NODESEP, ranksep=RANKSEP, bgcolor="white", compound="true",
        fontsize=str(FONT_SIZE), fontname="Helvetica",
        # Said in the picture itself, because a picture travels without its filename:
        # these boxes are objects a process holds, and no row of any of them exists
        # in any database.
        label=f"Structures declared in {path}\\n"
              "dataclasses, TypedDicts, NamedTuples and pydantic models — "
              "objects held in memory, not database tables\\n"
              "an arrow runs from a field to the structure its type names · "
              "a crow's foot means many · req marks a field with no default · "
              "every field shows the annotation it was declared with",
        labelloc="b", fontcolor="#666666",
    )
    graph.node_attr["fontsize"] = str(FONT_SIZE)
    graph.node_attr["fontname"] = "Helvetica"

    for name, item in sorted(structures.items()):
        graph.add_node(
            name, shape="plaintext",
            label=_node_label(
                name, item, tag_of=_structure_tag,
                emphasis=lambda column: bool(column.get("required")),
                header=STRUCTURE_HEADER,
            ),
        )

    # No file frame: every box on this picture came out of the one module the
    # caption names, so a frame around all of them would be ink saying nothing.
    for name, item in sorted(structures.items()):
        for column in item.get("columns", []):
            target = column.get("fk")
            if target not in structures:
                continue
            edge = {
                "color": PALETTE[slot[name]],
                "penwidth": "1.7",
                # Cardinality comes off the annotation: a container means many.
                "arrowhead": "crow" if column.get("many") else "normal",
                "arrowsize": "0.8",
                "tailport": column["name"],  # the row, side left to dot — see erd_dot_svg
            }
            graph.add_edge(name, target, **edge)

    # Same routing choice as the schema, and for the same reason: `Config` points at
    # four dataclasses laid out beside each other, and straight lines from four rows of
    # one box to four boxes below cross each other on the way.
    return _best_routing(graph)
