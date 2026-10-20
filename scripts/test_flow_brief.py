"""`flow --brief`: the spine of a flow card — one anchor per call, the entry's own steps always."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib.querycmd import cmd_flow

WIDE = (
    "".join(f"def b{i}_{j}():\n    return 0\n\n\n" for i in range(8) for j in range(8))
    + "".join(f"def a{i}():\n" + "".join(f"    b{i}_{j}()\n" for j in range(8)) + "\n\n" for i in range(8))
    + "def main():\n" + "".join(f"    a{i}()\n" for i in range(8))
)
LOOP = (
    "def get(k):\n    return k\n\n\ndef setup():\n    get(1)\n\n\ndef tick():\n    get(2)\n\n\n"
    "def main():\n    setup()\n    get(3)\n    while True:\n        tick()\n"
)


def _brief(tmp_path: Path, src: str, capsys) -> list[str]:
    (tmp_path / "app.py").write_text(src, encoding="utf-8")
    cmd_flow(root=tmp_path, out=tmp_path / "soulreap", target="app.py::main", brief=True)
    return capsys.readouterr().out.rstrip("\n").split("\n")


def test_brief_keeps_every_step_of_the_entry_and_collapses_below(tmp_path, capsys):
    lines = _brief(tmp_path, WIDE, capsys)
    assert all(any(f"[[app.py::a{i}]]" in line for line in lines) for i in range(8))
    assert len(lines) <= 42  # header, blank line, at most 40 calls
    assert any("+8 below" in line for line in lines)


def test_brief_is_a_pasteable_list_of_anchors(tmp_path, capsys):
    lines = _brief(tmp_path, LOOP, capsys)
    assert not any(line.startswith("#") for line in lines)  # pasted into a card, no new section
    assert not any("[L" in line or "→" in line for line in lines)  # spans are render's job
    assert lines[1] == ""  # a blank line, so every markdown viewer starts a list
    assert all(line.lstrip().startswith("- ") for line in lines[2:])


def test_brief_marks_calls_inside_a_loop_and_shows_each_target_once(tmp_path, capsys):
    lines = _brief(tmp_path, LOOP, capsys)
    assert any("↻" in line and "[[app.py::tick]]" in line for line in lines)
    assert not any("↻" in line and "[[app.py::setup]]" in line for line in lines)
    assert sum("[[app.py::get]]" in line for line in lines) == 1


def test_brief_marks_a_call_made_before_and_inside_the_loop(tmp_path, capsys):
    src = "def load():\n    return 1\n\n\ndef main():\n    load()\n    while True:\n        load()\n"
    lines = _brief(tmp_path, src, capsys)
    assert any("↻" in line and "[[app.py::load]]" in line for line in lines)


DOCS = (
    "def _b(v):\n    return v == 'yes'\n\n\n"
    "def load():\n    \"\"\"Read the settings\n    file. Missing keys default.\"\"\"\n    try:\n        return _b(open('x').read())\n"
    "    except OSError:\n        return False\n\n\n"
    "def main():\n    load()\n"
)


def test_brief_glosses_a_step_with_its_docstring_and_folds_a_tiny_helper(tmp_path, capsys):
    (tmp_path / "app.py").write_text(DOCS + "\n\ndef top():\n    main()\n", encoding="utf-8")
    cmd_flow(root=tmp_path, out=tmp_path / "soulreap", target="app.py::top", brief=True)
    lines = capsys.readouterr().out.rstrip("\n").split("\n")
    load = next(line for line in lines if "[[app.py::load]]" in line)
    assert load.endswith("— Read the settings file. · +1 helper")
    assert not any("[[app.py::_b]]" in line for line in lines)


def test_brief_legend_is_one_short_line(tmp_path, capsys):
    lines = _brief(tmp_path, LOOP, capsys)
    assert lines[0] == "Calls from [[app.py::main]] — ↻ in a loop · ? matched by name only · +N more below:"


def test_brief_follows_typescript_calls_across_files(tmp_path, capsys):
    (tmp_path / "cfg.ts").write_text("export function load(): string {\n  return read();\n}\n\n"
                                     "function read(): string {\n  const a = 1;\n  const b = 2;\n  const c = 3;\n  const d = 4;\n  return 'x';\n}\n", encoding="utf-8")
    (tmp_path / "main.ts").write_text("import { load } from './cfg';\n\nexport const main = () => {\n"
                                      "  const v = load();\n  return v;\n};\n", encoding="utf-8")
    cmd_flow(root=tmp_path, out=tmp_path / "soulreap", target="main.ts::main", brief=True)
    out = capsys.readouterr().out
    assert "[[cfg.ts::load]]" in out and "[[cfg.ts::read]]" in out


def test_callers_finds_a_typescript_caller(tmp_path):
    from srlib.build import build_facts
    from srlib.symbols import callers
    (tmp_path / "cfg.ts").write_text("export function load() {\n  return 1;\n}\n", encoding="utf-8")
    (tmp_path / "main.ts").write_text("import { load } from './cfg';\n\nexport function main() {\n  load();\n}\n",
                                      encoding="utf-8")
    found = callers(tmp_path, build_facts(tmp_path).files, "cfg.ts::load")
    assert [c["caller"] for c in found] == ["main.ts::main"]


def test_typescript_logic_calls_come_before_rendered_components(tmp_path, capsys):
    (tmp_path / "ui.tsx").write_text("export function Badge() {\n  return null;\n}\n", encoding="utf-8")
    (tmp_path / "list.tsx").write_text(
        "import { Badge } from './ui';\n\nfunction groupBy(xs: number[]) {\n  const a = 1;\n  const b = 2;\n"
        "  const c = 3;\n  return xs;\n}\n\nexport const List = () => {\n  groupBy([]);\n  return <Badge />;\n};\n",
        encoding="utf-8")
    cmd_flow(root=tmp_path, out=tmp_path / "soulreap", target="list.tsx::List", brief=True)
    steps = [line for line in capsys.readouterr().out.split("\n") if line.startswith("- ")]
    assert steps[0].startswith("- [[list.tsx::groupBy]]") and "[[ui.tsx::Badge]]" in steps[1]


def test_a_gloss_is_not_cut_at_an_abbreviation():
    from srlib.flowtext import _gloss
    assert _gloss("Return x, e.g. the first one. More.") == "Return x, e.g. the first one."


def test_a_long_gloss_is_cut_at_a_word_and_ends_once():
    from srlib.flowtext import _gloss
    text = _gloss("Trigger updates for repos that just transitioned to update-available, then wait for them all.")
    assert text.endswith("…") and not text.endswith(".…") and "….'" not in text and len(text) <= 70
    assert not text[:-1].endswith(" ")
