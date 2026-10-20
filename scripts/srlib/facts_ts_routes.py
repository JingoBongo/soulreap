"""Route entry points in TS/JS, expressed as decorators so `flow.entry_points` needs no change.

A decorator string is `<receiver>.<verb>('<path>')`; flow keeps those whose last dotted
segment is in ENTRY_DECORATORS, so Express/Fastify/Hono verbs pass as they are and the
rest are spelled `route`. Next.js file conventions use the receiver `next`.
"""

from __future__ import annotations

import re

from .facts_ts_hazards import FUNCTION_TYPES, string_value, text

ROUTE_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "all"})
HTTP_EXPORTS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
# A receiver must look like a router, or `cache.get('/k', fallback)` becomes an endpoint.
_RECEIVER = re.compile(r"(app|router|route|server|fastify|api|hono)", re.IGNORECASE)
_NEXT_FILE = re.compile(r"(route|page)\.(?:ts|tsx|js|jsx|mjs)")
_PAGES_API = re.compile(r"(?:^|/)pages/(api/.+?)\.(?:ts|tsx|js|jsx|mjs)$")


def next_convention(path: str) -> tuple[str, str] | None:
    """(`route`|`page`|`api`, url) for a Next.js app-router file or pages/api file, else None."""
    match = _PAGES_API.search(path)
    if match:
        url = match.group(1)
        return "api", "/" + (url[:-6] if url.endswith("/index") else url)
    parts = path.split("/")
    match = _NEXT_FILE.fullmatch(parts[-1])
    if not match or "app" not in parts[:-1]:
        return None
    segments = [s for s in parts[parts.index("app") + 1:-1] if not s.startswith("(")]
    return match.group(1), "/" + "/".join(segments)


def decorator_for_next(kind: str, url: str, export: str) -> str | None:
    """Decorator text for an exported name in a Next.js file, or None when it is not an endpoint."""
    if kind == "route" and export in HTTP_EXPORTS:
        return f"next.route('{export} {url}')"
    if kind == "page" and export == "default":
        return f"next.route('page {url}')"
    if kind == "api" and export == "default":
        return f"next.route('api {url}')"
    return None


def express_routes(program, known_receivers: set[str]) -> list[dict]:
    """`app.get('/x', ..., handler)` statements at module level.

    Each hit is {decorator, handler_name | None, handler_node | None, method, path, line}; the
    handler is the LAST argument, earlier ones are middleware.
    """
    found = []
    for stmt in program.children:
        call = stmt.children[0] if stmt.type == "expression_statement" and stmt.children else None
        if call is None or call.type != "call_expression":
            continue
        fn = call.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            continue
        prop = fn.child_by_field_name("property")
        recv = fn.child_by_field_name("object")
        if prop is None or recv is None or text(prop) not in ROUTE_VERBS:
            continue
        receiver = text(recv)
        if receiver not in known_receivers and not _RECEIVER.search(receiver.split(".")[-1]):
            continue
        args = [c for c in call.child_by_field_name("arguments").children if c.is_named and c.type != "comment"]
        route = string_value(args[0]) if args else None
        if len(args) < 2 or route is None or not route.startswith(("/", "*")):
            continue
        verb = text(prop)
        handler = args[-1]
        decorator = f"{receiver}.{verb if verb != 'all' else 'route'}('{route}')"
        entry = {"decorator": decorator, "method": verb.upper(), "path": route,
                 "line": call.start_point[0] + 1, "handler_name": None, "handler_node": None}
        if handler.type == "identifier":
            entry["handler_name"] = text(handler)
        elif handler.type in FUNCTION_TYPES:
            entry["handler_node"] = handler
        else:
            continue
        found.append(entry)
    return found


def router_names(program) -> set[str]:
    """Module-level names bound to `express()`, `Router()`, `fastify()`, `new Hono()` and the like."""
    names: set[str] = set()
    for stmt in program.children:
        inner = stmt.child_by_field_name("declaration") if stmt.type == "export_statement" else stmt
        if inner is None or inner.type not in ("lexical_declaration", "variable_declaration"):
            continue
        for decl in inner.children:
            value = decl.child_by_field_name("value") if decl.type == "variable_declarator" else None
            if value is None or value.type not in ("call_expression", "new_expression"):
                continue
            fn = value.child_by_field_name("function") or value.child_by_field_name("constructor")
            tail = text(fn).split(".")[-1] if fn is not None else ""
            if tail in {"express", "Router", "fastify", "Fastify", "Hono", "Elysia", "Koa"}:
                names.add(text(decl.child_by_field_name("name")))
    return names
