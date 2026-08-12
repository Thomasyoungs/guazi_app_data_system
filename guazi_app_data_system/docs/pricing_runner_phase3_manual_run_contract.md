# pricing_runner.py Phase 3 手动受控启动契约

## 目标

Phase 3 在 Phase 2 已经准备好 `data/current_target_task.json` 的基础上，新增人工本地显式启动主流程能力。只有本地人工运行 `--run-manual --allow-app-run` 时，runner 才允许调用主流程脚本。

本阶段仍不允许飞书确认后自动启动，不接真实飞书生产事件，不写真实飞书凭证。

## 与 Phase 2 的关系

Phase 2 负责把 `CONFIRMED` 飞书任务准备为 `QUEUED`，并写入 `data/current_target_task.json`。Phase 3 只接受 `QUEUED` 任务，不接受 `DRAFT`、`CONFIRMED`、`INVALID`、`CANCELLED`、`SUCCEEDED`、`FAILED`。

## 双参数安全阀

必须同时传入：

```bash
python scripts/pricing_runner.py --task-id FS20260609_0001 --run-manual --allow-app-run
```

如果只传 `--run-manual`，错误码为 `APP_RUN_CONFIRMATION_REQUIRED`。这是为了让启动瓜子 APP 自动化主流程这件事必须由本地人工明确确认。

## current_target_task 校验

运行前必须满足：

1. `data/current_target_task.json` 存在。
2. 文件中的 `task_id` 等于当前 `--task-id`。
3. 任务目录中的 `status.json` 状态为 `QUEUED`。

字段兼容表：

| builder 输出 | 主流程读取 |
| --- | --- |
| `brand` | `brand` |
| `series` | `series` |
| `year_model` / `model_year` | `model_year` |
| `config_model` / `trim` | `trim` |
| `color` | `color` |
| `register_date` / `registration_date` | `registration_date_raw` |
| `mileage_10k_km` / `display_mileage_wan_km` | `mileage_10k_km` |
| `transfer_count` | `transfer_count` |
| `condition_text` | `condition_text` |
| `accident_count` | `accident_count` |
| `max_accident_amount` | `max_accident_amount` |

Phase 3 兼容检查确认：`current_target_task_builder.py` 在保留 Phase 2 原始字段的同时，补齐了上述主流程读取字段。

## 主流程脚本路径

当前项目只读识别结果：

1. `runtime_s10_to_s16_mainline.py`：根目录未发现。
2. `scripts/runtime_s10_to_s16_mainline.py`：已发现，但 Phase 3.2 确认它更像 S10-S16 二段入口，依赖 S01-S10 已经到达 `S10_READY`，不能直接视为完整 APP 全链入口。
3. `全程跑通.py`：根目录未发现。
4. `scripts/全程跑通.py`：未发现。

Phase 3.1 起，默认候选顺序锁定为：

1. `runtime_s10_to_s16_mainline.py`
2. `scripts/runtime_s10_to_s16_mainline.py`
3. `全程跑通.py`
4. `scripts/全程跑通.py`

也可以通过 `--main-script path/to/全程跑通.py` 指定。找不到脚本时拒绝运行，错误码为 `MAIN_SCRIPT_NOT_FOUND`，不会创建 lock，不会进入 `RUNNING`。

入口优先级：

1. 命令行 `--main-script`
2. 环境变量 `GUAZI_MAIN_SCRIPT`
3. 默认候选列表

PowerShell 示例：

```powershell
$env:GUAZI_MAIN_SCRIPT="runtime_s10_to_s16_mainline.py"
python scripts/pricing_runner.py --task-id FS20260609_0001 --run-manual --allow-app-run
```

当前建议：先通过 `--diagnose-main-entry` 找到真正 S01-S10 或完整 APP 全链入口，再让 runner 调用该入口。若人工确认只运行 S10-S16 二段，必须先确保 S01-S10 输出已经达到 `S10_READY=true`。

推荐诊断命令：

```bash
python scripts/pricing_runner.py --diagnose-main-entry
```

