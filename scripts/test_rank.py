"""Tests for ranking: read set, checklist, card count, topics."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib import rank as rk
from srlib.build import build_facts


def _facts(tmp_path: Path, files: dict[str, str]) -> dict:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return build_facts(tmp_path).files


SMALL = {
    "app.py": (
        "from store import save\n\n\ndef main():\n"
        "    try:\n        save(1)\n    except Exception:\n        pass\n"
    ),
    "store.py": "CACHE = {}\n\n\ndef save(v):\n    CACHE[v] = 1\n",
    "tests/test_app.py": "def test_x():\n    assert True\n",
    "empty.py": "",
}


def _big(n: int) -> str:
    body = "".join(f"def helper{i}():\n    return {i}\n\n\n" for i in range(n))
    return body + "def main():\n    try:\n        helper0()\n    except Exception:\n        pass\n"


def test_read_set_small_repo_reads_everything_but_tests_and_trivial(tmp_path):
    read = rk.read_set(_facts(tmp_path, SMALL))
    assert read["whole"] is True
    assert sorted(f["file"] for f in read["files"]) == ["app.py", "store.py"]
    assert all(f["ranges"] is None for f in read["files"]) and read["not_covered"] == []


def test_read_set_over_budget_takes_ranges_of_big_file_and_names_the_rest(tmp_path):
    facts = _facts(tmp_path, {"big.py": _big(150), "store.py": SMALL["store.py"]})
    read = rk.read_set(facts, budget=50)
    big = next(f for f in read["files"] if f["file"] == "big.py")
    assert read["whole"] is False and big["ranges"] and big["ranges"][0][2] == "main"
    assert read["total_lines"] <= 50 + rk.BIG_FILE_LINES


def test_checklist_lists_check_kinds_in_read_symbols_only(tmp_path):
    facts = _facts(tmp_path, SMALL)
    items = rk.checklist(facts, rk.read_set(facts))
    assert {(i["symbol"], i["kind"]) for i in items} == {("app.py::main", "swallow"), ("store.py::save", "write")}


def test_checklist_covers_js_hazards_and_skips_languages_without_a_reader(tmp_path):
    facts = _facts(tmp_path, {"web.js": "function go() { try { x() } catch (e) {} }\n",
                              "m.go": "func go() { defer recover() }\n", **SMALL})
    items = rk.checklist(facts, rk.read_set(facts))
    assert ("web.js::go", "swallow") in {(i["symbol"], i["kind"]) for i in items}
    assert not any(i["symbol"].startswith("m.go") for i in items)


def test_card_count_is_clamped():
    assert rk.card_count({"total_lines": 10}) == 1
    assert rk.card_count({"total_lines": 842}) == 3
    assert rk.card_count({"total_lines": 100_000}) == rk.MAX_CARDS


def test_topics_put_entry_flows_before_components(tmp_path):
    facts = _facts(tmp_path, SMALL)
    read = rk.read_set(facts)
    assert rk.topics(facts, read, 2)[0] == {"kind": "flow", "target": "app.py::main"}


def test_read_set_exclude_glob_drops_matching_files(tmp_path):
    read = rk.read_set(_facts(tmp_path, SMALL), exclude=["store.py"])
    assert [f["file"] for f in read["files"]] == ["app.py"]


def test_read_set_with_tests_includes_test_files(tmp_path):
    facts = _facts(tmp_path, {**SMALL, "tests/test_app.py": "from app import main\n\n\ndef test_x():\n    main()\n"})
    assert "tests/test_app.py" in [f["file"] for f in rk.read_set(facts, with_tests=True)["files"]]


def test_candidates_reexporting_init_is_not_trivial(tmp_path):
    facts = _facts(tmp_path, {"pkg/__init__.py": "from pkg.core import run\n\n__all__ = ['run']\n",
                              "pkg/core.py": "def run():\n    return 1\n"})
    assert "pkg/__init__.py" in rk.candidates(facts)


def test_read_set_includes_small_config_and_manifest_files_but_not_secrets(tmp_path):
    from srlib.walk import walk

    files = {**SMALL, "config.yaml": "ui:\n  port: 4056\n", "requirements.txt": "requests\n",
             "big.yaml": "k: v\n" * 300, ".env": "TOKEN=x\n"}
    facts = _facts(tmp_path, files)
    read = rk.read_set(facts, entries=walk(tmp_path))
    assert [c["file"] for c in read["configs"]] == ["config.yaml", "requirements.txt"]
    assert read["total_lines"] == sum(f["lines"] for f in read["files"]) + 3


ROUTES = {
    "web.py": (
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
        "@app.get('/a')\ndef a_trivial():\n    return 1\n\n\n"
        "@app.get('/z')\ndef z_rich():\n    return step1()\n\n\n"
        "def step1():\n    return step2()\n\n\n"
        "def step2():\n    try:\n        return 2\n    except Exception:\n        return 0\n"
    ),
}


def test_topics_rank_entry_flows_by_what_they_reach_not_by_name(tmp_path):
    facts = _facts(tmp_path, ROUTES)
    read = rk.read_set(facts)
    assert rk.topics(facts, read, 1, root=tmp_path) == [{"kind": "flow", "target": "web.py::z_rich"}]


def test_config_files_skip_docs_fixtures_and_captured_data(tmp_path):
    from srlib.walk import walk

    files = {**SMALL, "config.yaml": "port: 1\n", "docs/connector-data/resp.json": '{"a": 1}\n',
             "tests/fixtures/cfg.yaml": "x: 1\n"}
    facts = _facts(tmp_path, files)
    assert [c["file"] for c in rk.read_set(facts, entries=walk(tmp_path))["configs"]] == ["config.yaml"]


def test_ranges_include_high_fan_in_symbols_without_hazards(tmp_path):
    hub = "".join(f"def filler{i}():\n    return {i}\n\n\n" for i in range(120)) + "def core(x):\n    return x * 2\n"
    files = {"hub.py": hub,
             "a.py": "from hub import core\n\n\ndef a():\n    return core(1)\n",
             "b.py": "from hub import core\n\n\ndef b():\n    return core(2)\n"}
    read = rk.read_set(_facts(tmp_path, files), budget=60)
    hub_entry = next(f for f in read["files"] if f["file"] == "hub.py")
    assert hub_entry["ranges"] and hub_entry["ranges"][0][2] == "core"


def test_read_set_never_exceeds_the_budget(tmp_path):
    big = "def main():\n    try:\n" + "        x = 1\n" * 500 + "    except Exception:\n        pass\n"
    read = rk.read_set(_facts(tmp_path, {"big.py": big, **SMALL}), budget=100)
    assert read["total_lines"] <= 100


def test_module_level_hazards_reach_the_checklist(tmp_path):
    boot = ("import requests\n\ntry:\n    TOKEN = requests.get('http://x/token').text\n"
            "except Exception:\n    TOKEN = ''\n")
    files = {"boot.py": boot}
    facts = _facts(tmp_path, files)
    items = {(i["symbol"], i["kind"]) for i in rk.checklist(facts, rk.read_set(facts))}
    assert {("boot.py::__module__", "external"), ("boot.py::__module__", "swallow")} <= items


def test_topics_only_name_entries_whose_code_is_in_the_read_set():
    facts = {"api.py": {"lang": "python", "lines": 50, "functions": [
        {"name": "a", "start_line": 1, "end_line": 5, "decorators": ["app.get('/a')"], "hazards": []},
        {"name": "b", "start_line": 20, "end_line": 30, "decorators": ["app.get('/b')"], "hazards": []}]}}
    read = {"files": [{"file": "api.py", "lines": 5, "ranges": [[1, 5, "a"]], "score": 5.0}]}
    assert [t["target"] for t in rk.topics(facts, read, 5)] == ["api.py::a"]


def _hub(n: int) -> str:
    """A big tooling script: many functions, hazards, a main — what fan-in and reach used to love."""
    body = "import os\nimport requests\n\n\n" + "".join(
        f"def step{i}(p):\n    requests.get(p)\n    os.remove(p)\n\n\n" for i in range(n))
    calls = "".join(f"    step{i}('x')\n" for i in range(n))
    return body + "def main():\n" + calls + "\n\nif __name__ == '__main__':\n    main()\n"


SERVICE = {
    "svc/__init__.py": "", "svc/main.py": "from svc.routes import handle\n\napp = handle\n",
    "svc/routes.py": "from svc.store import save\n\n\ndef handle(x):\n    return save(x)\n",
    "svc/store.py": "DATA = {}\n\n\ndef save(x):\n    DATA[x] = 1\n    return x\n",
    "scripts/__init__.py": "", "scripts/backfill.py": _hub(40),
}


def test_read_set_starts_from_what_runs_and_follows_its_imports(tmp_path):
    facts = _facts(tmp_path, SERVICE)
    read = rk.read_set(facts, budget=60, roots=["svc/main.py"])
    files = [f["file"] for f in read["files"]]
    assert files[:3] == ["svc/main.py", "svc/routes.py", "svc/store.py"]
    assert "scripts/backfill.py" not in files[:3]


def test_two_deployed_services_share_the_budget(tmp_path):
    two = {"a/__init__.py": "", "a/main.py": "from a.core import run\n\napp = run\n",
           "a/core.py": "def run():\n    return 1\n" * 1 + "\n" * 40,
           "b/__init__.py": "", "b/main.py": "from b.core import run\n\napp = run\n",
           "b/core.py": "def run():\n    return 2\n" + "\n" * 40}
    facts = _facts(tmp_path, two)
    files = [f["file"] for f in rk.read_set(facts, budget=12, roots=["a/main.py", "b/main.py"])["files"]]
    assert {"a/main.py", "b/main.py"} <= set(files)


def test_a_big_root_with_only_module_level_code_is_read_from_the_top(tmp_path):
    facts = _facts(tmp_path, {"app.py": "import os\n" + "".join(f"X{i} = os.getenv('X{i}')\n" for i in range(500))})
    read = rk.read_set(facts, budget=100, roots=["app.py"])
    assert read["files"] and read["files"][0]["file"] == "app.py" and read["files"][0]["ranges"][0][0] == 1


def test_without_deploy_files_main_guards_outside_poc_and_evals_are_the_roots(tmp_path):
    files = {"tool/__init__.py": "", "tool/cli.py": "from tool.core import go\n\n\ndef main():\n    go()\n\n\n"
             "if __name__ == '__main__':\n    main()\n",
             "tool/core.py": "def go():\n    return 1\n",
             "poc/old.py": _hub(40), "evals/check.py": _hub(30)}
    facts = _facts(tmp_path, files)
    assert rk.default_roots(facts) == ["tool/cli.py"]
    files_read = [f["file"] for f in rk.read_set(facts, budget=40, roots=rk.default_roots(facts))["files"]]
    assert files_read[:2] == ["tool/cli.py", "tool/core.py"]


def test_topics_give_each_root_a_card_and_group_routes_by_file(tmp_path):
    routes = "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n" + "".join(
        f"@router.get('/r{i}')\ndef r{i}():\n    return {i}\n\n\n" for i in range(5))
    files = {"api/__init__.py": "", "api/routes.py": routes,
             "api/main.py": "from api.routes import router\n\n\ndef main():\n    return router\n\n\n"
                            "if __name__ == '__main__':\n    main()\n"}
    facts = _facts(tmp_path, files)
    read = rk.read_set(facts, budget=3000, roots=["api/main.py"])
    got = rk.topics(facts, read, 8, root=tmp_path, roots=[{"file": "api/main.py", "symbol": None}])
    assert got[0] == {"kind": "flow", "target": "api/main.py::main"}
    assert {"kind": "component", "target": "api/routes.py"} in got
    assert not any(t["target"].startswith("api/routes.py::") for t in got)


def test_config_files_skip_eval_question_sets_and_prototypes(tmp_path):
    from srlib.walk import walk

    files = {**SMALL, "config.yaml": "port: 1\n", "evals/repo/questions.yaml": "q: secret answer\n",
             "examples/demo.yaml": "x: 1\n", "poc/settings.yaml": "y: 1\n"}
    facts = _facts(tmp_path, files)
    assert [c["file"] for c in rk.read_set(facts, entries=walk(tmp_path))["configs"]] == ["config.yaml"]


def test_files_two_imports_away_are_read_where_nearer_code_calls_them(tmp_path):
    lib = "".join(f"def unused{i}():\n    return {i}\n\n\n" for i in range(60)) + "def used():\n    return 1\n"
    files = {"app/__init__.py": "", "app/main.py": "from app.svc import run\n\n\ndef main():\n    run()\n\n\n"
             "if __name__ == '__main__':\n    main()\n",
             "app/svc.py": "from app.lib import used\n\n\ndef run():\n    return used()\n",
             "app/lib.py": lib, "other/unrelated.py": lib.replace("used", "spare")}
    facts = _facts(tmp_path, files)
    read = {f["file"]: f["ranges"] for f in rk.read_set(facts, budget=300, roots=["app/main.py"])["files"]}
    assert read["app/main.py"] is None and read["app/svc.py"] is None
    assert [q for _s, _e, q in read["app/lib.py"]] == ["used"]


def test_imported_files_are_read_where_used_with_their_own_helpers_and_most_used_first(tmp_path):
    filler = "".join(f"def idle{i}():\n    return {i}\n\n\n" for i in range(40))
    files = {
        "app/__init__.py": "",
        "app/main.py": "from app import plan, draw\n\n\ndef main():\n    plan.a()\n    plan.b()\n    plan.c()\n"
                       "    draw.d()\n\n\nif __name__ == '__main__':\n    main()\n",
        "app/plan.py": "def a():\n    return _help()\n\n\ndef b():\n    return 2\n\n\ndef c():\n    return 3\n\n\n"
                       "def _help():\n    return 1\n\n\n" + filler,
        "app/draw.py": "def d():\n    return 4\n\n\n" + filler,
        "other/unrelated.py": filler * 3,
    }
    facts = _facts(tmp_path, files)
    read = rk.read_set(facts, budget=120, roots=["app/main.py"])["files"]
    order = [f["file"] for f in read]
    assert order[:3] == ["app/main.py", "app/plan.py", "app/draw.py"]  # plan: 3 uses, draw: 1
    plan = next(f for f in read if f["file"] == "app/plan.py")
    assert sorted(q for _s, _e, q in plan["ranges"]) == ["_help", "a", "b", "c"]


def test_a_router_the_app_mounts_is_read_at_its_routes(tmp_path):
    routes = "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n" + "".join(
        f"@router.get('/r{i}')\ndef r{i}():\n    return {i}\n\n\n" for i in range(4)) + "".join(
        f"def unused{i}():\n    return {i}\n\n\n" for i in range(30))
    files = {"api/__init__.py": "", "api/routes.py": routes,
             "api/main.py": "from fastapi import FastAPI\nfrom api import routes\n\napp = FastAPI()\n"
                            "app.include_router(routes.router)\n",
             "other/unrelated.py": "".join(f"def x{i}():\n    return {i}\n\n\n" for i in range(80))}
    facts = _facts(tmp_path, files)
    read = {f["file"]: f["ranges"] for f in rk.read_set(facts, budget=200, roots=["api/main.py"])["files"]}
    assert sorted(q for _s, _e, q in read["api/routes.py"]) == ["r0", "r1", "r2", "r3"]


def test_with_no_main_guard_a_conventional_entry_file_is_the_root(tmp_path):
    files = {"main.py": "from fastapi import FastAPI\nfrom engine import run\n\napp = FastAPI()\n",
             "engine.py": "def run():\n    return 1\n", "poc/try.py": _hub(10)}
    facts = _facts(tmp_path, files)
    assert rk.default_roots(facts) == ["main.py"]


def test_a_reached_file_nothing_calls_by_name_is_still_read_before_unreached_code(tmp_path):
    models = "class Base:\n    pass\n\n\n" + "".join(f"class Model{i}(Base):\n    field = {i}\n\n\n" for i in range(40))
    files = {"app/__init__.py": "", "app/models.py": models,
             "app/main.py": "from app import models\n\n\ndef main():\n    return models.Model1.field\n\n\n"
                            "if __name__ == '__main__':\n    main()\n",
             "seed/seed_dev.py": "".join(f"def seed{i}():\n    return {i}\n\n\n" for i in range(40))}
    facts = _facts(tmp_path, files)
    read = [f["file"] for f in rk.read_set(facts, budget=250, roots=["app/main.py"])["files"]]
    assert read[:2] == ["app/main.py", "app/models.py"]


def test_a_module_a_root_loads_through_config_is_read_after_the_root(tmp_path):
    from srlib.reach import linked
    facts = _facts(tmp_path, {"defs.py": "from loader import load\n\n\ndef defs():\n    return load('defs')\n",
                              "loader.py": "def load(p):\n    return p\n",
                              "component.py": "class Component:\n    def build(self):\n        return 1\n",
                              "other.py": _hub(30)})
    roots = ["defs.py"]
    plain = [f["file"] for f in rk.read_set(facts, budget=12, roots=roots)["files"]]
    assert "component.py" not in plain
    joined = [f["file"] for f in rk.read_set(linked(facts, {"defs.py": ["component.py"]}), budget=12,
                                             roots=roots)["files"]]
    assert "component.py" in joined


def test_a_config_loaded_module_comes_first_among_its_roots_imports(tmp_path):
    from srlib.reach import linked
    routes = "".join(f"def r{i}():\n    return {i}\n\n\n" for i in range(6))
    facts = _facts(tmp_path, {"defs.py": "from routes import r0, r1, r2, r3, r4, r5\n\n\ndef defs():\n"
                                         "    return [r0(), r1(), r2(), r3(), r4(), r5()]\n",
                              "routes.py": routes,
                              "component.py": "class Component:\n    def build(self):\n        return 1\n"})
    read = rk.read_set(linked(facts, {"defs.py": ["component.py"]}), budget=12, roots=["defs.py"])
    assert [f["file"] for f in read["files"]][:2] == ["defs.py", "component.py"]


def test_a_config_loaded_module_gets_a_card_right_after_its_root(tmp_path):
    from srlib.reach import linked
    routes = "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n" + "".join(
        f"@router.get('/r{i}')\ndef r{i}():\n    return {i}\n\n\n" for i in range(5))
    facts = linked(_facts(tmp_path, {"defs.py": "import api\n\n\ndef defs():\n    return api.router\n",
                                     "api.py": routes,
                                     "component.py": "class Component:\n    def build(self):\n        return 1\n"}),
                   {"defs.py": ["component.py"]})
    read = rk.read_set(facts, budget=400, roots=["defs.py"])
    picked = rk.topics(facts, read, 3, roots=[{"file": "defs.py", "symbol": None, "via": "x"}])
    assert [t["target"] for t in picked][:2] == ["defs.py", "component.py"]


def test_an_import_only_re_exported_in_all_is_not_a_use(tmp_path):
    from srlib.reach import _depths
    facts = _facts(tmp_path, {
        "cli.py": "from fresh import load\nfrom walk import walk\n\n__all__ = ['walk']\n\n\n"
                  "def main():\n    load()\n\n\nif __name__ == '__main__':\n    main()\n",
        "fresh.py": "from build import build\n\n\ndef load():\n    return build()\n",
        "build.py": "from walk import walk\n\n\ndef build():\n    return walk()\n",
        "walk.py": "def walk():\n    return []\n"})
    depth = _depths(facts, ["cli.py"])
    assert (depth["fresh.py"], depth["build.py"], depth["walk.py"]) == (1, 2, 3)


def test_an_agents_local_settings_are_not_app_config():
    from types import SimpleNamespace as E
    entries = [E(kind="non_code", category=rk.CONFIG_CATEGORIES[0], lines=10, path=p)
               for p in (".claude/settings.local.json", "config.yaml")]
    assert [c["file"] for c in rk.config_files(entries)] == ["config.yaml"]


def test_a_facade_module_still_reaches_what_it_re_exports(tmp_path):
    from srlib.reach import _depths
    facts = _facts(tmp_path, {"cli.py": "from api import handler\n\n\ndef main():\n    handler()\n",
                              "api.py": "from impl import handler\n\n__all__ = ['handler']\n",
                              "impl.py": "def handler():\n    return 1\n"})
    depth = _depths(facts, ["cli.py"])
    assert (depth["api.py"], depth["impl.py"]) == (1, 2)


def test_a_name_used_in_a_quoted_annotation_is_not_only_re_exported(tmp_path):
    facts = _facts(tmp_path, {"cli.py": "from model import Foo\n\n__all__ = ['Foo']\n\n\n"
                                        "def f(x: 'Foo') -> None:\n    return None\n",
                              "model.py": "class Foo:\n    pass\n"})
    assert not any(i.get("reexport") for i in facts["cli.py"]["imports"])
