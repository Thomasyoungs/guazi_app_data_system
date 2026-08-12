# Honda Accord AUTO_PRICING_SUCCESS_PATH Runtime Validation

## Status

`SECOND_STAGE_DID_NOT_REACH_S16`

## What Ran

- Nissan Terra closure evidence archived before run: yes
- Target switched to: `本田|雅阁|2023款|260TURBO 智享版|黑|2024.01`
- First stage script run: yes
- Second stage script run: yes
- Code modified: no
- Manual click used: no

## First Stage

| Field | Value |
| --- | --- |
| status | S10_READY |
| S10_READY | True |
| trisame_count | 10 |

## Second Stage

| Field | Value |
| --- | --- |
| status | SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED |
| issue_code | SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED |
| recognized_state | S10 |
| reached S16 | False |
| final price generated | False |

## Failure Diagnosis

Second-stage fast handoff stopped before S11. The S10 page was reliable and complete target cards were visible, but a bottom partial target card triggered the strong error signal:

- strong_error_signals: `['target_partial_card_only']`
- partial_card_count: `1`
- target_card_visible: `True`
- target_partial_card_visible: `True`

This is a page-contract protection stop, not an automatic pricing result.

## Evidence

- `output/result_s01_to_s10.json`
- `output/result_s10_to_s16.json`
- `output/result.json`
- S10 screenshot: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s10_s16_start_20260516_121222.png`
- S10 XML: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s10_s16_start_20260516_121222.xml`

## Recommendation

Next step should be a read-only diagnosis of whether `target_partial_card_only` is too strict when multiple complete target cards are already visible and the intended reference card is complete. No bypass was performed in this run.
