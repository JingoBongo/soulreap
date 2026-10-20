"""Which functions carry a repo — scored from facts and file text, no LLM, stdlib only.

Fitted in research/importance (95 repos, 23.8k functions, labels from readers who read each repo
whole): a weighted sum ("rule") of size, use across files, surface and place, plus two cheap
signals — a random walk on the reversed call graph (functions that drive much of the code) and a
TF-IDF cosine of the body with the repo's own docs. The council is the mean of the rule's and the
docs similarity's within-repo ranks: per-repo AUC 0.83 against the readers, 0.79 against the gold
question anchors (size alone: 0.78 / 0.75). Hazards, I/O and raw call counts predicted nothing.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from .paths import is_test_path

# rule_lite fitted on y_needed_direct, all repos, as weight / std (the mean term is a per-repo
# constant: it cannot change a ranking inside one repo). `_pct` = within-repo percentile.
WEIGHTS = {
    "lines_pct": 2.1832, "fan_in_files_pct": 1.4352, "calls_into_repo_pct": -0.1924,
    "file_imported_by_pct": 0.3098, "exported": 0.7562, "name_defined_times": -0.0567,
    "path_depth": -0.3747, "file_funcs": -0.0227, "lang_tsx": -0.8776, "deco_route": 0.5138,
    "under_main": -0.1165, "in_scripts": -0.3250, "in_examples": -0.5561, "in_poc": -1.6416,
    "pr_rev_pct": 1.4226, "doc_sim_local_pct": 1.4739,
}
DIR_FLAGS = {
    "in_scripts": {"scripts", "script", "tools", "bin", "utils", "util"},
    "in_examples": {"examples", "example", "samples", "sample", "demo", "demos", "docs", "doc", "notebooks"},
    "in_poc": {"poc", "prototype", "sandbox", "experiments", "experiment", "legacy", "old", "archive", "scratch"},
}
ROUTE_DECOS = {"get", "post", "put", "delete", "patch", "route", "websocket", "api_route", "api_view", "head",
               "options", "GET", "POST", "PUT", "PATCH", "DELETE"}
DOC_EXT = {".md", ".rst", ".txt", ".mdx"}
GENERATED_DOC_DIRS = {"soulreap", "units", "codemap", "code_map", ".soulreap"}
DOC_MAX_BYTES = 2_000_000
REASON_PCT = 0.8  # a signal is named as a reason when the function is in its top 20%
REASON_LINES = 20  # …and size only from this many lines: in a tiny repo the top 20% is three lines
# A name defined more often than this (`get`, `run`, `main` across a monorepo) links no calls: every
# call would fan out to every definition, callers x definitions edges for a name that says nothing.
FANOUT_CAP = 50
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
STOP = {"self", "cls", "def", "return", "none", "true", "false", "import", "from", "for", "while", "if", "else",
        "elif", "try", "except", "finally", "with", "as", "and", "or", "not", "in", "is", "lambda", "yield",
        "async", "await", "class", "pass", "raise", "const", "let", "var", "function", "new", "this", "null",
        "undefined", "export", "default", "type", "interface", "the", "get", "set", "str", "int", "dict", "list",
        "any", "bool", "len", "print", "string", "number", "void", "of", "to", "a", "an", "be", "it"}


def words(text: str) -> list[str]:
    """Identifier sub-words: `loadVaultConfig` / `load_vault_config` → load, vault, config."""
    out = []
    for ident in WORD.findall(text):
        for part in ident.split("_"):
            out += [w.lower() for w in CAMEL.findall(part)]
    return [w for w in out if len(w) > 2 and w not in STOP]


def doc_paths(paths) -> list[str]:
    """The repo's own prose (README, docs/): not generated maps, not tests."""
    return sorted(p for p in paths if PurePosixPath(p).suffix.lower() in DOC_EXT
                  and not GENERATED_DOC_DIRS & set(p.split("/")[:-1]) and not is_test_path(p))


