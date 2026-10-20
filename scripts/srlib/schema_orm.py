"""ORM models: tables read from SQLAlchemy declarative classes, 1.x and 2.0 style.

The column-factory helpers (`Column`, `mapped_column`, foreign keys, explicit names,
types) and `detect_sqlalchemy` live here. `__tablename__` is the signal, so a class
that is not an ORM model is never reported as a table.
"""

from __future__ import annotations

import ast

# --------------------------------------------------------------- sqlalchemy ---

COLUMN_FACTORIES = {"Column", "mapped_column"}


def _keyword_bool(call: ast.Call, name: str) -> bool | None:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return bool(keyword.value.value)
    return None


def _callee_name(node: ast.expr) -> str:
    """The name a call was made under, whether bare or through a module."""
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def _is_mapped_annotation(annotation: ast.expr | None) -> bool:
    """True for a `Mapped[...]` annotation — the 2.0 way to declare a column."""
    if not isinstance(annotation, ast.Subscript):
        return False
    head = annotation.value
    name = head.id if isinstance(head, ast.Name) else getattr(head, "attr", "")
    return name == "Mapped"


def _foreign_key_of(call: ast.Call) -> str | None:
    """The `ForeignKey("table.column")` string among a column's positional arguments."""
    for arg in call.args:
        if (
            _callee_name(arg) == "ForeignKey"
            and arg.args
            and isinstance(arg.args[0], ast.Constant)
            and isinstance(arg.args[0].value, str)
        ):
            return arg.args[0].value
    return None


def _explicit_column_name(call: ast.Call | None) -> str | None:
    """The column's name in the database, when the model states it explicitly.

    `owner_id = Column("target_id", Integer)` puts the attribute `owner_id` into
    the column `target_id`, and a schema diagram wants the latter. Reading that
    first positional argument as the TYPE instead means putting a quoted name
    where a type is expected.
    """
    if call is None or not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _column_type_of(call: ast.Call | None, annotation: ast.expr | None) -> str:
    """The column's type: the first positional argument that actually is one.

    Two kinds of argument are skipped because neither is a type: a string literal
    (that is an explicit column name) and `ForeignKey(...)`, including one written
    through a module — otherwise the type field ends up holding `sa.ForeignKey('t.id')`.
    """
    for arg in call.args if call else []:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            continue
        if _callee_name(arg) == "ForeignKey":
            continue
        return ast.unparse(arg)
    if isinstance(annotation, ast.Subscript):
        return ast.unparse(annotation.slice)
    return ""


def _column_call(node: ast.AST) -> tuple[str, ast.Call | None, ast.expr | None] | None:
    """(attribute name, column call if any, annotation) for a class-body statement."""
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        target, value, annotation = node.target.id, node.value, node.annotation
    elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        target, value, annotation = node.targets[0].id, node.value, None
    else:
        return None
    if isinstance(value, ast.Call):
        return (target, value, annotation) if _callee_name(value) in COLUMN_FACTORIES else None
    # `name: Mapped[str]` with no call is a column too: 2.0 infers its type from the
    # annotation. Dropping it would leave the table looking complete while a real
    # column is missing. An assignment to something else (`relationship(...)`) does
    # not reach here: its value is not None.
    if value is None and _is_mapped_annotation(annotation):
        return target, None, annotation
    return None


def _tablename_of(node: ast.ClassDef) -> str | None:
    for item in node.body:
        if (
            isinstance(item, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in item.targets)
            and isinstance(item.value, ast.Constant)
            and isinstance(item.value.value, str)
        ):
            return item.value.value
    return None


def _declarative_base(node: ast.ClassDef) -> str | None:
    """The base that names the metadata: `class Label(_IdMixin, _CreatedMixin, Base)` → `Base`.
    Mixins come first in the MRO by convention, so the last non-mixin base wins; grouping by the
    first one drew one ERD per mixin on eager-connection-platform."""
    bases = [ast.unparse(b) for b in node.bases]
    plain = [b for b in bases if not b.lstrip("_").lower().endswith("mixin")]
    return (plain or bases or [None])[-1]


def detect_sqlalchemy(tree: ast.Module, source: str) -> tuple[list[dict], list[dict]]:
    """Tables from SQLAlchemy declarative models, both 1.x and 2.0 style.

    `__tablename__` is the signal, not the shape of the class: it is a declaration
    that no non-ORM model carries, so this never reports a fabricated table.
    """
    tables: list[dict] = []
    unread: list[dict] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        name = _tablename_of(node)
        if name is None:
            continue
        columns = []
        for item in node.body:
            found = _column_call(item)
            if found is None:
                continue
            attribute_name, call, annotation = found
            columns.append({
                "name": _explicit_column_name(call) or attribute_name,
                "type": _column_type_of(call, annotation),
                "pk": bool(_keyword_bool(call, "primary_key")) if call else False,
                "fk": _foreign_key_of(call) if call else None,
                "nullable": _keyword_bool(call, "nullable") if call else None,
            })
        tables.append({
            "name": name,
            "line": node.lineno,
            "end_line": node.end_lineno or node.lineno,
            "detector": "sqlalchemy",
            "base": _declarative_base(node),
            "columns": columns,
        })

    return tables, unread
