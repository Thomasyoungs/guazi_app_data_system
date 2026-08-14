# S11 Half Screen Scroll View Full Report Patch

- generated_at: 2026-05-13T17:32:04
- target_fingerprint: ??|??|2020?|2.5L XL Upper 4WD ???????|?|2021.08

## ????
- scripts/runtime_s10_to_s16_mainline.py
- ? S11 ?????? / ???????

## ????
- ?? XML/text ???????????
- ????????? 1/2?
- ???????????
- ????????????????????
- OCR / ?? / ???? / ???????????

## ????
- A_first_screen_has_view_full_report: {'passed': True, 'decision': 'click_view_full_report', 'scroll_attempts': 0, 'stop_scroll_reason': 'VIEW_FULL_REPORT_FOUND', 'clicked_forbidden': False}
- B_after_one_half_scroll_has_view_full_report: {'passed': True, 'decision': 'click_view_full_report', 'scroll_attempts': 1, 'stop_scroll_reason': 'VIEW_FULL_REPORT_FOUND', 'clicked_forbidden': False}
- C_after_two_half_scrolls_has_view_full_report: {'passed': True, 'decision': 'click_view_full_report', 'scroll_attempts': 2, 'stop_scroll_reason': 'VIEW_FULL_REPORT_FOUND', 'clicked_forbidden': False}
- D_merchant_self_check_marker_excludes: {'passed': True, 'decision': 'exclude_reference', 'scroll_attempts': 1, 'excluded_reference_reason': 'OFFICIAL_REPORT_NOT_AVAILABLE', 'clicked_forbidden': False}
- E_no_decisive_marker_exhausted: {'passed': True, 'decision': 'stop', 'scroll_attempts': 2, 'stop_code': 'S11_REPORT_ENTRY_SEARCH_EXHAUSTED_WITHOUT_DECISIVE_MARKER', 'clicked_forbidden': False}
- F_advisor_only_not_report: {'passed': True, 'decision': 'stop', 'scroll_attempts': 1, 'stop_code': 'S11_REPORT_ENTRY_SEARCH_EXHAUSTED_WITHOUT_DECISIVE_MARKER', 'clicked_forbidden': False}

## py_compile
- passed

## ????
- attempted: False
- blocked_by: CURRENT_TARGET_TASK_JSON_INVALID
- error: 

## ????
S11_HALF_SCREEN_SCROLL_VIEW_FULL_REPORT_PATCHED
