"""Facts that are never stale: every v10 command starts here.

`facts.json` stays committed, so an agent without `uv` can still read it, but no
command trusts it over the tree: each call re-hashes the repo and re-parses only
the files whose sha moved. Nothing is written when nothing moved, so a query does
not dirty the working tree.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .build import build_facts
from .facts import FACTS_VERSION
from .util import load_json, write_json


def extractor_digest(folder: Path) -> str:
    """A digest of every module in *folder*: the code that produced a cached blob.

    A cached blob is only reusable when it was produced by this exact code: FACTS_VERSION
    covers deliberate shape changes, this covers the rest (a fixed detector, a new hazard)
    without anyone having to remember a bump. It used to hash a hand-kept list of nine
    modules, which had already missed fingerprints.py (the literal detectors) and went on
    to miss every module split out of facts, schema, context and hazards. All of them,
    then: a change anywhere costs one re-parse, a missed one serves stale facts silently.
    """
    return hashlib.sha1(
        b"".join(path.name.encode() + path.read_bytes() for path in sorted(Path(folder).glob("*.py")))
    ).hexdigest()[:12]


EXTRACTOR = extractor_digest(Path(__file__).parent)


def facts_path(out: Path | str) -> Path:
    """Where the facts of a map live — or under `$SOULREAP_CACHE` when set, so a git hook can keep
    its cache inside `.git` rather than rewrite a tracked `soulreap/` folder mid-commit."""
    cache = os.environ.get("SOULREAP_CACHE")
    return Path(cache) / "facts.json" if cache else Path(out) / ".soulreap" / "facts.json"


def fresh_facts(root: Path | str, out: Path | str, write: bool = True) -> dict:
    """Facts for the working tree right now; rewrites facts.json only if something moved."""
    path = facts_path(out)
    stored = load_json(path, {})
    reusable = stored.get("facts_version") == FACTS_VERSION and stored.get("extractor") == EXTRACTOR
    previous = (stored.get("files") or {}) if reusable else {}
    build = build_facts(root, previous)
    payload = {"facts_version": FACTS_VERSION, "extractor": EXTRACTOR, "files": build.files, "context": build.context}
    moved = bool(build.reparsed) or set(build.files) != set(previous) or not reusable
    if moved and write:
        write_json(path, payload)
    return {**payload, "reparsed": build.reparsed, "entries": build.entries, "cycles": build.graph.cycles}
