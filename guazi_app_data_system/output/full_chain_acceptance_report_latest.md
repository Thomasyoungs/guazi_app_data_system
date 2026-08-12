# FULL_CHAIN 验收报告

生成时间：2026-05-08T09:21:07.021Z
目标车：福特|福克斯|2017款|两厢 1.6L 自动舒适型智行版|白|2017.07
最终验收状态：`FULL_CHAIN_ACCEPTANCE_PASSED_BASELINE_READY`

## 一、完整链路验收结论
- 第一段是否成功到 S10_READY：是
- 第二段是否成功从 S10 进入 S11：是
- 是否完成 S11/S12/S13/S14/S15：是
- 是否完成 S16 定价：是
- 是否输出 S17 payload：是
- 最终状态是否为 FULL_CHAIN_PRICED_DONE：是

## 二、参考车采集链路
### 第 1 辆
- index：1
- 价格：2.52万
- 年份/里程/城市：2017年 | 7.81万公里 | 唐山
- 采集字段：过户 2 次；理赔 3 次；最大金额 2000；历史修复 {"驾驶侧":9}；S14_COLLECT_DONE=true
- 维修项：[{"part":"左前门","damage_type":"钣金"}]
- 分数：90.5
- 是否达标：否
- 结论：未达标，参考车分 90.5 低于目标车分 93，继续下一辆。

### 第 2 辆
- index：2
- 价格：2.55万
- 年份/里程/城市：2017年 | 12.01万公里 | 唐山
- 采集字段：过户 3 次；理赔 2 次；最大金额 5000；历史修复 {"驾驶侧":12}；S14_COLLECT_DONE=true
- 维修项：[{"part":"左前翼子板","damage_type":"喷漆"}]
- 分数：85
- 是否达标：否
- 结论：未达标，参考车分 85 低于目标车分 93，继续下一辆。

### 第 3 辆
- index：3
- 价格：2.62万
- 年份/里程/城市：2017年 | 8.18万公里 | 唐山
- 采集字段：过户 0 次；理赔 2 次；最大金额 5000；历史修复 {"驾驶侧":8}；S14_COLLECT_DONE=true
- 维修项：[{"part":"左前翼子板","damage_type":"喷漆"}]
- 分数：93.5
- 是否达标：是
- 结论：达标，选为最终参考车。

## 三、最终定价结果
- target_score：93
- selected_reference_index：3
- selected_reference_score：93.5
- selected_reference_price：2.62万
- suggested_listing_price：26200
- suggested_purchase_price：20890
- pricing_basis：选择第 3 辆参考车，参考车分 93.5 >= 目标车分 93.0，按参考价 2.62 万形成挂牌价，并扣除回收/成本/利润得到收车价。
- manual_review_reasons：["目标车缺少出险次数，已采用默认分。","目标车缺少最大金额，已采用默认分。","三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。"]
- sample_shortage_warning：是

## 四、本轮关键修复是否生效
- S11_TOP_ONE_THIRD_IMAGE_ONLY_STANDARD：生效
- S11 查看完整报告完整可见、安全区内、未被底部栏遮挡后再点击：生效
- S11_TO_S12_WAIT_STABLE_AFTER_REPORT_CLICK：生效
- S11_TO_S12 context 下 S12 优先于 S14：生效
- reference_index 续采逻辑：生效
- S10 第 N 辆 reference_index 绑定逻辑：生效
- reference_history 保留逻辑：生效
- 旧 MINI 污染防护：生效

## 五、慢动作诊断
### S14_COLLECT
- duration：219496
- 是否影响正确性：否
- 是否建议立即优化：否
- 是否建议先冻结基线，后续单独做性能优化：是
- 原因：S14_END_CONFIRM_TOO_CONSERVATIVE；S14 terminal is confirmed by two consecutive image swipes with no page label, first line, normalized damage, or S14 key change

### S11_REPORT_SEARCH
- duration：12318
- 是否影响正确性：否
- 是否建议立即优化：否
- 是否建议先冻结基线，后续单独做性能优化：是
- 原因：WEBVIEW_TEXT_DELAY；report entry search stops only after the exact node is fully visible and safely clickable

### S11_TO_S12
- duration：11431
- 是否影响正确性：否
- 是否建议立即优化：否
- 是否建议先冻结基线，后续单独做性能优化：是
- 原因：PAGE_TRANSITION_VERIFY；fresh XML is required after safe report-entry click; S11_TO_S12 waits for stable S12 report-page evidence before entering S12 handler

### S14_RETURN_TO_S10
- duration：8450
- 是否影响正确性：否
- 是否建议立即优化：否
- 是否建议先冻结基线，后续单独做性能优化：是
- 原因：S14_RETURN_PATH_OK_NEEDS_NO_FIX；S14 return stopped as soon as a reliable S10 list was recognized

### S14_IMAGE_HORIZONTAL_SWIPE
- duration：{"min":5378,"max":6968,"avg":6263}
- 是否影响正确性：否
- 是否建议立即优化：否
- 是否建议先冻结基线，后续单独做性能优化：是
- 原因：S14_IMAGE_HORIZONTAL_SWIPE；short-poll after image swipe treats page label, first line, normalized damage, and new S14 key as the only effective evidence; image hash is auxiliary only

### S10_TO_S11_XML_DUMP
- duration：3002
- 是否影响正确性：否
- 是否建议立即优化：否
- 是否建议先冻结基线，后续单独做性能优化：是
- 原因：S10_TO_S11_FULL_XML_CAN_BE_COMPRESSED；S10 to S11 XML evidence prefers compressed uiautomator dump and falls back to full XML at most once

## 六、剩余风险
- 当前是否还有必须修复的功能性阻塞：无
- 当前是否适合作为 baseline：是
- 哪些问题只属于性能优化：S14_COLLECT、S11_REPORT_SEARCH、S11_TO_S12、S14_RETURN_TO_S10、S14_IMAGE_HORIZONTAL_SWIPE、S10_TO_S11_XML_DUMP
- 哪些规则还需要后续同步到文档：S11 顶部三分之一图片识别标准；查看完整报告完整可见/安全区点击；S11_TO_S12 稳定等待与 S12 优先于 S14 的 transition context 规则

## 七、最终验收状态
`FULL_CHAIN_ACCEPTANCE_PASSED_BASELINE_READY`
