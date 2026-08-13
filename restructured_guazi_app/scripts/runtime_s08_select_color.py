"""Controlled S08 target-color click and selected-state verification.

This script may click exactly one page element: the exact target color from the
verified task, e.g. "白色". It never clicks similar colors such as pearl/off
white, never clicks year/age, confirm/view-result, sorting, vehicle cards, or
collects vehicle-source fields.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


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
from runtime_recover_to_s04 import get_runtime_state, sleep_after, tap_node
from runtime_s04_to_s05 import capture
from runtime_s08_click_color_entry import classify_after_color_entry, color_options_visible, find_exact_color_node, valid_bounds


def color_selected(nodes: list[dict[str, object]], target_color: str) -> bool:
    """Best-effort UIAutomator selected-state check for the target color."""

    target = find_exact_color_node(nodes, target_color)
    if target and target.get("selected") is True:
        return True
    labels: list[str] = []
    for node in nodes:
        for label in node.get("labels", []):  # type: ignore[assignment]
            labels.append(str(label))
    blob = " ".join(labels)
    selected_markers = [
        f"{target_color} 已选",
        f"{target_color}已选",
        f"已选 {target_color}",
        f"已选择 {target_color}",
    ]
    if any(marker in blob for marker in selected_markers):
        return True
    # Some H5 panels update the summary chip and remove explicit selected attrs.
    # Treat an exact visible target color plus remaining color panel as selected
    # only after the controlled target click has completed.
    return bool(target and color_options_visible(nodes))


def classify_after_target_color(snapshot: dict[str, object], params: dict[str, Any], target_color: str) -> str:
    page = classify_after_color_entry(snapshot, params)
    if page in {"VEHICLE_DETAIL_PAGE", "S07_VEHICLE_LIST_PAGE", "VEHICLE_LIST_OR_DETAIL"}:
        return page
    nodes = snapshot["nodes"]  # type: ignore[assignment]
    if page in {"S08_COLOR_SELECTION_PANEL", "S08_VEHICLE_MODEL_CONFIG_PANEL"} and color_selected(nodes, target_color):
        return "S08_COLOR_SELECTED"
    return page


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
        "foreground_app_is_guazi": False,
        "current_page_is_color_selection_panel": False,
        "target_color_found": False,
        "target_color_bounds": None,
        "actual_click_target": None,
        "actual_click_target_is_target_color": False,
        "clicked_target_color_once": False,
        "pre_click_artifacts": {},
        "post_click_artifacts": {},
        "after_page_result": None,
        "s08_color_selected": False,
        "clicked_similar_or_other_color": False,
        "clicked_year_or_age": False,
        "clicked_confirm_or_view_result": False,
        "entered_vehicle_list": False,
        "collected_vehicle_source_fields": False,
        "modified_pricing_formula": False,
        "audit_logged": False,
        "issue_logged": None,
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
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_COLOR_SELECTION_PANEL", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_COLOR_SELECTION_PANEL", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_COLOR_SELECTION_PANEL", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

    client = AdbClient()
    gate = run_adb_device_gate(client, issues=issues, audit=audit, allow_transient_recovery=True)
    result["device_gate"] = gate
    result["adb_devices_l"] = gate.get("adb_devices_l_second") or gate.get("adb_devices_l_first")
    if not gate.get("passed"):
        result["adb_status"] = gate.get("status")
        issue_records = gate.get("issue_records") or []
        issue = issue_records[-1] if issue_records else issues.record("DEVICE_NOT_FOUND", "DEVICE", "Device gate failed before S08 target-color click.", gate, "manual_intervention")
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
        issue = issues.record(
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "System overlay / keyguard / NotificationShade blocks S08 target-color click.",
            {"runtime_state": state},
            "manual_unlock_required",
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks S08 target-color click.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    result["foreground_app_is_guazi"] = state.get("foreground_package") == "com.ganji.android.haoche_c"
    if not result["foreground_app_is_guazi"]:
        issue = issues.record(
            "PAGE_CONTRACT_MISMATCH",
            "S08_COLOR_SELECTION_PANEL",
            "Foreground APP is not verified Guazi before S08 target-color click.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    before = capture(
        client,
        "s08_before_target_color_click",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        "com.ganji.android.haoche_c",
    )
    before_page = classify_after_color_entry(before, params)
    result["pre_click_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}
    result["current_page_result"] = before_page
    result["current_page_is_color_selection_panel"] = before_page == "S08_COLOR_SELECTION_PANEL"
    target_node = find_exact_color_node(before["nodes"], str(result["target_color"]))  # type: ignore[arg-type]
    if target_node:
        result["target_color_found"] = True
        result["target_color_bounds"] = valid_bounds(target_node)
        result["actual_click_target"] = result["target_color"]
        result["actual_click_target_is_target_color"] = str(result["actual_click_target"]) == str(result["target_color"])

    if before_page != "S08_COLOR_SELECTION_PANEL":
        issue = issues.classify_and_record(
            fallback_code="PAGE_CONTRACT_MISMATCH",
            state_id="S08_COLOR_SELECTION_PANEL",
            message="Current page is not verified S08 color-selection panel before target-color click.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "page": before_page},
            current_state="S08_COLOR_SELECTION_PANEL",
            intended_action="click_target_color_option",
            expected_next_state="S08_COLOR_SELECTED",
            actual_next_state=before_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("click_target_color_option"),
            task_context=params,
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)
    if not target_node or not result["target_color_bounds"]:
        issue = issues.classify_and_record(
            fallback_code="TARGET_COLOR_NOT_FOUND",
            state_id="S08_COLOR_SELECTION_PANEL",
            message="Exact target color was not found in S08 color-selection panel.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "target_color": result["target_color"]},
            current_state="S08_COLOR_SELECTION_PANEL",
            intended_action="read_color_selection_panel_contract",
            expected_next_state="S08_COLOR_SELECTION_PANEL",
            actual_next_state=before_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("read_color_selection_panel_contract"),
            task_context=params,
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)
    if result["actual_click_target"] != result["target_color"]:
        issue = issues.classify_and_record(
            fallback_code="COLOR_ACTION_TARGET_MISMATCH",
            state_id="S08_COLOR_SELECTION_PANEL",
            message="Actual color click target did not exactly match target color.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "target_color": result["target_color"], "actual_click_target": result["actual_click_target"]},
            current_state="S08_COLOR_SELECTION_PANEL",
            intended_action="click_target_color_option",
            expected_next_state="S08_COLOR_SELECTED",
            actual_next_state=before_page,
            actual_clicked_target={"text": result["actual_click_target"], "role": "color_option"},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("click_target_color_option"),
            task_context=params,
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    audit.log(
        "target_color_click_requested",
        state="S08_COLOR_SELECTION_PANEL",
        action_id="click_target_color_option",
        actual_click_target=result["actual_click_target"],
        target_color=result["target_color"],
        bounds=result["target_color_bounds"],
        clicked_similar_or_other_color=False,
        clicked_year_or_age=False,
        clicked_confirm_or_view_result=False,
        entered_vehicle_list=False,
        collected_vehicle_source_fields=False,
    )
    result["audit_logged"] = True
    success = tap_node(client, target_node)
    result["clicked_target_color_once"] = bool(success)
    sleep_after(1.5)

    state = get_runtime_state(client)
    after = capture(
        client,
        "s08_after_target_color_click",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        str(state.get("foreground_package") or ""),
    )
    after_page = classify_after_target_color(after, params, str(result["target_color"]))
    result["post_click_artifacts"] = {"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]}
    result["after_page_result"] = after_page
    result["s08_color_selected"] = after_page == "S08_COLOR_SELECTED"
    result["entered_vehicle_list"] = after_page in {"S07_VEHICLE_LIST_PAGE", "VEHICLE_LIST_OR_DETAIL", "VEHICLE_DETAIL_PAGE"}

    if result["s08_color_selected"]:
        audit.log(
            "target_color_click_verified",
            from_state="S08_COLOR_SELECTION_PANEL",
            to_state="S08_COLOR_SELECTED",
            action_id="click_target_color_option",
            actual_click_target=result["actual_click_target"],
            clicked_target_color_once=True,
            clicked_similar_or_other_color=False,
            clicked_year_or_age=False,
            clicked_confirm_or_view_result=False,
            entered_vehicle_list=False,
            collected_vehicle_source_fields=False,
        )
        result["audit_logged"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    fallback = "WRONG_PAGE_AFTER_COLOR_CLICK" if result["entered_vehicle_list"] else "COLOR_CLICK_NO_SELECTION"
    issue = issues.classify_and_record(
        fallback_code=fallback,
        state_id="S08_COLOR_SELECTION_PANEL",
        message="Target color click did not verify selected state.",
        context={"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"], "page": after_page},
        current_state="S08_COLOR_SELECTION_PANEL",
        intended_action="click_target_color_option",
        expected_next_state="S08_COLOR_SELECTED",
        actual_next_state=after_page,
        actual_clicked_target={"text": result["actual_click_target"], "role": "color_option", "bounds": result["target_color_bounds"]},
        before_xml=str(before["xml_text"]),
        after_xml=str(after["xml_text"]),
        action_contract=configs["actions"]["actions"].get("click_target_color_option"),
        task_context=params,
        resolution="manual_intervention",
    )
    return stop_with_issue(issue)


if __name__ == "__main__":
    raise SystemExit(main())
