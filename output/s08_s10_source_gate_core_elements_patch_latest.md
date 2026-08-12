# S08/S10 Source Gate Core Elements Patch Report

## Final Status

S08_S10_SOURCE_GATE_CORE_ELEMENTS_PATCHED_S10_READY

## Target

- fingerprint: 零跑|C10|2026款|210悦享版|白|2026.02
- scope: 第一段 `scripts/runtime_s01_to_s10_mainline.py`
- second stage: not run
- pricing: not run

## Modified File

- `scripts/runtime_s01_to_s10_mainline.py`

## Patch Summary

1. Added `S08_TARGET_LIST_AFTER_FILTER` recognition after `S07_VIEW_RESULT_TO_LIST`.
2. Persisted `S07_FILTER_DONE=true` and `transition_context=S07_VIEW_RESULT_TO_LIST` after color and age were confirmed and `查看X辆` was clicked.
3. Prevented S06 re-recognition after S07 was already complete.
4. Added S08 source gate, core element, and reverse exclusion checks.
5. Added S09 sort popup recognition for the current app sort sheet.
6. Added S10_READY source gate, core element, target trisame evidence, and boundary checks.
7. Added kilometer metadata parsing for cards like `2026年 | 300公里 | 北京`.

## Offline Replay

| Scenario | Result |
| --- | --- |
| S07 view-result page with `S07_FILTER_DONE=true` | recognized as `S08_TARGET_LIST_AFTER_FILTER` |
| S07 view-result page without `S07_FILTER_DONE` | not recognized as S08/S10_READY |
| Sort popup after clicking `综合排序` | recognized as S09 |
| List after sorting without current target trisame evidence | not accepted as final current-target S10_READY positive |
| More/recommend source boundary | excluded from trisame ready evidence |

## Real Device First-Stage Validation

- device: `ANDROID_SERIAL=6TGYYHPZCETCSK6L`
- log: `output/s08_s10_source_gate_first_stage_20260512_105611.log`
- command result: exit code 0
- first-stage status: `S10_READY`
- `S03`: followed V1.16 contract
- `S04`: matched target series `零跑C10`
- `S05`: selected `2026款 + 210悦享版`
- `S06`: recognized as `S06_TARGET_FILTER_LIST_AFTER_S05_CONFIRM`
- `S07`: completed color `白色` and age `0年以下`
- `S08`: recognized as target list after filter, clicked `综合排序`
- `S09`: clicked `价格从低到高`
- `S10`: recognized reliable ready state after sort

## S10 Evidence

- `S07_FILTER_DONE=true`
- `COLOR_FILTER_DONE=true`
- `AGE_FILTER_DONE=true`
- `SORT_DONE=true`
- `S10_READY=true`
- `s08_source_gate_passed=true`
- `s08_reverse_exclusion_passed=true`
- `s10_source_gate_passed=true`
- `s10_reverse_exclusion_passed=true`
- `complete_target_vehicle_card_count=2`
- `non_trisame_boundary_detected=true`
- `s10_ready_reason=source_gate_core_elements_target_trisame_boundary_passed`

Target trisame cards:

1. `零跑汽车 零跑C10 2026款 210悦享版` - `10.64万` - `2026年 | 0.17万公里 | 唐山 | LeapPilot`
2. `零跑汽车 零跑C10 2026款 210悦享版` - `10.86万` - `2026年 | 0.77万公里 | 唐山 | LeapPilot`

## Result Files

- `output/result_s01_to_s10.json`: valid JSON, fingerprint matches target
- `output/result.json`: valid JSON, fingerprint matches target
- raw XML / nodes / visible blob fields: not written to result JSON
- baseline files: not overwritten

## Next Step

First stage is ready at S10. The second stage may be run in a separate requested turn.
