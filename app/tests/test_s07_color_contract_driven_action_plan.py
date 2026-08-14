from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guazi_app_data_system.page_contract_execution_plan import build_s07_color_action_plan  # noqa: E402
from guazi_app_data_system.runtime_contract_guard import (  # noqa: E402
    S07_COLOR_CONTRACT_MISMATCH,
    guard_s07_color,
)


class S07ColorContractDrivenActionPlanTest(unittest.TestCase):
    def test_target_black_plan_only_allows_exact_target_color_binding(self):
        plan = build_s07_color_action_plan(target_color="黑色")

        self.assertEqual(plan["expected"]["expected_color"], "黑色")
        self.assertEqual(plan["action_algorithm"]["candidate_match"], "exact_target_color_only")
        self.assertTrue(plan["action_algorithm"]["click_point_must_inside_candidate_bounds"])
        self.assertIn("fixed_coordinate", plan["forbidden_actions"])
        self.assertIn("ratio_coordinate", plan["forbidden_actions"])
        self.assertIn("candidate_visible_as_selected", plan["forbidden_actions"])

    def test_selected_blue_is_contract_mismatch_for_target_black(self):
        record = guard_s07_color(expected_color="黑色", selected_color="蓝色")

        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], S07_COLOR_CONTRACT_MISMATCH)
        self.assertEqual(record["contract_action_plan"]["expected"]["selected_after_click"], "黑色")

    def test_s10_blue_summary_is_contract_mismatch_for_target_black(self):
        record = guard_s07_color(expected_color="黑色", selected_color="黑色", s08_color="黑色", s10_color="蓝色")

        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], S07_COLOR_CONTRACT_MISMATCH)
        self.assertIn("verify_s08_s10_summary", record["contract_action_plan"]["allowed_actions"])


if __name__ == "__main__":
    unittest.main()
