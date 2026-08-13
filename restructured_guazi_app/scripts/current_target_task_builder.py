"""Build current_target_task payloads from Feishu Phase 1 drafts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable

try:
    from registration_date_normalizer import normalize_registration_date
except ImportError:  # pragma: no cover - supports package-style imports in tests.
    from scripts.registration_date_normalizer import normalize_registration_date


REQUIRED_DRAFT_FIELDS: tuple[str, ...] = (
    "brand",
    "series",
    "model_config",
    "license_date",
    "mileage_text",
    "color",
    "transfer_count_text",
    "condition_text",
)

OPTIONAL_DRAFT_FIELDS: tuple[str, ...] = (
    "accident_count_text",
    "max_claim_amount_text",
    "city",
    "remark",
    "guide_price_text",
    "guide_price_wan",
    "emission_standard",
    "energy_type",
    "sunroof_text",
    "sunroof",
    "insurance_expiry",
    "insurance_expire_date",
)

PASSTHROUGH_META_FIELDS: tuple[str, ...] = (
    "raw_model_text",
    "vehicle_model",
    "full_model",
    "raw_message_id",
    "raw_sender_id",
    "raw_chat_id",
    "vehicle_parser_version",
    "vehicle_parser_matched_alias",
    "vehicle_model_decision_code",
    "vehicle_model_identity_key",
    "vehicle_model_identity_key_v2",
    "vehicle_model_identity_key_v2_scope",
    "canonical_brand",
    "canonical_series",
    "canonical_year_model",
    "canonical_config_model",
    "config_semantic_key",
    "config_semantic_version",
    "raw_config_model",
)

FORBIDDEN_RESULT_FIELDS: tuple[str, ...] = (
    "target_score",
    "reference_score",
    "final_reference_index",
    "final_reference_score",
    "boundary_reference_index",
    "boundary_reference_score",
    "competition_coefficient",
    "suggested_purchase_price",
)

FIELD_MAPPING: dict[str, str] = {
    "brand": "brand",
    "series": "series",
    "model_config": "model_config",
    "license_date": "license_date",
    "mileage_text": "mileage_text",
    "color": "color",
    "transfer_count_text": "transfer_count_text",
    "condition_text": "condition_text",
    "accident_count_text": "accident_count_text",
    "max_claim_amount_text": "max_claim_amount_text",
    "city": "city",
    "remark": "remark",
    "guide_price_text": "guide_price_text",
    "guide_price_wan": "guide_price_wan",
    "emission_standard": "emission_standard",
    "energy_type": "energy_type",
    "sunroof_text": "sunroof_text",
    "sunroof": "sunroof",
    "insurance_expiry": "insurance_expiry",
    "insurance_expire_date": "insurance_expire_date",
}

REGISTRATION_DATE_SOURCE_FIELDS: tuple[str, ...] = (
    "license_date",
    "register_date",
    "registration_date",
)


@dataclass(frozen=True)
class BuildResult:
    current_target_task: dict[str, Any]
    missing_fields: list[str]
    warnings: list[str]

    @property
    def valid(self) -> bool:
        return not self.missing_fields


def now_iso(clock: Callable[[], datetime] | None = None) -> str:
    now = clock() if clock else datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.isoformat(timespec="seconds")
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def build_current_target_task(
    draft: dict[str, Any],
    *,
    clock: Callable[[], datetime] | None = None,
) -> BuildResult:
    missing_fields = _missing_required_draft_fields(draft)
    normalized_registration = _normalized_registration_from_draft(draft)
    if _draft_registration_date_value(draft) and normalized_registration is None:
        missing_fields.append("registration_date_year")
    warnings = [
        f"IGNORED_FORBIDDEN_FIELD:{field}"
        for field in FORBIDDEN_RESULT_FIELDS
        if field in draft
    ]

    task: dict[str, Any] = {
        "source": "feishu",
        "task_id": draft.get("task_id"),
        "created_from": "feishu_phase2_pricing_runner",
        "created_at": now_iso(clock),
    }

    for source_field, target_field in FIELD_MAPPING.items():
        if source_field in draft:
            value = draft[source_field]
            if value is None or value == "":
                continue
            task[target_field] = value

    for field in PASSTHROUGH_META_FIELDS:
        if draft.get(field):
            task[field] = draft[field]

    if draft.get("created_at"):
        task["feishu_task_created_at"] = draft["created_at"]

    task.update(_build_mainline_compatibility_fields(draft))

    return BuildResult(
        current_target_task=task,
        missing_fields=missing_fields,
        warnings=warnings,
    )


def _build_mainline_compatibility_fields(draft: dict[str, Any]) -> dict[str, Any]:
    compatibility: dict[str, Any] = {}

    model_config = str(draft.get("model_config") or "").strip()
    if model_config:
        model_year, trim = split_model_config(model_config)
        if model_year:
            compatibility["year_model"] = model_year
            compatibility["model_year"] = model_year
        if trim:
            compatibility["config_model"] = trim
            compatibility["trim"] = trim

    registration = _normalized_registration_from_draft(draft)
    if registration is not None:
        compatibility["license_date"] = registration.normalized_date
        compatibility["register_date"] = registration.normalized_date
        compatibility["registration_date"] = registration.normalized_date
        compatibility["register_year"] = registration.year
        compatibility["registration_date_year"] = registration.year
        compatibility["registration_date_month"] = registration.month

    mileage = parse_first_number(draft.get("mileage_text"))
    if mileage is not None:
        compatibility["mileage_10k_km"] = mileage
        compatibility["display_mileage_wan_km"] = mileage

    transfer_count = parse_first_int(draft.get("transfer_count_text"))
    if transfer_count is not None:
        compatibility["transfer_count"] = transfer_count

    accident_count = parse_first_int(draft.get("accident_count_text"))
    if accident_count is not None:
        compatibility["accident_count"] = accident_count

    max_amount = parse_first_number(draft.get("max_claim_amount_text"))
    if max_amount is not None:
        compatibility["max_accident_amount"] = int(max_amount) if max_amount.is_integer() else max_amount

    city = str(draft.get("city") or "").strip()
    if city:
        compatibility["license_city"] = city
        compatibility["plate_location"] = city

    return compatibility


def _missing_required_draft_fields(draft: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in REQUIRED_DRAFT_FIELDS:
        if field == "license_date":
            if not _draft_registration_date_value(draft):
                missing.append(field)
            continue
        if not str(draft.get(field, "")).strip():
            missing.append(field)
    return missing


def _draft_registration_date_value(draft: dict[str, Any]) -> Any:
    for field in REGISTRATION_DATE_SOURCE_FIELDS:
        value = draft.get(field)
        if str(value or "").strip():
            return value
    return ""


def _normalized_registration_from_draft(draft: dict[str, Any]):
    return normalize_registration_date(_draft_registration_date_value(draft))


def split_model_config(model_config: str) -> tuple[str | None, str | None]:
    match = re.search(r"(19\d{2}|20\d{2})\s*款", model_config)
    if not match:
        return None, model_config.strip() or None
    model_year = f"{match.group(1)}款"
    trim = model_config[match.end() :].strip()
    return model_year, trim or None


def parse_first_number(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def parse_first_int(value: Any) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None
