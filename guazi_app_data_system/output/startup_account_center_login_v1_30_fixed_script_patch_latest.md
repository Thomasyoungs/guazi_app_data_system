# V1.30 启动入口账号中心登录页返回退出契约补丁

状态：`STARTUP_ACCOUNT_CENTER_LOGIN_V1_30_FIXED_SCRIPT_AND_LEARNING_LOOP_PATCHED`

## 修改文件

- `scripts/runtime_s01_to_s10_mainline.py`
- `knowledge_base/solutions.jsonl`

未修改：

- 第二段脚本
- pricing / config / DOCX / baseline
- 打分规则 / 服务费规则 / 竞争力系数规则

## 固定脚本实现

新增启动入口识别：

- `recognized_page=S_STARTUP_ACCOUNT_CENTER_LOGIN_PAGE`
- `recognize_startup_account_center_login_page(snapshot)`

识别信号：

- `foreground_package=com.shuqing.tqaccountcenter` 且含“欢迎登录”
- “欢迎登录” + “请输入手机号/请输入手机号码” + “请输入验证码”
- “获取验证码” + “账号密码登录” + “用户协议/隐私协议”

唯一允许动作：

- `STARTUP_ACCOUNT_CENTER_ONLY_ALLOWED_ACTION_PRESS_BACK`

最大次数：

- `max_account_center_back_attempts=2`

流程位置：

`APP_FORCE_RESTART -> 稍后弹窗直到关闭 -> 账号中心登录页 BACK 退出 -> 查找瓜子二手车 APP 图标 -> 进入瓜子 APP`

## 禁止动作

账号中心登录页不允许：

- 点击手机号输入框
- 点击验证码输入框
- 点击获取验证码
- 点击登录
- 点击账号密码登录
- 点击用户协议 / 隐私协议
- 勾选协议
- 输入任何文本
- 当作业务页继续

## Learning Loop

已追加 approved solution：

- `solution_id=SOL-STARTUP-ACCOUNT-CENTER-LOGIN-BACK-EXIT-V1-30`
- `issue_code=STARTUP_ACCOUNT_CENTER_LOGIN_PAGE`
- `category=startup_entry_blocker`
- `contract_version=V1.30`
- `status=approved`

`knowledge_base/solutions.jsonl` 合法性检查：PASS

## 离线验证

| 场景 | 结果 |
|---|---|
| A：账号中心包 + 欢迎登录 | PASS |
| B：第一次 BACK 仍在登录页，第二次退出 | PASS |
| C：两次 BACK 后仍在登录页 | PASS |
| D：尝试点击登录 / 获取验证码 / 输入框 | PASS |
| E：无账号中心，瓜子图标可见 | PASS |

`py_compile scripts/runtime_s01_to_s10_mainline.py`：PASS

## 残留检核

未发现 V1.30 账号中心登录页可执行路径会点击：

- 登录
- 获取验证码
- 手机号 / 验证码输入框
- 协议
- 文本输入

既有 `S_LOGIN` 的“稍后”处理仍保留，但不属于账号中心登录页 V1.30 契约路径。

## 实机验证

设备：`6TGYYHPZCETCSK6L`

本轮只做启动入口验证，未跑完整业务流程。

结果：

- `status=ENTERED_GUAZI_APP_NO_ACCOUNT_CENTER_SEEN`
- 本次未复现账号中心登录页
- 启动器“稍后”弹窗出现并点击 1 次关闭
- 瓜子 APP 图标可见
- 成功进入瓜子 APP
- 识别入口页：`S01`
- 到达入口页后立即停止验证

日志：

- `logs/startup_account_center_v1_30_real_device_20260513_231322.log`

证据：

- `artifacts/screenshots/s01_s10_after_force_restart_20260513_231337.png`
- `artifacts/debug/s01_s10_after_force_restart_20260513_231337.xml`

## 本轮确认

- 未启动第二段
- 未采参考车
- 未定价
- 未覆盖 baseline
- 未写 raw XML / nodes / visible_blob / page_source 大字段到 result

## 结论

V1.30 已沉淀到第一段固定脚本和 learning loop。实机本轮验证通过“无账号中心出现”的正常入口路径；账号中心页 BACK 退出由离线场景覆盖，等待下次真机复现时自动生效。
