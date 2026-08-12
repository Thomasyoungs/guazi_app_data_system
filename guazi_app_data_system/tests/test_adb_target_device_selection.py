import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from guazi_app_data_system.adb_target_device import (  # noqa: E402
    TARGET_ADB_DEVICE_NOT_CONNECTED,
    TARGET_ADB_DEVICE_OFFLINE,
    TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT,
    TARGET_ADB_DEVICE_UNAUTHORIZED,
    TARGET_ADB_SERIAL_NOT_CONFIGURED,
    build_adb_command,
    load_target_adb_serial,
    validate_target_device_available,
)
from guazi_app_data_system.app_startup import AdbClient  # noqa: E402
from guazi_app_data_system.app_startup import ADBCommandResult  # noqa: E402
from feishu_task_store import FeishuTaskStore  # noqa: E402
from pricing_runner import PricingRunner  # noqa: E402
from pricing_runner import _first_stage_adb_evidence_from_payload  # noqa: E402
from runtime_s01_to_s10_mainline import (  # noqa: E402
    _raise_device_ready_gate,
    _run_first_stage_target_device_gate,
    GuaziFlowError,
)


class FakeIssueRecorder:
    def __init__(self) -> None:
        self.records = []

    def record(self, code, state_id, message, context, resolution=None):
        record = {"code": code, "state_id": state_id, "message": message, "context": context, "resolution": resolution}
        self.records.append(record)
        return record


class FakeAudit:
    def __init__(self) -> None:
        self.records = []

    def log(self, event, **payload):
        self.records.append({"event": event, **payload})


class FakeAdbClient:
    def __init__(self, devices_raw: str, *, serial: str = "6TGYHPZCETCSK6L") -> None:
        self.available = True
        self.adb_path = ROOT / "output" / "tmp_test" / "fake_adb" / "adb.exe"
        self.adb_path_source = "PATH"
        self.adb_serial = serial
        self.devices_raw = devices_raw
        self.calls: list[list[str]] = []

    def runtime_environment_snapshot(self):
        return {
            "target_adb_serial": self.adb_serial,
            "adb_serial_source": "config",
            "adb_path": str(self.adb_path),
            "adb_path_source": self.adb_path_source,
            "adb_runtime_env_mode": "inherited_user_environment",
            "use_isolated_adb_home": False,
            "adb_vendor_keys_configured": False,
            "adb_vendor_keys_path_summary": [],
            "adb_vendor_keys_exists": False,
            "output_adb_home_exists": False,
            "output_adb_home_android_dir_exists": False,
            "android_adb_server_port": None,
            "adb_command_preview": f"adb.exe -s {self.adb_serial} ...",
            "cwd": str(ROOT),
            "project_root": str(ROOT),
            "python_executable": sys.executable,
        }

    def run(self, args, timeout=20):
        self.calls.append(list(args))
        if args == ["version"]:
            return ADBCommandResult(["adb", "version"], 0, "Android Debug Bridge version 1.0.41\n", "")
        if args == ["devices", "-l"]:
            return ADBCommandResult(["adb", "devices", "-l"], 0, self.devices_raw, "")
        raise AssertionError(f"unexpected adb call: {args}")


def _runtime_python_sources() -> list[Path]:
    skipped = {"rule_source_sync_check.py"}
    return [
        path
        for path in [*ROOT.joinpath("scripts").glob("*.py"), *ROOT.joinpath("src").rglob("*.py")]
        if path.name not in skipped
    ]