诊断只读扫描 Python 入口文件，输出 `output/main_entry_diagnosis.json`，不会启动 APP，不调用 adb / uiautomator，不运行主流程。

谨慎手动运行命令示例：

```bash
python scripts/pricing_runner.py --task-id FS20260609_0001 --run-manual --allow-app-run --main-script scripts/runtime_s10_to_s16_mainline.py
```

如果主流程入口不存在，runner 不能进入 `RUNNING`，因为这时无法确定实际会启动哪个受控脚本，也无法保证审计日志、状态和 lock 与真实执行对象一致。

## 状态流

允许：

1. `QUEUED -> RUNNING`
2. `RUNNING -> SUCCEEDED`
3. `RUNNING -> NEEDS_REVIEW`
4. `RUNNING -> FAILED`
5. `QUEUED -> RUNNING_FIRST_STAGE`
6. `RUNNING_FIRST_STAGE -> S10_READY`
7. `RUNNING_FIRST_STAGE -> FAILED`
8. `S10_READY -> RUNNING_SECOND_STAGE`
9. `RUNNING_SECOND_STAGE -> SUCCEEDED`
10. `RUNNING_SECOND_STAGE -> NEEDS_REVIEW`
11. `RUNNING_SECOND_STAGE -> FAILED`

禁止：

1. `DRAFT -> RUNNING`
2. `CONFIRMED -> RUNNING`
3. `INVALID -> RUNNING`
4. `CANCELLED -> RUNNING`
5. `SUCCEEDED/FAILED -> RUNNING`
6. `QUEUED -> RUNNING_SECOND_STAGE`
7. `RUNNING_FIRST_STAGE -> RUNNING_SECOND_STAGE`

## lock 规则

lock 路径为 `runtime/pricing.lock`。运行前如果 lock 已存在，拒绝启动，错误码 `PRICING_LOCK_EXISTS`。创建 lock 后才允许进入 `RUNNING`。无论主流程成功、失败或异常，finally 中都会尝试释放 lock。

lock 内容：

```json
{
  "task_id": "FS20260609_0001",
  "pid": 12345,
  "created_at": "ISO8601",
  "mode": "run-manual"
}
```

## 结果收集

`pricing_result_collector.py` 默认查找：

1. `data/pricing_result.json`
2. `data/latest_pricing_result.json`
3. `output/pricing_result.json`
4. `pricing_result.json`
5. `output/result_s10_to_s16.json`
6. `output/result.json`

也支持 `--result-path` 指定。找到后复制为 `data/feishu_tasks/<task_id>/pricing_result.json`，不修改主流程原始结果文件。找不到结果文件时错误码为 `RESULT_FILE_NOT_FOUND`，JSON 无效时错误码为 `RESULT_JSON_INVALID`。

只读检查确认，当前 `scripts/runtime_s10_to_s16_mainline.py` 的 `_write_second_stage_result(...)` 会写：

1. `output/result_s10_to_s16.json`
2. `config/system.yaml` 中 `paths.result_json` 指向的 `output/result.json`

如果主流程结束后找不到结果文件，任务会进入 `FAILED`，因为 runner 无法判断定价结果、人工审核状态和飞书回传内容。

Phase 3.2 起，runner 在启动前记录 `run_started_at`，并只接受本次启动后新写入的结果文件。若结果文件修改时间早于 `run_started_at`，拒绝收集并返回 `STALE_RESULT_FILE`。

启动前如果以下结果文件已经存在，runner 会复制到任务目录 `pre_run_result_backups/`，不直接删除旧文件：

1. 显式 `--result-path` 指定的结果文件。
2. `output/result_s10_to_s16.json`。
3. `output/result.json`。

手动覆盖结果路径示例：

```bash
python scripts/pricing_runner.py --task-id FS20260609_0001 --run-manual --allow-app-run --main-script scripts/runtime_s10_to_s16_mainline.py --result-path output/result_s10_to_s16.json
```

## SUCCEEDED 判定标准

`return_code=0` 只是主流程进程正常退出，不等于定价成功。

