"""Small shared helpers for the v10 commands: git, JSON files."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RENDER_LOCK_STALE_SEC = 120  # only for a lock with no owner file (older soulreap)
LOCK_WAIT_SEC = 60.0


def git(root: Path | str, *args: str) -> str | None:
    """`git -C root args…` stdout, or None when git is missing or the call fails."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def head_commit(root: Path | str) -> str | None:
    """Short HEAD — or `$SOULREAP_COMMIT` when set: the pre-commit hook renders an extracted copy of
    the index, which has no git of its own (and must not find an enclosing repo's)."""
    return os.environ.get("SOULREAP_COMMIT") or git(root, "rev-parse", "--short", "HEAD")


def commit_label(root: Path | str) -> str:
    """Short HEAD, or `worktree` when there is no commit to name (no repo, or no commits yet)."""
    return head_commit(root) or "worktree"


def load_json(path: Path, fallback):
    """Parsed JSON at *path*, or *fallback* when missing or unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def write_text_atomic(path: Path, text: str) -> None:
    """Write via a temp file and `os.replace`, so a reader never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path: Path, payload) -> None:
    """Write *payload* as indented UTF-8 JSON, atomically, creating parent directories."""
    write_text_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _owner_alive(lock: Path) -> bool | None:
    """Is the process that holds *lock* alive? None when the lock names no owner."""
    try:
        pid = int((lock / "owner").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False  # no such process, or not a valid pid at all
    except PermissionError:
        return True  # it exists; it belongs to someone else
    return True


@contextlib.contextmanager
def render_lock(out: Path):
    """Serialize render and confirm: the hook, a manual render and a confirm must not interleave.

    An atomic mkdir is the lock and an `owner` file names the holder's pid. A lock whose owner is
    dead (or, for an ownerless lock, older than RENDER_LOCK_STALE_SEC) is taken over by renaming
    it away first — a rename succeeds for exactly one waiter, so two waiters cannot both steal it,
    and a long render by a live process is never stolen.
    """
    lock = Path(out) / ".soulreap" / "render.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_WAIT_SEC
    while True:
        try:
            os.mkdir(lock)
            (lock / "owner").write_text(str(os.getpid()), encoding="utf-8")
            break
        except FileExistsError:
            alive = _owner_alive(lock)
            with contextlib.suppress(OSError):
                ownerless_and_old = alive is None and time.time() - os.path.getmtime(lock) > RENDER_LOCK_STALE_SEC
                if alive is False or ownerless_and_old:
                    grave = lock.with_name(f"{lock.name}.abandoned.{os.getpid()}.{time.monotonic_ns()}")
                    os.rename(lock, grave)
                    shutil.rmtree(grave, ignore_errors=True)
                    continue
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"render is already running (lock held at {lock}). If no other render is "
                    "running, delete that directory and run render again."
                ) from None
            time.sleep(0.1)
    try:
        yield
    finally:
        shutil.rmtree(lock, ignore_errors=True)


# Lines native libraries print on every run that say nothing about the map: graphviz's fontconfig
# complains about a system config element it does not know. An agent that runs `render 2>&1 | json`
# then cannot parse the JSON, so they are dropped rather than documented around.
NATIVE_NOISE = ("Fontconfig warning",)


@contextlib.contextmanager
def quiet_native_noise():
    """Hold file descriptor 2 for the block and re-emit it without `NATIVE_NOISE` lines.

    pygraphviz draws in-process, so the noise is written by C code straight to fd 2, below
    `sys.stderr`; only a descriptor-level redirect reaches it.
    """
    sys.stderr.flush()
    saved = os.dup(2)
    with tempfile.TemporaryFile() as held:
        os.dup2(held.fileno(), 2)
        try:
            yield
        finally:
            sys.stderr.flush()
            os.dup2(saved, 2)
            os.close(saved)
            held.seek(0)
            for line in held.read().decode("utf-8", errors="replace").splitlines(keepends=True):
                if not line.startswith(NATIVE_NOISE):
                    sys.stderr.write(line)
            sys.stderr.flush()
