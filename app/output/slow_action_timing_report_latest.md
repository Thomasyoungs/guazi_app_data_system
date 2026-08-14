# Slow Action Timing Report

- threshold: 2.0s
- slow_action_count: 70
- source_timing_files: output\slow_action_first_stage_timing_20260512_183238.jsonl, output\slow_action_second_stage_timing_20260512_183442.jsonl

## Top 10 Slowest
| # | stage | page | action | duration_s | interval_s | reason | evidence |
|---:|---|---|---|---:|---:|---|---|
| 1 | second_stage | S14 | collect_image_sequence_until_terminal | 56.88 | 0.001 | S14_END_CONFIRM_TOO_CONSERVATIVE | C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s14_image_swipe_20260512_183731.xml |
| 2 | first_stage | S07 | tap_color_tap_target_color_confirm_then_tap_age_confirm_exact_then_tap_view | 16.824 | 5.325 | S07_AGE_SLIDER | artifacts/debug/s07_to_s08_20260512_183414.xml |
| 3 | second_stage | S10 | tap_title_text_node_bounds | 12.476 | 0.0 | PAGE_LOAD | C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s10_to_s11_20260512_183449_compressed.xml |
| 4 | second_stage | S10 | wait_s11_contract_stable | 12.376 | 0.0 | S10_TO_S11_PAGE_LOAD_SLOW | C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s10_to_s11_20260512_183449_compressed.xml |
| 5 | second_stage | S11 | tap_full_report | 11.235 | 0.001 | PAGE_TRANSITION_VERIFY | C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s11_to_s12_wait_stable_2_20260512_183613.xml |
| 6 | second_stage | S10 | fresh_recognize_wait_round | 11.196 | 0.0 | S10_TO_S11_WEBVIEW_TEXT_DELAY | C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s10_to_s11_20260512_183449_compressed.xml |
| 7 | second_stage | S11 | scroll_then_report_node_search | 11.022 | 0.0 | WEBVIEW_TEXT_DELAY | C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s11_report_entry_search_2_20260512_183602.xml |
| 8 | second_stage | S12 | S12_CLICK_BODY_APPEARANCE_TAB | 10.341 | 0.0 | S12_BODY_APPEARANCE_TAB_CLICK | C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s12_to_s13_20260512_183624.xml |
| 9 | second_stage | S10 | dump_xml_during_s11_wait | 10.293 | 0.0 | S10_TO_S11_FULL_XML_CAN_BE_COMPRESSED | C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s10_to_s11_20260512_183449_compressed.xml |
| 10 | second_stage | S10 | dump_compressed_xml_during_s11_wait | 10.293 | 0.0 | S10_TO_S11_FULL_XML_CAN_BE_COMPRESSED | C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s10_to_s11_20260512_183449_compressed.xml |

## By Page
| page | count | total_s | max_s |
|---|---:|---:|---:|
| RUNTIME | 6 | 13.698 | 6.758 |
| S01 | 1 | 0.999 | 0.999 |
| S02 | 1 | 0.898 | 0.898 |
| S03 | 2 | 5.713 | 5.64 |
| S04 | 1 | 0.873 | 0.873 |
| S05 | 1 | 7.416 | 7.416 |
| S05_MODEL_YEAR_SELECTED | 1 | 0.4 | 0.4 |
| S06 | 3 | 13.253 | 6.954 |
| S07 | 5 | 29.669 | 16.824 |
| S08 | 1 | 0.878 | 0.878 |
| S09 | 1 | 1.094 | 1.094 |
| S10 | 12 | 80.731 | 12.476 |
| S11 | 15 | 79.229 | 11.235 |
| S12 | 3 | 19.822 | 10.341 |
| S13 | 3 | 15.844 | 10.266 |
| S14 | 14 | 128.989 | 56.88 |

## By Reason
| reason | count | total_s | max_s |
|---|---:|---:|---:|
| PAGE_LOAD | 2 | 17.723 | 12.476 |
| PAGE_RECOGNITION_SLOW | 1 | 0.0 | 0.0 |
| PAGE_TRANSITION_VERIFY | 1 | 11.235 | 11.235 |
| REPORT_PAGE_STABLE_WAIT | 2 | 11.084 | 5.878 |
| S06_TO_S07_SCREENSHOT | 1 | 0.587 | 0.587 |
| S06_TO_S07_WEBVIEW_TEXT_DELAY | 1 | 6.954 | 6.954 |
| S07_AGE_PANEL_WAIT_SLOW | 1 | 6.464 | 6.464 |
| S07_AGE_SLIDER | 1 | 16.824 | 16.824 |
| S07_AGE_SLIDER_MOVE_SLOW | 1 | 0.523 | 0.523 |
| S07_COLOR_SELECTED_CONFIRM_SLOW | 1 | 5.858 | 5.858 |
| S07_WEBVIEW_TEXT_DELAY | 1 | 0.0 | 0.0 |
| S10_TO_S11_FULL_XML_CAN_BE_COMPRESSED | 4 | 25.806 | 10.293 |
| S10_TO_S11_PAGE_LOAD_SLOW | 2 | 17.492 | 12.376 |
| S10_TO_S11_PRE_DUMP_STABILIZE | 2 | 4.52 | 2.464 |
| S10_TO_S11_WEBVIEW_TEXT_DELAY | 2 | 15.19 | 11.196 |
| S11_REPORT_SEARCH_SCROLL | 1 | 0.541 | 0.541 |
| S12_BODY_APPEARANCE_TAB_CLICK | 1 | 10.341 | 10.341 |
| S14_END_CONFIRM_TOO_CONSERVATIVE | 1 | 56.88 | 56.88 |
| S14_IMAGE_HORIZONTAL_SWIPE | 10 | 56.825 | 7.062 |
| S14_RETURN_PATH_OK_NEEDS_NO_FIX | 1 | 8.558 | 8.558 |
| S14_RETURN_TO_S10 | 2 | 6.726 | 3.489 |
| UNKNOWN | 10 | 33.715 | 10.266 |
| WEBVIEW_TEXT_DELAY | 2 | 16.248 | 11.022 |
| XML_DUMP | 7 | 14.098 | 6.758 |
| XML_DUMP_SLOW | 12 | 55.314 | 5.863 |

## Notes
- ????????????? fresh/XML dump???????????S10/S11/S13/S14 ?????
- ??????S11/S14 ??? XML dump?S10->S11 ???????
