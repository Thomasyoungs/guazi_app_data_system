# ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW structured output patch

## Status

ALL_REFERENCES_OUTPUT_PATCHED_TARGET_SCORE_NOT_PERSISTED

## Modified scope

- Modified: `scripts/runtime_s10_to_s16_mainline.py`
- Scope: terminal manual review output enrichment only
- Collection flow changed: no
- S14 logic changed: no
- Pricing/scoring/config/baseline changed: no
- `result.json` overwritten during validation: no

## Implementation

When the second-stage writer sees `ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW`, it now enriches the terminal result with:

- `manual_review_required=true`
- `auto_pricing_allowed=false`
- `final_price=null`
- `final_price_status=not_generated`
- `final_price_block_reason=all_references_exhausted_manual_review`
- deduped `reference_outcomes`
- count fields for processed/exhausted/excluded/trusted-scored/auto-pricing references
- `manual_review_payload` and `s17_manual_review_payload` for downstream consumers

## Offline validation

Source sample: `output/result_s10_to_s16.json`

| Check | Result |
| --- | --- |
| manual_review_required | True |
| auto_pricing_allowed=false | True |
| final_price=null | True |
| deduped reference outcomes count | 2 |
| reference #2 appears once | True |
| old 78.0 absent | True |
| reference #1 score 70.0 trustworthy | True |
| reference #2 OFFICIAL_REPORT_NOT_AVAILABLE | True |
| target_score not inferred | True |
| manual_review_payload present | True |
| s17_manual_review_payload present | True |

## Reference outcomes preview

| reference_index | outcome | score | trustworthy | exclusion_reason | included_in_auto_pricing |
| --- | --- | --- | --- | --- | --- |
| 1 | scored_but_below_target | 70.0 | True | None | False |
| 2 | excluded | None | None | OFFICIAL_REPORT_NOT_AVAILABLE | False |

## Target score handling

Current terminal sample does not persist runtime `target_score`, so the patch outputs:

- `target_score=null`
- `target_score_available=false`
- `target_score_source=missing_runtime_target_score`
- `target_score_missing_reason=target_score_not_persisted_in_terminal_result`

It does not infer target score from reference #1 score `70.0` or score delta.

## Py Compile

Passed: `python -m py_compile scripts/runtime_s10_to_s16_mainline.py` using bundled Python.

## Final

No final price is generated, S16 is not entered, and the terminal payload is now suitable for manual review routing.
