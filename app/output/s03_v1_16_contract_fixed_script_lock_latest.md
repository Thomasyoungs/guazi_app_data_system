# S03 V1.16 固定脚本锁定报告

最终状态：`S03_V1_16_CONTRACT_FIXED_SCRIPT_LOCKED`

## 结论

S03 V1.16 页面契约已经沉淀到第一段固定脚本 `scripts/runtime_s01_to_s10_mainline.py`，作为通用执行标准，不是零跑专用逻辑。

本轮未运行实机、未启动第二段、未采参考车、未输出定价。

## 固定契约

当前屏可见目标品牌 alias 时，唯一允许动作：

`S03_ONLY_ALLOWED_ACTION_CLICK_BRAND_ROW_LEFT_ICON_SAFE_POINT`

当前屏不可见目标品牌 alias 时，唯一允许动作：

点击目标品牌首字母对应的右侧字母索引。零跑 / 零跑汽车 / LEAPMOTOR / Leapmotor 对应 `L`。

点击字母后必须 fresh，再重新执行 S03 契约判断。

## 通用函数

- `get_target_brand_initial(target_brand, target_brand_aliases)`
- `detect_visible_target_brand_alias(snapshot, target_brand_aliases)`
- `_find_right_letter_index_node(snapshot, target_initial)`
- `find_target_brand_row_bounds(snapshot, matched_alias)`
- `compute_brand_row_left_icon_safe_point(row_bounds, icon_bounds)`
- `validate_s03_brand_click_contract(snapshot, click_point, row_bounds)`
- `execute_s03_only_allowed_brand_click(snapshot, target_brand_aliases)`
- `_s03_contract_context_fields(context)`

## 结果字段

后续第一段结果可保留结构化 S03 契约字段，不写 raw XML / nodes / visible_blob：

- `s03_contract_version`
- `target_brand_aliases`
- `target_initial_letter`
- `target_brand_visible_before_letter`
- `clicked_initial_letter`
- `target_brand_visible_after_letter`
- `matched_brand_text`
- `brand_row_bounds`
- `selected_click_region_type`
- `selected_click_point`
- `after_click_page_type`
- `brand_zone_continuation_allowed`

## 旧路径状态

以下 S03 旧路径已禁用或不存在可执行分支：

- 点击“只看新能源”
- 点击 `G` 或非目标首字母
- 滑动找品牌
- 点击品牌名文字
- 点击整行中心
- 点击整行最右侧
- 点击品牌专区
- 品牌专区里找 `C10 / 零跑C10`
- 品牌专区里点击“车型配置”
- 品牌专区继续进入 S05 / S07 / S10

`retry` 残留仅属于 S05 年款重试逻辑，不属于 S03 品牌选择路径。

## 离线回放

场景 A：当前屏不可见零跑汽车，右侧 `L` 可见。

- 证据：`artifacts/debug/s02_to_s03_20260511_160338.xml`
- 结果：只允许点击 `L`
- 通过：是

场景 B：点击 `L` 后 fresh，当前屏可见零跑汽车。

- 证据：`artifacts/debug/s03_after_contract_initial_L_20260511_160342.xml`
- 结果：只允许点击品牌行最左侧图标安全点
- `selected_click_region_type=brand_row_left_icon_safe_point`
- 通过：是

场景 C：点击 `L` 后目标品牌仍不可见。

- 结果：STOP
- stop_code：`S03_TARGET_BRAND_NOT_VISIBLE_AFTER_INITIAL_LETTER`
- 通过：是

场景 D：品牌专区页。

- 证据：`artifacts/debug/s04_brand_zone_series_selected_20260510_145414.xml`
- `continuation_allowed=false`
- 不允许找 C10、不允许点车型配置、不允许进入 S05/S07/S10
- 通过：是

## 验证

- `py_compile scripts/runtime_s01_to_s10_mainline.py`：通过
- 残留检核：无 S03 可执行违约路径
- 实机：未运行
- 第二段：未启动
- 定价：未执行

## 下一步

允许进入下一步 S04 问题诊断：`S04_VISIBLE_TARGET_EXTRACTION_FAILED`。
