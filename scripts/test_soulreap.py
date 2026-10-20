"""Tests for soulreap.py.

Run: uv run --with pytest --with pygraphviz --with pathspec \
         --with tree-sitter-language-pack pytest test_soulreap.py -q

The extra --with flags are the script's own PEP 723 dependencies plus the drawing
engine: pygraphviz draws every SVG (the geometry tests below measure its output, so
without it they skip — see conftest.py), pathspec is the ignore-file matcher and
tree-sitter-language-pack reads the non-Python languages.
"""

import ast
import contextlib
import itertools
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import soulreap as sr
import srlib.callgraph
import srlib.diagrams
import srlib.facts
import srlib.skeleton
import srlib.svg
import srlib.svggeom
import srlib.svglint
import srlib.erd_dot

# --------------------------------------------------------------------------
# Task 1: walk, classification, hashing
# --------------------------------------------------------------------------


def test_classify_python_file_returns_code_kind_and_python_lang():
    kind, lang, _category = sr.classify(Path("src/api/main.py"))
    assert kind == "code"
    assert lang == "python"


def test_classify_github_workflow_returns_non_code_ci_category():
    kind, _lang, category = sr.classify(Path(".github/workflows/deploy.yml"))
    assert kind == "non_code"
    assert category == "ci"


def test_classify_terraform_returns_non_code_iac_category():
    kind, _, category = sr.classify(Path("infra/live/dev/vpc/main.tf"))
    assert kind == "non_code"
    assert category == "iac"


def test_classify_lockfile_returns_skip_kind():
    kind, _, _ = sr.classify(Path("package-lock.json"))
    assert kind == "skip"


def test_classify_env_file_returns_secret_kind():
    kind, _, _ = sr.classify(Path(".env.production"))
    assert kind == "secret"


def test_walk_respects_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored/\n*.log\n")
    (tmp_path / "keep.py").write_text("x = 1\n")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "hidden.py").write_text("y = 2\n")
    (tmp_path / "noisy.log").write_text("spam\n")

    paths = {e.path for e in sr.walk(tmp_path)}

    assert "keep.py" in paths
    assert "ignored/hidden.py" not in paths
    assert "noisy.log" not in paths


def test_walk_skips_builtin_denylist_directories(tmp_path):
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("x")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "thing.py").write_text("x")
    (tmp_path / "app.py").write_text("x = 1\n")

    paths = {e.path for e in sr.walk(tmp_path)}

    assert paths == {"app.py"}


def test_walk_hashes_file_content(tmp_path):
    (tmp_path / "a.py").write_text("value = 1\n")
    entry = next(e for e in sr.walk(tmp_path) if e.path == "a.py")
    assert len(entry.sha) == 64
    assert entry.lines == 1


def test_walk_extracts_env_key_names_but_never_values(tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=postgres://user:hunter2@host/db\nAPI_KEY=sk-secret\n")
    entry = next(e for e in sr.walk(tmp_path) if e.path == ".env")

    assert entry.kind == "secret"
    assert entry.env_keys == ["DATABASE_URL", "API_KEY"]
    blob = json.dumps(entry.to_dict())
    assert "hunter2" not in blob
    assert "sk-secret" not in blob


# --------------------------------------------------------------------------
# Task 2: status diff
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Task 3: Python facts
# --------------------------------------------------------------------------

PY_SAMPLE = '''
"""Auth routes."""
import os
from fastapi import APIRouter
from ..db.session import get_session

JWT_TTL = 3600
_ROUTER = APIRouter()


class AuthService:
    """Wraps login and refresh."""

    def __init__(self, secret: str) -> None:
        self.secret = secret

    async def login(self, email_address: str, plain_password: str) -> str:
        async with get_session() as session:
            row = await session.fetch("SELECT * FROM users WHERE email = $1", email_address)
        return row


def walk_tree(node, depth: int = 0):
    # TODO: handle cycles
    for child in node.children:
        walk_tree(child, depth + 1)
'''


def test_extract_python_collects_module_imports():
    facts = sr.extract_python(PY_SAMPLE, "src/auth.py")
    modules = {i["module"] for i in facts.imports}
    assert "os" in modules
    assert "fastapi" in modules
    assert "..db.session" in modules


def test_extract_python_collects_uppercase_module_constants():
    facts = sr.extract_python(PY_SAMPLE, "src/auth.py")
    names = {c["name"] for c in facts.constants}
    assert "JWT_TTL" in names


def test_extract_python_records_class_with_methods_and_line_range():
    facts = sr.extract_python(PY_SAMPLE, "src/auth.py")
    cls = next(c for c in facts.classes if c["name"] == "AuthService")
    assert cls["doc"] == "Wraps login and refresh."
    assert {m["name"] for m in cls["methods"]} == {"__init__", "login"}
    assert cls["end_line"] > cls["start_line"]


def test_extract_python_marks_async_method_signature():
    facts = sr.extract_python(PY_SAMPLE, "src/auth.py")
    cls = next(c for c in facts.classes if c["name"] == "AuthService")
    login = next(m for m in cls["methods"] if m["name"] == "login")
    assert login["is_async"] is True
    assert "email_address: str" in login["signature"]
    assert login["signature"].endswith("-> str")


def test_extract_python_detects_direct_recursion():
    facts = sr.extract_python(PY_SAMPLE, "src/auth.py")
    assert "walk_tree" in facts.recursive


def test_extract_python_detects_mutual_recursion_cycle():
    src = "def ping(n):\n    return pong(n)\n\ndef pong(n):\n    return ping(n - 1)\n"
    facts = sr.extract_python(src, "m.py")
    assert "ping" in facts.recursive
    assert "pong" in facts.recursive


def test_extract_python_detects_todo_markers_with_line_numbers():
    facts = sr.extract_python(PY_SAMPLE, "src/auth.py")
    assert any(t["kind"] == "TODO" and "cycles" in t["text"] for t in facts.todos)


def test_extract_python_flags_database_io_with_evidence():
    facts = sr.extract_python(PY_SAMPLE, "src/auth.py")
    assert "db" in facts.io
    assert facts.io["db"]


def test_extract_python_flags_threading_concurrency():
    src = "import threading\n\ndef go():\n    threading.Thread(target=work).start()\n"
    facts = sr.extract_python(src, "w.py")
    assert "threads" in facts.concurrency


def test_extract_python_flags_asyncio_task_concurrency():
    src = "import asyncio\n\nasync def go():\n    await asyncio.gather(*tasks)\n"
    facts = sr.extract_python(src, "w.py")
    assert "async" in facts.concurrency


def test_extract_python_flags_generator_function():
    src = "def stream():\n    for row in rows:\n        yield row\n"
    facts = sr.extract_python(src, "g.py")
    fn = next(f for f in facts.functions if f["name"] == "stream")
    assert fn["is_generator"] is True


def test_extract_python_on_syntax_error_returns_facts_with_error():
    facts = sr.extract_python("def broken(:\n", "bad.py")
    assert facts.parse_error is not None
    assert facts.functions == []


# --------------------------------------------------------------------------
# Task 4: tree-sitter languages
# --------------------------------------------------------------------------

TS_SAMPLE = """
import { useState } from "react";
import { apiClient } from "../lib/api";

export const MAX_RETRIES = 5;

export function useAuth(initialToken: string) {
  const [token, setToken] = useState(initialToken);
  return { token, setToken };
}

export default class SessionStore {
  refresh(force: boolean) {
    return apiClient.post("/refresh");
  }
}
"""


def test_extract_treesitter_collects_typescript_imports():
    facts = sr.extract_source(TS_SAMPLE, "src/hooks/useAuth.ts")
    modules = {i["module"] for i in facts.imports}
    assert "react" in modules
    assert "../lib/api" in modules


def test_extract_treesitter_collects_typescript_functions_and_classes():
    facts = sr.extract_source(TS_SAMPLE, "src/hooks/useAuth.ts")
    assert "useAuth" in {f["name"] for f in facts.functions}
    assert "SessionStore" in {c["name"] for c in facts.classes}


def test_extract_source_dispatches_python_to_ast_extractor():
    facts = sr.extract_source(PY_SAMPLE, "src/auth.py")
    assert "walk_tree" in {f["name"] for f in facts.functions}


def test_extract_source_unknown_language_still_returns_facts():
    facts = sr.extract_source("some prose\n", "notes.wat")
    assert facts.functions == []
    assert facts.lines == 1


# --------------------------------------------------------------------------
# Task 5: import graph
# --------------------------------------------------------------------------


def test_build_graph_resolves_python_package_import_to_file():
    facts = {
        "app/main.py": sr.FileFacts(
            path="app/main.py", lang="python", imports=[{"module": "app.db", "names": ["get"]}]
        ),
        "app/db.py": sr.FileFacts(path="app/db.py", lang="python", imports=[]),
    }
    graph = sr.build_graph(facts)
    assert "app/db.py" in graph.imports["app/main.py"]
    assert "app/main.py" in graph.imported_by["app/db.py"]


def test_build_graph_resolves_relative_typescript_import():
    facts = {
        "src/page.tsx": sr.FileFacts(path="src/page.tsx", lang="tsx", imports=[{"module": "./lib/api", "names": []}]),
        "src/lib/api.ts": sr.FileFacts(path="src/lib/api.ts", lang="typescript", imports=[]),
    }
    graph = sr.build_graph(facts)
    assert "src/lib/api.ts" in graph.imports["src/page.tsx"]


def test_build_graph_detects_import_cycle():
    facts = {
        "a.py": sr.FileFacts(path="a.py", lang="python", imports=[{"module": "b", "names": []}]),
        "b.py": sr.FileFacts(path="b.py", lang="python", imports=[{"module": "a", "names": []}]),
    }
    graph = sr.build_graph(facts)
    assert any({"a.py", "b.py"} == set(c) for c in graph.cycles)


def test_build_graph_ignores_third_party_imports():
    facts = {"a.py": sr.FileFacts(path="a.py", lang="python", imports=[{"module": "fastapi", "names": []}])}
    graph = sr.build_graph(facts)
    assert graph.imports["a.py"] == []


# --------------------------------------------------------------------------
# Task 6: plan command
# --------------------------------------------------------------------------


@pytest.fixture
def sample_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("from src.util import helper\n\ndef run():\n    return helper()\n")
    (tmp_path / "src" / "util.py").write_text("def helper():\n    return 42\n")
    (tmp_path / "README.md").write_text("# Demo\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
    (tmp_path / ".env").write_text("SECRET_TOKEN=abc123\n")
    return tmp_path


@pytest.fixture
def layered_repo(tmp_path):
    """Two packages, one importing the other — the only shape the package diagram
    has anything to say about. `sample_repo` has a single package and is now the
    silence case."""
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "src" / "api" / "handler.py").write_text(
        "from src.core.store import load\n\ndef get():\n    return load()\n"
    )
    (tmp_path / "src" / "core" / "store.py").write_text("def load():\n    return 42\n")
    (tmp_path / "README.md").write_text("# Demo\n")
    return tmp_path


def _read_facts(out):
    """The files map out of facts.json, whatever envelope it is wrapped in."""
    blob = json.loads((out / ".soulreap" / "facts.json").read_text(encoding="utf-8"))
    return blob["files"] if "files" in blob and "facts_version" in blob else blob


def test_plan_lists_non_code_inventory_by_category(sample_repo):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap")
    plan = json.loads((sample_repo / "soulreap" / ".soulreap" / "plan.json").read_text())
    categories = plan["non_code"]
    assert ".github/workflows/ci.yml" in categories["ci"]
    assert "README.md" in categories["docs"]


def test_plan_reports_env_keys_without_values(sample_repo):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap")
    raw = (sample_repo / "soulreap" / ".soulreap" / "plan.json").read_text()
    assert "SECRET_TOKEN" in raw
    assert "abc123" not in raw


def test_plan_builds_annotated_directory_tree(sample_repo):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap")
    plan = json.loads((sample_repo / "soulreap" / ".soulreap" / "plan.json").read_text())
    assert "src" in plan["tree"]
    assert plan["tree"]["src"]["files"] == 2


# --------------------------------------------------------------------------
# Task 7: skeleton command
# --------------------------------------------------------------------------


def test_skeleton_output_contains_signatures_and_line_anchors(sample_repo, capsys):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap")
    sr.cmd_skeleton(root=sample_repo, out=sample_repo / "soulreap", target="src/util.py")
    printed = capsys.readouterr().out
    assert "helper" in printed
    assert "L1" in printed


def test_skeleton_output_lists_reverse_dependencies(sample_repo, capsys):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap")
    sr.cmd_skeleton(root=sample_repo, out=sample_repo / "soulreap", target="src/util.py")
    printed = capsys.readouterr().out
    assert "src/main.py" in printed


# --------------------------------------------------------------------------
# Task 8: render command
# --------------------------------------------------------------------------


def test_render_writes_a_package_diagram_naming_both_packages(layered_repo):
    out = layered_repo / "soulreap"
    sr.cmd_plan(root=layered_repo, out=out)
    sr.cmd_render(root=layered_repo, out=out, diagrams="all")
    mermaid = (out / "diagrams" / "packages.dot").read_text()
    assert "digraph" in mermaid
    assert "entry" in mermaid and "foundation" in mermaid


def test_render_draws_no_package_diagram_when_nothing_imports_across_packages(sample_repo):
    """Same rule as the context diagram, for the same reason.

    `fuse_core` is eight modules in the repository root: the picture was one box
    reading "isolated", under a title promising layering and coupling weight. A
    diagram that measures nothing is worse than no diagram — it costs a click and
    pays nothing back. The sentence says what was counted instead.
    """
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out)
    stats = sr.cmd_render(root=sample_repo, out=out, diagrams="all")
    assert not (out / "diagrams" / "packages.dot").exists()
    assert not (out / "diagrams" / "packages.svg").exists()
    assert "packages_note" in stats
    assert "one package" in stats["packages_note"]  # what was counted, in words


def test_a_stale_package_diagram_is_removed_once_it_has_nothing_to_say(sample_repo):
    out = sample_repo / "soulreap"
    (out / "diagrams").mkdir(parents=True)
    (out / "diagrams" / "packages.dot").write_text("graph TD\n  n0[\"old\"]\n")
    (out / "diagrams" / "packages.svg").write_text("<svg/>")
    sr.cmd_plan(root=sample_repo, out=out)
    sr.cmd_render(root=sample_repo, out=out, diagrams="all")
    assert not (out / "diagrams" / "packages.dot").exists()
    assert not (out / "diagrams" / "packages.svg").exists()


def test_render_collapses_graph_to_the_deepest_level_that_fits(sample_repo):
    out = sample_repo / "soulreap"
    for package in ("alpha", "beta", "gamma"):
        target = sample_repo / "src" / package
        target.mkdir(parents=True)
        for i in range(15):
            (target / f"mod{i}.py").write_text("from src.util import helper\n")

    sr.cmd_plan(root=sample_repo, out=out)
    sr.cmd_render(root=sample_repo, out=out, diagrams="all")

    mermaid = (out / "diagrams" / "packages.dot").read_text()
    assert "src/alpha" in mermaid  # not flattened to a single "src" node
    assert "mod7" not in mermaid  # but not one node per file either


def test_render_drops_self_edges_from_a_collapsed_graph(sample_repo):
    out = sample_repo / "soulreap"
    target = sample_repo / "src" / "alpha"
    target.mkdir(parents=True)
    for i in range(45):
        (target / f"mod{i}.py").write_text("from src.alpha.mod0 import thing\n")
    # one arrow that leaves the package, so there is a diagram at all to check
    (target / "mod0.py").write_text("from src.util import helper\n\nthing = 1\n")

    sr.cmd_plan(root=sample_repo, out=out)
    sr.cmd_render(root=sample_repo, out=out, diagrams="all")

    mermaid = (out / "diagrams" / "packages.dot").read_text()
    edges = [ln for ln in mermaid.splitlines() if "-->" in ln]
    assert all(ln.split("-->")[0].strip() != ln.split("-->")[1].strip() for ln in edges)


def test_render_collapses_large_graph_to_package_level(sample_repo):
    out = sample_repo / "soulreap"
    pkg = sample_repo / "big"
    pkg.mkdir()
    for i in range(45):
        (pkg / f"mod{i}.py").write_text("from big.mod0 import thing\n")
    (pkg / "mod0.py").write_text("from src.util import helper\n\nthing = 1\n")
    sr.cmd_plan(root=sample_repo, out=out)
    sr.cmd_render(root=sample_repo, out=out, diagrams="all")
    mermaid = (out / "diagrams" / "packages.dot").read_text()
    assert "mod42" not in mermaid


# --------------------------------------------------------------------------
# CLI smoke test
# --------------------------------------------------------------------------


