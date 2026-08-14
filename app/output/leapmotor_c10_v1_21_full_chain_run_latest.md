# Leapmotor C10 V1.21 Full Chain Run

## Overall Result

- Final task state: `RUN_FULL_CHAIN_LEAPMOTOR_C10_AFTER_V1_21_TITLE_NORMALIZED_CONTRACT_DONE`
- Terminal status: `FULL_CHAIN_MANUAL_REVIEW_DONE`
- Target fingerprint: `零跑|C10|2026款|210悦享版|白|2026.02`
- Pricing produced: `True`
- Manual review required: `True`
- Raw XML / nodes / visible_blob in result JSON: `False`

## V1.21 Patch

- Modified file: `scripts/runtime_s10_to_s16_mainline.py`
- Strategy: `normalized_alias_match`
- Positive live title accepted: `???? ??C10 2026? 210???`
- Negative cases rejected: C11, 2025?, 530???, ?? conflict
- py_compile: `passed`

## First Stage

- Status: `S10_READY`
- S10_READY: `True`
- COLOR_FILTER_DONE: `True`
- AGE_FILTER_DONE: `True`
- S07_FILTER_DONE: `True`
- Bottom view result: `查看2辆`
- True trisame count: `2`
- Excluded non-trisame cards: `20`

1. 零跑汽车 零跑C10 2026款 210悦享版 | 2026年 | 0.17万公里 | 唐山 | LeapPilot | 10.64?
2. 零跑汽车 零跑C10 2026款 210悦享版 | 2026年 | 0.77万公里 | 唐山 | LeapPilot | 10.86?

## Second Stage

- Status: `FULL_CHAIN_MANUAL_REVIEW_DONE`
- S16 status: `S16_READY`
- Final reference index: `1`
- Final reference price: `106400`
- Final reference score: `99.0`
- Target score: `96.0`
- Title normalized match: `True`
- Conflict terms: `[]`

## Reference History

- reference_index=1, price=10.64万, metadata=2026年 | 0.17万公里 | 唐山 | LeapPilot, transfer=0, claims=0, max_amount=0.0, repairs={'驾驶侧': 0, '车尾': 0, '副驾驶': 0, '车头': 0}, score=99.0, selected=True

## Pricing

- competition_coefficient: `0.96`
- target_guazi_listing_price_yuan: `102100`
- guazi_service_fee_yuan: `4500`
- guazi_net_payout_yuan: `97600`
- suggested_purchase_price_yuan: `87292`
- non_trisame_prices_used: `False`
- ai_model_used: `False`
- old_95_percent_rule_used: `false`

## Manual Review

Reasons:
- 目标车缺少出险次数，已采用默认分。
- 目标车缺少最大金额，已采用默认分。
- 三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。
- SAMPLE_SHORTAGE_MANUAL_REVIEW

## S17 Payload

- task_status: `manual_review`
- suggested_acquisition_price_yuan: `87292`
- suggested_listing_price_yuan: `102100`
- final_reference_index: `1`
- reference_score: `99.0`
- target_score: `96.0`

## Conclusion

V1.21 title normalized matching allowed the live title alias `???? ??C10 2026? 210???` deterministically. The chain reached S16/S17, produced pricing, and requires manual review due to sample shortage and target missing claim/max-amount inputs.
