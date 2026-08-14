"""Deterministic Universal Vehicle Parser V1 for Feishu target model text."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

try:
    from config_semantic_normalization_v1 import ENGINE_VERSION as CONFIG_SEMANTIC_VERSION
    from config_semantic_normalization_v1 import normalize_config_semantics
except ImportError:  # pragma: no cover - supports package-style imports in tests.
    from scripts.config_semantic_normalization_v1 import ENGINE_VERSION as CONFIG_SEMANTIC_VERSION
    from scripts.config_semantic_normalization_v1 import normalize_config_semantics


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "universal_vehicle_parser_v1.json"
PARSER_VERSION = "UNIVERSAL_VEHICLE_PARSER_V1"
STRICT_TEMPLATE_SOURCE = "target_model_strict_template"
KNOWN_BRAND_PREFIX_SOURCE = "known_brand_prefix_fallback"
MODEL_STRICT_TEMPLATE_INCOMPLETE = "MODEL_STRICT_TEMPLATE_INCOMPLETE"

DEFAULT_CONFIG: dict[str, Any] = {
    "version": PARSER_VERSION,
    "known_brands": ["雪佛兰", "本田", "斯柯达", "欧拉", "比亚迪", "五菱", "零跑"],
    "series_rules": [
        {"brand": "欧拉", "series": "黑猫", "aliases": ["黑猫", "欧拉黑猫", "ORA黑猫", "长城欧拉黑猫"]},
        {"brand": "比亚迪", "series": "海豚", "aliases": ["海豚", "比亚迪海豚", "BYD海豚"]},
        {"brand": "五菱", "series": "缤果", "aliases": ["缤果", "五菱缤果"]},
        {"brand": "零跑", "series": "T03", "aliases": ["T03", "零跑T03", "零跑 T03"]},
        {"brand": "雪佛兰", "series": "科鲁泽", "aliases": ["科鲁泽", "雪佛兰科鲁泽"]},
        {"brand": "本田", "series": "雅阁", "aliases": ["雅阁", "本田雅阁"]},
        {"brand": "斯柯达", "series": "星锐", "aliases": ["星锐", "斯柯达星锐"]},
    ],
}


@dataclass(frozen=True)
class VehicleModelParse:
    ok: bool
    brand: str = ""
    series: str = ""
    year_model: str | None = None
    config_model: str = ""
    raw_config_model: str = ""
    canonical_brand: str = ""
    canonical_series: str = ""
    canonical_year_model: str = ""
    canonical_config_model: str = ""
    config_semantic_key: str = ""
    config_semantic_version: str = CONFIG_SEMANTIC_VERSION
    vehicle_model_identity_key: str = ""
    vehicle_model_identity_key_v2: str = ""
    vehicle_model_identity_key_v2_scope: str = ""
    decision_code: str = ""
    matched_alias: str = ""
    matched_brand: str = ""
    source: str = PARSER_VERSION
    error: str | None = None
    matches: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "brand": self.brand,
            "series": self.series,
            "year_model": self.year_model,
            "config_model": self.config_model,
            "raw_config_model": self.raw_config_model,
            "canonical_brand": self.canonical_brand,
            "canonical_series": self.canonical_series,
            "canonical_year_model": self.canonical_year_model,
            "canonical_config_model": self.canonical_config_model,
            "config_semantic_key": self.config_semantic_key,
            "config_semantic_version": self.config_semantic_version,
            "vehicle_model_identity_key": self.vehicle_model_identity_key,
            "vehicle_model_identity_key_v2": self.vehicle_model_identity_key_v2,
            "vehicle_model_identity_key_v2_scope": self.vehicle_model_identity_key_v2_scope,
            "decision_code": self.decision_code,
            "matched_alias": self.matched_alias,
            "matched_brand": self.matched_brand,
            "source": self.source,
            "matches": list(self.matches),
        }
        if self.error:
            payload["error"] = self.error
        return payload


def load_vehicle_parser_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return DEFAULT_CONFIG
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        return DEFAULT_CONFIG
    return payload


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip().upper()


def normalize_model_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_canonical_config(value: str) -> str:
    normalized = normalize_model_text(value)
    normalized = re.sub(r"(?i)(\d+)\s*km", r"\1km", normalized)
    return re.sub(r"\s+", "", normalized)


def build_vehicle_model_identity_key(
    *,
    brand: str,
    series: str,
    year_model: str | None,
    config_model: str,
) -> str:
    canonical_brand = normalize_model_text(brand)
    canonical_series = normalize_model_text(series)
    canonical_year = normalize_model_text(year_model or "")
    canonical_config = normalize_canonical_config(config_model)
    return "|".join([canonical_brand, canonical_series, canonical_year, canonical_config])


def build_vehicle_model_identity_key_v2(
    *,
    brand: str,
    series: str,
    year_model: str | None,
) -> str:
    canonical_brand = normalize_model_text(brand)
    canonical_series = normalize_model_text(series)
    canonical_year = normalize_model_text(year_model or "")
    return "|".join([canonical_brand, canonical_series, canonical_year])


def extract_year_model(model_text: str) -> str | None:
    match = re.search(r"((?:19|20)\d{2})\s*款", model_text or "")
    return f"{match.group(1)}款" if match else None


def strip_year_model_prefix(model_text: str) -> str:
    match = re.search(r"(?:19|20)\d{2}\s*款", model_text or "")
    if not match:
        return normalize_model_text(model_text)
    return normalize_model_text(model_text[match.end() :])


def _known_brands(config: dict[str, Any]) -> list[str]:
    brands = [str(item).strip() for item in config.get("known_brands", []) if str(item).strip()]
    for rule in config.get("series_rules", []):
        if isinstance(rule, dict) and str(rule.get("brand") or "").strip():
            brands.append(str(rule.get("brand")).strip())
    unique = sorted(set(brands), key=lambda item: len(compact_text(item)), reverse=True)
    return unique


def _remove_compact_prefix(text: str, compact_prefix: str) -> str:
    if not compact_prefix:
        return normalize_model_text(text)
    consumed = 0
    for index, char in enumerate(text):
        if char.isspace():
            continue
        consumed += 1
        if consumed == len(compact_prefix):
            return normalize_model_text(text[index + 1 :])
    return normalize_model_text(text)


def _candidate_aliases(rule: dict[str, Any]) -> list[str]:
    brand = str(rule.get("brand") or "").strip()
    series = str(rule.get("series") or "").strip()
    candidates = [series]
    candidates.extend(str(item).strip() for item in rule.get("aliases", []) if str(item).strip())
    if brand and series:
        candidates.append(f"{brand}{series}")
        candidates.append(f"{brand} {series}")
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        compacted = compact_text(candidate)
        if compacted and compacted not in seen:
            seen.add(compacted)
            unique.append(candidate)
    return unique


def _find_matches(model_text: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    compact_model = compact_text(model_text)
    matches: list[dict[str, Any]] = []
    for rule in config.get("series_rules", []):
        if not isinstance(rule, dict):
            continue
        brand = str(rule.get("brand") or "").strip()
        series = str(rule.get("series") or "").strip()
        if not brand or not series:
            continue
        for alias in _candidate_aliases(rule):
            compact_alias = compact_text(alias)
            position = compact_model.find(compact_alias)
            if position < 0:
                continue
            matches.append(
                {
                    "brand": brand,
                    "series": series,
                    "alias": alias,
                    "position": position,
                    "length": len(compact_alias),
                }
            )
    matches.sort(key=lambda item: (item["position"], -item["length"], item["brand"], item["series"]))
    return matches


def _select_unique_match(matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not matches:
        return None
    best = matches[0]
    best_identity = (best["brand"], best["series"])
    conflicting = [
        item
        for item in matches
        if item["position"] == best["position"]
        and item["length"] == best["length"]
        and (item["brand"], item["series"]) != best_identity
    ]
    if conflicting:
        return None
    return best


def _derive_config_model(body_without_year: str, match: dict[str, Any]) -> str:
    compact_body = compact_text(body_without_year)
    compact_alias = compact_text(str(match.get("alias") or ""))
    brand_series = compact_text(f"{match.get('brand', '')}{match.get('series', '')}")
    series = compact_text(str(match.get("series") or ""))
    for prefix in (brand_series, compact_alias, series):
        if prefix and compact_body.startswith(prefix):
            return _remove_compact_prefix(body_without_year, prefix)
    return normalize_model_text(body_without_year)


def _build_success_result(
    *,
    brand: str,
    series: str,
    year_model: str | None,
    config_model: str,
    raw_config_model: str,
    decision_code: str,
    matched_alias: str,
    source: str,
    matches: tuple[dict[str, Any], ...] = (),
) -> VehicleModelParse:
    canonical_brand = normalize_model_text(brand)
    canonical_series = normalize_model_text(series)
    canonical_year = normalize_model_text(year_model or "")
    canonical_config = normalize_canonical_config(config_model)
    config_semantic = normalize_config_semantics(config_model)
    return VehicleModelParse(
        ok=True,
        brand=brand,
        series=series,
        year_model=year_model,
        config_model=config_model,
        raw_config_model=raw_config_model,
        canonical_brand=canonical_brand,
        canonical_series=canonical_series,
        canonical_year_model=canonical_year,
        canonical_config_model=canonical_config,
        config_semantic_key=config_semantic.semantic_key,
        config_semantic_version=CONFIG_SEMANTIC_VERSION,
        vehicle_model_identity_key=build_vehicle_model_identity_key(
            brand=brand,
            series=series,
            year_model=year_model,
            config_model=config_model,
        ),
        vehicle_model_identity_key_v2=build_vehicle_model_identity_key_v2(
            brand=brand,
            series=series,
            year_model=year_model,
        ),
        vehicle_model_identity_key_v2_scope="brand_series_year",
        decision_code=decision_code,
        matched_alias=matched_alias,
        matched_brand=brand,
        source=source,
        matches=matches,
    )


def _parse_strict_template_model(normalized: str, config: dict[str, Any]) -> VehicleModelParse | None:
    """Parse the Feishu standard template: brand series year trim."""
    year_match = re.search(r"((?:19|20)\d{2})\s*款", normalized)
    if not year_match:
        return None
    before_year = normalize_model_text(normalized[: year_match.start()])
    after_year = normalize_model_text(normalized[year_match.end() :])
    if not before_year or not after_year:
        return None
    year_model = f"{year_match.group(1)}款"
    tokens = before_year.split()
    if len(tokens) >= 2:
        brand = tokens[0].strip()
        series = tokens[1].strip()
        if brand and series:
            return _build_success_result(
                brand=brand,
                series=series,
                year_model=year_model,
                config_model=after_year,
                raw_config_model=after_year,
                decision_code="TARGET_MODEL_STRICT_TEMPLATE_PARSE_OK",
                matched_alias=f"{brand} {series}",
                source=STRICT_TEMPLATE_SOURCE,
            )

    compact_before = compact_text(before_year)
    for brand in _known_brands(config):
        compact_brand = compact_text(brand)
        if not compact_brand or not compact_before.startswith(compact_brand):
            continue
        series = _remove_compact_prefix(before_year, compact_brand)
        if not series:
            continue
        return _build_success_result(
            brand=brand,
            series=series,
            year_model=year_model,
            config_model=after_year,
            raw_config_model=after_year,
            decision_code="KNOWN_BRAND_PREFIX_PARSE_OK",
            matched_alias=f"{brand}{series}",
            source=KNOWN_BRAND_PREFIX_SOURCE,
        )
    return None


def _strict_template_incomplete(normalized: str, config: dict[str, Any]) -> bool:
    year_match = re.search(r"(?:19|20)\d{2}\s*款", normalized)
    if not year_match:
        return False
    before_year = normalize_model_text(normalized[: year_match.start()])
    after_year = normalize_model_text(normalized[year_match.end() :])
    if not before_year or not after_year:
        return False
    tokens = before_year.split()
    if len(tokens) != 1:
        return False
    return True


def parse_vehicle_model(
    model_text: str,
    *,
    config: dict[str, Any] | None = None,
) -> VehicleModelParse:
    normalized = normalize_model_text(model_text)
    if not normalized:
        return VehicleModelParse(ok=False, error="MODEL_TEXT_EMPTY", decision_code="MODEL_TEXT_EMPTY")

    parser_config = config or load_vehicle_parser_config()
    strict_result = _parse_strict_template_model(normalized, parser_config)
    if strict_result is not None:
        return strict_result

    matches = _find_matches(normalized, parser_config)
    selected = _select_unique_match(matches)
    if selected is None:
        if matches:
            return VehicleModelParse(
                ok=False,
                error="MODEL_BRAND_SERIES_AMBIGUOUS",
                decision_code="MODEL_BRAND_SERIES_AMBIGUOUS",
                matches=tuple(matches),
            )
        if _strict_template_incomplete(normalized, parser_config):
            return VehicleModelParse(
                ok=False,
                error=MODEL_STRICT_TEMPLATE_INCOMPLETE,
                decision_code=MODEL_STRICT_TEMPLATE_INCOMPLETE,
            )
        return VehicleModelParse(
            ok=False,
            error="MODEL_BRAND_SERIES_UNRESOLVED",
            decision_code="MODEL_BRAND_SERIES_UNRESOLVED",
        )

    year_model = extract_year_model(normalized)
    raw_config_model = strip_year_model_prefix(normalized)
    config_model = _derive_config_model(raw_config_model, selected)
    brand = str(selected["brand"])
    series = str(selected["series"])
    return _build_success_result(
        brand=brand,
        series=series,
        year_model=year_model,
        config_model=config_model,
        raw_config_model=raw_config_model,
        decision_code="VEHICLE_MODEL_PARSE_OK",
        matched_alias=str(selected["alias"]),
        source=PARSER_VERSION,
        matches=tuple(matches),
    )


def parse_vehicle_model_text(
    model_text: str,
    *,
    config: dict[str, Any] | None = None,
) -> VehicleModelParse:
    """Backward-compatible wrapper for the unified parser entry."""
    return parse_vehicle_model(model_text, config=config)
