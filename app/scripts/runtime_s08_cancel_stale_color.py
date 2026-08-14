"""Controlled S08 stale-color cancellation.

This script may click exactly one page element: the stale selected color from
the current S08 color panel, currently "白色", while preserving the task target
color "黑色". It never clicks black, any other color, year/age, confirm/view
result, sorting, vehicle cards, or collects vehicle-source fields.
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
from runtime_s08_click_color_entry import COLOR_OPTION_TOKENS, classify_after_color_entry, find_exact_color_node, valid_bounds


COLOR_OPTION_SET = set(COLOR_OPTION_TOKENS)


def first_line(value: str) -> str:
    return value.splitlines()[0].strip()


def selected_color_chips(nodes: list[dict[str, object]]) -> list[str]:
    """Read the selected-color summary chips above the color option grid."""

    selected: list[str] = []
    for node in nodes:
        bounds = valid_bounds(node)
        if not bounds:
            continue
        # The selected color chips are in the applied-filter row near y ~= 900.
        if not (820 <= bounds[1] <= 1080):
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            color = first_line(str(label))
            if color in COLOR_OPTION_SET and color not in selected:
                selected.append(color)
    return selected


def exact_color_option_node(nodes: list[dict[str, object]], color: str) -> dict[str, object] | None:
    """Find a clickable color option in the color grid, not the summary chip."""

    candidates: list[dict[str, object]] = []
    for node in nodes:
        bounds = valid_bounds(node)
        if not bounds or bounds[1] < 1200:
            continue
        if not node.get("clickable"):
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            if first_line(str(label)) == color:
                candidates.append(node)
                break
    if candidates:
        return sorted(candidates, key=lambda item: (valid_bounds(item) or [9999, 9999, 9999, 9999])[1])[0]
    return find_exact_color_node(nodes, color)


def classify_color_multi_snapshot(snapshot: dict[str, object], params: dict[str, Any], target_color: str) -> str:
    base = classify_after_color_entry(snapshot, params)
    nodes = snapshot["nodes"]  # type: ignore[assignment]
    selected = selected_color_chips(nodes)
    if selected == [target_color]:
        return "S08_COLOR_SELECTED_SINGLE_TARGET"
    if target_color in selected and len(selected) > 1:
        return "S08_COLOR_MULTI_SELECTED"
    if base in {"S08_COLOR_SELECTION_PANEL", "S08_VEHICLE_MODEL_CONFIG_PANEL"}:
        return base
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
        "target_color": None,
        "adb_status": None,
        "foreground_app_is_guazi": False,
        "current_page_result": None,
        "selected_colors_before": [],
        "target_color_selected_before": False,
        "stale_color_selected_before": False,
        "stale_color": "白色",
        "stale_color_bounds": None,
        "actual_click_target": None,
        "actual_click_target_is_stale_color": False,
        "clicked_stale_color_once": False,
        "pre_click_artifacts": {},
        "post_click_artifacts": {},
        "after_page_result": None,
        "selected_colors_after": [],
        "target_color_still_selected": False,
        "stale_color_cancelled": False,
        "s08_color_selected_single_target": False,
        "clicked_target_color": False,
        "clicked_other_color": False,
        "clicked_year_or_age": False,
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
    if not result["task_import_verified"]:
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_COLOR_MULTI_SELECTED", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_COLOR_MULTI_SELECTED", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_COLOR_MULTI_SELECTED", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

    target_color = str(result["target_color"] or "")
    stale_color = str(result["stale_color"])
    if target_color != "黑色":
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_COLOR_MULTI_SELECTED", "This stale-color cancel step is authorized only for target color 黑色.", {"target_color": target_color}, "blocked"))

    client = AdbClient()
    gate = run_adb_device_gate(client, issues=issues, audit=audit, allow_transient_recovery=True)
    result["device_gate"] = gate
    result["adb_devices_l"] = gate.get("adb_devices_l_second") or gate.get("adb_devices_l_first")
    if not gate.get("passed"):
        result["adb_status"] = gate.get("status")
        issue_records = gate.get("issue_records") or []
        issue = issue_records[-1] if issue_records else issues.record("DEVICE_NOT_FOUND", "DEVICE", "Device gate failed before S08 stale-color cancellation.", gate, "manual_intervention")
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
            "System overlay / keyguard / NotificationShade blocks S08 stale-color cancellation.",
            {"runtime_state": state},
            "manual_unlock_required",
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks S08 stale-color cancellation.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    result["foreground_app_is_guazi"] = state.get("foreground_package") == "com.ganji.android.haoche_c"
    if not result["foreground_app_is_guazi"]:
        issue = issues.record(
            "PAGE_CONTRACT_MISMATCH",
            "S08_COLOR_MULTI_SELECTED",
            "Foreground APP is not verified Guazi before S08 stale-color cancellation.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    before = capture(
        client,
        "s08_before_stale_color_cancel",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        "com.ganji.android.haoche_c",
    )
    before_page = classify_color_multi_snapshot(before, params, target_color)
    result["pre_click_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}
    result["current_page_result"] = before_page
    selected_before = selected_color_chips(before["nodes"])  # type: ignore[arg-type]
    result["selected_colors_before"] = selected_before
    result["target_color_selected_before"] = target_color in selected_before
    result["stale_color_selected_before"] = stale_color in selected_before

    if before_page != "S08_COLOR_MULTI_SELECTED" or not result["target_color_selected_before"] or not result["stale_color_selected_before"]:
        issue = issues.record(
            "COLOR_MULTI_SELECTED_TARGET_AND_STALE_COLOR",
            "S08_COLOR_MULTI_SELECTED",
            "S08 color state is not confirmed as target black plus stale white; no blind click is allowed.",
            {
                "page": before_page,
                "selected_colors": selected_before,
                "target_color": target_color,
                "stale_color": stale_color,
                "screenshot_path": before["screenshot_path"],
                "xml_path": before["xml_path"],
            },
            "manual_intervention",
        )
        return stop_with_issue(issue)

    issue = issues.record(
        "COLOR_MULTI_SELECTED_TARGET_AND_STALE_COLOR",
        "S08_COLOR_MULTI_SELECTED",
        "S08 has both target color and stale old color selected; cancelling stale color is required before downstream flow.",
        {
            "selected_colors": selected_before,
            "target_color": target_color,
            "stale_color": stale_color,
            "screenshot_path": before["screenshot_path"],
            "xml_path": before["xml_path"],
        },
        "approved_recovery_authorized_by_current_turn",
    )
    result["issue_logged"] = issue

    stale_node = exact_color_option_node(before["nodes"], stale_color)  # type: ignore[arg-type]
    if stale_node:
        result["stale_color_bounds"] = valid_bounds(stale_node)
        result["actual_click_target"] = stale_color
        result["actual_click_target_is_stale_color"] = True
    if not stale_node or not result["stale_color_bounds"]:
        issue = issues.record(
            "STALE_COLOR_NODE_NOT_FOUND",
            "S08_COLOR_MULTI_SELECTED",
            "Stale color 白色 is selected but its cancelable color node was not safely located.",
            {
                "selected_colors": selected_before,
                "target_color": target_color,
                "stale_color": stale_color,
                "screenshot_path": before["screenshot_path"],
                "xml_path": before["xml_path"],
            },
            "manual_intervention",
        )
        return stop_with_issue(issue)

    audit.log(
        "stale_color_cancel_requested",
        state="S08_COLOR_MULTI_SELECTED",
        action_id="cancel_stale_selected_color",
        target_color=target_color,
        stale_color=stale_color,
        selected_colors=selected_before,
        actual_click_target=stale_color,
        stale_color_bounds=result["stale_color_bounds"],
        clicked_target_color=False,
        clicked_other_color=False,
        clicked_year_or_age=False,
        dragged_slider=False,
        clicked_confirm_or_view_result=False,
        entered_vehicle_list=False,
        collected_vehicle_source_fields=False,
    )
    result["audit_logged"] = True

    success = tap_node(client, stale_node)
    result["clicked_stale_color_once"] = bool(success)
    sleep_after(1.5)

    state = get_runtime_state(client)
    after = capture(
        client,
        "s08_after_stale_color_cancel",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        str(state.get("foreground_package") or ""),
    )
    after_page = classify_color_multi_snapshot(after, params, target_color)
    result["post_click_artifacts"] = {"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]}
    result["after_page_result"] = after_page
    selected_after = selected_color_chips(after["nodes"])  # type: ignore[arg-type]
    result["selected_colors_after"] = selected_after
    result["target_color_still_selected"] = target_color in selected_after
    result["stale_color_cancelled"] = stale_color not in selected_after
    result["s08_color_selected_single_target"] = selected_after == [target_color]
    result["entered_vehicle_list"] = after_page in {"S07_VEHICLE_LIST_PAGE", "VEHICLE_LIST_OR_DETAIL", "VEHICLE_DETAIL_PAGE"}

    if result["entered_vehicle_list"]:
        issue = issues.record(
            "WRONG_PAGE_AFTER_COLOR_CANCEL",
            "S08_COLOR_MULTI_SELECTED",
            "Stale-color cancellation unexpectedly reached a vehicle list or detail page.",
            {"page": after_page, "screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]},
            "stop_without_collection",
        )
        return stop_with_issue(issue)
    if not result["target_color_still_selected"]:
        issue = issues.record(
            "TARGET_COLOR_LOST_AFTER_STALE_COLOR_CANCEL",
            "S08_COLOR_MULTI_SELECTED",
            "Target color was lost after cancelling stale color.",
            {"selected_colors_after": selected_after, "screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]},
            "manual_intervention",
        )
        return stop_with_issue(issue)
    if not result["stale_color_cancelled"]:
        issue = issues.record(
            "STALE_COLOR_CANCEL_NO_EFFECT",
            "S08_COLOR_MULTI_SELECTED",
            "Stale color remains selected after one cancellation click.",
            {"selected_colors_after": selected_after, "screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]},
            "manual_intervention",
        )
        return stop_with_issue(issue)
    if not result["s08_color_selected_single_target"]:
        issue = issues.record(
            "COLOR_MULTI_SELECTED_TARGET_AND_STALE_COLOR",
            "S08_COLOR_MULTI_SELECTED",
            "Color state is still not a single task-target color after stale-color cancellation.",
            {"selected_colors_after": selected_after, "screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    audit.log(
        "stale_color_cancel_verified",
        from_state="S08_COLOR_MULTI_SELECTED",
        to_state="S08_COLOR_SELECTED_SINGLE_TARGET",
        action_id="cancel_stale_selected_color",
        target_color=target_color,
        stale_color=stale_color,
        selected_colors_before=selected_before,
        selected_colors_after=selected_after,
        clicked_stale_color_once=True,
        clicked_target_color=False,
        clicked_other_color=False,
        clicked_year_or_age=False,
        dragged_slider=False,
        clicked_confirm_or_view_result=False,
        entered_vehicle_list=False,
        collected_vehicle_source_fields=False,
    )
    result["audit_logged"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
