"""Controlled S05 exact-trim selection.

The script starts from S05_MODEL_YEAR_SELECTED, searches only the right-side
configuration list for the exact target trim, clicks it once if found, then
stops after verifying S05_TRIM_SELECTED. It never clicks model years, similar
trims, confirm, or vehicle-list content.
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
from guazi_app_data_system.trim_normalizer import (
    emission_normalization_used,
    exact_trim_match_with_emission_normalization,
    normalize_trim_for_match,
)
from runtime_recover_to_s04 import adb_device_state, find_popup_close, get_runtime_state, sleep_after, tap_node
from runtime_s04_to_s05 import capture, looks_like_vehicle_list_or_detail
from runtime_s05_select_model_year import configuration_list_visible, target_model_year_selected, year_list_visible


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


def expected_app_trim_forms(target_trim: str, model_year: str) -> list[str]:
    """Accepted exact forms for APP rows.

    The APP may render configuration rows as either the trim alone or as
    ``{model_year} {trim}``. The year is not ignored: when present, it must
    exactly match the verified task model_year.
    """
    forms = [target_trim]
    if model_year:
        forms.append(f"{model_year} {target_trim}")
    return list(dict.fromkeys(forms))


def trim_match_metadata(task_trim: str, app_trim: str, model_year: str) -> dict[str, object]:
    app_raw = first_line(app_trim)
    task_forms = expected_app_trim_forms(task_trim, model_year)
    for task_form in task_forms:
        task_normalized = normalize_trim_for_match(task_form)
        app_normalized = normalize_trim_for_match(app_raw)
        if exact_trim_match_with_emission_normalization(task_form, app_raw):
            emission_used = emission_normalization_used(task_form, app_raw)
            return {
                "matched": True,
                "match_status": "emission_normalized_exact_match" if emission_used else "exact_match",
                "task_trim_raw": task_trim,
                "task_trim_expected_raw": task_form,
                "app_trim_raw": app_raw,
                "task_trim_normalized": task_normalized,
                "app_trim_normalized": app_normalized,
                "normalization_used": emission_used,
                "emission_normalization_used": emission_used,
                "alias_used": False,
                "alias_id": None,
            }
    return {
        "matched": False,
        "match_status": "no_match",
        "task_trim_raw": task_trim,
        "app_trim_raw": app_raw,
        "task_trim_normalized": normalize_trim_for_match(task_trim),
        "app_trim_normalized": normalize_trim_for_match(app_raw),
        "normalization_used": False,
        "emission_normalization_used": False,
        "alias_used": False,
        "alias_id": None,
    }


def find_exact_trim_node(nodes: list[dict[str, object]], target_trim: str, model_year: str) -> dict[str, object] | None:
    for node in nodes:
        bounds = node.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            continue
        # Right-side configuration list only. This keeps the year list untouched.
        if int(bounds[0]) < 260:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            text = first_line(str(label))
            metadata = trim_match_metadata(target_trim, text, model_year)
            if metadata["matched"] is True:
                return {**node, "trim_match": metadata}
    return None


def similar_trim_candidates(nodes: list[dict[str, object]], target_trim: str, model_year: str) -> list[str]:
    target_tokens = [token for token in re.split(r"\s+", target_trim) if len(token) >= 3]
    candidates: list[str] = []
    for node in nodes:
        bounds = node.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4 or int(bounds[0]) < 260:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            text = first_line(str(label))
            metadata = trim_match_metadata(target_trim, text, model_year)
            normalized = str(metadata["app_trim_normalized"])
            if metadata["matched"] is True:
                continue
            if any(token in normalized for token in target_tokens):
                if text not in candidates:
                    candidates.append(text)
    return candidates


def visible_trim_candidates(nodes: list[dict[str, object]]) -> list[str]:
    candidates: list[str] = []
    for node in nodes:
        bounds = node.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4 or int(bounds[0]) < 260:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            text = first_line(str(label))
            if text and text not in candidates:
                candidates.append(text)
    return candidates


def confirm_button_visible(nodes: list[dict[str, object]]) -> bool:
    for node in nodes:
        for label in node.get("labels", []):  # type: ignore[assignment]
            if first_line(str(label)) == "确定":
                return True
    return False


def target_trim_selected(nodes: list[dict[str, object]], target_trim: str, model_year: str) -> bool:
    exact = find_exact_trim_node(nodes, target_trim, model_year)
    if exact and exact.get("selected") is True:
        return True
    labels = node_labels(nodes)
    return any("已选1项" in label for label in labels) and confirm_button_visible(nodes)


def scroll_configuration_list(client: AdbClient) -> bool:
    result = client.run(["shell", "input", "swipe", "850", "2140", "850", "690", "450"], timeout=20)
    return result.success


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
        "current_page_is_s05": False,
        "s05_model_year_selected": False,
        "target_model_year_selected": False,
        "configuration_list_visible": False,
        "target_trim_found": False,
        "target_trim_bounds": None,
        "visible_trim_candidates": [],
        "match_status": None,
        "task_trim_raw": None,
        "app_trim_raw": None,
        "task_trim_normalized": None,
        "app_trim_normalized": None,
        "normalization_used": False,
        "emission_normalization_used": False,
        "alias_used": False,
        "alias_id": None,
        "allow_click": False,
        "clicked_target_trim_once": False,
        "pre_trim_click_artifacts": {},
        "post_trim_click_artifacts": {},
        "after_page_result": None,
        "s05_trim_selected": False,
        "confirm_button_visible": False,
        "clicked_similar_trim": False,
        "clicked_model_year": False,
        "clicked_confirm": False,
        "entered_vehicle_list": False,
        "collected_vehicle_data": False,
        "modified_pricing_formula": False,
        "audit_logged": False,
        "issue_logged": None,
        "emission_variant_issue_logged": None,
        "scroll_attempts": 0,
        "step_artifacts": {},
        "similar_trim_candidates": [],
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
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S05_MODEL_YEAR_SELECTED", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S05_MODEL_YEAR_SELECTED", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S05_MODEL_YEAR_SELECTED", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

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
        snap = capture(client, "s05_trim_gate_blocked", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
        result["pre_trim_click_artifacts"] = {"screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"]}
        issue = issues.record(
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "System overlay / keyguard / NotificationShade blocks trim-selection gate verification.",
            {"runtime_state": state, "screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"], "page": snap["page"]},
            "manual_unlock_required",
            recognized_text=" ".join(snap["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        snap = capture(client, "s05_trim_third_party_overlay_blocked", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
        result["pre_trim_click_artifacts"] = {"screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"]}
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks the trim-selection gate verification.",
            {"runtime_state": state, "screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"], "page": snap["page"]},
            "manual_intervention",
            recognized_text=" ".join(snap["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)

    before = capture(client, "s05_before_trim_click", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
    result["initial_page_result"] = before["page"]
    result["current_page_is_s05"] = before["page"] == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"
    result["pre_trim_click_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}

    if before["page"] == "POPUP_MARKETING_OVERLAY":
        close_node = find_popup_close(before["nodes"])  # type: ignore[arg-type]
        if close_node and tap_node(client, close_node):
            audit.log("popup_marketing_overlay_close_clicked", bounds=close_node["bounds"], from_page=before["page"])
            result["audit_logged"] = True
            sleep_after(2.5)
            state = get_runtime_state(client)
            before = capture(client, "s05_trim_after_popup_close", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
            result["initial_page_result"] = before["page"]
            result["current_page_is_s05"] = before["page"] == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"
            result["pre_trim_click_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}
        else:
            return stop_with_issue(issues.record("POPUP_MARKETING_OVERLAY", "S05_MODEL_YEAR_SELECTED", "Marketing popup detected but explicit close button was not safely clickable.", {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}, "manual_intervention", recognized_text=" ".join(before["labels"])))  # type: ignore[arg-type]
    elif before["page"] == "POPUP_UNCONTRACTED":
        return stop_with_issue(issues.record("POPUP_UNCONTRACTED", "S05_MODEL_YEAR_SELECTED", "Blocking non-marketing popup detected before trim click.", {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "page": before["page"]}, "manual_intervention", recognized_text=" ".join(before["labels"])))  # type: ignore[arg-type]

    if before["page"] != "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED":
        issue = issues.classify_and_record(
            fallback_code="PAGE_CONTRACT_MISMATCH",
            state_id="S05_MODEL_YEAR_SELECTED",
            message="Current page is not the verified S05 model/year/trim page.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "page": before["page"]},
            current_state="S05_MODEL_YEAR_SELECTED",
            intended_action="tap_exact_trim",
            expected_next_state="S05_TRIM_SELECTED",
            actual_next_state=str(before["page"]),
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("tap_exact_trim"),
            task_context={"trim": result["target_trim"]},
            resolution="manual_intervention",
            recognized_text=" ".join(before["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)

    result["target_model_year_selected"] = target_model_year_selected(before["nodes"], str(result["target_model_year"]), str(result["target_trim"]))  # type: ignore[arg-type]
    result["s05_model_year_selected"] = bool(result["target_model_year_selected"])
    result["configuration_list_visible"] = configuration_list_visible(before["nodes"], str(result["target_model_year"]), str(result["target_trim"]))  # type: ignore[arg-type]
    result["year_list_visible"] = year_list_visible(before["nodes"])  # type: ignore[arg-type]
    if looks_like_vehicle_list_or_detail(before["labels"]):  # type: ignore[arg-type]
        return stop_with_issue(issues.record("PAGE_CONTRACT_MISMATCH", "S05_MODEL_YEAR_SELECTED", "S05 gate blocked because vehicle-list/detail markers are already visible.", {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}, "manual_intervention", recognized_text=" ".join(before["labels"])))  # type: ignore[arg-type]
    if not result["s05_model_year_selected"]:
        issue = issues.classify_and_record(
            fallback_code="MODEL_YEAR_NOT_SELECTED",
            state_id="S05_MODEL_YEAR_SELECTED",
            message="Target model year is not confirmed selected; trim click is blocked.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "target_model_year": result["target_model_year"]},
            current_state="S05_MODEL_YEAR_SELECTED",
            intended_action="tap_exact_trim",
            expected_next_state="S05_TRIM_SELECTED",
            actual_next_state=str(before["page"]),
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("tap_exact_trim"),
            task_context={"trim": result["target_trim"]},
            resolution="manual_intervention",
            recognized_text=" ".join(before["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)

    current = before
    trim_node = None
    similar: list[str] = []
    for attempt in range(5):
        result["visible_trim_candidates"] = visible_trim_candidates(current["nodes"])  # type: ignore[arg-type]
        trim_node = find_exact_trim_node(current["nodes"], str(result["target_trim"]), str(result["target_model_year"]))  # type: ignore[arg-type]
        similar.extend(
            candidate for candidate in similar_trim_candidates(current["nodes"], str(result["target_trim"]), str(result["target_model_year"]))  # type: ignore[arg-type]
            if candidate not in similar
        )
        if trim_node and trim_node.get("bounds"):
            break
        if attempt == 4:
            break
        scroll_configuration_list(client)
        result["scroll_attempts"] = int(result["scroll_attempts"]) + 1
        audit.log("configuration_list_scroll_requested", state="S05_MODEL_YEAR_SELECTED", attempt=attempt + 1, target_trim=result["target_trim"])
        result["audit_logged"] = True
        sleep_after(1.5)
        current = capture(client, f"s05_after_trim_scroll_{attempt + 1}", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), "com.ganji.android.haoche_c")
        result["step_artifacts"][f"s05_after_trim_scroll_{attempt + 1}"] = {"screenshot_path": current["screenshot_path"], "xml_path": current["xml_path"], "page": current["page"]}

    result["similar_trim_candidates"] = similar[:10]
    result["target_trim_found"] = bool(trim_node)
    if trim_node:
        result["target_trim_bounds"] = trim_node.get("bounds")
        match = trim_node.get("trim_match") if isinstance(trim_node.get("trim_match"), dict) else {}
        result["match_status"] = match.get("match_status")
        result["task_trim_raw"] = match.get("task_trim_raw")
        result["app_trim_raw"] = match.get("app_trim_raw")
        result["task_trim_normalized"] = match.get("task_trim_normalized")
        result["app_trim_normalized"] = match.get("app_trim_normalized")
        result["normalization_used"] = bool(match.get("normalization_used"))
        result["emission_normalization_used"] = bool(match.get("emission_normalization_used"))
        result["alias_used"] = bool(match.get("alias_used"))
        result["alias_id"] = match.get("alias_id")
        result["allow_click"] = True

    if not trim_node or not trim_node.get("bounds"):
        issue_code = "TRIM_EXACT_MATCH_REQUIRED" if similar else "TRIM_NOT_FOUND"
        issue = issues.record(
            issue_code,
            "S05_MODEL_YEAR_SELECTED",
            "Exact target trim was not found; similar or partial trim text must not be clicked.",
            {
                "screenshot_path": current["screenshot_path"],
                "xml_path": current["xml_path"],
                "target_trim": result["target_trim"],
                "similar_trim_candidates": similar[:10],
                "scroll_attempts": result["scroll_attempts"],
            },
            "manual_intervention",
            recognized_text=" ".join(current["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)

    if result["emission_normalization_used"]:
        result["emission_variant_issue_logged"] = issues.record(
            "TRIM_EMISSION_STANDARD_VARIANT",
            "S05_MODEL_YEAR_SELECTED",
            "Task trim and APP trim differ only by approved emission-standard spelling variant.",
            {
                "task_trim_raw": result["task_trim_raw"],
                "app_trim_raw": result["app_trim_raw"],
                "task_trim_normalized": result["task_trim_normalized"],
                "app_trim_normalized": result["app_trim_normalized"],
                "match_status": result["match_status"],
                "alias_used": result["alias_used"],
                "alias_id": result["alias_id"],
            },
            "approved_emission_normalization_then_exact_trim_match",
        )

    audit.log(
        "trim_click_requested",
        state="S05_MODEL_YEAR_SELECTED",
        action_id="tap_exact_trim",
        actual_click_target=result["target_trim"],
        task_trim_raw=result["task_trim_raw"],
        app_trim_raw=result["app_trim_raw"],
        task_trim_normalized=result["task_trim_normalized"],
        app_trim_normalized=result["app_trim_normalized"],
        match_status=result["match_status"],
        normalization_used=result["normalization_used"],
        emission_normalization_used=result["emission_normalization_used"],
        alias_used=result["alias_used"],
        alias_id=result["alias_id"],
        clicked_trim_once=False,
        entered_vehicle_list=False,
        collected_vehicle_data=False,
        bounds=trim_node["bounds"],
        forbidden_clicks_performed=[],
    )
    result["audit_logged"] = True
    success = tap_node(client, trim_node)
    result["clicked_target_trim_once"] = bool(success)
    sleep_after(2.5)

    state = get_runtime_state(client)
    after = capture(client, "s05_after_trim_click", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
    result["post_trim_click_artifacts"] = {"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]}
    result["after_page_result"] = after["page"]
    result["entered_vehicle_list"] = after["page"] == "VEHICLE_LIST_OR_DETAIL" or looks_like_vehicle_list_or_detail(after["labels"])  # type: ignore[arg-type]
    result["confirm_button_visible"] = confirm_button_visible(after["nodes"])  # type: ignore[arg-type]
    result["s05_trim_selected"] = (
        after["page"] == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"
        and not result["entered_vehicle_list"]
        and target_trim_selected(after["nodes"], str(result["target_trim"]), str(result["target_model_year"]))  # type: ignore[arg-type]
    )

    if result["entered_vehicle_list"]:
        return stop_with_issue(
            issues.classify_and_record(
                fallback_code="WRONG_PAGE_AFTER_TRIM_CLICK",
                state_id="S05_MODEL_YEAR_SELECTED",
                message="Target trim click reached vehicle list/detail markers.",
                context={"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"], "page": after["page"]},
                current_state="S05_MODEL_YEAR_SELECTED",
                intended_action="tap_exact_trim",
                expected_next_state="S05_TRIM_SELECTED",
                actual_next_state="VEHICLE_LIST_OR_DETAIL",
                actual_clicked_target={"text": result["target_trim"], "role": "trim", "bounds": trim_node["bounds"]},
                before_xml=str(current["xml_text"]),
                after_xml=str(after["xml_text"]),
                page_contract=None,
                action_contract=configs["actions"]["actions"].get("tap_exact_trim"),
                task_context={"trim": result["target_trim"]},
                resolution="manual_intervention",
                recognized_text=" ".join(after["labels"]),  # type: ignore[arg-type]
            )
        )

    if result["s05_trim_selected"]:
        audit.log(
            "trim_click_verified",
            from_state="S05_MODEL_YEAR_SELECTED",
            to_state="S05_TRIM_SELECTED",
            action_id="tap_exact_trim",
            actual_click_target=result["target_trim"],
            task_trim_raw=result["task_trim_raw"],
            app_trim_raw=result["app_trim_raw"],
            task_trim_normalized=result["task_trim_normalized"],
            app_trim_normalized=result["app_trim_normalized"],
            match_status=result["match_status"],
            normalization_used=result["normalization_used"],
            emission_normalization_used=result["emission_normalization_used"],
            alias_used=result["alias_used"],
            alias_id=result["alias_id"],
            clicked_trim_once=True,
            confirm_button_visible=result["confirm_button_visible"],
            clicked_confirm=False,
            entered_vehicle_list=False,
            collected_vehicle_data=False,
        )
        result["audit_logged"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    return stop_with_issue(
        issues.classify_and_record(
            fallback_code="TRIM_CLICK_NO_SELECTION",
            state_id="S05_MODEL_YEAR_SELECTED",
            message="Exact target trim click did not verify selected state.",
            context={"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"], "page": after["page"], "target_trim": result["target_trim"]},
            current_state="S05_MODEL_YEAR_SELECTED",
            intended_action="tap_exact_trim",
            expected_next_state="S05_TRIM_SELECTED",
            actual_next_state=str(after["page"]),
            actual_clicked_target={"text": result["target_trim"], "role": "trim", "bounds": trim_node["bounds"]},
            before_xml=str(current["xml_text"]),
            after_xml=str(after["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("tap_exact_trim"),
            task_context={"trim": result["target_trim"]},
            resolution="manual_intervention",
            recognized_text=" ".join(after["labels"]),  # type: ignore[arg-type]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
