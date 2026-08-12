# S14 Return Slow Diagnosis Note

归档时间：2026-05-07

归档状态：S14_RETURN_SLOW_DIAGNOSIS_ARCHIVED

## Scope

本 note 归档最近一次 `FULL_CHAIN_PRICED_DONE` 成功链路中，S14 最后一张车况采集完成后返回 S10 较慢的专项诊断结论。

本次归档不修改代码、不修改页面契约、不修改打分规则、不修改 config、不运行实机。

## Evidence

- result: `output/result_s10_to_s16.json`
- final result: `output/result.json`
- timing jsonl: `output/page_contract_timing_report.jsonl`
- timing md: `output/page_contract_timing_report.md`
- S14 terminal screenshot: `artifacts/screenshots/s14_image_swipe_20260506_213225.png`
- S14 terminal XML: `artifacts/debug/s14_image_swipe_20260506_213225.xml`
- S14 return attempt 1 screenshot: `artifacts/screenshots/s14_return_to_s10_attempt_1_20260506_213230.png`
- S14 return attempt 1 XML: `artifacts/debug/s14_return_to_s10_attempt_1_20260506_213230.xml`
- S14 return attempt 2 screenshot: `artifacts/screenshots/s14_return_to_s10_attempt_2_20260506_213234.png`
- S14 return attempt 2 XML: `artifacts/debug/s14_return_to_s10_attempt_2_20260506_213234.xml`

## Conclusion

1. `S14_COLLECT_DONE` 后没有额外 sleep / fresh / XML dump。
2. `S14_COLLECT_DONE` 后立即进入 `_fixed_return_to_s10`。
3. 第一次 back 动作本身约 89ms。
4. 第二次 back 动作本身约 67ms。
5. 第一次 back 后视觉上离开 S14 弹层，但 XML 仍包含 S14 车况项文本，`recognized_page` 仍为 `S14`。
6. 第二次 back 后 `recognized_page=S10`。
7. 命中 S10 后立即停止返回。
8. 没有第三次 back。
9. 没有多余 back。
10. `S14_RETURN_TO_S10` 是 aggregate，不是单次动作。
11. 真正慢点是每次返回后的 screenshot / XML dump / WebView fresh。
12. 当前无语义明确唯一关闭节点。
13. 视觉上的 X 在 XML 中是空 label clickable image，且存在多个空 label clickable 节点，不适合作为固定关闭动作。
14. 当前最安全路径仍是系统 back 两次 + fresh 校验。
15. 不建议继续 patch `S14_RETURN_TO_S10`，除非后续页面契约明确允许该 X 关闭节点。

## Timing Summary

| action | duration_ms | note |
|---|---:|---|
| S14_RETURN_TO_S10_ATTEMPT #1 back | 89 | back 本身不慢 |
| S14_RETURN_TO_S10_ATTEMPT #1 wait_after_back | 350 | 固定短等待，不是主慢点 |
| S14_RETURN_TO_S10_ATTEMPT #1 screenshot | 577 | 可接受 |
| S14_RETURN_TO_S10_ATTEMPT #1 XML dump | 2976 | 主要慢点 |
| S14_RETURN_TO_S10_ATTEMPT #1 recognized_page | - | S14 |
| S14_RETURN_TO_S10_ATTEMPT #2 back | 67 | back 本身不慢 |
| S14_RETURN_TO_S10_ATTEMPT #2 wait_after_back | 350 | 固定短等待，不是主慢点 |
| S14_RETURN_TO_S10_ATTEMPT #2 screenshot | 718 | 可接受 |
| S14_RETURN_TO_S10_ATTEMPT #2 XML dump | 3378 | 主要慢点 |
| S14_RETURN_TO_S10_ATTEMPT #2 recognized_page | - | S10 |
| S14_RETURN_TO_S10 | 9121 | aggregate timing |

## Root Cause Categories

- `S14_RETURN_REQUIRES_TWO_BACKS`
- `S14_FIRST_BACK_CLOSES_INNER_LAYER_ONLY`
- `S14_FIRST_BACK_VISUAL_CHANGED_XML_STILL_S14`
- `S14_SECOND_BACK_REACHES_S10`
- `S14_RETURN_TO_S10_ALREADY_MINIMAL`
- `S14_RETURN_XML_DUMP_SLOW`
- `S14_RETURN_WEBVIEW_TEXT_DELAY`
- `S14_RETURN_AGGREGATE_TIMING_MISLEADING`

## Decision

保持当前 S14 返回逻辑：

- 保持系统 back 两次的最小可靠路径。
- 保持 `returned_list_source_verified=true`。
- 不跳过 S10 fresh 校验。
- 不点击无语义 X。
- 不从 S14 直接进入 S15。
- 将 `S14_RETURN_TO_S10` 标记为当前不可安全继续优化项。

## Future Optimization Priority

后续性能优化优先级转向：

1. S07 颜色 / 车龄。
2. S10_TO_S11 XML dump 的重复取证。
3. S11_REPORT_SEARCH。
4. S13_HISTORY_REPAIR_COUNT_CONFIRM。
5. S14_IMAGE_HORIZONTAL_SWIPE 每轮 fresh 成本。

