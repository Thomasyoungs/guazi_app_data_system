# 当前真实任务导入目录

`input/current_target_task.json` 是当前真机流程唯一允许读取的临时真实任务文件。

规则：

- 文件必须来自飞书任务表导出的 JSON。
- `source` 必须是 `feishu_export`。
- `simulation_only` 必须是 `false`。
- `task_id` 必须来自飞书表字段，不能由系统或人工临时生成。
- 目标车输入中禁止出现 `reference_index`。
- 文件不存在时系统只输出 `CURRENT_TASK_FILE_NOT_FOUND` 和“等待真实任务导入”，不得使用 fixtures 中的样例文件替代。
- 字段校验通过且 `allow_real_device_operation=true` 后，才允许进入后续真机 APP 动作。
