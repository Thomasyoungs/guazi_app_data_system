# S11 内部截图/XML 可视区域错配补丁报告

## 最终状态

S11_INTERNAL_XML_SCREENSHOT_REGION_MISMATCH_PATCHED

## 修改范围

仅修改：

- `scripts/runtime_s10_to_s16_mainline.py`

未修改：

- 第一段脚本
- S10 selected card autoscroll
- S10 local title binding
- S11 点击安全门禁
- S11 unsafe reposition
- S12/S13/S14
- pricing / config / metal_deduct / baseline / 打分规则 / 定价规则

## 补丁内容

在 S11_REPORT_SEARCH 的 fresh evidence pair 校验中新增了 S11 内部可视区域一致性检测。

当当前已经处于 S11 报告入口搜索区域，XML 中出现车况/检测下方区域信号，但完全没有任何报告区上下文节点时，不再允许该 XML 参与“查看完整报告不存在”的判断。

新增检测字段包括：

- `s11_internal_visible_region_check`
- `s11_internal_visible_region_mismatch`
- `s11_internal_visible_region_mismatch_reason`
- `s11_report_context_markers_in_xml`
- `s11_report_context_marker_count`
- `s11_internal_mismatch_redump_attempted`
- `s11_internal_mismatch_redump_result`
- `s11_fresh_pair_valid_after_internal_check`

## 执行规则

发现 S11 内部截图/XML 可视区域错配时：

1. 第一次只允许重新 dump 一次 XML/截图。
2. 如果重新 dump 后出现精确 XML 文本“查看完整报告”，仍回到既有安全门禁判断。
3. 如果重新 dump 后仍没有任何报告区上下文，停止并输出：
   `S11_INTERNAL_XML_SCREENSHOT_VISIBLE_REGION_MISMATCH`
4. 错配 XML 不得用于推导：
   - `exact_report_entry_seen=false`
   - `view_full_report_exact_text_seen=false`
   - `official_report_entry_seen=false`
   - `S11_REPORT_ENTRY_SEARCH_EXHAUSTED_WITHOUT_DECISIVE_MARKER`

## 保留约束

- 不启用 OCR。
- 不做截图坐标点击。
- 不做 micro-scroll 刷 XML。
- 不点击“检测报告 / 官方检测 / 车况报告 / 查看报价”等弱信号。
- 不放松“查看完整报告”的完整可见、安全区、底部栏遮挡和点击目标绑定门禁。

## 验证结果

- `py_compile scripts/runtime_s10_to_s16_mainline.py`：通过
- 模块 import 验证：通过，输出 `IMPORT_OK`
- 离线 A-H 验证：全部通过

离线验证文件：

- `output/s11_internal_xml_screenshot_region_mismatch_offline_validation.json`

## 本轮未执行

- 未运行实机。
- 未覆盖 `result.json`。
- 未修改任何采集、评分、定价规则。
