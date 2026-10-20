"""The context diagram's facts: which external systems a repo talks to, found from
manifests, config key names, compose services, client-library imports and URL
literals, and the DOT drawing of them."""

from __future__ import annotations

from urllib.parse import urlsplit

from .context import VIA_IMPORT, VIA_LITERAL, from_binaries
from .diagrams_labels import (
    GENERIC_SQL,
    GOOGLE_CLOUD,
    HOST_DENY_RE,
    LOCAL_PROCESS,
    LOOPBACK_RE,
    SERVICE_BY_MODULE,
    _clean_host,
    _grouping_depth,
    diagram_paths,
    group_key,
)
from .stores import store_library


def _ids(labels: list[str], prefix: str) -> dict[str, str]:
    return {label: f"{prefix}{i}" for i, label in enumerate(labels)}


# ----------------------------------------------------------------- context ---


def _services_from_imports(imports: list[dict]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for item in imports:
        module = str(item.get("module", ""))
        head = module.split(".")[0]
        if module.startswith("google.cloud"):
            tail = module.split(".")[2:]
            for token in list(item.get("names", [])) + tail:
                if token in GOOGLE_CLOUD:
                    found.add(GOOGLE_CLOUD[token])
            continue
        for heads, name, kind in SERVICE_BY_MODULE:
            if head in heads:
                found.add((name, kind))
                break
    return found


def _hosts_from_io(io: dict) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for evidence in io.get("http", []):
        if not evidence.startswith("http"):
            continue
        try:
            raw = urlsplit(evidence).hostname
        except ValueError:
            continue  # regex-looking evidence (e.g. "https://([a-z0-9]{8,})\\.supabase\\.co") is not a URL
        if not raw:
            continue
        if LOOPBACK_RE.match(raw.strip().strip("`'\"")):
            found.add(LOCAL_PROCESS)
            continue
        host = _clean_host(raw)
        if host and not HOST_DENY_RE.match(host):
            found.add((host, "HTTP"))
    return found


def context_depth(paths: list[str]) -> int:
    """Grouping depth for the CONTEXT question, which wants the opposite of the
    layering question.

    "What does this repo talk to" is answered per deployable. Splitting one service
    into its four sub-packages draws four rows all reaching the same host, which
    multiplies the edges and adds nothing — on a real monorepo this took the left
    column from 12 nodes to 7 and the edge count from 23 to 17. Depth 1 unless that
    would collapse the whole repo into a single node, where the sub-package IS the
    information.

    Shared by `context_mermaid` and `context_svg` on purpose: they are separate
    functions drawing the same facts, and a reduction applied to one and not the other
    diverges silently.
    """
    return 1 if len({group_key(p, 1) for p in paths}) >= 2 else _grouping_depth(paths)


CONTEXT_EMPTY_NOTE = (
    "No external systems found: nothing in the dependency manifests, nothing in the "
    "configuration key names, no compose services, no client-library imports and no "
    "URL literals in the source. Check this if it looks wrong — a system reached "
    "through a value this repository never writes down cannot be seen from here."
)


def _context_edges(facts: dict, evidence=()) -> tuple[dict, dict, int]:
    """``(edges, systems, depth)`` from all five sources at once.

    An edge is keyed on (package, system) and carries the SET of sources that
    found it, because that is what tells the reader how solid the claim is: a
    package in a manifest is nearly proof, a key name ending in `_URL` is a
    strong hint, and one edge may well be both.
    """
    paths = diagram_paths(facts)
    depth = context_depth(paths)

    edges: dict[tuple[str, str], set[str]] = {}
    systems: dict[str, str] = {}

    def add(group: str, name: str, kind: str, via: str) -> None:
        systems.setdefault(name, kind)
        edges.setdefault((group, name), set()).add(via)

    for path in paths:
        blob = facts[path]
        group = group_key(path, depth)
        for name, kind in _services_from_imports(blob.get("imports", [])):
            add(group, name, kind, VIA_IMPORT)
        for name, kind in _hosts_from_io(blob.get("io", {})):
            add(group, name, kind, VIA_LITERAL)
        # Programs the file runs. Same group as the file that runs them, so the edge
        # says which part of the repository shells out, not just that something does.
        for item in from_binaries({path: blob}):
            add(group, item["system"], item["kind"], item["via"])
        # A store the code reads and writes through a client library (mercy's `vaults.Vault`)
        # is where its state lives; no import table names a small library like that.
        for store in blob.get("stores") or []:
            library = store_library(blob, store)  # None: a class of this repo, not an outside system
            named = _services_from_imports([i for i in blob.get("imports", [])
                                            if str(i.get("module", "")).split(".")[0] == library])
            if library and not named:  # redis already has its own node
                add(group, library, "key-value store", VIA_IMPORT)

    for item in evidence or ():
        add(group_key(item["where"], depth), item["system"], item["kind"], item["via"])

    # A specific driver makes the generic SQLAlchemy/DATABASE_URL node redundant.
    if GENERIC_SQL in systems and any(
        s in systems for s in ("PostgreSQL", "MySQL", "MongoDB", "Supabase")
    ):
        systems.pop(GENERIC_SQL)
        edges = {e: v for e, v in edges.items() if e[1] != GENERIC_SQL}

    return edges, systems, depth


def context_systems(facts: dict, evidence=()) -> set[str]:
    """The names the context picture would carry, for whoever needs to compare two
    readings of it — today, the .mmd against the .svg."""
    _edges, systems, _depth = _context_edges(facts, evidence)
    return set(systems)


def _context_stats(edges: dict, systems: dict) -> dict:
    sources: dict[str, int] = {}
    for vias in edges.values():
        for via in vias:
            sources[via] = sources.get(via, 0) + 1
    return {
        "context_systems": len(systems),
        "context_edges": len(edges),
        "context_sources": sources,
    }


def _dot_label(s: str) -> str:
    """Escape a string for a quoted dot label."""
    return s.replace("\\", "\\\\").replace('\"', '\\"').replace("\n", "\\n")


_DOT_NODE = 'node [shape=box, style="rounded,filled", fillcolor="#f6f8fa", fontsize=13]'
_DOT_EDGE = "edge [fontsize=10, color='#8b949e', arrowsize=0.75]"


def context_dot(facts: dict, evidence=()) -> tuple[str, dict]:
    """Which packages reach which external systems, and how we know (dot source).

    graphviz renders this SVG (srlib/dviz.py); mermaid is gone from the outputs.
    Returns the empty-case text as well as the picture, but `render` is what
    decides whether to write it.
    """
    edges, systems, _depth = _context_edges(facts, evidence)

    head = [
        "digraph context {",
        '  graph [label="External systems this code talks to", fontsize=20,',
        '        rankdir=LR, ranksep="1.4", nodesep="0.5"]',
        "  // Left: packages in this repo. Right: systems outside it.",
        "  // Edge label says what the system is and how it was found — manifest and",
        "  // compose are nearly proof, a config key is the name of a setting, import",
        "  // and literal are written in the source itself.",
        f"  {_DOT_NODE}",
        f"  {_DOT_EDGE}",
    ]
    if not systems:
        return (
            "\n".join([
                *head,
                "  n0 [label=\"No external systems detected. "
                + CONTEXT_EMPTY_NOTE.replace('\"', "\\\"") + '\"]',
                "}",
            ])
            + "\n",
            {**_context_stats(edges, systems), "context_note": CONTEXT_EMPTY_NOTE},
        )

    packages = sorted({src for src, _ in edges})
    package_ids = _ids(packages, "p")
    system_ids = _ids(sorted(systems), "s")

    lines = [
        *head,
        '  subgraph cluster_repo {',
        '    label="this repository";',
        '    style="rounded,dashed"; color="#8b949e";',
    ]
    lines += [f'    {package_ids[p]} [label="{_dot_label(p)}"]' for p in packages]
    lines += ["  }"]
    lines += [f'  {system_ids[s]} [label="{_dot_label(s)}", shape=cylinder]'
              for s in sorted(systems)]
    for (source, target), vias in sorted(edges.items()):
        label = f"{systems[target]} · {', '.join(sorted(vias))}"
        lines.append(f"  {package_ids[source]} -> {system_ids[target]} "
                     f'[label="{_dot_label(label)}"]')
    lines.append("}")

    return "\n".join(lines) + "\n", _context_stats(edges, systems)


# ---------------------------------------------------------------- packages ---
