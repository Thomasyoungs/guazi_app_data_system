# Nissan Terra S14 Image Sequence Diagnosis

## Conclusion
- Final state: `NISSAN_TERRA_S14_IMAGE_SEQUENCE_DIAGNOSIS_DONE`
- Stop code: `S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED`
- Root cause: `S14_COLLECT_DONE_NOT_SET_AFTER_SEQUENCE_END`
- Secondary findings: `S14_CAPTION_EXTRACTION_MISSING, S14_IMAGE_SIGNATURE_DEDUP_TOO_STRICT`
- Stop correctness: `false`

## Trigger Background
- Reference index: `3`
- Reference title: `日产 途达 2020款 2.5L XL Upper 4WD 自动四驱豪华版`
- Reference price: `10.90万`
- S13 region: `车头`
- S13 repair count: `1`
- Clicked repair item: `前保险杠`
- S14 confirmed: `True`
- Expected images: `1`
- Collected image records: `40`
- S14_COLLECT_DONE: `False`

## S14 Evidence
- Unique page labels: `['前保险杠(1/1)']`
- Unique first lines: `['前保险杠—拆卸痕迹']`
- Horizontal swipes: `40`
- all_swipes_same_page_label: `True`
- all_swipes_same_first_line: `True`
- semantic_changed=true count: `40`
- image_sequence_end_confirmed count: `0`
- max no_semantic_change_count: `0`

## Why It Failed
The page label remained ????(1/1) and the first-line caption remained ????????? across 40 swipes. Because caption parsing produced normalized_part=null and normalized_damage=null, visited_s14_keys stayed empty; _s14_semantic_changed treated the same key as new on every swipe, reset no_semantic_change_count to 0, and the terminal condition never fired before the guard limit.

## Contract Assessment
- Correct S14 page: `true`
- Forbidden live/seller page: `false`
- Tab click evidence: `false`
- Only image-region swipes: `true`
- Actual sequence end reached: `true`
- Actual collection complete but `S14_COLLECT_DONE=false`: `true`

## Recommended Patch
`PATCH_S14_IMAGE_SEQUENCE_COMPLETION_GATE`

Recommended rules:
- Use page label total_pages, e.g. (1/1), as deterministic completion evidence when only one image is declared and the first caption has been read.
- Treat identical page_label + raw_first_line + stable visible caption as no new semantic content even if screenshot hash changes.
- Add keys to seen set using raw page_label/raw_first_line fallback when normalized_part or normalized_damage cannot be parsed.
- If caption extraction remains impossible, output S14_CAPTION_EXTRACTION_MISSING instead of S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED.
- Keep tab/live/seller/quote forbidden checks unchanged; continue to allow only image-region horizontal swipes.

## Evidence Paths
- Result: `output/result.json`
- Second-stage result: `output/result_s10_to_s16.json`
- Log: `logs/nissan_terra_2020_second_stage_continue_2_20260513_105004.log`
- First S14 XML: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s13_to_s14_车头_20260513_105217.xml`
- First S14 screenshot: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s13_to_s14_车头_20260513_105217.png`
- Last S14 XML: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s14_image_swipe_20260513_105544.xml`
- Last S14 screenshot: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s14_image_swipe_20260513_105544.png`

## Readonly Confirmation
No code changed. No real-device run. No second-stage rerun. No pricing rerun. No result overwrite.
