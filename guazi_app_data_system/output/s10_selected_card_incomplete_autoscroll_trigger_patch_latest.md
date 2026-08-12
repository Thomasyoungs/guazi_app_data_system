# S10 Selected Card Incomplete Autoscroll Trigger Patch

- status: S10_SELECTED_CARD_INCOMPLETE_AUTOSCROLL_TRIGGER_PATCHED
- modified_files: scripts/runtime_s10_to_s16_mainline.py
- py_compile: passed
- offline_validation: S10_SELECTED_CARD_INCOMPLETE_AUTOSCROLL_TRIGGER_OFFLINE_VALIDATED

## What Changed
- Added REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL to the recoverable selected_reference_card completion-scroll stop codes.
- Preserved hard click gates: price-missing, half-visible, unsafe, or non-unique cards are still never clicked.
- Added structured evidence fields for incomplete reason and price missing before/after autoscroll.
- Classified terminal failure after completion attempts as REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL for missing fields or SELECTED_REFERENCE_CARD_NOT_FULLY_VISIBLE for still partial/unsafe cards.

## Offline Validation
- A: PASS - REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL executes controlled autoscroll instead of direct return
- B: PASS - price appears after autoscroll and selected card becomes clickable
- C: PASS - still missing price blocks with REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL
- D: PASS - still partial/unsafe blocks with SELECTED_REFERENCE_CARD_NOT_FULLY_VISIBLE
- E: PASS - binding not unique after autoscroll blocks without click
- F: PASS - bottom partial card remains excluded from valid reference history/index advancement
- G: PASS - price-missing or half-visible card is blocked and not clicked

## Safety
- no_click_half_visible_card: True
- no_click_price_missing_card: True
- fresh_after_each_autoscroll: True
- reparse_reliable_s10_after_scroll: True
- boundary_check_preserved: True
- reference_identity_recheck_preserved: True

## Final Status

S10_SELECTED_CARD_INCOMPLETE_AUTOSCROLL_TRIGGER_PATCHED
