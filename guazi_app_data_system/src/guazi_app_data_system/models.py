"""Shared data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DamageRecord:
    part: str
    damage_type: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class TargetCar:
    task_id: str
    brand: str
    series: str
    model_year: str
    trim: str
    color: str
    registration_date: str
    mileage_10k_km: float
    transfer_count: int
    condition_text: str
    accident_count: int | None = None
    max_accident_amount: float | str | None = None
    panel_repairs: list[DamageRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["panel_repairs"] = [item.to_dict() for item in self.panel_repairs]
        return data


@dataclass
class ReferenceCar:
    reference_index: int
    list_price_10k: float
    list_year: int
    list_mileage_10k_km: float
    transfer_count: int
    accident_count: int
    max_accident_amount: float | str | None
    repair_counts: dict[str, int]
    panel_repairs: list[DamageRecord] = field(default_factory=list)
    score: float | None = None
    is_final_reference: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["panel_repairs"] = [item.to_dict() for item in self.panel_repairs]
        return data
