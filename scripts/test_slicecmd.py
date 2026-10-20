"""Tests for the `slice` command line."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import soulreap
from repo_fixtures import write
from test_trace import FILES


def _run(root, out, *args):
    return soulreap.main(["slice", *args, "--root", str(root), "--out", str(out)])


def test_slice_cli_prints_header_and_frontier(tmp_path, capsys):
    root = write(tmp_path / "r", FILES)
    assert _run(root, tmp_path / "o", "store.py::save", "--budget", "500") == 0
    text = capsys.readouterr().out
    assert text.startswith("# slice store.py::save  budget 500") and "# frontier:" in text


def test_slice_cli_trace_from_file(tmp_path, capsys):
    root = write(tmp_path / "r", FILES)
    tb = tmp_path / "tb.txt"
    tb.write_text(f'  File "{root}/store.py", line 4, in save\n')
    assert _run(root, tmp_path / "o", "--trace", str(tb)) == 0
    assert "CACHE[v] = helper(v)" in capsys.readouterr().out


def test_slice_cli_unknown_and_frameless_trace_exit_2(tmp_path, capsys):
    root = write(tmp_path / "r", FILES)
    tb = tmp_path / "tb.txt"
    tb.write_text("nothing here\n")
    assert _run(root, tmp_path / "o", "nope") == 2
    assert _run(root, tmp_path / "o", "--trace", str(tb)) == 2
    assert "no frame inside the repo" in capsys.readouterr().err


def test_slice_needs_exactly_one_of_target_and_trace(tmp_path):
    root = write(tmp_path / "r", FILES)
    assert _run(root, tmp_path / "o") == 2
