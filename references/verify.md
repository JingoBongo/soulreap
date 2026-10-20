# Verify contract (soulreap map v10)

You are a fresh verifier. You get the map (OVERVIEW.md, cards/) and the packet's read
set. Find claims that are **WRONG** and fix each one in place, once.

This contract overrides your agent's defaults where they differ: you may edit
`OVERVIEW.md` and every `cards/*.md` of this map (nothing else); do not check line numbers;
and your agent's own report template does not apply — answer in exactly this format, with
no `OK` lines, no headings, no list of what was right:

    WRONG <doc>#<section> [[anchor]] — what the code actually does — FIXED
    UNRESOLVED <doc>#<section> [[anchor]] — why you could not settle it
    CHECKED <n> sections, <m> wrong

## What to check — only what a parser cannot

* **Attribution** — the function a sentence says does the thing (the write, the call, the
  check, the route, the log) is the one that does it.
* **Values** — timeouts, defaults, ports, counts, precedence (which setting wins).
* **Failure paths** — what happens when a call, write or lock fails: caught where,
  swallowed or not, retried or not, fatal or not.
* **Guarantees and order** — "always", "only", "before", "never", "every": the code gives
  exactly that.
* **State** — what is kept, and whether it survives a restart. Check every such sentence,
  **including the unanchored ones** in a card's `What & why` ("everything lives in dicts and
  dies with the process"), against the packet's `## State outside the process` and the
  OVERVIEW's `State and data`. A card contradicting either is WRONG.

## What not to check — `render` already proved it

Line spans (`#L…` in a link, `(L…)` in older maps), whether a citation names a real symbol, whether the checklist is cited,
anything between `<!-- soulreap:* -->` markers, missing material, style, length. Spending
time on these finds nothing: they are checked mechanically on every render.

## How to read

Section by section: read the code at the lines its citations link to (`file#L12-L40`), and only that, unless a
claim is about something outside them (then read just enough to settle it). Never read a
whole file to check one sentence. "Wrong caller or writer" is one query, not a read:
`uv run --script <skill>/scripts/soulreap.py callers|writes|skeleton --root <repo> <target>`
(`<skill>` is the folder this file is in, minus `references/`).

## Report

The format at the top: one line per wrong claim, one summary line, then stop. You run once. Do not re-read your own fixes and do not run tests.
