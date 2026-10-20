"""Tests for `precommit`: what the git hook runs on the staged change."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import soulreap
from repo_fixtures import git, repo
from srlib import precommit

SAVE = {"s.py": "def save():\n    pass\n", "app.py": "from s import save\n\ndef main():\n    save()\n"}


def _run(root):
    return soulreap.main(["precommit", "--root", str(root)])


def _stage(root, rel, text):
    (root / rel).write_text(text)
    git(root, "add", rel)


def test_high_finding_blocks_and_says_how_to_bypass(tmp_path, capsys):
    root = repo(tmp_path, SAVE)
    _stage(root, "s.py", "def keep():\n    pass\n")
    assert _run(root) == 1
    err = capsys.readouterr().err
    assert "removed-still-called" in err and "commit blocked: 1 HIGH finding(s)" in err
    assert "SOULREAP_SKIP=1" in err


def test_med_only_passes_and_lists_the_finding(tmp_path, capsys):
    soft = "import subprocess\n\ndef sync():\n    subprocess.run(['git', 'reset', '--soft'])\n"
    root = repo(tmp_path, {"g.py": soft})
    _stage(root, "g.py", "import subprocess\n\ndef sync():\n    subprocess.run(['git', 'reset', '--hard'])\n")
    assert _run(root) == 0
    err = capsys.readouterr().err
    assert "MED" in err and "literal-changed" in err and "blocked" not in err


def test_clean_commit_is_silent(tmp_path, capsys):
    root = repo(tmp_path, SAVE)
    _stage(root, "s.py", "def save():\n    return 1\n")
    assert _run(root) == 0
    assert capsys.readouterr().err == ""


def test_skip_env_does_no_work(tmp_path, capsys, monkeypatch):
    root = repo(tmp_path, SAVE)
    _stage(root, "s.py", "def keep():\n    pass\n")
    monkeypatch.setenv("SOULREAP_SKIP", "1")
    assert _run(root) == 0
    assert not (root / ".git" / "soulreap").exists()


def test_cache_lives_in_git_dir_never_in_the_tree(tmp_path, monkeypatch):
    monkeypatch.delenv("SOULREAP_CACHE", raising=False)
    root = repo(tmp_path, SAVE)
    _stage(root, "s.py", "def save():\n    return 1\n")
    _run(root)
    assert (root / ".git" / "soulreap" / "facts.json").exists()
    assert not (root / "soulreap").exists()
    assert "SOULREAP_CACHE" not in __import__("os").environ


def test_an_unclean_map_prints_one_line_and_never_blocks(tmp_path, capsys):
    root = repo(tmp_path, SAVE)
    cards = root / "soulreap" / "cards"
    cards.mkdir(parents=True)
    (cards / "save.md").write_text("# Save\n\n## Saving\n\n[[s.py::gone]] used to save.\n")  # a broken anchor
    _stage(root, "s.py", "def save():\n    return 1\n")
    assert _run(root) == 0
    assert "1 broken anchor(s)" in capsys.readouterr().err


def test_an_internal_error_never_blocks(tmp_path, capsys, monkeypatch):
    root = repo(tmp_path, SAVE)

    def boom(*_args, **_kwargs):
        raise RuntimeError("kaput")

    monkeypatch.setattr(precommit, "run_delta", boom)
    assert _run(root) == 0
    assert "soulreap: check failed (RuntimeError: kaput)" in capsys.readouterr().err
