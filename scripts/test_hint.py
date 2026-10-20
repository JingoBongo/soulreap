"""Tests for hint.py: a one-line pointer to soulreap when an agent greps for a symbol."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from repo_fixtures import repo

HINT = Path(__file__).parent / "hint.py"
SAVE = {"s.py": "def save():\n    pass\n"}


def _hint(tmp_path, mode: str, payload: dict | str) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    result = subprocess.run(["python3", str(HINT), mode], input=body, capture_output=True, text=True,
                            env={"XDG_RUNTIME_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _claude(root: Path, tool: str, tool_input: dict, session: str = "s1") -> dict:
    return {"session_id": session, "cwd": str(root), "hook_event_name": "PreToolUse",
            "tool_name": tool, "tool_input": tool_input}


def _installed(tmp_path) -> Path:
    root = repo(tmp_path, SAVE)
    (root / "AGENTS.md").write_text("<!-- soulreap:agents -->\nx\n<!-- /soulreap:agents -->\n")
    return root


def test_grep_for_a_call_gets_claude_context(tmp_path):
    root = _installed(tmp_path)
    out = json.loads(_hint(tmp_path, "--claude", _claude(root, "Grep", {"pattern": r"save\("})))
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    text = out["hookSpecificOutput"]["additionalContext"]
    assert '"soulreap callers save"' in text and '"soulreap slice save"' in text and "permissionDecision" not in out
    assert "hookSpecificOutput" in out and "decision" not in out


def test_bash_rg_for_a_definition_is_hinted(tmp_path):
    root = _installed(tmp_path)
    out = _hint(tmp_path, "--claude", _claude(root, "Bash", {"command": 'cd src && rg -n "def save" .'}))
    assert "soulreap callers save" in out


def test_a_log_string_is_not_symbol_shaped(tmp_path):
    root = _installed(tmp_path)
    cmd = 'grep -rn "connection refused" logs'
    assert _hint(tmp_path, "--claude", _claude(root, "Bash", {"command": cmd})) == ""
    assert _hint(tmp_path, "--claude", _claude(root, "Grep", {"pattern": "TODO|FIXME"})) == ""
    assert _hint(tmp_path, "--claude", _claude(root, "Bash", {"command": "ls -la"})) == ""


def test_a_repo_without_soulreap_stays_silent(tmp_path):
    root = repo(tmp_path, SAVE)
    assert _hint(tmp_path, "--claude", _claude(root, "Grep", {"pattern": "save"})) == ""


def test_a_bare_facts_cache_does_not_count_but_a_map_or_our_hook_does(tmp_path):
    root = repo(tmp_path, SAVE)
    (root / "soulreap" / ".soulreap").mkdir(parents=True)
    (root / "soulreap" / ".soulreap" / "facts.json").write_text("{}")
    assert _hint(tmp_path, "--claude", _claude(root, "Grep", {"pattern": "save"})) == ""
    (root / "soulreap" / "OVERVIEW.md").write_text("# map\n")
    assert "soulreap callers save" in _hint(tmp_path, "--claude", _claude(root, "Grep", {"pattern": "save"}))
    other = repo(tmp_path / "o", SAVE)
    (other / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\n# soulreap:pre-commit\n")
    assert "soulreap callers save" in _hint(tmp_path, "--claude", _claude(other, "Grep", {"pattern": "save"}))


def test_once_per_symbol_per_session(tmp_path):
    root = _installed(tmp_path)
    grep = {"pattern": "save"}
    assert _hint(tmp_path, "--claude", _claude(root, "Grep", grep, "a")) != ""
    assert _hint(tmp_path, "--claude", _claude(root, "Grep", grep, "a")) == ""
    assert _hint(tmp_path, "--claude", _claude(root, "Grep", grep, "b")) != ""


def test_pi_mode_prints_plain_text(tmp_path):
    root = _installed(tmp_path)
    payload = {"tool": "grep", "input": {"pattern": r"\bsave\b"}, "cwd": str(root), "session": "p1"}
    out = _hint(tmp_path, "--pi", payload)
    assert out.startswith("soulreap is installed in this repo") and not out.lstrip().startswith("{")


def test_a_search_path_inside_another_repo_decides(tmp_path):
    root = _installed(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    out = _hint(tmp_path, "--claude", _claude(elsewhere, "Grep", {"pattern": "save", "path": str(root / "s.py")}))
    assert "soulreap callers save" in out


def test_garbage_is_silent_and_fast(tmp_path):
    start = time.monotonic()
    assert _hint(tmp_path, "--claude", "not json") == ""
    assert time.monotonic() - start < 0.2
