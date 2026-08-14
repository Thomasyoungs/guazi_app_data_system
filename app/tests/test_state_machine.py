import unittest

from guazi_app_data_system.config_loader import load_config
from guazi_app_data_system.exception_handler import GuaziFlowError
from guazi_app_data_system.page_recognition import PageRecognizer
from guazi_app_data_system.page_state_machine import PageStateMachine


class StateMachineTest(unittest.TestCase):
    def setUp(self):
        self.pages = load_config("pages.yaml")
        self.machine = PageStateMachine(self.pages)

    def test_s03_requires_select_brand_text(self):
        recognizer = PageRecognizer(self.pages)
        s03 = self.machine.get_page("S03")

        self.assertTrue(recognizer.matches(s03, "\u9876\u90e8 \u9009\u62e9\u54c1\u724c A B C D Z \u54c1\u724c\u5217\u8868"))
        self.assertFalse(recognizer.matches(s03, "A B C D Z \u54c1\u724c\u5217\u8868"))

    def test_home_requires_four_bottom_tabs_and_new_energy_is_optional(self):
        recognizer = PageRecognizer(self.pages)
        s01 = self.machine.get_page("S01")

        self.assertTrue(recognizer.matches(s01, "首页 选车 卖车 我的 新能源"))
        self.assertTrue(recognizer.matches(s01, "首页 选车 卖车 我的"))
        self.assertFalse(recognizer.matches(s01, "首页 选车 卖车 新能源"))

    def test_s02_naming_does_not_mix_entry_terms(self):
        all_page_names = " ".join(page["name"] for page in self.pages["pages"])

        self.assertIn("选车页", self.machine.get_page("S02")["name"])
        self.assertNotIn("买车入口", all_page_names)
        self.assertNotIn("选车入口", all_page_names)

    def test_sort_transition(self):
        self.assertEqual(self.machine.transition("S08", "sort_present"), "S09")
        self.assertEqual(self.machine.transition("S09", "sorted_same_source_page_detected"), "S10")

    def test_forbidden_action_is_blocked(self):
        with self.assertRaises(GuaziFlowError):
            self.machine.assert_action_allowed("S08", "tap_detail_before_sort")

    def test_same_source_filtering_actions_are_explicit(self):
        s07 = self.machine.get_page("S07")
        s08 = self.machine.get_page("S08")
        s10 = self.machine.get_page("S10")

        self.assertIn("apply_narrowest_year_filter", s07["allowed_actions"])
        self.assertIn("expand_year_range_as_same_source", s07["forbidden_actions"])
        self.assertIn("auto_merge_color_family", s07["forbidden_actions"])
        self.assertIn("filter_list_by_exact_year_and_color", s08["allowed_actions"])
        self.assertIn("include_non_same_source_vehicle", s10["forbidden_actions"])

    def test_home_tab_only_allows_select_car_tab_switch(self):
        recognizer = PageRecognizer(self.pages)
        state = self.machine.get_page("S01")
        bottom_nav_text = "\u9996\u9875 \u9009\u8f66 \u5356\u8f66 \u65b0\u80fd\u6e90 \u6211\u7684"

        self.assertTrue(recognizer.matches(state, bottom_nav_text, context={"current_tab": "\u9996\u9875"}))
        self.assertFalse(recognizer.matches(state, "\u9996\u9875 \u9009\u8f66 \u5356\u8f66", context={"current_tab": "\u9996\u9875"}))
        self.assertEqual(self.machine.transition("S01", "buy_car_tab_green"), "S02")
        self.assertIn("click_bottom_select_car_tab", state["allowed_actions"])
        self.assertIn("tap_search", state["forbidden_actions"])
        self.assertIn("tap_recommend_car", state["forbidden_actions"])
        self.assertIn("tap_sell_car", state["forbidden_actions"])
        self.assertIn("tap_my", state["forbidden_actions"])



    def test_s02_select_car_tab_only_allows_brand_entry(self):
        recognizer = PageRecognizer(self.pages)
        state = self.machine.get_page("S02_SELECT_CAR_TAB")
        select_text = "\u9996\u9875 \u9009\u8f66 \u5356\u8f66 \u65b0\u80fd\u6e90 \u6211\u7684 \u54c1\u724c \u9884\u7b97"

        self.assertTrue(recognizer.matches(state, select_text, context={"current_tab": "\u9009\u8f66"}))
        self.assertFalse(recognizer.matches(state, select_text, context={"current_tab": "\u9996\u9875"}))
        self.assertFalse(recognizer.matches(state, select_text + " \u9009\u62e9\u54c1\u724c", context={"current_tab": "\u9009\u8f66"}))
        self.assertEqual(self.machine.transition("S02_SELECT_CAR_TAB", "brand_select_page_verified"), "S03")
        self.assertIn("click_brand_entry", state["allowed_actions"])
        self.assertIn("tap_target_brand", state["forbidden_actions"])
        self.assertIn("click_series_model_button", state["forbidden_actions"])
        self.assertIn("collect_list_whitelist_fields", state["forbidden_actions"])

    def test_s04_series_page_only_allows_series_model_button_click(self):
        state = self.machine.get_page("S04")

        self.assertEqual(self.machine.transition("S04", "trim_page_detected"), "S05")
        self.assertEqual(state["allowed_actions"], ["click_series_model_button"])
        self.assertIn("click_series_card", state["forbidden_actions"])
        self.assertIn("click_series_name", state["forbidden_actions"])
        self.assertIn("click_other_series", state["forbidden_actions"])
        self.assertIn("click_other_series_model_button", state["forbidden_actions"])
        self.assertIn("enter_vehicle_list", state["forbidden_actions"])
        self.assertIn("tap_ad", state["forbidden_actions"])

    def test_s05_model_year_selection_contract(self):
        state = self.machine.get_page("S05")
        selected = self.machine.get_page("S05_MODEL_YEAR_SELECTED")

        self.assertEqual(self.machine.transition("S05", "target_model_year_selected"), "S05_MODEL_YEAR_SELECTED")
        self.assertIn("tap_target_year", state["allowed_actions"])
        self.assertIn("tap_other_model_year", state["forbidden_actions"])
        self.assertIn("tap_target_trim_before_year_selected", state["forbidden_actions"])
        self.assertIn("enter_vehicle_list", state["forbidden_actions"])
        self.assertIn("collect_vehicle_data", state["forbidden_actions"])
        self.assertIn("tap_exact_trim", selected["allowed_actions"])
        self.assertIn("tap_green_confirm", selected["forbidden_actions"])

    def test_s05_trim_selection_contract(self):
        selected = self.machine.get_page("S05_MODEL_YEAR_SELECTED")
        trim_selected = self.machine.get_page("S05_TRIM_SELECTED")
        confirm_action = load_config("actions.yaml")["actions"]["tap_green_confirm"]

        self.assertEqual(self.machine.transition("S05_MODEL_YEAR_SELECTED", "target_trim_selected"), "S05_TRIM_SELECTED")
        self.assertIn("tap_exact_trim", selected["allowed_actions"])
        self.assertIn("tap_similar_trim", selected["forbidden_actions"])
        self.assertIn("tap_partial_trim_match", selected["forbidden_actions"])
        self.assertIn("tap_model_year_after_selected", selected["forbidden_actions"])
        self.assertIn("collect_vehicle_data", selected["forbidden_actions"])
        self.assertIn("tap_green_confirm", trim_selected["allowed_actions"])
        self.assertEqual(confirm_action["precondition"], "S05_TRIM_SELECTED")
        self.assertIn("sort", confirm_action["forbidden_targets"])
        self.assertIn("price_low_to_high", confirm_action["forbidden_targets"])
        self.assertIn("vehicle_detail", confirm_action["forbidden_targets"])
        self.assertIn("collect_vehicle_data", trim_selected["forbidden_actions"])

    def test_s07_vehicle_list_supports_model_config_and_same_source_branching(self):
        state = self.machine.get_page("S07_VEHICLE_LIST_PAGE")
        actions = load_config("actions.yaml")["actions"]
        detect_action = actions["detect_vehicle_model_config_entry"]
        click_action = actions["click_vehicle_model_config_entry"]
        count_action = actions["detect_same_source_vehicle_count"]
        branching = state["result_list_branching"]

        self.assertEqual(
            state["allowed_actions"],
            ["detect_vehicle_model_config_entry", "click_vehicle_model_config_entry", "detect_same_source_vehicle_count"],
        )
        self.assertEqual(state["action_authorization"]["detect_vehicle_model_config_entry"], "current_round_read_only_allowed")
        self.assertEqual(state["action_authorization"]["click_vehicle_model_config_entry"], "requires_next_round_explicit_authorization")
        self.assertEqual(state["action_authorization"]["detect_same_source_vehicle_count"], "current_round_read_only_allowed")
        self.assertIn("click_generic_filter", state["forbidden_actions"])
        self.assertIn("click_filter", state["forbidden_actions"])
        self.assertIn("click_color_filter", state["forbidden_actions"])
        self.assertIn("click_year_filter", state["forbidden_actions"])
        self.assertIn("click_comprehensive_sort", state["forbidden_actions"])
        self.assertIn("click_price_low_to_high", state["forbidden_actions"])
        self.assertIn("click_vehicle_card", state["forbidden_actions"])
        self.assertIn("collect_vehicle_data", state["forbidden_actions"])
        self.assertIn("read_vehicle_price", state["forbidden_actions"])
        self.assertIn("read_vehicle_year", state["forbidden_actions"])
        self.assertEqual(detect_action["type"], "read_only_detect_text")
        self.assertEqual(detect_action["text"], "\u8f66\u578b\u914d\u7f6e")
        self.assertIn("generic_filter", detect_action["forbidden_targets"])
        self.assertEqual(click_action["text"], "\u8f66\u578b\u914d\u7f6e")
        self.assertTrue(click_action["requires_explicit_user_authorization"])
        self.assertIn("filter", click_action["forbidden_targets"])
        self.assertEqual(count_action["type"], "read_only_count_vehicle_cards")
        self.assertEqual(count_action["on_uncertain"], "COUNT_UNCERTAIN")
        self.assertEqual(branching["read_only_gate_action"], "detect_same_source_vehicle_count")
        self.assertEqual(branching["count_uncertain_exception"], "COUNT_UNCERTAIN")
        self.assertEqual(branching["single_vehicle_branch"]["trigger_condition"], "same_source_vehicle_count_eq_1")
        self.assertEqual(branching["single_vehicle_branch"]["detail_state"], "S07_SINGLE_RESULT_DETAIL_PENDING")
        self.assertEqual(branching["multi_vehicle_branch"]["trigger_condition"], "same_source_vehicle_count_gt_1")
        self.assertEqual(branching["multi_vehicle_branch"]["sort_state"], "S07_MULTI_RESULT_SORT_PENDING")

    def test_s07_same_source_result_list_requires_reliable_source_gate(self):
        actions = load_config("actions.yaml")["actions"]
        count_action = actions["detect_same_source_vehicle_count"]
        states = [
            self.machine.get_page("S07_VEHICLE_LIST_PAGE"),
            self.machine.get_page("S07_DISCOVERY_RESULT_MIXED_PAGE"),
        ]

        for state in states:
            source_gate = state["same_source_result_source_gate"]
            self.assertTrue(source_gate["source_gate_is_only_gate"])
            self.assertTrue(source_gate["requires_strong_source_evidence"])
            self.assertEqual(
                source_gate["reliable_sources"],
                ["from_s08_view_result", "from_vehicle_detail_back"],
            )
            self.assertEqual(
                source_gate["reliable_source_evidence_requirements"]["from_s08_view_result"],
                [
                    "stable_s08_vehicle_model_config_panel_before_list_entry",
                    "selected_color_confirmed_in_panel",
                    "age_gate_passed",
                    "view_result_clicked_once",
                    "entered_list_after_view_result",
                ],
            )
            self.assertEqual(
                source_gate["reliable_source_evidence_requirements"]["from_vehicle_detail_back"],
                [
                    "stable_vehicle_detail_page_before_back",
                    "back_action_executed",
                    "entered_list_after_back",
                ],
            )
            self.assertEqual(source_gate["unreliable_source_exception"], "RESULT_LIST_SOURCE_UNRELIABLE")
            self.assertTrue(source_gate["forbid_branching_when_source_unverified"])
            self.assertTrue(state["result_list_branching"]["requires_reliable_source_gate"])

        self.assertTrue(count_action["requires_reliable_result_list_source"])
        self.assertTrue(count_action["requires_strong_source_evidence_for_reliable_source"])
        self.assertEqual(
            count_action["reliable_sources"],
            ["from_s08_view_result", "from_vehicle_detail_back"],
        )
        self.assertEqual(count_action["on_unreliable_source"], "RESULT_LIST_SOURCE_UNRELIABLE")
        self.assertIn("S07_DISCOVERY_RESULT_MIXED_PAGE", count_action["also_allowed_in_states"])

    def test_s07_discovery_result_mixed_page_is_recognized_and_not_standard_s07(self):
        recognizer = PageRecognizer(self.pages)
        mixed_state = self.machine.get_page("S07_DISCOVERY_RESULT_MIXED_PAGE")
        standard_s07 = self.machine.get_page("S07_VEHICLE_LIST_PAGE")
        mixed_text = (
            "\u5168\u90e8 \u54c1\u724c\u9009\u8f66 AI\u9009\u8f66 \u641c\u7d22 \u5510\u5c71 "
            "\u5927\u4f17\u4e13\u533a 12383\u8f86\u5728\u552e \u7efc\u5408\u6392\u5e8f "
            "\u54c1\u724c \u4ef7\u683c \u8f66\u9f84/\u91cc\u7a0b \u7b5b\u9009 \u6e05\u7a7a "
            "\u5927\u4f17 \u5e15\u8428\u7279 2020\u6b3e 330TSI \u5c0a\u8d35\u7248 \u56fdVI "
            "\u5168\u56fd\u6dd8\u8f66"
        )

        self.assertTrue(recognizer.matches(mixed_state, mixed_text))
        self.assertFalse(recognizer.matches(standard_s07, mixed_text))

    def test_s07_discovery_result_mixed_page_allows_same_source_chain_when_source_reliable(self):
        state = self.machine.get_page("S07_DISCOVERY_RESULT_MIXED_PAGE")
        actions = load_config("actions.yaml")["actions"]
        action = actions["diagnose_discovery_result_mixed_page"]
        count_action = actions["detect_same_source_vehicle_count"]

        self.assertEqual(
            state["allowed_actions"],
            ["diagnose_discovery_result_mixed_page", "detect_same_source_vehicle_count"],
        )
        self.assertEqual(state["action_authorization"]["detect_same_source_vehicle_count"], "current_round_read_only_allowed")
        self.assertEqual(action["precondition"], "S07_DISCOVERY_RESULT_MIXED_PAGE")
        self.assertEqual(action["on_uncertain"], "RESULT_PAGE_VARIANT_UNCLASSIFIED")
        self.assertEqual(self.machine.transition("S07_DISCOVERY_RESULT_MIXED_PAGE", "same_source_vehicle_count_eq_1"), "S07_SINGLE_RESULT_DETAIL_PENDING")
        self.assertEqual(self.machine.transition("S07_DISCOVERY_RESULT_MIXED_PAGE", "same_source_vehicle_count_gt_1"), "S07_MULTI_RESULT_SORT_PENDING")
        self.assertTrue(state["same_source_result_identity"]["source_gate_is_only_gate"])
        self.assertTrue(state["same_source_result_identity"]["discovery_anchors_do_not_negate_same_source_identity_when_source_reliable"])
        self.assertEqual(
            state["same_source_result_source_gate"]["reliable_sources"],
            ["from_s08_view_result", "from_vehicle_detail_back"],
        )
        self.assertTrue(state["same_source_result_source_gate"]["source_gate_is_only_gate"])
        self.assertTrue(state["same_source_result_source_gate"]["requires_strong_source_evidence"])
        self.assertEqual(count_action["precondition"], "S07_VEHICLE_LIST_PAGE")
        self.assertIn("S07_DISCOVERY_RESULT_MIXED_PAGE", count_action["also_allowed_in_states"])
        self.assertIn("tap_sort_if_present", state["forbidden_actions"])
        self.assertIn("tap_price_low_to_high", state["forbidden_actions"])
        self.assertIn("tap_single_car_if_no_sort", state["forbidden_actions"])
        self.assertIn("click_vehicle_card", state["forbidden_actions"])
        self.assertNotIn("detect_same_source_vehicle_count", state["forbidden_actions"])

    def test_s07_single_result_detail_pending_only_allows_direct_detail(self):
        state = self.machine.get_page("S07_SINGLE_RESULT_DETAIL_PENDING")

        self.assertEqual(state["allowed_actions"], ["tap_single_car_if_no_sort"])
        self.assertEqual(self.machine.transition("S07_SINGLE_RESULT_DETAIL_PENDING", "vehicle_detail_page_detected"), "S11")
        self.assertIn("tap_sort_if_present", state["forbidden_actions"])
        self.assertIn("tap_price_low_to_high", state["forbidden_actions"])

    def test_s07_multi_result_sort_pending_only_allows_sort(self):
        state = self.machine.get_page("S07_MULTI_RESULT_SORT_PENDING")

        self.assertEqual(state["allowed_actions"], ["tap_sort_if_present"])
        self.assertEqual(self.machine.transition("S07_MULTI_RESULT_SORT_PENDING", "sort_present"), "S09")
        self.assertIn("tap_single_car_if_no_sort", state["forbidden_actions"])
        self.assertIn("tap_price_low_to_high", state["forbidden_actions"])

    def test_s08_model_config_panel_is_read_only_until_next_authorization(self):
        panel = self.machine.get_page("S08_VEHICLE_MODEL_CONFIG_PANEL")
        page = self.machine.get_page("S08_VEHICLE_MODEL_CONFIG_PAGE")
        actions = load_config("actions.yaml")["actions"]
        action = actions["read_vehicle_model_config_panel_contract"]
        color_action = actions["click_color_entry"]

        self.assertEqual(panel["allowed_actions"], ["read_vehicle_model_config_panel_contract", "click_color_entry"])
        self.assertEqual(page["allowed_actions"], ["read_vehicle_model_config_panel_contract", "click_color_entry"])
        self.assertEqual(panel["action_authorization"]["read_vehicle_model_config_panel_contract"], "current_round_read_only_allowed")
        self.assertEqual(panel["action_authorization"]["click_color_entry"], "requires_explicit_user_authorization_for_color_entry_only")
        self.assertEqual(action["type"], "read_only_panel_contract")
        self.assertEqual(action["precondition"], "S08_VEHICLE_MODEL_CONFIG_PANEL")
        self.assertEqual(color_action["precondition"], "S08_VEHICLE_MODEL_CONFIG_PANEL")
        self.assertEqual(color_action["text"], "\u989c\u8272")
        self.assertTrue(color_action["requires_explicit_user_authorization"])
        self.assertIn("target_color_option", color_action["forbidden_targets"])
        self.assertIn("similar_color_option", color_action["forbidden_targets"])
        self.assertIn("confirm_button", color_action["forbidden_targets"])
        self.assertIn("color_filter", action["forbidden_targets"])
        self.assertIn("year_filter", action["forbidden_targets"])
        self.assertIn("confirm_button", action["forbidden_targets"])
        self.assertIn("reset_button", action["forbidden_targets"])
        self.assertIn("close_button", action["forbidden_targets"])
        self.assertIn("vehicle_data", action["forbidden_targets"])
        self.assertIn("click_color_filter", panel["forbidden_actions"])
        self.assertIn("click_year_filter", panel["forbidden_actions"])
        self.assertIn("click_confirm", panel["forbidden_actions"])
        self.assertIn("click_reset", panel["forbidden_actions"])
        self.assertIn("click_close", panel["forbidden_actions"])
        self.assertIn("collect_vehicle_data", panel["forbidden_actions"])
        self.assertIn("read_vehicle_price", panel["forbidden_actions"])
        self.assertIn("read_vehicle_mileage", panel["forbidden_actions"])
        self.assertTrue(panel["recognition"]["forbid_read_vehicle_fields"])
        self.assertEqual(panel["exception"], "VEHICLE_MODEL_CONFIG_PANEL_NOT_RECOGNIZED")

    def test_s08_color_selection_panel_is_read_only_and_strict_color(self):
        state = self.machine.get_page("S08_COLOR_SELECTION_PANEL")
        actions = load_config("actions.yaml")["actions"]
        action = actions["read_color_selection_panel_contract"]
        click_action = actions["click_target_color_option"]

        self.assertEqual(state["allowed_actions"], ["read_color_selection_panel_contract", "click_target_color_option"])
        self.assertEqual(action["precondition"], "S08_COLOR_SELECTION_PANEL")
        self.assertEqual(action["type"], "read_only_panel_contract")
        self.assertEqual(click_action["precondition"], "S08_COLOR_SELECTION_PANEL")
        self.assertEqual(click_action["source"], "target_color")
        self.assertTrue(click_action["exact_match_required"])
        self.assertTrue(click_action["requires_explicit_user_authorization"])
        self.assertIn("target_color_option", action["forbidden_targets"])
        self.assertIn("similar_color_option", action["forbidden_targets"])
        self.assertNotIn("click_target_color", state["forbidden_actions"])
        self.assertIn("click_similar_color", state["forbidden_actions"])
        self.assertIn("click_pearl_white", state["forbidden_actions"])
        self.assertIn("click_off_white", state["forbidden_actions"])
        self.assertIn("click_non_target_color", state["forbidden_actions"])
        self.assertIn("auto_merge_color_family", state["forbidden_actions"])
        self.assertIn("fuzzy_match_color", state["forbidden_actions"])
        self.assertIn("click_confirm", state["forbidden_actions"])
        self.assertIn("collect_vehicle_data", state["forbidden_actions"])
        self.assertTrue(state["recognition"]["forbid_read_vehicle_fields"])

    def test_s08_color_selected_blocks_downstream_until_authorized(self):
        state = self.machine.get_page("S08_COLOR_SELECTED")
        action = load_config("actions.yaml")["actions"]["click_year_or_age_entry"]

        self.assertEqual(state["allowed_actions"], ["click_year_or_age_entry"])
        self.assertEqual(state["action_authorization"]["click_year_or_age_entry"], "requires_explicit_user_authorization_for_year_or_age_entry_only")
        self.assertEqual(action["precondition"], "S08_COLOR_SELECTED")
        self.assertTrue(state["recognition"]["selected_color_confirmed_in_panel"])
        self.assertTrue(action["requires_selected_color_confirmed_in_panel"])
        self.assertIn("selected_color_ui_confirmed", action["required_context_flags"])
        self.assertIn("selected_color_confirmed_in_panel", action["required_context_flags"])
        self.assertIn("selected_color_visible_in_panel", action["required_context_flags"])
        self.assertIn("S08_COLOR_SELECTED_SINGLE_TARGET", action["also_allowed_in_states"])
        self.assertEqual(action["target_role"], "year_or_age_entry")
        self.assertTrue(action["requires_explicit_user_authorization"])
        self.assertIn("unlimited_age", action["forbidden_targets"])
        self.assertIn("age_option", action["forbidden_targets"])
        self.assertIn("continue_with_old_color", state["forbidden_actions"])
        self.assertIn("click_year_filter", state["forbidden_actions"])
        self.assertIn("click_confirm", state["forbidden_actions"])
        self.assertIn("click_vehicle_card", state["forbidden_actions"])
        self.assertIn("collect_vehicle_data", state["forbidden_actions"])
        self.assertTrue(state["recognition"]["forbid_read_vehicle_fields"])

    def test_s08_stale_color_state_only_allows_read_and_learning_actions(self):
        state = self.machine.get_page("S08_COLOR_STALE_AFTER_TASK_CHANGE")
        actions = load_config("actions.yaml")["actions"]

        self.assertEqual(
            state["allowed_actions"],
            [
                "read_current_selected_color",
                "detect_target_color_entry",
                "record_issue",
                "lookup_knowledge_base",
            ],
        )
        self.assertTrue(state["recognition"]["selected_color_must_not_equal_task_color"])
        self.assertEqual(actions["read_current_selected_color"]["precondition"], "S08_COLOR_STALE_AFTER_TASK_CHANGE")
        self.assertEqual(actions["detect_target_color_entry"]["precondition"], "S08_COLOR_STALE_AFTER_TASK_CHANGE")
        self.assertIn("continue_to_age_slider", state["forbidden_actions"])
        self.assertIn("click_confirm", state["forbidden_actions"])
        self.assertIn("click_view_result", state["forbidden_actions"])
        self.assertIn("enter_vehicle_list", state["forbidden_actions"])
        self.assertIn("collect_vehicle_data", state["forbidden_actions"])
        self.assertIn("continue_with_old_color", state["forbidden_actions"])
        self.assertIn("skip_color_revalidation", state["forbidden_actions"])
        self.assertIn("target_color_option", actions["read_current_selected_color"]["forbidden_targets"])
        self.assertIn("year_or_age_entry", actions["detect_target_color_entry"]["forbidden_targets"])

    def test_s08_color_multi_selected_only_allows_stale_color_cancel(self):
        state = self.machine.get_page("S08_COLOR_MULTI_SELECTED")
        actions = load_config("actions.yaml")["actions"]

        self.assertEqual(state["allowed_actions"], ["read_selected_colors", "cancel_stale_selected_color"])
        self.assertTrue(state["recognition"]["selected_colors_count_greater_than_one"])
        self.assertTrue(state["recognition"]["selected_colors_must_include_task_color"])
        self.assertTrue(state["recognition"]["selected_colors_must_include_stale_color"])
        self.assertEqual(actions["read_selected_colors"]["precondition"], "S08_COLOR_MULTI_SELECTED")
        self.assertEqual(actions["cancel_stale_selected_color"]["precondition"], "S08_COLOR_MULTI_SELECTED")
        self.assertEqual(actions["cancel_stale_selected_color"]["source"], "stale_color")
        self.assertEqual(actions["cancel_stale_selected_color"]["target_role"], "stale_selected_color_option")
        self.assertIn("target_color_option", actions["cancel_stale_selected_color"]["forbidden_targets"])
        self.assertIn("continue_to_age_slider", state["forbidden_actions"])
        self.assertIn("click_confirm", state["forbidden_actions"])
        self.assertIn("enter_vehicle_list", state["forbidden_actions"])
        self.assertIn("collect_vehicle_data", state["forbidden_actions"])

    def test_s08_color_selected_single_target_allows_later_age_only(self):
        state = self.machine.get_page("S08_COLOR_SELECTED_SINGLE_TARGET")
        action = load_config("actions.yaml")["actions"]["click_year_or_age_entry"]

        self.assertEqual(state["allowed_actions"], ["click_year_or_age_entry"])
        self.assertTrue(state["recognition"]["selected_colors_must_equal_task_color_only"])
        self.assertTrue(state["recognition"]["selected_color_confirmed_in_panel"])
        self.assertTrue(action["requires_selected_color_confirmed_in_panel"])
        self.assertIn("selected_color_ui_confirmed", action["required_context_flags"])
        self.assertIn("selected_color_confirmed_in_panel", action["required_context_flags"])
        self.assertIn("selected_color_visible_in_panel", action["required_context_flags"])
        self.assertIn("continue_with_multiple_colors", state["forbidden_actions"])
        self.assertIn("click_confirm", state["forbidden_actions"])
        self.assertIn("enter_vehicle_list", state["forbidden_actions"])
        self.assertIn("collect_vehicle_data", state["forbidden_actions"])

    def test_s08_year_selection_panel_requires_left_age_tab_first(self):
        state = self.machine.get_page("S08_YEAR_SELECTION_PANEL")
        actions = load_config("actions.yaml")["actions"]
        detect_action = actions["detect_left_age_tab"]
        click_action = actions["click_left_age_tab"]

        self.assertEqual(state["allowed_actions"], ["detect_left_age_tab", "click_left_age_tab"])
        self.assertEqual(state["action_authorization"]["detect_left_age_tab"], "current_round_read_only_allowed")
        self.assertEqual(state["action_authorization"]["click_left_age_tab"], "requires_next_round_explicit_authorization")
        self.assertTrue(state["recognition"]["requires_selected_color_confirmed_in_panel_before_entry"])
        self.assertEqual(detect_action["precondition"], "S08_YEAR_SELECTION_PANEL")
        self.assertEqual(click_action["precondition"], "S08_YEAR_SELECTION_PANEL")
        self.assertEqual(detect_action["text"], "车龄")
        self.assertEqual(click_action["text"], "车龄")
        self.assertIn("unlimited_age", detect_action["forbidden_targets"])
        self.assertIn("age_option", detect_action["forbidden_targets"])
        self.assertIn("click_unlimited_age", state["forbidden_actions"])
        self.assertIn("click_age_option", state["forbidden_actions"])
        self.assertIn("drag_slider", state["forbidden_actions"])
        self.assertIn("click_confirm", state["forbidden_actions"])
        self.assertIn("collect_vehicle_data", state["forbidden_actions"])
        self.assertTrue(state["recognition"]["forbid_read_vehicle_fields"])

    def test_s08_age_exact_slider_panel_only_allows_exact_slider_logic(self):
        state = self.machine.get_page("S08_AGE_EXACT_SLIDER_PANEL")
        actions = load_config("actions.yaml")["actions"]

        self.assertEqual(
            state["allowed_actions"],
            [
                "detect_age_exact_slider",
                "detect_age_slider_handles",
                "detect_right_age_handle_visual_center",
                "read_left_age_handle_value",
                "read_right_age_handle_value",
                "read_age_slider_bounds",
                "calculate_target_age",
                "calculate_age_handle_overlap_target",
                "validate_age_handle_physical_overlap",
                "validate_exact_age_range",
                "set_age_slider_exact_value",
                "set_left_age_handle_to_target",
                "set_right_age_handle_to_target",
            ],
        )
        self.assertEqual(state["action_authorization"]["set_age_slider_exact_value"], "requires_next_round_explicit_authorization")
        self.assertEqual(actions["detect_age_exact_slider"]["precondition"], "S08_AGE_EXACT_SLIDER_PANEL")
        self.assertEqual(actions["set_age_slider_exact_value"]["precondition"], "S08_AGE_EXACT_SLIDER_PANEL")
        self.assertEqual(actions["set_right_age_handle_to_target"]["precondition"], "S08_AGE_EXACT_SLIDER_PANEL")
        exact_state = self.machine.get_page("S08_AGE_EXACT_VALUE_SELECTED")
        self.assertTrue(exact_state["recognition"]["left_handle_equals_target_age"])
        self.assertTrue(exact_state["recognition"]["right_handle_equals_target_age"])
        self.assertTrue(exact_state["recognition"]["left_and_right_handle_physical_overlap_at_target_tick"])
        self.assertTrue(exact_state["recognition"]["target_age_calculation_verified"])
        self.assertIn("click_unlimited_age", state["forbidden_actions"])
        self.assertIn("click_age_option", state["forbidden_actions"])
        self.assertIn("set_age_range", state["forbidden_actions"])
        self.assertIn("expand_age_range", state["forbidden_actions"])
        self.assertIn("skip_right_handle_verification", state["forbidden_actions"])
        self.assertIn("treat_range_as_exact", state["forbidden_actions"])
        self.assertIn("click_confirm", state["forbidden_actions"])
        self.assertIn("collect_vehicle_data", state["forbidden_actions"])

if __name__ == "__main__":
    unittest.main()
