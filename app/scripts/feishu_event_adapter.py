"""Adapt Feishu message events to the local Phase 1 gateway event shape."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import json
from typing import Any


SENSITIVE_KEYS = {
    "access_key",
    "app_secret",
    "authorization",
    "encrypt_key",
    "secret",
    "tenant_access_token",
    "ticket",
    "token",
}


class FeishuEventAdapterError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AdaptedFeishuEvent:
    event: dict[str, Any]


def adapt_feishu_event(raw_event: Any) -> dict[str, Any]:
    payload, conversion = _event_to_dict(raw_event)
    body = payload.get("event", payload)
    message = body.get("message", body)
    sender = body.get("sender", payload.get("sender", {}))
    header = payload.get("header", {})

    event_type = str(header.get("event_type") or payload.get("event_type") or "")
    message_type = message.get("message_type") or message.get("msg_type") or body.get("message_type")
    if message_type and message_type != "text":
        raise FeishuEventAdapterError("UNSUPPORTED_MESSAGE_TYPE", f"Unsupported message type: {message_type}")

    text = message.get("text") or body.get("text") or payload.get("text")
    content = message.get("content") or body.get("content") or payload.get("content")
    if not text and content:
        text = _extract_text_from_content(content)
    text = str(text or "").strip()
    if not text:
        raise FeishuEventAdapterError("EMPTY_MESSAGE_TEXT", "Feishu message text is empty.")

    sender_id = _sender_id(sender) or body.get("sender_id") or payload.get("sender_id") or ""
    chat_id = message.get("chat_id") or body.get("chat_id") or payload.get("chat_id")
    chat_name = message.get("chat_name") or body.get("chat_name") or payload.get("chat_name") or message.get("name") or body.get("name")
    receive_id = chat_id or message.get("receive_id") or body.get("receive_id") or payload.get("receive_id")
    if not receive_id:
        raise FeishuEventAdapterError("FEISHU_CHAT_ID_MISSING", "Feishu message chat_id is missing.")
    chat_id = chat_id or receive_id
    message_id = message.get("message_id") or body.get("message_id") or payload.get("message_id") or ""
    reply_to_message_id = (
        message.get("reply_to_message_id")
        or body.get("reply_to_message_id")
        or payload.get("reply_to_message_id")
    )
    parent_message_id = (
        message.get("parent_message_id")
        or message.get("parent_id")
        or body.get("parent_message_id")
        or payload.get("parent_message_id")
    )

    return {
        "raw_message_id": message_id,
        "raw_sender_id": sender_id,
        "raw_chat_id": chat_id,
        "chat_name": chat_name,
        "reply_to_message_id": reply_to_message_id,
        "parent_message_id": parent_message_id,
        "receive_id": receive_id,
        "message_id": message_id,
        "sender_id": sender_id,
        "chat_id": chat_id,
        "text": text,
        "created_at": _now_iso(),
        "event_type": event_type,
        "message_type": str(message_type or ""),
        "adapter_debug": conversion,
        "raw_event": sanitize_event(payload),
    }


def adapt_feishu_event_result(raw_event: Any) -> dict[str, Any]:
    try:
        return {"ok": True, "event": adapt_feishu_event(raw_event)}
    except FeishuEventAdapterError as exc:
        return {
            "ok": False,
            "error_code": exc.code,
            "message": str(exc),
            "raw_event": _safe_event_preview(raw_event),
        }
    except Exception as exc:
        return {"ok": False, "error_code": "FEISHU_EVENT_ADAPT_FAILED", "message": str(exc)}


def sanitize_event(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                sanitized[key] = "<REDACTED>"
            else:
                sanitized[key] = sanitize_event(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_event(item) for item in value]
    return value


def _event_to_dict(raw_event: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    base_meta = {
        "event_object_class": _class_name(raw_event),
        "marshal_success": False,
        "marshal_method": "",
    }
    if isinstance(raw_event, dict):
        return raw_event, {**base_meta, "marshal_method": "dict"}
    if isinstance(raw_event, str):
        return _ensure_dict(json.loads(raw_event), "json_string"), {**base_meta, "marshal_method": "json_string"}

    marshaled = _marshal_with_lark(raw_event)
    if marshaled is not None:
        payload, method = marshaled
        return payload, {**base_meta, "marshal_success": True, "marshal_method": method}

    to_dict = getattr(raw_event, "to_dict", None)
    if callable(to_dict):
        return _ensure_dict(_object_to_plain(to_dict()), "to_dict"), {**base_meta, "marshal_method": "to_dict"}

    for attr in ("to_json", "json", "model_dump_json"):
        method = getattr(raw_event, attr, None)
        if callable(method):
            return _ensure_dict(json.loads(method()), attr), {**base_meta, "marshal_method": attr}

    data = getattr(raw_event, "data", None)
    if data is not None:
        payload = _object_to_plain(data)
        if isinstance(payload, dict) and payload:
            return payload, {**base_meta, "marshal_method": "data"}

    payload = _object_to_plain(raw_event)
    if isinstance(payload, dict) and _looks_like_event_payload(payload):
        return payload, {**base_meta, "marshal_method": "__dict__"}

    payload = _object_attributes_to_payload(raw_event)
    if payload:
        return payload, {**base_meta, "marshal_method": "attributes"}

    raise FeishuEventAdapterError("UNSUPPORTED_FEISHU_EVENT_OBJECT", f"Unsupported Feishu event object: {_class_name(raw_event)}")


def _marshal_with_lark(raw_event: Any) -> tuple[dict[str, Any], str] | None:
    try:
        lark = importlib.import_module("lark_oapi")
    except ImportError:
        return None
    json_api = getattr(lark, "JSON", None)
    marshal = getattr(json_api, "marshal", None)
    if not callable(marshal):
        return None
    try:
        marshaled = marshal(raw_event)
    except Exception as exc:
        raise FeishuEventAdapterError("FEISHU_EVENT_MARSHAL_FAILED", f"Failed to marshal Feishu event object: {_class_name(raw_event)}") from exc
    try:
        if isinstance(marshaled, str):
            payload = _ensure_dict(json.loads(marshaled), "lark.JSON.marshal")
            method = "lark.JSON.marshal:str"
        elif isinstance(marshaled, bytes):
            payload = _ensure_dict(json.loads(marshaled.decode("utf-8")), "lark.JSON.marshal")
            method = "lark.JSON.marshal:bytes"
        elif isinstance(marshaled, dict):
            payload = marshaled
            method = "lark.JSON.marshal:dict"
        else:
            payload = _ensure_dict(_object_to_plain(marshaled), "lark.JSON.marshal")
            method = "lark.JSON.marshal:object"
        if "data" in payload and isinstance(payload["data"], dict) and _looks_like_event_payload(payload["data"]):
            payload = payload["data"]
        if not _looks_like_event_payload(payload):
            return None
        return payload, method
    except Exception as exc:
        raise FeishuEventAdapterError("FEISHU_EVENT_MARSHAL_FAILED", f"Failed to decode marshaled Feishu event object: {_class_name(raw_event)}") from exc


def _extract_text_from_content(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("text", ""))
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            return content
        if isinstance(decoded, dict):
            return str(decoded.get("text", ""))
        if isinstance(decoded, str):
            return decoded
        return content
    return ""


def _sender_id(sender: Any) -> str | None:
    if not isinstance(sender, dict):
        return None
    sender_id = sender.get("sender_id")
    if isinstance(sender_id, dict):
        return sender_id.get("open_id") or sender_id.get("user_id") or sender_id.get("union_id")
    if sender_id:
        return str(sender_id)
    return sender.get("open_id") or sender.get("user_id") or sender.get("union_id")


def _ensure_dict(value: Any, source: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise FeishuEventAdapterError("UNSUPPORTED_FEISHU_EVENT_OBJECT", f"Feishu event from {source} is not a dict.")


def _object_to_plain(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _object_to_plain(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_object_to_plain(item, depth=depth + 1) for item in value]
    if hasattr(value, "__dict__"):
        return {
            str(key).lstrip("_"): _object_to_plain(item, depth=depth + 1)
            for key, item in vars(value).items()
            if not callable(item)
        }
    return value


def _object_attributes_to_payload(value: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for attr in ("schema", "header", "event", "message", "sender", "receive_id"):
        if hasattr(value, attr):
            payload[attr] = _object_to_plain(getattr(value, attr))
    if "event" in payload or "message" in payload:
        return payload
    return {}


def _looks_like_event_payload(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("event", "message", "sender", "schema", "header", "receive_id"))


def _safe_event_preview(raw_event: Any) -> Any:
    if isinstance(raw_event, dict):
        return sanitize_event(raw_event)
    if isinstance(raw_event, str):
        try:
            return sanitize_event(json.loads(raw_event))
        except json.JSONDecodeError:
            return {"object_class": "str", "length": len(raw_event)}
    return {"object_class": _class_name(raw_event)}


def _class_name(value: Any) -> str:
    cls = value.__class__
    return f"{cls.__module__}.{cls.__name__}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
