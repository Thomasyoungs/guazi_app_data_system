# Nissan Terra 2020 Full Chain After V1.27

- status: RUN_NISSAN_TERRA_AFTER_V1_27_CONTRACT_PATCH_DONE
- final_status: ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW
- target: 日产|途达|2020款|2.5L XL Upper 4WD 自动四驱豪华版|白|2021.08
- first_stage_status: S10_READY
- second_stage_status: ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW
- trisame_count: 3
- target_score: 92.0

## Reference History
- #1: price=8.48?, score=78.0, result=BELOW_TARGET_CONTINUE, diff=-14.0, s11=V1_27_exact_text_half_screen_bottom_reposition, s14=SINGLE_IMAGE_WITH_CAPTION
- #2: price=10.59?, score=None, result=EXCLUDED_OFFICIAL_REPORT_NOT_AVAILABLE, diff=None, s11=V1_27_exact_text_half_screen_bottom_reposition, s14=None
- #3: price=10.9?, score=84.5, result=BELOW_TARGET_CONTINUE, diff=-7.5, s11=V1_27_exact_text_half_screen_bottom_reposition, s14=SINGLE_IMAGE_WITH_CAPTION

## Outcome
- manual_review_required: True
- reason: ?? 3 ???????????? target_score ?????????????? S16?????????
- pricing_emitted: False
- non_trisame_used: False
- old_95_percent_rule_used: False

## Contract Evidence
- s06_fast_gate_rule: None
- s06_to_s07_result: entered_s07
- s10_handoff_fast_gate_observed: True
- s11_v1_27_rule_observed: True
- s14_single_image_completion_observed: True
- reference_state_reset_observed: True
- reference_state_leak_detected: False

## Result Quality
- output/result_s01_to_s10.json: json_valid=True, forbidden_large_fields=[]
- output/result_s10_to_s16.json: json_valid=True, forbidden_large_fields=[]
- output/result.json: json_valid=True, forbidden_large_fields=[]