def _symbols(blob: dict):
    for fn in blob.get("functions") or []:
        yield fn["name"], fn["name"], None, fn
    for cls in blob.get("classes") or []:
        for method in cls.get("methods") or []:
            yield f"{cls['name']}.{method['name']}", method["name"], cls["name"], method


def _deco(text: str) -> str:
    return text.split("(", 1)[0].split(".")[-1].strip()


def percentiles(values: list[float]) -> list[float]:
    """Average-rank percentile (pandas `rank(pct=True)`): ties share their mean rank."""
    order = sorted(range(len(values)), key=values.__getitem__)
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j + 2) / 2 / len(values)
        i = j + 1
    return out


def pagerank(n: int, edges: dict[tuple[int, int], float], alpha: float = 0.85, iters: int = 100,
             tol: float = 1e-6) -> list[float]:
    """Stationary share of a random walk along weighted *edges* (networkx's PageRank, uniform restart)."""
    if not n:
        return []
    out_w: dict[int, float] = defaultdict(float)
    inbound: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for (a, b), w in edges.items():
        out_w[a] += w
        inbound[b].append((a, w))
    x = [1.0 / n] * n
    for _ in range(iters):
        dangling = sum(x[i] for i in range(n) if not out_w.get(i))
        nxt = [(1 - alpha) / n + alpha * dangling / n + alpha * sum(x[a] * w / out_w[a] for a, w in inbound[i])
               for i in range(n)]
        done = sum(abs(a - b) for a, b in zip(nxt, x, strict=True)) < n * tol
        x = nxt
        if done:
            break
    return x


def _read_lines(root: Path | None, rel: str) -> list[str]:
    if root is None:  # facts only: sizes, calls and places still score; the docs vote is silent
        return []
    try:
        # split on "\n" only: splitlines() also breaks on form feeds, and the parsers' line numbers do not
        return (root / rel).read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return []


