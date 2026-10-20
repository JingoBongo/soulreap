"""TS/JS front end: functions (declared, arrow, wrapped), classes with methods, imports, hazards, routes.

Produces the same `FileFacts` the Python reader does, so everything downstream — symbols,
reach, flow, the checklist — works on a TS file without knowing which language it came from.
`facts_treesitter` stays the generic fallback for every other grammar.
"""

from __future__ import annotations

import re

from .facts_ast import CONST_VALUE_MAX, _find_cycles, group_shapes
from .facts_detect import SQL_RE, URL_RE, _detect_concurrency, _detect_io, _is_stdlib, _safe_value, _todos
from .facts_model import FileFacts
from .facts_treesitter import _ts_shape_of, _ts_shape_tokens, extract_treesitter
from .facts_ts_hazards import (
    ModuleScope,
    callees_of,
    hazards_of,
    string_value,
    text,
    walk,
)
from .facts_ts_routes import decorator_for_next, express_routes, next_convention, router_names
from .fingerprints import text_fingerprint

_FN_VALUES = frozenset({"arrow_function", "function_expression", "function", "generator_function"})
_WRAPPERS = frozenset({"memo", "forwardRef", "observer"})
_DECLS = frozenset({"function_declaration", "generator_function_declaration"})
_CLASSES = frozenset({"class_declaration", "abstract_class_declaration"})
_OTHER_TYPES = frozenset({"interface_declaration", "enum_declaration"})
_CONST_RE = re.compile(r"const\s+([A-Z][A-Z0-9_]*)\s*=\s*(.+)", re.S)


def _line(node) -> int:
    return node.start_point[0] + 1


def _doc_of(node) -> tuple[str | None, list[str]]:
    """First line and the remaining prose of a JSDoc comment directly above *node*."""
    prev = node.prev_sibling
    while prev is not None and prev.type == "decorator":
        prev = prev.prev_sibling
    if prev is None or prev.type != "comment" or not text(prev).startswith("/**"):
        return None, []
    if prev.end_point[0] < node.start_point[0] - 1:
        return None, []
    lines = []
    for raw in text(prev)[3:-2].splitlines():
        line = raw.strip().lstrip("*").strip()
        if line and not line.startswith("@"):
            lines.append(line)
    return (lines[0] if lines else None), lines[1:]


def _unwrap_value(value):
    """The function inside `memo(() => …)` / `forwardRef(function …)`, or *value* if it is one."""
    while value is not None and value.type == "call_expression":
        fn = value.child_by_field_name("function")
        if fn is None or text(fn).split(".")[-1] not in _WRAPPERS:
            return None
        args = [c for c in value.child_by_field_name("arguments").children if c.is_named]
        value = args[0] if args else None
    return value if value is not None and value.type in _FN_VALUES else None


def _signature(name: str, fn) -> str:
    params = fn.child_by_field_name("parameters") or fn.child_by_field_name("parameter")
    sig = name + (" ".join(text(params).split()) if params is not None and params.type == "formal_parameters"
                  else f"({text(params)})" if params is not None else "()")
    ret = fn.child_by_field_name("return_type")
    if ret is not None:
        sig += " " + " ".join(text(ret).split())
    return sig[:160]


def _record(name: str, fn, scope: ModuleScope, source_lines: list[str], doc_node=None,
            decorators: list[str] | None = None, arrow: bool = False) -> dict:
    """The function record, with the same keys `_describe_function` gives a Python one."""
    signature = _signature(name, fn)
    callees = callees_of(fn)
    hazards = hazards_of(fn, scope)
    doc, doc_more = _doc_of(doc_node or fn)
    start, end = _line(fn), fn.end_point[0] + 1
    if doc_node is not None:
        start = min(start, _line(doc_node))
    body = "\n".join(source_lines[start - 1:end])
    record = {
        "name": name, "signature": signature,
        "is_async": any(c.type == "async" for c in fn.children),
        "is_generator": fn.type in ("generator_function", "generator_function_declaration")
        or any(c.type == "*" for c in fn.children),
        "decorators": decorators or [], "start_line": start, "end_line": end,
        "doc": doc, "doc_more": doc_more, "shape": _ts_shape_of(fn), "callees": callees,
        "hazards": hazards, "literals": [],
        "sfp": text_fingerprint(signature, body), "bfp": "",
    }
    if arrow:
        record["arrow"] = True
    return record


def _import_names(clause) -> tuple[list[str], dict[str, str]]:
    names: list[str] = []
    aliases: dict[str, str] = {}
    for node in clause.children:
        if node.type == "identifier":
            names.append(text(node))
        elif node.type == "namespace_import":
            ids = [c for c in node.children if c.type == "identifier"]
            names += [text(i) for i in ids]
        elif node.type == "named_imports":
            for spec in node.children:
                if spec.type == "import_specifier":
                    name, alias = spec.child_by_field_name("name"), spec.child_by_field_name("alias")
                    names.append(text(name))
                    if alias is not None:
                        aliases[text(alias)] = text(name)
    return names, aliases


