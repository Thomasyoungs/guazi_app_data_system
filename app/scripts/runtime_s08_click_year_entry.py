"""Controlled S08 year/age-entry click and left-age-tab recognition.

This script may click exactly one page element: the explicit year/age entry in
the verified S08 color-selected model-config panel. After that it captures
screenshot/XML, recognizes S08_YEAR_SELECTION_PANEL, detects only the left-side
车龄 tab, and stops. It never scans ordinary age options, never clicks
不限车龄, never drags the slider, never clicks confirm/view-result, sorting,
vehicle cards, or collects vehicle-source fields.
"""

from __future__ import annotations

import json
import re
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
from runtime_s08_click_color_entry import valid_bounds
from runtime_s08_read_model_config_panel import YEAR_TOKENS, first_line
from runtime_s08_select_color import classify_after_target_color


EXCLUDED_OPTION_TEXTS = {
    "颜色",
    "白色",
    "车型配置",
    "确定",
    "查看结果",
    "查看",
    "重置",
    "关闭",
    "大众",
    "帕萨特",
    "2020款",
    "330TSI 尊贵版 国VI",
}
YEAR_ENTRY_LABELS = {"年份", "车龄", "上牌年份", "上牌时间", "年款"}
LEFT_AGE_TAB_LABEL = "车龄"


def find_year_entry_node(nodes: list[dict[str, object]]) -> dict[str, object] | None:
    candidates: list[dict[str, object]] = []
    for node in nodes:
        bounds = valid_bounds(node)
        if not bounds:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            line = first_line(str(label))
            if line in YEAR_TOKENS:
                candidates.append(node)
                break
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: ((valid_bounds(item) or [9999, 9999, 9999, 9999])[1], (valid_bounds(item) or [9999, 9999, 9999, 9999])[0]))[0]


def find_left_age_tab_node(nodes: list[dict[str, object]]) -> dict[str, object] | None:
    candidates: list[dict[str, object]] = []
    for node in nodes:
        bounds = valid_bounds(node)
        if not bounds:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            text = first_line(str(label))
            if text == LEFT_AGE_TAB_LABEL:
                candidates.append(node)
                break
    if not candidates:
        return None
    candidates.sort(key=lambda item: ((valid_bounds(item) or [9999, 9999, 9999, 9999])[1], (valid_bounds(item) or [9999, 9999, 9999, 9999])[0]))
    return candidates[0]


