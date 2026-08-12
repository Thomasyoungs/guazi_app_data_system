# Geely Yuanjing 2019 V1.24 full-chain run

Final state: `RUN_FULL_CHAIN_GEELY_YUANJING_2019_AFTER_V1_24_DETERMINISTIC_SEQUENCE_DONE`  
Second-stage status: `FULL_CHAIN_PRICED_DONE`  
Target: `吉利|远景|2019款|升级版 1.5L 手动豪华型 国VI|白|2019.10`

## Key Pricing Result
- target_score: 96.0
- final_reference_index: 4
- final_reference_price_yuan: 23300
- final_reference_score: 97.0
- competition_coefficient: 0.98
- target_guazi_listing_price_yuan: 22800
- guazi_service_fee_yuan: 2500
- guazi_net_payout_yuan: 20300
- suggested_purchase_price_yuan: 16300

## Reference History
| index | price | status | score | note |
|---:|---:|---|---:|---|
| 1 | 2.04万 | COLLECTED_NOT_QUALIFIED | 94.0 |  |
| 2 | 2.09万 | EXCLUDED_OFFICIAL_REPORT_NOT_AVAILABLE |  | OFFICIAL_REPORT_NOT_AVAILABLE |
| 3 | 2.20万 | COLLECTED_NOT_QUALIFIED | 80.0 |  |
| 4 | 2.33万 | FINAL_REFERENCE | 97.0 |  |


## Pricing Rule Checks
- Service fee ladder: 2500.
- V1.2.1 competition coefficient: 0.98 (sample_reliability_adjustment -0.010: sample_slightly_small; price_distribution_adjustment -0.010: price_distribution_discrete).
- Non-trisame prices used: False.
- AI model used: False.
- Old 95 percent payout rule used: false.
- OBD unreadable: note only (`OBD_NOT_READ_NOTE`), no coefficient deduction and no manual-review trigger.

## Evidence Files
- `output/result_s01_to_s10.json`
- `output/result_s10_to_s16.json`
- `output/result.json`
- `output/geely_yuanjing_v1_24_deterministic_sequence_old_stop_patch.json`
