"""Python-AST helpers for `extract_python`: signatures, shapes, call cycles, symbols.

Everything that reads one `ast` node and says something about it: class fields, docstring
splitting, rendered signatures, the structural shape
fingerprint and the clone groups built from it, per-callable descriptions, call-cycle
detection and the name/attribute/string collection that feeds the I/O detectors.
"""

from __future__ import annotations

import ast
import hashlib

from .facts_detect import _safe_value
from .fingerprints import body_fingerprint, literals_of
from .hazards import fingerprint, hazards_of


def _class_fields(node: ast.ClassDef) -> list[dict]:
    """The annotated attributes a class body declares — its contents, not its behaviour.

    Until this existed a dataclass read as a name and nothing else: `ServerConfig`
    with no host and no port, because only methods were collected and a dataclass has
    none. That is the emptiest an Entities row can be, and it is also what blocked
    every structure detector — dataclass, TypedDict, pydantic — since all of them are
    nothing BUT annotated attributes.

    Annotated only. A bare `x = 5` in a class body is as often a shared constant or a
    marker object as it is a field, and nothing in the source says which; an annotation
    is the author stating it. The default travels through `_safe_value`, the same guard
    module constants already pass: `api_token: str = "hunter2"` is a hardcoded
    credential wherever it is written.
    """
    fields: list[dict] = []
    for item in node.body:
        if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
            continue
        name = item.target.id
        default = ast.unparse(item.value) if item.value is not None else None
        fields.append({
            "name": name,
            "type": ast.unparse(item.annotation),
            "default": _safe_value(name, default)[:SNIPPET_MAX] if default is not None else None,
            "line": item.lineno,
        })
    return fields


def _first_line(text: str | None) -> str | None:
    if not text:
        return None
    stripped = text.strip().splitlines()
    return stripped[0].strip() if stripped else None


# Caps, not truncation policy. A fact that is clipped away is a fact the map does not
# have: `EDITABLE = {…}` arrived as `{'telegram_token': 'TELEGRAM_BOT_TOKEN',` and eight of
# its nine keys were invisible, which is exactly the question a maintainer asks first.
# These are sized for whole literals and whole docstrings; a file that blows past them is
# pathological, not ordinary.
CONST_VALUE_MAX = 2000
SNIPPET_MAX = 200
DOC_MORE_MAX = 2000


def _doc_more(text: str | None) -> list[str]:
    """The rest of a module docstring — the part that lists keys, defaults and contracts.

    `doc` stays the first line so every existing consumer reads what it always read;
    this is where the multi-line body goes, uncut until DOC_MORE_MAX.
    """
    if not text:
        return []
    lines = text.strip().splitlines()[1:]
    out: list[str] = []
    used = 0
    for line in lines:
        line = line.rstrip()
        if not line.strip():
            continue
        used += len(line) + 1
        if used > DOC_MORE_MAX:
            out.append("... (docstring clipped)")
            break
        out.append(line.strip())
    return out


def _fmt_arg(arg: ast.arg, default: ast.expr | None = None) -> str:
    out = arg.arg
    if arg.annotation is not None:
        out += ": " + ast.unparse(arg.annotation)
    if default is not None:
        out += " = " + ast.unparse(default)
    return out


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Human-readable signature, annotations included."""
    a = node.args
    positional = list(a.posonlyargs) + list(a.args)
    padding: list[ast.expr | None] = [None] * (len(positional) - len(a.defaults))
    parts = [_fmt_arg(arg, dflt) for arg, dflt in zip(positional, padding + list(a.defaults), strict=True)]
    if a.posonlyargs:
        parts.insert(len(a.posonlyargs), "/")
    if a.vararg is not None:
        parts.append("*" + _fmt_arg(a.vararg))
    elif a.kwonlyargs:
        parts.append("*")
    for arg, dflt in zip(a.kwonlyargs, a.kw_defaults, strict=True):
        parts.append(_fmt_arg(arg, dflt))
    if a.kwarg is not None:
        parts.append("**" + _fmt_arg(a.kwarg))
    sig = f"{node.name}({', '.join(parts)})"
    if node.returns is not None:
        sig += " -> " + ast.unparse(node.returns)
    return sig


def _own_nodes(node: ast.AST):
    """Descendants of *node*, not descending into nested definitions."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(current))


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            target = sub.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


# Node types that carry a name or a value rather than a structure. Two callables
# that differ only in these are the same shape: cover the identifiers with your
# hand and the code reads letter for letter the same.
_SHAPE_SKIP = (ast.Name, ast.Constant, ast.arg, ast.Attribute, ast.Load, ast.Store, ast.Del)


