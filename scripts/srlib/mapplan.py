"""v10 map commands that prepare work for a reader: plan, explain, refresh, confirm, hook.

Each writes a *packet* — a short markdown file naming exactly what to read and what
must be cited, and the exact commands to run — so the reader copies a decision instead of
making one. Contracts reach the reader as absolute file paths.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from . import cards as _cards
from . import context as _context
from . import flow as _flow
from . import importance, mapdiff, rank
from . import swap as _swap
from .anchors import find_anchors, unwrap_cited
from .diagrams import group_databases
from .freshness import fresh_facts
from .paths import is_test_path, matches_any
from .query import map_documents
from .reach import linked
from .runroots import config_links, library_roots, run_roots, vendored
from .sections import heading_count, section_status, stamp
from .stores import store_library
from .symbols import callers, file_symbols, resolve_symbol, symbol_table
from .util import commit_label, git, load_json, render_lock, write_json, write_text_atomic

MAP_VERSION = 10
# Absolute, so a reader working inside the mapped repo (or a subagent) can open it.
CARD_CONTRACT = str(Path(__file__).resolve().parents[2] / "references" / "card.md")
OVERVIEW_CONTRACT = str(Path(__file__).resolve().parents[2] / "references" / "overview.md")
SCRIPT = str(Path(__file__).resolve().parents[1] / "soulreap.py")
CONFIRM_ARGS = '<doc> "<section>"'
SWAP_ARGS = '<card target> <shortlist target> --why "<one line>"'


def run_cmd(root: Path | str, cmd: str, rest: str = "", out: Path | str | None = None) -> str:
    """The exact command line for *cmd*: there is no `soulreap` on PATH, and `--root` defaults to
    the working directory, so a packet that says only `render` gets run from the wrong place.
    `--out` is spelled out only when the map does not live in the default `<root>/soulreap`."""
    root = Path(root).resolve()
    where = f" --out {Path(out).resolve()}" if out and Path(out).resolve() != root / "soulreap" else ""
    return f"uv run --script {SCRIPT} {cmd} --root {root}{where}" + (f" {rest}" if rest else "")
HOOK_MARK = "# soulreap-hook"


def _soulreap(out: Path) -> Path:
    return Path(out) / ".soulreap"


def _range_text(entry: dict) -> str:
    if entry["ranges"] is None:
        return f"- {entry['file']} (whole, {entry['lines']} lines)"
    spans = ", ".join(f"L{s}–{e} ({q})" for s, e, q in entry["ranges"])
    return f"- {entry['file']}: {spans}"


def _checklist_lines(items: list[dict]) -> list[str]:
    return [f"- [[{i['symbol']}]] {i['kind']}: {i['what']} (L{i['line']})" for i in items] or ["- (none)"]


def _tree(entries: list) -> dict:
    """Files and lines per directory — the Layout block of OVERVIEW."""
    tree: dict[str, dict] = {}
    for entry in entries:
        node = tree.setdefault(str(Path(entry.path).parent), {"files": 0, "lines": 0, "langs": []})
        node["files"] += 1
        node["lines"] += entry.lines
        if entry.lang and entry.lang not in node["langs"]:
            node["langs"].append(entry.lang)
    return dict(sorted(tree.items()))


def _non_code(entries: list) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for entry in entries:
        if entry.kind == "non_code":
            found.setdefault(entry.category, []).append(entry.path)
    return {key: sorted(paths) for key, paths in found.items()}


def _left_out(facts: dict, exclude, with_tests: bool) -> dict[str, list[str]]:
    """What the run never considered, by reason — a map that quietly drops a third of a repo
    reads as a map of the whole thing. Facts still cover these files."""
    code = sorted(rel for rel, blob in facts.items() if (blob or {}).get("lang") not in (None, "sql", "sqlite"))
    tests = [] if with_tests else [rel for rel in code if is_test_path(rel)]
    globbed = [rel for rel in code if rel not in tests and matches_any(rel, list(exclude))]
    return {"tests": tests, "excluded": globbed}


def cmd_plan(root: Path | str, out: Path | str, budget: int = rank.BUDGET_LINES,
             exclude=(), with_tests: bool = False) -> dict:
    """Rank, write plan.json and the reader packet `.soulreap/read.md`."""
    root, out = Path(root), Path(out)
    fresh = fresh_facts(root, out)
    facts = fresh["files"]
    exclude = tuple(exclude) + tuple(vendored(root, [e.path for e in fresh["entries"]]))
    # What runs: deploy files and console scripts first; without them, `__main__` guards.
    roots = [r for r in run_roots(root, [e.path for e in fresh["entries"]])
             if r["file"] in facts and not matches_any(r["file"], list(exclude))]
    if not roots:  # nothing deployed: guards and published packages; else entry-named files
        libraries = [r for r in library_roots(root, [e.path for e in fresh["entries"]]) if r["file"] in facts]
        roots = [{"file": rel, "symbol": None, "via": "__main__ guard or entry file"}
                 for rel in rank.default_roots(facts, fallback=not libraries)] + libraries
    # Modules a root loads through config (Dagster defs.yaml `type:`) count as its imports here.
    facts = linked(facts, config_links(root, [e.path for e in fresh["entries"]], facts, [r["file"] for r in roots]))
    # What carries the repo, by the study's score (sizes, use across files, reach, the docs vote).
    scores = importance.score_symbols(root, facts, rank.candidates(facts, exclude, with_tests),
                                      importance.doc_paths(e.path for e in fresh["entries"]))
    read = rank.read_set(facts, budget, exclude, with_tests, fresh["entries"], roots=[r["file"] for r in roots],
                         scores=scores)
    choice = _cards.choose(facts, read, max(rank.card_count(read), min(len(roots), rank.MAX_CARDS)), scores, roots,
                           explains=_cards.explainer(root, facts))
    previous = load_json(_soulreap(out) / "plan.json", {})
    table = symbol_table(facts)
    topics, kept_swap = _swap.reapply(choice["topics"], choice["fixed"], choice["shortlist"], previous,
                                      lambda target: target in table or target in facts)
    k = len(topics)  # promise only the cards there are topics for
    payload = {
        "map_version": MAP_VERSION,
        "state": _state(facts),
        "commit": git(root, "rev-parse", "--short", "HEAD"),
        "generated_at": date.today().isoformat(),
        "excluded_by": {"with_tests": with_tests, "skip_tests": not with_tests, "patterns": list(exclude)},
        "left_out": _left_out(facts, exclude, with_tests),
        "runs": roots,
        "read_set": read,
        "checklist": rank.checklist(facts, read),
        "cards": k,
        "topics": topics,
        "fixed_cards": choice["fixed"],
        "shortlist": [{**t, "reasons": (scores.get(t["target"]) or {}).get("reasons") or []}
                      for t in choice["shortlist"] if t["target"] not in {x["target"] for x in topics}],
        "look_here": _cards.look_here(facts, read, scores),
        "legacy": [name for name in ("files", "units") if (out / name).is_dir()],
        "context": {"searched": _context.searched(fresh["entries"]), "found": fresh["context"]},
        "cycles": fresh["cycles"],
        "tree": _tree(fresh["entries"]),
        "non_code": _non_code(fresh["entries"]),
        "secrets": {e.path: e.env_keys for e in fresh["entries"] if e.kind == "secret"},
    }
    if kept_swap:
        payload["swap"] = kept_swap
    payload["entries"] = mapdiff.entry_keys(facts, exclude, with_tests)
    # Cards written by `explain` keep their checklists across a re-plan, minus symbols that are gone.
    explained = {card: [i for i in items if i["symbol"] in table]
                 for card, items in (previous.get("explain") or {}).items()}
    if explained:
        payload["explain"] = explained
    docs = map_documents(out)
    cited = {a.key for doc in docs for a in find_anchors(unwrap_cited(doc.read_text(encoding="utf-8"), table))}
    if previous and cited:  # a map exists: say what is new since it, and hold the map to it
        # Judged under this run's exclusions: an `--exclude` added now hides what it names.
        update = mapdiff.since_plan(facts, {**previous, "excluded_by": payload["excluded_by"]}, cited, table,
                                    payload["checklist"])
        payload["update"] = update
        payload["checklist"] += [
            {"symbol": key, "kind": "entry", "what": "entry point", "line": (table.get(key) or {}).get("start", 0)}
            for key in update["entries"]
        ]
        write_text_atomic(_soulreap(out) / "update.md",
                          mapdiff.update_packet(update, CARD_CONTRACT, run_cmd(root, "render", out=out),
                                                run_cmd(root, "explain", "<target>", out=out)) + "\n")
    write_json(_soulreap(out) / "plan.json", payload)
    _write_read_packet(root, out, payload)
    if not (out / "OVERVIEW.md").exists():  # the reader fills a scaffold; it never writes one by hand
        from .maprender import cmd_render  # imported late: maprender imports this module

        cmd_render(root, out)
    return payload


def _write_read_packet(root: Path, out: Path, plan: dict) -> None:
    read = plan["read_set"]
    lines = [
        f"# Read packet — {root.name} (soulreap map v{MAP_VERSION})", "",
        f"Contract: `{CARD_CONTRACT}` — read it once, first.",
        f"Budget: {read['budget']} lines · reading {read['total_lines']}"
        + (" (whole non-test source)" if read["whole"] else " (ranked subset)"), "",
        "## What runs (from deploy files, console scripts or `__main__` guards) — start here", "",
        *([f"- {r['file']}" + (f" → `{r['symbol']}`" if r["symbol"] else "") + f" (via {r['via']})"
           for r in plan.get("runs") or []] or ["- (nothing declares how this repo runs)"]), "",
        "## State outside the process — what survives a restart (cards must not contradict it)", "",
        *(plan.get("state") or ["- (none found: no database, no store the code reads and writes)"]), "",
        "## Read these, in this order", "", *[_range_text(e) for e in read["files"]],
        *[f"- {c['file']} (config, {c['lines']} lines)" for c in read.get("configs") or []], "",
        "## Checklist — cite each as [[file::Symbol]] in the map, or list it under “Not covered” with a reason",
        "", *_checklist_lines(plan["checklist"]), "",
        "## Look here — what carries this repo, scored without an LLM (size, use across files, reach, "
        "what the docs talk about); read everything above, weigh these most", "",
        *([f"- `{i['symbol']}`" + (f" — {' · '.join(i['reasons'])}" if i["reasons"] else "")
           for i in plan.get("look_here") or []] or ["- (nothing scored)"]), "",
        f"## Cards to write ({plan['cards']}) — one file each, at exactly this path under `{out.resolve()}`; "
        "nothing else names them",
        "", *[f"- {t['kind']}: {t['target']} → `cards/{_slug(t['target'])}.md`"
              + (" (what runs — fixed)" if n < plan.get("fixed_cards", 0) else "")
              for n, t in enumerate(plan["topics"])], "",
        *_swap_lines(root, out, plan),
        "## Not read by this run (list under OVERVIEW → Not covered)", "",
        *([f"- {p}" for p in read["not_covered"]] or ["- (nothing — the whole source fits)"]), "",
        "## Other files — not code; grep one when a claim needs it, do not read it whole", "",
        *(_other_files(root, plan) or ["- (none)"]), "",
        "## After writing", "",
        f"1. `{(out / 'OVERVIEW.md').resolve()}` is already scaffolded: replace every `_Write this by hand — a parser "
        "cannot._` line and keep the `<!-- soulreap:* -->` blocks (rules: "
        f"`{OVERVIEW_CONTRACT}`), and write `{(out / 'diagrams' / 'architecture.dot').resolve()}` — the runtime "
        "picture, same contract. Write each card at its path above. Nothing else checks names, "
        "headings or length — there is no need to read soulreap's own code.",
        f"2. Run `{run_cmd(root, 'render', out=out)}` once. stdout is one JSON object. `strict: true` means an "
        "anchor names no symbol (`unresolved`) or a checklist item is uncited (`checklist_missing` — "
        "cite it, or list it under Not covered with a reason). Also fix `unwritten` (placeholders "
        "left) and `diagram_warnings` (a .dot that does not parse, dot source pasted as text). "
        "`overview_restored` names generated blocks you dropped that render put back, and a "
        "backticked anchor to a real symbol becomes a citation by itself. `stale` (sections whose "
        "code moved) is empty on a first map. Fix only what those name.",
        "3. `render` rewrites the files it reads — a line span after every anchor, a basis line under "
        "each section, a version mark on top — so re-read a file before you edit it again.",
    ]
    (_soulreap(out) / "read.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _swap_lines(root: Path, out: Path, plan: dict) -> list[str]:
    """The reader's one veto: after reading, before writing cards."""
    if plan.get("swap"):
        done = plan["swap"]
        return ["## Card swap — already used", "", f"- {done['in']} instead of {done['out']}: {done['why']}", ""]
    if not plan.get("shortlist") or len(plan["topics"]) <= plan.get("fixed_cards", 0):
        return []
    return [
        "## One card swap — optional, after reading and before writing cards", "",
        "If one of these matters more than a card above (not a fixed one), swap it — once:",
        f"`{run_cmd(root, 'swap', SWAP_ARGS, out=out)}`", "",
        *[f"- {t['kind']}: {t['target']} → `cards/{_slug(t['target'])}.md`"
          + (f" — {' · '.join(t['reasons'])}" if t.get("reasons") else "") for t in plan["shortlist"]], "",
    ]