def _import_record(module: str, names: list[str], line: int, aliases: dict[str, str] | None = None,
                   lazy: bool = False) -> dict:
    rec = {"module": module, "names": names, "line": line, "stdlib": _is_stdlib(module, "typescript")}
    if aliases:
        rec["aliases"] = aliases
    if lazy:
        rec["lazy"] = True
    return rec


def _imports(program) -> list[dict]:
    out: list[dict] = []
    for stmt in program.children:
        source = stmt.child_by_field_name("source")
        if stmt.type == "import_statement" and source is not None:
            clause = next((c for c in stmt.children if c.type == "import_clause"), None)
            names, aliases = _import_names(clause) if clause is not None else ([], {})
            out.append(_import_record(string_value(source) or "", names, _line(stmt), aliases))
        elif stmt.type == "export_statement" and source is not None:
            clause = next((c for c in stmt.children if c.type == "export_clause"), None)
            names = [text(s.child_by_field_name("name")) for s in clause.children
                     if s.type == "export_specifier"] if clause is not None else []
            out.append(_import_record(string_value(source) or "", names, _line(stmt)))
        elif stmt.type in ("lexical_declaration", "variable_declaration"):
            for decl in stmt.children:
                value = decl.child_by_field_name("value") if decl.type == "variable_declarator" else None
                if value is None or value.type != "call_expression":
                    continue
                fn = value.child_by_field_name("function")
                args = [c for c in value.child_by_field_name("arguments").children if c.is_named]
                if fn is not None and text(fn) == "require" and args and string_value(args[0]):
                    target = decl.child_by_field_name("name")
                    names = ([text(target)] if target.type == "identifier" else
                             [text(c.child_by_field_name("key") or c) for c in target.children
                              if c.type in ("shorthand_property_identifier_pattern", "pair_pattern")])
                    out.append(_import_record(string_value(args[0]) or "", names, _line(stmt)))
    seen = {i["module"] for i in out}
    for sub in walk(program):  # `import("./X")`, as in React.lazy: a dependency that loads later
        if sub.type == "call_expression" and (fn := sub.child_by_field_name("function")) is not None \
                and fn.type == "import":
            args = [c for c in sub.child_by_field_name("arguments").children if c.is_named]
            module = string_value(args[0]) if args else None
            if module and module not in seen:
                seen.add(module)
                out.append(_import_record(module, [], _line(sub), lazy=True))
    return out


_NEST_VERBS = frozenset({"Get", "Post", "Put", "Patch", "Delete", "Head", "Options", "All"})


def _normal(decorator: str) -> str:
    """`Get('x')` -> `nest.get('x')`: flow matches entry decorators case-sensitively, Nest capitalises."""
    head = decorator.split("(")[0]
    return f"nest.{decorator[0].lower()}{decorator[1:]}" if head in _NEST_VERBS else decorator


def _decorators(node) -> list[str]:
    return [_normal(d) for d in _raw_decorators(node)]


def _raw_decorators(node) -> list[str]:
    found = [text(c)[1:].strip() for c in node.children if c.type == "decorator"]
    prev = node.prev_sibling
    while prev is not None and prev.type == "decorator":
        found.insert(0, text(prev)[1:].strip())
        prev = prev.prev_sibling
    return found


def _class(node, scope: ModuleScope, lines: list[str], outer, decorators: list[str]) -> dict:
    name = node.child_by_field_name("name")
    heritage = [" ".join(text(c).split()) for c in node.children if c.type == "class_heritage"]
    bases = [b.strip() for h in heritage for b in re.split(r"\b(?:extends|implements)\b|,", h) if b.strip()]
    methods = []
    body = node.child_by_field_name("body")
    for item in body.children if body is not None else []:
        key = item.child_by_field_name("name")
        if key is None:
            continue
        if item.type == "method_definition":
            methods.append(_record(text(key), item, scope, lines, decorators=_decorators(item)))
        elif item.type in ("public_field_definition", "field_definition"):
            value = _unwrap_value(item.child_by_field_name("value"))
            if value is not None:
                methods.append(_record(text(key), value, scope, lines, doc_node=item,
                                       decorators=_decorators(item), arrow=True))
    doc, _more = _doc_of(outer)
    return {"name": text(name), "bases": bases, "decorators": decorators, "start_line": _line(node),
            "end_line": node.end_point[0] + 1, "doc": doc, "fields": [], "methods": methods}


