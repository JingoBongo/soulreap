"""Callees and hazards of one TS/JS callable, read from its tree-sitter node.

Same kinds as the Python reader (`swallow`, `external`, `write`), same shape
(`{kind, what, line}`), so the checklist and the cards never ask which language a symbol
is in. Deliberately conservative: a false positive on a checklist costs more than a miss,
so only patterns that cannot be anything else are reported.
"""

from __future__ import annotations

FUNCTION_TYPES = frozenset({
    "arrow_function", "function_expression", "function", "generator_function",
    "function_declaration", "generator_function_declaration", "method_definition",
})
HTTP_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "request"})
HTTP_ROOTS = frozenset({"axios", "ky"})
STORAGE_ROOTS = frozenset({"localStorage", "sessionStorage"})
STORAGE_WRITES = frozenset({"setItem", "removeItem", "clear"})
FS_WRITES = ("writeFile", "appendFile", "write", "createWriteStream")  # prefix match, fs-rooted only
FS_MODULES = frozenset({"fs", "node:fs", "fs/promises", "node:fs/promises", "fs-extra"})
LOG_ROOTS = frozenset({"console", "logger", "log"})
_ASSIGN = frozenset({"assignment_expression", "augmented_assignment_expression"})


def text(node) -> str:
    return node.text.decode("utf-8", errors="replace")


def walk(node, into_functions: bool = True):
    """Descendants of *node*; with `into_functions=False` a nested function is yielded but not entered."""
    stack = list(reversed(node.children))
    while stack:
        cur = stack.pop()
        yield cur
        if into_functions or cur.type not in FUNCTION_TYPES:
            stack.extend(reversed(cur.children))


def callee_name(call) -> str | None:
    """The bare called name: `foo()` -> foo, `a.b.c()` -> c."""
    fn = call.child_by_field_name("function") or call.child_by_field_name("constructor")
    if fn is None:
        return None
    if fn.type == "identifier":
        return text(fn)
    if fn.type == "member_expression":
        prop = fn.child_by_field_name("property")
        return text(prop) if prop is not None else None
    return None


def callees_of(node) -> list[str]:
    """Every name called in *node*, nested callbacks included; capitalised JSX tags count as calls."""
    names: set[str] = set()
    for sub in walk(node):
        if sub.type in ("call_expression", "new_expression"):
            name = callee_name(sub)
            if name:
                names.add(name)
        elif sub.type in ("jsx_opening_element", "jsx_self_closing_element"):
            tag = sub.child_by_field_name("name")
            if tag is not None:
                last = text(tag).split(".")[-1]
                if last[:1].isupper():
                    names.add(last)
    return sorted(names)


def string_value(node) -> str | None:
    """A string or template literal as text, `{expr}` standing for each interpolation."""
    if node is None:
        return None
    if node.type == "string":
        return text(node)[1:-1]
    if node.type == "template_string":
        parts = []
        for child in node.children:
            if child.type == "string_fragment":
                parts.append(text(child))
            elif child.type == "template_substitution":
                parts.append("{" + text(child)[2:-1].strip() + "}")
        return "".join(parts)
    return None


def _first_arg(call):
    args = call.child_by_field_name("arguments")
    named = [c for c in args.children if c.is_named and c.type != "comment"] if args is not None else []
    return named[0] if named else None


def _dotted(node) -> str:
    return "".join(text(node).split())


def _root(node) -> str:
    while node is not None and node.type == "member_expression":
        node = node.child_by_field_name("object")
    return text(node) if node is not None and node.type == "identifier" else ""


def _is_log_only(block) -> bool:
    """A catch body that is empty, comments, or only `console.x(...)`-style logging."""
    for stmt in block.children:
        if stmt.type in ("{", "}", "comment", "empty_statement"):
            continue
        call = stmt.children[0] if stmt.type == "expression_statement" and stmt.children else None
        if call is None or call.type != "call_expression":
            return False
        fn = call.child_by_field_name("function")
        if fn is None or fn.type != "member_expression" or _root(fn) not in LOG_ROOTS:
            return False
    return True


def _declared_inside(node) -> set[str]:
    """Names a function binds itself (parameters and local declarations): they shadow module state."""
    found: set[str] = set()
    for sub in walk(node):
        if sub.type == "identifier" and sub.parent is not None and sub.parent.type in (
                "variable_declarator", "required_parameter", "optional_parameter", "formal_parameters",
                "catch_clause", "array_pattern", "object_pattern", "shorthand_property_identifier_pattern",
                "pair_pattern", "rest_pattern", "assignment_pattern"):
            if sub.parent.type != "variable_declarator" or sub.parent.child_by_field_name("name") == sub:
                found.add(text(sub))
        elif sub.type == "shorthand_property_identifier_pattern":
            found.add(text(sub))
    return found


