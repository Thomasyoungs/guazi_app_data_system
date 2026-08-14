"""Local testable Feishu Phase 1 gateway."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable

try:
    from admin_intervention_router import detect_admin_recovery_command, format_system_not_recovered_reply
    from feishu_group_bindings import DEFAULT_GROUP_BINDINGS_PATH, FeishuGroupBindings, mask_identifier
    from feishu_message_to_target_task import parse_template_fields
    from feishu_result_formatter import format_manual_review_business_notice, format_supervisor_review_card, manual_review_reasons
    from feishu_send_message import build_text_message_payload, send_text_message
    from feishu_task_store import CANCELLED_TASK_RESEND_REPLY, DEFAULT_TASK_ROOT, WAITING_CONFIRMATION_STATUSES, FeishuTaskStore, TaskOperationResult
    from pricing_runner import PricingRunner
    from system_health_preflight import check_system_health_preflight
except ImportError:  # pragma: no cover - supports package-style imports in tests.
    from scripts.admin_intervention_router import detect_admin_recovery_command, format_system_not_recovered_reply
    from scripts.feishu_group_bindings import DEFAULT_GROUP_BINDINGS_PATH, FeishuGroupBindings, mask_identifier
    from scripts.feishu_message_to_target_task import parse_template_fields
    from scripts.feishu_result_formatter import format_manual_review_business_notice, format_supervisor_review_card, manual_review_reasons
    from scripts.feishu_send_message import build_text_message_payload, send_text_message
    from scripts.feishu_task_store import CANCELLED_TASK_RESEND_REPLY, DEFAULT_TASK_ROOT, WAITING_CONFIRMATION_STATUSES, FeishuTaskStore, TaskOperationResult
    from scripts.pricing_runner import PricingRunner
    from scripts.system_health_preflight import check_system_health_preflight


HELP_TEXT = "请发送目标车信息。\n收到确认卡后，请回复：确认\n若任务需要人工复核，请直接回复人工确认收车价，例如：86000 或 8.6万。"
TASK_ID_PATTERN = r"FS\d{8}_\d{4}"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROLES_PATH = PROJECT_ROOT / "config" / "feishu_roles.yaml"
CONFIRM_AUTO_DISPATCH_REPLY = "【定价已开始】{task_id}\n系统已开始自动定价，请等待结果。"
MANUAL_PRICE_CONFIRM_PROMPT = "当前任务需要主管确认收车价，请直接回复价格，例如：86000 或 8.6万。"
TARGET_INFO_CONFIRM_PROMPT = "这台车源信息需要修改，请重新发送完整目标车源信息，我会重新生成任务并排队。"
BUSINESS_SYSTEM_PROCESSING_REPLY = "系统定价暂未完成，请稍后查看结果。"
EMPTY_ROLES = {
    "admin_open_ids": [],
    "business_chat_ids": [],
    "supervisor_chat_ids": [],
    "supervisor_open_ids": [],
    "admin_chat_ids": [],
}


def extract_event_message(event: dict[str, Any]) -> dict[str, str | None]:
    body = event.get("event", event)
    message = body.get("message", body)
    sender = body.get("sender", event.get("sender", {}))
    sender_id = (
        body.get("sender_id")
        or sender.get("sender_id", {}).get("open_id")
        or sender.get("sender_id", {}).get("user_id")
        or sender.get("open_id")
        or sender.get("user_id")
    )

    text = event.get("text") or body.get("text") or message.get("text")
    content = message.get("content") or body.get("content")
    if not text and content:
        text = _extract_text_from_content(content)

    return {
        "text": text or "",
        "raw_message_id": event.get("raw_message_id") or event.get("message_id") or body.get("message_id") or message.get("message_id"),
        "raw_sender_id": event.get("raw_sender_id") or sender_id,
        "raw_chat_id": event.get("raw_chat_id") or event.get("chat_id") or body.get("chat_id") or message.get("chat_id"),
        "chat_name": event.get("chat_name") or body.get("chat_name") or message.get("chat_name") or body.get("name") or message.get("name"),
        "reply_to_message_id": event.get("reply_to_message_id") or body.get("reply_to_message_id") or message.get("reply_to_message_id"),
        "parent_message_id": event.get("parent_message_id") or body.get("parent_message_id") or message.get("parent_message_id") or message.get("parent_id"),
    }


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
    return ""


def detect_command(text: str) -> tuple[str, str | None]:
    stripped = text.strip()
    first_line = next((line.strip() for line in stripped.splitlines() if line.strip()), "")
    if first_line == "定价":
        return "pricing_template", None
    if stripped == "确认":
        return "confirm_latest", None
    match = re.match(rf"^(确认|取消|状态)\s+({TASK_ID_PATTERN})$", stripped)
    if match:
        command = {"确认": "confirm", "取消": "cancel", "状态": "status"}[match.group(1)]
        return command, match.group(2)
    reverse_confirm = re.match(rf"^({TASK_ID_PATTERN})\s+确认$", stripped)
    if reverse_confirm:
        return "confirm", reverse_confirm.group(1)
    return "help", None


def _duplicate_confirm_has_preflight_failure(status: dict[str, Any]) -> bool:
    current = str(status.get("status") or "")
    return (
        current in WAITING_CONFIRMATION_STATUSES
        and bool(status.get("confirm_preflight_failed"))
        and status.get("device_ready_for_pricing") is False
        and not bool(status.get("started"))
    )


def _duplicate_status_has_started_evidence(status: dict[str, Any]) -> bool:
    current = str(status.get("status") or "")
    if current not in {"CONFIRMED", "QUEUED", "RUNNING", "IN_PROGRESS"}:
        return False
    if status.get("device_ready_for_pricing") is not True:
        return False
    return bool(status.get("started")) or bool(status.get("queued_at"))


def handle_event(
    event: dict[str, Any],
    *,
    store: FeishuTaskStore | None = None,
    clock: Callable[[], datetime] | None = None,
    roles: dict[str, Any] | None = None,
    roles_path: str | Path | None = None,
    group_bindings: FeishuGroupBindings | None = None,
    group_bindings_path: str | Path | None = None,
    system_health_checker: Callable[..., dict[str, Any]] = check_system_health_preflight,
    confirm_preflight_checker: Callable[..., dict[str, Any]] | None = None,
    dispatch_kicker: Callable[..., dict[str, Any]] | None = None,
    dispatch_kick_allow_app_run: bool = False,
) -> dict[str, Any]:
    store = store or FeishuTaskStore(DEFAULT_TASK_ROOT, clock=clock)
    roles = normalize_feishu_roles(roles if roles is not None else load_feishu_roles(roles_path or DEFAULT_ROLES_PATH))
    group_bindings = group_bindings or FeishuGroupBindings(
        group_bindings_path or _default_group_bindings_path_for_store(store),
        clock=clock or store.clock,
    )
    message = extract_event_message(event)
    text = clean_feishu_command_text(str(message["text"] or ""))
    raw_message_id = message["raw_message_id"]
    raw_event_id = str(event.get("event_id") or event.get("uuid") or "")
    if detect_self_identity_command(text):
        result = TaskOperationResult(
            success=True,
            action="self_identity",
            changed=False,
            reply_text=format_self_identity_reply(
                sender_open_id=message["raw_sender_id"],
                chat_id=message["raw_chat_id"],
                chat_name=message.get("chat_name"),
                roles=roles,
            ),
        )
        return _to_reply_payload(result)
    duplicate_task_id = store.lookup_processed_message(raw_message_id)
    if duplicate_task_id:
        duplicate_command, _duplicate_arg = detect_command(text)
        duplicate_status = store.load_status(duplicate_task_id) or {}
        start_trace_path = store.task_dir(duplicate_task_id) / "feishu_start_message_delivery.json"
        start_ack_sent = False
        if start_trace_path.exists():
            try:
                start_ack_sent = bool(json.loads(start_trace_path.read_text(encoding="utf-8")).get("start_ack_sent"))
            except (OSError, json.JSONDecodeError):
                start_ack_sent = False
        if duplicate_command in {"confirm", "confirm_latest"} and start_ack_sent:
            trace = store.record_duplicate_confirm_ignored(
                duplicate_task_id,
                raw_message_id=raw_message_id,
                event_id=raw_event_id or None,
            )
            result = TaskOperationResult(
                success=True,
                action="duplicate_confirm_ignored",
                task_id=duplicate_task_id,
                status=(store.load_status(duplicate_task_id) or {}).get("status"),
                duplicate=True,
                changed=False,
                reply_text="",
                data={
                    "duplicate_confirm_ignored": True,
                    "duplicate_confirm_guard_code": "FEISHU_CONFIRM_START_ACK_ALREADY_SENT_DUPLICATE_IGNORED",
                    "feishu_start_message_trace": trace,
                },
            )
            return _to_reply_payload(result)
        if duplicate_command in {"confirm", "confirm_latest"} and _duplicate_confirm_has_preflight_failure(duplicate_status):
            reply_text = str(duplicate_status.get("confirm_preflight_business_reply_text") or "")
            if not reply_text:
                preview_path = store.task_dir(duplicate_task_id) / "confirm_preflight_business_reply.preview.txt"
                if preview_path.exists():
                    try:
                        reply_text = preview_path.read_text(encoding="utf-8").strip()
                    except OSError:
                        reply_text = ""
            trace = store.record_confirm_preflight_failure_duplicate_replayed(
                duplicate_task_id,
                raw_message_id=raw_message_id,
                event_id=raw_event_id or None,
                replayed_reply_text=reply_text,
                silent=not bool(reply_text),
            )
            result = TaskOperationResult(
                success=False,
                action="duplicate_confirm_preflight_failure_replayed" if reply_text else "duplicate_confirm_preflight_failure_ignored",
                task_id=duplicate_task_id,
                status=duplicate_status.get("status"),
                duplicate=True,
                changed=True,
                reply_text=reply_text,
                data={
                    "duplicate_confirm_preflight_failure": True,
                    "duplicate_confirm_guard_code": trace.get("duplicate_confirm_guard_code"),
                    "start_ack_blocked_code": trace.get("start_ack_blocked_code"),
                    "processed_message_outcome": "confirm_preflight_failed",
                    "trace": trace,
                },
            )
            return _to_reply_payload(result)
        if str(duplicate_status.get("status") or "") == "CANCELLED":
            feedback_result = store.ensure_cancelled_task_final_feedback(duplicate_task_id)
            if feedback_result.success:
                result = TaskOperationResult(
                    success=True,
                    action="duplicate_message_cancelled_feedback",
                    task_id=duplicate_task_id,
                    status="CANCELLED",
                    duplicate=True,
                    changed=feedback_result.changed,
                    reply_text=feedback_result.reply_text,
                    data={"feedback": feedback_result.data},
                )
                return _to_reply_payload(result)
        if _duplicate_status_has_started_evidence(duplicate_status):
            result = TaskOperationResult(
                success=True,
                action="duplicate_message_started_evidence",
                task_id=duplicate_task_id,
                status=duplicate_status.get("status"),
                duplicate=True,
                changed=False,
                reply_text=CONFIRM_AUTO_DISPATCH_REPLY.format(task_id=duplicate_task_id),
                data={
                    "duplicate_message_started_evidence": True,
                    "started_evidence": {
                        "status": duplicate_status.get("status"),
                        "device_ready_for_pricing": duplicate_status.get("device_ready_for_pricing"),
                        "started": duplicate_status.get("started"),
                        "queued_at": duplicate_status.get("queued_at"),
                    },
                },
            )
            return _to_reply_payload(result)
        trace = store.record_duplicate_message_no_start_ack_without_started_evidence(
            duplicate_task_id,
            raw_message_id=raw_message_id,
            event_id=raw_event_id or None,
            command=duplicate_command,
        )
        result = TaskOperationResult(
            success=True,
            action="duplicate_message_no_start_ack_without_started_evidence",
            task_id=duplicate_task_id,
            duplicate=True,
            status=duplicate_status.get("status"),
            changed=True,
            reply_text="",
            data={
                "duplicate_message_guard_code": "FEISHU_DUPLICATE_MESSAGE_NO_START_ACK_WITHOUT_STARTED_EVIDENCE",
                "trace": trace,
            },
        )
        return _to_reply_payload(result)

    admin_recovery_command, recovery_task_id = detect_admin_recovery_command(text)
    if admin_recovery_command:
        result = handle_admin_recovery_command(
            recovery_task_id,
            sender_open_id=message["raw_sender_id"],
            roles=roles,
            store=store,
            system_health_checker=system_health_checker,
            dispatch_kicker=dispatch_kicker,
            dispatch_kick_allow_app_run=dispatch_kick_allow_app_run,
        )
        if result.task_id:
            store.record_processed_message(raw_message_id, result.task_id)
        return _to_reply_payload(result)

    group_command, group_arg = detect_group_command(text)
    if group_command:
        result = handle_group_command(
            group_command,
            group_arg,
            sender_open_id=message["raw_sender_id"],
            chat_id=message["raw_chat_id"],
            chat_name=message.get("chat_name"),
            roles=roles,
            group_bindings=group_bindings,
        )
        if result.task_id:
            store.record_processed_message(raw_message_id, result.task_id)
    else:
        command, task_id = detect_command(text)
        if command == "pricing_template" or (command == "help" and is_target_vehicle_message(text)):
            result = _handle_target_vehicle_message(
                text,
                raw_event=event,
                raw_message_id=raw_message_id,
                message=message,
                store=store,
                roles=roles,
                group_bindings=group_bindings,
            )
        elif command == "confirm_latest":
            result = handle_one_word_confirm(
                sender_open_id=message["raw_sender_id"],
                business_chat_id=message["raw_chat_id"],
                reply_to_message_id=message.get("reply_to_message_id"),
                parent_message_id=message.get("parent_message_id"),
                roles=roles,
                store=store,
                system_health_checker=system_health_checker,
                confirm_preflight_checker=confirm_preflight_checker,
                dispatch_kicker=dispatch_kicker,
                dispatch_kick_allow_app_run=dispatch_kick_allow_app_run,
            )
            if result.task_id:
                store.record_processed_message(raw_message_id, result.task_id)
        elif command == "confirm" and task_id:
            result = handle_explicit_confirm(
                task_id,
                sender_open_id=message["raw_sender_id"],
                roles=roles,
                store=store,
                system_health_checker=system_health_checker,
                confirm_preflight_checker=confirm_preflight_checker,
                dispatch_kicker=dispatch_kicker,
                dispatch_kick_allow_app_run=dispatch_kick_allow_app_run,
            )
            if result.task_id:
                store.record_processed_message(raw_message_id, result.task_id)
        elif command == "cancel" and task_id:
            result = store.cancel_task(task_id)
            if result.task_id:
                store.record_processed_message(raw_message_id, result.task_id)
        elif command == "status" and task_id:
            result = store.status_reply(task_id)
            if result.task_id:
                store.record_processed_message(raw_message_id, result.task_id)
        else:
            if should_attempt_manual_price_route(
                sender_open_id=message["raw_sender_id"],
                chat_id=message["raw_chat_id"],
                roles=roles,
                group_bindings=group_bindings,
            ):
                task_id_from_text, price_yuan, price_error = parse_manual_price_command(text)
            else:
                task_id_from_text, price_yuan, price_error = None, None, None
            if price_yuan is not None:
                result = _handle_manual_price(
                    price_yuan,
                    store=store,
                    sender_open_id=message["raw_sender_id"],
                    supervisor_chat_id=message["raw_chat_id"],
                    reply_to_message_id=message.get("reply_to_message_id"),
                    parent_message_id=message.get("parent_message_id"),
                    task_id_from_text=task_id_from_text,
                    manual_confirm_raw_text=text,
                    roles=roles,
                    group_bindings=group_bindings,
                )
                if result.task_id:
                    store.record_processed_message(raw_message_id, result.task_id)
            elif price_error:
                result = TaskOperationResult(
                    success=False,
                    action="manual_price_parse_failed",
                    changed=False,
                    reply_text="人工确认收车价格式无法识别。请直接回复一个价格，例如：86000 或 8.6万。",
                )
            else:
                result = TaskOperationResult(
                    success=False,
                    action="help",
                    changed=False,
                    reply_text="请回复“确认”两个字确认目标车信息，或重新发送目标车信息。\n如果是在补充字段，请重新发送完整目标车源信息，避免字段混乱。",
                )
    return _to_reply_payload(result)


TARGET_VEHICLE_ROUTE_FIELDS = {
    "brand",
    "series",
    "model_config",
    "license_date",
    "mileage_text",
    "color",
    "transfer_count_text",
    "condition_text",
    "city",
}


def is_target_vehicle_message(text: str) -> bool:
    fields = parse_template_fields(text)
    return bool(TARGET_VEHICLE_ROUTE_FIELDS.intersection(fields))


def _handle_target_vehicle_message(
    text: str,
    *,
    raw_event: dict[str, Any],
    raw_message_id: str | None,
    message: dict[str, str | None],
    store: FeishuTaskStore,
    roles: dict[str, list[str]],
    group_bindings: FeishuGroupBindings,
) -> TaskOperationResult:
    if not is_business_chat(str(message["raw_chat_id"] or ""), roles=roles, group_bindings=group_bindings):
        return TaskOperationResult(
            success=False,
            action="business_chat_not_initialized",
            changed=False,
            reply_text="本群尚未设置为一线群，请管理员发送“设置本群为一线群”。",
        )
    return store.create_task_from_message(
        text=text,
        raw_event=raw_event,
        raw_message_id=raw_message_id,
        raw_sender_id=message["raw_sender_id"],
        raw_chat_id=message["raw_chat_id"],
    )


def handle_one_word_confirm(
    *,
    sender_open_id: str | None,
    business_chat_id: str | None,
    reply_to_message_id: str | None,
    parent_message_id: str | None,
    roles: dict[str, list[str]],
    store: FeishuTaskStore,
    system_health_checker: Callable[..., dict[str, Any]] = check_system_health_preflight,
    confirm_preflight_checker: Callable[..., dict[str, Any]] | None = None,
    dispatch_kicker: Callable[..., dict[str, Any]] | None = None,
    dispatch_kick_allow_app_run: bool = False,
) -> TaskOperationResult:
    bound_message_id = reply_to_message_id or parent_message_id
    preflight_candidate = _resolve_one_word_confirm_task_id(
        store=store,
        sender_open_id=sender_open_id,
        business_chat_id=business_chat_id,
        bound_message_id=bound_message_id,
    )
    if isinstance(preflight_candidate, TaskOperationResult):
        return preflight_candidate
    if preflight_candidate:
        preflight_failure = _run_confirm_preflight(
            preflight_candidate,
            store=store,
            confirm_preflight_checker=confirm_preflight_checker,
            dispatch_kick_allow_app_run=dispatch_kick_allow_app_run,
        )
        if preflight_failure:
            return preflight_failure
        target_result = store.confirm_task(preflight_candidate, confirmed_by_open_id=sender_open_id)
        if target_result.success:
            return _with_dispatch_kick(
                target_result,
                store=store,
                dispatch_kicker=dispatch_kicker,
                dispatch_kick_allow_app_run=dispatch_kick_allow_app_run,
            )
        if (target_result.data or {}).get("candidate_task_ids"):
            return target_result
        return target_result

    target_result = store.confirm_bound_target_task(
        sender_open_id=sender_open_id,
        business_chat_id=business_chat_id,
        reply_to_message_id=reply_to_message_id,
        parent_message_id=parent_message_id,
    )
    if target_result.success:
        return _with_dispatch_kick(
            target_result,
            store=store,
            dispatch_kicker=dispatch_kicker,
            dispatch_kick_allow_app_run=dispatch_kick_allow_app_run,
        )
    if (target_result.data or {}).get("candidate_task_ids"):
        return target_result

    target_info_tasks = store.target_info_correction_context_tasks(
        sender_open_id=sender_open_id,
        business_chat_id=business_chat_id,
        confirm_card_message_id=bound_message_id,
    )
    if len(target_info_tasks) == 1:
        return TaskOperationResult(
            success=False,
            action="target_info_correction_confirm_rejected",
            task_id=target_info_tasks[0],
            status=(store.load_status(target_info_tasks[0]) or {}).get("status"),
            changed=False,
            reply_text=TARGET_INFO_CONFIRM_PROMPT,
        )

    manual_price_tasks = store.manual_price_context_tasks(
        chat_id=business_chat_id,
        supervisor_review_card_message_id=bound_message_id,
    )
    if len(manual_price_tasks) == 1:
        return TaskOperationResult(
            success=False,
            action="manual_price_confirm_rejected",
            task_id=manual_price_tasks[0],
            status=(store.load_status(manual_price_tasks[0]) or {}).get("status"),
            changed=False,
            reply_text=MANUAL_PRICE_CONFIRM_PROMPT,
        )
    if len(manual_price_tasks) > 1:
        return TaskOperationResult(
            success=False,
            action="manual_price_multiple_waiting_review",
            changed=False,
            reply_text="当前有多个待复核任务，请回复对应复核卡片，或直接回复任务号和价格。",
            data={"candidate_task_ids": manual_price_tasks},
        )

    blocked_task_ids = store.admin_blocked_tasks(recoverable_only=True)
    if blocked_task_ids:
        if not is_admin_open_id(sender_open_id, roles):
            return TaskOperationResult(
                success=False,
                action="admin_recovery_confirm_not_admin",
                changed=False,
                reply_text=BUSINESS_SYSTEM_PROCESSING_REPLY,
                data={"candidate_task_ids": blocked_task_ids},
            )
        return handle_admin_recovery_command(
            None,
            sender_open_id=sender_open_id,
            roles=roles,
            store=store,
            system_health_checker=system_health_checker,
            dispatch_kicker=dispatch_kicker,
            dispatch_kick_allow_app_run=dispatch_kick_allow_app_run,
        )

    return target_result


def handle_explicit_confirm(
    task_id: str,
    *,
    sender_open_id: str | None,
    roles: dict[str, list[str]],
    store: FeishuTaskStore,
    system_health_checker: Callable[..., dict[str, Any]] = check_system_health_preflight,
    confirm_preflight_checker: Callable[..., dict[str, Any]] | None = None,
    dispatch_kicker: Callable[..., dict[str, Any]] | None = None,
    dispatch_kick_allow_app_run: bool = False,
) -> TaskOperationResult:
    status = store.load_status(task_id) or {}
    current = str(status.get("status") or "")
    if current == "CANCELLED":
        feedback_result = store.ensure_cancelled_task_final_feedback(task_id)
        reply_text = feedback_result.reply_text if feedback_result.success else CANCELLED_TASK_RESEND_REPLY
        return TaskOperationResult(
            success=False,
            action="cancelled_task_confirm_rejected",
            task_id=task_id,
            status=current,
            changed=feedback_result.changed if feedback_result.success else False,
            reply_text=reply_text,
            data={"feedback": feedback_result.data} if feedback_result.success else None,
        )
    if current in {"SYSTEM_BLOCKED", "ADMIN_INTERVENTION_REQUIRED"}:
        if not is_admin_open_id(sender_open_id, roles):
            return TaskOperationResult(
                success=False,
                action="admin_recovery_confirm_not_admin",
                task_id=task_id,
                status=current,
                changed=False,
                reply_text=BUSINESS_SYSTEM_PROCESSING_REPLY,
            )
        return handle_admin_recovery_command(
            task_id,
            sender_open_id=sender_open_id,
            roles=roles,
            store=store,
            system_health_checker=system_health_checker,
            dispatch_kicker=dispatch_kicker,
            dispatch_kick_allow_app_run=dispatch_kick_allow_app_run,
        )
    if current in {"NEEDS_REVIEW", "WAITING_MANUAL_PRICE", "MANUAL_REVIEW_REQUIRED"}:
        return TaskOperationResult(
            success=False,
            action="manual_price_confirm_rejected",
            task_id=task_id,
            status=current,
            changed=False,
            reply_text=MANUAL_PRICE_CONFIRM_PROMPT,
        )
    if current in {"TARGET_INFO_NEEDS_CORRECTION", "WAITING_TARGET_INFO_CORRECTION", "DRAFT_NEEDS_TARGET_INFO", "DRAFT_NEEDS_MODEL_RESOLUTION", "INVALID"}:
        return TaskOperationResult(
            success=False,
            action="target_info_correction_confirm_rejected",
            task_id=task_id,
            status=current,
            changed=False,
            reply_text=TARGET_INFO_CONFIRM_PROMPT,
        )
    if current in WAITING_CONFIRMATION_STATUSES:
        preflight_failure = _run_confirm_preflight(
            task_id,
            store=store,
            confirm_preflight_checker=confirm_preflight_checker,
            dispatch_kick_allow_app_run=dispatch_kick_allow_app_run,
        )
        if preflight_failure:
            return preflight_failure
    return _with_dispatch_kick(
        store.confirm_task(task_id, confirmed_by_open_id=sender_open_id),
        store=store,
        dispatch_kicker=dispatch_kicker,
        dispatch_kick_allow_app_run=dispatch_kick_allow_app_run,
    )


def _resolve_one_word_confirm_task_id(
    *,
    store: FeishuTaskStore,
    sender_open_id: str | None,
    business_chat_id: str | None,
    bound_message_id: str | None,
) -> str | TaskOperationResult | None:
    if bound_message_id:
        task_id = store.find_task_by_confirm_card_message_id(
            bound_message_id,
            business_chat_id=business_chat_id,
        )
        if not task_id:
            return TaskOperationResult(
                success=False,
                action="confirm_bound_task",
                changed=False,
                reply_text="未找到对应待确认任务，请回复对应确认卡，并输入“确认”。",
            )
        return task_id
    candidates = store.waiting_confirmation_tasks(
        sender_open_id=sender_open_id,
        business_chat_id=business_chat_id,
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return TaskOperationResult(
            success=False,
            action="confirm_bound_task",
            changed=False,
            reply_text="你当前有多个待确认任务，请回复对应确认卡，并输入“确认”。",
            data={"candidate_task_ids": candidates},
        )
    return None


def _run_confirm_preflight(
    task_id: str,
    *,
    store: FeishuTaskStore,
    confirm_preflight_checker: Callable[..., dict[str, Any]] | None,
    dispatch_kick_allow_app_run: bool,
) -> TaskOperationResult | None:
    if confirm_preflight_checker is None:
        return None
    try:
        preflight = confirm_preflight_checker(
            task_id=task_id,
            store=store,
            allow_app_run=dispatch_kick_allow_app_run,
        )
    except Exception as exc:
        preflight = {
            "ok": False,
            "status": "DEVICE_READY_PRECHECK_FAILED",
            "error_code": "DEVICE_READY_PRECHECK_FAILED",
            "business_reply_text": "\n".join(
                [
                    "【本次定价未开始】",
                    "原因：确认前设备就绪检查未通过。",
                    "任务暂未进入定价队列，不会占用队列。",
                    "请确认手机状态正常后，重新回复“确认”。",
                ]
            ),
            "admin_reply_text": f"确认前设备就绪检查异常：{exc}",
        }
    if bool(preflight.get("ok", False)):
        return None
    return store.record_confirm_preflight_failure(task_id, preflight)


def default_dispatch_kick(
    *,
    store: FeishuTaskStore,
    task_id: str | None = None,
    allow_app_run: bool = False,
    force_health_check: bool = True,
    background: bool | None = None,
    source: str = "feishu_confirm",
) -> dict[str, Any]:
    try:
        from feishu_pricing_dispatcher import safe_dispatch_kick
    except ImportError:  # pragma: no cover - supports package-style imports in tests.
        from scripts.feishu_pricing_dispatcher import safe_dispatch_kick

    return safe_dispatch_kick(
        store=store,
        task_id=task_id,
        allow_app_run=allow_app_run,
        force_health_check=force_health_check,
        background=background,
        source=source,
    )


def _with_dispatch_kick(
    result: TaskOperationResult,
    *,
    store: FeishuTaskStore,
    dispatch_kicker: Callable[..., dict[str, Any]] | None,
    dispatch_kick_allow_app_run: bool,
) -> TaskOperationResult:
    if not (result.success and result.status == "QUEUED" and result.task_id):
        return result
    kicker = dispatch_kicker or default_dispatch_kick
    kick_result = kicker(
        store=store,
        task_id=result.task_id,
        allow_app_run=dispatch_kick_allow_app_run,
        force_health_check=True,
        background=bool(dispatch_kick_allow_app_run),
        source="feishu_confirm" if result.action == "confirm_task" else result.action,
    )
    data = dict(result.data or {})
    data["dispatch_kick"] = kick_result
    kick_failed = _dispatch_kick_failed(kick_result)
    if result.action == "confirm_task":
        trace = store.record_confirm_dispatch_kick_trace(
            result.task_id,
            kick_result=kick_result,
            dispatch_kick_failed=kick_failed,
            failure_feedback_blocked_before_start_ack=kick_failed,
        )
        data["feishu_start_message_trace"] = trace
        data["confirm_ack_order_guard"] = {
            "start_ack_reply_preserved": True,
            "dispatch_kick_attempted": True,
            "dispatch_kick_failed": kick_failed,
            "failure_feedback_blocked_before_start_ack": kick_failed,
            "guard_code": "FEISHU_CONFIRM_DOUBLE_PATH_FEEDBACK_PREVENTED" if kick_failed else "",
        }
    reply_text = result.reply_text
    if kick_failed and result.action == "resolve_admin_intervention":
        reply_text = _dispatch_kick_failure_reply(result, kick_result)
    return TaskOperationResult(
        success=result.success,
        action=result.action,
        reply_text=reply_text,
        task_id=result.task_id,
        status=result.status,
        duplicate=result.duplicate,
        changed=result.changed,
        data=data,
    )


def _dispatch_kick_failed(kick_result: dict[str, Any]) -> bool:
    if not kick_result.get("ok", True):
        return True
    dispatch_once_result = kick_result.get("dispatch_once_result")
    return isinstance(dispatch_once_result, dict) and not dispatch_once_result.get("ok", True)


def _dispatch_kick_failure_reply(result: TaskOperationResult, kick_result: dict[str, Any]) -> str:
    failure = _dispatch_failure_payload(kick_result)
    if failure.get("business_reply_text"):
        return str(failure["business_reply_text"])
    if failure.get("status") == "CANCELLED":
        auto_cancel = failure.get("auto_cancel_result")
        if isinstance(auto_cancel, dict):
            feedback = auto_cancel.get("feedback")
            if isinstance(feedback, dict) and feedback.get("business_reply_text"):
                return str(feedback["business_reply_text"])
        return "本次定价没有开始执行，任务已自动取消，不会继续占用队列。请确认手机、瓜子登录状态正常后，重新发送目标车源并回复“确认”重新开始。"
    if set(failure.get("errors") or []) & {"ACTIVE_PRICING_LOCK_EXISTS", "ACTIVE_APP_TASK_EXISTS"}:
        task_id = result.task_id or str(failure.get("task_id") or "")
        return f"【定价已开始】{task_id}\n系统已开始自动定价，请等待结果。"
    if result.action == "resolve_admin_intervention":
        task_id = result.task_id or str(failure.get("task_id") or "")
        return format_system_not_recovered_reply(
            task_id,
            health_result=failure,
            error_codes=list(failure.get("errors") or []),
        )
    if _dispatch_failure_has_started_pricing_evidence(failure):
        return "【本次定价未完成】\n系统已开始自动定价，但未能形成完整结果，本次已安全停止，已通知管理员处理。"
    return "【本次定价未开始】\n系统暂时不能开始自动定价，请确认手机和瓜子登录状态正常后，重新发送目标车源并回复“确认”。"


def _dispatch_failure_payload(kick_result: dict[str, Any]) -> dict[str, Any]:
    dispatch_once_result = kick_result.get("dispatch_once_result")
    if isinstance(dispatch_once_result, dict):
        return dispatch_once_result
    attempts = kick_result.get("auto_recovery_attempts")
    if isinstance(attempts, list) and attempts:
        errors: list[str] = []
        task_id = None
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            if task_id is None and attempt.get("task_id"):
                task_id = attempt.get("task_id")
            errors.extend(str(item) for item in attempt.get("errors") or [] if item)
        return {
            "ok": False,
            "status": kick_result.get("message") or kick_result.get("status"),
            "task_id": kick_result.get("task_id") or task_id,
            "errors": errors or list(kick_result.get("errors") or []),
            "auto_recovery_attempts": attempts,
        }
    return {
        "ok": bool(kick_result.get("ok")),
        "status": kick_result.get("message") or kick_result.get("status"),
        "task_id": kick_result.get("task_id"),
        "errors": list(kick_result.get("errors") or []),
    }


def _dispatch_failure_has_started_pricing_evidence(failure: dict[str, Any]) -> bool:
    if not isinstance(failure, dict):
        return False
    started_statuses = {
        "S10_READY",
        "RUNNING_SECOND_STAGE",
        "CONTINUE_NEXT_REFERENCE",
        "FULL_CHAIN_PRICED_DONE",
        "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
        "ADMIN_INTERVENTION_REQUIRED",
    }
    status_values = {
        str(failure.get(key) or "")
        for key in ("status", "final_status", "current_state", "business_status", "technical_status")
    }
    if status_values & started_statuses:
        return True
    errors = {str(item) for item in failure.get("errors") or []}
    if errors & {"RESULT_MISSING_REQUIRED_PRICING_FIELDS", "REFERENCE_CARD_BINDING_NOT_UNIQUE"}:
        return True
    if bool(failure.get("started")):
        return True
    for key in ("reference_history", "candidate_reference_pool"):
        value = failure.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def should_attempt_manual_price_route(
    *,
    sender_open_id: str | None,
    chat_id: str | None,
    roles: dict[str, list[str]],
    group_bindings: FeishuGroupBindings,
) -> bool:
    return is_supervisor_open_id(sender_open_id, roles) or is_supervisor_chat(chat_id, roles=roles, group_bindings=group_bindings)


def load_feishu_roles(path: str | Path = DEFAULT_ROLES_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return _empty_feishu_roles()
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return _empty_feishu_roles()
    if text.lstrip().startswith("{"):
        return normalize_feishu_roles(json.loads(text))
    roles = _empty_feishu_roles()
    current_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line and not line.startswith("-"):
            key, raw_value = line.split(":", 1)
            current_key = _normalize_role_key(key)
            roles.setdefault(current_key, [])
            inline_value = raw_value.strip()
            if inline_value and inline_value != "[]":
                roles[current_key].extend(_normalize_role_list_value(inline_value))
            continue
        if line.startswith("-") and current_key:
            roles.setdefault(current_key, []).extend(_normalize_role_list_value(line[1:]))
    return normalize_feishu_roles(roles)


def normalize_feishu_roles(roles: dict[str, Any] | None) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    source = {_normalize_role_key(key): value for key, value in (roles or {}).items()}
    for key, default in EMPTY_ROLES.items():
        value = source.get(key, default)
        normalized[key] = _normalize_role_list_value(value)
    return normalized


def _empty_feishu_roles() -> dict[str, list[str]]:
    return {key: list(value) for key, value in EMPTY_ROLES.items()}


def _normalize_role_key(key: Any) -> str:
    return str(key or "").lstrip("\ufeff").strip()


def _normalize_role_list_value(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped == "[]":
            return []
        if stripped.startswith("[") and stripped.endswith("]"):
            items = stripped[1:-1].split(",")
        else:
            items = [stripped]
    elif isinstance(value, list):
        items = value
    else:
        return []
    normalized = []
    for item in items:
        text = str(item).strip().strip('"').strip("'")
        if text:
            normalized.append(text)
    return normalized


def is_admin_open_id(open_id: str | None, roles: dict[str, list[str]]) -> bool:
    return str(open_id or "") in set(roles.get("admin_open_ids", []))


def is_supervisor_open_id(open_id: str | None, roles: dict[str, list[str]]) -> bool:
    return str(open_id or "") in set(roles.get("supervisor_open_ids", []))


def _default_group_bindings_path_for_store(store: FeishuTaskStore) -> Path:
    try:
        return store.data_dir / "feishu_group_bindings.json"
    except Exception:
        return DEFAULT_GROUP_BINDINGS_PATH


def clean_feishu_command_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^(?:@\S+\s*)+", "", cleaned).strip()
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", cleaned).strip()
    return cleaned


def detect_group_command(text: str) -> tuple[str | None, str | None]:
    stripped = clean_feishu_command_text(text)
    if stripped == "设置本群为一线群":
        return "set_business_chat", None
    if stripped == "设置本群为主管群":
        return "set_supervisor_chat", None
    if stripped == "生成主管群绑定码":
        return "generate_supervisor_binding_code", None
    match = re.fullmatch(r"绑定一线群\s+(BD-\d{4})", stripped, flags=re.IGNORECASE)
    if match:
        return "bind_business_chat", match.group(1).upper()
    if stripped == "查看本群设置":
        return "view_group_settings", None
    return None, None


def detect_self_identity_command(text: str) -> bool:
    return clean_feishu_command_text(text) in {"查看我的ID", "我是谁", "我的ID"}


def format_self_identity_reply(
    *,
    sender_open_id: str | None,
    chat_id: str | None,
    chat_name: str | None,
    roles: dict[str, list[str]],
) -> str:
    sender = str(sender_open_id or "")
    is_admin = is_admin_open_id(sender, roles)
    is_supervisor = is_supervisor_open_id(sender, roles)
    return "\n".join(
        [
            "你的飞书身份信息：",
            f"open_id：{sender or '未获取到'}",
            f"chat_id：{mask_identifier(chat_id)}",
            f"当前群：{chat_name or '未获取到群名称'}",
            f"是否管理员：{'是' if is_admin else '否'}",
            f"是否主管：{'是' if is_supervisor else '否'}",
        ]
    )


def handle_group_command(
    command: str,
    arg: str | None,
    *,
    sender_open_id: str | None,
    chat_id: str | None,
    chat_name: str | None,
    roles: dict[str, list[str]],
    group_bindings: FeishuGroupBindings,
) -> TaskOperationResult:
    if not roles.get("admin_open_ids"):
        return TaskOperationResult(
            success=False,
            action=command,
            changed=False,
            reply_text="管理员权限未配置，请先在后台配置 admin_open_ids。",
        )
    if not is_admin_open_id(sender_open_id, roles):
        return TaskOperationResult(
            success=False,
            action=command,
            changed=False,
            reply_text="你没有权限设置群身份，请联系管理员操作。",
        )
    if not chat_id:
        return TaskOperationResult(
            success=False,
            action=command,
            changed=False,
            reply_text="当前群信息不可用，请稍后重试。",
        )
    if command == "set_business_chat":
        group_bindings.set_business_chat(chat_id=chat_id, chat_name=chat_name, created_by=str(sender_open_id or ""))
        return TaskOperationResult(
            success=True,
            action=command,
            changed=True,
            reply_text="\n".join(
                [
                    "已设置本群为一线群。",
                    "销售/评估师可以在本群发送目标车源信息。",
                    "需要人工复核时，我会同步到主管复核群。",
                ]
            ),
        )
    if command == "set_supervisor_chat":
        group_bindings.set_supervisor_chat(chat_id=chat_id, chat_name=chat_name, created_by=str(sender_open_id or ""))
        return TaskOperationResult(
            success=True,
            action=command,
            changed=True,
            reply_text="\n".join(
                [
                    "已设置本群为主管复核群。",
                    "需要人工复核的任务会同步到本群。",
                    "主管可直接回复人工确认价，例如：86000 或 8.6万。",
                ]
            ),
        )
    if command == "generate_supervisor_binding_code":
        result = group_bindings.generate_binding_code(business_chat_id=chat_id, created_by=str(sender_open_id or ""))
        if not result.ok:
            return TaskOperationResult(
                success=False,
                action=command,
                changed=False,
                reply_text="当前群尚未设置为一线群，请先发送“设置本群为一线群”。",
                data={"error_code": result.error_code},
            )
        return TaskOperationResult(
            success=True,
            action=command,
            changed=True,
            data={"binding_code": result.code},
            reply_text="\n".join(
                [
                    f"绑定码：{result.code}",
                    "请在主管复核群发送：",
                    f"绑定一线群 {result.code}",
                ]
            ),
        )
    if command == "bind_business_chat":
        result = group_bindings.bind_business_chat(code=str(arg or ""), supervisor_chat_id=chat_id, used_by=str(sender_open_id or ""))
        if not result.ok:
            return TaskOperationResult(
                success=False,
                action=command,
                changed=False,
                data={"error_code": result.error_code},
                reply_text=_binding_error_reply(result.error_code),
            )
        data = result.data or {}
        business_chat = data.get("business_chat") or {}
        supervisor_chat = data.get("supervisor_chat") or {}
        return TaskOperationResult(
            success=True,
            action=command,
            changed=True,
            data=data,
            reply_text="\n".join(
                [
                    "绑定成功。",
                    f"一线群：{business_chat.get('chat_name') or mask_identifier(data.get('business_chat_id'))}",
                    f"主管群：{supervisor_chat.get('chat_name') or mask_identifier(data.get('supervisor_chat_id'))}",
                    "后续一线群需要人工复核的任务，会自动同步到本主管群。",
                ]
            ),
        )
    if command == "view_group_settings":
        return TaskOperationResult(
            success=True,
            action=command,
            changed=False,
            reply_text=format_group_settings_reply(chat_id=chat_id, group_bindings=group_bindings),
        )
    return TaskOperationResult(success=False, action=command, changed=False, reply_text="无法识别的群设置命令。")

def handle_admin_recovery_command(
    task_id: str | None,
    *,
    sender_open_id: str | None,
    roles: dict[str, list[str]],
    store: FeishuTaskStore,
    system_health_checker: Callable[..., dict[str, Any]] = check_system_health_preflight,
    dispatch_kicker: Callable[..., dict[str, Any]] | None = None,
    dispatch_kick_allow_app_run: bool = False,
) -> TaskOperationResult:
    if not roles.get("admin_open_ids"):
        return TaskOperationResult(
            success=False,
            action="resolve_admin_intervention",
            changed=False,
            reply_text="管理员权限未配置，请先在后台配置 admin_open_ids。",
        )
    if not is_admin_open_id(sender_open_id, roles):
        return TaskOperationResult(
            success=False,
            action="resolve_admin_intervention",
            changed=False,
            reply_text="你没有权限恢复系统任务，请联系管理员处理。",
        )
    if not task_id:
        candidates = store.admin_blocked_tasks(recoverable_only=True)
        if len(candidates) != 1:
            return store.resolve_admin_intervention(
                task_id=None,
                resolved_by_open_id=sender_open_id,
                health_result=None,
                automatic=False,
            )
        task_id = candidates[0]
    status = store.load_status(task_id) or {}
    if str(status.get("status") or "") == "CANCELLED":
        feedback_result = store.ensure_cancelled_task_final_feedback(task_id)
        reply_text = feedback_result.reply_text if feedback_result.success else CANCELLED_TASK_RESEND_REPLY
        return TaskOperationResult(
            success=False,
            action="cancelled_task_confirm_rejected",
            task_id=task_id,
            status="CANCELLED",
            changed=feedback_result.changed if feedback_result.success else False,
            reply_text=reply_text,
            data={"feedback": feedback_result.data} if feedback_result.success else None,
        )
    if str(status.get("status") or "") in {"SYSTEM_BLOCKED", "ADMIN_INTERVENTION_REQUIRED"}:
        released = store.release_blocker_without_active_runner(task_id)
        if released.success:
            return released
    health = system_health_checker(dry_run=False, task_id=task_id, force=True)
    result = store.resolve_admin_intervention(
        task_id=task_id,
        resolved_by_open_id=sender_open_id,
        health_result=health,
        automatic=False,
    )
    return _with_dispatch_kick(
        result,
        store=store,
        dispatch_kicker=dispatch_kicker,
        dispatch_kick_allow_app_run=dispatch_kick_allow_app_run,
    )


def _binding_error_reply(error_code: str | None) -> str:
    if error_code == "SUPERVISOR_CHAT_NOT_INITIALIZED":
        return "当前群尚未设置为主管复核群，请先发送“设置本群为主管群”。"
    if error_code == "BINDING_CODE_EXPIRED":
        return "绑定码已过期，请在一线群重新生成主管群绑定码。"
    if error_code == "BINDING_CODE_USED":
        return "绑定码已使用，不能重复使用。"
    if error_code == "BUSINESS_CHAT_ALREADY_BOUND":
        return "该一线群已绑定主管复核群。如需重新绑定，请联系管理员走后台变更流程。"
    return "绑定码无效，请检查后重试。"


def format_group_settings_reply(*, chat_id: str | None, group_bindings: FeishuGroupBindings) -> str:
    description = group_bindings.describe_chat(chat_id)
    role = description.get("role")
    if role == "business":
        chat = description.get("chat") or {}
        supervisor_chat = description.get("bound_supervisor_chat") or {}
        lines = [
            "当前群身份：一线群",
            f"群标识：{mask_identifier(chat.get('chat_id'))}",
        ]
        if supervisor_chat:
            lines.append(f"已绑定主管群：{supervisor_chat.get('chat_name') or mask_identifier(supervisor_chat.get('chat_id'))}")
        else:
            lines.append("主管群绑定状态：未绑定")
        return "\n".join(lines)
    if role == "supervisor":
        chat = description.get("chat") or {}
        bound_business_chats = description.get("bound_business_chats") or []
        lines = [
            "当前群身份：主管群",
            f"群标识：{mask_identifier(chat.get('chat_id'))}",
        ]
        if bound_business_chats:
            lines.append("已绑定一线群：")
            lines.extend(f"- {item.get('chat_name') or mask_identifier(item.get('chat_id'))}" for item in bound_business_chats)
        else:
            lines.append("已绑定一线群：暂无")
        return "\n".join(lines)
    return "当前群身份：未设置"


def is_business_chat(chat_id: str | None, *, roles: dict[str, list[str]], group_bindings: FeishuGroupBindings) -> bool:
    return bool(chat_id and (group_bindings.is_business_chat(chat_id) or chat_id in roles.get("business_chat_ids", [])))


def is_supervisor_chat(chat_id: str | None, *, roles: dict[str, list[str]], group_bindings: FeishuGroupBindings) -> bool:
    return bool(chat_id and (group_bindings.is_supervisor_chat(chat_id) or chat_id in roles.get("supervisor_chat_ids", [])))


def parse_manual_confirm_price_text(text: str) -> tuple[int | None, str | None]:
    stripped = re.sub(r"[\s,，]", "", str(text or ""))
    if not stripped:
        return None, None
    if re.search(r"[-负]", stripped):
        return None, "MANUAL_PRICE_INVALID"
    if re.fullmatch(r"\d+(?:\.\d+)?万", stripped):
        value = float(stripped[:-1]) * 10000
        price_yuan = int(round(value))
        return (price_yuan, None) if price_yuan > 0 else (None, "MANUAL_PRICE_INVALID")
    if re.fullmatch(r"\d+元?", stripped):
        price_yuan = int(stripped[:-1] if stripped.endswith("元") else stripped)
        return (price_yuan, None) if price_yuan > 0 else (None, "MANUAL_PRICE_INVALID")
    if re.search(r"\d", stripped):
        return None, "MANUAL_PRICE_AMBIGUOUS"
    return None, None


def parse_manual_price_command(text: str) -> tuple[str | None, int | None, str | None]:
    stripped = str(text or "").strip()
    match = re.match(rf"^\s*({TASK_ID_PATTERN})(?:\s+|[：:,，])\s*(.+?)\s*$", stripped)
    if match:
        price_yuan, error = parse_manual_confirm_price_text(match.group(2))
        return match.group(1), price_yuan, error
    price_yuan, error = parse_manual_confirm_price_text(stripped)
    return None, price_yuan, error


def sync_manual_review_to_supervisor(
    task_id: str,
    *,
    store: FeishuTaskStore,
    roles: dict[str, Any] | None = None,
    group_bindings: FeishuGroupBindings | None = None,
    supervisor_review_card_message_id: str | None = None,
    send_messages: bool = False,
    dry_run: bool = True,
    message_sender: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message_sender = message_sender or send_text_message
    roles = normalize_feishu_roles(roles if roles is not None else load_feishu_roles(DEFAULT_ROLES_PATH))
    group_bindings = group_bindings or FeishuGroupBindings(_default_group_bindings_path_for_store(store), clock=store.clock)
    status = store.load_status(task_id) or {}
    business_chat_id = str(status.get("business_chat_id") or status.get("raw_chat_id") or "")
    supervisor_chat_id = group_bindings.bound_supervisor_chat_id(business_chat_id)
    if not supervisor_chat_id and business_chat_id in roles.get("business_chat_ids", []) and len(roles.get("supervisor_chat_ids", [])) == 1:
        supervisor_chat_id = roles["supervisor_chat_ids"][0]
    task_dir = store.task_dir(task_id)
    pricing_result_path = task_dir / "pricing_result.json"
    pricing_result = json.loads(pricing_result_path.read_text(encoding="utf-8")) if pricing_result_path.exists() else {}
    target_task = store.load_draft(task_id) or {}
    if not supervisor_chat_id:
        store.update_task_status_fields(
            task_id,
            fields={
                "technical_status": "SUCCEEDED",
                "business_status": "NEEDS_REVIEW",
                "recommended_next_action": "bind-supervisor-chat",
            },
        )
        business_reply = "本群尚未绑定主管复核群，请管理员完成绑定。"
        return {
            "ok": False,
            "task_id": task_id,
            "status": "NEEDS_REVIEW",
            "business_reply_text": business_reply,
            "supervisor_chat_id": None,
            "supervisor_review_card_message_id": None,
            "supervisor_reply_text": "",
            "business_reply_payload": build_text_message_payload(business_reply),
            "supervisor_reply_payload": None,
            "recommended_next_action": "bind-supervisor-chat",
        }
    mark_result = store.mark_waiting_manual_price(
        task_id,
        supervisor_chat_id=supervisor_chat_id,
        supervisor_review_card_message_id=supervisor_review_card_message_id,
    )
    supervisor_card = format_supervisor_review_card(task_id=task_id, pricing_result=pricing_result, target_task=target_task).text
    business_reply = format_manual_review_business_notice(pricing_result).text
    result = {
        "ok": mark_result.success,
        "task_id": task_id,
        "status": mark_result.status,
        "business_reply_text": business_reply,
        "supervisor_chat_id": supervisor_chat_id,
        "supervisor_review_card_message_id": (mark_result.data or {}).get("supervisor_review_card_message_id"),
        "supervisor_reply_text": supervisor_card,
        "business_reply_payload": build_text_message_payload(business_reply),
        "supervisor_reply_payload": build_text_message_payload(supervisor_card),
    }
    if send_messages:
        delivery_result = _deliver_manual_review_notices(
            task_id=task_id,
            store=store,
            pricing_result=pricing_result,
            business_chat_id=business_chat_id,
            supervisor_chat_id=supervisor_chat_id,
            business_text=business_reply,
            supervisor_text=supervisor_card,
            dry_run=dry_run,
            message_sender=message_sender,
        )
        result["manual_review_delivery_result"] = delivery_result
        result["ok"] = bool(result["ok"]) and bool(delivery_result.get("ok"))
    return result


def _deliver_manual_review_notices(
    *,
    task_id: str,
    store: FeishuTaskStore,
    pricing_result: dict[str, Any],
    business_chat_id: str,
    supervisor_chat_id: str,
    business_text: str,
    supervisor_text: str,
    dry_run: bool,
    message_sender: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    existing = store.load_manual_review_delivery(task_id)
    if isinstance(existing, dict) and existing.get("send_success"):
        return {
            "ok": True,
            "already_sent": True,
            "delivery": existing,
        }

    sent_at = store.clock().isoformat()
    idempotency_key = f"manual_review_notice:{task_id}"
    deliveries: list[dict[str, Any]] = []
    business_delivery = _send_manual_review_notice(
        task_id=task_id,
        target_group="business",
        chat_id=business_chat_id,
        text=business_text,
        dry_run=dry_run,
        idempotency_key=idempotency_key,
        sent_at=sent_at,
        message_sender=message_sender,
    )
    deliveries.append(business_delivery)
    supervisor_delivery = _send_manual_review_notice(
        task_id=task_id,
        target_group="supervisor",
        chat_id=supervisor_chat_id,
        text=supervisor_text,
        dry_run=dry_run,
        idempotency_key=idempotency_key,
        sent_at=sent_at,
        message_sender=message_sender,
    )
    deliveries.append(supervisor_delivery)

    send_success = all(item.get("send_success") for item in deliveries)
    errors = [str(item.get("error")) for item in deliveries if item.get("error")]
    delivery = {
        "task_id": task_id,
        "delivery_type": "manual_review_notice",
        "target_group": "business_and_supervisor",
        "group_type": "manual_review",
        "chat_id": {
            "business": business_chat_id,
            "supervisor": supervisor_chat_id,
        },
        "idempotency_key": idempotency_key,
        "sent_at": sent_at,
        "send_success": send_success,
        "message_id": {
            item["target_group"]: item.get("message_id")
            for item in deliveries
            if item.get("message_id")
        },
        "error": "; ".join(errors) if errors else "",
        "payload_preview": {
            "business": business_text[:500],
            "supervisor": supervisor_text[:500],
        },
        "deliveries": deliveries,
    }
    store.write_manual_review_delivery(task_id, delivery)
    reason_code = _manual_review_reason_code(pricing_result)
    reason_business = "系统已完成参考车采集，但当前边界/价格结果需要主管确认。"
    status = store.record_manual_review_delivery_status(
        task_id,
        delivery=delivery,
        reason_business=reason_business,
        reason_code=reason_code,
    )
    return {
        "ok": send_success,
        "already_sent": False,
        "delivery": delivery,
        "status": status,
    }


def _send_manual_review_notice(
    *,
    task_id: str,
    target_group: str,
    chat_id: str,
    text: str,
    dry_run: bool,
    idempotency_key: str,
    sent_at: str,
    message_sender: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    if not chat_id:
        return {
            "task_id": task_id,
            "delivery_type": "manual_review_notice",
            "target_group": target_group,
            "group_type": target_group,
            "chat_id": chat_id,
            "idempotency_key": idempotency_key,
            "sent_at": sent_at,
            "send_success": False,
            "message_id": None,
            "error": "CHAT_ID_MISSING",
            "payload_preview": text[:500],
        }
    send_result = message_sender(text=text, chat_id=chat_id, dry_run=dry_run)
    ok = bool(send_result.get("ok"))
    return {
        "task_id": task_id,
        "delivery_type": "manual_review_notice",
        "target_group": target_group,
        "group_type": target_group,
        "chat_id": chat_id,
        "idempotency_key": idempotency_key,
        "sent_at": sent_at,
        "send_success": ok,
        "message_id": send_result.get("message_id"),
        "error": "" if ok else str(send_result.get("error_code") or send_result.get("message") or "FEISHU_SEND_FAILED"),
        "payload_preview": text[:500],
        "send_result": send_result,
    }


def _manual_review_reason_code(pricing_result: dict[str, Any]) -> str:
    reasons = manual_review_reasons(pricing_result)
    for reason in reasons:
        text = str(reason)
        if text.upper() == text and "_" in text:
            return text
    return str(reasons[0]) if reasons else "MANUAL_REVIEW_REQUIRED"


def _handle_manual_price(
    price_yuan: int,
    *,
    store: FeishuTaskStore,
    sender_open_id: str | None,
    supervisor_chat_id: str | None,
    reply_to_message_id: str | None,
    parent_message_id: str | None,
    task_id_from_text: str | None,
    manual_confirm_raw_text: str | None,
    roles: dict[str, list[str]],
    group_bindings: FeishuGroupBindings,
) -> TaskOperationResult:
    if not roles.get("supervisor_open_ids"):
        return TaskOperationResult(
            success=False,
            action="manual_price_supervisor_not_configured",
            changed=False,
            reply_text="请管理员先配置主管 open_id，当前无法人工确认价格。",
        )
    if not is_supervisor_open_id(sender_open_id, roles):
        return TaskOperationResult(
            success=False,
            action="manual_price_permission_denied",
            changed=False,
            reply_text="当前任务需要主管复核价格，请主管回复人工确认价。",
        )
    if not is_supervisor_chat(supervisor_chat_id, roles=roles, group_bindings=group_bindings):
        return TaskOperationResult(
            success=False,
            action="manual_price_wrong_chat",
            changed=False,
            reply_text="请到主管复核群回复对应复核卡片完成价格确认。",
        )

    review_task_id = _bind_manual_price_task(
        store=store,
        supervisor_chat_id=supervisor_chat_id,
        reply_to_message_id=reply_to_message_id,
        parent_message_id=parent_message_id,
        task_id_from_text=task_id_from_text,
    )
    if isinstance(review_task_id, TaskOperationResult):
        return review_task_id
    if not review_task_id:
        return TaskOperationResult(
            success=False,
            action="manual_price_ignored",
            changed=False,
            reply_text="当前没有待人工复核的任务。请回复对应复核卡片，或输入：FSxxxx 8.6万",
        )

    status = store.load_status(review_task_id) or {}
    current_status = str(status.get("status") or "")
    if current_status == "RESULT_SENT":
        return TaskOperationResult(
            success=False,
            action="manual_price_result_sent",
            task_id=review_task_id,
            status=current_status,
            changed=False,
            reply_text="当前任务结果已发送，如需修改请联系管理员。",
        )
    if current_status == "MANUAL_REVIEW_CONFIRMED":
        confirmed_price = _confirmed_manual_price_yuan(store, review_task_id)
        if confirmed_price == price_yuan:
            return TaskOperationResult(
                success=True,
                action="manual_price_already_confirmed_same_price",
                task_id=review_task_id,
                status=current_status,
                changed=False,
                reply_text=f"该任务已确认人工复核价：{confirmed_price} 元",
            )
        return TaskOperationResult(
            success=False,
            action="manual_price_already_confirmed",
            task_id=review_task_id,
            status=current_status,
            changed=False,
            reply_text="当前任务已人工确认，如需修改请联系管理员走后台改价流程。",
        )
    if current_status not in {"NEEDS_REVIEW", "WAITING_MANUAL_PRICE", "MANUAL_REVIEW_REQUIRED"}:
        return TaskOperationResult(
            success=False,
            action="manual_price_not_waiting_review",
            task_id=review_task_id,
            status=current_status,
            changed=False,
            reply_text="当前任务不在主管复核状态。",
        )

    pricing_result_path = store.task_dir(review_task_id) / "pricing_result.json"
    system_price = None
    if pricing_result_path.exists():
        try:
            pricing_payload = json.loads(pricing_result_path.read_text(encoding="utf-8"))
            system_price = pricing_payload.get("system_suggested_purchase_price_yuan") or pricing_payload.get("suggested_purchase_price_yuan")
            if not system_price and isinstance(pricing_payload.get("pricing"), dict):
                system_price = pricing_payload["pricing"].get("suggested_purchase_price_yuan")
        except json.JSONDecodeError:
            system_price = None
    note = (
        f"系统测算价 {int(system_price)} 元，人工确认收车价 {price_yuan} 元。"
        if system_price
        else f"人工确认收车价 {price_yuan} 元。"
    )
    runner = PricingRunner(task_root=store.base_dir, data_dir=store.data_dir)
    result = runner.manual_confirm_price(
        review_task_id,
        manual_confirm_price=price_yuan,
        manual_review_note=note,
        manual_confirm_by=sender_open_id or "feishu_supervisor",
        manual_confirm_raw_text=manual_confirm_raw_text,
        manual_confirm_task_id=review_task_id,
        manual_confirmed_by_role="supervisor",
    )
    if not result.get("ok"):
        return TaskOperationResult(
            success=False,
            action="manual_confirm_price",
            task_id=review_task_id,
            status=str(result.get("status") or ""),
            changed=False,
            reply_text="人工复核确认失败，请联系后台检查本地任务状态。",
            data=result,
        )
    if result.get("system_suggested_price_missing"):
        reply_lines = [
            "人工复核已确认",
            f"最终收车价：{result.get('manual_confirmed_purchase_price_yuan')} 元",
            "确认来源：主管人工报价",
            "状态：已确认，待发送/回写飞书",
        ]
    else:
        reply_lines = [
            "人工复核已确认",
            f"系统测算价：{result.get('system_suggested_purchase_price_yuan')} 元",
            f"人工确认价：{result.get('manual_confirmed_purchase_price_yuan')} 元",
            f"调整金额：{result.get('manual_adjustment_yuan')} 元",
            "状态：已确认，待发送/回写飞书",
        ]
    return TaskOperationResult(
        success=True,
        action="manual_confirm_price",
        task_id=review_task_id,
        status="MANUAL_REVIEW_CONFIRMED",
        changed=True,
        data=result,
        reply_text="\n".join(reply_lines),
    )


def _confirmed_manual_price_yuan(store: FeishuTaskStore, task_id: str) -> int | None:
    pricing_result_path = store.task_dir(task_id) / "pricing_result.json"
    if not pricing_result_path.exists():
        return None
    try:
        pricing_payload = json.loads(pricing_result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    for key in ("manual_price_yuan", "manual_confirmed_purchase_price_yuan", "final_purchase_price_yuan"):
        value = pricing_payload.get(key)
        if value is None and isinstance(pricing_payload.get("pricing"), dict):
            value = pricing_payload["pricing"].get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _bind_manual_price_task(
    *,
    store: FeishuTaskStore,
    supervisor_chat_id: str | None,
    reply_to_message_id: str | None,
    parent_message_id: str | None,
    task_id_from_text: str | None,
) -> str | TaskOperationResult | None:
    bound_message_id = reply_to_message_id or parent_message_id
    if bound_message_id:
        return store.find_task_by_supervisor_review_card_message_id(
            bound_message_id,
            supervisor_chat_id=supervisor_chat_id,
        )
    if task_id_from_text:
        return task_id_from_text if store.load_status(task_id_from_text) else None
    candidates = store.manual_price_waiting_tasks(supervisor_chat_id=supervisor_chat_id)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return TaskOperationResult(
            success=False,
            action="manual_price_multiple_waiting_review",
            changed=False,
            reply_text="当前有多个待复核任务，请回复对应复核卡片，或输入：FSxxxx 8.6万",
            data={"candidate_task_ids": candidates},
        )
    return None


def _to_reply_payload(result: TaskOperationResult) -> dict[str, Any]:
    return {
        "ok": result.success,
        "action": result.action,
        "task_id": result.task_id,
        "status": result.status,
        "duplicate": result.duplicate,
        "changed": result.changed,
        "data": result.data or {},
        "reply_text": result.reply_text,
        "reply_payload": build_text_message_payload(result.reply_text),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Handle a local simulated Feishu event JSON.")
    parser.add_argument("--input", default=None, help="Local JSON event file. If omitted, stdin is used.")
    parser.add_argument("--data-root", default=str(DEFAULT_TASK_ROOT), help="Task store root.")
    parser.add_argument("--reply-text", action="store_true", help="Print reply text only.")
    args = parser.parse_args(argv)

    raw = Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read()
    event = json.loads(raw)
    store = FeishuTaskStore(args.data_root)
    result = handle_event(event, store=store)
    if args.reply_text:
        print(result["reply_text"])
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
