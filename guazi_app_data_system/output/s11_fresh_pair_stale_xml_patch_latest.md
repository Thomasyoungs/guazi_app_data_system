# S11 fresh pair / stale XML patch

Final status: `S11_FRESH_PAIR_STALE_XML_PATCHED`

## Modified files
- `scripts/runtime_s10_to_s16_mainline.py`

## What changed
- Added S11 fresh evidence pair validation before report-entry decisions.
- Added S10-stale XML detection for S11_REPORT_SEARCH and exactly one redump attempt.
- Invalid, empty, stale, or mtime-invalid XML is no longer used to decide that ?????? is absent.
- Disabled V1.38 XML stabilization wait/redump/micro-scroll mainline and fine/backtrack weak-marker search.
- Kept exact XML text plus full-visible, safe-click, bottom-bar gate and unsafe reposition.

## Disabled mainline paths
- S11 normal/fine/backtrack weak-marker switching
- V1.38 XML stabilization wait/redump/micro-scroll loop
- OCR / visual binding / screenshot coordinate click paths

## Retained paths
- S11 first one-third scroll when no safe exact entry exists
- Fixed small scroll after first scroll
- Exact XML ?????? only
- Visible-but-unsafe reposition
- S10 selected card autoscroll/local title binding

## Validation
- `py_compile`: passed
- A_fresh_pair_exact_entry_safe_click: passed
- B_fresh_pair_exact_entry_unsafe_reposition: passed
- C_stale_s10_xml_detected_not_used_as_absence: passed
- D_stale_xml_redump_once_recovers: passed
- E_stale_xml_redump_still_stale_stops: passed
- F_xml_dump_failure_no_old_xml_reuse: passed
- G_v1_38_mainline_disabled: passed
- H_unrelated_paths_static_guard: passed

No real-device run was performed in this PATCH_ONLY round.
