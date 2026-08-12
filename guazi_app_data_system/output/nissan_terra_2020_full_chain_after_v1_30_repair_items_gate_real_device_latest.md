# Nissan Terra V1.30 Repair Items Gate Real Device Validation

## Final Status

RUN_NISSAN_TERRA_AFTER_V1_30_REPAIR_ITEMS_GATE_REAL_DEVICE_DONE

## Version

- overall_contract_version: V1.30
- execution_contract_version: V1.30
- validated_gate_contract_version: V1.29
- validated_gate: S13_S14_ALL_REPAIR_ITEMS_REQUIRED_BEFORE_S15
- rule_source_version: V1.29
- startup_entry_contract_version: V1.30

## Target

日产|途达|2020款|2.5L XL Upper 4WD 自动四驱豪华版|白|2021.08

## Run Strategy

Current result context was not reliable enough to continue directly, so the run used APP_FORCE_RESTART, completed first stage to S10_READY, then started the second stage.

## First Stage

- status: S10_READY
- S10_READY: True
- COLOR_FILTER_DONE: True
- AGE_FILTER_DONE: True
- S07_FILTER_DONE: True
- SORT_DONE: True
- trisame_count: 3
- S03 brand initial: R
- S06 gate: fast_gate_source_s05_done_model_config_bounds
- launcher later attempts: 2
- launcher later dismissed: True
- account center login detected: False

## Second Stage Result

- status: S15_BLOCKED_REPAIR_DETAILS_INCOMPLETE
- issue_code: S15_BLOCKED_REPAIR_DETAILS_INCOMPLETE
- entered S16: false
- pricing output: false

## Reference #1 Gate Validation

- reference_index: 1
- reference_title: 日产 途达 2020款 2.5L XL Upper 4WD 自动四驱豪华版
- reference_price: 8.48万
- selected_card_metadata: 2021年 | 12.5万公里 | 安庆
- s13_total_repair_count: 21
- expected_repair_item_count: 21
- enumerated_repair_item_count: 1
- collected_repair_item_count: 1
- missing_repair_item_count: 20
- all_repair_items_collect_done: False
- s15_entry_allowed: False
- s15_entry_block_reason: S15_BLOCKED_REPAIR_DETAILS_INCOMPLETE
- reference_score_trustworthy: False
- reference_score_invalid_reason: repair_details_incomplete_before_s15
- reference_score: 
- untrusted 78 score generated: false

Conclusion: the inherited V1.29 repair-items completeness gate is effective under the V1.30 execution contract. The script blocked S15 before a trustworthy reference score could be generated.

## Quality Checks

- result_s01_to_s10.json: valid JSON
- result_s10_to_s16.json: valid JSON
- result.json: valid JSON
- raw_xml / nodes / visible_blob / page_source large fields: not found by scan
- non-trisame price used: false
- old 95 percent rule used: false
- old target pollution detected: false
- code modified this round: false

## Evidence

- first_stage_log: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\logs\nissan_terra_v1_30_first_stage_20260515_171023.log
- second_stage_log: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\logs\nissan_terra_v1_30_second_stage_20260515_171301.log
- result_s01_to_s10: output/result_s01_to_s10.json
- result_s10_to_s16: output/result_s10_to_s16.json
- result: output/result.json