def shape_of(node: ast.AST) -> str:
    """A fingerprint of a callable's structure, blind to names and literals.

    Counted as a multiset, not a sequence, so moving one assignment a line up
    does not change the answer — an exact-order hash called two of afas_client's
    five identical lookups "different" over exactly that.
    """
    counts: dict[str, int] = {}
    for child in ast.walk(node):
        if isinstance(child, _SHAPE_SKIP):
            continue
        counts[type(child).__name__] = counts.get(type(child).__name__, 0) + 1
    return hashlib.sha1(repr(sorted(counts.items())).encode()).hexdigest()[:8]


def _shape_tokens(node: ast.AST) -> set[str]:
    """The names and literals a same-shape sibling could differ in.

    The docstring is skipped: it differs between siblings by definition and is
    already carried on the entity line, so listing it as a varying part buries
    the two constants that actually distinguish them.
    """
    body = list(getattr(node, "body", []))
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        body = body[1:]
    tokens: set[str] = set()
    for statement in body:
        for child in ast.walk(statement):
            if isinstance(child, ast.Attribute):
                tokens.add(ast.unparse(child))
            elif isinstance(child, ast.Constant) and isinstance(child.value, str):
                tokens.add(repr(child.value))
    return tokens


def shape_groups(nodes: dict[str, ast.AST]) -> list[dict]:
    """Group callables by shape and report, per member, what it alone carries.

    The differing constants are parsed rather than left to the card writer: a
    fold table is only worth having if its rows are facts. Anything every member
    shares is dropped — it does not vary, so it belongs in the shared block.
    """
    shapes = {name: shape_of(node) for name, node in nodes.items()}
    tokens = {name: _shape_tokens(node) for name, node in nodes.items()}
    return group_shapes(shapes, tokens)


def group_shapes(shapes: dict[str, str], tokens: dict[str, set[str]]) -> list[dict]:
    """The language-neutral half: bucket by hash, report what each member alone carries."""
    by_shape: dict[str, list[str]] = {}
    for name, shape in shapes.items():
        by_shape.setdefault(shape, []).append(name)

    groups = []
    for shape, members in sorted(by_shape.items()):
        if len(members) < 2:
            continue
        shared = set.intersection(*(tokens[name] for name in members))
        groups.append({
            "shape": shape,
            "members": sorted(members),
            "varies": {name: sorted(tokens[name] - shared) for name in sorted(members)},
        })
    return groups


def _describe_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    names: frozenset[str] = frozenset(),
    io: dict[str, str] | None = None,
    constants: dict[str, str] | None = None,
) -> dict:
    signature = _signature(node)
    callees = sorted(_called_names(node))
    hazards = hazards_of(node, names, io, constants)
    literals = literals_of(node)
    return {
        "name": node.name,
        "signature": signature,
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "is_generator": any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _own_nodes(node)),
        "decorators": [ast.unparse(d) for d in node.decorator_list],
        "start_line": node.lineno,
        "end_line": node.end_lineno or node.lineno,
        "doc": _first_line(ast.get_docstring(node)),
        # The contract often lives past the first line ("keys outside EDITABLE are dropped");
        # a map reader working from skeleton must see it.
        "doc_more": _doc_more(ast.get_docstring(node)),
        "shape": shape_of(node),
        "callees": callees,
        "hazards": hazards,
        "literals": literals,
        "sfp": fingerprint(signature, callees, hazards, literals),
        "bfp": body_fingerprint(node, constants),
    }


def _find_cycles(graph: dict[str, set[str]]) -> set[str]:
    """Names that sit on a call cycle, self-calls included."""
    on_cycle: set[str] = set()
    colour: dict[str, int] = {}
    stack: list[str] = []

    def visit(name: str) -> None:
        colour[name] = 1
        stack.append(name)
        for nxt in graph.get(name, ()):  # deterministic enough for reporting
            if nxt == name:
                on_cycle.add(name)
            elif colour.get(nxt, 0) == 0:
                visit(nxt)
            elif colour.get(nxt) == 1:
                on_cycle.update(stack[stack.index(nxt):])
        stack.pop()
        colour[name] = 2

    for node in sorted(graph):
        if colour.get(node, 0) == 0:
            visit(node)
    return on_cycle


def _collect_symbols(tree: ast.AST) -> tuple[set[str], set[str], list[str]]:
    call_names: set[str] = set()
    attrs: set[str] = set()
    strings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                call_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                attrs.add(node.func.attr)
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)
        elif isinstance(node, ast.Name):
            call_names.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)
    return call_names, attrs, strings
