# BASELINE_TOYOTA_YARIS_2015_FULL_CHAIN_PRICED_DONE_202605

## 冻结结论

冻结状态：**TOYOTA_YARIS_BASELINE_FREEZE_READY**

本包为只读冻结整理：未修改代码、页面契约文档、config、pricing，未运行实机，未覆盖 result.json，未覆盖福克斯 baseline 文件。

目标 fingerprint：`丰田|YARiS L 致炫|2015款|1.5E 自动魅动版|白|2015.07`

最终结果：第一段 `S10_READY`，第二段 `FULL_CHAIN_PRICED_DONE`，最终 `FULL_CHAIN_PRICED_DONE`。最终参考车为第 2 辆，价格 2.78万，信息为 2015年 | 7.72万公里 | 唐山；target_score=95.0，reference_score=95.0，建议挂牌价 27800，建议收车价 22410。

## 冻结范围

- OK `scripts/runtime_s01_to_s10_mainline.py` (304400 bytes, sha256=1edd01e568f7...)
- OK `scripts/runtime_s10_to_s16_mainline.py` (269921 bytes, sha256=b6c003bf16a7...)
- OK `output/result_s01_to_s10.json` (6629 bytes, sha256=bb58cc442f2c...)
- OK `output/result_s10_to_s16.json` (687489 bytes, sha256=7af6680e565e...)
- OK `output/result.json` (687489 bytes, sha256=7af6680e565e...)
- OK `output/toyota_yaris_full_chain_acceptance_report_latest.md` (4222 bytes, sha256=8688e34ae552...)
- OK `output/toyota_yaris_full_chain_acceptance_report.json` (28495 bytes, sha256=154247c9f869...)

关键 artifacts/debug 与 screenshots：

- OK `artifacts/screenshots/s07_age_long_press_drag_left_2_20260509_112521.png` (769647 bytes)
- OK `artifacts/debug/s07_age_long_press_drag_left_2_20260509_112521.xml` (213981 bytes)
- OK `artifacts/screenshots/s09_to_s10_20260509_112536.png` (1430413 bytes)
- OK `artifacts/debug/s09_to_s10_20260509_112536.xml` (68252 bytes)
- OK `artifacts/screenshots/s10_to_s11_pre_dump_2_20260509_113050.png` (1024394 bytes)
- OK `artifacts/debug/s10_to_s11_20260509_113051_compressed.xml` (33827 bytes)
- OK `artifacts/screenshots/s13_to_s14_驾驶侧_20260509_113136.png` (841939 bytes)
- OK `artifacts/debug/s13_to_s14_驾驶侧_20260509_113136.xml` (168201 bytes)
- OK `artifacts/screenshots/s14_return_to_s10_attempt_2_20260509_113302.png` (1429939 bytes)
- OK `artifacts/debug/s14_return_to_s10_attempt_2_20260509_113302.xml` (68251 bytes)

## 成功能力清单

1. APP_FORCE_RESTART
2. 第一段 S01-S10
3. S07 白色筛选
4. S07 车龄隐藏刻度 11 年
5. S07 查看6辆
6. S09 价格从低到高排序
7. S10_READY 门禁
8. reference_index 逐辆采集
9. 达标即停，不固定采 2 辆或 3 辆
10. S10 第 N 辆车卡唯一绑定
11. S11 顶部车辆图片区识别
12. 查看完整报告完整可见 + 安全区点击
13. S11_TO_S12 稳定等待
14. S11_TO_S12 context 下 S12 优先于 S14
15. S12 理赔次数 / 最大金额采集
16. S13 历史修复判断
17. S14 具体修复项采集
18. S15 单车评分判断
19. S16 定价
20. S17 payload 模拟输出
21. reference_history 保留
22. 旧目标污染防护
23. raw XML 不写入 result JSON

## S07 隐藏车龄刻度规则

当前页面可见刻度为：0 / 2 / 4 / 6 / 8 / 10 / 不限。隐藏精确刻度规则为：11 年 = 10 右侧第 1 个隐藏节点，12 年 = 10 右侧第 2 个隐藏节点。

本轮证据：target_age=11，x8=821，x10=937，one_year_step=58.0，target_age_x=995，verify_text=11-11年，bottom_view_result_text=查看6辆，AGE_FILTER_DONE=true。

门禁：必须通过 11-11年 / 12-12年 验证后，才允许置 AGE_FILTER_DONE=true。target_age>12 不自动映射为“不限”。

## 参考车循环验收

参考车采集规则：在 S10 可靠三同车源列表中按 reference_index 顺序逐辆采集；每辆完成后进入 S15 打分判断。字段完整、未被事故车/结构风险淘汰且 reference_score >= target_score 时，当前车立即成为最终参考车，停止继续采集并进入 S16。该流程不是固定采 2 辆或 3 辆。

第 1 辆：价格 2.53万，信息 2015年 | 11.49万公里 | 唐山，过户 0，理赔次数 2，最大金额 3000，历史修复 驾驶侧 2，S14 维修项 左前翼子板喷漆，reference_score=91.5，target_score=95.0。未淘汰，但不达标，继续第 2 辆。

第 2 辆：价格 2.78万，信息 2015年 | 7.72万公里 | 唐山，过户 1，理赔次数 1，最大金额 3000，历史修复 驾驶侧 2，S14 维修项 左后门喷漆，reference_score=95.0，target_score=95.0。未淘汰，达标，立即停止继续采集，进入 S16 定价。

## 最终定价结果

- final_reference_index=2
- selected_reference_price=2.78万
- selected_reference_score=95.0
- target_score=95.0
- suggested_listing_price=27800
- suggested_purchase_price=22410
- S17 payload：已输出本轮丰田致炫 fingerprint、建议挂牌价、建议收车价，并携带人工复核/样本不足提示。

## 风险与后续议题

- 目标车缺少出险次数，已采用默认分。
- 目标车缺少最大金额，已采用默认分。
- 样本不足提示已进入 payload。
- 左侧下坎钣金当前未单独写成人工审核原因，后续单独开规则议题：TARGET_CONDITION_LEFT_SILL_SCORING_REVIEW。

## 慢动作清单

- S14_COLLECT：约 77s，不影响正确性，先保留，后续单独做性能优化。
- S14_HORIZONTAL_SWIPE：单次约 5-6s，不影响正确性，先保留，后续单独做性能优化。
- S10_TO_S11 首次 XML fresh：约 9-10s，不影响正确性，先保留，后续单独做性能优化。
- S14_RETURN_TO_S10：约 8.2s，不影响正确性，先保留，后续单独做性能优化。

## 一致性检查

- result_s01_to_s10_status_ok: true
- result_s10_to_s16_status_ok: true
- result_status_ok: true
- acceptance_status_ok: true
- fingerprint_ok: true
- reference_history_count_ok: true
- raw_xml_not_in_result_json: true
- no_old_focus_pollution: true
- no_old_mini_pollution: true
- no_old_honda_pollution: true

旧目标污染检查：福克斯=未发现，MINI/1.5T ONE=未发现，本田/缤智=未发现。

最终状态：**TOYOTA_YARIS_BASELINE_FREEZE_READY**
