# soulreap

A code map you read and a set of codebase facts you query. Soulreap parses a repo into exact
facts, then spends one LLM pass writing only what a parser cannot: what matters, why, and where
the traps are.

**Two layers, and only one of them costs an LLM.**

- **Facts** — computed per file in seconds from the working tree as it is right now: entities with
  line ranges, imports, callers, reverse dependencies, stores and their write verbs, schema,
  concurrency, I/O, and per-function **hazards** (swallowed exceptions, locks, state writes,
  external calls, spawned threads). Never written as prose.
- **The map** — `soulreap/OVERVIEW.md` plus a few `cards/<slug>.md`, written by one reader from
  one reading of what matters. Every claim cites `[[file::Symbol]]`; every section records the
  revision it was written against and says so under its heading once the code has moved.

A fact the parser can prove is never left to the model, and the map is a navigation aid — not a
replacement for the code.

## Query it while you code (no LLM)

The facts answer questions grep cannot: `callers save` follows `from store import save as
persist`, `self.save()`, `store.save()` on a typed instance, and skips the comment, the string
and the unrelated `save` in another class.

| you are about to… | run | what you get |
|---|---|---|
| locate a name | `find <substr>` | `file:line`, kind |
| work on a symbol you have not read | `slice <symbol\|file> [--budget N]` | its source, callers/callees as skeletons, 2-hop one-liners, a `# frontier` of what did not fit — one call, ≤ N tokens (default 4000) |
| debug a stack trace | `slice --trace tb.txt` | the same, seeded from the trace's repo frames, deepest first |
| edit a file you have not read | `skeleton <file>` | signatures, line ranges, hazards, literal `command:` / `regex:` lines |
| trace what an entry point does | `flow <file>::<callable>` | the cross-file call tree |
| rename, delete or change a signature | `callers <symbol>`, then `impact <symbol>` | every call site; dependents, entry points reaching it, state it writes, tests |
| change how some state is written | `writes <name>` | each symbol assigning, mutating or `put`/`pop`/`delete`-ing that name |
| finish the change | `delta`, then `status` | every symbol you changed vs `HEAD` (work tree, untracked included), HIGH/MED/LOW findings, tests to run, map sections citing them |
| review a branch | `delta --base main` | the same since the merge base |

`callers`, `impact` and `writes` end with the map sections citing the symbol, marked `[fresh]` or
`[stale]` — the sections your change can make wrong. Answers are cached by file hash: warm calls
are under a second.

## Map it (one LLM pass)

1. `plan` — walk the repo, hash it, diff the fingerprints, choose what deserves a card, and write
   the reading order to `soulreap/.soulreap/read.md`.
2. **cards** — one subagent per batch writes `soulreap/cards/<slug>.md`; parallel where the
   harness allows, serial works anywhere.
3. `render` — parent only, once: `OVERVIEW.md`, diagrams, spans and `manifest.json`.
4. **hand-written** — the overview prose, the flow and the architecture diagram are written by a
   reader, not a parser. Stubs left behind by `render` are the checklist of what is incomplete.

Output lands in `<repo>/soulreap/`:

```
soulreap/
  OVERVIEW.md              the map: what runs, why, in what order
  cards/<slug>.md          a few decision-level cards, not one per file
  diagrams/
    context.svg            external systems this code talks to        (generated)
    packages.svg           import layering + coupling weight          (generated)
    <name>.svg             flow and architecture, hand-written as .dot sources
  .soulreap/
    facts.json             the fact cache behind every query
    manifest.json          fingerprints; drives `status` and `delta`
    read.md, plan.json     the plan the cards were written from
```

Diagrams are Graphviz **dot → SVG**; dot sources are kept next to the SVGs. There is no mermaid.

Then keep it honest: `status` reports what went stale (seconds, no LLM, writes nothing),
`delta` says what your uncommitted work changed, `refresh` lists every stale section, and
`confirm <doc> "<section>"` records that a section was re-checked against the current code.

## Prerequisite

[`uv`](https://docs.astral.sh/uv/) — that is all. The scripts are self-contained (PEP 723 inline
dependencies); no venv, no `pip install`, no network at runtime. On Windows, set `PYTHONUTF8=1`.
Graphviz (`pygraphviz`) is optional: without it, diagrams are left as `.dot` sources.

Always invoke as `uv run --script <skill>/scripts/soulreap.py <command> --root <repo>`; a bare
`python soulreap.py` runs without the declared dependencies.

## Install

```bash
./install.sh claude          # -> ~/.claude/skills/soulreap
./install.sh pi              # -> ~/.pi/agent/skills/soulreap
./install.sh opencode        # -> opencode skills dir
./install.sh /path/to/skills/soulreap    # any directory
```

Windows: `pwsh ./install.ps1 claude` (same targets). Or copy `SKILL.md`, `scripts/` and
`references/` into your harness's skills directory by hand — the engine is path-independent.
`sync-to-claude.sh` mirrors a Pi install into `~/.claude/skills/soulreap` and `--check` reports
drift; it is a convenience for keeping the two copies identical, not a requirement.

Then trigger it in your agent ("soulreap this repo", "who calls this", "map this codebase",
`/soulreap`).

## Harnesses

`SKILL.md` is harness-neutral: every command is one `uv run --script` call, so the work happens
in whichever agent session invoked it. The only harness-specific step is the verification pass at
the end of a map run — on Pi, `delegate` with agent `editor` (which may edit); on Claude Code the
`Agent` tool; anywhere else, a fresh session handed `references/verify.md` and the map folder.
There are no writer subagents: one reader writes the map, within the plan's budget.

The full procedure the agent follows is in [`SKILL.md`](SKILL.md); the card, verify and overview
templates are in [`references/`](references/).

## Development

```bash
cd scripts
uv run --with pytest --with pygraphviz --with pathspec --with tree-sitter-language-pack pytest -q
uv run --with ruff ruff check --config ruff.toml srlib soulreap.py hint.py conftest.py repo_fixtures.py
```

The suite is ~900 tests and runs in about 15 s. Tests that need `pygraphviz` are skipped when it
is absent, so a bare `pytest` is a convenience rather than the reference run.

## License

[MIT](LICENSE).
