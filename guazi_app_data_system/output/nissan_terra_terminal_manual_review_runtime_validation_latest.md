# Nissan Terra Terminal Manual Review Runtime Validation

## Status

NISSAN_TERRA_TERMINAL_MANUAL_REVIEW_RUNTIME_VERIFIED

## Execution

- Code modified this round: no
- First stage status: S10_READY
- Second stage status: ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW
- Fingerprint: 日产|途达|2020款|2.5L XL Upper 4WD 自动四驱豪华版|白|2021.08

## Key Checks

| Check | Result |
| --- | --- |
| S10_READY before second stage | True |
| S14 full sequence runtime verified | True |
| S14 image records count | 27 |
| S14 horizontal swipes count | 28 |
| stale first-line warning observed and non-blocking | True |
| reference #1 score trustworthy | True |
| reference #1 score | 70.0 |
| old 78.0 absent | True |
| reference #2 deduped once in outcomes | True |
| reference #2 OFFICIAL_REPORT_NOT_AVAILABLE | True |
| terminal status ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW | True |
| manual_review_required=true | True |
| auto_pricing_allowed=false | True |
| final_price=null | True |
| final_price_status=not_generated | True |

## Target Score

| Location | Value |
| --- | --- |
| top-level result.target_score | 92.0 |
| manual_review_payload.target.target_score | 92.0 |
| s17_manual_review_payload.target.target_score | 92.0 |
| top-level target_score_source | reference_history[1].target_score |
| runtime origin source | score_target_runtime_s15 |

Note: top-level `target_score_source` records the extraction location. The original runtime computation source is persisted on reference #1 as `score_target_runtime_s15`.

## Outcome

All true tri-same references were processed: reference #1 was fully collected and scored below target; reference #2 was excluded because the official full report was unavailable. No S16 pricing was generated.
