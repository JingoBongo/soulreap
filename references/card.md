# Card contract (soulreap map v10)

You are the reader. You read the files in the packet **once** and write the map from
that one reading. Do not run tests and do not read soulreap's own code: run `render` once
when the map is written — it tells you exactly what is missing — and fix only that. The
soulreap queries are allowed and cheap. Below, `$SR` is `uv run --script
<skill>/scripts/soulreap.py` (the packet prints the full command), always with `--root <repo>`.

## What the map is for

A developer or an agent must be able to (1) grasp what the system is and how it runs,
(2) find the part they need, (3) know what bites. Facts the parser can print
(`skeleton`, `flow`, `callers`, `impact`, `writes`) are **not** retyped — cite them.

## Anchors

Cite code only as `[[file::Symbol]]` or `[[file::Class.method]]` — never a line number.
`render` turns each into a link a person can read and click,
`[save_order](../../shop/orders.py#L40-L72 "shop/orders.py::save_order")`, and keeps its span live;
leave such links as they are, and add new citations as `[[…]]`. Every claim about behaviour
carries at least one anchor — but one or two per sentence, not four: a sentence that is
mostly citations is unreadable once rendered.
Write anchors as plain text. Inside a code fence they are examples and do not count; in
`backticks`, `render` turns one that names a real symbol into a citation and leaves
`[[file::Symbol]]`-style placeholders alone.

Anchor the function that **does** the thing, not the one nearby: the write, the call, the
check, the route. Misattribution ("the cooldown is set in `revive`" when `check_cycle` sets
it) is the error a verifier fixes most. When unsure, one query answers it:
`$SR writes` (who assigns or mutates it), `$SR callers` (who calls it), `$SR skeleton <file>`
(each callable's signature, callees and hazards).

## OVERVIEW.md — fill each prose slot, keep the `<!-- soulreap:* -->` blocks untouched

* **What this is** — 2–4 sentences: purpose, who/what calls it, where it runs.
* **How it runs** — every entry point (anchor), what each one starts, one line each, and
  every external system and endpoint the code calls, by name (`POST {fuse_url}/launch_process`).
  A grasp question is answered from here; detail that lives only in a card is missed.
* **Where things are** — one line per area/file that matters, under the generated layout.
* **State and data** — what is stored where, who writes it (`$SR writes <name>`).
* **Sharp edges** — the traps: swallowed errors, non-atomic writes, ordering
  assumptions, retries, guards that look stronger than they are. Each with an anchor.
* **Not covered** — under the generated block, any checklist item you did not describe,
  each `[[anchor]] — reason`.

## A card — one per topic, at the path the packet gives (`cards/<slug>.md`)

    # <topic name, from the code>
    **What & why.** 2–3 sentences. What it keeps, and whether that survives a restart,
                    follows the packet's `State outside the process` — a store there is not
                    "in-memory" because it reads like a dict.
    ## Flow            (flow topics: paste `$SR flow --brief --root <repo> <entry>` output —
                        a list of anchors, ↻ for calls inside a loop, a docstring's sentence
                        after a step that has one. Add ` — <what it decides>` after each step
                        that matters and has none, in the list itself. Below it, only what a
                        step's line cannot hold: ordering and conditions, a few bullets)
    ## Contract        (component topics: what callers may rely on — a table or one bullet
                        per guarantee, never a paragraph chaining citations)
    ## Decisions       why it is shaped this way, each with an anchor
    ## Failure modes   what happens when each external call / write / lock fails
    ## Open by         the one anchor to start reading at

≤ 60 lines of prose. A guarantee ("atomic", "never raises", "thread-safe") is checked on
the failure path before you write it.

## What a reader asks first — answer it with the concrete value

* **Every input handler** (route, form handler, CLI command, message consumer): what it
  accepts, and what it **drops, ignores or rejects** — a whitelist, a silent `continue`, a
  default that overrides the input. Filtering is not a hazard, so no checklist forces it:
  you have to look.
* **Every external endpoint**: name it (`/launch_process`, the host or config key it comes
  from), not "calls Fuse". The checklist's `external` lines carry the path when it is literal.
* **Every command line and pattern that decides behaviour**: quote it from `skeleton`'s
  `command:` / `regex:` lines (`git … merge-base --is-ancestor`, `reset --hard`, a greedy
  `(.*)`), and say what follows from it. A paraphrase loses exactly the flag that matters.
* **Every port, interval, threshold and switch**: its value and where it is set (code
  default, config file, env var), and which one wins.

## The checklist

Every checklist item in the packet must be cited somewhere in the map, or listed under
OVERVIEW → Not covered with a reason. That is the whole completeness rule.

## Rewriting a stale section (refresh)

Rewrite the section, then delete its `<!-- basis@… -->` line and its `> ⚠ stale` banner.
If the claim still holds, do not rewrite — run `$SR confirm <doc> "<section>"` (doc relative to `soulreap/`, section without `#`).
