# Launcher Later Dialog Repeat-Until-Closed Patch

## 总体结论

最终状态：

`LAUNCHER_LATER_DIALOG_REPEAT_UNTIL_CLOSED_PATCH_OFFLINE_VERIFIED`

本轮只完成补丁收尾、py_compile、离线验证、残留检核和报告落盘。未运行实机，未启动第二段，未采车，未定价。

## 1. 修改文件

- `scripts/runtime_s01_to_s10_mainline.py`

## 2. 修改范围

范围检查结论：`PASS`

本轮仅处理第一段固定脚本的启动入口 / launcher / APP 图标查找前 / “稍后”阻塞弹窗逻辑，以及启动失败结果字段瘦身。

工作区当前不是 git repository，无法使用 `git diff` 输出标准 diff；本轮通过定向代码段和关键词扫描确认改动集中在以下启动入口符号：

- `LAUNCHER_ACCOUNT_DIALOG_CORE_TEXTS`
- `LAUNCHER_LATER_DIALOG_TYPE`
- `LAUNCHER_LATER_ACTION_ID`
- `LAUNCHER_LATER_MAX_ATTEMPTS`
- `_launcher_account_learning_loop_candidate`
- `_device_ready_context`
- `_handle_launcher_account_dialog_until_closed`
- APP 图标查找前的 launcher dialog 调用点
- 弹窗关闭后仍找不到图标的 stop_code 分支

未修改 S03-S10 业务页面逻辑。

## 3. 旧 once 路径检核

旧单次点击路径已不可执行。

| keyword | exists | classification |
|---|---:|---|
| `_handle_launcher_account_dialog_once` | false | none |
| `later_click_once_only` | false | none |
| `STILL_VISIBLE_AFTER_LATER_ONCE` | false | none |
| `LAUNCHER_ACCOUNT_DIALOG_NO_LATER_BUTTON` | false | none |
| `launcher_account_dialog_click_later_once` | false | none |
| `single allowed 稍后` | false | none |
| `只点击一次稍后` | false | none |
| `clicked_later_once_then_stop` | false | none |
| `max_later_click_count=1` | false | none |

## 4. 新 until-closed 循环逻辑

新函数：

`_handle_launcher_account_dialog_until_closed(context, snapshot, max_attempts=5)`

契约行为：

1. 仅在 APP_FORCE_RESTART 后、桌面 / 启动器 / APP 图标查找前生效。
2. 检测到启动器账号退出阻塞弹窗时启用：
   `launcher_later_dialog_contract_enabled=true`
3. 唯一允许动作：
   `LAUNCHER_ONLY_ALLOWED_ACTION_CLICK_LATER`
4. 只点击精确文本 `稍后`。
5. 每次点击后 fresh。
6. 弹窗消失后继续原 APP 图标查找流程。
7. 弹窗存在但没有 `稍后`：
   `LAUNCHER_LATER_BUTTON_NOT_FOUND`
8. 达到 5 次仍未关闭：
   `LAUNCHER_LATER_DIALOG_NOT_DISMISSED_AFTER_MAX_ATTEMPTS`
9. 弹窗关闭后仍找不到 APP 图标：
   `APP_ICON_NOT_FOUND_AFTER_LATER_DIALOG_DISMISSED`

禁止按钮：

- `去登录`
- `立即升级`
- `退出账号`
- `确认`
- `设置`
- 未知按钮
- 广告主体

## 5. 结果字段

新增 / 保留结构化字段：

- `launcher_later_dialog_contract_enabled`
- `later_dialog_type`
- `max_later_click_attempts`
- `later_click_attempts`
- `later_dialog_dismissed`
- `app_icon_visible_after_later_dialog`
- `launcher_later_dialog_stop_code`
- `launcher_later_dialog_evidence_paths`
- `launcher_later_click_history`
- `screenshot_path`
- `xml_path`

禁止大字段检查：

- `raw_xml`: 未新增
- `fresh_xml`: 启动失败上下文不再通过 `_device_ready_context` 写入
- `nodes`: 启动失败上下文不再通过 `_device_ready_context` 写入
- `visible_blob`: 未新增
- `page_source`: 未新增
- `full_xml`: 未新增

## 6. 离线验证

| 场景 | 预期 | 实际 | 结果 |
|---|---|---|---|
| A：弹窗存在，稍后可见，点击一次后消失 | `later_click_attempts=1`，继续 APP 图标查找 | `later_click_attempts=1`，`later_dialog_dismissed=true` | PASS |
| B：弹窗存在，第一次仍在，第二次消失 | `later_click_attempts=2`，继续 APP 图标查找 | `later_click_attempts=2`，`later_dialog_dismissed=true` | PASS |
| C：弹窗存在，点击 5 次仍不消失 | `LAUNCHER_LATER_DIALOG_NOT_DISMISSED_AFTER_MAX_ATTEMPTS` | 命中该 stop_code | PASS |
| D：弹窗存在但无稍后 | `LAUNCHER_LATER_BUTTON_NOT_FOUND`，不得点击其他按钮 | 命中该 stop_code，`tap_count=0` | PASS |
| E：无弹窗，APP 图标可见 | 不执行 later dialog 逻辑，继续图标流程 | `later_click_attempts=0`，继续图标流程 | PASS |

## 7. py_compile

命令：

`python -m py_compile scripts/runtime_s01_to_s10_mainline.py`

结果：`PASS`

## 8. 实机验证

本轮按用户最新约束不运行实机。

下一步可以进入启动阶段实机验证，检查真实 launcher 弹窗是否会重复点击 `稍后` 到关闭，并继续找瓜子二手车 APP 图标。

