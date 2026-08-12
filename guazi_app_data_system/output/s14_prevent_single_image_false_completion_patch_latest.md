# S14 ?? 1/1 + caption ???????

???`S14_PREVENT_SINGLE_IMAGE_FALSE_COMPLETION_PATCHED`

## ????

- ?????`scripts/runtime_s10_to_s16_mainline.py`
- ?????????pricing?config?DOCX?baseline????????????????

## ????

- Removed executable completion from local 1/1 + caption; it now only records current image readability.
- Added XML/text based uncollected-next-condition-signal detection from unvisited S14 tab labels and damage lines.
- S14 completion now requires no_new_semantic_after_swipe_count >= 2, no uncollected next condition signal, and at least one collected S14 image.
- S14 failure marks reference_score_trustworthy=false and reference_score_invalid_reason=s14_full_image_sequence_incomplete_before_s15.

## ????

| ?? | ?? |
|---|---|
| `A_NISSAN_TERRA_FALSE_SINGLE_IMAGE_COMPLETION` | PASS |
| `B_TRUE_SINGLE_IMAGE_TERMINAL` | PASS |
| `C_MULTI_IMAGE_TERMINAL` | PASS |
| `D_NO_EFFECTIVE_SWIPE_WITH_UNCOLLECTED_SIGNAL` | PASS |
| `E_S15_BLOCKS_WHEN_S14_INCOMPLETE` | PASS |

## py_compile

- `python -m py_compile scripts/runtime_s10_to_s16_mainline.py`?PASS

## ????

- `s14_has_uncollected_next_condition_signal`
- `s14_uncollected_next_condition_signals`
- `s14_no_new_semantic_after_swipe_count`
- `reference_score_trustworthy`
- `reference_score_invalid_reason`

?? raw XML / nodes / visible_blob / page_source ????
