"""Local controlled runner for Feishu Phase 2/3 pricing tasks."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable
import uuid

try:
    from adb_target_device import TARGET_ADB_SERIAL_NOT_CONFIGURED, validate_target_serial_configured
    from current_target_task_builder import build_current_target_task, now_iso
    from feishu_send_message import send_text_message
    from feishu_result_formatter import write_feishu_result_preview
    from pricing_result_collector import (
        CONFIG_MISMATCH_HARD_STOP,
        CONTINUE_NEXT_REFERENCE,
        CROSS_TASK_PRICING_RESULT_REJECTED,
        RESULT_MISSING_REQUIRED_PRICING_FIELDS,
        TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED,
        PricingResultCollector,
        is_automatic_pricing_terminal_success,
        normalize_pricing_result_fields,
        pricing_result_business_status,
        pricing_result_config_mismatch_reason,
        pricing_result_manual_review_reasons,
        pricing_success_missing_required_fields,
        resolve_pricing_result_field,
        stamp_result_task_scope,
        target_fingerprints_from_artifacts,
        validate_result_task_scope,
        validate_pricing_result_payload,
    )
    from stage_result_validator import first_stage_s10_ready, result_file_is_stale, validate_first_stage_payload
    from target_info_correction_feedback import TARGET_INFO_NEEDS_CORRECTION, is_target_info_error, target_info_status_fields, write_target_info_correction_feedback
except ImportError:  # pragma: no cover - supports package-style imports in tests.
    from scripts.adb_target_device import TARGET_ADB_SERIAL_NOT_CONFIGURED, validate_target_serial_configured
    from scripts.current_target_task_builder import build_current_target_task, now_iso
    from scripts.feishu_send_message import send_text_message
    from scripts.feishu_result_formatter import write_feishu_result_preview
    from scripts.pricing_result_collector import (
        CONFIG_MISMATCH_HARD_STOP,
        CONTINUE_NEXT_REFERENCE,
        CROSS_TASK_PRICING_RESULT_REJECTED,
        RESULT_MISSING_REQUIRED_PRICING_FIELDS,
        TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED,
        PricingResultCollector,
        is_automatic_pricing_terminal_success,
        normalize_pricing_result_fields,
        pricing_result_business_status,
        pricing_result_config_mismatch_reason,
        pricing_result_manual_review_reasons,
        pricing_success_missing_required_fields,
        resolve_pricing_result_field,
        stamp_result_task_scope,
        target_fingerprints_from_artifacts,
        validate_result_task_scope,
        validate_pricing_result_payload,
    )
    from scripts.stage_result_validator import first_stage_s10_ready, result_file_is_stale, validate_first_stage_payload
    from scripts.target_info_correction_feedback import TARGET_INFO_NEEDS_CORRECTION, is_target_info_error, target_info_status_fields, write_target_info_correction_feedback


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_TASK_ROOT = DATA_DIR / "feishu_tasks"
DEFAULT_RUNTIME_LOCK = PROJECT_ROOT / "runtime" / "pricing.lock"
DEFAULT_FIRST_STAGE_SCRIPT = Path("scripts") / "runtime_s01_to_s10_mainline.py"
DEFAULT_SECOND_STAGE_SCRIPT = Path("scripts") / "runtime_s10_to_s16_mainline.py"
DEFAULT_FIRST_STAGE_RESULT = Path("output") / "result_s01_to_s10.json"
DEFAULT_SECOND_STAGE_RESULT = Path("output") / "result_s10_to_s16.json"
DEFAULT_MAIN_SCRIPT_CANDIDATES: tuple[Path, ...] = (
    Path("runtime_s10_to_s16_mainline.py"),
    Path("scripts") / "runtime_s10_to_s16_mainline.py",
    Path("全程跑通.py"),
    Path("scripts") / "全程跑通.py",
)

DEFAULT_REVALIDATION_SERVICE_FEE_TIERS: tuple[dict[str, int], ...] = (
    {"min_price_yuan": 200000, "service_fee_yuan": 5000},
    {"min_price_yuan": 150000, "service_fee_yuan": 4000},
    {"min_price_yuan": 100000, "service_fee_yuan": 3500},
    {"min_price_yuan": 50000, "service_fee_yuan": 3000},
    {"min_price_yuan": 0, "service_fee_yuan": 2500},
)

DEFAULT_REVALIDATION_MIN_PROFIT_TIERS: tuple[dict[str, int], ...] = (
    {"min_price_yuan": 150000, "min_profit_yuan": 10000},
    {"min_price_yuan": 100000, "min_profit_yuan": 6500},
    {"min_price_yuan": 50000, "min_profit_yuan": 4500},
    {"min_price_yuan": 0, "min_profit_yuan": 2500},
)

DEFAULT_REVALIDATION_COST_RULES: tuple[dict[str, int], ...] = (
    {"lte_price_yuan": 50000, "cost_yuan": 600},
    {"lte_price_yuan": 100000, "cost_yuan": 1000},
)

MANUAL_PRICE_WAITING_STATUSES = {"WAITING_MANUAL_PRICE", "NEEDS_REVIEW", "MANUAL_REVIEW_REQUIRED"}
TASK_LOCAL_MANUAL_REVIEW_RESULT_STATUSES = {
    "FULL_CHAIN_MANUAL_REVIEW_DONE",
    "WAITING_MANUAL_PRICE",
    "NEEDS_REVIEW",
    "MANUAL_REVIEW_REQUIRED",
    "SECOND_STAGE_COLLECTION_INCOMPLETE",
    "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
    "V3_BOUNDARY_NOT_CONFIRMED_AFTER_ALL_REFERENCES",
}

ERROR_CODES = (
    "TASK_NOT_FOUND",
    "TASK_NOT_CONFIRMED",
    "TASK_NOT_QUEUED",
    "TASK_CANCELLED",
    "TASK_INVALID",
    "TASK_ALREADY_FINISHED",
    "TARGET_TASK_DRAFT_MISSING",
    "STATUS_JSON_MISSING",
    "CURRENT_TARGET_TASK_EXISTS_BACKED_UP",
    "CURRENT_TARGET_TASK_MISSING",
    "CURRENT_TARGET_TASK_TASK_ID_MISMATCH",
    "PRICING_LOCK_EXISTS",
    "CURRENT_TARGET_TASK_WRITE_FAILED",
    "INVALID_MODE",
    "APP_RUN_CONFIRMATION_REQUIRED",
    "TARGET_ADB_SERIAL_NOT_CONFIGURED",
    "TARGET_ADB_DEVICE_NOT_CONNECTED",
    "TARGET_ADB_DEVICE_UNAUTHORIZED",
    "TARGET_ADB_DEVICE_OFFLINE",
    "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT",
    "MAIN_SCRIPT_NOT_FOUND",
    "MAIN_SCRIPT_FAILED",
    "RESULT_FILE_NOT_FOUND",
    "RESULT_JSON_INVALID",
    "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY",
    "RESULT_SCHEMA_INVALID_FOR_PRICING",
    "STALE_RESULT_FILE",
    "MAIN_SCRIPT_NOOP_OR_STALE_RESULT",
    "RESULT_FORMAT_FAILED",
    "MANUAL_CONFIRM_REQUIRES_NEEDS_REVIEW",
    "MANUAL_CONFIRM_PRICE_INVALID",
    "MANUAL_CONFIRM_REVIEW_REASON_MISSING",
    "SYSTEM_SUGGESTED_PRICE_MISSING",
    "FEISHU_RESULT_PREVIEW_NOT_FOUND",
    "FEISHU_RESULT_PREVIEW_EMPTY",
    "FEISHU_CHAT_ID_MISSING",
    "SEND_RESULT_REQUIRES_MANUAL_CONFIRMATION",
    "SEND_RESULT_STATUS_NOT_READY",
    "FEISHU_RESULT_SEND_FAILED",
    "LOCK_RELEASE_FAILED",
    "TASK_NOT_FAILED",
    "TASK_NOT_REQUEUEABLE_TO_SECOND_STAGE",
    "FORCE_REQUEUE_ERROR_NOT_ALLOWED",
    "REQUEUED_SECOND_STAGE_FROM_FAILED",
    "STALE_RUN_RESULT_IGNORED",
    "TASK_NOT_S10_READY",
    "TASK_NOT_QUEUE_HEAD",
    "ACTIVE_APP_TASK_EXISTS",
    "TARGET_INFO_VALIDATION_FAILED",
    "TARGET_INFO_NEEDS_CORRECTION",
    "WAITING_TARGET_INFO_CORRECTION",
    "TARGET_DATE_UNRECOGNIZED",
    "TARGET_MODEL_UNRECOGNIZED",
    "TARGET_BRAND_SERIES_INFERENCE_FAILED",
    "TARGET_BRAND_SERIES_CONFLICT",
    "TARGET_REQUIRED_FIELD_MISSING",
    "TARGET_FIELD_FORMAT_INVALID",
    "FIRST_STAGE_RESULT_NOT_FOUND",
    "FIRST_STAGE_RESULT_JSON_INVALID",
    "FIRST_STAGE_NOT_S10_READY",
    "FIRST_STAGE_TARGET_NOT_FOUND",
    "S05_TARGET_CONFIG_NOT_FOUND",
    "S05_TARGET_CONFIG_VISIBLE_BUT_NOT_MATCHED",
    "S05_TARGET_CONFIG_CLICK_FAILED",
    "S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED",
    "FIRST_STAGE_SCHEMA_INVALID",
    "DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS",
    "DESKTOP_UPGRADE_MODAL_DISMISS_FAILED",
    "HUMAN_LOGIN_REQUIRED",
    "LOGIN_REQUIRED_MANUAL",
    "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED",
    "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED",
    "S14_STALE_FIRST_LINE_BINDING_UNRESOLVED",
    "S14_CONTRACT_DEGRADED_NEEDS_REVIEW",
    "S14_DEGRADED_COLLECTION_THRESHOLD_EXCEEDED",
    "S14_DETAIL_POPUP_CLOSE_UNSAFE_OR_FAILED",
)

FIRST_STAGE_ADB_EVIDENCE_KEYS = (
    "target_adb_serial",
    "adb_serial_source",
    "adb_path",
    "adb_path_source",
    "adb_runtime_env_mode",
    "use_isolated_adb_home",
    "adb_vendor_keys_configured",
    "adb_vendor_keys_path_summary",
    "adb_vendor_keys_exists",
    "output_adb_home_exists",
    "output_adb_home_android_dir_exists",
    "android_adb_server_port",
    "adb_devices_l_raw",
    "parsed_devices",
    "target_device_state",
    "target_device_present_before_first_stage",
    "device_snapshot_taken_at",
    "device_snapshot_error",
    "adb_command_preview",
    "cwd",
    "project_root",
    "python_executable",
)

FORCE_REQUEUE_ALLOWED_ERRORS = {
    "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY",
    "RESULT_SCHEMA_INVALID_FOR_PRICING",
    "STALE_RESULT_FILE",
    "MAIN_SCRIPT_NOOP_OR_STALE_RESULT",
    "FIRST_STAGE_NOT_S10_READY",
    "FIRST_STAGE_TARGET_NOT_FOUND",
    "S05_TARGET_CONFIG_NOT_FOUND",
    "S05_TARGET_CONFIG_VISIBLE_BUT_NOT_MATCHED",
    "S05_TARGET_CONFIG_CLICK_FAILED",
    "S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED",
    "FIRST_STAGE_SCHEMA_INVALID",
    "DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS",
    "DESKTOP_UPGRADE_MODAL_DISMISS_FAILED",
    "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED",
    "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED",
    "S14_STALE_FIRST_LINE_BINDING_UNRESOLVED",
    "S14_CONTRACT_DEGRADED_NEEDS_REVIEW",
    "S14_DEGRADED_COLLECTION_THRESHOLD_EXCEEDED",
    "S14_DETAIL_POPUP_CLOSE_UNSAFE_OR_FAILED",
}

DIAGNOSE_KEYWORDS: tuple[str, ...] = (
    'if __name__ == "__main__"',
    "if __name__ == '__main__'",
    "def main",
    "S01",
    "S10_READY",
    "result_s01_to_s10",
    "result_s10_to_s16",
    "PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE",
    "uiautomator",
    "adb",
    "app_start",
    "app_current",
    "com.guazi",
    "瓜子",
    "全程",
    "pipeline",
    "mainline",
    "S01_TO_S10",
    "S10_TO_S16",
)


class PricingRunner:
    def __init__(
        self,
        *,
        task_root: str | Path = DEFAULT_TASK_ROOT,
        data_dir: str | Path = DATA_DIR,
        runtime_lock_path: str | Path = DEFAULT_RUNTIME_LOCK,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.task_root = Path(task_root)
        self.data_dir = Path(data_dir)
        self.project_root = self.data_dir.parent
        self.current_target_task_path = self.data_dir / "current_target_task.json"
        self.backup_dir = self.data_dir / "backup"
        self.runtime_lock_path = Path(runtime_lock_path)
        self.clock = clock or _now_utc

    def dry_run(self, task_id: str) -> dict[str, Any]:
        return self._prepare(task_id, mode="dry-run", prepare_current_task=False)

    def prepare_current_task(self, task_id: str) -> dict[str, Any]:
        return self._prepare(task_id, mode="prepare-current-task", prepare_current_task=True)

    def auto_prepare_queued_current_task(self, task_id: str, *, mode: str = "auto-prepare-current-task") -> dict[str, Any]:
        """Prepare data/current_target_task.json only for the queue head."""
        task_dir = self.task_dir(task_id)
        if not task_dir.exists():
            return self._write_error(task_id, mode, None, ["TASK_NOT_FOUND"], [])
        status_path = task_dir / "status.json"
        if not status_path.exists():
            return self._write_error(task_id, mode, None, ["STATUS_JSON_MISSING"], [])
        status_payload = self._read_json(status_path)
        status_before = status_payload.get("status")
        if status_before != "QUEUED":
            return self._write_error(task_id, mode, status_before, [self._stage_status_error(status_before, "QUEUED")], [])
        if self.runtime_lock_path.exists():
            return self._write_error(task_id, mode, status_before, ["PRICING_LOCK_EXISTS"], [])
        active_task_id = self._active_app_task_id(exclude_task_id=task_id)
        if active_task_id:
            return self._write_error(
                task_id,
                mode,
                status_before,
                ["ACTIVE_APP_TASK_EXISTS"],
                [],
                extra={"active_task_id": active_task_id},
            )
        queue_head = self._queue_head_task_id()
        if queue_head != task_id:
            return self._write_error(
                task_id,
                mode,
                status_before,
                ["TASK_NOT_QUEUE_HEAD"],
                [],
                extra={"queue_head_task_id": queue_head},
            )
        draft_path = task_dir / "target_task_draft.json"
        if not draft_path.exists():
            return self._write_error(task_id, mode, status_before, ["TARGET_TASK_DRAFT_MISSING"], [])
        draft = self._read_json(draft_path)
        build_result = build_current_target_task(draft, clock=self.clock)
        if not build_result.valid:
            return self._write_target_info_correction_error(
                task_id,
                mode,
                status_before,
                ["MISSING_REQUIRED_FIELDS"],
                build_result.missing_fields,
                draft=draft,
            )
        validation = self._validation_payload(
            task_id=task_id,
            valid=True,
            mode=mode,
            status_before=status_before,
            missing_fields=[],
            warnings=build_result.warnings,
            errors=[],
        )
        try:
            backup_path = self._backup_existing_current_target_task(validation)
            self._write_json(self.current_target_task_path, build_result.current_target_task)
            self._write_json(task_dir / "current_target_task.preview.json", build_result.current_target_task)
            self._write_json(task_dir / "current_target_task.snapshot.json", build_result.current_target_task)
            if backup_path:
                validation["backup_path"] = str(backup_path)
        except OSError as exc:
            return self._write_error(
                task_id,
                mode,
                status_before,
                ["CURRENT_TARGET_TASK_WRITE_FAILED"],
                [],
                extra={"exception": str(exc)},
            )
        self._write_json(task_dir / "runner_validation.json", validation)
        self._append_audit(task_id=task_id, action=mode, status_before=status_before, status_after=status_before, success=True, errors=[])
        return {
            "ok": True,
            "task_id": task_id,
            "mode": mode,
            "status_before": status_before,
            "status_after": status_before,
            "queue_head_task_id": queue_head,
            "current_target_task_task_id_match": True,
            "validation": validation,
            "current_target_task": build_result.current_target_task,
        }

    def status(self, task_id: str) -> dict[str, Any]:
        task_dir = self.task_dir(task_id)
        status_payload = self._read_json(task_dir / "status.json") if (task_dir / "status.json").exists() else None
        first_stage_payload = self._load_first_stage_payload(task_dir)
        current_task_match = None
        if self.current_target_task_path.exists():
            try:
                current_task_match = str((self._read_json(self.current_target_task_path) or {}).get("task_id") or "") == task_id
            except (OSError, json.JSONDecodeError):
                current_task_match = False
        status_value = status_payload.get("status") if status_payload else None
        latest_run_id = str(status_payload.get("latest_run_id") or "") if status_payload else ""
        latest_generation_id = str(status_payload.get("generation_id") or "") if status_payload else ""
        runner_artifacts = self._task_runner_artifact_summary(task_dir, latest_run_id) if task_dir.exists() else {"errors": [], "warnings": []}
        last_error_code = runner_artifacts["errors"][0] if runner_artifacts["errors"] else None
        first_stage_ready = first_stage_s10_ready(first_stage_payload)
        technical_status = status_payload.get("technical_status") if status_payload else None
        business_status = status_payload.get("business_status") if status_payload else None
        if business_status is None and status_value in {"SUCCEEDED", "NEEDS_REVIEW"} and task_dir.exists() and status_payload:
            existing_result = self._find_revalidation_pricing_result(task_dir, status_payload)
            if existing_result.get("ok") and existing_result.get("pricing_result"):
                business_status = pricing_result_business_status(existing_result["pricing_result"])
                if technical_status is None:
                    technical_status = "SUCCEEDED"
        effective_status_for_action = business_status if business_status == "NEEDS_REVIEW" else status_value
        recommended_next_action = (status_payload or {}).get("recommended_next_action") or self._recommended_next_action(effective_status_for_action, last_error_code)
        if status_value in {"FAILED", "NEEDS_REVIEW_BLOCKED"} and first_stage_ready and current_task_match is True:
            recommended_next_action = "requeue-second-stage"
        return {
            "task_id": task_id,
            "status": status_value,
            "technical_status": technical_status,
            "business_status": business_status,
            "latest_run_id": latest_run_id or None,
            "generation_id": latest_generation_id or None,
            "task_dir_exists": task_dir.exists(),
            "target_task_draft_exists": (task_dir / "target_task_draft.json").exists(),
            "current_target_task_preview_exists": (task_dir / "current_target_task.preview.json").exists(),
            "current_target_task_snapshot_exists": (task_dir / "current_target_task.snapshot.json").exists(),
            "first_stage_result_exists": (task_dir / "first_stage_result.json").exists() or (PROJECT_ROOT / DEFAULT_FIRST_STAGE_RESULT).exists(),
            "first_stage_s10_ready": first_stage_ready,
            "pricing_result_exists": (task_dir / "pricing_result.json").exists(),
            "last_error_code": last_error_code,
            "runner_warnings": runner_artifacts["warnings"],
            "current_target_task_task_id_match": current_task_match,
            "recommended_next_action": recommended_next_action,
            "changed": False,
        }

    def run_manual(
        self,
        task_id: str,
        *,
        allow_app_run: bool = False,
        main_script: str | Path | None = None,
        result_path: str | Path | None = None,
    ) -> dict[str, Any]:
        self._append_audit(task_id=task_id, action="run_manual_requested", status_before=None, status_after=None, success=True, errors=[])
        if not allow_app_run:
            self._append_audit(
                task_id=task_id,
                action="app_run_confirmation_missing",
                status_before=None,
                status_after=None,
                success=False,
                errors=["APP_RUN_CONFIRMATION_REQUIRED"],
            )
            return self._write_error(
                task_id,
                "run-manual",
                None,
                ["APP_RUN_CONFIRMATION_REQUIRED"],
                [],
                extra={"message": "即将启动瓜子 APP 自动化主流程，必须显式增加 --allow-app-run 才允许执行。"},
            )

        task_dir = self.task_dir(task_id)
        if not task_dir.exists():
            return self._write_error(task_id, "run-manual", None, ["TASK_NOT_FOUND"], [])

        status_path = task_dir / "status.json"
        if not status_path.exists():
            return self._write_error(task_id, "run-manual", None, ["STATUS_JSON_MISSING"], [])
        status_payload = self._read_json(status_path)
        status_before = status_payload.get("status")
        status_error = self._manual_run_status_error(status_before)
        if status_error:
            self._append_audit(
                task_id=task_id,
                action="run_rejected_by_status",
                status_before=status_before,
                status_after=status_before,
                success=False,
                errors=[status_error],
            )
            return self._write_error(task_id, "run-manual", status_before, [status_error], [])

        if not self.current_target_task_path.exists():
            return self._write_error(task_id, "run-manual", status_before, ["CURRENT_TARGET_TASK_MISSING"], [])
        current_task = self._read_json(self.current_target_task_path)
        if str(current_task.get("task_id") or "") != task_id:
            return self._write_error(
                task_id,
                "run-manual",
                status_before,
                ["CURRENT_TARGET_TASK_TASK_ID_MISMATCH"],
                [],
                extra={"current_target_task_id": current_task.get("task_id")},
            )

        target_serial_check = validate_target_serial_configured(project_root=self.project_root)
        if not target_serial_check.get("ok"):
            return self._write_error(
                task_id,
                "run-manual",
                status_before,
                [str(target_serial_check.get("code") or TARGET_ADB_SERIAL_NOT_CONFIGURED)],
                [],
                extra={
                    "target_adb_device": target_serial_check.get("target"),
                    "strict_device_selection": True,
                    "no_default_device_fallback": True,
                },
            )

        if self.runtime_lock_path.exists():
            return self._write_error(task_id, "run-manual", status_before, ["PRICING_LOCK_EXISTS"], [])

        script_path = self._resolve_main_script(main_script)
        if script_path is None:
            return self._write_error(task_id, "run-manual", status_before, ["MAIN_SCRIPT_NOT_FOUND"], [])

        return self._execute_manual_run(
            task_id=task_id,
            task_dir=task_dir,
            script_path=script_path,
            status_before=status_before,
            result_path=result_path,
        )

    def requeue_failed(self, task_id: str, *, force_requeue_invalid_success: bool = False) -> dict[str, Any]:
        task_dir = self.task_dir(task_id)
        if not task_dir.exists():
            return self._write_error(task_id, "requeue-failed", None, ["TASK_NOT_FOUND"], [])
        status_path = task_dir / "status.json"
        if not status_path.exists():
            return self._write_error(task_id, "requeue-failed", None, ["STATUS_JSON_MISSING"], [])
        status_payload = self._read_json(status_path)
        status_before = status_payload.get("status")
        if status_before == "FAILED":
            self._set_task_status(task_id, "QUEUED")
            self._append_audit(task_id=task_id, action="requeue_failed", status_before="FAILED", status_after="QUEUED", success=True, errors=[])
            return {"ok": True, "task_id": task_id, "status_before": "FAILED", "status_after": "QUEUED"}
        if status_before == "SUCCEEDED" and force_requeue_invalid_success:
            errors = self._task_runner_errors(task_dir)
            allowed = [error for error in errors if error in FORCE_REQUEUE_ALLOWED_ERRORS]
            if allowed:
                self._set_task_status(task_id, "QUEUED")
                self._append_audit(task_id=task_id, action="force_requeue_invalid_success", status_before="SUCCEEDED", status_after="QUEUED", success=True, errors=allowed)
                return {
                    "ok": True,
                    "task_id": task_id,
                    "status_before": "SUCCEEDED",
                    "status_after": "QUEUED",
                    "forced_by_errors": allowed,
                }
            return self._write_error(task_id, "requeue-failed", "SUCCEEDED", ["FORCE_REQUEUE_ERROR_NOT_ALLOWED"], [])
        if status_before == "SUCCEEDED":
            return self._write_error(task_id, "requeue-failed", "SUCCEEDED", ["TASK_ALREADY_FINISHED"], [])
        return self._write_error(task_id, "requeue-failed", status_before, ["TASK_NOT_FAILED"], [])

    def requeue_second_stage(self, task_id: str) -> dict[str, Any]:
        task_dir = self.task_dir(task_id)
        mode = "requeue-second-stage"
        run_id = self._new_run_id(mode)
        generation_id = run_id
        if not task_dir.exists():
            return self._write_error(task_id, mode, None, ["TASK_NOT_FOUND"], [], run_id=run_id, generation_id=generation_id)
        status_path = task_dir / "status.json"
        if not status_path.exists():
            return self._write_error(task_id, mode, None, ["STATUS_JSON_MISSING"], [], run_id=run_id, generation_id=generation_id)
        status_payload = self._read_json(status_path)
        status_before = status_payload.get("status")
        if status_before not in {"FAILED", "NEEDS_REVIEW_BLOCKED"}:
            return self._write_error(
                task_id,
                mode,
                status_before,
                ["TASK_NOT_REQUEUEABLE_TO_SECOND_STAGE"],
                [],
                run_id=run_id,
                generation_id=generation_id,
            )
        if not self.current_target_task_path.exists():
            return self._write_error(task_id, mode, status_before, ["CURRENT_TARGET_TASK_MISSING"], [], run_id=run_id, generation_id=generation_id)
        current_task = self._read_json(self.current_target_task_path)
        if str(current_task.get("task_id") or "") != task_id:
            return self._write_error(
                task_id,
                mode,
                status_before,
                ["CURRENT_TARGET_TASK_TASK_ID_MISMATCH"],
                [],
                extra={"current_target_task_id": current_task.get("task_id")},
                run_id=run_id,
                generation_id=generation_id,
            )
        first_stage_errors = self._validate_task_first_stage_result(task_dir)
        if first_stage_errors:
            return self._write_error(task_id, mode, status_before, first_stage_errors, [], run_id=run_id, generation_id=generation_id)
        status_after = "S10_READY"
        self._set_task_status(task_id, status_after, run_id=run_id, generation_id=generation_id)
        payload = {
            "task_id": task_id,
            "ok": True,
            "mode": mode,
            "status_before": status_before,
            "status_after": status_after,
            "status": status_after,
            "run_id": run_id,
            "generation_id": generation_id,
            "requeue_code": "REQUEUED_SECOND_STAGE_FROM_FAILED",
            "recommended_next_action": "run-second-stage",
            "first_stage_s10_ready": True,
            "current_target_task_task_id_match": True,
            "changed": True,
        }
        self._write_json(task_dir / "runner_result.json", payload)
        self._append_audit(
            task_id=task_id,
            action=mode,
            status_before=status_before,
            status_after=status_after,
            success=True,
            errors=[],
        )
        return payload

    def diagnose_main_entry(self) -> dict[str, Any]:
        return diagnose_main_entry(PROJECT_ROOT)

    def run_first_stage(
        self,
        task_id: str,
        *,
        allow_app_run: bool = False,
        first_stage_script: str | Path | None = None,
        first_stage_result_path: str | Path | None = None,
    ) -> dict[str, Any]:
        self._append_audit(task_id=task_id, action="run_first_stage_requested", status_before=None, status_after=None, success=True, errors=[])
        precheck = self._precheck_stage_run(
            task_id,
            mode="run-first-stage",
            allow_app_run=allow_app_run,
            expected_status="QUEUED",
        )
        if not precheck["ok"]:
            return precheck
        task_dir = precheck["task_dir"]
        status_before = precheck["status_before"]
        script_path = self._resolve_stage_script(first_stage_script, DEFAULT_FIRST_STAGE_SCRIPT)
        if script_path is None:
            return self._write_error(task_id, "run-first-stage", status_before, ["MAIN_SCRIPT_NOT_FOUND"], [])
        result_path = self._resolve_stage_result_path(first_stage_result_path, DEFAULT_FIRST_STAGE_RESULT)
        return self._execute_first_stage(
            task_id=task_id,
            task_dir=task_dir,
            script_path=script_path,
            result_path=result_path,
            status_before=status_before,
        )

    def run_second_stage(
        self,
        task_id: str,
        *,
        allow_app_run: bool = False,
        second_stage_script: str | Path | None = None,
        second_stage_result_path: str | Path | None = None,
    ) -> dict[str, Any]:
        self._append_audit(task_id=task_id, action="run_second_stage_requested", status_before=None, status_after=None, success=True, errors=[])
        task_dir = self.task_dir(task_id)
        existing_terminal = self._find_existing_terminal_success_result(task_id, task_dir) if task_dir.exists() else None
        if existing_terminal is not None:
            existing_source = "terminal_success_recovered_from_pre_run_backup" if existing_terminal.get("terminal_success_recovered_from_backup") else "existing_terminal_success_before_second_stage"
            return self.persist_terminal_success_result(
                task_id,
                existing_terminal,
                source=existing_source,
            )
        precheck = self._precheck_stage_run(
            task_id,
            mode="run-second-stage",
            allow_app_run=allow_app_run,
            expected_status="S10_READY",
        )
        if not precheck["ok"]:
            return precheck
        task_dir = precheck["task_dir"]
        status_before = precheck["status_before"]
        first_stage_errors = self._validate_existing_first_stage_result(task_dir)
        if first_stage_errors:
            return self._write_error(task_id, "run-second-stage", status_before, first_stage_errors, [])
        script_path = self._resolve_stage_script(second_stage_script, DEFAULT_SECOND_STAGE_SCRIPT)
        if script_path is None:
            return self._write_error(task_id, "run-second-stage", status_before, ["MAIN_SCRIPT_NOT_FOUND"], [])
        result_path = self._resolve_stage_result_path(second_stage_result_path, DEFAULT_SECOND_STAGE_RESULT)
        return self._execute_second_stage(
            task_id=task_id,
            task_dir=task_dir,
            script_path=script_path,
            result_path=result_path,
            status_before=status_before,
        )

    def revalidate_result(self, task_id: str) -> dict[str, Any]:
        task_dir = self.task_dir(task_id)
        if not task_dir.exists():
            return self._write_error(task_id, "revalidate-result", None, ["TASK_NOT_FOUND"], [])
        status_path = task_dir / "status.json"
        if not status_path.exists():
            return self._write_error(task_id, "revalidate-result", None, ["STATUS_JSON_MISSING"], [])
        status_payload = self._read_json(status_path)
        status_before = status_payload.get("status")
        previous_business_status = status_payload.get("business_status")
        result_lookup = self._find_revalidation_pricing_result(task_dir, status_payload)
        errors = list(result_lookup.get("errors") or [])
        warnings = list(result_lookup.get("warnings") or [])
        pricing_result = result_lookup.get("pricing_result")
        source_path = result_lookup.get("source_path")
        pricing_chain_refreshed = False
        latest_run_id = str(status_payload.get("latest_run_id") or "")
        latest_generation_id = str(status_payload.get("generation_id") or "")
        revalidation_run_id = self._new_run_id("revalidate-result")
        result_run_id = latest_run_id or revalidation_run_id
        result_generation_id = latest_generation_id or result_run_id

        if errors:
            if RESULT_MISSING_REQUIRED_PRICING_FIELDS in errors and isinstance(pricing_result, dict):
                final_status = RESULT_MISSING_REQUIRED_PRICING_FIELDS
                technical_status = "INCOMPLETE"
                business_status = final_status
            else:
                final_status = "FAILED"
                technical_status = "FAILED"
                business_status = "FAILED"
        else:
            pricing_result, pricing_chain_refreshed = self._refresh_revalidation_pricing_chain(pricing_result)
            final_status = pricing_result_business_status(pricing_result)
            if final_status in {CONTINUE_NEXT_REFERENCE, RESULT_MISSING_REQUIRED_PRICING_FIELDS}:
                technical_status = "INCOMPLETE"
                if final_status == RESULT_MISSING_REQUIRED_PRICING_FIELDS:
                    missing_fields = pricing_success_missing_required_fields(pricing_result)
                    errors = _dedupe([
                        RESULT_MISSING_REQUIRED_PRICING_FIELDS,
                        *[f"MISSING_REQUIRED_FIELD:{field_name}" for field_name in missing_fields],
                        *errors,
                    ])
            else:
                technical_status = "SUCCEEDED"
            business_status = final_status
            target_path = task_dir / "pricing_result.json"
            if pricing_result is not None:
                self._write_json(target_path, pricing_result)
            self._stamp_run_identity(target_path, run_id=result_run_id, generation_id=result_generation_id)

        config_mismatch_reason = pricing_result_config_mismatch_reason(pricing_result)
        status_extra: dict[str, Any] = {}
        if config_mismatch_reason:
            final_status = TARGET_INFO_NEEDS_CORRECTION
            business_status = TARGET_INFO_NEEDS_CORRECTION
            technical_status = "VALIDATION_FAILED"
            errors = _dedupe([CONFIG_MISMATCH_HARD_STOP, config_mismatch_reason, *errors])
            status_extra = {
                "canonical_error_code": CONFIG_MISMATCH_HARD_STOP,
                "mismatch_reason": config_mismatch_reason,
                "human_reason": "车型配置无法确认一致，为避免错误定价已停止自动定价。",
                "blocks_queue": False,
                "auto_pricing_allowed": False,
                "final_price_allowed": False,
                "needs_resend_target_info": True,
            }
        elif final_status == RESULT_MISSING_REQUIRED_PRICING_FIELDS:
            status_extra = {
                "canonical_error_code": RESULT_MISSING_REQUIRED_PRICING_FIELDS,
                "human_reason": "定价结果缺少完整参考车或价格链，系统未输出自动定价结果。",
                "blocks_queue": False,
                "auto_pricing_allowed": False,
                "final_price_allowed": False,
                "missing_required_fields": pricing_success_missing_required_fields(pricing_result),
            }
        elif final_status == CONTINUE_NEXT_REFERENCE:
            status_extra = {
                "canonical_error_code": CONTINUE_NEXT_REFERENCE,
                "human_reason": "参考车边界尚未闭合，需要继续采集下一台参考车。",
                "blocks_queue": False,
                "auto_pricing_allowed": False,
                "final_price_allowed": False,
            }
        recommended_next_action = self._recommended_next_action(final_status, errors[0] if errors else None)
        changed = status_before != final_status or previous_business_status != business_status or pricing_chain_refreshed
        self._set_task_status(
            task_id,
            final_status,
            run_id=result_run_id,
            generation_id=result_generation_id,
            technical_status=technical_status,
            business_status=business_status,
            recommended_next_action=recommended_next_action,
            extra_fields=status_extra,
        )

        formatted_warnings: list[str] = []
        try:
            preview_status = "FAILED" if final_status == RESULT_MISSING_REQUIRED_PRICING_FIELDS else final_status
            formatted = write_feishu_result_preview(task_dir=task_dir, task_id=task_id, status=preview_status, errors=errors)
            formatted_warnings = formatted.warnings
        except Exception as exc:  # pragma: no cover - defensive path keeps structured artifacts.
            errors = _dedupe(errors + ["RESULT_FORMAT_FAILED"])
            warnings.append(f"RESULT_FORMAT_EXCEPTION:{type(exc).__name__}")

        payload = {
            "ok": not errors,
            "task_id": task_id,
            "status": final_status,
            "previous_status": status_before,
            "new_status": final_status,
            "changed": changed,
            "technical_status": technical_status,
            "business_status": business_status,
            "recommended_next_action": recommended_next_action,
            "run_id": result_run_id,
            "generation_id": result_generation_id,
            "revalidation_run_id": revalidation_run_id,
            "result_source_path": str(source_path) if source_path else None,
            "pricing_chain_refreshed": pricing_chain_refreshed,
            "errors": errors,
            "warnings": _dedupe(warnings),
        }
        if pricing_result and not config_mismatch_reason:
            payload["profit_yuan"] = resolve_pricing_result_field(pricing_result, "profit_yuan")
            payload["suggested_purchase_price_yuan"] = resolve_pricing_result_field(pricing_result, "suggested_purchase_price_yuan")
        if config_mismatch_reason:
            payload.update(status_extra)
        if status_extra and not config_mismatch_reason:
            payload.update(status_extra)
        if formatted_warnings:
            payload["formatter_warnings"] = formatted_warnings
        self._write_json(task_dir / ("runner_result.json" if payload["ok"] else "runner_error.json"), payload)
        self._append_audit(task_id=task_id, action="revalidate_result", status_before=status_before, status_after=final_status, success=payload["ok"], errors=errors)
        return payload

    def manual_confirm_price(
        self,
        task_id: str,
        *,
        manual_confirm_price: int,
        manual_review_note: str,
        manual_confirm_by: str = "local_user",
        manual_confirm_raw_text: str | None = None,
        manual_confirm_task_id: str | None = None,
        manual_confirmed_by_role: str | None = None,
    ) -> dict[str, Any]:
        task_dir = self.task_dir(task_id)
        mode = "manual-confirm-price"
        if not task_dir.exists():
            return self._write_error(task_id, mode, None, ["TASK_NOT_FOUND"], [])
        status_path = task_dir / "status.json"
        if not status_path.exists():
            return self._write_error(task_id, mode, None, ["STATUS_JSON_MISSING"], [])
        status_payload = self._read_json(status_path)
        status_before = status_payload.get("status")
        business_status_before = status_payload.get("business_status")
        if status_before not in {"NEEDS_REVIEW", "WAITING_MANUAL_PRICE", "MANUAL_REVIEW_REQUIRED"} and business_status_before != "NEEDS_REVIEW":
            return self._write_error(task_id, mode, status_before, ["MANUAL_CONFIRM_REQUIRES_NEEDS_REVIEW"], [])
        if not isinstance(manual_confirm_price, int) or manual_confirm_price <= 0:
            return self._write_error(task_id, mode, status_before, ["MANUAL_CONFIRM_PRICE_INVALID"], [])

        result_lookup = self._find_revalidation_pricing_result(
            task_dir,
            status_payload,
            allow_task_local_manual_confirm_result=True,
        )
        errors = list(result_lookup.get("errors") or [])
        warnings = list(result_lookup.get("warnings") or [])
        pricing_result = result_lookup.get("pricing_result")
        source_path = result_lookup.get("source_path")
        if errors or not isinstance(pricing_result, dict):
            return self._write_error(task_id, mode, status_before, errors or ["RESULT_FILE_NOT_FOUND"], [])

        pricing_result, _ = self._refresh_revalidation_pricing_chain(pricing_result)
        reasons = pricing_result_manual_review_reasons(pricing_result or {})
        manual_review_context = self._manual_review_confirm_context(status_payload, pricing_result, reasons)
        if not reasons and not manual_review_context:
            return self._write_error(task_id, mode, status_before, ["MANUAL_CONFIRM_REVIEW_REASON_MISSING"], [])
        if not reasons:
            reasons = [str(status_payload.get("manual_review_reason_code") or "MANUAL_REVIEW_REQUIRED")]
        system_suggested_price = _coerce_int(resolve_pricing_result_field(pricing_result or {}, "suggested_purchase_price_yuan"))
        if system_suggested_price is None and not manual_review_context:
            return self._write_error(task_id, mode, status_before, ["SYSTEM_SUGGESTED_PRICE_MISSING"], [])

        confirmed_at = now_iso(self.clock)
        manual_adjustment_yuan = manual_confirm_price - system_suggested_price if system_suggested_price is not None else None
        system_suggested_price_missing = system_suggested_price is None
        confirmed_result = copy.deepcopy(pricing_result)
        pricing_section = confirmed_result.setdefault("pricing", {})
        pricing_result_run_id = str(result_lookup.get("pricing_result_run_id") or "")
        pricing_result_generation_id = str(result_lookup.get("pricing_result_generation_id") or "")
        pricing_result_source = str(result_lookup.get("pricing_result_source") or "")
        task_local_pricing_result_accepted = bool(result_lookup.get("task_local_pricing_result_accepted_for_manual_confirm"))
        confirmation_fields = {
            "manual_price_yuan": manual_confirm_price,
            "manual_confirmed_purchase_price_yuan": manual_confirm_price,
            "manual_review_note": manual_review_note,
            "manual_review_confirmed": True,
            "manual_review_confirmed_at": confirmed_at,
            "manual_review_confirmed_by": manual_confirm_by or "local_user",
            "manual_confirmed_at": confirmed_at,
            "manual_confirmed_by_open_id": manual_confirm_by or "local_user",
            "manual_confirmed_by_role": manual_confirmed_by_role or "supervisor_manual_review",
            "manual_confirm_raw_text": manual_confirm_raw_text,
            "manual_confirm_task_id": manual_confirm_task_id or task_id,
            "system_suggested_purchase_price_yuan": system_suggested_price,
            "system_suggested_price_yuan": system_suggested_price,
            "system_suggested_price_missing": system_suggested_price_missing,
            "system_suggested_price_required": False,
            "manual_adjustment_yuan": manual_adjustment_yuan,
            "final_purchase_price_yuan": manual_confirm_price,
            "final_price_source": "SUPERVISOR_MANUAL_CONFIRM",
            "pricing_decision_source": "MANUAL_SUPERVISOR_PRICE",
            "price_confirmed": True,
            "technical_status": "SUCCEEDED",
            "business_status": "MANUAL_REVIEW_CONFIRMED",
            "pricing_result_source": pricing_result_source or None,
            "pricing_result_run_id": pricing_result_run_id or None,
            "pricing_result_generation_id": pricing_result_generation_id or None,
            "task_local_pricing_result_accepted_for_manual_confirm": task_local_pricing_result_accepted,
        }
        for key, value in confirmation_fields.items():
            confirmed_result[key] = value
            pricing_section[key] = value
        confirmed_result["status"] = "MANUAL_REVIEW_CONFIRMED"

        run_id = self._new_run_id(mode)
        generation_id = run_id
        target_path = task_dir / "pricing_result.json"
        self._write_json(target_path, confirmed_result)
        self._stamp_run_identity(target_path, run_id=run_id, generation_id=generation_id)
        confirmed_result = self._read_json(target_path)

        final_status = "MANUAL_REVIEW_CONFIRMED"
        recommended_next_action = "ready-to-send"
        current_target_task_cleared = self._clear_current_target_task_if_matches(task_id)
        self._set_task_status(
            task_id,
            final_status,
            run_id=run_id,
            generation_id=generation_id,
            technical_status="SUCCEEDED",
            business_status=final_status,
            recommended_next_action=recommended_next_action,
            extra_fields={
                "waiting_manual_price": False,
                "manual_review_required": False,
                "manual_price_yuan": manual_confirm_price,
                "final_purchase_price_yuan": manual_confirm_price,
                "final_price_source": "SUPERVISOR_MANUAL_CONFIRM",
                "pricing_decision_source": "MANUAL_SUPERVISOR_PRICE",
                "system_suggested_price_yuan": system_suggested_price,
                "system_suggested_price_missing": system_suggested_price_missing,
                "system_suggested_price_required": False,
                "price_confirmed": True,
                "blocks_queue": False,
                "manual_confirmed_at": confirmed_at,
                "manual_confirmed_by_open_id": manual_confirm_by or "local_user",
                "manual_confirmed_by_role": manual_confirmed_by_role or "supervisor_manual_review",
                "manual_confirm_raw_text": manual_confirm_raw_text,
                "manual_confirm_task_id": manual_confirm_task_id or task_id,
                "manual_confirm_run_id": run_id,
                "pricing_result_source": pricing_result_source or None,
                "pricing_result_run_id": pricing_result_run_id or None,
                "pricing_result_generation_id": pricing_result_generation_id or None,
                "task_local_pricing_result_accepted_for_manual_confirm": task_local_pricing_result_accepted,
                "current_target_task_cleared": current_target_task_cleared,
                "current_target_task_clear_reason": "MANUAL_PRICE_CONFIRMED" if current_target_task_cleared else None,
            },
        )
        formatted = write_feishu_result_preview(task_dir=task_dir, task_id=task_id, status=final_status, errors=[])
        preview_path = task_dir / "feishu_result_reply.preview.txt"

        payload = {
            "ok": True,
            "task_id": task_id,
            "status": final_status,
            "previous_status": status_before,
            "technical_status": "SUCCEEDED",
            "business_status": final_status,
            "recommended_next_action": recommended_next_action,
            "run_id": run_id,
            "generation_id": generation_id,
            "result_source_path": str(source_path) if source_path else None,
            "pricing_result_source": pricing_result_source or None,
            "pricing_result_run_id": pricing_result_run_id or None,
            "pricing_result_generation_id": pricing_result_generation_id or None,
            "task_local_pricing_result_accepted_for_manual_confirm": task_local_pricing_result_accepted,
            "system_suggested_purchase_price_yuan": system_suggested_price,
            "system_suggested_price_yuan": system_suggested_price,
            "system_suggested_price_missing": system_suggested_price_missing,
            "system_suggested_price_required": False,
            "manual_price_yuan": manual_confirm_price,
            "manual_confirmed_purchase_price_yuan": manual_confirm_price,
            "final_purchase_price_yuan": manual_confirm_price,
            "manual_adjustment_yuan": manual_adjustment_yuan,
            "manual_review_note": manual_review_note,
            "manual_confirm_raw_text": manual_confirm_raw_text,
            "manual_confirm_task_id": manual_confirm_task_id or task_id,
            "manual_confirmed_by_role": manual_confirmed_by_role or "supervisor_manual_review",
            "final_price_source": "SUPERVISOR_MANUAL_CONFIRM",
            "pricing_decision_source": "MANUAL_SUPERVISOR_PRICE",
            "current_target_task_cleared": current_target_task_cleared,
            "preview_path": str(preview_path),
            "errors": [],
            "warnings": _dedupe(warnings),
        }
        if formatted.warnings:
            payload["formatter_warnings"] = formatted.warnings
        self._write_json(task_dir / "manual_confirm_result.json", payload)
        self._write_json(task_dir / "runner_result.json", payload)
        self._append_audit(task_id=task_id, action=mode, status_before=status_before, status_after=final_status, success=True, errors=[])
        return payload

    def _manual_review_confirm_context(
        self,
        status_payload: dict[str, Any],
        pricing_result: dict[str, Any],
        reasons: list[str],
    ) -> bool:
        status_value = str(status_payload.get("status") or "")
        business_status = str(status_payload.get("business_status") or "")
        pricing_statuses = {
            str(pricing_result.get("status") or ""),
            str(pricing_result.get("final_status") or ""),
            str(pricing_result.get("current_state") or ""),
        }
        return bool(
            status_value in {"WAITING_MANUAL_PRICE", "NEEDS_REVIEW", "MANUAL_REVIEW_REQUIRED"}
            or business_status == "NEEDS_REVIEW"
            or status_payload.get("manual_review_required") is True
            or status_payload.get("waiting_manual_price") is True
            or status_payload.get("manual_review_reason_code")
            or pricing_statuses & {"FULL_CHAIN_MANUAL_REVIEW_DONE", "NEEDS_REVIEW", "MANUAL_REVIEW_REQUIRED"}
            or reasons
        )

    def send_result(self, task_id: str, *, live: bool = False) -> dict[str, Any]:
        task_dir = self.task_dir(task_id)
        mode = "send-result"
        if not task_dir.exists():
            return self._send_result_error(task_id, mode, None, ["TASK_NOT_FOUND"], dry_run=not live)
        status_path = task_dir / "status.json"
        if not status_path.exists():
            return self._send_result_error(task_id, mode, None, ["STATUS_JSON_MISSING"], dry_run=not live)
        status_payload = self._read_json(status_path)
        status_before = str(status_payload.get("status") or "")
        business_status = str(status_payload.get("business_status") or "")
        recommended_next_action = status_payload.get("recommended_next_action")
        if status_before in {"NEEDS_REVIEW", "MANUAL_REVIEW_REQUIRED"} or business_status in {"NEEDS_REVIEW", "MANUAL_REVIEW_REQUIRED"}:
            return self._send_result_error(
                task_id,
                mode,
                status_before,
                ["SEND_RESULT_REQUIRES_MANUAL_CONFIRMATION"],
                dry_run=not live,
                extra={"message": "任务仍需人工复核，请先确认人工收车价。"},
            )
        if not self._send_result_status_ready(status_before, business_status, recommended_next_action):
            return self._send_result_error(task_id, mode, status_before, ["SEND_RESULT_STATUS_NOT_READY"], dry_run=not live)

        preview_path = task_dir / "feishu_result_reply.preview.txt"
        if not preview_path.exists():
            return self._send_result_error(task_id, mode, status_before, ["FEISHU_RESULT_PREVIEW_NOT_FOUND"], dry_run=not live)
        preview_text = preview_path.read_text(encoding="utf-8")
        if not preview_text.strip():
            return self._send_result_error(task_id, mode, status_before, ["FEISHU_RESULT_PREVIEW_EMPTY"], dry_run=not live)

        chat_id = self._find_task_chat_id(task_dir, status_payload)
        if not chat_id:
            return self._send_result_error(task_id, mode, status_before, ["FEISHU_CHAT_ID_MISSING"], dry_run=not live)
        chat_id_masked = _mask_chat_id(chat_id)

        send_result = send_text_message(text=preview_text, chat_id=chat_id, dry_run=not live)
        if not send_result.get("ok"):
            payload = {
                "ok": False,
                "dry_run": not live,
                "sent": False,
                "task_id": task_id,
                "status": status_before,
                "preview_path": str(preview_path),
                "chat_id_masked": chat_id_masked,
                "errors": [str(send_result.get("error_code") or "FEISHU_RESULT_SEND_FAILED")],
                "message": send_result.get("message"),
            }
            self._append_audit(task_id=task_id, action=mode, status_before=status_before, status_after=status_before, success=False, errors=payload["errors"])
            return payload

        payload = {
            "ok": True,
            "dry_run": not live,
            "sent": bool(live),
            "task_id": task_id,
            "status": status_before,
            "preview_path": str(preview_path),
            "chat_id_masked": chat_id_masked,
            "message_preview": preview_text,
            "message_length": len(preview_text),
            "errors": [],
            "warnings": [],
        }
        if live:
            sent_at = now_iso(self.clock)
            updated_status = copy.deepcopy(status_payload)
            updated_status.update(
                {
                    "status": "RESULT_SENT",
                    "business_status": "RESULT_SENT",
                    "technical_status": status_payload.get("technical_status") or "SUCCEEDED",
                    "sent_to_feishu": True,
                    "sent_to_feishu_at": sent_at,
                    "recommended_next_action": None,
                    "updated_at": sent_at,
                }
            )
            self._write_json(status_path, updated_status)
            payload.update(
                {
                    "status": "RESULT_SENT",
                    "previous_status": status_before,
                    "business_status": "RESULT_SENT",
                    "technical_status": updated_status["technical_status"],
                    "sent_to_feishu_at": sent_at,
                }
            )
            payload.pop("message_preview", None)
            self._append_audit(task_id=task_id, action=mode, status_before=status_before, status_after="RESULT_SENT", success=True, errors=[])
        else:
            self._append_audit(task_id=task_id, action="send_result_dry_run", status_before=status_before, status_after=status_before, success=True, errors=[])
        return payload

    def task_dir(self, task_id: str) -> Path:
        return self.task_root / task_id

    def _iter_task_statuses(self) -> list[tuple[str, dict[str, Any]]]:
        if not self.task_root.exists():
            return []
        rows: list[tuple[str, dict[str, Any]]] = []
        for child in self.task_root.iterdir():
            if not child.is_dir():
                continue
            status_path = child / "status.json"
            if not status_path.exists():
                continue
            try:
                status = self._read_json(status_path)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(status, dict):
                rows.append((child.name, status))
        return rows

    def _queued_task_ids(self) -> list[str]:
        candidates: list[tuple[str, str]] = []
        for task_id, status in self._iter_task_statuses():
            if status.get("status") != "QUEUED":
                continue
            stamp = str(status.get("queued_at") or status.get("confirmed_at") or status.get("updated_at") or status.get("created_at") or "")
            candidates.append((stamp, task_id))
        candidates.sort()
        return [task_id for _, task_id in candidates]

    def _queue_head_task_id(self) -> str | None:
        queued = self._queued_task_ids()
        return queued[0] if queued else None

    def _active_app_task_id(self, *, exclude_task_id: str | None = None) -> str | None:
        active_statuses = {"RUNNING_FIRST_STAGE", "RUNNING_SECOND_STAGE", "APP_CONTROL_LOCKED", "RUNNING"}
        for task_id, status in self._iter_task_statuses():
            if exclude_task_id and task_id == exclude_task_id:
                continue
            if status.get("status") in active_statuses:
                return task_id
        return None

    def _prepare(self, task_id: str, *, mode: str, prepare_current_task: bool) -> dict[str, Any]:
        if mode not in {"dry-run", "prepare-current-task"}:
            return self._write_error(task_id, mode, None, ["INVALID_MODE"], [])

        task_dir = self.task_dir(task_id)
        if not task_dir.exists():
            return self._write_error(task_id, mode, None, ["TASK_NOT_FOUND"], [])

        status_path = task_dir / "status.json"
        if not status_path.exists():
            return self._write_error(task_id, mode, None, ["STATUS_JSON_MISSING"], [])
        status_payload = self._read_json(status_path)
        status_before = status_payload.get("status")

        draft_path = task_dir / "target_task_draft.json"
        if not draft_path.exists():
            return self._write_error(task_id, mode, status_before, ["TARGET_TASK_DRAFT_MISSING"], [])

        status_error = self._phase2_status_error(status_before)
        if status_error:
            return self._write_error(task_id, mode, status_before, [status_error], [])

        if prepare_current_task and self.runtime_lock_path.exists():
            return self._write_error(task_id, mode, status_before, ["PRICING_LOCK_EXISTS"], [])

        draft = self._read_json(draft_path)
        build_result = build_current_target_task(draft, clock=self.clock)
        if not build_result.valid:
            return self._write_target_info_correction_error(
                task_id,
                mode,
                status_before,
                ["MISSING_REQUIRED_FIELDS"],
                build_result.missing_fields,
                draft=draft,
            )

        self._write_json(task_dir / "current_target_task.preview.json", build_result.current_target_task)
        validation = self._validation_payload(
            task_id=task_id,
            valid=True,
            mode=mode,
            status_before=status_before,
            missing_fields=[],
            warnings=build_result.warnings,
            errors=[],
        )

        if prepare_current_task:
            try:
                backup_path = self._backup_existing_current_target_task(validation)
                self._write_json(self.current_target_task_path, build_result.current_target_task)
                self._write_json(task_dir / "current_target_task.snapshot.json", build_result.current_target_task)
                self._set_task_status(task_id, "QUEUED")
                if backup_path:
                    validation["backup_path"] = str(backup_path)
            except OSError as exc:
                return self._write_error(
                    task_id,
                    mode,
                    status_before,
                    ["CURRENT_TARGET_TASK_WRITE_FAILED"],
                    [],
                    extra={"exception": str(exc)},
                )

        self._write_json(task_dir / "runner_validation.json", validation)
        self._append_audit(
            task_id=task_id,
            action=mode,
            status_before=status_before,
            status_after="QUEUED" if prepare_current_task else status_before,
            success=True,
            errors=[],
        )
        return {
            "ok": True,
            "task_id": task_id,
            "mode": mode,
            "status_before": status_before,
            "status_after": "QUEUED" if prepare_current_task else status_before,
            "validation": validation,
            "current_target_task": build_result.current_target_task,
        }

    def _execute_manual_run(
        self,
        *,
        task_id: str,
        task_dir: Path,
        script_path: Path,
        status_before: str,
        result_path: str | Path | None,
    ) -> dict[str, Any]:
        lock_created = False
        started_at = now_iso(self.clock)
        finished_at = started_at
        return_code = -1
        stdout = ""
        stderr = ""
        final_status = "FAILED"
        final_errors: list[str] = []
        stage_result_payload: dict[str, Any] | None = None
        run_started_at = self.clock()
        run_id = self._new_run_id("run-manual")
        generation_id = run_id

        try:
            pre_run_backups = self._backup_pre_run_results(task_id, task_dir, result_path)
            self._create_lock(task_id, run_id=run_id, generation_id=generation_id)
            lock_created = True
            if pre_run_backups:
                self._append_audit(task_id=task_id, action="pre_run_result_backed_up", status_before=status_before, status_after=status_before, success=True, errors=[])
            self._append_audit(task_id=task_id, action="lock_created", status_before=status_before, status_after=status_before, success=True, errors=[])
            self._set_task_status(task_id, "RUNNING", run_id=run_id, generation_id=generation_id)
            self._append_audit(task_id=task_id, action="status_changed_to_running", status_before=status_before, status_after="RUNNING", success=True, errors=[])
            self._append_audit(task_id=task_id, action="main_script_started", status_before="RUNNING", status_after="RUNNING", success=True, errors=[])

            completed = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return_code = int(completed.returncode)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            finished_at = now_iso(self.clock)
            self._append_audit(
                task_id=task_id,
                action="main_script_finished",
                status_before="RUNNING",
                status_after="RUNNING",
                success=return_code == 0,
                errors=[] if return_code == 0 else ["MAIN_SCRIPT_FAILED"],
            )

            collection = PricingResultCollector(project_root=PROJECT_ROOT, task_dir=task_dir).collect(
                result_path=result_path,
                run_started_at=run_started_at,
            )
            if collection.copied_path:
                self._stamp_run_identity(collection.copied_path, run_id=run_id, generation_id=generation_id)
            if collection.ok:
                self._append_audit(task_id=task_id, action="pricing_result_collected", status_before="RUNNING", status_after="RUNNING", success=True, errors=[])
            else:
                final_errors.extend(collection.errors)

            if return_code != 0:
                final_status = "FAILED"
                final_errors.append("MAIN_SCRIPT_FAILED")
            else:
                business_decision = pricing_result_business_status(collection.result) if collection.result else None
                if not collection.ok and RESULT_MISSING_REQUIRED_PRICING_FIELDS not in collection.errors:
                    final_status = "FAILED"
                elif business_decision == CONTINUE_NEXT_REFERENCE:
                    final_status = CONTINUE_NEXT_REFERENCE
                elif collection.result and pricing_result_business_status(collection.result) == "NEEDS_REVIEW":
                    final_status = "NEEDS_REVIEW"
                elif business_decision == RESULT_MISSING_REQUIRED_PRICING_FIELDS:
                    final_status = RESULT_MISSING_REQUIRED_PRICING_FIELDS
                    final_errors = _dedupe([
                        RESULT_MISSING_REQUIRED_PRICING_FIELDS,
                        *collection.errors,
                        *[f"MISSING_REQUIRED_FIELD:{field_name}" for field_name in collection.missing_required_fields],
                        *final_errors,
                    ])
                else:
                    final_status = "SUCCEEDED"
        except Exception as exc:  # pragma: no cover - defensive path keeps structured artifacts.
            stderr = (stderr + "\n" if stderr else "") + str(exc)
            finished_at = now_iso(self.clock)
            final_status = "FAILED"
            final_errors.append("MAIN_SCRIPT_FAILED")
        finally:
            (task_dir / "run_stdout.log").write_text(stdout, encoding="utf-8")
            (task_dir / "run_stderr.log").write_text(stderr, encoding="utf-8")
            if lock_created:
                final_errors.extend(self._release_lock(task_id))

        final_errors = _dedupe(final_errors)
        if final_status in {CONTINUE_NEXT_REFERENCE, RESULT_MISSING_REQUIRED_PRICING_FIELDS}:
            technical_status = "INCOMPLETE"
        else:
            technical_status = _technical_status_from_return_code(return_code)
        business_status = final_status
        recommended_next_action = self._recommended_next_action(final_status, final_errors[0] if final_errors else None)
        status_extra: dict[str, Any] = {}
        if final_status == RESULT_MISSING_REQUIRED_PRICING_FIELDS:
            status_extra = {
                "canonical_error_code": RESULT_MISSING_REQUIRED_PRICING_FIELDS,
                "human_reason": "定价结果缺少完整参考车或价格链，系统未输出自动定价结果。",
                "blocks_queue": False,
                "auto_pricing_allowed": False,
                "final_price_allowed": False,
                "missing_required_fields": pricing_success_missing_required_fields(collection.result if "collection" in locals() else None),
            }
        elif final_status == CONTINUE_NEXT_REFERENCE:
            status_extra = {
                "canonical_error_code": CONTINUE_NEXT_REFERENCE,
                "human_reason": "参考车边界尚未闭合，需要继续采集下一台参考车。",
                "blocks_queue": False,
                "auto_pricing_allowed": False,
                "final_price_allowed": False,
            }
        self._set_task_status(
            task_id,
            final_status,
            run_id=run_id,
            generation_id=generation_id,
            technical_status=technical_status,
            business_status=business_status,
            recommended_next_action=recommended_next_action,
            extra_fields=status_extra,
        )
        self._append_audit(
            task_id=task_id,
            action=_status_audit_action(final_status),
            status_before="RUNNING",
            status_after=final_status,
            success=final_status in {"SUCCEEDED", "NEEDS_REVIEW", CONTINUE_NEXT_REFERENCE},
            errors=final_errors,
        )

        run_meta = {
            "task_id": task_id,
            "run_id": run_id,
            "generation_id": generation_id,
            "main_script": str(script_path.relative_to(PROJECT_ROOT) if _is_relative_to(script_path, PROJECT_ROOT) else script_path),
            "run_started_at": now_iso(lambda: run_started_at),
            "started_at": started_at,
            "finished_at": finished_at,
            "return_code": return_code,
            "technical_status": technical_status,
            "business_status": business_status,
            "recommended_next_action": recommended_next_action,
            "status_before": status_before,
            "status_after": final_status,
        }
        if final_errors:
            run_meta["errors"] = final_errors
        if status_extra:
            run_meta.update(status_extra)
        self._write_json(task_dir / "run_meta.json", run_meta)

        result_payload = {
            "task_id": task_id,
            "ok": final_status in {"SUCCEEDED", "NEEDS_REVIEW", CONTINUE_NEXT_REFERENCE},
            "status": final_status,
            "run_id": run_id,
            "generation_id": generation_id,
            "return_code": return_code,
            "technical_status": technical_status,
            "business_status": business_status,
            "recommended_next_action": recommended_next_action,
            "errors": final_errors,
            "run_meta": run_meta,
        }
        if status_extra:
            result_payload.update(status_extra)

        if final_status != CONTINUE_NEXT_REFERENCE:
            try:
                preview_status = "FAILED" if final_status == RESULT_MISSING_REQUIRED_PRICING_FIELDS else final_status
                formatted = write_feishu_result_preview(task_dir=task_dir, task_id=task_id, status=preview_status, errors=final_errors)
                if formatted.warnings:
                    result_payload["formatter_warnings"] = formatted.warnings
                self._append_audit(task_id=task_id, action="result_format_generated", status_before=final_status, status_after=final_status, success=True, errors=[])
            except Exception as exc:  # pragma: no cover
                result_payload["ok"] = False
                result_payload["status"] = "FAILED"
                result_payload["errors"] = _dedupe(final_errors + ["RESULT_FORMAT_FAILED"])
                result_payload["formatter_exception"] = str(exc)

        self._write_json(task_dir / ("runner_result.json" if result_payload["ok"] else "runner_error.json"), result_payload)
        return result_payload

    def _execute_first_stage(
        self,
        *,
        task_id: str,
        task_dir: Path,
        script_path: Path,
        result_path: Path,
        status_before: str,
    ) -> dict[str, Any]:
        return self._execute_stage(
            task_id=task_id,
            task_dir=task_dir,
            script_path=script_path,
            result_path=result_path,
            status_before=status_before,
            running_status="RUNNING_FIRST_STAGE",
            stage_name="first_stage",
        )

    def _execute_second_stage(
        self,
        *,
        task_id: str,
        task_dir: Path,
        script_path: Path,
        result_path: Path,
        status_before: str,
    ) -> dict[str, Any]:
        return self._execute_stage(
            task_id=task_id,
            task_dir=task_dir,
            script_path=script_path,
            result_path=result_path,
            status_before=status_before,
            running_status="RUNNING_SECOND_STAGE",
            stage_name="second_stage",
        )

    def _execute_stage(
        self,
        *,
        task_id: str,
        task_dir: Path,
        script_path: Path,
        result_path: Path,
        status_before: str,
        running_status: str,
        stage_name: str,
    ) -> dict[str, Any]:
        lock_created = False
        started_at = now_iso(self.clock)
        finished_at = started_at
        return_code = -1
        stdout = ""
        stderr = ""
        final_status = "FAILED"
        final_errors: list[str] = []
        stage_result_payload: dict[str, Any] | None = None
        pre_run_backups: list[str] = []
        pre_run_isolated_results: list[str] = []
        run_started_at = self.clock()
        run_id = self._new_run_id(stage_name)
        generation_id = run_id
        try:
            backup_defaults = (
                [DEFAULT_FIRST_STAGE_RESULT, Path("output") / "result.json"]
                if stage_name == "first_stage"
                else [DEFAULT_SECOND_STAGE_RESULT, Path("output") / "result.json"]
            )
            pre_run_backups = self._backup_pre_run_results(task_id, task_dir, result_path, default_paths=backup_defaults)
            pre_run_isolated_results = self._isolate_pre_run_result_paths(
                task_id,
                task_dir,
                result_path,
                default_paths=backup_defaults,
                stage_name=stage_name,
            )
            self._create_lock(task_id, mode=stage_name, run_id=run_id, generation_id=generation_id)
            lock_created = True
            if pre_run_backups:
                self._append_audit(task_id=task_id, action=f"{stage_name}_pre_run_result_backed_up", status_before=status_before, status_after=status_before, success=True, errors=[])
            if pre_run_isolated_results:
                self._append_audit(task_id=task_id, action=f"{stage_name}_pre_run_result_isolated", status_before=status_before, status_after=status_before, success=True, errors=[])
            self._append_audit(task_id=task_id, action="lock_created", status_before=status_before, status_after=status_before, success=True, errors=[])
            self._set_task_status(task_id, running_status, run_id=run_id, generation_id=generation_id)
            self._append_audit(task_id=task_id, action=f"status_changed_to_{running_status.lower()}", status_before=status_before, status_after=running_status, success=True, errors=[])
            self._append_audit(task_id=task_id, action=f"{stage_name}_script_started", status_before=running_status, status_after=running_status, success=True, errors=[])

            completed = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return_code = int(completed.returncode)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            finished_at = now_iso(self.clock)
            self._append_audit(
                task_id=task_id,
                action=f"{stage_name}_script_finished",
                status_before=running_status,
                status_after=running_status,
                success=return_code == 0,
                errors=[] if return_code == 0 else ["MAIN_SCRIPT_FAILED"],
            )

            if stage_name == "first_stage":
                collection = self._collect_first_stage_result(task_dir, result_path, run_started_at)
                stage_result_payload = collection.get("result") if isinstance(collection.get("result"), dict) else None
                if collection.get("copied_path"):
                    self._stamp_run_identity(Path(str(collection["copied_path"])), run_id=run_id, generation_id=generation_id)
                final_errors.extend(collection["errors"])
                if return_code != 0:
                    final_errors.append("MAIN_SCRIPT_FAILED")
                final_status = "S10_READY" if return_code == 0 and not final_errors else "FAILED"
            else:
                collection = PricingResultCollector(project_root=PROJECT_ROOT, task_dir=task_dir).collect(
                    result_path=result_path,
                    run_started_at=run_started_at,
                )
                stage_result_payload = collection.result if isinstance(collection.result, dict) else None
                if collection.copied_path:
                    self._stamp_run_identity(collection.copied_path, run_id=run_id, generation_id=generation_id)
                if collection.ok:
                    self._append_audit(task_id=task_id, action="pricing_result_collected", status_before=running_status, status_after=running_status, success=True, errors=[])
                else:
                    final_errors.extend(collection.errors)
                if return_code != 0:
                    final_errors.append("MAIN_SCRIPT_FAILED")
                business_decision = pricing_result_business_status(collection.result) if collection.result else None
                if return_code != 0:
                    final_status = "FAILED"
                elif not collection.ok and RESULT_MISSING_REQUIRED_PRICING_FIELDS not in collection.errors:
                    final_status = "FAILED"
                elif business_decision == CONTINUE_NEXT_REFERENCE:
                    final_status = CONTINUE_NEXT_REFERENCE
                elif collection.result and pricing_result_business_status(collection.result) == "NEEDS_REVIEW":
                    final_status = "NEEDS_REVIEW"
                elif business_decision == RESULT_MISSING_REQUIRED_PRICING_FIELDS:
                    final_status = RESULT_MISSING_REQUIRED_PRICING_FIELDS
                    final_errors = _dedupe([
                        RESULT_MISSING_REQUIRED_PRICING_FIELDS,
                        *collection.errors,
                        *[f"MISSING_REQUIRED_FIELD:{field_name}" for field_name in collection.missing_required_fields],
                        *final_errors,
                    ])
                else:
                    final_status = "SUCCEEDED"
        except Exception as exc:  # pragma: no cover - defensive path keeps structured artifacts.
            stderr = (stderr + "\n" if stderr else "") + str(exc)
            finished_at = now_iso(self.clock)
            final_status = "FAILED"
            final_errors.append("MAIN_SCRIPT_FAILED")
        finally:
            (task_dir / f"{stage_name}_stdout.log").write_text(stdout, encoding="utf-8")
            (task_dir / f"{stage_name}_stderr.log").write_text(stderr, encoding="utf-8")
            if lock_created:
                final_errors.extend(self._release_lock(task_id))

        final_errors = _dedupe(final_errors)
        terminal_success = bool(stage_name == "second_stage" and is_automatic_pricing_terminal_success(stage_result_payload))
        if terminal_success:
            final_status = "SUCCEEDED"
            final_errors = []
        config_mismatch_reason = pricing_result_config_mismatch_reason(stage_result_payload)
        if config_mismatch_reason:
            final_errors = _dedupe([CONFIG_MISMATCH_HARD_STOP, config_mismatch_reason, *final_errors])
        target_info_failure = bool(config_mismatch_reason) or (stage_name == "first_stage" and is_target_info_error(
            errors=final_errors,
            missing_fields=list((stage_result_payload or {}).get("missing_fields") or []),
            result=stage_result_payload,
        ))
        target_info_feedback: dict[str, Any] | None = None
        if target_info_failure:
            final_status = TARGET_INFO_NEEDS_CORRECTION
            if config_mismatch_reason:
                final_errors = _dedupe([CONFIG_MISMATCH_HARD_STOP, config_mismatch_reason, *final_errors])
            else:
                final_errors = _dedupe(["TARGET_INFO_VALIDATION_FAILED", *final_errors])
            draft = self._read_json(task_dir / "target_task_draft.json") if (task_dir / "target_task_draft.json").exists() else {}
            status_payload = self._read_json(task_dir / "status.json") if (task_dir / "status.json").exists() else {}
            target_info_feedback = write_target_info_correction_feedback(
                task_dir=task_dir,
                task_id=task_id,
                status_payload=status_payload,
                draft=draft,
                errors=final_errors,
                missing_fields=list((stage_result_payload or {}).get("missing_fields") or []),
                result=stage_result_payload,
                dry_run=True,
                clock=self.clock,
            )
        incomplete_second_stage_status = final_status in {CONTINUE_NEXT_REFERENCE, RESULT_MISSING_REQUIRED_PRICING_FIELDS}
        if target_info_failure:
            technical_status = "VALIDATION_FAILED"
        elif terminal_success:
            technical_status = "SUCCEEDED"
        elif incomplete_second_stage_status:
            technical_status = "INCOMPLETE"
        else:
            technical_status = _technical_status_from_return_code(return_code)
        business_status = TARGET_INFO_NEEDS_CORRECTION if target_info_failure else final_status
        recommended_next_action = self._recommended_next_action(final_status, final_errors[0] if final_errors else None)
        if terminal_success:
            recommended_next_action = "ready-to-send"
        status_extra: dict[str, Any] = {}
        if config_mismatch_reason:
            status_extra = {
                "canonical_error_code": CONFIG_MISMATCH_HARD_STOP,
                "mismatch_reason": config_mismatch_reason,
                "human_reason": "车型配置无法确认一致，为避免错误定价已停止自动定价。",
                "blocks_queue": False,
                "auto_pricing_allowed": False,
                "final_price_allowed": False,
                "needs_resend_target_info": True,
            }
        elif final_status == RESULT_MISSING_REQUIRED_PRICING_FIELDS:
            status_extra = {
                "canonical_error_code": RESULT_MISSING_REQUIRED_PRICING_FIELDS,
                "human_reason": "定价结果缺少完整参考车或价格链，系统未输出自动定价结果。",
                "blocks_queue": False,
                "auto_pricing_allowed": False,
                "final_price_allowed": False,
                "missing_required_fields": pricing_success_missing_required_fields(stage_result_payload),
            }
        elif final_status == CONTINUE_NEXT_REFERENCE:
            status_extra = {
                "canonical_error_code": CONTINUE_NEXT_REFERENCE,
                "human_reason": "参考车边界尚未闭合，需要继续采集下一台参考车。",
                "blocks_queue": False,
                "auto_pricing_allowed": False,
                "final_price_allowed": False,
            }
        elif terminal_success:
            status_extra = {
                "terminal_success_result_exists": True,
                "terminal_success_result_protected": True,
                "preserved_terminal_success_status": _terminal_success_status(stage_result_payload),
                "terminal_success_source": "second_stage_result_payload",
                "terminal_success_recommended_next_action": "deliver-result",
                "blocks_queue": False,
                "auto_pricing_allowed": True,
                "final_price_allowed": True,
            }
        self._set_task_status(
            task_id,
            final_status,
            run_id=run_id,
            generation_id=generation_id,
            technical_status=technical_status,
            business_status=business_status,
            recommended_next_action=recommended_next_action,
            extra_fields=status_extra,
        )
        self._append_audit(
            task_id=task_id,
            action=_status_audit_action(final_status),
            status_before=running_status,
            status_after=final_status,
            success=final_status in {"S10_READY", "SUCCEEDED", "NEEDS_REVIEW", CONTINUE_NEXT_REFERENCE},
            errors=final_errors,
        )
        run_meta = {
            "task_id": task_id,
            "stage": stage_name,
            "run_id": run_id,
            "generation_id": generation_id,
            "main_script": str(script_path.relative_to(PROJECT_ROOT) if _is_relative_to(script_path, PROJECT_ROOT) else script_path),
            "result_path": str(result_path.relative_to(PROJECT_ROOT) if _is_relative_to(result_path, PROJECT_ROOT) else result_path),
            "run_started_at": now_iso(lambda: run_started_at),
            "started_at": started_at,
            "finished_at": finished_at,
            "return_code": return_code,
            "technical_status": technical_status,
            "business_status": business_status,
            "recommended_next_action": recommended_next_action,
            "status_before": status_before,
            "status_after": final_status,
        }
        if final_errors:
            run_meta["errors"] = final_errors
        if config_mismatch_reason:
            run_meta.update(status_extra)
        if target_info_feedback:
            run_meta["target_info_feedback"] = target_info_feedback
        if pre_run_backups:
            run_meta["pre_run_result_backups"] = pre_run_backups
        if pre_run_isolated_results:
            run_meta["pre_run_isolated_results"] = pre_run_isolated_results
        if stage_name == "first_stage" and isinstance(stage_result_payload, dict):
            run_meta.update(_first_stage_adb_evidence_from_payload(stage_result_payload))
        if terminal_success:
            run_meta.update(
                {
                    "terminal_success_result_exists": True,
                    "terminal_success_result_protected": True,
                    "preserved_terminal_success_status": _terminal_success_status(stage_result_payload),
                    "terminal_success_source": "stage_result_payload",
                }
            )
        self._write_json(task_dir / f"{stage_name}_run_meta.json", run_meta)
        if stage_name == "second_stage":
            self._write_json(task_dir / "run_meta.json", run_meta)
            if terminal_success and isinstance(stage_result_payload, dict):
                self.persist_terminal_success_result(
                    task_id,
                    stage_result_payload,
                    source="second_stage_result_payload",
                    run_id=run_id,
                    generation_id=generation_id,
                    write_runner_result=False,
                )

        result_payload = {
            "task_id": task_id,
            "ok": final_status in {"S10_READY", "SUCCEEDED", "NEEDS_REVIEW", CONTINUE_NEXT_REFERENCE},
            "status": final_status,
            "run_id": run_id,
            "generation_id": generation_id,
            "return_code": return_code,
            "technical_status": technical_status,
            "business_status": business_status,
            "recommended_next_action": recommended_next_action,
            "errors": final_errors,
            "run_meta": run_meta,
        }
        if status_extra and not config_mismatch_reason:
            result_payload.update(status_extra)
        if terminal_success:
            result_payload.update(
                {
                    "terminal_success_result_exists": True,
                    "terminal_success_result_protected": True,
                    "preserved_terminal_success_status": _terminal_success_status(stage_result_payload),
                    "terminal_success_source": "second_stage_result_payload",
                    "terminal_success_recommended_next_action": "deliver-result",
                }
            )
        if target_info_feedback:
            result_payload["target_info_feedback"] = target_info_feedback
        if final_status in {"FAILED", RESULT_MISSING_REQUIRED_PRICING_FIELDS}:
            try:
                write_feishu_result_preview(task_dir=task_dir, task_id=task_id, status="FAILED", errors=final_errors)
            except Exception as exc:  # pragma: no cover
                result_payload["formatter_exception"] = str(exc)
        elif stage_name == "second_stage" and final_status != CONTINUE_NEXT_REFERENCE:
            try:
                formatted = write_feishu_result_preview(task_dir=task_dir, task_id=task_id, status=final_status, errors=final_errors)
                if formatted.warnings:
                    result_payload["formatter_warnings"] = formatted.warnings
            except Exception as exc:  # pragma: no cover
                result_payload["formatter_exception"] = str(exc)
        self._write_json(task_dir / ("runner_result.json" if result_payload["ok"] else "runner_error.json"), result_payload)
        return result_payload

    def task_target_fingerprints(self, task_id: str, task_dir: Path | None = None) -> list[str]:
        return target_fingerprints_from_artifacts(
            self.project_root,
            task_dir or self.task_dir(task_id),
            task_id=task_id,
        )

    def _record_result_scope_rejection(
        self,
        task_id: str,
        task_dir: Path,
        *,
        trace: dict[str, Any],
        kind: str,
    ) -> None:
        rejection_dir = task_dir / "result_scope_rejections"
        rejection_dir.mkdir(parents=True, exist_ok=True)
        safe_kind = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in kind)
        path = rejection_dir / f"{self.clock().strftime('%Y%m%dT%H%M%S')}_{safe_kind}.json"
        payload = {
            "task_id": task_id,
            "kind": kind,
            "cross_task_result_rejected": trace.get("rejection_code") == CROSS_TASK_PRICING_RESULT_REJECTED,
            "target_fingerprint_mismatch_rejected": trace.get("rejection_code") == TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED,
            "created_at": now_iso(self.clock),
            **trace,
        }
        self._write_json(path, payload)

    def _find_existing_terminal_success_result(self, task_id: str, task_dir: Path) -> dict[str, Any] | None:
        current_fingerprints = self.task_target_fingerprints(task_id, task_dir)
        current_paths = (
            task_dir / "pricing_result.json",
            task_dir / "second_stage_result.json",
            task_dir / "runner_result.json",
            self.project_root / DEFAULT_SECOND_STAGE_RESULT,
            self.project_root / "output" / "result.json",
        )
        backup_dir = task_dir / "pre_run_result_backups"
        backup_paths = []
        if backup_dir.exists():
            backup_paths = sorted(
                (
                    *backup_dir.glob("*.output__result_s10_to_s16.json"),
                    *backup_dir.glob("*.output__result.json"),
                ),
                key=lambda candidate: candidate.stat().st_mtime,
                reverse=True,
            )
        for path in (*current_paths, *backup_paths):
            if not path.exists() or not path.is_file():
                continue
            try:
                payload = self._read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and is_automatic_pricing_terminal_success(payload):
                scope_check = validate_result_task_scope(
                    payload,
                    current_task_id=task_id,
                    current_target_fingerprints=current_fingerprints,
                    source_path=path,
                    require_task_id=True,
                    require_target_fingerprint=True,
                )
                if not scope_check.ok:
                    self._record_result_scope_rejection(
                        task_id,
                        task_dir,
                        trace=scope_check.as_trace(),
                        kind="terminal_success_recovery_rejected",
                    )
                    continue
                payload = dict(payload)
                payload.setdefault("terminal_success_source_path", str(path))
                if backup_dir in path.parents:
                    payload.setdefault("terminal_success_recovered_from_backup", True)
                    payload.setdefault("terminal_success_backup_path", str(path))
                    payload.setdefault("terminal_success_recovery_reason", "PRE_RUN_ISOLATED_SUCCESS_RESULT")
                return payload
        return None

    def persist_terminal_success_result(
        self,
        task_id: str,
        payload: dict[str, Any],
        *,
        source: str,
        ignored_failure: dict[str, Any] | None = None,
        run_id: str | None = None,
        generation_id: str | None = None,
        write_runner_result: bool = True,
    ) -> dict[str, Any]:
        task_dir = self.task_dir(task_id)
        if not isinstance(payload, dict) or not is_automatic_pricing_terminal_success(payload):
            return {
                "ok": False,
                "task_id": task_id,
                "status": "FAILED",
                "errors": ["TERMINAL_SUCCESS_RESULT_NOT_VALID"],
            }
        result_payload = copy.deepcopy(payload)
        current_fingerprints = self.task_target_fingerprints(task_id, task_dir)
        strict_recovery_source = bool(
            result_payload.get("terminal_success_recovered_from_backup")
            or "backup" in str(source)
            or "existing_terminal" in str(source)
            or result_payload.get("terminal_success_source_path")
        )
        if strict_recovery_source:
            scope_check = validate_result_task_scope(
                result_payload,
                current_task_id=task_id,
                current_target_fingerprints=current_fingerprints,
                source_path=result_payload.get("terminal_success_source_path") or result_payload.get("terminal_success_backup_path") or source,
                require_task_id=True,
                require_target_fingerprint=True,
            )
            if not scope_check.ok:
                self._record_result_scope_rejection(
                    task_id,
                    task_dir,
                    trace=scope_check.as_trace(),
                    kind="terminal_success_persist_rejected",
                )
                return {
                    "ok": False,
                    "task_id": task_id,
                    "status": "FAILED",
                    "errors": [scope_check.code or CROSS_TASK_PRICING_RESULT_REJECTED],
                    "canonical_error_code": scope_check.code or CROSS_TASK_PRICING_RESULT_REJECTED,
                    "task_scope_trace": scope_check.as_trace(),
                    "blocks_queue": False,
                }
        stamp_result_task_scope(result_payload, task_id=task_id, target_fingerprints=current_fingerprints)
        normalize_pricing_result_fields(result_payload, project_root=self.project_root)
        result_payload.setdefault("status", "FULL_CHAIN_PRICED_DONE")
        preserved_status = _terminal_success_status(result_payload)
        result_payload.update(
            {
                "terminal_success_result_exists": True,
                "terminal_success_result_protected": True,
                "preserved_terminal_success_status": preserved_status,
                "terminal_success_source": source,
                "business_status": "SUCCEEDED",
                "technical_status": "SUCCEEDED",
                "recommended_next_action": "deliver-result",
            }
        )
        if run_id:
            result_payload["run_id"] = run_id
        if generation_id:
            result_payload["generation_id"] = generation_id
        recovery_fields = {
            key: result_payload.get(key)
            for key in (
                "terminal_success_recovered_from_backup",
                "terminal_success_backup_path",
                "terminal_success_recovery_reason",
            )
            if result_payload.get(key) not in (None, "")
        }
        if ignored_failure:
            ignored_code = _first_nonempty(
                ignored_failure.get("status"),
                ignored_failure.get("error_code"),
                ignored_failure.get("canonical_error_code"),
                *(ignored_failure.get("errors") or []),
            )
            result_payload.update(
                {
                    "failure_after_terminal_success_ignored": True,
                    "latest_failure_after_terminal_success": ignored_failure,
                    "ignored_failure_code": ignored_code,
                }
            )

        for path in (
            task_dir / "pricing_result.json",
            self.project_root / DEFAULT_SECOND_STAGE_RESULT,
            self.project_root / "output" / "result.json",
        ):
            self._write_json(path, result_payload)

        status_extra = {
            "terminal_success_result_exists": True,
            "terminal_success_result_protected": True,
            "preserved_terminal_success_status": preserved_status,
            "terminal_success_source": source,
            "terminal_success_recommended_next_action": "deliver-result",
            "auto_pricing_allowed": True,
            "final_price_allowed": True,
            "blocks_queue": False,
        }
        status_extra.update(recovery_fields)
        if ignored_failure:
            status_extra.update(
                {
                    "failure_after_terminal_success_ignored": True,
                    "latest_failure_after_terminal_success": ignored_failure,
                    "ignored_failure_code": result_payload.get("ignored_failure_code"),
                }
            )
        self._set_task_status(
            task_id,
            "SUCCEEDED",
            run_id=run_id or str(result_payload.get("run_id") or ""),
            generation_id=generation_id or str(result_payload.get("generation_id") or ""),
            technical_status="SUCCEEDED",
            business_status="SUCCEEDED",
            recommended_next_action="ready-to-send",
            extra_fields=status_extra,
        )
        runner_payload = {
            "task_id": task_id,
            "ok": True,
            "status": "SUCCEEDED",
            "technical_status": "SUCCEEDED",
            "business_status": "SUCCEEDED",
            "recommended_next_action": "ready-to-send",
            "terminal_success_recommended_next_action": "deliver-result",
            "terminal_success_result_exists": True,
            "terminal_success_result_protected": True,
            "preserved_terminal_success_status": preserved_status,
            "terminal_success_source": source,
            "errors": [],
        }
        runner_payload.update(recovery_fields)
        if ignored_failure:
            runner_payload.update(
                {
                    "failure_after_terminal_success_ignored": True,
                    "latest_failure_after_terminal_success": ignored_failure,
                    "ignored_failure_code": result_payload.get("ignored_failure_code"),
                }
            )
        if write_runner_result:
            self._write_json(task_dir / "runner_result.json", runner_payload)
        return runner_payload

    def _phase2_status_error(self, status: str | None) -> str | None:
        if status == "CONFIRMED":
            return None
        if status == "INVALID":
            return "TASK_INVALID"
        if status == "CANCELLED":
            return "TASK_CANCELLED"
        return "TASK_NOT_CONFIRMED"

    def _manual_run_status_error(self, status: str | None) -> str | None:
        if status == "QUEUED":
            return None
        if status == "INVALID":
            return "TASK_INVALID"
        if status == "CANCELLED":
            return "TASK_CANCELLED"
        if status in {"SUCCEEDED", "FAILED", "NEEDS_REVIEW", "MANUAL_REVIEW_CONFIRMED"}:
            return "TASK_ALREADY_FINISHED"
        return "TASK_NOT_QUEUED"

    def _precheck_stage_run(
        self,
        task_id: str,
        *,
        mode: str,
        allow_app_run: bool,
        expected_status: str,
    ) -> dict[str, Any]:
        if not allow_app_run:
            return self._write_error(
                task_id,
                mode,
                None,
                ["APP_RUN_CONFIRMATION_REQUIRED"],
                [],
                extra={"message": "即将启动瓜子 APP 自动化主流程，必须显式增加 --allow-app-run 才允许执行。"},
            )
        task_dir = self.task_dir(task_id)
        if not task_dir.exists():
            return self._write_error(task_id, mode, None, ["TASK_NOT_FOUND"], [])
        status_path = task_dir / "status.json"
        if not status_path.exists():
            return self._write_error(task_id, mode, None, ["STATUS_JSON_MISSING"], [])
        status_before = self._read_json(status_path).get("status")
        target_serial_check = validate_target_serial_configured(project_root=self.project_root)
        if not target_serial_check.get("ok"):
            return self._write_error(
                task_id,
                mode,
                status_before,
                [str(target_serial_check.get("code") or TARGET_ADB_SERIAL_NOT_CONFIGURED)],
                [],
                extra={
                    "target_adb_device": target_serial_check.get("target"),
                    "strict_device_selection": True,
                    "no_default_device_fallback": True,
                },
            )
        allowed_statuses = {expected_status}
        if expected_status == "S10_READY":
            allowed_statuses.add(CONTINUE_NEXT_REFERENCE)
        if status_before not in allowed_statuses:
            return self._write_error(task_id, mode, status_before, [self._stage_status_error(status_before, expected_status)], [])
        if expected_status == "QUEUED":
            active_task_id = self._active_app_task_id(exclude_task_id=task_id)
            if active_task_id:
                return self._write_error(
                    task_id,
                    mode,
                    status_before,
                    ["ACTIVE_APP_TASK_EXISTS"],
                    [],
                    extra={"active_task_id": active_task_id},
                )
            queue_head = self._queue_head_task_id()
            if queue_head != task_id:
                return self._write_error(
                    task_id,
                    mode,
                    status_before,
                    ["TASK_NOT_QUEUE_HEAD"],
                    [],
                    extra={"queue_head_task_id": queue_head},
                )
        if not self.current_target_task_path.exists():
            if expected_status == "QUEUED":
                prepared = self.auto_prepare_queued_current_task(task_id, mode=f"{mode}-auto-prepare-current-task")
                if not prepared.get("ok"):
                    return prepared
            else:
                return self._write_error(task_id, mode, status_before, ["CURRENT_TARGET_TASK_MISSING"], [])
        current_task = self._read_json(self.current_target_task_path)
        if str(current_task.get("task_id") or "") != task_id:
            if expected_status == "QUEUED":
                prepared = self.auto_prepare_queued_current_task(task_id, mode=f"{mode}-auto-prepare-current-task")
                if not prepared.get("ok"):
                    return prepared
                current_task = self._read_json(self.current_target_task_path)
            if str(current_task.get("task_id") or "") != task_id:
                return self._write_error(
                    task_id,
                    mode,
                    status_before,
                    ["CURRENT_TARGET_TASK_TASK_ID_MISMATCH"],
                    [],
                    extra={"current_target_task_id": current_task.get("task_id")},
                )
        if self.runtime_lock_path.exists():
            return self._write_error(task_id, mode, status_before, ["PRICING_LOCK_EXISTS"], [])
        return {"ok": True, "task_dir": task_dir, "status_before": status_before}

    def _stage_status_error(self, status: str | None, expected_status: str) -> str:
        if status == "INVALID":
            return "TASK_INVALID"
        if status == "CANCELLED":
            return "TASK_CANCELLED"
        if status in {"SUCCEEDED", "FAILED", "NEEDS_REVIEW", "MANUAL_REVIEW_CONFIRMED"}:
            return "TASK_ALREADY_FINISHED"
        if expected_status == "S10_READY":
            return "TASK_NOT_S10_READY"
        return "TASK_NOT_QUEUED"

    def _resolve_stage_script(self, script: str | Path | None, default_script: Path) -> Path | None:
        candidate = Path(script) if script else default_script
        path = candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
        return path if path.exists() else None

    def _resolve_stage_result_path(self, result_path: str | Path | None, default_result_path: Path) -> Path:
        candidate = Path(result_path) if result_path else default_result_path
        return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate

    def _refresh_revalidation_pricing_chain(self, payload: dict[str, Any] | None) -> tuple[dict[str, Any] | None, bool]:
        if not isinstance(payload, dict):
            return payload, False
        listing_price_yuan = _coerce_int(resolve_pricing_result_field(payload, "target_guazi_listing_price_yuan"))
        if listing_price_yuan is None:
            return payload, False

        pricing_config = self._load_pricing_config_for_revalidation()
        service_fee_yuan = _service_fee_for_price(listing_price_yuan, pricing_config)
        net_payout_yuan = listing_price_yuan - service_fee_yuan
        cost_yuan = _coerce_int(resolve_pricing_result_field(payload, "cost_yuan"))
        if cost_yuan is None:
            cost_yuan = _cost_for_price(listing_price_yuan, pricing_config)
        profit_rate = _coerce_float(pricing_config.get("profit_rate"), default=0.08)
        min_profit_yuan = _min_profit_for_price(listing_price_yuan, pricing_config)
        profit_yuan = max(round(net_payout_yuan * profit_rate), min_profit_yuan)
        suggested_purchase_price_yuan = net_payout_yuan - cost_yuan - profit_yuan

        refreshed = copy.deepcopy(payload)
        pricing_section = refreshed.setdefault("pricing", {})
        values = {
            "target_guazi_listing_price_yuan": listing_price_yuan,
            "guazi_service_fee_yuan": service_fee_yuan,
            **_service_fee_contract_trace(listing_price_yuan, service_fee_yuan),
            "guazi_net_payout_yuan": net_payout_yuan,
            "guazi_return_price_yuan": net_payout_yuan,
            "cost_yuan": cost_yuan,
            "profit_rate": profit_rate,
            "profit_yuan": profit_yuan,
            "suggested_purchase_price_yuan": suggested_purchase_price_yuan,
            "suggested_acquisition_price_yuan": suggested_purchase_price_yuan,
        }
        for key, value in values.items():
            refreshed[key] = value
            pricing_section[key] = value
        return refreshed, refreshed != payload

    def _load_pricing_config_for_revalidation(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "profit_rate": 0.08,
            "guazi_service_fee_tiers": [dict(item) for item in DEFAULT_REVALIDATION_SERVICE_FEE_TIERS],
            "min_profit_yuan": 2500,
            "min_profit_tiers": [dict(item) for item in DEFAULT_REVALIDATION_MIN_PROFIT_TIERS],
            "cost_rules": [dict(item) for item in DEFAULT_REVALIDATION_COST_RULES],
            "cost_increment_per_50000_yuan": 400,
        }
        fields_path = PROJECT_ROOT / "config" / "fields.yaml"
        try:
            fields = json.loads(fields_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return config
        pricing_config = fields.get("pricing") if isinstance(fields, dict) else None
        if isinstance(pricing_config, dict):
            config.update(pricing_config)
        return config

    def _find_revalidation_pricing_result(
        self,
        task_dir: Path,
        status_payload: dict[str, Any],
        *,
        allow_task_local_manual_confirm_result: bool = False,
    ) -> dict[str, Any]:
        latest_run_id = str(status_payload.get("latest_run_id") or "")
        latest_generation_id = str(status_payload.get("generation_id") or "")
        task_id = str(status_payload.get("task_id") or task_dir.name)
        manual_confirm_context = allow_task_local_manual_confirm_result and _status_allows_task_local_manual_confirm(status_payload)
        task_candidates = [
            task_dir / "pricing_result.json",
            task_dir / "second_stage_result.json",
        ]
        task_candidates = sorted(
            [path for path in task_candidates if path.exists()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        candidates = task_candidates + [
            PROJECT_ROOT / DEFAULT_SECOND_STAGE_RESULT,
            PROJECT_ROOT / "output" / "result.json",
        ]
        warnings: list[str] = []
        identity_warnings: list[str] = []
        for path in candidates:
            if not path.exists():
                continue
            try:
                payload = self._read_json(path)
            except json.JSONDecodeError:
                return {
                    "ok": False,
                    "pricing_result": None,
                    "source_path": path,
                    "errors": ["RESULT_JSON_INVALID"],
                    "warnings": warnings,
                }
            is_task_local = _is_relative_to(path, task_dir)
            accepted_task_local_manual_confirm = False
            if is_task_local and manual_confirm_context:
                local_error = _task_local_manual_confirm_result_error(payload, task_id=task_id)
                if local_error:
                    identity_warnings.append(local_error)
                    continue
                accepted_task_local_manual_confirm = True
                identity_error = None
            else:
                identity_error = _pricing_result_identity_error(
                    payload,
                    latest_run_id=latest_run_id,
                    latest_generation_id=latest_generation_id,
                    require_identity=not is_task_local and bool(latest_run_id or latest_generation_id),
                )
            if identity_error:
                identity_warnings.append(identity_error)
                continue
            errors = validate_pricing_result_payload(payload)
            if accepted_task_local_manual_confirm and _manual_confirm_can_accept_validation_errors(errors):
                warnings.extend(f"MANUAL_CONFIRM_ACCEPTED_TASK_LOCAL_VALIDATION_WARNING:{error}" for error in errors)
                errors = []
            result_run_id = str(payload.get("run_id") or _nested_get(payload, ("run_meta", "run_id")) or "")
            result_generation_id = str(payload.get("generation_id") or _nested_get(payload, ("run_meta", "generation_id")) or "")
            return {
                "ok": not errors,
                "pricing_result": payload,
                "source_path": path,
                "errors": errors,
                "warnings": warnings,
                "pricing_result_source": "task_local_pricing_result" if is_task_local else "global_output_result",
                "pricing_result_run_id": result_run_id or None,
                "pricing_result_generation_id": result_generation_id or None,
                "task_local_pricing_result_accepted_for_manual_confirm": accepted_task_local_manual_confirm,
            }
        return {
            "ok": False,
            "pricing_result": None,
            "source_path": None,
            "errors": ["RESULT_FILE_NOT_FOUND"],
            "warnings": _dedupe(warnings + identity_warnings),
        }

    def _collect_first_stage_result(self, task_dir: Path, result_path: Path, run_started_at: datetime) -> dict[str, Any]:
        if not result_path.exists():
            return {"ok": False, "result": None, "errors": ["FIRST_STAGE_RESULT_NOT_FOUND"]}
        if result_file_is_stale(result_path, run_started_at):
            return {"ok": False, "result": None, "errors": ["STALE_RESULT_FILE"]}
        try:
            payload = self._read_json(result_path)
        except json.JSONDecodeError:
            return {"ok": False, "result": None, "errors": ["FIRST_STAGE_RESULT_JSON_INVALID"]}
        copied_path = task_dir / "first_stage_result.json"
        copied_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result_path, copied_path)
        errors = validate_first_stage_payload(payload)
        return {"ok": not errors, "result": payload, "errors": errors, "copied_path": str(copied_path)}

    def _stamp_run_identity(self, path: Path, *, run_id: str, generation_id: str) -> None:
        try:
            payload = self._read_json(path)
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, dict):
            payload.setdefault("run_id", run_id)
            payload.setdefault("generation_id", generation_id)
            self._write_json(path, payload)

    def _clear_current_target_task_if_matches(self, task_id: str) -> bool:
        if not self.current_target_task_path.exists():
            return False
        try:
            payload = self._read_json(self.current_target_task_path)
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict) or str(payload.get("task_id") or "") != task_id:
            return False
        self.current_target_task_path.unlink(missing_ok=True)
        return True

    def _load_first_stage_payload(self, task_dir: Path) -> dict[str, Any] | None:
        for path in (task_dir / "first_stage_result.json", PROJECT_ROOT / DEFAULT_FIRST_STAGE_RESULT):
            if not path.exists():
                continue
            try:
                return self._read_json(path)
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def _validate_existing_first_stage_result(self, task_dir: Path) -> list[str]:
        payload = self._load_first_stage_payload(task_dir)
        if payload is None:
            return ["FIRST_STAGE_RESULT_NOT_FOUND"]
        errors = validate_first_stage_payload(payload)
        return errors

    def _validate_task_first_stage_result(self, task_dir: Path) -> list[str]:
        path = task_dir / "first_stage_result.json"
        if not path.exists():
            return ["FIRST_STAGE_RESULT_NOT_FOUND"]
        try:
            payload = self._read_json(path)
        except json.JSONDecodeError:
            return ["FIRST_STAGE_RESULT_JSON_INVALID"]
        return validate_first_stage_payload(payload)

    def _recommended_next_action(self, status: str | None, last_error_code: str | None) -> str | None:
        if status == "QUEUED":
            return "run-first-stage"
        if status == TARGET_INFO_NEEDS_CORRECTION:
            return "ask-sender-to-resend-target-info"
        if status == "SYSTEM_BLOCKED":
            return "wait-admin-resolution"
        if status == "ADMIN_INTERVENTION_REQUIRED":
            return "notify-admin"
        if status == "ADMIN_INTERVENTION_RESOLVED":
            return "continue-queue"
        if status == "S10_READY":
            return "run-second-stage"
        if status == CONTINUE_NEXT_REFERENCE:
            return "run-second-stage"
        if status == RESULT_MISSING_REQUIRED_PRICING_FIELDS:
            return "investigate-pricing-result"
        if status == "FAILED":
            return "requeue-failed"
        if status == "SUCCEEDED" and last_error_code in FORCE_REQUEUE_ALLOWED_ERRORS:
            return "revalidate-result"
        if status in {"RUNNING_FIRST_STAGE", "RUNNING_SECOND_STAGE", "RUNNING"}:
            return "wait"
        if status == "NEEDS_REVIEW":
            return "manual-review"
        if status == "MANUAL_REVIEW_CONFIRMED":
            return "ready-to-send"
        return None

    def _send_result_status_ready(self, status: str, business_status: str, recommended_next_action: Any) -> bool:
        if status in {"MANUAL_REVIEW_CONFIRMED", "AUTO_PRICING_SUCCEEDED"}:
            return True
        if status == "SUCCEEDED" and business_status == "SUCCEEDED":
            return True
        if recommended_next_action == "ready-to-send":
            return True
        return False

    def _find_task_chat_id(self, task_dir: Path, status_payload: dict[str, Any]) -> str | None:
        for payload in [status_payload, *self._read_optional_task_payloads(task_dir)]:
            chat_id = _extract_chat_id(payload)
            if chat_id:
                return chat_id
        return None

    def _read_optional_task_payloads(self, task_dir: Path) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for name in (
            "target_task_draft.json",
            "current_target_task.snapshot.json",
            "raw_message.json",
            "raw_event.json",
        ):
            path = task_dir / name
            if not path.exists():
                continue
            try:
                payload = self._read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    def _send_result_error(
        self,
        task_id: str,
        mode: str,
        status_before: str | None,
        errors: list[str],
        *,
        dry_run: bool,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "ok": False,
            "dry_run": dry_run,
            "sent": False,
            "task_id": task_id,
            "status": status_before,
            "errors": errors,
        }
        if extra:
            payload.update(extra)
        self._append_audit(task_id=task_id, action=mode, status_before=status_before, status_after=status_before, success=False, errors=errors)
        return payload

    def _resolve_main_script(self, main_script: str | Path | None) -> Path | None:
        if main_script:
            candidates = [Path(main_script)]
        else:
            env_main_script = os.getenv("GUAZI_MAIN_SCRIPT")
            candidates = [Path(env_main_script)] if env_main_script else list(DEFAULT_MAIN_SCRIPT_CANDIDATES)
        for candidate in candidates:
            path = candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
            if path.exists():
                return path
        return None

    def _create_lock(
        self,
        task_id: str,
        *,
        mode: str = "run-manual",
        run_id: str | None = None,
        generation_id: str | None = None,
    ) -> None:
        self.runtime_lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(
            self.runtime_lock_path,
            {
                "task_id": task_id,
                "pid": os.getpid(),
                "created_at": now_iso(self.clock),
                "mode": mode,
                "run_id": run_id,
                "generation_id": generation_id,
            },
        )

    def _release_lock(self, task_id: str) -> list[str]:
        try:
            if self.runtime_lock_path.exists():
                self.runtime_lock_path.unlink()
            self._append_audit(task_id=task_id, action="lock_released", status_before=None, status_after=None, success=True, errors=[])
            return []
        except OSError as exc:
            self._append_audit(task_id=task_id, action="lock_release_failed", status_before=None, status_after=None, success=False, errors=["LOCK_RELEASE_FAILED"])
            self._write_json(
                self.task_dir(task_id) / "runner_error.json",
                {"task_id": task_id, "errors": ["LOCK_RELEASE_FAILED"], "exception": str(exc), "created_at": now_iso(self.clock)},
            )
            return ["LOCK_RELEASE_FAILED"]

    def _backup_pre_run_results(
        self,
        task_id: str,
        task_dir: Path,
        result_path: str | Path | None,
        *,
        default_paths: list[Path] | None = None,
    ) -> list[str]:
        timestamp = self.clock().strftime("%Y%m%dT%H%M%S")
        backup_dir = task_dir / "pre_run_result_backups"
        candidates: list[Path] = []
        if result_path is not None:
            explicit = Path(result_path)
            candidates.append(explicit if explicit.is_absolute() else self.project_root / explicit)
        for default_path in default_paths or [DEFAULT_SECOND_STAGE_RESULT, Path("output") / "result.json"]:
            candidates.append(default_path if default_path.is_absolute() else self.project_root / default_path)
        backups: list[str] = []
        continuation_backup_paths: list[str] = []
        continuation_backup_manifest: list[dict[str, Any]] = []
        current_fingerprints = self.task_target_fingerprints(task_id, task_dir)
        seen: set[Path] = set()
        for path in candidates:
            resolved = path.resolve()
            if resolved in seen or not path.exists():
                continue
            seen.add(resolved)
            backup_dir.mkdir(parents=True, exist_ok=True)
            safe_name = str(path.relative_to(self.project_root) if _is_relative_to(path, self.project_root) else path.name).replace("\\", "__").replace("/", "__").replace(":", "")
            backup_path = backup_dir / f"{timestamp}.{safe_name}"
            shutil.copy2(path, backup_path)
            backups.append(str(backup_path))
            try:
                backup_payload = self._read_json(backup_path)
            except (OSError, json.JSONDecodeError):
                backup_payload = {}
            if isinstance(backup_payload, dict) and self._is_same_task_continue_next_reference_payload(
                backup_payload,
                task_id=task_id,
                target_fingerprints=current_fingerprints,
            ):
                continuation_backup_paths.append(str(backup_path))
                continuation_backup_manifest.append(
                    {
                        "source_path": str(path),
                        "backup_path": str(backup_path),
                        "task_id": task_id,
                        "status": backup_payload.get("status"),
                        "final_status": backup_payload.get("final_status"),
                        "current_reference_index": backup_payload.get("current_reference_index"),
                        "next_reference_index": backup_payload.get("next_reference_index"),
                        "continuation_backup_for_same_task": True,
                    }
                )
        if backups:
            self._write_json(
                task_dir / "pre_run_result_backups" / "manifest.json",
                {
                    "task_id": task_id,
                    "backups": backups,
                    "continuation_backup_paths": continuation_backup_paths,
                    "continuation_backup_manifest": continuation_backup_manifest,
                    "created_at": now_iso(self.clock),
                },
            )
        return backups

    def _isolate_pre_run_result_paths(
        self,
        task_id: str,
        task_dir: Path,
        result_path: str | Path | None,
        *,
        default_paths: list[Path] | None = None,
        stage_name: str,
    ) -> list[str]:
        candidates: list[Path] = []
        if result_path is not None:
            explicit = Path(result_path)
            candidates.append(explicit if explicit.is_absolute() else self.project_root / explicit)
        for default_path in default_paths or [DEFAULT_SECOND_STAGE_RESULT, Path("output") / "result.json"]:
            candidates.append(default_path if default_path.is_absolute() else self.project_root / default_path)

        isolated: list[str] = []
        kept: list[dict[str, Any]] = []
        continuation_backup_paths: list[str] = []
        continuation_backup_manifest: list[dict[str, Any]] = []
        manifest_path = task_dir / "pre_run_result_backups" / "manifest.json"
        try:
            manifest = self._read_json(manifest_path) if manifest_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            manifest = {}
        if isinstance(manifest, dict):
            continuation_backup_paths = [str(item) for item in manifest.get("continuation_backup_paths") or [] if item]
            continuation_backup_manifest = [
                item for item in manifest.get("continuation_backup_manifest") or [] if isinstance(item, dict)
            ]
        seen: set[Path] = set()
        timestamp = self.clock().strftime("%Y%m%dT%H%M%S")
        stale_dir = task_dir / "stale_cross_task_results"
        for path in candidates:
            resolved = path.resolve()
            if resolved in seen or not path.exists():
                continue
            seen.add(resolved)
            if self._should_keep_pre_run_result_for_same_task_continuation(path, task_id=task_id, stage_name=stage_name):
                kept.append({"path": str(path), "reason": "same_task_continue_next_reference"})
                continue
            stale_dir.mkdir(parents=True, exist_ok=True)
            safe_name = str(path.relative_to(self.project_root) if _is_relative_to(path, self.project_root) else path.name).replace("\\", "__").replace("/", "__").replace(":", "")
            isolated_path = stale_dir / f"{timestamp}.{safe_name}"
            try:
                shutil.move(str(path), str(isolated_path))
            except OSError as exc:
                self._write_json(
                    task_dir / f"{stage_name}_pre_run_result_isolation_error.json",
                    {
                        "task_id": task_id,
                        "stage": stage_name,
                        "path": str(path),
                        "isolated_path": str(isolated_path),
                        "error": str(exc),
                        "created_at": now_iso(self.clock),
                    },
                )
                raise
            isolated.append(str(path))
        if isolated or kept:
            self._write_json(
                task_dir / f"{stage_name}_pre_run_result_isolation.json",
                {
                    "task_id": task_id,
                    "stage": stage_name,
                    "isolated_paths": isolated,
                    "stale_cross_task_result_isolated": bool(isolated),
                    "stale_cross_task_result_archive_dir": str(stale_dir),
                    "kept_paths": kept,
                    "continuation_backup_paths": continuation_backup_paths,
                    "continuation_backup_manifest": continuation_backup_manifest,
                    "continuation_backup_source_available": bool(continuation_backup_paths),
                    "created_at": now_iso(self.clock),
                },
            )
        return isolated

    def _is_same_task_continue_next_reference_payload(
        self,
        payload: dict[str, Any],
        *,
        task_id: str,
        target_fingerprints: list[str] | tuple[str, ...] | None = None,
    ) -> bool:
        if payload.get("status") != CONTINUE_NEXT_REFERENCE and payload.get("final_status") != CONTINUE_NEXT_REFERENCE:
            return False
        scope_check = validate_result_task_scope(
            payload,
            current_task_id=task_id,
            current_target_fingerprints=list(target_fingerprints or []),
            require_task_id=True,
            require_target_fingerprint=bool(target_fingerprints),
        )
        return scope_check.ok

    def _should_keep_pre_run_result_for_same_task_continuation(self, path: Path, *, task_id: str, stage_name: str) -> bool:
        if stage_name != "second_stage":
            return False
        try:
            payload = self._read_json(path)
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        return self._is_same_task_continue_next_reference_payload(
            payload,
            task_id=task_id,
            target_fingerprints=self.task_target_fingerprints(task_id),
        )

    def _new_run_id(self, mode: str) -> str:
        timestamp = self.clock().strftime("%Y%m%dT%H%M%S")
        safe_mode = "".join(ch if ch.isalnum() else "_" for ch in mode).strip("_")
        return f"{timestamp}_{safe_mode}_{uuid.uuid4().hex[:8]}"

    def _backup_existing_current_target_task(self, validation: dict[str, Any]) -> Path | None:
        if not self.current_target_task_path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = self.clock().strftime("%Y%m%dT%H%M%S")
        backup_path = self.backup_dir / f"current_target_task.{timestamp}.json"
        shutil.copy2(self.current_target_task_path, backup_path)
        validation.setdefault("warnings", []).append("CURRENT_TARGET_TASK_EXISTS_BACKED_UP")
        return backup_path

    def _write_target_info_correction_error(
        self,
        task_id: str,
        mode: str,
        status_before: str | None,
        errors: list[str],
        missing_fields: list[str],
        *,
        draft: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        run_id: str | None = None,
        generation_id: str | None = None,
    ) -> dict[str, Any]:
        task_dir = self.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        run_id = run_id or self._new_run_id(mode)
        generation_id = generation_id or run_id
        status_payload = self._read_json(task_dir / "status.json") if (task_dir / "status.json").exists() else {}
        if draft is None and (task_dir / "target_task_draft.json").exists():
            draft = self._read_json(task_dir / "target_task_draft.json")
        draft = draft or {}
        status_fields = target_info_status_fields(clock=self.clock)
        status_payload.update(status_fields)
        status_payload["status"] = TARGET_INFO_NEEDS_CORRECTION
        status_payload["latest_run_id"] = run_id
        status_payload["generation_id"] = generation_id
        self._write_json(task_dir / "status.json", status_payload)
        if draft:
            draft["status"] = TARGET_INFO_NEEDS_CORRECTION
            self._write_json(task_dir / "target_task_draft.json", draft)
        feedback = write_target_info_correction_feedback(
            task_dir=task_dir,
            task_id=task_id,
            status_payload=status_payload,
            draft=draft,
            errors=errors,
            missing_fields=missing_fields,
            result=result,
            dry_run=True,
            clock=self.clock,
        )
        final_errors = _dedupe(["TARGET_INFO_VALIDATION_FAILED", *list(feedback.get("classification", {}).get("codes") or []), *errors])
        payload = self._validation_payload(
            task_id=task_id,
            valid=False,
            mode=mode,
            status_before=status_before,
            missing_fields=missing_fields,
            warnings=[],
            errors=final_errors,
        )
        payload.update(
            {
                "run_id": run_id,
                "generation_id": generation_id,
                "status": TARGET_INFO_NEEDS_CORRECTION,
                "status_after": TARGET_INFO_NEEDS_CORRECTION,
                "business_status": TARGET_INFO_NEEDS_CORRECTION,
                "technical_status": "VALIDATION_FAILED",
                "recommended_next_action": "ask-sender-to-resend-target-info",
                "target_info_feedback": feedback,
            }
        )
        self._write_json(task_dir / "runner_validation.json", payload)
        self._write_json(task_dir / "runner_error.json", payload)
        self._append_audit(
            task_id=task_id,
            action=mode,
            status_before=status_before,
            status_after=TARGET_INFO_NEEDS_CORRECTION,
            success=False,
            errors=final_errors,
        )
        return {
            "ok": False,
            "task_id": task_id,
            "mode": mode,
            "status": TARGET_INFO_NEEDS_CORRECTION,
            "status_before": status_before,
            "status_after": TARGET_INFO_NEEDS_CORRECTION,
            "business_status": TARGET_INFO_NEEDS_CORRECTION,
            "technical_status": "VALIDATION_FAILED",
            "recommended_next_action": "ask-sender-to-resend-target-info",
            "errors": final_errors,
            "missing_fields": missing_fields,
            "run_id": run_id,
            "generation_id": generation_id,
            "target_info_feedback": feedback,
            "validation": payload,
        }

    def _write_error(
        self,
        task_id: str,
        mode: str,
        status_before: str | None,
        errors: list[str],
        missing_fields: list[str],
        *,
        extra: dict[str, Any] | None = None,
        run_id: str | None = None,
        generation_id: str | None = None,
    ) -> dict[str, Any]:
        task_dir = self.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        run_id = run_id or self._new_run_id(mode)
        generation_id = generation_id or run_id
        payload = self._validation_payload(
            task_id=task_id,
            valid=False,
            mode=mode,
            status_before=status_before,
            missing_fields=missing_fields,
            warnings=[],
            errors=errors,
        )
        payload["run_id"] = run_id
        payload["generation_id"] = generation_id
        if extra:
            payload.update(extra)
        if (task_dir / "status.json").exists():
            status_payload = self._read_json(task_dir / "status.json")
            status_payload["latest_run_id"] = run_id
            status_payload["generation_id"] = generation_id
            status_payload["updated_at"] = now_iso(self.clock)
            self._write_json(task_dir / "status.json", status_payload)
        self._write_json(task_dir / "runner_validation.json", payload)
        self._write_json(task_dir / "runner_error.json", payload)
        self._append_audit(
            task_id=task_id,
            action=mode,
            status_before=status_before,
            status_after=status_before,
            success=False,
            errors=errors,
        )
        return {
            "ok": False,
            "task_id": task_id,
            "mode": mode,
            "status_before": status_before,
            "errors": errors,
            "run_id": run_id,
            "generation_id": generation_id,
            "validation": payload,
        }

    def _task_runner_artifact_summary(self, task_dir: Path, latest_run_id: str | None = None) -> dict[str, list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        for name in ("runner_result.json", "runner_error.json", "run_meta.json"):
            path = task_dir / name
            if not path.exists():
                continue
            try:
                payload = self._read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            artifact_run_id = str(payload.get("run_id") or _nested_get(payload, ("run_meta", "run_id")) or "")
            if latest_run_id and artifact_run_id != latest_run_id:
                warnings.append("STALE_RUN_RESULT_IGNORED")
                continue
            value = payload.get("errors")
            if isinstance(value, list):
                errors.extend(str(item) for item in value)
            elif value:
                errors.append(str(value))
            if payload.get("error_code"):
                errors.append(str(payload["error_code"]))
        return {"errors": _dedupe(errors), "warnings": _dedupe(warnings)}

    def _task_runner_errors(self, task_dir: Path) -> list[str]:
        return self._task_runner_artifact_summary(task_dir).get("errors", [])

    def _validation_payload(
        self,
        *,
        task_id: str,
        valid: bool,
        mode: str,
        status_before: str | None,
        missing_fields: list[str],
        warnings: list[str],
        errors: list[str],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": task_id,
            "valid": valid,
            "mode": mode,
            "status_before": status_before,
            "required_files_exist": valid,
            "missing_fields": missing_fields,
            "warnings": warnings,
            "created_at": now_iso(self.clock),
        }
        if errors:
            payload["errors"] = errors
        return payload

    def _append_audit(
        self,
        *,
        task_id: str,
        action: str,
        status_before: str | None,
        status_after: str | None,
        success: bool,
        errors: list[str],
    ) -> None:
        payload = {
            "task_id": task_id,
            "action": action,
            "status_before": status_before,
            "status_after": status_after,
            "success": success,
            "errors": errors,
            "created_at": now_iso(self.clock),
        }
        audit_path = self.task_root / "audit_log.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _set_task_status(
        self,
        task_id: str,
        status: str,
        *,
        run_id: str | None = None,
        generation_id: str | None = None,
        technical_status: str | None = None,
        business_status: str | None = None,
        recommended_next_action: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status_path = self.task_dir(task_id) / "status.json"
        payload = self._read_json(status_path)
        payload["status"] = status
        payload["updated_at"] = now_iso(self.clock)
        if run_id:
            payload["latest_run_id"] = run_id
        if generation_id:
            payload["generation_id"] = generation_id
        if technical_status is not None:
            payload["technical_status"] = technical_status
        if business_status is not None:
            payload["business_status"] = business_status
        if recommended_next_action is not None:
            payload["recommended_next_action"] = recommended_next_action
        if extra_fields:
            payload.update(extra_fields)
        self._write_json(status_path, payload)
        return payload

    def _read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if value in (None, ""):
            continue
        return str(value)
    return ""


def _terminal_success_status(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("status", "final_status", "current_state", "s16_status"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    pricing = payload.get("pricing")
    if isinstance(pricing, dict) and pricing.get("status"):
        return str(pricing["status"])
    return "FULL_CHAIN_PRICED_DONE"


def _extract_chat_id(payload: dict[str, Any]) -> str | None:
    for key in ("raw_chat_id", "chat_id", "receive_id"):
        value = payload.get(key)
        if value:
            return str(value)
    for key in ("message", "event", "raw_event"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            value = _extract_chat_id(nested)
            if value:
                return value
    return None


def _mask_chat_id(chat_id: str) -> str:
    text = str(chat_id or "")
    if len(text) <= 8:
        return text[:2] + "***" if text else ""
    return f"{text[:6]}****{text[-4:]}"


def _technical_status_from_return_code(return_code: int) -> str:
    return "SUCCEEDED" if return_code == 0 else "FAILED"


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def _first_stage_adb_evidence_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    for source in (payload, context):
        for key in FIRST_STAGE_ADB_EVIDENCE_KEYS:
            if key in source and key not in evidence:
                evidence[key] = source.get(key)
        for key in ("target_device_gate_passed", "target_device_gate_status", "target_device_validation"):
            if key in source and key not in evidence:
                evidence[key] = source.get(key)
    return evidence


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, *, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _service_fee_for_price(price_yuan: int, pricing_config: dict[str, Any]) -> int:
    tiers = pricing_config.get("guazi_service_fee_tiers") or DEFAULT_REVALIDATION_SERVICE_FEE_TIERS
    parsed = sorted(
        (
            (
                int(row.get("min_price_yuan", 0)),
                int(row["service_fee_yuan"]),
            )
            for row in tiers
            if isinstance(row, dict) and "service_fee_yuan" in row
        ),
        reverse=True,
    )
    for min_price_yuan, service_fee_yuan in parsed:
        if price_yuan >= min_price_yuan:
            return service_fee_yuan
    return DEFAULT_REVALIDATION_SERVICE_FEE_TIERS[-1]["service_fee_yuan"]


def _service_fee_contract_trace(price_yuan: int, service_fee_yuan: int) -> dict[str, Any]:
    expected_fee = _service_fee_for_price(
        price_yuan,
        {"guazi_service_fee_tiers": [dict(item) for item in DEFAULT_REVALIDATION_SERVICE_FEE_TIERS]},
    )
    matched_tier = next(
        (dict(item) for item in DEFAULT_REVALIDATION_SERVICE_FEE_TIERS if price_yuan >= item["min_price_yuan"]),
        dict(DEFAULT_REVALIDATION_SERVICE_FEE_TIERS[-1]),
    )
    return {
        "service_fee_rule_source_file": "desktop_rule_compiled.json::pricing_rule",
        "service_fee_rule_version": "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "service_fee_tiers": [dict(item) for item in DEFAULT_REVALIDATION_SERVICE_FEE_TIERS],
        "service_fee_tier_matched": matched_tier,
        "service_fee_expected_by_contract": expected_fee,
        "service_fee_actual": service_fee_yuan,
        "service_fee_contract_match": service_fee_yuan == expected_fee,
    }


def _min_profit_for_price(price_yuan: int, pricing_config: dict[str, Any]) -> int:
    tiers = pricing_config.get("min_profit_tiers") or DEFAULT_REVALIDATION_MIN_PROFIT_TIERS
    parsed = sorted(
        (
            (
                int(row.get("min_price_yuan", 0)),
                int(row["min_profit_yuan"]),
            )
            for row in tiers
            if isinstance(row, dict) and "min_profit_yuan" in row
        ),
        reverse=True,
    )
    for min_price_yuan, min_profit_yuan in parsed:
        if price_yuan >= min_price_yuan:
            return min_profit_yuan
    return int(pricing_config.get("min_profit_yuan", 2500))


def _cost_for_price(price_yuan: int, pricing_config: dict[str, Any]) -> int:
    rules = pricing_config.get("cost_rules") or DEFAULT_REVALIDATION_COST_RULES
    parsed = sorted(
        (
            (
                int(row["lte_price_yuan"]),
                int(row["cost_yuan"]),
            )
            for row in rules
            if isinstance(row, dict) and "lte_price_yuan" in row and "cost_yuan" in row
        ),
        key=lambda item: item[0],
    )
    for lte_price_yuan, cost_yuan in parsed:
        if price_yuan <= lte_price_yuan:
            return cost_yuan
    highest_lte, highest_cost = parsed[-1] if parsed else (100000, 1000)
    increment = int(pricing_config.get("cost_increment_per_50000_yuan", 400))
    extra_steps = max(0, (price_yuan - highest_lte + 49999) // 50000)
    return highest_cost + extra_steps * increment


def _pricing_result_identity_error(
    payload: dict[str, Any],
    *,
    latest_run_id: str,
    latest_generation_id: str,
    require_identity: bool = False,
) -> str | None:
    result_run_id = str(payload.get("run_id") or _nested_get(payload, ("run_meta", "run_id")) or "")
    result_generation_id = str(payload.get("generation_id") or _nested_get(payload, ("run_meta", "generation_id")) or "")
    if require_identity and not (result_run_id or result_generation_id):
        return "RESULT_GENERATION_ID_MISSING"
    if latest_run_id and result_run_id and result_run_id != latest_run_id:
        return "STALE_RESULT_RUN_ID_IGNORED"
    if latest_generation_id and result_generation_id and result_generation_id != latest_generation_id:
        return "STALE_RESULT_GENERATION_ID_IGNORED"
    return None


def _status_allows_task_local_manual_confirm(status_payload: dict[str, Any]) -> bool:
    status_value = str(status_payload.get("status") or "")
    business_status = str(status_payload.get("business_status") or "")
    return bool(
        status_value in MANUAL_PRICE_WAITING_STATUSES
        or business_status == "NEEDS_REVIEW"
        or status_payload.get("manual_review_required") is True
        or status_payload.get("waiting_manual_price") is True
    )


def _task_local_manual_confirm_result_error(payload: dict[str, Any], *, task_id: str) -> str | None:
    payload_task_id = _pricing_result_task_id(payload)
    if payload_task_id and payload_task_id != task_id:
        return "TASK_LOCAL_PRICING_RESULT_TASK_ID_MISMATCH"
    statuses = {
        str(payload.get("status") or ""),
        str(payload.get("final_status") or ""),
        str(payload.get("current_state") or ""),
        str(_nested_get(payload, ("pricing", "status")) or ""),
    }
    if statuses & TASK_LOCAL_MANUAL_REVIEW_RESULT_STATUSES:
        return None
    if resolve_pricing_result_field(payload, "manual_review_required") is True:
        return None
    if pricing_result_manual_review_reasons(payload):
        return None
    return "TASK_LOCAL_PRICING_RESULT_NOT_MANUAL_REVIEW"


def _manual_confirm_can_accept_validation_errors(errors: list[str]) -> bool:
    if not errors:
        return False
    for error in errors:
        if error.startswith("MISSING_REQUIRED_FIELD:"):
            continue
        if error in TASK_LOCAL_MANUAL_REVIEW_RESULT_STATUSES:
            continue
        return False
    return True


def _pricing_result_task_id(payload: dict[str, Any]) -> str:
    candidates = (
        payload.get("task_id"),
        payload.get("feishu_task_id"),
        _nested_get(payload, ("task", "task_id")),
        _nested_get(payload, ("run_meta", "task_id")),
        _nested_get(payload, ("s17_payload", "task_id")),
    )
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _nested_get(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _status_audit_action(status: str) -> str:
    return {
        "S10_READY": "status_changed_to_s10_ready",
        "SUCCEEDED": "status_changed_to_succeeded",
        "NEEDS_REVIEW": "status_changed_to_needs_review",
        "MANUAL_REVIEW_CONFIRMED": "status_changed_to_manual_review_confirmed",
        "FAILED": "status_changed_to_failed",
    }.get(status, "status_changed")


def diagnose_main_entry(project_root: str | Path = PROJECT_ROOT) -> dict[str, Any]:
    root = Path(project_root)
    candidate_files = []
    for path in _iter_python_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        matched = [keyword for keyword in DIAGNOSE_KEYWORDS if keyword in text]
        if not matched:
            continue
        if not _looks_like_entry_file(path, text):
            continue
        candidate_files.append(_diagnose_python_file(root, path, text, matched))
    config_issues = _diagnose_config_paths(root)
    payload = {
        "generated_at": now_iso(),
        "project_root": str(root.resolve()),
        "candidate_files": sorted(candidate_files, key=lambda item: (item["likely_role"], item["file_path"])),
        "config_issues": config_issues,
        "recommendation": _diagnosis_recommendation(candidate_files, config_issues),
    }
    output_path = root / "output" / "main_entry_diagnosis.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload["output_path"] = str(output_path)
    return payload


def _iter_python_files(root: Path) -> list[Path]:
    excluded = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".test_deps", ".adbhome"}
    files: list[Path] = []
    for path in root.rglob("*.py"):
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            relative_parts = path.parts
        if any(part in excluded for part in relative_parts):
            continue
        if any(part in {"artifacts", "output", "logs", "tests"} for part in relative_parts):
            continue
        files.append(path)
    return files


def _diagnose_python_file(root: Path, path: Path, text: str, matched: list[str]) -> dict[str, Any]:
    has_if_main = 'if __name__ == "__main__"' in text or "if __name__ == '__main__'" in text
    has_main_function = "def main" in text
    mentions_adb = "adb" in text
    mentions_uiautomator = "uiautomator" in text.lower()
    mentions_result_s01_to_s10 = "result_s01_to_s10" in text
    mentions_result_s10_to_s16 = "result_s10_to_s16" in text
    likely_role = _infer_likely_role(text)
    return {
        "file_path": str(path.relative_to(root) if _is_relative_to(path, root) else path),
        "matched_keywords": matched,
        "has_if_main": has_if_main,
        "has_main_function": has_main_function,
        "mentions_adb": mentions_adb,
        "mentions_uiautomator": mentions_uiautomator,
        "mentions_result_s01_to_s10": mentions_result_s01_to_s10,
        "mentions_result_s10_to_s16": mentions_result_s10_to_s16,
        "likely_role": likely_role,
        "recommendation": _file_recommendation(likely_role),
    }


def _looks_like_entry_file(path: Path, text: str) -> bool:
    name = path.name.lower()
    return (
        name.startswith("runtime_")
        or "mainline" in name
        or name in {"main.py", "全程跑通.py"}
    )


def _infer_likely_role(text: str) -> str:
    lower = text.lower()
    mentions_app = any(keyword in text for keyword in ("com.guazi", "瓜子", "app_start", "app_current")) or "uiautomator" in lower or "adb" in lower
    mentions_s01 = "S01" in text or "S01_TO_S10" in text or "result_s01_to_s10" in text
    mentions_s10_to_s16 = "S10_TO_S16" in text or "result_s10_to_s16" in text
    if "PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE" in text:
        return "second_stage_s10_to_s16"
    if mentions_app and mentions_s01 and mentions_s10_to_s16:
        return "full_app_mainline"
    if mentions_app and mentions_s01:
        return "first_stage_s01_to_s10"
    if mentions_s10_to_s16 or "S10_READY" in text:
        return "second_stage_s10_to_s16"
    if "contract" in lower:
        return "contract_only"
    return "unknown"


def _file_recommendation(role: str) -> str:
    if role == "full_app_mainline":
        return "可人工复核是否为 runner 的完整 APP 全链入口。"
    if role == "first_stage_s01_to_s10":
        return "可能只负责到达 S10_READY，需要再衔接 S10-S16 二段。"
    if role == "second_stage_s10_to_s16":
        return "可能依赖已经到达 S10_READY，不能直接当作完整 APP 全链入口。"
    if role == "contract_only":
        return "更像合约或离线执行入口，不建议直接作为 APP 主流程。"
    return "角色不明确，需要人工阅读确认。"


def _diagnose_config_paths(root: Path) -> list[dict[str, Any]]:
    config_path = root / "config" / "system.yaml"
    if not config_path.exists():
        return []
    text = config_path.read_text(encoding="utf-8", errors="ignore")
    issues: list[dict[str, Any]] = []
    absolute_paths = _extract_absolute_paths(text)
    for path_text in absolute_paths:
        path = Path(path_text)
        if path.is_absolute() and not _is_relative_to(path, root):
            issues.append(
                {
                    "code": "CONFIG_PATH_OUTSIDE_PROJECT",
                    "path": path_text,
                    "message": "config/system.yaml contains an absolute path outside the current project; please confirm manually.",
                }
            )
    for expected in ("output/result_s01_to_s10.json", "output/result_s10_to_s16.json"):
        if expected in text:
            continue
    return issues


def _extract_absolute_paths(text: str) -> list[str]:
    import re

    windows = re.findall(r"[A-Za-z]:[\\/][^\"'\s,}]+", text)
    unix = re.findall(r"(?<![\w])/(?:[^\"'\s,}]+/)*[^\"'\s,}]+", text)
    return windows + unix


def _diagnosis_recommendation(candidate_files: list[dict[str, Any]], config_issues: list[dict[str, Any]]) -> str:
    full = [item["file_path"] for item in candidate_files if item.get("likely_role") == "full_app_mainline"]
    first = [item["file_path"] for item in candidate_files if item.get("likely_role") == "first_stage_s01_to_s10"]
    second = [item["file_path"] for item in candidate_files if item.get("likely_role") == "second_stage_s10_to_s16"]
    parts: list[str] = []
    if full:
        parts.append(f"优先人工复核完整 APP 全链入口候选：{', '.join(full[:5])}")
    elif first:
        parts.append(f"先人工复核 S01-S10 入口候选：{', '.join(first[:5])}")
    if second:
        parts.append(f"S10-S16 二段候选不能单独视为全链入口：{', '.join(second[:5])}")
    if config_issues:
        parts.append("发现 config/system.yaml 路径疑似指向项目外，请先人工确认。")
    return "；".join(parts) if parts else "未识别到明确入口，请人工阅读候选文件。"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare or run confirmed Feishu tasks for local pricing.")
    parser.add_argument("--task-id", default=None, help="Feishu task id, for example FS20260609_0001.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Generate preview and validation files only.")
    mode.add_argument("--prepare-current-task", action="store_true", help="Write data/current_target_task.json and queue the task.")
    mode.add_argument("--status", action="store_true", help="Show local task preparation status.")
    mode.add_argument("--run-manual", action="store_true", help="Manually run the main APP automation after explicit confirmation.")
    mode.add_argument("--run-first-stage", action="store_true", help="Run controlled S01-S10 first-stage APP automation.")
    mode.add_argument("--run-second-stage", action="store_true", help="Run controlled S10-S16 second-stage APP automation.")
    mode.add_argument("--diagnose-main-entry", action="store_true", help="Read-only scan for possible APP main entry scripts.")
    mode.add_argument("--requeue-failed", action="store_true", help="Move a failed task back to QUEUED without starting the APP.")
    mode.add_argument("--requeue-second-stage", action="store_true", help="Move a failed task with trusted S10_READY evidence back to S10_READY without starting the APP.")
    mode.add_argument("--revalidate-result", action="store_true", help="Revalidate existing task pricing result without starting the APP.")
    mode.add_argument("--manual-confirm-price", type=int, default=None, metavar="YUAN", help="Locally confirm a manual review purchase price without starting the APP or sending Feishu.")
    mode.add_argument("--send-result", action="store_true", help="Send or dry-run the generated Feishu final result preview for a task.")
    parser.add_argument("--allow-app-run", action="store_true", help="Required together with --run-manual.")
    parser.add_argument("--live", action="store_true", help="Required together with --send-result to actually send to Feishu.")
    parser.add_argument("--force-requeue-invalid-success", action="store_true", help="Allow SUCCEEDED -> QUEUED only for known invalid-success error codes.")
    parser.add_argument("--manual-review-note", default="", help="Required together with --manual-confirm-price.")
    parser.add_argument("--manual-confirm-by", default="local_user", help="Local operator label for --manual-confirm-price.")
    parser.add_argument("--main-script", default=None, help="Optional path to 全程跑通.py.")
    parser.add_argument("--result-path", default=None, help="Optional path to pricing_result.json.")
    parser.add_argument("--first-stage-script", default=None, help="Optional path to runtime_s01_to_s10_mainline.py.")
    parser.add_argument("--first-stage-result-path", default=None, help="Optional path to result_s01_to_s10.json.")
    parser.add_argument("--second-stage-script", default=None, help="Optional path to runtime_s10_to_s16_mainline.py.")
    parser.add_argument("--second-stage-result-path", default=None, help="Optional path to result_s10_to_s16.json.")
    parser.add_argument("--task-root", default=str(DEFAULT_TASK_ROOT), help="Feishu task root directory.")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="Data directory containing current_target_task.json.")
    parser.add_argument("--runtime-lock", default=str(DEFAULT_RUNTIME_LOCK), help="Runtime pricing lock path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runner = PricingRunner(
        task_root=args.task_root,
        data_dir=args.data_dir,
        runtime_lock_path=args.runtime_lock,
    )
    if args.diagnose_main_entry:
        result = runner.diagnose_main_entry()
    elif not args.task_id:
        result = {"ok": False, "errors": ["TASK_ID_REQUIRED"]}
    elif args.status:
        result = runner.status(args.task_id)
    elif args.dry_run:
        result = runner.dry_run(args.task_id)
    elif args.prepare_current_task:
        result = runner.prepare_current_task(args.task_id)
    elif args.run_manual:
        result = runner.run_manual(
            args.task_id,
            allow_app_run=args.allow_app_run,
            main_script=args.main_script,
            result_path=args.result_path,
        )
    elif args.run_first_stage:
        result = runner.run_first_stage(
            args.task_id,
            allow_app_run=args.allow_app_run,
            first_stage_script=args.first_stage_script,
            first_stage_result_path=args.first_stage_result_path,
        )
    elif args.run_second_stage:
        result = runner.run_second_stage(
            args.task_id,
            allow_app_run=args.allow_app_run,
            second_stage_script=args.second_stage_script,
            second_stage_result_path=args.second_stage_result_path,
        )
    elif args.requeue_failed:
        result = runner.requeue_failed(
            args.task_id,
            force_requeue_invalid_success=args.force_requeue_invalid_success,
        )
    elif args.requeue_second_stage:
        result = runner.requeue_second_stage(args.task_id)
    elif args.revalidate_result:
        result = runner.revalidate_result(args.task_id)
    elif args.manual_confirm_price is not None:
        result = runner.manual_confirm_price(
            args.task_id,
            manual_confirm_price=args.manual_confirm_price,
            manual_review_note=args.manual_review_note,
            manual_confirm_by=args.manual_confirm_by,
        )
    elif args.send_result:
        result = runner.send_result(args.task_id, live=args.live)
    else:
        result = {"ok": False, "errors": ["INVALID_MODE"]}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
