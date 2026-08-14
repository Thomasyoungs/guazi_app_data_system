# 吉利远景 2019 V1.22 完整链路运行报告

## 目标

- fingerprint: 吉利|远景|2019款|升级版 1.5L 手动豪华型 国VI|白|2019.10
- 目标: 吉利 远景 2019款 升级版 1.5L 手动豪华型 国VI / 白 / 2019.10

## V1.22 补丁与验证

- 修改文件: scripts/runtime_s01_to_s10_mainline.py
- 补丁范围: S03 品牌首字母确定性映射 / alias
- 吉利 / 吉利汽车 / GEELY / Geely -> J
- 离线验证: 通过
- py_compile: 通过

## 第一段结果

- 状态: S10_READY
- S10_READY: true
- S03: 当前屏不可见吉利，点击 J；fresh 后命中“吉利汽车”，点击品牌行最左侧图标安全点。
- S05: 2019款 + 升级版 1.5L 手动豪华型 国VI，已选2项。
- S05 排放版本组: 2019款 升级版 1.5L 手动豪华型 国V / 2019款 升级版 1.5L 手动豪华型 国VI
- S07: 白色 + 7 年车龄完成，查看4辆
- S10: 真实三同 4 辆，排除非三同 20 辆。

## 第二段结果

- 状态: S11_REPORT_ENTRY_FULL_VISIBILITY_NOT_ACHIEVED
- 最终状态: RUN_FAILED_WITH_ISSUE
- 第 1 辆参考车已完成采集，但 reference_score=94.0，低于目标分，继续下一辆。
- 第 2 辆参考车进入 S11 后，查看完整报告入口未完整可见，停止于安全点击门禁。

| reference_index | 价格 | 年份 / 里程 / 城市 | 过户 | 理赔次数 | 最大金额 | reference_score | 达标 |
|---:|---:|---|---:|---:|---:|---:|---|
| 1 | 2.04万 | 2019年 | 8.15万公里 | 唐山 | 0 | 0 | 0 | 94 | 否 |
| 2 | 2.09万 | 2019年 | 10.18万公里 | 安顺 | 1 |  |  |  | 未完成 |

## 阻断证据

- stop_code: S11_REPORT_ENTRY_FULL_VISIBILITY_NOT_ACHIEVED
- s11_report_scroll_count: 8
- report_entry_seen: false
- reason: not_seen
- screenshot: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s11_report_entry_search_8_20260512_152440.png
- xml: C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s11_report_entry_search_8_20260512_152440.xml

## 定价

未进入 S16，因此未输出最终定价。未使用非三同价格，未使用旧 ×95% 回款规则。

## 约束确认

- 未修改第二段脚本、pricing、config、DOCX。
- 未覆盖 baseline 文件。
- 所有业务动作继续通过 page_id + action_id。
- 页面契约不命中时停止，没有补救继续。
- result/report 未写 raw XML / nodes / visible_blob 大字段。

## 最终状态

RUN_FULL_CHAIN_GEELY_YUANJING_2019_AFTER_V1_22_BRAND_INITIAL_CONTRACT_DONE
