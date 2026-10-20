"""The half of OVERVIEW.md that render owns.

The skill's first paragraph says a fact a parser can prove is never left to the model.
OVERVIEW was the one place that rule was not applied: the stamp, the directory counts,
the non-code inventory, the diagram pastes and the bullets about cycles and packages
are all sitting in `plan.json` and in the diagrams folder, and a model was retyping
them by hand at the end of every run.

That is what made the file drift. On fuse_core it sat for a day showing a hand-drawn
context with a GitHub node while the generated one named six systems and GitHub was not
among them, and pointing at a `packages.mmd` the silence rule had deleted. Nobody was
careless; the work was simply the wrong work to hand to a writer.

So render writes those blocks, between markers, and touches nothing else. What stays
prose is what no parser can produce: what the system is for, the sentence under each
diagram saying what to look for, and any judgement about sharp edges.
"""

from __future__ import annotations

import re
from pathlib import Path

MARKS = ("stamp", "inventory", "diagrams", "coverage", "verdicts")
OPEN = "<!-- soulreap:{name} -->"
CLOSE = "<!-- /soulreap:{name} -->"
PROSE = "_Write this by hand — a parser cannot._"

# Pasted by reference: the SVG is the artefact, so OVERVIEW embeds it as an image.
# Stores are pasted: measured at 4 in 2845 files, and where one exists it is often the only
# thing that persists (mercy's vault) — a filename in a list hid it.
PASTED = ("architecture.svg", "context.svg", "erd-*.svg", "observed-*.svg")
LINKED = ("structures-*.svg", "tasks.svg")


def _block(name: str, body: str) -> str:
    return f"{OPEN.format(name=name)}\n{body.rstrip()}\n{CLOSE.format(name=name)}"


def _human(number: int) -> str:
    return f"{number:,}".replace(",", " ")


def stamp(plan: dict, files: int, lines: int) -> str:
    commit = plan.get("commit") or "n/a"
    return (f"`{commit}` · {files} source files · {_human(lines)} lines · "
            f"generated {plan.get('generated_at', '')}")


def inventory(plan: dict) -> str:
    """Layout and non-code, straight off `plan.json`. Counts are never estimated."""
    lines = ["## Layout", "", "```"]
    tree = plan.get("tree") or {}
    width = max((len(name) for name in tree), default=1)
    for name in sorted(tree):
        item = tree[name] or {}
        label = "." if name == "." else f"{name}/"
        lines.append(
            f"{label:<{width + 1}}  {item.get('files', 0):>3} files, "
            f"{_human(item.get('lines', 0)):>7} lines"
        )
    lines += ["```", ""]

    categories = plan.get("non_code") or {}
    if categories:
        lines.append("## Non-code inventory")
        lines.append("")
        for category in sorted(categories):
            names = categories[category]
            shown = ", ".join(f"`{n}`" for n in names[:6])
            if len(names) > 6:
                shown += f", +{len(names) - 6} more"
            lines.append(f"- **{category}:** {shown}")
    secrets = plan.get("secrets") or {}
    for path, keys in sorted(secrets.items()):
        lines.append(f"- **config keys:** `{path}` declares "
                     + ", ".join(f"`{k}`" for k in keys) + " — names only, no values")
    return "\n".join(lines)


def diagrams(folder: Path) -> str:
    """Every generated diagram, pasted or linked, each with room for its sentence.

    The SVG is the artefact — mermaid is gone — so the pasted pair is an image
    reference, not source text. ``<img src="diagrams/x.svg">`` renders on GitHub
    and in any markdown viewer; the file itself stays beside the overview.
    """
    if not folder.is_dir():
        return "_No diagrams were generated._"
    lines: list[str] = []
    for pattern in PASTED:
        for path in sorted(folder.glob(pattern)):
            lines += [
                f"### {path.stem}",
                "",
                f'<p><img src="diagrams/{path.name}" alt="{path.stem}"></p>',
                "",
            ]
    linked = [p for pattern in LINKED for p in sorted(folder.glob(pattern))]
    if linked:
        lines.append("Also generated, not pasted:")
        lines.append("")
        for path in linked:
            lines.append(f"- `diagrams/{path.name}`")
        lines.append("")
    if not lines:
        return "_Nothing generated: no external system found, no schema, no store._"
    return "\n".join(lines).rstrip()


