# Launcher Later Dialog Click-Once Contract Diagnosis

## 总体结论

本轮诊断完成。

最终判断：`LAUNCHER_LATER_DIALOG_CLICK_ONCE_CONTRACT_DIAGNOSIS_DONE`

这次启动失败不是业务页面契约执行失败，也不是 S01-S10 主流程动作问题。失败发生在 `APP_FORCE_RESTART` 后、尚未点击瓜子 App 图标前的启动器阶段。

弹窗是桌面启动器层的账号退出弹窗。脚本识别到“稍后”并点击了一次，点击后重新 fresh，弹窗仍然存在，因此按当前代码中的单次点击安全策略停止：

`LAUNCHER_ACCOUNT_DIALOG_STILL_VISIBLE_AFTER_LATER_ONCE`

根因归类：

- 主根因：`CONTRACT_MISSING`
- 代码表现：`CONTRACT_IMPLEMENTATION_TOO_STRICT_SINGLE_CLICK`

也就是说，当前页面契约没有正式定义“启动器账号退出弹窗出现时，循环点击稍后直到关闭”的规则；代码里现有逻辑是刻意的“一次点击后仍未消失就停止”，不是遗漏执行了已有契约。

## 一、弹窗类型

| 字段 | 结论 |
|---|---|
| popup_context | `ACCOUNT_LOGOUT_DIALOG` |
| runtime_layer | `LAUNCHER_SYSTEM_DIALOG` |
| popup_texts | `检测到您的账号已退出登录`、`请重新登录账号` |
| button_texts | `稍后`、`去登录` |
| clicked_text | `稍后` |
| clicked_bounds | `[133,1366][569,1509]` |
| click_count | `1` |
| popup_still_visible_after_click | `true` |
| app_icon_visible_after_click | `false` |
| foreground_package | `com.shuqing.launcher` |
| focused_window_before_later | `com.shuqing.launcher/com.shuqing.launcher.Launcher` |
| focused_window_after_later | `com.shuqing.launcher/com.shuqing.launcher.Launcher` |

证据路径：

- 点击前截图：`artifacts/screenshots/device_ready_launcher_before_icon_20260512_191311.png`
- 点击前 XML：`artifacts/debug/device_ready_launcher_before_icon_20260512_191311.xml`
- 点击后截图：`artifacts/screenshots/device_ready_after_launcher_later_20260512_191315.png`
- 点击后 XML：`artifacts/debug/device_ready_after_launcher_later_20260512_191315.xml`

## 二、当前页面契约覆盖情况

| 检查项 | 结论 |
|---|---|
| contract_says_click_later_until_closed | `false` |
| contract_says_single_click_only | `true`，但这是当前代码/候选策略，不是正式“循环关闭”契约 |
| contract_source | `scripts/runtime_s01_to_s10_mainline.py`、`docs/page_state_machine.md`、`docs/codex_development_rules.md` |
| contract_gap | `LAUNCHER_LATER_DIALOG_CONTRACT_MISSING` |

文档证据摘要：

- `docs/page_state_machine.md` 只定义启动前恢复门禁和从 `Launcher/SystemUI` 启动已验证 App 的路径，没有定义启动器账号退出弹窗“稍后直到关闭”的循环契约。
- `docs/codex_development_rules.md` 明确写有“未建契约弹窗不自动点击”。
- 现有代码候选策略写的是：只在 launcher ready gate 中点击一次 `稍后`，然后重新截图/XML/focused_window，再找瓜子图标。

## 三、代码为什么只点一次

定位代码：`scripts/runtime_s01_to_s10_mainline.py`

相关逻辑：

- `_launcher_account_dialog_detected(...)`
- `_find_launcher_account_later_button(...)`
- `_launcher_account_learning_loop_candidate(...)`
- `_handle_launcher_account_dialog_once(...)`

关键行为：

1. 代码设置 `later_click_once_only=true`。
2. 点击 `稍后` 后设置 `launcher_account_later_clicked_once=true`。
3. 点击后确实 fresh 了新截图和 XML。
4. fresh 后仍检测到同一启动器账号弹窗。
5. 因为当前策略禁止重复点击 `稍后`，直接停止：
   `LAUNCHER_ACCOUNT_DIALOG_STILL_VISIBLE_AFTER_LATER_ONCE`

代码中还明确存在候选策略限制：

- 不点击 `去登录`
- 不输入账号、手机号或验证码
- 不在瓜子业务页面使用
- 不重复点击 `稍后`
- 不把它当作页面契约状态

因此，本次不是“代码没有重新 fresh”，也不是“没有识别到弹窗仍在”。它 fresh 后确认弹窗仍在，然后按单次点击策略停止。

## 四、正确契约建议

如果后续确认这是启动入口前的阻塞弹窗，建议新增正式契约：

`LAUNCHER_BLOCKING_LATER_DIALOG_CONTRACT`

建议规则：

1. 仅在 `APP_FORCE_RESTART` 后、还未找到瓜子 App 图标、仍处于桌面/启动器阶段时生效。
2. 弹窗出现 `稍后` 按钮时，唯一允许动作是点击 `稍后`。
3. 点击后必须 fresh。
4. 如果弹窗仍在，继续点击 `稍后`。
5. 最大次数建议 3 次或 5 次，防止死循环。
6. 每次点击必须确认 `clicked_text=稍后`。
7. 禁止点击 `去登录`、立即升级、退出账号、设置、广告主体、未知按钮。
8. 弹窗消失后，重新识别桌面，继续查找瓜子 App 图标。
9. 达到最大次数仍未消失，停止：
   `LAUNCHER_LATER_DIALOG_NOT_DISMISSED_AFTER_MAX_ATTEMPTS`

## 五、下一步建议

建议先补页面契约，再改代码。

推荐补丁名：

`PATCH_LAUNCHER_LATER_DIALOG_REPEAT_UNTIL_CLOSED_CONTRACT`

补丁方向：

- 将单次点击 `稍后` 改为受控循环。
- 每次点击后 fresh。
- 弹窗仍在则继续，直到消失或达到最大次数。
- 弹窗消失后继续找瓜子 App 图标。
- 上限保护。
- 全程只允许点击 `稍后`。

## 六、本轮只读确认

- 未修改代码。
- 未运行实机。
- 未执行 `APP_FORCE_RESTART`。
- 未启动第二段。
- 未采参考车。
- 未定价。
- 未覆盖 `result.json`。
- 仅生成本轮诊断报告。

