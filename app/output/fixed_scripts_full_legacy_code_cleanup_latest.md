# ??????????????????

- ?????2026-05-11T18:41:20+08:00
- ?????`scripts/runtime_s01_to_s10_mainline.py`?`scripts/runtime_s10_to_s16_mainline.py`
- ?????`scripts/runtime_s01_to_s10_mainline.py`
- ??????????pricing?config?DOCX?result.json?baseline ??

## ????

- ??????????
- ??????????
- ????????????????????????????
- ?????`FIXED_SCRIPTS_LEGACY_CODE_CLEANUP_VERIFIED_CLEAN`

## ??? / ??????
- `scripts/runtime_s01_to_s10_mainline.py` / `handle_s07 -> previous _find_target_color_node direct branch`?converted_to_contract_path?Always click the left Color tab first, then bind target color inside the Color panel. Default-panel visible labels cannot be clicked as color.
- `scripts/runtime_s01_to_s10_mainline.py` / `_target_color_selected`?converted_to_strict_evidence?Color selected only by selected=true node or top selected filter chip evidence; raw visible text membership is no longer enough.
- `scripts/runtime_s01_to_s10_mainline.py` / `S07 forbidden filter guard`?converted_to_stop?Selected forbidden filter chips stop with S07_FORBIDDEN_FILTER_SELECTED_BEFORE_COLOR or S07_FORBIDDEN_FILTER_SELECTED_BY_COLOR_CLICK.
- `scripts/runtime_s01_to_s10_mainline.py` / `S05 target-year retry block`?converted_to_report_only_disabled_flag?No second click is executed; evidence records legacy_retry_year_click_disabled=true and existing S05 year evidence gate decides continue/stop.
- `scripts/runtime_s10_to_s16_mainline.py` / `all reviewed direct tap sites`?kept?No executable legacy branch found requiring code change in this pass; remaining fallback strings are evidence acquisition/screen bounds, not business continuation.

## ???????
- `runtime_s01_to_s10_mainline.py` / S00/S_LOGIN startup and login close helpers?`CONTRACT_VALID_ACTIVE`?kept?Only contract-defined app entry / skip / close actions; fresh after action.
- `runtime_s01_to_s10_mainline.py` / handle_s01 / handle_s02?`CONTRACT_VALID_ACTIVE`?kept?Only bottom ?? then ?? entry with page checks.
- `runtime_s01_to_s10_mainline.py` / S03 V1.16 helpers and handle_s03?`CONTRACT_VALID_ACTIVE + CONTRACT_STOP_DEFENSIVE`?kept?Target invisible -> target initial only; target visible -> left icon safe point only; brand zone stops.
- `runtime_s01_to_s10_mainline.py` / S04 standard series binding?`CONTRACT_VALID_ACTIVE + CONTRACT_STOP_DEFENSIVE`?kept?Standard S04 only; brand-zone mixed list stops; visible target series clicks same-row right ??.
- `runtime_s01_to_s10_mainline.py` / S05 year/trim/emission variant selection?`CONTRACT_VALID_ACTIVE`?kept_with_retry_disabled?Exact year, exact trim, emission variant group all-selected; old target-year retry click disabled.
- `runtime_s01_to_s10_mainline.py` / S06_TARGET_FILTER_LIST_AFTER_S05_CONFIRM recognizer and action?`CONTRACT_VALID_ACTIVE + CONTRACT_STOP_DEFENSIVE`?kept?Requires S05_CONFIRM_TO_S06 source gate, S05_DONE, target evidence, core elements, reverse exclusion; only action is ????.
- `runtime_s01_to_s10_mainline.py` / handle_s07 color/age/view result?`CONTRACT_VALID_ACTIVE + CONTRACT_STOP_DEFENSIVE`?cleaned?Color now requires left Color tab first; forbidden S07 filters stop; exact age and view result gates retained.
- `runtime_s01_to_s10_mainline.py` / handle_s08 / handle_s09 / handle_s10?`CONTRACT_VALID_ACTIVE`?kept?Requires S07 filters done, sort contract, S10_READY evidence and real trisame cards.
- `runtime_s10_to_s16_mainline.py` / S10 reliable gate and canonical_reference_order?`CONTRACT_VALID_ACTIVE + CONTRACT_STOP_DEFENSIVE`?kept?Live reliable S10, trisame boundary, target title filter, complete card gate, partial card stop.
- `runtime_s10_to_s16_mainline.py` / S11 and S11->S12?`CONTRACT_VALID_ACTIVE`?kept?S10_TO_S11 context, top image evidence, safe full-report click, stable wait, S12 priority.
- `runtime_s10_to_s16_mainline.py` / S12/S13 history repair parsing?`CONTRACT_VALID_ACTIVE + CONTRACT_STOP_DEFENSIVE`?kept?Raw XML nodes + bounds; duplicate 0 preserved; summary numbers excluded; uncertain count stops.
- `runtime_s10_to_s16_mainline.py` / S13->S14 repair item click guard?`CONTRACT_VALID_ACTIVE + CONTRACT_STOP_DEFENSIVE`?kept?Specific repair row only; live room/contact/bargain forbidden; post-click page verifies S14 or stops.
- `runtime_s10_to_s16_mainline.py` / S14 collect / return / S15 / S16?`CONTRACT_VALID_ACTIVE`?kept?Image-region swipe, S14 complete before return, reliable S10 return gate, score gate before S16.

