# 瓜子二手车 APP 数据系统

这是一个围绕“瓜子二手车 APP + 飞书消息输入 + 目标车定价”场景构建的自动化数据处理系统。

项目当前主线已回到老代码基线，并在此基础上维护结构与关键能力，避免继续做大规模重构导致稳定性下降。

## 1. 项目定位

该系统的核心目标包括：

- 从飞书消息中解析车辆信息
- 对目标车进行字段校验与任务标准化
- 进入瓜子 APP 进行品牌/车系/年款/配置筛选
- 把搜索结果按价格排序并拿到对比车源
- 对目标车做评分、参考车筛选、最终定价
- 输出定价结果、问题记录和审计日志

它主要覆盖两大链路：

1. 任务输入链路
   - 飞书文本/任务字段 -> 规范化 -> 目标车对象
2. 设备执行与定价链路
   - APP 页面筛选 -> 比价 -> 评分 -> 服务费处理 -> 最终定价

## 2. 目录结构

```text
app/
├── .env.example
├── .gitignore
├── AGENTS.md
├── README.md
├── requirements.txt
├── config/                      # 系统配置、页面契约、字段规则等
├── docs/                        # 文档和设计说明
├── fixtures/                    # 测试夹具/样例数据
├── input/                       # 输入文件目录
├── knowledge_base/              # 知识库、已知问题/策略
├── output/                      # 结果输出目录
├── reports/                     # 报告文件
├── scripts/                     # 运行脚本与辅助脚本
├── src/
│   ├── sitecustomize.py         # Windows 测试环境兼容脚本
│   └── guazi_app_data_system/
│       ├── __init__.py
│       ├── action_executor.py
│       ├── adb_device_gate.py
│       ├── adb_target_device.py
│       ├── app_startup.py
│       ├── audit.py
│       ├── config_loader.py
│       ├── data_collection.py
│       ├── exception_handler.py
│       ├── feishu_sync.py
│       ├── field_validation.py
│       ├── issue_classifier.py
│       ├── learning_loop.py
│       ├── main.py
│       ├── models.py
│       ├── output_writer.py
│       ├── page_contract_execution_plan.py
│       ├── page_recognition.py
│       ├── page_state_machine.py
│       ├── pricing.py
│       ├── reference_early_exit.py
│       ├── runtime_contract_guard.py
│       ├── runtime_rule_coverage.py
│       ├── task_normalizer.py
│       ├── transient_popup_handler.py
│       ├── trim_normalizer.py
│       ├── year_age_filter.py
│       └── ...
├── tests/
│   ├── __pycache__/            # 生成缓存，已从仓库中清理
│   └── ...                     # 回归测试与流程测试
├── tools/                      # 实用工具脚本
└── .env.example
```

说明：

- `src/guazi_app_data_system` 是 Python 包根目录，
- 这也是当前项目中最稳定、最常用的包结构之一。
- `config/` 存放全局配置文件，如页面状态、字段规范、规则描述等。
- `scripts/` 存放运行脚本与补丁脚本。
- `output/` 和 `reports/` 用于落盘结果、审计和反馈。
- 生成缓存目录（如 `__pycache__`）已清理，避免误提交和目录噪音。

## 3. 运行方式

### 3.1 安装依赖

```bash
cd E:\project\zhikuan\guazi_app_data_system\app
pip install -r requirements.txt
```

### 3.2 通用运行入口

项目主入口在：

- `src/guazi_app_data_system/main.py`

可用方式：

```bash
cd app\src
python -m guazi_app_data_system.main --help
```

或者直接执行脚本：

```bash
cd app\src\guazi_app_data_system
python main.py --help
```

### 3.3 模拟模式

```bash
cd app\src
python -m guazi_app_data_system.main --mode simulate
```

作用：

- 跑一轮模拟流程
- 生成目标车、参考车和定价结果
- 适合本地验证逻辑

### 3.4 设备模式

```bash
cd app\src
python -m guazi_app_data_system.main --mode device
```

作用：

- 连接真实 Android 设备
- 启动瓜子 APP
- 走品牌/车系/筛选/排序流程
- 获取真实列表并进行定价

### 3.5 飞书消息模式

