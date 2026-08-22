# Adapter — opencode (sst/opencode)

## Install

opencode discovers skills/agents under its config directory. Install with:

```bash
./install.sh opencode        # -> $XDG_CONFIG_HOME/opencode/skills/soulreap (or ~/.config/opencode/...)
```

If opencode's skills path differs in your version, pass the directory explicitly instead:

```bash
./install.sh ~/.config/opencode/skills/soulreap
```

and set `$SKILL` in `SKILL.md` to that path. Check the opencode docs for the current
skills/agents location — the engine is path-independent, so any location works as long as you
point the script calls at it.

## Parallel card-writing (SKILL.md step 2)

Use opencode's **subagent / Task primitive** (spawn an agent per batch, `@`-mention or the task
tool depending on your version) and launch the batches together so they run concurrently. The
per-batch task text is the same as the other adapters:

> Write soulreap cards for these files: `<paths>`. Repo root `<repo>`. For each file: run
> `uv run --script <SKILL>/scripts/soulreap.py skeleton --root <repo> <path>`, read the source
> once, then write `<repo>/soulreap/files/<path>.md` following `<SKILL>/references/file-card.md`
> and `<SKILL>/references/pseudocode.md`. Leave `## Dependencies` empty; do NOT run render. No
> checker scripts, no grep-loops, one skeleton + one read per file. Return one line per file:
> `<path> — <Role sentence>`. Do not paste card bodies.

If your opencode setup has no working subagent fan-out, use **`generic-serial.md`** — the main
agent writes the batches itself, one at a time. Same result, just slower. Either way, the
parent runs `render` once at the end.
