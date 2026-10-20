"""The package diagram's facts: packages, the arrows between them, each package's role,
and the DOT drawing of the graph."""

from __future__ import annotations

from .diagrams_context import _DOT_EDGE, _DOT_NODE, _dot_label, _ids
from .diagrams_labels import _grouping_depth, diagram_paths, display_label, group_key


def package_graph(facts: dict) -> dict:
    """Packages, the arrows between them, and what each package's role is.

    The single source both the `.mmd` and the `.svg` are built from. They used to be
    computed twice — once as text, once as geometry — which is exactly how the context
    pair came to name different systems.
    """
    paths = diagram_paths(facts)
    visible = set(paths)
    depth = _grouping_depth(paths)

    file_edges = [
        (path, dep)
        for path in paths
        for dep in facts[path].get("deps", [])
        if dep in visible
    ]
    file_edge_set = set(file_edges)
    real_mutual = {
        (group_key(a, depth), group_key(b, depth))
        for (a, b) in file_edge_set
        if (b, a) in file_edge_set
    }

    weights: dict[tuple[str, str], int] = {}
    for source, target in file_edges:
        pair = (group_key(source, depth), group_key(target, depth))
        if pair[0] == pair[1]:
            continue
        weights[pair] = weights.get(pair, 0) + 1

    labels = sorted({n for pair in weights for n in pair} or {group_key(p, depth) for p in paths})
    incoming = {label: 0 for label in labels}
    outgoing = {label: 0 for label in labels}
    for source, target in weights:
        outgoing[source] = outgoing.get(source, 0) + 1
        incoming[target] = incoming.get(target, 0) + 1

    def role(label: str) -> str:
        into, out_of = incoming.get(label, 0), outgoing.get(label, 0)
        if not into and not out_of:
            return "isolated"
        if not into:
            return "entry"
        if not out_of:
            return "foundation"
        return "core"

    # A pair drawn both ways where no two FILES import each other: the cycle is an
    # artifact of collapsing files into packages, and saying so is the difference
    # between a reader hunting an import cycle and a reader reading on.
    artifacts = {
        pair for pair in weights
        if (pair[1], pair[0]) in weights and pair not in real_mutual
    }

    return {
        "labels": labels,
        "weights": weights,
        "artifacts": artifacts,
        "roles": {label: role(label) for label in labels},
        "depth": depth,
        "excluded": len(facts) - len(paths),
    }


def package_stats(graph: dict) -> dict:
    roles = {"entry": 0, "core": 0, "foundation": 0, "isolated": 0}
    for role in graph["roles"].values():
        roles[role] += 1
    node_count = len(graph["labels"])
    possible = node_count * (node_count - 1)
    return {
        "graph_roles": roles,
        "graph_nodes": node_count,
        "graph_edges": len(graph["weights"]),
        "graph_density": (len(graph["weights"]) / possible) if possible else 0.0,
        "graph_depth": graph["depth"],
        "graph_excluded": graph["excluded"],
    }


PACKAGES_EMPTY_NOTE = (
    "No package layering to draw: {nodes} — nothing imports across a package "
    "boundary, so the layering-and-coupling picture would have measured nothing. "
    "The per-file imports are on the cards, as `imports` and `imported_by`."
)


def packages_empty_note(stats: dict) -> str:
    count = stats.get("graph_nodes", 0)
    nodes = "one package" if count == 1 else f"{count} packages, no arrows between them"
    return PACKAGES_EMPTY_NOTE.format(nodes=nodes)


def package_dot(facts: dict) -> tuple[str, dict]:
    """How the packages are layered, and how heavy each dependency is (dot source).

    graphviz renders this SVG; mermaid is gone from the outputs. Returns the text
    even when there is no layering at all; `render` decides whether to write it.
    """
    graph = package_graph(facts)
    labels, weights = graph["labels"], graph["weights"]
    artifacts, roles = graph["artifacts"], graph["roles"]

    ids = _ids(labels, "n")
    lines = [
        "digraph packages {",
        '  graph [label="Package layering and coupling weight", fontsize=20,',
        '        rankdir=TB, ranksep="1.1", nodesep="0.5"]',
        f"  {_DOT_NODE}",
        f"  {_DOT_EDGE}",
        "  // Read top to bottom: entry points depend downward on foundations.",
        "  // Node label carries its own role, so the picture survives losing colour.",
        "  // Edge label is how many file-level imports the arrow stands for.",
    ]
    if artifacts:
        lines.append(
            "  // A dotted arrow is a mutual dependency between two groups, not an "
            "import cycle — no single pair of files imports each other."
        )
    for label in labels:
        lines.append(
            f'  {ids[label]} [label="{_dot_label(display_label(label))} \\n '
            f'<i>{_dot_label(roles[label])}</i>"]'
        )
    for (source, target), weight in sorted(weights.items()):
        noun = "import" if weight == 1 else "imports"
        attrs = [f'label="{weight} {noun}"']
        if (source, target) in artifacts:
            attrs.append("style=dashed")
        lines.append(f"  {ids[source]} -> {ids[target]} [{', '.join(attrs)}]")
    lines.append("}")

    return "\n".join(lines) + "\n", package_stats(graph)


# --------------------------------------------------------------------- erd ---
