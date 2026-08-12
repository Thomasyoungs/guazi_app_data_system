"""Utilities for service-reload evidence snapshots and package validation.

This module is intentionally limited to evidence collection. It does not start
or stop services, call adb, run the Guazi app, or touch task state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import zipfile
from typing import Any, Iterable


DEFAULT_REQUIRED_FILES = (
    "reports/FULL_EVIDENCE_FOR_CHATGPT_REVIEW.txt",
    "reports/ROOT_CAUSE_EVIDENCE_INDEX.md",
    "reports/ROOT_CAUSE_REPORT.md",
    "reports/CHATGPT_COPYABLE_SUMMARY.txt",
    "reports/SERVICE_RELOAD_STATUS_REPORT.md",
)

DEFAULT_REQUIRED_DIRS = (
    "original_evidence_zip/",
    "extracted_evidence/",
    "raw_task_artifacts/",
    "logs/",
    "screenshots_and_xml/",
    "traces/",
    "tests/",
    "reports/",
)

LISTENER_SCRIPT = "feishu_realtime_receiver.py"
DISPATCHER_SCRIPT = "feishu_pricing_dispatcher.py"
RUNNER_SCRIPTS = (
    "pricing_runner.py",
    "runtime_s01_to_s10_mainline.py",
    "runtime_s10_to_s16_mainline.py",
)


@dataclass(frozen=True)
class ProcessInfo:
    process_id: int | None
    parent_process_id: int | None
    creation_date: str | None
    executable_path: str
    command_line: str

    @classmethod
    def from_any(cls, value: Any) -> "ProcessInfo":
        def get(name: str, default: Any = None) -> Any:
            if isinstance(value, dict):
                return value.get(name, default)
            return getattr(value, name, default)

        return cls(
            process_id=_coerce_int(get("ProcessId")),
            parent_process_id=_coerce_int(get("ParentProcessId")),
            creation_date=str(get("CreationDate") or "") or None,
            executable_path=str(get("ExecutablePath") or ""),
            command_line=str(get("CommandLine") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ProcessId": self.process_id,
            "ParentProcessId": self.parent_process_id,
            "CreationDate": self.creation_date,
            "ExecutablePath": self.executable_path,
            "CommandLine": self.command_line,
        }


def build_service_process_snapshot(processes: Iterable[Any], *, project_root: str | Path) -> dict[str, Any]:
    """Return listener/dispatcher evidence without counting collector shells.

    Only Python/py executables directly running the service scripts are counted
    as services. PowerShell commands that merely contain service script text are
    retained separately as evidence collectors.
    """

    root_text = _normalize_path_text(str(project_root))
    infos = [ProcessInfo.from_any(item) for item in processes]
    listener: list[ProcessInfo] = []
    dispatcher: list[ProcessInfo] = []
    runner: list[ProcessInfo] = []
    evidence_collectors: list[ProcessInfo] = []
    runner_wrappers: list[ProcessInfo] = []

    for info in infos:
        command = _normalize_path_text(info.command_line)
        in_project = root_text in command
        if not in_project:
            continue
        if _is_python_executable(info.executable_path):
            if _command_runs_script(command, LISTENER_SCRIPT):
                listener.append(info)
            elif _command_runs_script(command, DISPATCHER_SCRIPT):
                dispatcher.append(info)
            elif any(_command_runs_script(command, script) for script in RUNNER_SCRIPTS):
                runner.append(info)
            continue
        if _command_mentions_any(command, (LISTENER_SCRIPT, DISPATCHER_SCRIPT)):
            evidence_collectors.append(info)
        if _is_powershell_executable(info.executable_path) and _command_mentions_any(command, RUNNER_SCRIPTS):
            runner_wrappers.append(info)

    wrapper_child_runner = [
        child
        for child in infos
        if _is_python_executable(child.executable_path)
        and child.parent_process_id in {wrapper.process_id for wrapper in runner_wrappers}
        and any(_command_runs_script(_normalize_path_text(child.command_line), script) for script in RUNNER_SCRIPTS)
    ]

    return {
        "listener_count": len(listener),
        "dispatcher_count": len(dispatcher),
        "listeners": [item.as_dict() for item in listener],
        "dispatchers": [item.as_dict() for item in dispatcher],
        "active_runner_count": len(runner) + len(wrapper_child_runner),
        "python_runners": [item.as_dict() for item in runner],
        "powershell_runner_wrappers": [item.as_dict() for item in runner_wrappers],
        "wrapper_child_runners": [item.as_dict() for item in wrapper_child_runner],
        "evidence_collectors": [item.as_dict() for item in evidence_collectors],
    }


def normalize_zip_entry_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def validate_chatgpt_review_package(
    package_dir: str | Path,
    zip_path: str | Path,
    *,
    required_files: Iterable[str] = DEFAULT_REQUIRED_FILES,
    required_dirs: Iterable[str] = DEFAULT_REQUIRED_DIRS,
) -> dict[str, Any]:
    package = Path(package_dir)
    archive_path = Path(zip_path)
    entries = _zip_entries(archive_path) if archive_path.exists() else []
    entry_set = set(entries)
    required_file_list = [normalize_zip_entry_name(item) for item in required_files]
    required_dir_list = [
        normalize_zip_entry_name(item).rstrip("/") + "/" for item in required_dirs
    ]

    missing_files = [
        rel
        for rel in required_file_list
        if rel not in entry_set and not (package / Path(rel)).exists()
    ]
    missing_dirs = [
        rel
        for rel in required_dir_list
        if not any(entry.startswith(rel) for entry in entries)
        and not (package / Path(rel)).exists()
    ]
    full_evidence = package / "reports" / "FULL_EVIDENCE_FOR_CHATGPT_REVIEW.txt"
    root_cause_index = package / "reports" / "ROOT_CAUSE_EVIDENCE_INDEX.md"
    root_cause_report = package / "reports" / "ROOT_CAUSE_REPORT.md"
    chatgpt_summary = package / "reports" / "CHATGPT_COPYABLE_SUMMARY.txt"
    can_upload = not missing_files and not missing_dirs
    return {
        "package_dir": str(package),
        "package_zip": str(archive_path),
        "package_dir_exists": package.exists(),
        "package_zip_exists": archive_path.exists(),
        "package_zip_size_bytes": archive_path.stat().st_size if archive_path.exists() else 0,
        "package_zip_entry_count": len(entries),
        "zip_entry_paths_normalized_to_forward_slash": all("\\" not in entry for entry in entries),
        "missing_required_files": missing_files,
        "missing_required_dirs": missing_dirs,
        "full_evidence_exists": full_evidence.exists(),
        "root_cause_index_exists": root_cause_index.exists(),
        "root_cause_report_exists": root_cause_report.exists(),
        "chatgpt_summary_exists": chatgpt_summary.exists(),
        "can_upload_to_chatgpt": can_upload,
    }


def build_post_start_feedback_classification_load_check(project_root: str | Path) -> dict[str, Any]:
    """Static evidence that post-start failures cannot use pre-start copy.

    The assertion intentionally checks multiple fields and the exact production
    business copy. Older reload reports only searched for one legacy marker,
    which produced false negatives after the implementation moved to structured
    fields.
    """

    root = Path(project_root)
    task_store = _read_text(root / "scripts" / "feishu_task_store.py")
    formatter = _read_text(root / "scripts" / "feishu_result_formatter.py")
    dispatcher = _read_text(root / "scripts" / "feishu_pricing_dispatcher.py")
    duplicate_message = "系统已开始自动定价，但参考车回采阶段未能继续执行，已安全停止，已通知管理员处理。"
    generic_message = "系统已开始自动定价，但在参考车采集阶段未能形成完整结果，已安全停止，已通知管理员处理。"
    required_task_store_markers = {
        "post_start_failure": "post_start_failure" in task_store,
        "post_start_failure_stage": "post_start_failure_stage" in task_store,
        "primary_error_code": "primary_error_code" in task_store,
        "wrapper_error_code": "wrapper_error_code" in task_store,
        "pricing_result_issue_code": "pricing_result_issue_code" in task_store,
        "binding_stop_code": "binding_stop_code" in task_store,
        "second_stage_entered": "second_stage_entered" in task_store,
        "reached_s10_before_failure": "reached_s10_before_failure" in task_store,
        "entered_s11_before_failure": "entered_s11_before_failure" in task_store,
        "post_start_not_started_template_blocked": "post_start_not_started_template_blocked" in task_store,
        "duplicate_business_message": duplicate_message in task_store,
        "generic_business_message": generic_message in task_store,
    }
    formatter_markers = {
        "duplicate_business_message": duplicate_message in formatter,
        "duplicate_code_unwrapped": "DUPLICATE_REFERENCE_CLICK_BLOCKED" in formatter
        and "RESULT_SCHEMA_INVALID_FOR_PRICING" in formatter,
    }
    dispatcher_markers = {
        "send_result_live_path_present": "send_result_live" in dispatcher or "final_feedback" in dispatcher,
    }
    loaded = all(required_task_store_markers.values()) and all(formatter_markers.values())
    return {
        "task_store_required_markers": required_task_store_markers,
        "formatter_required_markers": formatter_markers,
        "dispatcher_markers": dispatcher_markers,
        "task_store_has_post_start_duplicate_message": required_task_store_markers["duplicate_business_message"],
        "post_start_feedback_classification_loaded": loaded,
    }


def compress_directory_with_posix_paths(source_dir: str | Path, zip_path: str | Path) -> None:
    """Create a zip whose entry names always use forward slashes."""

    source = Path(source_dir)
    archive = Path(zip_path)
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(source).as_posix())


def _zip_entries(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        return [normalize_zip_entry_name(item.filename) for item in zf.infolist()]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:
        return ""


def _normalize_path_text(value: str) -> str:
    return value.replace("\\", "/").lower()


def _is_python_executable(path: str) -> bool:
    name = Path(path).name.lower()
    return name in {"python.exe", "py.exe"}


def _is_powershell_executable(path: str) -> bool:
    return Path(path).name.lower() in {"powershell.exe", "pwsh.exe"}


def _command_runs_script(command: str, script_name: str) -> bool:
    return f"/{script_name.lower()}" in command or command.endswith(script_name.lower())


def _command_mentions_any(command: str, script_names: Iterable[str]) -> bool:
    return any(script.lower() in command for script in script_names)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
