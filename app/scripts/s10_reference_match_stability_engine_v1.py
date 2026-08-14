"""Stable S10 reference identity matching helpers.

This module is intentionally pure and side-effect free. It does not collect
reference cars, select final references, score, or price; it only normalizes
target/reference model identity evidence into deterministic match decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from config_semantic_normalization_v1 import match_config_semantics, normalize_config_semantics
    from universal_vehicle_parser_v1 import (
        build_vehicle_model_identity_key,
        build_vehicle_model_identity_key_v2,
        normalize_canonical_config,
        parse_vehicle_model,
    )
except ImportError:  # pragma: no cover - supports package-style imports in tests.
    from scripts.config_semantic_normalization_v1 import match_config_semantics, normalize_config_semantics
    from scripts.universal_vehicle_parser_v1 import (
        build_vehicle_model_identity_key,
        build_vehicle_model_identity_key_v2,
        normalize_canonical_config,
        parse_vehicle_model,
    )


ENGINE_VERSION = "S10_REFERENCE_MATCH_STABILITY_ENGINE_V1"


@dataclass(frozen=True)
class StableVehicleIdentity:
    ok: bool
    identity_key_v2: str = ""
    strict_identity_key_v1: str = ""
    brand: str = ""
    series: str = ""
    year_model: str = ""
    canonical_config_model: str = ""
    config_semantic_key: str = ""
    source: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "identity_key_v2": self.identity_key_v2,
            "strict_identity_key_v1": self.strict_identity_key_v1,
            "brand": self.brand,
            "series": self.series,
            "year_model": self.year_model,
            "canonical_config_model": self.canonical_config_model,
            "config_semantic_key": self.config_semantic_key,
            "source": self.source,
            "error": self.error,
        }


def _first_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def build_stable_vehicle_identity(payload: dict[str, Any], *, source: str = "") -> StableVehicleIdentity:
    key_v2 = str(payload.get("vehicle_model_identity_key_v2") or "").strip()
    strict_key = str(payload.get("vehicle_model_identity_key") or "").strip()
    brand = _first_text(payload, ("canonical_brand", "brand"))
    series = _first_text(payload, ("canonical_series", "series"))
    year_model = _first_text(payload, ("canonical_year_model", "year_model", "model_year"))
    config_model = _first_text(payload, ("canonical_config_model", "config_model", "trim"))
    config_semantic_key = str(payload.get("config_semantic_key") or "").strip()

    if not key_v2:
        model_text = _first_text(payload, ("model_config", "vehicle_title", "title", "card_title", "name"))
        parsed = parse_vehicle_model(model_text) if model_text else None
        if parsed and parsed.ok:
            brand = parsed.canonical_brand
            series = parsed.canonical_series
            year_model = parsed.canonical_year_model
            config_model = parsed.canonical_config_model
            config_semantic_key = parsed.config_semantic_key
            key_v2 = parsed.vehicle_model_identity_key_v2
            strict_key = parsed.vehicle_model_identity_key

    if not key_v2 and brand and series:
        key_v2 = build_vehicle_model_identity_key_v2(
            brand=brand,
            series=series,
            year_model=year_model,
        )
    if not strict_key and brand and series:
        strict_key = build_vehicle_model_identity_key(
            brand=brand,
            series=series,
            year_model=year_model,
            config_model=config_model,
        )
    if config_model:
        config_model = normalize_canonical_config(config_model)
    if not config_semantic_key and config_model:
        config_semantic_key = normalize_config_semantics(config_model).semantic_key

    if not key_v2:
        return StableVehicleIdentity(ok=False, source=source, error="VEHICLE_IDENTITY_KEY_MISSING")
    return StableVehicleIdentity(
        ok=True,
        identity_key_v2=key_v2,
        strict_identity_key_v1=strict_key,
        brand=brand,
        series=series,
        year_model=year_model,
        canonical_config_model=config_model,
        config_semantic_key=config_semantic_key,
        source=source,
    )


def match_s10_reference_identity(
    target: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    target_identity = build_stable_vehicle_identity(target, source="target")
    reference_identity = build_stable_vehicle_identity(reference, source="reference")
    semantic_config_result = match_config_semantics(
        target_identity.config_semantic_key or target_identity.canonical_config_model,
        reference_identity.config_semantic_key or reference_identity.canonical_config_model,
    )
    identity_key_v2_match = False
    if not target_identity.ok or not reference_identity.ok:
        decision = "S10_REFERENCE_IDENTITY_UNKNOWN"
        identity_match = False
    else:
        identity_key_v2_match = target_identity.identity_key_v2 == reference_identity.identity_key_v2
        if not identity_key_v2_match:
            identity_match = False
            decision = "S10_REFERENCE_IDENTITY_MISMATCH"
        elif not semantic_config_result["semantic_match"]:
            identity_match = False
            decision = "S10_REFERENCE_CONFIG_SEMANTIC_MISMATCH"
        else:
            identity_match = True
            decision = "S10_REFERENCE_IDENTITY_MATCH"
    return {
        "engine_version": ENGINE_VERSION,
        "decision_code": decision,
        "identity_match": identity_match,
        "identity_key_v2_match": identity_key_v2_match,
        "config_semantic_decision_code": semantic_config_result["decision_code"],
        "config_semantic_match": semantic_config_result["semantic_match"],
        "config_semantic_result": semantic_config_result,
        "target_identity": target_identity.as_dict(),
        "reference_identity": reference_identity.as_dict(),
        "identity_key_v2_scope": "brand_series_year",
    }