class ModuleScope:
    """What a file binds at module level, which decides what a hazard in a function can mean."""

    def __init__(self, program) -> None:
        self.lets: set[str] = set()
        self.http_clients: set[str] = set(HTTP_ROOTS)
        self.fs_names: set[str] = set()
        self.fs_funcs: set[str] = set()
        for stmt in program.children:
            inner = stmt.child_by_field_name("declaration") if stmt.type == "export_statement" else stmt
            if inner is None:
                continue
            if inner.type == "import_statement":
                self._import(inner)
            elif inner.type in ("lexical_declaration", "variable_declaration"):
                mutable = text(inner).lstrip().startswith(("let", "var"))
                for decl in inner.children:
                    if decl.type != "variable_declarator":
                        continue
                    name = decl.child_by_field_name("name")
                    value = decl.child_by_field_name("value")
                    if name is None or name.type != "identifier":
                        continue
                    if mutable:
                        self.lets.add(text(name))
                    if value is not None and value.type == "call_expression":
                        fn = value.child_by_field_name("function")
                        if fn is not None and fn.type == "member_expression" and _root(fn) in HTTP_ROOTS \
                                and callee_name(value) == "create":
                            self.http_clients.add(text(name))

    def _import(self, node) -> None:
        source = node.child_by_field_name("source")
        if source is None or (string_value(source) or "") not in FS_MODULES:
            return
        for sub in walk(node):
            if sub.type == "identifier" and sub.parent is not None:
                if sub.parent.type in ("import_clause", "namespace_import"):
                    self.fs_names.add(text(sub))
                elif sub.parent.type == "import_specifier":
                    alias = sub.parent.child_by_field_name("alias")
                    local = text(alias) if alias is not None else text(sub)
                    self.fs_funcs.add(local)


def hazards_of(node, scope: ModuleScope, into_functions: bool = True) -> list[dict]:
    """Hazards in *node*, first line per (kind, what), in line order."""
    found: dict[tuple[str, str], int] = {}

    def add(kind: str, what: str, line: int) -> None:
        key = (kind, what)
        found[key] = min(found.get(key, line), line)

    shadowed = _declared_inside(node) if scope.lets else set()
    for sub in walk(node, into_functions):
        line = sub.start_point[0] + 1
        if sub.type == "catch_clause":
            body = sub.child_by_field_name("body")
            if body is not None and _is_log_only(body):
                add("swallow", "catch", line)
        elif sub.type == "call_expression":
            _call(sub, scope, add, line)
        elif sub.type in _ASSIGN or sub.type == "update_expression":
            target = sub.child_by_field_name("left") or sub.child_by_field_name("argument")
            if target is not None and target.type == "identifier":
                name = text(target)
                if name in scope.lets and name not in shadowed:
                    add("write", name, line)
    ordered = sorted(found.items(), key=lambda item: (item[1], item[0]))
    return [{"kind": kind, "what": what, "line": line} for (kind, what), line in ordered]


def _call(call, scope: ModuleScope, add, line: int) -> None:
    fn = call.child_by_field_name("function")
    if fn is None:
        return
    dotted = _dotted(fn)
    if fn.type == "identifier":
        name = text(fn)
        if name in ("fetch",) or (name in scope.http_clients and name in HTTP_ROOTS):
            add("external", string_value(_first_arg(call)) or dotted, line)
        elif name in scope.fs_funcs and name.startswith(FS_WRITES):
            add("write", dotted, line)
        return
    if fn.type != "member_expression":
        return
    root, prop = _root(fn), callee_name(call) or ""
    if dotted in ("window.fetch", "globalThis.fetch") or (root in scope.http_clients and prop in HTTP_VERBS):
        add("external", string_value(_first_arg(call)) or dotted, line)
    elif (root in STORAGE_ROOTS and prop in STORAGE_WRITES) or (
            (root in scope.fs_names or dotted.startswith("fs.promises.")) and prop.startswith(FS_WRITES)):
        add("write", dotted, line)
    elif prop == "catch":
        handler = _first_arg(call)
        if handler is not None and handler.type in ("arrow_function", "function_expression", "function"):
            body = handler.child_by_field_name("body")
            if body is not None and body.type == "statement_block" and _is_log_only(body):
                add("swallow", "catch()", line)
