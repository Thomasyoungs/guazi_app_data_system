"""Runtime page/rule contract guard helpers.

This module keeps page-contract execution evidence in one normalized shape:
expected -> action -> actual -> match.  Callers can attach these records to
runtime payloads, and tests/scripts can reject any path that continues after a
contract mismatch.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .page_contract_execution_plan import (
    CONTRACT_ACTION_PLAN_REQUIRED_FIELDS,
    build_action_plan_binding_trace,
    build_generic_contract_action_plan,
    build_s07_age_action_plan,
    build_s07_color_action_plan,
    build_s10_filter_summary_action_plan,
    build_s11_report_entry_action_plan,
    validate_action_plan_binding,
    validate_action_against_plan,
    validate_contract_action_plan,
)


PAGE_CONTRACT_MISMATCH = "PAGE_CONTRACT_MISMATCH"

S07_COLOR_CONTRACT_MISMATCH = "S07_COLOR_CONTRACT_MISMATCH"
S07_AGE_FILTER_CONTRACT_MISMATCH = "S07_AGE_FILTER_CONTRACT_MISMATCH"
S08_FILTER_SUMMARY_CONTRACT_MISMATCH = "S08_FILTER_SUMMARY_CONTRACT_MISMATCH"
S10_FILTER_SUMMARY_CONTRACT_MISMATCH = "S10_FILTER_SUMMARY_CONTRACT_MISMATCH"
S11_REPORT_ENTRY_CONTRACT_MISMATCH = "S11_REPORT_ENTRY_CONTRACT_MISMATCH"
S13_S14_COLLECTION_CONTRACT_MISMATCH = "S13_S14_COLLECTION_CONTRACT_MISMATCH"

SCORING_RULE_SOURCE_MISMATCH = "SCORING_RULE_SOURCE_MISMATCH"
REFERENCE_SELECTION_RULE_SOURCE_MISMATCH = "REFERENCE_SELECTION_RULE_SOURCE_MISMATCH"
PRICING_RULE_SOURCE_MISMATCH = "PRICING_RULE_SOURCE_MISMATCH"
PRICING_RESULT_REQUIRED_FIELD_MISSING = "PRICING_RESULT_REQUIRED_FIELD_MISSING"
RUNTIME_CONTRACT_RECORD_INCOMPLETE = "RUNTIME_CONTRACT_RECORD_INCOMPLETE"
RUNTIME_CONTRACT_BYPASS_FORBIDDEN = "RUNTIME_CONTRACT_BYPASS_FORBIDDEN"
RUNTIME_CONTRACT_CONTINUED_AFTER_MISMATCH = "RUNTIME_CONTRACT_CONTINUED_AFTER_MISMATCH"
RUNTIME_CONTRACT_ACTION_PLAN_MISSING = "RUNTIME_CONTRACT_ACTION_PLAN_MISSING"
RUNTIME_CONTRACT_ACTION_PLAN_NOT_USED = "RUNTIME_CONTRACT_ACTION_PLAN_NOT_USED"
RUNTIME_CONTRACT_ACTION_PLAN_BINDING_INVALID = "RUNTIME_CONTRACT_ACTION_PLAN_BINDING_INVALID"
RUNTIME_CONTRACT_ACTION_PLAN_BYPASSED = "RUNTIME_CONTRACT_ACTION_PLAN_BYPASSED"
RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED = "RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED"
RUNTIME_CONTRACT_ACTION_DONE_WITHOUT_ACTUAL = "RUNTIME_CONTRACT_ACTION_DONE_WITHOUT_ACTUAL"

EXPECTED_SCORING_RULE_VERSION = "V1.11"
EXPECTED_REFERENCE_SELECTION_RULE = "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
EXPECTED_PRICING_RULE_VERSION = "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
EXPECTED_COMPETITION_COEFFICIENT_VERSION = "V1.2.6"
EXPECTED_PROFIT_RATE = 0.08
EXPECTED_SERVICE_FEE_TIERS = (
    {"min_price_yuan": 200000, "service_fee_yuan": 5000},
    {"min_price_yuan": 150000, "service_fee_yuan": 4000},
    {"min_price_yuan": 100000, "service_fee_yuan": 3500},
    {"min_price_yuan": 50000, "service_fee_yuan": 3000},
    {"min_price_yuan": 0, "service_fee_yuan": 2500},
)

CONTRACT_REQUIRED_FIELDS = (
    "contract_expected",
    "contract_action",
    "contract_actual",
    "contract_match",
    "contract_mismatch_reason",
    "contract_stop_code",
    "contract_source_version",
    "contract_source_file",
    "contract_action_plan",
)

PAGE_STAGE_GUARD_BUILDERS = {
    "S03": "guard_selected_value_contract",
    "S05": "guard_selected_value_contract",
    "S07_COLOR": "guard_s07_color",
    "S07_AGE": "guard_s07_age",
    "S08_FILTER_SUMMARY": "guard_filter_summary",
    "S10_FILTER_SUMMARY": "guard_filter_summary",
    "S11_REPORT_ENTRY": "guard_s11_report_entry",
    "S13_S14_COLLECTION": "guard_s13_s14_collection",
    "S15_SCORING": "guard_scoring_rule",
    "S15_REFERENCE_SELECTION": "guard_reference_selection_rule",
    "S16_PRICING": "guard_pricing_rule",
}


@dataclass
class ContractGuardError(RuntimeError):
    """Raised when a runtime path attempts to pass a failed contract."""

    code: str
    record: dict[str, Any]

    def __str__(self) -> str:  # pragma: no cover - convenience only.
        stage = self.record.get("stage") or self.record.get("page_stage") or "UNKNOWN"
        reason = self.record.get("contract_mismatch_reason") or self.code
        return f"{self.code} at {stage}: {reason}"


def build_contract_record(
    *,
    stage: str,
    expected: Any,
    action: Any,
    actual: Any,
    action_plan: dict[str, Any] | None = None,
    stop_code: str = PAGE_CONTRACT_MISMATCH,
    source_version: str = "",
    source_file: str = "",
    contract_match: bool | None = None,
    mismatch_reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a normalized runtime contract record."""

    matched = contract_values_match(expected, actual) if contract_match is None else bool(contract_match)
    reason = "" if matched else (mismatch_reason or contract_mismatch_reason(expected, actual))
    plan = action_plan or build_generic_contract_action_plan(
        step_id=stage,
        expected=expected if isinstance(expected, dict) else {"expected": expected},
        action_name=_action_name(action),
        success_condition="contract_actual == contract_expected",
        failure_stop_code=stop_code,
        contract_source_file=source_file or "config/pages.yaml",
        contract_source_version=source_version or "PAGE_CONTRACT_DRIVEN_EXECUTION_CORE_PATCH",
    )
    record: dict[str, Any] = {
        "stage": stage,
        "page_stage": stage,
        "canonical_contract_stop_code": PAGE_CONTRACT_MISMATCH,
        "contract_expected": deepcopy(expected),
        "contract_action_plan": deepcopy(plan),
        "contract_action": deepcopy(action),
        "contract_actual": deepcopy(actual),
        "contract_match": matched,
        "contract_mismatch_reason": reason,
        "contract_stop_code": stop_code if not matched else "",
        "contract_source_version": source_version,
        "contract_source_file": source_file,
        "continue_allowed": matched,
    }
    binding_trace = build_action_plan_binding_trace(
        plan,
        action_algorithm_used=_action_algorithm_used(action, plan),
        contract_action_plan_used=_bool_from_action(action, "contract_action_plan_used", default=True),
        forbidden_action_used=_truthy_from_action(action, "forbidden_action_used"),
        runtime_bypassed_action_plan=_truthy_from_action(action, "runtime_bypassed_action_plan"),
    )
    record.update(binding_trace)
    record["contract_action_plan_binding"] = deepcopy(binding_trace)
    if extra:
        record.update(deepcopy(extra))
    return record


