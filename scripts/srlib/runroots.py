"""Run roots: what a repository actually runs, read from its own deploy files.

Ranking by fan-in and call-tree size read utilities, type files and one-off scripts on
every larger repo it was tried on, and missed the four services jam-horeca deploys, the
FastAPI app nvm-forecast-app deploys and soulreap's own CLI. What runs in production is
written down: a Dockerfile's CMD, a compose `command:`, a Procfile, Fuse's
`fuseinstructions.txt`, a `[project.scripts]` entry. This module reads those and
resolves each command to the source file (and, where the command names one, the symbol)
it starts. Nothing here guesses from folder names.
"""

from __future__ import annotations

import contextlib
import json
import re
import shlex
import tomllib
from pathlib import Path, PurePosixPath

from .graph import _string_ref_target
from .paths import is_test_path

# `type: pkg.mod.Class`, `_target_: …`, `module = "…"`: a config value naming a module of this repo.
CONFIG_REF = re.compile(r"""^\s*-?\s*[\w-]+\s*[:=]\s*["']?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)["']?\s*$""")
CONFIG_SKIP = frozenset({".github", "docs", "doc", "node_modules", "examples", "example"})
SERVERS = frozenset({"uvicorn", "gunicorn", "hypercorn", "daphne", "granian"})
TARGET = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_][\w.]*$")
INSTRUCTION = re.compile(r"^\s*(CMD|ENTRYPOINT)\s+(.*)$", re.I)
COMPOSE_KEY = re.compile(r"^\s*(command|entrypoint):\s*(\S.*)$")
COMPOSE_BLOCK = re.compile(r"^\s*(command|entrypoint):\s*$")
MODULE_FLAGS = ("-m", "--module-name", "--module")  # python -m, dagster code-server --module-name
# A JS/TS app: package.json with one of these scripts runs something, and that something starts here.
JS_RUN_SCRIPTS = ("dev", "start", "serve")
JS_ENTRIES = ("src/main.tsx", "src/main.ts", "src/main.jsx", "src/main.js", "src/index.tsx", "src/index.ts",
              "src/index.jsx", "src/index.js", "server.ts", "server.js", "index.ts", "index.js", "app.js",
              "app/layout.tsx", "pages/_app.tsx")


NEXT_ROUTED = re.compile(r"^(?:src/)?(?:app/(?:.+/)?(?:page|route|layout)|pages/.+)\.(?:tsx|ts|jsx|js)$")


def _is_dockerfile(name: str) -> bool:
    return name in {"Dockerfile", "Containerfile"} or name.startswith("Dockerfile.") or name.endswith(".Dockerfile")


def _is_compose(name: str) -> bool:
    return re.fullmatch(r"(docker-)?compose[\w.-]*\.ya?ml", name) is not None


def _is_workflow(rel: str) -> bool:
    return rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml"))


def _compose_blocks(lines: list[str]) -> list[list[str]]:
    """`command:` written as a YAML block list, one `- token` per line (comments allowed between)."""
    found, current = [], None
    for line in lines:
        stripped = line.strip()
        if COMPOSE_BLOCK.match(line):
            current = []
            found.append(current)
        elif current is not None and (not stripped or stripped.startswith("#")):
            continue
        elif current is not None and stripped.startswith("- "):
            current.append(stripped[2:].strip().strip("'\""))
        else:
            current = None
    return [tokens for tokens in found if tokens]


def _cloud_run_commands(lines: list[str]) -> list[list[str]]:
    """`gcloud run deploy … --command=X --args=a,b,c` in a CI workflow: the command a service runs.
    Only these lines — the rest of a workflow is CI (tests, migrations), not what runs deployed."""
    commands = []
    for line in lines:
        if "--command=" not in line:
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        command = [t.split("=", 1)[1] for t in tokens if t.startswith("--command=")]
        args = [a for t in tokens if t.startswith("--args=") for a in t.split("=", 1)[1].split(",")]
        commands.append(command + args)
    return commands


def _split(text: str) -> list[str]:
    """A command as tokens: a JSON array (exec form) or a shell string; `sh -c "…"` unwrapped."""
    text = text.strip()
    try:
        tokens = json.loads(text) if text.startswith("[") else shlex.split(text)
    except (ValueError, json.JSONDecodeError):
        try:
            tokens = shlex.split(text.strip("[]").replace(",", " "))
        except ValueError:
            return []
    tokens = [str(t) for t in tokens]
    if "-c" in tokens[:-1]:  # sh -c "exec uvicorn …": the real command is the string
        with contextlib.suppress(ValueError):
            tokens += shlex.split(tokens[tokens.index("-c") + 1])
    return tokens


