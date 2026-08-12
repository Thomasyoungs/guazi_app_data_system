import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from pricing_runner import PricingRunner, diagnose_main_entry  # noqa: E402


def fixed_clock():
    return datetime(2026, 6, 9, 8, 30, tzinfo=timezone.utc)


class PricingRunnerPhase3ManualRunTest(unittest.TestCase):
    def setUp(self):
        self.adb_env_patch = patch.dict("os.environ", {"GUAZI_ADB_SERIAL": "UNITTEST_TARGET_SERIAL"}, clear=False)
        self.adb_env_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task_id = "FS20260609_0001"
        self.task_root = self.root / "data" / "feishu_tasks"
        self.task_dir = self.task_root / self.task_id
        self.data_dir = self.root / "data"
        self.runtime_lock = self.root / "runtime" / "pricing.lock"
        self.main_script = self.root / "scripts" / "全程跑通.py"
        self.first_stage_script = self.root / "scripts" / "runtime_s01_to_s10_mainline.py"
        self.second_stage_script = self.root / "scripts" / "runtime_s10_to_s16_mainline.py"
        self.result_path = self.root / "data" / "pricing_result.json"
        self.first_stage_result_path = self.root / "output" / "result_s01_to_s10.json"
        self.second_stage_result_path = self.root / "output" / "result_s10_to_s16.json"
        self.runner = PricingRunner(
            task_root=self.task_root,
            data_dir=self.data_dir,
            runtime_lock_path=self.runtime_lock,
            clock=fixed_clock,
        )
        self.create_task()
        self.write_json(self.data_dir / "current_target_task.json", {"task_id": self.task_id, "target_fingerprint": "unit-target"})
        self.main_script.parent.mkdir(parents=True, exist_ok=True)
        self.main_script.write_text("# fake main script\n", encoding="utf-8")
        self.first_stage_script.write_text("# fake first stage\n", encoding="utf-8")
        self.second_stage_script.write_text("# fake second stage\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()
        self.adb_env_patch.stop()

    def create_task(self, status="QUEUED", task_id=None):
        task_id = task_id or self.task_id
        task_dir = self.task_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(task_dir / "status.json", {"task_id": task_id, "status": status, "source": "feishu"})
        return task_dir

    def test_run_manual_without_allow_app_run_is_rejected(self):
        result = self.runner.run_manual(self.task_id, allow_app_run=False, main_script=self.main_script)

        self.assertFalse(result["ok"])
        self.assertIn("APP_RUN_CONFIRMATION_REQUIRED", result["errors"])
        self.assertFalse(self.runtime_lock.exists())

    def test_queued_task_with_allow_app_run_enters_running_and_succeeds(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_success):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "SUCCEEDED")
        audit_text = (self.task_root / "audit_log.jsonl").read_text(encoding="utf-8")
        self.assertIn("status_changed_to_running", audit_text)

    def test_disallowed_statuses_cannot_run_manual(self):
        cases = {
            "DRAFT": "TASK_NOT_QUEUED",
            "CONFIRMED": "TASK_NOT_QUEUED",
            "INVALID": "TASK_INVALID",
            "CANCELLED": "TASK_CANCELLED",
            "SUCCEEDED": "TASK_ALREADY_FINISHED",
        }
        for status, error in cases.items():
            task_id = f"FS20260609_{len(status):04d}"
            self.create_task(status=status, task_id=task_id)
            result = self.runner.run_manual(task_id, allow_app_run=True, main_script=self.main_script)
            self.assertFalse(result["ok"])
            self.assertIn(error, result["errors"])

    def test_current_target_task_missing_rejects_run(self):
        (self.data_dir / "current_target_task.json").unlink()

        result = self.runner.run_manual(self.task_id, allow_app_run=True, main_script=self.main_script)

        self.assertFalse(result["ok"])
        self.assertIn("CURRENT_TARGET_TASK_MISSING", result["errors"])

    def test_current_target_task_id_mismatch_rejects_run(self):
        self.write_json(self.data_dir / "current_target_task.json", {"task_id": "FS20260609_9999"})

        result = self.runner.run_manual(self.task_id, allow_app_run=True, main_script=self.main_script)

        self.assertFalse(result["ok"])
        self.assertIn("CURRENT_TARGET_TASK_TASK_ID_MISMATCH", result["errors"])

    def test_lock_exists_rejects_run(self):
        self.runtime_lock.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_lock.write_text("locked", encoding="utf-8")

        result = self.runner.run_manual(self.task_id, allow_app_run=True, main_script=self.main_script)

        self.assertFalse(result["ok"])
        self.assertIn("PRICING_LOCK_EXISTS", result["errors"])

    def test_main_script_missing_rejects_run_without_lock(self):
        self.main_script.unlink()

        result = self.runner.run_manual(self.task_id, allow_app_run=True, main_script=self.main_script)

        self.assertFalse(result["ok"])
        self.assertIn("MAIN_SCRIPT_NOT_FOUND", result["errors"])
        self.assertFalse(self.runtime_lock.exists())

    def test_default_candidate_detects_scripts_runtime_mainline(self):
        candidate = self.root / "scripts" / "runtime_s10_to_s16_mainline.py"
        candidate.write_text("# fake runtime mainline\n", encoding="utf-8")

        with patch("pricing_runner.PROJECT_ROOT", self.root), patch.dict("os.environ", {}, clear=True):
            resolved = self.runner._resolve_main_script(None)

        self.assertEqual(resolved, candidate)

    def test_main_script_argument_has_priority_over_default_candidate(self):
        default_candidate = self.root / "scripts" / "runtime_s10_to_s16_mainline.py"
        override = self.root / "custom_main.py"
        default_candidate.write_text("# default\n", encoding="utf-8")
        override.write_text("# override\n", encoding="utf-8")

        with patch("pricing_runner.PROJECT_ROOT", self.root), patch.dict("os.environ", {}, clear=True):
            resolved = self.runner._resolve_main_script(override)

        self.assertEqual(resolved, override)

    def test_guazi_main_script_has_priority_over_default_candidate(self):
        default_candidate = self.root / "scripts" / "runtime_s10_to_s16_mainline.py"
        env_candidate = self.root / "env_main.py"
        default_candidate.write_text("# default\n", encoding="utf-8")
        env_candidate.write_text("# env\n", encoding="utf-8")

        with patch("pricing_runner.PROJECT_ROOT", self.root), patch.dict("os.environ", {"GUAZI_MAIN_SCRIPT": str(env_candidate)}, clear=True):
            resolved = self.runner._resolve_main_script(None)

        self.assertEqual(resolved, env_candidate)

    def test_main_script_argument_has_priority_over_guazi_main_script(self):
        env_candidate = self.root / "env_main.py"
        override = self.root / "cli_main.py"
        env_candidate.write_text("# env\n", encoding="utf-8")
        override.write_text("# cli\n", encoding="utf-8")

        with patch("pricing_runner.PROJECT_ROOT", self.root), patch.dict("os.environ", {"GUAZI_MAIN_SCRIPT": str(env_candidate)}, clear=True):
            resolved = self.runner._resolve_main_script(override)

        self.assertEqual(resolved, override)

    def test_return_code_zero_without_result_sets_failed(self):
        with patch("pricing_runner.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="ok", stderr="")):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("RESULT_FILE_NOT_FOUND", result["errors"])

    def test_missing_main_script_does_not_enter_running(self):
        self.main_script.unlink()

        self.runner.run_manual(self.task_id, allow_app_run=True, main_script=self.main_script)

        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "QUEUED")

    def test_nonzero_return_code_sets_failed(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_failure_with_result):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("MAIN_SCRIPT_FAILED", result["errors"])

    def test_manual_review_result_sets_needs_review(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_manual_review):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertEqual(result["technical_status"], "SUCCEEDED")
        self.assertEqual(result["business_status"], "NEEDS_REVIEW")
        self.assertEqual(result["recommended_next_action"], "manual-review")
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["technical_status"], "SUCCEEDED")
        self.assertEqual(status["business_status"], "NEEDS_REVIEW")
        self.assertEqual(status["recommended_next_action"], "manual-review")

    def test_return_code_zero_with_blocked_status_sets_failed(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_blocked_status):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY", result["errors"])

    def test_return_code_zero_with_s14_blocked_status_sets_failed(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_s14_blocked_status):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED", result["errors"])

    def test_return_code_zero_with_contract_issue_code_sets_failed(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_contract_issue_code):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY", result["errors"])

    def test_return_code_zero_with_s10_ready_false_sets_failed(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_s10_ready_false):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY", result["errors"])

    def test_return_code_zero_without_pricing_core_fields_sets_failed(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_contract_only_result):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("RESULT_SCHEMA_INVALID_FOR_PRICING", result["errors"])

    def test_return_code_zero_with_only_pricing_core_field_sets_incomplete(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_core_field_success):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "RESULT_MISSING_REQUIRED_PRICING_FIELDS")
        self.assertIn("RESULT_MISSING_REQUIRED_PRICING_FIELDS", result["errors"])

    def test_stale_result_file_sets_failed(self):
        self.write_json(self.result_path, {"manual_review_required": False})
        old_timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(self.result_path, (old_timestamp, old_timestamp))

        with patch("pricing_runner.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="noop", stderr="")):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("STALE_RESULT_FILE", result["errors"])

    def test_pre_run_result_file_is_backed_up(self):
        output_result = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(output_result, {"manual_review_required": False, "old": True})

        with patch("pricing_runner.subprocess.run", side_effect=lambda *args, **kwargs: self.mock_success_to_path(output_result)):
            result = self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=output_result,
            )

        self.assertTrue(result["ok"])
        backup_dir = self.task_dir / "pre_run_result_backups"
        backups = [path for path in backup_dir.iterdir() if path.name != "manifest.json"]
        self.assertTrue(backups)
        self.assertTrue(any('"old": true' in path.read_text(encoding="utf-8") for path in backups))

    def test_diagnose_main_entry_does_not_call_subprocess_and_writes_output(self):
        full = self.root / "scripts" / "full_mainline.py"
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(
            'def main():\n    print("S01 S10_TO_S16 result_s10_to_s16 com.guazi 瓜子")\nif __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )
        second = self.root / "scripts" / "runtime_s10_to_s16_mainline.py"
        second.write_text("S10_READY = False\nPAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE = True\nresult_s10_to_s16 = 'x'\n", encoding="utf-8")

        with patch("pricing_runner.subprocess.run", side_effect=AssertionError("diagnose must not run subprocess")) as run:
            result = diagnose_main_entry(self.root)

        run.assert_not_called()
        self.assertTrue((self.root / "output" / "main_entry_diagnosis.json").exists())
        self.assertTrue(result["candidate_files"])

    def test_requeue_failed_moves_failed_to_queued(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "FAILED"})

        result = self.runner.requeue_failed(self.task_id)

        self.assertTrue(result["ok"])
        self.assertEqual(self.read_json(self.task_dir / "status.json")["status"], "QUEUED")
        audit_text = (self.task_root / "audit_log.jsonl").read_text(encoding="utf-8")
        self.assertIn("requeue_failed", audit_text)

    def test_requeue_failed_does_not_allow_succeeded_without_force(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "SUCCEEDED"})

        result = self.runner.requeue_failed(self.task_id)

        self.assertFalse(result["ok"])
        self.assertIn("TASK_ALREADY_FINISHED", result["errors"])
        self.assertEqual(self.read_json(self.task_dir / "status.json")["status"], "SUCCEEDED")

    def test_force_requeue_invalid_success_requires_allowed_error(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "SUCCEEDED"})
        self.write_json(self.task_dir / "runner_result.json", {"task_id": self.task_id, "errors": ["MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY"]})

        result = self.runner.requeue_failed(self.task_id, force_requeue_invalid_success=True)

        self.assertTrue(result["ok"])
        self.assertEqual(self.read_json(self.task_dir / "status.json")["status"], "QUEUED")

    def test_force_requeue_invalid_success_rejects_unlisted_error(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "SUCCEEDED"})
        self.write_json(self.task_dir / "runner_result.json", {"task_id": self.task_id, "errors": ["SOME_OTHER_ERROR"]})

        result = self.runner.requeue_failed(self.task_id, force_requeue_invalid_success=True)

        self.assertFalse(result["ok"])
        self.assertIn("FORCE_REQUEUE_ERROR_NOT_ALLOWED", result["errors"])
        self.assertEqual(self.read_json(self.task_dir / "status.json")["status"], "SUCCEEDED")

    def test_success_and_failure_release_lock_and_write_artifacts(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_success):
            self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        self.assertFalse(self.runtime_lock.exists())
        self.assertTrue((self.task_dir / "run_stdout.log").exists())
        self.assertTrue((self.task_dir / "run_stderr.log").exists())
        self.assertTrue((self.task_dir / "run_meta.json").exists())
        self.assertTrue((self.task_dir / "pricing_result.json").exists())
        self.assertTrue((self.task_dir / "feishu_result_reply.preview.txt").exists())

    def test_queued_task_can_run_first_stage_to_s10_ready(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_first_stage_ready):
            result = self.runner.run_first_stage(
                self.task_id,
                allow_app_run=True,
                first_stage_script=self.first_stage_script,
                first_stage_result_path=self.first_stage_result_path,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "S10_READY")
        self.assertEqual(self.read_json(self.task_dir / "status.json")["status"], "S10_READY")
        self.assertTrue((self.task_dir / "first_stage_result.json").exists())
        self.assertFalse(self.runtime_lock.exists())

    def test_confirmed_task_cannot_run_first_stage(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "CONFIRMED"})

        result = self.runner.run_first_stage(
            self.task_id,
            allow_app_run=True,
            first_stage_script=self.first_stage_script,
            first_stage_result_path=self.first_stage_result_path,
        )

        self.assertFalse(result["ok"])
        self.assertIn("TASK_NOT_QUEUED", result["errors"])

    def test_run_first_stage_s10_ready_false_sets_failed(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_first_stage_not_ready):
            result = self.runner.run_first_stage(
                self.task_id,
                allow_app_run=True,
                first_stage_script=self.first_stage_script,
                first_stage_result_path=self.first_stage_result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("FIRST_STAGE_NOT_S10_READY", result["errors"])
        self.assertFalse(self.runtime_lock.exists())

    def test_run_first_stage_missing_result_sets_failed(self):
        with patch("pricing_runner.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="noop", stderr="")):
            result = self.runner.run_first_stage(
                self.task_id,
                allow_app_run=True,
                first_stage_script=self.first_stage_script,
                first_stage_result_path=self.first_stage_result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("FIRST_STAGE_RESULT_NOT_FOUND", result["errors"])

    def test_run_first_stage_invalid_json_sets_failed(self):
        def write_bad(*args, **kwargs):
            self.first_stage_result_path.parent.mkdir(parents=True, exist_ok=True)
            self.first_stage_result_path.write_text("{bad", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="bad-json", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=write_bad):
            result = self.runner.run_first_stage(
                self.task_id,
                allow_app_run=True,
                first_stage_script=self.first_stage_script,
                first_stage_result_path=self.first_stage_result_path,
            )

        self.assertFalse(result["ok"])
        self.assertIn("FIRST_STAGE_RESULT_JSON_INVALID", result["errors"])

    def test_run_first_stage_stale_result_is_not_collected(self):
        self.write_json(self.first_stage_result_path, {"flow_state": {"S10_READY": True}})
        old_timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(self.first_stage_result_path, (old_timestamp, old_timestamp))

        with patch("pricing_runner.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="noop", stderr="")):
            result = self.runner.run_first_stage(
                self.task_id,
                allow_app_run=True,
                first_stage_script=self.first_stage_script,
                first_stage_result_path=self.first_stage_result_path,
            )

        self.assertFalse(result["ok"])
        self.assertIn("FIRST_STAGE_RESULT_NOT_FOUND", result["errors"])
        self.assertFalse((self.task_dir / "first_stage_result.json").exists())

    def test_first_stage_pre_run_result_is_backed_up(self):
        self.write_json(self.first_stage_result_path, {"old": True, "flow_state": {"S10_READY": False}})

        with patch("pricing_runner.subprocess.run", side_effect=self.mock_first_stage_ready):
            result = self.runner.run_first_stage(
                self.task_id,
                allow_app_run=True,
                first_stage_script=self.first_stage_script,
                first_stage_result_path=self.first_stage_result_path,
            )

        self.assertTrue(result["ok"])
        backup_dir = self.task_dir / "pre_run_result_backups"
        backups = [path for path in backup_dir.iterdir() if path.name != "manifest.json"]
        self.assertTrue(any('"old": true' in path.read_text(encoding="utf-8") for path in backups))
        isolation = self.read_json(self.task_dir / "first_stage_pre_run_result_isolation.json")
        self.assertIn(str(self.first_stage_result_path), isolation["isolated_paths"])

    def test_s10_ready_task_can_run_second_stage_to_succeeded(self):
        self.mark_s10_ready()

        with patch("pricing_runner.subprocess.run", side_effect=self.mock_second_stage_success):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertTrue((self.task_dir / "pricing_result.json").exists())
        self.assertFalse(self.runtime_lock.exists())

    def test_run_second_stage_continue_next_reference_is_not_success(self):
        self.mark_s10_ready()

        with patch("pricing_runner.subprocess.run", side_effect=self.mock_second_stage_continue_next_reference):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "CONTINUE_NEXT_REFERENCE")
        self.assertEqual(result["technical_status"], "INCOMPLETE")
        self.assertEqual(result["recommended_next_action"], "run-second-stage")
        self.assertFalse((self.task_dir / "feishu_result_reply.preview.txt").exists())
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "CONTINUE_NEXT_REFERENCE")
        self.assertFalse(status["final_price_allowed"])

    def test_queued_task_cannot_run_second_stage(self):
        result = self.runner.run_second_stage(
            self.task_id,
            allow_app_run=True,
            second_stage_script=self.second_stage_script,
            second_stage_result_path=self.second_stage_result_path,
        )

        self.assertFalse(result["ok"])
        self.assertIn("TASK_NOT_S10_READY", result["errors"])

    def test_run_second_stage_manual_review_sets_needs_review(self):
        self.mark_s10_ready()

        with patch("pricing_runner.subprocess.run", side_effect=self.mock_second_stage_manual_review):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertEqual(result["technical_status"], "SUCCEEDED")
        self.assertEqual(result["business_status"], "NEEDS_REVIEW")
        self.assertEqual(result["recommended_next_action"], "manual-review")

    def test_run_second_stage_nested_full_chain_manual_review_sets_business_review(self):
        self.mark_s10_ready()

        with patch("pricing_runner.subprocess.run", side_effect=self.mock_second_stage_nested_manual_review):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertEqual(result["technical_status"], "SUCCEEDED")
        self.assertEqual(result["business_status"], "NEEDS_REVIEW")
        self.assertEqual(result["recommended_next_action"], "manual-review")
        self.assertNotIn("formatter_warnings", result)
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "NEEDS_REVIEW")
        self.assertEqual(status["technical_status"], "SUCCEEDED")
        self.assertEqual(status["business_status"], "NEEDS_REVIEW")
        self.assertEqual(status["recommended_next_action"], "manual-review")
        preview = (self.task_dir / "feishu_result_reply.preview.txt").read_text(encoding="utf-8")
        self.assertIn("\u3010\u5f85\u4eba\u5de5\u590d\u6838\u3011FS20260609_0001", preview)
        self.assertNotIn("\u3010\u5b9a\u4ef7\u5b8c\u6210\u3011", preview)
        self.assertIn("NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING", preview)
        self.assertIn("profit_yuan = 7472", preview)
        self.assertIn("suggested_purchase_price_yuan = 84928", preview)

    def test_run_second_stage_config_mismatch_hard_stops_without_success_or_price(self):
        self.mark_s10_ready()

        with patch("pricing_runner.subprocess.run", side_effect=self.mock_second_stage_config_mismatch):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "TARGET_INFO_NEEDS_CORRECTION")
        self.assertEqual(result["business_status"], "TARGET_INFO_NEEDS_CORRECTION")
        self.assertIn("CONFIG_MISMATCH_HARD_STOP", result["errors"])
        self.assertIn("CONFIG_TIER_MISMATCH", result["errors"])
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "TARGET_INFO_NEEDS_CORRECTION")
        self.assertEqual(status["canonical_error_code"], "CONFIG_MISMATCH_HARD_STOP")
        self.assertEqual(status["mismatch_reason"], "CONFIG_TIER_MISMATCH")
        self.assertFalse(status["auto_pricing_allowed"])
        self.assertFalse(status["final_price_allowed"])
        self.assertFalse(status["blocks_queue"])
        self.assertTrue(status["needs_resend_target_info"])
        preview = (self.task_dir / "feishu_result_reply.preview.txt").read_text(encoding="utf-8")
        self.assertIn("【目标车信息需修改】FS20260609_0001", preview)
        self.assertIn("重新发送完整车型配置", preview)
        self.assertNotIn("suggested_purchase_price_yuan", preview)
        self.assertNotIn("【定价完成】", preview)

    def test_run_second_stage_blocked_result_sets_failed(self):
        self.mark_s10_ready()

        with patch("pricing_runner.subprocess.run", side_effect=self.mock_second_stage_blocked):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY", result["errors"])

    def test_run_second_stage_missing_core_fields_sets_failed(self):
        self.mark_s10_ready()

        with patch("pricing_runner.subprocess.run", side_effect=self.mock_second_stage_contract_only):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertFalse(result["ok"])
        self.assertIn("RESULT_SCHEMA_INVALID_FOR_PRICING", result["errors"])

    def test_run_second_stage_stale_result_is_not_collected(self):
        self.mark_s10_ready()
        self.write_json(self.second_stage_result_path, {"manual_review_required": False})
        old_timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(self.second_stage_result_path, (old_timestamp, old_timestamp))

        with patch("pricing_runner.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="noop", stderr="")):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertFalse(result["ok"])
        self.assertIn("RESULT_FILE_NOT_FOUND", result["errors"])

    def test_second_stage_pre_run_result_is_backed_up(self):
        self.mark_s10_ready()
        self.write_json(self.second_stage_result_path, {"old": True, "manual_review_required": False})

        with patch("pricing_runner.subprocess.run", side_effect=self.mock_second_stage_success):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertTrue(result["ok"])
        backups = [path for path in (self.task_dir / "pre_run_result_backups").iterdir() if path.name != "manifest.json"]
        self.assertTrue(any('"old": true' in path.read_text(encoding="utf-8") for path in backups))
        isolation = self.read_json(self.task_dir / "second_stage_pre_run_result_isolation.json")
        self.assertIn(str(self.second_stage_result_path), isolation["isolated_paths"])

    def test_same_task_continue_next_reference_result_is_kept_for_continuation(self):
        self.mark_s10_ready()
        self.write_json(
            self.second_stage_result_path,
            {
                "task_id": self.task_id,
                "status": "CONTINUE_NEXT_REFERENCE",
                "target_fingerprint": "unit-target",
                "reference_history": [{"reference_index": 1, "list_price_10k": 7.30}],
            },
        )

        with patch("pricing_runner.subprocess.run", side_effect=self.mock_second_stage_success):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertTrue(result["ok"])
        isolation = self.read_json(self.task_dir / "second_stage_pre_run_result_isolation.json")
        self.assertEqual(isolation["isolated_paths"], [])
        self.assertEqual(isolation["kept_paths"][0]["reason"], "same_task_continue_next_reference")

    def test_run_second_stage_recovers_terminal_success_from_pre_run_backup_without_subprocess(self):
        self.mark_s10_ready()
        backup_path = self.task_dir / "pre_run_result_backups" / "20260702T063453.output__result_s10_to_s16.json"
        terminal_success = terminal_success_with_stale_low_score_payload()
        terminal_success.update(
            {
                "task_id": self.task_id,
                "produced_by_task_id": self.task_id,
                "target_fingerprint": "unit-target",
                "task_target_fingerprint": "unit-target",
            }
        )
        self.write_json(backup_path, terminal_success)
        self.write_json(
            self.second_stage_result_path,
            {
                "task_id": self.task_id,
                "status": "FAILED",
                "stop_code": "S13_ALL_ZERO_LOOP",
            },
        )

        with patch("pricing_runner.subprocess.run", side_effect=AssertionError("second stage should not run")):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertTrue(result["terminal_success_recovered_from_backup"])
        self.assertEqual(result["terminal_success_recovery_reason"], "PRE_RUN_ISOLATED_SUCCESS_RESULT")
        self.assertIn("pre_run_result_backups", result["terminal_success_backup_path"])
        pricing_result = self.read_json(self.task_dir / "pricing_result.json")
        self.assertEqual(pricing_result["status"], "FULL_CHAIN_PRICED_DONE")
        self.assertFalse(pricing_result["dispatcher_should_continue"])
        self.assertIsNone(pricing_result["next_reference_index"])
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "SUCCEEDED")
        self.assertTrue(status["terminal_success_recovered_from_backup"])

    def test_run_second_stage_rejects_cross_task_terminal_success_backup(self):
        self.mark_s10_ready()
        backup_path = self.task_dir / "pre_run_result_backups" / "20260702T063453.output__result_s10_to_s16.json"
        terminal_success = terminal_success_with_stale_low_score_payload()
        terminal_success.update(
            {
                "task_id": "FS20260702_0003",
                "produced_by_task_id": "FS20260702_0003",
                "target_fingerprint": "other-target",
                "task_target_fingerprint": "other-target",
            }
        )
        self.write_json(backup_path, terminal_success)

        with patch("pricing_runner.subprocess.run", side_effect=self.mock_second_stage_success):
            result = self.runner.run_second_stage(
                self.task_id,
                allow_app_run=True,
                second_stage_script=self.second_stage_script,
                second_stage_result_path=self.second_stage_result_path,
            )

        self.assertTrue(result["ok"])
        self.assertNotIn("terminal_success_recovered_from_backup", result)
        rejection_files = list((self.task_dir / "result_scope_rejections").glob("*.json"))
        self.assertTrue(rejection_files)
        rejection_text = rejection_files[0].read_text(encoding="utf-8")
        self.assertIn("CROSS_TASK_PRICING_RESULT_REJECTED", rejection_text)

    def test_revalidate_result_changes_invalid_success_to_failed(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "SUCCEEDED"})
        self.write_json(self.task_dir / "pricing_result.json", {"status": "SECOND_STAGE_BLOCKED_NOT_AT_S10_READY"})

        with patch("pricing_runner.PROJECT_ROOT", self.root):
            result = self.runner.revalidate_result(self.task_id)

        self.assertFalse(result["ok"])
        self.assertEqual(self.read_json(self.task_dir / "status.json")["status"], "FAILED")
        self.assertIn("MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY", result["errors"])

    def test_revalidate_result_config_mismatch_hard_stops_without_price_output(self):
        self.write_json(
            self.task_dir / "status.json",
            {
                "task_id": self.task_id,
                "status": "SUCCEEDED",
                "business_status": "SUCCEEDED",
                "source": "feishu",
                "latest_run_id": "current-run",
                "generation_id": "current-run",
            },
        )
        self.write_json(
            self.task_dir / "pricing_result.json",
            {
                "manual_review_required": False,
                "suggested_purchase_price_yuan": 100000,
                "config_semantic_decision_code": "POWERTRAIN_TOKEN_MISMATCH",
            },
        )

        with patch("pricing_runner.PROJECT_ROOT", self.root):
            result = self.runner.revalidate_result(self.task_id)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "TARGET_INFO_NEEDS_CORRECTION")
        self.assertEqual(result["business_status"], "TARGET_INFO_NEEDS_CORRECTION")
        self.assertIn("CONFIG_MISMATCH_HARD_STOP", result["errors"])
        self.assertIn("POWERTRAIN_TOKEN_MISMATCH", result["errors"])
        self.assertNotIn("suggested_purchase_price_yuan", result)
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["canonical_error_code"], "CONFIG_MISMATCH_HARD_STOP")
        self.assertEqual(status["mismatch_reason"], "POWERTRAIN_TOKEN_MISMATCH")
        self.assertFalse(status["final_price_allowed"])

    def test_revalidate_result_changes_existing_success_to_needs_review(self):
        self.write_json(
            self.task_dir / "status.json",
            {
                "task_id": self.task_id,
                "status": "SUCCEEDED",
                "source": "feishu",
                "latest_run_id": "second-stage-run",
                "generation_id": "second-stage-run",
            },
        )
        self.write_json(self.task_dir / "pricing_result.json", full_chain_manual_review_payload())

        with patch("pricing_runner.PROJECT_ROOT", self.root):
            result = self.runner.revalidate_result(self.task_id)

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["previous_status"], "SUCCEEDED")
        self.assertEqual(result["new_status"], "NEEDS_REVIEW")
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertEqual(result["technical_status"], "SUCCEEDED")
        self.assertEqual(result["business_status"], "NEEDS_REVIEW")
        self.assertEqual(result["recommended_next_action"], "manual-review")
        self.assertNotIn("formatter_warnings", result)
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "NEEDS_REVIEW")
        self.assertEqual(status["technical_status"], "SUCCEEDED")
        self.assertEqual(status["business_status"], "NEEDS_REVIEW")
        self.assertEqual(status["recommended_next_action"], "manual-review")
        preview = (self.task_dir / "feishu_result_reply.preview.txt").read_text(encoding="utf-8")
        self.assertIn("\u3010\u5f85\u4eba\u5de5\u590d\u6838\u3011FS20260609_0001", preview)
        self.assertNotIn("\u3010\u5b9a\u4ef7\u5b8c\u6210\u3011", preview)
        self.assertIn("NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING", preview)
        self.assertIn("profit_yuan = 7472", preview)
        self.assertIn("suggested_purchase_price_yuan = 84928", preview)

    def test_revalidate_result_refreshes_existing_old_profit_rate(self):
        self.write_json(
            self.task_dir / "status.json",
            {
                "task_id": self.task_id,
                "status": "SUCCEEDED",
                "source": "feishu",
                "latest_run_id": "second-stage-run",
                "generation_id": "second-stage-run",
            },
        )
        self.write_json(
            self.task_dir / "pricing_result.json",
            full_chain_manual_review_payload(profit_yuan=6168, suggested_purchase_price_yuan=87732),
        )

        with patch("pricing_runner.subprocess.run") as run:
            result = self.runner.revalidate_result(self.task_id)

        run.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertTrue(result["pricing_chain_refreshed"])
        self.assertEqual(result["profit_yuan"], 7472)
        self.assertEqual(result["suggested_purchase_price_yuan"], 84928)
        refreshed = self.read_json(self.task_dir / "pricing_result.json")
        self.assertEqual(refreshed["profit_yuan"], 7472)
        self.assertEqual(refreshed["pricing"]["profit_yuan"], 7472)
        self.assertEqual(refreshed["suggested_purchase_price_yuan"], 84928)
        self.assertEqual(refreshed["pricing"]["suggested_purchase_price_yuan"], 84928)
        preview = (self.task_dir / "feishu_result_reply.preview.txt").read_text(encoding="utf-8")
        self.assertIn("profit_yuan = 7472", preview)
        self.assertIn("suggested_purchase_price_yuan = 84928", preview)
        self.assertNotIn("profit_yuan = 6168", preview)
        self.assertNotIn("suggested_purchase_price_yuan = 87732", preview)

    def test_revalidate_result_missing_price_chain_is_not_success(self):
        self.write_json(
            self.task_dir / "status.json",
            {"task_id": self.task_id, "status": "SUCCEEDED", "source": "feishu"},
        )
        self.write_json(
            self.task_dir / "pricing_result.json",
            {
                "status": "SUCCEEDED",
                "manual_review_required": False,
                "suggested_purchase_price_yuan": 100000,
            },
        )

        result = self.runner.revalidate_result(self.task_id)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "RESULT_MISSING_REQUIRED_PRICING_FIELDS")
        self.assertEqual(result["technical_status"], "INCOMPLETE")
        self.assertIn("RESULT_MISSING_REQUIRED_PRICING_FIELDS", result["errors"])
        preview = (self.task_dir / "feishu_result_reply.preview.txt").read_text(encoding="utf-8")
        self.assertIn("定价失败", preview)
        self.assertNotIn("【定价完成】", preview)

    def test_revalidate_result_maps_s17_payload_manual_review(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "SUCCEEDED", "source": "feishu"})
        payload = {
            "status": "SUCCEEDED",
            "s17_payload": {
                "manual_review_required": True,
                "manual_review_reasons": ["NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING"],
                "reference_score": 94.0,
                "reference_price_10k": 9.84,
            },
        }
        self.write_json(self.task_dir / "pricing_result.json", payload)

        result = self.runner.revalidate_result(self.task_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertEqual(result["business_status"], "NEEDS_REVIEW")

    def test_revalidate_result_maps_pricing_manual_review(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "SUCCEEDED", "source": "feishu"})
        payload = {
            "status": "SUCCEEDED",
            "pricing": {
                "manual_review_required": True,
                "manual_review_reasons": ["NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING"],
                "suggested_purchase_price_yuan": 84928,
            },
        }
        self.write_json(self.task_dir / "pricing_result.json", payload)

        result = self.runner.revalidate_result(self.task_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertEqual(result["business_status"], "NEEDS_REVIEW")

    def test_revalidate_status_reports_business_status_and_manual_action(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "SUCCEEDED", "source": "feishu"})
        self.write_json(self.task_dir / "pricing_result.json", full_chain_manual_review_payload())

        self.runner.revalidate_result(self.task_id)
        status = self.runner.status(self.task_id)

        self.assertEqual(status["status"], "NEEDS_REVIEW")
        self.assertEqual(status["technical_status"], "SUCCEEDED")
        self.assertEqual(status["business_status"], "NEEDS_REVIEW")
        self.assertEqual(status["recommended_next_action"], "manual-review")

    def test_manual_confirm_price_updates_local_task_without_running_app(self):
        note = "未找到边界参考车，样本偏少，按系统测算价 84928 元上调取整，人工确认收车价 86000 元。"
        self.write_json(
            self.task_dir / "status.json",
            {"task_id": self.task_id, "status": "NEEDS_REVIEW", "business_status": "NEEDS_REVIEW", "technical_status": "SUCCEEDED"},
        )
        self.write_json(self.task_dir / "pricing_result.json", full_chain_manual_review_payload())

        with patch("pricing_runner.subprocess.run") as run:
            result = self.runner.manual_confirm_price(
                self.task_id,
                manual_confirm_price=86000,
                manual_review_note=note,
                manual_confirm_by="local_user",
            )

        run.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertEqual(result["technical_status"], "SUCCEEDED")
        self.assertEqual(result["business_status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertEqual(result["recommended_next_action"], "ready-to-send")
        self.assertEqual(result["system_suggested_purchase_price_yuan"], 84928)
        self.assertEqual(result["manual_confirmed_purchase_price_yuan"], 86000)
        self.assertEqual(result["final_purchase_price_yuan"], 86000)
        self.assertEqual(result["manual_adjustment_yuan"], 1072)
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertEqual(status["technical_status"], "SUCCEEDED")
        self.assertEqual(status["business_status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertEqual(status["recommended_next_action"], "ready-to-send")
        pricing_result = self.read_json(self.task_dir / "pricing_result.json")
        self.assertEqual(pricing_result["suggested_purchase_price_yuan"], 84928)
        self.assertEqual(pricing_result["system_suggested_purchase_price_yuan"], 84928)
        self.assertEqual(pricing_result["manual_confirmed_purchase_price_yuan"], 86000)
        self.assertEqual(pricing_result["final_purchase_price_yuan"], 86000)
        self.assertEqual(pricing_result["manual_adjustment_yuan"], 1072)
        self.assertTrue(pricing_result["manual_review_confirmed"])
        self.assertTrue(pricing_result["manual_review_confirmed_at"])
        self.assertEqual(pricing_result["manual_review_confirmed_by"], "local_user")
        self.assertTrue((self.task_dir / "manual_confirm_result.json").exists())
        preview = (self.task_dir / "feishu_result_reply.preview.txt").read_text(encoding="utf-8")
        self.assertIn("\u3010\u4eba\u5de5\u590d\u6838\u5df2\u786e\u8ba4\u3011FS20260609_0001", preview)
        self.assertIn("system_suggested_purchase_price_yuan = 84928", preview)
        self.assertIn("manual_confirmed_purchase_price_yuan = 86000", preview)
        self.assertIn("final_purchase_price_yuan = 86000", preview)
        self.assertIn(note, preview)

    def test_manual_confirm_price_allows_manual_review_without_system_suggested_price(self):
        note = "系统未输出自动建议价，主管人工确认收车价 86000 元。"
        self.write_json(
            self.task_dir / "status.json",
            {
                "task_id": self.task_id,
                "status": "WAITING_MANUAL_PRICE",
                "business_status": "NEEDS_REVIEW",
                "technical_status": "SUCCEEDED",
                "manual_review_required": True,
                "waiting_manual_price": True,
                "manual_review_reason_code": "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
            },
        )
        payload = full_chain_manual_review_payload(suggested_purchase_price_yuan=None)
        payload["status"] = "FULL_CHAIN_MANUAL_REVIEW_DONE"
        payload["pricing"] = {
            "status": "NEEDS_REVIEW",
            "reason": "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
            "manual_review_required": True,
        }
        self.write_json(self.task_dir / "pricing_result.json", payload)

        with patch("pricing_runner.subprocess.run") as run:
            result = self.runner.manual_confirm_price(
                self.task_id,
                manual_confirm_price=86000,
                manual_review_note=note,
                manual_confirm_by="ou_supervisor",
                manual_confirm_raw_text=f"{self.task_id} 8.6万",
                manual_confirm_task_id=self.task_id,
                manual_confirmed_by_role="supervisor",
            )

        run.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["final_purchase_price_yuan"], 86000)
        self.assertEqual(result["manual_price_yuan"], 86000)
        self.assertIsNone(result["system_suggested_price_yuan"])
        self.assertTrue(result["system_suggested_price_missing"])
        self.assertFalse(result["system_suggested_price_required"])
        self.assertIsNone(result["manual_adjustment_yuan"])
        self.assertEqual(result["final_price_source"], "SUPERVISOR_MANUAL_CONFIRM")
        self.assertEqual(result["pricing_decision_source"], "MANUAL_SUPERVISOR_PRICE")
        self.assertTrue(result["current_target_task_cleared"])
        self.assertFalse((self.data_dir / "current_target_task.json").exists())

        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertFalse(status["waiting_manual_price"])
        self.assertFalse(status["manual_review_required"])
        self.assertFalse(status["blocks_queue"])
        self.assertTrue(status["price_confirmed"])
        self.assertTrue(status["current_target_task_cleared"])
        pricing_result = self.read_json(self.task_dir / "pricing_result.json")
        self.assertEqual(pricing_result["manual_price_yuan"], 86000)
        self.assertEqual(pricing_result["final_purchase_price_yuan"], 86000)
        self.assertTrue(pricing_result["system_suggested_price_missing"])
        self.assertFalse(pricing_result["system_suggested_price_required"])
        self.assertEqual(pricing_result["manual_confirm_raw_text"], f"{self.task_id} 8.6万")
        preview = (self.task_dir / "feishu_result_reply.preview.txt").read_text(encoding="utf-8")
        self.assertIn("最终收车价：86000 元", preview)
        self.assertIn("确认来源：主管人工报价", preview)
        self.assertNotIn("suggested_purchase_price_yuan", preview)
        self.assertNotIn("SYSTEM_SUGGESTED_PRICE_MISSING", preview)

    def test_manual_confirm_price_accepts_legacy_waiting_task_local_result_with_different_run_id(self):
        note = "旧人工复核任务，主管确认收车价 86000 元。"
        self.write_json(
            self.task_dir / "status.json",
            {
                "task_id": self.task_id,
                "status": "WAITING_MANUAL_PRICE",
                "business_status": "NEEDS_REVIEW",
                "technical_status": "SUCCEEDED",
                "manual_review_required": True,
                "waiting_manual_price": True,
                "latest_run_id": "20260624T074757_manual_confirm_price_ac58f5ca",
                "generation_id": "20260624T074757_manual_confirm_price_ac58f5ca",
            },
        )
        payload = full_chain_manual_review_payload(suggested_purchase_price_yuan=None)
        payload.update(
            {
                "task_id": self.task_id,
                "run_id": "20260623T110125_second_stage_17c7ed2e",
                "generation_id": "20260623T110125_second_stage_17c7ed2e",
                "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
                "final_status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
                "current_state": "FULL_CHAIN_MANUAL_REVIEW_DONE",
            }
        )
        payload["pricing"] = {
            "status": "manual_review",
            "reason": "无有效参考车",
            "manual_review_required": True,
        }
        self.write_json(self.task_dir / "pricing_result.json", payload)

        with patch("pricing_runner.subprocess.run") as run:
            result = self.runner.manual_confirm_price(
                self.task_id,
                manual_confirm_price=86000,
                manual_review_note=note,
                manual_confirm_by="ou_supervisor",
                manual_confirm_raw_text=f"{self.task_id} 8.6万",
                manual_confirm_task_id=self.task_id,
                manual_confirmed_by_role="supervisor",
            )

        run.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["final_purchase_price_yuan"], 86000)
        self.assertEqual(result["manual_price_yuan"], 86000)
        self.assertEqual(result["pricing_result_source"], "task_local_pricing_result")
        self.assertEqual(result["pricing_result_run_id"], "20260623T110125_second_stage_17c7ed2e")
        self.assertTrue(result["task_local_pricing_result_accepted_for_manual_confirm"])
        self.assertTrue(result["system_suggested_price_missing"])
        self.assertNotIn("RESULT_FILE_NOT_FOUND", result["errors"])
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertEqual(status["manual_price_yuan"], 86000)
        self.assertEqual(status["pricing_result_run_id"], "20260623T110125_second_stage_17c7ed2e")
        self.assertTrue(status["task_local_pricing_result_accepted_for_manual_confirm"])
        self.assertFalse(status["waiting_manual_price"])
        self.assertFalse(status["manual_review_required"])
        self.assertFalse((self.data_dir / "current_target_task.json").exists())

    def test_manual_confirm_price_keeps_global_output_stale_protection_without_task_local_result(self):
        self.write_json(
            self.task_dir / "status.json",
            {
                "task_id": self.task_id,
                "status": "WAITING_MANUAL_PRICE",
                "business_status": "NEEDS_REVIEW",
                "technical_status": "SUCCEEDED",
                "manual_review_required": True,
                "waiting_manual_price": True,
                "latest_run_id": "manual-confirm-run",
                "generation_id": "manual-confirm-run",
            },
        )
        stale_global = full_chain_manual_review_payload(suggested_purchase_price_yuan=None)
        stale_global.update({"run_id": "other-task-run", "generation_id": "other-task-run", "task_id": "FS20260623_9999"})
        self.write_json(self.second_stage_result_path, stale_global)

        with patch("pricing_runner.PROJECT_ROOT", self.root):
            result = self.runner.manual_confirm_price(
                self.task_id,
                manual_confirm_price=86000,
                manual_review_note="note",
            )

        self.assertFalse(result["ok"])
        self.assertIn("RESULT_FILE_NOT_FOUND", result["errors"])
        validation = self.read_json(self.task_dir / "runner_error.json")
        self.assertIn("RESULT_FILE_NOT_FOUND", validation["errors"])

    def test_manual_confirm_price_rejects_task_local_result_for_other_task(self):
        self.write_json(
            self.task_dir / "status.json",
            {
                "task_id": self.task_id,
                "status": "WAITING_MANUAL_PRICE",
                "business_status": "NEEDS_REVIEW",
                "manual_review_required": True,
                "waiting_manual_price": True,
                "latest_run_id": "manual-confirm-run",
            },
        )
        payload = full_chain_manual_review_payload(suggested_purchase_price_yuan=None)
        payload.update({"task_id": "FS20260623_9999", "run_id": "second-stage-run"})
        self.write_json(self.task_dir / "pricing_result.json", payload)

        result = self.runner.manual_confirm_price(
            self.task_id,
            manual_confirm_price=86000,
            manual_review_note="note",
        )

        self.assertFalse(result["ok"])
        self.assertIn("RESULT_FILE_NOT_FOUND", result["errors"])

    def test_manual_confirm_price_accepts_task_local_missing_required_pricing_fields_review(self):
        self.write_json(
            self.task_dir / "status.json",
            {
                "task_id": self.task_id,
                "status": "WAITING_MANUAL_PRICE",
                "business_status": "NEEDS_REVIEW",
                "manual_review_required": True,
                "waiting_manual_price": True,
                "latest_run_id": "manual-confirm-run",
            },
        )
        payload = full_chain_manual_review_payload(suggested_purchase_price_yuan=None)
        payload.update(
            {
                "task_id": self.task_id,
                "status": "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
                "final_status": "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
                "run_id": "second-stage-run",
                "generation_id": "second-stage-run",
            }
        )
        payload["pricing"].pop("suggested_purchase_price_yuan", None)
        payload["pricing"]["manual_review_required"] = True
        self.write_json(self.task_dir / "pricing_result.json", payload)

        result = self.runner.manual_confirm_price(
            self.task_id,
            manual_confirm_price=86000,
            manual_review_note="note",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["final_purchase_price_yuan"], 86000)
        self.assertTrue(result["task_local_pricing_result_accepted_for_manual_confirm"])
        self.assertTrue(any("RESULT_MISSING_REQUIRED_PRICING_FIELDS" in warning for warning in result["warnings"]))

    def test_manual_confirm_price_keeps_non_manual_review_missing_price_protection(self):
        self.write_json(
            self.task_dir / "status.json",
            {"task_id": self.task_id, "status": "SUCCEEDED", "business_status": "SUCCEEDED", "technical_status": "SUCCEEDED"},
        )
        payload = full_success_payload()
        payload.pop("suggested_purchase_price_yuan", None)
        payload.pop("final_purchase_price_yuan", None)
        self.write_json(self.task_dir / "pricing_result.json", payload)

        result = self.runner.manual_confirm_price(
            self.task_id,
            manual_confirm_price=86000,
            manual_review_note="note",
        )

        self.assertFalse(result["ok"])
        self.assertIn("MANUAL_CONFIRM_REQUIRES_NEEDS_REVIEW", result["errors"])

    def test_manual_confirm_price_rejects_failed_task_without_valid_result(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "FAILED", "business_status": "FAILED"})

        result = self.runner.manual_confirm_price(
            self.task_id,
            manual_confirm_price=86000,
            manual_review_note="note",
        )

        self.assertFalse(result["ok"])
        self.assertIn("MANUAL_CONFIRM_REQUIRES_NEEDS_REVIEW", result["errors"])

    def test_send_result_dry_run_reads_preview_and_masks_chat_id(self):
        chat_id = "oc_secret_chat_123456789"
        self.write_send_ready_task(chat_id=chat_id)

        with patch("pricing_runner.send_text_message", return_value={"ok": True, "dry_run": True, "chat_id": chat_id}) as send:
            result = self.runner.send_result(self.task_id)

        send.assert_called_once()
        self.assertTrue(send.call_args.kwargs["dry_run"])
        self.assertEqual(send.call_args.kwargs["chat_id"], chat_id)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["sent"])
        self.assertEqual(result["task_id"], self.task_id)
        self.assertEqual(result["status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertEqual(result["preview_path"], str(self.task_dir / "feishu_result_reply.preview.txt"))
        self.assertIn("【人工复核已确认】", result["message_preview"])
        self.assertNotEqual(result["chat_id_masked"], chat_id)
        self.assertNotIn(chat_id, json.dumps(result, ensure_ascii=False))
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "MANUAL_REVIEW_CONFIRMED")

    def test_send_result_missing_preview_is_rejected(self):
        self.write_send_ready_task(write_preview=False)

        with patch("pricing_runner.send_text_message") as send:
            result = self.runner.send_result(self.task_id)

        send.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("FEISHU_RESULT_PREVIEW_NOT_FOUND", result["errors"])

    def test_send_result_missing_chat_id_is_rejected(self):
        self.write_send_ready_task(chat_id=None)

        with patch("pricing_runner.send_text_message") as send:
            result = self.runner.send_result(self.task_id)

        send.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("FEISHU_CHAT_ID_MISSING", result["errors"])

    def test_send_result_needs_review_requires_manual_confirmation_first(self):
        self.write_json(
            self.task_dir / "status.json",
            {"task_id": self.task_id, "status": "NEEDS_REVIEW", "business_status": "NEEDS_REVIEW", "raw_chat_id": "oc_secret_chat_123456789"},
        )
        (self.task_dir / "feishu_result_reply.preview.txt").write_text("【待人工复核】\n", encoding="utf-8")

        with patch("pricing_runner.send_text_message") as send:
            result = self.runner.send_result(self.task_id)

        send.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("SEND_RESULT_REQUIRES_MANUAL_CONFIRMATION", result["errors"])
        self.assertIn("请先确认人工收车价", result["message"])

    def test_send_result_live_updates_status_without_overwriting_pricing_result(self):
        chat_id = "oc_secret_chat_123456789"
        self.write_send_ready_task(chat_id=chat_id)
        pricing_result = {
            "status": "MANUAL_REVIEW_CONFIRMED",
            "manual_confirmed_purchase_price_yuan": 86000,
            "final_purchase_price_yuan": 86000,
        }
        self.write_json(self.task_dir / "pricing_result.json", pricing_result)

        with patch("pricing_runner.send_text_message", return_value={"ok": True, "dry_run": False, "chat_id": chat_id}) as send:
            result = self.runner.send_result(self.task_id, live=True)

        send.assert_called_once()
        self.assertFalse(send.call_args.kwargs["dry_run"])
        self.assertTrue(result["ok"])
        self.assertFalse(result["dry_run"])
        self.assertTrue(result["sent"])
        self.assertEqual(result["status"], "RESULT_SENT")
        self.assertEqual(result["business_status"], "RESULT_SENT")
        self.assertNotIn("message_preview", result)
        self.assertNotIn(chat_id, json.dumps(result, ensure_ascii=False))
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "RESULT_SENT")
        self.assertEqual(status["business_status"], "RESULT_SENT")
        self.assertTrue(status["sent_to_feishu"])
        self.assertTrue(status["sent_to_feishu_at"])
        self.assertIsNone(status["recommended_next_action"])
        self.assertEqual(self.read_json(self.task_dir / "pricing_result.json"), pricing_result)

    def test_send_result_allows_auto_pricing_succeeded_business_status(self):
        chat_id = "oc_secret_chat_123456789"
        self.write_json(
            self.task_dir / "status.json",
            {"task_id": self.task_id, "status": "SUCCEEDED", "business_status": "SUCCEEDED", "raw_chat_id": chat_id},
        )
        (self.task_dir / "feishu_result_reply.preview.txt").write_text("【定价完成】\n", encoding="utf-8")

        with patch("pricing_runner.send_text_message", return_value={"ok": True, "dry_run": True, "chat_id": chat_id}):
            result = self.runner.send_result(self.task_id)

        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])

    def test_manual_confirm_price_requires_positive_integer(self):
        self.write_json(
            self.task_dir / "status.json",
            {"task_id": self.task_id, "status": "NEEDS_REVIEW", "business_status": "NEEDS_REVIEW", "technical_status": "SUCCEEDED"},
        )
        self.write_json(self.task_dir / "pricing_result.json", full_chain_manual_review_payload())

        result = self.runner.manual_confirm_price(
            self.task_id,
            manual_confirm_price=0,
            manual_review_note="note",
        )

        self.assertFalse(result["ok"])
        self.assertIn("MANUAL_CONFIRM_PRICE_INVALID", result["errors"])

    def test_status_reports_manual_review_confirmed(self):
        self.write_json(
            self.task_dir / "status.json",
            {"task_id": self.task_id, "status": "NEEDS_REVIEW", "business_status": "NEEDS_REVIEW", "technical_status": "SUCCEEDED"},
        )
        self.write_json(self.task_dir / "pricing_result.json", full_chain_manual_review_payload())
        self.runner.manual_confirm_price(
            self.task_id,
            manual_confirm_price=86000,
            manual_review_note="note",
        )

        status = self.runner.status(self.task_id)

        self.assertEqual(status["status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertEqual(status["technical_status"], "SUCCEEDED")
        self.assertEqual(status["business_status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertEqual(status["recommended_next_action"], "ready-to-send")

    def test_revalidate_result_skips_stale_output_generation(self):
        self.write_json(
            self.task_dir / "status.json",
            {
                "task_id": self.task_id,
                "status": "SUCCEEDED",
                "source": "feishu",
                "latest_run_id": "current-run",
                "generation_id": "current-run",
            },
        )
        self.write_json(
            self.second_stage_result_path,
            {
                "run_id": "old-run",
                "generation_id": "old-run",
                "manual_review_required": True,
            },
        )

        with patch("pricing_runner.PROJECT_ROOT", self.root):
            result = self.runner.revalidate_result(self.task_id)

        self.assertFalse(result["ok"])
        self.assertIn("RESULT_FILE_NOT_FOUND", result["errors"])
        self.assertIn("STALE_RESULT_RUN_ID_IGNORED", result["warnings"])

    def test_status_recommends_next_action(self):
        queued = self.runner.status(self.task_id)
        self.assertEqual(queued["recommended_next_action"], "run-first-stage")

        self.mark_s10_ready()
        ready = self.runner.status(self.task_id)
        self.assertEqual(ready["recommended_next_action"], "run-second-stage")
        self.assertTrue(ready["first_stage_s10_ready"])
        self.assertTrue(ready["current_target_task_task_id_match"])

    def test_requeue_second_stage_moves_failed_with_s10_ready_back_to_s10_ready(self):
        self.mark_s10_ready()
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "FAILED", "source": "feishu"})

        result = self.runner.requeue_second_stage(self.task_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status_after"], "S10_READY")
        self.assertEqual(result["requeue_code"], "REQUEUED_SECOND_STAGE_FROM_FAILED")
        self.assertEqual(result["recommended_next_action"], "run-second-stage")
        status = self.read_json(self.task_dir / "status.json")
        self.assertEqual(status["status"], "S10_READY")
        self.assertEqual(status["latest_run_id"], result["run_id"])
        self.assertTrue((self.task_dir / "first_stage_result.json").exists())

    def test_requeue_second_stage_rejects_missing_s10_ready_evidence(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "FAILED", "source": "feishu"})

        result = self.runner.requeue_second_stage(self.task_id)

        self.assertFalse(result["ok"])
        self.assertIn("FIRST_STAGE_RESULT_NOT_FOUND", result["errors"])

    def test_status_ignores_stale_runner_result_when_latest_error_has_new_run_id(self):
        self.write_json(
            self.task_dir / "status.json",
            {"task_id": self.task_id, "status": "FAILED", "source": "feishu", "latest_run_id": "new-run"},
        )
        self.write_json(self.task_dir / "runner_result.json", {"task_id": self.task_id, "run_id": "old-run", "errors": []})
        self.write_json(self.task_dir / "runner_error.json", {"task_id": self.task_id, "run_id": "new-run", "errors": ["MAIN_SCRIPT_FAILED"]})

        result = self.runner.status(self.task_id)

        self.assertEqual(result["last_error_code"], "MAIN_SCRIPT_FAILED")
        self.assertIn("STALE_RUN_RESULT_IGNORED", result["runner_warnings"])

    def test_stage_run_records_run_identity_and_uses_utf8_replace(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_first_stage_ready) as run:
            result = self.runner.run_first_stage(
                self.task_id,
                allow_app_run=True,
                first_stage_script=self.first_stage_script,
                first_stage_result_path=self.first_stage_result_path,
            )

        self.assertTrue(result["run_id"])
        self.assertEqual(result["generation_id"], result["run_id"])
        self.assertEqual(self.read_json(self.task_dir / "first_stage_run_meta.json")["run_id"], result["run_id"])
        self.assertEqual(self.read_json(self.task_dir / "first_stage_result.json")["run_id"], result["run_id"])
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_audit_log_records_key_events(self):
        with patch("pricing_runner.subprocess.run", side_effect=self.mock_success):
            self.runner.run_manual(
                self.task_id,
                allow_app_run=True,
                main_script=self.main_script,
                result_path=self.result_path,
            )

        audit_text = (self.task_root / "audit_log.jsonl").read_text(encoding="utf-8")
        for marker in [
            "run_manual_requested",
            "lock_created",
            "main_script_started",
            "main_script_finished",
            "pricing_result_collected",
            "result_format_generated",
            "lock_released",
        ]:
            self.assertIn(marker, audit_text)

    def mock_success(self, *args, **kwargs):
        self.write_json(self.result_path, full_success_payload())
        return SimpleNamespace(returncode=0, stdout="stdout-ok", stderr="stderr-ok")

    def mock_failure_with_result(self, *args, **kwargs):
        self.write_json(self.result_path, {"manual_review_required": False})
        return SimpleNamespace(returncode=2, stdout="stdout-fail", stderr="stderr-fail")

    def mock_manual_review(self, *args, **kwargs):
        self.write_json(
            self.result_path,
            {
                "manual_review_required": True,
                "manual_review_reasons": ["NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING"],
            },
        )
        return SimpleNamespace(returncode=0, stdout="stdout-review", stderr="")

    def mock_blocked_status(self, *args, **kwargs):
        self.write_json(self.result_path, {"status": "SECOND_STAGE_BLOCKED_NOT_AT_S10_READY"})
        return SimpleNamespace(returncode=0, stdout="blocked", stderr="")

    def mock_s14_blocked_status(self, *args, **kwargs):
        self.write_json(self.result_path, {"status": "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED"})
        return SimpleNamespace(returncode=0, stdout="s14-blocked", stderr="")

    def mock_contract_issue_code(self, *args, **kwargs):
        self.write_json(self.result_path, {"issue_code": "PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE"})
        return SimpleNamespace(returncode=0, stdout="contract-blocked", stderr="")

    def mock_s10_ready_false(self, *args, **kwargs):
        self.write_json(
            self.result_path,
            {
                "flow_state": {"S10_READY": False},
                "manual_review_required": False,
            },
        )
        return SimpleNamespace(returncode=0, stdout="not-at-s10", stderr="")

    def mock_contract_only_result(self, *args, **kwargs):
        self.write_json(self.result_path, {"status": "CONTRACT_ONLY", "ready": False})
        return SimpleNamespace(returncode=0, stdout="contract-only", stderr="")

    def mock_core_field_success(self, *args, **kwargs):
        self.write_json(self.result_path, {"target_score": 88})
        return SimpleNamespace(returncode=0, stdout="success", stderr="")

    def mock_success_to_path(self, path):
        self.write_json(path, full_success_payload())
        return SimpleNamespace(returncode=0, stdout="stdout-ok", stderr="")

    def mock_first_stage_ready(self, *args, **kwargs):
        self.write_json(self.first_stage_result_path, {"flow_state": {"S10_READY": True}, "same_source_cards": [{"id": 1}]})
        return SimpleNamespace(returncode=0, stdout="first-ready", stderr="")

    def mock_first_stage_not_ready(self, *args, **kwargs):
        self.write_json(self.first_stage_result_path, {"flow_state": {"S10_READY": False}, "same_source_cards": []})
        return SimpleNamespace(returncode=0, stdout="first-not-ready", stderr="")

    def mock_second_stage_success(self, *args, **kwargs):
        self.write_json(self.second_stage_result_path, full_success_payload())
        return SimpleNamespace(returncode=0, stdout="second-success", stderr="")

    def mock_second_stage_continue_next_reference(self, *args, **kwargs):
        self.write_json(
            self.second_stage_result_path,
            {
                "status": "CONTINUE_NEXT_REFERENCE",
                "final_status": "CONTINUE_NEXT_REFERENCE",
                "current_state": "CONTINUE_NEXT_REFERENCE",
                "current_reference_index": 1,
                "next_reference_index": 2,
                "reference_history_len": 1,
                "target_score": 82.0,
                "reference_score": 68.0,
                "s14_collect_done": True,
                "s14_images_processed": 39,
                "s14_images_total": 39,
                "manual_review_required": False,
            },
        )
        return SimpleNamespace(returncode=0, stdout="second-continue", stderr="")

    def mock_second_stage_manual_review(self, *args, **kwargs):
        self.write_json(self.second_stage_result_path, {"manual_review_required": True, "manual_review_reasons": ["NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING"]})
        return SimpleNamespace(returncode=0, stdout="second-review", stderr="")

    def mock_second_stage_nested_manual_review(self, *args, **kwargs):
        self.write_json(self.second_stage_result_path, full_chain_manual_review_payload())
        return SimpleNamespace(returncode=0, stdout="second-review-nested", stderr="")

    def mock_second_stage_config_mismatch(self, *args, **kwargs):
        self.write_json(
            self.second_stage_result_path,
            {
                "target_vehicle": "大众 迈腾 2018款 330TSI DSG 豪华型",
                "manual_review_required": False,
                "suggested_purchase_price_yuan": 100000,
                "config_semantic_decision_code": "CONFIG_TIER_MISMATCH",
            },
        )
        return SimpleNamespace(returncode=0, stdout="second-config-mismatch", stderr="")

    def mock_second_stage_blocked(self, *args, **kwargs):
        self.write_json(self.second_stage_result_path, {"status": "SECOND_STAGE_BLOCKED_NOT_AT_S10_READY"})
        return SimpleNamespace(returncode=0, stdout="second-blocked", stderr="")

    def mock_second_stage_contract_only(self, *args, **kwargs):
        self.write_json(self.second_stage_result_path, {"status": "CONTRACT_ONLY"})
        return SimpleNamespace(returncode=0, stdout="second-contract-only", stderr="")

    def mark_s10_ready(self):
        self.write_json(self.task_dir / "status.json", {"task_id": self.task_id, "status": "S10_READY", "source": "feishu"})
        self.write_json(
            self.task_dir / "first_stage_result.json",
            {"flow_state": {"S10_READY": True}, "same_source_cards": [{"id": 1}], "target_fingerprint": "unit-target"},
        )

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def write_send_ready_task(self, *, chat_id="oc_secret_chat_123456789", write_preview=True):
        status = {
            "task_id": self.task_id,
            "status": "MANUAL_REVIEW_CONFIRMED",
            "technical_status": "SUCCEEDED",
            "business_status": "MANUAL_REVIEW_CONFIRMED",
            "recommended_next_action": "ready-to-send",
        }
        if chat_id is not None:
            status["raw_chat_id"] = chat_id
        self.write_json(self.task_dir / "status.json", status)
        if write_preview:
            (self.task_dir / "feishu_result_reply.preview.txt").write_text(
                "【人工复核已确认】FS20260609_0001\n最终收车价：86000 元\n",
                encoding="utf-8",
            )


def full_chain_manual_review_payload(*, profit_yuan=7472, suggested_purchase_price_yuan=84928):
    return {
        "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "final_status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "current_state": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "target_vehicle": "Honda Accord 2021 260TURBO",
        "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "boundary_confirmed": False,
        "boundary_reference_index": None,
        "boundary_reference_score": None,
        "s17_payload": {
            "final_reference_index": 1,
            "reference_price_10k": 9.84,
            "reference_score": 94.0,
            "target_score": 94.5,
            "manual_review_required": True,
            "manual_review_reasons": [
                "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING",
                "SAMPLE_SHORTAGE_MANUAL_REVIEW",
            ],
        },
        "pricing": {
            "base_reference_price_yuan": 98400,
            "target_guazi_listing_price_yuan": 96400,
            "guazi_service_fee_yuan": 1500,
            "guazi_net_payout_yuan": 94900,
            "guazi_return_price_yuan": 94900,
            "cost_yuan": 1000,
            "profit_yuan": profit_yuan,
            "suggested_purchase_price_yuan": suggested_purchase_price_yuan,
            "manual_review_required": True,
        },
    }


def full_success_payload():
    return {
        "status": "SUCCEEDED",
        "final_status": "SUCCEEDED",
        "current_state": "SUCCEEDED",
        "target_vehicle": "Honda Accord 2021 260TURBO",
        "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "target_score": 94.5,
        "boundary_confirmed": True,
        "boundary_reference_index": 2,
        "boundary_reference_score": 95.0,
        "final_reference_index": 1,
        "final_reference_score": 94.0,
        "final_reference_price_yuan": 98400,
        "target_guazi_listing_price_yuan": 96400,
        "guazi_service_fee_yuan": 1500,
        "guazi_net_payout_yuan": 94900,
        "guazi_return_price_yuan": 94900,
        "cost_yuan": 1000,
        "profit_rate": 0.08,
        "profit_yuan": 7472,
        "suggested_purchase_price_yuan": 84928,
        "final_purchase_price_yuan": 84928,
        "manual_review_required": False,
    }


def terminal_success_with_stale_low_score_payload():
    payload = full_success_payload()
    payload.update(
        {
            "status": "FULL_CHAIN_PRICED_DONE",
            "final_status": "FULL_CHAIN_PRICED_DONE",
            "current_state": "FULL_CHAIN_PRICED_DONE",
            "business_status": "SUCCEEDED",
            "s16_status": "S16_READY",
            "selected_reference": {"reference_index": 3, "score": 91.0},
            "final_reference_index": 3,
            "final_purchase_price_yuan": 140156,
            "suggested_purchase_price_yuan": 140156,
            "system_suggested_price_yuan": 140156,
            "pricing_decision_source": "AUTOMATIC_PRICING",
            "final_price_source": "SYSTEM_AUTOMATIC_PRICING",
            "target_score": {"score": 92.0},
            "current_reference_index": 3,
            "current_reference": {
                "reference_index": 3,
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                "target_score": 92.0,
                "reference_score_upper_bound": 91.5,
                "s14_low_score_skip_triggered": True,
                "return_to_s10_after_low_score_skip": True,
                "returned_list_source_verified": True,
                "remaining_reference_count": 2,
                "early_exit_decision": {
                    "current_reference_index": 3,
                    "next_reference_index": 4,
                    "target_score": 92.0,
                    "reference_score_upper_bound": 91.5,
                    "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                    "s14_low_score_skip_triggered": True,
                    "early_exit_decision": "LOW_SCORE_SKIP_AND_CONTINUE_NEXT_REFERENCE",
                    "return_to_s10_after_low_score_skip": True,
                    "returned_list_source_verified": True,
                    "remaining_reference_count": 2,
                },
            },
            "s17_payload": {
                "task_status": "priced",
                "suggested_acquisition_price_yuan": 140156,
                "final_reference_index": 3,
            },
            "pricing": {
                "status": "priced",
                "suggested_purchase_price_yuan": 140156,
                "final_purchase_price_yuan": 140156,
                "pricing_decision_source": "AUTOMATIC_PRICING",
                "final_price_source": "SYSTEM_AUTOMATIC_PRICING",
            },
        }
    )
    return payload


if __name__ == "__main__":
    unittest.main()
