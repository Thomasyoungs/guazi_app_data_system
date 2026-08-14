"""Strict target ADB device selection helpers.

This module deliberately never falls back to "the first" or "the only"
connected device. APP automation may only use the configured target serial.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "adb_target_device.yaml"

ENV_ADB_SERIAL = "GUAZI_ADB_SERIAL"

TARGET_ADB_SERIAL_NOT_CONFIGURED = "TARGET_ADB_SERIAL_NOT_CONFIGURED"
TARGET_ADB_DEVICE_NOT_CONNECTED = "TARGET_ADB_DEVICE_NOT_CONNECTED"
TARGET_ADB_DEVICE_UNAUTHORIZED = "TARGET_ADB_DEVICE_UNAUTHORIZED"
TARGET_ADB_DEVICE_OFFLINE = "TARGET_ADB_DEVICE_OFFLINE"
TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT = "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT"
TARGET_ADB_DEVICE_NOT_WHITELISTED = "TARGET_ADB_DEVICE_NOT_WHITELISTED"
TARGET_ADB_FORBIDDEN_SERVER_COMMAND = "TARGET_ADB_FORBIDDEN_SERVER_COMMAND"

TARGET_ADB_ERROR_CODES = {
    TARGET_ADB_SERIAL_NOT_CONFIGURED,
    TARGET_ADB_DEVICE_NOT_CONNECTED,
    TARGET_ADB_DEVICE_UNAUTHORIZED,
    TARGET_ADB_DEVICE_OFFLINE,
    TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT,
    TARGET_ADB_DEVICE_NOT_WHITELISTED,
}

SERIAL_FREE_ADB_COMMANDS = {
    ("devices",),
    ("devices", "-l"),
    ("version",),
}

FORBIDDEN_ADB_COMMANDS = {
    ("kill" + "-server",),
    ("disconnect",),
}


def load_target_device_config(project_root: str | Path | None = None) -> dict[str, Any]:
    """Load the tiny YAML config without requiring a YAML dependency."""

    root = Path(project_root) if project_root else PROJECT_ROOT
    path = root / "config" / "adb_target_device.yaml"
    config: dict[str, Any] = {
        "active_adb_serial": "",
        "device_alias": "Redmi Note 12 5G",
        "strict_device_selection": True,
        "allow_default_when_single_device": False,
        "device_whitelist": [],
    }
    if not path.exists():
        return config
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        text = value.strip().strip('"').strip("'")
        lowered = text.lower()
        # support a simple comma-separated whitelist entry, e.g.:
        # device_whitelist: 3417599354001L0,6TGYHPZCETCSK6L
        # or device_whitelist: [3417..., 6TGY...]
        if key == "device_whitelist":
            t = text.strip()
            t = t.strip("[]")
            items = [s.strip().strip('"').strip("'") for s in t.split(",") if s.strip()]
            config[key] = items
        elif lowered in {"true", "false"}:
            config[key] = lowered == "true"
        else:
            config[key] = text
    return config


def load_target_adb_serial(
    *,
    environ: dict[str, str] | None = None,
    project_root: str | Path | None = None,
) -> str:
    env = environ if environ is not None else os.environ
    env_value = str(env.get(ENV_ADB_SERIAL) or "").strip()
    if env_value:
        return env_value
    config = load_target_device_config(project_root)
    return str(config.get("active_adb_serial") or "").strip()


def get_target_device_context(
    *,
    environ: dict[str, str] | None = None,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    config = load_target_device_config(project_root)
    serial = load_target_adb_serial(environ=environ, project_root=project_root)
    return {
        "adb_serial": serial,
        "device_alias": str(config.get("device_alias") or "Redmi Note 12 5G"),
        "strict": bool(config.get("strict_device_selection", True)),
        "allow_default_when_single_device": bool(config.get("allow_default_when_single_device", False)),
        "device_whitelist": list(config.get("device_whitelist") or []),
        "config_path": str((Path(project_root) if project_root else PROJECT_ROOT) / "config" / "adb_target_device.yaml"),
        "serial_source": "env" if (environ if environ is not None else os.environ).get(ENV_ADB_SERIAL) else "config",
    }


def normalize_adb_args(args: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(arg) for arg in args)


def adb_command_requires_serial(args: Iterable[Any]) -> bool:
    normalized = normalize_adb_args(args)
    return normalized not in SERIAL_FREE_ADB_COMMANDS


def adb_command_is_forbidden(args: Iterable[Any]) -> bool:
    normalized = normalize_adb_args(args)
    return normalized in FORBIDDEN_ADB_COMMANDS


def build_adb_command(
    args: list[str],
    *,
    adb_path: str | Path = "adb",
    active_serial: str | None = None,
    environ: dict[str, str] | None = None,
    project_root: str | Path | None = None,
) -> list[str]:
    """Build an ADB command bound to the configured target serial."""

    normalized_args = list(normalize_adb_args(args))
    if not adb_command_requires_serial(normalized_args):
        return [str(adb_path), *normalized_args]
    serial = str(
        load_target_adb_serial(environ=environ, project_root=project_root) if active_serial is None else active_serial
    ).strip()
    if not serial:
        raise ValueError(TARGET_ADB_SERIAL_NOT_CONFIGURED)
    return [str(adb_path), "-s", serial, *normalized_args]


def validate_target_device_available(
    device_list: list[dict[str, str]],
    *,
    active_serial: str | None = None,
    environ: dict[str, str] | None = None,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    serial = str(
        load_target_adb_serial(environ=environ, project_root=project_root) if active_serial is None else active_serial
    ).strip()
    context = get_target_device_context(environ=environ, project_root=project_root)
    context["adb_serial"] = serial
    if not serial:
        return {
            "ok": False,
            "code": TARGET_ADB_SERIAL_NOT_CONFIGURED,
            "status": "not_configured",
            "target": context,
            "connected_serials": [str(item.get("serial") or "") for item in device_list],
        }
    # enforce whitelist if present in config: only allow configured serials that appear in whitelist
    whitelist = list(context.get("device_whitelist") or [])
    if whitelist:
        if serial not in whitelist:
            return {
                "ok": False,
                "code": TARGET_ADB_DEVICE_NOT_WHITELISTED,
                "status": "not_whitelisted",
                "target": context,
                "connected_serials": [str(item.get("serial") or "") for item in device_list],
                "device_whitelist": whitelist,
            }
    for device in device_list:
        if str(device.get("serial") or "").strip() != serial:
            continue
        status = str(device.get("status") or "").strip()
        if status == "device":
            return {
                "ok": True,
                "code": "TARGET_ADB_DEVICE_READY",
                "status": "device",
                "target": context,
                "device": device,
                "connected_serials": [str(item.get("serial") or "") for item in device_list],
            }
        if status == "unauthorized":
            code = TARGET_ADB_DEVICE_UNAUTHORIZED
        elif status == "offline":
            code = TARGET_ADB_DEVICE_OFFLINE
        else:
            code = TARGET_ADB_DEVICE_NOT_CONNECTED
        return {
            "ok": False,
            "code": code,
            "status": status or "unknown",
            "target": context,
            "device": device,
            "connected_serials": [str(item.get("serial") or "") for item in device_list],
        }
    return {
        "ok": False,
        "code": TARGET_ADB_DEVICE_NOT_CONNECTED,
        "status": "not_connected",
        "target": context,
        "connected_serials": [str(item.get("serial") or "") for item in device_list],
    }


def validate_target_serial_configured(
    *,
    environ: dict[str, str] | None = None,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    context = get_target_device_context(environ=environ, project_root=project_root)
    if not context.get("adb_serial"):
        return {
            "ok": False,
            "code": TARGET_ADB_SERIAL_NOT_CONFIGURED,
            "status": "not_configured",
            "target": context,
        }
    return {"ok": True, "code": "TARGET_ADB_SERIAL_CONFIGURED", "status": "configured", "target": context}