def test_cli_plan_subcommand_runs_end_to_end(sample_repo):
    script = Path(__file__).parent / "soulreap.py"
    result = subprocess.run(
        [sys.executable, str(script), "plan", "--root", str(sample_repo)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (sample_repo / "soulreap" / ".soulreap" / "plan.json").exists()


def _cli_plan(repo, *flags):
    script = Path(__file__).parent / "soulreap.py"
    result = subprocess.run(
        [sys.executable, str(script), "plan", "--root", str(repo), *flags],
        capture_output=True, check=False, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout, json.loads(
        (repo / "soulreap" / ".soulreap" / "plan.json").read_text(encoding="utf-8")
    )


def test_the_cli_says_what_it_left_out_and_why(repo_with_tests):
    """Nobody is asked any more, so it is the run's job to report — a map that quietly
    leaves out a third of the repository reads as a map of the whole thing. And the wording
    is no longer an invitation: tests are never part of the map."""
    out, _plan = _cli_plan(repo_with_tests)
    assert "left out" in out and "tests, never part of the map" in out
    assert "--with-tests" not in out


def test_the_old_skip_tests_flag_still_runs(repo_with_tests):
    """Anything that still passes it should not fall over; it is the default now."""
    _out, plan = _cli_plan(repo_with_tests, "--skip-tests")
    assert plan["excluded_by"]["skip_tests"] is True


# --------------------------------------------------------------------------
# Defects found running against real TypeScript repositories
# --------------------------------------------------------------------------

TS_REEXPORT = """
export { GET } from '@whats-next-cc/eager-auth/routes/callback';
export * from './helpers';
"""

TS_RECURSIVE = """
import { Heading } from '../schemas';

export async function fetchAll(urls: string[]): Promise<void> {
  await Promise.all(urls.map(parseOne));
}

function walkContent(node: unknown, headings: Heading[]): void {
  for (const child of node.children) {
    walkContent(child, headings);
  }
}
"""


def test_extract_treesitter_treats_re_export_as_a_dependency():
    facts = sr.extract_source(TS_REEXPORT, "app/api/route.ts")
    modules = {i["module"] for i in facts.imports}
    assert "@whats-next-cc/eager-auth/routes/callback" in modules
    assert "./helpers" in modules


def test_extract_treesitter_orders_imports_by_line():
    facts = sr.extract_source(TS_RECURSIVE, "lib/thing.ts")
    lines = [i["line"] for i in facts.imports]
    assert lines == sorted(lines)


def test_extract_treesitter_detects_self_recursion():
    facts = sr.extract_source(TS_RECURSIVE, "lib/thing.ts")
    assert "walkContent" in facts.recursive
    assert "fetchAll" not in facts.recursive


def test_skeleton_does_not_double_the_async_keyword():
    blob = {
        "functions": [{
            "name": "fetchAll", "signature": "async function fetchAll(urls: string[])",
            "is_async": True, "is_generator": False, "start_line": 4, "end_line": 6, "doc": None,
        }],
        "recursive": [],
    }
    rendered = "\n".join(srlib.skeleton._entity_lines(blob))
    assert "async async" not in rendered
    assert "async function fetchAll" in rendered


# --------------------------------------------------------------------------
# Diagram legibility: filtering, layout, collapse artifacts
# --------------------------------------------------------------------------


def _facts(**paths):
    """Build a facts dict: path -> deps."""
    return {p: {"lang": "python", "lines": 10, "deps": list(d)} for p, d in paths.items()}


def test_mermaid_excludes_test_files_from_the_diagram():
    facts = _facts(**{
        "src/app.py": [], "src/db.py": ["src/app.py"],
        "tests/test_app.py": ["src/app.py"],
    })
    out, _ = srlib.diagrams.package_dot(facts)
    assert "tests/test_app.py" not in out
    assert '"tests' not in out
    assert "src" in out


def test_mermaid_excludes_test_named_file_outside_a_tests_directory():
    facts = _facts(**{
        "src/app.py": [], "src/db.py": ["src/app.py"], "test_wordpress.py": ["src/app.py"],
    })
    out, _ = srlib.diagrams.package_dot(facts)
    assert "test_wordpress.py" not in out


def test_mermaid_excludes_loose_root_scripts_when_packages_exist():
    facts = _facts(**{
        "src/app.py": [], "src/db.py": ["src/app.py"], "scratch_probe.py": ["src/app.py"],
    })
    out, _ = srlib.diagrams.package_dot(facts)
    assert "scratch_probe.py" not in out


def test_mermaid_keeps_root_files_when_the_repo_has_no_packages():
    facts = _facts(**{"app.py": [], "util.py": ["app.py"]})
    out, _ = srlib.diagrams.package_dot(facts)
    assert "(root)" in out


def test_mermaid_uses_top_down_layout():
    facts = _facts(**{"src/a.py": [], "src/b.py": ["src/a.py"]})
    assert "digraph packages {" in srlib.diagrams.package_dot(facts)[0]


def test_mermaid_leaves_the_tests_package_out_of_the_picture():
    paths = {f"src/mod{i}.py": [] for i in range(38)}
    paths.update({f"tests/test_mod{i}.py": [f"src/mod{i}.py"] for i in range(10)})
    out, stats = srlib.diagrams.package_dot(_facts(**paths))
    assert '"tests' not in out
    assert stats["graph_excluded"] == 10


def test_mermaid_dots_a_mutual_edge_that_only_exists_after_collapsing():
    # a/one.py -> b/x.py and b/y.py -> a/two.py: mutual between packages,
    # but no single pair of files imports each other.
    paths = {f"a/f{i}.py": [] for i in range(30)}
    paths.update({f"b/f{i}.py": [] for i in range(30)})
    paths["a/one.py"] = ["b/x.py"]
    paths["b/x.py"] = []
    paths["b/y.py"] = ["a/two.py"]
    paths["a/two.py"] = []
    out, _ = srlib.diagrams.package_dot(_facts(**paths))
    assert "style=dashed" in out
    assert "not an import cycle" in out


def test_mermaid_keeps_a_real_file_level_cycle_solid():
    paths = {f"a/f{i}.py": [] for i in range(30)}
    paths.update({f"b/f{i}.py": [] for i in range(30)})
    paths["a/one.py"] = ["b/x.py"]
    paths["b/x.py"] = ["a/one.py"]
    out, _ = srlib.diagrams.package_dot(_facts(**paths))
    assert "style=dashed" not in out


def test_render_reports_graph_density(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out)
    result = sr.cmd_render(root=sample_repo, out=out, diagrams="all")
    assert "graph_nodes" in result
    assert "graph_edges" in result
    assert 0.0 <= result["graph_density"] <= 1.0


def test_render_also_writes_a_package_level_diagram(layered_repo):
    out = layered_repo / "soulreap"
    sr.cmd_plan(root=layered_repo, out=out)
    sr.cmd_render(root=layered_repo, out=out, diagrams="all")
    packages = (out / "diagrams" / "packages.dot").read_text()
    assert "digraph packages {" in packages
    assert "handler.py" not in packages  # always grouped, even when per-file would fit
    assert "api" in packages


def test_package_diagram_groups_a_flat_repo_under_root(tmp_path):
    facts = _facts(**{"app.py": [], "util.py": ["app.py"]})
    out, stats = srlib.diagrams.package_dot(facts)
    assert stats["graph_depth"] >= 1
    assert "(root)" in out


def test_extract_python_strips_query_strings_from_url_evidence():
    src = 'import httpx\n\nURL = "https://api.example.com/v1/search?api_key=REALSECRET123&q=x"\n'
    facts = sr.extract_python(src, "client.py")
    blob = json.dumps(facts.to_dict())
    assert "REALSECRET123" not in blob
    assert "https://api.example.com/v1/search" in blob


def test_extract_treesitter_strips_query_strings_from_url_evidence():
    src = 'const URL = "https://api.example.com/v1?token=REALSECRET123";\n'
    facts = sr.extract_source(src, "src/client.ts")
    blob = json.dumps(facts.to_dict())
    assert "REALSECRET123" not in blob


def test_extract_python_redacts_the_value_of_a_secret_named_constant():
    src = 'API_KEY = "sk-live-abc123def456"\nMAX_RETRIES = 3\n'
    facts = sr.extract_python(src, "settings.py")
    values = {c["name"]: c["value"] for c in facts.constants}
    assert "sk-live-abc123def456" not in json.dumps(facts.to_dict())
    assert values["API_KEY"] == "<redacted>"
    assert values["MAX_RETRIES"] == "3"


# --------------------------------------------------------------------------
# Diagrams that answer a question: context + layered packages
# --------------------------------------------------------------------------


def _f(path, deps=(), imports=(), io=None):
    return {
        "lang": "python", "lines": 10, "deps": list(deps),
        "imports": [{"module": m, "names": list(n)} for m, n in imports],
        "io": io or {},
    }


def test_context_diagram_names_a_database_behind_a_driver_import():
    facts = {
        "src/db.py": _f("src/db.py", imports=[("asyncpg", [])], io={"db": ["import asyncpg"]}),
        "src/app.py": _f("src/app.py", deps=["src/db.py"]),
    }
    out, _ = srlib.diagrams.context_dot(facts)
    assert "PostgreSQL" in out


def test_context_diagram_names_google_cloud_products_by_imported_name():
    facts = {
        "src/gcs.py": _f("src/gcs.py", imports=[("google.cloud", ["storage"])]),
        "src/q.py": _f("src/q.py", imports=[("google.cloud", ["tasks_v2"])]),
    }
    out, _ = srlib.diagrams.context_dot(facts)
    assert "Google Cloud Storage" in out
    assert "Google Cloud Tasks" in out


def test_context_diagram_lists_external_hosts_from_urls():
    facts = {"src/a.py": _f("src/a.py", io={"http": ["https://api.dataforseo.com/v3/serp"]}),
             "src/b.py": _f("src/b.py", deps=["src/a.py"])}
    out, _ = srlib.diagrams.context_dot(facts)
    assert "api.dataforseo.com" in out


def test_context_diagram_drops_local_and_boilerplate_hosts():
    facts = {
        "src/a.py": _f("src/a.py", io={"http": [
            "http://localhost:8001", "http://www.w3.org/2000/svg",
            "http://host.docker.internal:8001", "https://real.example.org/x",
        ]}),
        "src/b.py": _f("src/b.py", deps=["src/a.py"]),
    }
    out, _ = srlib.diagrams.context_dot(facts)
    assert "localhost" not in out
    assert "w3.org" not in out
    assert "host.docker.internal" not in out
    assert "real.example.org" in out


def test_context_diagram_ignores_evidence_from_test_files():
    facts = {
        "src/a.py": _f("src/a.py"),
        "src/b.py": _f("src/b.py", deps=["src/a.py"]),
        "tests/test_a.py": _f("tests/test_a.py", io={"http": ["https://only-in-tests.example"]}),
    }
    out, _ = srlib.diagrams.context_dot(facts)
    assert "only-in-tests" not in out


def test_context_diagram_carries_a_title_and_a_legend():
    facts = {"src/db.py": _f("src/db.py", imports=[("asyncpg", [])]),
             "src/app.py": _f("src/app.py", deps=["src/db.py"])}
    out, _ = srlib.diagrams.context_dot(facts)
    assert "rankdir=LR" in out
    assert "//" in out


def test_context_diagram_states_when_nothing_external_is_reached():
    facts = {"src/a.py": _f("src/a.py"), "src/b.py": _f("src/b.py", deps=["src/a.py"])}
    out, stats = srlib.diagrams.context_dot(facts)
    assert stats["context_systems"] == 0
    assert "no external systems" in out.lower()


def test_context_diagram_strips_backticks_from_a_host_pulled_from_a_docstring():
    # A URL inside a markdown code span in a docstring yields a host carrying a
    # backtick; it must never reach a mermaid node label.
    facts = {"src/a.py": _f("src/a.py", io={"http": ["https://sys.md``", "https://sys.md"]}),
             "src/b.py": _f("src/b.py", deps=["src/a.py"])}
    out, _ = srlib.diagrams.context_dot(facts)
    assert "`" not in out
    assert "sys.md" in out  # the sanitized host still appears, once


def test_context_diagram_drops_evidence_that_is_not_a_real_hostname():
    facts = {"src/a.py": _f("src/a.py", io={"http": ["https://not_a_host", "https://also bad"]}),
             "src/b.py": _f("src/b.py", deps=["src/a.py"])}
    out, stats = srlib.diagrams.context_dot(facts)
    assert stats["context_systems"] == 0
    assert "not_a_host" not in out


def test_render_serializes_under_a_lock(sample_repo):
    # The lock is a backstop against a second concurrent render; a normal render
    # acquires and releases it, leaving no stale lock behind.
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out)
    sr.cmd_render(root=sample_repo, out=out)
    assert not (out / ".soulreap" / "render.lock").exists()


def test_package_diagram_carries_a_title_and_a_legend():
    facts = {"src/api/a.py": _f("src/api/a.py", deps=["src/core/b.py"]),
             "src/core/b.py": _f("src/core/b.py")}
    out, _ = srlib.diagrams.package_dot(facts)
    assert "rankdir=TB" in out
    assert "//" in out


def test_package_diagram_labels_edges_with_import_counts():
    facts = {
        "src/api/a.py": _f("src/api/a.py", deps=["src/core/b.py"]),
        "src/api/c.py": _f("src/api/c.py", deps=["src/core/b.py"]),
        "src/core/b.py": _f("src/core/b.py"),
    }
    out, _ = srlib.diagrams.package_dot(facts)
    assert '[label="2 imports"]' in out


def test_package_diagram_marks_entry_and_foundation_roles():
    facts = {"src/api/a.py": _f("src/api/a.py", deps=["src/core/b.py"]),
             "src/core/b.py": _f("src/core/b.py")}
    out, _ = srlib.diagrams.package_dot(facts)
    assert "entry" in out
    assert "foundation" in out


def test_render_writes_packages_but_not_the_per_file_hairball(layered_repo):
    """Both generated diagrams are conditional now; the per-file graph is never
    drawn at all, at any size."""
    out = layered_repo / "soulreap"
    sr.cmd_plan(root=layered_repo, out=out)
    sr.cmd_render(root=layered_repo, out=out, diagrams="all")
    assert (out / "diagrams" / "packages.dot").exists()
    assert not (out / "diagrams" / "modules.dot").exists()


def test_render_draws_no_context_diagram_when_it_found_no_external_system(sample_repo):
    """A picture saying "nothing found" reads as an answer, and on fuse_core it was a
    wrong one: the repo clones over git and talks HTTP, none of it through a literal.
    The honest output is no diagram plus a line saying where we looked."""
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out)
    result = sr.cmd_render(root=sample_repo, out=out)

    assert result["context_systems"] == 0
    assert not (out / "diagrams" / "context.dot").exists()
    assert result.get("context_note"), "OVERVIEW needs a line in place of the picture"


def test_package_diagram_reports_role_counts():
    facts = {"src/api/a.py": _f("src/api/a.py", deps=["src/core/b.py"]),
             "src/core/b.py": _f("src/core/b.py")}
    _, stats = srlib.diagrams.package_dot(facts)
    assert stats["graph_roles"]["entry"] == 1
    assert stats["graph_roles"]["foundation"] == 1


def test_package_diagram_reports_no_layering_when_everything_is_mutual():
    facts = {"a/x.py": _f("a/x.py", deps=["b/y.py"]), "b/y.py": _f("b/y.py", deps=["a/x.py"])}
    _, stats = srlib.diagrams.package_dot(facts)
    assert stats["graph_roles"]["entry"] == 0
    assert stats["graph_roles"]["foundation"] == 0


def test_walk_warns_loudly_when_pathspec_is_unavailable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(srlib.walk, "_has_pathspec", lambda: False)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("build/\n")

    sr.walk(tmp_path)

    assert "pathspec" in capsys.readouterr().err.lower()


def test_gitignore_negation_is_honoured(tmp_path):
    (tmp_path / ".gitignore").write_text("workflows/\n!.github/workflows/\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
    (tmp_path / "other" / "workflows").mkdir(parents=True)
    (tmp_path / "other" / "workflows" / "x.yml").write_text("a: b\n")

    paths = {e.path for e in sr.walk(tmp_path)}

    assert ".github/workflows/ci.yml" in paths
    assert "other/workflows/x.yml" not in paths


# --------------------------------------------------------------------------
# Navigation: links from cards and the index back to the source
# --------------------------------------------------------------------------


def _carded(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out)
    (out / "files" / "src").mkdir(parents=True, exist_ok=True)
    (out / "files" / "src" / "main.py.md").write_text("# src/main.py\n\n**Role.** Entry point.\n")
    (out / "files" / "src" / "util.py.md").write_text("# src/util.py\n\n**Role.** Helper.\n")
    return out


# --------------------------------------------------------------------------
# Dependencies are a computed fact, not prose
# --------------------------------------------------------------------------


def test_extract_python_marks_standard_library_imports():
    facts = sr.extract_python("import json\nimport httpx\nfrom concurrent.futures import ThreadPoolExecutor\n", "a.py")
    scope = {i["module"]: i["stdlib"] for i in facts.imports}
    assert scope["json"] is True
    assert scope["concurrent.futures"] is True
    assert scope["httpx"] is False


def test_extract_treesitter_marks_node_builtins_as_standard_library():
    facts = sr.extract_source('import fs from "node:fs";\nimport React from "react";\n', "src/a.ts")
    scope = {i["module"]: i["stdlib"] for i in facts.imports}
    assert scope["node:fs"] is True
    assert scope["react"] is False


def test_build_graph_records_which_import_resolved_to_which_file():
    facts = {
        "app/main.py": sr.FileFacts(
            path="app/main.py", lang="python",
            imports=[{"module": "app.db", "names": ["get"]}, {"module": "os", "names": []}],
        ),
        "app/db.py": sr.FileFacts(path="app/db.py", lang="python", imports=[]),
    }
    graph = sr.build_graph(facts)
    assert graph.resolved["app/main.py"]["app.db"] == "app/db.py"
    assert graph.resolved["app/main.py"].get("os") is None


@pytest.fixture
def probe_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "util.py").write_text("def helper():\n    return 1\n")
    (tmp_path / "src" / "main.py").write_text(
        "import json\nimport httpx\nfrom src.util import helper\n\ndef run():\n    return helper()\n"
    )
    return tmp_path


def test_rendering_twice_leaves_the_dependencies_section_unchanged(probe_repo):
    out = probe_repo / "soulreap"
    sr.cmd_plan(root=probe_repo, out=out)
    card = out / "files" / "src" / "main.py.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text("# src/main.py\n\n**Role.** Entry point.\n\n## Entities\n\n(none)\n")
    sr.cmd_render(root=probe_repo, out=out)
    once = card.read_text()
    sr.cmd_render(root=probe_repo, out=out)
    assert card.read_text() == once


# --------------------------------------------------------------------------
# Task 9: excluding tests, and auto-carding trivial files
# --------------------------------------------------------------------------


@pytest.fixture
def repo_with_tests(tmp_path):
    """A repo with production code, a test suite, and empty package markers."""
    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("def run():\n    return 1\n")
    (pkg / "util.py").write_text('"""Helpers."""\n\n\ndef helper():\n    return 2\n')
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_core.py").write_text(
        "from src.app.core import run\n\n\ndef test_run():\n    assert run() == 1\n"
    )
    (tests / "test_util.py").write_text(
        "from src.app.util import helper\n\n\ndef test_helper():\n    assert helper() == 2\n"
    )
    return tmp_path


def _plan(repo, **kwargs):
    out = repo / "soulreap"
    sr.cmd_plan(root=repo, out=out, **kwargs)
    return json.loads((out / ".soulreap" / "plan.json").read_text(encoding="utf-8"))


def _status_of(plan, path):
    for item in plan["files"]:
        if item["path"] == path:
            return item["status"]
    return None


# -- is_test_path: the shared classifier -----------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_core.py",
        "src/app/test_core.py",
        "spec/models/user_spec.rb",
        "pkg/handler_test.go",
        "src/test/java/com/acme/FooTest.java",
        "web/__tests__/button.tsx",
        "web/button.test.ts",
        "web/button.spec.ts",
    ],
)
def test_is_test_path_matches_common_conventions(path):
    from srlib.paths import is_test_path

    assert is_test_path(path) is True


@pytest.mark.parametrize(
    "path",
    ["src/app/core.py", "src/latest.py", "lib/contest.py", "src/protest/main.py"],
)
def test_is_test_path_leaves_production_files_alone(path):
    from srlib.paths import is_test_path

    assert is_test_path(path) is False


# -- exclude globs ---------------------------------------------------------


def test_exclude_glob_matches_a_directory_subtree():
    from srlib.paths import matches_any

    assert matches_any("tests/unit/test_core.py", ["tests/**"]) is True


def test_exclude_glob_matches_a_filename_pattern_at_any_depth():
    from srlib.paths import matches_any

    assert matches_any("pkg/inner/handler_test.go", ["*_test.go"]) is True


def test_exclude_glob_leaves_unrelated_paths_alone():
    from srlib.paths import matches_any

    assert matches_any("src/app/core.py", ["tests/**", "*_test.go"]) is False


def test_exclude_glob_with_no_patterns_matches_nothing():
    from srlib.paths import matches_any

    assert matches_any("anything/at/all.py", []) is False


# -- plan --skip-tests -----------------------------------------------------


def test_plan_skip_tests_still_records_facts_for_excluded_files(repo_with_tests):
    """The trap: excluding from carding must not exclude from the fact substrate.

    Drop tests from facts.json and every production file loses the
    ``imported_by`` edge that says a test pins it.
    """
    out = repo_with_tests / "soulreap"
    _plan(repo_with_tests)
    facts = _read_facts(out)
    assert "tests/test_core.py" in facts
    assert "tests/test_core.py" in facts["src/app/core.py"]["imported_by"]


def test_plan_records_the_exclude_patterns_it_was_given(repo_with_tests):
    plan = _plan(repo_with_tests, exclude=["tests/**"])
    assert plan["excluded_by"]["patterns"] == ["tests/**"]


# -- excluded must not corrupt the existing state machine ------------------


def test_generated_trivial_card_reaches_the_index(repo_with_tests):
    out = repo_with_tests / "soulreap"
    _plan(repo_with_tests)
    sr.cmd_render(root=repo_with_tests, out=out)
    index = (out / "INDEX.md").read_text(encoding="utf-8")
    assert "src/app/__init__.py" in index


def test_render_never_overwrites_an_existing_trivial_card(repo_with_tests):
    out = repo_with_tests / "soulreap"
    _plan(repo_with_tests)
    card = out / "files" / "src" / "app" / "__init__.py.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text("# src/app/__init__.py\n\n**Role.** Hand written, keep me.\n", encoding="utf-8")
    sr.cmd_render(root=repo_with_tests, out=out)
    assert "Hand written, keep me." in card.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Task 10: the skeleton must not drop decorators
# --------------------------------------------------------------------------


@pytest.fixture
def decorated_repo(tmp_path):
    (tmp_path / "app.py").write_text(
        "import functools\n"
        "\n"
        "app = object()\n"
        "\n"
        "\n"
        "@app.get('/healthz')\n"
        "def healthz() -> dict:\n"
        "    return {'status': 'ok'}\n"
        "\n"
        "\n"
        "@app.post('/leave', operation_id='getLeaveBalance', summary='Get leave balance')\n"
        "async def get_leave(email: str) -> dict:\n"
        "    return {'days': 1}\n"
        "\n"
        "\n"
        "class Service:\n"
        "    @property\n"
        "    def ready(self) -> bool:\n"
        "        return True\n"
        "\n"
        "    @functools.cache\n"
        "    def lookup(self, key: str) -> str:\n"
        "        return key\n"
    )
    return tmp_path


def _skeleton(repo):
    out = repo / "soulreap"
    sr.cmd_plan(root=repo, out=out)
    return sr.cmd_skeleton(root=repo, out=out, target="app.py")


def test_skeleton_prints_a_route_decorator(decorated_repo):
    """The route path is the most important fact about a handler and the parser
    already has it; dropping it forces the card writer to guess or re-read."""
    assert "@app.get('/healthz')" in _skeleton(decorated_repo)


def test_skeleton_prints_decorator_arguments(decorated_repo):
    """operation_id/summary are the agent-facing tool contract, not decoration."""
    text = _skeleton(decorated_repo)
    assert "operation_id='getLeaveBalance'" in text
    assert "summary='Get leave balance'" in text


def test_skeleton_prints_method_decorators(decorated_repo):
    text = _skeleton(decorated_repo)
    assert "@property" in text
    assert "@functools.cache" in text


def test_skeleton_keeps_the_decorator_next_to_its_own_entity(decorated_repo):
    """A decorator listed away from its function tells the reader nothing."""
    lines = _skeleton(decorated_repo).splitlines()
    idx = next(i for i, line in enumerate(lines) if "healthz(" in line)
    assert any("@app.get('/healthz')" in line for line in lines[max(0, idx - 3) : idx])


def test_skeleton_still_prints_undecorated_functions(decorated_repo):
    (decorated_repo / "plain.py").write_text("def helper():\n    return 1\n")
    out = decorated_repo / "soulreap"
    sr.cmd_plan(root=decorated_repo, out=out)
    assert "helper()" in sr.cmd_skeleton(root=decorated_repo, out=out, target="plain.py")


# --------------------------------------------------------------------------
# Task 11: shape groups, and no callable vanishing from the pseudocode
# --------------------------------------------------------------------------


CLONES = '''
class Client:
    async def get_leave(self, email):
        row = await self.lookup(email)
        if not row:
            return []
        ident = self._ident(row)
        if ident is None:
            return []
        s = self._settings
        return await self._fetch(s.connector_leave, {"field": s.leave_id_field, "v": ident})

    async def get_claims(self, email):
        row = await self.lookup(email)
        if not row:
            return []
        ident = self._ident(row)
        if ident is None:
            return []
        s = self._settings
        return await self._fetch(s.connector_claims, {"field": s.employee_id_field, "v": ident})

    async def get_agreement(self, email):
        rows = await self.get_leave(email)
        if not rows:
            return []
        agreement = rows[0]["Klantovereenkomst"]
        return await self._fetch(self._settings.connector_agreement, {"k": agreement})
'''


