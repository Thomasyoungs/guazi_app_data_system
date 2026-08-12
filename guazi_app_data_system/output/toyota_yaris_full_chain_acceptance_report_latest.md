# 丰田 YARiS L 致炫完整闭环验收报告

- 验收状态：`TOYOTA_YARIS_FULL_CHAIN_ACCEPTANCE_PASSED`
- 目标 fingerprint：`丰田|YARiS L 致炫|2015款|1.5E 自动魅动版|白|2015.07`
- 第一段：`S10_READY`
- 第二段：`FULL_CHAIN_PRICED_DONE`
- 最终参考车：第 `2` 辆
- 建议挂牌价：`27800`
- 建议收车价：`22410`

## 一、完整链路验收结论

- 第一段成功到 `S10_READY`。
- 第二段成功启动，并完成 S11/S12/S13/S14/S15。
- S16 定价完成，S17 模拟 payload 已输出。
- 最终状态为 `FULL_CHAIN_PRICED_DONE`。

## 二、S07 车龄隐藏刻度验收

- `target_age=11`。
- 可见刻度：`0 / 2 / 4 / 6 / 8 / 10 / 不限`。
- `x8=821`，`x10=937`，`one_year_step=58.0`，`target_age_x=995`。
- 11 年不是可见刻度文本，而是 10 右侧第 1 个隐藏节点；12 年是 10 右侧第 2 个隐藏节点。
- 本轮通过 fresh 文本 `11-11年` 验证后才置 `AGE_FILTER_DONE=true`。
- 底部按钮为 `查看6辆`，`S07_FILTER_DONE=true`。

## 三、参考车采集链路

规则：按 S10 价格从低到高的 `reference_index` 逐辆采集，达标即停；不是固定采 2 辆或固定采 3 辆。

### 第 1 辆
- 价格：`2.53万`
- 信息：`2015年 | 11.49万公里 | 唐山`
- 过户：`0`
- 理赔次数：`2`
- 最大金额：`3000`
- 历史修复：`{"驾驶侧":2}`
- S14 维修项：左前翼子板喷漆
- reference_score：`91.5`；target_score：`95`
- 是否淘汰：`否`
- 是否达标：`否`
- 决策：字段完整且未淘汰，但参考车分低于目标车分，按逐辆采集规则继续下一辆。

### 第 2 辆
- 价格：`2.78万`
- 信息：`2015年 | 7.72万公里 | 唐山`
- 过户：`1`
- 理赔次数：`1`
- 最大金额：`3000`
- 历史修复：`{"驾驶侧":2}`
- S14 维修项：左后门喷漆
- reference_score：`95`；target_score：`95`
- 是否淘汰：`否`
- 是否达标：`是`
- 决策：字段完整、未淘汰且分数达标，立即选为最终参考车并停止继续采集。

## 四、最终定价结果

- target_score：`95`
- final_reference_index：`2`
- selected_reference_score：`95`
- selected_reference_price：`2.78万`
- suggested_listing_price：`27800`
- suggested_purchase_price：`22410`
- pricing_basis：以第 2 辆达标参考车 `2.78万` 为瓜子参考价，结合目标车/参考车分数后输出挂牌价和收车价。
- S17 payload 摘要：已输出模拟写回，包含最终参考车、参考价、目标分、参考车分和人工审核提示。

## 五、人工审核 / 风险提示

- 目标车缺少出险次数，已采用默认分。
- 目标车缺少最大金额，已采用默认分。
- 三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。
- 左侧下坎钣金当前未单独写成人工审核原因；本报告只记录现有 scoring 处理结果，不擅自改规则。
- 建议后续单独开规则议题：`TARGET_CONDITION_LEFT_SILL_SCORING_REVIEW`。

## 六、关键能力复用验收

- APP_FORCE_RESTART
- 第一段 S01-S10
- S07 颜色筛选
- S07 车龄隐藏刻度 11 年
- S09 价格从低到高排序
- S10_READY 门禁
- reference_index 逐辆采集
- 第 N 辆 S10 车卡唯一绑定
- 逐辆采集，达标即停，不固定采 3 辆
- S11 顶部车辆图片区识别
- 查看完整报告完整可见 + 安全区点击
- S11_TO_S12 稳定等待
- S11_TO_S12 context 下 S12 优先于 S14
- S13 历史修复判断
- S14 具体修复项采集
- S15 单车评分判断
- S16 定价
- S17 payload 模拟输出
- reference_history 保留
- 旧目标污染防护
- raw XML 不写入 result JSON

## 七、慢动作诊断

- `S14_COLLECT` 约 77s：不影响正确性；不建议本轮立即优化，后续可单独做性能议题。
- `S14_HORIZONTAL_SWIPE` 单次约 5-6s：不影响正确性；建议保留当前语义确认，后续单独优化 fresh 成本。
- `S10_TO_S11` 首次 XML fresh 约 9-10s：不影响正确性；属于 WebView/uiautomator fresh 成本，后续性能专项处理。
- `S14_RETURN_TO_S10` 约 8.2s：不影响正确性；当前返回路径已确认安全，后续不优先优化。

## 八、最终验收状态

`TOYOTA_YARIS_FULL_CHAIN_ACCEPTANCE_PASSED`
