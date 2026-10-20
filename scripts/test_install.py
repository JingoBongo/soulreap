"""Tests for `install`: the pre-commit hook and the AGENTS.md block, owned whole and idempotent."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import soulreap
from repo_fixtures import git, repo
from srlib.install import AGENTS_CLOSE, AGENTS_OPEN, HOOK_MARK, cmd_install
from srlib.mapplan import HOOK_MARK as OLD_MARK

SCRIPT = Path(__file__).parent / "soulreap.py"
SAVE = {"s.py": "def save():\n    pass\n", "app.py": "from s import save\n\ndef main():\n    save()\n"}


def _hook(root: Path, name: str = "pre-commit") -> Path:
    return root / ".git" / "hooks" / name


def test_fresh_install_writes_an_executable_hook_and_the_agents_block(tmp_path):
    root = repo(tmp_path, SAVE)
    code, lines = cmd_install(root, SCRIPT)
    hook = _hook(root)
    assert code == 0 and hook.stat().st_mode & 0o111
    text = hook.read_text()
    assert HOOK_MARK in text and " precommit " in text and str(SCRIPT) in text and "SOULREAP_SKIP" in text
    agents = (root / "AGENTS.md").read_text()
    assert agents.startswith(AGENTS_OPEN) and AGENTS_CLOSE in agents and "soulreap callers" in agents
    assert any(line.startswith("pre-commit: installed") for line in lines)


def test_rerun_changes_nothing(tmp_path):
    root = repo(tmp_path, SAVE)
    cmd_install(root, SCRIPT)
    before = (_hook(root).read_bytes(), (root / "AGENTS.md").read_bytes())
    code, lines = cmd_install(root, SCRIPT)
    assert code == 0 and (_hook(root).read_bytes(), (root / "AGENTS.md").read_bytes()) == before
    assert "pre-commit: current" in lines and "AGENTS.md: current" in lines


def test_a_foreign_hook_is_left_alone_and_the_line_to_add_is_printed(tmp_path):
    root = repo(tmp_path, SAVE)
    _hook(root).parent.mkdir(parents=True, exist_ok=True)
    _hook(root).write_text("#!/bin/sh\necho mine\n")
    code, lines = cmd_install(root, SCRIPT)
    assert code == 1 and _hook(root).read_text() == "#!/bin/sh\necho mine\n"
    foreign = next(line for line in lines if line.startswith("pre-commit: foreign"))
    assert f'"{SCRIPT}" precommit' in foreign
    assert (root / "AGENTS.md").exists()


def test_core_hookspath_is_followed(tmp_path):
    root = repo(tmp_path, SAVE)
    git(root, "config", "core.hooksPath", ".githooks")
    cmd_install(root, SCRIPT)
    assert (root / ".githooks" / "pre-commit").exists() and not _hook(root).exists()


def test_the_old_post_commit_hook_is_removed_but_a_foreign_one_kept(tmp_path):
    root = repo(tmp_path, SAVE)
    _hook(root).parent.mkdir(parents=True, exist_ok=True)
    _hook(root, "post-commit").write_text(f"#!/bin/sh\n{OLD_MARK}: old\n")
    _, lines = cmd_install(root, SCRIPT)
    assert not _hook(root, "post-commit").exists() and "post-commit (old): removed" in lines
    _hook(root, "post-commit").write_text("#!/bin/sh\necho mine\n")
    cmd_install(root, SCRIPT)
    assert _hook(root, "post-commit").exists()


def test_existing_agents_prose_is_kept_and_an_outdated_block_replaced_in_place(tmp_path):
    root = repo(tmp_path, SAVE)
    (root / "AGENTS.md").write_text(f"# Rules\n\nBe nice.\n\n{AGENTS_OPEN}\nold\n{AGENTS_CLOSE}\n\n## More\n\nStay.\n")
    code, lines = cmd_install(root, SCRIPT)
    text = (root / "AGENTS.md").read_text()
    assert code == 0 and "AGENTS.md: updated" in lines
    assert text.startswith("# Rules\n\nBe nice.\n\n" + AGENTS_OPEN) and text.endswith("## More\n\nStay.\n")
    assert "\nold\n" not in text and text.count(AGENTS_OPEN) == 1


def test_a_block_is_appended_after_prose(tmp_path):
    root = repo(tmp_path, SAVE)
    (root / "AGENTS.md").write_text("# Rules\n\nBe nice.\n")
    cmd_install(root, SCRIPT)
    assert (root / "AGENTS.md").read_text().startswith("# Rules\n\nBe nice.\n\n" + AGENTS_OPEN)


def test_check_reports_missing_then_clean(tmp_path):
    root = repo(tmp_path, SAVE)
    code, lines = cmd_install(root, SCRIPT, check=True)
    assert code == 1 and "pre-commit: missing" in lines and "AGENTS.md: missing" in lines
    assert not _hook(root).exists()
    cmd_install(root, SCRIPT)
    assert cmd_install(root, SCRIPT, check=True)[0] == 0


def test_uninstall_removes_only_what_it_owns(tmp_path):
    root = repo(tmp_path, SAVE)
    (root / "AGENTS.md").write_text("# Rules\n\nBe nice.\n")
    cmd_install(root, SCRIPT)
    code, _ = cmd_install(root, SCRIPT, uninstall=True)
    assert code == 0 and not _hook(root).exists()
    assert (root / "AGENTS.md").read_text() == "# Rules\n\nBe nice.\n"


def test_uninstall_deletes_an_agents_file_that_held_only_the_block(tmp_path):
    root = repo(tmp_path, SAVE)
    cmd_install(root, SCRIPT)
    cmd_install(root, SCRIPT, uninstall=True)
    assert not (root / "AGENTS.md").exists()


def test_hook_only_and_the_hook_alias_skip_agents_md(tmp_path, capsys):
    root = repo(tmp_path, SAVE)
    assert soulreap.main(["hook", "--root", str(root)]) == 0
    assert _hook(root).exists() and not (root / "AGENTS.md").exists()
    assert "pre-commit: installed" in capsys.readouterr().out


def test_not_a_repo_exits_2(tmp_path):
    assert soulreap.main(["install", "--root", str(tmp_path)]) == 2


@pytest.mark.skipif(shutil.which("uv") is None, reason="the hook runs soulreap through uv")
def test_a_real_commit_is_blocked_then_bypassed(tmp_path):
    root = repo(tmp_path, SAVE)
    cmd_install(root, SCRIPT)
    (root / "s.py").write_text("def keep():\n    pass\n")
    git(root, "add", "s.py")
    blocked = subprocess.run(["git", "-C", str(root), "commit", "-qm", "x"], capture_output=True, text=True)
    assert blocked.returncode != 0 and "removed-still-called" in blocked.stderr
    env = {**os.environ, "SOULREAP_SKIP": "1"}
    assert subprocess.run(["git", "-C", str(root), "commit", "-qm", "x"], env=env).returncode == 0
