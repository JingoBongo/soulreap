---
name: soulreap
description: "Codebase facts you query and a map you read. Use when asked to map, explain, document or onboard onto a repo, when the user says soulreap / code map / карта кода / объясни проект, or when a soulreap/ directory or an AGENTS.md soulreap block exists. Also use while coding: before changing, renaming or deleting a function or class, or when you need who-calls / what-breaks / who-writes / where-is-X / the code around X in a repo, run a soulreap query instead of grepping; and run `delta` before saying a change is done."
allowed-tools: bash read write
metadata:
  jev-tier: "1"
  jev-group: "Dev/Codebase"
  jev-description: "Soulreap code map and queries: map, explain, document or onboard onto a repo, and before renaming or deleting a function run who-calls / what-breaks instead of grepping. Use when you must understand or safely change a repo: \"map this repo\", \"who calls this\", \"карта кода\", \"объясни проект\"."
  jev-hint: "Soulreap code map"
---

# Soulreap

Two layers, and only one of them costs an LLM:

* **Facts** — computed by a parser for every file, in seconds, from the working tree as
  it is right now: entities with line ranges, imports, callers, reverse dependencies,
  stores, schema, concurrency, I/O and per-function **hazards** (swallowed exceptions,
  locks, state writes, external calls, spawned threads). Never written as prose.
* **The map** — `soulreap/OVERVIEW.md` plus a few `cards/<slug>.md`, written by one
  reader from one reading of what matters. Every claim cites `[[file::Symbol]]`; every
  section records what it was written against, and says so under its heading when the
  code has moved.

```bash
export PYTHONUTF8=1
SR=~/.pi/agent/skills/soulreap/scripts/soulreap.py
uv run --script $SR <command> --root <repo> [...]
```

Always `uv run --script` — elsewhere the declared dependencies are absent and non-Python
files silently report no facts.

## 0. Freshness first

When `<repo>/soulreap/` exists, the first thing every invocation does is

```bash
uv run --script $SR status --root <repo>
```

(seconds, no LLM, **writes nothing** — safe in the middle of coding) and report `stale`,
`unresolved`, `checklist_missing`, `legacy` from its JSON before anything else. Exit 1 means
something is listed, not that the command failed. `render` is the command that writes spans
and banners; run it when you are about to read or edit the map. A `legacy` entry (`files`,
`units`, or an older `OVERVIEW.md` / `INDEX.md` / `manifest.json`) is from an older (v9 or
earlier) map: report it, never delete it — the user decides.

## 1. Query mode — no LLM, use it while coding

**Why not grep.** grep matches text; a query resolves. `callers save` follows `from store import
save as persist`, `self.save()`, `store.save()` on a typed instance — and skips the comment, the
string and the other `save` in an unrelated class. One `slice` replaces skeleton + callers + flow +
three file reads, within a token budget. `delta` sees what a diff cannot: the caller in a file you
never opened. Answers come from the code on disk now (cache refreshed by file hash, < 1 s warm).

Reach for it at these moments:

| you are about to… | run | what you get |
|---|---|---|
| locate a name | `find <substr>` | `file:line`, kind |
| work on a symbol you have not read | `slice <symbol\|file> [--budget N]` | its source, callers/callees as skeletons, 2-hop one-liners, a `# frontier` of what did not fit — one call, ≤ N tokens (default 4000) |
| debug a stack trace | `slice --trace tb.txt` (`-` = stdin) | the same, seeded from the trace's repo frames, deepest first |
| edit a file you have not read | `skeleton <file>` | signatures, line ranges, hazards, literal `command:` / `regex:` lines |
| trace what an entry point does | `flow <file>::<callable>` | the cross-file call tree |
| rename, delete or change a signature | `callers <symbol>`, then `impact <symbol>` | every call site; dependents, entry points reaching it, state it writes, tests |
| change how some state is written | `writes <name>` | each symbol assigning, mutating or `put`/`pop`/`delete`-ing that name |
| finish the change | `delta`, then `status` | every symbol you changed vs HEAD (work tree, untracked included), HIGH/MED/LOW findings, tests to run, map sections citing them |
| review a branch | `delta --base main` (`--head REF` for two commits) | the same since the merge base |

**Reading the answer**

* A symbol is `file::Name` or `file::Class.method`; a bare name works when it is unique.
  Exit 2 means `no such symbol` or `ambiguous — <candidates>`: re-run with a candidate from
  the list. `find` with no match exits 0 and prints `# 0 match(es)`.
