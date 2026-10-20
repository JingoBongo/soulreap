"""The Claude copy of the skill must be reproducible from the Pi copy, and drift must be caught."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "sync-to-claude.sh"


def _run(pi: Path, claude: Path, *args: str) -> int:
    env = {**os.environ, "PI_DIR": str(pi), "CLAUDE_DIR": str(claude)}
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, check=False).returncode


def test_sync_then_check_is_clean_and_drift_is_detected(tmp_path):
    pi = tmp_path / "pi"
    shutil.copytree(SKILL, pi, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache"))
    claude = tmp_path / "claude"
    assert _run(pi, claude) == 0
    assert "~/.claude/skills/soulreap/" in (claude / "SKILL.md").read_text(encoding="utf-8")
    assert "~/.pi/agent/skills/soulreap/" not in (claude / "SKILL.md").read_text(encoding="utf-8")
    assert _run(pi, claude, "--check") == 0
    (pi / "references" / "card.md").write_text("changed\n", encoding="utf-8")
    assert _run(pi, claude, "--check") == 1
