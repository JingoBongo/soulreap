"""Repository walking, file classification and hashing.

Nothing here calls an LLM or the network. It answers one question only:
which files exist, what are they, and have they changed.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE_BYTES = 1_000_000

DENY_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", ".env.d",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "dist", "build", "out", ".next", ".nuxt", ".svelte-kit", ".turbo",
    "target", "vendor", "site-packages", ".terraform", ".serverless",
    "coverage", "htmlcov", ".idea", ".vscode", ".gradle", ".cache",
    "soulreap",
}
ALLOWED_HIDDEN_DIRS = {".github", ".gitlab", ".circleci", ".claude", ".husky"}

LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "uv.lock", "Cargo.lock", "Gemfile.lock", "composer.lock", "go.sum",
    "Pipfile.lock", "bun.lockb",
}

BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf", ".zip",
    ".gz", ".tar", ".whl", ".so", ".dylib", ".dll", ".exe", ".bin", ".jar",
    ".class", ".pyc", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp4",
    ".mp3", ".wav", ".avif", ".db", ".sqlite", ".sqlite3",
}

CODE_LANGS = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".cs": "csharp", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
    ".hpp": "cpp", ".swift": "swift", ".scala": "scala", ".lua": "lua",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
}

_CI_MARKERS = (".github/workflows", ".gitlab-ci", ".circleci", "jenkinsfile", "azure-pipelines")
_DOCKER_NAMES = ("dockerfile", "docker-compose", ".dockerignore", "containerfile")
_IAC_EXT = {".tf", ".tfvars", ".hcl"}
_DOC_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc"}
_CONFIG_EXT = {".yml", ".yaml", ".toml", ".ini", ".cfg", ".json", ".conf", ".properties", ".xml"}
_DATA_EXT = {".csv", ".tsv", ".jsonl", ".ndjson", ".parquet"}
_STYLE_EXT = {".css", ".scss", ".sass", ".less"}


@dataclass
class FileEntry:
    """One file found in the repository."""

    path: str
    sha: str = ""
    lines: int = 0
    kind: str = "code"
    lang: str | None = None
    category: str = "code"
    size: int = 0
    env_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serializable form. Never contains file content."""
        return {
            "path": self.path, "sha": self.sha, "lines": self.lines,
            "kind": self.kind, "lang": self.lang, "category": self.category,
            "size": self.size, "env_keys": self.env_keys,
        }


def classify(path: Path | str) -> tuple[str, str | None, str]:
    """Return ``(kind, lang, category)`` for a repository-relative path.

    kind is one of ``code``, ``non_code``, ``secret`` or ``skip``.
    """
    path = Path(path)
    name = path.name
    lower = name.lower()
    suffix = path.suffix.lower()
    posix = path.as_posix().lower()

    if lower == ".env" or lower.startswith(".env.") or lower.endswith(".env"):
        return "secret", None, "secret"
    if lower.endswith((".pem", ".key", ".p12", ".pfx", ".keystore")):
        return "secret", None, "secret"
    if name in LOCKFILES or suffix in BINARY_EXT or ".min." in lower:
        return "skip", None, "skip"
    if suffix in CODE_LANGS:
        return "code", CODE_LANGS[suffix], "code"

    if any(marker in posix for marker in _CI_MARKERS):
        return "non_code", None, "ci"
    if any(lower.startswith(d) or lower == d for d in _DOCKER_NAMES):
        return "non_code", None, "docker"
    if suffix in _IAC_EXT:
        return "non_code", None, "iac"
    if suffix == ".sql" or "migration" in posix or "alembic" in posix:
        return "non_code", None, "migration"
    if suffix in _DOC_EXT:
        return "non_code", None, "docs"
    if suffix in _STYLE_EXT:
        return "non_code", None, "style"
    if suffix in _DATA_EXT:
        return "non_code", None, "data"
    if suffix in _CONFIG_EXT or lower in {"makefile", "justfile", "procfile"}:
        return "non_code", None, "config"
    return "non_code", None, "other"


def _has_pathspec() -> bool:
    try:
        import pathspec  # noqa: F401
    except ImportError:  # pragma: no cover - only without the declared dependency
        return False
    return True


