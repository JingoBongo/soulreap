---
name: soulreap
description: "Read a codebase file by file and write it down: one card per source file with role, dependencies, entities and readable pseudocode, plus a project overview and diagrams. Use when asked to map, explain or document a repo, to onboard onto unfamiliar code, when the user says soulreap / code map / карта кода / explain this project, or when a soulreap/ directory already exists and the code has moved on since it was written."
allowed-tools: bash read write
---

# Soulreap

Two halves. Parsers state facts; subagents write prose. **A fact a parser can prove is never
left to the model** — imports, signatures, line ranges, recursion, concurrency primitives,
I/O targets and reverse dependencies all come from the script. The model supplies what a
parser cannot: what a file is for, and pseudocode a human can read.

Output goes to `<repo>/soulreap/` — `OVERVIEW.md`, `INDEX.md`, `files/<path>.md` per source
file, `diagrams/{context,packages}.mmd` (generated) + `{flow,architecture}.mmd` (hand-written),
`manifest.json`. It is documentation: commit it.

## Prerequisites and paths

The scripts are plain Python with PEP 723 inline dependencies, run through
[`uv`](https://docs.astral.sh/uv/). No venv, no install step, no network, no LLM inside them.
`uv` is the only prerequisite.

`$SKILL` below is the directory this skill is installed in — substitute the real path for your
harness (e.g. `~/.claude/skills/soulreap`, `~/.pi/agent/skills/soulreap`). **Always invoke the
script as `uv run --script $SKILL/scripts/soulreap.py`.** The `#!/usr/bin/env -S uv run --script`
shebang only fires in a POSIX shell; on Windows a bare path falls through to the system
interpreter, where the declared dependencies are absent, and non-Python files then report no
entities and no facts at all — with nothing warning you. On Windows also set `PYTHONUTF8=1` so
the arrow/warning glyphs in cards and diagrams don't hit a cp1252 encode error.

This skill needs a way to run subagents in parallel for the card-writing step. If your harness
has none, the serial fallback works too — see step 2 and `adapters/`.

## 1. Plan

```bash
uv run --script $SKILL/scripts/soulreap.py plan --root <repo> [subpath ...] [--since main] [--all]
```

First run resolves dependencies, so allow ~120s; afterwards it is seconds. Then read
`<repo>/soulreap/.soulreap/plan.json` — it reports counts and a `batches` list. Only `new` and
`dirty` files need a subagent.

| Status | Meaning | Action |
|---|---|---|
| `new` | no card yet | write one |
| `dirty` | source hash changed | rewrite the card |
| `carded` | card written this run but not yet indexed by `render` | skip — a worker already wrote it; the final `render` folds it into the manifest |
| `clean` | unchanged since last run | skip, costs nothing |
| `touched` | a dependency changed | `render` refreshes its links; no subagent |
| `edited` | card was hand-modified | **leave it alone**, list it in the report |
| `orphan` | source deleted | delete the card |

## 2. Cards — one subagent per batch, in parallel

Each batch is independent and writes a disjoint set of `files/<path>.md` cards, so batches
parallelize cleanly. Dispatch each batch to a subagent using whatever parallel-subagent
primitive your harness provides — the exact call for Claude Code, Pi, opencode and a
no-subagent fallback is in **`adapters/`**. If your harness has no subagent mechanism, run the
batches yourself one at a time (`adapters/generic-serial.md`): correctness is identical, you
only lose the parallel speedup and the flat-context benefit.

**What each subagent does, per file in its batch:**

1. `uv run --script $SKILL/scripts/soulreap.py skeleton --root <repo> <path>` — print the
   structural facts.
2. Read the source **once**.
3. Write `<repo>/soulreap/files/<path>.md`, following `$SKILL/references/file-card.md` and
   `$SKILL/references/pseudocode.md` exactly (read both templates before the first card).

**Rules to carry into the subagent's task text (all of them):**

- Leave the `## Dependencies` heading empty — the parent fills it in the single final render.
  **Never run `render` inside a subagent.**
- This is a documentation task, not a code change: there are no tests to run and nothing to
  debug. **No checker/verification scripts, no grep-loops over line anchors, one skeleton call
  plus one read per file**, then write the card and stop. Over-verifying burns the runtime and
  the activity watchdog kills the worker mid-write, losing cards.
- Return **one line per file** (`<path> — <the Role sentence>`). **Never paste card bodies** —
  that blows up the parent's context on the third batch.

**Pack each dispatch full.** Whatever your harness's parallel cap is, put that many batches in
one dispatch so all lanes stay busy; a half-full dispatch leaves lanes idle, and the gap
between one dispatch finishing and the next being issued is dead time. A big repo (600+ files)
plans into 100+ batches — for anything that size, scope the run to a subpath instead.

**Single writer.** Subagents write only their own `files/<path>.md` cards — disjoint paths,
safe in parallel. Everything that touches the whole tree — `plan` (before), `render` (after) —
runs **once, in the parent, never inside a subagent**. `render` is a read-modify-write over
every card plus `.soulreap`/manifest; two at once truncate cards and shrink `plan.json` to the
last writer's slice. `render` takes a lockfile as a backstop, but the rule is the fix: card
writers card, the parent renders.

**No self-verification.** A carder writes documentation — nothing to test or debug. Left
unchecked, a worker writes its own checker scripts, grep-loops over every anchor and re-reads
sources until the watchdog kills it mid-write — the single biggest cause of dropped cards. The
task text forbids it; keep it forbidden. It bites hardest on heavy files (1000+ lines, dozens
of entities) — keep such a file to its own small batch.

**Finding dropped cards.** A subagent's "ok" is words; the cards on disk are the truth — a
worker can time out or emit garbage and still report success. To see what a round actually
landed, re-run `plan`: files back as `new` (not `carded`) are the ones a worker silently
dropped. Redelegate exactly those, listing them in the task. Don't hand-roll a disk audit —
`plan` already is one. Expect a bad worker or two per large run (degenerate sampling ~10% on
small/quantised models); just replan and re-delegate.

## 3. Render

```bash
uv run --script $SKILL/scripts/soulreap.py render --root <repo>
```

Run it **once, from the parent, after every card is written** — never from a subagent (see
Single writer). Rebuilds `INDEX.md`, the two generated diagrams and `manifest.json`, and prints
the graph shape plus verdicts. Quote the verdicts in your report — they describe the code, not
the drawing. It also lints the `.mmd` files and prints `mermaid:` warnings on stderr — fix any
before finishing; they mean a diagram will not render. The linter is a heuristic, not a parser:
a clean lint is not proof a hand-written diagram renders (see step 4).

Render generates two files. `context.mmd` (what this code talks to outside itself) is pasted
into OVERVIEW as its own section. `packages.mmd` (import layering and coupling weight) is
**not** pasted — it reads as a rival to the hand-written `architecture.mmd`; it is kept as a
file and its one-line verdict becomes a bullet under Sharp edges. Tests and loose root scripts
are left out of both diagrams.

## 4. Hand-written diagrams and overview

Two diagrams no parser can derive, then the overview — all following
`$SKILL/references/overview.md`, built from `plan.json`, `INDEX.md` and the cards, without
re-reading source:

- `<repo>/soulreap/diagrams/flow.mmd` — a **sequence** diagram of the one flow that matters
  most (what happens in what order).
- `<repo>/soulreap/diagrams/architecture.mmd` — a **component/topology** diagram (services,
  workers, datastores, queues, external boundary — and how they wire). Required when the system
  is more than one process; skip it (and say why in OVERVIEW) for a single-process library/CLI.
- `<repo>/soulreap/OVERVIEW.md`.

`render` reports whether `flow.mmd` and `architecture.mmd` exist and lints them, but writes
neither.

**Mermaid syntax that bites.** In a sequence message, `A->>B: text`, a `;` inside the text is
read as a statement separator and the whole diagram fails to parse — use `,` (or `#59;`). In a
flowchart edge label, `A -->|text| B`, parentheses break the parser — `-->|control (9050)|`
fails; drop them or use `#40;`/`#41;` (parens inside a double-quoted *node* label like
`x["a (b)"]` are fine). No backticks in any node label. When you embed a diagram in
`OVERVIEW.md`, fix it in both places. The `render` linter catches these known cases, but it is
a heuristic, not a parser — validate a hand-written diagram with a real mermaid renderer before
finishing; a passing lint is not a guarantee it renders.

## 5. Report

Counts, import cycles, files that failed to parse, cards left alone because they were edited,
and the render verdicts.

## Rules

- Never write a fact the skeleton contradicts. No skeleton concurrency means no `SPAWN`.
- Never put secret values in a card. The scripts strip URL query strings and redact constants
  named like credentials; keep it that way in prose.
- Never overwrite an `edited` card without the user asking.
- Never card a file the plan calls `clean` — regenerating identical prose is pure waste.
- A file that fails to parse still gets a card: state the parse error, describe what is readable.
- Every diagram gets a sentence saying what to look for. A diagram with no question gets skipped.

## Common mistakes

| Mistake | Consequence |
|---|---|
| Subagents return full card text | this session's context blows up on the third batch |
| Writing `OVERVIEW.md` before the cards | it restates the file tree instead of the system |
| Skipping `render` | `INDEX.md` and `manifest.json` go stale, next run redoes everything |
| Pseudocode as prose summary | the reader cannot see loops, threads or failure paths — the whole point |
| Running `--all` out of habit | full rebuild; a template version bump already forces one |
| A subagent runs `render` (to fill Dependencies) | concurrent renders truncate other workers' cards and shrink `plan.json`; render is parent-only, once, at the end |
| A subagent writes checker scripts / grep-loops to self-verify | it hits the watchdog and dies mid-write — the top cause of dropped cards; carding has no tests, forbid it in the task |
| Trusting a subagent's "ok" instead of re-planning | timed-out or garbage workers report success; re-run `plan` and redelegate anything still `new` |
| `;` in a flow.mmd sequence message, or `()` in a flowchart edge label | mermaid fails to parse; use `,` / drop the parens |
