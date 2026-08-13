# 重构版瓜子二手车APP数据系统

## 项目概述

这是一个重构版的瓜子二手车APP数据获取系统，从原项目 `guazi_app_data_system` 迁移而来，保留了核心功能的同时提高了代码可维护性。

### 主要改进

1. **简化项目结构**：减少了不必要的嵌套和复杂的依赖关系
2. **模块化设计**：将功能拆分为独立的模块，便于理解和维护
3. **核心定价逻辑保留**：完整的打分、参考车选择（V3边界确认法）、定价计算
4. **飞书集成**：支持接收飞书消息并返回定价结果
5. **ADB设备支持**：保留Android设备交互能力
6. **契约驱动执行**：引入页面契约执行计划与运行时契约守卫，提升流程可靠性
7. **清晰的接口**：定义了明确的模块接口，便于扩展和测试

## 项目结构

```
restructured_guazi_app/
├── src/                           # 源代码目录
│   ├── main.py                   # 应用程序入口
│   └── guazi_core/              # 核心业务逻辑
│       ├── __init__.py
│       ├── app.py               # 主应用类
│       ├── application.py       # 应用逻辑（build_runtime, run_simulation）
│       ├── models.py            # 数据模型（TargetCar, ReferenceCar, DamageRecord）
│       ├── exceptions.py        # 异常处理（GuaziFlowError, IssueRecorder）
│       ├── config.py            # 配置加载
│       ├── audit.py             # 审计日志
│       ├── feishu_sync.py       # 飞书同步
│       ├── data_collector.py    # 数据收集与模拟
│       ├── pricing_calculator.py  # 定价计算（核心打分、参考车选择、定价）
│       ├── simulator.py         # 状态-动作模拟器
│       ├── output_writer.py     # 输出写入与反馈报告
│       ├── page_recognition.py  # 页面识别
│       ├── page_state_machine.py  # 页面状态机
│       ├── task_normalizer.py  # 任务规范化
│       ├── adb_client.py        # ADB设备客户端（简化版）
│       ├── adb_target_device.py # 严格目标ADB设备选择
│       ├── adb_device_gate.py   # ADB设备门控
│       ├── app_startup.py       # APP启动（含完整AdbClient）
│       ├── trim_normalizer.py   # 配置名称标准化
│       ├── year_age_filter.py  # 车龄筛选与滑块处理
│       ├── reference_early_exit.py  # 参考车低分跳过决策
│       ├── learning_loop.py     # 知识循环查找
│       ├── transient_popup_handler.py  # 弹窗检测与安全关闭
│       ├── runtime_rule_coverage.py    # 规则覆盖率追踪
│       ├── page_contract_execution_plan.py  # 页面契约执行计划
│       ├── runtime_contract_guard.py        # 运行时契约守卫
│       ├── issue_classifier.py  # 契约感知运行时问题分类
│       ├── action_executor.py   # 动作执行与状态机强制
│       └── feishu/              # 飞书集成模块
│           ├── __init__.py
│           ├── message_handler.py
│           └── task_store.py
├── scripts/                     # PowerShell运行时脚本（12个，从原项目迁移）
├── tests/                       # 测试文件
│   ├── __init__.py
│   └── guazi_core/
│       ├── __init__.py
│       ├── test_models.py
│       ├── test_pricing.py
│       └── test_task_normalizer.py
├── config/                      # 配置文件（从原项目迁移）
├── output/                      # 输出文件
├── docs/                        # 文档（从原项目迁移）
├── requirements.txt            # 依赖包列表
└── README.md                   # 项目说明
```

## 快速开始

### 环境要求

- Python 3.10+

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行应用

```bash
# 进入src目录
cd src

# 运行模拟模式
python main.py --mode simulate

# 处理飞书消息
python main.py --mode feishu --feishu-message '{"text": "品牌: 大众\n车系: 帕萨特\n年份: 2020\n里程: 4.5万公里"}' --chat-id 'your_chat_id'
```

## 核心模块说明

### 1. guazi_core.models
- `TargetCar`：目标车辆数据模型
- `ReferenceCar`：参考车辆数据模型
- `DamageRecord`：损伤记录
- `ScoreResult`：打分结果

### 2. guazi_core.pricing_calculator
- `score_target()`：目标车打分
- `score_reference()`：参考车打分
- `select_reference()`：V3边界确认法参考车选择
- `calculate_pricing()`：定价计算
- `calc_competition_coefficient()`：竞争力系数
- `calc_guazi_service_fee()`：瓜子服务费计算

