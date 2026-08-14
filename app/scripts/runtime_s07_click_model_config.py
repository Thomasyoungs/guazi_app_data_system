"""Controlled S07 click on the explicit vehicle model-config entry.

This script may click exactly one page element: the explicit S07
"vehicle model config" entry. It then captures screenshot/XML, recognizes the
resulting model-config page or panel, and stops. It never clicks filters,
colors, years, sorting, vehicle cards, details, or collects vehicle fields.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


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
from runtime_s07_detect_model_config import combined_page_result, find_vehicle_model_config_entry


MODEL_CONFIG_TOKENS = {
    "车型配置",
    "杞﹀瀷閰嶇疆",
    "重置",
    "閲嶇疆",
    "确定",
    "纭畾",
    "查看",
    "鏌ョ湅",
    "品牌",
    "鍝佺墝",
    "车系",
    "杞︾郴",
    "年款",
    "骞存",
    "配置",
    "閰嶇疆",
    "已选",
    "宸查€?",
}

DETAIL_TOKENS = {
    "联系卖家",
    "鑱旂郴鍗栧",
    "微信咨询",
    "寰俊鍜ㄨ",
    "查看完整报告",
    "鏌ョ湅瀹屾暣鎶ュ憡",
    "立即订购",
    "绔嬪嵆璁㈣喘",
}


def label_blob(snapshot: dict[str, object]) -> str:
    labels = snapshot.get("labels") or []
    return "".join(str(label) for label in labels)


def classify_after_model_config_click(snapshot: dict[str, object], params: dict[str, object]) -> str:
    blob = label_blob(snapshot)
    root_package = str(snapshot.get("root_package") or "")
    foreground_package = str(snapshot.get("foreground_package") or "")
    if root_package == "com.android.systemui" or foreground_package == "com.android.systemui":
        return "SystemUI"
    if foreground_package != "com.ganji.android.haoche_c":
        return "UNKNOWN_PAGE"
    if any(token in blob for token in DETAIL_TOKENS):
        return "VEHICLE_DETAIL_PAGE"
    has_model_config_title = "车型配置" in blob or "杞﹀瀷閰嶇疆" in blob
    supporting_tokens = [token for token in MODEL_CONFIG_TOKENS if token in blob and token not in {"车型配置", "杞﹀瀷閰嶇疆"}]
    task_markers = [
        str(params.get("brand") or ""),
        str(params.get("series") or ""),
        str(params.get("model_year") or ""),
        str(params.get("trim") or ""),
    ]
    task_related_seen = any(marker and marker in blob for marker in task_markers)
    if has_model_config_title and (supporting_tokens or task_related_seen):
        return "S08_VEHICLE_MODEL_CONFIG_PANEL"
    return combined_page_result(
        snapshot,
        str(params.get("brand") or ""),
        str(params.get("series") or ""),
        str(params.get("model_year") or ""),
        str(params.get("trim") or ""),
    )


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
        "current_page_is_s07": False,
        "model_config_entry_found": False,
        "model_config_entry_bounds": None,
        "actual_click_target": None,
        "actual_click_target_is_vehicle_model_config": False,
        "clicked_vehicle_model_config_once": False,
        "pre_click_artifacts": {},
        "post_click_artifacts": {},
        "after_page_result": None,
        "output_model_config_state": None,
        "clicked_generic_filter": False,
        "clicked_color_or_year": False,
        "clicked_sort": False,
        "clicked_vehicle_card": False,
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
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S07_VEHICLE_LIST_PAGE", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S07_VEHICLE_LIST_PAGE", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S07_VEHICLE_LIST_PAGE", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

    client = AdbClient()
    gate = run_adb_device_gate(client, issues=issues, audit=audit, allow_transient_recovery=True)
    result["device_gate"] = gate
    result["adb_devices_l"] = gate.get("adb_devices_l_second") or gate.get("adb_devices_l_first")
    if not gate.get("passed"):
        result["adb_status"] = gate.get("status")
        issue_records = gate.get("issue_records") or []
        issue = issue_records[-1] if issue_records else issues.record("DEVICE_NOT_FOUND", "DEVICE", "Device gate failed before S07 model-config click.", gate, "manual_intervention")
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
            "System overlay / keyguard / NotificationShade blocks S07 model-config click.",
            {"runtime_state": state},
            "manual_unlock_required",
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks S07 model-config click.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)
    if state.get("foreground_package") != "com.ganji.android.haoche_c":
        result["foreground_app_is_guazi"] = False
        issue = issues.record(
            "PAGE_CONTRACT_MISMATCH",
            "S07_VEHICLE_LIST_PAGE",
            "Foreground APP is not verified Guazi before S07 model-config click.",
            {"runtime_state": state},
            "manual_intervention",
        )
        return stop_with_issue(issue)
    result["foreground_app_is_guazi"] = True

    before = capture(
        client,
        "s07_before_model_config_click",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        "com.ganji.android.haoche_c",
    )
    before_page = combined_page_result(
        before,
        str(result["target_brand"]),
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
    )
    result["pre_click_artifacts"] = {"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]}
    result["current_page_result"] = before_page
    result["current_page_is_s07"] = before_page == "S07_VEHICLE_LIST_PAGE"
    entry = find_vehicle_model_config_entry(before["nodes"])  # type: ignore[arg-type]
    if entry:
        result["model_config_entry_found"] = True
        result["model_config_entry_bounds"] = entry["bounds"]
        result["model_config_entry_region"] = entry["region"]
        result["actual_click_target"] = "车型配置"
        result["actual_click_target_raw"] = entry["text"]
        result["actual_click_target_is_vehicle_model_config"] = True

    if not result["current_page_is_s07"]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not entry:
        issue = issues.classify_and_record(
            fallback_code="VEHICLE_MODEL_CONFIG_ENTRY_NOT_FOUND",
            state_id="S07_VEHICLE_LIST_PAGE",
            message="S07 was recognized, but the explicit vehicle model-config entry was not found.",
            context={"screenshot_path": before["screenshot_path"], "xml_path": before["xml_path"]},
            current_state="S07_VEHICLE_LIST_PAGE",
            intended_action="click_vehicle_model_config_entry",
            expected_next_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
            actual_next_state=before_page,
            actual_clicked_target={"text": None, "role": None},
            before_xml=str(before["xml_text"]),
            after_xml=str(before["xml_text"]),
            action_contract=configs["actions"]["actions"].get("click_vehicle_model_config_entry"),
            resolution="manual_intervention",
        )
        return stop_with_issue(issue)

    audit.log(
        "vehicle_model_config_entry_click_requested",
        state="S07_VEHICLE_LIST_PAGE",
        action_id="click_vehicle_model_config_entry",
        actual_click_target="车型配置",
        actual_click_target_raw=entry["text"],
        bounds=entry["bounds"],
        region=entry["region"],
        clicked_generic_filter=False,
        clicked_color_or_year=False,
        clicked_sort=False,
        clicked_vehicle_card=False,
        collected_vehicle_data=False,
    )
    result["audit_logged"] = True
    success = tap_node(client, entry)
    result["clicked_vehicle_model_config_once"] = bool(success)
    sleep_after(2.5)

    state = get_runtime_state(client)
    after = capture(
        client,
        "s07_after_model_config_click",
        str(result["target_series"]),
        str(result["target_model_year"]),
        str(result["target_trim"]),
        str(state.get("foreground_package") or ""),
    )
    after_page = classify_after_model_config_click(after, params)
    result["post_click_artifacts"] = {"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"]}
    result["after_page_result"] = after_page
    result["entered_vehicle_detail"] = after_page == "VEHICLE_DETAIL_PAGE"
    if after_page in {"S08_VEHICLE_MODEL_CONFIG_PAGE", "S08_VEHICLE_MODEL_CONFIG_PANEL"}:
        result["output_model_config_state"] = after_page
        audit.log(
            "vehicle_model_config_entry_click_verified",
            from_state="S07_VEHICLE_LIST_PAGE",
            to_state=after_page,
            action_id="click_vehicle_model_config_entry",
            clicked_vehicle_model_config_once=True,
            clicked_generic_filter=False,
            clicked_color_or_year=False,
            clicked_sort=False,
            clicked_vehicle_card=False,
            entered_vehicle_detail=False,
            collected_vehicle_data=False,
        )
        result["audit_logged"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    fallback = "UNEXPECTED_DETAIL_PAGE" if after_page == "VEHICLE_DETAIL_PAGE" else "VEHICLE_MODEL_CONFIG_CLICK_NO_NAVIGATION"
    issue = issues.classify_and_record(
        fallback_code=fallback,
        state_id="S07_VEHICLE_LIST_PAGE",
        message="Vehicle model-config entry click did not verify the expected model-config page or panel.",
        context={"screenshot_path": after["screenshot_path"], "xml_path": after["xml_path"], "page": after_page},
        current_state="S07_VEHICLE_LIST_PAGE",
        intended_action="click_vehicle_model_config_entry",
        expected_next_state="S08_VEHICLE_MODEL_CONFIG_PANEL",
        actual_next_state=after_page,
        actual_clicked_target={"text": "车型配置", "role": "vehicle_model_config_entry", "bounds": entry["bounds"]},
        before_xml=str(before["xml_text"]),
        after_xml=str(after["xml_text"]),
        action_contract=configs["actions"]["actions"].get("click_vehicle_model_config_entry"),
        resolution="manual_intervention",
    )
    return stop_with_issue(issue)


if __name__ == "__main__":
    raise SystemExit(main())
