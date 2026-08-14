"""Controlled S08 color-entry click and read-only color-area recognition.

This script may click exactly one page element: the explicit "颜色" entry in
the verified S08 model-config panel. After that it captures screenshot/XML,
recognizes the color selection area, locates the exact target color text, and
stops. It never clicks "白色" or any other color option, never clicks year,
confirm/view-result, sorting, vehicle cards, or collects vehicle source fields.
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
from runtime_s07_click_model_config import classify_after_model_config_click
from runtime_s08_read_model_config_panel import (
    COLOR_TOKENS,
    first_line,
    node_labels,
    recognize_panel_contract,
)


COLOR_OPTION_TOKENS = ("黑色", "白色", "银色", "红色", "蓝色", "灰色", "绿色", "棕色", "紫色", "香槟色", "黄色", "其它")


def valid_bounds(node: dict[str, object] | None) -> list[int] | None:
    if not node:
        return None
    bounds = node.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        return None
    left, top, right, bottom = bounds
    if right <= left or bottom <= top:
        return None
    return bounds


def find_color_entry_node(nodes: list[dict[str, object]]) -> dict[str, object] | None:
    """Find the panel's color entry, not a concrete color option."""

    candidates: list[dict[str, object]] = []
    for node in nodes:
        bounds = valid_bounds(node)
        if not bounds:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            if first_line(str(label)) in COLOR_TOKENS:
                candidates.append(node)
                break
    if not candidates:
        return None
    # Prefer the top row entry (the last verified contract had y ~= 880).
    return sorted(candidates, key=lambda item: (valid_bounds(item) or [9999, 9999, 9999, 9999])[1])[0]


def find_exact_color_node(nodes: list[dict[str, object]], target_color: str) -> dict[str, object] | None:
    for node in nodes:
        bounds = valid_bounds(node)
        if not bounds:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            if first_line(str(label)) == target_color:
                return node
    return None


def color_options_visible(nodes: list[dict[str, object]]) -> bool:
    labels = {first_line(label) for label in node_labels(nodes)}
    return bool(labels.intersection(COLOR_OPTION_TOKENS))


