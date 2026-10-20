"""Tests for the context question: what does this repository talk to, and how do we know.

Run: uv run --with pytest --with pygraphviz --with pathspec \
         --with tree-sitter-language-pack pytest test_context.py -q

Kept out of test_soulreap.py on purpose — the context detectors are a separate
source of facts from the per-file parser, and mixing them into a 5000-line file
makes it impossible to tell which half a failure belongs to.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import soulreap as sr
import srlib.context as ctx
from srlib.diagrams import context_dot
from srlib.walk import classify, walk


def _f(path, deps=(), imports=(), io=None):
    return {
        "lang": "python", "lines": 10, "deps": list(deps),
        "imports": [{"module": m, "names": list(n)} for m, n in imports],
        "io": io or {},
    }


# --------------------------------------------------------------- 1. manifest ---


def test_classify_calls_every_dependency_manifest_format_a_manifest():
    for name in (
        "requirements.txt", "requirements-dev.txt", "pyproject.toml",
        "package.json", "go.mod", "Cargo.toml", "Gemfile",
    ):
        kind, _lang, category = classify(Path(name))
        assert (kind, category) == ("non_code", "manifest"), name


def test_classify_calls_a_prefixed_requirements_file_a_manifest():
    """fuse_core ships `fuse_requirements.txt`; it was landing in `docs`."""
    kind, _lang, category = classify(Path("fuse_requirements.txt"))
    assert (kind, category) == ("non_code", "manifest")


def test_classify_still_calls_an_ordinary_config_a_config():
    """Only the six manifest formats move; a neighbouring .toml or .json does not."""
    for name in ("settings.toml", "tsconfig.json", "pytest.ini"):
        _kind, _lang, category = classify(Path(name))
        assert category == "config", name


def test_plan_lists_manifests_in_their_own_non_code_category(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "requirements.txt").write_text("flask\n")
    sr.cmd_plan(root=tmp_path, out=tmp_path / "soulreap")
    plan = json.loads((tmp_path / "soulreap" / ".soulreap" / "plan.json").read_text())
    assert plan["non_code"]["manifest"] == ["requirements.txt"]


# -------------------------------------------------------------- 2. worktrees ---


def test_walk_skips_a_git_worktree_checkout(tmp_path):
    """A worktree is a second checkout of the same repo: every file it holds is a
    duplicate of one already walked, and nvm-forecast went from 99 files to 198."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "app.py").write_text("x = 1\n")
    tree = tmp_path / ".claude" / "worktrees" / "feature" / "backend"
    tree.mkdir(parents=True)
    (tree / "app.py").write_text("x = 1\n")
    paths = {e.path for e in walk(tmp_path)}
    assert paths == {"backend/app.py"}


def test_walk_skips_a_worktrees_directory_anywhere(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    nested = tmp_path / "var" / "worktrees" / "wip"
    nested.mkdir(parents=True)
    (nested / "b.py").write_text("x = 2\n")
    paths = {e.path for e in walk(tmp_path)}
    assert paths == {"a.py"}


# --------------------------------------------------------------- 3. manifest ---


def _collect(tmp_path):
    return ctx.collect(tmp_path, walk(tmp_path))


def _systems(evidence):
    return {e["system"] for e in evidence}


def test_a_python_requirement_names_the_database_behind_it(tmp_path):
    (tmp_path / "requirements.txt").write_text("psycopg2-binary==2.9.9\nflask\n")
    found = _collect(tmp_path)
    assert ("PostgreSQL", "manifest") in {(e["system"], e["via"]) for e in found}


def test_a_requirement_with_extras_and_a_specifier_is_still_matched(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["psycopg[binary]>=3.2", "sqlalchemy>=2.0"]\n'
    )
    assert "PostgreSQL" in _systems(_collect(tmp_path))


def test_poetry_style_dependencies_are_read_too(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry.dependencies]\npython = "^3.12"\nredis = "^5.0"\n'
    )
    assert "Redis" in _systems(_collect(tmp_path))


