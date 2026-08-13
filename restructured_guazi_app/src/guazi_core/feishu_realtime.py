"""Feishu long-connection realtime receiver.

Uses lark-oapi SDK WebSocket to receive Feishu messages locally.
Parses vehicle search parameters and drives ADB automation.

Usage:
    python -m guazi_core.feishu_realtime

Environment:
    FEISHU_APP_ID     - Feishu app ID
    FEISHU_APP_SECRET - Feishu app secret
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

from .app_startup import AdbClient
from .task_normalizer import TargetCarTask, normalize_target_task

LOGGER = logging.getLogger(__name__)

# Reuse the field parser from feishu_webhook
from .feishu_webhook import _parse_car_fields as parse_car_fields  # noqa: F401

GUAZI_PACKAGE = "com.ganji.android.haoche_c"


def _launch_guazi_app(task: TargetCarTask) -> dict[str, Any]:
    """Launch Guazi APP via ADB and optionally navigate to search."""
    try:
        client = AdbClient()
    except Exception as exc:
        return {"success": False, "error": f"ADB client init failed: {exc}"}

    if not client.available:
        return {"success": False, "error": "ADB not available"}

    # Launch the main activity
    launch_cmd = [
        "shell",
        "am", "start", "-W",
        "-a", "android.intent.action.MAIN",
        "-n", f"{GUAZI_PACKAGE}/.activity.SplashActivity",
    ]
    result = client.run(launch_cmd, timeout=30)

    adb_result = {
        "success": result.success,
        "returncode": result.returncode,
        "stdout": result.stdout[:500] if result.stdout else "",
        "stderr": result.stderr[:500] if result.stderr else "",
    }

    # If launch succeeded, optionally tap the search tab
    if result.success and task.brand and task.series:
        import time
        time.sleep(2)
        try:
            client.run(["shell", "input", "tap", "540", "2200"], timeout=10)
        except Exception:
            pass

    return adb_result


def _handle_feishu_text(text: str) -> dict[str, Any]:
    """Process a single Feishu text message."""
    # Parse car fields
    car_fields = parse_car_fields(text)
    if not car_fields.get("brand") or not car_fields.get("series"):
        return {
            "ok": False,
            "error": "Missing brand or series in message",
            "extracted_text": text,
        }

    # Build a normalized task
    try:
        task = normalize_target_task(car_fields, source="feishu_api", simulation_only=True)
    except Exception as exc:
        return {"ok": False, "error": f"Task normalization failed: {exc}", "fields": car_fields}

    # Launch Guazi APP via ADB
    adb_result = _launch_guazi_app(task)

    return {
        "ok": True,
        "task_id": task.task_id,
        "parsed_fields": car_fields,
        "adb_launch": adb_result,
        "message": "Task accepted and ADB launch initiated.",
    }


def _on_message_receive(data: Any) -> None:
    """Callback when a Feishu message is received via long connection."""
    try:
        # data is a P2ImMessageReceiveV1 event object from lark-oapi
        # Try to extract text from common payload shapes
        text = ""
        if hasattr(data, "event") and hasattr(data.event, "message"):
            msg = data.event.message
            if hasattr(msg, "content"):
                content = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                text = content.get("text", "") if isinstance(content, dict) else str(content)
        # Fallback: try dict-like access
        if not text and isinstance(data, dict):
            event = data.get("event", {})
            message = event.get("message", {})
            content = message.get("content", {})
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    pass
            text = content.get("text", "") if isinstance(content, dict) else str(content)

        text = str(text or "").strip()
        if not text:
            LOGGER.info("Empty or unparseable message received, skipping.")
            return

        LOGGER.info("Received Feishu message text: %s", text[:200])

        if text in {"测试", "test"}:
            LOGGER.info("Test message received, replying.")
            return

        result = _handle_feishu_text(text)
        LOGGER.info("Feishu message handled: ok=%s", result.get("ok"))

    except Exception:
        LOGGER.exception("Error handling Feishu message event")


def run_realtime_receiver() -> None:
    """Start the Feishu long-connection receiver."""
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")

    if not app_id or not app_secret:
        print("Error: FEISHU_APP_ID and FEISHU_APP_SECRET environment variables are required.")
        print("")
        print("Option 1: Set environment variables manually")
        print('  $env:FEISHU_APP_ID="cli_xxxxxxxxxxxxx"')
        print('  $env:FEISHU_APP_SECRET="your_app_secret_here"')
        print("")
        print("Option 2: Create .env file in project root")
        print("  FEISHU_APP_ID=cli_xxxxxxxxxxxxx")
        print("  FEISHU_APP_SECRET=your_app_secret_here")
        sys.exit(1)

    try:
        import lark_oapi as lark  # type: ignore[import-not-found]
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        print("Error: lark-oapi is not installed.")
        print("Install with: python -m pip install lark-oapi")
        sys.exit(1)

    # Set up logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    def on_message_receive(data: Any) -> None:
        _on_message_receive(data)

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )

    print(f"Connecting to Feishu with APP_ID: {app_id[:6]}...")
    print("Waiting for messages... (Ctrl+C to stop)")
    client = lark.ws.Client(app_id, app_secret, event_handler=event_handler)
    client.start()


if __name__ == "__main__":
    run_realtime_receiver()
