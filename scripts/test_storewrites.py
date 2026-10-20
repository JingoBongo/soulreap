"""`writes` sees a store client's own verbs: `vault.put(k, v)` is a write of the vault."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import soulreap as sr
from srlib import query as qy
from srlib.build import build_facts
from srlib.storewrites import merged_writes, store_writes
from srlib.symbols import symbol_table, writes

STORE = (
    "from vaults import Vault\n\n"
    "repos = Vault('repos')\n\n\n"
    "def add(name, entry):\n    repos.put(name, entry)\n\n\n"
    "def drop(name):\n    repos.pop(name)\n\n\n"
    "def wipe():\n    repos.delete_vault()\n\n\n"
    "def read(name):\n    return repos.get(name)\n\n\n"
    "def later(name):\n    def inner():\n        repos.put(name, 1)\n    return inner\n"
)
USER = (
    "from app import repos as r\nimport app\n\n\n"
    "def via_alias():\n    r.put('a', 1)\n\n\n"
    "def via_module():\n    app.repos.put('b', 2)\n"
)


def _repo(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text(STORE, encoding="utf-8")
    (root / "user.py").write_text(USER, encoding="utf-8")
    return root, build_facts(root).files


def _symbols(rows: list[dict]) -> set[str]:
    return {row["symbol"] for row in rows}


def test_put_pop_and_a_wipe_verb_are_writes_and_get_is_not(tmp_path):
    root, facts = _repo(tmp_path)
    found = _symbols(store_writes(root, facts))
    assert {"app.py::add", "app.py::drop", "app.py::wipe"} <= found
    assert "app.py::read" not in found


def test_a_nested_function_is_reported_on_its_enclosing_function(tmp_path):
    root, facts = _repo(tmp_path)
    assert "app.py::later" in _symbols(store_writes(root, facts))


def test_imported_alias_and_module_attribute_reach_the_store(tmp_path):
    root, facts = _repo(tmp_path)
    found = _symbols(store_writes(root, facts))
    assert {"user.py::via_alias", "user.py::via_module"} <= found


def test_binding_and_store_name_both_select_the_writes(tmp_path):
    root, facts = _repo(tmp_path)
    table = symbol_table(facts)
    by_binding = _symbols(merged_writes(root, facts, writes(table, "repos"), "repos"))
    assert {"app.py::add", "app.py::wipe", "user.py::via_alias", "user.py::via_module"} <= by_binding
    other = _symbols(merged_writes(root, facts, writes(table, "elsewhere"), "elsewhere"))
    assert not other & by_binding


def test_cli_writes_lists_every_put_site(tmp_path, capsys):
    root, _ = _repo(tmp_path)
    assert sr.main(["writes", "repos", "--root", str(root), "--out", str(tmp_path / "map")]) == 0
    printed = capsys.readouterr().out
    assert "app.py::add" in printed and "repos.put" in printed
    assert "app.py::read" not in printed


def test_impact_names_the_store_a_function_writes(tmp_path):
    root, facts = _repo(tmp_path)
    out = tmp_path / "map"
    result = qy.impact(root, out, facts, "app.py::add")
    assert any("repos.put" in w and "store repos" in w for w in result["writes"])