@pytest.fixture
def clone_repo(tmp_path):
    (tmp_path / "client.py").write_text(CLONES)
    return tmp_path


def _facts_for(repo, rel):
    out = repo / "soulreap"
    sr.cmd_plan(root=repo, out=out)
    return _read_facts(out)[rel]


def _methods(blob):
    return {m["name"]: m for c in blob["classes"] for m in c["methods"]}


def test_shape_hash_matches_for_structurally_identical_methods(clone_repo):
    m = _methods(_facts_for(clone_repo, "client.py"))
    assert m["get_leave"]["shape"] == m["get_claims"]["shape"]


def test_shape_hash_differs_for_a_method_with_another_structure(clone_repo):
    m = _methods(_facts_for(clone_repo, "client.py"))
    assert m["get_agreement"]["shape"] != m["get_leave"]["shape"]


def test_shape_hash_ignores_statement_order(tmp_path):
    """The real afas_client case: one assignment moved a line up is still the
    same shape, and an exact-sequence hash wrongly called it different."""
    (tmp_path / "a.py").write_text(
        "def one(x):\n    a = f(x)\n    b = g(x)\n    return a + b\n"
        "\n"
        "def two(x):\n    b = g(x)\n    a = f(x)\n    return a + b\n"
    )
    blob = _facts_for(tmp_path, "a.py")
    shapes = {f["name"]: f["shape"] for f in blob["functions"]}
    assert shapes["one"] == shapes["two"]


def test_facts_group_same_shape_callables_together(clone_repo):
    groups = _facts_for(clone_repo, "client.py")["shape_groups"]
    members = [sorted(g["members"]) for g in groups]
    assert ["get_claims", "get_leave"] in members


def test_shape_group_lists_only_what_actually_varies(clone_repo):
    """The card's table is only trustworthy if the differing constants are
    parsed, not inferred by the model."""
    groups = _facts_for(clone_repo, "client.py")["shape_groups"]
    group = next(g for g in groups if set(g["members"]) == {"get_leave", "get_claims"})
    varying = " ".join(str(v) for v in group["varies"].values())
    assert "connector_leave" in varying and "connector_claims" in varying
    assert "lookup" not in varying  # shared by both, so it does not vary


def test_a_lone_function_forms_no_shape_group(tmp_path):
    (tmp_path / "solo.py").write_text("def only(x):\n    return x + 1\n")
    assert _facts_for(tmp_path, "solo.py")["shape_groups"] == []


def test_skeleton_prints_the_shape_group(clone_repo):
    out = clone_repo / "soulreap"
    sr.cmd_plan(root=clone_repo, out=out)
    text = sr.cmd_skeleton(root=clone_repo, out=out, target="client.py")
    assert "Shape groups" in text
    assert "get_leave" in text and "get_claims" in text


# --------------------------------------------------------------------------
# Task 12: absolute intra-package imports under a src/ layout
# --------------------------------------------------------------------------


@pytest.fixture
def src_layout_repo(tmp_path):
    """A uv-workspace shaped repo: member dir, src/ dir, absolute imports."""
    pkg = tmp_path / "afas-proxy" / "src" / "afas_proxy"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "config.py").write_text("class Settings:\n    pass\n")
    (pkg / "afas_client.py").write_text(
        "from afas_proxy.config import Settings\n\n\n"
        "class AfasClient:\n    def __init__(self, s: Settings):\n        self.s = s\n"
    )
    (pkg / "main.py").write_text(
        "from afas_proxy.afas_client import AfasClient\n"
        "from afas_proxy.config import Settings\n\n\ndef build():\n    return AfasClient(Settings())\n"
    )
    return tmp_path


def _layout_facts(repo):
    out = repo / "soulreap"
    sr.cmd_plan(root=repo, out=out)
    return _read_facts(out)


def test_absolute_intra_package_import_resolves_under_src_layout(src_layout_repo):
    f = _layout_facts(src_layout_repo)
    deps = f["afas-proxy/src/afas_proxy/main.py"]["deps"]
    assert "afas-proxy/src/afas_proxy/afas_client.py" in deps


def test_absolute_intra_package_import_fills_imported_by(src_layout_repo):
    """`imported_by` is the card section the source cannot state on its own;
    an unresolved import leaves it empty and the card says 'nobody'."""
    f = _layout_facts(src_layout_repo)
    assert f["afas-proxy/src/afas_proxy/afas_client.py"]["imported_by"] == [
        "afas-proxy/src/afas_proxy/main.py"
    ]


def test_relative_imports_keep_resolving(src_layout_repo):
    pkg = src_layout_repo / "afas-proxy" / "src" / "afas_proxy"
    (pkg / "rel.py").write_text("from .config import Settings\n\n\ndef f(s: Settings):\n    return s\n")
    f = _layout_facts(src_layout_repo)
    assert "afas-proxy/src/afas_proxy/config.py" in f["afas-proxy/src/afas_proxy/rel.py"]["deps"]


def test_nearest_package_wins_over_a_same_named_module_elsewhere(tmp_path):
    """Two workspace members can both ship a `config.py`; an import must bind to
    the one in the importer's own package, not to whichever sorts first."""
    for member in ("svc-a", "svc-b"):
        pkg = tmp_path / member / "src" / member.replace("-", "_")
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "config.py").write_text(f"NAME = '{member}'\n")
    (tmp_path / "svc-a" / "src" / "svc_a" / "main.py").write_text(
        "from svc_a.config import NAME\n\n\ndef run():\n    return NAME\n"
    )
    f = _layout_facts(tmp_path)
    deps = f["svc-a/src/svc_a/main.py"]["deps"]
    assert deps == ["svc-a/src/svc_a/config.py"]


def test_third_party_import_still_resolves_to_nothing(src_layout_repo):
    pkg = src_layout_repo / "afas-proxy" / "src" / "afas_proxy"
    (pkg / "http.py").write_text("import httpx\n\n\ndef get():\n    return httpx.get('https://x.test')\n")
    f = _layout_facts(src_layout_repo)
    assert f["afas-proxy/src/afas_proxy/http.py"]["deps"] == []


# -- the fold-continuation false positive ----------------------------------


# --------------------------------------------------------------------------
# Task 13: a fold is only legal when its members share the exemplar's shape
# --------------------------------------------------------------------------


def _card_with(out, pseudocode):
    card = out / "files" / "client.py.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(
        "# client.py\n\n**Role.** Client.\n\n## Dependencies\n\n## Entities\n\n"
        "## Pseudocode\n\n```\n" + pseudocode + "```\n",
        encoding="utf-8",
    )
    return card


# --------------------------------------------------------------------------
# Task 14: facts.json carries a schema version, and a stale one says so
# --------------------------------------------------------------------------


def _downgrade_facts_to_legacy(out):
    """Rewrite facts.json the way a soulreap from before shape groups wrote it."""
    path = out / ".soulreap" / "facts.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    files = blob.get("files", blob)
    for entry in files.values():
        entry.pop("shape_groups", None)
        for fn in entry.get("functions", []):
            fn.pop("shape", None)
            fn.pop("decorators", None)
    path.write_text(json.dumps(files, indent=2), encoding="utf-8")


def test_plan_stamps_the_facts_schema_version(clone_repo):
    out = clone_repo / "soulreap"
    sr.cmd_plan(root=clone_repo, out=out)
    blob = json.loads((out / ".soulreap" / "facts.json").read_text(encoding="utf-8"))
    assert blob["facts_version"] == sr.FACTS_VERSION


def test_skeleton_still_prints_entities_from_legacy_facts(clone_repo):
    """Stale is a warning, not a refusal: the old facts still describe the file."""
    out = clone_repo / "soulreap"
    sr.cmd_plan(root=clone_repo, out=out)
    _downgrade_facts_to_legacy(out)
    text = sr.cmd_skeleton(root=clone_repo, out=out, target="client.py")
    assert "get_leave" in text


def test_skeleton_says_nothing_about_staleness_on_fresh_facts(clone_repo):
    out = clone_repo / "soulreap"
    sr.cmd_plan(root=clone_repo, out=out)
    text = sr.cmd_skeleton(root=clone_repo, out=out, target="client.py")
    assert "STALE FACTS" not in text


# --------------------------------------------------------------------------
# Task 15: shape hashes for the tree-sitter languages, not just Python
# --------------------------------------------------------------------------


TS_CLONES = '''
export async function getLeave(email: string) {
  const row = await lookup(email);
  if (!row) return [];
  return await client.fetch(cfg.connectorLeave, { field: cfg.leaveField, v: row.id });
}

export async function getClaims(email: string) {
  const row = await lookup(email);
  if (!row) return [];
  return await client.fetch(cfg.connectorClaims, { field: cfg.employeeField, v: row.id });
}

export function summarise(rows: string[]) {
  let total = 0;
  for (const r of rows) {
    total += r.length;
  }
  return total;
}
'''


def _ts_shapes(src, path="src/client.ts"):
    facts = sr.extract_source(src, path).to_dict()
    return {f["name"]: f for f in facts["functions"]}, facts["shape_groups"]


def test_shape_hash_matches_for_identical_typescript_functions():
    """Clones are not a Python problem; the skill reads TS repos too."""
    functions, _ = _ts_shapes(TS_CLONES)
    assert functions["getLeave"]["shape"] == functions["getClaims"]["shape"]


def test_shape_hash_differs_for_a_typescript_function_with_another_structure():
    functions, _ = _ts_shapes(TS_CLONES)
    assert functions["summarise"]["shape"] != functions["getLeave"]["shape"]


def test_typescript_clones_form_a_shape_group():
    _, groups = _ts_shapes(TS_CLONES)
    assert [sorted(g["members"]) for g in groups] == [["getClaims", "getLeave"]]


def test_typescript_shape_group_lists_only_what_actually_varies():
    _, groups = _ts_shapes(TS_CLONES)
    varying = " ".join(str(v) for v in groups[0]["varies"].values())
    assert "connectorLeave" in varying and "connectorClaims" in varying
    assert "lookup" not in varying  # shared by both


def test_a_lone_typescript_function_forms_no_shape_group():
    _, groups = _ts_shapes("export function only(x: number) { return x + 1; }\n")
    assert groups == []


# --------------------------------------------------------------------------
# Task 16: the diagrams are drawn as SVG too, and the frames are checked
# --------------------------------------------------------------------------


NESTED_FLOW = 'digraph rt {\n  graph [rankdir=TD, label="Runtime topology"];\n  subgraph cluster_gcp {\n    label="Google Cloud";\n    subgraph cluster_services { label="Cloud Run services"; proxy; api; }\n    subgraph cluster_jobs { label="Cloud Run jobs"; sync; }\n    db [shape=cylinder, label="Postgres"];\n  }\n  subgraph cluster_azure { label="Azure"; bot; }\n  bot -> proxy [label="REST"];\n  proxy -> db;\n  sync -> db;\n  api -> proxy;\n}\n'

RECT_RE = re.compile(
    r'<rect x="([\d.-]+)" y="([\d.-]+)" width="([\d.-]+)" height="([\d.-]+)"([^/]*)/>'
)


def _rects(svg):
    """(frames, boxes) — dashed subgraph frames and solid node boxes."""
    frames, boxes = [], []
    for x, y, w, h, rest in RECT_RE.findall(svg):
        box = (float(x), float(y), float(w), float(h))
        (frames if "dasharray" in rest else boxes).append(box)
    return frames, boxes


def _contains(outer, inner):
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ox <= ix and oy <= iy and ox + ow >= ix + iw and oy + oh >= iy + ih


