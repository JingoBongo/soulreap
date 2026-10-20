"""What this repository talks to, read off the files that write it down.

The context question — "what does this code talk to" — used to be answered from
two facts only: an imported client library, and a URL literal in the source. Both
dogfood repositories came back wrong. fuse_core got "No external systems
detected" while cloning repositories over git and installing its own `vaults`
storage from a git server; nvm-forecast got "SQL database" twice, once from a
worktree copy of itself. In both, every address arrives through a config value,
which is exactly where a literal-hunting detector is blind.

So three more sources, all mechanical, none of them a guess by a model:

* a **dependency manifest** — a package name IS the name of the system behind it;
* a **configuration key** — a name ending in ``_URL``/``_URI``/``_DSN``/
  ``_ENDPOINT``/``_HOST`` says a system is reached there, and says what it is
  called, without anyone reading the value;
* a **compose file** — the neighbouring services are listed by name, with images.

Every finding carries `via`, the source it came from, because the reader needs to
know how solid the claim is: a package in a manifest is nearly proof, a key name
is a strong hint, and the two deserve different weight on the same picture.

Nothing here reads a value out of a secret file. Keys only, as everywhere else.
"""

from __future__ import annotations

from pathlib import Path

# Re-exported: other modules and tests import these names from here.
from .context_config import (  # noqa: F401  (re-exported)
    _config_keys,
    compose_services,
    from_compose,
    from_config_keys,
    is_compose_file,
    own_service_names,
    system_for_image,
    system_for_key,
)
from .context_manifests import (  # noqa: F401  (re-exported)
    from_manifest,
    system_for_package,
)
from .context_tables import (  # noqa: F401  (re-exported)
    BASE_IMAGES,
    CONFIG_KEY_SUFFIXES,
    GENERIC_SQL,
    KIND_BY_SUFFIX,
    SYSTEM_BY_IMAGE,
    SYSTEM_BY_KEY_STEM,
    SYSTEM_BY_PACKAGE,
    SYSTEM_BY_PACKAGE_PREFIX,
    VIA_BINARY,
    VIA_COMPOSE,
    VIA_CONFIG,
    VIA_IMPORT,
    VIA_LITERAL,
    VIA_MANIFEST,
    _evidence,
)

MAX_MANIFEST_BYTES = 400_000

# A program name is not always the name of the system it reaches: `psql` is PostgreSQL,
# `gh` is GitHub. Anything not listed keeps its own name — `git` really is called git, and
# inventing a prettier label for an unknown binary would be a guess.
SYSTEM_BY_BINARY = {
    "git": ("git", "version control"),
    "gh": ("GitHub", "version control"),
    "hg": ("Mercurial", "version control"),
    "psql": ("PostgreSQL", "database"),
    "pg_dump": ("PostgreSQL", "database"),
    "mysql": ("MySQL", "database"),
    "sqlite3": ("SQLite", "database"),
    "redis-cli": ("Redis", "cache"),
    "mongosh": ("MongoDB", "database"),
    "docker": ("Docker", "container runtime"),
    "docker-compose": ("Docker", "container runtime"),
    "kubectl": ("Kubernetes", "orchestrator"),
    "helm": ("Kubernetes", "orchestrator"),
    "terraform": ("Terraform", "infrastructure"),
    "gcloud": ("Google Cloud", "cloud"),
    "aws": ("AWS", "cloud"),
    "az": ("Azure", "cloud"),
    "systemctl": ("systemd", "host init"),
    "ssh": ("remote host", "host"),
    "rsync": ("remote host", "host"),
    "curl": ("HTTP", "network"),
    "wget": ("HTTP", "network"),
    "ffmpeg": ("ffmpeg", "media tool"),
    "pandoc": ("pandoc", "document tool"),
}

# Build and package tooling a repo runs on ITSELF. Listing them as external systems would
# bury the two that matter under twenty that every Python repository has.
_OWN_TOOLING = {
    # build and package tooling every repository of its language runs on itself
    "python", "python3", "pip", "pip3", "uv", "uvx", "poetry", "pytest", "ruff", "mypy",
    "node", "npm", "npx", "yarn", "pnpm", "tsc", "eslint", "prettier", "make", "cargo",
    "go", "gofmt",
    # shell and file utilities. `rmdir` really was on fuse_core's context diagram beside
    # git and systemd, and a node a reader has to skip every time is worse than no node:
    # deleting a directory is not a system this code talks to.
    "bash", "sh", "zsh", "cmd", "powershell", "cp", "mv", "rm", "rmdir", "mkdir", "ls",
    "cat", "echo", "chmod", "chown", "touch", "find", "grep", "sed", "awk", "tar", "zip",
    "unzip", "gzip", "kill", "killall", "taskkill", "which", "where", "env", "true",
}


def from_binaries(facts: dict) -> list[dict]:
    """Systems named by the programs this repository shells out to.

    The source that closes fuse_core's hole: it clones and resets through `git` and
    restarts itself through `systemctl`, and neither shows up as an import or a URL.
    """
    found: list[dict] = []
    for path in sorted(facts):
        for program in (facts[path] or {}).get("binaries") or []:
            if program in _OWN_TOOLING:
                continue
            system, kind = SYSTEM_BY_BINARY.get(program, (program, "external program"))
            found.append(_evidence(system, kind, VIA_BINARY, path, program))
    return found


def searched(entries) -> dict[str, list[str]]:
    """The files each detector will look at, so `plan` can report what it read.

    An empty context diagram is only believable if the reader can see the search
    was real; this is that list.
    """
    manifests, configs, compose = [], [], []
    for entry in entries:
        if entry.kind == "non_code" and entry.category == "manifest":
            manifests.append(entry.path)
        elif entry.kind == "secret" or (entry.kind == "non_code" and entry.category == "config"):
            configs.append(entry.path)
        if is_compose_file(entry.path):
            compose.append(entry.path)
    return {
        "manifest": sorted(manifests),
        "config": sorted(configs),
        "compose": sorted(compose),
    }


def collect(root: Path | str, entries) -> list[dict]:
    """Every external system the non-code files of this repository name.

    The per-file parser's own two sources — client-library imports and URL
    literals — are merged in later, in `diagrams.context_mermaid`: they come out
    of facts.json, which this function has no business reading.
    """
    root = Path(root)
    by_source = searched(entries)
    env_keys = {e.path: e.env_keys for e in entries if e.kind == "secret"}
    found: list[dict] = []

    def read(rel: str) -> str | None:
        target = root / rel
        try:
            if target.stat().st_size > MAX_MANIFEST_BYTES:
                return None
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    for path in by_source["manifest"]:
        text = read(path)
        if text is not None:
            found.extend(from_manifest(path, text))

    # Compose first: it says which services this repository BUILDS, and a config key
    # naming one of those is pointing at us, not out.
    ours: set[str] = set()
    compose_texts: dict[str, str] = {}
    for path in by_source["compose"]:
        text = read(path)
        if text is not None:
            compose_texts[path] = text
            ours |= own_service_names(text)

    for path in by_source["config"]:
        # A secret file's keys were already harvested by `walk` without its values
        # ever being held; re-reading it here would undo that guarantee.
        keys = env_keys.get(path)
        if keys is None:
            text = read(path)
            keys = _config_keys(text) if text is not None else []
        found.extend(from_config_keys(path, keys, ours=ours))

    for path, text in compose_texts.items():
        found.extend(from_compose(path, text))

    return found
