# File card template

One card per source file, written to `soulreap/files/<source path>.md`. The heading
path, the `**Role.**` marker and the four section headings are required and must
appear in this order — `render` parses them.

```markdown
# src/api/routes/auth.py

<!-- render adds the [source](...) link here -->

`core` · 214 lines · python · ⚠ concurrency: async, locks

**Role.** Issues and refreshes JWTs for the login flow; the only place password
hashes are compared.

## Dependencies

<!-- render writes this section; do not fill it in -->

## Entities

| Kind | Name | Signature | What it does |
| --- | --- | --- | --- |
| const | `JWT_TTL` | `int = 3600` | Token lifetime in seconds. Also used by the refresh path. |
| class | `AuthService` | `(secret: str)` | Holds the signing secret; one instance per app. |
| method | `AuthService.login` | `async (email_address, plain_password) -> Token` | Verifies the password and mints a token. Raises 401 on mismatch. |
| func | `rotate_secret` | `(new_secret: str) -> None` | Swaps the signing key without dropping live sessions. |

## Pseudocode

(follow references/pseudocode.md exactly)

## Notes

- `AuthService` is instantiated once at import time — module-level state.
- TODO [L142]: refresh tokens are not revoked on logout.
- Compares password hashes; treat as security-sensitive.
```

## Rules

**Role line.** One or two sentences. Say why the file exists, not what its name
already says. "Auth routes" is worthless; "the only place password hashes are
compared" is the reason someone opens this file.

**Tag line.** `role-tag · lines · lang`, then any warning flags from the skeleton.
Role tags: `entrypoint`, `core`, `glue`, `model`, `config`, `test`, `generated`.

**Dependencies.** Leave the heading and move on — `render` composes the whole
section from parsed facts: standard library, third-party, internal (linked to the
neighbouring cards) and imported-by. Sorting imports into those buckets by hand
goes wrong: the first run of this skill dropped every standard-library import on
files whose only dependencies were standard library. Anything you would have
added as commentary belongs in Notes.

**Entities.** One row per constant, class, method and function the skeleton
lists. Signatures are copied verbatim from the skeleton, not retyped. The
description is one sentence, and it must add information the signature does not
already carry. Skip `__repr__`-grade trivia; say `+ 4 dunder methods` instead.

**Notes.** Only things that would bite someone editing the file: global state,
TODO/FIXME with line anchors, network or disk side effects, race conditions,
security sensitivity, dead code, generated-do-not-edit warnings. Omit the
section when there is nothing to say.

**Empty and trivial files.** A package marker or an empty file still gets all four
headings. Under Entities write `(none)`; under Pseudocode write one line saying why
there is none — `No executable code; package marker only.` Dropping the heading makes
cards inconsistent across batches, and a reader cannot tell an empty file from a card
its writer left unfinished.

**Never.** No secret values, no invented facts, no restating the source line by
line, no "this file is well structured" commentary.
