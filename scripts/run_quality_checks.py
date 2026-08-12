from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tokenize
from pathlib import Path


REQUIRED_FILES = (
    ".gitignore",
    "AGENTS.md",
    "docs/architecture/GIT_CLEAN_MAINLINE_GOVERNANCE.md",
    "guazi_app_data_system/AGENTS.md",
    "guazi_app_data_system/requirements.txt",
    "guazi_app_data_system/scripts/run_tests.ps1",
)

SCAN_DIRS = (
    "scripts",
    "guazi_app_data_system/src",
    "guazi_app_data_system/scripts",
    "guazi_app_data_system/tests",
)

SKIP_DIR_NAMES = {
    ".git",
    ".cache",
    ".pytest_cache",
    ".tmp",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "evidence",
    "node_modules",
    "output",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def print_step(message: str) -> None:
    print(f"[quality] {message}", flush=True)


def check_required_files(root: Path) -> bool:
    print_step("checking required files")
    missing = [path for path in REQUIRED_FILES if not (root / path).exists()]
    if missing:
        for path in missing:
            print(f"missing: {path}")
        return False
    return True


def should_skip(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in SKIP_DIR_NAMES for part in relative.parts)


def iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for scan_dir in SCAN_DIRS:
        base = root / scan_dir
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if not should_skip(path, root):
                files.append(path)
    return sorted(files)


def compile_python_files(root: Path) -> bool:
    print_step("checking Python syntax")
    failures: list[tuple[Path, Exception]] = []
    for path in iter_python_files(root):
        try:
            with tokenize.open(path) as handle:
                source = handle.read()
            compile(source, str(path), "exec")
        except Exception as exc:  # noqa: BLE001 - report any syntax/read failure.
            failures.append((path, exc))

    if failures:
        for path, exc in failures:
            print(f"syntax failure: {path.relative_to(root)}: {exc}")
        return False

    print_step("Python syntax OK")
    return True


def test_environment(root: Path) -> dict[str, str]:
    project = root / "guazi_app_data_system"
    temp_root = project / "artifacts" / "test_runtime"
    temp_root.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["GUAZI_TEST_MODE"] = "1"
    env["TMP"] = str(temp_root)
    env["TEMP"] = str(temp_root)
    env.setdefault("GUAZI_PYTHON", sys.executable)

    src = str(project / "src")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not existing_pythonpath else src + os.pathsep + existing_pythonpath
    return env


def run_tests(root: Path, direct_unittest: bool) -> bool:
    print_step("running tests")
    env = test_environment(root)
    project = root / "guazi_app_data_system"
    existing_entry = project / "scripts" / "run_tests.ps1"

    if existing_entry.exists() and os.name == "nt" and not direct_unittest:
        entry = str(existing_entry).replace("'", "''")
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& '{entry}'; exit $LASTEXITCODE",
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(project / "tests"),
            "-p",
            "test_*.py",
            "-v",
        ]

    print_step("command: " + " ".join(command))
    completed = subprocess.run(command, cwd=root, env=env, check=False)
    return completed.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repository quality checks.")
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Only run config and Python syntax checks.",
    )
    parser.add_argument(
        "--direct-unittest",
        action="store_true",
        help="Use the current Python interpreter instead of the PowerShell test wrapper.",
    )
    args = parser.parse_args()

    root = repo_root()
    checks = [
        check_required_files(root),
        compile_python_files(root),
    ]
    if not args.skip_tests:
        checks.append(run_tests(root, direct_unittest=args.direct_unittest))

    if all(checks):
        print_step("all checks passed")
        return 0

    print_step("checks failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
