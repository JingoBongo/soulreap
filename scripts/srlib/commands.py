"""The three commands: plan, skeleton, render."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from datetime import date
from pathlib import Path

from .diagrams import context_mermaid as _context_mermaid
from .diagrams import package_mermaid as _mermaid
from .facts import FileFacts, extract_source
from .graph import build_graph
from .walk import FileEntry, sha_of, walk

TEMPLATE_VERSION = 1
BATCH_MAX_FILES = 6
BATCH_MAX_LINES = 1500
ROLE_MARKERS = ("**Role.**", "**Role**", "**Роль.**", "**Роль**")


# ------------------------------------------------------------------ util ---


def _load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _git(root: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


RENDER_LOCK_STALE_SEC = 120


@contextlib.contextmanager
def _render_lock(out: Path):
    """Serialize render, so a second one can't corrupt cards mid-rewrite.

    `render` rewrites every card (the Dependencies block is read-modify-write) plus
    `.soulreap` and the manifest — whole-repo mutable state. The skill runs card
    writers in parallel; if one of them also runs `render` (it must not — see
    SKILL.md) two concurrent renders interleave on the same file and truncate it.
    An atomic mkdir is the lock; a stale one (a crashed render) is stolen after
    RENDER_LOCK_STALE_SEC so the tool never wedges.
    """
    lock = out / ".soulreap" / "render.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(600):  # ~60s of polling before giving up
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - os.path.getmtime(lock) > RENDER_LOCK_STALE_SEC:
                    os.rmdir(lock)
                    continue
            time.sleep(0.1)
    else:
        raise RuntimeError(
            f"render is already running (lock held at {lock}). If no other render is "
            f"active, delete that directory. Never run render from a card worker — "
            f"only once, from the parent, after all cards are written."
        )
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.rmdir(lock)


def _lint_mermaid(out: Path) -> list[str]:
    """Catch the mermaid footguns render or a hand-written diagram can emit.

    Not a parser — two targeted checks for what actually shipped broken:
      * a ``;`` inside a sequenceDiagram message (mermaid reads it as a statement
        separator, so the diagram fails to parse), and
      * a backtick inside a node label (a URL pulled from a docstring code span).
    """
    warnings: list[str] = []
    diagrams = out / "diagrams"
    if not diagrams.is_dir():
        return warnings
    arrow = ("->>", "-->>", "->)", "->", "-->", "--x", "-x", "-)")
    for mmd in sorted(diagrams.glob("*.mmd")):
        text = mmd.read_text(encoding="utf-8", errors="replace")
        is_sequence = "sequenceDiagram" in text
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("%%") or stripped.startswith("title:"):
                continue
            if is_sequence and ":" in stripped and any(a in stripped for a in arrow):
                message = stripped.split(":", 1)[1]
                if ";" in message:
                    warnings.append(
                        f"{mmd.name}:{i}: ';' in a sequence message -- mermaid treats it "
                        f"as a statement separator and fails to parse; use ',' or '#59;'"
                    )
            if "`" in stripped and ("[" in stripped or "(" in stripped):
                warnings.append(f"{mmd.name}:{i}: backtick in a node label — sanitize it")
            if not is_sequence and stripped.count("|") >= 2:
                first, last = stripped.find("|"), stripped.rfind("|")
                label = stripped[first + 1 : last]
                if "(" in label or ")" in label:
                    warnings.append(
                        f"{mmd.name}:{i}: '()' in a flowchart edge label — mermaid fails to "
                        f"parse parens between |...| (node labels are fine); drop them or use "
                        f"'#40;'/'#41;'"
                    )
    return warnings


def card_path(out: Path, rel: str) -> Path:
    """Where the card for a source file lives."""
    return out / "files" / (rel + ".md")


def diff_statuses(entries: list[FileEntry], manifest: dict, template_version: int) -> dict[str, str]:
    """Compare on-disk hashes with the manifest.

    Returns ``path -> new | dirty | clean | orphan``. A template bump dirties
    everything, because cards written against an older template no longer
    match the ones being produced now.
    """
    recorded = manifest.get("files", {}) if isinstance(manifest, dict) else {}
    stale_template = manifest.get("template_version") != template_version
    statuses: dict[str, str] = {}

    for entry in entries:
        known = recorded.get(entry.path)
        if known is None:
            statuses[entry.path] = "new"
        elif stale_template or known.get("source_sha") != entry.sha:
            statuses[entry.path] = "dirty"
        else:
            statuses[entry.path] = "clean"

    seen = {entry.path for entry in entries}
    for path in recorded:
        if path not in seen:
            statuses[path] = "orphan"
    return statuses


def _in_scope(path: str, scope: list[str]) -> bool:
    if not scope:
        return True
    return any(path == s or path.startswith(s.rstrip("/") + "/") for s in scope)


def _batches(paths: list[str], line_counts: dict[str, int]) -> list[list[str]]:
    """Pack work into subagent-sized batches.

    Sorted order keeps files from the same directory adjacent, so a batch
    usually sees related code. Packing across directory boundaries matters
    more than purity: a repo of one-file packages would otherwise spawn one
    agent per file.
    """
    out: list[list[str]] = []
    current: list[str] = []
    current_lines = 0

    for path in sorted(paths):
        lines = line_counts.get(path, 0)
        too_many = len(current) >= BATCH_MAX_FILES
        too_long = bool(current) and current_lines + lines > BATCH_MAX_LINES
        if too_many or too_long:
            out.append(current)
            current, current_lines = [], 0
        current.append(path)
        current_lines += lines

    if current:
        out.append(current)
    return out


def _tree(entries: list[FileEntry]) -> dict:
    tree: dict[str, dict] = {}
    for entry in entries:
        parent = str(Path(entry.path).parent)
        node = tree.setdefault(parent, {"files": 0, "lines": 0, "langs": []})
        node["files"] += 1
        node["lines"] += entry.lines
        if entry.lang and entry.lang not in node["langs"]:
            node["langs"].append(entry.lang)
    return dict(sorted(tree.items()))


# ------------------------------------------------------------------ plan ---


def cmd_plan(root: Path | str, out: Path | str, paths=(), since: str | None = None, force: bool = False) -> dict:
    """Scan the repo, diff against the manifest, write plan.json + facts.json."""
    root, out = Path(root), Path(out)
    scope = [str(Path(p).as_posix()).rstrip("/") for p in (paths or [])]

    entries = walk(root)
    code = [e for e in entries if e.kind == "code"]

    facts: dict[str, FileFacts] = {}
    for entry in code:
        text = (root / entry.path).read_text(encoding="utf-8", errors="replace")
        item = extract_source(text, entry.path)
        item.sha = entry.sha
        facts[entry.path] = item

    graph = build_graph(facts)
    manifest = _load_json(out / "manifest.json", {})
    statuses = diff_statuses(code, manifest, TEMPLATE_VERSION)

    if force:
        statuses = {p: ("orphan" if s == "orphan" else "dirty" if s != "new" else "new") for p, s in statuses.items()}
    if since:
        changed = _git(root, "diff", "--name-only", f"{since}...HEAD")
        if changed is not None:
            touched_by_git = set(changed.splitlines())
            for path, status in list(statuses.items()):
                if status in {"dirty", "clean"} and path not in touched_by_git:
                    statuses[path] = "clean"
                elif status == "clean" and path in touched_by_git:
                    statuses[path] = "dirty"

    recorded = manifest.get("files", {})
    for path, status in list(statuses.items()):
        if status == "orphan":
            continue
        card = card_path(out, path)
        if not card.exists():
            # A recorded hash without a card means the card was never written
            # or was deleted. The source is undocumented either way.
            statuses[path] = "new"
        elif status == "new":
            # Card on disk but no manifest entry: a worker wrote it this run and
            # render has not indexed it yet. Report "carded" so a re-plan shows real
            # progress and does not re-delegate it; the next render folds it in.
            # (A "dirty" source keeps its status — its existing card is for the old
            # source and must be rewritten.)
            statuses[path] = "carded"
        elif status == "clean":
            expected = recorded.get(path, {}).get("card_sha")
            if expected and sha_of(card) != expected:
                statuses[path] = "edited"

    hot = {p for p, s in statuses.items() if s in {"new", "dirty"}}
    for path, status in list(statuses.items()):
        if status == "clean" and any(dep in hot for dep in graph.imports.get(path, [])):
            statuses[path] = "touched"

    listed = [
        {
            "path": path,
            "status": status,
            "lang": facts[path].lang if path in facts else None,
            "lines": facts[path].lines if path in facts else 0,
            "sha": facts[path].sha if path in facts else None,
            "card": str(card_path(out, path).relative_to(out)),
        }
        for path, status in sorted(statuses.items())
        if status == "orphan" or _in_scope(path, scope)
    ]

    counts: dict[str, int] = {}
    for item in listed:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    for key in ("new", "dirty", "carded", "clean", "touched", "edited", "orphan"):
        counts.setdefault(key, 0)

    work = [i["path"] for i in listed if i["status"] in {"new", "dirty"}]
    non_code: dict[str, list[str]] = {}
    for entry in entries:
        if entry.kind == "non_code":
            non_code.setdefault(entry.category, []).append(entry.path)
    for key in non_code:
        non_code[key].sort()

    payload = {
        "template_version": TEMPLATE_VERSION,
        "root": str(root),
        "commit": _git(root, "rev-parse", "--short", "HEAD"),
        "generated_at": date.today().isoformat(),
        "scope": scope,
        "counts": counts,
        "files": listed,
        "batches": _batches(work, {p: facts[p].lines for p in facts}),
        "tree": _tree(entries),
        "non_code": non_code,
        "secrets": {e.path: e.env_keys for e in entries if e.kind == "secret"},
        "cycles": graph.cycles,
    }

    serialised = {}
    for path, item in facts.items():
        blob = item.to_dict()
        blob["deps"] = graph.imports.get(path, [])
        blob["imported_by"] = graph.imported_by.get(path, [])
        targets = graph.resolved.get(path, {})
        for item in blob.get("imports", []):
            item["target"] = targets.get(item["module"])
        serialised[path] = blob

    _write_json(out / ".soulreap" / "plan.json", payload)
    _write_json(out / ".soulreap" / "facts.json", serialised)
    return payload


# -------------------------------------------------------------- skeleton ---


def _entity_lines(blob: dict) -> list[str]:
    lines: list[str] = []
    for const in blob.get("constants", []):
        lines.append(f"  const {const['name']} = {const['value']}  [L{const['line']}]")
    for cls in blob.get("classes", []):
        bases = f"({', '.join(cls.get('bases', []))})" if cls.get("bases") else ""
        doc = f"  — {cls['doc']}" if cls.get("doc") else ""
        lines.append(f"  class {cls['name']}{bases}  [L{cls['start_line']}-L{cls['end_line']}]{doc}")
        for method in cls.get("methods", []):
            prefix = "async " if method.get("is_async") and not method["signature"].startswith("async") else ""
            mdoc = f"  — {method['doc']}" if method.get("doc") else ""
            lines.append(
                f"    {prefix}{method['signature']}  [L{method['start_line']}-L{method['end_line']}]{mdoc}"
            )
    for fn in blob.get("functions", []):
        signature = fn["signature"]
        prefix = "async " if fn.get("is_async") and not signature.startswith("async") else ""
        marks = []
        if fn.get("is_generator"):
            marks.append("generator")
        if fn["name"] in blob.get("recursive", []):
            marks.append("recursive")
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        doc = f"  — {fn['doc']}" if fn.get("doc") else ""
        lines.append(
            f"  {prefix}{signature}  [L{fn['start_line']}-L{fn['end_line']}]{suffix}{doc}"
        )
    return lines


def cmd_skeleton(root: Path | str, out: Path | str, target: str) -> str:
    """Print the deterministic skeleton a card writer needs for one file."""
    out = Path(out)
    facts = _load_json(out / ".soulreap" / "facts.json", {})
    blob = facts.get(target)
    if blob is None:
        message = f"no facts for {target}; run `soulreap.py plan` first"
        print(message)
        return message

    lines = [f"# {target}", f"lang: {blob.get('lang')}  lines: {blob.get('lines')}"]
    if blob.get("doc"):
        lines.append(f"module docstring: {blob['doc']}")
    if blob.get("parse_error"):
        lines.append(f"PARSE ERROR: {blob['parse_error']}")

    flags = []
    if blob.get("concurrency"):
        flags.append("concurrency: " + ", ".join(blob["concurrency"]))
    if blob.get("recursive"):
        flags.append("recursive: " + ", ".join(blob["recursive"]))
    if blob.get("io"):
        flags.append("io: " + "; ".join(f"{k} ({', '.join(v)})" for k, v in blob["io"].items()))
    if flags:
        lines.append("")
        lines.append("## Facts")
        lines += [f"  {f}" for f in flags]

    lines.append("")
    lines.append("## Imports")
    for item in blob.get("imports", []):
        names = f" ({', '.join(item['names'])})" if item.get("names") else ""
        lines.append(f"  {item['module']}{names}  [L{item.get('line', 0)}]")
    lines.append("  internal deps: " + (", ".join(blob.get("deps", [])) or "none"))
    lines.append("  imported by: " + (", ".join(blob.get("imported_by", [])) or "nobody"))

    lines.append("")
    lines.append("## Entities")
    lines += _entity_lines(blob) or ["  (none)"]

    if blob.get("todos"):
        lines.append("")
        lines.append("## Markers")
        for todo in blob["todos"]:
            lines.append(f"  {todo['kind']} [L{todo['line']}] {todo['text']}")

    text = "\n".join(lines)
    print(text)
    return text


# ---------------------------------------------------------------- render ---


def _relative_link(target: Path, from_dir: Path) -> str:
    """POSIX relative path from *from_dir* to *target*, for a markdown link."""
    return os.path.relpath(target.resolve(), from_dir.resolve()).replace(os.sep, "/")


def _ensure_source_link(card: Path, target: str) -> None:
    """Keep a link back to the source file directly under the card's heading.

    The path is computed, never written by hand: a subagent counting ``../``
    segments would get it wrong, and a moved output directory would silently
    break every link. Rewriting is idempotent — the line is stripped and
    reinserted, so running render twice changes nothing.
    """
    try:
        text = card.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    kept = [ln for ln in text.splitlines() if not ln.lstrip().startswith("[source](")]

    insert_at = 0
    for index, line in enumerate(kept):
        if line.startswith("# "):
            insert_at = index + 1
            break

    head, tail = kept[:insert_at], kept[insert_at:]
    while tail and not tail[0].strip():
        tail.pop(0)
    updated = "\n".join([*head, "", f"[source]({target})", "", *tail]).lstrip("\n") + "\n"
    if updated != text:
        card.write_text(updated, encoding="utf-8")


def _dependency_block(root: Path, out: Path, card: Path, path: str, blob: dict) -> str:
    """The Dependencies section, composed entirely from parsed facts.

    Written by render rather than by a subagent: which imports are standard
    library, which are third-party and which resolve inside the repo is decided
    by a parser, and a model asked to sort them drops whole categories — the
    first run of this skill silently lost every stdlib import on files whose
    only dependencies were stdlib.
    """
    imports = blob.get("imports", [])
    stdlib = sorted({i["module"] for i in imports if i.get("stdlib") and not i.get("target")})
    third = sorted({i["module"] for i in imports if not i.get("stdlib") and not i.get("target")})

    internal: list[str] = []
    for item in sorted(imports, key=lambda i: i["module"]):
        target = item.get("target")
        if not target:
            continue
        link = _relative_link(card_path(out, target), card.parent)
        names = ", ".join(f"`{n}`" for n in item.get("names", []))
        internal.append(f"[{target}]({link})" + (f" → {names}" if names else ""))

    users = [
        f"[{user}]({_relative_link(card_path(out, user), card.parent)})"
        for user in blob.get("imported_by", [])
    ]

    def render(items: list[str], code: bool = False) -> str:
        if not items:
            return "none"
        return ", ".join(f"`{i}`" for i in items) if code else ", ".join(items)

    lines = [
        "## Dependencies",
        "",
        f"- **Standard library:** {render(stdlib, code=True)}",
        f"- **Third-party:** {render(third, code=True)}",
    ]
    # One entry per line: comma-joining them runs the "→ names" of one entry
    # into the start of the next, and the list is unreadable past two items.
    if internal:
        lines.append("- **Internal:**")
        lines += [f"  - {entry}" for entry in internal]
    else:
        lines.append("- **Internal:** none")
    if users:
        lines.append("- **Imported by:**")
        lines += [f"  - {entry}" for entry in users]
    else:
        lines.append("- **Imported by:** nobody")
    return "\n".join(lines)


def _ensure_dependency_block(card: Path, block: str) -> None:
    """Replace the card's Dependencies section, or insert it before the next one."""
    try:
        text = card.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    lines = text.splitlines()

    start = next((i for i, ln in enumerate(lines) if ln.strip().lower() == "## dependencies"), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    else:
        start = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), len(lines))
        end = start

    tail = lines[end:]
    while tail and not tail[0].strip():
        tail.pop(0)
    head = lines[:start]
    while head and not head[-1].strip():
        head.pop()

    updated = "\n".join([*head, "", *block.splitlines(), "", *tail]).lstrip("\n") + "\n"
    if updated != text:
        card.write_text(updated, encoding="utf-8")


