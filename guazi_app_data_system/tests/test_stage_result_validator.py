import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from stage_result_validator import first_stage_s10_ready, validate_first_stage_payload  # noqa: E402


class StageResultValidatorTest(unittest.TestCase):
    def test_flow_state_s10_ready_true_is_success(self):
        payload = {"flow_state": {"S10_READY": True}}

        self.assertTrue(first_stage_s10_ready(payload))
        self.assertEqual(validate_first_stage_payload(payload), [])

    def test_top_level_s10_ready_true_is_success(self):
        payload = {"S10_READY": True}

        self.assertTrue(first_stage_s10_ready(payload))
        self.assertEqual(validate_first_stage_payload(payload), [])

    def test_status_s10_ready_is_success(self):
        payload = {"status": "S10_READY_DONE"}

        self.assertTrue(first_stage_s10_ready(payload))
        self.assertEqual(validate_first_stage_payload(payload), [])

    def test_target_initial_letter_not_found_is_specific_failure(self):
        payload = {"status": "S03_TARGET_INITIAL_LETTER_NOT_FOUND"}

        self.assertEqual(validate_first_stage_payload(payload), ["FIRST_STAGE_TARGET_NOT_FOUND"])

    def test_s03_brand_not_found_statuses_are_specific_target_failures(self):
        for status in [
            "S03_TARGET_INITIAL_LETTER_NOT_DERIVABLE",
            "S03_TARGET_BRAND_NOT_FOUND",
            "S03_TARGET_BRAND_CLICK_FAILED",
            "S03_TARGET_BRAND_PANEL_NOT_READY",
        ]:
            with self.subTest(status=status):
                self.assertEqual(validate_first_stage_payload({"status": status}), ["FIRST_STAGE_TARGET_NOT_FOUND"])

    def test_desktop_upgrade_modal_failure_status_is_preserved(self):
        payload = {"status": "DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS"}

        self.assertEqual(validate_first_stage_payload(payload), ["DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS"])

    def test_login_required_manual_status_is_preserved(self):
        self.assertEqual(validate_first_stage_payload({"status": "LOGIN_REQUIRED_MANUAL"}), ["LOGIN_REQUIRED_MANUAL"])
        self.assertEqual(validate_first_stage_payload({"final_status": "HUMAN_LOGIN_REQUIRED"}), ["HUMAN_LOGIN_REQUIRED"])

    def test_target_task_field_missing_is_preserved_as_target_input_failure(self):
        payload = {"final_status": "TARGET_TASK_FIELD_MISSING", "missing_fields": ["registration_date_year"]}

        self.assertEqual(validate_first_stage_payload(payload), ["TARGET_TASK_FIELD_MISSING"])

    def test_target_required_field_missing_in_errors_is_preserved(self):
        payload = {"status": "FAILED", "errors": ["TARGET_REQUIRED_FIELD_MISSING"]}

        self.assertEqual(validate_first_stage_payload(payload), ["TARGET_REQUIRED_FIELD_MISSING"])

    def test_s05_target_config_failures_are_preserved(self):
        cases = [
            "S05_TARGET_CONFIG_NOT_FOUND",
            "S05_TARGET_CONFIG_VISIBLE_BUT_NOT_MATCHED",
            "S05_TARGET_CONFIG_CLICK_FAILED",
            "S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED",
        ]
        for code in cases:
            with self.subTest(code=code):
                self.assertEqual(validate_first_stage_payload({"final_status": code}), [code])
                self.assertEqual(validate_first_stage_payload({"status": "FAILED", "errors": [code]}), [code])

    def test_s10_ready_false_is_not_ready_failure(self):
        payload = {"flow_state": {"S10_READY": False}, "same_source_cards": []}

        self.assertFalse(first_stage_s10_ready(payload))
        self.assertEqual(validate_first_stage_payload(payload), ["FIRST_STAGE_NOT_S10_READY"])

    def test_missing_signals_is_schema_invalid(self):
        payload = {"unexpected": "shape"}

        self.assertEqual(validate_first_stage_payload(payload), ["FIRST_STAGE_SCHEMA_INVALID"])


if __name__ == "__main__":
    unittest.main()
