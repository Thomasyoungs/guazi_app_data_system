# Leapmotor C10 Full Chain Manual Review Acceptance

## Conclusion
- Acceptance status: `LEAPMOTOR_C10_FULL_CHAIN_MANUAL_REVIEW_ACCEPTANCE_PASSED`
- Freeze package status: `READ_ONLY_LEAPMOTOR_C10_BASELINE_FREEZE_PACKAGE_DONE`
- Baseline name: `BASELINE_LEAPMOTOR_C10_2026_FULL_CHAIN_MANUAL_REVIEW_DONE_202605`
- Target fingerprint: `零跑|C10|2026款|210悦享版|白|2026.02`
- Baseline type: `FULL_CHAIN_MANUAL_REVIEW_DONE`, not `AUTO_PRICED_NO_REVIEW_DONE`

## Result Consistency
- First stage: `S10_READY` with `S10_READY=True`
- Second stage: `FULL_CHAIN_MANUAL_REVIEW_DONE` with `s16_status=S16_READY`
- Final reference index: `1`
- Final reference title: `零跑汽车 零跑C10 2026款 210悦享版`
- Final reference price: `106400`
- Final reference score: `99`
- Target score: `96`
- Score gate: `reference_score >= target_score` => `True`
- True trisame count: `2`
- Excluded non-trisame cards: `20`
- Non-trisame / more-source / recommended-source used: `False`

## Page Contract Acceptance
- S03 V1.16 brand page contract: `PASS`
- S04 C10 / 零跑C10 alias recognition: `PASS`
- S05 2026款 + 210悦享版 selection: `PASS`
- S06_TARGET_FILTER_LIST_AFTER_S05_CONFIRM: `PASS`
- S07 白色 + 0年车龄: `PASS`
- S07 forbidden checked-only gate: `PASS`
- S08/S09/S10 source gate + core elements + reverse exclusion: `PASS`
- S10 V1.21 normalized title match: `PASS`
- Second stage S10→S11→S12→S13→S15→S16/S17: `PASS`

## Pricing Acceptance
- competition_coefficient: `0.96`
- base_reference_price_yuan: `106400`
- target_guazi_listing_price_yuan: `102100`
- guazi_service_fee_yuan: `4500`
- service fee tier: `>=100000 and <150000 => 4500`
- guazi_net_payout_yuan: `97600`
- net formula: `102100 - 4500 = 97600`
- suggested_purchase_price_yuan: `87292`
- V1.2.1 competition coefficient: `PASS`
- old ×95% payout rule used: `False`

## Manual Review Scope

Manual review is required because:

1. 真实三同样本数为 2，触发样本不足复核。
2. 目标车缺少出险次数 / 最大金额，按默认分并提示人工复核。

## JSON And Raw Field Check
- result_s01_to_s10.json valid: `True`
- result_s10_to_s16.json valid: `True`
- result.json valid: `True`
- raw XML / nodes / visible_blob large fields present: `False`

## Final Status
`LEAPMOTOR_C10_FULL_CHAIN_MANUAL_REVIEW_ACCEPTANCE_PASSED`

`READ_ONLY_LEAPMOTOR_C10_BASELINE_FREEZE_PACKAGE_DONE`
