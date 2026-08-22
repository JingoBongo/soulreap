"""Tests for soulreap.py.

Run: uv run --with pytest --with pathspec --with tree-sitter-language-pack \
         pytest test_soulreap.py -q

The two extra --with flags are the script's own PEP 723 dependencies. `--with pytest`
alone does not pull them in, and two tests then fail on the fallback paths rather
than on the parsers they mean to exercise.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import soulreap as sr
import srlib.commands

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


def test_diff_statuses_marks_unknown_file_as_new():
    entries = [sr.FileEntry(path="a.py", sha="aaa", lines=1, kind="code", lang="python")]
    statuses = sr.diff_statuses(entries, manifest={"template_version": 1, "files": {}}, template_version=1)
    assert statuses["a.py"] == "new"


def test_diff_statuses_marks_changed_sha_as_dirty():
    entries = [sr.FileEntry(path="a.py", sha="bbb", lines=1, kind="code", lang="python")]
    manifest = {"template_version": 1, "files": {"a.py": {"source_sha": "aaa"}}}
    statuses = sr.diff_statuses(entries, manifest, template_version=1)
    assert statuses["a.py"] == "dirty"


def test_diff_statuses_marks_unchanged_sha_as_clean():
    entries = [sr.FileEntry(path="a.py", sha="aaa", lines=1, kind="code", lang="python")]
    manifest = {"template_version": 1, "files": {"a.py": {"source_sha": "aaa"}}}
    statuses = sr.diff_statuses(entries, manifest, template_version=1)
    assert statuses["a.py"] == "clean"


def test_diff_statuses_marks_vanished_file_as_orphan():
    manifest = {"template_version": 1, "files": {"gone.py": {"source_sha": "aaa"}}}
    statuses = sr.diff_statuses([], manifest, template_version=1)
    assert statuses["gone.py"] == "orphan"


def test_diff_statuses_dirties_everything_when_template_version_bumped():
    entries = [sr.FileEntry(path="a.py", sha="aaa", lines=1, kind="code", lang="python")]
    manifest = {"template_version": 1, "files": {"a.py": {"source_sha": "aaa"}}}
    statuses = sr.diff_statuses(entries, manifest, template_version=2)
    assert statuses["a.py"] == "dirty"


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


def test_plan_writes_plan_and_facts_json(sample_repo):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap", paths=[], since=None, force=False)
    plan = json.loads((sample_repo / "soulreap" / ".soulreap" / "plan.json").read_text())
    assert plan["counts"]["new"] == 2
    assert (sample_repo / "soulreap" / ".soulreap" / "facts.json").exists()


def test_plan_lists_non_code_inventory_by_category(sample_repo):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap", paths=[], since=None, force=False)
    plan = json.loads((sample_repo / "soulreap" / ".soulreap" / "plan.json").read_text())
    categories = plan["non_code"]
    assert ".github/workflows/ci.yml" in categories["ci"]
    assert "README.md" in categories["docs"]


def test_plan_reports_env_keys_without_values(sample_repo):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap", paths=[], since=None, force=False)
    raw = (sample_repo / "soulreap" / ".soulreap" / "plan.json").read_text()
    assert "SECRET_TOKEN" in raw
    assert "abc123" not in raw


def test_plan_groups_files_into_batches(sample_repo):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap", paths=[], since=None, force=False)
    plan = json.loads((sample_repo / "soulreap" / ".soulreap" / "plan.json").read_text())
    flat = [f for batch in plan["batches"] for f in batch]
    assert sorted(flat) == ["src/main.py", "src/util.py"]


def test_batches_pack_small_sibling_directories_together():
    paths = [f"pkg{i}/mod.py" for i in range(6)]
    batches = srlib.commands._batches(paths, {p: 20 for p in paths})
    assert len(batches) == 1


def test_batches_respect_the_file_cap():
    paths = [f"pkg/mod{i}.py" for i in range(13)]
    batches = srlib.commands._batches(paths, {p: 20 for p in paths})
    assert all(len(b) <= srlib.commands.BATCH_MAX_FILES for b in batches)
    assert sum(len(b) for b in batches) == 13


def test_batches_split_when_line_budget_exceeded():
    paths = ["pkg/big1.py", "pkg/big2.py"]
    batches = srlib.commands._batches(paths, {"pkg/big1.py": 1400, "pkg/big2.py": 1400})
    assert len(batches) == 2


def test_plan_second_run_with_cards_written_reports_clean(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    (out / "files" / "src").mkdir(parents=True)
    (out / "files" / "src" / "main.py.md").write_text("# src/main.py\n\n**Role.** Entry point.\n")
    (out / "files" / "src" / "util.py.md").write_text("# src/util.py\n\n**Role.** Helper.\n")
    sr.cmd_render(root=sample_repo, out=out)

    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    plan = json.loads((out / ".soulreap" / "plan.json").read_text())
    assert plan["counts"]["new"] == 0
    assert plan["counts"]["clean"] == 2


def test_plan_marks_file_as_new_when_its_card_is_missing(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    sr.cmd_render(root=sample_repo, out=out)  # records hashes, but no cards were written

    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)

    plan = json.loads((out / ".soulreap" / "plan.json").read_text())
    assert plan["counts"]["new"] == 2
    assert plan["counts"]["clean"] == 0


def test_plan_reports_a_written_card_as_carded_before_render(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    # A worker wrote one card; render has not run, so the manifest doesn't know it yet.
    (out / "files" / "src").mkdir(parents=True)
    (out / "files" / "src" / "main.py.md").write_text(
        "# src/main.py\n\n**Role.** Entry point.\n", encoding="utf-8"
    )
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)

    plan = json.loads((out / ".soulreap" / "plan.json").read_text(encoding="utf-8"))
    assert plan["counts"]["carded"] == 1
    carded = [f["path"] for f in plan["files"] if f["status"] == "carded"]
    assert "src/main.py" in carded
    # a carded file is done — it must not be re-delegated
    batched = {p for batch in plan["batches"] for p in batch}
    assert "src/main.py" not in batched


def test_plan_scoped_to_subpath_ignores_other_files(sample_repo):
    (sample_repo / "other").mkdir()
    (sample_repo / "other" / "thing.py").write_text("x = 1\n")
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap", paths=["src"], since=None, force=False)
    plan = json.loads((sample_repo / "soulreap" / ".soulreap" / "plan.json").read_text())
    carded = [f["path"] for f in plan["files"]]
    assert "other/thing.py" not in carded


def test_plan_builds_annotated_directory_tree(sample_repo):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap", paths=[], since=None, force=False)
    plan = json.loads((sample_repo / "soulreap" / ".soulreap" / "plan.json").read_text())
    assert "src" in plan["tree"]
    assert plan["tree"]["src"]["files"] == 2


# --------------------------------------------------------------------------
# Task 7: skeleton command
# --------------------------------------------------------------------------


def test_skeleton_output_contains_signatures_and_line_anchors(sample_repo, capsys):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap", paths=[], since=None, force=False)
    sr.cmd_skeleton(root=sample_repo, out=sample_repo / "soulreap", target="src/util.py")
    printed = capsys.readouterr().out
    assert "helper" in printed
    assert "L1" in printed


def test_skeleton_output_lists_reverse_dependencies(sample_repo, capsys):
    sr.cmd_plan(root=sample_repo, out=sample_repo / "soulreap", paths=[], since=None, force=False)
    sr.cmd_skeleton(root=sample_repo, out=sample_repo / "soulreap", target="src/util.py")
    printed = capsys.readouterr().out
    assert "src/main.py" in printed


# --------------------------------------------------------------------------
# Task 8: render command
# --------------------------------------------------------------------------


def test_render_writes_index_listing_every_card(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    (out / "files" / "src").mkdir(parents=True)
    (out / "files" / "src" / "main.py.md").write_text("# src/main.py\n\n**Role.** Entry point.\n")
    (out / "files" / "src" / "util.py.md").write_text("# src/util.py\n\n**Role.** Helper.\n")

    sr.cmd_render(root=sample_repo, out=out)

    index = (out / "INDEX.md").read_text()
    assert "src/main.py" in index
    assert "Entry point." in index


def test_render_writes_a_package_diagram_naming_the_only_package(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    sr.cmd_render(root=sample_repo, out=out)
    mermaid = (out / "diagrams" / "packages.mmd").read_text()
    assert "graph TD" in mermaid
    assert "src" in mermaid
    assert "isolated" in mermaid  # one package, nothing else to depend on


def test_render_updates_manifest_with_source_and_card_hashes(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    (out / "files" / "src").mkdir(parents=True)
    (out / "files" / "src" / "util.py.md").write_text("# src/util.py\n")

    sr.cmd_render(root=sample_repo, out=out)

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["template_version"] == sr.TEMPLATE_VERSION
    assert manifest["files"]["src/util.py"]["card_sha"] is not None
    assert manifest["files"]["src/main.py"]["card_sha"] is None


def test_render_flags_hand_edited_card_on_next_plan(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    (out / "files" / "src").mkdir(parents=True)
    card = out / "files" / "src" / "util.py.md"
    card.write_text("# src/util.py\n")
    sr.cmd_render(root=sample_repo, out=out)

    card.write_text("# src/util.py\n\nhand edited\n")
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)

    plan = json.loads((out / ".soulreap" / "plan.json").read_text())
    statuses = {f["path"]: f["status"] for f in plan["files"]}
    assert statuses["src/util.py"] == "edited"


def test_render_keeps_a_role_sentence_that_wraps_across_lines(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    (out / "files" / "src").mkdir(parents=True)
    (out / "files" / "src" / "util.py.md").write_text(
        "# src/util.py\n\n**Role.** Shared helper used by the API layer,\n"
        "wrapped onto a second line by the formatter.\n\n## Dependencies\n"
    )

    sr.cmd_render(root=sample_repo, out=out)

    index = (out / "INDEX.md").read_text()
    assert "wrapped onto a second line by the formatter." in index
    assert "## Dependencies" not in index


def test_render_collapses_graph_to_the_deepest_level_that_fits(sample_repo):
    out = sample_repo / "soulreap"
    for package in ("alpha", "beta", "gamma"):
        target = sample_repo / "src" / package
        target.mkdir(parents=True)
        for i in range(15):
            (target / f"mod{i}.py").write_text("from src.util import helper\n")

    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    sr.cmd_render(root=sample_repo, out=out)

    mermaid = (out / "diagrams" / "packages.mmd").read_text()
    assert "src/alpha" in mermaid  # not flattened to a single "src" node
    assert "mod7" not in mermaid  # but not one node per file either


def test_render_drops_self_edges_from_a_collapsed_graph(sample_repo):
    out = sample_repo / "soulreap"
    target = sample_repo / "src" / "alpha"
    target.mkdir(parents=True)
    for i in range(45):
        (target / f"mod{i}.py").write_text("from src.alpha.mod0 import thing\n")
    (target / "mod0.py").write_text("thing = 1\n")

    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    sr.cmd_render(root=sample_repo, out=out)

    mermaid = (out / "diagrams" / "packages.mmd").read_text()
    edges = [ln for ln in mermaid.splitlines() if "-->" in ln]
    assert all(ln.split("-->")[0].strip() != ln.split("-->")[1].strip() for ln in edges)


def test_render_collapses_large_graph_to_package_level(sample_repo):
    out = sample_repo / "soulreap"
    pkg = sample_repo / "big"
    pkg.mkdir()
    for i in range(45):
        (pkg / f"mod{i}.py").write_text("from big.mod0 import thing\n")
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    sr.cmd_render(root=sample_repo, out=out)
    mermaid = (out / "diagrams" / "packages.mmd").read_text()
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
    rendered = "\n".join(srlib.commands._entity_lines(blob))
    assert "async async" not in rendered
    assert "async function fetchAll" in rendered


def test_plan_since_ref_only_dirties_files_changed_in_git(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "stable.py").write_text("def stable():\n    return 1\n")
    (tmp_path / "src" / "moving.py").write_text("def moving():\n    return 1\n")
    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "test")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("branch", "-M", "main")
    git("checkout", "-qb", "feature")
    (tmp_path / "src" / "moving.py").write_text("def moving():\n    return 2\n")
    git("commit", "-qam", "change moving")

    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out, paths=[], since=None, force=False)
    for name in ("stable.py", "moving.py"):
        card = out / "files" / "src" / f"{name}.md"
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text(f"# src/{name}\n\n**Role.** Placeholder.\n")
    sr.cmd_render(root=tmp_path, out=out)

    (tmp_path / "src" / "stable.py").write_text("def stable():\n    return 99\n")  # uncommitted
    sr.cmd_plan(root=tmp_path, out=out, paths=[], since="main", force=False)

    plan = json.loads((out / ".soulreap" / "plan.json").read_text())
    statuses = {f["path"]: f["status"] for f in plan["files"]}
    assert statuses["src/moving.py"] == "dirty"
    assert statuses["src/stable.py"] == "clean"


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
    out, _ = srlib.commands._mermaid(facts)
    assert "tests/test_app.py" not in out
    assert '"tests' not in out
    assert "src" in out


def test_mermaid_excludes_test_named_file_outside_a_tests_directory():
    facts = _facts(**{
        "src/app.py": [], "src/db.py": ["src/app.py"], "test_wordpress.py": ["src/app.py"],
    })
    out, _ = srlib.commands._mermaid(facts)
    assert "test_wordpress.py" not in out


def test_mermaid_excludes_loose_root_scripts_when_packages_exist():
    facts = _facts(**{
        "src/app.py": [], "src/db.py": ["src/app.py"], "scratch_probe.py": ["src/app.py"],
    })
    out, _ = srlib.commands._mermaid(facts)
    assert "scratch_probe.py" not in out


def test_mermaid_keeps_root_files_when_the_repo_has_no_packages():
    facts = _facts(**{"app.py": [], "util.py": ["app.py"]})
    out, _ = srlib.commands._mermaid(facts)
    assert "(root)" in out


def test_mermaid_uses_top_down_layout():
    facts = _facts(**{"src/a.py": [], "src/b.py": ["src/a.py"]})
    assert "graph TD" in srlib.commands._mermaid(facts)[0]


def test_mermaid_leaves_the_tests_package_out_of_the_picture():
    paths = {f"src/mod{i}.py": [] for i in range(38)}
    paths.update({f"tests/test_mod{i}.py": [f"src/mod{i}.py"] for i in range(10)})
    out, stats = srlib.commands._mermaid(_facts(**paths))
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
    out, _ = srlib.commands._mermaid(_facts(**paths))
    assert "-.->" in out
    assert "not an import cycle" in out


def test_mermaid_keeps_a_real_file_level_cycle_solid():
    paths = {f"a/f{i}.py": [] for i in range(30)}
    paths.update({f"b/f{i}.py": [] for i in range(30)})
    paths["a/one.py"] = ["b/x.py"]
    paths["b/x.py"] = ["a/one.py"]
    out, _ = srlib.commands._mermaid(_facts(**paths))
    assert "-.->" not in out


def test_render_reports_graph_density(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    result = sr.cmd_render(root=sample_repo, out=out)
    assert "graph_nodes" in result
    assert "graph_edges" in result
    assert 0.0 <= result["graph_density"] <= 1.0


def test_render_also_writes_a_package_level_diagram(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    sr.cmd_render(root=sample_repo, out=out)
    packages = (out / "diagrams" / "packages.mmd").read_text()
    assert "graph TD" in packages
    assert "src/main.py" not in packages  # always grouped, even when per-file would fit
    assert "src" in packages


def test_package_diagram_groups_a_flat_repo_under_root(tmp_path):
    facts = _facts(**{"app.py": [], "util.py": ["app.py"]})
    out, stats = srlib.commands._mermaid(facts)
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
    out, _ = srlib.commands._context_mermaid(facts)
    assert "PostgreSQL" in out


def test_context_diagram_names_google_cloud_products_by_imported_name():
    facts = {
        "src/gcs.py": _f("src/gcs.py", imports=[("google.cloud", ["storage"])]),
        "src/q.py": _f("src/q.py", imports=[("google.cloud", ["tasks_v2"])]),
    }
    out, _ = srlib.commands._context_mermaid(facts)
    assert "Google Cloud Storage" in out
    assert "Google Cloud Tasks" in out


def test_context_diagram_lists_external_hosts_from_urls():
    facts = {"src/a.py": _f("src/a.py", io={"http": ["https://api.dataforseo.com/v3/serp"]}),
             "src/b.py": _f("src/b.py", deps=["src/a.py"])}
    out, _ = srlib.commands._context_mermaid(facts)
    assert "api.dataforseo.com" in out


def test_context_diagram_drops_local_and_boilerplate_hosts():
    facts = {
        "src/a.py": _f("src/a.py", io={"http": [
            "http://localhost:8001", "http://www.w3.org/2000/svg",
            "http://host.docker.internal:8001", "https://real.example.org/x",
        ]}),
        "src/b.py": _f("src/b.py", deps=["src/a.py"]),
    }
    out, _ = srlib.commands._context_mermaid(facts)
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
    out, _ = srlib.commands._context_mermaid(facts)
    assert "only-in-tests" not in out


def test_context_diagram_carries_a_title_and_a_legend():
    facts = {"src/db.py": _f("src/db.py", imports=[("asyncpg", [])]),
             "src/app.py": _f("src/app.py", deps=["src/db.py"])}
    out, _ = srlib.commands._context_mermaid(facts)
    assert "title:" in out
    assert "%%" in out


def test_context_diagram_states_when_nothing_external_is_reached():
    facts = {"src/a.py": _f("src/a.py"), "src/b.py": _f("src/b.py", deps=["src/a.py"])}
    out, stats = srlib.commands._context_mermaid(facts)
    assert stats["context_systems"] == 0
    assert "no external systems" in out.lower()


def test_context_diagram_strips_backticks_from_a_host_pulled_from_a_docstring():
    # A URL inside a markdown code span in a docstring yields a host carrying a
    # backtick; it must never reach a mermaid node label.
    facts = {"src/a.py": _f("src/a.py", io={"http": ["https://sys.md``", "https://sys.md"]}),
             "src/b.py": _f("src/b.py", deps=["src/a.py"])}
    out, _ = srlib.commands._context_mermaid(facts)
    assert "`" not in out
    assert "sys.md" in out  # the sanitized host still appears, once


def test_context_diagram_drops_evidence_that_is_not_a_real_hostname():
    facts = {"src/a.py": _f("src/a.py", io={"http": ["https://not_a_host", "https://also bad"]}),
             "src/b.py": _f("src/b.py", deps=["src/a.py"])}
    out, stats = srlib.commands._context_mermaid(facts)
    assert stats["context_systems"] == 0
    assert "not_a_host" not in out


def test_lint_mermaid_flags_semicolon_in_a_sequence_message(tmp_path):
    d = tmp_path / "diagrams"
    d.mkdir()
    (d / "flow.mmd").write_text(
        "sequenceDiagram\n  A->>B: normalize input; dedup on create\n", encoding="utf-8"
    )
    warnings = srlib.commands._lint_mermaid(tmp_path)
    assert any("flow.mmd" in w and ";" in w for w in warnings)


def test_lint_mermaid_flags_parens_in_a_flowchart_edge_label(tmp_path):
    d = tmp_path / "diagrams"
    d.mkdir()
    (d / "architecture.mmd").write_text(
        "graph TD\n  a -->|SOCKS5 + control (9050, 9051)| b\n", encoding="utf-8"
    )
    warnings = srlib.commands._lint_mermaid(tmp_path)
    assert any("architecture.mmd" in w and "edge label" in w for w in warnings)


def test_lint_mermaid_allows_parens_in_a_quoted_node_label(tmp_path):
    d = tmp_path / "diagrams"
    d.mkdir()
    # parens inside a quoted node label are valid mermaid — must NOT warn
    (d / "architecture.mmd").write_text(
        'graph TD\n  c["NVD, OSV, CISA KEV (+ EPSS)"]\n  a -->|HTTP| c\n', encoding="utf-8"
    )
    assert srlib.commands._lint_mermaid(tmp_path) == []


def test_lint_mermaid_flags_a_backtick_in_a_node_label(tmp_path):
    d = tmp_path / "diagrams"
    d.mkdir()
    (d / "context.mmd").write_text('graph LR\n  s0(["sys.md``"])\n', encoding="utf-8")
    warnings = srlib.commands._lint_mermaid(tmp_path)
    assert any("context.mmd" in w and "backtick" in w for w in warnings)


def test_lint_mermaid_is_silent_on_clean_diagrams(tmp_path):
    d = tmp_path / "diagrams"
    d.mkdir()
    (d / "flow.mmd").write_text(
        "sequenceDiagram\n  A->>B: normalize input, dedup on create\n", encoding="utf-8"
    )
    (d / "context.mmd").write_text('graph LR\n  s0(["api.example.org"])\n', encoding="utf-8")
    assert srlib.commands._lint_mermaid(tmp_path) == []


def test_render_serializes_under_a_lock(sample_repo):
    # The lock is a backstop against a second concurrent render; a normal render
    # acquires and releases it, leaving no stale lock behind.
    out = sample_repo / "soulreap"
    srlib.commands.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    srlib.commands.cmd_render(root=sample_repo, out=out)
    assert not (out / ".soulreap" / "render.lock").exists()


def test_package_diagram_carries_a_title_and_a_legend():
    facts = {"src/api/a.py": _f("src/api/a.py", deps=["src/core/b.py"]),
             "src/core/b.py": _f("src/core/b.py")}
    out, _ = srlib.commands._mermaid(facts)
    assert "title:" in out
    assert "%%" in out


def test_package_diagram_labels_edges_with_import_counts():
    facts = {
        "src/api/a.py": _f("src/api/a.py", deps=["src/core/b.py"]),
        "src/api/c.py": _f("src/api/c.py", deps=["src/core/b.py"]),
        "src/core/b.py": _f("src/core/b.py"),
    }
    out, _ = srlib.commands._mermaid(facts)
    assert "|2 imports|" in out


def test_package_diagram_marks_entry_and_foundation_roles():
    facts = {"src/api/a.py": _f("src/api/a.py", deps=["src/core/b.py"]),
             "src/core/b.py": _f("src/core/b.py")}
    out, _ = srlib.commands._mermaid(facts)
    assert "entry" in out
    assert "foundation" in out


def test_render_writes_context_and_packages_but_not_the_per_file_hairball(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    sr.cmd_render(root=sample_repo, out=out)
    assert (out / "diagrams" / "context.mmd").exists()
    assert (out / "diagrams" / "packages.mmd").exists()
    assert not (out / "diagrams" / "modules.mmd").exists()


def test_package_diagram_reports_role_counts():
    facts = {"src/api/a.py": _f("src/api/a.py", deps=["src/core/b.py"]),
             "src/core/b.py": _f("src/core/b.py")}
    _, stats = srlib.commands._mermaid(facts)
    assert stats["graph_roles"]["entry"] == 1
    assert stats["graph_roles"]["foundation"] == 1


def test_package_diagram_reports_no_layering_when_everything_is_mutual():
    facts = {"a/x.py": _f("a/x.py", deps=["b/y.py"]), "b/y.py": _f("b/y.py", deps=["a/x.py"])}
    _, stats = srlib.commands._mermaid(facts)
    assert stats["graph_roles"]["entry"] == 0
    assert stats["graph_roles"]["foundation"] == 0


def test_render_reports_a_missing_flow_diagram(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    result = sr.cmd_render(root=sample_repo, out=out)
    assert result["flow_present"] is False


def test_render_notices_a_hand_written_flow_diagram_and_leaves_it_alone(sample_repo):
    out = sample_repo / "soulreap"
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    flow = out / "diagrams" / "flow.mmd"
    flow.parent.mkdir(parents=True, exist_ok=True)
    flow.write_text("sequenceDiagram\n    A->>B: hand written\n")

    result = sr.cmd_render(root=sample_repo, out=out)

    assert result["flow_present"] is True
    assert "hand written" in flow.read_text()


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
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    (out / "files" / "src").mkdir(parents=True, exist_ok=True)
    (out / "files" / "src" / "main.py.md").write_text("# src/main.py\n\n**Role.** Entry point.\n")
    (out / "files" / "src" / "util.py.md").write_text("# src/util.py\n\n**Role.** Helper.\n")
    return out


def test_render_adds_a_source_link_to_a_card_that_lacks_one(sample_repo):
    out = _carded(sample_repo)
    sr.cmd_render(root=sample_repo, out=out)
    card = (out / "files" / "src" / "main.py.md").read_text()
    assert "[source](../../../src/main.py)" in card


def test_the_source_link_actually_resolves_to_the_file(sample_repo):
    out = _carded(sample_repo)
    sr.cmd_render(root=sample_repo, out=out)
    card_path = out / "files" / "src" / "main.py.md"
    target = re.search(r"\[source\]\(([^)]+)\)", card_path.read_text()).group(1)
    assert (card_path.parent / target).resolve() == (sample_repo / "src" / "main.py").resolve()


def test_source_link_for_a_repo_root_file(tmp_path):
    (tmp_path / "probe.py").write_text("x = 1\n")
    (tmp_path / "other.py").write_text("import probe\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out, paths=[], since=None, force=False)
    (out / "files").mkdir(parents=True, exist_ok=True)
    (out / "files" / "probe.py.md").write_text("# probe.py\n\n**Role.** A probe.\n")
    sr.cmd_render(root=tmp_path, out=out)
    assert "[source](../../probe.py)" in (out / "files" / "probe.py.md").read_text()


def test_rendering_twice_does_not_duplicate_the_source_link(sample_repo):
    out = _carded(sample_repo)
    sr.cmd_render(root=sample_repo, out=out)
    sr.cmd_render(root=sample_repo, out=out)
    card = (out / "files" / "src" / "main.py.md").read_text()
    assert card.count("[source](") == 1


def test_render_repairs_a_stale_source_link(sample_repo):
    out = _carded(sample_repo)
    (out / "files" / "src" / "main.py.md").write_text(
        "# src/main.py\n\n[source](../../../wrong/place.py) · stale\n\n**Role.** Entry point.\n"
    )
    sr.cmd_render(root=sample_repo, out=out)
    card = (out / "files" / "src" / "main.py.md").read_text()
    assert "wrong/place.py" not in card
    assert "[source](../../../src/main.py)" in card


def test_index_links_both_the_card_and_the_source(sample_repo):
    out = _carded(sample_repo)
    sr.cmd_render(root=sample_repo, out=out)
    index = (out / "INDEX.md").read_text()
    assert "(files/src/main.py.md)" in index
    assert "(../src/main.py)" in index


def test_manifest_card_hash_matches_the_card_after_the_link_is_injected(sample_repo):
    out = _carded(sample_repo)
    sr.cmd_render(root=sample_repo, out=out)
    manifest = json.loads((out / "manifest.json").read_text())
    recorded = manifest["files"]["src/main.py"]["card_sha"]
    assert recorded == sr.sha_of(out / "files" / "src" / "main.py.md")


def test_injecting_the_link_does_not_make_the_card_look_hand_edited(sample_repo):
    out = _carded(sample_repo)
    sr.cmd_render(root=sample_repo, out=out)
    sr.cmd_plan(root=sample_repo, out=out, paths=[], since=None, force=False)
    plan = json.loads((out / ".soulreap" / "plan.json").read_text())
    statuses = {f["path"]: f["status"] for f in plan["files"]}
    assert statuses["src/main.py"] == "clean"


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


def test_render_writes_the_dependencies_section_from_facts(probe_repo):
    out = probe_repo / "soulreap"
    sr.cmd_plan(root=probe_repo, out=out, paths=[], since=None, force=False)
    card = out / "files" / "src" / "main.py.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text("# src/main.py\n\n**Role.** Entry point.\n\n## Entities\n\n(none)\n")

    sr.cmd_render(root=probe_repo, out=out)

    text = card.read_text()
    assert "**Standard library:** `json`" in text
    assert "**Third-party:** `httpx`" in text
    assert "src/util.py" in text.split("## Entities")[0]


def test_render_replaces_a_dependencies_section_the_model_got_wrong(probe_repo):
    out = probe_repo / "soulreap"
    sr.cmd_plan(root=probe_repo, out=out, paths=[], since=None, force=False)
    card = out / "files" / "src" / "main.py.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(
        "# src/main.py\n\n**Role.** Entry point.\n\n## Dependencies\n\n"
        "- **External:** none\n- **Internal:** none\n\n## Entities\n\n(none)\n"
    )

    sr.cmd_render(root=probe_repo, out=out)

    text = card.read_text()
    assert "**External:** none" not in text
    assert "`json`" in text
    assert text.count("## Dependencies") == 1


def test_render_reports_who_imports_the_file(probe_repo):
    out = probe_repo / "soulreap"
    sr.cmd_plan(root=probe_repo, out=out, paths=[], since=None, force=False)
    card = out / "files" / "src" / "util.py.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text("# src/util.py\n\n**Role.** Helper.\n\n## Entities\n\n(none)\n")

    sr.cmd_render(root=probe_repo, out=out)

    assert "src/main.py" in card.read_text()


def test_rendering_twice_leaves_the_dependencies_section_unchanged(probe_repo):
    out = probe_repo / "soulreap"
    sr.cmd_plan(root=probe_repo, out=out, paths=[], since=None, force=False)
    card = out / "files" / "src" / "main.py.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text("# src/main.py\n\n**Role.** Entry point.\n\n## Entities\n\n(none)\n")
    sr.cmd_render(root=probe_repo, out=out)
    once = card.read_text()
    sr.cmd_render(root=probe_repo, out=out)
    assert card.read_text() == once


def test_internal_dependencies_get_one_line_each(probe_repo):
    (probe_repo / "src" / "extra.py").write_text("VALUE = 1\n")
    (probe_repo / "src" / "main.py").write_text(
        "from src.util import helper\nfrom src.extra import VALUE\n\ndef run():\n    return helper(), VALUE\n"
    )
    out = probe_repo / "soulreap"
    sr.cmd_plan(root=probe_repo, out=out, paths=[], since=None, force=False)
    card = out / "files" / "src" / "main.py.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text("# src/main.py\n\n**Role.** Entry.\n\n## Entities\n\n(none)\n")

    sr.cmd_render(root=probe_repo, out=out)

    block = card.read_text().split("## Dependencies")[1].split("## Entities")[0]
    internal = [ln for ln in block.splitlines() if ln.strip().startswith("- [src/")]
    assert len(internal) == 2
