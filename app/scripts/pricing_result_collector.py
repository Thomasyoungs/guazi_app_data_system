"""Collect pricing_result.json into a Feishu task directory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any


DEFAULT_RESULT_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("data", "pricing_result.json"),
    ("data", "latest_pricing_result.json"),
    ("output", "pricing_result.json"),
    ("pricing_result.json",),
    ("output", "result_s10_to_s16.json"),
    ("output", "result.json"),
)

PRICING_CORE_FIELDS: tuple[str, ...] = (
    "target_score",
    "boundary_confirmed",
    "boundary_reference_index",
    "boundary_reference_score",
    "final_reference_index",
    "final_reference_score",
    "final_reference_price",
    "target_guazi_listing_price_yuan",
    "guazi_service_fee_yuan",
    "guazi_net_payout_yuan",
    "guazi_return_price_yuan",
    "cost_yuan",
    "profit_yuan",
    "suggested_purchase_price_yuan",
    "manual_review_required",
    "manual_review_reason",
    "auto_pricing_allowed",
)

CONTINUE_NEXT_REFERENCE = "CONTINUE_NEXT_REFERENCE"
V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE = "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE"
V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW = (
    "V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW"
)
SECOND_STAGE_COLLECTION_INCOMPLETE = "SECOND_STAGE_COLLECTION_INCOMPLETE"
SECOND_STAGE_CONTINUE_NEXT_REFERENCE_NOT_COMPLETED = "SECOND_STAGE_CONTINUE_NEXT_REFERENCE_NOT_COMPLETED"
RESULT_MISSING_REQUIRED_PRICING_FIELDS = "RESULT_MISSING_REQUIRED_PRICING_FIELDS"
V3_BOUNDARY_NOT_CONFIRMED_AFTER_ALL_REFERENCES = "V3_BOUNDARY_NOT_CONFIRMED_AFTER_ALL_REFERENCES"
REFERENCE_CARD_BINDING_NOT_UNIQUE = "REFERENCE_CARD_BINDING_NOT_UNIQUE"
FULL_CHAIN_PRICED_DONE = "FULL_CHAIN_PRICED_DONE"
CROSS_TASK_PRICING_RESULT_REJECTED = "CROSS_TASK_PRICING_RESULT_REJECTED"
TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED = "TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED"
FINAL_FEEDBACK_TARGET_MISMATCH_BLOCKED = "FINAL_FEEDBACK_TARGET_MISMATCH_BLOCKED"
CROSS_TASK_SUCCESS_RESULT_BLOCKED_BEFORE_FEISHU_SEND = "CROSS_TASK_SUCCESS_RESULT_BLOCKED_BEFORE_FEISHU_SEND"
TASK_SCOPED_RESULT_ISOLATION_VERSION = "TASK_SCOPED_RESULT_ISOLATION_V1_20260702"

NON_TERMINAL_SECOND_STAGE_STATUSES = {
    CONTINUE_NEXT_REFERENCE,
}

INCOMPLETE_PRICING_STATUSES = {
    CONTINUE_NEXT_REFERENCE,
    SECOND_STAGE_COLLECTION_INCOMPLETE,
    SECOND_STAGE_CONTINUE_NEXT_REFERENCE_NOT_COMPLETED,
    RESULT_MISSING_REQUIRED_PRICING_FIELDS,
    V3_BOUNDARY_NOT_CONFIRMED_AFTER_ALL_REFERENCES,
}

PRICING_SUCCESS_REQUIRED_FIELDS: tuple[str, ...] = (
    "boundary_confirmed",
    "boundary_reference_index",
    "boundary_reference_score",
    "final_reference_index",
    "final_reference_score",
    "final_reference_price_yuan",
    "target_guazi_listing_price_yuan",
    "guazi_service_fee_yuan",
    "guazi_net_payout_yuan",
    "guazi_return_price_yuan",
    "cost_yuan",
    "profit_rate",
    "profit_yuan",
    "suggested_purchase_price_yuan",
    "final_purchase_price_yuan",
)

PRICING_SUCCESS_NUMERIC_FIELDS: tuple[str, ...] = tuple(
    field_name for field_name in PRICING_SUCCESS_REQUIRED_FIELDS if field_name != "boundary_confirmed"
)

CONFIG_MISMATCH_HARD_STOP = "CONFIG_MISMATCH_HARD_STOP"
CONFIG_MISMATCH_REASON_CODES = {
    "CONFIG_TIER_MISMATCH",
    "POWERTRAIN_TOKEN_MISMATCH",
}
CONFIG_MISMATCH_RELEVANT_KEYS = {
    "canonical_error_code",
    "error_code",
    "issue_code",
    "stop_code",
    "status",
    "final_status",
    "current_state",
    "decision_code",
    "mismatch_reason",
    "config_semantic_decision_code",
}

SECOND_STAGE_HANDOFF_FAILURE_CODES = {
    "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED",
    REFERENCE_CARD_BINDING_NOT_UNIQUE,
}

MANUAL_REVIEW_FINAL_STATUSES = {
    "FULL_CHAIN_MANUAL_REVIEW_DONE",
}

REFERENCE_BINDING_RECOVERY_PROGRESS_STATUSES = {
    "S14_FULL_IMAGE_SEQUENCE_COLLECTED",
    "FULL_CHAIN_MANUAL_REVIEW_DONE",
    "FULL_CHAIN_PRICED_DONE",
    CONTINUE_NEXT_REFERENCE,
}

HISTORICAL_DIAGNOSTIC_KEYS = {
    "attempt",
    "attempts",
    "binding_attempt",
    "binding_attempts",
    "diagnostics",
    "history",
    "previous_attempt",
    "previous_attempts",
    "raw_history",
    "raw_trace",
    "return_attempt",
    "return_attempts",
    "s14_return_attempts",
    "trace",
}

STALE_ERROR_DIAGNOSTIC_KEYS = {
    "binding_attempt_error_recovered",
    "ignored_stale_error_codes",
    "recovered_attempt_error_codes",
    "stale_attempt_error",
}

BLOCKED_STATUS_VALUES = {
    "SECOND_STAGE_BLOCKED_NOT_AT_S10_READY",
    "S03_TARGET_INITIAL_LETTER_NOT_DERIVABLE",
    "S03_TARGET_INITIAL_LETTER_NOT_FOUND",
    "S03_TARGET_BRAND_NOT_FOUND",
    "S03_TARGET_BRAND_CLICK_FAILED",
    "S03_TARGET_BRAND_PANEL_NOT_READY",
    "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED",
    "S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE",
    "S13_REPAIR_ITEM_CLICK_DID_NOT_OPEN_DETAIL",
    "S13_HISTORY_REPAIR_ENTRY_NOT_VISIBLE_OR_UNBOUND",
    "S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND",
    "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED",
    "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED",
    "S14_STALE_FIRST_LINE_BINDING_UNRESOLVED",
    "S14_CONTRACT_DEGRADED_NEEDS_REVIEW",
    "S14_DEGRADED_COLLECTION_THRESHOLD_EXCEEDED",
    "S14_DETAIL_POPUP_CLOSE_UNSAFE_OR_FAILED",
}

BLOCKED_ISSUE_CODES = {
    "PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE",
}


TARGET_FINGERPRINT_KEYS = (
    "target_fingerprint",
    "task_target_fingerprint",
    "target_vehicle_fingerprint",
    "current_target_fingerprint",
    "target_identity_fingerprint",
)

TARGET_SCOPE_NESTED_KEYS = (
    "target",
    "target_task",
    "current_target_task",
    "task_params",
    "first_stage_result",
    "target_vehicle",
    "pricing_target",
    "vehicle",
)

TARGET_FINGERPRINT_FIELD_ALIASES = (
    ("brand", "canonical_brand", "target_brand"),
    ("series", "canonical_series", "target_series"),
    ("year_model", "model_year", "year款", "year", "target_year_model"),
    ("model_config", "config", "trim", "vehicle_model", "full_model", "target_model_config"),
    ("color", "vehicle_color", "target_color"),
    ("registration_date", "register_date", "license_date", "target_registration_date"),
    ("mileage_text", "mileage_10k_km", "display_mileage_wan_km", "target_mileage"),
    ("transfer_count_text", "transfer_count", "target_transfer_count"),
    ("license_city", "plate_location", "city", "target_city"),
    ("condition_text", "condition_summary", "target_condition"),
)


def _scope_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, dict):
        if "score" in value:
            return _scope_text(value.get("score"))
        return ""
    text = str(value).strip()
    return " ".join(text.split())


def _unique_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _scope_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _first_scope_value(payload: dict[str, Any], aliases: tuple[str, ...]) -> str:
    for alias in aliases:
        if alias in payload:
            text = _scope_text(payload.get(alias))
            if text:
                return text
    return ""


def build_target_fingerprint(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    values = [_first_scope_value(payload, aliases) for aliases in TARGET_FINGERPRINT_FIELD_ALIASES]
    if not values[0] or not values[1]:
        return ""
    visible = [value for value in values if value]
    if len(visible) < 3:
        return ""
    return "|".join(visible)


def target_fingerprint_candidates(payload: dict[str, Any] | None, *, max_depth: int = 3) -> list[str]:
    if not isinstance(payload, dict):
        return []
    values: list[str] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        if depth > max_depth or not isinstance(node, dict):
            return
        for key in TARGET_FINGERPRINT_KEYS:
            text = _scope_text(node.get(key))
            if text:
                values.append(text)
        built = build_target_fingerprint(node)
        if built:
            values.append(built)
        for nested_key in TARGET_SCOPE_NESTED_KEYS:
            nested = node.get(nested_key)
            if isinstance(nested, dict):
                walk(nested, depth + 1)

    walk(payload, 0)
    return _unique_text(values)


def target_fingerprints_from_artifacts(project_root: str | Path, task_dir: str | Path, *, task_id: str | None = None) -> list[str]:
    project_root = Path(project_root)
    task_dir = Path(task_dir)
    candidates: list[Path] = [
        task_dir / "first_stage_result.json",
        task_dir / "target_task_draft.json",
        task_dir / "current_target_task.snapshot.json",
        task_dir / "status.json",
        task_dir / "pricing_result.json",
    ]
    current_target_task = project_root / "data" / "current_target_task.json"
    if current_target_task.exists():
        candidates.append(current_target_task)
    values: list[str] = []
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload_task_id = _scope_text(payload.get("task_id"))
        if task_id and payload_task_id and payload_task_id != str(task_id):
            continue
        values.extend(target_fingerprint_candidates(payload))
    return _unique_text(values)


def result_task_id_candidates(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    values: list[str] = []
    for key in ("task_id", "produced_by_task_id", "source_task_id", "origin_task_id", "current_task_id"):
        text = _scope_text(payload.get(key))
        if text:
            values.append(text)
    for nested_key in ("metadata", "task", "result_identity", "terminal_success_trace"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            for key in ("task_id", "produced_by_task_id", "source_task_id"):
                text = _scope_text(nested.get(key))
                if text:
                    values.append(text)
    return _unique_text(values)


@dataclass(frozen=True)
class ResultTaskScopeCheck:
    ok: bool
    code: str | None
    current_task_id: str
    result_task_ids: list[str] = field(default_factory=list)
    current_target_fingerprints: list[str] = field(default_factory=list)
    result_target_fingerprints: list[str] = field(default_factory=list)
    source_path: str = ""
    reason: str = ""
    task_scope_version: str = TASK_SCOPED_RESULT_ISOLATION_VERSION

    def as_trace(self) -> dict[str, Any]:
        return {
            "task_scope_version": self.task_scope_version,
            "task_scoped_result_guard": True,
            "task_scoped_result_match": self.ok,
            "current_task_id": self.current_task_id,
            "result_task_ids": self.result_task_ids,
            "current_target_fingerprints": self.current_target_fingerprints,
            "result_target_fingerprints": self.result_target_fingerprints,
            "result_source_path": self.source_path,
            "rejection_code": self.code,
            "rejection_reason": self.reason,
        }


def validate_result_task_scope(
    payload: dict[str, Any] | None,
    *,
    current_task_id: str,
    current_target_fingerprints: list[str] | tuple[str, ...] | None = None,
    source_path: str | Path | None = None,
    require_task_id: bool = True,
    require_target_fingerprint: bool = True,
) -> ResultTaskScopeCheck:
    task_id = _scope_text(current_task_id)
    source = str(source_path or "")
    if not isinstance(payload, dict):
        return ResultTaskScopeCheck(False, CROSS_TASK_PRICING_RESULT_REJECTED, task_id, source_path=source, reason="payload_not_dict")
    result_task_ids = result_task_id_candidates(payload)
    current_fps = _unique_text([_scope_text(value) for value in (current_target_fingerprints or [])])
    result_fps = target_fingerprint_candidates(payload)
    if require_task_id and not result_task_ids:
        return ResultTaskScopeCheck(False, CROSS_TASK_PRICING_RESULT_REJECTED, task_id, result_task_ids, current_fps, result_fps, source, "result_task_id_missing")
    if task_id and any(result_task_id != task_id for result_task_id in result_task_ids):
        return ResultTaskScopeCheck(False, CROSS_TASK_PRICING_RESULT_REJECTED, task_id, result_task_ids, current_fps, result_fps, source, "result_task_id_mismatch")
    if require_target_fingerprint and not result_fps:
        return ResultTaskScopeCheck(False, TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED, task_id, result_task_ids, current_fps, result_fps, source, "result_target_fingerprint_missing")
    if current_fps and result_fps and not (set(current_fps) & set(result_fps)):
        return ResultTaskScopeCheck(False, TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED, task_id, result_task_ids, current_fps, result_fps, source, "result_target_fingerprint_mismatch")
    return ResultTaskScopeCheck(True, None, task_id, result_task_ids, current_fps, result_fps, source, "matched")


def stamp_result_task_scope(
    payload: dict[str, Any],
    *,
    task_id: str,
    target_fingerprints: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    payload.setdefault("task_id", task_id)
    payload.setdefault("produced_by_task_id", task_id)
    fingerprints = _unique_text([_scope_text(value) for value in (target_fingerprints or [])])
    if fingerprints:
        payload.setdefault("target_fingerprint", fingerprints[0])
        payload.setdefault("task_target_fingerprint", fingerprints[0])
    payload.setdefault("task_scope_version", TASK_SCOPED_RESULT_ISOLATION_VERSION)
    payload.setdefault("result_recovery_scope", "current_task_only")
    return payload


@dataclass(frozen=True)
class PricingResultCollection:
    ok: bool
    result: dict[str, Any] | None
    source_path: Path | None
    copied_path: Path | None
    errors: list[str]
    schema_ok: bool = True
    pricing_success_ok: bool = False
    missing_required_fields: list[str] = field(default_factory=list)
    non_terminal_status: str | None = None


class PricingResultCollector:
    def __init__(self, *, project_root: str | Path, task_dir: str | Path) -> None:
        self.project_root = Path(project_root)
        self.task_dir = Path(task_dir)

    def collect(
        self,
        *,
        result_path: str | Path | None = None,
        run_started_at: datetime | str | None = None,
    ) -> PricingResultCollection:
        source_path = self._find_result_path(result_path)
        if source_path is None:
            return PricingResultCollection(
                ok=False,
                result=None,
                source_path=None,
                copied_path=None,
                errors=["RESULT_FILE_NOT_FOUND"],
            )

        if run_started_at is not None and _is_stale(source_path, run_started_at):
            return PricingResultCollection(
                ok=False,
                result=None,
                source_path=source_path,
                copied_path=None,
                errors=["STALE_RESULT_FILE"],
            )

        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return PricingResultCollection(
                ok=False,
                result=None,
                source_path=source_path,
                copied_path=None,
                errors=["RESULT_JSON_INVALID"],
            )

        current_task_id = self.task_dir.name
        current_fingerprints = target_fingerprints_from_artifacts(
            self.project_root,
            self.task_dir,
            task_id=current_task_id,
        )
        has_scope_markers = bool(result_task_id_candidates(payload) or target_fingerprint_candidates(payload))
        if has_scope_markers:
            scope_check = validate_result_task_scope(
                payload,
                current_task_id=current_task_id,
                current_target_fingerprints=current_fingerprints,
                source_path=source_path,
                require_task_id=True,
                require_target_fingerprint=bool(current_fingerprints or target_fingerprint_candidates(payload)),
            )
            if not scope_check.ok:
                self._write_rejection(scope_check)
                return PricingResultCollection(
                    ok=False,
                    result=payload,
                    source_path=source_path,
                    copied_path=None,
                    errors=[scope_check.code or CROSS_TASK_PRICING_RESULT_REJECTED],
                    schema_ok=False,
                )
        stamp_result_task_scope(payload, task_id=current_task_id, target_fingerprints=current_fingerprints)

        copied_path = self.task_dir / "pricing_result.json"
        copied_path.parent.mkdir(parents=True, exist_ok=True)
        normalized = normalize_pricing_result_fields(payload, project_root=self.project_root)
        annotations_added = _annotate_recovered_handoff_attempt_errors(payload)
        if normalized or annotations_added:
            copied_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            shutil.copy2(source_path, copied_path)
        schema_errors = validate_pricing_result_payload(payload)
        non_terminal_status = pricing_result_non_terminal_status(payload)
        missing_required_fields = pricing_success_missing_required_fields(payload)
        pricing_success_ok = (
            not schema_errors
            and not non_terminal_status
            and not missing_required_fields
            and pricing_result_business_status(payload) == "SUCCEEDED"
        )
        return PricingResultCollection(
            ok=not schema_errors,
            result=payload,
            source_path=source_path,
            copied_path=copied_path,
            errors=schema_errors,
            schema_ok=not schema_errors,
            pricing_success_ok=pricing_success_ok,
            missing_required_fields=missing_required_fields,
            non_terminal_status=non_terminal_status,
        )

    def _find_result_path(self, result_path: str | Path | None) -> Path | None:
        if result_path is not None:
            path = Path(result_path)
            if not path.is_absolute():
                path = self.project_root / path
            return path if path.exists() else None
        for parts in DEFAULT_RESULT_CANDIDATES:
            path = self.project_root.joinpath(*parts)
            if path.exists():
                return path
        return None

    def _write_rejection(self, scope_check: ResultTaskScopeCheck) -> None:
        path = self.task_dir / "cross_task_pricing_result_rejected.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = scope_check.as_trace()
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_pricing_result_payload(payload: dict[str, Any]) -> list[str]:
    normalize_pricing_result_fields(payload)
    if pricing_result_config_mismatch_reason(payload):
        return [CONFIG_MISMATCH_HARD_STOP]
    if is_automatic_pricing_terminal_success(payload):
        return []
    handoff_error = _second_stage_handoff_error(payload)
    if handoff_error:
        return [handoff_error]
    blocked_error = _blocked_result_error(payload)
    if blocked_error:
        return [blocked_error]
    non_terminal_status = pricing_result_non_terminal_status(payload)
    if non_terminal_status:
        return []
    codes = _collect_payload_codes(payload)
    if V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW in codes and is_pricing_result_manual_review(payload):
        return []
    for status in INCOMPLETE_PRICING_STATUSES:
        if status in codes:
            return [status]
    if not any(_has_non_empty_key(payload, field) or _has_value(resolve_pricing_result_field(payload, field)) for field in PRICING_CORE_FIELDS):
        return ["RESULT_SCHEMA_INVALID_FOR_PRICING"]
    if not is_pricing_result_manual_review(payload):
        missing_required_fields = pricing_success_missing_required_fields(payload)
        if missing_required_fields:
            return [
                RESULT_MISSING_REQUIRED_PRICING_FIELDS,
                *[f"MISSING_REQUIRED_FIELD:{field_name}" for field_name in missing_required_fields],
            ]
    return []


def resolve_pricing_result_field(payload: dict[str, Any], field: str, default: Any = None) -> Any:
    """Read a pricing result field from the top level, s17_payload, pricing, or known aliases."""
    if not isinstance(payload, dict):
        return default
    if field == "target_score":
        return _first_value(
            payload,
            (("s17_payload", "target_score"), ("target_score", "score"), ("target_score",)),
            default=default,
        )
    if field == "final_reference_score":
        return _first_value(
            payload,
            (
                ("final_reference_score",),
                ("s17_payload", "final_reference_score"),
                ("s17_payload", "reference_score"),
                ("selected_reference_score", "score"),
                ("selected_reference", "score"),
                ("current_reference", "reference_score"),
            ),
            default=default,
        )
    if field == "final_reference_index":
        return _first_value(
            payload,
            (
                ("final_reference_index",),
                ("s17_payload", "final_reference_index"),
                ("pricing", "final_reference_index"),
                ("selected_reference", "reference_index"),
                ("selected_reference", "index"),
                ("current_reference", "reference_index"),
                ("final_reference_candidate_index",),
                ("pre_boundary_reference_index",),
            ),
            default=default,
        )
    if field in {"final_reference_price", "final_reference_price_yuan"}:
        return _first_value(
            payload,
            (
                ("final_reference_price_yuan",),
                ("final_reference_price",),
                ("s17_payload", "final_reference_price_yuan"),
                ("s17_payload", "final_reference_price"),
                ("pricing", "base_reference_price_yuan"),
                ("s17_payload", "base_reference_price_yuan"),
                ("s17_payload", "reference_price_10k"),
                ("selected_reference", "list_price_10k"),
            ),
            default=default,
            transform=_price_transform_for_path,
        )
    if field == "manual_review_required":
        value = _first_value(
            payload,
            (("manual_review_required",), ("s17_payload", "manual_review_required"), ("pricing", "manual_review_required")),
            default=None,
        )
        if _has_value(value):
            return _truthy(value)
        if any(str(payload.get(key) or "") in MANUAL_REVIEW_FINAL_STATUSES for key in ("status", "final_status", "current_state")):
            return True
        if pricing_result_manual_review_reasons(payload):
            return True
        return default
    if field == "manual_review_reasons":
        reasons = pricing_result_manual_review_reasons(payload)
        return reasons if reasons else default
    if field == "manual_review_reason":
        reasons = pricing_result_manual_review_reasons(payload)
        return reasons[0] if reasons else default
    if field == "reference_score":
        return _first_value(
            payload,
            (
                ("reference_score",),
                ("s17_payload", "reference_score"),
                ("selected_reference_score", "score"),
                ("selected_reference", "score"),
                ("current_reference", "reference_score"),
            ),
            default=default,
        )
    if field == "reference_price_10k":
        return _first_value(
            payload,
            (("reference_price_10k",), ("s17_payload", "reference_price_10k"), ("selected_reference", "list_price_10k")),
            default=default,
        )

    nested_pricing_fields = {
        "base_reference_price_yuan",
        "target_guazi_listing_price_yuan",
        "guazi_service_fee_yuan",
        "guazi_net_payout_yuan",
        "guazi_return_price_yuan",
        "cost_yuan",
        "profit_yuan",
        "suggested_purchase_price_yuan",
        "suggested_acquisition_price_yuan",
        "competition_coefficient",
    }
    if field in nested_pricing_fields:
        return _first_value(
            payload,
            ((field,), ("s17_payload", field), ("pricing", field)),
            default=default,
        )
    return _first_value(payload, ((field,), ("s17_payload", field), ("pricing", field)), default=default)


def normalize_pricing_result_fields(payload: dict[str, Any] | None, *, project_root: str | Path | None = None) -> bool:
    """Backfill success-path pricing fields before strict result validation."""
    if not isinstance(payload, dict):
        return False
    changed = False
    if _has_raw_terminal_success_signal(payload):
        changed |= _protect_terminal_success_payload(payload, project_root=project_root)
    else:
        changed |= normalize_v33_low_score_continuation_fields(payload)
    manual_review = is_pricing_result_manual_review(payload)

    suggested_price = _resolve_suggested_purchase_price(payload)
    if _has_required_pricing_value(suggested_price):
        changed |= _set_if_missing(payload, "suggested_purchase_price_yuan", suggested_price)
        changed |= _set_if_missing(payload, "system_suggested_price_yuan", suggested_price)
        if not manual_review:
            changed |= _set_if_missing(payload, "final_purchase_price_yuan", suggested_price)
            changed |= _set_if_missing(payload, "final_price_source", "SYSTEM_AUTOMATIC_PRICING")
            changed |= _set_if_missing(payload, "pricing_decision_source", "AUTOMATIC_PRICING")
            changed |= _set_if_missing(payload, "final_purchase_price_required", True)

    profit_rate = _resolve_profit_rate(payload, project_root=project_root)
    if _has_required_pricing_value(profit_rate):
        changed |= _set_if_missing(payload, "profit_rate", profit_rate)
    return changed


def _has_raw_terminal_success_signal(payload: dict[str, Any] | None) -> bool:
    """Detect terminal automatic pricing before any continuation normalizer mutates it."""
    if not isinstance(payload, dict):
        return False
    if pricing_result_config_mismatch_reason(payload):
        return False
    if is_pricing_result_manual_review(payload):
        return False

    pricing_section = payload.get("pricing") if isinstance(payload.get("pricing"), dict) else {}
    s17_payload = payload.get("s17_payload") if isinstance(payload.get("s17_payload"), dict) else {}
    top_level_statuses = {
        str(payload.get(key) or "").strip().upper()
        for key in ("status", "final_status", "current_state", "business_status", "technical_status")
        if payload.get(key) is not None
    }
    source_values = {
        str(_first_value(payload, (path,)) or "").strip().upper()
        for path in (
            ("pricing_decision_source",),
            ("final_price_source",),
            ("pricing", "pricing_decision_source"),
            ("pricing", "final_price_source"),
            ("s17_payload", "pricing_decision_source"),
            ("s17_payload", "final_price_source"),
        )
    }
    terminal_signal = (
        FULL_CHAIN_PRICED_DONE in top_level_statuses
        or "SUCCEEDED" in top_level_statuses
        or str(payload.get("s16_status") or "").strip().upper() == "S16_READY"
        or str(pricing_section.get("status") or "").strip().lower() == "priced"
        or str(s17_payload.get("task_status") or "").strip().lower() == "priced"
        or "AUTOMATIC_PRICING" in source_values
        or "SYSTEM_AUTOMATIC_PRICING" in source_values
    )
    if not terminal_signal:
        return False
    return bool(
        _has_required_pricing_value(_resolve_raw_terminal_price(payload))
        and _has_required_pricing_value(_resolve_suggested_purchase_price(payload))
        and _has_required_pricing_value(_resolve_raw_terminal_reference_index(payload))
    )


def _resolve_raw_terminal_price(payload: dict[str, Any]) -> Any:
    return _first_value(
        payload,
        (
            ("final_purchase_price_yuan",),
            ("system_suggested_price_yuan",),
            ("suggested_purchase_price_yuan",),
            ("pricing", "final_purchase_price_yuan"),
            ("pricing", "system_suggested_price_yuan"),
            ("pricing", "suggested_purchase_price_yuan"),
            ("s17_payload", "final_purchase_price_yuan"),
            ("s17_payload", "system_suggested_price_yuan"),
            ("s17_payload", "suggested_purchase_price_yuan"),
            ("s17_payload", "suggested_acquisition_price_yuan"),
        ),
    )


def _resolve_raw_terminal_reference_index(payload: dict[str, Any]) -> int | None:
    return _first_int(
        payload.get("final_reference_index"),
        payload.get("selected_reference_index"),
        payload.get("final_reference_candidate_index"),
        payload.get("recollect_reference_index"),
        _nested_get(payload, ("selected_reference", "reference_index")),
        _nested_get(payload, ("pricing", "final_reference_index")),
        _nested_get(payload, ("s17_payload", "final_reference_index")),
    )


def _protect_terminal_success_payload(payload: dict[str, Any], *, project_root: str | Path | None = None) -> bool:
    changed = False

    def set_field(key: str, value: Any) -> None:
        nonlocal changed
        if key not in payload or payload.get(key) != value:
            payload[key] = value
            changed = True

    final_price = _resolve_raw_terminal_price(payload)
    suggested_price = _resolve_suggested_purchase_price(payload) or final_price
    final_reference_index = _resolve_raw_terminal_reference_index(payload)
    stale_low_score_state = _infer_v33_low_score_continuation_state(payload)

    for key in ("status", "final_status", "current_state"):
        set_field(key, FULL_CHAIN_PRICED_DONE)
    set_field("business_status", "SUCCEEDED")
    set_field("technical_status", "SUCCEEDED")
    set_field("terminal", True)
    set_field("terminal_success_result_exists", True)
    set_field("terminal_success_result_protected", True)
    set_field("terminal_success_normalization_precedence_applied", True)
    set_field("dispatcher_should_continue", False)
    set_field("should_continue_reference_collection", False)
    set_field("continue_next_reference", False)
    set_field("next_reference_index", None)
    for key in ("issue_code", "stop_code", "canonical_error_code", "error_code"):
        set_field(key, None)
    set_field("failed", False)
    set_field("cancelled", False)
    set_field("final_price_source", "SYSTEM_AUTOMATIC_PRICING")
    set_field("pricing_decision_source", "AUTOMATIC_PRICING")
    if _has_required_pricing_value(final_price):
        set_field("final_purchase_price_yuan", final_price)
        set_field("system_suggested_price_yuan", final_price)
    if _has_required_pricing_value(suggested_price):
        set_field("suggested_purchase_price_yuan", suggested_price)
    if final_reference_index is not None:
        set_field("final_reference_index", final_reference_index)
    if stale_low_score_state is not None:
        set_field("stale_low_score_continuation_ignored", True)
        set_field("stale_nested_low_score_continuation_present", True)
        set_field("stale_nested_low_score_continuation_ignored_due_to_terminal_success", True)
    return changed


def normalize_v33_low_score_continuation_fields(payload: dict[str, Any] | None) -> bool:
    """Promote legal V3.3 low-score-skip continuation to top-level status.

    Runtime can produce a nested current_reference early-exit decision before
    S15/S16 success fields exist. That state is intentionally non-terminal and
    must not be reclassified as RESULT_MISSING_REQUIRED_PRICING_FIELDS.
    """

    if not isinstance(payload, dict):
        return False
    if _has_raw_terminal_success_signal(payload):
        return False
    state = _infer_v33_low_score_continuation_state(payload)
    if not state:
        return False
    changed = False

    def set_field(key: str, value: Any) -> None:
        nonlocal changed
        if payload.get(key) != value:
            payload[key] = value
            changed = True

    set_field("status", CONTINUE_NEXT_REFERENCE)
    set_field("final_status", CONTINUE_NEXT_REFERENCE)
    set_field("current_state", CONTINUE_NEXT_REFERENCE)
    set_field("business_status", CONTINUE_NEXT_REFERENCE)
    set_field("technical_status", "INCOMPLETE")
    set_field("reference_status", "LOW_SCORE_SKIPPED_INCOMPLETE")
    set_field("current_reference_index", state["current_reference_index"])
    set_field("next_reference_index", state["next_reference_index"])
    set_field("remaining_reference_count", state.get("remaining_reference_count"))
    set_field("continue_next_reference", True)
    set_field("dispatcher_should_continue", True)
    set_field("should_continue_reference_collection", True)
    set_field("continue_reason", "EARLY_EXIT_CONTINUE_NEXT_REFERENCE")
    set_field("continuation_source", "low_score_skip")
    set_field("stop_code", V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE)
    set_field("issue_code", V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE)
    set_field("canonical_error_code", CONTINUE_NEXT_REFERENCE)
    set_field("terminal", False)
    set_field("failed", False)
    set_field("cancelled", False)
    set_field("s15_entry_allowed", False)
    set_field("s15_blocked_reason", None)
    set_field("s15_entry_block_reason", None)

    current_reference = payload.get("current_reference")
    if isinstance(current_reference, dict):
        for key, value in {
            "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
            "reference_score_trustworthy": False,
            "reference_score_usable_for_boundary": False,
            "usable_for_boundary": False,
            "usable_for_pre_boundary": False,
            "excluded_from_s16": True,
            "s15_entry_block_reason": None,
        }.items():
            if current_reference.get(key) != value:
                current_reference[key] = value
                changed = True
    return changed


def _infer_v33_low_score_continuation_state(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    def as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    current_reference = as_dict(payload.get("current_reference"))
    if not current_reference:
        current_reference = as_dict(as_dict(payload.get("issue_context")).get("current_reference"))
    decision = as_dict(current_reference.get("early_exit_decision"))
    if not decision:
        decision = as_dict(current_reference.get("s14_in_flight_early_exit_decision"))
    if not decision:
        decision = as_dict(payload.get("early_exit_decision"))

    current_index = _first_int(
        payload.get("current_reference_index"),
        current_reference.get("current_reference_index"),
        current_reference.get("reference_index"),
        decision.get("current_reference_index"),
    )
    nested_next = _first_int(
        decision.get("next_reference_index"),
        current_reference.get("next_reference_index"),
        payload.get("next_reference_index"),
    )
    if current_index is None or nested_next is None or nested_next <= current_index:
        return None

    target_score = _first_float(payload.get("target_score"), decision.get("target_score"), current_reference.get("target_score"))
    if isinstance(payload.get("target_score"), dict):
        target_score = _first_float(payload["target_score"].get("score"), target_score)
    upper_bound = _first_float(
        current_reference.get("reference_score_upper_bound"),
        current_reference.get("max_possible_reference_score"),
        decision.get("reference_score_upper_bound"),
        decision.get("max_possible_reference_score"),
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
    if not (low_score_status and low_score_triggered):
        return None

    returned_to_s10 = (
        current_reference.get("return_to_s10_after_low_score_skip") is True
        or decision.get("return_to_s10_after_low_score_skip") is True
        or payload.get("return_to_s10_after_low_score_skip") is True
    )
    returned_verified = (
        current_reference.get("returned_list_source_verified") is True
        or decision.get("returned_list_source_verified") is True
        or payload.get("returned_list_source_verified") is True
    )
    if not (returned_to_s10 and returned_verified):
        return None

    remaining = _first_int(
        payload.get("remaining_reference_count"),
        current_reference.get("remaining_reference_count"),
        decision.get("remaining_reference_count"),
    )
    if remaining is not None and remaining <= 0:
        return None
    return {
        "current_reference_index": current_index,
        "next_reference_index": nested_next,
        "remaining_reference_count": remaining,
    }


def _first_int(*values: Any) -> int | None:
    for value in values:
        parsed = _safe_int(value)
        if parsed is not None:
            return parsed
    return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, dict):
            value = value.get("score")
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def is_pricing_result_manual_review(payload: dict[str, Any]) -> bool:
    return bool(resolve_pricing_result_field(payload, "manual_review_required", default=False))


def pricing_result_manual_review_reasons(payload: dict[str, Any]) -> list[str]:
    for path in (
        ("manual_review_reasons",),
        ("manual_review_reason",),
        ("s17_payload", "manual_review_reasons"),
        ("s17_payload", "manual_review_reason"),
        ("pricing", "manual_review_reasons"),
        ("pricing", "manual_review_reason"),
    ):
        value = _nested_get(payload, path)
        reasons = _coerce_reason_list(value)
        if reasons:
            return reasons
    return []


def pricing_result_business_status(payload: dict[str, Any] | None) -> str:
    if payload and pricing_result_config_mismatch_reason(payload):
        return "TARGET_INFO_NEEDS_CORRECTION"
    if payload and _has_raw_terminal_success_signal(payload):
        _protect_terminal_success_payload(payload)
        return "SUCCEEDED"
    normalize_pricing_result_fields(payload)
    if payload and pricing_result_config_mismatch_reason(payload):
        return "TARGET_INFO_NEEDS_CORRECTION"
    if payload and is_automatic_pricing_terminal_success(payload):
        return "SUCCEEDED"
    non_terminal_status = pricing_result_non_terminal_status(payload)
    if non_terminal_status:
        return non_terminal_status
    if payload:
        codes = _collect_payload_codes(payload)
        for status in INCOMPLETE_PRICING_STATUSES:
            if status in codes:
                return status
    if payload and (
        resolve_pricing_result_field(payload, "manual_review_confirmed", default=False)
        or resolve_pricing_result_field(payload, "business_status") == "MANUAL_REVIEW_CONFIRMED"
        or payload.get("status") == "MANUAL_REVIEW_CONFIRMED"
    ):
        return "MANUAL_REVIEW_CONFIRMED"
    if payload and is_pricing_result_manual_review(payload):
        return "NEEDS_REVIEW"
    if payload and pricing_success_missing_required_fields(payload):
        return RESULT_MISSING_REQUIRED_PRICING_FIELDS
    return "SUCCEEDED"


def pricing_result_non_terminal_status(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    codes = _collect_payload_codes(payload)
    for status in NON_TERMINAL_SECOND_STAGE_STATUSES:
        if status in codes:
            return status
    return None


def pricing_success_missing_required_fields(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return list(PRICING_SUCCESS_REQUIRED_FIELDS)
    normalize_pricing_result_fields(payload)
    if is_automatic_pricing_terminal_success(payload):
        return []
    if pricing_result_non_terminal_status(payload):
        return []
    if pricing_result_config_mismatch_reason(payload) or is_pricing_result_manual_review(payload):
        return []
    missing: list[str] = []
    for field_name in PRICING_SUCCESS_REQUIRED_FIELDS:
        value = resolve_pricing_result_field(payload, field_name)
        if field_name == "boundary_confirmed":
            if value is not True:
                missing.append(field_name)
            continue
        if not _has_required_pricing_value(value):
            missing.append(field_name)
            continue
        if field_name in PRICING_SUCCESS_NUMERIC_FIELDS and not _is_numeric_pricing_value(value):
            missing.append(field_name)
    return missing


def pricing_result_success_ok(payload: dict[str, Any] | None) -> bool:
    return (
        isinstance(payload, dict)
        and pricing_result_business_status(payload) == "SUCCEEDED"
        and not validate_pricing_result_payload(payload)
        and not pricing_success_missing_required_fields(payload)
    )


def is_automatic_pricing_terminal_success(payload: dict[str, Any] | None) -> bool:
    """Return True for complete automatic S16 pricing terminal results.

    V3.3 recollect runs may expose the success as FULL_CHAIN_PRICED_DONE /
    S16_READY while the reference identity still lives under selected_reference.
    Runner and dispatcher share this predicate so later failures cannot
    overwrite an already priced result.
    """

    if not isinstance(payload, dict):
        return False
    if _has_raw_terminal_success_signal(payload):
        return True
    if pricing_result_config_mismatch_reason(payload):
        return False
    top_level_statuses = {
        str(payload.get(key) or "")
        for key in ("status", "final_status", "current_state", "business_status")
    }
    if CONTINUE_NEXT_REFERENCE in top_level_statuses:
        return False
    if is_pricing_result_manual_review(payload):
        return False
    pricing_section = payload.get("pricing") if isinstance(payload.get("pricing"), dict) else {}
    s17_payload = payload.get("s17_payload") if isinstance(payload.get("s17_payload"), dict) else {}
    terminal_signal = (
        FULL_CHAIN_PRICED_DONE in top_level_statuses
        or str(payload.get("s16_status") or "") == "S16_READY"
        or str(pricing_section.get("status") or "").lower() == "priced"
        or str(s17_payload.get("task_status") or "").lower() == "priced"
        or str(resolve_pricing_result_field(payload, "pricing_decision_source") or "") == "AUTOMATIC_PRICING"
    )
    if not terminal_signal:
        return False
    required_values = (
        resolve_pricing_result_field(payload, "final_purchase_price_yuan"),
        resolve_pricing_result_field(payload, "suggested_purchase_price_yuan"),
        resolve_pricing_result_field(payload, "final_reference_index"),
    )
    return all(_has_required_pricing_value(value) for value in required_values)


def pricing_result_config_mismatch_reason(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    codes = _collect_config_mismatch_codes(payload)
    for reason in sorted(CONFIG_MISMATCH_REASON_CODES):
        if reason in codes:
            return reason
    if CONFIG_MISMATCH_HARD_STOP in codes:
        return CONFIG_MISMATCH_HARD_STOP
    return None


def _blocked_result_error(payload: dict[str, Any]) -> str | None:
    status_values = {
        str(payload.get("status") or ""),
        str(payload.get("final_status") or ""),
        str(_nested_get(payload, ("first_stage_evidence", "status")) or ""),
        str(_nested_get(payload, ("first_stage_evidence", "final_status")) or ""),
    }
    issue_values = {
        str(payload.get("issue_code") or ""),
        str(_nested_get(payload, ("first_stage_evidence", "issue_code")) or ""),
    }
    specific_stage_errors = [
        value
        for value in sorted(status_values | issue_values)
        if value.startswith("S13_") or value.startswith("S14_")
    ]
    if specific_stage_errors:
        return specific_stage_errors[0]
    blocked_status = sorted(status_values & BLOCKED_STATUS_VALUES)
    if blocked_status or issue_values & BLOCKED_ISSUE_CODES or _contains_false_s10_ready(payload):
        return "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY"
    return None


def _second_stage_handoff_error(payload: dict[str, Any]) -> str | None:
    codes = _collect_payload_codes(payload)
    if "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED" in codes:
        return "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED"
    if _has_unrecovered_reference_card_binding_error(payload):
        return REFERENCE_CARD_BINDING_NOT_UNIQUE
    return None


def _annotate_recovered_handoff_attempt_errors(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if _has_top_level_reference_card_binding_error(payload):
        return False
    recovery = _reference_card_binding_recovery(payload)
    if not recovery:
        return False
    ignored = list(payload.get("ignored_stale_error_codes") or [])
    if REFERENCE_CARD_BINDING_NOT_UNIQUE not in ignored:
        ignored.append(REFERENCE_CARD_BINDING_NOT_UNIQUE)
    payload["ignored_stale_error_codes"] = ignored
    payload["binding_attempt_error_recovered"] = True
    payload["recovered_attempt_error_codes"] = ignored
    if recovery.get("failed_attempt_index") is not None:
        payload["stale_reference_binding_failed_attempt_index"] = recovery.get("failed_attempt_index")
    if recovery.get("recovered_by_attempt_index") is not None:
        payload["stale_reference_binding_recovered_by_attempt_index"] = recovery.get("recovered_by_attempt_index")
    return True


def _has_unrecovered_reference_card_binding_error(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if _has_top_level_reference_card_binding_error(payload):
        return True
    recovery = _reference_card_binding_recovery(payload)
    if recovery:
        return False
    attempts = _reference_binding_attempts(payload)
    if attempts:
        return any(_payload_has_reference_card_binding_code(attempt) for _order, _index, attempt in attempts)
    return _has_active_reference_card_binding_code(payload)


def _has_top_level_reference_card_binding_error(payload: dict[str, Any]) -> bool:
    for key in (
        "canonical_error_code",
        "error_code",
        "issue_code",
        "stop_code",
        "status",
        "final_status",
        "current_state",
    ):
        if str(payload.get(key) or "") == REFERENCE_CARD_BINDING_NOT_UNIQUE:
            return True
    errors = payload.get("errors")
    if isinstance(errors, list) and REFERENCE_CARD_BINDING_NOT_UNIQUE in {str(item) for item in errors}:
        return True
    return False


def _reference_card_binding_recovery(payload: dict[str, Any]) -> dict[str, Any] | None:
    attempts = _reference_binding_attempts(payload)
    last_failure: tuple[float, int | None] | None = None
    last_success: tuple[float, int | None] | None = None
    for order, attempt_index, attempt in attempts:
        if _payload_has_reference_card_binding_code(attempt):
            last_failure = (order, attempt_index)
        if _payload_has_successful_reference_card_binding(attempt):
            last_success = (order, attempt_index)
    if last_failure and last_success and last_success[0] > last_failure[0]:
        return {
            "failed_attempt_index": last_failure[1],
            "recovered_by_attempt_index": last_success[1],
        }

    returned_evidence = _returned_s10_reliable_evidence(payload)
    returned_attempt_index = _safe_int(payload.get("returned_s10_snapshot_attempt_index"))
    if returned_attempt_index is None:
        current_reference = payload.get("current_reference")
        if isinstance(current_reference, dict):
            returned_attempt_index = _safe_int(current_reference.get("returned_s10_snapshot_attempt_index"))
    if (
        last_failure
        and _payload_has_successful_reference_card_binding(returned_evidence)
        and (
            returned_attempt_index is None
            or last_failure[1] is None
            or returned_attempt_index > last_failure[1]
        )
    ):
        return {
            "failed_attempt_index": last_failure[1],
            "recovered_by_attempt_index": returned_attempt_index,
        }

    if (
        last_failure
        and _payload_has_successful_reference_card_binding(payload)
        and _payload_has_reference_binding_progress_after_attempt(payload)
    ):
        return {
            "failed_attempt_index": last_failure[1],
            "recovered_by_attempt_index": returned_attempt_index,
        }
    return None


def _reference_binding_attempts(payload: Any) -> list[tuple[float, int | None, dict[str, Any]]]:
    attempts: list[tuple[float, int | None, dict[str, Any]]] = []

    def walk(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                lowered = key_text.lower()
                if isinstance(item, list) and ("attempt" in lowered or key_text in HISTORICAL_DIAGNOSTIC_KEYS):
                    for position, entry in enumerate(item):
                        if isinstance(entry, dict):
                            attempt_index = _safe_int(entry.get("attempt_index"))
                            order = float(attempt_index if attempt_index is not None else position + 1)
                            attempts.append((order, attempt_index, entry))
                if isinstance(item, (dict, list)):
                    walk(item, key_text)
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, (dict, list)):
                    walk(entry, parent_key)

    walk(payload)
    attempts.sort(key=lambda item: item[0])
    return attempts


def _returned_s10_reliable_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = payload.get("returned_s10_reliable_evidence")
    if isinstance(evidence, dict):
        return evidence
    current_reference = payload.get("current_reference")
    if isinstance(current_reference, dict):
        evidence = current_reference.get("returned_s10_reliable_evidence")
        if isinstance(evidence, dict):
            return evidence
    return {}


def _payload_has_reference_binding_progress_after_attempt(payload: Any) -> bool:
    if isinstance(payload, dict):
        statuses = {
            str(payload.get(key) or "")
            for key in ("status", "final_status", "current_state", "business_status")
        }
        if statuses & REFERENCE_BINDING_RECOVERY_PROGRESS_STATUSES:
            return True
        if payload.get("s15_entry_allowed") is True:
            return True
        if payload.get("s14_collect_done") is True or payload.get("s14_last_page_reached") is True:
            return True
        if str(payload.get("target_score_source") or "").startswith("score_target_runtime_s15"):
            return True
        return any(
            _payload_has_reference_binding_progress_after_attempt(item)
            for item in payload.values()
            if isinstance(item, (dict, list))
        )
    if isinstance(payload, list):
        return any(_payload_has_reference_binding_progress_after_attempt(item) for item in payload)
    return False


def _payload_has_successful_reference_card_binding(payload: Any) -> bool:
    if isinstance(payload, dict):
        gate = payload.get("selected_reference_card_gate_passed")
        if gate is None:
            gate = payload.get("gate_passed")
        if gate is True:
            return True
        if payload.get("binding_unique") is True or payload.get("target_card_unique") is True:
            candidate_count = _safe_int(payload.get("candidate_count") or payload.get("target_candidate_count"))
            if candidate_count in (None, 1):
                return True
        if payload.get("target_card_visible") is True and payload.get("target_card_matches_expected") is not False:
            if payload.get("selected_reference_card_gate_passed") is True:
                return True
        selected = payload.get("selected_card") or payload.get("bound_card")
        if isinstance(selected, dict) and payload.get("candidate_count") in (1, "1", None):
            return True
        return any(
            _payload_has_successful_reference_card_binding(item)
            for key, item in payload.items()
            if key not in STALE_ERROR_DIAGNOSTIC_KEYS and isinstance(item, (dict, list))
        )
    if isinstance(payload, list):
        return any(_payload_has_successful_reference_card_binding(item) for item in payload)
    return False


def _has_active_reference_card_binding_code(payload: Any, *, parent_key: str = "") -> bool:
    if isinstance(payload, dict):
        for key, item in payload.items():
            key_text = str(key)
            lowered = key_text.lower()
            if key_text in STALE_ERROR_DIAGNOSTIC_KEYS:
                continue
            if key_text in HISTORICAL_DIAGNOSTIC_KEYS or lowered.endswith("_history"):
                continue
            if key_text in {
                "canonical_error_code",
                "error_code",
                "issue_code",
                "stop_code",
                "status",
                "final_status",
                "current_state",
            } and str(item) == REFERENCE_CARD_BINDING_NOT_UNIQUE:
                return True
            if key_text == "errors" and isinstance(item, list):
                if REFERENCE_CARD_BINDING_NOT_UNIQUE in {str(entry) for entry in item}:
                    return True
            if isinstance(item, (dict, list)) and _has_active_reference_card_binding_code(item, parent_key=key_text):
                return True
    elif isinstance(payload, list):
        return any(_has_active_reference_card_binding_code(item, parent_key=parent_key) for item in payload)
    return False


def _payload_has_reference_card_binding_code(payload: Any) -> bool:
    return REFERENCE_CARD_BINDING_NOT_UNIQUE in _collect_payload_codes(payload)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _collect_payload_codes(value: Any) -> set[str]:
    codes: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {
                "canonical_error_code",
                "error_code",
                "issue_code",
                "stop_code",
                "status",
                "final_status",
                "current_state",
            } and isinstance(item, (str, int, float, bool)):
                codes.add(str(item))
            if key in {"errors", "warnings", "second_stage_fast_handoff_strong_error_signals"} and isinstance(item, list):
                codes.update(str(entry) for entry in item if isinstance(entry, (str, int, float, bool)))
            codes.update(_collect_payload_codes(item))
    elif isinstance(value, list):
        for item in value:
            codes.update(_collect_payload_codes(item))
    elif isinstance(value, (str, int, float, bool)):
        text = str(value)
        if text in SECOND_STAGE_HANDOFF_FAILURE_CODES:
            codes.add(text)
    return codes


def _collect_config_mismatch_codes(value: Any, *, parent_key: str = "") -> set[str]:
    codes: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text in CONFIG_MISMATCH_RELEVANT_KEYS and isinstance(item, (str, int, float, bool)):
                codes.add(str(item))
            if key_text in {"errors", "warnings"} and isinstance(item, list):
                codes.update(str(entry) for entry in item if isinstance(entry, (str, int, float, bool)))
            codes.update(_collect_config_mismatch_codes(item, parent_key=key_text))
    elif isinstance(value, list):
        for item in value:
            codes.update(_collect_config_mismatch_codes(item, parent_key=parent_key))
    return {code for code in codes if code}


def _has_non_empty_key(payload: dict[str, Any], key: str) -> bool:
    return key in payload and payload[key] not in (None, "")


def _has_value(value: Any) -> bool:
    return value not in (None, "")


def _has_required_pricing_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() not in {"", "未输出", "None", "none", "null", "NULL", "N/A", "n/a", "--"}
    return True


def _is_numeric_pricing_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("元", "").replace("%", "")
        if not _has_required_pricing_value(text):
            return False
        try:
            float(text)
            return True
        except ValueError:
            return False
    return False


def _nested_get(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_value(
    payload: dict[str, Any],
    paths: tuple[tuple[str, ...], ...],
    *,
    default: Any = None,
    transform: Any = None,
) -> Any:
    for path in paths:
        value = _nested_get(payload, path)
        if not _has_value(value):
            continue
        if transform is not None:
            value = transform(path, value)
        if _has_value(value):
            return value
    return default


def _price_transform_for_path(path: tuple[str, ...], value: Any) -> Any:
    if path and path[-1] in {"reference_price_10k", "list_price_10k"}:
        try:
            return round(float(str(value).replace("万", "").strip()) * 10000)
        except (TypeError, ValueError):
            return value
    return value


def _set_if_missing(payload: dict[str, Any], key: str, value: Any) -> bool:
    if _has_required_pricing_value(payload.get(key)):
        return False
    payload[key] = value
    return True


def _resolve_suggested_purchase_price(payload: dict[str, Any]) -> Any:
    return _first_value(
        payload,
        (
            ("suggested_purchase_price_yuan",),
            ("s17_payload", "suggested_purchase_price_yuan"),
            ("s17_payload", "suggested_acquisition_price_yuan"),
            ("pricing", "suggested_purchase_price_yuan"),
            ("pricing", "suggested_acquisition_price_yuan"),
            ("suggested_acquisition_price_yuan",),
        ),
    )


def _resolve_profit_rate(payload: dict[str, Any], *, project_root: str | Path | None = None) -> Any:
    value = _first_value(
        payload,
        (
            ("profit_rate",),
            ("pricing", "profit_rate"),
            ("pricing", "expected_profit_rate"),
            ("s17_payload", "profit_rate"),
            ("rule_source", "profit_rate"),
            ("active_pricing_rule", "profit_rate"),
            ("pricing_rule_source_guard", "profit_rate"),
        ),
    )
    if _has_required_pricing_value(value):
        return _coerce_profit_rate(value)
    config_value = _profit_rate_from_config(project_root)
    return _coerce_profit_rate(config_value) if _has_required_pricing_value(config_value) else None


def _profit_rate_from_config(project_root: str | Path | None) -> Any:
    if project_root is None:
        return None
    path = Path(project_root) / "config" / "fields.yaml"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    pricing = data.get("pricing")
    if isinstance(pricing, dict):
        return pricing.get("profit_rate")
    return None


def _coerce_profit_rate(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            try:
                return float(text[:-1].strip()) / 100
            except ValueError:
                return value
    return value


def _coerce_reason_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if str(value or "").strip():
        return [str(value).strip()]
    return []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _contains_false_s10_ready(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) == "S10_READY" and _is_false(item):
                return True
            if _contains_false_s10_ready(item):
                return True
    if isinstance(value, list):
        return any(_contains_false_s10_ready(item) for item in value)
    return False


def _is_false(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    return str(value).strip().lower() in {"false", "0", "no", "n"}


def _is_stale(path: Path, run_started_at: datetime | str) -> bool:
    threshold = _coerce_datetime(run_started_at)
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return modified < threshold


def _coerce_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
