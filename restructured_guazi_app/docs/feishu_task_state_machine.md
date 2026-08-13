# 飞书任务状态机

## Phase 1 状态

| 状态 | 含义 |
| --- | --- |
| `INVALID` | 模板已收到，但缺少必填字段，不能确认 |
| `DRAFT` | 草稿已生成，等待人工确认 |
| `CONFIRMED` | 人工已确认，但 Phase 1 不自动运行定价 |
| `CANCELLED` | 任务已取消 |

## 后续预留状态

`QUEUED`、`RUNNING`、`SUCCEEDED`、`FAILED`、`NEEDS_REVIEW` 仅为 Phase 2/Phase 3 预留，Phase 1 代码不得主动进入这些状态。

## 状态转换表

| 触发 | 原状态 | 新状态 | 说明 |
| --- | --- | --- | --- |
| 新模板字段完整 | 无 | `DRAFT` | 生成草稿 |
| 新模板缺必填字段 | 无 | `INVALID` | 落盘校验结果，不可确认 |
| `确认 <task_id>` | `DRAFT` | `CONFIRMED` | 只确认，不启动 APP |
| `取消 <task_id>` | `DRAFT` | `CANCELLED` | 取消草稿 |
| `取消 <task_id>` | `CONFIRMED` | `CANCELLED` | 允许撤销已确认任务 |
| `状态 <task_id>` | 任意 | 不变 | 只读查询 |

## 禁止转换

1. `INVALID` 不能确认。
2. `CANCELLED` 不能确认。
3. `CONFIRMED` 重复确认不改变状态，只提示已确认。
4. 未知 `task_id` 拒绝确认、取消、查询。
5. Phase 1 不允许转换到 `QUEUED`、`RUNNING`、`SUCCEEDED`、`FAILED`、`NEEDS_REVIEW`。

## 为什么 Phase 1 不进入 RUNNING

Phase 1 的目标是把外部飞书消息隔离成可审查的本地草稿。进入 `RUNNING` 意味着开始驱动后续定价流程，可能触发 APP 自动化、真实设备或主流程脚本。为了保护 S01-S16 页面状态机、S10-S16 采集和定价规则，Phase 1 只停留在草稿、确认、取消、查询层。

## 后续 pricing_runner.py 接入方式

Phase 2 可新增受控 `pricing_runner.py`：

1. 只读取 `CONFIRMED` 状态的 `data/feishu_tasks/<task_id>/target_task_draft.json`。
2. 由人工或本地命令显式触发。
3. 在触发前再次校验状态、必填字段和禁止用户传入的结果字段。
4. 通过审计日志记录从草稿到 runner 输入的转换。
5. 只有 Phase 2/Phase 3 明确允许时，才可把草稿转换为当前任务输入。
