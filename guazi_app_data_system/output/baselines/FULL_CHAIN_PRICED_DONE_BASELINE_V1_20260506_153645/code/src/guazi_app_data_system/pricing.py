"""Scoring, reference selection and pricing calculation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .models import DamageRecord, ReferenceCar, TargetCar


REPLACE_DAMAGE_TYPES = {"更换", "换件", "鏇存崲", "鎹欢"}
METAL_DAMAGE_TYPES = {"钣金", "钣金喷漆", "閽ｉ噾"}
PAINT_DAMAGE_TYPES = {"喷漆", "漆面", "漆面损伤", "鍠锋紗", "婕嗛潰", "婕嗛潰鎹熶激"}
ABC_PARTS = {"ABC柱", "A柱", "B柱", "C柱"}
WATER_TANK_PARTS = {"水箱框架", "水箱架", "前水箱框架"}
FRONT_COVER_DEDUCT_KEYS = ["发动机舱盖", "机盖", "前机盖", "鍓嶇洊", "鍓嶆満鐩?", "鍙戝姩鏈虹洊"]
REAR_COVER_DEDUCT_KEYS = ["后盖", "后备箱盖", "尾门", "鍚庣洊", "鍚庡绠辩洊", "灏鹃棬"]


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
    if value in {"钣金", "喷漆", "漆面"}:
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


def _part_matches(part: str, candidates: set[str]) -> bool:
    compact = re.sub(r"\s+", "", str(part or ""))
    return any(re.sub(r"\s+", "", candidate) in compact for candidate in candidates)


def _deduct_value(deduct_map: dict[str, Any], keys: list[str], default: float) -> float:
    for key in keys:
        if key in deduct_map:
            return float(deduct_map[key])
    return float(deduct_map.get("default", default))


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
    return _score_common(
        mileage_10k_km=target.mileage_10k_km,
        registration_year_value=year,
        transfer_count=target.transfer_count,
        accident_count=target.accident_count,
        max_accident_amount=target.max_accident_amount,
        repairs=target.panel_repairs,
        fields_config=fields_config,
        is_target=True,
        current_year=current_year,
    )


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
    amount = _amount_score(max_accident_amount, scoring, is_target, review)
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
        if _part_matches(part, ABC_PARTS):
            if damage_type == "更换":
                hard_reject = True
                continue
            if damage_type == "钣金":
                deduct_map = scoring["metal_deduct"]
                score -= _deduct_value(deduct_map, FRONT_COVER_DEDUCT_KEYS, 3.0)
                continue
            if damage_type == "喷漆":
                deduct_map = scoring["paint_deduct"]
                score -= _deduct_value(deduct_map, FRONT_COVER_DEDUCT_KEYS, 3.0)
                continue
        if _part_matches(part, WATER_TANK_PARTS):
            if damage_type == "更换":
                deduct_map = scoring["replace_deduct"]
                score -= _deduct_value(deduct_map, REAR_COVER_DEDUCT_KEYS, 6.0)
                continue
            if damage_type in {"钣金", "喷漆"}:
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
        if _part_matches(record.part, WATER_TANK_PARTS) and damage_type in {"钣金", "喷漆"}:
            reasons.append("水箱框架钣金/喷漆扣分等同项待规则补充。")
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
        review.append("目标车或参考车事故字段缺失，需要人工审核。")
        return float(scoring.get("missing_reference_accident_score", 4))
    return _score_count(count, scoring["accident_score"])


def _amount_score(amount: float | str | None, scoring: dict[str, Any], is_target: bool, review: list[str]) -> float:
    if amount is None or amount == "":
        if is_target:
            review.append("目标车缺少最大金额，已采用默认分。")
            return float(scoring.get("missing_target_amount_score", 3))
        review.append("目标车或参考车事故字段缺失，需要人工审核。")
        return float(scoring.get("missing_reference_amount_score", 3))
    if isinstance(amount, str) and ("无" in amount or amount.lower() == "none"):
        return float(next(row["score"] for row in scoring["amount_score"] if row.get("value") == "none"))
    numeric = float(amount)
    return _score_by_threshold(numeric, [row for row in scoring["amount_score"] if "value" not in row])


def select_reference(
    target_score: ScoreResult,
    references: list[ReferenceCar],
    fields_config: dict[str, Any],
    current_year: int | None = None,
) -> dict[str, Any]:
    review_reasons: list[str] = []
    if len(references) < 3:
        review_reasons.append(fields_config.get("same_source_policy", {}).get("sample_too_small_message", "三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。"))
    scored: list[tuple[ReferenceCar, ScoreResult]] = []
    for reference in references:
        if reference.list_price_10k is None or reference.list_price_10k <= 0:
            review_reasons.append(f"参考车 {reference.reference_index} 价格无效，已跳过。")
            continue
        score = score_reference(reference, fields_config, current_year=current_year)
        reference.score = score.score
        reference.is_final_reference = False
        if score.hard_reject:
            review_reasons.extend(score.review_reasons)
            continue
        scored.append((reference, score))

    above_or_equal = [item for item in scored if item[1].score >= target_score.score]
    selected: tuple[ReferenceCar, ScoreResult] | None = None
    if above_or_equal:
        selected = min(above_or_equal, key=lambda item: (item[1].score - target_score.score, item[0].list_price_10k))
    best_below = max((item for item in scored if item[1].score < target_score.score), key=lambda item: item[1].score, default=None)
    if selected is None and best_below is not None:
        selected = best_below
        review_reasons.append("所有参考车总分均低于目标车，已选择最接近车辆作为临时参考车。")
    if selected is None:
        review_reasons.append("无有效参考车，要求人工审核。")
        return {"selected_reference": None, "selected_score": None, "review_reasons": review_reasons}

    selected[0].is_final_reference = True
    review_reasons.extend(selected[1].review_reasons)
    return {
        "selected_reference": selected[0],
        "selected_score": selected[1],
        "review_reasons": review_reasons,
    }


def calculate_pricing(reference: ReferenceCar | None, fields_config: dict[str, Any]) -> dict[str, Any]:
    if reference is None:
        return {"status": "manual_review", "reason": "无有效参考车"}
    pricing = fields_config["pricing"]
    guazi_price_yuan = round(reference.list_price_10k * 10000)
    return_price_yuan = round(guazi_price_yuan * float(pricing["guazi_return_rate"]))
    cost_yuan = calculate_cost(guazi_price_yuan, pricing)
    profit_yuan = max(round(return_price_yuan * float(pricing["profit_rate"])), int(pricing["min_profit_yuan"]))
    acquisition_price_yuan = return_price_yuan - cost_yuan - profit_yuan
    return {
        "status": "priced",
        "guazi_reference_price_yuan": guazi_price_yuan,
        "guazi_price_yuan": guazi_price_yuan,
        "guazi_return_price_yuan": return_price_yuan,
        "cost_yuan": cost_yuan,
        "profit_yuan": profit_yuan,
        "suggested_acquisition_price_yuan": acquisition_price_yuan,
    }


def calculate_cost(price_yuan: int, pricing_config: dict[str, Any]) -> int:
    if price_yuan <= 50000:
        return 1500
    if price_yuan <= 100000:
        return 2000
    extra_blocks = math.ceil((price_yuan - 100000) / 50000)
    return 2000 + extra_blocks * int(pricing_config.get("cost_increment_per_50000_yuan", 500))
