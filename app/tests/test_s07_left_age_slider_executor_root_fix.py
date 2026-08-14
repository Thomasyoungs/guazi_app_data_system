import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "runtime_s01_to_s10_mainline.py"


def load_runtime_module():
    spec = importlib.util.spec_from_file_location("runtime_s01_to_s10_mainline_s07_left_fix", SCRIPT_PATH)
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


def _tick(label: str, x: int, y: int = 1047) -> dict:
    return {
        "labels": [label],
        "text": label,
        "bounds": (x - 26, y, x + 26, y + 36),
        "clickable": False,
        "enabled": True,
        "selected": False,
        "class_name": "android.widget.TextView",
    }


def make_age_one_snapshot(
    *,
    visible_blob: str = "age panel",
    left_bounds=(258, 1047, 363, 1179),
    right_bounds=(937, 1047, 1042, 1179),
    left_age=None,
    right_age=None,
) -> dict:
    nodes = [
        _tick("0", 307),
        _tick("2", 411),
        _tick("4", 515),
        _tick("6", 620),
        _tick("8", 724),
        _tick("10", 828),
        _tick("不限", 985),
        {"labels": [], "bounds": (307, 1105, 985, 1122), "clickable": True, "enabled": True, "class_name": "android.view.View"},
        {"labels": [], "bounds": left_bounds, "clickable": True, "enabled": True, "class_name": "android.view.View"},
        {"labels": [], "bounds": right_bounds, "clickable": True, "enabled": True, "class_name": "android.view.View"},
    ]
    if visible_blob:
        nodes.append(
            {
                "labels": [visible_blob],
                "text": visible_blob,
                "bounds": (840, 890, 1030, 950),
                "clickable": False,
                "enabled": True,
                "class_name": "android.widget.TextView",
            }
        )
    snapshot = {
        "nodes": nodes,
        "visible_blob": visible_blob,
        "screenshot_path": "artifacts/screenshots/s07_age_one.png",
        "xml_path": "artifacts/debug/s07_age_one.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }
    if left_age is not None:
        snapshot["left_age"] = left_age
    if right_age is not None:
        snapshot["right_age"] = right_age
    return snapshot


class S07LeftAgeSliderExecutorRootFixTest(unittest.TestCase):
    def setUp(self):
        self.module = load_runtime_module()

    def test_after_right_overlap_handle_pair_binds_as_distinct_left_and_right(self):
        snapshot = make_age_one_snapshot(
            visible_blob="1年以下 查看8辆",
            left_bounds=(258, 1047, 363, 1179),
            right_bounds=(310, 1047, 415, 1179),
        )

        binding = self.module.bind_s07_real_slider_handle(snapshot)

        self.assertIsNone(binding["stop_code"])
        self.assertEqual(binding["selected_left_handle_bounds"], [258, 1047, 363, 1179])
        self.assertEqual(binding["selected_right_handle_bounds"], [310, 1047, 415, 1179])
        self.assertTrue(binding["s07_handle_pair_overlap_allowed"])
        self.assertTrue(binding["s07_handle_pair_close_allowed"])
        self.assertEqual(binding["s07_handle_pair_separation_px"], 52)
        self.assertNotEqual(binding.get("rejected_reason"), "real_handle_pair_not_found")

    def test_target_age_one_continues_left_drag_after_right_broad_range(self):
        initial = make_age_one_snapshot(left_age=0, right_age=10)
        after_right = make_age_one_snapshot(
            visible_blob="1年以下 查看8辆",
            left_bounds=(258, 1047, 363, 1179),
            right_bounds=(310, 1047, 415, 1179),
            left_age=0,
            right_age=1,
        )
        final = make_age_one_snapshot(
            visible_blob="1-1年 查看7辆",
            left_bounds=(310, 1047, 415, 1179),
            right_bounds=(310, 1047, 415, 1179),
            left_age=1,
            right_age=1,
        )
        context = {"client": FakeClient(), "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_capture", side_effect=[after_right, final]),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 1)

        self.assertTrue(evidence["success"])
        self.assertEqual([item["side"] for item in evidence["age_strategy_attempts"]], ["right", "left"])
        self.assertTrue(evidence["s07_left_drag_attempted"])
        self.assertEqual(evidence["s07_left_drag_start_point"], [310, 1113])
        self.assertEqual(evidence["s07_left_drag_target_point"], [359, 1113])
        self.assertEqual(evidence["s07_left_drag_distance_px"], 49)
        self.assertTrue(evidence["s07_left_drag_short_distance_nudge_used"])
        self.assertEqual(evidence["s07_after_right_left_handle_bounds"], [258, 1047, 363, 1179])
        self.assertEqual(evidence["s07_after_right_right_handle_bounds"], [310, 1047, 415, 1179])
        self.assertEqual(evidence["s07_final_exact_age_verify_text"], "1-1年")
        self.assertTrue(evidence["s07_final_exact_age_proof_passed"])

    def test_target_age_one_broad_range_after_left_retries_does_not_pass(self):
        initial = make_age_one_snapshot(left_age=0, right_age=10)
        after_right = make_age_one_snapshot(
            visible_blob="1年以下 查看8辆",
            left_bounds=(258, 1047, 363, 1179),
            right_bounds=(310, 1047, 415, 1179),
            left_age=0,
            right_age=1,
        )
        context = {"client": FakeClient(), "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_capture", side_effect=[after_right, after_right, after_right, after_right]),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 1)

        self.assertFalse(evidence["success"])
        self.assertFalse(evidence["AGE_FILTER_DONE"])
        self.assertTrue(evidence["s07_left_drag_attempted"])
        self.assertGreaterEqual(evidence["s07_left_drag_retry_count"], 2)
        self.assertIn(
            evidence["failure_reason"],
            {
                "S07_LEFT_AGE_SLIDER_DRAG_NO_VISUAL_MOVEMENT",
                "S07_LEFT_AGE_SLIDER_EXACT_VERIFY_FAILED_AFTER_RETRY",
                "S07_AGE_ONE_POST_ACTION_VERIFY_FAILED",
            },
        )

    def test_close_handle_pair_below_minimum_separation_fails_without_blind_drag(self):
        snapshot = make_age_one_snapshot(
            visible_blob="1年以下 查看8辆",
            left_bounds=(300, 1047, 405, 1179),
            right_bounds=(310, 1047, 415, 1179),
        )

        binding = self.module.bind_s07_real_slider_handle(snapshot)

        self.assertEqual(binding["stop_code"], "S07_AGE_SLIDER_CLOSE_HANDLE_PAIR_BINDING_FAILED")
        self.assertEqual(binding["rejected_reason"], "real_handle_pair_too_close")
        self.assertEqual(binding["s07_handle_pair_separation_px"], 10)

    def test_target_age_zero_success_texts_are_unchanged(self):
        snapshot = make_age_one_snapshot(visible_blob="0年以下 查看12辆", left_age=0, right_age=0)

        verify = self.module._verify_exact_age_selected(snapshot, 0)

        self.assertTrue(verify["exact_age_verified"])
        self.assertEqual(verify["matched_age_text"], "0年以下")


if __name__ == "__main__":
    unittest.main()
