#!/usr/bin/env python3
"""A one-line pointer to soulreap when an agent greps for a symbol — never a block.

Run by a Claude Code PreToolUse hook (`--claude`, the hook JSON on stdin) and by the Pi extension
`soulreap-hint` (`--pi`, `{"tool", "input", "cwd", "session"}` on stdin). Silent unless all hold:
the search is symbol-shaped (`save(`, `def save`, `\\bStore.load\\b`), the repo already has soulreap
in some form (a map, the AGENTS.md block or our pre-commit hook — a bare facts cache does not
count), and this session was not told about this symbol yet. Stdlib only, so it runs on the system
python in milliseconds; any error is silence.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

SOULREAP = Path(__file__).resolve().parent / "soulreap.py"
AGENTS_MARK = "<!-- soulreap:agents -->"
HOOK_MARK = "# soulreap:pre-commit"
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")
PREFIXES = ("async def ", "def ", "class ", "function ", "fn ", "func ")
SEARCHERS = {"grep", "egrep", "rg"}
VALUE_FLAGS = {"-A", "-B", "-C", "-m", "-f", "-g", "-t", "-T", "--glob", "--type", "--include", "--exclude",
               "--max-count", "--context", "--after-context", "--before-context"}


def symbol_of(pattern: str) -> str | None:
    """The symbol a search pattern looks for, or None when it is not symbol-shaped."""
    text = re.sub(r"\\s[+*]?", " ", pattern).replace(r"\b", "").replace(r"\(", "").replace("(", "")
    text = text.strip("^$ ").replace("\\.", ".")
    for prefix in PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text if len(text) >= 3 and IDENT.fullmatch(text) else None


def _bash_pattern(command: str) -> str | None:
    for segment in re.split(r"&&|\|\||[;|]", command):
        try:
            words = shlex.split(segment)
        except ValueError:
            continue
        if words[:2] == ["git", "grep"]:
            args = words[2:]
        elif words and Path(words[0]).name in SEARCHERS:
            args = words[1:]
        else:
            continue
        skip = False
        for i, word in enumerate(args):
            if skip:
                skip = False
            elif word in ("-e", "--regexp") and i + 1 < len(args):
                return args[i + 1]
            elif word in VALUE_FLAGS:
                skip = True
            elif not word.startswith("-"):
                return word
    return None


def _git_dir(top: Path) -> Path:
    dot = top / ".git"
    if dot.is_file():
        found = dot.read_text(encoding="utf-8", errors="replace").partition("gitdir:")[2].strip()
        return (top / found).resolve() if found else dot
    return dot


def _top(start: Path) -> Path | None:
    for folder in [start, *start.parents]:
        if (folder / ".git").exists():
            return folder
    return None


def _has_soulreap(top: Path) -> bool:
    if (top / "soulreap" / "OVERVIEW.md").exists() or (top / "soulreap" / "cards").is_dir():
        return True
    agents = top / "AGENTS.md"
    if agents.exists() and AGENTS_MARK in agents.read_text(encoding="utf-8", errors="replace"):
        return True
    hooks = [_git_dir(top) / "hooks" / "pre-commit"]
    custom = subprocess.run(["git", "-C", str(top), "config", "core.hooksPath"],
                            capture_output=True, text=True, timeout=2, check=False).stdout.strip()
    if custom:
        hooks.append((top / custom).resolve() / "pre-commit")
    return any(h.exists() and HOOK_MARK in h.read_text(encoding="utf-8", errors="replace") for h in hooks)


def _first_time(session: str, key: str) -> bool:
    folder = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    state = folder / f"soulreap-hints-{re.sub(r'[^A-Za-z0-9_-]', '_', session or 'none')[:80]}.json"
    try:
        seen = set(json.loads(state.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    if key in seen:
        return False
    seen.add(key)
    state.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    return True


def decide(tool: str, tool_input: dict, cwd: str, session: str) -> str | None:
    """The hint text, or None."""
    tool = tool.lower()
    pattern = tool_input.get("pattern") if tool == "grep" else _bash_pattern(tool_input.get("command") or "")
    name = symbol_of(pattern or "")
    if not name:
        return None
    start = Path(tool_input.get("path") or cwd or ".")
    if not start.is_absolute():
        start = Path(cwd or ".") / start
    start = start if start.is_dir() else start.parent
    top = _top(start.resolve())
    if top is None or not _has_soulreap(top) or not _first_time(session, f"{top}::{name}"):
        return None
    return (f'soulreap is installed in this repo: "soulreap callers {name}" lists resolved call sites of {name} '
            f'(through imports and methods); "soulreap slice {name}" returns its code and call neighbours in one '
            f"budgeted call. Command: uv run -q --script {SOULREAP} callers {name} --root {top}")


def main(argv: list[str]) -> int:
    try:
        data = json.loads(sys.stdin.read())
        if "--claude" in argv:
            text = decide(data.get("tool_name", ""), data.get("tool_input") or {}, data.get("cwd", ""),
                          data.get("session_id", ""))
            if text:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}))
        elif "--pi" in argv:
            text = decide(data.get("tool", ""), data.get("input") or {}, data.get("cwd", ""), data.get("session", ""))
            if text:
                print(text)
    except Exception:  # a hint is never worth an error
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
