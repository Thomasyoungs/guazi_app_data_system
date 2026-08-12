import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from runtime_s10_to_s16_mainline import (  # noqa: E402
    _load_reference_continuation_plan,
    validate_reference_history_matches_current_s10_order,
    validate_second_stage_continuation_state_for_current_task,
)
from unittest.mock import patch


class SecondStageContinuationTaskIsolationTest(unittest.TestCase):
    def current_first_stage_evidence(self):
        return {
            "first_stage_result_digest": "first-digest-current",
            "s10_canonical_order_digest": "order-digest-current",
            "canonical_reference_order": [
                {
                    "reference_index": 1,
                    "list_title": "Buick LaCrosse 2021",
                    "list_price_10k": 7.30,
                    "raw_metadata": "2021-08 | 4.9w | Tangshan",
                },
                {
                    "reference_index": 2,
                    "list_title": "Buick LaCrosse 2021",
                    "list_price_10k": 8.45,
                    "raw_metadata": "2021-08 | 5.1w | Tangshan",
                },
            ],
        }

    def test_reference_history_must_match_current_s10_order(self):
        validation = validate_reference_history_matches_current_s10_order(
            [
                {
                    "reference_index": 1,
                    "list_title": "Buick LaCrosse 2021",
                    "list_price_10k": 7.05,
                    "raw_metadata": "2021-08 | 4.9w | Tangshan",
                }
            ],
            self.current_first_stage_evidence()["canonical_reference_order"],
        )

        self.assertFalse(validation["reference_history_current_task_valid"])
        self.assertEqual(validation["reject_reason"], "REFERENCE_HISTORY_STALE_CONTAMINATION")
        self.assertIn(1, validation["stale_reference_indices"])

    def test_current_task_continuation_is_accepted_when_identity_and_order_match(self):
        validation = validate_second_stage_continuation_state_for_current_task(
            task_id="FS20260627_0003",
            target_fingerprint="target-fp",
            first_stage_evidence=self.current_first_stage_evidence(),
            candidate_result={
                "task_id": "FS20260627_0003",
                "status": "CONTINUE_NEXT_REFERENCE",
                "target_fingerprint": "target-fp",
                "first_stage_result_digest": "first-digest-current",
                "s10_canonical_order_digest": "order-digest-current",
                "reference_history": [
                    {
                        "reference_index": 1,
                        "list_title": "Buick LaCrosse 2021",
                        "list_price_10k": 7.30,
                        "raw_metadata": "2021-08 | 4.9w | Tangshan",
                    }
                ],
            },
        )

        self.assertTrue(validation["continue_allowed"])
        self.assertEqual(validation["reject_reasons"], [])

    def test_missing_task_id_rejects_stale_continuation_output(self):
        validation = validate_second_stage_continuation_state_for_current_task(
            task_id="FS20260627_0003",
            target_fingerprint="target-fp",
            first_stage_evidence=self.current_first_stage_evidence(),
            candidate_result={
                "status": "CONTINUE_NEXT_REFERENCE",
                "target_fingerprint": "target-fp",
                "reference_history": [
                    {
                        "reference_index": 1,
                        "list_price_10k": 7.30,
                    }
                ],
            },
        )

        self.assertFalse(validation["continue_allowed"])
        self.assertEqual(validation["reject_code"], "SECOND_STAGE_CONTINUATION_REJECTED_STALE_TASK_STATE")
        self.assertIn("TASK_ID_MISSING", validation["reject_reasons"])

    def test_run_id_mismatch_rejects_when_current_run_is_supplied(self):
        validation = validate_second_stage_continuation_state_for_current_task(
            task_id="FS20260627_0003",
            target_fingerprint="target-fp",
            first_stage_evidence=self.current_first_stage_evidence(),
            current_run_id="run-current",
            candidate_result={
                "task_id": "FS20260627_0003",
                "run_id": "run-old",
                "status": "CONTINUE_NEXT_REFERENCE",
                "target_fingerprint": "target-fp",
                "reference_history": [
                    {
                        "reference_index": 1,
                        "list_price_10k": 7.30,
                    }
                ],
            },
        )

        self.assertFalse(validation["continue_allowed"])
        self.assertIn("RUN_ID_MISMATCH", validation["reject_reasons"])

    def test_stale_continuation_rejected_starts_from_reference_index_one(self):
        stale_result = {
            "status": "CONTINUE_NEXT_REFERENCE",
            "target_fingerprint": "别克|君越|2021款|652T 豪华型|黑|21.8",
            "reference_history": [{"reference_index": 1, "list_price_10k": 7.05}],
        }
        task_result = {
            "task_id": "FS20260627_0003",
            "brand": "别克",
            "series": "君越",
            "model_year": "2021款",
            "trim": "652T 豪华型",
            "color": "黑",
            "registration_date_raw": "21.8",
        }

        with patch("runtime_s10_to_s16_mainline._safe_read_json", side_effect=[stale_result, {}]):
            plan = _load_reference_continuation_plan(
                task_result,
                first_stage_evidence=self.current_first_stage_evidence(),
            )

        self.assertFalse(plan["continuation_mode"])
        self.assertEqual(plan["next_reference_index"], 1)
        self.assertEqual(plan["continuation_rejected_reason"], "SECOND_STAGE_CONTINUATION_REJECTED_STALE_TASK_STATE")


if __name__ == "__main__":
    unittest.main()
