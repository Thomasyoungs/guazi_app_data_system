"""Scoring, reference selection and pricing calculation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .models import DamageRecord, ReferenceCar, TargetCar
from .reference_early_exit import reference_can_participate_in_v3_selection


GUAZI_SERVICE_FEE_TIERS = (
    (200000, 5000),
    (150000, 4000),
    (100000, 3500),
    (50000, 3000),
    (0, 2500),
)

SCORING_RULE_VERSION = "V1.11"
SCORING_RULE_DOC = "瓜子自动定价打分规则V1.11_边界前车回采确认法版.docx"
REFERENCE_SELECTION_RULE = "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
PRICING_RULE_VERSION = "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
SERVICE_FEE_RULE_VERSION = PRICING_RULE_VERSION
PRICING_RULE_DOC = "定价逻辑备份_服务费阶梯修正版_参考车选择V3边界确认法版_V3.3边界前车回采确认版 (1).docx"
SERVICE_FEE_RULE_SOURCE_FILE = PRICING_RULE_DOC
SERVICE_FEE_TIERS_FOR_TRACE = [
    {"min_price_yuan": min_price_yuan, "service_fee_yuan": service_fee_yuan}
    for min_price_yuan, service_fee_yuan in GUAZI_SERVICE_FEE_TIERS
]
COMPETITION_COEFFICIENT_VERSION = "V1.2.6"
COMPETITION_COEFFICIENT_DOC = "目标车竞争力系数算法设计_V1.2.6_边界前车回采确认适配版.docx"


REPLACE_DAMAGE_TYPES = {"更换", "换件", "鏇存崲", "鎹欢"}
METAL_DAMAGE_TYPES = {"钣金", "板金", "閽ｉ噾"}
PAINT_DAMAGE_TYPES = {"喷漆", "补漆", "钣金喷漆", "钣喷", "板喷", "漆面", "漆面修复", "漆面损伤", "鍠锋紗", "婕嗛潰", "婕嗛潰鎹熶激"}
ABC_PARTS = {"ABC柱", "A柱", "B柱", "C柱"}
WATER_TANK_PARTS = {"水箱框架", "水箱架", "前水箱框架"}
HEADLIGHT_PARTS = {"左大灯", "右大灯", "大灯", "灯具"}
NON_SCORING_TARGET_DAMAGE_TYPES = {"凹陷", "剐蹭", "变形", "剐蹭变形", "外观瑕疵", "外观变形"}
FRONT_COVER_DEDUCT_KEYS = ["发动机舱盖", "机盖", "前机盖", "鍓嶇洊", "鍓嶆満鐩?", "鍙戝姩鏈虹洊"]
REAR_COVER_DEDUCT_KEYS = ["后盖", "后备箱盖", "尾门", "鍚庣洊", "鍚庡绠辩洊", "灏鹃棬"]
TARGET_MULTI_PART_ALIASES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("左大灯", "右大灯"), ("两大灯", "两个大灯", "双大灯", "左右大灯")),
)
TARGET_PART_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("左前翼子板", ("左前翼子板", "左前叶子板", "左前叶")),
    ("右前翼子板", ("右前翼子板", "右前叶子板", "右前叶")),
    ("左后翼子板", ("左后翼子板", "左后叶子板", "左后叶")),
    ("右后翼子板", ("右后翼子板", "右后叶子板", "右后叶")),
    ("后翼子板", ("后叶子板", "后翼子板")),
    ("翼子板", ("叶子板", "翼子板")),
    ("右前门下坎", ("右前门下坎", "右前门门槛", "右前门下边梁")),
    ("左前门下坎", ("左前门下坎", "左前门门槛", "左前门下边梁")),
    ("右后门下坎", ("右后门下坎", "右后门门槛", "右后门下边梁")),
    ("左后门下坎", ("左后门下坎", "左后门门槛", "左后门下边梁")),
    ("下坎", ("下坎", "门槛", "下边梁")),
    ("左前门", ("左前门", "左前门板")),
    ("右前门", ("右前门", "右前门板")),
    ("左后门", ("左后门", "左后门板")),
    ("右后门", ("右后门", "右后门板")),
    ("前保险杠", ("前保险杠", "前杠")),
    ("后保险杠", ("后保险杠", "后杠")),
    ("前盖", ("前盖", "前机盖", "发动机舱盖", "机盖")),
    ("后盖", ("后盖", "后备箱盖", "尾门")),
    ("车顶", ("车顶", "大顶")),
    ("左大灯", ("左大灯", "左前大灯")),
    ("右大灯", ("右大灯", "右前大灯")),
    ("大灯", ("前大灯", "大灯", "灯具")),
)

TARGET_DAMAGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("更换", re.compile(r"更换|换件")),
    ("钣金", re.compile(r"钣金|板金")),
    ("喷漆", re.compile(r"钣金喷漆|钣喷|板喷|补漆|喷漆|漆面修复|漆面损伤|漆面")),
    ("剐蹭变形", re.compile(r"(剐蹭|刮蹭|划痕|擦伤).{0,4}变形|变形.{0,4}(剐蹭|刮蹭|划痕|擦伤)")),
    ("凹陷", re.compile(r"凹陷")),
    ("变形", re.compile(r"变形")),
    ("剐蹭", re.compile(r"剐蹭|刮蹭|划痕|擦伤")),
)

TARGET_CONDITION_REPAIR_SIGNALS = ("补漆", "喷漆", "漆面修复", "漆面损伤", "漆面", "钣金", "板金", "钣喷", "板喷", "更换", "换件")
TARGET_CONDITION_NON_REPAIR_SIGNALS = (
    "划痕",
    "擦伤",
    "凹陷",
    "碰伤",
    "水痕",
    "发霉",
    "锈蚀",
    "地毯变色",
    "疑似进水",
    "进水痕迹",
)
TARGET_ORIGINAL_PAINT_SIGNALS = ("原版原漆", "原车原漆")


@dataclass
class ScoreResult:
    score: float
    components: dict[str, float]
    review_reasons: list[str]
    hard_reject: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "components": {key: round(value, 2) for key, value in self.components.items()},
            "review_reasons": self.review_reasons,
            "hard_reject": self.hard_reject,
        }


def registration_year(value: str | int) -> int:
    if isinstance(value, int):
        return value
    match = re.search(r"(20\d{2}|19\d{2})", value or "")
    if not match:
        raise ValueError(f"Cannot parse registration year from {value!r}")
    return int(match.group(1))


def normalize_damage_type(value: str) -> str:
    if value in {"换件", "更换"}:
        return "更换"
    if value in {"钣金", "喷漆", "补漆", "漆面", "漆面修复"}:
        return value
    return value


def normalize_damage_type(value: str) -> str:
    if value in REPLACE_DAMAGE_TYPES:
        return "更换"
    if value in METAL_DAMAGE_TYPES:
        return "钣金"
    if value in PAINT_DAMAGE_TYPES:
        return "喷漆"
    return value


def _target_condition_declares_original_paint(text: str) -> bool:
    return any(signal in text for signal in TARGET_ORIGINAL_PAINT_SIGNALS)


def _target_condition_has_modern_repair_signal(text: str) -> bool:
    return any(signal in text for signal in TARGET_CONDITION_REPAIR_SIGNALS)


def _target_condition_has_modern_non_repair_signal(text: str) -> bool:
    return any(signal in text for signal in TARGET_CONDITION_NON_REPAIR_SIGNALS)


def _target_condition_clause_spans(text: str) -> list[tuple[int, int, str]]:
    clauses: list[tuple[int, int, str]] = []
    for match in re.finditer(r"[^，,、。；;\r\n]+", text or ""):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        start = match.start() + leading
        end = match.start() + trailing
        clause = text[start:end].strip()
        if clause:
            clauses.append((start, end, clause))
    return clauses


def _span_overlaps(span: tuple[int, int], accepted: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < accepted_end and end > accepted_start for accepted_start, accepted_end in accepted)


def _append_unique(items: list[str], item: str) -> None:
    if item not in items:
        items.append(item)


def _extract_target_condition_part_matches(text: str) -> list[dict[str, Any]]:
    hits: list[tuple[int, int, int, str, str]] = []
    for normalized_parts, aliases in TARGET_MULTI_PART_ALIASES:
        for alias in aliases:
            for match in re.finditer(re.escape(alias), text):
                for normalized in normalized_parts:
                    hits.append((match.start(), -len(alias), match.end(), normalized, alias))
    for normalized, aliases in TARGET_PART_ALIASES:
        for alias in aliases:
            for match in re.finditer(re.escape(alias), text):
                hits.append((match.start(), -len(alias), match.end(), normalized, alias))

    ordered: list[dict[str, Any]] = []
    accepted_spans: list[tuple[int, int]] = []
    seen: set[tuple[str, int, int]] = set()
    for start, _length, end, normalized, alias in sorted(hits):
        span = (start, end)
        key = (normalized, start, end)
        if key in seen:
            continue
        if _span_overlaps(span, accepted_spans) and not any(
            existing["span"] == span and existing["normalized"] != normalized for existing in ordered
        ):
            continue
        seen.add(key)
        accepted_spans.append(span)
        ordered.append({"normalized": normalized, "alias": alias, "span": span})
    return ordered


def _extract_target_condition_parts(text: str) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for match in _extract_target_condition_part_matches(text):
        normalized = str(match["normalized"])
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _extract_target_condition_damage_matches(text: str) -> list[dict[str, Any]]:
    hits: list[tuple[int, int, int, str, str]] = []
    for normalized, pattern in TARGET_DAMAGE_PATTERNS:
        for match in pattern.finditer(text):
            hits.append((match.start(), -len(match.group(0)), match.end(), normalized, match.group(0)))

    ordered: list[dict[str, Any]] = []
    accepted_spans: list[tuple[int, int]] = []
    seen: set[str] = set()
    for start, _length, end, normalized, raw in sorted(hits):
        span = (start, end)
        if normalized in seen:
            continue
        if _span_overlaps(span, accepted_spans):
            continue
        seen.add(normalized)
        accepted_spans.append(span)
        ordered.append({"normalized": normalized, "alias": raw, "span": span})
    return ordered


def _extract_target_condition_damages(text: str) -> list[str]:
    return [str(match["normalized"]) for match in _extract_target_condition_damage_matches(text)]


def _target_condition_parse_warning(part: str | None, damage: str | None) -> str | None:
    if part and _part_matches(part, HEADLIGHT_PARTS) and damage == "更换":
        return "TARGET_CONDITION_HEADLIGHT_REPLACE_RULE_REVIEW"
    if part and "下坎" in part:
        return "TARGET_CONDITION_SILL_SCORING_REVIEW"
    if damage in NON_SCORING_TARGET_DAMAGE_TYPES:
        return "TARGET_CONDITION_NON_SCORING_DAMAGE_NOTE"
    if damage and not part:
        return "TARGET_CONDITION_PART_UNCLEAR_REVIEW"
    return None


def standardize_target_condition_repairs_with_evidence(
    condition_text: str,
) -> tuple[list[DamageRecord], list[str], list[dict[str, Any]]]:
    """Parse target-condition repairs with clause-scoped binding evidence."""
    text = str(condition_text or "").strip()
    if not text or _target_condition_declares_original_paint(text):
        return [], [], []

    records: list[DamageRecord] = []
    review_reasons: list[str] = []
    evidence: list[dict[str, Any]] = []

    for clause_start, _clause_end, clause in _target_condition_clause_spans(text):
        cleaned = clause.replace("局部", "")
        parts = _extract_target_condition_part_matches(cleaned)
        damages = _extract_target_condition_damage_matches(cleaned)
        if parts and damages:
            for part_match in parts:
                normalized_part = str(part_match["normalized"])
                part_span = part_match["span"]
                for damage_match in damages:
                    normalized_damage = str(damage_match["normalized"])
                    damage_span = damage_match["span"]
                    records.append(DamageRecord(normalized_part, normalized_damage))
                    warning = _target_condition_parse_warning(normalized_part, normalized_damage)
                    if warning in {"TARGET_CONDITION_HEADLIGHT_REPLACE_RULE_REVIEW", "TARGET_CONDITION_SILL_SCORING_REVIEW"}:
                        _append_unique(review_reasons, warning)
                    source_start = clause_start + min(part_span[0], damage_span[0])
                    source_end = clause_start + max(part_span[1], damage_span[1])
                    evidence.append(
                        {
                            "source_span": [source_start, source_end],
                            "clause_text": clause,
                            "matched_part": part_match["alias"],
                            "matched_damage": damage_match["alias"],
                            "normalized_part": normalized_part,
                            "normalized_damage": normalized_damage,
                            "binding_reason": "same_clause_explicit_part_and_damage",
                            "binding_scope": "same_clause",
                            "confidence": "high" if warning is None else "partial",
                            "from_pending_binding": False,
                            "parse_warning": warning,
                        }
                    )
            continue

        if damages and not parts:
            _append_unique(review_reasons, "TARGET_CONDITION_PART_UNCLEAR_REVIEW")
            for damage_match in damages:
                damage_span = damage_match["span"]
                evidence.append(
                    {
                        "source_span": [clause_start + damage_span[0], clause_start + damage_span[1]],
                        "clause_text": clause,
                        "matched_part": None,
                        "matched_damage": damage_match["alias"],
                        "normalized_part": None,
                        "normalized_damage": damage_match["normalized"],
                        "binding_reason": "damage_without_explicit_part_not_bound",
                        "binding_scope": "same_clause_only",
                        "confidence": "low",
                        "from_pending_binding": False,
                        "parse_warning": "TARGET_CONDITION_PART_UNCLEAR_REVIEW",
                    }
                )
            continue

        for part_match in parts:
            part_span = part_match["span"]
            evidence.append(
                {
                    "source_span": [clause_start + part_span[0], clause_start + part_span[1]],
                    "clause_text": clause,
                    "matched_part": part_match["alias"],
                    "matched_damage": None,
                    "normalized_part": part_match["normalized"],
                    "normalized_damage": None,
                    "binding_reason": "part_without_scoring_damage",
                    "binding_scope": "same_clause",
                    "confidence": "partial",
                    "from_pending_binding": False,
                    "parse_warning": "TARGET_CONDITION_DAMAGE_NOT_RECOGNIZED",
                }
            )

    return records, list(dict.fromkeys(review_reasons)), evidence


def standardize_target_condition_repairs(condition_text: str) -> tuple[list[DamageRecord], list[str]]:
    """Parse explicit target-condition repairs without upgrading cosmetic paint.

    补漆/漆面修复 normalize to 喷漆 only. They never imply 钣金, 更换,
    accident, or structural risk unless the original text says so.
    """
    records, review_reasons, _evidence = standardize_target_condition_repairs_with_evidence(condition_text)
    return records, review_reasons


def _part_matches(part: str, candidates: set[str]) -> bool:
    compact = re.sub(r"\s+", "", str(part or ""))
    return any(re.sub(r"\s+", "", candidate) in compact for candidate in candidates)


def _deduct_value(deduct_map: dict[str, Any], keys: list[str], default: float) -> float:
    for key in keys:
        if key in deduct_map:
            return float(deduct_map[key])
    return float(deduct_map.get("default", default))


def _required_deduct_map(scoring: dict[str, Any], key: str) -> dict[str, Any]:
    value = scoring.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"scoring.{key} is required for special-structure damage scoring")
    return value


def dedupe_repairs(records: list[DamageRecord], fields_config: dict[str, Any]) -> list[DamageRecord]:
    priority = fields_config.get("damage_priority", {"喷漆": 1, "钣金": 2, "更换": 3})
    by_part: dict[str, DamageRecord] = {}
    for record in records:
        normalized = DamageRecord(record.part, normalize_damage_type(record.damage_type))
        current = by_part.get(record.part)
        if current is None or priority.get(normalized.damage_type, 0) > priority.get(current.damage_type, 0):
            by_part[record.part] = normalized
    return list(by_part.values())


def score_target(target: TargetCar, fields_config: dict[str, Any], current_year: int | None = None) -> ScoreResult:
    year = registration_year(target.registration_date)
    parsed_repairs, condition_review_reasons = standardize_target_condition_repairs(target.condition_text)
    condition_text = str(target.condition_text or "")
    should_use_condition_text = (
        bool(parsed_repairs)
        or bool(condition_review_reasons)
        or _target_condition_declares_original_paint(condition_text)
        or _target_condition_has_modern_repair_signal(condition_text)
        or _target_condition_has_modern_non_repair_signal(condition_text)
    )
    result = _score_common(
        mileage_10k_km=target.mileage_10k_km,
        registration_year_value=year,
        transfer_count=target.transfer_count,
        accident_count=target.accident_count,
        max_accident_amount=target.max_accident_amount,
        repairs=parsed_repairs if should_use_condition_text else target.panel_repairs,
        fields_config=fields_config,
        is_target=True,
        current_year=current_year,
    )
    result.review_reasons.extend(reason for reason in condition_review_reasons if reason not in result.review_reasons)
    return result


def score_reference(reference: ReferenceCar, fields_config: dict[str, Any], current_year: int | None = None) -> ScoreResult:
    return _score_common(
        mileage_10k_km=reference.list_mileage_10k_km,
        registration_year_value=reference.list_year,
        transfer_count=reference.transfer_count,
        accident_count=reference.accident_count,
        max_accident_amount=reference.max_accident_amount,
        repairs=reference.panel_repairs,
        fields_config=fields_config,
        is_target=False,
        current_year=current_year,
    )


def _score_common(
    mileage_10k_km: float,
    registration_year_value: int,
    transfer_count: int,
    accident_count: int | None,
    max_accident_amount: float | str | None,
    repairs: list[DamageRecord],
    fields_config: dict[str, Any],
    is_target: bool,
    current_year: int | None = None,
) -> ScoreResult:
    scoring = fields_config["scoring"]
    review: list[str] = []
    age = max((current_year or date.today().year) - registration_year_value, 1)
    annual_mileage = mileage_10k_km / age

    deduped_repairs = dedupe_repairs(repairs, fields_config)
    base, hard_reject = _body_score(deduped_repairs, scoring)
    review.extend(_special_structure_review_reasons(deduped_repairs))
    mileage = _score_by_threshold(annual_mileage, scoring["mileage_score"])
    transfer = _score_count(transfer_count, scoring["transfer_score"])
    accident = _accident_score(accident_count, scoring, is_target, review)
    amount = _amount_score(max_accident_amount, scoring, is_target, review, accident_count=accident_count)
    score = base + mileage + transfer + accident + amount
    if hard_reject:
        review.append("存在硬淘汰项，要求人工决定价。")
    return ScoreResult(
        score=score,
        components={
            "body_score": base,
            "mileage_score": mileage,
            "transfer_score": transfer,
            "accident_score": accident,
            "max_amount_score": amount,
        },
        review_reasons=review,
        hard_reject=hard_reject,
    )


def _body_score(records: list[DamageRecord], scoring: dict[str, Any]) -> tuple[float, bool]:
    score = float(scoring.get("base_score", 70))
    hard_reject = False
    for record in records:
        damage_type = normalize_damage_type(record.damage_type)
        part = record.part
        if _part_matches(part, HEADLIGHT_PARTS):
            continue
        if damage_type in NON_SCORING_TARGET_DAMAGE_TYPES:
            continue
        if _part_matches(part, ABC_PARTS):
            if damage_type == "更换":
                hard_reject = True
                continue
            if damage_type == "钣金":
                score -= float(scoring.get("abc_structure_deduct", 3.0))
                continue
            if damage_type == "喷漆":
                score -= float(scoring.get("abc_structure_deduct", 3.0))
                continue
        if _part_matches(part, WATER_TANK_PARTS):
            if damage_type == "更换":
                score -= float(scoring.get("water_tank_replace_deduct", 6.0))
                continue
            if damage_type in {"钣金", "喷漆"}:
                score -= float(scoring.get("water_tank_paint_metal_deduct", 2.0))
                continue
        if damage_type == "更换" and (part == "大顶" or "后翼子板" in part):
            hard_reject = True
        if damage_type == "更换":
            deduct_map = scoring["replace_deduct"]
            score -= float(deduct_map.get(part, deduct_map.get("default", 3.0)))
        elif damage_type in {"喷漆", "钣金", "漆面"}:
            deduct_map = scoring["paint_deduct"]
            score -= float(deduct_map.get(part, deduct_map.get("default", 1.0)))
    return max(score, 0), hard_reject


def _special_structure_review_reasons(records: list[DamageRecord]) -> list[str]:
    reasons: list[str] = []
    for record in records:
        damage_type = normalize_damage_type(record.damage_type)
        if _part_matches(record.part, ABC_PARTS) and damage_type in {"钣金", "喷漆"}:
            reasons.append("ABC柱/A柱/B柱/C柱钣金或喷漆，已按特殊结构风险件扣 3 分，建议人工复核。")
        if _part_matches(record.part, WATER_TANK_PARTS):
            if damage_type == "更换":
                reasons.append("水箱框架更换，已按特殊结构风险件扣 6 分，建议人工复核。")
            elif damage_type in {"钣金", "喷漆"}:
                reasons.append("水箱框架钣金/喷漆，已按特殊结构风险件扣 2 分，建议人工复核。")
    return reasons


def _score_by_threshold(value: float, table: list[dict[str, Any]]) -> float:
    for row in table:
        if "lte" in row and value <= float(row["lte"]):
            return float(row["score"])
        if "gt" in row and value > float(row["gt"]):
            return float(row["score"])
    return 0.0


def _score_count(count: int, table: list[dict[str, Any]]) -> float:
    for row in table:
        if "max_count" in row and count <= int(row["max_count"]):
            return float(row["score"])
        if "min_count" in row and count >= int(row["min_count"]):
            return float(row["score"])
    return 0.0


def _accident_score(count: int | None, scoring: dict[str, Any], is_target: bool, review: list[str]) -> float:
    if count is None:
        if is_target:
            review.append("目标车缺少出险次数，已采用默认分。")
            return float(scoring.get("missing_target_accident_score", 4))
        review.append("REFERENCE_ACCIDENT_COUNT_MISSING_FIELD_INCOMPLETE")
        return float(scoring.get("missing_reference_accident_score", 4))
    return _score_count(count, scoring["accident_score"])


def _amount_score(
    amount: float | str | None,
    scoring: dict[str, Any],
    is_target: bool,
    review: list[str],
    *,
    accident_count: int | None = None,
) -> float:
    if amount is None or amount == "":
        if is_target:
            review.append("目标车缺少最大金额，已采用默认分。")
            return float(scoring.get("missing_target_amount_score", 3))
        review.append("REFERENCE_MAX_AMOUNT_MISSING_FIELD_INCOMPLETE")
        return float(scoring.get("missing_reference_amount_score", 3))
    if isinstance(amount, str) and ("无" in amount or amount.lower() == "none"):
        return float(next(row["score"] for row in scoring["amount_score"] if row.get("value") == "none"))
    numeric = float(amount)
    if numeric <= 0:
        return float(next(row["score"] for row in scoring["amount_score"] if row.get("value") == "none"))
    return _score_by_threshold(numeric, [row for row in scoring["amount_score"] if "value" not in row])


def select_reference(
    target_score: ScoreResult,
    references: list[ReferenceCar],
    fields_config: dict[str, Any],
    current_year: int | None = None,
) -> dict[str, Any]:
    review_reasons: list[str] = []
    if len(references) < 3:
        review_reasons.append(
            fields_config.get(
                "same_source_policy",
                {},
            ).get(
                "sample_too_small_message",
                "same-source sample size is below 3; manual review recommended.",
            )
        )
    scored: list[tuple[ReferenceCar, ScoreResult]] = []
    skipped_incomplete: list[dict[str, Any]] = []
    for reference in references:
        runtime_flags = {
            "reference_early_exit": getattr(reference, "reference_early_exit", False),
            "excluded_from_final_reference_selection": getattr(reference, "excluded_from_final_reference_selection", False),
            "usable_for_boundary": getattr(reference, "usable_for_boundary", True),
            "usable_for_pre_boundary": getattr(reference, "usable_for_pre_boundary", True),
            "reference_score_trustworthy": getattr(reference, "reference_score_trustworthy", True),
            "reference_score_usable_for_boundary": getattr(reference, "reference_score_usable_for_boundary", True),
        }
        if not reference_can_participate_in_v3_selection(runtime_flags):
            skipped_incomplete.append({
                "reference_index": reference.reference_index,
                "reference_score": getattr(reference, "score", None),
                "excluded_reason": "LOW_SCORE_SKIPPED_INCOMPLETE"
                if runtime_flags["reference_early_exit"]
                else "reference_excluded_from_v3_selection",
            })
            continue
        if reference.list_price_10k is None or reference.list_price_10k <= 0:
            review_reasons.append(f"reference {reference.reference_index} has invalid price and was skipped.")
            continue
        score = score_reference(reference, fields_config, current_year=current_year)
        reference.score = score.score
        reference.is_final_reference = False
        if score.hard_reject:
            review_reasons.extend(score.review_reasons)
            continue
        if _score_has_reference_missing_required_field(score):
            skipped_incomplete.append({
                "reference_index": reference.reference_index,
                "reference_score": score.score,
                "excluded_reason": "reference_required_field_missing",
                "review_reasons": score.review_reasons,
            })
            continue
        scored.append((reference, score))

    v3 = _select_v3_boundary_reference(scored, target_score.score)
    selected = v3.get("selected_tuple")
    if selected is None:
        review_reasons.append(v3.get("manual_review_reason") or "NO_VALID_REFERENCE")
        return {
            "selected_reference": None,
            "selected_score": None,
            "review_reasons": review_reasons,
            "manual_review_required": True,
            "auto_pricing_allowed": False,
            "reference_selection_rule": REFERENCE_SELECTION_RULE,
            "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
            "candidate_reference_pool": [],
            "excluded_incomplete_references": skipped_incomplete,
            **{k: v for k, v in v3.items() if k != "selected_tuple"},
        }

    selected[0].is_final_reference = True
    review_reasons.extend(selected[1].review_reasons)
    review_reasons.extend(v3.get("review_reasons", []))
    return {
        "selected_reference": selected[0],
        "selected_score": selected[1],
        "review_reasons": review_reasons,
        "manual_review_required": bool(v3.get("manual_review_required")),
        "auto_pricing_allowed": not bool(v3.get("manual_review_required")),
        "reference_selection_rule": REFERENCE_SELECTION_RULE,
        "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
        "excluded_incomplete_references": skipped_incomplete,
        **{k: v for k, v in v3.items() if k != "selected_tuple"},
    }


def _score_has_reference_missing_required_field(score: ScoreResult | None) -> bool:
    if score is None:
        return True
    return any(str(reason).startswith("REFERENCE_") and "MISSING_FIELD_INCOMPLETE" in str(reason) for reason in score.review_reasons)


def _reference_pool_entry(reference: ReferenceCar, score: ScoreResult, target_value: float) -> dict[str, Any]:
    return {
        "reference_index": reference.reference_index,
        "reference_score": score.score,
        "score_gap_to_target": round(target_value - score.score, 2),
        "price_yuan": round(reference.list_price_10k * 10000),
        "list_price_10k": reference.list_price_10k,
    }


def _select_v3_boundary_reference(
    scored: list[tuple[ReferenceCar, ScoreResult]],
    target_value: float,
) -> dict[str, Any]:
    low_candidates: list[tuple[ReferenceCar, ScoreResult]] = []
    for reference, score in scored:
        if score.score >= target_value:
            boundary = (reference, score)
            boundary_entry = _reference_pool_entry(reference, score, target_value)
            final_candidate_index = reference.reference_index - 1
            if not low_candidates or final_candidate_index < 1:
                return {
                    "selected_tuple": None,
                    "boundary_confirmed": True,
                    "boundary_reference_index": reference.reference_index,
                    "boundary_reference_score": score.score,
                    "pre_boundary_reference_index": None,
                    "final_reference_candidate_index": final_candidate_index if final_candidate_index >= 1 else None,
                    "final_reference_candidate_status": "MISSING_PREVIOUS_REFERENCE",
                    "final_reference_index": None,
                    "final_reference_score": None,
                    "final_reference_price": None,
                    "boundary_reference_price_yuan": round(reference.list_price_10k * 10000),
                    "final_reference_selection_reason": "first_boundary_has_no_previous_reference",
                    "manual_review_required": True,
                    "manual_review_reason": "FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE",
                    "auto_pricing_allowed": False,
                    "candidate_reference_pool": [boundary_entry],
                }
            selected = low_candidates[-1]
            selected_price = round(selected[0].list_price_10k * 10000)
            return {
                "selected_tuple": selected,
                "boundary_confirmed": True,
                "boundary_reference_index": reference.reference_index,
                "boundary_reference_score": score.score,
                "boundary_reference_price_yuan": round(reference.list_price_10k * 10000),
                "pre_boundary_reference_index": selected[0].reference_index,
                "final_reference_candidate_index": final_candidate_index,
                "final_reference_candidate_status": "COMPLETE_TRUSTWORTHY_LOW_SCORE",
                "final_reference_recollect_required": False,
                "final_reference_recollect_done": False,
                "final_reference_index": selected[0].reference_index,
                "final_reference_score": selected[1].score,
                "final_reference_price": selected_price,
                "final_reference_price_yuan": selected_price,
                "final_reference_selection_reason": "boundary_previous_reference_complete_trustworthy",
                "manual_review_required": False,
                "manual_review_reason": None,
                "candidate_reference_pool": [_reference_pool_entry(ref, sc, target_value) for ref, sc in low_candidates + [boundary]],
            }
        low_candidates.append((reference, score))
    if low_candidates:
        return {
            "selected_tuple": None,
            "boundary_confirmed": False,
            "boundary_reference_index": None,
            "boundary_reference_score": None,
            "pre_boundary_reference_index": None,
            "final_reference_candidate_index": None,
            "final_reference_candidate_status": "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING",
            "final_reference_index": None,
            "final_reference_score": None,
            "final_reference_price": None,
            "final_reference_price_yuan": None,
            "final_reference_selection_reason": "no_boundary_reference_found_manual_review_no_auto_pricing",
            "manual_review_required": True,
            "manual_review_reason": "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING",
            "auto_pricing_allowed": False,
            "candidate_reference_pool": [_reference_pool_entry(ref, sc, target_value) for ref, sc in low_candidates],
        }
    return {
        "selected_tuple": None,
        "boundary_confirmed": False,
        "boundary_reference_index": None,
        "boundary_reference_score": None,
        "pre_boundary_reference_index": None,
        "final_reference_index": None,
        "final_reference_score": None,
        "final_reference_price": None,
        "final_reference_selection_reason": "no_valid_reference",
        "manual_review_required": True,
        "manual_review_reason": "NO_VALID_REFERENCE",
        "candidate_reference_pool": [],
    }

def calc_guazi_service_fee(guazi_price_yuan: int | float, pricing_config: dict[str, Any] | None = None) -> int:
    price_yuan = round(float(guazi_price_yuan))
    configured_tiers = (pricing_config or {}).get("guazi_service_fee_tiers")
    if configured_tiers:
        tiers = sorted(
            (
                (int(row.get("min_price_yuan", 0)), int(row["service_fee_yuan"]))
                for row in configured_tiers
            ),
            key=lambda item: item[0],
            reverse=True,
        )
    else:
        tiers = GUAZI_SERVICE_FEE_TIERS
    for min_price_yuan, service_fee_yuan in tiers:
        if price_yuan >= min_price_yuan:
            return service_fee_yuan
    return GUAZI_SERVICE_FEE_TIERS[-1][1]


def service_fee_tier_trace(guazi_price_yuan: int | float, service_fee_yuan: int | None = None) -> dict[str, Any]:
    """Return the desktop-rule service-fee contract evidence for S16 pricing."""

    price_yuan = round(float(guazi_price_yuan))
    expected_fee = calc_guazi_service_fee(price_yuan)
    actual_fee = expected_fee if service_fee_yuan is None else int(service_fee_yuan)
    matched_tier = next(
        (
            {"min_price_yuan": min_price_yuan, "service_fee_yuan": fee}
            for min_price_yuan, fee in GUAZI_SERVICE_FEE_TIERS
            if price_yuan >= min_price_yuan
        ),
        {"min_price_yuan": 0, "service_fee_yuan": GUAZI_SERVICE_FEE_TIERS[-1][1]},
    )
    return {
        "service_fee_rule_source_file": SERVICE_FEE_RULE_SOURCE_FILE,
        "service_fee_rule_version": SERVICE_FEE_RULE_VERSION,
        "service_fee_tiers": [dict(item) for item in SERVICE_FEE_TIERS_FOR_TRACE],
        "service_fee_tier_matched": matched_tier,
        "service_fee_expected_by_contract": expected_fee,
        "service_fee_actual": actual_fee,
        "service_fee_contract_match": actual_fee == expected_fee,
    }


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else default


def _safe_int(value: Any, default: int | None = None) -> int | None:
    parsed = _safe_float(value)
    return int(round(parsed)) if parsed is not None else default


def _get_value(source: Any, *keys: str, default: Any = None) -> Any:
    if source is None:
        return default
    for key in keys:
        if isinstance(source, dict) and key in source and source.get(key) is not None:
            return source.get(key)
        if hasattr(source, key):
            value = getattr(source, key)
            if value is not None:
                return value
    return default


def _parse_price_yuan(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric <= 0:
            return None
        return round(numeric * 10000) if numeric < 1000 else round(numeric)
    text = str(value).replace(",", "").strip()
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    number = float(match.group(1))
    if "万" in text or number < 1000:
        return round(number * 10000)
    return round(number)


def _reference_price_yuan(reference: ReferenceCar | dict[str, Any] | None) -> int | None:
    if reference is None:
        return None
    list_price_10k = _get_value(reference, "list_price_10k")
    if list_price_10k not in (None, ""):
        parsed = _safe_float(list_price_10k)
        if parsed and parsed > 0:
            return round(parsed * 10000)
    for key in (
        "price_yuan",
        "selected_reference_price_yuan",
        "selected_card_price_yuan",
        "guazi_price_yuan",
        "selected_card_price",
        "list_price_text",
        "price",
        "price_text",
    ):
        parsed = _parse_price_yuan(_get_value(reference, key))
        if parsed:
            return parsed
    return None


def _card_price_yuan(card: Any) -> int | None:
    for key in (
        "price_yuan",
        "selected_reference_price_yuan",
        "selected_card_price_yuan",
        "guazi_price_yuan",
        "list_price_yuan",
        "selected_card_price",
        "list_price_text",
        "price",
        "price_text",
    ):
        parsed = _parse_price_yuan(_get_value(card, key))
        if parsed:
            return parsed
    list_price_10k = _safe_float(_get_value(card, "list_price_10k"))
    if list_price_10k and list_price_10k > 0:
        return round(list_price_10k * 10000)
    return None


def _collect_trisame_prices(trisame_cards: list[Any] | None, pricing_context: dict[str, Any]) -> list[int]:
    explicit_prices = pricing_context.get("trisame_price_list_yuan")
    if isinstance(explicit_prices, list):
        prices = [_parse_price_yuan(item) for item in explicit_prices]
        return [price for price in prices if price is not None and price > 0]
    prices: list[int] = []
    for card in trisame_cards or []:
        price = _card_price_yuan(card)
        if price and price > 0:
            prices.append(price)
    return prices


def _parse_city_from_metadata(metadata: Any) -> str:
    text = str(metadata or "").strip()
    parts = [part.strip() for part in re.split(r"[|｜]", text) if part.strip()]
    return parts[-1] if parts else ""


def _reference_city(selected_reference: Any, pricing_context: dict[str, Any]) -> str:
    for key in ("selected_reference_city", "reference_city", "city"):
        value = str(pricing_context.get(key) or _get_value(selected_reference, key) or "").strip()
        if value:
            return value
    return _parse_city_from_metadata(
        pricing_context.get("selected_card_metadata")
        or _get_value(selected_reference, "selected_card_metadata", "raw_metadata")
    )


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term and term in text for term in terms)


def _count_any(text: str, terms: list[str]) -> int:
    return sum(text.count(term) for term in terms if term)


def _adjustment_record(factor: str, adjustment: float, reason: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "factor": factor,
        "adjustment": round(float(adjustment), 4),
        "reason": reason,
        "data": data or {},
    }


def _target_text(target: Any, pricing_context: dict[str, Any]) -> str:
    pieces = [
        _get_value(target, "condition_text", default=""),
        pricing_context.get("condition_text", ""),
        pricing_context.get("inspection_note", ""),
        _get_value(target, "inspection_note", default=""),
    ]
    return " ".join(str(piece or "") for piece in pieces)


def _target_descriptor(target: Any, pricing_context: dict[str, Any]) -> str:
    pieces = [
        _get_value(target, "brand", default=""),
        _get_value(target, "series", default=""),
        _get_value(target, "model_year", "year_model", default=""),
        _get_value(target, "trim", "config_model", default=""),
        pricing_context.get("selected_card_title", ""),
    ]
    return " ".join(str(piece or "") for piece in pieces)


def _target_is_ev(target: Any, pricing_context: dict[str, Any]) -> bool:
    descriptor = _target_descriptor(target, pricing_context)
    energy_values = [
        pricing_context.get("energy_type"),
        _get_value(target, "energy_type", "fuel_type", "power_type", default=""),
        _get_value(target, "range_km", default=""),
        _get_value(target, "battery_supplier", default=""),
    ]
    energy_blob = " ".join(str(value or "") for value in energy_values)
    return any(term in energy_blob or term in descriptor for term in ("纯电", "新能源", "EV", "电动"))


def _sample_reliability_adjustment(trisame_count: int | None) -> tuple[dict[str, Any], bool, list[str], list[str]]:
    if trisame_count is None:
        return (
            _adjustment_record("sample_reliability_adjustment", 0, "trisame_count_missing_no_adjustment", {"trisame_count": None}),
            False,
            [],
            ["TRISAME_COUNT_MISSING_NOTE"],
        )
    if trisame_count <= 0:
        return (
            _adjustment_record("sample_reliability_adjustment", 0, "no_trisame_source_requires_manual_pricing", {"trisame_count": trisame_count}),
            True,
            ["NO_TRISAME_SOURCE_MANUAL_PRICING"],
            [],
        )
    if trisame_count <= 2:
        return (
            _adjustment_record("sample_reliability_adjustment", -0.02, "sample_shortage_manual_review", {"trisame_count": trisame_count}),
            True,
            ["SAMPLE_SHORTAGE_MANUAL_REVIEW"],
            [],
        )
    if trisame_count <= 4:
        return (
            _adjustment_record("sample_reliability_adjustment", -0.01, "sample_slightly_small", {"trisame_count": trisame_count}),
            False,
            [],
            ["SAMPLE_SLIGHTLY_SMALL_NOTE"],
        )
    return (
        _adjustment_record("sample_reliability_adjustment", 0, "sample_sufficient", {"trisame_count": trisame_count}),
        False,
        [],
        [],
    )


def _price_distribution_adjustment(price_list_yuan: list[int], trisame_count: int | None) -> tuple[dict[str, Any], bool, list[str]]:
    prices = [int(price) for price in price_list_yuan if price and price > 0]
    if len(prices) <= 1:
        return (
            _adjustment_record(
                "price_distribution_adjustment",
                0,
                "single_or_missing_trisame_price_list_no_adjustment",
                {"price_list_yuan": prices, "price_spread_rate": None},
            ),
            False,
            [],
        )
    min_price = min(prices)
    max_price = max(prices)
    spread_rate = (max_price - min_price) / min_price if min_price > 0 else None
    if trisame_count is not None and trisame_count <= 2:
        return (
            _adjustment_record(
                "price_distribution_adjustment",
                0,
                "sample_shortage_price_distribution_record_only_no_duplicate_deduction",
                {"price_list_yuan": prices, "price_spread_rate": round(spread_rate, 4) if spread_rate is not None else None},
            ),
            False,
            [],
        )
    if spread_rate is None or spread_rate <= 0.05:
        adjustment = 0
        reason = "price_distribution_stable"
        manual = False
    elif spread_rate <= 0.10:
        adjustment = -0.005
        reason = "price_distribution_slightly_discrete"
        manual = False
    elif spread_rate <= 0.20:
        adjustment = -0.01
        reason = "price_distribution_discrete"
        manual = False
    else:
        adjustment = -0.02
        reason = "price_distribution_highly_discrete_manual_review"
        manual = True
    return (
        _adjustment_record(
            "price_distribution_adjustment",
            adjustment,
            reason,
            {"price_list_yuan": prices, "price_spread_rate": round(spread_rate, 4) if spread_rate is not None else None},
        ),
        manual,
        ["PRICE_DISTRIBUTION_MANUAL_REVIEW"] if manual else [],
    )


def _classify_water_risk(text: str) -> tuple[float, str, list[str], bool]:
    confirmed_terms = ["水泡", "泡水", "确认进水", "已进水"]
    if _contains_any(text, confirmed_terms):
        hits = [term for term in confirmed_terms if term in text]
        return -0.05, "confirmed_water_damage_manual_pricing", hits, True

    evidence_groups = {
        "water_trace": ["进水", "水痕", "进水痕迹", "姘寸棔", "杩涙按"],
        "mildew": ["发霉", "霉", "地板发霉", "鍙戦湁", "鍦版澘鍙戦湁"],
        "rust": ["锈蚀", "管柱锈蚀", "閿堣殌", "绠℃煴"],
        "carpet_color": ["地毯变色", "副驾驶地毯变色", "鍦版鍙樿壊"],
        "possible": ["疑似进水", "可能会出进水痕迹", "可能进水", "濮樺妫"],
    }
    categories = [name for name, terms in evidence_groups.items() if _contains_any(text, terms)]
    hits = [term for terms in evidence_groups.values() for term in terms if term in text]
    if not categories:
        return 0, "no_water_ingress_risk", [], False
    if len(categories) == 1:
        return -0.02, "light_water_ingress_risk_single_evidence", hits, False
    if len(categories) == 2:
        return -0.03, "medium_water_ingress_risk_grouped_no_keyword_stack", hits, False
    return -0.04, "obvious_water_ingress_risk_grouped_no_keyword_stack", hits, False


def _target_condition_risk_adjustment(target: Any, pricing_context: dict[str, Any]) -> tuple[dict[str, Any], bool, list[str], list[str], bool]:
    text = _target_text(target, pricing_context)
    risk_flags: list[str] = []
    manual_reasons: list[str] = []
    notes: list[str] = []
    strong_risk = False
    if re.search(r"OBD|未读\s*OBD|未读取\s*OBD|鏈\s*OBD", text, re.IGNORECASE):
        notes.append("OBD_NOT_READ_NOTE")

    water_adjustment, water_reason, water_hits, confirmed_water_damage = _classify_water_risk(text)
    if water_hits:
        risk_flags.extend(water_hits)
        manual_reasons.append("TARGET_WATER_INGRESS_RISK_REVIEW")
        strong_risk = True
        if confirmed_water_damage:
            manual_reasons.append("TARGET_CONFIRMED_WATER_DAMAGE_MANUAL_PRICING")
        return (
            _adjustment_record(
                "target_condition_risk_adjustment",
                water_adjustment,
                f"{water_reason}; obd_note_only_if_present",
                {
                    "risk_flags": risk_flags,
                    "obd_not_read_note": "OBD_NOT_READ_NOTE" in notes,
                    "water_risk_grouped": True,
                    "confirmed_water_damage": confirmed_water_damage,
                },
            ),
            True,
            manual_reasons,
            notes,
            strong_risk,
        )

    structural_terms = ["ABC柱", "A柱", "B柱", "C柱", "结构", "重大事故", "瀛樺湪纭"]
    if _contains_any(text, structural_terms):
        risk_flags.append("STRUCTURAL_OR_MAJOR_ACCIDENT_RISK")
        manual_reasons.append("TARGET_STRUCTURAL_RISK_MANUAL_REVIEW")
        strong_risk = True
        return (
            _adjustment_record("target_condition_risk_adjustment", -0.05, "structural_or_major_accident_risk_manual_review", {"risk_flags": risk_flags}),
            True,
            manual_reasons,
            notes,
            strong_risk,
        )

    left_sill_terms = ["左侧下坎", "左下坎", "涓嬪潕"]
    localized_metal_terms = ["局部钣金", "灞€閮ㄩ挘閲"]
    metal_terms = ["钣金", "閽ｉ噾", "鈑金"]
    replace_terms = ["更换", "换件", "鏇存崲", "鎹"]
    if _contains_any(text, replace_terms):
        risk_flags.append("REPLACE_OR_PART_CHANGE")
        return (
            _adjustment_record("target_condition_risk_adjustment", -0.02, "replacement_or_part_change_competitiveness_risk", {"risk_flags": risk_flags}),
            False,
            [],
            notes,
            False,
        )
    if _contains_any(text, localized_metal_terms) and not _contains_any(text, left_sill_terms):
        risk_flags.append("LOCALIZED_SHEET_METAL")
        return (
            _adjustment_record("target_condition_risk_adjustment", -0.01, "localized_sheet_metal_competitiveness_risk", {"risk_flags": risk_flags}),
            False,
            [],
            notes,
            False,
        )
    metal_count = _count_any(text, metal_terms)
    if metal_count >= 2 and not _contains_any(text, left_sill_terms):
        risk_flags.append("MULTIPLE_SHEET_METAL")
        return (
            _adjustment_record("target_condition_risk_adjustment", -0.02, "multiple_sheet_metal_competitiveness_risk", {"risk_flags": risk_flags, "metal_count": metal_count}),
            False,
            [],
            notes,
            False,
        )

    paint_count = _count_any(text, ["喷漆", "补漆", "漆面修复", "鍠锋紗"])
    minor_terms = ["划痕", "擦伤", "凹陷", "碰伤", "鍒掔棔", "鎿︿激", "鍑归櫡"]
    minor_hits = [term for term in minor_terms if term in text]
    if paint_count >= 3:
        risk_flags.append("MULTIPLE_PAINT_RECORDED")
        notes.append("MULTIPLE_PAINT_RECORDED_NO_EXTRA_COEFFICIENT_DEDUCTION")
    if minor_hits:
        risk_flags.extend(minor_hits)
        notes.append("MINOR_DAMAGE_RECORDED_NO_EXTRA_COEFFICIENT_DEDUCTION")
    if _contains_any(text, left_sill_terms):
        notes.append("TARGET_CONDITION_LEFT_SILL_SCORING_REVIEW")
    return (
        _adjustment_record("target_condition_risk_adjustment", 0, "no_strong_condition_risk_for_coefficient", {"risk_flags": risk_flags, "paint_count": paint_count}),
        False,
        [],
        notes,
        False,
    )


def _score_gap_adjustment(selected_reference_score: float | None, target_score: float | None, *, strong_risk: bool = False) -> dict[str, Any]:
    if selected_reference_score is None or target_score is None:
        return _adjustment_record("score_gap_adjustment", 0, "score_gap_missing_no_adjustment", {"score_gap": None})
    gap = selected_reference_score - target_score
    if gap <= 0:
        return _adjustment_record(
            "score_gap_adjustment",
            0,
            "final_reference_selected_by_v3_boundary_confirmation_no_score_gap_adjustment",
            {
                "score_gap": round(gap, 2),
                "score_gap_note": "final_reference_score <= target_score under V3 boundary confirmation; score_gap_adjustment=0",
            },
        )
    return _adjustment_record(
        "score_gap_adjustment",
        0,
        "selected_reference_score_above_target_invalid_for_v3_manual_review_required",
        {
            "score_gap": round(gap, 2),
            "score_gap_note": "V3 automatic pricing should not price with final_reference_score > target_score; return to S15 boundary confirmation or manual review.",
        },
    )


def _price_band_adjustment(price_yuan: int | None) -> dict[str, Any]:
    if price_yuan is None:
        return _adjustment_record("price_band_adjustment", 0, "price_missing_no_adjustment", {"price_yuan": None})
    if price_yuan < 100000:
        adjustment = 0
        reason = "price_band_below_100000"
    elif price_yuan < 150000:
        adjustment = -0.01
        reason = "price_band_100000_to_150000"
    elif price_yuan < 200000:
        adjustment = -0.02
        reason = "price_band_150000_to_200000"
    else:
        adjustment = -0.03
        reason = "price_band_ge_200000"
    return _adjustment_record("price_band_adjustment", adjustment, reason, {"price_yuan": price_yuan})


def _model_liquidity_and_fuel_adjustments(target: Any, pricing_context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    descriptor = _target_descriptor(target, pricing_context)
    if _target_is_ev(target, pricing_context):
        return (
            _adjustment_record("model_liquidity_adjustment", 0, "ev_model_liquidity_profile_missing_no_adjustment", {"profile": None, "energy_type": "EV"}),
            _adjustment_record("fuel_cost_pressure_adjustment", 0, "ev_fuel_cost_not_applicable_v1_2_1", {"energy_type": "EV"}),
        )
    if "途昂" in descriptor or "閫旀槀" in descriptor:
        return (
            _adjustment_record("model_liquidity_adjustment", 0, "large_suv_pressure_counted_in_fuel_cost_without_double_deduction", {"profile": "large_suv"}),
            _adjustment_record("fuel_cost_pressure_adjustment", -0.02, "large_suv_330tsi_high_holding_and_fuel_cost_pressure", {"vehicle_class": "large_suv", "power_keyword": "330TSI"}),
        )
    if "普拉多" in descriptor or "Prado" in descriptor or "2.7L" in descriptor:
        return (
            _adjustment_record("model_liquidity_adjustment", 0, "hard_suv_pressure_counted_in_fuel_cost_without_double_deduction", {"profile": "hard_suv"}),
            _adjustment_record("fuel_cost_pressure_adjustment", -0.02, "hard_suv_2_7l_high_holding_and_fuel_cost_pressure", {"vehicle_class": "hard_suv", "power_keyword": "2.7L"}),
        )
    if any(term in descriptor for term in ("YARiS", "致炫", "鑷寸偒")):
        return (
            _adjustment_record("model_liquidity_adjustment", 0, "small_joint_venture_car_normal_liquidity", {"profile": "small_car"}),
            _adjustment_record("fuel_cost_pressure_adjustment", 0, "small_car_low_fuel_pressure", {"profile": "fuel_cost_pressure=low"}),
        )
    if any(term in descriptor for term in ("桑塔纳", "妗戝")):
        return (
            _adjustment_record("model_liquidity_adjustment", 0, "compact_sedan_normal_liquidity", {"profile": "compact_sedan"}),
            _adjustment_record("fuel_cost_pressure_adjustment", 0, "compact_sedan_normal_fuel_pressure", {"profile": "fuel_cost_pressure=normal"}),
        )
    return (
        _adjustment_record("model_liquidity_adjustment", 0, "no_stable_model_profile_no_adjustment", {"profile": None}),
        _adjustment_record("fuel_cost_pressure_adjustment", 0, "no_stable_fuel_pressure_profile_no_adjustment", {"profile": None}),
    )


def _province_for_city(city: str) -> str:
    city = str(city or "").strip()
    hebei = {"唐山", "鍞愬北", "石家庄", "秦皇岛", "廊坊", "保定", "邯郸", "邢台", "沧州", "承德", "张家口", "衡水"}
    chongqing = {"重庆", "閲嶅簡"}
    heilongjiang = {"齐齐哈尔", "榻愰綈鍝堝皵"}
    if city in hebei:
        return "hebei"
    if city in chongqing:
        return "chongqing"
    if city in heilongjiang:
        return "heilongjiang"
    return city


def _city_comparability_adjustment(target: Any, selected_reference: Any, pricing_context: dict[str, Any]) -> dict[str, Any]:
    license_city = str(pricing_context.get("license_city") or _get_value(target, "license_city", "plate_location", default="") or "").strip()
    reference_city = _reference_city(selected_reference, pricing_context)
    if not license_city or not reference_city:
        return _adjustment_record("city_comparability_adjustment", 0, "city_missing_no_adjustment", {"license_city": license_city, "reference_city": reference_city})
    if license_city == reference_city:
        return _adjustment_record("city_comparability_adjustment", 0, "same_city_record_only_v1_2_1", {"license_city": license_city, "reference_city": reference_city})
    if _province_for_city(license_city) == _province_for_city(reference_city):
        return _adjustment_record("city_comparability_adjustment", 0, "same_province_different_city_record_only_v1_2_1", {"license_city": license_city, "reference_city": reference_city})
    return _adjustment_record("city_comparability_adjustment", 0, "cross_province_record_only_v1_2_1", {"license_city": license_city, "reference_city": reference_city})


def _cap_price_pressure_adjustments(records: list[dict[str, Any]], cap: float = -0.02) -> list[dict[str, Any]]:
    total = sum(float(record.get("adjustment") or 0) for record in records)
    if total >= cap:
        return records
    overflow = cap - total
    capped: list[dict[str, Any]] = [dict(record) for record in records]
    for record in reversed(capped):
        adjustment = float(record.get("adjustment") or 0)
        if adjustment < 0:
            record["adjustment"] = round(adjustment + overflow, 4)
            record["reason"] = f"{record.get('reason')}_LIQUIDITY_PRESSURE_CAP_APPLIED"
            record.setdefault("data", {})["v1_2_1_combined_pressure_cap"] = cap
            record.setdefault("data", {})["cap_reason"] = "LIQUIDITY_PRESSURE_CAP_APPLIED"
            break
    return capped


def calc_competition_coefficient(
    target: TargetCar | dict[str, Any] | None,
    selected_reference: ReferenceCar | dict[str, Any] | None,
    trisame_cards: list[Any] | None = None,
    reference_history: list[Any] | None = None,
    pricing_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pricing_context = dict(pricing_context or {})
    target = target if target is not None else pricing_context.get("target")
    selected_reference = selected_reference if selected_reference is not None else pricing_context.get("selected_reference")
    trisame_cards = list(trisame_cards or pricing_context.get("trisame_cards") or [])
    reference_history = list(reference_history or pricing_context.get("reference_history") or [])

    price_list_yuan = _collect_trisame_prices(trisame_cards, pricing_context)
    trisame_count = _safe_int(
        pricing_context.get("trisame_count")
        or pricing_context.get("trisame_cards_count")
        or (len(trisame_cards) if trisame_cards else None)
        or (len(price_list_yuan) if price_list_yuan else None)
    )
    base_reference_price_yuan = _parse_price_yuan(pricing_context.get("base_reference_price_yuan")) or _reference_price_yuan(selected_reference)
    selected_reference_score = _safe_float(pricing_context.get("selected_reference_score") or pricing_context.get("reference_score") or _get_value(selected_reference, "score"))
    target_score = _safe_float(pricing_context.get("target_score") or _get_value(target, "score"))

    adjustments: list[dict[str, Any]] = []
    manual_review_required = False
    manual_review_reasons: list[str] = []
    notes: list[str] = []

    sample_record, sample_manual, sample_reasons, sample_notes = _sample_reliability_adjustment(trisame_count)
    adjustments.append(sample_record)
    manual_review_required = manual_review_required or sample_manual
    manual_review_reasons.extend(sample_reasons)
    notes.extend(sample_notes)

    price_record, price_manual, price_reasons = _price_distribution_adjustment(price_list_yuan, trisame_count)
    adjustments.append(price_record)
    manual_review_required = manual_review_required or price_manual
    manual_review_reasons.extend(price_reasons)

    condition_record, condition_manual, condition_reasons, condition_notes, strong_risk = _target_condition_risk_adjustment(target, pricing_context)
    adjustments.append(condition_record)
    manual_review_required = manual_review_required or condition_manual
    manual_review_reasons.extend(condition_reasons)
    notes.extend(condition_notes)

    adjustments.append(_score_gap_adjustment(selected_reference_score, target_score, strong_risk=strong_risk))
    price_pressure_records = [_price_band_adjustment(base_reference_price_yuan)]
    model_record, fuel_record = _model_liquidity_and_fuel_adjustments(target, pricing_context)
    if str(fuel_record.get("reason") or "").startswith("ev_fuel_cost_not_applicable"):
        notes.append("EV_FUEL_COST_NOT_APPLICABLE_NOTE")
    price_pressure_records.extend([model_record, fuel_record])
    adjustments.extend(_cap_price_pressure_adjustments(price_pressure_records))
    adjustments.append(_city_comparability_adjustment(target, selected_reference, pricing_context))

    raw_coefficient = 1.0 + sum(float(item.get("adjustment") or 0) for item in adjustments)
    clipped_coefficient = min(max(raw_coefficient, 0.90), 1.03)
    coefficient = round(clipped_coefficient, 3)
    reasons = [
        {
            "factor": item.get("factor"),
            "adjustment": float(item.get("adjustment") or 0),
            "reason": item.get("reason"),
            "data_source": item.get("data") or {},
        }
        for item in adjustments
        if abs(float(item.get("adjustment") or 0)) > 0
    ]
    if not reasons:
        reasons.append(
            {
                "factor": "competition_coefficient",
                "adjustment": 0.0,
                "reason": "competition_coefficient_no_adjustment",
                "data_source": {},
            }
        )

    return {
        "competition_coefficient": coefficient,
        "competition_coefficient_version": COMPETITION_COEFFICIENT_VERSION,
        "competition_coefficient_doc": COMPETITION_COEFFICIENT_DOC,
        "raw_competition_coefficient": round(raw_coefficient, 4),
        "clipped_competition_coefficient": coefficient,
        "competition_coefficient_reasons": reasons,
        "competition_adjustments": adjustments,
        "manual_review_required": manual_review_required,
        "manual_review_reasons": list(dict.fromkeys(manual_review_reasons)),
        "notes": list(dict.fromkeys(notes)),
        "round_strategy": "strong_risk_floor_to_100" if strong_risk else "round_to_100",
        "trisame_count": trisame_count,
        "trisame_price_list_yuan": price_list_yuan,
        "base_reference_price_yuan": base_reference_price_yuan,
        "selected_reference_score": selected_reference_score,
        "target_score": target_score,
        "score_gap": round(selected_reference_score - target_score, 2) if selected_reference_score is not None and target_score is not None else None,
        "non_trisame_prices_used": False,
        "ai_model_used": False,
    }


def calculate_pricing(
    reference: ReferenceCar | None,
    fields_config: dict[str, Any],
    pricing_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if reference is None:
        return {"status": "manual_review", "reason": "无有效参考车"}
    pricing_context = dict(pricing_context or {})
    pricing = fields_config["pricing"]
    base_reference_price_yuan = _reference_price_yuan(reference)
    if base_reference_price_yuan is None:
        return {"status": "manual_review", "reason": "reference_price_missing"}
    coefficient_result = calc_competition_coefficient(
        pricing_context.get("target"),
        reference,
        pricing_context.get("trisame_cards"),
        pricing_context.get("reference_history"),
        pricing_context,
    )
    raw_listing_price_yuan = base_reference_price_yuan * float(coefficient_result["competition_coefficient"])
    if coefficient_result.get("round_strategy") == "strong_risk_floor_to_100":
        guazi_price_yuan = int(math.floor(raw_listing_price_yuan / 100.0) * 100)
    else:
        guazi_price_yuan = int(round(raw_listing_price_yuan / 100.0) * 100)
    service_fee_yuan = calc_guazi_service_fee(guazi_price_yuan, pricing)
    service_fee_contract = service_fee_tier_trace(guazi_price_yuan, service_fee_yuan)
    return_price_yuan = guazi_price_yuan - service_fee_yuan
    same_other_deductions_yuan = _safe_int(pricing_context.get("same_other_deductions_yuan"))
    cost_yuan = calculate_cost(guazi_price_yuan, pricing)
    min_profit_yuan = calc_min_profit(guazi_price_yuan, pricing)
    profit_yuan = max(round(return_price_yuan * float(pricing["profit_rate"])), min_profit_yuan)
    other_deductions_yuan = same_other_deductions_yuan if same_other_deductions_yuan is not None else cost_yuan + profit_yuan
    acquisition_price_yuan = return_price_yuan - other_deductions_yuan
    return {
        "status": "priced",
        "base_reference_price_yuan": base_reference_price_yuan,
        "guazi_reference_price_yuan": base_reference_price_yuan,
        "target_guazi_listing_price_yuan": guazi_price_yuan,
        "guazi_price_yuan": guazi_price_yuan,
        "guazi_service_fee_yuan": service_fee_yuan,
        **service_fee_contract,
        "pricing_rule_version": pricing.get("pricing_rule_version", PRICING_RULE_VERSION),
        "pricing_rule_doc": pricing.get("pricing_rule_doc", PRICING_RULE_DOC),
        "guazi_net_payout_yuan": return_price_yuan,
        "guazi_return_price_yuan": return_price_yuan,
        "cost_yuan": cost_yuan,
        "profit_rate": float(pricing["profit_rate"]),
        "profit_yuan": profit_yuan,
        "other_deductions_yuan": other_deductions_yuan,
        "same_other_deductions_yuan_used": same_other_deductions_yuan is not None,
        "suggested_acquisition_price_yuan": acquisition_price_yuan,
        "suggested_purchase_price_yuan": acquisition_price_yuan,
        "competition_coefficient": coefficient_result["competition_coefficient"],
        "competition_coefficient_version": coefficient_result["competition_coefficient_version"],
        "competition_coefficient_doc": coefficient_result["competition_coefficient_doc"],
        "raw_competition_coefficient": coefficient_result["raw_competition_coefficient"],
        "clipped_competition_coefficient": coefficient_result["clipped_competition_coefficient"],
        "competition_coefficient_reasons": coefficient_result["competition_coefficient_reasons"],
        "competition_adjustments": coefficient_result["competition_adjustments"],
        "manual_review_required": coefficient_result["manual_review_required"],
        "manual_review_reasons": coefficient_result["manual_review_reasons"],
        "notes": coefficient_result["notes"],
        "round_strategy": coefficient_result["round_strategy"],
        "trisame_count": coefficient_result["trisame_count"],
        "trisame_price_list_yuan": coefficient_result["trisame_price_list_yuan"],
        "non_trisame_prices_used": False,
        "ai_model_used": False,
    }


def calculate_cost(price_yuan: int, pricing_config: dict[str, Any]) -> int:
    increment = int(pricing_config.get("cost_increment_per_50000_yuan", 400))
    if price_yuan <= 50000:
        return int(pricing_config.get("cost_under_or_equal_50000_yuan", 600))
    if price_yuan <= 100000:
        return int(pricing_config.get("cost_50000_to_100000_yuan", 1000))
    extra_blocks = math.ceil((price_yuan - 100000) / 50000)
    return int(pricing_config.get("cost_50000_to_100000_yuan", 1000)) + extra_blocks * increment


def calc_min_profit(price_yuan: int | float, pricing_config: dict[str, Any] | None = None) -> int:
    price = round(float(price_yuan))
    configured_tiers = (pricing_config or {}).get("min_profit_tiers")
    if configured_tiers:
        tiers = sorted(
            (
                (int(row.get("min_price_yuan", 0)), int(row["min_profit_yuan"]))
                for row in configured_tiers
                if "min_profit_yuan" in row
            ),
            reverse=True,
        )
        for min_price, min_profit in tiers:
            if price >= min_price:
                return min_profit
    if price < 50000:
        return 2500
    if price < 100000:
        return 4500
    if price < 150000:
        return 6500
    if price < 200000:
        return 10000
    return int((pricing_config or {}).get("min_profit_yuan", 10000))
