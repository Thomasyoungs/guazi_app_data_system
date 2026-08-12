# Baseline Sample Registry

- generated_at: 2026-05-12T18:56:12+08:00
- final_status: DATABASE_SAMPLE_REGISTRY_UPDATED_WITH_HONDA_ACCORD_2023

## Overall Stats
- total_samples: 9
- auto_priced_success_count: 6
- manual_review_done_count: 1
- problem_sample_count: 2
- baseline_sample_count: 7
- database_main_table_ready_count: 7
- issue_case_only_count: 2

## Sample List
| sample_id | fingerprint | sample_type | final_status | suggested_purchase_price_yuan | manual_review_required | evidence_path |
|---|---|---|---|---:|---|---|
| BASELINE_FOCUS_2017_FULL_CHAIN_PRICED_DONE_202605 | 福特|福克斯|2017款|两厢 1.6L 自动舒适型智行版|白|2017.07 | FULL_CHAIN_PRICED_DONE | FULL_CHAIN_PRICED_DONE | 20890 | False | output/baseline_freeze_package.json |
| BASELINE_TOYOTA_YARIS_2015_FULL_CHAIN_PRICED_DONE_202605 | 丰田|YARiS L 致炫|2015款|1.5E 自动魅动版|白|2015.07 | FULL_CHAIN_PRICED_DONE | FULL_CHAIN_PRICED_DONE | 22410 | False | output/toyota_yaris_baseline_freeze_package.json |
| BASELINE_SANTANA_2021_FULL_CHAIN_PRICED_DONE_202605 | 大众|桑塔纳|2021款|1.5L 自动风尚版|白|2021.05 | FULL_CHAIN_PRICED_DONE | FULL_CHAIN_PRICED_DONE | 34472 | False | output/santana_baseline_freeze_package.json |
| TUANG_2017_FULL_CHAIN_PRICED_DONE_STRONG_RISK_202605 | 大众|途昂|2017款|330TSI 两驱豪华版|白|2018.07 | FULL_CHAIN_PRICED_DONE | FULL_CHAIN_PRICED_DONE | 84648 | True | output/tuang_full_chain_acceptance_report.json |
| BASELINE_LEAPMOTOR_C10_2026_FULL_CHAIN_MANUAL_REVIEW_DONE_202605 | 零跑|C10|2026款|210悦享版|白|2026.02 | FULL_CHAIN_MANUAL_REVIEW_DONE | FULL_CHAIN_MANUAL_REVIEW_DONE | 87292 | True | output/leapmotor_c10_baseline_freeze_package.json |
| PROBLEM_CHANGAN_LUMIN_EV_DEFERRED_202605 | 长安|Lumin|2026款|宝藏版 205km 酷爱米 宁德|白|2025.11 | PROBLEM_SAMPLE | PROBLEM_SAMPLE_DEFERRED |  | True | output/system_rules_contract_scoring_competition_audit.json |
| PROBLEM_PRADO_PARALLEL_IMPORT_DEFERRED_202605 | 丰田|普拉多（平行进口）|2017款|2.7L 平行进口|白|2017.08 | PROBLEM_SAMPLE | PROBLEM_SAMPLE_DEFERRED |  | True | output/system_rules_contract_scoring_competition_audit.json |
| BASELINE_GEELY_YUANJING_2019_FULL_CHAIN_PRICED_DONE_202605 | 吉利|远景|2019款|升级版 1.5L 手动豪华型 国VI|白|2019.10 | FULL_CHAIN_PRICED_DONE | FULL_CHAIN_PRICED_DONE | 16300 | False | output/geely_yuanjing_2019_baseline_freeze_package.json |
| BASELINE_HONDA_ACCORD_2023_FULL_CHAIN_PRICED_DONE_202605 | 本田|雅阁|2023款|260TURBO 智享版|黑|2024.01 | FULL_CHAIN_PRICED_DONE | FULL_CHAIN_PRICED_DONE | 97136 | False | output/honda_accord_2023_baseline_freeze_package.json |

## Database Main Table Ready Samples
- BASELINE_FOCUS_2017_FULL_CHAIN_PRICED_DONE_202605: 福特|福克斯|2017款|两厢 1.6L 自动舒适型智行版|白|2017.07
- BASELINE_TOYOTA_YARIS_2015_FULL_CHAIN_PRICED_DONE_202605: 丰田|YARiS L 致炫|2015款|1.5E 自动魅动版|白|2015.07
- BASELINE_SANTANA_2021_FULL_CHAIN_PRICED_DONE_202605: 大众|桑塔纳|2021款|1.5L 自动风尚版|白|2021.05
- TUANG_2017_FULL_CHAIN_PRICED_DONE_STRONG_RISK_202605: 大众|途昂|2017款|330TSI 两驱豪华版|白|2018.07
- BASELINE_LEAPMOTOR_C10_2026_FULL_CHAIN_MANUAL_REVIEW_DONE_202605: 零跑|C10|2026款|210悦享版|白|2026.02
- BASELINE_GEELY_YUANJING_2019_FULL_CHAIN_PRICED_DONE_202605: 吉利|远景|2019款|升级版 1.5L 手动豪华型 国VI|白|2019.10
- BASELINE_HONDA_ACCORD_2023_FULL_CHAIN_PRICED_DONE_202605: 本田|雅阁|2023款|260TURBO 智享版|黑|2024.01

## Problem Samples Not Registered As Success
- PROBLEM_CHANGAN_LUMIN_EV_DEFERRED_202605: 长安|Lumin|2026款|宝藏版 205km 酷爱米 宁德|白|2025.11
- PROBLEM_PRADO_PARALLEL_IMPORT_DEFERRED_202605: 丰田|普拉多（平行进口）|2017款|2.7L 平行进口|白|2017.08

## Quality Checks
- json_legal: True
- sample_id_unique: True
- duplicate_sample_ids: []
- fingerprints_non_empty: True
- empty_fingerprint_sample_ids: []
- success_samples_have_final_status: True
- success_missing_final_status: []
- success_samples_have_evidence_path: True
- success_missing_evidence: []
- problem_samples_have_problem_reason: True
- problem_missing_reason: []
- raw_xml_large_fields_present: False
- raw_xml_large_field_samples: []
- old_95_percent_rule_used_samples: []
- non_trisame_price_used_in_success_samples: []
- missing_source_files: []
- source_parse_warnings: []
- honda_added_or_updated: True
- pre_update_sample_count: 8
- post_update_sample_count: 9
- sample_count_increased_by_one_or_existing_updated: True
- slow_action_raw_details_not_embedded: True

DATABASE_SAMPLE_REGISTRY_UPDATED_WITH_HONDA_ACCORD_2023
