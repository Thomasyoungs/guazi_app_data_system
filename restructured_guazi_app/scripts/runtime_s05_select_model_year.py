"""Controlled S05 model-year selection.

This script verifies the target task and S05 page contract, clicks only the
exact target model year once, then stops after verifying the selected-year
state. It never clicks trim/configuration, confirm, or vehicle-list content.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from guazi_app_data_system.app_startup import AdbClient
from guazi_app_data_system.audit import AuditLogger
from guazi_app_data_system.config_loader import ensure_runtime_dirs, load_config, project_path
from guazi_app_data_system.exception_handler import IssueRecorder
from guazi_app_data_system.feishu_sync import validate_current_target_task
from guazi_app_data_system.issue_classifier import IssueClassifier
from guazi_app_data_system.learning_loop import LearningLoop
from runtime_recover_to_s04 import adb_device_state, find_popup_close, get_runtime_state, sleep_after, tap_node
from runtime_s04_to_s05 import (
    capture,
    find_target_series_model_button,
    looks_like_vehicle_list_or_detail,
)


def first_line(value: str | None) -> str:
    text = (value or "").strip()
    return text.splitlines()[0].strip() if text else ""


def node_labels(nodes: list[dict[str, object]]) -> list[str]:
    labels: list[str] = []
    for node in nodes:
        for label in node.get("labels", []):  # type: ignore[assignment]
            text = str(label)
            if text and text not in labels:
                labels.append(text)
    return labels


def find_model_year_node(nodes: list[dict[str, object]], target_model_year: str) -> dict[str, object] | None:
    """Find only the exact year item in the left model-year list."""
    for node in nodes:
        bounds = node.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            continue
        # The APP presents model years in the left column; avoid right-side trim rows.
        if int(bounds[2]) > 360:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            if first_line(str(label)) == target_model_year:
                return node
    return None


def year_list_visible(nodes: list[dict[str, object]]) -> bool:
    count = 0
    for node in nodes:
        bounds = node.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4 or int(bounds[2]) > 360:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            if re.fullmatch(r"20\d{2}款", first_line(str(label))):
                count += 1
    return count >= 2


def configuration_list_visible(nodes: list[dict[str, object]], target_model_year: str, target_trim: str) -> bool:
    for node in nodes:
        bounds = node.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4 or int(bounds[0]) < 260:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            text = str(label)
            if target_trim and target_trim in text:
                return True
            if target_model_year and target_model_year in text:
                return True
            if "全部车型" in text or "配置" in text or "车型" in text:
                return True
    return False


def target_model_year_selected(nodes: list[dict[str, object]], target_model_year: str, target_trim: str) -> bool:
    year_node = find_model_year_node(nodes, target_model_year)
    if year_node and year_node.get("selected") is True:
        return True
    return configuration_list_visible(nodes, target_model_year, target_trim)


def load_verified_target_app(learning: LearningLoop) -> dict[str, object] | None:
    for solution in learning.load_solutions():
        if solution.get("issue_code") == "TARGET_APP_VERIFIED" and solution.get("approved") is True:
            app = solution.get("verified_target_app") or {}
            excluded = app.get("excluded_packages") or []
            return {
                "package_name": app.get("package_name"),
                "app_label": app.get("app_label"),
                "launch_activity": app.get("launch_activity"),
                "excluded_confirmed": any(
                    item.get("package_name") == "com.guazi.android.chesupai" and item.get("excluded") is True
                    for item in excluded
                ),
            }
    return None


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
        "task_import_verified": False,
        "adb_status": None,
        "initial_page_result": None,
        "current_page_is_s05": False,
        "year_list_visible": False,
        "target_model_year_seen": False,
        "target_model_year_bounds": None,
        "clicked_target_model_year_once": False,
        "pre_model_year_click_artifacts": {},
        "post_model_year_click_artifacts": {},
        "after_page_result": None,
        "s05_model_year_selected": False,
        "target_trim_visible": False,
        "configuration_list_visible": False,
        "clicked_other_model_year": False,
        "clicked_target_trim": False,
        "clicked_confirm": False,
        "entered_vehicle_list": False,
        "collected_vehicle_data": False,
        "modified_pricing_formula": False,
        "audit_logged": False,
        "issue_logged": None,
        "recovery_path": [],
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
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S05", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S05", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S05", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

    verified_app = load_verified_target_app(learning)
    result["verified_app"] = verified_app
    if (
        not verified_app
        or verified_app.get("package_name") != "com.ganji.android.haoche_c"
        or verified_app.get("app_label") != "瓜子二手车"
        or verified_app.get("launch_activity") != "com.ganji.android.haoche_c/com.cars.guazi.app.home.MainActivity"
        or not verified_app.get("excluded_confirmed")
    ):
        return stop_with_issue(issues.record("APP_IDENTITY_NOT_FOUND", "DEVICE", "Verified target app record is missing or inconsistent.", {"verified_app": verified_app}, "manual_intervention"))

    client = AdbClient()
    if not client.available:
        return stop_with_issue(issues.record("ADB_NOT_FOUND", "DEVICE", "ADB executable is not available in PATH, SDK locations, or project fallback.", {"adb_path": str(client.adb_path or "")}, "local_simulation_only"))

    adb_stdout, device_entries = adb_device_state(client)
    result["adb_devices_l"] = adb_stdout
    ready = [entry for entry in device_entries if entry.get("status") == "device"]
    if not ready:
        if not device_entries:
            issue = issues.record("DEVICE_NOT_FOUND", "DEVICE", "adb devices -l returned no attached device.", {"devices": device_entries}, "manual_intervention")
        elif any(entry.get("status") == "unauthorized" for entry in device_entries):
            issue = issues.record("ADB_UNAUTHORIZED", "DEVICE", "ADB device is unauthorized.", {"devices": device_entries}, "wait_for_phone_rsa_authorization")
        elif any(entry.get("status") == "offline" for entry in device_entries):
            issue = issues.record("DEVICE_OFFLINE", "DEVICE", "ADB device is offline.", {"devices": device_entries}, "manual_intervention")
        else:
            issue = issues.record("DEVICE_NOT_FOUND", "DEVICE", "No ready adb device was found.", {"devices": device_entries}, "manual_intervention")
        result["adb_status"] = device_entries[0]["status"] if device_entries else None
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
    sleep_after(1.5)
    state = get_runtime_state(client)
    result["post_recovery_runtime_state"] = state

    if state["mDreamingLockscreen"] or state["isKeyguardShowing"] or state["notification_shade_focused"]:
        snap = capture(client, "s05_gate_blocked", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
        result["pre_model_year_click_artifacts"] = {"screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"]}
        issue = issues.record(
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "System overlay / keyguard / NotificationShade blocks S05 gate verification.",
            {"runtime_state": state, "screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"], "page": snap["page"]},
            "manual_unlock_required",
            recognized_text=" ".join(snap["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        snap = capture(client, "s05_third_party_overlay_blocked", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
        result["pre_model_year_click_artifacts"] = {"screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"]}
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks the S05 gate verification.",
            {"runtime_state": state, "screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"], "page": snap["page"]},
            "manual_intervention",
            recognized_text=" ".join(snap["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)

    if state.get("foreground_package") != "com.ganji.android.haoche_c":
        launch = client.launch_activity_component("com.ganji.android.haoche_c/com.cars.guazi.app.home.MainActivity", wait_seconds=5)
        result["launched_verified_guazi_app"] = bool(launch.success)
        result["recovery_path"].append("launch_verified_guazi_app")
        state = get_runtime_state(client)

    before = capture(client, "s05_before_model_year_click", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
    result["initial_page_result"] = before["page"]
    result["current_page_is_s05"] = before["page"] == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"

    # If the APP fell back one verified layer to S04, restore to S05 by clicking
    # only the already verified target-series right-side "车型" button.
    if before["page"] == "S04_SERIES_LIST_PAGE_VERIFIED":
        model_button = find_target_series_model_button(str(before["xml_text"]), str(result["target_series"]))
        if model_button and tap_node(client, model_button):
            audit.log("recover_s04_to_s05_model_button_clicked", state="S04", target_series=result["target_series"], bounds=model_button["bounds"])
            result["audit_logged"] = True
            result["recovery_path"].append("S04->click_series_model_button->S05")
            sleep_after(3.0)
            state = get_runtime_state(client)
            before = capture(client, "s05_after_recover_from_s04", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
            result["initial_page_result"] = before["page"]
            result["current_page_is_s05"] = before["page"] == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"

    result["pre_model_year_click_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}

    if before["page"] == "POPUP_MARKETING_OVERLAY":
        close_node = find_popup_close(before["nodes"])  # type: ignore[arg-type]
        if close_node and tap_node(client, close_node):
            audit.log("popup_marketing_overlay_close_clicked", bounds=close_node["bounds"], from_page=before["page"])
            result["audit_logged"] = True
            sleep_after(2.5)
            state = get_runtime_state(client)
            before = capture(client, "s05_after_popup_close", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
            result["initial_page_result"] = before["page"]
            result["current_page_is_s05"] = before["page"] == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"
            result["pre_model_year_click_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}
        else:
            return stop_with_issue(issues.record("POPUP_MARKETING_OVERLAY", "S05", "Marketing popup detected but explicit close button was not safely clickable.", {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}, "manual_intervention", recognized_text=" ".join(before["labels"])))  # type: ignore[arg-type]
    elif before["page"] == "POPUP_UNCONTRACTED":
        return stop_with_issue(issues.record("POPUP_UNCONTRACTED", "S05", "Blocking non-marketing popup detected before model-year click.", {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "page": before["page"]}, "manual_intervention", recognized_text=" ".join(before["labels"])))  # type: ignore[arg-type]

    if before["page"] != "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED":
        issue = issues.record(
            "PAGE_CONTRACT_MISMATCH",
            "S05",
            "Current page is not the verified S05 model/year/trim page.",
            {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "page": before["page"], "recovery_path": result["recovery_path"]},
            "manual_intervention",
            recognized_text=" ".join(before["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)
    if looks_like_vehicle_list_or_detail(before["labels"]):  # type: ignore[arg-type]
        return stop_with_issue(issues.record("PAGE_CONTRACT_MISMATCH", "S05", "S05 gate blocked because vehicle-list/detail markers are already visible.", {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}, "manual_intervention", recognized_text=" ".join(before["labels"])))  # type: ignore[arg-type]

    year_node = find_model_year_node(before["nodes"], str(result["target_model_year"]))  # type: ignore[arg-type]
    result["year_list_visible"] = year_list_visible(before["nodes"])  # type: ignore[arg-type]
    result["target_model_year_seen"] = bool(year_node)
    result["configuration_list_visible_before"] = configuration_list_visible(before["nodes"], str(result["target_model_year"]), str(result["target_trim"]))  # type: ignore[arg-type]
    if year_node:
        result["target_model_year_bounds"] = year_node.get("bounds")

    if not year_node or not year_node.get("bounds"):
        return stop_with_issue(
            issues.record(
                "MODEL_YEAR_NOT_FOUND",
                "S05",
                "Target model year was not found on verified S05.",
                {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "target_model_year": result["target_model_year"]},
                "manual_intervention",
                recognized_text=" ".join(before["labels"]),  # type: ignore[arg-type]
            )
        )

    audit.log(
        "model_year_click_requested",
        state="S05",
        action_id="tap_target_year",
        actual_click_target=result["target_model_year"],
        bounds=year_node["bounds"],
        forbidden_clicks_performed=[],
    )
    result["audit_logged"] = True
    success = tap_node(client, year_node)
    result["clicked_target_model_year_once"] = bool(success)
    sleep_after(2.5)

    state = get_runtime_state(client)
    after = capture(client, "s05_after_model_year_click", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
    result["post_model_year_click_artifacts"] = {"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]}
    result["after_page_result"] = after["page"]
    result["entered_vehicle_list"] = after["page"] == "VEHICLE_LIST_OR_DETAIL" or looks_like_vehicle_list_or_detail(after["labels"])  # type: ignore[arg-type]
    labels = node_labels(after["nodes"])  # type: ignore[arg-type]
    result["target_trim_visible"] = any(str(result["target_trim"]) in label for label in labels)
    result["configuration_list_visible"] = configuration_list_visible(after["nodes"], str(result["target_model_year"]), str(result["target_trim"]))  # type: ignore[arg-type]
    result["s05_model_year_selected"] = (
        after["page"] == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"
        and not result["entered_vehicle_list"]
        and target_model_year_selected(after["nodes"], str(result["target_model_year"]), str(result["target_trim"]))  # type: ignore[arg-type]
    )

    if result["entered_vehicle_list"]:
        return stop_with_issue(
            issues.classify_and_record(
                fallback_code="WRONG_PAGE_AFTER_MODEL_YEAR_CLICK",
                state_id="S05",
                message="Target model-year click reached vehicle list/detail markers.",
                context={"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"], "page": after["page"]},
                current_state="S05",
                intended_action="tap_target_year",
                expected_next_state="S05_MODEL_YEAR_SELECTED",
                actual_next_state="VEHICLE_LIST_OR_DETAIL",
                actual_clicked_target={"text": result["target_model_year"], "role": "model_year", "bounds": year_node["bounds"]},
                before_xml=str(before["xml_text"]),
                after_xml=str(after["xml_text"]),
                page_contract=None,
                action_contract=configs["actions"]["actions"].get("tap_target_year"),
                task_context={"model_year": result["target_model_year"]},
                resolution="manual_intervention",
                recognized_text=" ".join(after["labels"]),  # type: ignore[arg-type]
            )
        )

    if result["s05_model_year_selected"]:
        audit.log(
            "model_year_click_verified",
            from_state="S05",
            to_state="S05_MODEL_YEAR_SELECTED",
            action_id="tap_target_year",
            actual_click_target=result["target_model_year"],
            target_trim_visible=result["target_trim_visible"],
            configuration_list_visible=result["configuration_list_visible"],
            clicked_target_trim=False,
            clicked_confirm=False,
            entered_vehicle_list=False,
            collected_vehicle_data=False,
        )
        result["audit_logged"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    return stop_with_issue(
        issues.classify_and_record(
            fallback_code="MODEL_YEAR_CLICK_NO_SELECTION",
            state_id="S05",
            message="Target model-year click did not verify selected state or configuration-list refresh.",
            context={
                "screenshot_path": after["screenshot_path"],
                "xml_path": after["xml_path"],
                "page": after["page"],
                "target_model_year": result["target_model_year"],
            },
            current_state="S05",
            intended_action="tap_target_year",
            expected_next_state="S05_MODEL_YEAR_SELECTED",
            actual_next_state=str(after["page"]),
            actual_clicked_target={"text": result["target_model_year"], "role": "model_year", "bounds": year_node["bounds"]},
            before_xml=str(before["xml_text"]),
            after_xml=str(after["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("tap_target_year"),
            task_context={"model_year": result["target_model_year"]},
            resolution="manual_intervention",
            recognized_text=" ".join(after["labels"]),  # type: ignore[arg-type]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
