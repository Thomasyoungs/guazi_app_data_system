from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guazi_app_data_system.runtime_contract_guard import (  # noqa: E402
    PAGE_CONTRACT_MISMATCH,
    PRICING_RULE_SOURCE_MISMATCH,
    REFERENCE_SELECTION_RULE_SOURCE_MISMATCH,
    SCORING_RULE_SOURCE_MISMATCH,
    S07_AGE_FILTER_CONTRACT_MISMATCH,
    S07_COLOR_CONTRACT_MISMATCH,
    S08_FILTER_SUMMARY_CONTRACT_MISMATCH,
    S10_FILTER_SUMMARY_CONTRACT_MISMATCH,
    S13_S14_COLLECTION_CONTRACT_MISMATCH,
    ContractGuardError,
    ensure_contract_match,
    guard_filter_summary,
    guard_pricing_rule,
    guard_reference_selection_rule,
    guard_s07_age,
    guard_s07_color,
    guard_s13_s14_collection,
    guard_scoring_rule,
)


class RuntimeContractExecutionGuardTest(unittest.TestCase):
    def test_s07_color_black_expected_blue_actual_stops(self):
        record = guard_s07_color(expected_color="黑色", selected_color="蓝色")
        self.assertFalse(record["contract_match"])
        self.assertEqual(record["canonical_contract_stop_code"], PAGE_CONTRACT_MISMATCH)
        self.assertEqual(record["contract_stop_code"], S07_COLOR_CONTRACT_MISMATCH)
        with self.assertRaises(ContractGuardError) as raised:
            ensure_contract_match(record)
        self.assertEqual(raised.exception.code, S07_COLOR_CONTRACT_MISMATCH)

    def test_s07_age_expected_5_5_actual_5_6_stops(self):
        record = guard_s07_age(target_age_years=5, actual_age_filter="5-6年", actual_left_age=5, actual_right_age=6)
        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], S07_AGE_FILTER_CONTRACT_MISMATCH)

    def test_s08_filter_summary_missing_color_stops(self):
        record = guard_filter_summary(
            stage="S08",
            expected_filters={"brand": "别克", "series": "君越", "color": "黑色"},
            summary="别克 君越 2021款",
        )
        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], S08_FILTER_SUMMARY_CONTRACT_MISMATCH)

    def test_s10_filter_summary_mismatch_stops(self):
        record = guard_filter_summary(
            stage="S10",
            expected_filters={"brand": "别克", "series": "君越", "color": "黑色"},
            summary="别克 君越 蓝色 2021款",
        )
        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], S10_FILTER_SUMMARY_CONTRACT_MISMATCH)

    def test_s13_s14_incomplete_reference_excluded_from_boundary(self):
        reference = {"reference_index": 2, "reference_score_usable_for_boundary": True}
        record = guard_s13_s14_collection(
            s13_total_repair_count=6,
            s14_collected_items_count=3,
            reference=reference,
        )
        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], S13_S14_COLLECTION_CONTRACT_MISMATCH)
        self.assertFalse(reference["reference_score_usable_for_boundary"])
        self.assertTrue(reference["excluded_from_boundary"])

    def test_contract_match_false_cannot_continue_next_stage(self):
        record = guard_s07_color(expected_color="黑色", selected_color="蓝色")
        record["continue_allowed"] = True
        with self.assertRaises(ContractGuardError) as raised:
            ensure_contract_match(record)
        self.assertEqual(raised.exception.code, "RUNTIME_CONTRACT_CONTINUED_AFTER_MISMATCH")

    def test_scoring_version_mismatch_stops(self):
        record = guard_scoring_rule(active_scoring_rule_version="V1.7", source_file="old.docx")
        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], SCORING_RULE_SOURCE_MISMATCH)

    def test_reference_selection_version_mismatch_stops(self):
        record = guard_reference_selection_rule(
            active_reference_selection_rule="V2_FIRST_MATCH",
            target_score=92,
            reference_scores=[90, 95],
        )
        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], REFERENCE_SELECTION_RULE_SOURCE_MISMATCH)

    def test_pricing_version_mismatch_stops(self):
        record = guard_pricing_rule(
            {
                "pricing": {
                    "suggested_purchase_price_yuan": 86000,
                    "profit_rate": 0.08,
                    "guazi_service_fee_yuan": 1500,
                    "guazi_return_price_yuan": 93000,
                    "cost_yuan": 2000,
                }
            },
            active_pricing_rule_version="LEGACY_95_PERCENT",
            service_fee_rule_version="LEGACY",
            competition_coefficient_version="V1.2.3",
        )
        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], PRICING_RULE_SOURCE_MISMATCH)

    def test_baseline_contracts_pass(self):
        color = guard_s07_color(expected_color="黑色", selected_color="黑色", s08_color="黑色", s10_color="黑色")
        age = guard_s07_age(target_age_years=5, actual_age_filter="5-5年", actual_left_age=5, actual_right_age=5)
        pricing = guard_pricing_rule(
            {
                    "pricing_rule_version": "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
                    "competition_coefficient_version": "V1.2.6",
                "pricing": {
                    "suggested_purchase_price_yuan": 72968,
                    "profit_rate": 0.08,
                    "target_guazi_listing_price_yuan": 83400,
                    "guazi_service_fee_yuan": 3000,
                    "guazi_return_price_yuan": 80400,
                    "cost_yuan": 1000,
                },
            },
            active_pricing_rule_version="V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
            service_fee_rule_version="V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
            competition_coefficient_version="V1.2.6",
        )
        self.assertTrue(color["contract_match"])
        self.assertTrue(age["contract_match"])
        self.assertTrue(pricing["contract_match"])

    def test_runtime_contract_execution_check_script_passes(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "runtime_contract_execution_check.py")],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("RUNTIME_CONTRACT_EXECUTION_CHECK_PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
