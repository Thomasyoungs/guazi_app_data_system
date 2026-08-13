"""Feishu webhook server for receiving messages and driving ADB automation.

Runs a lightweight HTTP server that:
1. Receives Feishu webhook POST requests
2. Parses vehicle search parameters
3. Launches the Guazi APP via ADB
4. Returns a task tracking response

Usage:
    python -m guazi_core.feishu_webhook --port 8080
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .app_startup import AdbClient
from .task_normalizer import TargetCarTask, normalize_target_task


GUAZI_PACKAGE = "com.ganji.android.haoche_c"
DEFAULT_PORT = 8080


class FeishuWebhookHandler(BaseHTTPRequestHandler):
    """Handle incoming Feishu webhook requests."""

    def log_message(self, format: str, *args: Any) -> None:
        """Override to reduce noise; only log errors."""
        if hasattr(self, "server") and getattr(self.server, "_debug", False):
            super().log_message(format, *args)

    def do_GET(self) -> None:  # noqa: N802
        """Health check endpoint."""
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"status": "ok", "service": "feishu-webhook"})
        else:
            self._send_json(404, {"error": "Not found", "path": parsed.path})

    def do_POST(self) -> None:  # noqa: N802
        """Receive Feishu webhook message."""
        parsed = urlparse(self.path)
        if parsed.path != "/webhook/feishu":
            self._send_json(404, {"error": "Not found", "path": parsed.path})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(400, {"error": "Empty body"})
            return

        try:
            body = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": "Invalid JSON", "detail": str(exc)})
            return

        # Route to handler
        result = self._handle_feishu_message(payload)
        self._send_json(result.get("status_code", 200), result)

    def _handle_feishu_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse Feishu message and trigger ADB workflow."""
        # Extract text from various Feishu payload shapes
        text = _extract_text(payload)
        if not text:
            return {"status_code": 400, "error": "Could not extract text from payload"}

        # Parse car fields from text
        car_fields = _parse_car_fields(text)
        if not car_fields.get("brand") or not car_fields.get("series"):
            return {"status_code": 400, "error": "Missing brand or series in message", "extracted_text": text}

        # Build a normalized task (simulation-only for webhook flow)
        try:
            task = normalize_target_task(car_fields, source="feishu_api", simulation_only=True)
        except Exception as exc:
            return {"status_code": 400, "error": f"Task normalization failed: {exc}", "fields": car_fields}

        # Launch Guazi APP via ADB
        adb_result = _launch_guazi_app(task)

        return {
            "status_code": 200,
            "ok": True,
            "task_id": task.task_id,
            "parsed_fields": car_fields,
            "adb_launch": adb_result,
            "message": "Task accepted and ADB launch initiated."
        }

    def _send_json(self, status_code: int, data: dict[str, Any]) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def _extract_text(payload: dict[str, Any]) -> str:
    """Extract text content from various Feishu payload shapes."""
    # Direct text field
    text = payload.get("text")
    if text and isinstance(text, str):
        return text.strip()

    # Feishu event format
    event = payload.get("event") or {}
    message = event.get("message") or {}
    content = message.get("content") or {}
    if content:
        if isinstance(content, dict):
            return str(content.get("text", "")).strip()
        return str(content).strip()

    # Flat message format
    msg = payload.get("message", {})
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, dict):
            return str(content.get("text", "")).strip()
        if content and isinstance(content, str):
            return content.strip()
        text = msg.get("text")
        if text and isinstance(text, str):
            return text.strip()

    # Fallback
    return str(payload.get("text", "")).strip()


def _parse_car_fields(text: str) -> dict[str, Any]:
    """Parse car search fields from free-form text.

    Supports patterns like:
        品牌:大众
        车系:帕萨特
        年款:2020
        里程:4.5万公里
        颜色:白色
    """
    import re

    fields: dict[str, Any] = {
        "brand": "",
        "series": "",
        "model_year": "",
        "trim": "",
        "color": "",
        "registration_date": "",
        "mileage_10k_km": None,
        "transfer_count": None,
        "condition_text": text,
    }

    # Normalize delimiters
    normalized = text.replace("：", ":").replace("\n", " ")

    # Find positions of all known field markers
    known_fields = ["品牌", "车系", "年款", "颜色", "上牌", "里程", "过户"]
    markers: list[tuple[int, int, str]] = []
    for field_name in known_fields:
        for match in re.finditer(rf"{field_name}[:\s]+", normalized, re.IGNORECASE):
            markers.append((match.start(), match.end(), field_name))
    markers.sort(key=lambda x: x[0])

    # Extract values between markers
    for i, (_start, end, field_name) in enumerate(markers):
        if i + 1 < len(markers):
            next_start = markers[i + 1][0]
            value = normalized[end:next_start].strip()
        else:
            value = normalized[end:].strip()

        if field_name == "品牌":
            fields["brand"] = value
        elif field_name == "车系":
            fields["series"] = value
        elif field_name == "年款":
            year_match = re.search(r"(\d{4})", value)
            if year_match:
                fields["model_year"] = year_match.group(1)
        elif field_name == "颜色":
            fields["color"] = value
        elif field_name == "上牌":
            reg_match = re.search(r"(\d{4}\.\d{1,2})", value)
            if reg_match:
                fields["registration_date"] = reg_match.group(1)
        elif field_name == "里程":
            mileage_match = re.search(r"(\d+\.?\d*)\s*万", value, re.IGNORECASE)
            if mileage_match:
                fields["mileage_10k_km"] = float(mileage_match.group(1))
        elif field_name == "过户":
            transfer_match = re.search(r"(\d+)", value)
            if transfer_match:
                fields["transfer_count"] = int(transfer_match.group(1))

    return fields


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
        "-n", f"{GUAZI_PACKAGE}/com.cars.guazi.app.home.MainActivity",
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
        # Small delay to let the app settle
        import time
        time.sleep(2)
        # Try to tap the "选车" tab at bottom
        try:
            client.run(["shell", "input", "tap", "540", "2200"], timeout=10)
        except Exception:
            pass

    return adb_result


class ThreadedHTTPServer(HTTPServer):
    """Simple threaded HTTP server."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], debug: bool = False) -> None:
        super().__init__(server_address, handler_class)
        self._debug = debug


def run_webhook_server(port: int = DEFAULT_PORT, debug: bool = False) -> None:
    """Start the Feishu webhook HTTP server."""
    server = ThreadedHTTPServer(("0.0.0.0", port), FeishuWebhookHandler, debug=debug)
    print(f"Feishu webhook server listening on http://0.0.0.0:{port}/webhook/feishu")
    print(f"Health check: http://0.0.0.0:{port}/health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down webhook server...")
        server.shutdown()
