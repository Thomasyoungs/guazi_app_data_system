# 页面状态机规范

页面必须同时定义：页面编号、页面名称、识别条件、允许动作、禁止动作、进入下一层条件、返回上一层条件、异常处理方式。配置来源为 `config/pages.yaml`。

## 当前关键流转

- `S01_HOME`：首页，允许点击底部 `选车`。
- `S02_SELECT_CAR_TAB`：选车页，允许点击明确的 `品牌` 入口。
- `S03_BRAND_SELECT_PAGE`：品牌选择页，顶部必须出现 `选择品牌` 四个字，才允许进入目标品牌定位。
- `S04_SERIES_LIST_PAGE`：品牌车系列表页，只允许继续识别目标车系，不允许越级进入车源页。

## 强契约

- 首页识别必须命中底部导航：`首页 / 选车 / 卖车 / 我的`；`新能源` 只能作为辅助特征。
- S02 页面统一命名为 `S02 选车页`，不混用“买车入口”。
- S03 的强识别依据必须包含顶部 `选择品牌`。
- 没有明确页面契约时，不允许点击品牌、车系、车型、车源或其他业务入口。
- 页面契约必须在运行时变成硬门禁：页面 allowed-actions -> 计划动作校验 -> 实际点击目标校验 -> 点击前阻断 -> 点击后验页 -> 失败分类进入 learning loop。
- 如果当前状态只有一个唯一允许动作，执行层的 `planned_action` 必须等于这个动作；否则在点击前阻断并记录 `CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH`。

## 启动前恢复门禁

在任何业务点击前，都必须先通过启动前恢复检查：

1. ADB 状态必须为 `device`。
2. 如果黑屏，只允许执行 `KEYCODE_WAKEUP`。
3. 只允许执行 `wm dismiss-keyguard` 来关闭非安全锁屏。
4. 如果 `mDreamingLockscreen=true`、`isKeyguardShowing=true` 或 `mFocusedWindow=NotificationShade`，禁止点击任何业务元素。
5. 只有系统遮罩清除、前台回到瓜子二手车、并且当前页面重新验证为目标状态后，才允许继续。

## S03 点击品牌前置条件

点击目标品牌前，必须同时满足：

- 当前任务已 `TASK_IMPORT_VERIFIED`；
- `allow_real_device_operation=true`；
- 当前页面已重新识别为 `S03_BRAND_SELECT_PAGE_VERIFIED`；
- 顶部明确出现 `选择品牌`；
- 不存在锁屏、系统遮罩、`NotificationShade`、安全锁屏阻断。

## 明确禁止

- 不允许绕过密码、指纹、图案锁屏。
- 不允许在 `NotificationShade` 或锁屏遮罩上点击 `大众` 或其他品牌。
- 不允许使用坐标盲点关闭系统遮罩。
- 不允许在恢复阶段进入车系、车源或采集流程。

## Verified Recovery Paths

- `Launcher / SystemUI` -> launch verified `?????` -> recognize page -> continue only through verified page contracts.
- `??` -> `??` -> `S02 ???` -> `S03 ?????`.
- `??` -> `S02 ???` -> `S03 ?????`.
- `S02 ???` -> `S03 ?????`.
- `S03 ?????` -> click only the target brand from the verified task.

## S04 Series Action Contract

