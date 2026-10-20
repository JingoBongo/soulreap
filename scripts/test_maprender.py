"""Tests for the v10 render: scaffold, anchors, staleness end-to-end, checklist, INDEX."""

from __future__ import annotations

import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib import mapplan as mp
from srlib import maprender as mr

APP = (
    "from store import save\n\n\ndef main():\n"
    "    try:\n        save(1)\n    except Exception:\n        pass\n"
)
STORE = "CACHE = {}\n\n\ndef save(v):\n    CACHE[v] = 1\n"
CARD = "# Main loop\n\n## Flow\n\n[[app.py::main]] calls [[store.py::save]].\n"


def _mapped(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text(APP, encoding="utf-8")
    (root / "store.py").write_text(STORE, encoding="utf-8")
    out = root / "soulreap"
    mp.cmd_plan(root, out)
    (out / "cards").mkdir(parents=True)
    (out / "cards" / "main-loop.md").write_text(CARD, encoding="utf-8")
    return root, out


def test_render_scaffolds_overview_and_rewrites_anchors(tmp_path):
    root, out = _mapped(tmp_path)
    stats = mr.cmd_render(root, out)
    assert "<!-- soulreap:coverage -->" in (out / "OVERVIEW.md").read_text(encoding="utf-8")
    card = (out / "cards" / "main-loop.md").read_text(encoding="utf-8")
    assert '#L4-L8 "app.py::main")' in card and "<!-- basis@worktree:" in card
    assert stats["checklist_missing"] == [] and stats["strict"] is False


def test_render_callee_change_stales_section_and_comment_edit_does_not(tmp_path):
    root, out = _mapped(tmp_path)
    mr.cmd_render(root, out)
    (root / "store.py").write_text(STORE.replace("    CACHE[v] = 1\n", "    # cache it\n    CACHE[v] = 1\n"),
                                   encoding="utf-8")
    assert mr.cmd_render(root, out)["stale"] == []
    (root / "store.py").write_text(STORE + "    print(v)\n", encoding="utf-8")
    stats = mr.cmd_render(root, out)
    assert [(s["doc"], s["section"]) for s in stats["stale"]] == [("cards/main-loop.md", "Flow")]
    card = (out / "cards" / "main-loop.md").read_text(encoding="utf-8")
    assert "> ⚠ stale (basis worktree): save: +callee print" in card


def test_render_confirm_clears_the_banner(tmp_path):
    root, out = _mapped(tmp_path)
    mr.cmd_render(root, out)
    (root / "store.py").write_text(STORE + "    print(v)\n", encoding="utf-8")
    mr.cmd_render(root, out)
    assert mp.cmd_confirm(root, out, "cards/main-loop.md", "Flow") is True
    assert mr.cmd_render(root, out)["stale"] == []


def test_render_uncited_checklist_item_is_strict(tmp_path):
    root, out = _mapped(tmp_path)
    (out / "cards" / "main-loop.md").write_text("# Main\n\n## Flow\n\n[[app.py::main]] only.\n", encoding="utf-8")
    stats = mr.cmd_render(root, out)
    assert [i["symbol"] for i in stats["checklist_missing"]] == ["store.py::save"] and stats["strict"] is True


def test_render_unresolved_anchor_is_strict(tmp_path):
    root, out = _mapped(tmp_path)
    (out / "cards" / "main-loop.md").write_text(CARD + "\nAlso [[gone.py::f]].\n", encoding="utf-8")
    stats = mr.cmd_render(root, out)
    assert stats["unresolved"] == ["gone.py::f"] and stats["strict"] is True


def test_render_index_lists_cards_and_files(tmp_path):
    root, out = _mapped(tmp_path)
    mr.cmd_render(root, out)
    index = (out / "INDEX.md").read_text(encoding="utf-8")
    assert "| [main-loop](cards/main-loop.md) |" in index and "| app.py | python |" in index


def test_render_legacy_overview_without_markers_is_reported_not_rewritten(tmp_path):
    root, out = _mapped(tmp_path)
    (out / "OVERVIEW.md").write_text("# Old\n\nHand-written.\n", encoding="utf-8")
    (out / "units").mkdir()
    stats = mr.cmd_render(root, out)
    assert (out / "OVERVIEW.md").read_text(encoding="utf-8") == "# Old\n\nHand-written.\n"
    assert stats["overview_missing"] and {"units", "OVERVIEW.md"} <= set(stats["legacy"])


def test_render_marks_documents_with_map_version_and_reports_older(tmp_path):
    root, out = _mapped(tmp_path)
    mr.cmd_render(root, out)
    assert "<!-- soulreap map v10 -->" in (out / "cards" / "main-loop.md").read_text(encoding="utf-8")
    (out / "cards" / "old.md").write_text("# Old\n\n[[app.py::main]]\n\n<!-- soulreap map v9 -->\n", encoding="utf-8")
    assert mr.cmd_render(root, out)["old_contract"] == [{"doc": "cards/old.md", "version": 9}]


def test_render_scaffold_leaves_a_prose_slot_under_not_covered(tmp_path):
    root, out = _mapped(tmp_path)
    mr.cmd_render(root, out)
    tail = (out / "OVERVIEW.md").read_text(encoding="utf-8").split("## Not covered", 1)[1]
    assert "<!-- /soulreap:coverage -->" in tail
    assert "_Write this by hand" in tail.split("<!-- /soulreap:coverage -->")[1]


def test_render_stale_banner_in_overview_survives_the_block_rewrite(tmp_path):
    root, out = _mapped(tmp_path)
    mr.cmd_render(root, out)
    ov = (out / "OVERVIEW.md").read_text(encoding="utf-8")
    ov = ov.replace("## Where things are\n", "## Where things are\n\nStorage is [[store.py::save]].\n", 1)
    (out / "OVERVIEW.md").write_text(ov, encoding="utf-8")
    mr.cmd_render(root, out)
    (root / "store.py").write_text(STORE + "    print(v)\n", encoding="utf-8")
    stats = mr.cmd_render(root, out)
    assert ("OVERVIEW.md", "Where things are") in [(s["doc"], s["section"]) for s in stats["stale"]]
    assert "> ⚠ stale" in (out / "OVERVIEW.md").read_text(encoding="utf-8").split("## Where things are")[1]


V1_OVERVIEW = ("# App\n\n<!-- soulreap:stamp -->\nold\n<!-- /soulreap:stamp -->\n\n## Architecture\n\nhand\n\n"
               "<!-- soulreap:coverage -->\nunits\n<!-- /soulreap:coverage -->\n")


def test_render_v9_map_with_markers_is_left_alone_and_reported(tmp_path):
    root, out = _mapped(tmp_path)
    (out / "OVERVIEW.md").write_text(V1_OVERVIEW, encoding="utf-8")
    (out / "INDEX.md").write_text("# Index — 2 files\n\n| File | Lang | Lines | Owner | Role |\n", encoding="utf-8")
    (out / "manifest.json").write_text('{"template_version": 9, "files": {}}\n', encoding="utf-8")
    (out / "units").mkdir()
    stats = mr.cmd_render(root, out)
    assert (out / "OVERVIEW.md").read_text(encoding="utf-8") == V1_OVERVIEW
    assert "| Owner |" in (out / "INDEX.md").read_text(encoding="utf-8")
    assert '"template_version": 9' in (out / "manifest.json").read_text(encoding="utf-8")
    assert {"units", "OVERVIEW.md", "INDEX.md", "manifest.json"} <= set(stats["legacy"])


def test_status_reports_staleness_without_writing_anything(tmp_path):
    root, out = _mapped(tmp_path)
    mr.cmd_render(root, out)
    (root / "store.py").write_text(STORE + "    print(v)\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    status = mr.cmd_status(root, out)
    assert [(s["doc"], s["section"]) for s in status["stale"]] == [("cards/main-loop.md", "Flow")]
    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == before


def test_render_twice_on_a_fresh_scaffold_reports_no_unresolved_placeholder(tmp_path):
    root, out = _mapped(tmp_path)
    mr.cmd_render(root, out)
    stats = mr.cmd_render(root, out)
    assert stats["unresolved"] == [] and "basis@worktree:  -->" not in (out / "OVERVIEW.md").read_text(encoding="utf-8")


def test_render_restores_a_generated_block_the_reader_dropped(tmp_path):
    root, out = _mapped(tmp_path)
    mr.cmd_render(root, out)
    text = (out / "OVERVIEW.md").read_text(encoding="utf-8")
    start, end = text.index("<!-- soulreap:inventory -->"), text.index("<!-- /soulreap:inventory -->")
    (out / "OVERVIEW.md").write_text(text[:start] + text[end + len("<!-- /soulreap:inventory -->"):], encoding="utf-8")
    stats = mr.cmd_render(root, out)
    restored = (out / "OVERVIEW.md").read_text(encoding="utf-8")
    assert stats["overview_missing"] == [] and stats["overview_restored"] == ["inventory"]
    section = restored.split("## Where things are")[1].split("\n## ")[0]
    assert "<!-- soulreap:inventory -->" in section


def test_render_turns_a_backticked_anchor_to_a_real_symbol_into_a_citation(tmp_path):
    root, out = _mapped(tmp_path)
    card = out / "cards" / "main-loop.md"
    card.write_text(card.read_text(encoding="utf-8") + "\n## Store\n\nSaved by `[[store.py::save]]`, "
                    "cite as `[[file::Symbol]]`.\n\n```\n`[[app.py::main]]`\n```\n", encoding="utf-8")
    mr.cmd_render(root, out)
    text = card.read_text(encoding="utf-8")
    assert 'Saved by [save](../../store.py#L' in text or 'Saved by [store.save](../../store.py#L' in text
    assert "`[[file::Symbol]]`" in text and "`[[app.py::main]]`" in text  # examples and fences untouched


ARCHITECTURE = ('digraph architecture {\n  rankdir=LR;\n  app [label="app.py\\nmain loop"];\n'
                '  db [label="state.db", shape=cylinder];\n  app -> db [label="save()"];\n}\n')


def test_default_render_draws_the_runtime_diagram_the_reader_wrote_and_pastes_it_first(tmp_path):
    pytest.importorskip("pygraphviz")
    root, out = _mapped(tmp_path)
    (out / "diagrams").mkdir(exist_ok=True)
    (out / "diagrams" / "architecture.dot").write_text(ARCHITECTURE, encoding="utf-8")
    mr.cmd_render(root, out)
    assert "state.db" in (out / "diagrams" / "architecture.svg").read_text(encoding="utf-8")
    block = (out / "OVERVIEW.md").read_text(encoding="utf-8").split("<!-- soulreap:diagrams -->", 1)[1]
    assert block.index("architecture.svg") < block.index("context.svg") if "context.svg" in block else True
    assert "architecture.svg" in block


def test_a_runtime_diagram_that_does_not_parse_is_reported_not_fatal(tmp_path):
    pytest.importorskip("pygraphviz")
    root, out = _mapped(tmp_path)
    (out / "diagrams").mkdir(exist_ok=True)
    (out / "diagrams" / "architecture.dot").write_text("digraph { a -> ", encoding="utf-8")
    stats = mr.cmd_render(root, out)
    assert any("architecture.dot" in w for w in stats["diagram_warnings"])
    assert not (out / "diagrams" / "architecture.svg").exists()


def test_dot_source_pasted_into_a_document_is_reported(tmp_path):
    root, out = _mapped(tmp_path)
    mr.cmd_render(root, out)
    overview = out / "OVERVIEW.md"
    overview.write_text(overview.read_text(encoding="utf-8")
                        + "\n## Architecture\n\n```dot\ndigraph architecture {\n  a -> b;\n}\n```\n", encoding="utf-8")
    stats = mr.cmd_render(root, out)
    assert any("OVERVIEW.md" in w and "diagrams/architecture.dot" in w for w in stats["diagram_warnings"])
