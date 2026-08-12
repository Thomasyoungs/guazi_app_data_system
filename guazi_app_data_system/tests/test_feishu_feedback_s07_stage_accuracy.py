import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from scripts.feishu_task_store import FeishuTaskStore


def fixed_clock():
    return datetime(2026, 6, 27, 13, 30, tzinfo=timezone.utc)


class FeishuS07StageFeedbackAccuracyTest(unittest.TestCase):
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

    def create_s07_blocked_task(self, task_id: str = "FS20260627_0001") -> Path:
        task_dir = self.task_root / task_id
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "ADMIN_INTERVENTION_REQUIRED",
                "technical_status": "FAILED",
                "business_status": "FAILED",
                "errors": ["FIRST_STAGE_NOT_S10_READY", "FAILED"],
                "blocks_queue": True,
                "business_chat_id": "oc_business",
            },
        )
        self.write_json(
            task_dir / "first_stage_result.json",
            {
                "status": "S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT",
                "canonical_error_code": "S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT",
                "error": "Exact target age tick not found before view-result.",
                "flow_state": {
                    "COLOR_FILTER_DONE": True,
                    "AGE_FILTER_DONE": False,
                    "S10_READY": False,
                    "target_color": "黑",
                },
                "context": {
                    "foreground_package": "com.ganji.android.haoche_c",
                    "focused_window": "com.ganji.android.haoche_c/com.guazi.h5.Html5NewContainerActivity",
                    "target_age_years": 5,
                    "screenshot_path": "artifacts/screenshots/s07_age.png",
                    "xml_path": "artifacts/debug/s07_age.xml",
                    "age_action": {
                        "target_age": 5,
                        "expected_age_filter": "5-5",
                        "action_algorithm_used": "visible_tick_interpolation",
                        "direct_track_fastpath_used": True,
                        "target_x": 568,
                        "drag_start_point": [995, 988],
                        "drag_start_inside_selected_handle_bounds": True,
                        "drag_target_point": [568, 988],
                        "left_age_after": 0,
                        "right_age_after": 10,
                        "slider_value_changed": False,
                        "slider_bounds_changed": False,
                        "exact_text_verified": False,
                    },
                },
            },
        )
        return task_dir

    def test_feishu_s07_failure_not_reported_as_not_started_or_phone_env(self):
        task_id = "FS20260627_0001"
        task_dir = self.create_s07_blocked_task(task_id)

        result = self.store.release_blocker_without_active_runner(task_id)

        self.assertTrue(result.success)
        self.assertIn("【本次定价未完成】", result.reply_text)
        self.assertIn("车龄筛选", result.reply_text)
        self.assertNotIn("本次定价未开始", result.reply_text)
        self.assertNotIn("手机执行环境暂不可用", result.reply_text)
        updated = self.read_json(task_dir / "status.json")
        self.assertEqual(updated["canonical_error_code"], "S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT")
        self.assertEqual(updated["highest_stage"], "S07")
        self.assertEqual(updated["original_stage"], "S07")
        self.assertEqual(updated["original_stop_code"], "S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT")
        self.assertEqual(updated["foreground_package"], "com.ganji.android.haoche_c")
        delivery = self.read_json(task_dir / "released_blocker_delivery.json")
        self.assertEqual(delivery["canonical_error_code"], "S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT")
        self.assertNotIn("本次定价未开始", delivery["business_reply_text"])

    def test_admin_feedback_contains_s07_age_trace_fields(self):
        task_id = "FS20260627_0002"
        task_dir = self.create_s07_blocked_task(task_id)

        self.store.release_blocker_without_active_runner(task_id)

        admin_reply = (task_dir / "released_blocker_admin_reply.preview.txt").read_text(encoding="utf-8")
        for expected in (
            "highest_stage=S07",
            "target_age=5",
            "expected_age_filter=5-5",
            "action_algorithm_used=visible_tick_interpolation",
            "direct_track_fastpath_used=True",
            "target_x=568",
            "drag_start_point=[995, 988]",
            "drag_start_inside_selected_handle_bounds=True",
            "slider_value_changed=False",
            "slider_bounds_changed=False",
            "exact_text_verified=False",
            "foreground_package=com.ganji.android.haoche_c",
        ):
            self.assertIn(expected, admin_reply)


if __name__ == "__main__":
    unittest.main()