class StrictTargetAdbDeviceSelectionTest(unittest.TestCase):
    def test_env_serial_overrides_config(self):
        with mock.patch.dict("os.environ", {"GUAZI_ADB_SERIAL": "ENV_SERIAL"}, clear=False):
            self.assertEqual(load_target_adb_serial(project_root=ROOT), "ENV_SERIAL")

    def test_build_adb_command_uses_configured_serial(self):
        command = build_adb_command(["shell", "input", "tap", "1", "2"], adb_path="adb.exe", active_serial="A_SERIAL")
        self.assertEqual(command, ["adb.exe", "-s", "A_SERIAL", "shell", "input", "tap", "1", "2"])

    def test_empty_serial_fails_even_if_only_one_device_is_connected(self):
        result = validate_target_device_available([{"serial": "ONLY_DEVICE", "status": "device"}], active_serial="")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], TARGET_ADB_SERIAL_NOT_CONFIGURED)

    def test_configured_serial_must_be_connected(self):
        result = validate_target_device_available([{"serial": "B", "status": "device"}], active_serial="A")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], TARGET_ADB_DEVICE_NOT_CONNECTED)

    def test_unauthorized_and_offline_are_not_allowed(self):
        unauthorized = validate_target_device_available([{"serial": "A", "status": "unauthorized"}], active_serial="A")
        offline = validate_target_device_available([{"serial": "A", "status": "offline"}], active_serial="A")
        self.assertEqual(unauthorized["code"], TARGET_ADB_DEVICE_UNAUTHORIZED)
        self.assertEqual(offline["code"], TARGET_ADB_DEVICE_OFFLINE)

    def test_multi_device_online_only_allows_configured_serial_in_commands(self):
        validation = validate_target_device_available(
            [{"serial": "A", "status": "device"}, {"serial": "B", "status": "device"}],
            active_serial="A",
        )
        self.assertTrue(validation["ok"])
        adb = ROOT / "output" / "tmp_test" / "strict_adb" / "adb.exe"
        adb.parent.mkdir(parents=True, exist_ok=True)
        adb.write_text("", encoding="utf-8")
        client = AdbClient(adb, adb_serial="A")
        with mock.patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([str(adb)], 0, "ok", "")
            client.run(["shell", "echo", "ok"])
        command = run.call_args.args[0]
        self.assertIn("-s", command)
        self.assertIn("A", command)
        self.assertNotIn("B", command)

    def test_missing_serial_does_not_run_device_command(self):
        adb = ROOT / "output" / "tmp_test" / "strict_adb_missing" / "adb.exe"
        adb.parent.mkdir(parents=True, exist_ok=True)
        adb.write_text("", encoding="utf-8")
        client = AdbClient(adb, adb_serial="")
        with mock.patch("subprocess.run") as run:
            result = client.run(["shell", "input", "tap", "1", "2"])
        self.assertFalse(result.success)
        self.assertEqual(result.returncode, 125)
        self.assertIn(TARGET_ADB_SERIAL_NOT_CONFIGURED, result.stderr)
        run.assert_not_called()

    def test_devices_listing_is_not_used_as_default_selection(self):
        adb = ROOT / "output" / "tmp_test" / "strict_adb_devices" / "adb.exe"
        adb.parent.mkdir(parents=True, exist_ok=True)
        adb.write_text("", encoding="utf-8")
        client = AdbClient(adb, adb_serial="")
        with mock.patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                [str(adb), "devices"],
                0,
                "List of devices attached\nONLY\tdevice\n",
                "",
            )
            devices = client.devices()
        self.assertEqual(devices[0]["serial"], "ONLY")
        self.assertEqual(run.call_args.args[0], [str(adb), "devices"])
        with mock.patch("subprocess.run"):
            self.assertIsNone(client.first_ready_device())

    def test_forbidden_adb_server_command_is_not_executed(self):
        adb = ROOT / "output" / "tmp_test" / "strict_adb_forbidden" / "adb.exe"
        adb.parent.mkdir(parents=True, exist_ok=True)
        adb.write_text("", encoding="utf-8")
        client = AdbClient(adb, adb_serial="A")
        with mock.patch("subprocess.run") as run:
            result = client.kill_server()
        self.assertFalse(result.success)
        run.assert_not_called()

    def test_no_uiautomator_default_connect_call_exists(self):
        runtime_sources = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in _runtime_python_sources()
        )
        self.assertNotIn("u2.connect()", runtime_sources)
        self.assertNotIn("uiautomator2.connect()", runtime_sources)

    def test_runtime_sources_do_not_contain_forbidden_adb_server_commands(self):
        runtime_sources = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in _runtime_python_sources()
        )
        self.assertNotIn("adb kill-server", runtime_sources)
        self.assertNotIn("adb disconnect", runtime_sources)

    def test_task_store_generates_concrete_business_feedback_for_target_adb_errors(self):
        base = ROOT / "output" / "tmp_test" / "strict_target_adb_task_store"
        task_id = "FS20260622_9999"
        task_dir = base / task_id
        if task_dir.exists():
            import shutil

            shutil.rmtree(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "status.json").write_text(
            '{"task_id":"FS20260622_9999","status":"QUEUED","started":false}\n',
            encoding="utf-8",
        )
        store = FeishuTaskStore(base)
        result = store.auto_cancel_not_started_system_precheck_failure(
            task_id,
            errors=["TARGET_ADB_SERIAL_NOT_CONFIGURED"],
            result={"ok": False, "errors": ["TARGET_ADB_SERIAL_NOT_CONFIGURED"]},
            force_not_started=True,
        )
        self.assertTrue(result.success)
        self.assertIn("未配置执行手机", result.reply_text)
        status = store.load_status(task_id)
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["canonical_error_code"], "TARGET_ADB_SERIAL_NOT_CONFIGURED")
        self.assertFalse(status["blocks_queue"])

    def test_pricing_runner_fails_closed_before_stage_subprocess_when_serial_missing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {"GUAZI_ADB_SERIAL": ""}, clear=False):
            root = Path(tmp)
            task_id = "FS20260622_9998"
            task_root = root / "data" / "feishu_tasks"
            task_dir = task_root / task_id
            task_dir.mkdir(parents=True)
            (task_dir / "status.json").write_text(
                '{"task_id":"FS20260622_9998","status":"QUEUED","queued_at":"2026-06-22T00:00:00+00:00"}\n',
                encoding="utf-8",
            )
            (root / "data").mkdir(exist_ok=True)
            (root / "data" / "current_target_task.json").write_text(
                '{"task_id":"FS20260622_9998"}\n',
                encoding="utf-8",
            )
            script = root / "scripts" / "runtime_s01_to_s10_mainline.py"
            script.parent.mkdir(parents=True)
            script.write_text("# fake\n", encoding="utf-8")
            runner = PricingRunner(
                task_root=task_root,
                data_dir=root / "data",
                runtime_lock_path=root / "runtime" / "pricing.lock",
            )
            with mock.patch("pricing_runner.subprocess.run", side_effect=AssertionError("must not run subprocess")) as run:
                result = runner.run_first_stage(task_id, allow_app_run=True, first_stage_script=script)
            self.assertFalse(result["ok"])
            self.assertIn("TARGET_ADB_SERIAL_NOT_CONFIGURED", result["errors"])
            self.assertFalse((root / "runtime" / "pricing.lock").exists())
            run.assert_not_called()

    def test_first_stage_gate_records_snapshot_when_target_device_exists(self):
        raw = "List of devices attached\n6TGYHPZCETCSK6L\tdevice product:x model:Redmi_Note_12_5G\n"
        fake_client = FakeAdbClient(raw)
        context = {"client": fake_client, "issues": FakeIssueRecorder(), "audit": FakeAudit(), "startup": {}}
        gate = _run_first_stage_target_device_gate(context)

        self.assertTrue(gate["passed"])
        self.assertEqual(gate["target_device_state"], "device")
        self.assertTrue(gate["target_device_present_before_first_stage"])
        self.assertIn("adb_devices_l_raw", gate)
        self.assertEqual(gate["parsed_devices"][0]["serial"], "6TGYHPZCETCSK6L")
        self.assertEqual(fake_client.calls, [["version"], ["devices", "-l"]])

    def test_first_stage_gate_fails_before_app_action_when_target_missing(self):
        raw = "List of devices attached\n1d76fbdd0923\tdevice\n"
        fake_client = FakeAdbClient(raw)
        context = {"client": fake_client, "issues": FakeIssueRecorder(), "audit": FakeAudit(), "startup": {}}

        with self.assertRaises(GuaziFlowError) as raised:
            _run_first_stage_target_device_gate(context)

        self.assertEqual(raised.exception.code, TARGET_ADB_DEVICE_NOT_CONNECTED)
        self.assertEqual(raised.exception.context["target_device_state"], "missing")
        self.assertIn("1d76fbdd0923", raised.exception.context["adb_devices_l_raw"])
        self.assertEqual(fake_client.calls, [["version"], ["devices", "-l"]])

    def test_first_stage_gate_fails_before_app_action_when_target_unauthorized_or_offline(self):
        for state, expected_code in (
            ("unauthorized", TARGET_ADB_DEVICE_UNAUTHORIZED),
            ("offline", TARGET_ADB_DEVICE_OFFLINE),
        ):
            with self.subTest(state=state):
                raw = f"List of devices attached\n6TGYHPZCETCSK6L\t{state}\n"
                fake_client = FakeAdbClient(raw)
                context = {"client": fake_client, "issues": FakeIssueRecorder(), "audit": FakeAudit(), "startup": {}}
                with self.assertRaises(GuaziFlowError) as raised:
                    _run_first_stage_target_device_gate(context)
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(raised.exception.context["target_device_state"], state)
                self.assertEqual(fake_client.calls, [["version"], ["devices", "-l"]])

    def test_gate_pass_then_capture_device_not_found_prioritizes_transient_disconnect(self):
        context = {
            "issues": FakeIssueRecorder(),
            "startup": {},
            "target_device_gate": {
                "passed": True,
                "target_device_state": "device",
                "target_adb_serial": "6TGYHPZCETCSK6L",
                "target_serial": "6TGYHPZCETCSK6L",
            },
            "adb_env_snapshot": {
                "target_adb_serial": "6TGYHPZCETCSK6L",
                "adb_path": "adb.exe",
                "adb_path_source": "PATH",
                "adb_runtime_env_mode": "inherited_user_environment",
            },
        }
        snapshot = {
            "screenshot_error": "error: device '6TGYHPZCETCSK6L' not found",
            "xml_dump_error": "",
            "visible_texts": [],
            "nodes": [],
        }

        with self.assertRaises(GuaziFlowError) as raised:
            _raise_device_ready_gate(
                context,
                snapshot,
                code="APP_ICON_NOT_FOUND_AFTER_DEVICE_READY",
                message="icon not found",
                failed_action="tap_guazi_app_icon",
            )

        self.assertEqual(raised.exception.code, TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT)
        self.assertTrue(raised.exception.context["later_capture_device_not_found"])
        self.assertEqual(raised.exception.context["gate_target_device_state"], "device")
        self.assertEqual(raised.exception.context["original_stop_code_before_adb_priority"], "APP_ICON_NOT_FOUND_AFTER_DEVICE_READY")

    def test_adb_runtime_snapshot_does_not_leak_vendor_key_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_adb = root / "adb.exe"
            fake_adb.write_text("", encoding="utf-8")
            adb_key = root / "adbkey"
            secret = "-----BEGIN PRIVATE KEY-----\nDO_NOT_LEAK_THIS_SECRET\n-----END PRIVATE KEY-----\n"
            adb_key.write_text(secret, encoding="utf-8")
            with mock.patch.dict("os.environ", {"ADB_VENDOR_KEYS": str(adb_key)}, clear=False):
                client = AdbClient(fake_adb, adb_serial="6TGYHPZCETCSK6L")
                snapshot = client.runtime_environment_snapshot()
        encoded = json_dumps_for_test(snapshot)
        self.assertTrue(snapshot["adb_vendor_keys_configured"])
        self.assertTrue(snapshot["adb_vendor_keys_exists"])
        self.assertIn("adbkey", encoded)
        self.assertNotIn("DO_NOT_LEAK_THIS_SECRET", encoded)
        self.assertNotIn("BEGIN PRIVATE KEY", encoded)

    def test_pricing_runner_copies_first_stage_adb_evidence_to_run_meta(self):
        payload = {
            "target_adb_serial": "6TGYHPZCETCSK6L",
            "adb_path": "C:/platform-tools/adb.exe",
            "adb_path_source": "PATH",
            "adb_runtime_env_mode": "inherited_user_environment",
            "target_device_state": "device",
            "context": {"python_executable": sys.executable},
        }
        evidence = _first_stage_adb_evidence_from_payload(payload)
        self.assertEqual(evidence["target_adb_serial"], "6TGYHPZCETCSK6L")
        self.assertEqual(evidence["adb_path_source"], "PATH")
        self.assertEqual(evidence["python_executable"], sys.executable)

    def test_transient_disconnect_business_feedback_is_concrete_and_hides_internal_terms(self):
        base = ROOT / "output" / "tmp_test" / "transient_disconnect_task_store"
        task_id = "FS20260622_9997"
        task_dir = base / task_id
        if task_dir.exists():
            import shutil

            shutil.rmtree(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "status.json").write_text(
            '{"task_id":"FS20260622_9997","status":"QUEUED","started":false}\n',
            encoding="utf-8",
        )
        store = FeishuTaskStore(base)
        result = store.auto_cancel_not_started_system_precheck_failure(
            task_id,
            errors=[TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT],
            result={
                "status": TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT,
                "target_adb_serial": "6TGYHPZCETCSK6L",
                "adb_path": "C:/platform-tools/adb.exe",
                "adb_path_source": "PATH",
                "adb_runtime_env_mode": "inherited_user_environment",
                "target_device_state": "device",
            },
            force_not_started=True,
        )
        self.assertTrue(result.success)
        self.assertIn("连接不稳定", result.reply_text)
        forbidden = (
            "PowerShell",
            "dispatcher",
            "runner",
            "adb",
            "uiautomator",
            "status.json",
            "current_target_task.json",
            "run_id",
            "generation_id",
            "pricing.lock",
            "first_stage_result",
            "first_stage_run_meta",
            "ADMIN_INTERVENTION_TASK_EXISTS",
            "SYSTEM_BLOCKED",
            "UNKNOWN_PRECHECK_FAILED",
            "ADB_VENDOR_KEYS",
            "output/adb_home",
            "adb_path",
            "device_snapshot",
        )
        for term in forbidden:
            self.assertNotIn(term, result.reply_text)


def json_dumps_for_test(payload):
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
