# S04 可见目标车系提取失败诊断与最小修复

最终状态：`DIAGNOSE_AND_PATCH_S04_VISIBLE_TARGET_EXTRACTION_FAILED_AFTER_S03_V1_16_LOCK_DONE`

## 约束执行

- 未修改 S03
- 未恢复“只看新能源 / G / 滑动 / 品牌专区补救”等旧路径
- 未修改第二段
- 未启动第二段
- 未进入 S11
- 未采参考车
- 未定价
- 未处理品牌专区补救分支

## 诊断结论

失败状态：`S04_VISIBLE_TARGET_EXTRACTION_FAILED`

证据：

- XML：`artifacts/debug/s04_series_down_1_20260511_160350.xml`
- 截图：`artifacts/screenshots/s04_series_down_1_20260511_160350.png`

当前页面是标准 S04 车系页，不是品牌专区 / 混合列表。

页面中实际可见车系包括：

- 零跑S01
- 零跑C11
- 零跑C16
- 零跑C10
- 零跑B10
- 零跑A10
- 零跑D19

根因：

任务中的目标车系是 `C10`，但标准 S04 页面展示行是 `零跑C10`。旧逻辑按精确 `C10` 查找 `visible_series_names`，因此虽然 raw XML 包含 `C10`，但没有把 `零跑C10` 识别为同一目标车系。

诊断归类：

`S04_STANDARD_SERIES_VISIBLE_EXTRACTION_ALIAS_GAP`

## 最小修复

修改文件：

- `scripts/runtime_s01_to_s10_mainline.py`

修复范围只限 S04：

- 增加 `series_alias` 读取
- 增加 S04 目标车系 alias 生成
- 允许 `C10 / 零跑C10 / 零跑 C10` 绑定为同一目标车系
- 仍然只允许标准 S04 中点击目标车系同一行右侧“车型”
- 品牌专区 / 混合列表仍然只允许停止

新增 / 调整函数：

- `_s04_target_series_aliases(params, target_series)`
- `_s04_series_matches_target(series_name, target_aliases, target_series)`
- `_find_s04_series_item(snapshot, target_series, target_aliases)`
- `_find_series_model_button(snapshot, target_series, target_aliases)`

## 离线验证

对 `s04_series_down_1_20260511_160350.xml` 回放：

- `target_in_visible_series=true`
- 匹配车系：`零跑C10`
- 车系行 bounds：`[52, 1311, 1168, 1617]`
- 同行右侧“车型”按钮 bounds：`[869, 1361, 1129, 1540]`
- 点击目标：同一行右侧“车型”
- 品牌专区识别：false

`py_compile scripts/runtime_s01_to_s10_mainline.py`：通过

## 未做事项

本轮未运行实机，因此没有覆盖当前 result。

下一步如需验证，应只运行第一段，观察是否从 S04 正常进入 S05；仍禁止启动第二段。
