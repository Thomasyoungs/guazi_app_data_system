"""Test-only runtime adjustments for the bundled Windows Python.

The Codex Windows sandbox can create unreadable directories through
``tempfile.mkdtemp`` because that helper supplies restrictive POSIX-style
permission bits. Production code keeps Python's default tempfile behavior;
the test runner opts in with ``GUAZI_TEST_MODE=1``.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path


def _mkdtemp_with_default_windows_acl(
    suffix: str | None = None,
    prefix: str | None = None,
    dir: str | None = None,
) -> str:
    suffix = "" if suffix is None else str(suffix)
    prefix = "tmp" if prefix is None else str(prefix)
    root = Path(tempfile.gettempdir() if dir is None else dir)
    root.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        candidate = root / f"{prefix}{uuid.uuid4().hex}{suffix}"
        try:
            candidate.mkdir()
            return str(candidate.resolve())
        except FileExistsError:
            continue
    raise FileExistsError("could not create a unique temporary directory")


if os.environ.get("GUAZI_TEST_MODE") == "1":
    tempfile.mkdtemp = _mkdtemp_with_default_windows_acl