任务只有同时满足以下条件才允许进入 `SUCCEEDED`：

1. 主流程 return code 为 0。
2. 找到本次运行后新写入的 result 文件。
3. result JSON 有效。
4. result 不是阻塞状态或合约状态。
5. result 至少包含一个定价核心字段。
6. `manual_review_required` 不为 true。

定价核心字段包括：

```text
target_score
boundary_confirmed
final_reference_index
final_reference_price
target_guazi_listing_price_yuan
guazi_service_fee_yuan
guazi_net_payout_yuan
suggested_purchase_price_yuan
manual_review_required
```

若 `manual_review_required=true` 且结果 schema 有效，任务进入 `NEEDS_REVIEW`。

## 两段式 APP 主流程

Phase 3.3 起，推荐用两段式受控命令接入当前仓库的真实 APP 自动化入口：

第一段 S01-S10：

```bash
python scripts/pricing_runner.py --task-id FS20260611_0001 --run-first-stage --allow-app-run
```

第二段 S10-S16：

```bash
python scripts/pricing_runner.py --task-id FS20260611_0001 --run-second-stage --allow-app-run
```

默认第一段入口：

```text
scripts/runtime_s01_to_s10_mainline.py
```

默认第一段结果：

```text
output/result_s01_to_s10.json
```

默认第二段入口：

```text
scripts/runtime_s10_to_s16_mainline.py
```

默认第二段结果：

```text
output/result_s10_to_s16.json
```

可选覆盖参数：

```bash
--first-stage-script path/to/runtime_s01_to_s10_mainline.py
--first-stage-result-path output/result_s01_to_s10.json
--second-stage-script path/to/runtime_s10_to_s16_mainline.py
--second-stage-result-path output/result_s10_to_s16.json
```

`--run-manual` 仍保留，但只适合已经人工确认的完整 APP 全链入口。不得再推荐用 `--run-manual` 直接调用 `scripts/runtime_s10_to_s16_mainline.py`。

## 第一段启动前桌面升级弹窗

第一段 `--run-first-stage` 在 `HOME` 回到桌面、点击瓜子 APP 图标之前，会做一次 launcher 层弹窗处理。该处理只针对桌面启动器升级弹窗，不进入 S01-S10 页面状态机，不改变车型筛选、采集、打分、参考车选择或定价规则。

识别关键词来自当前 XML / 文本节点 / OCR 文本，命中任一升级关键词即视为 `DESKTOP_UPGRADE_MODAL_DETECTED`：

```text
软件升级
太擎桌面
稍后升级
立即升级
```

点击策略：

```text
只允许点击：稍后升级
禁止点击：立即升级
```

如果安全点击后弹窗消失，记录：

```text
DESKTOP_UPGRADE_MODAL_DISMISSED
```

如果只看到 `立即升级`，或没有可安全点击的 `稍后升级` 节点，立即停止：

```text
DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS
```

如果点击 `稍后升级` 后弹窗仍存在，只重试 1 次。重试后仍存在则停止：

```text
DESKTOP_UPGRADE_MODAL_DISMISS_FAILED
```

结构化日志字段：

```text
desktop_upgrade_modal_detected: true/false
desktop_upgrade_modal_action: "click_later" / "none"
desktop_upgrade_modal_status: "DISMISSED" / "NO_SAFE_DISMISS" / "DISMISS_FAILED"
```

## 第一段成功标准

第一段 result 满足任一条件即可视为到达 S10：

1. `flow_state.S10_READY == true`
2. `S10_READY == true`
3. `status` 为 `S10_READY` / `S10_READY_DONE` / `FIRST_STAGE_READY`
4. `final_status` 为上述 ready 状态

第一段失败错误码：

```text
FIRST_STAGE_NOT_S10_READY
FIRST_STAGE_RESULT_NOT_FOUND
FIRST_STAGE_RESULT_JSON_INVALID
FIRST_STAGE_TARGET_NOT_FOUND
FIRST_STAGE_SCHEMA_INVALID
```

第一段失败时任务进入 `FAILED`，不会继续运行第二段。失败预览说明：

