# Target score persistence for terminal output patch

## Status

TARGET_SCORE_PERSISTED_FOR_TERMINAL_OUTPUT

## Modified scope

- Modified: `scripts/runtime_s10_to_s16_mainline.py`
- Scope: target score persistence/extraction for terminal output only
- Collection/S14/pricing/scoring/reference-score logic changed: no

## Implementation

The runtime now persists the real target score computed by `score_target(...)` during S15 into the current reference record:

- `current_reference.target_score`
- `current_reference.target_score_source=score_target_runtime_s15`

The terminal manual-review output extractor can read target score from real runtime fields only, in this order:

1. `result.target_score`
2. `issue_context.target_score`
3. `current_reference.target_score`
4. `reference_history[].target_score`

It does not infer target score from `reference_score=70.0` or `score_diff_reference_minus_target`.

## Offline validation

| Check | Result |
| --- | --- |
| sample with runtime target_score => available true | True |
| sample with runtime target_score => target_score 92.0 | True |
| manual_review_payload target_score populated | True |
| s17_manual_review_payload target_score populated | True |
| sample without runtime target_score keeps null | True |
| missing reason is explicit | True |
| no score_delta inference | True |
| reference #1 remains 70.0 trustworthy | True |
| old 78.0 absent | True |
| final_price remains null | True |
| auto_pricing_allowed remains false | True |
| manual_review_required remains true | True |
| reference #2 deduped once | True |
| reference #2 reason OFFICIAL_REPORT_NOT_AVAILABLE | True |

## Missing target-score behavior

If a terminal result truly has no runtime `target_score` anywhere in the allowed sources, output remains:

- `target_score=null`
- `target_score_available=false`
- `target_score_missing_reason=target_score_not_available_in_runtime_context`

## Py Compile

Passed: `python -m py_compile scripts/runtime_s10_to_s16_mainline.py` using bundled Python.
