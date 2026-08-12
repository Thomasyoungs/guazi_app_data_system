"""Local preflight checks for Feishu Phase 4B.

This script only inspects local files and environment variables. It does not
connect to Feishu, send messages, or start the pricing runtime.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NEXT_COMMAND = "python scripts/feishu_realtime_receiver.py --listen"


def mask_secret(secret: str | None) -> str:
    value = str(secret or "")
    if not value:
        return "<not set>"
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}****{value[-3:]}"


def is_lark_sdk_installed() -> bool:
    return importlib.util.find_spec("lark_oapi") is not None


def run_preflight(
    *,
    project_root: str | Path = PROJECT_ROOT,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    sdk_installed: bool | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    current_dir = Path(cwd or os.getcwd()).resolve()
    current_env = env if env is not None else os.environ

    app_id = str(current_env.get("FEISHU_APP_ID") or "")
    app_secret = str(current_env.get("FEISHU_APP_SECRET") or "")
    sdk_ok = is_lark_sdk_installed() if sdk_installed is None else bool(sdk_installed)

    scripts_dir = root / "scripts"
    data_dir = root / "data"
    runtime_dir = root / "runtime"
    checks: dict[str, Any] = {
        "cwd_is_project_root": current_dir == root,
        "python_available": bool(sys.executable),
        "sdk_installed": sdk_ok,
        "feishu_app_id_set": bool(app_id),
        "feishu_app_secret_set": bool(app_secret),
        "feishu_app_id_cli_prefix": bool(app_id.startswith("cli_")) if app_id else False,
        "feishu_app_secret_non_empty": bool(app_secret),
        "feishu_app_secret_masked": mask_secret(app_secret),
        "receiver_exists": (scripts_dir / "feishu_realtime_receiver.py").exists(),
        "gateway_exists": (scripts_dir / "feishu_gateway.py").exists(),
        "send_message_exists": (scripts_dir / "feishu_send_message.py").exists(),
        "data_feishu_tasks_exists": (data_dir / "feishu_tasks").exists(),
        "current_target_task_exists": (data_dir / "current_target_task.json").exists(),
        "pricing_lock_exists": (runtime_dir / "pricing.lock").exists(),
        "next_command": NEXT_COMMAND,
    }

    errors: list[str] = []
    warnings: list[str] = []
    if not checks["cwd_is_project_root"]:
        errors.append("PROJECT_ROOT_MISMATCH")
    if not app_id or not app_secret:
        errors.append("FEISHU_ENV_MISSING")
    if app_id and not app_id.startswith("cli_"):
        errors.append("FEISHU_APP_ID_FORMAT_INVALID")
    if not sdk_ok:
        errors.append("FEISHU_SDK_NOT_INSTALLED")
    for key, code in (
        ("receiver_exists", "FEISHU_RECEIVER_NOT_FOUND"),
        ("gateway_exists", "FEISHU_GATEWAY_NOT_FOUND"),
        ("send_message_exists", "FEISHU_SEND_MESSAGE_NOT_FOUND"),
    ):
        if not checks[key]:
            errors.append(code)
    if not checks["data_feishu_tasks_exists"]:
        warnings.append("data/feishu_tasks/ does not exist; the gateway will create it when a task is received.")
    if checks["current_target_task_exists"]:
        warnings.append("data/current_target_task.json exists; Phase 4A/4B must not write or refresh it.")
    if checks["pricing_lock_exists"]:
        warnings.append("runtime/pricing.lock exists; check whether an old pricing run was interrupted.")

    return {
        "ok": not errors,
        "error_code": errors[0] if errors else None,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }


def format_preflight_report(result: Mapping[str, Any]) -> str:
    checks = result.get("checks", {})
    warnings = list(result.get("warnings", []))
    errors = list(result.get("errors", []))
    lines = [
        "FEISHU_PREFLIGHT_CHECK",
        "",
        f"cwd is project root: {_bool_text(checks.get('cwd_is_project_root'))}",
        f"Python available: {_bool_text(checks.get('python_available'))}",
        f"SDK installed: {_bool_text(checks.get('sdk_installed'))}",
        f"FEISHU_APP_ID set: {_bool_text(checks.get('feishu_app_id_set'))}",
        f"FEISHU_APP_SECRET set: {_bool_text(checks.get('feishu_app_secret_set'))}",
        f"FEISHU_APP_ID cli_ prefix: {_bool_text(checks.get('feishu_app_id_cli_prefix'))}",
        f"FEISHU_APP_SECRET non-empty: {_bool_text(checks.get('feishu_app_secret_non_empty'))}",
        f"FEISHU_APP_SECRET masked: {checks.get('feishu_app_secret_masked', '<not set>')}",
        f"receiver exists: {_bool_text(checks.get('receiver_exists'))}",
        f"gateway exists: {_bool_text(checks.get('gateway_exists'))}",
        f"send_message exists: {_bool_text(checks.get('send_message_exists'))}",
        f"data/feishu_tasks exists: {_bool_text(checks.get('data_feishu_tasks_exists'))}",
        f"current_target_task exists: {_bool_text(checks.get('current_target_task_exists'))}",
        f"pricing.lock exists: {_bool_text(checks.get('pricing_lock_exists'))}",
    ]
    if errors:
        lines.extend(["", "Errors:"])
        lines.extend(f"- {error}" for error in errors)
    if warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(["", "Next:", str(checks.get("next_command") or NEXT_COMMAND)])
    return "\n".join(lines)


def _bool_text(value: Any) -> str:
    return "true" if bool(value) else "false"


def main() -> int:
    result = run_preflight()
    print(format_preflight_report(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
