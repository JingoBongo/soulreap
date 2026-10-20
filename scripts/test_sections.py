"""Tests for section-level staleness: basis, banners, rewrite detection, confirm."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib.sections import basis_keys, section_status, stamp


def _row(sfp: str, callees=("g",), hazards=()) -> dict:
    return {"sfp": sfp, "signature": "f()", "callees": list(callees), "hazards": list(hazards)}


DOC = "# Card\n\n## Loop\n\nRuns [[a.py::f]] every tick.\n\n## Store\n\nWrites in [[b.py::save]].\n"


def test_stamp_first_time_adds_basis_and_reports_stamped():
    table = {"a.py::f": _row("aaa"), "b.py::save": _row("bbb")}
    snaps: dict = {}
    text, report = stamp(DOC, table, snaps, "c0ffee1", "cards/x.md")
    assert "<!-- basis@c0ffee1: a.py::f=aaa -->" in text
    assert "<!-- basis@c0ffee1: b.py::save=bbb -->" in text
    assert report.stamped == ["cards/x.md#Loop", "cards/x.md#Store"] and report.stale == []
    assert basis_keys(text) == {"a.py::f@aaa", "b.py::save@bbb"} and "a.py::f@aaa" in snaps


def test_stamp_unchanged_facts_is_idempotent():
    table = {"a.py::f": _row("aaa"), "b.py::save": _row("bbb")}
    snaps: dict = {}
    once, _ = stamp(DOC, table, snaps, "c1", "d")
    twice, report = stamp(once, table, snaps, "c2", "d")
    assert twice == once and report.stamped == [] and report.stale == []


def test_stamp_changed_callee_banners_only_the_citing_section():
    snaps: dict = {}
    once, _ = stamp(DOC, {"a.py::f": _row("aaa"), "b.py::save": _row("bbb")}, snaps, "c1", "d")
    moved = {"a.py::f": _row("zzz", callees=("g", "notify")), "b.py::save": _row("bbb")}
    text, report = stamp(once, moved, snaps, "c2", "d")
    assert "> ⚠ stale (basis c1): f: +callee notify" in text
    assert [s["section"] for s in report.stale] == ["Loop"]
    assert section_status(text)["Loop"]["stale"] and not section_status(text)["Store"]["stale"]


def test_stamp_deleted_symbol_is_reported_as_deleted():
    snaps: dict = {}
    once, _ = stamp(DOC, {"a.py::f": _row("aaa"), "b.py::save": _row("bbb")}, snaps, "c1", "d")
    _, report = stamp(once, {"a.py::f": _row("aaa")}, snaps, "c2", "d")
    assert report.stale[0]["changes"] == {"b.py::save": ["deleted"]}


def test_stamp_section_rewritten_with_new_anchors_is_restamped_without_banner():
    snaps: dict = {}
    once, _ = stamp(DOC, {"a.py::f": _row("aaa"), "b.py::save": _row("bbb")}, snaps, "c1", "d")
    table = {"a.py::f": _row("zzz"), "b.py::save": _row("bbb"), "a.py::g": _row("ggg")}
    stale, _ = stamp(once, table, snaps, "c2", "d")
    # The rewrite protocol: new text, and the section's basis line deleted.
    rewritten = stale.replace("Runs [[a.py::f]] every tick.", "Runs [[a.py::f]] then [[a.py::g]].")
    rewritten = "\n".join(line for line in rewritten.split("\n")
                          if not line.startswith("<!-- basis@c1: a.py::f="))
    text, report = stamp(rewritten, table, snaps, "c3", "d")
    assert "⚠ stale" not in text.split("## Store")[0]
    assert "<!-- basis@c3: a.py::f=zzz a.py::g=ggg -->" in text and report.stale == []


def test_stamp_force_confirms_a_stale_section():
    snaps: dict = {}
    once, _ = stamp(DOC, {"a.py::f": _row("aaa"), "b.py::save": _row("bbb")}, snaps, "c1", "d")
    table = {"a.py::f": _row("zzz"), "b.py::save": _row("bbb")}
    stale, _ = stamp(once, table, snaps, "c2", "d")
    text, _ = stamp(stale, table, snaps, "c3", "d", force=frozenset({"Loop"}))
    assert "⚠ stale" not in text and "<!-- basis@c3: a.py::f=zzz -->" in text


def test_stamp_without_commit_uses_worktree_label():
    text, _ = stamp(DOC, {"a.py::f": _row("aaa"), "b.py::save": _row("bbb")}, {}, "worktree", "d")
    assert "<!-- basis@worktree: a.py::f=aaa -->" in text


def test_stamp_section_without_anchors_gets_no_basis():
    text, report = stamp("# T\n\n## Intro\n\nPlain words.\n", {}, {}, "c1", "d")
    assert "basis@" not in text and report.stamped == []


def test_changes_names_a_changed_literal():
    from srlib.sections import changes

    old = {"signature": "f()", "callees": [], "hazards": [], "literals": ["command git reset --soft"]}
    row = {"signature": "f()", "callees": [], "hazards": [],
           "literals": [{"kind": "command", "text": "git reset --hard"}]}
    assert changes(old, row) == ["+command git reset --hard", "-command git reset --soft"]


def test_stamp_heading_inside_a_code_fence_is_not_a_section():
    doc = "# C\n\n## Run\n\n```bash\n# run it\nmake\n```\n\nThen [[a.py::store]] persists.\n"
    snaps: dict = {}
    once, report = stamp(doc, {"a.py::store": _row("s1")}, snaps, "c1", "d")
    assert "```bash\n# run it\nmake\n```" in once and report.stamped == ["d#Run"]
    assert once.rstrip().endswith("<!-- basis@c1: a.py::store=s1 -->")
    _, moved = stamp(once, {"a.py::store": _row("s2", callees=("g", "x"))}, snaps, "c2", "d")
    assert [s["section"] for s in moved.stale] == ["Run"]


def test_stamp_headings_inside_generated_blocks_belong_to_the_enclosing_section():
    doc = ("# O\n\n## Where things are\n\n<!-- soulreap:inventory -->\n## Layout\n\n```\n. 3 files\n```\n"
           "<!-- /soulreap:inventory -->\n\nThe loop lives in [[a.py::f]].\n")
    snaps: dict = {}
    once, report = stamp(doc, {"a.py::f": _row("aaa")}, snaps, "c1", "d")
    assert report.stamped == ["d#Where things are"]
    text, moved = stamp(once, {"a.py::f": _row("zzz", callees=("g", "n"))}, snaps, "c2", "d")
    assert [s["section"] for s in moved.stale] == ["Where things are"]
    assert text.index("> ⚠ stale") < text.index("<!-- soulreap:inventory -->")


def test_stamp_body_change_only_is_a_soft_stale_that_confirm_clears():
    row = {**_row("aaa"), "bfp": "b1"}
    snaps: dict = {}
    once, _ = stamp(DOC, {"a.py::f": row, "b.py::save": _row("bbb")}, snaps, "c1", "d")
    assert "a.py::f=aaa.b1" in once
    moved = {"a.py::f": {**row, "bfp": "b2"}, "b.py::save": _row("bbb")}
    text, report = stamp(once, moved, snaps, "c2", "d")
    assert report.stale[0]["changes"] == {"a.py::f": ["body changed"]}
    confirmed, report = stamp(text, moved, snaps, "c3", "d", force=frozenset({"Loop"}))
    assert "⚠ stale" not in confirmed and "a.py::f=aaa.b2" in confirmed


def test_stamp_adding_an_anchor_does_not_clear_a_stale_claim():
    snaps: dict = {}
    once, _ = stamp(DOC, {"a.py::f": _row("aaa"), "b.py::save": _row("bbb")}, snaps, "c1", "d")
    table = {"a.py::f": _row("zzz"), "b.py::save": _row("bbb"), "a.py::g": _row("ggg")}
    stale, _ = stamp(once, table, snaps, "c2", "d")
    touched = stale.replace("Runs [[a.py::f]] every tick.", "Runs [[a.py::f]] every tick, see [[a.py::g]].")
    text, report = stamp(touched, table, snaps, "c3", "d")
    assert [s["section"] for s in report.stale] == ["Loop"] and "a.py::g=ggg" in text


def test_stamp_unresolved_anchor_does_not_restamp_its_siblings():
    snaps: dict = {}
    doc = "# C\n\n## S\n\nUses [[a.py::f]] and [[gone.py::x]].\n"
    once, _ = stamp(doc, {"a.py::f": _row("aaa")}, snaps, "c1", "d")
    _, report = stamp(once, {"a.py::f": _row("zzz", callees=("g", "h"))}, snaps, "c2", "d")
    assert report.stamped == [] and [s["section"] for s in report.stale] == ["S"]


def test_stamp_banner_names_the_commit_an_added_anchor_was_stamped_at():
    snaps: dict = {}
    once, _ = stamp(DOC, {"a.py::f": _row("aaa"), "b.py::save": _row("bbb")}, snaps, "c1", "d")
    table = {"a.py::f": _row("aaa"), "b.py::save": _row("bbb"), "a.py::g": _row("ggg")}
    added, _ = stamp(once.replace("every tick.", "every tick, see [[a.py::g]]."), table, snaps, "c2", "d")
    assert "a.py::g=ggg@c2" in added and basis_keys(added) >= {"a.py::g@ggg", "a.py::f@aaa"}
    table["a.py::g"] = _row("hhh", callees=("g", "h"))
    text, _ = stamp(added, table, snaps, "c3", "d")
    banner = next(line for line in text.split("\n") if line.startswith("> ⚠ stale"))
    assert "(basis c2)" in banner and "c1" not in banner


def test_stamp_removing_the_anchor_of_a_stale_claim_keeps_it_stale():
    snaps: dict = {}
    once, _ = stamp(DOC, {"a.py::f": _row("aaa"), "b.py::save": _row("bbb")}, snaps, "c1", "d")
    table = {"a.py::f": _row("zzz", callees=("g", "h")), "b.py::save": _row("bbb"), "a.py::g": _row("ggg")}
    stale, _ = stamp(once, table, snaps, "c2", "d")
    unanchored = stale.replace("Runs [[a.py::f]] every tick.", "Runs [[a.py::g]] every tick.")
    text, report = stamp(unanchored, table, snaps, "c3", "d")
    assert [s["section"] for s in report.stale] == ["Loop"]
    assert "anchor removed" in report.stale[0]["changes"]["a.py::f"]


def test_stamp_removing_the_anchor_of_a_deleted_symbol_clears_it():
    snaps: dict = {}
    once, _ = stamp(DOC, {"a.py::f": _row("aaa"), "b.py::save": _row("bbb")}, snaps, "c1", "d")
    table = {"b.py::save": _row("bbb"), "a.py::g": _row("ggg")}
    stale, _ = stamp(once, table, snaps, "c2", "d")
    fixed = stale.replace("Runs [[a.py::f]] every tick.", "Runs [[a.py::g]] every tick.")
    _, report = stamp(fixed, table, snaps, "c3", "d")
    assert report.stale == []
