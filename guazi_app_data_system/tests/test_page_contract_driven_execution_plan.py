from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guazi_app_data_system.page_contract_execution_plan import (  # noqa: E402
    build_generic_contract_action_plan,
    build_s11_report_entry_action_plan,
    validate_action_against_plan,
    validate_contract_action_plan,
)
from guazi_app_data_system.runtime_contract_guard import (  # noqa: E402
    RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED,
    guard_s11_report_entry,
    guard_s07_color,
    validate_contract_records,
)


class PageContractDrivenExecutionPlanTest(unittest.TestCase):
    def test_contract_action_plan_has_required_shape(self):
        plan = build_generic_contract_action_plan(
            step_id="S03",
            expected={"brand": "别克"},
            action_name="tap_target_brand",
        )

        self.assertEqual(validate_contract_action_plan(plan), [])
        for key in (
            "step_id",
            "contract_source_file",
            "contract_source_version",
            "expected",
            "allowed_actions",
            "forbidden_actions",
            "action_algorithm",
            "action_inputs",
            "action_outputs",
            "success_condition",
            "failure_stop_code",
        ):
            self.assertIn(key, plan)

    def test_missing_contract_action_plan_is_rejected(self):
        record = guard_s07_color(expected_color="黑色", selected_color="黑色")
        record.pop("contract_action_plan", None)

        errors = validate_contract_records([record])

        self.assertTrue(any("missing_fields:contract_action_plan" in item for item in errors))

    def test_forbidden_action_is_rejected(self):
        record = guard_s07_color(expected_color="黑色", selected_color="黑色")
        record["contract_action"]["used_action_algorithm"] = "fixed_coordinate"

        errors = validate_contract_records([record])

        self.assertTrue(any(RUNTIME_CONTRACT_FORBIDDEN_ACTION_USED in item for item in errors))

    def test_s11_report_entry_plan_forbids_fixed_and_ratio_coordinate(self):
        plan = build_s11_report_entry_action_plan()

        self.assertEqual(plan["action_algorithm"]["name"], "xml_exact_text_bounds")
        self.assertIn("xml_exact_text_bounds", plan["action_algorithm"]["allowed_binding_sources"])
        self.assertIn("xml_clickable_parent_bounds", plan["action_algorithm"]["allowed_binding_sources"])
        self.assertEqual(
            set(plan["action_algorithm"]["allowed_binding_sources"]),
            {"xml_exact_text_bounds", "xml_clickable_parent_bounds", "xml_safe_container_bounds", "xml_after_stale_recovery"},
        )
        self.assertIn("legacy_s11_screenshot_rect_click_target", plan["forbidden_actions"])
        self.assertIn("fixed_coordinate", plan["forbidden_actions"])
        self.assertIn("ratio_coordinate", plan["forbidden_actions"])

        errors = validate_action_against_plan({"action": "click_view_full_report", "used_action_algorithm": "ratio_coordinate"}, plan)
        self.assertTrue(any("forbidden_action_used:ratio_coordinate" == item for item in errors))

    def test_s11_guard_attaches_action_plan(self):
        record = guard_s11_report_entry(
            click_source="xml_exact_text_bounds",
            xml_bounds=(66, 2050, 530, 2160),
            entered_report=True,
        )

        self.assertTrue(record["contract_match"])
        self.assertEqual(record["contract_action_plan"]["step_id"], "S11_REPORT_ENTRY")

    def test_s11_guard_rejects_screenshot_click_source(self):
        record = guard_s11_report_entry(
            click_source="legacy_s11_screenshot_rect_click_target",
            dynamic_button_rect=(66, 2050, 530, 2160),
            entered_report=True,
        )

        self.assertFalse(record["contract_match"])
        self.assertFalse(record["continue_allowed"])
        self.assertEqual(record["contract_action_plan"]["step_id"], "S11_REPORT_ENTRY")

    def test_runtime_contract_execution_check_rejects_bad_fixture_internally(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "runtime_contract_execution_check.py")],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("RUNTIME_CONTRACT_EXECUTION_CHECK_PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
