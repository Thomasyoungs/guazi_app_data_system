import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_event_adapter import adapt_feishu_event, adapt_feishu_event_result  # noqa: E402


def feishu_event(text="测试", *, message_type="text", message_id="om_test"):
    return {
        "schema": "2.0",
        "header": {
            "event_id": "evt_1",
            "token": "real-token-must-not-leak",
        },
        "event": {
            "sender": {
                "sender_id": {
                    "open_id": "ou_sender",
                }
            },
            "message": {
                "message_id": message_id,
                "chat_id": "oc_chat",
                "message_type": message_type,
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }


def feishu_event_with_content(content, *, message_type="text", include_chat_id=True):
    event = feishu_event("", message_type=message_type)
    message = event["event"]["message"]
    message["content"] = content
    if not include_chat_id:
        del message["chat_id"]
    return event


class AttrObject:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def typed_feishu_event_object(text="测试"):
    return AttrObject(
        schema="2.0",
        header=AttrObject(event_id="evt_1", event_type="im.message.receive_v1", access_key="must-not-leak"),
        event=AttrObject(
            sender=AttrObject(sender_id=AttrObject(open_id="ou_sender", user_id="user_1", union_id="union_1"), sender_type="user"),
            message=AttrObject(
                message_id="om_typed",
                chat_id="oc_typed",
                chat_type="p2p",
                message_type="text",
                content=json.dumps({"text": text}, ensure_ascii=False),
            ),
        ),
    )


class FeishuEventAdapterTest(unittest.TestCase):
    def test_adapts_text_message_to_gateway_shape(self):
        result = adapt_feishu_event(feishu_event("帮助"))

        self.assertEqual(result["raw_message_id"], "om_test")
        self.assertEqual(result["raw_sender_id"], "ou_sender")
        self.assertEqual(result["raw_chat_id"], "oc_chat")
        self.assertEqual(result["receive_id"], "oc_chat")
        self.assertEqual(result["chat_id"], "oc_chat")
        self.assertEqual(result["text"], "帮助")
        self.assertIn("created_at", result)
        self.assertEqual(result["message_type"], "text")
        self.assertEqual(result["adapter_debug"]["marshal_method"], "dict")
        self.assertEqual(result["raw_event"]["header"]["token"], "<REDACTED>")

    def test_adapts_object_with_data_dict(self):
        class EventObject:
            data = feishu_event("测试")

        with patch.dict(sys.modules, {"lark_oapi": None}):
            result = adapt_feishu_event(EventObject())

        self.assertEqual(result["text"], "测试")
        self.assertEqual(result["raw_message_id"], "om_test")

    def test_adapts_typed_event_object_without_lark_sdk(self):
        with patch.dict(sys.modules, {"lark_oapi": None}):
            result = adapt_feishu_event(typed_feishu_event_object("测试"))

        self.assertEqual(result["text"], "测试")
        self.assertEqual(result["event_type"], "im.message.receive_v1")
        self.assertEqual(result["raw_message_id"], "om_typed")
        self.assertEqual(result["raw_sender_id"], "ou_sender")
        self.assertEqual(result["raw_chat_id"], "oc_typed")
        self.assertEqual(result["receive_id"], "oc_typed")
        self.assertEqual(result["raw_event"]["header"]["access_key"], "<REDACTED>")

    def test_adapts_when_lark_json_marshal_returns_json_string(self):
        raw = object()
        fake_lark = types.SimpleNamespace(
            JSON=types.SimpleNamespace(
                marshal=lambda _data: json.dumps(feishu_event("测试", message_id="om_marshal_str"), ensure_ascii=False)
            )
        )

        with patch.dict(sys.modules, {"lark_oapi": fake_lark}):
            result = adapt_feishu_event(raw)

        self.assertEqual(result["text"], "测试")
        self.assertEqual(result["raw_message_id"], "om_marshal_str")
        self.assertEqual(result["adapter_debug"]["marshal_method"], "lark.JSON.marshal:str")
        self.assertTrue(result["adapter_debug"]["marshal_success"])

    def test_adapts_when_lark_json_marshal_returns_dict(self):
        raw = object()
        fake_lark = types.SimpleNamespace(
            JSON=types.SimpleNamespace(
                marshal=lambda _data: feishu_event("帮助", message_id="om_marshal_dict")
            )
        )

        with patch.dict(sys.modules, {"lark_oapi": fake_lark}):
            result = adapt_feishu_event(raw)

        self.assertEqual(result["text"], "帮助")
        self.assertEqual(result["raw_message_id"], "om_marshal_dict")
        self.assertEqual(result["adapter_debug"]["marshal_method"], "lark.JSON.marshal:dict")

    def test_marshal_failure_returns_specific_error_code(self):
        fake_lark = types.SimpleNamespace(
            JSON=types.SimpleNamespace(
                marshal=lambda _data: (_ for _ in ()).throw(RuntimeError("no raw dump"))
            )
        )

        with patch.dict(sys.modules, {"lark_oapi": fake_lark}):
            result = adapt_feishu_event_result(object())

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "FEISHU_EVENT_MARSHAL_FAILED")
        self.assertIn("object_class", result["raw_event"])

    def test_content_json_string_is_parsed(self):
        result = adapt_feishu_event(feishu_event_with_content(json.dumps({"text": "测试"}, ensure_ascii=False)))

        self.assertEqual(result["text"], "测试")

    def test_content_dict_is_parsed(self):
        result = adapt_feishu_event(feishu_event_with_content({"text": "帮助"}))

        self.assertEqual(result["text"], "帮助")

    def test_content_plain_string_is_preserved(self):
        result = adapt_feishu_event(feishu_event_with_content("测试"))

        self.assertEqual(result["text"], "测试")

    def test_rejects_non_text_message(self):
        result = adapt_feishu_event_result(feishu_event("", message_type="image"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "UNSUPPORTED_MESSAGE_TYPE")

    def test_rejects_empty_text_message(self):
        result = adapt_feishu_event_result(feishu_event("   "))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "EMPTY_MESSAGE_TEXT")

    def test_rejects_missing_chat_id(self):
        result = adapt_feishu_event_result(feishu_event_with_content({"text": "测试"}, include_chat_id=False))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "FEISHU_CHAT_ID_MISSING")

    def test_rejects_unsupported_object_without_dumping_sensitive_values(self):
        with patch.dict(sys.modules, {"lark_oapi": None}):
            result = adapt_feishu_event_result(object())

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "UNSUPPORTED_FEISHU_EVENT_OBJECT")
        self.assertIn("object_class", result["raw_event"])


if __name__ == "__main__":
    unittest.main()
