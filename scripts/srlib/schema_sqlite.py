"""SQLite databases: tables read straight out of a database file by introspection.

Opens the file read-only, reads columns, primary and foreign keys from the catalogue,
and hashes the resulting shape so a render is dirty only when the schema changed.
"""

from __future__ import annotations

# ------------------------------------------------------------------- sqlite ---

SQLITE_MAGIC = b"SQLite format 3\x00"


def _sqlite_identifier(name: str) -> str:
    """A name safe to paste into a PRAGMA, which takes no parameters."""
    return '"' + name.replace('"', '""') + '"'


def _sqlite_connection(path):
    """Read-only, and never a writer.

    `mode=ro` first, because it reads any pending WAL and therefore answers with the
    current schema. `immutable=1` second, for a database on a read-only filesystem or
    one whose lock we are not allowed to take — it cannot see a WAL, which is the
    price of being able to read it at all.
    """
    import sqlite3
    from urllib.request import pathname2url

    url = "file:" + pathname2url(str(path))
    for suffix in ("?mode=ro", "?mode=ro&immutable=1"):
        try:
            return sqlite3.connect(url + suffix, uri=True, timeout=2.0)
        except sqlite3.Error:
            continue
    return None


def detect_sqlite(path) -> tuple[list[dict], list[dict]]:
    """Tables a SQLite file declares, in the shape the other detectors return.

    The database's own account of itself, out of `sqlite_master` and `PRAGMA
    table_info`. That makes it the most solid of the four sources — a migration says
    what somebody meant to build, a model says what the application believes, and this
    says what is there. It is also the only one that answers `nullable` without
    guessing.

    Views are left out: a view is a saved query, and drawing it beside the tables would
    promise rows in a place that has none. `sqlite_*` tables are the engine's
    bookkeeping, not the schema.
    """
    from pathlib import Path as _Path

    path = _Path(path)
    try:
        if path.stat().st_size == 0:
            # `sqlite3.connect()` and nothing else: the file is a database that has
            # never been written to, and there is no schema to complain about.
            return [], []
        with path.open("rb") as handle:
            if handle.read(len(SQLITE_MAGIC)) != SQLITE_MAGIC:
                return [], [{
                    "line": 0,
                    "why": "not a SQLite database, despite the extension",
                    "snippet": path.name,
                }]
    except OSError as exc:
        return [], [{"line": 0, "why": f"unreadable: {exc.strerror or exc}", "snippet": path.name}]

    connection = _sqlite_connection(path)
    if connection is None:
        return [], [{
            "line": 0,
            "why": "a SQLite file this process could not open, even read-only",
            "snippet": path.name,
        }]

    import sqlite3

    tables: list[dict] = []
    unread: list[dict] = []
    try:
        names = [
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for order, name in enumerate(names, start=1):
            quoted = _sqlite_identifier(name)
            columns = []
            for _cid, column, declared, notnull, _default, pk in connection.execute(
                f"PRAGMA table_info({quoted})"
            ):
                columns.append({
                    "name": column,
                    "type": (declared or "").strip() or None,
                    "pk": bool(pk),
                    "fk": None,
                    "nullable": not bool(notnull),
                })
            by_name = {c["name"]: c for c in columns}
            for row in connection.execute(f"PRAGMA foreign_key_list({quoted})"):
                # (id, seq, table, from, to, on_update, on_delete, match)
                parent, child_column, parent_column = row[2], row[3], row[4]
                column = by_name.get(child_column)
                if not column:
                    continue
                # `REFERENCES hubs` without a column means the parent's primary key,
                # and sqlite reports `to` as NULL there.
                if not parent_column:
                    parent_column = _sqlite_primary_key(connection, parent) or "id"
                column["fk"] = f"{parent}.{parent_column}"
            tables.append({
                "name": name,
                "line": order,
                "end_line": order,
                "detector": "sqlite_file",
                "columns": columns,
            })
    except sqlite3.Error as exc:
        unread.append({"line": 0, "why": f"SQLite refused the read: {exc}", "snippet": path.name})
    finally:
        connection.close()
    return tables, unread


def _sqlite_primary_key(connection, table: str) -> str | None:
    import sqlite3
    try:
        for row in connection.execute(f"PRAGMA table_info({_sqlite_identifier(table)})"):
            if row[5]:
                return row[1]
    except sqlite3.Error:
        return None
    return None


def sqlite_schema_sha(tables: list[dict]) -> str:
    """A hash of the SHAPE, so a render is dirty when the schema changed and not when
    a row was inserted. The file's own bytes change on every write."""
    import hashlib
    import json as _json

    return hashlib.sha256(
        _json.dumps(tables, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
