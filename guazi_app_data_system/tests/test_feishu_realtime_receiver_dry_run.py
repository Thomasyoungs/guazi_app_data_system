import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_realtime_receiver import (  # noqa: E402
    HELP_TEXT,
    TEST_REPLY,
    handle_realtime_event,
    listen_forever,
    run_dry_run_event,
)
from feishu_group_bindings import FeishuGroupBindings  # noqa: E402
from feishu_task_store import FeishuTaskStore  # noqa: E402


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


def fixed_clock():
    return datetime(2026, 6, 9, 8, 30, tzinfo=timezone.utc)


def feishu_event(text, *, message_id="om_test"):
    return {
        "schema": "2.0",
        "event": {
            "sender": {
                "sender_id": {
                    "open_id": "ou_sender",
                }
            },
            "message": {
                "message_id": message_id,
                "chat_id": "oc_chat",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }


class AttrObject:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def typed_feishu_event(text, *, message_id="om_typed"):
    return AttrObject(
        schema="2.0",
        header=AttrObject(event_type="im.message.receive_v1", access_key="access-key-must-not-leak", ticket="ticket-must-not-leak"),
        event=AttrObject(
            sender=AttrObject(sender_id=AttrObject(open_id="ou_sender")),
            message=AttrObject(
                message_id=message_id,
                chat_id="oc_typed",
                message_type="text",
                content=json.dumps({"text": text}, ensure_ascii=False),
            ),
        ),
    )


class FeishuRealtimeReceiverDryRunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)
        self.task_root = self.temp_path / "data" / "feishu_tasks"
        self.store = FeishuTaskStore(self.task_root, clock=fixed_clock)
        self.group_bindings = FeishuGroupBindings(self.temp_path / "data" / "feishu_group_bindings.json", clock=fixed_clock)
        self.group_bindings.set_business_chat(chat_id="oc_chat", chat_name="mock business", created_by="ou_admin")

    def tearDown(self):
        self.temp.cleanup()

    def test_test_message_dry_run_replies_without_feishu_env(self):
        with patch.dict(os.environ, {}, clear=True):
            result = handle_realtime_event(feishu_event("测试"), store=self.store, send_live=False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["reply_text"], TEST_REPLY)
        self.assertTrue(result["send_result"]["dry_run"])
        self.assertEqual(result["send_result"]["chat_id"], "oc_chat")

    def test_help_message_dry_run_replies_with_existing_help_text(self):
        result = handle_realtime_event(feishu_event("帮助"), store=self.store, send_live=False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["reply_text"], HELP_TEXT)
        self.assertIn("请回复：确认", result["reply_text"])
        self.assertTrue(result["send_result"]["dry_run"])

    def test_typed_test_message_enters_gateway_and_replies(self):
        with patch.dict(sys.modules, {"lark_oapi": None}):
            result = handle_realtime_event(typed_feishu_event("测试"), store=self.store, send_live=False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["reply_text"], TEST_REPLY)
        self.assertEqual(result["event"]["raw_message_id"], "om_typed")
        self.assertEqual(result["event"]["raw_sender_id"], "ou_sender")
        self.assertEqual(result["event"]["receive_id"], "oc_typed")
        self.assertTrue(result["send_result"]["dry_run"])
        self.assertEqual(result["send_result"]["chat_id"], "oc_typed")

    def test_typed_help_message_enters_gateway_and_replies(self):
        with patch.dict(sys.modules, {"lark_oapi": None}):
            result = handle_realtime_event(typed_feishu_event("帮助", message_id="om_help"), store=self.store, send_live=False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["reply_text"], HELP_TEXT)
        self.assertIn("请回复：确认", result["reply_text"])

    def test_safe_logs_do_not_include_sensitive_values(self):
        with patch.dict(sys.modules, {"lark_oapi": None}):
            with self.assertLogs("feishu_realtime_receiver", level="INFO") as captured:
                result = handle_realtime_event(typed_feishu_event("测试"), store=self.store, send_live=False)

        self.assertTrue(result["ok"])
        logs = "\n".join(captured.output)
        self.assertIn("received feishu event object:", logs)
        self.assertIn("message_type=text", logs)
        self.assertIn("chat_id_present=True", logs)
        self.assertNotIn("access-key-must-not-leak", logs)
        self.assertNotIn("ticket-must-not-leak", logs)
        self.assertNotIn("tenant_access_token", logs)

    def test_template_message_creates_draft_only(self):
        result = handle_realtime_event(feishu_event(VALID_TEMPLATE, message_id="om_template"), store=self.store, send_live=False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "create_task")
        self.assertEqual(result["task_id"], "FS20260609_0001")
        task_dir = self.store.task_dir(result["task_id"])
        self.assertTrue((task_dir / "target_task_draft.json").exists())
        self.assertTrue((task_dir / "raw_event.json").exists())
        self.assertFalse((self.temp_path / "data" / "current_target_task.json").exists())
        self.assertFalse((self.temp_path / "runtime" / "pricing.lock").exists())
        self.assertIn("请确认目标车信息", result["reply_text"])
        self.assertIn("确认无误请回复：确认", result["reply_text"])

    def test_live_send_message_id_updates_confirm_card_binding_without_real_network(self):
        with patch("feishu_realtime_receiver.send_text_message") as send_mock:
            send_mock.return_value = {"ok": True, "dry_run": False, "message_id": "om_confirm_card_live"}
            result = handle_realtime_event(feishu_event(VALID_TEMPLATE, message_id="om_template"), store=self.store, send_live=True, group_bindings=self.group_bindings)

        self.assertTrue(result["ok"])
        status = json.loads((self.store.task_dir(result["task_id"]) / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["confirm_card_message_id"], "om_confirm_card_live")

    def test_live_confirm_passes_allow_app_run_to_dispatch_kick_without_real_network(self):
        kick_calls = []

        def fake_kick(**kwargs):
            kick_calls.append(kwargs)
            return {"ok": True, "dispatch_once_called": False, "dispatch_once_started_background": True}

        with patch("feishu_realtime_receiver.send_text_message") as send_mock:
            send_mock.return_value = {"ok": True, "dry_run": False, "message_id": "om_live_reply"}
            created = handle_realtime_event(
                feishu_event(VALID_TEMPLATE, message_id="om_template"),
                store=self.store,
                send_live=True,
                group_bindings=self.group_bindings,
            )
            result = handle_realtime_event(
                feishu_event("确认", message_id="om_confirm_live"),
                store=self.store,
                send_live=True,
                group_bindings=self.group_bindings,
                dispatch_kicker=fake_kick,
            )

        self.assertTrue(created["ok"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "confirm_task")
        self.assertTrue(kick_calls[0]["allow_app_run"])
        self.assertTrue(result["dispatch_kick"]["dispatch_once_started_background"])
        status = json.loads((self.store.task_dir(created["task_id"]) / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "QUEUED")

    def test_confirm_only_updates_draft_to_queued(self):
        created = handle_realtime_event(feishu_event(VALID_TEMPLATE, message_id="om_template"), store=self.store, send_live=False)

        result = handle_realtime_event(feishu_event("确认", message_id="om_confirm"), store=self.store, send_live=False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "confirm_task")
        self.assertIn("【定价已开始】FS20260609_0001", result["reply_text"])
        self.assertIn("系统已开始自动定价，请等待结果。", result["reply_text"])
        status = json.loads((self.store.task_dir(created["task_id"]) / "status.json").read_text(encoding="utf-8"))
        draft = json.loads((self.store.task_dir(created["task_id"]) / "target_task_draft.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "QUEUED")
        self.assertEqual(draft["status"], "QUEUED")
        self.assertFalse((self.temp_path / "data" / "current_target_task.json").exists())
        self.assertFalse((self.temp_path / "runtime" / "pricing.lock").exists())

    def test_duplicate_confirm_empty_reply_skips_send(self):
        handle_realtime_event(feishu_event(VALID_TEMPLATE, message_id="om_template"), store=self.store, send_live=False)
        first = handle_realtime_event(feishu_event("确认", message_id="om_confirm_once"), store=self.store, send_live=False)
        second = handle_realtime_event(feishu_event("确认", message_id="om_confirm_once"), store=self.store, send_live=False)

        self.assertEqual("confirm_task", first["action"])
        self.assertEqual("duplicate_confirm_ignored", second["action"])
        self.assertEqual("", second["reply_text"])
        self.assertTrue(second["send_result"]["skipped"])
        self.assertEqual("EMPTY_REPLY_TEXT", second["send_result"]["skip_reason"])

    def test_run_dry_run_event_writes_reply_preview_next_to_sample_event(self):
        event_path = self.temp_path / "sample_event.json"
        event_path.write_text(json.dumps(feishu_event("测试"), ensure_ascii=False), encoding="utf-8")

        result = run_dry_run_event(event_path, task_root=self.task_root)

        preview_path = Path(result["reply_preview_path"])
        self.assertTrue(result["ok"])
        self.assertEqual(preview_path, self.temp_path / "reply_preview.txt")
        self.assertEqual(preview_path.read_text(encoding="utf-8").strip(), TEST_REPLY)

    def test_listen_without_env_fails_before_sdk_or_network(self):
        with patch.dict(os.environ, {}, clear=True):
            result = listen_forever(task_root=self.task_root)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "FEISHU_ENV_MISSING")


if __name__ == "__main__":
    unittest.main()
