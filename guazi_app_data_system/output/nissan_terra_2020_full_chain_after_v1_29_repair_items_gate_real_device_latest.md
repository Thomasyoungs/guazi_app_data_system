# Nissan Terra V1.29 Repair Items Gate Real Device Validation

状态：`RUN_FAILED_WITH_ISSUE`

目标：`日产|途达|2020款|2.5L XL Upper 4WD 自动四驱豪华版|白|2021.08`

## 本轮确认

- 本轮未修改任何代码。
- 设备 `6TGYYHPZCETCSK6L` 在线。
- `data/current_target_task.json` 目标字段确认为日产途达。
- `output/result_s01_to_s10.json` / `output/result_s10_to_s16.json` / `output/result.json` JSON 合法。
- 未发现 `raw_xml` / `nodes` / `visible_blob` / `page_source` 结构化大字段键。

## 启动策略

当前可靠 S10 页面无法从静态证据确认，因此按约束从 `APP_FORCE_RESTART` 重跑第一段：

`scripts/runtime_s01_to_s10_mainline.py`

日志：

- `logs/nissan_terra_v1_29_first_stage_real_device_20260513_224955.log`

## 第一段结果

第一段未进入业务流程，停止在启动入口：

- `status=APP_ICON_NOT_FOUND_AFTER_LATER_DIALOG_DISMISSED`
- `S10_READY=false`

启动入口观察：

- `launcher_later_dialog_contract_enabled=true`
- `later_dialog_type=ACCOUNT_LOGOUT_DIALOG`
- 点击“稍后”次数：`2`
- 弹窗已关闭：`later_dialog_dismissed=true`
- 弹窗关闭后 APP 图标可见：`false`
- 前台包：`com.shuqing.tqaccountcenter`
- 前台窗口：`com.shuqing.tqaccountcenter/com.shuqing.kdgphone.account.login.LoginHomeActivity`

也就是说，脚本正确执行了“稍后”受控点击，但弹窗关闭后进入账号中心登录页，瓜子图标不可见，因此按启动入口契约停止。

## 第二段结果

第二段未启动。

原因：

- 第一段未到 `S10_READY`
- 不满足第二段启动条件

## V1.29 门禁验证结论

本轮实机没有到达 S13/S14/S15，因此没有触发 V1.29 修复项完整性门禁。

确认：

- 未再次生成不可信 `reference_score=78.0`
- 未错误进入 S15
- 未进入 S16
- 未输出最终定价

但这不是 V1.29 实机通过，只是启动入口阻断导致未被验证。

## 结果文件

- `output/result_s01_to_s10.json`：`APP_ICON_NOT_FOUND_AFTER_LATER_DIALOG_DISMISSED`
- `output/result_s10_to_s16.json`：仍为先前第二段结果，未在本轮刷新
- `output/result.json`：`APP_ICON_NOT_FOUND_AFTER_LATER_DIALOG_DISMISSED`

均为日产途达 fingerprint，未发现结构化大字段键。

## 结论

`V1_29_REPAIR_ITEMS_GATE_NOT_EXERCISED_DUE_TO_STARTUP_BLOCKER`

下一步需要先处理启动入口中“稍后”弹窗关闭后进入账号中心、瓜子图标不可见的问题，再重跑 V1.29 实机验证。
