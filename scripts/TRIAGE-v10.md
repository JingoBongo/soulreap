# Test triage for the v10 cutover (Task 10)

Rules (plan Task 10 Step 4, plus R7 added at execution): R1 legacy-card → delete · R2 optional diagram →
`cmd_render(..., diagrams="all")` · R3 old keyword → drop · R4 plan key → v10 key · R5 v9 overview → rewrite or
delete when superseded · R6 facts/extraction → must pass unchanged (fix code) · R7 moved name → re-point import.

Bulk, mechanical: R3 `paths=[], since=None, force=False[, skip_tests=True]` and `slice_id=None` dropped from every
call (218 calls); R7 `srlib.commands.<name>` re-pointed to `svggeom`/`svglint`/`callgraph`/`skeleton`/`facts`/
`diagrams`/`svg` (70 references). Deleted with their modules: test_units.py, test_allocate.py, test_authored.py,
test_stray_cards.py (per-file cards), test_facts_drift.py (facts are rebuilt per command; drift cannot exist).

R6 failures fixed in code, not tests: v10 plan.json lacked tree/non_code/secrets/cycles; v10 render did not return
context/ERD/drawing stats; context picture restored for ≥1 system (spec said >1; the tests pin the measured v9 rule).

| test | rule | action |
| --- | --- | --- |
| test_context.py::test_the_evidence_survives_the_round_trip_through_facts_json | R7 | reads facts.json `context` instead of `commands._context_evidence` |
| test_overview.py::test_a_generated_diagram_the_overview_never_mentions_is_reported | R5 | deleted — superseded: the diagrams block is generated every render and prose staleness is the section basis |
| test_overview.py::test_a_stale_context_image_is_reported | R5 | deleted — superseded: the diagrams block is generated every render and prose staleness is the section basis |
| test_overview.py::test_a_diagram_the_overview_points_at_that_no_longer_exists_is_reported | R5 | deleted — superseded: the diagrams block is generated every render and prose staleness is the section basis |
| test_overview.py::test_an_overview_older_than_the_cards_is_reported | R5 | deleted — superseded: the diagrams block is generated every render and prose staleness is the section basis |
| test_overview.py::test_the_warnings_reach_the_render_result | R5 | deleted — superseded: the diagrams block is generated every render and prose staleness is the section basis |
| test_overview.py::test_render_scaffolds_an_overview_when_there_is_none | R5 | rewritten against map_scaffold sections |
| test_overview.py::test_an_overview_without_the_markers_is_left_alone_and_reported | R5 | asserts `overview_missing` instead of `overview_warnings` |
| test_structures.py::test_render_counts_the_structures_it_drew | R2 | `diagrams="all"`; dropped `assert srlib.commands` |
| test_soulreap.py::test_a_callable_documented_twice_is_a_strict_failure | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_callable_longer_than_the_budget_gets_its_own_slice | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_card_for_an_excluded_file_does_not_fail_the_run | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_card_that_predates_the_change_stays_dirty_across_renders | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_card_without_an_entities_section_is_a_strict_failure | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_card_written_this_run_is_still_strict | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_comparison_is_not_markup | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_complete_entities_table_passes | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_dunder_fold_counts_as_coverage | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_file_under_the_heavy_threshold_is_not_sliced | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_file_with_no_call_graph_is_not_asked_for_one | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_generated_trivial_card_is_stamped_too | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_generic_type_is_not_markup | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_heavy_file_gets_a_head_slice_first | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_an_indented_block_is_not_a_strict_failure | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_an_indented_block_is_reported_as_style | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_an_in_scope_card_still_fails_strictly | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_an_untouched_clean_card_is_legacy_not_strict | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_partial_carding_round_does_not_launder_the_cards_it_skipped | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_prose_only_pseudocode_section_is_not_flagged | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_real_class_method_is_still_reported_missing | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_shape_group_is_never_split_across_slices | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_sql_literal_is_flagged_too | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_truncated_call_graph_is_a_strict_failure | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_truncated_entities_table_is_a_strict_failure | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_verbatim_call_graph_passes | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_batches_pack_small_sibling_directories_together_when_asked | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_batches_respect_the_file_cap | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_batches_split_when_line_budget_exceeded | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_canonical_sections_in_order_produce_no_warning | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_cli_accepts_batch_files | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_cli_command_header_is_counted | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_diff_statuses_dirties_everything_when_template_version_bumped | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_diff_statuses_marks_changed_sha_as_dirty | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_diff_statuses_marks_unchanged_sha_as_clean | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_diff_statuses_marks_unknown_file_as_new | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_diff_statuses_marks_vanished_file_as_orphan | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_empty_section_is_reported | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_every_callable_lands_in_exactly_one_slice | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_every_canonical_section_name_is_accepted | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_every_file_is_its_own_batch_by_default | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_fenced_pseudocode_passes | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_fixture_and_scheduled_headers_are_counted | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_generated_trivial_card_carries_a_role_marker | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_index_links_both_the_card_and_the_source | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_injecting_the_link_does_not_make_the_card_look_hand_edited | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_internal_dependencies_get_one_line_each | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_invented_section_is_reported | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_manifest_card_hash_matches_the_card_after_the_link_is_injected | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_markup_literals_name_the_blocks_that_paste_them | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_merge_puts_the_parts_into_one_card_in_canonical_order | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_merge_refuses_an_incomplete_set_and_names_what_is_missing | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_merge_removes_the_parts_once_the_card_is_written | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_methods_stay_with_their_class | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_naming_the_operation_is_not_flagged | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_no_slice_exceeds_the_budget_unless_one_callable_does | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plain_function_and_method_headers_still_counted | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_batches_one_file_per_task | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_does_not_batch_a_trivial_file | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_does_not_call_a_file_that_failed_to_parse_trivial | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_does_not_call_a_long_entityless_file_trivial | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_does_not_call_a_reexporting_init_trivial | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_excluded_file_is_never_reported_as_orphan | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_excluded_file_with_an_existing_card_stays_clean | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_exclude_glob_marks_matching_files_excluded | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_groups_files_into_batches | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_leaves_a_light_file_batched_the_old_way | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_lists_slices_and_batches_them_one_by_one | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_marks_an_empty_package_marker_as_trivial | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_marks_file_as_new_when_its_card_is_missing | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_packs_batches_when_told_to | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_prefers_excluded_over_trivial_for_a_test_package_marker | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_reports_a_written_card_as_carded_before_render | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_second_run_with_cards_written_reports_clean | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_since_ref_only_dirties_files_changed_in_git | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_without_skip_tests_still_cards_test_files | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_with_skip_tests_does_not_batch_excluded_files | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_with_skip_tests_keeps_production_files_new | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_with_skip_tests_marks_test_files_excluded | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_plan_writes_plan_and_facts_json | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_protocol_stub_methods_are_not_reported_missing | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_pseudocode_at_column_zero_is_a_strict_failure | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_accepts_a_callable_named_on_a_fold_continuation_line | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_accepts_a_callable_named_only_in_a_fold_table | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_accepts_a_fold_whose_exemplar_shares_the_folded_shape | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_adds_a_source_link_to_a_card_that_lacks_one | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_does_not_count_a_sql_file_as_a_card_or_as_missing_one | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_exits_nonzero_when_a_strict_check_fails | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_flags_hand_edited_card_on_next_plan | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_rendering_twice_does_not_duplicate_the_source_link | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_keeps_a_role_sentence_that_wraps_across_lines | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_quotes_the_module_docstring_of_a_trivial_file | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_repairs_a_stale_source_link | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_replaces_a_dependencies_section_the_model_got_wrong | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_reports_markup_as_a_warning_not_a_strict_failure | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_reports_who_imports_the_file | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_stays_quiet_for_a_file_with_no_callables | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_strips_the_template_comment_that_leaked_into_cards | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_updates_manifest_with_source_and_card_hashes | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_warns_when_a_callable_is_missing_from_the_pseudocode | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_warns_when_a_folded_callable_has_no_same_shape_exemplar | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_warns_when_every_member_of_a_shape_group_is_folded | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_writes_a_card_for_a_trivial_file | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_writes_index_listing_every_card | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_render_writes_the_dependencies_section_from_facts | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_route_header_without_function_keyword_is_counted | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_route_with_function_keyword_still_counted | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_sectionless_pseudocode_is_accepted | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_sections_out_of_order_are_reported | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_skeleton_names_the_callables_of_the_slice | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_skeleton_slice_leaves_other_slices_callables_out_of_the_assignment | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_skeleton_slice_prints_a_preamble | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_slice_assignment_anchors_every_callable | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_slice_assignment_says_the_name_list_wins | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_source_link_for_a_repo_root_file | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_template_version_is_bumped_for_the_section_change | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_template_version_is_nine_for_the_unit_model | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_the_cli_skips_tests_without_being_told | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_the_source_link_actually_resolves_to_the_file | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_trivial_file_reports_clean_on_the_next_plan | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_two_classes_with_a_same_named_method_are_not_a_duplicate | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_with_tests_asks_for_them_back | R1 | deleted — per-file card behaviour |
| test_soulreap.py::test_a_fenced_block_draws_no_style_warning | R1/R5 | deleted — card style lint (per-file cards) |
| test_soulreap.py::test_a_rewritten_card_takes_the_new_source_sha | R1/R5 | deleted — per-card source sha (per-file cards) |
| test_soulreap.py::test_plan_does_not_card_a_sql_file | R1/R5 | deleted — per-file card statuses |
| test_soulreap.py::test_plan_excluded_source_is_not_dropped_from_the_listing | R1/R5 | deleted — per-file card statuses; left_out replaces the listing |
| test_soulreap.py::test_plan_scoped_to_subpath_ignores_other_files | R1/R5 | deleted — plan --paths scope (v9 only) |
| test_soulreap.py::test_plan_skip_tests_reports_what_it_dropped | R1/R5 | deleted — per-file statuses; replaced by left_out (test_mapplan) |
| test_soulreap.py::test_render_does_not_report_stale_facts_after_a_fresh_plan | R1/R5 | deleted — facts are rebuilt per command; stale facts.json cannot exist |
| test_soulreap.py::test_render_reports_stale_facts | R1/R5 | deleted — facts are rebuilt per command; stale facts.json cannot exist |
| test_soulreap.py::test_skeleton_warns_that_facts_predate_the_current_schema | R1/R5 | deleted — facts are rebuilt per command; stale facts.json cannot exist |
| test_soulreap.py::test_render_keeps_the_source_sha_a_card_was_written_against | R1/R5 | deleted — per-card source sha (per-file cards) |
| test_soulreap.py::test_render_mermaid_lint_leaves_an_er_diagram_alone | R1/R5 | deleted — mermaid lint removed with mermaid output |
| test_soulreap.py::test_render_notices_a_hand_written_flow_diagram_and_leaves_it_alone | R1/R5 | deleted — flow_present stat: hand-drawn diagrams are no longer required |
| test_soulreap.py::test_render_reports_a_missing_flow_diagram | R1/R5 | deleted — flow_present stat: hand-drawn diagrams are no longer required |
| test_soulreap.py::test_render_stamps_a_card_with_when_it_was_written | R1/R5 | deleted — per-card carded stamp; v10 staleness is the section basis |
| test_soulreap.py::test_the_stamp_moves_when_the_card_is_rewritten | R1/R5 | deleted — per-card carded stamp; v10 staleness is the section basis |
| test_soulreap.py::test_the_stamp_sits_on_the_source_link_line | R1/R5 | deleted — per-card carded stamp; v10 staleness is the section basis |
| test_soulreap.py::test_the_stamp_survives_a_render_that_did_not_change_the_card | R1/R5 | deleted — per-card carded stamp; v10 staleness is the section basis |
| test_soulreap.py::test_the_excluded_card_is_still_reported_as_legacy | R1/R5 | deleted — legacy per-file card warnings |
| test_soulreap.py::test_a_stale_package_diagram_is_removed_once_it_has_nothing_to_say | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_also_writes_a_package_level_diagram | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_collapses_graph_to_the_deepest_level_that_fits | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_collapses_large_graph_to_package_level | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_draws_no_package_diagram_when_nothing_imports_across_packages | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_draws_the_hand_written_diagrams_too | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_drops_self_edges_from_a_collapsed_graph | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_reports_a_diagram_it_could_not_draw | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_reports_graph_density | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_writes_an_svg_beside_each_generated_mermaid | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_writes_a_package_diagram_naming_both_packages | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_writes_packages_but_not_the_per_file_hairball | R2 | `diagrams="all"` |
| test_soulreap.py::test_the_packages_svg_and_mmd_name_the_same_packages_at_render | R2 | `diagrams="all"` |
| test_soulreap.py::test_the_packages_picture_falls_back_to_the_hand_drawer | R2 | `diagrams="all"` |
| test_soulreap.py::test_render_falls_back_to_the_hand_written_drawer | R7 | monkeypatches `srlib.drawing` |
| test_soulreap.py::test_render_serializes_under_a_lock | R7 | v10 commands; lock ported to `util.render_lock` |
| test_soulreap.py::test_plan_skip_tests_still_records_facts_for_excluded_files | R3 | default skips tests |
| test_soulreap.py::test_build_graph_* (5) | R7 | `soulreap.build_graph` re-exported |
| test_soulreap.py::test_the_cli_says_what_it_left_out_and_why, test_the_old_skip_tests_flag_still_runs | R6 | fixed in code: plan reports `left_out`; `--skip-tests` accepted as a no-op |
| test_soulreap.py::test_file_card_template_documents_the_schema_section | R1 | deleted — references/file-card.md is gone with per-file cards |
