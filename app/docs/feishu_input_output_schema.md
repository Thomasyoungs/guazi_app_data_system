# 飞书输入输出 Schema

## 输入模板

```text
定价
品牌：本田
车系：雅阁
车型配置：2021款 260TURBO 豪华版
上牌日期：2021-06
表显里程：5.8万公里
颜色：白色
过户次数：1
车况：右前门喷漆，前杠喷漆
出险次数：1
最大金额：3200
城市：唐山
备注：客户着急卖
```

支持中文冒号 `：`、英文冒号 `:`，字段名前后允许空格。

## 字段别名

| 输入字段 | 内部字段 |
| --- | --- |
| 品牌 | `brand` |
| 车系 | `series` |
| 车型配置、配置 | `model_config` |
| 上牌日期 | `license_date` |
| 表显里程、里程 | `mileage_text` |
| 颜色 | `color` |
| 过户次数、过户 | `transfer_count_text` |
| 车况、车况描述 | `condition_text` |
| 出险次数、出险 | `accident_count_text` |
| 最大金额、金额 | `max_claim_amount_text` |
| 城市 | `city` |
| 备注 | `remark` |

## 必填字段

`品牌`、`车系`、`车型配置`、`上牌日期`、`表显里程`、`颜色`、`过户次数`、`车况` 为必填字段。缺少必填字段时生成 `INVALID` 状态，不允许确认。

## 可选字段

`出险次数`、`最大金额`、`城市`、`备注` 为可选字段。可选字段缺失时不报错，也不得伪造默认值。

特别约束：

1. 不允许默认填 `出险次数` 为 `0`。
2. 不允许默认填 `最大金额` 为 `0`。
3. Phase 1 不计算分数，不计算价格。

## target_task_draft.json

```json
{
  "task_id": "FS20260609_0001",
  "source": "feishu",
  "status": "DRAFT",
  "brand": "本田",
  "series": "雅阁",
  "model_config": "2021款 260TURBO 豪华版",
  "license_date": "2021-06",
  "mileage_text": "5.8万公里",
  "color": "白色",
  "transfer_count_text": "1",
  "condition_text": "右前门喷漆，前杠喷漆",
  "accident_count_text": "1",
  "max_claim_amount_text": "3200",
  "city": "唐山",
  "remark": "客户着急卖",
  "raw_message_id": "om_xxx",
  "raw_sender_id": "ou_xxx",
  "raw_chat_id": "oc_xxx",
  "created_at": "ISO8601"
}
```

该文件只是飞书草稿，不是 `data/current_target_task.json`。

## validation_result.json

```json
{
  "task_id": "FS20260609_0001",
  "valid": true,
  "missing_required_fields": [],
  "warnings": [],
  "created_at": "ISO8601"
}
```

缺字段示例：

```json
{
  "task_id": "FS20260609_0001",
  "valid": false,
  "missing_required_fields": ["车型配置", "表显里程"],
  "warnings": [],
  "created_at": "ISO8601"
}
```

## status.json

```json
{
  "task_id": "FS20260609_0001",
  "status": "DRAFT",
  "source": "feishu",
  "raw_message_id": "om_xxx",
  "raw_sender_id": "ou_xxx",
  "raw_chat_id": "oc_xxx",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

## 回复文案示例

解析成功：

```text
已生成定价任务草稿：FS20260609_0001

请确认以下信息：

品牌：本田
车系：雅阁
车型配置：2021款 260TURBO 豪华版
上牌日期：2021-06
表显里程：5.8万公里
颜色：白色
过户次数：1
车况：右前门喷漆，前杠喷漆
出险次数：1
最大金额：3200
城市：唐山
备注：客户着急卖

确认无误请回复：
确认

取消请回复：
取消 FS20260609_0001
```

缺字段：

```text
任务未生成，缺少以下必填字段：

1. 车型配置
2. 表显里程

请补充后重新发送完整定价模板。
```

## message_id 去重

实际采用全局本地 JSON 索引：

1. `data/feishu_tasks/processed_message_ids.json` 保存 `raw_message_id -> task_id`。
2. `data/feishu_tasks/task_index.json` 保存 `task_id`、状态、创建时间、更新时间。
3. 收到重复 `raw_message_id` 时，不再生成新 `task_id`，直接回复已处理任务。
