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

from feishu_gateway import handle_event, sync_manual_review_to_supervisor  # noqa: E402
from feishu_group_bindings import FeishuGroupBindings  # noqa: E402
from feishu_task_store import FeishuTaskStore  # noqa: E402
from pricing_runner import PricingRunner  # noqa: E402


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

FORBIDDEN_USER_TEXT = [
    "PowerShell",
    "--run-first-stage",
    "--run-second-stage",
    "--requeue-second-stage",
    "--revalidate-result",
    "--manual-confirm-price",
    "--manual-review-note",
    "--send-result",
    "adb",
    "uiautomator",
    "run_id",
    "generation_id",
    "status.json",
    "runner_result",
    "pricing_result.json",
    "WAITING_MANUAL_PRICE",
    "NEEDS_REVIEW",
    "FULL_CHAIN_MANUAL_REVIEW_DONE",
    "dispatcher",
    "runner",
    "current_target_task",
    "pricing.lock",
    "STALE_RUN_RESULT_IGNORED",
]


def fixed_clock():
    return datetime(2026, 6, 14, 8, 30, tzinfo=timezone.utc)


def event(text, *, message_id, sender_id="ou_sales_a", chat_id="oc_business", reply_to_message_id=None):
    return {
        "message_id": message_id,
        "sender_id": sender_id,
        "chat_id": chat_id,
        "reply_to_message_id": reply_to_message_id,
        "text": text,
    }


def roles():
    return {
        "business_chat_ids": ["oc_business"],
        "supervisor_chat_ids": ["oc_supervisor"],
        "supervisor_open_ids": ["ou_supervisor"],
        "admin_chat_ids": ["oc_admin"],
    }


class FeishuMultiUserQueueSupervisorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task_root = self.root / "data" / "feishu_tasks"
        self.store = FeishuTaskStore(self.task_root, clock=fixed_clock)
        self.group_bindings = FeishuGroupBindings(self.root / "data" / "feishu_group_bindings.json", clock=fixed_clock)
        self.group_bindings.set_business_chat(chat_id="oc_business", chat_name="mock business", created_by="ou_admin")
        self.group_bindings.set_supervisor_chat(chat_id="oc_supervisor", chat_name="mock supervisor", created_by="ou_admin")

    def tearDown(self):
        self.temp.cleanup()

    def test_two_sales_create_independent_waiting_tasks(self):
        a = handle_event(event(VALID_TEMPLATE, message_id="om_a", sender_id="ou_sales_a"), store=self.store)
        b = handle_event(event(VALID_TEMPLATE, message_id="om_b", sender_id="ou_sales_b"), store=self.store)

        self.assertEqual(a["task_id"], "FS20260614_0001")
        self.assertEqual(b["task_id"], "FS20260614_0002")
        self.assertEqual(self.status(a["task_id"])["status"], "WAITING_TARGET_CONFIRMATION")
        self.assertEqual(self.status(b["task_id"])["status"], "WAITING_TARGET_CONFIRMATION")
        self.assertNotEqual(self.status(a["task_id"])["confirm_card_message_id"], self.status(b["task_id"])["confirm_card_message_id"])

    def test_reply_to_confirm_card_only_confirms_bound_task(self):
        a = handle_event(event(VALID_TEMPLATE, message_id="om_a", sender_id="ou_sales_a"), store=self.store)
        b = handle_event(event(VALID_TEMPLATE, message_id="om_b", sender_id="ou_sales_b"), store=self.store)
        a_card = self.status(a["task_id"])["confirm_card_message_id"]
        b_card = self.status(b["task_id"])["confirm_card_message_id"]

        a_confirm = handle_event(
            event("确认", message_id="om_a_confirm", sender_id="ou_sales_a", reply_to_message_id=a_card),
            store=self.store,
        )
        b_confirm = handle_event(
            event("确认", message_id="om_b_confirm", sender_id="ou_sales_b", reply_to_message_id=b_card),
            store=self.store,
        )

        self.assertEqual(a_confirm["task_id"], a["task_id"])
        self.assertEqual(b_confirm["task_id"], b["task_id"])
        self.assertEqual(self.status(a["task_id"])["status"], "QUEUED")
        self.assertEqual(self.status(b["task_id"])["status"], "QUEUED")

    def test_multiple_waiting_without_reply_is_rejected(self):
        handle_event(event(VALID_TEMPLATE, message_id="om_a1", sender_id="ou_sales_a"), store=self.store)
        handle_event(event(VALID_TEMPLATE, message_id="om_a2", sender_id="ou_sales_a"), store=self.store)

        result = handle_event(event("确认", message_id="om_confirm", sender_id="ou_sales_a"), store=self.store)

        self.assertFalse(result["ok"])
        self.assertIn("多个待确认任务", result["reply_text"])

    def test_single_waiting_without_reply_is_allowed(self):
        created = handle_event(event(VALID_TEMPLATE, message_id="om_a1", sender_id="ou_sales_a"), store=self.store)

        result = handle_event(event("确认", message_id="om_confirm", sender_id="ou_sales_a"), store=self.store)

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_id"], created["task_id"])
        self.assertEqual(result["status"], "QUEUED")

    def test_dispatcher_takes_one_queued_task_and_writes_current_task_at_run_time(self):
        first = self.confirm_created("om_a", "ou_sales_a")
        second = self.confirm_created("om_b", "ou_sales_b")
        self.assertFalse((self.root / "data" / "current_target_task.json").exists())

        dispatched = self.store.dispatch_next_queued_task_dry_run()

        self.assertTrue(dispatched.success)
        self.assertEqual(dispatched.task_id, first)
        self.assertEqual(self.status(first)["status"], "APP_CONTROL_LOCKED")
        self.assertEqual(self.status(second)["status"], "QUEUED")
        current = self.read_json(self.root / "data" / "current_target_task.json")
        self.assertEqual(current["task_id"], first)

    def test_supervisor_review_sync_writes_binding_and_business_copy(self):
        task_id = self.create_needs_review_task("FS20260614_0001")

        result = sync_manual_review_to_supervisor(task_id, store=self.store, roles=roles())

        self.assertTrue(result["ok"])
        self.assertEqual(self.status(task_id)["status"], "WAITING_MANUAL_PRICE")
        self.assertEqual(self.status(task_id)["supervisor_chat_id"], "oc_supervisor")
        self.assertIn("【本次定价进入人工复核】", result["business_reply_text"])
        self.assertIn("【人工复核定价】FS20260614_0001", result["supervisor_reply_text"])
        self.assertIn("请直接回复最终收车价", result["supervisor_reply_text"])
        self.assert_user_text_safe(result["business_reply_text"], result["supervisor_reply_text"])

    def test_supervisor_review_sync_sends_business_and_supervisor_notices(self):
        task_id = self.create_needs_review_task("FS20260614_0001")
        calls = []

        def sender(**kwargs):
            calls.append(kwargs)
            return {
                "ok": True,
                "dry_run": kwargs["dry_run"],
                "chat_id": kwargs["chat_id"],
                "message_id": f"om_manual_{len(calls)}",
            }

        result = sync_manual_review_to_supervisor(
            task_id,
            store=self.store,
            roles=roles(),
            send_messages=True,
            dry_run=True,
            message_sender=sender,
        )

        self.assertTrue(result["ok"])
        self.assertEqual([call["chat_id"] for call in calls], ["oc_business", "oc_supervisor"])
        self.assertIn("【本次定价进入人工复核】", calls[0]["text"])
        self.assertIn("【人工复核定价】FS20260614_0001", calls[1]["text"])
        delivery = self.read_json(self.store.task_dir(task_id) / "manual_review_feedback_delivery.json")
        self.assertTrue(delivery["send_success"])
        self.assertEqual(delivery["delivery_type"], "manual_review_notice")
        self.assertEqual(delivery["target_group"], "business_and_supervisor")
        self.assertEqual(delivery["idempotency_key"], f"manual_review_notice:{task_id}")
        self.assertEqual(len(delivery["deliveries"]), 2)
        status = self.status(task_id)
        self.assertTrue(status["manual_review_required"])
        self.assertTrue(status["waiting_manual_price"])
        self.assertTrue(status["manual_review_business_notice_sent"])
        self.assertTrue(status["manual_review_supervisor_notice_sent"])
        self.assertEqual(status["manual_review_delivery_count"], 1)

    def test_supervisor_review_delivery_is_idempotent(self):
        task_id = self.create_needs_review_task("FS20260614_0001")
        calls = []

        def sender(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "dry_run": True, "chat_id": kwargs["chat_id"]}

        first = sync_manual_review_to_supervisor(
            task_id,
            store=self.store,
            roles=roles(),
            send_messages=True,
            dry_run=True,
            message_sender=sender,
        )
        second = sync_manual_review_to_supervisor(
            task_id,
            store=self.store,
            roles=roles(),
            send_messages=True,
            dry_run=True,
            message_sender=sender,
        )

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["manual_review_delivery_result"]["already_sent"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.status(task_id)["manual_review_delivery_count"], 1)

    def test_supervisor_review_send_failure_records_error_without_marking_sent(self):
        task_id = self.create_needs_review_task("FS20260614_0001")
        calls = []

        def sender(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                return {"ok": False, "dry_run": True, "error_code": "MOCK_FEISHU_FAILED"}
            return {"ok": True, "dry_run": True, "chat_id": kwargs["chat_id"]}

        result = sync_manual_review_to_supervisor(
            task_id,
            store=self.store,
            roles=roles(),
            send_messages=True,
            dry_run=True,
            message_sender=sender,
        )

        self.assertFalse(result["ok"])
        delivery = self.read_json(self.store.task_dir(task_id) / "manual_review_feedback_delivery.json")
        self.assertFalse(delivery["send_success"])
        self.assertIn("MOCK_FEISHU_FAILED", delivery["error"])
        status = self.status(task_id)
        self.assertFalse(status["manual_review_business_notice_sent"])
        self.assertFalse(status["manual_review_supervisor_notice_sent"])
        self.assertIn("MOCK_FEISHU_FAILED", status["manual_review_delivery_error"])

    def test_supervisor_reply_to_review_card_confirms_bound_task(self):
        task_id = self.create_waiting_manual_price_task("FS20260614_0001")
        card_id = self.status(task_id)["supervisor_review_card_message_id"]

        result = handle_event(
            event("8.6万", message_id="om_price", sender_id="ou_supervisor", chat_id="oc_supervisor", reply_to_message_id=card_id),
            store=self.store,
            roles=roles(),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_id"], task_id)
        self.assertEqual(self.status(task_id)["status"], "MANUAL_REVIEW_CONFIRMED")
        pricing = self.read_json(self.store.task_dir(task_id) / "pricing_result.json")
        self.assertEqual(pricing["manual_confirmed_purchase_price_yuan"], 86000)

    def test_multiple_waiting_manual_price_without_binding_is_rejected(self):
        self.create_waiting_manual_price_task("FS20260614_0001")
        self.create_waiting_manual_price_task("FS20260614_0002")

        result = handle_event(
            event("8.6万", message_id="om_price", sender_id="ou_supervisor", chat_id="oc_supervisor"),
            store=self.store,
            roles=roles(),
        )

        self.assertFalse(result["ok"])
        self.assertIn("多个待复核任务", result["reply_text"])

    def test_supervisor_task_id_price_binds_selected_task(self):
        self.create_waiting_manual_price_task("FS20260614_0001")
        selected = self.create_waiting_manual_price_task("FS20260614_0002")

        result = handle_event(
            event(f"{selected} 8.6万", message_id="om_price", sender_id="ou_supervisor", chat_id="oc_supervisor"),
            store=self.store,
            roles=roles(),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_id"], selected)
        self.assertEqual(self.status(selected)["status"], "MANUAL_REVIEW_CONFIRMED")

    def test_supervisor_task_id_price_binds_selected_task_without_system_suggested_price(self):
        first = self.create_waiting_manual_price_task("FS20260614_0001")
        selected = self.create_waiting_manual_price_task("FS20260614_0002")
        pricing_path = self.store.task_dir(selected) / "pricing_result.json"
        pricing = self.read_json(pricing_path)
        pricing["pricing"] = {
            "status": "NEEDS_REVIEW",
            "reason": "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
            "manual_review_required": True,
        }
        self.write_json(pricing_path, pricing)

        result = handle_event(
            event(f"{selected}，8.6万", message_id="om_price_no_suggested", sender_id="ou_supervisor", chat_id="oc_supervisor"),
            store=self.store,
            roles=roles(),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_id"], selected)
        self.assertEqual(self.status(first)["status"], "WAITING_MANUAL_PRICE")
        self.assertEqual(self.status(selected)["status"], "MANUAL_REVIEW_CONFIRMED")
        pricing = self.read_json(pricing_path)
        self.assertEqual(pricing["final_purchase_price_yuan"], 86000)
        self.assertTrue(pricing["system_suggested_price_missing"])
        self.assertIn("最终收车价：86000 元", result["reply_text"])
        self.assertNotIn("WAITING_MANUAL_PRICE", result["reply_text"])
        self.assertNotIn("suggested_purchase_price_yuan", result["reply_text"])

    def test_sales_price_reply_is_rejected(self):
        self.create_waiting_manual_price_task("FS20260614_0001")

        result = handle_event(
            event("8.6万", message_id="om_price", sender_id="ou_sales_a", chat_id="oc_supervisor"),
            store=self.store,
            roles=roles(),
        )

        self.assertFalse(result["ok"])
        self.assertIn("请主管回复人工确认价", result["reply_text"])

    def test_empty_supervisor_roles_forbid_manual_price(self):
        self.create_waiting_manual_price_task("FS20260614_0001")

        result = handle_event(
            event("8.6万", message_id="om_price", sender_id="ou_supervisor", chat_id="oc_supervisor"),
            store=self.store,
            roles={"supervisor_open_ids": []},
        )

        self.assertFalse(result["ok"])
        self.assertIn("配置主管 open_id", result["reply_text"])

    def test_confirmed_manual_price_is_not_overwritten(self):
        task_id = self.create_waiting_manual_price_task("FS20260614_0001")
        handle_event(
            event(f"{task_id} 8.6万", message_id="om_price_once", sender_id="ou_supervisor", chat_id="oc_supervisor"),
            store=self.store,
            roles=roles(),
        )

        result = handle_event(
            event(f"{task_id} 8.5万", message_id="om_price_twice", sender_id="ou_supervisor", chat_id="oc_supervisor"),
            store=self.store,
            roles=roles(),
        )

        self.assertFalse(result["ok"])
        self.assertIn("已人工确认", result["reply_text"])
        pricing = self.read_json(self.store.task_dir(task_id) / "pricing_result.json")
        self.assertEqual(pricing["manual_confirmed_purchase_price_yuan"], 86000)

    def test_result_sent_price_reply_is_not_overwritten(self):
        task_id = self.create_waiting_manual_price_task("FS20260614_0001")
        status = self.status(task_id)
        status["status"] = "RESULT_SENT"
        self.write_json(self.store.task_dir(task_id) / "status.json", status)

        result = handle_event(
            event(f"{task_id} 8.5万", message_id="om_price_sent", sender_id="ou_supervisor", chat_id="oc_supervisor"),
            store=self.store,
            roles=roles(),
        )

        self.assertFalse(result["ok"])
        self.assertIn("结果已发送", result["reply_text"])

    def test_send_result_wrapper_still_dry_runs(self):
        task_id = self.create_ready_to_send_task("FS20260614_0001")
        runner = PricingRunner(task_root=self.task_root, data_dir=self.root / "data", runtime_lock_path=self.root / "runtime" / "pricing.lock", clock=fixed_clock)

        with patch("pricing_runner.send_text_message") as send_mock:
            send_mock.return_value = {"ok": True, "dry_run": True}
            result = runner.send_result(task_id)

        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        send_mock.assert_called_once()
        self.assertTrue(send_mock.call_args.kwargs["dry_run"])

    def confirm_created(self, message_id, sender_id):
        created = handle_event(event(VALID_TEMPLATE, message_id=message_id, sender_id=sender_id), store=self.store)
        card_id = self.status(created["task_id"])["confirm_card_message_id"]
        confirmed = handle_event(event("确认", message_id=f"{message_id}_confirm", sender_id=sender_id, reply_to_message_id=card_id), store=self.store)
        self.assertTrue(confirmed["ok"])
        return created["task_id"]

    def create_needs_review_task(self, task_id):
        task_dir = self.store.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "NEEDS_REVIEW",
                "technical_status": "SUCCEEDED",
                "business_status": "NEEDS_REVIEW",
                "business_chat_id": "oc_business",
                "sender_open_id": "ou_sales_a",
                "raw_chat_id": "oc_business",
                "created_at": "2026-06-14T08:30:00+00:00",
                "updated_at": "2026-06-14T08:30:00+00:00",
            },
        )
        self.write_json(task_dir / "pricing_result.json", full_chain_manual_review_payload())
        return task_id

    def create_waiting_manual_price_task(self, task_id):
        self.create_needs_review_task(task_id)
        result = sync_manual_review_to_supervisor(task_id, store=self.store, roles=roles())
        self.assertTrue(result["ok"])
        return task_id

    def create_ready_to_send_task(self, task_id):
        task_dir = self.store.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "MANUAL_REVIEW_CONFIRMED",
                "technical_status": "SUCCEEDED",
                "business_status": "MANUAL_REVIEW_CONFIRMED",
                "recommended_next_action": "ready-to-send",
                "raw_chat_id": "oc_business_sensitive",
                "created_at": "2026-06-14T08:30:00+00:00",
                "updated_at": "2026-06-14T08:30:00+00:00",
            },
        )
        self.write_json(task_dir / "raw_message.json", {"raw_chat_id": "oc_business_sensitive"})
        (task_dir / "feishu_result_reply.preview.txt").write_text("人工复核已确认\n最终收车价：86000 元\n", encoding="utf-8")
        return task_id

    def assert_user_text_safe(self, *texts):
        joined = "\n".join(texts)
        for forbidden in FORBIDDEN_USER_TEXT:
            self.assertNotIn(forbidden, joined)

    def status(self, task_id):
        return self.read_json(self.store.task_dir(task_id) / "status.json")

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))


def full_chain_manual_review_payload():
    return {
        "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "final_status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "current_state": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "target_vehicle": "本田 雅阁 2021款 260TURBO 豪华版",
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
            "profit_yuan": 7592,
            "suggested_purchase_price_yuan": 86308,
            "manual_review_required": True,
        },
    }


if __name__ == "__main__":
    unittest.main()