- `S04_SERIES_LIST_PAGE` allows only `click_series_model_button`.
- `click_series_model_button` means: locate the target series from the verified task, then locate and click the right-side `车型` button in the same row/card.
- Clicking the series name, the series card body, another series, or another series' `车型` button is forbidden.
- If the target series row is visible but the matching `车型` button is not found, record `SERIES_MODEL_BUTTON_NOT_FOUND` and stop.
- If the verified `车型` button is clicked but S05 is not verified, record `MODEL_BUTTON_CLICK_NO_NAVIGATION` and stop.
- If a series card/name click caused the failed transition, classify it as `SERIES_ACTION_TARGET_MISMATCH`.
- `S07_DISCOVERY_RESULT_MIXED_PAGE` is an S07-family discovery/result hybrid page. It still shows discovery-zone anchors such as `品牌选车`, `AI选车`, `专区`, or `辆在售`, while also showing result-list controls and vehicle cards.
- `S07_DISCOVERY_RESULT_MIXED_PAGE` is no longer blocked solely because the page has discovery/zone anchors. Reliable source gate is now the only gate for same-source result-list identity.
- If the current list source is verified as either `from_s08_view_result` or `from_vehicle_detail_back`, then `S07_DISCOVERY_RESULT_MIXED_PAGE` may still be treated as a same-source result list and may enter the same-source branching chain through `detect_same_source_vehicle_count`.
- If the current list source is anything else, stop with `RESULT_LIST_SOURCE_UNRELIABLE`; do not count vehicles, sort, or enter detail.
- Reliable source cannot be guessed. It requires strong chain evidence. For `from_s08_view_result`, the runtime must be able to prove: stable `S08_VEHICLE_MODEL_CONFIG_PANEL`, color confirmed in panel UI, age gate passed, one `查看X辆` click, and list entry after that click. For `from_vehicle_detail_back`, the runtime must be able to prove: stable `VEHICLE_DETAIL_PAGE`, back action executed, and list entry after back.
- On `S07_DISCOVERY_RESULT_MIXED_PAGE`, direct sort clicks and direct detail entry remain forbidden. The page must still enter the branching chain through read-only count detection first.
- `S07_VEHICLE_LIST_PAGE` means brand, series, model year, and trim have been selected and the APP has entered the vehicle list page.
- At `S07_VEHICLE_LIST_PAGE`, the only valid entry is `车型配置`.
- The current read-only step may only run `detect_vehicle_model_config_entry`.
- `click_vehicle_model_config_entry` exists in the action contract but requires explicit next-turn authorization before use.
- Generic `筛选`, `颜色`, `年份`, sorting controls, vehicle cards, detail pages, and vehicle-source field reads are forbidden at this state.
- If planning drifts from `车型配置` to a generic filter/color/year/sort/card action, classify it as `S07_CONTRACT_DRIFT_TO_GENERIC_FILTER` or `CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH` before any click.
- Update: same-source result-list identity is now controlled by the **source gate alone**, not by whether the page has fully converged to a pure result-only layout.
- A page may be treated as a **reliable same-source result list** only from one of two verified sources: (1) after color/age selection and `查看X辆` from the model-config flow, or (2) after returning from `VEHICLE_DETAIL_PAGE`.
- This source rule applies to both `S07_VEHICLE_LIST_PAGE` and `S07_DISCOVERY_RESULT_MIXED_PAGE`.
- If a page looks like a list but does not come from one of those two verified sources, or if the runtime lacks strong evidence for one of those two sources, it must stop with `RESULT_LIST_SOURCE_UNRELIABLE`; it must not count vehicles, sort, or enter detail.
- In `same_source_result_list_mode`, the first read-only action must be `detect_same_source_vehicle_count`.
- If same-source vehicle count is exactly `1`, do not sort; transition to `S07_SINGLE_RESULT_DETAIL_PENDING` and enter detail only through `tap_single_car_if_no_sort`.
- If same-source vehicle count is greater than `1`, transition to `S07_MULTI_RESULT_SORT_PENDING`, then run `tap_sort_if_present -> S09 -> tap_price_low_to_high -> S10 -> tap_next_car_by_price_order -> S11`.
- If count cannot be stably determined, stop with `COUNT_UNCERTAIN`; do not sort and do not enter detail.
- In the single-vehicle branch, `综合排序` and `价格从低到高` remain forbidden.
- In the multi-vehicle branch, skipping sort and entering detail directly is forbidden.

## S08 Color And Age Contract

