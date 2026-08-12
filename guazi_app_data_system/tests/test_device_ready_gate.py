import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
SRC_DIR = ROOT / "src"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SRC_DIR))

from device_ready_gate import (  # noqa: E402
    ADB_DEVICE_NOT_CONNECTED,
    DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE,
    DEVICE_READY_RECOVERY_LADDER_VERSION,
    DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY,
    FAST_ENTRY_RECOVERED,
    RECOVERABLE_LOCK_OR_NOTIFICATION_SHADE,
    SECURE_KEYGUARD_LOCKED,
    check_device_ready_for_pricing,
)
from guazi_app_data_system.app_startup import ADBCommandResult, GUAZI_PACKAGE  # noqa: E402


class FakeAdbClient:
    def __init__(
        self,
        *,
        devices_output=None,
        power_state=None,
        window_dump="",
        window_policy="",
        activity_dump="",
        serial="target-serial",
    ):
        self.available = True
        self.adb_path = "adb.exe"
        self.adb_path_source = "path"
        self.adb_serial = serial
        self.devices_output = devices_output or "List of devices attached\ntarget-serial\tdevice product:test\n"
        self._power_state = power_state or {"wakefulness": "Awake", "interactive": True, "display_state": "ON", "raw": ""}
        self.window_dump = window_dump
        self.window_policy = window_policy
        self.activity_dump = activity_dump

    def runtime_environment_snapshot(self):
        return {
            "target_adb_serial": self.adb_serial,
            "adb_path": self.adb_path,
            "adb_path_source": self.adb_path_source,
            "adb_runtime_env_mode": "inherited_user_environment",
            "adb_command_preview": f"adb.exe -s {self.adb_serial} ...",
        }

    def power_state(self):
        return dict(self._power_state)

    def run(self, args, timeout=20, stdout_path=None):
        if args == ["version"]:
            return ADBCommandResult(["adb", *args], 0, "Android Debug Bridge version 1.0.41\n", "")
        if args == ["devices", "-l"]:
            return ADBCommandResult(["adb", *args], 0, self.devices_output, "")
        if args == ["shell", "dumpsys", "window"]:
            return ADBCommandResult(["adb", *args], 0, self.window_dump, "")
        if args == ["shell", "dumpsys", "window", "policy"]:
            return ADBCommandResult(["adb", *args], 0, self.window_policy, "")
        if args == ["shell", "dumpsys", "activity", "top"]:
            return ADBCommandResult(["adb", *args], 0, self.activity_dump, "")
        return ADBCommandResult(["adb", *args], 0, "", "")


