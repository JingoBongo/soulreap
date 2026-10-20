"""Tests for what is new since the last plan: entries, hazards, files, removed citations."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib import mapplan as mp
from srlib import maprender as mr

APP = "from store import save\n\n\ndef main():\n    try:\n        save(1)\n    except Exception:\n        pass\n"
STORE = "CACHE = {}\n\n\ndef save(v):\n    CACHE[v] = 1\n"
API = "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n@app.get('/ping')\ndef ping():\n    return 'pong'\n"
CARD = "# Main loop\n\n## Flow\n\n[[app.py::main]] calls [[store.py::save]].\n"


def _mapped(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text(APP, encoding="utf-8")
    (root / "store.py").write_text(STORE, encoding="utf-8")
    out = root / "soulreap"
    mp.cmd_plan(root, out)
    (out / "cards").mkdir(parents=True)
    (out / "cards" / "main-loop.md").write_text(CARD, encoding="utf-8")
    mr.cmd_render(root, out)
    return root, out


def test_status_on_an_unchanged_map_has_nothing_new(tmp_path):
    root, out = _mapped(tmp_path)
    since = mr.cmd_status(root, out)["since_plan"]
    assert since == {"entries": [], "hazards": [], "files": [], "removed": []}


def test_status_names_a_new_entry_point_and_file(tmp_path):
    root, out = _mapped(tmp_path)
    (root / "api.py").write_text(API, encoding="utf-8")
    status = mr.cmd_status(root, out)
    assert status["since_plan"]["entries"] == ["api.py::ping"]
    assert status["since_plan"]["files"] == ["api.py"]
    assert status["clean"] is False


def test_status_names_a_new_hazard_and_a_removed_citation(tmp_path):
    root, out = _mapped(tmp_path)
    (root / "app.py").write_text(APP.replace("from store import save", "import os\nfrom store import save")
                                 + "\n\ndef wipe(p):\n    os.remove(p)\n", encoding="utf-8")
    (root / "store.py").write_text("CACHE = {}\n", encoding="utf-8")
    since = mr.cmd_status(root, out)["since_plan"]
    assert [h["symbol"] for h in since["hazards"]] == ["app.py::wipe"]
    assert since["removed"] == ["store.py::save"]


def test_replan_on_a_map_writes_an_update_packet_and_enforces_new_entries(tmp_path):
    root, out = _mapped(tmp_path)
    (root / "api.py").write_text(API, encoding="utf-8")
    payload = mp.cmd_plan(root, out)
    assert payload["update"]["entries"] == ["api.py::ping"]
    packet = (out / ".soulreap" / "update.md").read_text(encoding="utf-8")
    assert "[[api.py::ping]]" in packet and "api.py" in packet
    assert "api.py::ping" in [i["symbol"] for i in mr.cmd_render(root, out)["checklist_missing"]]
    stored = json.loads((out / ".soulreap" / "plan.json").read_text(encoding="utf-8"))
    assert "api.py::ping" in stored["entries"]


def test_status_after_explain_only_has_nothing_new(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text(APP, encoding="utf-8")
    (root / "store.py").write_text(STORE, encoding="utf-8")
    out = root / "soulreap"
    mp.cmd_explain(root, out, "main")
    (out / "cards").mkdir(parents=True)
    (out / "cards" / "main.md").write_text(CARD, encoding="utf-8")
    mr.cmd_render(root, out)
    assert mr.cmd_status(root, out)["since_plan"] == {"entries": [], "hazards": [], "files": [], "removed": []}


def test_replan_keeps_explain_checklists_and_drops_gone_symbols(tmp_path):
    root, out = _mapped(tmp_path)
    mp.cmd_explain(root, out, "main")
    before = json.loads((out / ".soulreap" / "plan.json").read_text(encoding="utf-8"))["explain"]
    assert any(i["symbol"] == "store.py::save" for items in before.values() for i in items)
    (root / "store.py").write_text("CACHE = {}\n", encoding="utf-8")
    mp.cmd_plan(root, out)
    after = json.loads((out / ".soulreap" / "plan.json").read_text(encoding="utf-8"))["explain"]
    assert list(after) == list(before)
    symbols = {i["symbol"] for items in after.values() for i in items}
    assert "app.py::main" in symbols and "store.py::save" not in symbols


def test_replan_with_exclude_does_not_report_excluded_entries(tmp_path):
    root, out = _mapped(tmp_path)
    (root / "api.py").write_text(API, encoding="utf-8")
    payload = mp.cmd_plan(root, out, exclude=("api.py",))
    assert payload["update"]["entries"] == [] and "api.py::ping" not in payload["entries"]
    assert mr.cmd_status(root, out)["since_plan"]["entries"] == []


def test_update_packet_lists_every_new_item_render_will_demand(tmp_path):
    root, out = _mapped(tmp_path)
    (root / "jobs.py").write_text("import os\n\n\ndef wipe(p):\n    os.remove(p)\n", encoding="utf-8")
    payload = mp.cmd_plan(root, out)
    demanded = {i["symbol"] for i in mr.cmd_render(root, out)["checklist_missing"]}
    listed = {i["symbol"] for i in payload["update"]["hazards"]} | set(payload["update"]["entries"])
    assert "jobs.py::wipe" in demanded and demanded <= listed


def test_plan_and_status_ignore_citations_in_a_legacy_overview_alike(tmp_path):
    root, out = _mapped(tmp_path)
    (root / "api.py").write_text(API, encoding="utf-8")
    (out / "OVERVIEW.md").write_text("# Overview\n\n## Entry\n\nSee [[api.py::ping]].\n", encoding="utf-8")
    status_entries = mr.cmd_status(root, out)["since_plan"]["entries"]
    plan_entries = mp.cmd_plan(root, out)["update"]["entries"]
    assert status_entries == plan_entries == ["api.py::ping"]
