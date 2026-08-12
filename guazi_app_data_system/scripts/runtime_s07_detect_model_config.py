"""Read-only S07 vehicle-list verification and model-config entry detection.

This script does not click the APP page. It verifies the device/window gate,
captures screenshot/XML, confirms S07_VEHICLE_LIST_PAGE, locates the explicit
车型配置 entry, then stops.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from guazi_app_data_system.adb_device_gate import run_adb_device_gate
from guazi_app_data_system.app_startup import AdbClient
from guazi_app_data_system.audit import AuditLogger
from guazi_app_data_system.config_loader import ensure_runtime_dirs, load_config, project_path
from guazi_app_data_system.exception_handler import IssueRecorder
from guazi_app_data_system.feishu_sync import validate_current_target_task
from guazi_app_data_system.issue_classifier import IssueClassifier
from guazi_app_data_system.learning_loop import LearningLoop
from runtime_recover_to_s04 import classify_page as classify_known_page
from runtime_recover_to_s04 import get_runtime_state, load_verified_target_app, sleep_after
from runtime_s04_to_s05 import capture
from runtime_s05_confirm import classify_after_confirm


def first_line(value: str | None) -> str:
    text = (value or "").strip()
    return text.splitlines()[0].strip() if text else ""


def find_vehicle_model_config_entry(nodes: list[dict[str, object]]) -> dict[str, object] | None:
    for node in nodes:
        bounds = node.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            if first_line(str(label)) == "车型配置":
                return {
                    "text": "车型配置",
                    "bounds": bounds,
                    "region": "vehicle_list_filter_bar",
                }
    return None


def is_launcher_or_systemui_state(state: dict[str, object]) -> bool:
    focus = str(state.get("focus_package") or "").lower()
    foreground = str(state.get("foreground_package") or "").lower()
    return (
        "launcher" in focus
        or "launcher" in foreground
        or focus in {"com.android.systemui", ""}
        or foreground in {"com.android.systemui", ""}
    )


def combined_page_result(snapshot: dict[str, object], target_brand: str, target_series: str, target_model_year: str, target_trim: str) -> str:
    after_confirm_page = classify_after_confirm(snapshot, target_model_year, target_trim)
    if after_confirm_page in {"S07_VEHICLE_LIST_PAGE", "S06_FILTER_OR_RESULT_ENTRY_PAGE", "VEHICLE_DETAIL_PAGE", "SystemUI", "Launcher"}:
        return after_confirm_page
    known_page = classify_known_page(snapshot, target_brand, target_series)
    if known_page in {"S03_BRAND_SELECT_PAGE_VERIFIED", "S04_SERIES_LIST_PAGE_VERIFIED", "S04_SERIES_LIST_PAGE_POSSIBLE", "HOME_PAGE_VERIFIED", "S02_SELECT_CAR_TAB_VERIFIED", "鎴戠殑"}:
        return known_page
    if after_confirm_page == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED":
        return after_confirm_page
    return after_confirm_page


def main() -> int:
    ensure_runtime_dirs()
    configs = {
        "system": load_config("system.yaml"),
        "pages": load_config("pages.yaml"),
        "actions": load_config("actions.yaml"),
        "exceptions": load_config("exceptions.yaml"),
    }
    system = configs["system"]
    audit = AuditLogger(project_path(system["paths"]["audit_log"]))
    learning = LearningLoop(ROOT, configs["exceptions"], configs["pages"], configs["actions"])
    classifier = IssueClassifier(configs["pages"], configs["actions"])
    issues = IssueRecorder(
        project_path(system["paths"]["issue_log"]),
        configs["exceptions"],
        learning_loop=learning,
        issue_classifier=classifier,
        audit=audit,
    )

    result: dict[str, object] = {
        "adb_status": None,
        "task_import_verified": False,
        "launched_verified_guazi_app": False,
        "foreground_app_is_guazi": False,
        "current_page_result": None,
        "s07_vehicle_list_page": False,
        "vehicle_model_config_entry_found": False,
        "vehicle_model_config_entry_bounds": None,
        "screenshot_path": None,
        "xml_path": None,
        "clicked_generic_filter": False,
        "clicked_vehicle_model_config": False,
        "clicked_color_or_year": False,
        "clicked_sort": False,
        "clicked_vehicle_card": False,
        "collected_vehicle_data": False,
        "modified_pricing_formula": False,
        "issue_logged": None,
        "audit_logged": False,
    }

    def stop_with_issue(issue: dict[str, object]) -> int:
        result["issue_logged"] = issue
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    check = validate_current_target_task()
    result["task_check"] = check
    result["task_import_verified"] = check.get("status") == "TASK_IMPORT_VERIFIED"
    params = check.get("app_operation_params") or {}
    result["target_brand"] = params.get("brand")
    result["target_series"] = params.get("series")
    result["target_model_year"] = params.get("model_year")
    result["target_trim"] = params.get("trim")
    result["target_color"] = params.get("color")
    result["target_vehicle_year"] = params.get("vehicle_year")
    if not result["task_import_verified"]:
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S07_VEHICLE_LIST_PAGE", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S07_VEHICLE_LIST_PAGE", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S07_VEHICLE_LIST_PAGE", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

    client = AdbClient()
    gate = run_adb_device_gate(client, issues=issues, audit=audit, allow_transient_recovery=True)
    result["device_gate"] = gate
    result["adb_devices_l"] = gate.get("adb_devices_l_second") or gate.get("adb_devices_l_first")
    if not gate.get("passed"):
        result["adb_status"] = gate.get("status")
        issue_records = gate.get("issue_records") or []
        issue = issue_records[-1] if issue_records else issues.record("DEVICE_NOT_FOUND", "DEVICE", "Device gate failed before S07 read-only verification.", gate, "manual_intervention")
        return stop_with_issue(issue)
    result["adb_status"] = "device"

    state = get_runtime_state(client)
    result["initial_runtime_state"] = state
    if state["screen_off"]:
        client.run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], timeout=20)
        result["auto_wake_executed"] = True
        sleep_after(1.5)
    else:
        result["auto_wake_executed"] = False
    client.run(["shell", "wm", "dismiss-keyguard"], timeout=20)
    result["dismiss_keyguard_executed"] = True
    sleep_after(1.0)
    state = get_runtime_state(client)
    result["post_recovery_runtime_state"] = state

    if state["mDreamingLockscreen"] or state["isKeyguardShowing"] or state["notification_shade_focused"]:
        snapshot = capture(client, "s07_model_config_gate_blocked", "", "", "", str(state.get("foreground_package") or ""))
        result["screenshot_path"] = snapshot["screenshot_path"]
        result["xml_path"] = snapshot["xml_path"]
        issue = issues.record(
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "System overlay / keyguard / NotificationShade blocks S07 read-only verification.",
            {"runtime_state": state, "screenshot_path": snapshot["screenshot_path"], "xml_path": snapshot["xml_path"]},
            "manual_unlock_required",
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        snapshot = capture(client, "s07_model_config_third_party_blocked", "", "", "", str(state.get("foreground_package") or ""))
        result["screenshot_path"] = snapshot["screenshot_path"]
        result["xml_path"] = snapshot["xml_path"]
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks S07 read-only verification.",
            {"runtime_state": state, "screenshot_path": snapshot["screenshot_path"], "xml_path": snapshot["xml_path"]},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    if state.get("foreground_package") != "com.ganji.android.haoche_c" and is_launcher_or_systemui_state(state):
        verified_app = load_verified_target_app(learning)
        result["verified_app"] = verified_app
        if (
            verified_app
            and verified_app.get("package_name") == "com.ganji.android.haoche_c"
            and verified_app.get("launch_activity") == "com.ganji.android.haoche_c/com.cars.guazi.app.home.MainActivity"
            and verified_app.get("excluded_confirmed") is True
        ):
            launch = client.launch_activity_component(str(verified_app["launch_activity"]), wait_seconds=5)
            result["launched_verified_guazi_app"] = bool(launch.success)
            result["launch_result"] = {"success": launch.success, "returncode": launch.returncode, "stdout": launch.stdout, "stderr": launch.stderr}
            audit.log(
                "verified_guazi_app_launched_for_s07_read_only_probe",
                launch_activity=verified_app["launch_activity"],
                clicked_page=False,
                collected_vehicle_data=False,
            )
            sleep_after(2.0)
            state = get_runtime_state(client)
            result["post_launch_runtime_state"] = state
        else:
            issue = issues.record(
                "APP_IDENTITY_NOT_FOUND",
                "DEVICE",
                "Verified target app record is missing or inconsistent before S07 read-only probe launch.",
                {"verified_app": verified_app, "runtime_state": state},
                "manual_intervention",
            )
            return stop_with_issue(issue)

    result["foreground_app_is_guazi"] = state.get("foreground_package") == "com.ganji.android.haoche_c"
    snapshot = capture(
        client,
        "s07_detect_vehicle_model_config",
        str(result.get("target_series") or ""),
        str(result.get("target_model_year") or ""),
        str(result.get("target_trim") or ""),
        str(state.get("foreground_package") or ""),
    )
    page = combined_page_result(
        snapshot,
        str(result.get("target_brand") or ""),
        str(result.get("target_series") or ""),
        str(result.get("target_model_year") or ""),
        str(result.get("target_trim") or ""),
    )
    result["current_page_result"] = page
    result["screenshot_path"] = snapshot["screenshot_path"]
    result["xml_path"] = snapshot["xml_path"]
    result["s07_vehicle_list_page"] = page == "S07_VEHICLE_LIST_PAGE"
    entry = find_vehicle_model_config_entry(snapshot["nodes"])  # type: ignore[arg-type]
    if entry:
        result["vehicle_model_config_entry_found"] = True
        result["vehicle_model_config_entry_bounds"] = entry["bounds"]
        result["vehicle_model_config_entry_region"] = entry["region"]

    known_non_s07_pages = {
        "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
        "S06_FILTER_OR_RESULT_ENTRY_PAGE",
        "S03_BRAND_SELECT_PAGE_VERIFIED",
        "S04_SERIES_LIST_PAGE_VERIFIED",
        "S04_SERIES_LIST_PAGE_POSSIBLE",
        "HOME_PAGE_VERIFIED",
        "S02_SELECT_CAR_TAB_VERIFIED",
        "鎴戠殑",
        "Launcher",
        "SystemUI",
    }
    if not result["s07_vehicle_list_page"] and page in known_non_s07_pages:
        audit.log(
            "s07_read_only_probe_stopped_on_known_non_s07_page",
            page=page,
            screenshot_path=snapshot["screenshot_path"],
            xml_path=snapshot["xml_path"],
            clicked_any_page=False,
            collected_vehicle_data=False,
        )
        result["audit_logged"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not result["s07_vehicle_list_page"]:
        issue = issues.classify_and_record(
            fallback_code="PAGE_CONTRACT_MISMATCH",
            state_id="S07_VEHICLE_LIST_PAGE",
            message="Current page is not verified S07 vehicle list for read-only model-config entry detection.",
            context={"screenshot_path": snapshot["screenshot_path"], "xml_path": snapshot["xml_path"], "page": page},
            current_state="S07_VEHICLE_LIST_PAGE",
            intended_action="detect_vehicle_model_config_entry",
            expected_next_state="S07_VEHICLE_LIST_PAGE",
            actual_next_state=page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(snapshot["xml_text"]),
            after_xml=str(snapshot["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("detect_vehicle_model_config_entry"),
            task_context={},
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    if not entry:
        issue = issues.record(
            "VEHICLE_MODEL_CONFIG_ENTRY_NOT_FOUND",
            "S07_VEHICLE_LIST_PAGE",
            "S07 was recognized, but the explicit 车型配置 entry was not found.",
            {"screenshot_path": snapshot["screenshot_path"], "xml_path": snapshot["xml_path"]},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    audit.log(
        "vehicle_model_config_entry_detected",
        state="S07_VEHICLE_LIST_PAGE",
        action_id="detect_vehicle_model_config_entry",
        entry_text="车型配置",
        bounds=entry["bounds"],
        region=entry["region"],
        clicked_generic_filter=False,
        clicked_vehicle_model_config=False,
        clicked_color_or_year=False,
        clicked_sort=False,
        clicked_vehicle_card=False,
        collected_vehicle_data=False,
    )
    result["audit_logged"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
