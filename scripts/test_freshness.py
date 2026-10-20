"""Tests for fresh_facts: rebuilt from changed shas, written only when something moved."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib import freshness as fr
from srlib import util


def _repo(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    return root


def test_fresh_facts_first_call_writes_facts_json(tmp_path):
    repo, out = _repo(tmp_path / "repo"), tmp_path / "out"
    got = fr.fresh_facts(repo, out)
    assert got["reparsed"] == ["app.py"]
    stored = json.loads(fr.facts_path(out).read_text(encoding="utf-8"))
    assert stored["facts_version"] == 8 and "app.py" in stored["files"]


def test_fresh_facts_unchanged_tree_reparses_nothing_and_does_not_rewrite(tmp_path):
    repo, out = _repo(tmp_path / "repo"), tmp_path / "out"
    fr.fresh_facts(repo, out)
    before = fr.facts_path(out).stat().st_mtime_ns
    got = fr.fresh_facts(repo, out)
    assert got["reparsed"] == []
    assert fr.facts_path(out).stat().st_mtime_ns == before


def test_fresh_facts_edit_is_seen_by_the_next_call(tmp_path):
    repo, out = _repo(tmp_path / "repo"), tmp_path / "out"
    fr.fresh_facts(repo, out)
    (repo / "app.py").write_text("def main():\n    return helper()\n\ndef helper():\n    return 1\n", encoding="utf-8")
    got = fr.fresh_facts(repo, out)
    assert got["reparsed"] == ["app.py"]
    assert [f["name"] for f in got["files"]["app.py"]["functions"]] == ["main", "helper"]


def test_fresh_facts_older_version_is_rebuilt_from_scratch(tmp_path):
    repo, out = _repo(tmp_path / "repo"), tmp_path / "out"
    util.write_json(fr.facts_path(out), {"facts_version": 6, "files": {"app.py": {"sha": "x"}}, "context": []})
    assert fr.fresh_facts(repo, out)["reparsed"] == ["app.py"]


def test_commit_label_outside_git_is_worktree(tmp_path):
    assert util.commit_label(tmp_path) == "worktree"


def test_fresh_facts_extractor_change_rebuilds_everything(tmp_path, monkeypatch):
    repo, out = _repo(tmp_path / "repo"), tmp_path / "out"
    fr.fresh_facts(repo, out)
    monkeypatch.setattr(fr, "EXTRACTOR", "a-different-extractor")
    assert fr.fresh_facts(repo, out)["reparsed"] == ["app.py"]
    assert json.loads(fr.facts_path(out).read_text(encoding="utf-8"))["extractor"] == "a-different-extractor"


def test_write_json_crash_mid_write_leaves_the_old_file_intact(tmp_path, monkeypatch):
    target = tmp_path / "facts.json"
    util.write_json(target, {"v": 1})
    real = Path.write_text

    def torn(self, data, *args, **kwargs):
        real(self, data[:5], *args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", torn)
    with contextlib.suppress(OSError):
        util.write_json(target, {"v": 2})
    monkeypatch.setattr(Path, "write_text", real)
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 1}


def test_render_lock_held_by_a_live_process_is_waited_on(tmp_path, monkeypatch):
    import os

    import pytest

    monkeypatch.setattr(util, "LOCK_WAIT_SEC", 0.3)
    lock = tmp_path / ".soulreap" / "render.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(RuntimeError), util.render_lock(tmp_path):
        pass


def test_render_lock_of_a_dead_process_is_taken_over_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(util, "LOCK_WAIT_SEC", 0.3)
    lock = tmp_path / ".soulreap" / "render.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text("999999999", encoding="utf-8")
    with util.render_lock(tmp_path):
        assert lock.is_dir()
    assert not lock.exists()


def test_soulreap_cache_moves_the_facts_out_of_the_map_folder(tmp_path, monkeypatch):
    repo, out, cache = _repo(tmp_path / "repo"), tmp_path / "out", tmp_path / "cache"
    monkeypatch.setenv("SOULREAP_CACHE", str(cache))
    fr.fresh_facts(repo, out)
    assert (cache / "facts.json").exists()
    assert not (out / ".soulreap" / "facts.json").exists()
