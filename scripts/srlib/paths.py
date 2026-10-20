"""Path classification shared by ``plan`` and the diagrams.

``is_test_path`` used to live in ``diagrams.py``, where it decided which files
were too hub-like to draw. ``plan`` needs the same rule to answer a different
question — which files the map leaves out — and two copies of a rule
this fiddly drift apart. One definition, one place to fix it.
"""

from __future__ import annotations

import fnmatch
import re
from functools import lru_cache
from pathlib import Path

TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs", "testing"}
TEST_FILE_RE = re.compile(r"^(tests|test_.+|.+_test|.+\.test|.+\.spec)\.\w+$")  # tests.py: Django


def is_test_path(path: str) -> bool:
    """True for test files, by directory or by filename convention.

    Covers pytest, go, jest/vitest, rspec and the Java ``src/test/java`` layout.
    It does not cover ``*.Tests/`` (C#), ``e2e``/``cypress`` directories, or Rust
    ``#[cfg(test)]`` modules living inside a production file — no path rule can
    see that last one. Those are what ``--exclude`` is for.
    """
    parts = Path(path).parts
    if any(part in TEST_DIRS for part in parts[:-1]):
        return True
    return bool(TEST_FILE_RE.match(parts[-1]))


@lru_cache(maxsize=32)
def _compiled(patterns: tuple[str, ...]):
    """gitignore matcher for *patterns*, or None when pathspec is absent."""
    try:
        from pathspec import PathSpec
    except ImportError:  # pragma: no cover - only without the declared dependency
        return None
    # "gitignore" is the current factory name; older pathspec only knows
    # "gitwildmatch", which newer versions accept but deprecate loudly.
    for style in ("gitignore", "gitwildmatch"):
        try:
            return PathSpec.from_lines(style, patterns)
        except (KeyError, ValueError, LookupError):
            continue
    return None


def _fnmatch_any(path: str, patterns: tuple[str, ...]) -> bool:
    """Fallback matcher. A pattern with no ``/`` matches the basename at any depth."""
    name = path.rsplit("/", 1)[-1]
    for pattern in patterns:
        target = path if "/" in pattern else name
        if fnmatch.fnmatch(target, pattern) or fnmatch.fnmatch(target, pattern.rstrip("/") + "/*"):
            return True
    return False


def matches_any(path: str, patterns) -> bool:
    """True when *path* matches any gitignore-style pattern in *patterns*.

    Same syntax as ``.gitignore`` so the flag behaves the way the reader already
    expects: ``tests/**`` for a subtree, ``*_test.go`` at any depth.
    """
    compiled = tuple(patterns or ())
    if not compiled:
        return False
    spec = _compiled(compiled)
    if spec is not None:
        return spec.match_file(path)
    return _fnmatch_any(path, compiled)
