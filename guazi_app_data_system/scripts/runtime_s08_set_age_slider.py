"""Controlled S08 dual-handle exact-age-slider correction.

This script is intentionally narrow. It only works on the verified
``S08_AGE_EXACT_SLIDER_PANEL`` and treats the age control as a dual-handle
slider:

- left handle = minimum age
- right handle = maximum age

Exact success means ``left_handle_value == right_handle_value == target_age``.
If the left handle is already ``6`` but the right handle is not, this script is
allowed to move only the right handle once to ``6``. It never clicks unlimited
age, confirm/view-result, vehicle cards, or collects any vehicle fields.
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
    bounds_center,
    calculate_right_handle_overlap_target_coordinate,
    calculate_target_age,
    detect_dual_handle_slider,
    handles_physically_overlap,
    normalize_age_handle_value,
    parse_age_range_label,
    parse_left_handle_value,
    parse_right_handle_value,
    parse_age_slider_current_value,
    requires_vehicle_year_secondary_check,
    validate_exact_age_range,
)
from runtime_recover_to_s04 import get_runtime_state, sleep_after
from runtime_s04_to_s05 import capture
from runtime_s08_cancel_stale_color import selected_color_chips
from runtime_s08_click_color_entry import valid_bounds
from runtime_s08_click_left_age_tab import (
    AGE_TITLE_LABEL,
    GUAZI_PACKAGE,
    detect_age_exact_slider,
    classify_after_left_age_click,
)


TARGET_AGE = 6
RIGHT_HANDLE_DRAG_DURATION_MS = 900
UNLIMITED_TICK_LABEL = "\u4e0d\u9650"
AGE_TICK_RE = re.compile(r"^\d{1,2}$")
RANGE_LABEL_RE = re.compile(r"(?P<left>\d{1,2})\s*[-~\u5230]\s*(?P<right>\d{1,2}|\u4e0d\u9650)")
OVER_LABEL_RE = re.compile(r"(?P<left>\d{1,2})\s*\u5e74?\s*(?:\u4ee5\u4e0a|\u53ca\u4ee5\u4e0a)")


def first_line(value: str) -> str:
    return value.splitlines()[0].strip()


def labels_for(node: dict[str, object]) -> list[str]:
    return [first_line(str(label)) for label in node.get("labels", [])]  # type: ignore[assignment]


def center(bounds: list[int]) -> list[int]:
    return [(bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2]


def within(bounds: list[int], point: list[int]) -> bool:
    return bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]


def age_slider_vertical_region(nodes: list[dict[str, object]]) -> tuple[int, int]:
    top = 0
    bottom = 1800
    for node in nodes:
        bounds = valid_bounds(node)
        if not bounds:
            continue
        node_labels = labels_for(node)
        if AGE_TITLE_LABEL in node_labels:
            top = bounds[3]
        if "\u91cc\u7a0b\uff08\u4e07\u516c\u91cc\uff09" in node_labels and bounds[1] > top:
            bottom = bounds[1]
            break
    return top, bottom


def normalize_tick_label(label: str) -> int | str | None:
    parsed = parse_age_slider_current_value(label)
    if parsed is not None:
        return parsed
    if label == UNLIMITED_TICK_LABEL:
        return UNLIMITED_TICK_LABEL
    if AGE_TICK_RE.match(label):
        return int(label)
    return None


def find_tick_nodes(nodes: list[dict[str, object]], track_bounds: list[int] | None) -> list[dict[str, object]]:
    if not track_bounds:
        return []
    tick_nodes: list[dict[str, object]] = []
    seen: set[tuple[str, tuple[int, int, int, int]]] = set()
    top, bottom = age_slider_vertical_region(nodes)
    for node in nodes:
        bounds = valid_bounds(node)
        if not bounds:
            continue
        if bounds[0] < track_bounds[0] - 20 or bounds[2] > track_bounds[2] + 20:
            continue
        if not (top <= bounds[1] <= bottom):
            continue
        for label in labels_for(node):
            normalized = normalize_tick_label(label)
            if normalized is None:
                continue
            key = (str(normalized), tuple(bounds))
            if key in seen:
                continue
            seen.add(key)
            tick_nodes.append(
                {
                    "label": label,
                    "value": normalized,
                    "bounds": bounds,
                    "center": center(bounds),
                }
            )
    return sorted(tick_nodes, key=lambda item: int(item["bounds"][0]))  # type: ignore[index]


def find_target_tick(tick_nodes: list[dict[str, object]], target_age: int) -> dict[str, object] | None:
    for tick in tick_nodes:
        if tick.get("value") == target_age:
            return tick
    return None


def parse_selected_range_label_from_nodes(nodes: list[dict[str, object]]) -> str | None:
    for node in nodes:
        for label in labels_for(node):
            if RANGE_LABEL_RE.search(label) or OVER_LABEL_RE.search(label):
                return label
    return None


def find_summary_tick_nodes(nodes: list[dict[str, object]], track_bounds: list[int] | None) -> list[dict[str, object]]:
    if not track_bounds:
        return []
    summary_nodes: list[dict[str, object]] = []
    seen: set[tuple[str, tuple[int, int, int, int]]] = set()
    min_y = track_bounds[3] + 120
    max_y = track_bounds[3] + 650
    for node in nodes:
        bounds = valid_bounds(node)
        if not bounds:
            continue
        if bounds[0] < track_bounds[0] - 20 or bounds[2] > track_bounds[2] + 40:
            continue
        if not (min_y <= bounds[1] <= max_y):
            continue
        for label in labels_for(node):
            normalized = normalize_tick_label(label)
            if normalized is None:
                continue
            key = (str(normalized), tuple(bounds))
            if key in seen:
                continue
            seen.add(key)
            summary_nodes.append(
                {
                    "label": label,
                    "value": normalized,
                    "bounds": bounds,
                    "center": center(bounds),
                }
            )
    return sorted(summary_nodes, key=lambda item: int(item["bounds"][0]))  # type: ignore[index]


def infer_selected_range_from_summary(summary_nodes: list[dict[str, object]]) -> tuple[int | str | None, int | str | None]:
    if not summary_nodes:
        return None, None
    values: list[int | str] = [item["value"] for item in summary_nodes if item.get("value") is not None]
    if not values:
        return None, None
    trimmed = list(values)
    if trimmed and trimmed[0] == 0 and len(trimmed) >= 2:
        trimmed = trimmed[1:]
    if len(trimmed) >= 2:
        return trimmed[0], trimmed[-1]
    if len(trimmed) == 1:
        return trimmed[0], trimmed[0]
    return None, None


def find_handle_candidates(nodes: list[dict[str, object]], track_bounds: list[int] | None) -> list[list[int]]:
    if not track_bounds:
        return []
    candidates: list[list[int]] = []
    seen: set[tuple[int, int, int, int]] = set()
    # The usable handle node is the green thumb body below/around the track. Do
    # not accept tick labels, list-card text, track containers, or upper edges.
    band_top = track_bounds[1] - 20
    band_bottom = track_bounds[3] + 150
    for node in nodes:
        bounds = valid_bounds(node)
        if not bounds:
            continue
        if labels_for(node):
            continue
        if bounds[0] < track_bounds[0] - 40 or bounds[2] > track_bounds[2] + 40:
            continue
        if bounds[1] < band_top or bounds[3] > band_bottom:
            continue
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        if width < 80 or width > 190 or height < 95 or height > 190:
            continue
        # True thumb nodes overlap the slider track and extend clearly below it.
        if bounds[1] > track_bounds[1] + 40 or bounds[3] < track_bounds[3] + 65:
            continue
        key = tuple(bounds)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(bounds)
    return sorted(candidates, key=lambda item: item[0])


def nearest_tick_value_from_handle(handle_bounds: list[int] | None, tick_nodes: list[dict[str, object]]) -> int | str | None:
    if not handle_bounds or not tick_nodes:
        return None
    handle_center = center(handle_bounds)
    numeric_ticks = [tick for tick in tick_nodes if isinstance(tick.get("value"), int)]
    if not numeric_ticks:
        return None
    nearest = min(numeric_ticks, key=lambda tick: abs(int(tick["center"][0]) - handle_center[0]))  # type: ignore[index]
    return nearest.get("value")


def detect_dual_handle_snapshot(nodes: list[dict[str, object]], slider_snapshot: dict[str, object]) -> dict[str, object]:
    track_bounds = slider_snapshot.get("track_bounds")
    tick_nodes = find_tick_nodes(nodes, track_bounds if isinstance(track_bounds, list) else None)
    summary_nodes = find_summary_tick_nodes(nodes, track_bounds if isinstance(track_bounds, list) else None)
    range_label = parse_selected_range_label_from_nodes(nodes)
    parsed_range = parse_age_range_label(range_label)
    handle_candidates = find_handle_candidates(nodes, track_bounds if isinstance(track_bounds, list) else None)

    left_handle_bounds = handle_candidates[0] if len(handle_candidates) >= 1 else None
    right_handle_bounds = handle_candidates[-1] if len(handle_candidates) >= 2 else None

    summary_left, summary_right = infer_selected_range_from_summary(summary_nodes)
    # The selected age label (for example "6年以上") is authoritative. Lower
    # controls such as mileage can leave numeric ticks in the same XML band, so
    # summary ticks are only fallback evidence.
    left_handle_value = parsed_range["left_handle_value"]
    right_handle_value = parsed_range["right_handle_value"]
    if left_handle_value is None:
        left_handle_value = summary_left
    if right_handle_value is None:
        right_handle_value = summary_right
    if left_handle_value is None:
        left_handle_value = nearest_tick_value_from_handle(left_handle_bounds, tick_nodes)
    if right_handle_value is None:
        right_handle_value = nearest_tick_value_from_handle(right_handle_bounds, tick_nodes)

    normalized = detect_dual_handle_slider(
        {
            "dual_handle_detected": len(handle_candidates) >= 2,
            "left_handle_bounds": left_handle_bounds,
            "right_handle_bounds": right_handle_bounds,
            "left_handle_value": left_handle_value,
            "right_handle_value": right_handle_value,
            "slider_bounds": slider_snapshot.get("slider_bounds"),
            "track_bounds": slider_snapshot.get("track_bounds"),
            "selected_range_label": range_label,
        }
    )
    normalized["tick_nodes"] = tick_nodes
    normalized["summary_nodes"] = summary_nodes
    normalized["range_label"] = range_label
    normalized["left_handle_actual_bounds"] = left_handle_bounds
    normalized["right_handle_actual_bounds"] = right_handle_bounds
    normalized["left_handle_actual_center"] = bounds_center(left_handle_bounds)
    normalized["right_handle_actual_center"] = bounds_center(right_handle_bounds)
    normalized["handle_physical_overlap"] = handles_physically_overlap(left_handle_bounds, right_handle_bounds)
    return normalized


def right_handle_overlap_target_coordinate(slider_snapshot: dict[str, object]) -> dict[str, object]:
    left_bounds = slider_snapshot.get("left_handle_actual_bounds") or slider_snapshot.get("left_handle_bounds")
    target = calculate_right_handle_overlap_target_coordinate(left_bounds if isinstance(left_bounds, list) else None)
    return {
        "right_handle_overlap_target_coordinate": target,
        "right_handle_target_coordinate_source": "left_handle_physical_center" if target else None,
    }


def target_coordinate_from_slider(
    slider_snapshot: dict[str, object],
    target_age: int,
) -> dict[str, object]:
    track_bounds = slider_snapshot.get("track_bounds")
    tick_nodes = slider_snapshot.get("tick_nodes", [])
    if not isinstance(track_bounds, list) or len(track_bounds) != 4:
        return {
            "target_tick_found": False,
            "target_tick_bounds": None,
            "target_coordinate": None,
            "coordinate_source": None,
        }

    tick_node = find_target_tick(tick_nodes if isinstance(tick_nodes, list) else [], target_age)
    if tick_node:
        tick_bounds = valid_bounds(tick_node)
        if tick_bounds:
            coordinate = center(tick_bounds)
            if within(track_bounds, coordinate):
                return {
                    "target_tick_found": True,
                    "target_tick_bounds": tick_bounds,
                    "target_coordinate": coordinate,
                    "coordinate_source": "xml_tick_node",
                }

    tick_values = [int(item["value"]) for item in tick_nodes if isinstance(item.get("value"), int)] if isinstance(tick_nodes, list) else []
    if not tick_values or target_age not in tick_values:
        return {
            "target_tick_found": False,
            "target_tick_bounds": None,
            "target_coordinate": None,
            "coordinate_source": None,
        }
    min_tick = min(tick_values)
    max_tick = max(tick_values)
    if max_tick <= min_tick:
        return {
            "target_tick_found": False,
            "target_tick_bounds": None,
            "target_coordinate": None,
            "coordinate_source": None,
        }
    ratio = (target_age - min_tick) / (max_tick - min_tick)
    x = int(round(track_bounds[0] + ratio * (track_bounds[2] - track_bounds[0])))
    y = (track_bounds[1] + track_bounds[3]) // 2
    return {
        "target_tick_found": True,
        "target_tick_bounds": None,
        "target_coordinate": [x, y],
        "coordinate_source": "calculated_from_track",
    }


def classify_dual_slider_state(snapshot: dict[str, object], target_age: int) -> str:
    left_value = parse_left_handle_value(snapshot)
    right_value = parse_right_handle_value(snapshot)
    if validate_exact_age_range(
        left_value,
        right_value,
        target_age,
        left_handle_bounds=snapshot.get("left_handle_actual_bounds") if isinstance(snapshot.get("left_handle_actual_bounds"), list) else None,
        right_handle_bounds=snapshot.get("right_handle_actual_bounds") if isinstance(snapshot.get("right_handle_actual_bounds"), list) else None,
        require_physical_overlap=True,
        target_age_calculation_verified=True,
    ):
        return "S08_AGE_EXACT_VALUE_SELECTED"
    if normalize_age_handle_value(left_value) == target_age and normalize_age_handle_value(right_value) != target_age:
        return "S08_AGE_LEFT_HANDLE_SET_ONLY"
    return "S08_AGE_EXACT_SLIDER_PANEL"


def build_result() -> dict[str, object]:
    return {
        "task_import_verified": False,
        "target_color": None,
        "selected_colors": [],
        "target_age": None,
        "adb_status": None,
        "foreground_app_is_guazi": False,
        "current_page_result": None,
        "current_page_is_age_exact_slider_panel": False,
        "background_list_residual_not_misclassified": False,
        "slider_detected": False,
        "slider_bounds": None,
        "track_detected": False,
        "track_bounds": None,
        "visible_ticks": [],
        "target_tick_found": False,
        "target_tick_bounds": None,
        "target_tick_coordinate": None,
        "target_tick_coordinate_source": None,
        "target_coordinate": None,
        "coordinate_source": None,
        "dual_handle_detected": False,
        "left_handle_bounds": None,
        "right_handle_bounds": None,
        "left_handle_actual_bounds": None,
        "left_handle_actual_center": None,
        "right_handle_actual_bounds": None,
        "right_handle_actual_center": None,
        "handle_physical_overlap_before": False,
        "handle_physical_overlap_after": False,
        "left_handle_value_before": None,
        "right_handle_value_before": None,
        "controlled_slider_set_once": False,
        "right_handle_drag_start": None,
        "right_handle_drag_end": None,
        "right_handle_drag_duration_ms": RIGHT_HANDLE_DRAG_DURATION_MS,
        "pre_set_artifacts": {},
        "post_set_artifacts": {},
        "after_page_result": None,
        "left_handle_value_after": None,
        "right_handle_value_after": None,
        "s08_age_exact_value_selected": False,
        "requires_vehicle_year_secondary_check": False,
        "clicked_unlimited_age": False,
        "set_non_target_age": False,
        "clicked_confirm_or_view_result": False,
        "entered_vehicle_list": False,
        "collected_vehicle_source_fields": False,
        "modified_pricing_formula": False,
        "audit_logged": False,
        "issue_logged": None,
        "pending_issue_code": None,
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
    result = build_result()

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
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_AGE_EXACT_SLIDER_PANEL", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_AGE_EXACT_SLIDER_PANEL", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_AGE_EXACT_SLIDER_PANEL", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

    target_age = calculate_target_age(str(result["registration_date_raw"] or ""), result["target_vehicle_year"], date(2026, 4, 22))
    result["target_age"] = target_age
    result["requires_vehicle_year_secondary_check"] = requires_vehicle_year_secondary_check()
    if target_age != TARGET_AGE:
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S08_AGE_EXACT_SLIDER_PANEL", "This run is authorized only for target_age=6.", {"target_age": target_age}, "blocked"))

    client = AdbClient()
    gate = run_adb_device_gate(client, issues=issues, audit=audit, allow_transient_recovery=True)
    result["device_gate"] = gate
    result["adb_devices_l"] = gate.get("adb_devices_l_second") or gate.get("adb_devices_l_first")
    if not gate.get("passed"):
        result["adb_status"] = gate.get("status")
        issue_records = gate.get("issue_records") or []
        issue = issue_records[-1] if issue_records else issues.record("DEVICE_NOT_FOUND", "DEVICE", "Device gate failed before dual-handle age-slider correction.", gate, "manual_intervention")
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
            "System overlay / keyguard / NotificationShade blocks S08 dual-handle age-slider correction.",
            {"runtime_state": state},
            "manual_unlock_required",
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks S08 dual-handle age-slider correction.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    result["foreground_app_is_guazi"] = state.get("foreground_package") == GUAZI_PACKAGE
    if not result["foreground_app_is_guazi"]:
        issue = issues.record(
            "PAGE_CONTRACT_MISMATCH",
            "S08_AGE_EXACT_SLIDER_PANEL",
            "Foreground APP is not verified Guazi before S08 dual-handle age-slider correction.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    before = capture(
        client,
        "s08_before_age_slider_set",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        GUAZI_PACKAGE,
    )
    result["pre_set_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}
    before_page = classify_after_left_age_click(before, params, target_age)
    before_slider = detect_age_exact_slider(before["nodes"])  # type: ignore[arg-type]
    before_dual = detect_dual_handle_snapshot(before["nodes"], before_slider)  # type: ignore[arg-type]
    before_state = classify_dual_slider_state(before_dual, target_age)
    result["current_page_result"] = before_state if before_page == "S08_AGE_EXACT_SLIDER_PANEL" else before_page
    result["current_page_is_age_exact_slider_panel"] = before_page == "S08_AGE_EXACT_SLIDER_PANEL"
    result["background_list_residual_not_misclassified"] = before_page == "S08_AGE_EXACT_SLIDER_PANEL" and bool(before_slider["found"])
    result["slider_detected"] = bool(before_slider["found"])
    result["slider_bounds"] = before_slider["slider_bounds"]
    result["track_detected"] = bool(before_slider["track_bounds"])
    result["track_bounds"] = before_slider["track_bounds"]
    result["visible_ticks"] = before_slider["tick_values"]
    result["selected_colors"] = selected_color_chips(before["nodes"])  # type: ignore[arg-type]
    result["dual_handle_detected"] = bool(before_dual["dual_handle_detected"])
    result["left_handle_bounds"] = before_dual["left_handle_bounds"]
    result["right_handle_bounds"] = before_dual["right_handle_bounds"]
    result["left_handle_actual_bounds"] = before_dual["left_handle_actual_bounds"]
    result["left_handle_actual_center"] = before_dual["left_handle_actual_center"]
    result["right_handle_actual_bounds"] = before_dual["right_handle_actual_bounds"]
    result["right_handle_actual_center"] = before_dual["right_handle_actual_center"]
    result["handle_physical_overlap_before"] = before_dual["handle_physical_overlap"]
    result["left_handle_value_before"] = parse_left_handle_value(before_dual)
    result["right_handle_value_before"] = parse_right_handle_value(before_dual)
    if before_state == "S08_AGE_LEFT_HANDLE_SET_ONLY":
        result["pending_issue_code"] = "AGE_SLIDER_ONLY_LEFT_HANDLE_SET"

    if before_page != "S08_AGE_EXACT_SLIDER_PANEL" or not before_slider["found"]:
        issue = issues.classify_and_record(
            fallback_code="PAGE_CONTRACT_MISMATCH",
            state_id="S08_AGE_EXACT_SLIDER_PANEL",
            message="Current page is not verified S08 exact-age-slider panel before dual-handle correction.",
            context={"page": before_page, "slider": before_slider, "screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]},
            current_state="S08_AGE_EXACT_SLIDER_PANEL",
            intended_action="detect_age_exact_slider",
            expected_next_state="S08_AGE_EXACT_SLIDER_PANEL",
            actual_next_state=before_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("detect_age_exact_slider"),
            task_context={**params, "target_age": target_age},
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    if not before_dual["dual_handle_detected"] or not before_dual["left_handle_bounds"] or not before_dual["right_handle_bounds"]:
        issue = issues.record(
            "AGE_SLIDER_NOT_FOUND",
            "S08_AGE_EXACT_SLIDER_PANEL",
            "Dual-handle slider could not be distinguished safely before correction.",
            {"slider": before_slider, "dual": before_dual, "screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    left_before = normalize_age_handle_value(result["left_handle_value_before"])
    right_before = normalize_age_handle_value(result["right_handle_value_before"])
    coord = target_coordinate_from_slider(before_dual, target_age)
    overlap_target = right_handle_overlap_target_coordinate(before_dual)
    result["target_tick_found"] = bool(coord["target_tick_found"])
    result["target_tick_bounds"] = coord["target_tick_bounds"]
    result["target_tick_coordinate"] = coord["target_coordinate"]
    result["target_tick_coordinate_source"] = coord["coordinate_source"]
    result["target_coordinate"] = overlap_target["right_handle_overlap_target_coordinate"]
    result["coordinate_source"] = overlap_target["right_handle_target_coordinate_source"]
    if not result["target_tick_found"] or not result["target_coordinate"]:
        issue = issues.record(
            "TARGET_AGE_TICK_NOT_FOUND",
            "S08_AGE_EXACT_SLIDER_PANEL",
            "Target age tick 6 or physical overlap target coordinate could not be calculated safely.",
            {"slider": before_slider, "dual": before_dual, "screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    right_target_coordinate = result["target_coordinate"] if isinstance(result["target_coordinate"], list) else None
    result["right_target_coordinate"] = right_target_coordinate

    audit.log(
        "age_slider_dual_handle_set_requested",
        state="S08_AGE_EXACT_SLIDER_PANEL",
        action_id="set_right_age_handle_to_target",
        target_age=target_age,
        slider_bounds=result["slider_bounds"],
        track_bounds=result["track_bounds"],
        tick_values=result["visible_ticks"],
        left_handle_bounds=result["left_handle_bounds"],
        right_handle_bounds=result["right_handle_bounds"],
        left_handle_value_before=result["left_handle_value_before"],
        right_handle_value_before=result["right_handle_value_before"],
        target_tick=target_age,
        target_tick_bounds=result["target_tick_bounds"],
        target_tick_coordinate=result["target_tick_coordinate"],
        target_coordinate=result["target_coordinate"],
        right_target_coordinate=right_target_coordinate,
        coordinate_source=result["coordinate_source"],
        right_handle_actual_bounds=before_dual.get("right_handle_actual_bounds"),
        right_handle_actual_center=before_dual.get("right_handle_actual_center"),
        clicked_unlimited_age=False,
        set_non_target_age=False,
        clicked_confirm_or_view_result=False,
        entered_vehicle_list=False,
        collected_vehicle_source_fields=False,
        requires_vehicle_year_secondary_check=result["requires_vehicle_year_secondary_check"],
    )
    result["audit_logged"] = True

    if validate_exact_age_range(
        left_before,
        right_before,
        target_age,
        left_handle_bounds=before_dual.get("left_handle_actual_bounds") if isinstance(before_dual.get("left_handle_actual_bounds"), list) else None,
        right_handle_bounds=before_dual.get("right_handle_actual_bounds") if isinstance(before_dual.get("right_handle_actual_bounds"), list) else None,
        require_physical_overlap=True,
        target_age_calculation_verified=True,
    ):
        result["after_page_result"] = "S08_AGE_EXACT_VALUE_SELECTED"
        result["left_handle_value_after"] = result["left_handle_value_before"]
        result["right_handle_value_after"] = result["right_handle_value_before"]
        result["s08_age_exact_value_selected"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if left_before != target_age:
        issue = issues.record(
            "AGE_LEFT_HANDLE_NOT_TARGET",
            "S08_AGE_EXACT_SLIDER_PANEL",
            "Left age handle is not at target_age; refusing to move the right handle.",
            {
                "target_age": target_age,
                "left_handle_value_before": result["left_handle_value_before"],
                "right_handle_value_before": result["right_handle_value_before"],
                "screenshot_path": before["screenshot_path"],
                "xml_path": before["xml_path"],
            },
            "manual_intervention",
        )
        return stop_with_issue(issue)

    if right_before == target_age:
        issue = issues.classify_and_record(
            fallback_code="AGE_SLIDER_SET_NO_VERIFICATION",
            state_id="S08_AGE_EXACT_SLIDER_PANEL",
            message="Dual-handle exact-age correction requires right_handle_value != 6 before moving only the right handle.",
            context={
                "left_handle_value_before": result["left_handle_value_before"],
                "right_handle_value_before": result["right_handle_value_before"],
                "screenshot_path": before["screenshot_path"],
                "xml_path": before["xml_path"],
            },
            current_state="S08_AGE_EXACT_SLIDER_PANEL",
            intended_action="set_age_slider_exact_value",
            expected_next_state="S08_AGE_EXACT_VALUE_SELECTED",
            actual_next_state=before_state,
            actual_clicked_target={"text": str(target_age), "role": "right_age_handle", "bounds": result["right_handle_bounds"]},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("set_age_slider_exact_value"),
            task_context={
                **params,
                "target_age": target_age,
                "left_handle_value_after": result["left_handle_value_before"],
                "right_handle_value_after": result["right_handle_value_before"],
            },
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    if not isinstance(result["right_handle_bounds"], list):
        issue = issues.record(
            "AGE_RIGHT_HANDLE_NOT_RECOGNIZED",
            "S08_AGE_EXACT_SLIDER_PANEL",
            "Right age handle could not be recognized safely; refusing to drag.",
            {"dual": before_dual, "track_bounds": result["track_bounds"]},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    if not right_target_coordinate:
        issue = issues.record(
            "TARGET_AGE_TICK_NOT_FOUND",
            "S08_AGE_EXACT_SLIDER_PANEL",
            "Right-handle target coordinate could not be calculated safely.",
            {"dual": before_dual, "target_coordinate": result["target_coordinate"], "track_bounds": result["track_bounds"]},
            "manual_intervention",
        )
        return stop_with_issue(issue)

    start_x, start_y = center(result["right_handle_bounds"])  # type: ignore[arg-type]
    end_x, end_y = right_target_coordinate
    result["right_handle_drag_start"] = [start_x, start_y]
    result["right_handle_drag_end"] = [end_x, end_y]
    swipe_result = client.run(
        ["shell", "input", "swipe", str(start_x), str(start_y), str(end_x), str(end_y), str(RIGHT_HANDLE_DRAG_DURATION_MS)],
        timeout=20,
    )
    result["controlled_slider_set_once"] = bool(swipe_result.success)
    sleep_after(1.5)

    state = get_runtime_state(client)
    after = capture(
        client,
        "s08_after_age_slider_set",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        str(state.get("foreground_package") or ""),
    )
    result["post_set_artifacts"] = {"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]}
    after_slider = detect_age_exact_slider(after["nodes"])  # type: ignore[arg-type]
    after_dual = detect_dual_handle_snapshot(after["nodes"], after_slider)  # type: ignore[arg-type]
    after_page = classify_after_left_age_click(after, params, target_age)
    after_state = classify_dual_slider_state(after_dual, target_age)
    result["after_page_result"] = after_state if after_page == "S08_AGE_EXACT_SLIDER_PANEL" else after_page
    result["left_handle_value_after"] = parse_left_handle_value(after_dual)
    result["right_handle_value_after"] = parse_right_handle_value(after_dual)
    result["left_handle_actual_bounds_after"] = after_dual.get("left_handle_actual_bounds")
    result["left_handle_actual_center_after"] = after_dual.get("left_handle_actual_center")
    result["right_handle_actual_bounds_after"] = after_dual.get("right_handle_actual_bounds")
    result["right_handle_actual_center_after"] = after_dual.get("right_handle_actual_center")
    result["handle_physical_overlap_after"] = after_dual.get("handle_physical_overlap")
    result["entered_vehicle_list"] = after_page == "S07_VEHICLE_LIST_PAGE" and not after_slider.get("found")
    result["s08_age_exact_value_selected"] = validate_exact_age_range(
        result["left_handle_value_after"],
        result["right_handle_value_after"],
        target_age,
        left_handle_bounds=after_dual.get("left_handle_actual_bounds") if isinstance(after_dual.get("left_handle_actual_bounds"), list) else None,
        right_handle_bounds=after_dual.get("right_handle_actual_bounds") if isinstance(after_dual.get("right_handle_actual_bounds"), list) else None,
        require_physical_overlap=True,
        target_age_calculation_verified=True,
    )

    if result["entered_vehicle_list"]:
        issue = issues.classify_and_record(
            fallback_code="WRONG_PAGE_AFTER_AGE_SLIDER_SET",
            state_id="S08_AGE_EXACT_SLIDER_PANEL",
            message="Age slider correction unexpectedly entered a vehicle list/detail or unsafe page.",
            context={"after_page": after_page, "after_slider": after_slider, "screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]},
            current_state="S08_AGE_EXACT_SLIDER_PANEL",
            intended_action="set_age_slider_exact_value",
            expected_next_state="S08_AGE_EXACT_VALUE_SELECTED",
            actual_next_state=after_page,
            actual_clicked_target={"text": str(target_age), "role": "right_age_handle", "bounds": result["right_handle_bounds"]},
            before_xml=str(before["xml_text"]),
            after_xml=str(after["xml_text"]),
            action_contract=configs["actions"]["actions"].get("set_age_slider_exact_value"),
            task_context={**params, "target_age": target_age},
            resolution="stop_without_collection",
        )
        return stop_with_issue(issue)

    if not result["s08_age_exact_value_selected"]:
        if not (
            normalize_age_handle_value(result["left_handle_value_after"]) == target_age
            and normalize_age_handle_value(result["right_handle_value_after"]) == target_age
            and result["handle_physical_overlap_after"] is True
        ):
            result["set_non_target_age"] = True
        issue = issues.record(
            "RIGHT_AGE_HANDLE_SET_NO_VERIFICATION",
            "S08_AGE_EXACT_SLIDER_PANEL",
            "Right-handle correction did not verify right_handle_value == target_age, physical handle overlap, and target-age calculation.",
            {
                "after_page": after_page,
                "after_state": after_state,
                "after_slider": after_slider,
                "left_handle_value_after": result["left_handle_value_after"],
                "right_handle_value_after": result["right_handle_value_after"],
                "left_handle_actual_bounds_after": result["left_handle_actual_bounds_after"],
                "right_handle_actual_bounds_after": result["right_handle_actual_bounds_after"],
                "handle_physical_overlap_after": result["handle_physical_overlap_after"],
                "actual_clicked_target": {"text": str(target_age), "role": "right_age_handle", "bounds": result["right_handle_bounds"]},
                "screenshot_path": after["screenshot_path"],
                "xml_path": after["xml_path"],
            },
            "manual_intervention",
        )
        return stop_with_issue(issue)

    audit.log(
        "age_slider_dual_handle_set_verified",
        from_state="S08_AGE_LEFT_HANDLE_SET_ONLY",
        to_state="S08_AGE_EXACT_VALUE_SELECTED",
        action_id="validate_exact_age_range",
        target_age=target_age,
        slider_bounds=result["slider_bounds"],
        track_bounds=result["track_bounds"],
        tick_values=result["visible_ticks"],
        left_handle_bounds=result["left_handle_bounds"],
        right_handle_bounds=result["right_handle_bounds"],
        target_tick=target_age,
        target_tick_coordinate=result["target_tick_coordinate"],
        target_coordinate=result["target_coordinate"],
        right_target_coordinate=right_target_coordinate,
        coordinate_source=result["coordinate_source"],
        left_handle_value_before=result["left_handle_value_before"],
        right_handle_value_before=result["right_handle_value_before"],
        left_handle_value_after=result["left_handle_value_after"],
        right_handle_value_after=result["right_handle_value_after"],
        requires_vehicle_year_secondary_check=result["requires_vehicle_year_secondary_check"],
        entered_vehicle_list=False,
        collected_vehicle_source_fields=False,
    )
    result["audit_logged"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
