"""Simplified pricing calculator for the Guazi app."""

from typing import Any, List, Optional

from .models import CarData, ScoreResult, ReferenceCar


class PricingCalculator:
    """Handles pricing calculations for target and reference cars."""
    
    def __init__(self) -> None:
        """Initialize the pricing calculator."""
        pass
    
    def score_target(self, target: CarData) -> ScoreResult:
        """Calculate score for the target car."""
        # Simplified scoring logic
        score_value = 85.0  # Default score
        max_score = 100.0
        min_score = 0.0
        
        # Adjust score based on car properties
        if target.year:
            age_factor = max(0, (2026 - target.year) * -1)  # Negative impact of age
            score_value += age_factor
        
        if target.mileage:
            mileage_factor = max(-20, (target.mileage / 10000) * -0.5)  # Mileage penalty
            score_value += mileage_factor
        
        # Ensure score is within bounds
        score_value = max(min_score, min(max_score, score_value))
        
        review_reasons = []
        if score_value < 60:
            review_reasons.append("Score too low, requires manual review")
        
        return ScoreResult(
            score=score_value,
            max_score=max_score,
            min_score=min_score,
            review_reasons=set(review_reasons)
        )
    
    def select_reference(self, target_score: ScoreResult, references: List[ReferenceCar]) -> Optional[ReferenceCar]:
        """Select the best reference car based on target score."""
        if not references:
            return None
        
        # For simplicity, return the first reference car that has a similar score
        for ref in references:
            if abs(ref.score.score - target_score.score) <= 10:  # Within 10-point range
                return ref
        
        # If no close match, return the closest one
        closest_ref = references[0]
        min_diff = abs(references[0].score.score - target_score.score)
        
        for ref in references[1:]:
            diff = abs(ref.score.score - target_score.score)
            if diff < min_diff:
                min_diff = diff
                closest_ref = ref
        
        return closest_ref
    
    def calculate_pricing(self, selected_reference: Optional[ReferenceCar]) -> float:
        """Calculate pricing based on the selected reference car."""
        if selected_reference is None:
            return 0.0  # Default price if no reference
        
        # Simplified pricing calculation based on reference car price
        base_price = selected_reference.price or 80000  # Default to 80,000 if no price
        score_factor = (selected_reference.score.score / 100.0) if selected_reference.score else 1.0
        
        # Apply score factor to base price
        calculated_price = base_price * score_factor
        
        # Additional adjustments could be made here based on other factors
        
        return round(calculated_price, 2)