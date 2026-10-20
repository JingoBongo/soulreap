"""Run roots: what a repo actually runs, read from its deploy files — no LLM, no guessing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib.runroots import run_roots


def _roots(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str | None]]:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return {(r["file"], r["symbol"]) for r in run_roots(tmp_path, sorted(files))}


def test_uvicorn_in_a_subproject_dockerfile_resolves_under_its_src(tmp_path):
    files = {"svc/Dockerfile": 'CMD ["uvicorn", "svc_pkg.main:app", "--port", "8080"]\n',
             "svc/src/svc_pkg/__init__.py": "", "svc/src/svc_pkg/main.py": "app = object()\n",
             "other/src/svc_pkg/main.py": "app = 1\n"}
    assert _roots(tmp_path, files) == {("svc/src/svc_pkg/main.py", "app")}


def test_multiline_cmd_with_absolute_venv_binary(tmp_path):
    files = {"Dockerfile": ('CMD ["/app/.venv/bin/uvicorn", "cdp.main:app", "--host", "0.0.0.0", \\\n'
                           '     "--port", "8080"]\n'),
             "src/cdp/main.py": "app = 1\n"}
    assert _roots(tmp_path, files) == {("src/cdp/main.py", "app")}


def test_compose_commands_and_python_dash_m(tmp_path):
    files = {"docker-compose.yml": ("services:\n  orch:\n"
                                    "    command: uv run --no-sync python -m services.orchestrator\n"
                                    "  worker:\n"
                                    "    command: [\"python\", \"-m\", \"services.stage_worker\", \"infra\"]\n"
                                    "  cache:\n    command: redis-server --bind 0.0.0.0\n"),
             "services/orchestrator.py": "x = 1\n", "services/stage_worker.py": "x = 1\n"}
    assert _roots(tmp_path, files) == {("services/orchestrator.py", None), ("services/stage_worker.py", None)}


def test_dash_m_package_resolves_to_its_dunder_main(tmp_path):
    files = {"w/Dockerfile": 'CMD ["python", "-m", "watcher"]\n',
             "w/src/watcher/__init__.py": "", "w/src/watcher/__main__.py": "from watcher.main import main\nmain()\n"}
    assert _roots(tmp_path, files) == {("w/src/watcher/__main__.py", None)}


def test_procfile_script_path_and_fuseinstructions(tmp_path):
    files = {"Procfile": "web: gunicorn app.wsgi:application\nworker: python scripts/start.py\n",
             "app/wsgi.py": "application = 1\n", "scripts/start.py": "print(1)\n",
             "fuseinstructions.txt": "# fuse:port=4056\npython mercy.py --port 4054\n", "mercy.py": "x = 1\n"}
    assert _roots(tmp_path, files) == {("app/wsgi.py", "application"), ("scripts/start.py", None),
                                       ("mercy.py", None)}


def test_pyproject_console_scripts_are_roots(tmp_path):
    files = {"pyproject.toml": '[project]\nname = "x"\n[project.scripts]\ntool = "pkg.cli:main"\n',
             "src/pkg/cli.py": "def main():\n    return 0\n"}
    assert _roots(tmp_path, files) == {("src/pkg/cli.py", "main")}


def test_healthchecks_and_unresolvable_modules_are_not_roots(tmp_path):
    files = {"Dockerfile": ('HEALTHCHECK CMD ["curl", "-f", "http://localhost/health"]\n'
                            'CMD ["python", "-m", "nowhere.to.be.found"]\n')}
    assert _roots(tmp_path, files) == set()


def test_shell_entrypoints_a_dockerfile_runs_are_followed(tmp_path):
    files = {"Dockerfile": 'ENTRYPOINT ["./entrypoint.sh"]\n',
             "entrypoint.sh": "#!/bin/sh\nset -e\npython manage.py migrate\nexec gunicorn site_pkg.wsgi:application\n",
             "celery.worker.entrypoint.sh": "#!/bin/sh\ncelery -A site_pkg worker -l info\n",
             "docker-compose.yml": "services:\n  worker:\n    command: ./celery.worker.entrypoint.sh\n",
             "manage.py": "import os\n", "site_pkg/__init__.py": "from .celery import app\n",
             "site_pkg/celery.py": "app = 1\n", "site_pkg/wsgi.py": "application = 1\n"}
    assert _roots(tmp_path, files) == {("manage.py", None), ("site_pkg/wsgi.py", "application"),
                                       ("site_pkg/__init__.py", None)}


def test_a_js_app_package_json_gives_its_entry_as_a_root(tmp_path):
    files = {"frontend/package.json": '{"scripts": {"dev": "vite", "build": "vite build"}}\n',
             "frontend/src/main.tsx": "import App from './App'\n", "frontend/src/App.tsx": "export default 1\n",
             "tools/package.json": '{"scripts": {"lint": "eslint ."}}\n', "tools/index.js": "x\n"}
    assert _roots(tmp_path, files) == {("frontend/src/main.tsx", None)}


def test_without_anything_deployed_library_packages_are_roots(tmp_path):
    from srlib.runroots import library_roots

    files = {"python/eager-llm/pyproject.toml": '[project]\nname = "eager-llm"\n',
             "python/eager-llm/src/eager_llm/__init__.py": "from .client import Client\n",
             "python/eager-llm/src/eager_llm/client.py": "class Client:\n    pass\n",
             "pyproject.toml": '[project]\nname = "toolbox"\n', "toolbox/__init__.py": "X = 1\n"}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    assert sorted(r["file"] for r in library_roots(tmp_path, sorted(files))) == [
        "python/eager-llm/src/eager_llm/__init__.py", "toolbox/__init__.py"]


def test_an_absolute_container_path_maps_back_to_the_repo_file(tmp_path):
    files = {"Dockerfile": 'WORKDIR /app\nENTRYPOINT [ "/app/entrypoint.sh" ]\n',
             "entrypoint.sh": "#!/bin/sh\nexec gunicorn site_pkg.wsgi:application\n",
                 "site_pkg/wsgi.py": "application = 1\n"}
    assert _roots(tmp_path, files) == {("site_pkg/wsgi.py", "application")}


def test_compose_command_as_a_block_list_with_comments(tmp_path):
    files = {"deploy/dagster/docker-compose.yml": (
        "services:\n  code-location:\n    command:\n"
        "      # why code-server and not api grpc\n"
        "      - /app/.venv/bin/dagster\n      - code-server\n      - start\n"
        "      - --module-name\n      - cdp.dagster.definitions\n"
        "    environment:\n      X: 1\n"
        "  webserver:\n    command:\n      - /app/.venv/bin/dagster-webserver\n      - --port\n      - \"3000\"\n"),
        "src/cdp/dagster/definitions.py": "x = 1\n", "src/cdp/__init__.py": ""}
    assert _roots(tmp_path, files) == {("src/cdp/dagster/definitions.py", None)}


def test_a_cloud_run_deploy_command_in_a_workflow_is_a_root(tmp_path):
    files = {".github/workflows/deploy-test.yml": (
        "jobs:\n  deploy:\n    steps:\n      - run: |\n"
        "          gcloud run deploy cdp-mcp-test \\\n"
        "            --region=x \\\n"
        "            --command=/app/.venv/bin/uvicorn \\\n"
        "            --args=\"cdp.mcp.http:create_app,--factory,--port,8080\" \\\n"
        "            --set-env-vars=A=b\n"
        "      - run: uv run python scripts/bootstrap_db.py\n"),
        "src/cdp/mcp/http.py": "def create_app(): ...\n", "scripts/bootstrap_db.py": "x = 1\n"}
    assert _roots(tmp_path, files) == {("src/cdp/mcp/http.py", "create_app")}


def test_a_config_naming_a_module_links_it_to_the_root_that_shares_its_folder(tmp_path):
    from srlib.runroots import config_links
    files = {"src/cdp/dagster/definitions.py": "x = 1\n", "src/cdp/main.py": "app = 1\n",
             "src/cdp/dagster/component.py": "class ClientPipelineComponent: ...\n",
             "src/cdp/dagster/defs/seo/defs.yaml": "type: cdp.dagster.component.ClientPipelineComponent\n"
                                                   "attributes:\n  host: id.whatsnext.cc\n",
             "docs/example.yaml": "type: cdp.main.app\n"}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    code = {rel: {} for rel in files if rel.endswith(".py")}
    links = config_links(tmp_path, sorted(files), code, ["src/cdp/main.py", "src/cdp/dagster/definitions.py"])
    assert links == {"src/cdp/dagster/definitions.py": ["src/cdp/dagster/component.py"]}


def test_a_next_app_routes_by_file_so_every_page_and_route_is_a_root(tmp_path):
    files = {"package.json": '{"scripts": {"dev": "next dev"}, "dependencies": {"next": "15.0.0"}}',
             "app/layout.tsx": "export default function L() {}\n",
             "app/(chat)/page.tsx": "export default function P() {}\n",
             "app/api/chat/route.ts": "export async function POST() {}\n",
             "components/ui/button.tsx": "export function Button() {}\n"}
    assert {f for f, _ in _roots(tmp_path, files)} == {"app/layout.tsx", "app/(chat)/page.tsx", "app/api/chat/route.ts"}


def test_a_shadcn_ui_folder_is_vendored(tmp_path):
    from srlib.runroots import vendored
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "components.json").write_text('{"aliases": {"ui": "@/components/ui"}}', encoding="utf-8")
    assert vendored(tmp_path, ["web/components.json", "web/components/ui/button.tsx"]) == ["web/components/ui/**"]
