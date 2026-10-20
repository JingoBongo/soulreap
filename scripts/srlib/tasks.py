"""Background tasks: the one part of the broker question that is declared.

Measured over 2845 Python files before any of this was written, because the obvious
reading — "find the publish and consume verbs" — is worthless:

- `poll` is `subprocess.poll()` seven times out of seven;
- `subscribe` is an image-generation API's client, fifteen times;
- exactly ONE file in the corpus both publishes and consumes, and it names its streams
  through helper functions that build them out of a prefix constant, which no
  single-file parser can follow.

A task queue is the opposite kind of fact. The task is a decorated function — a
declaration, in as many words — and `process_file.delay(pk)` names it at the call
site. Across the same corpus that is 13 real tasks in 4 projects with 10 enqueue
sites, all of it written down. So this module reads task queues and nothing else, and
raw pub/sub stays undrawn until there is something honest to draw it from.
"""

from __future__ import annotations

import ast

# The decorators that turn a function into a queued task. Matched exactly, never by
# "the word task appears": that looser reading counted `@router.post("/tasks/report")`
# and `@pytest.mark.parametrize("task_id", ...)` as tasks.
TASK_NAMES = {"shared_task", "task", "periodic_task", "actor"}

# `.delay(...)` and `.apply_async(...)` are unambiguous — nothing else in the corpus
# uses either name. `.send()` and `.enqueue()` are not: both are ordinary English and
# both appear on things that are not queues.
ENQUEUE = {"delay", "apply_async"}


def _decorator_name(node: ast.expr) -> str | None:
    """`@shared_task`, `@celery_app.task(bind=True)` -> the dotted name, else None."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id if node.id in TASK_NAMES else None
    if isinstance(node, ast.Attribute):
        return ast.unparse(node) if node.attr in TASK_NAMES else None
    return None


def _kwarg(node: ast.expr, name: str) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            return value if isinstance(value, str) else None
    return None


def detect_tasks(tree: ast.Module) -> dict:
    """`{"declared": [...], "calls": [...]}` for one file."""
    declared = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            name = _decorator_name(decorator)
            if not name:
                continue
            declared.append({
                "name": node.name,
                "line": node.lineno,
                "decorator": name,
                # A task may be registered under a name of its own, and then that is
                # what a worker's logs and a routing table call it.
                "task_name": _kwarg(decorator, "name"),
                # The one piece of topology a task queue writes down.
                "queue": _kwarg(decorator, "queue"),
            })
            break

    calls = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in ENQUEUE:
            continue
        target = node.func.value
        if isinstance(target, ast.Name):
            calls.append({"task": target.id, "line": node.lineno})
        elif isinstance(target, ast.Attribute):
            calls.append({"task": target.attr, "line": node.lineno})
    return {"declared": declared, "calls": calls}