```bash
cd app\src
python -m guazi_app_data_system.main --mode feishu --feishu-message "{\"text\":\"【车 型】别克君越 2021款 652T 豪华型\\n【指 导 价】23.98\"}"
```

作用：

- 解析飞书消息文本
- 规范化为任务对象
- 进入后续业务流程

## 4. 主要模块职责

### 4.1 `main.py`

入口脚本。负责：

- 解析命令行参数
- 构建运行时配置
- 调用模拟模式或设备模式
- 汇总输出结果

### 4.2 `task_normalizer.py`

任务规范化模块。负责：

- 接收原始目标任务
- 校验字段是否完整
- 推导 `vehicle_year`
- 识别 `simulation_only` 和 `real_device` 允许状态
- 生成标准化 `TargetCarTask`

它是把“原始飞书字段”转成后续系统能用的核心桥接层。

### 4.3 `models.py`

数据模型定义。关键模型：

- `TargetCar`：目标车
- `ReferenceCar`：参考车
- `DamageRecord`：损伤记录
- `ScoreResult`：评分结果
- `CarData`：兼容版轻量数据结构

### 4.4 `pricing.py`

定价核心逻辑。负责：

- `score_target()`：目标车打分
- `select_reference()`：选参考车
- `calculate_pricing()`：最终定价
- 处理损伤、车况、竞争力、服务费等维度

这是系统最重要的业务模块之一。

### 4.5 `data_collection.py`

数据采集模块。负责：

- 生成模拟目标车
- 生成参考车样本
- 供模拟运行和定价测试使用

### 4.6 `page_state_machine.py`

页面状态机。负责：

- 描述 APP 各页面阶段
- 约束合法动作
- 在执行步骤中进行状态转移与校验

### 4.7 `action_executor.py`

动作执行器。负责：

- 执行页面动作
- 驱动 APP 流程
- 在状态机约束下做动作选择和校验

### 4.8 `page_recognition.py`

页面识别模块。负责：

- 识别当前页面类型
- 判定是否在品牌筛选、车型页、结果页等
- 识别页面是否异常、弹窗、登录框等

### 4.9 `app_startup.py`

应用启动与设备交互基础模块。负责：

- 启动瓜子 APP
- 读取 UI XML
- 处理屏幕状态、前台包、窗口状态
- 与 ADB 交互的底层基础能力

### 4.10 `adb_device_gate.py` / `adb_target_device.py`

设备门控模块。负责：

- 验证目标设备是否可用
- 过滤错误设备
- 保证跑流程之前满足设备条件

配置说明（新增）:

- `device_whitelist`: 可选，列出允许使用的设备 serial 列表（逗号分隔或方括号列表）。
  - 当此项非空时，只有在白名单中的设备才能作为目标设备（即使只有一台设备连接）。
  - 示例（adb_target_device.yaml）:

    ```yaml
    active_adb_serial: "3417599354001L0"
    strict_device_selection: true
    allow_default_when_single_device: false
    device_whitelist: [3417599354001L0, 6TGYHPZCETCSK6L]
    ```

- 行为：如果 `device_whitelist` 非空，runner 会拒绝不在白名单内的配置或环境指定 serial，返回错误 `TARGET_ADB_DEVICE_NOT_WHITELISTED`。如需临时在单台测试机上运行，可在配置中把目标 serial 加入白名单或按需允许默认设备（慎用）。

### 4.11 `exception_handler.py`

异常与问题记录模块。负责：

- 统一错误处理
- 归档运行问题日志
- 反馈至审计/学习循环模块

### 4.12 `issue_classifier.py`

问题分类模块。负责：

- 把不同执行失败归类到具体问题类型
- 方便后续人工处理或自动恢复

### 4.13 `learning_loop.py`

学习回路/知识库模块。负责：

- 在遇到已知问题时使用知识库加速恢复
- 允许部分动作按已学习方案自动继续

### 4.14 `field_validation.py`

字段契约校验。负责：

- 校验目标车/参考车字段是否缺失
- 禁止使用非法字段
- 限制输入结构的合规性

### 4.15 `runtime_rule_coverage.py`

运行规则覆盖率统计。负责：

- 统计哪些规则已命中
- 评估流程覆盖和缺失情况

### 4.16 关键脚本速查（简版注释）

下面这些脚本是 `guazi_app_data_system` 目录下最核心的入口和支撑模块：