class StatefulDeviceReadyClient(FakeAdbClient):
    def __init__(
        self,
        *,
        screen_on=False,
        keyguard_showing=True,
        secure_keyguard=False,
        input_restricted=True,
        notification_shade=True,
        foreground_package="com.miui.home",
        focused_window="NotificationShade",
        resumed_activity="com.miui.home/.launcher.Launcher",
        swipe_exits_shade=False,
        back_exits_shade=False,
        home_exits_shade=True,
        launch_foregrounds_guazi=True,
        launch_keeps_shade=False,
    ):
        super().__init__()
        self.device_ready_settle_seconds = 0
        self.action_log = []
        self.swipe_exits_shade = swipe_exits_shade
        self.back_exits_shade = back_exits_shade
        self.home_exits_shade = home_exits_shade
        self.launch_foregrounds_guazi = launch_foregrounds_guazi
        self.launch_keeps_shade = launch_keeps_shade
        self.state = {
            "screen_on": screen_on,
            "interactive": screen_on,
            "keyguard_showing": keyguard_showing,
            "secure_keyguard": secure_keyguard,
            "input_restricted": input_restricted,
            "notification_shade": notification_shade,
            "foreground_package": foreground_package,
            "focused_window": focused_window,
            "resumed_activity": resumed_activity,
        }

    def power_state(self):
        return {
            "wakefulness": "Awake" if self.state["screen_on"] else "Asleep",
            "interactive": bool(self.state["interactive"]),
            "display_state": "ON" if self.state["screen_on"] else "OFF",
            "raw": "",
        }

    def screen_size(self):
        return 1080, 2400

    def _exit_to_launcher(self):
        self.state.update(
            {
                "screen_on": True,
                "interactive": True,
                "keyguard_showing": False,
                "input_restricted": False,
                "notification_shade": False,
                "foreground_package": "com.miui.home",
                "focused_window": "com.miui.home/com.miui.home.launcher.Launcher",
                "resumed_activity": "com.miui.home/.launcher.Launcher",
            }
        )

    def _enter_guazi(self):
        self.state.update(
            {
                "screen_on": True,
                "interactive": True,
                "keyguard_showing": False,
                "input_restricted": False,
                "notification_shade": False,
                "foreground_package": GUAZI_PACKAGE,
                "focused_window": f"{GUAZI_PACKAGE}/.MainActivity",
                "resumed_activity": f"{GUAZI_PACKAGE}/.MainActivity",
            }
        )

    def _window_dump(self):
        focused = "NotificationShade" if self.state["notification_shade"] else self.state["focused_window"]
        return "\n".join(
            [
                f"mCurrentFocus=Window{{abc u0 {focused}}}",
                f"mKeyguardShowing={'true' if self.state['keyguard_showing'] else 'false'}",
                f"isKeyguardShowing={'true' if self.state['keyguard_showing'] else 'false'}",
                f"isKeyguardSecure={'true' if self.state['secure_keyguard'] else 'false'}",
                f"mInputRestricted={'true' if self.state['input_restricted'] else 'false'}",
                "StatusBarNotificationPresenter visible" if self.state["notification_shade"] else "",
            ]
        )

    def _activity_dump(self):
        return f"mResumedActivity: ActivityRecord{{abc u0 {self.state['resumed_activity']} t1}}"

    def run(self, args, timeout=20, stdout_path=None):
        if args == ["version"]:
            return ADBCommandResult(["adb", *args], 0, "Android Debug Bridge version 1.0.41\n", "")
        if args == ["devices", "-l"]:
            return ADBCommandResult(["adb", *args], 0, self.devices_output, "")
        if args == ["shell", "dumpsys", "window"]:
            return ADBCommandResult(["adb", *args], 0, self._window_dump(), "")
        if args == ["shell", "dumpsys", "window", "policy"]:
            return ADBCommandResult(["adb", *args], 0, self._window_dump(), "")
        if args == ["shell", "dumpsys", "activity", "top"]:
            return ADBCommandResult(["adb", *args], 0, self._activity_dump(), "")
        if args[:3] == ["shell", "input", "keyevent"]:
            key = args[3] if len(args) > 3 else ""
            self.action_log.append(key)
            if key == "KEYCODE_WAKEUP":
                self.state["screen_on"] = True
                self.state["interactive"] = True
            elif key == "BACK" and self.back_exits_shade:
                self._exit_to_launcher()
            elif key == "KEYCODE_HOME" and self.home_exits_shade:
                self._exit_to_launcher()
            return ADBCommandResult(["adb", *args], 0, "", "")
        if args[:3] == ["shell", "input", "swipe"]:
            self.action_log.append("SWIPE")
            if self.swipe_exits_shade:
                self._exit_to_launcher()
            return ADBCommandResult(["adb", *args], 0, "", "")
        if args[:3] == ["shell", "am", "force-stop"]:
            self.action_log.append("FORCE_STOP")
            return ADBCommandResult(["adb", *args], 0, "", "")
        if args[:2] == ["shell", "monkey"]:
            self.action_log.append("MONKEY")
            if self.launch_keeps_shade:
                self.state.update(
                    {
                        "screen_on": True,
                        "interactive": True,
                        "notification_shade": True,
                        "foreground_package": "com.miui.home",
                        "focused_window": "NotificationShade",
                        "resumed_activity": "com.miui.home/.launcher.Launcher",
                    }
                )
            elif self.launch_foregrounds_guazi:
                self._enter_guazi()
            return ADBCommandResult(["adb", *args], 0, "Events injected: 1\n", "")
        return ADBCommandResult(["adb", *args], 0, "", "")


