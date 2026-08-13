"""State-action simulator for the Guazi app.

Replaces the simplified stub with the original state-action sequence.
"""

from __future__ import annotations

from typing import Any

from .data_collector import DataCollector
from .exceptions import GuaziFlowError
from .page_state_machine import PageStateMachine


class ActionExecutor:
    """Executes actions with state-machine enforcement."""

    def __init__(
        self,
        state_machine: PageStateMachine,
        actions_config: dict[str, Any],
        audit: Any | None = None,
        issues: Any | None = None,
        *,
        dry_run: bool = True,
    ) -> None:
        self.state_machine = state_machine
        self.actions_config = actions_config
        self.actions = actions_config.get("actions", {})
        self.audit = audit
        self.issues = issues
        self.dry_run = dry_run

    def execute(self, state_id: str, action_id: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        self.state_machine.assert_action_allowed(state_id, action_id)
        if self.audit:
            self.audit.log("action_requested", state_id=state_id, action_id=action_id, dry_run=self.dry_run)
        if self.dry_run:
            return {"ok": True, "dry_run": True, "action_id": action_id}
        return {"ok": True, "action_id": action_id}


class StateActionSimulator:
    """Simulates the state transitions and actions in the Guazi app."""

    SEQUENCE = [
        ("S00", "tap_left_bottom_skip"),
        ("S01", "click_bottom_select_car_tab"),
        ("S02", "tap_brand_filter"),
        ("S03", "tap_brand_letter"),
        ("S03", "tap_target_brand"),
        ("S04", "click_series_model_button"),
        ("S05", "tap_target_year"),
        ("S05", "tap_exact_trim"),
        ("S05", "tap_green_confirm"),
        ("S06", "tap_trim_filter"),
        ("S07", "tap_color_filter"),
        ("S07", "tap_target_color"),
        ("S07", "tap_age_filter"),
        ("S07", "set_exact_age"),
        ("S07", "tap_view_cars"),
        ("S08", "collect_list_whitelist_fields"),
        ("S08", "tap_sort_if_present"),
        ("S09", "tap_price_low_to_high"),
        ("S10", "tap_next_car_by_price_order"),
        ("S11", "collect_transfer_count"),
        ("S11", "scroll_to_report"),
        ("S11", "tap_full_report"),
        ("S12", "collect_claim_count"),
        ("S12", "collect_max_amount"),
        ("S12", "scroll_to_body_appearance"),
        ("S12", "tap_body_appearance"),
        ("S13", "collect_repair_counts"),
        ("S13", "tap_repair_item_if_nonzero"),
        ("S14", "swipe_next_damage"),
        ("S15", "score_reference_car"),
        ("S16", "calculate_prices"),
        ("S17", "write_pricing_result"),
    ]

    def __init__(self, collector: DataCollector, actions_config: dict[str, Any] | None = None) -> None:
        self.collector = collector
        self.executed_actions: list[tuple[str, str]] = []
        self.actions_config = actions_config or {}

    def execute_sequence(self, executor: ActionExecutor | None = None) -> None:
        if executor is None:
            pages_config = self.actions_config.get("pages", {"pages": []})
            machine = PageStateMachine(pages_config)
            executor = ActionExecutor(machine, self.actions_config, dry_run=True)
        for state_id, action_id in self.SEQUENCE:
            try:
                executor.execute(state_id, action_id)
                self.executed_actions.append((state_id, action_id))
            except GuaziFlowError as e:
                print(f"Warning: Failed to execute {action_id} in {state_id}: {e}")
                continue
