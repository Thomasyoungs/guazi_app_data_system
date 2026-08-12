# S07 有选装旧点击路径诊断

最终状态：`S07_OPTIONAL_FEATURE_OLD_CLICK_PATH_LOCATED`

## 结论

已定位到导致 S07 误选/误点“有选装”的旧代码路径。

根因不在 S03/S04/S05，也不是第二段或 pricing。根因在第一段脚本的 S07 颜色处理路径：

`handle_s07()` 在 `COLOR_FILTER_DONE=false` 时，先用 `_find_target_color_node()` 在当前 S07 XML 中查找目标颜色。只要当前默认面板里已经能看到“白色”文本，就跳过点击左侧“颜色”页签，直接执行 `tap_target_color`。

当前零跑 C10 的 S07 弹窗默认仍停在“车源亮点”面板，同时右侧下方预加载/展示了“颜色”区域和“白色”。旧逻辑因此没有强制先进入“颜色”子状态，也没有禁止“有选装/车源亮点”等非目标项，最终在 S07 面板内发生了“有选装”被选中的错误状态。

## 证据文件

- `output/result_s01_to_s10.json`
- `output/result.json`
- `artifacts/screenshots/s06_to_s07_round_1_20260511_181210.png`
- `artifacts/debug/s06_to_s07_round_1_20260511_181210.xml`
- `artifacts/screenshots/s07_after_color_select_0_20260511_181215.png`
- `artifacts/debug/s07_after_color_select_0_20260511_181215.xml`
- `scripts/runtime_s01_to_s10_mainline.py`

说明：`logs` 目录没有 18:12 这次遗留运行对应的新 stdout 日志，`output/result_s01_to_s10.json` 仍停留在旧的 S05/S06 诊断状态；本轮对“有选装”点击的判断主要来自最新 S07 XML/截图和代码路径。

## 页面证据

点击前：`s06_to_s07_round_1_20260511_181210`

- 页面为 S07 车型配置筛选弹窗。
- 当前左侧高亮/当前区域为“车源亮点”。
- 顶部 chip 有：
  - `0年以下`
  - `2026款 210悦享版`
- 当前右侧面板可见：
  - `已检测`
  - `有选装`
  - `0次过户`
  - `本地车辆`
  - 下方同时可见“颜色”区域和 `白色`
- 底部按钮：`查看4辆`

点击后：`s07_after_color_select_0_20260511_181215`

- 顶部 chip 新增/保留了 `有选装`。
- 右侧 `有选装` 显示为绿色选中态。
- `白色` 仍未显示为选中态。
- 底部按钮仍为 `查看4辆`。

这说明本次所谓“颜色选择”后，实际改变的是“有选装”筛选项，而不是目标颜色“白色”。

## 代码路径

文件：`scripts/runtime_s01_to_s10_mainline.py`

触发函数：

- `handle_s07()`
- `_find_target_color_node()`
- `_target_color_selected()`

关键位置：

- `_find_target_color_node()`：约 `4681-4736`
- `_target_color_selected()`：约 `4778-4785`
- `handle_s07()` 颜色选择主路径：约 `7338-7476`

关键路径摘要：

1. `handle_s07()` 进入 S07 后读取目标颜色。
2. 调用 `_find_target_color_node(snapshot, target_color)`。
3. 如果当前 XML 里已经能找到目标颜色节点，就设置 `color_already_visible=true`。
4. 当 `color_node is not None` 时，不点击左侧“颜色”页签。
5. 直接执行 `machine.assert_action_allowed("S07", "tap_target_color")`。
6. 直接 `client.tap(*_center(color_node["bounds"]))`。
7. 之后 `_target_color_selected()` 只要在可见文本 blob 中看到目标颜色文本，也可能返回 true。

关键代码行为：

- `S07_COLOR_PANEL_ALREADY_VISIBLE_CHECK` 的说明是“当前 S07 XML 中目标颜色可见时跳过颜色页签点击”。
- 这条路径绕过了“COLOR_FILTER_DONE=false 时必须先进入颜色子状态”的契约。
- `_target_color_selected()` 的 fallback 使用 `visible_blob` 中存在目标颜色文本作为成功依据，不能证明颜色已被真实选中。

## 触发条件

本次触发条件符合以下链路：

- 刚从 S06 点击“车型配置”进入 S07。
- 当前 S07 默认/当前面板是“车源亮点”。
- `COLOR_FILTER_DONE=false`。
- `AGE_FILTER_DONE=false`。
- 当前 XML 同屏能看到下方“颜色”区域和 `白色` 文本。
- 脚本未强制点击左侧“颜色”页签。
- 脚本把当前 XML 中找到的颜色文本当成可直接点击的目标。
- S07 没有 forbidden text / forbidden region 门禁阻止“有选装”等非目标项。

## 点击动作来源

不是直接 `tap_text("有选装")`。

不是明确的 `click_first_checkbox()`，也没有发现命名为 `tap_checkbox` / `click_checkbox` / `first_unchecked` 的直接路径。

实际来源是通用颜色选择路径：

`handle_s07 -> _find_target_color_node -> tap_target_color`

它在当前 S07 默认面板中直接处理可见节点，而没有先确认“颜色”页签/颜色面板已成为当前受控子状态。

## 为什么没有被页面契约拦住

旧代码缺少这些门禁：

- 没有 S07 子状态门禁：`COLOR_FILTER_DONE=false` 时没有强制先点击“颜色”。
- 没有禁止项门禁：`有选装 / 车源亮点 / 辅助驾驶 / 电池类型 / 续航里程 / 里程 / 年款车型` 未作为 forbidden。
- `tap_target_color` 只经过动作 ID 断言，没有校验点击目标必须属于“颜色”子面板。
- `_target_color_selected()` 使用可见文本 fallback，不能证明目标颜色已选中。
- 没有检查点击后是否出现了 forbidden chip `有选装`。

## 责任归类

主归类：

- `C. S07_COLOR_GATE_MISSING`

并发风险：

- `A. S07_DEFAULT_PANEL_OPTION_CLICK_LEAK`
- `D. S07_FORBIDDEN_OPTION_GATE_MISSING`
- `E. S07_CONTRACT_ACTION_DISPATCHER_BYPASSED`

不支持作为主因：

- `B. S07_FIRST_CHECKBOX_CLICK_LEAK`

理由：只读搜索未发现明确“点击第一个 checkbox”的可执行函数路径；现有证据更符合“颜色节点复用/当前面板直点”路径。

## 下一步建议修复点

下一步应做最小补丁：

1. S07 增加子状态门禁：`COLOR_FILTER_DONE=false` 时，必须先点击左侧“颜色”页签并 fresh 验证颜色面板成为当前面板。
2. 目标颜色点击只允许发生在颜色面板/颜色区域内。
3. 增加 S07 forbidden 文案/区域检查，禁止点击或选中：
   - `有选装`
   - `车源亮点`
   - `辅助驾驶`
   - `电池类型`
   - `续航里程`
   - `里程`
   - `年款车型`
4. `_target_color_selected()` 不得仅凭 visible text 判断颜色成功，必须验证目标颜色选中态或顶部 chip。
5. 如果点击后出现 forbidden chip，应立即停止并输出明确 stop_code。

## 本轮只读确认

- 未修改代码。
- 未运行实机。
- 未启动第二段。
- 未采参考车。
- 未输出定价。
- 未覆盖 `result.json`。
