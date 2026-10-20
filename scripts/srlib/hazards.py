"""Per-symbol hazards, and the structural fingerprint a prose claim is pinned to.

A hazard is something a reader of the code has to be told and a parser can prove
is there: an exception swallowed, a lock taken, state written, the outside world
called, a thread spawned, a sleep inside a loop. v10's `render` requires every
checklist hazard in the read set to be cited by the map, and staleness compares
`fingerprint` — signature, callees and hazards, never line numbers or comments —
so moving code or editing a comment never marks a claim stale.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re

from .hazard_names import (  # noqa: F401  (io_aliases, module_names, module_constants: re-exported)
    CLIENT_FACTORIES,
    IO_MODULES,
    LOCK_MARK,
    PURE_SUBMODULES,
    SPAWN_KEY,
    SPAWN_MODULES,
    _is_lock_ctor,
    _root_name,
    _self_attr,
    _text,
    io_aliases,
    module_constants,
    module_names,
)

SPAWN_CALLS = frozenset({
    "Thread", "Process", "Timer", "create_task", "ensure_future", "run_in_executor",
    "start_new_thread", "submit",
})
MUTATORS = frozenset({
    "append", "extend", "insert", "pop", "popitem", "remove", "clear", "update",
    "setdefault", "add", "discard",
})
SQL_WRITE = re.compile(
    r'^\s*(INSERT|UPDATE|DELETE|REPLACE|MERGE|TRUNCATE|DROP|ALTER)\b\s*(?:INTO|FROM)?\s*(?:TABLE\s+)?([\w."]+)?',
    re.I,
)
FILE_WRITE_METHODS = frozenset({
    "write", "writelines", "write_text", "write_bytes", "unlink", "rmdir", "rename", "touch",
})
FILE_WRITE_FUNCS = frozenset({
    "os.remove", "os.unlink", "os.rename", "os.replace", "os.rmdir", "os.removedirs", "shutil.rmtree",
    "shutil.move", "shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copytree",
})
# A lock is named like one (`_lock`, `state_rlock`, `mutex`, `sem`) or built by a lock constructor —
# matched on the last name segment, so `self.clock` and `blocking_io()` are not locks.
LOCK_NAME = re.compile(r"(^|_)(r?locks?|mutex(es)?|sems?|semaphores?|cond|conditions?)$", re.I)
# A call that hands back a lock by its own name: `r.lock("k")`, `self._lock_for(key)`.
LOCK_CALL = re.compile(r"(^|_)(r?lock|mutex)(_|$)", re.I)
# `.submit` on something named like an executor or pool is a spawn even when nothing says where it came from.
EXECUTOR_NAME = re.compile(r"(^|_)(executor|pool)$", re.I)


def _own_nodes(node: ast.AST):
    """Descendants of *node*, not descending into nested definitions."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(current))


