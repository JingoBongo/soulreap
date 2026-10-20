"""Field observation for `stores`: the verbs of a key-value store, the alias walk, and the
fields read off a record (`record["x"]`, `record.get("x")`) or written as a dict literal.

Split from stores.py at the 400-line rule; `stores` decides what is a store, this module
reads what a store holds.
"""

from __future__ import annotations

import ast

# The verbs of a key-value store. A family each, because what matters is that several
# DIFFERENT things are done to the container, not which.
READ = {"get", "pop", "read", "fetch", "load", "hget", "hgetall", "mget"}
WRITE = {"put", "set", "setex", "hset", "write", "save", "store", "add", "insert", "update"}
LIST = {"keys", "list_keys", "scan", "scan_iter", "iter_keys", "items", "all", "list"}
DROP = {"delete", "remove", "expire", "unlink", "drop", "purge"}

# The verbs that hand back a record, and therefore start an alias worth following.
YIELDS_RECORD = READ


def _binding_key(node: ast.AST) -> str | None:
    """`repos` or `self.client` — the two ways a store is held onto."""
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return f"self.{node.attr}"
    return None


def _walk_scope(scope: ast.AST):
    """Every node of one scope, stopping at the next function or class.

    `ast.walk` from the module would visit each function body again, and a field read
    once then came out counted twice.
    """
    boundary = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, boundary):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _written_subscripts(scope: ast.AST) -> set[int]:
    """Ids of the subscript nodes that are being assigned TO in this scope."""
    out: set[int] = set()
    for node in _walk_scope(scope):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Subscript):
                out.add(id(target))
    return out


def _column(fields: dict[str, dict], name: str) -> dict:
    return fields.setdefault(name, {"name": name, "type": None, "pk": False, "fk": None, "reads": 0, "writes": 0})


LITERAL_TYPES = {"bool", "int", "float", "str", "list", "dict", "set", "tuple"}


def _literal_type(node: ast.AST) -> str | None:
    """The type a written value plainly has: `bool(on)` → bool, `0` → int, `[]` → list; else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in LITERAL_TYPES:
        return node.func.id
    if isinstance(node, ast.Constant) and node.value is not None:
        return type(node.value).__name__
    return {ast.List: "list", ast.Dict: "dict", ast.Set: "set", ast.Tuple: "tuple",
            ast.JoinedStr: "str"}.get(type(node))


def _literal_writes(scope: ast.AST, bindings: dict[str, dict], fields: dict[str, dict]) -> None:
    """`store.put(name, {"enabled": on})`: the keys of a dict literal written as the value are fields."""
    for node in _walk_scope(scope):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in WRITE):
            continue
        key = _binding_key(node.func.value)
        if key not in bindings:
            continue
        for value in node.args[1:]:
            if isinstance(value, ast.Dict):
                for item, written in zip(value.keys, value.values, strict=True):
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        column = _column(fields[key], item.value)
                        column["writes"] += 1
                        column["type"] = column["type"] or _literal_type(written)


def _observe_fields(tree: ast.Module, bindings: dict[str, dict]) -> tuple[dict, dict]:
    """(fields per store, unreadable-key count per store).

    One function at a time: `record = repos.get(name)` binds `record` to that store for
    the rest of the function, and every `record["..."]` after it is a field. Following
    an alias any further — through a parameter, or out of a helper's return — is
    interprocedural analysis, and the cost of that is not worth the handful of extra
    keys it would find.
    """
    fields: dict[str, dict[str, dict]] = {key: {} for key in bindings}
    unreadable: dict[str, int] = {key: 0 for key in bindings}

    scopes = [tree] + [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for scope in scopes:
        alias: dict[str, str] = {}
        for node in _walk_scope(scope):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
                continue
            call = node.value
            if not (isinstance(call.func, ast.Attribute) and call.func.attr in YIELDS_RECORD):
                continue
            key = _binding_key(call.func.value)
            if key not in bindings:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    alias[target.id] = key
        _literal_writes(scope, bindings, fields)
        if not alias:
            continue
        written = _written_subscripts(scope)
        for node in _walk_scope(scope):  # `record.get("enabled", False)` reads a field too
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in alias and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                _column(fields[alias[node.func.value.id]], node.args[0].value)["reads"] += 1
        for node in _walk_scope(scope):
            if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)):
                continue
            key = alias.get(node.value.id)
            if key is None:
                continue
            index = node.slice
            if isinstance(index, ast.Constant) and isinstance(index.value, str):
                column = _column(fields[key], index.value)
                if id(node) in written:
                    column["writes"] += 1
                else:
                    column["reads"] += 1
            elif not isinstance(index, ast.Constant):
                # A key the parser cannot read is the honest limit of this source, and
                # it is counted rather than passed over: the picture has to be able to
                # say how much of the shape it did not see.
                unreadable[key] += 1
    return fields, unreadable
