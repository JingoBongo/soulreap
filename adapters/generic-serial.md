# Adapter — generic / serial (no subagents)

Soulreap's card-writing parallelizes, but parallelism is only a speedup — correctness does not
depend on it. On any harness without a working parallel-subagent primitive, the **main agent
writes the cards itself, one batch at a time**. This also keeps everything in one context, so
skip it only when the repo is large enough that context pressure matters (then find a harness
with subagents, or scope to a subpath).

## Procedure

1. `uv run --script <SKILL>/scripts/soulreap.py plan --root <repo>` — read
   `<repo>/soulreap/.soulreap/plan.json`, take the `new` + `dirty` files.
2. Read `<SKILL>/references/file-card.md` and `<SKILL>/references/pseudocode.md` **once**.
3. For each file, in order:
   - `uv run --script <SKILL>/scripts/soulreap.py skeleton --root <repo> <path>`
   - read the source once
   - write `<repo>/soulreap/files/<path>.md` (Role, empty `## Dependencies`, Entities,
     Pseudocode) per the templates
   - move on — no checker scripts, no re-reading, no `render`
4. After the last card: `render` once (`... render --root <repo>`), fix any `mermaid:` warnings.
5. Hand-write `flow.mmd`, `architecture.mmd`, `OVERVIEW.md` per `<SKILL>/references/overview.md`.

## Notes

- The `carded`/`new` mechanic still helps: if you stop partway, re-running `plan` shows exactly
  what's left (`new`) versus already written (`carded`).
- Everything else in `SKILL.md` (single-writer render, the mermaid gotchas, the report) applies
  unchanged.