* Nested functions and lambdas are not symbols — query and cite the enclosing function.
* `callers`, `impact` and `writes` end with the map sections citing the symbol, marked
  `[fresh]` / `[stale]`: the sections your change can make wrong.
* `~heuristic` (in `flow`) and `?` mark edges matched by name only, not proven calls.

**What `writes` covers.** Assignments, in-place mutators (`append`, `pop`, `update`, …),
and the write verbs of a detected store (`put`, `set`, `save`, `pop`, `delete`, `delete_*`,
`clear*`, …) — one row per call site, test files included. Name the store by its binding
(`remote_repos_vault`), an imported alias, or its store name (`remote_runnables_repositories`).
A store is *detected* when a module-level or `self.` binding is both read and written through
verbs; a client handed in as an argument is not, so its writes are absent — confirm with
`skeleton` or grep when the answer decides the change.

**`impact`'s tests** are every test whose imports reach the module, transitively — a
superset of the tests that exercise it. Run them, or narrow by name.

**What a query writes** — its fact cache, `<out>/.soulreap/facts.json`, `<out>` defaulting
to `<root>/soulreap/`. In a repo without a map, pass `--out` a temp directory so the query
leaves no untracked folder behind.

**`delta`'s findings** are rules, each naming its evidence. HIGH is likely breakage: a removed
symbol that surviving code still calls; a signature change with callers in other files left
unchanged. MED is for review: a new swallow/lock/spawn/external call (`-reachable` when an entry
point reaches it), a changed command or regex literal, callees changed (builtins ignored), a
signature change whose callers were all updated (or are local), a new state write, a body change in a
symbol with ≥ 5 call sites, fan-in that grew. LOW: the rest. Test files raise none — they feed
*tests to run* (direct callers first, then importers). `--staged` checks only the index. Exit 1 on
any HIGH, 2 on a bad ref. `--json` for tools.

**After your change** — run `delta`, then `status`. A stale section citing what you changed is part of
the change: rewrite it or `confirm` it (§4), or name it in your report.

## 2. Map mode — when asked to map, onboard or document

1. `plan` — writes `soulreap/.soulreap/read.md`: **what runs** (from Dockerfile/compose
   commands and the shell scripts they run, Procfile, `fuseinstructions.txt`, console scripts,
   an app's `package.json`; else `__main__` guards, published packages, `main.py`-style
   files), then what to read: the whole non-test source when it fits the 3,000-line budget,
   else the run roots and what they import, nearest first, each file where the nearer code
   uses it; the **checklist** of hazards the map must cite, **Look here** (the functions that
   carry the repo by an importance score: size, use across files, reach, what the docs talk
   about), the K cards to write with their file paths (one per run root first — fixed — then by
   that score), a shortlist, and what was not read. On a first run it also scaffolds
   `soulreap/OVERVIEW.md`. `--budget N`, `--exclude GLOB`, `--with-tests`. Follow the
   packet it prints — read.md, or update.md when a map already exists.
2. Read `references/card.md`, then the files in the packet's order — **once**.
   Optional, before writing cards: if a shortlist entry matters more than one of the non-fixed
   cards, `swap <card> <shortlist entry> --why "<one line>"` — once per plan; it prints the path
   to write instead. The score is as good as a reader at picking, but not in the same places.
3. In `OVERVIEW.md`, replace every `_Write this by hand — a parser cannot._` line and keep
   the `<!-- soulreap:* -->` blocks; write `diagrams/architecture.dot`, the runtime picture
   (references/overview.md); write the K cards at the paths the packet gives.
4. `render`. `strict: true` (exit 1) means an anchor names no symbol (`unresolved`) or a
   checklist item is uncited (`checklist_missing`); also fix `unwritten` (placeholders left).
   A generated block you dropped is put back (`overview_restored`), and a backticked anchor
   to a real symbol is turned into a citation. Fix only what it names, in the same context —
   nothing else is a reason to re-read. `render` rewrites the files (spans, basis lines), so
   re-read a file before editing it again.
5. One verifier subagent, fresh, that **may edit**: on Pi `delegate` with agent `editor`
   (`reviewer` is read-only and only reports); in Claude Code the `Agent` tool. Its task is
   the absolute path of `references/verify.md`, the map folder `<repo>/soulreap/`, and
   `<repo>/soulreap/.soulreap/read.md`. It reports and fixes WRONG claims only, once, in
   verify.md's three-line format (no `OK` lines, not its agent's own template).
6. `render` again; report.

**The reader is this session.** No writer subagents: one reader, one reading, within the
budget. A bigger map is a bigger `--budget` (still one reader) or `explain` for the parts you need.

