# S13 历史修复重复 0 解析补丁报告

## 结论

最终状态：`PATCH_ONLY_S13_HISTORY_REPAIR_DUPLICATE_ZERO_PARSER_DONE`

本轮只修改第二段脚本中的 S13 历史修复次数解析逻辑：

- 修改文件：`scripts/runtime_s10_to_s16_mainline.py`
- 未修改第一段脚本
- 未修改 S10 同价排序 / canonical reference order
- 未修改 S11/S12 已验证逻辑
- 未修改 S14 采集逻辑
- 未修改 pricing / config / 页面契约文档

## 修复内容

旧逻辑使用去重后的 `visible_texts` 解析历史修复次数，导致重复数字节点被丢弃。

新逻辑改为：

1. 使用原始 XML `nodes` 顺序解析。
2. 保留重复文本节点，尤其是重复 `0`。
3. 基于 `bounds` 和同一行邻近关系绑定：
   - 区域标题，如 `驾驶侧深度检测：`
   - `历史修复`
   - 其右侧同一行纯数字节点
4. 强排除 `检测通过36`、`检测通过10` 等检测通过摘要数字。
5. count 不确定时输出 `S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED`，不默认 0，不默认 36。

## 离线 XML 回放验证

回放 XML：

`artifacts/debug/s13_region_驾驶侧_20260509_133419.xml`

验证结果：

- raw_nodes_used: `true`
- visible_texts_dedup_used: `false`
- duplicate_zero_preserved: `true`
- region: `驾驶侧`
- history_repair_label_bounds: `[533, 1852, 718, 1930]`
- bound_count_text: `0`
- bound_count_bounds: `[715, 1852, 754, 1930]`
- excluded_numbers: `驾驶侧：检测通过36`
- excluded_reason: `detection_pass_summary_not_history_repair_count`
- 离线回放结果：`OFFLINE_REPLAY_OK`

## 实机验证结果

目标 fingerprint：

`大众|桑塔纳|2021款|1.5L 自动风尚版|白|2021.05`

执行结果：

- 第一段重新恢复到 `S10_READY`
- 第二段重采 `reference_index=3`
- 第 3 辆仍按 canonical order 选择：
  - `4.03万`
  - `2021年 | 11.74万公里 | 唐山`
  - `selected_by=canonical_reference_order`
- S13 解析结果：
  - 驾驶侧: `0`
  - 车尾: `0`
  - 副驾驶: `6`
- 驾驶侧未再误读 `检测通过36`
- 车尾未再误读 `检测通过10`
- 副驾驶 count > 0 后按原规则进入 S14
- S14_COLLECT_DONE: `true`
- 第 3 辆进入 S15 并完成评分：
  - reference_3_score: `83.5`
  - target_score: `91.0`
  - reference_score_gte_target_score: `false`
- 当前状态：`CONTINUE_NEXT_REFERENCE`
- next_reference_index: `4`

## 关键证据

- 驾驶侧 XML: `artifacts/debug/s13_region_驾驶侧_20260509_141343.xml`
- 车尾 XML: `artifacts/debug/s13_region_车尾_20260509_141348.xml`
- 副驾驶 XML: `artifacts/debug/s13_region_副驾驶_20260509_141353.xml`
- S14 入口 XML: `artifacts/debug/s13_to_s14_副驾驶_20260509_141359.xml`
- 第二段日志: `output/santana_s13_duplicate_zero_second_20260509_141255.log`

## 结果文件

- `output/result_s10_to_s16.json`: 合法
- `output/result.json`: 合法
- raw XML 大块字段: 无
- 旧福克斯 / 丰田 / MINI 污染: 未发现

