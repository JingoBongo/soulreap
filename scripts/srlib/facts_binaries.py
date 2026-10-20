"""External programs a Python file spawns, found from literal command names only.

A repo that shells out to `git` or `systemctl` talks to those systems with no import or
URL to show for it. This module reads `subprocess`-style calls and command-shaped
string literals against a whitelist of known programs, and the names called under a
`__main__` guard.
"""

from __future__ import annotations

import ast

# Programs this repository runs. A repo that clones over `git` and restarts itself through
# `systemctl` talks to both, and no import or URL literal says so — fuse_core reported "no
# external systems" while doing exactly that. Only literal command names are taken: a command
# assembled from a variable is a name we cannot read, and guessing it would be invention.
_SPAWNERS = {"run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"}
# Wrappers that carry the real program in the NEXT argument.
_PASSTHROUGH = {"env", "sudo", "nohup", "time", "xargs", "nice"}


def _binary_name(raw: str) -> str | None:
    """`/usr/bin/env` -> None (a wrapper), `/usr/local/bin/git` -> `git`."""
    name = raw.strip().split("/")[-1]
    if not name or name.startswith("-") or name in _PASSTHROUGH:
        return None
    return name


# Programs we recognise by name. The literal signal below is whitelist-driven for a
# reason: without it, every sentence in the file would be a candidate command. Kept in
# step with SYSTEM_BY_BINARY in context.py, which maps these onto system names.
KNOWN_BINARIES = {
    "git", "gh", "hg", "psql", "pg_dump", "mysql", "sqlite3", "redis-cli", "mongosh",
    "docker", "docker-compose", "kubectl", "helm", "terraform", "gcloud", "aws", "az",
    "systemctl", "ssh", "rsync", "curl", "wget", "ffmpeg", "pandoc",
}


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Ids of string literals that are statements — prose, not arguments."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Constant, ast.JoinedStr)):
            out.add(id(node.value))
    return out


def _command_literals(tree: ast.Module) -> list[str]:
    """Known programs named at the start of a string that is not a docstring.

    fuse_core runs every git command through its own wrapper
    (`os_utils.start_system_barrel_process([f'git -C ... rev-parse HEAD'])`), so
    watching `subprocess` call sites alone reported no git at all in a repository whose
    whole job is cloning and resetting repositories.
    """
    prose = _docstring_nodes(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if id(node) in prose:
            continue
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr) and node.values:
            head = node.values[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                text = head.value
        if not text:
            continue
        first = text.strip().split(" ")[0].split("/")[-1]
        if first in KNOWN_BINARIES and first not in found:
            found.append(first)
    return found


def _binaries(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        callee = node.func
        name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
        if name not in _SPAWNERS:
            continue
        first = node.args[0]
        words: list[str] = []
        def literal_head(node: ast.AST) -> str | None:
            """The text at the start of a command, when it is written down.

            An f-string counts: `f"git -C {path} status"` interpolates its arguments but
            names its program outright, and that is how fuse_core calls git everywhere.
            A command that STARTS with a placeholder does not — `f"{tool} status"` is a
            name we cannot read.
            """
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.JoinedStr) and node.values:
                head = node.values[0]
                if isinstance(head, ast.Constant) and isinstance(head.value, str):
                    return head.value
            return None

        head_text = literal_head(first)
        if head_text is not None:
            words = head_text.split()
        elif isinstance(first, (ast.List, ast.Tuple)):
            for element in first.elts:
                text = literal_head(element)
                if text is not None:
                    words.extend(text.split() if " " in text else [text])
                else:
                    # `[sys.executable, "-m", "pip"]` — the program is this very process,
                    # not something external, and the rest of the line is its arguments.
                    words = []
                    break
        for word in words:
            program = _binary_name(word)
            if program:
                if program not in found:
                    found.append(program)
                break
    for program in _command_literals(tree):
        if program not in found:
            found.append(program)
    return found


def _main_calls(tree: ast.Module, path: str) -> list[str]:
    """Names called under `if __name__ == "__main__":`, or at the top of a `__main__.py`."""
    def guard(test: ast.AST) -> bool:
        if not isinstance(test, ast.Compare) or len(test.comparators) != 1:
            return False
        sides = [test.left, test.comparators[0]]
        return any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides) and any(
            isinstance(s, ast.Constant) and s.value == "__main__" for s in sides)

    bodies = [node.body for node in tree.body if isinstance(node, ast.If) and guard(node.test)]
    if path.endswith("__main__.py"):
        bodies.append([node for node in tree.body if isinstance(node, (ast.Expr, ast.Assign, ast.Return, ast.Raise))])
    # Bare names only: `uvicorn.run(...)` is not this repo's `run`. A name handed to a call
    # (`asyncio.run(main())`, `executor.submit(main)`) runs too.
    names: set[str] = set()
    for call in (n for body in bodies for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Call)):
        if isinstance(call.func, ast.Name):
            names.add(call.func.id)
        names.update(arg.id for arg in call.args if isinstance(arg, ast.Name))
    return sorted(names)
