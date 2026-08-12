"""Device-ready and Guazi fast-entry preflight for Feishu confirmations.

This module is deliberately small and mock-friendly.  The live listener can
use it before a task enters the runnable queue; unit tests can exercise the
same decision rules without touching a real phone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from guazi_app_data_system.adb_device_gate import run_adb_device_gate  # noqa: E402
from guazi_app_data_system.adb_target_device import (  # noqa: E402
    TARGET_ADB_DEVICE_NOT_CONNECTED,
    TARGET_ADB_DEVICE_OFFLINE,
    TARGET_ADB_DEVICE_UNAUTHORIZED,
    TARGET_ADB_SERIAL_NOT_CONFIGURED,
)
from guazi_app_data_system.app_startup import (  # noqa: E402
    GUAZI_PACKAGE,
    ADBCommandResult,
    AdbClient,
    _extract_focused_window,
    _extract_foreground_package,
    _extract_resumed_activity,
    _is_keyguard_secure_from_window_dump,
    _is_keyguard_showing_from_window_dump,
)


PHONE_SCREEN_OFF = "PHONE_SCREEN_OFF"
PHONE_LOCKED_OR_INPUT_RESTRICTED = "PHONE_LOCKED_OR_INPUT_RESTRICTED"
NOTIFICATION_SHADE_BLOCKING = "NOTIFICATION_SHADE_BLOCKING"
SECURE_KEYGUARD_LOCKED = "SECURE_KEYGUARD_LOCKED"
PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED = "PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED"
RECOVERABLE_LOCK_OR_NOTIFICATION_SHADE = "RECOVERABLE_LOCK_OR_NOTIFICATION_SHADE"
FAST_ENTRY_RECOVERED = "FAST_ENTRY_RECOVERED"
FAST_ENTRY_RETRY_EXHAUSTED = "FAST_ENTRY_RETRY_EXHAUSTED"
DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY = "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY"
DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME = "DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME"
DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME = "DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME"
DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE = "DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE"
DEVICE_READY_FINAL_SNAPSHOT_STALE = "DEVICE_READY_FINAL_SNAPSHOT_STALE"
GUAZI_APP_FAST_ENTRY_READY = "GUAZI_APP_FAST_ENTRY_READY"
DEVICE_READY_FOR_PRICING = "DEVICE_READY_FOR_PRICING"
GUAZI_APP_NOT_FOREGROUND = "GUAZI_APP_NOT_FOREGROUND"
GUAZI_APP_FOREGROUND_FAILED_AFTER_RETRY = "GUAZI_APP_FOREGROUND_FAILED_AFTER_RETRY"
GUAZI_LOGIN_REQUIRED = "GUAZI_LOGIN_REQUIRED"
ADB_DEVICE_NOT_CONNECTED = "ADB_DEVICE_NOT_CONNECTED"
ADB_DEVICE_UNAUTHORIZED = "ADB_DEVICE_UNAUTHORIZED"
ADB_DEVICE_OFFLINE = "ADB_DEVICE_OFFLINE"
ADB_SERIAL_NOT_CONFIGURED = "ADB_SERIAL_NOT_CONFIGURED"

MIUI_LAUNCHER_PACKAGES = {"com.miui.home", "com.miui.newhome"}
DEVICE_READY_RECOVERY_LADDER_VERSION = "DEVICE_READY_ROBUST_RECOVERY_LADDER_V1_20260630"

BUSINESS_REPLY_BY_CODE = {
    PHONE_SCREEN_OFF: "\n".join(
        [
            "【本次定价未开始】",
            "原因：执行手机当前未亮屏。",
            "任务暂未进入定价队列，不会占用队列。",
            "请手动点亮并解锁手机后，重新回复“确认”。",
        ]
    ),
    PHONE_LOCKED_OR_INPUT_RESTRICTED: "\n".join(
        [
            "【本次定价未开始】",
            "原因：手机已亮屏，但仍处于锁屏或输入受限状态。",
            "任务暂未进入定价队列，不会占用队列。",
            "请手动解锁手机，停留在桌面或瓜子页面后，重新回复“确认”。",
        ]
    ),
    NOTIFICATION_SHADE_BLOCKING: "\n".join(
        [
            "【本次定价未开始】",
            "原因：手机当前停在通知栏或系统遮挡页面。",
            "任务暂未进入定价队列，不会占用队列。",
            "请先退出遮挡页面并解锁手机后，重新回复“确认”。",
        ]
    ),
    SECURE_KEYGUARD_LOCKED: "\n".join(
        [
            "【本次定价未开始】",
            "原因：手机需要手动解锁后才能开始定价。",
            "任务暂未进入定价队列，不会占用队列。",
            "请手动解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
        ]
    ),
    PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED: "\n".join(
        [
            "【本次定价未开始】",
            "原因：手机需要密码或生物识别解锁。",
            "任务暂未进入定价队列，不会占用队列。",
            "请手动解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
        ]
    ),
    FAST_ENTRY_RETRY_EXHAUSTED: "\n".join(
        [
            "【本次定价未开始】",
            "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP。",
            "任务暂未进入定价队列，不会占用队列。",
            "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
        ]
    ),
    DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY: "\n".join(
        [
            "【本次定价未开始】",
            "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP。",
            "任务暂未进入定价队列，不会占用队列。",
            "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
        ]
    ),
    DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME: "\n".join(
        [
            "【本次定价未开始】",
            "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP。",
            "任务暂未进入定价队列，不会占用队列。",
            "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
        ]
    ),
    DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME: "\n".join(
        [
            "【本次定价未开始】",
            "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP。",
            "任务暂未进入定价队列，不会占用队列。",
            "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
        ]
    ),
    DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE: "\n".join(
        [
            "【本次定价未开始】",
            "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP。",
            "任务暂未进入定价队列，不会占用队列。",
            "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
        ]
    ),
    DEVICE_READY_FINAL_SNAPSHOT_STALE: "\n".join(
        [
            "【本次定价未开始】",
            "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP。",
            "任务暂未进入定价队列，不会占用队列。",
            "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
        ]
    ),
    GUAZI_APP_FOREGROUND_FAILED_AFTER_RETRY: "\n".join(
        [
            "【本次定价未开始】",
            "手机已连接，但系统未能自动进入瓜子 APP。",
            "任务暂未进入定价队列，不会占用队列。",
            "请确认瓜子 APP 可正常打开后，再回复“确认”。",
        ]
    ),
    GUAZI_LOGIN_REQUIRED: "\n".join(
        [
            "【本次定价未开始】",
            "原因：瓜子 APP 需要重新登录。",
            "任务暂未进入定价队列，不会占用队列。",
            "请手动登录瓜子 APP 后，再回复“确认”。",
        ]
    ),
    ADB_SERIAL_NOT_CONFIGURED: "\n".join(
        [
            "【本次定价未开始】",
            "原因：未配置指定执行手机。",
            "任务暂未进入定价队列，不会占用队列。",
            "请联系管理员检查执行手机配置。",
        ]
    ),
    ADB_DEVICE_NOT_CONNECTED: "\n".join(
        [
            "【本次定价未开始】",
            "原因：指定执行手机未连接或当前不可见。",
            "任务暂未进入定价队列，不会占用队列。",
            "请确认指定执行手机已连接并保持授权后，重新回复“确认”。",
        ]
    ),
    ADB_DEVICE_UNAUTHORIZED: "\n".join(
        [
            "【本次定价未开始】",
            "原因：指定执行手机未完成本电脑授权。",
            "任务暂未进入定价队列，不会占用队列。",
            "请在手机上允许本电脑调试授权后，重新回复“确认”。",
        ]
    ),
    ADB_DEVICE_OFFLINE: "\n".join(
        [
            "【本次定价未开始】",
            "原因：指定执行手机当前离线。",
            "任务暂未进入定价队列，不会占用队列。",
            "请恢复手机连接后，重新回复“确认”。",
        ]
    ),
}

ADMIN_HINT_BY_CODE = {
    PHONE_SCREEN_OFF: "执行手机未亮屏，未入队，未启动执行脚本。",
    PHONE_LOCKED_OR_INPUT_RESTRICTED: "执行手机亮屏但仍被锁屏或输入限制拦截，未入队，未启动执行脚本。",
    NOTIFICATION_SHADE_BLOCKING: "执行手机当前被通知栏或系统遮挡页拦截，未入队，未启动执行脚本。",
    SECURE_KEYGUARD_LOCKED: "执行手机处于安全锁屏，未尝试 fast entry，未入队。",
    PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED: "执行手机需要密码或生物识别解锁，未尝试 fast entry，未入队。",
    FAST_ENTRY_RETRY_EXHAUSTED: "direct swipe fastpath 已尝试退出非安全锁屏或遮挡页，但瓜子未进入前台，未入队。",
    DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY: "direct swipe fastpath 已尝试退出非安全锁屏或遮挡页，但瓜子未进入前台，未入队。",
    DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME: "robust recovery ladder 已尝试 direct swipe、BACK、HOME 与瓜子重启，但最终仍未达到可入队前台状态。",
    DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME: "robust recovery ladder 已回到桌面/退出遮挡，但瓜子 APP 启动后未进入前台。",
    DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE: "robust recovery ladder 启动瓜子后仍被 NotificationShade 或系统遮挡抢焦点。",
    DEVICE_READY_FINAL_SNAPSHOT_STALE: "robust recovery ladder 结束后无法获得可靠 fresh final snapshot，未入队。",
    GUAZI_APP_FOREGROUND_FAILED_AFTER_RETRY: "fast entry 已尝试重新打开瓜子，但瓜子未进入前台，未入队。",
    GUAZI_LOGIN_REQUIRED: "瓜子 APP 登录态不可用，未入队。",
    ADB_SERIAL_NOT_CONFIGURED: "指定执行手机 serial 未配置，未入队。",
    ADB_DEVICE_NOT_CONNECTED: "指定执行手机在确认前快照中不可见，未入队。",
    ADB_DEVICE_UNAUTHORIZED: "指定执行手机在确认前快照中未授权，未入队。",
    ADB_DEVICE_OFFLINE: "指定执行手机在确认前快照中离线，未入队。",
}

BUSINESS_FORBIDDEN_TERMS = (
    "PowerShell",
    "dispatcher",
    "runner",
    "adb",
    "uiautomator",
    "status.json",
    "current_target_task",
    "pricing.lock",
    "keyguard",
    "USB debugging",
    "USB 调试",
    "USB 充电",
    "USB",
    "NotificationShade",
    "inputRestricted",
    "input restricted",
    "dumpsys",
    "XML",
)


def check_device_ready_for_pricing(
    *,
    task_id: str | None = None,
    client_factory: Callable[[], Any] | None = None,
    fast_entry_runner: Callable[..., dict[str, Any]] | None = None,
    now: Callable[[], datetime] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Run the live device-ready gate.

    The first pass is diagnostic-only.  If the phone is connected but only
    blocked by a recoverable shell state (for example a non-secure keyguard or
    notification shade), the gate attempts a bounded fast entry before the task
    is allowed into the runnable queue.
    """

    client = (client_factory or AdbClient)()
    snapshot = collect_device_ready_snapshot(client, now=now)
    result = classify_device_ready_snapshot(snapshot)
    if result.get("requires_fast_entry"):
        runner = fast_entry_runner or default_fast_entry_runner
        fast_entry = runner(
            client=client,
            snapshot=snapshot,
            reason_code=result.get("status"),
            task_id=task_id,
        )
        if fast_entry.get("ok"):
            merged_snapshot = {
                **snapshot,
                **(fast_entry.get("snapshot") if isinstance(fast_entry.get("snapshot"), dict) else {}),
            }
            recovered = _result(True, None, merged_snapshot)
            recovered["status"] = FAST_ENTRY_RECOVERED
            recovered["recoverable_status"] = result.get("status")
            recovered["recoverable_reason_codes"] = result.get("recoverable_reason_codes") or []
            recovered["recoverable_device_state"] = bool(result.get("recoverable_device_state"))
            recovered["fast_entry_attempted"] = True
            recovered["fast_entry_result"] = fast_entry
            recovered["fast_entry_recovered"] = True
            result = recovered
        else:
            error_code = str(
                fast_entry.get("error_code")
                or fast_entry.get("status")
                or FAST_ENTRY_RETRY_EXHAUSTED
            )
            failed = _result(False, error_code, snapshot)
            failed["status"] = error_code
            failed["recoverable_status"] = result.get("status")
            failed["recoverable_reason_codes"] = result.get("recoverable_reason_codes") or []
            failed["recoverable_device_state"] = bool(result.get("recoverable_device_state"))
            failed["fast_entry_attempted"] = True
            failed["fast_entry_result"] = fast_entry
            failed["fast_entry_recovered"] = False
            result = failed
    result["task_id"] = task_id
    result["snapshot"] = snapshot
    result["business_reply_text"] = _business_reply(result.get("error_code"))
    result["admin_reply_text"] = _admin_reply(task_id=task_id, result=result)
    return result


