"""Controlled runtime recovery to S03 and single target-brand click to S04.

This script is intentionally read-mostly around the device until a verified
page contract allows the next single business click.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guazi_app_data_system.app_startup import AdbClient
from guazi_app_data_system.audit import AuditLogger
from guazi_app_data_system.config_loader import ensure_runtime_dirs, load_config, project_path
from guazi_app_data_system.exception_handler import IssueRecorder
from guazi_app_data_system.feishu_sync import validate_current_target_task
from guazi_app_data_system.learning_loop import LearningLoop


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def first_line(value: str | None) -> str:
    text = (value or "").strip()
    return text.splitlines()[0].strip() if text else ""


def parse_bounds(bounds: str | None) -> list[int] | None:
    if not bounds:
        return None
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    if not match:
        return None
    return [int(item) for item in match.groups()]


def center(bounds: list[int]) -> tuple[int, int]:
    return ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)


def parse_nodes(xml_text: str) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = []
    if not xml_text.strip():
        return nodes
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return nodes
    for node in root.iter("node"):
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        labels: list[str] = []
        if text:
            labels.append(text)
        if desc and desc not in labels:
            labels.append(desc)
        nodes.append(
            {
                "text": text,
                "content_desc": desc,
                "labels": labels,
                "label": labels[0] if labels else "",
                "bounds": parse_bounds(node.attrib.get("bounds")),
                "selected": node.attrib.get("selected") == "true",
                "clickable": node.attrib.get("clickable") == "true",
                "scrollable": node.attrib.get("scrollable") == "true",
                "class": node.attrib.get("class") or "",
                "package": node.attrib.get("package") or "",
            }
        )
    return nodes


def all_labels(nodes: list[dict[str, object]]) -> list[str]:
    items: list[str] = []
    for node in nodes:
        for label in node["labels"]:  # type: ignore[index]
            if label and label not in items:
                items.append(str(label))
    return items


def find_exact(nodes: list[dict[str, object]], target: str) -> dict[str, object] | None:
    for node in nodes:
        for label in node["labels"]:  # type: ignore[index]
            if label == target:
                return node
    return None


def find_contains(nodes: list[dict[str, object]], target: str) -> dict[str, object] | None:
    for node in nodes:
        for label in node["labels"]:  # type: ignore[index]
            if target in str(label):
                return node
    return None


def find_brand_node(nodes: list[dict[str, object]], brand: str) -> dict[str, object] | None:
    for node in nodes:
        for label in node["labels"]:  # type: ignore[index]
            text = str(label)
            if first_line(text) == brand or text == brand or text.startswith(brand + "\n"):
                return node
    return None


def detect_bottom_nav(nodes: list[dict[str, object]]) -> bool:
    required = {"首页", "选车", "卖车", "我的"}
    seen: set[str] = set()
    for node in nodes:
        bounds = node["bounds"]
        if not bounds or bounds[1] < 2200:  # type: ignore[index]
            continue
        for label in node["labels"]:  # type: ignore[index]
            fl = first_line(str(label))
            if fl in required:
                seen.add(fl)
    return required.issubset(seen)


def selected_tab(nodes: list[dict[str, object]]) -> str | None:
    tabs = {"首页", "选车", "卖车", "新能源", "我的"}
    for node in nodes:
        bounds = node["bounds"]
        if not node["selected"] or not bounds or bounds[1] < 2200:  # type: ignore[index]
            continue
        for label in node["labels"]:  # type: ignore[index]
            fl = first_line(str(label))
            if fl in tabs:
                return fl
    return None


def has_select_brand_title(nodes: list[dict[str, object]]) -> bool:
    for node in nodes:
        for label in node["labels"]:  # type: ignore[index]
            if "选择品牌" in str(label):
                return True
    return False


def has_brand_entry(nodes: list[dict[str, object]]) -> bool:
    return find_exact(nodes, "品牌") is not None


def has_target_series(nodes: list[dict[str, object]], series: str) -> bool:
    return find_contains(nodes, series) is not None


def looks_like_marketing_popup(nodes: list[dict[str, object]]) -> bool:
    labels = "".join(all_labels(nodes))
    if any(keyword in labels for keyword in ["登录", "允许", "同意", "隐私", "权限", "更新"]):
        return False
    return any(keyword in labels for keyword in ["活动", "福利", "优惠", "领取", "红包", "抽奖", "去看看", "立即查看", "专享", "限时"])


def find_popup_close(nodes: list[dict[str, object]]) -> dict[str, object] | None:
    for target in ("关闭", "X", "x"):
        node = find_exact(nodes, target)
        if node and node.get("bounds"):
            return node
    return None


def classify_page(snapshot: dict[str, object], target_brand: str, target_series: str) -> str:
    nodes = snapshot["nodes"]  # type: ignore[assignment]
    labels = snapshot["labels"]  # type: ignore[assignment]
    label_blob = "".join(labels)
    root_package = str(snapshot.get("root_package") or "")
    fg_package = str(snapshot.get("foreground_package") or "")
    if root_package == "com.android.systemui" or fg_package == "com.android.systemui":
        return "SystemUI"
    if fg_package.endswith(".launcher") or "launcher" in fg_package.lower() or root_package.endswith(".launcher"):
        return "Launcher"
    if has_select_brand_title(nodes):  # type: ignore[arg-type]
        return "S03_BRAND_SELECT_PAGE_VERIFIED"
    bottom_nav = detect_bottom_nav(nodes)  # type: ignore[arg-type]
    current = selected_tab(nodes)  # type: ignore[arg-type]
    if bottom_nav and current == "我的":
        return "我的"
    if bottom_nav and current == "首页":
        return "HOME_PAGE_VERIFIED"
    if bottom_nav and current == "选车" and has_brand_entry(nodes) and not has_select_brand_title(nodes):  # type: ignore[arg-type]
        return "S02_SELECT_CAR_TAB_VERIFIED"
    if fg_package == "com.ganji.android.haoche_c" and not has_select_brand_title(nodes):  # type: ignore[arg-type]
        blocked_markers = ["2020款", "配置", "颜色", "综合排序", "价格从低到高", "上牌", "表显", "公里", "万", "出险", "过户"]
        if has_target_series(nodes, target_series):  # type: ignore[arg-type]
            return "S04_SERIES_LIST_PAGE_VERIFIED"
        if target_brand in label_blob and not any(marker in label_blob for marker in blocked_markers):
            return "S04_SERIES_LIST_PAGE_POSSIBLE"
    if fg_package == "com.ganji.android.haoche_c" and looks_like_marketing_popup(nodes):  # type: ignore[arg-type]
        close_node = find_popup_close(nodes)  # type: ignore[arg-type]
        if close_node:
            return "POPUP_MARKETING_OVERLAY"
    if fg_package == "com.ganji.android.haoche_c" and any(keyword in label_blob for keyword in ["登录", "允许", "同意", "隐私", "权限", "更新"]):
        return "POPUP_UNCONTRACTED"
    return "未知"


def capture(client: AdbClient, name: str, target_brand: str, target_series: str, foreground_package: str = "") -> dict[str, object]:
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
    snapshot["page"] = classify_page(snapshot, target_brand, target_series)
    snapshot["bottom_nav_visible"] = detect_bottom_nav(nodes)
    snapshot["selected_tab"] = selected_tab(nodes)
    snapshot["has_select_brand_title"] = has_select_brand_title(nodes)
    return snapshot


def tap_node(client: AdbClient, node: dict[str, object] | None) -> bool:
    if not node or not node.get("bounds"):
        return False
    x, y = center(node["bounds"])  # type: ignore[arg-type]
    result = client.tap(x, y)
    return result.success


def sleep_after(seconds: float = 3.0) -> None:
    time.sleep(seconds)


def adb_device_state(client: AdbClient) -> tuple[str, list[dict[str, str]]]:
    devices_l = client.run(["devices", "-l"], timeout=20)
    entries: list[dict[str, str]] = []
    for line in devices_l.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) >= 2:
            entries.append({"serial": parts[0], "status": parts[1], "raw": line})
    return devices_l.stdout, entries


def window_token_package(token: str) -> str:
    text = (token or "").strip()
    if not text:
        return ""
    if "/" in text:
        return text.split("/", 1)[0]
    if "NotificationShade" in text:
        return "com.android.systemui"
    return text


def summarize_initial_state(runtime_state: dict[str, object]) -> str:
    if runtime_state.get("screen_off"):
        return "黑屏"
    if runtime_state.get("notification_shade_focused"):
        return "NotificationShade"
    focus_package = str(runtime_state.get("focus_package") or "")
    foreground_package = str(runtime_state.get("foreground_package") or "")
    if focus_package == "com.shuqing.launcher" or foreground_package == "com.shuqing.launcher":
        return "Launcher"
    if focus_package == "com.android.systemui" or foreground_package == "com.android.systemui":
        return "SystemUI"
    if runtime_state.get("third_party_overlay_detected"):
        return "未知"
    return "未知"


def get_runtime_state(client: AdbClient) -> dict[str, object]:
    window = client.run(["shell", "dumpsys", "window"], timeout=30).stdout
    activities = client.run(["shell", "dumpsys", "activity", "activities"], timeout=30).stdout
    power = client.run(["shell", "dumpsys", "power"], timeout=30).stdout
    current_focus = ""
    focused_window = ""
    match = re.search(r"mCurrentFocus=Window\{[^\}]+\s+([^\}\s]+)\}", window)
    if match:
        current_focus = match.group(1)
    match = re.search(r"mFocusedWindow=Window\{[^\}]+\s+([^\}\s]+)\}", window)
    if match:
        focused_window = match.group(1)
    foreground = ""
    for pattern in [
        r"topResumedActivity=ActivityRecord\{[^\}]+\s+([^/\s]+)/",
        r"mResumedActivity: ActivityRecord\{[^\}]+\s+([^/\s]+)/",
        r"mResumeActivity: ActivityRecord\{[^\}]+\s+([^/\s]+)/",
        r"ResumedActivity: ActivityRecord\{[^\}]+\s+([^/\s]+)/",
    ]:
        match = re.search(pattern, activities)
        if match:
            foreground = match.group(1)
            break
    focus_token = focused_window or current_focus
    focus_package = window_token_package(focus_token)
    screen_off = ("mWakefulness=Asleep" in power) or ("mWakefulness=Dozing" in power) or ("mAwake=false" in window and "mAwake=true" not in window)
    notification_shade_focused = "NotificationShade" in current_focus or "NotificationShade" in focused_window
    allowed_focus_packages = {"", "com.ganji.android.haoche_c", "com.shuqing.launcher", "com.android.systemui"}
    third_party_overlay_detected = bool(focus_package and focus_package not in allowed_focus_packages and focus_package != foreground)
    return {
        "mDreamingLockscreen": "mDreamingLockscreen=true" in window,
        "isKeyguardShowing": any(token in window for token in ["isKeyguardShowing=true", "mShowingLockscreen=true", "mKeyguardShowing=true", "isStatusBarKeyguard=true"]),
        "current_focus": current_focus,
        "focused_window": focused_window,
        "focus_package": focus_package,
        "notification_shade_focused": notification_shade_focused,
        "foreground_package": foreground,
        "screen_off": screen_off,
        "third_party_overlay_detected": third_party_overlay_detected,
    }


def load_verified_target_app(learning: LearningLoop) -> dict[str, object] | None:
    for solution in learning.load_solutions():
        if solution.get("issue_code") == "TARGET_APP_VERIFIED" and solution.get("approved") is True:
            app = solution.get("verified_target_app") or {}
            excluded = app.get("excluded_packages") or []
            return {
                "package_name": app.get("package_name"),
                "app_label": app.get("app_label"),
                "launch_activity": app.get("launch_activity"),
                "excluded_confirmed": any(item.get("package_name") == "com.guazi.android.chesupai" and item.get("excluded") is True for item in excluded),
            }
    return None


def verify_s03(snapshot: dict[str, object]) -> bool:
    return snapshot["page"] == "S03_BRAND_SELECT_PAGE_VERIFIED" and bool(snapshot["has_select_brand_title"])


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
    issues = IssueRecorder(project_path(system["paths"]["issue_log"]), configs["exceptions"], learning_loop=learning)

    result: dict[str, object] = {
        "task_import_verified": False,
        "adb_status": None,
        "initial_state": None,
        "auto_wake_executed": False,
        "dismiss_keyguard_executed": False,
        "third_party_overlay_detected": False,
        "launched_verified_guazi_app": False,
        "launch_page_result": None,
        "recovery_path": [],
        "step_artifacts": {},
        "restored_to_s03": False,
        "has_select_brand_title": False,
        "target_brand": None,
        "target_brand_found": False,
        "target_brand_bounds": None,
        "clicked_target_brand_once": False,
        "pre_brand_click_artifacts": {},
        "post_brand_click_artifacts": {},
        "after_page_result": None,
        "s04_verified": False,
        "target_series_seen": False,
        "clicked_other_brands": False,
        "clicked_target_series": False,
        "entered_vehicle_list": False,
        "collected_vehicle_data": False,
        "modified_pricing_formula": False,
        "issue_logged": None,
        "audit_logged": False,
        "verified_app": None,
    }

    def stop_with_issue(issue: dict[str, object]) -> int:
        result["issue_logged"] = issue
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    check = validate_current_target_task()
    result["task_check"] = check
    result["task_import_verified"] = check.get("status") == "TASK_IMPORT_VERIFIED"
    result["target_brand"] = (check.get("app_operation_params") or {}).get("brand")
    result["target_series"] = (check.get("app_operation_params") or {}).get("series")
    result["target_vehicle_year"] = (check.get("app_operation_params") or {}).get("vehicle_year")
    result["target_model_year"] = (check.get("app_operation_params") or {}).get("model_year")
    result["target_trim"] = (check.get("app_operation_params") or {}).get("trim")
    result["target_color"] = (check.get("app_operation_params") or {}).get("color")

    if not result["task_import_verified"]:
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S03", "Current target task is not verified.", {"task_check": check}, "blocked"))
    if check.get("task", {}).get("source") != "feishu_export" or check.get("task", {}).get("simulation_only") is not False or not check.get("allow_real_device_operation"):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S03", "Current target task does not permit real device operation.", {"task_check": check}, "blocked"))
    if "reference_index" in (check.get("task") or {}):
        return stop_with_issue(issues.record("TARGET_TASK_GATE_BLOCKED", "S03", "reference_index appeared in target input unexpectedly.", {"task_check": check}, "blocked"))

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
    result["initial_state"] = summarize_initial_state(state)
    if state["screen_off"]:
        client.run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], timeout=20)
        result["auto_wake_executed"] = True
        sleep_after(1.5)
    client.run(["shell", "wm", "dismiss-keyguard"], timeout=20)
    result["dismiss_keyguard_executed"] = True
    sleep_after(1.5)
    state = get_runtime_state(client)
    result["post_recovery_runtime_state"] = state
    result["third_party_overlay_detected"] = bool(state.get("third_party_overlay_detected"))

    initial_fg = str(state.get("foreground_package") or "")
    if state["mDreamingLockscreen"] or state["isKeyguardShowing"] or state["notification_shade_focused"]:
        snap = capture(client, "recovery_blocked", str(result["target_brand"]), str(result["target_series"]), initial_fg)
        result["initial_state"] = "SystemUI" if snap["page"] == "SystemUI" else "未知"
        result["step_artifacts"]["recovery_blocked"] = {"screenshot_path": snap["screenshot_path"], "xml_path": snap["xml_path"], "page": snap["page"]}
        issue = issues.record(
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "System overlay / keyguard / NotificationShade still blocks page recovery.",
            {
                "runtime_state": state,
                "screenshot_path": snap["screenshot_path"],
                "xml_path": snap["xml_path"],
                "page": snap["page"],
            },
            "manual_unlock_required",
            recognized_text=" ".join(snap["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)

    if state["third_party_overlay_detected"]:
        snap = capture(client, "third_party_overlay_blocked", str(result["target_brand"]), str(result["target_series"]), initial_fg)
        result["step_artifacts"]["third_party_overlay_blocked"] = {
            "screenshot_path": snap["screenshot_path"],
            "xml_path": snap["xml_path"],
            "page": snap["page"],
        }
        issue = issues.record(
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "A third-party app overlay or login window is covering the Guazi flow.",
            {
                "runtime_state": state,
                "screenshot_path": snap["screenshot_path"],
                "xml_path": snap["xml_path"],
                "page": snap["page"],
            },
            "manual_intervention",
            recognized_text=" ".join(snap["labels"]),  # type: ignore[arg-type]
        )
        return stop_with_issue(issue)

    current_snapshot: dict[str, object] | None = None
    if initial_fg == "com.ganji.android.haoche_c":
        current_snapshot = capture(client, "current_foreground", str(result["target_brand"]), str(result["target_series"]), initial_fg)
        if result["initial_state"] not in {"???", "NotificationShade", "Launcher", "SystemUI"}:
            result["initial_state"] = current_snapshot["page"]
        result["step_artifacts"]["current_foreground"] = {
            "screenshot_path": current_snapshot["screenshot_path"],
            "xml_path": current_snapshot["xml_path"],
            "page": current_snapshot["page"],
        }
    else:
        pre = capture(client, "pre_launch_state", str(result["target_brand"]), str(result["target_series"]), initial_fg)
        if result["initial_state"] not in {"???", "NotificationShade", "Launcher", "SystemUI"}:
            result["initial_state"] = pre["page"]
        result["step_artifacts"]["pre_launch_state"] = {
            "screenshot_path": pre["screenshot_path"],
            "xml_path": pre["xml_path"],
            "page": pre["page"],
        }
        if pre["page"] not in {"Launcher", "SystemUI", "未知"}:
            return stop_with_issue(
                issues.record(
                    "PAGE_CONTRACT_MISMATCH",
                    "DEVICE",
                    "Unexpected non-Guazi foreground state before verified app launch.",
                    {
                        "page": pre["page"],
                        "foreground_package": initial_fg,
                        "screenshot_path": pre["screenshot_path"],
                        "xml_path": pre["xml_path"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(pre["labels"]),  # type: ignore[arg-type]
                )
            )
        audit.log(
            "verified_recovery_launch_requested",
            package_name=verified_app["package_name"],
            app_label=verified_app["app_label"],
            launch_activity=verified_app["launch_activity"],
            initial_state=pre["page"],
        )
        result["audit_logged"] = True
        launch_result = client.launch_activity_component(str(verified_app["launch_activity"]), wait_seconds=int(system["timeouts"]["launch_wait_seconds"]))
        result["launched_verified_guazi_app"] = launch_result.success
        audit.log(
            "verified_recovery_launch_completed",
            package_name=verified_app["package_name"],
            launch_activity=verified_app["launch_activity"],
            success=launch_result.success,
            stdout=launch_result.stdout,
            stderr=launch_result.stderr,
        )
        result["audit_logged"] = True
        launched_state = get_runtime_state(client)
        current_snapshot = capture(client, "post_launch_state", str(result["target_brand"]), str(result["target_series"]), str(launched_state.get("foreground_package") or ""))
        result["launch_page_result"] = current_snapshot["page"]
        result["step_artifacts"]["post_launch_state"] = {
            "screenshot_path": current_snapshot["screenshot_path"],
            "xml_path": current_snapshot["xml_path"],
            "page": current_snapshot["page"],
        }
        if launched_state["mDreamingLockscreen"] or launched_state["isKeyguardShowing"] or launched_state["notification_shade_focused"]:
            return stop_with_issue(
                issues.record(
                    "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
                    "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
                    "Verified launch completed but system overlay / keyguard still blocks the Guazi page contract.",
                    {
                        "runtime_state": launched_state,
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                    },
                    "manual_unlock_required",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        if launched_state["third_party_overlay_detected"]:
            return stop_with_issue(
                issues.record(
                    "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
                    "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
                    "Verified launch completed but a third-party overlay is still covering the Guazi flow.",
                    {
                        "runtime_state": launched_state,
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        if launched_state.get("foreground_package") != "com.ganji.android.haoche_c":
            return stop_with_issue(
                issues.record(
                    "PAGE_CONTRACT_MISMATCH",
                    "DEVICE",
                    "Verified launch completed but foreground app is not Guazi used-car.",
                    {
                        "runtime_state": launched_state,
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )

    assert current_snapshot is not None

    if current_snapshot["page"] == "POPUP_MARKETING_OVERLAY":
        close_node = find_popup_close(current_snapshot["nodes"])  # type: ignore[arg-type]
        if close_node and tap_node(client, close_node):
            audit.log("popup_marketing_overlay_close_clicked", bounds=close_node["bounds"], from_page=current_snapshot["page"])
            result["audit_logged"] = True
            sleep_after(2.5)
            current_snapshot = capture(client, "post_popup_close", str(result["target_brand"]), str(result["target_series"]), "com.ganji.android.haoche_c")
            result["step_artifacts"]["post_popup_close"] = {
                "screenshot_path": current_snapshot["screenshot_path"],
                "xml_path": current_snapshot["xml_path"],
                "page": current_snapshot["page"],
            }
        else:
            return stop_with_issue(
                issues.record(
                    "POPUP_MARKETING_OVERLAY",
                    "DEVICE",
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
                "DEVICE",
                "Blocking non-marketing popup detected during recovery.",
                {
                    "screenshot_path": current_snapshot["screenshot_path"],
                    "xml_path": current_snapshot["xml_path"],
                    "page": current_snapshot["page"],
                },
                "manual_intervention",
                recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
            )
        )

    path: list[str] = []

    if current_snapshot["page"] == "S03_BRAND_SELECT_PAGE_VERIFIED":
        path.append("S03 direct")
    elif current_snapshot["page"] == "S02_SELECT_CAR_TAB_VERIFIED":
        path.append("S02 -> S03")
        brand_entry = find_exact(current_snapshot["nodes"], "品牌")  # type: ignore[arg-type]
        if not brand_entry or not brand_entry.get("bounds"):
            return stop_with_issue(
                issues.record(
                    "BRAND_ENTRY_NOT_FOUND",
                    "S02",
                    "Brand entry not found on verified S02 page.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        audit.log(
            "normal_state_transition_requested",
            from_state="S02_SELECT_CAR_TAB",
            to_state="S03",
            action_id="click_brand_entry",
            package_name="com.ganji.android.haoche_c",
            app_label="瓜子二手车",
            precondition="S02_SELECT_CAR_TAB_VERIFIED",
            target_bounds=str(brand_entry["bounds"]),
            target_center=list(center(brand_entry["bounds"])),  # type: ignore[arg-type]
            forbidden_clicks_performed=[],
        )
        result["audit_logged"] = True
        tap_node(client, brand_entry)
        sleep_after(3.0)
        current_snapshot = capture(client, "recover_s02_to_s03_after", str(result["target_brand"]), str(result["target_series"]), "com.ganji.android.haoche_c")
        result["step_artifacts"]["recover_s02_to_s03_after"] = {
            "screenshot_path": current_snapshot["screenshot_path"],
            "xml_path": current_snapshot["xml_path"],
            "page": current_snapshot["page"],
        }
        if not verify_s03(current_snapshot):
            code = "WRONG_PAGE_AFTER_BRAND_ENTRY" if current_snapshot["page"] not in {"未知", "S02_SELECT_CAR_TAB_VERIFIED"} else "S03_BRAND_PAGE_NOT_VERIFIED"
            return stop_with_issue(
                issues.record(
                    code,
                    "S02",
                    "Brand entry click did not verify S03 brand page.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                        "page": current_snapshot["page"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        audit.log(
            "normal_state_transition_verified",
            from_state="S02_SELECT_CAR_TAB",
            to_state="S03",
            action_id="click_brand_entry",
            result="S03_BRAND_SELECT_PAGE_VERIFIED",
            package_name="com.ganji.android.haoche_c",
            app_label="瓜子二手车",
            top_select_brand_present=True,
            selected_specific_brand=False,
            entered_series_page=False,
            collected_vehicle_data=False,
        )
        result["audit_logged"] = True
    elif current_snapshot["page"] == "HOME_PAGE_VERIFIED":
        path.append("HOME -> S02 -> S03")
        select_tab = find_exact(current_snapshot["nodes"], "选车") or find_contains(current_snapshot["nodes"], "选车")  # type: ignore[arg-type]
        if not select_tab or not select_tab.get("bounds"):
            return stop_with_issue(
                issues.record(
                    "SELECT_TAB_NOT_FOUND",
                    "S01_HOME",
                    "Bottom Select Car tab not found on verified Home page.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        audit.log(
            "normal_state_transition_requested",
            from_state="S01_HOME",
            to_state="S02",
            action_id="click_bottom_select_car_tab",
            package_name="com.ganji.android.haoche_c",
            app_label="瓜子二手车",
            precondition="HOME_PAGE_VERIFIED",
            target_bounds=str(select_tab["bounds"]),
            target_center=list(center(select_tab["bounds"])),  # type: ignore[arg-type]
            forbidden_clicks_performed=[],
        )
        result["audit_logged"] = True
        tap_node(client, select_tab)
        sleep_after(3.0)
        current_snapshot = capture(client, "recover_home_to_select_after", str(result["target_brand"]), str(result["target_series"]), "com.ganji.android.haoche_c")
        result["step_artifacts"]["recover_home_to_select_after"] = {
            "screenshot_path": current_snapshot["screenshot_path"],
            "xml_path": current_snapshot["xml_path"],
            "page": current_snapshot["page"],
        }
        if current_snapshot["page"] != "S02_SELECT_CAR_TAB_VERIFIED":
            return stop_with_issue(
                issues.record(
                    "HOME_TO_SELECT_NAVIGATION_FAILED",
                    "S01_HOME",
                    "Home to Select Car navigation did not verify S02.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                        "page": current_snapshot["page"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        audit.log(
            "normal_state_transition_verified",
            from_state="S01_HOME",
            to_state="S02",
            action_id="click_bottom_select_car_tab",
            result="S02_SELECT_CAR_TAB_VERIFIED",
            package_name="com.ganji.android.haoche_c",
            app_label="瓜子二手车",
            bottom_navigation_visible=True,
            selected_tab="选车",
            brand_page_detected=False,
            forbidden_clicks_performed=[],
            collected_vehicle_data=False,
        )
        result["audit_logged"] = True
        brand_entry = find_exact(current_snapshot["nodes"], "品牌")  # type: ignore[arg-type]
        if not brand_entry or not brand_entry.get("bounds"):
            return stop_with_issue(
                issues.record(
                    "BRAND_ENTRY_NOT_FOUND",
                    "S02",
                    "Brand entry not found after recovery to S02.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        audit.log(
            "normal_state_transition_requested",
            from_state="S02_SELECT_CAR_TAB",
            to_state="S03",
            action_id="click_brand_entry",
            package_name="com.ganji.android.haoche_c",
            app_label="瓜子二手车",
            precondition="S02_SELECT_CAR_TAB_VERIFIED",
            target_bounds=str(brand_entry["bounds"]),
            target_center=list(center(brand_entry["bounds"])),  # type: ignore[arg-type]
            forbidden_clicks_performed=[],
        )
        result["audit_logged"] = True
        tap_node(client, brand_entry)
        sleep_after(3.0)
        current_snapshot = capture(client, "recover_select_to_brand_after", str(result["target_brand"]), str(result["target_series"]), "com.ganji.android.haoche_c")
        result["step_artifacts"]["recover_select_to_brand_after"] = {
            "screenshot_path": current_snapshot["screenshot_path"],
            "xml_path": current_snapshot["xml_path"],
            "page": current_snapshot["page"],
        }
        if not verify_s03(current_snapshot):
            code = "WRONG_PAGE_AFTER_BRAND_ENTRY" if current_snapshot["page"] not in {"未知", "S02_SELECT_CAR_TAB_VERIFIED"} else "S03_BRAND_PAGE_NOT_VERIFIED"
            return stop_with_issue(
                issues.record(
                    code,
                    "S02",
                    "Brand entry click did not verify S03 after recovery.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                        "page": current_snapshot["page"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        audit.log(
            "normal_state_transition_verified",
            from_state="S02_SELECT_CAR_TAB",
            to_state="S03",
            action_id="click_brand_entry",
            result="S03_BRAND_SELECT_PAGE_VERIFIED",
            package_name="com.ganji.android.haoche_c",
            app_label="瓜子二手车",
            top_select_brand_present=True,
            selected_specific_brand=False,
            entered_series_page=False,
            collected_vehicle_data=False,
        )
        result["audit_logged"] = True
    elif current_snapshot["page"] == "我的":
        path.append("我的 -> HOME -> S02 -> S03")
        home_tab = find_exact(current_snapshot["nodes"], "首页") or find_contains(current_snapshot["nodes"], "首页")  # type: ignore[arg-type]
        if not home_tab or not home_tab.get("bounds"):
            return stop_with_issue(
                issues.record(
                    "PAGE_CONTRACT_MISMATCH",
                    "S01_MY",
                    "Home tab not found on My tab recovery path.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        audit.log("verified_recovery_step", from_state="S01_MY", to_state="S01_HOME", action_id="click_bottom_home_tab", target_bounds=str(home_tab["bounds"]))
        result["audit_logged"] = True
        tap_node(client, home_tab)
        sleep_after(3.0)
        current_snapshot = capture(client, "recover_my_to_home_after", str(result["target_brand"]), str(result["target_series"]), "com.ganji.android.haoche_c")
        result["step_artifacts"]["recover_my_to_home_after"] = {
            "screenshot_path": current_snapshot["screenshot_path"],
            "xml_path": current_snapshot["xml_path"],
            "page": current_snapshot["page"],
        }
        if current_snapshot["page"] != "HOME_PAGE_VERIFIED":
            return stop_with_issue(
                issues.record(
                    "PAGE_CONTRACT_MISMATCH",
                    "S01_MY",
                    "My tab recovery did not enter verified Home page.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                        "page": current_snapshot["page"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        select_tab = find_exact(current_snapshot["nodes"], "选车") or find_contains(current_snapshot["nodes"], "选车")  # type: ignore[arg-type]
        if not select_tab or not select_tab.get("bounds"):
            return stop_with_issue(
                issues.record(
                    "SELECT_TAB_NOT_FOUND",
                    "S01_HOME",
                    "Bottom Select Car tab not found after My->Home recovery.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        audit.log(
            "normal_state_transition_requested",
            from_state="S01_HOME",
            to_state="S02",
            action_id="click_bottom_select_car_tab",
            package_name="com.ganji.android.haoche_c",
            app_label="瓜子二手车",
            precondition="HOME_PAGE_VERIFIED",
            target_bounds=str(select_tab["bounds"]),
            target_center=list(center(select_tab["bounds"])),  # type: ignore[arg-type]
            forbidden_clicks_performed=[],
        )
        result["audit_logged"] = True
        tap_node(client, select_tab)
        sleep_after(3.0)
        current_snapshot = capture(client, "recover_home_to_select_after", str(result["target_brand"]), str(result["target_series"]), "com.ganji.android.haoche_c")
        result["step_artifacts"]["recover_home_to_select_after"] = {
            "screenshot_path": current_snapshot["screenshot_path"],
            "xml_path": current_snapshot["xml_path"],
            "page": current_snapshot["page"],
        }
        if current_snapshot["page"] != "S02_SELECT_CAR_TAB_VERIFIED":
            return stop_with_issue(
                issues.record(
                    "HOME_TO_SELECT_NAVIGATION_FAILED",
                    "S01_HOME",
                    "Home to Select Car navigation did not verify S02.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                        "page": current_snapshot["page"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        brand_entry = find_exact(current_snapshot["nodes"], "品牌")  # type: ignore[arg-type]
        if not brand_entry or not brand_entry.get("bounds"):
            return stop_with_issue(
                issues.record(
                    "BRAND_ENTRY_NOT_FOUND",
                    "S02",
                    "Brand entry not found after recovery to S02.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
        audit.log(
            "normal_state_transition_requested",
            from_state="S02_SELECT_CAR_TAB",
            to_state="S03",
            action_id="click_brand_entry",
            package_name="com.ganji.android.haoche_c",
            app_label="瓜子二手车",
            precondition="S02_SELECT_CAR_TAB_VERIFIED",
            target_bounds=str(brand_entry["bounds"]),
            target_center=list(center(brand_entry["bounds"])),  # type: ignore[arg-type]
            forbidden_clicks_performed=[],
        )
        result["audit_logged"] = True
        tap_node(client, brand_entry)
        sleep_after(3.0)
        current_snapshot = capture(client, "recover_select_to_brand_after", str(result["target_brand"]), str(result["target_series"]), "com.ganji.android.haoche_c")
        result["step_artifacts"]["recover_select_to_brand_after"] = {
            "screenshot_path": current_snapshot["screenshot_path"],
            "xml_path": current_snapshot["xml_path"],
            "page": current_snapshot["page"],
        }
        if not verify_s03(current_snapshot):
            code = "WRONG_PAGE_AFTER_BRAND_ENTRY" if current_snapshot["page"] not in {"未知", "S02_SELECT_CAR_TAB_VERIFIED"} else "S03_BRAND_PAGE_NOT_VERIFIED"
            return stop_with_issue(
                issues.record(
                    code,
                    "S02",
                    "Brand entry click did not verify S03 after recovery.",
                    {
                        "screenshot_path": current_snapshot["screenshot_path"],
                        "xml_path": current_snapshot["xml_path"],
                        "page": current_snapshot["page"],
                    },
                    "manual_intervention",
                    recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
                )
            )
    else:
        return stop_with_issue(
            issues.record(
                "PAGE_CONTRACT_MISMATCH",
                "DEVICE",
                "Current page cannot enter verified recovery path to S03.",
                {
                    "page": current_snapshot["page"],
                    "screenshot_path": current_snapshot["screenshot_path"],
                    "xml_path": current_snapshot["xml_path"],
                    "foreground_package": current_snapshot["foreground_package"],
                },
                "manual_intervention",
                recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
            )
        )

    result["recovery_path"] = path
    result["restored_to_s03"] = verify_s03(current_snapshot)
    result["has_select_brand_title"] = current_snapshot["has_select_brand_title"]
    if not result["restored_to_s03"]:
        return stop_with_issue(
            issues.record(
                "PAGE_CONTRACT_MISMATCH",
                "S03",
                "Recovery path ended without verified S03.",
                {
                    "page": current_snapshot["page"],
                    "screenshot_path": current_snapshot["screenshot_path"],
                    "xml_path": current_snapshot["xml_path"],
                },
                "manual_intervention",
                recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
            )
        )

    result["pre_brand_click_artifacts"] = {"screenshot_path": current_snapshot["screenshot_path"], "xml_path": current_snapshot["xml_path"]}
    brand_node = find_brand_node(current_snapshot["nodes"], str(result["target_brand"]))  # type: ignore[arg-type]
    if not brand_node:
        d_node = find_exact(current_snapshot["nodes"], "D")  # type: ignore[arg-type]
        if d_node and d_node.get("bounds") and d_node["bounds"][0] >= 1100:  # type: ignore[index]
            audit.log("brand_index_navigation_requested", state="S03", letter="D", bounds=d_node["bounds"])
            result["audit_logged"] = True
            tap_node(client, d_node)
            sleep_after(2.5)
            current_snapshot = capture(client, "s03_after_letter_d", str(result["target_brand"]), str(result["target_series"]), "com.ganji.android.haoche_c")
            result["step_artifacts"]["s03_after_letter_d"] = {
                "screenshot_path": current_snapshot["screenshot_path"],
                "xml_path": current_snapshot["xml_path"],
                "page": current_snapshot["page"],
            }
            brand_node = find_brand_node(current_snapshot["nodes"], str(result["target_brand"]))  # type: ignore[arg-type]
            if verify_s03(current_snapshot):
                result["pre_brand_click_artifacts"] = {"screenshot_path": current_snapshot["screenshot_path"], "xml_path": current_snapshot["xml_path"]}
    if not brand_node or not brand_node.get("bounds"):
        return stop_with_issue(
            issues.record(
                "BRAND_NOT_FOUND",
                "S03",
                "Target brand 大众 was not found on verified S03 page.",
                {
                    "screenshot_path": current_snapshot["screenshot_path"],
                    "xml_path": current_snapshot["xml_path"],
                    "target_brand": result["target_brand"],
                },
                "manual_intervention",
                recognized_text=" ".join(current_snapshot["labels"]),  # type: ignore[arg-type]
            )
        )

    result["target_brand_found"] = True
    result["target_brand_bounds"] = brand_node["bounds"]
    audit.log(
        "target_brand_click_requested",
        state="S03",
        target_brand=result["target_brand"],
        target_series=result["target_series"],
        bounds=brand_node["bounds"],
        forbidden_clicks_performed=[],
    )
    result["audit_logged"] = True
    success = tap_node(client, brand_node)
    result["clicked_target_brand_once"] = bool(success)
    sleep_after(3.0)
    after_fg = str(get_runtime_state(client).get("foreground_package") or "com.ganji.android.haoche_c")
    after_snapshot = capture(client, "s03_to_s04_after_brand_click", str(result["target_brand"]), str(result["target_series"]), after_fg)
    result["post_brand_click_artifacts"] = {"screenshot_path": after_snapshot["screenshot_path"], "xml_path": after_snapshot["xml_path"]}
    result["after_page_result"] = after_snapshot["page"]
    result["target_series_seen"] = has_target_series(after_snapshot["nodes"], str(result["target_series"]))  # type: ignore[arg-type]
    result["s04_verified"] = after_snapshot["page"] in {"S04_SERIES_LIST_PAGE_VERIFIED", "S04_SERIES_LIST_PAGE_POSSIBLE"}
    if result["s04_verified"]:
        audit.log(
            "target_brand_click_verified",
            from_state="S03",
            to_state="S04",
            action_id="tap_target_brand",
            package_name="com.ganji.android.haoche_c",
            app_label="瓜子二手车",
            result=after_snapshot["page"],
            target_brand=result["target_brand"],
            target_series_seen=result["target_series_seen"],
            entered_vehicle_list=False,
            collected_vehicle_data=False,
        )
        result["audit_logged"] = True
    else:
        code = "BRAND_CLICK_NO_NAVIGATION" if after_snapshot["page"] == "S03_BRAND_SELECT_PAGE_VERIFIED" else "WRONG_PAGE_AFTER_BRAND_CLICK"
        result["issue_logged"] = issues.record(
            code,
            "S03",
            "Target brand click did not verify S04 series list page.",
            {
                "screenshot_path": after_snapshot["screenshot_path"],
                "xml_path": after_snapshot["xml_path"],
                "page": after_snapshot["page"],
                "target_brand": result["target_brand"],
            },
            "manual_intervention",
            recognized_text=" ".join(after_snapshot["labels"]),  # type: ignore[arg-type]
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
