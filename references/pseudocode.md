# Pseudocode contract

The pseudocode block is the point of the card. Someone who has never opened the
file must be able to read it once and know what the file does.

## Shape

- KEYWORDS in uppercase, everything else lowercase. The eye finds structure first.
- One operation per line. Indentation is nesting depth.
- Explicit terminators: `ENDIF`, `ENDWHILE`, `ENDFOR`, `ENDTRY`, `ENDLOCK`, `ENDFUNCTION`.
- Every function header carries its source range: `[L88-L131]`. Anchors make the card verifiable.
- Hard cap: 40 lines per file. Over that, keep the entry points and summarise the rest as
  `... 6 more private helpers, see Entities`.
- Collapse trivia. A one-line getter is `RETURN self.name`, not four lines of ceremony.

## Name every operation and its target

State what happens and to what. A category tag says nothing.

| Instead of | Write |
| --- | --- |
| `IO:db` | `QUERY postgres users WHERE email = email_address` |
| `IO:http` | `HTTP POST https://api.stripe.com/v1/charges` |
| `IO:fs` | `READ FILE config/settings.yaml` |
| `THREAD` | `SPAWN 8 worker THREADS, each pulling from work_queue` |
| `CACHE` | `CACHE GET session:{user_id}, miss → fall through` |

## Keywords

| Group | Keywords |
| --- | --- |
| Branching | `IF ... THEN` / `ELSE` / `ENDIF`, `GUARD <condition> ELSE <action>` |
| Loops | `FOR EACH item IN collection` (order irrelevant, parallelisable), `FOR i = 1 TO n` (ordered), `WHILE cond ... ENDWHILE`, `REPEAT ... UNTIL cond`, `BREAK`, `CONTINUE` |
| Data | `SET x TO ...`, `COMPUTE`, `APPEND x TO list`, `REMOVE x FROM list`, `RETURN` |
| Storage | `QUERY <store> <table> WHERE ...`, `WRITE <store> <table> SET ...`, `DELETE FROM <store> <table>`, `CACHE GET/SET <key>` |
| Network | `HTTP <VERB> <url>`, `PUBLISH <topic>`, `CONSUME <queue>`, `RPC <service>.<method>` |
| Files | `READ FILE <path>`, `WRITE FILE <path>`, `LIST DIR <path>` |
| Concurrency | `SPAWN <n> THREADS`, `SPAWN PROCESS <name>`, `PARALLEL FOR EACH`, `AWAIT <task>`, `AWAIT ALL` |
| Synchronisation | `LOCK <resource> ... ENDLOCK`, `ATOMIC`, `<queue>.PUT/GET` |
| Errors | `TRY ... CATCH <ErrorType> ... ENDTRY`, `RAISE <Error> "<message>"`, `RETRY <n> TIMES WITH <strategy>` |
| Recursion | `CALL SELF(...)`, and mark the header `↺ recursive` |
| Streams | `YIELD <value>` |

`FOR EACH` versus `FOR` is a real distinction, not decoration: `FOR EACH` says the
iterations are independent, `FOR` says the order matters.

## Names are spelled out

Expand every abbreviation, even though the line gets longer. `pw` could be a password
or a perfect world; `plain_password` could not.

| Source | Pseudocode |
| --- | --- |
| `pw` | `plain_password (pw)` |
| `ctx` | `request_context (ctx)` |
| `req`, `res` | `request`, `response` |
| `cfg` | `config` |
| `usr`, `u` | `user_row` |
| `tmp` | name it for what it holds: `pending_rows` |

Give the original in parentheses at first appearance only, so the reader can grep back
into the source. Leave genuinely standard names alone: `id`, `url`, `db`, `http`, `api`, `jwt`.

## Worked example

<Good>
```
FUNCTION login(email_address, plain_password (pw)) RETURNS Token   [L88-L131]
    GUARD email_address is empty ELSE RAISE HttpError 400 "email required"

    QUERY postgres users WHERE email = email_address  → user_row   [L96]
    GUARD user_row exists AND bcrypt(plain_password) matches user_row.password_hash
          ELSE RAISE HttpError 401

    SET token TO sign_jwt(user_row.id, expires_in = JWT_TTL)
    WRITE postgres users SET last_login_at = now() WHERE id = user_row.id
    RETURN token
ENDFUNCTION
```
Every side effect is visible, every store is named, every failure path is explicit.
</Good>

<Bad>
```
FUNCTION login(email, pw)
    validate input
    IO:db lookup
    IF invalid: error
    create token
    RETURN token
```
"Validate input" hides the guard. "IO:db lookup" hides the table. `pw` hides the meaning.
The reader learns nothing they could not have guessed from the function name.
</Bad>

## Concurrency reads like a plan

```
FUNCTION reindex_all(document_ids)                                 [L40-L78]
    SET work_queue TO queue containing document_ids
    SPAWN 8 worker THREADS, each:
        WHILE work_queue not empty
            SET document_id TO work_queue.GET()
            HTTP POST https://api.embed/v1/vector  (RETRY 3 TIMES WITH exponential backoff)
            LOCK index_writer
                WRITE qdrant collection=documents
            ENDLOCK
        ENDWHILE
    AWAIT ALL threads
    RETURN count of indexed documents
ENDFUNCTION
```

## Facts you must not invent

`soulreap.py skeleton` already reports recursion, concurrency primitives, I/O evidence and
line ranges. Use what it gives you. If the skeleton says `recursive: walk_tree`, the
pseudocode must show `CALL SELF`. If it reports no concurrency, do not write `SPAWN`.
