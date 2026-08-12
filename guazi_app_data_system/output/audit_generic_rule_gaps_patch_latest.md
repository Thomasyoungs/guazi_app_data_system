# Audit Generic Rule Gaps Patch

Status: AUDIT_GENERIC_RULE_GAPS_PATCHED

Mode: patch only, offline validation only. No real-device run. No `result.json` overwrite. No baseline file overwrite.

## Scope

This patch intentionally excludes:

- Lumin S04 series search /新能源 tab / alphabet index / alias handling.
- S07 1-year hidden tick behavior.
- Prado S10 single-card / split-node recognition.
- S10/S11/S12/S13/S14 page collection flow changes.

Modified files:

- `src/guazi_app_data_system/pricing.py`
- `config/fields.yaml`
- `scripts/parse_target_vehicle_task.py`
- `src/guazi_app_data_system/data_collection.py`

## A. 补漆标准化

Implemented rule:

- `补漆` is normalized to `喷漆`.
- `漆面修复` is normalized to `喷漆`.
- `补漆` does not imply `钣金`, `更换`,事故, or结构风险.
- `左后叶` / `右后叶` / `左前叶` / `右前叶` normalize to the corresponding翼子板.
- Unclear part text is recorded as review note and is not silently scored as a concrete part.
- `原版原漆` produces no body repair deduction item.

Offline examples:

| Input | Parsed Result |
| --- | --- |
| 左后门补漆 | 左后门 / 喷漆 |
| 左后叶补漆 | 左后翼子板 / 喷漆 |
| 右后叶车身拉花下面有几口拳头大小补漆 | 右后翼子板 / 喷漆 |
| 左后门，左后叶，右后叶车身拉花下面有几口拳头大小补漆 | 左后门 / 喷漆; 左后翼子板 / 喷漆; 右后翼子板 / 喷漆 |
| 原版原漆 | no 喷漆 / no 钣金 / no 更换 |
| 左后门补漆，右后门钣金 | 左后门 / 喷漆; 右后门 / 钣金 |

Result: condition_standardization_tests=PASS

## B. Competition Coefficient V1.2.1

V1.2.1 synchronization completed in pricing code:

- `trisame_count <= 2`: sample reliability deducts `-0.02`; price distribution is record-only and does not deduct again.
- Water ingress risk is grouped by risk level, not stacked keyword by keyword.
- Strong target condition risk caps score-gap deduction at `-0.01`.
- `price_band_adjustment + model_liquidity_adjustment + fuel_cost_pressure_adjustment` is capped at `-0.02` with `LIQUIDITY_PRESSURE_CAP_APPLIED`.
- City comparability is record-only and does not deduct.
- OBD未读取 remains note-only: `OBD_NOT_READ_NOTE`; it does not deduct and does not trigger `TARGET_OBD_NOT_READ_REVIEW`.
- Non-trisame prices remain excluded from coefficient calculation.
- AI model is not used.

Direct Tuang V1.2.1 validation:

- reference_price=108900
- target_score=91.0
- selected_reference_score=96.0
- trisame_count=2
- obvious water-ingress risk grouped
- OBD未读取 note-only
- competition_coefficient=0.91
- target_guazi_listing_price_yuan=99000
- guazi_service_fee_yuan=3500
- guazi_net_payout_yuan=95500
- suggested_purchase_price_yuan=84648

Result: tuang_v1_2_1_pricing_test=PASS

## Historical Replay Summary

Read-only replay only. Historical baseline files were not overwritten.

| Sample | Coefficient | Listing | Service Fee | Payout | Purchase Price |
| --- | ---: | ---: | ---: | ---: | ---: |
| Focus 2017 | 0.98 | 25700 | 2500 | 23200 | 19200 |
| Toyota YARiS L | 1.00 | 27800 | 2500 | 25300 | 21300 |
| Santana 2021 | 0.975 | 40600 | 2500 | 38100 | 33472 |
| Tuang 2017 | 0.91 | 99000 | 3500 | 95500 | 84648 |

Tuang is no longer compressed to `0.90`; V1.2.1 result is in the expected `0.91-0.92` review band.

## Old 95% Rule Check

Active pricing code no longer contains `瓜子定价 × 95%` payout logic.

Remaining `0.95` occurrences in active source are only confidence values in `src/guazi_app_data_system/issue_classifier.py`; they are not pricing logic.

Archived baseline snapshots may still contain old formulas by design and were not modified.

## Validation

- `python -m py_compile src/guazi_app_data_system/pricing.py src/guazi_app_data_system/data_collection.py scripts/parse_target_vehicle_task.py`: PASS
- `python -m unittest tests.test_fields_contract tests.test_target_vehicle_task_parser`: PASS, 18 tests
- Condition standardization offline tests: PASS
- V1.2.1 Tuang pricing offline test: PASS
- Real-device run: no

`pytest` is not available in the bundled Python environment, so the existing standard-library unittest suite was used.

## Notes

- This patch does not modify page contracts or DOCX files.
- DOCX sync is recommended later because code/config now explicitly include `补漆 => 喷漆` and V1.2.1防重复减分口径.
- No `result.json` or baseline reports were overwritten.

Final status: AUDIT_GENERIC_RULE_GAPS_PATCHED
