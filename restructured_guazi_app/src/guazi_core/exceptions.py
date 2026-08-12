"""Simplified exception handling for the Guazi app data system."""

from __future__ import annotations

from typing import Any


class GuaziFlowError(Exception):
    """Custom exception for flow errors in the Guazi app."""
    
    def __init__(self, code: str, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context or {}
        
    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class ValidationError(GuaziFlowError):
    """Raised when validation fails."""
    
    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__("VALIDATION_ERROR", message)
        self.field = field