def _state(facts: dict) -> list[str]:
    """Databases and key-value stores the code keeps state in, one line each, for the packet.

    Mercy's card said everything "dies with the process" while its vault is a SQLite file: the
    writer had no fact saying the store outlives the process, and a vault reads like a dict.
    """
    lines = [f"- database `{db['name']}`: {len(db['tables'])} table(s) declared in "
             + ", ".join(db["sources"][:3]) + (" …" if len(db["sources"]) > 3 else "")
             for db in group_databases(facts).values() if db["tables"]]
    for rel in sorted(facts):
        for store in [] if is_test_path(rel) else (facts[rel] or {}).get("stores") or []:
            fields = ", ".join(c["name"] for c in store.get("columns") or []) or "none seen"
            computed = store.get("key") or {}
            keys = (f" · keyed by `{computed['names'][0]}`" + (f": {computed['type']}" if computed.get("type") else "")
                    + (f", set in {', '.join(computed['writers'][:3])}" if computed.get("writers") else "")
                    if computed else "")
            library = store_library(facts[rel], store)
            where = (f"a `{library}` client, so it lives outside the process: it survives a restart unless the "
                     "code clears it" if library else "a class of this repo: read it before saying whether it persists")
            lines.append(f"- {rel}: `{store['binding']}` = `{store['ctor']}` (`{store['name']}`) — a key-value "
                         f"store read and written ({', '.join(store.get('verbs') or [])}); fields: {fields}{keys}. "
                         f"It is {where}.")
    return lines


