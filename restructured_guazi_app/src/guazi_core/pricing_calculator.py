"""Pricing calculator for the Guazi app data system.

Implements the core scoring, reference selection (V3 boundary confirmation),
and pricing logic migrated from the original system.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .models import DamageRecord, ReferenceCar, ScoreResult, TargetCar


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GUAZI_SERVICE_FEE_TIERS = (
    (200000, 5000),
    (150000, 4000),
    (100000, 3500),
    (50000, 3000),
    (0, 2500),
)

SCORING_RULE_VERSION = "V1.11"
REFERENCE_SELECTION_RULE = "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
PRICING_RULE_VERSION = "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"

REPLACE_DAMAGE_TYPES = {"更换", "换件"}
METAL_DAMAGE_TYPES = {"钣金", "板金"}
PAINT_DAMAGE_TYPES = {"喷漆", "补漆", "钣金喷漆", "钣喷", "板喷", "漆面", "漆面修复", "漆面损伤"}
ABC_PARTS = {"ABC柱", "A柱", "B柱", "C柱"}
WATER_TANK_PARTS = {"水箱框架", "水箱架", "前水箱框架"}
HEADLIGHT_PARTS = {"左大灯", "右大灯", "大灯", "灯具"}
NON_SCORING_TARGET_DAMAGE_TYPES = {"凹陷", "剐蹭", "变形", "剐蹭变形", "外观瑕疵", "外观变形"}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def registration_year(value: str | int) -> int:
    if isinstance(value, int):
        return value
    match = re.search(r"(20\d{2}|19\d{2})", str(value) or "")
    if not match:
        raise ValueError(f"Cannot parse registration year from {value!r}")
    return int(match.group(1))


def normalize_damage_type(value: str) -> str:
    if value in REPLACE_DAMAGE_TYPES:
        return "更换"
    if value in METAL_DAMAGE_TYPES:
        return "钣金"
    if value in PAINT_DAMAGE_TYPES:
        return "喷漆"
    return value


# ---------------------------------------------------------------------------
# Core scoring
# ---------------------------------------------------------------------------

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
    result.review_reasons.extend(r for r in condition_review_reasons if r not in result.review_reasons)
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
    from datetime import date

    scoring = fields_config.get("scoring", {})
    review: list[str] = []
    age = max((current_year or date.today().year) - registration_year_value, 1)
    annual_mileage = mileage_10k_km / age

    deduped_repairs = dedupe_repairs(repairs, fields_config)
    base, hard_reject = _body_score(deduped_repairs, scoring)
    review.extend(_special_structure_review_reasons(deduped_repairs))
    mileage = _score_by_threshold(annual_mileage, scoring.get("mileage_score", []))
    transfer = _score_count(transfer_count, scoring.get("transfer_score", []))
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
            if damage_type in {"钣金", "喷漆"}:
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
            deduct_map = scoring.get("replace_deduct", {})
            score -= float(deduct_map.get(part, deduct_map.get("default", 3.0)))
        elif damage_type in {"喷漆", "钣金", "漆面"}:
            deduct_map = scoring.get("paint_deduct", {})
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
    return _score_count(count, scoring.get("accident_score", []))


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
        return float(next((row["score"] for row in scoring.get("amount_score", []) if row.get("value") == "none"), 0))
    numeric = float(amount)
    if numeric <= 0:
        return float(next((row["score"] for row in scoring.get("amount_score", []) if row.get("value") == "none"), 0))
    return _score_by_threshold(numeric, [row for row in scoring.get("amount_score", []) if "value" not in row])


def _part_matches(part: str, candidates: set[str]) -> bool:
    compact = re.sub(r"\s+", "", str(part or ""))
    return any(re.sub(r"\s+", "", candidate) in compact for candidate in candidates)


def dedupe_repairs(records: list[DamageRecord], fields_config: dict[str, Any]) -> list[DamageRecord]:
    priority = fields_config.get("damage_priority", {"喷漆": 1, "钣金": 2, "更换": 3})
    by_part: dict[str, DamageRecord] = {}
    for record in records:
        normalized = DamageRecord(record.part, normalize_damage_type(record.damage_type))
        current = by_part.get(record.part)
        if current is None or priority.get(normalized.damage_type, 0) > priority.get(current.damage_type, 0):
            by_part[record.part] = normalized
    return list(by_part.values())


# ---------------------------------------------------------------------------
# Target condition parsing
# ---------------------------------------------------------------------------

TARGET_ORIGINAL_PAINT_SIGNALS = ("原版原漆", "原车原漆")
TARGET_CONDITION_REPAIR_SIGNALS = ("补漆", "喷漆", "漆面修复", "漆面损伤", "漆面", "钣金", "板金", "钣喷", "板喷", "更换", "换件")
TARGET_CONDITION_NON_REPAIR_SIGNALS = (
    "划痕", "擦伤", "凹陷", "碰伤", "水痕", "发霉", "锈蚀", "地毯变色", "疑似进水", "进水痕迹"
)


def _target_condition_declares_original_paint(text: str) -> bool:
    return any(signal in text for signal in TARGET_ORIGINAL_PAINT_SIGNALS)


def _target_condition_has_modern_repair_signal(text: str) -> bool:
    return any(signal in text for signal in TARGET_CONDITION_REPAIR_SIGNALS)


def _target_condition_has_modern_non_repair_signal(text: str) -> bool:
    return any(signal in text for signal in TARGET_CONDITION_NON_REPAIR_SIGNALS)


def standardize_target_condition_repairs(condition_text: str) -> tuple[list[DamageRecord], list[str]]:
    text = str(condition_text or "").strip()
    if not text or _target_condition_declares_original_paint(text):
        return [], []

    records: list[DamageRecord] = []
    review_reasons: list[str] = []

    # Simple clause-based parsing
    for clause in re.findall(r"[^，,、。；;\r\n]+", text):
        clause = clause.strip()
        if not clause:
            continue
        parts = _extract_parts(clause)
        damages = _extract_damages(clause)
        if parts and damages:
            for part in parts:
                for damage in damages:
                    records.append(DamageRecord(part, damage))

    return records, review_reasons


def _extract_parts(text: str) -> list[str]:
    parts = ["左前翼子板", "右前翼子板", "左后翼子板", "右后翼子板", "后翼子板",
            "左前门", "右前门", "左后门", "右后门", "前盖", "后盖", "大顶",
            "前保险杠", "后保险杠"]
    return [p for p in parts if p in text]


def _extract_damages(text: str) -> list[str]:
    damages = []
    if "更换" in text or "换件" in text:
        damages.append("更换")
    if "钣金" in text:
        damages.append("钣金")
    if any(k in text for k in ("补漆", "喷漆", "漆面修复", "漆面")):
        damages.append("喷漆")
    return damages


# ---------------------------------------------------------------------------
# Reference selection (V3 Boundary)
# ---------------------------------------------------------------------------

def select_reference(
    target_score: ScoreResult,
    references: list[ReferenceCar],
    fields_config: dict[str, Any],
    current_year: int | None = None,
) -> dict[str, Any]:
    review_reasons: list[str] = []
    if len(references) < 3:
        review_reasons.append(
            fields_config.get("same_source_policy", {}).get(
                "sample_too_small_message",
                "same-source sample size is below 3; manual review recommended.",
            )
        )
    scored: list[tuple[ReferenceCar, ScoreResult]] = []
    skipped_incomplete: list[dict[str, Any]] = []

    for reference in references:
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


def _select_v3_boundary_reference(
    scored: list[tuple[ReferenceCar, ScoreResult]],
    target_value: float,
) -> dict[str, Any]:
    low_candidates: list[tuple[ReferenceCar, ScoreResult]] = []
    for reference, score in scored:
        if score.score >= target_value:
            boundary = (reference, score)
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
                    "manual_review_required": True,
                    "manual_review_reason": "FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE",
                    "auto_pricing_allowed": False,
                    "candidate_reference_pool": [_reference_pool_entry(reference, score, target_value)],
                }
            selected = low_candidates[-1]
            selected_price = round(selected[0].list_price_10k * 10000)
            return {
                "selected_tuple": selected,
                "boundary_confirmed": True,
                "boundary_reference_index": reference.reference_index,
                "boundary_reference_score": score.score,
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
                "auto_pricing_allowed": True,
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
        "manual_review_required": True,
        "manual_review_reason": "NO_VALID_REFERENCE",
        "auto_pricing_allowed": False,
        "candidate_reference_pool": [],
    }


def _reference_pool_entry(reference: ReferenceCar, score: ScoreResult, target_value: float) -> dict[str, Any]:
    return {
        "reference_index": reference.reference_index,
        "reference_score": score.score,
        "score_gap_to_target": round(target_value - score.score, 2),
        "price_yuan": round(reference.list_price_10k * 10000),
        "list_price_10k": reference.list_price_10k,
    }


# ---------------------------------------------------------------------------
# Pricing calculation
# ---------------------------------------------------------------------------

def calc_guazi_service_fee(guazi_price_yuan: int | float, pricing_config: dict[str, Any] | None = None) -> int:
    price_yuan = round(float(guazi_price_yuan))
    configured_tiers = (pricing_config or {}).get("guazi_service_fee_tiers")
    if configured_tiers:
        tiers = sorted(
            ((int(row.get("min_price_yuan", 0)), int(row["service_fee_yuan"])) for row in configured_tiers),
            key=lambda item: item[0],
            reverse=True,
        )
    else:
        tiers = GUAZI_SERVICE_FEE_TIERS
    for min_price_yuan, service_fee_yuan in tiers:
        if price_yuan >= min_price_yuan:
            return service_fee_yuan
    return GUAZI_SERVICE_FEE_TIERS[-1][1]


def calculate_pricing(
    reference: ReferenceCar | None,
    fields_config: dict[str, Any],
    pricing_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if reference is None:
        return {"status": "manual_review", "reason": "无有效参考车"}
    pricing_context = dict(pricing_context or {})
    pricing = fields_config.get("pricing", {})
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
    return_price_yuan = guazi_price_yuan - service_fee_yuan
    cost_yuan = calculate_cost(guazi_price_yuan, pricing)
    min_profit_yuan = calc_min_profit(guazi_price_yuan, pricing)
    profit_yuan = max(round(return_price_yuan * float(pricing.get("profit_rate", 0.05))), min_profit_yuan)
    other_deductions_yuan = cost_yuan + profit_yuan
    acquisition_price_yuan = return_price_yuan - other_deductions_yuan
    return {
        "status": "priced",
        "base_reference_price_yuan": base_reference_price_yuan,
        "guazi_price_yuan": guazi_price_yuan,
        "guazi_service_fee_yuan": service_fee_yuan,
        "guazi_net_payout_yuan": return_price_yuan,
        "cost_yuan": cost_yuan,
        "profit_rate": float(pricing.get("profit_rate", 0.05)),
        "profit_yuan": profit_yuan,
        "other_deductions_yuan": other_deductions_yuan,
        "suggested_acquisition_price_yuan": acquisition_price_yuan,
        "competition_coefficient": coefficient_result["competition_coefficient"],
        "manual_review_required": coefficient_result["manual_review_required"],
        "manual_review_reasons": coefficient_result["manual_review_reasons"],
    }


def calc_competition_coefficient(
    target: Any,
    selected_reference: ReferenceCar | None,
    trisame_cards: list[Any] | None = None,
    reference_history: list[Any] | None = None,
    pricing_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pricing_context = dict(pricing_context or {})
    trisame_count = _safe_int(pricing_context.get("trisame_count"))
    base_reference_price_yuan = _parse_price_yuan(pricing_context.get("base_reference_price_yuan"))
    selected_reference_score = _safe_float(pricing_context.get("selected_reference_score"))
    target_score = _safe_float(pricing_context.get("target_score"))

    adjustments: list[dict[str, Any]] = []
    manual_review_required = False
    manual_review_reasons: list[str] = []

    # Sample reliability
    if trisame_count is None:
        adjustments.append(_adjustment_record("sample_reliability_adjustment", 0, "trisame_count_missing_no_adjustment"))
    elif trisame_count <= 2:
        adjustments.append(_adjustment_record("sample_reliability_adjustment", -0.02, "sample_shortage_manual_review"))
        manual_review_required = True
        manual_review_reasons.append("SAMPLE_SHORTAGE_MANUAL_REVIEW")

    # Score gap
    if selected_reference_score is not None and target_score is not None:
        gap = selected_reference_score - target_score
        if gap <= 0:
            adjustments.append(_adjustment_record("score_gap_adjustment", 0, "no_score_gap_adjustment"))
        else:
            adjustments.append(_adjustment_record("score_gap_adjustment", 0, "manual_review_required"))

    raw_coefficient = 1.0 + sum(float(item.get("adjustment") or 0) for item in adjustments)
    clipped_coefficient = min(max(raw_coefficient, 0.90), 1.03)
    coefficient = round(clipped_coefficient, 3)

    return {
        "competition_coefficient": coefficient,
        "raw_competition_coefficient": round(raw_coefficient, 4),
        "clipped_competition_coefficient": coefficient,
        "manual_review_required": manual_review_required,
        "manual_review_reasons": list(dict.fromkeys(manual_review_reasons)),
        "round_strategy": "round_to_100",
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
        tiers = sorted(((int(row.get("min_price_yuan", 0)), int(row["min_profit_yuan"])) for row in configured_tiers if "min_profit_yuan" in row), reverse=True)
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


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int | None = None) -> int | None:
    parsed = _safe_float(value)
    return int(round(parsed)) if parsed is not None else default


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


def _reference_price_yuan(reference: ReferenceCar | None) -> int | None:
    if reference is None:
        return None
    list_price_10k = reference.list_price_10k
    if list_price_10k not in (None, "") and list_price_10k > 0:
        return round(list_price_10k * 10000)
    return None


def _adjustment_record(factor: str, adjustment: float, reason: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "factor": factor,
        "adjustment": round(float(adjustment), 4),
        "reason": reason,
        "data": data or {},
    }
