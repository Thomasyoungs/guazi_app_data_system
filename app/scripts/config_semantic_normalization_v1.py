"""Deterministic config semantic normalization for vehicle trim text.

This module is intentionally pure and side-effect free. It does not change
page contracts or runtime click decisions; it only produces stable semantic
keys for comparing config text that has the same meaning with small wording
differences.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

try:
    from guazi_app_data_system.trim_normalizer import normalize_emission_standard
except ImportError:  # pragma: no cover - supports direct script imports in tests.
    try:
        from src.guazi_app_data_system.trim_normalizer import normalize_emission_standard
    except ImportError:  # pragma: no cover
        normalize_emission_standard = None  # type: ignore[assignment]


ENGINE_VERSION = "FEISHU_CONFIG_SEMANTIC_NORMALIZATION_V1"

_GRADE_SUFFIX_WORDS = (
    "豪华",
    "豪享",
    "领先",
    "舒适",
    "精英",
    "尊贵",
    "尊享",
    "至尊",
    "旗舰",
    "运动",
    "时尚",
    "标准",
    "进取",
    "智享",
    "悦享",
    "臻享",
    "行政",
    "技术",
    "典雅",
    "风尚",
    "轻享",
    "长续航",
)
_GRADE_SUFFIX_PATTERN = re.compile(
    "(" + "|".join(map(re.escape, sorted(_GRADE_SUFFIX_WORDS, key=len, reverse=True))) + ")(?:型|版|款)"
)
_GRADE_TOKEN_PATTERN = re.compile(
    "|".join(map(re.escape, sorted(_GRADE_SUFFIX_WORDS, key=len, reverse=True)))
)
_TSI_PATTERN = re.compile(r"(?:280|330|380)\s*TSI", re.IGNORECASE)
_TURBO_PATTERN = re.compile(r"\d(?:\.\d)?\s*T(?!SI)", re.IGNORECASE)
_ENERGY_PATTERN = re.compile(r"PHEV|HEV|EV|DM\s*-?\s*I|增程", re.IGNORECASE)
_TRANSMISSION_PATTERN = re.compile(r"DSG|CVT|DCT|(?<![A-Z])(?:\d+)?AT(?![A-Z])", re.IGNORECASE)


@dataclass(frozen=True)
class ConfigSemanticNormalization:
    raw_text: str
    semantic_key: str
    normalized_text: str
    tokens: tuple[str, ...]
    tier_tokens: tuple[str, ...]
    powertrain_tokens: tuple[str, ...]
    engine_version: str = ENGINE_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "semantic_key": self.semantic_key,
            "normalized_text": self.normalized_text,
            "tokens": list(self.tokens),
            "tier_tokens": list(self.tier_tokens),
            "powertrain_tokens": list(self.powertrain_tokens),
            "engine_version": self.engine_version,
        }


def _normalize_emission(value: str) -> str:
    if normalize_emission_standard is None:
        return value
    return normalize_emission_standard(value)


def _strip_model_year_context(value: str) -> str:
    year_match = re.search(r"(?:19|20)\d{2}\s*款", value)
    if year_match and year_match.start() > 0 and not re.search(r"[A-Za-z0-9]", value[: year_match.start()]):
        text = value[year_match.end() :]
    else:
        text = re.sub(r"(?:19|20)\d{2}\s*款", " ", value)
    text = re.sub(r"改\s*款", " ", text)
    return text


def _normalize_spaced_grade_words(value: str) -> str:
    text = value
    for word in sorted(_GRADE_SUFFIX_WORDS, key=len, reverse=True):
        spaced_word = r"\s*".join(map(re.escape, word))
        text = re.sub(
            rf"{spaced_word}\s*(型|版|款)?",
            lambda match, tier=word: tier + (match.group(1) or ""),
            text,
        )
    return text


def _normalize_grade_suffix(value: str) -> str:
    text = _normalize_spaced_grade_words(value)
    return _GRADE_SUFFIX_PATTERN.sub(r"\1", text)


def _normalize_punctuation(value: str) -> str:
    text = value.replace("—", "-").replace("－", "-").replace("–", "-")
    text = text.replace("·", " ").replace("/", " ").replace("\\", " ")
    text = re.sub(r"[()（）\[\]【】,，;；:：]", " ", text)
    return text


def extract_trim_tier_tokens(text: str) -> set[str]:
    normalized = _normalize_grade_suffix(_normalize_punctuation(str(text or "")))
    compact = re.sub(r"\s+", "", normalized)
    return set(_GRADE_TOKEN_PATTERN.findall(compact))


def extract_powertrain_tokens(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", str(text or "").upper()).replace("DM-I", "DMI")
    tokens: set[str] = set()
    tokens.update(match.group(0).replace(" ", "").upper() for match in _TSI_PATTERN.finditer(compact))
    tokens.update(match.group(0).replace(" ", "").upper() for match in _TURBO_PATTERN.finditer(compact))
    for match in _ENERGY_PATTERN.finditer(compact):
        token = match.group(0).replace(" ", "").replace("-", "").upper()
        tokens.add("DM-I" if token == "DMI" else token)
    tokens.update(match.group(0).replace(" ", "").upper() for match in _TRANSMISSION_PATTERN.finditer(compact))
    return tokens


def _guard_mismatch_reason(
    left: ConfigSemanticNormalization,
    right: ConfigSemanticNormalization,
) -> str:
    left_tiers = set(left.tier_tokens)
    right_tiers = set(right.tier_tokens)
    if left_tiers and right_tiers and left_tiers != right_tiers:
        return "CONFIG_TIER_MISMATCH"

    left_powertrain = set(left.powertrain_tokens)
    right_powertrain = set(right.powertrain_tokens)
    if left_powertrain and right_powertrain and left_powertrain != right_powertrain:
        return "POWERTRAIN_TOKEN_MISMATCH"
    return ""


def _compact_semantic_key(value: str) -> str:
    text = value.upper()
    text = re.sub(r"(?i)(\d+)\s*KM", r"\1KM", text)
    powertrain_tokens = sorted(extract_powertrain_tokens(text))
    residual = text
    for token in sorted(powertrain_tokens, key=len, reverse=True):
        if token.endswith("TSI"):
            pattern = re.escape(token[:-3]) + r"\s*TSI"
        elif token.endswith("T") and re.match(r"\d(?:\.\d)?T$", token):
            pattern = re.escape(token[:-1]) + r"\s*T"
        elif token == "DM-I":
            pattern = r"DM\s*-?\s*I"
        else:
            pattern = re.escape(token)
        residual = re.sub(pattern, " ", residual, flags=re.IGNORECASE)
    return "".join(powertrain_tokens) + re.sub(r"\s+", "", residual)


def normalize_config_semantics(value: str) -> ConfigSemanticNormalization:
    raw = str(value or "").strip()
    normalized = _normalize_emission(raw)
    normalized = _strip_model_year_context(normalized)
    normalized = _normalize_grade_suffix(normalized)
    normalized = _normalize_punctuation(normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    semantic_key = _compact_semantic_key(normalized)
    tokens = tuple(re.findall(r"[A-Z]+|\d+(?:\.\d+)?KM?|\d+(?:\.\d+)?|[\u4e00-\u9fff]+", normalized.upper()))
    tier_tokens = tuple(sorted(extract_trim_tier_tokens(normalized)))
    powertrain_tokens = tuple(sorted(extract_powertrain_tokens(normalized)))
    return ConfigSemanticNormalization(
        raw_text=raw,
        semantic_key=semantic_key,
        normalized_text=normalized,
        tokens=tokens,
        tier_tokens=tier_tokens,
        powertrain_tokens=powertrain_tokens,
    )


def match_config_semantics(left: str, right: str) -> dict[str, Any]:
    left_normalized = normalize_config_semantics(left)
    right_normalized = normalize_config_semantics(right)
    mismatch_reason = _guard_mismatch_reason(left_normalized, right_normalized)
    if mismatch_reason:
        decision = mismatch_reason
        matched = False
    elif not left_normalized.semantic_key or not right_normalized.semantic_key:
        decision = "CONFIG_SEMANTIC_UNKNOWN"
        matched = False
    else:
        matched = left_normalized.semantic_key == right_normalized.semantic_key
        decision = "CONFIG_SEMANTIC_MATCH" if matched else "CONFIG_SEMANTIC_MISMATCH"
    return {
        "engine_version": ENGINE_VERSION,
        "decision_code": decision,
        "semantic_match": matched,
        "mismatch_reason": mismatch_reason,
        "left": left_normalized.as_dict(),
        "right": right_normalized.as_dict(),
    }
