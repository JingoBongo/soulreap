"""Tests for the one file nothing checked: OVERVIEW.md.

Run: uv run --with pytest --with pygraphviz --with pathspec \
         --with tree-sitter-language-pack pytest test_overview.py -q

A card goes `dirty` when its source hash moves. OVERVIEW has no such anchor — it is
prose, written by hand, and `render` never touches it. So it drifted: on fuse_core it
sat for a day showing a hand-drawn context with a GitHub node while the generated one
named six systems and GitHub was not among them, and pointing at a packages diagram
the silence rule had since deleted. Nothing said a word.

These checks are exact, never taste: a name is present or it is not, a file exists or
it does not.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import soulreap as sr

VAULT_APP = '''
from vaults import Vault

store = Vault("things")

def read(name):
    record = store.get(name)
    return record["status"]

def write(name, record):
    record["status"] = "ok"
    store.put(name, record)
'''


def _repo(tmp_path, overview: str | None = None):
    (tmp_path / "app.py").write_text(VAULT_APP)
    (tmp_path / "requirements.txt").write_text("psutil\nredis\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    if overview is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "OVERVIEW.md").write_text(overview)
    return out


def _warnings(out) -> list[str]:
    return sr.cmd_render(root=out.parent, out=out).get("overview_warnings", [])


def test_no_overview_yet_is_not_a_complaint(tmp_path):
    """Before the first one is written there is nothing to be stale."""
    out = _repo(tmp_path)
    assert _warnings(out) == []


def test_naming_the_diagram_file_is_enough(tmp_path):
    """Some are pasted, some are linked — either counts as being shown."""
    out = _repo(tmp_path, "# App\n\nSee diagrams/observed-app.svg and diagrams/context.dot.\n")
    assert not [w for w in _warnings(out) if "never named" in w]


def test_pasting_a_diagram_counts_as_showing_it(tmp_path):
    """The primary intent of the template is to PASTE, not to link, and a paste
    carries the title line rather than the filename."""
    out = _repo(tmp_path)
    _warnings(out)          # a first render, so there are diagrams to paste
    context = (out / "diagrams" / "context.dot").read_text()
    body = ("# App\n\n<img src=\"diagrams/context.svg\">\n\ndiagrams/observed-app.svg\n")
    (out / "OVERVIEW.md").write_text(body)
    assert not [w for w in _warnings(out) if "never named" in w]


def test_an_overview_carrying_every_system_is_quiet(tmp_path):
    out = _repo(tmp_path, "# App\n\ndiagrams/context.dot · diagrams/observed-app.svg\n\n"
                          "Talks to Redis and to the host OS.\n")
    assert [w for w in _warnings(out) if "context" in w] == []


def test_a_scaffolded_overview_is_quiet_from_the_start(tmp_path):
    """The anchor that matters now: what render writes itself passes its own checks.

    This test used to write an overview by hand to prove the checks could be
    satisfied. They can be — but nobody should have to.
    """
    out = _repo(tmp_path)
    sr.cmd_render(root=tmp_path, out=out)          # scaffolds it
    assert _warnings(out) == []


# ------------------------------------------------- generated half of OVERVIEW ---
#
# The deeper fix. Drift was a symptom: half of OVERVIEW is facts the parser already
# holds — the stamp, the directory counts, the non-code inventory, the diagram pastes,
# the bullets about cycles and packages — and a model was retyping them from
# `plan.json` by hand, which is the one thing this skill says never to do. Render owns
# those blocks now; the prose around them is never touched.


def test_render_scaffolds_an_overview_when_there_is_none(tmp_path):
    out = _repo(tmp_path)
    sr.cmd_render(root=tmp_path, out=out)
    text = (out / "OVERVIEW.md").read_text()
    assert "<!-- soulreap:stamp -->" in text
    assert "<!-- soulreap:diagrams -->" in text
    assert "_Write this by hand" in text        # the prose placeholders
    # v10 sections: no hand-drawn diagrams are asked for; Not covered is generated.
    assert "## How it runs" in text and "## Sharp edges" in text
    assert "## Not covered" in text and "<!-- soulreap:coverage -->" in text


def test_the_generated_blocks_carry_the_facts_nobody_should_retype(tmp_path):
    out = _repo(tmp_path)
    sr.cmd_render(root=tmp_path, out=out)
    text = (out / "OVERVIEW.md").read_text()
    assert "## Layout" in text and "files," in text          # counts, from plan.json.tree
    assert "requirements.txt" in text                       # non-code inventory
    assert "### context" in text and "diagrams/context.svg" in text  # image-ref pasted, not retyped


def test_prose_outside_the_markers_survives_a_rerender(tmp_path):
    out = _repo(tmp_path)
    sr.cmd_render(root=tmp_path, out=out)
    text = (out / "OVERVIEW.md").read_text()
    (out / "OVERVIEW.md").write_text(text + "\n## My own section\n\nWritten by a human.\n")
    sr.cmd_render(root=tmp_path, out=out)
    after = (out / "OVERVIEW.md").read_text()
    assert "Written by a human." in after
    assert "<!-- soulreap:stamp -->" in after


def test_a_generated_block_is_rewritten_in_place(tmp_path):
    out = _repo(tmp_path)
    sr.cmd_render(root=tmp_path, out=out)
    path = out / "OVERVIEW.md"
    spoiled = re.sub(
        r"(<!-- soulreap:stamp -->).*?(<!-- /soulreap:stamp -->)",
        r"\1\nnonsense\n\2",
        path.read_text(), flags=re.S,
    )
    path.write_text(spoiled)
    sr.cmd_render(root=tmp_path, out=out)
    assert "nonsense" not in path.read_text()


def test_an_overview_without_the_markers_is_left_alone_and_reported(tmp_path):
    """Someone's hand-written overview from an older run must not be clobbered."""
    out = _repo(tmp_path, "# App\n\nEverything by hand, no markers.\n")
    missing = sr.cmd_render(root=tmp_path, out=out)["overview_missing"]
    assert (out / "OVERVIEW.md").read_text() == "# App\n\nEverything by hand, no markers.\n"
    assert "stamp" in missing


def test_the_verdict_bullets_come_from_render_not_from_prose(tmp_path):
    out = _repo(tmp_path)
    sr.cmd_render(root=tmp_path, out=out)
    text = (out / "OVERVIEW.md").read_text()
    assert "soulreap:verdicts" in text
    assert "import cycle" in text
