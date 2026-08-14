# S11 首次整屏下滑试验补丁报告

## 状态

S11_FIRST_SCROLL_FULL_SCREEN_TRIAL_PATCHED

## 说明

本轮未更新页面契约文档。该改动是固定脚本层面的试验性回归，用于观察 S11 首次整屏下滑对“查看完整报告”入口查找效率和稳定性的影响。

## 修改范围

仅修改：

- `scripts/runtime_s10_to_s16_mainline.py`

未修改：

- 第一段脚本
- 页面契约文档 / DOCX
- S10 selected card autoscroll
- S10 local title binding
- S11 fresh pair / stale XML 检测
- S11 internal visible-region mismatch 检测
- S11 unsafe reposition
- S12 / S13 / S14
- pricing / config / metal_deduct / baseline / 打分规则 / 定价规则

## 改动内容

S11_REPORT_SEARCH 的首次下滑从原来的约 1/3 屏，切换为试验性整屏请求：

- `s11_first_scroll_strategy="full_screen_trial"`
- `s11_first_scroll_screen_ratio=1.0`
- 首次请求距离：`screen_height * 1.0`
- 手势起点：屏幕约 90% 高度
- 手势终点下限：屏幕约 6% 高度
- 手势时长：1050ms

说明：实际手势距离受屏幕安全区域限制，但请求步长和试验策略会在结果字段中记录。

## 保留逻辑

- 当前屏已有完整可见且安全可点的精确“查看完整报告”时，不执行首次下滑，直接进入既有安全点击门禁。
- 第一次下滑后仍 fresh screenshot + XML。
- fresh evidence pair 校验保留。
- stale XML 检测保留。
- S11 内部截图/XML 可视区域一致性检测保留。
- 第一次之后仍使用固定小幅下滑。
- 不恢复 normal/fine/backtrack。
- 不恢复弱信号切换。
- 不启用 OCR / 视觉识别 / 截图坐标点击。
- 不放松“查看完整报告”安全点击门禁。

## 验证结果

- `py_compile scripts/runtime_s10_to_s16_mainline.py`：通过
- module import：通过，输出 `IMPORT_OK`
- 离线 A-I 验证：全部通过

离线验证文件：

- `output/s11_first_scroll_full_screen_trial_offline_validation.json`

## 下一步

补丁已通过离线验证。下一步按用户要求运行雅阁正向样本实机回归，观察首次整屏下滑的效率、是否滑过入口、是否触发 stale XML / internal mismatch，以及是否最终进入 S16。
