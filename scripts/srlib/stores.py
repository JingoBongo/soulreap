"""Stores whose shape is read off their USES, not off a declaration.

The odd source among the four. `CREATE TABLE`, a SQLAlchemy model and a SQLite file
all state a schema; this one collects traces — a named container, and the string keys
the code happens to use against it. `fuse_core` keeps its whole world in four such
stores and declares none of them.

Every rule below was measured over 2845 Python files in ~/PycharmProjects, because the
obvious readings are all wrong:

- **the constructor is not evidence.** "A module-level name assigned a call with a
  literal first argument" finds `re.compile` 270 times, then `Path`, `os.environ.get`,
  `logging.getLogger` and `TypeVar`;
- **the verbs alone are not evidence either.** Adding "and `get`/`put`/`delete` are
  called on it" leaves 57 candidates whose largest group, 33 of them, is `APIRouter` —
  in FastAPI those verbs are routing;
- **three cuts make it exact.** A call taking part in a decorator is routing, not
  storage (this one is a rule, not a heuristic, and it removes `APIRouter` and
  `FastAPI` outright); a short list of in-process standard-library types is excluded,
  the same way `_OWN_TOOLING` excludes tools every repository runs; and a store must be
  both read and written — what is only read is a response or a fixture, what is only
  written is a log.

Left: 4 candidates over 2845 files, all four real, none false.

What this module deliberately does not try to be: a detector of one product. A cache
and a message broker fit the same "named thing plus verbs" mechanism and have nothing
to put on this picture — redis keys are namespace prefixes with computed suffixes, a
broker has streams and consumers. They are separate outputs of the same mechanism, and
mixing them into this one would be the mistake this module exists to avoid making with
DDL.
"""

from __future__ import annotations

import ast

from .stores_fields import (  # noqa: F401  (the verbs are part of this module's vocabulary)
    DROP,
    LIST,
    READ,
    WRITE,
    YIELDS_RECORD,
    _binding_key,
    _observe_fields,
    _walk_scope,
)

# In-process objects of the standard library. A thing every program has is not a store
# — the same argument that keeps `rmdir` off the context diagram.
IN_PROCESS = {
    "ContextVar", "contextvars.ContextVar",
    "defaultdict", "collections.defaultdict", "OrderedDict", "collections.OrderedDict",
    "Counter", "collections.Counter", "deque", "collections.deque",
    "dict", "list", "set", "tuple", "TypeVar",
    "re.compile", "Path", "pathlib.Path", "logging.getLogger", "logging.Logger",
    "ArgumentParser", "argparse.ArgumentParser",
    # In-process queues answer `put` and `get` and are not stores: nothing is kept
    # under a key and nothing has fields. They belong to the broker question — who
    # publishes and who consumes — which is a topology, not a schema. Caught on the
    # corpus: `_in = queue.Queue()` in osintov's crawler was the one false positive
    # the finished detector produced over 2845 files.
    "Queue", "queue.Queue", "queue.SimpleQueue", "queue.LifoQueue",
    "queue.PriorityQueue", "asyncio.Queue", "multiprocessing.Queue", "JoinableQueue",
}


def _decorator_names(tree: ast.Module) -> set[str]:
    """Names that take part in any decorator anywhere in the file."""
    found: set[str] = set()
    for node in ast.walk(tree):
        for decorator in getattr(node, "decorator_list", []):
            for part in ast.walk(decorator):
                if isinstance(part, ast.Name):
                    found.add(part.id)
    return found


def _bindings(tree: ast.Module) -> dict[str, dict]:
    """Every `x = Call(...)` worth considering, at module level or in an `__init__`.

    A redis client is never at module level — it is `self.client = redis.from_url(...)`
    — and leaving that out was the whole recall gap of the first measurement.
    """
    found: dict[str, dict] = {}
    named = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
             and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
             for t in n.targets if isinstance(t, ast.Name)}

    def record(target: ast.AST, call: ast.Call) -> None:
        key = _binding_key(target)
        if key is None:
            return
        ctor = ast.unparse(call.func)
        if ctor in IN_PROCESS:
            return
        literal = None
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
            literal = call.args[0].value
        elif call.args and isinstance(call.args[0], ast.Name):  # Vault(VAULT_NAME)
            literal = named.get(call.args[0].id)
        found.setdefault(key, {
            "name": literal or key.rsplit(".", 1)[-1],
            "binding": key,
            "ctor": ctor,
            "line": getattr(target, "lineno", 0),
        })

    def calls_in(value: ast.AST | None):
        """The constructions in an assigned value.

        `self.client = client or redis.from_url(...)` — an injected client with a
        default — is how one of the two real caches measured is written, and reading
        only a bare Call missed it entirely.
        """
        if isinstance(value, ast.Call):
            return [value]
        if isinstance(value, ast.BoolOp):
            return [v for v in value.values if isinstance(v, ast.Call)]
        return []

    def assignments(body) -> None:
        for node in body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for call in calls_in(getattr(node, "value", None)):
                for target in targets:
                    record(target, call)

    assignments(tree.body)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for member in node.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name == "__init__":
                assignments([n for n in ast.walk(member) if isinstance(n, (ast.Assign, ast.AnnAssign))])
    return found


def _verbs(tree: ast.Module, bindings: dict[str, dict]) -> dict[str, set[str]]:
    used: dict[str, set[str]] = {key: set() for key in bindings}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        key = _binding_key(node.func.value)
        if key in used:
            used[key].add(node.func.attr)
    return used


# The verbs whose FIRST argument is the key. `setex(key, ttl, value)` and
# `get(key)` agree on that, and it is the only position worth reading.
KEYED = READ | WRITE | DROP


