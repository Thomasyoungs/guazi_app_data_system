# 吉利远景 V1.23 S05 / S11 契约有效性只读诊断

生成时间：2026-05-12T16:12:43+08:00

目标 fingerprint：`吉利|远景|2019款|升级版 1.5L 手动豪华型 国VI|白|2019.10`

最终状态：`GEELY_YUANJING_V1_23_S05_S11_CONTRACT_EFFECTIVENESS_DIAGNOSIS_DONE`

## 总体结论

本轮只读诊断完成，未修改代码、未运行实机、未覆盖 result / baseline。

- **S05 根因**：`S05_CONTRACT_IMPLEMENTATION_MISSING`。V1.23 已明确要求先点击并确认左侧目标年款 `2019款`，但第一段脚本当前只具备“点击目标年款”的动作入口，缺少强制左侧年款选中证据门禁；并且现有证据更像依赖右侧混合配置列表完成选择。
- **S11 根因**：`S11_CONTRACT_IMPLEMENTATION_MISSING`。当前口径应只查找 `查看完整报告`；第 2 辆参考车受控下滑 8 次仍未找到该入口时，应排除当前参考车并返回 reliable S10 继续下一辆，但第二段脚本仍走旧 stop：`S11_REPORT_ENTRY_FULL_VISIBILITY_NOT_ACHIEVED`。
- **S10→S11 慢**：主要发生在 S11 报告入口查找阶段的 scroll / fresh / XML dump，不是点击详情加载本身。慢不是排除逻辑未生效的主因。
- **证据配对**：发现第 1 辆参考车一份 XML 含旧目标零跑/C10 碎片但同时间截图为吉利，存在证据配对风险；本轮 S05 与第 2 辆 S11 主要结论未依赖该污染证据。

## S05 诊断

| 检查项 | 结论 |
|---|---|
| 页面契约是否要求先点左侧 2019款 | 是，V1.23 |
| 代码是否完整实现该门禁 | 否 |
| 是否存在 S05_SELECT_TARGET_YEAR action | 是 |
| 是否存在未确认左侧目标年款就禁止右侧配置选择的硬门禁 | 不完整 |
| 本轮是否能证明点击了左侧 2019款 | 不能 |
| 是否直接在右侧配置列表选择目标配置 | 证据倾向是 |
| 国V/国VI 排放版本组是否正确全选 | 是 |
| 最终归因 | `S05_CONTRACT_IMPLEMENTATION_MISSING` |

关键证据路径：

- `artifacts/debug/s04_to_s05_20260512_151852.xml`
- `artifacts/screenshots/s04_to_s05_20260512_151852.png`
- `artifacts/debug/s05_after_trim_variant_2_20260512_151908.xml`
- `artifacts/screenshots/s05_after_trim_variant_2_20260512_151908.png`
- `output/result_s01_to_s10.json`

## S11 诊断

当前修正口径：**只找 `查看完整报告` 入口**。没有找到就排除当前参考车，返回 reliable S10 继续下一台。

| 检查项 | 结论 |
|---|---|
| 页面契约是否要求入口缺失时排除当前参考车 | 是 |
| 第二段代码是否已实现排除并继续 | 否 |
| 第 2 辆是否为 S11 页面 | 是 |
| `查看完整报告` 是否出现 | 否 |
| 受控下滑次数 | 8 |
| 是否显式记录 reached_bottom/page_no_longer_changes | 未充分记录 |
| 当前实际 stop | `S11_REPORT_ENTRY_FULL_VISIBILITY_NOT_ACHIEVED` |
| 最终归因 | `S11_CONTRACT_IMPLEMENTATION_MISSING` |

关键证据路径：

- `artifacts/debug/s10_to_s11_20260512_152327_compressed.xml`
- `artifacts/debug/s11_report_entry_search_1_20260512_152332.xml`
- `artifacts/debug/s11_report_entry_search_8_20260512_152440.xml`
- `artifacts/screenshots/s11_report_entry_search_8_20260512_152440.png`
- `output/result_s10_to_s16.json`

## S10→S11 耗时

归类：`S11_SCROLL_LIMIT_REACHED_BUT_BOTTOM_NOT_CONFIRMED`

主要耗时集中在 S11 查找 `查看完整报告` 的受控下滑、fresh、XML dump。当前停止不是因为慢导致提前失败，而是因为达到查找上限后仍走旧 stop，没有进入“排除当前参考车并继续下一辆”的契约分支。

建议同时补充：下滑到上限时，应显式记录 `reached_bottom`、`page_no_longer_changes`、`scroll_limit_reached`、`view_full_report_seen=false`。

## 证据配对

| 项目 | 结论 |
|---|---|
| result JSON 旧目标污染 | 未发现 |
| artifact 证据旧目标碎片 | 发现一处 |
| 风险路径 | `artifacts/debug/s10_to_s11_20260512_152040_compressed.xml` |
| 风险说明 | XML 出现零跑/C10 碎片，但同时间截图为吉利远景 |
| 风险等级 | 第 1 辆参考车证据 HIGH，系统层面 MEDIUM |

## 结论表

| 问题 | 契约是否明确 | 代码是否实现 | 本轮触发条件是否满足 | 是否按契约执行 | 根因 | 下一步建议 |
|---|---:|---:|---|---:|---|---|
| S05 左侧年款点击证据门禁 | 是 | 否 | 是 | 否 | `S05_CONTRACT_IMPLEMENTATION_MISSING` | `PATCH_S05_LEFT_YEAR_SELECTION_EVIDENCE_GATE` |
| S11 查看完整报告缺失排除当前参考车 | 是 | 否 | 基本满足，但底部确认字段需补 | 否 | `S11_CONTRACT_IMPLEMENTATION_MISSING` | `PATCH_S11_VIEW_FULL_REPORT_MISSING_EXCLUDE_REFERENCE_AND_CONTINUE` |
| S10→S11 慢 / 报告入口查找耗时 | 是 | 部分 | 到达滚动上限 | 部分 | `S11_SCROLL_LIMIT_REACHED_BUT_BOTTOM_NOT_CONFIRMED` | `PATCH_S11_REPORT_SEARCH_BOTTOM_CONFIRMATION_AND_TIMING_FIELDS` |
| evidence pairing 证据配对 | 不足 | 否 | 是 | 否 | `EVIDENCE_PAIRING_RISK_DETECTED` | `PATCH_EVIDENCE_PAIRING_CURRENT_FINGERPRINT_GATE` |

## 下一步建议

1. `PATCH_S05_LEFT_YEAR_SELECTION_EVIDENCE_GATE`
2. `PATCH_S11_VIEW_FULL_REPORT_MISSING_EXCLUDE_REFERENCE_AND_CONTINUE`，按当前口径只找 `查看完整报告`
3. `PATCH_S11_REPORT_SEARCH_BOTTOM_CONFIRMATION_AND_TIMING_FIELDS`
4. `PATCH_EVIDENCE_PAIRING_CURRENT_FINGERPRINT_GATE`

## 只读确认

- 未修改代码
- 未修改 runtime 脚本
- 未修改 pricing / config / DOCX
- 未运行实机
- 未执行 APP_FORCE_RESTART
- 未启动第二段
- 未采参考车
- 未重新定价
- 未覆盖 result.json
- 未覆盖 baseline
