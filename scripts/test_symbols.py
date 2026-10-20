"""Tests for the symbol table and the callers/writes queries."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib import symbols as sy
from srlib.build import build_facts

FILES = {
    "app.py": "from store import save\n\n\ndef main():\n    save(1)\n",
    "store.py": (
        "CACHE = {}\n\n\ndef save(v):\n    CACHE[v] = 1\n\n\n"
        "class Store:\n    def put(self):\n        self.flush()\n\n    def flush(self):\n        self.n = 0\n\n\n"
        "class Other:\n    def put(self):\n        return 1\n"
    ),
}


def _facts(tmp_path: Path) -> tuple[Path, dict]:
    for rel, text in FILES.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path, build_facts(tmp_path).files


def test_symbol_table_has_functions_methods_and_classes(tmp_path):
    _, facts = _facts(tmp_path)
    table = sy.symbol_table(facts)
    assert {"app.py::main", "store.py::save", "store.py::Store", "store.py::Store.put"} <= set(table)
    row = table["store.py::Store.flush"]
    assert row["kind"] == "method" and row["start"] == 12 and len(row["sfp"]) == 10


def test_resolve_exact_key_and_unique_suffix(tmp_path):
    _, facts = _facts(tmp_path)
    table = sy.symbol_table(facts)
    assert sy.resolve_symbol(table, "store.py::save") == ["store.py::save"]
    assert sy.resolve_symbol(table, "flush") == ["store.py::Store.flush"]


def test_resolve_ambiguous_name_returns_every_candidate(tmp_path):
    _, facts = _facts(tmp_path)
    assert sy.resolve_symbol(sy.symbol_table(facts), "put") == ["store.py::Other.put", "store.py::Store.put"]


def test_callers_across_files_and_through_self(tmp_path):
    root, facts = _facts(tmp_path)
    assert [c["caller"] for c in sy.callers(root, facts, "store.py::save")] == ["app.py::main"]
    assert [c["caller"] for c in sy.callers(root, facts, "store.py::Store.flush")] == ["store.py::Store.put"]


def test_writes_finds_every_writer_of_a_name(tmp_path):
    _, facts = _facts(tmp_path)
    table = sy.symbol_table(facts)
    assert [w["symbol"] for w in sy.writes(table, "CACHE")] == ["store.py::save"]
    assert [w["symbol"] for w in sy.writes(table, "n")] == ["store.py::Store.flush"]


def test_class_fingerprint_moves_when_a_field_changes(tmp_path):
    (tmp_path / "m.py").write_text("from dataclasses import dataclass\n\n@dataclass\nclass C:\n    port: int = 4053\n",
                                   encoding="utf-8")
    one = sy.symbol_table(build_facts(tmp_path).files)["m.py::C"]["sfp"]
    (tmp_path / "m.py").write_text("from dataclasses import dataclass\n\n@dataclass\nclass C:\n    port: int = 8080\n",
                                   encoding="utf-8")
    assert sy.symbol_table(build_facts(tmp_path).files)["m.py::C"]["sfp"] != one


def test_writes_accepts_a_self_attribute_name(tmp_path):
    (tmp_path / "s.py").write_text("class S:\n    def put(self, k):\n        self._cache[k] = 1\n", encoding="utf-8")
    table = sy.symbol_table(build_facts(tmp_path).files)
    assert [w["symbol"] for w in sy.writes(table, "self._cache")] == ["s.py::S.put"]


def test_module_row_outlives_its_last_hazard(tmp_path):
    (tmp_path / "boot.py").write_text("import os\n\nos.remove('x')\n", encoding="utf-8")
    assert "boot.py::__module__" in sy.symbol_table(build_facts(tmp_path).files)
    (tmp_path / "boot.py").write_text("import os\n\nPATH = os.getcwd()\n", encoding="utf-8")
    row = sy.symbol_table(build_facts(tmp_path).files).get("boot.py::__module__")
    assert row is not None and row["hazards"] == []


def test_writes_finds_a_function_that_mutates_a_dict_it_was_handed(tmp_path):
    (tmp_path / "loop.py").write_text(
        "def check(last_revive, now):\n    last_revive['a'] = now\n\n\n"
        "def main():\n    last_revive = {}\n    check(last_revive, 1)\n", encoding="utf-8")
    table = sy.symbol_table(build_facts(tmp_path).files)
    assert [w["symbol"] for w in sy.writes(table, "last_revive")] == ["loop.py::check"]
    assert not any(h["kind"] == "arg_write" for h in table["loop.py::main"]["hazards"])
