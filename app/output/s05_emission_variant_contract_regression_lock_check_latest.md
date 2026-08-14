# S05 Emission Variant Contract Regression Lock Check

- Final status: `S05_EMISSION_VARIANT_CONTRACT_REGRESSION_LOCK_CHECK_PASSED`
- Read-only lock check: no code/config/pricing/DOCX changes, no real-device run, no second stage, no reference collection, no pricing run.
- Checked patch report: `C:/Users/lzc93/Desktop/定价/guazi_app_data_system/output/s05_emission_variant_result_field_persistence_patch.json`

## Offline Regression
- case_1: PASS
  - group_count: 2
  - group: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - selected: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - all_selected: True
- case_2: PASS
  - group_count: 2
  - group: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - selected: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - all_selected: True
- case_3: PASS
  - group_count: 2
  - group: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - selected: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - all_selected: True
- case_4: PASS
  - group_count: 0
  - group: []
  - selected: []
  - all_selected: None

- Offline regression all pass: `True`

## Field Persistence Check
- `s05_emission_variant_contract_enabled`: present; paths: $.offline_tests[0].field_persistence.s05_emission_variant_contract_enabled, $.offline_tests[0].public_group_evidence.s05_emission_variant_contract_enabled, $.offline_tests[1].field_persistence.s05_emission_variant_contract_enabled, $.offline_tests[1].public_group_evidence.s05_emission_variant_contract_enabled, $.offline_tests[2].field_persistence.s05_emission_variant_contract_enabled, $.offline_tests[2].public_group_evidence.s05_emission_variant_contract_enabled, $.offline_tests[3].field_persistence.s05_emission_variant_contract_enabled, $.offline_tests[3].public_group_evidence.s05_emission_variant_contract_enabled
- `normalized_target_config`: present; paths: $.offline_tests[0].field_persistence.normalized_target_config, $.offline_tests[0].public_group_evidence.normalized_target_config, $.offline_tests[1].field_persistence.normalized_target_config, $.offline_tests[1].public_group_evidence.normalized_target_config, $.offline_tests[2].field_persistence.normalized_target_config, $.offline_tests[2].public_group_evidence.normalized_target_config, $.offline_tests[3].field_persistence.normalized_target_config, $.offline_tests[3].public_group_evidence.normalized_target_config
- `emission_variant_group`: present; paths: $.offline_tests[0].field_persistence.emission_variant_group, $.offline_tests[0].public_group_evidence.emission_variant_group, $.offline_tests[1].field_persistence.emission_variant_group, $.offline_tests[1].public_group_evidence.emission_variant_group, $.offline_tests[2].field_persistence.emission_variant_group, $.offline_tests[2].public_group_evidence.emission_variant_group, $.offline_tests[3].field_persistence.emission_variant_group, $.offline_tests[3].public_group_evidence.emission_variant_group
- `emission_variant_group_count`: present; paths: $.offline_tests[0].field_persistence.emission_variant_group_count, $.offline_tests[0].public_group_evidence.emission_variant_group_count, $.offline_tests[1].field_persistence.emission_variant_group_count, $.offline_tests[1].public_group_evidence.emission_variant_group_count, $.offline_tests[2].field_persistence.emission_variant_group_count, $.offline_tests[2].public_group_evidence.emission_variant_group_count, $.offline_tests[3].field_persistence.emission_variant_group_count, $.offline_tests[3].public_group_evidence.emission_variant_group_count
- `selected_emission_variants`: present; paths: $.offline_tests[0].field_persistence.selected_emission_variants, $.offline_tests[1].field_persistence.selected_emission_variants, $.offline_tests[2].field_persistence.selected_emission_variants, $.offline_tests[3].field_persistence.selected_emission_variants
- `selected_count_expected`: present; paths: $.offline_tests[0].field_persistence.selected_count_expected, $.offline_tests[1].field_persistence.selected_count_expected, $.offline_tests[2].field_persistence.selected_count_expected, $.offline_tests[3].field_persistence.selected_count_expected
- `s05_emission_variant_all_selected`: present; paths: $.offline_tests[0].field_persistence.s05_emission_variant_all_selected, $.offline_tests[1].field_persistence.s05_emission_variant_all_selected, $.offline_tests[2].field_persistence.s05_emission_variant_all_selected, $.offline_tests[3].field_persistence.s05_emission_variant_all_selected

## Old Logic Impact
- s03_changed: `False`
- s04_changed: `False`
- s07_changed: `False`
- s10_to_s14_changed: `False`
- second_stage_changed: `False`
- pricing_changed: `False`
- config_changed: `False`
- competition_coefficient_changed: `False`
- service_fee_tiers_changed: `False`
- The persistence patch only touched first-stage S05 result/context fields.
- No real-device or second-stage run was executed.

Final status: `S05_EMISSION_VARIANT_CONTRACT_REGRESSION_LOCK_CHECK_PASSED`
