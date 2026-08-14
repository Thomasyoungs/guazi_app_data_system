# S10 Selected Card Autoscroll Incomplete After Scroll Diagnosis

- target: 本田|雅阁|2023款|260TURBO 智享版|黑|2024.01
- issue_code: REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL
- current_reference_index: 7
- conclusion: S10_SELECTED_CARD_AUTOSCROLL_ATTEMPTS_OR_DISTANCE_INSUFFICIENT

## Key Finding

reference #7 did not actually execute autoscroll. The selected candidate was a bottom partial card with title and metadata visible but missing price evidence; `s10_card_completion_scroll_attempts=[]`.

The safety block was correct because the card was incomplete and must not be clicked. The strategy gap is that `REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL` is recoverable at the outer handoff layer, but the inner completion-scroll loop returns immediately for that stop_code.

## Bounds And Evidence

- reference_index: 7
- clicked_bounds: [429, 2242, 949, 2366]
- card_bounds: [429, 2242, 1082, 2424]
- missing_price: True
- missing_metadata: False
- incomplete_reason: ['missing_price']
- reason: bottom_partial_card
- screen_size: (1220, 2712)
- screenshot_path: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s11_report_missing_return_s10_1_20260516_171604.png
- xml_path: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s11_report_missing_return_s10_1_20260516_171604.xml

## Autoscroll

- attempts: 0
- direction / start / end / distance: N/A, no swipe executed
- page_changed_after_autoscroll: N/A
- selected_card_moved_up: false

## Code Path

- outer_handoff_function: _s10_handoff_autoscroll_selected_card_if_needed
- outer_allows_stop_codes: ['SELECTED_REFERENCE_CARD_NOT_FULLY_VISIBLE', 'REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL']
- inner_scroll_function: _select_s10_reference_card_with_completion_scroll
- inner_scrollable_stop_codes: ['S10_REFERENCE_CARD_PARTIAL_VISIBLE', 'SELECTED_REFERENCE_CARD_NOT_FULLY_VISIBLE']
- actual_stop_code: REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL
- effect: actual_stop_code is not in inner_scrollable_stop_codes, so selected_card is returned before any swipe; scroll_attempts stays empty
- relevant_lines: {'partial_candidate_return': 'scripts/runtime_s10_to_s16_mainline.py:2633-2667', 'inner_non_scrollable_return_condition': 'scripts/runtime_s10_to_s16_mainline.py:2919-2921', 'outer_autoscroll_invocation': 'scripts/runtime_s10_to_s16_mainline.py:4053-4084'}

## Judgment

This is not the V1.37 local title binding failure and not the previous global duplicate-title blocker. It is a selected-card completion-scroll recovery gap. The correct next patch would add controlled recovery scrolls for bottom partial `REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL`, while preserving the hard rule that half-visible cards are never clicked.

## Final Status

S10_SELECTED_CARD_AUTOSCROLL_ATTEMPTS_OR_DISTANCE_INSUFFICIENT
