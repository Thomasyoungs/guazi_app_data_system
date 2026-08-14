# 瓜子服务费阶梯定价补丁影响报告

状态：**GUAZI_SERVICE_FEE_TIER_PRICING_PATCHED**

## 规则变更

已废弃旧公式：瓜子回款价 = 瓜子定价 × 95%。

已启用新公式：瓜子回款价 = 瓜子定价 - 瓜子服务费。

服务费档位按从高到低匹配，边界按 >=：

- 瓜子定价 >= 200000：扣 8000
- 瓜子定价 >= 150000 且 < 200000：扣 6000
- 瓜子定价 >= 100000 且 < 150000：扣 4500
- 瓜子定价 >= 50000 且 < 100000：扣 3500
- 瓜子定价 < 50000：扣 2500

## 修改文件清单

- src/guazi_app_data_system/pricing.py
- config/fields.yaml
- output/guazi_service_fee_pricing_patch_report_latest.md
- output/guazi_service_fee_pricing_patch_report.json

## 旧公式位置

- src/guazi_app_data_system/pricing.py：round(guazi_price_yuan * float(pricing["guazi_return_rate"]))，已替换
- config/fields.yaml："guazi_return_rate": 0.95
- output/baselines/**/pricing.py：archived baseline copies contain old formula by design，未修改历史归档

## 新函数位置

- src/guazi_app_data_system/pricing.py：calc_guazi_service_fee(guazi_price_yuan, pricing_config=None)

## 离线样例验证

- guazi_price=26200，service_fee=2500，guazi_net_payout=23700，passed=true
- guazi_price=27800，service_fee=2500，guazi_net_payout=25300，passed=true
- guazi_price=50000，service_fee=3500，guazi_net_payout=46500，passed=true
- guazi_price=100000，service_fee=4500，guazi_net_payout=95500，passed=true
- guazi_price=150000，service_fee=6000，guazi_net_payout=144000，passed=true
- guazi_price=200000，service_fee=8000，guazi_net_payout=192000，passed=true

补充 calculate_pricing 样例：guazi_price=27800 时，service_fee=2500，guazi_net_payout=25300，suggested_acquisition_price=21300。

## 旧 0.95 回款价逻辑残留

活动代码与活动 pricing 配置中未发现 0.95 回款价逻辑残留。issue_classifier.py 中的 0.95 是置信度，不属于定价；历史 baseline 归档保留旧公式，未覆盖。

## 历史 baseline 影响测算（只报告，不覆盖旧结果）

- 福克斯 baseline：参考价 26200，旧 95% 回款价 24890，新服务费回款价 23700，回款价差异 -1190；若其他扣减不变，建议收车价应相应下调 1190。
- 丰田致炫 baseline：参考价 27800，旧 95% 回款价 26410，新服务费回款价 25300，回款价差异 -1110；若其他扣减不变，建议收车价应相应下调 1110。

## 验证

- py_compile：通过
- pricing 样例：通过
- config/fields.yaml JSON：通过
- 未运行实机
- 未覆盖 result.json
- 未覆盖福克斯 / 丰田 baseline 原始报告

最终状态：**GUAZI_SERVICE_FEE_TIER_PRICING_PATCHED**
