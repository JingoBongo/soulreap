"""What the code gained since the last plan that the map does not mention yet.

`refresh` handles prose that went stale; this handles prose that was never written. A route
added after mapping has no section citing it, so nothing goes stale and the map silently never
learns about it. Compared against the last `plan.json`: entry points it did not list, hazards it
did not put on the checklist (in files it read), files it did not consider, and citations whose
symbol is gone.
"""

from __future__ import annotations

from . import flow as _flow
from . import rank
from .paths import is_test_path, matches_any

NOTHING = {"entries": [], "hazards": [], "files": [], "removed": []}


def entry_keys(facts: dict, exclude=(), with_tests: bool = True) -> list[str]:
    """Every entry point as `file::name` — what plan.json records so the next run can diff it.

    Files the plan excludes (`--exclude`, tests unless `--with-tests`) contribute none.
    """
    return sorted(
        f"{e['file']}::{e['name']}" for e in _flow.entry_points(facts)
        if (with_tests or not is_test_path(e["file"])) and not matches_any(e["file"], list(exclude))
    )


def since_plan(facts: dict, plan: dict, cited: set[str], table: dict[str, dict],
               checklist: list[dict] | None = None) -> dict:
    """New, uncited things since *plan*; all lists empty when there is no plan run to compare with.

    *checklist* is the one render will hold the map to when it is not the one *plan*'s read set
    gives (a re-plan picks a new read set): new hazards are its items the old plan did not list.
    """
    if not plan or "read_set" not in plan:  # nothing, or only `explain` records: no plan run yet
        return {key: [] for key in NOTHING}
    read = plan.get("read_set") or {}
    excluded = plan.get("excluded_by") or {}
    known_entries = set(plan.get("entries") or [])
    entries = [key for key in entry_keys(facts, excluded.get("patterns") or [], bool(excluded.get("with_tests")))
               if "entries" in plan and key not in known_entries and key not in cited]
    known_files = ({f["file"] for f in read.get("files") or []} | set(read.get("not_covered") or [])
                   | {c["file"] for c in read.get("configs") or []})
    files = [rel for rel in rank.candidates(facts, excluded.get("patterns") or [], bool(excluded.get("with_tests")))
             if rel not in known_files]
    planned = {(i["symbol"], i["kind"], i["what"]) for i in plan.get("checklist") or []}
    if checklist is None:
        checklist = rank.checklist(facts, {"files": [f for f in read.get("files") or [] if f["file"] in facts]})
    hazards = [item for item in checklist
               if (item["symbol"], item["kind"], item["what"]) not in planned and item["symbol"] not in cited]
    removed = sorted(key for key in cited if key not in table)
    return {"entries": entries, "hazards": hazards, "files": files, "removed": removed}


def update_packet(update: dict, contract: str, render: str = "render", explain: str = "explain <target>") -> str:
    """The reader's task when a map exists: only what is new, then `render`."""
    def block(title: str, lines: list[str]) -> list[str]:
        return [f"## {title}", "", *(lines or ["- (none)"]), ""]

    return "\n".join([
        "# Update packet — what is new since the last plan", "",
        f"Contract: `{contract}`. The map exists: add only these (a line in OVERVIEW, a section in the",
        f"card that owns the area, or `{explain}` for a new card), then `{render}`.",
        "Do not follow read.md: the map exists, re-mapping from scratch throws it away.", "",
        *block("New entry points (not cited yet)", [f"- [[{key}]]" for key in update["entries"]]),
        *block("New hazards in files the map covers",
               [f"- [[{i['symbol']}]] {i['kind']}: {i['what']} (L{i['line']})" for i in update["hazards"]]),
        *block("New files (not considered by the last plan)", [f"- {rel}" for rel in update["files"]]),
        *block("Citations to symbols that no longer exist — rewrite or remove those sections",
               [f"- [[{key}]]" for key in update["removed"]]),
    ])
