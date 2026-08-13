"""Exception handling for the Guazi app data system.

Migrated and extended from the original system.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class GuaziFlowError(RuntimeError):
    """Custom exception for flow errors in the Guazi app."""

    def __init__(self, code: str, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.context = context or {}


class ValidationError(GuaziFlowError):
    """Raised when validation fails."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__("VALIDATION_ERROR", message)
        self.field = field


class IssueRecorder:
    """Structured issue recording with audit trail."""

    def __init__(
        self,
        path: Path,
        exception_config: dict[str, Any] | None = None,
        *,
        learning_loop: Any | None = None,
        issue_classifier: Any | None = None,
        audit: Any | None = None,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.exception_config = exception_config or {}
        self.learning_loop = learning_loop
        self.issue_classifier = issue_classifier
        self.audit = audit

    def record(
        self,
        code: str,
        state_id: str | None,
        message: str | None = None,
        context: dict[str, Any] | None = None,
        resolution: str | None = None,
    ) -> dict[str, Any]:
        spec = self.exception_config.get(code, {})
        record_data = {
            "issue_id": f"ISS-{uuid.uuid4().hex[:12]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "code": code,
            "severity": spec.get("severity", "unknown"),
            "state_id": state_id,
            "message": message or spec.get("message", code),
            "context": context or {},
            "resolution": resolution or spec.get("default_resolution", "manual_intervention"),
            "workflow": ["issue_recorded"],
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record_data, ensure_ascii=False) + "\n")
        if self.audit:
            self.audit.log(
                "issue_recorded",
                issue_id=record_data["issue_id"],
                issue_code=record_data["code"],
                state_id=state_id,
                resolution=record_data["resolution"],
            )
        return record_data

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
