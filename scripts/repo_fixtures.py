"""Small fixture repos for the delta/slice tests: files on disk, facts, and real git history."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib.build import build_facts


def write(root: Path, files: dict[str, str]) -> Path:
    """Write *files* under *root* and return it."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def tree(root: Path, files: dict[str, str]) -> tuple[Path, dict[str, dict]]:
    """Write *files* and return (root, facts)."""
    write(root, files)
    return root, build_facts(root).files


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def commit(root: Path, files: dict[str, str], message: str = "c") -> None:
    write(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)


def repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """A git repo under *tmp_path*/repo with *files* committed once."""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    commit(root, files)
    return root


def sha(root: Path, ref: str) -> str:
    return git(root, "rev-parse", ref)
