# 瓜子服务费阶梯定价回归验收

最终状态：**GUAZI_SERVICE_FEE_PRICING_REGRESSION_PASSED**

本轮只读：未修改代码、文档、config、pricing，未运行实机，未覆盖 result.json，未覆盖福克斯 / 丰田 baseline 原始结果。

## 一、代码残留检查

活动定价代码中未发现 0.95 回款价逻辑，也未发现 guazi_return_rate。

搜索到的 0.95 仅位于 `src/guazi_app_data_system/issue_classifier.py` 的置信度字段，行号 708 / 916 / 955 / 1001 / 1300，不属于定价，不算残留。

## 二、服务费阶梯函数验收

`calc_guazi_service_fee()` 位于 `src/guazi_app_data_system/pricing.py`，服务费配置位于 `config/fields.yaml:106`。档位从高到低匹配，边界按 >= 处理。

- price < 50000：2500
- price >= 50000 and price < 100000：3500
- price >= 100000 and price < 150000：4500
- price >= 150000 and price < 200000：6000
- price >= 200000：8000

## 三、离线样例验收

- 26200 -> fee 2500 -> payout 23700，passed=true
- 27800 -> fee 2500 -> payout 25300，passed=true
- 49999 -> fee 2500 -> payout 47499，passed=true
- 50000 -> fee 3500 -> payout 46500，passed=true
- 99999 -> fee 3500 -> payout 96499，passed=true
- 100000 -> fee 4500 -> payout 95500，passed=true
- 149999 -> fee 4500 -> payout 145499，passed=true
- 150000 -> fee 6000 -> payout 144000，passed=true
- 199999 -> fee 6000 -> payout 193999，passed=true
- 200000 -> fee 8000 -> payout 192000，passed=true

## 四、历史 baseline 影响只读测算

- 福克斯：参考价 26200，旧 95% 回款价 24890，新服务费回款价 23700，回款价差异 -1190；若其他扣减不变，旧建议收车价 20890 应下调为 19700。
- 丰田致炫：参考价 27800，旧 95% 回款价 26410，新服务费回款价 25300，回款价差异 -1110；若其他扣减不变，旧建议收车价 22410 应下调为 21300。

该测算只进入报告，不覆盖历史 baseline 文件。

## 五、未来输出字段

- guazi_price_yuan
- guazi_service_fee_yuan
- guazi_net_payout_yuan
- guazi_return_price_yuan：兼容字段，等于 guazi_net_payout_yuan
- suggested_purchase_price_yuan：建议收车价字段；当前代码输出 suggested_acquisition_price_yuan，S17 payload 映射为建议收车价。

最终状态：**GUAZI_SERVICE_FEE_PRICING_REGRESSION_PASSED**
