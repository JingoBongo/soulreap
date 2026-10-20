"""Pattern tables and the detectors built on them: I/O targets, concurrency, TODOs.

The regexes and symbol sets that say what a module name, a call or a string literal
implies (database, HTTP, queue, cache, filesystem, threads, locks), the stdlib test
used to sort imports, the small detectors that apply them, and the redaction of
credential-looking values. Language-agnostic: both the Python and the tree-sitter
extractors call these.
"""

from __future__ import annotations

import re
import sys

TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX|BUG)\b[:\-\s]*(.*)")
SQL_RE = re.compile(
    r"\b(SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE)\b",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s\"']+")
NODE_BUILTINS = {
    "assert", "buffer", "child_process", "cluster", "console", "crypto", "dgram",
    "dns", "events", "fs", "http", "http2", "https", "net", "os", "path", "perf_hooks",
    "process", "querystring", "readline", "stream", "string_decoder", "timers", "tls",
    "tty", "url", "util", "v8", "vm", "worker_threads", "zlib",
}


def _is_stdlib(module: str, lang: str | None) -> bool:
    """True when the module ships with the language runtime."""
    head = module.lstrip(".").split(".")[0].split("/")[0]
    if not head:
        return False
    if lang == "python":
        return head in sys.stdlib_module_names
    if module.startswith("node:"):
        return True
    return head in NODE_BUILTINS


SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|SALT|DSN)", re.IGNORECASE)

DB_MODULES = re.compile(
    r"^(asyncpg|psycopg2?|sqlalchemy|sqlmodel|supabase|pymongo|motor|databases"
    r"|peewee|tortoise|aiomysql|duckdb|qdrant_client|pinecone)"
)
HTTP_MODULES = re.compile(r"^(httpx|requests|aiohttp|urllib|http|websockets|grpc|openai|anthropic|boto3)")
QUEUE_MODULES = re.compile(r"^(pika|kafka|confluent_kafka|celery|nats|aio_pika|kombu)")
CACHE_MODULES = re.compile(r"^(redis|aioredis|aiocache|cachetools|diskcache|memcache)")
FS_MODULES = re.compile(r"^(shutil|tempfile|pathlib|glob|zipfile|tarfile)")

DB_CALLS = {
    "fetch", "fetchrow", "fetchval", "fetchall", "fetchone", "executemany",
    "execute", "commit", "rollback", "cursor", "upsert",
}
FS_CALLS = {"read_text", "write_text", "read_bytes", "write_bytes", "mkdir", "unlink", "rename", "iterdir", "rglob"}
THREAD_SYMBOLS = {"Thread", "ThreadPoolExecutor", "thread_map"}
PROCESS_SYMBOLS = {"Process", "ProcessPoolExecutor", "Popen", "fork"}
ASYNC_SYMBOLS = {
    "create_task", "gather", "as_completed", "wait_for", "run_in_executor",
    "TaskGroup", "to_thread", "ensure_future",
}
LOCK_SYMBOLS = {"Lock", "RLock", "Semaphore", "BoundedSemaphore", "Event", "Condition", "Barrier"}
QUEUE_SYMBOLS = {"Queue", "LifoQueue", "PriorityQueue", "SimpleQueue"}


def _detect_io(modules: list[str], names: set[str], attrs: set[str], strings: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}

    def note(kind: str, evidence: str) -> None:
        bucket = found.setdefault(kind, [])
        if evidence not in bucket and len(bucket) < 5:
            bucket.append(evidence)

    for module in modules:
        head = module.lstrip(".")
        if DB_MODULES.match(head):
            note("db", f"import {module}")
        if HTTP_MODULES.match(head):
            note("http", f"import {module}")
        if QUEUE_MODULES.match(head):
            note("queue", f"import {module}")
        if CACHE_MODULES.match(head):
            note("cache", f"import {module}")
        if FS_MODULES.match(head):
            note("fs", f"import {module}")

    for attr in sorted(attrs & DB_CALLS):
        note("db", f".{attr}()")
    for attr in sorted(attrs & FS_CALLS):
        note("fs", f".{attr}()")
    if "open" in names:
        note("fs", "open()")

    for text in strings:
        if SQL_RE.search(text):
            note("db", f"sql: {text.strip()[:60]}")
        url = URL_RE.search(text)
        if url:
            note("http", _redact_url(url.group(0))[:60])
    return found


def _detect_concurrency(modules: list[str], names: set[str], attrs: set[str], has_async_def: bool) -> list[str]:
    tags: set[str] = set()
    symbols = names | attrs
    heads = {m.lstrip(".").split(".")[0] for m in modules}

    if symbols & THREAD_SYMBOLS or "threading" in heads:
        tags.add("threads")
    if symbols & PROCESS_SYMBOLS or {"multiprocessing", "subprocess"} & heads:
        tags.add("processes")
    if symbols & ASYNC_SYMBOLS or ("asyncio" in heads and has_async_def):
        tags.add("async")
    if symbols & LOCK_SYMBOLS:
        tags.add("locks")
    if symbols & QUEUE_SYMBOLS or "queue" in heads:
        tags.add("queues")
    return sorted(tags)


def _todos(source: str) -> list[dict]:
    out: list[dict] = []
    for number, line in enumerate(source.splitlines(), start=1):
        match = TODO_RE.search(line)
        if match:
            out.append({"kind": match.group(1), "line": number, "text": match.group(2).strip()[:120]})
    return out


def _redact_url(text: str) -> str:
    """Drop query string and fragment.

    A URL literal in source can carry a hardcoded credential. Scheme, host and
    path are what a reader needs; everything after ? or # is where secrets live.
    """
    return re.split(r"[?#]", text, maxsplit=1)[0]


def _safe_value(name: str, value: str) -> str:
    """A constant's value, with anything that looks like a credential removed."""
    if SECRET_NAME_RE.search(name):
        return "<redacted>"
    return URL_RE.sub(lambda m: _redact_url(m.group(0)), value)
