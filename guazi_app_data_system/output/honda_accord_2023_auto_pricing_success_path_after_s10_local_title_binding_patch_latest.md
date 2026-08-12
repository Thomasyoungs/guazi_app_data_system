# Honda Accord Auto Pricing Success Path After S10 Local Title Binding Patch

- target_fingerprint: 本田|雅阁|2023款|260TURBO 智享版|黑|2024.01
- patch_status: S10_SELECTED_CARD_LOCAL_TITLE_BINDING_PATCHED
- offline_validation_status: S10_SELECTED_CARD_LOCAL_TITLE_BINDING_OFFLINE_VALIDATED
- first_stage_status: S10_READY
- runtime_final_status: REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL
- success_status: SECOND_STAGE_DID_NOT_REACH_S16

## Runtime Result

The V1.37 local title binding patch passed its targeted runtime check: the old S10_TITLE_TEXT_NODE_NOT_UNIQUE blocker did not recur, and selected cards were clicked through selected_reference_card_container binding despite duplicate page-level titles.

The full AUTO_PRICING_SUCCESS_PATH did not complete because the run stopped before S16 at a newer selected-card completeness gate: REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL for reference #7.

## Failure Evidence

- issue_code: REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL
- current_reference_index: 7
- next_reference_index: 7
- binding_stop_code: REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL
- binding_reason: Target reference card is only partially visible and is missing required price/metadata evidence.
- binding_target_reference_index: 7
- screenshot_path: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s11_report_missing_return_s10_1_20260516_171604.png
- xml_path: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s11_report_missing_return_s10_1_20260516_171604.xml

## Reference History

| reference_index | reference_score | trustworthy | s14_collect_done | exclusion |
|---:|---:|---|---|---|
| 1 | None | None | None | None |
| 2 | 83.0 | True | True | None |
| 3 | 76.0 | True | True | None |
| 4 | 81.0 | True | True | None |
| 5 | 74.5 | True | True | None |
| 6 | None | None | None | None |

## Regression Checks

- s10_selected_card_autoscroll_no_regression: True
- s11_report_entry_reposition_no_regression_observed: True
- metal_deduct_no_keyerror_observed: True
- s14_no_regression_observed: True
- old_title_global_uniqueness_block_removed: True

## Final Status

SECOND_STAGE_DID_NOT_REACH_S16
