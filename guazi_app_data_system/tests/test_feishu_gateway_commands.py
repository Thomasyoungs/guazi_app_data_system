import os
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_gateway import handle_event, parse_manual_price_command  # noqa: E402
from feishu_send_message import send_text_message  # noqa: E402
from feishu_task_store import FeishuTaskStore  # noqa: E402


def fixed_clock():
    return datetime(2026, 6, 9, 8, 30, tzinfo=timezone.utc)


VALID_TEMPLATE = """定价
品牌：本田
车系：雅阁
车型配置：2021款 260TURBO 豪华版
上牌日期：2021-06
表显里程：5.8万公里
颜色：白色
过户次数：1
车况：右前门喷漆，前杠喷漆
"""


class FeishuGatewayCommandsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FeishuTaskStore(Path(self.temp.name) / "feishu_tasks", clock=fixed_clock)
        handle_event(self.event("设置本群为一线群", "om_setup_business", sender_id="ou_admin"), store=self.store, roles=self.roles())

    def tearDown(self):
        self.temp.cleanup()

    def event(self, text, message_id="om_1", *, reply_to_message_id=None, sender_id="ou_1", chat_id="oc_1"):
        return {
            "message_id": message_id,
            "sender_id": sender_id,
            "chat_id": chat_id,
            "text": text,
            "reply_to_message_id": reply_to_message_id,
        }

    def create_task(self):
        return handle_event(self.event(VALID_TEMPLATE, "om_create"), store=self.store)

    def test_gateway_creates_draft_from_template(self):
        result = self.create_task()

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_id"], "FS20260609_0001")
        self.assertEqual(result["status"], "WAITING_TARGET_CONFIRMATION")
        self.assertIn("请确认目标车信息", result["reply_text"])
        self.assertIn("确认无误请回复：确认", result["reply_text"])
        self.assertNotIn("确认 FS", result["reply_text"])

    def test_gateway_confirm_command_success(self):
        created = self.create_task()

        result = handle_event(self.event("确认", "om_confirm"), store=self.store)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "QUEUED")
        self.assertIn("【定价已开始】FS20260609_0001", result["reply_text"])
        self.assertIn("系统已开始自动定价，请等待结果。", result["reply_text"])
        self.assertFalse((self.store.data_dir / "current_target_task.json").exists())

    def test_gateway_confirm_triggers_dispatch_kick_mock(self):
        created = self.create_task()
        calls = []

        def fake_kick(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "dispatch_once_called": True, "dispatcher_loop_running": False}

        result = handle_event(self.event("确认", "om_confirm_kick"), store=self.store, dispatch_kicker=fake_kick)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "QUEUED")
        self.assertEqual(calls[0]["task_id"], created["task_id"])
        self.assertTrue(calls[0]["force_health_check"])
        self.assertTrue(result["data"]["dispatch_kick"]["dispatch_once_called"])
        self.assertNotIn("PowerShell", result["reply_text"])
        self.assertNotIn("dispatcher", result["reply_text"])

    def test_gateway_confirm_live_kick_allows_app_run_and_forces_health_check(self):
        self.create_task()
        calls = []

        def fake_kick(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "dispatch_once_called": True, "dispatcher_loop_running": False}

        result = handle_event(
            self.event("确认", "om_confirm_live_kick"),
            store=self.store,
            dispatch_kicker=fake_kick,
            dispatch_kick_allow_app_run=True,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(calls[0]["allow_app_run"])
        self.assertTrue(calls[0]["force_health_check"])
        self.assertTrue(calls[0]["background"])
        self.assertEqual(calls[0]["source"], "feishu_confirm")

    def test_gateway_confirm_preflight_blocks_queue_before_dispatch(self):
        created = self.create_task()
        kick_calls = []

        def fake_preflight(**kwargs):
            return {
                "ok": False,
                "status": "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY",
                "error_code": "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY",
                "business_reply_text": "【本次定价未开始】\n手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP。\n任务暂未进入定价队列，不会占用队列。",
                "admin_reply_text": "fast entry 已尝试但未成功，确认前设备就绪门禁未通过。",
                "device_ready_for_pricing": False,
                "should_enqueue": False,
                "should_start_runner": False,
                "fast_entry_attempted": True,
            }

        result = handle_event(
            self.event("确认", "om_confirm_preflight_fast_entry_failed"),
            store=self.store,
            confirm_preflight_checker=fake_preflight,
            dispatch_kicker=lambda **kwargs: kick_calls.append(kwargs) or {"ok": True},
            dispatch_kick_allow_app_run=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "confirm_preflight_failed")
        self.assertIn("暂未进入定价队列", result["reply_text"])
        self.assertEqual(kick_calls, [])
        status = json.loads((self.store.task_dir(created["task_id"]) / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "WAITING_TARGET_CONFIRMATION")
        self.assertFalse(status["blocks_queue"])
        self.assertFalse(status["device_ready_for_pricing"])
        self.assertTrue((self.store.task_dir(created["task_id"]) / "confirm_preflight_gate_snapshot.json").exists())
        self.assertFalse((self.store.data_dir / "current_target_task.json").exists())

    def test_duplicate_confirm_after_preflight_failure_replays_failure_not_start_ack(self):
        created = self.create_task()
        preflight_calls = []

        def fake_preflight(**kwargs):
            preflight_calls.append(kwargs)
            return {
                "ok": False,
                "status": "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY",
                "error_code": "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY",
                "business_reply_text": "【本次定价未开始】\n手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP。\n任务暂未进入定价队列，不会占用队列。",
                "device_ready_for_pricing": False,
                "should_enqueue": False,
                "should_start_runner": False,
            }

        first = handle_event(
            self.event("确认", "om_confirm_preflight_duplicate_failed"),
            store=self.store,
            confirm_preflight_checker=fake_preflight,
            dispatch_kick_allow_app_run=True,
        )
        second = handle_event(
            self.event("确认", "om_confirm_preflight_duplicate_failed"),
            store=self.store,
            confirm_preflight_checker=fake_preflight,
            dispatch_kick_allow_app_run=True,
        )

        self.assertFalse(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual("duplicate_confirm_preflight_failure_replayed", second["action"])
        self.assertTrue(second["duplicate"])
        self.assertIn("【本次定价未开始】", second["reply_text"])
        self.assertNotIn("【定价已开始】", second["reply_text"])
        self.assertEqual(1, len(preflight_calls))
        status = self.read_json(self.store.task_dir(created["task_id"]) / "status.json")
        self.assertEqual("WAITING_TARGET_CONFIRMATION", status["status"])
        self.assertFalse(status["device_ready_for_pricing"])
        self.assertFalse(status["started"])
        self.assertFalse((self.store.data_dir / "current_target_task.json").exists())
        self.assertFalse((self.store.task_dir(created["task_id"]) / "feishu_start_message_delivery.json").exists())
        trace = self.read_json(self.store.task_dir(created["task_id"]) / "confirm_preflight_duplicate_delivery.json")
        self.assertEqual("FEISHU_CONFIRM_PREFLIGHT_FAILURE_DUPLICATE_REPLAYED", trace["duplicate_confirm_guard_code"])
        self.assertEqual("FEISHU_DUPLICATE_MESSAGE_START_ACK_BLOCKED_BY_PREFLIGHT_FAILURE", trace["start_ack_blocked_code"])

    def test_new_confirm_message_after_preflight_failure_reruns_preflight_and_can_queue(self):
        created = self.create_task()
        calls = []

        def fake_preflight(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {
                    "ok": False,
                    "status": "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY",
                    "error_code": "DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY",
                    "business_reply_text": "【本次定价未开始】\n手机已连接，但系统未能自动退出锁屏/通知栏遮挡并进入瓜子 APP。",
                    "device_ready_for_pricing": False,
                    "should_enqueue": False,
                    "should_start_runner": False,
                }
            return {
                "ok": True,
                "status": "FAST_ENTRY_RECOVERED",
                "device_ready_for_pricing": True,
                "should_enqueue": True,
                "should_start_runner": True,
            }

        first = handle_event(
            self.event("确认", "om_confirm_preflight_failed_once"),
            store=self.store,
            confirm_preflight_checker=fake_preflight,
            dispatch_kick_allow_app_run=True,
        )
        second = handle_event(
            self.event("确认", "om_confirm_preflight_new_message_ok"),
            store=self.store,
            confirm_preflight_checker=fake_preflight,
            dispatch_kicker=lambda **kwargs: {"ok": True, "dispatch_once_called": True},
            dispatch_kick_allow_app_run=True,
        )

        self.assertFalse(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual("confirm_task", second["action"])
        self.assertEqual("QUEUED", second["status"])
        self.assertIn("【定价已开始】", second["reply_text"])
        self.assertEqual(2, len(calls))
        status = self.read_json(self.store.task_dir(created["task_id"]) / "status.json")
        self.assertEqual("QUEUED", status["status"])

    def test_gateway_confirm_preflight_ready_allows_queue_and_dispatch(self):
        created = self.create_task()
        kick_calls = []

        def fake_preflight(**kwargs):
            return {
                "ok": True,
                "status": "FAST_ENTRY_RECOVERED",
                "device_ready_for_pricing": True,
                "should_enqueue": True,
                "should_start_runner": True,
                "fast_entry_attempted": True,
                "fast_entry_recovered": True,
            }

        result = handle_event(
            self.event("确认", "om_confirm_preflight_ready"),
            store=self.store,
            confirm_preflight_checker=fake_preflight,
            dispatch_kicker=lambda **kwargs: kick_calls.append(kwargs) or {"ok": True, "dispatch_once_called": True},
            dispatch_kick_allow_app_run=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "QUEUED")
        self.assertEqual(kick_calls[0]["task_id"], created["task_id"])

    def test_gateway_confirm_dispatch_kick_failure_reports_admin_handling_without_internal_terms(self):
        self.create_task()

        def fake_kick(**kwargs):
            return {"ok": False, "errors": ["DISPATCH_KICK_FAILED"], "message": "internal failure"}

        result = handle_event(self.event("确认", "om_confirm_kick_failed"), store=self.store, dispatch_kicker=fake_kick)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "QUEUED")
        self.assertIn("【定价已开始】FS20260609_0001", result["reply_text"])
        self.assertIn("系统已开始自动定价，请等待结果。", result["reply_text"])
        self.assertTrue(result["data"]["confirm_ack_order_guard"]["failure_feedback_blocked_before_start_ack"])
        self.assertEqual(
            "FEISHU_CONFIRM_DOUBLE_PATH_FEEDBACK_PREVENTED",
            result["data"]["confirm_ack_order_guard"]["guard_code"],
        )
        for forbidden in ["PowerShell", "dispatcher", "runner", "adb", "HEALTH_CHECK_COOLDOWN_OR_NOT_AUTO_RECOVERABLE", "SYSTEM_BLOCKED"]:
            self.assertNotIn(forbidden, result["reply_text"])

    def test_gateway_confirm_blocked_dispatch_result_reports_admin_handling_without_internal_terms(self):
        self.create_task()

        def fake_kick(**kwargs):
            return {
                "ok": False,
                "dispatch_once_called": True,
                "dispatch_once_result": {
                    "ok": False,
                    "status": "SYSTEM_BLOCKED",
                    "errors": ["ADMIN_INTERVENTION_TASK_EXISTS"],
                    "auto_recovery_attempts": [
                        {
                            "task_id": "FS20260614_0002",
                            "attempted": False,
                            "reason": "HEALTH_CHECK_COOLDOWN_OR_NOT_AUTO_RECOVERABLE",
                        }
                    ],
                },
            }

        result = handle_event(self.event("确认", "om_confirm_blocked_kick"), store=self.store, dispatch_kicker=fake_kick)

        self.assertTrue(result["ok"])
        self.assertIn("【定价已开始】FS20260609_0001", result["reply_text"])
        self.assertTrue(result["data"]["confirm_ack_order_guard"]["failure_feedback_blocked_before_start_ack"])
        for forbidden in ["PowerShell", "dispatcher", "runner", "adb", "HEALTH_CHECK_COOLDOWN_OR_NOT_AUTO_RECOVERABLE", "SYSTEM_BLOCKED"]:
            self.assertNotIn(forbidden, result["reply_text"])

    def test_started_dispatch_failure_does_not_use_not_started_login_fallback(self):
        self.create_task()

        def fake_kick(**kwargs):
            return {
                "ok": False,
                "dispatch_once_called": True,
                "dispatch_once_result": {
                    "ok": False,
                    "status": "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
                    "started": True,
                    "errors": ["RESULT_MISSING_REQUIRED_PRICING_FIELDS"],
                    "reference_history": [{"reference_index": 1}],
                },
            }

        result = handle_event(self.event("确认", "om_confirm_started_failure"), store=self.store, dispatch_kicker=fake_kick)

        self.assertTrue(result["ok"])
        self.assertIn("系统已开始自动定价", result["reply_text"])
        self.assertIn("请等待结果", result["reply_text"])
        self.assertNotIn("未能形成完整结果", result["reply_text"])
        self.assertNotIn("系统暂时不能开始自动定价", result["reply_text"])
        self.assertNotIn("瓜子登录状态", result["reply_text"])
        for forbidden in ["adb", "uiautomator", "runner", "dispatcher", "status.json", "RESULT_MISSING_REQUIRED_PRICING_FIELDS"]:
            self.assertNotIn(forbidden, result["reply_text"])
        trace = json.loads((self.store.task_dir("FS20260609_0001") / "feishu_start_message_delivery.json").read_text(encoding="utf-8"))
        self.assertTrue(trace["dispatch_kick_failed"])
        self.assertEqual("FEISHU_CONFIRM_FAILURE_FEEDBACK_BLOCKED_BEFORE_START_ACK", trace["confirm_failure_feedback_guard_code"])

    def test_admin_confirm_recovered_but_dispatch_still_blocked_reports_not_recovered(self):
        task_id = "FS20260609_0001"
        task_dir = self.store.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "SYSTEM_BLOCKED",
                "business_status": "SYSTEM_BLOCKED",
                "technical_status": "FAILED",
                "errors": ["HUMAN_LOGIN_REQUIRED"],
                "raw_chat_id": "oc_1",
                "business_chat_id": "oc_1",
                "sender_open_id": "ou_1",
                "created_at": "2026-06-09T08:30:00+00:00",
                "updated_at": "2026-06-09T08:30:00+00:00",
            },
        )

        kick_calls = []

        result = handle_event(
            self.event("确认", "om_admin_confirm_blocked", sender_id="ou_admin"),
            store=self.store,
            roles=self.roles(),
            system_health_checker=lambda **kwargs: {"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []},
            dispatch_kicker=lambda **kwargs: kick_calls.append(kwargs) or {"ok": True},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertIn("历史未完成任务", result["reply_text"])
        self.assertEqual(kick_calls, [])
        for forbidden in ["PowerShell", "dispatcher", "runner", "HEALTH_CHECK_COOLDOWN_OR_NOT_AUTO_RECOVERABLE", "SYSTEM_BLOCKED"]:
            self.assertNotIn(forbidden, result["reply_text"])

    def test_gateway_confirm_when_dispatcher_loop_running_only_enqueues(self):
        self.create_task()

        def fake_kick(**kwargs):
            return {"ok": True, "dispatch_once_called": False, "dispatcher_loop_running": True}

        result = handle_event(self.event("确认", "om_confirm_loop_running"), store=self.store, dispatch_kicker=fake_kick)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "QUEUED")
        self.assertFalse(result["data"]["dispatch_kick"]["dispatch_once_called"])
        self.assertTrue(result["data"]["dispatch_kick"]["dispatcher_loop_running"])

    def test_gateway_confirm_command_does_not_require_task_id(self):
        self.create_task()

        result = handle_event(self.event("确认", "om_confirm_no_id"), store=self.store)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "QUEUED")

    def test_gateway_duplicate_confirm_text_does_not_request_task_id(self):
        self.create_task()
        first = handle_event(self.event("确认", "om_confirm_once"), store=self.store)
        second = handle_event(self.event("确认", "om_confirm_twice"), store=self.store)

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertIn("请先发送目标车信息", second["reply_text"])
        self.assertNotIn("FS202606", second["reply_text"])

    def test_gateway_cancel_command_success(self):
        created = self.create_task()

        result = handle_event(self.event(f"取消 {created['task_id']}", "om_cancel"), store=self.store)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")

    def test_gateway_status_command_success(self):
        created = self.create_task()

        result = handle_event(self.event(f"状态 {created['task_id']}", "om_status"), store=self.store)

        self.assertTrue(result["ok"])
        self.assertIn("状态：WAITING_TARGET_CONFIRMATION", result["reply_text"])

    def test_gateway_unknown_task_id_rejected(self):
        result = handle_event(self.event("确认 FS20260609_9999", "om_unknown"), store=self.store)

        self.assertFalse(result["ok"])
        self.assertIn("未找到任务：FS20260609_9999", result["reply_text"])

    def test_gateway_duplicate_message_id_does_not_regenerate_task(self):
        first = handle_event(self.event(VALID_TEMPLATE, "om_dup"), store=self.store)
        second = handle_event(self.event(VALID_TEMPLATE, "om_dup"), store=self.store)

        self.assertEqual(first["task_id"], second["task_id"])
        self.assertTrue(second["duplicate"])
        self.assertEqual("duplicate_message_no_start_ack_without_started_evidence", second["action"])
        self.assertEqual("", second["reply_text"])
        self.assertEqual(
            "FEISHU_DUPLICATE_MESSAGE_NO_START_ACK_WITHOUT_STARTED_EVIDENCE",
            second["data"]["duplicate_message_guard_code"],
        )
        trace = self.read_json(self.store.task_dir("FS20260609_0001") / "duplicate_message_guard_trace.json")
        self.assertEqual("FEISHU_DUPLICATE_MESSAGE_NO_START_ACK_WITHOUT_STARTED_EVIDENCE", trace["duplicate_message_guard_code"])

    def test_duplicate_confirm_without_started_evidence_does_not_return_start_ack(self):
        self.create_task()
        self.store.record_processed_message("om_confirm_no_started_evidence", "FS20260609_0001")

        result = handle_event(self.event("确认", "om_confirm_no_started_evidence"), store=self.store)

        self.assertTrue(result["ok"])
        self.assertEqual("duplicate_message_no_start_ack_without_started_evidence", result["action"])
        self.assertEqual("", result["reply_text"])
        self.assertNotIn("【定价已开始】", result["reply_text"])

    def test_duplicate_queued_with_started_evidence_can_return_start_ack_without_state_change(self):
        created = self.create_task()
        status_path = self.store.task_dir(created["task_id"]) / "status.json"
        status = self.read_json(status_path)
        status.update(
            {
                "status": "QUEUED",
                "queued_at": "2026-06-09T08:31:00+00:00",
                "device_ready_for_pricing": True,
                "started": False,
            }
        )
        self.write_json(status_path, status)
        self.store.record_processed_message("om_queued_duplicate", created["task_id"])

        result = handle_event(self.event("确认", "om_queued_duplicate"), store=self.store)

        self.assertTrue(result["ok"])
        self.assertEqual("duplicate_message_started_evidence", result["action"])
        self.assertEqual("QUEUED", result["status"])
        self.assertIn("【定价已开始】FS20260609_0001", result["reply_text"])
        self.assertEqual("QUEUED", self.read_json(status_path)["status"])

    def test_duplicate_confirm_after_start_ack_is_silent_ignored(self):
        self.create_task()
        first = handle_event(self.event("确认", "om_confirm_once"), store=self.store)
        second = handle_event(self.event("确认", "om_confirm_once"), store=self.store)

        self.assertTrue(first["ok"])
        self.assertIn("【定价已开始】FS20260609_0001", first["reply_text"])
        self.assertTrue(second["ok"])
        self.assertEqual("duplicate_confirm_ignored", second["action"])
        self.assertTrue(second["duplicate"])
        self.assertEqual("", second["reply_text"])
        self.assertTrue(second["data"]["duplicate_confirm_ignored"])
        trace = json.loads((self.store.task_dir("FS20260609_0001") / "feishu_start_message_delivery.json").read_text(encoding="utf-8"))
        self.assertTrue(trace["duplicate_confirm_ignored"])
        self.assertEqual("FEISHU_CONFIRM_START_ACK_ALREADY_SENT_DUPLICATE_IGNORED", trace["duplicate_confirm_guard_code"])

    def test_duplicate_cancelled_task_backfills_final_feedback(self):
        task_id = "FS20260609_0001"
        task_dir = self.store.task_dir(task_id)
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "CANCELLED",
                "business_status": "CANCELLED",
                "technical_status": "CANCELLED",
                "errors": ["HUMAN_LOGIN_REQUIRED"],
                "business_chat_id": "oc_1",
                "raw_chat_id": "oc_1",
            },
        )
        self.store.record_processed_message("om_cancelled_dup", task_id)

        result = handle_event(self.event("确认", "om_cancelled_dup"), store=self.store, roles=self.roles())

        self.assertTrue(result["ok"])
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertIn("瓜子 APP 未登录或登录状态异常", result["reply_text"])
        self.assertIn("重新发送目标车源", result["reply_text"])
        self.assertNotIn("该消息已处理", result["reply_text"])
        status = self.read_json(task_dir / "status.json")
        self.assertTrue(status["final_feedback_generated"])
        self.assertTrue(status["final_feedback_delivery_dry_run"])
        self.assertFalse(status["final_feedback_sent"])
        self.assertTrue((task_dir / "final_failure_business_reply.preview.txt").exists())

    def test_gateway_unknown_command_returns_help(self):
        result = handle_event(self.event("帮助一下", "om_help"), store=self.store)

        self.assertFalse(result["ok"])
        self.assertIn("请回复“确认”两个字确认目标车信息，或重新发送目标车信息。", result["reply_text"])
        self.assertNotIn("确认 <task_id>", result["reply_text"])

    def test_gateway_plain_text_prompt_hides_internal_commands(self):
        result = handle_event(self.event("随便看看", "om_plain"), store=self.store)

        self.assertFalse(result["ok"])
        self.assertIn("请回复“确认”两个字", result["reply_text"])
        for forbidden in [
            "--requeue-second-stage",
            "--revalidate-result",
            "--manual-confirm-price",
            "--manual-review-note",
            "run_id",
            "generation_id",
            "technical_status",
            "status.json",
            "pricing_result.json",
            "adb",
            "uiautomator",
        ]:
            self.assertNotIn(forbidden, result["reply_text"])

    def test_needs_review_plain_price_confirms_manual_price(self):
        task_id = self.create_needs_review_task()

        result = handle_event(self.event("86000", "om_price"), store=self.store, roles=self.roles())

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_id"], task_id)
        self.assertEqual(result["status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertIn("人工复核已确认", result["reply_text"])
        self.assertIn("系统测算价：84928 元", result["reply_text"])
        self.assertIn("人工确认价：86000 元", result["reply_text"])
        pricing = self.read_json(self.store.task_dir(task_id) / "pricing_result.json")
        self.assertEqual(pricing["system_suggested_purchase_price_yuan"], 84928)
        self.assertEqual(pricing["suggested_purchase_price_yuan"], 84928)
        self.assertEqual(pricing["manual_confirmed_purchase_price_yuan"], 86000)
        self.assertEqual(pricing["final_purchase_price_yuan"], 86000)

    def test_needs_review_confirm_prompts_for_price_not_confirm_word(self):
        task_id = self.create_needs_review_task()

        result = handle_event(self.event("确认", "om_review_confirm"), store=self.store, roles=self.roles())

        self.assertFalse(result["ok"])
        self.assertEqual(result["task_id"], task_id)
        self.assertIn("当前任务需要主管确认收车价，请直接回复价格", result["reply_text"])
        self.assertNotIn("PowerShell", result["reply_text"])
        self.assertNotIn("dispatcher", result["reply_text"])

    def test_target_info_needs_correction_confirm_requires_resend(self):
        bad = self.store.create_task_from_message(
            text="""定价
车型配置：2021款 260TURBO 豪华版
上牌日期：not-a-date
表显里程：5.8万公里
颜色：白色
过户次数：1
车况：右前门喷漆""",
            raw_event={"message_id": "om_bad_target"},
            raw_message_id="om_bad_target",
            raw_sender_id="ou_1",
            raw_chat_id="oc_1",
        )

        result = handle_event(self.event("确认", "om_bad_confirm"), store=self.store, roles=self.roles())

        self.assertFalse(result["ok"])
        self.assertEqual(result["task_id"], bad.task_id)
        self.assertIn("这台车源信息需要修改，请重新发送完整目标车源信息", result["reply_text"])
        self.assertEqual(self.read_json(self.store.task_dir(bad.task_id) / "status.json")["status"], "TARGET_INFO_NEEDS_CORRECTION")

    def test_needs_review_wan_price_confirms_manual_price(self):
        task_id = self.create_needs_review_task()

        result = handle_event(self.event("8.6万", "om_price_wan"), store=self.store, roles=self.roles())

        self.assertTrue(result["ok"])
        pricing = self.read_json(self.store.task_dir(task_id) / "pricing_result.json")
        self.assertEqual(pricing["manual_confirmed_purchase_price_yuan"], 86000)

    def test_needs_review_yuan_suffix_price_confirms_manual_price(self):
        task_id = self.create_needs_review_task()

        result = handle_event(self.event("86000元", "om_price_yuan"), store=self.store, roles=self.roles())

        self.assertTrue(result["ok"])
        pricing = self.read_json(self.store.task_dir(task_id) / "pricing_result.json")
        self.assertEqual(pricing["manual_confirmed_purchase_price_yuan"], 86000)

    def test_manual_price_command_supports_task_id_separator_variants(self):
        for text in (
            "FS20260623_0011 8.6万",
            "FS20260623_0011：8.6万",
            "FS20260623_0011，8.6万",
            "FS20260623_0011 86000",
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_manual_price_command(text), ("FS20260623_0011", 86000, None))

    def test_needs_review_without_system_suggested_price_confirms_manual_price(self):
        task_id = self.create_needs_review_task()
        pricing_path = self.store.task_dir(task_id) / "pricing_result.json"
        payload = self.read_json(pricing_path)
        payload["pricing"] = {
            "status": "NEEDS_REVIEW",
            "reason": "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
            "manual_review_required": True,
        }
        self.write_json(pricing_path, payload)

        result = handle_event(self.event(f"{task_id}：8.6万", "om_price_no_system"), store=self.store, roles=self.roles())

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_id"], task_id)
        self.assertEqual(result["status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertIn("最终收车价：86000 元", result["reply_text"])
        self.assertIn("确认来源：主管人工报价", result["reply_text"])
        self.assertNotIn("系统测算价：None", result["reply_text"])
        self.assertNotIn("suggested_purchase_price_yuan", result["reply_text"])
        pricing = self.read_json(pricing_path)
        self.assertEqual(pricing["manual_price_yuan"], 86000)
        self.assertEqual(pricing["final_purchase_price_yuan"], 86000)
        self.assertTrue(pricing["system_suggested_price_missing"])
        self.assertFalse(pricing["system_suggested_price_required"])
        self.assertEqual(pricing["manual_confirm_raw_text"], f"{task_id}：8.6万")

    def test_non_review_number_does_not_confirm_manual_price(self):
        self.create_task()

        result = handle_event(self.event("86000", "om_number_not_review"), store=self.store, roles=self.roles())

        self.assertFalse(result["ok"])
        self.assertIn("当前没有待人工复核的任务", result["reply_text"])

    def test_confirmed_manual_review_price_is_not_overwritten(self):
        task_id = self.create_needs_review_task()
        handle_event(self.event("86000", "om_price_once"), store=self.store, roles=self.roles())

        result = handle_event(self.event(f"{task_id} 85000", "om_price_twice"), store=self.store, roles=self.roles())

        self.assertFalse(result["ok"])
        self.assertEqual(result["task_id"], task_id)
        self.assertIn("当前任务已人工确认", result["reply_text"])
        pricing = self.read_json(self.store.task_dir(task_id) / "pricing_result.json")
        self.assertEqual(pricing["manual_confirmed_purchase_price_yuan"], 86000)

    def test_confirmed_manual_review_same_price_is_idempotent_short_reply(self):
        task_id = self.create_needs_review_task()
        handle_event(self.event(f"{task_id} 8.6万", "om_price_once"), store=self.store, roles=self.roles())

        result = handle_event(self.event(f"{task_id} 8.6万", "om_price_same"), store=self.store, roles=self.roles())

        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(result["action"], "manual_price_already_confirmed_same_price")
        self.assertIn("该任务已确认人工复核价：86000 元", result["reply_text"])
        self.assertNotIn("系统测算价", result["reply_text"])

    def test_dry_run_send_message_does_not_need_real_feishu_env(self):
        with patch.dict(os.environ, {}, clear=True):
            result = send_text_message(text="hello", dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["chat_id"], "<FEISHU_TEST_CHAT_ID>")
        self.assertEqual(result["payload"]["content"]["text"], "hello")

    def create_needs_review_task(self):
        task_id = "FS20260609_0001"
        task_dir = self.store.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "NEEDS_REVIEW",
                "business_status": "NEEDS_REVIEW",
                "technical_status": "SUCCEEDED",
                "business_chat_id": "oc_1",
                "raw_chat_id": "oc_1",
                "sender_open_id": "ou_1",
                "created_at": "2026-06-09T08:30:00+00:00",
                "updated_at": "2026-06-09T08:30:00+00:00",
            },
        )
        self.write_json(task_dir / "pricing_result.json", full_chain_manual_review_payload())
        return task_id

    def roles(self):
        return {
            "admin_open_ids": ["ou_admin"],
            "business_chat_ids": ["oc_1"],
            "supervisor_chat_ids": ["oc_1"],
            "supervisor_open_ids": ["ou_1"],
            "admin_chat_ids": [],
        }

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))


