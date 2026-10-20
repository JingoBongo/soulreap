"""Systems named by dependency manifests.

A package name in requirements.txt, pyproject.toml, package.json, Cargo.toml,
go.mod or a Gemfile IS the name of the system behind it. This module reads those
manifests (names only), maps them through the package tables, and recognises a
package installed straight from a git server as a system of its own.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .context_tables import SYSTEM_BY_PACKAGE, SYSTEM_BY_PACKAGE_PREFIX, VIA_MANIFEST, _evidence

_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9._@/+-]+")
_REQUIREMENT_SPLIT = re.compile(r"[\[<>=!~;,\s]")
_GEM = re.compile(r"""^\s*gem\s+['"]([^'"]+)['"]""")
_GO_REQUIRE = re.compile(r"^\s*(?:require\s+)?([a-z0-9][a-z0-9.\-]*\.[a-z]{2,}/[^\s]+)\s+v[\w.\-+]+")


def _normalise_package(raw: str) -> str:
    """A distribution name as the tables spell it: lowercase, `_` folded to `-`.

    PyPI treats `python_dotenv` and `python-dotenv` as the same project, and a
    manifest may write either.
    """
    return raw.strip().strip("'\"").lower().replace("_", "-")


def system_for_package(raw: str) -> tuple[str, str] | None:
    """The system behind a distribution name, or None when we do not know it.

    None is the common answer and the right one: `flask`, `psutil` and `pyyaml`
    are not systems anyone integrates with, and inventing a node for every
    package is how the picture stops being readable.
    """
    name = _normalise_package(raw)
    if not name:
        return None
    if name in SYSTEM_BY_PACKAGE:
        return SYSTEM_BY_PACKAGE[name]
    best: tuple[str, tuple[str, str]] | None = None
    for prefix, system in SYSTEM_BY_PACKAGE_PREFIX:
        if name.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, system)
    return best[1] if best else None


def _requirement_names(text: str) -> list[str]:
    """Package names out of a requirements.txt, plus VCS requirements as URLs.

    A VCS line is kept whole (it still starts with its scheme) so the caller can
    tell the two apart: `git+http://.../vaults#egg=vaults` is not a package we
    have a table entry for, it is a system this repo installs from.
    """
    names: list[str] = []
    for line in text.splitlines():
        # `#` opens a comment everywhere except in `#egg=`, which is part of the URL.
        stripped = (line if "#egg=" in line else line.split("#", 1)[0]).strip()
        if not stripped or stripped.startswith("-"):
            continue
        if "://" in stripped:
            names.append(stripped.split()[0])
            continue
        match = _REQUIREMENT_NAME.match(stripped)
        if match:
            names.append(_REQUIREMENT_SPLIT.split(match.group(0))[0])
    return names


def _pyproject_names(text: str) -> list[str]:
    """Dependency names from PEP 621, PEP 735 groups and poetry alike.

    All three shapes live in the wild and a repo often carries two of them, so
    read every one rather than deciding which dialect this file "is".
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    names: list[str] = []

    def from_requirements(items) -> None:
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str):
                match = _REQUIREMENT_NAME.match(item.strip())
                if match:
                    names.append(_REQUIREMENT_SPLIT.split(match.group(0))[0])

    project = data.get("project") or {}
    from_requirements(project.get("dependencies"))
    for group in (project.get("optional-dependencies") or {}).values():
        from_requirements(group)
    for group in (data.get("dependency-groups") or {}).values():
        from_requirements(group)

    poetry = ((data.get("tool") or {}).get("poetry")) or {}
    for table in (poetry.get("dependencies"), poetry.get("dev-dependencies")):
        names.extend(k for k in (table or {}) if k != "python")
    for group in (poetry.get("group") or {}).values():
        names.extend(k for k in (group.get("dependencies") or {}) if k != "python")
    return names


def _package_json_names(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    names: list[str] = []
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        table = data.get(key)
        if isinstance(table, dict):
            names.extend(table)
    return names


def _cargo_names(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    names: list[str] = []
    for key in ("dependencies", "dev-dependencies", "build-dependencies"):
        table = data.get(key)
        if isinstance(table, dict):
            names.extend(table)
    workspace = (data.get("workspace") or {}).get("dependencies")
    if isinstance(workspace, dict):
        names.extend(workspace)
    return names


def _go_mod_names(text: str) -> list[str]:
    return [m.group(1) for m in (_GO_REQUIRE.match(line) for line in text.splitlines()) if m]


def _gemfile_names(text: str) -> list[str]:
    return [m.group(1) for m in (_GEM.match(line) for line in text.splitlines()) if m]


def _manifest_names(path: str, text: str) -> list[str]:
    name = Path(path).name.lower()
    if name == "pyproject.toml":
        return _pyproject_names(text)
    if name == "package.json":
        return _package_json_names(text)
    if name == "cargo.toml":
        return _cargo_names(text)
    if name == "go.mod":
        return _go_mod_names(text)
    if name == "gemfile":
        return _gemfile_names(text)
    return _requirement_names(text)


# Forges and registries every repository of its language pulls from. A node they all
# have is noise — the same reason `rmdir` is in _OWN_TOOLING.
PUBLIC_FORGE = re.compile(
    r"^(www\.|gist\.|raw\.|codeload\.)?("
    r"github\.com|githubusercontent\.com|gitlab\.com|bitbucket\.org|codeberg\.org"
    r"|git\.sr\.ht|sourceforge\.net|pypi\.org|files\.pythonhosted\.org"
    r"|registry\.npmjs\.org|npmjs\.com|crates\.io|proxy\.golang\.org|rubygems\.org"
    r")$",
    re.IGNORECASE,
)


def _vcs_requirement(raw: str) -> tuple[str, str] | None:
    """`git+http://192.168.0.4:9999/x/vaults#egg=vaults` -> ("192.168.0.4:9999", "package source").

    The node is the HOST, and it used to be the package name. The package is a library
    that runs inside this process — `vaults` is fuse_core's storage, but the code no
    more "talks to" it than to `json` — while the host is a machine that has to be up
    for the build to work, is named nowhere else in the repository, and is exactly the
    kind of thing a reader of a context diagram is looking for. A bare LAN IP does tell
    them little; it still tells them there IS a box, which the package name hid.

    A public forge is left out: every repository pulls from one, and credentials in the
    URL are dropped with the userinfo before anything is recorded.
    """
    if "://" not in raw:
        return None
    netloc = raw.split("://", 1)[-1].split("/", 1)[0].split("#", 1)[0].split("?", 1)[0]
    netloc = netloc.rsplit("@", 1)[-1].strip().lower()  # drop user:token@
    if not netloc or PUBLIC_FORGE.match(netloc.split(":", 1)[0]):
        return None
    return netloc, "package source"


def from_manifest(path: str, text: str) -> list[dict]:
    """Systems named by the packages one dependency manifest pulls in."""
    found: list[dict] = []
    for raw in _manifest_names(path, text):
        if "://" in raw:
            vcs = _vcs_requirement(raw)
            if vcs:
                # `detail` is the host, never `raw`: a VCS line can carry a token.
                found.append(_evidence(vcs[0], vcs[1], VIA_MANIFEST, path, vcs[0]))
            continue
        system = system_for_package(raw)
        if system:
            found.append(_evidence(system[0], system[1], VIA_MANIFEST, path, _normalise_package(raw)))
    return found


# -------------------------------------------------------------- config keys ---

