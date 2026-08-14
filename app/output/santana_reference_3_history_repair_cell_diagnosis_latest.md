# 桑塔纳第 3 辆 S13 历史修复单元格诊断

## 结论

最终归类：`S13_HISTORY_REPAIR_CELL_PARSER_BUG`

辅助现象：`S13_HISTORY_REPAIR_COUNT_NODE_SPLIT` / `S13_HISTORY_REPAIR_ZERO_OR_EMPTY_STYLE_CHANGED`

本轮没有修改代码、pricing、页面契约、config，也没有重新运行实机。诊断基于本轮 `HISTORY_REPAIR_CELL_NOT_FOUND` 的 result、timing、截图和 XML。

## 当前链路状态

- target_fingerprint: `大众|桑塔纳|2021款|1.5L 自动风尚版|白|2021.05`
- selected_reference_index: `3`
- selected_card_price: `4.03万`
- selected_card_metadata: `2021年 | 11.74万公里 | 唐山`
- selected_by: `canonical_reference_order`
- 当前阻塞: `HISTORY_REPAIR_CELL_NOT_FOUND`
- missing_region: `驾驶侧`

## S13 是否进入

- entered_s13: `true`
- recognized_page: `S13`
- visible_text_digest: `e35678e03f302f95`
- XML: `C:/Users/lzc93/Desktop/定价/guazi_app_data_system/artifacts/debug/s13_region_驾驶侧_20260509_133419.xml`
- screenshot: `C:/Users/lzc93/Desktop/定价/guazi_app_data_system/artifacts/screenshots/s13_region_驾驶侧_20260509_133419.png`

页面证据：
- 有 `车身外观`
- 有 `历史修复`
- 有 `驾驶侧 / 车尾 / 副驾驶 / 车头`
- 截图可见 `驾驶侧深度检测：历史修复 0 | 注意事项 0`

## 车身外观入口点击

- clicked_body_appearance: `true`
- clicked_text: `车身外观`
- clicked_bounds: `[487, 247, 796, 396]`
- click_strategy: `exact_text_node_bounds_after_controlled_scroll`
- page_changed_after_click: `true`
- 点击前 XML: `C:/Users/lzc93/Desktop/定价/guazi_app_data_system/artifacts/debug/s12_scroll_body_search_tab_0_20260509_133409.xml`
- 点击后 XML: `C:/Users/lzc93/Desktop/定价/guazi_app_data_system/artifacts/debug/s12_to_s13_20260509_133414.xml`

## 失败点

runtime 当前 parser 输出：

```json
{"驾驶侧": 36, "车尾": null, "副驾驶": null, "车头": null}
```

但 XML 原始相邻结构显示：

```text
驾驶侧深度检测：
历史修复
0
注意事项
0
检测通过
驾驶侧：检测通过36
```

因此 `36` 不是历史修复次数，而是“驾驶侧检测通过 36 项”。真实历史修复次数为 `0`。

## 根因

当前 `_visible_texts()` 会对文本去重。页面前面已经出现过一个可见文本 `0`，所以历史修复表格中位于 `历史修复` 后面的第二个 `0` 被去重丢弃。

去重后的 visible_texts 片段变成：

```text
驾驶侧深度检测：
历史修复
驾驶侧：检测通过36
左后门框漆面
...
```

随后 `_extract_adjacent_history_repair_count()` 在 `历史修复` 后找下一个带数字的文本，误把 `驾驶侧：检测通过36` 解析为 `36`。

由于 count 被误判为非 0，runtime 尝试寻找可点击的合法修复项；但当前页没有合法 S14 维修项入口，于是触发：

`HISTORY_REPAIR_CELL_NOT_FOUND`

## 必答项

- missing_region: `驾驶侧`
- expected_regions: `["驾驶侧", "车尾", "副驾驶", "车头"]`
- detected_regions: `["驾驶侧", "车尾", "副驾驶", "车头"]`
- detected_counts_current_parser: `{"驾驶侧": 36, "车尾": null, "副驾驶": null, "车头": null}`
- actual_visible_count_for_驾驶侧: `0`
- table_like_structure_seen: `true`
- history_table_visible: `true`
- 是否需要小幅滚动: `否，驾驶侧表格已可见`
- 是否 XML 预加载但截图不可见: `否，截图和 XML 都可见`
- 是否截图可见但 XML 结构分散: `是，区域名、历史修复、数字节点是分离节点`

## Parser 规则对比

current_parser_expected_pattern:

```text
region_name + "深度检测"
在后续窗口内找到 "历史修复"
读取 "历史修复" 后一个文本中的数字
```

actual_pattern:

```text
驾驶侧深度检测：
历史修复
0
注意事项
0
检测通过
驾驶侧：检测通过36
```

pattern_mismatch_reason:

`0` 节点存在，但被 `_visible_texts()` 去重后丢失，导致 parser 读取了下一个包含数字的检测通过摘要。

## 诊断状态

`DIAGNOSE_SANTANA_REFERENCE_3_HISTORY_REPAIR_CELL_NOT_FOUND_DONE`
