"""Test-discovery shim for src-layout package imports.

`python -m unittest discover tests -v` can import test modules as top-level
files with `tests/` as the first import root. This shim keeps those imports
pointing at the real package under `src/guazi_app_data_system`.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
REAL_PACKAGE = SRC / "guazi_app_data_system"

for path in (SRC, SCRIPTS, ROOT):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

if REAL_PACKAGE.exists():
    __path__.append(str(REAL_PACKAGE))  # type: ignore[name-defined]
