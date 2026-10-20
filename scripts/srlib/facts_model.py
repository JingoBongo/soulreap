"""The shape of what is extracted: `FileFacts`, its schema version and the triviality rule.

Every extractor returns a `FileFacts`, facts.json stores its `to_dict()`, and
`is_trivial` decides whether a file has anything a card could describe. Kept apart
from the extractors so each of them can import it without importing the others.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# v7: every callable carries callees, hazards and sfp (soulreap v10 staleness).
# v8: TS functions record `exported` (the importance score weighs a module's surface).
FACTS_VERSION = 8


TRIVIAL_MAX_LINES = 20


def is_trivial(blob) -> bool:
    """True when the parser found nothing a card could describe.

    Entity-based, never name-based. A ``__init__.py`` that re-exports carries
    imports and is not trivial; an empty ``conftest.py`` is. Three guards keep
    the rule from swallowing real code: a parse error is never trivial (the card
    has to say the file could not be read), and neither is anything longer than
    TRIVIAL_MAX_LINES — a shell script is a wall of statements with no entities,
    and it still does something worth writing down.
    """
    get = blob.get if hasattr(blob, "get") else lambda k, d=None: getattr(blob, k, d)
    if get("parse_error"):
        return False
    if (get("lines", 0) or 0) > TRIVIAL_MAX_LINES:
        return False
    return not any(
        get(key)
        for key in ("imports", "constants", "classes", "functions", "todos", "concurrency", "io", "recursive")
    )


@dataclass
class FileFacts:
    """Everything a parser can state about one file."""

    path: str
    lang: str | None = None
    sha: str = ""
    lines: int = 0
    doc: str | None = None
    # The rest of the module docstring, one entry per line. `doc` is the first line, so no
    # consumer has to change; this is what carries a key list or a stated contract.
    doc_more: list[str] = field(default_factory=list)
    imports: list[dict] = field(default_factory=list)
    # Dotted strings that may name a module of the repo (Django's INSTALLED_APPS / ROOT_URLCONF,
    # celery's include=[...]): the graph resolves the ones that do into dependencies.
    string_refs: list[str] = field(default_factory=list)
    constants: list[dict] = field(default_factory=list)
    classes: list[dict] = field(default_factory=list)
    functions: list[dict] = field(default_factory=list)
    todos: list[dict] = field(default_factory=list)
    concurrency: list[str] = field(default_factory=list)
    io: dict[str, list[str]] = field(default_factory=dict)
    recursive: list[str] = field(default_factory=list)
    calls: dict[str, list[str]] = field(default_factory=dict)
    shape_groups: list[dict] = field(default_factory=list)
    binaries: list[str] = field(default_factory=list)
    schema: dict = field(default_factory=lambda: {"tables": [], "targets": [], "unread": []})
    # Stores whose shape was OBSERVED rather than declared — kept apart from `schema`
    # on purpose, at every level down to the drawing, because the two are different
    # kinds of claim and a reader who cannot tell them apart is worse off.
    stores: list[dict] = field(default_factory=list)
    tasks: dict = field(default_factory=lambda: {"declared": [], "calls": []})
    parse_error: str | None = None
    module_hazards: list[dict] = field(default_factory=list)
    main_calls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serializable form used by facts.json."""
        return {
            "path": self.path, "lang": self.lang, "sha": self.sha, "lines": self.lines,
            "doc": self.doc, "doc_more": self.doc_more, "imports": self.imports, "constants": self.constants,
            "classes": self.classes, "functions": self.functions, "todos": self.todos,
            "concurrency": self.concurrency, "io": self.io, "recursive": self.recursive,
            "calls": self.calls,
            "shape_groups": self.shape_groups,
            "binaries": self.binaries, "schema": self.schema, "stores": self.stores,
            "tasks": self.tasks, "parse_error": self.parse_error, "module_hazards": self.module_hazards,
            "main_calls": self.main_calls, "string_refs": self.string_refs,
        }
