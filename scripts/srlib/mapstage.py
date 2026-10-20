"""Render the map from the *staged* code and put the result in the commit — precommit's first half.

A commit should carry a map that describes exactly the code it contains. `render` reads a tree on
disk, so the index is extracted to a temp dir and rendered there (stamped with HEAD's id), and
every map file render changed is written straight into the index. The same file on disk is
updated only when it had no unstaged edits, so a card being written is never clobbered. Only a
map the index already tracks is touched: a local, uncommitted map stays out of history.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

from .maprender import cmd_render
from .revisions import index_tree, tree_at
from .util import git, quiet_native_noise
from .walk import classify

MAP = "soulreap"


def _git(root: Path, *args: str, data: bytes | None = None) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], input=data, capture_output=True, check=True)
    return result.stdout.decode("utf-8", errors="replace").strip()


@contextlib.contextmanager
def _env(**values: str | None) -> Iterator[None]:
    before = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def map_tracked(root: Path) -> bool:
    """The index already holds a map (an OVERVIEW or cards), so the commit should carry it fresh."""
    return bool(git(root, "ls-files", "--", f"{MAP}/OVERVIEW.md", f"{MAP}/cards"))


def _relevant(root: Path) -> bool:
    touched = (git(root, "diff", "--cached", "--relative", "--name-only") or "").splitlines()
    return any(p.startswith(f"{MAP}/") or classify(p)[0] == "code" for p in touched)


def _index_blobs(root: Path) -> dict[str, tuple[str, str]]:
    found = {}
    for line in _git(root, "ls-files", "-s", "--", MAP).splitlines():
        meta, _, rel = line.partition("\t")
        mode, blob, _stage = meta.split()
        found[rel] = (mode, blob)
    return found


def _sync_disk(root: Path, rel: str, old_blob: str | None, data: bytes | None) -> bool:
    """Bring the disk copy along when it had no unstaged edits; False when it was left alone."""
    disk = root / rel
    current = _git(root, "hash-object", "--", rel) if disk.is_file() else None
    if current != old_blob:
        return False
    if data is None:
        disk.unlink(missing_ok=True)
    else:
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_bytes(data)
    return True


def stage_rendered_map(root: Path | str) -> list[str]:
    """Render the staged map into the index; report lines (empty when there was nothing to do).
    Never raises: a failure is one line, and the commit goes on."""
    root = Path(root).resolve()
    if not map_tracked(root) or not _relevant(root):
        return []
    try:
        head = git(root, "rev-parse", "--short", "HEAD") or "root"
        with tree_at(root, index_tree(root)) as tmp, _env(SOULREAP_COMMIT=head, SOULREAP_CACHE=None):
            with quiet_native_noise():
                stats = cmd_render(tmp, tmp / MAP)
            rendered = {p.relative_to(tmp).as_posix(): p.read_bytes()
                        for p in sorted((tmp / MAP).rglob("*")) if p.is_file()}
        before = _index_blobs(root)
        changed, kept = [], []
        for rel, data in rendered.items():
            old = before.get(rel)
            blob = _git(root, "hash-object", "-w", "--stdin", data=data)
            if old and old[1] == blob:
                continue
            if old is None and git(root, "check-ignore", "-q", "--", rel) is not None:
                continue  # an ignored file (a local cache, say) stays out of the commit
            _git(root, "update-index", "--add", "--cacheinfo", f"{old[0] if old else '100644'},{blob},{rel}")
            changed.append(rel)
            if not _sync_disk(root, rel, old[1] if old else None, data):
                kept.append(rel)
        for rel, (_mode, blob) in before.items():
            if rel not in rendered:
                _git(root, "update-index", "--force-remove", "--", rel)
                changed.append(rel)
                if not _sync_disk(root, rel, blob, None):
                    kept.append(rel)
    except Exception as exc:  # a map that cannot be refreshed must never stop a commit
        return [f"soulreap: map not refreshed ({type(exc).__name__}: {exc})"]
    lines = [f"soulreap: map refreshed in this commit ({len(changed)} file(s))"] if changed else []
    lines += [f"soulreap: kept your unstaged edits in {rel} — the commit has the rendered version" for rel in kept]
    if stats["stale"] or stats["unresolved"]:
        lines.append(f"soulreap: {len(stats['stale'])} stale section(s), {len(stats['unresolved'])} broken "
                     "anchor(s) in the committed map — run `soulreap refresh`")
    return lines
