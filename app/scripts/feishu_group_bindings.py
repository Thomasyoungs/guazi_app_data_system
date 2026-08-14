"""Local Feishu group role and binding store.

This module stores chat identity setup in project-local JSON only. It does not
send Feishu messages, start listeners, or touch the APP runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GROUP_BINDINGS_PATH = PROJECT_ROOT / "data" / "feishu_group_bindings.json"
DEFAULT_BINDING_CODE_TTL_MINUTES = 10


@dataclass(frozen=True)
class GroupBindingResult:
    ok: bool
    action: str
    code: str | None = None
    error_code: str | None = None
    data: dict[str, Any] | None = None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def empty_group_bindings_payload() -> dict[str, Any]:
    return {
        "business_chats": {},
        "supervisor_chats": {},
        "binding_codes": {},
    }


class FeishuGroupBindings:
    def __init__(
        self,
        path: str | Path = DEFAULT_GROUP_BINDINGS_PATH,
        *,
        clock: Callable[[], datetime] | None = None,
        code_generator: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.clock = clock or now_utc
        self.code_generator = code_generator or self._generate_code

    def ensure_file(self) -> None:
        if not self.path.exists():
            self._write(empty_group_bindings_payload())

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_group_bindings_payload()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        base = empty_group_bindings_payload()
        for key in base:
            if isinstance(payload.get(key), dict):
                base[key] = payload[key]
        return base

    def set_business_chat(self, *, chat_id: str, chat_name: str | None, created_by: str) -> dict[str, Any]:
        payload = self.load()
        now = isoformat(self.clock())
        existing = payload["business_chats"].get(chat_id, {})
        payload["business_chats"][chat_id] = {
            **existing,
            "chat_id": chat_id,
            "chat_name": chat_name or existing.get("chat_name") or "未命名一线群",
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "created_by": existing.get("created_by") or created_by,
            "updated_by": created_by,
        }
        self._write(payload)
        return payload["business_chats"][chat_id]

    def set_supervisor_chat(self, *, chat_id: str, chat_name: str | None, created_by: str) -> dict[str, Any]:
        payload = self.load()
        now = isoformat(self.clock())
        existing = payload["supervisor_chats"].get(chat_id, {})
        payload["supervisor_chats"][chat_id] = {
            **existing,
            "chat_id": chat_id,
            "chat_name": chat_name or existing.get("chat_name") or "未命名主管复核群",
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "created_by": existing.get("created_by") or created_by,
            "updated_by": created_by,
        }
        self._write(payload)
        return payload["supervisor_chats"][chat_id]

    def is_business_chat(self, chat_id: str | None) -> bool:
        return bool(chat_id and chat_id in self.load()["business_chats"])

    def is_supervisor_chat(self, chat_id: str | None) -> bool:
        return bool(chat_id and chat_id in self.load()["supervisor_chats"])

    def business_chat(self, chat_id: str | None) -> dict[str, Any] | None:
        return self.load()["business_chats"].get(chat_id or "")

    def supervisor_chat(self, chat_id: str | None) -> dict[str, Any] | None:
        return self.load()["supervisor_chats"].get(chat_id or "")

    def generate_binding_code(self, *, business_chat_id: str, created_by: str, ttl_minutes: int = DEFAULT_BINDING_CODE_TTL_MINUTES) -> GroupBindingResult:
        payload = self.load()
        if business_chat_id not in payload["business_chats"]:
            return GroupBindingResult(ok=False, action="generate_binding_code", error_code="BUSINESS_CHAT_NOT_INITIALIZED")
        code = self._unique_code(payload)
        created_at = self.clock()
        payload["binding_codes"][code] = {
            "business_chat_id": business_chat_id,
            "created_at": isoformat(created_at),
            "expires_at": isoformat(created_at + timedelta(minutes=ttl_minutes)),
            "created_by": created_by,
            "used": False,
        }
        self._write(payload)
        return GroupBindingResult(ok=True, action="generate_binding_code", code=code, data=payload["binding_codes"][code])

    def bind_business_chat(self, *, code: str, supervisor_chat_id: str, used_by: str) -> GroupBindingResult:
        payload = self.load()
        code = str(code or "").strip().upper()
        code_payload = payload["binding_codes"].get(code)
        if not code_payload:
            return GroupBindingResult(ok=False, action="bind_business_chat", error_code="BINDING_CODE_NOT_FOUND")
        if code_payload.get("used") is True:
            return GroupBindingResult(ok=False, action="bind_business_chat", error_code="BINDING_CODE_USED")
        expires_at = parse_iso_datetime(str(code_payload.get("expires_at") or "1970-01-01T00:00:00+00:00"))
        if self.clock().astimezone(timezone.utc) > expires_at:
            return GroupBindingResult(ok=False, action="bind_business_chat", error_code="BINDING_CODE_EXPIRED")
        if supervisor_chat_id not in payload["supervisor_chats"]:
            return GroupBindingResult(ok=False, action="bind_business_chat", error_code="SUPERVISOR_CHAT_NOT_INITIALIZED")

        business_chat_id = str(code_payload.get("business_chat_id") or "")
        business_chat = payload["business_chats"].get(business_chat_id)
        if not business_chat:
            return GroupBindingResult(ok=False, action="bind_business_chat", error_code="BUSINESS_CHAT_NOT_INITIALIZED")
        if business_chat.get("bound_supervisor_chat_id"):
            return GroupBindingResult(
                ok=False,
                action="bind_business_chat",
                error_code="BUSINESS_CHAT_ALREADY_BOUND",
                data={"business_chat_id": business_chat_id, "bound_supervisor_chat_id": business_chat.get("bound_supervisor_chat_id")},
            )

        business_chat["bound_supervisor_chat_id"] = supervisor_chat_id
        business_chat["bound_at"] = isoformat(self.clock())
        business_chat["bound_by"] = used_by
        code_payload["used"] = True
        code_payload["used_at"] = isoformat(self.clock())
        code_payload["used_by"] = used_by
        code_payload["supervisor_chat_id"] = supervisor_chat_id
        self._write(payload)
        return GroupBindingResult(
            ok=True,
            action="bind_business_chat",
            code=code,
            data={
                "business_chat_id": business_chat_id,
                "supervisor_chat_id": supervisor_chat_id,
                "business_chat": business_chat,
                "supervisor_chat": payload["supervisor_chats"][supervisor_chat_id],
            },
        )

    def bound_supervisor_chat_id(self, business_chat_id: str | None) -> str | None:
        chat = self.business_chat(business_chat_id)
        value = chat.get("bound_supervisor_chat_id") if chat else None
        return str(value) if value else None

    def business_chats_bound_to_supervisor(self, supervisor_chat_id: str | None) -> list[dict[str, Any]]:
        if not supervisor_chat_id:
            return []
        payload = self.load()
        return [
            chat
            for chat in payload["business_chats"].values()
            if chat.get("bound_supervisor_chat_id") == supervisor_chat_id
        ]

    def describe_chat(self, chat_id: str | None) -> dict[str, Any]:
        payload = self.load()
        if chat_id in payload["business_chats"]:
            chat = payload["business_chats"][chat_id]
            supervisor_id = chat.get("bound_supervisor_chat_id")
            return {
                "role": "business",
                "chat": chat,
                "bound_supervisor_chat": payload["supervisor_chats"].get(supervisor_id or ""),
            }
        if chat_id in payload["supervisor_chats"]:
            return {
                "role": "supervisor",
                "chat": payload["supervisor_chats"][chat_id],
                "bound_business_chats": self.business_chats_bound_to_supervisor(chat_id),
            }
        return {"role": "unset", "chat": None}

    def _unique_code(self, payload: dict[str, Any]) -> str:
        for _ in range(100):
            code = self.code_generator()
            if code not in payload["binding_codes"]:
                return code
        raise RuntimeError("failed to generate unique Feishu binding code")

    def _generate_code(self) -> str:
        return f"BD-{random.SystemRandom().randint(0, 9999):04d}"

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mask_identifier(value: str | None) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return "<未设置>" if not text else text[:2] + "***"
    return f"{text[:4]}...{text[-4:]}"
