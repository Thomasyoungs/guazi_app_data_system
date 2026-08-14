# V1.27 Fixed Script Contract Patch

- status: V1_27_FIXED_SCRIPT_CONTRACT_PATCHED
- target: ??|??|2020?|2.5L XL Upper 4WD ???????|?|2021.08
- modified_files: scripts/runtime_s01_to_s10_mainline.py, scripts/runtime_s10_to_s16_mainline.py

## Contract Changes
- s06: V1.27 minimal gate: S05_CONFIRM_TO_S06 + S05_DONE + bindable ???? -> S06_TARGET_FILTER_LIST_AFTER_S05_CONFIRM and click model config.
- s11: V1.27 XML/text exact ?????? only; before hit half-screen scroll; after hit bottom-safety gate; if bottom blocked use bounded small reposition; no OCR/visual/local structure.
- reference_reset: Reset S11/S14/scroll/exclusion/caption/signature state at each new reference_index.

## Verification
- py_compile runtime_s01_to_s10_mainline.py: passed
- py_compile runtime_s10_to_s16_mainline.py: passed
- offline_validation: passed

## S06 Evidence
- s06_fast_gate_enabled: None
- s06_fast_gate_rule: None
- recognized_page_after_s05_confirm: S06
- s06_page_variant: S06_TARGET_FILTER_LIST_AFTER_S05_CONFIRM
- model_config_entry_visible: None
- model_config_entry_bounds: None
- s06_to_s07_result: entered_s07

## Residual Check
- local_structure_binding_executable_call_sites: 0
- local_structure_binding_function_definition_present_but_disabled: True
- ocr_visual_screenshot_text_binding_enabled: False
- old_bottom_reposition_stop_present: False
- old_single_half_visible_click_path_present: False

## Result Quality
- output/result_s01_to_s10.json: json_valid=True, forbidden_large_fields=[]
- output/result_s10_to_s16.json: json_valid=True, forbidden_large_fields=[]
- output/result.json: json_valid=True, forbidden_large_fields=[]
