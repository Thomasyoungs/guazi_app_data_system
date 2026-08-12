import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guazi_app_data_system import app_startup  # noqa: E402
from guazi_app_data_system.app_startup import AdbClient  # noqa: E402


def make_test_root(name: str) -> Path:
    root = ROOT / "output" / "tmp_test" / "phone_connection" / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


class PhoneConnectionAdbEnvironmentTest(unittest.TestCase):
    def test_adb_client_default_env_inherits_user_powershell_env(self):
        root = make_test_root("inherits_user_env")
        adb = root / "adb.exe"
        adb.write_text("", encoding="utf-8")
        client = AdbClient(adb, adb_serial="TEST_SERIAL")
        user_env = {
            "ANDROID_SDK_HOME": r"C:\Users\lzc93\AppData\Local\Android\Sdk",
            "ANDROID_USER_HOME": r"C:\Users\lzc93\.android",
            "HOME": r"C:\Users\lzc93",
            "USERPROFILE": r"C:\Users\lzc93",
            "HOMEDRIVE": "C:",
            "HOMEPATH": r"\Users\lzc93",
            "APPDATA": r"C:\Users\lzc93\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\lzc93\AppData\Local",
        }

        with mock.patch.dict("os.environ", user_env, clear=True):
            with mock.patch("subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([str(adb), "version"], 0, "ok", "")
                client.run(["version"])

        env = run.call_args.kwargs["env"]
        for key, value in user_env.items():
            self.assertEqual(env[key], value)
        self.assertNotIn(str(ROOT / "output" / "adb_home"), str(env))

    def test_adb_client_default_env_does_not_add_adb_vendor_keys(self):
        root = make_test_root("no_vendor_key")
        adb = root / "adb.exe"
        adb.write_text("", encoding="utf-8")
        adbkey = root / "user" / ".android" / "adbkey"
        adbkey.parent.mkdir(parents=True)
        adbkey.write_text("key", encoding="utf-8")
        client = AdbClient(adb, adb_serial="TEST_SERIAL")
        client.ADB_VENDOR_KEY = adbkey

        with mock.patch.dict("os.environ", {"USERPROFILE": r"C:\Users\lzc93"}, clear=True):
            with mock.patch("subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([str(adb), "devices"], 0, "", "")
                client.run(["devices"])

        self.assertNotIn("ADB_VENDOR_KEYS", run.call_args.kwargs["env"])

    def test_adb_client_preserves_user_adb_vendor_keys_when_explicitly_set(self):
        root = make_test_root("preserve_vendor_key")
        adb = root / "adb.exe"
        adb.write_text("", encoding="utf-8")
        client = AdbClient(adb, adb_serial="TEST_SERIAL")

        with mock.patch.dict("os.environ", {"ADB_VENDOR_KEYS": r"C:\Users\lzc93\.android\adbkey"}, clear=True):
            with mock.patch("subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([str(adb), "devices"], 0, "", "")
                client.run(["devices"])

        self.assertEqual(run.call_args.kwargs["env"]["ADB_VENDOR_KEYS"], r"C:\Users\lzc93\.android\adbkey")

    def test_adb_run_binds_serial_without_using_old_phone(self):
        root = make_test_root("serial_binding")
        adb = root / "adb.exe"
        adb.write_text("", encoding="utf-8")
        client = AdbClient(adb, adb_serial="6TGYHPZCETCSK6L")

        with mock.patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([str(adb)], 0, "ok", "")
            client.run(["shell", "echo", "ok"])

        command = run.call_args.args[0]
        self.assertEqual(command[:3], [str(adb), "-s", "6TGYHPZCETCSK6L"])
        self.assertNotIn("1d76fbdd0923", command)

    def test_find_adb_prefers_path_before_project_fallback(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(app_startup.shutil, "which", return_value=r"C:\platform-tools\adb.exe"):
                self.assertEqual(AdbClient.find_adb(), Path(r"C:\platform-tools\adb.exe"))

    def test_find_adb_uses_project_adb_only_as_fallback(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(app_startup.shutil, "which", return_value=None):
                found = AdbClient.find_adb()

        self.assertIsNotNone(found)
        self.assertTrue(str(found).endswith(r"tools\android-platform-tools\platform-tools\adb.exe"))

    def test_adb_path_env_remains_explicit_override(self):
        root = make_test_root("env_adb_path")
        adb = root / "adb.exe"
        adb.write_text("", encoding="utf-8")

        with mock.patch.dict("os.environ", {"ADB_PATH": str(adb)}, clear=True):
            self.assertEqual(AdbClient.find_adb(), adb)

    def test_legacy_isolated_adb_home_is_opt_in_only(self):
        root = make_test_root("legacy_opt_in")
        adb = root / "adb.exe"
        adb.write_text("", encoding="utf-8")
        client = AdbClient(adb, adb_serial="TEST_SERIAL")
        client.ADB_HOME = root / "output" / "adb_home"

        with mock.patch.dict("os.environ", {"GUAZI_USE_ISOLATED_ADB_HOME": "1"}, clear=True):
            env = client.adb_environment()

        self.assertEqual(env["ANDROID_SDK_HOME"], str(root / "output" / "adb_home"))
        self.assertEqual(env["ANDROID_USER_HOME"], str(root / "output" / "adb_home"))
        self.assertEqual(env["HOME"], str(root / "output" / "adb_home"))
        self.assertEqual(env["USERPROFILE"], str(root / "output" / "adb_home"))

    def test_screenshot_uses_adbclient_run_stdout_path(self):
        root = make_test_root("screenshot_run")
        adb = root / "adb.exe"
        adb.write_text("", encoding="utf-8")
        client = AdbClient(adb, adb_serial="TEST_SERIAL")
        screenshot_path = root / "artifacts" / "screen.png"

        with mock.patch.object(client, "run") as run:
            run.return_value = mock.Mock(success=True, stdout=str(screenshot_path), stderr="", returncode=0)
            result = client.screenshot(screenshot_path)

        self.assertTrue(result.success)
        run.assert_called_once_with(["exec-out", "screencap", "-p"], timeout=20, stdout_path=screenshot_path)

    def test_dump_ui_xml_uses_adbclient_run(self):
        root = make_test_root("xml_run")
        adb = root / "adb.exe"
        adb.write_text("", encoding="utf-8")
        client = AdbClient(adb, adb_serial="TEST_SERIAL")

        with mock.patch.object(client, "run") as run:
            run.side_effect = [
                mock.Mock(success=True, stdout="dumped", stderr="", returncode=0),
                mock.Mock(success=True, stdout="<hierarchy />", stderr="", returncode=0),
            ]
            xml = client.dump_ui_xml()

        self.assertEqual(xml, "<hierarchy />")
        self.assertEqual(run.call_args_list[0].args[0], ["shell", "uiautomator", "dump", "/sdcard/window.xml"])
        self.assertEqual(run.call_args_list[1].args[0], ["exec-out", "cat", "/sdcard/window.xml"])


if __name__ == "__main__":
    unittest.main()
