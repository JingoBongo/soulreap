"""`install`: set a repo up for soulreap — the pre-commit check and the AGENTS.md block.

Both are owned whole: the hook file is written only when absent or ours, the block only between
its markers, so a re-run changes nothing and `--uninstall` removes exactly what was added. A
foreign pre-commit hook (yours, husky, pre-commit) is never touched: install prints the line to
add to it instead. The older post-commit `status --brief` hook is folded into the pre-commit one
and removed.
"""

from __future__ import annotations

import stat
from pathlib import Path

from .mapplan import HOOK_MARK as OLD_HOOK_MARK
from .util import git

HOOK_MARK = "# soulreap:pre-commit"
AGENTS_OPEN = "<!-- soulreap:agents -->"
AGENTS_CLOSE = "<!-- /soulreap:agents -->"

AGENTS_BODY = """\
## soulreap

This repo uses soulreap (skill `soulreap`): code facts you query instead of grepping — calls are
resolved through imports and methods, answers come from the code on disk now, no LLM.

| question | command |
|---|---|
| where is X | `soulreap find X` |
| who calls X / what breaks if X changes | `soulreap callers X`, `soulreap impact X` |
| who writes state X | `soulreap writes X` |
| the code around X, in one budgeted call | `soulreap slice X` (`--trace tb.txt` for a stack trace) |
| what did my change hit | `soulreap delta` (`--staged`, `--base main`) |

The pre-commit hook runs `soulreap precommit`: a HIGH finding (a removed symbol still called, a
signature changed with callers left behind) blocks the commit; bypass with `SOULREAP_SKIP=1` or
`--no-verify`. The full command line and the rest: the `soulreap` skill."""


def precommit_line(script: Path) -> str:
    return f'uv run -q --script "{script}" precommit --root "$(git rev-parse --show-toplevel)"'


def hook_text(script: Path) -> str:
    """The whole pre-commit file. Every failure path lets the commit through, with one line why."""
    return (
        "#!/bin/sh\n"
        f"{HOOK_MARK} — refuse a commit whose staged change has a HIGH soulreap finding (no LLM).\n"
        "# Bypass: SOULREAP_SKIP=1 git commit …, or git commit --no-verify. Remove: soulreap install --uninstall\n"
        '[ "${SOULREAP_SKIP:-}" = 1 ] && exit 0\n'
        'command -v uv >/dev/null 2>&1 || { echo "soulreap: uv not found — pre-commit check skipped" >&2; exit 0; }\n'
        f'[ -f "{script}" ] || {{ echo "soulreap: {script} is gone — check skipped; run soulreap install again" >&2;'
        " exit 0; }\n"
        f"PYTHONUTF8=1 exec {precommit_line(script)}\n"
    )


def agents_block() -> str:
    return f"{AGENTS_OPEN}\n{AGENTS_BODY}\n{AGENTS_CLOSE}"


def _hooks_dir(root: Path) -> Path:
    hooks = git(root, "rev-parse", "--git-path", "hooks")
    return (root / hooks).resolve() if hooks else root / ".git" / "hooks"


def _pre_commit(path: Path, script: Path, check: bool, uninstall: bool) -> tuple[int, str]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
    ours = text is not None and HOOK_MARK in text
    if text is not None and not ours:
        if uninstall:
            return 0, f"pre-commit: foreign {path}, left alone"
        return 1, f"pre-commit: foreign {path} — left alone; add this line to it: {precommit_line(script)}"
    if uninstall:
        if ours:
            path.unlink()
            return 0, "pre-commit: removed"
        return 0, "pre-commit: not installed"
    wanted = hook_text(script)
    if text == wanted:
        return 0, "pre-commit: current"
    if check:
        return 1, "pre-commit: " + ("outdated" if ours else "missing")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(wanted, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return 0, f"pre-commit: {'updated' if ours else 'installed'} {path}"


def _old_post_commit(path: Path, check: bool) -> tuple[int, str | None]:
    if not path.exists() or OLD_HOOK_MARK not in path.read_text(encoding="utf-8", errors="replace"):
        return 0, None
    if check:
        return 1, "post-commit (old): present — install removes it"
    path.unlink()
    return 0, "post-commit (old): removed"


def _without_block(text: str) -> tuple[str, bool]:
    start = text.find(AGENTS_OPEN)
    end = text.find(AGENTS_CLOSE, start)
    if start < 0 or end < 0:
        return text, False
    return text[:start] + "\u0000" + text[end + len(AGENTS_CLOSE):], True


def _agents(path: Path, check: bool, uninstall: bool) -> tuple[int, str]:
    text = path.read_text(encoding="utf-8") if path.exists() else None
    holed, has = _without_block(text or "")
    if uninstall:
        if not has:
            return 0, "AGENTS.md: no block"
        rest = holed.replace("\u0000", "").strip("\n")
        if rest.strip():
            before, _, after = holed.partition("\u0000")
            path.write_text((before.rstrip("\n") + "\n" + after.lstrip("\n")).lstrip("\n"), encoding="utf-8")
        else:
            path.unlink()
        return 0, "AGENTS.md: block removed"
    if has:
        wanted = holed.replace("\u0000", agents_block())
    elif text and text.strip():
        wanted = text.rstrip("\n") + "\n\n" + agents_block() + "\n"
    else:
        wanted = agents_block() + "\n"
    if text == wanted:
        return 0, "AGENTS.md: current"
    if check:
        return 1, "AGENTS.md: " + ("outdated" if has else "missing")
    path.write_text(wanted, encoding="utf-8")
    return 0, f"AGENTS.md: {'updated' if has else 'written'}"


def cmd_install(root: Path | str, script: Path, hook_only: bool = False, check: bool = False,
                uninstall: bool = False) -> tuple[int, list[str]]:
    """(exit code, one line per part). 2 outside a git repo; 1 when `check` finds something missing or
    outdated, or a foreign pre-commit hook kept the check out; else 0."""
    root = Path(root).resolve()
    top = git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return 2, [f"not a git repository: {root}"]
    hooks = _hooks_dir(root)
    results = [_pre_commit(hooks / "pre-commit", Path(script).resolve(), check, uninstall)]
    results.append(_old_post_commit(hooks / "post-commit", check))
    if not hook_only:
        results.append(_agents(Path(top) / "AGENTS.md", check, uninstall))
    return max(code for code, _ in results), [line for _, line in results if line]
