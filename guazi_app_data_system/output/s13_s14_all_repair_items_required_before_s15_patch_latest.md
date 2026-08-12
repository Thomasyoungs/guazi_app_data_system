# V1.29 S13/S14 全部修复项采集门禁补丁

状态：`V1_29_S13_S14_ALL_REPAIR_ITEMS_REQUIRED_BEFORE_S15_FIXED_SCRIPT_PATCHED`

目标：`日产|途达|2020款|2.5L XL Upper 4WD 自动四驱豪华版|白|2021.08`

## 修改范围

只修改：

- `scripts/runtime_s10_to_s16_mainline.py`

未修改：

- `scripts/runtime_s01_to_s10_mainline.py`
- pricing / config / DOCX / baseline 文件
- 打分规则 / 服务费阶梯 / 竞争力系数规则

## 落地规则

1. `repair_counts` 不作为直接评分字段，只作为采集完整性门禁。
2. `S14_COLLECT_DONE=true` 只代表当前具体修复项完成。
3. `S14_COLLECT_DONE=true` 不再等价于整辆参考车修复详情完成。
4. S15 前必须满足：
   - `S11_DONE=true`
   - `S12_DONE=true`
   - `S13_DONE=true`
   - 若 `s13_total_repair_count=0`，可跳过 S14
   - 若 `s13_total_repair_count>0`，必须 `all_repair_items_collect_done=true`
   - `collected_repair_item_count >= expected_repair_item_count`
5. 修复项采集不完整时：
   - 不进入 S15
   - 不生成可信 `reference_score`
   - 不作为 `final_reference`
   - 不进入 S16 定价

## 新增 / 强化字段

- `s13_total_repair_count`
- `s13_region_repair_counts`
- `expected_repair_item_count`
- `enumerated_repair_item_count`
- `collected_repair_item_count`
- `collected_repair_items`
- `missing_repair_item_count`
- `missing_repair_items`
- `current_repair_item_id`
- `current_repair_item_text`
- `current_repair_item_region`
- `current_repair_item_collect_done`
- `all_repair_items_collect_done`
- `s15_entry_allowed`
- `s15_entry_block_reason`
- `reference_score_trustworthy`
- `reference_score_invalid_reason`

阻断状态：

- `S15_BLOCKED_REPAIR_DETAILS_INCOMPLETE`
- `reference_score_invalid_reason=repair_details_incomplete_before_s15`

## 日产途达负例锁定

输入事实：

- `s13_total_repair_count=10`
- `collected_repair_item_count=1`
- `collected_repair_items=[左前翼子板]`
- `current_repair_item_collect_done=true`

补丁后结果：

- `all_repair_items_collect_done=false`
- `s15_entry_allowed=false`
- `reference_score_trustworthy=false`
- `reference_score_invalid_reason=repair_details_incomplete_before_s15`
- 不得再生成可信 `reference_score=78.0`
- 不得按 `78.0 < target_score` 继续下一辆

## 离线验证

| 场景 | 预期 | 结果 |
|---|---|---|
| A：日产途达真实负例，10 项只采 1 项 | 阻断 S15，不可信评分 | PASS |
| B：`repair_count=0` | 可跳过 S14，允许 S15 | PASS |
| C：`repair_count=3`，已采 3 项 | 允许 S15 | PASS |
| D：`repair_count=3`，只采 2 项 | 阻断 S15，缺 1 项 | PASS |
| E：`repair_count>0`，无法枚举具体项 | 阻断 S15，不可信评分 | PASS |
| F：单个 S14 item 完成 | 只标记当前项完成，不标记整车完成 | PASS |

`py_compile scripts/runtime_s10_to_s16_mainline.py`：PASS

## 实机状态

本轮未运行实机，未启动第二段，未采车，未定价。

本轮未覆盖：

- `output/result_s10_to_s16.json`
- `output/result.json`
- baseline 文件

## 输出约束

未写入以下大字段：

- `raw_xml`
- `fresh_xml`
- `nodes`
- `visible_blob`
- `page_source`

## 下一步

可以在该 V1.29 门禁基础上继续日产途达链路。若 reference #1 再出现 10 项只采 1 项的状态，应停止在 `S15_BLOCKED_REPAIR_DETAILS_INCOMPLETE` 或进入明确人工复核，而不是继续生成可信 78 分。
