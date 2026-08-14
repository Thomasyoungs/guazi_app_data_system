# S05 Emission Variant Result Field Persistence Patch

- Final status: `S05_EMISSION_VARIANT_RESULT_FIELD_PERSISTENCE_PATCHED`
- Modified file: `scripts/runtime_s01_to_s10_mainline.py`
- Business selection logic changed: `False`
- Real device run: `False`; second stage started: `False`; pricing run: `False`
- Raw XML / large node fields written: `False`

## Persisted Fields
- `s05_emission_variant_contract_enabled`
- `target_year_model`
- `target_config_model`
- `normalized_target_config`
- `emission_variant_group`
- `emission_variant_group_count`
- `selected_emission_variants`
- `selected_count_text`
- `selected_count_expected`
- `s05_emission_variant_all_selected`
- `selected_count_actual`
- `s05_single_trim_selected`
- `candidate_trim_names`
- `normalized_candidate_groups`
- `missing_emission_variants`
- `reason`

## Offline Tests
- test_1_target_without_emission: PASS
  - group_count: 2
  - group: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - selected: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - selected_count_text: 已选2项
  - all_selected: True
- test_2_target_with_emission: PASS
  - group_count: 2
  - group: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - selected: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - selected_count_text: 已选2项
  - all_selected: True
- test_3_similar_configs_excluded: PASS
  - group_count: 2
  - group: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - selected: 2019款 180TURBO CVT尚悦版 国V, 2019款 180TURBO CVT尚悦版 国VI
  - selected_count_text: 已选2项
  - all_selected: True
- test_4_single_config_unchanged: PASS
  - group_count: 0
  - group: []
  - selected: []
  - selected_count_text: 已选1项
  - all_selected: None

Final status: `S05_EMISSION_VARIANT_RESULT_FIELD_PERSISTENCE_PATCHED`
