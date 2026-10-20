"""Diagrams that answer one question each.

Two are generated, and neither is a raw dump of the import graph:

* ``context.mmd``  — what does this code talk to outside itself?
* ``packages.mmd`` — how is it layered, and how heavy is each coupling?

A per-file import graph is deliberately not drawn. Above roughly twenty nodes
it reads as noise, and the one question it answers — what breaks if I touch
this file — is already answered precisely by ``imported_by`` on each card.

This module holds the database (ERD) sources and re-exports the context and package
diagrams, which live in ``diagrams_context``, ``diagrams_packages`` and, for the
grouping and labelling both share, ``diagrams_labels``.
"""


from __future__ import annotations

import re

# The diagrams live in sibling modules; every name other code imports from here is
# re-exported so `srlib.diagrams` stays the one address.
from .diagrams_context import (  # noqa: F401  (re-exported)
    CONTEXT_EMPTY_NOTE,
    _context_edges,
    _context_stats,
    _dot_label,
    _hosts_from_io,
    _ids,
    _services_from_imports,
    context_depth,
    context_dot,
    context_systems,
)
from .diagrams_labels import (  # noqa: F401  (re-exported)
    GENERIC_SQL,
    GOOGLE_CLOUD,
    GRAPH_MAX_NODES,
    HOST_DENY_RE,
    HOSTNAME_RE,
    LOCAL_PROCESS,
    LOOPBACK_RE,
    SERVICE_BY_MODULE,
    _clean_host,
    _grouping_depth,
    diagram_paths,
    display_label,
    group_key,
)
from .diagrams_packages import (  # noqa: F401  (re-exported)
    PACKAGES_EMPTY_NOTE,
    package_dot,
    package_graph,
    package_stats,
    packages_empty_note,
)
from .paths import is_test_path

ERD_TYPE_RE = re.compile(r"[^A-Za-z0-9_]+")


def _database_key(path: str, blob: dict, table: dict) -> tuple[str, str]:
    """(grouping key, display name) for one table.

    The ladder from the spec: a literal connection target in the same file,
    else a shared declarative Base, else a shared DDL source directory, else
    the owning module. The first rung that resolves wins.

    Exactly one literal in the file is a solid fact: the file connects to one
    database, and its name is written out in plain text. Two or more literals
    in the same file are no longer a fact but two candidates, with nothing
    tying any specific table to either one (no fact links a particular CREATE
    TABLE to a particular connect() call inside a single file). Taking the
    first one anyway would invent a link the parser never proved, and silently
    merge two different databases under the name of just one. The final review
    caught this by execution: a module with sqlite3.connect("alpha.db") and
    sqlite3.connect("beta.db") produced one group named "alpha", into which
    beta's tables leaked. So an ambiguous file settles nothing at this rung and
    simply falls through to the next one on the ladder.
    """
    targets = blob.get("targets") or []
    if len(targets) == 1:
        return f"target:{targets[0]['name']}", targets[0]["name"]
    base = table.get("base")
    if base:
        return f"base:{base}", base
    posix = path.replace("\\", "/")
    if posix.lower().endswith(".sql"):
        # A directory, not a file: headroom keeps sql/create_dashboard_summary.sql
        # and sql/create_proxy_telemetry_v2.sql — that is one database, not two.
        directory = posix.rsplit("/", 1)[0] if "/" in posix else ""
        stem = posix.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return f"ddl:{directory}", (directory.rsplit("/", 1)[-1] if directory else stem)
    stem = posix.rsplit("/", 1)[-1]
    name = stem.rsplit(".", 1)[0] if "." in stem else stem
    if len(targets) > 1:
        # The same "no fact ties this table to a specific candidate" problem
        # that rules out picking targets[0] above rules out the ordinary
        # same-file-is-one-database assumption here too: nothing says these
        # particular tables share a database just because they share a file
        # whose own connection identity is unresolved. Keep each table in its
        # own group instead of folding them together under one guessed name.
        return f"module:{path}:{table.get('name')}:{table.get('line')}", name
    return f"module:{path}", name


def _one_entry_per_table(tables: list[dict]) -> list[dict]:
    """One entry per table name.

    A project may well declare the same table twice — a `schema.sql` for the
    initial rollout and a SQLAlchemy model for the application — and then two
    entries would draw it twice on the picture. Keep the fuller reading: it is
    cheaper for the reader, and neither of the two is wrong.
    """
    best: dict[str, dict] = {}
    for table in tables:
        seen = best.get(table["name"])
        if seen is None or len(table.get("columns") or []) > len(seen.get("columns") or []):
            best[table["name"]] = table
    return list(best.values())


def group_databases(facts: dict) -> dict[str, dict]:
    """The repository's tables, sorted into databases.

    The key is the connection target, not the file: one database declared
    across four modules is one picture. The reverse — a file with two literal
    connection targets in it — does not split into two pictures: no fact says
    which table belongs to which target, and guessing would mean inventing a
    link. `_database_key` simply leaves the connection target out of account
    in that case and falls through to the next rung of the ladder.
    """
    grouped: dict[str, dict] = {}
    for path in sorted(facts):
        if is_test_path(path):  # a test's scratch table (`CREATE TABLE control.evil`) is not a database
            continue
        blob = (facts[path] or {}).get("schema") or {}
        for table in blob.get("tables") or []:
            key, name = _database_key(path, blob, table)
            database = grouped.setdefault(key, {"name": name, "tables": [], "sources": [], "unread": []})
            # The declaring file travels with the table: the ERD draws one section per
            # module, and `sources` only says which files took part, never which table
            # came from which.
            database["tables"].append({**table, "source": path})
            if path not in database["sources"]:
                database["sources"].append(path)
        for item in blob.get("unread") or []:
            key, name = _database_key(path, blob, {})
            database = grouped.setdefault(key, {"name": name, "tables": [], "sources": [], "unread": []})
            database["unread"].append({**item, "path": path})
            if path not in database["sources"]:
                database["sources"].append(path)
    for database in grouped.values():
        database["tables"] = _one_entry_per_table(database["tables"])
    return grouped


