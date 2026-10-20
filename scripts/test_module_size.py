"""Every module stays under the 400-line limit (coding-style rule): split along a seam instead."""

from __future__ import annotations

from pathlib import Path

LIMIT = 400


def test_every_srlib_module_is_under_the_line_limit():
    srlib = Path(__file__).parent / "srlib"
    sizes = {p.name: len(p.read_text(encoding="utf-8").splitlines()) for p in sorted(srlib.glob("*.py"))}
    over = {name: n for name, n in sizes.items() if n > LIMIT}
    assert over == {}, f"over {LIMIT} lines: {over}"
