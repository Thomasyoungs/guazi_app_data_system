"""Tests for pricing calculator."""

from guazi_core.models import DamageRecord, ReferenceCar, TargetCar
from guazi_core.pricing_calculator import (
    calc_guazi_service_fee,
    score_target,
    select_reference,
)


def test_calc_guazi_service_fee():
    assert calc_guazi_service_fee(100000) == 3500
    assert calc_guazi_service_fee(200000) == 5000
    assert calc_guazi_service_fee(50000) == 3000


def test_score_target():
    target = TargetCar(
        task_id="TEST-001",
        brand="大众",
        series="帕萨特",
        model_year="2020款",
        trim="舒适版",
        color="黑",
        registration_date="2020.4",
        mileage_10k_km=5.0,
        transfer_count=0,
        condition_text="",
        accident_count=0,
        max_accident_amount=0,
    )
    fields_config = {
        "scoring": {
            "base_score": 70,
            "mileage_score": [{"lte": 1.5, "score": 5}, {"gt": 1.5, "score": 3}],
            "transfer_score": [{"max_count": 0, "score": 5}, {"min_count": 1, "score": 3}],
            "accident_score": [{"max_count": 0, "score": 5}, {"min_count": 1, "score": 3}],
            "amount_score": [{"value": "none", "score": 5}, {"lte": 5000, "score": 4}],
            "replace_deduct": {"default": 3.0},
            "paint_deduct": {"default": 1.0},
        }
    }
    result = score_target(target, fields_config, current_year=2026)
    assert result.score > 0
    assert "body_score" in result.components
