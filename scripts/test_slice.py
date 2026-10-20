"""Tests for the token-budgeted slice around a symbol."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from repo_fixtures import tree
from srlib.slice import build_slice, slice_seeds, tokens
from srlib.symbols import symbol_table

FILES = {
    "app.py": "from store import save\n\ndef main():\n    save(1)\n",
    "store.py": "CACHE = {}\n\ndef save(v):\n    CACHE[v] = helper(v)\n\ndef helper(v):\n    return deep(v)\n\n"
                "def deep(v):\n    return v\n",
    "tests/test_store.py": "from store import save\n\ndef test_save():\n    save(2)\n",
}


def _entries(result):
    return {e["key"]: (e["hop"], e["tier"]) for e in result["entries"]}


def test_seed_is_source_neighbours_skeleton_two_hops_one_line(tmp_path):
    root, facts = tree(tmp_path, FILES)
    got = _entries(build_slice(root, facts, ["store.py::save"]))
    assert got["store.py::save"] == (0, "source")
    assert got["store.py::helper"] == (1, "skeleton")
    assert got["app.py::main"] == (1, "skeleton")
    assert got["store.py::deep"] == (2, "line")


def test_seed_source_and_neighbour_signature_are_in_the_text(tmp_path):
    root, facts = tree(tmp_path, FILES)
    text = build_slice(root, facts, ["store.py::save"])["text"]
    assert "CACHE[v] = helper(v)" in text
    assert "  helper(v)" in text
    assert text.startswith("# slice store.py::save  budget 4000  used ")
    assert text.rstrip().splitlines()[-1] == "# frontier: none"


def test_callees_rank_before_callers_at_the_same_hop(tmp_path):
    root, facts = tree(tmp_path, FILES)
    keys = [e["key"] for e in build_slice(root, facts, ["store.py::save"])["entries"]]
    assert keys.index("store.py::helper") < keys.index("app.py::main")


def test_tests_are_not_neighbours(tmp_path):
    root, facts = tree(tmp_path, FILES)
    assert "tests/test_store.py::test_save" not in _entries(build_slice(root, facts, ["store.py::save"]))


def test_tight_budget_degrades_then_spills_to_frontier(tmp_path):
    root, facts = tree(tmp_path, FILES)
    result = build_slice(root, facts, ["store.py::save"], budget=50)
    assert result["used"] <= 50
    assert result["frontier"]
    assert result["text"].rstrip().splitlines()[-1].startswith("# frontier: ")
    assert tokens(result["text"]) == result["used"]


def test_oversized_seed_falls_back_to_skeleton(tmp_path):
    big = "def big():\n" + "".join(f"    x{i} = {i}\n" for i in range(400))
    root, facts = tree(tmp_path, {"b.py": big})
    result = build_slice(root, facts, ["b.py::big"], budget=200)
    assert result["entries"][0]["tier"] == "skeleton"
    assert "source omitted (401 lines)" in result["text"]


def test_frontier_is_capped_at_twenty_names(tmp_path):
    fan = "".join(f"def c{i}():\n    hub()\n\n" for i in range(120))
    root, facts = tree(tmp_path, {"h.py": "def hub():\n    pass\n\n" + fan})
    result = build_slice(root, facts, ["h.py::hub"], budget=400)
    assert result["used"] <= 400 and len(result["frontier"]) > 20
    last = result["text"].rstrip().splitlines()[-1]
    assert last.count("h.py::c") == 20 and last.endswith(f"(+{len(result['frontier']) - 20} more)")


def test_file_target_seeds_its_symbols_and_unknown_raises(tmp_path):
    _root, facts = tree(tmp_path, FILES)
    table = symbol_table(facts)
    assert slice_seeds(table, facts, "store.py") == ["store.py::deep", "store.py::helper", "store.py::save"]
    assert slice_seeds(table, facts, "helper") == ["store.py::helper"]
    with pytest.raises(LookupError):
        slice_seeds(table, facts, "nope")
