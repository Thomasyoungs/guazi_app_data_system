# Baseline Lock Check

生成时间：2026-05-09T02:48:11.950Z

baseline：`BASELINE_FOCUS_2017_FULL_CHAIN_PRICED_DONE_202605`

最终状态：`BASELINE_LOCK_CHECK_PASSED_READY_FOR_NEXT_TARGET`

## 一、结果文件存在且合法

- output/result_s01_to_s10.json：存在；JSON 合法=是
- output/result_s10_to_s16.json：存在；JSON 合法=是
- output/result.json：存在；JSON 合法=是
- output/full_chain_acceptance_report_latest.md：存在
- output/full_chain_acceptance_report.json：存在；JSON 合法=是
- output/baseline_freeze_package_latest.md：存在
- output/baseline_freeze_package.json：存在；JSON 合法=是

## 二、最终状态一致性

- result_s01_to_s10.status：S10_READY
- result_s10_to_s16.status：FULL_CHAIN_PRICED_DONE
- result.status：FULL_CHAIN_PRICED_DONE
- freeze_status：BASELINE_FREEZE_READY
- acceptance_status：FULL_CHAIN_ACCEPTANCE_PASSED_BASELINE_READY

## 三、Fingerprint 与旧目标污染检查

- 目标 fingerprint：福特|福克斯|2017款|两厢 1.6L 自动舒适型智行版|白|2017.07
- result fingerprint 一致：是
- result_s10_to_s16 fingerprint 一致：是
- 旧 MINI / 1.5T ONE 污染：否

## 四、reference_history 检查

- 规则口径：逐辆采集，达标即停；不按数量硬采满三辆。三同车源 / 有效样本少于三辆只作为样本不足 / 建议人工复核提示，不是硬门槛。
- reference_history 数量：3
- 第 1 辆：价格 2.52万；2017年 | 7.81万公里 | 唐山；分数 90.5 / 目标 93；不达标，继续第 2 辆。
- 第 2 辆：价格 2.55万；2017年 | 12.01万公里 | 唐山；分数 85 / 目标 93；不达标，继续第 3 辆。
- 第 3 辆：价格 2.62万；2017年 | 8.18万公里 | 唐山；分数 93.5 / 目标 93；达标，进入 S16 定价。

## 五、代码关键能力只读检查

- reference_index 续采逻辑：是
- 第 N 辆 S10 车卡唯一绑定：是
- S11_TOP_ONE_THIRD_IMAGE_ONLY_STANDARD：是
- 查看完整报告完整可见 + 安全区点击：是
- S11_TO_S12_WAIT_STABLE_AFTER_REPORT_CLICK：是
- S11_TO_S12 context 下 S12 优先于 S14：是
- S14 来源门禁：是
- reference_history 保留：是
- raw XML 不写入 result JSON：是

## 六、结论

`BASELINE_LOCK_CHECK_PASSED_READY_FOR_NEXT_TARGET`
