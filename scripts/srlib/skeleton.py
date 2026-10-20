"""`soulreap skeleton <file>`: every fact about one file, from facts that follow the working tree."""

from __future__ import annotations

from pathlib import Path

from .callgraph import _call_graph_lines
from .diagrams import erd_slugs, group_databases
from .erd_dot import structure_groups, structure_slug
from .freshness import fresh_facts

DECORATOR_MAX_CHARS = 400


def _decorator_lines(blob: dict, indent: str) -> list[str]:
    """Decorators, printed above their own entity, exactly as the source spells them.

    They are not decoration. ``@app.get('/leave', operation_id=..., summary=...)``
    IS the tool contract an LLM reads to decide whether to call that endpoint;
    ``@property`` changes how a caller writes the call; ``@pytest.fixture`` is the
    entire wiring of a test suite. The parser has always captured these — they were
    dropped here, so every card writer had to re-read the source and guess where to
    put them, and 15% of this repo's callables lost the most important fact about
    themselves on the way into the skeleton.
    """
    out: list[str] = []
    for decorator in blob.get("decorators") or []:
        text = decorator if len(decorator) <= DECORATOR_MAX_CHARS else decorator[:DECORATOR_MAX_CHARS] + "…"
        out.append(f"{indent}@{text}")
    return out


def _const_lines(const: dict, indent: str = "  ") -> list[str]:
    """One const, whole. A multi-line literal (a key map, a table) keeps its shape instead
    of being clipped — `EDITABLE = {…}` losing eight of nine keys is the failure this fixes."""
    value = str(const.get("value", ""))
    head = f"{indent}const {const['name']} = "
    tail = f"  [L{const['line']}]"
    if "\n" not in value:
        return [f"{head}{value}{tail}"]
    parts = value.splitlines()
    out = [f"{head}{parts[0]}"]
    out += [f"{indent}    {line}" for line in parts[1:-1]]
    out.append(f"{indent}    {parts[-1]}{tail}")
    return out


def _doc_lines(fn: dict, indent: str) -> list[str]:
    """The callable's docstring past its first line, where contracts and dropped cases live."""
    return [f"{indent}| {line}" for line in fn.get("doc_more") or []]


def _literal_lines(fn: dict, indent: str) -> list[str]:
    """Command lines and regex patterns, verbatim — the detail a summary loses."""
    return [f"{indent}{lit['kind']}: {lit['text']}" for lit in fn.get("literals") or []]


def _hazard_lines(fn: dict, blob: dict, indent: str) -> list[str]:
    """The callable's hazards, or an honest n/a for a language the hazard reader cannot parse."""
    hazards = fn.get("hazards")
    if hazards is None and blob.get("lang") != "python":
        return [f"{indent}hazards: n/a (not parsed for this language)"]
    if not hazards:
        return []
    return [f"{indent}hazards: " + "; ".join(f"{h['kind']} {h['what']} L{h['line']}" for h in hazards)]


def _entity_lines(blob: dict) -> list[str]:
    lines: list[str] = []
    for const in blob.get("constants", []):
        lines += _const_lines(const)
    for cls in blob.get("classes", []):
        bases = f"({', '.join(cls.get('bases', []))})" if cls.get("bases") else ""
        doc = f"  — {cls['doc']}" if cls.get("doc") else ""
        lines += _decorator_lines(cls, "  ")
        lines.append(f"  class {cls['name']}{bases}  [L{cls['start_line']}-L{cls['end_line']}]{doc}")
        # A dataclass has no methods, so before fields were parsed its Entities row was
        # a name and nothing else — the emptiest a row here gets, on exactly the classes
        # whose whole content is their fields.
        for item in cls.get("fields", []):
            default = f" = {item['default']}" if item.get("default") is not None else ""
            lines.append(f"    {item['name']}: {item['type']}{default}  [L{item['line']}]")
        for method in cls.get("methods", []):
            prefix = "async " if method.get("is_async") and not method["signature"].startswith("async") else ""
            mdoc = f"  — {method['doc']}" if method.get("doc") else ""
            lines += _decorator_lines(method, "    ")
            lines.append(
                f"    {prefix}{method['signature']}  [L{method['start_line']}-L{method['end_line']}]{mdoc}"
            )
            lines += _doc_lines(method, "      ")
            lines += _hazard_lines(method, blob, "      ")
            lines += _literal_lines(method, "      ")
    for fn in blob.get("functions", []):
        signature = fn["signature"]
        prefix = "async " if fn.get("is_async") and not signature.startswith("async") else ""
        marks = []
        if fn.get("is_generator"):
            marks.append("generator")
        if fn["name"] in blob.get("recursive", []):
            marks.append("recursive")
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        doc = f"  — {fn['doc']}" if fn.get("doc") else ""
        lines += _decorator_lines(fn, "  ")
        lines.append(
            f"  {prefix}{signature}  [L{fn['start_line']}-L{fn['end_line']}]{suffix}{doc}"
        )
        lines += _doc_lines(fn, "    ")
        lines += _hazard_lines(fn, blob, "    ")
        lines += _literal_lines(fn, "    ")
    return lines


