# Nissan Terra Reference #1 Score 78 / S15 Premature Entry Diagnosis

- status: NISSAN_TERRA_REFERENCE1_SCORE_78_S15_PREMATURE_ENTRY_DIAGNOSIS_DONE
- target: 日产|途达|2020款|2.5L XL Upper 4WD 自动四驱豪华版|白|2021.08
- reference_score: 78.0
- target_score: 92.0
- score_78_trustworthy: False
- reason: repair_details_incomplete_before_s15: S13 repair_count=10, but only 1 concrete repair item was collected into S14/panel_repairs before S15 scoring.

## Score Breakdown
- body_score: score=69.0, raw={'repair_counts': {'驾驶侧': 10}, 'panel_repairs': [{'part': '左前翼子板', 'damage_type': '喷漆'}]}, reason=????????????? panel_repairs ???? panel_repairs ????????????? 9 ??
- mileage_score: score=0.0, raw={'list_mileage_10k_km': 12.5}, reason=????? 12.5 ????????? 0?
- transfer_score: score=7.0, raw={'transfer_count': 1}, reason=?? 1 ???? 7 ??
- accident_score: score=0.0, raw={'accident_count': 4}, reason=??/???? 4????????? 0 ??
- max_amount_score: score=2.0, raw={'max_accident_amount': 19000.0}, reason=???? 19000????????? 2 ??

## S15 Entry Diagnosis
- classification: S15_ENTRY_ALLOWED_BY_SINGLE_S14_ITEM_DONE_ONLY
- s13_total_repair_count: 10
- s14_expected_repair_item_count: 10
- s14_collected_repair_item_count: 1
- s14_collected_items: ['左前翼子板']
- s14_missing_items_count: 9
- s15_entered_reason: handle_s15 only checked s14_collect_done + s14 tab/image metrics + repair_items presence; it did not compare collected item count with S13 repair_count.

## Correct Contract
- S13_S14_ALL_REPAIR_ITEMS_REQUIRED_BEFORE_S15
- S13 ??????????? > 0 ?????????????????
- S14_COLLECT_DONE ?????????????
- reference_collect_done ???? collected_repair_item_count >= expected_repair_item_count?
- ?????????????? S13_REPAIR_ITEMS_ENUMERATION_INCOMPLETE ? REFERENCE_REPAIR_DETAILS_INCOMPLETE_MANUAL_REVIEW?
- ?? repair_count=10 ???? 1 ?????? S15?
- ????? S14 ?????????????
- ???? required repair items ??????? S15 ???

## Next Patch
- PATCH_S13_S14_ALL_REPAIR_ITEMS_REQUIRED_BEFORE_S15
- S13 repair_count>0 ??? expected_repair_item_count?
- ????????? S14 ?????? collected_repair_item_count?
- S14_COLLECT_DONE ?????? item?
- collected_repair_item_count ??? expected_repair_item_count????? S15?
- ???????????????? stop_code ??????
- reference_score ??? incomplete repair details ????????

## Evidence Paths
- result_s10_to_s16: output/result_s10_to_s16.json
- result: output/result.json
- early_collect_done_diagnosis: output/nissan_terra_reference1_early_collect_done_diagnosis.json
- s14_first_screenshot: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s13_to_s14_驾驶侧_20260513_195243.png
- s14_first_xml: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s13_to_s14_驾驶侧_20260513_195243.xml
- s14_last_screenshot: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s14_image_swipe_20260513_195255.png
- s14_last_xml: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s14_image_swipe_20260513_195255.xml

???????????????????? result.json?
