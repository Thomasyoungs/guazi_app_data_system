"""Tests for data models."""

from guazi_core.models import DamageRecord, ReferenceCar, TargetCar


def test_target_car_to_dict():
    target = TargetCar(
        task_id="TEST-001",
        brand="大众",
        series="帕萨特",
        model_year="2020款",
        trim="2020款 帕萨特 1.5L 自动舒适版",
        color="黑",
        registration_date="2020.4",
        mileage_10k_km=5.0,
        transfer_count=0,
        condition_text="",
    )
    data = target.to_dict()
    assert data["brand"] == "大众"
    assert data["task_id"] == "TEST-001"


def test_damage_record():
    record = DamageRecord("左前门", "喷漆")
    assert record.to_dict() == {"part": "左前门", "damage_type": "喷漆"}
