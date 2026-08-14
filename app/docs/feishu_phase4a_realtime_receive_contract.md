# Phase 4A 真实飞书消息接收契约

## 目标

Phase 4A 只接入飞书长连接消息入口，把真实飞书文本消息转换成现有本地网关事件格式，并复用 Phase 1 草稿任务逻辑。

本阶段仍是安全接收阶段，不启动瓜子 APP，不调用 adb / uiautomator，不调用主流程脚本，不写入 `data/current_target_task.json`。

## 入口命令

真实监听命令：

```bash
python scripts/feishu_realtime_receiver.py --listen
```

本地 dry-run 命令：

```bash
python scripts/feishu_realtime_receiver.py --dry-run-event path/to/sample_event.json
```

dry-run 只读取本地事件 JSON，生成回复预览，不连接飞书服务器。

## 飞书环境变量

真实监听模式只从环境变量读取凭证：

```bash
FEISHU_APP_ID=<your_app_id>
FEISHU_APP_SECRET=<your_app_secret>
FEISHU_TEST_CHAT_ID=<optional_test_chat_id>
```

仓库中不得写入真实 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、飞书 token 或 chat_id。示例只能使用占位符。

## SDK 依赖

真实监听模式使用官方 SDK `lark-oapi`：

```bash
python -m pip install lark-oapi
```

dry-run 和单元测试不依赖 SDK。真实模式缺少 SDK 时返回：

```text
FEISHU_SDK_NOT_INSTALLED
```

## 飞书平台配置建议

在飞书开放平台创建或使用已有应用后，建议按以下方向配置：

1. 开启机器人能力。
2. 开启事件订阅。
3. 订阅接收消息事件。
4. 使用长连接模式接收事件。
5. 将应用安装到测试群或测试会话。
6. 本地设置 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 后执行监听命令。

## 事件适配结果

`scripts/feishu_event_adapter.py` 会把飞书消息事件转换为本地网关事件，至少保留：

```json
{
  "raw_message_id": "om_xxx",
  "raw_sender_id": "ou_xxx",
  "raw_chat_id": "oc_xxx",
  "receive_id": "oc_xxx",
  "message_id": "om_xxx",
  "sender_id": "ou_xxx",
  "chat_id": "oc_xxx",
  "text": "消息文本",
  "created_at": "2026-06-11T00:00:00+00:00"
}
```

`raw_event` 会做敏感字段脱敏后保存到任务目录，便于审计。

## SDK 事件对象兼容

长连接真实回调可能传入 `P2ImMessageReceiveV1` 这类 SDK typed event object，而不是普通 `dict`。

适配器按以下顺序转换：

1. `dict` 事件直接使用。
2. 非 `dict` 事件优先尝试 `lark_oapi.JSON.marshal(data)`。
3. marshal 返回 JSON 字符串时使用 `json.loads`。
4. marshal 返回 `dict` 时直接使用。
5. marshal 不可用或返回非事件结构时，继续尝试 `to_dict()`、JSON 方法、`data`、`__dict__` 和常见属性递归读取。
6. 仍无法识别时返回 `UNSUPPORTED_FEISHU_EVENT_OBJECT`，只记录对象 class name，不输出原始事件 JSON。

文本解析规则：

1. `message_type == "text"` 时解析 `message.content`。
2. `content` 可以是 JSON 字符串、`dict` 或普通字符串。
3. 非 text 消息返回 `UNSUPPORTED_MESSAGE_TYPE`。
4. 空文本返回 `EMPTY_MESSAGE_TEXT`。

`raw_sender_id` 优先取 `open_id`，其次 `user_id`、`union_id` 或字符串 sender_id。

`raw_chat_id` / `receive_id` 优先取 `message.chat_id`，其次顶层 `receive_id`；找不到时返回 `FEISHU_CHAT_ID_MISSING`。

## 安全调试日志

receiver 只记录安全元信息：

```text
received feishu event object: <class_name>
marshal method
event_type
message_type
message_id
chat_id_present
text_length
gateway ok/action
send ok/dry_run
```

日志不得打印 App Secret、access_key、ticket、tenant_access_token、完整 URL query 或完整 SDK 原始事件 JSON。

## 支持的消息

发送：

```text
测试
```

回复：

```text
【二手车定价系统】已收到测试消息，本地飞书网关连接正常。
```

发送：

```text
帮助
```

回复：

```text
请输入定价模板，或使用：
确认 <task_id>
取消 <task_id>
状态 <task_id>
```

发送完整定价模板时，系统只生成：

```text
data/feishu_tasks/<task_id>/target_task_draft.json
```

不会生成 `data/current_target_task.json`。

## 确认任务边界

`确认 <task_id>` 只允许把任务从 `DRAFT` 改为 `CONFIRMED`。

确认回复：

```text
任务 FS... 已确认。

当前仍为安全阶段，尚未自动启动瓜子 APP。
请在本地按 Phase 2/3 命令继续执行。
```

确认任务不会调用：

```text
python scripts/pricing_runner.py --prepare-current-task
python scripts/pricing_runner.py --run-manual
runtime_s10_to_s16_mainline.py
全程跑通.py
```

## 发送模式

`scripts/feishu_send_message.py` 支持 dry-run 和真实发送。

dry-run 默认返回发送 payload，不访问飞书接口。

真实发送模式使用 `chat_id` 作为 `receive_id`，通过环境变量读取 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`。缺少环境变量时返回：

```text
FEISHU_ENV_MISSING
```

缺少 SDK 时返回：

```text
FEISHU_SDK_NOT_INSTALLED
```

发送失败时返回：

```text
FEISHU_SEND_FAILED
```

## 错误码

事件适配错误：

```text
UNSUPPORTED_FEISHU_EVENT_OBJECT
FEISHU_EVENT_MARSHAL_FAILED
FEISHU_CHAT_ID_MISSING
UNSUPPORTED_MESSAGE_TYPE
EMPTY_MESSAGE_TEXT
FEISHU_EVENT_ADAPT_FAILED
GATEWAY_HANDLE_FAILED
```

监听错误：

```text
FEISHU_ENV_MISSING
FEISHU_SDK_NOT_INSTALLED
FEISHU_LISTEN_FAILED
```

发送错误：

```text
FEISHU_SEND_FAILED
```

## 安全边界

Phase 4A 不修改瓜子 APP 自动化主流程，不修改 S01-S16 状态机，不修改 S10-S16 采集、打分、参考车选择 V3、竞争力系数或定价公式。

Phase 4A 不启动瓜子 APP，不调用 adb / uiautomator，不真实调用 `runtime_s10_to_s16_mainline.py`，不真实调用 `全程跑通.py`。
