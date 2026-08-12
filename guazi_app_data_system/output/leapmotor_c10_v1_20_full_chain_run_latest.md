# Leapmotor C10 V1.20 Full Chain Run

## Overall Result

- Final task state: `RUN_FULL_CHAIN_LEAPMOTOR_C10_AFTER_V1_20_DETERMINISTIC_CONTRACT_DONE`
- Terminal status: `RUN_FAILED_WITH_ISSUE`
- Stop code: `REFERENCE_CARD_TITLE_MISMATCH`
- Target fingerprint: `零跑|C10|2026款|210悦享版|白|2026.02`
- Baseline overwritten: `false`
- Raw XML / nodes / visible_blob in result JSON: `False`

## V1.20 Contract Alignment Check

- Alignment status: `passed`
- First-stage action table and contract execute entrypoints: `present`
- Second-stage action table and contract execute entrypoints: `present`
- Recognizer pattern: `source_gate_passed AND core_elements_count >= 3 AND reverse_exclusion_passed`
- Executable legacy path detected: `false`

## First Stage

- Status: `S10_READY`
- S10_READY: `True`
- S03 contract version: `V1.16`
- S05 selected: `2026款` + `210悦享版` / `已选1项`
- S06 variant: `S06_TARGET_FILTER_LIST_AFTER_S05_CONFIRM`
- COLOR_FILTER_DONE: `True`
- AGE_FILTER_DONE: `True`
- S07_FILTER_DONE: `True`
- Bottom view result: `查看2辆`
- S08 source gate: `True`
- Price ascending clicked: `True`
- S10 source gate: `True`
- S10 reason: `source_gate_core_elements_target_trisame_boundary_passed`

## True Trisame Source Pool

- Raw visible cards: `22`
- True trisame count: `2`
- Excluded non-trisame cards: `20`
- Non-trisame boundary: `True` / `找不到想要的车`

1. 零跑汽车 零跑C10 2026款 210悦享版 | 2026年 | 0.17万公里 | 唐山 | LeapPilot | 10.64?
2. 零跑汽车 零跑C10 2026款 210悦享版 | 2026年 | 0.77万公里 | 唐山 | LeapPilot | 10.86?

## Second Stage

- Started: `true`
- Status: `REFERENCE_CARD_TITLE_MISMATCH`
- Current reference_index: `1`
- Reference history count: `0`
- Entered S11: `false`
- Entered S16: `false`
- S17 payload output: `false`

### Stop Detail

- Expected title: `零跑 C10 2026款 210悦享版`
- Actual title: `零跑汽车 零跑C10 2026款 210悦享版`
- Reason: `Target indexed card title does not match target vehicle title.`
- Selected card complete: `True`
- Selected card price: `10.64万`
- Selected card metadata: `2026年 | 0.17万公里 | 唐山 | LeapPilot`
- Canonical reference index: `1`
- S10 order rule: `price_asc_mileage_desc_for_same_price`

This is a deterministic pre-click contract stop. The selected card is complete and before the non-trisame boundary, but the strict second-stage title gate expected `?? C10 2026? 210???` while the live title was `???? ??C10 2026? 210???`.

## Pricing

Pricing was not produced because S16 was not reached. These fields remain null:

- target_score
- final_reference_index
- final_reference_price_yuan
- final_reference_score
- competition_coefficient
- target_guazi_listing_price_yuan
- guazi_service_fee_yuan
- guazi_net_payout_yuan
- suggested_purchase_price_yuan
- S17 payload

## Evidence

- First-stage log: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\output\leapmotor_c10_v1_20_first_stage_run.log`
- Second-stage log: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\output\leapmotor_c10_v1_20_second_stage_run.log`
- Second-stage screenshot: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s10_s16_start_20260512_131420.png`
- Second-stage XML: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s10_s16_start_20260512_131420.xml`

## Conclusion

The V1.20 full-chain run reached `S10_READY` in the first stage and launched the second stage. The run stopped at `REFERENCE_CARD_TITLE_MISMATCH` before entering S11. No reference vehicle was collected, and no S16/S17 pricing output was produced.
