"""Validate staged APP automation result files for the controlled runner."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FIRST_STAGE_READY_STATUSES = {
    "S10_READY",
    "S10_READY_DONE",
    "FIRST_STAGE_READY",
}

FIRST_STAGE_TARGET_NOT_FOUND_STATUSES = {
    "S03_TARGET_INITIAL_LETTER_NOT_DERIVABLE",
    "S03_TARGET_INITIAL_LETTER_NOT_FOUND",
    "S03_TARGET_BRAND_NOT_FOUND",
    "S03_TARGET_BRAND_CLICK_FAILED",
    "S03_TARGET_BRAND_PANEL_NOT_READY",
}

FIRST_STAGE_TARGET_CONFIG_FAILURE_STATUSES = {
    "S05_TARGET_CONFIG_NOT_FOUND",
    "S05_TARGET_CONFIG_VISIBLE_BUT_NOT_MATCHED",
    "S05_TARGET_CONFIG_CLICK_FAILED",
    "S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED",
}

FIRST_STAGE_TARGET_INFO_FAILURE_STATUSES = {
    "TARGET_TASK_FIELD_MISSING",
    "TARGET_REQUIRED_FIELD_MISSING",
    "TARGET_DATE_UNRECOGNIZED",
    "TARGET_MODEL_UNRECOGNIZED",
}

FIRST_STAGE_PASSTHROUGH_FAILURE_STATUSES = {
    "DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS",
    "DESKTOP_UPGRADE_MODAL_DISMISS_FAILED",
    "HUMAN_LOGIN_REQUIRED",
    "LOGIN_REQUIRED_MANUAL",
    "APP_LOGIN_REQUIRED",
    "TARGET_ADB_SERIAL_NOT_CONFIGURED",
    "TARGET_ADB_DEVICE_NOT_CONNECTED",
    "TARGET_ADB_DEVICE_UNAUTHORIZED",
    "TARGET_ADB_DEVICE_OFFLINE",
    "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT",
    "ADB_UNAUTHORIZED",
    "DEVICE_OFFLINE",
    "DEVICE_AUTH_REQUIRED",
    "APP_NOT_INSTALLED",
    "APP_NO_RESPONSE",
    "PHONE_LOCKED",
    "S_LOGIN_LATER_NO_PROGRESS",
}


def validate_first_stage_payload(payload: dict[str, Any]) -> list[str]:
    target_info_errors = _target_info_failure_statuses(payload)
    if target_info_errors:
        return target_info_errors
    if _status_is_target_not_found(payload):
        return ["FIRST_STAGE_TARGET_NOT_FOUND"]
    config_errors = _target_config_failure_statuses(payload)
    if config_errors:
        return config_errors
    passthrough_errors = _passthrough_failure_statuses(payload)
    if passthrough_errors:
        return passthrough_errors
    if first_stage_s10_ready(payload):
        return []
    if _contains_false_s10_ready(payload):
        return ["FIRST_STAGE_NOT_S10_READY"]
    if not _has_first_stage_signal(payload):
        return ["FIRST_STAGE_SCHEMA_INVALID"]
    return ["FIRST_STAGE_NOT_S10_READY"]


def first_stage_s10_ready(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if _truthy(payload.get("S10_READY")):
        return True
    if _truthy(_nested_get(payload, ("flow_state", "S10_READY"))):
        return True
    status_values = {
        str(payload.get("status") or ""),
        str(payload.get("final_status") or ""),
    }
    return bool(status_values & FIRST_STAGE_READY_STATUSES)


def result_file_is_stale(path: str | Path, run_started_at: datetime | str) -> bool:
    threshold = _coerce_datetime(run_started_at)
    modified = datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc)
    return modified < threshold


def _status_is_target_not_found(payload: dict[str, Any]) -> bool:
    return bool(_status_values(payload) & FIRST_STAGE_TARGET_NOT_FOUND_STATUSES)


def _target_info_failure_statuses(payload: dict[str, Any]) -> list[str]:
    values = _status_values(payload)
    errors = payload.get("errors")
    if isinstance(errors, list):
        values.update(str(item) for item in errors if item)
    return sorted(values & FIRST_STAGE_TARGET_INFO_FAILURE_STATUSES)


def _target_config_failure_statuses(payload: dict[str, Any]) -> list[str]:
    values = _status_values(payload)
    current_state = payload.get("current_state")
    if current_state:
        values.add(str(current_state))
    errors = payload.get("errors")
    if isinstance(errors, list):
        values.update(str(item) for item in errors if item)
    return sorted(values & FIRST_STAGE_TARGET_CONFIG_FAILURE_STATUSES)


def _passthrough_failure_statuses(payload: dict[str, Any]) -> list[str]:
    return sorted(_status_values(payload) & FIRST_STAGE_PASSTHROUGH_FAILURE_STATUSES)


def _status_values(payload: dict[str, Any]) -> set[str]:
    status_values = {
        str(payload.get("status") or ""),
        str(payload.get("final_status") or ""),
    }
    return {value for value in status_values if value}


def _has_first_stage_signal(payload: dict[str, Any]) -> bool:
    if any(key in payload for key in ("S10_READY", "status", "final_status", "same_source_cards")):
        return True
    flow_state = payload.get("flow_state")
    return isinstance(flow_state, dict) and "S10_READY" in flow_state


def _contains_false_s10_ready(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) == "S10_READY" and _falsey(item):
                return True
            if _contains_false_s10_ready(item):
                return True
    if isinstance(value, list):
        return any(_contains_false_s10_ready(item) for item in value)
    return False


def _nested_get(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _falsey(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    return str(value).strip().lower() in {"0", "false", "no", "n"}


def _coerce_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
