# S10 Selected Card Local Title Binding Patch

## Status

S10_SELECTED_CARD_LOCAL_TITLE_BINDING_PATCHED

## Scope

Modified only `scripts/runtime_s10_to_s16_mainline.py`.

No changes were made to first-stage script, S11, S14, pricing, config, metal_deduct, baseline, scoring rules, or pricing rules.

## What Changed

The S10 reference-card click binding no longer requires title text to be globally unique across the whole page. The selected reference card, after canonical order / autoscroll / fresh parsing gates, is now the click authority.

The new binding flow checks:

- selected card is fully visible, complete, clickable, and safe
- title / price / metadata bind to the same local selected card container
- global duplicate title count is recorded as evidence only
- visible local index may differ from business reference_index after autoscroll
- partial bottom cards remain excluded from selected card binding

## New Evidence Fields

- `s10_title_binding_scope`
- `s10_global_title_duplicate_count`
- `s10_local_title_node_count`
- `s10_selected_card_local_index`
- `s10_business_reference_index`
- `s10_reference_index_rebased_after_autoscroll`
- `s10_selected_card_bounds`
- `s10_selected_card_click_target_bounds`
- `s10_selected_card_binding_decision`

## Validation

`python -m py_compile scripts/runtime_s10_to_s16_mainline.py` passed.

Offline scenarios A-G all passed:

| Scenario | Result |
|---|---|
| Multiple same-title cards, selected container unique | PASS |
| business reference #7 maps to visible local index 5 | PASS |
| title / price / metadata bind to same selected card | PASS |
| local title not unique | PASS |
| title / price / metadata cannot bind together | PASS |
| selected card partial / unsafe | PASS |
| bottom other partial card ignored | PASS |

## Next Step

Run Honda Accord AUTO_PRICING_SUCCESS_PATH real-device validation.
