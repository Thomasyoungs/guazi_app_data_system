"""Main application logic for the Guazi APP data system.

Replaces the simplified stub with the original system's runtime logic.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .config import ensure_runtime_dirs, load_config, project_path
from .data_collector import DataCollector
from .device_operations import run_device_workflow
from .exceptions import GuaziFlowError, IssueRecorder
from .output_writer import read_json, write_feedback_report, write_json
from .page_recognition import PageRecognizer
from .page_state_machine import PageStateMachine
from .pricing_calculator import calculate_pricing, score_target, select_reference
from .task_normalizer import TargetCarTask


def build_runtime(config_dir: str | None = None) -> dict[str, Any]:
    ensure_runtime_dirs()
    configs = {
        "system": load_config("system.yaml", config_dir),
        "pages": load_config("pages.yaml", config_dir),
        "fields": load_config("fields.yaml", config_dir),
        "actions": load_config("actions.yaml", config_dir),
        "exceptions": load_config("exceptions.yaml", config_dir),
    }
    system = configs["system"]
    audit = AuditLogger(project_path(system["paths"]["audit_log"]))
    issues = IssueRecorder(
        project_path(system["paths"]["issue_log"]),
        configs["exceptions"],
        audit=audit,
    )
    return {"configs": configs, "audit": audit, "issues": issues}


def run_simulation(runtime: dict[str, Any], phone_test: dict[str, Any] | None = None) -> dict[str, Any]:
    configs = runtime["configs"]
    audit: AuditLogger = runtime["audit"]
    issues: IssueRecorder = runtime["issues"]
    machine = PageStateMachine(configs["pages"])
    collector = DataCollector(configs["fields"])

    target = collector.simulated_target()
    references = collector.simulated_reference_cars()

    target_score = score_target(target, configs["fields"], current_year=2026)
    selection = select_reference(target_score, references, configs["fields"], current_year=2026)
    selected_reference = selection["selected_reference"]
    pricing = calculate_pricing(selected_reference, configs["fields"])

    manual_review_reasons = list(target_score.review_reasons)
    manual_review_reasons.extend(selection.get("review_reasons", []))
    if len(references) < 3:
        sample_message = configs["fields"].get("same_source_policy", {}).get(
            "sample_too_small_message", "三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。"
        )
        issues.record("SAMPLE_TOO_SMALL", "S10", sample_message, {"sample_count": len(references)}, "manual_review")

    result = {
        "metadata": {
            "project": "guazi_app_data_system",
            "mode": "simulate" if not phone_test else "device_with_simulation_fallback",
            "field_scope": "contract_only",
        },
        "target_car": target.to_dict(),
        "target_score": target_score.to_dict(),
        "same_source_count": len(references),
        "reference_cars": [reference.to_dict() for reference in references],
        "selected_reference": selected_reference.to_dict() if selected_reference else None,
        "selected_reference_score": selection["selected_score"].to_dict() if selection.get("selected_score") else None,
        "pricing": pricing,
        "manual_review_reasons": _dedupe_keep_order(manual_review_reasons),
        "phone_test": phone_test or {},
    }
    write_json(project_path(configs["system"]["paths"]["result_json"]), result)
    return result


def run_device(runtime: dict[str, Any], task: TargetCarTask | None = None, adb_serial: str | None = None) -> dict[str, Any]:
    """Run the device mode: launch APP and enter search conditions via ADB.

    Args:
        runtime: The runtime dictionary from build_runtime().
        task: Optional TargetCarTask with search conditions. If None, uses default task.
        adb_serial: Optional ADB device serial number.

    Returns:
        dict with device operation results.
    """
    configs = runtime["configs"]
    audit: AuditLogger = runtime["audit"]
    issues: IssueRecorder = runtime["issues"]

    # Use default task if none provided
    if task is None:
        task = TargetCarTask(
            task_id="DEVICE-DEFAULT-001",
            brand="大众",
            series="帕萨特",
            model_year="2020",
            trim="330TSI DSG 尊荣版",
            color="白色",
            registration_date_raw="2020.4",
            vehicle_year=2020,
            mileage_10k_km=7.2,
            transfer_count=0,
            condition_text="正常",
        )

    audit.log("device_mode_start", task_id=task.task_id or "default")

    # Run device workflow
    device_result = run_device_workflow(task, adb_serial)

    if not device_result["success"]:
        issues.record(
            "DEVICE_OPERATION_FAILED",
            "S00",
            f"Device operation failed: {device_result.get('error', 'Unknown error')}",
            {"device_result": device_result},
            "manual_review",
        )
        return {
            "success": False,
            "error": device_result.get("error"),
            "device_result": device_result,
        }

    # Build result
    result = {
        "metadata": {
            "project": "guazi_app_data_system",
            "mode": "device",
            "field_scope": "contract_only",
        },
        "target_car": task.to_dict(),
        "device_operation": device_result,
        "phone_test": {
            "adb_serial": device_result.get("adb_serial"),
            "search_query": device_result.get("search", {}).get("search_query"),
        },
    }

    write_json(project_path(configs["system"]["paths"]["result_json"]), result)
    return result


def export_report(runtime: dict[str, Any], result: dict[str, Any] | None = None, phone_test: dict[str, Any] | None = None, local_tests: dict[str, Any] | None = None) -> Path:
    configs = runtime["configs"]
    issues: IssueRecorder = runtime["issues"]
    result_path = project_path(configs["system"]["paths"]["result_json"])
    report_path = project_path(configs["system"]["paths"]["feedback_report"])
    final_result = result or read_json(result_path)
    write_feedback_report(report_path, project_path(), final_result, phone_test or final_result.get("phone_test", {}), local_tests, issues.read_all())
    return report_path


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="瓜子二手车 APP 数据获取系统")
    parser.add_argument("--mode", choices=["simulate", "device"], default="simulate")
    parser.add_argument("--phone-check-only", action="store_true")
    parser.add_argument("--device-launch-only", action="store_true")
    parser.add_argument("--export-report-only", action="store_true")
    parser.add_argument("--local-test-status", default=None)
    parser.add_argument("--local-test-summary", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    runtime = build_runtime()
    if args.export_report_only:
        report = export_report(runtime, local_tests={"status": args.local_test_status, "summary": args.local_test_summary})
        print(json.dumps({"report": str(report)}, ensure_ascii=False))
        return 0

    phone_test: dict[str, Any] | None = None
    if args.mode == "simulate":
        result = run_simulation(runtime, phone_test=phone_test)
        local_tests = {"status": args.local_test_status or "未记录", "summary": args.local_test_summary or "未记录"}
        report = export_report(runtime, result, phone_test=phone_test, local_tests=local_tests)
        print(json.dumps({"result": str(project_path("output", "result.json")), "report": str(report)}, ensure_ascii=False, indent=2))
        return 0
    return 0
