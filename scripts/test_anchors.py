"""Tests for symbol anchors: parse, resolve, rewrite with live line numbers."""

from __future__ import annotations

import os
import posixpath
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from srlib.anchors import find_anchors, rewrite

TABLE = {
    "mercy.py::check_cycle": {"start": 290, "end": 370},
    "ui.py::Handler.do_GET": {"start": 12, "end": 12},
}


def test_rewrite_adds_current_line_span():
    text, unresolved = rewrite("see [[mercy.py::check_cycle]] and [[ui.py::Handler.do_GET]]", TABLE)
    assert text == "see [[mercy.py::check_cycle]] (L290–370) and [[ui.py::Handler.do_GET]] (L12)"
    assert unresolved == []


def test_rewrite_is_idempotent_and_updates_a_moved_span():
    once, _ = rewrite("[[mercy.py::check_cycle]]", TABLE)
    assert rewrite(once, TABLE)[0] == once
    moved = {"mercy.py::check_cycle": {"start": 300, "end": 380}}
    assert rewrite(once, moved)[0] == "[[mercy.py::check_cycle]] (L300–380)"


def test_rewrite_unresolved_anchor_is_reported_and_left_bare():
    text, unresolved = rewrite("[[gone.py::f]] (L1–2)", TABLE)
    assert text == "[[gone.py::f]]" and unresolved == ["gone.py::f"]


def test_find_anchors_reads_bare_and_rendered_forms():
    keys = [a.key for a in find_anchors("[[a.py::f]] then [[b.py::C.m]] (L3–9)")]
    assert keys == ["a.py::f", "b.py::C.m"]


def test_rewrite_inside_code_fence_is_left_alone():
    src = "real [[mercy.py::check_cycle]]\n```\nexample [[x.py::y]]\n```\n"
    text, unresolved = rewrite(src, TABLE)
    assert "example [[x.py::y]]\n" in text and unresolved == []
    assert [a.key for a in find_anchors(src)] == ["mercy.py::check_cycle"]


def test_anchor_inside_inline_code_is_an_example_not_a_citation():
    src = "Cite as `[[file::symbol]]`, e.g. [[mercy.py::check_cycle]]."
    text, unresolved = rewrite(src, TABLE)
    assert unresolved == [] and "`[[file::symbol]]`" in text
    assert [a.key for a in find_anchors(src)] == ["mercy.py::check_cycle"]


LINKED = {"doc_dir": "soulreap/cards", "subject": "mercy.py"}


def test_render_writes_a_link_with_a_short_name_and_the_key_in_its_title():
    text, _ = rewrite("see [[mercy.py::check_cycle]] and [[ui.py::Handler.do_GET]]", TABLE, link=LINKED)
    assert text == ('see [check_cycle](../../mercy.py#L290-L370 "mercy.py::check_cycle") and '
                    '[ui.Handler.do_GET](../../ui.py#L12 "ui.py::Handler.do_GET")')


def test_a_link_is_read_back_as_its_anchor_and_follows_the_code_when_it_moves():
    once, _ = rewrite("[[mercy.py::check_cycle]]", TABLE, link=LINKED)
    assert [a.key for a in find_anchors(once)] == ["mercy.py::check_cycle"]
    assert rewrite(once, TABLE, link=LINKED)[0] == once
    moved = {"mercy.py::check_cycle": {"start": 300, "end": 380}}
    assert "#L300-L380" in rewrite(once, moved, link=LINKED)[0]


def test_an_old_rendered_anchor_becomes_a_link_and_a_dead_link_is_reported():
    text, _ = rewrite("[[mercy.py::check_cycle]] (L1–2)", TABLE, link=LINKED)
    assert text.startswith("[check_cycle](../../mercy.py#L290-L370")
    gone, unresolved = rewrite('[f](../../gone.py#L1 "gone.py::f")', TABLE, link=LINKED)
    assert (gone, unresolved) == ("[[gone.py::f]]", ["gone.py::f"])


def test_a_package_init_is_named_by_its_package():
    table = {"src/cdp/mcp/__init__.py::build": {"start": 3, "end": 3}}
    text, _ = rewrite("[[src/cdp/mcp/__init__.py::build]]", table, link={"doc_dir": "soulreap", "subject": None})
    assert text == '[mcp.build](../src/cdp/mcp/__init__.py#L3 "src/cdp/mcp/__init__.py::build")'


def test_a_path_with_parentheses_survives_a_second_render():
    table = {"app/(auth)/login.py::f": {"start": 1, "end": 3}}
    link = {"doc_dir": "soulreap", "subject": None}
    once, _ = rewrite("[[app/(auth)/login.py::f]]", table, link=link)
    assert [a.key for a in find_anchors(once)] == ["app/(auth)/login.py::f"]
    assert rewrite(once, table, link=link)[0] == once


def test_an_out_folder_outside_the_repo_links_back_into_it():
    link = {"root": "/home/u/repo", "doc_dir": "/tmp/out/cards", "subject": None}
    text, _ = rewrite("[[x/y.py::f]]", {"x/y.py::f": {"start": 1, "end": 1}}, link=link)
    assert "(../../../home/u/repo/x/y.py#L1 " in text


@pytest.mark.skipif(os.name != "nt", reason="drive-letter paths only resolve through ntpath")
def test_a_windows_drive_root_links_back_to_the_repo_root():
    """`as_posix()` gives `C:/…` on Windows; posixpath never treats a drive letter as a root,
    so the hop count used to run past the repo (`../../../../…`). The link must land on the
    file next to the repo root on every platform."""
    link = {"root": "C:/Users/u/repo", "doc_dir": "C:/Users/u/repo/soulreap/cards", "subject": None}
    text, _ = rewrite("[[ui.py::f]]", {"ui.py::f": {"start": 1, "end": 1}}, link=link)
    href = text.split("](", 1)[1].split("#", 1)[0]
    assert "C:" not in href  # never an absolute drive path in the URL
    lands = posixpath.normpath(posixpath.join(link["doc_dir"], href))
    assert lands == "C:/Users/u/repo/ui.py"


def test_an_ordinary_link_with_a_double_colon_title_is_left_alone():
    text = '[Vec](https://doc "std::vector")'
    assert rewrite(text, TABLE, link=LINKED) == (text, [])
    assert find_anchors(text) == []


def test_a_dunder_name_does_not_render_bold():
    table = {"m.py::Foo.__init__": {"start": 2, "end": 4}}
    text, _ = rewrite("[[m.py::Foo.__init__]]", table, link={"doc_dir": ".", "subject": "m.py"})
    assert text.startswith(r"[Foo.\_\_init\_\_](m.py#L2-L4 ")
    assert [a.key for a in find_anchors(text)] == ["m.py::Foo.__init__"]
