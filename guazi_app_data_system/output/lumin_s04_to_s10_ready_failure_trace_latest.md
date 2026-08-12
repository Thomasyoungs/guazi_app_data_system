# 零跑 C10 S04 到 S10_READY 失败链路追踪

最终状态：`READ_ONLY_TRACE_LUMIN_S04_TO_S10_READY_AFTER_SORT_FAILURE_DONE`

## 结论

本轮只读证据表明，表层 stop_code 是 `S10_READY_AFTER_SORT_NOT_CONFIRMED`，但根因不在 S09 排序点击本身。排序动作实际执行了，`价格从低到高`也被选中，价格列表呈非递减；真正的问题是品牌页后没有执行标准 S04/S05 目标车系、年款、配置契约，而是落在 `品牌专区` 混合列表路径后点击了顶部筛选项 `车型配置`。

必须明确两点：

1. 本轮证明 S04/S05 目标契约没有按预期执行。
2. 本轮没有证明点击了左侧车系名。证据显示实际点击的是顶部筛选栏 `车型配置`，bounds=`[965,793,1183,841]`，不是右侧“车型”按钮，也不是左侧车系名。

诊断归类：`S04_CLICK_TARGET_ERROR`

精确子类：`S04_S05_TARGET_CONTRACT_NOT_EXECUTED_BRAND_ZONE_FILTER_CLICKED`

下一步唯一建议：先修 S04/S05。品牌专区落地后必须选择目标车系 `C10/零跑C10` 并确认 `2026款 + 210悦享版`，或显式建立品牌专区替代契约。S09/S10_READY 本轮是正确阻断，不是优先修复点。

## S03 证据

- `S03_BRAND_SEARCH_V2=true`
- 品牌 alias：`零跑 / 零跑汽车 / LEAPMOTOR / Leapmotor`
- 尝试 tab：`只看新能源`
- 尝试字母：`L`
- 命中 alias：`零跑汽车`
- 点击品牌 bounds：`[0,529,1155,685]`
- 点击后识别：`S06`
- XML：`artifacts/debug/s03_to_s04_20260510_141646.xml`
- 截图：`artifacts/screenshots/s03_to_s04_20260510_141646.png`

## S04 追踪

- `s04_search_strategy_version=null`
- `S04_SERIES_SEARCH_V2` 未执行
- 可见车系包含：`全部 / 零跑T03 / 零跑C11 / 零跑C16 / 零跑C10 / 零跑B10 / 零跑C01 / 零跑B01 / 零跑Lafa5 / 零跑S01`
- 实际点击：`车型配置`
- 点击 bounds：`[965,793,1183,841]`
- 点击策略：`text_node_bounds`
- 点击目标类型：品牌专区混合列表顶部筛选项
- `clicked_on_right_models_button=false`
- `clicked_on_left_series_name=false`
- 点击后进入：`S07`
- `s05_contract_matched=false`

S04 结论：`S04_CLICKED_RIGHT_MODELS_BUTTON_AND_ENTERED_S05` 不成立；`S04_CLICKED_LEFT_SERIES_NAME_NOT_MODELS_BUTTON` 也不成立。真实情况是跳过了标准 S04/S05，点击了品牌专区顶部 `车型配置` 筛选项。

## S05 追踪

未发现独立 S05 年款/配置选择证据：

- `selected_year_model=null`
- `year_model_click_text=null`
- `selected_config_model=null`
- `config_click_text=null`
- `green_check_confirmed=false`
- `confirm_clicked=false`
- `s05_to_s06_or_s07_transition_ok=false`

因此，后续 S07 成功只能说明颜色和车龄筛选面板操作成功，不能证明 `C10 / 2026款 / 210悦享版` 目标配置已成立。

## S07 追踪

S07 本身按当前 0 年重合契约通过：

- `target_age=0`
- `matched_age_text=0年以下`
- `age_filter_verify_method=zero_or_below_text`
- `exact_age_overlap_allowed=true`
- `bottom_view_result_text=查看33辆`
- `COLOR_FILTER_DONE=true`
- `AGE_FILTER_DONE=true`
- `S07_FILTER_DONE=true`

证据：

- XML：`artifacts/debug/s07_age_track_based_drag_right_1_20260510_141734.xml`
- 截图：`artifacts/screenshots/s07_age_track_based_drag_right_1_20260510_141734.png`

## 查看 33 辆后页面

点击 `查看33辆` 后确实进入列表类页面，但它是品牌专区/混合车系列表，不是可靠目标三同 S10：

- 可见 `综合排序 / 价格 / 成色/车况 / 车型配置`
- 可见目标 C10 卡片：`零跑汽车 零跑C10 2026款 210悦享版`
- 该卡片价格：`10.64万`
- 该卡片信息：`2026年 | 0.17万公里 | 唐山 | LeapPilot`
- 同屏也有 `零跑T03 / 零跑A10 / 零跑C11 / 零跑C16` 等非目标车系

证据：

- XML：`artifacts/debug/s07_to_s08_20260510_141740.xml`
- 截图：`artifacts/screenshots/s07_to_s08_20260510_141740.png`

## 排序追踪

排序动作实际执行：

- 点击 `综合排序`：是
- 进入 S09 排序弹层：是
- 可见 `价格从低到高`：是
- 点击 `价格从低到高`：是
- 点击 bounds：`[0,1348,1220,1469]`
- 排序弹层关闭：是
- 当前排序文本：`价格从低到高`
- 价格顺序检查：`non_decreasing`

但 `SORT_DONE=false`，原因是排序后页面不是可靠目标三同 S10。

最终排序后可见标题主要为：

- `零跑汽车 零跑T03 2025款 310舒享版`
- `零跑汽车 零跑A10 2026款 403舒享版`
- `零跑汽车 零跑A10 2026款 403悦享版`

证据：

- XML：`artifacts/debug/s09_to_s10_20260510_141750.xml`
- 截图：`artifacts/screenshots/s09_to_s10_20260510_141750.png`

## S10_READY 未确认原因

- `s10_ready_candidate=false`
- `vehicle_card_count=0`
- `trisame_cards_count=0`
- `same_source_cards=[]`
- `trisame_count_confirmed=false`
- 最终排序后视口未看到目标 C10 三同卡
- 页面是混合品牌专区排序结果，不是目标三同列表

因此本次停止是正确停止：没有可靠 S10，不应进入第二段，不应点击车卡。

## 回答用户问题

1. 是否证明 S04 没有按页面契约执行？  
   是。标准 S04/S05 目标车系、年款、配置契约没有执行。

2. 是否证明 S04 点击了左侧而非右侧“车型”？  
   否。证据显示点击的是顶部筛选栏 `车型配置`，不是左侧车系名，也不是右侧“车型”按钮。

3. 真正 stop_code 对应哪条页面契约？  
   表层 stop_code 是 `S10_READY_AFTER_SORT_NOT_CONFIRMED`；根因契约是 S04/S05 目标车系/年款/配置选择门禁缺失或被品牌专区路径绕过。

4. 这次停止是否正确？  
   正确。排序后未确认可靠目标三同 S10，脚本没有启动第二段，符合门禁原则。

5. 下一步应该修 S04 还是修 S09/S10_READY？  
   应先修 S04/S05。S09 排序已执行，S10_READY 未确认是合理阻断。
