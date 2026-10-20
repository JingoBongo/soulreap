"""Tests for resolving delta's revisions and reading facts at a commit."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from repo_fixtures import commit, git, repo, sha
from srlib.revisions import RevisionError, facts_at, resolve_revisions


def test_default_base_is_head_and_head_is_worktree(tmp_path):
    root = repo(tmp_path, {"a.py": "def f():\n    pass\n"})
    assert resolve_revisions(root, None, None) == (sha(root, "HEAD"), None)


def test_base_is_reduced_to_merge_base(tmp_path):
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    first = sha(root, "HEAD")
    git(root, "checkout", "-qb", "side")
    commit(root, {"b.py": "y = 2\n"})
    git(root, "checkout", "-q", "main")
    commit(root, {"c.py": "z = 3\n"})
    assert resolve_revisions(root, "side", None)[0] == first


def test_explicit_head_is_resolved_to_a_sha(tmp_path):
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    first = sha(root, "HEAD")
    commit(root, {"a.py": "x = 2\n"})
    assert resolve_revisions(root, first, "HEAD") == (first, sha(root, "HEAD"))


def test_unknown_ref_is_a_revision_error(tmp_path):
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    with pytest.raises(RevisionError):
        resolve_revisions(root, "nope", None)


def test_not_a_repo_is_a_revision_error(tmp_path):
    with pytest.raises(RevisionError):
        resolve_revisions(tmp_path, None, None)


def test_facts_at_reads_the_commit_not_the_worktree_and_cleans_up(tmp_path):
    root = repo(tmp_path, {"a.py": "def old():\n    pass\n"})
    (root / "a.py").write_text("def new():\n    pass\n")
    with facts_at(root, sha(root, "HEAD"), {}) as (tree, files):
        assert [f["name"] for f in files["a.py"]["functions"]] == ["old"]
        assert (tree / "a.py").exists()
    assert not tree.exists()