def _doc_sim(rows: list[dict], doc_text: str) -> list[float]:
    """Cosine of each body's TF-IDF vector with the docs', word rarity counted across this repo's files."""
    files: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        files[r["file"]] |= set(r["words"])
    df = Counter(w for ws in files.values() for w in ws)
    idf = {w: math.log((1 + len(files)) / (1 + c)) + 1 for w, c in df.items()}
    doc = {w: math.log1p(c) * idf.get(w, 0.0) for w, c in Counter(words(doc_text)).items()}
    doc_norm = math.sqrt(sum(v * v for v in doc.values())) or 1.0
    out = []
    for r in rows:
        vec = {w: math.log1p(c) * idf[w] for w, c in Counter(r["words"]).items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        out.append(sum(v * doc.get(w, 0.0) for w, v in vec.items()) / norm / doc_norm)
    return out


def _rows(root: Path | None, facts: dict, pool: list[str]) -> list[dict]:
    rows = []
    for rel in pool:
        blob = facts.get(rel) or {}
        lines = _read_lines(root, rel)
        symbols = list(_symbols(blob))
        dirs = set(rel.lower().split("/")[:-1])
        main_calls = set(blob.get("main_calls") or [])
        for qual, name, owner, fn in symbols:
            start, end = fn["start_line"], fn["end_line"]
            # Python: public by name (methods too); TS/JS: an `export`ed top-level function.
            exported = not name.startswith("_") if blob.get("lang") == "python" else bool(fn.get("exported"))
            rows.append({
                "key": f"{rel}::{qual}", "file": rel, "name": name, "lines": end - start + 1,
                "callees": set(fn.get("callees") or []), "words": words("\n".join(lines[start - 1:end])),
                "exported": int(exported),
                "deco_route": int(any(_deco(d) in ROUTE_DECOS for d in fn.get("decorators") or [])),
                "under_main": int(owner is None and name in main_calls),
                "lang_tsx": int(rel.endswith(".tsx")), "path_depth": rel.count("/"), "file_funcs": len(symbols),
                "file_imported_by": len([p for p in blob.get("imported_by") or [] if not is_test_path(p)]),
                **{flag: int(bool(dirs & names)) for flag, names in DIR_FLAGS.items()},
            })
    return rows


def _graph(rows: list[dict]) -> None:
    """Fan-in by files, calls into the repo, and the reversed-call random walk, onto *rows*."""
    by_name: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_name[r["name"]].append(i)
    callers_files: dict[str, set[str]] = defaultdict(set)  # name → files whose functions call it
    for r in rows:
        for callee in r["callees"]:
            callers_files[callee].add(r["file"])
    edges: dict[tuple[int, int], float] = defaultdict(float)
    for i, r in enumerate(rows):
        for callee in r["callees"]:
            defs = by_name.get(callee) or []
            local = [j for j in defs if rows[j]["file"] == r["file"]]
            if not local and len(defs) > FANOUT_CAP:
                continue
            targets = [j for j in (local or defs) if j != i]
            for j in targets:
                edges[(j, i)] += 1 / len(targets)  # reversed: walk from callee to caller
    reach = pagerank(len(rows), edges)
    for i, r in enumerate(rows):
        r["fan_in_files"] = len(callers_files.get(r["name"], set()) - {r["file"]})
        r["calls_into_repo"] = sum(1 for c in r["callees"] if c in by_name)
        r["name_defined_times"] = len(by_name[r["name"]])
        r["pr_rev"] = reach[i]


def _reasons(r: dict) -> list[str]:
    out = []
    if r["lines_pct"] >= REASON_PCT and r["lines"] >= REASON_LINES:
        out.append(f"{r['lines']} lines")
    if r["pr_rev_pct"] >= REASON_PCT:
        out.append("drives much of the code")
    if r["fan_in_files"] >= 2 and r["fan_in_files_pct"] >= REASON_PCT:
        out.append(f"called from {r['fan_in_files']} files")
    if r["doc_sim"] > 0 and r["doc_sim_local_pct"] >= REASON_PCT:
        out.append("what the docs talk about")
    if r["deco_route"]:
        out.append("route")
    return out


def score_symbols(root: Path | str | None, facts: dict, pool: list[str], docs: list[str] = ()) -> dict[str, dict]:
    """`file::Qual` → {rule, docs, council (0..1, within this repo), reasons, lines} for every
    function and method in *pool*'s files. *docs* are repo-relative prose paths (`doc_paths`);
    *root* None scores from facts alone."""
    root = Path(root) if root is not None else None
    rows = _rows(root, facts, pool)
    if not rows:
        return {}
    _graph(rows)
    doc_text = "\n".join("\n".join(_read_lines(root, d)) for d in docs if root is not None
                         and (root / d).is_file() and (root / d).stat().st_size < DOC_MAX_BYTES)
    for r, sim in zip(rows, _doc_sim(rows, doc_text), strict=True):
        r["doc_sim"] = sim
    for name, source in (("lines_pct", "lines"), ("fan_in_files_pct", "fan_in_files"),
                         ("calls_into_repo_pct", "calls_into_repo"), ("file_imported_by_pct", "file_imported_by"),
                         ("pr_rev_pct", "pr_rev"), ("doc_sim_local_pct", "doc_sim")):
        for r, p in zip(rows, percentiles([r[source] for r in rows]), strict=True):
            r[name] = p
    rule = [sum(w * r[f] for f, w in WEIGHTS.items()) for r in rows]
    # Two equal votes, re-ranked so the council spans 0..1 even when the docs vote is silent (no
    # docs: every function ties at 0.5, and a plain mean would cap the council at 0.75).
    council = percentiles([a + b for a, b in zip(percentiles(rule), [r["doc_sim_local_pct"] for r in rows],
                                                 strict=True)])
    return {r["key"]: {"rule": round(s, 4), "docs": round(r["doc_sim"], 4), "council": round(c, 4),
                       "reasons": _reasons(r), "lines": r["lines"]}
            for r, s, c in zip(rows, rule, council, strict=True)}
