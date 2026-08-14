# S10 Reference Card Completeness Gate Patch

Final status: `PATCH_ONLY_S10_REFERENCE_CARD_COMPLETENESS_GATE_AND_SCROLL_TO_COMPLETE_DONE`

## Scope

- Modified only: `scripts/runtime_s10_to_s16_mainline.py`
- First-stage script: unchanged
- Pricing / service fee logic: unchanged
- Config and page contract documents: unchanged
- S11/S12/S13/S14 logic: unchanged

## Patch Summary

1. Added complete-card evidence gates for S10 reference cards:
   - title present
   - price present and parseable
   - metadata present
   - year / mileage / city parseable
   - card bounds and click target are not bottom residual fragments
2. Incomplete cards are recorded as `partial_card_candidate` and are not included in clickable `canonical_reference_order`.
3. If target reference card is only partially visible, runtime records `S10_REFERENCE_CARD_PARTIAL_VISIBLE`, performs controlled S10 list scroll, fresh dumps again, and reparses before binding.
4. If a previous invalid partial reference exists in `reference_history`, continuation sanitizes it and recovers `next_reference_index=5`.
5. Clicking remains blocked when price or metadata is empty.

## Offline Validation

Input XML:

- `artifacts/debug/s10_s16_start_20260509_143020.xml`

Result:

- `invalid_partial_reference_detected=true`
- `invalid_partial_reference_index=5`
- `continuation_recovered_next_reference_index=5`
- valid reference history count recovered to `4`
- complete card count: `4`
- partial card count: `1`
- partial card has `missing_price=true`
- partial card has `missing_metadata=true`
- selection result: `S10_REFERENCE_CARD_PARTIAL_VISIBLE`
- click allowed: `false`

Post-refinement validation:

- S10 list completion scroll distance tightened to `650px` on the failing XML.
- Offline expected-card rebinding after completion scroll selects `4.05万 / 2021年 | 7.61万公里 | 唐山` as reference index 5.

## Device Validation

First stage:

- status: `S10_READY`
- fingerprint: `大众|桑塔纳|2021款|1.5L 自动风尚版|白|2021.05`
- `S07_FILTER_DONE=true`
- `COLOR_FILTER_DONE=true`
- `AGE_FILTER_DONE=true`
- `SORT_DONE=true`
- `S10_READY=true`

Second stage:

- status: `FULL_CHAIN_PRICED_DONE`
- invalid partial reference was detected and recovered from index 6 back to index 5.
- completion scroll was used before selecting reference index 5.
- selected reference card was complete before click.
- result JSON remained legal and contains the Santana fingerprint.
- no raw XML blob was written to result JSON.

Current result summary:

- selected reference index: `5`
- selected card price: `4.16万`
- selected card metadata: `2021年 | 4.62万公里 | 唐山`
- selected card complete: `true`
- reference score: `96.0`
- target score: `91.0`
- final status: `FULL_CHAIN_PRICED_DONE`
- guazi price: `41600`
- guazi service fee: `2500`
- guazi net payout: `39100`
- suggested acquisition price: `34472`

## Guardrails Confirmed

- Empty price / empty metadata cards cannot be clicked.
- Partial title fragments cannot advance reference history.
- Invalid partial reference records are excluded from effective continuation.
- Reliable S10 gate remains required before selecting any reference card.
- Existing S11/S12/S13/S14 page collection logic was not changed.