# Where each generated block lives in the scaffold: under this heading, first thing in it.
BLOCK_HOME = {"stamp": None, "inventory": "## Where things are", "diagrams": "## State and data",
              "verdicts": "## Sharp edges", "coverage": "## Not covered"}


def _restore(text: str, name: str, block: str) -> str:
    """Put a dropped block back as the first thing under its heading (`stamp`: under the title)."""
    lines = text.split("\n")
    home = BLOCK_HOME[name]
    at = next((i for i, line in enumerate(lines)
               if (line.startswith("# ") if home is None else line.strip() == home)), None)
    if at is None:  # the heading went too: the section comes back at the end
        return text.rstrip("\n") + f"\n\n{home or '# Overview'}\n\n{block}\n"
    return "\n".join([*lines[:at + 1], "", block, *lines[at + 1:]])


def update(text: str, blocks: dict[str, str], restore: bool = False) -> tuple[str, list[str]]:
    """Rewrite each marked block in place. Everything else is left exactly as it is.

    A block that is not there is reported in the returned list — and, with *restore*, put
    back under its heading first. Restore is only for an OVERVIEW this skill scaffolded: one
    written before it existed is somebody's work, and silently rewriting it is the mistake
    that cost 23 cards. A reader that rewrites the whole scaffold and drops a block is the
    case *restore* is for.
    """
    missing = []
    for name in MARKS:
        pattern = re.compile(
            re.escape(OPEN.format(name=name)) + r".*?" + re.escape(CLOSE.format(name=name)),
            re.DOTALL,
        )
        if not pattern.search(text):
            missing.append(name)
            if restore:
                text = _restore(text, name, _block(name, blocks[name]))
            continue
        text = pattern.sub(lambda _m, n=name: _block(n, blocks[n]), text, count=1)
    return text, missing


MAP_SCAFFOLD = """# {name}

{stamp}

## What this is

{prose}

## How it runs

{prose} Cite every entry point as `[[file::symbol]]`.

## Where things are

{inventory}

{prose}

## State and data

{diagrams}

{prose}

## Sharp edges

{verdicts}

{prose}

## Not covered

{coverage}

{prose} Name each checklist item or area you did not describe, with the reason.
"""


def map_scaffold(name: str, blocks: dict[str, str]) -> str:
    """A fresh v10 OVERVIEW.md with every generated block in place."""
    return MAP_SCAFFOLD.format(name=name, prose=PROSE, **{key: _block(key, blocks[key]) for key in MARKS})


def map_coverage(plan: dict, cards: list[str], missing: list[dict]) -> str:
    """What the map read, what it did not, and checklist items still uncited."""
    read = plan.get("read_set") or {}
    lines = [
        f"Read {read.get('total_lines', 0)} lines in {len(read.get('files') or [])} file(s)"
        + (" — the whole non-test source." if read.get("whole") else f" of a {read.get('budget')}-line budget."),
        f"Cards: {len(cards)}." + (" " + ", ".join(f"`{c}`" for c in cards) if cards else ""),
    ]
    left = read.get("not_covered") or []
    if left:
        lines.append("Not read (facts only — use `soulreap explain <file|symbol>` for prose): "
                     + ", ".join(f"`{p}`" for p in left))
    if missing:
        lines.append(f"Checklist items not cited yet: {len(missing)} — cite each, or list it below with a reason.")
    return "\n\n".join(lines)


def map_verdicts(plan: dict, stats: dict) -> str:
    """Findings, not opinions: cycles, unread schema, stale sections."""
    lines = [f"- **import cycle:** {' → '.join(c)}" for c in (plan.get("cycles") or [])[:5]]
    if not lines:
        lines.append("- No import cycle found.")
    for warning in (stats.get("schema_unread") or [])[:3]:
        lines.append(f"- **Schema not fully read:** {warning}")
    if stats.get("stale_count"):
        lines.append(f"- **{stats['stale_count']} section(s) stale** — run `soulreap refresh`.")
    return "\n".join(lines)
