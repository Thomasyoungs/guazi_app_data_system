# S15 metal_deduct KeyError Reference Diff Diagnosis

## 结论

本轮只读诊断完成。KeyError: 'metal_deduct' 不是“有钣金就炸”，而是 reference #5 首次命中 ABC柱 + 钣金 的特殊结构件分支。

根因分类：D. 特殊结构件钣金分支字段命名不一致，兼有 S15 scoring config/default field initialization missing for metal_deduct branch。

## 异常位置

- 函数：src.guazi_app_data_system.pricing._body_score
- 行号：src/guazi_app_data_system/pricing.py:296
- 代码：deduct_map = scoring["metal_deduct"]
- 调用链：handle_s15 -> select_reference -> score_reference -> _score_common -> _body_score
- 阶段：S15 评分计算阶段；不是 scoring result 输出层、final candidate 或 S16 payload 阶段。

## reference #2/#3/#5 差异

| reference | score | 钣金/喷漆差异 | 是否触发 metal_deduct |
|---|---:|---|---|
| #2 | 83.0 | 左前门、左后门、后保险杠、前保险杠：普通覆盖件/保险杠钣金 | 否，普通钣金走 paint_deduct |
| #3 | 76.0 | 含 ABC柱，但异常为喷漆 | 否，ABC柱喷漆走 paint_deduct |
| #5 | 未生成 | 含 ABC柱 钣金 | 是，进入 scoring["metal_deduct"] 后缺键 |

## autoscroll 关系

reference #5 是本轮已处理参考车中第一个记录 s10_selected_card_autoscroll_attempted=true 的 S15 车辆；但证据显示 autoscroll 后 selected card 身份保持、字段完整、点击区安全，且 eference_score_input 包含过户、出险、金额、repair_counts、panel_repairs。未发现 autoscroll 导致 current_reference schema 缺评分字段。

## scoring keys

reference #2/#3 成功生成的 eference_score_components keys 一致：
ody_score / mileage_score / transfer_score / accident_score / max_amount_score。

reference #5 在 components 生成前即因 metal_deduct 缺键中断，因此没有 score components。

## 配置证据

config/fields.yaml 当前有：
- paint_deduct
- eplace_deduct

未发现：
- metal_deduct

## 下一步建议

下一轮如果允许 PATCH_ONLY，应修复评分配置 schema / 读取一致性，或输出明确配置缺失诊断。不要改扣分规则，不要用 metal_deduct=0 直接掩盖问题。
