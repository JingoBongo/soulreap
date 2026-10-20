"""Tests for the v10 plan, explain, refresh, confirm and hook commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from srlib import mapplan as mp

FILES = {
    "app.py": (
        "from store import save\n\n\ndef main():\n"
        "    try:\n        save(1)\n    except Exception:\n        pass\n"
    ),
    "store.py": "CACHE = {}\n\n\ndef save(v):\n    CACHE[v] = 1\n",
}


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    for rel, text in FILES.items():
        (root / rel).write_text(text, encoding="utf-8")
    return root, root / "soulreap"


def test_plan_writes_read_set_checklist_and_packet(tmp_path):
    root, out = _repo(tmp_path)
    payload = mp.cmd_plan(root, out)
    assert payload["map_version"] == 10 and payload["read_set"]["whole"] is True
    assert {i["symbol"] for i in payload["checklist"]} == {"app.py::main", "store.py::save"}
    assert payload["cards"] == 1 and "dispatch" not in payload
    stored = json.loads((out / ".soulreap" / "plan.json").read_text(encoding="utf-8"))
    assert stored["checklist"] == payload["checklist"]
    packet = (out / ".soulreap" / "read.md").read_text(encoding="utf-8")
    assert "[[app.py::main]] swallow: Exception" in packet and "- app.py (whole" in packet


def test_plan_reports_legacy_directories(tmp_path):
    root, out = _repo(tmp_path)
    (out / "units").mkdir(parents=True)
    assert mp.cmd_plan(root, out)["legacy"] == ["units"]


def test_explain_writes_packet_with_callers_and_checklist(tmp_path):
    root, out = _repo(tmp_path)
    packet = mp.cmd_explain(root, out, "save").read_text(encoding="utf-8")
    assert "[[store.py::save]]" in packet and "[[app.py::main]]" in packet and "write: CACHE" in packet
    assert "cards/store-py-save.md" in packet


def test_explain_ambiguous_target_raises_with_candidates(tmp_path):
    root, out = _repo(tmp_path)
    (root / "other.py").write_text("def save(v):\n    return v\n", encoding="utf-8")
    with pytest.raises(LookupError) as err:
        mp.cmd_explain(root, out, "save")
    assert err.value.args[0] == ["other.py::save", "store.py::save"]


def test_plan_writes_facts_json_and_reports_left_out_tests(tmp_path):
    root, out = _repo(tmp_path)
    (root / "tests").mkdir()
    test_src = "from app import main\n\n\ndef test_x():\n    main()\n"
    (root / "tests" / "test_app.py").write_text(test_src, encoding="utf-8")
    payload = mp.cmd_plan(root, out, exclude=["store.py"])
    facts = json.loads((out / ".soulreap" / "facts.json").read_text(encoding="utf-8"))["files"]
    assert "tests/test_app.py" in facts, "facts cover tests even when the map leaves them out"
    assert payload["left_out"] == {"tests": ["tests/test_app.py"], "excluded": ["store.py"]}
    assert payload["excluded_by"]["skip_tests"] is True


def test_cli_callers_with_no_callers_says_so(tmp_path):
    import subprocess

    root, _ = _repo(tmp_path)
    script = Path(__file__).parent / "soulreap.py"
    result = subprocess.run([sys.executable, str(script), "callers", "--root", str(root), "app.py::main"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0 and "no callers" in result.stdout


def test_plan_promises_only_as_many_cards_as_it_has_topics(tmp_path):
    root, out = _repo(tmp_path)
    (root / "consts.py").write_text("".join(f"X{i} = {i}\n" for i in range(900)), encoding="utf-8")
    payload = mp.cmd_plan(root, out)
    assert payload["cards"] == len(payload["topics"])
    packet = (out / ".soulreap" / "read.md").read_text(encoding="utf-8")
    assert f"## Cards to write ({len(payload['topics'])})" in packet


def test_confirm_respects_the_render_lock(tmp_path, monkeypatch):
    import os

    from srlib import util

    root, out = _repo(tmp_path)
    mp.cmd_plan(root, out)
    (out / "cards").mkdir()
    (out / "cards" / "c.md").write_text("# C\n\n## Flow\n\n[[app.py::main]]\n", encoding="utf-8")
    monkeypatch.setattr(util, "LOCK_WAIT_SEC", 0.3)
    lock = out / ".soulreap" / "render.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(RuntimeError):
        mp.cmd_confirm(root, out, "cards/c.md", "Flow")


def test_cli_writes_with_no_writers_says_so(tmp_path):
    import subprocess

    root, _ = _repo(tmp_path)
    script = Path(__file__).parent / "soulreap.py"
    result = subprocess.run([sys.executable, str(script), "writes", "--root", str(root), "nothing_here"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0 and "no writers found" in result.stdout


def test_packet_names_the_contract_by_an_absolute_path_that_exists(tmp_path):
    import re

    root, out = _repo(tmp_path)
    mp.cmd_plan(root, out)
    packet = (out / ".soulreap" / "read.md").read_text(encoding="utf-8")
    path = Path(re.search(r"Contract: `([^`]+)`", packet).group(1))
    assert path.is_absolute() and path.exists()


def test_confirm_refuses_an_ambiguous_heading(tmp_path):
    root, out = _repo(tmp_path)
    mp.cmd_plan(root, out)
    (out / "cards").mkdir()
    (out / "cards" / "c.md").write_text("# C\n\n## Notes\n\n[[app.py::main]]\n\n## Notes\n\n[[store.py::save]]\n",
                                        encoding="utf-8")
    with pytest.raises(LookupError):
        mp.cmd_confirm(root, out, "cards/c.md", "Notes")


def test_explain_builds_the_call_index_once(tmp_path, monkeypatch):
    from srlib import flow

    root, out = _repo(tmp_path)
    builds = []
    real = flow.build
    monkeypatch.setattr(flow, "build", lambda *a, **k: builds.append(1) or real(*a, **k))
    mp.cmd_explain(root, out, "store.py")
    assert len(builds) == 1


def test_explain_checklist_is_enforced_by_render(tmp_path):
    from srlib import maprender as mr

    root, out = _repo(tmp_path)
    mp.cmd_plan(root, out)
    mp.cmd_explain(root, out, "main")
    (out / "cards").mkdir(exist_ok=True)
    (out / "cards" / "app-py-main.md").write_text("# Main\n\n## Flow\n\n[[app.py::main]] runs.\n", encoding="utf-8")
    plan = json.loads((out / ".soulreap" / "plan.json").read_text(encoding="utf-8"))
    plan["checklist"] = []
    (out / ".soulreap" / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    stats = mr.cmd_render(root, out)
    assert "store.py::save" in [i["symbol"] for i in stats["checklist_missing"]]


def test_read_packet_names_the_file_for_every_card(tmp_path):
    root, out = _repo(tmp_path)
    plan = mp.cmd_plan(root, out)
    packet = (out / ".soulreap" / "read.md").read_text(encoding="utf-8")
    for topic in plan["topics"]:
        assert f"cards/{mp._slug(topic['target'])}.md" in packet


def test_read_packet_lists_other_files_to_grep_not_read(tmp_path):
    root, out = _repo(tmp_path)
    (root / "templates").mkdir()
    (root / "templates" / "ui.html").write_text("<html>\n" * 40, encoding="utf-8")
    mp.cmd_plan(root, out)
    packet = (out / ".soulreap" / "read.md").read_text(encoding="utf-8")
    other = packet.split("## Other files")[1].split("\n## ")[0]
    assert "templates/ui.html (40 lines)" in other and "grep" in other


def test_read_packet_says_what_render_checks_and_rewrites(tmp_path):
    root, out = _repo(tmp_path)
    mp.cmd_plan(root, out)
    packet = (out / ".soulreap" / "read.md").read_text(encoding="utf-8")
    after = packet.split("## After writing")[1]
    words = ("`strict: true`", "`unwritten`", "`unresolved`", "`checklist_missing`", "`stale`",
             "re-read", "overview.md")
    for word in words:
        assert word in after


def _cli(*args: str):
    import subprocess

    script = Path(__file__).parent / "soulreap.py"
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, check=False)


def test_plan_scaffolds_the_overview_on_a_first_run(tmp_path):
    root, out = _repo(tmp_path)
    mp.cmd_plan(root, out)
    assert "_Write this by hand" in (out / "OVERVIEW.md").read_text(encoding="utf-8")


def test_explain_leaves_tests_out_of_the_packet_and_the_checklist(tmp_path):
    root, out = _repo(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "test_store.py").write_text(
        "import os\nfrom store import save\n\n\ndef test_save():\n    save(1)\n    os.remove('x')\n",
        encoding="utf-8")
    mp.cmd_plan(root, out)
    packet = mp.cmd_explain(root, out, "save").read_text(encoding="utf-8")
    plan = json.loads((out / ".soulreap" / "plan.json").read_text(encoding="utf-8"))
    assert "tests/" not in packet
    assert not any(i["symbol"].startswith("tests/") for items in plan["explain"].values() for i in items)


def test_packets_spell_out_runnable_commands(tmp_path):
    root, out = _repo(tmp_path)
    mp.cmd_plan(root, out)
    run = f"uv run --script {mp.SCRIPT}"
    assert f"{run} render --root {root.resolve()}" in (out / ".soulreap" / "read.md").read_text(encoding="utf-8")
    explain = mp.cmd_explain(root, out, "main").read_text(encoding="utf-8")
    assert f"{run} render --root {root.resolve()}" in explain


def test_replan_on_a_map_points_only_at_the_update_packet(tmp_path):
    root, out = _repo(tmp_path)
    mp.cmd_plan(root, out)
    (out / "cards").mkdir()
    (out / "cards" / "main.md").write_text("# Main\n\n## Flow\n\n[[app.py::main]] runs.\n", encoding="utf-8")
    result = _cli("plan", "--root", str(root))
    assert "update.md" in result.stdout and "read.md" not in result.stdout


def test_confirm_says_when_the_section_is_not_there(tmp_path):
    root, out = _repo(tmp_path)
    mp.cmd_plan(root, out)
    result = _cli("confirm", "--root", str(root), "OVERVIEW.md", "No such heading")
    assert result.returncode == 2 and "not found" in result.stderr


def test_packet_commands_carry_a_non_default_out(tmp_path):
    root, _ = _repo(tmp_path)
    out = tmp_path / "elsewhere"
    mp.cmd_plan(root, out)
    packet = (out / ".soulreap" / "read.md").read_text(encoding="utf-8")
    assert f"--out {out.resolve()}" in packet and f"under `{out.resolve()}`" in packet


def test_packet_names_each_store_outside_the_process(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "store.py").write_text(
        "import vaults\n\nNAME = 'mercy_auto_update'\n\n\nclass S:\n"
        "    def __init__(self):\n        self._vault = vaults.Vault(NAME)\n\n"
        "    def on(self, k):\n        return self._vault.get(k).get('enabled')\n\n"
        "    def set(self, k):\n        self._vault.put(k, {'enabled': True})\n", encoding="utf-8")
    out = tmp_path / "map"
    mp.cmd_plan(root, out)
    packet = (out / ".soulreap" / "read.md").read_text(encoding="utf-8")
    state = packet.split("## State outside the process", 1)[1].split("\n", 1)[1].split("\n## ", 1)[0]
    assert "store.py" in state and "mercy_auto_update" in state and "enabled" in state
    assert "survives a restart" in state


def test_a_card_without_a_topic_links_its_most_cited_file_the_same_way_every_time(tmp_path):
    from srlib.maprender import _link_style
    card = tmp_path / "soulreap" / "cards" / "x.md"
    style = _link_style(tmp_path, tmp_path / "soulreap", card, {}, "[[b.py::g]] [[a.py::f]]")
    assert style["subject"] == "a.py"


def test_packet_does_not_say_an_in_process_store_survives_a_restart(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "cache.py").write_text("class Cache:\n    def __init__(self):\n        self.d = {}\n\n"
                                   "    def get(self, k):\n        return self.d.get(k)\n\n"
                                   "    def put(self, k, v):\n        self.d[k] = v\n", encoding="utf-8")
    (root / "svc.py").write_text("from cache import Cache\n\ncache = Cache()\n\n\ndef a(k):\n    return cache.get(k)\n\n\n"
                                 "def b(k):\n    cache.put(k, {'x': 1})\n", encoding="utf-8")
    out = tmp_path / "map"
    mp.cmd_plan(root, out)
    state = (out / ".soulreap" / "read.md").read_text(encoding="utf-8").split("## State outside", 1)[1].split("\n", 1)[1].split("\n## ", 1)[0]
    assert "survives a restart" not in state and "svc.py" in state
