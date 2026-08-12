# S11 Report Entry Visible But Unsafe Reposition Patch

## Status

S11_REPORT_ENTRY_VISIBLE_BUT_UNSAFE_REPOSITION_PATCHED

## Scope

Modified:

- `scripts/runtime_s10_to_s16_mainline.py`

Not modified:

- first-stage script
- S10 selected-card autoscroll logic
- S14 logic
- raw_first_line stale logic
- pricing, config, baseline, scoring rules
- page contract documents
- Nissan Terra closure package

## Contract Implemented

When exact XML text `查看完整报告` is found but the candidate is not safe to click, the script no longer stops immediately. It now enters a controlled S11 reposition branch:

- no click while unsafe
- small controlled reposition scroll
- fresh screenshot + XML after each reposition
- re-check exact entry visibility and safe click region
- click only after the exact entry is safe
- bounded reposition attempts
- still stops with `S11_REPORT_ENTRY_NOT_FULLY_VISIBLE_SAFE` if it cannot become safe

The existing S11 first 1/3 scroll, normal/fine/backtrack search, exact-text-only entry gate, and weak-marker behavior are preserved.

## Validation

- `python -m py_compile scripts/runtime_s10_to_s16_mainline.py`: passed
- offline validation A-E: passed

## Offline Cases

- A: exact `查看完整报告` found but too close to bottom -> reposition required
- B: exact entry fully visible and safe -> direct click allowed
- C: reposition still unsafe after max attempts -> `S11_REPORT_ENTRY_NOT_FULLY_VISIBLE_SAFE`
- D: weak marker only -> no click
- E: reposition bounded by max attempts

## Result

The V1.36 S11 visible-but-unsafe reposition contract is patched and ready for Honda Accord runtime validation.
