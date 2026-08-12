import tempfile
import unittest
from pathlib import Path

from guazi_app_data_system.action_executor import ActionExecutor
from guazi_app_data_system.audit import AuditLogger
from guazi_app_data_system.config_loader import load_config, project_root
from guazi_app_data_system.exception_handler import GuaziFlowError, IssueRecorder
from guazi_app_data_system.feishu_sync import FeishuExportTaskReader, MockTaskReader
from guazi_app_data_system.page_state_machine import PageStateMachine
from guazi_app_data_system.task_normalizer import (
    TaskContractError,
    brand_entry_gate,
    normalize_target_task,
    real_device_operation_allowed,
)


class TaskSourceGateTest(unittest.TestCase):
    def setUp(self):
        self.root = project_root()
        self.mock_fixture = self.root / "fixtures" / "sample_target_task.json"
        self.export_fixture = self.root / "fixtures" / "sample_feishu_export_task.json"

    def test_mock_standardizes_but_cannot_drive_real_device(self):
        task = MockTaskReader().read_json(self.mock_fixture).task

        self.assertEqual(task.source, "mock")
        self.assertFalse(task.allow_real_device_operation)
        self.assertFalse(real_device_operation_allowed(task))
        self.assertFalse(brand_entry_gate(task)["allowed"])

    def test_mock_task_is_blocked_by_real_device_brand_gate(self):
        task = MockTaskReader().read_json(self.mock_fixture).task

        with self.assertRaises(GuaziFlowError):
            self._executor().execute(
                "S02_SELECT_CAR_TAB",
                "click_brand_entry",
                {"target_task": task, "enforce_task_gate": True},
            )

    def test_feishu_export_standardizes_and_allows_real_device_operation(self):
        task = FeishuExportTaskReader().read_json(self.export_fixture).task

        self.assertEqual(task.source, "feishu_export")
        self.assertFalse(task.simulation_only)
        self.assertTrue(task.allow_real_device_operation)
        self.assertTrue(real_device_operation_allowed(task))
        self.assertTrue(brand_entry_gate(task)["allowed"])
        self.assertEqual(Path(task.source_import_path).name, "sample_feishu_export_task.json")
        self.assertIsNotNone(task.source_imported_at)

    def test_feishu_export_can_pass_action_executor_brand_gate(self):
        task = FeishuExportTaskReader().read_json(self.export_fixture).task

        result = self._executor().execute(
            "S02_SELECT_CAR_TAB",
            "click_brand_entry",
            {"target_task": task, "enforce_task_gate": True},
        )

        self.assertTrue(result["ok"])

    def test_feishu_export_missing_task_id_blocks(self):
        payload = self._valid_export_payload()
        payload["task_id"] = None

        task = normalize_target_task(payload, source="feishu_export", simulation_only=False)

        self.assertTrue(task.app_flow_blocked)
        self.assertFalse(task.allow_real_device_operation)
        self.assertFalse(brand_entry_gate(task)["allowed"])
        self.assertTrue(any("task_id" in reason for reason in task.app_flow_block_reasons))

    def test_feishu_export_reference_index_is_forbidden(self):
        payload = self._valid_export_payload()
        payload["reference_index"] = 1

        with self.assertRaises(TaskContractError):
            normalize_target_task(payload, source="feishu_export", simulation_only=False)

    def test_feishu_export_missing_brand_blocks_s03_brand_click(self):
        payload = self._valid_export_payload()
        payload["brand"] = None

        task = normalize_target_task(payload, source="feishu_export", simulation_only=False)
        gate = brand_entry_gate(task)

        self.assertTrue(task.app_flow_blocked)
        self.assertFalse(gate["allowed"])
        self.assertTrue(any("brand" in reason for reason in gate["details"]))

    def test_feishu_export_outputs_app_operation_brand(self):
        task = FeishuExportTaskReader().read_json(self.export_fixture).task

        self.assertEqual(task.app_operation_params()["brand"], "大众")

    def test_unknown_source_is_blocked(self):
        payload = self._valid_export_payload()
        payload["source"] = "spreadsheet_copy"

        with self.assertRaises(TaskContractError):
            normalize_target_task(payload)

    def test_simulation_only_true_blocks_real_device_operation(self):
        payload = self._valid_export_payload()
        payload["simulation_only"] = True

        with self.assertRaises(TaskContractError):
            normalize_target_task(payload, source="feishu_export", simulation_only=True)

    def test_no_target_car_task_blocks_brand_click(self):
        gate = brand_entry_gate(None)

        self.assertFalse(gate["allowed"])
        with self.assertRaises(GuaziFlowError):
            self._executor().execute(
                "S02_SELECT_CAR_TAB",
                "click_brand_entry",
                {"enforce_task_gate": True},
            )

    def _executor(self) -> ActionExecutor:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_path = Path(tmp.name)
        machine = PageStateMachine(load_config("pages.yaml"))
        issues = IssueRecorder(tmp_path / "issues.jsonl", load_config("exceptions.yaml"))
        audit = AuditLogger(tmp_path / "audit.jsonl")
        return ActionExecutor(machine, load_config("actions.yaml"), audit, issues, dry_run=True)

    def _valid_export_payload(self):
        return {
            "source": "feishu_export",
            "simulation_only": False,
            "task_id": "FEISHU-GZ-001",
            "brand": "大众",
            "series": "帕萨特",
            "model_year": "2020款",
            "trim": "330TSI DSG 尊荣版",
            "color": "白色",
            "registration_date": "2020.4",
            "mileage_10k_km": 7.2,
            "transfer_count": 1,
            "condition_text": "右后门钣金喷漆",
            "accident_count": None,
            "max_accident_amount": None,
        }


if __name__ == "__main__":
    unittest.main()
