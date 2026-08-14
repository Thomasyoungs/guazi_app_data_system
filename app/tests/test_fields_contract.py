import unittest

from guazi_app_data_system.config_loader import load_config
from guazi_app_data_system.data_collection import DataCollector
from guazi_app_data_system.field_validation import FieldContract
from guazi_app_data_system.models import ReferenceCar
from guazi_app_data_system.pricing import score_reference, score_target


class FieldContractTest(unittest.TestCase):
    def setUp(self):
        self.fields = load_config("fields.yaml")

    def test_target_contract_and_missing_accident_defaults(self):
        collector = DataCollector(self.fields)
        target = collector.simulated_target()
        contract = FieldContract(self.fields)

        self.assertEqual(contract.validate_target(target.to_dict()), [])
        score = score_target(target, self.fields, current_year=2026)

        self.assertEqual(score.score, 92.5)
        self.assertIn("目标车缺少出险次数，已采用默认分。", score.review_reasons)
        self.assertIn("目标车缺少最大金额，已采用默认分。", score.review_reasons)

    def test_forbidden_fields_are_rejected(self):
        contract = FieldContract(self.fields)
        errors = contract.validate_target({"task_id": "1", "city": "北京"})

        self.assertTrue(any("forbidden field present: city" == item for item in errors))

    def test_confirmed_source_registration_and_color_policy(self):
        target_fields = self.fields["target_fields"]
        color_policy = self.fields["color_policy"]
        same_source = self.fields["same_source_policy"]

        self.assertEqual(target_fields["source"], "feishu_api")
        self.assertEqual(target_fields["temporary_export_source"], "feishu_export")
        self.assertEqual(target_fields["mock_source"], "mock")
        self.assertEqual(target_fields["local_json_usage"], "simulation_and_offline_regression_only")
        self.assertFalse(target_fields["mock_allows_real_device_operation"])
        self.assertTrue(target_fields["feishu_export_allows_real_device_operation"])
        self.assertTrue(target_fields["registration_date_policy"]["preserve_raw_month_value"])
        self.assertTrue(target_fields["registration_date_policy"]["derive_same_source_year"])
        self.assertEqual(target_fields["registration_date_policy"]["normalize_to"], "YYYY.MM")
        self.assertIn("22.8", target_fields["registration_date_policy"]["accepted_input_formats"])
        self.assertIn("registration_date_year", target_fields["registration_date_policy"]["required_internal_fields"])
        self.assertEqual(color_policy["same_source_match"], "exact_only")
        self.assertFalse(color_policy["auto_merge_color_family"])
        self.assertTrue(same_source["allow_narrowest_filter_then_list_second_pass"])
        self.assertTrue(same_source["forbid_expanded_year_range_as_same_source"])

    def test_reference_missing_accident_fields_have_confirmed_defaults(self):
        reference_fields = self.fields["reference_fields"]
        scoring = self.fields["scoring"]

        self.assertTrue(reference_fields["same_source_pool_only"])
        self.assertEqual(reference_fields["missing_accident_fields_policy"]["accident_count_default_score"], 4)
        self.assertEqual(reference_fields["missing_accident_fields_policy"]["max_accident_amount_default_score"], 3)
        self.assertEqual(scoring["missing_reference_accident_score"], 4)
        self.assertEqual(scoring["missing_reference_amount_score"], 3)
        self.assertIn("目标车或参考车事故字段缺失", reference_fields["missing_accident_fields_policy"]["review_message"])

    def test_forbidden_capture_scope_is_extended(self):
        forbidden = set(self.fields["forbidden_fields"])

        for field in ["city", "seller", "phone", "loan", "browse_count", "favorite_count", "user_reviews", "recommend_tags", "image_count", "ai_explain", "ad_content"]:
            self.assertIn(field, forbidden)

    def test_reference_selection_policy(self):
        policy = self.fields["reference_selection"]

        self.assertTrue(policy["must_come_from_same_source_pool"])
        self.assertTrue(policy["valid_price_required"])
        self.assertEqual(
            policy["reference_selection_rule"],
            "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        )
        self.assertEqual(
            policy["early_exit_rule_id"],
            "V33_S14_LOW_SCORE_UPPER_BOUND_SKIP_AND_RECOLLECT_CONTRACT",
        )
        self.assertTrue(policy["ordered_by_reference_index_low_to_high"])
        self.assertTrue(policy["boundary_reference_required_for_auto_pricing"])
        self.assertFalse(policy["equal_score_boundary_is_final_reference"])
        self.assertTrue(policy["above_score_boundary_uses_previous_low_reference"])
        self.assertEqual(policy["no_boundary_manual_review_reason"], "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING")
        self.assertEqual(policy["first_boundary_no_previous_manual_review_reason"], "FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE")
        self.assertTrue(policy["sample_below_three_warn_only"])
        self.assertFalse(policy["competition_coefficient_affects_s15"])
        self.assertTrue(policy["forbid_out_of_flow_reference"])

    def test_reference_missing_accident_fields_score_with_defaults(self):
        reference = ReferenceCar(
            reference_index=1,
            list_price_10k=5.0,
            list_year=2020,
            list_mileage_10k_km=6.0,
            transfer_count=0,
            accident_count=None,
            max_accident_amount=None,
            repair_counts={"驾驶室": 0, "车尾": 0, "副驾驶": 0, "车头": 0},
        )

        score = score_reference(reference, self.fields, current_year=2026)

        self.assertEqual(score.components["accident_score"], 4)
        self.assertEqual(score.components["max_amount_score"], 3)
        self.assertIn("REFERENCE_ACCIDENT_COUNT_MISSING_FIELD_INCOMPLETE", score.review_reasons)
        self.assertIn("REFERENCE_MAX_AMOUNT_MISSING_FIELD_INCOMPLETE", score.review_reasons)


if __name__ == "__main__":
    unittest.main()
