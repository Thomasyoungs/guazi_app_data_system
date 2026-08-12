# S15 metal_deduct Special Structure Patch

## Status

S15_METAL_DEDUCT_SPECIAL_STRUCTURE_PATCHED

## Modified Files

- config/fields.yaml
- src/guazi_app_data_system/pricing.py

## What Changed

- Added scoring.metal_deduct with the same structure and values as the existing paint_deduct map.
- Replaced the naked scoring["metal_deduct"] access in the special-structure metal branch with explicit config validation.
- No S10/S11/S14 runtime flow, pricing formula, competitiveness coefficient, service fee, baseline, or scoring numeric rule was changed.

## Rule Value

The existing code already routes ABC柱/A柱/B柱/C柱 + 钣金 through the front-cover equivalent branch with default 3.0. The added metal_deduct map preserves that same口径 and does not introduce a new扣分强度.

## Offline Validation

- reference #2 ordinary panel/bumper metal path: score remains 83.0.
- reference #3 ABC柱 paint path: score remains 76.0.
- reference #5 ABC柱 metal path: no KeyError; score components generated; offline score 74.5.
- Missing metal_deduct config now raises clear ValueError: scoring.metal_deduct is required for special-structure damage scoring instead of naked KeyError.

## py_compile

- src/guazi_app_data_system/pricing.py: passed
- scripts/runtime_s10_to_s16_mainline.py: passed