def _store_lines(blob: dict, target: str) -> list[str]:
    """The named stores this file keeps data in, and the shape its own uses reveal.

    A card is the per-file document, and until this existed the single most important
    fact about `fuse.py` — that every piece of the service's state lives in four stores
    it owns and none of them is declared anywhere — reached the diagram and never
    reached the card. The facts were collected, the picture was drawn, and the writer
    of the card was never told.

    Marked as observed on every line, because that is the difference a reader has to
    keep: a table is a contract, this is a reading of traces.
    """
    stores = blob.get("stores") or []
    if not stores:
        return []
    lines = ["", "## Stores",
             "  Named stores this file reads and writes. Their shape is OBSERVED — "
             "collected from the string keys used against them, declared nowhere."]
    if any(store.get("columns") or store.get("keys") for store in stores):
        lines.append(f"  diagram  ->  diagrams/observed-{structure_slug(target)}.svg")
    for store in stores:
        lines.append(
            f"  {store['name']}  via {store['binding']} = {store['ctor']}(...)  "
            f"[L{store['line']}]"
        )
        for column in store.get("columns") or []:
            lines.append(
                f"    field {column['name']}  read {column.get('reads', 0)}x  "
                f"written {column.get('writes', 0)}x"
            )
        for key in store.get("keys") or []:
            lines.append(f"    {key.get('kind', 'key')} {key['name']}  used {key.get('uses', 0)}x")
        if store.get("dynamic_keys"):
            lines.append(
                f"    keyed by a value computed at run time in {store['dynamic_keys']} place(s)"
            )
        if store.get("unreadable"):
            lines.append(
                f"    {store['unreadable']} field(s) could not be read statically"
            )
    return lines


def _task_lines(blob: dict, facts: dict) -> list[str]:
    """Background tasks this file declares, and the ones it puts on a queue.

    Both halves matter to a card: a declared task runs somewhere else entirely, and an
    enqueue is a call whose work does not happen where the call is written.
    """
    tasks = blob.get("tasks") or {}
    declared = tasks.get("declared") or []
    calls = tasks.get("calls") or []
    if not declared and not calls:
        return []
    lines = ["", "## Tasks", "  Work that runs later, in a worker process.",
             "  diagram  ->  diagrams/tasks.svg"]
    for task in declared:
        extra = []
        if task.get("task_name"):
            extra.append(f"registered as {task['task_name']}")
        if task.get("queue"):
            extra.append(f"queue {task['queue']}")
        suffix = ("  " + ", ".join(extra)) if extra else ""
        lines.append(f"  declares {task['name']}  @{task['decorator']}  [L{task['line']}]{suffix}")
    known = {
        item["name"]
        for other in facts.values()
        for item in ((other or {}).get("tasks") or {}).get("declared") or []
    }
    for call in calls:
        where = "" if call["task"] in known else "  (declared outside this repository)"
        lines.append(f"  enqueues {call['task']}  [L{call['line']}]{where}")
    return lines


def _schema_lines(blob: dict, target: str, facts: dict) -> list[str]:
    """Tables declared by this file, exactly as the parser read them.

    Printed so the card never lists columns again: a column typed from memory
    by the model is a fabricated fact, and here it has already been parsed.

    The final review found a gap: the card template asks for a link to
    `diagrams/erd-<slug>.svg`, but before this fix the skeleton named neither
    the database nor its slug — and `render`, the only place that computes
    them, runs AFTER the cards are already written (step 2 comes before step 3
    in SKILL.md). The carder could neither read the slug off the skeleton nor
    look in the diagrams/ directory, which did not exist yet. Verified on a
    real card, osintov/app/db.py: its Schema section carried no database
    information at all. `group_databases` is a pure function of already-loaded
    `facts`, so it needs no running `render`, and calling it right here is
    safe.

    The second review round caught two more holes in the first version of this fix:

    1. The slug was computed here fresh, through a bare `erd_slug(name)`, with
       no knowledge of the disambiguation `_write_erds` does as it walks its
       own loop. On two databases sharing a slug ("user_data"/"user-data"),
       the second database's card linked to the FIRST database's diagram —
       exactly the fabricated link this section exists to rule out. Both
       places now read the same `erd_slugs(...)`, computed over the same
       `group_databases` walk, so they cannot diverge that way anymore.
    2. The `database` line was printed even for a group with zero tables (for
       example, a file with nothing but unreadable DDL, which
       `group_databases`'s unread loop now also adds to `sources`) — but
       `_write_erds` never writes a file for such a group. The card promised
       a diagram that will never exist. Skip a group with no tables under the
       same condition `_write_erds` uses to skip it.
    """
    block = blob.get("schema") or {}
    tables, unread = block.get("tables") or [], block.get("unread") or []
    if not tables and not unread:
        return []

    lines = ["", "## Schema"]
    grouped = group_databases(facts)
    slugs = erd_slugs(grouped)
    for key, database in grouped.items():
        if database["tables"] and target in database["sources"]:
            lines.append(f"  database {database['name']}  ->  diagrams/erd-{slugs[key]}.svg")
    for table in tables:
        lines.append(f"  table {table['name']}  [L{table['line']}-L{table['end_line']}]  via {table['detector']}")
        for column in table.get("columns", []):
            marks = []
            if column.get("pk"):
                marks.append("PK")
            if column.get("fk"):
                marks.append(f"FK -> {column['fk']}")
            if column.get("nullable") is False:
                marks.append("NOT NULL")
            suffix = (" " + " ".join(marks)) if marks else ""
            lines.append(f"    {column['name']} {column.get('type') or '?'}{suffix}")
    for item in unread:
        lines.append(f"  not read [L{item['line']}]: {item['why']}")
    return lines