def test_a_node_dependency_names_its_system(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"ioredis": "^5", "react": "^19"},
                    "devDependencies": {"@aws-sdk/client-s3": "^3"}})
    )
    assert {"Redis", "AWS"} <= _systems(_collect(tmp_path))


def test_a_go_module_requirement_names_its_system(tmp_path):
    (tmp_path / "go.mod").write_text(
        "module example.com/x\n\ngo 1.22\n\nrequire (\n\tgithub.com/jackc/pgx/v5 v5.5.0\n)\n"
    )
    assert "PostgreSQL" in _systems(_collect(tmp_path))


def test_a_cargo_dependency_names_its_system(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[dependencies]\ntokio-postgres = "0.7"\n')
    assert "PostgreSQL" in _systems(_collect(tmp_path))


def test_a_gemfile_dependency_names_its_system(tmp_path):
    (tmp_path / "Gemfile").write_text('source "https://rubygems.org"\ngem "redis", "~> 5.0"\n')
    assert "Redis" in _systems(_collect(tmp_path))


def test_a_vcs_requirement_names_the_host_it_pulls_code_from(tmp_path):
    """The node is the machine, not the package.

    `vaults` is a library that runs inside this process — the reader does not "talk
    to" it — while `192.168.0.4:9999` is a box on the LAN that has to be up for the
    build to work, and nothing else in the repository names it.
    """
    (tmp_path / "fuse_requirements.txt").write_text(
        "flask\ngit+http://192.168.0.4:9999/mscebec/vaults#egg=vaults\n"
    )
    found = _collect(tmp_path)
    assert ("192.168.0.4:9999", "manifest") in {(e["system"], e["via"]) for e in found}
    assert "vaults" not in _systems(found)


def test_a_vcs_requirement_from_a_public_forge_adds_no_node(tmp_path):
    """A node every repository would have is noise, like `rmdir` was."""
    (tmp_path / "requirements.txt").write_text(
        "git+https://github.com/psf/requests@main#egg=requests\n"
    )
    assert _systems(_collect(tmp_path)) == set()


def test_credentials_in_a_vcs_url_never_reach_the_node_name(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "git+https://mscebec:ghp_secret@192.168.0.4:9999/x/vaults#egg=vaults\n"
    )
    found = _collect(tmp_path)
    assert _systems(found) == {"192.168.0.4:9999"}
    assert all("ghp_secret" not in json.dumps(e) for e in found)


def test_psutil_names_the_host_it_reads(tmp_path):
    """fuse_core's whole os_utils is cpu, memory, disk and pids of the machine."""
    (tmp_path / "requirements.txt").write_text("flask\npsutil\npyyaml\n")
    assert _systems(_collect(tmp_path)) == {"host OS"}


def test_an_unknown_package_is_not_invented_into_a_system(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\npyyaml\nrich\n")
    assert _systems(_collect(tmp_path)) == set()


# ---------------------------------------------------------- 4. config keys ---


def test_a_url_shaped_config_key_is_an_external_system(tmp_path):
    (tmp_path / ".env.example").write_text("FUSE_SELF_REPO_URL=\nLOG_LEVEL=info\n")
    found = _collect(tmp_path)
    assert [(e["system"], e["via"]) for e in found] == [("Fuse self repo", "config")]


def test_every_named_key_suffix_is_recognised(tmp_path):
    (tmp_path / ".env.example").write_text(
        "A_URL=\nB_URI=\nC_DSN=\nD_ENDPOINT=\nE_HOST=\n"
    )
    assert len(_collect(tmp_path)) == 5


def test_a_key_that_merely_contains_host_is_not_a_system(tmp_path):
    """`KC_HOSTNAME` and `HOSTILE_TAKEOVER` end in neither of the five suffixes."""
    (tmp_path / ".env.example").write_text("KC_HOSTNAME=\nHOSTILE_TAKEOVER=\nURL=\n")
    assert _collect(tmp_path) == []


def test_a_key_whose_name_is_a_known_system_uses_that_systems_name(tmp_path):
    """`REDIS_URL` says Redis in as many words; naming the node "Redis" lets it
    merge with the same system found in the manifest instead of drawing twice."""
    (tmp_path / ".env.example").write_text("APP_REDIS_URL=\n")
    assert _systems(_collect(tmp_path)) == {"Redis"}


def test_config_keys_are_read_from_a_yaml_config_too(tmp_path):
    (tmp_path / "config.yaml").write_text("server:\n  host: 0.0.0.0\nafas_proxy_url: x\n")
    assert _systems(_collect(tmp_path)) == {"Afas proxy"}


def test_a_config_key_never_carries_its_value(tmp_path):
    (tmp_path / ".env.example").write_text("PAYMENTS_ENDPOINT=https://secret.internal/abc\n")
    assert "secret.internal" not in json.dumps(_collect(tmp_path))


# ------------------------------------------------------------ 5. literals ---


def test_a_loopback_url_is_a_process_on_this_host():
    """fuse_core polls `http://localhost:{port}/health` on every service it starts.

    The host is denied as a NAME — "localhost" on a picture names nothing — but the
    fact is real and it is half of what this service does, so it gets the one node
    the evidence supports: something else on this machine, over HTTP.
    """
    facts = {
        "fuse.py": _f("fuse.py", io={"http": ["http://localhost:{port}/health"]}),
        "cli.py": _f("cli.py", deps=["fuse.py"]),
    }
    _out, stats = context_dot(facts)
    assert stats["context_systems"] == 1
    assert stats["context_sources"] == {"literal": 1}


def test_a_loopback_node_is_not_named_after_the_loopback_host():
    facts = {
        "a.py": _f("a.py", io={"http": ["http://127.0.0.1:8080/health"]}),
        "b.py": _f("b.py", deps=["a.py"]),
    }
    out, _stats = context_dot(facts)
    assert "127.0.0.1" not in out and "localhost" not in out
    assert "local process" in out


def test_a_documentation_link_is_still_not_a_system():
    """The deny list keeps doing its job — only loopback gets the new node."""
    facts = {
        "a.py": _f("a.py", io={"http": ["https://docs.python.org/3/library/os.html"]}),
        "b.py": _f("b.py", deps=["a.py"]),
    }
    _out, stats = context_dot(facts)
    assert stats["context_systems"] == 0


# ------------------------------------------------------------- 6. compose ---


def test_a_compose_image_names_the_service_behind_it(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  db:\n    image: postgres:17-alpine\n"
        "  cache:\n    image: redis:7\n"
    )
    found = _collect(tmp_path)
    assert {(e["system"], e["via"]) for e in found} == {
        ("PostgreSQL", "compose"), ("Redis", "compose")
    }


def test_a_compose_service_built_from_this_repo_is_not_external(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  api:\n    build:\n      context: ./backend\n"
        "  db:\n    image: postgres:16\n"
    )
    assert _systems(_collect(tmp_path)) == {"PostgreSQL"}


def test_a_language_runtime_image_is_not_an_external_system(tmp_path):
    """`node:22-alpine` bind-mounting ./frontend runs this repo's own code."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: node:22-alpine\n"
    )
    assert _collect(tmp_path) == []


def test_a_registry_qualified_image_is_named_by_its_last_segment(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  auth:\n    image: quay.io/keycloak/keycloak:26.0\n"
    )
    assert _systems(_collect(tmp_path)) == {"Keycloak"}


def test_an_unknown_image_is_still_a_neighbour_worth_naming(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  afas:\n    image: whatsnext/afas-proxy:1.4\n"
    )
    assert _systems(_collect(tmp_path)) == {"afas-proxy"}


# ------------------------------------------------------------ 7. assembly ---


def test_an_edge_says_how_the_system_was_found():
    facts = {"src/a.py": _f("src/a.py"), "src/b.py": _f("src/b.py", deps=["src/a.py"])}
    evidence = [{"system": "PostgreSQL", "kind": "database", "via": "manifest",
                 "where": "requirements.txt", "detail": "psycopg"}]
    out, _stats = context_dot(facts, evidence)
    assert "manifest" in out
    assert "PostgreSQL" in out


def test_two_sources_agreeing_draw_one_edge_naming_both():
    facts = {"a.py": _f("a.py", imports=[("asyncpg", [])]), "b.py": _f("b.py", deps=["a.py"])}
    evidence = [{"system": "PostgreSQL", "kind": "database", "via": "manifest",
                 "where": "requirements.txt", "detail": "asyncpg"}]
    out, stats = context_dot(facts, evidence)
    assert stats["context_systems"] == 1
    assert stats["context_edges"] == 1
    assert "import" in out and "manifest" in out


def test_every_edge_records_which_source_found_it():
    facts = {"a.py": _f("a.py", io={"http": ["https://api.real-example.net/v1"]}),
             "b.py": _f("b.py", deps=["a.py"])}
    evidence = [{"system": "Redis", "kind": "cache", "via": "compose",
                 "where": "docker-compose.yml", "detail": "redis:7"}]
    _out, stats = context_dot(facts, evidence)
    assert stats["context_sources"] == {"compose": 1, "literal": 1}


def test_an_edge_label_carries_no_parentheses():
    """`_lint_mermaid` rejects them: mermaid fails to parse parens between |...|."""
    facts = {"a.py": _f("a.py"), "b.py": _f("b.py", deps=["a.py"])}
    evidence = [{"system": "vaults", "kind": "git dependency", "via": "manifest",
                 "where": "fuse_requirements.txt", "detail": "vaults"}]
    out, _stats = context_dot(facts, evidence)
    for line in out.splitlines():
        if line.count("|") >= 2:
            label = line[line.find("|") + 1 : line.rfind("|")]
            assert "(" not in label and ")" not in label


def test_a_generic_sql_node_from_a_config_key_yields_to_a_named_driver():
    facts = {"a.py": _f("a.py"), "b.py": _f("b.py", deps=["a.py"])}
    evidence = [
        {"system": "SQL database", "kind": "database", "via": "config",
         "where": ".env.example", "detail": "DATABASE_URL"},
        {"system": "PostgreSQL", "kind": "database", "via": "manifest",
         "where": "pyproject.toml", "detail": "psycopg"},
    ]
    out, stats = context_dot(facts, evidence)
    assert stats["context_systems"] == 1
    assert "SQL database" not in out


# -------------------------------------------------------------- 8. silence ---


def test_the_diagram_says_which_five_sources_came_back_empty():
    facts = {"a.py": _f("a.py"), "b.py": _f("b.py", deps=["a.py"])}
    _out, stats = context_dot(facts, [])
    assert stats["context_systems"] == 0
    note = stats["context_note"]
    for source in ("manifest", "config", "compose", "import", "URL"):
        assert source in note


def test_render_writes_no_context_picture_when_nothing_was_found(tmp_path):
    """A box reading "No external systems detected" looks like an answer. It is not
    one: every address in both dogfood repos arrives through a config value."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "src" / "b.py").write_text("from src.a import x\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    stats = sr.cmd_render(root=tmp_path, out=out)
    assert not (out / "diagrams" / "context.dot").exists()
    assert not (out / "diagrams" / "context.svg").exists()
    assert "no external systems" in stats["context_note"].lower()


def test_render_removes_a_context_picture_left_by_an_earlier_run(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "src" / "b.py").write_text("from src.a import x\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    (out / "diagrams").mkdir(parents=True, exist_ok=True)
    (out / "diagrams" / "context.dot").write_text("graph LR\n", encoding="utf-8")
    (out / "diagrams" / "context.svg").write_text("<svg/>", encoding="utf-8")
    sr.cmd_render(root=tmp_path, out=out)
    assert not (out / "diagrams" / "context.dot").exists()
    assert not (out / "diagrams" / "context.svg").exists()


def test_render_draws_the_context_again_once_a_manifest_names_a_system(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "src" / "b.py").write_text("from src.a import x\n")
    (tmp_path / "requirements.txt").write_text("asyncpg\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    stats = sr.cmd_render(root=tmp_path, out=out)
    drawn = (out / "diagrams" / "context.dot").read_text(encoding="utf-8")
    assert "PostgreSQL" in drawn and "manifest" in drawn
    assert stats["context_systems"] == 1
    assert "context_note" not in stats


def test_plan_records_what_it_searched_for_external_systems(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "requirements.txt").write_text("redis\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    plan = json.loads((out / ".soulreap" / "plan.json").read_text())
    assert plan["context"]["searched"]["manifest"] == ["requirements.txt"]
    assert {e["system"] for e in plan["context"]["found"]} == {"Redis"}


def test_the_evidence_survives_the_round_trip_through_facts_json(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "requirements.txt").write_text("redis\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    stored = json.loads((out / ".soulreap" / "facts.json").read_text(encoding="utf-8"))
    assert stored["context"][0]["system"] == "Redis"


def test_render_draws_the_context_svg_from_the_same_sources_as_the_mmd(tmp_path):
    """The guard these two tests used to describe — skip the picture when it would
    contradict the text — is gone because the reason for it is gone: both come out of
    one set of edges now. What is left to check is that a system found ONLY by a source
    the old drawer could not see still reaches the picture."""
    pytest.importorskip("pygraphviz")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "src" / "b.py").write_text("from src.a import x\n")
    (tmp_path / "requirements.txt").write_text("asyncpg\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    stats = sr.cmd_render(root=tmp_path, out=out)

    assert (out / "diagrams" / "context.dot").exists()
    assert (out / "diagrams" / "context.svg").exists()
    drawn = (out / "diagrams" / "context.svg").read_text(encoding="utf-8")
    assert "PostgreSQL" in drawn, "the manifest-only system has to be in the picture too"
    assert "manifest" in drawn, "and the picture says how it was found"
    assert not any("context.svg" in w for w in stats["svg_warnings"])


def test_a_config_key_naming_our_own_compose_service_is_not_external():
    """nvm's `VITE_API_BASE_URL` points at the repository's own API — compose declares
    that service with `build:`, which is the mechanical way to know it is ours. Without
    this the context diagram lists the repo as its own external system."""
    from srlib import context as ctx

    compose = (
        "services:\n"
        "  db:\n"
        "    image: postgres:17-alpine\n"
        "  api:\n"
        "    build: ./backend\n"
    )
    ours = ctx.own_service_names(compose)
    assert "api" in ours and "db" not in ours

    found = ctx.from_config_keys(".env.example", ["VITE_API_BASE_URL", "REDIS_URL"], ours=ours)
    systems = {item["system"] for item in found}
    assert "Redis" in systems
    assert not any("api" in s.lower() for s in systems), systems


def test_the_context_picture_is_drawn_by_graphviz_from_the_same_edges_as_the_text():
    """The pair used to disagree: the mermaid file was built from five sources and the
    SVG from two, so the picture showed a node the text had dropped as redundant. Both
    now come out of one set of edges."""
    pytest.importorskip("pygraphviz")
    from srlib.erd_dot import context_dot_svg

    edges = {("backend", "PostgreSQL"): {"manifest", "compose"}, ("backend", "Keycloak"): {"compose"}}
    systems = {"PostgreSQL": "database", "Keycloak": "identity provider"}
    svg = context_dot_svg(edges, systems)

    assert svg is not None
    for text in ("backend", "PostgreSQL", "Keycloak", "manifest", "compose", "identity provider"):
        assert text in svg, f"{text} missing from the drawing"


def test_the_context_picture_is_not_drawn_without_graphviz(monkeypatch):
    import builtins

    from srlib import erd_dot

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "pygraphviz":
            raise ImportError("no wheel here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert erd_dot.context_dot_svg({("a", "B"): {"manifest"}}, {"B": "database"}) is None