def _commands(rel: str, text: str) -> list[list[str]]:
    """Every command line a deploy file declares, tokenized."""
    name = PurePosixPath(rel).name
    lines = text.replace("\\\n", " ").splitlines()
    if _is_dockerfile(name):
        return [_split(m[2]) for line in lines if (m := INSTRUCTION.match(line))]
    if _is_compose(name):
        return [_split(m[2]) for line in lines if (m := COMPOSE_KEY.match(line))] + _compose_blocks(lines)
    if _is_workflow(rel):
        return _cloud_run_commands(lines)
    if name == "Procfile":
        return [_split(line.split(":", 1)[1]) for line in lines if ":" in line and not line.startswith("#")]
    if name == "fuseinstructions.txt":
        return [_split(line) for line in lines if line.strip() and not line.lstrip().startswith("#")]
    return []


def _targets(tokens: list[str]) -> list[tuple[str, str, str | None]]:
    """(kind, name, symbol) a command starts: ("module", "pkg.main", "app") or ("path", "x.py", None)."""
    found = []
    base = [PurePosixPath(t).name for t in tokens]
    for i, (token, name) in enumerate(zip(tokens, base, strict=True)):
        if name in SERVERS:
            hit = next((t for t in tokens[i + 1:] if TARGET.match(t)), None)
            if hit:
                module, _, attr = hit.partition(":")
                found.append(("module", module, attr))
        elif token in MODULE_FLAGS and i + 1 < len(tokens) and re.fullmatch(r"[\w.]+", tokens[i + 1]):
            found.append(("module", tokens[i + 1], None))
        elif token in ("-A", "--app") and name and i + 1 < len(tokens) and base[max(i - 1, 0)] != "-m":
            if any(b == "celery" for b in base[:i]):
                found.append(("module", tokens[i + 1].split(":")[0], None))
        elif token.endswith(".py") and not token.startswith("-"):
            found.append(("path", token, None))
        elif token.endswith(".sh") and not token.startswith("-"):  # entrypoint.sh: read its commands
            found.append(("shell", token, None))
    return found


def _resolve_module(module: str, files: list[str], base: str) -> str | None:
    """The file `python -m module` / `module:attr` loads, preferring the deploy file's own tree."""
    stem = module.replace(".", "/")
    for suffix in (f"{stem}.py", f"{stem}/__main__.py", f"{stem}/__init__.py"):
        hits = [f for f in files if f == suffix or f.endswith("/" + suffix)]
        if hits:
            local = [f for f in hits if not base or f.startswith(base + "/")]
            return min(local or hits, key=lambda f: (len(PurePosixPath(f).parts), f))
    return None


def _resolve_path(path: str, files: list[str], base: str) -> str | None:
    """A script path as the container sees it (`/app/x.py`, `./x.py`) mapped back to the repo."""
    clean = path.lstrip("./")
    for candidate in (f"{base}/{clean}" if base else clean, clean):
        if candidate in files:
            return candidate
    for parts in (2, 1):  # `/app/entrypoint.sh`: /app is the container's WORKDIR, not a repo folder
        tail = "/".join(PurePosixPath(clean).parts[-parts:])
        hits = [f for f in files if f == tail or f.endswith("/" + tail)]
        local = [f for f in hits if not base or f.startswith(base + "/")]
        if local or hits:
            return min(local or hits, key=len)
    return None


