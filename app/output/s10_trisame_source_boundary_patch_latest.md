# S10 三同车源边界与目标标题过滤补丁报告

最终状态：`PATCH_ONLY_S10_TRISAME_SOURCE_BOUNDARY_AND_TITLE_FILTER_DONE`

## 修改范围

- 修改第一段脚本：是
  - `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\scripts\runtime_s01_to_s10_mainline.py`
- 修改第二段脚本：是
  - `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\scripts\runtime_s10_to_s16_mainline.py`
- 修改 pricing：否
- 修改服务费阶梯规则：否
- 修改 S11/S12/S13/S14 已验证逻辑：否
- 修改页面契约文档：否
- 修改 config：否

## 补丁内容

1. S10 canonical reference order 生成前新增目标标题硬过滤。
   - 必须同时命中目标品牌、车系、年款、配置关键词。
   - 本轮目标为：`大众|途昂|2017款|330TSI 两驱豪华版|白|2018.07`。
   - 允许进入 reference_order 的标题：`大众 途昂 2017款 330TSI 两驱豪华版`。
   - `大众ID.3 2024款 出众版` 等非目标车型进入 excluded cards。

2. S10 非三同区域边界识别。
   - 边界文本包括：`找不到想要的车`、`全国淘车`、`更多车源`、`推荐车源`、`猜你喜欢`、`同品牌推荐`、`为你推荐`、`其他车源`。
   - 边界之后的车卡不进入三同车源池，不进入 canonical_reference_order。

3. 第一段 S10_READY 落盘真实三同池。
   - `same_source_cards` 只保留真实目标三同车源。
   - 新增/保留诊断字段：
     - `raw_visible_cards_count`
     - `trisame_cards_count`
     - `trisame_count`
     - `trisame_count_confirmed`
     - `excluded_non_trisame_cards_count`
     - `excluded_non_trisame_cards`
     - `non_trisame_section_detected`
     - `non_trisame_section_title`
     - `cards_after_boundary_excluded_count`

4. 第二段执行三同池数量上限。
   - 读取第一段真实 `trisame_count`。
   - 如果 `next_reference_index > trisame_count`，输出 `ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW`。
   - 不再继续向下滑入 `更多车源` 区域寻找第 3 辆。

5. 正确顺序已固化：
   - 解析 live reliable S10 候选 card
   - 检测非三同区域边界
   - 排除边界之后 card
   - 目标标题硬过滤
   - 完整车卡门禁
   - 生成 canonical_reference_order
   - 再按 price asc / same-price mileage desc / live display order asc 排序

## py_compile

结果：通过

命令：

```powershell
& 'C:\Users\lzc93\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m py_compile scripts/runtime_s01_to_s10_mainline.py scripts/runtime_s10_to_s16_mainline.py
```

## 离线 XML 回放验证

途昂样本：

- XML：`C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s10_s16_start_20260509_161743.xml`
- raw visible cards：`22`
- trisame cards：`2`
- excluded non-trisame cards：`20`
- boundary：`找不到想要的车`
- second-stage canonical_reference_order：只包含 2 辆途昂
- 大众 ID.3：已排除，不在 canonical_reference_order

真实三同车源：

1. `大众 途昂 2017款 330TSI 两驱豪华版`，`10.89万`，`2018年 | 5.65万公里 | 重庆`
2. `大众 途昂 2017款 330TSI 两驱豪华版`，`11.79万`，`2018年 | 12.22万公里 | 齐齐哈尔`

历史样本回归：

- 福克斯：未误删真实目标车卡。
- 丰田 YARiS L 致炫：未误删真实目标车卡。
- 桑塔纳：未误删真实目标车卡。

## 实机验证

第一段：

- status：`S10_READY`
- fingerprint：`大众|途昂|2017款|330TSI 两驱豪华版|白|2018.07`
- raw_visible_cards_count：`22`
- trisame_count：`2`
- same_source_cards：只包含 2 辆途昂
- excluded_non_trisame_cards_count：`20`
- non_trisame_section_title：`找不到想要的车`

第二段：

- status：`FULL_CHAIN_PRICED_DONE`
- fingerprint：`大众|途昂|2017款|330TSI 两驱豪华版|白|2018.07`
- selected_reference_index：`1`
- selected_card_title：`大众 途昂 2017款 330TSI 两驱豪华版`
- selected_card_price：`10.89万`
- selected_card_metadata：`2018年 | 5.65万公里 | 重庆`
- reference_score：`96.0`
- target_score：`91.0`
- reference_score >= target_score：是
- 结论：第 1 辆真实三同途昂达标，达标即停并进入 S16。

## S16 定价

- 瓜子定价：`108900`
- 瓜子服务费：`4500`
- 瓜子回款价：`104400`
- 建议收车价：`93548`
- 使用服务费阶梯规则：是
- 使用旧 `×95%` 规则：否

## 污染与 raw XML 检查

- 未点击大众 ID.3。
- 大众 ID.3 未进入 reference_history。
- 未把“更多车源 / 全国淘车 / 找不到想要的车”下方车源纳入 reference_order。
- result JSON 未写入 raw XML 大块字段。
- 未覆盖福克斯 / 丰田 / 桑塔纳 baseline 文件。

## 输出结果文件

- `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\output\result_s01_to_s10.json`
- `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\output\result_s10_to_s16.json`
- `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\output\result.json`

## 最终结论

补丁已验证生效：S10 reference_order 只包含真实三同车源，非三同更多车源被排除。途昂第 1 辆真实参考车达标并完成完整定价闭环。

最终状态：`PATCH_ONLY_S10_TRISAME_SOURCE_BOUNDARY_AND_TITLE_FILTER_DONE`