def _key_text(node: ast.AST) -> tuple[str, str] | None:
    """`(text, kind)` for a key written down in the source, else None.

    Two kinds, and telling them apart matters. `get("settings")` names ONE key.
    `f"perplexity:{hash}"` names a family of them, and its literal head is the
    namespace — which for a cache is the entire shape there is: a redis client has no
    fields, only prefixes with a computed tail.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
        return node.value, "key"
    if isinstance(node, ast.JoinedStr) and node.values:
        head = node.values[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str) and head.value:
            return head.value, "prefix"
    return None


def _observe_keys(tree: ast.Module, bindings: dict[str, dict]) -> tuple[dict, dict, dict]:
    """(literal keys per store, count of keys not written out, the computed key of each store).

    Same alias trick as the fields, the other way round: `key = f"mfa:otp:{id}"`
    followed by `client.get(key)` in the same function. Both real caches measured —
    one in an auth service, one in a scoring tool — are written exactly that way.
    """
    keys: dict[str, dict[str, dict]] = {name: {} for name in bindings}
    unreadable: dict[str, int] = {name: 0 for name in bindings}
    computed: dict[str, dict] = {name: {"names": {}, "type": None, "writers": set(), "removers": set(),
                                        "values": set()}
                                 for name in bindings}

    scopes = [tree] + [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for scope in scopes:
        literal_locals: dict[str, tuple[str, str]] = {}
        for node in _walk_scope(scope):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                text = _key_text(node.value)
                if isinstance(target, ast.Name) and text:
                    literal_locals[target.id] = text
        for node in _walk_scope(scope):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in KEYED or not node.args:
                continue
            key = _binding_key(node.func.value)
            if key not in bindings:
                continue
            first = node.args[0]
            text = _key_text(first)
            if text is None and isinstance(first, ast.Name):
                text = literal_locals.get(first.id)
            if text is None:
                unreadable[key] += 1
                if isinstance(first, ast.Name):
                    _note_computed_key(computed[key], scope, first.id, node.func.attr)
                    if node.func.attr in WRITE and len(node.args) > 1 and isinstance(node.args[1], ast.Name):
                        computed[key]["values"].add(node.args[1].id)  # `put(name, path)`: kept whole
                continue
            entry = keys[key].setdefault(
                text[0], {"name": text[0], "kind": text[1], "uses": 0}
            )
            entry["uses"] += 1
    return keys, unreadable, {name: _key_shape(info) for name, info in computed.items()}


def _note_computed_key(info: dict, scope: ast.AST, variable: str, verb: str) -> None:
    """A key held in a variable: its name, its declared type, and who writes or removes it."""
    info["names"][variable] = info["names"].get(variable, 0) + 1
    where = getattr(scope, "name", None)
    if where and verb in WRITE:
        info["writers"].add(where)
    if where and verb in DROP | {"pop"}:
        info["removers"].add(where)
    args = getattr(scope, "args", None)
    for arg in (args.posonlyargs + args.args + args.kwonlyargs) if args else []:
        if arg.arg == variable and arg.annotation is not None:
            info["type"] = info["type"] or ast.unparse(arg.annotation)


def _key_shape(info: dict) -> dict | None:
    """The computed key of one store, or None when every key is written out in the source."""
    if not info["names"]:
        return None
    return {"names": sorted(info["names"], key=lambda n: (-info["names"][n], n)), "type": info["type"],
            "writers": sorted(info["writers"]), "removers": sorted(info["removers"]),
            "values": sorted(info["values"])}


def detect_stores(tree: ast.Module) -> list[dict]:
    """Named stores this file uses, with whatever shape its uses reveal."""
    bindings = _bindings(tree)
    if not bindings:
        return []
    decorated = _decorator_names(tree)
    verbs = _verbs(tree, bindings)

    keep = {}
    for key, binding in bindings.items():
        if key.split(".")[0] in decorated:
            continue
        used = verbs.get(key, set())
        if not (used & READ) or not (used & (WRITE | DROP)):
            continue
        keep[key] = binding
    if not keep:
        return []

    fields, unreadable = _observe_fields(tree, keep)
    keys, key_unreadable, computed = _observe_keys(tree, keep)
    found = []
    for key, binding in keep.items():
        columns = sorted(fields[key].values(), key=lambda c: c["name"])
        found.append({
            **binding,
            "detector": "observed",
            "columns": columns,
            "keys": sorted(keys[key].values(), key=lambda k: k["name"]),
            "unreadable": unreadable[key],
            # Kept apart from `unreadable` because it is not the same admission. A
            # field nobody could read is a hole in the picture. A key computed at run
            # time is how a record store is SUPPOSED to work — `remote_repositories`
            # is keyed by a repository name — and reporting it as a gap would cry
            # wolf on every store in existence.
            "dynamic_keys": key_unreadable[key],
            # What a record store is keyed BY: drawn without it, a store is a value with
            # nothing to look it up by.
            "key": computed[key],
            "verbs": sorted(verbs[key] & (READ | WRITE | LIST | DROP)),
        })
    return sorted(found, key=lambda s: (s["line"], s["name"]))


def store_library(blob: dict, store: dict) -> str | None:
    """The outside library a store's constructor comes from (`vaults` for `Vault(...)` or
    `vaults.Vault(...)`), or None when it is a class of this repository or defined in the file —
    only an outside client may be said to keep state beyond the process."""
    head = str(store.get("ctor", "")).split(".")[0]
    for item in blob.get("imports") or []:
        if item.get("target"):  # resolved to a file of this repo
            continue
        module = str(item.get("module", ""))
        if module.startswith("."):
            continue
        if head in (module.split(".")[0], module.split(".")[-1]) or head in (item.get("names") or []):
            return module.split(".")[0]
    return None