def _other_files(root: Path, plan: dict) -> list[str]:
    """Non-code files the packet does not already list as configs, with their line counts."""
    listed = {c["file"] for c in plan["read_set"].get("configs") or []}
    found = []
    for rel in sorted({rel for group in (plan.get("non_code") or {}).values() for rel in group} - listed):
        try:
            count = len((root / rel).read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            continue  # binary or gone: nothing a grep would find
        found.append(f"- {rel} ({count} lines)")
    return found


_slug = _swap.slug


def cmd_explain(root: Path | str, out: Path | str, target: str) -> Path:
    """Packet for one on-demand card: the target, its callers and callees, their hazards."""
    root, out = Path(root), Path(out)
    facts = fresh_facts(root, out)["files"]
    table = symbol_table(facts)
    keys = resolve_symbol(table, target) if target not in facts else file_symbols(table, target)
    if not keys or (len(keys) > 1 and target not in facts):
        raise LookupError(keys)
    idx = _flow.build(root, facts)
    near: dict[str, str] = {}
    for key in keys:
        near[key] = "target"
        for hit in callers(root, facts, key, idx=idx):
            if not is_test_path(hit["caller"].split("::", 1)[0]):  # tests are never part of the map
                near.setdefault(hit["caller"], "caller")
        rel, qual = key.split("::", 1)
        if qual in idx.defs.get(rel, {}):
            for trel, tqual, _conf, _line, _text in _flow._children(idx, rel, qual):
                near.setdefault(f"{trel}::{tqual}", "callee")
    name = keys[0] if len(keys) == 1 else target
    card = f"cards/{_slug(name)}.md"
    items = [
        {"symbol": k, "kind": h["kind"], "what": h["what"], "line": h["line"]}
        for k in near if k in table for h in table[k]["hazards"] if h["kind"] in rank.CHECK_KINDS
    ]
    lines = [
        f"# Explain packet — {target}", "",
        f"Contract: `{CARD_CONTRACT}`. Write or update `{card}`"
        + (" (exists — update it, keep what is still true)." if (out / card).exists() else "."), "",
        "## Read these ranges", "",
        *[f"- {role}: [[{k}]] {table[k]['file']} L{table[k]['start']}–{table[k]['end']}"
          for k, role in near.items() if k in table], "",
        "## Checklist", "", *_checklist_lines(items), "",
        f"Then run `{run_cmd(root, 'render', out=out)}`.",
    ]
    # Recorded in plan.json so render holds the card to its checklist, like a map run's cards.
    plan = load_json(_soulreap(out) / "plan.json", {})
    plan.setdefault("explain", {})[card] = items
    write_json(_soulreap(out) / "plan.json", plan)
    path = _soulreap(out) / f"explain-{_slug(name)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def cmd_refresh(root: Path | str, out: Path | str) -> Path:
    """Packet listing every stale section, what moved, and the moved symbols' current ranges."""
    from .maprender import cmd_render  # render owns staleness; imported late to avoid a cycle

    root, out = Path(root), Path(out)
    stats = cmd_render(root, out)
    table = symbol_table(fresh_facts(root, out)["files"])
    lines = [f"# Refresh packet — {len(stats['stale'])} stale section(s)", "",
             "For each: read the ranges, then rewrite the section (and delete its basis line) or, "
             f"if the claim still holds, run `{run_cmd(root, 'confirm', CONFIRM_ARGS, out=out)}` — <doc> as "
             "named below, <section> the heading text without `#`.", ""]
    for item in stats["stale"]:
        lines += [f"## {item['doc']} — {item['section']}", ""]
        for key, what in item["changes"].items():
            row = table.get(key)
            where = f"{row['file']} L{row['start']}–{row['end']}" if row else "deleted"
            lines.append(f"- [[{key}]] — {', '.join(what)} — read {where}")
        lines.append("")
    path = _soulreap(out) / "refresh.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def cmd_confirm(root: Path | str, out: Path | str, doc: str, heading: str) -> bool:
    """Re-stamp one section as still true; False when the section is not in *doc*."""
    root, out = Path(root), Path(out)
    path = out / doc
    with render_lock(out):  # a hook or manual render must not interleave with this read-modify-write
        text = path.read_text(encoding="utf-8")
        if heading not in section_status(text):
            return False
        if heading_count(text, heading) > 1:
            raise LookupError(f"{doc} has {heading_count(text, heading)} sections titled {heading!r}; "
                              "rename one before confirming")
        table = symbol_table(fresh_facts(root, out)["files"])
        snapshots = load_json(_soulreap(out) / "basis.json", {})
        updated, _ = stamp(text, table, snapshots, commit_label(root), doc, force=frozenset({heading}))
        write_text_atomic(path, updated)
        write_json(_soulreap(out) / "basis.json", snapshots)
    return True
