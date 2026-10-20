#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pathspec>=0.12",
#   "tree-sitter-language-pack>=0.9",
#   "pygraphviz>=2.0",
# ]
# ///
"""soulreap — the deterministic half of the codebase-map skill (v10).

Query (no LLM, always fresh):  find · skeleton · flow · callers · impact · writes · delta · slice
Map (prepares and checks LLM work):  plan · render · explain · refresh · confirm · hook

    uv run --script scripts/soulreap.py impact --root /path/to/repo store.py::save
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from srlib.deltacmd import format_delta, run_delta
from srlib.facts import FACTS_VERSION, FileFacts, extract_python, extract_source
from srlib.freshness import fresh_facts
from srlib.graph import build_graph
from srlib.install import cmd_install
from srlib.mapplan import MAP_VERSION, cmd_confirm, cmd_explain, cmd_plan, cmd_refresh
from srlib.maprender import cmd_render, cmd_status
from srlib.precommit import brief_status, run_precommit
from srlib.query import citing, format_impact, impact
from srlib.querycmd import cmd_find, cmd_flow, cmd_skeleton
from srlib.revisions import RevisionError
from srlib.slice import DEFAULT_BUDGET, build_slice, slice_seeds
from srlib.storewrites import merged_writes
from srlib.swap import cmd_swap
from srlib.symbols import callers, resolve_symbol, symbol_table, writes
from srlib.trace import parse_trace
from srlib.util import quiet_native_noise
from srlib.walk import FileEntry, classify, walk

__all__ = [
    "FACTS_VERSION",
    "MAP_VERSION",
    "FileEntry",
    "FileFacts",
    "build_graph",
    "classify",
    "cmd_confirm",
    "cmd_explain",
    "cmd_find",
    "cmd_flow",
    "cmd_plan",
    "cmd_refresh",
    "cmd_render",
    "cmd_skeleton",
    "extract_python",
    "extract_source",
    "walk",
]


def _preflight() -> None:
    """Warn when a declared dependency is absent, i.e. we ran outside `uv`.

    walk() already warns about a missing pathspec. tree-sitter has no such
    notice: its absence silently empties the Facts and Entities sections of
    every non-Python skeleton, leaving the map nothing to anchor to.
    Checked with find_spec, not an import: loading the parser pack is slow.
    """
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        print(
            "soulreap: tree-sitter-language-pack is not installed — non-Python files "
            "will report no entities and no facts, and the map will have nothing to "
            "anchor to. Run this script through `uv run --script` so its "
            "declared dependencies are present.",
            file=sys.stderr,
        )



def _one(table: dict, target: str) -> str | None:
    keys = resolve_symbol(table, target)
    if len(keys) == 1:
        return keys[0]
    print(f"{target}: " + ("no such symbol" if not keys else "ambiguous — " + ", ".join(keys)), file=sys.stderr)
    return None


def _not_found(target: str, err: LookupError) -> int:
    candidates = err.args[0] if err.args else []
    print(f"{target}: " + ("ambiguous — " + ", ".join(candidates) if candidates else "no such file or symbol"),
          file=sys.stderr)
    return 2


def _slice(root: Path, out: Path, args: argparse.Namespace) -> int:
    if (args.target is None) == (args.trace is None):
        print("slice: give a target or --trace, not both and not neither", file=sys.stderr)
        return 2
    facts = fresh_facts(root, out)["files"]
    table = symbol_table(facts)
    if args.trace is not None:
        trace = sys.stdin if args.trace == "-" else open(args.trace, encoding="utf-8", errors="replace")  # noqa: SIM115
        with trace:
            text = trace.read()
        seeds = parse_trace(text, root, table)
        if not seeds:
            print("slice: the trace has no frame inside the repo", file=sys.stderr)
            return 2
        label = f"trace ({len(seeds)} frame(s))"
    else:
        try:
            seeds = slice_seeds(table, facts, args.target)
        except LookupError as err:
            return _not_found(args.target, err)
        label = args.target
    print(build_slice(root, facts, seeds, args.budget, label=label)["text"], end="")
    return 0


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".")
    common.add_argument("--out", default=None, help="map directory (default <root>/soulreap)")
    parser = argparse.ArgumentParser(prog="soulreap", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    plan = sub.add_parser("plan", parents=[common], help="rank the read set, write the reader packet")
    plan.add_argument("--budget", type=int, default=3000)
    plan.add_argument("--exclude", action="append", default=[])
    plan.add_argument("--with-tests", action="store_true")
    # Pre-v10 flag: tests are skipped by default now; accepted so old invocations keep working.
    plan.add_argument("--skip-tests", action="store_true", help=argparse.SUPPRESS)
    render = sub.add_parser("render", parents=[common], help="anchors, staleness, INDEX, OVERVIEW blocks")
    render.add_argument("--diagrams", choices=["default", "all"], default="default")
    for name in ("skeleton", "flow", "callers", "impact", "writes", "explain"):
        sub.add_parser(name, parents=[common]).add_argument("target")
    sub.choices["flow"].add_argument("--dot", action="store_true")
    sub.choices["flow"].add_argument("--brief", action="store_true", help="cap the chain for pasting into a card")
    find = sub.add_parser("find", parents=[common])
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=40)
    status = sub.add_parser("status", parents=[common], help="read-only freshness check (writes nothing)")
    status.add_argument("--brief", action="store_true", help="one line, nothing when the map is fresh")
    sub.add_parser("refresh", parents=[common], help="packet for every stale section")
    confirm = sub.add_parser("confirm", parents=[common], help="re-stamp a section that is still true")
    confirm.add_argument("doc")
    confirm.add_argument("heading")
    install = sub.add_parser("install", parents=[common],
                             help="set the repo up: pre-commit check + AGENTS.md block (idempotent)")
    install.add_argument("--hook-only", action="store_true", help="the git hook, no AGENTS.md block")
    install.add_argument("--check", action="store_true", help="report what is missing or outdated; change nothing")
    install.add_argument("--uninstall", action="store_true", help="remove what install added, nothing else")
    sub.add_parser("hook", parents=[common], help="same as install --hook-only")
    sub.add_parser("precommit", parents=[common], help="what the git hook runs: delta --staged + status --brief")
    swap = sub.add_parser("swap", parents=[common], help="the reader's one card swap: a card for a shortlist entry")
    swap.add_argument("card")
    swap.add_argument("replacement")
    swap.add_argument("--why", required=True)
    delta = sub.add_parser("delta", parents=[common],
                           help="symbol changes between revisions, risk tiers, tests to run")
    delta.add_argument("--base", default=None, help="compare from merge-base(BASE, head); default HEAD")
    delta.add_argument("--head", default=None, help="a commit to compare to; default the work tree")
    delta.add_argument("--staged", action="store_true", help="compare the index (what the next commit holds)")
    delta.add_argument("--json", action="store_true")
    slc = sub.add_parser("slice", parents=[common],
                         help="the code around a symbol, file or stack trace, in a token budget")
    slc.add_argument("target", nargs="?")
    slc.add_argument("--trace", default=None, help="a Python/Node stack trace file, or - for stdin")
    slc.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one subcommand; exit 0 on success, 1 when render is strict-dirty, 2 on bad input."""
    _preflight()
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    out = Path(args.out).resolve() if args.out else root / "soulreap"

    if args.cmd == "plan":
        with quiet_native_noise():  # a first plan renders the OVERVIEW scaffold, and graphviz is noisy
            payload = cmd_plan(root, out, args.budget, args.exclude, args.with_tests)
        read = payload["read_set"]
        print(f"read set: {read['total_lines']} lines in {len(read['files'])} file(s)"
              f"{' (whole)' if read['whole'] else ''}; checklist {len(payload['checklist'])}; "
              f"cards {payload['cards']}; not covered {len(read['not_covered'])}"
              + (f"; LEGACY {payload['legacy']}" if payload["legacy"] else ""))
        left = payload["left_out"]
        if left["tests"] or left["excluded"]:
            print(f"left out: {len(left['tests'])} test file(s) — tests, never part of the map"
                  + (f"; {len(left['excluded'])} excluded (--exclude, or a generated UI kit)"
                     if left["excluded"] else ""))
        if payload.get("update"):  # one packet, never both: read.md would have a literal model re-map
            upd = payload["update"]
            print(f"map exists — new since last plan: {len(upd['entries'])} entry point(s), {len(upd['hazards'])} "
                  f"hazard(s), {len(upd['files'])} file(s), {len(upd['removed'])} removed citation(s)")
            print(f"packet: {out / '.soulreap' / 'update.md'}")
        else:
            print(f"packet: {out / '.soulreap' / 'read.md'}")
        return 0
    if args.cmd == "render":
        with quiet_native_noise():
            stats = cmd_render(root, out, args.diagrams)
        print(json.dumps({k: v for k, v in stats.items() if k != "stamped"}, indent=2, ensure_ascii=False))
        return 1 if stats["strict"] else 0
    if args.cmd == "status":
        result = cmd_status(root, out)
        if args.brief:
            line = brief_status(result)
            if line:
                print(line)
            return 0
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["clean"] else 1
    if args.cmd == "skeleton":
        cmd_skeleton(root, out, args.target)
        return 0
    if args.cmd == "flow":
        return cmd_flow(root=root, out=out, target=args.target, dot=args.dot, brief=args.brief)
    if args.cmd == "find":
        return cmd_find(root=root, out=out, query=args.query, limit=args.limit)
    if args.cmd == "refresh":
        with quiet_native_noise():
            path = cmd_refresh(root, out)
        print(path)
        return 0
    if args.cmd == "confirm":
        try:
            if cmd_confirm(root, out, args.doc, args.heading):
                return 0
            print(f"section {args.heading!r} not found in {out / args.doc} — <doc> is relative to the map "
                  "folder (OVERVIEW.md, cards/x.md), <section> is the heading text without '#'", file=sys.stderr)
            return 2
        except LookupError as err:
            print(err, file=sys.stderr)
            return 2
    if args.cmd in ("install", "hook"):
        code, lines = cmd_install(root, Path(__file__).resolve(), hook_only=args.cmd == "hook" or args.hook_only,
                                  check=getattr(args, "check", False), uninstall=getattr(args, "uninstall", False))
        print("\n".join(lines))
        return code
    if args.cmd == "swap":
        try:
            result = cmd_swap(out, args.card, args.replacement, args.why, root=root)
        except LookupError as err:
            print(err, file=sys.stderr)
            return 2
        print(f"swapped: write {out / result['write']} instead of {out / result['instead_of']}")
        return 0
    if args.cmd == "explain":
        try:
            print(cmd_explain(root, out, args.target))
        except LookupError as err:
            return _not_found(args.target, err)
        return 0
    if args.cmd == "delta":
        try:
            report = run_delta(root, out, args.base, args.head, args.staged)
        except RevisionError as err:
            print(f"delta: {err}", file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else format_delta(report))
        return 1 if any(f["tier"] == "HIGH" for f in report["findings"]) else 0
    if args.cmd == "precommit":
        return run_precommit(root)
    if args.cmd == "slice":
        return _slice(root, out, args)
    if args.cmd == "impact":
        facts = fresh_facts(root, out)["files"]
        try:
            print(format_impact(impact(root, out, facts, args.target)))
        except LookupError as err:
            return _not_found(args.target, err)
        return 0
    facts = fresh_facts(root, out)["files"]
    table = symbol_table(facts)
    if args.cmd == "callers":
        key = _one(table, args.target)
        if key is None:
            return 2
        hits = callers(root, facts, key)
        for hit in hits:
            print(f"{hit['caller']}  L{hit['line']}  {hit['text']}  [{hit['confidence']}]")
        if not hits:
            print(f"{key}: no callers found (dynamic dispatch — getattr, callbacks, routes — is not traced)")
        for section in citing(out, key):
            print(f"cited by {section['doc']}#{section['section']} [{section['status']}]")
        return 0
    hits = merged_writes(root, facts, writes(table, args.target), args.target)
    for hit in hits:
        print(f"{hit['symbol']}  L{hit['line']}  {hit['what']}")
    if not hits:
        print(f"{args.target}: no writers found")
    for key in dict.fromkeys(hit["symbol"] for hit in hits):
        for section in citing(out, key):
            print(f"{key} cited by {section['doc']}#{section['section']} [{section['status']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
