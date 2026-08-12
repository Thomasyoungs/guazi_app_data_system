# S05 同配置多排放版本全选契约补丁

最终状态：`S05_SAME_TRIM_MULTI_EMISSION_VARIANT_CONTRACT_PATCHED`

## 修改范围
- 修改文件：`C:\Users\lzc93\Desktop\定价\guazi_app_data_system\scripts\runtime_s01_to_s10_mainline.py`
- 未运行实机，未启动第二段，未采参考车，未输出定价。
- `py_compile` 已通过。

## 新增契约
`S05_SAME_TRIM_MULTI_EMISSION_VARIANT_SELECT`：同年款、同一具体配置，仅排放标准不同的多行配置必须全部勾选。

## 新增能力
- 配置标准化：去除 `国V / 国VI / 国5 / 国6 / 国Ⅴ / 国Ⅵ` 后比较具体配置 identity。
- 只允许 `normalized_trim == normalized_target_config` 的行进入排放版本组。
- 多行组必须全部点击；点击后以 `已选N项` 验证，N 必须等于排放版本组数量。
- 多个疑似行但无法确认仅排放不同时停止：`S05_EMISSION_VARIANT_GROUP_NOT_CONFIRMED`。
- 未全选或数量不一致时停止：`S05_EMISSION_VARIANT_NOT_ALL_SELECTED` / `S05_SELECTED_COUNT_MISMATCH`。

## 离线测试
1. 目标 `2019款 180TURBO CVT尚悦版`：只选择 `国V` + `国VI` 两行，未选择全部车型、尚动版、尚擎版、220TURBO。
2. 目标 `2019款 180TURBO CVT尚悦版 国VI`：仍全选同配置排放组 `国V` + `国VI`。
3. 标准化验证：目标自带排放和不带排放会映射到同一个 normalized identity。

## 误选防护
- 不选择 2019款全部车型。
- 不选择不同动力 / 不同配置 / 不同版本。
- 不把相似名称车型纳入排放版本组。

## 后续
需要后续实机验证 S05 页面真实 checkbox/已选数量表现。