def classify_after_year_entry(snapshot: dict[str, object], params: dict[str, Any], target_color: str) -> str:
    page = classify_after_target_color(snapshot, params, target_color)
    if page in {"VEHICLE_DETAIL_PAGE", "S07_VEHICLE_LIST_PAGE", "VEHICLE_LIST_OR_DETAIL"}:
        return page
    nodes = snapshot["nodes"]  # type: ignore[assignment]
    if str(snapshot.get("foreground_package") or "") != "com.ganji.android.haoche_c":
        return page
    if find_left_age_tab_node(nodes):
        return "S08_YEAR_SELECTION_PANEL"
    if page == "S08_COLOR_SELECTED":
        return "S08_COLOR_SELECTED"
    base = classify_after_model_config_click(snapshot, params)
    if base == "S08_VEHICLE_MODEL_CONFIG_PANEL":
        return "S08_VEHICLE_MODEL_CONFIG_PANEL"
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
        "current_page_is_color_selected": False,
        "color_selected": False,
        "year_entry_visible": False,
        "year_entry_bounds": None,
        "actual_click_target": None,
        "actual_click_target_is_year_entry": False,
        "clicked_year_entry_once": False,
        "pre_click_artifacts": {},
        "post_click_artifacts": {},
        "after_page_result": None,
        "s08_year_selection_panel": False,
        "left_age_tab_visible": False,
        "left_age_tab_bounds": None,
        "clicked_year_option": False,
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
    result["registration_date_raw"] = check.get("registration_date_raw") or check.get("task", {}).get("registration_date_raw")
    if not result["task_import_verified"]:
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_COLOR_SELECTED", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_COLOR_SELECTED", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_COLOR_SELECTED", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

    client = AdbClient()
    gate = run_adb_device_gate(client, issues=issues, audit=audit, allow_transient_recovery=True)
    result["device_gate"] = gate
    result["adb_devices_l"] = gate.get("adb_devices_l_second") or gate.get("adb_devices_l_first")
    result["transient_recovery_attempted"] = gate.get("transient_recovery_attempted", False)
    if not gate.get("passed"):
        result["adb_status"] = gate.get("status")
        issue_records = gate.get("issue_records") or []
        issue = issue_records[-1] if issue_records else issues.record("DEVICE_NOT_FOUND", "DEVICE", "Device gate failed before S08 year-entry click.", gate, "manual_intervention")
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
            "System overlay / keyguard / NotificationShade blocks S08 year-entry click.",
            {"runtime_state": state},
            "manual_unlock_required",
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks S08 year-entry click.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    result["foreground_app_is_guazi"] = state.get("foreground_package") == "com.ganji.android.haoche_c"
    if not result["foreground_app_is_guazi"]:
        issue = issues.record(
            "PAGE_CONTRACT_MISMATCH",
            "S08_COLOR_SELECTED",
            "Foreground APP is not verified Guazi before S08 year-entry click.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    before = capture(
        client,
        "s08_before_year_entry_click",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        "com.ganji.android.haoche_c",
    )
    before_page = classify_after_target_color(before, params, str(result["target_color"]))
    result["pre_click_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}
    result["current_page_result"] = before_page
    result["current_page_is_color_selected"] = before_page == "S08_COLOR_SELECTED"
    result["color_selected"] = before_page == "S08_COLOR_SELECTED"
    year_entry = find_year_entry_node(before["nodes"])  # type: ignore[arg-type]
    if year_entry:
        result["year_entry_visible"] = True
        result["year_entry_bounds"] = valid_bounds(year_entry)
        result["actual_click_target"] = first_line(str((year_entry.get("labels") or [""])[0]))
        result["actual_click_target_is_year_entry"] = result["actual_click_target"] in YEAR_ENTRY_LABELS

    if before_page != "S08_COLOR_SELECTED":
        issue = issues.classify_and_record(
            fallback_code="PAGE_CONTRACT_MISMATCH",
            state_id="S08_COLOR_SELECTED",
            message="Current page is not verified S08 color-selected state before year-entry click.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "page": before_page},
            current_state="S08_COLOR_SELECTED",
            intended_action="click_year_or_age_entry",
            expected_next_state="S08_YEAR_SELECTION_PANEL",
            actual_next_state=before_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("click_year_or_age_entry"),
            task_context={**params, "registration_date_raw": result["registration_date_raw"]},
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    if not year_entry or not result["year_entry_bounds"]:
        issue = issues.classify_and_record(
            fallback_code="YEAR_ENTRY_NOT_FOUND",
            state_id="S08_COLOR_SELECTED",
            message="S08 color-selected state is verified, but explicit year/age entry was not found.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]},
            current_state="S08_COLOR_SELECTED",
            intended_action="click_year_or_age_entry",
            expected_next_state="S08_YEAR_SELECTION_PANEL",
            actual_next_state=before_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("click_year_or_age_entry"),
            task_context={**params, "registration_date_raw": result["registration_date_raw"]},
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    if not result["actual_click_target_is_year_entry"]:
        issue = issues.classify_and_record(
            fallback_code="YEAR_ACTION_TARGET_MISMATCH",
            state_id="S08_COLOR_SELECTED",
            message="Actual year-entry click target did not match the explicit year/age entry.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "actual_click_target": result["actual_click_target"]},
            current_state="S08_COLOR_SELECTED",
            intended_action="click_year_or_age_entry",
            expected_next_state="S08_YEAR_SELECTION_PANEL",
            actual_next_state=before_page,
            actual_clicked_target={"text": result["actual_click_target"], "role": "year_or_age_entry", "bounds": result["year_entry_bounds"]},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("click_year_or_age_entry"),
            task_context={**params, "registration_date_raw": result["registration_date_raw"]},
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    audit.log(
        "year_entry_click_requested",
        state="S08_COLOR_SELECTED",
        action_id="click_year_or_age_entry",
        actual_click_target=result["actual_click_target"],
        bounds=result["year_entry_bounds"],
        target_vehicle_year=result["target_vehicle_year"],
        registration_date_raw=result["registration_date_raw"],
        clicked_year_option=False,
        clicked_confirm_or_view_result=False,
        entered_vehicle_list=False,
        collected_vehicle_source_fields=False,
    )
    result["audit_logged"] = True
    success = tap_node(client, year_entry)
    result["clicked_year_entry_once"] = bool(success)
    sleep_after(1.5)

    state = get_runtime_state(client)
    after = capture(
        client,
        "s08_after_year_entry_click",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        str(state.get("foreground_package") or ""),
    )
    after_page = classify_after_year_entry(after, params, str(result["target_color"]))
    left_age_tab = find_left_age_tab_node(after["nodes"])  # type: ignore[arg-type]
    result["post_click_artifacts"] = {"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]}
    result["after_page_result"] = after_page
    result["s08_year_selection_panel"] = after_page == "S08_YEAR_SELECTION_PANEL"
    result["left_age_tab_visible"] = bool(left_age_tab)
    result["left_age_tab_bounds"] = valid_bounds(left_age_tab) if left_age_tab else None
    result["entered_vehicle_list"] = after_page in {"S07_VEHICLE_LIST_PAGE", "VEHICLE_LIST_OR_DETAIL", "VEHICLE_DETAIL_PAGE"}

    if after_page == "S08_YEAR_SELECTION_PANEL" and left_age_tab:
        audit.log(
            "year_selection_panel_verified",
            from_state="S08_COLOR_SELECTED",
            to_state="S08_YEAR_SELECTION_PANEL",
            action_id="click_year_or_age_entry",
            actual_click_target=result["actual_click_target"],
            clicked_year_entry_once=True,
            target_vehicle_year=result["target_vehicle_year"],
            registration_date_raw=result["registration_date_raw"],
            left_age_tab_bounds=result["left_age_tab_bounds"],
            clicked_year_option=False,
            clicked_confirm_or_view_result=False,
            entered_vehicle_list=False,
            collected_vehicle_source_fields=False,
        )
        result["audit_logged"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if after_page == "S08_YEAR_SELECTION_PANEL" and not left_age_tab:
        issue = issues.classify_and_record(
            fallback_code="LEFT_AGE_TAB_NOT_FOUND",
            state_id="S08_YEAR_SELECTION_PANEL",
            message="Year/age selection area was verified, but the explicit left-side age tab was not found.",
            context={"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"], "target_vehicle_year": result["target_vehicle_year"], "registration_date_raw": result["registration_date_raw"]},
            current_state="S08_YEAR_SELECTION_PANEL",
            intended_action="detect_left_age_tab",
            expected_next_state="S08_YEAR_SELECTION_PANEL",
            actual_next_state=after_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(after["xml_text"]),
            after_xml=str(after["xml_text"]),
            action_contract=configs["actions"]["actions"].get("detect_left_age_tab"),
            task_context={**params, "registration_date_raw": result["registration_date_raw"]},
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    fallback = "WRONG_PAGE_AFTER_YEAR_ENTRY_CLICK" if result["entered_vehicle_list"] else "YEAR_ENTRY_CLICK_NO_PANEL"
    issue = issues.classify_and_record(
        fallback_code=fallback,
        state_id="S08_COLOR_SELECTED",
        message="Year/age entry click did not verify the expected year/age selection panel.",
        context={"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"], "page": after_page},
        current_state="S08_COLOR_SELECTED",
        intended_action="click_year_or_age_entry",
        expected_next_state="S08_YEAR_SELECTION_PANEL",
        actual_next_state=after_page,
        actual_clicked_target={"text": result["actual_click_target"], "role": "year_or_age_entry", "bounds": result["year_entry_bounds"]},
        before_xml=str(before["xml_text"]),
        after_xml=str(after["xml_text"]),
        action_contract=configs["actions"]["actions"].get("click_year_or_age_entry"),
        task_context={**params, "registration_date_raw": result["registration_date_raw"]},
        resolution="manual_intervention",
    )
    return stop_with_issue(issue)


if __name__ == "__main__":
    raise SystemExit(main())
