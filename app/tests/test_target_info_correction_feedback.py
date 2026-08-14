import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from target_info_correction_feedback import (  # noqa: E402
    INTERNAL_FEEDBACK_FORBIDDEN_TERMS,
    TARGET_BRAND_SERIES_CONFLICT,
    TARGET_BRAND_SERIES_INFERENCE_FAILED,
    TARGET_DATE_UNRECOGNIZED,
    TARGET_INFO_NEEDS_CORRECTION,
    TARGET_REQUIRED_FIELD_MISSING,
    classify_target_info_errors,
    is_target_info_error,
    write_target_info_correction_feedback,
)


def fixed_clock():
    return datetime(2026, 6, 15, 8, 30, tzinfo=timezone.utc)


class TargetInfoCorrectionFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.task_dir = Path(self.temp.name) / "FS20260615_0001"

    def tearDown(self):
        self.temp.cleanup()

    def test_registration_date_error_writes_business_feedback_preview(self):
        feedback = write_target_info_correction_feedback(
            task_dir=self.task_dir,
            task_id="FS20260615_0001",
            status_payload={"business_chat_id": "oc_business", "sender_open_id": "ou_sales"},
            draft={"license_date_raw": "bad-date"},
            errors=["REGISTRATION_DATE_UNRECOGNIZED"],
            missing_fields=[],
            clock=fixed_clock,
        )

        self.assertEqual(feedback["target_status"], TARGET_INFO_NEEDS_CORRECTION)
        self.assertEqual(feedback["business_chat_id"], "oc_business")
        self.assertEqual(feedback["sender_open_id"], "ou_sales")
        self.assertIn(TARGET_DATE_UNRECOGNIZED, feedback["classification"]["codes"])
        self.assertIn("上牌日期无法识别", feedback["reply_text"])
        self.assertIn("2022.08", feedback["reply_text"])
        self.assertTrue((self.task_dir / "target_info_correction_reply.preview.txt").exists())
        delivery = json.loads((self.task_dir / "target_info_correction_delivery.json").read_text(encoding="utf-8"))
        self.assertTrue(delivery["dry_run"])
        self.assert_no_internal_terms(feedback["reply_text"])

    def test_model_unrecognized_and_conflict_are_classified_for_sender_feedback(self):
        unresolved = classify_target_info_errors(errors=["MODEL_BRAND_SERIES_UNRESOLVED"])
        conflict = classify_target_info_errors(errors=["MODEL_BRAND_SERIES_CONFLICT"])

        self.assertIn(TARGET_BRAND_SERIES_INFERENCE_FAILED, unresolved["codes"])
        self.assertIn(TARGET_BRAND_SERIES_CONFLICT, conflict["codes"])

    def test_config_mismatch_hard_stop_feedback_asks_sender_to_resend_config(self):
        feedback = write_target_info_correction_feedback(
            task_dir=self.task_dir,
            task_id="FS20260619_0004",
            status_payload={"raw_chat_id": "oc_business", "raw_sender_id": "ou_sales"},
            errors=["CONFIG_MISMATCH_HARD_STOP", "CONFIG_TIER_MISMATCH"],
            clock=fixed_clock,
        )

        self.assertEqual(feedback["target_status"], TARGET_INFO_NEEDS_CORRECTION)
        self.assertIn("CONFIG_MISMATCH_HARD_STOP", feedback["classification"]["codes"])
        self.assertIn("车型配置无法确认一致", feedback["reply_text"])
        self.assertIn("重新发送完整车型配置", feedback["reply_text"])
        self.assert_no_internal_terms(feedback["reply_text"])

    def test_multiple_missing_fields_are_listed(self):
        feedback = write_target_info_correction_feedback(
            task_dir=self.task_dir,
            task_id="FS20260615_0002",
            status_payload={"raw_chat_id": "oc_business", "raw_sender_id": "ou_sales"},
            missing_fields=["license_date", "mileage_text", "color", "condition_text"],
            errors=["MISSING_REQUIRED_FIELDS"],
            clock=fixed_clock,
        )

        self.assertIn(TARGET_REQUIRED_FIELD_MISSING, feedback["classification"]["codes"])
        for label in ["上牌日期", "表显里程", "车辆颜色", "具体车况"]:
            self.assertIn(label, feedback["reply_text"])
        self.assertIn("重新发送完整目标车源信息", feedback["reply_text"])

    def test_app_and_environment_errors_are_not_sales_target_info_errors(self):
        for code in ["LOGIN_REQUIRED_MANUAL", "ADB_UNAUTHORIZED", "PAGE_CONTRACT_MISMATCH", "FIRST_STAGE_NOT_S10_READY"]:
            self.assertFalse(is_target_info_error(errors=[code]))

    def assert_no_internal_terms(self, text):
        for term in INTERNAL_FEEDBACK_FORBIDDEN_TERMS:
            self.assertNotIn(term, text)


if __name__ == "__main__":
    unittest.main()
