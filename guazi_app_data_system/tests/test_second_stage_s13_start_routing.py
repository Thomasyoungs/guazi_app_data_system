import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402


class SecondStageS13StartRoutingTest(unittest.TestCase):
    def first_stage_evidence(self):
        return {
            "ready": True,
            "same_source_cards": [
                {
                    "title": "参考车 A",
                    "list_price_10k": 8.6,
                    "list_year": "2024",
                    "list_mileage_10k_km": 1.2,
                }
            ],
        }

    def test_s13_with_second_stage_context_routes_to_s13_executor(self):
        context = {"current_reference_index": 1, "current_reference": {}, "continuation_mode": True}

        evidence = runtime._second_stage_start_page_routing_evidence(
            recognized_state="S13",
            context=context,
            first_stage_evidence=self.first_stage_evidence(),
        )

        self.assertTrue(evidence["second_stage_context_valid"])
        self.assertTrue(evidence["in_flight_page_allowed"])
        self.assertEqual("handle_s13", evidence["selected_executor_name"])
        self.assertTrue(evidence["executor_registry_hit"])
        self.assertFalse(evidence["page_contract_executor_missing"])
        self.assertEqual("", evidence["contract_stop_code"])

    def test_s13_without_reference_context_gets_precise_context_stop(self):
        context = {"current_reference_index": 2, "current_reference": {}, "continuation_mode": True}

        evidence = runtime._second_stage_start_page_routing_evidence(
            recognized_state="S13",
            context=context,
            first_stage_evidence=self.first_stage_evidence(),
        )

        self.assertFalse(evidence["second_stage_context_valid"])
        self.assertFalse(evidence["in_flight_page_allowed"])
        self.assertFalse(evidence["page_contract_executor_missing"])
        self.assertEqual("S13_RECOGNIZED_BUT_SECOND_STAGE_CONTEXT_MISSING", evidence["contract_stop_code"])
        self.assertIn("first_stage_expected_reference_card_missing", evidence["context_missing_reasons"])

    def test_non_inflight_page_without_s10_uses_not_at_s10_scope(self):
        context = {"current_reference_index": 1, "current_reference": {}, "continuation_mode": False}

        evidence = runtime._second_stage_start_page_routing_evidence(
            recognized_state="UNKNOWN",
            context=context,
            first_stage_evidence=self.first_stage_evidence(),
        )

        self.assertFalse(evidence["in_flight_page_allowed"])
        self.assertTrue(evidence["page_contract_executor_missing"])
        self.assertEqual("MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY", evidence["contract_stop_code"])


if __name__ == "__main__":
    unittest.main()
