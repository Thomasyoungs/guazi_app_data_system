# 飞书任务字段契约

正式目标车任务必须来自飞书任务。当前尚未接入真实飞书 API 鉴权，因此 API 接入前允许使用人工从飞书任务表导出的 CSV/JSON 作为临时真实任务输入，但必须和 mock 数据严格区分。

| 字段中文名 | 内部名 | 字段类型 | 是否必填 | 示例 | 缺失处理 | 是否参与 APP 操作 | 是否参与定价 | 是否禁止人工填入 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 任务编号 | `task_id` | 文本 / 飞书记录字段 | 是 | `FEISHU-GZ-001` | 阻断 APP 流程 | 是 | 任务追踪 | 是 |
| 品牌 | `brand` | 文本 | 是 | `大众` | 阻断 APP 流程 | 是 | 否 | 否 |
| 车系 | `series` | 文本 | 是 | `帕萨特` | 阻断 APP 流程 | 是 | 否 | 否 |
| 年款 | `model_year` | 文本 | 是 | `2020款` | 阻断 APP 流程 | 是 | 三同约束 | 否 |
| 配置 | `trim` | 文本 | 是 | `330TSI DSG 尊荣版` | 阻断 APP 流程 | 是 | 三同约束 | 否 |
| 颜色 | `color` | 文本 | 是 | `白色` | 阻断 APP 流程；颜色必须精确一致，不做归并 | 是 | 三同约束 | 否 |
| 上牌年月 | `registration_date` | 文本 | 是 | `2020.4` | 无法派生年份时阻断 APP 流程和定价 | 是，派生年份用于匹配 | 是 | 否 |
| 车辆年份 | `vehicle_year` | 派生整数 | 派生 | `2020` | 由 `registration_date` 派生，不能人工填入 | 是 | 是 | 是 |
| 表显里程 | `mileage_10k_km` | 数字，万公里 | 是 | `7.2` | 阻断定价 | 否 | 是 | 否 |
| 过户次数 | `transfer_count` | 整数 | 是 | `1` | 阻断定价 | 否 | 是 | 否 |
| 车况描述 | `condition_text` | 文本 | 是 | `右后门钣金喷漆` | 阻断定价 | 否 | 是 | 否 |
| 出险次数 | `accident_count` | 整数 / 空 | 否 | `1` | 不阻断；缺失时后续按 4 分并标记人工审核 | 否 | 是 | 否 |
| 最大出险金额 | `max_accident_amount` | 数字 / 文本 / 空 | 否 | `5000` | 不阻断；缺失时后续按 3 分并标记人工审核 | 否 | 是 | 否 |
| 参考车的序号 | `reference_index` | 系统生成整数 | 否，目标车输入禁止出现 | `1` | 若出现在目标车输入中，视为契约错误并阻断 | 否 | 参考车输出 | 是 |

## 标准化输出

目标任务标准化后输出：

- `task_id`
- `brand`
- `series`
- `model_year`
- `trim`
- `color`
- `registration_date_raw`
- `vehicle_year`
- `mileage_10k_km`
- `transfer_count`
- `condition_text`
- `accident_count`
- `max_accident_amount`
- `manual_review_required`
- `manual_review_reasons`
- `app_operation_params`
- `allow_real_device_operation`
- `source_import_path`
- `source_imported_at`

`registration_date_raw` 必须保留原始上牌年月，例如 `2020.4`、`2020-04`、`2020年4月`。`vehicle_year` 只作为三同匹配和车龄计算派生值。

## 来源边界

- 正式 API 接入后使用 `source=feishu_api`。
- API 接入前允许使用人工从飞书任务表导出的 CSV/JSON，来源必须为 `source=feishu_export` 且 `simulation_only=false`。
- `source=feishu_export` 必须保留原始导入文件路径 `source_import_path` 和导入时间 `source_imported_at`。
- 本地 mock 必须显式 `source=mock` 且 `simulation_only=true`，只能用于模拟测试和离线回归。
- mock 数据不允许驱动真机 APP 操作。
- `source=mock` 时 `allow_real_device_operation=false`。
- `source=feishu_export` 且 APP 流程字段校验通过时 `allow_real_device_operation=true`。
- `source=feishu_api` 且 APP 流程字段校验通过时 `allow_real_device_operation=true`。
- 未知 `source` 必须阻断。
- 代码不得硬编码飞书 token。
- `task_id` 不允许人工填入或系统生成，必须来自飞书任务表字段。
- `reference_index` 不属于目标车输入，若出现在目标车任务中必须阻断。

## S03 前真实任务门禁

- 进入 S03 品牌选择动作前，运行时必须存在有效 `TargetCarTask`。
- 没有 `TargetCarTask` 时禁止点击品牌。
- `TargetCarTask.source == mock` 时禁止真机点击品牌。
- `TargetCarTask.source == feishu_export` 且字段校验通过时，允许点击目标品牌。
- `TargetCarTask.source == feishu_api` 且字段校验通过时，允许点击目标品牌。
- `brand` 缺失时禁止点击品牌。
- `series`、`model_year`、`trim`、`color`、`vehicle_year` 缺失时禁止进入后续车系、车型、筛选流程。

## 当前任务导入文件

- 当前真机流程只读取 `input/current_target_task.json`。
- `input/current_target_task.json` 必须来自飞书任务表导出。
- 文件不存在时输出 `CURRENT_TASK_FILE_NOT_FOUND` 和“等待真实任务导入”。
- 文件不存在时不得使用 `fixtures/sample_feishu_export_task.json` 替代。
- 校验通过时输出 `TASK_IMPORT_VERIFIED`、标准化 `TargetCarTask`、`app_operation_params` 和 `allow_real_device_operation`。
