"""Action execution with state-machine enforcement."""

from __future__ import annotations

import time
from typing import Any

from .audit import AuditLogger
from .exception_handler import GuaziFlowError, IssueRecorder
from .page_state_machine import PageStateMachine
from .task_normalizer import TargetCarTask, brand_entry_gate
from .year_age_filter import handles_physically_overlap, normalize_age_handle_value


BRAND_ENTRY_ACTIONS = {"click_brand_entry", "tap_brand_filter"}
SERIES_MODEL_BUTTON_ACTIONS = {"click_series_model_button"}
YEAR_ENTRY_LABELS = {"年份", "车龄", "上牌年份", "上牌时间", "年款"}


class ActionExecutor:
    def __init__(
        self,
        state_machine: PageStateMachine,
        actions_config: dict[str, Any],
        audit: AuditLogger,
        issues: IssueRecorder,
        device: Any | None = None,
        dry_run: bool = True,
    ) -> None:
        self.state_machine = state_machine
        self.actions_config = actions_config
        self.actions = actions_config.get("actions", {})
        self.runtime_action_whitelist = actions_config.get("runtime_recovery_action_whitelist", {})
        self.audit = audit
        self.issues = issues
        self.device = device
        self.dry_run = dry_run

    def execute(self, state_id: str, action_id: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        self._assert_target_task_gate_if_needed(state_id, action_id, context)
        self._assert_planned_action_contract(state_id, action_id, context)
        self._assert_action_contract_if_needed(state_id, action_id, context)
        self._assert_task_color_alignment_if_needed(state_id, action_id, context)
        self._assert_multi_color_cancel_gate_if_needed(state_id, action_id, context)
        self._assert_dual_handle_age_gate_if_needed(state_id, action_id, context)
        self._assert_actual_click_target_contract_if_present(state_id, action_id, context)
        try:
            self.state_machine.assert_action_allowed(state_id, action_id)
        except GuaziFlowError as exc:
            self.issues.record(exc.code, state_id, str(exc), exc.context, "blocked")
            raise

        spec = self.actions.get(action_id)
        if not spec:
            if action_id in self._all_runtime_recovery_actions():
                self.audit.log("runtime_recovery_action_deferred", state_id=state_id, action_id=action_id, dry_run=self.dry_run)
                return {"ok": True, "deferred": True, "action_id": action_id}
            self.issues.record("FORBIDDEN_ACTION", state_id, f"Action spec missing: {action_id}", {"action_id": action_id}, "blocked")
            raise GuaziFlowError("FORBIDDEN_ACTION", f"Action spec missing: {action_id}", {"action_id": action_id})

        self.audit.log("action_requested", state_id=state_id, action_id=action_id, spec=spec, dry_run=self.dry_run)
        if self.dry_run or not self.device:
            result = {"ok": True, "dry_run": True, "action_id": action_id}
            return self._verify_post_action_state(state_id, action_id, result, context)

        result = self._execute_device_action(spec, context)
        ok = bool(result.get("ok"))
        self.audit.log("action_finished", state_id=state_id, action_id=action_id, result=result)
        return self._verify_post_action_state(state_id, action_id, result, context)

    def execute_approved_recovery(self, issue: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute only approved knowledge actions already filtered by state whitelist."""
        context = context or {}
        lookup = issue.get("knowledge_lookup", {})
        if lookup.get("status") != "approved_solution_matched":
            return {"ok": False, "reason": "no_approved_solution"}
        state_id = str(issue.get("state_id") or "")
        attempts = int(lookup.get("attempts", 0))
        max_retries = int(lookup.get("max_auto_retries", 1))
        if attempts >= max_retries:
            return {"ok": False, "reason": "auto_retry_limit_reached"}
        results: list[dict[str, Any]] = []
        for action_id in lookup.get("allowed_auto_actions", []):
            self._assert_recovery_action_allowed(str(issue.get("code") or lookup.get("issue_code") or ""), state_id, str(action_id))
            result = self.execute(state_id, str(action_id), {**context, "allow_auto_recovery": False})
            results.append({"action_id": action_id, "result": result})
            if not result.get("ok"):
                return {"ok": False, "reason": "recovery_action_failed", "results": results}
        return {"ok": True, "solution_id": lookup.get("solution_id"), "results": results}

    def controlled_return(self, state_id: str, expected_state: str, recognized_state: str | None) -> bool:
        self.state_machine.require_return_target(state_id)
        self.audit.log("return_requested", state_id=state_id, expected_state=expected_state)
        if self.device and not self.dry_run:
            self.device.back()
        if recognized_state != expected_state:
            self.issues.record(
                "RETURN_MISMATCH",
                state_id,
                "返回后未命中预期上一层页面",
                {"expected_state": expected_state, "recognized_state": recognized_state},
                "restart_app",
            )
            return False
        self.audit.log("return_verified", state_id=state_id, expected_state=expected_state)
        return True

    def _execute_device_action(self, spec: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        action_type = spec.get("type")
        if action_type == "wait":
            time.sleep(float(spec.get("seconds", 1)))
            return {"ok": True, "type": "wait"}
        if action_type == "tap_text":
            result = self.device.tap_text(str(spec.get("text", "")))
            return {"ok": result.success, "stdout": result.stdout, "stderr": result.stderr}
        if action_type == "tap_text_exact":
            result = self.device.tap_exact_text(str(spec.get("text", "")))
            return {"ok": result.success, "stdout": result.stdout, "stderr": result.stderr}
        if action_type == "tap_s01_bottom_select_car_tab":
            result = self.device.tap_s01_bottom_select_car_tab()
            tap_result = result.get("tap_result")
            return {
                "ok": bool(result.get("success")),
                "tap_x": result.get("tap_x"),
                "tap_y": result.get("tap_y"),
                "method": result.get("method"),
                "within_safe_app_bounds": result.get("within_safe_app_bounds"),
                "failure_reason": result.get("failure_reason"),
                "stdout": getattr(tap_result, "stdout", ""),
                "stderr": getattr(tap_result, "stderr", ""),
            }
        if action_type == "tap_text_prefix":
            text = str(spec.get("text", ""))
            result = self.device.tap_text(text)
            return {"ok": result.success, "stdout": result.stdout, "stderr": result.stderr}
        if action_type == "tap_region":
            result = self.device.tap_region(str(spec.get("region", "")))
            return {"ok": result.success, "stdout": result.stdout, "stderr": result.stderr}
        if action_type == "swipe":
            result = self.device.swipe(str(spec.get("direction", "up")))
            return {"ok": result.success, "stdout": result.stdout, "stderr": result.stderr}
        if action_type == "tap_dynamic":
            target = str(context.get(spec.get("source", ""), ""))
            if not target:
                return {"ok": False, "stderr": f"missing dynamic source {spec.get('source')}"}
            result = self.device.tap_text(target)
            return {"ok": result.success, "stdout": result.stdout, "stderr": result.stderr}
        if action_type == "tap_scoped_text":
            bounds = context.get("actual_click_bounds")
            if isinstance(bounds, list) and len(bounds) == 4:
                x = (int(bounds[0]) + int(bounds[2])) // 2
                y = (int(bounds[1]) + int(bounds[3])) // 2
                result = self.device.tap(x, y)
                return {"ok": result.success, "stdout": result.stdout, "stderr": result.stderr}
            target = str(context.get("actual_click_target", "") or spec.get("text", ""))
            result = self.device.tap_text(target)
            return {"ok": result.success, "stdout": result.stdout, "stderr": result.stderr}
        if action_type in {"collect", "filter", "internal", "slider", "tap_vehicle_title", "swipe_until_text", "controlled_return"}:
            return {"ok": True, "deferred": True, "type": action_type}
        return {"ok": False, "stderr": f"unsupported action type {action_type}"}

    def _assert_target_task_gate_if_needed(self, state_id: str, action_id: str, context: dict[str, Any]) -> None:
        if action_id not in BRAND_ENTRY_ACTIONS or state_id not in {"S02", "S02_SELECT_CAR_TAB"}:
            return
        if self.dry_run and not context.get("enforce_task_gate"):
            return
        target_task = context.get("target_task")
        gate = brand_entry_gate(target_task if isinstance(target_task, TargetCarTask) else None)
        if gate["allowed"]:
            return
        self.issues.record(
            "TARGET_TASK_GATE_BLOCKED",
            state_id,
            "TargetCarTask gate blocked brand entry click before S03.",
            {"action_id": action_id, "gate": gate},
            "blocked",
        )
        raise GuaziFlowError("TARGET_TASK_GATE_BLOCKED", "TargetCarTask gate blocked brand entry click before S03.", gate)

    def _assert_action_contract_if_needed(self, state_id: str, action_id: str, context: dict[str, Any]) -> None:
        if action_id not in SERIES_MODEL_BUTTON_ACTIONS or state_id != "S04":
            return

        target_task = context.get("target_task")
        task_context = {
            "series": getattr(target_task, "series", None) if isinstance(target_task, TargetCarTask) else context.get("target_series"),
            "brand": getattr(target_task, "brand", None) if isinstance(target_task, TargetCarTask) else context.get("target_brand"),
        }
        before_xml = str(context.get("before_xml") or "")
        classifier = self.issues.issue_classifier
        if classifier and before_xml:
            validation = classifier.validate_series_model_click_target(
                task_context=task_context,
                actual_clicked_target={
                    "text": context.get("actual_click_target"),
                    "role": context.get("actual_click_target_role"),
                    "series": context.get("actual_click_target_series"),
                    "bounds": context.get("actual_click_bounds"),
                },
                before_xml=before_xml,
            )
        else:
            validation = {
                "target_series": task_context.get("series"),
                "series_row_found": bool(context.get("series_row_found")),
                "series_model_button_found": bool(context.get("series_model_button_found")),
                "same_row_or_card": bool(context.get("same_row_or_card")),
                "actual_target_is_model_button": context.get("actual_click_target") == "车型"
                and context.get("actual_click_target_role") == "series_model_button",
                "clicked_target": {
                    "text": context.get("actual_click_target"),
                    "role": context.get("actual_click_target_role"),
                    "series": context.get("actual_click_target_series"),
                    "bounds": context.get("actual_click_bounds"),
                },
                "before_contract_evidence": {},
            }

        if not validation["series_model_button_found"]:
            issue = self.issues.record(
                "SERIES_MODEL_BUTTON_NOT_FOUND",
                state_id,
                "The target series row is visible but the matching right-side '车型' button was not found.",
                {"validation": validation, "action_id": action_id},
                "manual_intervention",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

        if not validation["actual_target_is_model_button"] or not validation["same_row_or_card"]:
            issue = self.issues.classify_and_record(
                fallback_code="SERIES_ACTION_TARGET_MISMATCH",
                state_id=state_id,
                message="S04 action target does not satisfy the click_series_model_button contract.",
                context={"validation": validation, "action_id": action_id},
                current_state=state_id,
                intended_action=action_id,
                expected_next_state="S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
                actual_next_state=str(context.get("actual_next_state") or state_id),
                actual_clicked_target={
                    "text": context.get("actual_click_target"),
                    "role": context.get("actual_click_target_role"),
                    "series": context.get("actual_click_target_series"),
                    "bounds": context.get("actual_click_bounds"),
                },
                before_xml=before_xml,
                after_xml=str(context.get("after_xml") or ""),
                page_contract=self.state_machine.get_page(state_id),
                action_contract=self.actions.get(action_id, {}),
                task_context=task_context,
                resolution="manual_intervention",
                recognized_text=str(context.get("recognized_text") or ""),
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    def _assert_planned_action_contract(self, state_id: str, action_id: str, context: dict[str, Any]) -> None:
        if state_id not in self.state_machine.pages:
            return
        page_contract = self.state_machine.get_page(state_id)
        allowed_actions = list(page_contract.get("allowed_actions", []))
        if not allowed_actions:
            return
        if len(allowed_actions) == 1 and action_id != allowed_actions[0]:
            issue = self.issues.classify_and_record(
                fallback_code="CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH",
                state_id=state_id,
                message="Planned action does not match the unique allowed action for the current state.",
                context={"planned_action": action_id, "required_action": allowed_actions[0]},
                current_state=state_id,
                intended_action=action_id,
                expected_next_state=str(context.get("expected_next_state") or state_id),
                actual_next_state=state_id,
                actual_clicked_target={"text": None, "role": None},
                before_xml=str(context.get("before_xml") or ""),
                after_xml=str(context.get("before_xml") or ""),
                page_contract=page_contract,
                action_contract=self.actions.get(action_id, {}),
                task_context=self._task_context_from_context(context),
                resolution="blocked",
                recognized_text=str(context.get("recognized_text") or ""),
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

        if action_id not in self.state_machine.allowed_actions(state_id):
            issue = self.issues.classify_and_record(
                fallback_code="CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH",
                state_id=state_id,
                message="Planned action is outside the current page contract.",
                context={"planned_action": action_id, "allowed_actions": allowed_actions},
                current_state=state_id,
                intended_action=action_id,
                expected_next_state=str(context.get("expected_next_state") or state_id),
                actual_next_state=state_id,
                actual_clicked_target={"text": None, "role": None},
                before_xml=str(context.get("before_xml") or ""),
                after_xml=str(context.get("before_xml") or ""),
                page_contract=page_contract,
                action_contract=self.actions.get(action_id, {}),
                task_context=self._task_context_from_context(context),
                resolution="blocked",
                recognized_text=str(context.get("recognized_text") or ""),
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    def _assert_task_color_alignment_if_needed(self, state_id: str, action_id: str, context: dict[str, Any]) -> None:
        if state_id not in {"S08_COLOR_SELECTED", "S08_COLOR_SELECTED_SINGLE_TARGET"} or action_id != "click_year_or_age_entry":
            return
        panel_color_confirmed = bool(
            context.get("selected_color_ui_confirmed")
            or context.get("selected_color_confirmed_in_panel")
            or context.get("selected_color_visible_in_panel")
        )
        if not panel_color_confirmed:
            issue = self.issues.record(
                "COLOR_STATE_NOT_CONFIRMED_BEFORE_AGE_SELECTION",
                state_id,
                "The S08 model-config panel has not visibly confirmed the task target color as selected before entering year/age flow.",
                {
                    "action_id": action_id,
                    "selected_color_ui_confirmed": bool(context.get("selected_color_ui_confirmed")),
                    "selected_color_confirmed_in_panel": bool(context.get("selected_color_confirmed_in_panel")),
                    "selected_color_visible_in_panel": bool(context.get("selected_color_visible_in_panel")),
                },
                "blocked",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        target_task = context.get("target_task")
        target_color = (
            getattr(target_task, "color", None)
            if isinstance(target_task, TargetCarTask)
            else context.get("target_color") or context.get("color")
        )
        selected_color = context.get("current_selected_color") or context.get("selected_color")
        if not target_color or not selected_color or str(target_color) == str(selected_color):
            return
        issue = self.issues.record(
            "TASK_COLOR_CHANGED_INVALIDATES_SELECTED_COLOR",
            state_id,
            "The currently selected color no longer matches the current task color.",
            {
                "target_color": str(target_color),
                "selected_color": str(selected_color),
                "action_id": action_id,
            },
            "blocked",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    def _assert_multi_color_cancel_gate_if_needed(self, state_id: str, action_id: str, context: dict[str, Any]) -> None:
        if state_id != "S08_COLOR_MULTI_SELECTED" or action_id != "cancel_stale_selected_color":
            return
        target_color = str(context.get("target_color") or context.get("color") or "").strip()
        stale_color = str(context.get("stale_color") or "").strip()
        selected_colors = [str(item).strip() for item in context.get("selected_colors", []) if str(item).strip()]
        actual_target = str(context.get("actual_click_target") or "").strip()

        if not target_color or target_color not in selected_colors:
            issue = self.issues.record(
                "TARGET_COLOR_NOT_SELECTED",
                state_id,
                "Target color is not confirmed selected before stale-color cancellation.",
                {"target_color": target_color, "selected_colors": selected_colors, "action_id": action_id},
                "blocked",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        if not stale_color or stale_color not in selected_colors:
            issue = self.issues.record(
                "STALE_COLOR_NODE_NOT_FOUND",
                state_id,
                "Stale color is not confirmed selected before stale-color cancellation.",
                {"stale_color": stale_color, "selected_colors": selected_colors, "action_id": action_id},
                "blocked",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        if actual_target and actual_target != stale_color:
            issue = self.issues.record(
                "COLOR_ACTION_TARGET_MISMATCH",
                state_id,
                "Actual stale-color cancellation target does not match the stale selected color.",
                {
                    "target_color": target_color,
                    "stale_color": stale_color,
                    "selected_colors": selected_colors,
                    "actual_click_target": actual_target,
                    "action_id": action_id,
                },
                "blocked",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    def _assert_dual_handle_age_gate_if_needed(self, state_id: str, action_id: str, context: dict[str, Any]) -> None:
        if state_id not in {"S08_AGE_EXACT_SLIDER_PANEL", "S08_AGE_LEFT_HANDLE_SET_ONLY"}:
            return

        target_age = normalize_age_handle_value(context.get("target_age"))
        left_value = normalize_age_handle_value(context.get("left_handle_value") or context.get("left_handle_value_before"))
        right_value = normalize_age_handle_value(context.get("right_handle_value") or context.get("right_handle_value_before"))

        if action_id == "set_right_age_handle_to_target":
            if target_age is None or left_value != target_age:
                issue = self.issues.record(
                    "AGE_LEFT_HANDLE_NOT_TARGET",
                    state_id,
                    "Right age handle cannot move until the left handle is already fixed at target_age.",
                    {
                        "action_id": action_id,
                        "target_age": target_age,
                        "left_handle_value": left_value,
                        "right_handle_value": right_value,
                    },
                    "blocked",
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            if right_value == target_age:
                issue = self.issues.record(
                    "AGE_SLIDER_ONLY_LEFT_HANDLE_SET",
                    state_id,
                    "Right age handle is already at target_age; there is no remaining right-handle correction to perform.",
                    {
                        "action_id": action_id,
                        "target_age": target_age,
                        "left_handle_value": left_value,
                        "right_handle_value": right_value,
                    },
                    "blocked",
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

        if action_id == "validate_exact_age_range":
            left_bounds = context.get("left_handle_bounds") or context.get("left_handle_actual_bounds")
            right_bounds = context.get("right_handle_bounds") or context.get("right_handle_actual_bounds")
            physical_overlap = bool(context.get("left_and_right_handle_physical_overlap_at_target_tick"))
            if not physical_overlap and isinstance(left_bounds, list) and isinstance(right_bounds, list):
                physical_overlap = handles_physically_overlap(left_bounds, right_bounds)
            target_age_calculation_verified = context.get("target_age_calculation_verified") is True
            if target_age is None or left_value != target_age or right_value != target_age or not physical_overlap or not target_age_calculation_verified:
                issue = self.issues.record(
                    "AGE_SLIDER_SET_NO_VERIFICATION",
                    state_id,
                    "Exact age validation requires left_handle_value == right_handle_value == target_age, physical handle overlap, and verified target-age calculation.",
                    {
                        "action_id": action_id,
                        "target_age": target_age,
                        "left_handle_value": left_value,
                        "right_handle_value": right_value,
                        "left_handle_bounds": left_bounds,
                        "right_handle_bounds": right_bounds,
                        "left_and_right_handle_physical_overlap_at_target_tick": physical_overlap,
                        "target_age_calculation_verified": target_age_calculation_verified,
                    },
                    "blocked",
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    def _assert_actual_click_target_contract_if_present(self, state_id: str, action_id: str, context: dict[str, Any]) -> None:
        if state_id not in self.state_machine.pages:
            return
        if "actual_click_target" not in context:
            return
        action_contract = self.actions.get(action_id, {})
        actual_text = str(context.get("actual_click_target") or "").strip()
        actual_role = str(context.get("actual_click_target_role") or "").strip()
        expected_text = self._expected_click_target_text(action_id, action_contract, context)
        accepted_labels = set(action_contract.get("accepted_labels", []))
        expected_role = str(action_contract.get("target_role") or "").strip()

        text_ok = True
        if expected_text:
            text_ok = actual_text == expected_text
        elif accepted_labels:
            text_ok = actual_text in accepted_labels
        role_ok = True
        if expected_role:
            role_ok = actual_role == expected_role
        if text_ok and role_ok:
            return
        issue = self.issues.classify_and_record(
            fallback_code="CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH",
            state_id=state_id,
            message="Actual click target does not satisfy the action contract.",
            context={
                "action_id": action_id,
                "actual_click_target": actual_text,
                "actual_click_target_role": actual_role,
                "expected_click_target": expected_text,
                "accepted_labels": sorted(accepted_labels),
                "expected_role": expected_role,
            },
            current_state=state_id,
            intended_action=action_id,
            expected_next_state=str(context.get("expected_next_state") or state_id),
            actual_next_state=state_id,
            actual_clicked_target={
                "text": actual_text,
                "role": actual_role,
                "series": context.get("actual_click_target_series"),
                "bounds": context.get("actual_click_bounds"),
            },
            before_xml=str(context.get("before_xml") or ""),
            after_xml=str(context.get("before_xml") or ""),
            page_contract=self.state_machine.get_page(state_id),
            action_contract=action_contract,
            task_context=self._task_context_from_context(context),
            resolution="blocked",
            recognized_text=str(context.get("recognized_text") or ""),
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    def _verify_post_action_state(
        self,
        state_id: str,
        action_id: str,
        result: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        ok = bool(result.get("ok"))
        expected_next_state = str(context.get("expected_next_state") or "")
        actual_next_state = str(context.get("actual_next_state") or "")
        if ok and expected_next_state and actual_next_state and expected_next_state == actual_next_state:
            return result

        if ok and expected_next_state and actual_next_state and expected_next_state != actual_next_state:
            issue = self.issues.classify_and_record(
                fallback_code="PAGE_CONTRACT_MISMATCH",
                state_id=state_id,
                message=f"Action {action_id} did not reach expected next state {expected_next_state}.",
                context={"result": result, "failed_action_id": action_id},
                current_state=state_id,
                intended_action=action_id,
                expected_next_state=expected_next_state,
                actual_next_state=actual_next_state,
                actual_clicked_target={
                    "text": context.get("actual_click_target"),
                    "role": context.get("actual_click_target_role"),
                    "series": context.get("actual_click_target_series"),
                    "bounds": context.get("actual_click_bounds"),
                },
                before_xml=str(context.get("before_xml") or ""),
                after_xml=str(context.get("after_xml") or ""),
                page_contract=self.state_machine.get_page(state_id),
                action_contract=self.actions.get(action_id, {}),
                task_context={
                    "brand": context.get("target_brand"),
                    "series": context.get("target_series"),
                },
                resolution="manual_intervention",
                recognized_text=str(context.get("recognized_text") or ""),
            )
            if context.get("allow_auto_recovery", True):
                recovery = self.execute_approved_recovery(issue, context)
                if recovery.get("ok"):
                    return {"ok": True, "recovered": True, "recovery": recovery, "original_result": result}
            return {"ok": False, "issue": issue, "original_result": result}

        if not ok:
            issue = self.issues.record(
                "CLICK_FAILED",
                state_id,
                f"Action failed: {action_id}",
                {"result": result, "failed_action_id": action_id},
                "manual_intervention",
            )
            if context.get("allow_auto_recovery", True):
                recovery = self.execute_approved_recovery(issue, context)
                if recovery.get("ok"):
                    return {"ok": True, "recovered": True, "recovery": recovery, "original_result": result}
        return result

    def _assert_recovery_action_allowed(self, issue_code: str, state_id: str, action_id: str) -> None:
        if action_id in set(self.runtime_action_whitelist.get(issue_code, [])):
            return
        self.state_machine.assert_action_allowed(state_id, action_id)

    def _all_runtime_recovery_actions(self) -> set[str]:
        actions: set[str] = set()
        for values in self.runtime_action_whitelist.values():
            actions.update(str(item) for item in values)
        return actions

    def _expected_click_target_text(self, action_id: str, action_contract: dict[str, Any], context: dict[str, Any]) -> str:
        if action_contract.get("text"):
            return str(action_contract["text"])
        source = str(action_contract.get("source") or "")
        if source:
            return str(context.get(source) or "")
        if action_id == "click_year_or_age_entry":
            actual = str(context.get("actual_click_target") or "")
            if actual in YEAR_ENTRY_LABELS:
                return actual
        return ""

    def _task_context_from_context(self, context: dict[str, Any]) -> dict[str, Any]:
        target_task = context.get("target_task")
        if isinstance(target_task, TargetCarTask):
            return target_task.app_operation_params() | {
                "color": target_task.color,
                "selected_color": context.get("current_selected_color") or context.get("selected_color"),
                "selected_color_ui_confirmed": bool(context.get("selected_color_ui_confirmed")),
                "selected_color_confirmed_in_panel": bool(context.get("selected_color_confirmed_in_panel")),
                "selected_color_visible_in_panel": bool(context.get("selected_color_visible_in_panel")),
            }
        return {
            "brand": context.get("target_brand"),
            "series": context.get("target_series"),
            "model_year": context.get("target_model_year"),
            "trim": context.get("target_trim"),
            "color": context.get("target_color") or context.get("color"),
            "vehicle_year": context.get("target_vehicle_year"),
            "target_age": context.get("target_age"),
            "left_handle_value_before": context.get("left_handle_value_before") or context.get("left_handle_value"),
            "right_handle_value_before": context.get("right_handle_value_before") or context.get("right_handle_value"),
            "left_handle_value_after": context.get("left_handle_value_after"),
            "right_handle_value_after": context.get("right_handle_value_after"),
            "selected_color": context.get("current_selected_color") or context.get("selected_color"),
            "selected_color_ui_confirmed": bool(context.get("selected_color_ui_confirmed")),
            "selected_color_confirmed_in_panel": bool(context.get("selected_color_confirmed_in_panel")),
            "selected_color_visible_in_panel": bool(context.get("selected_color_visible_in_panel")),
        }
