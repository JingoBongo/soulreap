"""Structures declared in code: dataclass, TypedDict, NamedTuple and pydantic models.

Read into the same shape a table detector returns, but never tables: they are kept in
a list of their own all the way to their own diagram. Field annotations are resolved to
links between structures, with cardinality, nullability and a `required` mark.
"""

from __future__ import annotations

import ast

from .schema_orm import _tablename_of

# Structures declared in code — dataclasses, TypedDicts, NamedTuples, pydantic
# models. They are read into the SAME shape a table detector returns, because the
# ERD backend takes exactly four things (entity, fields, field->field links, the
# file that declared it) and a class body describes itself with all four. What it
# does NOT have is a primary key: there is no such concept in a class, so `pk` is
# always False and the mark that replaces it is `required` — a field with no
# default, the one thing a caller MUST supply.
#
# They are kept in their own list and drawn on their own picture, never merged
# into a database's tables: a reader has to be able to tell a row that will exist
# in Postgres from an object that lives for the length of a request.

STRUCTURE_BASES = {"TypedDict": "typed_dict", "NamedTuple": "named_tuple", "BaseModel": "pydantic"}
DATACLASS_DECORATORS = {"dataclass"}
# The field is a container; what it holds is what the link points at. Cardinality
# is the only thing that changes: `items: list[Item]` is the same reference as
# `owner: Person`, drawn with a crow's foot.
SEQUENCE_CONTAINERS = {
    "list", "List", "set", "Set", "frozenset", "FrozenSet", "tuple", "Tuple",
    "Sequence", "MutableSequence", "Iterable", "Collection", "deque",
}
# A mapping is read value-first: `dict[str, Item]` is a collection of Items keyed
# by a string, and the string is not the thing being described.
MAPPING_CONTAINERS = {"dict", "Dict", "Mapping", "MutableMapping", "defaultdict", "OrderedDict"}
# Wrappers that qualify the field rather than name its type.
TRANSPARENT_WRAPPERS = {"Optional", "Union", "Annotated", "Final", "NotRequired", "Required"}
DEFAULT_FACTORIES = {"field", "Field"}


def _bare_name(node: ast.expr) -> str:
    """The last component of whatever this expression names.

    `pydantic.BaseModel`, `BaseModel`, `@dataclass(frozen=True)` and `list[int]`
    all answer with the one word a detector matches on.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _bare_name(node.func)
    if isinstance(node, ast.Subscript):
        return _bare_name(node.value)
    return ""


def _structure_kind(node: ast.ClassDef, known: dict[str, str]) -> str | None:
    """Which family this class belongs to, or None when it is an ordinary class.

    Every branch reads a declaration the author wrote: a decorator, a base class,
    or a base that this same file already declared a structure. Shape is never
    used as evidence — a class with three annotated attributes is just a class.
    """
    for decorator in node.decorator_list:
        if _bare_name(decorator) in DATACLASS_DECORATORS:
            return "dataclass"
    for base in node.bases:
        kind = STRUCTURE_BASES.get(_bare_name(base))
        if kind:
            return kind
        # Inheritance counts only from a structure declared HERE. `_bare_name`
        # would read `schemas.User` as `User` and hand a local class's kind to a
        # name that belongs to another module — an invented fact.
        if isinstance(base, ast.Name) and base.id in known:
            return known[base.id]
    return None


def _forward_ref(annotation: ast.expr) -> ast.expr | None:
    """A quoted annotation (`home: "ServerConfig"`) parsed back into an expression."""
    if not (isinstance(annotation, ast.Constant) and isinstance(annotation.value, str)):
        return None
    try:
        return ast.parse(annotation.value, mode="eval").body
    except SyntaxError:
        return None


def _referenced_structure(annotation: ast.expr | None, known: set[str], many: bool = False) -> tuple[str | None, bool]:
    """(the structure this annotation names, whether it names more than one of them).

    Only a bare name resolves, and only to a structure declared in the same file.
    A dotted name or an unknown one gives None: `lead: Person` where Person is
    imported says nothing about which Person, and drawing the local one would be
    a fabricated link.
    """
    if annotation is None:
        return None, many
    forward = _forward_ref(annotation)
    if forward is not None:
        return _referenced_structure(forward, known, many)
    if isinstance(annotation, ast.Name):
        return (annotation.id if annotation.id in known else None), many
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        for side in (annotation.left, annotation.right):
            name, inner_many = _referenced_structure(side, known, many)
            if name:
                return name, inner_many
        return None, many
    if isinstance(annotation, ast.Subscript):
        head = _bare_name(annotation.value)
        slice_node = annotation.slice
        parts = list(slice_node.elts) if isinstance(slice_node, ast.Tuple) else [slice_node]
        if head in MAPPING_CONTAINERS:
            parts = parts[::-1]
        if head in MAPPING_CONTAINERS or head in SEQUENCE_CONTAINERS:
            for part in parts:
                # `dict[str, list[Item]]` is two containers deep and still one
                # link — many stays many, and the edge's label carries the real
                # type so the reader sees the nesting the arrow cannot show.
                name, _ = _referenced_structure(part, known, True)
                if name:
                    return name, True
            return None, many
        if head in TRANSPARENT_WRAPPERS:
            # `Annotated[int, "Person"]` — everything after the first element is
            # metadata, and reading a metadata string as a forward reference is
            # how a documentation string turns into an arrow.
            for part in parts[:1] if head == "Annotated" else parts:
                name, inner_many = _referenced_structure(part, known, many)
                if name:
                    return name, inner_many
    return None, many


def _is_nullable(annotation: ast.expr | None) -> bool:
    """True when the declaration itself admits None."""
    if annotation is None:
        return False
    forward = _forward_ref(annotation)
    if forward is not None:
        return _is_nullable(forward)
    if isinstance(annotation, ast.Constant):
        return annotation.value is None
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _is_nullable(annotation.left) or _is_nullable(annotation.right)
    if isinstance(annotation, ast.Subscript):
        head = _bare_name(annotation.value)
        if head == "Optional":
            return True
        slice_node = annotation.slice
        parts = list(slice_node.elts) if isinstance(slice_node, ast.Tuple) else [slice_node]
        if head == "Annotated":
            # Everything past the first element is metadata, and metadata saying
            # None means nothing about the type.
            return bool(parts) and _is_nullable(parts[0])
        if head in {"Union", "NotRequired", "Required", "Final"}:
            return any(_is_nullable(part) for part in parts)
    return False


def _declares_a_default(value: ast.expr) -> bool:
    """Whether an assigned value actually supplies a default.

    `field()` and `Field(...)` both LOOK like defaults and are not: the first
    raises at construction time if nothing is passed, the second is pydantic's
    own spelling of "required". Reading them as defaults would mark every
    required field of a pydantic model optional.
    """
    if isinstance(value, ast.Call) and _bare_name(value) in DEFAULT_FACTORIES:
        if any(keyword.arg in {"default", "default_factory"} for keyword in value.keywords):
            return True
        if value.args:
            first = value.args[0]
            return not (isinstance(first, ast.Constant) and first.value is Ellipsis)
        return False
    return True


def _is_required(item: ast.AnnAssign, kind: str, total: bool) -> bool:
    """A field the caller has to supply — the mark that stands in for a primary key."""
    if kind == "typed_dict":
        head = _bare_name(item.annotation) if isinstance(item.annotation, ast.Subscript) else ""
        if head == "NotRequired":
            return False
        if head == "Required":
            return True
        return total
    if item.value is None:
        return True
    return not _declares_a_default(item.value)


def _total_of(node: ast.ClassDef) -> bool:
    """A TypedDict's `total=`, which decides whether its keys are required."""
    for keyword in node.keywords:
        if keyword.arg == "total" and isinstance(keyword.value, ast.Constant):
            return bool(keyword.value.value)
    return True