## 3. Explain mode — depth on demand

`explain <entry|file|symbol>` writes `soulreap/.soulreap/explain-<slug>.md`: the target's
range, its callers and callees, their hazards, and the card path to write or update. Read
it, write the card, `render`. The card is an ordinary card from then on — indexed,
stamped, tracked for staleness. The map grows where people actually look.

## 4. Refresh mode — after the code moved

`refresh` renders and writes `soulreap/.soulreap/refresh.md`: every stale section, what
moved (`+callee notify`, `-write self._cache`, `deleted`) and the moved symbols' current
ranges. For each section: rewrite it (delete its basis line and banner) or, if the claim
still holds, `confirm <doc> "<section>"` (`<doc>` relative to `soulreap/`, e.g.
`cards/main.md`; `<section>` the heading text without `#`). Then `render`. Comment, docstring and formatting edits
never stale anything. A change to a cited symbol's signature, callees, hazards or literals names what
moved; any other code change (a value, a condition, a constant it reads) shows as `body changed` —
check the claim, then rewrite or `confirm`.

With the pre-commit hook (§6), every commit carries a map rendered from exactly its code: a section
your change staled is bannered in that commit, and one line says so — rewriting it still needs you.

### Update — the code grew since the map

`status` also reports `since_plan`: entry points, hazards (in files the map read), files and
removed citations that are new since the last `plan`. When anything is listed, run `plan` again:
on an existing map it writes `soulreap/.soulreap/update.md` — only the new things; read that,
not read.md — and adds the
new entry points to the checklist, so `render` keeps asking until they are cited. Write them
into OVERVIEW or the owning card (or `explain` for a new card), then `render`. Never re-map from
scratch to pick up new code.

## 5. Reading a map (if you are the agent asked about this repo)

1. `OVERVIEW.md` — what it is, how it runs, sharp edges, and *Not covered*.
2. `INDEX.md` — cards (fresh/stale) and every file with what read it and who cites it.
3. Query commands — `find`, `skeleton`, `flow`, `callers`, `impact`, `writes`.
4. The card that covers the part.
5. The source.

The code wins over the map. A `> ⚠ stale` line under a heading means: verify that section
against the source before relying on it.

## 6. Setting up a repo

`install` (only when the user asks): a git pre-commit hook running `precommit` — when the repo
commits its map, `render` of the *staged* code put into the same commit (spans, basis lines, stale
banners, INDEX, diagrams, facts; your unstaged edits to a card are kept on disk), then
`delta --staged`, blocking only on HIGH, bypass `SOULREAP_SKIP=1` or `--no-verify` — and a
marked block in `AGENTS.md` telling any agent soulreap applies here. Idempotent; `--check` reports
what is missing, `--uninstall` removes only what it added, `--hook-only` skips AGENTS.md. A foreign
pre-commit hook is never touched: install prints the line to add to it. In a repo set up like this
(or one with a map), a symbol-shaped grep gets a one-line hint pointing here — that is this skill.

## Done — before you say a change is finished

1. `delta` (or `delta --staged`): every HIGH fixed, or named in your report as intended.
2. The tests under *tests to run*: direct ones at least.
3. `status`: each stale section citing what you changed rewritten, `confirm`ed, or named.

## Anti-patterns

* grepping for a function's callers, then editing on that list — `callers` resolves what grep misses.
* reading three whole files to understand one function — `slice` it.
* re-mapping from scratch because the code moved — `refresh` / `plan` update the map in place.
* trusting a `~heuristic` edge or a `?` caller as proven — confirm it in the source first.
* committing with `SOULREAP_SKIP=1` without saying which HIGH finding you overrode, and why.

## Rules

* Never hand-edit a generated block, `INDEX.md` or `manifest.json`.
* Never delete a legacy `files/` or `units/` tree, or a hand-written OVERVIEW without
  markers — report them.
* No review loops: render's checks are deterministic, the verifier runs once.
* Cite symbols, never line numbers; no headings inside fenced code.
* Tests are never part of the map (facts still cover them; `impact` lists them).

## Schema warnings

`render` reports `schema_unread`: `<path>:<line>: <why>` names DDL it found but could not
read statically (an interpolated table name); `<path>: db evidence, but no schema was found`
names a database whose schema it never saw. The ERD is missing that table — say so
in the report and in OVERVIEW → Not covered, rather than staying quiet.

## Report

Lines read and of how many, cards written, checklist cited/total, stale sections before
and after, the verifier's findings, and what the map does not cover.
