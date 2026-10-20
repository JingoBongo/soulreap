"""What a prose claim is pinned to beyond structure: command/regex literals and body fingerprints.

Split from hazards.py (400-line rule). `literals_of` keeps decisive strings verbatim — a flag
like `reset --hard` is exactly what a paraphrase loses; the two body fingerprints drive the soft
"body changed" staleness tier, for Python (AST) and for languages read as text.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import re

from .hazards import _hazard_nodes, _render, _root_name

# Programs whose command lines a reader needs verbatim (a flag decides behaviour: `reset
# --hard`, `merge-base --is-ancestor`). facts.KNOWN_BINARIES names the external systems;
# this adds the interpreters and package managers a repo drives.
PROGRAMS = frozenset({
    "git", "gh", "hg", "psql", "pg_dump", "mysql", "sqlite3", "redis-cli", "mongosh",
    "docker", "docker-compose", "kubectl", "helm", "terraform", "gcloud", "aws", "az",
    "systemctl", "ssh", "rsync", "curl", "wget", "ffmpeg", "pandoc",
    "pip", "pip3", "python", "python3", "uv", "npm", "npx", "node", "bash", "sh", "sudo",
    "kill", "nohup", "make", "poetry", "journalctl",
})


REGEX_CALLS = frozenset({"compile", "match", "search", "fullmatch", "findall", "finditer", "sub", "subn", "split"})


def literals_of(node: ast.AST) -> list[dict]:
    """Command lines and regex patterns written in *node*'s own body, in source order.

    Parsers cannot summarise these and a map that paraphrases them loses the decisive
    detail — fuse_core's eval lost points on a greedy regex and two git flags.
    """
    own = list(_hazard_nodes(node))
    prose = {id(s.value) for s in own
             if isinstance(s, ast.Expr) and isinstance(s.value, (ast.Constant, ast.JoinedStr))}
    containers = (ast.List, ast.Tuple, ast.JoinedStr)
    inner = {id(child) for s in own if isinstance(s, containers) for child in ast.walk(s) if child is not s}
    found: dict[tuple[str, str], int] = {}
    order: list[tuple[int, int, str, str]] = []

    def add(kind: str, text: str, sub: ast.AST) -> None:
        text = " ".join(text.split())[:160]
        if text and (kind, text) not in found:
            found[(kind, text)] = sub.lineno
            order.append((sub.lineno, sub.col_offset, kind, text))

    for sub in own:
        if isinstance(sub, ast.Call) and sub.args and isinstance(sub.func, ast.Attribute):
            if sub.func.attr in REGEX_CALLS and _root_name(sub.func) == "re":
                pattern = sub.args[0]
                if isinstance(pattern, ast.Constant) and isinstance(pattern.value, str):
                    add("regex", pattern.value, sub)
            continue
        if id(sub) in prose or id(sub) in inner:
            continue
        text = _render(sub)
        if text and text.strip().split(" ")[0].split("/")[-1] in PROGRAMS:
            add("command", text, sub)
    return [{"kind": kind, "text": text, "line": line} for line, _col, kind, text in sorted(order)]


def body_fingerprint(node: ast.AST, constants: dict[str, str] | None = None) -> str:
    """Everything a claim about values could depend on — numbers, keyword arguments, conditions,
    and the module constants the body reads — but not line numbers, comments or the docstring.

    The soft staleness tier: when only this moves, prose quoting a timeout or a retry count is
    flagged "body changed", and one `confirm` clears it if the claim still holds.
    """
    clone = copy.deepcopy(node)
    body = getattr(clone, "body", None)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        clone.body = body[1:] or [ast.Pass()]
    used = sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name) and n.id in (constants or {})})
    payload = ast.dump(clone, include_attributes=False) + "".join(f"|{n}={constants[n]}" for n in used)
    return hashlib.sha1(payload.encode()).hexdigest()[:10]


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


_LINE_COMMENT = re.compile(r"//[^\n]*")


def text_fingerprint(signature: str, body: str) -> str:
    """Fingerprint for a callable the AST reader does not parse (TS, JS, Go…): signature plus body
    text with comments and whitespace removed. Coarser than the Python one — any code edit counts."""
    clean = " ".join(_LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", body)).split())
    return hashlib.sha1(f"{signature}\n{clean}".encode()).hexdigest()[:10]