def contract_values_match(expected: Any, actual: Any) -> bool:
    """Return True when actual satisfies the expected contract shape."""

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, expected_value in expected.items():
            if key not in actual:
                return False
            if not contract_values_match(expected_value, actual.get(key)):
                return False
        return True
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)):
            return False
        if len(expected) != len(actual):
            return False
        return all(contract_values_match(left, right) for left, right in zip(expected, actual))
    return _normalize_scalar(expected) == _normalize_scalar(actual)


def contract_mismatch_reason(expected: Any, actual: Any) -> str:
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key, expected_value in expected.items():
            if key not in actual:
                return f"missing_actual:{key}"
            if not contract_values_match(expected_value, actual.get(key)):
                return f"value_mismatch:{key}:expected={expected_value!r}:actual={actual.get(key)!r}"
    return f"expected={expected!r}:actual={actual!r}"


def ensure_contract_match(record: dict[str, Any]) -> None:
    """Raise if a record is incomplete, bypassed, mismatched, or illegally continued."""

    missing = [field for field in CONTRACT_REQUIRED_FIELDS if field not in record]
    if missing:
        raise ContractGuardError(
            RUNTIME_CONTRACT_RECORD_INCOMPLETE,
            {**record, "contract_mismatch_reason": f"missing_fields:{','.join(missing)}"},
        )
    plan_errors = validate_action_against_plan(record.get("contract_action"), record.get("contract_action_plan"))
    if plan_errors:
        plan_error_code = (
            RUNTIME_CONTRACT_ACTION_PLAN_MISSING
            if any("missing_contract_action_plan" in item or "missing_plan_field" in item for item in plan_errors)
            else RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED
        )
        raise ContractGuardError(
            plan_error_code,
            {**record, "contract_mismatch_reason": ";".join(plan_errors)},
        )
    binding_errors = validate_action_plan_binding(_record_binding(record), record.get("contract_action_plan"))
    if binding_errors:
        raise ContractGuardError(
            _binding_error_code(binding_errors),
            {**record, "contract_mismatch_reason": ";".join(binding_errors)},
        )
    if _contract_action_done_without_actual(record):
        raise ContractGuardError(
            RUNTIME_CONTRACT_ACTION_DONE_WITHOUT_ACTUAL,
            {**record, "contract_mismatch_reason": "action_done_without_contract_actual"},
        )
    if record.get("bypass_contract_guard") is True:
        raise ContractGuardError(RUNTIME_CONTRACT_BYPASS_FORBIDDEN, record)
    if record.get("contract_match") is False:
        if record.get("continue_allowed") is True or record.get("continued_after_mismatch") is True:
            raise ContractGuardError(RUNTIME_CONTRACT_CONTINUED_AFTER_MISMATCH, record)
        raise ContractGuardError(str(record.get("contract_stop_code") or PAGE_CONTRACT_MISMATCH), record)


