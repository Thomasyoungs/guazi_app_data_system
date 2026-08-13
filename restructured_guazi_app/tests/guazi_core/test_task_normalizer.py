"""Tests for task normalizer."""

from guazi_core.task_normalizer import TargetCarTask, normalize_target_task


def test_normalize_target_task():
    raw = {
        "task_id": "FS123456",
        "brand": "大众",
        "series": "帕萨特",
        "model_year": "2020款",
        "trim": "1.5L 自动舒适版",
        "color": "黑色",
        "registration_date": "2020.04",
        "mileage_10k_km": "5.2",
        "transfer_count": "0",
        "condition_text": "原版原漆",
        "source": "feishu_export",
    }
    task = normalize_target_task(raw)
    assert task.task_id == "FS123456"
    assert task.brand == "大众"
    assert task.vehicle_year == 2020
    assert not task.simulation_only
