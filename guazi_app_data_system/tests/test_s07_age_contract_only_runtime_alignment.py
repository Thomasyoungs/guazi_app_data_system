import unittest

from guazi_app_data_system.page_contract_execution_plan import (
    build_s07_age_action_plan,
    compute_visible_tick_target_x,
)
from guazi_app_data_system.runtime_rule_coverage import coverage_for_stage


class S07AgeContractOnlyRuntimeAlignmentTest(unittest.TestCase):
    def test_s07_age_plan_allows_only_source_backed_algorithms(self):
        plan = build_s07_age_action_plan(target_age_years=5)

        for action in (
            "visible_tick_interpolation",
            "exact_tick_binding",
            "direct_track_fastpath",
            "direct_track_fastpath_5_5",
            "text_result_verify_first",
        ):
            self.assertIn(action, plan["allowed_actions"])
        for forbidden in (
            "right_first",
            "long_press_drag",
            "segmented_drag",
            "track_based_drag",
            "legacy_age_slider_unbounded_track_ratio",
            "legacy_age_slider_off_by_one_target",
        ):
            self.assertIn(forbidden, plan["forbidden_actions"])

    def test_s07_age_coverage_has_no_legacy_fallbacks(self):
        clause = coverage_for_stage("S07_AGE")

        self.assertEqual([], clause.get("allowed_fallbacks"))
        for forbidden in (
            "right_first_without_source_clause",
            "long_press_drag_without_source_clause",
            "segmented_drag_without_source_clause",
            "track_based_drag_without_source_clause",
            "legacy_age_slider_unbounded_track_ratio",
        ):
            self.assertIn(forbidden, clause.get("forbidden_actions"))

    def test_visible_tick_interpolation_calculates_5_between_4_and_6(self):
        result = compute_visible_tick_target_x(
            [
                {"label": "4", "center_x": 500, "center_y": 680},
                {"label": "6", "center_x": 700, "center_y": 680},
            ],
            5,
        )

        self.assertEqual(600, result["target_x"])
        self.assertEqual("visible_tick_interpolation", result["calculation"])
        self.assertEqual(0.5, result["ratio_between_ticks"])

    def test_s07_age_left_slider_timing_fields_declared_in_coverage(self):
        clause = coverage_for_stage("S07_AGE")

        evidence_fields = clause.get("evidence_fields") or []
        for field in (
            "age_panel_wait_ms",
            "left_slider_bind_ms",
            "right_slider_bind_ms",
            "drag_ms",
            "verify_ms",
            "fallback_ms",
            "xml_dump_count",
            "screenshot_count",
            "fallback_strategies_used",
        ):
            self.assertIn(field, evidence_fields)


if __name__ == "__main__":
    unittest.main()
