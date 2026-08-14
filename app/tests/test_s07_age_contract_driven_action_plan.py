from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guazi_app_data_system.page_contract_execution_plan import (  # noqa: E402
    build_s07_age_action_plan,
    compute_visible_tick_target_x,
)
from guazi_app_data_system.runtime_contract_guard import (  # noqa: E402
    S07_AGE_FILTER_CONTRACT_MISMATCH,
    guard_s07_age,
)


class S07AgeContractDrivenActionPlanTest(unittest.TestCase):
    def test_registration_2021_08_generates_expected_5_5_plan(self):
        plan = build_s07_age_action_plan(
            registration_date="2021.08",
            current_year=2026,
            visible_ticks=[
                {"age": 4, "center_x": 500, "center_y": 680},
                {"age": 6, "center_x": 700, "center_y": 680},
                {"label": "不限", "center_x": 1200, "center_y": 680},
            ],
        )

        self.assertEqual(plan["expected"]["target_age_years"], 5)
        self.assertEqual(plan["expected"]["expected_age_filter"], "5-5")
        self.assertEqual(plan["expected"]["left_slider_target"], 5)
        self.assertEqual(plan["expected"]["right_slider_target"], 5)
        self.assertEqual(plan["action_outputs"]["left_target_x"], 600)
        self.assertEqual(plan["action_outputs"]["right_target_x"], 600)
        self.assertEqual(plan["action_outputs"]["left_target_x"], plan["action_outputs"]["right_target_x"])

    def test_age_plan_uses_neutral_forbidden_names_for_legacy_algorithms(self):
        plan = build_s07_age_action_plan(target_age_years=5)

        self.assertIn("legacy_age_slider_unbounded_track_ratio", plan["forbidden_actions"])
        self.assertIn("legacy_age_slider_off_by_one_target", plan["forbidden_actions"])
        self.assertEqual(plan["action_algorithm"]["target_x_algorithm"], "visible_tick_interpolation")
        self.assertTrue(plan["action_algorithm"]["exclude_unlimited_tick"])

    def test_visible_tick_4_6_interpolation_for_age_5(self):
        output = compute_visible_tick_target_x(
            [
                {"age": 4, "center_x": 500, "center_y": 680},
                {"age": 6, "center_x": 700, "center_y": 680},
                {"label": "不限", "center_x": 1200, "center_y": 680},
            ],
            5,
        )

        self.assertEqual(output["target_x"], 600)
        self.assertEqual(output["calculation"], "visible_tick_interpolation")
        self.assertTrue(output["excluded_unlimited_tick"])
        self.assertEqual(output["lower_tick"]["age"], 4)
        self.assertEqual(output["upper_tick"]["age"], 6)

    def test_final_5_6_is_contract_mismatch(self):
        record = guard_s07_age(
            target_age_years=5,
            actual_age_filter="5-6年",
            actual_left_age=5,
            actual_right_age=6,
        )

        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], S07_AGE_FILTER_CONTRACT_MISMATCH)
        self.assertEqual(record["contract_action_plan"]["expected"]["expected_age_filter"], "5-5")


if __name__ == "__main__":
    unittest.main()
