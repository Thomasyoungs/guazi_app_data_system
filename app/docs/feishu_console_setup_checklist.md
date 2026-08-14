# Phase 4B 飞书开放平台配置与自检清单

## 安全前提

不要把真实 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、tenant token 或 chat_id 写进代码、文档、测试、日志或聊天记录。

如果 App Secret 已经在截图、聊天、日志或 Codex 对话中暴露，必须先在飞书开放平台重置 App Secret，再继续接入。不要截图展示 Secret，也不要把 Secret 发给 Codex。

## 本地 PowerShell 操作

```powershell
cd C:\Users\lzc93\Desktop\定价\guazi_app_data_system

python -m pip install lark-oapi

$env:FEISHU_APP_ID='cli_xxxxxxxxxxxxx'
$env:FEISHU_APP_SECRET='your_new_app_secret_here'

python scripts/feishu_preflight_check.py

python scripts/feishu_realtime_receiver.py --listen
```

环境变量只在当前 PowerShell 会话内生效。不要把真实 Secret 写入 `.env`、脚本、文档或测试。

## 飞书开放平台人工配置

1. 打开飞书开放平台。
2. 进入应用“唐山人人车”。
3. 进入“凭证与基础信息”。
4. 如果 App Secret 已经在截图、聊天、日志、Codex 中暴露，先重置 App Secret。
5. 复制新的 App ID 和 App Secret 到本地 PowerShell 环境变量，不写入代码。
6. 进入“机器人”，确认机器人能力已启用。
7. 进入“权限管理”，确认消息相关权限已开通。
8. 进入“事件与回调”。
9. 选择“使用长连接接收事件”。
10. 添加接收消息事件，例如 `im.message.receive_v1` / 接收消息事件，按飞书后台实际名称为准。
11. 保存配置。
12. 发布应用版本。
13. 确认应用已安装到当前飞书账号或目标群聊。
14. 本地启动：`python scripts/feishu_realtime_receiver.py --listen`。
15. 在飞书聊天框发送：`测试`。
16. 期望机器人回复：`【二手车定价系统】已收到测试消息，本地飞书网关连接正常。`
17. 如果仍回复默认 AI 助手文案，说明没有进入本地网关，按排查清单检查。

## 自检命令

```powershell
python scripts/feishu_preflight_check.py
```

也可以通过 receiver 调用同一套自检逻辑：

```powershell
python scripts/feishu_realtime_receiver.py --self-check
```

自检只检查本地环境、SDK 和文件状态，不连接真实飞书，不发送消息，不启动瓜子 APP，不写 `data/current_target_task.json`。

## 飞书 APP 测试文案

先发送：

```text
测试
```

期望回复：

```text
【二手车定价系统】已收到测试消息，本地飞书网关连接正常。
```

再发送：

```text
帮助
```

期望回复：

```text
请输入定价模板，或使用：
确认 <task_id>
取消 <task_id>
状态 <task_id>
```

再发送定价模板：

```text
定价
品牌：本田
车系：雅阁
车型配置：2021款 260TURBO 豪华版
上牌日期：2021-06
表显里程：5.8万公里
颜色：白色
过户次数：1
车况：右前门喷漆，前杠喷漆
出险次数：1
最大金额：3200
城市：唐山
备注：客户着急卖
```

期望回复：

```text
已生成定价任务草稿：FSxxxx
```

此时只应生成 `data/feishu_tasks/<task_id>/target_task_draft.json`，不得自动启动瓜子 APP。

## 错误排查清单

| 现象 | 排查项 |
| --- | --- |
| 没有回复 | 监听脚本没有启动。 |
| 自检环境缺失 | `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 设置错。 |
| 鉴权失败 | 使用了旧的 App Secret。 |
| 鉴权失败 | App Secret 已重置但本地环境变量没更新。 |
| 事件不到本地 | 机器人能力未启用。 |
| 事件不到本地 | 事件订阅没有选择长连接。 |
| 事件不到本地 | 没有添加接收消息事件。 |
| 事件不到本地 | 权限没开。 |
| 事件不到本地 | 应用没发布。 |
| 事件不到本地 | 当前聊天里的机器人不是这个自建应用。 |
| 本地启动失败 | SDK 未安装。 |
| 连接失败 | 本地网络无法访问飞书开放平台。 |
| 收到消息但无回复 | 检查发送消息权限。 |
| 群聊无回复 | 群聊中需要 @机器人，单聊不需要。 |
| 仍回复“你好，我是你的 AI 助手” | 消息未进入本地网关。 |

## Phase 4B 边界

Phase 4B 只做真实接入自检与人工配置清单，不修改瓜子 APP 自动化主流程，不修改 `scripts/runtime_s10_to_s16_mainline.py`，不修改 `全程跑通.py`，不修改 S01-S16 状态机，不修改打分规则、参考车选择 V3、竞争力系数或定价公式。
