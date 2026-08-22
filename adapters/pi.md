# Adapter — Pi (@earendil-works/pi-coding-agent)

## Install

```bash
./install.sh pi              # -> ~/.pi/agent/skills/soulreap
```

`$SKILL` = `~/.pi/agent/skills/soulreap`. On Windows prefix script calls with `PYTHONUTF8=1`.

## Parallel card-writing (SKILL.md step 2)

Pi's `delegate` extension runs subagents from **one call carrying many tasks**. Use role
**`worker`** (the sole-writer role with the `write` tool), **one task per batch inside the
`tasks` array of a single `delegate` call**. Concurrency is provider-gated — fan-out on hosted
providers, **strictly one-at-a-time on local llama.cpp** — and tunable with
`PI_DELEGATE_CONCURRENCY`; it serializes the tasks *within* the call.

**Never emit more than one `delegate` call at once** (e.g. one per batch). Parallel `delegate`
calls spawn independent worker fleets that all hit the model together; on a local / single-slot
model they deadlock on the one inference slot and the session hangs. One call, all batches as
tasks — on local that is exactly the safe sequential run; on hosted it fans out up to the cap.
More batches than the per-call cap → several **sequential** calls (issue one, wait, then next).

```json
{
  "tasks": [
    {
      "agent": "worker",
      "task": "Write soulreap cards for these files: <batch-1 paths>. Repo root <repo>. For each file: run `PYTHONUTF8=1 uv run --script ~/.pi/agent/skills/soulreap/scripts/soulreap.py skeleton --root <repo> <path>`, read the source once, then write <repo>/soulreap/files/<path>.md following ~/.pi/agent/skills/soulreap/references/file-card.md and ~/.pi/agent/skills/soulreap/references/pseudocode.md exactly. Read both templates before the first card. Leave the `## Dependencies` heading empty; do NOT run render. Documentation task, no tests: no checker scripts, no grep-loops over anchors, one skeleton + one read per file, then write the card. Return one line per file: `<path> — <the Role sentence>`. Do not paste card bodies.",
      "done_criteria": "One .md file exists per listed source file, each with Role, an empty Dependencies heading, Entities and Pseudocode sections."
    },
    {
      "agent": "worker",
      "task": "Write soulreap cards for these files: <batch-2 paths>. Repo root <repo>. (same instructions as the first task.)",
      "done_criteria": "One .md file exists per listed source file, each with Role, an empty Dependencies heading, Entities and Pseudocode sections."
    }
  ]
}
```

One `delegate` call, **one task per batch** in the array (two shown; pack up to the cap) — not
one call per batch.

More batches than the per-call task cap → several sequential `delegate` calls (still pack each
full). After all cards, the **parent** runs `render` once, then the hand-written diagrams and
OVERVIEW. Re-run `plan` to catch dropped cards (files back as `new`, not `carded`) and
re-delegate exactly those.
