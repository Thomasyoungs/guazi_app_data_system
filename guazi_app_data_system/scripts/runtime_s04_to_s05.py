"""Controlled S04 verification and single right-side model-button click to S05."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree


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
from runtime_recover_to_s04 import (
    adb_device_state,
    all_labels,
    capture as _unused_capture_reference,
    detect_bottom_nav,
    find_contains,
    find_exact,
    find_popup_close,
    get_runtime_state,
    has_select_brand_title,
    looks_like_marketing_popup,
    now_tag,
    parse_nodes,
    parse_bounds,
    sleep_after,
    tap_node,
)


def has_target_series(nodes: list[dict[str, object]], target_series: str) -> bool:
    return find_contains(nodes, target_series) is not None


def find_target_series_node(nodes: list[dict[str, object]], target_series: str) -> dict[str, object] | None:
    exact = find_exact(nodes, target_series)
    if exact:
        return exact
    return find_contains(nodes, target_series)


def _node_label(node: ElementTree.Element) -> str:
    return (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()


def _build_click_node(node: ElementTree.Element) -> dict[str, object] | None:
    bounds = parse_bounds(node.attrib.get("bounds"))
    if not bounds:
        return None
    text = (node.attrib.get("text") or "").strip()
    desc = (node.attrib.get("content-desc") or "").strip()
    labels = [item for item in [text, desc] if item]
    return {
        "text": text,
        "content_desc": desc,
        "labels": labels,
        "bounds": bounds,
        "clickable": node.attrib.get("clickable") == "true",
    }


def find_target_series_model_button(xml_text: str, target_series: str) -> dict[str, object] | None:
    if not xml_text.strip():
        return None
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    for node in root.iter("node"):
        label = _node_label(node)
        if target_series not in label:
            continue
        for descendant in node.iter("node"):
            if descendant is node:
                continue
            child_label = _node_label(descendant)
            if "车型" in child_label:
                click_node = _build_click_node(descendant)
                if click_node:
                    return click_node
    return None


def has_model_year(labels: list[str], target_model_year: str) -> bool:
    if target_model_year and any(target_model_year in label for label in labels):
        return True
    return any(re.search(r"20\d{2}款", label) for label in labels)


def has_trim_region(labels: list[str], target_trim: str) -> bool:
    if target_trim and any(target_trim in label for label in labels):
        return True
    markers = ("车型", "配置", "确定")
    return any(marker in label for label in labels for marker in markers)


def looks_like_vehicle_list_or_detail(labels: list[str]) -> bool:
    markers = [
        "综合排序",
        "价格从低到高",
        "上牌",
        "表显",
        "万公里",
        "联系卖家",
        "微信咨询",
        "查看完整报告",
    ]
    blob = "".join(labels)
    return any(marker in blob for marker in markers)


def classify_page(snapshot: dict[str, object], target_series: str, target_model_year: str, target_trim: str) -> str:
    nodes = snapshot["nodes"]  # type: ignore[assignment]
    labels = snapshot["labels"]  # type: ignore[assignment]
    root_package = str(snapshot.get("root_package") or "")
    fg_package = str(snapshot.get("foreground_package") or "")

    if root_package == "com.android.systemui" or fg_package == "com.android.systemui":
        return "SystemUI"
    if fg_package.endswith(".launcher") or "launcher" in fg_package.lower() or root_package.endswith(".launcher"):
        return "Launcher"
    if fg_package != "com.ganji.android.haoche_c":
        return "未知"
    if has_select_brand_title(nodes):  # type: ignore[arg-type]
        return "S03_BRAND_SELECT_PAGE_VERIFIED"
    if looks_like_marketing_popup(nodes):  # type: ignore[arg-type]
        close_node = find_popup_close(nodes)  # type: ignore[arg-type]
        if close_node:
            return "POPUP_MARKETING_OVERLAY"
    label_blob = "".join(labels)
    if any(keyword in label_blob for keyword in ["登录", "允许", "同意", "隐私", "权限", "更新"]):
        return "POPUP_UNCONTRACTED"
    if looks_like_vehicle_list_or_detail(labels):  # type: ignore[arg-type]
        return "VEHICLE_LIST_OR_DETAIL"
    model_year_seen = has_model_year(labels, target_model_year)  # type: ignore[arg-type]
    trim_region_seen = has_trim_region(labels, target_trim)  # type: ignore[arg-type]
    if model_year_seen and trim_region_seen:
        return "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"
    if has_target_series(nodes, target_series):  # type: ignore[arg-type]
        return "S04_SERIES_LIST_PAGE_VERIFIED"
    return "未知"


def capture(client: AdbClient, name: str, target_series: str, target_model_year: str, target_trim: str, foreground_package: str = "") -> dict[str, object]:
    tag = now_tag()
    shot = ROOT / "artifacts" / "screenshots" / f"{name}_{tag}.png"
    xml_path = ROOT / "artifacts" / "debug" / f"{name}_{tag}.xml"
    shot_result = client.screenshot(shot)
    xml_text = client.dump_ui_xml()
    xml_path.write_text(xml_text, encoding="utf-8")
    nodes = parse_nodes(xml_text)
    labels = all_labels(nodes)
    snapshot: dict[str, object] = {
        "screenshot_path": str(shot) if shot_result.success else None,
        "xml_path": str(xml_path),
        "xml_text": xml_text,
        "nodes": nodes,
        "labels": labels,
        "root_package": nodes[0]["package"] if nodes else "",
        "foreground_package": foreground_package,
    }
    snapshot["page"] = classify_page(snapshot, target_series, target_model_year, target_trim)
    snapshot["bottom_nav_visible"] = detect_bottom_nav(nodes)
    snapshot["has_select_brand_title"] = has_select_brand_title(nodes)
    snapshot["target_series_seen"] = has_target_series(nodes, target_series)
    snapshot["target_series_node"] = find_target_series_node(nodes, target_series)
    snapshot["target_series_model_button"] = find_target_series_model_button(xml_text, target_series)
    snapshot["model_year_seen"] = has_model_year(labels, target_model_year)
    snapshot["trim_region_seen"] = has_trim_region(labels, target_trim)
    snapshot["vehicle_list_markers_seen"] = looks_like_vehicle_list_or_detail(labels)
    return snapshot


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
        "has_select_brand_title": False,
        "target_series_seen": False,
        "target_series_bounds": None,
        "target_series_model_button_bounds": None,
        "clicked_target_series_once": False,
        "clicked_series_model_button_once": False,
        "pre_series_click_artifacts": {},
        "post_series_click_artifacts": {},
        "after_page_result": None,
        "s05_verified": False,
        "model_year_seen": False,
        "trim_region_seen": False,
        "clicked_other_series": False,
        "clicked_target_model_year": False,
        "clicked_target_trim": False,
        "clicked_confirm": False,
        "entered_vehicle_list": False,
        "collected_vehicle_data": False,
        "modified_pricing_formula": False,
        "audit_logged": False,
        "issue_logged": None,
        "verified_app": None,
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
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S04", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S04", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S04", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))
    required_params = ["brand", "series", "model_year", "trim", "color", "vehicle_year"]
    missing_params = [key for key in required_params if params.get(key) in (None, "")]
    if missing_params:
        return stop_with_issue(
            issues.record(
                "TARGET_TASK_GATE_BLOCKED",
                "S04",
                "Current target task is missing required APP operation parameters.",
                {"task_check": check, "missing_params": missing_params},
                "blocked",
            )
        )

    verified_app = None
    for solution in learning.load_solutions():
        if solution.get("issue_code") == "TARGET_APP_VERIFIED" and solution.get("approved") is True:
            app = solution.get("verified_target_app") or {}
            excluded = app.get("excluded_packages") or []
            verified_app = {
                "package_name": app.get("package_name"),
                "app_label": app.get("app_label"),
                "launch_activity": app.get("launch_activity"),
                "excluded_confirmed": any(item.get("package_name") == "com.guazi.android.chesupai" and item.get("excluded") is True for item in excluded),
            }
            break
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
        snap = capture(client, "s04_gate_blocked", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
        result["pre_series_click_artifacts"] = {"screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"]}
        issue = issues.record(
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "System overlay / keyguard / NotificationShade blocks S04 gate verification.",
            {"runtime_state": state, "screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"], "page": snap["page"]},
            "manual_unlock_required",
            recognized_text=" ".join(snap["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)
    if state["third_party_overlay_detected"]:
        snap = capture(client, "s04_third_party_overlay_blocked", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
        result["pre_series_click_artifacts"] = {"screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"]}
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party overlay blocks the S04 gate verification.",
            {"runtime_state": state, "screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"], "page": snap["page"]},
            "manual_intervention",
            recognized_text=" ".join(snap["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)
    if state.get("foreground_package") != "com.ganji.android.haoche_c":
        snap = capture(client, "s04_wrong_foreground", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(state.get("foreground_package") or ""))
        result["pre_series_click_artifacts"] = {"screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"]}
        return stop_with_issue(
            issues.record(
                "PAGE_CONTRACT_MISMATCH",
                "S04",
                "Foreground app is not verified Guazi used-car before series click.",
                {"runtime_state": state, "screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"], "page": snap["page"]},
                "manual_intervention",
                recognized_text=" ".join(snap["labels"]),  # type: ignore[arg-type]
            )
        )

    current_snapshot = capture(client, "s04_before_series_click", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), "com.ganji.android.haoche_c")
    result["current_page_result"] = current_snapshot["page"]
    result["foreground_app_is_guazi"] = True
    result["has_select_brand_title"] = bool(current_snapshot["has_select_brand_title"])
    result["target_series_seen"] = bool(current_snapshot["target_series_seen"])
    if current_snapshot["page"] == "POPUP_MARKETING_OVERLAY":
        close_node = find_popup_close(current_snapshot["nodes"])  # type: ignore[arg-type]
        if close_node and tap_node(client, close_node):
            audit.log("popup_marketing_overlay_close_clicked", bounds=close_node["bounds"], from_page=current_snapshot["page"])
            result["audit_logged"] = True
            sleep_after(2.5)
            current_snapshot = capture(client, "s04_after_popup_close", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), "com.ganji.android.haoche_c")
            result["current_page_result"] = current_snapshot["page"]
            result["has_select_brand_title"] = bool(current_snapshot["has_select_brand_title"])
            result["target_series_seen"] = bool(current_snapshot["target_series_seen"])
        else:
            return stop_with_issue(
                issues.record(
                    "POPUP_MARKETING_OVERLAY",
                    "S04",
                    "Marketing popup detected but explicit close button was not safely clickable.",
                    {"screenshot_path": current_snapshot["screenshot_path"], "xml_path": current_snapshot["xml_path"]},
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
    elif current_snapshot["page"] == "POPUP_UNCONTRACTED":
        return stop_with_issue(
            issues.record(
                "POPUP_UNCONTRACTED",
                "S04",
                "Blocking non-marketing popup detected before series click.",
                {"screenshot_path": current_snapshot["screenshot_path"], "xml_path": current_snapshot["xml_path"], "page": current_snapshot["page"]},
                "manual_intervention",
                recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
            )
        )

    result["pre_series_click_artifacts"] = {
        "screenshot_path": current_snapshot["screenshot_path"],
        "xml_path": current_snapshot["xml_path"],
    }
    if current_snapshot["page"] != "S04_SERIES_LIST_PAGE_VERIFIED":
        return stop_with_issue(
            issues.record(
                "PAGE_CONTRACT_MISMATCH",
                "S04",
                "Current page is not the verified S04 series list page.",
                {"screenshot_path": current_snapshot["screenshot_path"], "xml_path": current_snapshot["xml_path"], "page": current_snapshot["page"]},
                "manual_intervention",
                recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
            )
        )
    if current_snapshot["has_select_brand_title"]:
        return stop_with_issue(
            issues.record(
                "PAGE_CONTRACT_MISMATCH",
                "S04",
                "S04 gate failed because top title still indicates Select Brand.",
                {"screenshot_path": current_snapshot["screenshot_path"], "xml_path": current_snapshot["xml_path"], "page": current_snapshot["page"]},
                "manual_intervention",
                recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
            )
        )

    series_node = current_snapshot["target_series_model_button"]
    for attempt in range(2):
        if series_node and series_node.get("bounds"):
            break
        client.swipe("up")
        audit.log("series_scroll_requested", state="S04", attempt=attempt + 1, target_series=result["target_series"])
        result["audit_logged"] = True
        sleep_after(2.0)
        current_snapshot = capture(client, f"s04_after_series_scroll_{attempt + 1}", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), "com.ganji.android.haoche_c")
        result["step_artifacts"][f"s04_after_series_scroll_{attempt + 1}"] = {
            "screenshot_path": current_snapshot["screenshot_path"],
            "xml_path": current_snapshot["xml_path"],
            "page": current_snapshot["page"],
        }
        series_node = current_snapshot["target_series_model_button"]

    if not series_node or not series_node.get("bounds"):
        return stop_with_issue(
            issues.record(
                "SERIES_MODEL_BUTTON_NOT_FOUND",
                "S04",
                "Target series 帕萨特 was not found on verified S04 page.",
                {"screenshot_path": current_snapshot["screenshot_path"], "xml_path": current_snapshot["xml_path"], "target_series": result["target_series"]},
                "manual_intervention",
                recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
            )
        )

    result["target_series_model_button_bounds"] = series_node["bounds"]
    audit.log(
        "series_model_button_click_requested",
        state="S04",
        target_brand=result["target_brand"],
        target_series=result["target_series"],
        action_id="click_series_model_button",
        actual_click_target="车型",
        actual_click_target_role="series_model_button",
        bounds=series_node["bounds"],
        forbidden_clicks_performed=[],
    )
    result["audit_logged"] = True
    success = tap_node(client, series_node)
    result["clicked_series_model_button_once"] = bool(success)
    sleep_after(3.0)

    after_state = get_runtime_state(client)
    after_snapshot = capture(client, "s04_to_s05_after_series_click", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(after_state.get("foreground_package") or ""))
    if after_snapshot["page"] == "POPUP_MARKETING_OVERLAY":
        close_node = find_popup_close(after_snapshot["nodes"])  # type: ignore[arg-type]
        if close_node and tap_node(client, close_node):
            audit.log("popup_marketing_overlay_close_clicked", bounds=close_node["bounds"], from_page=after_snapshot["page"])
            result["audit_logged"] = True
            sleep_after(2.5)
            after_state = get_runtime_state(client)
            after_snapshot = capture(client, "s05_after_popup_close", str(result["target_series"]), str(result["target_model_year"]), str(result["target_trim"]), str(after_state.get("foreground_package") or ""))
        else:
            return stop_with_issue(
                issues.record(
                    "POPUP_MARKETING_OVERLAY",
                    "S05",
                    "Marketing popup detected after series click but explicit close button was not safely clickable.",
                    {"screenshot_path": after_snapshot["screenshot_path"], "xml_path": after_snapshot["xml_path"]},
                    "manual_intervention",
                    recognized_text=" ".join(after_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
    elif after_snapshot["page"] == "POPUP_UNCONTRACTED":
        return stop_with_issue(
            issues.record(
                "POPUP_UNCONTRACTED",
                "S05",
                "Blocking non-marketing popup detected after series click.",
                {"screenshot_path": after_snapshot["screenshot_path"], "xml_path": after_snapshot["xml_path"], "page": after_snapshot["page"]},
                "manual_intervention",
                recognized_text=" ".join(after_snapshot["labels"]),  # type: ignore[arg-type]
            )
        )

    result["post_series_click_artifacts"] = {
        "screenshot_path": after_snapshot["screenshot_path"],
        "xml_path": after_snapshot["xml_path"],
    }
    result["after_page_result"] = after_snapshot["page"]
    result["model_year_seen"] = bool(after_snapshot["model_year_seen"])
    result["trim_region_seen"] = bool(after_snapshot["trim_region_seen"])
    result["entered_vehicle_list"] = after_snapshot["page"] == "VEHICLE_LIST_OR_DETAIL"
    result["s05_verified"] = after_snapshot["page"] == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"

    if result["s05_verified"]:
        audit.log(
            "series_model_button_click_verified",
            from_state="S04",
            to_state="S05",
            action_id="click_series_model_button",
            package_name="com.ganji.android.haoche_c",
            app_label="瓜子二手车",
            result=after_snapshot["page"],
            target_series=result["target_series"],
            model_year_seen=result["model_year_seen"],
            trim_region_seen=result["trim_region_seen"],
            entered_vehicle_list=False,
            collected_vehicle_data=False,
        )
        result["audit_logged"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    return stop_with_issue(
        issues.classify_and_record(
            fallback_code="MODEL_BUTTON_CLICK_NO_NAVIGATION",
            state_id="S04",
            message="Target series model button click did not verify S05 model/year/trim page.",
            context={
                "screenshot_path": after_snapshot["screenshot_path"],
                "xml_path": after_snapshot["xml_path"],
                "page": after_snapshot["page"],
                "target_series": result["target_series"],
            },
            current_state="S04",
            intended_action="click_series_model_button",
            expected_next_state="S05_MODEL_YEAR_TRIM_PAGE_VERIFIED",
            actual_next_state=str(after_snapshot["page"]),
            actual_clicked_target={
                "text": "车型",
                "role": "series_model_button",
                "series": result["target_series"],
                "bounds": series_node["bounds"],
            },
            before_xml=str(current_snapshot["xml_text"]),
            after_xml=str(after_snapshot["xml_text"]),
            page_contract=None,
            action_contract=configs["actions"]["actions"].get("click_series_model_button"),
            task_context={"brand": result["target_brand"], "series": result["target_series"]},
            resolution="manual_intervention",
            recognized_text=" ".join(after_snapshot["labels"]),  # type: ignore[arg-type]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
