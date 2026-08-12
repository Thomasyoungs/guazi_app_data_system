# CONTINUE_NEXT_REFERENCE 诊断

最终分类：`CONTINUE_NEXT_REFERENCE_DUE_TO_LOW_SCORE`

## 结论

当前 `CONTINUE_NEXT_REFERENCE` 是预期规则触发，不是 S11 / S12 / S13 / S14 采集失败，也不是旧 MINI 结果污染。

直接触发原因：

- 当前参考车采集完成，`S14_COLLECT_DONE=true`
- 当前参考车未被淘汰，`reference_disqualified=false`
- 目标车分：`93.0`
- 当前参考车分：`90.5`
- 分差：`reference_score - target_score = -2.5`
- `scripts/runtime_s10_to_s16_mainline.py` 的 S15 分支只有在 `selected_score.score >= target_score.score` 时才进入 S16；当前低于目标车分，因此返回 S10 并写入 `CONTINUE_NEXT_REFERENCE`

## 决策证据

- `output/result_s10_to_s16.json`
- `output/result.json`
- `logs/issues.jsonl`
- `scripts/runtime_s10_to_s16_mainline.py:4789`
- `scripts/runtime_s10_to_s16_mainline.py:5009`
- `src/guazi_app_data_system/pricing.py:266`

`logs/issues.jsonl` 本轮记录：

`CONTINUE_NEXT_REFERENCE` / `S15` / `Current reference score is below target score; continue from S10.`

## 当前参考车采集完整性

- 参考车 index：`1`
- 标题：`福特 福克斯 2017款 两厢 1.6L 自动舒适型智行版`
- 列表价：`2.52万`
- 年份 / 里程：`2017年 / 7.81万公里`
- 过户次数：`2`
- 理赔次数：`3`
- 最大理赔金额：`2000.0`
- 四区历史修复：`驾驶侧=9`
- S14 维修项：`左前门 / 钣金`
- 缺失字段：无
- 特殊结构风险：无
- 是否淘汰：否

## 分数

目标车：

- 总分：`93.0`
- body_score：`69.0`
- mileage_score：`10.0`
- transfer_score：`7.0`
- accident_score：`4.0`
- max_amount_score：`3.0`

当前参考车：

- 总分：`90.5`
- body_score：`69.0`
- mileage_score：`10.0`
- transfer_score：`5.5`
- accident_score：`2.0`
- max_amount_score：`4.0`

判断：`reference_score >= target_score` 不成立。

## 样本数量

- 系统最小参考样本提示阈值：`3`
- 当前已采集参考车：`1`
- 当前有效参考车：`1`
- 当前达到目标分的参考车：`0`
- 仍缺最小样本数：`2`
- 第一段同源车卡数量：`10`

因此当前同时存在样本不足的人工审核提示，但本轮 `CONTINUE_NEXT_REFERENCE` 的直接触发规则是“当前参考车分低于目标车”。

## 是否应该继续下一辆

应继续采下一辆参考车。

下一辆候选：

- next_reference_index：`2`
- 标题：`福特 福克斯 2017款 两厢 1.6L 自动舒适型智行版`
- 价格：`2.55万`
- 年份 / 里程 / 城市：`2017年 | 12.01万公里 | 唐山`

当前结果里已有返回 S10 的快照：

- `artifacts/debug/s14_return_to_s10_attempt_2_20260507_191321.xml`
- `returned_list_source_verified=true`

如果要继续下一辆，仍应先做 live fresh 校验当前页面确实是可靠 S10；若不在 S10，则先恢复到 S10_READY。

## 是否应进入人工审核或最终定价

当前不应直接进入最终定价。

阻断原因：

`selected_score=90.5 < target_score=93.0`

如果强行定价，会触发人工审核原因：

- 目标车缺少出险次数，已采用默认分。
- 目标车缺少最大金额，已采用默认分。
- 三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。
- 所有参考车总分均低于目标车，已选择最接近车辆作为临时参考车。

## 最终状态

`DIAGNOSE_CONTINUE_NEXT_REFERENCE_DECISION_DONE`
