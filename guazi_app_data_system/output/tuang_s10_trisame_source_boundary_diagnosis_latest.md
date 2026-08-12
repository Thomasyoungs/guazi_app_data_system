# 途昂 S10 三同车源边界诊断

最终状态：`DIAGNOSE_TUANG_S10_TRISAME_SOURCE_BOUNDARY_ONLY_DONE`

## 结论

诊断归类：`S10_TRISAME_SOURCE_BOUNDARY_LEAK_CONFIRMED`

辅助确认：

- `S10_NON_TRISAME_MORE_CARS_LEAKED_INTO_REFERENCE_ORDER`
- `S10_TARGET_TITLE_HARD_GATE_MISSING_CONFIRMED`
- `S10_TRISAME_POOL_COUNT_NOT_ENFORCED_CONFIRMED`
- `S10_SCROLL_POSITION_IN_MORE_SECTION_CONFIRMED`

本轮未修改代码、未修改页面契约、未修改 config、未修改 pricing、未运行新实机、未继续采集、未进入 S15/S16、未输出定价。

## 证据路径

- 第一段结果：`C:\Users\lzc93\Desktop\定价\guazi_app_data_system\output\result_s01_to_s10.json`
- 第二段结果：`C:\Users\lzc93\Desktop\定价\guazi_app_data_system\output\result_s10_to_s16.json`
- 汇总结果：`C:\Users\lzc93\Desktop\定价\guazi_app_data_system\output\result.json`
- 第二段日志：`C:\Users\lzc93\Desktop\定价\guazi_app_data_system\output\tuang_s13_guard_second_20260509_161742.log`
- 最新 S10 XML：`C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s10_s16_start_20260509_161743.xml`
- 最新 S10 screenshot：`C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s10_s16_start_20260509_161743.png`

## 一、第一段 S10_READY 三同车源数量

目标 fingerprint：`大众|途昂|2017款|330TSI 两驱豪华版|白|2018.07`

第一段 `status=S10_READY`，但 `same_source_cards` 当前落盘数量为 `22`。其中严格匹配目标标题 `大众 途昂 2017款 330TSI 两驱豪华版` 的只有 `2` 辆：

1. `大众 途昂 2017款 330TSI 两驱豪华版`，`10.89万`，`2018年 | 5.65万公里 | 重庆`
2. `大众 途昂 2017款 330TSI 两驱豪华版`，`11.79万`，`2018年 | 12.22万公里 | 齐齐哈尔`

第一段 `same_source_cards` 后续还包含非目标车型，例如：

- `大众ID.3 2024款 出众版`，`8.78万`，`2024年 | 2.86万公里 | 唐山 | IQ.Drive`
- `大众 桑塔纳 2021款 1.5L 手动风尚版`，`3.56万`，`2021年 | 2.41万公里 | 唐山`
- `大众 捷达 2017款 1.5L 自动时尚型`，`3.22万`，`2018年 | 9.05万公里 | 唐山`

因此：

- `trisame_count_from_first_stage`: 当前没有干净字段；按目标标题严格过滤推断为 `2`
- `s10_ready_card_count`: 现有 parser 输出 `22`
- `s10_ready_parsed_cards`: 已混入更多车源/推荐车源里的非三同车

## 二、大众 ID.3 所在区域

在 `s10_s16_start_20260509_161743.xml` 中，大众 ID.3 周围结构如下：

- `找不到想要的车？` bounds=`[26,1800,864,1852]`
- `全国淘车` bounds=`[955,1833,1144,1898]`
- `更多车源` bounds=`[52,2054,1220,2138]`
- `大众ID.3 2024款 出众版` bounds=`[562,2177,1082,2242]`
- `2024年 | 2.86万公里 | 唐山 | IQ.Drive` bounds=`[429,2249,1020,2301]`
- `8.78` bounds=`[429,2405,523,2463]`

结论：

- `id3_seen=true`
- `section_title=更多车源`
- `nearby_texts=[找不到想要的车？, 全国淘车, 更多车源]`
- `non_trisame_section_detected=true`

ID.3 明确位于“三同车源列表”下方的更多/推荐/全国淘车区域，不属于本轮目标车三同车源池。

## 三、ID.3 是否进入 canonical_reference_order

已进入。

第二段结果显示：

- `status=REFERENCE_CARD_TITLE_MISMATCH`
- `target_reference_index=1`
- `expected_title=大众 途昂 2017款 330TSI 两驱豪华版`
- `actual_title=大众ID.3 2024款 出众版`
- `selected_card.reference_index=1`
- `selected_card.canonical_reference_index=1`
- `selected_card.live_display_order=3`
- `selected_card.list_price_text=8.78万`
- `selected_card.raw_metadata=2024年 | 2.86万公里 | 唐山 | IQ.Drive`

原因链路：

