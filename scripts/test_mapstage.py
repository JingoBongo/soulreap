"""Tests for rendering the *staged* map into the commit (precommit's first half)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import soulreap
from repo_fixtures import commit, git, repo, sha
from srlib import mapstage, util
from srlib.mapstage import stage_rendered_map

CARD = "# Save\n\n## Saving\n\n[[s.py::save]] saves.\n"
FILES = {"s.py": "def save():\n    pass\n", "soulreap/cards/save.md": CARD, "README.md": "hi\n"}


def _staged(root: Path, rel: str) -> str:
    return git(root, "show", f":{rel}")


def _stage(root: Path, rel: str, text: str) -> None:
    (root / rel).write_text(text)
    git(root, "add", rel)


def test_soulreap_commit_overrides_the_commit_label(tmp_path, monkeypatch):
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    monkeypatch.setenv("SOULREAP_COMMIT", "abc1234")
    assert util.commit_label(root) == "abc1234" and util.head_commit(root) == "abc1234"
    monkeypatch.delenv("SOULREAP_COMMIT")
    assert util.head_commit(root) == sha(root, "HEAD")[:7]


def test_a_staged_code_change_puts_the_rendered_map_in_the_index_and_on_disk(tmp_path):
    root = repo(tmp_path, FILES)
    _stage(root, "s.py", "def save():\n    return 1\n")
    lines = stage_rendered_map(root)
    head = sha(root, "HEAD")[:7]
    card = _staged(root, "soulreap/cards/save.md")
    assert f"<!-- basis@{head}:" in card
    assert json.loads(_staged(root, "soulreap/manifest.json"))["commit"] == head
    assert f"Commit `{head}`" in _staged(root, "soulreap/INDEX.md")
    assert (root / "soulreap/cards/save.md").read_text().rstrip("\n") == card
    assert any("map refreshed in this commit" in line for line in lines)
    assert git(root, "diff", "--name-only", "--", "soulreap") == ""  # disk == index


def test_partial_staging_maps_only_the_staged_code(tmp_path):
    root = repo(tmp_path, FILES)
    _stage(root, "s.py", "def save():\n    return 1\n")
    (root / "s.py").write_text("def save():\n    return 1\n\ndef only_on_disk():\n    pass\n")
    stage_rendered_map(root)
    facts = json.loads(_staged(root, "soulreap/.soulreap/facts.json"))["files"]
    assert [f["name"] for f in facts["s.py"]["functions"]] == ["save"]


def test_an_unstaged_edit_to_a_card_is_kept_on_disk(tmp_path):
    root = repo(tmp_path, FILES)
    _stage(root, "s.py", "def save():\n    return 1\n")
    mine = CARD + "\nA thought I am still writing.\n"
    (root / "soulreap/cards/save.md").write_text(mine)
    lines = stage_rendered_map(root)
    assert (root / "soulreap/cards/save.md").read_text() == mine
    assert "<!-- basis@" in _staged(root, "soulreap/cards/save.md")
    assert any("soulreap/cards/save.md" in line and "unstaged" in line for line in lines)


def test_a_map_the_index_does_not_track_is_never_added(tmp_path):
    root = repo(tmp_path, {"s.py": "def save():\n    pass\n"})
    (root / "soulreap/cards").mkdir(parents=True)
    (root / "soulreap/cards/save.md").write_text(CARD)
    _stage(root, "s.py", "def save():\n    return 1\n")
    assert stage_rendered_map(root) == []
    assert git(root, "ls-files", "soulreap") == ""


def test_a_commit_without_code_or_map_changes_skips_render(tmp_path, monkeypatch):
    root = repo(tmp_path, FILES)
    _stage(root, "README.md", "hello\n")
    monkeypatch.setattr(mapstage, "cmd_render", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rendered")))
    assert stage_rendered_map(root) == []


def test_a_render_failure_is_one_line_and_changes_nothing(tmp_path, monkeypatch):
    root = repo(tmp_path, FILES)
    _stage(root, "s.py", "def save():\n    return 1\n")

    def boom(*_a, **_k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(mapstage, "cmd_render", boom)
    lines = stage_rendered_map(root)
    assert lines == ["soulreap: map not refreshed (RuntimeError: kaput)"]
    assert _staged(root, "soulreap/cards/save.md") == CARD.rstrip("\n")


def test_precommit_renders_then_checks(tmp_path, capsys):
    root = repo(tmp_path, {**FILES, "app.py": "from s import save\n\ndef main():\n    save()\n"})
    _stage(root, "s.py", "def keep():\n    pass\n")
    assert soulreap.main(["precommit", "--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "map refreshed in this commit" in err and "removed-still-called" in err
    assert "broken anchor" in err  # the card cites s.py::save, which the staged code removed


def test_a_real_commit_carries_the_map_and_leaves_the_tree_clean(tmp_path):
    root = repo(tmp_path, FILES)
    soulreap.main(["install", "--root", str(root), "--hook-only"])
    (root / "s.py").write_text("def save():\n    return 1\n")
    commit(root, {}, "change")
    assert "<!-- basis@" in git(root, "show", "HEAD:soulreap/cards/save.md")
    assert git(root, "status", "--porcelain") == ""
