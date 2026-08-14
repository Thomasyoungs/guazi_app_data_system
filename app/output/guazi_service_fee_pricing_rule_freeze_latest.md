# BASELINE_GUAZI_SERVICE_FEE_TIER_PRICING_202605

## 冻结结论

冻结状态：**GUAZI_SERVICE_FEE_PRICING_RULE_FREEZE_READY**

本轮只读生成定价规则冻结包：未修改代码、config、pricing、页面契约文档，未运行实机，未覆盖 result.json，未覆盖福克斯 / 丰田 baseline 原始报告。

## 冻结范围

- OK `src/guazi_app_data_system/pricing.py` (14120 bytes, sha256=1b62d2646ebc...)
- OK `config/fields.yaml` (4870 bytes, sha256=7f3e5add5261...)
- OK `output/guazi_service_fee_pricing_patch_report_latest.md` (2704 bytes, sha256=3bf317c82a66...)
- OK `output/guazi_service_fee_pricing_patch_report.json` (4390 bytes, sha256=405fd7bc327f...)
- OK `output/guazi_service_fee_pricing_regression_check_latest.md` (2348 bytes, sha256=619257b10174...)
- OK `output/guazi_service_fee_pricing_regression_check.json` (5166 bytes, sha256=77f875922f39...)
- OK `C:\Users\lzc93\Desktop\定价\定价逻辑备份_服务费阶梯修正版.docx` (10734 bytes, sha256=2503644a4bb6...)

## 规则说明

旧规则：瓜子回款价 = 瓜子定价 × 95%，已废弃。

新规则：瓜子回款价 = 瓜子定价 - 瓜子服务费。

边界规则：5万、10万、15万、20万均按 >= 进入更高档位；档位必须从高到低匹配。

兼容字段：guazi_return_price_yuan 为兼容字段，值等于 guazi_net_payout_yuan。

服务费阶梯：

- guazi_price_yuan >= 200000：8000
- guazi_price_yuan >= 150000 and guazi_price_yuan < 200000：6000
- guazi_price_yuan >= 100000 and guazi_price_yuan < 150000：4500
- guazi_price_yuan >= 50000 and guazi_price_yuan < 100000：3500
- guazi_price_yuan < 50000：2500

## 离线样例确认

- 26200 -> fee 2500 -> payout 23700
- 27800 -> fee 2500 -> payout 25300
- 49999 -> fee 2500 -> payout 47499
- 50000 -> fee 3500 -> payout 46500
- 99999 -> fee 3500 -> payout 96499
- 100000 -> fee 4500 -> payout 95500
- 149999 -> fee 4500 -> payout 145499
- 150000 -> fee 6000 -> payout 144000
- 199999 -> fee 6000 -> payout 193999
- 200000 -> fee 8000 -> payout 192000

## 历史 baseline 影响测算（只记录，不覆盖旧结果）

- 福克斯：旧收车价 20890，按新规则且其他扣减不变为 19700，差异 -1190。
- 丰田致炫：旧收车价 22410，按新规则且其他扣减不变为 21300，差异 -1110。

## 未来执行口径

- 以后所有新目标车进入 S16 定价时，统一使用服务费阶梯规则。
- 不再使用 ×95%。
- 历史 baseline 原始结果不覆盖。
- 如需更新历史 baseline 价格，只能单独做离线重算报告，不能覆盖原验收报告。

## 证据

- patch_report_status：GUAZI_SERVICE_FEE_TIER_PRICING_PATCHED
- regression_report_status：GUAZI_SERVICE_FEE_PRICING_REGRESSION_PASSED
- 活动定价代码无 0.95 回款价逻辑残留；剩余 0.95 仅为 issue_classifier.py 置信度，不属于定价。

## 一致性检查

- patch_status_ok: true
- regression_status_ok: true
- pricing_py_exists: true
- fields_yaml_exists: true
- patch_reports_exist: true
- regression_reports_exist: true
- service_fee_docx_exists: true
- no_old_rate_remaining_in_activity_pricing: true

最终状态：**GUAZI_SERVICE_FEE_PRICING_RULE_FREEZE_READY**
