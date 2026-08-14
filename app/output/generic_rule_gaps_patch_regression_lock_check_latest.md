# Generic Rule Gaps Patch Regression Lock Check

Status: GENERIC_RULE_GAPS_PATCH_REGRESSION_LOCK_CHECK_PASSED

Mode: read-only. No code/config/pricing/doc changes. No real-device run. No `result.json` or baseline overwrite.

## Scope Guard
- code_modified: False
- config_modified: False
- pricing_modified: False
- page_contract_docx_modified: False
- real_device_run: False
- result_json_overwritten: False
- baseline_files_overwritten: False
- lumin_processed: False
- prado_s10_single_card_processed: False
- new_rules_added: False

## 1. 补漆标准化回归

| Input | Actual | Passed |
| --- | --- | --- |
| 左后门补漆 | [('左后门', '喷漆')] | True |
| 左后叶补漆 | [('左后翼子板', '喷漆')] | True |
| 右后叶车身拉花下面有几口拳头大小补漆 | [('右后翼子板', '喷漆')] | True |
| 左后门，左后叶，右后叶车身拉花下面有几口拳头大小补漆 | [('左后门', '喷漆'), ('左后翼子板', '喷漆'), ('右后翼子板', '喷漆')] | True |
| 原版原漆 | [] | True |
| 左后门补漆，右后门钣金 | [('左后门', '喷漆'), ('右后门', '钣金')] | True |

结论：补漆 / 漆面修复 仍统一为 喷漆；未升级为钣金、更换、事故或结构风险。

## 2. 竞争力系数 V1.2.1 回归
- sample_shortage_deducts_minus_0_02: True
- price_distribution_record_only_when_trisame_lte_2: True
- water_risk_grouped_not_keyword_stacked: True
- strong_risk_score_gap_cap_minus_0_01: True
- liquidity_pressure_cap_not_below_minus_0_02: True
- city_comparability_record_only: True
- obd_note_only: True
- manual_review_not_caused_by_obd: True
- tuang_coefficient_0_91: True

途昂直接验证：
- coefficient: 0.91
- listing: 99000
- service_fee: 3500
- payout: 95500
- purchase: 84648
- manual_review_reasons: ['SAMPLE_SHORTAGE_MANUAL_REVIEW', 'TARGET_WATER_INGRESS_RISK_REVIEW']
- notes: ['OBD_NOT_READ_NOTE']

确认：OBD 未读取只输出 OBD_NOT_READ_NOTE，不扣分、不触发 TARGET_OBD_NOT_READ_REVIEW。

## 3. 历史样本离线回放

| Sample | Coefficient | Listing | Fee | Payout | Purchase | Passed |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| focus_2017 | 0.98 | 25700 | 2500 | 23200 | 19200 | True |
| toyota_yaris_2015 | 1.0 | 27800 | 2500 | 25300 | 21300 | True |
| santana_2021 | 0.975 | 40600 | 2500 | 38100 | 33472 | True |
| tuang_2017 | 0.91 | 99000 | 3500 | 95500 | 84648 | True |

- 致炫保持 1.00。
- 途昂为 0.91，并处于 0.91-0.92 业务复核区间。
- 未使用非三同 / 更多车源 / 全国淘车 / 推荐车源价格。
- 未调用 AI 模型。
- 服务费仍按目标挂牌价重新计算。

## 4. 代码残留检查

- 活动定价代码 `×95%` 回款价残留: false
- `TARGET_OBD_NOT_READ_REVIEW` 残留: false
- `0.95` 仅见于 `src/guazi_app_data_system/issue_classifier.py` 置信度字段，不属于定价。
- `补漆` / `漆面修复` 标准化路径存在于 pricing、fields、task parser、data_collection。

## 5. 文档同步差异

- V1.11 主流程 DOCX found: True
- competition V1.2.1 DOCX found: True
- service fee pricing DOCX found: True
- 建议后续将补漆/漆面修复=>喷漆写入主流程或打分规则文档。
- 建议后续标注竞争力系数 V1.2.1 已落代码。
- 如继续冻结新版本，可生成 V1.12 主流程 DOCX；本轮未修改文档。
- 定价逻辑备份服务费阶梯规则已存在；本轮无需修改。

本轮只输出建议，未修改任何 DOCX。

Final status: GENERIC_RULE_GAPS_PATCH_REGRESSION_LOCK_CHECK_PASSED
