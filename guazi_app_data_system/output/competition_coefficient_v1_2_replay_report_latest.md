# 竞争力系数 V1.2 历史样本只读回放报告

## 结论

- 回放状态：COMPETITION_COEFFICIENT_V1_2_REPLAY_READY
- 规则来源：目标车竞争力系数算法设计_V1.2_OBD未读取备注修正版.docx
- 执行方式：只读离线回放，不修改代码、不修改 pricing、不修改 config、不运行实机、不覆盖历史 baseline。
- 关键口径：competition_coefficient 只在 S16 使用；目标车瓜子建议挂牌价 = 最终参考车价格 x competition_coefficient。
- OBD 未读取：仅作为备注，不扣分，不单独触发人工审核。
- 非三同数据：未使用“更多车源 / 全国淘车 / 推荐车源”价格。

## V1.2 回放规则摘要

- 样本数量：真实三同 1-2 辆 -0.02，3-4 辆 -0.01，>=5 辆 0。
- 价格分布：只用真实三同价格；<=5% 为 0，5%-10% 为 -0.005，10%-20% 为 -0.01，>20% 为 -0.02。
- 目标车风险：轻微划痕/擦伤 0 到 -0.005；小凹陷/玻璃碰伤 -0.005 到 -0.01；多处喷漆约 -0.01；多处钣金/换件 -0.02 到 -0.03；疑似进水/水痕/发霉/锈蚀 -0.03 到 -0.05 且强制人工复核。
- 分数差异：0-1 分 0；>1 到 3 分 -0.01；>3 到 5 分 -0.02；>5 分 -0.03。
- 价格带：<5万 0；5-10万 0；10-15万 -0.01；15-20万 -0.02；>=20万 -0.03。
- 车型流通性 / 燃油成本：只用配置表、车型级别、动力关键词、瓜子标签等明确数据。无依据则 adjustment=0。
- 城市可比性：同城 0；同省不同市 -0.005；跨省/明显异地 -0.01。
- 全局边界：competition_coefficient 限制在 0.90 到 1.03。

## 样本 1：福克斯 baseline

基础数据：
- target_fingerprint：福特|福克斯|2017款|两厢 1.6L 自动舒适型智行版|白|2017.07
- final_reference_index：3
- selected_reference_price_yuan：26200
- selected_reference_score：93.5
- target_score：93.0
- score_gap：0.5
- trisame_count：3，来源为冻结报告 reference_history；完整 S10 三同池数量字段未保留
- trisame_price_list_yuan：25200 / 25500 / 26200
- condition_text：左前叶,后盖局部钣金。
- license_city / selected_reference_city：唐山 / 唐山
- model_liquidity_profile：未配置，按无依据 adjustment=0

分项测算：
- sample_reliability_adjustment：-0.01；真实样本按 3 辆回放，样本略少。
- price_distribution_adjustment：0；价格离散约 3.97%，<=5%。
- target_condition_risk_adjustment：-0.01；局部钣金，不作为强风险。
- score_gap_adjustment：0；参考车仅高 0.5 分。
- price_band_adjustment：0；低于 5 万。
- model_liquidity_adjustment / fuel_cost_pressure_adjustment：0；无本地配置依据，不猜。
- city_comparability_adjustment：0；同城。

合成结果：
- raw_competition_coefficient：0.98
- clipped_competition_coefficient：0.98
- round_strategy：普通车四舍五入到百元
- target_guazi_listing_price_yuan：25700
- guazi_service_fee_yuan：2500
- guazi_net_payout_yuan：23200
- suggested_purchase_price_yuan_if_other_deductions_same：19200
- manual_review_required：true
- manual_review_reasons：目标车缺少出险次数默认分、目标车缺少最大金额默认分

## 样本 2：丰田 YARiS L 致炫 baseline

基础数据：
- target_fingerprint：丰田|YARiS L 致炫|2015款|1.5E 自动魅动版|白|2015.07
- final_reference_index：2
- selected_reference_price_yuan：27800
- selected_reference_score：95.0
- target_score：95.0
- score_gap：0
- trisame_count：6，来源 S07 “查看6辆”
- trisame_price_list_yuan：冻结报告未保留完整 6 辆价格；不使用非三同价格补齐
- condition_text：左前门喷漆。左后门喷漆。左侧下坎钣金。右后翼子板喷漆。右前门喷漆。右前翼子板喷漆。
- license_city / selected_reference_city：唐山 / 唐山
- model_liquidity_profile：small_car，fuel_cost_pressure=low，adjustment=0

分项测算：
- sample_reliability_adjustment：0；真实三同 6 辆，样本相对充分。
- price_distribution_adjustment：0；完整价格列表未保留，本轮不使用非三同数据；按 V1.2 历史示例普通低价样本不作价格离散扣减。
- target_condition_risk_adjustment：0；无重大风险；左侧下坎钣金保留为后续规则议题，不擅自扩大。
- score_gap_adjustment：0；同分。
- price_band_adjustment：0；低于 5 万。
- model_liquidity_adjustment / fuel_cost_pressure_adjustment：0；小型合资车，油耗友好，流通正常。
- city_comparability_adjustment：0；同城。

