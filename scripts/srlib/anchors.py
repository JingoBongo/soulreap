"""`[[file::Symbol]]` anchors: what prose cites, and where render keeps line numbers live.

Writers cite symbols, never lines. `render` turns each citation into a link a person can read
and click — `[check_cycle](../../mercy.py#L290-L370 "mercy.py::check_cycle")`: a short name,
the file and current span in the target, the full key in the title — on every run, so a line
number in the map is never stale, and a citation whose symbol is gone is reported instead of
silently pointing at the wrong code. The bare `[[…]]` form (and the older `[[…]] (L1-2)`) is
still read, so a writer or verifier may always add a citation that way.
Anchors inside fenced code blocks or `inline code` are examples, not citations, and are left alone.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath
from urllib.parse import quote

ANCHOR = re.compile(r"\[\[(?P<file>[^\]\s:][^\]:]*?)::(?P<sym>[A-Za-z_][\w.]*)\]\](?: \(L\d+(?:–\d+)?\))?")
# Only a link that points at lines (`#L3` / `#L3-L9`) is a citation: `[Vec](url "std::vector")` is not.
LINK = (r'\[(?P<text>(?:\\.|[^\]\n\\])+)\]\((?P<href>[^)\s]+#L\d+(?:-L\d+)?) '
        r'"(?P<lfile>[^"\s:][^":]*?)::(?P<lsym>[A-Za-z_][\w.]*)"\)')
CITE = re.compile(f"{ANCHOR.pattern}|{LINK}")
FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Anchor:
    """One citation of a symbol."""

    file: str
    symbol: str

    @property
    def key(self) -> str:
        return f"{self.file}::{self.symbol}"


def _prose_chunks(text: str) -> list[tuple[bool, str]]:
    """Split *text* into (is_prose, chunk) by fenced code blocks, keeping every character."""
    chunks: list[tuple[bool, str]] = []
    inside, current = False, []
    for line in text.splitlines(keepends=True):
        if FENCE.match(line):
            if not inside:
                chunks.append((True, "".join(current)))
                current = [line]
            else:
                current.append(line)
                chunks.append((False, "".join(current)))
                current = []
            inside = not inside
            continue
        current.append(line)
    chunks.append((not inside, "".join(current)))
    return chunks


INLINE_CODE = re.compile(r"(`[^`\n]*`)")


def _outside_inline_code(chunk: str) -> list[tuple[bool, str]]:
    """Split a prose chunk into (is_prose, part): text inside `backticks` is an example."""
    return [(i % 2 == 0, part) for i, part in enumerate(INLINE_CODE.split(chunk))]


def _parts(match: re.Match) -> tuple[str, str]:
    return (match["file"], match["sym"]) if match["file"] else (match["lfile"], match["lsym"])


def _link(key: str, row: dict, link: dict) -> str:
    """`[name](relative/path#L1-L9 "file::Symbol")`; the file is named only when it is not the card's own."""
    file, symbol = key.split("::", 1)
    path = PurePosixPath(file)
    module = path.parent.name if path.name == "__init__.py" and path.parent.name else path.stem
    name = symbol if file == link.get("subject") else f"{module}.{symbol}"
    if "__" in name or "*" in name:  # `Foo.__init__` would render with a bold "init"
        name = name.replace("_", "\\_").replace("*", "\\*")
    lines = f"L{row['start']}" if row["start"] == row["end"] else f"L{row['start']}-L{row['end']}"
    # Both sides against the repo root, never the process cwd: `--out` may sit outside the repo.
    base = link.get("root") or "/"
    # `root`/`doc_dir` come from `Path.as_posix()`, which on Windows is `C:/…`. posixpath does not
    # treat a drive letter as a root, so joining it a second time nested the repo inside itself and
    # the hop count ran past the repo — the OS decides the relative path, the URL renders as posix.
    start = os.path.join(base, link.get("doc_dir") or ".")
    try:
        href = PurePath(os.path.relpath(os.path.join(base, file), start)).as_posix()
    except ValueError:  # different Windows drives — no relative URL exists
        href = PurePath(os.path.join(base, file)).as_posix()
    return f'[{name}]({quote(href, safe="/.-_~")}#{lines} "{key}")'


def find_anchors(text: str) -> list[Anchor]:
    """Every anchor in prose, in order of appearance (fenced and inline code excluded)."""
    return [
        Anchor(*_parts(m))
        for prose, chunk in _prose_chunks(text) if prose
        for live, part in _outside_inline_code(chunk) if live
        for m in CITE.finditer(part)
    ]


BACKTICKED = re.compile(r"`(\[\[[^\]\n]+\]\](?: \(L\d+(?:–\d+)?\))?)`")


def unwrap_cited(text: str, table: dict[str, dict]) -> str:
    """`` `[[file::Symbol]]` `` → `[[file::Symbol]]` when the key names a real symbol.

    Backticks make an anchor an example, which is right for `[[file::Symbol]]` and wrong for
    the reader who wrote every citation in code style — a weak model does that by habit,
    and every one of them then reads as uncited. A key that resolves was meant as a
    citation; one that does not stays an example. Fenced blocks are never touched.
    """
    def unwrap(match: re.Match) -> str:
        inner = ANCHOR.fullmatch(match[1])
        return match[1] if inner and f"{inner['file']}::{inner['sym']}" in table else match[0]

    return "".join(BACKTICKED.sub(unwrap, chunk) if prose else chunk for prose, chunk in _prose_chunks(text))


def rewrite(text: str, table: dict[str, dict], link: dict | None = None) -> tuple[str, list[str]]:
    """Every prose anchor re-rendered with its current span; unresolved keys returned.

    With *link* (`{"doc_dir": the document's folder, relative to the repo root, "subject": the
    card's own file or None}`) a citation becomes a readable link; without it, `[[key]] (L1-2)`.
    """
    unresolved: set[str] = set()

    def render(match: re.Match) -> str:
        key = "::".join(_parts(match))
        row = table.get(key)
        if row is None:
            unresolved.add(key)
            return f"[[{key}]]"
        if link is not None:
            return _link(key, row, link)
        span = f"L{row['start']}" if row["start"] == row["end"] else f"L{row['start']}–{row['end']}"
        return f"[[{key}]] ({span})"

    out = "".join(
        "".join(CITE.sub(render, part) if live else part for live, part in _outside_inline_code(chunk))
        if prose else chunk
        for prose, chunk in _prose_chunks(text)
    )
    return out, sorted(unresolved)
