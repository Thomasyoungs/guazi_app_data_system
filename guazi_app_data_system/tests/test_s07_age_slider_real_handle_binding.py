import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "runtime_s01_to_s10_mainline.py"


def load_runtime_module():
    spec = importlib.util.spec_from_file_location("runtime_s01_to_s10_mainline_real_handle", SCRIPT_PATH)
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


def _tick(label: str, x: int, y: int = 918) -> dict:
    return {
        "labels": [label],
        "bounds": (x - 20, y, x + 20, y + 42),
        "clickable": False,
        "enabled": True,
        "selected": False,
        "class_name": "android.widget.TextView",
    }


def make_0002_like_age_snapshot(*, left_age=None, right_age=None) -> dict:
    nodes = [
        _tick("0", 90),
        _tick("2", 242),
        _tick("4", 448),
        _tick("6", 688),
        _tick("8", 897),
        _tick("10", 1075),
        {"labels": [], "bounds": (307, 948, 985, 1179), "clickable": True, "enabled": True, "class_name": "android.view.View"},
        {"labels": [], "bounds": (258, 1047, 363, 1179), "clickable": True, "enabled": True, "class_name": "android.view.View"},
        {"labels": [], "bounds": (937, 1047, 1042, 1179), "clickable": True, "enabled": True, "class_name": "android.view.View"},
        {"labels": [], "bounds": (937, 1058, 1042, 1179), "clickable": True, "enabled": True, "class_name": "android.widget.TextView"},
        {
            "labels": ["qnbdp3c27bd43e"],
            "text": "qnbdp3c27bd43e",
            "bounds": (957, 948, 1034, 1028),
            "clickable": True,
            "enabled": True,
            "class_name": "android.widget.Image",
        },
    ]
    snapshot = {
        "nodes": nodes,
        "visible_blob": "age panel",
        "screenshot_path": "artifacts/screenshots/fs0002_s07_age.png",
        "xml_path": "artifacts/debug/fs0002_s07_age.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }
    if left_age is not None:
        snapshot["left_age"] = left_age
    if right_age is not None:
        snapshot["right_age"] = right_age
    return snapshot


class S07AgeSliderRealHandleBindingTest(unittest.TestCase):
    def setUp(self):
        self.module = load_runtime_module()

    def test_s07_real_handle_binding_rejects_upper_image_ghost(self):
        snapshot = make_0002_like_age_snapshot()

        binding = self.module.bind_s07_real_slider_handle(snapshot)

        self.assertIsNone(binding["stop_code"])
        self.assertEqual(binding["selected_handle_source"], "real_green_slider_handle")
        self.assertEqual(binding["selected_right_handle_bounds"], [937, 1047, 1042, 1179])
        self.assertNotEqual(binding["selected_right_handle_bounds"], [957, 948, 1034, 1028])
        rejected_bounds = [item["bounds"] for item in binding["rejected_handle_candidates"]]
        self.assertIn([957, 948, 1034, 1028], rejected_bounds)
        ghost_reject = [item for item in binding["rejected_handle_candidates"] if item["bounds"] == [957, 948, 1034, 1028]][0]
        self.assertEqual(ghost_reject["rejected_reason"], "S07_AGE_SLIDER_GHOST_HANDLE_REJECTED")
        self.assertFalse(binding["handle_is_ghost"])

    def test_s07_drag_y_uses_real_handle_center_not_track_center(self):
        binding = self.module._s07_build_age_drag_binding(
            side="right",
            selected_handle_bounds=[937, 1047, 1042, 1179],
            target_x=568,
            preferred_y=1064,
            original_start_point=(989, 1064),
            track_bounds=[307, 948, 985, 1179],
        )

        self.assertEqual(binding["real_handle_center_y"], 1113)
        self.assertEqual(binding["track_bounds_center_y"], 1064)
        self.assertEqual(binding["drag_y_source"], "real_handle_center_y")
        self.assertEqual(binding["drag_start_point"], [989, 1113])
        self.assertTrue(binding["drag_start_inside_real_handle_bounds"])

    def test_s07_5_5_real_touch_executor_uses_right_real_handle(self):
        initial = make_0002_like_age_snapshot()
        final = make_0002_like_age_snapshot(left_age=5, right_age=5)
        client = FakeClient()
        context = {"client": client, "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_capture", return_value=final),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 5)

        self.assertTrue(evidence["success"])
        self.assertEqual(evidence["right_swipe_start"], [989, 1113])
        self.assertNotEqual(evidence["right_swipe_start"], [995, 988])
        self.assertEqual(evidence["selected_right_handle_bounds"], [937, 1047, 1042, 1179])
        self.assertEqual(evidence["touch_executor"], "real_handle_down_move_up")
        self.assertEqual(evidence["selected_handle_source"], "real_green_slider_handle")
        self.assertTrue(evidence["drag_start_inside_real_handle_bounds"])
        self.assertEqual(client.commands[0][0:3], ["shell", "input", "swipe"])
        self.assertEqual(client.commands[0][3:5], ["989", "1113"])

    def test_s07_5_5_right_then_left_contract_sequence(self):
        initial = make_0002_like_age_snapshot(left_age=0, right_age=10)
        after_right = make_0002_like_age_snapshot(left_age=0, right_age=5)
        final = make_0002_like_age_snapshot(left_age=5, right_age=5)
        client = FakeClient()
        context = {"client": client, "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_capture", side_effect=[after_right, final]),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 5)

        self.assertTrue(evidence["success"])
        self.assertEqual([item["side"] for item in evidence["age_strategy_attempts"]], ["right", "left"])
        self.assertEqual(evidence["age_strategy_attempts"][0]["touch_executor"], "real_handle_down_move_up")
        self.assertEqual(evidence["age_strategy_attempts"][1]["touch_executor"], "real_handle_down_move_up")

    def test_s07_real_touch_no_effect_reports_real_touch_no_effect(self):
        initial = make_0002_like_age_snapshot()
        context = {"client": FakeClient(), "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_capture", return_value=initial),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 5)

        self.assertFalse(evidence["success"])
        self.assertEqual(evidence["failure_reason"], "S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT")
        self.assertNotEqual(evidence["failure_reason"], "S07_AGE_SLIDER_FALLBACK_BUDGET_EXCEEDED")
        self.assertEqual(evidence["fallback_strategies_used"], [])
        self.assertFalse(evidence["fallback_used"])

    def test_s07_selected_handle_source_real_green_slider_handle(self):
        evidence = self.module._s07_age_slider_evidence(make_0002_like_age_snapshot(), 5)

        self.assertEqual(evidence["selected_handle_source"], "real_green_slider_handle")
        self.assertEqual(evidence["handle_binding_method"], "s07_real_green_slider_handle")
        self.assertFalse(evidence["handle_is_ghost"])
        self.assertEqual(evidence["right_handle_source"], "real_green_slider_handle")


if __name__ == "__main__":
    unittest.main()
