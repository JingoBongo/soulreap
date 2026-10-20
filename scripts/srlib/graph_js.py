"""JS/TS import resolution: relative paths, tsconfig/jsconfig `paths` and `baseUrl`.

An alias such as `@/*` means whatever the nearest tsconfig.json says it means, relative
to that file's own directory — so a subproject at `frontend/` resolves `@/lib/x` to
`frontend/src/lib/x`, not to a `src/lib/x` at the repo root. With no config the old
guesses (`@/` and `~/` from the root, under `src/` and `app/`) still apply.
"""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path

JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
JS_INDEXES = tuple(f"/index{ext}" for ext in JS_SUFFIXES)
_CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
_ESM_SWAPS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}


def parse_jsonc(text: str) -> dict:
    """JSON with `//` and `/* */` comments and trailing commas, as tsconfig allows. {} if unreadable."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
    try:
        data = json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class JsConfigs:
    """tsconfig/jsconfig lookups under one repo root, cached per directory for one build."""

    def __init__(self, root: Path | str | None) -> None:
        self.root = Path(root) if root is not None else None
        self._dirs: dict[str, tuple[str, str | None, dict[str, tuple[str, list[str]]]] | None] = {}

    def _read(self, rel: str) -> dict:
        if self.root is None:
            return {}
        try:
            return parse_jsonc((self.root / rel).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return {}

    def _options(self, rel: str, depth: int = 0) -> tuple[str | None, dict[str, tuple[str, list[str]]]]:
        """(baseUrl dir, pattern → (dir the targets are relative to, targets)) for one config file,
        following a relative `extends` and a solution-style `references` once."""
        data = self._read(rel)
        here = posixpath.dirname(rel)
        base: str | None = None
        paths: dict[str, tuple[str, list[str]]] = {}
        parent = data.get("extends")
        if depth == 0 and isinstance(parent, str) and parent.startswith("."):
            target = posixpath.normpath(posixpath.join(here, parent))
            target = target if target.endswith(".json") else f"{target}.json"
            base, paths = self._options(target, 1)
        opts = data.get("compilerOptions") or {}
        if isinstance(opts.get("baseUrl"), str):
            base = posixpath.normpath(posixpath.join(here, opts["baseUrl"]))
        own = opts.get("paths")
        if isinstance(own, dict):
            anchor = base if base is not None else here
            paths = {k: (anchor, [t for t in v if isinstance(t, str)])
                     for k, v in own.items() if isinstance(v, list)}
        if depth == 0 and not paths:
            for ref in data.get("references") or []:
                if isinstance(ref, dict) and isinstance(ref.get("path"), str):
                    target = posixpath.normpath(posixpath.join(here, ref["path"]))
                    target = target if target.endswith(".json") else posixpath.join(target, "tsconfig.json")
                    ref_base, ref_paths = self._options(target, 1)
                    base, paths = base or ref_base, paths or ref_paths
        return base, paths

    def for_dir(self, directory: str) -> tuple[str, str | None, dict[str, tuple[str, list[str]]]] | None:
        """The nearest config that sets `baseUrl` or `paths`, walking up from *directory*."""
        if directory in self._dirs:
            return self._dirs[directory]
        found = None
        if self.root is not None:
            for cur in _ancestors(directory):
                name = next((n for n in _CONFIG_NAMES if (self.root / cur / n).is_file()), None)
                if name:
                    base, paths = self._options(posixpath.join(cur, name))
                    if base is not None or paths:
                        found = (cur, base, paths)
                        break
        self._dirs[directory] = found
        return found


def _ancestors(directory: str) -> list[str]:
    parts = directory.split("/") if directory else []
    return ["/".join(parts[:i]) for i in range(len(parts), -1, -1)]


def _with_suffixes(stem: str) -> list[str]:
    out = [stem]
    out += [f"{stem}{ext}" for ext in JS_SUFFIXES]
    out += [f"{stem}{index}" for index in JS_INDEXES]
    for ext, swaps in _ESM_SWAPS.items():
        if stem.endswith(ext):
            out += [f"{stem[:-len(ext)]}{swap}" for swap in swaps]
    return out


def _match(pattern: str, module: str) -> str | None:
    """What `*` captured, "" for an exact pattern, None for no match."""
    if "*" not in pattern:
        return "" if pattern == module else None
    head, _, tail = pattern.partition("*")
    if module.startswith(head) and module.endswith(tail) and len(module) >= len(head) + len(tail):
        return module[len(head):len(module) - len(tail)]
    return None


def _configured_stems(module: str, config: tuple[str, str | None, dict[str, tuple[str, list[str]]]]) -> list[str]:
    _here, base, paths = config
    best = max((p for p in paths if _match(p, module) is not None),
               key=lambda p: len(p.split("*")[0]), default=None)
    stems: list[str] = []
    if best is not None:
        anchor, targets = paths[best]
        captured = _match(best, module) or ""
        stems += [posixpath.normpath(posixpath.join(anchor, t.replace("*", captured))) for t in targets]
    if base is not None:
        stems.append(posixpath.normpath(posixpath.join(base, module)))
    return stems


def js_candidates(module: str, from_path: str, configs: JsConfigs | None = None) -> list[str]:
    """Possible repo paths for a JS/TS import specifier, most specific first."""
    directory = posixpath.dirname(from_path)
    if module.startswith("."):
        stems = [posixpath.normpath(posixpath.join(directory, module))]
    else:
        stems = []
        config = configs.for_dir(directory) if configs else None
        if config:
            stems += _configured_stems(module, config)
        if module.startswith(("@/", "~/")):
            rest = module[2:]
            for prefix in _ancestors(directory):
                stems += [posixpath.join(prefix, s, rest) if prefix else posixpath.join(s, rest)
                          for s in ("src", "app", "")]
        stems = [posixpath.normpath(s) for s in stems]
    out: list[str] = []
    for stem in stems:
        out += [c for c in _with_suffixes(stem) if c not in out]
    return out
