"""Non-Python extraction: tree-sitter with a regex fallback.

Node-type tables, the structural shape fingerprint over tree-sitter nodes, the regex
extractor used when no grammar is available, and `extract_treesitter`, which turns a
parsed file into `FileFacts`.
"""

from __future__ import annotations

import hashlib
import re

from .facts_ast import CONST_VALUE_MAX, group_shapes
from .facts_detect import SQL_RE, URL_RE, _detect_concurrency, _detect_io, _is_stdlib, _safe_value, _todos
from .facts_model import FileFacts

# ----------------------------------------------------------- tree-sitter ---

_IMPORT_NODES = {"import_statement", "import_declaration", "import_from_statement", "use_declaration", "import_spec"}
_FUNC_NODES = {
    "function_declaration", "function_definition", "method_definition",
    "method_declaration", "function_item",
}
_CLASS_NODES = {
    "class_declaration", "class_definition", "class_specifier", "struct_item",
    "interface_declaration", "enum_declaration",
}
_STRING_NODES = {"string", "string_literal", "interpreted_string_literal", "raw_string_literal"}

_IMPORT_RE = re.compile(r"""(?:from\s+['"]([^'"]+)['"]|import\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"])""")
_FUNC_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE)
_CLASS_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)", re.MULTILINE)


# Node types that name or spell a value rather than shape the code. Dropping
# them is what makes two clones hash alike; the Python side drops ast.Name and
# ast.Constant for the same reason.
_TS_ATTRIBUTE_NODES = {"member_expression", "selector_expression", "field_expression"}

_TS_SHAPE_SKIP = {
    "identifier", "property_identifier", "shorthand_property_identifier",
    "shorthand_property_identifier_pattern", "type_identifier", "statement_identifier",
    "field_identifier", "string", "string_fragment", "template_string", "number", "comment",
}