def classify_after_color_entry(snapshot: dict[str, object], params: dict[str, Any]) -> str:
    base = classify_after_model_config_click(snapshot, params)
    nodes = snapshot["nodes"]  # type: ignore[assignment]
    if base == "VEHICLE_DETAIL_PAGE":
        return base
    if str(snapshot.get("foreground_package") or "") != "com.ganji.android.haoche_c":
        return base
    if color_options_visible(nodes):
        return "S08_COLOR_SELECTION_PANEL"
    if base == "S08_VEHICLE_MODEL_CONFIG_PANEL":
        return "S08_VEHICLE_MODEL_CONFIG_PANEL"
    return base


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
        "current_page_is_s08_panel": False,
        "panel_task_markers_visible": {},
        "color_entry_visible": False,
        "color_entry_bounds": None,
        "actual_click_target": None,
        "actual_click_target_is_color_entry": False,
        "clicked_color_entry_once": False,
        "pre_click_artifacts": {},
        "post_click_artifacts": {},
        "after_page_result": None,
        "output_color_selection_panel": False,
        "target_color_found": False,
        "target_color_bounds": None,
        "clicked_target_color": False,
        "clicked_any_color_option": False,
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
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_VEHICLE_MODEL_CONFIG_PANEL", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_VEHICLE_MODEL_CONFIG_PANEL", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_VEHICLE_MODEL_CONFIG_PANEL", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

    client = AdbClient()
    gate = run_adb_device_gate(client, issues=issues, audit=audit, allow_transient_recovery=True)
    result["device_gate"] = gate
    result["adb_devices_l"] = gate.get("adb_devices_l_second") or gate.get("adb_devices_l_first")
    if not gate.get("passed"):
        result["adb_status"] = gate.get("status")
        issue_records = gate.get("issue_records") or []
        issue = issue_records[-1] if issue_records else issues.record("DEVICE_NOT_FOUND", "DEVICE", "Device gate failed before S08 color-entry click.", gate, "manual_intervention")
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
            "System overlay / keyguard / NotificationShade blocks S08 color-entry click.",
            {"runtime_state": state},
            "manual_unlock_required",
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks S08 color-entry click.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    result["foreground_app_is_guazi"] = state.get("foreground_package") == "com.ganji.android.haoche_c"
    if not result["foreground_app_is_guazi"]:
        issue = issues.record(
            "PAGE_CONTRACT_MISMATCH",
            "S08_VEHICLE_MODEL_CONFIG_PANEL",
            "Foreground APP is not verified Guazi before S08 color-entry click.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    before = capture(
        client,
        "s08_before_color_entry_click",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        "com.ganji.android.haoche_c",
    )
    before_page = classify_after_model_config_click(before, params)
    before_panel = recognize_panel_contract(before, params)
    result["pre_click_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}
    result["current_page_result"] = before_page
    result["current_page_is_s08_panel"] = before_page == "S08_VEHICLE_MODEL_CONFIG_PANEL"
    result["panel_task_markers_visible"] = before_panel["task_markers_visible"]
    color_entry = find_color_entry_node(before["nodes"])  # type: ignore[arg-type]
    if color_entry:
        result["color_entry_visible"] = True
        result["color_entry_bounds"] = valid_bounds(color_entry)
        result["actual_click_target"] = "颜色入口"
        result["actual_click_target_raw"] = "颜色"
        result["actual_click_target_is_color_entry"] = True

    if before_page != "S08_VEHICLE_MODEL_CONFIG_PANEL":
        issue = issues.classify_and_record(
            fallback_code="PAGE_CONTRACT_MISMATCH",
            state_id="S08_VEHICLE_MODEL_CONFIG_PANEL",
            message="Current page is not verified S08 model-config panel before color-entry click.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "page": before_page},
            current_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            intended_action="click_color_entry",
            expected_next_state="S08_COLOR_SELECTION_PANEL",
            actual_next_state=before_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("click_color_entry"),
            task_context=params,
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)
    if not all(before_panel["task_markers_visible"].values()):
        issue = issues.record(
            "PAGE_CONTRACT_MISMATCH",
            "S08_VEHICLE_MODEL_CONFIG_PANEL",
            "S08 panel is visible, but current target model-config markers are not all confirmed.",
            {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "task_markers_visible": before_panel["task_markers_visible"]},
            "manual_intervention",
        )
        return stop_with_issue(issue)
    if not color_entry or not result["color_entry_bounds"]:
        issue = issues.classify_and_record(
            fallback_code="COLOR_ENTRY_NOT_FOUND",
            state_id="S08_VEHICLE_MODEL_CONFIG_PANEL",
            message="S08 panel was verified, but explicit color entry was not found.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]},
            current_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            intended_action="click_color_entry",
            expected_next_state="S08_COLOR_SELECTION_PANEL",
            actual_next_state=before_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("click_color_entry"),
            task_context=params,
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    audit.log(
        "color_entry_click_requested",
        state="S08_VEHICLE_MODEL_CONFIG_PANEL",
        action_id="click_color_entry",
        actual_click_target="颜色入口",
        actual_click_target_raw="颜色",
        target_color=result["target_color"],
        bounds=result["color_entry_bounds"],
        clicked_target_color=False,
        clicked_any_color_option=False,
        clicked_year_or_age=False,
        clicked_confirm_or_view_result=False,
        collected_vehicle_source_fields=False,
    )
    result["audit_logged"] = True
    success = tap_node(client, color_entry)
    result["clicked_color_entry_once"] = bool(success)
    sleep_after(1.5)

    state = get_runtime_state(client)
    after = capture(
        client,
        "s08_after_color_entry_click",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        str(state.get("foreground_package") or ""),
    )
    after_page = classify_after_color_entry(after, params)
    result["post_click_artifacts"] = {"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]}
    result["after_page_result"] = after_page
    result["output_color_selection_panel"] = after_page == "S08_COLOR_SELECTION_PANEL"
    result["entered_vehicle_list"] = after_page in {"S07_VEHICLE_LIST_PAGE", "VEHICLE_LIST_OR_DETAIL", "VEHICLE_DETAIL_PAGE"}
    target_color_node = find_exact_color_node(after["nodes"], str(result["target_color"]))  # type: ignore[arg-type]
    if target_color_node:
        result["target_color_found"] = True
        result["target_color_bounds"] = valid_bounds(target_color_node)

    if after_page == "S08_COLOR_SELECTION_PANEL" and result["target_color_found"]:
        audit.log(
            "color_selection_panel_verified",
            from_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            to_state="S08_COLOR_SELECTION_PANEL",
            action_id="click_color_entry",
            clicked_color_entry_once=True,
            target_color=result["target_color"],
            target_color_found=True,
            target_color_bounds=result["target_color_bounds"],
            clicked_target_color=False,
            clicked_any_color_option=False,
            clicked_year_or_age=False,
            clicked_confirm_or_view_result=False,
            entered_vehicle_list=False,
            collected_vehicle_source_fields=False,
        )
        result["audit_logged"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if after_page == "S08_COLOR_SELECTION_PANEL" and not result["target_color_found"]:
        issue = issues.classify_and_record(
            fallback_code="TARGET_COLOR_NOT_FOUND",
            state_id="S08_COLOR_SELECTION_PANEL",
            message="Color selection area was verified, but exact target color was not found.",
            context={"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"], "target_color": result["target_color"]},
            current_state="S08_COLOR_SELECTION_PANEL",
            intended_action="read_color_selection_panel_contract",
            expected_next_state="S08_COLOR_SELECTION_PANEL",
            actual_next_state=after_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(after["xml_text"]),
            after_xml=str(after["xml_text"]),
            action_contract=configs["actions"]["actions"].get("read_color_selection_panel_contract"),
            task_context=params,
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    fallback = "WRONG_PAGE_AFTER_COLOR_ENTRY_CLICK" if result["entered_vehicle_list"] else "COLOR_ENTRY_CLICK_NO_PANEL"
    issue = issues.classify_and_record(
        fallback_code=fallback,
        state_id="S08_VEHICLE_MODEL_CONFIG_PANEL",
        message="Color entry click did not verify the expected color-selection panel.",
        context={"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"], "page": after_page},
        current_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
        intended_action="click_color_entry",
        expected_next_state="S08_COLOR_SELECTION_PANEL",
        actual_next_state=after_page,
        actual_clicked_target={"text": "颜色", "role": "color_entry", "bounds": result["color_entry_bounds"]},
        before_xml=str(before["xml_text"]),
        after_xml=str(after["xml_text"]),
        action_contract=configs["actions"]["actions"].get("click_color_entry"),
        task_context=params,
        resolution="manual_intervention",
    )
    return stop_with_issue(issue)


if __name__ == "__main__":
    raise SystemExit(main())
