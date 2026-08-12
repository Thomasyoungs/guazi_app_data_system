import unittest

from guazi_app_data_system.config_loader import load_config
from guazi_app_data_system.issue_classifier import (
    AGE_SLIDER_BOTH_HANDLES_SOLUTION_ID,
    SERIES_ACTION_TARGET_MISMATCH_SOLUTION_ID,
    IssueClassifier,
)


SERIES_XML = """
<hierarchy>
  <node content-desc="series-A#10;local-stock" bounds="[52,858][1168,1163]">
    <node text="\u8f66\u578b" bounds="[869,908][1129,1087]" clickable="true" />
  </node>
  <node content-desc="other-series#10;local-stock" bounds="[52,1164][1168,1469]">
    <node text="\u8f66\u578b" bounds="[869,1214][1129,1393]" clickable="true" />
  </node>
</hierarchy>
"""


class IssueClassifierTest(unittest.TestCase):
    def setUp(self):
        self.classifier = IssueClassifier(load_config("pages.yaml"), load_config("actions.yaml"))

    def test_s04_wrong_series_card_click_classifies_target_mismatch(self):
        classification = self.classifier.classify(
            current_state="S04",
            intended_action="click_series_model_button",
            expected_next_state="S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
            actual_next_state="S04_SERIES_LIST_PAGE_VERIFIED",
            actual_clicked_target={"text": "series-A", "role": "series_card", "series": "series-A"},
            before_xml=SERIES_XML,
            after_xml=SERIES_XML,
            task_context={"brand": "brand-A", "series": "series-A"},
        )

        self.assertEqual(classification["issue_code"], "SERIES_ACTION_TARGET_MISMATCH")
        self.assertEqual(classification["recommended_solution_id"], SERIES_ACTION_TARGET_MISMATCH_SOLUTION_ID)
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("click_series_model_button", classification["allowed_auto_actions"])
        self.assertIn("click_series_card", classification["forbidden_actions"])
        self.assertIn("click_series_name", classification["forbidden_actions"])

    def test_classifier_reads_action_contract_for_correct_action(self):
        classification = self.classifier.classify(
            current_state="S04",
            intended_action="click_series_model_button",
            expected_next_state="S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
            actual_next_state="S04_SERIES_LIST_PAGE_VERIFIED",
            actual_clicked_target={"text": "series-A", "role": "series_name", "series": "series-A"},
            before_xml=SERIES_XML,
            after_xml=SERIES_XML,
            action_contract=load_config("actions.yaml")["actions"]["click_series_model_button"],
            task_context={"series": "series-A"},
        )

        evidence = classification["evidence"]
        self.assertEqual(evidence["action_contract"]["text"], "\u8f66\u578b")
        self.assertEqual(evidence["action_contract"]["target_role"], "series_model_button")
        self.assertTrue(evidence["before"]["model_button_found"])

    def test_unknown_new_page_is_candidate_not_approved(self):
        classification = self.classifier.classify(
            current_state="S04",
            intended_action="click_series_model_button",
            expected_next_state="S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
            actual_next_state="UNKNOWN_PAGE",
            actual_clicked_target={"text": "\u8f66\u578b", "role": "series_model_button", "series": "series-A"},
            before_xml=SERIES_XML,
            after_xml="<hierarchy><node text='new-page' /></hierarchy>",
            task_context={"series": "series-A"},
        )

        self.assertEqual(classification["issue_code"], "SERIES_CLICK_REVEALS_NEW_INTERMEDIATE_PAGE")
        self.assertEqual(classification["candidate_or_approved"], "candidate")
        self.assertFalse(classification["solution_record"]["approved"])

    def test_vehicle_list_after_model_button_is_specific_wrong_page_issue(self):
        classification = self.classifier.classify(
            current_state="S04",
            intended_action="click_series_model_button",
            expected_next_state="S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
            actual_next_state="VEHICLE_LIST_OR_DETAIL",
            actual_clicked_target={"text": "\u8f66\u578b", "role": "series_model_button", "series": "series-A"},
            before_xml=SERIES_XML,
            after_xml="<hierarchy><node text='sort' /><node text='registration' /></hierarchy>",
            task_context={"series": "series-A"},
        )

        self.assertEqual(classification["issue_code"], "WRONG_PAGE_AFTER_MODEL_BUTTON_CLICK")
        self.assertEqual(classification["candidate_or_approved"], "candidate")
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_model_year_click_without_selection_is_specific_issue(self):
        classification = self.classifier.classify(
            current_state="S05",
            intended_action="tap_target_year",
            expected_next_state="S05_MODEL_YEAR_SELECTED",
            actual_next_state="S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
            actual_clicked_target={"text": "2020\u6b3e", "role": "model_year", "bounds": [0, 1371, 293, 1501]},
            before_xml="<hierarchy><node text='2020\u6b3e' bounds='[0,1371][293,1501]' /></hierarchy>",
            after_xml="<hierarchy><node text='2020\u6b3e' bounds='[0,1371][293,1501]' /></hierarchy>",
            task_context={"model_year": "2020\u6b3e"},
        )

        self.assertEqual(classification["issue_code"], "MODEL_YEAR_CLICK_NO_SELECTION")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("tap_exact_trim", classification["forbidden_actions"])
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_vehicle_list_after_model_year_click_is_specific_wrong_page_issue(self):
        classification = self.classifier.classify(
            current_state="S05",
            intended_action="tap_target_year",
            expected_next_state="S05_MODEL_YEAR_SELECTED",
            actual_next_state="VEHICLE_LIST_OR_DETAIL",
            actual_clicked_target={"text": "2020\u6b3e", "role": "model_year", "bounds": [0, 1371, 293, 1501]},
            before_xml="<hierarchy><node text='2020\u6b3e' bounds='[0,1371][293,1501]' /></hierarchy>",
            after_xml="<hierarchy><node text='sort' /><node text='registration' /></hierarchy>",
            task_context={"model_year": "2020\u6b3e"},
        )

        self.assertEqual(classification["issue_code"], "WRONG_PAGE_AFTER_MODEL_YEAR_CLICK")
        self.assertEqual(classification["candidate_or_approved"], "candidate")
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_trim_click_without_selection_is_specific_issue(self):
        classification = self.classifier.classify(
            current_state="S05_MODEL_YEAR_SELECTED",
            intended_action="tap_exact_trim",
            expected_next_state="S05_TRIM_SELECTED",
            actual_next_state="S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
            actual_clicked_target={"text": "330TSI DSG trim-A", "role": "trim", "bounds": [293, 601, 1220, 741]},
            before_xml="<hierarchy><node text='330TSI DSG trim-A' bounds='[293,601][1220,741]' /></hierarchy>",
            after_xml="<hierarchy><node text='330TSI DSG trim-A' bounds='[293,601][1220,741]' /></hierarchy>",
            task_context={"trim": "330TSI DSG trim-A"},
        )

        self.assertEqual(classification["issue_code"], "TRIM_CLICK_NO_SELECTION")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("tap_green_confirm", classification["forbidden_actions"])
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_trim_click_uses_emission_normalization_without_alias(self):
        classification = self.classifier.classify(
            current_state="S05_MODEL_YEAR_SELECTED",
            intended_action="tap_exact_trim",
            expected_next_state="S05_TRIM_SELECTED",
            actual_next_state="S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
            actual_clicked_target={"text": "330TSI \u5c0a\u8d35\u7248 \u56fdVI", "role": "trim", "bounds": [293, 601, 1220, 741]},
            before_xml="<hierarchy><node text='330TSI \u5c0a\u8d35\u7248 \u56fdVI' bounds='[293,601][1220,741]' /></hierarchy>",
            after_xml="<hierarchy><node text='330TSI \u5c0a\u8d35\u7248 \u56fdVI' bounds='[293,601][1220,741]' /></hierarchy>",
            task_context={"trim": "330TSI \u5c0a\u8d35\u7248 \u56fd\u516d"},
        )

        self.assertEqual(classification["issue_code"], "TRIM_CLICK_NO_SELECTION")
        self.assertTrue(classification["evidence"]["trim_match"]["emission_normalization_used"])
        self.assertEqual(classification["evidence"]["trim_match"]["target_trim_normalized"], "330TSI \u5c0a\u8d35\u7248 \u56fdVI")
        self.assertEqual(classification["evidence"]["trim_match"]["actual_clicked_trim_normalized"], "330TSI \u5c0a\u8d35\u7248 \u56fdVI")

    def test_trim_click_does_not_ignore_dsg_difference(self):
        classification = self.classifier.classify(
            current_state="S05_MODEL_YEAR_SELECTED",
            intended_action="tap_exact_trim",
            expected_next_state="S05_TRIM_SELECTED",
            actual_next_state="S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
            actual_clicked_target={"text": "330TSI \u5c0a\u8d35\u7248 \u56fdVI", "role": "trim", "bounds": [293, 601, 1220, 741]},
            before_xml="<hierarchy><node text='330TSI \u5c0a\u8d35\u7248 \u56fdVI' bounds='[293,601][1220,741]' /></hierarchy>",
            after_xml="<hierarchy><node text='330TSI \u5c0a\u8d35\u7248 \u56fdVI' bounds='[293,601][1220,741]' /></hierarchy>",
            task_context={"trim": "330TSI DSG \u5c0a\u8d35\u7248 \u56fdVI"},
        )

        self.assertNotEqual(classification["issue_code"], "TRIM_CLICK_NO_SELECTION")

    def test_vehicle_list_after_trim_click_is_specific_wrong_page_issue(self):
        classification = self.classifier.classify(
            current_state="S05_MODEL_YEAR_SELECTED",
            intended_action="tap_exact_trim",
            expected_next_state="S05_TRIM_SELECTED",
            actual_next_state="VEHICLE_LIST_OR_DETAIL",
            actual_clicked_target={"text": "330TSI DSG trim-A", "role": "trim", "bounds": [293, 601, 1220, 741]},
            before_xml="<hierarchy><node text='330TSI DSG trim-A' bounds='[293,601][1220,741]' /></hierarchy>",
            after_xml="<hierarchy><node text='sort' /><node text='registration' /></hierarchy>",
            task_context={"trim": "330TSI DSG trim-A"},
        )

        self.assertEqual(classification["issue_code"], "WRONG_PAGE_AFTER_TRIM_CLICK")
        self.assertEqual(classification["candidate_or_approved"], "candidate")
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_s07_generic_filter_planning_is_contract_drift(self):
        classification = self.classifier.classify(
            current_state="S07_VEHICLE_LIST_PAGE",
            intended_action="click_generic_filter",
            expected_next_state="S07_MODEL_CONFIG_ENTRY_PENDING",
            actual_next_state="S07_VEHICLE_LIST_PAGE",
            actual_clicked_target={"text": "\u7b5b\u9009", "role": "generic_filter"},
            before_xml="<hierarchy />",
            after_xml="<hierarchy />",
            task_context={},
        )

        self.assertEqual(classification["issue_code"], "S07_CONTRACT_DRIFT_TO_GENERIC_FILTER")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertEqual(
            classification["recommended_solution_id"],
            "SOL-S07-CONTRACT-DRIFT-TO-GENERIC-FILTER-DETECT-MODEL-CONFIG",
        )
        self.assertIn("detect_vehicle_model_config_entry", classification["allowed_auto_actions"])
        self.assertIn("click_generic_filter", classification["forbidden_actions"])
        self.assertTrue(classification["solution_record"]["approved"])
        self.assertEqual(classification["solution_record"]["max_auto_retries"], 0)

    def test_s07_vehicle_card_planning_is_specific_contract_drift(self):
        classification = self.classifier.classify(
            current_state="S07_VEHICLE_LIST_PAGE",
            intended_action="click_vehicle_card",
            expected_next_state="DETAIL_PAGE",
            actual_next_state="S07_VEHICLE_LIST_PAGE",
            actual_clicked_target={"text": "card", "role": "vehicle_card"},
            before_xml="<hierarchy />",
            after_xml="<hierarchy />",
            task_context={},
        )

        self.assertEqual(classification["issue_code"], "S07_CONTRACT_DRIFT_TO_GENERIC_FILTER")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("click_vehicle_card", classification["forbidden_actions"])

    def test_s07_model_config_click_without_navigation_is_specific_issue(self):
        classification = self.classifier.classify(
            current_state="S07_VEHICLE_LIST_PAGE",
            intended_action="click_vehicle_model_config_entry",
            expected_next_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            actual_next_state="S07_VEHICLE_LIST_PAGE",
            actual_clicked_target={"text": "\u8f66\u578b\u914d\u7f6e", "role": "vehicle_model_config_entry"},
            before_xml="<hierarchy />",
            after_xml="<hierarchy />",
            task_context={},
        )

        self.assertEqual(classification["issue_code"], "VEHICLE_MODEL_CONFIG_CLICK_NO_NAVIGATION")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("click_vehicle_card", classification["forbidden_actions"])
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_s07_model_config_click_to_detail_is_unexpected_detail(self):
        classification = self.classifier.classify(
            current_state="S07_VEHICLE_LIST_PAGE",
            intended_action="click_vehicle_model_config_entry",
            expected_next_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            actual_next_state="VEHICLE_DETAIL_PAGE",
            actual_clicked_target={"text": "\u8f66\u578b\u914d\u7f6e", "role": "vehicle_model_config_entry"},
            before_xml="<hierarchy />",
            after_xml="<hierarchy />",
            task_context={},
        )

        self.assertEqual(classification["issue_code"], "UNEXPECTED_DETAIL_PAGE")
        self.assertEqual(classification["candidate_or_approved"], "candidate")
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_s08_color_entry_no_panel_is_specific_issue(self):
        classification = self.classifier.classify(
            current_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            intended_action="click_color_entry",
            expected_next_state="S08_COLOR_SELECTION_PANEL",
            actual_next_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            actual_clicked_target={"text": "\u989c\u8272", "role": "color_entry"},
            before_xml="<hierarchy />",
            after_xml="<hierarchy />",
            task_context={"color": "\u767d\u8272"},
        )

        self.assertEqual(classification["issue_code"], "COLOR_ENTRY_CLICK_NO_PANEL")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("click_target_color", classification["forbidden_actions"])
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_s08_color_entry_to_vehicle_list_is_wrong_page_issue(self):
        classification = self.classifier.classify(
            current_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            intended_action="click_color_entry",
            expected_next_state="S08_COLOR_SELECTION_PANEL",
            actual_next_state="S07_VEHICLE_LIST_PAGE",
            actual_clicked_target={"text": "\u989c\u8272", "role": "color_entry"},
            before_xml="<hierarchy />",
            after_xml="<hierarchy><node text='缁煎悎鎺掑簭' /></hierarchy>",
            task_context={"color": "\u767d\u8272"},
        )

        self.assertEqual(classification["issue_code"], "WRONG_PAGE_AFTER_COLOR_ENTRY_CLICK")
        self.assertEqual(classification["candidate_or_approved"], "candidate")
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_s08_target_color_missing_is_specific_issue(self):
        classification = self.classifier.classify(
            current_state="S08_COLOR_SELECTION_PANEL",
            intended_action="read_color_selection_panel_contract",
            expected_next_state="S08_COLOR_SELECTION_PANEL",
            actual_next_state="S08_COLOR_SELECTION_PANEL",
            actual_clicked_target={"text": None, "role": None},
            before_xml="<hierarchy><node text='榛戣壊' /><node text='閾惰壊' /></hierarchy>",
            after_xml="<hierarchy><node text='榛戣壊' /><node text='閾惰壊' /></hierarchy>",
            task_context={"color": "\u767d\u8272"},
        )

        self.assertEqual(classification["issue_code"], "TARGET_COLOR_NOT_FOUND")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("click_similar_color", classification["forbidden_actions"])
        self.assertIn("fuzzy_match_color", classification["forbidden_actions"])

    def test_s08_color_click_target_mismatch_is_blocked(self):
        classification = self.classifier.classify(
            current_state="S08_COLOR_SELECTION_PANEL",
            intended_action="click_target_color_option",
            expected_next_state="S08_COLOR_SELECTED",
            actual_next_state="S08_COLOR_SELECTION_PANEL",
            actual_clicked_target={"text": "pearl-white", "role": "color_option"},
            before_xml="<hierarchy><node text='white' /><node text='pearl-white' /></hierarchy>",
            after_xml="<hierarchy><node text='white' /><node text='pearl-white' /></hierarchy>",
            task_context={"color": "鐧借壊"},
        )

        self.assertEqual(classification["issue_code"], "COLOR_ACTION_TARGET_MISMATCH")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("click_similar_color", classification["forbidden_actions"])
        self.assertIn("auto_merge_color_family", classification["forbidden_actions"])

    def test_s08_color_click_without_selection_is_specific_issue(self):
        classification = self.classifier.classify(
            current_state="S08_COLOR_SELECTION_PANEL",
            intended_action="click_target_color_option",
            expected_next_state="S08_COLOR_SELECTED",
            actual_next_state="S08_COLOR_SELECTION_PANEL",
            actual_clicked_target={"text": "鐧借壊", "role": "color_option"},
            before_xml="<hierarchy><node text='鐧借壊' /></hierarchy>",
            after_xml="<hierarchy><node text='鐧借壊' /></hierarchy>",
            task_context={"color": "鐧借壊"},
        )

        self.assertEqual(classification["issue_code"], "COLOR_CLICK_NO_SELECTION")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("retry_same_wrong_click", classification["forbidden_actions"])
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_s08_color_click_to_vehicle_list_is_wrong_page_issue(self):
        classification = self.classifier.classify(
            current_state="S08_COLOR_SELECTION_PANEL",
            intended_action="click_target_color_option",
            expected_next_state="S08_COLOR_SELECTED",
            actual_next_state="S07_VEHICLE_LIST_PAGE",
            actual_clicked_target={"text": "鐧借壊", "role": "color_option"},
            before_xml="<hierarchy><node text='鐧借壊' /></hierarchy>",
            after_xml="<hierarchy><node text='缁煎悎鎺掑簭' /></hierarchy>",
            task_context={"color": "鐧借壊"},
        )

        self.assertEqual(classification["issue_code"], "WRONG_PAGE_AFTER_COLOR_CLICK")
        self.assertEqual(classification["candidate_or_approved"], "candidate")
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_validate_series_model_click_target_requires_same_row(self):
        validation = self.classifier.validate_series_model_click_target(
            task_context={"series": "series-A"},
            actual_clicked_target={
                "text": "\u8f66\u578b",
                "role": "series_model_button",
                "series": "other-series",
                "bounds": [869, 1214, 1129, 1393],
            },
            before_xml=SERIES_XML,
        )

        self.assertTrue(validation["series_model_button_found"])
        self.assertFalse(validation["same_row_or_card"])
        self.assertTrue(validation["actual_target_is_model_button"])


    def test_s08_year_entry_target_mismatch_is_blocked(self):
        classification = self.classifier.classify(
            current_state="S08_COLOR_SELECTED",
            intended_action="click_year_or_age_entry",
            expected_next_state="S08_YEAR_SELECTION_PANEL",
            actual_next_state="S08_COLOR_SELECTED",
            actual_clicked_target={"text": "棰滆壊", "role": "color_entry"},
            before_xml="<hierarchy><node text='鐧借壊' /></hierarchy>",
            after_xml="<hierarchy><node text='鐧借壊' /></hierarchy>",
            task_context={"vehicle_year": 2020, "selected_color_ui_confirmed": True},
        )

        self.assertEqual(classification["issue_code"], "YEAR_ACTION_TARGET_MISMATCH")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("click_target_year_option", classification["forbidden_actions"])
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_s08_year_entry_click_without_panel_is_specific_issue(self):
        classification = self.classifier.classify(
            current_state="S08_COLOR_SELECTED",
            intended_action="click_year_or_age_entry",
            expected_next_state="S08_YEAR_SELECTION_PANEL",
            actual_next_state="S08_COLOR_SELECTED",
            actual_clicked_target={"text": "骞存", "role": "year_or_age_entry"},
            before_xml="<hierarchy><node text='鐧借壊' /></hierarchy>",
            after_xml="<hierarchy><node text='鐧借壊' /></hierarchy>",
            task_context={"vehicle_year": 2020, "selected_color_ui_confirmed": True},
        )

        self.assertEqual(classification["issue_code"], "YEAR_ENTRY_CLICK_NO_PANEL")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("retry_same_wrong_click", classification["forbidden_actions"])
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_s08_year_entry_click_to_vehicle_list_is_wrong_page_issue(self):
        classification = self.classifier.classify(
            current_state="S08_COLOR_SELECTED",
            intended_action="click_year_or_age_entry",
            expected_next_state="S08_YEAR_SELECTION_PANEL",
            actual_next_state="S07_VEHICLE_LIST_PAGE",
            actual_clicked_target={"text": "杞﹂緞", "role": "year_or_age_entry"},
            before_xml="<hierarchy><node text='鐧借壊' /></hierarchy>",
            after_xml="<hierarchy><node text='缁煎悎鎺掑簭' /></hierarchy>",
            task_context={"vehicle_year": 2020, "selected_color_ui_confirmed": True},
        )

        self.assertEqual(classification["issue_code"], "WRONG_PAGE_AFTER_YEAR_ENTRY_CLICK")
        self.assertEqual(classification["candidate_or_approved"], "candidate")
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_s08_year_panel_without_left_age_tab_is_specific_issue(self):
        classification = self.classifier.classify(
            current_state="S08_YEAR_SELECTION_PANEL",
            intended_action="detect_left_age_tab",
            expected_next_state="S08_YEAR_SELECTION_PANEL",
            actual_next_state="S08_YEAR_SELECTION_PANEL",
            actual_clicked_target={"text": None, "role": None},
            before_xml="<hierarchy><node text='涓嶉檺杞﹂緞' /><node text='鏌ョ湅34杈? /></hierarchy>",
            after_xml="<hierarchy><node text='涓嶉檺杞﹂緞' /><node text='鏌ョ湅34杈? /></hierarchy>",
            task_context={"vehicle_year": 2020},
        )

        self.assertEqual(classification["issue_code"], "LEFT_AGE_TAB_NOT_FOUND")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("click_unlimited_age", classification["forbidden_actions"])
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_s08_year_option_scan_is_contract_drift(self):
        classification = self.classifier.classify(
            current_state="S08_YEAR_SELECTION_PANEL",
            intended_action="read_year_selection_panel_contract",
            expected_next_state="S08_AGE_EXACT_SLIDER_PANEL",
            actual_next_state="S08_YEAR_SELECTION_PANEL",
            actual_clicked_target={"text": None, "role": None},
            before_xml="<hierarchy><node text='杞﹂緞' /><node text='涓嶉檺杞﹂緞' /></hierarchy>",
            after_xml="<hierarchy><node text='杞﹂緞' /><node text='涓嶉檺杞﹂緞' /></hierarchy>",
            task_context={"vehicle_year": 2020, "registration_date_raw": "2020.4"},
        )

        self.assertEqual(classification["issue_code"], "S08_YEAR_CONTRACT_DRIFT_TO_OPTION_SCAN")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertEqual(
            classification["recommended_solution_id"],
            "SOL-S08-YEAR-CONTRACT-DRIFT-TO-OPTION-SCAN-DETECT-LEFT-AGE-TAB",
        )
        self.assertIn("detect_left_age_tab", classification["allowed_auto_actions"])
        self.assertIn("click_unlimited_age", classification["forbidden_actions"])

    def test_s08_age_slider_panel_evidence_wins_over_background_s07_text(self):
        slider_panel_xml = """
        <hierarchy>
          <node text="\u8f66\u9f84" bounds="[0,1153][260,1300]" />
          <node text="\u8f66\u9f84\uff08\u5e74\uff09" bounds="[328,1053][542,1108]" />
          <node text="\u4e0d\u9650\u8f66\u9f84" bounds="[965,1053][1153,1108]" />
          <node text="0" bounds="[279,1235][448,1274]" />
          <node text="2" bounds="[396,1235][562,1274]" />
          <node text="4" bounds="[510,1235][676,1274]" />
          <node text="6" bounds="[624,1235][789,1274]" />
          <node text="8" bounds="[737,1235][906,1274]" />
          <node text="10" bounds="[854,1235][1020,1274]" />
          <node text="\u4e0d\u9650" bounds="[1027,1235][1192,1274]" />
          <node text="\u7efc\u5408\u6392\u5e8f" bounds="[0,200][200,260]" />
        </hierarchy>
        """

        classification = self.classifier.classify(
            current_state="S08_YEAR_SELECTION_PANEL",
            intended_action="click_left_age_tab",
            expected_next_state="S08_AGE_EXACT_SLIDER_PANEL",
            actual_next_state="S07_VEHICLE_LIST_PAGE",
            actual_clicked_target={"text": "\u8f66\u9f84", "role": "left_age_tab"},
            before_xml="<hierarchy><node text='\u8f66\u9f84' /></hierarchy>",
            after_xml=slider_panel_xml,
            task_context={"vehicle_year": 2020, "registration_date_raw": "2020.4"},
        )

        self.assertEqual(classification["issue_code"], "AGE_SLIDER_PANEL_MISCLASSIFIED_AS_S07")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertEqual(classification["evidence"]["page_priority"], "S08_AGE_EXACT_SLIDER_PANEL")
        self.assertTrue(classification["evidence"]["background_list_residual_ignored"])
        self.assertTrue(classification["evidence"]["after_age_slider"]["age_slider_evidence_found"])
        self.assertIn("detect_age_exact_slider", classification["allowed_auto_actions"])
        self.assertIn("enter_vehicle_list", classification["forbidden_actions"])
        self.assertIn("collect_vehicle_data", classification["forbidden_actions"])

    def test_task_color_change_invalidates_existing_selected_color(self):
        classification = self.classifier.classify(
            current_state="S08_COLOR_SELECTED",
            intended_action="click_year_or_age_entry",
            expected_next_state="S08_YEAR_SELECTION_PANEL",
            actual_next_state="S08_COLOR_SELECTED",
            actual_clicked_target={"text": "骞存", "role": "year_or_age_entry"},
            before_xml="<hierarchy><node text='鐧借壊' /></hierarchy>",
            after_xml="<hierarchy><node text='鐧借壊' /></hierarchy>",
            task_context={
                "color": "榛戣壊",
                "selected_color": "鐧借壊",
                "selected_color_ui_confirmed": True,
            },
        )

        self.assertEqual(classification["issue_code"], "TASK_COLOR_CHANGED_INVALIDATES_SELECTED_COLOR")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertEqual(classification["recommended_solution_id"], "SOL-TASK-COLOR-CHANGED-RESELECT-COLOR")
        self.assertIn("continue_with_old_color", classification["forbidden_actions"])

    def test_color_state_must_be_confirmed_in_panel_before_year_entry(self):
        classification = self.classifier.classify(
            current_state="S08_COLOR_SELECTED",
            intended_action="click_year_or_age_entry",
            expected_next_state="S08_YEAR_SELECTION_PANEL",
            actual_next_state="S08_COLOR_SELECTED",
            actual_clicked_target={"text": "year-entry", "role": "year_or_age_entry"},
            before_xml="<hierarchy><node text='target-black' /></hierarchy>",
            after_xml="<hierarchy><node text='target-black' /></hierarchy>",
            task_context={"color": "target-black", "selected_color": "target-black", "selected_color_ui_confirmed": False},
        )

        self.assertEqual(classification["issue_code"], "COLOR_STATE_NOT_CONFIRMED_BEFORE_AGE_SELECTION")
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("click_year_or_age_entry", classification["forbidden_actions"])
        self.assertIn("click_view_result", classification["forbidden_actions"])

    def test_dual_handle_left_only_set_is_specific_issue(self):
        slider_panel_xml = """
        <hierarchy>
          <node text="杞﹂緞" bounds="[0,1014][260,1160]" />
          <node text="杞﹂緞锛堝勾锛? bounds="[328,1053][542,1108]" />
          <node text="0" bounds="[279,1235][448,1274]" />
          <node text="2" bounds="[396,1235][562,1274]" />
          <node text="4" bounds="[510,1235][676,1274]" />
          <node text="6" bounds="[624,1235][789,1274]" />
          <node text="8" bounds="[737,1235][906,1274]" />
          <node text="10" bounds="[854,1235][1020,1274]" />
          <node text="涓嶉檺" bounds="[1027,1235][1192,1274]" />
        </hierarchy>
        """

        classification = self.classifier.classify(
            current_state="S08_AGE_EXACT_SLIDER_PANEL",
            intended_action="set_age_slider_exact_value",
            expected_next_state="S08_AGE_EXACT_VALUE_SELECTED",
            actual_next_state="S08_AGE_LEFT_HANDLE_SET_ONLY",
            actual_clicked_target={"text": "6", "role": "right_age_handle"},
            before_xml=slider_panel_xml,
            after_xml=slider_panel_xml,
            task_context={
                "target_age": 6,
                "left_handle_value_after": 6,
                "right_handle_value_after": "涓嶉檺",
            },
        )

        self.assertEqual(classification["issue_code"], "AGE_SLIDER_ONLY_LEFT_HANDLE_SET")
        self.assertEqual(classification["recommended_solution_id"], AGE_SLIDER_BOTH_HANDLES_SOLUTION_ID)
        self.assertEqual(classification["candidate_or_approved"], "approved")
        self.assertIn("set_right_age_handle_to_target", classification["allowed_auto_actions"])
        self.assertIn("treat_range_as_exact", classification["forbidden_actions"])


if __name__ == "__main__":
    unittest.main()


