# Rule Source Manifest

## Active Desktop Rule Sources

- source_dir: `C:\Users\lzc93\Desktop\定价`
- data_flow_contract_version: `V1.50`
- data_flow_contract_file: `瓜子数据获取流程文档_V1.50_S12到S13四区域证明门禁全量版.docx`
- scoring_rule_version: `V1.11`
- scoring_rule_file: `瓜子自动定价打分规则V1.11_边界前车回采确认法版.docx`
- reference_selection_rule: `V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT`
- pricing_rule_version: `V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT`
- pricing_rule_file: `定价逻辑备份_服务费阶梯修正版_参考车选择V3边界确认法版_V3.3边界前车回采确认版 (1).docx`
- competition_coefficient_version: `V1.2.6`
- competition_coefficient_file: `目标车竞争力系数算法设计_V1.2.6_边界前车回采确认适配版.docx`
- checked_at: `2026-06-27`

The desktop `定价` folder is the highest rule source. Chat snippets, historical evidence,
old reports, and backup files cannot override these source files.

## Runtime Alignment Summary

- `config/fields.yaml` now declares the active V1.50/V1.11/V3.3/V1.2.6 source versions.
- `config/rule_manifest.json` points to the four desktop source documents listed above.
- `config/page_contract_runtime_coverage.yaml` is aligned to V1.50 page-contract execution coverage.
- `src/guazi_app_data_system/pricing.py` exposes the same active pricing, scoring, reference-selection, and competition versions.
- `scripts/rule_source_sync_check.py` validates these active source versions before runtime acceptance.

## S11 XML-Only Report Entry Contract

`S11_REPORT_ENTRY_BIND_VIEW_FULL_REPORT` is XML/accessibility driven only.

Allowed click sources:

- `xml_exact_text_bounds`
- `xml_clickable_parent_bounds`
- `xml_safe_container_bounds`
- `xml_after_stale_recovery`

Allowed stale recovery:

- `fresh_pair_stale_xml_redump_once`
- `xml_redump_once_after_stale`
- `bottom_safe_reposition_after_xml_exact_seen`

Forbidden actions and sources:

- `screenshot_dynamic_button_rect`
- `screenshot_button_layout_detector`
- `screenshot_text_detector`
- `visual_button_detector`
- `OCR`
- `fixed_coordinate`
- `ratio_coordinate`
- `default_click`
- `screenshot_coordinate_click`
- `click_when_xml_missing`
- `click_from_screenshot_visible_only`

Screenshots may be retained as evidence/debug only. If the screenshot shows the
button but XML/accessibility has no bindable target, runtime must not click. It
must record `screenshot_visible_xml_missing_debug=true`,
`screenshot_used_for_click=false`, `s11_visual_debug_not_used_for_click=true`,
and stop with `S11_REPORT_ENTRY_XML_MISSING_BUT_SCREENSHOT_VISIBLE_NOT_CLICKED`
or `S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET`.

## S07 Age Slider Contract

Allowed algorithms:

- `visible_tick_interpolation`
- `exact_tick_binding`
- `direct_track_fastpath`
- `direct_track_fastpath_5_5`
- `text_result_verify_first`

Fallbacks are allowed only when explicitly authorized by the V1.50 page contract.
At this sync point no legacy fallback is authorized.

Forbidden actions:

- `full_track_ratio_with_unlimited`
- `target_age_plus_one_right_slider`
- `right_first_without_source_clause`
- `long_press_drag_without_source_clause`
- `segmented_drag_without_source_clause`
- `track_based_drag_without_source_clause`
- `ghost_handle_binding`
- `legacy_fallback_after_direct_success`
- `unlimited_wait_panel_stable`

Required timing trace:

- `age_panel_wait_ms`
- `left_slider_bind_ms`
- `right_slider_bind_ms`
- `drag_ms`
- `verify_ms`
- `fallback_ms`
- `xml_dump_count`
- `screenshot_count`
- `fallback_strategies_used`

## Reference Early Exit Contract

Rule id: `REFERENCE_EARLY_EXIT_MAX_POSSIBLE_SCORE_CONTRACT_V1`

Early exit is source-backed by the V1.50 page contract, V1.11 scoring rule, and
V3.3 reference-selection/pricing rule. It is a continuation optimization for
low-score references only. It cannot produce final pricing.

Early exit is allowed only when all of these are true:

- active page contract is `V1.50`
- active scoring rule is `V1.11`
- active reference-selection rule is `V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT`
- target score is calculated and trustworthy
- mandatory fields are collected
- partial confirmed score is trustworthy
- remaining maximum possible score is deterministic
- `max_possible_reference_score < target_score`
- a trusted pre-boundary reference exists
- `reference_score_upper_bound < target_score`
- return to reliable S10 and continue-next-reference are available

An early-exited reference is always excluded from:

- `final_reference`
- `boundary_reference`
- `pre_boundary_reference`
- `NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING` closest-low fallback

## Sync Status

- status: `PENDING_TEST_VALIDATION`
- validation commands:
  - `python scripts/rule_source_sync_check.py`
  - `python scripts/runtime_contract_execution_check.py`
  - `python -m unittest discover tests -v`


V1.50 alignment note: S13 all-zero exit, reference_history writes, continuation and all-references-exhausted decisions require physical UI transition proof.
