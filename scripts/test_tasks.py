"""Tests for background tasks — the one part of the broker family that is DECLARED.

Run: uv run --with pytest --with pygraphviz --with pathspec \
         --with tree-sitter-language-pack pytest test_tasks.py -q

Measured before it was written. Across 2845 files the verb-based readings of a broker
are noise — `poll` is `subprocess.poll()` seven times out of seven, `subscribe` is an
image API — and exactly one file both publishes and consumes, through helpers that
compute their stream names. A task queue is the opposite: the task is a decorated
function, the enqueue names it, and both are written down.
"""

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import soulreap as sr
from srlib.tasks import detect_tasks

WORKER = '''
from celery import shared_task
from app.celery import celery_app

@shared_task
def process_file(pk):
    return pk

@celery_app.task(bind=True, max_retries=2)
def run_scan(self, job_id):
    return job_id

@shared_task(queue="slow", name="reports.build")
def build_report(spec):
    return spec
'''

CALLER = '''
from app.tasks import process_file, run_scan

def on_upload(pk):
    process_file.delay(pk)

def on_button(job):
    run_scan.apply_async(args=[job])
'''


def _tasks(source: str):
    return detect_tasks(ast.parse(source))


# ------------------------------------------------------------- 1. detector ---


def test_a_decorated_function_is_a_task():
    assert [t["name"] for t in _tasks(WORKER)["declared"]] == [
        "process_file", "run_scan", "build_report"
    ]


def test_the_decorator_that_made_it_a_task_is_recorded():
    by_name = {t["name"]: t for t in _tasks(WORKER)["declared"]}
    assert by_name["process_file"]["decorator"] == "shared_task"
    assert by_name["run_scan"]["decorator"] == "celery_app.task"


def test_a_queue_named_in_the_decorator_is_kept():
    """`queue="slow"` is the one piece of topology a task queue writes down."""
    by_name = {t["name"]: t for t in _tasks(WORKER)["declared"]}
    assert by_name["build_report"]["queue"] == "slow"
    assert by_name["process_file"]["queue"] is None


def test_an_explicit_task_name_wins_over_the_function_name():
    by_name = {t["name"]: t for t in _tasks(WORKER)["declared"]}
    assert by_name["build_report"]["task_name"] == "reports.build"


def test_an_enqueue_names_the_task_it_puts_on_the_queue():
    assert [c["task"] for c in _tasks(CALLER)["calls"]] == ["process_file", "run_scan"]


def test_a_route_is_not_a_task():
    """`@router.post` was a third of what a decorator search that looked for the
    word "task" turned up."""
    source = '''
from fastapi import APIRouter
router = APIRouter()

@router.post("/tasks/report")
def run_report():
    return {}
'''
    assert _tasks(source)["declared"] == []


def test_a_test_marker_is_not_a_task():
    source = '''
import pytest

@pytest.mark.parametrize("task_id", ["a", "b"])
def test_valid_task_id_accepted(task_id):
    assert task_id
'''
    assert _tasks(source)["declared"] == []


def test_a_plain_function_is_not_a_task():
    assert _tasks("def work():\n    return 1\n")["declared"] == []


# --------------------------------------------------------------- 2. render ---


def _repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "tasks.py").write_text(WORKER)
    (tmp_path / "app" / "views.py").write_text(CALLER)
    return tmp_path


def test_plan_records_the_tasks_and_their_call_sites(tmp_path):
    out = _repo(tmp_path) / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    facts = json.loads((out / ".soulreap" / "facts.json").read_text())["files"]
    assert [t["name"] for t in facts["app/tasks.py"]["tasks"]["declared"]] == [
        "process_file", "run_scan", "build_report"
    ]
    assert [c["task"] for c in facts["app/views.py"]["tasks"]["calls"]] == [
        "process_file", "run_scan"
    ]


def test_render_draws_who_enqueues_what(tmp_path):
    out = _repo(tmp_path) / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    stats = sr.cmd_render(root=tmp_path, out=out)
    assert stats["tasks_declared"] == 3
    drawing = (out / "diagrams" / "tasks.svg").read_text()
    assert "process_file" in drawing and "run_scan" in drawing
    assert "app/views.py" in drawing        # the producer, named
    assert "slow" in drawing                # the queue a task declares


def test_a_task_nobody_enqueues_is_still_drawn(tmp_path):
    """A task with no visible producer is the interesting one — it is either dead or
    triggered from somewhere this parser cannot see, and both are worth knowing."""
    (tmp_path / "tasks.py").write_text(WORKER)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_render(root=tmp_path, out=out)
    assert "build_report" in (out / "diagrams" / "tasks.svg").read_text()


def test_no_task_means_no_picture(tmp_path):
    (tmp_path / "app.py").write_text("def go():\n    return 1\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    stats = sr.cmd_render(root=tmp_path, out=out)
    assert not (out / "diagrams" / "tasks.svg").exists()
    assert stats["tasks_declared"] == 0


def test_a_stale_task_picture_is_removed(tmp_path):
    out = tmp_path / "soulreap"
    (out / "diagrams").mkdir(parents=True)
    (out / "diagrams" / "tasks.svg").write_text("<svg/>")
    (tmp_path / "app.py").write_text("def go():\n    return 1\n")
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_render(root=tmp_path, out=out)
    assert not (out / "diagrams" / "tasks.svg").exists()


# ----------------------------------------------------------- 3. the skeleton ---


def test_the_skeleton_tells_a_carder_what_it_declares_and_what_it_enqueues(tmp_path):
    out = _repo(tmp_path) / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    worker = sr.cmd_skeleton(root=tmp_path, out=out, target="app/tasks.py")
    assert "## Tasks" in worker
    assert "declares process_file" in worker and "queue slow" in worker
    caller = sr.cmd_skeleton(root=tmp_path, out=out, target="app/views.py")
    assert "enqueues process_file" in caller


def test_an_enqueue_of_a_task_this_repository_never_declares_says_so(tmp_path):
    (tmp_path / "views.py").write_text(
        "from elsewhere import ship\n\ndef go():\n    ship.delay(1)\n"
    )
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    text = sr.cmd_skeleton(root=tmp_path, out=out, target="views.py")
    assert "declared outside this repository" in text
