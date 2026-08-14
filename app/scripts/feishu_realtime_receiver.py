"""Feishu Phase 4A realtime receiver.

This module wires real Feishu message events into the existing local gateway.
Dry-run handling never starts the APP; live "确认" may trigger the guarded
background dispatch kick.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any

try:
    from feishu_event_adapter import adapt_feishu_event_result
    from feishu_confirm_preflight import check_confirm_device_ready_preflight
    from feishu_gateway import HELP_TEXT, handle_event
    from feishu_preflight_check import format_preflight_report, run_preflight
    from feishu_send_message import send_text_message
    from feishu_task_store import DEFAULT_TASK_ROOT, FeishuTaskStore
except ImportError:  # pragma: no cover
    from scripts.feishu_event_adapter import adapt_feishu_event_result
    from scripts.feishu_confirm_preflight import check_confirm_device_ready_preflight
    from scripts.feishu_gateway import HELP_TEXT, handle_event
    from scripts.feishu_preflight_check import format_preflight_report, run_preflight
    from scripts.feishu_send_message import send_text_message
    from scripts.feishu_task_store import DEFAULT_TASK_ROOT, FeishuTaskStore


TEST_REPLY = "【二手车定价系统】已收到测试消息，本地飞书网关连接正常。"
LOGGER = logging.getLogger(__name__)


def handle_realtime_event(
    raw_event: Any,
    *,
    store: FeishuTaskStore | None = None,
    task_root: str | Path = DEFAULT_TASK_ROOT,
    send_live: bool = False,
    roles: dict[str, Any] | None = None,
    group_bindings: Any | None = None,
    dispatch_kicker: Any | None = None,
    dispatch_kick_allow_app_run: bool | None = None,
    confirm_preflight_checker: Any | None = None,
) -> dict[str, Any]:
    LOGGER.info("received feishu event object: %s", _class_name(raw_event))
    adapted = adapt_feishu_event_result(raw_event)
    if not adapted.get("ok"):
        LOGGER.warning("feishu event adapt failed: error_code=%s class=%s", adapted.get("error_code"), _class_name(raw_event))
        return {
            "ok": False,
            "error_code": adapted.get("error_code"),
            "message": adapted.get("message"),
            "reply_text": "",
            "send_result": None,
        }

    internal_event = adapted["event"]
    debug = internal_event.get("adapter_debug", {})
    LOGGER.info(
        "feishu event adapted: class=%s marshal=%s event_type=%s message_type=%s message_id=%s chat_id_present=%s text_length=%s",
        debug.get("event_object_class") or _class_name(raw_event),
        debug.get("marshal_method", ""),
        internal_event.get("event_type", ""),
        internal_event.get("message_type", ""),
        internal_event.get("raw_message_id", ""),
        bool(internal_event.get("receive_id") or internal_event.get("raw_chat_id")),
        len(str(internal_event.get("text") or "")),
    )
    store = store or FeishuTaskStore(task_root)
    text = str(internal_event.get("text") or "").strip()
    if text == "测试":
        gateway_result = _reply_result(TEST_REPLY, action="test")
    elif text == "帮助":
        gateway_result = _reply_result(HELP_TEXT, action="help")
    else:
        try:
            gateway_result = handle_event(
                internal_event,
                store=store,
                roles=roles,
                group_bindings=group_bindings,
                dispatch_kicker=dispatch_kicker,
                dispatch_kick_allow_app_run=send_live if dispatch_kick_allow_app_run is None else dispatch_kick_allow_app_run,
                confirm_preflight_checker=confirm_preflight_checker,
            )
        except Exception as exc:
            return {
                "ok": False,
                "error_code": "GATEWAY_HANDLE_FAILED",
                "message": str(exc),
                "reply_text": "",
                "send_result": None,
                "event": internal_event,
            }

    LOGGER.info("feishu gateway handled: ok=%s action=%s", bool(gateway_result.get("ok", True)), gateway_result.get("action"))
    reply_text = str(gateway_result.get("reply_text") or "")
    if reply_text:
        send_result = send_text_message(
            text=reply_text,
            chat_id=str(internal_event.get("receive_id") or internal_event.get("raw_chat_id") or ""),
            dry_run=not send_live,
        )
    else:
        send_result = {
            "ok": True,
            "dry_run": not send_live,
            "skipped": True,
            "skip_reason": "EMPTY_REPLY_TEXT",
        }
    if gateway_result.get("action") == "create_task" and gateway_result.get("task_id") and send_result.get("message_id"):
        try:
            store.bind_confirm_card_message_id(str(gateway_result["task_id"]), str(send_result["message_id"]))
        except Exception as exc:  # pragma: no cover - defensive logging only.
            LOGGER.warning("failed to bind confirm card message id: task_id=%s error=%s", gateway_result.get("task_id"), exc)
    if gateway_result.get("action") == "confirm_task" and gateway_result.get("task_id") and reply_text:
        try:
            store.record_start_ack_delivery_result(
                str(gateway_result["task_id"]),
                send_result=send_result,
                returned_to_gateway=True,
            )
        except Exception as exc:  # pragma: no cover - defensive logging only.
            LOGGER.warning("failed to persist start ack delivery: task_id=%s error=%s", gateway_result.get("task_id"), exc)
    LOGGER.info("feishu reply send result: ok=%s dry_run=%s", bool(send_result.get("ok", False)), bool(send_result.get("dry_run", False)))
    _persist_raw_event_if_task_exists(store, gateway_result.get("task_id"), internal_event)
    return {
        "ok": bool(gateway_result.get("ok", True)) and bool(send_result.get("ok", False)),
        "action": gateway_result.get("action"),
        "task_id": gateway_result.get("task_id"),
        "reply_text": reply_text,
        "send_result": send_result,
        "gateway_result": gateway_result,
        "dispatch_kick": (gateway_result.get("data") or {}).get("dispatch_kick"),
        "event": internal_event,
    }


def run_dry_run_event(event_path: str | Path, *, task_root: str | Path = DEFAULT_TASK_ROOT) -> dict[str, Any]:
    event_path = Path(event_path)
    raw_event = json.loads(event_path.read_text(encoding="utf-8"))
    store = FeishuTaskStore(task_root)
    result = handle_realtime_event(raw_event, store=store, task_root=task_root, send_live=False)
    preview_path = event_path.parent / "reply_preview.txt"
    preview_path.write_text(str(result.get("reply_text") or "") + "\n", encoding="utf-8")
    result["reply_preview_path"] = str(preview_path)
    return result


def listen_forever(*, task_root: str | Path = DEFAULT_TASK_ROOT) -> dict[str, Any]:
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        return {"ok": False, "error_code": "FEISHU_ENV_MISSING", "message": "FEISHU_APP_ID and FEISHU_APP_SECRET are required."}

    try:
        import lark_oapi as lark  # type: ignore[import-not-found]
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        return {"ok": False, "error_code": "FEISHU_SDK_NOT_INSTALLED", "message": "Install lark-oapi with: python -m pip install lark-oapi"}

    try:
        def on_message_receive(data: Any) -> None:
            raw = data
            handle_realtime_event(
                raw,
                task_root=task_root,
                send_live=True,
                confirm_preflight_checker=check_confirm_device_ready_preflight,
            )

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message_receive)
            .build()
        )
        client = lark.ws.Client(app_id, app_secret, event_handler=event_handler)
        client.start()
        return {"ok": True, "mode": "listen"}
    except Exception as exc:  # pragma: no cover - live SDK path is not hit by unit tests.
        return {"ok": False, "error_code": "FEISHU_LISTEN_FAILED", "message": str(exc)}


def _reply_result(reply_text: str, *, action: str) -> dict[str, Any]:
    return {
        "ok": True,
        "action": action,
        "task_id": None,
        "status": None,
        "duplicate": False,
        "changed": False,
        "reply_text": reply_text,
        "reply_payload": {"msg_type": "text", "content": {"text": reply_text}},
    }


def _persist_raw_event_if_task_exists(store: FeishuTaskStore, task_id: Any, internal_event: dict[str, Any]) -> None:
    if not task_id:
        return
    task_dir = store.task_dir(str(task_id))
    if task_dir.exists():
        (task_dir / "raw_event.json").write_text(json.dumps(internal_event.get("raw_event", internal_event), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _class_name(value: Any) -> str:
    cls = value.__class__
    return f"{cls.__module__}.{cls.__name__}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Receive Feishu realtime messages for Phase 4A.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--listen", action="store_true", help="Connect to Feishu long connection and listen.")
    mode.add_argument("--dry-run-event", default=None, help="Read a local Feishu event JSON and process it without network.")
    mode.add_argument("--self-check", action="store_true", help="Run local preflight checks without network.")
    parser.add_argument("--data-root", default=str(DEFAULT_TASK_ROOT), help="Feishu task store root.")
    args = parser.parse_args(argv)

    if args.self_check:
        result = run_preflight()
        print(format_preflight_report(result))
        return 0 if result.get("ok") else 1
    if args.dry_run_event:
        result = run_dry_run_event(args.dry_run_event, task_root=args.data_root)
    else:
        result = listen_forever(task_root=args.data_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