def validate_contract_records(records: list[dict[str, Any]]) -> list[str]:
    """Return validation errors for a batch of contract records."""

    errors: list[str] = []
    for index, record in enumerate(records):
        missing = [field for field in CONTRACT_REQUIRED_FIELDS if field not in record]
        if missing:
            errors.append(f"record[{index}]:missing_fields:{','.join(missing)}")
        plan = record.get("contract_action_plan")
        for plan_error in validate_contract_action_plan(plan):
            errors.append(f"record[{index}]:{RUNTIME_CONTRACT_ACTION_PLAN_MISSING}:{plan_error}")
        for action_error in validate_action_against_plan(record.get("contract_action"), plan):
            if action_error.startswith("missing_"):
                continue
            code = (
                RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED
                if "forbidden" in action_error or "used_forbidden_action" in action_error
                else RUNTIME_CONTRACT_RECORD_INCOMPLETE
            )
            errors.append(f"record[{index}]:{code}:{action_error}")
        for binding_error in validate_action_plan_binding(_record_binding(record), plan):
            errors.append(f"record[{index}]:{_binding_error_code([binding_error])}:{binding_error}")
        if _contract_action_done_without_actual(record):
            errors.append(f"record[{index}]:{RUNTIME_CONTRACT_ACTION_DONE_WITHOUT_ACTUAL}")
        if record.get("bypass_contract_guard") is True:
            errors.append(f"record[{index}]:{RUNTIME_CONTRACT_BYPASS_FORBIDDEN}")
        if record.get("contract_match") is False and (
            record.get("continue_allowed") is True or record.get("continued_after_mismatch") is True
        ):
            errors.append(f"record[{index}]:{RUNTIME_CONTRACT_CONTINUED_AFTER_MISMATCH}")
        if record.get("contract_action") in (None, ""):
            errors.append(f"record[{index}]:missing_contract_action")
        if "contract_expected" not in record or record.get("contract_expected") in (None, ""):
            errors.append(f"record[{index}]:missing_contract_expected")
        if "contract_actual" not in record or record.get("contract_actual") in (None, ""):
            errors.append(f"record[{index}]:missing_contract_actual")
    return errors


def guard_selected_value_contract(
    *,
    stage: str,
    expected_field: str,
    expected_value: Any,
    selected_value: Any,
    action: str,
    stop_code: str = PAGE_CONTRACT_MISMATCH,
    source_version: str = "",
    source_file: str = "",
) -> dict[str, Any]:
    return build_contract_record(
        stage=stage,
        expected={expected_field: expected_value},
        action={"action": action, "expected_field": expected_field},
        actual={expected_field: selected_value},
        stop_code=stop_code,
        source_version=source_version,
        source_file=source_file,
    )


def guard_s07_color(
    *,
    expected_color: str,
    selected_color: str | None,
    s08_color: str | None = None,
    s10_color: str | None = None,
    source_version: str = "S07_COLOR_CONTRACT_V1",
    source_file: str = "config/pages.yaml",
) -> dict[str, Any]:
    expected = {"target_color": expected_color}
    actual = {"selected_color": selected_color}
    if s08_color is not None:
        actual["s08_color"] = s08_color
    if s10_color is not None:
        actual["s10_color"] = s10_color
    normalized_target = _normalize_scalar(expected_color)
    actual_values = [selected_color, s08_color, s10_color]
    matched = all(_normalize_scalar(value) == normalized_target for value in actual_values if value is not None)
    return build_contract_record(
        stage="S07_COLOR",
        expected=expected,
        action={"action": "select_color_and_confirm", "target_color": expected_color},
        actual=actual,
        action_plan=build_s07_color_action_plan(
            target_color=expected_color,
            contract_source_file=source_file,
            contract_source_version=source_version,
        ),
        stop_code=S07_COLOR_CONTRACT_MISMATCH,
        source_version=source_version,
        source_file=source_file,
        contract_match=matched,
        mismatch_reason="" if matched else f"target_color={expected_color!r}:actual={actual!r}",
    )


