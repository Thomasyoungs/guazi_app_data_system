"""Simplified data collection for the Guazi app."""

from __future__ import annotations

from typing import Any

from .models import CarData, ReferenceCar


class TargetCar(CarData):
    """Represents a target car for pricing."""
    
    def __init__(self, data: dict[str, Any] | None = None):
        data = data or {
            "task_id": "SIM-TARGET-001",
            "brand": "大众",
            "series": "帕萨特",
            "year": 2020,
            "mileage": 4.5,  # 万公里
            "price": 12.5,   # 万元
        }
        # 调用父类构造函数初始化属性
        super().__init__(
            task_id=data.get("task_id", ""),
            brand=data.get("brand", ""),
            series=data.get("series", ""),
            year=data.get("year"),
            mileage=data.get("mileage"),
            price=data.get("price")
        )
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "brand": self.brand,
            "series": self.series,
            "year": self.year,
            "mileage": self.mileage,
            "price": self.price,
            "features": self.features or []
        }


class DataCollector:
    """Collects data for target and reference cars."""
    
    def __init__(self, fields_config: dict[str, Any]):
        self.fields_config = fields_config
        self.target_car = TargetCar()
        self.reference_cars = [
            ReferenceCar(ref_id="REF-001"),
            ReferenceCar(ref_id="REF-002"),
            ReferenceCar(ref_id="REF-003"),
        ]
    
    def simulated_target(self) -> TargetCar:
        """Get the simulated target car."""
        return self.target_car
    
    def simulated_reference_cars(self) -> list[ReferenceCar]:
        """Get the simulated reference cars."""
        return self.reference_cars