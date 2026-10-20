"""TS/JS front end: functions, arrow consts, methods, callees, imports, hazards, routes."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib import flow
from srlib.build import build_facts
from srlib.facts import extract_source


def _facts(src: str, path: str = "a.ts"):
    return extract_source(src, path)


def _fn(facts, name):
    return next(f for f in facts.functions if f["name"] == name)


def test_arrow_consts_and_function_expressions_are_functions():
    src = ("export const Foo = () => <div/>;\n"
           "const useX = async (a: number): Promise<void> => { await go(a); };\n"
           "const f = function () { return 1; };\n"
           "export default function Page() { return null; }\n")
    facts = _facts(src, "a.tsx")
    assert {f["name"] for f in facts.functions} == {"Foo", "useX", "f", "Page"}
    use_x = _fn(facts, "useX")
    assert use_x["is_async"] and use_x["callees"] == ["go"]
    assert use_x["signature"].startswith("useX(a: number)")
    assert use_x["start_line"] == 2 and _fn(facts, "Page")["start_line"] == 4


def test_wrapped_components_are_functions_named_by_the_const():
    facts = _facts("export const Card = React.memo(() => { return null; });\n"
                   "const Inp = forwardRef((p, r) => null);\n", "a.tsx")
    assert {f["name"] for f in facts.functions} == {"Card", "Inp"}


def test_callees_are_bare_names_including_nested_callbacks_and_new():
    src = ("function run(items) {\n  items.map((i) => helper(i));\n  obj.bar();\n"
           "  new Thing();\n  a.b.c();\n}\n")
    assert set(_fn(_facts(src), "run")["callees"]) == {"map", "helper", "bar", "Thing", "c"}


def test_jsx_components_are_callees():
    facts = _facts("export function A() { return <div><Card/><ui.Btn/><span/></div>; }\n", "a.tsx")
    assert set(_fn(facts, "A")["callees"]) == {"Card", "Btn"}


def test_class_methods_attach_to_the_class():
    src = ("class K extends B implements I {\n  async m(x) { this.q(); }\n"
           "  static s() {}\n  handle = () => { go(); };\n}\n")
    facts = _facts(src)
    assert facts.functions == []
    cls = facts.classes[0]
    assert cls["name"] == "K" and "B" in " ".join(cls["bases"])
    methods = {m["name"]: m for m in cls["methods"]}
    assert set(methods) == {"m", "s", "handle"}
    assert methods["m"]["is_async"] and methods["m"]["callees"] == ["q"]
    assert methods["handle"]["callees"] == ["go"]


def test_imports_carry_names():
    src = ('import D, { a, b as c } from "./m";\nimport * as N from "n";\nimport "./side";\n'
           'import type { T } from "./t";\nexport { z } from "./z";\nexport * from "./all";\n'
           'const { p, q } = require("./cjs");\nconst lazy = () => import("./Lazy");\n')
    imps = {i["module"]: i for i in _facts(src).imports}
    assert imps["./m"]["names"] == ["D", "a", "b"] and imps["./m"]["aliases"] == {"c": "b"}
    assert imps["n"]["names"] == ["N"] and imps["./side"]["names"] == []
    assert imps["./t"]["names"] == ["T"] and imps["./z"]["names"] == ["z"]
    assert imps["./all"]["names"] == [] and imps["./cjs"]["names"] == ["p", "q"]
    assert imps["./Lazy"].get("lazy") is True and imps["n"]["stdlib"] is False
    assert imps["./m"]["line"] == 1


def test_jsdoc_becomes_doc_and_doc_more():
    src = ("/**\n * Fetch the user.\n * Retries twice.\n * @param id the id\n */\n"
           "export const getUser = (id) => id;\n\n// plain\nfunction nodoc() {}\n")
    facts = _facts(src)
    user = _fn(facts, "getUser")
    assert user["doc"] == "Fetch the user." and user["doc_more"] == ["Retries twice."]
    assert _fn(facts, "nodoc")["doc"] is None


def test_swallow_only_for_empty_or_log_only_catch():
    src = ("function a() { try { go(); } catch (e) {} }\n"
           "function b() { try { go(); } catch (e) { console.error(e); } }\n"
           "function c() { try { go(); } catch (e) { throw e; } }\n"
           "function d() { try { go(); } catch (e) { setError(e); } }\n"
           "function e2() { p.catch(() => {}); }\n")
    facts = _facts(src)
    kinds = {n: [h["kind"] for h in _fn(facts, n)["hazards"]] for n in ("a", "b", "c", "d", "e2")}
    assert kinds == {"a": ["swallow"], "b": ["swallow"], "c": [], "d": [], "e2": ["swallow"]}


def test_external_names_the_literal_url_or_the_callee():
    src = ("async function a() { await fetch('/api/x'); }\n"
           "async function b(u) { await fetch(u); }\n"
           "async function c(id) { await axios.post(`/api/y/${id}`, {}); }\n"
           "const http = axios.create({});\nasync function d() { await http.get('/z'); }\n"
           "function e() { cache.get('/nope'); }\n")
    facts = _facts(src)
    what = {n: [h["what"] for h in _fn(facts, n)["hazards"] if h["kind"] == "external"]
            for n in "abcde"}
    assert what == {"a": ["/api/x"], "b": ["fetch"], "c": ["/api/y/{id}"], "d": ["/z"], "e": []}


def test_write_hazards():
    src = ("let counter = 0;\nlet other;\nconst K = 1;\n"
           "function a() { localStorage.setItem('k', 'v'); }\n"
           "function b() { counter += 1; other = 2; }\n"
           "function c() { let counter = 0; counter++; }\n"
           "function d(counter) { counter = 3; K.x; }\n"
           "import fs from 'fs';\nfunction e() { fs.writeFileSync('p', 'd'); }\n"
           "function f() { sessionStorage.getItem('k'); }\n")
    facts = _facts(src)
    got = {n: sorted(h["what"] for h in _fn(facts, n)["hazards"] if h["kind"] == "write") for n in "abcdef"}
    assert got == {"a": ["localStorage.setItem"], "b": ["counter", "other"], "c": [], "d": [],
                   "e": ["fs.writeFileSync"], "f": []}


def test_every_function_has_the_python_record_keys():
    fn = _fn(_facts("export const f = (a) => a;\n"), "f")
    for key in ("name", "signature", "is_async", "is_generator", "decorators", "start_line", "end_line",
                "doc", "doc_more", "shape", "callees", "hazards", "literals", "sfp", "bfp"):
        assert key in fn, key
    assert fn["sfp"]


def test_intra_file_calls_and_recursion():
    facts = _facts("function a() { b(); }\nfunction b() { b(); ext(); }\n")
    assert facts.calls == {"a": ["b"], "b": ["b"]} and facts.recursive == ["b"]


def test_js_and_jsx_extensions_use_the_same_front_end():
    for path in ("a.js", "a.jsx", "a.mjs", "a.cjs"):
        assert _fn(_facts("const f = () => go();\n", path), "f")["callees"] == ["go"]


def _entries(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str, str]]:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return {(e["file"], e["name"], e["kind"]) for e in flow.entry_points(build_facts(tmp_path).files)}


def test_express_routes_are_entries(tmp_path):
    src = ("const app = express();\nfunction handler(req, res) { res.send(1); }\n"
           "app.get('/x', handler);\nrouter.post(`/y/${id}`, auth, async (req, res) => { save(); });\n"
           "cache.get('/nope', fallback);\n")
    found = _entries(tmp_path, {"srv.ts": src})
    assert ("srv.ts", "handler", "decorator") in found
    assert ("srv.ts", "POST /y/{id}", "decorator") in found
    assert not any("nope" in name for _f, name, _k in found)


def test_next_app_router_files_are_entries(tmp_path):
    files = {
        "app/api/users/[id]/route.ts": "export async function GET() { return load(); }\n"
                                       "export const POST = async () => save();\nexport function helper() {}\n",
        "src/app/(shop)/cart/page.tsx": "export default function CartPage() { return null; }\n",
        "app/other.ts": "export function GET() {}\n",
    }
    found = _entries(tmp_path, files)
    assert ("app/api/users/[id]/route.ts", "GET", "decorator") in found
    assert ("app/api/users/[id]/route.ts", "POST", "decorator") in found
    assert ("app/api/users/[id]/route.ts", "helper", "decorator") not in found
    assert ("src/app/(shop)/cart/page.tsx", "CartPage", "decorator") in found
    assert not any(f == "app/other.ts" for f, _n, _k in found)


def test_nest_style_method_decorators_are_entries(tmp_path):
    src = "class C {\n  @Get('x')\n  list() { return 1; }\n  plain() {}\n}\n"
    found = _entries(tmp_path, {"c.controller.ts": src})
    assert ("c.controller.ts", "C.list", "decorator") in found
    assert ("c.controller.ts", "C.plain", "decorator") not in found


def test_exported_functions_are_marked_and_others_are_not():
    src = ("export function load() { return 1; }\n"
           "export const save = () => 2;\n"
           "function helper() { return 3; }\n"
           "export default function Page() { return null; }\n")
    facts = _facts(src, "a.ts")
    assert {f["name"] for f in facts.functions if f.get("exported")} == {"load", "save", "Page"}
    assert not _fn(facts, "helper").get("exported")
