"""Business-field collection helpers and simulated first-run data."""

from __future__ import annotations

import re
from typing import Any

from .models import DamageRecord, ReferenceCar, TargetCar


PARTS = [
    "左前翼子板",
    "右前翼子板",
    "左后翼子板",
    "右后翼子板",
    "后翼子板",
    "左前门",
    "右前门",
    "左后门",
    "右后门",
    "前盖",
    "后盖",
    "大顶",
    "前保险杠",
    "后保险杠",
]


def parse_condition_text(condition_text: str) -> list[DamageRecord]:
    records: list[DamageRecord] = []
    chunks = re.split(r"[\s,，;；]+", condition_text or "")
    for chunk in chunks:
        if not chunk:
            continue
        part = next((item for item in PARTS if item in chunk), None)
        if not part:
            continue
        if "更换" in chunk or "换件" in chunk:
            damage = "更换"
        elif "钣金" in chunk:
            damage = "钣金"
        elif "补漆" in chunk or "喷漆" in chunk or "漆面修复" in chunk or "漆面" in chunk:
            damage = "喷漆"
        else:
            continue
        records.append(DamageRecord(part=part, damage_type=damage))
    return records


class DataCollector:
    """First runnable collector.

    Real device collection is deliberately conservative. The simulation path
    exercises the full scoring and output chain without touching external data.
    """

    def __init__(self, fields_config: dict[str, Any]) -> None:
        self.fields_config = fields_config

    def simulated_target(self) -> TargetCar:
        target = TargetCar(
            task_id="SIM-20260421-0001",
            brand="斯柯达",
            series="昕锐",
            model_year="2020款",
            trim="2020款 昕锐 1.5L 自动舒适版",
            color="金",
            registration_date="2020.4",
            mileage_10k_km=7.2,
            transfer_count=0,
            condition_text="右后门钣金喷漆 左后门钣金喷漆",
            accident_count=None,
            max_accident_amount=None,
        )
        target.panel_repairs = parse_condition_text(target.condition_text)
        return target

    def simulated_reference_cars(self) -> list[ReferenceCar]:
        return [
            ReferenceCar(
                reference_index=1,
                list_price_10k=4.88,
                list_year=2020,
                list_mileage_10k_km=8.0,
                transfer_count=1,
                accident_count=1,
                max_accident_amount=5000,
                repair_counts={"驾驶室": 1, "车尾": 0, "副驾驶": 1, "车头": 1},
                panel_repairs=[
                    DamageRecord("右后门", "喷漆"),
                    DamageRecord("左后门", "喷漆"),
                    DamageRecord("前保险杠", "喷漆"),
                ],
            ),
            ReferenceCar(
                reference_index=2,
                list_price_10k=5.2,
                list_year=2020,
                list_mileage_10k_km=6.2,
                transfer_count=0,
                accident_count=0,
                max_accident_amount="无金额记录",
                repair_counts={"驾驶室": 1, "车尾": 0, "副驾驶": 1, "车头": 0},
                panel_repairs=[
                    DamageRecord("右后门", "喷漆"),
                    DamageRecord("左后门", "喷漆"),
                ],
            ),
        ]

    def collect_whitelisted_from_text(self, state_id: str, text: str) -> dict[str, Any]:
        """Small text extractor used after UI dumps; it only returns whitelisted fields."""
        data: dict[str, Any] = {}
        if state_id in {"S08", "S10"}:
            price = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
            mileage = re.search(r"(\d+(?:\.\d+)?)\s*万公里", text)
            year = re.search(r"(20\d{2})", text)
            if price:
                data["list_price_10k"] = float(price.group(1))
            if mileage:
                data["list_mileage_10k_km"] = float(mileage.group(1))
            if year:
                data["list_year"] = int(year.group(1))
        if state_id == "S11":
            transfer = re.search(r"过户(?:次数)?\s*(\d+)", text)
            if transfer:
                data["transfer_count"] = int(transfer.group(1))
        if state_id == "S12":
            accident = re.search(r"理赔次数\s*(\d+)", text)
            amount = re.search(r"最大金额\s*(\d+(?:\.\d+)?)", text)
            if accident:
                data["accident_count"] = int(accident.group(1))
            if amount:
                data["max_accident_amount"] = float(amount.group(1))
        return data
