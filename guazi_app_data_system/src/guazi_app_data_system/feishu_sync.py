"""Feishu task sync framework.

No real Feishu API credentials are read here yet. The official reader is an
interface stub; mock readers are explicit simulation-only helpers.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .task_normalizer import TargetCarTask, TaskContractError, normalize_target_task


CURRENT_TARGET_TASK_RELATIVE_PATH = Path("input") / "current_target_task.json"
CURRENT_TASK_REQUIRED_FIELDS = ("brand", "series", "model_year", "trim", "color", "registration_date")


FEISHU_FIELD_MAPPING: dict[str, dict[str, Any]] = {
    "task_id": {
        "display_name": "任务编号",
        "type": "text",
        "required": True,
        "forbid_manual_input": True,
    },
    "brand": {"display_name": "品牌", "type": "text", "required": True},
    "series": {"display_name": "车系", "type": "text", "required": True},
    "model_year": {"display_name": "年款", "type": "text", "required": True},
    "trim": {"display_name": "配置", "type": "text", "required": True},
    "color": {"display_name": "颜色", "type": "text", "required": True},
    "registration_date": {"display_name": "上牌年月", "type": "text", "required": True},
    "mileage_10k_km": {"display_name": "表显里程", "type": "number", "required": True},
    "transfer_count": {"display_name": "过户次数", "type": "number", "required": True},
    "condition_text": {"display_name": "车况描述", "type": "text", "required": True},
    "accident_count": {"display_name": "出险次数", "type": "number", "required": False},
    "max_accident_amount": {"display_name": "最大出险金额", "type": "number", "required": False},
}


@dataclass(frozen=True)
class TaskReadResult:
    task: TargetCarTask
    source: str
    simulation_only: bool
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "simulation_only": self.simulation_only,
            "warnings": self.warnings,
            "task": self.task.to_dict(),
        }


class FeishuTaskReader:
    """Future real Feishu task reader.

    This class intentionally does not accept or hard-code tokens. A future
    implementation must receive credentials from approved runtime config.
    """

    simulation_only = False

    def read_target_task(self, task_id: str) -> TaskReadResult:
        raise NotImplementedError("Real Feishu API task reading is not connected yet.")


class FeishuExportTaskReader:
    """Reader for CSV/JSON files exported from the Feishu task table.

    Export files are temporary real-task inputs before API access exists. They
    are not mock data and may drive real device operation only after field
    validation passes.
    """

    simulation_only = False
    source = "feishu_export"

    def read_json(self, path: str | Path) -> TaskReadResult:
        export_path = Path(path)
        payload = json.loads(export_path.read_text(encoding="utf-8"))
        return self._normalize_payload(payload, export_path)

    def read_csv(self, path: str | Path) -> TaskReadResult:
        export_path = Path(path)
        with export_path.open("r", encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        if len(rows) != 1:
            raise TaskContractError("Feishu export CSV must contain exactly one target task row.")
        payload = dict(rows[0])
        payload["simulation_only"] = _truthy(payload.get("simulation_only", False))
        return self._normalize_payload(payload, export_path)

    def _normalize_payload(self, payload: dict[str, Any], path: Path) -> TaskReadResult:
        if payload.get("source") != "feishu_export":
            raise TaskContractError("Feishu export reader only accepts payloads with source='feishu_export'.")
        if _truthy(payload.get("simulation_only", False)):
            raise TaskContractError("Feishu export tasks must not be marked simulation_only=true.")
        imported_at = datetime.now(timezone.utc).isoformat()
        task = normalize_target_task(
            payload,
            source="feishu_export",
            simulation_only=False,
            source_import_path=str(path.resolve()),
            source_imported_at=imported_at,
        )
        warnings: list[str] = []
        if task.manual_review_required:
            warnings.extend(task.manual_review_reasons)
        return TaskReadResult(task=task, source="feishu_export", simulation_only=False, warnings=warnings)


class MockTaskReader:
    """Explicit local reader for simulation and offline regression only."""

    simulation_only = True

    def read_json(self, path: str | Path) -> TaskReadResult:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return self._normalize_payload(payload)

    def read_csv(self, path: str | Path) -> TaskReadResult:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        if len(rows) != 1:
            raise TaskContractError("Mock CSV must contain exactly one target task row.")
        payload = dict(rows[0])
        payload.setdefault("source", "mock")
        payload["simulation_only"] = _truthy(payload.get("simulation_only", True))
        return self._normalize_payload(payload)

    def _normalize_payload(self, payload: dict[str, Any]) -> TaskReadResult:
        if payload.get("source") != "mock":
            raise TaskContractError("Mock reader only accepts payloads with source='mock'.")
        if payload.get("simulation_only") is not True:
            raise TaskContractError("Mock reader requires simulation_only=true.")
        task = normalize_target_task(payload, source="mock", simulation_only=True)
        warnings = list(task.simulation_warnings)
        return TaskReadResult(task=task, source="mock", simulation_only=True, warnings=warnings)


def official_reader_requires_feishu() -> bool:
    return True


def mock_reader_is_never_default() -> bool:
    return True


def current_target_task_path(root: str | Path | None = None) -> Path:
    if root is None:
        from .config_loader import project_root

        root_path = project_root()
    else:
        root_path = Path(root)
    return root_path / CURRENT_TARGET_TASK_RELATIVE_PATH


def validate_current_target_task(path: str | Path | None = None) -> dict[str, Any]:
    """Validate input/current_target_task.json without falling back to fixtures."""
    task_path = Path(path) if path else current_target_task_path()
    base = {
        "file_path": str(task_path),
        "exists": task_path.exists(),
        "sample_fallback_used": False,
        "phone_operation": "none",
    }
    if not task_path.exists():
        return {
            **base,
            "status": "CURRENT_TASK_FILE_NOT_FOUND",
            "message": "等待真实任务导入",
            "allow_real_device_operation": False,
            "next_step_allowed": False,
        }

    try:
        payload = json.loads(task_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _blocked_current_task(base, "REQUIRED_FIELD_MISSING", f"Invalid JSON: {exc}", {"missing_fields": []})

    if "reference_index" in payload:
        return _blocked_current_task(base, "REFERENCE_INDEX_FORBIDDEN", "reference_index must not appear in target task input.")
    if payload.get("source") != "feishu_export":
        return _blocked_current_task(base, "INVALID_TASK_SOURCE", "Current task source must be feishu_export.", {"source": payload.get("source")})
    if _truthy(payload.get("simulation_only", False)):
        return _blocked_current_task(
            base,
            "SIMULATION_TASK_NOT_ALLOWED_FOR_REAL_DEVICE",
            "simulation_only=true cannot drive real device APP operation.",
        )
    if _is_missing(payload.get("task_id")):
        return _blocked_current_task(base, "TASK_ID_MISSING", "task_id must come from Feishu export.")

    missing_fields = [field for field in CURRENT_TASK_REQUIRED_FIELDS if _is_missing(payload.get(field))]
    if missing_fields:
        return _blocked_current_task(
            base,
            "REQUIRED_FIELD_MISSING",
            "Required task fields are missing.",
            {"missing_fields": missing_fields},
        )

    result = FeishuExportTaskReader().read_json(task_path)
    task = result.task
    if task.vehicle_year is None:
        return _blocked_current_task(base, "VEHICLE_YEAR_PARSE_FAILED", "vehicle_year could not be derived.", {"task": task.to_dict()})
    if task.app_flow_blocked:
        vehicle_year_errors = [reason for reason in task.app_flow_block_reasons if "Cannot derive vehicle_year" in reason]
        if vehicle_year_errors:
            return _blocked_current_task(base, "VEHICLE_YEAR_PARSE_FAILED", vehicle_year_errors[0], {"task": task.to_dict()})
        return _blocked_current_task(
            base,
            "REQUIRED_FIELD_MISSING",
            "Task fields failed APP-flow validation.",
            {"task": task.to_dict(), "reasons": task.app_flow_block_reasons},
        )

    return {
        **base,
        "status": "TASK_IMPORT_VERIFIED",
        "message": "Target task import verified.",
        "task_id": task.task_id,
        "brand": task.brand,
        "series": task.series,
        "model_year": task.model_year,
        "trim": task.trim,
        "color": task.color,
        "registration_date_raw": task.registration_date_raw,
        "vehicle_year": task.vehicle_year,
        "mileage_10k_km": task.mileage_10k_km,
        "transfer_count": task.transfer_count,
        "condition_text": task.condition_text,
        "accident_count": task.accident_count,
        "max_accident_amount": task.max_accident_amount,
        "manual_review_required": task.manual_review_required,
        "manual_review_reasons": task.manual_review_reasons,
        "allow_real_device_operation": task.allow_real_device_operation,
        "next_step_allowed": task.allow_real_device_operation,
        "app_operation_params": task.app_operation_params(),
        "task": task.to_dict(),
    }


def _blocked_current_task(base: dict[str, Any], status: str, message: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        **base,
        "status": status,
        "message": message,
        "allow_real_device_operation": False,
        "next_step_allowed": False,
        **(extra or {}),
    }


def _is_missing(value: Any) -> bool:
    return value is None or value == ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}
