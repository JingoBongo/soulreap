"""Systems named by configuration keys and by compose files.

A key ending in ``_URL``/``_URI``/``_DSN``/``_ENDPOINT``/``_HOST`` says a system is
reached there without anyone reading the value, and a compose file lists its
neighbouring services by name and image. Both are read here, keys and names only.
"""

from __future__ import annotations

import re
from pathlib import Path

from .context_tables import (
    BASE_IMAGES,
    CONFIG_KEY_SUFFIXES,
    KIND_BY_SUFFIX,
    SYSTEM_BY_IMAGE,
    SYSTEM_BY_KEY_STEM,
    VIA_COMPOSE,
    VIA_CONFIG,
    _evidence,
)

_CONFIG_KEY = re.compile(r"""^[\s\-]*["']?([A-Za-z_][A-Za-z0-9_]*)["']?\s*[:=]""")
_COMPOSE_NAME = re.compile(r"^(\s*)([A-Za-z0-9][A-Za-z0-9._-]*):\s*(.*)$")


def _humanise(stem: str) -> str:
    """`FUSE_SELF_REPO` -> `Fuse self repo`. The key is all we know, so the key is
    what the node is called — no prefix is stripped, because stripping one would
    mean guessing which part of the name is the company and which is the system."""
    words = stem.replace("-", "_").split("_")
    text = " ".join(w for w in words if w).lower()
    return text[:1].upper() + text[1:] if text else ""


def system_for_key(key: str) -> tuple[str, str] | None:
    """The system a configuration key names, or None when the key is not one.

    Only the name is used. The value is never read, here or anywhere else in
    soulreap — which is the whole reason this detector can run over `.env`.
    """
    upper = key.strip().upper()
    for suffix in CONFIG_KEY_SUFFIXES:
        if not upper.endswith(suffix):
            continue
        stem = upper[: -len(suffix)].strip("_")
        if not stem:
            return None  # a bare `URL:` names nothing
        known = SYSTEM_BY_KEY_STEM.get(stem.lower()) or SYSTEM_BY_KEY_STEM.get(
            stem.rsplit("_", 1)[-1].lower()
        )
        if known:
            return known
        return _humanise(stem), KIND_BY_SUFFIX[suffix]
    return None


def _config_keys(text: str) -> list[str]:
    """Key names out of an .env, .yaml, .ini or .json config. Names only.

    A line-shaped reader rather than a parser for each format on purpose: the one
    thing every one of them agrees on is `name: value` / `name = value` at the
    start of a line, and that is the only thing this needs.
    """
    keys: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//")):
            continue
        match = _CONFIG_KEY.match(line)
        if match and match.group(1) not in keys:
            keys.append(match.group(1))
    return keys


def own_service_names(text: str) -> set[str]:
    """Compose services built from this repository — our own code, not a system.

    `build:` is the mechanical statement of ownership: an image is something we pull,
    a build is something we are.
    """
    return {
        name.lower()
        for name, service in compose_services(text).items()
        if service.get("build")
    }


def from_config_keys(path: str, keys: list[str], ours: set[str] | None = None) -> list[dict]:
    """Systems named by the configuration keys of one file.

    `ours` names services compose says this repository builds. nvm-forecast declares
    `VITE_API_BASE_URL`, and the API it points at is the repo itself — without this the
    diagram lists the repository as one of its own external systems.
    """
    ours = {name.lower() for name in (ours or set())}
    found: list[dict] = []
    seen: set[str] = set()
    for key in keys:
        if ours and {word for word in key.lower().split("_")} & ours:
            continue
        system = system_for_key(key)
        if system and system[0] not in seen:
            seen.add(system[0])
            found.append(_evidence(system[0], system[1], VIA_CONFIG, path, key))
    return found


# ------------------------------------------------------------------ compose ---


def is_compose_file(path: str) -> bool:
    name = Path(path).name.lower()
    return name.startswith("docker-compose") or name.startswith("compose.")


def compose_services(text: str) -> dict[str, dict]:
    """``{service: {"image": str|None, "build": bool}}`` from a compose file.

    Hand-rolled instead of a YAML parser because pulling PyYAML in would add a
    runtime dependency to the whole skill for four lines of structure, and the
    structure this needs — one `services:` block, one level of names under it,
    two keys under each — is the part of YAML nobody writes exotically.
    """
    services: dict[str, dict] = {}
    services_indent: int | None = None
    entry_indent: int | None = None
    current: str | None = None

    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _COMPOSE_NAME.match(line.rstrip())
        indent = len(line) - len(line.lstrip())
        if services_indent is None:
            if match and match.group(2) == "services" and not match.group(3):
                services_indent = indent
            continue
        if indent <= services_indent:
            break  # a sibling of `services:` ends the block
        if not match:
            continue
        name, value = match.group(2), match.group(3).strip()
        if entry_indent is None:
            entry_indent = indent
        if indent == entry_indent:
            current = name
            services[current] = {"image": None, "build": False}
            continue
        if current is None:
            continue
        if name == "image" and value:
            services[current]["image"] = value.strip("'\"")
        elif name == "build":
            services[current]["build"] = True
    return services


def system_for_image(image: str) -> tuple[str, str] | None:
    """The system an image runs, or None when it is a runtime for our own code."""
    last = image.split("@", 1)[0].rsplit("/", 1)[-1]
    # Only the last segment can carry a tag; a colon earlier in the reference is a
    # registry port (`localhost:5000/postgres`).
    last = last.rsplit(":", 1)[0].lower() if ":" in last else last.lower()
    if not last or last in BASE_IMAGES:
        return None
    if last in SYSTEM_BY_IMAGE:
        return SYSTEM_BY_IMAGE[last]
    # An image we have no entry for is still a neighbour listed by name; naming it
    # after the image is the most we can honestly say about it.
    return last, "service"


def from_compose(path: str, text: str) -> list[dict]:
    """Systems named by the services of one compose file.

    A service with `build:` is this repository's own code — it is the thing the
    diagram's left column is about, not something it talks to.
    """
    found: list[dict] = []
    for name, service in compose_services(text).items():
        image = service.get("image")
        if service.get("build") or not image:
            continue
        system = system_for_image(image)
        if system:
            found.append(_evidence(system[0], system[1], VIA_COMPOSE, path, f"{name}: {image}"))
    return found


# ------------------------------------------------------------------ collect ---


# A program name is not always the name of the system it reaches: `psql` is PostgreSQL,
# `gh` is GitHub. Anything not listed keeps its own name — `git` really is called git, and
