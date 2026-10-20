"""`delta`: what a change does at the symbol level — changes, risk findings, tests, map sections.

Base defaults to HEAD and head to the work tree (uncommitted and untracked files included), so a
bare `delta` answers "what did my change hit". `--base main` compares from the merge base;
`--head REF` compares two commits and ignores the work tree; `--staged` compares the index — what
the next commit holds, and what the pre-commit hook checks.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from .delta import changed_files, symbol_changes
from .freshness import fresh_facts
from .query import citing
from .revisions import facts_at, resolve_revisions
from .risk import assess, side, suggested_tests


def run_delta(root: Path | str, out: Path | str, base: str | None, head: str | None,
              staged: bool = False) -> dict:
    """The full report; raises RevisionError for a ref git cannot resolve. *staged* compares the
    index (what the next commit holds) instead of the work tree."""
    root, out = Path(root), Path(out)
    base_sha, head_sha = resolve_revisions(root, base, head, staged)
    work = fresh_facts(root, out)["files"]
    with contextlib.ExitStack() as stack:
        if head_sha:
            head_root, head_files = stack.enter_context(facts_at(root, head_sha, work))
        else:
            head_root, head_files = root, work
        base_root, base_files = stack.enter_context(facts_at(root, base_sha, head_files))
        changes = symbol_changes(base_files, head_files)
        if not changes:
            findings, tests = [], {"direct": [], "transitive": []}
        else:
            base_side, head_side = side(base_root, base_files), side(head_root, head_files)
            findings = assess(changes, base_side, head_side)
            tests = suggested_tests(changes, head_side)
    sections = [{"key": c["key"], **s} for c in changes if c["status"] != "added" for s in citing(out, c["key"])]
    label = "index" if staged else head_sha or "worktree"
    return {"base": base_sha, "head": label, "files": changed_files(base_files, head_files),
            "changes": changes, "findings": findings, "tests": tests, "sections": sections}


def format_delta(report: dict) -> str:
    """Plain text for the CLI: findings by tier, then changes, tests and citing sections."""
    span = f"{report['base'][:10]}..{report['head'][:10]}"
    if not report["changes"]:
        return f"delta {span}: no symbol changes"
    lines = [f"delta {span}: {len(report['files'])} file(s), {len(report['changes'])} symbol change(s)", ""]
    lines += [f"{f['tier']:<4} {f['rule']}  {f['key']} — {f['detail']}" for f in report["findings"]]
    lines += ["", "changes:"]
    lines += [f"  {c['key']}  {c['status']}" + (f"  {'; '.join(c['what'])}" if c["status"] == "modified" else "")
              for c in report["changes"]]
    tests = report["tests"]
    lines += ["", "tests to run:",
              "  direct: " + (", ".join(tests["direct"]) or "none"),
              "  transitive: " + (", ".join(tests["transitive"]) or "none")]
    if report["sections"]:
        lines += ["", "map sections citing changed symbols:"]
        lines += [f"  {s['doc']}#{s['section']} [{s['status']}]  ({s['key']})" for s in report["sections"]]
    return "\n".join(lines)
