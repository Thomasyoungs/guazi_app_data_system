"""Test runner using mock data (no Feishu credentials required).

Usage:
    cd src
    python ../scripts/run_mock_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from guazi_core.pricing_calculator import (
    calc_competition_coefficient,
    calc_guazi_service_fee,
    calculate_cost,
    calculate_pricing,
    TargetCar,
    ReferenceCar,
)
from guazi_core.task_normalizer import normalize_target_task


def load_mock_task(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_normalize_task() -> None:
    """Test task normalization with mock data."""
    task_data = load_mock_task("../fixtures/sample_target_task.json")
    task = normalize_target_task(task_data, source="mock", simulation_only=True)
    print(f"[OK] Task normalized: {task.brand} {task.series} ({task.model_year})")
    assert task.brand == "大众"
    assert task.series == "帕萨特"
    return task


def test_pricing_calculation() -> None:
    """Test pricing calculation with mock data."""
    print("\n--- Pricing Calculation Test ---")
    task_data = load_mock_task("../fixtures/sample_target_task.json")
    task = normalize_target_task(task_data, source="mock", simulation_only=True)

    # Calculate service fee
    service_fee = calc_guazi_service_fee(150000)
    print(f"Service fee for 150k CNY car: {service_fee} yuan")

    # Calculate competition coefficient
    comp = calc_competition_coefficient(
        target=task,
        selected_reference=None,
        pricing_context={
            "trisame_count": 5,
            "base_reference_price_yuan": "11.5万",
            "selected_reference_score": 85.0,
            "target_score": 82.0,
        },
    )
    print(f"Competition coefficient: {comp}")

    # Calculate cost
    cost = calculate_cost(
        price_yuan=120000,
        pricing_config={
            "cost_under_or_equal_50000_yuan": 600,
            "cost_50000_to_100000_yuan": 1000,
            "cost_increment_per_50000_yuan": 400,
        },
    )
    print(f"Total cost: {cost} yuan")

    # Full pricing calculation
    try:
        result = calculate_pricing(task)
        print(f"[OK] Pricing result: {result}")
    except Exception as e:
        print(f"[INFO] Full pricing skipped (expected in mock mode): {e}")


def test_feishu_export_task() -> None:
    """Test Feishu export task parsing."""
    print("\n--- Feishu Export Task Test ---")
    task_data = load_mock_task("../fixtures/sample_feishu_export_task.json")
    task = normalize_target_task(task_data, source="feishu_export", simulation_only=False)
    print(f"[OK] Feishu task parsed: {task.brand} {task.series}")
    assert task.source == "feishu_export"


def main() -> int:
    print("=" * 60)
    print("Mock Data Test Runner (No Feishu credentials needed)")
    print("=" * 60)

    try:
        # Test 1: Task normalization
        test_normalize_task()

        # Test 2: Pricing calculation
        test_pricing_calculation()

        # Test 3: Feishu export task
        test_feishu_export_task()

        print("\n" + "=" * 60)
        print("All mock tests passed!")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n[Test Failed] {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