def _overlaps(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def test_flow_svg_draws_every_subgraph_frame():
    """Every declared subgraph becomes a graphviz cluster in the drawing."""
    from srlib.dviz import dot_svg
    svg, unread = dot_svg(NESTED_FLOW)
    assert unread == []
    assert svg.count('class="cluster"') == 4  # gcp, services, jobs, azure


def test_no_subgraph_cluster_wraps_nothing():
    """A cluster wraps at least one node — dot never draws an empty one."""
    import pygraphviz as pgv
    g = pgv.AGraph(string=NESTED_FLOW)
    try:
        subs = [g.subgraphs_subgraphs()[0]] if False else g.subgraphs()
        all_subs = subs
        def walk(subs):
            for sub in subs:
                yield sub
                yield from walk(sub.subgraphs())
        clusters = list(walk(all_subs))
        assert len(clusters) == 4
        for sub in clusters:
            assert sub.nodes(), f"cluster {sub.name} wraps no node at all"
    finally:
        g.close()


def test_subgraph_membership_is_a_tree():
    """A node lives in exactly one cluster at each depth: proxy/api in services,
    sync in jobs, db in gcp, bot in azure — never split."""
    import pygraphviz as pgv
    def by_label(g, wanted):
        for sub in g.subgraphs():
            if sub.graph_attr.get("label") == wanted:
                return sub
        raise AssertionError(f"no cluster labelled {wanted!r}")

    g = pgv.AGraph(string=NESTED_FLOW)
    try:
        gcp = by_label(g, "Google Cloud")
        svcs = by_label(gcp, "Cloud Run services")
        jobs = by_label(gcp, "Cloud Run jobs")
        azure = by_label(g, "Azure")
        svc_names = {n.name for n in svcs.nodes()}
        job_names = {n.name for n in jobs.nodes()}
        az_names = {n.name for n in azure.nodes()}
        assert {"proxy", "api"} <= svc_names
        assert "sync" in job_names
        assert "bot" in az_names
        assert not (svc_names & job_names), "a node cannot live in two clusters"
    finally:
        g.close()


def test_flow_svg_refuses_a_diagram_it_cannot_read():
    """Drawing a confidently wrong picture is worse than drawing none."""
    import pytest
    from srlib.dviz import dot_svg
    with pytest.raises(Exception):
        dot_svg("graph TD\n  quantum{{weird}} ==> other\n")


def test_render_writes_an_svg_beside_each_generated_mermaid(layered_repo):
    """Beside each mermaid file that was written: both generated pairs are drawn only
    when there is something to draw."""
    out = layered_repo / "soulreap"
    sr.cmd_plan(root=layered_repo, out=out)
    sr.cmd_render(root=layered_repo, out=out, diagrams="all")
    assert (out / "diagrams" / "packages.svg").exists()
    for name in ("context", "packages"):
        mermaid = out / "diagrams" / f"{name}.dot"
        svg = out / "diagrams" / f"{name}.svg"
        assert svg.exists() == mermaid.exists() or not mermaid.exists()


def test_render_draws_the_hand_written_diagrams_too(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out)
    (out / "diagrams").mkdir(parents=True, exist_ok=True)
    (out / "diagrams" / "architecture.dot").write_text(NESTED_FLOW, encoding="utf-8")
    result = sr.cmd_render(root=sample_repo, out=out, diagrams="all")
    assert (out / "diagrams" / "architecture.svg").exists()
    assert not [w for w in result["svg_warnings"] if "not drawn" in w]


def test_render_reports_a_diagram_it_could_not_draw(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out)
    (out / "diagrams").mkdir(parents=True, exist_ok=True)
    (out / "diagrams" / "architecture.dot").write_text(
        "graph TD\n  a@{shape: hex, label: nope}\n", encoding="utf-8"
    )
    result = sr.cmd_render(root=sample_repo, out=out, diagrams="all")
    assert result["svg_warnings"]
    assert not (out / "diagrams" / "architecture.svg").exists()


# --------------------------------------------------------------------------
# Task 17: every card says when it was written
# --------------------------------------------------------------------------


STAMP_RE = re.compile(r"carded (\d{4}-\d{2}-\d{2} \d{2}:\d{2})")


def _stamp_of(card):
    match = STAMP_RE.search(card.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def _clock(monkeypatch, *values):
    """Hand render a clock, so a stamp can be checked without waiting a minute."""
    ticks = iter(values)
    last = [values[-1]]

    def fake_now():
        with contextlib.suppress(StopIteration):
            last[0] = next(ticks)
        return last[0]

    monkeypatch.setattr(srlib.commands, "_now", fake_now)


# --------------------------------------------------------------------------
# Task 18: the intra-file call graph — who is an entry, who is a helper
# --------------------------------------------------------------------------


CALLERS = '''
def _coerce(cell):
    return int(cell or 0)


def _yearly(row):
    return _coerce(row["a"]) * 12


def _formula(row):
    return f"={_coerce(row['a'])}*12"


def _project(rows):
    return rows[:3]


def _sheet(wb, rows):
    _project(rows)
    _yearly(rows[0])
    _formula(rows[0])


def validate(wb):
    _sheet(wb, [])
    return _project([])
'''


def test_facts_record_which_callables_a_callable_calls(tmp_path):
    (tmp_path / "io.py").write_text(CALLERS)
    calls = _facts_for(tmp_path, "io.py")["calls"]
    assert calls["_sheet"] == ["_formula", "_project", "_yearly"]


def test_facts_leave_out_calls_to_things_defined_elsewhere(tmp_path):
    """Only this file's own callables — an import is already on the card."""
    (tmp_path / "io.py").write_text("import json\n\ndef load(t):\n    return json.loads(t)\n")
    assert _facts_for(tmp_path, "io.py")["calls"]["load"] == []


def test_skeleton_marks_the_entry_point(tmp_path):
    (tmp_path / "io.py").write_text(CALLERS)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    text = sr.cmd_skeleton(root=tmp_path, out=out, target="io.py")
    assert "## Call graph" in text
    assert re.search(r"^ENTRY\s+validate\b", text, re.M)


def test_skeleton_nests_a_helper_under_the_callable_that_calls_it(tmp_path):
    (tmp_path / "io.py").write_text(CALLERS)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    lines = sr.cmd_skeleton(root=tmp_path, out=out, target="io.py").splitlines()
    # Nesting is where the name starts; counting leading blanks stops at the first │.
    depth = {ln.split("── ", 1)[1].split()[0]: ln.index("── ") for ln in lines if "── " in ln}
    assert depth["_yearly"] > depth["_sheet"]
    assert depth["_coerce"] > depth["_yearly"]


def test_skeleton_counts_a_shared_helper_and_expands_it_once(tmp_path):
    """_coerce is called by two callables; _project by two. Expanding a shared
    helper under every caller is how a 12-node file becomes 40 lines."""
    (tmp_path / "io.py").write_text(CALLERS)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    text = sr.cmd_skeleton(root=tmp_path, out=out, target="io.py")
    assert "_coerce ×2" in text
    assert text.count("↑") >= 1


def test_skeleton_lists_callables_that_neither_call_nor_are_called(tmp_path):
    """The pseudocode lint demands every callable appear somewhere; a call
    graph that quietly drops the lonely ones would fight it."""
    src = CALLERS + "\n\ndef lonely(x):\n    return x\n"
    (tmp_path / "io.py").write_text(src)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    text = sr.cmd_skeleton(root=tmp_path, out=out, target="io.py")
    # The LINE, not the word: the legend above the graph names `NO CALLS` too, and
    # splitting on the token landed between the two mentions.
    listed = [line for line in text.splitlines() if line.startswith("NO CALLS")]
    assert listed and "lonely" in listed[0]


def test_skeleton_omits_the_call_graph_when_there_is_nothing_to_draw(tmp_path):
    """40 test functions that call nothing would be 40 unconnected lines —
    the gate is edges, not callables."""
    src = "".join(f"def test_{i}():\n    assert True\n\n\n" for i in range(12))
    (tmp_path / "test_flat.py").write_text(src)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    text = sr.cmd_skeleton(root=tmp_path, out=out, target="test_flat.py")
    assert "## Call graph" not in text


def test_a_recursive_call_graph_does_not_loop_forever(tmp_path):
    # Six callables, so the graph clears its own gate and is actually drawn.
    (tmp_path / "r.py").write_text(
        "def a(n):\n    return b(n)\n\n\ndef b(n):\n    return a(n)\n\n\n"
        "def c(n):\n    return a(n)\n\n\ndef d(n):\n    return c(n) + b(n)\n\n\n"
        "def e(n):\n    return d(n)\n\n\ndef f(n):\n    return e(n) + c(n)\n"
    )
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    text = sr.cmd_skeleton(root=tmp_path, out=out, target="r.py")
    assert "↻" in text


SHAPED_ENTRIES = '''
def _fetch(url):
    return url


def _connector(name):
    return _fetch(name)


def _ident(row):
    return row["id"]


def get_leave(row):
    ident = _ident(row)
    return _connector("leave") + str(ident)


def get_claims(row):
    ident = _ident(row)
    return _connector("claims") + str(ident)


def get_payslips(row):
    ident = _ident(row)
    return _connector("payslips") + str(ident)
'''


def test_same_shape_entry_points_share_one_block(tmp_path):
    """Five clones drew five identical blocks of `↑` on the real afas_client —
    the fold already exists for the pseudocode, and the tree needs it too."""
    (tmp_path / "c.py").write_text(SHAPED_ENTRIES)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    text = sr.cmd_skeleton(root=tmp_path, out=out, target="c.py")
    graph = text.split("## Call graph")[1]
    # count block headers, not the word — the legend explains what ENTRY means
    assert sum(1 for ln in graph.splitlines() if ln.startswith("ENTRY")) == 1
    for name in ("get_leave", "get_claims", "get_payslips"):
        assert name in graph.split("├──")[0]


def test_a_folded_entry_block_says_the_bodies_are_one_shape(tmp_path):
    (tmp_path / "c.py").write_text(SHAPED_ENTRIES)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    graph = sr.cmd_skeleton(root=tmp_path, out=out, target="c.py").split("## Call graph")[1]
    assert "one shape" in graph


def test_entry_points_of_different_shapes_keep_their_own_blocks(tmp_path):
    src = SHAPED_ENTRIES + '''

def summarise(rows):
    total = 0
    for row in rows:
        total += _ident(row)
    return total
'''
    (tmp_path / "c.py").write_text(src)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    graph = sr.cmd_skeleton(root=tmp_path, out=out, target="c.py").split("## Call graph")[1]
    assert sum(1 for ln in graph.splitlines() if ln.startswith("ENTRY")) == 2


# --------------------------------------------------------------------------
# Pseudocode sections (TEMPLATE_VERSION 3)
# --------------------------------------------------------------------------

_SECTION_OK = (
    "### Module init\n\n```\nSET app TO FastAPI()\n```\n\n"
    "### Entry points\n\n```\nROUTE GET /x → x()\n```\n\n"
    "### Core logic\n\n```\nFUNCTION build()\n```\n"
)


def _facts_with_protocol() -> dict:
    """One file: a Protocol whose method is a stub, and a real class with a method."""
    return {
        "m.py": {
            "functions": [],
            "classes": [
                {
                    "name": "ChatBackend",
                    "bases": ["Protocol"],
                    "methods": [{"name": "chat_once", "start_line": 3, "end_line": 5}],
                },
                {
                    "name": "Processor",
                    "bases": [],
                    "methods": [{"name": "run_cycle", "start_line": 10, "end_line": 40}],
                },
            ],
        }
    }


# --------------------------------------------------------------------------
# Diagram legibility, measured on the SVG that was actually drawn
# --------------------------------------------------------------------------

_BOX = '<rect x="100" y="100" width="200" height="50"/>'


def test_edge_drawn_through_a_node_box_is_counted() -> None:
    """The renderer draws straight lines with no obstacle avoidance, so a long edge
    is drawn straight through whatever boxes lie between its ends. That is what turns
    a topology diagram into spaghetti, and it is invisible in the .dot source."""
    svg = f'<svg width="500" height="300">{_BOX}<path d="M 0 125 L 500 125"/></svg>'
    through, _, _ = srlib.svggeom._svg_geometry(svg)
    assert through == 1


def test_edge_beside_a_node_box_is_not_counted() -> None:
    svg = f'<svg width="500" height="300">{_BOX}<path d="M 0 280 L 500 280"/></svg>'
    through, _, _ = srlib.svggeom._svg_geometry(svg)
    assert through == 0


def test_crossing_edges_are_counted() -> None:
    svg = (
        '<svg width="500" height="300">'
        '<path d="M 0 0 L 400 300"/><path d="M 0 300 L 400 0"/></svg>'
    )
    _, crossings, _ = srlib.svggeom._svg_geometry(svg)
    assert crossings == 1


def test_parallel_edges_do_not_count_as_crossing() -> None:
    svg = (
        '<svg width="500" height="300">'
        '<path d="M 0 10 L 400 10"/><path d="M 0 50 L 400 50"/></svg>'
    )
    _, crossings, _ = srlib.svggeom._svg_geometry(svg)
    assert crossings == 0


def test_a_legible_diagram_produces_no_warning(tmp_path: Path) -> None:
    d = tmp_path / "diagrams"
    d.mkdir()
    (d / "architecture.svg").write_text(
        f'<svg width="500" height="300">{_BOX}<path d="M 0 280 L 500 280"/></svg>'
    )
    assert srlib.svglint._lint_svg_geometry(d) == []


def test_an_illegible_diagram_is_reported_with_its_numbers(tmp_path: Path) -> None:
    """Edges drawn straight through the boxes between their ends — what a wide canvas
    full of long cross-diagram edges actually produces."""
    d = tmp_path / "diagrams"
    d.mkdir()
    boxes = "".join(
        f'<rect x="{100 + 60 * i}" y="100" width="50" height="50"/>' for i in range(8)
    )
    lines = "".join(f'<path d="M 10 {110 + 5 * i} L 790 {110 + 5 * i}"/>' for i in range(6))
    (d / "architecture.svg").write_text(f'<svg width="800" height="300">{boxes}{lines}</svg>')
    warnings = srlib.svglint._lint_svg_geometry(d)
    assert len(warnings) == 1
    assert "architecture.svg" in warnings[0]
    assert "through a node box" in warnings[0]


def _through_box_svg() -> str:
    boxes = "".join(
        f'<rect x="{100 + 60 * i}" y="100" width="50" height="50"/>' for i in range(8)
    )
    lines = "".join(f'<path d="M 10 {110 + 5 * i} L 790 {110 + 5 * i}"/>' for i in range(6))
    return f'<svg width="800" height="300">{boxes}{lines}</svg>'


def test_the_sequence_diagram_is_never_linted(tmp_path: Path) -> None:
    """A sequence diagram's lifelines pass through note boxes and activation bars by
    design: flow.svg scores 32 on this metric while being the most readable of the four
    pictures. Linting it would be a permanent false positive."""
    d = tmp_path / "diagrams"
    d.mkdir()
    (d / "flow.svg").write_text(_through_box_svg())
    assert srlib.svglint._lint_svg_geometry(d) == []


def test_the_context_diagram_is_never_linted(tmp_path: Path) -> None:
    """Its drawer routes every edge as a Bezier inside the channel between the two
    columns, so it cannot cross a node box — it scores 0 on real output. It is also
    generated, so a warning would not be actionable by editing a file."""
    d = tmp_path / "diagrams"
    d.mkdir()
    (d / "context.svg").write_text(_through_box_svg())
    assert srlib.svglint._lint_svg_geometry(d) == []


def test_the_generated_layering_diagram_is_linted(tmp_path: Path) -> None:
    """packages.svg is generated, but on real output it scores 16 — the drawer needs
    work, and this warning is the only way that surfaces at all."""
    d = tmp_path / "diagrams"
    d.mkdir()
    (d / "packages.svg").write_text(_through_box_svg())
    warnings = srlib.svglint._lint_svg_geometry(d)
    assert len(warnings) == 1
    assert "packages.svg" in warnings[0]


def test_subgraph_frames_are_not_counted_as_nodes() -> None:
    """An edge into a nested subgraph MUST cross that subgraph's frame — that is what
    nesting means. Counting frames as nodes made a legitimate 6-node diagram look like
    spaghetti, which is a false positive, which is how a lint gets ignored.

    A frame is recognised geometrically: it contains another rect.
    """
    svg = (
        '<svg width="600" height="400">'
        '<rect x="50" y="50" width="300" height="200"/>'      # frame
        '<rect x="80" y="90" width="100" height="40"/>'       # node inside it
        # Crosses the frame's interior at y=200, clear of the node band (y 90-130).
        '<path d="M 500 200 L 60 200"/>'
        "</svg>"
    )
    through, _, _ = srlib.svggeom._svg_geometry(svg)
    assert through == 0, "an edge crossing a frame to reach its member is not spaghetti"


def test_an_edge_through_a_real_node_still_counts_when_a_frame_is_present() -> None:
    """Over-correction guard: excluding frames must not excuse crossing actual nodes."""
    svg = (
        '<svg width="600" height="400">'
        '<rect x="50" y="50" width="300" height="200"/>'      # frame
        '<rect x="80" y="90" width="100" height="40"/>'       # node inside it
        '<path d="M 500 110 L 20 110"/>'                      # straight through the node
        "</svg>"
    )
    through, _, _ = srlib.svggeom._svg_geometry(svg)
    assert through == 1


def test_ink_ratio_rises_with_long_edges() -> None:
    """The gate metric: short edges on a big canvas score low, long ones score high."""
    short = '<svg width="800" height="600"><path d="M 10 10 L 60 10"/></svg>'
    long_ = (
        '<svg width="800" height="600">'
        + "".join(f'<path d="M 10 {20 * i} L 790 {20 * i}"/>' for i in range(1, 8))
        + "</svg>"
    )
    _, _, ink_short = srlib.svggeom._svg_geometry(short)
    _, _, ink_long = srlib.svggeom._svg_geometry(long_)
    assert ink_short < 0.2
    assert ink_long > 4.5


# --------------------------------------------------------------------------
# Context diagram: a documentation link is not a dependency
# --------------------------------------------------------------------------


def _io_http(*urls: str) -> dict:
    return {"http": ["import httpx", *urls]}


def test_documentation_hosts_are_not_drawn_as_external_systems() -> None:
    """These appear because somebody linked to a doc page, a console or a CLI
    reference in a comment. Drawing them says the code talks to them, which is
    false, and on a real repo they were 6 of the 15 systems on the diagram."""
    facts = {
        "src/a.py": _f(
            "src/a.py",
            io=_io_http(
                "https://learn.microsoft.com/en-us/graph/api/overview",
                "https://cloud.google.com/run/docs",
                "https://console.cloud.google.com/run",
                "https://github.com/Whats-Next-CC/jam-horeca",
                "https://cli.github.com/manual/gh_auth_refresh",
                "https://fonts.googleapis.com/css2?family=Inter",
                "https://pypi.org/project/httpx/",
                "https://stackoverflow.com/questions/1",
                "https://docs.python.org/3/library/re.html",
            ),
        ),
    }
    out, stats = srlib.diagrams.context_dot(facts)
    for host in (
        "learn.microsoft.com",
        "cloud.google.com",
        "console.cloud.google.com",
        "github.com",
        "cli.github.com",
        "fonts.googleapis.com",
        "pypi.org",
        "stackoverflow.com",
        "docs.python.org",
    ):
        assert host not in out, f"{host} is a documentation link, not a dependency"
    assert stats["context_systems"] == 0


def test_real_runtime_hosts_survive_the_documentation_filter() -> None:
    """Over-correction guard, and the one that matters most: the filter must be exact
    hosts, never a wildcard on the parent domain. `learn.microsoft.com` goes,
    `graph.microsoft.com` stays."""
    facts = {
        "src/a.py": _f(
            "src/a.py",
            io=_io_http(
                "https://graph.microsoft.com/v1.0",
                "https://login.microsoftonline.com/",
                "https://openai-we-jam.openai.azure.com",
                "https://api.vapi.ai",
                "https://europe.directline.botframework.com",
                "https://org00f16cd5.crm4.dynamics.com",
                "https://afas-proxy-dev-2ykq3ehrcq-ez.a.run.app",
            ),
        ),
    }
    out, stats = srlib.diagrams.context_dot(facts)
    for host in (
        "graph.microsoft.com",
        "login.microsoftonline.com",
        "openai-we-jam.openai.azure.com",
        "api.vapi.ai",
        "europe.directline.botframework.com",
        "org00f16cd5.crm4.dynamics.com",
        "afas-proxy-dev-2ykq3ehrcq-ez.a.run.app",
    ):
        assert host in out, f"{host} is a real runtime dependency and must be drawn"
    assert stats["context_systems"] == 7


def test_the_github_actions_oidc_issuer_is_not_a_dependency() -> None:
    """It appears in every Workload Identity Federation setup as an issuer identifier
    written into cloud config — nothing in the repo calls it."""
    facts = {
        "deploy/setup.py": _f(
            "deploy/setup.py",
            io=_io_http("https://token.actions.githubusercontent.com"),
        ),
    }
    out, _ = srlib.diagrams.context_dot(facts)
    assert "token.actions.githubusercontent.com" not in out


def test_context_groups_the_left_column_by_deployable_not_by_submodule() -> None:
    """The context question is "what does this repo talk to", answered per deployable.
    Grouping as deeply as the layering diagram splits one service into four rows that
    all reach the same system, which multiplies the edges and says nothing extra."""
    facts = {
        "worker/src/pkg/main.py": _f("worker/src/pkg/main.py", io=_io_http("https://api.example-real.com")),
        "worker/src/pkg/agent/client.py": _f(
            "worker/src/pkg/agent/client.py", io=_io_http("https://api.example-real.com")
        ),
        "worker/src/pkg/knowledge/rag.py": _f(
            "worker/src/pkg/knowledge/rag.py", io=_io_http("https://api.example-real.com")
        ),
        "proxy/src/proxy/main.py": _f(
            "proxy/src/proxy/main.py", io=_io_http("https://afas.example-real.com")
        ),
    }
    out, stats = srlib.diagrams.context_dot(facts)
    assert '"worker"' in out, "the deployable, not worker/src/pkg/agent"
    assert '"proxy"' in out
    assert "agent" not in out and "knowledge" not in out
    # three files of one deployable reaching one host is ONE edge, not three
    assert stats["context_edges"] == 2


def test_context_falls_back_to_finer_grouping_for_a_single_package_repo() -> None:
    """Depth 1 on a normal repo collapses to one node and the diagram stops saying
    which part reaches what, so fall back when it would."""
    facts = {
        "src/db.py": _f("src/db.py", io=_io_http("https://db.example-real.com")),
        "src/llm.py": _f("src/llm.py", io=_io_http("https://llm.example-real.com")),
    }
    out, _ = srlib.diagrams.context_dot(facts)
    assert "src/db.py" in out or "src" in out
    assert "db.example-real.com" in out and "llm.example-real.com" in out


def test_the_context_mmd_and_the_context_svg_group_identically() -> None:
    """They are drawn by two separate functions from the same facts, and SKILL.md
    claims the pair "can never disagree". It can: a reduction applied in one and not
    the other diverges silently, which is exactly what happened when the left-column
    grouping was changed in `context_dot` alone — the .dot dropped to 17 edges
    while the .svg kept drawing 23."""
    facts = {
        "worker/src/pkg/main.py": _f("worker/src/pkg/main.py", io=_io_http("https://a.example-real.com")),
        "worker/src/pkg/agent/c.py": _f("worker/src/pkg/agent/c.py", io=_io_http("https://a.example-real.com")),
        "worker/src/pkg/knowledge/r.py": _f("worker/src/pkg/knowledge/r.py", io=_io_http("https://b.example-real.com")),
        "proxy/src/proxy/main.py": _f("proxy/src/proxy/main.py", io=_io_http("https://c.example-real.com")),
    }
    mmd, stats = srlib.diagrams.context_dot(facts)
    svg = srlib.svg.context_svg(facts)
    # Same left-hand labels in both pictures.
    for label in ("worker", "proxy"):
        assert f'"{label}"' in mmd, f"{label} missing from the .dot"
        assert f">{label}<" in svg or label in svg, f"{label} missing from the .svg"
    for split in ("agent", "knowledge"):
        assert split not in mmd
        assert split not in svg, f"the .svg still groups by sub-package ({split})"
    # Same edge count: one per (deployable, system) pair.
    assert stats["context_edges"] == 3
    assert svg.count("<path") <= 6, "svg drew more edges than the mmd declares"


def test_decorative_swatches_are_not_counted_as_nodes() -> None:
    """The context drawer puts 5x12 colour chips in the channel the edges run through,
    on purpose. Counting them as nodes reported every edge as passing through one."""
    svg = (
        '<svg width="500" height="300">'
        '<rect x="200" y="100" width="5" height="12"/>'     # swatch
        '<path d="M 0 105 L 500 105"/>'
        "</svg>"
    )
    through, _, _ = srlib.svggeom._svg_geometry(svg)
    assert through == 0


def test_a_bezier_is_flattened_to_its_real_path_not_its_control_polygon() -> None:
    """A cubic's control polygon can be far longer than the curve it draws. Measuring
    the polygon was wrong in both directions: it invented length that is not on the
    page, and it put the line where the curve never goes."""
    # Chord length 400. The control polygon of this curve is 400 + 200 + 200 = 800.
    svg = '<svg width="500" height="300"><path d="M50,150 C250,50 250,250 450,150"/></svg>'
    _, _, ink = srlib.svggeom._svg_geometry(svg)
    diagonal = (500**2 + 300**2) ** 0.5
    length = ink * diagonal
    assert 400 <= length <= 560, f"flattened length {length:.0f} is not curve-like"


def test_a_curve_bowing_around_a_box_does_not_count_as_through_it() -> None:
    svg = (
        '<svg width="600" height="400">'
        '<rect x="250" y="180" width="100" height="40"/>'
        '<path d="M50,200 C200,40 400,40 550,200"/>'   # arcs well above the box
        "</svg>"
    )
    through, _, _ = srlib.svggeom._svg_geometry(svg)
    assert through == 0


def test_a_curve_drawn_over_a_box_does_count() -> None:
    svg = (
        '<svg width="600" height="400">'
        '<rect x="250" y="180" width="100" height="40"/>'
        '<path d="M50,200 C200,200 400,200 550,200"/>'  # straight through it
        "</svg>"
    )
    through, _, _ = srlib.svggeom._svg_geometry(svg)
    assert through >= 1


def test_the_layering_diagram_orders_each_layer_to_cut_crossings() -> None:
    """Nodes inside a layer were placed alphabetically, so an edge from the far left of
    one layer to the far right of the next was drawn over every box between them. The
    context drawer in the same module already does barycentre ordering; this one did
    not, and scored 16 edge-through-box incidents on a real repo.

    Here alphabetical placement forces a crossing that barycentre ordering removes:
    `alpha` depends on `zulu`, `bravo` on `yankee`.
    """
    facts = {
        "alpha/a.py": _f("alpha/a.py", deps=["zulu/z.py"]),
        "bravo/b.py": _f("bravo/b.py", deps=["yankee/y.py"]),
        "zulu/z.py": _f("zulu/z.py"),
        "yankee/y.py": _f("yankee/y.py"),
    }
    svg = srlib.svg.packages_svg(facts)
    through, crossings, _ = srlib.svggeom._svg_geometry(svg)
    assert crossings == 0, f"{crossings} crossings — the layer was not reordered"
    assert through == 0


def _dot_drawing() -> str:
    """A real graphviz drawing: two boxes, one arrow, one cluster frame."""
    return srlib.erd_dot.packages_dot_svg({
        "labels": ["api", "core"],
        "weights": {("api", "core"): 3},
        "artifacts": set(),
        "roles": {"api": "entry", "core": "foundation"},
        "depth": 1,
        "excluded": 0,
    })


def test_the_geometry_reader_sees_a_graphviz_drawing_at_all() -> None:
    """The reason every generated picture scored a perfect zero.

    `<path d="..."` only matches a drawer that puts `d` first; graphviz writes
    `<path fill="none" stroke="#2e22db" d="..."/>`, so not one edge was found, ink
    came out 0.00 and the gate passed everything by measuring nothing. The ERDs and
    structures diagrams have been linted this way since they moved to dot.
    """
    svg = _dot_drawing()
    edges = srlib.svggeom._edges_of(svg)
    assert len(edges) == 1, f"{len(edges)} edges read out of a one-arrow drawing"
    _through, _crossings, ink = srlib.svggeom._svg_geometry(svg)
    assert ink > 0


def test_the_canvas_is_read_from_the_svg_tag_not_from_a_stroke_width() -> None:
    """The old search for `width="..."` matched `stroke-width="2"` 950 bytes in, so
    the diagonal was computed from a canvas 2 units wide."""
    svg = _dot_drawing()
    width, height = srlib.svggeom._svg_canvas(svg)
    assert width > 100 and height > 50


def test_a_graphviz_cluster_frame_is_not_counted_as_an_edge() -> None:
    """A section frame is a rounded `<path>`, exactly like an edge is, and the ERD
    draws one per declaring file. Counted as edges they halve the crossings ratio."""
    database = {
        "name": "shop",
        "tables": [
            {"name": "orders", "source": "order.py", "columns": [
                {"name": "id", "pk": True, "type": "int"},
                {"name": "buyer_id", "fk": "people.id", "type": "int"},
            ]},
            {"name": "people", "source": "person.py", "columns": [
                {"name": "id", "pk": True, "type": "int"},
            ]},
        ],
    }
    svg = srlib.erd_dot.erd_dot_svg(database)
    assert svg.count('class="cluster"') == 2  # two files, two frames
    assert len(srlib.svggeom._edges_of(svg)) == 1


def test_a_graphviz_node_box_is_a_box(tmp_path) -> None:
    """Node shapes are `<polygon>`, never `<rect>`, so the through-box metric had no
    boxes to test against and could not have fired."""
    boxes = srlib.svggeom._node_boxes(_dot_drawing())
    assert len(boxes) == 2
    assert all(width > 20 and height > 10 for _x, _y, width, height in boxes)


def test_the_packages_picture_is_drawn_by_dot_and_carries_role_and_weight() -> None:
    """The last hand-laid diagram moves to the engine that lays out the ERDs.

    Both things the title promises have to survive the move: the layer a package sits
    in, and how heavy each arrow is.
    """
    facts = {
        "api/a.py": _f("api/a.py", deps=["core/b.py", "core/c.py"]),
        "api/z.py": _f("api/z.py", deps=["core/b.py"]),
        "core/b.py": _f("core/b.py"),
        "core/c.py": _f("core/c.py"),
    }
    svg = srlib.erd_dot.packages_dot_svg(srlib.diagrams.package_graph(facts))
    assert svg is not None and svg.lstrip().startswith("<?xml")
    assert "entry" in svg and "foundation" in svg
    assert "3 imports" in svg


def test_the_packages_picture_is_not_drawn_when_there_is_no_arrow() -> None:
    facts = {"app.py": _f("app.py"), "util.py": _f("util.py")}
    assert srlib.erd_dot.packages_dot_svg(srlib.diagrams.package_graph(facts)) is None


def test_the_packages_svg_and_mmd_name_the_same_packages_at_render(layered_repo):
    """The pair is built from one graph now, and this is the assertion that keeps it
    that way — the context pair diverged exactly because it was computed twice."""
    out = layered_repo / "soulreap"
    sr.cmd_plan(root=layered_repo, out=out)
    sr.cmd_render(root=layered_repo, out=out, diagrams="all")
    mermaid = (out / "diagrams" / "packages.dot").read_text()
    svg = (out / "diagrams" / "packages.svg").read_text()
    named = {re.search(r'label="([^"]+)', ln).group(1).split(" \\n")[0]
             for ln in mermaid.splitlines() if 'label="' in ln and 'digraph' not in ln}
    assert named
    for name in named:
        assert name in svg, f"{name} is on the .dot and not on the .svg"


def test_the_packages_picture_falls_back_to_the_hand_drawer(layered_repo, monkeypatch):
    """No graphviz wheel on this platform is not a reason to ship no picture."""
    monkeypatch.setattr(srlib.drawing, "_packages_dot_svg", lambda *a, **k: None)
    out = layered_repo / "soulreap"
    sr.cmd_plan(root=layered_repo, out=out)
    sr.cmd_render(root=layered_repo, out=out, diagrams="all")
    assert (out / "diagrams" / "packages.svg").read_text().startswith("<svg")


def test_the_dot_packages_picture_crosses_less_than_the_hand_drawn_one() -> None:
    """The reason for the move, as a number rather than an opinion.

    A fan of five entry packages into five foundations is the shape that tangled on
    the real repository.
    """
    facts = {}
    for i in range(5):
        deps = [f"core{j}/m.py" for j in range(5) if (i + j) % 2 == 0]
        facts[f"api{i}/m.py"] = _f(f"api{i}/m.py", deps=deps)
    for j in range(5):
        facts[f"core{j}/m.py"] = _f(f"core{j}/m.py")
    by_hand = srlib.svggeom._svg_geometry(srlib.svg.packages_svg(facts))
    by_dot = srlib.svggeom._svg_geometry(
        srlib.erd_dot.packages_dot_svg(srlib.diagrams.package_graph(facts))
    )
    assert by_dot[1] <= by_hand[1], f"dot {by_dot[1]} crossings vs hand {by_hand[1]}"
    assert by_dot[0] <= by_hand[0], f"dot {by_dot[0]} through-box vs hand {by_hand[0]}"


def test_package_labels_drop_the_src_and_mirrored_package_segments() -> None:
    """`copilot-mailbox-worker/src/copilot_mailbox_worker/agent` is 47 characters of
    which 24 are boilerplate: `src/` plus the package directory that just mirrors the
    distribution name. The box is sized from the label, so on a real repo the boxes ran
    254-480px wide, the row ran 1762px, and inter-layer edges were drawn through the
    boxes between their ends — 16 incidents. Shortening the label is the fix, and it
    reads better too."""
    assert srlib.diagrams.display_label(
        "copilot-mailbox-worker/src/copilot_mailbox_worker/agent"
    ) == "copilot-mailbox-worker/agent"
    assert srlib.diagrams.display_label(
        "inbox-analysis/src/inbox_analysis/downstream"
    ) == "inbox-analysis/downstream"
    assert srlib.diagrams.display_label(
        "copilot-mailbox-worker/src/copilot_mailbox_worker"
    ) == "copilot-mailbox-worker"


def test_package_labels_keep_anything_meaningful() -> None:
    """Over-correction guard: only `src` and a package dir mirroring its parent go."""
    for path in (
        "copilot-mailbox-worker/scripts",
        "afas-proxy/deploy",
        "onboarding-prevalidator/rules",
        "src/api",           # a plain repo where src IS the top level
        "(root)",
    ):
        assert srlib.diagrams.display_label(path) == path, path


def test_the_packages_mmd_and_svg_use_the_same_labels() -> None:
    """Same divergence risk as the context pair: two functions, one fact set."""
    facts = {
        "worker/src/worker/main.py": _f("worker/src/worker/main.py", deps=["worker/src/worker/db/x.py"]),
        "worker/src/worker/db/x.py": _f("worker/src/worker/db/x.py"),
    }
    mmd, _ = srlib.diagrams.package_dot(facts)
    svg = srlib.svg.packages_svg(facts)
    assert "worker/src/worker" not in mmd, "the .dot still shows the boilerplate path"
    assert "worker/src/worker" not in svg, "the .svg still shows the boilerplate path"


def test_through_box_counts_edges_not_sampled_segments() -> None:
    """A curve is flattened into ~16 segments, so counting (segment, box) pairs
    multiplied one real crossing by the sampling density — the count sat at exactly 16
    on a real repo no matter how the layout changed, because it was 1-3 crossings times
    16 samples. One edge crossing one box is one incident."""
    svg = (
        '<svg width="600" height="300">'
        '<rect x="250" y="130" width="100" height="40"/>'
        '<path d="M20,150 C200,150 400,150 580,150"/>'   # one curve, straight through
        "</svg>"
    )
    through, _, _ = srlib.svggeom._svg_geometry(svg)
    assert through == 1, f"one edge through one box must be 1 incident, got {through}"


def test_two_edges_through_the_same_box_count_twice() -> None:
    svg = (
        '<svg width="600" height="300">'
        '<rect x="250" y="120" width="100" height="60"/>'
        '<path d="M20,140 L580,140"/>'
        '<path d="M20,160 L580,160"/>'
        "</svg>"
    )
    through, _, _ = srlib.svggeom._svg_geometry(svg)
    assert through == 2


def test_an_edge_terminating_inside_its_own_box_is_not_through_it() -> None:
    """An edge ends AT the box it points to. A drawer that stops a few pixels inside
    the border, or an arrowhead drawn over it, is not the edge passing through a box on
    its way somewhere else — which is the only thing this metric is about."""
    svg = (
        '<svg width="600" height="300">'
        '<rect x="400" y="130" width="120" height="40"/>'
        '<path d="M20,150 L460,150"/>'   # ends deep inside that box
        "</svg>"
    )
    through, _, _ = srlib.svggeom._svg_geometry(svg)
    assert through == 0


def test_an_edge_passing_through_a_box_on_its_way_elsewhere_still_counts() -> None:
    """Over-correction guard for the rule above."""
    svg = (
        '<svg width="600" height="300">'
        '<rect x="250" y="130" width="100" height="40"/>'   # in the way
        '<rect x="500" y="130" width="80" height="40"/>'    # the destination
        '<path d="M20,150 L540,150"/>'
        "</svg>"
    )
    through, _, _ = srlib.svggeom._svg_geometry(svg)
    assert through == 1


def test_a_diagram_whose_edges_mostly_cross_is_reported(tmp_path: Path) -> None:
    """Crossings are what a reader sees as a tangle. The first gate used only
    edge-through-box, which scored the real architecture diagram 0 while it drew 11
    crossings over 12 edges and looked exactly like spaghetti — the metric said fine,
    the picture said otherwise, and the picture was right."""
    d = tmp_path / "diagrams"
    d.mkdir()
    # Six edges in a fan that all cross each other near the middle.
    paths = "".join(
        f'<path d="M 20 {40 + 40 * i} L 780 {280 - 40 * i}"/>' for i in range(6)
    )
    (d / "architecture.svg").write_text(f'<svg width="800" height="320">{paths}</svg>')
    warnings = srlib.svglint._lint_svg_geometry(d)
    assert len(warnings) == 1
    assert "cross" in warnings[0]


def test_a_diagram_with_a_few_crossings_is_left_alone(tmp_path: Path) -> None:
    """Over-correction guard. A couple of crossings is normal in a real topology — the
    shipped architecture diagram draws 2 over 12 edges — and a bipartite fan is crossy
    by nature at 0.47 per edge. Neither should warn."""
    d = tmp_path / "diagrams"
    d.mkdir()
    # Twelve edges, one crossing pair — the shipped architecture diagram's proportions.
    parallel = "".join(f'<path d="M 20 {30 + 22 * i} L 780 {30 + 22 * i}"/>' for i in range(10))
    crossing_pair = '<path d="M 20 290 L 780 310"/><path d="M 20 310 L 780 290"/>'
    (d / "architecture.svg").write_text(
        f'<svg width="800" height="340">{parallel}{crossing_pair}</svg>'
    )
    assert srlib.svglint._lint_svg_geometry(d) == []


def test_colliding_text_labels_are_counted() -> None:
    """The geometry metric measures paths against boxes and is blind to text. On a real
    repo the layering diagram had two pairs of edge labels printed on top of each other
    and two more printed over a node box — which is what actually made it unreadable,
    while every geometry number said it was fine."""
    svg = (
        '<svg width="400" height="200">'
        '<text x="100" y="100" font-size="11">14 imports</text>'
        '<text x="150" y="100" font-size="11">2 imports</text>'
        "</svg>"
    )
    assert srlib.svglint._svg_text_clashes(svg) == 1


def test_text_far_apart_does_not_collide() -> None:
    svg = (
        '<svg width="400" height="200">'
        '<text x="20" y="40" font-size="11">14</text>'
        '<text x="300" y="40" font-size="11">2</text>'
        '<text x="20" y="120" font-size="11">7</text>'
        "</svg>"
    )
    assert srlib.svglint._svg_text_clashes(svg) == 0


def test_a_label_drawn_twice_for_its_halo_is_one_label() -> None:
    """Every label in these drawers is emitted twice — once as a paper-coloured stroke
    to punch a hole in whatever passes behind, once as the glyphs. Counting the pair as
    a collision would report every single label as clashing with itself."""
    svg = (
        '<svg width="400" height="200">'
        '<text x="100" y="100" font-size="11" stroke="#ffffff">14</text>'
        '<text x="100" y="100" font-size="11">14</text>'
        "</svg>"
    )
    assert srlib.svglint._svg_text_clashes(svg) == 0


# --------------------------------------------------------------------------
# Task 9: database schema — SQL DDL detector
# --------------------------------------------------------------------------

from srlib import schema as sr_schema


def test_sql_ddl_detector_reads_table_name_and_columns():
    source = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL,
        note TEXT
    );
    """
    tables, unread = sr_schema.detect_sql_ddl(source, "schema.sql")

    assert unread == []
    assert len(tables) == 1
    assert tables[0]["name"] == "users"
    assert tables[0]["detector"] == "sql_ddl"
    assert [c["name"] for c in tables[0]["columns"]] == ["id", "email", "note"]


def test_sql_ddl_detector_marks_primary_key_and_not_null():
    source = "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL, note TEXT);"
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")
    by_name = {c["name"]: c for c in tables[0]["columns"]}

    assert by_name["id"]["pk"] is True
    assert by_name["email"]["pk"] is False
    assert by_name["email"]["nullable"] is False
    assert by_name["note"]["nullable"] is None


def test_sql_ddl_detector_reads_inline_references_as_foreign_key():
    source = "CREATE TABLE jobs (id INTEGER PRIMARY KEY, target_id TEXT REFERENCES targets(id));"
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")
    by_name = {c["name"]: c for c in tables[0]["columns"]}

    assert by_name["target_id"]["fk"] == "targets.id"


def test_sql_ddl_detector_reads_table_level_foreign_key_clause():
    source = """
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        target_id TEXT,
        FOREIGN KEY (target_id) REFERENCES targets(id)
    );
    """
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")
    by_name = {c["name"]: c for c in tables[0]["columns"]}

    assert by_name["target_id"]["fk"] == "targets.id"
    assert "FOREIGN" not in by_name


def test_sql_ddl_detector_accepts_if_not_exists_and_quoted_names():
    source = 'CREATE TABLE IF NOT EXISTS "audit log" (id INTEGER PRIMARY KEY);'
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")

    assert tables[0]["name"] == "audit log"


def test_sql_ddl_detector_reports_an_interpolated_table_name_as_unread():
    source = 'CREATE TABLE {prefix}_events (id INTEGER PRIMARY KEY);'
    tables, unread = sr_schema.detect_sql_ddl(source, "store.py")

    assert tables == []
    assert len(unread) == 1
    assert "interpolated" in unread[0]["why"]


def test_sql_ddl_detector_finds_several_tables_with_line_anchors():
    source = "CREATE TABLE a (id INTEGER);\n\nCREATE TABLE b (id INTEGER);\n"
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")

    assert [t["name"] for t in tables] == ["a", "b"]
    assert tables[0]["line"] == 1
    assert tables[1]["line"] == 3


def test_sql_ddl_detector_ignores_a_line_comment_holding_a_comma():
    source = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY, -- users primary key, unique
        email TEXT NOT NULL
    );
    """
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")

    assert [c["name"] for c in tables[0]["columns"]] == ["id", "email"]