def full_chain_manual_review_payload():
    return {
        "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "final_status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "current_state": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "target_vehicle": "本田 雅阁 2021款 260TURBO 豪华·星空限量版",
        "brand": "本田",
        "series": "雅阁",
        "model_config": "2021款 260TURBO 豪华·星空限量版",
        "color": "白色",
        "license_date": "2021.06",
        "mileage_text": "5.8万公里",
        "transfer_count_text": "1次",
        "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "boundary_confirmed": False,
        "boundary_reference_index": None,
        "boundary_reference_score": None,
        "s17_payload": {
            "final_reference_index": 1,
            "reference_price_10k": 9.84,
            "reference_score": 94.0,
            "target_score": 94.5,
            "manual_review_required": True,
            "manual_review_reasons": [
                "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING",
                "SAMPLE_SHORTAGE_MANUAL_REVIEW",
            ],
        },
        "pricing": {
            "base_reference_price_yuan": 98400,
            "target_guazi_listing_price_yuan": 96400,
            "guazi_service_fee_yuan": 1500,
            "guazi_net_payout_yuan": 94900,
            "guazi_return_price_yuan": 94900,
            "cost_yuan": 1000,
            "profit_rate": 0.08,
            "profit_yuan": 7472,
            "suggested_purchase_price_yuan": 84928,
            "manual_review_required": True,
        },
    }


if __name__ == "__main__":
    unittest.main()
