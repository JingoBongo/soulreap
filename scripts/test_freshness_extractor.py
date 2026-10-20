"""The cache key for facts covers every module the extractor is made of."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib.freshness import extractor_digest

SRLIB = Path(__file__).parent / "srlib"


def test_a_change_in_any_srlib_module_changes_the_extractor_digest(tmp_path):
    copy = tmp_path / "srlib"
    shutil.copytree(SRLIB, copy, ignore=shutil.ignore_patterns("__pycache__"))
    before = extractor_digest(copy)
    for name in ("fingerprints.py", "hazard_names.py", "flow.py"):
        (copy / name).write_text((copy / name).read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        after = extractor_digest(copy)
        assert after != before, name
        before = after
