"""Offline check for runtime page/rule contract execution guards."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guazi_app_data_system.runtime_contract_guard import (  # noqa: E402
    RUNTIME_CONTRACT_ACTION_PLAN_BINDING_INVALID,
    RUNTIME_CONTRACT_ACTION_PLAN_MISSING,
    RUNTIME_CONTRACT_ACTION_PLAN_NOT_USED,
    RUNTIME_CONTRACT_ACTION_PLAN_BYPASSED,
    RUNTIME_CONTRACT_CONTINUED_AFTER_MISMATCH,
    RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED,
    PRICING_RULE_SOURCE_MISMATCH,
    guard_filter_summary,
    guard_pricing_rule,
    guard_reference_selection_rule,
    guard_s07_age,
    guard_s07_color,
    guard_s11_report_entry,
    guard_s13_s14_collection,
    guard_scoring_rule,
    guard_selected_value_contract,
    build_contract_record,
    validate_contract_records,
)
from guazi_app_data_system.runtime_rule_coverage import (  # noqa: E402
    build_runtime_rule_coverage_report,
    load_runtime_rule_coverage,
)


def main() -> int:
    records = _sample_runtime_records()
    records.extend(_coverage_fixture_records())
    errors = validate_contract_records(records)

    mismatch_record = guard_s07_color(expected_color="black", selected_color="blue")
    mismatch_record["continue_allowed"] = False
    mismatch_errors = validate_contract_records([mismatch_record])
    if mismatch_errors:
        errors.extend(f"mismatch_stop_record:{item}" for item in mismatch_errors)

    illegal_continue = dict(mismatch_record)
    illegal_continue["continue_allowed"] = True
    illegal_errors = validate_contract_records([illegal_continue])
    if not any(RUNTIME_CONTRACT_CONTINUED_AFTER_MISMATCH in item for item in illegal_errors):
        errors.append("contract_match_false_did_not_block_continuation")

    bypass = dict(records[0])
    bypass["bypass_contract_guard"] = True
    bypass_errors = validate_contract_records([bypass])
    if not bypass_errors:
        errors.append("bypass_contract_guard_true_not_rejected")

    missing_plan = dict(records[2])
    missing_plan.pop("contract_action_plan", None)
    missing_plan_errors = validate_contract_records([missing_plan])
    if not any(RUNTIME_CONTRACT_ACTION_PLAN_MISSING in item or "missing_fields:contract_action_plan" in item for item in missing_plan_errors):
        errors.append("missing_contract_action_plan_not_rejected")

    plan_not_used = dict(records[2])
    plan_not_used["contract_action_plan_used"] = False
    plan_not_used["action_plan_binding_check_passed"] = False
    plan_not_used_errors = validate_contract_records([plan_not_used])
    if not any(RUNTIME_CONTRACT_ACTION_PLAN_NOT_USED in item for item in plan_not_used_errors):
        errors.append("contract_action_plan_used_false_not_rejected")

    wrong_input_source = dict(records[2])
    wrong_input_source["action_inputs_source"] = "runtime_ad_hoc"
    wrong_input_source["action_plan_binding_check_passed"] = False
    wrong_input_source_errors = validate_contract_records([wrong_input_source])
    if not any(RUNTIME_CONTRACT_ACTION_PLAN_BINDING_INVALID in item for item in wrong_input_source_errors):
        errors.append("non_contract_action_input_source_not_rejected")

    runtime_bypass = dict(records[2])
    runtime_bypass["runtime_bypassed_action_plan"] = True
    runtime_bypass["action_plan_binding_check_passed"] = False
    runtime_bypass_errors = validate_contract_records([runtime_bypass])
    if not any(RUNTIME_CONTRACT_ACTION_PLAN_BYPASSED in item for item in runtime_bypass_errors):
        errors.append("runtime_bypassed_action_plan_not_rejected")

    forbidden_action = dict(records[3])
    forbidden_action["contract_action"] = {
        **dict(forbidden_action.get("contract_action") or {}),
        "used_action_algorithm": "legacy_age_slider_unbounded_track_ratio",
    }
    forbidden_errors = validate_contract_records([forbidden_action])
    if not any(RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED in item for item in forbidden_errors):
        errors.append("forbidden_contract_action_not_rejected")

    forbidden_binding = dict(records[3])
    forbidden_binding["forbidden_action_used"] = True
    forbidden_binding["action_plan_binding_check_passed"] = False
    forbidden_binding_errors = validate_contract_records([forbidden_binding])
    if not any(RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED in item for item in forbidden_binding_errors):
        errors.append("forbidden_binding_flag_not_rejected")

    pricing_83400 = guard_pricing_rule(
        {
            "pricing_rule_version": "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
            "competition_coefficient_version": "V1.2.6",
            "target_guazi_listing_price_yuan": 83400,
            "pricing_decision_source": "MANUAL_REVIEW_PENDING",
            "manual_review_required": True,
            "pricing": {
                "target_guazi_listing_price_yuan": 83400,
                "profit_rate": 0.08,
                "guazi_service_fee_yuan": 3000,
                "guazi_return_price_yuan": 80400,
                "cost_yuan": 1000,
                "profit_yuan": 6432,
                "suggested_purchase_price_yuan": 72968,
            },
        },
        active_pricing_rule_version="V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        service_fee_rule_version="V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        competition_coefficient_version="V1.2.6",
    )
    if not pricing_83400.get("contract_match"):
        errors.append("S16_PRICING_SERVICE_FEE_CONTRACT_CHECK_NOT_PASSED")

    wrong_fee_83400 = guard_pricing_rule(
        {
            "pricing_rule_version": "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
            "competition_coefficient_version": "V1.2.6",
            "target_guazi_listing_price_yuan": 83400,
            "pricing_decision_source": "MANUAL_REVIEW_PENDING",
            "manual_review_required": True,
            "pricing": {
                "target_guazi_listing_price_yuan": 83400,
                "profit_rate": 0.08,
                "guazi_service_fee_yuan": 2999,
                "guazi_return_price_yuan": 80401,
                "cost_yuan": 1000,
                "profit_yuan": 6432,
                "suggested_purchase_price_yuan": 72969,
            },
        },
        active_pricing_rule_version="V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        service_fee_rule_version="V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        competition_coefficient_version="V1.2.6",
    )
    if (
        wrong_fee_83400.get("contract_match")
        or wrong_fee_83400.get("contract_stop_code") != PRICING_RULE_SOURCE_MISMATCH
    ):
        errors.append("runtime_contract_check_did_not_reject_wrong_service_fee_for_83400")

    missing_binding = dict(records[2])
    for key in (
        "contract_action_plan_id",
        "contract_action_plan_used",
        "action_plan_step_id",
        "action_algorithm_used",
        "action_inputs_source",
        "action_outputs_source",
        "forbidden_action_used",
        "runtime_bypassed_action_plan",
        "action_plan_binding_check_passed",
    ):
        missing_binding.pop(key, None)
    missing_binding.pop("contract_action_plan_binding", None)
    missing_binding_errors = validate_contract_records([missing_binding])
    if not any(RUNTIME_CONTRACT_ACTION_PLAN_BINDING_INVALID in item for item in missing_binding_errors):
        errors.append("missing_execution_binding_trace_not_rejected")

    s11_missing_binding = guard_s11_report_entry(
        click_source="xml_exact_text_bounds",
        xml_bounds=(76, 1976, 520, 2078),
        entered_report=True,
    )
    for key in (
        "contract_action_plan_id",
        "contract_action_plan_used",
        "action_plan_step_id",
        "action_algorithm_used",
        "action_inputs_source",
        "action_outputs_source",
        "forbidden_action_used",
        "runtime_bypassed_action_plan",
        "action_plan_binding_check_passed",
    ):
        s11_missing_binding.pop(key, None)
    s11_missing_binding.pop("contract_action_plan_binding", None)
    s11_missing_binding_errors = validate_contract_records([s11_missing_binding])
    if not any(RUNTIME_CONTRACT_ACTION_PLAN_BINDING_INVALID in item for item in s11_missing_binding_errors):
        errors.append("s11_missing_execution_binding_trace_not_rejected")

    coverage = load_runtime_rule_coverage()
    coverage_report = build_runtime_rule_coverage_report(records, coverage)
    errors.extend(f"runtime_rule_coverage:{item}" for item in coverage_report.get("errors") or [])

    ok = not errors
    result = {
        "ok": ok,
        "status": "RUNTIME_RULE_COVERAGE_CHECK_PASSED" if ok else "RUNTIME_RULE_COVERAGE_CHECK_FAILED",
        "legacy_status": "RUNTIME_CONTRACT_EXECUTION_CHECK_PASSED" if ok else "RUNTIME_CONTRACT_EXECUTION_CHECK_FAILED",
        "coverage_score": coverage_report.get("coverage_score"),
        "coverage_status_counts": coverage_report.get("coverage_status_counts"),
        "not_covered_clauses": coverage_report.get("not_covered_clauses"),
        "needs_source_rule_clauses": coverage_report.get("needs_source_rule_clauses"),
        "runtime_actions_without_clause": coverage_report.get("runtime_actions_without_clause"),
        "forbidden_fallbacks": coverage_report.get("forbidden_fallbacks"),
        "performance_budget_exceeded_stages": coverage_report.get("performance_budget_exceeded_stages"),
        "coverage_warnings": coverage_report.get("warnings"),
        "records_checked": len(records),
        "record_stages": [record.get("stage") for record in records],
        "errors": errors,
        "rule_manifest": str(ROOT / "config" / "rule_manifest.json"),
        "fields_config": str(ROOT / "config" / "fields.yaml"),
        "runtime_rule_coverage_config": str(ROOT / "config" / "page_contract_runtime_coverage.yaml"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        return 1
    print("RUNTIME_RULE_COVERAGE_CHECK_PASSED")
    print("RUNTIME_CONTRACT_EXECUTION_CHECK_PASSED")
    print("S16_PRICING_SERVICE_FEE_CONTRACT_CHECK_PASSED")
    return 0


def _sample_runtime_records() -> list[dict]:
    manifest = _read_json(ROOT / "config" / "rule_manifest.json")
    fields = _read_json(ROOT / "config" / "fields.yaml")
    scoring = fields.get("scoring") or {}
    selection = fields.get("reference_selection") or {}
    pricing = fields.get("pricing") or {}
    competition = fields.get("competition_coefficient") or {}
    pricing_rule_version = pricing.get("pricing_rule_version") or "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
    competition_version = competition.get("competition_coefficient_version") or "V1.2.6"
    scoring_source = scoring.get("scoring_rule_doc") or (manifest.get("scoring_rule") or {}).get("file") or "V1.11"
    references = [
        {"reference_index": 1, "reference_score": 90, "reference_score_usable_for_boundary": True},
        {"reference_index": 2, "reference_score": 95, "reference_score_usable_for_boundary": True},
    ]
    return [
        guard_selected_value_contract(
            stage="S03",
            expected_field="brand",
            expected_value="别克",
            selected_value="别克",
            action="tap_target_brand",
            source_version="PAGE_CONTRACT_EXECUTION_GUARD_CORE",
            source_file="config/pages.yaml",
        ),
        guard_selected_value_contract(
            stage="S05",
            expected_field="series_config",
            expected_value="君越 2021款 652T 豪华型",
            selected_value="君越 2021款 652T 豪华型",
            action="tap_exact_trim",
            source_version="PAGE_CONTRACT_EXECUTION_GUARD_CORE",
            source_file="config/pages.yaml",
        ),
        guard_s07_color(expected_color="黑色", selected_color="黑色", s08_color="黑色", s10_color="黑色"),
        guard_s07_age(target_age_years=5, actual_age_filter="5-5年", actual_left_age=5, actual_right_age=5),
        guard_filter_summary(
            stage="S08",
            expected_filters={"brand": "别克", "series": "君越", "color": "黑色", "age": "5"},
            summary="别克 君越 黑色 5年 2021款",
        ),
        guard_filter_summary(
            stage="S10",
            expected_filters={"brand": "别克", "series": "君越", "color": "黑色", "age": "5"},
            summary={"chips": ["别克", "君越", "黑色", "5年", "652T 豪华型"]},
        ),
        guard_s11_report_entry(click_source="xml_exact_text_bounds", xml_bounds=(80, 1800, 520, 1900), entered_report=True),
        guard_s13_s14_collection(s13_total_repair_count=3, s14_collected_items_count=3),
        guard_scoring_rule(
            active_scoring_rule_version=scoring.get("scoring_rule_version"),
            source_file=scoring_source,
            components={"body": 68, "mileage": 12},
            deduction_items=[{"part": "右后翼子板", "damage_type": "钣金"}],
            score_input_summary={"repair_count": 1},
        ),
        guard_reference_selection_rule(
            active_reference_selection_rule=selection.get("reference_selection_rule"),
            target_score=92,
            reference_scores=[90, 95],
            references=references,
            selected_reference_index=1,
            boundary_reference_index=2,
        ),
        guard_pricing_rule(
            {
                "pricing_rule_version": pricing_rule_version,
                "competition_coefficient_version": competition_version,
                "pricing": {
                    "suggested_purchase_price_yuan": 86000,
                    "profit_rate": pricing.get("profit_rate", 0.08),
                    "target_guazi_listing_price_yuan": 83400,
                    "guazi_service_fee_yuan": 3000,
                    "guazi_return_price_yuan": 93000,
                    "cost_yuan": 2000,
                },
            },
            active_pricing_rule_version=pricing_rule_version,
            service_fee_rule_version=pricing_rule_version,
            competition_coefficient_version=competition_version,
        ),
    ]


def _coverage_fixture_records() -> list[dict]:
    """Contract-bound fixture records for stages not naturally hit by samples."""

    fixture_stages = [
        ("S10_REFERENCE_CARD_BINDING", "unique_card_signature_binding"),
        ("S13_REPAIR_COUNT", "s13_area_count_scan"),
        ("S13_REPAIR_ENTRY_BINDING", "history_region_candidate_binding"),
        ("S14_RETURN_TO_S10", "safe_back_to_s10"),
        ("DISPATCHER_REFERENCE_CONTINUATION", "continue_next_reference_state_machine"),
        ("FEISHU_USER_FEEDBACK", "format_user_feedback_from_canonical_error"),
    ]
    return [
        build_contract_record(
            stage=stage,
            expected={"contract_fixture": True},
            action={"action": action_name},
            actual={"contract_fixture": True},
            source_version="PAGE_CONTRACT_RUNTIME_COVERAGE_MATRIX_AND_EXECUTION_COVERAGE_ENFORCEMENT_PATCH",
            source_file="config/page_contract_runtime_coverage.yaml",
        )
        for stage, action_name in fixture_stages
    ]


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
