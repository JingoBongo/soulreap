"""The call-graph block of a file's skeleton: who calls what inside one file, and what is reached from outside."""

from __future__ import annotations

# A call graph is worth drawing only when it has something to say. A test file of
# forty flat `test_*` functions clears any node threshold and draws forty
# unconnected lines, so the gate is edges. Below these, the Entities table already
# tells the whole story.
CALL_GRAPH_MIN_EDGES = 3


CALL_GRAPH_MIN_NODES = 6


def _line_ranges(blob: dict) -> dict[str, str]:
    """`L<start>-<end>` per callable, methods included."""
    spans = {f["name"]: (f.get("start_line"), f.get("end_line")) for f in blob.get("functions", [])}
    for cls in blob.get("classes", []):
        for method in cls.get("methods", []):
            spans[method["name"]] = (method.get("start_line"), method.get("end_line"))
    return {name: f"L{a}-{b}" for name, (a, b) in spans.items() if a}


def _shape_of_root(blob: dict) -> dict[str, str]:
    """Shape hash per callable, but only for names that sit in a group of clones."""
    grouped: dict[str, str] = {}
    for group in blob.get("shape_groups") or []:
        for member in group.get("members", []):
            grouped[member] = group["shape"]
    return grouped


def _wrap_names(labels: list[str], first: str, indent: str, width: int = 84) -> list[str]:
    """`a · b · c`, wrapped, so a fold header does not run off the page."""
    out: list[str] = []
    line = first
    for index, label in enumerate(labels):
        piece = label if index == len(labels) - 1 else label + " ·"
        if len(line) + 1 + len(piece) > width and line.strip() not in ("", first.strip()):
            out.append(line)
            line = indent + piece
        else:
            line = f"{line} {piece}" if line.strip() else indent + piece
    out.append(line)
    return out


# Decorators that hand a callable to something outside this file: a web framework, a
# task queue, a CLI, a test runner. A callable carrying one of these is not uncalled just
# because no line in its own module names it — it is called by whatever it was registered
# with, and saying otherwise is how twenty-four Flask routes came to sit under a heading
# that reads as dead code.
#
# Matched on the LAST dotted segment, so `@app.route`, `@bp.route` and `@router.get` all
# land, and a local helper called `route()` does not sneak in as a decorator it never was.
ENTRY_DECORATORS = {
    # web
    "route", "get", "post", "put", "patch", "delete", "head", "options", "websocket",
    "errorhandler", "before_request", "after_request", "before_app_request",
    "teardown_request", "exception_handler", "middleware", "on_event", "hookimpl",
    # queues and schedules
    "task", "shared_task", "periodic_task", "actor", "scheduled", "cron", "job",
    # command lines
    "command", "group", "cli", "subcommand", "entrypoint",
    # test runners
    "fixture", "hookspec",
}


def _reached_from_outside(blob: dict) -> dict[str, str]:
    """`name -> the decorator that reaches it`, for callables something else calls.

    The information was already in the facts — the Entities table prints
    `@app.route('/api/venv-names', methods=['GET'])` right beside the name — it simply
    was not consulted when deciding what counts as uncalled.
    """
    found: dict[str, str] = {}

    def consider(item: dict) -> None:
        for decorator in item.get("decorators") or []:
            bare = decorator.lstrip("@").split("(")[0].strip()
            if bare.split(".")[-1] in ENTRY_DECORATORS:
                found[item["name"]] = bare
                return

    for function in blob.get("functions") or []:
        consider(function)
    for cls in blob.get("classes") or []:
        for method in cls.get("methods") or []:
            consider(method)
    return found


