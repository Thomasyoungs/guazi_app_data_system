# 飞书接入 Phase 1 集成契约

## Phase 1 目标

飞书 Phase 1 只实现可控入口：解析飞书文本消息、生成本地草稿任务、等待人工确认、支持取消和状态查询、把任务文件落盘到 `data/feishu_tasks/<task_id>/`。本阶段不执行自动定价，不控制瓜子二手车 APP。

这里的 APP 指瓜子二手车 APP。飞书只做消息入口和人工确认入口，不启动 APP，不调用 `adb`、`uiautomator`，不调用 `全程跑通.py`。

## 安全边界

1. 不写入 `data/current_target_task.json`。
2. 不自动启动 `全程跑通.py`。
3. 不自动启动瓜子二手车 APP。
4. 不连接真实飞书生产事件订阅。
5. 不把真实 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、token、chat_id 写进代码或文档。
6. 飞书凭证只允许从环境变量读取。

## 人工确认

人工确认用于把“消息已收到”和“可以进入后续受控 runner”分开。用户发送定价模板后，系统只生成 `DRAFT` 草稿并回显字段；用户回复 `确认 <task_id>` 后，状态变为 `CONFIRMED`，但仍不会启动 APP 或定价脚本。

人工确认的好处：

1. 避免用户消息缺字段或错字段时误触发主流程。
2. 给人工检查品牌、车系、配置、里程、车况等关键字段的机会。
3. 为后续 `pricing_runner.py` 提供清晰的受控入口。

## 阶段规划

Phase 1：只做本地网关和任务草稿，不进入运行态。

Phase 2：接入 `pricing_runner.py`，由人工或本地受控命令把已确认草稿转换为运行输入。

Phase 3：在确认后才允许自动启动，但仍必须经过安全开关、审计日志和主流程保护。

## 禁止用户传入的结果字段

飞书用户不能传入以下字段：

1. `final_reference_index`
2. `final_reference_score`
3. `boundary_reference_index`
4. `competition_coefficient`
5. `suggested_purchase_price`

这些字段必须由后续脚本根据冻结规则计算产生，不能由外部消息覆盖。