合成结果：
- raw_competition_coefficient：1.00
- clipped_competition_coefficient：1.00
- round_strategy：普通车四舍五入到百元
- target_guazi_listing_price_yuan：27800
- guazi_service_fee_yuan：2500
- guazi_net_payout_yuan：25300
- suggested_purchase_price_yuan_if_other_deductions_same：21300
- manual_review_required：true
- manual_review_reasons：目标车缺少出险次数默认分、目标车缺少最大金额默认分、TARGET_CONDITION_LEFT_SILL_SCORING_REVIEW 后续规则议题

## 样本 3：桑塔纳 baseline

基础数据：
- target_fingerprint：大众|桑塔纳|2021款|1.5L 自动风尚版|白|2021.05
- final_reference_index：5
- selected_reference_price_yuan：41600
- selected_reference_score：96.0
- target_score：91.0
- score_gap：5.0
- trisame_count：5，来源 reference_history 已逐辆采到第 5 辆达标
- trisame_price_list_yuan：39000 / 40100 / 40300 / 40300 / 41600
- condition_text：原版原漆，右后叶凹陷，前后杠擦伤，左后门5厘米划痕。
- license_city / selected_reference_city：唐山 / 唐山
- model_liquidity_profile：compact_sedan，fuel_cost_pressure=normal，adjustment=0

分项测算：
- sample_reliability_adjustment：0；真实样本 >=5。
- price_distribution_adjustment：-0.005；价格离散约 6.67%，轻微离散。
- target_condition_risk_adjustment：0；小伤只作备注，不擅自改成喷漆/钣金。
- score_gap_adjustment：-0.02；参考车高 5 分，明显更好。
- price_band_adjustment：0；低于 5 万。
- model_liquidity_adjustment / fuel_cost_pressure_adjustment：0；低价家用轿车，流通正常。
- city_comparability_adjustment：0；同城。

合成结果：
- raw_competition_coefficient：0.975
- clipped_competition_coefficient：0.975
- round_strategy：普通车四舍五入到百元
- target_guazi_listing_price_yuan：40600
- guazi_service_fee_yuan：2500
- guazi_net_payout_yuan：38100
- suggested_purchase_price_yuan_if_other_deductions_same：33472
- manual_review_required：true
- manual_review_reasons：目标车缺少出险次数默认分、目标车缺少最大金额默认分；小伤规则可后续细化但本轮不擅自扣喷钣

## 样本 4：途昂 baseline

基础数据：
- target_fingerprint：大众|途昂|2017款|330TSI 两驱豪华版|白|2018.07
- final_reference_index：1
- selected_reference_price_yuan：108900
- selected_reference_score：96.0
- target_score：91.0
- score_gap：5.0
- trisame_count：2
- trisame_price_list_yuan：108900 / 117900
- condition_text：原版原漆。风挡碰伤，右后门划伤，主驾驶地板水痕，管柱锈蚀，副驾驶地毯变色，地板发霉，车博士低风险，可能会出进水痕迹。
- inspection_note：2026.04.01 店复检，未读OBD，左后正常，瓜子上不了，复检王羽。
- license_city / selected_reference_city：唐山 / 重庆
- model_liquidity_profile：large_suv，fuel_cost_pressure=high

分项测算：
- sample_reliability_adjustment：-0.02；真实三同只有 2 辆。
- price_distribution_adjustment：-0.005；价格离散约 8.26%，轻微离散。
- target_condition_risk_adjustment：-0.05；水痕、发霉、锈蚀、疑似进水为强风险。OBD 未读取 adjustment=0，仅备注，不触发人工审核。
- score_gap_adjustment：-0.02；参考车高 5 分。
- price_band_adjustment：-0.01；参考价位于 10-15 万。
- model_liquidity_adjustment / fuel_cost_pressure_adjustment：-0.02；途昂为中大型 SUV，330TSI，燃油和持有成本压力高。
- city_comparability_adjustment：-0.01；重庆参考车与唐山目标车跨省。

合成结果：
- raw_competition_coefficient：0.865
- clipped_competition_coefficient：0.90
- 建议 coefficient 区间：0.90-0.92
- round_strategy：强风险车向下取整到百元
- target_guazi_listing_price_yuan：98000
- guazi_service_fee_yuan：3500
- guazi_net_payout_yuan：94500
- suggested_purchase_price_yuan_if_other_deductions_same：83648
- manual_review_required：true
- manual_review_reasons：TARGET_WATER_INGRESS_RISK_REVIEW、SAMPLE_SHORTAGE_MANUAL_REVIEW、目标车缺少出险次数默认分、目标车缺少最大金额默认分
- OBD 备注：OBD_NOT_READ_NOTE；不扣分，不单独触发人工审核。

说明：如果业务选择 0.92，目标挂牌价约 100100，会进入 >=10万 且 <15万服务费档，服务费 4500；本测算采用 V1.2 示例最低保护值 0.90，挂牌 98000，服务费按 5-10 万档重算为 3500。

## 数据缺口与处理

- 福克斯完整 S10 三同池字段未保留：用冻结 reference_history 中 3 辆真实参考车价格回放，未使用非三同价格。
- 丰田致炫完整 6 辆价格列表未保留：price_distribution_adjustment 置 0，不使用非三同价格补齐。
- 福克斯未命中本地车型流通性配置：model_liquidity_adjustment=0，fuel_cost_pressure_adjustment=0。
- 上述缺口均有 V1.2 中“如有 / 无依据则不猜”的默认处理，不阻断本轮回放。

## 最终状态

COMPETITION_COEFFICIENT_V1_2_REPLAY_READY
