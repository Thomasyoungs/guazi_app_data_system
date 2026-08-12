import tempfile
import unittest
from pathlib import Path

from guazi_app_data_system.action_executor import ActionExecutor
from guazi_app_data_system.audit import AuditLogger
from guazi_app_data_system.config_loader import load_config
from guazi_app_data_system.exception_handler import GuaziFlowError, IssueRecorder
from guazi_app_data_system.issue_classifier import IssueClassifier
from guazi_app_data_system.page_state_machine import PageStateMachine


class ActionExecutionTest(unittest.TestCase):
    def test_allowed_action_logs_and_forbidden_action_records_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"))
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            result = executor.execute("S01", "click_bottom_select_car_tab")
            self.assertTrue(result["ok"])
            self.assertTrue((tmp_path / "audit.jsonl").exists())

            with self.assertRaises(GuaziFlowError):
                executor.execute("S01", "tap_search")
            self.assertEqual(issues.read_all()[0]["code"], "CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH")

    def test_home_tab_flow_only_allows_bottom_select_car(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"))
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            result = executor.execute("S01", "click_bottom_select_car_tab")
            self.assertTrue(result["ok"])

            with self.assertRaises(GuaziFlowError):
                executor.execute("S01", "tap_brand_filter")
            self.assertEqual(issues.read_all()[0]["code"], "CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH")

    def test_select_car_tab_only_allows_brand_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"))
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            result = executor.execute("S02_SELECT_CAR_TAB", "click_brand_entry")
            self.assertTrue(result["ok"])

            with self.assertRaises(GuaziFlowError):
                executor.execute("S02_SELECT_CAR_TAB", "tap_target_brand")
            self.assertEqual(issues.read_all()[0]["code"], "CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH")

    def test_series_page_only_allows_target_series_model_button_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"))
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            result = executor.execute(
                "S04",
                "click_series_model_button",
                {
                    "target_series": "series-A",
                    "series_model_button_found": True,
                    "same_row_or_card": True,
                    "actual_click_target": "\u8f66\u578b",
                    "actual_click_target_role": "series_model_button",
                    "actual_click_target_series": "series-A",
                },
            )
            self.assertTrue(result["ok"])

            with self.assertRaises(GuaziFlowError):
                executor.execute("S04", "tap_non_target_series")
            self.assertEqual(issues.read_all()[0]["code"], "CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH")

    def test_series_model_button_action_blocks_series_card_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"), issue_classifier=classifier)
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)
            xml = """
            <hierarchy>
              <node content-desc="series-A#10;local-stock" bounds="[52,858][1168,1163]">
                <node text="\u8f66\u578b" bounds="[869,908][1129,1087]" clickable="true" />
              </node>
            </hierarchy>
            """

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute(
                    "S04",
                    "click_series_model_button",
                    {
                        "target_series": "series-A",
                        "series_model_button_found": True,
                        "before_xml": xml,
                        "after_xml": xml,
                        "actual_click_target": "series-A",
                        "actual_click_target_role": "series_card",
                        "actual_click_target_series": "series-A",
                        "actual_next_state": "S04",
                    },
                )

            self.assertEqual(raised.exception.code, "SERIES_ACTION_TARGET_MISMATCH")
            self.assertEqual(issues.read_all()[0]["code"], "SERIES_ACTION_TARGET_MISMATCH")

    def test_series_model_button_action_requires_same_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"))
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute(
                    "S04",
                    "click_series_model_button",
                    {
                        "target_series": "series-A",
                        "series_model_button_found": True,
                        "same_row_or_card": False,
                        "actual_click_target": "model-button",
                        "actual_click_target_role": "series_model_button",
                        "actual_click_target_series": "other-series",
                    },
                )

            self.assertEqual(raised.exception.code, "SERIES_ACTION_TARGET_MISMATCH")

    def test_missing_series_model_button_reports_specific_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"))
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute(
                    "S04",
                    "click_series_model_button",
                    {
                        "target_series": "series-A",
                        "series_row_found": True,
                        "series_model_button_found": False,
                        "actual_click_target": "series-A",
                        "actual_click_target_role": "series_name",
                        "actual_click_target_series": "series-A",
                    },
                )

            self.assertEqual(raised.exception.code, "SERIES_MODEL_BUTTON_NOT_FOUND")

    def test_model_button_click_without_s05_reports_no_navigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"), issue_classifier=classifier)
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)
            xml = """
            <hierarchy>
              <node content-desc="series-A#10;local-stock" bounds="[52,858][1168,1163]">
                <node text="\u8f66\u578b" bounds="[869,908][1129,1087]" clickable="true" />
              </node>
            </hierarchy>
            """

            result = executor.execute(
                "S04",
                "click_series_model_button",
                {
                    "target_series": "series-A",
                    "target_brand": "brand-A",
                    "before_xml": xml,
                    "after_xml": xml,
                    "series_model_button_found": True,
                    "same_row_or_card": True,
                    "actual_click_target": "\u8f66\u578b",
                    "actual_click_target_role": "series_model_button",
                    "actual_click_target_series": "series-A",
                    "actual_click_bounds": [869, 908, 1129, 1087],
                    "expected_next_state": "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
                    "actual_next_state": "S04_SERIES_LIST_PAGE_VERIFIED",
                    "allow_auto_recovery": False,
                },
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["issue"]["code"], "MODEL_BUTTON_CLICK_NO_NAVIGATION")

    def test_recovery_executor_only_runs_approved_allowed_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"))
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)
            issue = {
                "state_id": "S00",
                "knowledge_lookup": {
                    "status": "approved_solution_matched",
                    "solution_id": "SOL-TEST",
                    "allowed_auto_actions": ["wait_for_home"],
                    "attempts": 0,
                    "max_auto_retries": 1,
                },
            }

            result = executor.execute_approved_recovery(issue)

            self.assertTrue(result["ok"])
            self.assertEqual(result["results"][0]["action_id"], "wait_for_home")

            blocked = {
                "state_id": "S00",
                "knowledge_lookup": {
                    "status": "approved_solution_matched",
                    "solution_id": "SOL-TEST",
                    "allowed_auto_actions": ["tap_price_low_to_high"],
                    "attempts": 0,
                    "max_auto_retries": 1,
                },
            }
            with self.assertRaises(GuaziFlowError):
                executor.execute_approved_recovery(blocked)

    def test_runtime_s04_script_only_targets_right_side_model_button(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s04_to_s05.py").read_text(encoding="utf-8")

        self.assertIn('series_node = current_snapshot["target_series_model_button"]', script)
        self.assertNotIn('target_series_model_button"] or current_snapshot["target_series_node"', script)
        self.assertNotIn('action_id="tap_target_series_model_button"', script)
        self.assertNotIn("WRONG_PAGE_AFTER_SERIES_CLICK", script)
        self.assertNotIn("SERIES_CLICK_NO_NAVIGATION", script)
        self.assertNotIn('result["clicked_target_series_once"] = bool(success)', script)
        self.assertIn("click_series_model_button", script)
        self.assertIn('result["clicked_series_model_button_once"] = bool(success)', script)

    def test_runtime_s05_script_only_targets_exact_model_year(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s05_select_model_year.py").read_text(encoding="utf-8")

        self.assertIn("find_model_year_node", script)
        self.assertIn('actual_click_target=result["target_model_year"]', script)
        self.assertIn('result["clicked_target_model_year_once"] = bool(success)', script)
        self.assertIn("MODEL_YEAR_NOT_FOUND", script)
        self.assertIn("MODEL_YEAR_CLICK_NO_SELECTION", script)
        self.assertIn("WRONG_PAGE_AFTER_MODEL_YEAR_CLICK", script)
        self.assertIn("clicked_target_trim=False", script)
        self.assertIn("clicked_confirm=False", script)
        self.assertIn("collected_vehicle_data=False", script)
        self.assertNotIn("actual_click_target=result[\"target_trim\"]", script)
        self.assertNotIn("action_id=\"tap_green_confirm\"", script)

    def test_runtime_s05_trim_script_only_targets_exact_trim(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s05_select_trim.py").read_text(encoding="utf-8")

        self.assertIn("find_exact_trim_node", script)
        self.assertIn("exact_trim_match_with_emission_normalization", script)
        self.assertIn("normalize_trim_for_match", script)
        self.assertIn('actual_click_target=result["target_trim"]', script)
        self.assertIn('result["clicked_target_trim_once"] = bool(success)', script)
        self.assertIn("TRIM_NOT_FOUND", script)
        self.assertIn("TRIM_EXACT_MATCH_REQUIRED", script)
        self.assertIn("TRIM_CLICK_NO_SELECTION", script)
        self.assertIn("WRONG_PAGE_AFTER_TRIM_CLICK", script)
        self.assertIn("scroll_configuration_list", script)
        self.assertIn("850\", \"2140\", \"850\", \"690", script)
        self.assertIn("task_trim_raw=result[\"task_trim_raw\"]", script)
        self.assertIn("app_trim_raw=result[\"app_trim_raw\"]", script)
        self.assertIn("task_trim_normalized=result[\"task_trim_normalized\"]", script)
        self.assertIn("app_trim_normalized=result[\"app_trim_normalized\"]", script)
        self.assertIn("emission_normalization_used=result[\"emission_normalization_used\"]", script)
        self.assertIn("clicked_confirm=False", script)
        self.assertIn("collected_vehicle_data=False", script)
        self.assertNotIn("actual_click_target=result[\"target_model_year\"]", script)
        self.assertNotIn("action_id=\"tap_green_confirm\"", script)

    def test_runtime_s05_confirm_script_only_clicks_confirm_and_reads_next_page(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s05_confirm.py").read_text(encoding="utf-8")

        self.assertIn("find_confirm_node", script)
        self.assertIn('actual_click_target="\u786e\u5b9a"', script)
        self.assertIn('result["clicked_confirm_once"] = bool(success)', script)
        self.assertIn("S06_FILTER_OR_RESULT_ENTRY_PAGE", script)
        self.assertIn("S07_VEHICLE_LIST_PAGE", script)
        self.assertIn("CONFIRM_BUTTON_NOT_FOUND", script)
        self.assertIn("CONFIRM_CLICK_NO_NAVIGATION", script)
        self.assertIn("WRONG_PAGE_AFTER_CONFIRM_CLICK", script)
        self.assertIn("clicked_filter_color_year=False", script)
        self.assertIn("clicked_sort_or_comprehensive_sort=False", script)
        self.assertIn("clicked_price_low_to_high=False", script)
        self.assertIn("entered_vehicle_detail=False", script)
        self.assertIn("collected_vehicle_data=False", script)
        self.assertNotIn("tap_price_low_to_high", script)
        self.assertNotIn("tap_sort_if_present", script)
        self.assertNotIn("collect_list_whitelist_fields", script)

    def test_runtime_s07_detect_script_is_read_only_model_config_detection(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s07_detect_model_config.py").read_text(encoding="utf-8")

        self.assertIn("find_vehicle_model_config_entry", script)
        self.assertIn('first_line(str(label)) == "\u8f66\u578b\u914d\u7f6e"', script)
        self.assertIn("S07_VEHICLE_LIST_PAGE", script)
        self.assertIn("VEHICLE_MODEL_CONFIG_ENTRY_NOT_FOUND", script)
        self.assertIn("clicked_generic_filter=False", script)
        self.assertIn("clicked_vehicle_model_config=False", script)
        self.assertIn("clicked_color_or_year=False", script)
        self.assertIn("clicked_sort=False", script)
        self.assertIn("clicked_vehicle_card=False", script)
        self.assertIn("collected_vehicle_data=False", script)
        self.assertNotIn("tap_node(client, entry", script)
        self.assertNotIn("tap_price_low_to_high", script)
        self.assertNotIn("collect_list_whitelist_fields", script)

    def test_runtime_s07_click_script_only_clicks_model_config_entry(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s07_click_model_config.py").read_text(encoding="utf-8")

        self.assertIn("click_vehicle_model_config_entry", script)
        self.assertIn('actual_click_target="\u8f66\u578b\u914d\u7f6e"', script)
        self.assertIn('result["clicked_vehicle_model_config_once"] = bool(success)', script)
        self.assertIn("S08_VEHICLE_MODEL_CONFIG_PANEL", script)
        self.assertIn("VEHICLE_MODEL_CONFIG_CLICK_NO_NAVIGATION", script)
        self.assertIn("UNEXPECTED_DETAIL_PAGE", script)
        self.assertIn("clicked_generic_filter=False", script)
        self.assertIn("clicked_color_or_year=False", script)
        self.assertIn("clicked_sort=False", script)
        self.assertIn("clicked_vehicle_card=False", script)
        self.assertIn("collected_vehicle_data=False", script)
        self.assertNotIn("tap_price_low_to_high", script)
        self.assertNotIn("collect_list_whitelist_fields", script)
        self.assertNotIn("click_color_filter", script)

    def test_runtime_s08_read_script_is_read_only_panel_contract_probe(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s08_read_model_config_panel.py").read_text(encoding="utf-8")

        self.assertIn("read_vehicle_model_config_panel_contract", script)
        self.assertIn("recognize_panel_contract", script)
        self.assertIn("VEHICLE_MODEL_CONFIG_PANEL_NOT_RECOGNIZED", script)
        self.assertIn("clicked_color=False", script)
        self.assertIn("clicked_year_or_age=False", script)
        self.assertIn("clicked_confirm_or_view_result=False", script)
        self.assertIn("clicked_sort=False", script)
        self.assertIn("clicked_vehicle_card=False", script)
        self.assertIn("collected_vehicle_source_fields=False", script)
        self.assertNotIn("tap_node(", script)
        self.assertNotIn("client.tap(", script)
        self.assertNotIn("tap_price_low_to_high", script)
        self.assertNotIn("collect_list_whitelist_fields", script)
        self.assertNotIn("collect_vehicle_data", script)

    def test_runtime_s08_color_entry_script_only_clicks_color_entry_then_reads(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s08_click_color_entry.py").read_text(encoding="utf-8")

        self.assertIn("click_color_entry", script)
        self.assertIn("find_color_entry_node", script)
        self.assertIn("find_exact_color_node", script)
        self.assertIn("S08_COLOR_SELECTION_PANEL", script)
        self.assertIn("COLOR_ENTRY_NOT_FOUND", script)
        self.assertIn("COLOR_ENTRY_CLICK_NO_PANEL", script)
        self.assertIn("TARGET_COLOR_NOT_FOUND", script)
        self.assertIn("WRONG_PAGE_AFTER_COLOR_ENTRY_CLICK", script)
        self.assertIn('result["clicked_color_entry_once"] = bool(success)', script)
        self.assertIn("clicked_target_color=False", script)
        self.assertIn("clicked_any_color_option=False", script)
        self.assertIn("clicked_year_or_age=False", script)
        self.assertIn("clicked_confirm_or_view_result=False", script)
        self.assertIn("collected_vehicle_source_fields=False", script)
        self.assertIn("target_color_found=True", script)
        self.assertNotIn("click_target_color=True", script)
        self.assertNotIn("tap_price_low_to_high", script)
        self.assertNotIn("collect_list_whitelist_fields", script)
        self.assertNotIn("collect_vehicle_data", script)

    def test_runtime_s08_select_color_script_only_clicks_exact_target_color(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s08_select_color.py").read_text(encoding="utf-8")

        self.assertIn("click_target_color_option", script)
        self.assertIn("find_exact_color_node", script)
        self.assertIn("COLOR_ACTION_TARGET_MISMATCH", script)
        self.assertIn("COLOR_CLICK_NO_SELECTION", script)
        self.assertIn("WRONG_PAGE_AFTER_COLOR_CLICK", script)
        self.assertIn('result["clicked_target_color_once"] = bool(success)', script)
        self.assertIn("clicked_similar_or_other_color=False", script)
        self.assertIn("clicked_year_or_age=False", script)
        self.assertIn("clicked_confirm_or_view_result=False", script)
        self.assertIn("collected_vehicle_source_fields=False", script)
        self.assertIn("S08_COLOR_SELECTED", script)
        self.assertNotIn("click_pearl_white", script)
        self.assertNotIn("click_off_white", script)
        self.assertNotIn("tap_price_low_to_high", script)
        self.assertNotIn("collect_list_whitelist_fields", script)
        self.assertNotIn("collect_vehicle_data", script)

    def test_runtime_s08_year_entry_script_only_clicks_year_entry_then_reads(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s08_click_year_entry.py").read_text(encoding="utf-8")

        self.assertIn("click_year_or_age_entry", script)
        self.assertIn("find_year_entry_node", script)
        self.assertIn("find_left_age_tab_node", script)
        self.assertIn("S08_YEAR_SELECTION_PANEL", script)
        self.assertIn("YEAR_ENTRY_NOT_FOUND", script)
        self.assertIn("YEAR_ENTRY_CLICK_NO_PANEL", script)
        self.assertIn("LEFT_AGE_TAB_NOT_FOUND", script)
        self.assertIn('result["left_age_tab_visible"]', script)
        self.assertIn('result["left_age_tab_bounds"]', script)
        self.assertIn("WRONG_PAGE_AFTER_YEAR_ENTRY_CLICK", script)
        self.assertIn('result["clicked_year_entry_once"] = bool(success)', script)
        self.assertIn("clicked_year_option=False", script)
        self.assertIn("clicked_confirm_or_view_result=False", script)
        self.assertIn("collected_vehicle_source_fields=False", script)
        self.assertNotIn("year_related_options", script)
        self.assertNotIn("click_target_year_option=True", script)
        self.assertNotIn("tap_price_low_to_high", script)
        self.assertNotIn("collect_list_whitelist_fields", script)
        self.assertNotIn("collect_vehicle_data", script)

    def test_runtime_s08_left_age_tab_script_only_clicks_left_age_tab(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s08_click_left_age_tab.py").read_text(encoding="utf-8")

        self.assertIn("click_left_age_tab", script)
        self.assertIn("S08_AGE_EXACT_SLIDER_PANEL", script)
        self.assertIn("click_unlimited_age", script)
        self.assertIn("click_age_option", script)
        self.assertIn("set_age_range", script)
        self.assertIn("expand_age_range", script)
        self.assertIn("click_confirm", script)
        self.assertIn("collect_vehicle_data", script)

    def test_runtime_s08_detect_age_slider_script_is_read_only(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s08_detect_age_slider.py").read_text(encoding="utf-8")

        self.assertIn("detect_age_exact_slider", script)
        self.assertIn("S08_AGE_EXACT_SLIDER_PANEL", script)
        self.assertIn("slider_bounds", script)
        self.assertIn("track_bounds", script)
        self.assertIn("current_age_value", script)
        self.assertIn("drag_slider", script)
        self.assertIn("collect_vehicle_data", script)

    def test_unique_allowed_action_mismatch_is_blocked_before_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"), issue_classifier=classifier)
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute("S08_COLOR_SELECTED", "click_color_entry")

            self.assertEqual(raised.exception.code, "CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH")
            self.assertEqual(issues.read_all()[0]["code"], "CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH")

    def test_s08_year_option_scan_drift_is_blocked_before_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"), issue_classifier=classifier)
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute("S08_YEAR_SELECTION_PANEL", "read_year_selection_panel_contract")

            self.assertEqual(raised.exception.code, "S08_YEAR_CONTRACT_DRIFT_TO_OPTION_SCAN")
            self.assertEqual(issues.read_all()[0]["code"], "S08_YEAR_CONTRACT_DRIFT_TO_OPTION_SCAN")

    def test_color_change_invalidates_old_color_before_year_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"), issue_classifier=classifier)
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute(
                    "S08_COLOR_SELECTED",
                    "click_year_or_age_entry",
                    {
                        "target_color": "榛戣壊",
                        "current_selected_color": "鐧借壊",
                        "selected_color_ui_confirmed": True,
                    },
                )

            self.assertEqual(raised.exception.code, "TASK_COLOR_CHANGED_INVALIDATES_SELECTED_COLOR")
            self.assertEqual(issues.read_all()[0]["code"], "TASK_COLOR_CHANGED_INVALIDATES_SELECTED_COLOR")

    def test_color_ui_confirmation_is_required_before_year_entry(self):
        class MemoryIssues:
            def __init__(self):
                self._items = []

            def record(self, code, state_id, message, context=None, resolution=None):
                issue = {
                    "code": code,
                    "state_id": state_id,
                    "message": message,
                    "context": context or {},
                    "resolution": resolution,
                }
                self._items.append(issue)
                return issue

            def read_all(self):
                return list(self._items)

        class MemoryAudit:
            def log(self, *_args, **_kwargs):
                return None

        machine = PageStateMachine(load_config("pages.yaml"))
        classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))
        issues = MemoryIssues()
        audit = MemoryAudit()
        executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

        with self.assertRaises(GuaziFlowError) as raised:
            executor.execute(
                "S08_COLOR_SELECTED",
                "click_year_or_age_entry",
                {"target_color": "target-black", "current_selected_color": "target-black"},
            )

        self.assertEqual(raised.exception.code, "COLOR_STATE_NOT_CONFIRMED_BEFORE_AGE_SELECTION")
        self.assertEqual(issues.read_all()[0]["code"], "COLOR_STATE_NOT_CONFIRMED_BEFORE_AGE_SELECTION")

    def test_color_ui_confirmation_allows_year_entry_gate_to_pass(self):
        class MemoryIssues:
            def __init__(self):
                self._items = []

            def record(self, code, state_id, message, context=None, resolution=None):
                issue = {
                    "code": code,
                    "state_id": state_id,
                    "message": message,
                    "context": context or {},
                    "resolution": resolution,
                }
                self._items.append(issue)
                return issue

            def read_all(self):
                return list(self._items)

        class MemoryAudit:
            def log(self, *_args, **_kwargs):
                return None

        machine = PageStateMachine(load_config("pages.yaml"))
        classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))
        issues = MemoryIssues()
        audit = MemoryAudit()
        executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

        result = executor.execute(
            "S08_COLOR_SELECTED_SINGLE_TARGET",
            "click_year_or_age_entry",
            {
                "target_color": "target-black",
                "current_selected_color": "target-black",
                "selected_color_ui_confirmed": True,
            },
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(issues.read_all(), [])

    def test_multi_selected_color_blocks_downstream_before_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"), issue_classifier=classifier)
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute(
                    "S08_COLOR_MULTI_SELECTED",
                    "click_year_or_age_entry",
                    {"target_color": "榛戣壊", "selected_colors": ["榛戣壊", "鐧借壊"], "stale_color": "鐧借壊"},
                )

            self.assertEqual(raised.exception.code, "COLOR_MULTI_SELECTED_TARGET_AND_STALE_COLOR")
            self.assertEqual(issues.read_all()[0]["code"], "COLOR_MULTI_SELECTED_TARGET_AND_STALE_COLOR")

    def test_cancel_stale_color_requires_target_and_stale_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"), issue_classifier=classifier)
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute(
                    "S08_COLOR_MULTI_SELECTED",
                    "cancel_stale_selected_color",
                    {
                        "target_color": "榛戣壊",
                        "selected_colors": ["鐧借壊"],
                        "stale_color": "鐧借壊",
                        "actual_click_target": "鐧借壊",
                        "actual_click_target_role": "stale_selected_color_option",
                    },
                )

            self.assertEqual(raised.exception.code, "TARGET_COLOR_NOT_SELECTED")

    def test_cancel_stale_color_requires_actual_click_target_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"), issue_classifier=classifier)
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute(
                    "S08_COLOR_MULTI_SELECTED",
                    "cancel_stale_selected_color",
                    {
                        "target_color": "榛戣壊",
                        "selected_colors": ["榛戣壊", "鐧借壊"],
                        "stale_color": "鐧借壊",
                        "actual_click_target": "榛戣壊",
                        "actual_click_target_role": "stale_selected_color_option",
                    },
                )

            self.assertEqual(raised.exception.code, "COLOR_ACTION_TARGET_MISMATCH")

    def test_cancel_stale_color_allows_exact_stale_target_in_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"), issue_classifier=classifier)
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            result = executor.execute(
                "S08_COLOR_MULTI_SELECTED",
                "cancel_stale_selected_color",
                {
                    "target_color": "榛戣壊",
                    "selected_colors": ["榛戣壊", "鐧借壊"],
                    "stale_color": "鐧借壊",
                    "actual_click_target": "鐧借壊",
                    "actual_click_target_role": "stale_selected_color_option",
                },
            )

            self.assertTrue(result["ok"])

    def test_runtime_s08_cancel_stale_color_script_is_bounded(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s08_cancel_stale_color.py").read_text(encoding="utf-8")

        self.assertIn("S08_COLOR_MULTI_SELECTED", script)
        self.assertIn("S08_COLOR_SELECTED_SINGLE_TARGET", script)
        self.assertIn("cancel_stale_selected_color", script)
        self.assertIn("selected_color_chips", script)
        self.assertIn("STALE_COLOR_CANCEL_NO_EFFECT", script)
        self.assertIn("TARGET_COLOR_LOST_AFTER_STALE_COLOR_CANCEL", script)
        self.assertIn("clicked_target_color=False", script)
        self.assertIn("clicked_year_or_age=False", script)
        self.assertIn("dragged_slider=False", script)
        self.assertIn("clicked_confirm_or_view_result=False", script)
        self.assertIn("collected_vehicle_source_fields=False", script)

    def test_runtime_s08_set_age_slider_script_is_bounded(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "runtime_s08_set_age_slider.py").read_text(encoding="utf-8")

        self.assertIn("S08_AGE_EXACT_SLIDER_PANEL", script)
        self.assertIn("set_age_slider_exact_value", script)
        self.assertIn("set_right_age_handle_to_target", script)
        self.assertIn("detect_dual_handle_snapshot", script)
        self.assertIn("left_handle_value_before", script)
        self.assertIn("right_handle_value_before", script)
        self.assertIn("left_handle_value_after", script)
        self.assertIn("right_handle_value_after", script)
        self.assertIn("target_coordinate", script)
        self.assertIn("right_target_coordinate", script)
        self.assertIn("right_handle_actual_center", script)
        self.assertIn("right_handle_overlap_target_coordinate", script)
        self.assertIn("left_handle_physical_center", script)
        self.assertIn("xml_tick_node", script)
        self.assertIn("calculated_from_track", script)
        self.assertIn("AGE_SLIDER_ONLY_LEFT_HANDLE_SET", script)
        self.assertIn("AGE_SLIDER_SET_NO_VERIFICATION", script)
        self.assertIn("RIGHT_AGE_HANDLE_SET_NO_VERIFICATION", script)
        self.assertIn("RIGHT_HANDLE_DRAG_DURATION_MS = 900", script)
        self.assertIn("clicked_unlimited_age=False", script)
        self.assertIn("set_non_target_age=False", script)
        self.assertIn("clicked_confirm_or_view_result=False", script)
        self.assertIn("entered_vehicle_list=False", script)
        self.assertIn("collected_vehicle_source_fields=False", script)
        self.assertNotIn("click_confirm=True", script)
        self.assertNotIn("collect_vehicle_data=True", script)

    def test_set_right_age_handle_requires_left_already_at_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"))
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute(
                    "S08_AGE_EXACT_SLIDER_PANEL",
                    "set_right_age_handle_to_target",
                    {
                        "target_age": 6,
                        "left_handle_value": 4,
                        "right_handle_value": 10,
                        "actual_click_target": "6",
                        "actual_click_target_role": "right_age_handle",
                    },
                )

            self.assertEqual(raised.exception.code, "AGE_LEFT_HANDLE_NOT_TARGET")

    def test_validate_exact_age_range_blocks_any_range_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"))
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute(
                    "S08_AGE_EXACT_SLIDER_PANEL",
                    "validate_exact_age_range",
                    {
                        "target_age": 6,
                        "left_handle_value": 6,
                        "right_handle_value": 10,
                    },
                )

            self.assertEqual(raised.exception.code, "AGE_SLIDER_SET_NO_VERIFICATION")

    def test_validate_exact_age_range_requires_physical_overlap_and_age_calculation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            machine = PageStateMachine(load_config("pages.yaml"))
            issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"))
            audit = AuditLogger(tmp_path / "audit.jsonl")
            executor = ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

            with self.assertRaises(GuaziFlowError) as raised:
                executor.execute(
                    "S08_AGE_EXACT_SLIDER_PANEL",
                    "validate_exact_age_range",
                    {
                        "target_age": 6,
                        "left_handle_value": 6,
                        "right_handle_value": 6,
                        "left_handle_bounds": [650, 1235, 773, 1387],
                        "right_handle_bounds": [1053, 1235, 1176, 1387],
                        "target_age_calculation_verified": True,
                    },
                )

            self.assertEqual(raised.exception.code, "AGE_SLIDER_SET_NO_VERIFICATION")

            result = executor.execute(
                "S08_AGE_EXACT_SLIDER_PANEL",
                "validate_exact_age_range",
                {
                    "target_age": 6,
                    "left_handle_value": 6,
                    "right_handle_value": 6,
                    "left_handle_bounds": [650, 1235, 773, 1387],
                    "right_handle_bounds": [656, 1238, 779, 1388],
                    "target_age_calculation_verified": True,
                },
            )

            self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()