- `S08_VEHICLE_MODEL_CONFIG_PANEL` is the vehicle model-config panel. Before explicit authorization, it may only read panel contract and click the explicit `颜色` entry.
- `S08_COLOR_SELECTION_PANEL` allows only exact target color logic. Color matching is strict; no family merge, no alias, no fuzzy match.
- The color-selection flow is not complete until the flow has returned to the S08 model-config panel and the target color is visibly confirmed as selected in the panel UI. Internal click success or cached runtime state alone is not enough.
- If the task color changes after a previous selection, the old selected color becomes invalid. The runtime must record `TASK_COLOR_CHANGED_INVALIDATES_SELECTED_COLOR`, block downstream age/confirm/list actions, and force color revalidation first.
- `S08_COLOR_STALE_AFTER_TASK_CHANGE` is the explicit stale-color state. It applies when the S08 panel still shows an old selected color, such as `白色`, while the current task color is `黑色`.
- In `S08_COLOR_STALE_AFTER_TASK_CHANGE`, only read-only color-state inspection and target-color-entry detection are allowed. Continuing to age slider, clicking confirm/view-result, entering the vehicle list, or collecting data is forbidden.
- `S08_COLOR_MULTI_SELECTED` applies when the S08 color state contains multiple selected colors, including the task target color and a stale old color. For example, `selected_colors = [黑色, 白色]` while the task color is `黑色`.
- In `S08_COLOR_MULTI_SELECTED`, the only color-changing action allowed is `cancel_stale_selected_color`, and the actual click target must be the stale color, such as `白色`. Clicking `黑色`, gray, any other color, age, confirm/view-result, vehicle cards, or collection actions is forbidden.
- `S08_COLOR_SELECTED_SINGLE_TARGET` is the only valid post-correction color state: `selected_colors` must contain exactly the current task color. Only after this state is verified can a later explicitly authorized turn proceed to the year/age flow.
- `S08_COLOR_SELECTED` and `S08_COLOR_SELECTED_SINGLE_TARGET` may proceed to `click_year_or_age_entry` only after the selected color is verified to match the current task color **and** the returned S08 panel visibly confirms that color as selected. The runtime gate must require panel-level confirmation evidence such as `selected_color_ui_confirmed`, `selected_color_confirmed_in_panel`, or `selected_color_visible_in_panel`.
- If year/age flow is attempted before that panel-level confirmation, classify it as `COLOR_STATE_NOT_CONFIRMED_BEFORE_AGE_SELECTION` and block year selection, age slider entry, and `查看X辆`.
- `S08_YEAR_SELECTION_PANEL` is not a year-range picker. After entering it, the first valid next step is only to detect or click the left-side `车龄` tab.
- `S08_AGE_EXACT_SLIDER_PANEL` means the left `车龄` tab is active and the right side shows a dual-handle exact age slider and track.
- The age slider is an exact-value contract implemented by two handles, not a range contract. For the current task (`registration_date_raw=2020.4`, `vehicle_year=2020`, current year 2026), the target age is `6`.
- Exact success requires the page to confirm the target exact age after fresh XML. Handle overlap at the target tick is valid evidence, but the runtime must not require a separately visible right handle or a physically separated handle pair.
- S07/S08 exact-age overlap contract `S07_AGE_EXACT_SLIDER_OVERLAP_V1`: exact age success is primarily verified by the page result text after fresh XML. Left and right handles may overlap and this is a valid success state; the runtime must not require a separately visible right handle or a physically separated pair of handles. For `target_age=0`, any of `0年以下`, `0-0年`, or `0年` plus a refreshed bottom `查看X辆` button is enough to set `AGE_FILTER_DONE=true`. For `target_age=1`, the hidden exact tick is between visible `0` and `2` and must be verified by `1-1年`. Existing hidden `11/12` rules remain: `11年` is the first hidden tick right of `10`, `12年` is the second, and both require `11-11年` / `12-12年` text verification. `target_age > 12` must not be mapped to `不限`.
- A partial state such as `6-10`, `6-不限`, or `4-6` is not exact and must stay blocked.
- `S08_AGE_LEFT_HANDLE_SET_ONLY` is a blocked intermediate state: the left handle already equals `6`, but the right handle does not yet equal `6`.
- `S08_AGE_EXACT_VALUE_SELECTED` may be output only after the target exact-age state is verified by result text or by both handles at the target value. A parsed tick value alone is not enough when the page result text does not confirm the exact-age filter.
- Right-handle recognition must prefer the visible green handle body. Tick text, `不限` text, track edges, container edges, no-text containers, and background vehicle-list nodes are not valid handle candidates.
- When moving the right handle to make an exact age, the target point is the left handle's physical center or computed overlap center. The `6` tick label center is only evidence for scale and must not be used as the drag end point.
- Slider recognition must bind to the S08 panel and slider structure. Background list text, price/首付 text, vehicle titles, and vehicle-card copy are background noise, not current page state or handle candidates.
- `不限车龄` is forbidden. Wide age ranges are forbidden. If the exact slider cannot be set to the target age, the flow must stop for manual intervention.
- Even after exact age is set, `requires_vehicle_year_secondary_check=true` must remain true. The vehicle list must still be secondarily filtered so only `vehicle_year = 2020` enters the same-source pool.
