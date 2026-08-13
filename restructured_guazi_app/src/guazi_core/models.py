"""Data models for the Guazi app data system.

This module combines the simplified models from the refactored version
with the full data models from the original system to ensure
backward compatibility and feature completeness.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Original system models (complete)
# ---------------------------------------------------------------------------

@dataclass
class DamageRecord:
    """Represents a single damage/repair record for a vehicle panel."""

    part: str
    damage_type: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class TargetCar:
    """Represents a target vehicle for pricing evaluation."""

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
    """Represents a reference (comparable) vehicle for pricing."""

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


# ---------------------------------------------------------------------------
# Refactored compatibility models
# ---------------------------------------------------------------------------

@dataclass
class CarData:
    """Base data class for car information (refactored compatibility)."""

    brand: str = ""
    series: str = ""
    year: int | None = None
    mileage: float | None = None
    price: float | None = None
    features: list[str] = None  # type: ignore[assignment]
    task_id: str = ""

    def __post_init__(self):
        if self.features is None:
            self.features = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "brand": self.brand,
            "series": self.series,
            "year": self.year,
            "mileage": self.mileage,
            "price": self.price,
            "features": self.features or [],
            "task_id": self.task_id,
        }


@dataclass
class ScoreResult:
    """Result of a scoring operation (refactored compatibility)."""

    score: float
    components: dict[str, float] = field(default_factory=dict)
    review_reasons: list[str] = field(default_factory=list)
    hard_reject: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "components": {k: round(v, 2) for k, v in self.components.items()},
            "review_reasons": self.review_reasons,
            "hard_reject": self.hard_reject,
        }


@dataclass
class ReferenceCarCompat(ReferenceCar):
    """Extended reference car with scoring (refactored compatibility)."""

    ref_id: str = ""

    def __post_init__(self):
        if self.score is None:
            self.score = 80.0

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base["ref_id"] = self.ref_id
        return base
