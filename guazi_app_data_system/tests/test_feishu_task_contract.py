import unittest
from pathlib import Path

from guazi_app_data_system.config_loader import project_root
from guazi_app_data_system.feishu_sync import (
    FEISHU_FIELD_MAPPING,
    FeishuTaskReader,
    MockTaskReader,
    mock_reader_is_never_default,
    official_reader_requires_feishu,
)
from guazi_app_data_system.task_normalizer import (
    TaskContractError,
    colors_match_exact,
    derive_vehicle_year,
    normalize_target_task,
)


class FeishuTaskContractTest(unittest.TestCase):
    def setUp(self):
        self.fixture = project_root() / "fixtures" / "sample_target_task.json"

    def test_can_read_mock_target_task_and_marks_simulation_only(self):
        result = MockTaskReader().read_json(self.fixture)

        self.assertEqual(result.source, "mock")
        self.assertTrue(result.simulation_only)
        self.assertTrue(result.task.simulation_only)
        self.assertIn("simulation_only", " ".join(result.warnings))
        self.assertEqual(result.task.task_id, "MOCK-GZ-001")

    def test_official_flow_does_not_default_to_mock(self):
        self.assertTrue(official_reader_requires_feishu())
        self.assertTrue(mock_reader_is_never_default())
        with self.assertRaises(NotImplementedError):
            FeishuTaskReader().read_target_task("GZ-001")

    def test_registration_date_raw_is_preserved_and_vehicle_year_is_derived(self):
        task = MockTaskReader().read_json(self.fixture).task

        self.assertEqual(task.registration_date_raw, "2020.4")
        self.assertEqual(task.vehicle_year, 2020)

    def test_supported_registration_date_formats_derive_same_year(self):
        for value in ["2020.4", "2020-04", "\u0032\u0030\u0032\u0030\u5e74\u0034\u6708"]:
            self.assertEqual(derive_vehicle_year(value), 2020)

    def test_color_matching_is_exact_only(self):
        self.assertTrue(colors_match_exact("\u767d\u8272", "\u767d\u8272"))
        self.assertFalse(colors_match_exact("\u767d\u8272", "\u73cd\u73e0\u767d"))
        self.assertFalse(colors_match_exact("\u7070\u8272", "\u94f6\u7070"))
        self.assertFalse(colors_match_exact("\u91d1\u8272", "\u9999\u69df\u91d1"))

    def test_required_app_fields_block_app_flow_when_missing(self):
        for field in ["brand", "series", "model_year", "trim", "color"]:
            payload = self._valid_payload()
            payload[field] = None

            task = normalize_target_task(payload, source="mock", simulation_only=True)

            self.assertTrue(task.app_flow_blocked)
            self.assertTrue(any(field in reason for reason in task.app_flow_block_reasons))

    def test_pricing_fields_block_pricing_when_missing(self):
        for field in ["mileage_10k_km", "transfer_count", "condition_text"]:
            payload = self._valid_payload()
            payload[field] = None

            task = normalize_target_task(payload, source="mock", simulation_only=True)

            self.assertTrue(task.pricing_blocked)
            self.assertTrue(any(field in reason for reason in task.pricing_block_reasons))

    def test_missing_accident_fields_do_not_block_but_require_review(self):
        payload = self._valid_payload()
        payload["accident_count"] = None
        payload["max_accident_amount"] = None

        task = normalize_target_task(payload, source="mock", simulation_only=True)

        self.assertFalse(task.app_flow_blocked)
        self.assertFalse(task.pricing_blocked)
        self.assertTrue(task.manual_review_required)
        self.assertTrue(any("default score 4" in reason for reason in task.manual_review_reasons))
        self.assertTrue(any("default score 3" in reason for reason in task.manual_review_reasons))

    def test_task_id_cannot_be_manual_and_reference_index_is_not_target_input(self):
        payload = self._valid_payload()
        payload["task_id_source"] = "manual"
        with self.assertRaises(TaskContractError):
            normalize_target_task(payload, source="mock", simulation_only=True)

        payload = self._valid_payload()
        payload["reference_index"] = 1
        with self.assertRaises(TaskContractError):
            normalize_target_task(payload, source="mock", simulation_only=True)

    def test_app_state_machine_params_include_required_fields(self):
        task = MockTaskReader().read_json(self.fixture).task
        params = task.app_operation_params()

        for field in ["brand", "series", "model_year", "trim", "color", "vehicle_year"]:
            self.assertIn(field, params)
            self.assertIsNotNone(params[field])

    def test_feishu_mapping_includes_confirmed_fields(self):
        for field in [
            "task_id",
            "brand",
            "series",
            "model_year",
            "trim",
            "color",
            "registration_date",
            "mileage_10k_km",
            "transfer_count",
            "condition_text",
            "accident_count",
            "max_accident_amount",
        ]:
            self.assertIn(field, FEISHU_FIELD_MAPPING)
        self.assertTrue(FEISHU_FIELD_MAPPING["task_id"]["forbid_manual_input"])

    def _valid_payload(self):
        return {
            "source": "mock",
            "simulation_only": True,
            "task_id": "MOCK-GZ-001",
            "brand": "\u5927\u4f17",
            "series": "\u5e15\u8428\u7279",
            "model_year": "2020\u6b3e",
            "trim": "330TSI DSG \u5c0a\u8363\u7248",
            "color": "\u767d\u8272",
            "registration_date": "2020.4",
            "mileage_10k_km": 7.2,
            "transfer_count": 1,
            "condition_text": "\u53f3\u540e\u95e8\u94a3\u91d1\u55b7\u6f06",
            "accident_count": None,
            "max_accident_amount": None,
        }


if __name__ == "__main__":
    unittest.main()
