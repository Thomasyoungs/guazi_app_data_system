# ???? / ???? / ?? / ????????? V1

- ?????`SYSTEM_RULES_CONTRACT_SCORING_COMPETITION_AUDIT_FOUND_ISSUES`
- ??????????????????????config?pricing??????????? result.json?
- ???????`??|Lumin|2026?|??? 205km ??? ??|?|2025.11`
- ???????? `TARGET_SERIES_NOT_FOUND_IN_S04`????????

## ????

???????????????? Lumin ?????????????
- S04_SERIES_SEARCH_V2 missing for new-energy tab / letter index / alias, causing Lumin S04 failure.
- S07 hidden tick logic supports 11/12 only, not 1-year hidden tick required by Lumin.
- ?? normalization is not clearly mapped to ??, risking wrong target_score for Prado-like targets.
- S10 first-stage single-card/split-card parser risk remains from Prado evidence.

????????????????????S10 ??????? mileage desc????????S13 raw nodes ???????S13?S14 ?????S16 ??????????? V1.2.1 ???????????????

## ?????????

- `S10 non-trisame boundary and title filter`?implemented????scripts/runtime_s10_to_s16_mainline.py:1677, 1805, 1927; output/s10_trisame_source_boundary_patch.json
- `S10 canonical order price asc + same-price mileage desc`?implemented????scripts/runtime_s10_to_s16_mainline.py:1940
- `S10 card completeness / partial card gate`?implemented????scripts/runtime_s10_to_s16_mainline.py:1743, 2025; output/s10_reference_card_completeness_gate_patch.json
- `S11 top-one-third image-only recognition and report safe click`?implemented????scripts/runtime_s10_to_s16_mainline.py:651, 4535; prior acceptance reports
- `S11_TO_S12 stable wait and S12 priority over S14`?implemented????scripts/runtime_s10_to_s16_mainline.py:3627, 941, 3670
- `S13 raw XML node history repair parsing with duplicate zero preserved`?implemented????scripts/runtime_s10_to_s16_mainline.py:4920; output/s13_history_repair_duplicate_zero_parser_patch_report.json
- `S13 repair item click guard and live-room prohibition`?implemented????scripts/runtime_s10_to_s16_mainline.py:113, 5263; output/s13_repair_item_click_guard_patch.json
- `Service fee tier pricing and no active 0.95 payout logic`?implemented????src/guazi_app_data_system/pricing.py:296, 915; rg found 0.95 only in issue_classifier confidence
- `Competition coefficient V1.2.1 core anti-duplication`?mostly implemented????src/guazi_app_data_system/pricing.py:524, 579, 689, 729, 776, 788, 804

## ????

### P0_S04_SERIES_SEARCH_V2_MISSING_FOR_EV_ALIAS
- ???`runtime_s01_to_s10_mainline`
- ??/???`S04_SERIES_SEARCH_V2`
- ?????`HIGH`???????`P0`??????`true`
- ?????S04 only authorizes click_series_model_button in config/pages.yaml; runtime may use right-side letter index only when page contract allows tap_series_letter. It does not have a contract-backed new-energy tab flow, C/L letter strategy, or alias strategy for Lumin / ??Lumin / ?? Lumin. The latest Lumin run stopped with TARGET_SERIES_NOT_FOUND_IN_S04 after linear scrolling.
- ?????S04 should have a contract-backed V2 search path: try allowed tabs such as ??? when present, use A-Z index under contract, apply explicit alias set, record attempted_tabs / attempted_letters / visible_series_names_by_step, and still forbid similar series such as ??? / ?? / ??.
- ???New-energy or alias-named series can be unreachable; first stage blocks even when the APP has a valid target series under a different tab or index path.
- ?????Add S04_SERIES_SEARCH_V2 contract and runtime implementation for tab/index/alias search, with strict similar-series exclusion and full attempt logging.
- ?????output/result_s01_to_s10.json, artifacts/debug/s04_series_down_12_20260510_110914.xml, artifacts/screenshots/s04_series_down_12_20260510_110914.png
- ?????scripts/runtime_s01_to_s10_mainline.py:5048, scripts/runtime_s01_to_s10_mainline.py:5238, config/pages.yaml:S04 allowed_actions
- ???????????????_V1.11_???????_S10??????_S13???????.docx: term scan did not find ??? / Lumin / ??