def guard_s07_age(
    *,
    target_age_years: int | float,
    actual_age_filter: str | None,
    actual_left_age: int | float | None = None,
    actual_right_age: int | float | None = None,
    source_version: str = "S07_AGE_FILTER_CONTRACT_V1",
    source_file: str = "config/pages.yaml",
) -> dict[str, Any]:
    age = int(float(target_age_years))
    expected_filter = f"{age}-{age}"
    actual_filter = _normalize_age_filter(actual_age_filter)
    left_ok = actual_left_age is None or int(float(actual_left_age)) == age
    right_ok = actual_right_age is None or int(float(actual_right_age)) == age
    matched = actual_filter == expected_filter and left_ok and right_ok
    return build_contract_record(
        stage="S07_AGE",
        expected={"target_age_years": age, "target_age_filter": expected_filter, "left_slider_target": age, "right_slider_target": age},
        action={"action": "set_exact_age_filter", "target_age_filter": expected_filter},
        actual={
            "actual_age_filter": actual_filter,
            "left_slider_actual": actual_left_age,
            "right_slider_actual": actual_right_age,
        },
        action_plan=build_s07_age_action_plan(
            target_age_years=age,
            contract_source_file=source_file,
            contract_source_version=source_version,
        ),
        stop_code=S07_AGE_FILTER_CONTRACT_MISMATCH,
        source_version=source_version,
        source_file=source_file,
        contract_match=matched,
        mismatch_reason="" if matched else f"expected_age_filter={expected_filter}:actual_age_filter={actual_filter}",
    )


def guard_filter_summary(
    *,
    stage: str,
    expected_filters: dict[str, Any],
    summary: dict[str, Any] | str,
    source_version: str = "FILTER_SUMMARY_CONTRACT_V1",
    source_file: str = "config/pages.yaml",
) -> dict[str, Any]:
    summary_text = _summary_to_text(summary)
    matched_fields = {
        key: _summary_contains(summary_text, value)
        for key, value in expected_filters.items()
        if value not in (None, "")
    }
    matched = all(matched_fields.values()) if matched_fields else False
    stop_code = S08_FILTER_SUMMARY_CONTRACT_MISMATCH if stage == "S08" else S10_FILTER_SUMMARY_CONTRACT_MISMATCH
    return build_contract_record(
        stage=f"{stage}_FILTER_SUMMARY",
        expected={"filters": deepcopy(expected_filters)},
        action={"action": "verify_filter_summary", "stage": stage},
        actual={"summary_text": summary_text, "matched_fields": matched_fields},
        action_plan=build_s10_filter_summary_action_plan(
            expected_filters,
            contract_source_file=source_file,
            contract_source_version=source_version,
        ),
        stop_code=stop_code,
        source_version=source_version,
        source_file=source_file,
        contract_match=matched,
        mismatch_reason="" if matched else f"missing_or_mismatched_filter:{_first_false_key(matched_fields)}",
    )


def guard_s11_report_entry(
    *,
    click_source: str | None,
    xml_bounds: tuple[int, int, int, int] | list[int] | None = None,
    dynamic_button_rect: tuple[int, int, int, int] | list[int] | None = None,
    forbidden_overlap: bool = False,
    entered_report: bool | None = None,
    source_version: str = "V1.50",
    source_file: str = "config/pages.yaml",
) -> dict[str, Any]:
    allowed_sources = {
        "xml_exact_text_bounds",
        "xml_clickable_parent_bounds",
        "xml_safe_container_bounds",
        "xml_after_stale_recovery",
    }
    bound_rect = xml_bounds if click_source in allowed_sources else dynamic_button_rect
    bindable = click_source in allowed_sources and _valid_rect(bound_rect) and not forbidden_overlap
    entered_ok = True if entered_report is None else bool(entered_report)
    matched = bindable and entered_ok
    return build_contract_record(
        stage="S11_REPORT_ENTRY",
        expected={"button_text": "view_full_report", "bindable_target_required": True, "enter_report_after_click": True},
        action={"action": "click_view_full_report", "click_source": click_source, "target_bounds": bound_rect},
        actual={
            "click_source": click_source,
            "xml_bounds": list(xml_bounds) if xml_bounds is not None else None,
            "dynamic_button_rect": list(dynamic_button_rect) if dynamic_button_rect is not None else None,
            "forbidden_overlap": forbidden_overlap,
            "entered_report": entered_report,
        },
        action_plan=build_s11_report_entry_action_plan(
            contract_source_file=source_file,
            contract_source_version=source_version,
        ),
        stop_code=S11_REPORT_ENTRY_CONTRACT_MISMATCH,
        source_version=source_version,
        source_file=source_file,
        contract_match=matched,
        mismatch_reason="" if matched else "report_entry_not_reliably_bound_or_not_entered",
    )


