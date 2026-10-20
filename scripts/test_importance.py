"""Tests for the importance score: the study's signals, computed from facts and file text."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib import importance as imp
from srlib.build import build_facts


def _repo(tmp_path: Path, files: dict[str, str]) -> dict:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return build_facts(tmp_path).files


def _scores(tmp_path: Path, files: dict[str, str]) -> dict[str, dict]:
    facts = _repo(tmp_path, files)
    pool = sorted(rel for rel in facts if rel.endswith((".py", ".ts", ".tsx")))
    return imp.score_symbols(tmp_path, facts, pool, imp.doc_paths(files))


def _body(n: int, word: str = "value") -> str:
    return "".join(f"    {word}_{i} = {i}\n" for i in range(n))


def test_percentiles_average_ties_like_pandas():
    assert imp.percentiles([3, 1, 1, 2]) == [1.0, 0.375, 0.375, 0.75]
    assert imp.percentiles([]) == []


def test_pagerank_sums_to_one_and_favours_the_target_of_many_edges():
    ranks = imp.pagerank(3, {(0, 2): 1.0, (1, 2): 1.0})
    assert abs(sum(ranks) - 1) < 1e-6 and ranks[2] > ranks[0] == ranks[1]
    assert imp.pagerank(0, {}) == []


def test_words_split_snake_and_camel_and_drop_noise():
    assert imp.words("load_vault_config(self) loadVaultConfig get x") == [
        "load", "vault", "config", "load", "vault", "config"]


def test_a_big_function_outranks_a_tiny_one(tmp_path):
    scores = _scores(tmp_path, {"app.py": f"def big():\n{_body(60)}\n\ndef tiny():\n    return 1\n"})
    assert scores["app.py::big"]["council"] > scores["app.py::tiny"]["council"]


def test_a_name_defined_in_many_files_loses_to_a_unique_one(tmp_path):
    files = {f"m{i}.py": f"def render():\n{_body(8)}" for i in range(6)}
    files["core.py"] = f"def settle():\n{_body(8)}"
    scores = _scores(tmp_path, files)
    assert scores["core.py::settle"]["rule"] > scores["m0.py::render"]["rule"]


def test_a_function_that_drives_the_code_ranks_above_its_leaves(tmp_path):
    leaves = "".join(f"def leaf{i}():\n{_body(4)}\n\n" for i in range(5))
    calls = "".join(f"    leaf{i}()\n" for i in range(5))
    scores = _scores(tmp_path, {"work.py": leaves + f"def run():\n{calls}{_body(0)}"})
    assert scores["work.py::run"]["council"] > max(scores[f"work.py::leaf{i}"]["council"] for i in range(5))
    assert "drives much of the code" in scores["work.py::run"]["reasons"]


def test_the_docs_vote_lifts_what_the_readme_talks_about(tmp_path):
    files = {"README.md": "Mercy keeps vault secrets and rotates vault tokens.\n",
             "a.py": f"def rotate_vault_tokens():\n{_body(6, 'vault_token')}\n\ndef pad_left():\n{_body(6, 'padding')}"}
    scores = _scores(tmp_path, files)
    assert scores["a.py::rotate_vault_tokens"]["docs"] > scores["a.py::pad_left"]["docs"] == 0
    assert "what the docs talk about" in scores["a.py::rotate_vault_tokens"]["reasons"]


def test_without_docs_the_council_follows_the_rule(tmp_path):
    scores = _scores(tmp_path, {"app.py": f"def big():\n{_body(40)}\n\ndef small():\n{_body(3)}"})
    assert all(s["docs"] == 0 for s in scores.values())
    assert scores["app.py::big"]["council"] > scores["app.py::small"]["council"]


def test_one_function_repo_scores_without_dividing_by_zero(tmp_path):
    scores = _scores(tmp_path, {"only.py": "def _hidden():\n    return 1\n"})
    assert list(scores) == ["only.py::_hidden"] and scores["only.py::_hidden"]["council"] == 1.0


def test_generated_maps_and_tests_are_not_the_repos_docs():
    assert imp.doc_paths(["README.md", "soulreap/OVERVIEW.md", "tests/notes.md", "docs/a.rst", "x.py"]) == [
        "README.md", "docs/a.rst"]


def test_ts_exports_and_tsx_components_count_as_the_study_found(tmp_path):
    body = "".join(f"  const v{i} = {i};\n" for i in range(6))
    scores = _scores(tmp_path, {"lib.ts": f"export function load() {{\n{body}}}\nfunction quiet() {{\n{body}}}\n",
                                "View.tsx": f"export function View() {{\n{body}}}\n"})
    assert scores["lib.ts::load"]["rule"] > scores["lib.ts::quiet"]["rule"]
    assert scores["lib.ts::load"]["rule"] > scores["View.tsx::View"]["rule"]


def test_without_docs_the_council_still_spans_the_whole_range(tmp_path):
    files = {"app.py": "".join(f"def f{i}():\n{_body(i + 1)}\n\n" for i in range(8))}
    councils = sorted(s["council"] for s in _scores(tmp_path, files).values())
    assert councils[-1] == 1.0 and councils[0] < 0.25


def test_a_form_feed_does_not_shift_function_bodies(tmp_path):
    files = {"README.md": "rotate vault tokens\n",
             "a.py": "\x0c\x0c\x0c\x0c\ndef pad():\n    padding = 1\n\n\n"
                     "def rotate_vault_tokens():\n    vault_tokens = 2\n"}
    scores = _scores(tmp_path, files)
    assert scores["a.py::rotate_vault_tokens"]["docs"] > 0 and scores["a.py::pad"]["docs"] == 0


def test_size_is_not_a_reason_for_a_tiny_function(tmp_path):
    scores = _scores(tmp_path, {"a.py": "def a():\n    return 1\n\n\ndef b():\n    x = 1\n    return x\n"})
    assert not any(r.endswith(" lines") for s in scores.values() for r in s["reasons"])


def test_fan_in_counts_distinct_calling_files(tmp_path):
    files = {"core.py": "def settle():\n    return 1\n",
             **{f"c{i}.py": f"from core import settle\n\n\ndef use{i}():\n    settle()\n    settle()\n"
                for i in range(3)}}
    scores = _scores(tmp_path, files)
    assert "called from 3 files" in scores["core.py::settle"]["reasons"]