- `main.py`：系统总入口。负责 CLI 参数解析、运行时构建、模拟模式/设备模式切换以及最终输出汇总。
- `task_normalizer.py`：任务标准化模块。负责把原始飞书字段清洗成统一的 `TargetCarTask`，在这里做字段校验、年份推导和 APP 流程必填项判断。
- `pricing.py`：定价核心。负责目标车打分、参考车筛选、损伤扣分、服务费处理和最终报价计算。
- `data_collection.py`：模拟数据生成器。用于生成目标车和参考车样本，方便本地测试和流程验证。
- `page_state_machine.py`：页面状态机。描述每个 APP 页面允许/禁止的动作，并控制业务流转是否合法。
- `action_executor.py`：动作执行器。根据状态机和业务约束执行点击、等待、返回等动作，负责把页内操作串起来。
- `app_startup.py`：APP 启动与 ADB 基础能力模块。负责探测 ADB、启动瓜子 APP、读取 UI XML 和处理界面状态。
- `field_validation.py`：字段契约守卫。保证任务对象和参考车对象不会出现非法字段或缺失关键字段。
- `exception_handler.py`：统一异常处理中心。记录错误、问题和恢复建议，增强运行稳定性。
- `learning_loop.py`：知识库/学习回路。用于保存已知问题和已验证恢复方案，让流程在复杂场景下更稳。
- `audit.py`：审计日志模块。记录关键动作、状态、返回值和诊断信息，方便复盘和定位问题。
- `output_writer.py`：输出落盘模块。负责把分析结果、报告和反馈写到 `output/`、`reports/` 等目录。

这些脚本共同构成一个“输入 → 标准化 → APP 执行 → 参考车筛选 → 定价输出”的闭环系统。对后续维护来说，最重要的是先看 `main.py`、`task_normalizer.py`、`pricing.py` 这几处入口逻辑。

## 5. 主要业务流程

### 5.1 模拟流程

```text
构建 runtime -> DataCollector 生成数据 -> score_target -> select_reference -> calculate_pricing -> 输出 JSON
```

### 5.2 真实设备流程

```text
目标任务 -> normalize_target_task -> 设备门控 -> 启动 APP -> 页面识别 -> 状态机驱动 -> 品牌/车系筛选 -> 参考车提取 -> 定价输出
```

### 5.3 飞书输入流程

```text
飞书消息 -> 解析器 -> 结构化字段 -> TargetCarTask -> normalize_target_task ->业务流程
```

## 6. 技术特点

- 支持模拟模式和真实设备模式切换
- 具备页面状态机与动作约束
- 具备规则化字段校验
- 具备问题分类和学习回路
- 具备真实设备运行时的异常处理能力
- 解决了“从飞书输入到 APP 处理与定价”的闭环问题

## 7. 使用建议

当前项目建议遵循以下工作方式：

1. 先在 `tests/` 目录中补充回归用例
2. 使用真实样例测试飞书消息
3. 先验证字段标准化与目标车创建
4. 再验证 APP 页面过滤流程
5. 最后验证定价结果是否合理

## 8. 当前状态说明

该项目当前处于“主线稳定 + 持续修正”的状态：

- 核心逻辑保留了老项目中的关键能力
- 目录结构已经被整理得更清晰
- 运行入口和模块职责更容易维护
- 仍需继续补齐某些真实消息样式兼容和品牌映射覆盖

## 9. 进一步优化方向

建议后续重点放在以下几项：

- 飞书消息样式兼容（多种文本结构）
- 品牌/车系/模型别名补全
- S03 品牌匹配稳定性提升
- 对真实设备流程增加更强的回退和恢复逻辑
- 补足更全面的测试样例

## 10. 结论

这个项目的主线是：

- “飞书任务输入” -> “标准化处理” -> “APP 搜索筛选” -> “参考车提取” -> “目标车定价”

如果你要开展后续开发，最重点的是：

- 保证真实消息兼容
- 保证品牌/车系的稳定识别
- 保证 APP 流程的稳定执行
- 保证定价结果可回归验证


---

如需，我还可以继续帮你做两件事中的任意一个：

1. 生成一版更详细的“模块依赖图”说明
2. 直接补一份更偏“开发者文档”的 README（更偏技术架构说明版本）
