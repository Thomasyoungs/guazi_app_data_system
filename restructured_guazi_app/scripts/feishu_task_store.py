"""Local JSON task store for Feishu Phase 1 draft tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable

try:
    from admin_intervention_router import (
        ADMIN_INTERVENTION_REQUIRED,
        ADMIN_INTERVENTION_RESOLVED,
        DEFAULT_HEALTH_CHECK_COOLDOWN_SECONDS,
        PAGE_OR_PROGRAM_ERROR_CODES,
        RECOVERABLE_ADMIN_ERROR_CODES,
        SYSTEM_BLOCKED,
        TARGET_INFO_NEEDS_CORRECTION as ADMIN_TARGET_INFO_NEEDS_CORRECTION,
        TARGET_INFO_ERROR_CODES,
        collect_error_codes,
        format_system_not_recovered_reply,
        is_auto_health_recoverable_error,
        is_recoverable_admin_error,
    )
    from current_target_task_builder import build_current_target_task
    from feishu_message_to_target_task import parse_target_task_message
    from target_info_correction_feedback import (
        TARGET_INFO_NEEDS_CORRECTION,
        WAITING_TARGET_INFO_CORRECTION,
        target_info_status_fields,
        write_target_info_correction_feedback,
    )
except ImportError:  # pragma: no cover - supports package-style imports in tests.
    from scripts.admin_intervention_router import (
        ADMIN_INTERVENTION_REQUIRED,
        ADMIN_INTERVENTION_RESOLVED,
        DEFAULT_HEALTH_CHECK_COOLDOWN_SECONDS,
        PAGE_OR_PROGRAM_ERROR_CODES,
        RECOVERABLE_ADMIN_ERROR_CODES,
        SYSTEM_BLOCKED,
        TARGET_INFO_NEEDS_CORRECTION as ADMIN_TARGET_INFO_NEEDS_CORRECTION,
        TARGET_INFO_ERROR_CODES,
        collect_error_codes,
        format_system_not_recovered_reply,
        is_auto_health_recoverable_error,
        is_recoverable_admin_error,
    )
    from scripts.current_target_task_builder import build_current_target_task
    from scripts.feishu_message_to_target_task import parse_target_task_message
    from scripts.target_info_correction_feedback import (
        TARGET_INFO_NEEDS_CORRECTION,
        WAITING_TARGET_INFO_CORRECTION,
        target_info_status_fields,
        write_target_info_correction_feedback,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK_ROOT = PROJECT_ROOT / "data" / "feishu_tasks"
TASK_ID_RE = re.compile(r"^FS(?P<date>\d{8})_(?P<number>\d{4})$")
WAITING_TARGET_CONFIRMATION = "WAITING_TARGET_CONFIRMATION"
WAITING_CONFIRMATION_STATUSES = {WAITING_TARGET_CONFIRMATION, "DRAFT"}
MODEL_RESOLUTION_STATUS = "DRAFT_NEEDS_MODEL_RESOLUTION"
TARGET_INFO_RESOLUTION_STATUS = "DRAFT_NEEDS_TARGET_INFO"
TARGET_INFO_CORRECTION_STATUSES = {
    TARGET_INFO_NEEDS_CORRECTION,
    WAITING_TARGET_INFO_CORRECTION,
    ADMIN_TARGET_INFO_NEEDS_CORRECTION,
    TARGET_INFO_RESOLUTION_STATUS,
    MODEL_RESOLUTION_STATUS,
    "INVALID",
}
ACTIVE_APP_STATUSES = {"RUNNING_FIRST_STAGE", "S10_READY", "RUNNING_SECOND_STAGE", "APP_CONTROL_LOCKED"}
MANUAL_PRICE_WAITING_STATUSES = {"NEEDS_REVIEW", "WAITING_MANUAL_PRICE", "MANUAL_REVIEW_REQUIRED"}
SYSTEM_PRECHECK_FAILED_NOT_STARTED = "SYSTEM_PRECHECK_FAILED_NOT_STARTED"
NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER = "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER"
ADMIN_CHAT_ID_MISSING = "ADMIN_CHAT_ID_MISSING"
FINAL_FEEDBACK_DRY_RUN_NOT_MARKED_SENT = "FINAL_FEEDBACK_DRY_RUN_NOT_MARKED_SENT"
FINAL_FEEDBACK_SENT_FLAG_INVALID_DRY_RUN_ONLY = "FINAL_FEEDBACK_SENT_FLAG_INVALID_DRY_RUN_ONLY"
FINAL_FEEDBACK_SENT_FLAG_INVALID_NO_LIVE_EVIDENCE = "FINAL_FEEDBACK_SENT_FLAG_INVALID_NO_LIVE_EVIDENCE"
FINAL_FEEDBACK_LIVE_SEND_ATTEMPTED = "FINAL_FEEDBACK_LIVE_SEND_ATTEMPTED"
FINAL_FEEDBACK_LIVE_SEND_SUCCEEDED = "FINAL_FEEDBACK_LIVE_SEND_SUCCEEDED"
FINAL_FEEDBACK_LIVE_SEND_FAILED = "FINAL_FEEDBACK_LIVE_SEND_FAILED"
POST_START_FAILURE_GENERIC_TEMPLATE = "POST_START_REFERENCE_COLLECTION_INCOMPLETE"
POST_START_FAILURE_DUPLICATE_TEMPLATE = "POST_START_DUPLICATE_REFERENCE_RECOLLECT"
POST_START_FAILURE_V33_RECOLLECT_NEEDS_REVIEW_TEMPLATE = "V33_RECOLLECTED_PREVIOUS_REFERENCE_NEEDS_REVIEW"
V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW = (
    "V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW"
)
S12_CLAIM_FIELDS_NOT_READABLE = "S12_CLAIM_FIELDS_NOT_READABLE"
S12_REPORT_CLAIM_COUNT_MAX_AMOUNT_FIELD_MISSING = "S12_REPORT_CLAIM_COUNT_MAX_AMOUNT_FIELD_MISSING"
S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE = "S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE"
S12_CLAIM_RECOVERY_EXTENT_INVALID = "S12_CLAIM_RECOVERY_EXTENT_INVALID"
S12_CLAIM_RECOVERY_BOUNDS_INVALID = "S12_CLAIM_RECOVERY_BOUNDS_INVALID"
S12_CLAIM_RECOVERY_CANDIDATE_MALFORMED = "S12_CLAIM_RECOVERY_CANDIDATE_MALFORMED"
S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE = "S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE"
S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW = (
    "S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW"
)
S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW = "S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW"
S10_NEXT_REFERENCE_BINDING_FAILURE_CODES = {
    "NEXT_REFERENCE_CARD_NOT_FOUND_IN_S10",
    "NEXT_REFERENCE_CARD_NOT_FULLY_VISIBLE_AFTER_SCROLL",
    "S10_NEXT_REFERENCE_ABSOLUTE_CARD_NOT_FOUND",
    "S10_NEXT_REFERENCE_CARD_NOT_UNIQUE",
    "S10_NEXT_REFERENCE_PARTIAL_CARD_NOT_COMPLETED",
    "REFERENCE_LOOP_STATE_RESET_DETECTED",
}
S12_CLAIM_FIELD_FAILURE_CODES = {
    "FIELD_MISSING",
    S12_CLAIM_FIELDS_NOT_READABLE,
    S12_REPORT_CLAIM_COUNT_MAX_AMOUNT_FIELD_MISSING,
    S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE,
    S12_CLAIM_RECOVERY_EXTENT_INVALID,
    S12_CLAIM_RECOVERY_BOUNDS_INVALID,
    S12_CLAIM_RECOVERY_CANDIDATE_MALFORMED,
    S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE,
    S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW,
    S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW,
}
S12_TO_S13_REGION_PROOF_FAILURE_CODES = {
    "S12_TO_S13_REGION_PROOF_NOT_CONFIRMED",
    "S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED",
    "S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED_AFTER_S12_BODY_REACHED",
    "S13_REGION_HEADERS_NOT_FOUND",
    "S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED",
    "S13_REGION_HISTORY_COUNT_BINDING_FAILED",
}
LOW_SCORE_CONTINUATION_FEEDBACK_TERMINAL_CODES = {
    "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE",
    "SECOND_STAGE_CONTINUATION_SOURCE_MISSING",
    "SECOND_STAGE_CONTINUATION_SOURCE_LOST_AFTER_PRE_RUN_ISOLATION",
    "SECOND_STAGE_CONTINUATION_STATE_MISSING",
    "REFERENCE_LOOP_STATE_RESET_DETECTED",
    "CONTINUE_NEXT_REFERENCE_FAILED",
    "LOW_SCORE_CONTINUATION_FAILED",
}
LOW_SCORE_CONTINUATION_FEEDBACK_BLOCKED_TERMINAL_CODES = {
    "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED",
    *S12_TO_S13_REGION_PROOF_FAILURE_CODES,
    "SECOND_STAGE_RUNTIME_EXCEPTION",
    S12_CLAIM_FIELDS_NOT_READABLE,
    S12_REPORT_CLAIM_COUNT_MAX_AMOUNT_FIELD_MISSING,
    "S10_NEXT_REFERENCE_ABSOLUTE_CARD_NOT_FOUND",
    "S10_NEXT_REFERENCE_CARD_NOT_UNIQUE",
    "S10_NEXT_REFERENCE_PARTIAL_CARD_NOT_COMPLETED",
    "FIELD_MISSING",
    "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
}
POST_START_GENERIC_BUSINESS_MESSAGE = "系统已开始自动定价，但在参考车采集阶段未能形成完整结果，已安全停止，已通知管理员处理。"
POST_START_DUPLICATE_REFERENCE_BUSINESS_MESSAGE = "系统已开始自动定价，但参考车回采阶段未能继续执行，已安全停止，已通知管理员处理。"
NOT_STARTED_SYSTEM_PRECHECK_ERROR_CODES = {
    "HUMAN_LOGIN_REQUIRED",
    "LOGIN_REQUIRED_MANUAL",
    "APP_LOGIN_REQUIRED",
    "TARGET_ADB_SERIAL_NOT_CONFIGURED",
    "TARGET_ADB_DEVICE_NOT_CONNECTED",
    "TARGET_ADB_DEVICE_UNAUTHORIZED",
    "TARGET_ADB_DEVICE_OFFLINE",
    "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT",
    "ADB_DEVICE_NOT_FOUND",
    "ADB_UNAUTHORIZED",
    "DEVICE_OFFLINE",
    "DEVICE_AUTH_REQUIRED",
    "ADB_SERIAL_NOT_CONFIGURED",
    "ADB_DEVICE_NOT_CONNECTED",
    "ADB_DEVICE_UNAUTHORIZED",
    "ADB_DEVICE_OFFLINE",
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
    "APP_NOT_FOREGROUND_AFTER_3_RETRIES",
    "GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES",
    "GUAZI_SPLASH_PAGE_STUCK_AFTER_3_RETRIES",
    "RUNTIME_FRESH_EVIDENCE_MISSING",
    "XML_DUMP_FAILED",
    "GUAZI_LOGIN_REQUIRED",
    "PHONE_NOT_AWAKE",
    "PHONE_LOCKED",
    "DEVICE_READY_LAUNCHER_OVERLAY_KEYGUARD_STALE",
    "MIUI_LAUNCHER_OVERLAY_AD_BLOCKS_APP_ICON",
    "APP_NOT_READY",
    "APP_NO_RESPONSE",
}
CANCELLED_TASK_RESEND_REPLY = "该任务已取消，请重新发送目标车源后重新确认。"
FINAL_FAILURE_FEEDBACK_DELIVERY = "final_failure_feedback_delivery.json"
FINAL_FAILURE_BUSINESS_REPLY = "final_failure_business_reply.preview.txt"
FINAL_FAILURE_ADMIN_REPLY = "final_failure_admin_reply.preview.txt"
NOT_STARTED_AUTO_CANCEL_BUSINESS_REPLY = "not_started_auto_cancel_business_reply.preview.txt"
NOT_STARTED_AUTO_CANCEL_ADMIN_REPLY = "not_started_auto_cancel_admin_reply.preview.txt"
NOT_STARTED_AUTO_CANCEL_DELIVERY = "not_started_auto_cancel_delivery.json"
RELEASED_BLOCKER_BUSINESS_REPLY = "released_blocker_business_reply.preview.txt"
RELEASED_BLOCKER_ADMIN_REPLY = "released_blocker_admin_reply.preview.txt"
RELEASED_BLOCKER_DELIVERY = "released_blocker_delivery.json"
MANUAL_REVIEW_FEEDBACK_DELIVERY = "manual_review_feedback_delivery.json"
CONCRETE_FAILURE_PRIORITY = (
    ("ACTIVE_RUN_LOCK", {"ACTIVE_RUN_LOCK", "ACTIVE_PRICING_LOCK_EXISTS", "PRICING_LOCK_EXISTS", "REAL_RUNNING_TASK_EXISTS", "ACTIVE_APP_TASK_EXISTS"}),
    ("TARGET_ADB_SERIAL_NOT_CONFIGURED", {"TARGET_ADB_SERIAL_NOT_CONFIGURED"}),
    ("TARGET_ADB_DEVICE_UNAUTHORIZED", {"TARGET_ADB_DEVICE_UNAUTHORIZED"}),
    ("TARGET_ADB_DEVICE_OFFLINE", {"TARGET_ADB_DEVICE_OFFLINE"}),
    ("TARGET_ADB_DEVICE_NOT_CONNECTED", {"TARGET_ADB_DEVICE_NOT_CONNECTED"}),
    ("TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT", {"TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT"}),
    ("ADB_SERIAL_NOT_CONFIGURED", {"ADB_SERIAL_NOT_CONFIGURED"}),
    ("ADB_DEVICE_UNAUTHORIZED", {"ADB_DEVICE_UNAUTHORIZED"}),
    ("ADB_DEVICE_OFFLINE", {"ADB_DEVICE_OFFLINE"}),
    ("ADB_DEVICE_NOT_CONNECTED", {"ADB_DEVICE_NOT_CONNECTED"}),
    ("HUMAN_LOGIN_REQUIRED", {"HUMAN_LOGIN_REQUIRED", "LOGIN_REQUIRED_MANUAL", "APP_LOGIN_REQUIRED"}),
    ("ADB_DEVICE_NOT_FOUND", {"ADB_DEVICE_NOT_FOUND", "DEVICE_OFFLINE"}),
    ("ADB_UNAUTHORIZED", {"ADB_UNAUTHORIZED", "DEVICE_AUTH_REQUIRED"}),
    ("ADB_INPUT_PERMISSION_DENIED", {"ADB_INPUT_PERMISSION_DENIED", "USB_DEBUG_SECURITY_DISABLED"}),
    (
        "DEVICE_READY_LAUNCHER_OVERLAY_KEYGUARD_STALE",
        {"DEVICE_READY_LAUNCHER_OVERLAY_KEYGUARD_STALE", "MIUI_LAUNCHER_OVERLAY_AD_BLOCKS_APP_ICON"},
    ),
    ("GUAZI_LOGIN_REQUIRED", {"GUAZI_LOGIN_REQUIRED"}),
    ("SECURE_KEYGUARD_LOCKED", {"SECURE_KEYGUARD_LOCKED"}),
    ("PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED", {"PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED"}),
    (
        "FAST_ENTRY_RETRY_EXHAUSTED",
        {
            "FAST_ENTRY_RETRY_EXHAUSTED",
            "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY",
            "DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME",
            "DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME",
            "DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE",
            "DEVICE_READY_FINAL_SNAPSHOT_STALE",
            "GUAZI_APP_FOREGROUND_FAILED_AFTER_RETRY",
        },
    ),
    ("PHONE_SCREEN_OFF", {"PHONE_SCREEN_OFF"}),
    (
        "NOTIFICATION_SHADE_BLOCKING",
        {"NOTIFICATION_SHADE_BLOCKING"},
    ),
    (
        "PHONE_LOCKED_OR_INPUT_RESTRICTED",
        {"PHONE_LOCKED_OR_INPUT_RESTRICTED"},
    ),
    ("PHONE_NOT_AWAKE", {"PHONE_NOT_AWAKE", "PHONE_LOCKED", "NON_SECURE_KEYGUARD_SWIPE_FAILED", "NOTIFICATION_SHADE_STILL_VISIBLE"}),
    ("GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE", {"GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE"}),
    ("APP_PACKAGE_NOT_FOUND", {"APP_PACKAGE_NOT_FOUND", "APP_NOT_INSTALLED", "PACKAGE_NOT_FOUND"}),
    (
        "GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES",
        {"GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES", "RUNTIME_FRESH_EVIDENCE_MISSING", "XML_DUMP_FAILED"},
    ),
    ("GUAZI_SPLASH_PAGE_STUCK_AFTER_3_RETRIES", {"GUAZI_SPLASH_PAGE_STUCK_AFTER_3_RETRIES"}),
    ("APP_NOT_FOREGROUND_AFTER_3_RETRIES", {"APP_NOT_FOREGROUND_AFTER_3_RETRIES"}),
    (
        "GUAZI_PUSH_POPUP_CLOSE_FAILED",
        {
            "GUAZI_TRANSIENT_POPUP_BLOCKED_FLOW",
            "GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND",
            "GUAZI_PUSH_POPUP_CLOSE_FAILED",
        },
    ),
    (
        "APP_LAUNCH_FAILED",
        {
            "APP_LAUNCH_FAILED",
            "APP_ICON_NOT_FOUND_AFTER_DEVICE_READY",
            "APP_ICON_NOT_FOUND_AFTER_LATER_DIALOG_DISMISSED",
            "APP_ICON_NOT_FOUND_AFTER_ACCOUNT_CENTER_EXIT",
            "GUAZI_APP_ICON_NOT_FOUND",
            "APP_ICON_CLICK_DID_NOT_OPEN_GUAZI",
        },
    ),
    ("RESULT_MISSING_REQUIRED_PRICING_FIELDS", {"RESULT_MISSING_REQUIRED_PRICING_FIELDS"}),
    ("S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT", {"S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT"}),
    ("S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED", {"S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED"}),
    ("S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT", {"S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT"}),
    ("S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT", {"S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT"}),
    ("S07_AGE_SLIDER_DRAG_START_OUTSIDE_HANDLE_BOUNDS", {"S07_AGE_SLIDER_DRAG_START_OUTSIDE_HANDLE_BOUNDS"}),
    ("S07_AGE_SLIDER_HANDLE_BINDING_FAILED", {"S07_AGE_SLIDER_HANDLE_BINDING_FAILED"}),
    ("S07_AGE_EXACT_RANGE_VERIFY_FAILED", {"S07_AGE_EXACT_RANGE_VERIFY_FAILED"}),
    ("S07_AGE_ONE_POST_ACTION_VERIFY_FAILED", {"S07_AGE_ONE_POST_ACTION_VERIFY_FAILED"}),
    ("S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED", {"S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED"}),
    ("S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED", {"S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED"}),
    ("S07_AGE_FILTER_ACTUAL_RANGE_MISMATCH", {"S07_AGE_FILTER_ACTUAL_RANGE_MISMATCH"}),
    ("S07_POST_ACTION_FRESH_EVIDENCE_MISSING", {"S07_POST_ACTION_FRESH_EVIDENCE_MISSING"}),
    ("S07_AGE_FILTER_PLANNED_ACTUAL_MISMATCH", {"S07_AGE_FILTER_PLANNED_ACTUAL_MISMATCH"}),
    ("S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER", {"S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER"}),
    ("S07_AGE_SLIDER_FALLBACK_BUDGET_EXCEEDED", {"S07_AGE_SLIDER_FALLBACK_BUDGET_EXCEEDED"}),
    ("S07_AGE_SLIDER_FINAL_VALUE_MISMATCH", {"S07_AGE_SLIDER_FINAL_VALUE_MISMATCH"}),
    ("S07_AGE_SLIDER_FASTPATH_FAILED", {"S07_AGE_SLIDER_FASTPATH_FAILED"}),
    (
        V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
        {V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW},
    ),
    (S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW, {S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW}),
    (S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW, {S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW}),
    (S12_REPORT_CLAIM_COUNT_MAX_AMOUNT_FIELD_MISSING, {S12_REPORT_CLAIM_COUNT_MAX_AMOUNT_FIELD_MISSING, S12_CLAIM_FIELDS_NOT_READABLE, "FIELD_MISSING"}),
    (S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE, {S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE}),
    ("DUPLICATE_REFERENCE_CLICK_BLOCKED", {"DUPLICATE_REFERENCE_CLICK_BLOCKED"}),
    ("REFERENCE_CARD_BINDING_NOT_UNIQUE", {"REFERENCE_CARD_BINDING_NOT_UNIQUE"}),
    ("SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED", {"SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED"}),
    (
        "REFERENCE_SCORE_UNTRUSTED_INCOMPLETE_COLLECTION",
        {"REFERENCE_SCORE_UNTRUSTED_INCOMPLETE_COLLECTION"},
    ),
    (
        "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE",
        {"S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE", "S14_COLLECTION_INCOMPLETE_UNRECOVERABLE"},
    ),
    (
        "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED",
        {"S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED"},
    ),
    (
        "S13_HISTORY_REPAIR_ENTRY_NOT_VISIBLE_OR_UNBOUND",
        {"S13_HISTORY_REPAIR_ENTRY_NOT_VISIBLE_OR_UNBOUND"},
    ),
    (
        "S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND",
        {"S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND"},
    ),
    ("S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED", {"S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED"}),
    ("S13_FOUR_REGION_LOOP_GUARD_TRIGGERED", {"S13_FOUR_REGION_LOOP_GUARD_TRIGGERED"}),
    ("APP_NOT_FOREGROUND", {"APP_NOT_FOREGROUND"}),
    ("APP_HOME_NOT_READY", {"APP_HOME_NOT_READY", "POPUP_BLOCKED"}),
    ("APP_NOT_READY", {"APP_NOT_READY", "APP_NO_RESPONSE"}),
    (
        "SECOND_STAGE_RUNTIME_EXCEPTION",
        {
            "SECOND_STAGE_RUNTIME_EXCEPTION",
            "V149_REFERENCE_IDENTITY_SUMMARY_TYPE_ERROR",
            "RESULT_SCHEMA_INVALID_FOR_PRICING_WITH_RUNTIME_EXCEPTION",
        },
    ),
    (
        "GUAZI_PAGE_UNRECOGNIZED_AFTER_FORCE_RESTART",
        {
            "GUAZI_PAGE_UNRECOGNIZED_AFTER_FORCE_RESTART",
            "APP_FORCE_RESTART_NON_CONTRACT_PAGE",
        },
    ),
    (
        "BRAND_FILTER_STEP_NOT_ENTERED",
        {
            "BRAND_FILTER_STEP_NOT_ENTERED",
            "APP_SELECT_PAGE_NOT_READY",
        },
    ),
    ("BRAND_FILTER_NOT_FOUND", {"BRAND_FILTER_NOT_FOUND"}),
    ("BRAND_FILTER_CLICK_FAILED", {"BRAND_FILTER_CLICK_FAILED"}),
    ("BRAND_FILTER_PANEL_NOT_OPENED", {"BRAND_FILTER_PANEL_NOT_OPENED"}),
    ("S03_TARGET_BRAND_CLICK_FAILED", {"S03_TARGET_BRAND_CLICK_FAILED"}),
    ("S03_TARGET_BRAND_PANEL_NOT_READY", {"S03_TARGET_BRAND_PANEL_NOT_READY"}),
    ("S03_TARGET_INITIAL_LETTER_NOT_DERIVABLE", {"S03_TARGET_INITIAL_LETTER_NOT_DERIVABLE"}),
    ("S03_TARGET_INITIAL_LETTER_NOT_FOUND", {"S03_TARGET_INITIAL_LETTER_NOT_FOUND"}),
    (
        "S03_TARGET_BRAND_NOT_FOUND",
        {
            "S03_TARGET_BRAND_NOT_FOUND",
            "S03_TARGET_BRAND_NOT_VISIBLE_AFTER_INITIAL_LETTER",
            "FIRST_STAGE_TARGET_NOT_FOUND",
        },
    ),
    ("S05_TARGET_CONFIG_NOT_FOUND", {"S05_TARGET_CONFIG_NOT_FOUND"}),
    ("S05_TARGET_CONFIG_VISIBLE_BUT_NOT_MATCHED", {"S05_TARGET_CONFIG_VISIBLE_BUT_NOT_MATCHED"}),
    ("S05_TARGET_CONFIG_CLICK_FAILED", {"S05_TARGET_CONFIG_CLICK_FAILED"}),
    (
        "S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED",
        {"S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED", "S05_TRIM_CLICK_NO_EFFECT"},
    ),
    ("S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE", {"S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE"}),
    ("S13_REPAIR_ITEM_CLICK_DID_NOT_OPEN_DETAIL", {"S13_REPAIR_ITEM_CLICK_DID_NOT_OPEN_DETAIL"}),
    ("S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED", {"S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED"}),
)
S02_BRAND_FILTER_ENTRY_FAILURE_CODES = {
    "PAGE_CONTRACT_MISMATCH",
    "FIRST_STAGE_NOT_S10_READY",
    "APP_SELECT_PAGE_NOT_READY",
    "BRAND_FILTER_STEP_NOT_ENTERED",
}
S02_BRAND_FILTER_MARKERS = ("综合排序", "品牌", "价格", "车龄/里程", "筛选")
S02_BRAND_LIST_MARKERS = ("门店实车", "已检测", "万公里", "首付", "万")
CONCRETE_FAILURE_MESSAGES = {
    "S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT": {
        "human_reason": "系统已进入车龄筛选步骤，但车龄滑块拖动后页面值未发生变化",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED": {
        "human_reason": "S07 age slider real handle binding failed.",
        "retry_instruction": "Please wait for admin handling before starting a new pricing task.",
    },
    "S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT": {
        "human_reason": "S07 age slider real-handle touch produced no page value or bounds change.",
        "retry_instruction": "Please wait for admin handling before starting a new pricing task.",
    },
    "S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT": {
        "human_reason": "S07 left age slider real-handle touch produced no page value or bounds change.",
        "retry_instruction": "Please wait for admin handling before starting a new pricing task.",
    },
    "S07_AGE_SLIDER_DRAG_START_OUTSIDE_HANDLE_BOUNDS": {
        "human_reason": "系统已进入车龄筛选步骤，但车龄滑块拖动起点未命中可拖动滑块",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_AGE_SLIDER_HANDLE_BINDING_FAILED": {
        "human_reason": "系统已进入车龄筛选步骤，但未能可靠绑定车龄滑块",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_AGE_EXACT_RANGE_VERIFY_FAILED": {
        "human_reason": "系统已进入车龄筛选步骤，但最终车龄筛选结果未能确认一致",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_AGE_SLIDER_FALLBACK_BUDGET_EXCEEDED": {
        "human_reason": "系统已进入车龄筛选步骤，但车龄滑块筛选未能稳定完成",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_AGE_SLIDER_FINAL_VALUE_MISMATCH": {
        "human_reason": "系统已进入车龄筛选步骤，但车龄筛选结果与目标车龄不一致",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_AGE_SLIDER_FASTPATH_FAILED": {
        "human_reason": "系统已进入车龄筛选步骤，但车龄滑块快速定位失败",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE": {
        "human_reason": "S07 target age 1 hidden tick between 0 and 2 was not bindable.",
        "retry_instruction": "Please wait for admin handling before starting a new pricing task.",
    },
    "S07_AGE_ONE_HIDDEN_TICK_VERIFY_FAILED": {
        "human_reason": "S07 target age 1 hidden tick was selected but exact 1-1 year verification failed.",
        "retry_instruction": "Please wait for admin handling before starting a new pricing task.",
    },
    "S07_AGE_ONE_POST_ACTION_VERIFY_FAILED": {
        "human_reason": "系统已进入车龄筛选步骤，但动作后的新鲜页面证据未能证明最终筛选为 1-1年",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED": {
        "human_reason": "系统已进入车龄筛选步骤，但动作后的页面证据仍是 1年以下/1年以内这类宽泛范围，不是精确 1-1年",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED": {
        "human_reason": "系统已进入车龄筛选步骤，但动作后的新鲜页面证据未能证明最终筛选为 0年",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_AGE_FILTER_ACTUAL_RANGE_MISMATCH": {
        "human_reason": "系统已进入车龄筛选步骤，但实际车龄范围与目标车龄不一致",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_POST_ACTION_FRESH_EVIDENCE_MISSING": {
        "human_reason": "系统已进入车龄筛选步骤，但缺少动作后的新鲜截图/XML验证证据",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_AGE_FILTER_PLANNED_ACTUAL_MISMATCH": {
        "human_reason": "系统已进入车龄筛选步骤，但计划车龄和实际页面车龄不一致",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER": {
        "human_reason": "系统已进入车龄筛选步骤，但查看车源前的车龄验证门禁未通过",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S11_CONTRACT_HANDLER_NOT_INVOKED_AFTER_DETAIL_PAGE_RECOGNIZED": {
        "human_reason": "系统已进入参考车详情页，但未能执行详情页采集契约",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S11_XML_STALE_OR_MISMATCHED_WITH_SCREENSHOT": {
        "human_reason": "系统已点击参考车卡片，但详情页截图与页面结构证据不一致",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S11_DETAIL_PAGE_RECOGNITION_FAILED_AFTER_S10_CLICK": {
        "human_reason": "系统已点击参考车卡片，但未能可靠确认进入参考车详情页",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S11_ALLOWED_ACTION_NOT_STARTED_AFTER_HANDLER_INVOKED": {
        "human_reason": "系统已进入参考车详情页，但详情页采集动作未能启动",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "S11_REPORT_SEARCH_STATE_NOT_INITIALIZED": {
        "human_reason": "系统已进入参考车详情页，但完整报告搜索状态未能初始化",
        "retry_instruction": "请勿重复确认，等待管理员处理后再重新发起任务。",
    },
    "SECOND_STAGE_COLLECTION_INCOMPLETE": {
        "human_reason": "参考车采集尚未完成，系统未输出自动定价结果",
        "retry_instruction": "请管理员检查参考车采集流程后继续。",
    },
    "RESULT_MISSING_REQUIRED_PRICING_FIELDS": {
        "human_reason": "定价结果缺少完整参考车或价格链",
        "retry_instruction": "请管理员检查采集结果，确认边界参考车和价格链完整后再继续。",
    },
    "V3_BOUNDARY_NOT_CONFIRMED_AFTER_ALL_REFERENCES": {
        "human_reason": "参考车边界未能确认",
        "retry_instruction": "请管理员检查三同参考车采集结果后再继续。",
    },
    "TARGET_ADB_SERIAL_NOT_CONFIGURED": {
        "human_reason": "未配置执行手机",
        "retry_instruction": "请管理员配置指定执行手机后，重新发送目标车源并回复“确认”重新开始。",
    },
    "TARGET_ADB_DEVICE_NOT_CONNECTED": {
        "human_reason": "指定执行手机未连接或当前不可见",
        "retry_instruction": "请确认指定执行手机已连接并保持授权后，重新发送目标车源并回复“确认”重新开始。",
    },
    "TARGET_ADB_DEVICE_UNAUTHORIZED": {
        "human_reason": "指定执行手机未授权",
        "retry_instruction": "请在指定执行手机上允许本电脑调试后，重新发送目标车源并回复“确认”重新开始。",
    },
    "TARGET_ADB_DEVICE_OFFLINE": {
        "human_reason": "指定执行手机离线",
        "retry_instruction": "请恢复指定执行手机连接后，重新发送目标车源并回复“确认”重新开始。",
    },
    "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT": {
        "human_reason": "指定执行手机连接不稳定，执行瞬间设备不可见",
        "retry_instruction": "请检查数据线和 USB 接口后，重新发送目标车源并回复“确认”重新开始。",
    },
    "ADB_SERIAL_NOT_CONFIGURED": {
        "human_reason": "未配置执行手机",
        "retry_instruction": "请联系管理员检查执行手机配置后，再回复“确认”。",
    },
    "ADB_DEVICE_NOT_CONNECTED": {
        "human_reason": "指定执行手机未连接或当前不可见",
        "retry_instruction": "请确认指定执行手机已连接并保持授权后，再回复“确认”。",
    },
    "ADB_DEVICE_UNAUTHORIZED": {
        "human_reason": "指定执行手机未完成本电脑授权",
        "retry_instruction": "请在手机上允许本电脑调试授权后，再回复“确认”。",
    },
    "ADB_DEVICE_OFFLINE": {
        "human_reason": "指定执行手机当前离线",
        "retry_instruction": "请恢复手机连接后，再回复“确认”。",
    },
    "HUMAN_LOGIN_REQUIRED": {
        "human_reason": "瓜子 APP 未登录或登录状态异常",
        "retry_instruction": "请在手机上登录瓜子并停留在首页后，重新发送目标车源并回复“确认”重新开始。",
    },
    "ADB_DEVICE_NOT_FOUND": {
        "human_reason": "执行手机未连接",
        "retry_instruction": "请连接手机后重新发送目标车源并回复“确认”重新开始。",
    },
    "ADB_UNAUTHORIZED": {
        "human_reason": "手机授权未通过",
        "retry_instruction": "请在手机上允许本电脑控制后，重新发送目标车源并回复“确认”重新开始。",
    },
    "ADB_INPUT_PERMISSION_DENIED": {
        "human_reason": "手机未允许电脑自动操作",
        "retry_instruction": "请在手机开发者选项中打开 USB 调试（安全设置）并重新授权后，重新发送目标车源并回复“确认”重新开始。",
    },
    "PHONE_SCREEN_OFF": {
        "human_reason": "执行手机当前未亮屏",
        "retry_instruction": "请手动点亮并解锁手机后，再回复“确认”。",
    },
    "PHONE_LOCKED_OR_INPUT_RESTRICTED": {
        "human_reason": "手机已亮屏，但仍处于锁屏或输入受限状态",
        "retry_instruction": "请手动解锁手机，停留在桌面或瓜子页面后，再回复“确认”。",
    },
    "NOTIFICATION_SHADE_BLOCKING": {
        "human_reason": "手机当前停在通知栏或系统遮挡页面",
        "retry_instruction": "请退出遮挡页面并解锁手机后，再回复“确认”。",
    },
    "SECURE_KEYGUARD_LOCKED": {
        "human_reason": "手机需要手动解锁后才能开始定价",
        "retry_instruction": "请手动解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
    },
    "PASSWORD_OR_BIOMETRIC_LOCK_REQUIRED": {
        "human_reason": "手机需要密码或生物识别解锁",
        "retry_instruction": "请手动解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
    },
    "FAST_ENTRY_RETRY_EXHAUSTED": {
        "human_reason": "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP",
        "retry_instruction": "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
    },
    "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY": {
        "human_reason": "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP",
        "retry_instruction": "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
    },
    "DEVICE_READY_ROBUST_RECOVERY_FAILED_AFTER_BACK_HOME": {
        "human_reason": "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP",
        "retry_instruction": "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
    },
    "DEVICE_READY_GUAZI_LAUNCH_FAILED_AFTER_HOME": {
        "human_reason": "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP",
        "retry_instruction": "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
    },
    "DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE": {
        "human_reason": "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP",
        "retry_instruction": "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
    },
    "DEVICE_READY_FINAL_SNAPSHOT_STALE": {
        "human_reason": "手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP",
        "retry_instruction": "请手动上滑解锁手机，停留在桌面或瓜子首页后，再回复“确认”。",
    },
    "GUAZI_APP_FOREGROUND_FAILED_AFTER_RETRY": {
        "human_reason": "手机已连接，但系统未能自动进入瓜子 APP",
        "retry_instruction": "请确认瓜子 APP 可正常打开后，再回复“确认”。",
    },
    "GUAZI_LOGIN_REQUIRED": {
        "human_reason": "瓜子 APP 需要重新登录",
        "retry_instruction": "请手动登录瓜子 APP 后，再回复“确认”。",
    },
    "PHONE_NOT_AWAKE": {
        "human_reason": "手机未亮屏解锁",
        "retry_instruction": "请保持手机亮屏解锁后，重新发送目标车源并回复“确认”重新开始。",
    },
    "GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE": {
        "human_reason": "瓜子 APP 重新打开失败",
        "retry_instruction": "请确认瓜子 APP 可正常打开后，重新发送目标车源并回复“确认”重新开始。",
    },
    "GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES": {
        "human_reason": "瓜子 APP 已在前台，但系统连续 3 次未能取得完整页面证据",
        "retry_instruction": "请确认瓜子 APP 页面可正常显示，关闭异常弹窗后重新发送目标车源并回复“确认”。",
    },
    "GUAZI_SPLASH_PAGE_STUCK_AFTER_3_RETRIES": {
        "human_reason": "瓜子 APP 已打开，但启动广告或启动页连续 3 次未能自动进入首页",
        "retry_instruction": "请手动关闭启动广告或进入瓜子首页后重新发送目标车源并回复“确认”。",
    },
    "APP_NOT_FOREGROUND_AFTER_3_RETRIES": {
        "human_reason": "系统连续 3 次尝试后仍未能进入瓜子 APP 前台",
        "retry_instruction": "请确认瓜子 APP 可正常打开后重新发送目标车源并回复“确认”。",
    },
    "RUNTIME_FRESH_EVIDENCE_MISSING": {
        "human_reason": "瓜子 APP 已响应，但页面证据采集不完整",
        "retry_instruction": "请确认瓜子 APP 页面可正常显示，关闭异常弹窗后重新发送目标车源并回复“确认”。",
    },
    "XML_DUMP_FAILED": {
        "human_reason": "瓜子 APP 已响应，但页面结构采集失败",
        "retry_instruction": "请确认瓜子 APP 页面可正常显示，关闭异常弹窗后重新发送目标车源并回复“确认”。",
    },
    "DEVICE_READY_LAUNCHER_OVERLAY_KEYGUARD_STALE": {
        "human_reason": "手机桌面被系统弹窗或广告页遮挡，未能进入瓜子",
        "retry_instruction": "请关闭手机弹窗并保持桌面可操作后，重新发送目标车源并回复“确认”重新开始。",
    },
    "APP_PACKAGE_NOT_FOUND": {
        "human_reason": "瓜子 APP 未安装或安装包不可用",
        "retry_instruction": "请确认瓜子 APP 已安装且可正常打开后，重新发送目标车源并回复“确认”重新开始。",
    },
    "APP_LAUNCH_FAILED": {
        "human_reason": "瓜子 APP 启动失败",
        "retry_instruction": "请确认瓜子 APP 已安装且桌面入口可见后，重新发送目标车源并回复“确认”重新开始。",
    },
    "APP_NOT_FOREGROUND": {
        "human_reason": "瓜子 APP 未成功打开到前台",
        "retry_instruction": "请确认瓜子 APP 可正常打开后，重新发送目标车源并回复“确认”重新开始。",
    },
    "APP_HOME_NOT_READY": {
        "human_reason": "瓜子 APP 未进入可操作页面，可能未在首页或存在弹窗",
        "retry_instruction": "请确认瓜子 APP 可正常进入首页后，重新发送目标车源并回复“确认”重新开始。",
    },
    "APP_NOT_READY": {
        "human_reason": "瓜子 APP 未处于可操作状态，可能未在首页或存在弹窗",
        "retry_instruction": "请打开瓜子首页并关闭弹窗后，重新发送目标车源并回复“确认”重新开始。",
    },
    "GUAZI_PAGE_UNRECOGNIZED_AFTER_FORCE_RESTART": {
        "human_reason": "瓜子 APP 已打开，但系统未能识别当前页面",
        "retry_instruction": "请确认瓜子 APP 首页可正常显示后，重新发送目标车源并回复“确认”重新开始。",
    },
    "BRAND_FILTER_STEP_NOT_ENTERED": {
        "human_reason": "瓜子 APP 已打开，但未能进入品牌筛选页",
        "retry_instruction": "请确认瓜子 APP 可正常操作后，重新发送目标车源并回复“确认”重新开始。",
    },
    "BRAND_FILTER_NOT_FOUND": {
        "human_reason": "未找到品牌筛选入口",
        "retry_instruction": "请确认瓜子 APP 可正常显示选车页后，重新发送目标车源并回复“确认”重新开始。",
    },
    "BRAND_FILTER_CLICK_FAILED": {
        "human_reason": "品牌筛选入口点击失败",
        "retry_instruction": "请确认瓜子 APP 可正常操作后，重新发送目标车源并回复“确认”重新开始。",
    },
    "BRAND_FILTER_PANEL_NOT_OPENED": {
        "human_reason": "品牌筛选页未打开",
        "retry_instruction": "请确认瓜子 APP 可正常打开品牌筛选后，重新发送目标车源并回复“确认”重新开始。",
    },
    "S03_TARGET_INITIAL_LETTER_NOT_DERIVABLE": {
        "human_reason": "系统暂未能定位目标品牌的品牌分组",
        "retry_instruction": "请确认品牌名称是否准确，或重新发送完整车型信息。",
    },
    "S03_TARGET_INITIAL_LETTER_NOT_FOUND": {
        "human_reason": "瓜子品牌选择页已打开，但未找到目标品牌所在的字母分组",
        "retry_instruction": "请确认品牌名称是否准确，或重新发送完整车型信息。",
    },
    "S03_TARGET_BRAND_NOT_FOUND": {
        "human_reason": "瓜子品牌选择页已打开，但未能定位目标品牌",
        "retry_instruction": "请确认品牌名称是否准确，或重新发送完整车型信息。",
    },
    "S03_TARGET_BRAND_CLICK_FAILED": {
        "human_reason": "瓜子品牌选择页已找到目标品牌，但点击失败",
        "retry_instruction": "请确认瓜子 APP 可正常操作后，重新发送目标车源并回复“确认”重新开始。",
    },
    "S03_TARGET_BRAND_PANEL_NOT_READY": {
        "human_reason": "瓜子品牌选择页没有准备好",
        "retry_instruction": "请确认瓜子 APP 可正常打开品牌选择页后，重新发送目标车源并回复“确认”重新开始。",
    },
    "S05_TARGET_CONFIG_NOT_FOUND": {
        "human_reason": "已进入车款配置页，但未能确认目标配置",
        "retry_instruction": "请确认配置名称是否准确，或重新发送完整车型配置。",
    },
    "S05_TARGET_CONFIG_VISIBLE_BUT_NOT_MATCHED": {
        "human_reason": "页面中已出现目标车款配置，但系统未能完成配置匹配",
        "retry_instruction": "请重新发送完整车型配置。",
    },
    "S05_TARGET_CONFIG_CLICK_FAILED": {
        "human_reason": "车款配置已找到，但点击目标配置失败",
        "retry_instruction": "请确认瓜子 APP 可正常操作后，重新发送目标车源并回复“确认”重新开始。",
    },
    "S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED": {
        "human_reason": "车款配置已点击，但未能确认已选中目标配置",
        "retry_instruction": "请确认配置名称是否准确，或重新发送完整车型配置。",
    },
    "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED": {
        "human_reason": "已进入瓜子结果页，但系统未能稳定绑定参考车",
        "retry_instruction": "请稍后重试或联系管理员检查结果页识别。",
    },
    "REFERENCE_CARD_BINDING_NOT_UNIQUE": {
        "human_reason": "系统已进入三同参考车采集，但在识别下一辆参考车卡片时无法唯一确认目标卡片，为避免采错参考车，本次已安全停止",
        "retry_instruction": "请重新发送目标车源并回复“确认”重新开始；如连续出现，请联系管理员检查页面状态。",
    },
    "REFERENCE_SCORE_UNTRUSTED_INCOMPLETE_COLLECTION": {
        "human_reason": "参考车车况证据未能完整采集，无法形成可信自动定价结果",
        "retry_instruction": "请重新发送目标车源并回复“确认”重新开始；如连续出现，请联系管理员检查页面状态。",
    },
    "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE": {
        "human_reason": "参考车车况证据未能完整采集，无法形成可信自动定价结果",
        "retry_instruction": "请重新发送目标车源并回复“确认”重新开始；如连续出现，请联系管理员检查页面状态。",
    },
    "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED": {
        "human_reason": "参考车检测报告中的车况证据未能安全确认，无法形成可信自动定价结果",
        "retry_instruction": "请重新发送目标车源并回复“确认”重新开始；如连续出现，请联系管理员检查页面状态。",
    },
    "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED": {
        "human_reason": "已进入检测报告，但系统未能安全打开历史修复详情",
        "retry_instruction": "请稍后重试或联系管理员检查车况详情页识别。",
    },
    "S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE": {
        "human_reason": "已进入检测报告，但历史修复项点击区域不安全，系统未继续自动点击",
        "retry_instruction": "请稍后重试或联系管理员检查车况详情页识别。",
    },
    "S13_REPAIR_ITEM_CLICK_DID_NOT_OPEN_DETAIL": {
        "human_reason": "已进入检测报告，但点击历史修复项后未打开详情页",
        "retry_instruction": "请稍后重试或联系管理员检查车况详情页识别。",
    },
    "S13_HISTORY_REPAIR_ENTRY_NOT_VISIBLE_OR_UNBOUND": {
        "human_reason": "已进入检测报告，但未能可靠定位检测报告中的历史修复车况入口",
        "retry_instruction": "请重新发送目标车源并回复“确认”重新开始；如连续出现，请联系管理员检查页面状态。",
    },
    "S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND": {
        "human_reason": "已进入检测报告，但未能安全打开历史修复车况入口",
        "retry_instruction": "请重新发送目标车源并回复“确认”重新开始；如连续出现，请联系管理员检查页面状态。",
    },
    "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED": {
        "human_reason": "\u7cfb\u7edf\u5df2\u8fdb\u5165\u53c2\u8003\u8f66\u68c0\u6d4b\u62a5\u544a\uff0c\u4f46\u56db\u4e2a\u8f66\u8eab\u533a\u57df\u7684\u5386\u53f2\u4fee\u590d\u6b21\u6570\u672a\u80fd\u53ef\u9760\u786e\u8ba4",
        "retry_instruction": "\u8bf7\u6682\u7b49\u7ba1\u7406\u5458\u5904\u7406\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002",
    },
    "S12_TO_S13_REGION_PROOF_NOT_CONFIRMED": {
        "human_reason": "系统已进入参考车检测报告页，但从车身外观进入四区域/历史修复采集阶段的页面证据未能可靠确认",
        "retry_instruction": "请等待管理员处理后再重新发起任务。",
    },
    "S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED": {
        "human_reason": "系统已进入参考车检测报告页，但车身外观区域后的四区域页签未能可靠确认",
        "retry_instruction": "请等待管理员处理后再重新发起任务。",
    },
    "S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED_AFTER_S12_BODY_REACHED": {
        "human_reason": "系统已进入参考车检测报告页，但车身外观区域后的历史修复表未能可靠确认",
        "retry_instruction": "请等待管理员处理后再重新发起任务。",
    },
    "S13_REGION_HEADERS_NOT_FOUND": {
        "human_reason": "系统已进入参考车检测报告页，但四个车身区域页签未能可靠确认",
        "retry_instruction": "请等待管理员处理后再重新发起任务。",
    },
    "S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED": {
        "human_reason": "系统已进入参考车检测报告页，但历史修复表未能可靠确认",
        "retry_instruction": "请等待管理员处理后再重新发起任务。",
    },
    "S13_REGION_HISTORY_COUNT_BINDING_FAILED": {
        "human_reason": "系统已进入参考车检测报告页，但区域历史修复次数未能可靠绑定",
        "retry_instruction": "请等待管理员处理后再重新发起任务。",
    },
    "S13_FOUR_REGION_LOOP_GUARD_TRIGGERED": {
        "human_reason": "\u7cfb\u7edf\u5df2\u5b8c\u6210\u4e00\u8f6e\u8f66\u8eab\u56db\u533a\u57df\u5386\u53f2\u4fee\u590d\u6b21\u6570\u68c0\u67e5\uff0c\u4f46\u6d41\u7a0b\u51c6\u5907\u91cd\u590d\u56de\u5230\u9996\u4e2a\u533a\u57df\uff0c\u5df2\u4e3a\u907f\u514d\u5faa\u73af\u91c7\u96c6\u800c\u5b89\u5168\u505c\u6b62",
        "retry_instruction": "\u8bf7\u6682\u7b49\u7ba1\u7406\u5458\u5904\u7406\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002",
    },
    "SECOND_STAGE_RUNTIME_EXCEPTION": {
        "human_reason": "\u53c2\u8003\u8f66\u8be6\u60c5\u91c7\u96c6\u9636\u6bb5\u51fa\u73b0\u7cfb\u7edf\u5f02\u5e38",
        "retry_instruction": "\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\uff0c\u8bf7\u6682\u7b49\u5904\u7406\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002",
    },
    "UNKNOWN_PRECHECK_FAILED": {
        "human_reason": "手机执行环境暂不可用",
        "retry_instruction": "请确认手机和瓜子 APP 状态正常后，重新发送目标车源并回复“确认”重新开始。",
    },
}
MAINTENANCE_ERROR_CODES = {
    "TASK_NOT_FAILED",
    "TASK_NOT_REQUEUEABLE",
    "TASK_NOT_REQUEUEABLE_TO_SECOND_STAGE",
    "INVALID_REQUEUE_STATE",
    "FORCE_REQUEUE_ERROR_NOT_ALLOWED",
    "TASK_ALREADY_FINISHED",
    "STATUS_JSON_MISSING",
    "TASK_NOT_FOUND",
}
GENERIC_QUEUE_RELEASE_ERROR_CODES = {
    NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER,
    SYSTEM_PRECHECK_FAILED_NOT_STARTED,
}
POST_S10_FEEDBACK_SPECIFIC_ERROR_CODES = (
    *S10_NEXT_REFERENCE_BINDING_FAILURE_CODES,
    *S12_TO_S13_REGION_PROOF_FAILURE_CODES,
    "SECOND_STAGE_RUNTIME_EXCEPTION",
    "V149_REFERENCE_IDENTITY_SUMMARY_TYPE_ERROR",
    "RESULT_SCHEMA_INVALID_FOR_PRICING_WITH_RUNTIME_EXCEPTION",
    "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
    V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
    "DUPLICATE_REFERENCE_CLICK_BLOCKED",
    "REFERENCE_CARD_BINDING_NOT_UNIQUE",
    "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED",
    "REFERENCE_SCORE_UNTRUSTED_INCOMPLETE_COLLECTION",
    "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE",
    "S14_COLLECTION_INCOMPLETE_UNRECOVERABLE",
    "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED",
    "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED",
    "S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE",
    "S13_REPAIR_ITEM_CLICK_DID_NOT_OPEN_DETAIL",
    "S13_HISTORY_REPAIR_ENTRY_NOT_VISIBLE_OR_UNBOUND",
    "S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND",
    "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED",
    "S13_FOUR_REGION_LOOP_GUARD_TRIGGERED",
    "S14_STALE_FIRST_LINE_BINDING_UNRESOLVED",
    "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED",
    "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED",
)
POST_START_COLLECTION_INCOMPLETE_ERROR_CODES = {
    "SECOND_STAGE_RUNTIME_EXCEPTION",
    *S12_TO_S13_REGION_PROOF_FAILURE_CODES,
    "V149_REFERENCE_IDENTITY_SUMMARY_TYPE_ERROR",
    "RESULT_SCHEMA_INVALID_FOR_PRICING_WITH_RUNTIME_EXCEPTION",
    V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
    "DUPLICATE_REFERENCE_CLICK_BLOCKED",
    "REFERENCE_SCORE_UNTRUSTED_INCOMPLETE_COLLECTION",
    "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE",
    "S14_COLLECTION_INCOMPLETE_UNRECOVERABLE",
    "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED",
    "S13_HISTORY_REPAIR_ENTRY_NOT_VISIBLE_OR_UNBOUND",
    "S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND",
    "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED",
    "S13_FOUR_REGION_LOOP_GUARD_TRIGGERED",
    "S14_STALE_FIRST_LINE_BINDING_UNRESOLVED",
    "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED",
    "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED",
    "ALL_TRISAME_REFERENCES_EXHAUSTED_NEEDS_REVIEW",
    "SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED",
    "SECOND_STAGE_CONTINUATION_STATE_MISSING",
}
S07_AGE_FAILURE_ERROR_CODES = {
    "S07_AGE_SLIDER_FASTPATH_FAILED",
    "S07_AGE_SLIDER_FINAL_VALUE_MISMATCH",
    "S07_AGE_SLIDER_FALLBACK_BUDGET_EXCEEDED",
    "S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT",
    "S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED",
    "S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT",
    "S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT",
    "S07_AGE_SLIDER_HANDLE_BINDING_FAILED",
    "S07_AGE_SLIDER_DRAG_START_OUTSIDE_HANDLE_BOUNDS",
    "S07_AGE_EXACT_RANGE_VERIFY_FAILED",
    "S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE",
    "S07_AGE_ONE_HIDDEN_TICK_VERIFY_FAILED",
    "S07_AGE_ONE_POST_ACTION_VERIFY_FAILED",
    "S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED",
    "S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED",
    "S07_AGE_FILTER_ACTUAL_RANGE_MISMATCH",
    "S07_POST_ACTION_FRESH_EVIDENCE_MISSING",
    "S07_AGE_FILTER_PLANNED_ACTUAL_MISMATCH",
    "S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER",
}
GUAZI_APP_PACKAGE = "com.ganji.android.haoche_c"
PHASE1_STATUSES = {
    "INVALID",
    MODEL_RESOLUTION_STATUS,
    TARGET_INFO_RESOLUTION_STATUS,
    TARGET_INFO_NEEDS_CORRECTION,
    WAITING_TARGET_INFO_CORRECTION,
    "DRAFT",
    WAITING_TARGET_CONFIRMATION,
    "CONFIRMED",
    "QUEUED",
    "CANCELLED",
    "APP_CONTROL_LOCKED",
}
RESERVED_STATUSES = {
    "RUNNING",
    "RUNNING_FIRST_STAGE",
    "S10_READY",
    "RUNNING_SECOND_STAGE",
    "CONTINUE_NEXT_REFERENCE",
    "SECOND_STAGE_COLLECTION_INCOMPLETE",
    "SECOND_STAGE_CONTINUE_NEXT_REFERENCE_NOT_COMPLETED",
    "ALL_TRISAME_REFERENCES_EXHAUSTED_NEEDS_REVIEW",
    "SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED",
    "SECOND_STAGE_CONTINUATION_STATE_MISSING",
    "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
    "V3_BOUNDARY_NOT_CONFIRMED_AFTER_ALL_REFERENCES",
    "SUCCEEDED",
    "FAILED",
    "NEEDS_REVIEW",
    "WAITING_MANUAL_PRICE",
    "MANUAL_REVIEW_CONFIRMED",
    "RESULT_SENT",
    "SYSTEM_BLOCKED",
    "ADMIN_INTERVENTION_REQUIRED",
    "ADMIN_INTERVENTION_RESOLVED",
}


@dataclass(frozen=True)
class TaskOperationResult:
    success: bool
    action: str
    reply_text: str
    task_id: str | None = None
    status: str | None = None
    duplicate: bool = False
    changed: bool = False
    data: dict[str, Any] | None = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat(timespec="seconds")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _adb_diagnostics_from_payloads(*payloads: Any) -> dict[str, Any]:
    keys = (
        "target_adb_serial",
        "adb_path",
        "adb_path_source",
        "adb_runtime_env_mode",
        "adb_devices_l_raw",
        "parsed_devices",
        "target_device_state",
        "target_device_present_before_first_stage",
        "device_snapshot_taken_at",
        "device_snapshot_error",
        "adb_command_preview",
    )
    diagnostics: dict[str, Any] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in keys:
                if key in value and key not in diagnostics:
                    diagnostics[key] = value.get(key)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for payload in payloads:
        visit(payload)
    return diagnostics


class FeishuTaskStore:
    def __init__(
        self,
        base_dir: str | Path = DEFAULT_TASK_ROOT,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.clock = clock or _now_utc

    @property
    def task_index_path(self) -> Path:
        return self.base_dir / "task_index.json"

    @property
    def processed_message_ids_path(self) -> Path:
        return self.base_dir / "processed_message_ids.json"

    @property
    def audit_log_path(self) -> Path:
        return self.base_dir / "audit_log.jsonl"

    @property
    def data_dir(self) -> Path:
        return self.base_dir.parent

    @property
    def current_target_task_path(self) -> Path:
        return self.data_dir / "current_target_task.json"

    def task_dir(self, task_id: str) -> Path:
        return self.base_dir / task_id

    def next_task_id(self, now: datetime | None = None) -> str:
        stamp = (now or self.clock()).strftime("%Y%m%d")
        max_number = 0
        if self.base_dir.exists():
            for child in self.base_dir.iterdir():
                if not child.is_dir():
                    continue
                match = TASK_ID_RE.match(child.name)
                if match and match.group("date") == stamp:
                    max_number = max(max_number, int(match.group("number")))
        return f"FS{stamp}_{max_number + 1:04d}"

    def lookup_processed_message(self, raw_message_id: str | None) -> str | None:
        if not raw_message_id:
            return None
        processed = self._read_json(self.processed_message_ids_path, default={})
        value = processed.get(raw_message_id)
        return str(value) if value else None

    def record_processed_message(self, raw_message_id: str | None, task_id: str | None) -> None:
        if not raw_message_id or not task_id:
            return
        processed = self._read_json(self.processed_message_ids_path, default={})
        processed[raw_message_id] = task_id
        self._write_json(self.processed_message_ids_path, processed)

    def create_task_from_message(
        self,
        *,
        text: str,
        raw_event: dict[str, Any] | None = None,
        raw_message_id: str | None = None,
        raw_sender_id: str | None = None,
        raw_chat_id: str | None = None,
    ) -> TaskOperationResult:
        duplicate_task_id = self.lookup_processed_message(raw_message_id)
        if duplicate_task_id:
            self._append_audit(
                action="duplicate_message",
                task_id=duplicate_task_id,
                status=self.load_status(duplicate_task_id).get("status") if self.load_status(duplicate_task_id) else None,
                success=True,
                raw_message_id=raw_message_id,
            )
            return TaskOperationResult(
                success=True,
                action="duplicate_message",
                task_id=duplicate_task_id,
                status=self.load_status(duplicate_task_id).get("status") if self.load_status(duplicate_task_id) else None,
                duplicate=True,
                reply_text=f"【定价已开始】{duplicate_task_id}\n系统已开始自动定价，请等待结果。",
            )

        now = self.clock()
        task_id = self.next_task_id(now)
        task_dir = self.task_dir(task_id)
        if task_dir.exists():
            raise FileExistsError(f"task directory already exists: {task_dir}")
        task_dir.mkdir(parents=True)

        parsed = parse_target_task_message(
            text,
            task_id=task_id,
            raw_message_id=raw_message_id,
            raw_sender_id=raw_sender_id,
            raw_chat_id=raw_chat_id,
            clock=self.clock,
        )
        confirm_card_message_id = f"confirm_card:{task_id}"
        raw_payload = {
            "task_id": task_id,
            "raw_message_id": raw_message_id,
            "raw_sender_id": raw_sender_id,
            "raw_chat_id": raw_chat_id,
            "source_message_id": raw_message_id,
            "source_message_time": _isoformat(now),
            "sender_open_id": raw_sender_id,
            "business_chat_id": raw_chat_id,
            "confirm_card_message_id": confirm_card_message_id,
            "text": text,
            "received_at": _isoformat(now),
            "event": raw_event or {},
        }
        metadata = {
            "business_chat_id": raw_chat_id,
            "sender_open_id": raw_sender_id,
            "source_message_id": raw_message_id,
            "source_message_time": _isoformat(now),
            "confirm_card_message_id": confirm_card_message_id,
        }
        parsed.draft.update({key: value for key, value in metadata.items() if value})
        parsed.status.update({key: value for key, value in metadata.items() if value})
        reply_text = parsed.reply_text
        if not parsed.valid:
            correction_fields = target_info_status_fields(clock=self.clock)
            parsed.status.update(correction_fields)
            parsed.status["status"] = TARGET_INFO_NEEDS_CORRECTION
            parsed.draft["status"] = TARGET_INFO_NEEDS_CORRECTION
            parsed.validation_result["target_info_correction"] = True
        self._write_json(task_dir / "raw_message.json", raw_payload)
        self._write_json(task_dir / "target_task_draft.json", parsed.draft)
        self._write_json(task_dir / "validation_result.json", parsed.validation_result)
        self._write_json(task_dir / "status.json", parsed.status)
        if not parsed.valid:
            feedback = write_target_info_correction_feedback(
                task_dir=task_dir,
                task_id=task_id,
                status_payload=parsed.status,
                draft=parsed.draft,
                errors=list(parsed.validation_result.get("date_errors") or []) + list(parsed.validation_result.get("model_resolution_errors") or []),
                missing_fields=list(parsed.validation_result.get("missing_required_fields") or []),
                validation_result=parsed.validation_result,
                dry_run=True,
                clock=self.clock,
            )
            reply_text = feedback["reply_text"]
        (task_dir / "reply_preview.txt").write_text(reply_text + "\n", encoding="utf-8")
        self._upsert_index(parsed.status)
        self.record_processed_message(raw_message_id, task_id)
        self._append_audit(
            action="create_task",
            task_id=task_id,
            status=parsed.status["status"],
            success=parsed.valid,
            raw_message_id=raw_message_id,
        )
        return TaskOperationResult(
            success=parsed.valid,
            action="create_task",
            task_id=task_id,
            status=parsed.status["status"],
            changed=True,
            data={
                "draft": parsed.draft,
                "validation_result": parsed.validation_result,
                "status": parsed.status,
                "confirm_card_message_id": confirm_card_message_id,
            },
            reply_text=reply_text,
        )

    def load_status(self, task_id: str) -> dict[str, Any] | None:
        path = self.task_dir(task_id) / "status.json"
        if not path.exists():
            return None
        return self._read_json(path, default=None)

    def load_draft(self, task_id: str) -> dict[str, Any] | None:
        path = self.task_dir(task_id) / "target_task_draft.json"
        if not path.exists():
            return None
        return self._read_json(path, default=None)

    def latest_task_by_status(self, statuses: set[str]) -> str | None:
        candidates: list[tuple[str, str]] = []
        if not self.base_dir.exists():
            return None
        for child in self.base_dir.iterdir():
            if not child.is_dir():
                continue
            status = self.load_status(child.name)
            if not status or status.get("status") not in statuses:
                continue
            updated_at = str(status.get("updated_at") or status.get("created_at") or "")
            candidates.append((updated_at, child.name))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def confirm_latest_target_task(self) -> TaskOperationResult:
        task_id = self.latest_task_by_status(WAITING_CONFIRMATION_STATUSES)
        if not task_id:
            task_id = self.latest_task_by_status({"CONFIRMED", "QUEUED"})
            if task_id:
                return self.confirm_task(task_id)
        if not task_id:
            return TaskOperationResult(
                success=False,
                action="confirm_latest_task",
                changed=False,
                reply_text="请先发送目标车信息。请回复“确认”两个字确认目标车信息，或重新发送目标车信息。",
            )
        return self.confirm_task(task_id)

    def confirm_bound_target_task(
        self,
        *,
        sender_open_id: str | None,
        business_chat_id: str | None,
        reply_to_message_id: str | None = None,
        parent_message_id: str | None = None,
    ) -> TaskOperationResult:
        bound_message_id = reply_to_message_id or parent_message_id
        if bound_message_id:
            task_id = self.find_task_by_confirm_card_message_id(
                bound_message_id,
                business_chat_id=business_chat_id,
            )
            if not task_id:
                return TaskOperationResult(
                    success=False,
                    action="confirm_bound_task",
                    changed=False,
                    reply_text="未找到对应待确认任务，请回复对应确认卡，并输入“确认”。",
                )
            return self.confirm_task(task_id, confirmed_by_open_id=sender_open_id)

        candidates = self.waiting_confirmation_tasks(
            sender_open_id=sender_open_id,
            business_chat_id=business_chat_id,
        )
        if len(candidates) == 1:
            return self.confirm_task(candidates[0], confirmed_by_open_id=sender_open_id)
        if not candidates:
            return TaskOperationResult(
                success=False,
                action="confirm_bound_task",
                changed=False,
                reply_text="请先发送目标车信息。请回复对应确认卡，并输入“确认”。",
            )
        return TaskOperationResult(
            success=False,
            action="confirm_bound_task",
            changed=False,
            reply_text="你当前有多个待确认任务，请回复对应确认卡，并输入“确认”。",
            data={"candidate_task_ids": candidates},
        )

    def confirm_task(self, task_id: str, *, confirmed_by_open_id: str | None = None) -> TaskOperationResult:
        status = self.load_status(task_id)
        if not status:
            return self._unknown_task("confirm_task", task_id)
        current = status["status"]
        if current in WAITING_CONFIRMATION_STATUSES:
            draft = self.load_draft(task_id) or {}
            build_result = build_current_target_task(draft, clock=self.clock)
            if not build_result.valid:
                feedback = self._mark_target_info_correction(
                    task_id,
                    status,
                    draft=draft,
                    errors=["MISSING_REQUIRED_FIELDS"],
                    missing_fields=build_result.missing_fields,
                )
                return TaskOperationResult(
                    success=False,
                    action="target_info_correction_required",
                    task_id=task_id,
                    status=TARGET_INFO_NEEDS_CORRECTION,
                    changed=True,
                    reply_text=feedback["reply_text"],
                    data=feedback,
                )
            self._write_json(self.task_dir(task_id) / "current_target_task.preview.json", build_result.current_target_task)
            self._write_json(self.task_dir(task_id) / "current_target_task.snapshot.json", build_result.current_target_task)
            queued_at = _isoformat(self.clock())
            extra = {
                "confirmed_at": queued_at,
                "queued_at": queued_at,
                "start_ack_sent": True,
                "start_ack_sent_at": queued_at,
                "start_ack_message_id": None,
                "start_ack_generated": True,
                "start_ack_returned_to_gateway": True,
                "start_ack_live_send_attempted": False,
                "start_ack_live_sent": False,
                "dispatch_kick_attempted": False,
                "dispatch_kick_failed": False,
                "duplicate_confirm_ignored": False,
            }
            if confirmed_by_open_id:
                extra["confirmed_by_open_id"] = confirmed_by_open_id
            updated = self._set_status(task_id, "QUEUED", extra=extra)
            queue_position = self.queue_position(task_id)
            reply_text = f"【定价已开始】{task_id}\n系统已开始自动定价，请等待结果。"
            start_trace = self._persist_feishu_start_message_trace(task_id, reply_text=reply_text)
            self._append_audit(action="confirm_task", task_id=task_id, status="QUEUED", success=True)
            return TaskOperationResult(
                success=True,
                action="confirm_task",
                task_id=task_id,
                status="QUEUED",
                changed=True,
                data={**updated, "queue_position": queue_position, "feishu_start_message_trace": start_trace},
                reply_text=reply_text,
            )
        if current in {"CONFIRMED", "QUEUED"}:
            reply_text = f"【定价已开始】{task_id}\n系统已开始自动定价，请等待结果。"
            start_trace = self._persist_feishu_start_message_trace(task_id, reply_text=reply_text)
            self._append_audit(action="confirm_task", task_id=task_id, status=current, success=True)
            return TaskOperationResult(
                success=True,
                action="confirm_task",
                task_id=task_id,
                status=current,
                changed=False,
                data={"feishu_start_message_trace": start_trace},
                reply_text=reply_text,
            )
        if current == "INVALID":
            reply = f"任务 {task_id} 状态为 INVALID，不能确认。请补齐必填字段后重新发送完整定价模板。"
        elif current in {TARGET_INFO_NEEDS_CORRECTION, WAITING_TARGET_INFO_CORRECTION, TARGET_INFO_RESOLUTION_STATUS, MODEL_RESOLUTION_STATUS}:
            reply = "这台车源信息需要修改，请重新发送完整目标车源信息，我会重新生成任务并排队。"
        elif current == "CANCELLED":
            reply = f"任务 {task_id} 已取消，不能确认。"
            reply = CANCELLED_TASK_RESEND_REPLY
        else:
            reply = f"任务 {task_id} 状态为 {current}，Phase 1 不允许确认。"
        return TaskOperationResult(
            success=False,
            action="confirm_task",
            task_id=task_id,
            status=current,
            changed=False,
            reply_text=reply,
        )

    def _persist_feishu_start_message_trace(self, task_id: str, *, reply_text: str) -> dict[str, Any]:
        """Persist the start-message ordering evidence returned to Feishu."""
        path = self.task_dir(task_id) / "feishu_start_message_delivery.json"
        existing = self._read_json(path, default={})
        if isinstance(existing, dict) and existing.get("confirm_message_idempotent_key"):
            return existing
        now_text = _isoformat(self.clock())
        payload = {
            "ok": True,
            "dry_run": True,
            "task_id": task_id,
            "reply_text": reply_text,
            "start_ack_sent": True,
            "start_ack_sent_at": now_text,
            "start_ack_generated": True,
            "start_ack_generated_at": now_text,
            "start_ack_returned_to_gateway": True,
            "start_ack_returned_to_gateway_at": now_text,
            "start_ack_live_send_attempted": False,
            "start_ack_live_sent": False,
            "start_ack_message_id": None,
            "feishu_confirm_event_received_at": now_text,
            "feishu_start_message_send_requested_at": now_text,
            "feishu_start_message_persisted_at": now_text,
            "feishu_start_message_sent_at": None,
            "send_result": "returned_to_feishu_gateway_for_send",
            "message_order_guard_passed": True,
            "confirm_message_idempotent_key": f"feishu_start_message:{task_id}",
            "confirm_event_id": None,
            "confirm_message_id": None,
            "dispatch_kick_attempted": False,
            "dispatch_kick_failed": False,
            "duplicate_confirm_ignored": False,
            "duplicate_confirm_ignored_at": None,
        }
        self._write_json(path, payload)
        return payload

    def record_confirm_dispatch_kick_trace(
        self,
        task_id: str,
        *,
        kick_result: dict[str, Any],
        dispatch_kick_failed: bool,
        failure_feedback_blocked_before_start_ack: bool,
    ) -> dict[str, Any]:
        path = self.task_dir(task_id) / "feishu_start_message_delivery.json"
        payload = self._read_json(path, default={})
        now_text = _isoformat(self.clock())
        if not isinstance(payload, dict):
            payload = {}
        payload.update(
            {
                "task_id": task_id,
                "dispatch_kick_attempted": True,
                "dispatch_kick_attempted_at": now_text,
                "dispatch_kick_failed": bool(dispatch_kick_failed),
                "dispatch_kick_failed_at": now_text if dispatch_kick_failed else None,
                "start_ack_dispatch_kick_started_background": bool(kick_result.get("dispatch_once_started_background")),
                "start_ack_dispatch_kick_sync_blocked": bool(kick_result.get("start_ack_dispatch_kick_sync_blocked", False)),
                "confirm_handler_sync_dispatch_forbidden": bool(kick_result.get("confirm_handler_sync_dispatch_forbidden", False)),
                "dispatch_kick_failure_admin_only": bool(dispatch_kick_failed),
                "failure_feedback_blocked_before_start_ack": bool(failure_feedback_blocked_before_start_ack),
                "confirm_failure_feedback_guard_code": "FEISHU_CONFIRM_FAILURE_FEEDBACK_BLOCKED_BEFORE_START_ACK"
                if failure_feedback_blocked_before_start_ack
                else "",
                "confirm_double_path_feedback_prevented": bool(failure_feedback_blocked_before_start_ack),
                "confirm_double_path_feedback_prevented_code": "FEISHU_CONFIRM_DOUBLE_PATH_FEEDBACK_PREVENTED"
                if failure_feedback_blocked_before_start_ack
                else "",
                "dispatch_kick_result_summary": {
                    "ok": kick_result.get("ok"),
                    "dispatch_once_called": kick_result.get("dispatch_once_called"),
                    "dispatch_once_started_background": kick_result.get("dispatch_once_started_background"),
                    "dispatcher_loop_running": kick_result.get("dispatcher_loop_running"),
                    "status": kick_result.get("status") or kick_result.get("message"),
                },
            }
        )
        self._write_json(path, payload)
        return payload

    def record_start_ack_delivery_result(
        self,
        task_id: str,
        *,
        send_result: dict[str, Any] | None,
        returned_to_gateway: bool,
    ) -> dict[str, Any]:
        path = self.task_dir(task_id) / "feishu_start_message_delivery.json"
        payload = self._read_json(path, default={})
        now_text = _isoformat(self.clock())
        if not isinstance(payload, dict):
            payload = {"task_id": task_id}
        send_result = send_result if isinstance(send_result, dict) else {}
        dry_run = bool(send_result.get("dry_run"))
        message_id = str(send_result.get("message_id") or "")
        live_attempted = bool(send_result) and not dry_run and not send_result.get("skipped")
        live_sent = bool(send_result.get("ok")) and live_attempted and bool(message_id)
        payload.update(
            {
                "task_id": task_id,
                "start_ack_returned_to_gateway": bool(returned_to_gateway),
                "start_ack_returned_to_gateway_at": now_text if returned_to_gateway else payload.get("start_ack_returned_to_gateway_at"),
                "start_ack_live_send_attempted": live_attempted,
                "start_ack_live_send_attempted_at": now_text if live_attempted else payload.get("start_ack_live_send_attempted_at"),
                "start_ack_live_sent": live_sent,
                "start_ack_live_sent_at": now_text if live_sent else payload.get("start_ack_live_sent_at"),
                "start_ack_message_id": message_id or payload.get("start_ack_message_id"),
                "start_ack_send_result": send_result,
                "start_ack_sent": bool(returned_to_gateway or live_sent),
                "start_ack_sent_at": payload.get("start_ack_sent_at") or now_text if (returned_to_gateway or live_sent) else payload.get("start_ack_sent_at"),
            }
        )
        self._write_json(path, payload)
        status_path = self.task_dir(task_id) / "status.json"
        status_payload = self._read_json(status_path, default={})
        if isinstance(status_payload, dict) and status_payload:
            status_payload.update(
                {
                    "start_ack_sent": bool(returned_to_gateway or live_sent),
                    "start_ack_sent_at": status_payload.get("start_ack_sent_at") or now_text if (returned_to_gateway or live_sent) else status_payload.get("start_ack_sent_at"),
                    "start_ack_generated": True,
                    "start_ack_returned_to_gateway": bool(returned_to_gateway),
                    "start_ack_live_send_attempted": live_attempted,
                    "start_ack_live_sent": live_sent,
                    "start_ack_message_id": message_id or status_payload.get("start_ack_message_id"),
                }
            )
            self._write_json(status_path, status_payload)
        return payload

    def record_duplicate_confirm_ignored(
        self,
        task_id: str,
        *,
        raw_message_id: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        path = self.task_dir(task_id) / "feishu_start_message_delivery.json"
        payload = self._read_json(path, default={})
        now_text = _isoformat(self.clock())
        if not isinstance(payload, dict):
            payload = {}
        payload.update(
            {
                "task_id": task_id,
                "duplicate_confirm_ignored": True,
                "duplicate_confirm_ignored_at": now_text,
                "duplicate_confirm_message_id": raw_message_id,
                "duplicate_confirm_event_id": event_id,
                "duplicate_confirm_guard_code": "FEISHU_CONFIRM_START_ACK_ALREADY_SENT_DUPLICATE_IGNORED",
            }
        )
        self._write_json(path, payload)
        self._append_audit(
            action="duplicate_confirm_ignored",
            task_id=task_id,
            status=(self.load_status(task_id) or {}).get("status"),
            success=True,
            raw_message_id=raw_message_id,
        )
        return payload

    def record_confirm_preflight_failure_duplicate_replayed(
        self,
        task_id: str,
        *,
        raw_message_id: str | None = None,
        event_id: str | None = None,
        replayed_reply_text: str = "",
        silent: bool = False,
    ) -> dict[str, Any]:
        path = self.task_dir(task_id) / "confirm_preflight_duplicate_delivery.json"
        payload = self._read_json(path, default={})
        now_text = _isoformat(self.clock())
        if not isinstance(payload, dict):
            payload = {}
        guard_code = (
            "FEISHU_CONFIRM_PREFLIGHT_FAILURE_DUPLICATE_IGNORED"
            if silent
            else "FEISHU_CONFIRM_PREFLIGHT_FAILURE_DUPLICATE_REPLAYED"
        )
        payload.update(
            {
                "task_id": task_id,
                "duplicate_confirm_message_id": raw_message_id,
                "duplicate_confirm_event_id": event_id,
                "duplicate_confirm_observed_at": now_text,
                "confirm_preflight_failure_duplicate": True,
                "duplicate_confirm_replayed": not silent,
                "duplicate_confirm_ignored": bool(silent),
                "duplicate_confirm_guard_code": guard_code,
                "start_ack_blocked": True,
                "start_ack_blocked_code": "FEISHU_DUPLICATE_MESSAGE_START_ACK_BLOCKED_BY_PREFLIGHT_FAILURE",
                "processed_message_outcome": "confirm_preflight_failed",
                "replayed_reply_text": replayed_reply_text,
            }
        )
        self._write_json(path, payload)
        self._append_audit(
            action="duplicate_confirm_preflight_failure_replayed" if not silent else "duplicate_confirm_preflight_failure_ignored",
            task_id=task_id,
            status=(self.load_status(task_id) or {}).get("status"),
            success=False,
            raw_message_id=raw_message_id,
        )
        return payload

    def record_duplicate_message_no_start_ack_without_started_evidence(
        self,
        task_id: str,
        *,
        raw_message_id: str | None = None,
        event_id: str | None = None,
        command: str | None = None,
    ) -> dict[str, Any]:
        path = self.task_dir(task_id) / "duplicate_message_guard_trace.json"
        payload = self._read_json(path, default={})
        now_text = _isoformat(self.clock())
        if not isinstance(payload, dict):
            payload = {}
        payload.update(
            {
                "task_id": task_id,
                "duplicate_message_id": raw_message_id,
                "duplicate_event_id": event_id,
                "duplicate_command": command,
                "duplicate_observed_at": now_text,
                "reply_text": "",
                "start_ack_blocked": True,
                "duplicate_message_guard_code": "FEISHU_DUPLICATE_MESSAGE_NO_START_ACK_WITHOUT_STARTED_EVIDENCE",
                "processed_message_outcome": "duplicate_ignored_without_started_evidence",
            }
        )
        self._write_json(path, payload)
        self._append_audit(
            action="duplicate_message_no_start_ack_without_started_evidence",
            task_id=task_id,
            status=(self.load_status(task_id) or {}).get("status"),
            success=True,
            raw_message_id=raw_message_id,
        )
        return payload

    def record_confirm_preflight_failure(self, task_id: str, preflight_result: dict[str, Any]) -> TaskOperationResult:
        status = self.load_status(task_id)
        if not status:
            return self._unknown_task("confirm_preflight_failed", task_id)
        current = str(status.get("status") or "")
        if current not in WAITING_CONFIRMATION_STATUSES:
            return TaskOperationResult(
                success=False,
                action="confirm_preflight_failed",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text=f"任务 {task_id} 当前状态为 {current}，不能执行确认前检查。",
                data={"preflight": preflight_result},
            )

        now_text = _isoformat(self.clock())
        error_code = str(preflight_result.get("error_code") or preflight_result.get("status") or "DEVICE_READY_PRECHECK_FAILED")
        business_reply = str(preflight_result.get("business_reply_text") or "")
        if not business_reply:
            details = self.concrete_failure_details(task_id, status=status, errors=[error_code], result=preflight_result)
            business_reply = str(details.get("business_reply_text") or "本次定价未开始，执行手机当前未就绪。")
        admin_reply = str(preflight_result.get("admin_reply_text") or "")
        attempts = int(status.get("confirm_preflight_attempts") or 0) + 1
        extra = {
            "confirm_preflight_failed": True,
            "confirm_preflight_status": preflight_result.get("status") or error_code,
            "confirm_preflight_error_code": error_code,
            "confirm_preflight_failed_at": now_text,
            "confirm_preflight_attempts": attempts,
            "confirm_preflight_business_reply_text": business_reply,
            "confirm_preflight_admin_reply_text": admin_reply,
            "device_ready_for_pricing": False,
            "started": False,
            "blocks_queue": False,
            "queued_at": None,
        }
        updated = self._set_status(task_id, current, extra=extra)
        task_dir = self.task_dir(task_id)
        snapshot_payload = {
            "task_id": task_id,
            "ok": False,
            "status": preflight_result.get("status") or error_code,
            "error_code": error_code,
            "device_ready_for_pricing": False,
            "should_enqueue": False,
            "should_start_runner": False,
            "preflight_result": preflight_result,
            "created_at": now_text,
        }
        self._write_json(task_dir / "confirm_preflight_gate_snapshot.json", snapshot_payload)
        (task_dir / "confirm_preflight_business_reply.preview.txt").write_text(business_reply + "\n", encoding="utf-8")
        if admin_reply:
            (task_dir / "confirm_preflight_admin_reply.preview.txt").write_text(admin_reply + "\n", encoding="utf-8")
        self._append_audit(action="confirm_preflight_failed", task_id=task_id, status=current, success=False)
        return TaskOperationResult(
            success=False,
            action="confirm_preflight_failed",
            task_id=task_id,
            status=current,
            changed=True,
            reply_text=business_reply,
            data={**updated, "preflight": snapshot_payload},
        )

    def cancel_task(self, task_id: str) -> TaskOperationResult:
        status = self.load_status(task_id)
        if not status:
            return self._unknown_task("cancel_task", task_id)
        current = status["status"]
        if current in WAITING_CONFIRMATION_STATUSES | {"CONFIRMED", "QUEUED"}:
            updated = self._set_status(task_id, "CANCELLED")
            self._append_audit(action="cancel_task", task_id=task_id, status="CANCELLED", success=True)
            return TaskOperationResult(
                success=True,
                action="cancel_task",
                task_id=task_id,
                status="CANCELLED",
                changed=True,
                data=updated,
                reply_text=f"任务 {task_id} 已取消。",
            )
        if current == "CANCELLED":
            self._append_audit(action="cancel_task", task_id=task_id, status=current, success=True)
            return TaskOperationResult(
                success=True,
                action="cancel_task",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text=f"任务 {task_id} 已取消。",
            )
        return TaskOperationResult(
            success=False,
            action="cancel_task",
            task_id=task_id,
            status=current,
            changed=False,
            reply_text=f"任务 {task_id} 状态为 {current}，不能取消。",
        )

    def status_reply(self, task_id: str) -> TaskOperationResult:
        status = self.load_status(task_id)
        if not status:
            return self._unknown_task("status_task", task_id)
        draft = self.load_draft(task_id) or {}
        target = " ".join(
            str(draft.get(field, "")).strip()
            for field in ("brand", "series", "model_config")
            if draft.get(field)
        )
        return TaskOperationResult(
            success=True,
            action="status_task",
            task_id=task_id,
            status=status["status"],
            changed=False,
            data={"status": status, "draft": draft},
            reply_text="\n".join(
                [
                    f"任务：{task_id}",
                    f"状态：{status['status']}",
                    f"创建时间：{status.get('created_at', '')}",
                    f"目标车：{target}",
                ]
            ),
        )

    def bind_confirm_card_message_id(self, task_id: str, confirm_card_message_id: str) -> dict[str, Any]:
        status = self.load_status(task_id)
        if not status:
            raise FileNotFoundError(f"task status not found: {task_id}")
        return self._set_status(
            task_id,
            str(status.get("status") or ""),
            extra={"confirm_card_message_id": confirm_card_message_id},
        )

    def waiting_confirmation_tasks(
        self,
        *,
        sender_open_id: str | None,
        business_chat_id: str | None,
    ) -> list[str]:
        return [
            task_id
            for task_id, status in self._iter_statuses()
            if status.get("status") in WAITING_CONFIRMATION_STATUSES
            and (sender_open_id is None or status.get("sender_open_id") == sender_open_id or status.get("raw_sender_id") == sender_open_id)
            and (business_chat_id is None or status.get("business_chat_id") == business_chat_id or status.get("raw_chat_id") == business_chat_id)
        ]

    def find_task_by_confirm_card_message_id(
        self,
        confirm_card_message_id: str,
        *,
        business_chat_id: str | None,
    ) -> str | None:
        for task_id, status in self._iter_statuses():
            if status.get("status") not in WAITING_CONFIRMATION_STATUSES:
                continue
            if status.get("confirm_card_message_id") != confirm_card_message_id:
                continue
            if business_chat_id and status.get("business_chat_id") not in {business_chat_id, None} and status.get("raw_chat_id") != business_chat_id:
                continue
            return task_id
        return None

    def queued_tasks(self) -> list[str]:
        candidates: list[tuple[str, str]] = []
        for task_id, status in self._iter_statuses():
            if status.get("status") != "QUEUED":
                continue
            stamp = str(status.get("queued_at") or status.get("confirmed_at") or status.get("updated_at") or status.get("created_at") or "")
            candidates.append((stamp, task_id))
        candidates.sort()
        return [task_id for _, task_id in candidates]

    def queue_position(self, task_id: str) -> int:
        queued = self.queued_tasks()
        return queued.index(task_id) + 1 if task_id in queued else 0

    def active_app_task(self) -> str | None:
        for task_id, status in self._iter_statuses():
            if status.get("status") in ACTIVE_APP_STATUSES:
                return task_id
        return None

    def admin_blocked_tasks(self, *, recoverable_only: bool = False) -> list[str]:
        candidates: list[tuple[str, str]] = []
        for task_id, status in self._iter_statuses():
            if status.get("status") not in {SYSTEM_BLOCKED, ADMIN_INTERVENTION_REQUIRED}:
                continue
            if recoverable_only and not is_recoverable_admin_error(errors=self._admin_error_codes(status), result=status):
                continue
            stamp = str(status.get("updated_at") or status.get("created_at") or "")
            candidates.append((stamp, task_id))
        candidates.sort()
        return [task_id for _, task_id in candidates]

    def system_blocked_tasks_ready_for_health_check(
        self,
        *,
        cooldown_seconds: int = DEFAULT_HEALTH_CHECK_COOLDOWN_SECONDS,
        force: bool = False,
    ) -> list[str]:
        candidates: list[tuple[str, str]] = []
        for task_id, status in self._iter_statuses():
            if status.get("status") not in {SYSTEM_BLOCKED, ADMIN_INTERVENTION_REQUIRED}:
                continue
            error_codes = self._admin_error_codes(status)
            if not is_auto_health_recoverable_error(errors=error_codes, result=status):
                continue
            if not self.system_blocked_health_check_due(status, cooldown_seconds=cooldown_seconds, force=force):
                continue
            stamp = str(status.get("system_blocked_at") or status.get("updated_at") or status.get("created_at") or "")
            candidates.append((stamp, task_id))
        candidates.sort()
        return [task_id for _, task_id in candidates]

    def system_blocked_health_check_due(
        self,
        status: dict[str, Any],
        *,
        cooldown_seconds: int = DEFAULT_HEALTH_CHECK_COOLDOWN_SECONDS,
        force: bool = False,
    ) -> bool:
        if force:
            return True
        last_checked = self._parse_datetime(status.get("last_health_check_at"))
        if last_checked is None:
            return True
        return self.clock() >= last_checked + timedelta(seconds=cooldown_seconds)

    def system_blocked_next_health_check_at(
        self,
        status: dict[str, Any],
        *,
        cooldown_seconds: int = DEFAULT_HEALTH_CHECK_COOLDOWN_SECONDS,
    ) -> str:
        last_checked = self._parse_datetime(status.get("last_health_check_at"))
        if last_checked is None:
            return _isoformat(self.clock())
        return _isoformat(last_checked + timedelta(seconds=cooldown_seconds))

    def record_system_blocked_health_check(
        self,
        task_id: str,
        health_result: dict[str, Any],
        *,
        cooldown_seconds: int = DEFAULT_HEALTH_CHECK_COOLDOWN_SECONDS,
    ) -> dict[str, Any]:
        status = self.load_status(task_id)
        if not status:
            raise FileNotFoundError(f"task status not found: {task_id}")
        now = _isoformat(self.clock())
        count = int(status.get("health_check_count") or 0) + 1
        return self._set_status(
            task_id,
            str(status.get("status") or ""),
            extra={
                "last_health_check_at": now,
                "health_check_count": count,
                "last_health_check_ok": bool(health_result.get("ok")),
                "last_health_check_status": health_result.get("status"),
                "last_health_check_errors": list(health_result.get("errors") or []),
                "next_health_check_at": self.system_blocked_next_health_check_at(
                    {"last_health_check_at": now},
                    cooldown_seconds=cooldown_seconds,
                ),
            },
        )

    def auto_cancel_not_started_system_precheck_failure(
        self,
        task_id: str,
        *,
        errors: list[str] | None = None,
        result: dict[str, Any] | None = None,
        force_not_started: bool = False,
        dry_run: bool = True,
        message_sender: Callable[..., dict[str, Any]] | None = None,
    ) -> TaskOperationResult:
        status = self.load_status(task_id)
        if not status:
            return self._unknown_task("auto_cancel_not_started_system_precheck_failure", task_id)
        if not self.is_not_started_system_precheck_failure(
            task_id,
            status=status,
            errors=errors,
            result=result,
            force_not_started=force_not_started,
        ):
            return TaskOperationResult(
                success=False,
                action="auto_cancel_not_started_system_precheck_failure",
                task_id=task_id,
                status=str(status.get("status") or ""),
                changed=False,
                reply_text="NOT_NOT_STARTED_SYSTEM_PRECHECK_FAILURE",
            )

        error_codes = self._not_started_precheck_error_codes(task_id, status=status, errors=errors, result=result)
        details = self.concrete_failure_details(task_id, status=status, errors=error_codes, result=result)
        primary = str(details.get("canonical_error_code") or (error_codes[0] if error_codes else SYSTEM_PRECHECK_FAILED_NOT_STARTED))
        cancelled_at = _isoformat(self.clock())
        extra = {
            "technical_status": "NOT_STARTED",
            "business_status": "CANCELLED",
            "recommended_next_action": "resend-target-info",
            "cancel_reason": SYSTEM_PRECHECK_FAILED_NOT_STARTED,
            "canonical_error_code": primary,
            "canonical_error_codes": error_codes,
            "user_facing_error_code": primary,
            "canonical_blocking_error_code": primary,
            "canonical_blocking_error_codes": error_codes,
            "admin_intervention_error_code": primary,
            "admin_intervention_error_codes": error_codes,
            "last_blocking_error_code": primary,
            "last_blocking_error_codes": error_codes,
            "started": False,
            "runner_started": False,
            "phone_flow_started": False,
            "blocks_queue": False,
            "recoverable_by_health_check": False,
            "auto_cancelled": True,
            "auto_cancelled_at": cancelled_at,
            "auto_cancelled_reason": SYSTEM_PRECHECK_FAILED_NOT_STARTED,
            "auto_cancelled_from_status": status.get("status"),
            "errors": error_codes,
            "human_reason": details.get("human_reason"),
            "user_facing_reason": details.get("user_facing_reason") or details.get("human_reason"),
            "reached_s10_before_failure": bool(details.get("reached_s10_before_failure")),
            "app_foreground_confirmed_before_failure": bool(details.get("app_foreground_confirmed_before_failure")),
            "entered_s11_before_failure": bool(details.get("entered_s11_before_failure")),
            "last_state": details.get("last_state"),
            "root_exception_type": details.get("root_exception_type"),
            "root_exception_message": details.get("root_exception_message"),
            "root_exception_function": details.get("root_exception_function"),
            "root_exception_file": details.get("root_exception_file"),
            "root_cause_code": details.get("root_cause_code"),
            "wrapper_error_code": details.get("wrapper_error_code"),
            "pricing_result_issue_code": details.get("pricing_result_issue_code"),
            "binding_stop_code": details.get("binding_stop_code"),
            **self._failure_classification_status_fields(details),
            "retry_instruction": details.get("retry_instruction"),
            "final_feedback_generated": False,
            "final_feedback_delivery_dry_run": bool(dry_run),
            "final_feedback_send_attempted": False,
            "final_feedback_sent": False,
            "final_feedback_sent_at": "",
            "final_feedback_message_id": "",
            "final_feedback_sent_flag_valid": False,
            "final_feedback_type": "confirm_failure_cancelled",
        }
        updated = self._set_status(task_id, "CANCELLED", extra=extra)
        feedback = self._write_not_started_auto_cancel_feedback(
            task_id,
            status_payload={**status, **updated},
            error_codes=error_codes,
            result=result,
            dry_run=dry_run,
            message_sender=message_sender,
        )
        updated = self.update_task_status_fields(
            task_id,
            fields=self._final_feedback_status_fields(feedback, feedback_type="confirm_failure_cancelled"),
        )
        self._append_audit(
            action="auto_cancel_not_started_system_precheck_failure",
            task_id=task_id,
            status="CANCELLED",
            success=True,
        )
        return TaskOperationResult(
            success=True,
            action="auto_cancel_not_started_system_precheck_failure",
            task_id=task_id,
            status="CANCELLED",
            changed=True,
            reply_text=feedback["business_reply_text"],
            data={"status": updated, "feedback": feedback, "error_codes": error_codes},
        )

    def is_not_started_system_precheck_failure(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        errors: list[str] | None = None,
        result: dict[str, Any] | None = None,
        force_not_started: bool = False,
    ) -> bool:
        status = status or self.load_status(task_id) or {}
        current = str(status.get("status") or "")
        if current in {"CANCELLED", "FAILED", "SUCCEEDED", "NEEDS_REVIEW", TARGET_INFO_NEEDS_CORRECTION}:
            return False
        error_codes = self._not_started_precheck_error_codes(task_id, status=status, errors=errors, result=result)
        if not (set(error_codes) & NOT_STARTED_SYSTEM_PRECHECK_ERROR_CODES):
            return False
        if self._task_runner_or_phone_started(task_id, status=status):
            return False
        if force_not_started:
            return True
        return self._has_not_started_precheck_signal(task_id, status=status)

    def _not_started_precheck_error_codes(
        self,
        task_id: str,
        *,
        status: dict[str, Any],
        errors: list[str] | None = None,
        result: dict[str, Any] | None = None,
    ) -> list[str]:
        direct = self._filter_blocking_error_codes(collect_error_codes(errors=errors, result=result))
        status_codes = self._admin_error_codes(status)
        combined = direct + status_codes
        return self._filter_blocking_error_codes(combined)

    def _has_not_started_precheck_signal(self, task_id: str, *, status: dict[str, Any]) -> bool:
        if status.get("started") is False or status.get("runner_started") is False:
            return True
        if status.get("failed_step") == "system-health-preflight":
            return True
        dispatcher_result = self._read_json(self.task_dir(task_id) / "dispatcher_result.json", default={})
        if isinstance(dispatcher_result, dict):
            return dispatcher_result.get("started") is False and dispatcher_result.get("failed_step") == "system-health-preflight"
        return False

    def _task_runner_or_phone_started(self, task_id: str, *, status: dict[str, Any]) -> bool:
        if status.get("started") is True or status.get("runner_started") is True or status.get("phone_flow_started") is True:
            return True
        for key in (
            "run_started_at",
            "first_stage_started_at",
            "second_stage_started_at",
            "app_control_locked_at",
            "started_at",
        ):
            if status.get(key):
                return True
        task_dir = self.task_dir(task_id)
        for filename in (
            "first_stage_run_meta.json",
            "second_stage_run_meta.json",
            "first_stage_result.json",
            "second_stage_result.json",
        ):
            payload = self._read_json(task_dir / filename, default={})
            if isinstance(payload, dict) and payload:
                return True
        dispatcher_result = self._read_json(task_dir / "dispatcher_result.json", default={})
        if isinstance(dispatcher_result, dict) and dispatcher_result.get("started") is True:
            return True
        return False

    def _write_not_started_auto_cancel_feedback(
        self,
        task_id: str,
        *,
        status_payload: dict[str, Any],
        error_codes: list[str],
        result: dict[str, Any] | None = None,
        dry_run: bool = True,
        message_sender: Callable[..., dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        details = self.concrete_failure_details(task_id, status=status_payload, errors=error_codes, result=result)
        return self._write_final_failure_feedback(
            task_id,
            status_payload=status_payload,
            details=details,
            cancel_reason=SYSTEM_PRECHECK_FAILED_NOT_STARTED,
            result=result,
            file_prefix="not_started_auto_cancel",
            dry_run=dry_run,
            message_sender=message_sender,
        )

    def _has_low_score_continuation_failure_evidence(self, result: dict[str, Any] | None) -> bool:
        if not isinstance(result, dict):
            return False

        def walk(value: Any) -> bool:
            if isinstance(value, dict):
                if (
                    (
                        value.get("reference_status") == "LOW_SCORE_SKIPPED_INCOMPLETE"
                        or value.get("low_score_skipped_incomplete") is True
                        or value.get("s14_low_score_skip_triggered") is True
                    )
                    and (
                        value.get("next_reference_index") is not None
                        or value.get("continue_next_reference") is True
                        or value.get("should_continue_reference_collection") is True
                    )
                ):
                    return True
                if value.get("continue_reason") == "EARLY_EXIT_CONTINUE_NEXT_REFERENCE":
                    return True
                if value.get("continuation_source") == "low_score_skip":
                    return True
                return any(walk(item) for item in value.values())
            if isinstance(value, list):
                return any(walk(item) for item in value)
            return False

        return walk(result)

    def _terminal_feedback_issue_candidates(
        self,
        *,
        canonical: str,
        primary_error_code: str,
        raw_summary: dict[str, Any],
        result: dict[str, Any] | None,
    ) -> list[str]:
        candidates: list[str] = []

        def add(value: Any) -> None:
            code = str(value or "").strip()
            if code and code not in candidates:
                candidates.append(code)

        for value in (
            primary_error_code,
            canonical,
            raw_summary.get("pricing_result_issue_code"),
            raw_summary.get("binding_stop_code"),
            raw_summary.get("original_error_code"),
            raw_summary.get("root_cause_code"),
        ):
            add(value)
        if isinstance(result, dict):
            for key in (
                "issue_code",
                "stop_code",
                "status",
                "final_status",
                "current_state",
                "canonical_error_code",
                "original_error_code",
                "user_facing_error_code",
            ):
                add(result.get(key))
            issue_context = result.get("issue_context") if isinstance(result.get("issue_context"), dict) else {}
            for key in ("issue_code", "stop_code", "stage"):
                add(issue_context.get(key))
            binding_result = issue_context.get("binding_result") if isinstance(issue_context.get("binding_result"), dict) else {}
            add(binding_result.get("stop_code"))
        return candidates

    def _low_score_feedback_allowed_for_terminal_issue(self, terminal_issue_candidates: list[str]) -> bool:
        if set(terminal_issue_candidates) & LOW_SCORE_CONTINUATION_FEEDBACK_BLOCKED_TERMINAL_CODES:
            return False
        return bool(set(terminal_issue_candidates) & LOW_SCORE_CONTINUATION_FEEDBACK_TERMINAL_CODES)

    def _latest_reference_index_for_feedback(self, result: dict[str, Any] | None) -> Any:
        if not isinstance(result, dict):
            return None
        for key in ("current_reference_index", "latest_reference_index", "selected_reference_index", "reference_index"):
            value = result.get(key)
            if value not in (None, "", [], {}):
                return value
        current_reference = result.get("current_reference") if isinstance(result.get("current_reference"), dict) else {}
        for key in ("reference_index", "selected_reference_index", "latest_reference_index"):
            value = current_reference.get(key)
            if value not in (None, "", [], {}):
                return value
        return None

    def _has_loop_limit_source_missing_evidence(self, result: dict[str, Any] | None) -> bool:
        if not isinstance(result, dict):
            return False
        state = result.get("dispatcher_reference_loop_state")
        if not isinstance(state, dict):
            states = result.get("dispatcher_reference_loop_states")
            if isinstance(states, list) and states and isinstance(states[-1], dict):
                state = states[-1]
        if not isinstance(state, dict):
            return False
        return bool(
            state.get("dispatcher_stop_reason") == "SECOND_STAGE_CONTINUATION_STATE_MISSING"
            and state.get("fallback_default_4_used")
            and (
                state.get("next_reference_index") is not None
                or state.get("remaining_reference_count") not in (None, 0)
            )
        )

    def _s12_claim_field_failure_summary(self, result: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        summary: dict[str, Any] = {}

        def remember(key: str, value: Any) -> None:
            if value not in (None, "", [], {}) and key not in summary:
                summary[key] = value

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                code = str(value.get("issue_code") or value.get("stop_code") or value.get("status") or "")
                if code in S12_CLAIM_FIELD_FAILURE_CODES:
                    remember("s12_issue_code", code)
                if value.get("s12_claim_field_recovery_attempted") is not None:
                    remember("s12_claim_field_recovery_attempted", value.get("s12_claim_field_recovery_attempted"))
                if value.get("s12_claim_field_recovery_attempts") is not None:
                    remember("s12_claim_field_recovery_attempts", value.get("s12_claim_field_recovery_attempts"))
                if value.get("s12_missing_fields") is not None:
                    remember("s12_missing_fields", value.get("s12_missing_fields"))
                if value.get("s12_claim_field_decision") is not None:
                    remember("s12_claim_field_decision", value.get("s12_claim_field_decision"))
                for key in (
                    "s12_claim_recovery_candidate_count",
                    "s12_claim_recovery_valid_candidate_count",
                    "s12_claim_recovery_malformed_candidate_count",
                    "s12_claim_recovery_skipped_malformed_extents",
                    "s12_claim_recovery_selected_candidate_extent",
                    "s12_claim_recovery_bounds_valid",
                    "s12_claim_recovery_failure_reason",
                    "s12_claim_recovery_stop_code",
                    "root_exception_function",
                    "root_exception_type",
                    "root_exception_message",
                ):
                    remember(key, value.get(key))
                for key in ("current_reference_index", "reference_index", "next_reference_index", "source_id", "card_id", "screenshot_path", "xml_path"):
                    remember(key, value.get(key))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(result)
        if isinstance(result.get("current_reference"), dict):
            current = result["current_reference"]
            remember("current_reference_index", current.get("reference_index"))
            remember("source_id", current.get("source_id") or current.get("listing_id") or current.get("vehicle_id"))
            remember("card_id", current.get("card_id") or current.get("selected_card_id"))
        if result.get("business_status") == "NEEDS_REVIEW" or result.get("manual_review_required") is True:
            summary["s12_needs_review"] = True
        return summary

    def _s10_next_reference_binding_failure_summary(self, result: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        summary: dict[str, Any] = {}

        def remember(key: str, value: Any) -> None:
            if value not in (None, "", [], {}) and key not in summary:
                summary[key] = value

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                code = str(value.get("issue_code") or value.get("stop_code") or value.get("status") or "")
                if code in S10_NEXT_REFERENCE_BINDING_FAILURE_CODES:
                    remember("s10_binding_stop_code", code)
                binding = value.get("binding_result") if isinstance(value.get("binding_result"), dict) else {}
                if binding:
                    binding_code = str(binding.get("stop_code") or "")
                    if binding_code in S10_NEXT_REFERENCE_BINDING_FAILURE_CODES:
                        remember("s10_binding_stop_code", binding_code)
                    for key in (
                        "target_reference_index",
                        "target_canonical_reference_index",
                        "visible_reference_indices",
                        "visible_live_display_orders",
                        "expected_identity",
                        "partial_card_candidates",
                        "s10_absolute_identity_scan_attempts",
                        "s10_partial_card_completion_attempts",
                        "screenshot_path",
                        "xml_path",
                    ):
                        remember(key, binding.get(key))
                for key in (
                    "target_reference_index",
                    "target_canonical_reference_index",
                    "visible_reference_indices",
                    "visible_live_display_orders",
                    "expected_identity",
                    "partial_card_candidates",
                    "s10_absolute_identity_scan_attempts",
                    "s10_partial_card_completion_attempts",
                    "s10_viewport_renumbering_detected",
                    "s10_absolute_identity_scan_stop_reason",
                    "screenshot_path",
                    "xml_path",
                ):
                    remember(key, value.get(key))
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(result)
        return summary

    def concrete_failure_details(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        errors: list[str] | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = status or self.load_status(task_id) or {}
        raw_codes = self._collect_concrete_failure_codes(task_id, status=status, errors=errors, result=result)
        raw_codes = self._prioritize_feedback_error_codes(task_id, status=status, result=result, codes=raw_codes)
        canonical = self._primary_concrete_failure_code(raw_codes)
        raw_summary = self._raw_error_summary(task_id, status=status, errors=errors, result=result, codes=raw_codes)
        reached_s10 = self._has_s10_or_second_stage_evidence(task_id, status=status, result=result)
        post_start_context = self._post_start_failure_context(
            task_id,
            status=status,
            result=result,
            codes=raw_codes,
            reached_s10=reached_s10,
            raw_summary=raw_summary,
        )
        reached_s10 = bool(post_start_context.get("reached_s10_before_failure"))
        entered_s11_before_failure = bool(post_start_context.get("entered_s11_before_failure"))
        second_stage_entered = bool(post_start_context.get("second_stage_entered"))
        post_start_failure = bool(post_start_context.get("post_start_failure"))
        primary_error_code = str(
            raw_summary.get("pricing_result_issue_code")
            or raw_summary.get("binding_stop_code")
            or (
                canonical
                if raw_summary.get("wrapper_error_code")
                and canonical
                and canonical != raw_summary.get("wrapper_error_code")
                else ""
            )
            or raw_summary.get("original_error_code")
            or raw_summary.get("root_cause_code")
            or canonical
            or "UNKNOWN_PRECHECK_FAILED"
        )
        app_foreground_confirmed = self._has_guazi_foreground_evidence(task_id, status=status, result=result)
        highest_stage = "S07" if canonical in S07_AGE_FAILURE_ERROR_CODES or self._has_s07_age_evidence(task_id, status=status, result=result) else None
        low_score_continuation_failed = self._has_low_score_continuation_failure_evidence(result)
        loop_limit_source_missing = self._has_loop_limit_source_missing_evidence(result)
        s12_claim_field_summary = self._s12_claim_field_failure_summary(result)
        s10_binding_failure_summary = self._s10_next_reference_binding_failure_summary(result)
        terminal_issue_candidates = self._terminal_feedback_issue_candidates(
            canonical=canonical,
            primary_error_code=primary_error_code,
            raw_summary=raw_summary,
            result=result,
        )
        low_score_feedback_allowed = self._low_score_feedback_allowed_for_terminal_issue(terminal_issue_candidates)
        low_score_evidence_ignored_due_to_terminal_issue = bool(
            low_score_continuation_failed and not low_score_feedback_allowed
        )
        feedback_terminal_issue_code = terminal_issue_candidates[0] if terminal_issue_candidates else canonical
        feedback_latest_failed_stage = str(
            raw_summary.get("last_state")
            or raw_summary.get("original_stage")
            or (result.get("failed_state") if isinstance(result, dict) else "")
            or (result.get("current_state") if isinstance(result, dict) else "")
            or post_start_context.get("post_start_failure_stage")
            or ""
        )
        feedback_latest_reference_index = self._latest_reference_index_for_feedback(result)
        post_start_failure_business_template = (
            POST_START_FAILURE_GENERIC_TEMPLATE if post_start_failure else ""
        )
        if canonical == "ACTIVE_RUN_LOCK":
            return {
                "canonical_error_code": canonical,
                "canonical_error_codes": raw_codes,
                **raw_summary,
                "human_reason": "当前已有任务正在执行",
                "retry_instruction": "本次任务将等待系统调度，请勿重复发送。",
                "business_reply_text": "当前已有任务正在执行，本次任务将等待系统调度，请勿重复发送。",
                "cancelled": False,
            }
        message = CONCRETE_FAILURE_MESSAGES.get(canonical, CONCRETE_FAILURE_MESSAGES["UNKNOWN_PRECHECK_FAILED"])
        human_reason = message["human_reason"]
        retry_instruction = message["retry_instruction"]
        target_brand = self._target_brand_for_feedback(task_id, status=status, result=result)
        if target_brand and canonical == "S03_TARGET_BRAND_NOT_FOUND":
            human_reason = f"瓜子品牌选择页已打开，但未能定位目标品牌【{target_brand}】"
        elif target_brand and canonical == "S03_TARGET_INITIAL_LETTER_NOT_DERIVABLE":
            human_reason = f"系统暂未能定位目标品牌【{target_brand}】的品牌分组"
        elif target_brand and canonical == "S03_TARGET_INITIAL_LETTER_NOT_FOUND":
            human_reason = f"瓜子品牌选择页已打开，但未找到目标品牌【{target_brand}】所在的字母分组"
        target_config = self._target_config_for_feedback(task_id, status=status, result=result)
        if target_config and canonical == "S05_TARGET_CONFIG_NOT_FOUND":
            human_reason = f"已进入车款配置页，但未能确认目标配置【{target_config}】"
        elif target_config and canonical == "S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED":
            human_reason = f"车款配置【{target_config}】已点击，但未能确认已选中"
        frontend_retry_codes = {
            "GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES",
            "GUAZI_SPLASH_PAGE_STUCK_AFTER_3_RETRIES",
            "RUNTIME_FRESH_EVIDENCE_MISSING",
            "XML_DUMP_FAILED",
        }
        if canonical in S07_AGE_FAILURE_ERROR_CODES:
            business_reply = self._s07_age_failure_business_reply(
                task_id,
                canonical=canonical,
                status=status,
                result=result,
            ) or "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    "系统已开始执行，并已进入车龄筛选步骤。",
                    "本次在瓜子车龄滑块筛选时安全停止，已通知管理员处理。",
                    "请勿重复确认，等待管理员处理后再重新发起任务。",
                ]
            )
        elif canonical in frontend_retry_codes:
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    f"原因：{human_reason}。",
                    "",
                    "系统已安全停止并释放队列。",
                    retry_instruction,
                ]
            )
        elif canonical == "APP_NOT_FOREGROUND_AFTER_3_RETRIES" and not post_start_failure:
            business_reply = "\n".join(
                [
                    f"【本次定价未开始】{task_id}",
                    "",
                    f"原因：{human_reason}。",
                    "",
                    "任务已自动取消，不会占用队列。",
                    retry_instruction,
                ]
            )
        elif reached_s10 and (
            canonical == V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW
            or primary_error_code == V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW
        ):
            post_start_failure_business_template = POST_START_FAILURE_V33_RECOLLECT_NEEDS_REVIEW_TEMPLATE
            business_reply = "\n".join(
                [
                    f"【需要人工复核定价】{task_id}",
                    "",
                    "系统已完成三同车源边界判断，但边界前参考车回采后仍不完整，暂不能自动给出收车价，已提交管理员人工复核。",
                    "",
                    "请等待管理员确认价格后再收车。",
                ]
            )
            human_reason = "边界前参考车回采后仍不完整"
            retry_instruction = "请管理员核查边界前参考车回采证据并手工复核价格。"
            highest_stage = str(
                post_start_context.get("post_start_failure_stage")
                or raw_summary.get("last_state")
                or raw_summary.get("original_stage")
                or "S15"
            )
        elif post_start_failure and canonical == "DUPLICATE_REFERENCE_CLICK_BLOCKED":
            post_start_failure_business_template = POST_START_FAILURE_DUPLICATE_TEMPLATE
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    POST_START_DUPLICATE_REFERENCE_BUSINESS_MESSAGE,
                    "",
                    "请等待管理员处理后再重新发起任务。",
                ]
            )
            human_reason = "参考车回采阶段未能继续执行"
            retry_instruction = "请等待管理员检查参考车回采和续采状态后再重新发起任务。"
            highest_stage = str(
                post_start_context.get("post_start_failure_stage")
                or raw_summary.get("last_state")
                or raw_summary.get("original_stage")
                or "S10"
            )
        elif (reached_s10 or second_stage_entered or entered_s11_before_failure or post_start_failure) and (
            canonical in S12_TO_S13_REGION_PROOF_FAILURE_CODES
            or primary_error_code in S12_TO_S13_REGION_PROOF_FAILURE_CODES
            or raw_summary.get("pricing_result_issue_code") in S12_TO_S13_REGION_PROOF_FAILURE_CODES
            or raw_summary.get("original_error_code") in S12_TO_S13_REGION_PROOF_FAILURE_CODES
        ):
            post_start_failure_business_template = "POST_START_S12_TO_S13_REGION_PROOF_NOT_CONFIRMED"
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    "系统已开始自动定价，但在参考车官方检测报告页从“车身外观”区域进入四区域/历史修复采集阶段时未能形成可靠结果，已安全停止，已通知管理员处理。",
                    "",
                    "请等待管理员处理后再重新发起任务。",
                ]
            )
            human_reason = "参考车官方检测报告页从车身外观进入四区域/历史修复采集阶段时未能形成可靠结果"
            retry_instruction = "请管理员核查 S12 到 S13 的四区域页签、历史修复表、截图/XML fresh 证据和区域修复次数绑定。"
            highest_stage = "S13"
        elif (reached_s10 or second_stage_entered or entered_s11_before_failure or post_start_failure) and (
            canonical == "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED"
            or primary_error_code == "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED"
            or raw_summary.get("pricing_result_issue_code") == "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED"
            or raw_summary.get("original_error_code") == "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED"
        ):
            post_start_failure_business_template = "POST_START_S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED"
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    "系统已开始自动定价，但在参考车历史修复记录采集阶段未能可靠确认修复次数，已安全停止，已通知管理员处理。",
                    "",
                    "请等待管理员处理后再重新发起任务。",
                ]
            )
            human_reason = "参考车历史修复记录采集阶段未能可靠确认修复次数"
            retry_instruction = "请管理员核查 S13 历史修复记录截图、XML、修复次数候选和页面识别状态。"
            highest_stage = "S13"
        elif (reached_s10 or second_stage_entered or entered_s11_before_failure or post_start_failure) and (
            canonical in S12_CLAIM_FIELD_FAILURE_CODES
            or primary_error_code in S12_CLAIM_FIELD_FAILURE_CODES
            or raw_summary.get("pricing_result_issue_code") in S12_CLAIM_FIELD_FAILURE_CODES
            or (
                canonical == "RESULT_MISSING_REQUIRED_PRICING_FIELDS"
                and str(raw_summary.get("last_state") or raw_summary.get("original_stage") or "").upper() == "S12"
            )
        ):
            s12_needs_review = (
                primary_error_code
                in {
                    S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW,
                    S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW,
                }
                or canonical
                in {
                    S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW,
                    S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW,
                }
                or (isinstance(result, dict) and (result.get("manual_review_required") is True or result.get("business_status") == "NEEDS_REVIEW"))
            )
            if s12_needs_review:
                business_reply = "\n".join(
                    [
                        f"【需要人工复核定价】{task_id}",
                        "",
                        "系统已采集多辆三同参考车，但某辆参考车报告页的理赔次数/最大金额未能可靠读取，暂不能自动给出收车价，已提交管理员人工复核。",
                        "",
                        "请等待管理员确认价格后再收车。",
                    ]
                )
                human_reason = "参考车报告页理赔次数/最大金额未能可靠读取，需人工复核"
                retry_instruction = "请管理员核查该参考车 S12 报告页截图/XML 后人工复核价格。"
                post_start_failure_business_template = "POST_START_S12_CLAIM_FIELDS_NEEDS_REVIEW"
            else:
                business_reply = "\n".join(
                    [
                        f"【本次定价未完成】{task_id}",
                        "",
                        "系统已开始自动定价，但在参考车报告页读取理赔次数/最大金额时未能形成可靠结果，已安全停止，已通知管理员处理。",
                        "",
                        "请等待管理员处理后再重新发起任务。",
                    ]
                )
                human_reason = "参考车报告页理赔次数/最大金额未能可靠读取"
                retry_instruction = "请管理员核查该参考车 S12 报告页截图/XML 和字段补采 trace。"
                post_start_failure_business_template = "POST_START_S12_CLAIM_FIELDS_UNREADABLE"
            highest_stage = "S12"
        elif reached_s10 and (
            canonical in S10_NEXT_REFERENCE_BINDING_FAILURE_CODES
            or primary_error_code in S10_NEXT_REFERENCE_BINDING_FAILURE_CODES
            or raw_summary.get("pricing_result_issue_code") in S10_NEXT_REFERENCE_BINDING_FAILURE_CODES
            or raw_summary.get("binding_stop_code") in S10_NEXT_REFERENCE_BINDING_FAILURE_CODES
            or s10_binding_failure_summary.get("s10_binding_stop_code") in S10_NEXT_REFERENCE_BINDING_FAILURE_CODES
        ):
            post_start_failure_business_template = "POST_START_S10_NEXT_REFERENCE_BINDING_FAILED"
            business_reply = "\n".join(
                [
                    f"\u3010\u672c\u6b21\u5b9a\u4ef7\u672a\u5b8c\u6210\u3011{task_id}",
                    "",
                    "\u7cfb\u7edf\u5df2\u5f00\u59cb\u81ea\u52a8\u5b9a\u4ef7\uff0c\u4f46\u5728\u8fd4\u56de\u4e09\u540c\u8f66\u6e90\u5217\u8868\u5b9a\u4f4d\u4e0b\u4e00\u8f86\u53c2\u8003\u8f66\u65f6\u672a\u80fd\u552f\u4e00\u7ed1\u5b9a\u76ee\u6807\u5361\u7247\uff0c\u5df2\u5b89\u5168\u505c\u6b62\uff0c\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\u3002",
                    "",
                    "\u8bf7\u7b49\u5f85\u7ba1\u7406\u5458\u5904\u7406\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002",
                ]
            )
            human_reason = "\u8fd4\u56de\u4e09\u540c\u8f66\u6e90\u5217\u8868\u540e\u672a\u80fd\u552f\u4e00\u7ed1\u5b9a\u4e0b\u4e00\u8f86\u53c2\u8003\u8f66\u5361\u7247"
            retry_instruction = "\u8bf7\u7ba1\u7406\u5458\u68c0\u67e5 S10 \u4e09\u540c\u5217\u8868\u7684\u4e0b\u4e00\u8f86\u53c2\u8003\u8f66\u5361\u7247\u7ed1\u5b9a\u3001\u7edd\u5bf9\u5e8f\u53f7\u548c\u5c40\u90e8\u89c6\u53e3\u7f16\u53f7 trace\u3002"
            highest_stage = "S10"
        elif (reached_s10 or second_stage_entered or entered_s11_before_failure or post_start_failure) and (
            canonical == "SECOND_STAGE_RUNTIME_EXCEPTION"
            or raw_summary.get("root_exception_type")
            or raw_summary.get("root_cause_code") == "SECOND_STAGE_RUNTIME_EXCEPTION"
        ) and (
            str(raw_summary.get("root_exception_function") or "") == "_recover_s12_claim_fields"
            or str(raw_summary.get("last_state") or raw_summary.get("original_stage") or "").upper() == "S12"
        ):
            post_start_failure_business_template = "POST_START_S12_RUNTIME_EXCEPTION"
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    "系统已开始自动定价，但在参考车报告页理赔字段采集阶段出现系统异常，已安全停止，已通知管理员处理。",
                    "",
                    "请等待管理员处理后再重新发起任务。",
                ]
            )
            human_reason = "参考车报告页理赔字段采集阶段出现系统异常"
            retry_instruction = "请管理员核查 S12 理赔字段恢复 trace、截图、XML 和异常堆栈。"
            highest_stage = "S12"
        elif reached_s10 and low_score_continuation_failed and low_score_feedback_allowed:
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    "原因：参考车低分跳过后，系统未能继续采集下一辆参考车。",
                    "",
                    "需要处理：已通知管理员检查续采状态，暂不能自动给出收车价。",
                ]
            )
            human_reason = "参考车低分跳过后未能继续采集下一辆参考车"
            retry_instruction = "请管理员检查参考车续采状态和下一辆参考车索引。"
        elif reached_s10 and loop_limit_source_missing:
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    "原因：参考车仍需继续采集，但系统未正确读取三同车源数量。",
                    "",
                    "需要处理：已通知管理员检查续采循环，暂不能自动给出收车价。",
                ]
            )
            human_reason = "参考车仍需继续采集，但三同车源数量读取异常"
            retry_instruction = "请管理员检查 first_stage_result 和续采循环状态。"
        elif reached_s10 and (
            canonical == "SECOND_STAGE_RUNTIME_EXCEPTION"
            or raw_summary.get("root_exception_type")
            or raw_summary.get("root_cause_code") == "V149_REFERENCE_IDENTITY_SUMMARY_TYPE_ERROR"
        ):
            business_reply = "\n".join(
                [
                    f"\u3010\u672c\u6b21\u5b9a\u4ef7\u672a\u5b8c\u6210\u3011{task_id}",
                    "",
                    "\u7cfb\u7edf\u5df2\u5f00\u59cb\u81ea\u52a8\u5b9a\u4ef7\uff0c\u4f46\u5728\u53c2\u8003\u8f66\u8be6\u60c5\u91c7\u96c6\u9636\u6bb5\u51fa\u73b0\u7cfb\u7edf\u5f02\u5e38\uff0c\u672c\u6b21\u5df2\u5b89\u5168\u505c\u6b62\uff0c\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\u3002",
                    "",
                    "\u8bf7\u6682\u7b49\u7ba1\u7406\u5458\u5904\u7406\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002",
                ]
            )
            human_reason = "\u53c2\u8003\u8f66\u8be6\u60c5\u91c7\u96c6\u9636\u6bb5\u51fa\u73b0\u7cfb\u7edf\u5f02\u5e38"
            retry_instruction = "\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\uff0c\u8bf7\u6682\u7b49\u5904\u7406\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002"
            highest_stage = str(raw_summary.get("last_state") or raw_summary.get("original_stage") or "second_stage")
        elif reached_s10 and canonical == "RESULT_MISSING_REQUIRED_PRICING_FIELDS":
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    "系统已完成参考车采集并形成价格测算，但结果字段校验异常，暂未自动发送最终收车价。",
                    "",
                    "任务已安全停止并释放队列。",
                    "已通知管理员处理。",
                ]
            )
        elif post_start_failure and canonical == "DUPLICATE_REFERENCE_CLICK_BLOCKED":
            post_start_failure_business_template = POST_START_FAILURE_DUPLICATE_TEMPLATE
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    POST_START_DUPLICATE_REFERENCE_BUSINESS_MESSAGE,
                    "",
                    "请等待管理员处理后再重新发起任务。",
                ]
            )
            human_reason = "参考车回采阶段未能继续执行"
            retry_instruction = "请等待管理员检查参考车回采和续采状态后再重新发起任务。"
            highest_stage = str(
                post_start_context.get("post_start_failure_stage")
                or raw_summary.get("last_state")
                or raw_summary.get("original_stage")
                or "S10"
            )
        elif reached_s10 and (canonical in POST_START_COLLECTION_INCOMPLETE_ERROR_CODES):
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    "系统已开始采集参考车，但参考车车况证据未能完整采集，无法形成可信自动定价结果。",
                    "",
                    "任务已安全停止并释放队列。",
                    retry_instruction,
                ]
            )
        elif canonical.startswith("S13_REPAIR_ITEM_CLICK_"):
            business_reply = "\n".join(
                [
                    f"【本次定价已停止】{task_id}",
                    f"本次定价已停止，原因：{human_reason}。",
                    "任务已自动取消，不会占用队列。",
                    retry_instruction,
                ]
            )
        elif canonical == "REFERENCE_CARD_BINDING_NOT_UNIQUE":
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    f"原因：{human_reason}。",
                    "",
                    "任务已自动取消并释放队列。",
                    retry_instruction,
                ]
            )
        elif post_start_failure:
            business_reply = "\n".join(
                [
                    f"【本次定价未完成】{task_id}",
                    "",
                    POST_START_GENERIC_BUSINESS_MESSAGE,
                    "",
                    "请等待管理员处理后再重新发起任务。",
                ]
            )
            human_reason = "参考车采集阶段未能形成完整结果"
            retry_instruction = "请等待管理员处理后再重新发起任务。"
            highest_stage = str(
                post_start_context.get("post_start_failure_stage")
                or raw_summary.get("last_state")
                or highest_stage
                or "post_start"
            )
        else:
            business_reply = "\n".join(
                [
                    f"【本次定价未开始】{task_id}",
                    f"本次定价没有开始执行，原因：{human_reason}。",
                    "任务已自动取消，不会占用队列。",
                    retry_instruction,
                ]
            )
        return {
            "canonical_error_code": canonical,
            "canonical_error_codes": raw_codes,
            **raw_summary,
            "human_reason": human_reason,
            "retry_instruction": retry_instruction,
            "business_reply_text": business_reply,
            "primary_error_code": primary_error_code,
            "wrapper_error_code": raw_summary.get("wrapper_error_code"),
            "pricing_result_issue_code": raw_summary.get("pricing_result_issue_code"),
            "binding_stop_code": raw_summary.get("binding_stop_code"),
            "post_start_failure": post_start_failure,
            "post_start_failure_stage": post_start_context.get("post_start_failure_stage"),
            "second_stage_entered": second_stage_entered,
            "post_start_not_started_template_blocked": bool(post_start_context.get("post_start_not_started_template_blocked")),
            "post_start_failure_business_template": post_start_failure_business_template,
            "post_start_failure_classification_reason": post_start_context.get("post_start_failure_classification_reason"),
            "feedback_primary_issue_source": "current_terminal",
            "feedback_low_score_evidence_present": bool(low_score_continuation_failed),
            "feedback_low_score_evidence_ignored_due_to_terminal_issue": low_score_evidence_ignored_due_to_terminal_issue,
            "feedback_selected_template": post_start_failure_business_template or canonical,
            "feedback_selected_reason_code": primary_error_code or canonical,
            "feedback_terminal_issue_code": feedback_terminal_issue_code,
            "feedback_latest_reference_index": feedback_latest_reference_index,
            "feedback_latest_failed_stage": highest_stage or feedback_latest_failed_stage,
            "feedback_terminal_issue_candidates": terminal_issue_candidates,
            "user_facing_error_code": canonical,
            "user_facing_reason": human_reason,
            "reached_s10_before_failure": reached_s10,
            "app_foreground_confirmed_before_failure": app_foreground_confirmed,
            "entered_s11_before_failure": entered_s11_before_failure,
            "last_state": raw_summary.get("last_state"),
            "root_exception_type": raw_summary.get("root_exception_type") or s12_claim_field_summary.get("root_exception_type"),
            "root_exception_message": raw_summary.get("root_exception_message") or s12_claim_field_summary.get("root_exception_message"),
            "root_exception_function": raw_summary.get("root_exception_function") or s12_claim_field_summary.get("root_exception_function"),
            "root_exception_file": raw_summary.get("root_exception_file"),
            "root_cause_code": raw_summary.get("root_cause_code"),
            "highest_stage": highest_stage,
            "original_stage": highest_stage,
            "original_stop_code": canonical if canonical in S07_AGE_FAILURE_ERROR_CODES else raw_summary.get("original_error_code"),
            "s12_claim_field_failure_summary": s12_claim_field_summary,
            "s10_next_reference_binding_failure_summary": s10_binding_failure_summary,
            "cancelled": True,
        }

    def _prioritize_feedback_error_codes(
        self,
        task_id: str,
        *,
        status: dict[str, Any],
        result: dict[str, Any] | None = None,
        codes: list[str],
    ) -> list[str]:
        codes = self._filter_blocking_error_codes(codes)
        if not codes:
            return []
        if "DUPLICATE_REFERENCE_CLICK_BLOCKED" in codes:
            duplicate_codes = [code for code in codes if code == "DUPLICATE_REFERENCE_CLICK_BLOCKED"]
            remainder = [
                code
                for code in codes
                if code != "DUPLICATE_REFERENCE_CLICK_BLOCKED"
                and code != "APP_NOT_FOREGROUND"
                and code not in GENERIC_QUEUE_RELEASE_ERROR_CODES
            ]
            tail = [code for code in codes if code not in duplicate_codes and code not in remainder]
            return self._filter_blocking_error_codes(duplicate_codes + remainder + tail)
        s12_codes = [code for code in codes if code in S12_CLAIM_FIELD_FAILURE_CODES]
        if s12_codes:
            remainder = [
                code
                for code in codes
                if code not in S12_CLAIM_FIELD_FAILURE_CODES
                and code != "RESULT_MISSING_REQUIRED_PRICING_FIELDS"
                and not str(code).startswith("MISSING_REQUIRED_FIELD:")
                and code not in GENERIC_QUEUE_RELEASE_ERROR_CODES
            ]
            tail = [code for code in codes if code not in s12_codes and code not in remainder]
            return self._filter_blocking_error_codes(s12_codes + remainder + tail)
        reached_s10 = self._has_s10_or_second_stage_evidence(task_id, status=status, result=result)
        guazi_foreground = self._has_guazi_foreground_evidence(task_id, status=status, result=result)
        if not (reached_s10 or guazi_foreground):
            return codes

        runtime_exception_codes = [
            code
            for code in codes
            if code
            in {
                "SECOND_STAGE_RUNTIME_EXCEPTION",
                "V149_REFERENCE_IDENTITY_SUMMARY_TYPE_ERROR",
                "RESULT_SCHEMA_INVALID_FOR_PRICING_WITH_RUNTIME_EXCEPTION",
            }
        ]
        if reached_s10 and runtime_exception_codes:
            remainder = [
                code
                for code in codes
                if code not in runtime_exception_codes
                and code != "APP_NOT_FOREGROUND"
                and code not in GENERIC_QUEUE_RELEASE_ERROR_CODES
            ]
            tail = [code for code in codes if code not in runtime_exception_codes and code not in remainder]
            return self._filter_blocking_error_codes(runtime_exception_codes + remainder + tail)

        preferred_s07_age = [code for code in codes if code in S07_AGE_FAILURE_ERROR_CODES]
        if preferred_s07_age:
            remainder = [
                code
                for code in codes
                if code not in preferred_s07_age
                and code != "APP_NOT_FOREGROUND"
                and code not in GENERIC_QUEUE_RELEASE_ERROR_CODES
            ]
            tail = [code for code in codes if code not in preferred_s07_age and code not in remainder]
            return self._filter_blocking_error_codes(preferred_s07_age + remainder + tail)

        preferred = [code for code in POST_S10_FEEDBACK_SPECIFIC_ERROR_CODES if code in codes]
        if preferred:
            remainder = [
                code
                for code in codes
                if code not in preferred
                and code != "APP_NOT_FOREGROUND"
                and code not in GENERIC_QUEUE_RELEASE_ERROR_CODES
            ]
            tail = [code for code in codes if code not in preferred and code not in remainder]
            return self._filter_blocking_error_codes(preferred + remainder + tail)

        non_app_codes = [
            code
            for code in codes
            if code != "APP_NOT_FOREGROUND" and code not in GENERIC_QUEUE_RELEASE_ERROR_CODES
        ]
        if "APP_NOT_FOREGROUND" in codes and non_app_codes:
            return self._filter_blocking_error_codes(non_app_codes + ["APP_NOT_FOREGROUND"])
        return codes

    def _feedback_context_payloads(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        if isinstance(result, dict):
            payloads.append(result)
            step_result = result.get("step_result")
            if isinstance(step_result, dict):
                payloads.append(step_result)
        if isinstance(status, dict):
            payloads.append(status)
        for filename in (
            "first_stage_result.json",
            "runner_result.json",
            "runner_error.json",
            "pricing_result.json",
            "second_stage_result.json",
            "second_stage_run_meta.json",
            "run_meta.json",
            "dispatcher_result.json",
            "admin_intervention_delivery.json",
        ):
            payload = self._read_json(self.task_dir(task_id) / filename, default={})
            if isinstance(payload, dict) and payload:
                payloads.append(payload)
        return payloads

    def _has_s10_or_second_stage_evidence(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> bool:
        return any(
            self._payload_has_s10_or_second_stage_evidence(payload)
            for payload in self._feedback_context_payloads(task_id, status=status, result=result)
        )

    def _payload_has_s10_or_second_stage_evidence(self, payload: Any) -> bool:
        if isinstance(payload, dict):
            states = {
                str(payload.get(key) or "")
                for key in ("status", "final_status", "current_state", "business_status", "technical_status")
            }
            if "S10_READY" in states:
                return True
            flow_state = payload.get("flow_state")
            if isinstance(flow_state, dict) and flow_state.get("S10_READY") is True:
                return True
            try:
                if int(payload.get("trisame_cards_count") or 0) > 0:
                    return True
            except (TypeError, ValueError):
                pass
            if str(payload.get("stage") or "").lower() == "second_stage":
                return True
            for key in ("run_id", "generation_id", "latest_run_id"):
                if "second_stage" in str(payload.get(key) or "").lower():
                    return True
            if payload.get("s15_entry_allowed") is True:
                return True
            if payload.get("s14_collect_done") is True or payload.get("s14_last_page_reached") is True:
                return True
            if str(payload.get("target_score_source") or "").startswith("score_target_runtime_s15"):
                return True
            second_stage_states = {
                "S14_FULL_IMAGE_SEQUENCE_COLLECTED",
                "FULL_CHAIN_MANUAL_REVIEW_DONE",
                "CONTINUE_NEXT_REFERENCE",
            }
            if states & second_stage_states:
                return True
            return any(
                self._payload_has_s10_or_second_stage_evidence(value)
                for value in payload.values()
                if isinstance(value, (dict, list))
            )
        if isinstance(payload, list):
            return any(self._payload_has_s10_or_second_stage_evidence(item) for item in payload)
        return False

    def _post_start_failure_context(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        codes: list[str] | None = None,
        reached_s10: bool | None = None,
        raw_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = status or {}
        raw_summary = raw_summary or {}
        payloads = self._feedback_context_payloads(task_id, status=status, result=result)
        reasons: list[str] = []
        stage_candidates: list[str] = []

        def add_reason(reason: str, stage: str | None = None) -> None:
            if reason not in reasons:
                reasons.append(reason)
            if stage and stage not in stage_candidates:
                stage_candidates.append(stage)

        def walk(value: Any):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        task_dir = self.task_dir(task_id)
        preflight_codes = set(codes or []) & NOT_STARTED_SYSTEM_PRECHECK_ERROR_CODES
        start_ack_counts = not preflight_codes
        if status.get("start_ack_sent") is True and start_ack_counts:
            add_reason("start_ack_sent", "post_start_ack")
        for key in ("started_at", "run_started_at", "first_stage_started_at", "second_stage_started_at"):
            if status.get(key) and start_ack_counts:
                add_reason(key, "post_start_ack")
        if start_ack_counts and (
            status.get("started") is True
            or status.get("runner_started") is True
            or status.get("phone_flow_started") is True
        ):
            add_reason("runner_or_phone_started", "post_start_ack")

        second_stage_entered = False
        entered_s11 = bool(raw_summary.get("entered_s11_before_failure"))
        attempted_reference_count = 0
        for payload in payloads:
            for item in walk(payload):
                if item.get("second_stage_entered") is True:
                    second_stage_entered = True
                    add_reason("second_stage_entered", "second_stage")
                if str(item.get("stage") or "").lower() == "second_stage":
                    second_stage_entered = True
                    add_reason("stage_second_stage", "second_stage")
                if any("second_stage" in str(item.get(key) or "").lower() for key in ("run_id", "generation_id", "latest_run_id")):
                    second_stage_entered = True
                    add_reason("second_stage_run_id", "second_stage")
                if item.get("entered_s11") is True or item.get("s11_page_recognized") is True:
                    entered_s11 = True
                    second_stage_entered = True
                    add_reason("entered_s11_before_failure", "S11")
                for key in ("status", "final_status", "current_state", "last_state", "failed_state", "recognized_page", "stage", "current_step", "last_step"):
                    value = str(item.get(key) or "")
                    match = re.search(r"\bS(1[0-6])\b", value)
                    if match:
                        stage = f"S{match.group(1)}"
                        second_stage_entered = second_stage_entered or stage != "S10"
                        add_reason(f"{key}:{stage}", stage)
                if item.get("first_stage_status") == "S10_READY":
                    add_reason("first_stage_status:S10_READY", "S10")
                flow_state = item.get("flow_state") if isinstance(item.get("flow_state"), dict) else {}
                if flow_state.get("S10_READY") is True:
                    add_reason("flow_state:S10_READY", "S10")
                history = item.get("reference_history")
                if isinstance(history, list) and history:
                    attempted_reference_count = max(attempted_reference_count, len(history))
                    second_stage_entered = True
                    add_reason("reference_history_non_empty", "S10")
                for key in ("attempted_reference_count", "collected_reference_count", "processed_reference_count"):
                    try:
                        count = int(item.get(key) or 0)
                    except (TypeError, ValueError):
                        count = 0
                    if count > 0:
                        attempted_reference_count = max(attempted_reference_count, count)
                        second_stage_entered = True
                        add_reason(key, "S10")
                processed = item.get("processed_reference_indices")
                if isinstance(processed, list) and processed:
                    attempted_reference_count = max(attempted_reference_count, len(processed))
                    second_stage_entered = True
                    add_reason("processed_reference_indices", "S10")

        if reached_s10 is None:
            reached_s10 = self._has_s10_or_second_stage_evidence(task_id, status=status, result=result)
        if reached_s10:
            add_reason("reached_s10_before_failure", "S10")
        if entered_s11:
            add_reason("entered_s11_before_failure", "S11")
        if second_stage_entered and "second_stage_entered" not in reasons:
            add_reason("second_stage_entered", "second_stage")

        stage = stage_candidates[0] if stage_candidates else ""
        if entered_s11:
            stage = "S11"
        elif second_stage_entered and stage in {"", "queued", "post_start_ack"}:
            stage = "second_stage"
        elif reached_s10 and stage in {"", "queued", "post_start_ack"}:
            stage = "S10"
        elif status.get("start_ack_sent") is True:
            stage = "post_start_ack"

        post_start_failure = bool(reasons)
        return {
            "post_start_failure": post_start_failure,
            "post_start_failure_stage": stage,
            "post_start_failure_classification_reason": ",".join(reasons),
            "post_start_not_started_template_blocked": post_start_failure,
            "second_stage_entered": bool(second_stage_entered),
            "reached_s10_before_failure": bool(reached_s10),
            "entered_s11_before_failure": bool(entered_s11),
            "attempted_reference_count": attempted_reference_count,
        }

    def _has_guazi_foreground_evidence(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> bool:
        return any(
            self._payload_has_guazi_foreground_evidence(payload)
            for payload in self._feedback_context_payloads(task_id, status=status, result=result)
        )

    def _payload_has_guazi_foreground_evidence(self, payload: Any) -> bool:
        if isinstance(payload, dict):
            for key in ("foreground_package", "focused_window", "resumed_activity", "raw_error_summary"):
                value = payload.get(key)
                if value and GUAZI_APP_PACKAGE in str(value):
                    return True
            context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            startup = payload.get("startup") if isinstance(payload.get("startup"), dict) else {}
            for nested in (context, startup):
                for key in (
                    "foreground_package",
                    "focused_window",
                    "foreground_package_after",
                    "foreground_package_before",
                    "focused_window_after",
                    "focused_window_before",
                ):
                    value = nested.get(key)
                    if value and GUAZI_APP_PACKAGE in str(value):
                        return True
            return any(
                self._payload_has_guazi_foreground_evidence(value)
                for value in payload.values()
                if isinstance(value, (dict, list))
            )
        if isinstance(payload, list):
            return any(self._payload_has_guazi_foreground_evidence(item) for item in payload)
        return False

    def _has_s07_age_evidence(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> bool:
        return any(
            self._payload_has_s07_age_evidence(payload)
            for payload in self._feedback_context_payloads(task_id, status=status, result=result)
        )

    def _payload_has_s07_age_evidence(self, payload: Any) -> bool:
        if isinstance(payload, dict):
            codes = set(self._codes_from_payload(payload))
            if codes & S07_AGE_FAILURE_ERROR_CODES:
                return True
            for key in ("status", "canonical_error_code", "error_code", "stop_code", "failure_reason"):
                value = str(payload.get(key) or "")
                if value.startswith("S07_AGE"):
                    return True
            flow_state = payload.get("flow_state") if isinstance(payload.get("flow_state"), dict) else {}
            if flow_state.get("COLOR_FILTER_DONE") is True and flow_state.get("AGE_FILTER_DONE") is not True:
                return True
            context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            if isinstance(context.get("age_action"), dict):
                return True
            return any(
                self._payload_has_s07_age_evidence(value)
                for value in payload.values()
                if isinstance(value, (dict, list))
            )
        if isinstance(payload, list):
            return any(self._payload_has_s07_age_evidence(item) for item in payload)
        return False

    def _s07_age_failure_feedback_context(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_dir = self.task_dir(task_id)
        payloads: list[Any] = []
        for payload in (status, result):
            if isinstance(payload, dict):
                payloads.append(payload)
        for filename in (
            "first_stage_result.json",
            "target_task_draft.json",
            "current_target_task.preview.json",
            "current_target_task.snapshot.json",
            "status.json",
        ):
            payload = self._read_json(task_dir / filename, default={})
            if isinstance(payload, dict):
                payloads.append(payload)

        def walk(value: Any):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        def first_text(keys: tuple[str, ...]) -> str | None:
            for payload in payloads:
                for item in walk(payload):
                    for key in keys:
                        text = str(item.get(key) or "").strip()
                        if text:
                            return text
            return None

        def first_int(keys: tuple[str, ...]) -> int | None:
            for payload in payloads:
                for item in walk(payload):
                    for key in keys:
                        value = item.get(key)
                        if value in (None, ""):
                            continue
                        try:
                            return int(float(value))
                        except (TypeError, ValueError):
                            continue
            return None

        return {
            "registration_date": first_text(
                (
                    "registration_date",
                    "register_date",
                    "target_registration_date",
                    "registration_date_raw",
                )
            ),
            "target_age": first_int(("target_age_years", "target_age")),
            "expected_age_filter": first_text(("expected_age_filter", "target_range")),
        }

    def _s07_age_failure_business_reply(
        self,
        task_id: str,
        *,
        canonical: str,
        status: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> str | None:
        context = self._s07_age_failure_feedback_context(task_id, status=status, result=result)
        target_age = context.get("target_age")
        if target_age != 1 and canonical not in {
            "S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE",
            "S07_AGE_ONE_HIDDEN_TICK_VERIFY_FAILED",
        }:
            return None
        registration_date = str(context.get("registration_date") or "2025.02")
        return "\n".join(
            [
                f"\u3010\u672c\u6b21\u5b9a\u4ef7\u672a\u5b8c\u6210\u3011{task_id}",
                "",
                f"\u7cfb\u7edf\u5df2\u8bc6\u522b\u4e0a\u724c\u65e5\u671f\u4e3a {registration_date}\uff0c\u6309\u5e74\u4efd\u5dee\u5e94\u7b5b\u9009 1 \u5e74\u8f66\u9f84\u3002",
                "\u672c\u6b21\u672a\u80fd\u7a33\u5b9a\u5b8c\u6210 1 \u5e74\u9690\u85cf\u523b\u5ea6\u9009\u62e9\u6216 1-1\u5e74\u7ed3\u679c\u9a8c\u8bc1\uff0c\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\u3002",
                "\u8bf7\u52ff\u91cd\u590d\u786e\u8ba4\uff0c\u7b49\u5f85\u7ba1\u7406\u5458\u5904\u7406\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002",
            ]
        )

    def _target_brand_for_feedback(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> str | None:
        payloads: list[dict[str, Any]] = []
        if isinstance(result, dict):
            payloads.append(result)
        if isinstance(status, dict):
            payloads.append(status)
        task_dir = self.task_dir(task_id)
        for filename in (
            "target_task_draft.json",
            "current_target_task.preview.json",
            "current_target_task.snapshot.json",
            "first_stage_result.json",
        ):
            payload = self._read_json(task_dir / filename, default={})
            if isinstance(payload, dict):
                payloads.append(payload)
        for payload in payloads:
            brand = payload.get("brand") or payload.get("canonical_brand")
            if not brand and isinstance(payload.get("target_task"), dict):
                brand = payload["target_task"].get("brand")
            text = str(brand or "").strip()
            if text:
                return text
        return None

    def _target_config_for_feedback(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> str | None:
        payloads: list[dict[str, Any]] = []
        if isinstance(result, dict):
            payloads.append(result)
        if isinstance(status, dict):
            payloads.append(status)
        task_dir = self.task_dir(task_id)
        for filename in (
            "target_task_draft.json",
            "current_target_task.preview.json",
            "current_target_task.snapshot.json",
            "first_stage_result.json",
        ):
            payload = self._read_json(task_dir / filename, default={})
            if isinstance(payload, dict):
                payloads.append(payload)
        for payload in payloads:
            candidates = [
                payload.get("target_trim"),
                payload.get("target_config_model"),
                payload.get("config_model"),
                payload.get("model_config"),
                payload.get("canonical_config_model"),
            ]
            if isinstance(payload.get("target_task"), dict):
                target_task = payload["target_task"]
                candidates.extend(
                    [
                        target_task.get("target_trim"),
                        target_task.get("target_config_model"),
                        target_task.get("config_model"),
                        target_task.get("model_config"),
                        target_task.get("canonical_config_model"),
                    ]
                )
            for candidate in candidates:
                text = str(candidate or "").strip()
                if text:
                    return text
        return None

    def confirm_failure_should_auto_cancel(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        errors: list[str] | None = None,
        result: dict[str, Any] | None = None,
    ) -> bool:
        status = status or self.load_status(task_id) or {}
        raw_codes = self._collect_concrete_failure_codes(task_id, status=status, errors=errors, result=result)
        normalized = self._normalize_concrete_failure_code(raw_codes)
        if normalized == "ACTIVE_RUN_LOCK":
            return False
        if self._strict_non_auto_failure_codes(raw_codes):
            return False
        if normalized != "UNKNOWN_PRECHECK_FAILED":
            return True
        non_auto_codes = set(raw_codes) & (set(TARGET_INFO_ERROR_CODES) | set(PAGE_OR_PROGRAM_ERROR_CODES) | {"RULE_SOURCE_CONFLICT"})
        return not non_auto_codes

    def _strict_non_auto_failure_codes(self, codes: list[str]) -> set[str]:
        wrapper_page_codes = {"FIRST_STAGE_NOT_S10_READY", "S10_NOT_READY", "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY"}
        brand_filter_codes = {
            "BRAND_FILTER_STEP_NOT_ENTERED",
            "BRAND_FILTER_NOT_FOUND",
            "BRAND_FILTER_CLICK_FAILED",
            "BRAND_FILTER_PANEL_NOT_OPENED",
        }
        if set(codes) & brand_filter_codes:
            wrapper_page_codes.add("PAGE_CONTRACT_MISMATCH")
        non_auto = set(codes) & (set(TARGET_INFO_ERROR_CODES) | set(PAGE_OR_PROGRAM_ERROR_CODES) | {"RULE_SOURCE_CONFLICT"})
        return non_auto - wrapper_page_codes

    def _collect_concrete_failure_codes(
        self,
        task_id: str,
        *,
        status: dict[str, Any],
        errors: list[str] | None = None,
        result: dict[str, Any] | None = None,
    ) -> list[str]:
        sources: list[list[str]] = []
        sources.append(self._filter_blocking_error_codes(collect_error_codes(errors=errors, result=result)))
        if isinstance(result, dict):
            sources.append(self._filter_blocking_error_codes(self._codes_from_payload(result)))
        if isinstance(result, dict) and self._payload_has_s02_brand_filter_entry_failure_evidence(result):
            sources.append(["BRAND_FILTER_STEP_NOT_ENTERED"])
        if self._payload_has_s02_brand_filter_entry_failure_evidence(status):
            sources.append(["BRAND_FILTER_STEP_NOT_ENTERED"])
        sources.append(self._filter_blocking_error_codes(self._values_from_keys(status, ("original_error_code", "original_error_codes"))))
        sources.append(self._admin_error_codes(status))
        for filename in (
            "dispatcher_result.json",
            "first_stage_result.json",
            "runner_result.json",
            "runner_error.json",
            "pricing_result.json",
            "second_stage_result.json",
            "second_stage_run_meta.json",
            "run_meta.json",
            "runner_validation.json",
            "admin_intervention_delivery.json",
        ):
            sources.append(self._filter_blocking_error_codes(self._codes_from_json_file(self.task_dir(task_id) / filename)))
        combined: list[str] = []
        for codes in sources:
            combined.extend(codes)
        return self._filter_blocking_error_codes(combined)

    def _raw_error_summary(
        self,
        task_id: str,
        *,
        status: dict[str, Any],
        errors: list[str] | None = None,
        result: dict[str, Any] | None = None,
        codes: list[str],
    ) -> dict[str, Any]:
        payloads: list[dict[str, Any]] = []
        if isinstance(result, dict):
            payloads.append(result)
            step_result = result.get("step_result")
            if isinstance(step_result, dict):
                payloads.append(step_result)
        payloads.append(status)
        for filename in (
            "first_stage_result.json",
            "runner_result.json",
            "dispatcher_result.json",
            "runner_error.json",
            "pricing_result.json",
            "second_stage_result.json",
            "second_stage_run_meta.json",
            "run_meta.json",
            "runner_validation.json",
            "admin_intervention_delivery.json",
        ):
            payload = self._read_json(self.task_dir(task_id) / filename, default={})
            if isinstance(payload, dict):
                payloads.append(payload)

        wrapper_codes = {
            "FAILED",
            "ADMIN_INTERVENTION_REQUIRED",
            "SYSTEM_BLOCKED",
            "CANCELLED",
            "FIRST_STAGE_NOT_S10_READY",
        }
        preferred_original_codes = (
            "TARGET_ADB_SERIAL_NOT_CONFIGURED",
            "TARGET_ADB_DEVICE_UNAUTHORIZED",
            "TARGET_ADB_DEVICE_OFFLINE",
            "TARGET_ADB_DEVICE_NOT_CONNECTED",
            "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT",
        )
        original_code = next((code for code in codes if code in S07_AGE_FAILURE_ERROR_CODES), None)
        if original_code is None:
            original_code = next((code for code in preferred_original_codes if code in codes), None)
        if original_code is None:
            original_code = next((code for code in codes if code not in wrapper_codes), codes[0] if codes else None)
        original_message = None
        focused_window = None
        foreground_package = None
        visible_text_digest = None
        root_exception_type = None
        root_exception_message = None
        root_exception_function = None
        root_exception_file = None
        traceback_tail = None
        last_state = None
        entered_s11 = False
        wrapper_error_code = None
        pricing_result_issue_code = None
        binding_stop_code = None
        if "RESULT_SCHEMA_INVALID_FOR_PRICING" in codes:
            wrapper_error_code = "RESULT_SCHEMA_INVALID_FOR_PRICING"

        def walk_dicts(value: Any):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from walk_dicts(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk_dicts(child)

        def extract_traceback_location(text: str) -> tuple[str | None, str | None]:
            file_match = None
            func_match = None
            for file_match in re.finditer(r'File "([^"]+)", line \d+, in ([A-Za-z_][A-Za-z0-9_]*)', text or ""):
                pass
            if file_match:
                return file_match.group(2), file_match.group(1)
            for func_match in re.finditer(r"\bin\s+([A-Za-z_][A-Za-z0-9_]*)", text or ""):
                pass
            return (func_match.group(1) if func_match else None, None)

        message_keys = (
            "error",
            "message",
            "stderr",
            "adb_stderr",
            "xml_dump_stderr",
            "screenshot_error",
            "xml_dump_error",
            "swipe_stderr",
            "raw_error_summary",
            "original_error_message",
        )
        for payload in payloads:
            for item in walk_dicts(payload):
                root_exception_type = root_exception_type or item.get("exception_type")
                root_exception_message = root_exception_message or item.get("exception_message") or item.get("exception")
                traceback_tail = traceback_tail or item.get("traceback_tail") or item.get("traceback")
                for key in (
                    "status",
                    "final_status",
                    "current_state",
                    "issue_code",
                    "stop_code",
                    "error_code",
                    "canonical_error_code",
                    "original_error_code",
                    "user_facing_error_code",
                ):
                    value = str(item.get(key) or "")
                    if value == "RESULT_SCHEMA_INVALID_FOR_PRICING":
                        wrapper_error_code = wrapper_error_code or value
                    if value in {
                        "DUPLICATE_REFERENCE_CLICK_BLOCKED",
                        V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
                    } or value in S12_CLAIM_FIELD_FAILURE_CODES or value in S10_NEXT_REFERENCE_BINDING_FAILURE_CODES:
                        pricing_result_issue_code = pricing_result_issue_code or value
                binding_result = item.get("binding_result") if isinstance(item.get("binding_result"), dict) else {}
                if binding_result:
                    binding_stop_code = binding_stop_code or binding_result.get("stop_code")
                    binding_code = str(binding_result.get("stop_code") or "")
                    if binding_code in {
                        "DUPLICATE_REFERENCE_CLICK_BLOCKED",
                        V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
                    } or binding_code in S12_CLAIM_FIELD_FAILURE_CODES or binding_code in S10_NEXT_REFERENCE_BINDING_FAILURE_CODES:
                        pricing_result_issue_code = pricing_result_issue_code or binding_code
                last_state = (
                    last_state
                    or item.get("failed_state")
                    or item.get("current_state")
                    or item.get("last_state")
                    or item.get("recognized_page")
                    or item.get("stage")
                )
                entered_s11 = entered_s11 or bool(item.get("entered_s11") is True or item.get("s11_page_recognized") is True)
            if traceback_tail and not (root_exception_function or root_exception_file):
                root_exception_function, root_exception_file = extract_traceback_location(str(traceback_tail))
            for key in message_keys:
                value = payload.get(key)
                if not value:
                    continue
                text = str(value)
                if original_code == "TARGET_ADB_DEVICE_NOT_CONNECTED" and "TARGET_ADB_DEVICE_NOT_CONNECTED" in self._codes_from_text(text):
                    original_message = text
                    break
                if original_message is None and key == "error":
                    original_message = text
            context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            startup = payload.get("startup") if isinstance(payload.get("startup"), dict) else {}
            focused_window = focused_window or payload.get("focused_window") or context.get("focused_window") or startup.get("focused_window_after") or startup.get("focused_window_before")
            foreground_package = foreground_package or payload.get("foreground_package") or context.get("foreground_package") or startup.get("foreground_package_after") or startup.get("foreground_package_before")
            if visible_text_digest is None:
                digest = payload.get("visible_text_digest") or context.get("visible_text_digest")
                if isinstance(digest, list):
                    visible_text_digest = [str(item) for item in digest[:20]]
        summary_parts = []
        if original_code:
            summary_parts.append(f"code={original_code}")
        if original_message:
            summary_parts.append(f"message={original_message}")
        if focused_window:
            summary_parts.append(f"focused_window={focused_window}")
        if foreground_package:
            summary_parts.append(f"foreground_package={foreground_package}")
        if visible_text_digest:
            summary_parts.append("visible_text_digest=" + "|".join(visible_text_digest[:10]))
        if root_exception_type:
            summary_parts.append(f"root_exception_type={root_exception_type}")
        if root_exception_message:
            summary_parts.append(f"root_exception_message={root_exception_message}")
        if root_exception_function:
            summary_parts.append(f"root_exception_function={root_exception_function}")
        if root_exception_file:
            summary_parts.append(f"root_exception_file={root_exception_file}")
        if last_state:
            summary_parts.append(f"last_state={last_state}")
        if entered_s11:
            summary_parts.append("entered_s11=true")
        return {
            "original_error_code": original_code,
            "original_error_message": original_message,
            "raw_error_summary": "; ".join(summary_parts),
            "focused_window": focused_window,
            "foreground_package": foreground_package,
            "root_exception_type": root_exception_type,
            "root_exception_message": root_exception_message,
            "root_exception_function": root_exception_function,
            "root_exception_file": root_exception_file,
            "root_cause_code": (
                "V149_REFERENCE_IDENTITY_SUMMARY_TYPE_ERROR"
                if root_exception_type == "TypeError" and "_reference_identity_summary" in str(root_exception_message or traceback_tail or "")
                else ("SECOND_STAGE_RUNTIME_EXCEPTION" if root_exception_type or traceback_tail else "")
            ),
            "wrapper_error_code": wrapper_error_code,
            "pricing_result_issue_code": pricing_result_issue_code,
            "binding_stop_code": binding_stop_code,
            "last_state": last_state,
            "entered_s11_before_failure": entered_s11,
        }

    def _normalize_concrete_failure_code(self, codes: list[str]) -> str:
        code_set = set(codes)
        for canonical, aliases in CONCRETE_FAILURE_PRIORITY:
            if code_set & aliases:
                return canonical
        return "UNKNOWN_PRECHECK_FAILED"

    def _primary_concrete_failure_code(self, codes: list[str]) -> str:
        if V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW in codes:
            return V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW
        if "DUPLICATE_REFERENCE_CLICK_BLOCKED" in codes:
            return "DUPLICATE_REFERENCE_CLICK_BLOCKED"
        for code in codes:
            if code in S10_NEXT_REFERENCE_BINDING_FAILURE_CODES:
                return code
        if any(
            code
            in {
                "SECOND_STAGE_RUNTIME_EXCEPTION",
                "V149_REFERENCE_IDENTITY_SUMMARY_TYPE_ERROR",
                "RESULT_SCHEMA_INVALID_FOR_PRICING_WITH_RUNTIME_EXCEPTION",
            }
            for code in codes
        ):
            return "SECOND_STAGE_RUNTIME_EXCEPTION"
        strict_non_auto = self._strict_non_auto_failure_codes(codes)
        if strict_non_auto:
            return next(code for code in codes if code in strict_non_auto)
        normalized = self._normalize_concrete_failure_code(codes)
        if normalized != "UNKNOWN_PRECHECK_FAILED":
            return normalized
        return codes[0] if codes else normalized

    def _default_final_feedback_message_sender(self) -> Callable[..., dict[str, Any]]:
        try:
            from feishu_send_message import send_text_message  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover - package-style imports in tests.
            from scripts.feishu_send_message import send_text_message  # type: ignore[no-redef]
        return send_text_message

    def _send_result_message_id(self, send_result: Any) -> str:
        if not isinstance(send_result, dict):
            return ""
        direct = send_result.get("message_id")
        if direct:
            return str(direct)
        data = send_result.get("data")
        if isinstance(data, dict):
            nested = data.get("message_id") or data.get("message", {}).get("message_id")
            if nested:
                return str(nested)
        message = send_result.get("message")
        if isinstance(message, dict) and message.get("message_id"):
            return str(message["message_id"])
        return ""

    def _final_feedback_live_sent(self, delivery: Any) -> bool:
        if not isinstance(delivery, dict):
            return False
        if delivery.get("dry_run") is True or delivery.get("final_feedback_delivery_dry_run") is True:
            return False
        send_result = delivery.get("business_send_result") or delivery.get("send_result")
        message_id = (
            str(delivery.get("final_feedback_message_id") or delivery.get("business_message_id") or delivery.get("message_id") or "")
            or self._send_result_message_id(send_result)
        )
        send_ok = isinstance(send_result, dict) and send_result.get("ok") is True
        return bool(delivery.get("final_feedback_sent") and send_ok and message_id)

    def _final_feedback_sent_flag_invalid_reason(self, status: dict[str, Any], delivery: Any) -> str:
        if not status.get("final_feedback_sent"):
            return ""
        if self._final_feedback_live_sent(delivery):
            return ""
        if isinstance(delivery, dict) and (
            delivery.get("dry_run") is True or delivery.get("final_feedback_delivery_dry_run") is True
        ):
            return FINAL_FEEDBACK_SENT_FLAG_INVALID_DRY_RUN_ONLY
        return FINAL_FEEDBACK_SENT_FLAG_INVALID_NO_LIVE_EVIDENCE

    def _failure_classification_status_fields(self, details: dict[str, Any]) -> dict[str, Any]:
        return {
            "primary_error_code": details.get("primary_error_code") or details.get("canonical_error_code"),
            "wrapper_error_code": details.get("wrapper_error_code"),
            "pricing_result_issue_code": details.get("pricing_result_issue_code"),
            "binding_stop_code": details.get("binding_stop_code"),
            "post_start_failure": bool(details.get("post_start_failure")),
            "post_start_failure_stage": details.get("post_start_failure_stage") or "",
            "second_stage_entered": bool(details.get("second_stage_entered")),
            "reached_s10_before_failure": bool(details.get("reached_s10_before_failure")),
            "entered_s11_before_failure": bool(details.get("entered_s11_before_failure")),
            "post_start_not_started_template_blocked": bool(details.get("post_start_not_started_template_blocked")),
            "post_start_failure_business_template": details.get("post_start_failure_business_template") or "",
            "post_start_failure_classification_reason": details.get("post_start_failure_classification_reason") or "",
            "feedback_primary_issue_source": details.get("feedback_primary_issue_source") or "",
            "feedback_low_score_evidence_present": bool(details.get("feedback_low_score_evidence_present")),
            "feedback_low_score_evidence_ignored_due_to_terminal_issue": bool(
                details.get("feedback_low_score_evidence_ignored_due_to_terminal_issue")
            ),
            "feedback_selected_template": details.get("feedback_selected_template") or "",
            "feedback_selected_reason_code": details.get("feedback_selected_reason_code") or "",
            "feedback_terminal_issue_code": details.get("feedback_terminal_issue_code") or "",
            "feedback_latest_reference_index": details.get("feedback_latest_reference_index"),
            "feedback_latest_failed_stage": details.get("feedback_latest_failed_stage") or "",
        }

    def _final_feedback_status_fields(self, delivery: dict[str, Any], *, feedback_type: str) -> dict[str, Any]:
        generated = bool(delivery.get("final_feedback_generated") or delivery.get("business_reply_text"))
        sent = self._final_feedback_live_sent(delivery)
        invalid_reason = str(delivery.get("final_feedback_sent_flag_invalid_reason") or "")
        fields: dict[str, Any] = {
            "final_feedback_generated": generated,
            "final_feedback_generated_at": delivery.get("final_feedback_generated_at") or delivery.get("created_at") or "",
            "final_feedback_delivery_dry_run": bool(
                delivery.get("dry_run") is True or delivery.get("final_feedback_delivery_dry_run") is True
            ),
            "final_feedback_send_attempted": bool(delivery.get("final_feedback_send_attempted")),
            "final_feedback_sent": sent,
            "final_feedback_sent_at": delivery.get("final_feedback_sent_at") if sent else "",
            "final_feedback_message_id": delivery.get("final_feedback_message_id") if sent else "",
            "final_feedback_type": feedback_type,
            "final_feedback_sent_flag_valid": bool(sent),
            "final_feedback_sent_flag_invalid_reason": "" if sent else invalid_reason,
            "final_feedback_guard_codes": list(delivery.get("guard_codes") or []),
            "final_feedback_business_chat_id": delivery.get("business_chat_id") or "",
            "final_feedback_admin_chat_id": delivery.get("admin_chat_id") or "",
        }
        if delivery.get("final_feedback_send_failed"):
            fields.update(
                {
                    "final_feedback_send_failed": True,
                    "final_feedback_send_error": delivery.get("send_error") or "FINAL_FEEDBACK_LIVE_SEND_FAILED",
                    "final_feedback_retryable": bool(delivery.get("retryable", True)),
                }
            )
        elif sent:
            fields.update(
                {
                    "final_feedback_send_failed": False,
                    "final_feedback_send_error": "",
                    "final_feedback_retryable": False,
                }
            )
        return fields

    def _send_final_feedback_text(
        self,
        *,
        message_sender: Callable[..., dict[str, Any]] | None,
        text: str,
        chat_id: str | None,
    ) -> dict[str, Any]:
        if not chat_id:
            return {
                "ok": False,
                "dry_run": False,
                "error_code": "FEISHU_CHAT_ID_MISSING",
                "message": "chat_id is required for live final feedback sending.",
            }
        sender = message_sender or self._default_final_feedback_message_sender()
        try:
            return sender(text=text, chat_id=chat_id, dry_run=False)
        except Exception as exc:  # pragma: no cover - defensive around injectable/live sender.
            return {
                "ok": False,
                "dry_run": False,
                "error_code": "FEISHU_SEND_EXCEPTION",
                "message": str(exc),
            }

    def _write_final_failure_feedback(
        self,
        task_id: str,
        *,
        status_payload: dict[str, Any],
        details: dict[str, Any],
        cancel_reason: str,
        result: dict[str, Any] | None = None,
        file_prefix: str = "final_failure",
        dry_run: bool = True,
        message_sender: Callable[..., dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        task_dir = self.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        existing_delivery = self._read_json(task_dir / FINAL_FAILURE_FEEDBACK_DELIVERY, default={})
        primary = str(details.get("canonical_error_code") or "UNKNOWN_PRECHECK_FAILED")
        idempotency_key = f"final_failure:{task_id}:{primary}"
        if (
            isinstance(existing_delivery, dict)
            and existing_delivery.get("business_reply_text")
            and str(existing_delivery.get("feishu_result_message_idempotent_key") or existing_delivery.get("idempotency_key") or "") == idempotency_key
            and (dry_run or self._final_feedback_live_sent(existing_delivery))
        ):
            return existing_delivery
        sent_flag_invalid_reason = str(status_payload.get("final_feedback_sent_flag_invalid_reason") or "") or (
            self._final_feedback_sent_flag_invalid_reason(status_payload, existing_delivery)
        )
        if (
            status_payload.get("final_feedback_sent")
            and not sent_flag_invalid_reason
            and isinstance(existing_delivery, dict)
            and existing_delivery.get("business_reply_text")
        ):
            existing_primary = str(existing_delivery.get("canonical_error_code") or "")
            if existing_primary == primary:
                return existing_delivery
            if primary == "UNKNOWN_PRECHECK_FAILED":
                return existing_delivery
            if existing_primary and existing_primary in CONCRETE_FAILURE_MESSAGES and existing_primary != "UNKNOWN_PRECHECK_FAILED":
                return existing_delivery
        raw_codes = list(details.get("canonical_error_codes") or [])
        business_reply = str(details.get("business_reply_text") or "")
        admin_reply = "\n".join(
            [
                f"任务 {task_id} 已取消且不再占用队列。",
                f"原因：{details.get('human_reason') or '手机执行环境暂不可用'}。",
                f"错误：{primary}。",
                "请确认手机连接、手机授权、瓜子登录、首页状态、弹窗阻挡等。",
            ]
        )
        if details.get("root_exception_type") or primary == "SECOND_STAGE_RUNTIME_EXCEPTION":
            admin_reply = "\n".join(
                [
                    f"任务 {task_id} 已取消且不再占用队列。",
                    "失败阶段：second_stage",
                    f"last_state={details.get('last_state') or details.get('original_stage') or ''}",
                    f"canonical_error_code={primary}",
                    f"original_error_code={details.get('original_error_code') or ''}",
                    f"root_cause_code={details.get('root_cause_code') or ''}",
                    f"exception_type={details.get('root_exception_type') or ''}",
                    f"exception_message={details.get('root_exception_message') or ''}",
                    f"source_function={details.get('root_exception_function') or ''}",
                    f"source_file={details.get('root_exception_file') or ''}",
                    f"reached_s10_before_failure={bool(details.get('reached_s10_before_failure'))}",
                    f"entered_s11_before_failure={bool(details.get('entered_s11_before_failure'))}",
                    f"raw_error_summary={details.get('raw_error_summary') or ''}",
                ]
            )
        if primary == "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED":
            pricing_result = self._read_json(task_dir / "pricing_result.json", default={})
            current_reference = pricing_result.get("current_reference") if isinstance(pricing_result, dict) else {}
            if not isinstance(current_reference, dict):
                current_reference = {}
            issue_context = pricing_result.get("issue_context") if isinstance(pricing_result, dict) else {}
            if not isinstance(issue_context, dict):
                issue_context = {}
            admin_reply = "\n".join(
                [
                    f"任务 {task_id} 已取消且不再占用队列。",
                    "失败阶段：S13",
                    "issue_code=S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED",
                    f"reference_index={details.get('feedback_latest_reference_index') or pricing_result.get('current_reference_index') or current_reference.get('reference_index') or ''}",
                    f"screenshot_path={issue_context.get('screenshot_path') or current_reference.get('screenshot_path') or ''}",
                    f"xml_path={issue_context.get('xml_path') or current_reference.get('xml_path') or ''}",
                    f"repair_count_candidates={issue_context.get('repair_count_candidates') or current_reference.get('repair_count_candidates') or current_reference.get('s13_repair_count_candidates') or ''}",
                    f"current_page_recognized={issue_context.get('recognized_page') or current_reference.get('recognized_page') or details.get('last_state') or ''}",
                    "suggested_action=inspect_s13_history_repair_count_evidence",
                    f"raw_error_summary={details.get('raw_error_summary') or ''}",
                ]
            )
        if (
            (
                primary == "SECOND_STAGE_RUNTIME_EXCEPTION"
                and str(details.get("root_exception_function") or "") == "_recover_s12_claim_fields"
            )
            or primary in {
                S12_CLAIM_RECOVERY_EXTENT_INVALID,
                S12_CLAIM_RECOVERY_BOUNDS_INVALID,
                S12_CLAIM_RECOVERY_CANDIDATE_MALFORMED,
                S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE,
            }
        ):
            s12_summary = details.get("s12_claim_field_failure_summary") if isinstance(details.get("s12_claim_field_failure_summary"), dict) else {}
            admin_reply = "\n".join(
                [
                    f"任务 {task_id} 已取消且不再占用队列。",
                    "失败阶段：S12",
                    f"issue_code={primary}",
                    f"reference_index={details.get('feedback_latest_reference_index') or s12_summary.get('current_reference_index') or ''}",
                    f"root_exception_type={details.get('root_exception_type') or ''}",
                    f"root_exception_message={details.get('root_exception_message') or ''}",
                    f"root_exception_function={details.get('root_exception_function') or s12_summary.get('root_exception_function') or '_recover_s12_claim_fields'}",
                    f"source_file={details.get('root_exception_file') or ''}",
                    f"screenshot_path={s12_summary.get('screenshot_path') or ''}",
                    f"xml_path={s12_summary.get('xml_path') or ''}",
                    f"s12_claim_field_recovery_attempted={s12_summary.get('s12_claim_field_recovery_attempted')}",
                    f"s12_claim_recovery_candidate_count={s12_summary.get('s12_claim_recovery_candidate_count') or ''}",
                    f"s12_claim_recovery_valid_candidate_count={s12_summary.get('s12_claim_recovery_valid_candidate_count') or ''}",
                    f"s12_claim_recovery_malformed_candidate_count={s12_summary.get('s12_claim_recovery_malformed_candidate_count') or ''}",
                    f"s12_claim_recovery_skipped_malformed_extents={s12_summary.get('s12_claim_recovery_skipped_malformed_extents') or ''}",
                    f"s12_claim_recovery_selected_candidate_extent={s12_summary.get('s12_claim_recovery_selected_candidate_extent') or ''}",
                    f"s12_claim_recovery_stop_code={s12_summary.get('s12_claim_recovery_stop_code') or ''}",
                    f"s12_missing_fields={s12_summary.get('s12_missing_fields') or ''}",
                    f"s12_claim_field_decision={s12_summary.get('s12_claim_field_decision') or ''}",
                    "suggested_action=inspect_s12_claim_field_recovery_trace_and_malformed_candidates",
                    f"raw_error_summary={details.get('raw_error_summary') or ''}",
                ]
            )
        if primary in S07_AGE_FAILURE_ERROR_CODES:
            first_stage_payload = self._read_json(task_dir / "first_stage_result.json", default={})
            context = first_stage_payload.get("context") if isinstance(first_stage_payload, dict) else {}
            age_action = context.get("age_action") if isinstance(context, dict) else {}
            admin_reply = "\n".join(
                [
                    f"任务 {task_id} 已取消且不再占用队列。",
                    f"原因：{details.get('human_reason') or '车龄筛选失败'}。",
                    f"错误：{primary}。",
                    "S07车龄筛选诊断：",
                    "highest_stage=S07",
                    f"target_age={age_action.get('target_age') or context.get('target_age_years') or ''}",
                    f"expected_age_filter={age_action.get('expected_age_filter') or age_action.get('target_range') or ''}",
                    f"action_algorithm_used={age_action.get('action_algorithm_used') or ''}",
                    f"direct_track_fastpath_used={age_action.get('direct_track_fastpath_used')}",
                    f"target_x={age_action.get('target_x')}",
                    f"drag_start_point={age_action.get('drag_start_point') or age_action.get('right_swipe_start') or ''}",
                    f"drag_start_inside_selected_handle_bounds={age_action.get('drag_start_inside_selected_handle_bounds')}",
                    f"drag_target_point={age_action.get('drag_target_point') or age_action.get('right_swipe_end') or ''}",
                    f"result_value_after={age_action.get('result_value_after') or ''}",
                    f"left_age_after={age_action.get('left_age_after')}",
                    f"right_age_after={age_action.get('right_age_after')}",
                    f"slider_value_changed={age_action.get('slider_value_changed')}",
                    f"slider_bounds_changed={age_action.get('slider_bounds_changed')}",
                    f"exact_text_verified={age_action.get('exact_text_verified')}",
                    f"foreground_package={details.get('foreground_package') or ''}",
                    f"focused_window={details.get('focused_window') or ''}",
                    f"screenshot_path={age_action.get('exact_snapshot_path') or context.get('screenshot_path') or ''}",
                    f"xml_path={age_action.get('exact_xml_path') or context.get('xml_path') or ''}",
                ]
            )
        if primary in {
            "DUPLICATE_REFERENCE_CLICK_BLOCKED",
            V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
        }:
            pricing_result = self._read_json(task_dir / "pricing_result.json", default={})
            issue_context = pricing_result.get("issue_context") if isinstance(pricing_result, dict) else {}
            binding_result = issue_context.get("binding_result") if isinstance(issue_context, dict) else {}
            selection = pricing_result.get("selection") if isinstance(pricing_result, dict) else {}
            if not isinstance(selection, dict):
                selection = {}
            terminal_trace = {}
            if isinstance(pricing_result, dict):
                for trace_candidate in (
                    pricing_result.get("v33_recollect_terminal_trace"),
                    selection.get("v33_recollect_terminal_trace"),
                    issue_context.get("v33_recollect_terminal_trace") if isinstance(issue_context, dict) else None,
                ):
                    if isinstance(trace_candidate, dict):
                        terminal_trace = trace_candidate
                        break
            admin_reply = "\n".join(
                [
                    f"任务 {task_id} 已取消且不再占用队列。",
                    "失败阶段=post_start_reference_collection",
                    f"canonical_error_code={primary}",
                    f"primary_error_code={details.get('primary_error_code') or primary}",
                    f"wrapper_error_code={details.get('wrapper_error_code') or ''}",
                    f"pricing_result_issue_code={details.get('pricing_result_issue_code') or ''}",
                    f"binding_stop_code={details.get('binding_stop_code') or binding_result.get('stop_code') or ''}",
                    f"target_reference_index={binding_result.get('target_reference_index') or pricing_result.get('current_reference_index') or ''}",
                    f"processed_reference_indices={binding_result.get('processed_reference_indices') or ''}",
                    f"recollect_mode={binding_result.get('recollect_mode') or pricing_result.get('recollect_mode') or ''}",
                    f"boundary_reference_index={binding_result.get('boundary_reference_index') or pricing_result.get('boundary_reference_index') or ''}",
                    f"boundary_reference_score={terminal_trace.get('boundary_reference_score') or pricing_result.get('boundary_reference_score') or ''}",
                    f"target_score={terminal_trace.get('target_score') or pricing_result.get('target_score_value') or pricing_result.get('target_score') or ''}",
                    f"final_reference_candidate_index={terminal_trace.get('v33_final_reference_candidate_index') or pricing_result.get('final_reference_candidate_index') or ''}",
                    f"recollect_reference_index={terminal_trace.get('v33_recollect_reference_index') or pricing_result.get('recollect_reference_index') or ''}",
                    f"recollect_terminal_decision={terminal_trace.get('v33_recollect_terminal_decision') or pricing_result.get('recollect_terminal_decision') or ''}",
                    f"recollect_terminal_candidate_status={terminal_trace.get('v33_recollect_terminal_candidate_status') or pricing_result.get('candidate_status') or ''}",
                    f"recollect_blocked_low_score_continue={terminal_trace.get('v33_recollect_blocked_low_score_continue')}",
                    f"recollect_prevented_next_boundary_reclick={terminal_trace.get('v33_recollect_prevented_next_boundary_reclick')}",
                    f"duplicate_detected_by_index={binding_result.get('duplicate_detected_by_index')}",
                    f"duplicate_detected_by_identity={binding_result.get('duplicate_detected_by_identity')}",
                    f"duplicate_reference_allowed_for_recollect={binding_result.get('duplicate_reference_allowed_for_recollect')}",
                    f"duplicate_reference_allowed_reason={binding_result.get('duplicate_reference_allowed_reason') or ''}",
                    f"suggested_action={'manual_review_required_no_boundary_reclick' if primary == V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW else 'inspect_recollect_continuation_without_reclicking_boundary'}",
                    f"reached_s10_before_failure={bool(details.get('reached_s10_before_failure'))}",
                    f"entered_s11_before_failure={bool(details.get('entered_s11_before_failure'))}",
                    f"post_start_failure={bool(details.get('post_start_failure'))}",
                    f"post_start_failure_stage={details.get('post_start_failure_stage') or ''}",
                    f"last_state={details.get('last_state') or ''}",
                    f"raw_error_summary={details.get('raw_error_summary') or ''}",
                ]
            )
        adb_diagnostics = _adb_diagnostics_from_payloads(details, result or {})
        if adb_diagnostics:
            admin_reply = "\n".join(
                [
                    admin_reply,
                    "ADB诊断：",
                    f"target_serial={adb_diagnostics.get('target_adb_serial') or ''}",
                    f"target_device_state={adb_diagnostics.get('target_device_state') or ''}",
                    f"adb_path_source={adb_diagnostics.get('adb_path_source') or ''}",
                    f"adb_runtime_env_mode={adb_diagnostics.get('adb_runtime_env_mode') or ''}",
                    f"adb_command_preview={adb_diagnostics.get('adb_command_preview') or ''}",
                ]
            )
        business_chat_id = status_payload.get("business_chat_id") or status_payload.get("raw_chat_id")
        admin_chat_id = status_payload.get("admin_chat_id") or status_payload.get("admin_notify_chat_id")
        created_at = _isoformat(self.clock())
        guard_codes: list[str] = []
        business_send_result: dict[str, Any] | None = None
        admin_send_result: dict[str, Any] | None = None
        business_message_id = ""
        admin_message_id = ""
        send_error = ""
        if dry_run:
            guard_codes.append(FINAL_FEEDBACK_DRY_RUN_NOT_MARKED_SENT)
        else:
            guard_codes.append(FINAL_FEEDBACK_LIVE_SEND_ATTEMPTED)
            business_send_result = self._send_final_feedback_text(
                message_sender=message_sender,
                text=business_reply,
                chat_id=str(business_chat_id) if business_chat_id else None,
            )
            business_message_id = self._send_result_message_id(business_send_result)
            if not (business_send_result.get("ok") is True and business_message_id):
                send_error = str(
                    business_send_result.get("error_code")
                    or business_send_result.get("message")
                    or "BUSINESS_FINAL_FEEDBACK_SEND_FAILED"
                )
            if admin_chat_id:
                admin_send_result = self._send_final_feedback_text(
                    message_sender=message_sender,
                    text=admin_reply,
                    chat_id=str(admin_chat_id),
                )
                admin_message_id = self._send_result_message_id(admin_send_result)
            else:
                guard_codes.append(ADMIN_CHAT_ID_MISSING)
                admin_send_result = {
                    "ok": False,
                    "dry_run": False,
                    "error_code": ADMIN_CHAT_ID_MISSING,
                    "message": "admin_chat_id is missing; business final feedback is not blocked.",
                }
        final_feedback_sent = bool(
            not dry_run
            and isinstance(business_send_result, dict)
            and business_send_result.get("ok") is True
            and business_message_id
        )
        if final_feedback_sent:
            guard_codes.append(FINAL_FEEDBACK_LIVE_SEND_SUCCEEDED)
        elif not dry_run:
            guard_codes.append(FINAL_FEEDBACK_LIVE_SEND_FAILED)
        if sent_flag_invalid_reason:
            guard_codes.append(sent_flag_invalid_reason)
        delivery = {
            "ok": bool(final_feedback_sent or dry_run),
            "dry_run": bool(dry_run),
            "task_id": task_id,
            "status": "CANCELLED",
            "cancelled": True,
            "cancel_reason": cancel_reason,
            "canonical_error_code": primary,
            "canonical_error_codes": raw_codes,
            "feishu_result_message_idempotent_key": idempotency_key,
            "idempotency_key": idempotency_key,
            "user_facing_error_code": details.get("user_facing_error_code") or primary,
            "original_error_code": details.get("original_error_code"),
            "original_error_message": details.get("original_error_message"),
            "original_stage": details.get("original_stage"),
            "highest_stage": details.get("highest_stage"),
            "original_stop_code": details.get("original_stop_code"),
            "foreground_package": details.get("foreground_package"),
            "focused_window": details.get("focused_window"),
            "raw_error_summary": details.get("raw_error_summary"),
            "focused_window": details.get("focused_window"),
            "foreground_package": details.get("foreground_package"),
            "human_reason": details.get("human_reason"),
            "user_facing_reason": details.get("user_facing_reason") or details.get("human_reason"),
            "reached_s10_before_failure": bool(details.get("reached_s10_before_failure")),
            "app_foreground_confirmed_before_failure": bool(details.get("app_foreground_confirmed_before_failure")),
            "entered_s11_before_failure": bool(details.get("entered_s11_before_failure")),
            "last_state": details.get("last_state"),
            "root_exception_type": details.get("root_exception_type"),
            "root_exception_message": details.get("root_exception_message"),
            "root_exception_function": details.get("root_exception_function"),
            "root_exception_file": details.get("root_exception_file"),
            "root_cause_code": details.get("root_cause_code"),
            "wrapper_error_code": details.get("wrapper_error_code"),
            "pricing_result_issue_code": details.get("pricing_result_issue_code"),
            "binding_stop_code": details.get("binding_stop_code"),
            **self._failure_classification_status_fields(details),
            "retry_instruction": details.get("retry_instruction"),
            "blocks_queue": False,
            "active_runner_exists": False,
            "released_old_blocker_task_ids": [],
            "queued_continued": False,
            "business_chat_id": business_chat_id,
            "admin_chat_id": admin_chat_id,
            "business_reply_text": business_reply,
            "admin_reply_text": admin_reply,
            "adb_diagnostics": adb_diagnostics,
            "result": result or {},
            "created_at": created_at,
            "final_feedback_generated": True,
            "final_feedback_generated_at": created_at,
            "final_feedback_delivery_dry_run": bool(dry_run),
            "final_feedback_send_attempted": not dry_run,
            "business_send_attempted": not dry_run,
            "business_send_result": business_send_result or {},
            "business_message_id": business_message_id,
            "send_result": business_send_result or {},
            "message_id": business_message_id if final_feedback_sent else "",
            "admin_send_attempted": bool(not dry_run and admin_chat_id),
            "admin_send_result": admin_send_result or {},
            "admin_message_id": admin_message_id,
            "final_feedback_sent": final_feedback_sent,
            "final_feedback_sent_at": created_at if final_feedback_sent else "",
            "final_feedback_message_id": business_message_id if final_feedback_sent else "",
            "final_feedback_sent_flag_valid": final_feedback_sent,
            "final_feedback_sent_flag_invalid_reason": "" if final_feedback_sent else sent_flag_invalid_reason,
            "final_feedback_send_failed": bool((not dry_run) and not final_feedback_sent),
            "send_error": send_error,
            "retryable": bool((not dry_run) and not final_feedback_sent),
            "guard_codes": guard_codes,
            "admin_feedback_guard_code": ADMIN_CHAT_ID_MISSING if (not dry_run and not admin_chat_id) else "",
        }
        (task_dir / FINAL_FAILURE_BUSINESS_REPLY).write_text(business_reply + "\n", encoding="utf-8")
        (task_dir / FINAL_FAILURE_ADMIN_REPLY).write_text(admin_reply + "\n", encoding="utf-8")
        self._write_json(task_dir / FINAL_FAILURE_FEEDBACK_DELIVERY, delivery)
        if file_prefix != "final_failure":
            business_name = f"{file_prefix}_business_reply.preview.txt"
            admin_name = f"{file_prefix}_admin_reply.preview.txt"
            delivery_name = f"{file_prefix}_delivery.json"
            if file_prefix == "not_started_auto_cancel":
                business_name = NOT_STARTED_AUTO_CANCEL_BUSINESS_REPLY
                admin_name = NOT_STARTED_AUTO_CANCEL_ADMIN_REPLY
                delivery_name = NOT_STARTED_AUTO_CANCEL_DELIVERY
            elif file_prefix == "released_blocker":
                business_name = RELEASED_BLOCKER_BUSINESS_REPLY
                admin_name = RELEASED_BLOCKER_ADMIN_REPLY
                delivery_name = RELEASED_BLOCKER_DELIVERY
            (task_dir / business_name).write_text(business_reply + "\n", encoding="utf-8")
            (task_dir / admin_name).write_text(admin_reply + "\n", encoding="utf-8")
            self._write_json(task_dir / delivery_name, delivery)
        return delivery

    def cancel_confirm_failure(
        self,
        task_id: str,
        *,
        errors: list[str] | None = None,
        result: dict[str, Any] | None = None,
        cancel_reason: str = SYSTEM_PRECHECK_FAILED_NOT_STARTED,
        dry_run: bool = True,
        message_sender: Callable[..., dict[str, Any]] | None = None,
    ) -> TaskOperationResult:
        status = self.load_status(task_id)
        if not status:
            return self._unknown_task("cancel_confirm_failure", task_id)
        details = self.concrete_failure_details(task_id, status=status, errors=errors, result=result)
        raw_codes = list(details.get("canonical_error_codes") or [])
        primary = str(details.get("canonical_error_code") or "UNKNOWN_PRECHECK_FAILED")
        cancelled_at = _isoformat(self.clock())
        extra = {
            "technical_status": "CANCELLED",
            "business_status": "CANCELLED",
            "recommended_next_action": "resend-target-info",
            "cancel_reason": cancel_reason,
            "canonical_error_code": primary,
            "canonical_error_codes": raw_codes,
            "user_facing_error_code": primary,
            "canonical_blocking_error_code": primary,
            "canonical_blocking_error_codes": raw_codes,
            "admin_intervention_error_code": primary,
            "admin_intervention_error_codes": raw_codes,
            "last_blocking_error_code": primary,
            "last_blocking_error_codes": raw_codes,
            "original_error_code": details.get("original_error_code"),
            "original_error_message": details.get("original_error_message"),
            "original_stage": details.get("original_stage"),
            "highest_stage": details.get("highest_stage"),
            "original_stop_code": details.get("original_stop_code"),
            "foreground_package": details.get("foreground_package"),
            "focused_window": details.get("focused_window"),
            "raw_error_summary": details.get("raw_error_summary"),
            "blocks_queue": False,
            "recoverable_by_health_check": False,
            "auto_cancelled": True,
            "auto_cancelled_at": cancelled_at,
            "auto_cancelled_reason": cancel_reason,
            "auto_cancelled_from_status": status.get("status"),
            "errors": raw_codes,
            "human_reason": details.get("human_reason"),
            "user_facing_reason": details.get("user_facing_reason") or details.get("human_reason"),
            "reached_s10_before_failure": bool(details.get("reached_s10_before_failure")),
            "app_foreground_confirmed_before_failure": bool(details.get("app_foreground_confirmed_before_failure")),
            "entered_s11_before_failure": bool(details.get("entered_s11_before_failure")),
            "last_state": details.get("last_state"),
            "root_exception_type": details.get("root_exception_type"),
            "root_exception_message": details.get("root_exception_message"),
            "root_exception_function": details.get("root_exception_function"),
            "root_exception_file": details.get("root_exception_file"),
            "root_cause_code": details.get("root_cause_code"),
            "wrapper_error_code": details.get("wrapper_error_code"),
            "pricing_result_issue_code": details.get("pricing_result_issue_code"),
            "binding_stop_code": details.get("binding_stop_code"),
            **self._failure_classification_status_fields(details),
            "retry_instruction": details.get("retry_instruction"),
            "final_feedback_generated": False,
            "final_feedback_delivery_dry_run": bool(dry_run),
            "final_feedback_send_attempted": False,
            "final_feedback_sent": False,
            "final_feedback_sent_at": "",
            "final_feedback_message_id": "",
            "final_feedback_sent_flag_valid": False,
            "final_feedback_type": "confirm_failure_cancelled",
        }
        updated = self._set_status(task_id, "CANCELLED", extra=extra)
        current_target_cleared = self._clear_current_target_task_if_matches(task_id)
        feedback = self._write_final_failure_feedback(
            task_id,
            status_payload={**status, **updated},
            details=details,
            cancel_reason=cancel_reason,
            result=result,
            dry_run=dry_run,
            message_sender=message_sender,
        )
        updated = self.update_task_status_fields(
            task_id,
            fields=self._final_feedback_status_fields(feedback, feedback_type="confirm_failure_cancelled"),
        )
        self._append_audit(
            action="cancel_confirm_failure",
            task_id=task_id,
            status="CANCELLED",
            success=True,
        )
        return TaskOperationResult(
            success=True,
            action="cancel_confirm_failure",
            task_id=task_id,
            status="CANCELLED",
            changed=True,
            reply_text=feedback["business_reply_text"],
            data={
                "status": updated,
                "feedback": feedback,
                "error_codes": raw_codes,
                "current_target_task_cleared": current_target_cleared,
            },
        )

    def ensure_cancelled_task_final_feedback(
        self,
        task_id: str,
        *,
        dry_run: bool = True,
        message_sender: Callable[..., dict[str, Any]] | None = None,
    ) -> TaskOperationResult:
        status = self.load_status(task_id)
        if not status:
            return self._unknown_task("ensure_cancelled_task_final_feedback", task_id)
        current = str(status.get("status") or "")
        if current != "CANCELLED":
            return TaskOperationResult(
                success=False,
                action="ensure_cancelled_task_final_feedback",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text="TASK_NOT_CANCELLED",
            )
        result_context = self._read_json(self.task_dir(task_id) / "pricing_result.json", default={})
        details = self.concrete_failure_details(
            task_id,
            status=status,
            errors=list(status.get("errors") or []),
            result=result_context if isinstance(result_context, dict) else None,
        )
        existing = self._read_json(self.task_dir(task_id) / FINAL_FAILURE_FEEDBACK_DELIVERY, default={})
        invalid_sent_reason = self._final_feedback_sent_flag_invalid_reason(status, existing)
        if (
            dry_run
            and not invalid_sent_reason
            and not status.get("final_feedback_sent")
            and status.get("final_feedback_generated")
            and isinstance(existing, dict)
            and existing.get("business_reply_text")
            and str(existing.get("canonical_error_code") or "") == str(details.get("canonical_error_code") or "")
        ):
            return TaskOperationResult(
                success=True,
                action="ensure_cancelled_task_final_feedback",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text=str(existing["business_reply_text"]),
                data={
                    "feedback": existing,
                    "status": status,
                    "sent_flag_valid": False,
                    "sent_flag_invalid_reason": "",
                },
            )
        if (
            status.get("final_feedback_sent")
            and not invalid_sent_reason
            and self._final_feedback_live_sent(existing)
            and isinstance(existing, dict)
            and existing.get("business_reply_text")
            and str(existing.get("canonical_error_code") or "") == str(details.get("canonical_error_code") or "")
        ):
            return TaskOperationResult(
                success=True,
                action="ensure_cancelled_task_final_feedback",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text=str(existing["business_reply_text"]),
                data={"feedback": existing, "status": status},
            )
        feedback_at = _isoformat(self.clock())
        updated = self._set_status(
            task_id,
            "CANCELLED",
            extra={
                "canonical_error_code": details.get("canonical_error_code"),
                "canonical_error_codes": details.get("canonical_error_codes"),
                "user_facing_error_code": details.get("user_facing_error_code") or details.get("canonical_error_code"),
                "canonical_blocking_error_code": details.get("canonical_error_code"),
                "canonical_blocking_error_codes": details.get("canonical_error_codes"),
                "admin_intervention_error_code": details.get("canonical_error_code"),
                "admin_intervention_error_codes": details.get("canonical_error_codes"),
                "last_blocking_error_code": details.get("canonical_error_code"),
                "last_blocking_error_codes": details.get("canonical_error_codes"),
                "errors": details.get("canonical_error_codes"),
                "original_error_code": details.get("original_error_code"),
                "original_error_message": details.get("original_error_message"),
                "original_stage": details.get("original_stage"),
                "highest_stage": details.get("highest_stage"),
                "original_stop_code": details.get("original_stop_code"),
                "foreground_package": details.get("foreground_package"),
                "focused_window": details.get("focused_window"),
                "raw_error_summary": details.get("raw_error_summary"),
                "human_reason": details.get("human_reason"),
                "user_facing_reason": details.get("user_facing_reason") or details.get("human_reason"),
                "reached_s10_before_failure": bool(details.get("reached_s10_before_failure")),
                "app_foreground_confirmed_before_failure": bool(details.get("app_foreground_confirmed_before_failure")),
                "entered_s11_before_failure": bool(details.get("entered_s11_before_failure")),
                "last_state": details.get("last_state"),
                "root_exception_type": details.get("root_exception_type"),
                "root_exception_message": details.get("root_exception_message"),
                "root_exception_function": details.get("root_exception_function"),
                "root_exception_file": details.get("root_exception_file"),
                "root_cause_code": details.get("root_cause_code"),
                "wrapper_error_code": details.get("wrapper_error_code"),
                "pricing_result_issue_code": details.get("pricing_result_issue_code"),
                "binding_stop_code": details.get("binding_stop_code"),
            **self._failure_classification_status_fields(details),
                "retry_instruction": details.get("retry_instruction"),
                "blocks_queue": False,
                "recoverable_by_health_check": False,
                "final_feedback_generated": bool(existing.get("business_reply_text")) if isinstance(existing, dict) else False,
                "final_feedback_delivery_dry_run": bool(dry_run),
                "final_feedback_send_attempted": False,
                "final_feedback_sent": False,
                "final_feedback_sent_at": "",
                "final_feedback_message_id": "",
                "final_feedback_sent_flag_valid": False,
                "final_feedback_sent_flag_invalid_reason": invalid_sent_reason,
                "final_feedback_compensation_attempted": bool(invalid_sent_reason),
                "final_feedback_compensation_attempted_at": feedback_at if invalid_sent_reason else "",
                "final_feedback_type": "cancelled_task_feedback_backfill",
            },
        )
        feedback = self._write_final_failure_feedback(
            task_id,
            status_payload={**status, **updated},
            details=details,
            cancel_reason=str(updated.get("cancel_reason") or "CANCELLED"),
            result=result_context if isinstance(result_context, dict) else None,
            dry_run=dry_run,
            message_sender=message_sender,
        )
        updated = self.update_task_status_fields(
            task_id,
            fields={
                **self._final_feedback_status_fields(feedback, feedback_type="cancelled_task_feedback_backfill"),
                "final_feedback_compensation_attempted": bool(invalid_sent_reason),
                "final_feedback_compensation_reason": invalid_sent_reason,
            },
        )
        self._append_audit(
            action="ensure_cancelled_task_final_feedback",
            task_id=task_id,
            status="CANCELLED",
            success=True,
        )
        return TaskOperationResult(
            success=True,
            action="ensure_cancelled_task_final_feedback",
            task_id=task_id,
            status="CANCELLED",
            changed=True,
            reply_text=feedback["business_reply_text"],
            data={
                "feedback": feedback,
                "status": updated,
                "sent_flag_valid": self._final_feedback_live_sent(feedback),
                "sent_flag_invalid_reason": invalid_sent_reason,
            },
        )

    def release_blocker_without_active_runner(
        self,
        task_id: str,
        *,
        dry_run: bool = True,
        message_sender: Callable[..., dict[str, Any]] | None = None,
    ) -> TaskOperationResult:
        status = self.load_status(task_id)
        if not status:
            return self._unknown_task("release_blocker_without_active_runner", task_id)
        current = str(status.get("status") or "")
        if current not in {SYSTEM_BLOCKED, ADMIN_INTERVENTION_REQUIRED}:
            return TaskOperationResult(
                success=False,
                action="release_blocker_without_active_runner",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text="TASK_NOT_BLOCKING_QUEUE",
            )

        error_codes = self._admin_error_codes(status)
        result_context = self._read_json(self.task_dir(task_id) / "pricing_result.json", default={})
        details = self.concrete_failure_details(
            task_id,
            status=status,
            errors=error_codes,
            result=result_context if isinstance(result_context, dict) else None,
        )
        raw_codes = list(details.get("canonical_error_codes") or error_codes)
        primary = str(details.get("canonical_error_code") or (error_codes[0] if error_codes else NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER))
        released_at = _isoformat(self.clock())
        extra = {
            "technical_status": "CANCELLED",
            "business_status": "CANCELLED",
            "recommended_next_action": "resend-target-info",
            "cancel_reason": NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER,
            "canonical_error_code": primary,
            "canonical_error_codes": raw_codes,
            "user_facing_error_code": primary,
            "canonical_blocking_error_code": primary,
            "canonical_blocking_error_codes": raw_codes,
            "admin_intervention_error_code": primary,
            "admin_intervention_error_codes": raw_codes,
            "last_blocking_error_code": primary,
            "last_blocking_error_codes": raw_codes,
            "original_error_code": details.get("original_error_code"),
            "original_error_message": details.get("original_error_message"),
            "original_stage": details.get("original_stage"),
            "highest_stage": details.get("highest_stage"),
            "original_stop_code": details.get("original_stop_code"),
            "foreground_package": details.get("foreground_package"),
            "focused_window": details.get("focused_window"),
            "raw_error_summary": details.get("raw_error_summary"),
            "blocks_queue": False,
            "recoverable_by_health_check": False,
            "released_from_blocker_at": released_at,
            "released_from_blocker_reason": NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER,
            "auto_cancelled": True,
            "auto_cancelled_at": released_at,
            "auto_cancelled_reason": NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER,
            "auto_cancelled_from_status": current,
            "errors": raw_codes,
            "human_reason": details.get("human_reason"),
            "user_facing_reason": details.get("user_facing_reason") or details.get("human_reason"),
            "reached_s10_before_failure": bool(details.get("reached_s10_before_failure")),
            "app_foreground_confirmed_before_failure": bool(details.get("app_foreground_confirmed_before_failure")),
            "entered_s11_before_failure": bool(details.get("entered_s11_before_failure")),
            "last_state": details.get("last_state"),
            "root_exception_type": details.get("root_exception_type"),
            "root_exception_message": details.get("root_exception_message"),
            "root_exception_function": details.get("root_exception_function"),
            "root_exception_file": details.get("root_exception_file"),
            "root_cause_code": details.get("root_cause_code"),
            "wrapper_error_code": details.get("wrapper_error_code"),
            "pricing_result_issue_code": details.get("pricing_result_issue_code"),
            "binding_stop_code": details.get("binding_stop_code"),
            **self._failure_classification_status_fields(details),
            "retry_instruction": details.get("retry_instruction"),
            "final_feedback_generated": False,
            "final_feedback_delivery_dry_run": bool(dry_run),
            "final_feedback_send_attempted": False,
            "final_feedback_sent": False,
            "final_feedback_sent_at": "",
            "final_feedback_message_id": "",
            "final_feedback_sent_flag_valid": False,
            "final_feedback_type": "released_blocker_cancelled",
        }
        updated = self._set_status(task_id, "CANCELLED", extra=extra)
        current_target_cleared = self._clear_current_target_task_if_matches(task_id)
        feedback = self._write_released_blocker_feedback(
            task_id,
            status_payload={**status, **updated},
            error_codes=error_codes,
            result=result_context if isinstance(result_context, dict) else None,
            dry_run=dry_run,
            message_sender=message_sender,
        )
        updated = self.update_task_status_fields(
            task_id,
            fields=self._final_feedback_status_fields(feedback, feedback_type="released_blocker_cancelled"),
        )
        self._append_audit(
            action="release_blocker_without_active_runner",
            task_id=task_id,
            status="CANCELLED",
            success=True,
        )
        return TaskOperationResult(
            success=True,
            action="release_blocker_without_active_runner",
            task_id=task_id,
            status="CANCELLED",
            changed=True,
            reply_text=feedback["business_reply_text"],
            data={
                "status": updated,
                "feedback": feedback,
                "error_codes": error_codes,
                "current_target_task_cleared": current_target_cleared,
            },
        )

    def _clear_current_target_task_if_matches(self, task_id: str) -> bool:
        payload = self._read_json(self.current_target_task_path, default={})
        if not isinstance(payload, dict) or payload.get("task_id") != task_id:
            return False
        self.current_target_task_path.unlink(missing_ok=True)
        return True

    def _write_released_blocker_feedback(
        self,
        task_id: str,
        *,
        status_payload: dict[str, Any],
        error_codes: list[str],
        result: dict[str, Any] | None = None,
        dry_run: bool = True,
        message_sender: Callable[..., dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result_context = result if isinstance(result, dict) else self._read_json(self.task_dir(task_id) / "pricing_result.json", default={})
        details = self.concrete_failure_details(
            task_id,
            status=status_payload,
            errors=error_codes,
            result=result_context if isinstance(result_context, dict) else None,
        )
        base_reply = str(details.get("business_reply_text") or "")
        details = {
            **details,
            "business_reply_text": "\n".join(
                [
                    base_reply,
                    "历史未完成任务已自动取消并释放队列。",
                ]
            ),
        }
        delivery = self._write_final_failure_feedback(
            task_id,
            status_payload=status_payload,
            details=details,
            cancel_reason=NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER,
            result=result_context if isinstance(result_context, dict) else None,
            file_prefix="released_blocker",
            dry_run=dry_run,
            message_sender=message_sender,
        )
        return delivery

    def resolve_admin_intervention(
        self,
        *,
        task_id: str | None = None,
        resolved_by_open_id: str | None = None,
        health_result: dict[str, Any] | None = None,
        automatic: bool = False,
        cooldown_seconds: int = DEFAULT_HEALTH_CHECK_COOLDOWN_SECONDS,
    ) -> TaskOperationResult:
        if not task_id:
            candidates = self.admin_blocked_tasks(recoverable_only=True)
            if not candidates:
                return TaskOperationResult(
                    success=False,
                    action="resolve_admin_intervention",
                    changed=False,
                    reply_text="当前没有可由管理员恢复的阻塞任务。",
                    data={"candidate_task_ids": []},
                )
            if len(candidates) > 1:
                return TaskOperationResult(
                    success=False,
                    action="resolve_admin_intervention",
                    changed=False,
                    reply_text="当前有多个待处理任务，请回复对应任务卡片“确认”，或输入任务号确认。",
                    data={"candidate_task_ids": candidates},
                )
            task_id = candidates[0]

        status = self.load_status(task_id)
        if not status:
            return self._unknown_task("resolve_admin_intervention", task_id)
        current = str(status.get("status") or "")
        if current == "CANCELLED":
            return TaskOperationResult(
                success=False,
                action="resolve_admin_intervention",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text=CANCELLED_TASK_RESEND_REPLY,
            )
        if current in {TARGET_INFO_NEEDS_CORRECTION, WAITING_TARGET_INFO_CORRECTION, ADMIN_TARGET_INFO_NEEDS_CORRECTION}:
            return TaskOperationResult(
                success=False,
                action="resolve_admin_intervention",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text=f"任务 {task_id} 是目标车信息需要修改，不能由管理员直接恢复，请提交人重新发送车源信息。",
            )
        if current not in {SYSTEM_BLOCKED, ADMIN_INTERVENTION_REQUIRED}:
            return TaskOperationResult(
                success=False,
                action="resolve_admin_intervention",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text=f"任务 {task_id} 当前状态为 {current}，不需要管理员恢复。",
            )

        error_codes = self._admin_error_codes(status)
        if not is_recoverable_admin_error(errors=error_codes, result=status):
            return TaskOperationResult(
                success=False,
                action="resolve_admin_intervention",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text=f"任务 {task_id} 的错误需要人工排查后重新处理，不能直接恢复队列。",
                data={"error_codes": error_codes},
            )
        recovered_system_block = is_auto_health_recoverable_error(errors=error_codes, result=status)
        if recovered_system_block:
            status = self.repair_blocking_reason_fields(
                task_id,
                status=status,
                normalize_recoverable_system_block=True,
            )
            current = str(status.get("status") or current)

        if health_result is None:
            return TaskOperationResult(
                success=False,
                action="resolve_admin_intervention",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text=f"任务 {task_id} 需要先通过系统健康检查，暂未恢复队列。",
                data={"error_codes": error_codes, "health_check_required": True},
            )

        self.record_system_blocked_health_check(task_id, health_result, cooldown_seconds=cooldown_seconds)
        if not health_result.get("ok", False):
            status_after_health = self.load_status(task_id) or status
            reply_text = format_system_not_recovered_reply(
                task_id,
                health_result=health_result,
                error_codes=error_codes,
            )
            self._write_admin_system_not_recovered_feedback(
                task_id,
                reply_text=reply_text,
                health_result=health_result,
                error_codes=error_codes,
            )
            self._append_audit(
                action="admin_intervention_health_check_failed",
                task_id=task_id,
                status=str(status_after_health.get("status") or current),
                success=False,
            )
            return TaskOperationResult(
                success=False,
                action="resolve_admin_intervention",
                task_id=task_id,
                status=str(status_after_health.get("status") or current),
                changed=False,
                reply_text=reply_text,
                data={"error_codes": error_codes, "health_result": health_result},
            )

        resolved_at = _isoformat(self.clock())
        extra = {
            "technical_status": "RECOVERED",
            "business_status": "QUEUED",
            "recommended_next_action": "wait-dispatcher",
            "admin_intervention_resolved": True,
            "admin_intervention_resolved_at": resolved_at,
            "admin_intervention_resolved_by": resolved_by_open_id,
            "admin_intervention_resolved_from": current,
            "admin_intervention_resolved_error_codes": error_codes,
            "admin_intervention_auto_resolved": bool(automatic),
            "admin_intervention_auto_resolved_at": resolved_at if automatic else None,
            "admin_intervention_manual_resolved": not automatic,
            "queued_at": resolved_at,
        }
        updated = self._set_status(task_id, "QUEUED", extra=extra)
        self._append_audit(
            action="resolve_admin_intervention",
            task_id=task_id,
            status="QUEUED",
            success=True,
        )
        return TaskOperationResult(
            success=True,
            action="resolve_admin_intervention",
            task_id=task_id,
            status="QUEUED",
            changed=True,
            reply_text=f"【定价已开始】{task_id}\n系统已开始自动定价，请等待结果。",
            data={"status": updated, "error_codes": error_codes},
        )

    def _write_admin_system_not_recovered_feedback(
        self,
        task_id: str,
        *,
        reply_text: str,
        health_result: dict[str, Any],
        error_codes: list[str],
    ) -> None:
        task_dir = self.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "admin_system_not_recovered_reply.preview.txt").write_text(reply_text + "\n", encoding="utf-8")
        self._write_json(
            task_dir / "admin_system_not_recovered_delivery.json",
            {
                "ok": False,
                "dry_run": True,
                "task_id": task_id,
                "reply_text": reply_text,
                "health_result": health_result,
                "error_codes": error_codes,
                "created_at": _isoformat(self.clock()),
            },
        )

    def target_info_correction_context_tasks(
        self,
        *,
        sender_open_id: str | None = None,
        business_chat_id: str | None = None,
        confirm_card_message_id: str | None = None,
    ) -> list[str]:
        candidates: list[tuple[str, str]] = []
        for task_id, status in self._iter_statuses():
            if status.get("status") not in TARGET_INFO_CORRECTION_STATUSES:
                continue
            if confirm_card_message_id and status.get("confirm_card_message_id") != confirm_card_message_id:
                continue
            if not confirm_card_message_id:
                if sender_open_id and status.get("sender_open_id") != sender_open_id and status.get("raw_sender_id") != sender_open_id:
                    continue
                if business_chat_id and status.get("business_chat_id") != business_chat_id and status.get("raw_chat_id") != business_chat_id:
                    continue
            stamp = str(status.get("updated_at") or status.get("created_at") or "")
            candidates.append((stamp, task_id))
        candidates.sort()
        return [task_id for _, task_id in candidates]

    def manual_price_context_tasks(
        self,
        *,
        chat_id: str | None = None,
        supervisor_review_card_message_id: str | None = None,
    ) -> list[str]:
        candidates: list[tuple[str, str]] = []
        for task_id, status in self._iter_statuses():
            if status.get("status") not in MANUAL_PRICE_WAITING_STATUSES:
                continue
            if supervisor_review_card_message_id and status.get("supervisor_review_card_message_id") != supervisor_review_card_message_id:
                continue
            if not supervisor_review_card_message_id and chat_id:
                chat_matches = {
                    status.get("supervisor_chat_id"),
                    status.get("business_chat_id"),
                    status.get("raw_chat_id"),
                }
                if chat_id not in chat_matches:
                    continue
            stamp = str(status.get("waiting_manual_price_at") or status.get("updated_at") or status.get("created_at") or "")
            candidates.append((stamp, task_id))
        candidates.sort()
        return [task_id for _, task_id in candidates]

    def canonical_blocking_error_codes(self, task_id: str, *, status: dict[str, Any] | None = None) -> list[str]:
        status = status or self.load_status(task_id) or {}
        sources: list[list[str]] = []
        sources.append(self._filter_blocking_error_codes(self._values_from_keys(status, ("admin_intervention_error_code",))))
        sources.append(self._filter_blocking_error_codes(self._values_from_keys(status, ("admin_intervention_error_codes",))))
        sources.append(self._filter_blocking_error_codes(self._values_from_keys(status, ("last_blocking_error_code",))))
        sources.append(self._filter_blocking_error_codes(self._values_from_keys(status, ("last_blocking_error_codes",))))
        for filename in ("first_stage_result.json", "runner_result.json", "runner_error.json"):
            sources.append(self._filter_blocking_error_codes(self._codes_from_json_file(self.task_dir(task_id) / filename)))
        sources.append(self._filter_blocking_error_codes(self._codes_from_admin_intervention_history(status, task_id)))
        sources.append(self._filter_blocking_error_codes(self._codes_from_text_file(self.task_dir(task_id) / "first_stage_stderr.log")))
        sources.append(self._filter_blocking_error_codes(self._codes_from_text_file(self.task_dir(task_id) / "first_stage_stdout.log")))
        for codes in sources:
            if codes:
                return codes
        return []

    def canonical_blocking_error_code(self, task_id: str, *, status: dict[str, Any] | None = None) -> str | None:
        codes = self.canonical_blocking_error_codes(task_id, status=status)
        return codes[0] if codes else None

    def repair_blocking_reason_fields(
        self,
        task_id: str,
        *,
        status: dict[str, Any] | None = None,
        normalize_recoverable_system_block: bool = False,
    ) -> dict[str, Any]:
        status = status or self.load_status(task_id) or {}
        codes = self.canonical_blocking_error_codes(task_id, status=status)
        if not codes:
            return status
        primary = codes[0]
        recoverable_by_health_check = is_auto_health_recoverable_error(errors=codes, result=status)
        new_status = str(status.get("status") or "")
        extra: dict[str, Any] = {
            "admin_intervention_error_code": primary,
            "admin_intervention_error_codes": codes,
            "last_blocking_error_code": primary,
            "last_blocking_error_codes": codes,
            "canonical_blocking_error_code": primary,
            "canonical_blocking_error_codes": codes,
            "recoverable_by_health_check": recoverable_by_health_check,
        }
        if normalize_recoverable_system_block and recoverable_by_health_check and new_status in {ADMIN_INTERVENTION_REQUIRED, SYSTEM_BLOCKED}:
            new_status = SYSTEM_BLOCKED
            extra.update(
                {
                    "business_status": SYSTEM_BLOCKED,
                    "technical_status": "FAILED",
                    "recommended_next_action": "wait-admin-resolution",
                }
            )
        return self._set_status(task_id, new_status, extra=extra)

    def _admin_error_codes(self, status: dict[str, Any]) -> list[str]:
        task_id = str(status.get("task_id") or "")
        if task_id:
            canonical_codes = self.canonical_blocking_error_codes(task_id, status=status)
            if canonical_codes:
                return canonical_codes
        codes = collect_error_codes(errors=list(status.get("errors") or []), result=status)
        for key in ("admin_intervention_error_code", "admin_intervention_error_codes", "last_blocking_error_code", "last_blocking_error_codes", "last_error_codes", "error_code", "error_codes"):
            values = status.get(key)
            if isinstance(values, list):
                codes.extend(str(value) for value in values if value)
            elif values:
                codes.append(str(values))
        for key in ("last_error_code", "error_code", "issue_code", "stop_code"):
            value = status.get(key)
            if value:
                codes.append(str(value))
        return self._filter_blocking_error_codes(codes)

    def _values_from_keys(self, payload: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
        values: list[str] = []
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                values.extend(str(item) for item in value if item)
            elif value:
                values.append(str(value))
        return values

    def _filter_blocking_error_codes(self, codes: list[str]) -> list[str]:
        deduped: list[str] = []
        for code in codes:
            code = str(code or "").strip()
            if not code or code in MAINTENANCE_ERROR_CODES:
                continue
            if TASK_ID_RE.match(code):
                continue
            if code and code not in deduped:
                deduped.append(code)
        return deduped

    def _codes_from_json_file(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        codes = self._codes_from_payload(payload)
        if self._payload_has_s02_brand_filter_entry_failure_evidence(payload):
            codes.insert(0, "BRAND_FILTER_STEP_NOT_ENTERED")
        return codes

    def _codes_from_admin_intervention_history(self, status: dict[str, Any], task_id: str) -> list[str]:
        codes: list[str] = []
        history = status.get("admin_intervention_history")
        if isinstance(history, list):
            codes.extend(self._codes_from_payload(history))
        for filename in ("admin_intervention_history.json", "admin_intervention_history.jsonl"):
            path = self.task_dir(task_id) / filename
            if not path.exists():
                continue
            if path.suffix == ".jsonl":
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                for line in lines:
                    try:
                        codes.extend(self._codes_from_payload(json.loads(line)))
                    except json.JSONDecodeError:
                        codes.extend(self._codes_from_text(line))
                continue
            codes.extend(self._codes_from_json_file(path))
        return codes

    def _codes_from_payload(self, payload: Any) -> list[str]:
        codes: list[str] = []
        if isinstance(payload, dict):
            for key in (
                "admin_intervention_error_code",
                "last_blocking_error_code",
                "original_error_code",
                "user_facing_error_code",
                "status",
                "final_status",
                "current_state",
                "stop_code",
                "issue_code",
                "error_code",
                "last_error_code",
            ):
                value = payload.get(key)
                if value:
                    codes.append(str(value))
            for key in (
                "error",
                "message",
                "stderr",
                "stdout",
                "adb_stderr",
                "xml_dump_stderr",
                "screenshot_error",
                "xml_dump_error",
                "swipe_stderr",
                "raw_error_summary",
                "original_error_message",
                "focused_window",
                "foreground_package",
                "resumed_activity",
            ):
                value = payload.get(key)
                if value:
                    codes.extend(self._codes_from_text(str(value)))
            exception_type = str(payload.get("exception_type") or "").strip()
            exception_message = str(payload.get("exception_message") or payload.get("exception") or "").strip()
            traceback_text = str(payload.get("traceback") or payload.get("traceback_tail") or "").strip()
            exception_blob = "\n".join(part for part in (exception_type, exception_message, traceback_text) if part)
            if exception_type or traceback_text:
                codes.append("SECOND_STAGE_RUNTIME_EXCEPTION")
            if "_reference_identity_summary" in exception_blob and "TypeError" in exception_blob:
                codes.append("V149_REFERENCE_IDENTITY_SUMMARY_TYPE_ERROR")
            for key in (
                "errors",
                "error_codes",
                "admin_intervention_error_codes",
                "last_blocking_error_codes",
                "last_error_codes",
                "original_error_codes",
            ):
                value = payload.get(key)
                if isinstance(value, list):
                    codes.extend(str(item) for item in value if item)
                elif value:
                    codes.append(str(value))
            for value in payload.values():
                if isinstance(value, (dict, list)):
                    codes.extend(self._codes_from_payload(value))
        elif isinstance(payload, list):
            for item in payload:
                codes.extend(self._codes_from_payload(item))
        elif isinstance(payload, str):
            codes.extend(self._codes_from_text(payload))
        return self._filter_blocking_error_codes(codes)

    def _payload_has_s02_brand_filter_entry_failure_evidence(self, payload: Any) -> bool:
        codes = set(self._codes_from_payload(payload))
        if not (codes & S02_BRAND_FILTER_ENTRY_FAILURE_CODES):
            return False
        text_blob = "\n".join(self._payload_text_evidence_values(payload))
        if "选车" not in text_blob:
            return False
        filter_hits = sum(1 for marker in S02_BRAND_FILTER_MARKERS if marker in text_blob)
        if filter_hits < 4:
            return False
        vehicle_list_visible = any(marker in text_blob for marker in S02_BRAND_LIST_MARKERS) or bool(re.search(r"20\d{2}年\s*\|", text_blob))
        return vehicle_list_visible

    def _payload_text_evidence_values(self, payload: Any) -> list[str]:
        values: list[str] = []
        if isinstance(payload, dict):
            for key in (
                "visible_text_digest",
                "visible_texts",
                "visible_blob",
                "fresh_xml",
                "xml",
                "error",
                "message",
                "raw_error_summary",
                "stdout",
                "stderr",
            ):
                value = payload.get(key)
                if isinstance(value, list):
                    values.extend(str(item) for item in value if item)
                elif value:
                    values.append(str(value))
            for value in payload.values():
                if isinstance(value, (dict, list)):
                    values.extend(self._payload_text_evidence_values(value))
        elif isinstance(payload, list):
            for item in payload:
                values.extend(self._payload_text_evidence_values(item))
        elif isinstance(payload, str):
            values.append(payload)
        return values

    def _codes_from_text_file(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        try:
            return self._codes_from_text(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return []

    def _codes_from_text(self, text: str) -> list[str]:
        text = text or ""
        candidates = re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text)
        lowered = text.lower()
        if (
            re.search(r"\bdevice\s+['\"][^'\"]+['\"]\s+not\s+found\b", lowered)
            or "no devices/emulators found" in lowered
            or "more than one device/emulator" in lowered
            or "target device not found" in lowered
            or "specified device not found" in lowered
        ):
            candidates.append("TARGET_ADB_DEVICE_NOT_CONNECTED")
        if "securityexception" in lowered and ("inject_events" in lowered or "permission denial" in lowered):
            candidates.append("ADB_INPUT_PERMISSION_DENIED")
        if "usb" in lowered and "security" in lowered:
            candidates.append("USB_DEBUG_SECURITY_DISABLED")
        if "adb_vendor_keys" in text or "unauthorized" in lowered:
            candidates.append("ADB_UNAUTHORIZED")
        if "runtime_fresh_evidence_missing" in lowered or "fresh runtime evidence is incomplete" in lowered:
            candidates.append("RUNTIME_FRESH_EVIDENCE_MISSING")
        if "xml_dump_failed" in lowered or "xml dump failed" in lowered or "xml_dump_error" in lowered:
            candidates.append("XML_DUMP_FAILED")
        if "guazi_foreground_evidence_missing_after_3_retries" in lowered:
            candidates.append("GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES")
        if "guazi_splash_page_stuck_after_3_retries" in lowered:
            candidates.append("GUAZI_SPLASH_PAGE_STUCK_AFTER_3_RETRIES")
        if "app_not_foreground_after_3_retries" in lowered:
            candidates.append("APP_NOT_FOREGROUND_AFTER_3_RETRIES")
        if "notificationshade" in text or "keyguard" in lowered:
            candidates.append("PHONE_NOT_AWAKE")
        if "package not found" in lowered or "not installed" in lowered or "can't find service" in lowered:
            candidates.append("APP_PACKAGE_NOT_FOUND")
        if "com.android.settings" in text or "launcher" in lowered:
            candidates.append("APP_NOT_FOREGROUND")
        if "popup" in lowered or "dialog" in lowered or "弹窗" in text:
            candidates.append("POPUP_BLOCKED")
        return self._filter_blocking_error_codes(candidates)

    def dispatch_next_queued_task_dry_run(self) -> TaskOperationResult:
        active_task_id = self.active_app_task()
        if active_task_id:
            return TaskOperationResult(
                success=False,
                action="dispatch_next_queued_task_dry_run",
                task_id=active_task_id,
                status=self.load_status(active_task_id).get("status") if self.load_status(active_task_id) else None,
                changed=False,
                reply_text="当前已有任务占用 APP 控制锁，其他任务继续排队。",
            )
        queued = self.queued_tasks()
        if not queued:
            return TaskOperationResult(
                success=False,
                action="dispatch_next_queued_task_dry_run",
                changed=False,
                reply_text="当前没有排队中的任务。",
            )
        task_id = queued[0]
        draft = self.load_draft(task_id) or {}
        build_result = build_current_target_task(draft, clock=self.clock)
        if not build_result.valid:
            status = self.load_status(task_id) or {}
            feedback = self._mark_target_info_correction(
                task_id,
                status,
                draft=draft,
                errors=["MISSING_REQUIRED_FIELDS"],
                missing_fields=build_result.missing_fields,
            )
            return TaskOperationResult(
                success=False,
                action="dispatch_next_queued_task_dry_run",
                task_id=task_id,
                status=TARGET_INFO_NEEDS_CORRECTION,
                changed=True,
                data=feedback,
                reply_text=feedback["reply_text"],
            )
        self._write_json(self.current_target_task_path, build_result.current_target_task)
        self._write_json(self.task_dir(task_id) / "current_target_task.snapshot.json", build_result.current_target_task)
        started_at = _isoformat(self.clock())
        updated = self._set_status(
            task_id,
            "APP_CONTROL_LOCKED",
            extra={"app_control_locked_at": started_at, "dispatcher_dry_run": True},
        )
        self._append_audit(action="dispatch_next_queued_task_dry_run", task_id=task_id, status="APP_CONTROL_LOCKED", success=True)
        return TaskOperationResult(
            success=True,
            action="dispatch_next_queued_task_dry_run",
            task_id=task_id,
            status="APP_CONTROL_LOCKED",
            changed=True,
            data={"status": updated, "current_target_task": build_result.current_target_task},
            reply_text="dispatcher dry-run 已取出队首任务，未启动 APP。",
        )

    def mark_waiting_manual_price(
        self,
        task_id: str,
        *,
        supervisor_chat_id: str | None,
        supervisor_review_card_message_id: str | None = None,
    ) -> TaskOperationResult:
        status = self.load_status(task_id)
        if not status:
            return self._unknown_task("mark_waiting_manual_price", task_id)
        card_id = supervisor_review_card_message_id or f"supervisor_review_card:{task_id}"
        now = _isoformat(self.clock())
        updated = self._set_status(
            task_id,
            "WAITING_MANUAL_PRICE",
            extra={
                "technical_status": "SUCCEEDED",
                "business_status": "NEEDS_REVIEW",
                "recommended_next_action": "manual-review",
                "supervisor_chat_id": supervisor_chat_id,
                "supervisor_review_card_message_id": card_id,
                "waiting_manual_price_at": now,
                "manual_review_required": True,
                "waiting_manual_price": True,
            },
        )
        current_target_cleared = self._clear_current_target_task_if_matches(task_id)
        if current_target_cleared:
            updated["current_target_task_cleared"] = True
            self._write_json(self.task_dir(task_id) / "status.json", updated)
            self._upsert_index(updated)
        return TaskOperationResult(
            success=True,
            action="mark_waiting_manual_price",
            task_id=task_id,
            status="WAITING_MANUAL_PRICE",
            changed=True,
            data=updated,
            reply_text="该任务需要主管人工复核，已同步到主管复核群。",
        )

    def load_manual_review_delivery(self, task_id: str) -> dict[str, Any] | None:
        path = self.task_dir(task_id) / MANUAL_REVIEW_FEEDBACK_DELIVERY
        if not path.exists():
            return None
        return self._read_json(path, default=None)

    def manual_review_notice_sent(self, task_id: str) -> bool:
        delivery = self.load_manual_review_delivery(task_id)
        if isinstance(delivery, dict) and delivery.get("send_success"):
            return True
        status = self.load_status(task_id) or {}
        return bool(status.get("manual_review_business_notice_sent") and status.get("manual_review_supervisor_notice_sent"))

    def manual_review_notice_pending_tasks(self) -> list[str]:
        candidates: list[tuple[str, str]] = []
        for task_id, status in self._iter_statuses():
            if status.get("status") not in MANUAL_PRICE_WAITING_STATUSES and not status.get("waiting_manual_price"):
                continue
            if self.manual_review_notice_sent(task_id):
                continue
            stamp = str(status.get("waiting_manual_price_at") or status.get("updated_at") or status.get("created_at") or "")
            candidates.append((stamp, task_id))
        candidates.sort(reverse=True)
        return [task_id for _, task_id in candidates]

    def write_manual_review_delivery(self, task_id: str, delivery: dict[str, Any]) -> dict[str, Any]:
        self._write_json(self.task_dir(task_id) / MANUAL_REVIEW_FEEDBACK_DELIVERY, delivery)
        return delivery

    def record_manual_review_delivery_status(
        self,
        task_id: str,
        *,
        delivery: dict[str, Any],
        reason_business: str,
        reason_code: str,
    ) -> dict[str, Any]:
        status = self.load_status(task_id) or {}
        previous_count = int(status.get("manual_review_delivery_count") or 0)
        send_success = bool(delivery.get("send_success"))
        delivery_errors = delivery.get("error")
        fields: dict[str, Any] = {
            "manual_review_required": True,
            "waiting_manual_price": True,
            "manual_review_reason_business": reason_business,
            "manual_review_reason_code": reason_code,
            "manual_review_business_notice_sent": send_success,
            "manual_review_supervisor_notice_sent": send_success,
            "manual_review_delivery_count": previous_count + 1,
            "manual_review_idempotency_key": delivery.get("idempotency_key"),
        }
        if send_success:
            fields["manual_review_notice_sent_at"] = delivery.get("sent_at")
            fields["manual_review_delivery_error"] = ""
        else:
            fields["manual_review_delivery_error"] = delivery_errors or "MANUAL_REVIEW_NOTICE_SEND_FAILED"
        return self.update_task_status_fields(task_id, fields=fields)

    def update_task_status_fields(self, task_id: str, *, fields: dict[str, Any]) -> dict[str, Any]:
        status = self.load_status(task_id)
        if not status:
            raise FileNotFoundError(f"task status not found: {task_id}")
        return self._set_status(task_id, str(status.get("status") or ""), extra=fields)

    def _mark_target_info_correction(
        self,
        task_id: str,
        status: dict[str, Any],
        *,
        draft: dict[str, Any],
        errors: list[str],
        missing_fields: list[str],
        result: dict[str, Any] | None = None,
        validation_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fields = target_info_status_fields(clock=self.clock)
        updated_status = self._set_status(
            task_id,
            TARGET_INFO_NEEDS_CORRECTION,
            extra={key: value for key, value in fields.items() if key != "status"},
        )
        feedback = write_target_info_correction_feedback(
            task_dir=self.task_dir(task_id),
            task_id=task_id,
            status_payload={**status, **updated_status},
            draft=draft,
            errors=errors,
            missing_fields=missing_fields,
            result=result,
            validation_result=validation_result,
            dry_run=True,
            clock=self.clock,
        )
        self._append_audit(
            action="target_info_correction_required",
            task_id=task_id,
            status=TARGET_INFO_NEEDS_CORRECTION,
            success=False,
        )
        return {
            **feedback,
            "status": TARGET_INFO_NEEDS_CORRECTION,
            "business_status": TARGET_INFO_NEEDS_CORRECTION,
            "technical_status": "VALIDATION_FAILED",
            "recommended_next_action": "ask-sender-to-resend-target-info",
        }

    def manual_price_waiting_tasks(self, *, supervisor_chat_id: str | None = None) -> list[str]:
        candidates: list[tuple[str, str]] = []
        for task_id, status in self._iter_statuses():
            if status.get("status") not in MANUAL_PRICE_WAITING_STATUSES:
                continue
            if supervisor_chat_id and status.get("supervisor_chat_id") not in {supervisor_chat_id, None}:
                continue
            stamp = str(status.get("waiting_manual_price_at") or status.get("updated_at") or status.get("created_at") or "")
            candidates.append((stamp, task_id))
        candidates.sort()
        return [task_id for _, task_id in candidates]

    def find_task_by_supervisor_review_card_message_id(
        self,
        supervisor_review_card_message_id: str,
        *,
        supervisor_chat_id: str | None = None,
    ) -> str | None:
        for task_id, status in self._iter_statuses():
            if status.get("supervisor_review_card_message_id") != supervisor_review_card_message_id:
                continue
            if supervisor_chat_id and status.get("supervisor_chat_id") not in {supervisor_chat_id, None}:
                continue
            return task_id
        return None

    def _iter_statuses(self) -> list[tuple[str, dict[str, Any]]]:
        if not self.base_dir.exists():
            return []
        rows: list[tuple[str, dict[str, Any]]] = []
        for child in self.base_dir.iterdir():
            if not child.is_dir():
                continue
            status = self.load_status(child.name)
            if isinstance(status, dict):
                rows.append((child.name, status))
        return rows

    def _set_status(self, task_id: str, new_status: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        if new_status not in PHASE1_STATUSES and new_status not in RESERVED_STATUSES:
            raise ValueError(f"Task cannot enter status: {new_status}")
        status_path = self.task_dir(task_id) / "status.json"
        status = self._read_json(status_path, default={})
        if new_status:
            status["status"] = new_status
        status["updated_at"] = _isoformat(self.clock())
        if extra:
            status.update({key: value for key, value in extra.items() if value is not None})
        self._write_json(status_path, status)

        draft_path = self.task_dir(task_id) / "target_task_draft.json"
        if draft_path.exists():
            draft = self._read_json(draft_path, default={})
            if new_status:
                draft["status"] = new_status
            if extra:
                draft.update({key: value for key, value in extra.items() if value is not None})
            self._write_json(draft_path, draft)
        self._upsert_index(status)
        return status

    def _upsert_index(self, status: dict[str, Any]) -> None:
        index = self._read_json(self.task_index_path, default={})
        task_id = status["task_id"]
        index[task_id] = {
            "task_id": task_id,
            "status": status.get("status"),
            "raw_message_id": status.get("raw_message_id"),
            "business_chat_id": status.get("business_chat_id") or status.get("raw_chat_id"),
            "sender_open_id": status.get("sender_open_id") or status.get("raw_sender_id"),
            "source_message_id": status.get("source_message_id") or status.get("raw_message_id"),
            "confirm_card_message_id": status.get("confirm_card_message_id"),
            "supervisor_chat_id": status.get("supervisor_chat_id"),
            "supervisor_review_card_message_id": status.get("supervisor_review_card_message_id"),
            "queued_at": status.get("queued_at"),
            "confirmed_at": status.get("confirmed_at"),
            "created_at": status.get("created_at"),
            "updated_at": status.get("updated_at"),
        }
        self._write_json(self.task_index_path, index)

    def _unknown_task(self, action: str, task_id: str) -> TaskOperationResult:
        return TaskOperationResult(
            success=False,
            action=action,
            task_id=task_id,
            changed=False,
            reply_text=f"未找到任务：{task_id}\n\n请检查 task_id 是否正确。",
        )

    def _read_json(self, path: Path, *, default: Any) -> Any:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _parse_datetime(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _append_audit(
        self,
        *,
        action: str,
        task_id: str | None,
        status: str | None,
        success: bool,
        raw_message_id: str | None = None,
    ) -> None:
        payload = {
            "action": action,
            "task_id": task_id,
            "status": status,
            "success": success,
            "raw_message_id": raw_message_id,
            "created_at": _isoformat(self.clock()),
        }
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