class DeviceReadyGateTest(unittest.TestCase):
    def test_non_secure_locked_or_input_restricted_runs_fast_entry_before_queue(self):
        client = FakeAdbClient(
            window_dump=(
                "mCurrentFocus=Window{abc u0 com.miui.home/com.miui.home.launcher.Launcher}\n"
                "mKeyguardShowing=true\n"
                "mInputRestricted=true\n"
                "isKeyguardSecure=false\n"
            )
        )
        fast_calls = []

        def fake_fast_entry(**kwargs):
            fast_calls.append(kwargs)
            return {
                "ok": True,
                "status": FAST_ENTRY_RECOVERED,
                "snapshot": {
                    "foreground_package": GUAZI_PACKAGE,
                    "focused_window": f"{GUAZI_PACKAGE}/.MainActivity",
                    "guazi_foreground": True,
                    "guazi_fast_entry_ready": True,
                },
            }

        result = check_device_ready_for_pricing(
            task_id="FS20260625_0001",
            client_factory=lambda: client,
            fast_entry_runner=fake_fast_entry,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["should_enqueue"])
        self.assertTrue(result["should_start_runner"])
        self.assertEqual(result["status"], FAST_ENTRY_RECOVERED)
        self.assertEqual(result["recoverable_status"], RECOVERABLE_LOCK_OR_NOTIFICATION_SHADE)
        self.assertEqual(result["recoverable_reason_codes"], [RECOVERABLE_LOCK_OR_NOTIFICATION_SHADE])
        self.assertTrue(result["fast_entry_attempted"])
        self.assertEqual(len(fast_calls), 1)
        self.assertTrue(result["keyguard_showing"])
        for forbidden in ["adb", "keyguard", "NotificationShade", "runner", "dispatcher"]:
            self.assertNotIn(forbidden, result["business_reply_text"])

    def test_notification_shade_usb_notification_runs_fast_entry_before_queue(self):
        client = FakeAdbClient(
            window_dump=(
                "mCurrentFocus=Window{abc u0 NotificationShade}\n"
                "isKeyguardShowing=true\n"
                "isKeyguardSecure=false\n"
                "USB debugging connected\n"
            ),
        )

        result = check_device_ready_for_pricing(
            task_id="FS20260625_0001",
            client_factory=lambda: client,
            fast_entry_runner=lambda **kwargs: {
                "ok": True,
                "status": FAST_ENTRY_RECOVERED,
                "snapshot": {"foreground_package": GUAZI_PACKAGE, "guazi_foreground": True},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["recoverable_status"], RECOVERABLE_LOCK_OR_NOTIFICATION_SHADE)
        self.assertEqual(result["recoverable_reason_codes"], [RECOVERABLE_LOCK_OR_NOTIFICATION_SHADE])
        self.assertTrue(result["fast_entry_attempted"])
        self.assertTrue(result["notification_shade_showing"])

    def test_secure_keyguard_is_hard_blocker_without_fast_entry(self):
        client = FakeAdbClient(
            window_dump=(
                "mCurrentFocus=Window{abc u0 com.android.systemui/.keyguard.KeyguardViewMediator}\n"
                "mKeyguardShowing=true\n"
                "isKeyguardSecure=true\n"
            )
        )
        fast_calls = []

        result = check_device_ready_for_pricing(
            task_id="FS20260625_0001",
            client_factory=lambda: client,
            fast_entry_runner=lambda **kwargs: fast_calls.append(kwargs) or {"ok": True},
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["should_enqueue"])
        self.assertEqual(result["error_code"], SECURE_KEYGUARD_LOCKED)
        self.assertFalse(result["fast_entry_attempted"])
        self.assertEqual(fast_calls, [])

    def test_unlocked_home_screen_must_fast_entry_before_enqueue(self):
        client = FakeAdbClient(
            window_dump="mCurrentFocus=Window{abc u0 com.miui.home/com.miui.home.launcher.Launcher}\nisKeyguardShowing=false\n",
            activity_dump="mResumedActivity: ActivityRecord{abc u0 com.miui.home/.launcher.Launcher t1}",
        )

        result = check_device_ready_for_pricing(
            task_id="FS20260625_0001",
            client_factory=lambda: client,
            fast_entry_runner=lambda **kwargs: {
                "ok": True,
                "status": FAST_ENTRY_RECOVERED,
                "snapshot": {"foreground_package": GUAZI_PACKAGE, "guazi_foreground": True},
            },
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["should_enqueue"])
        self.assertTrue(result["should_start_runner"])
        self.assertEqual(result["status"], FAST_ENTRY_RECOVERED)
        self.assertEqual(result["recoverable_status"], RECOVERABLE_LOCK_OR_NOTIFICATION_SHADE)
        self.assertTrue(result["fast_entry_attempted"])
        self.assertTrue(result["launcher_visible"])

    def test_guazi_foreground_still_uses_clean_reopen_fastpath(self):
        client = FakeAdbClient(
            window_dump=f"mCurrentFocus=Window{{abc u0 {GUAZI_PACKAGE}/.MainActivity}}\nisKeyguardShowing=false\n",
            activity_dump=f"mResumedActivity: ActivityRecord{{abc u0 {GUAZI_PACKAGE}/.MainActivity t1}}",
        )
        fast_calls = []

        result = check_device_ready_for_pricing(
            task_id="FS20260625_0001",
            client_factory=lambda: client,
            fast_entry_runner=lambda **kwargs: fast_calls.append(kwargs) or {
                "ok": True,
                "status": FAST_ENTRY_RECOVERED,
                "snapshot": {"foreground_package": GUAZI_PACKAGE, "guazi_foreground": True},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], FAST_ENTRY_RECOVERED)
        self.assertTrue(result["guazi_foreground"])
        self.assertTrue(result["guazi_fast_entry_ready"])
        self.assertTrue(result["guazi_force_reopen_required"])
        self.assertEqual(len(fast_calls), 1)

    def test_missing_target_device_is_blocked_before_runner(self):
        client = FakeAdbClient(devices_output="List of devices attached\nother\tdevice\n")

        result = check_device_ready_for_pricing(task_id="FS20260625_0001", client_factory=lambda: client)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], ADB_DEVICE_NOT_CONNECTED)
        self.assertFalse(result["should_enqueue"])

    def test_fast_entry_failure_does_not_enqueue_and_hides_internal_terms(self):
        client = FakeAdbClient(
            window_dump="mCurrentFocus=Window{abc u0 com.miui.home/com.miui.home.launcher.Launcher}\nisKeyguardShowing=false\n",
            activity_dump="mResumedActivity: ActivityRecord{abc u0 com.miui.home/.launcher.Launcher t1}",
        )

        result = check_device_ready_for_pricing(
            task_id="FS20260625_0001",
            client_factory=lambda: client,
            fast_entry_runner=lambda **kwargs: {
                "ok": False,
                "status": DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY,
                "error_code": DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY,
            },
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["should_enqueue"])
        self.assertFalse(result["should_start_runner"])
        self.assertEqual(result["error_code"], DIRECT_SWIPE_FASTPATH_FAILED_AFTER_RETRY)
        self.assertTrue(result["fast_entry_attempted"])
        self.assertIn("未能自动退出锁屏/通知栏遮挡并进入瓜子 APP", result["business_reply_text"])
        for forbidden in ["adb", "keyguard", "NotificationShade", "runner", "dispatcher", "USB"]:
            self.assertNotIn(forbidden, result["business_reply_text"])

    def test_robust_ladder_home_fallback_recovers_notification_shade_active_path(self):
        client = StatefulDeviceReadyClient(
            screen_on=False,
            keyguard_showing=True,
            secure_keyguard=False,
            input_restricted=True,
            notification_shade=True,
            swipe_exits_shade=False,
            back_exits_shade=False,
            home_exits_shade=True,
            launch_foregrounds_guazi=True,
        )

        result = check_device_ready_for_pricing(task_id="FS20260630_0005", client_factory=lambda: client)

        self.assertTrue(result["ok"])
        self.assertTrue(result["should_enqueue"])
        self.assertEqual(result["status"], FAST_ENTRY_RECOVERED)
        fast = result["fast_entry_result"]
        self.assertEqual(DEVICE_READY_RECOVERY_LADDER_VERSION, fast["device_ready_recovery_ladder_version"])
        self.assertTrue(fast["confirm_preflight_uses_robust_ladder"])
        self.assertTrue(fast["recovery_path_consistency_checked"])
        names = [item["name"] for item in fast["actions"]]
        self.assertIn("wake_screen", names)
        self.assertIn("direct_swipe_dismiss_overlay", names)
        self.assertIn("back_fallback", names)
        self.assertIn("home_fallback", names)
        self.assertIn("clean_reopen_guazi_force_stop", names)
        self.assertIn("launch_guazi_app", names)
        self.assertTrue(fast["final_guazi_foreground"])
        self.assertIn("KEYCODE_HOME", client.action_log)

    def test_robust_ladder_back_then_launch_recovers_without_start_ack_leak(self):
        client = StatefulDeviceReadyClient(
            screen_on=True,
            keyguard_showing=True,
            secure_keyguard=False,
            input_restricted=True,
            notification_shade=True,
            swipe_exits_shade=False,
            back_exits_shade=True,
            home_exits_shade=True,
            launch_foregrounds_guazi=True,
        )

        result = check_device_ready_for_pricing(task_id="FS20260630_0005", client_factory=lambda: client)

        self.assertTrue(result["ok"])
        names = [item["name"] for item in result["fast_entry_result"]["actions"]]
        self.assertIn("back_fallback", names)
        self.assertIn("launch_guazi_app", names)
        self.assertTrue(result["fast_entry_result"]["final_guazi_foreground"])

    def test_robust_ladder_failure_after_launch_blocked_by_notification_shade(self):
        client = StatefulDeviceReadyClient(
            screen_on=False,
            keyguard_showing=True,
            secure_keyguard=False,
            input_restricted=True,
            notification_shade=True,
            swipe_exits_shade=False,
            back_exits_shade=False,
            home_exits_shade=False,
            launch_foregrounds_guazi=False,
            launch_keeps_shade=True,
        )

        result = check_device_ready_for_pricing(
            task_id="FS20260630_0005",
            client_factory=lambda: client,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["should_enqueue"])
        self.assertFalse(result["should_start_runner"])
        self.assertEqual(DEVICE_READY_GUAZI_LAUNCH_BLOCKED_BY_NOTIFICATION_SHADE, result["error_code"])
        names = [item["name"] for item in result["fast_entry_result"]["actions"]]
        self.assertIn("back_fallback", names)
        self.assertIn("home_fallback", names)
        self.assertIn("launch_guazi_app", names)
        self.assertIn("未能自动退出锁屏/通知栏遮挡并进入瓜子 APP", result["business_reply_text"])
        self.assertNotIn("【定价已开始】", result["business_reply_text"])

    def test_secure_keyguard_skips_robust_back_home_and_launch(self):
        client = StatefulDeviceReadyClient(
            screen_on=True,
            keyguard_showing=True,
            secure_keyguard=True,
            input_restricted=True,
            notification_shade=True,
        )

        result = check_device_ready_for_pricing(task_id="FS20260630_0005", client_factory=lambda: client)

        self.assertFalse(result["ok"])
        self.assertEqual(SECURE_KEYGUARD_LOCKED, result["error_code"])
        self.assertFalse(result["fast_entry_attempted"])
        self.assertNotIn("BACK", client.action_log)
        self.assertNotIn("KEYCODE_HOME", client.action_log)
        self.assertNotIn("MONKEY", client.action_log)

    def test_screen_on_notification_shade_is_not_ready_without_robust_recovery(self):
        client = StatefulDeviceReadyClient(
            screen_on=True,
            keyguard_showing=False,
            secure_keyguard=False,
            input_restricted=False,
            notification_shade=True,
            swipe_exits_shade=False,
            back_exits_shade=False,
            home_exits_shade=True,
            launch_foregrounds_guazi=True,
        )

        result = check_device_ready_for_pricing(task_id="FS20260630_0005", client_factory=lambda: client)

        self.assertTrue(result["ok"])
        self.assertTrue(result["fast_entry_attempted"])
        names = [item["name"] for item in result["fast_entry_result"]["actions"]]
        self.assertIn("direct_swipe_dismiss_overlay", names)
        self.assertIn("home_fallback", names)
        self.assertTrue(result["fast_entry_result"]["final_guazi_foreground"])


if __name__ == "__main__":
    unittest.main()
