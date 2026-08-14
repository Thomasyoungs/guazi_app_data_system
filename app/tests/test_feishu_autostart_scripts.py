import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DOC = ROOT / "docs" / "feishu_autostart_listener.md"

SCRIPT_FILES = [
    SCRIPTS / "start_feishu_listener.ps1",
    SCRIPTS / "start_feishu_dispatcher.ps1",
    SCRIPTS / "feishu_service_single_instance.ps1",
    SCRIPTS / "install_feishu_listener_task.ps1",
    SCRIPTS / "uninstall_feishu_listener_task.ps1",
    SCRIPTS / "check_feishu_listener_task.ps1",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class FeishuAutostartScriptsTest(unittest.TestCase):
    def test_required_scripts_exist(self):
        for path in SCRIPT_FILES:
            self.assertTrue(path.exists(), path)

    def test_start_script_runs_self_check_and_listen_only(self):
        text = read(SCRIPTS / "start_feishu_listener.ps1")

        self.assertIn("feishu_realtime_receiver.py --self-check", text)
        self.assertIn("feishu_realtime_receiver.py --listen", text)
        self.assertIn("logs", text)
        self.assertIn("feishu_listener_startup.log", text)
        self.assertIn("feishu_listener_runtime.log", text)
        self.assertIn("FEISHU_APP_ID", text)
        self.assertIn("FEISHU_APP_SECRET", text)
        self.assertIn("Stop-ProjectScopedServiceProcesses", text)
        self.assertIn("Assert-SingleProjectServiceInstance", text)

    def test_dispatcher_start_script_runs_single_instance_loop(self):
        text = read(SCRIPTS / "start_feishu_dispatcher.ps1")

        self.assertIn("feishu_pricing_dispatcher.py", text)
        self.assertIn("--loop", text)
        self.assertIn("--allow-app-run", text)
        self.assertIn("feishu_dispatcher_autostart.log", text)
        self.assertIn("Stop-ProjectScopedServiceProcesses", text)
        self.assertIn("Assert-SingleProjectServiceInstance", text)
        self.assertIn("start_feishu_dispatcher.ps1", text)

    def test_single_instance_helper_uses_project_scoped_win32_matching(self):
        text = read(SCRIPTS / "feishu_service_single_instance.ps1")

        self.assertIn("Get-CimInstance Win32_Process", text)
        self.assertIn("CommandLine", text)
        self.assertIn("ParentProcessId", text)
        self.assertIn("Normalize-ServiceProjectRoot", text)
        self.assertIn("Stop-Process -Id", text)
        self.assertIn("ExcludeProcessIds", text)
        self.assertIn("Expected exactly one", text)
        self.assertNotIn("Stop-Process python", text)
        self.assertNotIn("Stop-Process -Name python", text)

    def test_single_instance_scripts_do_not_match_app_runner_or_device_tools(self):
        forbidden = [
            "runtime_s01_to_s10_mainline.py",
            "runtime_s10_to_s16_mainline.py",
            "pricing_runner.py",
            "adb",
            "uiautomator",
        ]
        for path in [
            SCRIPTS / "start_feishu_listener.ps1",
            SCRIPTS / "start_feishu_dispatcher.ps1",
            SCRIPTS / "feishu_service_single_instance.ps1",
        ]:
            text = read(path).lower()
            self.assertNotIn("stop-process python", text, path.name)
            for token in forbidden:
                self.assertNotIn(token.lower(), text, f"{token} found in {path.name}")

    def test_single_instance_scripts_match_only_service_targets(self):
        listener = read(SCRIPTS / "start_feishu_listener.ps1")
        dispatcher = read(SCRIPTS / "start_feishu_dispatcher.ps1")

        self.assertIn('@("feishu_realtime_receiver.py", "start_feishu_listener.ps1")', listener)
        self.assertIn('@("feishu_pricing_dispatcher.py", "start_feishu_dispatcher.ps1")', dispatcher)
        self.assertIn("ProjectRoot=$ProjectRoot", listener)
        self.assertIn("ProjectRoot=$ProjectRoot", dispatcher)

    def test_install_script_contains_expected_task_registration(self):
        text = read(SCRIPTS / "install_feishu_listener_task.ps1")

        self.assertIn("GuaziFeishuListener", text)
        self.assertIn("-ExecutionPolicy Bypass", text)
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", text)
        self.assertIn("PT30S", text)
        self.assertIn("-Force", text)
        self.assertIn("FEISHU_LISTENER_TASK_INSTALLED", text)

    def test_uninstall_and_check_scripts_contain_expected_task_name(self):
        uninstall = read(SCRIPTS / "uninstall_feishu_listener_task.ps1")
        check = read(SCRIPTS / "check_feishu_listener_task.ps1")

        self.assertIn("GuaziFeishuListener", uninstall)
        self.assertIn("GuaziFeishuListener", check)
        self.assertIn("FEISHU_LISTENER_TASK_UNINSTALLED", uninstall)
        self.assertIn("LastRunTime", check)
        self.assertIn("LastTaskResult", check)
        self.assertIn("feishu_listener_startup.log", check)
        self.assertIn("feishu_listener_runtime.log", check)

    def test_scripts_do_not_contain_forbidden_runtime_entries_or_device_tools(self):
        forbidden = [
            "--run-first-stage",
            "--run-second-stage",
            "--run-manual",
            "runtime_s01_to_s10_mainline.py",
            "runtime_s10_to_s16_mainline.py",
            "pricing_runner.py",
            "uiautomator",
        ]
        for path in SCRIPT_FILES:
            text = read(path).lower()
            for token in forbidden:
                self.assertNotIn(token.lower(), text, f"{token} found in {path.name}")

    def test_scripts_do_not_contain_real_app_secret(self):
        suspicious_values = [
            "cli_a",
            "app_secret",
            "secret=",
            "sk-",
            "Bearer ",
            "eyJ",
        ]
        for path in SCRIPT_FILES:
            text = read(path)
            for token in suspicious_values:
                self.assertNotIn(token, text, f"{token} found in {path.name}")

    def test_docs_state_autostart_boundaries_and_secret_policy(self):
        text = read(DOC)

        self.assertIn("不会自动启动瓜子 APP", text)
        self.assertIn("不会自动跑第一段", text)
        self.assertIn("不会自动跑第二段", text)
        self.assertIn("不会自动定价", text)
        self.assertIn("不要把 `FEISHU_APP_ID` 或 `FEISHU_APP_SECRET` 写进脚本", text)
        self.assertIn("如果 App Secret 泄露", text)
        self.assertIn("python scripts/feishu_realtime_receiver.py --listen", text)


if __name__ == "__main__":
    unittest.main()
