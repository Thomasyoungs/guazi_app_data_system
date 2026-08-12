# S03 Visible-Only Contract Rewrite

- Final status: `S03_VISIBLE_ONLY_CONTRACT_REWRITE_VERIFIED_CLEAN`
- Modified file: `scripts/runtime_s01_to_s10_mainline.py`
- Real device run: `False`; second stage started: `False`; pricing run: `False`

## Final S03 Structure
fresh S03 snapshot -> detect visible target brand alias -> if visible click brand row right safe point and return -> if not visible stop with S03_TARGET_BRAND_NOT_VISIBLE_CONTRACT_UNDEFINED -> brand-zone page stops.

## Offline Replay
- scenario_a_visible_target: PASS
  - `target_brand_visible`: `True`
  - `matched_alias`: `零跑汽车`
  - `matched_brand_bounds`: `[0, 300, 1080, 380]`
  - `matched_brand_text`: `零跑汽车`
  - `brand_row_bounds`: `[0, 220, 1080, 460]`
  - `selected_click_point`: `[1056, 340]`
  - `selected_click_region_type`: `brand_row_right_safe_point`
  - `next_action`: `S03_ONLY_ALLOWED_ACTION_CLICK_BRAND_ROW_RIGHT_SAFE_POINT`
  - `forbidden_text_seen`: `False`
  - `forbidden_bounds`: `[]`
  - `brand_zone_text_seen`: `False`
  - `brand_zone_bounds`: `[]`
  - `selected_click_point_in_row`: `True`
  - `selected_click_overlaps_forbidden`: `False`
  - `selected_click_overlaps_brand_zone`: `False`
  - `contract_click_valid`: `True`
  - `attempted_new_energy_tab`: `False`
  - `attempted_letter_L`: `False`
  - `attempted_letter_G`: `False`
  - `attempted_alphabet`: `False`
  - `attempted_scroll`: `False`
  - `attempted_brand_name_click`: `False`
  - `attempted_brand_icon_click`: `False`
  - `attempted_row_center_click`: `False`
- scenario_b_target_not_visible: PASS
  - `target_brand_visible`: `False`
  - `next_action`: `STOP`
  - `stop_code`: `S03_TARGET_BRAND_NOT_VISIBLE_CONTRACT_UNDEFINED`
  - `attempted_new_energy_tab`: `False`
  - `attempted_letter_L`: `False`
  - `attempted_letter_G`: `False`
  - `attempted_alphabet`: `False`
  - `attempted_scroll`: `False`
- brand_zone_page: PASS
  - `brand_zone_page_detected`: `True`
  - `continuation_allowed`: `False`
  - `stop_code`: `S04_BRAND_ZONE_PAGE_BLOCKED_BY_CONTRACT`
  - `attempted_find_C10`: `False`
  - `attempted_click_model_config`: `False`
  - `attempted_enter_S05`: `False`
  - `attempted_enter_S07`: `False`
  - `attempted_enter_S10`: `False`

## Residual Keyword Check
- `只看新能源`: category `B`, present=`True`, executable_violation=`False`; present only as false audit flags or non-S03 helper code; no S03 search/click action is reachable
- `new_energy`: category `B`, present=`True`, executable_violation=`False`; present only as false audit flags or non-S03 helper code; no S03 search/click action is reachable
- `新能源`: category `B`, present=`True`, executable_violation=`False`; present only as false audit flags or non-S03 helper code; no S03 search/click action is reachable
- `attempted_tabs`: category `A`, present=`False`, executable_violation=`False`; not present
- `attempted_letters`: category `A`, present=`False`, executable_violation=`False`; not present
- `letter_L`: category `B`, present=`True`, executable_violation=`False`; present only as false audit flags or non-S03 helper code; no S03 search/click action is reachable
- `letter_G`: category `B`, present=`True`, executable_violation=`False`; present only as false audit flags or non-S03 helper code; no S03 search/click action is reachable
- `alphabet`: category `B`, present=`True`, executable_violation=`False`; present only as false audit flags or non-S03 helper code; no S03 search/click action is reachable
- `scroll_brand`: category `A`, present=`False`, executable_violation=`False`; not present
- `search_target_brand_when_not_visible`: category `A`, present=`False`, executable_violation=`False`; not present
- `execute_s03_contract_defined_not_visible_action`: category `A`, present=`False`, executable_violation=`False`; not present
- `scroll_brand_list`: category `A`, present=`False`, executable_violation=`False`; not present
- `click_new_energy_tab`: category `A`, present=`False`, executable_violation=`False`; not present
- `品牌专区`: category `A`, present=`False`, executable_violation=`False`; not present
- `BRAND_ZONE`: category `B`, present=`True`, executable_violation=`False`; present as brand-zone detection/stop or non-S03 page logic; brand-zone continuation remains blocked
- `BRAND_ZONE_MIXED_LIST`: category `B`, present=`True`, executable_violation=`False`; present as brand-zone detection/stop or non-S03 page logic; brand-zone continuation remains blocked
- `target_series_verified`: category `A`, present=`False`, executable_violation=`False`; not present
- `C10`: category `A`, present=`False`, executable_violation=`False`; not present
- `零跑C10`: category `A`, present=`False`, executable_violation=`False`; not present
- `车型配置`: category `B`, present=`True`, executable_violation=`False`; present as brand-zone detection/stop or non-S03 page logic; brand-zone continuation remains blocked
- `fallback`: category `A`, present=`False`, executable_violation=`False`; not present
- `fallback_click`: category `A`, present=`False`, executable_violation=`False`; not present
- `fallback_to`: category `A`, present=`False`, executable_violation=`False`; not present
- `兜底`: category `A`, present=`False`, executable_violation=`False`; not present
- `补救`: category `A`, present=`False`, executable_violation=`False`; not present
- `retry`: category `B`, present=`True`, executable_violation=`False`; present only in S05 year-click retry, outside S03 brand-selection contract cleanup scope
- `guess`: category `A`, present=`False`, executable_violation=`False`; not present
- `old snapshot`: category `A`, present=`False`, executable_violation=`False`; not present

- Executable violation paths remaining: `0`
- Allow next real-device validation: `True`

Final status: `S03_VISIBLE_ONLY_CONTRACT_REWRITE_VERIFIED_CLEAN`
