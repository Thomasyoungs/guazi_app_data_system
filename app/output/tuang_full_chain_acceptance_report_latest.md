# 大众途昂完整闭环验收报告

## 一、完整链路验收结论

- 验收状态：TUANG_FULL_CHAIN_ACCEPTANCE_PASSED
- 目标 fingerprint：大众|途昂|2017款|330TSI 两驱豪华版|白|2018.07
- 第一段结果：S10_READY
- 第二段结果：FULL_CHAIN_PRICED_DONE
- 第二段已成功启动并完成 S10 -> S11 -> S12 -> S13 -> S14 -> S15 -> S16。
- S17 payload：已输出，mode=simulated_feishu_writeback，task_status=priced。
- 结果一致性：output/result_s01_to_s10.json、output/result_s10_to_s16.json、output/result.json 均为途昂目标，无旧目标结果冒充。

## 二、S10 三同源边界补丁验收

- raw cards：22
- 真实三同途昂：2
- excluded_non_trisame_cards_count：20
- 非三同边界：找不到想要的车
- 大众 ID.3 位于“找不到想要的车 / 全国淘车 / 更多车源”之后的非三同区域。
- 大众 ID.3 已排除，未进入 canonical_reference_order，未进入 reference_history，也未被点击。
- same_source_cards 只保留 2 辆真实途昂：
  - 10.89万，2018年 | 5.65万公里 | 重庆
  - 11.79万，2018年 | 12.22万公里 | 齐齐哈尔
- 规则确认：“找不到想要的车 / 全国淘车 / 更多车源”后面的车不得进入 reference_order。

## 三、参考车采集链路

参考车不是按固定数量采集。本轮按 S10 reference_index 逐辆采集，第一辆已满足最终参考车条件，因此达标即停。

第 1 辆：
- reference_index：1
- 价格：10.89万
- 信息：2018年 | 5.65万公里 | 重庆
- 过户次数：1
- 理赔次数：0
- 最大金额：0
- 历史修复：驾驶侧=0，车尾=1
- S14 维修项：右后翼子板 / 钣金
- reference_score：96.0
- target_score：91.0
- 是否淘汰：否
- 是否达标：是，reference_score >= target_score
- 选中原因：字段采集完整、未被事故车或结构风险规则淘汰、分数达标，所以立即停止继续采集并进入 S16。

## 四、最终定价结果

- target_score：91.0
- final_reference_index：1
- selected_reference_score：96.0
- selected_reference_price：10.89万
- guazi_price_yuan：108900
- guazi_service_fee_yuan：4500
- guazi_net_payout_yuan：104400
- suggested_purchase_price_yuan：93548
- S17 payload 摘要：建议挂牌价 108900，建议收车价 93548，最终参考车 index=1。
- 服务费档位确认：108900 属于 >=10万 且 <15万档，服务费 4500。
- 旧规则确认：未使用“瓜子定价 x 95%”。

## 五、目标车人工审核 / 风险提示

S17 payload 已包含：
- 目标车缺少出险次数，已采用默认分。
- 目标车缺少最大金额，已采用默认分。
- 三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。

目标车备注中“主驾驶地板水痕、管柱锈蚀、副驾驶地毯变色、地板发霉、可能会出进水痕迹、未读 OBD”未作为独立人工审核原因进入当前 payload。后续规则议题：

- TUANG_TARGET_WATER_INGRESS_AND_OBD_REVIEW_RULE

## 六、关键能力复用验收

本轮实际复用并验证：

1. APP_FORCE_RESTART
2. 第一段 S01-S10
3. S07 颜色 / 车龄筛选
4. S09 价格从低到高
5. S10_READY 门禁
6. reliable S10 门禁
7. S10 三同源边界过滤
8. 目标标题硬过滤
9. canonical_reference_order
10. S10 完整车卡门禁
11. S11 顶部车辆图片区识别
12. 查看完整报告完整可见 + 安全区点击
13. S11_TO_S12 稳定等待
14. S12 优先于 S14
15. S13 raw XML nodes 历史修复解析
16. S13 修复项点击 guard / 直播入口禁点：本轮车尾修复项“后备箱盖铰链”安全点击，禁点区域未触发误点
17. S14 具体修复项采集
18. S15 单车评分判断
19. S16 服务费阶梯定价
20. S17 payload 输出
21. 旧目标污染防护
22. raw XML 不写入 result JSON

## 七、慢动作诊断

只记录，不优化：

- S10 XML fresh / S10_TO_S11_XML_DUMP：约 9034ms；不影响正确性；建议后续单独优化。
- S11_REPORT_SEARCH：约 13312ms；不影响正确性；该阶段为完整可见与安全区点击门禁，建议保留正确性基线后单独优化。
- S11_TO_S12：约 11551ms；不影响正确性；稳定等待避免 loading overlay 误判，建议后续单独优化。
- S14_COLLECT：约 42799ms；不影响正确性；主要来自图片横滑和终止确认，建议后续单独优化。
- S14_HORIZONTAL_SWIPE：单次约 5133-5568ms；不影响正确性；建议后续单独优化。
- S14_RETURN_TO_S10：约 8798ms；不影响正确性；返回后可靠 S10 校验生效，建议后续单独优化。

## 八、最终验收状态

TUANG_FULL_CHAIN_ACCEPTANCE_PASSED
