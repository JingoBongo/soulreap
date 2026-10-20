"""External programs a repository shells out to — the fifth context source.

Split out of test_context.py deliberately: that file belongs to the manifest/config/
compose detectors, this one to the AST walk that finds `subprocess.run(["git", ...])`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import soulreap as sr
from srlib import context as sr_context
from srlib.facts import extract_python


def _binaries(source: str) -> list[str]:
    facts = extract_python(source, "app.py")
    return sorted(facts.to_dict().get("binaries") or [])


def test_a_literal_argv_names_the_program():
    """fuse_core shells out to git for every clone, fetch and reset — the single
    biggest thing it talks to, and the context diagram could not see it."""
    assert _binaries('import subprocess\nsubprocess.run(["git", "fetch", "--all"])\n') == ["git"]


def test_popen_counts_too():
    source = 'import subprocess\nsubprocess.Popen(["systemctl", "restart", "fuse"])\n'
    assert _binaries(source) == ["systemctl"]


def test_a_string_command_is_read_when_it_is_a_literal():
    assert _binaries('import subprocess\nsubprocess.run("psql -c select 1", shell=True)\n') == ["psql"]


def test_a_command_built_from_a_variable_is_not_guessed():
    """A name we cannot read is not a program we can name. Silence beats invention."""
    source = "import subprocess\ncmd = build()\nsubprocess.run(cmd)\n"
    assert _binaries(source) == []


def test_an_interpreter_path_keeps_only_the_program_name():
    source = 'import subprocess\nsubprocess.run(["/usr/bin/env", "uv", "run", "x.py"])\n'
    assert _binaries(source) == ["uv"]


def test_the_python_running_this_process_is_not_an_external_system():
    """`sys.executable` is this program, and a venv's own python is this program too."""
    source = 'import subprocess, sys\nsubprocess.run([sys.executable, "-m", "pip", "install", "x"])\n'
    assert _binaries(source) == []


def test_binaries_reach_the_context_diagram():
    facts = {"app.py": {"binaries": ["git", "docker"], "imports": [], "io": {}}}
    found = sr_context.from_binaries(facts)
    systems = {item["system"] for item in found}
    assert {"git", "Docker"} <= systems


def test_fuse_core_finally_sees_git(tmp_path):
    """The repo that reported no external systems at all clones over git in
    git_utils.py and restarts itself through systemctl."""
    (tmp_path / "git_utils.py").write_text(
        'import subprocess\n\n\ndef check(path):\n'
        '    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path)\n',
        encoding="utf-8",
    )
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_render(root=tmp_path, out=out)
    drawn = (out / "diagrams" / "context.dot").read_text(encoding="utf-8")
    assert "git" in drawn
    assert "binary" in drawn, "the edge must say how the system was found"


def test_an_f_string_command_gives_up_its_program_name():
    """fuse_core's git_utils.py builds every call as
    `[f'git -c safe.directory={path} -C {path} rev-parse HEAD']` — the arguments are
    interpolated but the program is a literal, and refusing to read it lost the single
    system that repository talks to most."""
    source = (
        "import subprocess\n"
        "def check(path):\n"
        "    return subprocess.run([f'git -C {path} rev-parse HEAD'], shell=True)\n"
    )
    assert _binaries(source) == ["git"]


def test_an_f_string_that_starts_with_a_placeholder_is_still_not_guessed():
    source = (
        "import subprocess\n"
        "def run(tool, path):\n"
        "    return subprocess.run([f'{tool} -C {path} status'], shell=True)\n"
    )
    assert _binaries(source) == []


def test_a_command_literal_behind_a_project_wrapper_is_still_found():
    """fuse_core runs git through its own `os_utils.start_system_barrel_process`, not
    through subprocess, so watching the call site alone misses the one system that
    repository talks to most. A string whose first word is a program we know by name
    is evidence on its own."""
    source = (
        "import os_utils\n"
        "def status(path):\n"
        "    return os_utils.start_system_barrel_process(\n"
        "        [f'git -c safe.directory={path} -C {path} rev-parse HEAD'])\n"
    )
    assert _binaries(source) == ["git"]


def test_a_docstring_that_mentions_a_program_is_not_a_call():
    """Prose about git is not a git call, and the difference is that a docstring is a
    statement of its own."""
    source = '''def clone(url):
    """git clone is what this wraps, eventually."""
    return url
'''
    assert _binaries(source) == []


def test_an_unknown_first_word_in_a_bare_string_is_not_a_program():
    """The literal signal is whitelist-driven on purpose: without that guard every
    sentence in the file becomes a candidate external system."""
    source = "MESSAGE = 'hello there, this is not a command'\n"
    assert _binaries(source) == []


def test_shell_file_utilities_are_not_external_systems():
    """`rmdir` is a shell builtin this repo runs on its own directories; listing it as
    a system it "talks to" put a fourth node on fuse_core's context beside git and
    systemd, and a reader has to skip it every time."""
    source = 'import subprocess\nsubprocess.run(["rmdir", "/s", "/q", path])\n'
    assert _binaries(source) == [] or "rmdir" not in sr_context.SYSTEM_BY_BINARY

    facts = {"a.py": {"binaries": ["rmdir", "tar", "unzip", "git"], "imports": [], "io": {}}}
    systems = {item["system"] for item in sr_context.from_binaries(facts)}
    assert systems == {"git"}, f"only git is a system here, got {systems}"
