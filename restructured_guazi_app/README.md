# 重构版瓜子二手车APP数据系统

## 项目概述

这是一个重构版的瓜子二手车APP数据获取系统，从原项目 `guazi_app_data_system` 迁移而来，保留了核心功能的同时提高了代码可维护性。

### 主要改进

1. **简化项目结构**：减少了不必要的嵌套和复杂的依赖关系
2. **模块化设计**：将功能拆分为独立的模块，便于理解和维护
3. **核心定价逻辑保留**：完整的打分、参考车选择（V3边界确认法）、定价计算
4. **飞书集成**：支持接收飞书消息并返回定价结果
5. **ADB设备支持**：保留Android设备交互能力
6. **清晰的接口**：定义了明确的模块接口，便于扩展和测试

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
│       ├── data_collector.py   # 数据收集与模拟
│       ├── pricing_calculator.py  # 定价计算（核心打分、参考车选择、定价）
│       ├── simulator.py         # 状态-动作模拟器
│       ├── config.py           # 配置加载
│       ├── audit.py            # 审计日志
│       ├── output_writer.py    # 输出写入与反馈报告
│       ├── page_recognition.py # 页面识别
│       ├── page_state_machine.py  # 页面状态机
│       ├── task_normalizer.py # 任务规范化
│       ├── feishu_sync.py      # 飞书同步
│       ├── adb_client.py       # ADB设备客户端
│       └── feishu/             # 飞书集成模块
│           ├── __init__.py
│           ├── message_handler.py
│           └── task_store.py
├── scripts/                     # 运行时脚本（从原项目迁移）
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

### 8. guazi_core.adb_client
- `AdbClient`：ADB设备客户端

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
| action_executor.py | 619行 | 简化 | 核心执行逻辑保留 |
| page_state_machine.py | 完整 | 完整保留 | 状态机逻辑不变 |
| feishu_sync.py | 270行 | 精简 | 核心同步逻辑保留 |
| task_normalizer.py | 274行 | 完整保留 | 任务规范化完整 |
| app_startup.py | 773行 | 精简为adb_client.py | ADB核心功能保留 |
| scripts/ | 60+脚本 | 已复制 | 全部迁移 |
| config/ | 20+文件 | 已复制 | 全部迁移 |
| docs/ | 完整 | 已复制 | 全部迁移 |
