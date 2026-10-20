"""Tests for the v10 skeleton: whole callable docstrings and hazards are printed."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib.skeleton import cmd_skeleton


def test_skeleton_prints_the_whole_callable_docstring(tmp_path):
    (tmp_path / "ui.py").write_text(
        'def save(body):\n    """Write the config.\n\n    Keys outside EDITABLE are silently dropped.\n    """\n'
        "    return body\n",
        encoding="utf-8",
    )
    text = cmd_skeleton(tmp_path, tmp_path / "soulreap", "ui.py")
    assert "Keys outside EDITABLE are silently dropped." in text


def test_skeleton_prints_command_and_regex_literals(tmp_path):
    (tmp_path / "g.py").write_text(
        "import re\n\ndef sync(p):\n    run(f'git -C {p} fetch --prune')\n    return re.match(r'v(\\d+)', p)\n",
        encoding="utf-8",
    )
    text = cmd_skeleton(tmp_path, tmp_path / "soulreap", "g.py")
    assert "command: git -C {p} fetch --prune" in text and "regex: v(\\d+)" in text