### 3. guazi_core.data_collector
- `DataCollector`：数据收集器，提供模拟目标车和参考车

### 4. guazi_core.simulator
- `StateActionSimulator`：状态-动作模拟器
- `ActionExecutor`：动作执行器

### 5. guazi_core.page_state_machine
- `PageStateMachine`：页面状态机，管理页面状态和动作权限

### 6. guazi_core.feishu
- `FeishuMessageHandler`：处理飞书消息
- `FeishuTaskStore`：任务存储

### 7. guazi_core.task_normalizer
- `TargetCarTask`：飞书任务数据模型
- `normalize_target_task()`：任务数据规范化
- `brand_entry_gate()`：品牌入口门控验证

### 8. guazi_core.adb_target_device / adb_device_gate / app_startup
- `validate_target_device_available()`：严格目标ADB设备选择
- `run_adb_device_gate()`：ADB设备可用性门控检查
- `AdbClient`：完整ADB客户端（截图、XML dump、点击等）

### 9. guazi_core.action_executor
- `ActionExecutor`：带状态机强制的动作执行器，支持契约验证和自动恢复

### 10. guazi_core.issue_classifier
- `IssueClassifier`：契约感知运行时问题分类器，用于诊断页面契约失配和执行失败

### 11. guazi_core.runtime_contract_guard / page_contract_execution_plan
- `build_contract_record()`：构建运行时契约记录
- `ensure_contract_match()`：确保契约匹配，失配时阻止继续执行
- `make_contract_action_plan()`：根据页面契约生成动作执行计划
- `build_action_plan_binding_trace()`：构建动作计划绑定追踪

### 12. guazi_core.learning_loop
- `LearningLoop`：知识循环查找，在请求人工介入前优先查询已批准的解决方案

### 13. guazi_core.transient_popup_handler
- `detect_guazi_push_notification_popup()`：检测瓜子推送通知弹窗
- `close_guazi_push_popup_from_snapshot()`：安全关闭弹窗
- `format_guazi_push_popup_failure_feedback()`：格式化弹窗关闭失败反馈

### 14. guazi_core.reference_early_exit
- 参考车低分跳过决策逻辑

### 15. guazi_core.year_age_filter
- 车龄筛选与精确滑块处理

### 16. guazi_core.trim_normalizer
- 配置名称标准化与排放规则匹配

## 测试

```bash
# 运行测试
pytest tests/
```

## 与原项目的对比

| 模块 | 原项目 | 重构版 | 说明 |
|------|--------|--------|------|
| models.py | 完整 | 完整保留 | 数据模型不变 |
| pricing.py | 1517行 | 精简核心逻辑 | 保留打分、选择、定价核心算法 |
| data_collection.py | 完整 | 完整保留 | 模拟数据收集 |
| action_executor.py | 619行 | 完整保留 | 动作执行与状态机强制 |
| issue_classifier.py | 1738行 | 完整保留 | 契约感知问题分类 |
| page_contract_execution_plan.py | 648行 | 完整保留 | 页面契约执行计划 |
| runtime_contract_guard.py | 935行 | 完整保留 | 运行时契约守卫 |
| page_state_machine.py | 完整 | 完整保留 | 状态机逻辑不变 |
| feishu_sync.py | 270行 | 精简 | 核心同步逻辑保留 |
| task_normalizer.py | 274行 | 完整保留 | 任务规范化完整 |
| app_startup.py | 773行 | 完整保留 | 含完整AdbClient实现 |
| adb_target_device.py | 209行 | 完整保留 | 严格目标设备选择 |
| adb_device_gate.py | 212行 | 完整保留 | 设备门控检查 |
| trim_normalizer.py | 44行 | 完整保留 | 配置名称标准化 |
| year_age_filter.py | 509行 | 完整保留 | 车龄筛选与滑块处理 |
| reference_early_exit.py | 278行 | 完整保留 | 参考车低分跳过决策 |
| learning_loop.py | 296行 | 完整保留 | 知识循环查找 |
| transient_popup_handler.py | 465行 | 完整保留 | 弹窗检测与安全关闭 |
| runtime_rule_coverage.py | 477行 | 完整保留 | 规则覆盖率追踪 |
| scripts/ | 60+脚本 | 已复制 | PowerShell脚本全部迁移 |
| config/ | 20+文件 | 已复制 | 全部迁移 |
| docs/ | 完整 | 已复制 | 全部迁移 |
