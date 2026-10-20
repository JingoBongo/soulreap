"""The pictures render draws.

An ERD per database by default; tasks, observed stores, structures and packages
behind `render --diagrams all`.
"""

from __future__ import annotations

import re
from pathlib import Path

from .diagrams import display_label, erd_slugs, group_databases
from .diagrams import package_dot as _dot
from .diagrams import package_graph as _package_graph
from .diagrams import packages_empty_note as _packages_empty_note
from .dviz import dot_svg as _dot_svg
from .erd_dot import erd_dot_svg as _erd_dot_svg
from .erd_dot import observed_dot_svg as _observed_dot_svg
from .erd_dot import observed_groups, structure_groups, structure_slug
from .erd_dot import packages_dot_svg as _packages_dot_svg
from .erd_dot import structures_dot_svg as _structures_dot_svg
from .erd_dot import task_graph as _task_graph
from .erd_dot import tasks_dot_svg as _tasks_dot_svg
from .svg import context_svg as _context_svg
from .svg import erd_svg as _erd_svg
from .svg import packages_svg as _packages_svg
from .svglint import _lint_svg_geometry

_UNREAD_DB_EVIDENCE_RE = re.compile(r"^sql:.*create\s+table", re.IGNORECASE | re.DOTALL)


def _write_erds(out: Path, facts: dict) -> dict:
    """One diagram per database, one per module of linked structures, and a report.

    Stale erd-*.mmd/.svg files are removed: a database that was dropped from the
    code must not leave behind a picture the next reader would mistake for live.
    The structures diagrams follow the same rule, under their own prefix — they are
    written as separate files on purpose, because a box promising a row in Postgres
    and a box describing an object held for the length of a request must not share
    a canvas.
    """
    diagrams = out / "diagrams"
    diagrams.mkdir(parents=True, exist_ok=True)
    databases = group_databases(facts)
    slugs = erd_slugs(databases)

    keep: set[str] = set()
    tables = 0
    unread: list[str] = []
    drawn = 0
    for key, database in databases.items():
        for item in database["unread"]:
            unread.append(f"{item['path']}:{item['line']}: {item['why']}")
        if not database["tables"]:
            continue
        slug = slugs[key]
        keep.add(slug)
        tables += len(database["tables"])
        drawn += 1
        # Graphviz when its wheel is there, our own drawer when it is not. Measured on a
        # ten-table schema: half the ink, a quarter of the canvas area, one crossing less.
        picture = _erd_dot_svg(database) or _erd_svg(database)
        (diagrams / f"erd-{slug}.svg").write_text(picture, encoding="utf-8")

    for stale in list(diagrams.glob("erd-*.dot")) + list(diagrams.glob("erd-*.mmd")) + list(diagrams.glob("erd-*.svg")):
        if stale.stem[len("erd-") :] not in keep:
            stale.unlink(missing_ok=True)

    # A file with db evidence that no detector pulled anything out of is not
    # "no database" — it is "could not read the schema". That must not pass
    # silently.
    #
    # But `io.db` evidence is set by ANY database touch — importing a driver,
    # calling .execute() — which means a plain consumer of the schema (a
    # route, a test) that never had a reason to declare a table trips it too.
    # The final review caught this by execution: on osintov, where the schema
    # read completely, the warning still fired on 10 files, 5 of them tests.
    # Narrow the evidence to entries where `_detect_io` actually saw text that
    # looks like `CREATE TABLE` — i.e. the DDL detector was entitled to expect
    # a table and did not find one, not merely "this file touches a database
    # at all".
    #
    # The anchor is `^sql:` without requiring `CREATE TABLE` to come right
    # after it — the second review round caught that the "CREATE TABLE first"
    # version stayed silent on multi-statement scripts (`DROP TABLE ...;
    # CREATE TABLE ...`) and on a line with a leading SQL comment (`-- schema
    # v2\nCREATE TABLE ...`), both ordinary shapes, not contrived ones. DOTALL
    # is needed for the second case: `.` otherwise does not match the newline
    # inside the evidence string.
    for path in sorted(facts):
        blob = facts[path]
        block = blob.get("schema") or {}
        if block.get("tables") or block.get("unread"):
            continue
        db_evidence = blob.get("io", {}).get("db") or []
        if any(_UNREAD_DB_EVIDENCE_RE.match(item) for item in db_evidence):
            unread.append(f"{path}: db evidence, but no schema was found")

    return {
        "schema_databases": drawn, "schema_tables": tables, "schema_unread": unread,
        **_write_structure_diagrams(diagrams, facts),
        **_write_observed_diagrams(diagrams, facts),
        **_write_task_diagram(diagrams, facts),
    }


def _write_task_diagram(diagrams: Path, facts: dict) -> dict:
    """One picture for the whole repository: who enqueues which task.

    Repository-wide because that is the shape of the fact — a task is declared in one
    file and enqueued from others, so a per-module picture would cut every edge it
    has. This is the only part of the broker question with anything honest to draw:
    the task is a decorated function and the enqueue names it, while a raw pub/sub
    topology is built from verbs that turned out to be `subprocess.poll()` and an
    image API's `subscribe`.
    """
    graph = _task_graph(facts)
    picture = _tasks_dot_svg(graph, display=display_label) if graph["tasks"] else None
    if picture:
        (diagrams / "tasks.svg").write_text(picture, encoding="utf-8")
    else:
        (diagrams / "tasks.svg").unlink(missing_ok=True)
    return {
        "tasks_declared": len(graph["tasks"]),
        "tasks_enqueued": len(graph["edges"]),
    }


