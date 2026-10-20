"""Tests for the `delta` command: revisions, exit codes, output."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import soulreap
from repo_fixtures import commit, git, repo, sha

SAVE = {"s.py": "def save():\n    pass\n", "app.py": "from s import save\n\ndef main():\n    save()\n"}


def _run(root, out, *extra):
    return soulreap.main(["delta", "--root", str(root), "--out", str(out), *extra])


def test_uncommitted_removal_still_called_exits_1(tmp_path, capsys):
    root = repo(tmp_path, SAVE)
    (root / "s.py").write_text("def keep():\n    pass\n")
    assert _run(root, tmp_path / "o") == 1
    text = capsys.readouterr().out
    assert "HIGH" in text and "removed-still-called" in text
    assert "s.py::save" in text and "app.py::main" in text


def test_clean_tree_exits_0_and_says_nothing_changed(tmp_path, capsys):
    root = repo(tmp_path, SAVE)
    assert _run(root, tmp_path / "o") == 0
    assert "no symbol changes" in capsys.readouterr().out


def test_low_only_change_exits_0_and_lists_tests(tmp_path, capsys):
    root = repo(tmp_path, {**SAVE, "tests/test_s.py": "from s import save\n\ndef test_save():\n    save()\n"})
    (root / "s.py").write_text("def save():\n    return 1\n")
    assert _run(root, tmp_path / "o") == 0
    text = capsys.readouterr().out
    assert "LOW" in text and "tests/test_s.py" in text


def test_two_commits_ignore_the_worktree(tmp_path, capsys):
    root = repo(tmp_path, {"s.py": "def save():\n    pass\n"})
    first = sha(root, "HEAD")
    commit(root, {"s.py": "def save():\n    return 1\n"})
    (root / "s.py").write_text("def other():\n    pass\n")
    _run(root, tmp_path / "o", "--base", first, "--head", "HEAD", "--json")
    report = json.loads(capsys.readouterr().out)
    assert [(c["key"], c["status"]) for c in report["changes"]] == [("s.py::save", "modified")]
    assert report["base"] == first and report["head"] == sha(root, "HEAD")


def test_untracked_file_counts_as_added(tmp_path, capsys):
    root = repo(tmp_path, SAVE)
    (root / "new.py").write_text("def fresh():\n    pass\n")
    _run(root, tmp_path / "o", "--json")
    report = json.loads(capsys.readouterr().out)
    assert report["head"] == "worktree"
    assert [(c["key"], c["status"]) for c in report["changes"]] == [("new.py::fresh", "added")]


def test_bad_ref_exits_2(tmp_path, capsys):
    root = repo(tmp_path, {"s.py": "x = 1\n"})
    assert _run(root, tmp_path / "o", "--base", "nope") == 2
    assert "unknown revision: nope" in capsys.readouterr().err


def test_citing_map_sections_are_listed(tmp_path, capsys):
    root = repo(tmp_path, {"s.py": "def save():\n    pass\n"})
    out = tmp_path / "o"
    (out / "cards").mkdir(parents=True)
    (out / "cards" / "save.md").write_text("# Save\n\n## Writes\n\n[[s.py::save]] saves.\n")
    (root / "s.py").write_text("def save():\n    return 1\n")
    _run(root, out)
    assert "cards/save.md#Writes" in capsys.readouterr().out


def test_staged_checks_only_the_index(tmp_path, capsys):
    root = repo(tmp_path, {**SAVE, "u.py": "def util():\n    pass\n"})
    (root / "s.py").write_text("def keep():\n    pass\n")
    git(root, "add", "s.py")
    (root / "s.py").write_text("def save():\n    pass\n\ndef keep():\n    pass\n")  # unstaged fix
    (root / "u.py").write_text("def other():\n    pass\n")  # unstaged, not part of the commit
    (root / "new.py").write_text("def fresh():\n    pass\n")  # untracked
    assert _run(root, tmp_path / "o", "--staged", "--json") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["head"] == "index"
    assert [(c["key"], c["status"]) for c in report["changes"]] == [("s.py::keep", "added"), ("s.py::save", "removed")]


def test_staged_with_only_unstaged_edits_reports_nothing(tmp_path, capsys):
    root = repo(tmp_path, SAVE)
    (root / "s.py").write_text("def keep():\n    pass\n")
    assert _run(root, tmp_path / "o", "--staged") == 0
    assert "no symbol changes" in capsys.readouterr().out


def test_staged_and_head_together_exit_2(tmp_path):
    root = repo(tmp_path, SAVE)
    assert _run(root, tmp_path / "o", "--staged", "--head", "HEAD") == 2


def test_staged_in_a_repo_without_commits_adds_everything(tmp_path, capsys):
    root = tmp_path / "fresh"
    root.mkdir()
    git(root, "init", "-q")
    (root / "a.py").write_text("def f():\n    pass\n")
    git(root, "add", "a.py")
    _run(root, tmp_path / "o", "--staged", "--json")
    report = json.loads(capsys.readouterr().out)
    assert [(c["key"], c["status"]) for c in report["changes"]] == [("a.py::f", "added")]
