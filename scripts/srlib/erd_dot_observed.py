"""Observed stores (shapes reassembled from how the code uses them) drawn by Graphviz.

A key-value store is drawn as what it is: a KEY, then the VALUE kept under it. The key is
either written out in the source (a cache's `settings`, a namespace `mfa:otp:`) or computed —
then it is named by the variable the code passes, its declared type, and the functions that
write and remove it. Drawing only the value fields (as this did until 2026-10-16) showed
mercy's vault as a lone `enabled` box and fuse_core's `remote_runnables_repositories` as a
record with nothing to look it up by.
"""

from __future__ import annotations

import html
from itertools import pairwise

from .erd_dot_common import FONT_SIZE, NODESEP, RANKSEP, SPLINES, TAG_SIZE
from .stores import store_library

# A table is a contract, a structure is an object a process holds, and this is neither: a
# place data lives whose shape nobody wrote down. Grey on purpose — provisional.
OBSERVED_HEADER = "#ECECEC"
SECTION = "#F7F7F7"
CAPTION_SIZE = 14  # at the boxes' 20pt, one store's caption made a 1760pt-wide strip
GENERIC_KEYS = {"key", "k", "id", "_id"}
LISTED = 3  # functions named per key note; more read as "+N"


def _has_shape(store: dict) -> bool:
    return bool(store.get("columns") or store.get("keys") or store.get("key"))


def observed_groups(facts: dict) -> dict[str, dict]:
    """Module path -> the observed stores in it worth drawing: a store with a key or a field.

    Each store carries `library` — the outside client it is built from, or None for a class
    of this repo — because that, not its verbs, says whether it outlives the process.
    """
    groups: dict[str, dict] = {}
    for path in sorted(facts):
        blob = facts[path] or {}
        stores = [s for s in blob.get("stores") or [] if _has_shape(s)]
        if stores:
            groups[path] = {"name": path, "source": path,
                            "stores": [{**s, "library": store_library(blob, s)} for s in stores]}
    return groups


def _key_rows(store: dict) -> list[tuple[str, str]]:
    """(label, note) per key: the computed key first, then any written out in the source."""
    rows = []
    computed = store.get("key")
    if computed:
        names = [n for n in computed["names"] if n not in GENERIC_KEYS] or computed["names"]
        label = names[0] + (f": {computed['type']}" if computed.get("type") else "")
        notes = [f"also held as {', '.join(names[1:LISTED])}" + (f" +{len(names) - LISTED}" if len(names) > LISTED
                                                                     else "")] if len(names) > 1 else []
        for verb, funcs in (("set in", computed.get("writers")), ("removed in", computed.get("removers"))):
            if funcs:
                notes.append(f"{verb} {', '.join(funcs[:LISTED])}" + (f" +{len(funcs) - LISTED}" if len(funcs) > LISTED
                                                                       else ""))
        rows.append((label, "\n".join(notes) or "computed at run time"))
    for key in store.get("keys") or []:
        uses = key.get("uses", 0)
        kind = "prefix" if key.get("kind") == "prefix" else "literal key"
        rows.append((key["name"] + ("…" if kind == "prefix" else ""), kind + (f" ×{uses}" if uses > 1 else "")))
    return rows


def _value_rows(store: dict) -> list[tuple[str, str]]:
    """(field, note) per field of the value: its type when the code shows one, how many read and write it."""
    rows = []
    for column in store.get("columns") or []:
        counts = " ".join(f"{n}{c}" for n, c in ((column.get("reads"), "r"), (column.get("writes"), "w")) if n)
        rows.append((column["name"], " · ".join(p for p in (column.get("type"), counts) if p)))
    return rows


def _cells(label: str, note: str, bold: bool = False) -> str:
    shown = f"<B>{html.escape(label)}</B>" if bold else html.escape(label)
    return (f'<TR><TD ALIGN="LEFT">{shown}</TD><TD ALIGN="LEFT" BALIGN="LEFT">'
            f'<FONT POINT-SIZE="{TAG_SIZE}" COLOR="#777777">{_lines(note) or "&#160;"}</FONT></TD></TR>')


def _lines(note: str) -> str:
    return "<BR/>".join(html.escape(line) for line in note.split("\n"))


def _section(title: str) -> str:
    return (f'<TR><TD COLSPAN="2" ALIGN="LEFT" BGCOLOR="{SECTION}">'
            f'<FONT POINT-SIZE="{TAG_SIZE}" COLOR="#555555">{title}</FONT></TD></TR>')


def store_label(store: dict) -> str:
    """One store as a Graphviz HTML table: name and lifetime, then KEY rows, then VALUE rows."""
    library = store.get("library")
    where = f"{library} client · outside the process" if library else "a class of this repo"
    keys, values = _key_rows(store), _value_rows(store)
    body = _section("KEY") + "".join(_cells(label, note, bold=True) for label, note in keys) if keys else ""
    held = ", ".join((store.get("key") or {}).get("values") or [])
    body += _section("VALUE") + ("".join(_cells(label, note) for label, note in values) if values else
                                 _cells(held or "not read field by field", "kept whole" if held else ""))
    return ('<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="5">'
            f'<TR><TD COLSPAN="2" BGCOLOR="{OBSERVED_HEADER}"><B>{html.escape(store["name"])}</B></TD></TR>'
            f'<TR><TD COLSPAN="2" BGCOLOR="{OBSERVED_HEADER}">'
            f'<FONT POINT-SIZE="{TAG_SIZE}" COLOR="#555555">{html.escape(where)}</FONT></TD></TR>{body}</TABLE>>')


def observed_dot_svg(group: dict) -> str | None:
    """One module's observed stores, or None when Graphviz is absent or nothing has a shape.

    No fallback drawer: the hand-written one says "database" and "PK", and an observed store
    wearing those words is the confusion this picture exists to prevent.
    """
    try:
        import pygraphviz
    except Exception:
        return None
    stores = [store for store in group.get("stores") or [] if _has_shape(store)]
    if not stores:
        return None
    caption = (f"{'Key-value stores' if len(stores) > 1 else 'Key-value store'} in {group.get('name', '')}"
               " — key → value as observed in the code, nowhere declared")
    if any(store.get("columns") for store in stores):
        caption += "\nr / w: how many places read / write a field"
    unreadable = sum(store.get("unreadable", 0) for store in stores)
    if unreadable:
        caption += f" · {unreadable} field{'s' if unreadable > 1 else ''} could not be read statically"
    picture = pygraphviz.AGraph(
        directed=True, strict=False, rankdir="TB", splines=SPLINES, nodesep=NODESEP, ranksep=RANKSEP,
        bgcolor="white", fontsize=str(CAPTION_SIZE), fontname="Helvetica", label=caption, labelloc="b",
        fontcolor="#666666",
    )
    picture.node_attr["fontsize"] = str(FONT_SIZE)
    picture.node_attr["fontname"] = "Helvetica"
    for store in stores:
        picture.add_node(store["name"], shape="plaintext", label=store_label(store))
    for upper, lower in pairwise(stores):  # stacked: side by side, four were 4158pt wide
        picture.add_edge(upper["name"], lower["name"], style="invis")
    picture.layout(prog="dot")
    return picture.draw(format="svg").decode("utf-8")
