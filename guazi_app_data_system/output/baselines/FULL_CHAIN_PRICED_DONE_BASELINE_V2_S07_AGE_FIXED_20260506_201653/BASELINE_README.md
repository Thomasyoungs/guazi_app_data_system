# FULL_CHAIN_PRICED_DONE_BASELINE_V2_S07_AGE_FIXED

## ??

- baseline_type: functional_stability_baseline
- replaces_v1: false
- performance_best: false
- previous_baseline_preserved: FULL_CHAIN_PRICED_DONE_BASELINE_V1

???? S07 ?????????????????????????V1 ?????????????????

## ????

- ??? status: S10_READY
- ??? status: FULL_CHAIN_PRICED_DONE
- S07_FILTER_DONE: True
- COLOR_FILTER_DONE: True
- AGE_FILTER_DONE: True
- SORT_DONE: True
- S10_READY: True
- S14_COLLECT_DONE: True

## S07 ???????

- age_tick_labels_detected: ["0","2","4","6","8","10","??"]
- age_tick_unlimited_detected: true
- selected_right_handle_bounds: [1053,1248,1176,1387]
- right_age_after_confirm: 2
- exact_age_text: 2-2?
- AGE_FILTER_DONE: True

## ????

- ????: 12.87?
- ????: 91.5
- ????: 88.0
- ?????: 128700
- ?????: 109984

## ??????

- 目标车缺少出险次数，已采用默认分。
- 目标车缺少最大金额，已采用默认分。
- 三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。

## ????

V2 ???????????????? V1?V1 ??????????

next_recommended_branch = PATCH_ONLY_PERFORMANCE_OVER_2S_BASED_ON_V2

## S14 Return Slow Diagnosis

- archived_note: `S14_RETURN_SLOW_DIAGNOSIS_NOTE.md`
- final_status: `S14_RETURN_SLOW_DIAGNOSIS_ARCHIVED`
- conclusion: `S14_RETURN_TO_S10` is an aggregate timing. The return path is already minimal and contract-safe: first back closes the S14 inner layer, second back reaches reliable S10, and the runtime stops immediately after S10 is recognized.
- decision: keep current S14 return logic; do not patch S14_RETURN_TO_S10 unless the page contract later explicitly allows a semantic close node.
