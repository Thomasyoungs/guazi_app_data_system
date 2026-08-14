"""Guazi APP data system entry point.

This module is the top-level runtime for the project: it assembles the runtime
config, switches between simulation and real-device execution, and coordinates the
main business pipeline from task input to final pricing output.

The surrounding package is organized around a few core responsibilities:
- task normalization and validation
- device startup and page recognition
- state-machine-driven action execution
- scoring and pricing of the target vehicle
- issue recording, audit logging and learning-loop recovery
"""

from __future__ import annotations

# Allow running this module directly as a script (python main.py) while still
# keeping the package-relative imports used throughout the project. When the
# module is executed as a script, __package__ is None or empty which breaks
# relative imports like "from .action_executor import ...". Patch sys.path and
# set __package__ so the relative imports resolve correctly.
if __package__ in (None, ""):
    import os
    import sys

    # package layout: .../app/src/guazi_app_data_system/main.py
    # pkg_root should be app/src (so imports like 'guazi_app_data_system.xxx' resolve)
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)
    # also add the outer 'app' directory and its 'scripts' folder so scripts
    # (which do top-level imports like `import s10s16_clean`) can be imported
    app_root = os.path.dirname(pkg_root)
    scripts_dir = os.path.join(app_root, "scripts")
    if app_root not in sys.path:
        sys.path.insert(0, app_root)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    # declare package name so relative imports work
    __package__ = "guazi_app_data_system"

