"""Tests for build_facts: parity with plan's facts.json, and incremental reuse."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import soulreap as sr
from srlib import build as bd


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


FILES = {
    "app.py": "from store import save\n\n\ndef main():\n    save(1)\n",
    "store.py": "CACHE = {}\n\n\ndef save(v):\n    CACHE[v] = 1\n",
}


def test_build_facts_matches_what_plan_writes(tmp_path):
    repo = _repo(tmp_path / "repo", FILES)
    out = tmp_path / "out"
    sr.cmd_plan(repo, out)
    stored = json.loads((out / ".soulreap" / "facts.json").read_text(encoding="utf-8"))
    built = bd.build_facts(repo)
    assert built.files == stored["files"]
    assert built.context == stored["context"]


def test_build_facts_with_previous_reparses_nothing_unchanged(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo", FILES)
    first = bd.build_facts(repo)
    assert sorted(first.reparsed) == ["app.py", "store.py"]
    calls: list[str] = []
    real = bd.extract_source
    monkeypatch.setattr(bd, "extract_source", lambda text, path: calls.append(path) or real(text, path))
    second = bd.build_facts(repo, first.files)
    assert second.reparsed == [] and calls == []
    assert second.files == first.files


def test_build_facts_with_previous_reparses_only_the_changed_file(tmp_path):
    repo = _repo(tmp_path / "repo", FILES)
    first = bd.build_facts(repo)
    (repo / "app.py").write_text(FILES["app.py"] + "\n\ndef other():\n    return 2\n", encoding="utf-8")
    second = bd.build_facts(repo, first.files)
    assert second.reparsed == ["app.py"]
    assert [f["name"] for f in second.files["app.py"]["functions"]] == ["main", "other"]


def test_build_facts_deleted_file_disappears(tmp_path):
    repo = _repo(tmp_path / "repo", FILES)
    first = bd.build_facts(repo)
    (repo / "app.py").unlink()
    second = bd.build_facts(repo, first.files)
    assert "app.py" not in second.files
    assert second.files["store.py"]["imported_by"] == []


def test_build_facts_import_inside_a_function_is_a_dependency(tmp_path):
    repo = _repo(tmp_path / "repo", {
        "config.py": "def sep():\n    import os_utils\n    return os_utils.win()\n",
        "os_utils.py": "def win():\n    return False\n",
    })
    files = bd.build_facts(repo).files
    assert files["config.py"]["deps"] == ["os_utils.py"]
    assert files["os_utils.py"]["imported_by"] == ["config.py"]
    lazy = [i for i in files["config.py"]["imports"] if i["module"] == "os_utils"]
    assert lazy and lazy[0]["lazy"] is True


def test_from_package_import_module_depends_on_the_module(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/rank.py": "def score():\n    return 1\n",
             "pkg/flow.py": "def chain():\n    return 2\n",
             "pkg/plan.py": "from . import flow, rank\n\n\ndef plan():\n    return rank.score() + flow.chain()\n",
             "main.py": "from pkg import plan\n\nplan.plan()\n"}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    built = bd.build_facts(tmp_path).files
    assert {"pkg/rank.py", "pkg/flow.py"} <= set(built["pkg/plan.py"]["deps"])
    assert "pkg/plan.py" in built["main.py"]["deps"]
    assert "pkg/plan.py" in built["pkg/rank.py"]["imported_by"]


def test_dotted_strings_naming_repo_modules_are_dependencies(tmp_path):
    files = {"site_pkg/__init__.py": "", "site_pkg/urls.py": "urlpatterns = []\n",
             "apps/__init__.py": "", "apps/webhooks/__init__.py": "",
                 "apps/webhooks/apps.py": "class WebhooksConfig:\n    pass\n",
             "site_pkg/settings.py": ('ROOT_URLCONF = "site_pkg.urls"\n'
                                      'INSTALLED_APPS = ["django.contrib.admin", "apps.webhooks.apps.WebhooksConfig"]\n'
                                      'LOG = "not.a.module"\n')}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    deps = set(bd.build_facts(tmp_path).files["site_pkg/settings.py"]["deps"])
    assert {"site_pkg/urls.py", "apps/webhooks/apps.py"} <= deps
