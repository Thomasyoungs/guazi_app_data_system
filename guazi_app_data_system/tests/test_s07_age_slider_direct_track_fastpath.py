import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "runtime_s01_to_s10_mainline.py"
FORMATTER_PATH = ROOT / "scripts" / "feishu_result_formatter.py"


def load_runtime_module():
    spec = importlib.util.spec_from_file_location("runtime_s01_to_s10_mainline", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_formatter_module():
    spec = importlib.util.spec_from_file_location("feishu_result_formatter", FORMATTER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
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


def _tick(label: str, x: int) -> dict:
    return {
        "labels": [label],
        "bounds": (x - 20, 600, x + 20, 640),
        "clickable": False,
        "enabled": True,
        "selected": False,
    }


def make_age_snapshot(*, left_age=None, right_age=None) -> dict:
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
        "visible_blob": "age panel",
        "screenshot_path": "artifacts/screenshots/s07_age.png",
        "xml_path": "artifacts/debug/s07_age.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }
    if left_age is not None:
        snapshot["left_age"] = left_age
    if right_age is not None:
        snapshot["right_age"] = right_age
    return snapshot


class S07AgeSliderDirectTrackFastpathTest(unittest.TestCase):
    def setUp(self):
        self.module = load_runtime_module()

    def test_target_x_calculated_from_contract_visible_ticks_for_5_years(self):
        plan = self.module._s07_age_direct_track_plan(make_age_snapshot(), 5)

        self.assertTrue(plan["direct_track_fastpath_available"])
        self.assertEqual(plan["target_x"], 600)
        self.assertEqual(plan["target_y"], 680)
        self.assertEqual(plan["target_ratio"], 0.5)
        self.assertEqual(plan["target_x_calculation"], "visible_tick_interpolation")
        self.assertEqual(plan["contract_action_plan"]["action_algorithm"]["target_x_algorithm"], "visible_tick_interpolation")
        self.assertIn("legacy_age_slider_unbounded_track_ratio", plan["contract_action_plan"]["forbidden_actions"])

    def test_5_5_year_fastpath_uses_one_drag_and_one_final_verify(self):
        initial = make_age_snapshot()
        final = make_age_snapshot(left_age=5, right_age=5)
        client = FakeClient()
        context = {"client": client, "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_capture", return_value=final),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 5)

        self.assertTrue(evidence["success"])
        self.assertTrue(evidence["direct_track_fastpath_used"])
        self.assertTrue(evidence["special_5_5_fastpath"])
        self.assertEqual(evidence["fallback_strategies_used"], [])
        self.assertEqual(evidence["xml_dump_count"], 1)
        self.assertEqual(evidence["screenshot_count"], 1)
        self.assertEqual(evidence["final_xml_verify_count"], 1)
        self.assertEqual(evidence["target_x"], 600)
        self.assertEqual(evidence["drag_attempts_count"], 1)
        self.assertEqual(evidence["age_strategy_attempts"][0]["strategy"], "direct_track_fastpath_5_5")
        self.assertEqual(client.commands[0][0:3], ["shell", "input", "swipe"])
        self.assertEqual(client.commands[0][5], "600")

    def test_success_path_reuses_bounds_inside_same_round(self):
        initial = make_age_snapshot()
        final = make_age_snapshot(left_age=5, right_age=5)
        context = {"client": FakeClient(), "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_capture", return_value=final),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 5)

        self.assertTrue(evidence["direct_track_bounds_reused_in_round"])
        self.assertFalse(evidence["right_slider_recalc_bounds_each_attempt"])
        self.assertFalse(evidence["right_slider_fresh_before_each_attempt"])

    def test_direct_no_effect_does_not_use_fallback_budget_when_fallbacks_empty(self):
        initial = make_age_snapshot()
        context = {"client": FakeClient(), "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_capture", return_value=initial),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 5)

        self.assertFalse(evidence["success"])
        self.assertTrue(evidence["direct_track_fastpath_used"])
        self.assertEqual(evidence["fallback_strategies_used"], [])
        self.assertFalse(evidence["fallback_used"])
        self.assertFalse(evidence["right_slider_moved"])
        self.assertTrue(evidence["swipe_command_sent"])
        self.assertFalse(evidence["slider_value_changed"])
        self.assertFalse(evidence["slider_bounds_changed"])
        self.assertFalse(evidence["slider_moved_success"])
        self.assertEqual(evidence["failure_reason"], "S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT")
        self.assertNotEqual(evidence["failure_reason"], "S07_AGE_SLIDER_FALLBACK_BUDGET_EXCEEDED")

    def test_timing_trace_fields_are_present(self):
        initial = make_age_snapshot()
        final = make_age_snapshot(left_age=5, right_age=5)
        context = {"client": FakeClient(), "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_capture", return_value=final),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 5)

        for key in (
            "age_slider_start_time",
            "age_slider_end_time",
            "total_duration_ms",
            "target_age_years",
            "target_range",
            "track_bounds",
            "left_handle_bounds",
            "right_handle_bounds",
            "target_x",
            "drag_attempts_count",
            "xml_dump_count",
            "screenshot_count",
            "fallback_strategies_used",
            "attempts",
            "age_panel_wait_ms",
            "left_slider_bind_ms",
            "right_slider_bind_ms",
            "drag_ms",
            "verify_ms",
            "fallback_ms",
        ):
            self.assertIn(key, evidence)

    def test_direct_track_fastpath_runs_before_legacy_strategy_chain(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        section = source.split("def _set_exact_age_from_ticks", 1)[1].split("def _wait_for_s07_age_panel", 1)[0]

        self.assertLess(section.index("run_direct_track_fastpath"), section.index("# Strategy A"))
        self.assertIn("S07_AGE_SLIDER_FASTPATH_MAX_FALLBACK_STRATEGIES", section)

    def test_5_5_moves_right_then_left_to_target(self):
        initial = make_age_snapshot(left_age=0, right_age=10)
        after_right = make_age_snapshot(left_age=0, right_age=5)
        final = make_age_snapshot(left_age=5, right_age=5)
        client = FakeClient()
        context = {"client": client, "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_capture", side_effect=[after_right, final]),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 5)

        self.assertTrue(evidence["success"])
        self.assertEqual(len(client.commands), 2)
        self.assertEqual(evidence["age_strategy_attempts"][0]["side"], "right")
        self.assertEqual(evidence["age_strategy_attempts"][1]["side"], "left")
        self.assertEqual(evidence["left_age_after"], 5)
        self.assertEqual(evidence["right_age_after"], 5)

    def test_drag_start_uses_handle_center_when_track_y_is_outside_bounds(self):
        binding = self.module._s07_build_age_drag_binding(
            side="right",
            selected_handle_bounds=[957, 948, 1034, 1028],
            target_x=568,
            preferred_y=1064,
            original_start_point=(995, 1064),
        )

        self.assertEqual(binding["original_drag_start_point"], [995, 1064])
        self.assertEqual(binding["drag_start_point"], [995, 988])
        self.assertTrue(binding["drag_start_inside_selected_handle_bounds"])
        self.assertEqual(binding["handle_binding_reason"], "real_handle_center_y_used")
        self.assertEqual(binding["drag_y_source"], "real_handle_center_y")

    def test_handle_binding_failure_stops_before_swipe(self):
        initial = make_age_snapshot()
        client = FakeClient()
        context = {"client": client, "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_s07_age_direct_track_plan") as plan,
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            plan.return_value = {
                "direct_track_fastpath_available": True,
                "right_handle_bounds": None,
                "left_handle_bounds": [60, 620, 140, 760],
                "track_bounds": [100, 620, 1100, 760],
                "right_slider_target": 5,
                "left_slider_target": 5,
                "expected_age_filter": "5-5",
                "target_x": 600,
                "target_y": 680,
                "target_ratio": 0.5,
                "target_x_algorithm": "visible_tick_interpolation",
                "target_x_calculation": "visible_tick_interpolation",
                "contract_action_plan": {"expected": {}, "action_algorithm": {}, "forbidden_actions": [], "allowed_fallbacks": []},
            }
            evidence = self.module._set_exact_age_from_ticks(context, initial, 5)

        self.assertFalse(evidence["success"])
        self.assertEqual(evidence["failure_reason"], "S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED")
        self.assertEqual(client.commands, [])

    def test_exact_range_verify_failed_when_values_change_but_not_to_target(self):
        initial = make_age_snapshot(left_age=0, right_age=10)
        after_right = make_age_snapshot(left_age=0, right_age=5)
        after_left = make_age_snapshot(left_age=4, right_age=5)
        context = {"client": FakeClient(), "issues": DummyIssues()}

        with (
            mock.patch.object(self.module, "_capture", side_effect=[after_right, after_left]),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 5)

        self.assertFalse(evidence["success"])
        self.assertTrue(evidence["slider_value_changed"])
        self.assertEqual(evidence["failure_reason"], "S07_AGE_EXACT_RANGE_VERIFY_FAILED")

    def test_business_failure_text_has_no_internal_forbidden_words(self):
        formatter = load_formatter_module()
        result = formatter.format_result_reply(
            task_id="FS_TEST",
            pricing_result=None,
            status="FAILED",
            errors=["S07_AGE_SLIDER_FASTPATH_FAILED"],
        )

        self.assertIn("S07_AGE_SLIDER_FASTPATH_FAILED", result.warnings)
        for forbidden in (
            "S07",
            "XML",
            "dump",
            "bounds",
            "adb",
            "uiautomator",
            "runner",
            "dispatcher",
            "traceback",
            "status.json",
        ):
            self.assertNotIn(forbidden, result.text)


if __name__ == "__main__":
    unittest.main()
