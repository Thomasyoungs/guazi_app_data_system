import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
import sys

sys.path.insert(0, str(SCRIPT_DIR))

from service_reload_evidence_validator import (  # noqa: E402
    build_post_start_feedback_classification_load_check,
    build_service_process_snapshot,
    compress_directory_with_posix_paths,
    validate_chatgpt_review_package,
)


class ServiceReloadEvidenceValidatorTest(unittest.TestCase):
    def test_dispatcher_count_ignores_powershell_evidence_collector_text(self):
        root = r"C:\Users\lzc93\Desktop\定价\guazi_app_data_system"
        snapshot = build_service_process_snapshot(
            [
                {
                    "ProcessId": 7404,
                    "ParentProcessId": 1,
                    "ExecutablePath": r"C:\Python312\python.exe",
                    "CommandLine": rf'"C:\Python312\python.exe" "{root}\scripts\feishu_pricing_dispatcher.py" --loop',
                },
                {
                    "ProcessId": 9000,
                    "ParentProcessId": 1,
                    "ExecutablePath": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                    "CommandLine": rf'powershell -Command "$x = \"feishu_pricing_dispatcher.py\"; $root = \"{root}\""',
                },
            ],
            project_root=root,
        )

        self.assertEqual(snapshot["dispatcher_count"], 1)
        self.assertEqual(snapshot["dispatchers"][0]["ProcessId"], 7404)
        self.assertEqual(len(snapshot["evidence_collectors"]), 1)
        self.assertEqual(snapshot["evidence_collectors"][0]["ProcessId"], 9000)

    def test_listener_count_requires_python_process(self):
        root = r"C:\Users\lzc93\Desktop\定价\guazi_app_data_system"
        snapshot = build_service_process_snapshot(
            [
                {
                    "ProcessId": 100,
                    "ExecutablePath": r"C:\Python312\python.exe",
                    "CommandLine": rf'python "{root}\scripts\feishu_realtime_receiver.py" --listen',
                },
                {
                    "ProcessId": 101,
                    "ExecutablePath": r"C:\Windows\System32\cmd.exe",
                    "CommandLine": rf'echo "{root}\scripts\feishu_realtime_receiver.py"',
                },
            ],
            project_root=root,
        )

        self.assertEqual(snapshot["listener_count"], 1)
        self.assertEqual(snapshot["listeners"][0]["ProcessId"], 100)
        self.assertEqual(len(snapshot["evidence_collectors"]), 1)

    def test_package_validation_accepts_backslash_zip_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "package"
            reports = package / "reports"
            reports.mkdir(parents=True)
            for rel in (
                "FULL_EVIDENCE_FOR_CHATGPT_REVIEW.txt",
                "ROOT_CAUSE_EVIDENCE_INDEX.md",
                "ROOT_CAUSE_REPORT.md",
                "CHATGPT_COPYABLE_SUMMARY.txt",
                "SERVICE_RELOAD_STATUS_REPORT.md",
            ):
                (reports / rel).write_text("ok", encoding="utf-8")
            for dirname in (
                "original_evidence_zip",
                "extracted_evidence",
                "raw_task_artifacts",
                "logs",
                "screenshots_and_xml",
                "traces",
                "tests",
            ):
                folder = package / dirname
                folder.mkdir()
                (folder / "README.txt").write_text("ok", encoding="utf-8")
            zip_path = Path(temp) / "package.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                for path in package.rglob("*"):
                    if path.is_file():
                        zf.write(path, str(path.relative_to(package)))

            result = validate_chatgpt_review_package(package, zip_path)

            self.assertEqual(result["missing_required_files"], [])
            self.assertEqual(result["missing_required_dirs"], [])
            self.assertTrue(result["can_upload_to_chatgpt"])

    def test_compress_directory_writes_forward_slash_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "package"
            (package / "reports").mkdir(parents=True)
            (package / "reports" / "FULL_EVIDENCE_FOR_CHATGPT_REVIEW.txt").write_text(
                "ok", encoding="utf-8"
            )
            zip_path = Path(temp) / "package.zip"

            compress_directory_with_posix_paths(package, zip_path)

            with zipfile.ZipFile(zip_path, "r") as zf:
                entries = [item.filename for item in zf.infolist()]
            self.assertIn("reports/FULL_EVIDENCE_FOR_CHATGPT_REVIEW.txt", entries)
            self.assertTrue(all("\\" not in item for item in entries))

    def test_post_start_feedback_load_check_uses_structured_markers(self):
        result = build_post_start_feedback_classification_load_check(ROOT)

        self.assertTrue(result["post_start_feedback_classification_loaded"])
        self.assertTrue(result["task_store_has_post_start_duplicate_message"])
        self.assertTrue(result["task_store_required_markers"]["primary_error_code"])
        self.assertTrue(result["task_store_required_markers"]["post_start_failure_stage"])
        self.assertTrue(result["formatter_required_markers"]["duplicate_business_message"])

    def test_post_start_feedback_load_check_detects_missing_duplicate_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "feishu_task_store.py").write_text(
                "\n".join(
                    [
                        "post_start_failure",
                        "post_start_failure_stage",
                        "primary_error_code",
                        "wrapper_error_code",
                        "pricing_result_issue_code",
                        "binding_stop_code",
                        "second_stage_entered",
                        "reached_s10_before_failure",
                        "entered_s11_before_failure",
                        "post_start_not_started_template_blocked",
                        "系统已开始自动定价，但在参考车采集阶段未能形成完整结果，已安全停止，已通知管理员处理。",
                    ]
                ),
                encoding="utf-8",
            )
            (scripts / "feishu_result_formatter.py").write_text(
                "DUPLICATE_REFERENCE_CLICK_BLOCKED\nRESULT_SCHEMA_INVALID_FOR_PRICING",
                encoding="utf-8",
            )
            (scripts / "feishu_pricing_dispatcher.py").write_text("send_result_live", encoding="utf-8")

            result = build_post_start_feedback_classification_load_check(root)

            self.assertFalse(result["post_start_feedback_classification_loaded"])
            self.assertFalse(result["task_store_has_post_start_duplicate_message"])


if __name__ == "__main__":
    unittest.main()
