"""Feishu text message sender with dry-run and live SDK modes."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any


def build_text_message_payload(text: str) -> dict[str, Any]:
    return {
        "msg_type": "text",
        "content": {
            "text": text,
        },
    }


def send_text_message(
    *,
    text: str,
    chat_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    resolved_chat_id = chat_id or os.getenv("FEISHU_TEST_CHAT_ID") or "<FEISHU_TEST_CHAT_ID>"
    payload = build_text_message_payload(text)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "chat_id": resolved_chat_id,
            "receive_id_type": "chat_id",
            "payload": payload,
        }

    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        return {
            "ok": False,
            "dry_run": False,
            "error_code": "FEISHU_ENV_MISSING",
            "message": "FEISHU_APP_ID and FEISHU_APP_SECRET must be provided by environment variables.",
        }
    if not chat_id:
        return {
            "ok": False,
            "dry_run": False,
            "error_code": "FEISHU_SEND_FAILED",
            "message": "chat_id is required for live Feishu sending.",
        }

    try:
        import lark_oapi as lark  # type: ignore[import-not-found]
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody  # type: ignore[import-not-found]
    except ImportError:
        return {
            "ok": False,
            "dry_run": False,
            "error_code": "FEISHU_SDK_NOT_INSTALLED",
            "message": "Install lark-oapi with: python -m pip install lark-oapi",
        }

    try:
        client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = client.im.v1.message.create(request)
        if hasattr(response, "success") and not response.success():
            return {
                "ok": False,
                "dry_run": False,
                "error_code": "FEISHU_SEND_FAILED",
                "message": getattr(response, "msg", "Feishu send failed."),
                "code": getattr(response, "code", None),
            }
        message_id = _response_message_id(response)
        result = {
            "ok": True,
            "dry_run": False,
            "chat_id": chat_id,
            "receive_id_type": "chat_id",
            "payload": payload,
        }
        if message_id:
            result["message_id"] = message_id
        return result
    except Exception as exc:  # pragma: no cover - live SDK path is not hit by unit tests.
        return {
            "ok": False,
            "dry_run": False,
            "error_code": "FEISHU_SEND_FAILED",
            "message": str(exc),
        }


def _response_message_id(response: Any) -> str | None:
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        value = data.get("message_id") or data.get("message", {}).get("message_id")
        return str(value) if value else None
    for attr in ("message_id", "message_id_"):
        value = getattr(data, attr, None) if data is not None else None
        if value:
            return str(value)
    body = getattr(response, "body", None)
    if isinstance(body, dict):
        value = body.get("data", {}).get("message_id") or body.get("message_id")
        return str(value) if value else None
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send or dry-run a Feishu text message.")
    parser.add_argument("text", help="Reply text.")
    parser.add_argument("--chat-id", default=None, help="Feishu chat id. Defaults to FEISHU_TEST_CHAT_ID in dry-run.")
    parser.add_argument("--live", action="store_true", help="Send through Feishu SDK instead of dry-run.")
    args = parser.parse_args(argv)
    result = send_text_message(text=args.text, chat_id=args.chat_id, dry_run=not args.live)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
