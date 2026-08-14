"""Script-level wrapper for strict target ADB device selection helpers.

Configuration contract: GUAZI_ADB_SERIAL overrides config/adb_target_device.yaml,
whose allow_default_when_single_device value must remain false.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

PACKAGE_ROOT = SRC_ROOT / "guazi_app_data_system"
loaded_package = sys.modules.get("guazi_app_data_system")
package_path = getattr(loaded_package, "__path__", None)
if package_path is not None and str(PACKAGE_ROOT) not in list(package_path):
    package_path.append(str(PACKAGE_ROOT))

from guazi_app_data_system.adb_target_device import (  # noqa: E402,F401
    ENV_ADB_SERIAL,
    TARGET_ADB_DEVICE_NOT_CONNECTED,
    TARGET_ADB_DEVICE_OFFLINE,
    TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT,
    TARGET_ADB_DEVICE_UNAUTHORIZED,
    TARGET_ADB_ERROR_CODES,
    TARGET_ADB_FORBIDDEN_SERVER_COMMAND,
    TARGET_ADB_SERIAL_NOT_CONFIGURED,
    adb_command_is_forbidden,
    adb_command_requires_serial,
    build_adb_command,
    get_target_device_context,
    load_target_adb_serial,
    load_target_device_config,
    validate_target_device_available,
    validate_target_serial_configured,
)