def collect_device_ready_snapshot(client: Any, *, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    timestamp = (now or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat()
    snapshot: dict[str, Any] = {
        "device_ready_snapshot_taken_at": timestamp,
        "target_adb_serial": str(getattr(client, "adb_serial", "") or ""),
        "adb_path": str(getattr(client, "adb_path", "") or ""),
        "adb_path_source": str(getattr(client, "adb_path_source", "") or ""),
        "adb_runtime_env_mode": "",
        "adb_gate": {},
        "adb_gate_passed": False,
        "screen_on": None,
        "interactive": None,
        "keyguard_showing": None,
        "secure_keyguard": False,
        "input_restricted": False,
        "notification_shade_showing": False,
        "focused_window": "",
        "foreground_package": "",
        "resumed_activity": "",
        "launcher_visible": False,
        "guazi_foreground": False,
        "guazi_fast_entry_ready": False,
        "guazi_force_reopen_required": False,
        "power_state": {},
        "window_dump_excerpt": "",
        "activity_dump_excerpt": "",
        "device_ready_commands": [
            "adb devices -l",
            "adb -s <target> shell dumpsys power",
            "adb -s <target> shell dumpsys window policy",
            "adb -s <target> shell dumpsys window",
            "adb -s <target> shell dumpsys activity top",
        ],
    }
    if hasattr(client, "runtime_environment_snapshot"):
        try:
            env_snapshot = client.runtime_environment_snapshot()
            snapshot.update({key: value for key, value in env_snapshot.items() if key not in snapshot or not snapshot.get(key)})
            snapshot["adb_runtime_env_mode"] = str(env_snapshot.get("adb_runtime_env_mode") or "")
        except Exception as exc:  # pragma: no cover - diagnostic only
            snapshot["runtime_environment_snapshot_error"] = str(exc)

    adb_gate = run_adb_device_gate(client, allow_transient_recovery=False)
    snapshot["adb_gate"] = adb_gate
    snapshot["adb_gate_passed"] = bool(adb_gate.get("passed"))
    for key in (
        "adb_devices_l_raw",
        "parsed_devices",
        "target_device_state",
        "target_device_present_before_first_stage",
        "device_snapshot_taken_at",
        "device_snapshot_error",
        "adb_command_preview",
        "target_serial",
        "status",
    ):
        if key in adb_gate:
            snapshot[key] = adb_gate.get(key)
    if not snapshot["adb_gate_passed"]:
        return snapshot

    power_state = _read_power_state(client)
    window_dump = _run_text(client, ["shell", "dumpsys", "window"], timeout=20)
    window_policy = _run_text(client, ["shell", "dumpsys", "window", "policy"], timeout=20)
    activity_dump = _run_text(client, ["shell", "dumpsys", "activity", "top"], timeout=20)

    snapshot["power_state"] = power_state
    snapshot["screen_on"] = _screen_on(power_state, window_policy, window_dump)
    snapshot["interactive"] = _interactive(power_state, window_policy, window_dump)
    snapshot["focused_window"] = _extract_focused_window(window_dump)
    snapshot["resumed_activity"] = _extract_resumed_activity(activity_dump)
    snapshot["foreground_package"] = _extract_foreground_package(window_dump, activity_dump)
    snapshot["keyguard_showing"] = _is_keyguard_showing_from_window_dump(window_dump)
    snapshot["secure_keyguard"] = _is_keyguard_secure_from_window_dump(window_dump)
    snapshot["input_restricted"] = _input_restricted(window_dump, window_policy)
    snapshot["notification_shade_showing"] = _notification_shade_showing(snapshot["focused_window"], window_dump)
    snapshot["launcher_visible"] = snapshot["foreground_package"] in MIUI_LAUNCHER_PACKAGES or any(
        package in snapshot["focused_window"] for package in MIUI_LAUNCHER_PACKAGES
    )
    snapshot["guazi_foreground"] = GUAZI_PACKAGE in {
        snapshot["foreground_package"],
        _package_from_component(snapshot["focused_window"]),
        _package_from_component(snapshot["resumed_activity"]),
    }
    snapshot["guazi_fast_entry_ready"] = bool(snapshot["guazi_foreground"]) and not bool(snapshot["keyguard_showing"])
    snapshot["guazi_force_reopen_required"] = bool(snapshot["guazi_foreground"])
    snapshot["window_dump_excerpt"] = _compact_excerpt(window_dump)
    snapshot["activity_dump_excerpt"] = _compact_excerpt(activity_dump)
    return snapshot


def classify_device_ready_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    adb_code = _adb_error_code(snapshot)
    if adb_code:
        return _result(False, adb_code, snapshot)
    if _password_or_biometric_required(snapshot):
        return _result(False, PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED, snapshot)
    if snapshot.get("secure_keyguard"):
        return _result(False, SECURE_KEYGUARD_LOCKED, snapshot)
    return _recoverable_result(
        RECOVERABLE_LOCK_OR_NOTIFICATION_SHADE,
        snapshot,
        [RECOVERABLE_LOCK_OR_NOTIFICATION_SHADE],
        recoverable_device_state=True,
    )


def default_fast_entry_runner(
    *,
    client: Any,
    snapshot: dict[str, Any],
    reason_code: str | None = None,
    task_id: str | None = None,
    poll_seconds: float = 9.0,
    poll_interval: float = 0.5,
    settle_seconds: float | None = None,
) -> dict[str, Any]:
    """Recover to Guazi foreground with a bounded wake/swipe/BACK/HOME ladder.

    Command success is only recorded as action delivery.  The phone is not
    considered ready until a fresh post-action snapshot proves Guazi foreground
    and no secure lock / NotificationShade focus remains.
    """

    started_at = datetime.now(timezone.utc).isoformat()
    actions: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    poll_snapshots: list[dict[str, Any]] = []
    settle = (
        float(getattr(client, "device_ready_settle_seconds", 0.9))
        if settle_seconds is None
        else float(settle_seconds)
    )

    def remember(stage: str, snap: dict[str, Any], *, source: str = "fresh_dumpsys_snapshot") -> dict[str, Any]:
        enriched = dict(snap or {})
        enriched.setdefault("snapshot_stage", stage)
        enriched.setdefault("snapshot_source", source)
        enriched.setdefault("snapshot_ts", datetime.now(timezone.utc).isoformat())
        enriched.setdefault("final_snapshot_fresh", True)
        snapshots.append(_recovery_snapshot_summary(enriched))
        return enriched

    def capture(stage: str) -> dict[str, Any]:
        try:
            return remember(stage, collect_device_ready_snapshot(client), source="collect_device_ready_snapshot")
        except Exception as exc:  # pragma: no cover - production diagnostic
            return remember(
                stage,
                {
                    "snapshot_capture_error": str(exc),
                    "final_snapshot_fresh": False,
                    "focused_window": "",
                    "foreground_package": "",
                    "guazi_foreground": False,
                },
                source="snapshot_capture_failed",
            )

    current = remember("stage_0_initial_preflight_snapshot", dict(snapshot or {}), source="preflight_initial_snapshot")

    wake_action = _run_fast_action(client, "wake_screen", ["shell", "input", "keyevent", "KEYCODE_WAKEUP"])
    actions.append(wake_action)
    _settle_device_ready(settle)
    current = capture("stage_1_after_wake_screen")
    if _secure_lock_required(current):
        return _fast_entry_result(
            ok=False,
            status=PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED if _password_or_biometric_required(current) else SECURE_KEYGUARD_LOCKED,
            reason_code=reason_code,
            task_id=task_id,
            started_at=started_at,
            actions=actions,
            snapshots=snapshots,
            snapshot=current,
            failed_final_verify_reason="secure_keyguard_or_password_required_after_wake",
        )

    if _needs_overlay_dismiss(current):
        before = current
        swipe_action = _run_direct_swipe_action(client)
        actions.append(swipe_action)
        _settle_device_ready(max(settle, 0.8))
        current = capture("stage_2_after_direct_swipe_dismiss")
        _annotate_action_transition(swipe_action, before, current)
    else:
        actions.append(
            {
                "name": "direct_swipe_dismiss_overlay",
                "skipped": True,
                "success": True,
                "skip_reason": "fresh_snapshot_did_not_require_overlay_dismiss",
            }
        )

    if _secure_lock_required(current):
        return _fast_entry_result(
            ok=False,
            status=PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED if _password_or_biometric_required(current) else SECURE_KEYGUARD_LOCKED,
            reason_code=reason_code,
            task_id=task_id,
            started_at=started_at,
            actions=actions,
            snapshots=snapshots,
            snapshot=current,
            failed_final_verify_reason="secure_keyguard_or_password_required_after_swipe",
        )

    if _blocking_overlay_present(current):
        before = current
        back_action = _run_fast_action(client, "back_fallback", ["shell", "input", "keyevent", "BACK"])
        back_action["back_fallback_attempted"] = True
        actions.append(back_action)
        _settle_device_ready(max(settle, 0.8))
        current = capture("stage_3_after_back_fallback")
        _annotate_action_transition(back_action, before, current, changed_key="back_fallback_changed_window")

    if _secure_lock_required(current):
        return _fast_entry_result(
            ok=False,
            status=PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED if _password_or_biometric_required(current) else SECURE_KEYGUARD_LOCKED,
            reason_code=reason_code,
            task_id=task_id,
            started_at=started_at,
            actions=actions,
            snapshots=snapshots,
            snapshot=current,
            failed_final_verify_reason="secure_keyguard_or_password_required_after_back",
        )

    if not _final_guazi_ready(current):
        before = current
        home_action = _run_fast_action(client, "home_fallback", ["shell", "input", "keyevent", "KEYCODE_HOME"])
        home_action["home_fallback_attempted"] = True
        actions.append(home_action)
        _settle_device_ready(max(settle, 0.8))
        current = capture("stage_4_after_home_fallback")
        _annotate_action_transition(home_action, before, current, changed_key="home_fallback_changed_window")

    force_stop = _run_fast_action(client, "clean_reopen_guazi_force_stop", ["shell", "am", "force-stop", GUAZI_PACKAGE])
    actions.append(force_stop)
    launch_result = _run_fast_action(
        client,
        "launch_guazi_app",
        ["shell", "monkey", "-p", GUAZI_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1"],
    )
    actions.append(launch_result)
    if not force_stop.get("success") or not launch_result.get("success"):
        return _fast_entry_result(
            ok=False,
            status=DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME,
            reason_code=reason_code,
            task_id=task_id,
            started_at=started_at,
            actions=actions,
            snapshots=snapshots,
            snapshot=current,
            failed_final_verify_reason="force_stop_or_launch_command_failed",
        )

    _settle_device_ready(max(settle, 1.5))
    deadline = time.monotonic() + max(0.0, poll_seconds)
    last_snapshot: dict[str, Any] = current
    while True:
        last_snapshot = _collect_foreground_snapshot(client)
        last_snapshot.setdefault("snapshot_ts", datetime.now(timezone.utc).isoformat())
        last_snapshot.setdefault("snapshot_source", "foreground_poll_snapshot")
        poll_snapshots.append(_recovery_snapshot_summary(last_snapshot))
        if _guazi_login_required(last_snapshot):
            return _fast_entry_result(
                ok=False,
                status=GUAZI_LOGIN_REQUIRED,
                reason_code=reason_code,
                task_id=task_id,
                started_at=started_at,
                actions=actions,
                snapshots=snapshots,
                poll_snapshots=poll_snapshots,
                snapshot=last_snapshot,
                failed_final_verify_reason="guazi_login_required_after_launch",
            )
        if _final_guazi_ready(last_snapshot):
            return _fast_entry_result(
                ok=True,
                status=FAST_ENTRY_RECOVERED,
                reason_code=reason_code,
                task_id=task_id,
                started_at=started_at,
                actions=actions,
                snapshots=snapshots,
                poll_snapshots=poll_snapshots,
                snapshot=last_snapshot,
            )
        if time.monotonic() >= deadline:
            break
        time.sleep(max(0.05, poll_interval))

    error_code, reason = _final_recovery_error(last_snapshot, actions)
    return _fast_entry_result(
        ok=False,
        status=error_code,
        reason_code=reason_code,
        task_id=task_id,
        started_at=started_at,
        actions=actions,
        snapshots=snapshots,
        poll_snapshots=poll_snapshots,
        snapshot=last_snapshot,
        failed_final_verify_reason=reason,
    )


def _fast_entry_result(
    *,
    ok: bool,
    status: str,
    reason_code: str | None,
    task_id: str | None,
    started_at: str,
    actions: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    snapshot: dict[str, Any],
    poll_snapshots: list[dict[str, Any]] | None = None,
    failed_final_verify_reason: str = "",
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "error_code": None if ok else status,
        "reason_code": reason_code,
        "task_id": task_id,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "actions": actions,
        "snapshots": snapshots,
        "poll_snapshots": poll_snapshots or [],
        "snapshot": snapshot,
        "device_ready_recovery_ladder_version": DEVICE_READY_RECOVERY_LADDER_VERSION,
        "confirm_preflight_uses_robust_ladder": True,
        "dispatcher_uses_robust_ladder": False,
        "confirm_ladder_not_weaker_than_runtime_device_ready_gate": True,
        "recovery_path_consistency_checked": True,
        "last_successful_action": _last_successful_action(actions),
        "failed_final_verify_reason": failed_final_verify_reason,
        "final_snapshot_fresh": _snapshot_fresh(snapshot),
        "final_focused_window": snapshot.get("focused_window"),
        "final_foreground_package": snapshot.get("foreground_package"),
        "final_guazi_foreground": bool(snapshot.get("guazi_foreground")),
    }


def _run_direct_swipe_action(client: Any) -> dict[str, Any]:
    width, height = _client_screen_size(client)
    if hasattr(client, "wake_swipe_once"):
        try:
            payload = _action_payload("direct_swipe_dismiss_overlay", client.wake_swipe_once(duration_ms=500))
        except TypeError:
            payload = _action_payload("direct_swipe_dismiss_overlay", client.wake_swipe_once())
    else:
        start_x = width // 2
        end_x = width // 2
        start_y = int(height * 0.82)
        end_y = int(height * 0.30)
        payload = _run_fast_action(
            client,
            "direct_swipe_dismiss_overlay",
            ["shell", "input", "swipe", str(start_x), str(start_y), str(end_x), str(end_y), "500"],
        )
        payload.update({"start_x": start_x, "start_y": start_y, "end_x": end_x, "end_y": end_y, "duration_ms": 500})
    payload.update(
        {
            "action_sent": True,
            "screen_size": [width, height],
            "direction": "up",
            "intent": "dismiss_non_secure_keyguard_or_notification_shade",
        }
    )
    return payload


def _client_screen_size(client: Any) -> tuple[int, int]:
    if hasattr(client, "screen_size"):
        try:
            width, height = client.screen_size()
            return int(width), int(height)
        except Exception:
            pass
    return 1080, 2400


def _settle_device_ready(seconds: float) -> None:
    if seconds <= 0:
        return
    time.sleep(seconds)


def _secure_lock_required(snapshot: dict[str, Any]) -> bool:
    return bool(snapshot.get("secure_keyguard")) or _password_or_biometric_required(snapshot)


def _needs_overlay_dismiss(snapshot: dict[str, Any]) -> bool:
    return bool(
        snapshot.get("keyguard_showing")
        or snapshot.get("input_restricted")
        or snapshot.get("notification_shade_showing")
        or "NotificationShade" in str(snapshot.get("focused_window") or "")
        or not snapshot.get("guazi_foreground")
    )


def _blocking_overlay_present(snapshot: dict[str, Any]) -> bool:
    return bool(
        snapshot.get("notification_shade_showing")
        or snapshot.get("input_restricted")
        or "NotificationShade" in str(snapshot.get("focused_window") or "")
    )


def _final_guazi_ready(snapshot: dict[str, Any]) -> bool:
    foreground = str(snapshot.get("foreground_package") or "")
    focused = str(snapshot.get("focused_window") or "")
    return bool(
        snapshot.get("guazi_foreground")
        and (foreground == GUAZI_PACKAGE or GUAZI_PACKAGE in foreground or GUAZI_PACKAGE in focused)
        and not snapshot.get("secure_keyguard")
        and not snapshot.get("notification_shade_showing")
        and "NotificationShade" not in focused
        and _snapshot_fresh(snapshot)
    )


def _snapshot_fresh(snapshot: dict[str, Any]) -> bool:
    if not isinstance(snapshot, dict) or snapshot.get("final_snapshot_fresh") is False:
        return False
    return bool(
        snapshot.get("snapshot_ts")
        or snapshot.get("device_ready_snapshot_taken_at")
        or snapshot.get("focused_window") is not None
        or snapshot.get("foreground_package") is not None
    )


def _annotate_action_transition(
    action: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    changed_key: str = "changed_window",
) -> None:
    before_focus = str(before.get("focused_window") or "")
    after_focus = str(after.get("focused_window") or "")
    before_pkg = str(before.get("foreground_package") or "")
    after_pkg = str(after.get("foreground_package") or "")
    changed = before_focus != after_focus or before_pkg != after_pkg
    action[changed_key] = changed
    action["before_focused_window"] = before_focus
    action["after_focused_window"] = after_focus
    action["before_foreground_package"] = before_pkg
    action["after_foreground_package"] = after_pkg
    action["state_changed_after_action"] = changed


def _final_recovery_error(snapshot: dict[str, Any], actions: list[dict[str, Any]]) -> tuple[str, str]:
    if not _snapshot_fresh(snapshot):
        return DEVICE_READY_FINAL_SNAPSHOT_STALE, "final_snapshot_not_fresh_or_missing"
    if snapshot.get("secure_keyguard"):
        return SECURE_KEYGUARD_LOCKED, "secure_keyguard_visible_after_recovery"
    if _password_or_biometric_required(snapshot):
        return PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED, "password_or_biometric_required_after_recovery"
    if _blocking_overlay_present(snapshot):
        return DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE, "notification_shade_or_input_restricted_after_launch"
    if any(action.get("name") == "home_fallback" for action in actions):
        return DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME, "home_fallback_completed_but_guazi_not_foreground"
    return DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME, "robust_recovery_ladder_exhausted_without_guazi_foreground"


def _recovery_snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_stage": snapshot.get("snapshot_stage"),
        "snapshot_ts": snapshot.get("snapshot_ts") or snapshot.get("device_ready_snapshot_taken_at"),
        "snapshot_source": snapshot.get("snapshot_source"),
        "screen_on": snapshot.get("screen_on"),
        "interactive": snapshot.get("interactive"),
        "keyguard_showing": snapshot.get("keyguard_showing"),
        "secure_keyguard": snapshot.get("secure_keyguard"),
        "input_restricted": snapshot.get("input_restricted"),
        "notification_shade_showing": snapshot.get("notification_shade_showing"),
        "focused_window": snapshot.get("focused_window"),
        "foreground_package": snapshot.get("foreground_package"),
        "resumed_activity": snapshot.get("resumed_activity"),
        "guazi_foreground": snapshot.get("guazi_foreground"),
        "final_snapshot_fresh": _snapshot_fresh(snapshot),
    }


def _last_successful_action(actions: list[dict[str, Any]]) -> str:
    for action in reversed(actions):
        if action.get("success"):
            return str(action.get("name") or "")
    return ""


def _result(ok: bool, error_code: str | None, snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": DEVICE_READY_FOR_PRICING if ok else error_code,
        "error_code": error_code,
        "device_ready_for_pricing": ok,
        "should_enqueue": ok,
        "should_start_runner": ok,
        "adb_device_connected": bool(snapshot.get("adb_gate_passed")),
        "screen_on": snapshot.get("screen_on"),
        "interactive": snapshot.get("interactive"),
        "keyguard_showing": snapshot.get("keyguard_showing"),
        "secure_keyguard": snapshot.get("secure_keyguard"),
        "input_restricted": snapshot.get("input_restricted"),
        "notification_shade_showing": snapshot.get("notification_shade_showing"),
        "focused_window": snapshot.get("focused_window"),
        "foreground_package": snapshot.get("foreground_package"),
        "launcher_visible": snapshot.get("launcher_visible"),
        "guazi_foreground": snapshot.get("guazi_foreground"),
        "guazi_fast_entry_ready": snapshot.get("guazi_fast_entry_ready"),
        "guazi_force_reopen_required": snapshot.get("guazi_force_reopen_required"),
        "recoverable_device_state": False,
        "requires_fast_entry": False,
        "fast_entry_attempted": False,
        "fast_entry_recovered": False,
        "diagnostics": _redact_snapshot(snapshot),
    }


def _recoverable_result(
    status: str,
    snapshot: dict[str, Any],
    reason_codes: list[str],
    *,
    recoverable_device_state: bool = True,
) -> dict[str, Any]:
    result = _result(False, None, snapshot)
    result.update(
        {
            "status": status,
            "error_code": None,
            "device_ready_for_pricing": bool(snapshot.get("adb_gate_passed")),
            "should_enqueue": False,
            "should_start_runner": False,
            "recoverable_device_state": recoverable_device_state,
            "requires_fast_entry": True,
            "recoverable_reason_codes": list(reason_codes),
        }
    )
    return result


def _adb_error_code(snapshot: dict[str, Any]) -> str | None:
    if snapshot.get("adb_gate_passed"):
        return None
    gate = snapshot.get("adb_gate") if isinstance(snapshot.get("adb_gate"), dict) else {}
    code = str(gate.get("status") or snapshot.get("status") or "")
    if code == TARGET_ADB_SERIAL_NOT_CONFIGURED:
        return ADB_SERIAL_NOT_CONFIGURED
    if code == TARGET_ADB_DEVICE_UNAUTHORIZED:
        return ADB_DEVICE_UNAUTHORIZED
    if code == TARGET_ADB_DEVICE_OFFLINE:
        return ADB_DEVICE_OFFLINE
    if code == TARGET_ADB_DEVICE_NOT_CONNECTED:
        return ADB_DEVICE_NOT_CONNECTED
    state = str(snapshot.get("target_device_state") or "")
    if state == "unauthorized":
        return ADB_DEVICE_UNAUTHORIZED
    if state == "offline":
        return ADB_DEVICE_OFFLINE
    if state in {"missing", "unknown", ""}:
        return ADB_DEVICE_NOT_CONNECTED
    return ADB_DEVICE_NOT_CONNECTED


def _read_power_state(client: Any) -> dict[str, Any]:
    if hasattr(client, "power_state"):
        try:
            state = client.power_state()
            if isinstance(state, dict):
                return state
        except Exception:
            return {}
    text = _run_text(client, ["shell", "dumpsys", "power"], timeout=20)
    return {
        "raw": text,
        "wakefulness": _match_group(r"mWakefulness=([A-Za-z]+)", text),
        "interactive": _parse_bool(_match_group(r"mInteractive=(true|false)", text)),
        "display_state": _match_group(r"Display Power: state=([A-Z_]+)", text),
    }


def _run_text(client: Any, args: list[str], *, timeout: int) -> str:
    if not hasattr(client, "run"):
        return ""
    result = client.run(args, timeout=timeout)
    if isinstance(result, ADBCommandResult):
        return result.stdout if result.success else result.stdout or result.stderr
    if isinstance(result, dict):
        return str(result.get("stdout") or result.get("stderr") or "")
    return str(getattr(result, "stdout", "") or getattr(result, "stderr", "") or "")


def _screen_on(power_state: dict[str, Any], window_policy: str, window_dump: str) -> bool | None:
    wakefulness = str(power_state.get("wakefulness") or "").lower()
    display = str(power_state.get("display_state") or "").upper()
    if wakefulness in {"awake", "waking"} or display == "ON":
        return True
    if wakefulness in {"asleep", "dozing"} or display in {"OFF", "DOZE"}:
        return False
    if "SCREEN_STATE_ON" in window_policy or "state=ON" in window_dump:
        return True
    if "SCREEN_STATE_OFF" in window_policy or "state=OFF" in window_dump:
        return False
    return None


def _interactive(power_state: dict[str, Any], window_policy: str, window_dump: str) -> bool | None:
    interactive = power_state.get("interactive")
    if isinstance(interactive, bool):
        return interactive
    if "INTERACTIVE_STATE_AWAKE" in window_policy or "mInteractive=true" in window_dump:
        return True
    if "INTERACTIVE_STATE_SLEEP" in window_policy or "mInteractive=false" in window_dump:
        return False
    return None


def _input_restricted(window_dump: str, window_policy: str) -> bool:
    combined = f"{window_dump}\n{window_policy}"
    return any(marker in combined for marker in ("inputRestricted=true", "mInputRestricted=true", "Input restricted=true"))


def _notification_shade_showing(focused_window: str, window_dump: str) -> bool:
    focused = focused_window or ""
    return "NotificationShade" in focused or "StatusBarNotificationPresenter" in window_dump


def _password_or_biometric_required(snapshot: dict[str, Any]) -> bool:
    if not snapshot.get("keyguard_showing") and not snapshot.get("input_restricted"):
        return False
    text = " ".join(
        str(snapshot.get(key) or "")
        for key in ("focused_window", "window_dump_excerpt", "activity_dump_excerpt")
    ).lower()
    markers = (
        "keyguardpasswordview",
        "keyguardpinview",
        "keyguardpatternview",
        "biometricprompt",
        "fingerprint",
        "passwordentry",
        "pinentry",
        "password",
    )
    return any(marker in text for marker in markers)


def _run_fast_action(client: Any, name: str, args: list[str]) -> dict[str, Any]:
    if name == "wake_screen" and hasattr(client, "wake_screen_once"):
        try:
            return _action_payload(name, client.wake_screen_once())
        except TypeError:
            pass
    if name in {"home_before_guazi_launch", "home_fallback"} and hasattr(client, "home_key_once"):
        try:
            return _action_payload(name, client.home_key_once())
        except TypeError:
            pass
    if name == "back_fallback" and hasattr(client, "back"):
        try:
            return _action_payload(name, client.back())
        except TypeError:
            pass
    if not hasattr(client, "run"):
        return {"name": name, "success": False, "error": "client_run_unavailable"}
    try:
        result = client.run(args, timeout=20)
    except Exception as exc:  # pragma: no cover - defensive production diagnostic
        return {"name": name, "success": False, "error": str(exc)}
    return _action_payload(name, result)


def _action_payload(name: str, result: Any) -> dict[str, Any]:
    stdout = str(getattr(result, "stdout", "") or (result.get("stdout") if isinstance(result, dict) else "") or "")
    stderr = str(getattr(result, "stderr", "") or (result.get("stderr") if isinstance(result, dict) else "") or "")
    return {
        "name": name,
        "success": _command_success(result),
        "returncode": getattr(result, "returncode", None) if not isinstance(result, dict) else result.get("returncode"),
        "stdout_excerpt": _compact_excerpt(stdout, limit=240),
        "stderr_excerpt": _compact_excerpt(stderr, limit=240),
    }


def _command_success(result: Any) -> bool:
    if isinstance(result, ADBCommandResult):
        return bool(result.success)
    if isinstance(result, dict):
        if "success" in result:
            return bool(result.get("success"))
        success_keys = [key for key in result if str(key).endswith("_success")]
        if success_keys:
            return all(bool(result.get(key)) for key in success_keys)
        if "returncode" in result:
            return int(result.get("returncode") or 0) == 0
        return True
    if hasattr(result, "success"):
        return bool(getattr(result, "success"))
    if hasattr(result, "returncode"):
        return int(getattr(result, "returncode") or 0) == 0
    return result is not None


def _collect_foreground_snapshot(client: Any) -> dict[str, Any]:
    window_dump = _run_text(client, ["shell", "dumpsys", "window"], timeout=20)
    activity_dump = _run_text(client, ["shell", "dumpsys", "activity", "top"], timeout=20)
    focused_window = _extract_focused_window(window_dump)
    resumed_activity = _extract_resumed_activity(activity_dump)
    foreground_package = _extract_foreground_package(window_dump, activity_dump)
    guazi_foreground = GUAZI_PACKAGE in {
        foreground_package,
        _package_from_component(focused_window),
        _package_from_component(resumed_activity),
    }
    return {
        "snapshot_ts": datetime.now(timezone.utc).isoformat(),
        "snapshot_source": "foreground_poll_snapshot",
        "final_snapshot_fresh": True,
        "focused_window": focused_window,
        "foreground_package": foreground_package,
        "resumed_activity": resumed_activity,
        "keyguard_showing": _is_keyguard_showing_from_window_dump(window_dump),
        "secure_keyguard": _is_keyguard_secure_from_window_dump(window_dump),
        "notification_shade_showing": _notification_shade_showing(focused_window, window_dump),
        "guazi_foreground": guazi_foreground,
        "guazi_fast_entry_ready": guazi_foreground,
        "guazi_force_reopen_required": guazi_foreground,
        "window_dump_excerpt": _compact_excerpt(window_dump),
        "activity_dump_excerpt": _compact_excerpt(activity_dump),
    }


def _guazi_login_required(snapshot: dict[str, Any]) -> bool:
    text = " ".join(
        str(snapshot.get(key) or "")
        for key in ("focused_window", "resumed_activity", "window_dump_excerpt", "activity_dump_excerpt")
    ).lower()
    return GUAZI_PACKAGE in text and any(marker in text for marker in ("login", "signin", "passport"))


def _business_reply(error_code: str | None) -> str:
    if not error_code:
        return ""
    return _strip_forbidden(BUSINESS_REPLY_BY_CODE.get(error_code, BUSINESS_REPLY_BY_CODE[PHONE_LOCKED_OR_INPUT_RESTRICTED]))


def _admin_reply(*, task_id: str | None, result: dict[str, Any]) -> str:
    error_code = str(result.get("error_code") or "")
    if not error_code:
        return ""
    lines = [
        f"【确认前设备就绪门禁】{task_id or ''}".strip(),
        f"状态：{error_code}",
        ADMIN_HINT_BY_CODE.get(error_code, "确认前设备就绪检查未通过，任务未入队。"),
    ]
    focused = result.get("focused_window")
    foreground = result.get("foreground_package")
    if focused:
        lines.append(f"focused_window={focused}")
    if foreground:
        lines.append(f"foreground_package={foreground}")
    fast_entry = result.get("fast_entry_result") if isinstance(result.get("fast_entry_result"), dict) else {}
    if fast_entry:
        ladder_version = fast_entry.get("device_ready_recovery_ladder_version")
        if ladder_version:
            lines.append(f"device_ready_recovery_ladder_version={ladder_version}")
        lines.append(f"final_guazi_foreground={bool(fast_entry.get('final_guazi_foreground'))}")
        last_success = fast_entry.get("last_successful_action")
        if last_success:
            lines.append(f"last_successful_action={last_success}")
        failed_reason = fast_entry.get("failed_final_verify_reason")
        if failed_reason:
            lines.append(f"failed_final_verify_reason={failed_reason}")
        action_names = [str(action.get("name") or "") for action in fast_entry.get("actions") or [] if isinstance(action, dict)]
        if action_names:
            lines.append("recovery_ladder_actions=" + " -> ".join(action_names))
    return "\n".join(lines)


def _redact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "device_ready_snapshot_taken_at",
        "target_adb_serial",
        "adb_path_source",
        "adb_runtime_env_mode",
        "adb_devices_l_raw",
        "parsed_devices",
        "target_device_state",
        "target_device_present_before_first_stage",
        "screen_on",
        "interactive",
        "keyguard_showing",
        "secure_keyguard",
        "input_restricted",
        "notification_shade_showing",
        "focused_window",
        "foreground_package",
        "resumed_activity",
        "launcher_visible",
        "guazi_foreground",
        "guazi_fast_entry_ready",
        "guazi_force_reopen_required",
        "adb_command_preview",
    )
    return {key: snapshot.get(key) for key in keep if key in snapshot}


def _strip_forbidden(text: str) -> str:
    cleaned = text
    for term in BUSINESS_FORBIDDEN_TERMS:
        cleaned = cleaned.replace(term, "")
    return cleaned


def _package_from_component(value: str) -> str:
    return value.split("/", 1)[0].strip() if value and "/" in value else ""


def _compact_excerpt(text: str, limit: int = 800) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    return compact[:limit]


def _match_group(pattern: str, text: str) -> str:
    match = re.search(pattern, text or "")
    return match.group(1) if match else ""


def _parse_bool(value: str) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None
