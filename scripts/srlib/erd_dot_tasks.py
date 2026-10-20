"""The task graph (who enqueues what, across the repository) drawn by Graphviz.

`task_graph` reads the facts into nodes and edges; `tasks_dot_svg` draws them.
"""

from __future__ import annotations

import html

from .erd_dot_common import TAG_SIZE, section_labels
from .erd_dot_structures import structure_slug

# ---------------------------------------------------------------- tasks ---

TASK_HEADER = "#EAF3EA"   # green: work that happens later, elsewhere


def task_graph(facts: dict) -> dict:
    """`{"tasks": {...}, "edges": [...]}` — who enqueues what, across the repository.

    Repository-wide rather than per module, because that is the shape of the fact: a
    task is declared in one file and enqueued from others, and a per-file picture
    would cut every edge it has.
    """
    tasks: dict[str, dict] = {}
    for path in sorted(facts):
        for task in ((facts[path] or {}).get("tasks") or {}).get("declared") or []:
            tasks.setdefault(task["name"], {**task, "source": path})
    edges: list[tuple[str, str]] = []
    for path in sorted(facts):
        for call in ((facts[path] or {}).get("tasks") or {}).get("calls") or []:
            if call["task"] in tasks and (path, call["task"]) not in edges:
                edges.append((path, call["task"]))
    return {"tasks": tasks, "edges": edges}


def tasks_dot_svg(graph: dict, display=lambda name: name) -> str | None:
    """Tasks and their producers, or None when there is no task or no Graphviz."""
    try:
        import pygraphviz
    except Exception:
        return None
    tasks = graph.get("tasks") or {}
    if not tasks:
        return None

    edges = graph.get("edges") or []
    orphans = sum(1 for name in tasks if not any(name == t for _p, t in edges))
    note = ""
    if orphans:
        # The interesting ones. A task nobody enqueues is either dead or triggered
        # from somewhere no parser can see — a beat schedule, another service, a
        # management command — and both are worth a reader's attention.
        noun = "task has" if orphans == 1 else "tasks have"
        note = (f" · {orphans} {noun} no producer in this repository: dead, or "
                "triggered from a schedule or another service")

    picture = pygraphviz.AGraph(
        directed=True, strict=False, rankdir="LR", splines="spline",
        nodesep="0.3", ranksep="1.0", bgcolor="white",
        fontname="Helvetica", fontsize="14", fontcolor="#666666", labelloc="b",
        label="Background tasks · an arrow runs from the file that enqueues a task to "
              "the task itself · the work happens later, in a worker process, not "
              "where the arrow starts" + note,
    )

    producers = sorted({path for path, _task in edges})
    for path in producers:
        picture.add_node(
            f"src::{path}", label=display(path), shape="box", style="rounded",
            color="#9AA0B5", fontcolor="#33373F", fontname="Helvetica", fontsize="14",
        )
    by_source: dict[str, list[str]] = {}
    for name, task in tasks.items():
        by_source.setdefault(task.get("source", ""), []).append(name)
    sections = section_labels(sorted(by_source))
    for source, names in sorted(by_source.items()):
        cluster = picture.add_subgraph(
            name=f"cluster_{structure_slug(source)}", label=sections.get(source, source),
            style="rounded,dashed", color="#9AA0B5", fontcolor="#5A6072",
            fontname="Courier", fontsize="14", labelloc="t",
        )
        for name in sorted(names):
            task = tasks[name]
            rows = []
            if task.get("task_name"):
                rows.append(f"registered as {task['task_name']}")
            if task.get("queue"):
                rows.append(f"queue {task['queue']}")
            rows.append(task["decorator"])
            body = "<br/>".join(
                f'<font point-size="{TAG_SIZE}" color="#5A6072">{html.escape(row)}</font>'
                for row in rows
            )
            cluster.add_node(
                name,
                label=f"<<b>{html.escape(name)}</b><br/>{body}>",
                shape="box", style="rounded,filled", fillcolor=TASK_HEADER,
                color="#5A7A5A", penwidth="1.6", fontname="Helvetica", fontsize="15",
                # Vertical room for the second line: at 0.1 the decorator's baseline
                # sat on the box border and read as struck through.
                margin="0.2,0.18",
            )
    for path, task in edges:
        picture.add_edge(f"src::{path}", task, color="#5A7A5A", penwidth="1.6",
                         arrowsize="0.8")
    picture.layout(prog="dot")
    return picture.draw(format="svg").decode("utf-8")
