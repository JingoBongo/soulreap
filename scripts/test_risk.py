"""Tests for delta's risk tiers and the tests it says to run."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from repo_fixtures import tree
from srlib.delta import symbol_changes
from srlib.risk import assess, side, suggested_tests

MAIN = "\n\nif __name__ == '__main__':\n    main()\n"


def rules(tmp_path, base, head):
    b, h = side(*tree(tmp_path / "b", base)), side(*tree(tmp_path / "h", head))
    return {(f["key"], f["rule"]): f["tier"] for f in assess(symbol_changes(b.files, h.files), b, h)}


def test_removed_but_still_called_is_high(tmp_path):
    base = {"s.py": "def save():\n    pass\n", "app.py": "from s import save\n\ndef main():\n    save()\n"}
    head = {"s.py": "def keep():\n    pass\n", "app.py": "from s import save\n\ndef main():\n    save()\n"}
    assert rules(tmp_path, base, head)[("s.py::save", "removed-still-called")] == "HIGH"


def test_removed_and_caller_updated_is_low(tmp_path):
    base = {"s.py": "def save():\n    pass\n", "app.py": "from s import save\n\ndef main():\n    save()\n"}
    head = {"s.py": "def keep():\n    pass\n", "app.py": "def main():\n    pass\n"}
    got = rules(tmp_path, base, head)
    assert ("s.py::save", "removed-still-called") not in got
    assert got[("s.py::save", "removed")] == "LOW"


def test_signature_change_with_cross_file_caller_is_high(tmp_path):
    base = {"s.py": "def save(a):\n    pass\n", "app.py": "from s import save\n\ndef main():\n    save(1)\n"}
    head = {**base, "s.py": "def save(a, b):\n    pass\n"}
    assert rules(tmp_path, base, head)[("s.py::save", "signature-cross-file")] == "HIGH"


def test_signature_change_with_every_cross_file_caller_updated_is_med(tmp_path):
    base = {"s.py": "def save(a):\n    pass\n", "app.py": "from s import save\n\ndef main():\n    save(1)\n"}
    head = {"s.py": "def save(a, b):\n    pass\n", "app.py": "from s import save\n\ndef main():\n    save(1, 2)\n"}
    got = rules(tmp_path, base, head)
    assert got[("s.py::save", "signature-updated")] == "MED"
    assert ("s.py::save", "signature-cross-file") not in got


def test_signature_change_with_local_callers_only_is_med(tmp_path):
    base = {"s.py": "def save(a):\n    pass\n\ndef run():\n    save(1)\n"}
    head = {"s.py": "def save(a, b):\n    pass\n\ndef run():\n    save(1)\n"}
    got = rules(tmp_path, base, head)
    assert got[("s.py::save", "signature-local")] == "MED"
    assert ("s.py::save", "signature-cross-file") not in got


def test_new_swallow_in_entry_reachable_code_is_med_and_names_the_entry(tmp_path):
    base = {"app.py": "def work():\n    return 1\n\ndef main():\n    work()" + MAIN}
    head = {"app.py": "def work():\n    try:\n        return 1\n    except Exception:\n        pass\n\n"
                      "def main():\n    work()" + MAIN}
    b, h = side(*tree(tmp_path / "b", base)), side(*tree(tmp_path / "h", head))
    found = [f for f in assess(symbol_changes(b.files, h.files), b, h) if f["key"] == "app.py::work"]
    assert [(f["rule"], f["tier"]) for f in found] == [("new-hazard-reachable", "MED")]
    assert "reached from app.py::main" in found[0]["detail"]


def test_new_swallow_in_unreached_code_is_med(tmp_path):
    base = {"lib.py": "def work():\n    return 1\n"}
    head = {"lib.py": "def work():\n    try:\n        return 1\n    except Exception:\n        pass\n"}
    got = rules(tmp_path, base, head)
    assert got[("lib.py::work", "new-hazard")] == "MED"
    assert ("lib.py::work", "new-hazard-reachable") not in got


def test_new_state_write_is_med(tmp_path):
    base = {"c.py": "CACHE = {}\n\ndef put(k):\n    return k\n"}
    head = {"c.py": "CACHE = {}\n\ndef put(k):\n    CACHE[k] = 1\n"}
    assert rules(tmp_path, base, head)[("c.py::put", "new-hazard")] == "MED"


def test_changed_command_literal_is_med(tmp_path):
    base = {"g.py": "import subprocess\n\ndef sync():\n    subprocess.run(['git', 'reset', '--soft'])\n"}
    head = {"g.py": "import subprocess\n\ndef sync():\n    subprocess.run(['git', 'reset', '--hard'])\n"}
    assert rules(tmp_path, base, head)[("g.py::sync", "literal-changed")] == "MED"


def test_callee_change_is_med(tmp_path):
    base = {"a.py": "def x():\n    pass\n\ndef y():\n    pass\n\ndef f():\n    x()\n"}
    head = {"a.py": "def x():\n    pass\n\ndef y():\n    pass\n\ndef f():\n    y()\n"}
    assert rules(tmp_path, base, head)[("a.py::f", "callees-changed")] == "MED"


def test_body_change_in_hot_symbol_is_med_and_in_leaf_is_low(tmp_path):
    callers = "".join(f"def c{i}():\n    hot()\n\n" for i in range(5))
    base = {"a.py": "def hot():\n    return 1\n\ndef leaf():\n    return 1\n\n" + callers}
    head = {"a.py": "def hot():\n    return 2\n\ndef leaf():\n    return 2\n\n" + callers}
    got = rules(tmp_path, base, head)
    assert got[("a.py::hot", "hot-body")] == "MED"
    assert got[("a.py::leaf", "body")] == "LOW"


def test_fan_in_growth_is_med(tmp_path):
    base = {"a.py": "def t():\n    pass\n\ndef c0():\n    t()\n"}
    head = {"a.py": "def t():\n    pass\n\n" + "".join(f"def c{i}():\n    t()\n\n" for i in range(4))}
    assert rules(tmp_path, base, head)[("a.py::t", "fan-in-grew")] == "MED"


def test_added_symbol_is_low(tmp_path):
    base = {"a.py": "def t():\n    pass\n"}
    head = {"a.py": "def t():\n    pass\n\ndef u():\n    pass\n"}
    assert rules(tmp_path, base, head) == {("a.py::u", "added"): "LOW"}


def test_changes_in_test_files_raise_no_findings(tmp_path):
    base = {"tests/test_a.py": "def test_x():\n    pass\n"}
    head = {"tests/test_a.py": "import subprocess\n\ndef test_x():\n    subprocess.run(['git', 'reset', '--hard'])\n"}
    assert rules(tmp_path, base, head) == {}


def test_builtin_callees_do_not_count_as_a_callee_change(tmp_path):
    base = {"a.py": "def f(x):\n    return x\n"}
    head = {"a.py": "def f(x):\n    return isinstance(x, str) and len(x)\n"}
    got = rules(tmp_path, base, head)
    assert ("a.py::f", "callees-changed") not in got
    assert got[("a.py::f", "body")] == "LOW"


def test_findings_are_ordered_high_first(tmp_path):
    base = {"s.py": "def save(a):\n    pass\n\ndef leaf():\n    return 1\n",
            "app.py": "from s import save\n\ndef main():\n    save(1)\n"}
    head = {**base, "s.py": "def save(a, b):\n    pass\n\ndef leaf():\n    return 2\n"}
    b, h = side(*tree(tmp_path / "b", base)), side(*tree(tmp_path / "h", head))
    tiers = [f["tier"] for f in assess(symbol_changes(b.files, h.files), b, h)]
    assert tiers == sorted(tiers, key=["HIGH", "MED", "LOW"].index)


def test_direct_tests_come_before_transitive(tmp_path):
    files = {"s.py": "def save():\n    pass\n", "app.py": "from s import save\n",
             "tests/test_s.py": "from s import save\n\ndef test_save():\n    save()\n",
             "tests/test_app.py": "import app\n\ndef test_app():\n    pass\n"}
    b = side(*tree(tmp_path / "b", files))
    h = side(*tree(tmp_path / "h", {**files, "s.py": "def save():\n    return 1\n"}))
    assert suggested_tests(symbol_changes(b.files, h.files), h) == {
        "direct": ["tests/test_s.py"], "transitive": ["tests/test_app.py"]}
