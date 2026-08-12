import json
import tempfile
import unittest
from pathlib import Path

from guazi_app_data_system.feishu_sync import validate_current_target_task


class CurrentTaskImportTest(unittest.TestCase):
    def test_missing_current_task_does_not_fallback_to_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_current_target_task(Path(tmp) / "input" / "current_target_task.json")

        self.assertEqual(result["status"], "CURRENT_TASK_FILE_NOT_FOUND")
        self.assertEqual(result["message"], "等待真实任务导入")
        self.assertFalse(result["sample_fallback_used"])
        self.assertFalse(result["allow_real_device_operation"])

    def test_valid_feishu_export_current_task_is_verified(self):
        result = self._validate(self._valid_payload())

        self.assertEqual(result["status"], "TASK_IMPORT_VERIFIED")
        self.assertEqual(result["task_id"], "FEISHU-GZ-001")
        self.assertEqual(result["brand"], "大众")
        self.assertTrue(result["allow_real_device_operation"])
        self.assertTrue(result["next_step_allowed"])

    def test_mock_current_task_is_blocked(self):
        payload = self._valid_payload()
        payload["source"] = "mock"
        payload["simulation_only"] = True

        result = self._validate(payload)

        self.assertEqual(result["status"], "INVALID_TASK_SOURCE")
        self.assertFalse(result["allow_real_device_operation"])

    def test_simulation_only_true_is_blocked(self):
        payload = self._valid_payload()
        payload["simulation_only"] = True

        result = self._validate(payload)

        self.assertEqual(result["status"], "SIMULATION_TASK_NOT_ALLOWED_FOR_REAL_DEVICE")
        self.assertFalse(result["allow_real_device_operation"])

    def test_task_id_missing_is_blocked(self):
        payload = self._valid_payload()
        payload["task_id"] = None

        result = self._validate(payload)

        self.assertEqual(result["status"], "TASK_ID_MISSING")
        self.assertFalse(result["allow_real_device_operation"])

    def test_reference_index_is_blocked(self):
        payload = self._valid_payload()
        payload["reference_index"] = 1

        result = self._validate(payload)

        self.assertEqual(result["status"], "REFERENCE_INDEX_FORBIDDEN")
        self.assertFalse(result["allow_real_device_operation"])

    def test_brand_missing_is_blocked(self):
        payload = self._valid_payload()
        payload["brand"] = None

        result = self._validate(payload)

        self.assertEqual(result["status"], "REQUIRED_FIELD_MISSING")
        self.assertIn("brand", result["missing_fields"])
        self.assertFalse(result["allow_real_device_operation"])

    def test_vehicle_year_is_derived_from_registration_date(self):
        result = self._validate(self._valid_payload())

        self.assertEqual(result["registration_date_raw"], "2020.4")
        self.assertEqual(result["vehicle_year"], 2020)

    def test_app_operation_params_are_output(self):
        result = self._validate(self._valid_payload())

        self.assertEqual(result["app_operation_params"]["brand"], "大众")
        self.assertEqual(result["app_operation_params"]["vehicle_year"], 2020)
        self.assertEqual(result["app_operation_params"]["color"], "黑色")

    def test_allow_real_device_operation_true_is_required_for_next_step(self):
        result = self._validate(self._valid_payload())

        self.assertTrue(result["allow_real_device_operation"])
        self.assertTrue(result["next_step_allowed"])

    def test_actual_current_target_task_is_black_and_verified(self):
        root = Path(__file__).resolve().parents[1]
        result = validate_current_target_task(root / "input" / "current_target_task.json")

        self.assertEqual(result["status"], "TASK_IMPORT_VERIFIED")
        self.assertTrue(result["allow_real_device_operation"])
        self.assertEqual(result["app_operation_params"]["color"], "黑色")
        self.assertEqual(result["app_operation_params"]["trim"], "330TSI 尊贵版 国VI")
        self.assertNotEqual(result["app_operation_params"]["color"], "白色")

    def test_vehicle_year_parse_failure_is_reported(self):
        payload = self._valid_payload()
        payload["registration_date"] = "not-a-date"

        result = self._validate(payload)

        self.assertEqual(result["status"], "VEHICLE_YEAR_PARSE_FAILED")
        self.assertFalse(result["allow_real_device_operation"])

    def _validate(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input" / "current_target_task.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return validate_current_target_task(path)

    def _valid_payload(self):
        return {
            "source": "feishu_export",
            "simulation_only": False,
            "task_id": "FEISHU-GZ-001",
            "brand": "大众",
            "series": "帕萨特",
            "model_year": "2020款",
            "trim": "330TSI 尊贵版 国VI",
            "color": "黑色",
            "registration_date": "2020.4",
            "mileage_10k_km": 7.2,
            "transfer_count": 1,
            "condition_text": "右后门钣金喷漆",
            "accident_count": None,
            "max_accident_amount": None,
        }


if __name__ == "__main__":
    unittest.main()