def _js_app(root: Path, rel: str, files: list[str]) -> list[dict]:
    """A package.json that runs an app (`dev`/`start`/`serve`): its `main`, else a conventional entry."""
    if "node_modules" in rel.split("/"):
        return []
    try:
        data = json.loads((root / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or not set(JS_RUN_SCRIPTS) & set(data.get("scripts") or {}):
        return []
    base = str(PurePosixPath(rel).parent) if "/" in rel else ""
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    if "next" in deps:  # Next routes by file path: nothing imports a page or a route handler
        prefix = f"{base}/" if base else ""
        routed = [f for f in files if f.startswith(prefix) and NEXT_ROUTED.match(f[len(prefix):])]
        if routed:
            return [{"file": f, "symbol": None, "via": rel} for f in sorted(routed)]
    for entry in ([str(data["main"]).lstrip("./")] if data.get("main") else []) + list(JS_ENTRIES):
        candidate = f"{base}/{entry}" if base else entry
        if candidate in files:
            return [{"file": candidate, "symbol": None, "via": rel}]
    return []


def vendored(root: Path | str, files: list[str]) -> list[str]:
    """Folders a generator owns, as exclude globs: shadcn's `components.json` names its `ui` alias
    (`@/components/ui`) — copied-in library code that took a third of more2win's read budget."""
    found = []
    for rel in files:
        if PurePosixPath(rel).name != "components.json" or "node_modules" in rel.split("/"):
            continue
        try:
            ui = (json.loads((Path(root) / rel).read_text(encoding="utf-8")).get("aliases") or {}).get("ui")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(ui, str) and ui[:2] in ("@/", "~/"):
            base = str(PurePosixPath(rel).parent) if "/" in rel else ""
            found.append(f"{base}/{ui[2:]}/**" if base else f"{ui[2:]}/**")
    return found


def library_roots(root: Path | str, files: list[str]) -> list[dict]:
    """Packages a pyproject.toml publishes: when nothing is deployed, a library's public API is
    what "runs" — its `__init__.py`, which re-exports what callers import."""
    roots = []
    for rel in files:
        if PurePosixPath(rel).name != "pyproject.toml":
            continue
        try:
            data = tomllib.loads((Path(root) / rel).read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        name = data.get("project", {}).get("name") or data.get("tool", {}).get("poetry", {}).get("name")
        if not name:
            continue
        package = re.sub(r"[-.]", "_", str(name)).lower()
        base = f"{PurePosixPath(rel).parent}/" if "/" in rel else ""
        for candidate in (f"{base}src/{package}/__init__.py", f"{base}{package}/__init__.py"):
            if candidate in files:
                roots.append({"file": candidate, "symbol": None, "via": rel})
                break
    return roots


def _console_scripts(root: Path, rel: str, files: list[str]) -> list[dict]:
    try:
        data = tomllib.loads((root / rel).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    scripts = {**(data.get("project", {}).get("scripts") or {}),
               **(data.get("tool", {}).get("poetry", {}).get("scripts") or {})}
    base = str(PurePosixPath(rel).parent).replace(".", "") if "/" in rel else ""
    roots = []
    for spec in scripts.values():
        module, _, func = str(spec).partition(":")
        target = _resolve_module(module, files, base)
        if target:
            roots.append({"file": target, "symbol": func.split(".")[0] or None, "via": rel})
    return roots


def run_roots(root: Path | str, files: list[str]) -> list[dict]:
    """Every source file a deploy file or console script starts: {file, symbol, via}.

    *files* are repo-relative paths (code and non-code). A command naming nothing in the
    repo (`redis-server`, a healthcheck `curl`) yields nothing.
    """
    root = Path(root)
    code = [f for f in files if f.endswith(".py")]
    roots: list[dict] = []
    for rel in files:
        name = PurePosixPath(rel).name
        base = str(PurePosixPath(rel).parent) if "/" in rel else ""
        if name == "pyproject.toml":
            roots += _console_scripts(root, rel, code)
            continue
        if name == "package.json":
            roots += _js_app(root, rel, files)
            continue
        if not (_is_dockerfile(name) or _is_compose(name) or _is_workflow(rel)
                or name in ("Procfile", "fuseinstructions.txt")):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        commands = [(rel, base, tokens) for tokens in _commands(rel, text)]
        while commands:
            via, here, tokens = commands.pop(0)
            for kind, target, symbol in _targets(tokens):
                if kind == "shell":  # one level: the commands an entrypoint script runs
                    script = _resolve_path(target, files, here) if via == rel else None
                    if script:
                        lines = (root / script).read_text(encoding="utf-8", errors="replace").splitlines()
                        commands += [(script, here, _split(line)) for line in lines
                                     if line.strip() and not line.lstrip().startswith(("#", "set "))]
                    continue
                found = (_resolve_module if kind == "module" else _resolve_path)(target, code, here)
                if found:
                    roots.append({"file": found, "symbol": symbol, "via": via})
    unique: dict[tuple[str, str | None], dict] = {}
    for item in roots:
        unique.setdefault((item["file"], item["symbol"]), item)
    return list(unique.values())


def config_links(root: Path | str, files: list[str], code: dict, roots: list[str]) -> dict[str, list[str]]:
    """Modules a run root loads through config rather than imports: {root file: [module files]}.

    Dagster's `load_from_defs_folder` builds every asset from `defs/*/defs.yaml`, whose `type:`
    names the component class; nothing imports it, so reach from `definitions.py` never got
    there and the whole nightly pipeline went unread. A YAML/TOML value naming a repo module
    belongs to the run root whose folder holds that config (the deepest one, if several do).
    """
    root, known, links = Path(root), set(code), {}
    folders = {r: str(PurePosixPath(r).parent) if "/" in r else "" for r in roots}
    for rel in files:
        if not rel.endswith((".yaml", ".yml", ".toml")) or CONFIG_SKIP & set(rel.split("/")[:-1]) \
                or is_test_path(rel):
            continue
        owners = [r for r, d in folders.items() if not d or rel.startswith(d + "/")]
        if not owners:
            continue
        owner = max(owners, key=lambda r: (len(folders[r]), r))
        try:
            lines = (root / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = CONFIG_REF.match(line)
            target = match and _string_ref_target(match[1], rel, known)
            if target and target != owner and target not in links.setdefault(owner, []):
                links[owner].append(target)
    return {r: sorted(t) for r, t in links.items() if t}
