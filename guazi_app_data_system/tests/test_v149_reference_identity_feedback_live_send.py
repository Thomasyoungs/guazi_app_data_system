import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from scripts.feishu_result_formatter import format_result_reply
from scripts.feishu_task_store import (
    ADMIN_CHAT_ID_MISSING,
    FINAL_FEEDBACK_LIVE_SEND_FAILED,
    FINAL_FEEDBACK_LIVE_SEND_SUCCEEDED,
    FeishuTaskStore,
)


def fixed_clock():
    return datetime(2026, 7, 1, 10, 30, tzinfo=timezone.utc)


class V149ReferenceIdentityFeedbackLiveSendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.task_root = Path(self.temp.name) / "data" / "feishu_tasks"
        self.store = FeishuTaskStore(self.task_root, clock=fixed_clock)

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, path: Path, payload: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def create_post_start_runtime_exception_task(self, task_id="FS20260701_0003", *, admin_chat_id=None):
        task_dir = self.task_root / task_id
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "ADMIN_INTERVENTION_REQUIRED",
                "business_status": "FAILED",
                "technical_status": "FAILED",
                "business_chat_id": "oc_business",
                "admin_chat_id": admin_chat_id,
                "start_ack_sent": True,
                "blocks_queue": True,
                "errors": ["APP_NOT_FOREGROUND", "RESULT_SCHEMA_INVALID_FOR_PRICING"],
            },
        )
        self.write_json(
            task_dir / "first_stage_result.json",
            {
                "status": "S10_READY",
                "flow_state": {"S10_READY": True},
                "trisame_cards_count": 4,
            },
        )
        traceback_tail = (
            'File "scripts/runtime_s10_to_s16_mainline.py", line 8564, in handle_s10_reference_card\n'
            'File "scripts/runtime_s10_to_s16_mainline.py", line 3058, in _build_reference_physical_ui_transition_proof'
        )
        self.write_json(
            task_dir / "pricing_result.json",
            {
                "status": "RESULT_SCHEMA_INVALID_FOR_PRICING",
                "issue_code": "SECOND_STAGE_RUNTIME_EXCEPTION",
                "exception_type": "TypeError",
                "exception_message": "_reference_identity_summary() takes 1 positional argument but 2 were given",
                "traceback_tail": traceback_tail,
                "stage": "second_stage",
                "failed_state": "S11",
                "s10_to_s11_wait": {
                    "page_changed_after_click": True,
                    "recognized_page": "S11",
                    "entered_s11": True,
                    "xml_stale": False,
                },
            },
        )
        return task_id, task_dir

    def test_post_start_runtime_exception_business_feedback_is_not_not_started_or_phone_env(self):
        task_id, task_dir = self.create_post_start_runtime_exception_task()

        result = self.store.release_blocker_without_active_runner(task_id)

        self.assertTrue(result.success)
        self.assertIn("【本次定价未完成】", result.reply_text)
        self.assertIn("参考车详情采集阶段出现系统异常", result.reply_text)
        for forbidden in ("【本次定价未开始】", "手机执行环境暂不可用", "APP_NOT_FOREGROUND"):
            self.assertNotIn(forbidden, result.reply_text)
        status = self.read_json(task_dir / "status.json")
        self.assertEqual(status["canonical_error_code"], "SECOND_STAGE_RUNTIME_EXCEPTION")
        self.assertEqual(status["root_exception_type"], "TypeError")
        self.assertEqual(status["root_cause_code"], "V149_REFERENCE_IDENTITY_SUMMARY_TYPE_ERROR")
        self.assertTrue(status["reached_s10_before_failure"])
        self.assertTrue(status["entered_s11_before_failure"])
        delivery = self.read_json(task_dir / "released_blocker_delivery.json")
        self.assertEqual(delivery["canonical_error_code"], "SECOND_STAGE_RUNTIME_EXCEPTION")
        self.assertEqual(delivery["root_exception_type"], "TypeError")
        self.assertIn("source_function=_build_reference_physical_ui_transition_proof", delivery["admin_reply_text"])

    def test_released_post_start_failure_live_send_success_marks_sent_and_admin_missing_does_not_block_business(self):
        task_id, task_dir = self.create_post_start_runtime_exception_task(admin_chat_id=None)
        calls = []

        def fake_sender(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "dry_run": False, "message_id": f"om_live_{len(calls)}"}

        result = self.store.release_blocker_without_active_runner(
            task_id,
            dry_run=False,
            message_sender=fake_sender,
        )

        self.assertTrue(result.success)
        self.assertEqual(len(calls), 1)
        status = self.read_json(task_dir / "status.json")
        delivery = self.read_json(task_dir / "released_blocker_delivery.json")
        self.assertFalse(delivery["dry_run"])
        self.assertTrue(delivery["final_feedback_send_attempted"])
        self.assertTrue(delivery["business_send_attempted"])
        self.assertTrue(delivery["final_feedback_sent"])
        self.assertEqual(delivery["business_message_id"], "om_live_1")
        self.assertTrue(status["final_feedback_sent"])
        self.assertIn(FINAL_FEEDBACK_LIVE_SEND_SUCCEEDED, delivery["guard_codes"])
        self.assertIn(ADMIN_CHAT_ID_MISSING, delivery["guard_codes"])
        self.assertEqual(delivery["admin_feedback_guard_code"], ADMIN_CHAT_ID_MISSING)

    def test_released_post_start_failure_live_send_failure_records_send_error(self):
        task_id, task_dir = self.create_post_start_runtime_exception_task()

        def fake_sender(**kwargs):
            return {"ok": False, "dry_run": False, "error_code": "MOCK_SEND_FAILED"}

        self.store.release_blocker_without_active_runner(
            task_id,
            dry_run=False,
            message_sender=fake_sender,
        )

        status = self.read_json(task_dir / "status.json")
        delivery = self.read_json(task_dir / "released_blocker_delivery.json")
        self.assertTrue(delivery["final_feedback_send_attempted"])
        self.assertTrue(delivery["business_send_attempted"])
        self.assertFalse(delivery["final_feedback_sent"])
        self.assertEqual(delivery["send_error"], "MOCK_SEND_FAILED")
        self.assertFalse(status["final_feedback_sent"])
        self.assertEqual(status["final_feedback_send_error"], "MOCK_SEND_FAILED")
        self.assertIn(FINAL_FEEDBACK_LIVE_SEND_FAILED, delivery["guard_codes"])

    def test_released_post_start_failure_dry_run_does_not_attempt_live_send(self):
        task_id, task_dir = self.create_post_start_runtime_exception_task()
        calls = []

        self.store.release_blocker_without_active_runner(
            task_id,
            dry_run=True,
            message_sender=lambda **kwargs: calls.append(kwargs) or {"ok": True, "message_id": "should_not_send"},
        )

        delivery = self.read_json(task_dir / "released_blocker_delivery.json")
        self.assertEqual(calls, [])
        self.assertTrue(delivery["dry_run"])
        self.assertFalse(delivery["final_feedback_send_attempted"])
        self.assertFalse(delivery["business_send_attempted"])
        self.assertFalse(delivery["final_feedback_sent"])

    def test_result_formatter_keeps_runtime_exception_specific_when_schema_wrapper_is_present(self):
        result = format_result_reply(
            task_id="FS20260701_0003",
            pricing_result={
                "status": "RESULT_SCHEMA_INVALID_FOR_PRICING",
                "issue_code": "SECOND_STAGE_RUNTIME_EXCEPTION",
                "exception_type": "TypeError",
                "exception_message": "_reference_identity_summary() takes 1 positional argument but 2 were given",
            },
            status="RESULT_SCHEMA_INVALID_FOR_PRICING",
        )

        self.assertIn("【本次定价未完成】", result.text)
        self.assertIn("参考车详情采集阶段出现系统异常", result.text)
        self.assertNotIn("【本次定价未开始】", result.text)
        self.assertIn("SECOND_STAGE_RUNTIME_EXCEPTION", result.warnings)

    def test_preflight_not_started_failure_still_does_not_get_runtime_exception_classification(self):
        task_id, task_dir = "FS20260701_0004", self.task_root / "FS20260701_0004"
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "ADMIN_INTERVENTION_REQUIRED",
                "business_status": "FAILED",
                "technical_status": "FAILED",
                "business_chat_id": "oc_business",
                "blocks_queue": True,
                "errors": ["TARGET_ADB_DEVICE_NOT_CONNECTED"],
            },
        )

        result = self.store.release_blocker_without_active_runner(task_id)

        self.assertTrue(result.success)
        status = self.read_json(task_dir / "status.json")
        self.assertEqual(status["canonical_error_code"], "TARGET_ADB_DEVICE_NOT_CONNECTED")
        self.assertFalse(status["reached_s10_before_failure"])
        self.assertFalse(status["post_start_failure"])
        self.assertFalse(status["post_start_not_started_template_blocked"])
        self.assertNotIn("参考车详情采集阶段出现系统异常", result.reply_text)


if __name__ == "__main__":
    unittest.main()