### P0_S07_AGE_HIDDEN_TICK_1_YEAR_MISSING
- ???`runtime_s01_to_s10_mainline`
- ??/???`S07_AGE_SLIDER_HIDDEN_TICKS`
- ?????`HIGH`???????`P0`??????`true`
- ?????_s07_hidden_age_tick_info currently declares hidden_tick_supported_range=11-12 and rejects target_age not in (11, 12). It therefore does not support the user-required 1-year hidden tick between 0 and 2.
- ?????When visible ticks include 0 and 2 but not 1, dynamically compute the 1-year hidden point from x0/x2, set both sliders to 1, and verify 1-1? before AGE_FILTER_DONE=true.
- ???Young vehicles such as 2025-registered Lumin cannot pass exact age filtering once S04 is fixed.
- ?????Extend hidden tick logic to supported ranges 1, 11, 12; record x0/x2/one_year_step/verify_text and fail closed when target_age is unsupported.
- ?????scripts/runtime_s01_to_s10_mainline.py
- ?????scripts/runtime_s01_to_s10_mainline.py:4052, scripts/runtime_s01_to_s10_mainline.py:4055, scripts/runtime_s01_to_s10_mainline.py:4079
- ?????V1.11 DOCX term scan: no 1-1? hit

### P0_TARGET_CONDITION_BUQI_NOT_NORMALIZED_TO_PAINT
- ???`scoring / target condition standardization`
- ??/???`target_score body repair normalization`
- ?????`HIGH`???????`P0`??????`true`
- ?????Damage normalization maps paint/metal/replace aliases but ?? is not visibly present in config/fields.yaml field_mapping or pricing.py PAINT_DAMAGE_TYPES. Competition risk counting also counts ??/paint-like terms, not clearly ??. User-provided Prado condition uses ??.
- ??????? must normalize to ?? / ???? for target_score and competition risk notes; ??? / ??? must map to rear fenders.
- ??????????????? target_score ??????????????????????????????
- ?????Add ?? / ???? aliases to parser, fields.yaml, damage priority, scoring normalization, and report standardized target repairs.
- ?????src/guazi_app_data_system/pricing.py, config/fields.yaml
- ?????src/guazi_app_data_system/pricing.py:20, src/guazi_app_data_system/pricing.py:60, config/fields.yaml:field_mapping
- ???????????????V1.5_????????.docx

### P1_S10_SINGLE_CARD_OR_SPLIT_CARD_READY_RISK
- ???`runtime_s01_to_s10_mainline / S10 recognizer`
- ??/???`S10_READY card audit`
- ?????`HIGH`???????`P1`??????`true`
- ?????Prado rerun evidence showed a live S10-like page with one valid target card but first-stage S10_READY reported vehicle_card_count=0 and trisame_count_confirmed=false. This suggests the first-stage S10 card audit can miss single-card or split-node cards.
- ?????A single fully visible true trisame card before the non-trisame boundary should be accepted as S10_READY, with trisame_count=1 and sample shortage warning, not blocked by parser structure assumptions.
- ???Valid rare models with only one same-source listing can be incorrectly blocked or misclassified as no reliable S10.
- ?????Replay the Prado S10 XML and strengthen first-stage S10 card extraction for split title/price/metadata and single-card pages before boundary.
- ?????artifacts/debug/s09_to_s10_20260510_105333.xml, artifacts/screenshots/s09_to_s10_20260510_105333.png
- ?????scripts/runtime_s01_to_s10_mainline.py:3500, scripts/runtime_s01_to_s10_mainline.py:3589, scripts/runtime_s01_to_s10_mainline.py:6582
- ?????V1.11 DOCX includes S10 canonical/partial but single-card parser edge is not explicit

### P1_S04_CONTRACT_CONFIG_DRIFT
- ???`config/pages.yaml and runtime`
- ??/???`S04 allowed actions`
- ?????`MEDIUM`???????`P1`??????`true`
- ?????Runtime contains logic to detect a right-side letter index and raises CONTRACT_NEEDS_UPDATE_S04_LETTER_INDEX if the contract does not authorize it. config/pages.yaml currently lists only click_series_model_button for S04.
- ?????If S04 letter index is part of the accepted page contract, config/pages.yaml and actions.yaml should explicitly define tap_series_letter with safety bounds and logging; otherwise runtime should not carry half-enabled code.
- ???The system can stop on a valid page solely because the code sees a useful control that the contract has not yet authorized.
- ?????Synchronize S04 page contract, actions.yaml, and runtime around the same tab/index strategy.
- ?????config/pages.yaml, scripts/runtime_s01_to_s10_mainline.py
- ?????scripts/runtime_s01_to_s10_mainline.py:5048, scripts/runtime_s01_to_s10_mainline.py:5057, config/pages.yaml:S04
- ?????V1.11 DOCX term scan did not find ??