import argparse
import importlib.util
import json
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .action_executor import ActionExecutor
from .adb_device_gate import run_adb_device_gate
from .app_startup import AdbClient, SELECT_CAR_LABEL
from .audit import AuditLogger
from .config_loader import ensure_runtime_dirs, load_config, project_path, project_root
from .data_collection import DataCollector, parse_condition_text
from .exception_handler import IssueRecorder
from .field_validation import FieldContract
from .issue_classifier import IssueClassifier
from .learning_loop import LearningLoop
from .models import TargetCar, ReferenceCar
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
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - fail safely and let caller know executor is unavailable
        # If the executor or its dependencies are missing or fail to import, report None so
        # caller can write a helpful diagnostics result rather than crashing.
        try:
            # attempt to capture a short traceback message
            tb = traceback.format_exc()
        except Exception:
            tb = str(exc)
        # attach an attribute to the script path object for diagnostics (caller may display it)
        script_path = script_path
        # write a small diagnostics file next to the script to help debugging
        diag_path = script_path.with_suffix(script_path.suffix + ".import_error.txt")
        try:
            diag_path.write_text(tb, encoding="utf-8")
        except Exception:
            pass
        return script_path, None
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
    parser.add_argument("--auto-launch-app", action="store_true", help="Auto-launch Guazi app on the connected device before running device mode")
    parser.add_argument("--test-task-file", default=None, help="Path to local JSON file to use as current_target_task.json for testing")
    parser.add_argument("--quick-pricing", action="store_true", help="Run a quick device pricing flow: open app, search/filter using the provided test-task JSON and run scoring/pricing (skips full S10-S16 mainline). Requires --test-task-file.")
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
        # If a test task file is provided, copy it into the runtime data location so the runner uses it
        if args.test_task_file:
            try:
                src = Path(args.test_task_file)
                if src.exists():
                    dst = project_path(runtime["configs"]["system"]["paths"]["data"], "current_target_task.json")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    import shutil

                    shutil.copyfile(str(src), str(dst))
                    runtime["issues"].audit.log("test_task_installed", src=str(src), dst=str(dst))
                    print(json.dumps({"test_task_installed": str(dst)}, ensure_ascii=False))
                else:
                    print(json.dumps({"error": f"test task file not found: {src}"}, ensure_ascii=False))
                    return 1
            except Exception as exc:
                print(json.dumps({"error": f"failed to install test task file: {exc}"}, ensure_ascii=False))
                return 1

        # Optionally auto-launch the Guazi app before running the device mainline
        if args.auto_launch_app:
            try:
                client = AdbClient()
                launch_comp = "com.ganji.android.haoche_c/com.cars.guazi.app.home.MainActivity"
                launch_res = client.launch_activity_component(launch_comp, wait_seconds=10)
                phone_test.setdefault("launch_result", {})
                phone_test["launch_result"]= {
                    "component": launch_comp,
                    "ok": bool(launch_res.get("ok")),
                    "launch_stdout": getattr(launch_res.get("launch_result"), "stdout", "") if launch_res.get("launch_result") else "",
                    "snapshot_foreground": (launch_res.get("snapshot") or {}).get("foreground_package") if isinstance(launch_res.get("snapshot"), dict) else None,
                }
                print(json.dumps({"auto_launch_result": phone_test["launch_result"]}, ensure_ascii=False))
            except Exception as exc:
                print(json.dumps({"error": f"failed to auto-launch app: {exc}"}, ensure_ascii=False))
                return 1

        # Support a quick-pricing device mode that uses the provided test-task JSON
        # to open the app, perform the search/sort steps, collect visible listing data,
        # and run the scoring/ pricing logic without executing the full S10-S16 mainline.
        if args.quick_pricing:
            if not args.test_task_file:
                print(json.dumps({"error": "--quick-pricing requires --test-task-file to be provided"}, ensure_ascii=False))
                return 1
            try:
                # determine where the runtime task file was copied (support multiple possible locations)
                candidates = []
                try:
                    candidates.append(project_path(runtime["configs"]["system"]["paths"]["data"], "current_target_task.json"))
                except Exception:
                    pass
                candidates.append(project_path("data", "current_target_task.json"))
                # also respect explicit runtime setting if present
                try:
                    cur = runtime["configs"]["system"]["runtime"].get("current_task_path")
                    if cur:
                        candidates.append(project_path(cur))
                except Exception:
                    pass

                task_path = None
                for p in candidates:
                    if isinstance(p, Path) and p.exists():
                        task_path = p
                        break
                if task_path is None:
                    print(json.dumps({"error": "current_target_task.json not found in expected locations"}, ensure_ascii=False))
                    return 1

                task_data = json.loads(task_path.read_text(encoding="utf-8"))
                # Build TargetCar from task_data (best-effort)
                target = TargetCar(
                    task_id=str(task_data.get("task_id") or ""),
                    brand=task_data.get("brand") or "",
                    series=task_data.get("series") or "",
                    model_year=str(task_data.get("year_model") or task_data.get("model_year") or ""),
                    trim=task_data.get("config_model") or task_data.get("trim") or "",
                    color=task_data.get("color") or "",
                    registration_date=str(task_data.get("register_date") or task_data.get("registration_date") or ""),
                    mileage_10k_km=float(task_data.get("mileage_10k_km") or task_data.get("display_mileage_wan_km") or 0.0),
                    transfer_count=int(task_data.get("transfer_count") or 0),
                    condition_text=str(task_data.get("condition_text") or ""),
                    accident_count=task_data.get("accident_count"),
                    max_accident_amount=task_data.get("max_accident_amount"),
                )
                target.panel_repairs = parse_condition_text(target.condition_text)

                # prepare device client and executor
                client = AdbClient()
                machine = PageStateMachine(runtime["configs"]["pages"])
                executor = ActionExecutor(machine, runtime["configs"]["actions"], runtime["audit"], runtime["issues"], device=client, dry_run=False)

                # minimal action sequence to reach listing and sort by price low->high
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
                ]
                context = {
                    "target_brand": target.brand,
                    "target_series": target.series,
                    "target_model_year": target.model_year,
                    "target_trim": target.trim,
                    "target_color": target.color,
                }
                for state_id, action_id in sequence:
                    try:
                        executor.execute(state_id, action_id, context)
                        time.sleep(0.2)
                    except Exception as exc:
                        # record and continue where possible
                        runtime["issues"].record("ACTION_EXECUTION_FAILED", state_id, str(exc), {"action_id": action_id}, "manual_review")

                # dump UI and extract visible text to find prices/mileage/year
                ui = client.dump_ui_text()
                text_blob = str((ui.get("text") or ""))
                # extract lists of candidate reference values
                price_matches = re.findall(r"(\d+(?:\.\d+)?)\s*万", text_blob)
                mileage_matches = re.findall(r"(\d+(?:\.\d+)?)\s*万公里", text_blob)
                year_matches = re.findall(r"(20\d{2})", text_blob)

                references: list[ReferenceCar] = []
                max_candidates = max(len(price_matches), 0)
                for i in range(min(max_candidates, 10)):
                    try:
                        price = float(price_matches[i]) if i < len(price_matches) else 0.0
                    except Exception:
                        price = 0.0
                    try:
                        mileage = float(mileage_matches[i]) if i < len(mileage_matches) else 0.0
                    except Exception:
                        mileage = 0.0
                    try:
                        year = int(year_matches[i]) if i < len(year_matches) else (int(target.model_year[:4]) if target.model_year and target.model_year[:4].isdigit() else 0)
                    except Exception:
                        year = 0
                    ref = ReferenceCar(
                        reference_index=i + 1,
                        list_price_10k=price,
                        list_year=year,
                        list_mileage_10k_km=mileage,
                        transfer_count=0,
                        accident_count=0,
                        max_accident_amount=None,
                        repair_counts={},
                        panel_repairs=[],
                    )
                    references.append(ref)

                if not references:
                    res = {"metadata": {"project": "guazi_app_data_system", "mode": "device_quick_pricing"}, "status": "NO_REFERENCES_FOUND", "text_blob_length": len(text_blob)}
                    write_json(project_path(runtime["configs"]["system"]["paths"]["result_json"]), res)
                    report = export_report(runtime, res, phone_test=phone_test)
                    print(json.dumps({"status": "NO_REFERENCES_FOUND", "report": str(report)}, ensure_ascii=False))
                    return 1

                # score and select
                current_year = time.localtime().tm_year
                target_score = score_target(target, runtime["configs"]["fields"], current_year=current_year)
                selection = select_reference(target_score, references, runtime["configs"]["fields"], current_year=current_year)
                selected_reference = selection.get("selected_reference")
                pricing = calculate_pricing(selected_reference, runtime["configs"]["fields"]) if selected_reference else {}

                result = {
                    "metadata": {"project": "guazi_app_data_system", "mode": "device_quick_pricing", "field_scope": "contract_only"},
                    "target_car": target.to_dict(),
                    "target_score": target_score.to_dict(),
                    "reference_cars": [r.to_dict() for r in references],
                    "selected_reference": selected_reference.to_dict() if selected_reference else None,
                    "pricing": pricing,
                    "phone_test": phone_test or {},
                }
                write_json(project_path(runtime["configs"]["system"]["paths"]["result_json"]), result)
                report = export_report(runtime, result, phone_test=phone_test)
                print(json.dumps({"result": str(project_path("output", "result.json")), "report": str(report)}, ensure_ascii=False, indent=2))
                return 0
            except Exception as exc:
                tb = traceback.format_exc()
                err_result = {"status": "QUICK_PRICING_EXCEPTION", "error": str(exc), "traceback": tb}
                write_json(project_path(runtime["configs"]["system"]["paths"]["result_json"]), err_result)
                report = export_report(runtime, err_result, phone_test=phone_test)
                print(json.dumps({"status": "QUICK_PRICING_EXCEPTION", "error": str(exc), "traceback": tb, "report": str(report)}, ensure_ascii=False, indent=2))
                return 1

        # fallback to full mainline executor
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
        try:
            result = runner(runtime, phone_test=phone_test)
        except Exception as exc:
            tb = traceback.format_exc()
            err_result = {"status": "EXECUTION_EXCEPTION", "error": str(exc), "traceback": tb}
            out_path = project_path("output", "result.json")
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(err_result, f, ensure_ascii=False, indent=2)
            report = export_report(runtime, err_result, phone_test=phone_test)
            print(json.dumps({"status": "EXECUTION_EXCEPTION", "error": str(exc), "traceback": tb, "result": str(out_path), "report": str(report)}, ensure_ascii=False, indent=2))
            return 1
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