1. `_extract_s10_reference_cards()` 当前以目标品牌 `大众` + 年款标题形态识别车卡。
2. 它未在 `更多车源 / 找不到想要的车 / 全国淘车` 边界处停止。
3. 它未在生成 canonical order 前强制完整目标车型标题匹配。
4. `_canonicalize_s10_reference_order()` 对所有完整卡片按 `price_yuan asc, mileage desc, live_display_order asc` 排序。
5. ID.3 价格 `8.78万` 低于两辆途昂 `10.89万 / 11.79万`，因此被排成 canonical `reference_index=1`。
6. 之后的标题硬校验才拦截，输出 `REFERENCE_CARD_TITLE_MISMATCH`。

所以本次没有把 ID.3 当作有效参考车继续采集，但它已经错误进入了 reference_order。

## 四、reliable S10 门禁是否过宽

当前 `_s10_reliable_list_evidence()` 的可靠列表核心条件为：

- 有 `价格从低到高`
- 有可解析完整车卡
- 没有报告页强信号，如 `查看完整报告 / 保险理赔记录 / 理赔次数 / 最大金额`

这能排除报告页/S11/S12/S13，但不足以区分：

- 真正三同车源池
- 页面下方 `更多车源 / 推荐车源 / 全国淘车`

缺失门禁：

- 目标车型标题硬匹配应前置到 canonical order 生成前
- 三同车源边界识别
- `更多车源 / 找不到想要的车 / 全国淘车` 区域排除
- 第一段三同池数量上限

结论：`reliable_s10_too_broad=true`

## 五、第二段为什么走到 ID.3

执行链路：

1. 途昂第 1 辆参考车此前未完成有效 S14/S15，因此当前应重采或继续 `reference_index=1`，不是进入最终定价。
2. 第二段启动时 live S10 XML 同屏包含两辆途昂和下方 `更多车源` 区域。
3. S10 reliable 门禁认为该页是可靠 S10，因为它有 `价格从低到高`、有车卡、无报告页信号。
4. S10 parser 把 `更多车源` 里的 ID.3 解析为完整 card。
5. canonical order 按价格排序，ID.3 `8.78万` 排在途昂 `10.89万 / 11.79万` 前。
6. selection 选择 canonical `reference_index=1`，即 ID.3。
7. 标题硬校验发现 `actual=大众ID.3 2024款 出众版` 不等于 `expected=大众 途昂 2017款 330TSI 两驱豪华版`，流程阻断。

判断：

- `whether_reference_1_valid=false`
- `whether_next_reference_index_should_be_1_or_2=1`，因为第 1 辆未形成有效完整参考车结果
- `whether_system_should_continue_beyond_trisame_count=false`

严格三同池只有 2 辆。若两辆途昂都完成采集后仍无合格参考车，应输出 `ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW`，不应继续进入 `更多车源`。

## 六、最终诊断归类

主归类：

`A. S10_TRISAME_SOURCE_BOUNDARY_LEAK_CONFIRMED`

理由：大众 ID.3 明确来自 `更多车源 / 全国淘车` 非三同区域，并被错误纳入 canonical reference order。

同时成立的辅助分类：

- `B. S10_TARGET_TITLE_HARD_GATE_MISSING_CONFIRMED`：标题硬校验存在于选中后，但 canonical order 生成前没有先过滤目标标题。
- `C. S10_TRISAME_POOL_COUNT_NOT_ENFORCED_CONFIRMED`：第一段严格三同车只有 2 辆，但结果中的 `same_source_cards` 被污染为 22 辆；第二段未强制三同池数量上限。
- `D. S10_SCROLL_POSITION_IN_MORE_SECTION_CONFIRMED`：最新 S10 XML 已包含并解析到 `更多车源` 区域。

不成立：

- `E. ID3_NOT_IN_REFERENCE_ORDER_BUT_TITLE_MISMATCH_CAUSED_BY_OTHER_REASON`：不成立，ID.3 已作为 selected canonical card 出现。
- `F. EVIDENCE_INSUFFICIENT_NEED_MORE_LOGS`：不成立，当前 XML/result/log 证据足够。

## 七、下一步补丁建议

本轮不修改代码，仅给出建议：

1. S10 canonical reference order 生成前先做目标标题硬过滤：
   - 必须命中 `brand + series + year_model + config_model`
   - ID.3 / 桑塔纳 / 捷达等非目标车型直接进入 `excluded_cards`
   - `exclude_reason=title_mismatch_or_non_trisame_section`

2. S10 parser 遇到以下边界后停止向下扩展三同源池：
   - `找不到想要的车？`
   - `更多车源`
   - `推荐车源`
   - `全国淘车`
   - `猜你喜欢`

3. 第一段 S10_READY 应落盘干净的 `trisame_count` 和 `trisame_cards`：
   - 本轮应为 `trisame_count=2`
   - 原 `same_source_cards=22` 不能作为三同池上限

4. 第二段应强制执行三同池上限：
   - `next_reference_index > trisame_count` 时输出 `ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW`
   - 不允许继续解析更多车源

5. reliable S10 门禁保持现有报告页排除，但增加目标车池边界：
   - 当前页面必须有至少一张目标车型完整卡
   - 若当前可见区已进入更多车源且无目标卡，应阻断或回滚到三同池区域

## 八、最终状态

`DIAGNOSE_TUANG_S10_TRISAME_SOURCE_BOUNDARY_ONLY_DONE`
