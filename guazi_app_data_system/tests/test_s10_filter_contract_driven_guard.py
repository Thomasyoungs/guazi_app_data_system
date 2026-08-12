from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guazi_app_data_system.page_contract_execution_plan import build_s10_filter_summary_action_plan  # noqa: E402
from guazi_app_data_system.runtime_contract_guard import (  # noqa: E402
    S10_FILTER_SUMMARY_CONTRACT_MISMATCH,
    guard_filter_summary,
)


class S10FilterContractDrivenGuardTest(unittest.TestCase):
    def test_s10_action_plan_contains_expected_filter_summary(self):
        expected = {
            "brand": "别克",
            "series": "君越",
            "color": "黑色",
            "age_filter": "5-5",
            "model_config_core": "652T 豪华型",
        }

        plan = build_s10_filter_summary_action_plan(expected)

        self.assertEqual(plan["step_id"], "S10_FILTER_SUMMARY")
        self.assertEqual(plan["expected"]["expected_filter_summary"], expected)
        self.assertIn("verify_filter_summary", plan["allowed_actions"])
        self.assertEqual(plan["failure_stop_code"], S10_FILTER_SUMMARY_CONTRACT_MISMATCH)

    def test_s10_summary_blue_but_expected_black_stops(self):
        record = guard_filter_summary(
            stage="S10",
            expected_filters={"brand": "别克", "series": "君越", "color": "黑色", "age_filter": "5"},
            summary={"chips": ["别克", "君越", "蓝色", "5年"]},
        )

        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], S10_FILTER_SUMMARY_CONTRACT_MISMATCH)
        self.assertEqual(record["contract_action_plan"]["step_id"], "S10_FILTER_SUMMARY")


if __name__ == "__main__":
    unittest.main()
