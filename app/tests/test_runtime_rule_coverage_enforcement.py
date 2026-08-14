import unittest

from guazi_app_data_system.runtime_contract_guard import build_contract_record
from guazi_app_data_system.runtime_rule_coverage import (
    build_runtime_rule_coverage_report,
    coverage_for_stage,
    load_runtime_rule_coverage,
    validate_runtime_records_against_coverage,
)


def _record(stage, action="verify_contract"):
    return build_contract_record(
        stage=stage,
        expected={"ok": True},
        action={"action": action},
        actual={"ok": True},
        source_version="PAGE_CONTRACT_RUNTIME_COVERAGE_MATRIX_AND_EXECUTION_COVERAGE_ENFORCEMENT_PATCH",
        source_file="config/page_contract_runtime_coverage.yaml",
    )


class RuntimeRuleCoverageEnforcementTest(unittest.TestCase):
    def test_every_critical_runtime_action_has_rule_clause_id(self):
        stages = [
            "S03",
            "S05",
            "S07_COLOR",
            "S07_AGE",
            "S08_FILTER_SUMMARY",
            "S10_FILTER_SUMMARY",
            "S10_REFERENCE_CARD_BINDING",
            "S11_REPORT_ENTRY",
            "S13_REPAIR_COUNT",
            "S13_REPAIR_ENTRY_BINDING",
            "S14_COLLECTION",
            "S14_RETURN_TO_S10",
            "S15_SCORING_RULE",
            "S15_REFERENCE_SELECTION_V3",
            "S16_PRICING_RULE",
            "DISPATCHER_REFERENCE_CONTINUATION",
            "FEISHU_USER_FEEDBACK",
        ]
        records = [_record(stage) for stage in stages]
        missing = [record.get("stage") for record in records if not record.get("rule_clause_id")]
        self.assertEqual([], missing)

    def test_no_runtime_action_without_contract_action_plan(self):
        record = _record("S07_AGE")
        for key in ("contract_action_plan_id", "contract_action_plan", "action_plan_id"):
            record.pop(key, None)
        record["contract_action_plan_used"] = False
        report = validate_runtime_records_against_coverage([record])
        self.assertTrue(any("runtime_action_without_contract_action_plan" in item for item in report["errors"]))

    def test_s07_age_fallback_must_be_allowed_by_clause(self):
        record = _record("S07_AGE")
        record.update({"fallback_used": True, "fallback_name": "long_press_drag", "fallback_allowed_by_clause": False})
        report = validate_runtime_records_against_coverage([record])
        self.assertTrue(any("fallback_not_allowed_by_clause" in item for item in report["errors"]))

    def test_s07_age_performance_budget_exceeded_flags_failure(self):
        record = _record("S07_AGE")
        record.update({"performance_budget_ms": 5000, "actual_duration_ms": 5200, "performance_budget_exceeded": True})
        report = validate_runtime_records_against_coverage([record])
        self.assertEqual("S07_AGE", report["performance_budget_exceeded_stages"][0]["stage"])

    def test_s07_age_direct_success_does_not_run_legacy_fallback(self):
        record = _record("S07_AGE")
        record.update(
            {
                "action_algorithm_used": "visible_tick_interpolation",
                "direct_fastpath_used": True,
                "fallback_used": False,
                "performance_budget_exceeded": False,
            }
        )
        report = validate_runtime_records_against_coverage([record])
        self.assertEqual([], report["errors"])

    def test_s11_screenshot_detector_is_never_authorized(self):
        record = _record("S11_REPORT_ENTRY")
        record.update(
            {
                "fallback_used": True,
                "fallback_name": "screenshot_dynamic_button_rect",
                "screenshot_detector_used": True,
                "xml_exact_attempted": False,
                "xml_text_missing": True,
            }
        )
        report = validate_runtime_records_against_coverage([record])
        self.assertIn("S11_REPORT_ENTRY_VISUAL_CLICK_NOT_AUTHORIZED_BY_PAGE_CONTRACT", report["errors"])
        self.assertTrue(any("fallback_not_allowed_by_clause" in item for item in report["errors"]))

    def test_s11_screenshot_click_source_is_rejected_even_when_xml_was_attempted(self):
        record = _record("S11_REPORT_ENTRY")
        record.update(
            {
                "click_source": "screenshot_dynamic_button_rect",
                "s11_report_entry_click_source": "screenshot_dynamic_button_rect",
                "screenshot_used_for_click": True,
                "xml_exact_attempted": True,
                "xml_stale": False,
                "xml_text_missing": False,
                "view_full_report_seen_in_xml": True,
            }
        )
        report = validate_runtime_records_against_coverage([record])
        self.assertIn("S11_REPORT_ENTRY_VISUAL_CLICK_NOT_AUTHORIZED_BY_PAGE_CONTRACT", report["errors"])

    def test_s11_performance_budget_exceeded_flags_failure(self):
        record = _record("S11_REPORT_ENTRY")
        record.update({"performance_budget_ms": 10000, "actual_duration_ms": 10500, "performance_budget_exceeded": True})
        report = validate_runtime_records_against_coverage([record])
        self.assertEqual("S11_REPORT_ENTRY", report["performance_budget_exceeded_stages"][0]["stage"])

    def test_reference_early_exit_without_source_rule_fails(self):
        record = _record("S15_REFERENCE_SELECTION_V3")
        record.update({"early_exit_allowed": True, "early_exit_rule_clause_id": ""})
        report = validate_runtime_records_against_coverage([record])
        self.assertIn("reference_early_exit_without_source_rule", report["errors"])

    def test_reference_early_exit_source_rule_required_before_runtime_action(self):
        clause = coverage_for_stage("REFERENCE_EARLY_EXIT")
        self.assertNotEqual("NEEDS_SOURCE_RULE", clause.get("coverage_status"))
        coverage = load_runtime_rule_coverage()
        proposal = [
            item
            for item in coverage["clauses"]
            if item["rule_clause_id"] == "V33_S14_LOW_SCORE_UPPER_BOUND_SKIP_AND_RECOLLECT_CONTRACT"
        ][0]
        self.assertNotEqual("NEEDS_SOURCE_RULE", proposal["coverage_status"])
        self.assertEqual(
            "V33_S14_LOW_SCORE_UPPER_BOUND_SKIP_AND_RECOLLECT_CONTRACT",
            proposal.get("rule_clause_id"),
        )

    def test_runtime_rule_coverage_score_generated(self):
        report = build_runtime_rule_coverage_report([])
        self.assertIn("coverage_score", report)
        self.assertGreater(report["coverage_score"], 0)

    def test_runtime_contract_execution_check_detects_uncovered_action(self):
        record = _record("S07_COLOR")
        record.pop("rule_clause_id", None)
        report = validate_runtime_records_against_coverage([record])
        self.assertTrue(report["runtime_actions_without_clause"])

    def test_existing_success_paths_not_regressed(self):
        records = [_record("S07_COLOR"), _record("S07_AGE"), _record("S11_REPORT_ENTRY")]
        records[1].update({"action_algorithm_used": "visible_tick_interpolation", "fallback_used": False})
        records[2].update({"xml_exact_attempted": True, "xml_exact_success": True, "fallback_used": False})
        report = validate_runtime_records_against_coverage(records)
        self.assertEqual([], report["errors"])

    def test_existing_needs_review_paths_not_regressed(self):
        record = _record("S14_COLLECTION")
        record.update({"continue_allowed": False, "contract_match": False, "contract_stop_code": "S13_S14_COLLECTION_CONTRACT_MISMATCH"})
        report = validate_runtime_records_against_coverage([record])
        self.assertEqual([], report["errors"])


if __name__ == "__main__":
    unittest.main()
