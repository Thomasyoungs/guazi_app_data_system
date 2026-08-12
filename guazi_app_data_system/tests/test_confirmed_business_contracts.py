import unittest

from guazi_app_data_system.config_loader import load_config


class ConfirmedBusinessContractsTest(unittest.TestCase):
    def test_sample_too_small_message_is_exact(self):
        fields = load_config("fields.yaml")
        exceptions = load_config("exceptions.yaml")
        message = "三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。"

        self.assertEqual(fields["same_source_policy"]["sample_too_small_message"], message)
        self.assertEqual(exceptions["exceptions"]["SAMPLE_TOO_SMALL"]["message"], message)

    def test_screenshot_policy_allows_only_required_and_exception_screenshots(self):
        system = load_config("system.yaml")
        policy = system["screenshot_policy"]

        self.assertTrue(policy["allow_required_flow_screenshots"])
        self.assertTrue(policy["allow_exception_screenshots"])
        self.assertFalse(policy["allow_unrelated_page_screenshots"])
        self.assertEqual(policy["usage"], ["page_recognition", "exception_review", "knowledge_base_learning"])

    def test_runtime_source_contract(self):
        system = load_config("system.yaml")
        runtime = system["runtime"]

        self.assertEqual(runtime["official_input_source"], "feishu_api")
        self.assertEqual(runtime["temporary_real_task_source"], "feishu_export")
        self.assertEqual(runtime["mock_input_source"], "mock")
        self.assertEqual(runtime["current_task_path"], "input/current_target_task.json")
        self.assertEqual(runtime["local_json_usage"], "simulation_and_offline_regression_only")
        self.assertFalse(runtime["mock_allows_real_device_operation"])
        self.assertTrue(runtime["feishu_export_allows_real_device_operation"])
        self.assertFalse(runtime["allow_contacts_or_seller_actions"])

    def test_target_app_identity_contract(self):
        target = load_config("system.yaml")["target_app"]

        self.assertEqual(target["required_display_name"], "瓜子二手车")
        self.assertTrue(target["require_exact_label_match"])
        self.assertTrue(target["require_single_verified_match"])
        self.assertFalse(target["allow_keyword_guess"])
        self.assertFalse(target["allow_manual_foreground_reverse_lookup"])
        self.assertTrue(target["auto_wake_device_before_launch"])
        self.assertIn("com.guazi.android.chesupai", target["excluded_packages"])

    def test_same_source_vehicle_list_branching_contract(self):
        pages = load_config("pages.yaml")
        actions = load_config("actions.yaml")
        exceptions = load_config("exceptions.yaml")
        s07 = next(page for page in pages["pages"] if page["id"] == "S07_VEHICLE_LIST_PAGE")
        branching = s07["result_list_branching"]

        self.assertEqual(branching["read_only_gate_action"], "detect_same_source_vehicle_count")
        self.assertEqual(branching["count_uncertain_exception"], "COUNT_UNCERTAIN")
        self.assertEqual(actions["actions"]["detect_same_source_vehicle_count"]["on_uncertain"], "COUNT_UNCERTAIN")
        self.assertEqual(branching["single_vehicle_branch"]["detail_state"], "S07_SINGLE_RESULT_DETAIL_PENDING")
        self.assertEqual(branching["multi_vehicle_branch"]["sort_state"], "S07_MULTI_RESULT_SORT_PENDING")
        self.assertIn("COUNT_UNCERTAIN", exceptions["exceptions"])

    def test_same_source_vehicle_list_source_gate_contract(self):
        pages = load_config("pages.yaml")
        actions = load_config("actions.yaml")
        exceptions = load_config("exceptions.yaml")
        count_action = actions["actions"]["detect_same_source_vehicle_count"]
        states = [
            next(page for page in pages["pages"] if page["id"] == "S07_VEHICLE_LIST_PAGE"),
            next(page for page in pages["pages"] if page["id"] == "S07_DISCOVERY_RESULT_MIXED_PAGE"),
        ]

        for state in states:
            source_gate = state["same_source_result_source_gate"]
            self.assertTrue(source_gate["source_gate_is_only_gate"])
            self.assertTrue(source_gate["requires_strong_source_evidence"])
            self.assertEqual(
                source_gate["reliable_sources"],
                ["from_s08_view_result", "from_vehicle_detail_back"],
            )
            self.assertEqual(source_gate["unreliable_source_exception"], "RESULT_LIST_SOURCE_UNRELIABLE")
            self.assertTrue(state["result_list_branching"]["requires_reliable_source_gate"])

        self.assertTrue(count_action["requires_reliable_result_list_source"])
        self.assertTrue(count_action["requires_strong_source_evidence_for_reliable_source"])
        self.assertEqual(
            count_action["reliable_sources"],
            ["from_s08_view_result", "from_vehicle_detail_back"],
        )
        self.assertEqual(count_action["on_unreliable_source"], "RESULT_LIST_SOURCE_UNRELIABLE")
        self.assertIn("S07_DISCOVERY_RESULT_MIXED_PAGE", count_action["also_allowed_in_states"])
        self.assertIn("RESULT_LIST_SOURCE_UNRELIABLE", exceptions["exceptions"])

    def test_s07_discovery_result_mixed_page_contract_allows_reliable_source_gate_branching(self):
        pages = load_config("pages.yaml")
        actions = load_config("actions.yaml")
        exceptions = load_config("exceptions.yaml")
        mixed = next(page for page in pages["pages"] if page["id"] == "S07_DISCOVERY_RESULT_MIXED_PAGE")

        self.assertEqual(
            mixed["allowed_actions"],
            ["diagnose_discovery_result_mixed_page", "detect_same_source_vehicle_count"],
        )
        self.assertTrue(mixed["same_source_result_identity"]["source_gate_is_only_gate"])
        self.assertTrue(
            mixed["same_source_result_identity"]["discovery_anchors_do_not_negate_same_source_identity_when_source_reliable"]
        )
        self.assertTrue(mixed["same_source_result_source_gate"]["source_gate_is_only_gate"])
        self.assertTrue(mixed["same_source_result_source_gate"]["requires_strong_source_evidence"])
        self.assertEqual(
            mixed["result_list_branching"]["read_only_gate_action"],
            "detect_same_source_vehicle_count",
        )
        self.assertEqual(
            mixed["result_list_branching"]["single_vehicle_branch"]["detail_state"],
            "S07_SINGLE_RESULT_DETAIL_PENDING",
        )
        self.assertEqual(
            mixed["result_list_branching"]["multi_vehicle_branch"]["sort_state"],
            "S07_MULTI_RESULT_SORT_PENDING",
        )
        self.assertIn("tap_sort_if_present", mixed["forbidden_actions"])
        self.assertIn("tap_single_car_if_no_sort", mixed["forbidden_actions"])
        self.assertEqual(
            actions["actions"]["diagnose_discovery_result_mixed_page"]["on_uncertain"],
            "RESULT_PAGE_VARIANT_UNCLASSIFIED",
        )
        self.assertEqual(
            mixed["same_source_result_source_gate"]["unreliable_source_exception"],
            "RESULT_LIST_SOURCE_UNRELIABLE",
        )
        self.assertNotIn("detect_same_source_vehicle_count", mixed["forbidden_actions"])
        self.assertIn("RESULT_LIST_SOURCE_UNRELIABLE", exceptions["exceptions"])
        self.assertIn("RESULT_PAGE_VARIANT_UNCLASSIFIED", exceptions["exceptions"])


if __name__ == "__main__":
    unittest.main()