def _call_graph_lines(blob: dict) -> list[str]:
    """The file's own call graph as a tree: entry points first, helpers under them.

    Between "this file imports that file" and the line-by-line pseudocode there was
    nothing, and on a file with one public function and thirteen private helpers
    that middle level is the thing a reader is trying to reconstruct by hand.

    A shared helper is expanded once and referred to afterwards. Expanding it under
    every caller is how a twelve-node file turns into forty lines — afas_client's
    _fetch_connector has seven callers.
    """
    calls = {name: list(dsts) for name, dsts in (blob.get("calls") or {}).items()}
    if not calls:
        return []

    callers: dict[str, set[str]] = {}
    edges = 0
    for source, targets in calls.items():
        for target in targets:
            callers.setdefault(target, set()).add(source)
            edges += 1
    if edges < CALL_GRAPH_MIN_EDGES or len(calls) < CALL_GRAPH_MIN_NODES:
        return []

    spans = _line_ranges(blob)
    order = {name: spans.get(name, "L0-0") for name in calls}

    def by_line(name: str) -> int:
        text = order.get(name, "L0-0")
        return int(text[1:].split("-")[0] or 0)

    roots = sorted((n for n in calls if n not in callers and calls[n]), key=by_line)
    lonely = sorted((n for n in calls if n not in callers and not calls[n]), key=by_line)

    rows: list[tuple[str, str]] = []   # (left column, anchor)
    expanded: set[str] = set()

    def walk(name: str, prefix: str, path: tuple[str, ...]) -> None:
        children = sorted(calls.get(name, ()), key=by_line)
        for index, child in enumerate(children):
            last = index == len(children) - 1
            stem = prefix + ("└── " if last else "├── ")
            shared = f" ×{len(callers[child])}" if len(callers.get(child, ())) > 1 else ""
            if child in path:
                rows.append((f"{stem}{child}{shared} ↻", ""))
                continue
            if child in expanded:
                rows.append((f"{stem}{child}{shared} ↑", ""))
                continue
            expanded.add(child)
            rows.append((f"{stem}{child}{shared}", order.get(child, "")))
            walk(child, prefix + ("    " if last else "│   "), (*path, child))

    # Roots that are clones of one another share one block. On afas_client five
    # same-shape lookups drew five identical blocks of `↑` — the fold that already
    # exists for the pseudocode belongs here too.
    shapes = _shape_of_root(blob)
    grouped_roots: dict[str, list[str]] = {}
    for root in roots:
        grouped_roots.setdefault(shapes.get(root) or f"solo:{root}", []).append(root)

    blocks: list[list[tuple[str, str]]] = []
    headers: list[list[str]] = []
    for shape, members in sorted(grouped_roots.items(), key=lambda kv: by_line(kv[1][0])):
        rows = []
        for member in members:
            expanded.add(member)
        if len(members) == 1:
            rows.append((f"ENTRY  {members[0]}", order.get(members[0], "")))
            headers.append([])
        else:
            labels = [f"{name} {order.get(name, '')}".strip() for name in members]
            wrapped = _wrap_names(labels, "ENTRY ", "       ")
            wrapped.append(f"       {len(members)} callables of one shape ({shape}) — "
                           "identical bodies, see Shape groups. Each:")
            headers.append(wrapped)
            rows.append(("", ""))
        walk(members[0], "   ", (members[0],))
        blocks.append(rows)

    # A group nothing outside it calls — a closed cycle. Naming it ENTRY would be a
    # lie, and dropping it would hide code from a reader.
    leftover = sorted((n for n in calls if n not in expanded and calls[n]), key=by_line)
    for start in leftover:
        if start in expanded:
            continue
        rows = []
        expanded.add(start)
        rows.append((f"CYCLE  {start}", order.get(start, "")))
        walk(start, "   ", (start,))
        blocks.append(rows)
        headers.append([])

    width = max((len(left) for block in blocks for left, _ in block), default=0) + 2
    out = [
        "",
        "## Call graph",
        '  Read the indentation as "calls". A name at column 0 is an ENTRY — nothing else in',
        "  this file calls it; everything indented under one is a helper reached through it.",
        "  `helper` means nothing in THIS file's own code calls it from the outside — it may",
        "  still be imported elsewhere; the parser does not resolve names across files.",
        "  ×N = how many places call that helper · ↑ = already expanded above · ↻ = recursion",
        "  Two lists may follow. REACHED FROM OUTSIDE is callables a framework calls — a",
        "  route, a task, a CLI command — named by the decorator that registers them. Only",
        "  NO CALLS is a dead-code smell, and then only if nothing imports the name either.",
        "",
    ]
    for index, block in enumerate(blocks):
        if index:
            out.append("")
        out += headers[index]
        out += [f"{left.ljust(width)}{anchor}".rstrip() for left, anchor in block if left or anchor]
    if lonely:
        registered = _reached_from_outside(blob)
        outside = [n for n in lonely if n in registered]
        dead = [n for n in lonely if n not in registered]
        if outside:
            out.append("")
            out.append(
                "REACHED FROM OUTSIDE  "
                + " · ".join(
                    f"{n} {order.get(n, '')} @{registered[n]}".strip() for n in outside
                )
                + " — nothing in this file calls them and nothing needs to: each is"
                " registered with something that does"
            )
        if dead:
            out.append("")
            out.append(
                "NO CALLS  "
                + " · ".join(f"{n} {order.get(n, '')}".strip() for n in dead)
                + " — nothing in this file calls them, and no decorator hands them to"
                " anything that would. Either dead, or reached from another module,"
                " which this parser cannot see"
            )
    return out