def guard_s13_s14_collection(
    *,
    s13_total_repair_count: int,
    s14_collected_items_count: int,
    reference: dict[str, Any] | None = None,
    source_version: str = "V1.47_S14_WHOLE_VEHICLE_COLLECTION_COMPLETENESS_GATE",
    source_file: str = "desktop_data_flow_contract_V1.47",
) -> dict[str, Any]:
    expected_count = max(int(s13_total_repair_count or 0), 0)
    actual_count = max(int(s14_collected_items_count or 0), 0)
    matched = actual_count >= expected_count
    excluded_reason = "" if matched else "S13_S14_COLLECTION_CONTRACT_MISMATCH"
    if reference is not None and not matched:
        reference["reference_score_usable_for_boundary"] = False
        reference["reference_score_trustworthy"] = False
        reference["reference_score_preliminary"] = True
        reference["excluded_from_boundary"] = True
        reference["excluded_from_boundary_reason"] = excluded_reason
    return build_contract_record(
        stage="S13_S14_COLLECTION",
        expected={"s13_total_repair_count": expected_count, "s14_min_collected_items_count": expected_count},
        action={"action": "collect_all_s13_repair_entries_before_s15"},
        actual={"s13_total_repair_count": expected_count, "s14_collected_items_count": actual_count},
        stop_code=S13_S14_COLLECTION_CONTRACT_MISMATCH,
        source_version=source_version,
        source_file=source_file,
        contract_match=matched,
        mismatch_reason="" if matched else f"s13_total={expected_count}:s14_collected={actual_count}",
        extra={"reference_excluded_from_v3_boundary": not matched, "excluded_from_boundary_reason": excluded_reason},
    )


