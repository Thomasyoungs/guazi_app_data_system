# 系统 baseline 与服务费定价规则锁定检查

最终状态：**SYSTEM_BASELINE_AND_PRICING_LOCK_CHECK_PASSED_READY_FOR_NEXT_TARGET**

本轮只读：未修改代码、页面契约文档、config、pricing，未运行实机，未覆盖 result.json，未删除 logs / artifacts / baseline 文件。

## 一、福特福克斯 baseline

- baseline_name：BASELINE_FOCUS_2017_FULL_CHAIN_PRICED_DONE_202605
- 状态：FULL_CHAIN_PRICED_DONE
- 最终参考车：第 3 辆
- 参考车价格：2.62万
- 目标车分：93
- 参考车分：93.5
- 旧规则建议收车价：20890
- 新服务费规则影响测算：19700
- 不覆盖旧 baseline 原结果：true

## 二、丰田致炫 baseline

- baseline_name：BASELINE_TOYOTA_YARIS_2015_FULL_CHAIN_PRICED_DONE_202605
- 状态：FULL_CHAIN_PRICED_DONE
- 最终参考车：第 2 辆
- 参考车价格：2.78万
- 目标车分：95
- 参考车分：95
- 旧规则建议收车价：22410
- 新服务费规则影响测算：21300
- 不覆盖旧 baseline 原结果：true

## 三、参考车采集规则

- 不固定采 2 辆：true
- 不固定采 3 辆：true
- 按 S10 reference_index 价格从低到高逐辆采集
- 每辆采完进入 S15 判断
- 字段完整、未淘汰、reference_score >= target_score 时立即达标即停
- 进入 S16 定价
- 三同车源 / 有效样本少于 3 辆只作为人工复核提示，不是硬门槛

## 四、S07 车龄隐藏刻度规则

- 可见刻度：0 / 2 / 4 / 6 / 8 / 10 / 不限
- 11 年：10 右侧第 1 个隐藏节点
- 12 年：10 右侧第 2 个隐藏节点
- 必须验证 11-11年 / 12-12年 后才允许 AGE_FILTER_DONE=true
- target_age > 12：不自动映射为“不限”

## 五、瓜子服务费阶梯定价规则

- 废弃：瓜子回款价 = 瓜子定价 × 95%
- 新规则：瓜子回款价 = 瓜子定价 - 瓜子服务费
- < 50000：2500
- >= 50000 and < 100000：3500
- >= 100000 and < 150000：4500
- >= 150000 and < 200000：6000
- >= 200000：8000
- pricing.py 使用 calc_guazi_service_fee()：true
- fields.yaml 使用 guazi_service_fee_tiers：true
- 活动定价代码无 0.95 回款价逻辑残留：true
- guazi_return_price_yuan 兼容字段等于 guazi_net_payout_yuan：true

## 六、文档检查

- OK C:\Users\lzc93\Desktop\定价\瓜子数据获取流程文档_V1.10_瓜子服务费阶梯定价规则冻结版.docx
- OK C:\Users\lzc93\Desktop\定价\定价逻辑备份_服务费阶梯修正版.docx

## 检查项

- focus_baseline_locked: true
- focus_status_ok: true
- toyota_baseline_locked: true
- toyota_status_ok: true
- reference_rule_locked: true
- s07_hidden_tick_rule_locked: true
- service_fee_rule_locked: true
- pricing_doc_exists: true
- process_doc_v110_exists: true
- pricing_py_exists: true
- fields_yaml_exists: true
- no_old_095_payout_logic: true

不一致项：无

最终状态：**SYSTEM_BASELINE_AND_PRICING_LOCK_CHECK_PASSED_READY_FOR_NEXT_TARGET**
