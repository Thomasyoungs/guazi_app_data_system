# S13 repair item clickable container binding patch

## Status
S13_REPAIR_ITEM_CLICKABLE_CONTAINER_BINDING_PATCHED

## Scope
Modified only `scripts/runtime_s10_to_s16_mainline.py`. No changes to S10/S11/S12/S14 business flow, pricing, config, baseline, scoring, or page-contract documents.

## Change
S13 repair detail entry enumeration now treats S13 entries as gateways into S14, not as final S14 scored parts. It enumerates clickable/enabled containers inside the count>0 history-repair entry zone and no longer filters S13 entry candidates by `S14_ALLOWED_PARTS`.

The patch rejects candidates from the lower normal detection-pass list, such as `???????10` / `????`, when the active history-repair entry zone contains valid clickable containers like `????` and `????`.

## Verification
- py_compile: passed
- module import: passed
- Offline A-H: passed
- Real evidence replay selected `????` at bounds `[81,1608,611,1716]` instead of lower-list `????`.

## Important invariant
`S14_ALLOWED_PARTS` was not extended with `??`; it remains scoped to S14 collection/scoring filtering.
