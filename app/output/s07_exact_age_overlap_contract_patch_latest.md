# S07 Exact Age Overlap Contract Patch

Final status: **PATCH_S07_EXACT_AGE_OVERLAP_CONTRACT_AND_SCRIPT_FIX_DONE**

## Scope

- Modified first-stage S07 exact-age logic only.
- Updated project markdown page-contract note only; main DOCX was not modified.
- Did not modify second-stage script, pricing, or non-S07 config.
- Did not start the second stage.

## Target

`零跑|C10|2026款|210悦享版|白|2026.02`

## Contract Added

- Exact age slider handles may overlap; overlap is a valid success state.
- Runtime must prefer fresh page result text over independent right-handle visibility.
- For `target_age=0`, any of `0年以下`, `0-0年`, or `0年` plus a refreshed `查看X辆` button verifies exact age.
- `target_age=1` is the hidden tick between `0` and `2` and must verify `1-1年`.
- Existing `11/12` hidden tick rules remain.
- `target_age > 12` must not map to `不限`.

## Offline Validation

- XML: `artifacts/debug/s07_age_track_based_drag_right_1_20260510_132326.xml`
- Screenshot: `artifacts/screenshots/s07_age_track_based_drag_right_1_20260510_132326.png`
- Matched text: `0年以下`
- Bottom button: `查看33辆`
- Result: `AGE_FILTER_DONE_should_be_true=true`; old `S07_RIGHT_AGE_SLIDER_MOVE_NO_EFFECT` is no longer emitted for zero age.

## Live First-Stage Validation

- First-stage status: `S10_READY_AFTER_SORT_NOT_CONFIRMED`
- `COLOR_FILTER_DONE=True`
- `AGE_FILTER_DONE=True`
- `S07_FILTER_DONE=True`
- `SORT_DONE=False`
- `S10_READY=False`

S07 passed on live device. The run then stopped later at `S10_READY_AFTER_SORT_NOT_CONFIRMED`, which is outside this S07-only patch scope.

## Files Modified

- `scripts/runtime_s01_to_s10_mainline.py`
- `docs/page_state_machine.md`

## Verification

- `py_compile scripts/runtime_s01_to_s10_mainline.py`: passed
- `output/result_s01_to_s10.json`: valid JSON and sanitized
- `output/result.json`: valid JSON and sanitized
