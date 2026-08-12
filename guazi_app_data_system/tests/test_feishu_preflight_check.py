import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_preflight_check import format_preflight_report, mask_secret, run_preflight  # noqa: E402
import feishu_realtime_receiver  # noqa: E402


class FeishuPreflightCheckTest(unittest.TestCase):
    def test_missing_app_id_returns_env_missing(self):
        result = run_preflight(
            project_root=ROOT,
            cwd=ROOT,
            env={"FEISHU_APP_SECRET": "secret_value"},
            sdk_installed=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "FEISHU_ENV_MISSING")
        self.assertIn("FEISHU_ENV_MISSING", result["errors"])

    def test_missing_app_secret_returns_env_missing(self):
        result = run_preflight(
            project_root=ROOT,
            cwd=ROOT,
            env={"FEISHU_APP_ID": "cli_xxxxxxxxxxxxx"},
            sdk_installed=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "FEISHU_ENV_MISSING")
        self.assertIn("FEISHU_ENV_MISSING", result["errors"])

    def test_secret_report_is_masked(self):
        secret = "B88abcdefTx4"
        result = run_preflight(
            project_root=ROOT,
            cwd=ROOT,
            env={"FEISHU_APP_ID": "cli_xxxxxxxxxxxxx", "FEISHU_APP_SECRET": secret},
            sdk_installed=True,
        )
        report = format_preflight_report(result)

        self.assertEqual(mask_secret(secret), "B88****Tx4")
        self.assertIn("FEISHU_APP_SECRET masked: B88****Tx4", report)
        self.assertNotIn(secret, report)

    def test_self_check_does_not_connect_or_send(self):
        fake_result = {
            "ok": True,
            "checks": {
                "cwd_is_project_root": True,
                "python_available": True,
                "sdk_installed": True,
                "feishu_app_id_set": True,
                "feishu_app_secret_set": True,
                "feishu_app_id_cli_prefix": True,
                "feishu_app_secret_non_empty": True,
                "feishu_app_secret_masked": "abc****xyz",
                "receiver_exists": True,
                "gateway_exists": True,
                "send_message_exists": True,
                "data_feishu_tasks_exists": False,
                "current_target_task_exists": False,
                "pricing_lock_exists": False,
                "next_command": "python scripts/feishu_realtime_receiver.py --listen",
            },
            "warnings": [],
            "errors": [],
        }
        with patch.object(feishu_realtime_receiver, "run_preflight", return_value=fake_result) as preflight:
            with patch.object(feishu_realtime_receiver, "listen_forever", side_effect=AssertionError("must not listen")):
                with patch.object(feishu_realtime_receiver, "send_text_message", side_effect=AssertionError("must not send")):
                    with patch("sys.stdout", new_callable=io.StringIO):
                        code = feishu_realtime_receiver.main(["--self-check"])

        self.assertEqual(code, 0)
        preflight.assert_called_once_with()

    def test_self_check_source_has_no_app_or_device_commands(self):
        preflight_source = (SCRIPT_DIR / "feishu_preflight_check.py").read_text(encoding="utf-8").lower()
        receiver_source = (SCRIPT_DIR / "feishu_realtime_receiver.py").read_text(encoding="utf-8").lower()
        combined = preflight_source + "\n" + receiver_source

        self.assertNotIn("subprocess", preflight_source)
        self.assertNotIn("adb", combined)
        self.assertNotIn("uiautomator", combined)
        self.assertNotIn("runtime_s10_to_s16_mainline", combined)
        self.assertNotIn("全程跑通", combined)

    def test_env_example_only_uses_placeholders(self):
        content = (ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertEqual(
            content.strip().splitlines(),
            [
                "FEISHU_APP_ID=cli_xxxxxxxxxxxxx",
                "FEISHU_APP_SECRET=your_new_app_secret_here",
                "FEISHU_TEST_CHAT_ID=optional_chat_id_here",
            ],
        )

    def test_docs_do_not_contain_non_placeholder_app_secret_assignment(self):
        docs = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "docs").glob("feishu*.md"))

        for line in docs.splitlines():
            if "FEISHU_APP_SECRET" not in line or "=" not in line:
                continue
            self.assertTrue(
                any(placeholder in line for placeholder in ("your_new_app_secret_here", "<your_app_secret>")),
                msg="FEISHU_APP_SECRET examples must use placeholders only.",
            )

    def test_gitignore_contains_env_entry(self):
        content = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

        self.assertIn(".env", content)
        self.assertIn("*.secret", content)
        self.assertIn("secrets.json", content)
        self.assertIn("config/pricing_runner.local.json", content)
        self.assertIn("config/feishu.local.json", content)


if __name__ == "__main__":
    unittest.main()
