#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pathspec>=0.12",
#   "tree-sitter-language-pack>=0.9",
# ]
# ///
"""soulreap — deterministic half of the codebase-map skill.

Three commands, no LLM, no network:

    plan      walk the repo, hash it, diff against the manifest, emit work
    skeleton  print the structural facts for one file
    render    rebuild INDEX.md, the module diagram and the manifest

Run through uv so dependencies resolve themselves:

    uv run --script scripts/soulreap.py plan --root /path/to/repo
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from srlib.commands import (
    TEMPLATE_VERSION,
    card_path,
    cmd_plan,
    cmd_render,
    cmd_skeleton,
    diff_statuses,
)
from srlib.facts import (
    FileFacts,
    extract_python,
    extract_source,
    extract_treesitter,
)
from srlib.graph import Graph, build_graph
from srlib.walk import FileEntry, classify, sha_of, walk

__all__ = [
    "TEMPLATE_VERSION", "FileEntry", "FileFacts", "Graph", "build_graph",
    "card_path", "classify", "cmd_plan", "cmd_render", "cmd_skeleton",
    "diff_statuses", "extract_python", "extract_source", "extract_treesitter",
    "sha_of", "walk",
]


def _preflight() -> None:
    """Warn when a declared dependency is absent, i.e. we ran outside `uv`.

    walk() already warns about a missing pathspec. tree-sitter has no such
    notice: its absence silently empties the Facts and Entities sections of
    every non-Python skeleton, leaving a subagent nothing to anchor a card to.
    Checked with find_spec, not an import: loading the parser pack is slow.
    """
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        print(
            "soulreap: tree-sitter-language-pack is not installed — non-Python files "
            "will report no entities and no facts, and a subagent will have nothing to "
            "anchor its card to. Run this script through `uv run --script` so its "
            "declared dependencies are present.",
            file=sys.stderr,
        )


def _resolve_out(args: argparse.Namespace) -> Path:
    root = Path(args.root).resolve()
    return Path(args.out).resolve() if args.out else root / "soulreap"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    # --root/--out are accepted on either side of the subcommand; SUPPRESS keeps
    # the subparser from overwriting a value given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help="repository root (default: cwd)")
    common.add_argument("--out", default=argparse.SUPPRESS, help="output directory (default: <root>/soulreap)")

    parser = argparse.ArgumentParser(prog="soulreap", description=__doc__, parents=[common])
    parser.set_defaults(root=".", out=None)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="scan, hash, diff, emit work batches", parents=[common])
    plan.add_argument("paths", nargs="*", help="limit carding to these subpaths")
    plan.add_argument("--since", default=None, help="use git diff against this ref instead of hashes")
    plan.add_argument("--all", dest="force", action="store_true", help="ignore the manifest, redo everything")

    skeleton = sub.add_parser("skeleton", help="print structural facts for one file", parents=[common])
    skeleton.add_argument("target", help="repo-relative path")

    sub.add_parser("render", help="rebuild INDEX.md, diagram and manifest", parents=[common])

    args = parser.parse_args(argv)
    _preflight()
    root = Path(args.root).resolve()
    out = _resolve_out(args)

    if args.command == "plan":
        payload = cmd_plan(root=root, out=out, paths=args.paths, since=args.since, force=args.force)
        counts = payload["counts"]
        print(
            f"soulreap plan: {counts['new']} new, {counts['dirty']} dirty, {counts['carded']} carded, "
            f"{counts['clean']} clean, {counts['touched']} touched, {counts['edited']} edited, "
            f"{counts['orphan']} orphan ({len(payload['batches'])} batches)"
        )
        if payload["cycles"]:
            print(f"import cycles: {len(payload['cycles'])}")
        return 0

    if args.command == "skeleton":
        cmd_skeleton(root=root, out=out, target=args.target)
        return 0

    result = cmd_render(root=root, out=out)
    print(f"soulreap render: {result['cards']} cards indexed, {len(result['missing'])} missing")
    roles = result["graph_roles"]
    grouping = "one node per file" if not result["graph_depth"] else "grouped by directory"
    print(
        f"  packages: {result['graph_nodes']} nodes, {result['graph_edges']} edges, "
        f"density {result['graph_density']:.0%}, {grouping}, "
        f"{result['graph_excluded']} files left out (tests, loose root scripts)"
    )
    print(f"  context:  {result['context_systems']} external systems, {result['context_edges']} links")
    if result["graph_density"] > 0.25 and result["graph_nodes"] > 3:
        print("  note: the package graph is dense — that is a property of the code, not the drawing")
    if not roles["entry"] and not roles["foundation"] and result["graph_nodes"] > 1:
        print("  note: no package is a pure entry or a pure foundation; dependencies run both ways")
    if not result["flow_present"]:
        print("  note: diagrams/flow.mmd is missing — write the main path by hand, no parser can")
    if not result.get("architecture_present"):
        print("  note: diagrams/architecture.mmd is missing — draw the runtime topology by hand "
              "(skip only for a single-process library/CLI)")
    for warning in result.get("mermaid_warnings", []):
        print(f"  mermaid: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
