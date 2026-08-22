# Adapter — Pi (@earendil-works/pi-coding-agent)

## Install

```bash
./install.sh pi              # -> ~/.pi/agent/skills/soulreap
```

`$SKILL` = `~/.pi/agent/skills/soulreap`. On Windows prefix script calls with `PYTHONUTF8=1`.

## Parallel card-writing (SKILL.md step 2)

Pi's `delegate` extension runs subagents in parallel. Use **parallel mode**, role **`worker`**
(the sole-writer role with the `write` tool), one task per batch. Concurrency is provider-gated
(fan-out on hosted providers, one-at-a-time on local llama.cpp) and tunable with
`PI_DELEGATE_CONCURRENCY`. Pack each `delegate` call up to its task cap so all lanes stay busy.

```json
{
  "tasks": [
    {
      "agent": "worker",
      "task": "Write soulreap cards for these files: <paths>. Repo root <repo>. For each file: run `PYTHONUTF8=1 uv run --script ~/.pi/agent/skills/soulreap/scripts/soulreap.py skeleton --root <repo> <path>`, read the source once, then write <repo>/soulreap/files/<path>.md following ~/.pi/agent/skills/soulreap/references/file-card.md and ~/.pi/agent/skills/soulreap/references/pseudocode.md exactly. Read both templates before the first card. Leave the `## Dependencies` heading empty; do NOT run render. Documentation task, no tests: no checker scripts, no grep-loops over anchors, one skeleton + one read per file, then write the card. Return one line per file: `<path> — <the Role sentence>`. Do not paste card bodies.",
      "done_criteria": "One .md file exists per listed source file, each with Role, an empty Dependencies heading, Entities and Pseudocode sections."
    }
  ]
}
```

More batches than the per-call task cap → several sequential `delegate` calls (still pack each
full). After all cards, the **parent** runs `render` once, then the hand-written diagrams and
OVERVIEW. Re-run `plan` to catch dropped cards (files back as `new`, not `carded`) and
re-delegate exactly those.