## ???????
- `fallback`?exists=True?classification=`report_only / contract_valid`?action=kept
- `??`?exists=False?classification=`none`?action=none
- `??`?exists=False?classification=`none`?action=none
- `retry`?exists=True?classification=`report_only / contract_valid`?action=S05 executable retry removed
- `guess / ???? / old snapshot / current page continue`?exists=False?classification=`none`?action=none
- `tap_text`?exists=True?classification=`contract_valid`?action=kept
- `tap_bounds/tap_center/click_bounds/direct tap`?exists=True?classification=`contract_valid`?action=kept
- `click_checkbox / first checkbox / first_unchecked`?exists=False?classification=`none`?action=none
- `visible option / select_option`?exists=False?classification=`none`?action=none
- `????? / new_energy / ???`?exists=True?classification=`report_only / stop_defensive`?action=kept
- `letter_G / click G`?exists=True?classification=`report_only`?action=kept
- `scroll_brand / scroll_brand_list`?exists=False?classification=`none`?action=none
- `BRAND_ZONE_MIXED_LIST / ????`?exists=True?classification=`stop_defensive`?action=kept
- `target_series_verified in brand zone / C10 in brand zone / ????????`?exists=False?classification=`none`?action=none
- `??? / ???? / ???? / ???? / ???? / ????`?exists=True?classification=`stop_defensive`?action=kept
- `tap_target_color / _find_target_color_node`?exists=True?classification=`contract_valid`?action=kept_after_cleanup
- `default panel`?exists=True?classification=`report_only`?action=kept
- `non_trisame / ???? / ???? / more source`?exists=True?classification=`contract_valid / stop_defensive`?action=kept
- `partial card`?exists=True?classification=`stop_defensive`?action=kept
- `visible_texts dedup`?exists=False?classification=`none`?action=none
- `live room / ???? / ???? / ?? / ????`?exists=True?classification=`stop_defensive`?action=kept
- `tab click`?exists=True?classification=`report_only / stop_defensive`?action=kept

## ??????
- S03?V1.16 ??? + ??????????????????? continuation_allowed=false?
- S04???C10 ?????????????????????????
- S05?????V/?VI?????????????/??????????
- S06?S05_CONFIRM_TO_S06 + S05_DONE + ??????? S06???????????
- S07????????????????????????????????????????
- S10??? ID.3 ?????????? partial card ???
- S13??? 0 ???????36????
- S13?S14???/????????? S14?

## ??
- `py_compile scripts/runtime_s01_to_s10_mainline.py scripts/runtime_s10_to_s16_mainline.py`???
- ?? unittest ????????????? `S01_HOME` ???????????????
- ???????????????????????? result/baseline

## ???
?????????????????????? S10_READY ??????? stop ?????????

`FIXED_SCRIPTS_LEGACY_CODE_CLEANUP_VERIFIED_CLEAN`
