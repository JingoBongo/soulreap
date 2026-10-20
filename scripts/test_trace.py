"""Tests for turning a Python or Node stack trace into slice seeds."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from repo_fixtures import tree
from srlib.symbols import symbol_table
from srlib.trace import parse_trace

FILES = {
    "app.py": "from store import save\n\ndef main():\n    save(1)\n",
    "store.py": "CACHE = {}\n\ndef save(v):\n    CACHE[v] = helper(v)\n",
}


def test_python_traceback_deepest_first_and_outside_frames_dropped(tmp_path):
    root, facts = tree(tmp_path, FILES)
    tb = (f'Traceback (most recent call last):\n  File "{root}/app.py", line 4, in main\n    save(1)\n'
          '  File "/usr/lib/python3.12/json/__init__.py", line 9, in dumps\n'
          '  File "store.py", line 4, in save\n    CACHE[v] = helper(v)\nKeyError: 1\n')
    assert parse_trace(tb, root, symbol_table(facts)) == ["store.py::save", "app.py::main"]


def test_node_stack_frames(tmp_path):
    js = "function f() {\n  g();\n}\nfunction g() {\n  throw new Error('x');\n}\n"
    root, facts = tree(tmp_path, {"src/a.js": js})
    st = f"Error: x\n    at g ({root}/src/a.js:5:9)\n    at f ({root}/src/a.js:2:3)\n    at node:internal/main:1:1\n"
    assert parse_trace(st, root, symbol_table(facts)) == ["src/a.js::g", "src/a.js::f"]


def test_method_frame_maps_to_innermost_symbol(tmp_path):
    root, facts = tree(tmp_path, {"c.py": "class C:\n    def m(self):\n        raise ValueError\n"})
    assert parse_trace(f'  File "{root}/c.py", line 3, in m\n', root, symbol_table(facts)) == ["c.py::C.m"]


def test_repeated_frames_are_deduped_and_unknown_text_gives_nothing(tmp_path):
    root, facts = tree(tmp_path, FILES)
    table = symbol_table(facts)
    tb = f'  File "{root}/store.py", line 4, in save\n  File "{root}/store.py", line 4, in save\n'
    assert parse_trace(tb, root, table) == ["store.py::save"]
    assert parse_trace("nothing here\n", root, table) == []