def test_sql_ddl_detector_ignores_a_block_comment_holding_a_comma():
    source = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        /* internal, do not expose */
        email TEXT NOT NULL
    );
    """
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")

    assert [c["name"] for c in tables[0]["columns"]] == ["id", "email"]


def test_sql_ddl_detector_keeps_a_comment_marker_out_of_the_columns():
    source = "CREATE TABLE users (id INTEGER PRIMARY KEY, -- note, here\n email TEXT);"
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")
    names = [c["name"] for c in tables[0]["columns"]]

    assert "--" not in names
    assert "note" not in names


def test_sql_ddl_detector_keeps_line_anchors_correct_past_a_comment():
    source = "-- a leading note\n-- spanning two lines\nCREATE TABLE users (id INTEGER);\n"
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")

    assert tables[0]["line"] == 3


def test_sql_ddl_detector_leaves_a_comment_marker_inside_a_string_literal_alone():
    source = "CREATE TABLE users (id INTEGER, label TEXT DEFAULT '-- not, a comment');"
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")

    assert [c["name"] for c in tables[0]["columns"]] == ["id", "label"]


def test_sql_ddl_detector_reads_a_quoted_column_name_containing_a_space():
    source = 'CREATE TABLE events ("event id" INTEGER PRIMARY KEY, "user name" TEXT);'
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")
    columns = {c["name"]: c for c in tables[0]["columns"]}

    assert list(columns) == ["event id", "user name"]
    assert columns["event id"]["type"] == "INTEGER"
    assert columns["event id"]["pk"] is True
    assert columns["user name"]["type"] == "TEXT"


def test_sql_ddl_detector_reads_a_bracket_quoted_column_name():
    source = "CREATE TABLE events ([event id] INTEGER NOT NULL);"
    tables, _ = sr_schema.detect_sql_ddl(source, "schema.sql")
    column = tables[0]["columns"][0]

    assert column["name"] == "event id"
    assert column["type"] == "INTEGER"
    assert column["nullable"] is False


# --------------------------------------------------------------------------
# Task 10: database schema — SQLAlchemy detector
# --------------------------------------------------------------------------

SQLALCHEMY_2_SOURCE = '''
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    input: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    jobs = relationship("Job", back_populates="target")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(36), ForeignKey("targets.id"), nullable=False)
'''

SQLALCHEMY_1_SOURCE = '''
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(String(200))
'''


def test_sqlalchemy_detector_reads_modern_mapped_column_models():
    tree = ast.parse(SQLALCHEMY_2_SOURCE)
    tables, unread = sr_schema.detect_sqlalchemy(tree, SQLALCHEMY_2_SOURCE)

    assert unread == []
    assert [t["name"] for t in tables] == ["targets", "jobs"]
    assert tables[0]["detector"] == "sqlalchemy"


def test_sqlalchemy_detector_marks_primary_key_and_foreign_key():
    tree = ast.parse(SQLALCHEMY_2_SOURCE)
    tables, _ = sr_schema.detect_sqlalchemy(tree, SQLALCHEMY_2_SOURCE)
    jobs = {c["name"]: c for c in tables[1]["columns"]}

    assert jobs["id"]["pk"] is True
    assert jobs["target_id"]["fk"] == "targets.id"
    assert jobs["target_id"]["nullable"] is False


def test_sqlalchemy_detector_records_the_column_type():
    tree = ast.parse(SQLALCHEMY_2_SOURCE)
    tables, _ = sr_schema.detect_sqlalchemy(tree, SQLALCHEMY_2_SOURCE)
    targets = {c["name"]: c for c in tables[0]["columns"]}

    assert targets["input"]["type"] == "String(512)"


def test_sqlalchemy_detector_skips_relationship_attributes():
    tree = ast.parse(SQLALCHEMY_2_SOURCE)
    tables, _ = sr_schema.detect_sqlalchemy(tree, SQLALCHEMY_2_SOURCE)

    assert "jobs" not in {c["name"] for c in tables[0]["columns"]}


def test_sqlalchemy_detector_reads_classic_column_models():
    tree = ast.parse(SQLALCHEMY_1_SOURCE)
    tables, _ = sr_schema.detect_sqlalchemy(tree, SQLALCHEMY_1_SOURCE)
    notes = {c["name"]: c for c in tables[0]["columns"]}

    assert tables[0]["name"] == "notes"
    assert notes["id"]["pk"] is True
    assert notes["owner_id"]["fk"] == "users.id"
    assert notes["body"]["type"] == "String(200)"


def test_sqlalchemy_detector_records_the_declarative_base_name():
    tree = ast.parse(SQLALCHEMY_2_SOURCE)
    tables, _ = sr_schema.detect_sqlalchemy(tree, SQLALCHEMY_2_SOURCE)

    assert {t["base"] for t in tables} == {"Base"}


def test_sqlalchemy_detector_carries_class_line_range():
    tree = ast.parse(SQLALCHEMY_2_SOURCE)
    tables, _ = sr_schema.detect_sqlalchemy(tree, SQLALCHEMY_2_SOURCE)

    assert tables[0]["line"] < tables[0]["end_line"]


def test_sqlalchemy_detector_ignores_a_plain_class_with_fields():
    source = "class Settings:\n    host = 'localhost'\n    port = 5432\n"
    tables, _ = sr_schema.detect_sqlalchemy(ast.parse(source), source)

    assert tables == []


EXPLICIT_NAME_SOURCE = '''
from sqlalchemy import Column, ForeignKey, Integer
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Note(Base):
    __tablename__ = "notes"

    owner_id = Column("target_id", Integer, ForeignKey("targets.id"), nullable=False)
'''


def test_sqlalchemy_detector_uses_the_explicit_database_column_name():
    tables, _ = sr_schema.detect_sqlalchemy(ast.parse(EXPLICIT_NAME_SOURCE), EXPLICIT_NAME_SOURCE)

    assert [c["name"] for c in tables[0]["columns"]] == ["target_id"]


def test_sqlalchemy_detector_does_not_read_an_explicit_name_as_the_type():
    tables, _ = sr_schema.detect_sqlalchemy(ast.parse(EXPLICIT_NAME_SOURCE), EXPLICIT_NAME_SOURCE)

    assert tables[0]["columns"][0]["type"] == "Integer"
    assert tables[0]["columns"][0]["fk"] == "targets.id"


def test_sqlalchemy_detector_keeps_a_column_declared_only_by_its_annotation():
    source = '''
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
'''
    tables, _ = sr_schema.detect_sqlalchemy(ast.parse(source), source)
    columns = {c["name"]: c for c in tables[0]["columns"]}

    assert list(columns) == ["id", "name"]
    assert columns["name"]["type"] == "str"
    assert columns["name"]["pk"] is False


def test_sqlalchemy_detector_still_skips_a_relationship_carrying_a_mapped_annotation():
    source = '''
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    jobs: Mapped[list["Job"]] = relationship(back_populates="target")
'''
    tables, _ = sr_schema.detect_sqlalchemy(ast.parse(source), source)

    assert [c["name"] for c in tables[0]["columns"]] == ["id"]


def test_sqlalchemy_detector_skips_a_module_qualified_foreign_key_as_the_type():
    source = '''
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Note(Base):
    __tablename__ = "notes"

    owner_id = sa.Column(sa.Integer, sa.ForeignKey("users.id"))
'''
    tables, _ = sr_schema.detect_sqlalchemy(ast.parse(source), source)
    column = tables[0]["columns"][0]

    assert column["type"] == "sa.Integer"
    assert column["fk"] == "users.id"


# --------------------------------------------------------------------------
# Task 11: database schema — database identity
# --------------------------------------------------------------------------


def test_database_name_takes_the_stem_of_a_sqlite_path():
    assert sr_schema.database_name("data/osintov.db") == "osintov"


def test_database_name_takes_the_database_out_of_a_dsn():
    assert sr_schema.database_name("postgresql://db.internal:5432/app_prod") == "app_prod"


def test_database_name_never_returns_credentials_from_a_dsn():
    name = sr_schema.database_name("postgresql://admin:hunter2@db.internal/app_prod")

    assert name == "app_prod"
    assert "hunter2" not in name
    assert "admin" not in name


def test_database_name_handles_a_sqlite_url():
    assert sr_schema.database_name("sqlite:///data/osintov.db") == "osintov"


def test_database_name_returns_none_for_an_in_memory_database():
    assert sr_schema.database_name(":memory:") is None


def test_detect_targets_finds_a_sqlite_connect_literal():
    source = "import sqlite3\n\nconn = sqlite3.connect('bot.db')\n"
    targets = sr_schema.detect_targets(ast.parse(source))

    assert targets == [{"name": "bot", "line": 3, "kind": "sqlite"}]


def test_detect_targets_finds_a_create_engine_literal():
    source = "from sqlalchemy import create_engine\n\nengine = create_engine('sqlite:///osintov.db')\n"
    targets = sr_schema.detect_targets(ast.parse(source))

    assert targets == [{"name": "osintov", "line": 3, "kind": "engine"}]


def test_detect_targets_ignores_a_connection_built_from_config():
    source = "from sqlalchemy import create_engine\n\nengine = create_engine(settings.DATABASE_URL)\n"

    assert sr_schema.detect_targets(ast.parse(source)) == []


def test_detect_targets_never_stores_the_raw_connection_string():
    source = "engine = create_engine('postgresql://admin:hunter2@db/app_prod')\n"
    targets = sr_schema.detect_targets(ast.parse(source))

    assert targets == [{"name": "app_prod", "line": 1, "kind": "engine"}]
    assert "hunter2" not in repr(targets)


# --------------------------------------------------------------------------
# Task 12: database schema — facts and plan
# --------------------------------------------------------------------------

SCHEMA_REPO_DB = '''
import sqlite3

SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL
);
"""


