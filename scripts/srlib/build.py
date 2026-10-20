"""Facts for every file in a repository, re-parsed only where the sha moved.

The one place facts are built in v10. `plan`, `render` and every query call it,
so an answer is always about the working tree, never about the last plan run.
A file whose sha matches the previous blob keeps that blob verbatim; the import
graph is rebuilt every time because one changed file can change who imports whom.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import context as _context
from .facts import FileFacts, extract_source
from .graph import Graph, build_graph
from .schema import detect_sql_ddl, detect_sqlite, sqlite_schema_sha
from .walk import FileEntry, walk


@dataclass
class FactsBuild:
    """The outcome of one build: facts per file and which files were parsed anew."""

    entries: list[FileEntry]
    files: dict[str, dict]
    context: list
    graph: Graph
    reparsed: list[str] = field(default_factory=list)


def _schema_items(root: Path, entries: list[FileEntry]) -> dict[str, FileFacts]:
    """Schema-only facts for .sql migrations and database files (same rule as the v9 plan)."""
    items: dict[str, FileFacts] = {}
    for entry in entries:
        if entry.kind != "non_code" or entry.category not in {"migration", "database"}:
            continue
        if entry.category == "migration":
            text = (root / entry.path).read_text(encoding="utf-8", errors="replace")
            found, missed = detect_sql_ddl(text, entry.path)
            lang, lines, sha = "sql", entry.lines, entry.sha
        else:
            found, missed = detect_sqlite(root / entry.path)
            lang, lines, sha = "sqlite", len(found), sqlite_schema_sha(found)
        if not found and not missed:
            continue
        item = FileFacts(path=entry.path, lang=lang, lines=lines)
        item.sha = sha
        item.schema = {"tables": found, "targets": [], "unread": missed}
        items[entry.path] = item
    return items


def _console_scripts(root: Path, files: dict[str, dict]) -> list[tuple[str, str]]:
    """`[project.scripts]` / `[tool.poetry.scripts]` targets (`pkg.mod:func`) as (file, func)."""
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    targets = {**(data.get("project", {}).get("scripts") or {}),
               **(data.get("tool", {}).get("poetry", {}).get("scripts") or {})}
    found = []
    for spec in targets.values():
        module, _, func = str(spec).partition(":")
        stem = module.replace(".", "/")
        for rel in (f"{stem}.py", f"src/{stem}.py", f"{stem}/__init__.py", f"src/{stem}/__init__.py"):
            if rel in files and func:
                found.append((rel, func.split(".")[0]))
                break
    return found


def build_facts(root: Path | str, previous: dict[str, dict] | None = None) -> FactsBuild:
    """Facts for every code file under *root*, reusing *previous* blobs whose sha matches."""
    root = Path(root)
    previous = previous or {}
    entries = walk(root)
    built: dict[str, FileFacts | dict] = {}
    reparsed: list[str] = []
    for entry in entries:
        if entry.kind != "code":
            continue
        old = previous.get(entry.path)
        if old and old.get("sha") == entry.sha:
            built[entry.path] = old
            continue
        text = (root / entry.path).read_text(encoding="utf-8", errors="replace")
        item = extract_source(text, entry.path)
        item.sha = entry.sha
        built[entry.path] = item
        reparsed.append(entry.path)
    for path, item in _schema_items(root, entries).items():
        if (previous.get(path) or {}).get("sha") != item.sha:
            reparsed.append(path)
        built[path] = item

    graph = build_graph(built, root)
    files: dict[str, dict] = {}
    for path, item in built.items():
        blob = dict(item) if isinstance(item, dict) else item.to_dict()
        blob.pop("script_entries", None)  # recomputed below from pyproject, never carried over
        blob["deps"] = graph.imports.get(path, [])
        blob["imported_by"] = graph.imported_by.get(path, [])
        targets = graph.resolved.get(path, {})
        blob["imports"] = [{**imp, "target": targets.get(imp["module"])} for imp in blob.get("imports", [])]
        files[path] = blob
    for rel, func in _console_scripts(root, files):
        files[rel].setdefault("script_entries", []).append(func)
    return FactsBuild(entries, files, _context.collect(root, entries), graph, sorted(reparsed))
