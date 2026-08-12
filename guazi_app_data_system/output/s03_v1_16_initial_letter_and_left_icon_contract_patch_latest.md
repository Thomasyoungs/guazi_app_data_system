# S03 V1.16 首字母与左侧图标点击契约补丁报告

最终状态：`S03_V1_16_INITIAL_LETTER_AND_LEFT_ICON_CONTRACT_PATCHED_AND_VERIFIED`

目标 fingerprint：`零跑|C10|2026款|210悦享版|白|2026.02`

## 修改范围

- 修改文件：`scripts/runtime_s01_to_s10_mainline.py`
- 未修改第二段脚本。
- 未修改 pricing / config / DOCX。
- 未启动第二段，未采参考车，未定价。

## S03 V1.16 契约实现

当前屏可见目标品牌 alias 时，唯一动作：

`S03_ONLY_ALLOWED_ACTION_CLICK_BRAND_ROW_LEFT_ICON_SAFE_POINT`

当前屏不可见目标品牌 alias 时，唯一动作：

`S03_ONLY_ALLOWED_ACTION_CLICK_TARGET_INITIAL_LETTER_L`

品牌专区 / 混合列表仍只允许停止，`continuation_allowed=false`。

## 新增 / 修改的通用能力

- `get_target_brand_initial`
- `_s03_brand_row_left_icon_bounds`
- `compute_brand_row_left_icon_safe_point`
- `execute_s03_only_allowed_brand_click`
- `_s03_brand_row_left_icon_safe_click_plan`
- `_s03_search_target_brand_v2`
- `handle_s03`

实现是通用品牌契约，不是零跑专用 if 分支。`零跑 / 零跑汽车 / LEAPMOTOR / Leapmotor -> L` 仅作为本轮验证样例。

## 旧路径清理

已删除 / 禁用以下 S03 可执行旧路径：

- 当前屏目标品牌可见时点击新能源、字母、滑动、品牌名、整行中心、整行最右侧、品牌专区。
- 当前屏目标品牌不可见时点击新能源、非目标首字母、滑动找品牌。
- 品牌专区页继续找 `C10 / 零跑C10`。
- 品牌专区页点击 `车型配置`。
- 品牌专区页继续进入 `S05 / S07 / S10`。
- `brand_row_right_safe_point` 不再是 S03 可执行点击策略。

## 残留检核

关键词残留均为不可执行字段、stop_code、防御识别或非 S03 流程文本。未发现 S03 可执行违约路径。

重点结论：

- `click_new_energy_tab` 不存在。
- `scroll_brand_list` 不存在于 S03 执行路径。
- `brand_row_right_safe_point` 不存在；仅保留 `attempted_row_right_click=false` 审计字段。
- `BRAND_ZONE_MIXED_LIST` 只用于阻断，不允许继续。
- `车型配置` 仅属于正常 S06 或品牌专区阻断证据，不是品牌专区继续分支。

## 离线回放

通过。

- 当前屏可见 `零跑汽车`：生成 `brand_row_left_icon_safe_point`，点击点 `[117, 1075]`。
- 当前屏不可见 `零跑汽车` 且右侧 `L` 可见：唯一动作是点击 `L`。
- 点击 `L` 后目标品牌可见：下一步为左侧品牌图标安全点。
- 点击 `L` 后目标仍不可见：停止 `S03_TARGET_BRAND_NOT_VISIBLE_AFTER_INITIAL_LETTER`。
- 品牌专区页：识别为 blocked，禁止找 C10、禁止点车型配置、禁止进入 S05/S07/S10。

## 实机第一段验证

已绑定真机：`6TGYYHPZCETCSK6L`

第一段终止状态：`S04_VISIBLE_TARGET_EXTRACTION_FAILED`

S03 验证结果：

- 初始 S03：目标品牌不可见，按契约点击右侧 `L`。
- `L` 后 fresh：目标品牌可见。
- 随后点击目标品牌行最左侧品牌图标安全点。
- 未点击新能源、G、滑动、品牌名、整行中心、整行最右侧或品牌专区。
- 点击后进入标准 S04，未进入品牌专区。

本轮后续阻塞点是 S04：`S04_VISIBLE_TARGET_EXTRACTION_FAILED`，不是 S03 契约问题。

## 证据路径

- 初始 S03 XML：`artifacts/debug/s02_to_s03_20260511_160338.xml`
- 点击 L 后 XML：`artifacts/debug/s03_after_contract_initial_L_20260511_160342.xml`
- 品牌点击后 XML：`artifacts/debug/s03_to_s04_20260511_160346.xml`
- 运行日志：`logs/s03_v1_16_first_stage_20260511_160316.log`
- 结果文件：`output/result_s01_to_s10.json`

## 结论

`S03_V1_16_INITIAL_LETTER_AND_LEFT_ICON_CONTRACT_PATCHED_AND_VERIFIED`

允许进入下一步：`true`
