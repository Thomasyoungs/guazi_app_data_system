"""Controlled S08 left-age-tab click and exact-slider read-only recognition.

This script may click exactly one page element: the explicit left-side
``车龄`` tab inside ``S08_YEAR_SELECTION_PANEL``. After the click it captures
screenshot/XML, verifies that the right side is an exact age slider panel, and
stops. It never clicks ``不限车龄`` or ordinary age options, never drags the
slider, never clicks confirm/view-result, never enters the vehicle list, and
never collects vehicle-source fields.
"""

from __future__ import annotations

from datetime import date
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
from guazi_app_data_system.year_age_filter import (
    calculate_target_age,
    parse_age_slider_current_value,
    requires_vehicle_year_secondary_check,
)
from runtime_recover_to_s04 import get_runtime_state, sleep_after, tap_node
from runtime_s04_to_s05 import capture
from runtime_s08_click_color_entry import valid_bounds
from runtime_s08_click_year_entry import classify_after_year_entry, find_left_age_tab_node


AGE_TAB_LABEL = "\u8f66\u9f84"
AGE_TITLE_LABEL = "\u8f66\u9f84\uff08\u5e74\uff09"
UNLIMITED_AGE_LABEL = "\u4e0d\u9650\u8f66\u9f84"
GUAZI_PACKAGE = "com.ganji.android.haoche_c"
AGE_TICK_RE = re.compile(r"^\d{1,2}$")
FORBIDDEN_ACTION_CONTRACT = (
    "click_unlimited_age",
    "click_age_option",
    "set_age_range",
    "expand_age_range",
    "click_confirm",
    "click_view_result",
    "collect_vehicle_data",
)


def first_line(value: str) -> str:
    return value.splitlines()[0].strip()


def _labels(node: dict[str, object]) -> list[str]:
    return [first_line(str(label)) for label in node.get("labels", [])]  # type: ignore[assignment]


def _valid_screen_bounds(bounds: Any) -> list[int] | None:
    if not isinstance(bounds, list) or len(bounds) != 4:
        return None
    left, top, right, bottom = bounds
    if right <= left or bottom <= top:
        return None
    if left < 0 or top < 0:
        return None
    return [int(left), int(top), int(right), int(bottom)]


def find_visible_left_age_tab_node(nodes: list[dict[str, object]]) -> dict[str, object] | None:
    """Find the left navigation ``车龄`` tab, not ``不限车龄`` or a slider label."""

    candidates: list[dict[str, object]] = []
    for node in nodes:
        bounds = valid_bounds(node)
        if not bounds:
            continue
        if bounds[0] > 320:
            continue
        if AGE_TAB_LABEL in _labels(node):
            candidates.append(node)
    if candidates:
        return sorted(candidates, key=lambda item: ((valid_bounds(item) or [9999, 9999, 9999, 9999])[1], (valid_bounds(item) or [9999, 9999, 9999, 9999])[0]))[0]
    return find_left_age_tab_node(nodes)


def _all_node_labels(nodes: list[dict[str, object]]) -> list[str]:
    labels: list[str] = []
    for node in nodes:
        for label in _labels(node):
            if label and label not in labels:
                labels.append(label)
    return labels


