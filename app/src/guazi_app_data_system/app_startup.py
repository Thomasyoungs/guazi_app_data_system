"""ADB based device connection, APP discovery, launch, screenshot and UI text helpers."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .adb_target_device import (
    TARGET_ADB_FORBIDDEN_SERVER_COMMAND,
    TARGET_ADB_SERIAL_NOT_CONFIGURED,
    adb_command_is_forbidden,
    adb_command_requires_serial,
    build_adb_command,
    get_target_device_context,
    load_target_adb_serial,
    validate_target_device_available,
)


SELECT_CAR_LABEL = "\u9009\u8f66"
GUAZI_PACKAGE = "com.ganji.android.haoche_c"
GUAZI_APP_ICON_LABEL = "\u74dc\u5b50\u4e8c\u624b\u8f66"


@dataclass
class ADBCommandResult:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def success(self) -> bool:
        return self.returncode == 0


def _summarize_adb_vendor_keys(value: str) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for raw_path in [item for item in value.split(os.pathsep) if item.strip()]:
        path = Path(raw_path.strip())
        item: dict[str, Any] = {
            "path_summary": str(path),
            "name": path.name,
            "exists": path.exists(),
            "size": None,
            "mtime": None,
        }
        if path.exists():
            stat = path.stat()
            item["size"] = stat.st_size
            item["mtime"] = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        summaries.append(item)
    return summaries


class AdbClient:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    ADB_HOME = PROJECT_ROOT / "output" / "adb_home"
    ADB_VENDOR_KEY = Path("C:/Users/lzc93/.android/adbkey")
    ISOLATED_ADB_ENV_FLAG = "GUAZI_USE_ISOLATED_ADB_HOME"

    def __init__(self, adb_path: str | Path | None = None, adb_serial: str | None = None) -> None:
        if adb_path:
            self.adb_path = Path(adb_path)
            self.adb_path_source = "explicit"
        else:
            self.adb_path, self.adb_path_source = self.find_adb_with_source()
        self.adb_env = self._build_adb_environment()
        self.adb_serial = (
            load_target_adb_serial() if adb_serial is None else str(adb_serial)
        ).strip()

    @staticmethod
    def find_adb() -> Path | None:
        adb_path, _source = AdbClient.find_adb_with_source()
        return adb_path

    @staticmethod
    def find_adb_with_source() -> tuple[Path | None, str]:
        env_path = os.environ.get("ADB_PATH")
        if env_path and Path(env_path).exists():
            return Path(env_path), "ADB_PATH"
        found = shutil.which("adb")
        if found:
            return Path(found), "PATH"
        user_profile = os.environ.get("USERPROFILE")
        candidates: list[tuple[Path, str]] = [
            (Path(os.environ.get("ANDROID_HOME", "")) / "platform-tools" / "adb.exe", "ANDROID_HOME"),
            (Path(os.environ.get("ANDROID_SDK_ROOT", "")) / "platform-tools" / "adb.exe", "ANDROID_SDK_ROOT"),
            (Path("C:/platform-tools/adb.exe"), "C:/platform-tools"),
            (Path("C:/Android/platform-tools/adb.exe"), "C:/Android/platform-tools"),
        ]
        if user_profile:
            candidates.insert(
                2,
                (
                    Path(user_profile) / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
                    "USERPROFILE_ANDROID_SDK",
                ),
            )
        for candidate, source in candidates:
            if str(candidate) and candidate.exists():
                return candidate, source
        project_adb = Path(__file__).resolve().parents[2] / "tools" / "android-platform-tools" / "platform-tools" / "adb.exe"
        if project_adb.exists():
            return project_adb, "project_fallback"
        return None, "not_found"

    @property
    def available(self) -> bool:
        return bool(self.adb_path and self.adb_path.exists())

    def _build_adb_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        if str(env.get(self.ISOLATED_ADB_ENV_FLAG) or "").strip().lower() not in {"1", "true", "yes"}:
            return env

        adb_home = self.ADB_HOME
        (adb_home / ".android").mkdir(parents=True, exist_ok=True)
        controlled_keys = {
            "ANDROID_SDK_HOME",
            "ANDROID_USER_HOME",
            "HOME",
            "USERPROFILE",
            "HOMEDRIVE",
            "HOMEPATH",
            "APPDATA",
            "LOCALAPPDATA",
            "ADB_VENDOR_KEYS",
        }
        for key in list(env):
            if key.upper() in controlled_keys:
                env.pop(key, None)
        env["ANDROID_SDK_HOME"] = str(adb_home)
        env["ANDROID_USER_HOME"] = str(adb_home)
        env["HOME"] = str(adb_home)
        env["USERPROFILE"] = str(adb_home)
        env["HOMEDRIVE"] = adb_home.drive or str(adb_home.anchor).rstrip("\\")
        env["HOMEPATH"] = str(adb_home)[len(env["HOMEDRIVE"]) :] if env["HOMEDRIVE"] else str(adb_home)
        env["APPDATA"] = str(adb_home / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(adb_home / "AppData" / "Local")
        if self.ADB_VENDOR_KEY.exists():
            env["ADB_VENDOR_KEYS"] = str(self.ADB_VENDOR_KEY)
        else:
            env.pop("ADB_VENDOR_KEYS", None)
        return env

    def adb_environment(self) -> dict[str, str]:
        self.adb_env = self._build_adb_environment()
        return self.adb_env

    def runtime_environment_snapshot(self) -> dict[str, Any]:
        env = self.adb_environment()
        target_context = self.target_device_context()
        adb_vendor_keys = str(env.get("ADB_VENDOR_KEYS") or "")
        key_summaries = _summarize_adb_vendor_keys(adb_vendor_keys)
        isolated = str(os.environ.get(self.ISOLATED_ADB_ENV_FLAG) or "").strip().lower() in {"1", "true", "yes"}
        adb_name = Path(self.adb_path).name if self.adb_path else "adb"
        command_preview = f"{adb_name} -s {self.adb_serial} ..." if self.adb_serial else f"{adb_name} ..."
        risk_flags: list[str] = []
        if isolated:
            risk_flags.append("isolated_adb_home_enabled")
        if adb_vendor_keys:
            risk_flags.append("adb_vendor_keys_configured")
        if self.adb_path_source == "project_fallback":
            risk_flags.append("project_fallback_adb")
        return {
            "target_adb_serial": self.adb_serial,
            "adb_serial_source": target_context.get("serial_source"),
            "adb_path": str(self.adb_path or ""),
            "adb_path_source": self.adb_path_source,
            "adb_runtime_env_mode": "isolated_adb_home" if isolated else "inherited_user_environment",
            "use_isolated_adb_home": isolated,
            "adb_vendor_keys_configured": bool(adb_vendor_keys),
            "adb_vendor_keys_path_summary": key_summaries,
            "adb_vendor_keys_exists": any(bool(item.get("exists")) for item in key_summaries),
            "output_adb_home_exists": self.ADB_HOME.exists(),
            "output_adb_home_android_dir_exists": (self.ADB_HOME / ".android").exists(),
            "android_adb_server_port": env.get("ANDROID_ADB_SERVER_PORT"),
            "adb_command_preview": command_preview,
            "cwd": str(Path.cwd()),
            "project_root": str(self.PROJECT_ROOT),
            "python_executable": sys.executable,
            "adb_env_risk_flags": risk_flags,
        }

    def run(self, args: list[str], timeout: int = 20, stdout_path: Path | None = None) -> ADBCommandResult:
        if not self.available:
            return ADBCommandResult(["adb", *args], 127, "", "adb not found")
        if adb_command_is_forbidden(args):
            return ADBCommandResult(
                [str(self.adb_path), *args],
                126,
                "",
                TARGET_ADB_FORBIDDEN_SERVER_COMMAND,
            )
        if adb_command_requires_serial(args) and not self.adb_serial:
            return ADBCommandResult(
                [str(self.adb_path), *args],
                125,
                "",
                TARGET_ADB_SERIAL_NOT_CONFIGURED,
            )
        try:
            command = build_adb_command(args, adb_path=str(self.adb_path), active_serial=self.adb_serial)
        except ValueError as exc:
            return ADBCommandResult([str(self.adb_path), *args], 125, "", str(exc))
        env = self.adb_environment()
        try:
            if stdout_path is not None:
                stdout_path.parent.mkdir(parents=True, exist_ok=True)
                with stdout_path.open("wb") as file:
                    completed = subprocess.run(
                        command,
                        stdout=file,
                        stderr=subprocess.PIPE,
                        timeout=timeout,
                        env=env,
                    )
                stderr = completed.stderr.decode("utf-8", errors="ignore") if completed.stderr else ""
                return ADBCommandResult(command, completed.returncode, str(stdout_path), stderr)
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout,
                env=env,
            )
            return ADBCommandResult(command, completed.returncode, completed.stdout, completed.stderr)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="ignore") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else (exc.stderr or "adb command timed out")
            return ADBCommandResult(command, 124, stdout, stderr)

    @staticmethod
    def parse_devices(output: str) -> list[dict[str, str]]:
        devices: list[dict[str, str]] = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            parts = re.split(r"\s+", line)
            if len(parts) >= 2:
                details: dict[str, str] = {}
                for part in parts[2:]:
                    if ":" in part:
                        key, value = part.split(":", 1)
                        details[key] = value
                devices.append({"serial": parts[0], "status": parts[1], "raw": line, **details})
        return devices

    def devices(self) -> list[dict[str, str]]:
        return self.parse_devices(self.run(["devices"]).stdout)

    def devices_l(self) -> tuple[str, list[dict[str, str]]]:
        result = self.run(["devices", "-l"], timeout=20)
        return result.stdout, self.parse_devices(result.stdout)

    def target_device_context(self) -> dict[str, Any]:
        context = get_target_device_context()
        context["adb_serial"] = self.adb_serial
        return context

    def validate_target_device_available(self) -> dict[str, Any]:
        _raw, devices = self.devices_l()
        return validate_target_device_available(devices, active_serial=self.adb_serial)

    def adb_version(self) -> ADBCommandResult:
        return self.run(["version"], timeout=20)

    def kill_server(self) -> ADBCommandResult:
        return ADBCommandResult([str(self.adb_path or "adb"), "server-restart-disabled"], 126, "", TARGET_ADB_FORBIDDEN_SERVER_COMMAND)

    def start_server(self) -> ADBCommandResult:
        return ADBCommandResult([str(self.adb_path or "adb"), "server-start-disabled"], 126, "", TARGET_ADB_FORBIDDEN_SERVER_COMMAND)

    def first_ready_device(self) -> dict[str, str] | None:
        validation = self.validate_target_device_available()
        if validation.get("ok"):
            device = validation.get("device")
            return device if isinstance(device, dict) else None
        return None

    def device_info(self) -> dict[str, str]:
        keys = {
            "manufacturer": "ro.product.manufacturer",
            "model": "ro.product.model",
            "android_version": "ro.build.version.release",
            "sdk": "ro.build.version.sdk",
        }
        info: dict[str, str] = {}
        for name, prop in keys.items():
            result = self.run(["shell", "getprop", prop])
            info[name] = result.stdout.strip() if result.success else ""
        return info

    def launch_activity_component(self, component: str, wait_seconds: int = 6) -> dict[str, object]:
        """Launch an activity component on the device and wait until the package is foreground.

        component: string in form "package/name" or "package/.Activity"
        Returns a dict with keys: ok(bool), launch_result(ADBCommandResult), snapshot(dict)
        """
        try:
            launch_result = self.run(["shell", "am", "start", "-n", component], timeout=15)
        except Exception as exc:  # pragma: no cover - defensive
            return {"ok": False, "launch_result": None, "error": str(exc)}
        package = component.split("/", 1)[0]
        snapshot = {}
        deadline = time.time() + float(wait_seconds)
        while time.time() < deadline:
            try:
                snapshot = self.runtime_preflight_snapshot(None, ) if hasattr(self, "runtime_preflight_snapshot") else {}
            except Exception:
                snapshot = {}
            fg = str(snapshot.get("foreground_package") or "")
            xml_pkg = str(snapshot.get("xml_package") or "")
            if fg == package or xml_pkg == package:
                return {"ok": True, "launch_result": launch_result, "snapshot": snapshot}
            time.sleep(0.8)
        return {"ok": False, "launch_result": launch_result, "snapshot": snapshot}

    def installed_packages(self) -> list[str]:
        result = self.run(["shell", "pm", "list", "packages"])
        packages: list[str] = []
        for line in result.stdout.splitlines():
            if line.startswith("package:"):
                packages.append(line.replace("package:", "").strip())
        return packages












    def wake_screen_once(self) -> dict[str, Any]:
        wake = self.run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], timeout=20)
        return {
            "wake_success": wake.success,
            "wake_stderr": wake.stderr,
        }



    def home_key_once(self) -> dict[str, Any]:
        home = self.run(["shell", "input", "keyevent", "KEYCODE_HOME"], timeout=20)
        return {
            "home_success": home.success,
            "home_stderr": home.stderr,
        }

    def wake_swipe_once(self, duration_ms: int = 400) -> dict[str, Any]:
        width, height = self.screen_size()
        start_x = width // 2
        end_x = width // 2
        start_y = int(height * 0.82)
        end_y = int(height * 0.30)
        swipe = self.run(
            [
                "shell",
                "input",
                "swipe",
                str(start_x),
                str(start_y),
                str(end_x),
                str(end_y),
                str(duration_ms),
            ],
            timeout=20,
        )
        return {
            "swipe_success": swipe.success,
            "swipe_stderr": swipe.stderr,
            "start_x": start_x,
            "start_y": start_y,
            "end_x": end_x,
            "end_y": end_y,
            "duration_ms": duration_ms,
        }

    def is_keyguard_locked(self) -> bool:
        result = self.run(["shell", "dumpsys", "window"], timeout=20)
        if not result.success:
            return True
        patterns = [
            "mDreamingLockscreen=true",
            "mShowingLockscreen=true",
            "mKeyguardShowing=true",
            "isStatusBarKeyguard=true",
        ]
        return any(pattern in result.stdout for pattern in patterns)

    def power_state(self) -> dict[str, Any]:
        result = self.run(["shell", "dumpsys", "power"], timeout=20)
        state = {
            "raw": result.stdout if result.success else "",
            "wakefulness": "",
            "interactive": None,
            "display_state": "",
        }
        if not result.success:
            return state
        wakefulness = re.search(r"mWakefulness=([A-Za-z]+)", result.stdout)
        if wakefulness:
            state["wakefulness"] = wakefulness.group(1)
        interactive = re.search(r"mInteractive=(true|false)", result.stdout)
        if interactive:
            state["interactive"] = interactive.group(1) == "true"
        display_state = re.search(r"Display Power: state=([A-Z_]+)", result.stdout)
        if display_state:
            state["display_state"] = display_state.group(1)
        return state

    def screenshot(self, output_path: Path, timeout: int = 20) -> ADBCommandResult:
        return self.run(["exec-out", "screencap", "-p"], timeout=timeout, stdout_path=output_path)

    def dump_ui_xml(self) -> str:
        self.run(["shell", "uiautomator", "dump", "/sdcard/window.xml"], timeout=20)
        result = self.run(["exec-out", "cat", "/sdcard/window.xml"], timeout=20)
        return result.stdout or "" if result.success else ""

    def dump_ui_text(self) -> dict[str, Any]:
        xml = self.dump_ui_xml()
        texts: list[str] = []
        if xml.strip():
            try:
                root = ElementTree.fromstring(xml)
                for node in root.iter("node"):
                    text = node.attrib.get("text") or node.attrib.get("content-desc") or ""
                    if text:
                        texts.append(text)
            except ElementTree.ParseError:
                pass
        return {"xml": xml, "text": " ".join(texts), "texts": texts}

    def screen_size(self) -> tuple[int, int]:
        result = self.run(["shell", "wm", "size"])
        match = re.search(r"(\d+)x(\d+)", result.stdout)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 1080, 1920

    def tap(self, x: int, y: int) -> ADBCommandResult:
        return self.run(["shell", "input", "tap", str(x), str(y)])

    def tap_region(self, region: str) -> ADBCommandResult:
        width, height = self.screen_size()
        if region == "left_bottom":
            return self.tap(int(width * 0.16), int(height * 0.86))
        return self.tap(width // 2, height // 2)

    def tap_text(self, text: str) -> ADBCommandResult:
        xml = self.dump_ui_xml()
        bounds = find_text_bounds(xml, text)
        if not bounds:
            return ADBCommandResult(["tap_text", text], 2, "", f"text not found: {text}")
        x1, y1, x2, y2 = bounds
        return self.tap((x1 + x2) // 2, (y1 + y2) // 2)

    def tap_exact_text(self, text: str, xml: str | None = None) -> ADBCommandResult:
        candidates = find_text_candidates(xml or self.dump_ui_xml(), text)
        if not candidates:
            return ADBCommandResult(["tap_exact_text", text], 2, "", f"text not found: {text}")
        target = candidates[0]
        return self.tap(target["center_x"], target["center_y"])

    def tap_guazi_app_icon_exact_text(self, xml: str | None = None) -> ADBCommandResult:
        return self.tap_exact_text(GUAZI_APP_ICON_LABEL, xml=xml)

    def tap_s01_bottom_select_car_tab(self, xml: str | None = None) -> dict[str, Any]:
        screen_width, screen_height = self.screen_size()
        xml_text = xml or self.dump_ui_xml()
        app_visible_bottom = max(_max_package_bottom(xml_text, GUAZI_PACKAGE), 0)
        min_safe_y = int(screen_height * 0.80)
        max_safe_y = int(screen_height * 0.94) - 1

        bottom_candidates = [
            candidate
            for candidate in find_text_candidates(xml_text, SELECT_CAR_LABEL)
            if candidate["center_y"] > min_safe_y and candidate["center_y"] < max_safe_y
        ]
        if bottom_candidates:
            target = max(bottom_candidates, key=lambda item: item["center_y"])
            tap_x = target["center_x"]
            tap_y = target["center_y"]
            tap_result = self.tap(tap_x, tap_y)
            return {
                "success": tap_result.success,
                "method": "bottom_text_node",
                "tap_x": tap_x,
                "tap_y": tap_y,
                "tap_result": tap_result,
                "within_safe_app_bounds": True,
                "bounds": target["bounds"],
                "rejected_non_bottom_matches": any(
                    candidate["center_y"] <= min_safe_y or candidate["center_y"] >= max_safe_y
                    for candidate in find_text_candidates(xml_text, SELECT_CAR_LABEL)
                ),
                "select_car_label": SELECT_CAR_LABEL,
                "failure_reason": "",
            }

        nav_bounds = find_resource_bounds(xml_text, "nav_view")
        if nav_bounds:
            x1, y1, x2, y2 = nav_bounds
            tab_width = max((x2 - x1) // 5, 1)
            tap_x = x1 + tab_width + (tab_width // 2)
            nav_center_y = (y1 + y2) // 2
            bottom_safe_cap = min(y2 - 20, max_safe_y, app_visible_bottom - 80 if app_visible_bottom else max_safe_y)
            tap_y = min(nav_center_y, bottom_safe_cap)
            if tap_y >= int(screen_height * 0.94) or not (y1 <= tap_y <= y2):
                return {
                    "success": False,
                    "method": "nav_view_second_tab_center",
                    "tap_x": tap_x,
                    "tap_y": tap_y,
                    "within_safe_app_bounds": False,
                    "bounds": nav_bounds,
                    "rejected_non_bottom_matches": bool(find_text_candidates(xml_text, SELECT_CAR_LABEL)),
                    "select_car_label": SELECT_CAR_LABEL,
                    "failure_reason": "tap_y_too_low",
                }
            tap_result = self.tap(tap_x, tap_y)
            return {
                "success": tap_result.success,
                "method": "nav_view_second_tab_center",
                "tap_x": tap_x,
                "tap_y": tap_y,
                "tap_result": tap_result,
                "within_safe_app_bounds": x1 <= tap_x <= x2 and y1 <= tap_y <= y2 and tap_y < int(screen_height * 0.94),
                "bounds": nav_bounds,
                "rejected_non_bottom_matches": bool(find_text_candidates(xml_text, SELECT_CAR_LABEL)),
                "select_car_label": SELECT_CAR_LABEL,
                "failure_reason": "",
            }

        return {
            "success": False,
            "method": None,
            "tap_x": None,
            "tap_y": None,
            "within_safe_app_bounds": False,
            "bounds": None,
            "rejected_non_bottom_matches": bool(find_text_candidates(xml_text, SELECT_CAR_LABEL)),
            "select_car_label": SELECT_CAR_LABEL,
            "failure_reason": "select_car_bottom_tab_not_found",
        }

    def swipe(self, direction: str = "up") -> ADBCommandResult:
        width, height = self.screen_size()
        if direction == "left":
            args = [str(int(width * 0.8)), str(height // 2), str(int(width * 0.2)), str(height // 2), "450"]
        elif direction == "down":
            args = [str(width // 2), str(int(height * 0.25)), str(width // 2), str(int(height * 0.75)), "450"]
        else:
            args = [str(width // 2), str(int(height * 0.75)), str(width // 2), str(int(height * 0.25)), "450"]
        return self.run(["shell", "input", "swipe", *args])

    def back(self) -> ADBCommandResult:
        return self.run(["shell", "input", "keyevent", "BACK"])

    def runtime_preflight_snapshot(self, screenshot_path: Path | None = None) -> dict[str, Any]:
        power = self.power_state()
        window_result = self.run(["shell", "dumpsys", "window"], timeout=20)
        activity_result = self.run(["shell", "dumpsys", "activity", "activities"], timeout=20)
        screenshot_result = self.screenshot(screenshot_path) if screenshot_path else None
        xml = self.dump_ui_xml()
        window_dump = window_result.stdout if window_result.success else ""
        activity_dump = activity_result.stdout if activity_result.success else ""
        keyguard_showing = _is_keyguard_showing_from_window_dump(window_dump)
        return {
            "wakefulness": power.get("wakefulness"),
            "interactive": power.get("interactive"),
            "display_state": power.get("display_state"),
            "keyguard_showing": keyguard_showing,
            "keyguard_locked": keyguard_showing,
            "keyguard_secure": _is_keyguard_secure_from_window_dump(window_dump),
            "foreground_package": _extract_foreground_package(
                window_dump,
                activity_dump,
            ),
            "resumed_activity": _extract_resumed_activity(activity_dump),
            "focused_window": _extract_focused_window(window_dump),
            "xml_package": extract_xml_root_package(xml),
            "fresh_xml": xml,
            "fresh_screenshot": str(screenshot_path) if screenshot_result and screenshot_result.success else None,
            "screenshot_is_black": _is_probably_black_screenshot(screenshot_path) if screenshot_result and screenshot_result.success and screenshot_path else False,
        }


def find_text_bounds(xml: str, needle: str) -> tuple[int, int, int, int] | None:
    if not xml.strip():
        return None
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    for node in root.iter("node"):
        text = node.attrib.get("text") or node.attrib.get("content-desc") or ""
        if needle and needle in text:
            bounds = node.attrib.get("bounds", "")
            match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
            if match:
                return tuple(int(item) for item in match.groups())  # type: ignore[return-value]
    return None


def find_text_candidates(xml: str, needle: str) -> list[dict[str, Any]]:
    if not xml.strip():
        return []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    matches: list[dict[str, Any]] = []
    for node in root.iter("node"):
        for key in ("text", "content-desc"):
            text = str(node.attrib.get(key) or "").strip()
            if text != needle:
                continue
            bounds = _parse_bounds(node.attrib.get("bounds", ""))
            if not bounds:
                continue
            x1, y1, x2, y2 = bounds
            matches.append(
                {
                    "text": text,
                    "bounds": bounds,
                    "center_x": (x1 + x2) // 2,
                    "center_y": (y1 + y2) // 2,
                    "resource_id": str(node.attrib.get("resource-id") or ""),
                    "package": str(node.attrib.get("package") or ""),
                    "clickable": str(node.attrib.get("clickable") or "") == "true",
                    "class_name": str(node.attrib.get("class") or ""),
                }
            )
            break
    return matches


def find_resource_bounds(xml: str, resource_suffix: str) -> tuple[int, int, int, int] | None:
    if not xml.strip():
        return None
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    for node in root.iter("node"):
        resource_id = str(node.attrib.get("resource-id") or "")
        if not resource_id.endswith(resource_suffix):
            continue
        bounds = _parse_bounds(node.attrib.get("bounds", ""))
        if bounds:
            return bounds
    return None


def _max_package_bottom(xml: str, package_name: str) -> int:
    if not xml.strip():
        return 0
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return 0
    max_bottom = 0
    for node in root.iter("node"):
        if str(node.attrib.get("package") or "") != package_name:
            continue
        bounds = _parse_bounds(node.attrib.get("bounds", ""))
        if bounds:
            max_bottom = max(max_bottom, bounds[3])
    return max_bottom


def _parse_bounds(raw_bounds: str) -> tuple[int, int, int, int] | None:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(raw_bounds or ""))
    if not match:
        return None
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]



def _extract_focused_window(window_dump: str) -> str:
    patterns = [
        r"mCurrentFocus=Window\{[^\s]+\s+[^\s]+\s+([^}]+)\}",
        r"mFocusedWindow=Window\{[^\s]+\s+[^\s]+\s+([^}]+)\}",
    ]
    for pattern in patterns:
        match = re.search(pattern, window_dump)
        if match:
            return match.group(1).strip()
    return ""


def _extract_resumed_activity(activity_dump: str) -> str:
    patterns = [
        r"mResumedActivity:.*? ([A-Za-z0-9._]+/[A-Za-z0-9.$_]+)",
        r"mResumeActivity:.*? ([A-Za-z0-9._]+/[A-Za-z0-9.$_]+)",
        r"\bACTIVITY ([A-Za-z0-9._]+/[A-Za-z0-9.$_]+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, activity_dump)
        if match:
            return match.group(1).strip()
    return ""


def _extract_foreground_package(window_dump: str, activity_dump: str) -> str:
    for candidate in (_extract_resumed_activity(activity_dump), _extract_focused_window(window_dump)):
        if candidate and "/" in candidate:
            return candidate.split("/", 1)[0]
    return ""


def _is_keyguard_showing_from_window_dump(window_dump: str) -> bool | None:
    if not window_dump:
        return None
    patterns = [
        "isKeyguardShowing=true",
        "mDreamingLockscreen=true",
        "mShowingLockscreen=true",
        "mKeyguardShowing=true",
        "isStatusBarKeyguard=true",
        "NotificationShade",
        "MiuiKeyguard",
    ]
    return any(pattern in window_dump for pattern in patterns)


def _is_keyguard_locked_from_window_dump(window_dump: str) -> bool | None:
    return _is_keyguard_showing_from_window_dump(window_dump)


def _is_keyguard_secure_from_window_dump(window_dump: str) -> bool:
    if not window_dump:
        return False
    secure_patterns = [
        r"\bisKeyguardSecure=true\b",
        r"\bkeyguardSecure=true\b",
        r"\bsecure=true\b",
    ]
    insecure_patterns = [
        r"\bisKeyguardSecure=false\b",
        r"\bkeyguardSecure=false\b",
        r"\bsecure=false\b",
    ]
    if any(re.search(pattern, window_dump) for pattern in insecure_patterns):
        return False
    return any(re.search(pattern, window_dump) for pattern in secure_patterns)


def extract_xml_root_package(xml: str) -> str:
    if not xml.strip():
        return ""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return ""
    for node in root.iter("node"):
        package_name = str(node.attrib.get("package") or "").strip()
        if package_name:
            return package_name
    return ""


def _is_probably_black_screenshot(path: Path) -> bool:
    try:
        from PIL import Image, ImageStat  # type: ignore

        with Image.open(path) as image:
            grayscale = image.convert("L")
            stat = ImageStat.Stat(grayscale)
            mean = stat.mean[0] if stat.mean else 255
            extrema = stat.extrema[0][1] if stat.extrema else 255
            return mean < 3 and extrema < 12
    except Exception:
        try:
            return path.exists() and path.stat().st_size <= 25000
        except OSError:
            return False