def guard_scoring_rule(
    *,
    active_scoring_rule_version: str | None,
    source_file: str | None,
    components: dict[str, Any] | None = None,
    deduction_items: list[dict[str, Any]] | None = None,
    score_input_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    matched = active_scoring_rule_version == EXPECTED_SCORING_RULE_VERSION and EXPECTED_SCORING_RULE_VERSION in str(source_file or "")
    return build_contract_record(
        stage="S15_SCORING_RULE",
        expected={"active_scoring_rule_version": EXPECTED_SCORING_RULE_VERSION, "source_file_contains": EXPECTED_SCORING_RULE_VERSION},
        action={"action": "score_with_active_rule_source"},
        actual={
            "active_scoring_rule_version": active_scoring_rule_version,
            "source_file": source_file,
            "components": deepcopy(components or {}),
            "deduction_items": deepcopy(deduction_items or []),
            "score_input_summary": deepcopy(score_input_summary or {}),
        },
        stop_code=SCORING_RULE_SOURCE_MISMATCH,
        source_version=EXPECTED_SCORING_RULE_VERSION,
        source_file=str(source_file or ""),
        contract_match=matched,
        mismatch_reason="" if matched else "active scoring rule source is not V1.11",
    )


def guard_reference_selection_rule(
    *,
    active_reference_selection_rule: str | None,
    target_score: float | int | None,
    reference_scores: list[float | int],
    references: list[dict[str, Any]] | None = None,
    selected_reference_index: int | None = None,
    boundary_reference_index: int | None = None,
    exclusion_reasons: list[str] | None = None,
) -> dict[str, Any]:
    references = references or []
    trusted_indices = {
        int(ref.get("reference_index"))
        for ref in references
        if ref.get("reference_index") is not None and _reference_is_usable_for_boundary(ref)
    }
    selected_ok = selected_reference_index is None or selected_reference_index in trusted_indices or not references
    boundary_ok = boundary_reference_index is None or boundary_reference_index in trusted_indices or not references
    matched = (
        active_reference_selection_rule == EXPECTED_REFERENCE_SELECTION_RULE
        and selected_ok
        and boundary_ok
    )
    numeric_target_score = _float_or_none(target_score)
    numeric_reference_scores = [_float_or_none(score) for score in reference_scores]
    valid_reference_scores = [score for score in numeric_reference_scores if score is not None]
    boundary_confirmed = (
        numeric_target_score is not None
        and any(score >= numeric_target_score for score in valid_reference_scores)
    )
    continue_required = bool(
        active_reference_selection_rule == EXPECTED_REFERENCE_SELECTION_RULE
        and numeric_target_score is not None
        and valid_reference_scores
        and not boundary_confirmed
    )
    collected_reference_indices = [
        int(ref.get("reference_index"))
        for ref in references
        if isinstance(ref, dict) and ref.get("reference_index") is not None
    ]
    next_reference_index = (max(collected_reference_indices) + 1) if continue_required and collected_reference_indices else None
    return build_contract_record(
        stage="S15_REFERENCE_SELECTION",
        expected={"active_reference_selection_rule": EXPECTED_REFERENCE_SELECTION_RULE, "trusted_references_only": True},
        action={"action": "select_v3_3_boundary_previous_reference_recollect"},
        actual={
            "active_reference_selection_rule": active_reference_selection_rule,
            "target_score": target_score,
            "reference_scores": list(reference_scores),
            "boundary_confirmed": boundary_confirmed,
            "continue_required": continue_required,
            "next_reference_index": next_reference_index,
            "remaining_reference_count": None,
            "early_exit_rule_clause_id": "",
            "early_exit_allowed": False,
            "trusted_reference_count": len(trusted_indices),
            "usable_for_boundary_reference_count": len(trusted_indices),
            "selected_reference_index": selected_reference_index,
            "boundary_reference_index": boundary_reference_index,
            "exclusion_reasons": list(exclusion_reasons or []),
        },
        stop_code=REFERENCE_SELECTION_RULE_SOURCE_MISMATCH,
        source_version=EXPECTED_REFERENCE_SELECTION_RULE,
        source_file="scoring_rule_V1.11_reference_selection_V3_3_boundary_previous_recollect",
        contract_match=matched,
        mismatch_reason="" if matched else "reference selection is not V3.3 or selected untrusted reference",
    )


def guard_pricing_rule(
    payload: dict[str, Any],
    *,
    active_pricing_rule_version: str | None = None,
    service_fee_rule_version: str | None = None,
    competition_coefficient_version: str | None = None,
    expected_profit_rate: float = EXPECTED_PROFIT_RATE,
) -> dict[str, Any]:
    normalized = normalize_pricing_payload_for_guard(payload, default_profit_rate=expected_profit_rate)
    actual_pricing_rule_version = active_pricing_rule_version or _first_present(
        normalized,
        ("active_pricing_rule_version",),
        ("pricing_rule_version",),
        ("pricing", "pricing_rule_version"),
    )
    actual_service_fee_rule_version = service_fee_rule_version or _first_present(
        normalized,
        ("service_fee_rule_version",),
        ("pricing", "service_fee_rule_version"),
        ("pricing_rule_version",),
    )
    actual_competition_version = competition_coefficient_version or _first_present(
        normalized,
        ("active_competition_coefficient_version",),
        ("competition_coefficient_version",),
        ("pricing", "competition_coefficient_version"),
    )
    if actual_pricing_rule_version is None:
        actual_pricing_rule_version = EXPECTED_PRICING_RULE_VERSION
    if actual_service_fee_rule_version is None:
        actual_service_fee_rule_version = EXPECTED_PRICING_RULE_VERSION
    if actual_competition_version is None:
        actual_competition_version = EXPECTED_COMPETITION_COEFFICIENT_VERSION
    required_fields = {
        "profit_rate": normalized.get("profit_rate"),
        "service_fee": normalized.get("service_fee") or normalized.get("guazi_service_fee_yuan"),
        "estimated_return_price": normalized.get("estimated_return_price") or normalized.get("guazi_return_price_yuan"),
        "cost_price": normalized.get("cost_price") or normalized.get("cost_yuan"),
        "suggested_purchase_price_yuan": normalized.get("suggested_purchase_price_yuan"),
        "final_purchase_price_yuan": normalized.get("final_purchase_price_yuan"),
        "pricing_decision_source": normalized.get("pricing_decision_source"),
    }
    manual_review_pending = _truthy(normalized.get("manual_review_required")) or normalized.get("pricing_decision_source") == "MANUAL_REVIEW_PENDING"
    required_for_mode = dict(required_fields)
    if manual_review_pending:
        required_for_mode.pop("final_purchase_price_yuan", None)
        required_for_mode.pop("suggested_purchase_price_yuan", None)
    missing = [key for key, value in required_for_mode.items() if value in (None, "")]
    listing_price = _coerce_int(
        _first_present(
            normalized,
            ("target_guazi_listing_price_yuan",),
            ("guazi_listing_price_yuan",),
            ("pricing", "target_guazi_listing_price_yuan"),
            ("pricing", "guazi_listing_price_yuan"),
        )
    )
    actual_service_fee = _coerce_int(required_fields["service_fee"])
    expected_service_fee = _expected_service_fee_for_price(listing_price) if listing_price is not None else None
    service_fee_contract_match = (
        True
        if expected_service_fee is None or actual_service_fee is None
        else actual_service_fee == expected_service_fee
    )
    version_ok = (
        actual_pricing_rule_version == EXPECTED_PRICING_RULE_VERSION
        and actual_service_fee_rule_version in {EXPECTED_PRICING_RULE_VERSION, "V3_SERVICE_FEE_TIER"}
        and actual_competition_version == EXPECTED_COMPETITION_COEFFICIENT_VERSION
    )
    matched = version_ok and not missing and service_fee_contract_match
    stop_code = PRICING_RULE_SOURCE_MISMATCH if not version_ok else PRICING_RESULT_REQUIRED_FIELD_MISSING
    if not service_fee_contract_match:
        stop_code = PRICING_RULE_SOURCE_MISMATCH
    mismatch_reason = ""
    if not matched:
        if not version_ok:
            mismatch_reason = "pricing rule version mismatch"
        elif missing:
            mismatch_reason = f"missing_fields:{','.join(missing)}"
        elif not service_fee_contract_match:
            mismatch_reason = (
                "service_fee_contract_mismatch:"
                f"listing={listing_price}:expected={expected_service_fee}:actual={actual_service_fee}"
            )
    return build_contract_record(
        stage="S16_PRICING_RULE",
        expected={
            "active_pricing_rule_version": EXPECTED_PRICING_RULE_VERSION,
            "service_fee_rule_version": EXPECTED_PRICING_RULE_VERSION,
            "competition_coefficient_version": EXPECTED_COMPETITION_COEFFICIENT_VERSION,
            "service_fee_tiers": [dict(item) for item in EXPECTED_SERVICE_FEE_TIERS],
            "required_fields": sorted(required_for_mode),
        },
        action={"action": "normalize_and_validate_pricing_result_before_delivery"},
        actual={
            "active_pricing_rule_version": actual_pricing_rule_version,
            "service_fee_rule_version": actual_service_fee_rule_version,
            "competition_coefficient_version": actual_competition_version,
            "target_guazi_listing_price_yuan": listing_price,
            "profit_rate": required_fields["profit_rate"],
            "service_fee": required_fields["service_fee"],
            "service_fee_expected_by_contract": expected_service_fee,
            "service_fee_actual": actual_service_fee,
            "service_fee_contract_match": service_fee_contract_match,
            "estimated_return_price": required_fields["estimated_return_price"],
            "cost_price": required_fields["cost_price"],
            "suggested_purchase_price_yuan": required_fields["suggested_purchase_price_yuan"],
            "final_purchase_price_yuan": required_fields["final_purchase_price_yuan"],
            "pricing_decision_source": required_fields["pricing_decision_source"],
            "manual_review_pending": manual_review_pending,
            "missing_required_fields": missing,
        },
        stop_code=stop_code,
        source_version=EXPECTED_PRICING_RULE_VERSION,
        source_file="config/desktop_rule_compiled.json",
        contract_match=matched,
        mismatch_reason=mismatch_reason,
        extra={"normalized_pricing_payload": normalized},
    )


def normalize_pricing_payload_for_guard(payload: dict[str, Any], *, default_profit_rate: float = EXPECTED_PROFIT_RATE) -> dict[str, Any]:
    """Return a non-mutating normalized copy for pricing contract validation."""

    normalized = deepcopy(payload or {})
    nested_pricing = normalized.get("pricing") if isinstance(normalized.get("pricing"), dict) else {}
    s17_payload = normalized.get("s17_payload") if isinstance(normalized.get("s17_payload"), dict) else {}
    suggested = _first_non_empty(
        normalized.get("suggested_purchase_price_yuan"),
        nested_pricing.get("suggested_purchase_price_yuan"),
        s17_payload.get("suggested_purchase_price_yuan"),
        s17_payload.get("suggested_acquisition_price_yuan"),
    )
    if suggested not in (None, ""):
        normalized.setdefault("suggested_purchase_price_yuan", suggested)
        normalized.setdefault("system_suggested_price_yuan", suggested)
        if not _truthy(normalized.get("manual_review_required")):
            normalized.setdefault("final_purchase_price_yuan", suggested)
            normalized.setdefault("final_price_source", "SYSTEM_AUTOMATIC_PRICING")
            normalized.setdefault("pricing_decision_source", "AUTOMATIC_PRICING")
    if normalized.get("profit_rate") in (None, ""):
        normalized["profit_rate"] = _first_non_empty(
            nested_pricing.get("profit_rate"),
            normalized.get("expected_profit_rate"),
            default_profit_rate,
        )
    if normalized.get("guazi_service_fee_yuan") in (None, ""):
        normalized["guazi_service_fee_yuan"] = _first_non_empty(
            normalized.get("service_fee"),
            nested_pricing.get("guazi_service_fee_yuan"),
            nested_pricing.get("service_fee"),
        )
    if normalized.get("guazi_return_price_yuan") in (None, ""):
        normalized["guazi_return_price_yuan"] = _first_non_empty(
            normalized.get("estimated_return_price"),
            nested_pricing.get("guazi_return_price_yuan"),
            nested_pricing.get("estimated_return_price"),
        )
    if normalized.get("cost_yuan") in (None, ""):
        normalized["cost_yuan"] = _first_non_empty(normalized.get("cost_price"), nested_pricing.get("cost_yuan"), nested_pricing.get("cost_price"))
    return normalized


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.strip().split()).lower()
    return value


