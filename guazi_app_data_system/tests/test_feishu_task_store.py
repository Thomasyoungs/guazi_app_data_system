import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

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

INVALID_TEMPLATE = """定价
车系：雅阁
车型配置：2021款 260TURBO 豪华版
上牌日期：2021-06
表显里程：5.8万公里
颜色：白色
过户次数：1
车况：右前门喷漆，前杠喷漆
"""


class FeishuTaskStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FeishuTaskStore(Path(self.temp.name) / "feishu_tasks", clock=fixed_clock)

    def tearDown(self):
        self.temp.cleanup()

    def create_valid(self, message_id="om_1"):
        return self.store.create_task_from_message(
            text=VALID_TEMPLATE,
            raw_event={"message_id": message_id},
            raw_message_id=message_id,
            raw_sender_id="ou_1",
            raw_chat_id="oc_1",
        )

    def create_invalid(self, message_id="om_bad"):
        return self.store.create_task_from_message(
            text="""定价
车系：雅阁
车型配置：2021款 260TURBO 豪华版
上牌日期：2021-06
颜色：白色
过户次数：1
车况：右前门喷漆，前杠喷漆""",
            raw_event={"message_id": message_id},
            raw_message_id=message_id,
            raw_sender_id="ou_bad",
            raw_chat_id="oc_business",
        )

    def test_create_valid_task_writes_required_files_and_draft_status(self):
        result = self.create_valid()

        self.assertEqual(result.task_id, "FS20260609_0001")
        self.assertEqual(result.status, "WAITING_TARGET_CONFIRMATION")
        task_dir = self.store.task_dir(result.task_id)
        for name in ["raw_message.json", "target_task_draft.json", "validation_result.json", "status.json"]:
            self.assertTrue((task_dir / name).exists())
        self.assertFalse((task_dir.parent.parent / "current_target_task.json").exists())

    def test_create_invalid_task_writes_invalid_status(self):
        result = self.create_invalid()

        self.assertFalse(result.success)
        self.assertEqual(result.status, "TARGET_INFO_NEEDS_CORRECTION")
        validation = self.read_task_json(result.task_id, "validation_result.json")
        self.assertIn("表显里程", validation["missing_required_fields"])
        status = self.read_task_json(result.task_id, "status.json")
        self.assertEqual(status["business_status"], "TARGET_INFO_NEEDS_CORRECTION")
        self.assertEqual(status["recommended_next_action"], "ask-sender-to-resend-target-info")
        delivery = self.read_task_json(result.task_id, "target_info_correction_delivery.json")
        self.assertEqual(delivery["business_chat_id"], "oc_business")
        self.assertEqual(delivery["sender_open_id"], "ou_bad")
        self.assertIn("表显里程", delivery["reply_text"])
        self.assertIn("重新发送整条目标车源信息", delivery["reply_text"])
        for forbidden in ["PowerShell", "adb", "uiautomator", "--run-first-stage", "current_target_task.json", "run_id", "generation_id"]:
            self.assertNotIn(forbidden, delivery["reply_text"])

    def test_confirm_waiting_task_success(self):
        created = self.create_valid()

        result = self.store.confirm_task(created.task_id)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "QUEUED")
        self.assertIn("【定价已开始】FS20260609_0001", result.reply_text)
        self.assertIn("系统已开始自动定价，请等待结果。", result.reply_text)
        task_dir = self.store.task_dir(created.task_id)
        self.assertTrue((task_dir / "current_target_task.snapshot.json").exists())
        self.assertTrue((task_dir / "current_target_task.preview.json").exists())
        self.assertFalse((self.store.data_dir / "current_target_task.json").exists())

    def test_confirm_invalid_task_fails(self):
        created = self.create_invalid()

        result = self.store.confirm_task(created.task_id)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "TARGET_INFO_NEEDS_CORRECTION")
        self.assertIn("这台车源信息需要修改", result.reply_text)
        self.assertIn("重新发送完整目标车源信息", result.reply_text)

    def test_resending_complete_target_creates_new_task_without_overwriting_old_one(self):
        bad = self.create_invalid(message_id="om_bad_first")
        good = self.create_valid(message_id="om_good_retry")

        self.assertEqual(bad.task_id, "FS20260609_0001")
        self.assertEqual(good.task_id, "FS20260609_0002")
        self.assertEqual(self.read_task_json(bad.task_id, "status.json")["status"], "TARGET_INFO_NEEDS_CORRECTION")
        self.assertEqual(self.read_task_json(good.task_id, "status.json")["status"], "WAITING_TARGET_CONFIRMATION")

    def test_cancel_draft_task_success(self):
        created = self.create_valid()

        result = self.store.cancel_task(created.task_id)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "CANCELLED")

    def test_cancel_queued_task_success(self):
        created = self.create_valid()
        self.store.confirm_task(created.task_id)

        result = self.store.cancel_task(created.task_id)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "CANCELLED")

    def test_status_query_success(self):
        created = self.create_valid()

        result = self.store.status_reply(created.task_id)

        self.assertTrue(result.success)
        self.assertIn("状态：WAITING_TARGET_CONFIRMATION", result.reply_text)
        self.assertIn("目标车：本田 雅阁 2021款 260TURBO 豪华版", result.reply_text)

    def test_unknown_task_id_rejects_confirm(self):
        result = self.store.confirm_task("FS20260609_9999")

        self.assertFalse(result.success)
        self.assertIn("未找到任务", result.reply_text)

    def test_duplicate_message_id_does_not_create_second_task(self):
        first = self.create_valid(message_id="om_dup")
        second = self.create_valid(message_id="om_dup")

        self.assertEqual(first.task_id, second.task_id)
        self.assertTrue(second.duplicate)
        task_dirs = [path for path in self.store.base_dir.iterdir() if path.is_dir()]
        self.assertEqual(len(task_dirs), 1)

    def test_duplicate_confirm_does_not_change_task(self):
        created = self.create_valid()
        first = self.store.confirm_task(created.task_id)
        second = self.store.confirm_task(created.task_id)

        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertIn("【定价已开始】FS20260609_0001", second.reply_text)
        self.assertIn("系统已开始自动定价，请等待结果。", second.reply_text)

    def test_cancelled_task_cannot_confirm(self):
        created = self.create_valid()
        self.store.cancel_task(created.task_id)

        result = self.store.confirm_task(created.task_id)

        self.assertFalse(result.success)
        self.assertIn("已取消", result.reply_text)
        self.assertIn("重新发送目标车源", result.reply_text)

    def read_task_json(self, task_id, filename):
        return json.loads((self.store.task_dir(task_id) / filename).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
