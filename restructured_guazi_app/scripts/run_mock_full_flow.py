"""Mock full flow test: Feishu message -> Parse -> ADB launch -> Price scrape.

Simulates the complete pipeline without real Feishu credentials.
Uses mock data to demonstrate the full chain.

Usage:
    cd src
    python ../scripts/run_mock_full_flow.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from guazi_core.app_startup import AdbClient
from guazi_core.pricing_calculator import (
    calc_competition_coefficient,
    calc_guazi_service_fee,
    calculate_cost,
    calculate_pricing,
)
from guazi_core.task_normalizer import normalize_target_task, TargetCarTask
from guazi_core.feishu_webhook import _parse_car_fields as parse_car_fields_from_text


# Mock Feishu message text (as received from webhook)
MOCK_FEISHU_MESSAGE = """品牌:大众
车系:帕萨特
年款:2020
里程:7.2万公里
颜色:白色
上牌:2020.4"""


def adb_launch_guazi_app(task: TargetCarTask) -> dict[str, Any]:
    """Launch Guazi APP via ADB and navigate to search with full conditions."""
    GUAZI_PACKAGE = "com.ganji.android.haoche_c"

    try:
        client = AdbClient()
    except Exception as exc:
        return {"success": False, "error": f"ADB client init failed: {exc}"}

    if not client.available:
        return {"success": False, "error": "ADB not available", "adb_path": str(client.adb_path) if client.adb_path else None}

    print(f"[ADB] Found device: {client.adb_serial or 'default'}")

    # Wake device
    client.run(["shell", "input", "keyevent", "224"], timeout=5)
    time.sleep(1)

    # Launch the main activity using correct activity name
    launch_cmd = [
        "shell",
        "am", "start", "-W",
        "-a", "android.intent.action.MAIN",
        "-n", f"{GUAZI_PACKAGE}/com.cars.guazi.app.home.MainActivity",
    ]
    print("[ADB] Launching Guazi APP...")
    result = client.run(launch_cmd, timeout=30)

    if not result.success:
        return {
            "success": False,
            "error": "Launch failed",
            "stderr": result.stderr[:500] if result.stderr else "",
        }

    print("[ADB] APP launched successfully")
    time.sleep(3)  # Wait for app to settle

    # Step 1: Click search bar to focus it
    if task.brand and task.series:
        print("[ADB] Clicking search bar...")
        # Search bar center coordinates (from UI dump: bounds="[138,123][412,201]")
        client.run(["shell", "input", "tap", "275", "162"], timeout=10)
        time.sleep(1)

        # Step 2: Clear existing text and enter search query
        print("[ADB] Clearing search text...")
        # Click delete button to clear (bounds="[361,147][391,177]")
        client.run(["shell", "input", "tap", "376", "162"], timeout=10)
        time.sleep(0.5)

        # Build search query with all conditions
        search_query = f"{task.brand} {task.series}"
        if task.model_year:
            search_query += f" {task.model_year}款"
        if task.color:
            search_query += f" {task.color}"

        print(f"[ADB] Entering search query: {search_query}")
        # Type the search query using ADB text input
        for char in search_query:
            # Use ADB shell input text for each character (slower but more reliable)
            client.run(["shell", "input", "text", char], timeout=5)
            time.sleep(0.1)
        time.sleep(1)

        # Step 3: Submit search (press Enter)
        print("[ADB] Submitting search...")
        client.run(["shell", "input", "keyevent", "66"], timeout=10)  # KEYCODE_ENTER
        time.sleep(2)

        # Step 4: Apply additional filters via "筛选" button if available
        if task.mileage_10k_km:
            print("[ADB] Applying mileage filter...")
            # Tap "筛选" button (bounds="[912,479][1032,599]")
            client.run(["shell", "input", "tap", "972", "539"], timeout=10)
            time.sleep(1)
            # Tap "车龄/里程" button (bounds="[639,479][862,599]")
            client.run(["shell", "input", "tap", "750", "539"], timeout=10)
            time.sleep(1)
            print(f"[ADB] Mileage filter: ~{task.mileage_10k_km} wan km (manual selection needed)")
            # Note: Exact mileage selection would require more complex UI automation
            time.sleep(1)

    return {
        "success": True,
        "adb_serial": client.adb_serial,
        "stdout": result.stdout[:200] if result.stdout else "",
    }


def mock_scrape_price(task: TargetCarTask) -> dict[str, Any]:
    """Mock price scraping (in real scenario, this would use OCR or UI dump)."""
    print(f"[MOCK] Scraping price for {task.brand} {task.series}...")

    # Simulate price range based on model_year and mileage
    import random
    random.seed(hash(task.brand + task.series + (task.model_year or "")))

    base_price = random.uniform(8.0, 15.0)  # Base price in wan
    mileage_factor = max(0.7, 1 - (task.mileage_10k_km or 0) * 0.01)
    year_factor = max(0.6, 1 - (2025 - int(task.model_year or "2020")) * 0.05)

    min_price = round(base_price * mileage_factor * year_factor * 0.9, 2)
    max_price = round(base_price * mileage_factor * year_factor * 1.1, 2)

    return {
        "source": "mock_scrape",
        "brand": task.brand,
        "series": task.series,
        "model_year": task.model_year,
        "estimated_min_price_wan": min_price,
        "estimated_max_price_wan": max_price,
        "note": "This is mock data. Real implementation would use OCR/UI dump.",
    }


def run_full_flow() -> int:
    print("=" * 70)
    print("Mock Full Flow Test: Feishu message -> Parse -> ADB -> Scrape")
    print("=" * 70)

    # Step 1: Receive Feishu message
    print("\n[Step 1] Receiving Feishu message...")
    print(f"Message (first line): {MOCK_FEISHU_MESSAGE[:50]}...")

    # Step 2: Parse message fields
    print("\n[Step 2] Parsing message fields...")
    fields = parse_car_fields_from_text(MOCK_FEISHU_MESSAGE)
    print("Parsed fields:")
    for key, value in fields.items():
        if value is not None:
            print(f"  - {key}: {value}")

    # Step 3: Build target task
    print("\n[Step 3] Building TargetCarTask...")
    task = normalize_target_task(fields, source="mock", simulation_only=True)
    print(f"Task: {task.brand} {task.series} ({task.model_year})")
    print(f"  Mileage: {task.mileage_10k_km} wan km")
    print(f"  Color: {task.color}")
    print(f"  Registration: {task.registration_date_raw}")

    # Step 4: Calculate pricing
    print("\n[Step 4] Calculating pricing...")
    service_fee = calc_guazi_service_fee(150000)
    print(f"  Service fee: {service_fee} yuan")

    cost = calculate_cost(
        price_yuan=120000,
        pricing_config={
            "cost_under_or_equal_50000_yuan": 600,
            "cost_50000_to_100000_yuan": 1000,
            "cost_increment_per_50000_yuan": 400,
        },
    )
    print(f"  Cost: {cost} yuan")

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
    print(f"  Competition coefficient: {comp.get('competition_coefficient')}")

    # Step 5: ADB launch APP
    print("\n[Step 5] Launching Guazi APP via ADB...")
    adb_result = adb_launch_guazi_app(task)
    if adb_result["success"]:
        print("[OK] ADB launch succeeded")
        print(f"  Device: {adb_result.get('adb_serial', 'N/A')}")
    else:
        print(f"[WARN] ADB launch: {adb_result.get('error')}")
        print("  (This is expected if no device is connected)")

    # Step 6: Scrape price (mock)
    print("\n[Step 6] Scraping price information...")
    price_data = mock_scrape_price(task)
    print(f"Mock price data:")
    print(f"  Min: {price_data['estimated_min_price_wan']} wan")
    print(f"  Max: {price_data['estimated_max_price_wan']} wan")

    # Summary
    print("\n" + "=" * 70)
    print("Full Flow Summary:")
    print("=" * 70)
    summary = {
        "brand": task.brand,
        "series": task.series,
        "model_year": task.model_year,
        "mileage_10k_km": task.mileage_10k_km,
        "service_fee_yuan": service_fee,
        "cost_yuan": cost,
        "competition_coefficient": comp.get("competition_coefficient"),
        "adb_launch": adb_result["success"],
        "estimated_min_price_wan": price_data["estimated_min_price_wan"],
        "estimated_max_price_wan": price_data["estimated_max_price_wan"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if adb_result["success"]:
        print("\nFull flow completed (with real ADB)")
    else:
        print("\nFull flow completed (ADB skipped - no device)")
        print("   Connect an Android device to test ADB launch")

    return 0


if __name__ == "__main__":
    sys.exit(run_full_flow())
