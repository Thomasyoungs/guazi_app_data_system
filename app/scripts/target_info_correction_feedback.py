"""Target-info correction classification and Feishu feedback previews.

This module is deliberately limited to local status/preview artifacts. It does
not send Feishu messages or touch APP automation.
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


TARGET_INFO_VALIDATION_FAILED = "TARGET_INFO_VALIDATION_FAILED"
TARGET_INFO_NEEDS_CORRECTION = "TARGET_INFO_NEEDS_CORRECTION"
WAITING_TARGET_INFO_CORRECTION = "WAITING_TARGET_INFO_CORRECTION"
TARGET_DATE_UNRECOGNIZED = "TARGET_DATE_UNRECOGNIZED"
TARGET_MODEL_UNRECOGNIZED = "TARGET_MODEL_UNRECOGNIZED"
TARGET_MODEL_TEMPLATE_INCOMPLETE = "TARGET_MODEL_TEMPLATE_INCOMPLETE"
TARGET_BRAND_SERIES_INFERENCE_FAILED = "TARGET_BRAND_SERIES_INFERENCE_FAILED"
TARGET_BRAND_SERIES_CONFLICT = "TARGET_BRAND_SERIES_CONFLICT"
TARGET_REQUIRED_FIELD_MISSING = "TARGET_REQUIRED_FIELD_MISSING"
TARGET_FIELD_FORMAT_INVALID = "TARGET_FIELD_FORMAT_INVALID"
CONFIG_MISMATCH_HARD_STOP = "CONFIG_MISMATCH_HARD_STOP"
CONFIG_TIER_MISMATCH = "CONFIG_TIER_MISMATCH"
POWERTRAIN_TOKEN_MISMATCH = "POWERTRAIN_TOKEN_MISMATCH"

TARGET_INFO_ERROR_CODES = {
    TARGET_INFO_VALIDATION_FAILED,
    TARGET_INFO_NEEDS_CORRECTION,
    WAITING_TARGET_INFO_CORRECTION,
    TARGET_DATE_UNRECOGNIZED,
    TARGET_MODEL_UNRECOGNIZED,
    TARGET_BRAND_SERIES_INFERENCE_FAILED,
    TARGET_BRAND_SERIES_CONFLICT,
    TARGET_REQUIRED_FIELD_MISSING,
    TARGET_FIELD_FORMAT_INVALID,
    "TARGET_TASK_FIELD_MISSING",
    "MISSING_REQUIRED_FIELDS",
    "REGISTRATION_DATE_UNRECOGNIZED",
    "MODEL_BRAND_SERIES_UNRESOLVED",
    "MODEL_STRICT_TEMPLATE_INCOMPLETE",
    "MODEL_BRAND_SERIES_AMBIGUOUS",
    "MODEL_BRAND_SERIES_CONFLICT",
    CONFIG_MISMATCH_HARD_STOP,
    CONFIG_TIER_MISMATCH,
    POWERTRAIN_TOKEN_MISMATCH,
}

APP_OR_ENVIRONMENT_ERROR_CODES = {
    "TARGET_ADB_SERIAL_NOT_CONFIGURED",
    "TARGET_ADB_DEVICE_NOT_CONNECTED",
    "TARGET_ADB_DEVICE_UNAUTHORIZED",
    "TARGET_ADB_DEVICE_OFFLINE",
    "ADB_UNAUTHORIZED",
    "ADB_DEVICE_UNAUTHORIZED",
    "LOGIN_REQUIRED_MANUAL",
    "HUMAN_LOGIN_REQUIRED",
    "S_LOGIN_LATER_NO_PROGRESS",
    "DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS",
    "DESKTOP_UPGRADE_MODAL_DISMISS_FAILED",
    "FIRST_STAGE_NOT_S10_READY",
    "FIRST_STAGE_TARGET_NOT_FOUND",
    "FIRST_STAGE_SCHEMA_INVALID",
    "PAGE_CONTRACT_MISMATCH",
    "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED",
    "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED",
    "S14_STALE_FIRST_LINE_BINDING_UNRESOLVED",
    "S14_CONTRACT_DEGRADED_NEEDS_REVIEW",
    "S14_DEGRADED_COLLECTION_THRESHOLD_EXCEEDED",
    "S14_DETAIL_POPUP_CLOSE_UNSAFE_OR_FAILED",
}

INTERNAL_FEEDBACK_FORBIDDEN_TERMS = (
    "PowerShell",
    "adb",
    "uiautomator",
    "--run-first-stage",
    "--run-second-stage",
    "--revalidate-result",
    "--manual-confirm-price",
    "runner_result",
    "status.json",
    "current_target_task.json",
    "run_id",
    "generation_id",
    "STALE_RUN_RESULT_IGNORED",
)

FIELD_DISPLAY_NAMES = {
    "brand": "品牌",
    "series": "车系",
    "model_config": "车型",
    "config_model": "车型配置",
    "year_model": "年款",
    "license_date": "上牌日期",
    "register_date": "上牌日期",
    "registration_date": "上牌日期",
    "register_year": "上牌年份",
    "registration_date_year": "上牌年份",
    "mileage_text": "表显里程",
    "mileage_10k_km": "表显里程",
    "color": "车辆颜色",
    "transfer_count_text": "过户次数",
    "transfer_count": "过户次数",
    "condition_text": "具体车况",
}


def now_iso(clock: Callable[[], datetime] | None = None) -> str:
    now = clock() if clock else datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.isoformat(timespec="seconds")
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def is_target_info_error(
    *,
    errors: list[str] | None = None,
    missing_fields: list[str] | None = None,
    result: dict[str, Any] | None = None,
) -> bool:
    """Return True only for pre-APP target input failures."""
    codes = _collect_codes(errors=errors, result=result)
    if codes & APP_OR_ENVIRONMENT_ERROR_CODES:
        return False
    if missing_fields:
        return True
    if codes & TARGET_INFO_ERROR_CODES:
        return True
    if result and _target_task_field_missing(result):
        return True
    return False


def classify_target_info_errors(
    *,
    errors: list[str] | None = None,
    missing_fields: list[str] | None = None,
    result: dict[str, Any] | None = None,
    draft: dict[str, Any] | None = None,
    validation_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_errors = _dedupe([str(item) for item in (errors or []) if item])
    raw_missing = _dedupe([str(item) for item in (missing_fields or []) if item])
    if result:
        raw_errors.extend(_result_status_codes(result))
        raw_missing.extend(_result_missing_fields(result))
    if validation_result:
        raw_errors.extend(str(item) for item in validation_result.get("date_errors") or [])
        raw_errors.extend(str(item) for item in validation_result.get("model_resolution_errors") or [])
        raw_missing.extend(str(item) for item in validation_result.get("missing_required_fields") or [])
    raw_errors = _dedupe(raw_errors)
    raw_missing = _dedupe(raw_missing)

    codes: list[str] = []
    reasons: list[str] = []
    missing_labels = [_display_field_name(field) for field in raw_missing]

    if raw_missing:
        codes.append(TARGET_REQUIRED_FIELD_MISSING)
        reasons.append("目标车信息缺少必填字段。")

    date_value = _first_present_value(draft or {}, ("license_date_raw", "license_date", "register_date", "registration_date"))
    if _has_any(raw_errors, {"REGISTRATION_DATE_UNRECOGNIZED", TARGET_DATE_UNRECOGNIZED}) or "registration_date_year" in raw_missing:
        codes.append(TARGET_DATE_UNRECOGNIZED)
        detail = f"上牌日期无法识别：{date_value}" if date_value else "上牌日期无法识别。"
        reasons.append(detail)

    if _has_any(raw_errors, {"MODEL_STRICT_TEMPLATE_INCOMPLETE", TARGET_MODEL_TEMPLATE_INCOMPLETE}):
        codes.append(TARGET_MODEL_TEMPLATE_INCOMPLETE)
        reasons.append("车型字段格式不完整。")

    if _has_any(raw_errors, {"MODEL_BRAND_SERIES_UNRESOLVED", TARGET_BRAND_SERIES_INFERENCE_FAILED}):
        codes.append(TARGET_BRAND_SERIES_INFERENCE_FAILED)
        reasons.append("车型字段无法确定品牌/车系。")

    if _has_any(raw_errors, {"MODEL_BRAND_SERIES_AMBIGUOUS", TARGET_MODEL_UNRECOGNIZED}):
        codes.append(TARGET_MODEL_UNRECOGNIZED)
        reasons.append("车型字段无法唯一识别。")

    if _has_any(raw_errors, {"MODEL_BRAND_SERIES_CONFLICT", TARGET_BRAND_SERIES_CONFLICT}):
        codes.append(TARGET_BRAND_SERIES_CONFLICT)
        reasons.append("填写的品牌/车系与车型识别结果不一致。")

    if _has_any(raw_errors, {CONFIG_MISMATCH_HARD_STOP, CONFIG_TIER_MISMATCH}):
        codes.append(CONFIG_MISMATCH_HARD_STOP)
        reasons.append("车型配置无法确认一致，目标车与参考车存在配置等级差异。")

    if _has_any(raw_errors, {POWERTRAIN_TOKEN_MISMATCH}):
        codes.append(CONFIG_MISMATCH_HARD_STOP)
        reasons.append("车型动力配置无法确认一致，目标车与参考车存在动力差异。")

    if _has_any(raw_errors, {"TARGET_TASK_FIELD_MISSING", "MISSING_REQUIRED_FIELDS"}):
        codes.append(TARGET_INFO_VALIDATION_FAILED)
        if not reasons:
            reasons.append("目标车字段不完整，暂时不能进入定价队列。")

    if not codes and is_target_info_error(errors=raw_errors, missing_fields=raw_missing, result=result):
        codes.append(TARGET_INFO_VALIDATION_FAILED)
        reasons.append("目标车信息格式需要检查。")

    return {
        "is_target_info_error": bool(codes or raw_missing),
        "codes": _dedupe(codes),
        "raw_errors": raw_errors,
        "missing_fields": raw_missing,
        "missing_field_labels": _dedupe(missing_labels),
        "reasons": _dedupe(reasons),
    }


def format_target_info_correction_reply(
    *,
    task_id: str,
    classification: dict[str, Any],
    draft: dict[str, Any] | None = None,
) -> str:
    reasons = list(classification.get("reasons") or [])
    missing_labels = list(classification.get("missing_field_labels") or [])
    lines = [
        f"【目标车信息需修改】{task_id}",
        "",
        "这台车暂时不能进入定价队列。",
        "",
    ]
    if reasons:
        lines.append("原因是：")
        lines.append("")
        lines.extend(f"* {reason}" for reason in reasons)
        lines.append("")
    if missing_labels:
        lines.append("缺少以下信息：")
        lines.append("")
        lines.extend(f"* {field}" for field in missing_labels)
        lines.append("")

    recognized_lines = _recognized_field_lines(draft or {})
    if recognized_lines:
        lines.append("已识别：")
        lines.append("")
        lines.extend(recognized_lines)
        lines.append("")

    lines.extend(_example_lines(classification, draft or {}))
    lines.extend(
        [
            "",
            "修改后请重新发送整条目标车源信息，我会重新生成任务并排队。",
        ]
    )
    text = "\n".join(lines).strip()
    return _strip_forbidden_terms(text)


def write_target_info_correction_feedback(
    *,
    task_dir: str | Path,
    task_id: str,
    status_payload: dict[str, Any] | None = None,
    draft: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    missing_fields: list[str] | None = None,
    result: dict[str, Any] | None = None,
    validation_result: dict[str, Any] | None = None,
    dry_run: bool = True,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    task_dir = Path(task_dir)
    status_payload = status_payload or {}
    draft = draft or {}
    classification = classify_target_info_errors(
        errors=errors,
        missing_fields=missing_fields,
        result=result,
        draft=draft,
        validation_result=validation_result,
    )
    reply_text = format_target_info_correction_reply(
        task_id=task_id,
        classification=classification,
        draft=draft,
    )
    business_chat_id = _first_present_value(
        {**draft, **status_payload},
        ("business_chat_id", "raw_chat_id", "chat_id"),
    )
    sender_open_id = _first_present_value(
        {**draft, **status_payload},
        ("sender_open_id", "raw_sender_id", "open_id"),
    )
    delivery = {
        "ok": True,
        "dry_run": dry_run,
        "task_id": task_id,
        "business_chat_id": business_chat_id,
        "sender_open_id": sender_open_id,
        "source_message_id": _first_present_value({**draft, **status_payload}, ("source_message_id", "raw_message_id")),
        "target_status": TARGET_INFO_NEEDS_CORRECTION,
        "recommended_next_action": "ask-sender-to-resend-target-info",
        "classification": classification,
        "reply_text": reply_text,
        "reply_payload": build_text_message_payload(reply_text),
        "created_at": now_iso(clock),
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "target_info_correction_reply.preview.txt").write_text(reply_text + "\n", encoding="utf-8")
    (task_dir / "target_info_correction_delivery.json").write_text(
        json.dumps(delivery, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return delivery


def target_info_status_fields(*, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    return {
        "status": TARGET_INFO_NEEDS_CORRECTION,
        "business_status": TARGET_INFO_NEEDS_CORRECTION,
        "technical_status": "VALIDATION_FAILED",
        "recommended_next_action": "ask-sender-to-resend-target-info",
        "target_info_correction_status": WAITING_TARGET_INFO_CORRECTION,
        "updated_at": now_iso(clock),
    }


def _example_lines(classification: dict[str, Any], draft: dict[str, Any]) -> list[str]:
    codes = set(classification.get("codes") or [])
    if TARGET_MODEL_TEMPLATE_INCOMPLETE in codes:
        return [
            "请按以下格式填写：",
            "",
            "【车型】 品牌 车系 年款 配置",
            "",
            "例如：",
            "",
            "【车型】 别克 君越 2021款 652T 豪华型",
        ]
    if TARGET_BRAND_SERIES_INFERENCE_FAILED in codes or TARGET_MODEL_UNRECOGNIZED in codes:
        return [
            "请补充完整车型名称，例如：",
            "",
            "【车型】雪佛兰 科鲁泽 2022款 320自动悦享天窗版",
        ]
    if TARGET_DATE_UNRECOGNIZED in codes:
        raw = _first_present_value(draft, ("license_date_raw", "license_date", "register_date", "registration_date"))
        head = [f"上牌日期原始值：{raw}", ""] if raw else []
        return head + [
            "请按以下格式重新发送完整目标车源信息：",
            "",
            "【车型】2022款科鲁泽320自动悦享天窗版（1.5L四缸）",
            "【上牌日期】2022.08",
            "【表显里程】1.05",
            "【车辆颜色】红",
            "【过户次数】0",
            "【具体车况】原漆，右后叶小坑掉漆",
        ]
    if CONFIG_MISMATCH_HARD_STOP in codes:
        return [
            "为避免错误定价，请重新发送完整车型配置，例如：",
            "",
            "【车型】2018款 改款 330TSI DSG 豪华型",
            "【上牌日期】2022.08",
            "【表显里程】1.05",
            "【车辆颜色】黑",
            "【过户次数】0",
            "【具体车况】请按实际填写",
        ]
    return [
        "请补齐后重新发送完整目标车源信息，例如：",
        "",
        "【车型】2022款科鲁泽320自动悦享天窗版（1.5L四缸）",
        "【上牌日期】2022.08",
        "【表显里程】1.05",
        "【车辆颜色】红",
        "【过户次数】0",
        "【具体车况】原漆，右后叶小坑掉漆",
    ]


def _recognized_field_lines(draft: dict[str, Any]) -> list[str]:
    field_pairs = (
        ("raw_model_text", "车型"),
        ("model_config", "车型"),
        ("brand", "品牌"),
        ("series", "车系"),
        ("year_model", "年款"),
        ("config_model", "配置"),
        ("license_date", "上牌日期"),
        ("mileage_10k_km", "表显里程"),
        ("mileage_text", "表显里程"),
        ("color", "颜色"),
        ("transfer_count", "过户次数"),
        ("transfer_count_text", "过户次数"),
        ("condition_text", "车况"),
        ("city", "城市"),
        ("emission_standard", "排放标准"),
        ("guide_price_wan", "指导价"),
        ("guide_price_text", "指导价"),
        ("insurance_expire_date", "保险到期"),
        ("insurance_expiry", "保险到期"),
        ("sunroof", "有无天窗"),
        ("sunroof_text", "有无天窗"),
    )
    lines: list[str] = []
    used_labels: set[str] = set()
    for field, label in field_pairs:
        if label in used_labels:
            continue
        value = draft.get(field)
        if value is None or value == "":
            continue
        lines.append(f"* {label}：{value}")
        used_labels.add(label)
    return lines


def _collect_codes(*, errors: list[str] | None = None, result: dict[str, Any] | None = None) -> set[str]:
    codes = set(str(item) for item in (errors or []) if item)
    if result:
        codes.update(_result_status_codes(result))
    return {code for code in codes if code}


def _target_task_field_missing(result: dict[str, Any]) -> bool:
    return bool(set(_result_status_codes(result)) & {"TARGET_TASK_FIELD_MISSING", TARGET_REQUIRED_FIELD_MISSING})


def _result_status_codes(result: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for key in ("status", "final_status", "current_state", "stop_code", "issue_code", "error_code"):
        value = result.get(key)
        if value:
            codes.append(str(value))
    errors = result.get("errors")
    if isinstance(errors, list):
        codes.extend(str(item) for item in errors if item)
    return _dedupe(codes)


def _result_missing_fields(result: dict[str, Any]) -> list[str]:
    value = result.get("missing_fields")
    if isinstance(value, list):
        return _dedupe([str(item) for item in value if item])
    return []


def _has_any(values: list[str], candidates: set[str]) -> bool:
    return bool(set(values) & candidates)


def _display_field_name(field: str) -> str:
    text = str(field or "").strip()
    return FIELD_DISPLAY_NAMES.get(text, text)


def _first_present_value(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _strip_forbidden_terms(text: str) -> str:
    cleaned = text
    for term in INTERNAL_FEEDBACK_FORBIDDEN_TERMS:
        cleaned = cleaned.replace(term, "")
    return cleaned


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
