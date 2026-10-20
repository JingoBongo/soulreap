"""Test-session guard: the diagram tests need pygraphviz.

Run the suite the way every test module header says:

    uv run --with pytest --with pygraphviz --with pathspec \
         --with tree-sitter-language-pack pytest -q

pygraphviz is the skill's drawing engine — the SVG is the artefact, mermaid is syntax
only — so a session without it measures drawings that were never produced. Those tests
would then fail for an environment reason rather than a code reason, which reads as a
regression and buries the real failures. They are skipped with that reason instead: the
rest of the suite still runs, and the documented command above runs everything.

If a new test needs pygraphviz, add its node id here. A bare `pytest` is a convenience;
the documented command is the gate.
"""

from __future__ import annotations

import pytest

try:
    import pygraphviz  # noqa: F401

    HAVE_PYGRAPHVIZ = True
except Exception:  # pragma: no cover - environment dependent
    HAVE_PYGRAPHVIZ = False

SKIP_REASON = "pygraphviz not installed — run with `--with pygraphviz` (see the module header)"

NEEDS_PYGRAPHVIZ = {
    # OVERVIEW's checks read the generated diagrams, which exist only when drawn.
    "test_overview.py::test_no_overview_yet_is_not_a_complaint",
    "test_overview.py::test_a_generated_diagram_the_overview_never_mentions_is_reported",
    "test_overview.py::test_a_scaffolded_overview_is_quiet_from_the_start",
    "test_overview.py::test_the_generated_blocks_carry_the_facts_nobody_should_retype",
    # The dot geometry reader and the packages picture.
    "test_soulreap.py::test_flow_svg_draws_every_subgraph_frame",
    "test_soulreap.py::test_no_subgraph_cluster_wraps_nothing",
    "test_soulreap.py::test_subgraph_membership_is_a_tree",
    "test_soulreap.py::test_render_draws_the_hand_written_diagrams_too",
    "test_soulreap.py::test_the_geometry_reader_sees_a_graphviz_drawing_at_all",
    "test_soulreap.py::test_the_canvas_is_read_from_the_svg_tag_not_from_a_stroke_width",
    "test_soulreap.py::test_a_graphviz_cluster_frame_is_not_counted_as_an_edge",
    "test_soulreap.py::test_a_graphviz_node_box_is_a_box",
    "test_soulreap.py::test_a_flowchart_edge_skipping_a_layer_is_routed_around_the_box",
    "test_soulreap.py::test_the_packages_picture_is_drawn_by_dot_and_carries_role_and_weight",
    "test_soulreap.py::test_the_packages_svg_and_mmd_name_the_same_packages_at_render",
    "test_soulreap.py::test_the_dot_packages_picture_crosses_less_than_the_hand_drawn_one",
    # ERD, observed stores and tasks are all drawn by dot.
    "test_sqlite.py::test_the_erd_says_the_schema_came_out_of_the_database_itself",
    "test_stores.py::test_a_cache_with_namespaces_is_drawn_even_without_fields",
    "test_stores.py::test_render_draws_the_observed_store",
    "test_stores.py::test_the_picture_says_the_schema_was_never_declared",
    "test_stores.py::test_an_unreadable_field_is_reported_on_the_picture",
    "test_stores.py::test_an_observed_store_never_lands_on_a_database_diagram",
    "test_tasks.py::test_render_draws_who_enqueues_what",
    "test_tasks.py::test_a_task_nobody_enqueues_is_still_drawn",
}


def pytest_collection_modifyitems(config, items):
    if HAVE_PYGRAPHVIZ:
        return
    for item in items:
        if item.nodeid in NEEDS_PYGRAPHVIZ:
            item.add_marker(pytest.mark.skip(reason=SKIP_REASON))
