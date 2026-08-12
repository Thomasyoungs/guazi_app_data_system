import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from scripts.feishu_task_store import (
    ADMIN_CHAT_ID_MISSING,
    FINAL_FEEDBACK_LIVE_SEND_FAILED,
    FINAL_FEEDBACK_LIVE_SEND_SUCCEEDED,
    FINAL_FEEDBACK_SENT_FLAG_INVALID_DRY_RUN_ONLY,
    FeishuTaskStore,
)


def fixed_clock():
    return datetime(2026, 6, 30, 20, 0, tzinfo=timezone.utc)


class CancelledFinalFeedbackDeliveryStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = FeishuTaskStore(self.root / "feishu_tasks", clock=fixed_clock)

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, path: Path, payload: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def create_cancel_candidate(self, task_id="FS20260630_0007", *, admin_chat_id=None):
        task_dir = self.store.task_dir(task_id)
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "RUNNING_FIRST_STAGE",
                "business_status": "RUNNING",
                "technical_status": "RUNNING_FIRST_STAGE",
                "business_chat_id": "oc_business",
                "admin_chat_id": admin_chat_id,
                "start_ack_sent": True,
                "errors": ["S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED"],
            },
        )
        self.write_json(
            task_dir / "first_stage_result.json",
            {
                "final_status": "S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED",
                "errors": ["S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED"],
                "context": {"age_action": {"target_age": 1}},
            },
        )
        return task_id, task_dir

    def test_cancel_confirm_failure_dry_run_generates_preview_but_not_sent(self):
        task_id, task_dir = self.create_cancel_candidate()

        result = self.store.cancel_confirm_failure(
            task_id,
            errors=["S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED"],
            dry_run=True,
        )

        self.assertTrue(result.success)
        status = self.read_json(task_dir / "status.json")
        delivery = self.read_json(task_dir / "final_failure_feedback_delivery.json")
        self.assertTrue(status["final_feedback_generated"])
        self.assertTrue(status["final_feedback_delivery_dry_run"])
        self.assertFalse(status["final_feedback_sent"])
        self.assertFalse(delivery["final_feedback_sent"])
        self.assertTrue(delivery["dry_run"])
        self.assertIn("FINAL_FEEDBACK_DRY_RUN_NOT_MARKED_SENT", delivery["guard_codes"])
        self.assertTrue((task_dir / "final_failure_business_reply.preview.txt").exists())

    def test_live_business_send_success_marks_sent_even_when_admin_chat_missing(self):
        task_id, task_dir = self.create_cancel_candidate(admin_chat_id=None)
        calls = []

        def fake_sender(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "dry_run": False, "message_id": f"om_{len(calls)}"}

        result = self.store.cancel_confirm_failure(
            task_id,
            errors=["S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED"],
            dry_run=False,
            message_sender=fake_sender,
        )

        self.assertTrue(result.success)
        self.assertEqual(len(calls), 1)
        status = self.read_json(task_dir / "status.json")
        delivery = self.read_json(task_dir / "final_failure_feedback_delivery.json")
        self.assertFalse(status["final_feedback_delivery_dry_run"])
        self.assertTrue(status["final_feedback_sent"])
        self.assertEqual(status["final_feedback_message_id"], "om_1")
        self.assertIn(FINAL_FEEDBACK_LIVE_SEND_SUCCEEDED, delivery["guard_codes"])
        self.assertIn(ADMIN_CHAT_ID_MISSING, delivery["guard_codes"])
        self.assertEqual(delivery["admin_feedback_guard_code"], ADMIN_CHAT_ID_MISSING)

    def test_live_send_failure_does_not_mark_sent_and_records_retryable_error(self):
        task_id, task_dir = self.create_cancel_candidate()

        def fake_sender(**kwargs):
            return {"ok": False, "dry_run": False, "error_code": "MOCK_SEND_FAILED"}

        self.store.cancel_confirm_failure(
            task_id,
            errors=["S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED"],
            dry_run=False,
            message_sender=fake_sender,
        )

        status = self.read_json(task_dir / "status.json")
        delivery = self.read_json(task_dir / "final_failure_feedback_delivery.json")
        self.assertFalse(status["final_feedback_sent"])
        self.assertTrue(status["final_feedback_send_failed"])
        self.assertEqual(status["final_feedback_send_error"], "MOCK_SEND_FAILED")
        self.assertTrue(status["final_feedback_retryable"])
        self.assertIn(FINAL_FEEDBACK_LIVE_SEND_FAILED, delivery["guard_codes"])

    def test_ensure_cancelled_feedback_compensates_invalid_dry_run_sent_flag(self):
        task_id, task_dir = self.create_cancel_candidate()
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "CANCELLED",
                "business_status": "CANCELLED",
                "technical_status": "CANCELLED",
                "business_chat_id": "oc_business",
                "start_ack_sent": True,
                "errors": ["S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED"],
                "final_feedback_sent": True,
            },
        )
        self.write_json(
            task_dir / "final_failure_feedback_delivery.json",
            {
                "task_id": task_id,
                "status": "CANCELLED",
                "dry_run": True,
                "canonical_error_code": "S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED",
                "business_reply_text": "preview only",
            },
        )
        calls = []

        def fake_sender(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "dry_run": False, "message_id": "om_compensated"}

        result = self.store.ensure_cancelled_task_final_feedback(
            task_id,
            dry_run=False,
            message_sender=fake_sender,
        )

        self.assertTrue(result.success)
        self.assertTrue(result.changed)
        self.assertEqual(result.data["sent_flag_invalid_reason"], FINAL_FEEDBACK_SENT_FLAG_INVALID_DRY_RUN_ONLY)
        self.assertEqual(len(calls), 1)
        status = self.read_json(task_dir / "status.json")
        delivery = self.read_json(task_dir / "final_failure_feedback_delivery.json")
        self.assertTrue(status["final_feedback_sent"])
        self.assertEqual(status["final_feedback_message_id"], "om_compensated")
        self.assertIn(FINAL_FEEDBACK_SENT_FLAG_INVALID_DRY_RUN_ONLY, delivery["guard_codes"])


if __name__ == "__main__":
    unittest.main()
