# 异常处理规范

异常配置来源于 `config/exceptions.yaml`。所有异常都必须先写入 issue 记录，再进入运行时学习循环。

## 统一处理顺序

1. 记录 issue。
2. 查询 `knowledge_base/solutions.jsonl`。
3. 只允许命中 `approved=true` 的 solution。
4. 只允许执行当前状态白名单内的 `allowed_auto_actions`。
5. 最多自动恢复 1 次。
6. 失败后停止并要求人工介入。

## 关键异常

| 编码 | 场景 | 处理 |
| --- | --- | --- |
| `ADB_UNAUTHORIZED` | 设备已识别但未授权 USB 调试 | 等待手机端人工确认 RSA 授权 |
| `DEVICE_NOT_FOUND` | 没有设备条目 | 先检查 USB 连接，再决定是否重启 adb |
| `DEVICE_LOCKED` | 仍处于安全锁屏或锁屏态 | 禁止绕过，等待人工解锁 |
| `SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP` | `NotificationShade`、锁屏遮罩或 keyguard 阻断业务页面契约 | 允许自动亮屏和 dismiss 非安全锁屏；若仍阻断则停止并人工处理 |
| `PAGE_CONTRACT_MISMATCH` | 当前页面不满足预期契约 | 停止业务点击，查询学习循环 |
| `POPUP_MARKETING_OVERLAY` | 已建契约的营销弹窗覆盖页面 | 只允许点击明确的 `X / 关闭` |

## 启动前恢复规则

- 允许自动执行：
  - `adb shell input keyevent KEYCODE_WAKEUP`
  - `adb shell wm dismiss-keyguard`
- 自动恢复后必须重新检查：
  - `mDreamingLockscreen`
  - `isKeyguardShowing`
  - `mFocusedWindow`
  - 前台 package
- 如果仍然是安全锁屏或 `NotificationShade`，必须停止并要求人工解锁或人工收起系统遮罩。
- 在恢复成功前，禁止点击任何业务页面元素。

## 明确禁止

- 不允许绕过密码、指纹、图案锁屏。
- 不允许在 `mDreamingLockscreen=true` 时点击业务按钮。
- 不允许在 `isKeyguardShowing=true` 时点击品牌。
- 不允许在 `mFocusedWindow=NotificationShade` 时点击 `大众` 或其他业务节点。
- 不允许使用坐标盲点关闭系统遮罩。
- 不允许在异常恢复阶段进入车源采集。

## Third-Party Overlay Gate

- Record `THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP` when a third-party login window or external overlay covers the verified Guazi flow.
- Allowed automatic actions stay limited to wake / dismiss non-secure keyguard, focus checks, launching the verified Guazi app, screenshot/XML capture, page recognition, and recovery through known audited paths back to `S03`.
- Business clicks remain forbidden until the overlay is gone and the page contract is verified again.

## Automatic Issue Classification

- Page-transition failures must call `issue_classifier` before writing a generic wrong-page code.
- The classifier reads `config/pages.yaml` and `config/actions.yaml`, compares the actual click target with the action contract, then chooses the issue code.
- Contract-proven failures can create or update `approved=true` solutions, but the solution still must pass state/action whitelists and `max_auto_retries=1`.
- Unknown pages, unknown popups, and unknown buttons are `candidate` only and cannot be automatically called.
- For S04, clicking the series card or series name instead of the target row's right-side `车型` button is `SERIES_ACTION_TARGET_MISMATCH`.
- `S07_CONTRACT_DRIFT_TO_GENERIC_FILTER`: if S07 planning drifts from the sole allowed `车型配置` entry to generic `筛选`, `颜色`, `年份`, sorting, vehicle card, detail, or collection actions, block before execution, record the issue, query the learning loop, and perform only read-only `detect_vehicle_model_config_entry` in the current round.
- `RIGHT_AGE_HANDLE_SET_NO_VERIFICATION`: after any right-handle correction attempt, if the right handle is not verified at `target_age`, the handles do not physically overlap, or target-age calculation is not verified, stop before confirm/view-result/list entry.
- `RIGHT_AGE_HANDLE_XML_NODE_MISIDENTIFIED`, `RIGHT_AGE_HANDLE_DRAG_TARGET_Y_MISMATCH`, `AGE_SLIDER_VISUAL_LAYER_XML_MIXED_WITH_BACKGROUND_LIST`, and `RIGHT_AGE_HANDLE_NEEDS_LONG_PRESS_DRAG` are candidate-only diagnostic causes. They can describe why a right-handle attempt failed, but cannot authorize another device drag until manually approved.
- For S08 age-slider recognition, the classifier must prefer `S08_AGE_EXACT_SLIDER_PANEL` when the panel and slider are visible. Background list nodes are noise and must not cause `S07_VEHICLE_LIST_PAGE` collection flow.

## ADB Transient Empty Device List

- `ADB_TRANSIENT_DEVICE_NOT_FOUND`: `adb` is available, but the first `adb devices -l` output is empty. Record issue, query learning loop, and only if an approved solution matches, run one `adb kill-server` / `adb start-server` recovery.
- `ADB_TRANSIENT_DEVICE_NOT_FOUND_RECOVERED`: the second `adb devices -l` returns `device` after the one allowed ADB server restart. This is an audit-worthy recovery state, not permission to skip gates.
- `DEVICE_NOT_FOUND`: only use after the approved transient recovery has run and `adb devices -l` is still empty. Stop for manual USB / MTP / cable / USB debugging checks.
- `ADB_UNAUTHORIZED` and `DEVICE_OFFLINE` do not use transient recovery; they stop immediately under their own rules.
- Until device state is exactly `device`, APP launch, screenshots, UI XML, page clicks, model-config/filter/sort/card clicks, collection, and pricing changes are forbidden.
