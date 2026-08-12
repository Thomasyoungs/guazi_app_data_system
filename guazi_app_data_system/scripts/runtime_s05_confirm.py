"""Controlled S05 confirmation click and read-only next-page recognition.

Starts only from S05_TRIM_SELECTED, clicks the explicit confirm button once,
captures screenshot/XML, classifies the next page, then stops. It never clicks
filters, sorting controls, vehicle cards, details, or collects vehicle data.
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
from runtime_s04_to_s05 import capture, looks_like_vehicle_list_or_detail
from runtime_s05_select_model_year import target_model_year_selected
from runtime_s05_select_trim import confirm_button_visible, first_line, target_trim_selected


def node_labels(nodes: list[dict[str, object]]) -> list[str]:
    labels: list[str] = []
    for node in nodes:
        for label in node.get("labels", []):  # type: ignore[assignment]
            text = str(label)
            if text and text not in labels:
                labels.append(text)
    return labels


def find_confirm_node(nodes: list[dict[str, object]]) -> dict[str, object] | None:
    for node in nodes:
        bounds = node.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            continue
        for label in node.get("labels", []):  # type: ignore[assignment]
            if first_line(str(label)) == "确定":
                return node
    return None


def looks_like_vehicle_detail(labels: list[str]) -> bool:
    blob = "".join(labels)
    detail_markers = ["联系卖家", "微信咨询", "查看完整报告", "收藏", "立即订购", "讲价"]
    return any(marker in blob for marker in detail_markers)


def looks_like_vehicle_list(labels: list[str]) -> bool:
    blob = "".join(labels)
    if "综合排序" in blob or "价格从低到高" in blob:
        return True
    if "上牌" in blob and ("万公里" in blob or "公里" in blob):
        return True
    if re.search(r"\d+(?:\.\d+)?万公里", blob) and re.search(r"\d{4}年", blob):
        return True
    return False


def looks_like_filter_or_result_entry(labels: list[str]) -> bool:
    blob = "".join(labels)
    markers = ["筛选", "更多筛选", "车型配置", "颜色", "年份", "车龄", "价格", "车源", "查看"]
    return any(marker in blob for marker in markers)


def classify_after_confirm(snapshot: dict[str, object], target_model_year: str, target_trim: str) -> str:
    labels = snapshot["labels"]  # type: ignore[assignment]
    nodes = snapshot["nodes"]  # type: ignore[assignment]
    root_package = str(snapshot.get("root_package") or "")
    fg_package = str(snapshot.get("foreground_package") or "")

    if root_package == "com.android.systemui" or fg_package == "com.android.systemui":
        return "SystemUI"
    if fg_package.endswith(".launcher") or "launcher" in fg_package.lower() or root_package.endswith(".launcher"):
        return "Launcher"
    if fg_package != "com.ganji.android.haoche_c":
        return "未知页"

    if looks_like_vehicle_detail(labels):  # type: ignore[arg-type]
        return "VEHICLE_DETAIL_PAGE"
    if looks_like_vehicle_list(labels):  # type: ignore[arg-type]
        return "S07_VEHICLE_LIST_PAGE"
    if confirm_button_visible(nodes) and target_model_year_selected(nodes, target_model_year, target_trim) and target_trim_selected(nodes, target_trim, target_model_year):  # type: ignore[arg-type]
        return "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"
    if looks_like_filter_or_result_entry(labels):  # type: ignore[arg-type]
        return "S06_FILTER_OR_RESULT_ENTRY_PAGE"
    return "未知页"


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
        "s05_trim_selected": False,
        "target_model_year_selected": False,
        "target_trim_selected": False,
        "confirm_button_visible": False,
        "confirm_button_bounds": None,
        "clicked_confirm_once": False,
        "pre_confirm_artifacts": {},
        "post_confirm_artifacts": {},
        "after_page_result": None,
        "output_next_page": None,
        "s07_read_only_recognition": False,
        "clicked_filter_color_year": False,
        "clicked_sort_or_comprehensive_sort": False,
        "clicked_price_low_to_high": False,
        "entered_vehicle_detail": False,
        "collected_vehicle_data": False,
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
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S05_TRIM_SELECTED", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S05_TRIM_SELECTED", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S05_TRIM_SELECTED", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

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
        snap = capture(client, "s05_confirm_gate_blocked", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
        result["pre_confirm_artifacts"] = {"screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"]}
        issue = issues.record(
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "System overlay / keyguard / NotificationShade blocks S05 confirm gate verification.",
            {"runtime_state": state, "screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"], "page": snap["page"]},
            "manual_unlock_required",
            recognized_text=" ".join(snap["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        snap = capture(client, "s05_confirm_third_party_overlay_blocked", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
        result["pre_confirm_artifacts"] = {"screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"]}
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks the S05 confirm gate verification.",
            {"runtime_state": state, "screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"], "page": snap["page"]},
            "manual_intervention",
            recognized_text=" ".join(snap["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)

    before = capture(client, "s05_before_confirm_click", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
    result["pre_confirm_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}
    result["initial_page_result"] = before["page"]
    result["current_page_is_s05"] = before["page"] == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"

    if before["page"] == "POPUP_MARKETING_OVERLAY":
        close_node = find_popup_close(before["nodes"])  # type: ignore[arg-type]
        if close_node and tap_node(client, close_node):
            audit.log("popup_marketing_overlay_close_clicked", bounds=close_node["bounds"], from_page=before["page"])
            result["audit_logged"] = True
            sleep_after(2.5)
            state = get_runtime_state(client)
            before = capture(client, "s05_confirm_after_popup_close", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
            result["pre_confirm_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}
            result["initial_page_result"] = before["page"]
            result["current_page_is_s05"] = before["page"] == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"
        else:
            return stop_with_issue(issues.record("POPUP_MARKETING_OVERLAY", "S05_TRIM_SELECTED", "Marketing popup detected but explicit close button was not safely clickable.", {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}, "manual_intervention", recognized_text=" ".join(before["labels"])))  # type: ignore[arg-type]
    elif before["page"] == "POPUP_UNCONTRACTED":
        return stop_with_issue(issues.record("POPUP_UNCONTRACTED", "S05_TRIM_SELECTED", "Blocking non-marketing popup detected before confirm click.", {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "page": before["page"]}, "manual_intervention", recognized_text=" ".join(before["labels"])))  # type: ignore[arg-type]

    if before["page"] != "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED":
        issue = issues.classify_and_record(
            fallback_code="PAGE_CONTRACT_MISMATCH",
            state_id="S05_TRIM_SELECTED",
            message="Current page is not the verified S05 model/year/trim page before confirm click.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "page": before["page"]},
            current_state="S05_TRIM_SELECTED",
            intended_action="tap_green_confirm",
            expected_next_state="S06_FILTER_OR_RESULT_ENTRY_PAGE_OR_S07_VEHICLE_LIST_PAGE",
            actual_next_state=str(before["page"]),
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("tap_green_confirm"),
            task_context={"model_year": result["target_model_year"], "trim": result["target_trim"]},
            resolution="manual_intervention",
            recognized_text=" ".join(before["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)
    if looks_like_vehicle_list_or_detail(before["labels"]) or looks_like_vehicle_list(before["labels"]):  # type: ignore[arg-type]
        return stop_with_issue(issues.record("PAGE_CONTRACT_MISMATCH", "S05_TRIM_SELECTED", "S05 confirm gate blocked because vehicle-list/detail markers are already visible.", {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}, "manual_intervention", recognized_text=" ".join(before["labels"])))  # type: ignore[arg-type]

    nodes = before["nodes"]  # type: ignore[assignment]
    result["target_model_year_selected"] = target_model_year_selected(nodes, str(result["target_model_year"]), str(result["target_trim"]))
    result["target_trim_selected"] = target_trim_selected(nodes, str(result["target_trim"]), str(result["target_model_year"]))
    result["s05_trim_selected"] = bool(result["target_model_year_selected"] and result["target_trim_selected"])
    confirm_node = find_confirm_node(nodes)
    result["confirm_button_visible"] = bool(confirm_node)
    if confirm_node:
        result["confirm_button_bounds"] = confirm_node.get("bounds")

    if not result["s05_trim_selected"]:
        issue = issues.classify_and_record(
            fallback_code="PAGE_CONTRACT_MISMATCH",
            state_id="S05_TRIM_SELECTED",
            message="S05 page is visible, but target model year and trim selected state is not confirmed.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"], "target_model_year": result["target_model_year"], "target_trim": result["target_trim"]},
            current_state="S05_TRIM_SELECTED",
            intended_action="tap_green_confirm",
            expected_next_state="S06_FILTER_OR_RESULT_ENTRY_PAGE_OR_S07_VEHICLE_LIST_PAGE",
            actual_next_state=str(before["page"]),
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("tap_green_confirm"),
            task_context={"model_year": result["target_model_year"], "trim": result["target_trim"]},
            resolution="manual_intervention",
            recognized_text=" ".join(before["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)
    if not confirm_node or not confirm_node.get("bounds"):
        issue = issues.classify_and_record(
            fallback_code="CONFIRM_BUTTON_NOT_FOUND",
            state_id="S05_TRIM_SELECTED",
            message="Target trim is selected but explicit confirm button was not found.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]},
            current_state="S05_TRIM_SELECTED",
            intended_action="tap_green_confirm",
            expected_next_state="S06_FILTER_OR_RESULT_ENTRY_PAGE_OR_S07_VEHICLE_LIST_PAGE",
            actual_next_state=str(before["page"]),
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("tap_green_confirm"),
            task_context={"model_year": result["target_model_year"], "trim": result["target_trim"]},
            resolution="manual_intervention",
            recognized_text=" ".join(before["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)

    audit.log(
        "confirm_click_requested",
        state="S05_TRIM_SELECTED",
        action_id="tap_green_confirm",
        actual_click_target="确定",
        target_model_year=result["target_model_year"],
        target_trim=result["target_trim"],
        bounds=confirm_node["bounds"],
        forbidden_clicks_performed=[],
        clicked_filter_color_year=False,
        clicked_sort_or_comprehensive_sort=False,
        clicked_price_low_to_high=False,
        entered_vehicle_detail=False,
        collected_vehicle_data=False,
    )
    result["audit_logged"] = True
    success = tap_node(client, confirm_node)
    result["clicked_confirm_once"] = bool(success)
    sleep_after(3.0)

    state = get_runtime_state(client)
    after = capture(client, "s05_after_confirm_click", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
    after_result = classify_after_confirm(after, str(result["target_model_year"]), str(result["target_trim"]))
    result["post_confirm_artifacts"] = {"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]}
    result["after_page_result"] = after_result
    result["output_next_page"] = after_result if after_result in {"S06_FILTER_OR_RESULT_ENTRY_PAGE", "S07_VEHICLE_LIST_PAGE"} else None
    result["s07_read_only_recognition"] = after_result == "S07_VEHICLE_LIST_PAGE"
    result["entered_vehicle_detail"] = after_result == "VEHICLE_DETAIL_PAGE"

    if after_result in {"S06_FILTER_OR_RESULT_ENTRY_PAGE", "S07_VEHICLE_LIST_PAGE"}:
        audit.log(
            "confirm_click_verified",
            from_state="S05_TRIM_SELECTED",
            to_state=after_result,
            action_id="tap_green_confirm",
            actual_click_target="确定",
            clicked_confirm_once=True,
            clicked_filter_color_year=False,
            clicked_sort_or_comprehensive_sort=False,
            clicked_price_low_to_high=False,
            entered_vehicle_detail=False,
            entered_vehicle_list=(after_result == "S07_VEHICLE_LIST_PAGE"),
            s07_read_only_recognition=(after_result == "S07_VEHICLE_LIST_PAGE"),
            collected_vehicle_data=False,
        )
        result["audit_logged"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if after_result == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED":
        issue = issues.classify_and_record(
            fallback_code="CONFIRM_CLICK_NO_NAVIGATION",
            state_id="S05_TRIM_SELECTED",
            message="Confirm button was clicked once but page still appears to be S05.",
            context={"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"], "page": after_result},
            current_state="S05_TRIM_SELECTED",
            intended_action="tap_green_confirm",
            expected_next_state="S06_FILTER_OR_RESULT_ENTRY_PAGE_OR_S07_VEHICLE_LIST_PAGE",
            actual_next_state=after_result,
            actual_clicked_target={"text": "确定", "role": "confirm_button", "bounds": confirm_node["bounds"]},
            before_xml=str(before["xml_text"]),
            after_xml=str(after["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("tap_green_confirm"),
            task_context={"model_year": result["target_model_year"], "trim": result["target_trim"]},
            resolution="manual_intervention",
            recognized_text=" ".join(after["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)

    issue = issues.classify_and_record(
        fallback_code="WRONG_PAGE_AFTER_CONFIRM_CLICK",
        state_id="S05_TRIM_SELECTED",
        message="Confirm button reached an unexpected page after one click.",
        context={"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"], "page": after_result},
        current_state="S05_TRIM_SELECTED",
        intended_action="tap_green_confirm",
        expected_next_state="S06_FILTER_OR_RESULT_ENTRY_PAGE_OR_S07_VEHICLE_LIST_PAGE",
        actual_next_state=after_result,
        actual_clicked_target={"text": "确定", "role": "confirm_button", "bounds": confirm_node["bounds"]},
        before_xml=str(before["xml_text"]),
        after_xml=str(after["xml_text"]),
        page_contract=None,
        action_contract=configs["actions"]["actions"].get("tap_green_confirm"),
        task_context={"model_year": result["target_model_year"], "trim": result["target_trim"]},
        resolution="manual_intervention",
        recognized_text=" ".join(after["labels"]),  # type: ignore[arg-type]
    )
    return stop_with_issue(issue)


if __name__ == "__main__":
    raise SystemExit(main())
