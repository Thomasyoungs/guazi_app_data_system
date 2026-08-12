"""CLI entry point for the first runnable Guazi APP data system."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .action_executor import ActionExecutor
from .adb_device_gate import run_adb_device_gate
from .app_startup import AdbClient, SELECT_CAR_LABEL
from .audit import AuditLogger
from .config_loader import ensure_runtime_dirs, load_config, project_path, project_root
from .data_collection import DataCollector
from .exception_handler import IssueRecorder
from .field_validation import FieldContract
from .issue_classifier import IssueClassifier
from .learning_loop import LearningLoop
from .output_writer import read_json, write_feedback_report, write_json
from .page_recognition import PageRecognizer
from .page_state_machine import PageStateMachine
from .pricing import calculate_pricing, score_target, select_reference


def build_runtime() -> dict[str, Any]:
    ensure_runtime_dirs()
    configs = {
        "system": load_config("system.yaml"),
        "pages": load_config("pages.yaml"),
        "fields": load_config("fields.yaml"),
        "actions": load_config("actions.yaml"),
        "exceptions": load_config("exceptions.yaml"),
    }
    system = configs["system"]
    audit = AuditLogger(project_path(system["paths"]["audit_log"]))
    learning = LearningLoop(project_root(), configs["exceptions"], configs["pages"], configs["actions"])
    classifier = IssueClassifier(configs["pages"], configs["actions"])
    issues = IssueRecorder(
        project_path(system["paths"]["issue_log"]),
        configs["exceptions"],
        learning_loop=learning,
        issue_classifier=classifier,
        audit=audit,
    )
    return {"configs": configs, "audit": audit, "issues": issues, "learning_loop": learning}


def _learning_loop_allows(issue: dict[str, Any], action_name: str) -> bool:
    lookup = issue.get("knowledge_lookup") or {}
    allowed = {str(action) for action in lookup.get("allowed_auto_actions", [])}
    return bool(lookup.get("auto_continue_allowed")) and action_name in allowed


def _capture_runtime_preflight(client: Any, system: dict[str, Any], stem: str) -> dict[str, Any]:
    screenshot_path = project_path(system["paths"]["screenshots"], f"{stem}.png")
    return client.runtime_preflight_snapshot(screenshot_path)


def _visible_texts_from_snapshot(snapshot: dict[str, Any]) -> list[str]:
    xml_text = str(snapshot.get("fresh_xml") or "")
    ordered: list[str] = []
    if xml_text.strip():
        try:
            root = ElementTree.fromstring(xml_text)
            for node in root.iter("node"):
                for candidate in (node.attrib.get("text") or "", node.attrib.get("content-desc") or ""):
                    value = str(candidate).strip()
                    if value and value not in ordered:
                        ordered.append(value)
        except ElementTree.ParseError:
            texts = re.findall(r'text="([^"]+)"', xml_text)
            for text in texts:
                value = text.strip()
                if value and value not in ordered:
                    ordered.append(value)
    return ordered


def _looks_like_third_party_login_blocker(snapshot: dict[str, Any]) -> bool:
    visible_texts = _visible_texts_from_snapshot(snapshot)
    blob = "".join(visible_texts)
    foreground_package = str(snapshot.get("foreground_package") or "")
    xml_package = str(snapshot.get("xml_package") or "")
    login_markers = ["欢迎登录", "请输入手机号码", "请输入验证码", "账号密码登录", "登录"]
    marker_hits = sum(1 for marker in login_markers if marker in blob)
    package_hint = any(token in value for token in (foreground_package, xml_package) for value in ("tqaccountcenter", "account"))
    return package_hint or marker_hits >= 2


def _looks_like_dismissible_third_party_login(snapshot: dict[str, Any]) -> bool:
    visible_texts = _visible_texts_from_snapshot(snapshot)
    return "稍后" in "".join(visible_texts) and _looks_like_third_party_login_blocker(snapshot)


def _looks_like_third_party_login_text(text: str) -> bool:
    normalized = str(text or "").replace(" ", "")
    if not normalized:
        return False
    login_markers = ["欢迎登录", "请输入手机号码", "请输入验证码", "账号密码登录", "登录"]
    return sum(1 for marker in login_markers if marker in normalized) >= 2


def looks_like_s01_home(snapshot: dict[str, Any]) -> bool:
    normalized = normalize_visible_texts(_visible_texts_from_snapshot(snapshot))
    return all(token in normalized for token in ["\u9996\u9875", SELECT_CAR_LABEL, "\u5356\u8f66", "\u6211\u7684"])


def looks_like_s02_select_car(snapshot: dict[str, Any]) -> bool:
    normalized = normalize_visible_texts(_visible_texts_from_snapshot(snapshot))
    return all(token in normalized for token in ["\u9996\u9875", SELECT_CAR_LABEL, "\u5356\u8f66", "\u6211\u7684", "\u54c1\u724c"])


def normalize_visible_texts(texts: list[str]) -> str:
    return re.sub(r"\s+", "", "".join(texts))



def _dismiss_third_party_login_later_once(
    client: Any,
    system: dict[str, Any],
    issues: IssueRecorder,
    probe: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    stem: str,
) -> tuple[dict[str, Any], bool]:
    if not _looks_like_dismissible_third_party_login(snapshot):
        return snapshot, False

    visible_texts = _visible_texts_from_snapshot(snapshot)
    issue = issues.record(
        "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
        "DEVICE",
        "Dismissible third-party blocking page is visible; try tapping 稍后 once before asking for manual handling.",
        {
            **snapshot,
            "visible_texts": visible_texts,
            "dismiss_control_text": "稍后",
        },
    )
    probe.setdefault("runtime_preflight_issues", []).append(issue)
    if not _learning_loop_allows(issue, "tap_third_party_login_later_once"):
        return snapshot, False
    if not hasattr(client, "tap_text"):
        return snapshot, False

    tap_result = client.tap_text("稍后")
    probe["third_party_login_dismiss_result"] = {
        "clicked_later": bool(tap_result.success),
        "returncode": tap_result.returncode,
        "stdout": tap_result.stdout,
        "stderr": tap_result.stderr,
    }
    if not tap_result.success:
        return snapshot, False

    time.sleep(1.5)
    refreshed = _capture_runtime_preflight(client, system, stem)
    probe["runtime_preflight_after_later_dismiss"] = refreshed
    return refreshed, True


def run_simulation(runtime: dict[str, Any], phone_test: dict[str, Any] | None = None) -> dict[str, Any]:
    configs = runtime["configs"]
    audit: AuditLogger = runtime["audit"]
    issues: IssueRecorder = runtime["issues"]
    machine = PageStateMachine(configs["pages"])
    executor = ActionExecutor(machine, configs["actions"], audit, issues, dry_run=True)
    collector = DataCollector(configs["fields"])
    contract = FieldContract(configs["fields"])

    _simulate_state_actions(executor)
    target = collector.simulated_target()
    references = collector.simulated_reference_cars()

    for error in contract.validate_target(target.to_dict()):
        issues.record("FIELD_MISSING", "S15", error, {"target": target.task_id}, "manual_review")
    target_score = score_target(target, configs["fields"], current_year=2026)
    selection = select_reference(target_score, references, configs["fields"], current_year=2026)
    selected_reference = selection["selected_reference"]
    pricing = calculate_pricing(selected_reference, configs["fields"])

    manual_review_reasons = list(target_score.review_reasons)
    manual_review_reasons.extend(selection["review_reasons"])
    if len(references) < 3:
        sample_message = configs["fields"].get("same_source_policy", {}).get("sample_too_small_message", "三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。")
        issues.record("SAMPLE_TOO_SMALL", "S10", sample_message, {"sample_count": len(references)}, "manual_review")

    for reference in references:
        for error in contract.validate_reference(reference.to_dict()):
            issues.record("FIELD_MISSING", "S15", error, {"reference_index": reference.reference_index}, "manual_review")

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
        "selected_reference_score": selection["selected_score"].to_dict() if selection["selected_score"] else None,
        "pricing": pricing,
        "manual_review_reasons": dedupe_keep_order(manual_review_reasons),
        "phone_test": phone_test or {},
    }
    write_json(project_path(configs["system"]["paths"]["result_json"]), result)
    return result


def _simulate_state_actions(executor: ActionExecutor) -> None:
    sequence = [
        ("S00", "tap_left_bottom_skip"),
        ("S01", "click_bottom_select_car_tab"),
        ("S02", "tap_brand_filter"),
        ("S03", "tap_brand_letter"),
        ("S03", "tap_target_brand"),
        ("S04", "click_series_model_button"),
        ("S05", "tap_target_year"),
        ("S05", "tap_exact_trim"),
        ("S05", "tap_green_confirm"),
        ("S06", "tap_trim_filter"),
        ("S07", "tap_color_filter"),
        ("S07", "tap_target_color"),
        ("S07", "tap_age_filter"),
        ("S07", "set_exact_age"),
        ("S07", "tap_view_cars"),
        ("S08", "collect_list_whitelist_fields"),
        ("S08", "tap_sort_if_present"),
        ("S09", "tap_price_low_to_high"),
        ("S10", "tap_next_car_by_price_order"),
        ("S11", "collect_transfer_count"),
        ("S11", "scroll_to_report"),
        ("S11", "tap_full_report"),
        ("S12", "collect_claim_count"),
        ("S12", "collect_max_amount"),
        ("S12", "scroll_to_body_appearance"),
        ("S12", "tap_body_appearance"),
        ("S13", "collect_repair_counts"),
        ("S13", "tap_repair_item_if_nonzero"),
        ("S14", "swipe_next_damage"),
        ("S15", "score_reference_car"),
        ("S16", "calculate_prices"),
        ("S17", "write_pricing_result"),
    ]
    context = {
        "target_brand_initial": "S",
        "target_brand": "???",
        "target_series": "??",
        "actual_click_target": "??",
        "actual_click_target_role": "series_model_button",
        "actual_click_target_series": "??",
        "series_row_found": True,
        "series_model_button_found": True,
        "same_row_or_card": True,
        "target_model_year": "2020?",
        "target_trim": "2020? ?? 1.5L ?????",
        "target_color": "?",
        "target_age_years": 6,
        "expected_next_state": "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
        "actual_next_state": "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
    }
    for state_id, action_id in sequence:
        executor.execute(state_id, action_id, context)

def run_phone_probe(runtime: dict[str, Any], launch: bool = True) -> dict[str, Any]:
    configs = runtime["configs"]
    audit: AuditLogger = runtime["audit"]
    issues: IssueRecorder = runtime["issues"]
    system = configs["system"]
    client = AdbClient()
    probe: dict[str, Any] = {
        "adb_available": client.available,
        "adb_path": str(client.adb_path) if client.adb_path else None,
        "device_gate": {},
        "devices": [],
        "device_info": {},
        "package_discovery": {},
        "wake_result": {},
        "swipe_unlock_result": {},
        "runtime_preflight": {},
        "runtime_preflight_after_wake": {},
        "runtime_preflight_after_swipe": {},
        "runtime_preflight_issues": [],
        "launch_result": {},
        "screenshot_path": None,
        "recognized_start_state": None,
        "failure_summary": "",
    }
    audit.log("phone_probe_started", adb_path=probe["adb_path"], adb_available=client.available)
    gate = run_adb_device_gate(client, issues=issues, audit=audit, allow_transient_recovery=True)
    probe["device_gate"] = gate
    probe["devices"] = gate.get("devices", [])
    if not gate.get("passed"):
        status = gate.get("status")
        if status == "ADB_NOT_FOUND":
            probe["failure_summary"] = "- Phone test failed: adb executable was not found."
        elif status == "unauthorized":
            probe["failure_summary"] = "- Phone test failed: ADB device is unauthorized."
        elif status == "offline":
            probe["failure_summary"] = "- Phone test failed: ADB device is offline."
        elif status == "TARGET_ADB_SERIAL_NOT_CONFIGURED":
            probe["failure_summary"] = "- Phone test failed: target ADB serial is not configured."
        elif status == "TARGET_ADB_DEVICE_NOT_CONNECTED":
            probe["failure_summary"] = "- Phone test failed: configured target ADB device is not connected."
        elif status == "TARGET_ADB_DEVICE_UNAUTHORIZED":
            probe["failure_summary"] = "- Phone test failed: configured target ADB device is unauthorized."
        elif status == "TARGET_ADB_DEVICE_OFFLINE":
            probe["failure_summary"] = "- Phone test failed: configured target ADB device is offline."
        elif status == "DEVICE_NOT_FOUND":
            probe["failure_summary"] = "- Phone test failed: configured target Android device was not ready."
        else:
            probe["failure_summary"] = "- Phone test failed: no ready Android device was found."
        return probe

    probe["device_info"] = client.device_info()
    if not launch:
        probe["failure_summary"] = ""
        return probe

    screenshot_path = project_path(system["paths"]["screenshots"], "startup.png")
    if hasattr(client, "runtime_preflight_snapshot"):
        preflight = _capture_runtime_preflight(client, system, "runtime_preflight_before_launch")
        probe["runtime_preflight"] = preflight
        preflight, dismissed_later = _dismiss_third_party_login_later_once(
            client,
            system,
            issues,
            probe,
            preflight,
            stem="runtime_preflight_after_later_dismiss",
        )
        if dismissed_later:
            probe["runtime_preflight_after_later_dismiss"] = preflight
        elif _looks_like_third_party_login_blocker(preflight):
            probe["failure_summary"] = "- APP launch probe stopped: third-party blocking page has no dismissible 绋嶅悗 control."
            return probe
        screenshot_result = client.screenshot(screenshot_path)
        probe["screenshot_path"] = str(screenshot_path) if screenshot_result.success else None
        ui = client.dump_ui_text()
    else:
        screenshot_result = client.screenshot(screenshot_path)
        probe["screenshot_path"] = str(screenshot_path) if screenshot_result.success else None
        ui = client.dump_ui_text()
    recognizer = PageRecognizer(configs["pages"])
    recognized = recognizer.classify_start_page(ui.get("text", ""))
    probe["recognized_start_state"] = recognized
    return probe

def write_probe_only_result(runtime: dict[str, Any], phone_test: dict[str, Any], mode: str) -> dict[str, Any]:
    configs = runtime["configs"]
    result = {
        "metadata": {
            "project": "guazi_app_data_system",
            "mode": mode,
            "field_scope": "contract_only",
        },
        "phone_test": phone_test,
    }
    write_json(project_path(configs["system"]["paths"]["result_json"]), result)
    return result


def _load_real_device_mainline_runner() -> tuple[Path, Any | None]:
    script_path = project_root() / "scripts" / "runtime_s10_to_s16_mainline.py"
    if not script_path.exists():
        return script_path, None

    spec = importlib.util.spec_from_file_location("runtime_s10_to_s16_mainline", script_path)
    if spec is None or spec.loader is None:
        return script_path, None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runner = getattr(module, "run_s10_to_s16_mainline", None)
    return script_path, runner if callable(runner) else None


def _write_missing_real_executor_result(runtime: dict[str, Any], phone_test: dict[str, Any] | None, script_path: Path) -> dict[str, Any]:
    configs = runtime["configs"]
    result = {
        "metadata": {
            "project": "guazi_app_data_system",
            "mode": "device",
            "field_scope": "contract_only",
        },
        "status": "PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE",
        "message": "Device mode cannot fall back to dry_run simulation when the S10-S16 real-device executor is missing.",
        "expected_script": str(script_path),
        "phone_test": phone_test or {},
    }
    write_json(project_path(configs["system"]["paths"]["result_json"]), result)
    return result


def export_report(runtime: dict[str, Any], result: dict[str, Any] | None = None, phone_test: dict[str, Any] | None = None, local_tests: dict[str, Any] | None = None) -> Path:
    configs = runtime["configs"]
    issues: IssueRecorder = runtime["issues"]
    result_path = project_path(configs["system"]["paths"]["result_json"])
    report_path = project_path(configs["system"]["paths"]["feedback_report"])
    final_result = result or read_json(result_path)
    write_feedback_report(report_path, project_root(), final_result, phone_test or final_result.get("phone_test", {}), local_tests, issues.read_all())
    return report_path


def dedupe_keep_order(items: list[str]) -> list[str]:
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
    if args.mode == "device" or args.phone_check_only or args.device_launch_only:
        phone_test = run_phone_probe(runtime, launch=not args.phone_check_only or args.device_launch_only)
        if args.phone_check_only or args.device_launch_only:
            result = write_probe_only_result(runtime, phone_test, "phone_check" if args.phone_check_only else "package_probe")
            report = export_report(runtime, result, phone_test=phone_test)
            print(json.dumps({"phone_test": phone_test, "result": str(project_path("output", "result.json")), "report": str(report)}, ensure_ascii=False, indent=2))
            return 0
    if args.mode == "device":
        script_path, runner = _load_real_device_mainline_runner()
        if runner is None:
            result = _write_missing_real_executor_result(runtime, phone_test, script_path)
            report = export_report(runtime, result, phone_test=phone_test)
            print(
                json.dumps(
                    {
                        "status": "PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE",
                        "expected_script": str(script_path),
                        "result": str(project_path("output", "result.json")),
                        "report": str(report),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        result = runner(runtime, phone_test=phone_test)
        local_tests = {"status": args.local_test_status or "未记录", "summary": args.local_test_summary or "未记录"}
        report = export_report(runtime, result, phone_test=phone_test, local_tests=local_tests)
        print(json.dumps({"result": str(project_path("output", "result.json")), "report": str(report)}, ensure_ascii=False, indent=2))
        return 0

    result = run_simulation(runtime, phone_test=phone_test)
    local_tests = {"status": args.local_test_status or "未记录", "summary": args.local_test_summary or "未记录"}
    report = export_report(runtime, result, phone_test=phone_test, local_tests=local_tests)
    print(json.dumps({"result": str(project_path("output", "result.json")), "report": str(report)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
