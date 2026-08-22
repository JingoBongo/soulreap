"""Diagrams that answer one question each.

Two are generated, and neither is a raw dump of the import graph:

* ``context.mmd``  — what does this code talk to outside itself?
* ``packages.mmd`` — how is it layered, and how heavy is each coupling?

A per-file import graph is deliberately not drawn. Above roughly twenty nodes
it reads as noise, and the one question it answers — what breaks if I touch
this file — is already answered precisely by ``imported_by`` on each card.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

GRAPH_MAX_NODES = 40
TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs", "testing"}
TEST_FILE_RE = re.compile(r"^(test_.+|.+_test|.+\.test|.+\.spec)\.\w+$")

# Hosts that appear in source but are not systems anyone integrates with.
HOST_DENY_RE = re.compile(
    r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|host\.docker\.internal"
    r"|.+\.local|(www\.)?w3\.org|(www\.)?schema\.org|(www\.)?example\.(com|org|net))$",
    re.IGNORECASE,
)

# A real dotted hostname. urlsplit is lenient — a URL pulled from a markdown code
# span or an f-string in a docstring yields a "host" carrying backticks, braces or
# a stray quote, which then lands verbatim in a mermaid node label. Sanitize to the
# hostname characters, then require this shape or drop the evidence entirely.
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    re.IGNORECASE,
)


def _clean_host(host: str) -> str | None:
    """Strip label-hostile junk and confirm it is really a hostname, else None."""
    host = host.strip().strip("`'\"").rstrip(".").lower()
    return host if HOSTNAME_RE.match(host) else None

# Client library -> the system on the other end of it.
SERVICE_BY_MODULE: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("asyncpg", "psycopg", "psycopg2"), "PostgreSQL", "database"),
    (("supabase",), "Supabase", "database"),
    (("pymongo", "motor"), "MongoDB", "database"),
    (("qdrant_client",), "Qdrant", "vector store"),
    (("pinecone",), "Pinecone", "vector store"),
    (("redis", "aioredis"), "Redis", "cache"),
    (("aiosmtplib", "smtplib"), "SMTP", "email"),
    (("pika", "aio_pika", "kombu"), "RabbitMQ", "queue"),
    (("kafka", "confluent_kafka"), "Kafka", "queue"),
    (("celery",), "Celery", "queue"),
    (("mistralai",), "Mistral", "LLM API"),
    (("openai",), "OpenAI", "LLM API"),
    (("anthropic",), "Anthropic", "LLM API"),
    (("stripe",), "Stripe", "payments"),
    (("boto3", "botocore"), "AWS", "cloud"),
    # Generic SQL last: a specific driver above wins when both are imported.
    (("sqlalchemy", "sqlmodel"), "SQL database", "database"),
)
GENERIC_SQL = "SQL database"

GOOGLE_CLOUD = {
    "storage": ("Google Cloud Storage", "object storage"),
    "tasks": ("Google Cloud Tasks", "queue"),
    "tasks_v2": ("Google Cloud Tasks", "queue"),
    "pubsub_v1": ("Google Pub/Sub", "queue"),
    "secretmanager": ("Secret Manager", "secrets"),
    "secretmanager_v1": ("Secret Manager", "secrets"),
    "bigquery": ("BigQuery", "database"),
    "firestore": ("Firestore", "database"),
    "logging": ("Cloud Logging", "observability"),
}


# ------------------------------------------------------------------ shared ---


def group_key(path: str, depth: int) -> str:
    """Directory of *path*, truncated to *depth* segments."""
    parts = Path(path).parent.parts
    if not parts or parts == (".",):
        return "(root)"
    return "/".join(parts[:depth])


def is_test_path(path: str) -> bool:
    """True for test files, by directory or by filename convention."""
    parts = Path(path).parts
    if any(part in TEST_DIRS for part in parts[:-1]):
        return True
    return bool(TEST_FILE_RE.match(parts[-1]))


def diagram_paths(facts: dict) -> list[str]:
    """The files worth drawing.

    Tests import everything by definition and loose root scripts are usually
    probes; both become hubs whose edges carry no design information. They keep
    their cards and index rows — they are only left out of the pictures.
    """
    non_test = [p for p in facts if not is_test_path(p)]
    packaged = [p for p in non_test if "/" in p]
    kept = packaged or non_test
    return sorted(kept) if len(kept) >= 2 else sorted(facts)


def _grouping_depth(paths: list[str]) -> int:
    depth = 1
    for candidate in range(2, 7):
        if len({group_key(p, candidate) for p in paths}) <= GRAPH_MAX_NODES:
            depth = candidate
        else:
            break
    return depth


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
        host = _clean_host(raw)
        if host and not HOST_DENY_RE.match(host):
            found.add((host, "HTTP"))
    return found


def context_mermaid(facts: dict) -> tuple[str, dict]:
    """Which packages reach which external systems, and how."""
    paths = diagram_paths(facts)
    depth = _grouping_depth(paths)

    edges: dict[tuple[str, str], str] = {}
    systems: dict[str, str] = {}
    for path in paths:
        blob = facts[path]
        reached = _services_from_imports(blob.get("imports", []))
        reached |= _hosts_from_io(blob.get("io", {}))
        for name, kind in reached:
            systems[name] = kind
            edges[(group_key(path, depth), name)] = kind

    # A specific driver makes the generic SQLAlchemy node redundant.
    if GENERIC_SQL in systems and any(
        s in systems for s in ("PostgreSQL", "MongoDB", "Supabase")
    ):
        systems.pop(GENERIC_SQL)
        edges = {e: k for e, k in edges.items() if e[1] != GENERIC_SQL}

    head = ["---", "title: External systems this code talks to", "---", "graph LR"]
    if not systems:
        return (
            "\n".join([
                *head,
                "%% No external systems detected: no service-client imports and no",
                "%% external URLs in the source. Anything reached through a config",
                "%% value rather than a literal will not appear here.",
                '  n0["No external systems detected"]',
            ])
            + "\n",
            {"context_systems": 0, "context_edges": 0},
        )

    packages = sorted({src for src, _ in edges})
    package_ids = _ids(packages, "p")
    system_ids = _ids(sorted(systems), "s")

    lines = [
        *head,
        "%% Left: packages in this repo. Right: systems outside it.",
        "%% Edge label says how the call is made; it is the only thing that carries",
        "%% meaning — shape and position do not.",
        '  subgraph repo["this repository"]',
    ]
    lines += [f'    {package_ids[p]}["{p}"]' for p in packages]
    lines.append("  end")
    lines += [f'  {system_ids[s]}(["{s}"])' for s in sorted(systems)]
    for (source, target), kind in sorted(edges.items()):
        lines.append(f"  {package_ids[source]} -->|{kind}| {system_ids[target]}")

    return "\n".join(lines) + "\n", {
        "context_systems": len(systems),
        "context_edges": len(edges),
    }


# ---------------------------------------------------------------- packages ---


def package_mermaid(facts: dict) -> tuple[str, dict]:
    """How the packages are layered, and how heavy each dependency is."""
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

    artifacts = {
        pair for pair in weights
        if (pair[1], pair[0]) in weights and pair not in real_mutual
    }

    ids = _ids(labels, "n")
    lines = [
        "---",
        "title: Package layering and coupling weight",
        "---",
        "graph TD",
        "%% Read top to bottom: entry points depend downward on foundations.",
        "%% Node label carries its own role, so the picture survives losing colour.",
        "%% Edge label is how many file-level imports the arrow stands for.",
    ]
    if artifacts:
        lines.append(
            "%% A dotted arrow is a mutual dependency between two groups, not an "
            "import cycle — no single pair of files imports each other."
        )
    for label in labels:
        lines.append(f'  {ids[label]}["{label}<br/><i>{role(label)}</i>"]')
    for (source, target), weight in sorted(weights.items()):
        arrow = "-.->" if (source, target) in artifacts else "-->"
        noun = "import" if weight == 1 else "imports"
        lines.append(f"  {ids[source]} {arrow}|{weight} {noun}| {ids[target]}")

    roles = {"entry": 0, "core": 0, "foundation": 0, "isolated": 0}
    for label in labels:
        roles[role(label)] += 1

    node_count = len(labels)
    possible = node_count * (node_count - 1)
    stats = {
        "graph_roles": roles,
        "graph_nodes": node_count,
        "graph_edges": len(weights),
        "graph_density": (len(weights) / possible) if possible else 0.0,
        "graph_depth": depth,
        "graph_excluded": len(facts) - len(paths),
    }
    return "\n".join(lines) + "\n", stats
