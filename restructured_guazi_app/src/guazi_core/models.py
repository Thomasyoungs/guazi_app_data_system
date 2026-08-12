"""Data models for the Guazi app."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set


@dataclass
class CarData:
    """Base data class for car information."""
    brand: str = ""
    series: str = ""
    year: Optional[int] = None
    mileage: Optional[float] = None
    price: Optional[float] = None
    features: List[str] = None
    task_id: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "brand": self.brand,
            "series": self.series,
            "year": self.year,
            "mileage": self.mileage,
            "price": self.price,
            "features": self.features or [],
            "task_id": self.task_id
        }


@dataclass
class ScoreResult:
    """Result of a scoring operation."""
    score: float
    max_score: float = 100.0
    min_score: float = 0.0
    review_reasons: Set[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "max_score": self.max_score,
            "min_score": self.min_score,
            "review_reasons": list(self.review_reasons) if self.review_reasons else []
        }


@dataclass
class ReferenceCar(CarData):
    """Extended car data for reference cars with scoring."""
    ref_id: str = ""
    score: ScoreResult = None
    
    def __post_init__(self):
        if self.score is None:
            self.score = ScoreResult(score=80.0)
    
    def to_dict(self) -> Dict[str, Any]:
        base_dict = super().to_dict()
        base_dict.update({
            "ref_id": self.ref_id,
            "score": self.score.to_dict() if self.score else None
        })
        return base_dict