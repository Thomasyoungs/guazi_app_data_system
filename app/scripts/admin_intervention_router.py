"""Production-mode routing for system/admin intervention.

The helpers here only write local preview/dry-run artifacts. They do not send
Feishu messages and do not touch APP automation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

try:
    from feishu_send_message import build_text_message_payload
except ImportError:  # pragma: no cover - supports package-style imports in tests.
    from scripts.feishu_send_message import build_text_message_payload


SYSTEM_BLOCKED = "SYSTEM_BLOCKED"
ADMIN_INTERVENTION_REQUIRED = "ADMIN_INTERVENTION_REQUIRED"
ADMIN_INTERVENTION_RESOLVED = "ADMIN_INTERVENTION_RESOLVED"
TARGET_INFO_NEEDS_CORRECTION = "TARGET_INFO_NEEDS_CORRECTION"

SYSTEM_ENVIRONMENT_ERROR_CODES = {
    "HUMAN_LOGIN_REQUIRED",
    "LOGIN_REQUIRED_MANUAL",
    "APP_LOGIN_REQUIRED",
    "TARGET_ADB_SERIAL_NOT_CONFIGURED",
    "TARGET_ADB_DEVICE_NOT_CONNECTED",
    "TARGET_ADB_DEVICE_UNAUTHORIZED",
    "TARGET_ADB_DEVICE_OFFLINE",
    "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT",
    "ADB_DEVICE_NOT_FOUND",
    "ADB_SERIAL_NOT_CONFIGURED",
    "ADB_DEVICE_NOT_CONNECTED",
    "ADB_DEVICE_UNAUTHORIZED",
    "ADB_DEVICE_OFFLINE",
    "ADB_UNAUTHORIZED",
    "DEVICE_OFFLINE",
    "DEVICE_AUTH_REQUIRED",
    "APP_NOT_INSTALLED",
    "APP_NOT_READY",
    "APP_NO_RESPONSE",
    "PHONE_NOT_AWAKE",
    "PHONE_LOCKED",
    "PHONE_SCREEN_OFF",
    "PHONE_LOCKED_OR_INPUT_RESTRICTED",
    "NOTIFICATION_SHADE_BLOCKING",
    "SECURE_KEYGUARD_LOCKED",
    "PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED",
    "FAST_ENTRY_RETRY_EXHAUSTED",
    "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY",
    "DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME",
    "DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME",
    "DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE",
    "DEVICE_READY_FINAL_SNAPSHOT_STALE",
    "GUAZI_APP_FOREGROUND_FAILED_AFTER_RETRY",
    "GUAZI_LOGIN_REQUIRED",
    "PRICING_LOCK_EXISTS",
    "ACTIVE_APP_TASK_EXISTS",
}

RECOVERABLE_ADMIN_ERROR_CODES = {
    "HUMAN_LOGIN_REQUIRED",
    "LOGIN_REQUIRED_MANUAL",
    "APP_LOGIN_REQUIRED",
    "TARGET_ADB_DEVICE_NOT_CONNECTED",
    "TARGET_ADB_DEVICE_UNAUTHORIZED",
    "TARGET_ADB_DEVICE_OFFLINE",
    "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT",
    "ADB_DEVICE_NOT_FOUND",
    "ADB_SERIAL_NOT_CONFIGURED",
    "ADB_DEVICE_NOT_CONNECTED",
    "ADB_DEVICE_UNAUTHORIZED",
    "ADB_DEVICE_OFFLINE",
    "ADB_UNAUTHORIZED",
    "DEVICE_OFFLINE",
    "DEVICE_AUTH_REQUIRED",
    "APP_NOT_READY",
    "APP_NO_RESPONSE",
    "PHONE_NOT_AWAKE",
    "PHONE_LOCKED",
    "PHONE_SCREEN_OFF",
    "PHONE_LOCKED_OR_INPUT_RESTRICTED",
    "NOTIFICATION_SHADE_BLOCKING",
    "SECURE_KEYGUARD_LOCKED",
    "PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED",
    "FAST_ENTRY_RETRY_EXHAUSTED",
    "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY",
    "DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME",
    "DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME",
    "DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE",
    "DEVICE_READY_FINAL_SNAPSHOT_STALE",
    "GUAZI_APP_FOREGROUND_FAILED_AFTER_RETRY",
    "GUAZI_LOGIN_REQUIRED",
}

AUTO_HEALTH_RECOVERABLE_ERROR_CODES = {
    "HUMAN_LOGIN_REQUIRED",
    "LOGIN_REQUIRED_MANUAL",
    "APP_LOGIN_REQUIRED",
    "TARGET_ADB_DEVICE_NOT_CONNECTED",
    "TARGET_ADB_DEVICE_UNAUTHORIZED",
    "TARGET_ADB_DEVICE_OFFLINE",
    "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT",
    "ADB_DEVICE_NOT_FOUND",
    "ADB_SERIAL_NOT_CONFIGURED",
    "ADB_DEVICE_NOT_CONNECTED",
    "ADB_DEVICE_UNAUTHORIZED",
    "ADB_DEVICE_OFFLINE",
    "ADB_UNAUTHORIZED",
    "DEVICE_OFFLINE",
    "DEVICE_AUTH_REQUIRED",
    "APP_NOT_READY",
    "APP_NO_RESPONSE",
    "PHONE_NOT_AWAKE",
    "PHONE_LOCKED",
    "PHONE_SCREEN_OFF",
    "PHONE_LOCKED_OR_INPUT_RESTRICTED",
    "NOTIFICATION_SHADE_BLOCKING",
    "SECURE_KEYGUARD_LOCKED",
    "PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED",
    "FAST_ENTRY_RETRY_EXHAUSTED",
    "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY",
    "DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME",
    "DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME",
    "DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE",
    "DEVICE_READY_FINAL_SNAPSHOT_STALE",
    "GUAZI_APP_FOREGROUND_FAILED_AFTER_RETRY",
    "GUAZI_LOGIN_REQUIRED",
}

PAGE_OR_PROGRAM_ERROR_CODES = {
    "PAGE_CONTRACT_MISMATCH",
    "S10_NOT_READY",
    "FIRST_STAGE_NOT_S10_READY",
    "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY",
    "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED",
    "S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE",
    "S13_REPAIR_ITEM_CLICK_DID_NOT_OPEN_DETAIL",
    "S13_HISTORY_REPAIR_ENTRY_NOT_VISIBLE_OR_UNBOUND",
    "S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND",
    "S14_STALE_FIRST_LINE_BINDING_UNRESOLVED",
    "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED",
    "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED",
    "S14_CONTRACT_DEGRADED_NEEDS_REVIEW",
    "S14_DEGRADED_COLLECTION_THRESHOLD_EXCEEDED",
    "S14_DETAIL_POPUP_CLOSE_UNSAFE_OR_FAILED",
    "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE",
    "S14_COLLECTION_INCOMPLETE_UNRECOVERABLE",
    "REFERENCE_SCORE_UNTRUSTED_INCOMPLETE_COLLECTION",
    "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED",
    "RESULT_SCHEMA_INVALID_FOR_PRICING",
    "SECOND_STAGE_RUNTIME_EXCEPTION",
    "V149_REFERENCE_IDENTITY_SUMMARY_TYPE_ERROR",
    "RESULT_FORMAT_FAILED",
    "FIRST_STAGE_SCHEMA_INVALID",
    "MAIN_SCRIPT_FAILED",
}

TARGET_INFO_ERROR_CODES = {
    TARGET_INFO_NEEDS_CORRECTION,
    "TARGET_TASK_FIELD_MISSING",
    "TARGET_REQUIRED_FIELD_MISSING",
    "TARGET_DATE_UNRECOGNIZED",
    "TARGET_MODEL_UNRECOGNIZED",
    "TARGET_BRAND_SERIES_INFERENCE_FAILED",
    "TARGET_BRAND_SERIES_CONFLICT",
    "TARGET_FIELD_FORMAT_INVALID",
}

NON_AUTO_RECOVERABLE_ERROR_CODES = TARGET_INFO_ERROR_CODES | PAGE_OR_PROGRAM_ERROR_CODES | {
    "RULE_SOURCE_CONFLICT",
}

DEFAULT_HEALTH_CHECK_COOLDOWN_SECONDS = 600
DEFAULT_ADMIN_NOTICE_COOLDOWN_SECONDS = 600

INTERNAL_BUSINESS_FORBIDDEN_TERMS = (
    "PowerShell",
    "adb",
    "uiautomator",
    "runner",
    "pricing_runner",
    "dispatcher",
    "python",
    "--run-first-stage",
    "--run-second-stage",
    "--run-manual",
    "--manual-confirm-price",
    "--revalidate-result",
    "--resolve-admin-intervention",
    "current_target_task.json",
    "status.json",
    "run_id",
    "technical_status",
    "ADMIN_INTERVENTION_REQUIRED",
)

ADMIN_RECOVERY_COMMANDS = {"已处理", "恢复运行", "继续队列"}


def now_iso(clock: Callable[[], datetime] | None = None) -> str:
    now = clock() if clock else datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.isoformat(timespec="seconds")
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def collect_error_codes(*, errors: list[str] | None = None, result: dict[str, Any] | None = None) -> list[str]:
    codes: list[str] = [str(item) for item in (errors or []) if item]
    if isinstance(result, dict):
        for key in ("status", "final_status", "current_state", "stop_code", "issue_code", "error_code", "last_error_code"):
            value = result.get(key)
            if value:
                codes.append(str(value))
        result_errors = result.get("errors")
        if isinstance(result_errors, list):
            codes.extend(str(item) for item in result_errors if item)
        run_meta = result.get("run_meta")
        if isinstance(run_meta, dict):
            meta_errors = run_meta.get("errors")
            if isinstance(meta_errors, list):
                codes.extend(str(item) for item in meta_errors if item)
    return _dedupe(codes)


def classify_admin_intervention(*, errors: list[str] | None = None, result: dict[str, Any] | None = None) -> dict[str, Any]:
    codes = collect_error_codes(errors=errors, result=result)
    code_set = set(codes)
    if code_set & TARGET_INFO_ERROR_CODES:
        return {
            "category": "target_info",
            "status": TARGET_INFO_NEEDS_CORRECTION,
            "business_status": TARGET_INFO_NEEDS_CORRECTION,
            "technical_status": "VALIDATION_FAILED",
            "recommended_next_action": "ask-sender-to-resend-target-info",
            "recoverable_by_admin": False,
            "error_codes": codes,
        }
    if code_set & SYSTEM_ENVIRONMENT_ERROR_CODES:
        recoverable = bool(code_set & RECOVERABLE_ADMIN_ERROR_CODES)
        return {
            "category": "system_environment",
            "status": SYSTEM_BLOCKED,
            "business_status": SYSTEM_BLOCKED,
            "technical_status": "FAILED",
            "recommended_next_action": "wait-admin-resolution",
            "recoverable_by_admin": recoverable,
            "error_codes": codes,
        }
    return {
        "category": "page_or_program",
        "status": ADMIN_INTERVENTION_REQUIRED,
        "business_status": ADMIN_INTERVENTION_REQUIRED,
        "technical_status": "FAILED",
        "recommended_next_action": "notify-admin",
        "recoverable_by_admin": bool(code_set & RECOVERABLE_ADMIN_ERROR_CODES),
        "error_codes": codes,
    }


def format_business_system_processing_reply(task_id: str, classification: dict[str, Any]) -> str:
    if classification.get("category") == "system_environment":
        text = "\n".join(
            [
                f"【系统处理中】{task_id}",
                "系统定价暂未完成，已通知管理员处理。",
                "处理完成后系统会自动继续。",
            ]
        )
    else:
        text = "\n".join(
            [
                f"【系统处理中】{task_id}",
                "系统定价暂未完成，已转管理员处理。",
            ]
        )
    return _strip_forbidden_terms(text)


def format_admin_intervention_reply(task_id: str, classification: dict[str, Any]) -> str:
    code = _primary_error_code(classification)
    reason = _admin_reason_for_code(code)
    action = _admin_action_for_code(code)
    return "\n".join(
        [
            f"【管理员处理】{task_id}",
            f"问题：{reason}",
            action,
            "处理完成后回复：确认。",
        ]
    )


def format_system_not_recovered_reply(
    task_id: str,
    *,
    health_result: dict[str, Any] | None = None,
    error_codes: list[str] | None = None,
) -> str:
    health_result = health_result or {}
    codes = set(collect_error_codes(errors=error_codes, result=health_result))
    adb_status = "异常" if codes & {
        "ADB_UNAUTHORIZED",
        "DEVICE_AUTH_REQUIRED",
        "DEVICE_OFFLINE",
        "TARGET_ADB_DEVICE_NOT_CONNECTED",
        "TARGET_ADB_DEVICE_UNAUTHORIZED",
        "TARGET_ADB_DEVICE_OFFLINE",
        "TARGET_ADB_SERIAL_NOT_CONFIGURED",
    } else "未确认"
    login_status = "疑似未登录" if codes & {"HUMAN_LOGIN_REQUIRED", "LOGIN_REQUIRED_MANUAL", "APP_LOGIN_REQUIRED"} else "未确认"
    popup_status = "疑似有阻挡" if codes & {"PHONE_LOCKED", "APP_NO_RESPONSE"} else "未确认"
    app_status = "未安装" if "APP_NOT_INSTALLED" in codes else "无响应" if "APP_NO_RESPONSE" in codes else "未确认"
    return "\n".join(
        [
            f"【系统暂未恢复】{task_id}",
            "",
            "系统暂时还不能开始定价，请管理员确认：",
            "",
            "1. 手机已连接电脑",
            "2. ADB 已授权",
            "3. 瓜子 APP 已登录并停在首页",
            "4. 没有弹窗阻挡",
            "",
            f"* ADB：{adb_status}",
            f"* 瓜子登录：{login_status}",
            f"* 弹窗：{popup_status}",
            f"* APP：{app_status}",
            "",
            "处理好后请再次回复：确认",
        ]
    )


def write_admin_intervention_feedback(
    *,
    task_dir: str | Path,
    task_id: str,
    status_payload: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    result: dict[str, Any] | None = None,
    dry_run: bool = True,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    task_dir = Path(task_dir)
    status_payload = status_payload or {}
    classification = classify_admin_intervention(errors=errors, result=result)
    business_reply = format_business_system_processing_reply(task_id, classification)
    admin_reply = format_admin_intervention_reply(task_id, classification)
    business_chat_id = _first_present_value(status_payload, ("business_chat_id", "raw_chat_id", "chat_id"))
    admin_chat_id = _first_present_value(status_payload, ("admin_chat_id", "admin_notify_chat_id"))
    delivery = {
        "ok": True,
        "dry_run": dry_run,
        "task_id": task_id,
        "status": classification["status"],
        "business_status": classification["business_status"],
        "technical_status": classification["technical_status"],
        "recommended_next_action": classification["recommended_next_action"],
        "business_chat_id": business_chat_id,
        "admin_chat_id": admin_chat_id,
        "classification": classification,
        "business_reply_text": business_reply,
        "admin_reply_text": admin_reply,
        "business_reply_payload": build_text_message_payload(business_reply),
        "admin_reply_payload": build_text_message_payload(admin_reply),
        "created_at": now_iso(clock),
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "business_system_processing_reply.preview.txt").write_text(business_reply + "\n", encoding="utf-8")
    (task_dir / "admin_intervention_reply.preview.txt").write_text(admin_reply + "\n", encoding="utf-8")
    (task_dir / "admin_intervention_delivery.json").write_text(
        json.dumps(delivery, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return delivery


def detect_admin_recovery_command(text: str) -> tuple[bool, str | None]:
    stripped = str(text or "").strip()
    if not stripped:
        return False, None
    import re

    task_match = re.search(r"FS\d{8}_\d{4}", stripped)
    task_id = task_match.group(0) if task_match else None
    without_task = re.sub(r"FS\d{8}_\d{4}", "", stripped).strip()
    if stripped in ADMIN_RECOVERY_COMMANDS or without_task in ADMIN_RECOVERY_COMMANDS:
        return True, task_id
    return False, None


def is_recoverable_admin_error(errors: list[str] | None = None, result: dict[str, Any] | None = None) -> bool:
    return bool(set(collect_error_codes(errors=errors, result=result)) & RECOVERABLE_ADMIN_ERROR_CODES)


def is_auto_health_recoverable_error(errors: list[str] | None = None, result: dict[str, Any] | None = None) -> bool:
    codes = set(collect_error_codes(errors=errors, result=result))
    if codes & NON_AUTO_RECOVERABLE_ERROR_CODES:
        return False
    return bool(codes & AUTO_HEALTH_RECOVERABLE_ERROR_CODES)


def _primary_error_code(classification: dict[str, Any]) -> str | None:
    for code in classification.get("error_codes") or []:
        if code not in {SYSTEM_BLOCKED, ADMIN_INTERVENTION_REQUIRED, "FAILED"}:
            return str(code)
    codes = classification.get("error_codes") or []
    return str(codes[0]) if codes else None


def _admin_reason_for_code(code: str | None) -> str:
    if code == "TARGET_ADB_SERIAL_NOT_CONFIGURED":
        return "未配置指定执行手机。"
    if code == "TARGET_ADB_DEVICE_NOT_CONNECTED":
        return "指定执行手机未连接。"
    if code == "TARGET_ADB_DEVICE_UNAUTHORIZED":
        return "指定执行手机调试授权不可用。"
    if code == "TARGET_ADB_DEVICE_OFFLINE":
        return "指定执行手机离线。"
    if code == "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT":
        return "指定执行手机连接不稳定。"
    if code == "ADB_SERIAL_NOT_CONFIGURED":
        return "未配置指定执行手机。"
    if code == "ADB_DEVICE_NOT_CONNECTED":
        return "指定执行手机未连接或当前不可见。"
    if code == "ADB_DEVICE_UNAUTHORIZED":
        return "指定执行手机调试授权不可用。"
    if code == "ADB_DEVICE_OFFLINE":
        return "指定执行手机离线。"
    if code in {"HUMAN_LOGIN_REQUIRED", "LOGIN_REQUIRED_MANUAL", "APP_LOGIN_REQUIRED"}:
        return "瓜子 APP 需要人工登录。"
    if code in {"ADB_UNAUTHORIZED", "DEVICE_AUTH_REQUIRED"}:
        return "手机 ADB 授权不可用。"
    if code == "DEVICE_OFFLINE":
        return "手机设备离线。"
    if code == "APP_NO_RESPONSE":
        return "瓜子 APP 无响应。"
    if code == "APP_NOT_INSTALLED":
        return "瓜子 APP 未安装或无法识别。"
    if code == "PHONE_LOCKED":
        return "手机可能处于锁屏状态。"
    if code == "PHONE_SCREEN_OFF":
        return "执行手机当前未亮屏。"
    if code == "PHONE_LOCKED_OR_INPUT_RESTRICTED":
        return "执行手机亮屏但仍处于锁屏或输入受限状态。"
    if code == "NOTIFICATION_SHADE_BLOCKING":
        return "执行手机被通知栏或系统遮挡页拦截。"
    if code == "SECURE_KEYGUARD_LOCKED":
        return "执行手机处于安全锁屏状态。"
    if code == "PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED":
        return "执行手机需要密码或生物识别解锁。"
    if code == "FAST_ENTRY_RETRY_EXHAUSTED":
        return "执行手机可恢复状态已尝试自动进入瓜子，但未成功。"
    if code == "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY":
        return "执行手机已尝试上滑退出遮挡并重新进入瓜子，但未成功。"
    if code == "DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME":
        return "执行手机已尝试上滑、返回、回到桌面并重新进入瓜子，但仍未恢复。"
    if code == "DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME":
        return "执行手机已退出遮挡并回到桌面，但瓜子 APP 未能进入前台。"
    if code == "DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE":
        return "瓜子 APP 启动后仍被通知栏或系统遮挡抢占焦点。"
    if code == "DEVICE_READY_FINAL_SNAPSHOT_STALE":
        return "设备恢复后未拿到可靠的最终前台快照。"
    if code == "GUAZI_APP_FOREGROUND_FAILED_AFTER_RETRY":
        return "瓜子 APP 自动进入前台失败。"
    if code == "GUAZI_LOGIN_REQUIRED":
        return "瓜子 APP 需要重新登录。"
    return "页面或程序流程需要管理员排查。"


def _admin_action_for_code(code: str | None) -> str:
    if code == "TARGET_ADB_SERIAL_NOT_CONFIGURED":
        return "请在项目配置中填写指定执行手机 serial，旧手机同时连接时不得默认选择。"
    if code == "TARGET_ADB_DEVICE_NOT_CONNECTED":
        return "请连接配置中指定的执行手机，不要切换到其他在线设备。"
    if code == "TARGET_ADB_DEVICE_UNAUTHORIZED":
        return "请在指定执行手机上允许本电脑调试授权。"
    if code == "TARGET_ADB_DEVICE_OFFLINE":
        return "请恢复指定执行手机在线状态后再继续。"
    if code == "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT":
        return "请检查指定执行手机的数据线、USB 接口和授权稳定性。"
    if code == "ADB_SERIAL_NOT_CONFIGURED":
        return "请在项目配置中填写指定执行手机 serial。"
    if code == "ADB_DEVICE_NOT_CONNECTED":
        return "请连接配置中指定的执行手机，不要切换到其他在线设备。"
    if code == "ADB_DEVICE_UNAUTHORIZED":
        return "请在指定执行手机上允许本电脑调试授权。"
    if code == "ADB_DEVICE_OFFLINE":
        return "请恢复指定执行手机在线状态后再继续。"
    if code in {"HUMAN_LOGIN_REQUIRED", "LOGIN_REQUIRED_MANUAL", "APP_LOGIN_REQUIRED"}:
        return "请在执行手机上登录瓜子 APP，确认进入首页。"
    if code in {"ADB_UNAUTHORIZED", "DEVICE_AUTH_REQUIRED"}:
        return "请确认执行手机已连接，并在手机上允许 USB 调试授权。"
    if code == "DEVICE_OFFLINE":
        return "请检查执行手机连接、数据线和 USB 调试授权。"
    if code == "APP_NO_RESPONSE":
        return "请确认执行手机无弹窗阻挡，瓜子 APP 可正常打开。"
    if code == "APP_NOT_INSTALLED":
        return "请在执行手机上安装瓜子 APP，并确认可以正常打开。"
    if code == "PHONE_LOCKED":
        return "请解锁执行手机，并确认无弹窗阻挡。"
    if code == "PHONE_SCREEN_OFF":
        return "请点亮并解锁执行手机后再回复：确认。"
    if code == "PHONE_LOCKED_OR_INPUT_RESTRICTED":
        return "请手动解锁执行手机，确认手机可操作后再回复：确认。"
    if code == "NOTIFICATION_SHADE_BLOCKING":
        return "请退出通知栏或系统遮挡页，确认手机可操作后再回复：确认。"
    if code == "SECURE_KEYGUARD_LOCKED":
        return "请手动解锁执行手机，停留在桌面或瓜子首页后再回复：确认。"
    if code == "PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED":
        return "请用密码或生物识别解锁执行手机后再回复：确认。"
    if code == "FAST_ENTRY_RETRY_EXHAUSTED":
        return "请手动解锁手机并确认瓜子 APP 可正常打开后再回复：确认。"
    if code == "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY":
        return "请手动上滑解锁执行手机，停留在桌面或瓜子首页后再回复：确认。"
    if code in {
        "DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME",
        "DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME",
        "DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE",
        "DEVICE_READY_FINAL_SNAPSHOT_STALE",
    }:
        return "请手动上滑解锁执行手机，停留在桌面或瓜子首页后再回复：确认。"
    if code == "GUAZI_APP_FOREGROUND_FAILED_AFTER_RETRY":
        return "请确认瓜子 APP 可正常打开到前台后再回复：确认。"
    if code == "GUAZI_LOGIN_REQUIRED":
        return "请手动登录瓜子 APP 后再回复：确认。"
    return "请检查执行手机和任务现场，处理完成后回到飞书。"


def _admin_suggestion_for_code(code: str | None) -> str:
    if code in {"HUMAN_LOGIN_REQUIRED", "LOGIN_REQUIRED_MANUAL", "APP_LOGIN_REQUIRED"}:
        return "请在执行手机上登录瓜子 APP，确认进入首页后回复：确认。"
    if code in {"ADB_UNAUTHORIZED", "DEVICE_AUTH_REQUIRED"}:
        return "请在手机上允许调试授权，确认设备恢复后回复：确认。"
    if code == "DEVICE_OFFLINE":
        return "请检查数据线、设备连接和授权状态，恢复后回复：确认。"
    if code == "APP_NO_RESPONSE":
        return "请确认手机和 APP 状态正常，恢复后回复：确认。"
    if code == "PHONE_LOCKED":
        return "请解锁手机并确认停留在可操作状态，恢复后回复：确认。"
    return "请查看任务日志和现场页面，确认处理完成后回复：确认。"


def format_business_system_processing_reply(task_id: str, classification: dict[str, Any]) -> str:
    if classification.get("category") == "system_environment":
        text = "\n".join(
            [
                f"【系统处理中】{task_id}",
                "系统定价暂未完成，已通知管理员处理。",
                "处理完成后系统会自动继续。",
            ]
        )
    else:
        text = "\n".join(
            [
                f"【系统处理中】{task_id}",
                "系统定价暂未完成，已转管理员处理。",
            ]
        )
    return _strip_forbidden_terms(text)


def _first_present_value(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _strip_forbidden_terms(text: str) -> str:
    cleaned = text
    for term in INTERNAL_BUSINESS_FORBIDDEN_TERMS:
        cleaned = cleaned.replace(term, "")
    return cleaned


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
