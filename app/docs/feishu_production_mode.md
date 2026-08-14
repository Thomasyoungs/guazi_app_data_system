# 飞书定价生产模式约束

## 角色边界

销售 / 评估师只在一线群发送目标车源信息，并在确认卡下回复“确认”。  
主管只在主管复核群回复人工确认收车价。  
管理员只处理系统环境、账号登录、设备授权、页面契约或程序异常。  
后台调度服务负责排队、准备 `current_target_task.json`、运行受控第一段 / 第二段，并生成结果预览。

## 错误分流

目标车信息错误反馈给提交人，要求修改后重新发送完整车源信息。典型错误包括：

- `TARGET_TASK_FIELD_MISSING`
- `TARGET_REQUIRED_FIELD_MISSING`
- `TARGET_DATE_UNRECOGNIZED`
- `TARGET_MODEL_UNRECOGNIZED`
- `TARGET_BRAND_SERIES_INFERENCE_FAILED`
- `TARGET_BRAND_SERIES_CONFLICT`
- `TARGET_FIELD_FORMAT_INVALID`

系统环境错误进入 `SYSTEM_BLOCKED`，通知管理员处理，不要求销售重新发送。典型错误包括：

- `HUMAN_LOGIN_REQUIRED`
- `APP_LOGIN_REQUIRED`
- `ADB_UNAUTHORIZED`
- `DEVICE_OFFLINE`
- `DEVICE_AUTH_REQUIRED`
- `APP_NOT_INSTALLED`
- `APP_NO_RESPONSE`
- `PHONE_LOCKED`

页面契约或程序异常进入 `ADMIN_INTERVENTION_REQUIRED`，通知管理员排查，不要求销售重新发送。典型错误包括：

- `PAGE_CONTRACT_MISMATCH`
- `S10_NOT_READY`
- `FIRST_STAGE_NOT_S10_READY`
- `S14_STALE_FIRST_LINE_BINDING_UNRESOLVED`
- `S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED`
- `MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY`

## 管理员恢复

管理员处理完可恢复的系统环境问题后，在对应管理员提示下回复：

```text
确认
```

只有 `admin_open_ids` 中的管理员可以恢复任务。  
如果同时存在多个可恢复任务，系统不会猜测，会要求回复对应任务卡片“确认”，或输入任务号确认。  
目标车信息错误不能由管理员直接恢复，必须由提交人重新发送车源信息。  
页面契约或程序异常默认不直接恢复队列，需要管理员排查后按实际情况处理。

## 自动健康检查恢复

`SYSTEM_BLOCKED` 不会被取消。任务进入该状态后，队列仍会暂停，避免反复失败和重复控制手机。  
对可恢复的系统环境问题，dispatcher 会按冷却时间执行一次安全 health preflight。检查通过后，任务自动恢复为 `QUEUED`，并继续由队列调度处理。检查失败时保持 `SYSTEM_BLOCKED`，记录 `last_health_check_at`、`health_check_count`、`next_health_check_at`，不重复刷屏。

冷却只用于后台静默轮询。销售 / 评估师回复目标车确认卡“确认”、管理员回复“确认”或“FSxxxx 确认”时，属于飞书主动确认动作，必须立即触发一次强制 health preflight 和安全调度 kick，不受 `next_health_check_at` 拦截。  
如果强制检查仍未恢复，飞书回复使用业务/管理员可读文案，例如“系统暂未开始定价，已通知管理员处理”或“【系统暂未恢复】FSxxxx”，普通业务群不展示 cooldown、runner、dispatcher、status 文件等内部概念。

可自动健康检查恢复的错误：

- `HUMAN_LOGIN_REQUIRED`
- `APP_LOGIN_REQUIRED`
- `ADB_UNAUTHORIZED`
- `DEVICE_OFFLINE`
- `DEVICE_AUTH_REQUIRED`
- `PHONE_LOCKED`
- `APP_NO_RESPONSE`

不可自动恢复的错误：

- `TARGET_INFO_NEEDS_CORRECTION`
- `TARGET_REQUIRED_FIELD_MISSING`
- `TARGET_DATE_UNRECOGNIZED`
- `TARGET_MODEL_UNRECOGNIZED`
- `RULE_SOURCE_CONFLICT`
- `PAGE_CONTRACT_MISMATCH`
- `S14_STALE_FIRST_LINE_BINDING_UNRESOLVED`
- `S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED`
- `FIRST_STAGE_NOT_S10_READY`

历史管理员命令“已处理 / 恢复运行 / 继续队列”仍作为后台兼容入口保留，但普通业务主流程只提示回复“确认”。恢复前会先触发同一套 health preflight。通过才恢复队列；未通过则继续保持 `SYSTEM_BLOCKED`。

## 一字确认自动继续

销售 / 评估师回复目标车确认卡“确认”后，任务进入 `QUEUED`，系统会触发一次安全调度 kick。后台循环已运行时只入队，不重复启动；后台循环未运行时可执行一次安全 `dispatch_once` 封装。

飞书业务群确认回复为：

```text
【定价已开始】FSxxxx
系统已开始自动定价，请等待结果。
```

`NEEDS_REVIEW / WAITING_MANUAL_PRICE` 状态下，“确认”不会被当成价格，系统会提示直接回复价格。  
`TARGET_INFO_NEEDS_CORRECTION` 状态下，“确认”不会继续旧任务，系统会提示重新发送完整目标车源信息。

## 业务文案约束

一线群只展示业务可理解的提示，例如“系统处理中，已通知管理员处理”。  
一线群不展示 PowerShell、runner、adb、uiautomator、脚本参数、日志文件路径、内部状态文件名。  
管理员消息可以包含 `task_id`、错误码、任务状态、建议动作和本地预览文件路径，便于排查。

## 实机边界

生产模式补丁只定义本地状态流转、预览文件和 dry-run 检查。  
本说明不要求启动瓜子 APP，不要求调用 adb / uiautomator，不要求发送真实飞书消息。