def _structure_lines(blob: dict, target: str, facts: dict) -> list[str]:
    """Structures this file declares — a section of its own, never under Schema.

    Its own heading is the point. A dataclass in a section called Schema reads as a
    table that exists in a database, and a card is the last place that confusion can
    still be caught before it is copied into prose.

    The diagram is named only when one will actually be drawn: `structure_groups`
    keeps a module out of the picture when nothing in it points at anything, so
    printing the link unconditionally would promise a file that render never writes
    — the same hole the Schema section had, found on a real card.
    """
    structures = ((blob.get("schema") or {}).get("structures")) or []
    if not structures:
        return []

    lines = ["", "## Structures", "  Declared shapes held in memory — not database tables."]
    if target in structure_groups(facts):
        lines.append(f"  diagram  ->  diagrams/structures-{structure_slug(target)}.svg")
    for item in structures:
        lines.append(
            f"  {item['detector']} {item['name']}  [L{item['line']}-L{item['end_line']}]"
        )
        for column in item.get("columns", []):
            marks = []
            if column.get("fk"):
                marks.append(f"-> {column['fk']}" + (" (many)" if column.get("many") else ""))
            if column.get("required"):
                marks.append("required")
            # Only the fields this section knows something extra about. Every field of
            # every class is already printed above with its annotation and default, and
            # a skeleton that says the same thing twice spends a carder's attention on
            # deciding whether the two copies differ.
            if not marks:
                continue
            lines.append(f"    {column['name']}: {column.get('type') or '?'} " + " ".join(marks))
    return lines


def cmd_skeleton(root: Path | str, out: Path | str, target: str) -> str:
    """Print every fact about one file, from facts refreshed against the working tree."""
    facts = fresh_facts(root, out)["files"]
    blob = facts.get(target)
    if blob is None:
        message = f"no facts for {target}: not a code file under {root}"
        print(message)
        return message

    lines = [f"# {target}", f"lang: {blob.get('lang')}  lines: {blob.get('lines')}"]
    if blob.get("doc"):
        lines.append(f"module docstring: {blob['doc']}")
        for more in blob.get("doc_more") or []:
            lines.append(f"    {more}")
    if blob.get("parse_error"):
        lines.append(f"PARSE ERROR: {blob['parse_error']}")

    flags = []
    if blob.get("concurrency"):
        flags.append("concurrency: " + ", ".join(blob["concurrency"]))
    if blob.get("recursive"):
        flags.append("recursive: " + ", ".join(blob["recursive"]))
    if blob.get("io"):
        flags.append("io: " + "; ".join(f"{k} ({', '.join(v)})" for k, v in blob["io"].items()))
    if flags:
        lines.append("")
        lines.append("## Facts")
        lines += [f"  {f}" for f in flags]

    lines.append("")
    lines.append("## Imports")
    for item in blob.get("imports", []):
        names = f" ({', '.join(item['names'])})" if item.get("names") else ""
        lines.append(f"  {item['module']}{names}  [L{item.get('line', 0)}]")
    lines.append("  internal deps: " + (", ".join(blob.get("deps", [])) or "none"))
    lines.append("  imported by: " + (", ".join(blob.get("imported_by", [])) or "nobody"))

    lines.append("")
    lines.append("## Entities")
    lines += _entity_lines(blob) or ["  (none)"]

    groups = blob.get("shape_groups") or []
    if groups:
        lines.append("")
        lines.append("## Shape groups")
        lines.append("  Callables whose bodies are structurally identical — cover the names and")
        lines.append("  they read the same. Show ONE of them in full as the shape, then table the")
        lines.append("  rest with what varies. Never invent a name for the shape: the exemplar is")
        lines.append("  a real callable and keeps its own name, kind and line range.")
        for group in groups:
            lines.append(f"  shape {group['shape']}: {', '.join(group['members'])}")
            for name in group["members"]:
                varies = group["varies"].get(name) or []
                lines.append(f"    {name} varies in: " + (", ".join(varies) or "nothing"))

    lines += _call_graph_lines(blob)
    lines += _schema_lines(blob, target, facts)
    lines += _structure_lines(blob, target, facts)
    lines += _store_lines(blob, target)
    lines += _task_lines(blob, facts)

    if blob.get("todos"):
        lines.append("")
        lines.append("## Markers")
        for todo in blob["todos"]:
            lines.append(f"  {todo['kind']} [L{todo['line']}] {todo['text']}")

    text = "\n".join(lines)
    print(text)
    return text
