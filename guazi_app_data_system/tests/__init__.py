"""Test package bootstrap for standard-library unittest discovery."""

from __future__ import annotations

import sys
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "scripts", ROOT):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

os.environ.setdefault("GUAZI_ADB_SERIAL", "UNITTEST_TARGET_SERIAL")
