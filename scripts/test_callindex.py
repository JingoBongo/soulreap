"""Tests for the shared call index: forward and reverse call edges in one pass."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from repo_fixtures import tree
from srlib import flow
from srlib.callindex import call_index

FILES = {
    "app.py": "from store import save\nimport store\n\n\ndef main():\n    save(1)\n    store.load()\n\n\n"
              "def idle():\n    pass\n",
    "store.py": "def save(v):\n    return helper(v)\n\n\ndef load():\n    pass\n\n\ndef helper(v):\n    return v\n",
}


def test_forward_and_reverse_edges_agree(tmp_path):
    root, facts = tree(tmp_path, FILES)
    calls = call_index(flow.build(root, facts))
    assert calls.callees["app.py::main"] == {"store.py::save", "store.py::load"}
    assert calls.callees["app.py::idle"] == set()
    assert [c["caller"] for c in calls.callers["store.py::helper"]] == ["store.py::save"]
    assert calls.callers["store.py::save"] == [{"caller": "app.py::main", "line": 6, "confidence": "exact"}]


def test_names_keep_unresolved_calls(tmp_path):
    root, facts = tree(tmp_path, {"a.py": "def f():\n    ghost()\n    obj.gone()\n"})
    calls = call_index(flow.build(root, facts))
    assert calls.names["a.py::f"] == {"ghost", "gone"}
    assert calls.callees["a.py::f"] == set()
