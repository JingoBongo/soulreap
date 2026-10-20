"""Entry points beyond web decorators: Dagster, `__main__` guards and modules, console scripts."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib import flow
from srlib.build import build_facts


def _entries(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str, str]]:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return {(e["file"], e["name"], e["kind"]) for e in flow.entry_points(build_facts(tmp_path).files)}


def test_dagster_assets_and_schedules_are_entries(tmp_path):
    src = ("import dagster as dg\n\n@dg.asset\ndef store():\n    return 1\n\n"
           "@dg.schedule(cron_schedule='0 6 * * *')\ndef nightly():\n    pass\n")
    found = _entries(tmp_path, {"pipes.py": src})
    assert {("pipes.py", "store", "decorator"), ("pipes.py", "nightly", "decorator")} <= found


def test_function_called_under_a_main_guard_is_an_entry(tmp_path):
    src = "def run():\n    return 1\n\n\nif __name__ == '__main__':\n    run()\n"
    assert ("tool.py", "run", "main_guard") in _entries(tmp_path, {"tool.py": src})


def test_dunder_main_module_calling_an_imported_cli_makes_that_an_entry(tmp_path):
    files = {
        "pkg/__init__.py": "",
        "pkg/cli.py": "def cli():\n    return 0\n",
        "pkg/__main__.py": "from pkg.cli import cli\n\ncli()\n",
    }
    assert ("pkg/cli.py", "cli", "main_guard") in _entries(tmp_path, files)


def test_console_scripts_in_pyproject_are_entries(tmp_path):
    files = {
        "pyproject.toml": "[project]\nname = 'x'\n\n[project.scripts]\nxtool = 'pkg.tool:start'\n",
        "pkg/__init__.py": "",
        "pkg/tool.py": "def start():\n    return 0\n",
    }
    assert ("pkg/tool.py", "start", "script") in _entries(tmp_path, files)


def test_console_script_entries_do_not_accumulate_across_incremental_builds(tmp_path):
    files = {"pyproject.toml": "[project.scripts]\nxtool = 'pkg.tool:start'\n",
             "pkg/__init__.py": "", "pkg/tool.py": "def start():\n    return 0\n"}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    first = build_facts(tmp_path).files
    second = build_facts(tmp_path, first).files
    third = build_facts(tmp_path, second).files
    assert third["pkg/tool.py"]["script_entries"] == ["start"]


def test_attribute_calls_under_a_main_guard_do_not_make_entries(tmp_path):
    src = ("import asyncio, uvicorn\n\ndef run():\n    return 1\n\ndef main():\n    return 2\n\n\n"
           "if __name__ == '__main__':\n    uvicorn.run('app')\n    asyncio.run(main())\n")
    found = _entries(tmp_path, {"svc.py": src})
    assert ("svc.py", "main", "main_guard") in found and ("svc.py", "run", "main_guard") not in found


def test_raise_systemexit_in_dunder_main_is_seen(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/cli.py": "def cli():\n    return 0\n",
             "pkg/__main__.py": "from pkg.cli import cli\n\nraise SystemExit(cli())\n"}
    assert ("pkg/cli.py", "cli", "main_guard") in _entries(tmp_path, files)


def test_aliased_import_called_under_a_main_guard_is_the_original_function(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/cli.py": "def main():\n    return 0\n",
             "pkg/__main__.py": "from pkg.cli import main as entry\n\nentry()\n"}
    assert ("pkg/cli.py", "main", "main_guard") in _entries(tmp_path, files)


def test_aliased_import_inside_the_main_guard_is_followed(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/cli.py": "def main():\n    return 0\n",
             "run.py": "if __name__ == '__main__':\n    from pkg.cli import main as go\n    go()\n"}
    assert ("pkg/cli.py", "main", "main_guard") in _entries(tmp_path, files)