def _normalize_age_filter(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value)
    numbers = [int(float(item)) for item in __import__("re").findall(r"\d+(?:\.\d+)?", text)]
    if len(numbers) >= 2:
        return f"{numbers[0]}-{numbers[1]}"
    if len(numbers) == 1:
        return f"{numbers[0]}-{numbers[0]}"
    return text.strip()


def _summary_to_text(summary: dict[str, Any] | str) -> str:
    if isinstance(summary, str):
        return summary
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                walk(item)
        elif value is not None:
            parts.append(str(value))

    walk(summary)
    return " ".join(parts)


def _summary_contains(summary_text: str, expected_value: Any) -> bool:
    if expected_value in (None, ""):
        return True
    if isinstance(expected_value, (list, tuple, set)):
        return any(_summary_contains(summary_text, item) for item in expected_value)
    return str(expected_value).strip() in summary_text


def _first_false_key(mapping: dict[str, bool]) -> str:
    for key, value in mapping.items():
        if not value:
            return key
    return ""


def _valid_rect(rect: tuple[int, int, int, int] | list[int] | None) -> bool:
    if rect is None or len(rect) != 4:
        return False
    try:
        x1, y1, x2, y2 = [int(item) for item in rect]
    except (TypeError, ValueError):
        return False
    return x2 > x1 and y2 > y1