### P1_MINOR_DAMAGE_SCORING_GAPS
- ???`scoring / target condition risk`
- ??/???`?? / ?? / ?? / ????`
- ?????`MEDIUM`???????`P1`??????`false`
- ?????Minor damage terms are captured in competition notes as MINOR_DAMAGE_RECORDED_NO_EXTRA_COEFFICIENT_DEDUCTION, but target_score body scoring only handles ?? / ?? / ?? damage records. There is no clear scoring mapping for scratches, scuffs, dents, windshield chip, or small cosmetic damage.
- ?????These terms should either have explicit scoring/risk mapping or a deterministic manual review note; they should not be silently ignored or converted to paint/metal without rule support.
- ???Targets like ??? with small cosmetic damage rely on notes/review rather than deterministic score handling.
- ?????Add explicit minor-damage categories: no-score note, small deduction, or manual review trigger, with examples and unit tests.
- ?????src/guazi_app_data_system/pricing.py, config/fields.yaml, santana_full_chain_acceptance_report.json
- ?????src/guazi_app_data_system/pricing.py:167, src/guazi_app_data_system/pricing.py:670
- ???????????????V1.5_????????.docx

### P1_S13_S14_REPAIR_CLICK_GUARD_CODED_BUT_DOC_DRIFT
- ???`runtime_s10_to_s16_mainline / page contract docs`
- ??/???`S13_TO_S14 repair item click`
- ?????`MEDIUM`???????`P1`??????`false`
- ?????Runtime now has forbidden click text, safe repair item binding, clicked_text/bounds audit, and live-room detection, but term scanning V1.11 did not find ?? / ???? terms. The code appears safer than the available document scan.
- ?????The page contract should explicitly freeze forbidden live-room and bottom-bar click zones and the required audit fields.
- ???Future maintenance could relax the guard because it is not equally visible in the contract document.
- ?????Sync S13_TO_S14 repair item click guard and click-audit requirements into the main page contract document.
- ?????scripts/runtime_s10_to_s16_mainline.py, output/s13_repair_item_click_guard_patch_latest.md
- ?????scripts/runtime_s10_to_s16_mainline.py:113, scripts/runtime_s10_to_s16_mainline.py:5263, scripts/runtime_s10_to_s16_mainline.py:5457
- ?????V1.11 DOCX term scan did not find ?? / ????

### P1_COMPETITION_V1_2_1_MODEL_PROFILE_GAP
- ???`pricing.py / competition coefficient`
- ??/???`model_liquidity and fuel_cost_pressure`
- ?????`MEDIUM`???????`P1`??????`false`
- ?????V1.2.1 anti-duplicate mechanics are mostly coded: sample-shortage distribution no duplicate deduction, OBD note only, city record-only, pressure cap, EV fuel-cost-not-applicable note. However model profiles are hard-coded in pricing.py rather than a transparent config table, and new models like Lumin depend on fallback notes rather than an explicit EV liquidity rule.
- ?????Model liquidity / fuel-cost / EV liquidity should come from a reviewable config table or deterministic taxonomy with missing-profile notes.
- ???The coefficient is reproducible, but model-level judgments are harder to audit and extend for EVs, parallel imports, and niche models.
- ?????Move model_liquidity_profiles / EV profile rules into config with explicit missing-profile behavior.
- ?????src/guazi_app_data_system/pricing.py, ????????????_V1.2.1_????????.docx
- ?????src/guazi_app_data_system/pricing.py:729, src/guazi_app_data_system/pricing.py:788, src/guazi_app_data_system/pricing.py:804
- ?????????????????_V1.2.1_????????.docx

### P2_EV_FIELDS_AND_EMISSION_CONFUSION_RISK
- ???`target task schema / pricing context`
- ??/???`?????`
- ?????`MEDIUM`???????`P2`??????`false`
- ?????The target task schema in config/fields.yaml requires traditional fields and does not formally list energy_type, range_km, or battery_supplier. Runtime/pricing can read these if present, but schema documentation is not aligned.
- ?????EV-specific fields should be explicit optional target fields; emission_standard should be absent/not-applicable, not overloaded with range.
- ???New EV targets may be written inconsistently and fuel/emission notes may become ambiguous.
- ?????Add optional EV fields to schema and report output: energy_type, range_km, battery_supplier, EV notes.
- ?????config/fields.yaml, output/result_s01_to_s10.json
- ?????config/fields.yaml:target_fields, src/guazi_app_data_system/pricing.py:475
- ?????????????????_V1.2.1_????????.docx

