# Honda Accord Auto Pricing Runtime After Reliable S10 Restore

Status: `AUTO_PRICING_SUCCESS_PATH_FELL_TO_MANUAL_REVIEW`

## Execution Summary

- First stage restored live S10: PASS
- Second stage started from reliable S10: PASS
- S11/S12/S13/S14/S15 reached: PASS
- S16 reached: `S16_READY`
- Auto pricing: NOT allowed under V3 boundary rule
- Final raw status: `FULL_CHAIN_MANUAL_REVIEW_DONE`

## Rule Source Guard

- active_scoring_rule_version: `V1.8`
- active_reference_selection_rule: `V3_BOUNDARY_CONFIRMATION`
- active_pricing_rule_version: `SERVICE_FEE_TIER_V3_BOUNDARY_CONFIRMATION`
- active_competition_coefficient_version: `V1.2.3`
- rule_source_guard_passed: `True`

## V1.8 Score Recalculation

Target score: `90.5`

Target components:

```json
{
  "body_score": 70.0,
  "mileage_score": 6.5,
  "transfer_score": 7.0,
  "accident_score": 4.0,
  "max_amount_score": 3.0
}
```

Target review reasons:

```json
[
  "目标车缺少出险次数，已采用默认分。",
  "目标车缺少最大金额，已采用默认分。"
]
```

Reference #1 score: `94.5`

Reference #1 components:

```json
{
  "body_score": 70.0,
  "mileage_score": 6.5,
  "transfer_score": 7.0,
  "accident_score": 6.0,
  "max_amount_score": 5.0
}
```

## V3 Boundary Confirmation

- reference_selection_rule: `V3_BOUNDARY_CONFIRMATION`
- boundary_confirmed: `True`
- boundary_reference_index: `1`
- boundary_reference_score: `94.5`
- pre_boundary_reference_index: `None`
- final_reference_index: `None`
- final_reference_score: `None`

V3 result: the first valid reference scored above the target score (`94.5 > 90.5`). Under V3 this triggers manual review with `FIRST_REFERENCE_SCORE_ABOVE_TARGET`; it must not auto-price from that reference.

## Pricing Output

- pricing.status: `manual_review`
- pricing.reason: `无有效参考车`
- final_price generated: `false`
- pricing / Feishu payload generated: `True`
- manual_review_reasons: `["目标车缺少出险次数，已采用默认分。", "目标车缺少最大金额，已采用默认分。", "FIRST_REFERENCE_SCORE_ABOVE_TARGET"]`

## Regression Notes

S10 handoff, S11 transfer count/report entry, S12 claim fields/body appearance, S13 history table/entry, S14 collection, and S15 scoring all reached without the prior blocking failures. The run fell to manual review because of the V3 reference selection rule, not because of page-flow regression.
