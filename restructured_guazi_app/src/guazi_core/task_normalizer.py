"""Target task normalization for Feishu-sourced vehicle tasks.

Migrated from the original task_normalizer.py.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


SUPPORTED_TASK_SOURCES = {"mock", "feishu_export", "feishu_api"}
REAL_DEVICE_OPERATION_SOURCES = {"feishu_export", "feishu_api"}
APP_FLOW_REQUIRED_FIELDS = ("task_id", "brand", "series", "model_year", "trim", "color", "registration_date")
PRICING_REQUIRED_FIELDS = ("registration_date", "mileage_10k_km", "transfer_count", "condition_text")
APP_OPERATION_FIELDS = ("brand", "series", "model_year", "trim", "color", "vehicle_year")


class TaskContractError(ValueError):
    """Raised when task input violates source or ownership rules."""


@dataclass
class TargetCarTask:
    task_id: str | None
    brand: str | None
    series: str | None
    model_year: str | None
    trim: str | None
    color: str | None
    registration_date_raw: str | None
    vehicle_year: int | None
    mileage_10k_km: float | None
    transfer_count: int | None
    condition_text: str | None
    accident_count: int | None = None
    max_accident_amount: float | str | None = None
    manual_review_required: bool = False
    manual_review_reasons: list[str] = field(default_factory=list)
    app_flow_blocked: bool = False
    app_flow_block_reasons: list[str] = field(default_factory=list)
    pricing_blocked: bool = False
    pricing_block_reasons: list[str] = field(default_factory=list)
    source: str = "feishu_api"
    simulation_only: bool = False
    simulation_warnings: list[str] = field(default_factory=list)
    source_import_path: str | None = None
    source_imported_at: str | None = None
    allow_real_device_operation: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["app_operation_params"] = self.app_operation_params()
        return data

    def app_operation_params(self) -> dict[str, Any]:
        return {
            "brand": self.brand,
            "series": self.series,
            "model_year": self.model_year,
            "trim": self.trim,
            "color": self.color,
            "vehicle_year": self.vehicle_year,
        }


def derive_vehicle_year(registration_date: str | int | None) -> int:
    if isinstance(registration_date, int):
        return registration_date
    match = re.search(r"(19\d{2}|20\d{2})", str(registration_date or ""))
    if not match:
        raise TaskContractError(f"Cannot derive vehicle_year from registration_date={registration_date!r}.")
    return int(match.group(1))


def normalize_target_task(
    raw: dict[str, Any],
    *,
    source: str | None = None,
    simulation_only: bool | None = None,
    source_import_path: str | None = None,
    source_imported_at: str | None = None,
) -> TargetCarTask:
    if "reference_index" in raw:
        raise TaskContractError("reference_index is system-generated and must not appear in target task input.")

    task_source = str(source or raw.get("source") or "")
    is_simulation_only = bool(raw.get("simulation_only") if simulation_only is None else simulation_only)
    if task_source not in SUPPORTED_TASK_SOURCES:
        raise TaskContractError(f"Unsupported target task source: {task_source or '<missing>'}.")
    if task_source == "mock" and not is_simulation_only:
        raise TaskContractError("mock target tasks must be marked simulation_only=true.")

    target_input_fields = (
        "task_id", "brand", "series", "model_year", "trim", "color",
        "registration_date", "mileage_10k_km", "transfer_count",
        "condition_text", "accident_count", "max_accident_amount",
    )
    data = {field: _empty_to_none(raw.get(field)) for field in target_input_fields}
    app_block_reasons = _missing_reasons(data, APP_FLOW_REQUIRED_FIELDS, "APP flow")
    pricing_block_reasons = _missing_reasons(data, PRICING_REQUIRED_FIELDS, "pricing")

    vehicle_year: int | None = None
    registration_date_raw = _string_or_none(data["registration_date"])
    if registration_date_raw:
        try:
            vehicle_year = derive_vehicle_year(registration_date_raw)
        except TaskContractError as exc:
            app_block_reasons.append(str(exc))
            pricing_block_reasons.append(str(exc))

    manual_review_reasons: list[str] = []
    if data["accident_count"] is None:
        manual_review_reasons.append("accident_count missing; downstream scoring must use default score 4 and require manual review.")
    if data["max_accident_amount"] is None:
        manual_review_reasons.append("max_accident_amount missing; downstream scoring must use default score 3 and require manual review.")

    simulation_warnings: list[str] = []
    if task_source == "mock":
        simulation_warnings.append("simulation_only: mock target task must not be used by the official Feishu flow.")

    allow_real_device = task_source in REAL_DEVICE_OPERATION_SOURCES and not is_simulation_only and not app_block_reasons

    return TargetCarTask(
        task_id=_string_or_none(data["task_id"]),
        brand=_string_or_none(data["brand"]),
        series=_string_or_none(data["series"]),
        model_year=_string_or_none(data["model_year"]),
        trim=_string_or_none(data["trim"]),
        color=_string_or_none(data["color"]),
        registration_date_raw=registration_date_raw,
        vehicle_year=vehicle_year,
        mileage_10k_km=_float_or_none(data["mileage_10k_km"]),
        transfer_count=_int_or_none(data["transfer_count"]),
        condition_text=_string_or_none(data["condition_text"]),
        accident_count=_int_or_none(data["accident_count"]),
        max_accident_amount=_amount_or_none(data["max_accident_amount"]),
        manual_review_required=bool(manual_review_reasons),
        manual_review_reasons=manual_review_reasons,
        app_flow_blocked=bool(app_block_reasons),
        app_flow_block_reasons=app_block_reasons,
        pricing_blocked=bool(pricing_block_reasons),
        pricing_block_reasons=pricing_block_reasons,
        source=task_source,
        simulation_only=is_simulation_only,
        simulation_warnings=simulation_warnings,
        source_import_path=_string_or_none(source_import_path or raw.get("source_import_path")),
        source_imported_at=_string_or_none(source_imported_at or raw.get("source_imported_at")),
        allow_real_device_operation=allow_real_device,
    )


def _missing_reasons(data: dict[str, Any], fields: tuple[str, ...], phase: str) -> list[str]:
    return [f"{phase} blocked: missing required field {field}." for field in fields if data.get(field) in (None, "")]


def _empty_to_none(value: Any) -> Any:
    if value == "":
        return None
    return value


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _amount_or_none(value: Any) -> float | str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    try:
        return float(text)
    except ValueError:
        return text


def real_device_operation_allowed(task: TargetCarTask | None) -> bool:
    if task is None:
        return False
    return _allows_real_device_operation(task.source, task.simulation_only, task.app_flow_blocked)


def _allows_real_device_operation(task_source: str, is_simulation_only: bool, app_flow_blocked: bool) -> bool:
    return task_source in REAL_DEVICE_OPERATION_SOURCES and not is_simulation_only and not app_flow_blocked


def brand_entry_gate(task: TargetCarTask | None) -> dict[str, Any]:
    """Validate the target-task gate before a real device can click S02 brand entry."""
    if task is None:
        return {"allowed": False, "reason": "TARGET_TASK_MISSING", "details": ["No TargetCarTask is loaded."]}
    details: list[str] = []
    if task.source not in SUPPORTED_TASK_SOURCES:
        details.append(f"Unsupported target task source: {task.source}.")
    if task.source == "mock":
        details.append("Mock target tasks cannot drive real device APP actions.")
    if task.simulation_only:
        details.append("simulation_only target tasks cannot drive real device APP actions.")
    if task.source not in REAL_DEVICE_OPERATION_SOURCES:
        details.append(f"Source {task.source} is not allowed for real device operation.")
    if task.app_flow_blocked:
        details.extend(task.app_flow_block_reasons)
    for field in APP_OPERATION_FIELDS:
        if getattr(task, field if field != "vehicle_year" else "vehicle_year") in (None, ""):
            details.append(f"APP operation blocked: missing required parameter {field}.")
    if task.task_id in (None, ""):
        details.append("APP operation blocked: missing required parameter task_id.")
    return {
        "allowed": not details,
        "reason": "OK" if not details else "TARGET_TASK_GATE_BLOCKED",
        "details": details,
        "source": task.source,
        "allow_real_device_operation": real_device_operation_allowed(task),
    }
