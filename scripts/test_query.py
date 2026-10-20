"""Tests for impact and citing sections."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from srlib import query as qy
from srlib.build import build_facts

FILES = {
    "app.py": "from store import save\n\n\ndef main():\n    save(1)\n",
    "store.py": "CACHE = {}\n\n\ndef save(v):\n    CACHE[v] = 1\n",
    "tests/test_store.py": "from store import save\n\n\ndef test_save():\n    save(2)\n",
}


def _setup(tmp_path: Path) -> tuple[Path, Path, dict]:
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    out = root / "soulreap"
    (out / "cards").mkdir(parents=True)
    (out / "cards" / "save.md").write_text(
        "# Save\n\n## Writes\n\n[[store.py::save]] writes CACHE.\n\n"
        "## Other\n\n> ⚠ stale (basis c1): main: +callee x\nUses [[app.py::main]].\n",
        encoding="utf-8",
    )
    return root, out, build_facts(root).files


def test_impact_of_symbol_names_dependents_tests_entries_writes_and_sections(tmp_path):
    root, out, facts = _setup(tmp_path)
    result = qy.impact(root, out, facts, "store.py::save")
    assert result["dependents"] == ["app.py"]
    assert result["tests"] == ["tests/test_store.py"]
    assert result["entry_points"] == ["app.py::main"]
    assert result["writes"] == ["store.py::save: CACHE"]
    assert result["sections"] == [{"doc": "cards/save.md", "section": "Writes", "status": "fresh"}]


def test_impact_of_file_covers_its_symbols(tmp_path):
    root, out, facts = _setup(tmp_path)
    assert qy.impact(root, out, facts, "store.py")["symbols"] == ["store.py::save"]


def test_citing_reports_stale_sections(tmp_path):
    _, out, _ = _setup(tmp_path)
    assert qy.citing(out, "app.py::main") == [{"doc": "cards/save.md", "section": "Other", "status": "stale"}]


def test_impact_unknown_target_raises_with_no_candidates(tmp_path):
    root, out, facts = _setup(tmp_path)
    with pytest.raises(LookupError):
        qy.impact(root, out, facts, "nope")


def test_format_impact_mentions_every_part(tmp_path):
    root, out, facts = _setup(tmp_path)
    text = qy.format_impact(qy.impact(root, out, facts, "store.py::save"))
    for needle in ("app.py", "tests/test_store.py", "app.py::main", "CACHE", "cards/save.md#Writes"):
        assert needle in text
