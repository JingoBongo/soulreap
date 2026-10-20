"""The two revisions `delta` compares, and the facts of a commit read from a temporary tree.

Git is the version store: a commit is extracted with `git archive` into a temp directory and
built with the head side's blobs as the reuse cache, so only files whose content differs are
parsed again. The tree is removed when the caller is done with it (the flow index reads sources
from disk, so it has to live as long as the queries over it).
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator
from pathlib import Path

from .build import build_facts
from .util import git


class RevisionError(Exception):
    """A ref git cannot resolve, or a root that is not inside a git repository."""


def _commit(root: Path, ref: str) -> str:
    found = git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if not found:
        raise RevisionError(f"unknown revision: {ref}")
    return found


def index_tree(root: Path | str) -> str:
    """The index as a tree object: exactly what the next commit would contain."""
    tree = git(root, "write-tree")
    if not tree:
        raise RevisionError("cannot read the index (unmerged paths?)")
    return tree


def _empty_tree(root: Path) -> str:
    return git(root, "hash-object", "-t", "tree", "/dev/null") or "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def resolve_revisions(root: Path | str, base: str | None, head: str | None,
                      staged: bool = False) -> tuple[str, str | None]:
    """(base, head) — head None is the work tree; with *staged* it is the index's tree. A given base
    is reduced to its merge base with the head (or HEAD), so `--base main` shows the branch's own
    changes. Staged in a repo with no commits yet compares against the empty tree."""
    root = Path(root)
    if git(root, "rev-parse", "--show-toplevel") is None:
        raise RevisionError(f"not a git repository: {root}")
    if staged and head:
        raise RevisionError("--staged compares the index with its base; it cannot take --head")
    head_sha = index_tree(root) if staged else _commit(root, head) if head else None
    if base is None:
        if staged and git(root, "rev-parse", "--verify", "--quiet", "HEAD") is None:
            return _empty_tree(root), head_sha
        return _commit(root, "HEAD"), head_sha
    base_sha = _commit(root, base)
    merge = git(root, "merge-base", base_sha, _commit(root, "HEAD") if staged or not head_sha else head_sha)
    if not merge:
        raise RevisionError(f"no merge base between {base} and {head or 'HEAD'}")
    return merge, head_sha


@contextlib.contextmanager
def facts_at(root: Path | str, sha: str, previous: dict[str, dict]) -> Iterator[tuple[Path, dict[str, dict]]]:
    """Yield (tree, facts) for *root* as of commit (or tree) *sha*; the tree is deleted afterwards."""
    with tree_at(root, sha) as tree:
        yield tree, build_facts(tree, previous).files


@contextlib.contextmanager
def tree_at(root: Path | str, sha: str) -> Iterator[Path]:
    """Yield a temp directory holding *root* as of commit or tree *sha*; deleted afterwards.

    *root* may be a subdirectory of the repository: only that subtree is extracted, so paths
    match the work tree's.
    """
    root = Path(root)
    prefix = git(root, "rev-parse", "--show-prefix") or ""
    tree = Path(tempfile.mkdtemp(prefix="soulreap-rev-"))
    try:
        treeish = f"{sha}:{prefix}" if prefix else sha
        result = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", treeish],
                                capture_output=True, check=False)
        if result.returncode != 0:
            raise RevisionError(f"git archive {sha} failed: {result.stderr.decode(errors='replace').strip()}")
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
            archive.extractall(tree, filter="data")
        yield tree
    finally:
        shutil.rmtree(tree, ignore_errors=True)
