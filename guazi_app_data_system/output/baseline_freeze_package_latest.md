# Baseline Freeze Package

baseline_name：`BASELINE_FOCUS_2017_FULL_CHAIN_PRICED_DONE_202605`

生成时间：2026-05-08T10:05:09.400Z

冻结结论：`BASELINE_FREEZE_READY`

## 一、Baseline 名称

`BASELINE_FOCUS_2017_FULL_CHAIN_PRICED_DONE_202605`

## 二、冻结范围

- scripts/runtime_s01_to_s10_mainline.py：存在
- scripts/runtime_s10_to_s16_mainline.py：存在
- output/result_s01_to_s10.json：存在
- output/result_s10_to_s16.json：存在
- output/result.json：存在
- output/full_chain_acceptance_report_latest.md：存在
- output/full_chain_acceptance_report.json：存在
- output/page_contract_timing_report.jsonl：存在
- artifacts/debug/：存在；保留关键 XML 证据，不复制、不删除。
- artifacts/screenshots/：存在；保留关键截图证据，不复制、不删除。

## 三、成功能力清单

- APP_FORCE_RESTART 入口策略：是
- 第一段 S01-S10 固定脚本：是
- S07 颜色 + 车龄筛选：是
- S09 价格从低到高排序：是
- S10_READY 门禁：是
- reference_index 顺序续采逻辑：是
- 参考车不是固定采 3 辆，而是逐辆采集，找到合格车即停止：是
- S10 第 N 辆车卡唯一绑定：是
- S11 顶部车辆图片区识别：是
- S11 查看完整报告完整可见 + 安全区点击：是
- S11_TO_S12 稳定等待：是
- S11_TO_S12 context 下 S12 优先于 S14：是
- S12 理赔次数 / 最大金额采集：是
- S13 历史修复判断：是
- S14 具体修复项采集：是
- S15 单车评分判断：是
- 当前参考车达标后立即进入 S16 定价：是
- S16 定价：是
- S17 payload 模拟输出：是
- reference_history 保留：是
- 旧 MINI 污染防护：是
- raw XML 不写入 result JSON：是

## 四、参考车循环验收说明

> 参考车采集不是固定采 3 辆。本 baseline 按 reference_index 顺序逐辆采集；一旦当前参考车字段完整、未被淘汰且 reference_score >= target_score，即立即停止继续采集并进入 S16 定价。三同车源/有效样本少于 3 辆只作为人工复核提示，不是进入 S16 的阻断条件。

### 第 1 辆
- 完整采集：是
- 未淘汰：是
- reference_score：90.5
- target_score：93
- 价格/信息：2.52万；2017年 | 7.81万公里 | 唐山
- 结论：不满足 reference_score >= target_score，返回可靠 S10 继续采第 2 辆。

### 第 2 辆
- 完整采集：是
- 未淘汰：是
- reference_score：85
- target_score：93
- 价格/信息：2.55万；2017年 | 12.01万公里 | 唐山
- 结论：不满足 reference_score >= target_score，返回可靠 S10 继续采第 3 辆。

### 第 3 辆
- 完整采集：是
- 未淘汰：是
- reference_score：93.5
- target_score：93
- 价格/信息：2.62万；2017年 | 8.18万公里 | 唐山
- 结论：满足最终参考车条件，立即停止继续采集，进入 S16 定价。

## 五、慢动作清单

只记录，不优化。

### S14_COLLECT
- duration：219496
- count：1
- is_aggregate：是
- reason_category：S14_END_CONFIRM_TOO_CONSERVATIVE
- reason_detail：S14 terminal is confirmed by two consecutive image swipes with no page label, first line, normalized damage, or S14 key change
- 处理建议：只记录，不在本轮优化；建议冻结功能基线后单独开性能分支处理。

### S11_REPORT_SEARCH
- duration：12318
- count：1
- is_aggregate：否
- reason_category：WEBVIEW_TEXT_DELAY
- reason_detail：report entry search stops only after the exact node is fully visible and safely clickable
- 处理建议：只记录，不在本轮优化；建议冻结功能基线后单独开性能分支处理。

### S11_TO_S12
- duration：11431
- count：1
- is_aggregate：否
- reason_category：PAGE_TRANSITION_VERIFY
- reason_detail：fresh XML is required after safe report-entry click; S11_TO_S12 waits for stable S12 report-page evidence before entering S12 handler
- 处理建议：只记录，不在本轮优化；建议冻结功能基线后单独开性能分支处理。

### S14_RETURN_TO_S10
- duration：8450
- count：1
- is_aggregate：是
- reason_category：S14_RETURN_PATH_OK_NEEDS_NO_FIX
- reason_detail：S14 return stopped as soon as a reliable S10 list was recognized
- 处理建议：只记录，不在本轮优化；建议冻结功能基线后单独开性能分支处理。

### S14_IMAGE_HORIZONTAL_SWIPE
- duration：{"min":5378,"max":6968,"avg":6263}
- count：35
- is_aggregate：否
- reason_category：S14_IMAGE_HORIZONTAL_SWIPE
- reason_detail：short-poll after image swipe treats page label, first line, normalized damage, and new S14 key as the only effective evidence; image hash is auxiliary only
- 处理建议：只记录，不在本轮优化；建议冻结功能基线后单独开性能分支处理。

### S10_TO_S11_XML_DUMP
- duration：3002
- count：1
- is_aggregate：否
- reason_category：S10_TO_S11_FULL_XML_CAN_BE_COMPRESSED
- reason_detail：S10 to S11 XML evidence prefers compressed uiautomator dump and falls back to full XML at most once
- 处理建议：只记录，不在本轮优化；建议冻结功能基线后单独开性能分支处理。

## 六、冻结结论

`BASELINE_FREEZE_READY`
