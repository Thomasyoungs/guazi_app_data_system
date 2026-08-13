"""Page-contract driven runtime action-plan helpers.

The runtime contract shape is:
contract source -> expected -> action plan -> action -> actual -> match.
This module owns the "expected -> action plan" step so page handlers do not
invent ad-hoc algorithms while executing.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import re
from typing import Any

from .runtime_rule_coverage import coverage_trace_for_step


PAGE_CONTRACT_DRIVEN_EXECUTION_CORE_VERSION = "PAGE_CONTRACT_DRIVEN_EXECUTION_CORE_PATCH"
DEFAULT_PAGE_CONTRACT_SOURCE_FILE = "config/pages.yaml"

CONTRACT_ACTION_PLAN_REQUIRED_FIELDS = (
    "step_id",
    "contract_source_file",
    "contract_source_version",
    "expected",
    "allowed_actions",
    "forbidden_actions",
    "action_algorithm",
    "action_inputs",
    "action_outputs",
    "success_condition",
    "failure_stop_code",
)

CONTRACT_ACTION_BINDING_REQUIRED_FIELDS = (
    "contract_action_plan_id",
    "contract_action_plan_used",
    "action_plan_step_id",
    "action_algorithm_used",
    "action_inputs_source",
    "action_outputs_source",
    "forbidden_action_used",
    "runtime_bypassed_action_plan",
    "action_plan_binding_check_passed",
)

S07_AGE_FORBIDDEN_ACTIONS = (
    "legacy_age_slider_unbounded_track_ratio",
    "legacy_age_slider_off_by_one_target",
    "legacy_fallback_without_contract_target",
    "right_first_without_source_clause",
    "long_press_drag_without_source_clause",
    "segmented_drag_without_source_clause",
    "track_based_drag_without_source_clause",
    "right_first",
    "long_press_drag",
    "segmented_drag",
    "track_based_drag",
    "legacy_age_slider_non_real_handle_binding",
    "legacy_fallback_after_direct_success",
    "unlimited_wait_panel_stable",
)

S07_COLOR_FORBIDDEN_ACTIONS = (
    "COLOR_FILTER_DONE_without_selected_confirm",
    "candidate_visible_as_selected",
    "default_color_click",
    "fixed_coordinate",
    "ratio_coordinate",
    "continue_after_non_target_selected",
)

S11_REPORT_ENTRY_FORBIDDEN_ACTIONS = (
    "legacy_s11_screenshot_rect_click_target",
    "legacy_s11_layout_detector_click_target",
    "legacy_s11_text_detector_click_target",
    "legacy_s11_visual_detector_click_target",
    "OCR",
    "fixed_coordinate",
    "ratio_coordinate",
    "default_click",
    "screenshot_coordinate_click",
    "click_when_xml_missing",
    "click_from_screenshot_visible_only",
    "wrong_button_advisor_report",
    "wrong_button_contact_advisor",
    "wrong_button_contact_merchant",
    "wrong_button_bargain",
    "wrong_button_order_now",
    "wrong_button_consult_condition",
    "wrong_button_view_quote",
)


def make_contract_action_plan(
    *,
    step_id: str,
    expected: dict[str, Any],
    allowed_actions: list[str] | tuple[str, ...],
    forbidden_actions: list[str] | tuple[str, ...],
    action_algorithm: dict[str, Any],
    action_inputs: dict[str, Any] | None = None,
    action_outputs: dict[str, Any] | None = None,
    success_condition: str,
    failure_stop_code: str,
    contract_source_file: str = DEFAULT_PAGE_CONTRACT_SOURCE_FILE,
    contract_source_version: str = PAGE_CONTRACT_DRIVEN_EXECUTION_CORE_VERSION,
) -> dict[str, Any]:
    coverage_trace = coverage_trace_for_step(step_id)
    plan = {
        "step_id": step_id,
        "contract_source_file": contract_source_file,
        "contract_source_version": contract_source_version,
        "rule_clause_id": coverage_trace.get("rule_clause_id", ""),
        "rule_source_file": coverage_trace.get("rule_source_file") or contract_source_file,
        "rule_source_version": coverage_trace.get("rule_source_version") or contract_source_version,
        "rule_clause_text_summary": coverage_trace.get("rule_clause_text_summary", ""),
        "coverage_status": coverage_trace.get("coverage_status", "NOT_COVERED"),
        "allowed_fallbacks": list(coverage_trace.get("allowed_fallbacks") or []),
        "performance_budget_ms": coverage_trace.get("performance_budget_ms"),
        "requires_runtime_trace": bool(coverage_trace.get("requires_runtime_trace", True)),
        "expected": deepcopy(expected),
        "allowed_actions": list(allowed_actions),
        "forbidden_actions": list(forbidden_actions),
        "action_algorithm": deepcopy(action_algorithm),
        "action_inputs": deepcopy(action_inputs or {}),
        "action_outputs": deepcopy(action_outputs or {}),
        "success_condition": success_condition,
        "failure_stop_code": failure_stop_code,
    }
    plan["contract_action_plan_id"] = contract_action_plan_id(plan)
    return plan


def contract_action_plan_id(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return ""
    payload = {key: value for key, value in plan.items() if key != "contract_action_plan_id"}
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:16]


def build_action_plan_binding_trace(
    plan: dict[str, Any],
    *,
    action_algorithm_used: str | None = None,
    action_inputs_source: str = "contract_action_plan",
    action_outputs_source: str = "contract_action_plan",
    contract_action_plan_used: bool = True,
    forbidden_action_used: bool = False,
    runtime_bypassed_action_plan: bool = False,
    fallback_used: bool = False,
    fallback_name: str = "",
    actual_duration_ms: int | float | None = None,
    performance_budget_exceeded: bool | None = None,
) -> dict[str, Any]:
    algorithm = plan.get("action_algorithm") if isinstance(plan.get("action_algorithm"), dict) else {}
    used_algorithm = action_algorithm_used or str(algorithm.get("name") or "")
    coverage_trace = coverage_trace_for_step(plan.get("step_id"))
    allowed_fallbacks = list(plan.get("allowed_fallbacks") or coverage_trace.get("allowed_fallbacks") or [])
    budget_ms = plan.get("performance_budget_ms")
    if budget_ms is None:
        budget_ms = coverage_trace.get("performance_budget_ms")
    fallback_allowed = (not fallback_used) or bool(fallback_name and fallback_name in allowed_fallbacks)
    if performance_budget_exceeded is None and actual_duration_ms is not None and budget_ms is not None:
        try:
            performance_budget_exceeded = float(actual_duration_ms) > float(budget_ms)
        except (TypeError, ValueError):
            performance_budget_exceeded = False
    binding = {
        "contract_action_plan_id": str(plan.get("contract_action_plan_id") or contract_action_plan_id(plan)),
        "contract_action_plan_used": bool(contract_action_plan_used),
        "action_plan_step_id": str(plan.get("step_id") or ""),
        "rule_clause_id": str(plan.get("rule_clause_id") or coverage_trace.get("rule_clause_id") or ""),
        "rule_source_file": str(plan.get("rule_source_file") or coverage_trace.get("rule_source_file") or ""),
        "rule_source_version": str(plan.get("rule_source_version") or coverage_trace.get("rule_source_version") or ""),
        "rule_clause_text_summary": str(
            plan.get("rule_clause_text_summary") or coverage_trace.get("rule_clause_text_summary") or ""
        ),
        "coverage_status": str(plan.get("coverage_status") or coverage_trace.get("coverage_status") or "NOT_COVERED"),
        "action_algorithm_used": used_algorithm,
        "action_inputs_source": action_inputs_source,
        "action_outputs_source": action_outputs_source,
        "fallback_used": bool(fallback_used),
        "fallback_name": str(fallback_name or ""),
        "fallback_allowed_by_clause": bool(fallback_allowed),
        "allowed_fallbacks": allowed_fallbacks,
        "forbidden_action_used": bool(forbidden_action_used),
        "runtime_bypassed_action_plan": bool(runtime_bypassed_action_plan),
        "performance_budget_ms": budget_ms,
        "actual_duration_ms": actual_duration_ms,
        "performance_budget_exceeded": bool(performance_budget_exceeded),
        "requires_runtime_trace": bool(plan.get("requires_runtime_trace", coverage_trace.get("requires_runtime_trace", True))),
        "action_plan_binding_check_passed": True,
    }
    binding["action_plan_binding_check_passed"] = not validate_action_plan_binding(binding, plan)
    return binding


def validate_action_plan_binding(binding: dict[str, Any] | None, plan: dict[str, Any] | None) -> list[str]:
    if not isinstance(binding, dict):
        return ["missing_execution_binding_trace"]
    errors: list[str] = []
    for field in CONTRACT_ACTION_BINDING_REQUIRED_FIELDS:
        if field not in binding:
            errors.append(f"missing_binding_field:{field}")
    if not isinstance(plan, dict):
        errors.append("missing_contract_action_plan")
        return errors
    expected_plan_id = str(plan.get("contract_action_plan_id") or contract_action_plan_id(plan))
    if str(binding.get("contract_action_plan_id") or "") != expected_plan_id:
        errors.append("contract_action_plan_id_mismatch")
    if binding.get("contract_action_plan_used") is not True:
        errors.append("contract_action_plan_not_used")
    if binding.get("action_inputs_source") != "contract_action_plan":
        errors.append("action_inputs_source_not_contract_action_plan")
    if binding.get("action_outputs_source") != "contract_action_plan":
        errors.append("action_outputs_source_not_contract_action_plan")
    if binding.get("forbidden_action_used") is True:
        errors.append("forbidden_action_used")
    if binding.get("runtime_bypassed_action_plan") is True:
        errors.append("runtime_bypassed_action_plan")
    used_algorithm = str(binding.get("action_algorithm_used") or "")
    allowed_algorithms = action_plan_allowed_algorithm_names(plan)
    if used_algorithm and used_algorithm not in allowed_algorithms:
        errors.append(f"action_algorithm_not_allowed_by_plan:{used_algorithm}")
    if binding.get("action_plan_binding_check_passed") is False:
        errors.append("action_plan_binding_check_failed")
    return errors


def action_plan_allowed_algorithm_names(plan: dict[str, Any]) -> set[str]:
    allowed = {str(item) for item in plan.get("allowed_actions") or [] if item not in (None, "")}
    algorithm = plan.get("action_algorithm") if isinstance(plan.get("action_algorithm"), dict) else {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                walk(item)
        elif isinstance(value, str) and value:
            allowed.add(value)

    walk(algorithm)
    return allowed


def build_generic_contract_action_plan(
    *,
    step_id: str,
    expected: dict[str, Any] | None = None,
    action_name: str = "verify_contract",
    success_condition: str = "contract_actual == contract_expected",
    failure_stop_code: str = "PAGE_CONTRACT_MISMATCH",
    contract_source_file: str = DEFAULT_PAGE_CONTRACT_SOURCE_FILE,
    contract_source_version: str = PAGE_CONTRACT_DRIVEN_EXECUTION_CORE_VERSION,
) -> dict[str, Any]:
    return make_contract_action_plan(
        step_id=step_id,
        contract_source_file=contract_source_file,
        contract_source_version=contract_source_version,
        expected=expected or {},
        allowed_actions=[action_name, "verify_contract"],
        forbidden_actions=["bypass_contract_guard", "continue_after_contract_mismatch"],
        action_algorithm={
            "name": action_name,
            "rule": ["read_contract_expected", "execute_allowed_action", "verify_actual_matches_expected"],
        },
        action_inputs={"expected": deepcopy(expected or {})},
        action_outputs={},
        success_condition=success_condition,
        failure_stop_code=failure_stop_code,
    )


def build_s07_age_action_plan(
    *,
    registration_date: str | None = None,
    current_year: int | None = None,
    target_age_years: int | float | None = None,
    visible_ticks: list[dict[str, Any]] | list[tuple[int, int, int]] | None = None,
    contract_source_file: str = DEFAULT_PAGE_CONTRACT_SOURCE_FILE,
    contract_source_version: str = PAGE_CONTRACT_DRIVEN_EXECUTION_CORE_VERSION,
) -> dict[str, Any]:
    age = _coerce_int(target_age_years)
    if age is None:
        age = calculate_target_age_years(registration_date, current_year=current_year)
    expected = {
        "target_age_years": age,
        "expected_age_filter": f"{age}-{age}" if age is not None else None,
        "left_slider_target": age,
        "right_slider_target": age,
    }
    tick_output = compute_visible_tick_target_x(visible_ticks or [], age)
    action_outputs = {
        "expected_age_filter": expected["expected_age_filter"],
        "left_slider_target": age,
        "right_slider_target": age,
        "target_x": tick_output.get("target_x"),
        "left_target_x": tick_output.get("target_x"),
        "right_target_x": tick_output.get("target_x"),
        "target_y": tick_output.get("target_y"),
        "target_x_calculation": tick_output.get("calculation"),
        "lower_tick": tick_output.get("lower_tick"),
        "upper_tick": tick_output.get("upper_tick"),
        "exact_tick": tick_output.get("exact_tick"),
        "ratio_between_ticks": tick_output.get("ratio_between_ticks"),
        "exclude_unlimited_tick": True,
        "left_right_same_target_x": True,
        "target_x_available": tick_output.get("target_x") is not None,
    }
    return make_contract_action_plan(
        step_id="S07_AGE_SLIDER",
        contract_source_file=contract_source_file,
        contract_source_version=contract_source_version,
        expected=expected,
        allowed_actions=[
            "tap_age_filter",
            "set_exact_age",
            "set_exact_age_filter",
            "direct_track_fastpath",
            "direct_track_fastpath_5_5",
            "real_green_slider_handle_binding",
            "real_handle_down_move_up",
            "exact_tick_binding",
            "visible_tick_interpolation",
            "text_result_verify_first",
            "final_exact_age_verify",
        ],
        forbidden_actions=list(S07_AGE_FORBIDDEN_ACTIONS),
        action_algorithm={
            "name": "exact_tick_binding",
            "target_age_algorithm": "year_diff_exact",
            "target_x_algorithm": "visible_tick_interpolation",
            "exclude_unlimited_tick": True,
            "left_right_same_target_x": True,
            "final_verify_required": True,
            "rule": [
                "use_visible_ticks",
                "exclude_unlimited_tick",
                "interpolate_between_0_and_2_for_age_1",
                "interpolate_between_4_and_6_for_age_5",
                "left_and_right_same_target_x",
            ],
        },
        action_inputs={
            "registration_date": registration_date,
            "current_year": current_year,
            "target_age_years": age,
            "visible_ticks": _normalize_visible_ticks(visible_ticks or []),
        },
        action_outputs=action_outputs,
        success_condition="final_age_filter == expected_age_filter",
        failure_stop_code="S07_AGE_FILTER_CONTRACT_MISMATCH",
    )


def build_s07_color_action_plan(
    *,
    target_color: str,
    contract_source_file: str = DEFAULT_PAGE_CONTRACT_SOURCE_FILE,
    contract_source_version: str = PAGE_CONTRACT_DRIVEN_EXECUTION_CORE_VERSION,
) -> dict[str, Any]:
    expected = {
        "expected_color": target_color,
        "selected_after_click": target_color,
        "s08_summary_color": target_color,
        "s10_summary_color": target_color,
    }
    return make_contract_action_plan(
        step_id="S07_COLOR",
        contract_source_file=contract_source_file,
        contract_source_version=contract_source_version,
        expected=expected,
        allowed_actions=[
            "select_color_and_confirm",
            "tap_color_filter",
            "tap_target_color",
            "exact_target_color_binding",
            "xml_or_accessibility_target_color_bounds",
            "target_color_node_bounds",
            "direct_clickable_color_text_node",
            "color_grid_text_node_bounds",
            "clickable_color_ancestor_bounds",
            "confirm_selected_color",
            "verify_s08_s10_summary",
        ],
        forbidden_actions=list(S07_COLOR_FORBIDDEN_ACTIONS),
        action_algorithm={
            "name": "exact_target_color_binding",
            "candidate_match": "exact_target_color_only",
            "click_source": "xml_or_accessibility_target_color_bounds",
            "if_text_node_clickable_false": "use_grid_text_bounds",
            "click_point_must_inside_candidate_bounds": True,
            "selected_after_click_must_equal_expected": True,
            "s08_s10_summary_must_equal_expected": True,
            "rule": [
                "bind_current_target_color_node",
                "reject_non_target_candidate",
                "reject_fixed_or_ratio_coordinate",
                "fresh_selected_confirm_required",
            ],
        },
        action_inputs={"target_color": target_color},
        action_outputs={},
        success_condition="selected_color == expected_color and s08_s10_summary_color == expected_color",
        failure_stop_code="S07_COLOR_CONTRACT_MISMATCH",
    )


def build_s10_filter_summary_action_plan(
    expected_filter_summary: dict[str, Any],
    *,
    contract_source_file: str = DEFAULT_PAGE_CONTRACT_SOURCE_FILE,
    contract_source_version: str = PAGE_CONTRACT_DRIVEN_EXECUTION_CORE_VERSION,
) -> dict[str, Any]:
    return make_contract_action_plan(
        step_id="S10_FILTER_SUMMARY",
        contract_source_file=contract_source_file,
        contract_source_version=contract_source_version,
        expected={"expected_filter_summary": deepcopy(expected_filter_summary)},
        allowed_actions=[
            "collect_list_whitelist_fields",
            "verify_filter_summary",
            "verify_target_trisame_cards",
        ],
        forbidden_actions=["continue_after_filter_summary_mismatch", "COLOR_FILTER_DONE_without_summary_match"],
        action_algorithm={
            "name": "filter_summary_contract_match",
            "required_matches": ["brand", "series", "color", "age_filter"],
            "optional_matches": ["model_config_core"],
            "rule": [
                "read_expected_filter_summary",
                "read_actual_filter_summary",
                "stop_on_mismatch_before_s11",
            ],
        },
        action_inputs={"expected_filter_summary": deepcopy(expected_filter_summary)},
        action_outputs={},
        success_condition="actual_filter_summary matches expected_filter_summary",
        failure_stop_code="S10_FILTER_SUMMARY_CONTRACT_MISMATCH",
    )


def build_s11_report_entry_action_plan(
    *,
    contract_source_file: str = DEFAULT_PAGE_CONTRACT_SOURCE_FILE,
    contract_source_version: str = PAGE_CONTRACT_DRIVEN_EXECUTION_CORE_VERSION,
) -> dict[str, Any]:
    return make_contract_action_plan(
        step_id="S11_REPORT_ENTRY",
        contract_source_file=contract_source_file,
        contract_source_version=contract_source_version,
        expected={"button_text": "查看完整报告", "enter_report_after_click": True},
        allowed_actions=[
            "click_view_full_report",
            "xml_exact_text_bounds",
            "xml_clickable_parent_bounds",
            "xml_safe_container_bounds",
            "safe_reposition_after_xml_exact_seen",
            "fresh_pair_stale_xml_redump_once",
            "xml_after_stale_recovery",
            "verify_enter_report_page",
        ],
        forbidden_actions=list(S11_REPORT_ENTRY_FORBIDDEN_ACTIONS),
        action_algorithm={
            "name": "xml_exact_text_bounds",
            "allowed_binding_sources": [
                "xml_exact_text_bounds",
                "xml_clickable_parent_bounds",
                "xml_safe_container_bounds",
                "xml_after_stale_recovery",
            ],
            "forbidden_buttons": ["找顾问解读报告", "联系顾问", "联系商家", "咨询车况", "讲价", "查看报价", "立即订购"],
            "rule": [
                "prefer_xml_exact_text_bounds",
                "redump_once_on_stale_xml",
                "record_screenshot_visible_debug_without_click",
                "reject_fixed_or_ratio_coordinate",
                "verify_click_enters_report",
            ],
        },
        action_inputs={"target_text": "查看完整报告"},
        action_outputs={},
        success_condition="click enters report page",
        failure_stop_code="S11_REPORT_ENTRY_CONTRACT_MISMATCH",
    )


def validate_contract_action_plan(plan: dict[str, Any] | None) -> list[str]:
    if not isinstance(plan, dict):
        return ["missing_contract_action_plan"]
    errors: list[str] = []
    for field in CONTRACT_ACTION_PLAN_REQUIRED_FIELDS:
        if field not in plan:
            errors.append(f"missing_plan_field:{field}")
    algorithm = plan.get("action_algorithm") if isinstance(plan.get("action_algorithm"), dict) else {}
    algorithm_name = str(algorithm.get("name") or "")
    forbidden = {str(item) for item in plan.get("forbidden_actions") or []}
    if algorithm_name and algorithm_name in forbidden:
        errors.append(f"forbidden_action_algorithm:{algorithm_name}")
    if not plan.get("success_condition"):
        errors.append("missing_plan_success_condition")
    if not plan.get("failure_stop_code"):
        errors.append("missing_plan_failure_stop_code")
    return errors


def validate_action_against_plan(action: Any, plan: dict[str, Any] | None) -> list[str]:
    plan_errors = validate_contract_action_plan(plan)
    if plan_errors:
        return plan_errors
    assert isinstance(plan, dict)
    errors: list[str] = []
    forbidden = {str(item) for item in plan.get("forbidden_actions") or []}
    allowed = {str(item) for item in plan.get("allowed_actions") or []}
    algorithm = plan.get("action_algorithm") if isinstance(plan.get("action_algorithm"), dict) else {}
    algorithm_name = str(algorithm.get("name") or "")
    if algorithm_name and algorithm_name in forbidden:
        errors.append(f"forbidden_action_algorithm:{algorithm_name}")
    if isinstance(action, dict):
        action_name = str(action.get("action") or "")
        if action_name and allowed and action_name not in allowed:
            errors.append(f"action_not_allowed_by_plan:{action_name}")
        used_algorithm = str(action.get("used_action_algorithm") or action.get("target_x_calculation") or "")
        if used_algorithm and used_algorithm in forbidden:
            errors.append(f"forbidden_action_used:{used_algorithm}")
        for used in action.get("forbidden_actions_used") or []:
            if str(used) in forbidden:
                errors.append(f"forbidden_action_used:{used}")
        if action.get("used_forbidden_action"):
            errors.append("used_forbidden_action")
    elif action not in (None, "") and allowed and str(action) not in allowed:
        errors.append(f"action_not_allowed_by_plan:{action}")
    return errors


def calculate_target_age_years(registration_date: str | None, *, current_year: int | None = None) -> int | None:
    if not registration_date:
        return None
    current_year = current_year or date.today().year
    text = str(registration_date).strip()
    match = re.search(r"(\d{2,4})(?:[.\-/年]\s*\d{1,2})?", text)
    if not match:
        return None
    year = int(match.group(1))
    if year < 100:
        year += 2000
    return max(int(current_year) - year, 0)


def compute_visible_tick_target_x(
    visible_ticks: list[dict[str, Any]] | list[tuple[int, int, int]],
    target_age: int | float | None,
) -> dict[str, Any]:
    age = _coerce_int(target_age)
    ticks = _normalize_visible_ticks(visible_ticks)
    if age is None:
        return {"failure_reason": "target_age_missing", "calculation": None}
    if not ticks:
        return {"failure_reason": "visible_ticks_missing", "calculation": None}
    exact = next((tick for tick in ticks if tick.get("age") == age), None)
    if exact is not None:
        return {
            "target_x": exact.get("center_x"),
            "target_y": exact.get("center_y"),
            "exact_tick": deepcopy(exact),
            "calculation": "visible_tick_exact",
            "excluded_unlimited_tick": True,
        }
    lower = next((tick for tick in reversed(ticks) if tick.get("age") < age), None)
    upper = next((tick for tick in ticks if tick.get("age") > age), None)
    if lower is None or upper is None:
        return {
            "failure_reason": "visible_tick_bracket_missing",
            "lower_tick": deepcopy(lower),
            "upper_tick": deepcopy(upper),
            "calculation": None,
        }
    lower_age = int(lower["age"])
    upper_age = int(upper["age"])
    if upper_age <= lower_age:
        return {"failure_reason": "visible_tick_bracket_invalid", "calculation": None}
    ratio = (age - lower_age) / (upper_age - lower_age)
    lower_x = int(lower["center_x"])
    upper_x = int(upper["center_x"])
    lower_y = int(lower.get("center_y") or upper.get("center_y") or 0)
    upper_y = int(upper.get("center_y") or lower_y)
    target_x = round(lower_x + (upper_x - lower_x) * ratio)
    target_y = round(lower_y + (upper_y - lower_y) * ratio)
    return {
        "target_x": target_x,
        "target_y": target_y,
        "lower_tick": deepcopy(lower),
        "upper_tick": deepcopy(upper),
        "ratio_between_ticks": ratio,
        "calculation": "visible_tick_interpolation",
        "excluded_unlimited_tick": True,
    }


def _normalize_visible_ticks(
    visible_ticks: list[dict[str, Any]] | list[tuple[int, int, int]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in visible_ticks:
        if isinstance(raw, dict):
            label = str(raw.get("label") or raw.get("text") or raw.get("age") or "").strip()
            if label == "不限":
                continue
            age = _coerce_int(raw.get("age") if raw.get("age") is not None else label)
            center_x = raw.get("center_x")
            center_y = raw.get("center_y")
            if center_x is None and isinstance(raw.get("center"), (list, tuple)) and len(raw["center"]) >= 2:
                center_x, center_y = raw["center"][0], raw["center"][1]
        elif isinstance(raw, tuple) and len(raw) >= 3:
            age = _coerce_int(raw[0])
            center_x, center_y = raw[1], raw[2]
            label = str(raw[0])
        else:
            continue
        if age is None or center_x is None:
            continue
        try:
            tick = {"age": int(age), "center_x": int(center_x), "center_y": int(center_y or 0), "label": label}
        except (TypeError, ValueError):
            continue
        normalized.append(tick)
    deduped: dict[int, dict[str, Any]] = {}
    for tick in normalized:
        deduped[tick["age"]] = tick
    return [deduped[age] for age in sorted(deduped)]


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value))
        return int(match.group(0)) if match else None
