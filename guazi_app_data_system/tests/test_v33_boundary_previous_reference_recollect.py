import unittest

from guazi_app_data_system.models import ReferenceCar
from guazi_app_data_system.pricing import (
    REFERENCE_SELECTION_RULE,
    ScoreResult,
    _select_v3_boundary_reference,
)
from guazi_app_data_system.reference_early_exit import (
    EXPECTED_COMPETITION_VERSION,
    EXPECTED_PAGE_CONTRACT_VERSION,
    EXPECTED_PRICING_RULE_VERSION,
    EXPECTED_REFERENCE_RULE,
    EXPECTED_SCORING_RULE_VERSION,
    evaluate_reference_early_exit_max_possible_score,
)
from scripts.runtime_s10_to_s16_mainline import (
    V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
    _select_v3_reference_from_history,
    _v33_low_score_skip_continue_fields,
    _v33_recollect_needs_review_result,
    _v33_recollect_terminal_reference_is_trusted,
)


ACTIVE_V33 = {
    "active_page_contract_version": EXPECTED_PAGE_CONTRACT_VERSION,
    "active_scoring_rule_version": EXPECTED_SCORING_RULE_VERSION,
    "active_reference_selection_rule": EXPECTED_REFERENCE_RULE,
    "active_pricing_rule_version": EXPECTED_PRICING_RULE_VERSION,
    "active_competition_coefficient_version": EXPECTED_COMPETITION_VERSION,
}


def ref(index: int, price_10k: float = 8.0) -> ReferenceCar:
        return ReferenceCar(
            reference_index=index,
            list_price_10k=price_10k,
            list_year=2021,
            list_mileage_10k_km=5.0,
            transfer_count=0,
            accident_count=0,
            max_accident_amount=0,
            repair_counts={},
        )


