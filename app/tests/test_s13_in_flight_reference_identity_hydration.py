import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402


class S13InFlightReferenceIdentityHydrationTest(unittest.TestCase):
    def physical_proof(self, reference_index=4, signature="sig-ref-4"):
        return {
            "proof_version": runtime.REFERENCE_PHYSICAL_UI_TRANSITION_PROOF_VERSION,
            "transition_context": "S10_TO_S11",
            "reference_index": reference_index,
            "next_card_click_verified": True,
            "page_changed_after_click": True,
            "destination_identity_matched": True,
            "same_page_signature_reused": False,
            "actual_page_signature": signature,
            "physical_evidence_ok": True,
        }

    def first_stage_evidence(self):
        return {
            "ready": True,
            "same_source_cards": [
                {
                    "title": "Ref 1",
                    "list_price_10k": 15.80,
                    "list_year": 2021,
                    "list_mileage_10k_km": 4.8,
                    "raw_metadata": "2021 | 4.8w | Tangshan",
                },
                {
                    "title": "Ref 2",
                    "list_price_10k": 16.20,
                    "list_year": 2021,
                    "list_mileage_10k_km": 5.1,
                    "raw_metadata": "2021 | 5.1w | Tangshan",
                },
                {
                    "title": "Ref 3",
                    "list_price_10k": 16.58,
                    "list_year": 2021,
                    "list_mileage_10k_km": 4.9,
                    "raw_metadata": "2021 | 4.9w | Tangshan",
                },
                {
                    "title": "Ref 4",
                    "list_price_10k": 16.74,
                    "list_year": 2021,
                    "list_mileage_10k_km": 4.7,
                    "raw_metadata": "2021 | 4.7w | Tangshan",
                },
            ],
        }

    def test_s13_in_flight_current_reference_identity_hydrates_from_first_stage_card(self):
        context = {
            "current_reference_index": 4,
            "current_reference": {
                "s13_all_zero": True,
                "s13_all_zero_exit_trace": "S13_ALL_ZERO_RETURN_TO_S10_FOR_S15",
            },
            "first_stage_evidence": self.first_stage_evidence(),
        }

        trace = runtime._hydrate_current_reference_identity_for_in_flight_context(
            context,
            reason="unit_s13_in_flight",
        )

        self.assertTrue(trace["identity_hydration_ok"])
        self.assertTrue(trace["identity_complete_after"])
        self.assertEqual(4, context["current_reference"]["reference_index"])
        self.assertEqual(16.74, context["current_reference"]["list_price_10k"])
        self.assertEqual("16.74\u4e07", context["current_reference"]["selected_card_price"])
        self.assertEqual("2021 | 4.7w | Tangshan", context["current_reference"]["selected_card_metadata"])
        self.assertEqual("Ref 4", context["current_reference"]["selected_card_title"])

    def test_reference_history_write_blocks_hydration_only_without_physical_proof(self):
        context = {
            "current_reference_index": 4,
            "current_reference": {"reference_score": 70.0},
            "reference_history": [],
            "first_stage_evidence": self.first_stage_evidence(),
        }

        history, gate = runtime._safe_reference_history_with_current_reference(
            context,
            purpose="unit_continue_next_reference",
            require_identity=True,
        )

        self.assertEqual([], history)
        self.assertTrue(gate["reference_history_write_blocked"])
        self.assertEqual(runtime.REFERENCE_HISTORY_PHYSICAL_PROOF_MISSING, gate["reference_history_write_block_code"])
        self.assertEqual(4, context["current_reference"]["reference_index"])
        self.assertEqual("Ref 4", context["current_reference"]["selected_card_title"])

    def test_reference_history_write_hydrates_and_appends_with_physical_proof(self):
        context = {
            "current_reference_index": 4,
            "current_reference": {
                "reference_score": 70.0,
                "physical_ui_transition_proof": self.physical_proof(4),
                "actual_page_signature": "sig-ref-4",
            },
            "reference_history": [],
            "first_stage_evidence": self.first_stage_evidence(),
        }

        history, gate = runtime._safe_reference_history_with_current_reference(
            context,
            purpose="unit_continue_next_reference",
            require_identity=True,
        )

        self.assertFalse(gate["reference_history_write_blocked"])
        self.assertTrue(gate["current_reference_written"])
        self.assertTrue(gate["physical_ui_transition_proof_gate"]["physical_ui_transition_proof_ok"])
        self.assertEqual(1, len(history))
        self.assertEqual(4, history[0]["reference_index"])
        self.assertEqual("Ref 4", history[0]["selected_card_title"])
        self.assertEqual("2021 | 4.7w | Tangshan", history[0]["selected_card_metadata"])

    def test_reference_history_write_blocks_unrestorable_missing_identity(self):
        context = {
            "current_reference_index": 4,
            "current_reference": {"s13_all_zero": True},
            "reference_history": [],
            "first_stage_evidence": {"ready": True, "same_source_cards": []},
        }

        history, gate = runtime._safe_reference_history_with_current_reference(
            context,
            purpose="unit_unrestorable",
            require_identity=True,
        )

        self.assertEqual([], history)
        self.assertTrue(gate["reference_history_write_blocked"])
        self.assertEqual("REFERENCE_HISTORY_ENTRY_BLOCKED_BY_MISSING_REFERENCE_INDEX", gate["reference_history_write_block_code"])
        self.assertEqual("REFERENCE_HISTORY_ENTRY_BLOCKED_BY_MISSING_REFERENCE_INDEX", context["reference_history_entry_blocked_code"])

    def test_rejected_continuation_does_not_reset_to_index_one_when_page_is_in_flight(self):
        plan = {
            "continuation_mode": False,
            "next_reference_index": 1,
            "continuation_rejected_candidates": [
                {
                    "reject_code": "SECOND_STAGE_CONTINUATION_REJECTED_STALE_TASK_STATE",
                    "reject_reasons": ["REFERENCE_HISTORY_STALE_CONTAMINATION", "REFERENCE_INDEX_MISSING"],
                }
            ],
        }

        self.assertTrue(runtime._second_stage_in_flight_continuation_reset_blocked("S13", plan))
        self.assertTrue(runtime._second_stage_in_flight_continuation_reset_blocked("S14", plan))
        self.assertFalse(runtime._second_stage_in_flight_continuation_reset_blocked("S10", plan))


if __name__ == "__main__":
    unittest.main()
