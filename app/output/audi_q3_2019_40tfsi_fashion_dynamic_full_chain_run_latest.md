# Audi Q3 2019 Full Chain Run

## Overall
- Task: `RUN_FULL_CHAIN_AUDI_Q3_2019_40TFSI_FASHION_DYNAMIC_AFTER_SYSTEM_LOCK`
- Fingerprint: `??|Q3|2019?|40 TFSI ?????|?|2020.05`
- First stage: `S10_READY`
- Second stage: `FULL_CHAIN_MANUAL_REVIEW_DONE`
- Final outcome: `FULL_CHAIN_MANUAL_REVIEW_DONE`
- Final state: `RUN_FULL_CHAIN_AUDI_Q3_2019_40TFSI_FASHION_DYNAMIC_AFTER_SYSTEM_LOCK_DONE`

## First Stage
- S10_READY: `True`
- COLOR_FILTER_DONE: `True`
- AGE_FILTER_DONE: `True`
- S07_FILTER_DONE: `True`
- SORT_DONE: `True`
- Complete target vehicle cards: `1`
- S10 reason: `source_gate_core_elements_target_trisame_boundary_passed`

## Reference And Pricing
- Target score: `92.0`
- Final reference index: `1`
- Final reference title: `奥迪Q3 2019款 40 TFSI 时尚动感型`
- Final reference price: `112400` yuan
- Final reference score: `92.0`
- Competition coefficient: `0.97`
- Target Guazi listing price: `109000` yuan
- Guazi service fee: `4500` yuan
- Guazi net payout: `104500` yuan
- Suggested purchase price: `93640` yuan

## Manual Review
- manual_review_required: `True`
- reasons:
  - 目标车缺少出险次数，已采用默认分。
  - 目标车缺少最大金额，已采用默认分。
  - 三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。
  - SAMPLE_SHORTAGE_MANUAL_REVIEW

## Reference History
- #1 `奥迪Q3 2019款 40 TFSI 时尚动感型` price=112400 score=92.0 title_match=True official_report=True

## Quality Checks
- Result JSON valid: `true`
- Fingerprint all match: `False`
- raw XML / nodes / visible_blob / page_source large fields present: `false`
- Old target pollution detected: `false`
- Used non-trisame price: `false`
- Used old 95% payout rule: `false`
- Baseline overwritten: `false`

## Evidence
- First stage log: `logs/audi_q3_2019_first_stage_20260513_102214.log`
- Second stage log: `logs/audi_q3_2019_second_stage_20260513_102524.log`
- Result files: `output/result_s01_to_s10.json`, `output/result_s10_to_s16.json`, `output/result.json`
