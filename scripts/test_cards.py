"""Tests for card choice by importance, the look-here list, and the reader's one swap."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from srlib import cards
from srlib import rank as rk
from srlib.build import build_facts
from srlib.mapplan import cmd_plan
from srlib.swap import cmd_swap, reapply


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def _body(n: int) -> str:
    return "".join(f"    v{i} = {i}\n" for i in range(n))


def _score(key: str, council: float) -> tuple[str, dict]:
    return key, {"council": council, "reasons": [f"r-{key}"]}


FACTS = {"a.py": {"lang": "python", "lines": 40, "functions": [
            {"name": "big", "start_line": 1, "end_line": 20}, {"name": "mid", "start_line": 21, "end_line": 30}]},
         "b.py": {"lang": "python", "lines": 10, "functions": [{"name": "low", "start_line": 1, "end_line": 9}]}}
READ = {"files": [{"file": "a.py", "lines": 40, "ranges": None, "score": 1.0},
                  {"file": "b.py", "lines": 10, "ranges": None, "score": 1.0}]}
SCORES = dict([_score("a.py::big", 0.9), _score("a.py::mid", 0.8), _score("b.py::low", 0.1)])


def test_cards_go_one_per_file_before_a_file_gets_a_second():
    got = cards.choose(FACTS, READ, 2, SCORES)
    assert [t["target"] for t in got["topics"]] == ["a.py::big", "b.py::low"]
    assert [t["target"] for t in got["shortlist"]] == ["a.py::mid"] and got["fixed"] == 0


def test_a_second_card_per_file_only_when_files_run_out():
    got = cards.choose(FACTS, READ, 3, SCORES)
    assert [t["target"] for t in got["topics"]] == ["a.py::big", "b.py::low", "a.py::mid"]


def test_run_roots_are_fixed_and_come_first():
    got = cards.choose(FACTS, READ, 2, SCORES, roots=[{"file": "b.py", "symbol": "low"}])
    assert got["fixed"] == 1 and [t["target"] for t in got["topics"]] == ["b.py::low", "a.py::big"]


def test_look_here_lists_the_most_important_read_symbols_with_reasons():
    assert cards.look_here(FACTS, READ, SCORES, n=2) == [
        {"symbol": "a.py::big", "reasons": ["r-a.py::big"]}, {"symbol": "a.py::mid", "reasons": ["r-a.py::mid"]}]


def test_a_symbol_outside_the_read_ranges_gets_no_card():
    read = {"files": [{"file": "a.py", "lines": 10, "ranges": [[21, 30, "mid"]], "score": 1.0}]}
    assert [t["target"] for t in cards.choose(FACTS, read, 3, SCORES)["topics"]] == ["a.py::mid"]


def test_topics_pick_what_carries_the_repo_over_a_tiny_helper(tmp_path):
    root = _repo(tmp_path, {"README.md": "Settles invoices.\n",
                            "app.py": f"def settle_invoices():\n{_body(30)}\n\ndef pad():\n    return 1\n"})
    facts = build_facts(root).files
    assert rk.topics(facts, rk.read_set(facts), 1, root=root)[0]["target"] == "app.py::settle_invoices"


def _plan(tmp_path: Path) -> tuple[Path, Path, dict]:
    files = {"main.py": "from work import run\n\n\ndef main():\n    run()\n\n\n"
                        "if __name__ == '__main__':\n    main()\n"}
    for i in range(4):
        files[f"m{i}.py"] = f"def job{i}():\n{_body(150 + 3 * i)}"  # 600+ lines: two cards or more
    files["work.py"] = "".join(f"from m{i} import job{i}\n" for i in range(4)) + "\n\ndef run():\n" + "".join(
        f"    job{i}()\n" for i in range(4))
    root = _repo(tmp_path / "repo", files)
    out = root / "soulreap"
    return root, out, cmd_plan(root, out)


def test_plan_packet_points_at_what_matters_and_offers_one_swap(tmp_path):
    _root, out, plan = _plan(tmp_path)
    packet = (out / ".soulreap" / "read.md").read_text(encoding="utf-8")
    assert plan["fixed_cards"] == 1 and plan["shortlist"] and plan["look_here"]
    assert "## Look here" in packet and "(what runs — fixed)" in packet
    assert "## One card swap" in packet and " swap --root " in packet


def test_swap_replaces_one_card_once_and_never_a_fixed_one(tmp_path):
    _root, out, plan = _plan(tmp_path)
    fixed, swappable = plan["topics"][0]["target"], plan["topics"][-1]["target"]
    alt = plan["shortlist"][0]["target"]
    with pytest.raises(LookupError, match="what runs"):
        cmd_swap(out, fixed, alt, "because")
    with pytest.raises(LookupError, match="not on the shortlist"):
        cmd_swap(out, swappable, "nowhere.py::x", "because")
    with pytest.raises(LookupError, match="why"):
        cmd_swap(out, swappable, alt, "  ")
    result = cmd_swap(out, swappable, alt, "it holds the retry rule")
    saved = json.loads((out / ".soulreap" / "plan.json").read_text())
    assert alt in [t["target"] for t in saved["topics"]] and swappable not in [t["target"] for t in saved["topics"]]
    assert result["write"].startswith("cards/") and saved["swap"]["why"] == "it holds the retry rule"
    with pytest.raises(LookupError, match="one swap per plan"):
        cmd_swap(out, alt, saved["shortlist"][0]["target"], "again")


def test_a_new_plan_keeps_the_readers_swap(tmp_path):
    root, out, plan = _plan(tmp_path)
    swappable, alt = plan["topics"][-1]["target"], plan["shortlist"][0]["target"]
    cmd_swap(out, swappable, alt, "it holds the retry rule")
    again = cmd_plan(root, out)
    assert alt in [t["target"] for t in again["topics"]] and again["swap"]["in"] == alt
    assert "## Card swap — already used" in (out / ".soulreap" / "read.md").read_text(encoding="utf-8")


def test_reapply_drops_a_swap_whose_target_is_gone():
    topics = [{"kind": "flow", "target": "a.py::x"}, {"kind": "flow", "target": "a.py::y"}]
    swap = {"swap": {"out": "a.py::y", "in": "b.py::gone", "why": "w"}}
    assert reapply(topics, 1, [], swap, lambda t: t != "b.py::gone") == (topics, None)


def test_swap_cli_refuses_with_exit_2_and_swaps_with_exit_0(tmp_path, capsys):
    import soulreap

    root, _out, plan = _plan(tmp_path)
    fixed, swappable = plan["topics"][0]["target"], plan["topics"][-1]["target"]
    alt = plan["shortlist"][0]["target"]
    assert soulreap.main(["swap", "--root", str(root), fixed, alt, "--why", "x"]) == 2
    assert soulreap.main(["swap", "--root", str(root), swappable, alt, "--why", "it holds the rule"]) == 0
    assert "swapped: write" in capsys.readouterr().out


def test_a_candidate_the_cards_already_explain_waits_its_turn():
    chain = {"a.py::big": {("a.py", "big"), ("a.py", "mid")}, "a.py::mid": {("a.py", "mid")},
             "b.py::low": {("b.py", "low")}}
    got = cards.choose(FACTS, READ, 2, SCORES, explains=lambda t: chain[t["target"]])
    assert [t["target"] for t in got["topics"]] == ["a.py::big", "b.py::low"]
    one_file = {"files": READ["files"][:1]}
    got = cards.choose(FACTS, one_file, 2, SCORES, explains=lambda t: chain[t["target"]])
    assert [t["target"] for t in got["topics"]] == ["a.py::big", "a.py::mid"]  # nothing else left


def test_reapply_reports_no_swap_when_the_swapped_in_card_is_already_chosen():
    topics = [{"kind": "flow", "target": "a.py::x"}, {"kind": "flow", "target": "b.py::y"}]
    swap = {"swap": {"out": "b.py::y", "in": "a.py::x", "why": "w"}}
    assert reapply(topics, 1, [], swap, lambda t: True) == (topics, None)


def test_the_shortlist_prefers_candidates_the_cards_do_not_already_explain():
    chain = {"a.py::big": {("a.py", "big"), ("a.py", "mid")}, "a.py::mid": {("a.py", "mid")},
             "b.py::low": {("b.py", "low")}}
    got = cards.choose(FACTS, READ, 1, SCORES, explains=lambda t: chain[t["target"]])
    assert [t["target"] for t in got["shortlist"]] == ["b.py::low", "a.py::mid"]


def test_a_swap_rewrites_the_packet(tmp_path):
    root, out, plan = _plan(tmp_path)
    swappable, alt = plan["topics"][-1]["target"], plan["shortlist"][0]["target"]
    from srlib.swap import cmd_swap as swap_with_root
    swap_with_root(out, swappable, alt, "it holds the rule", root=root)
    assert "## Card swap — already used" in (out / ".soulreap" / "read.md").read_text(encoding="utf-8")


def test_a_plan_from_before_fixed_cards_existed_is_refused(tmp_path):
    _root, out, plan = _plan(tmp_path)
    path = out / ".soulreap" / "plan.json"
    saved = json.loads(path.read_text())
    del saved["fixed_cards"]
    path.write_text(json.dumps(saved))
    with pytest.raises(LookupError, match="run `plan` again"):
        cmd_swap(out, plan["topics"][-1]["target"], plan["shortlist"][0]["target"], "w")


CLASSY = {"s.py": {"lang": "python", "lines": 40, "functions": [], "classes": [{"name": "Store", "methods": [
              {"name": "__init__", "start_line": 2, "end_line": 9},
              {"name": "sync", "start_line": 10, "end_line": 30}]}]},
          "b.py": {"lang": "python", "lines": 10, "functions": [{"name": "low", "start_line": 1, "end_line": 9}]}}
CLASSY_READ = {"files": [{"file": "s.py", "lines": 40, "ranges": None, "score": 1.0},
                         {"file": "b.py", "lines": 10, "ranges": None, "score": 1.0}]}
CLASSY_SCORES = dict([_score("s.py::Store.__init__", 0.9), _score("s.py::Store.sync", 0.8), _score("b.py::low", 0.1)])


def test_a_methods_card_is_about_its_class():
    got = cards.choose(CLASSY, CLASSY_READ, 2, CLASSY_SCORES)
    assert got["topics"] == [{"kind": "component", "target": "s.py::Store"}, {"kind": "flow", "target": "b.py::low"}]


def test_a_class_is_explained_only_when_most_of_its_methods_are():
    chain = {"s.py::Store": {("s.py", "Store.__init__"), ("s.py", "Store.sync")},
             "b.py::low": {("s.py", "Store.sync")}}
    roots = [{"file": "b.py", "symbol": "low"}]
    got = cards.choose(CLASSY, CLASSY_READ, 2, CLASSY_SCORES, roots=roots, explains=lambda t: chain[t["target"]])
    assert {"kind": "component", "target": "s.py::Store"} in got["topics"]  # sync explained, __init__ not


def test_explainer_gives_a_class_card_its_methods(tmp_path):
    root = _repo(tmp_path, {"s.py": "class Store:\n    def __init__(self):\n        self.x = 1\n\n"
                                    "    def sync(self):\n        return self.x\n"})
    explains = cards.explainer(root, build_facts(root).files)
    methods = explains({"kind": "component", "target": "s.py::Store"})
    assert methods == {("s.py", "Store.__init__"), ("s.py", "Store.sync")}


def test_reapply_keeps_the_kind_of_a_swapped_in_class_card():
    topics = [{"kind": "flow", "target": "a.py::x"}, {"kind": "flow", "target": "b.py::y"}]
    swap = {"swap": {"out": "b.py::y", "in": "s.py::Store", "kind": "component", "why": "w"}}
    got, kept = reapply(topics, 1, [], swap, lambda t: True)
    assert got[1] == {"kind": "component", "target": "s.py::Store"} and kept