```text
第一段 S01-S10 未到达 S10_READY，不能继续执行 S10-S16。请检查车型配置、筛选流程、颜色/车龄筛选或页面状态。
```

## 第二段成功标准

第二段沿用 Phase 3.2 的定价结果校验：不能把阻塞结果、`S10_READY=false` 或缺全部定价核心字段的结果当作成功。第二段只允许从 `S10_READY` 状态启动。

第二段成功后：

1. 有效定价结果且 `manual_review_required=false`：`SUCCEEDED`
2. 有效定价结果且 `manual_review_required=true`：`NEEDS_REVIEW`
3. 阻塞结果、旧结果、缺字段或主流程非零退出：`FAILED`

## 旧 result 防误收集

两段式启动前都会备份旧结果到：

```text
data/feishu_tasks/<task_id>/pre_run_result_backups/
```

第一段只接受本次启动后新写入的 `output/result_s01_to_s10.json`。

第二段只接受本次启动后新写入的 `output/result_s10_to_s16.json` / 指定结果文件。

旧文件返回：

```text
STALE_RESULT_FILE
```

## 阻塞结果不是定价结果

以下状态不能视为定价成功：

```text
SECOND_STAGE_BLOCKED_NOT_AT_S10_READY
PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE
S03_TARGET_INITIAL_LETTER_NOT_FOUND
S10_READY=false
```

`SECOND_STAGE_BLOCKED_NOT_AT_S10_READY` 表示当前主流程未到达 S10_READY，S10-S16 二段不能执行。

`PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE` 表示当前入口缺少完整页面合约执行器，不能完成全链 APP 主流程。

这类结果统一标记为 `FAILED`，错误码为：

```text
MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY
```

如果 result 只有合约状态或阻塞状态，没有定价核心字段，标记为 `FAILED`，错误码为：

```text
RESULT_SCHEMA_INVALID_FOR_PRICING
```

## 飞书结果预览

`feishu_result_formatter.py` 生成 `feishu_result_reply.preview.txt`。

成功：标题为 `【定价完成】<task_id>`，包含 V3 边界确认、最终参考车、挂牌价、服务费、回款价、建议收车价。

需审核：标题为 `【需人工审核】<task_id>`，显示 `manual_review_reason` 或 `manual_review_reasons`。

失败：标题为 `【定价失败】<task_id>`，显示错误码和说明。

字段缺失时不报错，显示 `未输出`，并在 formatter warnings 中记录。

Phase 3.2 起，formatter 会再次防守：即使命令行误传 `SUCCEEDED`，只要 result 是阻塞状态或不含定价核心字段，也会生成 `【定价失败】`，不会生成 `【定价完成】`。

阻塞失败预览说明固定为：

```text
当前主流程未到达 S10_READY，S10-S16 二段不能执行。请先运行完整 S01-S10 入口或切换到真正全链 APP 自动化入口。
```

## 安全重排队

失败任务可以安全重排队：

```bash
python scripts/pricing_runner.py --task-id FS20260611_0001 --requeue-failed
```

规则：

1. 只允许 `FAILED -> QUEUED`。
2. 不写 `data/current_target_task.json`。
3. 不启动 APP。
4. 必须写 `audit_log.jsonl`。

`SUCCEEDED -> QUEUED` 默认禁止。只有 runner_result 中包含以下假成功错误码之一时，才允许显式使用：

```bash
python scripts/pricing_runner.py --task-id FS20260611_0001 --requeue-failed --force-requeue-invalid-success
```

允许的假成功错误码：

```text
MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY
RESULT_SCHEMA_INVALID_FOR_PRICING
STALE_RESULT_FILE
MAIN_SCRIPT_NOOP_OR_STALE_RESULT
FIRST_STAGE_NOT_S10_READY
FIRST_STAGE_TARGET_NOT_FOUND
FIRST_STAGE_SCHEMA_INVALID
```

## 历史假成功重校验

如果旧 runner 曾把阻塞结果误标为 `SUCCEEDED`，先运行：

