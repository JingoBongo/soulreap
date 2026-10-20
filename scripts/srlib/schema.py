"""Database schema: which tables a file declares, and how they relate to each other.

A registry of independent detectors. Each one either returns an exact table or stays
silent and records what it saw-but-could-not-read into `unread`. No table is guessed
by resemblance: a file named models.py full of classes with fields could just as well
be a set of neural-network models, and a reader cannot tell a fabricated table from a
real one.

The last section reads a second kind of thing entirely: structures declared in code
(dataclass, TypedDict, NamedTuple, pydantic model). They come back in the same shape,
because the same drawing engine takes them, but they are never tables and are kept in
a list of their own all the way to their own diagram.
"""

from __future__ import annotations

import ast
from urllib.parse import urlsplit

from .schema_orm import _callee_name, detect_sqlalchemy  # noqa: F401  (re-exported)
from .schema_sql import detect_sql_ddl  # noqa: F401  (re-exported)
from .schema_sqlite import detect_sqlite, sqlite_schema_sha  # noqa: F401  (re-exported)
from .schema_structures import detect_structures  # noqa: F401  (re-exported)

# ------------------------------------------------------------------ identity ---

CONNECT_FUNCTIONS = {"connect": "sqlite", "create_engine": "engine", "create_async_engine": "engine"}
IN_MEMORY = {":memory:", "", "/", "."}


def database_name(raw: str) -> str | None:
    """The database's bare name out of a DSN or a file path — and only the name.

    A connection string can carry credentials (`postgresql://user:pass@host/db`),
    and a diagram wants one word out of it regardless: host and port tell a reader
    nothing. The name is derived here so the raw DSN never travels further down
    the pipeline.
    """
    text = raw.strip()
    if not text or text in IN_MEMORY:
        return None
    if "://" in text:
        parsed = urlsplit(text)
        tail = parsed.path.lstrip("/")
        if not tail:
            return None
        text = tail
    stem = text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    stem = stem.strip()
    return stem or None


def detect_targets(tree: ast.Module) -> list[dict]:
    """Connection targets declared by a literal.

    A literal only: `create_engine(settings.DATABASE_URL)` cannot be read
    statically, and inventing a name from the variable is exactly the sort of
    guess this module exists to guard against.
    """
    found: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = CONNECT_FUNCTIONS.get(_callee_name(node))
        if kind is None or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        name = database_name(first.value)
        if name:
            found.append({"name": name, "line": node.lineno, "kind": kind})
    return found
