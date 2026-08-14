import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from guazi_app_data_system.year_age_filter import calculate_target_age


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "runtime_s01_to_s10_mainline.py"
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_result_formatter import format_result_reply  # noqa: E402
from feishu_task_store import FeishuTaskStore  # noqa: E402
from registration_date_normalizer import normalize_registration_date  # noqa: E402


def load_runtime_module():
    spec = importlib.util.spec_from_file_location("runtime_s01_to_s10_mainline", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeAdbResult:
    success = True
    stdout = ""
    stderr = ""
    returncode = 0


class FakeClient:
    def __init__(self):
        self.commands = []

    def run(self, args, timeout=20):
        self.commands.append(list(args))
        return FakeAdbResult()


class DummyIssues:
    def record(self, code, state_id, message, context, resolution, recognized_text=None, attempts=0):
        return {"code": code, "state_id": state_id, "message": message, "context": context}


def fixed_clock():
    return datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)


def _tick(label: str, x: int) -> dict:
    return {
        "labels": [label],
        "bounds": (x - 20, 600, x + 20, 640),
        "clickable": False,
        "enabled": True,
        "selected": False,
    }


def make_age_snapshot(*, left_age=None, right_age=None, visible_blob="age panel") -> dict:
    nodes = [
        _tick("0", 100),
        _tick("2", 300),
        _tick("4", 500),
        _tick("6", 700),
        _tick("8", 900),
        _tick("10", 1100),
        {"labels": [], "bounds": (100, 660, 1100, 700), "clickable": True, "enabled": True},
        {"labels": [], "bounds": (60, 620, 140, 760), "clickable": True, "enabled": True},
        {"labels": [], "bounds": (1060, 620, 1140, 760), "clickable": True, "enabled": True},
    ]
    snapshot = {
        "nodes": nodes,
        "visible_blob": visible_blob,
        "screenshot_path": "artifacts/screenshots/s07_age.png",
        "xml_path": "artifacts/debug/s07_age.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }
    if left_age is not None:
        snapshot["left_age"] = left_age
    if right_age is not None:
        snapshot["right_age"] = right_age
    return snapshot


class S07TargetAgeYearOnlyAndAgeOneHiddenTickTest(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime_module()

    def test_target_age_uses_year_only_without_month_deduction(self):
        self.assertEqual(calculate_target_age("2025.02", 2025, date(2026, 1, 1)), 1)
        self.assertEqual(calculate_target_age("2025.12", 2025, date(2026, 1, 1)), 1)
        self.assertEqual(calculate_target_age("2026.01", 2026, date(2026, 6, 1)), 0)
        self.assertEqual(calculate_target_age("2024.10", 2024, date(2026, 1, 1)), 2)

    def test_short_registration_date_is_normalized_before_age_calculation(self):
        normalized = normalize_registration_date("25.2")

        self.assertIsNotNone(normalized)
        self.assertEqual(normalized.normalized_date, "2025.02")
        self.assertEqual(calculate_target_age(normalized.normalized_date, normalized.year, date(2026, 6, 1)), 1)

    def test_age_one_hidden_tick_binds_between_visible_0_and_2(self):
        snapshot = make_age_snapshot()

        hidden = self.runtime._s07_hidden_age_tick_info(snapshot, 1)
        bounds = self.runtime._s07_age_target_tick_bounds(snapshot, 1)
        direct_plan = self.runtime._s07_age_direct_track_plan(snapshot, 1)
        evidence = self.runtime._s07_age_slider_evidence(snapshot, 1)

        self.assertTrue(hidden["hidden_age_tick_valid"])
        self.assertEqual(hidden["lower_tick_age"], 0)
        self.assertEqual(hidden["upper_tick_age"], 2)
        self.assertEqual(hidden["ratio_between_ticks"], 0.5)
        self.assertEqual(hidden["age_1_x"], 200)
        self.assertEqual(hidden["target_age_point"], [200, 620])
        self.assertIsNotNone(bounds)
        self.assertEqual(direct_plan["target_x"], 200)
        self.assertEqual(direct_plan["target_x_calculation"], "visible_tick_interpolation")
        self.assertEqual(direct_plan["left_slider_target"], 1)
        self.assertEqual(direct_plan["right_slider_target"], 1)
        self.assertEqual(evidence["s07_visible_age_ticks"], [0, 2, 4, 6, 8, 10])
        self.assertEqual(evidence["age_0_tick_bounds"], [80, 600, 120, 640])
        self.assertEqual(evidence["age_2_tick_bounds"], [280, 600, 320, 640])
        self.assertEqual(evidence["age_1_hidden_tick_x"], 200)
        self.assertEqual(evidence["left_slider_target_age"], 1)
        self.assertEqual(evidence["right_slider_target_age"], 1)
        self.assertEqual(evidence["left_slider_target_x"], 200)
        self.assertEqual(evidence["right_slider_target_x"], 200)
        self.assertTrue(evidence["age_exact_overlap_allowed"])

    def test_age_one_does_not_require_visible_digit_1_tick(self):
        snapshot = make_age_snapshot()

        self.assertIsNone(self.runtime._find_s07_age_tick_node(snapshot, 1))
        self.assertEqual(self.runtime._s07_age_target_point(snapshot, 1), (200, 620))
        self.assertNotEqual(
            self.runtime._s07_missing_age_target_stop_code(1),
            "S07_AGE_SLIDER_TARGET_NOT_FOUND",
        )

    def test_age_one_exact_text_and_one_under_text_verify(self):
        self.assertTrue(self.runtime._verify_exact_age_selected({"visible_blob": "1-1年", "nodes": []}, 1)["exact_age_verified"])
        one_under = self.runtime._verify_exact_age_selected({"visible_blob": "1年以下", "nodes": []}, 1)
        one_within = self.runtime._verify_exact_age_selected({"visible_blob": "1年以内", "nodes": []}, 1)
        one_ambiguous = self.runtime._verify_exact_age_selected({"visible_blob": "1年", "nodes": []}, 1)

        self.assertFalse(one_under["exact_age_verified"])
        self.assertEqual(one_under["stop_code"], "S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED")
        self.assertEqual(one_under["s07_age_verify_text_class"], "broad_range")
        self.assertFalse(one_within["exact_age_verified"])
        self.assertEqual(one_within["stop_code"], "S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED")
        self.assertFalse(one_ambiguous["exact_age_verified"])
        self.assertEqual(one_ambiguous["s07_age_verify_text_class"], "ambiguous")

    def test_age_one_runtime_success_path_allows_left_right_overlap(self):
        initial = make_age_snapshot(left_age=0, right_age=10)
        final = make_age_snapshot(left_age=1, right_age=1, visible_blob="1-1年")
        client = FakeClient()
        context = {
            "client": client,
            "issues": DummyIssues(),
            "task_params": {
                "registration_date": "2025.02",
                "register_year": 2025,
                "current_year": 2026,
                "target_age_formula": "current_year - register_year",
            },
        }

        with (
            mock.patch.object(self.runtime, "_capture", return_value=final),
            mock.patch.object(self.runtime, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.runtime.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.runtime._set_exact_age_from_ticks(context, initial, 1)

        self.assertTrue(evidence["success"])
        self.assertTrue(evidence["direct_track_fastpath_used"])
        self.assertEqual(evidence["target_x"], 200)
        self.assertEqual(evidence["left_age_after"], 1)
        self.assertEqual(evidence["right_age_after"], 1)
        self.assertTrue(evidence["AGE_FILTER_DONE"])
        self.assertIsNone(evidence["s07_age_failure_reason"])
        self.assertEqual(evidence["register_date_raw"], "2025.02")
        self.assertEqual(evidence["register_date_normalized"], "2025.02")
        self.assertEqual(evidence["register_year"], 2025)
        self.assertEqual(evidence["current_business_year"], 2026)
        self.assertEqual(evidence["target_age_calc_rule"], "YEAR_ONLY_CURRENT_YEAR_MINUS_REGISTER_YEAR")
        self.assertEqual(evidence["left_slider_target_age"], 1)
        self.assertEqual(evidence["right_slider_target_age"], 1)
        self.assertEqual(evidence["left_slider_target_x"], 200)
        self.assertEqual(evidence["right_slider_target_x"], 200)
        self.assertIn("1-1", evidence["age_filter_verify_text"])
        self.assertNotEqual(evidence.get("failure_reason"), "S07_RIGHT_AGE_SLIDER_MOVE_NO_EFFECT")
        self.assertNotEqual(evidence.get("failure_reason"), "S07_AGE_TARGET_TICK_NOT_FOUND")
        self.assertEqual(client.commands[0][0:3], ["shell", "input", "swipe"])
        self.assertEqual(client.commands[0][5], "200")

    def test_hidden_11_and_12_ticks_are_not_regressed(self):
        snapshot = make_age_snapshot()
        snapshot["nodes"][6] = {"labels": [], "bounds": (100, 660, 1400, 700), "clickable": True, "enabled": True}
        snapshot["nodes"].append(_tick(self.runtime.S07_AGE_UNLIMITED_LABEL, 1400))

        hidden_11 = self.runtime._s07_hidden_age_tick_info(snapshot, 11)
        hidden_12 = self.runtime._s07_hidden_age_tick_info(snapshot, 12)

        self.assertTrue(hidden_11["hidden_age_tick_valid"])
        self.assertTrue(hidden_12["hidden_age_tick_valid"])
        self.assertEqual(hidden_11["age_11_x"], 1200)
        self.assertEqual(hidden_12["age_12_x"], 1300)

    def test_target_age_zero_still_uses_visible_zero_tick(self):
        snapshot = make_age_snapshot()

        self.assertEqual(self.runtime._s07_age_target_point(snapshot, 0), (100, 620))
        self.assertIsNotNone(self.runtime._find_s07_age_tick_node(snapshot, 0))

    def test_formatter_s07_age_one_feedback_mentions_year_only_rule(self):
        result = format_result_reply(
            task_id="FS20260628_0004",
            status="FAILED",
            errors=["S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE"],
            pricing_result={
                "context": {
                    "registration_date": "2025.02",
                    "target_age_years": 1,
                }
            },
        )

        self.assertIn("2025.02", result.text)
        self.assertIn("1 年车龄", result.text)
        self.assertIn("隐藏刻度", result.text)
        self.assertNotIn("手机执行环境不可用", result.text)
        self.assertNotIn("本次定价未开始", result.text)
        self.assertNotIn("车龄滑块筛选时安全停止", result.text)

    def test_store_s07_age_one_feedback_and_same_stop_code_idempotency(self):
        with tempfile.TemporaryDirectory() as temp:
            task_root = Path(temp) / "data" / "feishu_tasks"
            store = FeishuTaskStore(task_root, clock=fixed_clock)
            task_id = "FS20260628_0004"
            task_dir = task_root / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / "status.json").write_text(
                json.dumps(
                    {
                        "task_id": task_id,
                        "status": "ADMIN_INTERVENTION_REQUIRED",
                        "technical_status": "FAILED",
                        "business_status": "FAILED",
                        "errors": ["S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE", "APP_NOT_FOREGROUND"],
                        "blocks_queue": True,
                        "business_chat_id": "oc_business",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (task_dir / "target_task_draft.json").write_text(
                json.dumps({"registration_date": "2025.02"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (task_dir / "first_stage_result.json").write_text(
                json.dumps(
                    {
                        "status": "S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE",
                        "canonical_error_code": "S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE",
                        "context": {
                            "registration_date": "2025.02",
                            "target_age_years": 1,
                            "age_action": {
                                "target_age": 1,
                                "expected_age_filter": "1-1",
                                "target_x": 200,
                                "action_algorithm_used": "visible_tick_interpolation",
                            },
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            first = store.release_blocker_without_active_runner(task_id)
            second = store.ensure_cancelled_task_final_feedback(task_id)
            delivery = json.loads((task_dir / "final_failure_feedback_delivery.json").read_text(encoding="utf-8"))

            self.assertTrue(first.success)
            self.assertTrue(second.success)
            self.assertFalse(second.changed)
            self.assertIn("2025.02", first.reply_text)
            self.assertIn("1 年车龄", first.reply_text)
            self.assertNotIn("本次定价未开始", first.reply_text)
            self.assertEqual(delivery["canonical_error_code"], "S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE")
            self.assertEqual(
                delivery["feishu_result_message_idempotent_key"],
                f"final_failure:{task_id}:S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE",
            )


if __name__ == "__main__":
    unittest.main()