### P2_RESULT_RAW_SNAPSHOT_OUTPUT_RISK_FIRST_STAGE
- ???`result serialization`
- ??/???`raw XML / visible_blob omission`
- ?????`LOW`???????`P2`??????`false`
- ?????Second-stage has RESULT_OMIT_KEYS for fresh_xml/nodes/raw_xml/visible_blob. First-stage code still carries fresh_xml/visible_blob in snapshots and some failure contexts may include full snapshots unless sanitized by the final writer.
- ?????Both stages should share a strict result sanitizer that omits raw XML, nodes, visible_blob, and huge text blobs from result JSON while preserving artifact paths.
- ???Failure results can become oversized or leak raw page dumps if first-stage sanitization is incomplete.
- ?????Backport RESULT_OMIT_KEYS-style sanitizer to first-stage result writing and add JSON size/raw-key regression check.
- ?????scripts/runtime_s01_to_s10_mainline.py, scripts/runtime_s10_to_s16_mainline.py
- ?????scripts/runtime_s01_to_s10_mainline.py:331, scripts/runtime_s01_to_s10_mainline.py:353, scripts/runtime_s10_to_s16_mainline.py:2908
- ?????Multiple acceptance/freeze reports require raw XML not written to result JSON

### P2_LEFT_SILL_AND_SPECIAL_BODY_RULES_PARTIAL
- ???`scoring / competition notes`
- ??/???`left sill / water tank / special structure`
- ?????`LOW`???????`P2`??????`false`
- ?????Left sill terms produce a review note in competition risk code, and water tank paint/metal produces a special review reason, but these are not fully represented as deterministic scoring table rows in config/fields.yaml.
- ?????Special body locations should have explicit scoring or manual-review policy in fields.yaml and DOCX.
- ???Rules are visible in code but less transparent to non-code review.
- ?????Promote left sill and water-tank policies into config and rule docs.
- ?????src/guazi_app_data_system/pricing.py, config/fields.yaml
- ?????src/guazi_app_data_system/pricing.py:636, src/guazi_app_data_system/pricing.py:217
- ???????????????V1.5_????????.docx

### P3_HISTORICAL_BASELINE_NEW_PRICING_IMPACT_SEPARATION_OK
- ???`baseline reports / pricing freeze`
- ??/???`historical baseline vs new pricing rules`
- ?????`LOW`???????`P3`??????`false`
- ?????Historical baselines are preserved, and service-fee/competition coefficient impact reports are separate. This is correct, but the audit should keep reminding that old original baseline prices are not overwritten.
- ?????Historical baselines remain immutable; new pricing rules are applied only to future runs or separate replay reports.
- ???No functional blocker; governance note only.
- ?????Keep future replay reports separate from frozen baseline acceptance files.
- ?????output/guazi_service_fee_pricing_rule_freeze.json, output/competition_coefficient_v1_2_pricing_patch.json
- ?????src/guazi_app_data_system/pricing.py:903
- ???????????_????????.docx, ????????????_V1.2.1_????????.docx

## ??????

### P0
- `P0_S04_SERIES_SEARCH_V2_MISSING_FOR_EV_ALIAS`
- `P0_S07_AGE_HIDDEN_TICK_1_YEAR_MISSING`
- `P0_TARGET_CONDITION_BUQI_NOT_NORMALIZED_TO_PAINT`

### P1
- `P1_S10_SINGLE_CARD_OR_SPLIT_CARD_READY_RISK`
- `P1_S04_CONTRACT_CONFIG_DRIFT`
- `P1_MINOR_DAMAGE_SCORING_GAPS`
- `P1_S13_S14_REPAIR_CLICK_GUARD_CODED_BUT_DOC_DRIFT`
- `P1_COMPETITION_V1_2_1_MODEL_PROFILE_GAP`

### P2
- `P2_EV_FIELDS_AND_EMISSION_CONFUSION_RISK`
- `P2_RESULT_RAW_SNAPSHOT_OUTPUT_RISK_FIRST_STAGE`
- `P2_LEFT_SILL_AND_SPECIAL_BODY_RULES_PARTIAL`

### P3
- `P3_HISTORICAL_BASELINE_NEW_PRICING_IMPACT_SEPARATION_OK`

## ????

`SYSTEM_RULES_CONTRACT_SCORING_COMPETITION_AUDIT_FOUND_ISSUES`