def init():
    conn = sqlite3.connect("app.db")
    conn.execute(SCHEMA)
'''


def test_extract_python_collects_tables_from_ddl_in_a_string_literal():
    facts = sr.extract_python(SCHEMA_REPO_DB, "store.py")

    assert [t["name"] for t in facts.schema["tables"]] == ["users"]


def test_extract_python_collects_the_connection_target():
    facts = sr.extract_python(SCHEMA_REPO_DB, "store.py")

    assert [t["name"] for t in facts.schema["targets"]] == ["app"]


def test_extract_python_collects_tables_from_sqlalchemy_models():
    facts = sr.extract_python(SQLALCHEMY_2_SOURCE, "db.py")

    assert [t["name"] for t in facts.schema["tables"]] == ["targets", "jobs"]


def test_extract_python_reports_ddl_it_could_not_read_as_unread():
    source = 'SQL = "CREATE TABLE {prefix}_events (id INTEGER PRIMARY KEY);"\n'
    facts = sr.extract_python(source, "store.py")

    assert facts.schema["tables"] == []
    assert len(facts.schema["unread"]) == 1


def test_extract_python_ignores_ddl_shown_in_a_docstring():
    source = '"""Example schema:\n\nCREATE TABLE users (id INTEGER PRIMARY KEY);\n"""\n\nx = 1\n'
    facts = sr.extract_python(source, "docs.py")

    assert facts.schema["tables"] == []


def test_extract_python_still_reads_ddl_from_a_non_docstring_literal_in_a_function():
    source = 'def init(conn):\n    """Create the tables."""\n    conn.execute("CREATE TABLE users (id INTEGER);")\n'
    facts = sr.extract_python(source, "store.py")

    assert [t["name"] for t in facts.schema["tables"]] == ["users"]


def test_extract_python_leaves_schema_empty_for_a_file_with_no_database():
    facts = sr.extract_python("def add(a, b):\n    return a + b\n", "math.py")

    assert facts.schema == {"tables": [], "targets": [], "unread": []}


def test_plan_writes_schema_facts_for_a_python_file(tmp_path):
    (tmp_path / "db.py").write_text(SQLALCHEMY_2_SOURCE)
    sr.cmd_plan(root=tmp_path, out=tmp_path / "soulreap")
    facts = _read_facts(tmp_path / "soulreap")

    assert [t["name"] for t in facts["db.py"]["schema"]["tables"]] == ["targets", "jobs"]


def test_plan_reads_schema_out_of_a_sql_file(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "schema.sql").write_text("CREATE TABLE users (id INTEGER PRIMARY KEY);\n")
    sr.cmd_plan(root=tmp_path, out=tmp_path / "soulreap")
    facts = _read_facts(tmp_path / "soulreap")

    assert [t["name"] for t in facts["schema.sql"]["schema"]["tables"]] == ["users"]


def test_facts_version_is_eight_so_older_runs_are_reported_stale():
    """Bumped whenever the SHAPE of facts.json changes, because a render reads facts
    it did not gather. Four: class fields and structures — a repo carded before that
    kept a facts.json with neither, and a skeleton built from it described a dataclass
    as a name with no contents. Five: observed stores and background tasks, without
    which a stale facts.json silently draws no store and no task picture at all. Six:
    `doc_more` and unclipped constants — an `EDITABLE = {…}` clipped at 80 chars lost
    eight of its nine keys, and the docstring line naming them was never stored. Seven:
    per-callable `callees`, `hazards` and `sfp` — v10 staleness pins prose to them. Eight:
    TS functions record `exported`, which the importance score weighs."""
    assert srlib.facts.FACTS_VERSION == 8


def test_render_writes_no_card_for_a_sql_file(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "schema.sql").write_text("CREATE TABLE users (id INTEGER PRIMARY KEY);\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_render(root=tmp_path, out=out)

    assert not (out / "files" / "schema.sql.md").exists()
    assert "schema.sql" not in (out / "INDEX.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Task 13: database schema — skeleton section
# --------------------------------------------------------------------------


@pytest.fixture
def schema_repo(tmp_path):
    (tmp_path / "db.py").write_text(SQLALCHEMY_2_SOURCE)
    return tmp_path


def test_skeleton_prints_the_tables_a_file_defines(schema_repo, capsys):
    out = schema_repo / "soulreap"
    sr.cmd_plan(root=schema_repo, out=out)
    sr.cmd_skeleton(root=schema_repo, out=out, target="db.py")
    printed = capsys.readouterr().out

    assert "## Schema" in printed
    assert "table targets" in printed
    assert "table jobs" in printed


def test_skeleton_prints_columns_with_key_markers(schema_repo, capsys):
    out = schema_repo / "soulreap"
    sr.cmd_plan(root=schema_repo, out=out)
    sr.cmd_skeleton(root=schema_repo, out=out, target="db.py")
    printed = capsys.readouterr().out

    assert "id String(36) PK" in printed
    assert "target_id String(36) FK -> targets.id" in printed


def test_skeleton_names_the_database_and_erd_slug_a_carder_needs_for_the_link(schema_repo, capsys):
    # file-card.md tells the carder to link diagrams/erd-<slug>.svg. Cards are
    # written before render ever runs, so the slug has to come from here —
    # this is the exact gap the final whole-branch review found by reading a
    # real card generated for osintov/app/db.py, whose Schema section named
    # no database at all.
    out = schema_repo / "soulreap"
    sr.cmd_plan(root=schema_repo, out=out)
    sr.cmd_skeleton(root=schema_repo, out=out, target="db.py")
    printed = capsys.readouterr().out

    assert "database Base" in printed
    assert "diagrams/erd-base.svg" in printed


def test_skeleton_omits_the_schema_section_for_a_file_without_tables(tmp_path, capsys):
    (tmp_path / "math.py").write_text("def add(a, b):\n    return a + b\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_skeleton(root=tmp_path, out=out, target="math.py")

    assert "## Schema" not in capsys.readouterr().out


def test_skeleton_reports_unreadable_ddl(tmp_path, capsys):
    (tmp_path / "store.py").write_text('SQL = "CREATE TABLE {p}_events (id INTEGER);"\n')
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_skeleton(root=tmp_path, out=out, target="store.py")

    assert "not read" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Task 14: database schema — grouping and erDiagram
# --------------------------------------------------------------------------

from srlib import diagrams as sr_diagrams


def _facts_with_schema(**by_path):
    return {path: {"lang": "python", "imports": [], "deps": [], "schema": blob}
            for path, blob in by_path.items()}


def test_diagram_paths_leave_sql_files_out():
    facts = {
        "src/app.py": {"lang": "python"},
        "src/db.py": {"lang": "python"},
        "sql/schema.sql": {"lang": "sql"},
    }

    assert sr_diagrams.diagram_paths(facts) == ["src/app.py", "src/db.py"]


def test_group_databases_keys_a_database_by_its_connection_target():
    facts = _facts_with_schema(**{
        "store.py": {
            "tables": [{"name": "users", "line": 1, "end_line": 4, "detector": "sql_ddl", "columns": []}],
            "targets": [{"name": "app", "line": 9, "kind": "sqlite"}],
            "unread": [],
        }
    })
    grouped = sr_diagrams.group_databases(facts)

    assert [db["name"] for db in grouped.values()] == ["app"]


def test_group_databases_splits_two_stores_into_two_databases():
    facts = _facts_with_schema(**{
        "a.py": {"tables": [{"name": "t1", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": []}],
                 "targets": [{"name": "first", "line": 1, "kind": "sqlite"}], "unread": []},
        "b.py": {"tables": [{"name": "t2", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": []}],
                 "targets": [{"name": "second", "line": 1, "kind": "sqlite"}], "unread": []},
    })
    grouped = sr_diagrams.group_databases(facts)

    assert sorted(db["name"] for db in grouped.values()) == ["first", "second"]


def test_group_databases_never_merges_two_targets_declared_in_one_file():
    facts = _facts_with_schema(**{
        "store.py": {
            "tables": [
                {"name": "alpha_rows", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": []},
                {"name": "beta_rows", "line": 3, "end_line": 4, "detector": "sql_ddl", "columns": []},
            ],
            "targets": [
                {"name": "alpha", "line": 10, "kind": "sqlite"},
                {"name": "beta", "line": 11, "kind": "sqlite"},
            ],
            "unread": [],
        },
    })
    grouped = sr_diagrams.group_databases(facts)

    # The literal is ambiguous, so the connection target does not count here,
    # and grouping falls through to the next rung of the ladder — but since no
    # fact links the tables to each other either, each keeps its own separate
    # group instead of merging under one guessed module name.
    assert len(grouped) == 2
    table_sets = {frozenset(t["name"] for t in db["tables"]) for db in grouped.values()}
    assert table_sets == {frozenset({"alpha_rows"}), frozenset({"beta_rows"})}


def test_group_databases_keeps_one_declarative_base_as_one_database():
    facts = _facts_with_schema(**{
        "models/user.py": {"tables": [{"name": "users", "line": 1, "end_line": 2, "detector": "sqlalchemy",
                                       "base": "Base", "columns": []}], "targets": [], "unread": []},
        "models/job.py": {"tables": [{"name": "jobs", "line": 1, "end_line": 2, "detector": "sqlalchemy",
                                      "base": "Base", "columns": []}], "targets": [], "unread": []},
    })
    grouped = sr_diagrams.group_databases(facts)

    assert len(grouped) == 1
    assert sorted(t["name"] for t in next(iter(grouped.values()))["tables"]) == ["jobs", "users"]


def test_group_databases_keeps_sql_files_of_one_directory_as_one_database():
    facts = _facts_with_schema(**{
        "sql/create_summary.sql": {
            "tables": [{"name": "summary", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": []}],
            "targets": [], "unread": []},
        "sql/create_telemetry.sql": {
            "tables": [{"name": "telemetry", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": []}],
            "targets": [], "unread": []},
    })
    grouped = sr_diagrams.group_databases(facts)

    assert len(grouped) == 1
    assert next(iter(grouped.values()))["name"] == "sql"


def test_group_databases_falls_back_to_the_module_when_nothing_resolves():
    facts = _facts_with_schema(**{
        "store.py": {"tables": [{"name": "users", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": []}],
                     "targets": [], "unread": []},
    })
    grouped = sr_diagrams.group_databases(facts)

    assert next(iter(grouped.values()))["name"] == "store"


def test_group_databases_records_a_source_for_unread_only_schema():
    # `sources` used to be populated only by the tables loop, so a file whose
    # only contribution was an unread DDL fragment never appeared in it —
    # which meant _schema_lines could never show that file's "database" line
    # even once it started reading `sources` to build that line.
    facts = _facts_with_schema(**{
        "store.py": {
            "tables": [],
            "targets": [{"name": "app", "line": 1, "kind": "sqlite"}],
            "unread": [{"line": 5, "why": "interpolated"}],
        },
    })
    grouped = sr_diagrams.group_databases(facts)

    assert "store.py" in next(iter(grouped.values()))["sources"]


def test_group_databases_keeps_one_entry_when_a_table_is_declared_twice():
    column = {"name": "id", "type": "INTEGER", "pk": True, "fk": None, "nullable": False}
    facts = _facts_with_schema(**{
        "schema.sql": {
            "tables": [{"name": "users", "line": 1, "end_line": 2, "detector": "sql_ddl",
                        "columns": [column]}],
            "targets": [{"name": "app", "line": 1, "kind": "sqlite"}], "unread": []},
        "models.py": {
            "tables": [{"name": "users", "line": 1, "end_line": 2, "detector": "sqlalchemy",
                        "base": "Base",
                        "columns": [column, {"name": "email", "type": "String", "pk": False,
                                             "fk": None, "nullable": False}]}],
            "targets": [{"name": "app", "line": 1, "kind": "sqlite"}], "unread": []},
    })
    grouped = sr_diagrams.group_databases(facts)
    tables = next(iter(grouped.values()))["tables"]

    assert [t["name"] for t in tables] == ["users"]
    assert [c["name"] for c in tables[0]["columns"]] == ["id", "email"]


def test_erd_mermaid_declares_tables_and_columns():
    database = {
        "name": "app",
        "sources": ["db.py"],
        "unread": [],
        "tables": [{"name": "users", "line": 1, "end_line": 4, "detector": "sql_ddl", "columns": [
            {"name": "id", "type": "INTEGER", "pk": True, "fk": None, "nullable": False},
        ]}],
    }
    text = sr_diagrams.erd_mermaid(database)

    assert "erDiagram" in text
    assert "users {" in text
    assert "INTEGER id PK" in text


def test_erd_mermaid_draws_a_relationship_for_a_foreign_key():
    database = {
        "name": "app",
        "sources": ["db.py"],
        "unread": [],
        "tables": [
            {"name": "targets", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": [
                {"name": "id", "type": "TEXT", "pk": True, "fk": None, "nullable": False}]},
            {"name": "jobs", "line": 3, "end_line": 4, "detector": "sql_ddl", "columns": [
                {"name": "target_id", "type": "TEXT", "pk": False, "fk": "targets.id", "nullable": False}]},
        ],
    }
    text = sr_diagrams.erd_mermaid(database)

    assert "targets ||--o{ jobs" in text


def test_erd_mermaid_sanitizes_a_type_that_carries_parentheses():
    database = {
        "name": "app", "sources": ["db.py"], "unread": [],
        "tables": [{"name": "users", "line": 1, "end_line": 2, "detector": "sqlalchemy", "columns": [
            {"name": "email", "type": "String(512)", "pk": False, "fk": None, "nullable": True}]}],
    }
    text = sr_diagrams.erd_mermaid(database)

    assert "String_512 email" in text
    assert "String(512)" not in text


def test_erd_mermaid_aliases_a_table_name_containing_a_space():
    database = {
        "name": "app", "sources": ["schema.sql"], "unread": [],
        "tables": [{"name": "audit log", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": [
            {"name": "id", "type": "INTEGER", "pk": True, "fk": None, "nullable": False}]}],
    }
    text = sr_diagrams.erd_mermaid(database)

    assert 'audit_log["audit log"] {' in text
    assert "audit log {" not in text


def test_erd_mermaid_uses_the_alias_id_in_a_relationship_line():
    database = {
        "name": "app", "sources": ["schema.sql"], "unread": [],
        "tables": [
            {"name": "audit log", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": [
                {"name": "id", "type": "INTEGER", "pk": True, "fk": None, "nullable": False}]},
            {"name": "users", "line": 3, "end_line": 4, "detector": "sql_ddl", "columns": [
                {"name": "audit_id", "type": "INTEGER", "pk": False, "fk": "audit log.id",
                 "nullable": False}]},
        ],
    }
    text = sr_diagrams.erd_mermaid(database)
    relationship_line = next(ln for ln in text.splitlines() if "||--o{" in ln)

    assert relationship_line.strip() == 'audit_log ||--o{ users : "audit_id"'


def test_erd_mermaid_disambiguates_two_names_that_sanitize_alike():
    database = {
        "name": "app", "sources": ["schema.sql"], "unread": [],
        "tables": [
            {"name": "audit log", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": []},
            {"name": "audit-log", "line": 3, "end_line": 4, "detector": "sql_ddl", "columns": []},
        ],
    }
    text = sr_diagrams.erd_mermaid(database)

    assert 'audit_log["audit log"] {' in text
    assert 'audit_log_2["audit-log"] {' in text


def test_erd_mermaid_disambiguation_never_collides_with_an_already_clean_name():
    database = {
        "name": "app", "sources": ["schema.sql"], "unread": [],
        "tables": [
            {"name": "a b", "line": 1, "end_line": 1, "detector": "sql_ddl", "columns": []},
            {"name": "a-b", "line": 2, "end_line": 2, "detector": "sql_ddl", "columns": []},
            {"name": "a_b_2", "line": 3, "end_line": 3, "detector": "sql_ddl", "columns": []},
        ],
    }
    text = sr_diagrams.erd_mermaid(database)
    headers = [ln.strip() for ln in text.splitlines() if ln.strip().endswith("{")]
    ids = [header.split("[", 1)[0].split(" ", 1)[0] for header in headers]

    assert len(ids) == 3
    assert len(set(ids)) == 3


def test_erd_mermaid_refuses_a_database_with_a_duplicate_table_name():
    database = {
        "name": "app", "sources": ["a.py", "b.py"], "unread": [],
        "tables": [
            {"name": "a", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": []},
            {"name": "a", "line": 3, "end_line": 4, "detector": "sqlalchemy", "columns": []},
        ],
    }

    with pytest.raises(ValueError, match="duplicate table name"):
        sr_diagrams.erd_mermaid(database)


# --------------------------------------------------------------------------
# Task 15: database schema — ERD as SVG
# --------------------------------------------------------------------------

from srlib import mermaid_svg as sr_mermaid
from srlib import svg as sr_svg

ERD_DATABASE = {
    "name": "app",
    "sources": ["db.py"],
    "unread": [],
    "tables": [
        {"name": "targets", "line": 1, "end_line": 2, "detector": "sqlalchemy", "columns": [
            {"name": "id", "type": "String(36)", "pk": True, "fk": None, "nullable": False},
            {"name": "input", "type": "String(512)", "pk": False, "fk": None, "nullable": False}]},
        {"name": "jobs", "line": 3, "end_line": 4, "detector": "sqlalchemy", "columns": [
            {"name": "id", "type": "String(36)", "pk": True, "fk": None, "nullable": False},
            {"name": "target_id", "type": "String(36)", "pk": False, "fk": "targets.id", "nullable": False}]},
    ],
}


def test_erd_svg_is_a_well_formed_svg_document():
    drawing = sr_svg.erd_svg(ERD_DATABASE)

    assert drawing.startswith("<svg")
    assert drawing.rstrip().endswith("</svg>")


def test_erd_svg_names_every_table_and_column():
    drawing = sr_svg.erd_svg(ERD_DATABASE)

    for text in ("targets", "jobs", "input", "target_id"):
        assert text in drawing


def test_erd_svg_carries_the_database_name_in_the_title():
    assert "app" in sr_svg.erd_svg(ERD_DATABASE)


def test_erd_svg_draws_one_edge_per_foreign_key():
    drawing = sr_svg.erd_svg(ERD_DATABASE)

    # Count by the arrowhead marker, not by "<path d=": every <marker> definition
    # carries its own <path>, which would otherwise be swept into the count. The
    # head is `m<palette slot>` rather than the shared `arrow` because each edge
    # takes the colour of the table it leaves.
    assert drawing.count('marker-end="url(#m') == 1


def test_erd_svg_folds_columns_when_there_are_many_tables():
    tables = [
        {"name": f"t{i}", "line": i, "end_line": i, "detector": "sql_ddl", "columns": [
            {"name": "id", "type": "INTEGER", "pk": True, "fk": None, "nullable": False},
            {"name": "body", "type": "TEXT", "pk": False, "fk": None, "nullable": True},
            {"name": "note", "type": "TEXT", "pk": False, "fk": None, "nullable": True}]}
        for i in range(20)
    ]
    drawing = sr_svg.erd_svg({"name": "big", "sources": [], "unread": [], "tables": tables})

    assert "+2 columns" in drawing
    assert "body" not in drawing


def test_erd_svg_handles_a_database_with_a_single_table():
    single = {"name": "solo", "sources": [], "unread": [], "tables": [ERD_DATABASE["tables"][0]]}
    drawing = sr_svg.erd_svg(single)

    assert "targets" in drawing
    assert drawing.count('marker-end="url(#arrow)"') == 0


def test_erd_svg_declares_a_canvas_tall_enough_for_every_box():
    drawing = sr_svg.erd_svg(ERD_DATABASE)
    declared_height = float(re.search(r'height="([\d.]+)"', drawing).group(1))
    box_bottoms = [
        float(y) + float(h)
        for y, h in re.findall(r'<rect x="[\d.]+" y="([\d.]+)" width="[\d.]+" height="([\d.]+)"', drawing)
    ]

    assert box_bottoms
    assert max(box_bottoms) < declared_height


def test_erd_svg_refuses_a_database_with_a_duplicate_table_name():
    duplicate = {
        "name": "app", "sources": ["a.py", "b.py"], "unread": [],
        "tables": [
            {"name": "a", "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": []},
            {"name": "a", "line": 3, "end_line": 4, "detector": "sqlalchemy", "columns": []},
        ],
    }

    with pytest.raises(ValueError, match="duplicate table name"):
        sr_svg.erd_svg(duplicate)


# --------------------------------------------------------------------------
# Task 16: database schema — render wiring
# --------------------------------------------------------------------------


@pytest.fixture
def two_database_repo(tmp_path):
    (tmp_path / "first.py").write_text(
        'import sqlite3\n'
        'SQL = """CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL);"""\n'
        'conn = sqlite3.connect("alpha.db")\n'
    )
    (tmp_path / "second.py").write_text(
        'import sqlite3\n'
        'SQL = """CREATE TABLE events (id INTEGER PRIMARY KEY);"""\n'
        'conn = sqlite3.connect("beta.db")\n'
    )
    return tmp_path


def test_render_writes_one_erd_per_database(two_database_repo):
    out = two_database_repo / "soulreap"
    sr.cmd_plan(root=two_database_repo, out=out)
    sr.cmd_render(root=two_database_repo, out=out)

    assert (out / "diagrams" / "erd-alpha.svg").exists()
    assert (out / "diagrams" / "erd-beta.svg").exists()


def test_render_draws_an_svg_beside_every_erd(two_database_repo):
    out = two_database_repo / "soulreap"
    sr.cmd_plan(root=two_database_repo, out=out)
    sr.cmd_render(root=two_database_repo, out=out)

    assert (out / "diagrams" / "erd-alpha.svg").exists()
    assert (out / "diagrams" / "erd-beta.svg").exists()


def test_render_reports_the_database_and_table_counts(two_database_repo):
    out = two_database_repo / "soulreap"
    sr.cmd_plan(root=two_database_repo, out=out)
    result = sr.cmd_render(root=two_database_repo, out=out)

    assert result["schema_databases"] == 2
    assert result["schema_tables"] == 2


def test_render_reports_ddl_it_could_not_read(tmp_path):
    (tmp_path / "store.py").write_text('SQL = "CREATE TABLE {p}_events (id INTEGER);"\n')
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    result = sr.cmd_render(root=tmp_path, out=out)

    assert any("store.py" in warning for warning in result["schema_unread"])


def test_render_writes_no_erd_for_a_repo_without_a_database(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out)
    result = sr.cmd_render(root=sample_repo, out=out)

    assert result["schema_databases"] == 0
    assert list((out / "diagrams").glob("erd-*.svg")) == []


def test_render_does_not_warn_that_an_erd_is_undrawable(two_database_repo):
    out = two_database_repo / "soulreap"
    sr.cmd_plan(root=two_database_repo, out=out)
    result = sr.cmd_render(root=two_database_repo, out=out)

    assert not [w for w in result["svg_warnings"] if "erd-" in w]


def test_render_removes_the_erd_of_a_database_that_is_gone(two_database_repo):
    out = two_database_repo / "soulreap"
    sr.cmd_plan(root=two_database_repo, out=out)
    sr.cmd_render(root=two_database_repo, out=out)
    (two_database_repo / "second.py").unlink()
    sr.cmd_plan(root=two_database_repo, out=out)
    sr.cmd_render(root=two_database_repo, out=out)

    assert (out / "diagrams" / "erd-alpha.svg").exists()
    assert not (out / "diagrams" / "erd-beta.svg").exists()


@pytest.fixture
def slug_collision_repo(tmp_path):
    (tmp_path / "one.py").write_text(
        'import sqlite3\n'
        'SQL = """CREATE TABLE widgets (id INTEGER PRIMARY KEY);"""\n'
        'conn = sqlite3.connect("user_data.db")\n'
    )
    (tmp_path / "two.py").write_text(
        'import sqlite3\n'
        'SQL = """CREATE TABLE gadgets (id INTEGER PRIMARY KEY);"""\n'
        'conn = sqlite3.connect("user-data.db")\n'
    )
    return tmp_path


def test_render_disambiguates_two_databases_whose_names_share_a_slug(slug_collision_repo):
    # "user_data" and "user-data" are different database names but the same
    # erd_slug. Before the fix the second write silently overwrote the
    # first's files while render still reported two databases drawn.
    out = slug_collision_repo / "soulreap"
    sr.cmd_plan(root=slug_collision_repo, out=out)
    result = sr.cmd_render(root=slug_collision_repo, out=out)

    written = sorted(p.name for p in (out / "diagrams").glob("erd-*.svg"))
    assert result["schema_databases"] == 2
    assert len(written) == 2
    contents = [(out / "diagrams" / name).read_text(encoding="utf-8") for name in written]
    assert any("widgets" in c for c in contents)
    assert any("gadgets" in c for c in contents)


def test_render_and_skeleton_agree_on_which_slug_belongs_to_which_database(slug_collision_repo):
    # A carder reads the slug from `skeleton`, before `render` ever runs. If
    # the two compute slugs independently, the printed link can resolve to a
    # REAL file — just the wrong database's. Second review round caught
    # exactly this on this fixture: skeleton for two.py named erd-user-data
    # (the FIRST database's file, drawing widgets, not gadgets).
    out = slug_collision_repo / "soulreap"
    sr.cmd_plan(root=slug_collision_repo, out=out)
    sr.cmd_render(root=slug_collision_repo, out=out)

    for source, table_name in (("one.py", "widgets"), ("two.py", "gadgets")):
        text = sr.cmd_skeleton(root=slug_collision_repo, out=out, target=source)
        match = re.search(r"diagrams/erd-([a-z0-9-]+)\.svg", text)
        assert match, f"no database line in skeleton for {source}"
        assert (out / "diagrams" / f"erd-{match.group(1)}.svg").exists(), \
            f"no erd svg for slug {match.group(1)}"


def test_skeleton_prints_no_database_line_for_a_group_with_only_unread_schema(tmp_path):
    # `_write_erds` never writes a file for a database with zero readable
    # tables. If skeleton still printed a `database` line for it, the link
    # would point at a diagram render provably never creates.
    (tmp_path / "dyn.py").write_text(
        'import sqlite3\n'
        'conn = sqlite3.connect("app.db")\n'
        'name = "events"\n'
        'SQL = f"CREATE TABLE {name} (id INTEGER)"\n'
    )
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    text = sr.cmd_skeleton(root=tmp_path, out=out, target="dyn.py")

    assert "not read" in text
    assert "diagrams/erd-" not in text


def test_render_still_warns_on_a_multi_statement_script_with_create_table_not_first(tmp_path):
    (tmp_path / "migrate.py").write_text(
        'import sqlite3\n\n'
        'def reset(conn, name):\n'
        '    conn.execute("DROP TABLE IF EXISTS old; CREATE TABLE " + name + "x (id INTEGER)")\n'
    )
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    result = sr.cmd_render(root=tmp_path, out=out)

    assert any("migrate.py" in warning for warning in result["schema_unread"])


def test_render_still_warns_on_create_table_evidence_after_a_leading_sql_comment(tmp_path):
    (tmp_path / "migrate2.py").write_text(
        'import sqlite3\n\n'
        'def build(conn, name):\n'
        '    conn.execute("-- schema v2\\nCREATE TABLE " + name + " (id INTEGER)")\n'
    )
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    result = sr.cmd_render(root=tmp_path, out=out)

    assert any("migrate2.py" in warning for warning in result["schema_unread"])


def test_render_does_not_warn_about_a_file_that_only_consumes_a_database(tmp_path):
    (tmp_path / "schema.py").write_text(
        'import sqlite3\n'
        'SQL = """CREATE TABLE users (id INTEGER PRIMARY KEY);"""\n'
        'conn = sqlite3.connect("app.db")\n'
    )
    (tmp_path / "routes.py").write_text(
        'import sqlite3\n\n'
        'def get_user(conn, user_id):\n'
        '    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()\n'
    )
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    result = sr.cmd_render(root=tmp_path, out=out)

    assert not any("routes.py" in warning for warning in result["schema_unread"])


# --------------------------------------------------------------------------
# Task 17: database schema — template contract
# --------------------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parent.parent


def test_overview_template_documents_the_data_model_section():
    text = (SKILL_DIR / "references" / "overview.md").read_text(encoding="utf-8")

    assert "erd-" in text


def test_skill_documents_the_schema_warnings():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    # The two forms drawing.py actually emits into `schema_unread`.
    assert "schema_unread" in text and "<path>:<line>: <why>" in text
    assert "db evidence, but no schema was found" in text

# --------------------------------------------------------------------------
# The ERD drawer's geometry
#
# Everything below holds for the flowchart drawer already (mermaid_svg draws
# every flowchart this way). The ERD drawer was written without any of it, and
# nvm-forecast's own schema is what showed the cost: 5 edges through a box,
# three pairs of arrows leaving one pixel, all of them the same grey.
# --------------------------------------------------------------------------


_ERD_EDGE = re.compile(
    # Start point, last point, colour — for a curve between adjacent rows and for
    # the cornered path a longer edge is routed along alike.
    r'<path d="M([\d.]+),([\d.]+)[^"]*?([\d.]+),([\d.]+)" fill="none" stroke="([^"]+)"'
)


def _erd_fan_database() -> dict:
    """Three tables into one parent, and one edge reaching past a whole layer.

    nvm-forecast's schema reduced to the shape that broke: `alpha` references
    both `mid` and `omega`, so that second edge has to cross the row `mid`
    sits in, and three separate tables point at `mid` at once. The padding
    tables are there because alphabetical placement is what put a box under
    that crossing edge in the first place — without something to sort past,
    the defect does not reproduce.
    """

    def column(name: str, fk: str | None = None, pk: bool = False) -> dict:
        return {"name": name, "type": "TEXT", "pk": pk, "fk": fk, "nullable": False}

    def table(name: str, columns: list[dict]) -> dict:
        return {"name": name, "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": columns}

    return {
        "name": "app",
        "sources": ["db.py"],
        "unread": [],
        "tables": [
            table("alpha", [column("id", pk=True), column("mid_id", fk="mid.id"),
                            column("omega_id", fk="omega.id")]),
            table("beta", [column("id", pk=True), column("mid_id", fk="mid.id")]),
            table("gamma", [column("id", pk=True), column("mid_id", fk="mid.id")]),
            table("mid", [column("id", pk=True), column("omega_id", fk="omega.id")]),
            table("pad_one", [column("id", pk=True)]),
            table("pad_two", [column("id", pk=True)]),
            table("omega", [column("id", pk=True)]),
        ],
    }


def test_erd_fans_edge_ends_so_parallel_arrows_do_not_stack() -> None:
    """Three foreign keys into one table must not land on one point.

    Attaching every edge at the middle of the box draws them on top of each
    other for the last stretch, which is the one stretch a reader follows to
    find out what points at what.
    """
    edges = _ERD_EDGE.findall(sr_svg.erd_svg(_erd_fan_database()))

    starts = [(edge[0], edge[1]) for edge in edges]
    ends = [(edge[2], edge[3]) for edge in edges]
    assert len(set(starts)) == len(starts), f"arrows leave one pixel: {starts}"
    assert len(set(ends)) == len(ends), f"arrows arrive at one pixel: {ends}"


def test_erd_drops_clear_of_a_tall_neighbour_before_travelling_sideways() -> None:
    """Boxes in a row share a top but not a bottom, so a short table's edge leaves
    higher up than a tall neighbour's bottom — and a curve starts moving sideways the
    moment it leaves the box. `log_hours -> people` was drawn under `events` that way:
    the horizontal run was already in the gutter between the rows, but the approach to
    it was not.
    """

    def column(name: str, fk: str | None = None, pk: bool = False) -> dict:
        return {"name": name, "type": "TEXT", "pk": pk, "fk": fk, "nullable": False}

    def table(name: str, columns: list[dict]) -> dict:
        return {"name": name, "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": columns}

    # Alphabetical placement puts the tall table on the left and the short one on the
    # right, and the table they point at sits on the left — so the short table's edge
    # has to travel back across the tall one to reach it.
    database = {
        "name": "app",
        "sources": ["db.py"],
        "unread": [],
        "tables": [
            table("a_tall", [column(f"column_number_{index}") for index in range(12)]),
            table("z_short", [column("id", pk=True), column("t", fk="target.id")]),
            table("target", [column("id", pk=True)]),
        ],
    }

    through, _crossings, _ink = srlib.svggeom._svg_geometry(sr_svg.erd_svg(database))

    assert through == 0


def test_erd_colours_each_edge_by_the_table_it_leaves() -> None:
    """One grey for every arrow is untraceable where two of them cross: there is
    nothing at the crossing to say which line is which. The box borders already
    carry a palette colour each — the edges take the colour of the table they
    leave, so a line can be traced back to its box."""
    svg = sr_svg.erd_svg(_erd_fan_database())
    colours = {edge[4] for edge in _ERD_EDGE.findall(svg)}

    assert len(colours) > 1
    assert colours <= set(sr_svg.PALETTE)


def test_erd_keeps_its_edges_off_the_table_boxes() -> None:
    """An edge reaching past a layer is drawn straight through whatever box sits
    between its ends, unless the row it crosses is ordered to leave a gap there
    and the edge is routed into it."""
    through, _crossings, _ink = srlib.svggeom._svg_geometry(
        sr_svg.erd_svg(_erd_fan_database())
    )

    assert through == 0


def test_text_clash_check_reads_a_centred_label_from_its_middle() -> None:
    """Every label these drawers print carries `text-anchor="middle"`, so `x` is the
    CENTRE of the string, not its left edge. Measured from the left, a label's box sits
    half a label to the right of the glyphs — which invents clashes between a long label
    and a short one well clear of it, and the invented ones are what a reader is then
    sent to go and fix."""
    svg = (
        '<svg width="400" height="200">'
        '<text x="100" y="100" text-anchor="middle" font-size="10">aaaaaaaaaaaaaaaaaaaa</text>'
        '<text x="210" y="100" text-anchor="middle" font-size="10">bb</text>'
        "</svg>"
    )

    assert srlib.svglint._svg_text_clashes(svg) == 0


def test_a_flowchart_edge_skipping_a_layer_is_routed_around_the_box() -> None:
    """A long edge past a middle row is a spline in dot — it never ploughs through
    the middle box, and the geometry linter reads the drawn splines without error."""
    text = (
        'digraph c {\n'
        '  top [label="top"]\n'
        '  middle [label="a very wide middle box that spans this row"]\n'
        '  bottom [label="bottom"]\n'
        '  top -> middle\n'
        '  middle -> bottom\n'
        '  top -> bottom [label="skips"]\n'
        '}\n'
    )
    svg, _unread = sr_mermaid.flow_svg(text)
    assert svg
    # the geometry reader sees graphviz splines and never crashes on them
    through, _crossings, _ink = srlib.svggeom._svg_geometry(svg)
    assert through >= 0
    assert srlib.svglint._svg_text_clashes(svg) >= 0


def test_geometry_never_counts_an_arrowhead_definition_as_an_edge() -> None:
    """Colouring edges by source needs one <marker> per palette colour, and every
    marker carries a <path> of its own. Swept into the edge count those twelve
    inflate the denominator of the crossings ratio: nvm-forecast's package diagram
    went from a reported `18 crossings over 18 edges (1.00 each)` to a silent 0.60
    the moment the markers were added, with the drawing unchanged."""
    svg = (
        '<svg width="400" height="400">'
        '<defs><marker id="m0"><path d="M0,0 L10,5 L0,10 z"/></marker>'
        '<marker id="m1"><path d="M0,0 L10,5 L0,10 z"/></marker></defs>'
        '<path d="M10,10 L100,100" fill="none" stroke="#000"/>'
        '<path d="M20,10 L120,100" fill="none" stroke="#000"/>'
        "</svg>"
    )

    assert len(srlib.svggeom._edges_of(svg)) == 2


def _packages_span_facts() -> dict:
    """A dependency reaching past a whole layer, with a package sitting under it.

    `alpha` imports both `mid` and `omega`, and `mid` imports `omega` too, so
    `omega` lands two layers down and alpha's second edge has to cross the row
    `mid` sits in. `pad` fills that row, because an edge is only drawn through a
    box when a box is in the way.
    """
    return {
        "alpha/a.py": _f("alpha/a.py", deps=["mid/m.py", "omega/o.py"]),
        "beta/b.py": _f("beta/b.py", deps=["mid/m.py"]),
        "mid/m.py": _f("mid/m.py", deps=["omega/o.py"]),
        "pad/p.py": _f("pad/p.py", deps=["omega/o.py"]),
        "omega/o.py": _f("omega/o.py"),
    }


def test_packages_colours_each_edge_by_the_package_it_leaves() -> None:
    """Same argument as the ERD drawer, and this picture needs it more: on
    nvm-forecast it drew 18 edges with 18 crossings, every one of them grey."""
    svg = srlib.svg.packages_svg(_packages_span_facts())
    colours = {edge[4] for edge in _ERD_EDGE.findall(svg)}

    assert len(colours) > 1
    assert colours <= set(sr_svg.PALETTE)


def test_packages_fans_edge_ends_so_parallel_imports_do_not_stack() -> None:
    """Three packages importing the same one drew their arrows onto a single pixel."""
    edges = _ERD_EDGE.findall(srlib.svg.packages_svg(_packages_span_facts()))

    ends = [(edge[2], edge[3]) for edge in edges]
    assert len(set(ends)) == len(ends), f"arrows arrive at one pixel: {ends}"


def test_packages_leaves_no_blank_row_between_the_layers_it_uses() -> None:
    """A cycle of three or more packages is not broken by the mutual-pair rule that
    keeps layering to a DAG, so relaxation drives every member to the ceiling layer.
    Every layer between the entry points and them then comes out empty, and the rows
    are still placed by layer INDEX: on nvm-forecast that was ~700px of blank paper
    with every edge stretched across it.

    A row is 94px, so any gap wider than one row pitch is paper nobody asked for.
    """
    facts = {
        "entry/e.py": _f("entry/e.py", deps=["ring/a.py"]),
        "ring/a.py": _f("ring/a.py", deps=["loop/b.py"]),
        "loop/b.py": _f("loop/b.py", deps=["cycle/c.py"]),
        "cycle/c.py": _f("cycle/c.py", deps=["ring/a.py"]),
    }
    svg = srlib.svg.packages_svg(facts)

    rows = sorted({float(y) for y in re.findall(r'<rect x="[\d.]+" y="([\d.]+)"', svg)})
    gaps = [second - first for first, second in zip(rows, rows[1:])]
    assert gaps, "expected more than one row"
    assert max(gaps) < 150, f"blank rows between the layers used: {rows}"


def test_packages_keeps_an_edge_inside_one_row_off_the_box_between_its_ends() -> None:
    """A cycle puts every member in ONE row, and an edge between two of them was drawn
    like an inter-row edge — out of the bottom of one box and up into the top of the
    other — so it arced across whatever package sat between. `frontend -> pages` went
    under `components` that way, in both directions.
    """
    facts = {
        "entry/e.py": _f("entry/e.py", deps=["ring/a.py"]),
        "ring/a.py": _f("ring/a.py", deps=["loop/b.py"]),
        "loop/b.py": _f("loop/b.py", deps=["cycle/c.py"]),
        "cycle/c.py": _f("cycle/c.py", deps=["ring/a.py"]),
    }

    through, _crossings, _ink = srlib.svggeom._svg_geometry(
        srlib.svg.packages_svg(facts)
    )

    assert through == 0


def test_packages_keeps_an_edge_that_skips_a_layer_off_the_boxes() -> None:
    """The layer a skipping edge crosses has a box in it, so the edge has to go round."""
    through, _crossings, _ink = srlib.svggeom._svg_geometry(
        srlib.svg.packages_svg(_packages_span_facts())
    )

    assert through == 0


# --------------------------------------------------------------------------
# Task 18: honest freshness — render never restamps a card it did not write
# --------------------------------------------------------------------------


def _one_function_repo(tmp_path, body="    return 1\n"):
    (tmp_path / "a.py").write_text(f"def one():\n{body}", encoding="utf-8")
    return tmp_path


def _hand_card(out, pseudocode="FUNCTION one() [L1-L2]\n    RETURN 1\nENDFUNCTION\n"):
    card = out / "files" / "a.py.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(
        "# a.py\n\n**Role.** One function.\n\n## Dependencies\n\n## Entities\n\n"
        "| Kind | Name | Signature | What it does |\n| --- | --- | --- | --- |\n"
        "| func | `one` | `()` | Returns a number. |\n\n"
        "## Pseudocode\n\n```\n" + pseudocode + "```\n",
        encoding="utf-8",
    )
    return card


# --------------------------------------------------------------------------
# Task 19: strict checks — what can be proven is no longer advisory
# --------------------------------------------------------------------------


FENCED_CARD = (
    "# a.py\n\n## Pseudocode\n\n```\nFUNCTION one() [L1-L2]\n    RETURN 1\nENDFUNCTION\n```\n"
)
UNFENCED_CARD = "# a.py\n\n## Pseudocode\n\nFUNCTION one() [L1-L2]\n    RETURN 1\nENDFUNCTION\n"


def test_render_exits_zero_when_every_strict_check_passes(tmp_path):
    root = _one_function_repo(tmp_path)
    out = root / "soulreap"
    sr.cmd_plan(root=root, out=out)
    _hand_card(out)
    assert sr.main(["render", "--root", str(root)]) == 0


# --------------------------------------------------------------------------
# Task 20: strict coverage — Entities and the call graph are transcription,
# so a short one is a truncation, never a judgement call
# --------------------------------------------------------------------------


@pytest.fixture
def coverage_repo(tmp_path):
    """Six callables wired so the skeleton prints a call graph (3+ edges, 6+ nodes)."""
    (tmp_path / "m.py").write_text(
        "TIMEOUT = 5\n\n\n"
        "def first(x):\n    return x + 1\n\n\n"
        "def second(x):\n    return x - 1\n\n\n"
        "def third(x):\n    return x * 2\n\n\n"
        "def fourth(x):\n    return x // 2\n\n\n"
        "def fifth(x):\n    return third(x) + fourth(x)\n\n\n"
        "def main():\n    return first(1) + second(2) + fifth(3)\n",
        encoding="utf-8",
    )
    return tmp_path


def _blob_of(repo, rel="m.py"):
    out = repo / "soulreap"
    sr.cmd_plan(root=repo, out=out)
    return json.loads((out / ".soulreap" / "facts.json").read_text())["files"][rel]


# --------------------------------------------------------------------------
# Task 21: markup pasted into a block (advisory — a heuristic, not a proof)
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Task 22: a heavy file is carded in slices, one task per slice
# --------------------------------------------------------------------------


def _module_of(functions):
    """functions: [(name, filler_lines)] → a module whose spans are predictable."""
    out = []
    for name, filler in functions:
        out.append(f"def {name}(x):")
        out.extend(["    x += 1"] * filler)
        out.append("    return x")
        out += ["", ""]
    return "\n".join(out)


def _blob_for(tmp_path, source, rel="big.py"):
    (tmp_path / rel).write_text(source, encoding="utf-8")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    return json.loads((out / ".soulreap" / "facts.json").read_text())["files"][rel]


def _heavy_functions(n):
    """n functions sized so the module exceeds the slice budget as a whole."""
    per = srlib.commands.SLICE_MAX_LINES // n
    return [(f"f{i}", per + i) for i in range(n)]


# --------------------------------------------------------------------------
# Task 23: slice skeletons and the merge that puts the parts back together
# --------------------------------------------------------------------------


@pytest.fixture
def heavy_repo(tmp_path):
    """A repo whose single module is heavy enough to be sliced."""
    source = "TIMEOUT = 5\nimport os\n\n\n" + _module_of(_heavy_functions(5))
    (tmp_path / "big.py").write_text(source, encoding="utf-8")
    sr.cmd_plan(root=tmp_path, out=tmp_path / "soulreap")
    return tmp_path


def _slice_ids(repo, rel="big.py"):
    plan = json.loads((repo / "soulreap" / ".soulreap" / "plan.json").read_text())
    entry = {f["path"]: f for f in plan["files"]}[rel]
    return [s["id"] for s in entry["slices"]]


def _write_part(repo, part, text, rel="big.py"):
    path = repo / "soulreap" / ".soulreap" / "parts" / rel / f"part-{part}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _pseudo_part(section, blocks):
    return f"### {section}\n\n```\n{blocks}```\n"


# --------------------------------------------------------------------------
# Task 24: a card nobody was asked to write cannot fail the run
# --------------------------------------------------------------------------


def _repo_with_a_stale_test_card(tmp_path):
    (tmp_path / "a.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text(
        "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n", encoding="utf-8"
    )
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    card = out / "files" / "tests" / "test_a.py.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(  # an old card: no Entities rows, blocks flush left
        "# tests/test_a.py\n\n**Role.** Old card.\n\n## Dependencies\n\n## Entities\n\n"
        "| Kind | Name | Signature | What it does |\n| --- | --- | --- |\n\n"
        "## Pseudocode\n\nFUNCTION test_one() [L1-L2]\n    ASSERT true\nENDFUNCTION\n",
        encoding="utf-8",
    )
    _hand_card(out)
    return tmp_path, out


# --------------------------------------------------------------------------
# Task 25: a slice's assignment is the name list, not the line span
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Task 26: measure what makes a crossing unreadable, not just that it exists
# --------------------------------------------------------------------------


def _two_edge_svg(colour_a: str, colour_b: str, crossing: bool = True) -> str:
    """Two drawn edges, crossing or parallel, in the given stroke colours."""
    second = "M100,10 L10,90" if crossing else "M150,10 L190,90"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        f'<path d="M10,10 L100,90" fill="none" stroke="{colour_a}" stroke-width="1.6"/>'
        f'<path d="{second}" fill="none" stroke="{colour_b}" stroke-width="1.6"/>'
        "</svg>"
    )


def test_a_crossing_between_near_hues_is_counted_as_confusable():
    """The measurement behind "I cannot tell which line goes where": at a crossing
    the only thing distinguishing two lines is colour, and nvm-forecast's ERD had
    green #0a7d33 crossing teal #0d6a6a — 39 degrees apart."""
    assert srlib.svggeom._confusable_crossings(_two_edge_svg("#0a7d33", "#0d6a6a")) == 1


def test_a_crossing_between_distant_hues_is_not_confusable():
    assert srlib.svggeom._confusable_crossings(_two_edge_svg("#0a7d33", "#a03030")) == 0


def test_edges_that_do_not_cross_are_never_confusable():
    assert srlib.svggeom._confusable_crossings(_two_edge_svg("#0a7d33", "#0d6a6a", crossing=False)) == 0


def test_the_geometry_lint_reports_confusable_crossings(tmp_path):
    diagrams = tmp_path / "diagrams"
    diagrams.mkdir()
    (diagrams / "erd-app.svg").write_text(_two_edge_svg("#0a7d33", "#0d6a6a"), encoding="utf-8")
    warnings = srlib.svglint._lint_svg_geometry(diagrams)
    assert any("same colour" in w for w in warnings), warnings


# --------------------------------------------------------------------------
# Task 27: colour by the graph, not by the alphabet
# --------------------------------------------------------------------------


def test_graph_colours_separate_neighbours():
    """The map-colouring rule: two boxes joined by an edge must not share a colour."""
    neighbours = {"a": ["b", "c"], "b": ["a"], "c": ["a"]}
    slots = sr_svg.graph_colours(["a", "b", "c"], neighbours)
    assert slots["a"] != slots["b"]
    assert slots["a"] != slots["c"]


def test_graph_colours_separate_tables_two_hops_apart():
    """Stricter than the map rule, and it is the one that matters here: two edges
    that MEET at a table are the pair a reader has to tell apart, so their sources
    need different colours even though those sources never touch each other."""
    neighbours = {"hub": ["b", "c"], "b": ["hub"], "c": ["hub"]}
    slots = sr_svg.graph_colours(["hub", "b", "c"], neighbours)
    assert slots["b"] != slots["c"]


def test_graph_colours_are_deterministic():
    neighbours = {"a": ["b"], "b": ["a", "c"], "c": ["b"]}
    first = sr_svg.graph_colours(["a", "b", "c"], neighbours)
    assert first == sr_svg.graph_colours(["a", "b", "c"], neighbours)


def test_graph_colours_survive_a_graph_bigger_than_the_palette():
    """A clique of twelve cannot be coloured with eight; the drawing still has to
    happen, so slots wrap rather than raising."""
    names = [f"t{i}" for i in range(12)]
    neighbours = {n: [m for m in names if m != n] for n in names}
    slots = sr_svg.graph_colours(names, neighbours)
    assert set(slots) == set(names)
    assert all(0 <= slot < len(sr_svg.PALETTE) for slot in slots.values())


def test_the_erd_draws_no_confusable_crossings():
    """End to end: the fan that used to put two near-hues across each other."""
    assert srlib.svggeom._confusable_crossings(sr_svg.erd_svg(_erd_fan_database())) == 0


# --------------------------------------------------------------------------
# Task 28: node placement — the phase the layered layout was missing
# --------------------------------------------------------------------------


def _erd_lonely_child_database() -> dict:
    """Four tables in a row, and one table below referencing the last of them.

    The shape that made nvm-forecast unreadable: the lone table was packed at the
    left edge while its parent sat 1100px away, so its edge crossed the whole page.
    """

    def column(name: str, fk: str | None = None, pk: bool = False) -> dict:
        return {"name": name, "type": "TEXT", "pk": pk, "fk": fk, "nullable": False}

    def table(name: str, columns: list[dict]) -> dict:
        return {"name": name, "line": 1, "end_line": 2, "detector": "sql_ddl", "columns": columns}

    return {
        "name": "app",
        "sources": ["db.py"],
        "unread": [],
        "tables": [
            table("aaa_first", [column("id", pk=True)]),
            table("bbb_second", [column("id", pk=True)]),
            table("ccc_third", [column("id", pk=True)]),
            table("ddd_fourth", [column("id", pk=True)]),
            table("zzz_child", [column("id", pk=True), column("parent_id", fk="ddd_fourth.id")]),
        ],
    }


def _erd_boxes(svg: str) -> list[tuple[float, float, float, float]]:
    return [
        tuple(float(g) for g in m.groups())
        for m in re.finditer(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg
        )
    ]


def _erd_named_boxes(svg: str) -> dict[str, tuple[float, float, float, float]]:
    """Table name -> its box, matched by the header text drawn inside it."""
    boxes = _erd_boxes(svg)
    texts = [
        (float(x), float(y), name)
        for x, y, name in re.findall(
            r'<text x="([\d.]+)" y="([\d.]+)"[^>]*>([a-z_]+)</text>', svg
        )
    ]
    named: dict[str, tuple[float, float, float, float]] = {}
    for box in boxes:
        inside = [
            (y, name)
            for x, y, name in texts
            if box[0] <= x <= box[0] + box[2] and box[1] <= y <= box[1] + box[3]
        ]
        if inside:
            named[min(inside)[1]] = box
    return named


def test_a_lone_table_is_placed_under_the_table_that_references_it():
    """Packing left to right put it at x=24 whatever referenced it. The layered
    algorithm has a whole phase for this — node placement — and we had skipped it,
    which is why nvm-forecast drew two lone tables in the left margin with their
    edges crossing 1100px of page."""
    named = _erd_named_boxes(sr_svg.erd_svg(_erd_lonely_child_database()))
    centre = lambda box: box[0] + box[2] / 2  # noqa: E731

    assert abs(centre(named["ddd_fourth"]) - centre(named["zzz_child"])) < 30, (
        f"referenced table at {centre(named['ddd_fourth']):.0f}, "
        f"the one referencing it at {centre(named['zzz_child']):.0f}"
    )


def test_placement_never_overlaps_boxes():
    svg = sr_svg.erd_svg(_erd_fan_database())
    rows: dict[float, list[tuple[float, float, float, float]]] = {}
    for box in _erd_boxes(svg):
        rows.setdefault(round(box[1]), []).append(box)
    for row in rows.values():
        ordered = sorted(row)
        for left, right in zip(ordered, ordered[1:]):
            assert left[0] + left[2] <= right[0] + 0.01, "boxes overlap"


def test_placement_keeps_the_order_crossing_minimisation_chose():
    """Moving a box past its neighbour would undo the barycentre pass that put it
    there: fewer crossings is worth more than a shorter edge."""
    database = _erd_fan_database()
    svg = sr_svg.erd_svg(database)
    drawn = [
        (round(float(y)), float(x), name)
        for x, y, name in re.findall(
            r'<text x="([\d.]+)" y="([\d.]+)"[^>]*>([a-z_]+)</text>', svg
        )
    ]
    heads = {}
    for row, x, name in sorted(drawn):
        heads.setdefault(row, []).append((x, name))
    top_row = heads[min(heads)]
    assert [name for _x, name in sorted(top_row)] == [name for _x, name in top_row], (
        "names are drawn in a different order than the boxes sit"
    )


# --------------------------------------------------------------------------
# Task 29: colour has to separate lines that MEET, not only lines that cross
# --------------------------------------------------------------------------


def test_every_pair_of_palette_colours_is_distinguishable():
    """Eight hues at 45 degrees looked safe because consecutive slots were far
    apart — but slots 3 and 5 landed 45 degrees from each other, and that is the
    pair that drew log_hours and shifts as two greens."""
    import colorsys

    hues = []
    for colour in sr_svg.PALETTE:
        red, green, blue = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
        hues.append(colorsys.rgb_to_hls(red, green, blue)[0] * 360)
    for first, second in itertools.combinations(hues, 2):
        delta = abs(first - second) % 360
        assert min(delta, 360 - delta) >= 55, f"{first:.0f}° and {second:.0f}° read alike"


def _converging_svg(colour_a: str, colour_b: str) -> str:
    """Two edges arriving at the same box from different directions."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200">'
        f'<path d="M20,20 L150,180" fill="none" stroke="{colour_a}" stroke-width="1.6"/>'
        f'<path d="M280,20 L155,180" fill="none" stroke="{colour_b}" stroke-width="1.6"/>'
        "</svg>"
    )


def test_two_edges_meeting_at_one_box_in_near_hues_are_confusable():
    """The case the first metric missed and a human caught: log_hours and shifts
    both point at people, arriving side by side in two greens. They never cross,
    so counting crossings alone reported a clean picture."""
    assert srlib.svggeom._confusable_crossings(_converging_svg("#12B970", "#31B912")) == 1


def test_two_edges_meeting_at_one_box_in_distant_hues_are_fine():
    assert srlib.svggeom._confusable_crossings(_converging_svg("#2E22DB", "#C21E13")) == 0


# --------------------------------------------------------------------------
# Task 30: the schema diagram goes through Graphviz when its wheel is present
# --------------------------------------------------------------------------


def test_the_dot_backend_draws_every_table_and_relationship():
    pytest.importorskip("pygraphviz")
    from srlib.erd_dot import erd_dot_svg

    svg = erd_dot_svg(_erd_fan_database())
    assert svg is not None
    for name in ("alpha", "mid", "omega"):
        assert name in svg, f"{name} is missing from the drawing"


def test_the_dot_backend_draws_every_column():
    """Folding to keys halved the ink and the canvas, and was rejected: a reader
    asked to see the fields, and the fields are what the arrows point at."""
    pytest.importorskip("pygraphviz")
    from srlib.erd_dot import erd_dot_svg

    table = {
        "name": "wide",
        "line": 1,
        "end_line": 2,
        "detector": "sql_ddl",
        "source": "app/models/wide.py",
        "columns": [{"name": "id", "type": "TEXT", "pk": True, "fk": None, "nullable": False}]
        + [
            {"name": f"plain_{i}", "type": "TEXT", "pk": False, "fk": None, "nullable": True}
            for i in range(6)
        ],
    }
    svg = erd_dot_svg({"name": "app", "sources": [], "unread": [], "tables": [table]})
    for index in range(6):
        assert f"plain_{index}" in svg, f"plain_{index} should be drawn"


def test_the_dot_backend_returns_none_without_graphviz(monkeypatch):
    """A platform with no wheel must degrade to our own drawer, not crash."""
    import builtins

    from srlib import erd_dot

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "pygraphviz":
            raise ImportError("no wheel here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert erd_dot.erd_dot_svg(_erd_fan_database()) is None


def test_render_falls_back_to_the_hand_written_drawer(schema_repo, monkeypatch):
    """The fallback is what keeps the skill installable everywhere."""
    monkeypatch.setattr(srlib.drawing, "_erd_dot_svg", lambda database: None)
    out = schema_repo / "soulreap"
    sr.cmd_plan(root=schema_repo, out=out)
    sr.cmd_render(root=schema_repo, out=out)
    drawn = list((out / "diagrams").glob("erd-*.svg"))
    assert drawn, "an ERD should still be drawn without graphviz"


# --------------------------------------------------------------------------
# Task 31: bends — the second aesthetic the research ranks, unmeasured till now
# --------------------------------------------------------------------------


def test_a_straight_edge_has_no_bends():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<path d="M10,10 L90,90" fill="none" stroke="#2E22DB"/></svg>'
    )
    assert srlib.svggeom._edge_bends(svg) == 0


def test_a_dog_leg_counts_its_corners():
    """Purchase's studies put crossings first and bends second; we were counting
    only the first. A path that turns twice costs a reader two bends."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
        '<path d="M10,10 L10,100 L150,100 L150,180" fill="none" stroke="#2E22DB"/></svg>'
    )
    assert srlib.svggeom._edge_bends(svg) == 2


def test_the_geometry_lint_reports_bends(tmp_path):
    diagrams = tmp_path / "diagrams"
    diagrams.mkdir()
    zigzag = " ".join(f"L{x},{(x // 20) % 2 * 100}" for x in range(20, 400, 20))
    (diagrams / "erd-app.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200">'
        f'<path d="M10,10 {zigzag}" fill="none" stroke="#2E22DB"/></svg>',
        encoding="utf-8",
    )
    assert any("bends" in w for w in srlib.svglint._lint_svg_geometry(diagrams))


# --------------------------------------------------------------------------
# Task 32: every table belongs to the file that declares it, and says so
# --------------------------------------------------------------------------


def test_group_databases_records_the_file_each_table_came_from():
    """The ERD draws one section per declaring module, so the drawing needs to know
    which file a table came from — the database's `sources` list says which files
    took part, never which table came from which."""
    facts = {
        "app/models/roster.py": {
            "schema": {
                "tables": [{"name": "people", "line": 1, "end_line": 5, "detector": "sqlalchemy", "columns": []}],
                "targets": [],
                "unread": [],
            }
        },
        "app/models/imports.py": {
            "schema": {
                "tables": [{"name": "log_hours", "line": 1, "end_line": 5, "detector": "sqlalchemy", "columns": []}],
                "targets": [],
                "unread": [],
            }
        },
    }
    databases = sr_diagrams.group_databases(facts)
    owners = {
        table["name"]: table.get("source")
        for database in databases.values()
        for table in database["tables"]
    }
    assert owners == {
        "people": "app/models/roster.py",
        "log_hours": "app/models/imports.py",
    }


def test_section_labels_are_file_names_shortened_to_the_shortest_unique_suffix():
    """`models.py` in three packages would otherwise draw three identical labels."""
    from srlib.erd_dot import section_labels

    assert section_labels(["app/billing/models.py", "app/auth/models.py", "app/models/roster.py"]) == {
        "app/billing/models.py": "billing/models.py",
        "app/auth/models.py": "auth/models.py",
        "app/models/roster.py": "roster.py",
    }


def test_the_erd_draws_one_section_per_declaring_file():
    pytest.importorskip("pygraphviz")
    from srlib.erd_dot import erd_dot_svg

    def column(name, fk=None, pk=False):
        return {"name": name, "type": "TEXT", "pk": pk, "fk": fk, "nullable": False}

    database = {
        "name": "app",
        "sources": ["app/models/roster.py", "app/models/imports.py"],
        "unread": [],
        "tables": [
            {"name": "people", "line": 1, "end_line": 4, "detector": "sqlalchemy",
             "source": "app/models/roster.py", "columns": [column("id", pk=True)]},
            {"name": "shifts", "line": 6, "end_line": 9, "detector": "sqlalchemy",
             "source": "app/models/roster.py",
             "columns": [column("id", pk=True), column("person_id", fk="people.id")]},
            {"name": "log_hours", "line": 1, "end_line": 4, "detector": "sqlalchemy",
             "source": "app/models/imports.py",
             "columns": [column("id", pk=True), column("person_id", fk="people.id")]},
        ],
    }
    svg = erd_dot_svg(database)
    assert "roster.py" in svg, "the section label should name the declaring file"
    assert "imports.py" in svg


# ------------------------------------------- entry points are not dead code ---


def _routes_source() -> str:
    """Big enough to clear CALL_GRAPH_MIN_EDGES/NODES — the graph is skipped below that."""
    return '''
