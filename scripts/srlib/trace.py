"""Stack trace → slice seeds: the repo's own frames, deepest first.

Python tracebacks list the outermost call first; Node stacks list the innermost first. Frames
outside the repo (stdlib, node internals, site-packages) are dropped; each remaining frame maps
to the innermost function or method whose line range covers it.
"""

from __future__ import annotations

import re
from pathlib import Path

PY_FRAME = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)')
NODE_FRAME = re.compile(r"^\s*at (?:.*? \()?(?P<path>[^()\s]+?):(?P<line>\d+):\d+\)?\s*$", re.MULTILINE)


def _relative(path: str, root: Path, files: set[str]) -> str | None:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            rel = candidate.resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            return None
    else:
        rel = candidate.as_posix().removeprefix("./")
    return rel if rel in files else None


def _innermost(table: dict[str, dict], rel: str, line: int) -> str | None:
    covering = [(row["end"] - row["start"], key) for key, row in table.items()
                if row["file"] == rel and row["kind"] in ("function", "method") and row["start"] <= line <= row["end"]]
    return min(covering)[1] if covering else None


def parse_trace(text: str, root: Path | str, table: dict[str, dict]) -> list[str]:
    """Seeds for the repo frames in *text*, deepest first, each once."""
    root = Path(root)
    files = {row["file"] for row in table.values()}
    frames = [(m["path"], int(m["line"])) for m in PY_FRAME.finditer(text)][::-1]
    frames += [(m["path"], int(m["line"])) for m in NODE_FRAME.finditer(text)]
    seeds: list[str] = []
    for path, line in frames:
        rel = _relative(path, root, files)
        key = _innermost(table, rel, line) if rel else None
        if key and key not in seeds:
            seeds.append(key)
    return seeds
