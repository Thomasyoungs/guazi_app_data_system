# S03 Contract Violation Branch Cleanup Verification

- status: S03_CONTRACT_VIOLATION_BRANCHES_REMOVED_VERIFIED_CLEAN
- real_device_run: false
- second_stage_run: false
- modified_file: scripts/runtime_s01_to_s10_mainline.py
- py_compile: passed

## Removed / Disabled Branches
- S03 target-visible auxiliary new-energy-tab click branch
- S03 target-visible/right-letter click branch
- S03 linear scroll branch after target not found in current screen
- S03 old action extension that added tap_new_energy to page actions
- S04 brand-zone continuation fields for target_series_verified / visible_series_chips / selected_series_alias
- S03_TO_S04 timing action name that referenced letter/scroll path

## Offline Replay
- target visible XML: `C:/Users/lzc93/Desktop/定价/guazi_app_data_system/artifacts/debug/s03_after_letter_L_20260510_145401.xml`
- target_brand_visible: True
- matched_alias: 零跑汽车
- next_action: S03_ONLY_ALLOWED_ACTION_CLICK_BRAND_ROW_RIGHT_SAFE_POINT
- selected_click_region_type: brand_row_right_safe_point
- selected_click_point: [1196, 607]
- attempted_new_energy_tab: False
- attempted_letter_L: False
- attempted_letter_G: False
- attempted_scroll: False
- simulated forbidden action stop_code: S03_VISIBLE_TARGET_BRAND_NOT_CLICKED_BY_ONLY_ALLOWED_ACTION
- brand zone XML: `C:/Users/lzc93/Desktop/定价/guazi_app_data_system/artifacts/debug/s03_to_s04_20260510_145409.xml`
- brand_zone_page_detected: True
- brand_zone_continuation_allowed: False
- brand_zone_stop_code: S03_CLICKED_BRAND_ZONE_INSTEAD_OF_BRAND

## Residual Keyword Check
| keyword | present | hit_count | executable_violation_branch | classification |
|---|---:|---:|---:|---|
| ????? | False | 0 | False | absent |
| new_energy | True | 1 | False | non-executing audit/label/filter text; S03 no longer taps tabs or letters |
| ??? | False | 0 | False | absent |
| attempted_tabs | False | 0 | False | absent |
| attempted_letters | False | 0 | False | absent |
| letter_L | True | 1 | False | non-executing audit/label/filter text; S03 no longer taps tabs or letters |
| letter_G | True | 1 | False | non-executing audit/label/filter text; S03 no longer taps tabs or letters |
| alphabet | False | 0 | False | absent |
| scroll_brand | False | 0 | False | absent |
| brand zone | True | 1 | False | stop_only_or_diagnostic; brand-zone continuation is blocked, not executable as normal flow |
| BRAND_ZONE | True | 36 | False | stop_only_or_diagnostic; brand-zone continuation is blocked, not executable as normal flow |
| ???? | False | 0 | False | absent |
| target_series_verified | False | 0 | False | absent |
| C10 | True | 1 | False | stop_only_or_diagnostic; brand-zone continuation is blocked, not executable as normal flow |
| ??C10 | False | 0 | False | absent |
| ???? | False | 0 | False | absent |
| BRAND_ZONE_MIXED_LIST | True | 8 | False | stop_only_or_diagnostic; brand-zone continuation is blocked, not executable as normal flow |
| fallback | False | 0 | False | absent |
| ?? | False | 0 | False | absent |
| ?? | False | 0 | False | absent |

## Conclusion
No executable S03 contract-violation branch remains. It is allowed to enter the next step.

Final status: S03_CONTRACT_VIOLATION_BRANCHES_REMOVED_VERIFIED_CLEAN