```bash
python scripts/pricing_runner.py --task-id FS20260611_0001 --revalidate-result
```

`--revalidate-result` 只读取任务目录中的 `pricing_result.json` / `runner_result.json`，不启动 APP，不写 `current_target_task.json`。如果发现 result 是阻塞结果或缺核心字段，会改为 `FAILED` 并写 `runner_error.json` 和审计日志。

然后可运行：

```bash
python scripts/pricing_runner.py --task-id FS20260611_0001 --requeue-failed
```

把任务重新放回 `QUEUED`，再按两段式命令执行。

## status 增强

`--status` 会输出：

```text
first_stage_result_exists
first_stage_s10_ready
pricing_result_exists
last_error_code
current_target_task_task_id_match
recommended_next_action
```

推荐动作：

1. `QUEUED`：`run-first-stage`
2. `S10_READY`：`run-second-stage`
3. `FAILED`：`requeue-failed`
4. `RUNNING_FIRST_STAGE` / `RUNNING_SECOND_STAGE`：`wait`

## 配置路径检查

`--diagnose-main-entry` 会只读检查 `config/system.yaml` 中的路径。如果发现绝对路径指向当前项目目录之外，会在 `output/main_entry_diagnosis.json` 中报告：

```text
CONFIG_PATH_OUTSIDE_PROJECT
```

结果路径建议使用相对路径：

```text
output/result_s01_to_s10.json
output/result_s10_to_s16.json
```

如果诊断报告路径疑似旧项目目录，先人工确认配置，再运行真实 APP 自动化入口。

## 错误码

1. `APP_RUN_CONFIRMATION_REQUIRED`
2. `TASK_NOT_FOUND`
3. `TASK_NOT_QUEUED`
4. `TASK_CANCELLED`
5. `TASK_INVALID`
6. `TASK_ALREADY_FINISHED`
7. `CURRENT_TARGET_TASK_MISSING`
8. `CURRENT_TARGET_TASK_TASK_ID_MISMATCH`
9. `PRICING_LOCK_EXISTS`
10. `MAIN_SCRIPT_NOT_FOUND`
11. `MAIN_SCRIPT_FAILED`
12. `RESULT_FILE_NOT_FOUND`
13. `RESULT_JSON_INVALID`
14. `MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY`
15. `RESULT_SCHEMA_INVALID_FOR_PRICING`
16. `STALE_RESULT_FILE`
17. `MAIN_SCRIPT_NOOP_OR_STALE_RESULT`
18. `RESULT_FORMAT_FAILED`
19. `LOCK_RELEASE_FAILED`
20. `TASK_NOT_FAILED`
21. `FORCE_REQUEUE_ERROR_NOT_ALLOWED`
22. `TASK_NOT_S10_READY`
23. `FIRST_STAGE_RESULT_NOT_FOUND`
24. `FIRST_STAGE_RESULT_JSON_INVALID`
25. `FIRST_STAGE_NOT_S10_READY`
26. `FIRST_STAGE_TARGET_NOT_FOUND`
27. `FIRST_STAGE_SCHEMA_INVALID`

## 审计日志

继续写入 `data/feishu_tasks/audit_log.jsonl`，记录：

`run_manual_requested`、`app_run_confirmation_missing`、`run_rejected_by_status`、`lock_created`、`status_changed_to_running`、`main_script_started`、`main_script_finished`、`pricing_result_collected`、`result_format_generated`、`status_changed_to_succeeded`、`status_changed_to_needs_review`、`status_changed_to_failed`、`lock_released`。

## 日志与排查

第一段日志：

```text
first_stage_stdout.log
first_stage_stderr.log
first_stage_run_meta.json
first_stage_result.json
```

第二段日志：

```text
second_stage_stdout.log
second_stage_stderr.log
second_stage_run_meta.json
pricing_result.json
```

不要把阻塞结果发回飞书当作定价完成。Phase 4 后续再做真实结果回传。

## 后续 Phase 4

Phase 4 才考虑真实飞书结果回传或自动队列。届时仍必须保留状态机、lock、审计日志和人工可回滚路径。