class V33BoundaryPreviousReferenceRecollectTests(unittest.TestCase):
    def test_exact_boundary_still_uses_previous_reference(self):
        selection = _select_v3_boundary_reference(
            [
                (ref(1, 7.2), ScoreResult(90, {}, [])),
                (ref(2, 8.0), ScoreResult(92, {}, [])),
            ],
            target_value=92,
        )

        self.assertEqual(REFERENCE_SELECTION_RULE, "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT")
        self.assertTrue(selection["boundary_confirmed"])
        self.assertEqual(selection["boundary_reference_index"], 2)
        self.assertEqual(selection["final_reference_candidate_index"], 1)
        self.assertEqual(selection["final_reference_index"], 1)
        self.assertEqual(selection["final_reference_selection_reason"], "boundary_previous_reference_complete_trustworthy")

    def test_first_boundary_without_previous_reference_requires_manual_review(self):
        selection = _select_v3_boundary_reference([(ref(1, 8.0), ScoreResult(93, {}, []))], target_value=92)

        self.assertTrue(selection["manual_review_required"])
        self.assertFalse(selection["auto_pricing_allowed"])
        self.assertEqual(selection["manual_review_reason"], "FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE")
        self.assertIsNone(selection["final_reference_index"])

    def test_no_boundary_never_auto_prices_closest_low(self):
        selection = _select_v3_boundary_reference(
            [
                (ref(1, 7.2), ScoreResult(88, {}, [])),
                (ref(2, 7.8), ScoreResult(91, {}, [])),
            ],
            target_value=92,
        )

        self.assertTrue(selection["manual_review_required"])
        self.assertFalse(selection["auto_pricing_allowed"])
        self.assertEqual(selection["manual_review_reason"], "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING")
        self.assertIsNone(selection["selected_tuple"])
        self.assertIsNone(selection["final_reference_index"])

    def test_boundary_previous_skipped_requires_recollect_from_history(self):
        selection = _select_v3_reference_from_history(
            [
                {
                    "reference_index": 1,
                    "reference_score": 88,
                    "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                    "low_score_skipped_incomplete": True,
                    "reference_score_usable_for_boundary": False,
                    "reference_score_trustworthy": False,
                },
                {
                    "reference_index": 2,
                    "reference_score": 94,
                    "list_price_10k": 8.3,
                    "list_year": 2021,
                    "list_mileage_10k_km": 5.0,
                    "transfer_count": 0,
                    "reference_score_usable_for_boundary": True,
                    "reference_score_trustworthy": True,
                },
            ],
            {"score": 92},
        )

        self.assertTrue(selection["boundary_confirmed"])
        self.assertFalse(selection["auto_pricing_allowed"])
        self.assertTrue(selection["final_reference_recollect_required"])
        self.assertEqual(selection["recollect_reference_index"], 1)
        self.assertEqual(selection["final_reference_candidate_status"], "LOW_SCORE_SKIPPED_INCOMPLETE")
        self.assertIsNone(selection["final_reference_index"])

    def test_v33_low_score_skip_does_not_require_best_pre_boundary(self):
        decision = evaluate_reference_early_exit_max_possible_score(
            current_reference_index=2,
            target_score=92,
            partial_confirmed_score=90,
            remaining_max_possible_score=0,
            pre_boundary_evidence=None,
            next_reference_index=3,
            active_versions=ACTIVE_V33,
            target_score_trustworthy=True,
            reference_order_reliable=True,
            mandatory_fields_collected=True,
            partial_confirmed_score_trustworthy=True,
            remaining_max_possible_score_deterministic=True,
            no_unconfirmed_hard_risk=True,
            can_return_reliable_s10=True,
        )

        self.assertTrue(decision["early_exit_allowed"])
        self.assertEqual(decision["reference_status"], "LOW_SCORE_SKIPPED_INCOMPLETE")
        self.assertEqual(decision["s14_low_score_skip_reason"], "UPPER_BOUND_BELOW_TARGET")
        self.assertNotIn("PRE_BOUNDARY_EVIDENCE_NOT_REQUIRED", decision["early_exit_blockers"])

    def test_ordinary_low_score_skip_still_continues_before_boundary(self):
        context = {
            "target_score": {"score": 92},
            "current_reference": {
                "reference_index": 3,
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                "s14_low_score_skip_triggered": True,
                "reference_score_upper_bound": 89,
                "return_to_s10_after_low_score_skip": True,
                "returned_list_source_verified": True,
                "next_reference_index": 4,
            },
        }

        result = _v33_low_score_skip_continue_fields(context)

        self.assertIsNotNone(result)
        self.assertEqual(result["issue_code"], "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE")
        self.assertEqual(result["next_reference_index"], 4)
        self.assertTrue(result["continue_next_reference"])

    def test_boundary_previous_recollect_context_blocks_low_score_continue_to_boundary(self):
        context = {
            "target_score": {"score": 92},
            "continuation_plan": {
                "final_reference_recollect_required": True,
                "recollect_reference_index": 3,
                "boundary_reference_index": 4,
                "final_reference_candidate_index": 3,
                "boundary_reference_score": 93,
                "target_score": 92,
                "recollect_reason": "BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
            },
            "current_reference": {
                "reference_index": 3,
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                "s14_low_score_skip_triggered": True,
                "reference_score_upper_bound": 89,
                "return_to_s10_after_low_score_skip": True,
                "returned_list_source_verified": True,
                "next_reference_index": 4,
            },
        }

        result = _v33_low_score_skip_continue_fields(context)

        self.assertIsNone(result)
        trace = context["v33_recollect_terminal_trace"]
        self.assertTrue(trace["v33_recollect_terminal_context"])
        self.assertTrue(trace["v33_recollect_blocked_low_score_continue"])
        self.assertTrue(trace["v33_recollect_prevented_next_boundary_reclick"])

    def test_recollected_previous_reference_complete_is_trusted_final_candidate(self):
        trusted = _v33_recollect_terminal_reference_is_trusted(
            reference_valid_for_boundary=True,
            current_reference={
                "s15_entry_allowed": True,
                "reference_score_trustworthy": True,
                "reference_score_usable_for_boundary": True,
            },
            reference_score=ScoreResult(91, {}, []),
        )

        self.assertTrue(trusted)

    def test_recollected_previous_reference_still_incomplete_becomes_needs_review_terminal(self):
        context = {
            "target_score": ScoreResult(92, {}, []),
            "current_reference": {
                "reference_index": 3,
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
            },
            "selection": {},
        }
        trace = {
            "v33_recollect_terminal_reference_index": 3,
            "v33_recollect_terminal_boundary_reference_index": 4,
            "v33_final_reference_candidate_index": 3,
            "v33_recollect_reference_index": 3,
            "v33_recollect_terminal_candidate_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
            "boundary_reference_score": 93,
            "target_score": 92,
            "recollect_reason": "BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        }

        result = _v33_recollect_needs_review_result(
            context,
            trace=trace,
            continue_history=[],
            continue_history_gate={"ok": True},
        )

        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertEqual(result["issue_code"], V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW)
        self.assertEqual(result["selected_reference_index"], 3)
        self.assertEqual(result["boundary_reference_index"], 4)
        self.assertEqual(result["boundary_reference_score"], 93)
        self.assertEqual(result["target_score_value"], 92)
        self.assertFalse(result["auto_pricing_allowed"])
        self.assertFalse(result["final_price_allowed"])
        self.assertTrue(result["v33_recollect_terminal_trace"]["v33_recollect_blocked_low_score_continue"])


if __name__ == "__main__":
    unittest.main()
