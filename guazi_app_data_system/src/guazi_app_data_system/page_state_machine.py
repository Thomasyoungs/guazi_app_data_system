"""Page-state machine and action guardrails."""

from __future__ import annotations

from typing import Any

from .exception_handler import GuaziFlowError


class PageStateMachine:
    def __init__(self, pages_config: dict[str, Any]) -> None:
        pages = pages_config.get("pages", [])
        self.pages: dict[str, dict[str, Any]] = {page["id"]: page for page in pages}
        self._validate()

    def _validate(self) -> None:
        required_keys = {"id", "name", "recognition", "allowed_actions", "forbidden_actions", "next", "return_to", "exception"}
        for page in self.pages.values():
            missing = required_keys - set(page)
            if missing:
                raise ValueError(f"Page {page.get('id')} missing keys: {sorted(missing)}")
        s03 = self.pages.get("S03", {})
        strong = s03.get("recognition", {}).get("strong_contains", [])
        if "\u9009\u62e9\u54c1\u724c" not in strong:
            raise ValueError("S03 must strongly require \u9009\u62e9\u54c1\u724c")

    def get_page(self, state_id: str) -> dict[str, Any]:
        try:
            return self.pages[state_id]
        except KeyError as exc:
            raise GuaziFlowError("PAGE_NOT_RECOGNIZED", f"Unknown state {state_id}", {"state_id": state_id}) from exc

    def allowed_actions(self, state_id: str) -> set[str]:
        return set(self.get_page(state_id).get("allowed_actions", []))

    def forbidden_actions(self, state_id: str) -> set[str]:
        return set(self.get_page(state_id).get("forbidden_actions", []))

    def assert_action_allowed(self, state_id: str, action_id: str) -> None:
        if action_id in self.forbidden_actions(state_id) or action_id not in self.allowed_actions(state_id):
            raise GuaziFlowError(
                "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT",
                f"Action {action_id} is not allowed in {state_id}",
                {"state_id": state_id, "action_id": action_id},
            )

    def transition(self, state_id: str, condition: str) -> str | None:
        for item in self.get_page(state_id).get("next", []):
            if item.get("condition") == condition:
                return item.get("to")
        return None

    def return_target(self, state_id: str) -> str | None:
        return self.get_page(state_id).get("return_to")

    def require_return_target(self, state_id: str) -> str:
        target = self.return_target(state_id)
        if not target:
            raise GuaziFlowError(
                "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT",
                f"State {state_id} has no defined return rule",
                {"state_id": state_id},
            )
        return target
