"""`precommit`: what the git hook runs — the staged map rendered into the commit, then `delta --staged`.

Only a HIGH finding (likely breakage) blocks; MED findings are listed and let through; a map that
went stale is one line and never blocks. The facts cache lives inside `.git`, so a repo that
commits its `soulreap/` folder is never dirtied mid-commit. A checker that fails lets the commit
through: a broken tool must not stop work.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

from .deltacmd import run_delta
from .maprender import cmd_status
from .mapstage import map_tracked, stage_rendered_map
from .util import git

SKIP_ENV = "SOULREAP_SKIP"


def has_map(out: Path) -> bool:
    """A map exists: an OVERVIEW or cards, not just a facts cache some query left behind."""
    return (out / "OVERVIEW.md").exists() or (out / "cards").is_dir()


def brief_status(result: dict) -> str | None:
    """`status --brief`'s line, or None when the map is clean."""
    if result["clean"]:
        return None
    new = sum(len(v) for v in result["since_plan"].values())
    return (f"soulreap: {len(result['stale'])} stale section(s), {len(result['unresolved'])} broken "
            f"anchor(s), {new} new thing(s) since the last plan — run `soulreap refresh` / `plan`")


@contextlib.contextmanager
def _cache_in(path: Path) -> Iterator[None]:
    before = os.environ.get("SOULREAP_CACHE")
    os.environ["SOULREAP_CACHE"] = str(path)
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("SOULREAP_CACHE", None)
        else:
            os.environ["SOULREAP_CACHE"] = before


def run_precommit(root: Path | str, err: TextIO | None = None) -> int:
    """1 when the staged change has a HIGH finding, else 0. Writes only to *err* and `.git/soulreap`."""
    err = err or sys.stderr
    if os.environ.get(SKIP_ENV) == "1":
        return 0
    root = Path(root).resolve()
    out = root / "soulreap"
    for note in stage_rendered_map(root):  # never raises; its own failures are a line
        print(note, file=err)
    tracked = map_tracked(root)
    try:
        with _cache_in(Path(git(root, "rev-parse", "--absolute-git-dir") or root / ".git") / "soulreap"):
            report = run_delta(root, out, None, None, staged=True)
            line = brief_status(cmd_status(root, out)) if has_map(out) and not tracked else None
    except Exception as exc:  # a broken checker must never stop a commit
        print(f"soulreap: check failed ({type(exc).__name__}: {exc}) — commit not blocked", file=err)
        return 0
    shown = [f for f in report["findings"] if f["tier"] in ("HIGH", "MED")]
    for finding in shown:
        print(f"soulreap: {finding['tier']:<4} {finding['rule']}  {finding['key']} — {finding['detail']}", file=err)
    if line:
        print(line, file=err)
    high = sum(f["tier"] == "HIGH" for f in shown)
    if high:
        print(f"soulreap: commit blocked: {high} HIGH finding(s) — fix them, or commit with "
              f"{SKIP_ENV}=1 (or --no-verify) if they are intended", file=err)
        return 1
    return 0
