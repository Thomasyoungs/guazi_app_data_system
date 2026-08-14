# pricing_runner.py Phase 2 契约

## 目标

Phase 2 在飞书 Phase 1 草稿任务和现有定价脚本之间增加本地受控 runner。它只读取已经 `CONFIRMED` 的飞书任务，把 `target_task_draft.json` 转换为本地 `current_target_task` 格式。

Phase 2 不启动瓜子 APP，不调用 `全程跑通.py`，不调用 `adb` 或 `uiautomator`，不计算价格，不选择参考车，不修改 V3 边界确认法。

## 命令

预览模式：

```bash
python scripts/pricing_runner.py --task-id FS20260609_0001 --dry-run
```

输出 `current_target_task.preview.json` 和 `runner_validation.json`，不写 `data/current_target_task.json`。

准备模式：

```bash
python scripts/pricing_runner.py --task-id FS20260609_0001 --prepare-current-task
```

仅在任务为 `CONFIRMED` 且 `runtime/pricing.lock` 不存在时允许写入 `data/current_target_task.json`。如果旧文件存在，先备份到 `data/backup/current_target_task.<timestamp>.json`，再写新文件，并把任务状态更新为 `QUEUED`。

状态查看：

```bash
python scripts/pricing_runner.py --task-id FS20260609_0001 --status
```

只读输出任务状态、草稿文件是否存在、预览文件是否存在、快照文件是否存在，不改变状态。

## 字段映射

| Feishu draft | current_target_task |
| --- | --- |
| `brand` | `brand` |
| `series` | `series` |
| `model_config` | `model_config` |
| `license_date` | `license_date` |
| `mileage_text` | `mileage_text` |
| `color` | `color` |
| `transfer_count_text` | `transfer_count_text` |
| `condition_text` | `condition_text` |
| `accident_count_text` | `accident_count_text` |
| `max_claim_amount_text` | `max_claim_amount_text` |
| `city` | `city` |
| `remark` | `remark` |
| `task_id` | `task_id` |
| `raw_message_id` | `raw_message_id` |
| `raw_sender_id` | `raw_sender_id` |
| `raw_chat_id` | `raw_chat_id` |

可选字段缺失时不写默认值。`accident_count_text` 缺失时不默认写 `0`，`max_claim_amount_text` 缺失时不默认写 `0`。

## 禁止字段

builder 会忽略飞书草稿中的结果字段，并在 validation warnings 中记录：

1. `final_reference_index`
2. `final_reference_score`
3. `boundary_reference_index`
4. `boundary_reference_score`
5. `competition_coefficient`
6. `suggested_purchase_price`
7. `target_score`
8. `reference_score`

这些字段必须由后续定价脚本根据冻结规则计算产生。

## 状态规则

| 当前状态 | dry-run | prepare-current-task |
| --- | --- | --- |
| `CONFIRMED` | 允许，不改状态 | 允许，成功后改为 `QUEUED` |
| `DRAFT` | 拒绝 | 拒绝 |
| `INVALID` | 拒绝 | 拒绝 |
| `CANCELLED` | 拒绝 | 拒绝 |

## runtime lock

`--dry-run` 不创建锁。`--prepare-current-task` 前检查 `runtime/pricing.lock`，如果存在则拒绝并写 `runner_error.json`。Phase 2 成功准备输入后不长期持有锁，因为本阶段不运行 APP。Phase 3 真正运行 APP 时，`RUNNING` 阶段必须创建并持有 lock。

## 错误码

| 错误码 | 含义 |
| --- | --- |
| `TASK_NOT_FOUND` | 任务目录不存在 |
| `TASK_NOT_CONFIRMED` | 任务不是 `CONFIRMED` |
| `TASK_CANCELLED` | 任务已取消 |
| `TASK_INVALID` | 任务字段校验无效 |
| `TARGET_TASK_DRAFT_MISSING` | 缺少 `target_task_draft.json` |
| `STATUS_JSON_MISSING` | 缺少 `status.json` |
| `CURRENT_TARGET_TASK_EXISTS_BACKED_UP` | 旧 `current_target_task.json` 已备份 |
| `PRICING_LOCK_EXISTS` | `runtime/pricing.lock` 存在，拒绝准备 |
| `CURRENT_TARGET_TASK_WRITE_FAILED` | 写入当前任务失败 |
| `INVALID_MODE` | runner 模式无效 |

## 后续阶段

Phase 3 才考虑手动受控启动 `全程跑通.py`：由人工确认命令触发、检查 lock、写审计日志、进入 `RUNNING` 后持有 lock。Phase 4 才考虑飞书确认后自动进入队列。