def _ts_descendants(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def _ts_shape_of(node) -> str:
    """The tree-sitter twin of shape_of — a multiset of named node types.

    Unnamed nodes are punctuation, so counting them would only measure syntax
    noise. Multiset, not sequence, for the same reason as the Python side: a
    statement moved a line up is still the same shape.
    """
    counts: dict[str, int] = {}
    for child in _ts_descendants(node):
        if not child.is_named or child.type in _TS_SHAPE_SKIP:
            continue
        counts[child.type] = counts.get(child.type, 0) + 1
    return hashlib.sha1(repr(sorted(counts.items())).encode()).hexdigest()[:8]


def _ts_shape_tokens(node) -> set[str]:
    """The names and literals a same-shape sibling could differ in."""
    body = node.child_by_field_name("body") or node
    tokens: set[str] = set()
    for child in _ts_descendants(body):
        if child.type in _TS_ATTRIBUTE_NODES or child.type in _STRING_NODES:
            tokens.add(_node_text(child))
    return tokens


def _node_text(node) -> str:
    return node.text.decode("utf-8", errors="replace")


def _first_string(node) -> str | None:
    stack = list(node.children)
    while stack:
        current = stack.pop(0)
        if current.type in _STRING_NODES:
            return _node_text(current).strip("\"'`")
        stack.extend(current.children)
    return None


def _extract_regex(source: str, path: str, lang: str | None) -> FileFacts:
    """Fallback used when no grammar is available. Never raises."""
    facts = FileFacts(path=path, lang=lang, lines=source.count("\n") + (0 if source.endswith("\n") else 1))
    facts.todos = _todos(source)
    seen: set[str] = set()
    for match in _IMPORT_RE.finditer(source):
        module = next(g for g in match.groups() if g)
        if module not in seen:
            seen.add(module)
            facts.imports.append({
                "module": module, "names": [], "line": source[:match.start()].count("\n") + 1,
                "stdlib": _is_stdlib(module, lang),
            })
    for match in _FUNC_RE.finditer(source):
        line = source[:match.start()].count("\n") + 1
        facts.functions.append({
            "name": match.group(1), "signature": match.group(0).strip(), "is_async": "async" in match.group(0),
            "is_generator": False, "decorators": [], "start_line": line, "end_line": line, "doc": None,
        })
    for match in _CLASS_RE.finditer(source):
        line = source[:match.start()].count("\n") + 1
        facts.classes.append({
            "name": match.group(1), "bases": [], "decorators": [], "start_line": line,
            "end_line": line, "doc": None, "methods": [],
        })
    return facts


def _self_recursive(source: str, functions: list[dict]) -> list[str]:
    """Names that appear called inside their own body.

    A textual check, not a call graph: enough to flag the recursion a reader
    needs to see, and it never claims recursion the source does not show.
    """
    lines = source.splitlines()
    found: list[str] = []
    for fn in functions:
        body = "\n".join(lines[fn["start_line"]: fn["end_line"]])
        if re.search(rf"\b{re.escape(fn['name'])}\s*\(", body):
            found.append(fn["name"])
    return sorted(set(found))


def extract_treesitter(source: str, lang: str, path: str) -> FileFacts:
    """Facts for a non-Python file, via tree-sitter with a regex fallback."""
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(lang)
    except Exception:
        return _extract_regex(source, path, lang)

    facts = FileFacts(path=path, lang=lang, lines=source.count("\n") + (0 if source.endswith("\n") else 1))
    facts.todos = _todos(source)
    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception as exc:
        facts.parse_error = str(exc)[:200]
        return facts

    ts_nodes: dict[str, object] = {}
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        kind = node.type
        if kind in _IMPORT_NODES or (kind == "export_statement" and node.child_by_field_name("source")):
            # `export { X } from "./y"` is a dependency exactly like an import;
            # barrel files are made of nothing else.
            source_node = node.child_by_field_name("source")
            module = _node_text(source_node).strip("\"'`") if source_node else _first_string(node)
            if module:
                facts.imports.append({
                    "module": module, "names": [], "line": node.start_point[0] + 1,
                    "stdlib": _is_stdlib(module, lang),
                })
        elif kind in _FUNC_NODES or kind in _CLASS_NODES:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                entity_name = _node_text(name_node)
                record: dict = {
                    "name": entity_name,
                    "shape": _ts_shape_of(node) if kind in _FUNC_NODES else None,
                    "signature": _node_text(node).split("{")[0].strip()[:160],
                    "is_async": _node_text(node).lstrip().startswith("async"),
                    "is_generator": False,
                    "decorators": [],
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "doc": None,
                }
                if kind in _FUNC_NODES:
                    ts_nodes[entity_name] = node
                    facts.functions.append(record)
                else:
                    record.update({"bases": [], "methods": []})
                    facts.classes.append(record)
        elif kind == "lexical_declaration":
            text = _node_text(node)
            match = re.match(r"const\s+([A-Z][A-Z0-9_]*)\s*=\s*(.+)", text)
            if match:
                facts.constants.append({
                    "name": match.group(1),
                    "value": _safe_value(match.group(1), match.group(2).rstrip(";"))[:CONST_VALUE_MAX],
                    "line": node.start_point[0] + 1,
                })
        stack.extend(node.children)

    facts.shape_groups = group_shapes(
        {name: _ts_shape_of(node) for name, node in ts_nodes.items()},
        {name: _ts_shape_tokens(node) for name, node in ts_nodes.items()},
    )

    modules = [i["module"] for i in facts.imports]
    lowered = source.lower()
    names = set(re.findall(r"\b(\w+)\s*\(", source))
    attrs = set(re.findall(r"\.(\w+)\s*\(", source))
    facts.io = _detect_io(modules, names, attrs, URL_RE.findall(source) + SQL_RE.findall(source))
    facts.concurrency = _detect_concurrency(modules, names, attrs, "async " in lowered)
    facts.functions.sort(key=lambda f: f["start_line"])
    facts.classes.sort(key=lambda c: c["start_line"])
    facts.imports.sort(key=lambda i: i["line"])
    facts.recursive = _self_recursive(source, facts.functions + [
        m for c in facts.classes for m in c.get("methods", [])
    ])
    return facts
