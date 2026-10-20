"""What a module binds, read once per file: the names hazards are judged against.

`io_aliases` maps a local name to the outside-world or concurrency module it stands for
(an import, or a client/lock/pool assigned from one); `module_names` and `module_constants`
are the module's own top-level bindings. Split from hazards.py, which decides per callable.
"""

from __future__ import annotations

import ast

IO_MODULES = frozenset({
    "requests", "httpx", "aiohttp", "urllib", "urllib3", "http", "socket", "smtplib",
    "subprocess", "sqlite3", "psycopg", "psycopg2", "asyncpg", "pymysql", "redis",
    "boto3", "paramiko", "ftplib",
})
# Calls that hand back a live client: what they return is an I/O object, and calls on it reach outside.
CLIENT_FACTORIES = frozenset({
    "connect", "Session", "Client", "AsyncClient", "create_pool", "create_engine", "cursor", "acquire",
    "Redis", "StrictRedis", "client", "resource", "SSHClient", "SMTP", "SMTP_SSL", "socket",
    "create_connection", "Pool", "ConnectionPool", "ThreadPoolExecutor", "ProcessPoolExecutor",
})
# Constructors matched by suffix, so `FileLock`, `asyncio.Lock` and `BoundedSemaphore` all count.
LOCK_CTOR_SUFFIXES = ("Lock", "Semaphore")
LOCK_CTORS = frozenset({"Condition"})
# What `io_aliases` records for a name assigned a lock (`guard = threading.Lock()`).
LOCK_MARK = "<lock>"
# Prefix for names assigned from a spawn module (`pool = multiprocessing.Pool()`): kept apart from I/O
# clients, so the same name bound to `asyncpg.create_pool()` elsewhere in the file stays external.
SPAWN_KEY = "<spawn>"
# Modules whose aliases make a call a spawn (they are tracked in the same alias map as I/O modules,
# but never count as external).
SPAWN_MODULES = frozenset({"threading", "multiprocessing", "concurrent", "asyncio"})
# Submodules of an I/O package that never leave the process: parsing, quoting, status codes.
PURE_SUBMODULES = ("urllib.parse", "http.cookies", "http.HTTPStatus")


def io_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name → top-level module, for imports of modules that reach outside the process."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in IO_MODULES or top in SPAWN_MODULES:
                    found[(alias.asname or alias.name).split(".")[0]] = top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            top = node.module.split(".")[0]
            if (top in IO_MODULES or top in SPAWN_MODULES) and node.module not in PURE_SUBMODULES:
                for alias in node.names:
                    if node.module == "http" and alias.name not in ("client", "server"):
                        continue  # `from http import HTTPStatus` is an enum, not a connection
                    found[alias.asname or alias.name] = top
    # `self.db = sqlite3.connect(...)`, `pool = await asyncpg.create_pool()`: the client is I/O too.
    for _ in range(2):  # twice, so `cur = self.db.cursor()` follows `self.db = sqlite3.connect()`
        for node in ast.walk(tree):
            pairs = []
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                pairs = [(target, node.value) for target in targets]
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                pairs = [(item.optional_vars, item.context_expr) for item in node.items if item.optional_vars]
            for target, value in pairs:
                call = value.value if isinstance(value, ast.Await) else value
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                base = _text(func.value) if isinstance(func, ast.Attribute) else ""
                module = _module_of(found, _root_name(func) or "") or _module_of(found, base)
                label = _self_attr(target) or (target.id if isinstance(target, ast.Name) else None)
                if not label:
                    continue
                if _is_lock_ctor(name):
                    found.setdefault(label, LOCK_MARK)
                elif module and (name in CLIENT_FACTORIES or (name == "get_context" and module in SPAWN_MODULES)):
                    found.setdefault(f"{SPAWN_KEY}{label}" if module in SPAWN_MODULES else label, module)
    return found


def _module_of(io: dict[str, str], name: str) -> str | None:
    """The module *name* is bound to: an import or I/O client first, then a spawn-module object."""
    return io.get(name) or io.get(f"{SPAWN_KEY}{name}")


def module_names(tree: ast.Module) -> frozenset[str]:
    """Names bound at module level — a write to one of them is shared state."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
    return frozenset(names)


def _text(expr: ast.AST) -> str:
    try:
        return ast.unparse(expr)
    except Exception:  # pragma: no cover - unparse handles every node type we pass
        return "?"


def _root_name(expr: ast.AST) -> str | None:
    while isinstance(expr, (ast.Attribute, ast.Call)):
        expr = expr.value if isinstance(expr, ast.Attribute) else expr.func
    return expr.id if isinstance(expr, ast.Name) else None


def _self_attr(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id == "self":
        return f"self.{expr.attr}"
    return None


def _is_lock_ctor(name: str) -> bool:
    return name.endswith(LOCK_CTOR_SUFFIXES) or name in LOCK_CTORS


def module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = value` bindings, as source text — what a body fingerprint folds in."""
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.value is not None:
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = _text(node.value)
    return found
