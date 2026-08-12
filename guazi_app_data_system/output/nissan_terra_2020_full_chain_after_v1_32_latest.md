# Nissan Terra Full Chain After V1.32

- status: RUN_NISSAN_TERRA_AFTER_V1_32_CONTRACT_PATCH_DONE
- final_business_status: ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW
- target_fingerprint: 日产|途达|2020款|2.5L XL Upper 4WD 自动四驱豪华版|白|2021.08
- first_stage_status: S10_READY
- second_stage_status: ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW
- entered_s16: false
- pricing_output: false

## Reference Summary
| reference_index | price_10k | score | trustworthy | first_positive_region | repair_count | S14 full sequence | S15 allowed | decision |
|---:|---:|---:|---|---|---:|---|---|---|
| 1 | 8.48 | 78.0 | True | 驾驶侧 | 10 | True | True | CONTINUE_OR_EXHAUSTED |
| 2 | 10.59 |  |  |  |  |  |  | EXCLUDED (OFFICIAL_REPORT_NOT_AVAILABLE) |
| 3 | 10.9 | 84.5 | True | 车头 | 1 | True | True | CONTINUE_OR_EXHAUSTED |

## Conclusion
- V1.32 gate validated: true
- No V1.29 1/N repair-count block: true
- Reference #1: S14 ????????? S15?reference_score=78.0 < target_score??????
- Reference #2: ?????????????
- Reference #3: S14 ????????? S15?reference_score=84.5 < target_score?????
- Final: ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW

## Quality Checks
- JSON valid: true
- fingerprint matches: false
- forbidden large field keys absent: true
- non-trisame price used: false
- old 95 percent rule used: false
- baseline overwritten: false
