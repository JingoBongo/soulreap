"""Tests for pairing symbols between two fact sets."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from repo_fixtures import tree
from srlib.delta import changed_files, symbol_changes

BASE = {
    "s.py": "import subprocess\n\ndef save(v):\n    return v\n\ndef gone():\n    pass\n\n"
            "def same():\n    return 1\n\ndef body():\n    return 1\n",
    "u.py": "def untouched():\n    pass\n",
}
HEAD = {
    "s.py": "import subprocess\n\ndef save(v, w):\n    subprocess.run(['git', 'reset', '--hard'])\n    return v\n\n"
            "def same():\n    return 1\n\ndef body():\n    return 2\n\ndef fresh():\n    pass\n",
    "u.py": "def untouched():\n    pass\n",
}


def _pair(tmp_path, base, head):
    return tree(tmp_path / "b", base)[1], tree(tmp_path / "h", head)[1]


def test_pairs_symbols_by_key(tmp_path):
    got = {c["key"]: (c["status"], c["what"]) for c in symbol_changes(*_pair(tmp_path, BASE, HEAD))}
    assert got["s.py::gone"] == ("removed", ["deleted"])
    assert got["s.py::fresh"] == ("added", ["added"])
    assert got["s.py::body"] == ("modified", ["body changed"])
    assert got["s.py::save"][0] == "modified"
    assert "signature changed" in got["s.py::save"][1]
    assert "s.py::same" not in got
    assert "u.py::untouched" not in got


def test_changed_files_skips_identical_content(tmp_path):
    assert changed_files(*_pair(tmp_path, BASE, HEAD)) == ["s.py"]


def test_a_whole_new_file_is_all_added(tmp_path):
    got = symbol_changes({}, tree(tmp_path, {"n.py": "def a():\n    pass\n"})[1])
    assert [(c["key"], c["status"]) for c in got] == [("n.py::a", "added")]


def test_test_files_are_marked(tmp_path):
    got = symbol_changes({}, tree(tmp_path, {"tests/test_x.py": "def test_a():\n    pass\n"})[1])
    assert got[0]["test"] is True and got[0]["file"] == "tests/test_x.py"


def test_import_time_code_counts_only_when_its_hazards_change(tmp_path):
    base = {"m.py": "X = 1\n\ndef f():\n    pass\n"}
    head = {"m.py": "X = 2\n\ndef f():\n    pass\n"}
    assert symbol_changes(*_pair(tmp_path, base, head)) == []


def test_a_class_is_reported_for_its_own_shape_not_its_methods(tmp_path):
    base = {"c.py": "class C:\n    a: int = 1\n\n    def m(self):\n        return 1\n"}
    methods_only = {"c.py": "class C:\n    a: int = 1\n\n    def m(self, x):\n        return 1\n"}
    got = [c["key"] for c in symbol_changes(*_pair(tmp_path / "1", base, methods_only))]
    assert got == ["c.py::C.m"]
    new_field = {"c.py": "class C:\n    a: int = 1\n    b: int = 2\n\n    def m(self):\n        return 1\n"}
    got = {c["key"]: c["what"] for c in symbol_changes(*_pair(tmp_path / "2", base, new_field))}
    assert got == {"c.py::C": ["fields changed"]}
