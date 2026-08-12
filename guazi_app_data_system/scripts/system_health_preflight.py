"""Mockable system-health preflight for the Feishu dispatcher.

The default implementation is intentionally non-invasive: it does not call adb,
uiautomator, launch an APP, wake the phone, or inspect a real device. Production
deployments can inject a stricter checker around this interface.
"""

from __future__ import annotations

from typing import Any, Callable


def check_system_health_preflight(
    *,
    dry_run: bool = True,
    task_id: str | None = None,
    blocked_status: dict[str, Any] | None = None,
    error_codes: list[str] | None = None,
    force: bool = False,
    checker: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if checker is not None:
        return checker(
            dry_run=dry_run,
            task_id=task_id,
            blocked_status=blocked_status,
            error_codes=error_codes,
            force=force,
        )
    if blocked_status is not None or force:
        return {
            "ok": False,
            "dry_run": dry_run,
            "task_id": task_id,
            "force": force,
            "status": "SYSTEM_HEALTH_PREFLIGHT_CHECKER_NOT_CONFIGURED",
            "error_codes": error_codes or [],
            "checks": [
                "real_device_health_checker_not_configured",
            ],
            "errors": ["SYSTEM_HEALTH_CHECK_NOT_CONFIGURED"],
            "does_not_call_adb": True,
            "does_not_call_uiautomator": True,
            "does_not_start_app": True,
        }
    return {
        "ok": True,
        "dry_run": dry_run,
        "task_id": task_id,
        "force": force,
        "status": "SYSTEM_HEALTH_PREFLIGHT_MOCK_OK",
        "error_codes": error_codes or [],
        "checks": [
            "adb_device_check_injected_or_skipped",
            "guazi_app_check_injected_or_skipped",
            "login_block_check_injected_or_skipped",
        ],
        "errors": [],
        "does_not_call_adb": True,
        "does_not_call_uiautomator": True,
        "does_not_start_app": True,
    }
