# Nissan Terra 2020 Full Chain Fast Gate Test

## Overall
- Task: `RUN_FULL_CHAIN_NISSAN_TERRA_2020_WITH_S06_FAST_GATE_AND_S10_HANDOFF_FAST_GATE_TEST`
- Fingerprint: `??|??|2020?|2.5L XL Upper 4WD ???????|?|2021.08`
- Overall status: `RUN_FAILED_WITH_ISSUE`
- Stop code: `S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED`
- Final state: `RUN_FULL_CHAIN_NISSAN_TERRA_2020_WITH_S06_FAST_GATE_AND_S10_HANDOFF_FAST_GATE_TEST_DONE`

## First Stage
- Status: `S10_READY`
- S10_READY: `True`
- S07_FILTER_DONE: `True`
- COLOR_FILTER_DONE: `True`
- AGE_FILTER_DONE: `True`
- SORT_DONE: `True`
- Trisame count: `3`
- Complete target vehicle card count: `3`
- S10 ready reason: `source_gate_core_elements_target_trisame_boundary_passed`

## Fast Gate Checks
- S06 recognized by: `fast_gate_source_s05_done_model_config_bounds`
- S06 page variant: `S06_TARGET_FILTER_LIST_AFTER_S05_CONFIRM`
- S06 to S07 result: `entered_s07`
- Second-stage S10 fast handoff passed: `True`
- S10 handoff core elements: `['price_low_to_high_sort_signal', 'complete_target_vehicle_card', 'target_trisame_evidence']`
- S10 handoff strong error signals: `[]`
- S10 fast gate duration ms: `2`
- Second-stage start to reference click ms: `3`

## Second Stage
- Status: `S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED`
- Issue code: `S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED`
- Current reference index: `3`
- Reference history count: `3`

## Reference History
- #1 `日产 途达 2020款 2.5L XL Upper 4WD 自动四驱豪华版` price=84800 score=78.0 gte_target=False status=None s14_done=True
- #2 `日产 途达 2020款 2.5L XL Upper 4WD 自动四驱豪华版` price=105900 score=None gte_target=None status=EXCLUDED_OFFICIAL_REPORT_NOT_AVAILABLE s14_done=None
- #3 `日产 途达 2020款 2.5L XL Upper 4WD 自动四驱豪华版` price=109000 score=None gte_target=None status=None s14_done=False

## Stop Explanation
The run did not enter S16, so no final pricing was emitted. The chain stopped at S14 with `S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED` while processing image sequence evidence for reference #3. This is outside the user-authorized S06 fast gate and S10 handoff fast gate modification scope, so no S14 code was changed during this run.

## Quality
- JSON valid: `true`
- raw XML / nodes / visible_blob / page_source large fields present: `false`
- Old target pollution detected: `false`
- Used non-trisame price: `false`
- Used old 95% payout rule: `false`
- Baseline overwritten: `false`

## Evidence
- First stage log: `logs/nissan_terra_2020_first_stage_20260513_104316.log`
- Second stage log: `logs/nissan_terra_2020_second_stage_20260513_104545.log`
- Continuation log: `logs/nissan_terra_2020_second_stage_continue_2_20260513_105004.log`