def _reference_is_usable_for_boundary(reference: dict[str, Any]) -> bool:
    if reference.get("reference_early_exit") is True:
        return False
    if reference.get("excluded_from_final_reference_selection") is True:
        return False
    if reference.get("usable_for_boundary") is False or reference.get("usable_for_pre_boundary") is False:
        return False
    if reference.get("reference_score_usable_for_boundary") is False:
        return False
    if reference.get("excluded_from_boundary") is True:
        return False
    if reference.get("reference_score_trustworthy") is False:
        return False
    return True


def _first_present(payload: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current not in (None, ""):
            return current
    return None


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _expected_service_fee_for_price(price_yuan: int | None) -> int | None:
    if price_yuan is None:
        return None
    for row in EXPECTED_SERVICE_FEE_TIERS:
        if price_yuan >= int(row["min_price_yuan"]):
            return int(row["service_fee_yuan"])
    return int(EXPECTED_SERVICE_FEE_TIERS[-1]["service_fee_yuan"])


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _action_name(action: Any) -> str:
    if isinstance(action, dict):
        return str(action.get("action") or action.get("name") or "execute_contract_action")
    if action not in (None, ""):
        return str(action)
    return "execute_contract_action"


def _action_algorithm_used(action: Any, plan: dict[str, Any] | None) -> str | None:
    if isinstance(action, dict):
        for key in ("action_algorithm_used", "used_action_algorithm", "target_x_calculation", "click_source"):
            value = action.get(key)
            if value not in (None, ""):
                return str(value)
    algorithm = plan.get("action_algorithm") if isinstance(plan, dict) and isinstance(plan.get("action_algorithm"), dict) else {}
    return str(algorithm.get("name") or "") or None


def _truthy_from_action(action: Any, key: str) -> bool:
    return _bool_from_action(action, key, default=False)


def _bool_from_action(action: Any, key: str, *, default: bool = False) -> bool:
    if not isinstance(action, dict) or key not in action:
        return default
    value = action.get(key)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _record_binding(record: dict[str, Any]) -> dict[str, Any]:
    top_level = {
        "contract_action_plan_id": record.get("contract_action_plan_id"),
        "contract_action_plan_used": record.get("contract_action_plan_used"),
        "action_plan_step_id": record.get("action_plan_step_id"),
        "action_algorithm_used": record.get("action_algorithm_used"),
        "action_inputs_source": record.get("action_inputs_source"),
        "action_outputs_source": record.get("action_outputs_source"),
        "forbidden_action_used": record.get("forbidden_action_used"),
        "runtime_bypassed_action_plan": record.get("runtime_bypassed_action_plan"),
        "action_plan_binding_check_passed": record.get("action_plan_binding_check_passed"),
    }
    if any(value is not None for value in top_level.values()):
        return top_level
    if isinstance(record.get("contract_action_plan_binding"), dict):
        return dict(record["contract_action_plan_binding"])
    return top_level


def _binding_error_code(errors: list[str]) -> str:
    text = ";".join(errors)
    if "contract_action_plan_not_used" in text:
        return RUNTIME_CONTRACT_ACTION_PLAN_NOT_USED
    if "runtime_bypassed_action_plan" in text:
        return RUNTIME_CONTRACT_ACTION_PLAN_BYPASSED
    if "forbidden_action_used" in text or "action_algorithm_not_allowed_by_plan" in text:
        return RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED
    if "missing_contract_action_plan" in text:
        return RUNTIME_CONTRACT_ACTION_PLAN_MISSING
    return RUNTIME_CONTRACT_ACTION_PLAN_BINDING_INVALID


def _contract_action_done_without_actual(record: dict[str, Any]) -> bool:
    action = record.get("contract_action")
    if not isinstance(action, dict) or action.get("action_done") is not True:
        return False
    actual = record.get("contract_actual")
    return actual in (None, "", {}, [])