def _structure_columns(node: ast.ClassDef, kind: str, known: set[str]) -> list[dict]:
    total = _total_of(node) if kind == "typed_dict" else True
    columns: list[dict] = []
    for item in node.body:
        if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
            continue
        # A ClassVar belongs to the class, not to an instance of it: a dataclass
        # does not even pass it to __init__. Drawing it as a field would put a
        # shared constant in every box.
        if _bare_name(item.annotation) in {"ClassVar", "InitVar"}:
            continue
        reference, many = _referenced_structure(item.annotation, known)
        columns.append({
            "name": item.target.id,
            "type": ast.unparse(item.annotation),
            "pk": False,
            "fk": reference,
            "nullable": _is_nullable(item.annotation),
            "required": _is_required(item, kind, total),
            "many": many,
        })
    return columns


def detect_structures(tree: ast.Module, source: str) -> tuple[list[dict], list[dict]]:
    """Structures this file declares, in the shape a table detector returns.

    A class carrying `__tablename__` is left alone: it is a real table, already
    read by `detect_sqlalchemy`, and reporting it here as well would draw the same
    thing twice — once as a schema and once as an object — with no way for a
    reader to tell which of the two is the database.

    `unread` is always empty and the tuple keeps its shape anyway: unlike DDL,
    there is no "seen but unreadable" state here. A class either declares itself a
    structure or it does not, and an annotation naming something from another
    module is silently left unlinked rather than guessed at.
    """
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    kinds: dict[str, str] = {}
    # Repeated until nothing changes, so a chain (`Admin(User)`, `Root(Admin)`)
    # resolves whichever order the classes happen to be walked in.
    for _ in range(len(classes)):
        changed = False
        for node in classes:
            if node.name in kinds or _tablename_of(node) is not None:
                continue
            kind = _structure_kind(node, kinds)
            if kind:
                kinds[node.name] = kind
                changed = True
        if not changed:
            break

    known = set(kinds)
    structures = [
        {
            "name": node.name,
            "line": node.lineno,
            "end_line": node.end_lineno or node.lineno,
            "detector": kinds[node.name],
            "columns": _structure_columns(node, kinds[node.name], known),
        }
        for node in classes
        if node.name in kinds
    ]
    return structures, []
