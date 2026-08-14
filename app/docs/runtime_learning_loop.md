# Runtime Learning Loop Contract

The learning loop is called synchronously by `IssueRecorder.record()` during flow execution. It is not a background worker.

Runtime order:

- Record the issue object with full context.
- For action postcondition failures, call `issue_classifier` before choosing the final issue code.
- Load the current page contract from `config/pages.yaml` and the action contract from `config/actions.yaml`.
- Compare the expected click target with the actual click target and compare the expected next state with the actual next state.
- Classify deterministic contract mismatches before falling back to generic page errors.
- Query `knowledge_base/solutions.jsonl`.
- Match only `approved=true` solutions; candidate solutions are never auto-called.
- Compare every `allowed_auto_actions` item with the current state whitelist. Page states use `config/pages.yaml`; device preflight uses `config/actions.yaml` `runtime_recovery_action_whitelist`.
- Allow at most one automatic recovery attempt.
- If no approved solution is matched, if a candidate is matched, if an action is outside the whitelist, or if retry is exhausted, stop for manual intervention.
- Human fixes are appended as `candidate` first. They become callable only after manual review sets `approved=true`.
- Contract hard gate runs before business clicks: if the page has a unique allowed action and the planned action differs, or if the actual click target differs from the contract target, execution is blocked before the click.

Required solution fields:

- `issue_code`
- `symptoms`
- `root_cause`
- `steps`
- `allowed_auto_actions`
- `manual_required_actions`
- `forbidden_actions`
- `max_auto_retries`
- `approved`
- `created_at`
- `updated_at`

Reusable approved knowledge:

- `TARGET_APP_VERIFIED` records the verified target launcher app for this device: package `com.ganji.android.haoche_c`, app label `瓜子二手车`, launch activity `com.ganji.android.haoche_c/com.cars.guazi.app.home.MainActivity`.
- `com.guazi.android.chesupai` is recorded as excluded and must never be launched as the target app.
- `TARGET_APP_VERIFIED` can be reused only for label-only identity verification actions. It does not authorize page clicks, vehicle-source collection, or launching any app whose `app_label` is not exactly `瓜子二手车`.
- `SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP` is approved recovery knowledge for the pre-click stage only.
- `SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP` allows only: device-status checks, `KEYCODE_WAKEUP`, `wm dismiss-keyguard`, focus/keyguard re-check, screenshot/XML capture, and page re-recognition.
- `SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP` never authorizes bypassing password / fingerprint / pattern lock, blind coordinate taps on system overlays, brand clicks, series clicks, vehicle-source collection, or full-flow execution.
- `THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP` is approved recovery knowledge for third-party login windows or external overlays that cover the verified Guazi flow.
- `THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP` allows only: wake/dismiss non-secure keyguard, focus/keyguard checks, launching the verified Guazi app, screenshot/XML capture, page re-recognition, and recovery through known audited paths back to `S03`.
- `THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP` never authorizes clicking `大众`, clicking any other brand, clicking any series, collecting vehicle data, or continuing full flow before the page contract returns to a verified Guazi state.

## Contract-Based Auto Classification

- `SERIES_ACTION_TARGET_MISMATCH` is approved contract-based knowledge for S04 action target drift.
- In `S04`, the valid action is `click_series_model_button`: locate the target series row, then click only the right-side `车型` button in the same row/card.
- Clicking the series name or card body is forbidden. If that happens and S05 is not verified, the issue must be classified as `SERIES_ACTION_TARGET_MISMATCH`, not `WRONG_PAGE_AFTER_SERIES_CLICK`.
- Contract-derived issues can be recorded with `approved=true` only when the page/action contract proves the cause.
- Unknown pages, unknown popups, and unknown buttons must be recorded as `candidate` and cannot be auto-called.
- Approved solutions still remain bounded by the page/state whitelist and `runtime_recovery_action_whitelist`; `max_auto_retries` remains 1.

## Trim Emission Normalization

- `TRIM_EMISSION_STANDARD_VARIANT` is approved only for emission-standard spelling variants inside the trim text.
- Allowed equivalents are `国5` / `国Ⅴ` / `国V` / `国五` -> `国V`, and `国6` / `国Ⅵ` / `国VI` / `国六` -> `国VI`.
- After emission token normalization, every other trim character must remain exactly equal.
- The system must not fuzzy-match trims, create trim aliases, ignore `DSG`, ignore model year text, or map trim grades such as `尊贵版`, `豪华版`, `精英版`, and `尊荣版` to each other.
- If normalized trim text matches exactly, the runtime may click the APP's original trim node and must write `task_trim_raw`, `app_trim_raw`, `task_trim_normalized`, `app_trim_normalized`, and `emission_normalization_used` to audit.
- This solution never authorizes clicking confirm, entering the vehicle list, collecting vehicle-source data, or changing pricing formulas.

