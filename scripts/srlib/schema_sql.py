"""SQL DDL: tables read out of `CREATE TABLE` statements in a .sql file or an SQL string.

The comment blanker, balanced-paren body reader and top-level splitter live here, as
do the column, inline and table-level foreign-key and primary-key parsers. A statement
with interpolation in it is reported into `unread` rather than guessed at.
"""

from __future__ import annotations

import re

# `CREATE TABLE [IF NOT EXISTS] <name> (` — name quoted, bracketed, or bare.
CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+(?:TEMP\s+|TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([\"'`\[]?[\w${}. ]+[\"'`\]]?)\s*\(",
    re.IGNORECASE,
)
INLINE_FK_RE = re.compile(r"\bREFERENCES\s+[\"'`\[]?(\w+)[\"'`\]]?\s*\(\s*[\"'`\[]?(\w+)", re.IGNORECASE)
TABLE_FK_RE = re.compile(
    r"\bFOREIGN\s+KEY\s*\(\s*[\"'`\[]?(\w+)[\"'`\]]?\s*\)\s*REFERENCES\s+"
    r"[\"'`\[]?(\w+)[\"'`\]]?\s*\(\s*[\"'`\[]?(\w+)",
    re.IGNORECASE,
)
TABLE_PK_RE = re.compile(r"\bPRIMARY\s+KEY\s*\(([^)]*)\)", re.IGNORECASE)
# Body lines that are a table constraint rather than a column.
CONSTRAINT_RE = re.compile(
    r"^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|CONSTRAINT|EXCLUDE)\b", re.IGNORECASE
)
# A table name assembled by substitution — it cannot be read statically.
INTERPOLATED_RE = re.compile(r"[{}$]|%s")


def _unquote(name: str) -> str:
    return name.strip().strip("\"'`[]").strip()


def _blank_comments(text: str) -> str:
    """SQL comments blanked out with spaces, preserving every offset and newline.

    Blanked rather than cut, because both `line` and the paren-balancing scan
    compute offsets into this same string: cutting would shift every anchor after
    a comment.

    Without this, a comma inside `-- primary key, unique` reads as a top-level
    separator: the column after the comment is silently dropped, and a fabricated
    column named `--` appears in its place. Comments holding commas are ordinary in
    live DDL, and a reader cannot tell a fabricated column from a real one.
    """
    out = list(text)
    index, length, quote = 0, len(text), ""
    while index < length:
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            index += 1
        elif char in "\"'`":
            quote = char
            index += 1
        elif text.startswith("--", index):
            while index < length and text[index] != "\n":
                out[index] = " "
                index += 1
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            for position in range(index, end):
                if out[position] != "\n":
                    out[position] = " "
            index = end
        else:
            index += 1
    return "".join(out)


def _balanced_body(text: str, open_at: int) -> tuple[str, int] | None:
    """The body of the parenthesis opening at *open_at*, and the index of its closing paren.

    Track depth while skipping over the contents of string literals: a DEFAULT ')'
    would otherwise close the table definition halfway through.
    """
    depth = 0
    quote = ""
    for index in range(open_at, len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1 : index], index
    return None


def _split_top_level(body: str) -> list[str]:
    """The table body split on top-level commas — DECIMAL(10, 2) is not split apart."""
    parts: list[str] = []
    depth = 0
    quote = ""
    current: list[str] = []
    for char in body:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


def _leading_identifier(text: str) -> tuple[str, str] | None:
    """The column name at the start of a clause, and everything that follows it.

    Splitting on whitespace first is wrong: a quoted name with a space inside,
    `"event id" INTEGER`, would give the name `event` and declare `id` as the
    type — the real INTEGER is lost. The detector already understands quoted
    table names with a space, so for columns this was a plain inconsistency,
    and one that produces a fabricated value on top of it.
    """
    stripped = text.lstrip()
    if not stripped:
        return None
    pairs = {'"': '"', "'": "'", "`": "`", "[": "]"}
    closing = pairs.get(stripped[0])
    if closing:
        end = stripped.find(closing, 1)
        if end == -1:
            return None
        return stripped[1:end], stripped[end + 1 :].strip()
    head = stripped.split(None, 1)
    return head[0], (head[1] if len(head) > 1 else "")


def _column_of(clause: str) -> dict | None:
    """A single column definition, or None if this is a table constraint."""
    text = clause.strip()
    if not text or CONSTRAINT_RE.match(text):
        return None
    found = _leading_identifier(text)
    if found is None:
        return None
    name, rest = found
    name = _unquote(name)
    if not name:
        return None
    # The type is everything up to the first constraint keyword.
    type_match = re.match(r"^([\w ]*\w(?:\s*\([^)]*\))?)", rest)
    column_type = type_match.group(1).strip() if type_match else ""
    for keyword in ("PRIMARY", "NOT", "NULL", "DEFAULT", "REFERENCES", "UNIQUE", "CHECK", "COLLATE", "GENERATED"):
        cut = re.search(rf"\b{keyword}\b", column_type, re.IGNORECASE)
        if cut:
            column_type = column_type[: cut.start()].strip()
    upper = rest.upper()
    fk_match = INLINE_FK_RE.search(rest)
    nullable: bool | None = None
    if re.search(r"\bNOT\s+NULL\b", upper):
        nullable = False
    elif re.search(r"(?<!NOT )\bNULL\b", upper):
        nullable = True
    return {
        "name": name,
        "type": column_type,
        "pk": bool(re.search(r"\bPRIMARY\s+KEY\b", upper)),
        "fk": f"{fk_match.group(1)}.{fk_match.group(2)}" if fk_match else None,
        "nullable": nullable,
    }


def detect_sql_ddl(source: str, path: str) -> tuple[list[dict], list[dict]]:
    """Tables from the CREATE TABLE statements in *source*.

    Works equally on a .sql file and on the body of a string literal: DDL is DDL,
    wherever it happens to live.
    """
    tables: list[dict] = []
    unread: list[dict] = []
    source = _blank_comments(source)

    for match in CREATE_TABLE_RE.finditer(source):
        line = source[: match.start()].count("\n") + 1
        raw_name = match.group(1)
        if INTERPOLATED_RE.search(raw_name):
            unread.append({
                "line": line,
                "why": "table name is interpolated; it cannot be read statically",
                "snippet": source[match.start() : match.end()].strip()[:80],
            })
            continue
        found = _balanced_body(source, match.end() - 1)
        if found is None:
            unread.append({
                "line": line,
                "why": "table definition is not closed by a parenthesis",
                "snippet": source[match.start() : match.end()].strip()[:80],
            })
            continue
        body, close_at = found
        columns = [c for c in (_column_of(part) for part in _split_top_level(body)) if c]
        by_name = {c["name"]: c for c in columns}
        for fk in TABLE_FK_RE.finditer(body):
            column = by_name.get(fk.group(1))
            if column:
                column["fk"] = f"{fk.group(2)}.{fk.group(3)}"
        for pk in TABLE_PK_RE.finditer(body):
            for piece in pk.group(1).split(","):
                column = by_name.get(_unquote(piece))
                if column:
                    column["pk"] = True
        tables.append({
            "name": _unquote(raw_name),
            "line": line,
            "end_line": source[:close_at].count("\n") + 1,
            "detector": "sql_ddl",
            "columns": columns,
        })

    return tables, unread
