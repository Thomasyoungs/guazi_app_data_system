# 重构版瓜子二手车APP数据系统

## 项目概述

这是一个重构版的瓜子二手车APP数据获取系统，旨在简化原有复杂结构，提高代码可维护性。

### 主要改进

1. **简化项目结构**：减少了不必要的嵌套和复杂的依赖关系
2. **模块化设计**：将功能拆分为独立的模块，便于理解和维护
3. **错误处理优化**：改进了错误处理机制，避免因单一错误导致整个流程失败
4. **飞书集成**：添加了飞书消息处理功能，可以接收飞书消息并返回定价结果
5. **清晰的接口**：定义了明确的模块接口，便于扩展和测试

## 项目结构

```
restructured_guazi_app/
├── src/                     # 源代码目录
│   ├── main.py             # 应用程序入口
│   ├── guazi_core/         # 核心业务逻辑
│   │   ├── __init__.py
│   │   ├── app.py          # 主应用类
│   │   ├── application.py  # 应用逻辑
│   │   ├── models.py       # 数据模型
│   │   ├── exceptions.py   # 异常处理
│   │   ├── data_collector.py # 数据收集
│   │   ├── pricing_calculator.py # 定价计算
│   │   ├── simulator.py    # 状态-动作模拟器
│   │   └── feishu/         # 飞书集成模块
│   │       ├── __init__.py
│   │       ├── message_handler.py # 飞书消息处理
│   │       └── task_store.py      # 任务存储
│   ├── utils/              # 工具函数
│   └── helpers/            # 辅助函数
├── tests/                  # 测试文件
├── config/                 # 配置文件
├── data/                   # 数据文件
├── output/                 # 输出文件
├── docs/                   # 文档
├── requirements.txt        # 依赖包列表
└── README.md               # 项目说明
```

## 快速开始

### 环境要求

- Python 3.8+

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

### 1. guazi_core.app
- `GuaziApp`：主应用类，负责协调各个组件的工作

### 2. guazi_core.application
- 包含应用的主要逻辑，如 `build_runtime()` 和 `run_simulation()`

### 3. guazi_core.data_collector
- `DataCollector`：负责收集目标车和参考车的数据
- `TargetCar` 和 `ReferenceCar`：数据模型

### 4. guazi_core.pricing_calculator
- `PricingCalculator`：负责评分和定价计算

### 5. guazi_core.simulator
- `StateActionSimulator`：模拟状态转换和动作执行

### 6. guazi_core.exceptions
- 自定义异常类，如 `GuaziFlowError`

### 7. guazi_core.feishu
- `FeishuMessageHandler`：处理飞书消息并转换为任务数据
- `FeishuTaskStore`：管理飞书任务的存储

## 主要改进点

1. **简化错误处理**：原项目在验证合约时如果出现问题会直接抛出异常，
   新版本改为记录警告并继续执行，避免流程中断。

2. **模块职责分离**：将原来集中在 `main.py` 中的大量功能分散到不同模块中。

3. **默认配置**：提供合理的默认配置，减少对外部配置文件的依赖。

4. **飞书集成**：新增了处理飞书消息的功能，可以接收来自飞书的消息，
   解析车辆信息，进行定价计算，并将结果返回给飞书。

5. **易于扩展**：模块化设计使得添加新功能更加容易。

## 飞书功能说明

### 消息处理流程

1. **接收消息**：通过 `FeishuMessageHandler` 接收飞书消息
2. **解析数据**：将消息文本解析为结构化的车辆数据 (`CarData`)
3. **存储任务**：使用 `FeishuTaskStore` 存储任务信息
4. **执行定价**：运行定价计算流程
5. **返回结果**：将定价结果格式化并通过飞书发送回去

### 使用示例

```python
from guazi_core.app import GuaziApp

app = GuaziApp()
message = {
    "text": "品牌: 大众\n车系: 帕萨特\n年份: 2020\n里程: 4.5万公里",
    "received_at": "2023-10-01T10:00:00Z"
}
result = app.handle_feishu_message(message, chat_id="chat_xxxxx")
```

### 消息格式

飞书消息应包含以下字段：
- `text`: 消息正文，包含车辆信息
- `received_at`: 消息接收时间
- `chat_id`: 发送消息的聊天ID（可选）

车辆信息应按以下格式提供：
```
品牌: [品牌名称]
车系: [车系名称]
年份: [年份]
里程: [里程数]万公里
```