def detect_age_exact_slider(nodes: list[dict[str, object]]) -> dict[str, object]:
    """Read slider evidence without moving it.

    UIAutomator may expose the slider as a SeekBar, or only as tick labels. We
    accept either a real slider node or the age panel title plus visible numeric
    tick marks as read-only slider evidence.
    """

    labels = _all_node_labels(nodes)
    age_title_bounds: list[int] | None = None
    next_section_top: int | None = None
    unlimited_tick_bounds: list[int] | None = None
    tick_nodes: list[dict[str, object]] = []
    seekbar_nodes: list[dict[str, object]] = []
    current_candidates: list[int] = []

    for node in nodes:
        bounds = _valid_screen_bounds(node.get("bounds"))
        if not bounds:
            continue
        node_labels = _labels(node)
        if AGE_TITLE_LABEL in node_labels:
            age_title_bounds = bounds
            continue
        if "\u91cc\u7a0b\uff08\u4e07\u516c\u91cc\uff09" in node_labels and bounds[1] > (age_title_bounds or [0, 0, 0, 0])[1]:
            next_section_top = bounds[1]

    def in_age_slider_region(bounds: list[int]) -> bool:
        if not age_title_bounds:
            return bounds[0] >= 260
        lower = age_title_bounds[3]
        upper = next_section_top if next_section_top is not None else 1800
        return bounds[0] >= 260 and lower <= bounds[1] <= upper

    for node in nodes:
        bounds = _valid_screen_bounds(node.get("bounds"))
        node_class = str(node.get("class") or "")
        node_labels = _labels(node)
        if bounds and in_age_slider_region(bounds) and ("SeekBar" in node_class or "Slider" in node_class):
            seekbar_nodes.append(node)
        for label in node_labels:
            if bounds and in_age_slider_region(bounds) and AGE_TICK_RE.match(label):
                # Age slider ticks live to the right of the left-side nav.
                tick_nodes.append(node)
            if bounds and in_age_slider_region(bounds) and label == "\u4e0d\u9650":
                unlimited_tick_bounds = bounds
            parsed = parse_age_slider_current_value(label)
            if parsed is not None:
                current_candidates.append(parsed)

    tick_values: list[int] = []
    for node in tick_nodes:
        for label in _labels(node):
            if AGE_TICK_RE.match(label):
                value = int(label)
                if value not in tick_values:
                    tick_values.append(value)
    tick_values.sort()

    slider_bounds = None
    if seekbar_nodes:
        slider_bounds = _valid_screen_bounds(seekbar_nodes[0].get("bounds"))
    elif tick_nodes:
        # The knob itself is not always exposed. Use the tick row as the
        # read-only slider region; this is evidence only, never an action target.
        bounds_list = [_valid_screen_bounds(node.get("bounds")) for node in tick_nodes]
        bounds_list = [bounds for bounds in bounds_list if bounds]
        if bounds_list:
            slider_bounds = [
                min(bounds[0] for bounds in bounds_list),
                min(bounds[1] for bounds in bounds_list),
                max([bounds[2] for bounds in bounds_list] + ([unlimited_tick_bounds[2]] if unlimited_tick_bounds else [])),
                max(bounds[3] for bounds in bounds_list),
            ]

    track_bounds = slider_bounds
    current_value = None
    if current_candidates:
        # Avoid treating tick labels as the current value. Only accept explicit
        # current-value labels containing "年".
        explicit_current = [
            value
            for label in labels
            for value in [parse_age_slider_current_value(label)]
            if value is not None and "\u5e74" in label and label != AGE_TITLE_LABEL
        ]
        current_value = explicit_current[0] if explicit_current else None

    found = bool(slider_bounds and (seekbar_nodes or (AGE_TITLE_LABEL in labels and len(tick_values) >= 2)))
    return {
        "found": found,
        "slider_bounds": slider_bounds,
        "track_bounds": track_bounds,
        "current_value": current_value,
        "endpoint_values": [tick_values[0], "\u4e0d\u9650"] if tick_values and unlimited_tick_bounds else ([tick_values[0], tick_values[-1]] if tick_values else None),
        "tick_values": tick_values,
        "age_title_seen": AGE_TITLE_LABEL in labels,
        "unlimited_age_seen": UNLIMITED_AGE_LABEL in labels,
        "unlimited_tick_seen": unlimited_tick_bounds is not None,
        "seekbar_exposed": bool(seekbar_nodes),
    }


