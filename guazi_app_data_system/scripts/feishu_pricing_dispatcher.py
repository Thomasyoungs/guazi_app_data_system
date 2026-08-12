"""Serial dispatcher for confirmed Feishu pricing tasks.

Default mode is dry-run. Real APP automation requires --allow-app-run.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable
import uuid

try:
    from admin_intervention_router import (
        ADMIN_INTERVENTION_REQUIRED,
        SYSTEM_BLOCKED,
        classify_admin_intervention,
        write_admin_intervention_feedback,
    )
    from current_target_task_builder import build_current_target_task
    from feishu_gateway import sync_manual_review_to_supervisor
    from feishu_task_store import DEFAULT_TASK_ROOT, FeishuTaskStore
    from pricing_result_collector import (
        CONTINUE_NEXT_REFERENCE,
        CROSS_TASK_PRICING_RESULT_REJECTED,
        SECOND_STAGE_COLLECTION_INCOMPLETE,
        TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED,
        is_automatic_pricing_terminal_success,
        pricing_result_business_status,
        result_task_id_candidates,
        stamp_result_task_scope,
        target_fingerprint_candidates,
        target_fingerprints_from_artifacts,
        validate_result_task_scope,
    )
    from pricing_runner import DATA_DIR, DEFAULT_RUNTIME_LOCK, PricingRunner, now_iso
    from system_health_preflight import check_system_health_preflight
    from target_info_correction_feedback import TARGET_INFO_NEEDS_CORRECTION, is_target_info_error
except ImportError:  # pragma: no cover - supports package-style imports in tests.
    from scripts.admin_intervention_router import (
        ADMIN_INTERVENTION_REQUIRED,
        SYSTEM_BLOCKED,
        classify_admin_intervention,
        write_admin_intervention_feedback,
    )
    from scripts.current_target_task_builder import build_current_target_task
    from scripts.feishu_gateway import sync_manual_review_to_supervisor
    from scripts.feishu_task_store import DEFAULT_TASK_ROOT, FeishuTaskStore
    from scripts.pricing_result_collector import (
        CONTINUE_NEXT_REFERENCE,
        CROSS_TASK_PRICING_RESULT_REJECTED,
        SECOND_STAGE_COLLECTION_INCOMPLETE,
        TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED,
        is_automatic_pricing_terminal_success,
        pricing_result_business_status,
        result_task_id_candidates,
        stamp_result_task_scope,
        target_fingerprint_candidates,
        target_fingerprints_from_artifacts,
        validate_result_task_scope,
    )
    from scripts.pricing_runner import DATA_DIR, DEFAULT_RUNTIME_LOCK, PricingRunner, now_iso
    from scripts.system_health_preflight import check_system_health_preflight
    from scripts.target_info_correction_feedback import TARGET_INFO_NEEDS_CORRECTION, is_target_info_error


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


DISPATCHER_LOOP_HEARTBEAT_FILE = "feishu_dispatcher_loop_heartbeat.json"
DISPATCH_KICK_LOG_FILE = "dispatch_kick_log.jsonl"
SECOND_STAGE_REFERENCE_LOOP_ABSOLUTE_SAFETY_LIMIT = 20
ALL_TRISAME_REFERENCES_EXHAUSTED_NEEDS_REVIEW = "ALL_TRISAME_REFERENCES_EXHAUSTED_NEEDS_REVIEW"
SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED = "SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED"
SECOND_STAGE_CONTINUATION_STATE_MISSING = "SECOND_STAGE_CONTINUATION_STATE_MISSING"
V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE = "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE"
REFERENCE_LOOP_STATE_RESET_DETECTED = "REFERENCE_LOOP_STATE_RESET_DETECTED"


def dispatcher_loop_is_running(*, data_dir: str | Path = DATA_DIR, stale_seconds: int = 30) -> bool:
    heartbeat_path = Path(data_dir) / DISPATCHER_LOOP_HEARTBEAT_FILE
    if not heartbeat_path.exists():
        return False
    try:
        payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not payload.get("running"):
        return False
    heartbeat_at = str(payload.get("heartbeat_at") or "")
    if not heartbeat_at:
        return False
    try:
        parsed = datetime.fromisoformat(heartbeat_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    parsed = parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return (_now_utc() - parsed).total_seconds() <= stale_seconds


def safe_dispatch_kick(
    *,
    store: FeishuTaskStore,
    task_id: str | None = None,
    allow_app_run: bool = False,
    dry_run: bool | None = None,
    force_health_check: bool = True,
    loop_running_checker: Callable[[], bool] | None = None,
    dispatcher_factory: Callable[..., Any] | None = None,
    background: bool | None = None,
    source: str = "feishu_confirm",
) -> dict[str, Any]:
    effective_dry_run = (not allow_app_run) if dry_run is None else dry_run
    confirm_source = str(source or "") == "feishu_confirm"
    factory = dispatcher_factory or FeishuPricingDispatcher
    dispatcher_kwargs = {
        "task_root": store.base_dir,
        "data_dir": store.data_dir,
        "clock": store.clock,
        "auto_send_result": bool(allow_app_run and not effective_dry_run),
        "send_result_live": bool(allow_app_run and not effective_dry_run),
    }
    loop_running = (
        bool(loop_running_checker())
        if loop_running_checker is not None
        else dispatcher_loop_is_running(data_dir=store.data_dir)
    )
    payload: dict[str, Any] = {
        "ok": True,
        "action": "safe_dispatch_kick",
        "task_id": task_id,
        "dispatcher_loop_running": loop_running,
        "dispatch_once_called": False,
        "dispatch_once_started_background": False,
        "allow_app_run": allow_app_run,
        "dry_run": effective_dry_run,
        "force_health_check": force_health_check,
        "source": source,
        "started_loop": False,
        "confirm_handler_sync_dispatch_forbidden": bool(confirm_source),
        "start_ack_dispatch_kick_sync_blocked": False,
    }
    if loop_running:
        if force_health_check and not confirm_source:
            blocked_task_ids = store.admin_blocked_tasks(recoverable_only=False)
            if blocked_task_ids:
                try:
                    dispatcher = factory(**dispatcher_kwargs)
                    attempts = dispatcher._attempt_auto_recover_blocked_tasks(  # noqa: SLF001 - confirm kick coordinates manual recovery.
                        blocked_task_ids,
                        force_health_check=True,
                    )
                    remaining_blocked = store.admin_blocked_tasks(recoverable_only=False)
                except Exception as exc:
                    payload.update(
                        {
                            "ok": False,
                            "errors": ["DISPATCH_KICK_FORCE_HEALTH_CHECK_FAILED"],
                            "message": str(exc),
                            "blocked_task_ids": blocked_task_ids,
                        }
                    )
                else:
                    payload["auto_recovery_attempts"] = attempts
                    payload["blocked_task_ids"] = remaining_blocked
                    if remaining_blocked:
                        payload.update(
                            {
                                "ok": False,
                                "errors": ["ADMIN_INTERVENTION_TASK_EXISTS"],
                                "message": "DISPATCHER_LOOP_RUNNING_BLOCKED_TASK_NOT_RECOVERED",
                            }
                        )
                    else:
                        payload["message"] = "DISPATCHER_LOOP_ALREADY_RUNNING_BLOCKED_TASK_RECOVERED"
                _append_dispatch_kick_log(store, payload)
                return payload
        payload["message"] = "DISPATCHER_LOOP_ALREADY_RUNNING"
        if confirm_source:
            payload["kick_requested"] = True
            payload["loop_running"] = True
            payload["force_health_check_deferred_for_start_ack"] = bool(force_health_check)
        _append_dispatch_kick_log(store, payload)
        return payload

    run_background = (
        bool(background)
        if background is not None
        else bool(allow_app_run and not effective_dry_run and (confirm_source or not force_health_check))
    )
    if run_background:
        thread = threading.Thread(
            target=_run_dispatch_once_background,
            kwargs={
                "store": store,
                "payload": dict(payload),
                "dispatcher_factory": factory,
                "dispatcher_kwargs": dispatcher_kwargs,
                "dry_run": effective_dry_run,
                "allow_app_run": allow_app_run,
                "force_health_check": force_health_check,
            },
            name=f"feishu-dispatch-kick-{task_id or 'queue'}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception as exc:
            payload.update(
                {
                    "ok": False,
                    "errors": ["DISPATCH_KICK_BACKGROUND_START_FAILED"],
                    "message": str(exc),
                }
            )
            _write_dispatch_kick_admin_notice(store, task_id, "后台定价调度服务未运行，请检查服务。")
        else:
            payload.update(
                {
                    "message": "DISPATCH_ONCE_STARTED_BACKGROUND",
                    "dispatch_once_started_background": True,
                    "thread_name": thread.name,
                }
            )
        _append_dispatch_kick_log(store, payload)
        return payload

    try:
        dispatcher = factory(**dispatcher_kwargs)
        dispatch_once_result = dispatcher.dispatch_once(
            dry_run=effective_dry_run,
            allow_app_run=allow_app_run,
            force_health_check=force_health_check,
        )
    except Exception as exc:
        payload.update(
            {
                "ok": False,
                "dry_run": effective_dry_run,
                "errors": ["DISPATCH_KICK_FAILED"],
                "message": str(exc),
            }
        )
        _write_dispatch_kick_admin_notice(store, task_id, "后台定价调度服务未运行，请检查服务。")
        _append_dispatch_kick_log(store, payload)
        return payload
    payload.update(
        {
            "ok": bool(dispatch_once_result.get("ok", True)),
            "dry_run": effective_dry_run,
            "dispatch_once_called": True,
            "dispatch_once_result": dispatch_once_result,
        }
    )
    _append_dispatch_kick_log(store, payload)
    return payload


def _run_dispatch_once_background(
    *,
    store: FeishuTaskStore,
    payload: dict[str, Any],
    dispatcher_factory: Callable[..., Any],
    dispatcher_kwargs: dict[str, Any],
    dry_run: bool,
    allow_app_run: bool,
    force_health_check: bool,
) -> None:
    try:
        dispatcher = dispatcher_factory(**dispatcher_kwargs)
        dispatch_once_result = dispatcher.dispatch_once(
            dry_run=dry_run,
            allow_app_run=allow_app_run,
            force_health_check=force_health_check,
        )
    except Exception as exc:  # pragma: no cover - defensive production path.
        payload.update(
            {
                "ok": False,
                "action": "safe_dispatch_kick_background_failed",
                "dispatch_once_called": False,
                "dispatch_once_started_background": True,
                "errors": ["DISPATCH_KICK_BACKGROUND_FAILED"],
                "message": str(exc),
            }
        )
        _write_dispatch_kick_admin_notice(store, payload.get("task_id"), "后台定价调度服务未运行，请检查服务。")
    else:
        payload.update(
            {
                "ok": bool(dispatch_once_result.get("ok", True)),
                "action": "safe_dispatch_kick_background_complete",
                "dispatch_once_called": True,
                "dispatch_once_started_background": True,
                "dispatch_once_result": dispatch_once_result,
            }
        )
    _append_dispatch_kick_log(store, payload)


def _write_dispatch_kick_admin_notice(store: FeishuTaskStore, task_id: str | None, message: str) -> None:
    if not task_id:
        return
    task_dir = store.task_dir(str(task_id))
    task_dir.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        [
            f"【管理员处理】{task_id}",
            message,
            "处理完成后回复：确认。",
        ]
    )
    (task_dir / "dispatch_kick_admin_notice.preview.txt").write_text(text + "\n", encoding="utf-8")


def _append_dispatch_kick_log(store: FeishuTaskStore, payload: dict[str, Any]) -> None:
    row = dict(payload)
    row["logged_at"] = now_iso(store.clock)
    log_path = store.base_dir / DISPATCH_KICK_LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class FeishuPricingDispatcher:
    def __init__(
        self,
        *,
        task_root: str | Path = DEFAULT_TASK_ROOT,
        data_dir: str | Path = DATA_DIR,
        runtime_lock_path: str | Path = DEFAULT_RUNTIME_LOCK,
        clock: Callable[[], datetime] | None = None,
        runner: PricingRunner | None = None,
        supervisor_sync: Callable[..., dict[str, Any]] = sync_manual_review_to_supervisor,
        system_health_checker: Callable[..., dict[str, Any]] = check_system_health_preflight,
        first_stage_script: str | Path | None = None,
        first_stage_result_path: str | Path | None = None,
        second_stage_script: str | Path | None = None,
        second_stage_result_path: str | Path | None = None,
        auto_send_result: bool = False,
        send_result_live: bool = False,
    ) -> None:
        self.clock = clock or _now_utc
        self.store = FeishuTaskStore(task_root, clock=self.clock)
        self.runner = runner or PricingRunner(
            task_root=task_root,
            data_dir=data_dir,
            runtime_lock_path=runtime_lock_path,
            clock=self.clock,
        )
        self.supervisor_sync = supervisor_sync
        self.system_health_checker = system_health_checker
        self.first_stage_script = first_stage_script
        self.first_stage_result_path = first_stage_result_path
        self.second_stage_script = second_stage_script
        self.second_stage_result_path = second_stage_result_path
        self.auto_send_result = auto_send_result
        self.send_result_live = send_result_live

    @property
    def dispatcher_log_path(self) -> Path:
        return self.store.base_dir / "dispatcher_log.jsonl"

    def dispatch_once(
        self,
        *,
        dry_run: bool = True,
        allow_app_run: bool = False,
        force_health_check: bool = False,
    ) -> dict[str, Any]:
        dispatch_run_id = self._new_dispatch_run_id()
        queued = self.store.queued_tasks()
        blocked_task_ids = self.store.admin_blocked_tasks(recoverable_only=False)
        auto_recovery_attempts: list[dict[str, Any]] = []
        active_task_id = self.store.active_app_task()
        if active_task_id:
            payload = {
                "ok": False,
                "dry_run": dry_run or not allow_app_run,
                "allow_app_run": allow_app_run,
                "force_health_check": force_health_check,
                "dispatch_run_id": dispatch_run_id,
                "status": "BLOCKED",
                "errors": ["ACTIVE_APP_TASK_EXISTS"],
                "active_task_id": active_task_id,
                "queued_task_ids": queued,
                "started": False,
            }
            self._append_dispatch_log(payload)
            return payload
        if self._runtime_lock_exists():
            payload = {
                "ok": False,
                "dry_run": dry_run or not allow_app_run,
                "allow_app_run": allow_app_run,
                "force_health_check": force_health_check,
                "dispatch_run_id": dispatch_run_id,
                "status": "BLOCKED",
                "errors": ["ACTIVE_PRICING_LOCK_EXISTS"],
                "queued_task_ids": queued,
                "blocked_task_ids": blocked_task_ids,
                "started": False,
            }
            self._append_dispatch_log(payload)
            return payload
        if blocked_task_ids:
            released_blockers = self._release_blocked_tasks_without_active_runner(
                blocked_task_ids,
                dry_run=not self.send_result_live,
            )
            if released_blockers:
                auto_recovery_attempts.extend(released_blockers)
            queued = self.store.queued_tasks()
            blocked_task_ids = self.store.admin_blocked_tasks(recoverable_only=False)
        if blocked_task_ids:
            blocked_summaries = [self._blocked_task_summary(task_id) for task_id in blocked_task_ids]
            status = SYSTEM_BLOCKED if any(item.get("status") == SYSTEM_BLOCKED for item in blocked_summaries) else ADMIN_INTERVENTION_REQUIRED
            payload = {
                "ok": False,
                "dry_run": dry_run or not allow_app_run,
                "allow_app_run": allow_app_run,
                "force_health_check": force_health_check,
                "dispatch_run_id": dispatch_run_id,
                "status": status,
                "errors": ["ADMIN_INTERVENTION_TASK_EXISTS"],
                "blocked_task_ids": blocked_task_ids,
                "blocked_tasks": blocked_summaries,
                "queued_task_ids": queued,
                "started": False,
                "recommended_next_action": "wait-admin-resolution",
                "auto_recovery_attempts": auto_recovery_attempts,
                "next_health_check_at": self._earliest_next_health_check(blocked_summaries),
            }
            self._append_dispatch_log(payload)
            return payload
        if not queued:
            if allow_app_run and not dry_run:
                pending_manual_notice = self.store.manual_review_notice_pending_tasks()
                if pending_manual_notice:
                    notice_task_id = pending_manual_notice[0]
                    supervisor_sync_result = self.supervisor_sync(
                        notice_task_id,
                        store=self.store,
                        send_messages=True,
                        dry_run=not self.send_result_live,
                    )
                    payload = {
                        "ok": bool(supervisor_sync_result.get("ok", True)),
                        "dry_run": False,
                        "allow_app_run": allow_app_run,
                        "force_health_check": force_health_check,
                        "dispatch_run_id": dispatch_run_id,
                        "task_id": notice_task_id,
                        "selected_task_id": notice_task_id,
                        "status": str(supervisor_sync_result.get("status") or "WAITING_MANUAL_PRICE"),
                        "queued_task_ids": [],
                        "manual_review_notice_backfill": True,
                        "supervisor_sync_result": supervisor_sync_result,
                        "started": False,
                    }
                    self._append_dispatch_log(payload)
                    return payload
            payload = {
                "ok": True,
                "dry_run": dry_run or not allow_app_run,
                "allow_app_run": allow_app_run,
                "force_health_check": force_health_check,
                "dispatch_run_id": dispatch_run_id,
                "status": "NO_QUEUED_TASK",
                "queued_task_ids": [],
                "started": False,
                "message": "NO_QUEUED_TASK",
            }
            if auto_recovery_attempts:
                payload["auto_recovery_attempts"] = auto_recovery_attempts
            self._append_dispatch_log(payload)
            return payload

        task_id = queued[0]
        if dry_run or not allow_app_run:
            draft = self.store.load_draft(task_id) or {}
            build_result = build_current_target_task(draft, clock=self.clock)
            if not build_result.valid:
                correction = self.runner._write_target_info_correction_error(  # noqa: SLF001 - dispatcher owns pre-APP validation.
                    task_id,
                    "dispatcher-dry-run-target-info-validation",
                    "QUEUED",
                    ["MISSING_REQUIRED_FIELDS"],
                    build_result.missing_fields,
                    draft=draft,
                )
                payload = {
                    "ok": False,
                    "dry_run": True,
                    "allow_app_run": allow_app_run,
                    "force_health_check": force_health_check,
                    "dispatch_run_id": dispatch_run_id,
                    "status": TARGET_INFO_NEEDS_CORRECTION,
                    "selected_task_id": task_id,
                    "task_id": task_id,
                    "queued_task_ids": queued,
                    "would_prepare_current_target_task": False,
                    "would_run_first_stage": False,
                    "would_run_second_stage": False,
                    "started": False,
                    "step_result": correction,
                    "errors": correction.get("errors", []),
                }
                self._append_dispatch_log(payload)
                self._write_dispatcher_result(task_id, payload)
                return payload
            payload = {
                "ok": True,
                "dry_run": True,
                "allow_app_run": allow_app_run,
                "force_health_check": force_health_check,
                "dispatch_run_id": dispatch_run_id,
                "status": "DRY_RUN_READY",
                "selected_task_id": task_id,
                "task_id": task_id,
                "queued_task_ids": queued,
                "would_prepare_current_target_task": True,
                "would_run_first_stage": False,
                "would_run_second_stage": False,
                "started": False,
            }
            if auto_recovery_attempts:
                payload["auto_recovery_attempts"] = auto_recovery_attempts
            self._append_dispatch_log(payload)
            return payload

        health = self.system_health_checker(dry_run=False)
        if not health.get("ok", False):
            cancel_result = self.store.auto_cancel_not_started_system_precheck_failure(
                task_id,
                errors=list(health.get("errors") or []),
                result=health,
                force_not_started=True,
                dry_run=not self.send_result_live,
            )
            if cancel_result.success:
                payload = {
                    "ok": False,
                    "dry_run": False,
                    "allow_app_run": True,
                    "force_health_check": force_health_check,
                    "dispatch_run_id": dispatch_run_id,
                    "task_id": task_id,
                    "selected_task_id": task_id,
                    "queued_task_ids": queued,
                    "status": "CANCELLED",
                    "failed_step": "system-health-preflight",
                    "errors": list(health.get("errors") or ["SYSTEM_HEALTH_PREFLIGHT_FAILED"]),
                    "started": False,
                    "cancel_reason": "SYSTEM_PRECHECK_FAILED_NOT_STARTED",
                    "blocks_queue": False,
                    "recommended_next_action": "resend-target-info",
                    "step_result": health,
                    "auto_cancel_result": cancel_result.data,
                }
                payload.update(self._cancel_feedback_payload_fields(cancel_result))
                self._append_dispatch_log(payload)
                self._write_dispatcher_result(task_id, payload)
                return payload
            admin_delivery = self._mark_admin_intervention(
                task_id,
                errors=list(health.get("errors") or []),
                result=health,
            )
            payload = self._failure_payload(
                task_id=task_id,
                dispatch_run_id=dispatch_run_id,
                queued=queued,
                step="system-health-preflight",
                result=health,
                admin_intervention_feedback=admin_delivery,
            )
            self._append_dispatch_log(payload)
            self._write_dispatcher_result(task_id, payload)
            return payload

        payload = self._run_queue_head(
            task_id=task_id,
            queued=queued,
            dispatch_run_id=dispatch_run_id,
            manual_review_send_messages=bool(allow_app_run and not dry_run),
            manual_review_dry_run=not self.send_result_live,
        )
        if auto_recovery_attempts:
            payload["auto_recovery_attempts"] = auto_recovery_attempts
        self._append_dispatch_log(payload)
        self._write_dispatcher_result(task_id, payload)
        return payload

    def _runtime_lock_exists(self) -> bool:
        return bool(getattr(self.runner, "runtime_lock_path", Path()).exists())

    def _release_blocked_tasks_without_active_runner(
        self,
        blocked_task_ids: list[str],
        *,
        dry_run: bool | None = None,
    ) -> list[dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        effective_dry_run = not self.send_result_live if dry_run is None else bool(dry_run)
        for task_id in blocked_task_ids:
            result = self.store.release_blocker_without_active_runner(task_id, dry_run=effective_dry_run)
            if not result.success:
                continue
            feedback = ((result.data or {}).get("feedback") or {}) if isinstance(result.data, dict) else {}
            attempts.append(
                {
                    "task_id": task_id,
                    "attempted": True,
                    "action": "release_blocker_without_active_runner",
                    "status_after": "CANCELLED",
                    "cancel_reason": "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER",
                    "canonical_error_code": feedback.get("canonical_error_code"),
                    "human_reason": feedback.get("human_reason"),
                    "retry_instruction": feedback.get("retry_instruction"),
                    "business_reply_text": feedback.get("business_reply_text"),
                    "final_feedback_delivery_dry_run": bool(feedback.get("dry_run")),
                    "final_feedback_send_attempted": bool(feedback.get("final_feedback_send_attempted")),
                    "final_feedback_sent": bool(feedback.get("final_feedback_sent")),
                    "blocks_queue": False,
                    "changed": result.changed,
                    "errors": list((result.data or {}).get("error_codes") or []),
                    "current_target_task_cleared": bool((result.data or {}).get("current_target_task_cleared")),
                }
            )
        return attempts

    def _auto_cancel_not_started_blocked_tasks(self, blocked_task_ids: list[str]) -> list[dict[str, Any]]:
        return self._release_blocked_tasks_without_active_runner(blocked_task_ids)

    def _attempt_auto_recover_blocked_tasks(
        self,
        blocked_task_ids: list[str],
        *,
        force_health_check: bool = False,
    ) -> list[dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        if self._runtime_lock_exists():
            return [
                {
                    "task_id": task_id,
                    "attempted": False,
                    "status": (self.store.load_status(task_id) or {}).get("status"),
                    "reason": "ACTIVE_PRICING_LOCK_EXISTS",
                    "force_health_check": force_health_check,
                }
                for task_id in blocked_task_ids
            ]
        released = self._release_blocked_tasks_without_active_runner(blocked_task_ids, dry_run=not self.send_result_live)
        if released:
            return released
        ready_task_ids = set(self.store.system_blocked_tasks_ready_for_health_check(force=force_health_check))
        for task_id in blocked_task_ids:
            status = self.store.load_status(task_id) or {}
            cancel_result = self.store.auto_cancel_not_started_system_precheck_failure(
                task_id,
                dry_run=not self.send_result_live,
            )
            if cancel_result.success:
                attempts.append(
                    {
                        "task_id": task_id,
                        "attempted": True,
                        "action": "auto_cancel_not_started_system_precheck_failure",
                        "status_before": status.get("status"),
                        "status_after": "CANCELLED",
                        "cancel_reason": "SYSTEM_PRECHECK_FAILED_NOT_STARTED",
                        "blocks_queue": False,
                        "changed": cancel_result.changed,
                        "errors": list((cancel_result.data or {}).get("error_codes") or []),
                    }
                )
                continue
            if task_id not in ready_task_ids:
                attempts.append(
                    {
                        "task_id": task_id,
                        "attempted": False,
                        "status": status.get("status"),
                        "reason": "HEALTH_CHECK_COOLDOWN_OR_NOT_AUTO_RECOVERABLE",
                        "force_health_check": force_health_check,
                        "next_health_check_at": self.store.system_blocked_next_health_check_at(status),
                    }
                )
                continue
            error_codes = self.store._admin_error_codes(status)  # noqa: SLF001 - dispatcher coordinates recovery policy.
            health = self.system_health_checker(
                dry_run=False,
                task_id=task_id,
                blocked_status=status,
                error_codes=error_codes,
                force=force_health_check,
            )
            recovery = self.store.resolve_admin_intervention(
                task_id=task_id,
                resolved_by_open_id="auto-health-preflight",
                health_result=health,
                automatic=True,
            )
            attempts.append(
                {
                    "task_id": task_id,
                    "attempted": True,
                    "health_ok": bool(health.get("ok")),
                    "force_health_check": force_health_check,
                    "status_before": status.get("status"),
                    "status_after": recovery.status or (self.store.load_status(task_id) or {}).get("status"),
                    "changed": recovery.changed,
                    "errors": list(health.get("errors") or []),
                    "next_health_check_at": self.store.system_blocked_next_health_check_at(self.store.load_status(task_id) or {}),
                }
            )
        return attempts

    def _blocked_task_summary(self, task_id: str) -> dict[str, Any]:
        status = self.store.load_status(task_id) or {}
        canonical_codes = self.store.canonical_blocking_error_codes(task_id, status=status)
        return {
            "task_id": task_id,
            "status": status.get("status"),
            "reason": canonical_codes,
            "canonical_blocking_error_code": canonical_codes[0] if canonical_codes else None,
            "health_check_count": int(status.get("health_check_count") or 0),
            "last_health_check_at": status.get("last_health_check_at"),
            "next_health_check_at": self.store.system_blocked_next_health_check_at(status),
            "recoverable_by_health_check": task_id in self.store.system_blocked_tasks_ready_for_health_check(force=True),
        }

    def _earliest_next_health_check(self, blocked_summaries: list[dict[str, Any]]) -> str | None:
        values = sorted(str(item.get("next_health_check_at") or "") for item in blocked_summaries if item.get("next_health_check_at"))
        return values[0] if values else None

    def _run_queue_head(
        self,
        *,
        task_id: str,
        queued: list[str],
        dispatch_run_id: str,
        manual_review_send_messages: bool,
        manual_review_dry_run: bool,
    ) -> dict[str, Any]:
        prepare = self.runner.auto_prepare_queued_current_task(task_id, mode="dispatcher-auto-prepare-current-task")
        if not prepare.get("ok"):
            return self._failure_payload(
                task_id=task_id,
                dispatch_run_id=dispatch_run_id,
                queued=queued,
                step="prepare-current-task",
                result=prepare,
            )

        first = self.runner.run_first_stage(
            task_id,
            allow_app_run=True,
            first_stage_script=self.first_stage_script,
            first_stage_result_path=self.first_stage_result_path,
        )
        if not first.get("ok") or first.get("status") != "S10_READY":
            if is_target_info_error(errors=list(first.get("errors") or []), result=first):
                return self._failure_payload(
                    task_id=task_id,
                    dispatch_run_id=dispatch_run_id,
                    queued=queued,
                    step="first-stage",
                    result=first,
                )
            if not self.store.confirm_failure_should_auto_cancel(
                task_id,
                errors=list(first.get("errors") or []),
                result=first,
            ):
                admin_delivery = self._mark_admin_intervention(
                    task_id,
                    errors=list(first.get("errors") or []),
                    result=first,
                )
                return self._failure_payload(
                    task_id=task_id,
                    dispatch_run_id=dispatch_run_id,
                    queued=queued,
                    step="first-stage",
                    result=first,
                    admin_intervention_feedback=admin_delivery,
                )
            cancel_result = self.store.cancel_confirm_failure(
                task_id,
                errors=list(first.get("errors") or []),
                result=first,
                dry_run=not self.send_result_live,
            )
            payload = {
                "ok": False,
                "dry_run": False,
                "allow_app_run": True,
                "dispatch_run_id": dispatch_run_id,
                "task_id": task_id,
                "selected_task_id": task_id,
                "queued_task_ids": queued,
                "status": "CANCELLED",
                "failed_step": "first-stage",
                "errors": list(first.get("errors") or ["FIRST_STAGE_NOT_S10_READY"]),
                "started": True,
                "cancel_reason": "SYSTEM_PRECHECK_FAILED_NOT_STARTED",
                "blocks_queue": False,
                "recommended_next_action": "resend-target-info",
                "step_result": first,
                "auto_cancel_result": cancel_result.data,
            }
            payload.update(self._cancel_feedback_payload_fields(cancel_result))
            return payload

        second_stage_results: list[dict[str, Any]] = []
        reference_loop_states: list[dict[str, Any]] = []
        second = self.runner.run_second_stage(
            task_id,
            allow_app_run=True,
            second_stage_script=self.second_stage_script,
            second_stage_result_path=self.second_stage_result_path,
        )
        second_stage_results.append(second)
        reference_loop_state = self._resolve_second_stage_reference_loop_state(
            task_id,
            first,
            last_second_stage_result=second,
            second_stage_results=second_stage_results,
        )
        reference_loop_states.append(reference_loop_state)
        self._write_dispatcher_reference_loop_state(task_id, reference_loop_state)
        while reference_loop_state.get("dispatcher_continue_allowed"):
            second = self.runner.run_second_stage(
                task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )
            second_stage_results.append(second)
            reference_loop_state = self._resolve_second_stage_reference_loop_state(
                task_id,
                first,
                last_second_stage_result=second,
                second_stage_results=second_stage_results,
            )
            reference_loop_states.append(reference_loop_state)
            self._write_dispatcher_reference_loop_state(task_id, reference_loop_state)
        terminal_success_lookup = self._find_terminal_success_result(
            task_id,
            second_stage_results,
            include_task_files=True,
        )
        if terminal_success_lookup:
            ignored_failure = self._latest_failure_after_terminal_success(
                second_stage_results,
                terminal_index=terminal_success_lookup.get("result_index"),
            )
            persisted_terminal = self.runner.persist_terminal_success_result(
                task_id,
                terminal_success_lookup["payload"],
                source=str(terminal_success_lookup.get("source") or "dispatcher_terminal_success"),
                ignored_failure=ignored_failure,
            )
            second = self._coerce_terminal_success_dispatch_result(
                task_id=task_id,
                terminal_lookup=terminal_success_lookup,
                persisted_result=persisted_terminal,
                ignored_failure=ignored_failure,
                second_stage_results=second_stage_results,
            )
            reference_loop_state = {
                **reference_loop_state,
                "dispatcher_continue_allowed": False,
                "dispatcher_stop_reason": (
                    "TERMINAL_SUCCESS_RECOVERED_FROM_BACKUP"
                    if terminal_success_lookup.get("terminal_success_recovered_from_backup")
                    else "FULL_CHAIN_PRICED_DONE_TERMINAL_SUCCESS"
                ),
                "terminal_success_result_exists": True,
                "terminal_success_result_protected": True,
                "terminal_success_source": terminal_success_lookup.get("source"),
                "terminal_success_recovered_from_backup": bool(terminal_success_lookup.get("terminal_success_recovered_from_backup")),
                "terminal_success_recovery_reason": terminal_success_lookup.get("terminal_success_recovery_reason"),
                "preserved_terminal_success_status": terminal_success_lookup.get("preserved_terminal_success_status"),
                "failure_after_terminal_success_ignored": bool(ignored_failure),
                "ignored_failure_code": (ignored_failure or {}).get("status"),
            }
            if reference_loop_states:
                reference_loop_states[-1] = reference_loop_state
            self._write_dispatcher_reference_loop_state(task_id, reference_loop_state)
        if second.get("status") == CONTINUE_NEXT_REFERENCE:
            stop_code = str(reference_loop_state.get("dispatcher_stop_reason") or SECOND_STAGE_CONTINUATION_STATE_MISSING)
            if stop_code not in {
                ALL_TRISAME_REFERENCES_EXHAUSTED_NEEDS_REVIEW,
                SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED,
                SECOND_STAGE_CONTINUATION_STATE_MISSING,
            }:
                stop_code = SECOND_STAGE_CONTINUATION_STATE_MISSING
            second = {
                **second,
                "ok": False,
                "status": stop_code,
                "business_status": stop_code,
                "technical_status": "INCOMPLETE",
                "errors": [
                    stop_code,
                    *list(second.get("errors") or []),
                ],
                "second_stage_results": second_stage_results,
                "dispatcher_reference_loop_state": {
                    **reference_loop_state,
                    "dispatcher_wrapped_continue_as_failure": stop_code == SECOND_STAGE_COLLECTION_INCOMPLETE,
                },
                "dispatcher_reference_loop_states": reference_loop_states,
            }
        if not second.get("ok") or second.get("status") not in {"SUCCEEDED", "NEEDS_REVIEW"}:
            admin_delivery = self._mark_admin_intervention(
                task_id,
                errors=list(second.get("errors") or []),
                result=second,
            )
            return self._failure_payload(
                task_id=task_id,
                dispatch_run_id=dispatch_run_id,
                queued=queued,
                step="second-stage",
                result=second,
                first_stage_result=first,
                admin_intervention_feedback=admin_delivery,
            )

        revalidated = self.runner.revalidate_result(task_id)
        final_status = str(revalidated.get("status") or second.get("status") or "")
        supervisor_sync_result: dict[str, Any] | None = None
        if final_status == "NEEDS_REVIEW" or revalidated.get("business_status") == "NEEDS_REVIEW":
            supervisor_sync_result = self.supervisor_sync(
                task_id,
                store=self.store,
                send_messages=manual_review_send_messages,
                dry_run=manual_review_dry_run,
            )
            final_status = str(supervisor_sync_result.get("status") or final_status)
        elif final_status == "SUCCEEDED" and revalidated.get("business_status") == "SUCCEEDED":
            self.runner._set_task_status(  # noqa: SLF001 - dispatcher coordinates existing local runner state.
                task_id,
                "SUCCEEDED",
                technical_status="SUCCEEDED",
                business_status="SUCCEEDED",
                recommended_next_action="ready-to-send",
            )
        send_result: dict[str, Any] | None = None
        if final_status == "SUCCEEDED" and revalidated.get("business_status") == "SUCCEEDED" and self.auto_send_result:
            send_result = self.runner.send_result(task_id, live=self.send_result_live)
            if send_result.get("ok") and self.send_result_live:
                final_status = str(send_result.get("status") or "RESULT_SENT")

        return {
            "ok": True,
            "dry_run": False,
            "allow_app_run": True,
            "dispatch_run_id": dispatch_run_id,
            "task_id": task_id,
            "selected_task_id": task_id,
            "queued_task_ids": queued,
            "status": final_status,
            "started": True,
            "current_target_task_task_id_match": True,
            "prepare_result": prepare,
            "first_stage_result": first,
            "second_stage_result": second,
            "second_stage_results": second_stage_results,
            "dispatcher_reference_loop_state": reference_loop_state,
            "dispatcher_reference_loop_states": reference_loop_states,
            "revalidate_result": revalidated,
            "supervisor_sync_result": supervisor_sync_result,
            "auto_send_result": self.auto_send_result,
            "send_result": send_result,
        }

    def _second_stage_reference_loop_limit(self, first_stage_result: dict[str, Any]) -> int:
        """Compatibility wrapper for older tests and callers.

        New dispatcher runs use _resolve_second_stage_reference_loop_state(), which
        can read task artifacts and continuation state instead of only the runner
        wrapper. This method keeps the legacy signature but no longer caps a real
        count at 4.
        """
        state = self._resolve_second_stage_reference_loop_state(
            "",
            first_stage_result,
            last_second_stage_result=None,
            second_stage_results=[],
        )
        return int(state.get("loop_limit") or 4)

    def _candidate_payloads_from(self, source: str, payload: dict[str, Any], *, depth: int = 0) -> list[tuple[str, dict[str, Any]]]:
        if not isinstance(payload, dict) or depth > 3:
            return []
        candidates: list[tuple[str, dict[str, Any]]] = [(source, payload)]
        for key in ("pricing_result", "result", "step_result", "data", "payload"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                candidates.extend(self._candidate_payloads_from(f"{source}.{key}", nested, depth=depth + 1))
        return candidates

    def _read_json_dict(self, path: Path | None) -> dict[str, Any]:
        if path is None or not path.exists() or not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _task_target_fingerprints(self, task_id: str) -> list[str]:
        if not task_id:
            return []
        return target_fingerprints_from_artifacts(
            self.runner.project_root,
            self.store.task_dir(task_id),
            task_id=task_id,
        )

    def _result_scope_matches(
        self,
        task_id: str,
        payload: dict[str, Any],
        *,
        source: str,
        require_target_fingerprint: bool = True,
    ) -> bool:
        check = validate_result_task_scope(
            payload,
            current_task_id=task_id,
            current_target_fingerprints=self._task_target_fingerprints(task_id),
            source_path=source,
            require_task_id=True,
            require_target_fingerprint=require_target_fingerprint,
        )
        if check.ok:
            return True
        self._write_result_scope_rejection(task_id, check.as_trace())
        return False

    def _write_result_scope_rejection(self, task_id: str, trace: dict[str, Any]) -> None:
        if not task_id:
            return
        try:
            task_dir = self.store.task_dir(task_id)
            rejection_dir = task_dir / "result_scope_rejections"
            rejection_dir.mkdir(parents=True, exist_ok=True)
            path = rejection_dir / f"{self.clock().strftime('%Y%m%dT%H%M%S')}_dispatcher_rejected_result.json"
            payload = {
                "task_id": task_id,
                "dispatcher_result_scope_rejection": True,
                "cross_task_result_rejected": trace.get("rejection_code") == CROSS_TASK_PRICING_RESULT_REJECTED,
                "target_fingerprint_mismatch_rejected": trace.get("rejection_code") == TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED,
                "created_at": now_iso(self.clock),
                **trace,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            return

    def _filter_continuation_payloads_for_task_scope(
        self,
        task_id: str,
        payloads: list[tuple[str, dict[str, Any]]],
    ) -> list[tuple[str, dict[str, Any]]]:
        current_fingerprints = self._task_target_fingerprints(task_id)
        filtered: list[tuple[str, dict[str, Any]]] = []
        for source, payload in payloads:
            if not isinstance(payload, dict):
                continue
            is_persisted_source = source in {
                "task_pricing_result_json",
                "task_second_stage_result_json",
                "second_stage_result_output_path",
                "output_result_json",
            }
            has_scope_markers = bool(result_task_id_candidates(payload) or target_fingerprint_candidates(payload))
            if is_persisted_source or has_scope_markers or current_fingerprints:
                check = validate_result_task_scope(
                    payload,
                    current_task_id=task_id,
                    current_target_fingerprints=current_fingerprints,
                    source_path=source,
                    require_task_id=is_persisted_source or has_scope_markers,
                    require_target_fingerprint=bool(current_fingerprints and (is_persisted_source or has_scope_markers)),
                )
                if not check.ok:
                    self._write_result_scope_rejection(task_id, check.as_trace())
                    continue
            if current_fingerprints:
                stamp_result_task_scope(payload, task_id=task_id, target_fingerprints=current_fingerprints)
            filtered.append((source, payload))
        return filtered

    def _terminal_file_candidates(self, task_id: str) -> list[tuple[str, dict[str, Any]]]:
        candidates: list[tuple[str, dict[str, Any]]] = []
        task_dir = self.store.task_dir(task_id) if task_id else None
        if task_dir is not None:
            for name in ("pricing_result.json", "second_stage_result.json", "runner_result.json"):
                path = task_dir / name
                candidates.append((f"task_{name}", self._read_json_dict(path)))
            backup_dir = task_dir / "pre_run_result_backups"
            if backup_dir.exists():
                backup_paths = sorted(
                    (
                        *backup_dir.glob("*.output__result_s10_to_s16.json"),
                        *backup_dir.glob("*.output__result.json"),
                    ),
                    key=lambda candidate: candidate.stat().st_mtime,
                    reverse=True,
                )
                for path in backup_paths:
                    candidates.append((f"pre_run_result_backup:{path}", self._read_json_dict(path)))
        if self.second_stage_result_path:
            second_path = Path(self.second_stage_result_path)
            candidates.append(("second_stage_result_output_path", self._read_json_dict(second_path)))
            candidates.append(("output_result_json", self._read_json_dict(second_path.parent / "result.json")))
        return [(source, payload) for source, payload in candidates if payload]

    def _find_terminal_success_result(
        self,
        task_id: str,
        second_stage_results: list[dict[str, Any]] | None,
        *,
        include_task_files: bool,
    ) -> dict[str, Any] | None:
        candidate_roots: list[tuple[str, dict[str, Any], int | None]] = []
        for index, result in enumerate(second_stage_results or []):
            if isinstance(result, dict):
                candidate_roots.append((f"second_stage_results[{index}]", result, index))
        if include_task_files:
            for source, payload in self._terminal_file_candidates(task_id):
                candidate_roots.append((source, payload, None))
        for source, payload, result_index in candidate_roots:
            for candidate_source, candidate in self._candidate_payloads_from(source, payload):
                if is_automatic_pricing_terminal_success(candidate):
                    if not self._result_scope_matches(
                        task_id,
                        candidate,
                        source=candidate_source,
                        require_target_fingerprint=True,
                    ):
                        continue
                    recovered_from_backup = str(candidate_source).startswith("pre_run_result_backup:")
                    if recovered_from_backup:
                        candidate = dict(candidate)
                        candidate.setdefault("terminal_success_recovered_from_backup", True)
                        candidate.setdefault("terminal_success_backup_path", str(candidate_source))
                        candidate.setdefault("terminal_success_recovery_reason", "PRE_RUN_ISOLATED_SUCCESS_RESULT")
                    return {
                        "source": candidate_source,
                        "payload": candidate,
                        "result_index": result_index,
                        "preserved_terminal_success_status": self._terminal_success_status(candidate),
                        "terminal_success_recovered_from_backup": recovered_from_backup,
                        "terminal_success_recovery_reason": "PRE_RUN_ISOLATED_SUCCESS_RESULT" if recovered_from_backup else None,
                    }
        return None

    def _find_needs_review_terminal_result(
        self,
        task_id: str,
        second_stage_results: list[dict[str, Any]] | None,
        *,
        include_task_files: bool,
    ) -> dict[str, Any] | None:
        candidate_roots: list[tuple[str, dict[str, Any], int | None]] = []
        for index, result in enumerate(second_stage_results or []):
            if isinstance(result, dict):
                candidate_roots.append((f"second_stage_results[{index}]", result, index))
        if include_task_files:
            for source, payload in self._terminal_file_candidates(task_id):
                candidate_roots.append((source, payload, None))
        for source, payload, result_index in candidate_roots:
            for candidate_source, candidate in self._candidate_payloads_from(source, payload):
                status = str(candidate.get("status") or candidate.get("business_status") or "")
                if status == "NEEDS_REVIEW" or pricing_result_business_status(candidate) == "NEEDS_REVIEW":
                    if not self._result_scope_matches(
                        task_id,
                        candidate,
                        source=candidate_source,
                        require_target_fingerprint=True,
                    ):
                        continue
                    return {
                        "source": candidate_source,
                        "payload": candidate,
                        "result_index": result_index,
                    }
        return None

    def _terminal_success_status(self, payload: dict[str, Any]) -> str:
        for key in ("status", "final_status", "current_state", "s16_status"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        pricing = payload.get("pricing")
        if isinstance(pricing, dict) and pricing.get("status"):
            return str(pricing["status"])
        return "FULL_CHAIN_PRICED_DONE"

    def _latest_failure_after_terminal_success(
        self,
        second_stage_results: list[dict[str, Any]],
        *,
        terminal_index: int | None,
    ) -> dict[str, Any] | None:
        candidates = second_stage_results[(terminal_index + 1) :] if terminal_index is not None else second_stage_results
        latest: dict[str, Any] | None = None
        for result in candidates:
            if not isinstance(result, dict):
                continue
            status = str(result.get("status") or "")
            if not result.get("ok") or status not in {"SUCCEEDED", "NEEDS_REVIEW", CONTINUE_NEXT_REFERENCE}:
                latest = result
        return latest

    def _coerce_terminal_success_dispatch_result(
        self,
        *,
        task_id: str,
        terminal_lookup: dict[str, Any],
        persisted_result: dict[str, Any],
        ignored_failure: dict[str, Any] | None,
        second_stage_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = dict(terminal_lookup.get("payload") or {})
        payload.update(
            {
                "task_id": task_id,
                "ok": True,
                "status": "SUCCEEDED",
                "business_status": "SUCCEEDED",
                "technical_status": "SUCCEEDED",
                "recommended_next_action": "ready-to-send",
                "terminal_success_recommended_next_action": "deliver-result",
                "terminal_success_result_exists": True,
                "terminal_success_result_protected": True,
                "preserved_terminal_success_status": terminal_lookup.get("preserved_terminal_success_status"),
                "terminal_success_source": terminal_lookup.get("source"),
                "terminal_success_recovered_from_backup": bool(terminal_lookup.get("terminal_success_recovered_from_backup")),
                "terminal_success_recovery_reason": terminal_lookup.get("terminal_success_recovery_reason"),
                "persisted_terminal_success_result": persisted_result,
                "second_stage_results": second_stage_results,
                "errors": [],
            }
        )
        if ignored_failure:
            payload.update(
                {
                    "failure_after_terminal_success_ignored": True,
                    "latest_failure_after_terminal_success": ignored_failure,
                    "ignored_failure_code": str(ignored_failure.get("status") or ""),
                }
            )
        return payload

    def _resolve_second_stage_reference_loop_state(
        self,
        task_id: str,
        first_stage_result: dict[str, Any],
        last_second_stage_result: dict[str, Any] | None,
        second_stage_results: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        results = list(second_stage_results or [])
        total_count_candidates: list[dict[str, Any]] = []

        def safe_int(value: Any) -> int | None:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed >= 0 else None

        def add_count_candidate(source: str, key: str, value: Any) -> None:
            parsed = safe_int(value)
            total_count_candidates.append(
                {
                    "source": source,
                    "key": key,
                    "value": value,
                    "parsed_count": parsed,
                    "usable": bool(parsed and parsed > 0),
                }
            )

        def inspect_count_payload(payload: dict[str, Any] | None, source: str, *, depth: int = 0) -> None:
            if not isinstance(payload, dict) or depth > 4:
                return
            for key in ("trisame_cards_count", "trisame_count", "same_source_cards_count", "reference_cards_count", "reference_count"):
                if key in payload:
                    add_count_candidate(source, key, payload.get(key))
            for key in ("same_source_cards", "reference_cards", "cards"):
                value = payload.get(key)
                if isinstance(value, list):
                    add_count_candidate(source, f"len({key})", len(value))
            for nested_key in ("step_result", "result", "data", "payload", "pricing_result", "first_stage_result"):
                nested = payload.get(nested_key)
                if isinstance(nested, dict):
                    inspect_count_payload(nested, f"{source}.{nested_key}", depth=depth + 1)

        def read_json_dict(path: Path | None) -> dict[str, Any]:
            if path is None or not path.exists() or not path.is_file():
                return {}
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            return payload if isinstance(payload, dict) else {}

        task_dir = self.store.task_dir(task_id) if task_id else None
        task_first = read_json_dict(task_dir / "first_stage_result.json") if task_dir is not None else {}
        inspect_count_payload(task_first, "task_first_stage_result_json")
        inspect_count_payload(first_stage_result, "dispatcher_first_stage_result")

        first_stage_output = read_json_dict(Path(self.first_stage_result_path)) if self.first_stage_result_path else {}
        inspect_count_payload(first_stage_output, "first_stage_result_output_path")

        continuation_payloads: list[tuple[str, dict[str, Any]]] = []
        if isinstance(last_second_stage_result, dict):
            continuation_payloads.append(("last_second_stage_result", last_second_stage_result))
        dispatcher_loop_state_json: dict[str, Any] = {}
        if task_dir is not None:
            dispatcher_loop_state_json = read_json_dict(task_dir / "dispatcher_reference_loop_state.json")
            continuation_payloads.extend(
                [
                    ("task_pricing_result_json", read_json_dict(task_dir / "pricing_result.json")),
                    ("task_second_stage_result_json", read_json_dict(task_dir / "second_stage_result.json")),
                ]
            )
        if self.second_stage_result_path:
            second_path = Path(self.second_stage_result_path)
            continuation_payloads.append(("second_stage_result_output_path", read_json_dict(second_path)))
            continuation_payloads.append(("output_result_json", read_json_dict(second_path.parent / "result.json")))

        continuation_payloads = self._filter_continuation_payloads_for_task_scope(task_id, continuation_payloads)
        continuation_state = self._resolve_second_stage_continuation_state(continuation_payloads)
        if continuation_state.get("next_reference_index") is not None and continuation_state.get("remaining_reference_count") is not None:
            next_index = int(continuation_state["next_reference_index"])
            remaining = int(continuation_state["remaining_reference_count"])
            if next_index > 0 and remaining >= 0:
                add_count_candidate(
                    str(continuation_state.get("state_source") or "continuation_state"),
                    "derived_total_from_next_reference_index_and_remaining_reference_count",
                    next_index + remaining - 1,
                )

        real_count = None
        loop_limit_source = "fallback_default_4_no_real_count_no_continuation_state"
        for item in total_count_candidates:
            count = item.get("parsed_count")
            if isinstance(count, int) and count > 0:
                real_count = count
                loop_limit_source = f"{item.get('source')}_{item.get('key')}"
                break

        fallback_default_4_used = real_count is None
        if real_count is None:
            loop_limit = 4
        else:
            loop_limit = max(1, min(int(real_count), SECOND_STAGE_REFERENCE_LOOP_ABSOLUTE_SAFETY_LIMIT))

        attempted_reference_indices: list[int] = []
        for result in results:
            for key in ("current_reference_index", "selected_reference_index", "reference_index"):
                parsed = safe_int(result.get(key) if isinstance(result, dict) else None)
                if parsed is not None and parsed > 0:
                    attempted_reference_indices.append(parsed)
                    break
        if not attempted_reference_indices:
            for index in range(1, len(results) + 1):
                attempted_reference_indices.append(index)

        terminal_success_lookup = self._find_terminal_success_result(
            task_id,
            results,
            include_task_files=bool(task_id),
        )
        needs_review_lookup = None if terminal_success_lookup else self._find_needs_review_terminal_result(
            task_id,
            results,
            include_task_files=bool(task_id),
        )

        current_reference_index = continuation_state.get("current_reference_index")
        next_reference_index = continuation_state.get("next_reference_index")
        remaining_reference_count = continuation_state.get("remaining_reference_count")
        remaining_inferred_from_real_count = False
        if (
            remaining_reference_count is None
            and next_reference_index is not None
            and real_count is not None
            and next_reference_index > 0
        ):
            remaining_reference_count = max(0, real_count - next_reference_index + 1)
            remaining_inferred_from_real_count = True
        should_continue = bool(continuation_state.get("should_continue_reference_collection"))
        continue_reason = str(continuation_state.get("continue_reason") or "")
        legal_low_score_continue = bool(continuation_state.get("legal_low_score_continue"))
        early_exit_continue = bool(
            continuation_state.get("reference_early_exit")
            or continuation_state.get("early_exit_allowed")
            or str(continue_reason) == "EARLY_EXIT_CONTINUE_NEXT_REFERENCE"
            or legal_low_score_continue
        )

        dispatcher_continue_allowed = False
        dispatcher_continue_reason = ""
        dispatcher_stop_reason = ""
        last_status = str((last_second_stage_result or {}).get("status") or "")
        last_ok = bool((last_second_stage_result or {}).get("ok"))
        results_count = len(results)
        dispatcher_expected_next = safe_int(dispatcher_loop_state_json.get("next_reference_index")) if dispatcher_loop_state_json else None
        runtime_actual_reference = safe_int((last_second_stage_result or {}).get("current_reference_index"))
        runtime_plan = (
            (last_second_stage_result or {}).get("continuation_plan")
            if isinstance((last_second_stage_result or {}).get("continuation_plan"), dict)
            else {}
        )
        runtime_plan_next = safe_int(runtime_plan.get("next_reference_index")) if runtime_plan else None
        runtime_plan_source = str(runtime_plan.get("source_path") or runtime_plan.get("continuation_source_selected") or "") if runtime_plan else ""
        reference_loop_state_reset_detected = bool(
            dispatcher_loop_state_json
            and dispatcher_loop_state_json.get("dispatcher_continue_allowed") is True
            and dispatcher_expected_next is not None
            and dispatcher_expected_next > 1
            and (
                runtime_actual_reference == 1
                or (runtime_plan_next == 1 and not runtime_plan_source)
                or (last_second_stage_result or {}).get("reference_loop_state_reset_detected") is True
            )
        )

        if terminal_success_lookup:
            terminal_stop_reason = (
                "TERMINAL_SUCCESS_RECOVERED_FROM_BACKUP"
                if terminal_success_lookup.get("terminal_success_recovered_from_backup")
                else "FULL_CHAIN_PRICED_DONE_TERMINAL_SUCCESS"
            )
            return {
                "task_id": task_id,
                **continuation_state,
                "real_trisame_cards_count": real_count,
                "real_reference_count": real_count,
                "loop_limit": loop_limit,
                "loop_limit_source": loop_limit_source,
                "fallback_default_4_used": fallback_default_4_used,
                "absolute_safety_limit": SECOND_STAGE_REFERENCE_LOOP_ABSOLUTE_SAFETY_LIMIT,
                "total_count_candidates": total_count_candidates,
                "current_reference_index": current_reference_index,
                "next_reference_index": next_reference_index,
                "remaining_reference_count": remaining_reference_count,
                "remaining_reference_count_inferred_from_real_count": remaining_inferred_from_real_count,
                "attempted_reference_indices": attempted_reference_indices,
                "second_stage_results_count": results_count,
                "should_continue_reference_collection": False,
                "should_continue_reference_collection_inferred_from_status": False,
                "continue_reason": continue_reason,
                "continuation_consumed": False,
                "previous_reference_index": current_reference_index,
                "resumed_reference_index": None,
                "continuation_source": continuation_state.get("continuation_source") or "",
                "legal_low_score_continue": legal_low_score_continue,
                "reference_early_exit": bool(continuation_state.get("reference_early_exit")),
                "early_exit_rule_id": continuation_state.get("early_exit_rule_id"),
                "early_exit_allowed": bool(continuation_state.get("early_exit_allowed")),
                "dispatcher_continue_allowed": False,
                "dispatcher_continue_reason": "",
                "dispatcher_stop_reason": terminal_stop_reason,
                "dispatcher_wrapped_continue_as_failure": False,
                "terminal_success_result_exists": True,
                "terminal_success_result_protected": True,
                "terminal_success_source": terminal_success_lookup.get("source"),
                "terminal_success_recovered_from_backup": bool(terminal_success_lookup.get("terminal_success_recovered_from_backup")),
                "terminal_success_recovery_reason": terminal_success_lookup.get("terminal_success_recovery_reason"),
                "preserved_terminal_success_status": terminal_success_lookup.get("preserved_terminal_success_status"),
                "reference_loop_terminal_priority": "terminal_success",
                "first_stage_count_loaded_from_task_artifact": str(loop_limit_source).startswith("task_first_stage_result_json"),
                "first_stage_count_loaded_from_wrapper": str(loop_limit_source).startswith("dispatcher_first_stage_result"),
                "continuation_state_loaded_from_pricing_result": continuation_state.get("state_source") in {
                    "task_pricing_result_json",
                    "second_stage_result_output_path",
                    "output_result_json",
                    "dispatcher_reference_loop_state_json",
                },
            }
        if needs_review_lookup:
            return {
                "task_id": task_id,
                **continuation_state,
                "real_trisame_cards_count": real_count,
                "real_reference_count": real_count,
                "loop_limit": loop_limit,
                "loop_limit_source": loop_limit_source,
                "fallback_default_4_used": fallback_default_4_used,
                "absolute_safety_limit": SECOND_STAGE_REFERENCE_LOOP_ABSOLUTE_SAFETY_LIMIT,
                "total_count_candidates": total_count_candidates,
                "current_reference_index": current_reference_index,
                "next_reference_index": next_reference_index,
                "remaining_reference_count": remaining_reference_count,
                "remaining_reference_count_inferred_from_real_count": remaining_inferred_from_real_count,
                "attempted_reference_indices": attempted_reference_indices,
                "second_stage_results_count": results_count,
                "should_continue_reference_collection": False,
                "should_continue_reference_collection_inferred_from_status": False,
                "continue_reason": continue_reason,
                "continuation_consumed": False,
                "previous_reference_index": current_reference_index,
                "resumed_reference_index": None,
                "continuation_source": continuation_state.get("continuation_source") or "",
                "legal_low_score_continue": legal_low_score_continue,
                "reference_early_exit": bool(continuation_state.get("reference_early_exit")),
                "early_exit_rule_id": continuation_state.get("early_exit_rule_id"),
                "early_exit_allowed": bool(continuation_state.get("early_exit_allowed")),
                "dispatcher_continue_allowed": False,
                "dispatcher_continue_reason": "",
                "dispatcher_stop_reason": "NEEDS_REVIEW_TERMINAL",
                "dispatcher_wrapped_continue_as_failure": False,
                "needs_review_terminal_result_exists": True,
                "needs_review_terminal_source": needs_review_lookup.get("source"),
                "reference_loop_terminal_priority": "needs_review_terminal",
                "first_stage_count_loaded_from_task_artifact": str(loop_limit_source).startswith("task_first_stage_result_json"),
                "first_stage_count_loaded_from_wrapper": str(loop_limit_source).startswith("dispatcher_first_stage_result"),
                "continuation_state_loaded_from_pricing_result": continuation_state.get("state_source") in {
                    "task_pricing_result_json",
                    "second_stage_result_output_path",
                    "output_result_json",
                    "dispatcher_reference_loop_state_json",
                },
            }

        effective_continue_status = (
            (last_status == CONTINUE_NEXT_REFERENCE and last_ok)
            or legal_low_score_continue
        )
        should_continue_inferred_from_status = (
            effective_continue_status
            and next_reference_index is not None
            and next_reference_index > 0
            and (real_count is None or next_reference_index <= real_count)
            and (remaining_reference_count is None or remaining_reference_count > 0)
        )
        if should_continue_inferred_from_status and not should_continue:
            should_continue = True
            if not continue_reason:
                continue_reason = (
                    "EARLY_EXIT_CONTINUE_NEXT_REFERENCE"
                    if early_exit_continue
                    else "INFERRED_FROM_CONTINUE_NEXT_REFERENCE_STATUS"
                )

        if reference_loop_state_reset_detected:
            dispatcher_stop_reason = REFERENCE_LOOP_STATE_RESET_DETECTED
        elif not effective_continue_status:
            dispatcher_stop_reason = "SECOND_STAGE_TERMINAL_OR_FAILED_STATUS"
        elif results_count >= SECOND_STAGE_REFERENCE_LOOP_ABSOLUTE_SAFETY_LIMIT:
            dispatcher_stop_reason = SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED
        elif results_count >= loop_limit:
            if (
                real_count is not None
                and next_reference_index is not None
                and next_reference_index > real_count
            ) or (remaining_reference_count is not None and remaining_reference_count <= 0):
                dispatcher_stop_reason = ALL_TRISAME_REFERENCES_EXHAUSTED_NEEDS_REVIEW
            elif fallback_default_4_used:
                dispatcher_stop_reason = SECOND_STAGE_CONTINUATION_STATE_MISSING
            else:
                dispatcher_stop_reason = SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED
        elif (
            should_continue
            and remaining_reference_count is not None
            and remaining_reference_count > 0
            and next_reference_index is not None
            and (real_count is None or next_reference_index <= real_count)
        ):
            dispatcher_continue_allowed = True
            dispatcher_continue_reason = (
                "EARLY_EXIT_CONTINUE_NEXT_REFERENCE"
                if early_exit_continue
                else "CONTINUE_NEXT_REFERENCE_WITH_REMAINING_REFERENCES"
            )
        elif (
            next_reference_index is not None
            and real_count is not None
            and next_reference_index > real_count
        ) or (remaining_reference_count is not None and remaining_reference_count <= 0):
            dispatcher_stop_reason = ALL_TRISAME_REFERENCES_EXHAUSTED_NEEDS_REVIEW
        else:
            dispatcher_stop_reason = SECOND_STAGE_CONTINUATION_STATE_MISSING

        return {
            "task_id": task_id,
            "real_trisame_cards_count": real_count,
            "real_reference_count": real_count,
            "loop_limit": loop_limit,
            "loop_limit_source": loop_limit_source,
            "fallback_default_4_used": fallback_default_4_used,
            "absolute_safety_limit": SECOND_STAGE_REFERENCE_LOOP_ABSOLUTE_SAFETY_LIMIT,
            "total_count_candidates": total_count_candidates,
            "current_reference_index": current_reference_index,
            "next_reference_index": next_reference_index,
            "remaining_reference_count": remaining_reference_count,
            "remaining_reference_count_inferred_from_real_count": remaining_inferred_from_real_count,
            "attempted_reference_indices": attempted_reference_indices,
            "second_stage_results_count": results_count,
            "should_continue_reference_collection": should_continue,
            "should_continue_reference_collection_inferred_from_status": should_continue_inferred_from_status,
            "continue_reason": continue_reason,
            "continuation_consumed": bool(dispatcher_continue_allowed),
            "previous_reference_index": current_reference_index,
            "resumed_reference_index": next_reference_index if dispatcher_continue_allowed else None,
            "continuation_source": (
                "low_score_skip"
                if early_exit_continue
                else continuation_state.get("continuation_source")
                or ""
            ),
            "legal_low_score_continue": legal_low_score_continue,
            "reference_early_exit": bool(continuation_state.get("reference_early_exit")),
            "early_exit_rule_id": continuation_state.get("early_exit_rule_id"),
            "early_exit_allowed": bool(continuation_state.get("early_exit_allowed")),
            "dispatcher_continue_allowed": dispatcher_continue_allowed,
            "dispatcher_continue_reason": dispatcher_continue_reason,
            "dispatcher_stop_reason": dispatcher_stop_reason,
            "dispatcher_wrapped_continue_as_failure": False,
            "reference_loop_state_reset_detected": reference_loop_state_reset_detected,
            "reference_loop_state_reset_code": REFERENCE_LOOP_STATE_RESET_DETECTED if reference_loop_state_reset_detected else "",
            "dispatcher_expected_next_reference_index_before_runner": dispatcher_expected_next,
            "runtime_actual_reference_index_after_runner": runtime_actual_reference,
            "runtime_continuation_plan_next_reference_index": runtime_plan_next,
            "runtime_continuation_plan_source_path": runtime_plan_source,
            "first_stage_count_loaded_from_task_artifact": str(loop_limit_source).startswith("task_first_stage_result_json"),
            "first_stage_count_loaded_from_wrapper": str(loop_limit_source).startswith("dispatcher_first_stage_result"),
            "continuation_state_loaded_from_pricing_result": continuation_state.get("state_source") in {
                "task_pricing_result_json",
                "second_stage_result_output_path",
                "output_result_json",
                "dispatcher_reference_loop_state_json",
            },
            **continuation_state,
        }

    def _resolve_second_stage_continuation_state(self, payloads: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        keys = (
            "current_reference_index",
            "next_reference_index",
            "remaining_reference_count",
            "should_continue_reference_collection",
            "continue_reason",
            "reference_early_exit",
            "early_exit_rule_id",
            "early_exit_allowed",
        )

        def safe_int(value: Any) -> int | None:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed >= 0 else None

        def safe_float(value: Any) -> float | None:
            if isinstance(value, dict):
                value = value.get("score")
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def first_int(*values: Any) -> int | None:
            for value in values:
                parsed = safe_int(value)
                if parsed is not None:
                    return parsed
            return None

        def walk(payload: dict[str, Any], source: str, *, depth: int = 0) -> list[tuple[str, dict[str, Any]]]:
            if depth > 4 or not isinstance(payload, dict):
                return []
            found = [(source, payload)]
            for nested_key in (
                "step_result",
                "result",
                "data",
                "payload",
                "pricing_result",
                "second_stage_result",
                "current_reference",
                "issue_context",
                "early_exit_decision",
                "s14_in_flight_early_exit_decision",
            ):
                nested = payload.get(nested_key)
                if isinstance(nested, dict):
                    found.extend(walk(nested, f"{source}.{nested_key}", depth=depth + 1))
            return found

        def infer_legal_low_score_continue(source: str, payload: dict[str, Any]) -> dict[str, Any] | None:
            if not isinstance(payload, dict):
                return None
            current_reference = payload.get("current_reference") if isinstance(payload.get("current_reference"), dict) else {}
            issue_context = payload.get("issue_context") if isinstance(payload.get("issue_context"), dict) else {}
            if not current_reference and isinstance(issue_context.get("current_reference"), dict):
                current_reference = issue_context["current_reference"]
            if not current_reference and (
                payload.get("reference_status") == "LOW_SCORE_SKIPPED_INCOMPLETE"
                or payload.get("low_score_skipped_incomplete") is True
            ):
                current_reference = payload
            decision = current_reference.get("early_exit_decision") if isinstance(current_reference.get("early_exit_decision"), dict) else {}
            if not decision and isinstance(current_reference.get("s14_in_flight_early_exit_decision"), dict):
                decision = current_reference["s14_in_flight_early_exit_decision"]
            if not decision and isinstance(payload.get("early_exit_decision"), dict):
                decision = payload["early_exit_decision"]

            current_index = first_int(
                payload.get("current_reference_index"),
                current_reference.get("current_reference_index"),
                current_reference.get("reference_index"),
                decision.get("current_reference_index"),
            )
            next_index = first_int(
                decision.get("next_reference_index"),
                current_reference.get("next_reference_index"),
                payload.get("next_reference_index"),
            )
            if current_index is None or next_index is None or next_index <= current_index:
                return None

            target_score = safe_float(payload.get("target_score"))
            if target_score is None:
                target_score = safe_float(current_reference.get("target_score"))
            if target_score is None:
                target_score = safe_float(decision.get("target_score"))
            upper_bound = safe_float(
                current_reference.get("reference_score_upper_bound")
                or current_reference.get("max_possible_reference_score")
                or decision.get("reference_score_upper_bound")
                or decision.get("max_possible_reference_score")
            )
            if target_score is None or upper_bound is None or not upper_bound < target_score:
                return None

            low_score_status = (
                current_reference.get("reference_status") == "LOW_SCORE_SKIPPED_INCOMPLETE"
                or decision.get("reference_status") == "LOW_SCORE_SKIPPED_INCOMPLETE"
                or current_reference.get("low_score_skipped_incomplete") is True
                or decision.get("low_score_skipped_incomplete") is True
            )
            low_score_triggered = (
                current_reference.get("s14_low_score_skip_triggered") is True
                or decision.get("s14_low_score_skip_triggered") is True
                or decision.get("early_exit_decision") == "LOW_SCORE_SKIP_AND_CONTINUE_NEXT_REFERENCE"
            )
            returned_to_s10 = (
                current_reference.get("return_to_s10_after_low_score_skip") is True
                or decision.get("return_to_s10_after_low_score_skip") is True
                or payload.get("return_to_s10_after_low_score_skip") is True
            )
            return_verified = (
                current_reference.get("returned_list_source_verified") is True
                or decision.get("returned_list_source_verified") is True
                or payload.get("returned_list_source_verified") is True
            )
            if not (low_score_status and low_score_triggered and returned_to_s10 and return_verified):
                return None
            remaining = first_int(payload.get("remaining_reference_count"), current_reference.get("remaining_reference_count"))
            if remaining is not None and remaining <= 0:
                return None
            return {
                "state_source": source,
                "current_reference_index": current_index,
                "next_reference_index": next_index,
                "remaining_reference_count": remaining,
                "should_continue_reference_collection": True,
                "continue_reason": "EARLY_EXIT_CONTINUE_NEXT_REFERENCE",
                "reference_early_exit": True,
                "early_exit_rule_id": current_reference.get("early_exit_rule_id") or decision.get("early_exit_rule_id"),
                "early_exit_allowed": True,
                "legal_low_score_continue": True,
                "continuation_source": "low_score_skip",
            }

        candidates: list[tuple[str, dict[str, Any]]] = []
        for source, payload in payloads:
            candidates.extend(walk(payload, source))

        best_source = ""
        best_payload: dict[str, Any] = {}
        best_score = -1
        legal_continue_state: dict[str, Any] | None = None
        for source, payload in candidates:
            if legal_continue_state is None:
                legal_continue_state = infer_legal_low_score_continue(source, payload)
            score = sum(1 for key in keys if key in payload and payload.get(key) not in (None, ""))
            if score > best_score:
                best_source = source
                best_payload = payload
                best_score = score
        state = {
            "state_source": best_source,
            "current_reference_index": safe_int(best_payload.get("current_reference_index")),
            "next_reference_index": safe_int(best_payload.get("next_reference_index")),
            "remaining_reference_count": safe_int(best_payload.get("remaining_reference_count")),
            "should_continue_reference_collection": bool(best_payload.get("should_continue_reference_collection")),
            "continue_reason": str(best_payload.get("continue_reason") or ""),
            "reference_early_exit": bool(
                best_payload.get("reference_early_exit")
                or (isinstance(best_payload.get("current_reference"), dict) and best_payload["current_reference"].get("reference_early_exit"))
            ),
            "early_exit_rule_id": best_payload.get("early_exit_rule_id")
            or (
                best_payload.get("current_reference", {}).get("early_exit_rule_id")
                if isinstance(best_payload.get("current_reference"), dict)
                else None
            ),
            "early_exit_allowed": bool(
                best_payload.get("early_exit_allowed")
                or (isinstance(best_payload.get("current_reference"), dict) and best_payload["current_reference"].get("early_exit_allowed"))
            ),
        }
        if legal_continue_state:
            state.update({key: value for key, value in legal_continue_state.items() if value is not None})
        return state

    def _write_dispatcher_reference_loop_state(self, task_id: str, state: dict[str, Any]) -> None:
        if not task_id:
            return
        try:
            task_dir = self.store.task_dir(task_id)
            task_dir.mkdir(parents=True, exist_ok=True)
            state = dict(state)
            stamp_result_task_scope(state, task_id=task_id, target_fingerprints=self._task_target_fingerprints(task_id))
            (task_dir / "dispatcher_reference_loop_state.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            return

    def _cancel_feedback_payload_fields(self, result: Any) -> dict[str, Any]:
        data = result.data or {}
        feedback = data.get("feedback") if isinstance(data, dict) else {}
        status = data.get("status") if isinstance(data, dict) else {}
        if not isinstance(feedback, dict):
            feedback = {}
        if not isinstance(status, dict):
            status = {}
        return {
            "cancelled": True,
            "canonical_error_code": feedback.get("canonical_error_code") or status.get("canonical_error_code"),
            "human_reason": feedback.get("human_reason") or status.get("human_reason"),
            "retry_instruction": feedback.get("retry_instruction") or status.get("retry_instruction"),
            "business_reply_text": feedback.get("business_reply_text"),
            "active_runner_exists": False,
            "released_old_blocker_task_ids": [],
            "queued_continued": False,
            "final_feedback_sent": bool(status.get("final_feedback_sent")),
        }

    def _mark_admin_intervention(
        self,
        task_id: str,
        *,
        errors: list[str] | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            status_before = self.store.load_status(task_id) or {}
            classification = classify_admin_intervention(errors=errors, result=result)
            status = str(classification.get("status") or ADMIN_INTERVENTION_REQUIRED)
            marked_at = now_iso(self.clock)
            self.runner._set_task_status(  # noqa: SLF001 - dispatcher owns orchestration failure state.
                task_id,
                status,
                technical_status=str(classification.get("technical_status") or "FAILED"),
                business_status=str(classification.get("business_status") or status),
                recommended_next_action=str(classification.get("recommended_next_action") or "notify-admin"),
            )
            extra = {
                "admin_intervention_category": classification.get("category"),
                "admin_intervention_error_codes": classification.get("error_codes"),
                "admin_intervention_recoverable": classification.get("recoverable_by_admin"),
                "last_admin_notice_at": status_before.get("last_admin_notice_at") or marked_at,
            }
            if status == SYSTEM_BLOCKED:
                extra["system_blocked_at"] = status_before.get("system_blocked_at") or marked_at
            updated = self.store.update_task_status_fields(task_id, fields=extra)
            return write_admin_intervention_feedback(
                task_dir=self.store.task_dir(task_id),
                task_id=task_id,
                status_payload={**status_before, **updated},
                errors=errors,
                result=result,
                dry_run=True,
                clock=self.clock,
            )
        except Exception:
            return None

    def _failure_payload(
        self,
        *,
        task_id: str,
        dispatch_run_id: str,
        queued: list[str],
        step: str,
        result: dict[str, Any],
        first_stage_result: dict[str, Any] | None = None,
        admin_intervention_feedback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_info_failure = is_target_info_error(errors=list(result.get("errors") or []), result=result)
        classification = classify_admin_intervention(errors=list(result.get("errors") or []), result=result)
        failure_status = TARGET_INFO_NEEDS_CORRECTION if target_info_failure else str(classification.get("status") or ADMIN_INTERVENTION_REQUIRED)
        payload: dict[str, Any] = {
            "ok": False,
            "dry_run": False,
            "allow_app_run": True,
            "dispatch_run_id": dispatch_run_id,
            "task_id": task_id,
            "selected_task_id": task_id,
            "queued_task_ids": queued,
            "status": failure_status,
            "failed_step": step,
            "errors": list(result.get("errors") or ["DISPATCHER_STEP_FAILED"]),
            "started": step not in {"prepare-current-task", "system-health-preflight"},
            "step_result": result,
            "recommended_next_action": "ask-sender-to-resend-target-info"
            if target_info_failure
            else classification.get("recommended_next_action"),
        }
        if first_stage_result is not None:
            payload["first_stage_result"] = first_stage_result
        if admin_intervention_feedback is not None:
            payload["admin_intervention_feedback"] = admin_intervention_feedback
        return payload

    def _append_dispatch_log(self, payload: dict[str, Any]) -> None:
        row = dict(payload)
        row["logged_at"] = now_iso(self.clock)
        self.dispatcher_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.dispatcher_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_dispatcher_result(self, task_id: str, payload: dict[str, Any]) -> None:
        task_dir = self.store.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "dispatcher_result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _new_dispatch_run_id(self) -> str:
        timestamp = self.clock().strftime("%Y%m%dT%H%M%S")
        return f"{timestamp}_dispatcher_{uuid.uuid4().hex[:8]}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serial Feishu pricing dispatcher.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Check or run the queue head once.")
    mode.add_argument("--loop", action="store_true", help="Run dispatcher loop. Requires --allow-app-run for real APP automation.")
    parser.add_argument("--dry-run", action="store_true", help="Do not run APP automation. This is the default without --allow-app-run.")
    parser.add_argument("--allow-app-run", action="store_true", help="Required to run first/second stage APP automation.")
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="Loop polling interval.")
    parser.add_argument("--task-root", default=str(DEFAULT_TASK_ROOT))
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--runtime-lock", default=str(DEFAULT_RUNTIME_LOCK))
    parser.add_argument("--first-stage-script", default=None)
    parser.add_argument("--first-stage-result-path", default=None)
    parser.add_argument("--second-stage-script", default=None)
    parser.add_argument("--second-stage-result-path", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dry_run = args.dry_run or not args.allow_app_run
    dispatcher = FeishuPricingDispatcher(
        task_root=args.task_root,
        data_dir=args.data_dir,
        runtime_lock_path=args.runtime_lock,
        first_stage_script=args.first_stage_script,
        first_stage_result_path=args.first_stage_result_path,
        second_stage_script=args.second_stage_script,
        second_stage_result_path=args.second_stage_result_path,
        auto_send_result=bool(args.allow_app_run and not dry_run),
        send_result_live=bool(args.allow_app_run and not dry_run),
    )
    if args.once:
        result = dispatcher.dispatch_once(dry_run=dry_run, allow_app_run=args.allow_app_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1

    if dry_run:
        result = {
            "ok": False,
            "dry_run": True,
            "errors": ["DISPATCHER_LOOP_REQUIRES_ALLOW_APP_RUN"],
            "message": "--loop requires --allow-app-run for real deployment; use --once --dry-run for inspection.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    while True:
        result = dispatcher.dispatch_once(dry_run=False, allow_app_run=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        time.sleep(max(args.poll_seconds, 1.0))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
