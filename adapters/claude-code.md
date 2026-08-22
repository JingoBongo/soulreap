# Adapter — Claude Code

## Install

```bash
./install.sh claude          # -> ~/.claude/skills/soulreap
```

or copy `SKILL.md`, `scripts/`, `references/` into `~/.claude/skills/soulreap/` (or a plugin's
`skills/` dir) by hand. In `SKILL.md`, `$SKILL` = `~/.claude/skills/soulreap`.

Invoke it by asking to map/document/onboard a repo, or `/soulreap`.

## Parallel card-writing (SKILL.md step 2)

Use the **Task tool** (`general-purpose` agent), **one call per batch**, and send several Task
calls **in a single message** so they run concurrently. Claude Code caps how many run at once;
just dispatch all the batches and let it schedule them.

Task text per batch (fill `<paths>` and `<repo>`):

> Write soulreap cards for these files: `<paths>`. Repo root `<repo>`. For each file: run
> `uv run --script ~/.claude/skills/soulreap/scripts/soulreap.py skeleton --root <repo> <path>`,
> read the source **once**, then write `<repo>/soulreap/files/<path>.md` following
> `~/.claude/skills/soulreap/references/file-card.md` and
> `~/.claude/skills/soulreap/references/pseudocode.md` exactly (read both templates first).
> Leave the `## Dependencies` heading empty — the parent fills it in one final render; do NOT
> run `render`. This is documentation, not code: no checker scripts, no grep-loops over
> anchors, one skeleton call + one read per file, then write the card and stop. Return one line
> per file: `<path> — <the Role sentence>`. Do not paste card bodies.

After **all** batches finish, the **parent session** (never a subagent) runs `render`, then
hand-writes `flow.mmd` / `architecture.mmd` / `OVERVIEW.md`. To find any silently-dropped
cards, re-run `plan` and redelegate anything still `new`.
