"""Feishu confirmation preflight hooks.

The gateway keeps this hook injectable so unit tests and dry runs never touch a
real device.  The live receiver passes this function when it is allowed to kick
the local APP runner.
"""

from __future__ import annotations

from typing import Any

try:
    from device_ready_gate import check_device_ready_for_pricing
except ImportError:  # pragma: no cover
    from scripts.device_ready_gate import check_device_ready_for_pricing


def check_confirm_device_ready_preflight(
    *,
    task_id: str | None = None,
    store: Any | None = None,
    allow_app_run: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Return whether a Feishu confirmation may enter the runnable queue."""

    if not allow_app_run:
        return {
            "ok": True,
            "status": "CONFIRM_PREFLIGHT_SKIPPED_DRY_RUN",
            "device_ready_for_pricing": True,
            "should_enqueue": True,
            "should_start_runner": False,
            "does_not_call_adb": True,
        }
    return check_device_ready_for_pricing(task_id=task_id, **kwargs)