def erd_slug(name: str) -> str:
    """A database name turned into a filesystem-safe filename piece."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "db"


def erd_slugs(databases: dict[str, dict]) -> dict[str, str]:
    """A file slug for every database out of `group_databases`, unique across the whole set.

    Both `render` (writes erd-<slug>.mmd/.svg) and `skeleton` (prints a link to
    that same file in a card BEFORE render has had a chance to write it) must
    get the SAME slug for the same database. The second review round caught
    exactly this: `_write_erds` disambiguated slug collisions incrementally as
    it went, while `_schema_lines` computed `erd_slug(name)` fresh, with no
    knowledge of that collision — and on two databases sharing a slug
    ("user_data"/"user-data"), the second file's card linked to the FIRST
    database's diagram. A fabricated link is exactly the class of fact this
    function exists to rule out. One computation, one iteration order, both
    callers read it from here.
    """
    used: set[str] = set()
    slugs: dict[str, str] = {}
    for key, database in databases.items():
        base = erd_slug(database["name"])
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        slugs[key] = candidate
    return slugs


def _erd_type(text: str) -> str:
    """A column type spelled the way mermaid accepts inside an erDiagram."""
    cleaned = ERD_TYPE_RE.sub("_", (text or "unknown").strip()).strip("_")
    return cleaned or "unknown"


def _erd_entity_id(name: str) -> str:
    """A table name as a bare mermaid entity identifier.

    erDiagram only accepts [A-Za-z0-9_] unquoted. Verified against the real
    mermaid parser: `audit log { ... }` with no alias splits into two separate
    entities, `audit` and `log`, and the same name in a relationship line is a
    hard parse error (`Expecting COLON, got UNICODE_TEXT`), not just an ugly
    picture. `id["real name"] { ... }` parses cleanly in both cases — the real
    name stays visible to the reader as the label, the id is only used to link
    the lines to each other.
    """
    cleaned = ERD_TYPE_RE.sub("_", (name or "table").strip()).strip("_")
    return cleaned or "table"


def _erd_entity_ids(tables: list[dict]) -> dict[str, str]:
    """Table name -> entity id, unique even when two names sanitize alike.

    A counter keyed only on the sanitized base does not guarantee uniqueness:
    an already-clean name can land exactly on a suffix a different collision
    already claimed. `"a b"` and `"a-b"` both sanitize to base `a_b` and take
    `a_b` and `a_b_2`; the next name `"a_b_2"` is already clean by itself —
    its own base is also `a_b_2`, and that is that base's first occurrence,
    so the old version would hand it the id `a_b_2`, which is already taken.
    The real mermaid parser silently collapses two distinct tables into one
    entity in that case. So track the ids actually handed out, not a
    per-base count.
    """
    used: set[str] = set()
    ids: dict[str, str] = {}
    for table in tables:
        base = _erd_entity_id(table["name"])
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        ids[table["name"]] = candidate
    return ids


def erd_mermaid(database: dict) -> str:
    """One database's schema as a native mermaid erDiagram.

    GitHub renders erDiagram itself, so the .mmd is useful as-is; the SVG is
    drawn separately from the same facts, not by parsing this text.
    """
    lines = ["---", f"title: Schema of database {database['name']}", "---", "erDiagram"]
    tables = sorted(database["tables"], key=lambda t: t["name"])
    names = [table["name"] for table in tables]
    if len(names) != len(set(names)):
        # group_databases already deduplicates tables by name via
        # _one_entry_per_table; a caller who handed two same-named tables here
        # bypassed that guarantee. Silently merging them would draw a
        # confidently wrong picture — exactly what this function exists to
        # prevent — so fail loudly instead of merging silently.
        duplicate = next(name for name in names if names.count(name) > 1)
        raise ValueError(f"erd_mermaid: duplicate table name {duplicate!r} — call group_databases first")
    known = {table["name"] for table in tables}
    entity_id = _erd_entity_ids(tables)

    for table in tables:
        eid = entity_id[table["name"]]
        if eid == table["name"]:
            lines.append(f"  {eid} {{")
        else:
            # A quote inside the name itself would break the alias line; DDL
            # and ORM parsing already strips a name's own original quotes.
            label = table["name"].replace('"', "'")
            lines.append(f'  {eid}["{label}"] {{')
        for column in table.get("columns", []):
            marks = " PK" if column.get("pk") else (" FK" if column.get("fk") else "")
            lines.append(f"    {_erd_type(column.get('type'))} {column['name']}{marks}")
        lines.append("  }")

    for table in tables:
        for column in table.get("columns", []):
            target = (column.get("fk") or "").split(".")[0]
            if target and target in known and target != table["name"]:
                lines.append(
                    f"  {entity_id[target]} ||--o{{ {entity_id[table['name']]} : \"{column['name']}\""
                )

    return "\n".join(lines) + "\n"
