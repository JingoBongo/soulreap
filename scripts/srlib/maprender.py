"""v10 render: anchors, section staleness, the checklist, INDEX and OVERVIEW's generated blocks.

Seconds, no LLM. It never deletes a document and never rewrites an OVERVIEW that has
no markers; a legacy (v9 or earlier) tree is reported, not touched.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import mapdiff
from . import overview as _ov
from .anchors import find_anchors, rewrite, unwrap_cited
from .diagrams import _context_edges, context_dot
from .drawing import _write_erds, draw_all, draw_hand_written
from .erd_dot import context_dot_svg
from .freshness import fresh_facts
from .mapplan import MAP_VERSION, _slug
from .query import is_current_overview, map_documents
from .sections import basis_keys, section_status, stamp
from .symbols import symbol_table
from .util import commit_label, head_commit, load_json, render_lock, write_json, write_text_atomic

VERSION_MARK = re.compile(r"<!-- soulreap map v(\d+) -->")


def _mark_version(text: str) -> tuple[str, int | None]:
    """Stamp a document with the map version it was written under; return an older one if found.

    The mark goes on the first line — before any heading, so `sections.stamp` treats it as
    preamble and never moves it relative to a basis line.
    """
    match = VERSION_MARK.search(text)
    if match is None:
        return f"<!-- soulreap map v{MAP_VERSION} -->\n" + text, None
    found = int(match.group(1))
    return text, (found if found != MAP_VERSION else None)


def _checklist(plan: dict) -> list[dict]:
    """The map run's checklist plus every `explain` card's, without duplicates."""
    items = list(plan.get("checklist") or [])
    items += [item for card in (plan.get("explain") or {}).values() for item in card]
    return list({(i["symbol"], i["kind"], i["what"]): i for i in items}.values())


def _legacy_files(out: Path) -> list[str]:
    """Generated files an older (v9 or earlier) map left behind: never overwritten, only reported (spec §7).

    v9 wrote the same `<!-- soulreap:* -->` markers, so markers alone do not make an OVERVIEW current:
    v10's has a `## Not covered` section, its INDEX a card table, its manifest a `map_version`.
    """
    legacy = []
    overview = out / "OVERVIEW.md"
    if overview.exists() and not is_current_overview(overview):
        legacy.append("OVERVIEW.md")
    index = out / "INDEX.md"
    if index.exists() and "| Card | Cites | Status |" not in index.read_text(encoding="utf-8"):
        legacy.append("INDEX.md")
    manifest = out / "manifest.json"
    if manifest.exists() and "map_version" not in load_json(manifest, {}):
        legacy.append("manifest.json")
    return legacy


def _write_context(out: Path, facts: dict, evidence: list) -> dict:
    """The context picture, drawn for one external system or more; nothing found → no picture, a note.

    v9 measured why one system is still worth a picture (an asyncpg in requirements.txt is
    the whole database story of a repo) and why "nothing detected" must not be drawn: a box
    saying so reads as an answer, while every address may arrive through config.
    """
    diagrams = out / "diagrams"
    diagrams.mkdir(parents=True, exist_ok=True)
    dot, stats = context_dot(facts, evidence)
    stats["svg_warnings"] = []
    if stats.get("context_systems", 0) > 0:
        (diagrams / "context.dot").write_text(dot, encoding="utf-8")
        edges, systems, _depth = _context_edges(facts, evidence)
        svg = context_dot_svg(edges, systems)
        if svg:
            (diagrams / "context.svg").write_text(svg, encoding="utf-8")
        else:
            (diagrams / "context.svg").unlink(missing_ok=True)
            stats["svg_warnings"].append("context.svg: not drawn — graphviz is unavailable; read diagrams/context.dot")
    else:
        (diagrams / "context.dot").unlink(missing_ok=True)
        (diagrams / "context.svg").unlink(missing_ok=True)
    return stats


def _write_index(out: Path, facts: dict, plan: dict, cards: list[Path], cited: dict[str, set[str]],
                 root: Path) -> None:
    read = {e["file"]: ("whole" if e["ranges"] is None else "partial")
            for e in (plan.get("read_set") or {}).get("files") or []}
    rows = [
        f"# Index — {len(facts)} files · {len(cards)} cards", "",
        f"Commit `{head_commit(root) or 'n/a'}` · generated {date.today().isoformat()}", "",
        "## Reading this map", "",
        "1. `OVERVIEW.md` · 2. this index · 3. `soulreap find / skeleton / flow / callers / impact / writes` · "
        "4. a card · 5. the source", "",
        "## Cards", "", "| Card | Cites | Status |", "| --- | --- | --- |",
    ]
    for card in cards:
        status = section_status(card.read_text(encoding="utf-8"))
        stale = [title for title, info in status.items() if info["stale"]]
        cites = sorted({k.split("::", 1)[0] for info in status.values() for k in info["cites"]})
        rows.append(f"| [{card.stem}](cards/{card.name}) | {', '.join(cites) or '—'} | "
                    f"{'stale: ' + ', '.join(stale) if stale else 'fresh'} |")
    rows += ["", "## Files", "", "| File | Lang | Lines | Read | Cited by | Role |",
             "| --- | --- | --- | --- | --- | --- |"]
    for rel in sorted(facts):
        blob = facts[rel] or {}
        if blob.get("lang") in (None, "sql", "sqlite"):
            continue
        rows.append(f"| {rel} | {blob.get('lang')} | {blob.get('lines', 0)} | {read.get(rel, '—')} | "
                    f"{', '.join(sorted(cited.get(rel, set()))) or '—'} | {(blob.get('doc') or '').strip()[:120]} |")
    write_text_atomic(out / "INDEX.md", "\n".join(rows) + "\n")


PASTED_DOT = re.compile(r"^\s*(?:```|~~~)[ \t]*(?:dot|graphviz|gv)?[ \t]*\n\s*<?\s*(?:strict\s+)?(di)?graph\s+(\w+)",
                        re.MULTILINE | re.IGNORECASE)


def _pasted_dot(text: str, rel: str) -> list[str]:
    """A diagram's dot source pasted into a document shows as code in every viewer (v9 maps did
    this); it belongs in `diagrams/<name>.dot`, which render draws and pastes as a picture."""
    return [f"{rel}: dot source pasted as text — save it as diagrams/{m[2]}.dot and remove the block; "
            "render draws it and pastes the picture" for m in PASTED_DOT.finditer(text)]


def _link_style(root: Path, out: Path, doc: Path, plan: dict, text: str) -> dict:
    """How `rewrite` links citations from *doc*: its folder relative to the repo, and its own file.

    A card's own file is its topic's (from plan.json), else the file it cites most; links to it
    show the bare symbol, links elsewhere `module.symbol`. OVERVIEW has no file of its own.
    """
    style = {"root": root.resolve().as_posix(), "doc_dir": doc.parent.resolve().as_posix(), "subject": None}
    if doc.parent.name != "cards":
        return style
    topics = {_slug(t["target"]): t["target"].split("::")[0] for t in plan.get("topics") or []}
    files = [a.file for a in find_anchors(text)]
    # sorted first: a tie must break the same way on every render, or the link text churns
    return {**style, "subject": topics.get(doc.stem) or (max(sorted(set(files)), key=files.count) if files else None)}


def cmd_render(root: Path | str, out: Path | str, diagrams: str = "default") -> dict:
    """Refresh facts, re-anchor and re-stamp every document, rebuild INDEX and OVERVIEW blocks."""
    root, out = Path(root), Path(out)
    with render_lock(out):
        return _render_locked(root, out, diagrams)


def _render_locked(root: Path, out: Path, diagrams: str) -> dict:
    fresh = fresh_facts(root, out)
    facts = fresh["files"]
    table = symbol_table(facts)
    plan = load_json(out / ".soulreap" / "plan.json", {})
    snapshots = load_json(out / ".soulreap" / "basis.json", {})
    commit = commit_label(root)
    out.mkdir(parents=True, exist_ok=True)
    drawn = _write_context(out, facts, fresh["context"])
    if diagrams == "all":
        extra = draw_all(out, facts)
        drawn["svg_warnings"] += extra.pop("svg_warnings", [])
        drawn.update(extra)
    erd_stats = _write_erds(out, facts)
    drawn.update(erd_stats)
    if diagrams != "all":  # draw_all already drew them
        drawn["diagram_warnings"] = draw_hand_written(out / "diagrams")
    else:
        drawn["diagram_warnings"] = [w for w in drawn["svg_warnings"] if w.split(":")[0].endswith(".dot")]

    overview = out / "OVERVIEW.md"
    stale, stamped, unresolved, old_contract = [], [], set(), []
    cited_by_file: dict[str, set[str]] = {}
    cited: set[str] = set()
    used: set[str] = set()
    # An older skill's OVERVIEW is somebody's work: never re-anchored, re-stamped or
    # version-marked — only reported, below, as legacy.
    legacy_files = _legacy_files(out)
    cards = sorted((out / "cards").glob("*.md"))
    current_overview = is_current_overview(overview)
    docs = map_documents(out)
    for doc in docs:
        rel = doc.relative_to(out).as_posix()
        text = doc.read_text(encoding="utf-8")
        drawn["diagram_warnings"] += _pasted_dot(text, rel)
        text, missing = rewrite(unwrap_cited(text, table), table, _link_style(root, out, doc, plan, text))
        unresolved |= set(missing)
        text, report = stamp(text, table, snapshots, commit, rel)
        text, older = _mark_version(text)
        if older is not None:
            old_contract.append({"doc": rel, "version": older})
        stale += report.stale
        stamped += report.stamped
        for anchor in find_anchors(text):
            cited.add(anchor.key)
            if doc != overview:
                cited_by_file.setdefault(anchor.file, set()).add(doc.stem)
        used |= basis_keys(text)
        if text != doc.read_text(encoding="utf-8"):
            write_text_atomic(doc, text)
    write_json(out / ".soulreap" / "basis.json", {k: v for k, v in snapshots.items() if k in used})

    missing_items = [i for i in _checklist(plan) if i["symbol"] not in cited]
    stats = {"stale_count": len(stale), "schema_unread": erd_stats.get("schema_unread") or []}
    blocks = {
        "stamp": _ov.stamp(plan, len(facts), sum((b or {}).get("lines", 0) for b in facts.values())),
        "inventory": _ov.inventory(plan),
        "diagrams": _ov.diagrams(out / "diagrams"),
        "coverage": _ov.map_coverage(plan, [c.stem for c in cards], missing_items),
        "verdicts": _ov.map_verdicts(plan, stats),
    }
    overview_missing: list[str] = []
    overview_restored: list[str] = []
    if not overview.exists():
        write_text_atomic(overview, _ov.map_scaffold(root.name, blocks))
    elif current_overview:
        text = overview.read_text(encoding="utf-8")
        updated, overview_restored = _ov.update(text, blocks, restore=True)
        if updated != text:
            write_text_atomic(overview, updated)
    else:
        overview_missing = _ov.update(overview.read_text(encoding="utf-8"), blocks)[1]
    if "INDEX.md" not in legacy_files:
        _write_index(out, facts, plan, cards, cited_by_file, root)
    if "manifest.json" not in legacy_files:
        write_json(out / "manifest.json", {
            "map_version": MAP_VERSION,
            "commit": head_commit(root),
            "generated_at": date.today().isoformat(),
        })
    unwritten = [d.relative_to(out).as_posix() for d in map_documents(out)
                 if _ov.PROSE in d.read_text(encoding="utf-8")]
    return {
        **drawn,
        "documents": len(map_documents(out)),
        "stale": stale,
        "stamped": stamped,
        "unresolved": sorted(unresolved),
        "checklist_missing": missing_items,
        "unwritten": unwritten,
        "legacy": [name for name in ("files", "units") if (out / name).is_dir()] + legacy_files,
        "overview_missing": overview_missing,
        "overview_restored": overview_restored,
        "old_contract": old_contract,
        "swap": plan.get("swap"),
        "strict": bool(unresolved or missing_items),
    }


def cmd_status(root: Path | str, out: Path | str) -> dict:
    """Read-only freshness check: what render would report, with nothing written anywhere.

    Step 0 of every skill invocation and the post-commit hook run this, so asking "is the map
    fresh?" never dirties a working tree; `render` is what writes banners and spans.
    """
    root, out = Path(root), Path(out)
    facts = fresh_facts(root, out, write=False)["files"]
    table = symbol_table(facts)
    plan = load_json(out / ".soulreap" / "plan.json", {})
    snapshots = dict(load_json(out / ".soulreap" / "basis.json", {}))
    legacy_files = _legacy_files(out)
    docs = map_documents(out)
    stale, unresolved, cited = [], set(), set()
    for doc in docs:
        raw = doc.read_text(encoding="utf-8")
        text, missing = rewrite(unwrap_cited(raw, table), table, _link_style(root, out, doc, plan, raw))
        unresolved |= set(missing)
        text, report = stamp(text, table, snapshots, commit_label(root), doc.relative_to(out).as_posix())
        stale += report.stale
        cited |= {a.key for a in find_anchors(text)}
    missing_items = [i for i in _checklist(plan) if i["symbol"] not in cited]
    since = mapdiff.since_plan(facts, plan, cited, table)
    return {
        "since_plan": since,
        "stale": stale,
        "unresolved": sorted(unresolved),
        "checklist_missing": missing_items,
        "legacy": [name for name in ("files", "units") if (out / name).is_dir()] + legacy_files,
        "clean": not (stale or unresolved or missing_items or any(since.values())),
    }