def _hazard_nodes(node: ast.AST):
    """Descendants of *node* including nested functions and lambdas (they are not symbols of their
    own, so their hazards belong to the enclosing one), but not nested classes."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, ast.ClassDef):
            continue
        stack.extend(ast.iter_child_nodes(current))


def _write_target(target: ast.AST, names: frozenset[str], declared: set[str], in_init: bool) -> str | None:
    attr = _self_attr(target)
    if attr:
        return None if in_init else attr
    if isinstance(target, ast.Subscript):
        base = target.value
        own = _self_attr(base)
        if own:
            return None if in_init else own
        return base.id if isinstance(base, ast.Name) and base.id in names else None
    if isinstance(target, ast.Name) and target.id in declared:
        return target.id
    return None


def _open_mode(call: ast.Call) -> str | None:
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        return str(call.args[1].value)
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return None


def _literal_target(call: ast.Call) -> str | None:
    """The first argument when it is a string literal or an f-string — the URL or path called.

    `{name}` stands in for each interpolated part, so `f"{base}/launch_process"` reads as
    `{base}/launch_process`: the endpoint is named even when the host comes from config.
    """
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        text = arg.value
    elif isinstance(arg, ast.JoinedStr):
        parts = []
        for value in arg.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                parts.append("{" + _text(value.value) + "}")
        text = "".join(parts)
    else:
        return None
    text = " ".join(text.split())
    # Only something that names a place: a URL or a path. A bare word is an argument, not an endpoint.
    return text[:80] if "/" in text else None


def _sql_text(arg: ast.AST, constants: dict[str, str]) -> str | None:
    """A SQL string as written: a literal, an f-string, or a module constant holding one."""
    if isinstance(arg, (ast.Constant, ast.JoinedStr)):
        return _render(arg)
    if isinstance(arg, ast.Name) and arg.id in constants:
        try:
            value = ast.literal_eval(constants[arg.id])
        except (ValueError, SyntaxError):
            return None
        return value if isinstance(value, str) else None
    return None


def _is_lock(expr: ast.AST, io: dict[str, str] | None = None) -> bool:
    """A lock by construction (`threading.Lock()`), by assignment from one (`guard = Lock()`),
    by name (`self._lock`, `state_rlock`, `self._locks[k]`) or by a call named for one (`r.lock("k")`)."""
    if isinstance(expr, ast.Call):
        func = expr.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return _is_lock_ctor(name or "") or bool(LOCK_CALL.search(name or ""))
    if isinstance(expr, ast.Subscript):
        return _is_lock(expr.value, io)
    if (io or {}).get(_text(expr)) == LOCK_MARK:
        return True
    last = expr.attr if isinstance(expr, ast.Attribute) else getattr(expr, "id", "")
    return bool(LOCK_NAME.search(last or ""))


def _call_hazards(call: ast.Call, names: frozenset[str], io: dict[str, str], looped: bool, add,
                  in_init: bool = False, constants: dict[str, str] | None = None) -> None:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    if name is None:
        return
    line = call.lineno
    root = _root_name(func)
    receiver = _text(func.value) if isinstance(func, ast.Attribute) else ""
    concurrency = any(io.get(f"{prefix}{name_}") in SPAWN_MODULES
                      for prefix in ("", SPAWN_KEY) for name_ in (root or "", receiver))
    if name == "acquire" and isinstance(func, ast.Attribute) and _is_lock(func.value, io):
        add("lock", receiver, line)
    on_executor = name == "submit" and bool(EXECUTOR_NAME.search(receiver.rsplit(".", 1)[-1]))
    if name in {"create_task", "ensure_future", "run_in_executor", "start_new_thread"} or on_executor or (
            name in SPAWN_CALLS and concurrency):
        add("spawn", _text(func), line)
    if name == "sleep" and looped:
        add("sleep_retry", _text(func), line)
    if name == "open" and isinstance(func, ast.Name):
        mode = _open_mode(call)
        if mode and any(flag in mode for flag in "wax+"):
            add("write", f"open:{mode}", line)
    if isinstance(func, ast.Attribute) and name in MUTATORS:
        base = func.value
        own = _self_attr(base)
        # Filling a container in __init__ is construction, like assigning it there.
        target = (None if in_init else own) if own else (
            base.id if isinstance(base, ast.Name) and base.id in names else None)
        if target:
            add("write", target, line)
    dotted = _text(func)
    if dotted in FILE_WRITE_FUNCS or (isinstance(func, ast.Attribute) and name in FILE_WRITE_METHODS and root != "sys"):
        add("write", dotted, line)
    on_client = io.get(receiver) in IO_MODULES
    if (io.get(root or "") in IO_MODULES or on_client) and not dotted.startswith(
            tuple(f"{m}." for m in PURE_SUBMODULES)):
        target = _literal_target(call)
        add("external", f"{dotted} {target}" if target else dotted, line)
    elif root == "os" and name == "system":
        add("external", "os.system", line)
    for arg in call.args:
        sql = _sql_text(arg, constants or {})
        if sql:
            match = SQL_WRITE.match(sql)
            if match:
                add("write", f"sql:{match.group(1).upper()} {(match.group(2) or '?').strip(chr(34))}", line)


def hazards_of(node: ast.AST, names: frozenset[str] = frozenset(), io: dict[str, str] | None = None,
               constants: dict[str, str] | None = None) -> list[dict]:
    """Every hazard in *node*'s own body (nested definitions excluded), first line per (kind, what)."""
    io = io or {}
    found: dict[tuple[str, str], int] = {}

    def add(kind: str, what: str, line: int) -> None:
        key = (kind, what)
        if key not in found or line < found[key]:
            found[key] = line

    own = list(_hazard_nodes(node))
    declared = {n for sub in own if isinstance(sub, ast.Global) for n in sub.names}
    looped = {id(n) for sub in own if isinstance(sub, (ast.For, ast.AsyncFor, ast.While)) for n in _hazard_nodes(sub)}
    in_init = getattr(node, "name", "") == "__init__"
    # Arguments the body mutates in place (`last_revive[k] = now`, `history.append(x)`): state the
    # caller owns. Its own kind, so `writes` finds it while the checklist stays as it was.
    params = _params(node)
    # `raise requests.HTTPError(...)` constructs an exception; nothing leaves the process. Calls in its
    # arguments (`raise E(requests.get(u).text)`) still run, so only the constructor itself is skipped.
    building_exception = {id(sub.exc) for sub in own if isinstance(sub, ast.Raise) and isinstance(sub.exc, ast.Call)}
    optional_imports = {id(handler) for sub in own if isinstance(sub, ast.Try) and _imports_only(sub.body)
                        for handler in sub.handlers}
    for sub in own:
        if isinstance(sub, ast.ExceptHandler):
            if id(sub) in optional_imports:
                continue
            if not any(isinstance(n, ast.Raise) for n in _own_nodes(sub)):
                add("swallow", _text(sub.type) if sub.type else "bare except", sub.lineno)
        elif isinstance(sub, ast.Raise) and sub.exc is not None:
            exc = sub.exc.func if isinstance(sub.exc, ast.Call) else sub.exc
            add("raise", _text(exc), sub.lineno)
        elif isinstance(sub, (ast.With, ast.AsyncWith)):
            for item in sub.items:
                if _is_lock(item.context_expr, io):
                    add("lock", _text(item.context_expr), sub.lineno)
        elif isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            for target in targets:
                for element in target.elts if isinstance(target, ast.Tuple) else [target]:
                    written = _write_target(element, names, declared, in_init)
                    if written:
                        add("write", written, sub.lineno)
                    elif isinstance(element, ast.Subscript) and _bare(element.value) in params:
                        add("arg_write", _bare(element.value), sub.lineno)
        elif isinstance(sub, ast.Call) and id(sub) not in building_exception:
            _call_hazards(sub, names, io, id(sub) in looped, add, in_init, constants)
            func = sub.func
            if isinstance(func, ast.Attribute) and func.attr in MUTATORS and _bare(func.value) in params:
                add("arg_write", _bare(func.value), sub.lineno)
    ordered = sorted(found.items(), key=lambda item: (item[1], item[0]))
    return [{"kind": kind, "what": what, "line": line} for (kind, what), line in ordered]