class _Ignore:
    """gitignore matcher. Uses pathspec when available, fnmatch otherwise.

    The fallback does not implement negation (``!pattern``), so a repository
    that re-includes a path git would keep will lose files. That is why running
    without pathspec is a warning, not a silent degradation.
    """

    def __init__(self) -> None:
        self._specs: list[tuple[str, object]] = []
        self._pathspec = _has_pathspec()

    def add(self, dir_rel: str, lines: list[str]) -> None:
        """Register the patterns of one .gitignore, scoped to its directory."""
        if self._pathspec:
            import pathspec

            try:
                spec = pathspec.PathSpec.from_lines("gitignore", lines)
            except (KeyError, ValueError):  # older pathspec
                spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
            self._specs.append((dir_rel, spec))
        else:  # pragma: no cover
            cleaned = [ln.strip().rstrip("/") for ln in lines if ln.strip() and not ln.startswith("#")]
            self._specs.append((dir_rel, cleaned))

    def matches(self, rel: str, is_dir: bool = False) -> bool:
        """True when *rel* (repo-relative posix path) is ignored."""
        for dir_rel, spec in self._specs:
            if dir_rel and not rel.startswith(dir_rel + "/"):
                continue
            local = rel[len(dir_rel) + 1:] if dir_rel else rel
            local_probe = local + "/" if is_dir else local
            if self._pathspec:
                if spec.match_file(local_probe) or spec.match_file(local):  # type: ignore[union-attr]
                    return True
            else:  # pragma: no cover
                from fnmatch import fnmatch

                base = local.rsplit("/", 1)[-1]
                if any(fnmatch(base, p) or fnmatch(local, p) or local.startswith(p + "/") for p in spec):
                    return True
        return False


def _read(path: Path) -> bytes | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data


def _count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


_ENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _env_keys(text: str) -> list[str]:
    """Key names only. Values are never read out of a secret file."""
    keys: list[str] = []
    for line in text.splitlines():
        match = _ENV_KEY.match(line)
        if match and match.group(1) not in keys:
            keys.append(match.group(1))
    return keys


def walk(root: Path | str) -> list[FileEntry]:
    """Walk *root*, honouring .gitignore and the built-in denylist."""
    root = Path(root)
    if not _has_pathspec():
        print(
            "soulreap: pathspec is not installed — falling back to a matcher that "
            "ignores gitignore negation (!pattern). Files git would keep may be "
            "dropped. Run this script through `uv run --script` so its declared "
            "dependencies are present.",
            file=sys.stderr,
        )
    ignore = _Ignore()
    entries: list[FileEntry] = []

    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dir_rel = here.relative_to(root).as_posix()
        dir_rel = "" if dir_rel == "." else dir_rel

        if ".gitignore" in filenames:
            with contextlib.suppress(OSError):
                ignore.add(dir_rel, (here / ".gitignore").read_text(encoding="utf-8", errors="replace").splitlines())

        keep: list[str] = []
        for name in sorted(dirnames):
            if name in DENY_DIRS:
                continue
            if name.startswith(".") and name not in ALLOWED_HIDDEN_DIRS:
                continue
            child_rel = f"{dir_rel}/{name}" if dir_rel else name
            if ignore.matches(child_rel, is_dir=True):
                continue
            keep.append(name)
        dirnames[:] = keep

        for name in sorted(filenames):
            rel = f"{dir_rel}/{name}" if dir_rel else name
            if ignore.matches(rel):
                continue
            kind, lang, category = classify(Path(rel))
            if kind == "skip":
                continue

            full = here / name
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue
            data = _read(full)
            if data is None:
                continue

            text = data.decode("utf-8", errors="replace")
            entry = FileEntry(
                path=rel,
                sha=hashlib.sha256(data).hexdigest(),
                lines=_count_lines(text),
                kind=kind,
                lang=lang,
                category=category,
                size=size,
            )
            if kind == "secret":
                entry.env_keys = _env_keys(text)
            entries.append(entry)

    return entries


def sha_of(path: Path) -> str | None:
    """sha256 of a file, or None when it is unreadable."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
