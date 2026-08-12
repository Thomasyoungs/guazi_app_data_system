import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "runtime_s01_to_s10_mainline.py"
sys.path.insert(0, str(ROOT / "src"))

from guazi_app_data_system.runtime_contract_guard import (  # noqa: E402
    RUNTIME_CONTRACT_ACTION_PLAN_NOT_USED,
    RUNTIME_CONTRACT_CONTINUED_AFTER_MISMATCH,
    RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED,
    S10_FILTER_SUMMARY_CONTRACT_MISMATCH,
    S11_REPORT_ENTRY_CONTRACT_MISMATCH,
    guard_filter_summary,
    guard_s07_age,
    guard_s07_color,
    guard_s11_report_entry,
    validate_contract_records,
)


def load_runtime_module():
    spec = importlib.util.spec_from_file_location("runtime_s01_to_s10_mainline_real_path_binding", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _tick(label: str, x: int) -> dict:
    return {
        "labels": [label],
        "bounds": (x - 20, 600, x + 20, 640),
        "clickable": False,
        "enabled": True,
        "selected": False,
    }


def make_age_snapshot() -> dict:
    return {
        "nodes": [
            _tick("0", 100),
            _tick("2", 300),
            _tick("4", 500),
            _tick("6", 700),
            _tick("8", 900),
            _tick("10", 1100),
            {"labels": [], "bounds": (100, 660, 1100, 700), "clickable": True, "enabled": True},
            {"labels": [], "bounds": (60, 620, 140, 760), "clickable": True, "enabled": True},
            {"labels": [], "bounds": (1060, 620, 1140, 760), "clickable": True, "enabled": True},
        ],
        "visible_blob": "age panel",
        "screenshot_path": "mock_s07_age.png",
        "xml_path": "mock_s07_age.xml",
    }


class PageContractRealPathBindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = load_runtime_module()

    def test_s07_age_runtime_uses_action_plan_targets(self):
        plan = self.runtime._s07_age_direct_track_plan(make_age_snapshot(), 5)

        self.assertTrue(plan["contract_action_plan_used"])
        self.assertTrue(plan["action_plan_binding_check_passed"])
        self.assertEqual(plan["action_inputs_source"], "contract_action_plan")
        self.assertEqual(plan["action_outputs_source"], "contract_action_plan")
        self.assertEqual(plan["left_slider_target"], 5)
        self.assertEqual(plan["right_slider_target"], 5)
        self.assertEqual(plan["expected_age_filter"], "5-5")
        self.assertEqual(plan["target_x"], 600)

    def test_s07_age_runtime_rejects_neutral_legacy_forbidden_action(self):
        record = guard_s07_age(target_age_years=5, actual_age_filter="5-5", actual_left_age=5, actual_right_age=5)
        record["action_algorithm_used"] = "legacy_age_slider_unbounded_track_ratio"
        record["action_plan_binding_check_passed"] = False

        errors = validate_contract_records([record])

        self.assertTrue(any(RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED in item for item in errors))

    def test_s07_age_runtime_uses_visible_tick_interpolation(self):
        plan = self.runtime._s07_age_direct_track_plan(make_age_snapshot(), 5)

        self.assertEqual(plan["target_x_algorithm"], "visible_tick_interpolation")
        self.assertEqual(plan["action_algorithm_used"], "visible_tick_interpolation")
        self.assertEqual(plan["target_x_calculation"], "visible_tick_interpolation")

    def test_s07_age_runtime_bypass_action_plan_fails_check(self):
        record = guard_s07_age(target_age_years=5, actual_age_filter="5-5", actual_left_age=5, actual_right_age=5)
        record["contract_action_plan_used"] = False
        record["action_plan_binding_check_passed"] = False

        errors = validate_contract_records([record])

        self.assertTrue(any(RUNTIME_CONTRACT_ACTION_PLAN_NOT_USED in item for item in errors))

    def test_s07_color_runtime_uses_action_plan_allowed_click_source(self):
        trace = self.runtime._s07_build_color_click_action_trace(
            target_color="黑色",
            color_node={
                "text": "黑色",
                "labels": ["黑色"],
                "bounds": [360, 1010, 420, 1070],
                "click_source": "color_grid_text_node_bounds",
                "color_click_strategy": "color_grid_text_node_bounds",
                "clickable": False,
                "enabled": True,
            },
            click_point=(390, 1040),
            attempt_index=1,
            snapshot={"nodes": []},
        )

        self.assertTrue(trace["contract_action_plan_used"])
        self.assertTrue(trace["action_plan_binding_check_passed"])
        self.assertEqual(trace["action_algorithm_used"], "exact_target_color_binding")
        self.assertTrue(trace["action_plan_click_source_allowed"])
        self.assertFalse(trace["forbidden_action_used"])

    def test_s07_color_runtime_selected_blue_fails_contract(self):
        record = guard_s07_color(expected_color="黑色", selected_color="蓝色")

        self.assertFalse(record["contract_match"])
        self.assertTrue(validate_contract_records([record]) == [])

    def test_s10_runtime_expected_summary_gate_blocks_mismatch(self):
        record = guard_filter_summary(
            stage="S10",
            expected_filters={"brand": "别克", "series": "君越", "color": "黑色", "age_filter": "5"},
            summary={"chips": ["别克", "君越", "蓝色", "5年"]},
        )
        record["continue_allowed"] = True

        errors = validate_contract_records([record])

        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], S10_FILTER_SUMMARY_CONTRACT_MISMATCH)
        self.assertTrue(any(RUNTIME_CONTRACT_CONTINUED_AFTER_MISMATCH in item for item in errors))

    def test_s11_runtime_forbidden_binding_source_fails(self):
        record = guard_s11_report_entry(
            click_source="fixed_coordinate",
            xml_bounds=(80, 1800, 520, 1900),
            entered_report=True,
        )

        self.assertFalse(record["contract_match"])
        self.assertEqual(record["contract_stop_code"], S11_REPORT_ENTRY_CONTRACT_MISMATCH)

    def test_runtime_contract_execution_check_detects_plan_not_used(self):
        record = guard_s07_color(expected_color="黑色", selected_color="黑色")
        record["contract_action_plan_used"] = False
        record["action_plan_binding_check_passed"] = False

        errors = validate_contract_records([record])

        self.assertTrue(any(RUNTIME_CONTRACT_ACTION_PLAN_NOT_USED in item for item in errors))

    def test_runtime_contract_execution_check_detects_forbidden_action(self):
        record = guard_s07_color(expected_color="黑色", selected_color="黑色")
        record["forbidden_action_used"] = True
        record["action_plan_binding_check_passed"] = False

        errors = validate_contract_records([record])

        self.assertTrue(any(RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED in item for item in errors))

    def test_old_success_baselines_not_regressed(self):
        records = [
            guard_s07_age(target_age_years=5, actual_age_filter="5-5", actual_left_age=5, actual_right_age=5),
            guard_s07_color(expected_color="黑色", selected_color="黑色", s08_color="黑色", s10_color="黑色"),
            guard_s11_report_entry(click_source="xml_exact_text_bounds", xml_bounds=(80, 1800, 520, 1900), entered_report=True),
        ]

        self.assertEqual(validate_contract_records(records), [])


if __name__ == "__main__":
    unittest.main()
