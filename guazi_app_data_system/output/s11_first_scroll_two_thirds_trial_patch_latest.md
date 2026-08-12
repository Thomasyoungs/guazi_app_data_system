# S11 首次 2/3 屏下滑试验补丁报告

## 状态

S11_FIRST_SCROLL_TWO_THIRDS_TRIAL_PATCHED

## 说明

本轮不更新页面契约文档。该改动只是固定脚本层面的试验性回归，用于比较 2/3 屏首次下滑与上一轮整屏 trial 的稳定性和效率。

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

S11_REPORT_SEARCH 的首次下滑从上一轮整屏 trial 改为 2/3 屏 trial：

- `s11_first_scroll_strategy="two_thirds_screen_trial"`
- `s11_first_scroll_screen_ratio=0.66`
- 首次请求距离：`screen_height * 0.66`
- 手势起点：屏幕约 86% 高度
- 手势终点下限：屏幕约 16% 高度
- 手势时长：950ms

第一次下滑之后，后续仍然是固定小幅下滑。

## 保留逻辑

- 当前屏已有完整可见且安全可点的精确“查看完整报告”时，不执行首次下滑，直接进入既有安全点击门禁。
- 第一次下滑后仍 fresh screenshot + XML。
- fresh evidence pair 校验保留。
- stale XML 检测保留。
- S11 内部截图/XML 可视区域一致性检测保留。
- 不恢复 normal/fine/backtrack。
- 不恢复弱信号切换。
- 不启用 OCR / 视觉识别 / 截图坐标点击。
- 不放松“查看完整报告”安全点击门禁。

## 验证结果

- `py_compile scripts/runtime_s10_to_s16_mainline.py`：通过
- module import：通过，输出 `IMPORT_OK`
- 离线 A-I 验证：全部通过

离线验证文件：

- `output/s11_first_scroll_two_thirds_trial_offline_validation.json`

## 下一步

补丁已通过离线验证。下一步运行雅阁正向样本实机回归，观察 2/3 屏首次下滑是否减少滑过入口、是否触发 stale XML / internal mismatch，以及是否最终进入 S16。
