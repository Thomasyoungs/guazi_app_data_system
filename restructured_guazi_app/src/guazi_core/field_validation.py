"""Field-contract validation and forbidden-field guardrails."""

from __future__ import annotations

from typing import Any


class FieldContract:
    def __init__(self, fields_config: dict[str, Any]) -> None:
        self.config = fields_config
        self.forbidden = set(fields_config.get("forbidden_fields", []))

    def validate_target(self, data: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        required = self.config.get("target_fields", {}).get("required", [])
        for field in required:
            if data.get(field) in (None, ""):
                errors.append(f"target missing required field: {field}")
        errors.extend(self._forbidden_errors(data))
        return errors

    def validate_reference(self, data: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        required = self.config.get("reference_fields", {}).get("required", [])
        for field in required:
            if data.get(field) is None:
                errors.append(f"reference missing required field: {field}")
        errors.extend(self._forbidden_errors(data))
        return errors

    def assert_no_forbidden(self, data: dict[str, Any]) -> None:
        errors = self._forbidden_errors(data)
        if errors:
            raise ValueError("; ".join(errors))

    def whitelist_reference(self, data: dict[str, Any]) -> dict[str, Any]:
        allowed = set(self.config.get("reference_fields", {}).get("required", []))
        allowed.update(self.config.get("reference_fields", {}).get("conditional", []))
        allowed.update(self.config.get("reference_fields", {}).get("system_generated", []))
        return {key: value for key, value in data.items() if key in allowed}

    def _forbidden_errors(self, data: dict[str, Any]) -> list[str]:
        return [f"forbidden field present: {field}" for field in sorted(self.forbidden.intersection(data))]
