"""Strict target ADB device gate before any APP/page operation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .adb_target_device import (
    TARGET_ADB_DEVICE_NOT_CONNECTED,
    TARGET_ADB_DEVICE_OFFLINE,
    TARGET_ADB_DEVICE_UNAUTHORIZED,
    TARGET_ADB_SERIAL_NOT_CONFIGURED,
    validate_target_device_available,
)
from .app_startup import ADBCommandResult, AdbClient


TRANSIENT_ALLOWED_AUTO_ACTIONS = [
    "check_adb_available",
    "adb_version",
    "adb_devices_l",
    "validate_configured_target_serial",
    "record_issue",
    "lookup_knowledge_base",
]

TRANSIENT_FORBIDDEN_ACTIONS = [
    "launch_app",
    "capture_screenshot",
    "dump_ui_xml",
    "click_any_page",
    "click_vehicle_model_config",
    "click_filter",
    "click_sort",
    "click_vehicle_card",
    "collect_vehicle_data",
    "modify_pricing_formula",
]


def run_adb_device_gate(
    client: Any,
    *,
    issues: Any | None = None,
    audit: Any | None = None,
    allow_transient_recovery: bool = True,
) -> dict[str, Any]:
    """Return a structured gate result before any APP/page operation.

    The gate intentionally does not default to any connected device and does
    not perform service-level recovery. APP launch, screenshots, XML dump,
    clicks, and collection stay forbidden until this function returns passed.
    """

    result: dict[str, Any] = {
        "passed": False,
        "status": None,
        "device_snapshot_taken_at": datetime.now(timezone.utc).isoformat(),
        "device_snapshot_error": "",
        "adb_available": bool(getattr(client, "available", False)),
        "adb_path": str(getattr(client, "adb_path", "") or ""),
        "adb_path_source": str(getattr(client, "adb_path_source", "") or ""),
        "adb_version": "",
        "adb_devices_l_first": "",
        "adb_devices_l_second": "",
        "adb_devices_l_raw": "",
        "devices": [],
        "parsed_devices": [],
        "initial_devices": [],
        "recovered": False,
        "transient_recovery_attempted": False,
        "rerun_all_gates_required": False,
        "allowed_auto_actions": list(TRANSIENT_ALLOWED_AUTO_ACTIONS),
        "forbidden_actions": list(TRANSIENT_FORBIDDEN_ACTIONS),
        "issue_records": [],
        "target_device": {},
        "target_serial": "",
        "target_device_state": "unknown",
        "target_device_present_before_first_stage": False,
        "strict_device_selection": True,
    }
    if hasattr(client, "runtime_environment_snapshot"):
        try:
            result.update(client.runtime_environment_snapshot())
        except Exception as exc:  # pragma: no cover - defensive diagnostic only
            result["device_snapshot_error"] = f"env_snapshot_failed: {exc}"

    def record(code: str, message: str, context: dict[str, Any], resolution: str) -> dict[str, Any] | None:
        if not issues:
            return None
        issue = issues.record(code, "DEVICE", message, context, resolution)
        result["issue_records"].append(issue)
        return issue

    if not result["adb_available"]:
        record(
            "ADB_NOT_FOUND",
            "adb executable is not available; device gate stops before APP flow.",
            {"adb_available": False, "adb_path": result["adb_path"]},
            "local_simulation_only",
        )
        result["status"] = "ADB_NOT_FOUND"
        result["target_device_state"] = "adb_not_found"
        result["device_snapshot_error"] = result["device_snapshot_error"] or "adb executable is not available"
        return result

    version = _run_adb(client, ["version"], timeout=20)
    result["adb_version"] = version.stdout.strip()
    if not version.success and version.stderr:
        result["device_snapshot_error"] = result["device_snapshot_error"] or version.stderr

    first = _run_adb(client, ["devices", "-l"], timeout=20)
    result["adb_devices_l_first"] = first.stdout
    result["adb_devices_l_raw"] = first.stdout
    if not first.success and first.stderr:
        result["device_snapshot_error"] = result["device_snapshot_error"] or first.stderr
    first_entries = AdbClient.parse_devices(first.stdout)
    result["initial_devices"] = first_entries
    result["devices"] = first_entries
    result["parsed_devices"] = first_entries
    validation = validate_target_device_available(
        first_entries,
        active_serial=str(getattr(client, "adb_serial", "") or ""),
    )
    result["target_device"] = validation.get("target", {})
    result["target_serial"] = str((validation.get("target") or {}).get("adb_serial") or getattr(client, "adb_serial", "") or "")
    result["strict_device_selection"] = bool((validation.get("target") or {}).get("strict", True))
    if validation.get("ok"):
        result["passed"] = True
        result["status"] = "device"
        result["target_device_state"] = "device"
        result["target_device_present_before_first_stage"] = True
        result["target_device_validation"] = validation
        return result

    code = str(validation.get("code") or TARGET_ADB_DEVICE_NOT_CONNECTED)
    result["target_device_state"] = _target_device_state_from_validation(validation)
    message_by_code = {
        TARGET_ADB_SERIAL_NOT_CONFIGURED: "Target ADB serial is not configured; stop before any device operation.",
        TARGET_ADB_DEVICE_NOT_CONNECTED: "Configured target ADB device is not connected; no fallback device is allowed.",
        TARGET_ADB_DEVICE_UNAUTHORIZED: "Configured target ADB device is unauthorized; stop before APP flow.",
        TARGET_ADB_DEVICE_OFFLINE: "Configured target ADB device is offline; stop before APP flow.",
    }
    resolution_by_code = {
        TARGET_ADB_SERIAL_NOT_CONFIGURED: "configure_target_adb_serial",
        TARGET_ADB_DEVICE_NOT_CONNECTED: "connect_configured_target_device",
        TARGET_ADB_DEVICE_UNAUTHORIZED: "authorize_configured_target_device",
        TARGET_ADB_DEVICE_OFFLINE: "restore_configured_target_device_online",
    }
    record(
        code,
        message_by_code.get(code, message_by_code[TARGET_ADB_DEVICE_NOT_CONNECTED]),
        {
            "adb_available": True,
            "adb_devices_l": first.stdout,
            "devices": first_entries,
            "target_device_validation": validation,
            "no_default_device_fallback": True,
        },
        resolution_by_code.get(code, "manual_intervention"),
    )
    result["status"] = code
    result["target_device_validation"] = validation
    if audit:
        audit.log(
            "target_adb_device_gate_failed",
            code=code,
            target_serial=result["target_serial"],
            connected_serials=validation.get("connected_serials", []),
            no_default_device_fallback=True,
        )
    return result


def classify_device_entries(entries: list[dict[str, str]]) -> str:
    statuses = [entry.get("status", "") for entry in entries]
    if "device" in statuses:
        return "device"
    if "unauthorized" in statuses:
        return "unauthorized"
    if "offline" in statuses:
        return "offline"
    return "empty"


def _target_device_state_from_validation(validation: dict[str, Any]) -> str:
    code = str(validation.get("code") or "")
    if code == TARGET_ADB_SERIAL_NOT_CONFIGURED:
        return "not_configured"
    if code == TARGET_ADB_DEVICE_NOT_CONNECTED:
        return "missing"
    device = validation.get("device") if isinstance(validation.get("device"), dict) else {}
    status = str(device.get("status") or validation.get("status") or "").strip()
    if status:
        return status
    return "missing"


def _run_adb(client: Any, args: list[str], timeout: int = 20) -> ADBCommandResult:
    if hasattr(client, "run"):
        return client.run(args, timeout=timeout)
    if args == ["devices", "-l"] and hasattr(client, "devices"):
        devices = client.devices()
        return ADBCommandResult(["adb", *args], 0, _format_devices_output(devices), "")
    return ADBCommandResult(["adb", *args], 0, "", "")


def _format_devices_output(devices: list[dict[str, str]]) -> str:
    lines = ["List of devices attached"]
    lines.extend(f"{device.get('serial', '')}\t{device.get('status', '')}" for device in devices)
    return "\n".join(lines) + "\n"