def _write_observed_diagrams(diagrams: Path, facts: dict) -> dict:
    """A picture per module whose stores gave up a shape.

    Its own file, never a section of an ERD, and that separation is the feature. A
    box of observed keys is drawn exactly as confidently as a box of declared columns;
    the only things that keep a reader from mistaking one for the other are the
    caption, the third header colour and the fact that the two never share a canvas.
    """
    keep: set[str] = set()
    drawn = 0
    fields = 0
    keys = 0
    unreadable = 0
    for path, group in observed_groups(facts).items():
        picture = _observed_dot_svg(group)
        if picture is None:
            continue
        slug = structure_slug(path)
        keep.add(slug)
        drawn += 1
        for store in group["stores"]:
            fields += len(store.get("columns") or [])
            keys += len(store.get("keys") or [])
            unreadable += store.get("unreadable", 0)
        (diagrams / f"observed-{slug}.svg").write_text(picture, encoding="utf-8")

    for stale in diagrams.glob("observed-*.svg"):
        if stale.stem[len("observed-") :] not in keep:
            stale.unlink(missing_ok=True)

    return {
        "observed_diagrams": drawn,
        "observed_stores": sum(
            len(group["stores"]) for path, group in observed_groups(facts).items()
            if structure_slug(path) in keep
        ),
        "observed_fields": fields,
        "observed_keys": keys,
        "observed_unreadable": unreadable,
    }


def _write_structure_diagrams(diagrams: Path, facts: dict) -> dict:
    """A picture per module whose declared structures point at one another.

    Graphviz only. There is no fallback to the hand-written drawer: it captions its
    output "Schema of database" and tags keys PK, and a page of dataclasses wearing
    those words is worse than no page at all. Without the wheel the structures are
    still in every card, field by field.
    """
    keep: set[str] = set()
    drawn = 0
    structures = 0
    for path, group in structure_groups(facts).items():
        picture = _structures_dot_svg(group)
        if picture is None:
            continue
        slug = structure_slug(path)
        keep.add(slug)
        drawn += 1
        structures += len(group["structures"])
        (diagrams / f"structures-{slug}.svg").write_text(picture, encoding="utf-8")

    for stale in diagrams.glob("structures-*.svg"):
        if stale.stem[len("structures-") :] not in keep:
            stale.unlink(missing_ok=True)

    return {"structure_diagrams": drawn, "structures_drawn": structures}


def _draw_svgs(
    out: Path, facts: dict, draw_context: bool = False, draw_packages: bool = True
) -> list[str]:
    """An SVG beside every .mmd, so the diagrams are readable without a mermaid engine.

    The packages picture is drawn from the same facts as its .mmd, never from the
    mermaid text, so the pair can never disagree. The two hand-written ones have no
    source but the mermaid, and that parser knows a defined subset: when it cannot read
    a diagram it writes nothing and says so. A confidently wrong picture is worse than
    no picture.

    `context.svg` is NOT this function's any more — `_render_locked` draws it through
    graphviz from the merged five-source edges, and writing or deleting it here would
    undo that on the same run. `draw_context=True` is kept for the fallback drawer only.
    """
    diagrams = out / "diagrams"
    diagrams.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    drawings = []
    if draw_packages:
        # dot first, the hand drawer second. Measured on nvm-forecast, where the hand
        # drawer scored 71 crossings over 42 edges: this is the diagram that made the
        # linter's crossing gate fire on our own output, run after run.
        picture = _packages_dot_svg(_package_graph(facts), display=display_label)
        drawings.append(("packages", picture or _packages_svg(facts)))
    else:
        (diagrams / "packages.svg").unlink(missing_ok=True)
    if draw_context:
        drawings.insert(0, ("context", _context_svg(facts)))
    for name, drawing in drawings:
        (diagrams / f"{name}.svg").write_text(drawing, encoding="utf-8")

    warnings.extend(draw_hand_written(diagrams))
    warnings.extend(_lint_svg_geometry(diagrams))
    return warnings


def draw_hand_written(diagrams: Path) -> list[str]:
    """Draw the diagrams the reader wrote (`architecture.dot`, `flow.dot`) to SVG; warnings for bad ones.

    The runtime topology — processes, the stores and their files, the outside systems and what
    crosses each edge — is what no parser can draw (imports never say how deployed pieces talk),
    and v10 had stopped drawing it in the default run.
    """
    warnings: list[str] = []
    for name in ("architecture", "flow"):
        source = diagrams / f"{name}.dot"
        if not source.exists():
            continue
        try:
            drawing, unread = _dot_svg(source.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:  # a malformed diagram must not fail the whole render
            drawing, unread = "", [f"{type(exc).__name__}: {exc}"]
        if drawing and not unread:
            (diagrams / f"{name}.svg").write_text(drawing, encoding="utf-8")
        else:
            (diagrams / f"{name}.svg").unlink(missing_ok=True)
            detail = "; ".join(unread[:2]) if unread else "nothing drawable found"
            warnings.append(f"{name}.dot: not drawn — {detail}")
    return warnings


def draw_all(out: Path, facts: dict) -> dict:
    """The optional pictures (`render --diagrams all`): packages, structures, observed stores, tasks."""
    diagrams = Path(out) / "diagrams"
    diagrams.mkdir(parents=True, exist_ok=True)
    packages, stats = _dot(facts)
    packages_drawn = stats.get("graph_edges", 0) > 0
    if packages_drawn:
        (diagrams / "packages.dot").write_text(packages, encoding="utf-8")
    else:
        (diagrams / "packages.dot").unlink(missing_ok=True)
        stats["packages_note"] = _packages_empty_note(stats)
    stats.update(_write_task_diagram(diagrams, facts))
    stats.update(_write_observed_diagrams(diagrams, facts))
    stats.update(_write_structure_diagrams(diagrams, facts))
    stats["svg_warnings"] = _draw_svgs(out, facts, draw_context=False, draw_packages=packages_drawn)
    return stats