def classify_after_left_age_click(snapshot: dict[str, object], params: dict[str, Any], target_age: int) -> str:
    if str(snapshot.get("foreground_package") or "") != GUAZI_PACKAGE:
        return "UNKNOWN_PAGE"
    labels = _all_node_labels(snapshot["nodes"])  # type: ignore[arg-type]
    slider = detect_age_exact_slider(snapshot["nodes"])  # type: ignore[arg-type]
    if slider["found"]:
        return "S08_AGE_EXACT_SLIDER_PANEL"
    # S08 is a panel over the list; underlying list labels can remain present in
    # XML. Only classify as S07 if no S08 slider/panel evidence is present.
    if any(marker in "".join(labels) for marker in ["综合排序", "价格从低到高"]):
        return "S07_VEHICLE_LIST_PAGE"
    base = classify_after_year_entry(snapshot, params, str(params.get("color") or ""))
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
        "current_page_is_year_selection_panel": False,
        "left_age_tab_found": False,
        "left_age_tab_bounds": None,
        "actual_click_target": None,
        "actual_click_target_is_left_age_tab": False,
        "clicked_left_age_tab_once": False,
        "pre_click_artifacts": {},
        "post_click_artifacts": {},
        "after_page_result": None,
        "s08_age_exact_slider_panel": False,
        "right_slider_detected": False,
        "slider_bounds": None,
        "right_track_detected": False,
        "track_bounds": None,
        "current_slider_value": None,
        "slider_endpoint_values": None,
        "slider_tick_values": [],
        "target_age": None,
        "requires_vehicle_year_secondary_check": False,
        "clicked_unlimited_age": False,
        "clicked_age_option": False,
        "dragged_slider": False,
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
    result["registration_date_raw"] = check.get("registration_date_raw") or (check.get("task") or {}).get("registration_date_raw")
    if not result["task_import_verified"]:
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_YEAR_SELECTION_PANEL", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_YEAR_SELECTION_PANEL", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_YEAR_SELECTION_PANEL", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

    target_age = calculate_target_age(str(result["registration_date_raw"] or ""), result["target_vehicle_year"], date(2026, 4, 22))
    result["target_age"] = target_age
    result["requires_vehicle_year_secondary_check"] = requires_vehicle_year_secondary_check()

    client = AdbClient()
    gate = run_adb_device_gate(client, issues=issues, audit=audit, allow_transient_recovery=True)
    result["device_gate"] = gate
    result["adb_devices_l"] = gate.get("adb_devices_l_second") or gate.get("adb_devices_l_first")
    if not gate.get("passed"):
        result["adb_status"] = gate.get("status")
        issue_records = gate.get("issue_records") or []
        issue = issue_records[-1] if issue_records else issues.record("DEVICE_NOT_FOUND", "DEVICE", "Device gate failed before S08 left-age-tab click.", gate, "manual_intervention")
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
            "System overlay / keyguard / NotificationShade blocks S08 left-age-tab click.",
            {"runtime_state": state},
            "manual_unlock_required",
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks S08 left-age-tab click.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    result["foreground_app_is_guazi"] = state.get("foreground_package") == GUAZI_PACKAGE
    if not result["foreground_app_is_guazi"]:
        issue = issues.record(
            "PAGE_CONTRACT_MISMATCH",
            "S08_YEAR_SELECTION_PANEL",
            "Foreground APP is not verified Guazi before S08 left-age-tab click.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    before = capture(
        client,
        "s08_before_left_age_tab_click",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        GUAZI_PACKAGE,
    )
    before_page = classify_after_year_entry(before, params, str(result["target_color"] or ""))
    result["pre_click_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}
    result["current_page_result"] = before_page
    result["current_page_is_year_selection_panel"] = before_page == "S08_YEAR_SELECTION_PANEL"
    left_age_node = find_visible_left_age_tab_node(before["nodes"])  # type: ignore[arg-type]
    if left_age_node:
        result["left_age_tab_found"] = True
        result["left_age_tab_bounds"] = valid_bounds(left_age_node)
        result["actual_click_target"] = AGE_TAB_LABEL
        result["actual_click_target_is_left_age_tab"] = True

    if before_page != "S08_YEAR_SELECTION_PANEL":
        issue = issues.classify_and_record(
            fallback_code="PAGE_CONTRACT_MISMATCH",
            state_id="S08_YEAR_SELECTION_PANEL",
            message="Current page is not verified S08 year-selection panel before left-age-tab click.",
            context={"page": before_page, "screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]},
            current_state="S08_YEAR_SELECTION_PANEL",
            intended_action="click_left_age_tab",
            expected_next_state="S08_AGE_EXACT_SLIDER_PANEL",
            actual_next_state=before_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            page_contract=configs["pages"]["pages"],
            action_contract=configs["actions"]["actions"].get("click_left_age_tab"),
            task_context=params,
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)
    if not left_age_node or not result["left_age_tab_bounds"]:
        issue = issues.classify_and_record(
            fallback_code="LEFT_AGE_TAB_NOT_FOUND",
            state_id="S08_YEAR_SELECTION_PANEL",
            message="Left-side age tab was not found in S08 year-selection panel.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]},
            current_state="S08_YEAR_SELECTION_PANEL",
            intended_action="detect_left_age_tab",
            expected_next_state="S08_YEAR_SELECTION_PANEL",
            actual_next_state=before_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("detect_left_age_tab"),
            task_context=params,
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)
    if result["actual_click_target"] != AGE_TAB_LABEL:
        issue = issues.classify_and_record(
            fallback_code="S08_YEAR_CONTRACT_DRIFT_TO_OPTION_SCAN",
            state_id="S08_YEAR_SELECTION_PANEL",
            message="Actual click target is not the left-side age tab.",
            context={"actual_click_target": result["actual_click_target"], "bounds": result["left_age_tab_bounds"]},
            current_state="S08_YEAR_SELECTION_PANEL",
            intended_action="click_left_age_tab",
            expected_next_state="S08_AGE_EXACT_SLIDER_PANEL",
            actual_next_state=before_page,
            actual_clicked_target={"text": result["actual_click_target"], "role": "left_age_tab"},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("click_left_age_tab"),
            task_context=params,
            resolution="blocked",
        )
        return stop_with_issue(issue)

    audit.log(
        "left_age_tab_click_requested",
        state="S08_YEAR_SELECTION_PANEL",
        action_id="click_left_age_tab",
        actual_click_target=AGE_TAB_LABEL,
        left_age_tab_bounds=result["left_age_tab_bounds"],
        target_age=target_age,
        requires_vehicle_year_secondary_check=result["requires_vehicle_year_secondary_check"],
        clicked_unlimited_age=False,
        clicked_age_option=False,
        dragged_slider=False,
        clicked_confirm_or_view_result=False,
        entered_vehicle_list=False,
        collected_vehicle_source_fields=False,
    )
    result["audit_logged"] = True

    success = tap_node(client, left_age_node)
    result["clicked_left_age_tab_once"] = bool(success)
    sleep_after(1.5)

    state = get_runtime_state(client)
    after = capture(
        client,
        "s08_after_left_age_tab_click",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        str(state.get("foreground_package") or ""),
    )
    after_page = classify_after_left_age_click(after, params, target_age)
    slider = detect_age_exact_slider(after["nodes"])  # type: ignore[arg-type]
    result["post_click_artifacts"] = {"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]}
    result["after_page_result"] = after_page
    result["s08_age_exact_slider_panel"] = after_page == "S08_AGE_EXACT_SLIDER_PANEL"
    result["right_slider_detected"] = bool(slider["found"])
    result["slider_bounds"] = slider["slider_bounds"]
    result["right_track_detected"] = bool(slider["track_bounds"])
    result["track_bounds"] = slider["track_bounds"]
    result["current_slider_value"] = slider["current_value"]
    result["slider_endpoint_values"] = slider["endpoint_values"]
    result["slider_tick_values"] = slider["tick_values"]
    result["entered_vehicle_list"] = after_page == "S07_VEHICLE_LIST_PAGE"

    if result["entered_vehicle_list"]:
        issue = issues.record(
            "WRONG_PAGE_AFTER_AGE_TAB_CLICK",
            "S08_YEAR_SELECTION_PANEL",
            "Clicking the left age tab entered the vehicle list unexpectedly.",
            {"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]},
            "manual_intervention",
        )
        return stop_with_issue(issue)
    if after_page != "S08_AGE_EXACT_SLIDER_PANEL" or not slider["found"]:
        issue = issues.classify_and_record(
            fallback_code="AGE_EXACT_SLIDER_NOT_FOUND",
            state_id="S08_YEAR_SELECTION_PANEL",
            message="Left age tab click did not reveal a verified exact age slider/track.",
            context={"after_page": after_page, "slider": slider, "screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]},
            current_state="S08_YEAR_SELECTION_PANEL",
            intended_action="click_left_age_tab",
            expected_next_state="S08_AGE_EXACT_SLIDER_PANEL",
            actual_next_state=after_page,
            actual_clicked_target={"text": AGE_TAB_LABEL, "role": "left_age_tab", "bounds": result["left_age_tab_bounds"]},
            before_xml=str(before["xml_text"]),
            after_xml=str(after["xml_text"]),
            action_contract=configs["actions"]["actions"].get("click_left_age_tab"),
            task_context=params,
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    audit.log(
        "left_age_tab_click_verified",
        state="S08_AGE_EXACT_SLIDER_PANEL",
        action_id="detect_age_exact_slider",
        after_page=after_page,
        slider_bounds=result["slider_bounds"],
        track_bounds=result["track_bounds"],
        current_slider_value=result["current_slider_value"],
        slider_endpoint_values=result["slider_endpoint_values"],
        target_age=target_age,
        requires_vehicle_year_secondary_check=result["requires_vehicle_year_secondary_check"],
        dragged_slider=False,
        clicked_unlimited_age=False,
        clicked_confirm_or_view_result=False,
        entered_vehicle_list=False,
        collected_vehicle_source_fields=False,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
