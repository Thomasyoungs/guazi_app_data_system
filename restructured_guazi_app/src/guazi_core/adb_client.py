"""ADB client for Android device interaction.

Migrated and simplified from the original app_startup.py.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ADBCommandResult:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def success(self) -> bool:
        return self.returncode == 0


class AdbClient:
    """ADB client for device communication."""

    def __init__(self, adb_path: str | Path | None = None) -> None:
        if adb_path:
            self.adb_path = Path(adb_path)
            self.adb_path_source = "explicit"
        else:
            self.adb_path, self.adb_path_source = self.find_adb_with_source()

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
        return None, "not_found"

    @property
    def available(self) -> bool:
        return bool(self.adb_path and self.adb_path.exists())

    def run(self, args: list[str], timeout: int = 20) -> ADBCommandResult:
        if not self.available or not self.adb_path:
            return ADBCommandResult(["adb", *args], 127, "", "adb not found")
        command = [str(self.adb_path), *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout,
            )
            return ADBCommandResult(command, completed.returncode, completed.stdout, completed.stderr)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or "" if isinstance(exc.stdout, str) else (exc.stdout.decode("utf-8", errors="ignore") if exc.stdout else "")
            stderr = exc.stderr or "" if isinstance(exc.stdout, str) else (exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else "adb command timed out")
            return ADBCommandResult(command, 124, stdout, stderr)

    def devices(self) -> list[dict[str, str]]:
        output = self.run(["devices"]).stdout
        devices: list[dict[str, str]] = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            parts = re.split(r"\s+", line)
            if len(parts) >= 2:
                devices.append({"serial": parts[0], "status": parts[1]})
        return devices

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

    def screenshot(self, output_path: Path, timeout: int = 20) -> ADBCommandResult:
        return self.run(["exec-out", "screencap", "-p"], timeout=timeout)

    def dump_ui_xml(self) -> str:
        self.run(["shell", "uiautomator", "dump", "/sdcard/window.xml"], timeout=20)
        result = self.run(["exec-out", "cat", "/sdcard/window.xml"], timeout=20)
        return result.stdout or "" if result.success else ""

    def tap(self, x: int, y: int) -> ADBCommandResult:
        return self.run(["shell", "input", "tap", str(x), str(y)])

    def swipe(self, direction: str = "up") -> ADBCommandResult:
        # Simplified swipe - would need screen size for real implementation
        return self.run(["shell", "input", "swipe", "500", "1000", "500", "500"])

    def back(self) -> ADBCommandResult:
        return self.run(["shell", "input", "keyevent", "BACK"])