## Action-Planning Contract Drift

- `CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH` is used when a planned action does not match the current page/action contract.
- `S07_CONTRACT_DRIFT_TO_GENERIC_FILTER` is the approved specific rule for S07 vehicle-list planning drift: after entering `S07_VEHICLE_LIST_PAGE`, the next read-only action must locate `车型配置`.
- S07 must not be generalized into a generic filter flow. The system must not click `筛选`, `颜色`, `年份`, `综合排序`, `价格从低到高`, vehicle cards, or detail pages before the `车型配置` entry is detected and the next click is explicitly authorized.
- The approved S07 drift solution is read-only and has `max_auto_retries=0`; it allows screenshot/XML capture, S07 recognition, `detect_vehicle_model_config_entry`, issue recording, and knowledge lookup only.
- `S07_CONTRACT_DRIFT_TO_GENERIC_FILTER` is the approved specific rule for S07 vehicle-list planning drift: after entering `S07_VEHICLE_LIST_PAGE`, the only allowed entry is `车型配置`.
- In this read-only round, S07 may only run `detect_vehicle_model_config_entry`; `click_vehicle_model_config_entry` requires explicit next-turn authorization.
- S07 must not be generalized into a generic filter flow. The system must not click `筛选`, `颜色`, `年份`, `综合排序`, `价格从低到高`, vehicle cards, or detail pages before the `车型配置` entry contract is satisfied.
- Update: when `S07_VEHICLE_LIST_PAGE` is already in `same_source_result_list_mode`, the next read-only action must be `detect_same_source_vehicle_count`.
- `detect_same_source_vehicle_count` is allowed only after the list source is verified as either `from_s08_view_result` or `from_vehicle_detail_back`.
- If a page looks like a vehicle list but its source is not one of those two verified origins, stop with `RESULT_LIST_SOURCE_UNRELIABLE`; do not count, sort, or enter detail.
- If count is exactly `1`, the runtime must not sort and may only enter detail through `tap_single_car_if_no_sort`.
- If count is greater than `1`, the runtime must sort through `tap_sort_if_present -> tap_price_low_to_high` before entering detail.
- If count cannot be stably determined, the runtime must stop with `COUNT_UNCERTAIN`; it must not sort and must not enter detail.
- In `same_source_result_list_mode`, generic filter/color/year actions remain forbidden, and detail entry before the correct count branch is confirmed remains forbidden.
- If the page still shows discovery-zone anchors such as `品牌选车`, `AI选车`, `专区`, `辆在售`, or a zone-entry CTA together with result-list controls and vehicle cards, classify it as `S07_DISCOVERY_RESULT_MIXED_PAGE` instead of `S07_VEHICLE_LIST_PAGE`.
- `S07_DISCOVERY_RESULT_MIXED_PAGE` is no longer blocked merely because discovery/zone anchors remain visible. For same-source result-list identity, reliable list source with strong evidence is the only gate.
- If the current list source is verified with strong evidence as either `from_s08_view_result` or `from_vehicle_detail_back`, the mixed page may still enter the same-source branching chain through `detect_same_source_vehicle_count`.
- If source is anything else, or if strong source evidence is missing, stop with `RESULT_LIST_SOURCE_UNRELIABLE`; do not count, sort, or enter detail.
- `RESULT_PAGE_VARIANT_UNCLASSIFIED` is now reserved for read-only diagnosis when the runtime still cannot safely resolve the page variant/source-path contract from the available evidence. It is not a requirement to wait for a separate pure-result page before allowing the same-source chain.
- `S08_YEAR_CONTRACT_DRIFT_TO_OPTION_SCAN` is the approved specific rule for S08 year/age planning drift.
- On `S08_YEAR_SELECTION_PANEL`, the next valid step is only `detect_left_age_tab` or, after explicit authorization, `click_left_age_tab`.
- The runtime must not generalize S08 into ordinary age-option scanning, `不限车龄`, age ranges, or wide year filters.
- After the left `车龄` tab is active, the runtime must treat the right side as `S08_AGE_EXACT_SLIDER_PANEL`: a dual-handle exact age slider, not a range selector.
- `YEAR_FILTER_USES_EXACT_AGE_SLIDER` is approved reusable knowledge for exact-age-slider filtering. It allows exact age calculation, left-age-tab handling, dual-handle slider detection, and bounded handle setting only within whitelist limits.
- Exact age success requires `left_handle_value == right_handle_value == target_age`, physical overlap of both handles on the same target tick, and a verified target-age calculation. A partial state such as `6-10` or `6-不限` must not be treated as exact.
- `AGE_SLIDER_ONLY_LEFT_HANDLE_SET` is approved knowledge for the case where the left handle has already reached `target_age` but the right handle has not. In that case the only allowed recovery is to keep the left handle unchanged, move only the right handle to `target_age`, then re-verify both handles.
- Exact age filtering never replaces `vehicle_year` secondary validation on the result list; `requires_vehicle_year_secondary_check` must remain true.
- Right-handle recovery diagnostics are candidate-only until manually approved. The current candidate records are `RIGHT_AGE_HANDLE_XML_NODE_MISIDENTIFIED`, `RIGHT_AGE_HANDLE_DRAG_TARGET_Y_MISMATCH`, `AGE_SLIDER_VISUAL_LAYER_XML_MIXED_WITH_BACKGROUND_LIST`, and `RIGHT_AGE_HANDLE_NEEDS_LONG_PRESS_DRAG`.
- These candidate records may refine detection and planning, but they must not trigger real-device drag actions automatically. The runtime may only use them as diagnostics until a human explicitly approves a bounded recovery.
- The right-handle drag end point must be the left handle physical center or an overlap center. The `6` tick label center is not a valid drag target for exact-state verification.
- Background vehicle-list XML nodes seen behind the S08 panel are ignored for page state and handle detection. They must not trigger S07 list flow, list collection, or a confirm/view-result click.
- `TASK_COLOR_CHANGED_INVALIDATES_SELECTED_COLOR` is approved knowledge that blocks downstream year/age, confirm, list-entry, and collection actions when the task color has changed after an older color was already selected on the phone.
- When task color changes, the runtime must re-enter color selection, verify the new task color is selected, and only then continue to year/age actions.
- The corresponding runtime page state is `S08_COLOR_STALE_AFTER_TASK_CHANGE`. It permits only `read_current_selected_color`, `detect_target_color_entry`, `record_issue`, and `lookup_knowledge_base`.
- If the stale selected color is `白色` and the task color is `黑色`, the system must stop before age-slider, confirm/view-result, vehicle-list entry, or collection. The next authorized action must reselect and verify `黑色`.
- `COLOR_MULTI_SELECTED_TARGET_AND_STALE_COLOR` is approved knowledge for the case where S08 has multiple selected colors, such as `黑色` and stale `白色`, while the current task requires `黑色` only.
- The approved recovery is limited to reading selected colors, clicking the stale color once to cancel it, and verifying `S08_COLOR_SELECTED_SINGLE_TARGET`.
- A multi-color state must not proceed to age, confirm/view-result, vehicle list, sorting, detail, or collection. If the stale color cannot be safely located, if the target color is lost after canceling, or if the stale color remains after one click, the runtime must write an issue and stop.

## ADB Transient Device Recovery

- `ADB_TRANSIENT_DEVICE_NOT_FOUND` is recorded when `adb` is available but the first `adb devices -l` output is empty.
- This code means the empty result is treated as a possible ADB server / USB enumeration transient, not yet as final `DEVICE_NOT_FOUND`.
- The only approved automatic recovery is `SOL-ADB-TRANSIENT-DEVICE-NOT-FOUND-RECOVER-ADB-SERVER`.
- The solution allows one attempt only: `adb version`, `adb kill-server`, `adb start-server`, then `adb devices -l`.
- If the second device check returns `device`, record `ADB_TRANSIENT_DEVICE_NOT_FOUND_RECOVERED`, write audit, and rerun task gate, device gate, target-app identity gate, and page-contract gate before any further page action.
- If the second check is still empty, record `DEVICE_NOT_FOUND` and stop for manual USB / MTP / cable / USB debugging authorization checks.
- If the first or second check returns `unauthorized`, stop as `ADB_UNAUTHORIZED`; if it returns `offline`, stop as `DEVICE_OFFLINE`.
- During transient recovery the system must not launch an APP, capture screenshots, dump UI XML, click any page element, click model-config/filter/sort/vehicle-card controls, collect vehicle data, or modify pricing logic.