from flask import Flask
app = Flask(__name__)

def _one(x):
    return x + 1

def _two(x):
    return x + 2

def _three(x):
    return x + 3

def orchestrate():
    return _one(1) + _two(2) + _three(3)

def also_orchestrate():
    return _one(9) + _two(8)

def validate_name(name):
    return bool(name)

@app.route("/api/venv-names", methods=["GET"])
def api_venv_names():
    return []

@app.route("/api/repo-names", methods=["GET"])
def api_repo_names():
    return []

@app.errorhandler(404)
def not_found(error):
    return "", 404
'''


def test_a_decorated_endpoint_is_not_listed_as_uncalled():
    """The complaint that found this: two dozen Flask routes sat under `NO CALLS`
    reading as dead code. Nothing in the file calls them — the framework does, and the
    decorator that says so is already in the facts."""
    facts = sr.extract_python(_routes_source(), "app.py").to_dict()
    text = "\n".join(srlib.callgraph._call_graph_lines(facts))
    dead = [line for line in text.splitlines() if line.startswith("NO CALLS")]
    assert dead, "the genuinely unreferenced function should still be reported"
    assert "api_venv_names" not in dead[0]
    assert "not_found" not in dead[0]
    assert "validate_name" in dead[0]


def test_the_endpoints_are_reported_under_their_own_heading():
    facts = sr.extract_python(_routes_source(), "app.py").to_dict()
    text = "\n".join(srlib.callgraph._call_graph_lines(facts))
    line = next(
        row for row in text.splitlines() if row.startswith("REACHED FROM OUTSIDE")
    )
    assert "api_venv_names" in line and "api_repo_names" in line and "not_found" in line
    assert "app.route" in line              # the decorator that reaches them is named


def test_the_dead_line_says_what_makes_it_dead():
    facts = sr.extract_python(_routes_source(), "app.py").to_dict()
    text = "\n".join(srlib.callgraph._call_graph_lines(facts))
    dead = next(row for row in text.splitlines() if row.startswith("NO CALLS"))
    assert "no decorator" in dead


def test_a_file_of_nothing_but_endpoints_reports_no_dead_code():
    source = '''
from flask import Flask
app = Flask(__name__)

def _one():
    return 1

def _two():
    return 2

def _three():
    return 3

def both():
    return _one() + _two()

def all_three():
    return _one() + _two() + _three()

@app.route("/a")
def route_a():
    return "a"

@app.route("/b")
def route_b():
    return "b"
'''
    facts = sr.extract_python(source, "app.py").to_dict()
    text = "\n".join(srlib.callgraph._call_graph_lines(facts))
    assert not [row for row in text.splitlines() if row.startswith("NO CALLS")]
    assert "REACHED FROM OUTSIDE" in text


