# Honda Accord 2023 Auto Pricing Success Path Runtime Validation

## Status

S11_FIRST_SCROLL_RUNTIME_FAILED

Actual stop code:

`S11_REPORT_ENTRY_NOT_FULLY_VISIBLE_SAFE`

## Target

- fingerprint: 本田|雅阁|2023款|260TURBO 智享版|黑|2024.01
- first_stage_status: S10_READY
- second_stage_status: S11_REPORT_ENTRY_NOT_FULLY_VISIBLE_SAFE
- entered_s16: false
- auto_pricing_verified: false

## Patch Verification Before Runtime

- S10 selected card autoscroll patch: applied
- S11 first 1/3 scroll patch: applied
- py_compile: passed
- offline validation A-F: passed

## Runtime Findings

First stage reached `S10_READY` with reliable S10 and price ascending target trisame list.

Second stage started from reliable S10. S10 handoff passed, bottom partial cards were treated as allowed page state, and the selected reference card gate passed for reference #2.

S11 report search executed the new first-scroll behavior:

- `s11_first_scroll_done=true`
- `s11_first_scroll_step_px=894`
- `s11_report_search_scroll_mode=fine`
- `s11_report_search_iterations=4`

The exact XML text `查看完整报告` was found. The candidate was recorded as:

- bounds: `[80, 2194, 597, 2308]`
- full visible: true
- bottom bar overlap: false
- candidate safe clickable: true
- stop_scroll_reason: `VIEW_FULL_REPORT_FULLY_VISIBLE`

However, the click target validator still returned:

`S11_REPORT_ENTRY_NOT_FULLY_VISIBLE_SAFE`

with reason:

`exact report entry is present but blocked by the bottom action bar`

This means the runtime did not reach S12/S13/S14/S15/S16 in this run.

## Evidence

- screenshot: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s11_report_entry_search_3_20260516_144639.png`
- xml: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s11_report_entry_search_3_20260516_144639.xml`
- result: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\output\result_s10_to_s16.json`

## Conclusion

The S10 selected-card partial-card patch did not regress in this runtime. The S11 first-scroll contract executed, but downstream S11 click safety validation blocked the exact report entry even after the search logic classified it as fully visible. No final price or pricing payload was produced.
