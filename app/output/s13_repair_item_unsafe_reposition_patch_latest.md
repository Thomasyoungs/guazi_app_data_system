# S13 Repair Item Unsafe Reposition Patch

## Status

`S13_REPAIR_ITEM_UNSAFE_REPOSITION_PATCHED`

## Scope

Modified only:

- `scripts/runtime_s10_to_s16_mainline.py`

No changes were made to the first-stage script, S10/S11/S12/S14 logic, pricing, config, metal_deduct, baseline, scoring rules, or pricing rules.

## Implemented Contract

When S13 has already reached the history repair table, a region has `history_repair_count > 0`, and repair item candidates are visible but not safe to click, the script now:

1. Refuses to click the unsafe candidate.
2. Performs a bounded small upward reposition inside S13.
3. Fresh-captures screenshot and XML after each reposition.
4. Reconfirms S13, the history repair table, the same region count, and the repair item click target.
5. Clicks only after the target is complete, safe, and bound.

The reposition is limited to 2 attempts.

## Failure States

- `S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED`
- `S13_REPAIR_DETAIL_REPOSITION_LOST_CONTEXT`

## Validation

- `py_compile`: passed
- module import: passed
- offline A-H: passed

Offline report:

- `output/s13_repair_item_unsafe_reposition_offline_validation.json`