def _role_of(card: Path) -> str:
    """The Role sentence, including continuation lines up to the blank line."""
    try:
        content = card.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    collected: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if collected:
            if not stripped or stripped.startswith(("#", "|", "-", "*")):
                break
            collected.append(stripped)
            continue
        for marker in ROLE_MARKERS:
            if stripped.startswith(marker):
                collected.append(stripped[len(marker):].strip())
                break
    return " ".join(collected).strip()


def cmd_render(root: Path | str, out: Path | str) -> dict:
    """Rebuild INDEX.md, the module diagram and manifest.json."""
    root, out = Path(root), Path(out)
    with _render_lock(out):
        return _render_locked(root, out)


def _render_locked(root: Path, out: Path) -> dict:
    facts = _load_json(out / ".soulreap" / "facts.json", {})

    rows = []
    manifest_files = {}
    missing = []
    for path in sorted(facts):
        blob = facts[path]
        card = card_path(out, path)
        exists = card.exists()
        if exists:
            _ensure_source_link(card, _relative_link(root / path, card.parent))
            _ensure_dependency_block(card, _dependency_block(root, out, card, path, blob))
        role = _role_of(card) if exists else ""
        if not exists:
            missing.append(path)
        link = f"files/{path}.md"
        source = _relative_link(root / path, out)
        rows.append(
            f"| [{path}]({link}) · [src]({source}) | {blob.get('lang') or ''} | "
            f"{blob.get('lines', 0)} | {role or '_no card yet_'} |"
        )
        manifest_files[path] = {
            "source_sha": blob.get("sha"),
            "card_sha": sha_of(card) if exists else None,
            "lines": blob.get("lines", 0),
            "lang": blob.get("lang"),
            "generated_at": date.today().isoformat(),
        }

    header = [
        f"# Index — {len(facts)} files",
        "",
        f"Commit `{_git(root, 'rev-parse', '--short', 'HEAD') or 'n/a'}` · generated {date.today().isoformat()}",
        "",
        "| File | Lang | Lines | Role |",
        "| --- | --- | --- | --- |",
    ]
    (out).mkdir(parents=True, exist_ok=True)
    (out / "INDEX.md").write_text("\n".join(header + rows) + "\n", encoding="utf-8")

    (out / "diagrams").mkdir(parents=True, exist_ok=True)
    packages, stats = _mermaid(facts)
    (out / "diagrams" / "packages.mmd").write_text(packages, encoding="utf-8")
    context, context_stats = _context_mermaid(facts)
    (out / "diagrams" / "context.mmd").write_text(context, encoding="utf-8")
    stats.update(context_stats)
    # flow.mmd and architecture.mmd are written by hand — the request order and the
    # runtime topology are not derivable from imports. render never touches them, it
    # only reports whether they exist (and lints them below).
    stats["flow_present"] = (out / "diagrams" / "flow.mmd").exists()
    stats["architecture_present"] = (out / "diagrams" / "architecture.mmd").exists()

    manifest = {
        "template_version": TEMPLATE_VERSION,
        "commit": _git(root, "rev-parse", "--short", "HEAD"),
        "generated_at": date.today().isoformat(),
        "files": manifest_files,
    }
    _write_json(out / "manifest.json", manifest)
    stats["mermaid_warnings"] = _lint_mermaid(out)
    return {"cards": len(facts) - len(missing), "missing": missing, **stats}
