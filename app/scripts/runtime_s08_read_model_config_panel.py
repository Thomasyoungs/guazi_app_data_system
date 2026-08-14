"""Read-only S08 vehicle model-config panel contract verification.

This script is intentionally read-only after the device gate: it captures one
screenshot/XML pair, recognizes the S08 model-config panel, reports panel-level
controls, writes an audit record, and stops. It never clicks colors, years,
confirm/view-result, reset, close, sorting, vehicle cards, or collects vehicle
source fields.
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
from runtime_recover_to_s04 import get_runtime_state, sleep_after
from runtime_s04_to_s05 import capture
from runtime_s07_click_model_config import classify_after_model_config_click


PANEL_TITLE_TOKENS = ("车型配置",)
COLOR_TOKENS = ("颜色", "车身颜色", "外观颜色")
YEAR_TOKENS = ("年份", "车龄", "上牌", "上牌时间", "上牌年份", "年款")
CONFIRM_TOKENS = ("确定", "查看结果", "查看", "完成", "确认")
RESET_TOKENS = ("重置",)
CLOSE_TOKENS = ("关闭", "取消", "×", "X", "x")


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


def label_blob(nodes: list[dict[str, object]]) -> str:
    return "".join(node_labels(nodes))


def _bounds(node: dict[str, object] | None) -> list[int] | None:
    if not node:
        return None
    bounds = node.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        return None
    left, top, right, bottom = bounds
    if right <= left or bottom <= top:
        return None
    return bounds


def find_node_by_tokens(nodes: list[dict[str, object]], tokens: tuple[str, ...]) -> dict[str, object] | None:
    """Return the first visible node that contains one of the requested tokens."""

    for node in nodes:
        if not _bounds(node):
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            text = str(label)
            line = first_line(text)
            if any(token and (token == line or token in text) for token in tokens):
                return node
    return None


def task_marker_visibility(nodes: list[dict[str, object]], params: dict[str, Any]) -> dict[str, bool]:
    blob = label_blob(nodes)
    markers = {
        "大众": str(params.get("brand") or ""),
        "帕萨特": str(params.get("series") or ""),
        "2020款": str(params.get("model_year") or ""),
        "330TSI 尊贵版 国VI": str(params.get("trim") or ""),
    }
    return {display_name: bool(raw and raw in blob) for display_name, raw in markers.items()}


def recognize_panel_contract(snapshot: dict[str, object], params: dict[str, Any]) -> dict[str, Any]:
    nodes = snapshot["nodes"]  # type: ignore[assignment]
    title_node = find_node_by_tokens(nodes, PANEL_TITLE_TOKENS)
    color_node = find_node_by_tokens(nodes, COLOR_TOKENS)
    year_node = find_node_by_tokens(nodes, YEAR_TOKENS)
    confirm_node = find_node_by_tokens(nodes, CONFIRM_TOKENS)
    reset_node = find_node_by_tokens(nodes, RESET_TOKENS)
    close_node = find_node_by_tokens(nodes, CLOSE_TOKENS)
    markers = task_marker_visibility(nodes, params)
    selected_model_config_shown = any(markers.values())

    return {
        "panel_title_or_marker_recognized": bool(title_node),
        "selected_model_config_shown": selected_model_config_shown,
        "task_markers_visible": markers,
        "color_entry_exists": bool(color_node),
        "color_entry_bounds": _bounds(color_node),
        "year_or_age_entry_exists": bool(year_node),
        "year_or_age_entry_bounds": _bounds(year_node),
        "confirm_or_view_result_button_exists": bool(confirm_node),
        "confirm_or_view_result_button_bounds": _bounds(confirm_node),
        "reset_button_exists": bool(reset_node),
        "reset_button_bounds": _bounds(reset_node),
        "close_button_exists": bool(close_node),
        "close_button_bounds": _bounds(close_node),
    }


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
        "current_page_result": None,
        "current_page_is_s08_panel": False,
        "panel_title_or_marker_recognized": False,
        "selected_model_config_shown": False,
        "task_markers_visible": {},
        "color_entry_exists": False,
        "color_entry_bounds": None,
        "year_or_age_entry_exists": False,
        "year_or_age_entry_bounds": None,
        "confirm_or_view_result_button_exists": False,
        "confirm_or_view_result_button_bounds": None,
        "reset_button_exists": False,
        "close_button_exists": False,
        "screenshot_path": None,
        "xml_path": None,
        "clicked_color": False,
        "clicked_year_or_age": False,
        "clicked_confirm_or_view_result": False,
        "clicked_sort": False,
        "clicked_vehicle_card": False,
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
    result["transient_recovery_attempted"] = gate.get("transient_recovery_attempted", False)
    if not gate.get("passed"):
        result["adb_status"] = gate.get("status")
        issue_records = gate.get("issue_records") or []
        issue = issue_records[-1] if issue_records else issues.record("DEVICE_NOT_FOUND", "DEVICE", "Device gate failed before S08 read-only panel verification.", gate, "manual_intervention")
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
            "System overlay / keyguard / NotificationShade blocks S08 read-only panel verification.",
            {"runtime_state": state},
            "manual_unlock_required",
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks S08 read-only panel verification.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    result["foreground_app_is_guazi"] = state.get("foreground_package") == "com.ganji.android.haoche_c"
    if not result["foreground_app_is_guazi"]:
        issue = issues.record(
            "PAGE_CONTRACT_MISMATCH",
            "S08_VEHICLE_MODEL_CONFIG_PANEL",
            "Foreground APP is not verified Guazi during S08 read-only panel verification.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    snapshot = capture(
        client,
        "s08_read_model_config_panel",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        str(state.get("foreground_package") or ""),
    )
    page = classify_after_model_config_click(snapshot, params)
    panel = recognize_panel_contract(snapshot, params)

    result["current_page_result"] = page
    result["current_page_is_s08_panel"] = page == "S08_VEHICLE_MODEL_CONFIG_PANEL"
    result["screenshot_path"] = snapshot["screenshot_path"]
    result["xml_path"] = snapshot["xml_path"]
    result.update(panel)

    if page == "VEHICLE_DETAIL_PAGE":
        issue = issues.record(
            "UNEXPECTED_DETAIL_PAGE",
            "S08_VEHICLE_MODEL_CONFIG_PANEL",
            "Unexpected detail page detected during S08 read-only panel verification.",
            {"screenshot_path": snapshot["screenshot_path"], "xml_path": snapshot["xml_path"], "page": page},
            "stop_without_collection",
        )
        return stop_with_issue(issue)

    if page != "S08_VEHICLE_MODEL_CONFIG_PANEL":
        issue = issues.classify_and_record(
            fallback_code="PAGE_CONTRACT_MISMATCH",
            state_id="S08_VEHICLE_MODEL_CONFIG_PANEL",
            message="Current page is not verified S08 vehicle model-config panel.",
            context={"screenshot_path": snapshot["screenshot_path"], "xml_path": snapshot["xml_path"], "page": page},
            current_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            intended_action="read_vehicle_model_config_panel_contract",
            expected_next_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            actual_next_state=page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(snapshot["xml_text"]),
            after_xml=str(snapshot["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("read_vehicle_model_config_panel_contract"),
            task_context=params,
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    if not panel["panel_title_or_marker_recognized"]:
        issue = issues.classify_and_record(
            fallback_code="VEHICLE_MODEL_CONFIG_PANEL_NOT_RECOGNIZED",
            state_id="S08_VEHICLE_MODEL_CONFIG_PANEL",
            message="S08 panel state was inferred but the explicit model-config title/marker was not recognized.",
            context={"screenshot_path": snapshot["screenshot_path"], "xml_path": snapshot["xml_path"], "page": page},
            current_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            intended_action="read_vehicle_model_config_panel_contract",
            expected_next_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            actual_next_state=page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(snapshot["xml_text"]),
            after_xml=str(snapshot["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("read_vehicle_model_config_panel_contract"),
            task_context=params,
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    audit.log(
        "vehicle_model_config_panel_contract_read",
        state="S08_VEHICLE_MODEL_CONFIG_PANEL",
        action_id="read_vehicle_model_config_panel_contract",
        screenshot_path=snapshot["screenshot_path"],
        xml_path=snapshot["xml_path"],
        task_markers_visible=panel["task_markers_visible"],
        color_entry_exists=panel["color_entry_exists"],
        color_entry_bounds=panel["color_entry_bounds"],
        year_or_age_entry_exists=panel["year_or_age_entry_exists"],
        year_or_age_entry_bounds=panel["year_or_age_entry_bounds"],
        confirm_or_view_result_button_exists=panel["confirm_or_view_result_button_exists"],
        confirm_or_view_result_button_bounds=panel["confirm_or_view_result_button_bounds"],
        reset_button_exists=panel["reset_button_exists"],
        close_button_exists=panel["close_button_exists"],
        clicked_color=False,
        clicked_year_or_age=False,
        clicked_confirm_or_view_result=False,
        clicked_sort=False,
        clicked_vehicle_card=False,
        collected_vehicle_source_fields=False,
        modified_pricing_formula=False,
    )
    result["audit_logged"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
