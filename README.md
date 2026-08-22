# soulreap

Read a codebase file by file and **write it down** — one Markdown "card" per source file (role,
dependencies, entities, readable pseudocode), plus a project `OVERVIEW.md` and mermaid diagrams.
A code-map / onboarding artifact for AI coding agents, committed alongside the code.

**Two halves.** Deterministic Python parsers state the facts a parser can prove — imports,
signatures, line ranges, recursion, concurrency primitives, I/O targets, reverse-dependencies.
The agent writes only what a parser can't: what a file is *for*, and pseudocode a human can read.
A fact the skeleton can prove is never left to the model.

Output lands in `<repo>/soulreap/`:

```
soulreap/
  OVERVIEW.md          project overview (hand-written from the cards)
  INDEX.md             every file + its role, one row each
  files/<path>.md      one card per source file
  diagrams/
    context.mmd        external systems this code talks to      (generated)
    packages.mmd       import layering + coupling weight        (generated)
    flow.mmd           the one main request/data flow           (hand-written)
    architecture.mmd   runtime topology                         (hand-written)
  manifest.json        hashes, for incremental re-runs
```

## Prerequisite

[`uv`](https://docs.astral.sh/uv/) — that is all. The scripts are self-contained (PEP 723 inline
dependencies); no venv, no `pip install`, no network at runtime. On Windows, set `PYTHONUTF8=1`.

## Install

```bash
./install.sh claude          # -> ~/.claude/skills/soulreap
./install.sh pi              # -> ~/.pi/agent/skills/soulreap
./install.sh opencode        # -> opencode skills dir
./install.sh /path/to/skills/soulreap    # any directory
```

Windows: `pwsh ./install.ps1 claude` (same targets). Or just copy `SKILL.md`, `scripts/` and
`references/` into your harness's skills directory by hand — the engine is path-independent.

Then trigger it in your agent ("soulreap this repo", "map this codebase", `/soulreap`).

## How it runs

1. **plan** — walk the repo, hash it, diff against the manifest, emit work batches.
2. **cards** — one subagent per batch writes the file cards, in parallel (serial fallback works
   anywhere).
3. **render** — rebuild `INDEX.md`, the two generated diagrams and `manifest.json` (parent only,
   once).
4. **diagrams + overview** — hand-write `flow.mmd`, `architecture.mmd`, `OVERVIEW.md`.

The full procedure the agent follows is in [`SKILL.md`](SKILL.md); the card, pseudocode and
overview templates are in [`references/`](references/).

## Harness adapters

`SKILL.md` is harness-neutral. Each adapter gives the install path and the exact
parallel-subagent call for step 2:

- [`adapters/claude-code.md`](adapters/claude-code.md) — Task tool
- [`adapters/pi.md`](adapters/pi.md) — `delegate` extension
- [`adapters/opencode.md`](adapters/opencode.md) — opencode subagents
- [`adapters/generic-serial.md`](adapters/generic-serial.md) — no subagents, works everywhere

## Development

```bash
cd scripts
uv run --with pytest --with pathspec --with tree-sitter-language-pack pytest test_soulreap.py -q
uv run --with ruff ruff check --config ruff.toml .
```

## License

[MIT](LICENSE).
