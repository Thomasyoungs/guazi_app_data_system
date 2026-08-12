import unittest

from guazi_app_data_system.reference_early_exit import (
    EARLY_EXIT_RULE_ID,
    evaluate_reference_early_exit_max_possible_score,
    reference_can_participate_in_v3_selection,
)


ACTIVE_VERSIONS = {
    "active_page_contract_version": "V1.50",
    "active_scoring_rule_version": "V1.11",
    "active_reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
    "active_pricing_rule_version": "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
    "active_competition_coefficient_version": "V1.2.6",
}


class ReferenceEarlyExitSourceBackedTest(unittest.TestCase):
    def test_early_exit_requires_v147_v111_source_rule(self):
        decision = evaluate_reference_early_exit_max_possible_score(
            current_reference_index=4,
            next_reference_index=5,
            target_score=92,
            partial_confirmed_score=70,
            remaining_max_possible_score=10,
            pre_boundary_evidence={"reference_index": 2, "reference_score": 88},
            active_versions={**ACTIVE_VERSIONS, "active_scoring_rule_version": "V1.8"},
        )

        self.assertFalse(decision["early_exit_allowed"])
        self.assertIn("ACTIVE_RULE_SOURCE_VERSION_MISMATCH", decision["early_exit_blockers"])

    def test_early_exit_upper_bound_below_target(self):
        decision = evaluate_reference_early_exit_max_possible_score(
            current_reference_index=4,
            next_reference_index=5,
            target_score=92,
            partial_confirmed_score=70,
            remaining_max_possible_score=10,
            pre_boundary_evidence={"reference_index": 2, "reference_score": 88},
            active_versions=ACTIVE_VERSIONS,
        )

        self.assertTrue(decision["early_exit_allowed"])
        self.assertEqual(EARLY_EXIT_RULE_ID, decision["early_exit_rule_id"])
        self.assertEqual(80.0, decision["max_possible_reference_score"])
        self.assertFalse(decision["can_reach_target"])
        self.assertEqual("LOW_SCORE_SKIPPED_INCOMPLETE", decision["reference_status"])
        self.assertTrue(decision["low_score_skipped_incomplete"])
        self.assertTrue(decision["return_to_s10_required"])
        self.assertTrue(decision["continue_next_reference_required"])
        self.assertFalse(decision["enter_s16_allowed"])

    def test_early_exit_allowed_even_when_upper_bound_can_improve_old_pre_boundary(self):
        decision = evaluate_reference_early_exit_max_possible_score(
            current_reference_index=4,
            next_reference_index=5,
            target_score=92,
            partial_confirmed_score=86,
            remaining_max_possible_score=4,
            pre_boundary_evidence={"reference_index": 2, "reference_score": 88},
            active_versions=ACTIVE_VERSIONS,
        )

        self.assertTrue(decision["early_exit_allowed"])
        self.assertNotIn("MAX_POSSIBLE_CAN_IMPROVE_PRE_BOUNDARY", decision["early_exit_blockers"])

    def test_early_exit_without_best_pre_boundary(self):
        decision = evaluate_reference_early_exit_max_possible_score(
            current_reference_index=4,
            next_reference_index=5,
            target_score=92,
            partial_confirmed_score=70,
            remaining_max_possible_score=10,
            pre_boundary_evidence=None,
            active_versions=ACTIVE_VERSIONS,
        )

        self.assertTrue(decision["early_exit_allowed"])
        self.assertNotIn("PRE_BOUNDARY_EVIDENCE_NOT_REQUIRED", decision["early_exit_blockers"])

    def test_no_early_exit_when_mandatory_fields_missing(self):
        decision = evaluate_reference_early_exit_max_possible_score(
            current_reference_index=4,
            next_reference_index=5,
            target_score=92,
            partial_confirmed_score=70,
            remaining_max_possible_score=10,
            pre_boundary_evidence={"reference_index": 2, "reference_score": 88},
            active_versions=ACTIVE_VERSIONS,
            mandatory_fields_collected=False,
            mandatory_fields_missing=["s13_s14"],
        )

        self.assertFalse(decision["early_exit_allowed"])
        self.assertIn("MANDATORY_FIELDS_MISSING", decision["early_exit_blockers"])

    def test_early_exit_reference_not_used_in_v3_selection_roles(self):
        decision = evaluate_reference_early_exit_max_possible_score(
            current_reference_index=4,
            next_reference_index=5,
            target_score=92,
            partial_confirmed_score=70,
            remaining_max_possible_score=10,
            pre_boundary_evidence={"reference_index": 2, "reference_score": 88},
            active_versions=ACTIVE_VERSIONS,
        )

        self.assertTrue(decision["excluded_from_final_reference_selection"])
        self.assertFalse(decision["usable_for_boundary"])
        self.assertFalse(decision["usable_for_pre_boundary"])
        self.assertFalse(reference_can_participate_in_v3_selection(decision))

    def test_trusted_non_early_exit_reference_can_participate(self):
        reference = {
            "reference_early_exit": False,
            "excluded_from_final_reference_selection": False,
            "usable_for_boundary": True,
            "usable_for_pre_boundary": True,
            "reference_score_trustworthy": True,
            "reference_score_usable_for_boundary": True,
        }

        self.assertTrue(reference_can_participate_in_v3_selection(reference))


if __name__ == "__main__":
    unittest.main()
