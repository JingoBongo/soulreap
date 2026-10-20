"""Names and grouping shared by every diagram: which hosts are never a system, how a
host or a path is labelled for display, and how paths are grouped into boxes.
The context and package diagrams both build on this and neither on the other."""

from __future__ import annotations

import re
from pathlib import Path

from .paths import is_test_path

GRAPH_MAX_NODES = 40

# Hosts that appear in source but are not systems anyone integrates with.
# Hosts that are never a dependency, only a mention.
#
# Two groups. The first is loopback and placeholder addresses. The second is the one
# that actually cost us: a host appears in the evidence because somebody linked to its
# documentation, its web console, a registry page or a Stack Overflow answer in a
# comment or docstring. Drawing it asserts "this code talks to that system", which is
# false — and on a real repo six of the fifteen systems on the context diagram were
# exactly this, which both misled the reader and made the picture unreadable.
#
# Deliberately EXACT hosts and narrow prefixes, never a wildcard on the parent domain:
# `learn.microsoft.com` must go while `graph.microsoft.com` stays, and
# `cloud.google.com` must go while `<service>-<hash>.a.run.app` stays. The known cost
# is a real API served from a `docs.` subdomain, which is rare enough to accept.
HOST_DENY_RE = re.compile(
    r"^("
    # loopback and placeholders
    r"localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|host\.docker\.internal"
    r"|.+\.local|(www\.)?w3\.org|(www\.)?schema\.org|(www\.)?example\.(com|org|net)"
    # vendor documentation and web consoles
    r"|(learn|docs)\.microsoft\.com|(cloud|console\.cloud|developers)\.google\.com"
    r"|docs\..+|.+\.readthedocs\.io|developer\.mozilla\.org|devblogs\.microsoft\.com"
    r"|(www\.)?(stackoverflow|serverfault|superuser)\.com|.+\.wikipedia\.org"
    # code hosting, registries and CI identity — mentioned, not called
    r"|(www\.|gist\.|cli\.|raw\.)?github\.com|token\.actions\.githubusercontent\.com"
    r"|(www\.)?(gitlab\.com|bitbucket\.org)|(www\.)?pypi\.org|files\.pythonhosted\.org"
    r"|(www\.|registry\.)?npmjs\.(com|org)|crates\.io|hub\.docker\.com"
    # asset CDNs referenced from generated HTML, fetched by a browser and not by us
    r"|fonts\.(googleapis|gstatic)\.com|cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com"
    r"|unpkg\.com"
    r")$",
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


# Loopback is denied as a NAME above — "localhost" on a picture names nothing, and the
# port is usually interpolated. The FACT is still real and, on a process supervisor like
# fuse_core, it is half the job: it polls `http://localhost:{port}/health` on every
# service it starts. So loopback gets the one node the evidence supports.
LOOPBACK_RE = re.compile(r"^(localhost|127\.\d+\.\d+\.\d+|\[::1\]|::1)$", re.IGNORECASE)
LOCAL_PROCESS = ("local process", "HTTP on this host")


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
    (("psutil",), "host OS", "processes and metrics"),
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


def display_label(group: str) -> str:
    """A group key with the packaging boilerplate removed, for DISPLAY only.

    `copilot-mailbox-worker/src/copilot_mailbox_worker/agent` is 47 characters of which
    24 say nothing: the `src/` layer and the package directory that merely mirrors the
    distribution name. Boxes are sized from their label, so on a real repo those labels
    produced 254-480px boxes, a 1762px-wide row, and inter-layer edges drawn straight
    through the boxes between their ends.

    Only two segments go: a `src` that is not the first segment, and a segment equal to
    the one before it with dashes turned into underscores. Never the key itself — edges
    are keyed on the original, so shortening that would break them.
    """
    if "/" not in group:
        return group
    kept: list[str] = []
    for segment in group.split("/"):
        if segment == "src" and kept:
            continue
        if kept and segment == kept[-1].replace("-", "_"):
            continue
        kept.append(segment)
    return "/".join(kept) or group


def group_key(path: str, depth: int) -> str:
    """Directory of *path*, truncated to *depth* segments."""
    parts = Path(path).parent.parts
    if not parts or parts == (".",):
        return "(root)"
    return "/".join(parts[:depth])


def diagram_paths(facts: dict) -> list[str]:
    """The files worth drawing.

    Tests import everything by definition and loose root scripts are usually
    probes; both become hubs whose edges carry no design information. They keep
    their cards and index rows — they are only left out of the pictures.

    A .sql file carries a schema, not imports. Left in, it sits in the package
    graph as a node no edge ever touches, and counts against the density figure
    render reports.
    """
    non_test = [
        p for p in facts
        if not is_test_path(p) and (facts[p] or {}).get("lang") != "sql"
    ]
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