def _bare(expr: ast.AST) -> str | None:
    return expr.id if isinstance(expr, ast.Name) else None


def _params(node: ast.AST) -> frozenset[str]:
    """Argument names of a function, without `self`/`cls`; empty for anything else."""
    args = getattr(node, "args", None)
    if not isinstance(args, ast.arguments):
        return frozenset()
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
    return frozenset(a.arg for a in every if a is not None and a.arg not in ("self", "cls"))


def _imports_only(body: list[ast.stmt]) -> bool:
    """`try: import yaml; HAVE_YAML = True` — imports, plus constant flags and `pass`, nothing else."""
    simple = all(isinstance(stmt, (ast.Import, ast.ImportFrom, ast.Pass))
                 or (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant)) for stmt in body)
    return simple and any(isinstance(stmt, (ast.Import, ast.ImportFrom)) for stmt in body)


def _render(node: ast.AST) -> str | None:
    """A string, f-string or argv list as one line, `{expr}` standing in for each interpolation."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            str(v.value) if isinstance(v, ast.Constant) else "{" + _text(v.value) + "}"
            for v in node.values if isinstance(v, (ast.Constant, ast.FormattedValue))
        )
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        # argv lists, and the one-f-string list a shell wrapper takes: [f"git -C {p} ..."]
        if not isinstance(node.elts[0], (ast.Constant, ast.JoinedStr)) or _render(node.elts[0]) is None:
            return None
        return " ".join(_render(e) or "{" + _text(e) + "}" for e in node.elts)
    return None


def fingerprint(signature: str, callees, hazards, literals=()) -> str:
    """What a reader would claim about a symbol: signature, callees, hazards, literals. No lines."""
    payload = [signature, sorted(set(callees)), sorted({(h["kind"], h["what"]) for h in hazards})]
    if literals:  # absent → the v7 payload, so a symbol without literals keeps its fingerprint
        payload.append(sorted({(lit["kind"], lit["text"]) for lit in literals}))
    return hashlib.sha1(json.dumps(payload).encode()).hexdigest()[:10]