def extract_ts(source: str, lang: str, path: str) -> FileFacts:
    """Facts for a TypeScript / TSX / JavaScript file."""
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(lang)
    except Exception:
        return extract_treesitter(source, lang, path)
    facts = FileFacts(path=path, lang=lang, lines=source.count("\n") + (0 if source.endswith("\n") else 1))
    facts.todos = _todos(source)
    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception as exc:
        facts.parse_error = str(exc)[:200]
        return facts
    program, lines = tree.root_node, source.splitlines()
    scope = ModuleScope(program)
    nodes: dict[str, object] = {}
    next_kind = next_convention(path)
    routes = express_routes(program, router_names(program))
    by_handler = {r["handler_name"]: r["decorator"] for r in routes if r["handler_name"]}

    def add_function(name, fn, outer, exported_default=False, arrow=False, exported=False):
        decorators = [by_handler[name]] if name in by_handler else []
        if next_kind and exported:
            exp = "default" if exported_default else name
            deco = decorator_for_next(*next_kind, exp)
            if deco:
                decorators.append(deco)
        record = _record(name, fn, scope, lines, doc_node=outer, decorators=decorators, arrow=arrow)
        if exported:  # importance: an exported function is part of the module's surface
            record["exported"] = True
        facts.functions.append(record)
        nodes[name] = fn

    for stmt in program.children:
        exported = stmt.type == "export_statement"
        inner = stmt.child_by_field_name("declaration") if exported else stmt
        default = exported and any(c.type == "default" for c in stmt.children)
        if exported and inner is None and default:  # `export default Page;` / `export default () => …`
            value = stmt.child_by_field_name("value")
            if value is not None and value.type == "identifier":
                facts_default = text(value)
                by_handler.setdefault(facts_default, "")
                if next_kind and (deco := decorator_for_next(*next_kind, "default")):
                    by_handler[facts_default] = deco
            elif _unwrap_value(value) is not None:
                add_function("default", _unwrap_value(value), stmt, True, True, True)
            continue
        if inner is None:
            continue
        if inner.type in _DECLS:
            add_function(text(inner.child_by_field_name("name")), inner, stmt if exported else inner,
                         default, exported=exported)
        elif inner.type in ("lexical_declaration", "variable_declaration"):
            for decl in inner.children:
                if decl.type != "variable_declarator":
                    continue
                name_node = decl.child_by_field_name("name")
                value = _unwrap_value(decl.child_by_field_name("value"))
                if name_node is not None and name_node.type == "identifier" and value is not None:
                    add_function(text(name_node), value, stmt if exported else inner, default, True, exported)
                elif (match := _CONST_RE.match(text(inner))) is not None and value is None:
                    facts.constants.append({
                        "name": match.group(1), "line": _line(inner),
                        "value": _safe_value(match.group(1), match.group(2).rstrip().rstrip(";"))[:CONST_VALUE_MAX],
                    })
        elif inner.type in _CLASSES:
            outer = stmt if exported else inner
            facts.classes.append(_class(inner, scope, lines, outer,
                                        _decorators(outer) + (_decorators(inner) if exported else [])))
        elif inner.type in _OTHER_TYPES:
            name = inner.child_by_field_name("name")
            if name is not None:
                facts.classes.append({"name": text(name), "bases": [], "decorators": [], "methods": [],
                                      "start_line": _line(inner), "end_line": inner.end_point[0] + 1,
                                      "doc": _doc_of(stmt if exported else inner)[0], "fields": []})

    _finish_routes(facts, routes, scope, lines, by_handler, next_kind)
    facts.imports = sorted(_imports(program), key=lambda i: i["line"])
    facts.module_hazards = [h for h in hazards_of(program, scope, into_functions=False)
                            if h["kind"] in ("swallow", "external")]
    _file_level(facts, source, nodes)
    return facts


def _finish_routes(facts: FileFacts, routes, scope, lines, by_handler, next_kind) -> None:
    """Mark handlers that are named functions; give inline handlers a record of their own."""
    for fn in facts.functions:
        if not fn["decorators"] and by_handler.get(fn["name"]):
            fn["decorators"].append(by_handler[fn["name"]])
    for route in routes:
        node = route["handler_node"]
        if node is not None:
            facts.functions.append(_record(f"{route['method']} {route['path']}", node, scope, lines,
                                           decorators=[route["decorator"]]))


def _file_level(facts: FileFacts, source: str, nodes: dict[str, object]) -> None:
    """Call graph, recursion, shape groups, I/O and concurrency — the file-wide facts."""
    callables = facts.functions + [m for c in facts.classes for m in c["methods"]]
    defined = {fn["name"] for fn in callables}
    graph = {fn["name"]: set(fn["callees"]) & defined for fn in callables}
    facts.calls = {name: sorted(called) for name, called in sorted(graph.items())}
    facts.recursive = sorted(_find_cycles(graph))
    facts.shape_groups = group_shapes({n: _ts_shape_of(node) for n, node in nodes.items()},
                                      {n: _ts_shape_tokens(node) for n, node in nodes.items()})
    modules = [i["module"] for i in facts.imports]
    names = set(re.findall(r"\b(\w+)\s*\(", source))
    attrs = set(re.findall(r"\.(\w+)\s*\(", source))
    facts.io = _detect_io(modules, names, attrs, URL_RE.findall(source) + SQL_RE.findall(source))
    facts.concurrency = _detect_concurrency(modules, names, attrs, "async " in source.lower())
    facts.functions.sort(key=lambda f: f["start_line"])
    facts.classes.sort(key=lambda c: c["start_line"])
