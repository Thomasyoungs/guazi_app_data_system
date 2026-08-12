"""Real-device mainline executor for S01-S10.

This script covers the fixed page-contract chain:
S01 -> S02 -> S03 -> S04 -> S05 -> S06 -> S07 -> S08 -> S09 -> S10.

It stops at S10_READY and does not enter S11.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

try:
    from PIL import Image, ImageChops
except Exception:  # pragma: no cover - evidence comparison is best-effort.
    Image = None  # type: ignore[assignment]
    ImageChops = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guazi_app_data_system.app_startup import (
    AdbClient,
    GUAZI_APP_ICON_LABEL,
    GUAZI_PACKAGE,
    _extract_focused_window,
    _extract_foreground_package,
    _extract_resumed_activity,
    _is_keyguard_secure_from_window_dump,
    _is_keyguard_showing_from_window_dump,
    _is_probably_black_screenshot,
    extract_xml_root_package,
)
from guazi_app_data_system.audit import AuditLogger
from guazi_app_data_system.config_loader import ensure_runtime_dirs, load_config, project_path
from guazi_app_data_system.exception_handler import GuaziFlowError, IssueRecorder
from guazi_app_data_system.feishu_sync import validate_current_target_task
from guazi_app_data_system.issue_classifier import IssueClassifier
from guazi_app_data_system.learning_loop import LearningLoop
from guazi_app_data_system.output_writer import write_json
from guazi_app_data_system.page_recognition import PageRecognizer
from guazi_app_data_system.page_state_machine import PageStateMachine


S01_TO_S10_STATES = {
    "S01",
    "S02",
    "S02_SELECT_CAR_TAB",
    "S03",
    "S04",
    "S05",
    "S05_MODEL_YEAR_SELECTED",
    "S05_TRIM_SELECTED",
    "S06",
    "S07",
    "S08",
    "S09",
    "S10",
}
S_LOGIN_ACCOUNT_PACKAGE = "com.shuqing.tqaccountcenter"
S_LOGIN_LEGACY_PACKAGE = "com.shuqing.launcher"
S_LOGIN_ACTIVITY_HINT = "account.login.LoginHomeActivity"
S_LOGIN_LATER_TEXT = "稍后"
S_LOGIN_HINT_TEXTS = (
    "登录",
    "欢迎登录",
    "请输入手机号码",
    "请输入手机号",
    "请输入验证码",
    "获取验证码",
    "手机号码",
    "验证码",
)
S_LOGIN_BOTTOM_BACK_TEXTS = {"<", "\uff1c"}
TARGET_TASK_DATA_PATH = ROOT / "data" / "current_target_task.json"
TARGET_TASK_REQUIRED_FIELDS = ("brand", "series", "model_year", "trim", "color", "registration_date")
S03_BRAND_SCROLL_LIMIT = 8
S04_SERIES_SCROLL_LIMIT = 8
S04_SCROLL_SERIES_ACTION = "scroll_series_list"
LAUNCHER_ACCOUNT_DIALOG_TEXTS = (
    "检测到您的账号已退出登录",
    "请重新登录账号",
    "稍后",
    "去登录",
)
LAUNCHER_ACCOUNT_LATER_TEXT = "稍后"


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


class TimingRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def add(
        self,
        *,
        step_name: str,
        page_name: str,
        action_name: str,
        contract_check_ms: int,
        field_read_ms: int,
        action_ms: int,
        transition_wait_ms: int,
        screenshot_path: str | None,
        xml_path: str | None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        full_step_ms = contract_check_ms + field_read_ms + action_ms + transition_wait_ms
        record = {
            "step_name": step_name,
            "page_name": page_name,
            "action_name": action_name,
            "contract_check_ms": contract_check_ms,
            "field_read_ms": field_read_ms,
            "action_ms": action_ms,
            "transition_wait_ms": transition_wait_ms,
            "full_step_ms": full_step_ms,
            "exceeded_3s": full_step_ms > 3000,
            "screenshot_path": screenshot_path,
            "xml_path": xml_path,
        }
        if extra:
            record.update(extra)
        self.records.append(record)

    def write(self) -> None:
        md_path = project_path("output", "page_contract_timing_report.md")
        jsonl_path = project_path("output", "page_contract_timing_report.jsonl")
        lines = [
            "| step_name | page_name | action_name | contract_check_ms | field_read_ms | action_ms | transition_wait_ms | full_step_ms | exceeded_3s | screenshot_path | xml_path |",
            "|---|---|---|---:|---:|---:|---:|---:|---|---|---|",
        ]
        for record in self.records:
            lines.append(
                "| {step_name} | {page_name} | {action_name} | {contract_check_ms} | {field_read_ms} | {action_ms} | {transition_wait_ms} | {full_step_ms} | {exceeded_3s} | {screenshot_path} | {xml_path} |".format(
                    **record
                )
            )
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for record in self.records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(raw or ""))
    if not match:
        return None
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def _center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    return ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)


def _has_nonzero_bounds(bounds: tuple[int, int, int, int] | None) -> bool:
    return bool(bounds) and any(value != 0 for value in bounds)


def _parse_nodes(xml_text: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if not xml_text.strip():
        return nodes
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return nodes
    for node in root.iter("node"):
        text = str(node.attrib.get("text") or "").strip()
        desc = str(node.attrib.get("content-desc") or "").strip()
        labels = [item for item in [text, desc] if item]
        nodes.append(
            {
                "text": text,
                "content_desc": desc,
                "labels": labels,
                "bounds": _parse_bounds(node.attrib.get("bounds", "")),
                "clickable": str(node.attrib.get("clickable") or "") == "true",
                "enabled": str(node.attrib.get("enabled") or "") == "true",
                "selected": str(node.attrib.get("selected") or "") == "true",
                "package": str(node.attrib.get("package") or ""),
                "class_name": str(node.attrib.get("class") or ""),
            }
        )
    return nodes


def _visible_texts(xml_text: str) -> list[str]:
    texts: list[str] = []
    for node in _parse_nodes(xml_text):
        for label in node["labels"]:
            if label and label not in texts:
                texts.append(label)
    return texts


def _repo_relative(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _result_task_payload(result: dict[str, Any]) -> dict[str, Any]:
    task = result.get("target_task") if isinstance(result.get("target_task"), dict) else {}
    params = result.get("task_params") if isinstance(result.get("task_params"), dict) else {}
    return {
        "target_task_path": task.get("actual_target_task_path") or params.get("target_task_path"),
        "brand": task.get("brand") or params.get("brand"),
        "series": task.get("series") or params.get("series"),
        "year_model": task.get("year_model") or params.get("model_year"),
        "config_model": task.get("config_model") or params.get("trim"),
        "color": task.get("color") or params.get("color"),
        "register_date": task.get("register_date") or params.get("registration_date"),
    }


def _target_fingerprint(task: dict[str, Any]) -> str:
    parts = [
        task.get("brand"),
        task.get("series"),
        task.get("year_model"),
        task.get("config_model"),
        task.get("color"),
        task.get("register_date"),
    ]
    return "|".join(str(part or "") for part in parts)


def _result_with_segment_metadata(result: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(result)
    task = _result_task_payload(result)
    enriched.setdefault("target_fingerprint", _target_fingerprint(task))
    enriched.setdefault("target_task_path", task.get("target_task_path"))
    enriched.setdefault("brand", task.get("brand"))
    enriched.setdefault("series", task.get("series"))
    enriched.setdefault("year_model", task.get("year_model"))
    enriched.setdefault("config_model", task.get("config_model"))
    enriched.setdefault("color", task.get("color"))
    enriched.setdefault("register_date", task.get("register_date"))
    enriched.setdefault("current_state", enriched.get("status") or enriched.get("error"))
    enriched.setdefault("final_status", enriched.get("final_status") or enriched.get("status"))
    enriched.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    enriched.setdefault("result_segment", "s01_to_s10")
    return enriched


def _write_result_json(configs: dict[str, Any], result: dict[str, Any]) -> None:
    enriched = _result_with_segment_metadata(result)
    write_json(project_path("output", "result_s01_to_s10.json"), enriched)
    write_json(project_path(configs["system"]["paths"]["result_json"]), enriched)


def _enable_s04_scroll_series_list_action(pages_config: dict[str, Any]) -> None:
    for page in pages_config.get("pages", []):
        if page.get("id") != "S04":
            continue
        allowed_actions = page.setdefault("allowed_actions", [])
        if S04_SCROLL_SERIES_ACTION not in allowed_actions:
            allowed_actions.append(S04_SCROLL_SERIES_ACTION)
        return


def _capture(client: AdbClient, stem: str) -> dict[str, Any]:
    screenshot_path = ROOT / "artifacts" / "screenshots" / f"{stem}.png"
    screenshot_started = time.perf_counter()
    screenshot_result = client.screenshot(screenshot_path)
    screenshot_ms = int((time.perf_counter() - screenshot_started) * 1000)
    screenshot_exists = bool(screenshot_result.success) and screenshot_path.exists() and screenshot_path.stat().st_size > 0

    power = client.power_state()
    window_result = client.run(["shell", "dumpsys", "window"], timeout=20)
    activity_result = client.run(["shell", "dumpsys", "activity", "activities"], timeout=20)
    window_dump = window_result.stdout if window_result.success else ""
    activity_dump = activity_result.stdout if activity_result.success else ""

    xml_path = ROOT / "artifacts" / "debug" / f"{stem}.xml"
    xml_started = time.perf_counter()
    xml_dump_result = client.run(["shell", "uiautomator", "dump", "/sdcard/window.xml"], timeout=20)
    xml_read_result = client.run(["exec-out", "cat", "/sdcard/window.xml"], timeout=20)
    xml_ms = int((time.perf_counter() - xml_started) * 1000)
    xml_text = xml_read_result.stdout if xml_read_result.success else ""
    xml_missing = True
    if xml_text.strip():
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        xml_path.write_text(xml_text, encoding="utf-8")
        xml_missing = not xml_path.exists() or xml_path.stat().st_size <= 0

    screenshot_error = ""
    if not screenshot_exists:
        screenshot_error = screenshot_result.stderr or f"screenshot_failed_rc_{screenshot_result.returncode}"

    xml_dump_error = ""
    if xml_missing:
        if not xml_dump_result.success:
            xml_dump_error = xml_dump_result.stderr or f"xml_dump_failed_rc_{xml_dump_result.returncode}"
        elif not xml_read_result.success:
            xml_dump_error = xml_read_result.stderr or f"xml_read_failed_rc_{xml_read_result.returncode}"
        else:
            xml_dump_error = "xml_dump_empty"

    snapshot: dict[str, Any] = {
        "wakefulness": power.get("wakefulness"),
        "interactive": power.get("interactive"),
        "display_state": power.get("display_state"),
        "keyguard_showing": _is_keyguard_showing_from_window_dump(window_dump),
        "keyguard_locked": _is_keyguard_showing_from_window_dump(window_dump),
        "keyguard_secure": _is_keyguard_secure_from_window_dump(window_dump),
        "foreground_package": _extract_foreground_package(window_dump, activity_dump),
        "resumed_activity": _extract_resumed_activity(activity_dump),
        "focused_window": _extract_focused_window(window_dump),
        "xml_package": extract_xml_root_package(xml_text),
        "fresh_xml": xml_text,
        "fresh_screenshot": str(screenshot_path) if screenshot_exists else None,
        "screenshot_is_black": _is_probably_black_screenshot(screenshot_path) if screenshot_exists else False,
        "screenshot_missing": not screenshot_exists,
        "screenshot_path": _repo_relative(screenshot_path) if screenshot_exists else None,
        "screenshot_error": screenshot_error,
        "xml_missing": xml_missing,
        "xml_path": _repo_relative(xml_path) if not xml_missing else None,
        "xml_dump_error": xml_dump_error,
        "adb_rc": xml_dump_result.returncode if xml_missing else 0,
        "adb_stderr": xml_dump_result.stderr if xml_missing else "",
        "xml_dump_rc": xml_dump_result.returncode,
        "xml_dump_stderr": xml_dump_result.stderr,
        "xml_read_rc": xml_read_result.returncode,
        "xml_read_stderr": xml_read_result.stderr,
        "nodes": _parse_nodes(xml_text),
        "visible_texts": _visible_texts(xml_text),
        "capture_metrics": {
            "screenshot_ms": screenshot_ms,
            "xml_ms": xml_ms,
        },
    }
    snapshot["visible_blob"] = "".join(snapshot["visible_texts"])
    return snapshot


def _device_state_only(client: AdbClient) -> dict[str, Any]:
    power_started = time.perf_counter()
    power = client.power_state()
    power_ms = int((time.perf_counter() - power_started) * 1000)
    dumpsys_started = time.perf_counter()
    window_result = client.run(["shell", "dumpsys", "window"], timeout=20)
    activity_result = client.run(["shell", "dumpsys", "activity", "activities"], timeout=20)
    dumpsys_ms = int((time.perf_counter() - dumpsys_started) * 1000)
    window_dump = window_result.stdout if window_result.success else ""
    activity_dump = activity_result.stdout if activity_result.success else ""
    snapshot: dict[str, Any] = {
        "wakefulness": power.get("wakefulness"),
        "interactive": power.get("interactive"),
        "display_state": power.get("display_state"),
        "keyguard_showing": _is_keyguard_showing_from_window_dump(window_dump),
        "keyguard_locked": _is_keyguard_showing_from_window_dump(window_dump),
        "keyguard_secure": _is_keyguard_secure_from_window_dump(window_dump),
        "foreground_package": _extract_foreground_package(window_dump, activity_dump),
        "resumed_activity": _extract_resumed_activity(activity_dump),
        "focused_window": _extract_focused_window(window_dump),
        "xml_package": "",
        "fresh_xml": "",
        "fresh_screenshot": None,
        "screenshot_is_black": False,
        "screenshot_missing": True,
        "screenshot_path": None,
        "screenshot_error": "",
        "xml_missing": True,
        "xml_path": None,
        "xml_dump_error": "",
        "adb_rc": 0,
        "adb_stderr": "",
        "xml_dump_rc": 0,
        "xml_dump_stderr": "",
        "xml_read_rc": 0,
        "xml_read_stderr": "",
        "nodes": [],
        "visible_texts": [],
        "visible_blob": "",
        "capture_metrics": {
            "power_ms": power_ms,
            "dumpsys_ms": dumpsys_ms,
            "screenshot_ms": 0,
            "xml_ms": 0,
        },
    }
    return snapshot


def _is_s_login_prompt(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    package_names = {
        str(snapshot.get("foreground_package") or ""),
        str(snapshot.get("xml_package") or ""),
    }
    focused_window = str(snapshot.get("focused_window") or "")
    resumed_activity = str(snapshot.get("resumed_activity") or "")
    has_login_text = any(keyword in blob for keyword in S_LOGIN_HINT_TEXTS)
    has_account_package = S_LOGIN_ACCOUNT_PACKAGE in package_names
    has_legacy_login_package = S_LOGIN_LEGACY_PACKAGE in package_names and has_login_text
    has_login_activity = S_LOGIN_ACTIVITY_HINT in focused_window or S_LOGIN_ACTIVITY_HINT in resumed_activity
    return has_login_text or has_account_package or has_legacy_login_package or has_login_activity


def _s_login_later_bounds(snapshot: dict[str, Any]) -> tuple[tuple[int, int, int, int], ...]:
    bounds: list[tuple[int, int, int, int]] = []
    for node in snapshot.get("nodes", []):
        if S_LOGIN_LATER_TEXT not in [str(label) for label in node.get("labels", [])]:
            continue
        node_bounds = node.get("bounds")
        if isinstance(node_bounds, (list, tuple)) and len(node_bounds) == 4:
            bounds.append(tuple(int(value) for value in node_bounds))
    return tuple(bounds)


def _s_login_later_visible(snapshot: dict[str, Any]) -> bool:
    return bool(_s_login_later_bounds(snapshot))


def _s_login_bottom_back_node(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    node_bounds = [node.get("bounds") for node in snapshot.get("nodes", []) if node.get("bounds")]
    screen_bottom = max((bounds[3] for bounds in node_bounds), default=0)
    min_y = int(screen_bottom * 0.80) if screen_bottom else 0
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not bounds or _center(bounds)[1] < min_y:
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        if labels & S_LOGIN_BOTTOM_BACK_TEXTS:
            return node
    return None


def _tap_s_login_bottom_back_once(context: dict[str, Any], snapshot: dict[str, Any], *, capture_stem: str, step_name: str) -> dict[str, Any]:
    if context.get("login_bottom_back_clicked"):
        issue = _record_issue(
            context["issues"],
            "HUMAN_LOGIN_REQUIRED",
            "S_LOGIN",
            "S_LOGIN has no 稍后 and the bottom back control was already clicked once.",
            snapshot,
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    node = _s_login_bottom_back_node(snapshot)
    if not node or not node.get("bounds"):
        issue = _record_issue(
            context["issues"],
            "HUMAN_LOGIN_REQUIRED",
            "S_LOGIN",
            "S_LOGIN has no 稍后 and no bottom < / ＜ exit control.",
            snapshot,
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context["login_bottom_back_clicked"] = True
    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    action_start = time.perf_counter()
    client.tap(*_center(node["bounds"]))
    action_ms = int((time.perf_counter() - action_start) * 1000)
    time.sleep(0.8)
    next_snapshot = _capture(client, f"{capture_stem}_{_timestamp()}")
    capture_metrics = next_snapshot.get("capture_metrics", {})
    timing.add(
        step_name=step_name,
        page_name="S_LOGIN",
        action_name="S_LOGIN_BOTTOM_BACK_ONCE",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=800,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
    )
    context.setdefault("s_login_actions", []).append(
        {
            "action": "S_LOGIN_BOTTOM_BACK_ONCE",
            "bounds": node["bounds"],
            "screenshot_path": next_snapshot.get("screenshot_path"),
            "xml_path": next_snapshot.get("xml_path"),
            "capture_metrics": capture_metrics,
        }
    )
    return next_snapshot


def _s_login_progress_signature(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    visible_texts = tuple(str(text) for text in snapshot.get("visible_texts", []))
    login_layer_count = sum(
        1
        for text in visible_texts
        if any(keyword in text for keyword in S_LOGIN_HINT_TEXTS) or S_LOGIN_LATER_TEXT in text
    )
    return (
        str(snapshot.get("fresh_xml") or ""),
        _s_login_later_bounds(snapshot),
        login_layer_count,
        visible_texts,
        str(snapshot.get("foreground_package") or ""),
        str(snapshot.get("focused_window") or ""),
    )


def _flow_state_ready(flow_state: dict[str, Any] | None, *keys: str) -> bool:
    return all(bool((flow_state or {}).get(key)) for key in keys)


def _is_s06_s08_overlap_page(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    has_s06_entry = "\u8f66\u578b\u914d\u7f6e" in blob
    has_sort = "\u7efc\u5408\u6392\u5e8f" in blob
    has_year_filter = "\u5e74\u6b3e" in blob
    has_color_filter = "\u989c\u8272" in blob
    has_vehicle_signal = "\u4e07\u516c\u91cc" in blob or "\u516c\u91cc" in blob or bool(_extract_s10_contract_cards(snapshot))
    return has_s06_entry and has_sort and has_year_filter and has_color_filter and has_vehicle_signal


def _looks_like_s07_filter_panel(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    has_filter_tabs = "\u8f66\u6e90\u4eae\u70b9" in blob and "\u989c\u8272" in blob and "\u8f66\u9f84" in blob
    has_panel_footer = "\u91cd\u7f6e" in blob and "\u67e5\u770b" in blob
    has_panel_only_filter = "\u91cc\u7a0b" in blob and "\u5e74\u6b3e/\u8f66\u578b" in blob
    return has_filter_tabs and has_panel_footer and has_panel_only_filter


def _looks_like_s04_series_page(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    has_series_title = "\u9009\u62e9\u8f66\u7cfb" in blob or "\u4e0d\u9650\u8f66\u7cfb" in blob
    has_series_tabs = "\u5168\u90e8" in blob and ("\u8f7f\u8f66" in blob or "SUV" in blob or "\u65b0\u80fd\u6e90" in blob)
    return has_series_title and has_series_tabs


def _recognize_page(recognizer: PageRecognizer, snapshot: dict[str, Any], flow_state: dict[str, Any] | None = None) -> str | None:
    blob = str(snapshot.get("visible_blob") or "")
    if str(snapshot.get("xml_package") or "") == "com.android.systemui":
        return "RUNTIME"
    if _is_s_login_prompt(snapshot):
        return "S_LOGIN"
    if GUAZI_APP_ICON_LABEL in blob and str(snapshot.get("foreground_package") or "") != GUAZI_PACKAGE:
        return "S_APP_ICON"
    if _looks_like_s02_select_page(snapshot):
        for state_id, context in [
            ("S02_SELECT_CAR_TAB", {"current_tab": "选车"}),
            ("S02", {}),
            ("S01", {}),
        ]:
            page = recognizer.recognize(blob, candidate_ids=[state_id], context=context)
            if page:
                return page["id"]
    if _looks_like_s07_filter_panel(snapshot):
        page = recognizer.recognize(blob, candidate_ids=["S07"], context={})
        if page:
            return page["id"]
    if _looks_like_s04_series_page(snapshot):
        page = recognizer.recognize(blob, candidate_ids=["S04"], context={})
        if page:
            return page["id"]
    if _is_s06_s08_overlap_page(snapshot) and not _flow_state_ready(flow_state, "S07_FILTER_DONE"):
        page = recognizer.recognize(blob, candidate_ids=["S06"], context={})
        if page:
            return page["id"]
    candidates = [
        ("S10", {"sorted_by": "price_low_to_high"}),
        ("S09", {}),
        ("S08", {}),
        ("S07", {}),
        ("S06", {}),
        ("S05_TRIM_SELECTED", {}),
        ("S05_MODEL_YEAR_SELECTED", {}),
        ("S05", {}),
        ("S04", {}),
        ("S03", {}),
        ("S02_SELECT_CAR_TAB", {"current_tab": "选车"}),
        ("S02", {}),
        ("S01", {}),
    ]
    for state_id, context in candidates:
        if state_id in {"S08", "S09"} and not _flow_state_ready(flow_state, "S07_FILTER_DONE"):
            continue
        if state_id == "S10" and not _flow_state_ready(flow_state, "S07_FILTER_DONE", "COLOR_FILTER_DONE", "AGE_FILTER_DONE", "SORT_DONE"):
            continue
        if state_id == "S10" and not _looks_like_s10_ready_contract(snapshot):
            continue
        if state_id == "S09" and (_looks_like_s02_select_page(snapshot) or _has_bottom_main_nav(snapshot)):
            continue
        if state_id == "S08" and _looks_like_s02_select_page(snapshot):
            continue
        page = recognizer.recognize(blob, candidate_ids=[state_id], context=context)
        if page:
            return page["id"]
    return None


def _record_issue(issues: IssueRecorder, code: str, state_id: str, message: str, context: dict[str, Any]) -> dict[str, Any]:
    return issues.record(code, state_id, message, context, "manual_review")


def _record_capture_timing(context: dict[str, Any], snapshot: dict[str, Any], *, step_name: str, page_name: str) -> None:
    metrics = snapshot.get("capture_metrics", {})
    timing: TimingRecorder = context["timing"]
    timing.add(
        step_name=step_name,
        page_name=page_name,
        action_name="capture_current_screenshot",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=int(metrics.get("screenshot_ms", 0)),
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=None,
    )
    timing.add(
        step_name=step_name,
        page_name=page_name,
        action_name="dump_current_xml",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=int(metrics.get("xml_ms", 0)),
        transition_wait_ms=0,
        screenshot_path=None,
        xml_path=str(snapshot.get("xml_path") or ""),
    )


def _add_runtime_timing(
    context: dict[str, Any],
    *,
    step_name: str,
    page_name: str,
    action_name: str,
    action_ms: int = 0,
    field_read_ms: int = 0,
    contract_check_ms: int = 0,
    transition_wait_ms: int = 0,
    snapshot: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    timing: TimingRecorder = context["timing"]
    payload = {
        "reason_category": "",
        "reason_detail": "",
        "solution": "",
    }
    if extra:
        payload.update(extra)
    timing.add(
        step_name=step_name,
        page_name=page_name,
        action_name=action_name,
        contract_check_ms=contract_check_ms,
        field_read_ms=field_read_ms,
        action_ms=action_ms,
        transition_wait_ms=transition_wait_ms,
        screenshot_path=str((snapshot or {}).get("screenshot_path") or ""),
        xml_path=str((snapshot or {}).get("xml_path") or ""),
        extra=payload,
    )


def _visible_text_digest(snapshot: dict[str, Any], limit: int = 30) -> list[str]:
    return [str(text) for text in snapshot.get("visible_texts", [])[:limit]]


def _startup_close_x_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        if not node.get("bounds") or not node.get("clickable") or not node.get("enabled"):
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        if labels == {"\u00d7"}:
            candidates.append(node)
    return candidates


def _snapshot_screen_extent(snapshot: dict[str, Any]) -> tuple[int, int]:
    bounds = [node.get("bounds") for node in snapshot.get("nodes", []) if _has_nonzero_bounds(node.get("bounds"))]
    width = max((int(item[2]) for item in bounds), default=0)
    height = max((int(item[3]) for item in bounds), default=0)
    return width, height


def _startup_red_packet_overlay_candidate_nodes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Find the contract-approved startup coupon close target when XML exposes no text label."""
    if snapshot.get("screenshot_missing") or snapshot.get("xml_missing"):
        return []
    if str(snapshot.get("foreground_package") or snapshot.get("xml_package") or "") != GUAZI_PACKAGE:
        return []
    if _startup_close_x_candidates(snapshot):
        return []
    if snapshot.get("visible_texts"):
        return []

    screen_width, screen_height = _snapshot_screen_extent(snapshot)
    if not screen_width or not screen_height:
        return []

    has_large_center_overlay = False
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_nonzero_bounds(bounds) or not node.get("clickable") or not node.get("enabled"):
            continue
        labels = [str(label).strip() for label in node.get("labels", []) if str(label).strip()]
        if labels:
            continue
        x1, y1, x2, y2 = bounds
        width = x2 - x1
        height = y2 - y1
        cx, cy = _center(bounds)
        if (
            width >= screen_width * 0.60
            and height >= screen_height * 0.40
            and screen_width * 0.30 <= cx <= screen_width * 0.70
            and screen_height * 0.30 <= cy <= screen_height * 0.70
        ):
            has_large_center_overlay = True
            break
    if not has_large_center_overlay:
        return []

    candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_nonzero_bounds(bounds) or not node.get("clickable") or not node.get("enabled"):
            continue
        labels = [str(label).strip() for label in node.get("labels", []) if str(label).strip()]
        if labels:
            continue
        x1, y1, x2, y2 = bounds
        width = x2 - x1
        height = y2 - y1
        if width <= 0 or height <= 0:
            continue
        ratio = width / height
        cx, cy = _center(bounds)
        if not (0.70 <= ratio <= 1.35):
            continue
        if not (50 <= width <= 180 and 50 <= height <= 180):
            continue
        if not (screen_width * 0.42 <= cx <= screen_width * 0.58):
            continue
        if not (screen_height * 0.70 <= cy <= screen_height * 0.86):
            continue
        candidates.append(node)
    return candidates


def _startup_red_packet_learning_loop_candidate(
    snapshot: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    current_action_taken: str,
) -> dict[str, Any]:
    return {
        "problem_type": "APP_LAUNCH_MARKETING_OVERLAY_VISIBLE_X_NOT_EXPOSED_IN_XML",
        "trigger_context": "APP_FORCE_RESTART after exact Guazi icon tap; Guazi package is foreground and startup coupon overlay is represented by unlabeled XML nodes.",
        "screenshot_path": snapshot.get("screenshot_path"),
        "xml_path": snapshot.get("xml_path"),
        "visible_text_digest": _visible_text_digest(snapshot),
        "foreground_package": snapshot.get("foreground_package"),
        "focused_window": snapshot.get("focused_window"),
        "activity": snapshot.get("resumed_activity"),
        "candidate_bounds": [node.get("bounds") for node in candidates],
        "candidate_solution": "Only in APP_FORCE_RESTART/S_APP_ICON startup gate, click the unique lower-middle gray circular close target once, then recapture and recognize.",
        "current_action_taken": current_action_taken,
        "whether_auto_rule_added": False,
    }


def _maybe_close_startup_overlay_once(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    state: str | None,
    reason: str,
    capture_stem: str,
) -> tuple[dict[str, Any], str | None, bool]:
    if state in S01_TO_S10_STATES | {"S00", "S_LOGIN", "S_APP_ICON", "RUNTIME"}:
        return snapshot, state, False
    if context.get("startup_overlay_close_x_clicked"):
        return snapshot, state, False

    candidates = _startup_close_x_candidates(snapshot)
    startup = context.setdefault("startup", {})
    startup["startup_overlay_close_x_candidate_count"] = len(candidates)
    if len(candidates) != 1:
        return snapshot, state, False

    node = candidates[0]
    context["startup_overlay_close_x_clicked"] = True
    startup["startup_overlay_close_x_clicked"] = True
    startup["startup_overlay_close_x_reason"] = reason
    startup["startup_overlay_close_x_bounds"] = node.get("bounds")

    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    action_start = time.perf_counter()
    client.tap(*_center(node["bounds"]))
    action_ms = int((time.perf_counter() - action_start) * 1000)
    time.sleep(0.8)
    timing.add(
        step_name="runtime_recover_to_guazi_mainline",
        page_name=state or "UNKNOWN",
        action_name="close_startup_overlay_x_once",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=800,
        screenshot_path=None,
        xml_path=None,
    )

    next_snapshot = _capture(client, f"{capture_stem}_{_timestamp()}")
    next_snapshot["app_entry_mode"] = "force_restart"
    next_snapshot["app_force_restart_reason"] = reason
    _record_capture_timing(context, next_snapshot, step_name="runtime_recover_to_guazi_mainline", page_name="RUNTIME")
    next_state = _recognize_page(context["recognizer"], next_snapshot, context.get("flow_state"))
    startup["startup_overlay_close_x_after_state"] = next_state
    startup["startup_overlay_close_x_after_screenshot_path"] = next_snapshot.get("screenshot_path")
    startup["startup_overlay_close_x_after_xml_path"] = next_snapshot.get("xml_path")
    startup["startup_overlay_close_x_after_visible_text_digest"] = _visible_text_digest(next_snapshot)
    return next_snapshot, next_state, True


def _maybe_close_startup_red_packet_overlay_once(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    state: str | None,
    reason: str,
    capture_stem: str,
) -> tuple[dict[str, Any], str | None, bool]:
    if state in S01_TO_S10_STATES | {"S00", "S_LOGIN", "S_APP_ICON", "RUNTIME"}:
        return snapshot, state, False
    startup = context.setdefault("startup", {})
    candidates = _startup_red_packet_overlay_candidate_nodes(snapshot)
    startup["startup_red_packet_overlay_detected"] = bool(candidates)
    startup["xml_close_x_candidate_count"] = len(_startup_close_x_candidates(snapshot))
    startup["xml_close_x_used"] = False
    startup["visual_red_packet_close_candidate_count"] = len(candidates)
    startup["visual_red_packet_close_candidate_bounds"] = [node.get("bounds") for node in candidates]
    startup.setdefault("visual_red_packet_close_used", False)
    startup.setdefault("overlay_close_click_once_only", True)
    startup.setdefault("clicked_happy_accept", False)
    startup.setdefault("clicked_no_popup_3_days", False)
    startup.setdefault("clicked_bottom_ad_close", False)
    startup["screenshot_before_close"] = snapshot.get("screenshot_path")
    startup["xml_before_close"] = snapshot.get("xml_path")

    if len(candidates) != 1:
        if candidates:
            context.setdefault("learning_loop_candidates", []).append(
                _startup_red_packet_learning_loop_candidate(
                    snapshot,
                    candidates,
                    current_action_taken="stop because startup red packet close target is not unique",
                )
            )
        return snapshot, state, False
    if context.get("startup_red_packet_overlay_close_clicked"):
        return snapshot, state, False

    node = candidates[0]
    click_x, click_y = _center(node["bounds"])
    context["startup_red_packet_overlay_close_clicked"] = True
    startup["startup_red_packet_overlay_detected"] = True
    startup["visual_red_packet_close_used"] = True
    startup["overlay_close_click_x"] = click_x
    startup["overlay_close_click_y"] = click_y
    startup["overlay_close_click_once_only"] = True
    startup["clicked_happy_accept"] = False
    startup["clicked_no_popup_3_days"] = False
    startup["clicked_bottom_ad_close"] = False
    startup["startup_red_packet_overlay_close_reason"] = reason
    candidate = _startup_red_packet_learning_loop_candidate(
        snapshot,
        candidates,
        current_action_taken="fixed runtime gate clicked unique startup coupon gray circular close once",
    )
    startup["learning_loop_candidate_startup_red_packet_overlay"] = candidate
    context.setdefault("learning_loop_candidates", []).append(candidate)

    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    action_start = time.perf_counter()
    client.tap(click_x, click_y)
    action_ms = int((time.perf_counter() - action_start) * 1000)
    time.sleep(0.8)
    timing.add(
        step_name="runtime_recover_to_guazi_mainline",
        page_name=state or "UNKNOWN",
        action_name="close_startup_red_packet_gray_x_once",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=800,
        screenshot_path=None,
        xml_path=None,
    )

    next_snapshot = _capture(client, f"{capture_stem}_{_timestamp()}")
    next_snapshot["app_entry_mode"] = "force_restart"
    next_snapshot["app_force_restart_reason"] = reason
    _record_capture_timing(context, next_snapshot, step_name="runtime_recover_to_guazi_mainline", page_name="RUNTIME")
    next_state = _recognize_page(context["recognizer"], next_snapshot, context.get("flow_state"))
    startup["screenshot_after_close"] = next_snapshot.get("screenshot_path")
    startup["xml_after_close"] = next_snapshot.get("xml_path")
    startup["recognized_page_after_close"] = next_state
    startup["startup_red_packet_overlay_close_after_visible_text_digest"] = _visible_text_digest(next_snapshot)
    return next_snapshot, next_state, True


def _ensure_current_page_contract(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    allowed_states: set[str],
    *,
    action_page: str,
) -> str:
    actual = _recognize_page(context["recognizer"], snapshot, context.get("flow_state"))
    if actual not in allowed_states:
        issue = _record_issue(
            context["issues"],
            "PAGE_CONTRACT_MISMATCH",
            action_page,
            f"{action_page} action blocked because current fresh page contract is {actual or 'UNKNOWN'}.",
            {**snapshot, "expected_allowed_states": sorted(allowed_states), "actual_state": actual},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    return actual


def _current_state_or_stop(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    code: str = "PAGE_CONTRACT_MISMATCH",
    message: str = "Current fresh page does not match an S01-S10 page contract.",
) -> str:
    actual = _recognize_page(context["recognizer"], snapshot, context.get("flow_state"))
    if actual not in S01_TO_S10_STATES:
        issue = _record_issue(
            context["issues"],
            code,
            actual or "UNKNOWN",
            message,
            {**snapshot, "actual_state": actual},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if actual != "S10":
        context.pop("s10_ready_source", None)
    elif not context.get("s10_ready_source"):
        context["s10_ready_source"] = "DIRECT_FRESH_S10"
    return actual


def _ensure_runtime_fresh_evidence(issues: IssueRecorder, snapshot: dict[str, Any], *, state_id: str) -> None:
    if not snapshot.get("xml_missing") and not snapshot.get("screenshot_missing"):
        return
    causes: list[str] = []
    if snapshot.get("runtime_recovery_cause"):
        causes.append(str(snapshot.get("runtime_recovery_cause")))
    if snapshot.get("screenshot_missing"):
        causes.append(str(snapshot.get("screenshot_error") or "SCREENSHOT_CAPTURE_FAILED"))
    if snapshot.get("xml_missing"):
        causes.append(str(snapshot.get("xml_dump_error") or "XML_DUMP_FAILED"))
    snapshot["evidence_missing_cause"] = causes
    issue = _record_issue(
        issues,
        "RUNTIME_FRESH_EVIDENCE_MISSING",
        state_id,
        "Fresh runtime evidence is incomplete; XML or screenshot is missing.",
        snapshot,
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def _notification_shade_visible(snapshot: dict[str, Any]) -> bool:
    return "NotificationShade" in str(snapshot.get("focused_window") or "")


def _launcher_window_visible(snapshot: dict[str, Any]) -> bool:
    focused = str(snapshot.get("focused_window") or "")
    foreground = str(snapshot.get("foreground_package") or "")
    xml_package = str(snapshot.get("xml_package") or "")
    return "launcher" in focused.lower() or foreground.endswith(".launcher") or xml_package.endswith(".launcher")


def _snapshot_label_set(snapshot: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for node in snapshot.get("nodes", []):
        labels.update(str(label).strip() for label in node.get("labels", []) if str(label).strip())
    return labels


def _launcher_account_dialog_detected(snapshot: dict[str, Any]) -> bool:
    if not _launcher_window_visible(snapshot):
        return False
    if str(snapshot.get("foreground_package") or "") == GUAZI_PACKAGE or str(snapshot.get("xml_package") or "") == GUAZI_PACKAGE:
        return False
    labels = _snapshot_label_set(snapshot)
    return all(text in labels for text in LAUNCHER_ACCOUNT_DIALOG_TEXTS)


def _find_launcher_account_later_button(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        labels = {str(label).strip() for label in node.get("labels", [])}
        if labels == {LAUNCHER_ACCOUNT_LATER_TEXT} and node.get("clickable") and node.get("enabled"):
            candidates.append(node)
    if len(candidates) != 1:
        return None
    return candidates[0]


def _launcher_account_learning_loop_candidate(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "problem_type": "LAUNCHER_ACCOUNT_DIALOG_BLOCKS_APP_ICON",
        "trigger_scene": "APP_FORCE_RESTART before Guazi app entry; launcher is ready but account dialog blocks the exact Guazi app icon.",
        "recognition_evidence": {
            "required_texts": list(LAUNCHER_ACCOUNT_DIALOG_TEXTS),
            "screenshot_path": snapshot.get("screenshot_path"),
            "xml_path": snapshot.get("xml_path"),
            "focused_window": snapshot.get("focused_window"),
            "foreground_package": snapshot.get("foreground_package"),
        },
        "candidate_solution": "Only in launcher ready gate, tap 稍后 once, then recapture screenshot/XML/focused_window and search exact 瓜子二手车 icon again.",
        "limits": [
            "do not tap 去登录",
            "do not enter account, phone, or verification code",
            "do not use inside Guazi business pages",
            "do not tap 稍后 repeatedly",
            "do not treat this as a page-contract state",
        ],
    }


def _app_launch_h5_text_delay_suspected(snapshot: dict[str, Any]) -> bool:
    if str(snapshot.get("xml_package") or "") != GUAZI_PACKAGE:
        return False
    if snapshot.get("xml_missing") or snapshot.get("screenshot_missing"):
        return False
    if int(snapshot.get("xml_dump_rc") or snapshot.get("adb_rc") or 0) != 0:
        return False
    return not bool(snapshot.get("visible_texts"))


def _app_launch_h5_text_delay_candidate(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "problem_type": "APP_LAUNCH_H5_TEXT_DELAY",
        "trigger_context": "APP_FORCE_RESTART after exact Guazi icon tap; Guazi package is present but XML has no contract text yet.",
        "screenshot_path": snapshot.get("screenshot_path"),
        "xml_path": snapshot.get("xml_path"),
        "visible_text_digest": _visible_text_digest(snapshot),
        "foreground_package": snapshot.get("foreground_package"),
        "focused_window": snapshot.get("focused_window"),
        "activity": snapshot.get("resumed_activity"),
        "candidate_solution": "Use a bounded APP_LAUNCH_READY_GATE fresh wait: recapture screenshot/XML and recognize current page, without business clicks.",
        "current_action_taken": "bounded fresh wait only",
        "whether_auto_rule_added": False,
    }


def _guazi_icon_visible(snapshot: dict[str, Any]) -> bool:
    for node in snapshot.get("nodes", []):
        labels = {str(label).strip() for label in node.get("labels", [])}
        if GUAZI_APP_ICON_LABEL in labels:
            return True
    return False


def _device_ready_context(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    stop_code: str | None = None,
    failed_action: str | None = None,
) -> dict[str, Any]:
    startup = context.setdefault("startup", {})
    diagnostic = {
        "screen_interactive": snapshot.get("interactive"),
        "keyguard_showing": bool(snapshot.get("keyguard_showing")),
        "secure_keyguard": bool(snapshot.get("keyguard_secure")),
        "focused_window_before": startup.get("focused_window_before"),
        "focused_window_after": snapshot.get("focused_window"),
        "foreground_package_before": startup.get("foreground_package_before"),
        "foreground_package_after": snapshot.get("foreground_package"),
        "notification_shade_visible": _notification_shade_visible(snapshot),
        "wake_screen_done": bool(startup.get("wake_screen_done")),
        "non_secure_swipe_done": bool(startup.get("non_secure_swipe_done")),
        "keyguard_dismissed": bool(startup.get("keyguard_dismissed")),
        "notification_shade_collapsed": bool(startup.get("notification_shade_collapsed")),
        "launcher_visible": bool(startup.get("launcher_visible")),
        "launcher_account_dialog_detected": bool(startup.get("launcher_account_dialog_detected")),
        "launcher_account_dialog_text_digest": startup.get("launcher_account_dialog_text_digest"),
        "later_button_found": startup.get("later_button_found"),
        "later_button_clicked": startup.get("later_button_clicked"),
        "later_click_once_only": startup.get("later_click_once_only"),
        "focused_window_before_later": startup.get("focused_window_before_later"),
        "focused_window_after_later": startup.get("focused_window_after_later"),
        "launcher_visible_after_later": startup.get("launcher_visible_after_later"),
        "guazi_icon_visible_before_later": startup.get("guazi_icon_visible_before_later"),
        "guazi_icon_visible_after_later": startup.get("guazi_icon_visible_after_later"),
        "guazi_icon_visible_final": startup.get("guazi_icon_visible_final", startup.get("guazi_icon_visible")),
        "guazi_icon_visible": bool(startup.get("guazi_icon_visible")),
        "tap_guazi_app_icon_done": bool(startup.get("tap_guazi_app_icon_done")),
        "screenshot_before_later": startup.get("screenshot_before_later"),
        "xml_before_later": startup.get("xml_before_later"),
        "screenshot_after_later": startup.get("screenshot_after_later"),
        "xml_after_later": startup.get("xml_after_later"),
        "final_screenshot_path": startup.get("final_screenshot_path", snapshot.get("screenshot_path")),
        "final_xml_path": startup.get("final_xml_path", snapshot.get("xml_path")),
        "screenshot_path": snapshot.get("screenshot_path"),
        "xml_path": snapshot.get("xml_path"),
        "last_successful_state": context.get("last_successful_state"),
        "failed_action": failed_action,
        "target_fingerprint": context.get("target_fingerprint"),
        "stop_code": stop_code,
    }
    if stop_code in {"NON_SECURE_KEYGUARD_SWIPE_FAILED", "NOTIFICATION_SHADE_STILL_VISIBLE"}:
        diagnostic["learning_loop_candidate"] = {
            "problem_type": "DEVICE_READY_GATE_BLOCKED_BY_KEYGUARD_OR_NOTIFICATION_SHADE",
            "trigger_scene": "APP_FORCE_RESTART before Guazi app entry",
            "evidence": {
                "screenshot_path": snapshot.get("screenshot_path"),
                "xml_path": snapshot.get("xml_path"),
                "focused_window": snapshot.get("focused_window"),
                "foreground_package": snapshot.get("foreground_package"),
                "keyguard_showing": snapshot.get("keyguard_showing"),
                "secure_keyguard": snapshot.get("keyguard_secure"),
            },
            "current_handling": "stop and record issue before any business click",
            "candidate_solution": "If repeated 2-3 times, evaluate a device-ready wait/collapse strategy; not implemented in this run.",
        }
    if stop_code in {
        "APP_ICON_NOT_FOUND_AFTER_DEVICE_READY",
        "LAUNCHER_ACCOUNT_DIALOG_NO_LATER_BUTTON",
        "LAUNCHER_ACCOUNT_DIALOG_STILL_VISIBLE_AFTER_LATER_ONCE",
    } and startup.get("launcher_account_dialog_detected"):
        diagnostic["learning_loop_candidate"] = _launcher_account_learning_loop_candidate(snapshot)
    return {**snapshot, **diagnostic, "startup": dict(startup)}


def _raise_device_ready_gate(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    code: str,
    message: str,
    failed_action: str,
) -> None:
    issue_context = _device_ready_context(context, snapshot, stop_code=code, failed_action=failed_action)
    issue = _record_issue(context["issues"], code, "RUNTIME", message, issue_context)
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def _device_ready_gate_before_app_entry(context: dict[str, Any], *, reason: str) -> dict[str, Any]:
    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    startup = context.setdefault("startup", {})

    wake_started = time.perf_counter()
    wake_result = client.wake_screen_once()
    wake_ms = int((time.perf_counter() - wake_started) * 1000)
    startup["wake_screen_done"] = bool(wake_result.get("wake_success"))
    time.sleep(0.1)
    timing.add(
        step_name="DEVICE_WAKE",
        page_name="RUNTIME",
        action_name="wake_screen",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=wake_ms,
        transition_wait_ms=100,
        screenshot_path=None,
        xml_path=None,
        extra={
            "reason_category": "DEVICE_GATE_SLOW",
            "reason_detail": "wake is followed by a short device-state check instead of an immediate full screenshot/XML dump",
            "solution": "avoid XML dump before deciding whether a non-secure unlock swipe is needed",
        },
    )

    state_started = time.perf_counter()
    snapshot = _device_state_only(client)
    state_ms = int((time.perf_counter() - state_started) * 1000)
    startup["screen_interactive"] = snapshot.get("interactive")
    startup["keyguard_showing"] = bool(snapshot.get("keyguard_showing"))
    startup["secure_keyguard"] = bool(snapshot.get("keyguard_secure"))
    startup["focused_window_before"] = snapshot.get("focused_window")
    startup["foreground_package_before"] = snapshot.get("foreground_package")
    startup["notification_shade_visible"] = _notification_shade_visible(snapshot)
    _add_runtime_timing(
        context,
        step_name="DEVICE_KEYGUARD_CHECK",
        page_name="RUNTIME",
        action_name="read_device_state_once",
        field_read_ms=state_ms,
        snapshot=snapshot,
        extra={
            "keyguard_showing": startup["keyguard_showing"],
            "secure_keyguard": startup["secure_keyguard"],
            "focused_window": startup["focused_window_before"],
            "dumpsys_ms": int((snapshot.get("capture_metrics") or {}).get("dumpsys_ms") or 0),
            "reason_category": "DUMPSYS_STATE_PARSE_SLOW",
            "reason_detail": "device state is read by dumpsys only; screenshot/XML are deferred until evidence is needed",
            "solution": "reuse the same device state before wake/swipe/home changes",
        },
    )

    if snapshot.get("keyguard_showing"):
        if snapshot.get("keyguard_secure"):
            startup["keyguard_dismissed"] = False
            snapshot = _capture(client, f"device_ready_secure_keyguard_{_timestamp()}")
            _record_capture_timing(context, snapshot, step_name="DEVICE_KEYGUARD_CHECK", page_name="RUNTIME")
            _raise_device_ready_gate(
                context,
                snapshot,
                code="SECURE_KEYGUARD_HUMAN_REQUIRED",
                message="Secure keyguard is showing before APP_FORCE_RESTART; human unlock is required.",
                failed_action="device_ready_secure_keyguard_check",
            )

        swipe_started = time.perf_counter()
        swipe_result = client.wake_swipe_once(duration_ms=700)
        swipe_ms = int((time.perf_counter() - swipe_started) * 1000)
        startup["non_secure_swipe_done"] = bool(swipe_result.get("swipe_success"))
        startup["non_secure_swipe"] = swipe_result
        verify_started = time.perf_counter()
        timing.add(
            step_name="DEVICE_NON_SECURE_SWIPE",
            page_name="RUNTIME",
            action_name="non_secure_keyguard_swipe_unlock",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=swipe_ms,
            transition_wait_ms=0,
            screenshot_path=None,
            xml_path=None,
            extra={
                "reason_category": "NON_SECURE_UNLOCK_SWIPE_SLOW",
                "reason_detail": "single allowed non-secure unlock swipe",
                "solution": "verify with short device-state polling before taking a full screenshot/XML evidence capture",
            },
        )
        verify_rounds: list[dict[str, Any]] = []
        for attempt in range(3):
            time.sleep(0.35)
            snapshot = _device_state_only(client)
            verify_rounds.append(
                {
                    "attempt": attempt + 1,
                    "keyguard_showing": bool(snapshot.get("keyguard_showing")),
                    "focused_window": snapshot.get("focused_window"),
                    "foreground_package": snapshot.get("foreground_package"),
                    "dumpsys_ms": int((snapshot.get("capture_metrics") or {}).get("dumpsys_ms") or 0),
                }
            )
            if not snapshot.get("keyguard_showing"):
                break
        verify_ms = int((time.perf_counter() - verify_started) * 1000)
        snapshot = _capture(client, f"device_ready_after_non_secure_swipe_{_timestamp()}")
        _record_capture_timing(context, snapshot, step_name="DEVICE_POST_UNLOCK_VERIFY", page_name="RUNTIME")
        _add_runtime_timing(
            context,
            step_name="DEVICE_POST_UNLOCK_VERIFY",
            page_name="RUNTIME",
            action_name="short_poll_keyguard_dismissed",
            transition_wait_ms=verify_ms,
            snapshot=snapshot,
            extra={
                "poll_rounds": verify_rounds,
                "keyguard_showing": bool(snapshot.get("keyguard_showing")),
                "focused_window": snapshot.get("focused_window"),
                "reason_category": "NON_SECURE_UNLOCK_VERIFY",
                "reason_detail": "short dumpsys-only polling replaces repeated full screenshot/XML captures after the unlock swipe",
                "solution": "stop polling as soon as keyguard_showing=false",
            },
        )
        startup["keyguard_dismissed"] = not bool(snapshot.get("keyguard_showing"))
        startup["keyguard_showing_after_swipe"] = bool(snapshot.get("keyguard_showing"))
        startup["focused_window_after_swipe"] = snapshot.get("focused_window")
        startup["foreground_package_after_swipe"] = snapshot.get("foreground_package")
        if snapshot.get("keyguard_showing"):
            _raise_device_ready_gate(
                context,
                snapshot,
                code="NON_SECURE_KEYGUARD_SWIPE_FAILED",
                message="Non-secure keyguard remained after the single allowed unlock swipe before APP_FORCE_RESTART.",
                failed_action="device_ready_non_secure_keyguard_swipe",
            )

    if _notification_shade_visible(snapshot):
        home_started = time.perf_counter()
        home_result = client.home_key_once()
        home_ms = int((time.perf_counter() - home_started) * 1000)
        startup["notification_shade_collapse_home_done"] = bool(home_result.get("home_success"))
        timing.add(
            step_name="DEVICE_LAUNCHER_READY",
            page_name="RUNTIME",
            action_name="collapse_notification_shade_with_home",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=home_ms,
            transition_wait_ms=0,
            screenshot_path=None,
            xml_path=None,
            extra={
                "reason_category": "NOTIFICATION_SHADE_DELAY",
                "reason_detail": "HOME is issued once, then short device-state polling checks whether NotificationShade is gone",
                "solution": "avoid a fixed 0.5s wait plus immediate XML dump when focused window already changed",
            },
        )
        shade_rounds: list[dict[str, Any]] = []
        for attempt in range(2):
            time.sleep(0.35)
            snapshot = _device_state_only(client)
            shade_rounds.append(
                {
                    "attempt": attempt + 1,
                    "notification_shade_visible": _notification_shade_visible(snapshot),
                    "focused_window": snapshot.get("focused_window"),
                    "foreground_package": snapshot.get("foreground_package"),
                    "dumpsys_ms": int((snapshot.get("capture_metrics") or {}).get("dumpsys_ms") or 0),
                }
            )
            if not _notification_shade_visible(snapshot):
                break
        snapshot = _capture(client, f"device_ready_after_notification_home_{_timestamp()}")
        _record_capture_timing(context, snapshot, step_name="DEVICE_LAUNCHER_READY", page_name="RUNTIME")
        _add_runtime_timing(
            context,
            step_name="DEVICE_LAUNCHER_READY",
            page_name="RUNTIME",
            action_name="short_poll_notification_shade_collapsed",
            transition_wait_ms=350 * len(shade_rounds),
            snapshot=snapshot,
            extra={
                "poll_rounds": shade_rounds,
                "notification_shade_visible": _notification_shade_visible(snapshot),
                "focused_window": snapshot.get("focused_window"),
                "reason_category": "NOTIFICATION_SHADE_DELAY",
                "reason_detail": "notification shade collapse is verified with finite polling and a single final evidence capture",
                "solution": "reuse device-state polling and stop as soon as focused_window leaves NotificationShade",
            },
        )
        startup["notification_shade_collapsed"] = not _notification_shade_visible(snapshot)
        startup["focused_window_after"] = snapshot.get("focused_window")
        startup["foreground_package_after"] = snapshot.get("foreground_package")
        if _notification_shade_visible(snapshot):
            _raise_device_ready_gate(
                context,
                snapshot,
                code="NOTIFICATION_SHADE_STILL_VISIBLE",
                message="NotificationShade remained visible after HOME before APP_FORCE_RESTART.",
                failed_action="device_ready_notification_shade_collapse",
            )
    else:
        startup["notification_shade_collapsed"] = True
        if snapshot.get("xml_missing"):
            snapshot = _capture(client, f"device_ready_launcher_ready_{_timestamp()}")
            _record_capture_timing(context, snapshot, step_name="DEVICE_LAUNCHER_READY", page_name="RUNTIME")
        startup["focused_window_after"] = snapshot.get("focused_window")
        startup["foreground_package_after"] = snapshot.get("foreground_package")

    return snapshot


def _handle_launcher_account_dialog_once(context: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    startup = context.setdefault("startup", {})

    startup.setdefault("launcher_account_dialog_detected", False)
    startup.setdefault("launcher_account_dialog_text_digest", None)
    startup.setdefault("later_button_found", None)
    startup.setdefault("later_button_clicked", False)
    startup.setdefault("later_click_once_only", True)
    startup.setdefault("focused_window_before_later", None)
    startup.setdefault("focused_window_after_later", None)
    startup.setdefault("launcher_visible_after_later", None)
    startup.setdefault("guazi_icon_visible_before_later", None)
    startup.setdefault("guazi_icon_visible_after_later", None)
    startup.setdefault("screenshot_before_later", None)
    startup.setdefault("xml_before_later", None)
    startup.setdefault("screenshot_after_later", None)
    startup.setdefault("xml_after_later", None)

    if not _launcher_account_dialog_detected(snapshot):
        return snapshot

    startup["launcher_account_dialog_detected"] = True
    startup["launcher_account_dialog_text_digest"] = _visible_text_digest(snapshot)
    startup["focused_window_before_later"] = snapshot.get("focused_window")
    startup["screenshot_before_later"] = snapshot.get("screenshot_path")
    startup["xml_before_later"] = snapshot.get("xml_path")
    startup["guazi_icon_visible_before_later"] = _guazi_icon_visible(snapshot)
    startup["learning_loop_candidate_launcher_account_dialog"] = _launcher_account_learning_loop_candidate(snapshot)
    context.setdefault("learning_loop_candidates", []).append(startup["learning_loop_candidate_launcher_account_dialog"])

    if startup.get("launcher_account_later_clicked_once"):
        _raise_device_ready_gate(
            context,
            snapshot,
            code="LAUNCHER_ACCOUNT_DIALOG_STILL_VISIBLE_AFTER_LATER_ONCE",
            message="Launcher account dialog is still visible after the single allowed 稍后 click.",
            failed_action="launcher_account_dialog_later_once",
        )

    later_node = _find_launcher_account_later_button(snapshot)
    startup["later_button_found"] = later_node is not None
    if later_node is None:
        _raise_device_ready_gate(
            context,
            snapshot,
            code="LAUNCHER_ACCOUNT_DIALOG_NO_LATER_BUTTON",
            message="Launcher account dialog blocks Guazi app icon, but exact clickable 稍后 button was not found.",
            failed_action="launcher_account_dialog_find_later",
        )

    click_started = time.perf_counter()
    client.tap(*_center(later_node["bounds"]))
    click_ms = int((time.perf_counter() - click_started) * 1000)
    startup["launcher_account_later_clicked_once"] = True
    startup["later_button_clicked"] = True
    startup["later_click_once_only"] = True
    time.sleep(0.5)
    timing.add(
        step_name="device_ready_gate",
        page_name="RUNTIME",
        action_name="launcher_account_dialog_click_later_once",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=click_ms,
        transition_wait_ms=500,
        screenshot_path=None,
        xml_path=None,
    )

    next_snapshot = _capture(client, f"device_ready_after_launcher_later_{_timestamp()}")
    _record_capture_timing(context, next_snapshot, step_name="device_ready_gate", page_name="RUNTIME")
    startup["focused_window_after_later"] = next_snapshot.get("focused_window")
    startup["foreground_package_after_later"] = next_snapshot.get("foreground_package")
    startup["launcher_visible_after_later"] = _launcher_window_visible(next_snapshot)
    startup["screenshot_after_later"] = next_snapshot.get("screenshot_path")
    startup["xml_after_later"] = next_snapshot.get("xml_path")
    startup["guazi_icon_visible_after_later"] = _guazi_icon_visible(next_snapshot)

    if _launcher_account_dialog_detected(next_snapshot):
        _raise_device_ready_gate(
            context,
            next_snapshot,
            code="LAUNCHER_ACCOUNT_DIALOG_STILL_VISIBLE_AFTER_LATER_ONCE",
            message="Launcher account dialog remained after the single allowed 稍后 click.",
            failed_action="launcher_account_dialog_later_once",
        )
    return next_snapshot


def _app_launch_ready_gate_after_icon(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    state: str | None,
    *,
    reason: str,
) -> tuple[dict[str, Any], str | None]:
    if state in S01_TO_S10_STATES | {"S00", "S_LOGIN", "S_APP_ICON", "RUNTIME"}:
        return snapshot, state
    if not _app_launch_h5_text_delay_suspected(snapshot):
        return snapshot, state

    startup = context.setdefault("startup", {})
    startup["app_launch_ready_gate_called"] = True
    startup["app_launch_ready_gate_reason"] = reason
    startup["h5_text_delay_suspected"] = True
    startup["app_launch_ready_gate_attempts"] = []
    startup["learning_loop_candidate_app_launch_h5_text_delay"] = _app_launch_h5_text_delay_candidate(snapshot)
    context.setdefault("learning_loop_candidates", []).append(startup["learning_loop_candidate_app_launch_h5_text_delay"])

    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    recognizer: PageRecognizer = context["recognizer"]
    current_snapshot = snapshot
    current_state = state
    for attempt in range(1, 4):
        wait_ms = 800
        time.sleep(wait_ms / 1000)
        timing.add(
            step_name="app_launch_ready_gate",
            page_name="RUNTIME",
            action_name=f"wait_h5_text_ready_{attempt}",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=wait_ms,
            screenshot_path=None,
            xml_path=None,
        )
        current_snapshot = _capture(client, f"s01_s10_after_app_launch_ready_wait_{attempt}_{_timestamp()}")
        current_snapshot["app_entry_mode"] = "force_restart"
        current_snapshot["app_force_restart_reason"] = reason
        _record_capture_timing(context, current_snapshot, step_name="app_launch_ready_gate", page_name="RUNTIME")
        current_state = _recognize_page(recognizer, current_snapshot, context.get("flow_state"))
        current_snapshot, current_state, startup_overlay_closed = _maybe_close_startup_overlay_once(
            context,
            current_snapshot,
            state=current_state,
            reason=reason,
            capture_stem=f"s01_s10_after_startup_overlay_close_wait_{attempt}",
        )
        if not startup_overlay_closed:
            current_snapshot, current_state, _ = _maybe_close_startup_red_packet_overlay_once(
                context,
                current_snapshot,
                state=current_state,
                reason=reason,
                capture_stem=f"s01_s10_after_startup_red_packet_close_wait_{attempt}",
            )
        attempt_record = {
            "attempt": attempt,
            "state": current_state,
            "screenshot_path": current_snapshot.get("screenshot_path"),
            "xml_path": current_snapshot.get("xml_path"),
            "visible_text_digest": _visible_text_digest(current_snapshot),
            "xml_package": current_snapshot.get("xml_package"),
            "xml_dump_rc": current_snapshot.get("xml_dump_rc"),
            "xml_dump_stderr": current_snapshot.get("xml_dump_stderr"),
        }
        startup["app_launch_ready_gate_attempts"].append(attempt_record)
        if current_state in S01_TO_S10_STATES | {"S00", "S_LOGIN", "S_APP_ICON", "RUNTIME"}:
            startup["app_launch_ready_gate_resolved"] = True
            startup["app_launch_ready_gate_final_state"] = current_state
            return current_snapshot, current_state
        if not _app_launch_h5_text_delay_suspected(current_snapshot):
            startup["app_launch_ready_gate_resolved"] = False
            startup["app_launch_ready_gate_final_state"] = current_state
            return current_snapshot, current_state

    startup["app_launch_ready_gate_resolved"] = False
    startup["app_launch_ready_gate_final_state"] = current_state
    return current_snapshot, current_state


def _ensure_page(expected: str, recognizer: PageRecognizer, issues: IssueRecorder, snapshot: dict[str, Any]) -> None:
    actual = _recognize_page(recognizer, snapshot)
    if actual != expected:
        issue = _record_issue(
            issues,
            "PAGE_CONTRACT_MISMATCH",
            actual or "UNKNOWN",
            f"Expected {expected}, recognized {actual or 'UNKNOWN'}",
            snapshot,
        )
        lookup = issue.get("knowledge_lookup") or {}
        if not lookup.get("auto_continue_allowed"):
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def _task_params(path: Path | None = None) -> dict[str, Any]:
    target_path = path or TARGET_TASK_DATA_PATH
    data = _current_target_task_data(target_path)
    year_model = str(data.get("year_model") or data.get("model_year") or "")
    registration_date = str(data.get("registration_date") or data.get("registration_date_raw") or data.get("register_date") or "").strip()
    vehicle_year_match = re.search(r"(\d{4})", year_model)
    vehicle_year = int(vehicle_year_match.group(1)) if vehicle_year_match else None
    params = {
        "target_task_path": str(target_path.resolve()),
        "brand": str(data.get("brand") or "").strip(),
        "series": str(data.get("series") or "").strip(),
        "model_year": year_model.strip(),
        "trim": str(data.get("config_model") or data.get("trim") or "").strip(),
        "color": str(data.get("color") or "").strip(),
        "registration_date": registration_date,
        "vehicle_year": vehicle_year,
    }
    register_year = _register_year_from_registration(registration_date)
    current_year = date.today().year
    params["register_year"] = register_year
    params["current_year"] = current_year
    params["target_age_formula"] = "current_year - register_year"
    params["target_age_years"] = _target_age_from_registration(registration_date)
    params["brand_initial"] = _guess_brand_initial(str(params.get("brand") or ""))
    return params


def _current_target_task_data(path: Path | None = None) -> dict[str, Any]:
    target_path = path or TARGET_TASK_DATA_PATH
    if not target_path.exists():
        return {}
    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _register_year_from_registration(registration_date: str) -> int | None:
    match = re.search(r"(\d{4})", registration_date or "")
    if not match:
        return None
    year = int(match.group(1))
    return year


def _target_age_from_registration(registration_date: str, today: date | None = None) -> int | None:
    year = _register_year_from_registration(registration_date)
    if year is None:
        return None
    current = today or date.today()
    age = current.year - year
    return max(age, 0)


def _target_task_output(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "actual_target_task_path": params.get("target_task_path"),
        "brand": params.get("brand"),
        "series": params.get("series"),
        "year_model": params.get("model_year"),
        "config_model": params.get("trim"),
        "color": params.get("color"),
        "register_date": params.get("registration_date"),
        "register_year": params.get("register_year"),
        "current_year": params.get("current_year"),
        "target_age_formula": params.get("target_age_formula"),
        "calculated_target_age": params.get("target_age_years"),
    }


def _target_brand(params: dict[str, Any]) -> str:
    data = _current_target_task_data()
    return str(data.get("brand") or params.get("brand") or "").strip()


def _guess_brand_initial(brand: str) -> str | None:
    brand = (brand or "").strip()
    if not brand:
        return None
    if re.match(r"[A-Za-z]", brand[0]):
        return brand[0].upper()
    common = {
        "\u5927\u4f17": "D",
        "\u4e30\u7530": "F",
        "\u672c\u7530": "B",
        "\u5b9d\u9a6c": "B",
        "\u5954\u9a70": "B",
        "\u5965\u8fea": "A",
        "\u522b\u514b": "B",
        "\u65e5\u4ea7": "R",
        "\u73b0\u4ee3": "X",
        "\u6bd4\u4e9a\u8fea": "B",
        "大众": "D",
        "丰田": "F",
        "本田": "B",
        "宝马": "B",
        "奔驰": "B",
        "奥迪": "A",
        "别克": "B",
        "日产": "R",
        "现代": "X",
        "比亚迪": "B",
    }
    return common.get(brand)


def _target_task_mismatch_result(context: dict[str, Any]) -> dict[str, Any] | None:
    params = context["task_params"]
    expected_path = str(TARGET_TASK_DATA_PATH.resolve())
    if str(params.get("target_task_path") or "") != expected_path:
        result = {
            "metadata": {
                "project": "guazi_app_data_system",
                "mode": "device_real_mainline_s01_to_s10",
                "field_scope": "contract_only",
            },
            "status": "TARGET_TASK_PATH_INVALID",
            "error": "Target task path must be data/current_target_task.json.",
            "target_task": _target_task_output(params),
            "expected_target_task_path": expected_path,
        }
        context["timing"].write()
        _write_result_json(context["configs"], result)
        return result
    missing = [field for field in TARGET_TASK_REQUIRED_FIELDS if not str(params.get(field) or "").strip()]
    if params.get("register_year") is None or params.get("target_age_years") is None:
        missing.append("registration_date_year")
    if missing:
        result = {
            "metadata": {
                "project": "guazi_app_data_system",
                "mode": "device_real_mainline_s01_to_s10",
                "field_scope": "contract_only",
            },
            "status": "TARGET_TASK_FIELD_MISSING",
            "error": "Target task is missing required fields.",
            "target_task": _target_task_output(params),
            "missing_fields": missing,
        }
        context["timing"].write()
        _write_result_json(context["configs"], result)
        return result
    return None


def _recover_to_guazi_page(context: dict[str, Any], reason: str = "startup") -> dict[str, Any]:
    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    timing: TimingRecorder = context["timing"]
    startup = context.setdefault("startup", {})
    startup.update(
        {
            "app_entry_mode": "force_restart",
            "current_page_first_enabled": False,
            "app_force_restart_called": True,
            "force_stop_package": GUAZI_PACKAGE,
            "app_force_restart_reason": reason,
            "initial_capture_before_recovery": False,
            "initial_home_to_launcher_before_capture": True,
            "initial_tap_app_icon_before_capture": True,
            "recovery_called": True,
            "recovery_call_reason": reason,
        }
    )

    _device_ready_gate_before_app_entry(context, reason=reason)
    force_stop_started = time.perf_counter()
    force_stop_result = client.run(["shell", "am", "force-stop", GUAZI_PACKAGE], timeout=20)
    force_stop_ms = int((time.perf_counter() - force_stop_started) * 1000)
    startup["force_stop_done"] = force_stop_result.success
    timing.add(
        step_name="runtime_recover_to_guazi_mainline",
        page_name="RUNTIME",
        action_name="force_stop_guazi_app",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=force_stop_ms,
        transition_wait_ms=0,
        screenshot_path=None,
        xml_path=None,
    )
    home_started = time.perf_counter()
    home_result = client.home_key_once()
    home_ms = int((time.perf_counter() - home_started) * 1000)
    time.sleep(0.3)
    startup["home_to_launcher_done"] = bool(home_result.get("home_success"))
    timing.add(
        step_name="runtime_recover_to_guazi_mainline",
        page_name="RUNTIME",
        action_name="home_to_launcher",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=home_ms,
        transition_wait_ms=300,
        screenshot_path=None,
        xml_path=None,
    )
    launcher_snapshot = _capture(client, f"device_ready_launcher_before_icon_{_timestamp()}")
    _record_capture_timing(context, launcher_snapshot, step_name="DEVICE_LAUNCHER_READY", page_name="RUNTIME")
    _add_runtime_timing(
        context,
        step_name="DEVICE_LAUNCHER_READY",
        page_name="RUNTIME",
        action_name="verify_launcher_and_guazi_icon",
        snapshot=launcher_snapshot,
        extra={
            "keyguard_showing": bool(launcher_snapshot.get("keyguard_showing")),
            "focused_window": launcher_snapshot.get("focused_window"),
            "reason_category": "LAUNCHER_READY_GATE",
            "reason_detail": "launcher XML is captured once after HOME before the exact app-icon lookup",
            "solution": "reuse this launcher XML for dialog/icon checks until a launcher-layer action changes state",
        },
    )
    launcher_snapshot = _handle_launcher_account_dialog_once(context, launcher_snapshot)
    launcher_xml = str(launcher_snapshot.get("fresh_xml") or "")
    startup["launcher_visible"] = _launcher_window_visible(launcher_snapshot)
    startup["guazi_icon_visible"] = _guazi_icon_visible(launcher_snapshot)
    startup["guazi_icon_visible_final"] = startup["guazi_icon_visible"]
    startup["final_screenshot_path"] = launcher_snapshot.get("screenshot_path")
    startup["final_xml_path"] = launcher_snapshot.get("xml_path")
    if not startup["guazi_icon_visible"]:
        _raise_device_ready_gate(
            context,
            launcher_snapshot,
            code="APP_ICON_NOT_FOUND_AFTER_DEVICE_READY",
            message="Launcher is visible after device ready gate, but exact Guazi app icon text was not found.",
            failed_action="device_ready_find_guazi_icon",
        )
    icon_started = time.perf_counter()
    icon_result = client.tap_guazi_app_icon_exact_text(launcher_xml)
    icon_ms = int((time.perf_counter() - icon_started) * 1000)
    startup["tap_guazi_app_icon_done"] = icon_result.success
    timing.add(
        step_name="runtime_recover_to_guazi_mainline",
        page_name="RUNTIME",
        action_name="tap_guazi_app_icon",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=icon_ms,
        transition_wait_ms=0,
        screenshot_path=None,
        xml_path=None,
    )
    wait_ms = 1000 if icon_result.success else 200
    startup["app_restart_wait_ms"] = wait_ms
    time.sleep(wait_ms / 1000)
    timing.add(
        step_name="runtime_recover_to_guazi_mainline",
        page_name="RUNTIME",
        action_name="wait_app_open",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=0,
        transition_wait_ms=wait_ms,
        screenshot_path=None,
        xml_path=None,
    )
    snapshot = _capture(client, f"s01_s10_after_force_restart_{_timestamp()}")
    snapshot["icon_tap_success"] = icon_result.success
    snapshot["icon_tap_error"] = icon_result.stderr or ""
    snapshot["app_entry_mode"] = "force_restart"
    snapshot["app_force_restart_reason"] = reason
    if not icon_result.success:
        snapshot["runtime_recovery_cause"] = "GUAZI_APP_ICON_NOT_FOUND"
    else:
        recovered_state = _recognize_page(recognizer, snapshot)
        if (
            str(snapshot.get("foreground_package") or "") != GUAZI_PACKAGE
            and str(snapshot.get("xml_package") or "") != GUAZI_PACKAGE
            and recovered_state not in S01_TO_S10_STATES | {"S_LOGIN"}
        ):
            snapshot["runtime_recovery_cause"] = "APP_ICON_CLICK_DID_NOT_OPEN_GUAZI"
    after_force_restart_state = _recognize_page(recognizer, snapshot, context.get("flow_state"))
    snapshot, after_force_restart_state, startup_overlay_closed = _maybe_close_startup_overlay_once(
        context,
        snapshot,
        state=after_force_restart_state,
        reason=reason,
        capture_stem="s01_s10_after_startup_overlay_close",
    )
    if not startup_overlay_closed:
        snapshot, after_force_restart_state, _ = _maybe_close_startup_red_packet_overlay_once(
            context,
            snapshot,
            state=after_force_restart_state,
            reason=reason,
            capture_stem="s01_s10_after_startup_red_packet_close",
        )
    snapshot, after_force_restart_state = _app_launch_ready_gate_after_icon(
        context,
        snapshot,
        after_force_restart_state,
        reason=reason,
    )
    startup.update(
        {
            "after_force_restart_state": after_force_restart_state,
            "after_force_restart_screenshot_path": snapshot.get("screenshot_path"),
            "after_force_restart_xml_path": snapshot.get("xml_path"),
            "after_force_restart_visible_text_digest": _visible_text_digest(snapshot),
            "after_recovery_state": after_force_restart_state,
            "after_recovery_screenshot_path": snapshot.get("screenshot_path"),
            "after_recovery_xml_path": snapshot.get("xml_path"),
        }
    )
    capture_metrics = snapshot.get("capture_metrics", {})
    timing.add(
        step_name="runtime_recover_to_guazi_mainline",
        page_name="RUNTIME",
        action_name="capture_runtime_screenshot",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=int(capture_metrics.get("screenshot_ms", 0)),
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=None,
    )
    timing.add(
        step_name="runtime_recover_to_guazi_mainline",
        page_name="RUNTIME",
        action_name="dump_runtime_xml",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=int(capture_metrics.get("xml_ms", 0)),
        transition_wait_ms=0,
        screenshot_path=None,
        xml_path=str(snapshot.get("xml_path") or ""),
    )
    while _recognize_page(recognizer, snapshot) == "S_LOGIN":
        if not _s_login_later_visible(snapshot):
            snapshot = _tap_s_login_bottom_back_once(
                context,
                snapshot,
                capture_stem="s01_s10_after_login_bottom_back",
                step_name="runtime_recover_to_guazi_mainline",
            )
            _ensure_runtime_fresh_evidence(issues, snapshot, state_id="S_LOGIN")
            if _recognize_page(recognizer, snapshot) == "S_LOGIN" and not _s_login_later_visible(snapshot):
                issue = _record_issue(
                    issues,
                    "HUMAN_LOGIN_REQUIRED",
                    "S_LOGIN",
                    "S_LOGIN remained after the single allowed bottom < / ＜ exit click.",
                    snapshot,
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            continue
        before_later_signature = _s_login_progress_signature(snapshot)
        later_started = time.perf_counter()
        client.tap_text(S_LOGIN_LATER_TEXT)
        later_ms = int((time.perf_counter() - later_started) * 1000)
        time.sleep(0.8)
        timing.add(
            step_name="runtime_recover_to_guazi_mainline",
            page_name="S_LOGIN",
            action_name="S_LOGIN_CLICK_LATER_UNTIL_CLOSED",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=later_ms,
            transition_wait_ms=800,
            screenshot_path=None,
            xml_path=None,
        )
        context.setdefault("s_login_actions", []).append({"action": "S_LOGIN_CLICK_LATER_UNTIL_CLOSED"})
        snapshot = _capture(client, f"s01_s10_after_login_later_{_timestamp()}")
        capture_metrics = snapshot.get("capture_metrics", {})
        timing.add(
            step_name="runtime_recover_to_guazi_mainline",
            page_name="RUNTIME",
            action_name="capture_runtime_screenshot",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=int(capture_metrics.get("screenshot_ms", 0)),
            transition_wait_ms=0,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=None,
        )
        timing.add(
            step_name="runtime_recover_to_guazi_mainline",
            page_name="RUNTIME",
            action_name="dump_runtime_xml",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=int(capture_metrics.get("xml_ms", 0)),
            transition_wait_ms=0,
            screenshot_path=None,
            xml_path=str(snapshot.get("xml_path") or ""),
        )
        _ensure_runtime_fresh_evidence(issues, snapshot, state_id="S_LOGIN")
        after_login_state = _recognize_page(recognizer, snapshot)
        if after_login_state in S01_TO_S10_STATES:
            continue
        if after_login_state == "S_LOGIN":
            if _s_login_progress_signature(snapshot) == before_later_signature:
                issue = _record_issue(
                    issues,
                    "S_LOGIN_LATER_NO_PROGRESS",
                    "S_LOGIN",
                    "S_LOGIN 稍后 click did not change XML, bounds, visible texts, foreground, or focused window.",
                    snapshot,
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            continue
        if after_login_state == "S_APP_ICON":
            force_stop_started = time.perf_counter()
            force_stop_result = client.run(["shell", "am", "force-stop", GUAZI_PACKAGE], timeout=20)
            force_stop_ms = int((time.perf_counter() - force_stop_started) * 1000)
            startup = context.setdefault("startup", {})
            startup.update(
                {
                    "app_entry_mode": "force_restart",
                    "current_page_first_enabled": False,
                    "app_force_restart_called": True,
                    "force_stop_package": GUAZI_PACKAGE,
                    "force_stop_done": force_stop_result.success,
                    "app_force_restart_reason": "s_login_app_icon",
                }
            )
            timing.add(
                step_name="runtime_recover_to_guazi_mainline",
                page_name="S_APP_ICON",
                action_name="force_stop_guazi_app_after_login",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=force_stop_ms,
                transition_wait_ms=0,
                screenshot_path=None,
                xml_path=None,
            )
            home_started = time.perf_counter()
            home_result = client.home_key_once()
            home_ms = int((time.perf_counter() - home_started) * 1000)
            startup["home_to_launcher_done"] = bool(home_result.get("home_success"))
            time.sleep(0.3)
            timing.add(
                step_name="runtime_recover_to_guazi_mainline",
                page_name="S_APP_ICON",
                action_name="home_to_launcher_after_login",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=home_ms,
                transition_wait_ms=300,
                screenshot_path=None,
                xml_path=None,
            )
            launcher_xml = client.dump_ui_xml()
            icon_started = time.perf_counter()
            icon_result = client.tap_guazi_app_icon_exact_text(launcher_xml)
            icon_ms = int((time.perf_counter() - icon_started) * 1000)
            startup["tap_guazi_app_icon_done"] = icon_result.success
            timing.add(
                step_name="runtime_recover_to_guazi_mainline",
                page_name="S_APP_ICON",
                action_name="app_force_restart_tap_guazi_app_icon_after_login",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=icon_ms,
                transition_wait_ms=0,
                screenshot_path=None,
                xml_path=None,
            )
            if not icon_result.success:
                issue = _record_issue(
                    issues,
                    "GUAZI_APP_ICON_NOT_FOUND",
                    "S_APP_ICON",
                    "S_APP_ICON page did not expose the 瓜子二手车 icon.",
                    snapshot,
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            time.sleep(1.0)
            timing.add(
                step_name="runtime_recover_to_guazi_mainline",
                page_name="S_APP_ICON",
                action_name="wait_app_open_after_login_icon",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=0,
                transition_wait_ms=1000,
                screenshot_path=None,
                xml_path=None,
            )
            snapshot = _capture(client, f"s01_s10_after_force_restart_login_icon_{_timestamp()}")
            snapshot["app_entry_mode"] = "force_restart"
            snapshot["app_force_restart_reason"] = "s_login_app_icon"
            capture_metrics = snapshot.get("capture_metrics", {})
            timing.add(
                step_name="runtime_recover_to_guazi_mainline",
                page_name="RUNTIME",
                action_name="capture_runtime_screenshot",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=int(capture_metrics.get("screenshot_ms", 0)),
                transition_wait_ms=0,
                screenshot_path=str(snapshot.get("screenshot_path") or ""),
                xml_path=None,
            )
            timing.add(
                step_name="runtime_recover_to_guazi_mainline",
                page_name="RUNTIME",
                action_name="dump_runtime_xml",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=int(capture_metrics.get("xml_ms", 0)),
                transition_wait_ms=0,
                screenshot_path=None,
                xml_path=str(snapshot.get("xml_path") or ""),
            )
            _ensure_runtime_fresh_evidence(issues, snapshot, state_id="S_APP_ICON")
            after_icon_state = _recognize_page(recognizer, snapshot)
            snapshot, after_icon_state, _ = _maybe_close_startup_overlay_once(
                context,
                snapshot,
                state=after_icon_state,
                reason="s_login_app_icon",
                capture_stem="s01_s10_after_login_icon_overlay_close",
            )
            startup.update(
                {
                    "after_force_restart_state": after_icon_state,
                    "after_force_restart_screenshot_path": snapshot.get("screenshot_path"),
                    "after_force_restart_xml_path": snapshot.get("xml_path"),
                    "after_force_restart_visible_text_digest": _visible_text_digest(snapshot),
                    "after_recovery_state": after_icon_state,
                    "after_recovery_screenshot_path": snapshot.get("screenshot_path"),
                    "after_recovery_xml_path": snapshot.get("xml_path"),
                }
            )
            if after_icon_state in S01_TO_S10_STATES:
                continue
            if after_icon_state == "S_APP_ICON":
                issue = _record_issue(
                    issues,
                    "APP_ICON_CLICK_DID_NOT_OPEN_GUAZI",
                    "S_APP_ICON",
                    "Clicking the 瓜子二手车 icon did not open a S01-S10 page.",
                    snapshot,
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            issue = _record_issue(
                issues,
                "PAGE_CONTRACT_MISMATCH",
                after_icon_state or "UNKNOWN",
                f"After S_APP_ICON click, recognized {after_icon_state or 'UNKNOWN'} instead of S01-S10.",
                snapshot,
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        if after_login_state not in S01_TO_S10_STATES:
            issue = _record_issue(
                issues,
                "PAGE_CONTRACT_MISMATCH",
                after_login_state or "UNKNOWN",
                f"After S_LOGIN 稍后 click, recognized {after_login_state or 'UNKNOWN'} instead of S01-S10.",
                snapshot,
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    _ensure_runtime_fresh_evidence(issues, snapshot, state_id="RUNTIME")
    return snapshot


def _dismiss_initial_s_login(context: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    while _recognize_page(recognizer, snapshot, context.get("flow_state")) == "S_LOGIN":
        if not _s_login_later_visible(snapshot):
            next_snapshot = _tap_s_login_bottom_back_once(
                context,
                snapshot,
                capture_stem="s01_s10_initial_after_login_bottom_back",
                step_name="app_force_restart_s_login",
            )
            _record_capture_timing(context, next_snapshot, step_name="app_force_restart_s_login", page_name="S_LOGIN")
            _ensure_runtime_fresh_evidence(issues, next_snapshot, state_id="S_LOGIN")
            if _recognize_page(recognizer, next_snapshot, context.get("flow_state")) == "S_LOGIN" and not _s_login_later_visible(next_snapshot):
                issue = _record_issue(
                    issues,
                    "HUMAN_LOGIN_REQUIRED",
                    "S_LOGIN",
                    "S_LOGIN remained after the single allowed bottom < / ＜ exit click.",
                    next_snapshot,
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            snapshot = next_snapshot
            continue
        before_signature = _s_login_progress_signature(snapshot)
        later_started = time.perf_counter()
        client.tap_text(S_LOGIN_LATER_TEXT)
        later_ms = int((time.perf_counter() - later_started) * 1000)
        time.sleep(0.8)
        context["timing"].add(
            step_name="app_force_restart_s_login",
            page_name="S_LOGIN",
            action_name="S_LOGIN_CLICK_LATER_UNTIL_CLOSED",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=later_ms,
            transition_wait_ms=800,
            screenshot_path=None,
            xml_path=None,
        )
        context.setdefault("s_login_actions", []).append({"action": "S_LOGIN_CLICK_LATER_UNTIL_CLOSED"})
        next_snapshot = _capture(client, f"s01_s10_initial_after_login_later_{_timestamp()}")
        _record_capture_timing(context, next_snapshot, step_name="app_force_restart_s_login", page_name="S_LOGIN")
        _ensure_runtime_fresh_evidence(issues, next_snapshot, state_id="S_LOGIN")
        state = _recognize_page(recognizer, next_snapshot, context.get("flow_state"))
        if state == "S_LOGIN" and _s_login_progress_signature(next_snapshot) == before_signature:
            issue = _record_issue(
                issues,
                "S_LOGIN_LATER_NO_PROGRESS",
                "S_LOGIN",
                "S_LOGIN 稍后 click did not change XML, bounds, visible texts, foreground, or focused window.",
                next_snapshot,
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        snapshot = next_snapshot
    return next_snapshot


def _find_exact(snapshot: dict[str, Any], target: str, *, bottom_only: bool = False) -> dict[str, Any] | None:
    height = 1920
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if bottom_only and bounds and _center(bounds)[1] <= int(height * 0.80):
            continue
        for label in node.get("labels", []):
            if label == target:
                return node
    return None


def _find_contains(snapshot: dict[str, Any], target: str) -> dict[str, Any] | None:
    for node in snapshot.get("nodes", []):
        for label in node.get("labels", []):
            if target in str(label):
                return node
    return None


S05_TRIM_SCROLL_LIMIT = 12
S05_TRIM_SCROLL_DURATION_MS = 850


def _s05_labels(node: dict[str, Any]) -> list[str]:
    return [str(label or "").strip() for label in node.get("labels", []) if str(label or "").strip()]


def _s05_column_split_x(snapshot: dict[str, Any]) -> int | None:
    centers: list[int] = []
    max_x = 0
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_nonzero_bounds(bounds) or not _s05_labels(node):
            continue
        centers.append(_center(bounds)[0])
        max_x = max(max_x, int(bounds[2]))
    centers = sorted(set(centers))
    if len(centers) < 2 or max_x <= 0:
        return None
    gap, left_center, right_center = max(
        ((centers[index + 1] - centers[index], centers[index], centers[index + 1]) for index in range(len(centers) - 1)),
        key=lambda item: item[0],
    )
    if gap < max(1, int(max_x * 0.08)):
        return None
    return (left_center + right_center) // 2


def _s05_node_in_left_year_region(snapshot: dict[str, Any], bounds: tuple[int, int, int, int] | list[int] | None) -> bool:
    if not _has_nonzero_bounds(bounds):
        return False
    split_x = _s05_column_split_x(snapshot)
    return split_x is None or _center(bounds)[0] < split_x


def _s05_node_in_right_trim_region(snapshot: dict[str, Any], bounds: tuple[int, int, int, int] | list[int] | None) -> bool:
    if not _has_nonzero_bounds(bounds):
        return False
    split_x = _s05_column_split_x(snapshot)
    return split_x is not None and _center(bounds)[0] >= split_x


def _s05_normalize_trim_label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\r", " ").replace("\n", " ")).strip()


def _s05_trim_label_matches_target(label: str, target_year: str, target_trim: str) -> bool:
    text = _s05_normalize_trim_label(label)
    trim = _s05_normalize_trim_label(target_trim)
    year = _s05_normalize_trim_label(target_year)
    if not text or not trim:
        return False
    if text == trim:
        return True
    if year and text.startswith(year):
        remainder = _s05_normalize_trim_label(text[len(year) :])
        return remainder == trim
    return False


def _s05_node_from_xml_element(element: ElementTree.Element, *, matched_trim_text: str, strategy: str) -> dict[str, Any]:
    text = str(element.attrib.get("text") or "").strip()
    desc = str(element.attrib.get("content-desc") or "").strip()
    labels = [item for item in [text, desc] if item]
    return {
        "text": text,
        "content_desc": desc,
        "labels": labels,
        "bounds": _parse_bounds(element.attrib.get("bounds", "")),
        "clickable": str(element.attrib.get("clickable") or "") == "true",
        "enabled": str(element.attrib.get("enabled") or "true") == "true",
        "selected": str(element.attrib.get("selected") or "") == "true",
        "package": str(element.attrib.get("package") or ""),
        "class_name": str(element.attrib.get("class") or ""),
        "matched_trim_text": matched_trim_text,
        "trim_click_strategy": strategy,
    }


def _s05_find_target_trim_node_from_xml(snapshot: dict[str, Any], target_year: str, target_trim: str) -> dict[str, Any] | None:
    xml_text = str(snapshot.get("fresh_xml") or "")
    if not xml_text.strip():
        return None
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    parent_map = {child: parent for parent in root.iter() for child in parent}
    for element in root.iter("node"):
        labels = [
            str(element.attrib.get("text") or "").strip(),
            str(element.attrib.get("content-desc") or "").strip(),
        ]
        labels = [label for label in labels if label]
        matched_label = next((label for label in labels if _s05_trim_label_matches_target(label, target_year, target_trim)), "")
        if not matched_label:
            continue
        bounds = _parse_bounds(element.attrib.get("bounds", ""))
        if not _s05_node_in_right_trim_region(snapshot, bounds):
            continue
        current: ElementTree.Element | None = element
        while current is not None:
            current_bounds = _parse_bounds(current.attrib.get("bounds", ""))
            if (
                _s05_node_in_right_trim_region(snapshot, current_bounds)
                and str(current.attrib.get("enabled") or "true") == "true"
                and str(current.attrib.get("clickable") or "") == "true"
            ):
                return _s05_node_from_xml_element(
                    current,
                    matched_trim_text=matched_label,
                    strategy="clickable_trim_ancestor_bounds" if current is not element else "direct_clickable_trim_text_node",
                )
            current = parent_map.get(current)
        return _s05_node_from_xml_element(element, matched_trim_text=matched_label, strategy="direct_trim_text_node_bounds")
    return None


def _s05_find_left_year_node(snapshot: dict[str, Any], target_year: str) -> dict[str, Any] | None:
    target = str(target_year or "").strip()
    if not target:
        return None
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _s05_node_in_left_year_region(snapshot, bounds):
            continue
        for label in _s05_labels(node):
            if label == target:
                return node
    return None


def _s05_right_trim_labels(snapshot: dict[str, Any], target_year: str | None = None) -> list[str]:
    labels: list[str] = []
    year = str(target_year or "").strip()
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _s05_node_in_right_trim_region(snapshot, bounds):
            continue
        for label in _s05_labels(node):
            if year and year not in label:
                continue
            if label not in labels:
                labels.append(label)
    return labels


def _s05_right_list_contains_year(snapshot: dict[str, Any], target_year: str) -> bool:
    target = str(target_year or "").strip()
    if not target:
        return False
    return any(target in label for label in _s05_right_trim_labels(snapshot, target))


def _s05_right_trim_signature(snapshot: dict[str, Any], target_year: str | None = None) -> str:
    return "\n".join(_s05_right_trim_labels(snapshot, target_year))


def _s05_right_trim_nodes(snapshot: dict[str, Any], target_year: str | None = None) -> list[dict[str, Any]]:
    year = str(target_year or "").strip()
    nodes: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _s05_node_in_right_trim_region(snapshot, bounds):
            continue
        labels = _s05_labels(node)
        if not labels:
            continue
        if year and not any(year in label for label in labels):
            continue
        if any(label in {"MINI车型", "确定"} for label in labels):
            continue
        nodes.append(node)
    return nodes


def _s05_right_trim_scroll_bounds(snapshot: dict[str, Any]) -> list[int] | None:
    trim_bounds = [
        node.get("bounds")
        for node in _s05_right_trim_nodes(snapshot)
        if _has_nonzero_bounds(node.get("bounds"))
    ]
    if trim_bounds:
        return [
            min(bounds[0] for bounds in trim_bounds),
            min(bounds[1] for bounds in trim_bounds),
            max(bounds[2] for bounds in trim_bounds),
            max(bounds[3] for bounds in trim_bounds),
        ]
    candidates: list[list[int]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_nonzero_bounds(bounds):
            continue
        if _s05_node_in_right_trim_region(snapshot, bounds) and node.get("scrollable"):
            candidates.append(bounds)
    if candidates:
        return max(candidates, key=lambda item: (item[2] - item[0]) * (item[3] - item[1]))
    right_nodes = [
        node.get("bounds")
        for node in snapshot.get("nodes", [])
        if _s05_node_in_right_trim_region(snapshot, node.get("bounds"))
    ]
    if not right_nodes:
        return None
    return [
        min(bounds[0] for bounds in right_nodes),
        min(bounds[1] for bounds in right_nodes),
        max(bounds[2] for bounds in right_nodes),
        max(bounds[3] for bounds in right_nodes),
    ]


def _s05_right_trim_scroll_command(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    bounds = _s05_right_trim_scroll_bounds(snapshot)
    if not bounds:
        return None
    x1, y1, x2, y2 = bounds
    height = max(1, y2 - y1)
    x = (x1 + x2) // 2
    start_y = y1 + int(height * 0.75)
    end_y = y1 + int(height * 0.30)
    confirm_nodes = [
        node.get("bounds")
        for node in snapshot.get("nodes", [])
        if _has_nonzero_bounds(node.get("bounds")) and any(label == "确定" for label in _s05_labels(node))
    ]
    if confirm_nodes:
        confirm_top = min(bounds[1] for bounds in confirm_nodes)
        start_y = min(start_y, confirm_top - 80)
    start_y = max(y1 + 80, min(start_y, y2 - 120))
    end_y = max(y1 + 80, min(end_y, y2 - 120))
    if start_y <= end_y:
        return None
    return {
        "scroll_bounds": bounds,
        "swipe_x": x,
        "swipe_y_start": start_y,
        "swipe_y_end": end_y,
        "swipe_duration_ms": S05_TRIM_SCROLL_DURATION_MS,
    }


def _scroll_s05_right_trim_list(client: AdbClient, snapshot: dict[str, Any]) -> dict[str, Any]:
    command = _s05_right_trim_scroll_command(snapshot)
    if not command:
        return {"issued": False, "reason": "S05_RIGHT_TRIM_SCROLL_BOUNDS_MISSING"}
    started = time.perf_counter()
    completed = client.run(
        [
            "shell",
            "input",
            "swipe",
            str(command["swipe_x"]),
            str(command["swipe_y_start"]),
            str(command["swipe_x"]),
            str(command["swipe_y_end"]),
            str(command["swipe_duration_ms"]),
        ],
        timeout=20,
    )
    command.update(
        {
            "issued": True,
            "swipe_return_code": completed.returncode,
            "swipe_stdout": (completed.stdout or "").strip(),
            "swipe_stderr": (completed.stderr or "").strip(),
            "swipe_elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
    )
    return command


def _abs_artifact_path(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text)
    if path.is_absolute():
        return path
    return ROOT / path


def _s05_screenshot_region_changed(before_path: str | None, after_path: str | None, bounds: list[int] | tuple[int, int, int, int] | None) -> bool | None:
    if Image is None or ImageChops is None or not bounds:
        return None
    before = _abs_artifact_path(before_path)
    after = _abs_artifact_path(after_path)
    if not before or not after or not before.exists() or not after.exists():
        return None
    try:
        with Image.open(before).convert("RGB") as before_image, Image.open(after).convert("RGB") as after_image:
            if before_image.size != after_image.size:
                return True
            x1, y1, x2, y2 = [int(value) for value in bounds]
            x1 = max(0, min(x1, before_image.size[0]))
            x2 = max(0, min(x2, before_image.size[0]))
            y1 = max(0, min(y1, before_image.size[1]))
            y2 = max(0, min(y2, before_image.size[1]))
            if x2 <= x1 or y2 <= y1:
                return None
            diff = ImageChops.difference(before_image.crop((x1, y1, x2, y2)), after_image.crop((x1, y1, x2, y2)))
            return diff.getbbox() is not None
    except Exception:
        return None


def _s05_make_trim_scroll_attempt(
    *,
    index: int,
    command: dict[str, Any],
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any] | None,
    recognized_page_after_scroll: str | None,
    target_year: str,
) -> dict[str, Any]:
    before_visible = _s05_right_trim_labels(before_snapshot, target_year)
    after_visible = _s05_right_trim_labels(after_snapshot or {}, target_year) if after_snapshot else []
    before_right = [label for label in before_visible if target_year in label]
    after_right = [label for label in after_visible if target_year in label]
    trim_names_changed = before_visible != after_visible
    top_changed = (before_right[0] if before_right else None) != (after_right[0] if after_right else None)
    bottom_changed = (before_right[-1] if before_right else None) != (after_right[-1] if after_right else None)
    screenshot_region_changed = _s05_screenshot_region_changed(
        before_snapshot.get("screenshot_path"),
        (after_snapshot or {}).get("screenshot_path"),
        command.get("scroll_bounds"),
    )
    left_year_still_selected = _s05_find_left_year_node(after_snapshot or {}, target_year) is not None
    scroll_effective = any(
        value is True
        for value in [
            trim_names_changed,
            top_changed,
            bottom_changed,
            screenshot_region_changed,
        ]
    )
    return {
        "scroll_attempt_index": index,
        "swipe_x": command.get("swipe_x"),
        "swipe_y_start": command.get("swipe_y_start"),
        "swipe_y_end": command.get("swipe_y_end"),
        "swipe_duration_ms": command.get("swipe_duration_ms"),
        "swipe_return_code": command.get("swipe_return_code"),
        "swipe_stdout": command.get("swipe_stdout"),
        "swipe_stderr": command.get("swipe_stderr"),
        "swipe_elapsed_ms": command.get("swipe_elapsed_ms"),
        "scroll_bounds": command.get("scroll_bounds"),
        "before_screenshot_path": before_snapshot.get("screenshot_path"),
        "after_screenshot_path": (after_snapshot or {}).get("screenshot_path"),
        "before_xml_path": before_snapshot.get("xml_path"),
        "after_xml_path": (after_snapshot or {}).get("xml_path"),
        "before_visible_trim_names": before_visible,
        "after_visible_trim_names": after_visible,
        "trim_names_changed": trim_names_changed,
        "top_trim_changed": top_changed,
        "bottom_trim_changed": bottom_changed,
        "screenshot_region_changed": screenshot_region_changed,
        "scroll_effective": scroll_effective,
        "recognized_page_after_scroll": recognized_page_after_scroll,
        "left_year_still_selected": left_year_still_selected,
    }


def _s05_find_target_trim_node(snapshot: dict[str, Any], target_year: str, target_trim: str) -> dict[str, Any] | None:
    year = str(target_year or "").strip()
    trim = str(target_trim or "").strip()
    if not trim:
        return None
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _s05_node_in_right_trim_region(snapshot, bounds):
            continue
        for label in _s05_labels(node):
            if not _s05_trim_label_matches_target(label, year, trim):
                continue
            if node.get("enabled", True) and node.get("clickable"):
                return {**node, "matched_trim_text": label, "trim_click_strategy": "direct_clickable_trim_text_node"}
    return _s05_find_target_trim_node_from_xml(snapshot, year, trim)


def _s05_selected_one(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    if "已选1项" in blob:
        return True
    return any(label == "已选1项" for node in snapshot.get("nodes", []) for label in _s05_labels(node))


def _s05_target_trim_selected(snapshot: dict[str, Any], target_year: str, target_trim: str) -> bool:
    trim_node = _s05_find_target_trim_node(snapshot, target_year, target_trim)
    if trim_node and trim_node.get("selected"):
        return True
    return bool(trim_node) and _s05_selected_one(snapshot)


def _s05_find_confirm_node(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    return _find_exact(snapshot, "确定")


def _s05_confirm_clickable(snapshot: dict[str, Any]) -> bool:
    confirm_node = _s05_find_confirm_node(snapshot)
    return bool(confirm_node and confirm_node.get("bounds") and confirm_node.get("clickable"))


def _s03_brand_label_primary(label: str) -> str:
    return str(label or "").replace("\r", "\n").split("\n", 1)[0].strip()


def _find_s03_target_brand(snapshot: dict[str, Any], brand: str) -> dict[str, Any] | None:
    target = str(brand or "").strip()
    if not target:
        return None
    for node in snapshot.get("nodes", []):
        for label in node.get("labels", []):
            if _s03_brand_label_primary(str(label)) == target:
                return node
    return None


def _s03_brand_search_signature(snapshot: dict[str, Any]) -> str:
    labels: list[str] = []
    for node in snapshot.get("nodes", []):
        for label in node.get("labels", []):
            text = str(label).strip()
            if text:
                labels.append(text)
    return "\n".join(labels[:80])


S04_NON_SERIES_LABELS = {
    "S04_OK",
    "本田车系",
    "不限车系",
    "全部",
    "轿车",
    "SUV",
    "MPV",
    "跑车",
    "微面",
    "轻客",
    "新能源",
    "其他类型",
    "车型",
}


def _s04_series_label_primary(label: str) -> str | None:
    text = str(label or "").replace("&#10;", "\n").replace("\r", "\n").strip()
    if not text:
        return None
    primary = text.split("\n", 1)[0].strip()
    if not primary:
        return None
    if primary in S04_NON_SERIES_LABELS:
        return None
    if primary.endswith("车系") or primary.startswith("S04_"):
        return None
    if any(marker in primary for marker in ("在售", "万", "首页", "选车", "卖车", "我的")):
        return None
    return primary


def _extract_s04_visible_series(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(name: str | None, node: dict[str, Any] | None = None) -> None:
        if not name or name in seen:
            return
        seen.add(name)
        visible.append(
            {
                "name": name,
                "node": node,
                "bounds": node.get("bounds") if node else None,
            }
        )

    for node in snapshot.get("nodes", []):
        for label in node.get("labels", []):
            add(_s04_series_label_primary(str(label)), node)
    for label in snapshot.get("visible_texts", []):
        add(_s04_series_label_primary(str(label)), None)
    return visible


def _s04_visible_series_names(snapshot: dict[str, Any]) -> list[str]:
    return [item["name"] for item in _extract_s04_visible_series(snapshot)]


def _record_s04_visible_series(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    direction: str,
    screen_index: int | None = None,
    target_series: str | None = None,
    target_bounds: list[int] | None = None,
    candidate_model_button_bounds: list[list[int]] | None = None,
    selected_model_button_bounds: list[int] | None = None,
    action_taken: str = "inspect",
    seen_series_names: set[str] | None = None,
    bottom_reached: bool = False,
) -> list[str]:
    names = _s04_visible_series_names(snapshot)
    raw_xml = str(snapshot.get("fresh_xml") or "")
    target = str(target_series or "").strip()
    context.setdefault("s04_visible_series_history", []).append(names)
    context.setdefault("s04_search_records", []).append(
        {
            "s04_screen_index": screen_index,
            "direction": direction,
            "raw_xml_contains_target": bool(target and target in raw_xml),
            "visible_series_names": names,
            "target_in_visible_series": bool(target and target in names),
            "target_bounds": target_bounds,
            "candidate_model_button_bounds": candidate_model_button_bounds or [],
            "selected_model_button_bounds": selected_model_button_bounds,
            "action_taken": action_taken,
            "screenshot_path": snapshot.get("screenshot_path"),
            "xml_path": snapshot.get("xml_path"),
            "bottom_reached": bottom_reached,
            "seen_series_names": sorted(seen_series_names or set(names)),
        }
    )
    return names


def _find_s04_series_item(snapshot: dict[str, Any], target_series: str) -> dict[str, Any] | None:
    target = str(target_series or "").strip()
    if not target:
        return None
    return next((item for item in _extract_s04_visible_series(snapshot) if item["name"] == target), None)


def _s04_model_button_candidates(snapshot: dict[str, Any], series_item: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not series_item or not series_item.get("bounds"):
        return []
    sx1, sy1, sx2, sy2 = series_item["bounds"]
    series_center_y = (sy1 + sy2) // 2
    candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not bounds:
            continue
        for label in node.get("labels", []):
            if label == "车型":
                cx1, cy1, cx2, cy2 = bounds
                button_center_y = (cy1 + cy2) // 2
                vertically_overlaps = cy1 <= series_center_y <= cy2 or sy1 <= button_center_y <= sy2
                on_target_right_side = ((cx1 + cx2) // 2) > ((sx1 + sx2) // 2)
                if vertically_overlaps and on_target_right_side:
                    candidates.append(node)
    return candidates


def _find_series_model_button(snapshot: dict[str, Any], target_series: str) -> dict[str, Any] | None:
    series_item = _find_s04_series_item(snapshot, target_series)
    candidates = _s04_model_button_candidates(snapshot, series_item)
    if not series_item or not series_item.get("bounds"):
        return None
    sy1, sy2 = series_item["bounds"][1], series_item["bounds"][3]
    series_center_y = (sy1 + sy2) // 2
    return min(candidates, key=lambda item: abs(_center(item["bounds"])[1] - series_center_y)) if candidates else None


def _s04_series_search_signature(snapshot: dict[str, Any]) -> str:
    return "\n".join(_s04_visible_series_names(snapshot))


def _collect_list_fields(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    texts = [str(item).strip() for item in snapshot.get("visible_texts", []) if str(item).strip()]
    items: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for text in texts:
        price_match = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
        year_match = re.search(r"(20\d{2})", text)
        mileage_match = re.search(r"(\d+(?:\.\d+)?)\s*万公里", text)
        if price_match:
            if current:
                items.append(current)
            current = {"title": text, "list_price_10k": float(price_match.group(1))}
        if year_match:
            current["list_year"] = int(year_match.group(1))
        if mileage_match:
            current["list_mileage_10k_km"] = float(mileage_match.group(1))
    if current:
        items.append(current)
    return items


def _has_bottom_main_nav(snapshot: dict[str, Any]) -> bool:
    labels = {str(text).strip() for text in snapshot.get("visible_texts", []) if str(text).strip()}
    return {"首页", "选车", "卖车", "新能源", "我的"}.issubset(labels)


def _looks_like_s02_select_page(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    labels = {str(text).strip() for text in snapshot.get("visible_texts", []) if str(text).strip()}
    return _has_bottom_main_nav(snapshot) and (
        "品牌" in labels
        or "品牌" in blob
        or "搜索" in labels
        or "搜索" in blob
        or "品牌选车" in labels
        or "AI选车" in labels
    )


def _has_pre_sort_control(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    labels = {str(text).strip() for text in snapshot.get("visible_texts", []) if str(text).strip()}
    return "综合排序" in blob or "综合排序" in labels


def _has_price_low_to_high_sort(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    labels = {str(text).strip() for text in snapshot.get("visible_texts", []) if str(text).strip()}
    return "价格从低到高" in blob or "价格从低到高" in labels


def _extract_s10_contract_cards(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    texts = [str(item).strip() for item in snapshot.get("visible_texts", []) if str(item).strip()]
    invalid_price_markers = ("首付", "月供", "贷款", "价格区间", "以下", "以上", "万公里")
    prices: list[float] = []
    year_mileages: list[tuple[str, int, float]] = []
    for text in texts:
        if not any(marker in text for marker in invalid_price_markers):
            price_match = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
            if price_match:
                prices.append(float(price_match.group(1)))
        year_mileage = re.search(r"(20\d{2})年\s*[|｜]\s*(\d+(?:\.\d+)?)万公里", text)
        if year_mileage:
            year_mileages.append((text, int(year_mileage.group(1)), float(year_mileage.group(2))))
    cards: list[dict[str, Any]] = []
    for index, (title, year, mileage) in enumerate(year_mileages[: len(prices)]):
        cards.append(
            {
                "title": title,
                "list_price_10k": prices[index],
                "list_year": year,
                "list_mileage_10k_km": mileage,
            }
        )
    return cards


def _looks_like_s10_ready_contract(snapshot: dict[str, Any]) -> bool:
    return (
        not _has_bottom_main_nav(snapshot)
        and not _has_pre_sort_control(snapshot)
        and _has_price_low_to_high_sort(snapshot)
        and bool(_extract_s10_contract_cards(snapshot))
    )


def _assert_s10_ready_contract(issues: IssueRecorder, snapshot: dict[str, Any], *, source: str | None, flow_state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    required_flow = ("S07_FILTER_DONE", "COLOR_FILTER_DONE", "AGE_FILTER_DONE", "SORT_DONE")
    if not _flow_state_ready(flow_state, *required_flow):
        issue = _record_issue(
            issues,
            "S10_READY_BLOCKED_BEFORE_FILTER_OR_SORT_DONE",
            "S10",
            "S10_READY is blocked until S07 color/age filters and sorting are completed.",
            {**snapshot, "flow_state": dict(flow_state or {}), "missing_flow_state": [key for key in required_flow if not (flow_state or {}).get(key)]},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if source not in {"DIRECT_FRESH_S10", "S08_SINGLE_OR_NO_SORT", "S09_PRICE_LOW_TO_HIGH"}:
        issue = _record_issue(
            issues,
            "PAGE_CONTRACT_MISMATCH",
            "S10",
            "S10_READY rejected because the state machine has not reached an allowed S10 source.",
            snapshot,
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if _has_bottom_main_nav(snapshot):
        issue = _record_issue(
            issues,
            "PAGE_CONTRACT_MISMATCH",
            "S10",
            "S10_READY rejected because current fresh page contains bottom main navigation.",
            snapshot,
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if _has_pre_sort_control(snapshot):
        issue = _record_issue(
            issues,
            "PAGE_CONTRACT_MISMATCH",
            "S10",
            "S10_READY rejected because S08 综合排序 is still visible before sorting is complete.",
            snapshot,
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if source != "S08_SINGLE_OR_NO_SORT" and not _has_price_low_to_high_sort(snapshot):
        issue = _record_issue(
            issues,
            "PAGE_CONTRACT_MISMATCH",
            "S10",
            "S10_READY rejected because price low-to-high sorting is not confirmed in current fresh evidence.",
            snapshot,
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    cards = _extract_s10_contract_cards(snapshot)
    if not cards:
        issue = _record_issue(
            issues,
            "PAGE_CONTRACT_MISMATCH",
            "S10",
            "S10_READY requires vehicle card price, year, and mileage in current fresh evidence.",
            snapshot,
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    return cards


def _target_color_labels(target_color: str) -> list[str]:
    color = str(target_color or "").strip()
    if not color:
        return []
    labels = [color]
    if len(color) == 1 and color not in {"\u5176"}:
        labels.append(f"{color}\u8272")
    return list(dict.fromkeys(labels))


def _node_from_xml_element(element: ElementTree.Element, *, matched_color_text: str, strategy: str) -> dict[str, Any]:
    text = str(element.attrib.get("text") or "").strip()
    desc = str(element.attrib.get("content-desc") or "").strip()
    labels = [item for item in [text, desc] if item]
    node = {
        "text": text,
        "content_desc": desc,
        "labels": labels,
        "bounds": _parse_bounds(element.attrib.get("bounds", "")),
        "clickable": str(element.attrib.get("clickable") or "") == "true",
        "enabled": str(element.attrib.get("enabled") or "true") == "true",
        "selected": str(element.attrib.get("selected") or "") == "true",
        "package": str(element.attrib.get("package") or ""),
        "class_name": str(element.attrib.get("class") or ""),
        "matched_color_text": matched_color_text,
        "color_click_strategy": strategy,
    }
    return node


def _find_target_color_node(snapshot: dict[str, Any], target_color: str) -> dict[str, Any] | None:
    target_labels = set(_target_color_labels(target_color))
    for node in snapshot.get("nodes", []):
        if not node.get("bounds"):
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        matched = labels & target_labels
        if matched and node.get("enabled", True) and node.get("clickable"):
            return {
                **node,
                "matched_color_text": sorted(matched)[0],
                "color_click_strategy": "direct_clickable_color_text_node",
            }
    xml_text = str(snapshot.get("fresh_xml") or "")
    if not xml_text.strip():
        return None
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    parent_map = {child: parent for parent in root.iter() for child in parent}
    for element in root.iter("node"):
        labels = {
            str(element.attrib.get("text") or "").strip(),
            str(element.attrib.get("content-desc") or "").strip(),
        }
        labels.discard("")
        matched = labels & target_labels
        if not matched:
            continue
        bounds = _parse_bounds(element.attrib.get("bounds", ""))
        if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        matched_text = sorted(matched)[0]
        current: ElementTree.Element | None = element
        while current is not None:
            current_bounds = _parse_bounds(current.attrib.get("bounds", ""))
            if (
                current_bounds
                and current_bounds[2] > current_bounds[0]
                and current_bounds[3] > current_bounds[1]
                and str(current.attrib.get("enabled") or "true") == "true"
                and str(current.attrib.get("clickable") or "") == "true"
            ):
                return _node_from_xml_element(
                    current,
                    matched_color_text=matched_text,
                    strategy="clickable_color_ancestor_bounds" if current is not element else "direct_clickable_color_text_node",
                )
            current = parent_map.get(current)
        return _node_from_xml_element(
            element,
            matched_color_text=matched_text,
            strategy="direct_color_text_node_bounds",
        )
    return None


def _wait_for_s07_target_color_node(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    target_color: str,
    stem_prefix: str,
    *,
    rounds: int = 4,
    interval_s: float = 0.3,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    client: AdbClient = context["client"]
    for index in range(rounds):
        node = _find_target_color_node(snapshot, target_color)
        if node is not None and node.get("bounds"):
            return snapshot, node
        time.sleep(interval_s)
        snapshot = _capture(client, f"{stem_prefix}_{index}_{_timestamp()}")
        _ensure_current_page_contract(context, snapshot, {"S07"}, action_page="S07")
    return snapshot, _find_target_color_node(snapshot, target_color)


def _wait_for_s07_target_color_selected(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    target_color: str,
    stem_prefix: str,
    *,
    rounds: int = 4,
    interval_s: float = 0.3,
) -> dict[str, Any]:
    client: AdbClient = context["client"]
    for index in range(rounds):
        time.sleep(interval_s)
        snapshot = _capture(client, f"{stem_prefix}_{index}_{_timestamp()}")
        _ensure_current_page_contract(context, snapshot, {"S07"}, action_page="S07")
        if _target_color_selected(snapshot, target_color):
            return snapshot
    return snapshot


def _target_color_selected(snapshot: dict[str, Any], target_color: str) -> bool:
    target_labels = set(_target_color_labels(target_color))
    for node in snapshot.get("nodes", []):
        labels = {str(label).strip() for label in node.get("labels", [])}
        if labels & target_labels and node.get("selected") is True:
            return True
    blob = str(snapshot.get("visible_blob") or "")
    return any(label in blob for label in target_labels)


def _exact_age_confirmed(snapshot: dict[str, Any], target_age: int | None) -> bool:
    if target_age is None:
        return False
    blob = str(snapshot.get("visible_blob") or "").replace(" ", "")
    if f"{target_age}-{target_age}年" in blob:
        return True
    target_label = str(target_age)
    for node in snapshot.get("nodes", []):
        labels = {str(label).strip() for label in node.get("labels", [])}
        if target_label in labels and node.get("selected") is True:
            return True
    return False


def _snapshot_screen_height(snapshot: dict[str, Any]) -> int:
    heights: list[int] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if _has_nonzero_bounds(bounds):
            heights.append(int(bounds[3]))
    return max(heights, default=1920)


def _find_s07_view_cars_button(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Find the S07 bottom contract button like 查看1辆, not top 查看更多 links."""
    height = _snapshot_screen_height(snapshot)
    min_center_y = int(height * 0.70)
    view_pattern = re.compile(r"^\u67e5\u770b\s*\d+\s*\u8f86$")
    candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_nonzero_bounds(bounds):
            continue
        if not node.get("clickable") or not node.get("enabled"):
            continue
        if _center(bounds)[1] < min_center_y:
            continue
        labels = {str(label).strip().replace(" ", "") for label in node.get("labels", [])}
        if any(view_pattern.match(label) for label in labels):
            candidates.append(node)
    if not candidates:
        return None
    return max(candidates, key=lambda item: ((item["bounds"][2] - item["bounds"][0]), _center(item["bounds"])[1]))


def _s07_view_cars_button_text(node: dict[str, Any] | None) -> str | None:
    if not node:
        return None
    view_pattern = re.compile(r"^\u67e5\u770b\s*\d+\s*\u8f86$")
    for label in node.get("labels", []):
        text = str(label).strip().replace(" ", "")
        if view_pattern.match(text):
            return text
    return None


def _s07_view_cars_count(node: dict[str, Any] | None) -> int | None:
    text = _s07_view_cars_button_text(node)
    if not text:
        return None
    match = re.search(r"\u67e5\u770b\s*(\d+)\s*\u8f86", text)
    return int(match.group(1)) if match else None


def _manual_pricing_required_result(context: dict[str, Any], snapshot: dict[str, Any], view_node: dict[str, Any]) -> dict[str, Any]:
    params = context["task_params"]
    flow_state = context.setdefault("flow_state", {})
    flow_state["S07_FILTER_DONE"] = False
    flow_state["SORT_DONE"] = False
    flow_state["S10_READY"] = False
    button_text = _s07_view_cars_button_text(view_node) or ""
    reason = (
        "\u76ee\u6807\u8f66\u578b\u914d\u7f6e + \u76ee\u6807\u989c\u8272 + \u76ee\u6807\u8f66\u9f84\u7b5b\u9009\u540e\uff0c"
        "\u74dc\u5b50 APP \u5e95\u90e8\u663e\u793a\u201c\u67e5\u770b0\u8f86\u201d\uff0c\u65e0\u4e09\u540c\u8f66\u6e90\uff0c"
        "\u9700\u4eba\u5de5\u5b9a\u4ef7 / \u5f85\u4eba\u5de5\u5ba1\u6838\u3002"
    )
    evidence = {
        "target_task_path": params.get("target_task_path"),
        "brand": params.get("brand"),
        "series": params.get("series"),
        "year_model": params.get("model_year"),
        "config_model": params.get("trim"),
        "color": params.get("color"),
        "register_date": params.get("registration_date"),
        "register_year": params.get("register_year"),
        "current_year": params.get("current_year"),
        "target_age": params.get("target_age_years"),
        "COLOR_FILTER_DONE": bool(flow_state.get("COLOR_FILTER_DONE")),
        "AGE_FILTER_DONE": bool(flow_state.get("AGE_FILTER_DONE")),
        "S07_FILTER_DONE": False,
        "SORT_DONE": False,
        "S10_READY": False,
        "s07_bottom_button_text": button_text,
        "color_evidence": _target_color_selected(snapshot, str(params.get("color") or "")),
        "age_evidence": _exact_age_confirmed(snapshot, params.get("target_age_years")),
        "screenshot_path": snapshot.get("screenshot_path"),
        "xml_path": snapshot.get("xml_path"),
        "visible_text_digest": _visible_text_digest(snapshot),
        "foreground_package": snapshot.get("foreground_package"),
        "activity": snapshot.get("resumed_activity"),
    }
    result = {
        "metadata": {
            "project": "guazi_app_data_system",
            "mode": "device_real_mainline_s01_to_s10",
            "field_scope": "contract_only",
        },
        "status": "MANUAL_PRICING_REQUIRED",
        "final_status": "MANUAL_PRICING_REQUIRED",
        "business_result": "NO_MATCHING_SOURCE_AFTER_S07_FILTER",
        "manual_pricing_reason": reason,
        "target_task": _target_task_output(params),
        "flow_state": dict(flow_state),
        "startup": dict(context.get("startup", {})),
        "manual_pricing_evidence": evidence,
        "issues": [],
    }
    context["manual_pricing_result"] = result
    _write_result_json(context["configs"], result)
    return result


def _find_s07_age_tick_node(snapshot: dict[str, Any], target_age: int | None) -> dict[str, Any] | None:
    if target_age is None:
        return None
    target_label = str(target_age)
    candidates: list[dict[str, Any]] = []
    for node in _s07_age_tick_nodes(snapshot):
        bounds = node.get("bounds")
        if not _has_nonzero_bounds(bounds):
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        if target_label in labels:
            candidates.append(node)
    return max(candidates, key=lambda item: _center(item["bounds"])[0]) if candidates else None


def _s07_age_tick_nodes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[list[dict[str, Any]]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_nonzero_bounds(bounds):
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        if not labels & {"0", "2", "4", "6", "8", "10", "\u4e0d\u9650"}:
            continue
        center_y = _center(bounds)[1]
        for row in rows:
            row_y = _center(row[0]["bounds"])[1]
            if abs(center_y - row_y) <= 80:
                row.append(node)
                break
        else:
            rows.append([node])
    if not rows:
        return []

    def row_score(row: list[dict[str, Any]]) -> tuple[int, int]:
        labels = {str(label).strip() for item in row for label in item.get("labels", [])}
        age_labels = {"0", "2", "4", "6", "8", "10", "\u4e0d\u9650"}
        mileage_only_labels = {"3", "9", "12"}
        return (len(labels & age_labels) - len(labels & mileage_only_labels), -_center(row[0]["bounds"])[1])

    best = max(rows, key=row_score)
    return sorted(best, key=lambda item: _center(item["bounds"])[0])


def _s07_age_target_point(snapshot: dict[str, Any], target_age: int | None) -> tuple[int, int] | None:
    if target_age is None:
        return None
    numeric_points: list[tuple[int, int, int]] = []
    for node in _s07_age_tick_nodes(snapshot):
        bounds = node.get("bounds")
        if not _has_nonzero_bounds(bounds):
            continue
        for label in node.get("labels", []):
            text = str(label).strip()
            if text.isdigit():
                x, y = _center(bounds)
                numeric_points.append((int(text), x, y))
                break
    numeric_points = sorted(set(numeric_points), key=lambda item: item[0])
    if not numeric_points:
        return None
    for age, x, y in numeric_points:
        if age == target_age:
            return x, y
    for left, right in zip(numeric_points, numeric_points[1:]):
        left_age, left_x, left_y = left
        right_age, right_x, right_y = right
        if left_age < target_age < right_age:
            ratio = (target_age - left_age) / (right_age - left_age)
            x = round(left_x + (right_x - left_x) * ratio)
            y = round((left_y + right_y) / 2)
            return x, y
    return None


def _set_exact_age_from_ticks(client: AdbClient, snapshot: dict[str, Any], target_age: int | None) -> bool:
    tick_nodes = _s07_age_tick_nodes(snapshot)
    target_point = _s07_age_target_point(snapshot, target_age)
    if target_point is None or not tick_nodes:
        return False
    target_x, target_y = target_point
    centers = [_center(node["bounds"]) for node in tick_nodes]
    left_x = min(x for x, _ in centers)
    right_x = max(x for x, _ in centers)
    y = target_y
    client.run(["shell", "input", "swipe", str(right_x), str(y), str(target_x), str(y), "450"], timeout=20)
    time.sleep(0.25)
    client.run(["shell", "input", "swipe", str(left_x), str(y), str(target_x), str(y), "450"], timeout=20)
    time.sleep(0.25)
    client.tap(target_x, target_y)
    return True


def _require_s07_filters_done(context: dict[str, Any], snapshot: dict[str, Any], *, code: str, state_id: str) -> None:
    flow_state = context.setdefault("flow_state", {})
    required = ("S07_FILTER_DONE", "COLOR_FILTER_DONE", "AGE_FILTER_DONE")
    if _flow_state_ready(flow_state, *required):
        return
    issue = _record_issue(
        context["issues"],
        code,
        state_id,
        "Sorting or downstream readiness is blocked until S07 color and exact age filters are completed.",
        {**snapshot, "flow_state": dict(flow_state), "missing_flow_state": [key for key in required if not flow_state.get(key)]},
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def _require_s10_ready_flow_done(context: dict[str, Any], snapshot: dict[str, Any]) -> None:
    flow_state = context.setdefault("flow_state", {})
    required = ("S07_FILTER_DONE", "COLOR_FILTER_DONE", "AGE_FILTER_DONE", "SORT_DONE")
    if _flow_state_ready(flow_state, *required):
        return
    issue = _record_issue(
        context["issues"],
        "S10_READY_BLOCKED_BEFORE_FILTER_OR_SORT_DONE",
        "S10",
        "S10_READY is blocked until S07 color/age filters and sorting are completed.",
        {**snapshot, "flow_state": dict(flow_state), "missing_flow_state": [key for key in required if not flow_state.get(key)]},
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def handle_s01(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    client: AdbClient = context["client"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]

    started = time.perf_counter()
    _ensure_current_page_contract(context, snapshot, {"S01"}, action_page="S01")
    machine.assert_action_allowed("S01", "click_bottom_select_car_tab")
    action_start = time.perf_counter()
    result = client.tap_s01_bottom_select_car_tab(str(snapshot.get("fresh_xml") or ""))
    action_ms = int((time.perf_counter() - action_start) * 1000)
    if not result.get("success"):
        issue = _record_issue(issues, "S01_SELECT_TAB_CLICK_FAILED", "S01", "Failed to tap bottom select-car tab safely.", {**snapshot, **result})
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    time.sleep(0.8)
    next_snapshot = _capture(client, f"s01_to_s02_{_timestamp()}")
    timing.add(
        step_name="s01_select_car_tab",
        page_name="S01",
        action_name="click_bottom_select_car_tab",
        contract_check_ms=int((action_start - started) * 1000),
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=800,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
    )
    return "S02", next_snapshot


def handle_s02(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    client: AdbClient = context["client"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]

    started = time.perf_counter()
    _ensure_current_page_contract(context, snapshot, {"S02", "S02_SELECT_CAR_TAB"}, action_page="S02")
    machine.assert_action_allowed("S02", "tap_brand_filter")
    brand_node = _find_exact(snapshot, "品牌")
    if not brand_node or not brand_node.get("bounds"):
        issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S02", "Brand entry not found.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    action_start = time.perf_counter()
    client.tap(*_center(brand_node["bounds"]))
    action_ms = int((time.perf_counter() - action_start) * 1000)
    time.sleep(0.8)
    next_snapshot = _capture(client, f"s02_to_s03_{_timestamp()}")
    timing.add(
        step_name="s02_open_brand_select",
        page_name="S02",
        action_name="tap_brand_filter",
        contract_check_ms=int((action_start - started) * 1000),
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=800,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
    )
    return "S03", next_snapshot


def handle_s03(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    client: AdbClient = context["client"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    params = context["task_params"]

    started = time.perf_counter()
    _ensure_current_page_contract(context, snapshot, {"S03"}, action_page="S03")
    action_started = time.perf_counter()
    transition_wait_ms = 0
    brand = _target_brand(params)
    brand_initial = _guess_brand_initial(brand) or params.get("brand_initial")
    if not brand:
        issue = _record_issue(issues, "TARGET_BRAND_NOT_FOUND_IN_S03", "S03", "Target brand is empty for S03 brand selection.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if brand_initial:
        machine.assert_action_allowed("S03", "tap_brand_letter")
        letter_node = _find_exact(snapshot, str(brand_initial))
        if letter_node and letter_node.get("bounds"):
            client.tap(*_center(letter_node["bounds"]))
            time.sleep(0.3)
            transition_wait_ms += 300
            snapshot = _capture(client, f"s03_after_letter_{_timestamp()}")
            _ensure_current_page_contract(context, snapshot, {"S03"}, action_page="S03")
    brand_node = _find_s03_target_brand(snapshot, brand)
    seen_signatures = {_s03_brand_search_signature(snapshot)}
    scroll_attempts = 0
    while (brand_node is None or not brand_node.get("bounds")) and scroll_attempts < S03_BRAND_SCROLL_LIMIT:
        machine.assert_action_allowed("S03", "scroll_brand_list")
        client.swipe("up")
        scroll_attempts += 1
        time.sleep(0.35)
        transition_wait_ms += 350
        snapshot = _capture(client, f"s03_brand_scroll_{scroll_attempts}_{_timestamp()}")
        _ensure_current_page_contract(context, snapshot, {"S03"}, action_page="S03")
        brand_node = _find_s03_target_brand(snapshot, brand)
        signature = _s03_brand_search_signature(snapshot)
        if brand_node and brand_node.get("bounds"):
            break
        if signature in seen_signatures:
            break
        seen_signatures.add(signature)
    machine.assert_action_allowed("S03", "tap_target_brand")
    if brand_node is None or not brand_node.get("bounds"):
        issue = _record_issue(issues, "TARGET_BRAND_NOT_FOUND_IN_S03", "S03", f"Target brand {brand} not found in S03 brand page.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    client.tap(*_center(brand_node["bounds"]))
    action_ms = int((time.perf_counter() - action_started) * 1000)
    time.sleep(0.8)
    transition_wait_ms += 800
    next_snapshot = _capture(client, f"s03_to_s04_{_timestamp()}")
    timing.add(
        step_name="s03_select_target_brand",
        page_name="S03",
        action_name="tap_brand_letter_scroll_s03_then_tap_target_brand",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=transition_wait_ms,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
    )
    return "S04", next_snapshot


def handle_s04(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    client: AdbClient = context["client"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    params = context["task_params"]

    started = time.perf_counter()
    _ensure_current_page_contract(context, snapshot, {"S04"}, action_page="S04")
    machine.assert_action_allowed("S04", "click_series_model_button")
    target_series = str(params.get("series") or "")
    transition_wait_ms = 0
    seen_series_names: set[str] = set()
    previous_visible_series_names: list[str] | None = None
    down_unchanged_count = 0
    screen_index = 0
    bottom_reached = False

    while True:
        _ensure_current_page_contract(context, snapshot, {"S04"}, action_page="S04")
        raw_xml_contains_target = bool(target_series and target_series in str(snapshot.get("fresh_xml") or ""))
        visible_series_names = _s04_visible_series_names(snapshot)
        target_in_visible_series = target_series in visible_series_names if target_series else False
        series_item = _find_s04_series_item(snapshot, target_series)
        target_bounds = series_item.get("bounds") if series_item else None
        candidate_buttons = _s04_model_button_candidates(snapshot, series_item)
        selected_button = _find_series_model_button(snapshot, target_series)
        candidate_bounds = [button["bounds"] for button in candidate_buttons if button.get("bounds")]
        selected_bounds = selected_button.get("bounds") if selected_button else None

        if raw_xml_contains_target and not target_in_visible_series:
            _record_s04_visible_series(
                context,
                snapshot,
                direction="down",
                screen_index=screen_index,
                target_series=target_series,
                target_bounds=target_bounds,
                candidate_model_button_bounds=candidate_bounds,
                selected_model_button_bounds=selected_bounds,
                action_taken="stop_extraction_failed",
                seen_series_names=seen_series_names,
                bottom_reached=bottom_reached,
            )
            issue = _record_issue(
                issues,
                "S04_VISIBLE_TARGET_EXTRACTION_FAILED",
                "S04",
                "S04 raw XML contains target series but visible_series_names did not include it.",
                snapshot,
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

        if not visible_series_names:
            _record_s04_visible_series(
                context,
                snapshot,
                direction="down",
                screen_index=screen_index,
                target_series=target_series,
                target_bounds=target_bounds,
                candidate_model_button_bounds=candidate_bounds,
                selected_model_button_bounds=selected_bounds,
                action_taken="stop_visible_series_missing",
                seen_series_names=seen_series_names,
                bottom_reached=bottom_reached,
            )
            issue = _record_issue(issues, "XML_TEXT_MISSING_FOR_VISIBLE_SERIES", "S04", "S04 visible series names missing from fresh XML.", snapshot)
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

        seen_series_names.update(visible_series_names)

        if target_in_visible_series:
            if not selected_button or not selected_button.get("bounds"):
                _record_s04_visible_series(
                    context,
                    snapshot,
                    direction="down",
                    screen_index=screen_index,
                    target_series=target_series,
                    target_bounds=target_bounds,
                    candidate_model_button_bounds=candidate_bounds,
                    selected_model_button_bounds=selected_bounds,
                    action_taken="stop_model_button_binding_failed",
                    seen_series_names=seen_series_names,
                    bottom_reached=bottom_reached,
                )
                issue = _record_issue(
                    issues,
                    "S04_TARGET_MODEL_BUTTON_BINDING_FAILED",
                    "S04",
                    "S04 target series is visible but same-row model button could not be bound.",
                    snapshot,
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

            _record_s04_visible_series(
                context,
                snapshot,
                direction="down",
                screen_index=screen_index,
                target_series=target_series,
                target_bounds=target_bounds,
                candidate_model_button_bounds=candidate_bounds,
                selected_model_button_bounds=selected_bounds,
                action_taken="tap_target_model_button",
                seen_series_names=seen_series_names,
                bottom_reached=bottom_reached,
            )
            action_start = time.perf_counter()
            client.tap(*_center(selected_button["bounds"]))
            action_ms = int((time.perf_counter() - action_start) * 1000)
            time.sleep(0.8)
            transition_wait_ms += 800
            next_snapshot = _capture(client, f"s04_to_s05_{_timestamp()}")
            timing.add(
                step_name="s04_open_series_model",
                page_name="S04",
                action_name="scan_s04_until_target_gate_then_click_model_button",
                contract_check_ms=int((action_start - started) * 1000),
                field_read_ms=0,
                action_ms=action_ms,
                transition_wait_ms=transition_wait_ms,
                screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
                xml_path=str(next_snapshot.get("xml_path") or ""),
            )
            return "S05", next_snapshot

        if previous_visible_series_names is not None and visible_series_names == previous_visible_series_names:
            down_unchanged_count += 1
        else:
            down_unchanged_count = 0
        bottom_reached = down_unchanged_count >= 2

        if bottom_reached:
            _record_s04_visible_series(
                context,
                snapshot,
                direction="down",
                screen_index=screen_index,
                target_series=target_series,
                target_bounds=target_bounds,
                candidate_model_button_bounds=candidate_bounds,
                selected_model_button_bounds=selected_bounds,
                action_taken="stop_target_not_found",
                seen_series_names=seen_series_names,
                bottom_reached=True,
            )
            context["s04_search_bottom_reached"] = True
            context["s04_seen_series_names"] = sorted(seen_series_names)
            issue = _record_issue(issues, "TARGET_SERIES_NOT_FOUND_IN_S04", "S04", "Target series never appeared in S04 raw XML or visible series names before list stopped changing.", snapshot)
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

        _record_s04_visible_series(
            context,
            snapshot,
            direction="down",
            screen_index=screen_index,
            target_series=target_series,
            target_bounds=target_bounds,
            candidate_model_button_bounds=candidate_bounds,
            selected_model_button_bounds=selected_bounds,
            action_taken="scroll_down",
            seen_series_names=seen_series_names,
            bottom_reached=bottom_reached,
        )

        machine.assert_action_allowed("S04", "scroll_series_list")
        client.swipe("up")
        time.sleep(0.35)
        transition_wait_ms += 350
        previous_visible_series_names = visible_series_names
        screen_index += 1
        snapshot = _capture(client, f"s04_series_down_{screen_index}_{_timestamp()}")


def handle_s05(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    client: AdbClient = context["client"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    params = context["task_params"]

    started = time.perf_counter()
    state = _ensure_current_page_contract(
        context,
        snapshot,
        {"S05", "S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"},
        action_page="S05",
    )
    if context.get("s05_select_year_trim_executed"):
        issue = _record_issue(
            issues,
            "S05_NO_PROGRESS_AFTER_CONFIRM",
            "S05",
            "S05 selection action already ran once in this run; refusing to repeat year/trim/confirm.",
            snapshot,
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context["s05_select_year_trim_executed"] = True

    action_total = 0
    target_year = str(params.get("model_year") or "").strip()
    target_trim = str(params.get("trim") or "").strip()
    if state in {"S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"} and not _s05_right_list_contains_year(snapshot, target_year):
        state = "S05"
    elif state == "S05_TRIM_SELECTED" and (
        not _s05_target_trim_selected(snapshot, target_year, target_trim)
        or not _s05_selected_one(snapshot)
        or not _s05_confirm_clickable(snapshot)
    ):
        state = "S05_MODEL_YEAR_SELECTED"
    if state == "S05":
        machine.assert_action_allowed("S05", "tap_target_year")
        year_node = _s05_find_left_year_node(snapshot, target_year)
        if year_node is None or not year_node.get("bounds"):
            issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S05", "Target model year not found.", snapshot)
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        action_start = time.perf_counter()
        client.tap(*_center(year_node["bounds"]))
        action_total += int((time.perf_counter() - action_start) * 1000)
        time.sleep(0.4)
        snapshot = _capture(client, f"s05_after_year_{_timestamp()}")
        state = _ensure_current_page_contract(
            context,
            snapshot,
            {"S05", "S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"},
            action_page="S05_MODEL_YEAR_SELECTED",
        )
        if not _s05_right_list_contains_year(snapshot, target_year):
            issue = _record_issue(
                issues,
                "S05_YEAR_CLICK_NO_EFFECT",
                "S05",
                "Clicking target model year did not switch the right trim list to the target year.",
                {**snapshot, "target_year": target_year, "right_trim_labels": _s05_right_trim_labels(snapshot, target_year)},
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        state = "S05_MODEL_YEAR_SELECTED"

    if state == "S05_MODEL_YEAR_SELECTED":
        if not _s05_right_list_contains_year(snapshot, target_year):
            issue = _record_issue(
                issues,
                "S05_YEAR_CLICK_NO_EFFECT",
                "S05_MODEL_YEAR_SELECTED",
                "Target model year state was not backed by a target-year right trim list.",
                {**snapshot, "target_year": target_year, "right_trim_labels": _s05_right_trim_labels(snapshot, target_year)},
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        machine.assert_action_allowed("S05_MODEL_YEAR_SELECTED", "tap_exact_trim")
        trim_node = _s05_find_target_trim_node(snapshot, target_year, target_trim)
        seen_trim_names: set[str] = set(_s05_right_trim_labels(snapshot, target_year))
        trim_seen_signatures = {_s05_right_trim_signature(snapshot, target_year)}
        trim_scroll_count = 0
        trim_unchanged_count = 0
        effective_trim_scroll_count = 0
        trim_scroll_attempts: list[dict[str, Any]] = []
        while (trim_node is None or not trim_node.get("bounds")) and trim_scroll_count < S05_TRIM_SCROLL_LIMIT:
            before_snapshot = snapshot
            scroll_command = _scroll_s05_right_trim_list(client, before_snapshot)
            if not scroll_command.get("issued"):
                issue = _record_issue(
                    issues,
                    "S05_TRIM_SEARCH_NOT_COMPLETED",
                    "S05_MODEL_YEAR_SELECTED",
                    "S05 right trim list scroll could not be issued with valid right-list bounds.",
                    {
                        **before_snapshot,
                        "target_year": target_year,
                        "target_trim": target_trim,
                        "visible_trim_names": _s05_right_trim_labels(before_snapshot, target_year),
                        "seen_trim_names": sorted(seen_trim_names),
                        "s05_trim_scroll_attempts": trim_scroll_attempts,
                        "scroll_command": scroll_command,
                    },
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            action_total += int(scroll_command.get("swipe_elapsed_ms") or S05_TRIM_SCROLL_DURATION_MS)
            time.sleep(0.45)
            snapshot = _capture(client, f"s05_trim_scroll_{trim_scroll_count + 1}_{_timestamp()}")
            recognized_after_scroll = _ensure_current_page_contract(
                context,
                snapshot,
                {"S05", "S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"},
                action_page="S05_MODEL_YEAR_SELECTED",
            )
            scroll_attempt = _s05_make_trim_scroll_attempt(
                index=trim_scroll_count + 1,
                command=scroll_command,
                before_snapshot=before_snapshot,
                after_snapshot=snapshot,
                recognized_page_after_scroll=recognized_after_scroll,
                target_year=target_year,
            )
            trim_scroll_attempts.append(scroll_attempt)
            timing.add(
                step_name=f"s05_scroll_right_trim_list_{trim_scroll_count + 1}",
                page_name="S05_MODEL_YEAR_SELECTED",
                action_name="scroll_right_trim_list",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=int(scroll_command.get("swipe_elapsed_ms") or S05_TRIM_SCROLL_DURATION_MS),
                transition_wait_ms=450,
                screenshot_path=str(snapshot.get("screenshot_path") or ""),
                xml_path=str(snapshot.get("xml_path") or ""),
                extra=scroll_attempt,
            )
            if not _s05_right_list_contains_year(snapshot, target_year):
                issue = _record_issue(
                    issues,
                    "S05_YEAR_CLICK_NO_EFFECT",
                    "S05_MODEL_YEAR_SELECTED",
                    "Right trim list lost the target model year while searching exact trim.",
                    {
                        **snapshot,
                        "target_year": target_year,
                        "right_trim_labels": _s05_right_trim_labels(snapshot, target_year),
                        "s05_trim_scroll_attempts": trim_scroll_attempts,
                    },
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            if not scroll_attempt.get("left_year_still_selected"):
                issue = _record_issue(
                    issues,
                    "S05_TRIM_SEARCH_NOT_COMPLETED",
                    "S05_MODEL_YEAR_SELECTED",
                    "Target model year was no longer visible after right trim list scroll.",
                    {
                        **snapshot,
                        "target_year": target_year,
                        "target_trim": target_trim,
                        "visible_trim_names": _s05_right_trim_labels(snapshot, target_year),
                        "seen_trim_names": sorted(seen_trim_names),
                        "s05_trim_scroll_attempts": trim_scroll_attempts,
                    },
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            if not scroll_attempt.get("scroll_effective"):
                issue = _record_issue(
                    issues,
                    "S05_TRIM_SCROLL_NOT_EFFECTIVE",
                    "S05_MODEL_YEAR_SELECTED",
                    "S05 right trim list swipe did not change visible trims or the right-list screenshot region.",
                    {
                        **snapshot,
                        "target_year": target_year,
                        "target_trim": target_trim,
                        "visible_trim_names": _s05_right_trim_labels(snapshot, target_year),
                        "seen_trim_names": sorted(seen_trim_names),
                        "s05_trim_scroll_attempts": trim_scroll_attempts,
                    },
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            trim_node = _s05_find_target_trim_node(snapshot, target_year, target_trim)
            seen_trim_names.update(_s05_right_trim_labels(snapshot, target_year))
            signature = _s05_right_trim_signature(snapshot, target_year)
            if signature in trim_seen_signatures:
                trim_unchanged_count += 1
            else:
                trim_unchanged_count = 0
            trim_seen_signatures.add(signature)
            trim_scroll_count += 1
            effective_trim_scroll_count += 1
            if trim_node and trim_node.get("bounds"):
                break
            if trim_unchanged_count >= 2:
                break
        if trim_node is None or not trim_node.get("bounds"):
            not_found_code = "CONFIG_MODEL_NOT_FOUND" if effective_trim_scroll_count > 0 else "S05_TRIM_SEARCH_NOT_COMPLETED"
            not_found_message = "Exact trim not found." if effective_trim_scroll_count > 0 else "Exact trim search did not complete with effective right-list scroll evidence."
            issue = _record_issue(
                issues,
                not_found_code,
                "S05_MODEL_YEAR_SELECTED",
                not_found_message,
                {
                    **snapshot,
                    "target_year": target_year,
                    "target_trim": target_trim,
                    "visible_trim_names": _s05_right_trim_labels(snapshot, target_year),
                    "seen_trim_names": sorted(seen_trim_names),
                    "trim_scroll_count": trim_scroll_count,
                    "effective_trim_scroll_count": effective_trim_scroll_count,
                    "trim_unchanged_count": trim_unchanged_count,
                    "s05_trim_scroll_attempts": trim_scroll_attempts,
                },
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        action_start = time.perf_counter()
        client.tap(*_center(trim_node["bounds"]))
        action_total += int((time.perf_counter() - action_start) * 1000)
        time.sleep(0.4)
        snapshot = _capture(client, f"s05_after_trim_{_timestamp()}")
        state = _ensure_current_page_contract(
            context,
            snapshot,
            {"S05", "S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"},
            action_page="S05_TRIM_SELECTED",
        )
        if not _s05_target_trim_selected(snapshot, target_year, target_trim) or not _s05_selected_one(snapshot) or not _s05_confirm_clickable(snapshot):
            issue = _record_issue(
                issues,
                "S05_TRIM_CLICK_NO_EFFECT",
                "S05_MODEL_YEAR_SELECTED",
                "Clicking target trim did not select one item and enable the confirm button.",
                {
                    **snapshot,
                    "target_year": target_year,
                    "target_trim": target_trim,
                    "selected_one": _s05_selected_one(snapshot),
                    "confirm_clickable": _s05_confirm_clickable(snapshot),
                },
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        state = "S05_TRIM_SELECTED"

    machine.assert_action_allowed("S05_TRIM_SELECTED", "tap_green_confirm")
    confirm_node = _s05_find_confirm_node(snapshot)
    if (
        confirm_node is None
        or not confirm_node.get("bounds")
        or not _s05_selected_one(snapshot)
        or not _s05_confirm_clickable(snapshot)
    ):
        issue = _record_issue(
            issues,
            "S05_TRIM_CLICK_NO_EFFECT",
            "S05_TRIM_SELECTED",
            "Confirm is not allowed before one target trim is selected and the confirm button is clickable.",
            {
                **snapshot,
                "target_year": target_year,
                "target_trim": target_trim,
                "selected_one": _s05_selected_one(snapshot),
                "confirm_clickable": _s05_confirm_clickable(snapshot),
            },
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    action_start = time.perf_counter()
    client.tap(*_center(confirm_node["bounds"]))
    action_total += int((time.perf_counter() - action_start) * 1000)
    transition_wait_ms = 800
    next_snapshot: dict[str, Any] | None = None
    next_state: str | None = None
    for wait_ms in (1000, 2000, 3000, 4000, 5000):
        time.sleep(wait_ms / 1000)
        transition_wait_ms += wait_ms
        next_snapshot = _capture(client, f"s05_to_s06_{_timestamp()}")
        next_state = _recognize_page(recognizer, next_snapshot, context.get("flow_state"))
        if next_state == "S06":
            break
        if next_state in {"S05", "S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"}:
            break
    assert next_snapshot is not None
    timing.add(
        step_name="s05_select_year_trim_and_confirm",
        page_name="S05",
        action_name="tap_target_year_tap_exact_trim_tap_green_confirm",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_total,
        transition_wait_ms=transition_wait_ms,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
    )
    context["s05_after_confirm_state"] = next_state
    if next_state == "S06":
        return "S06", next_snapshot

    if next_state in {"S05", "S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"}:
        issue = _record_issue(
            issues,
            "S05_CONFIRM_NO_EFFECT",
            "S05",
            "S05 confirm did not enter S06 after the single allowed year/trim/confirm attempt.",
            {**next_snapshot, "after_confirm_state": next_state},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    issue = _record_issue(
        issues,
        "PAGE_CONTRACT_MISMATCH",
        next_state or "UNKNOWN",
        "S05 confirm did not transition to S06.",
        {**next_snapshot, "after_confirm_state": next_state},
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def handle_s06(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    client: AdbClient = context["client"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]

    started = time.perf_counter()
    _ensure_current_page_contract(context, snapshot, {"S06"}, action_page="S06")
    machine.assert_action_allowed("S06", "tap_trim_filter")
    node = _find_exact(snapshot, "车型配置")
    if node is None or not node.get("bounds"):
        issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S06", "车型配置 entry not found.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    action_start = time.perf_counter()
    client.tap(*_center(node["bounds"]))
    action_ms = int((time.perf_counter() - action_start) * 1000)
    time.sleep(0.8)
    next_snapshot = _capture(client, f"s06_to_s07_{_timestamp()}")
    next_state = _recognize_page(recognizer, next_snapshot, context.get("flow_state"))
    if next_state != "S07":
        issue = _record_issue(
            issues,
            "PAGE_CONTRACT_MISMATCH",
            "S06",
            "S06 车型配置 click did not open the S07 model-config filter window.",
            {**next_snapshot, "after_trim_filter_state": next_state},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    timing.add(
        step_name="s06_open_model_config_filter",
        page_name="S06",
        action_name="tap_trim_filter",
        contract_check_ms=int((action_start - started) * 1000),
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=800,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
    )
    _add_runtime_timing(
        context,
        step_name="S07_ENTER_FROM_S06",
        page_name="S06",
        action_name="tap_trim_filter_wait_s07_stable",
        contract_check_ms=int((action_start - started) * 1000),
        action_ms=action_ms,
        transition_wait_ms=800,
        snapshot=next_snapshot,
        extra={
            "recognized_page": next_state,
            "reason_category": "S07_WEBVIEW_TEXT_DELAY",
            "reason_detail": "S06 click waits for the S07 filter window contract to appear after a fresh capture",
            "solution": "keep the fresh page check; later tuning can replace fixed wait with finite polling",
        },
    )
    return next_state, next_snapshot


def handle_s07(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]] | dict[str, Any]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    client: AdbClient = context["client"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    params = context["task_params"]

    started = time.perf_counter()
    _ensure_current_page_contract(context, snapshot, {"S07"}, action_page="S07")
    total_action_ms = 0

    target_color = str(params.get("color") or "")
    color_check_started = time.perf_counter()
    color_node = _find_target_color_node(snapshot, target_color)
    color_already_visible = color_node is not None and bool(color_node.get("bounds"))
    _add_runtime_timing(
        context,
        step_name="S07_COLOR_PANEL_ALREADY_VISIBLE_CHECK",
        page_name="S07",
        action_name="reuse_current_xml_find_target_color",
        field_read_ms=int((time.perf_counter() - color_check_started) * 1000),
        snapshot=snapshot,
        extra={
            "target_color": target_color,
            "target_color_visible": color_already_visible,
            "reason_category": "S07_REDUNDANT_FRESH_CAPTURE",
            "reason_detail": "the current S07 XML is reused to decide whether the target color can be tapped directly",
            "solution": "skip the color-tab click when the target color node is already visible in the same fresh XML",
        },
    )
    if color_node is None or not color_node.get("bounds"):
        machine.assert_action_allowed("S07", "tap_color_filter")
        color_tab = _find_exact(snapshot, "颜色")
        if color_tab is None or not color_tab.get("bounds"):
            issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S07", "Color tab not found.", snapshot)
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        t0 = time.perf_counter()
        client.tap(*_center(color_tab["bounds"]))
        color_tab_ms = int((time.perf_counter() - t0) * 1000)
        total_action_ms += color_tab_ms
        _add_runtime_timing(
            context,
            step_name="S07_CLICK_COLOR_TAB",
            page_name="S07",
            action_name="tap_color_filter",
            action_ms=color_tab_ms,
            snapshot=snapshot,
            extra={
                "target_color": target_color,
                "reason_category": "S07_COLOR_PANEL_EXPAND_WAIT_SLOW",
                "reason_detail": "color tab is tapped only when the target color was not already visible",
                "solution": "after tapping, use short polling for the target color node",
            },
        )
        wait_color_started = time.perf_counter()
        snapshot, color_node = _wait_for_s07_target_color_node(
            context,
            snapshot,
            target_color,
            "s07_after_color_tab",
        )
        _add_runtime_timing(
            context,
            step_name="S07_WAIT_COLOR_OPTIONS",
            page_name="S07",
            action_name="short_poll_wait_target_color_node",
            transition_wait_ms=int((time.perf_counter() - wait_color_started) * 1000),
            snapshot=snapshot,
            extra={
                "target_color": target_color,
                "target_color_visible": color_node is not None and bool(color_node.get("bounds")),
                "reason_category": "S07_WEBVIEW_TEXT_DELAY",
                "reason_detail": "finite 0.3s polling waits only until the target color node appears",
                "solution": "avoid long sleeps and stop polling as soon as the node exists",
            },
        )

    machine.assert_action_allowed("S07", "tap_target_color")
    if color_node is None or not color_node.get("bounds"):
        issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S07", "Target color not found.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    t0 = time.perf_counter()
    client.tap(*_center(color_node["bounds"]))
    target_color_ms = int((time.perf_counter() - t0) * 1000)
    total_action_ms += target_color_ms
    _add_runtime_timing(
        context,
        step_name="S07_CLICK_TARGET_COLOR",
        page_name="S07",
        action_name="tap_target_color",
        action_ms=target_color_ms,
        snapshot=snapshot,
        extra={
            "target_color": target_color,
            "target_color_direct_from_current_xml": color_already_visible,
            "color_node_text": color_node.get("matched_color_text") or next(iter(color_node.get("labels", []) or []), ""),
            "color_node_bounds": color_node.get("bounds"),
            "color_node_clickable": bool(color_node.get("clickable")),
            "color_click_target_bounds": color_node.get("bounds"),
            "color_click_strategy": color_node.get("color_click_strategy") or "target_color_node_bounds",
            "reason_category": "S07_TARGET_COLOR_NODE_SEARCH_SLOW",
            "reason_detail": "target color is clicked from the exact XML node bounds",
            "solution": "reuse parsed XML nodes and avoid an extra color-tab expansion when possible",
        },
    )
    confirm_color_started = time.perf_counter()
    snapshot = _wait_for_s07_target_color_selected(
        context,
        snapshot,
        target_color,
        "s07_after_color_select",
    )
    _add_runtime_timing(
        context,
        step_name="S07_CONFIRM_COLOR_SELECTED",
        page_name="S07",
        action_name="short_poll_confirm_target_color_selected",
        transition_wait_ms=int((time.perf_counter() - confirm_color_started) * 1000),
        snapshot=snapshot,
        extra={
            "target_color": target_color,
            "target_color_selected": _target_color_selected(snapshot, target_color),
            "reason_category": "S07_COLOR_SELECTED_CONFIRM_SLOW",
            "reason_detail": "COLOR_FILTER_DONE is gated by a fresh selected-state confirmation",
            "solution": "keep the confirmation but reuse the fresh XML captured by the short poll",
        },
    )
    _ensure_current_page_contract(context, snapshot, {"S07"}, action_page="S07")
    if not _target_color_selected(snapshot, target_color):
        issue = _record_issue(issues, "COLOR_STATE_NOT_CONFIRMED", "S07", "Target color not confirmed before age filter.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context.setdefault("flow_state", {})["COLOR_FILTER_DONE"] = True
    _add_runtime_timing(
        context,
        step_name="COLOR_FILTER_DONE_SET",
        page_name="S07",
        action_name="set_color_filter_done_after_evidence",
        snapshot=snapshot,
        extra={
            "target_color": target_color,
            "COLOR_FILTER_DONE": True,
            "reason_category": "CONTRACT_GATE",
            "reason_detail": "COLOR_FILTER_DONE is set only after target color selected-state evidence exists",
            "solution": "do not bypass this gate while optimizing duplicate capture work",
        },
    )

    machine.assert_action_allowed("S07", "tap_age_filter")
    age_tab = _find_exact(snapshot, "车龄")
    if age_tab is None or not age_tab.get("bounds"):
        issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S07", "Age tab not found.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    t0 = time.perf_counter()
    client.tap(*_center(age_tab["bounds"]))
    total_action_ms += int((time.perf_counter() - t0) * 1000)
    time.sleep(0.4)
    snapshot = _capture(client, f"s07_after_age_tab_{_timestamp()}")
    _ensure_current_page_contract(context, snapshot, {"S07"}, action_page="S07")

    machine.assert_action_allowed("S07", "set_exact_age")
    age_action_started = time.perf_counter()
    if not _set_exact_age_from_ticks(client, snapshot, params.get("target_age_years")):
        issue = _record_issue(issues, "FIELD_MISSING", "S07", "Exact target age tick not found before view-result.", {**snapshot, "target_age_years": params.get("target_age_years")})
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    total_action_ms += int((time.perf_counter() - age_action_started) * 1000)
    snapshot = _capture(client, f"s07_after_exact_age_{_timestamp()}")
    _ensure_current_page_contract(context, snapshot, {"S07"}, action_page="S07")
    if not _exact_age_confirmed(snapshot, params.get("target_age_years")):
        issue = _record_issue(issues, "FIELD_MISSING", "S07", "Exact target age state not confirmed before view-result.", {**snapshot, "target_age_years": params.get("target_age_years")})
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context.setdefault("flow_state", {})["AGE_FILTER_DONE"] = True

    machine.assert_action_allowed("S07", "tap_view_cars")
    view_node = _find_s07_view_cars_button(snapshot)
    if view_node is None or not view_node.get("bounds"):
        issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S07", "S07 view-cars button not found.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    view_count = _s07_view_cars_count(view_node)
    if view_count == 0:
        timing.add(
            step_name="s07_manual_pricing_no_sources",
            page_name="S07",
            action_name="read_view_zero_manual_pricing",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=total_action_ms,
            transition_wait_ms=0,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
        )
        timing.write()
        return _manual_pricing_required_result(context, snapshot, view_node)
    t0 = time.perf_counter()
    client.tap(*_center(view_node["bounds"]))
    total_action_ms += int((time.perf_counter() - t0) * 1000)
    time.sleep(1.0)
    next_snapshot = _capture(client, f"s07_to_s08_{_timestamp()}")
    after_view_state = _recognize_page(recognizer, next_snapshot, context.get("flow_state"))
    if after_view_state not in {"S06", "S08", "S09", "S10"}:
        issue = _record_issue(
            issues,
            "PAGE_CONTRACT_MISMATCH",
            "S07",
            "S07 view-cars button did not return to a recognized vehicle list page.",
            {**next_snapshot, "after_view_state": after_view_state, "view_cars_button_bounds": view_node.get("bounds")},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context.setdefault("flow_state", {})["S07_FILTER_DONE"] = True
    after_view_state = _recognize_page(recognizer, next_snapshot, context.get("flow_state")) or "S08"
    timing.add(
        step_name="s07_color_age_and_view",
        page_name="S07",
        action_name="tap_color_tap_target_color_confirm_then_tap_age_confirm_exact_then_tap_view",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=total_action_ms,
        transition_wait_ms=2200,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
    )
    return after_view_state, next_snapshot


def handle_s08(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    client: AdbClient = context["client"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]

    started = time.perf_counter()
    _require_s07_filters_done(context, snapshot, code="SORT_BLOCKED_BEFORE_S07_DONE", state_id="S08")
    _ensure_current_page_contract(context, snapshot, {"S08"}, action_page="S08")
    machine.assert_action_allowed("S08", "collect_list_whitelist_fields")
    field_start = time.perf_counter()
    cards = _collect_list_fields(snapshot)
    context["s08_cards"] = cards
    field_ms = int((time.perf_counter() - field_start) * 1000)
    if "综合排序" in str(snapshot.get("visible_blob") or ""):
        machine.assert_action_allowed("S08", "tap_sort_if_present")
        sort_node = _find_exact(snapshot, "综合排序")
        if sort_node is None or not sort_node.get("bounds"):
            issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S08", "综合排序 visible but not tappable.", snapshot)
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        action_start = time.perf_counter()
        client.tap(*_center(sort_node["bounds"]))
        action_ms = int((time.perf_counter() - action_start) * 1000)
        time.sleep(0.8)
        next_snapshot = _capture(client, f"s08_to_s09_{_timestamp()}")
        next_state = _recognize_page(recognizer, next_snapshot, context.get("flow_state"))
        if next_state != "S09":
            issue = _record_issue(
                issues,
                "PAGE_CONTRACT_MISMATCH",
                "S08",
                "S08 综合排序 click did not open the S09 sort option panel.",
                {**next_snapshot, "after_sort_click_state": next_state},
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        timing.add(
            step_name="s08_open_sort_panel",
            page_name="S08",
            action_name="tap_sort_if_present",
            contract_check_ms=int((field_start - started) * 1000),
            field_read_ms=field_ms,
            action_ms=action_ms,
            transition_wait_ms=800,
            screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
            xml_path=str(next_snapshot.get("xml_path") or ""),
        )
        return next_state, next_snapshot

    timing.add(
        step_name="s08_ready_without_sort",
        page_name="S08",
        action_name="collect_list_whitelist_fields",
        contract_check_ms=int((field_start - started) * 1000),
        field_read_ms=field_ms,
        action_ms=0,
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
    )
    return "S10", snapshot


def handle_s09(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    client: AdbClient = context["client"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]

    started = time.perf_counter()
    _require_s07_filters_done(context, snapshot, code="SORT_OPTION_BLOCKED_BEFORE_S07_DONE", state_id="S09")
    _ensure_current_page_contract(context, snapshot, {"S09"}, action_page="S09")
    machine.assert_action_allowed("S09", "tap_price_low_to_high")
    node = _find_exact(snapshot, "价格从低到高")
    if node is None or not node.get("bounds"):
        issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S09", "价格从低到高 not found.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    action_start = time.perf_counter()
    client.tap(*_center(node["bounds"]))
    action_ms = int((time.perf_counter() - action_start) * 1000)
    time.sleep(1.0)
    next_snapshot = _capture(client, f"s09_to_s10_{_timestamp()}")
    if not (_has_price_low_to_high_sort(next_snapshot) and _extract_s10_contract_cards(next_snapshot)):
        issue = _record_issue(
            issues,
            "SORT_OPTION_BLOCKED_BEFORE_S07_DONE",
            "S09",
            "Price low-to-high click did not return to a sorted vehicle list page.",
            next_snapshot,
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context.setdefault("flow_state", {})["SORT_DONE"] = True
    timing.add(
        step_name="s09_sort_low_to_high",
        page_name="S09",
        action_name="tap_price_low_to_high",
        contract_check_ms=int((action_start - started) * 1000),
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=1000,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
    )
    return "S10", next_snapshot


def handle_s10_ready_check(context: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]

    started = time.perf_counter()
    _ensure_current_page_contract(context, snapshot, {"S10"}, action_page="S10")
    machine.assert_action_allowed("S10", "collect_list_whitelist_fields")
    field_start = time.perf_counter()
    _require_s10_ready_flow_done(context, snapshot)
    cards = _assert_s10_ready_contract(issues, snapshot, source=context.get("s10_ready_source"), flow_state=context.get("flow_state"))
    field_ms = int((time.perf_counter() - field_start) * 1000)
    context.setdefault("flow_state", {})["S10_READY"] = True
    timing.add(
        step_name="s10_ready_check",
        page_name="S10",
        action_name="collect_list_whitelist_fields",
        contract_check_ms=int((field_start - started) * 1000),
        field_read_ms=field_ms,
        action_ms=0,
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
    )
    result = {
        "metadata": {
            "project": "guazi_app_data_system",
            "mode": "device_real_mainline_s01_to_s10",
            "field_scope": "contract_only",
        },
        "status": "S10_READY",
        "target_task": _target_task_output(context["task_params"]),
        "flow_state": dict(context.get("flow_state", {})),
        "startup": dict(context.get("startup", {})),
        "task_params": context["task_params"],
        "same_source_cards": cards,
    }
    timing.write()
    _write_result_json(context["configs"], result)
    return result


def run_s01_to_s10_mainline(runtime: dict[str, Any], phone_test: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_dirs()
    configs = runtime["configs"]
    _enable_s04_scroll_series_list_action(configs["pages"])
    recognizer = PageRecognizer(configs["pages"])
    machine = PageStateMachine(configs["pages"])
    timing = TimingRecorder()
    task_params = _task_params()

    context: dict[str, Any] = {
        "configs": configs,
        "audit": runtime["audit"],
        "issues": runtime["issues"],
        "recognizer": recognizer,
        "machine": machine,
        "timing": timing,
        "task_params": task_params,
        "flow_state": {
            "S07_FILTER_DONE": False,
            "COLOR_FILTER_DONE": False,
            "AGE_FILTER_DONE": False,
            "SORT_DONE": False,
            "S10_READY": False,
        },
        "startup": {
            "startup_mode": "force_restart",
            "app_entry_mode": "force_restart",
            "current_page_first_enabled": False,
            "app_force_restart_called": False,
            "force_stop_package": GUAZI_PACKAGE,
            "force_stop_done": False,
            "home_to_launcher_done": False,
            "tap_guazi_app_icon_done": False,
            "app_restart_wait_ms": 0,
            "initial_capture_before_recovery": False,
            "initial_home_to_launcher_before_capture": True,
            "initial_tap_app_icon_before_capture": True,
            "initial_fresh_state": None,
            "initial_screenshot_path": None,
            "initial_xml_path": None,
            "recovery_called": False,
            "recovery_call_reason": None,
            "app_force_restart_reason": "startup",
            "after_force_restart_state": None,
            "after_force_restart_screenshot_path": None,
            "after_force_restart_xml_path": None,
            "after_force_restart_visible_text_digest": [],
            "after_recovery_state": None,
            "after_recovery_screenshot_path": None,
            "after_recovery_xml_path": None,
        },
        "phone_test": phone_test or {},
    }
    context["target_fingerprint"] = _target_fingerprint(_target_task_output(task_params))

    mismatch_result = _target_task_mismatch_result(context)
    if mismatch_result is not None:
        return mismatch_result

    client = AdbClient()
    context["client"] = client

    try:
        snapshot = _recover_to_guazi_page(context, reason="startup")
        after_recovery_state = _recognize_page(recognizer, snapshot, context.get("flow_state"))
        context["startup"].update(
            {
                "after_force_restart_state": after_recovery_state,
                "after_force_restart_screenshot_path": snapshot.get("screenshot_path"),
                "after_force_restart_xml_path": snapshot.get("xml_path"),
                "after_force_restart_visible_text_digest": _visible_text_digest(snapshot),
                "after_recovery_state": after_recovery_state,
                "after_recovery_screenshot_path": snapshot.get("screenshot_path"),
                "after_recovery_xml_path": snapshot.get("xml_path"),
            }
        )
        if after_recovery_state == "S_LOGIN":
            snapshot = _dismiss_initial_s_login(context, snapshot)
            after_recovery_state = _recognize_page(recognizer, snapshot, context.get("flow_state"))
            context["startup"].update(
                {
                    "after_force_restart_state": after_recovery_state,
                    "after_force_restart_screenshot_path": snapshot.get("screenshot_path"),
                    "after_force_restart_xml_path": snapshot.get("xml_path"),
                    "after_force_restart_visible_text_digest": _visible_text_digest(snapshot),
                    "after_recovery_state": after_recovery_state,
                    "after_recovery_screenshot_path": snapshot.get("screenshot_path"),
                    "after_recovery_xml_path": snapshot.get("xml_path"),
                }
            )
        if after_recovery_state not in S01_TO_S10_STATES:
            target_task = _target_task_output(context["task_params"])
            issue_context = {
                **snapshot,
                "actual_state": None,
                "after_force_restart_state": after_recovery_state,
                "visible_text_digest": _visible_text_digest(snapshot),
                "focused_window": snapshot.get("focused_window"),
                "foreground_package": snapshot.get("foreground_package"),
                "activity": snapshot.get("resumed_activity"),
                "app_force_restart_reason": context["startup"].get("app_force_restart_reason"),
                "target_task_path": context["task_params"].get("target_task_path"),
                "brand": target_task.get("brand"),
                "series": target_task.get("series"),
                "year_model": target_task.get("year_model"),
                "config_model": target_task.get("config_model"),
                "color": target_task.get("color"),
                "register_date": target_task.get("register_date"),
                "startup": dict(context.get("startup", {})),
            }
            issue = _record_issue(
                context["issues"],
                "APP_FORCE_RESTART_NON_CONTRACT_PAGE",
                after_recovery_state or "UNKNOWN",
                "APP_FORCE_RESTART did not land on a verified S00-S10 or S_LOGIN page contract.",
                issue_context,
            )
            raise GuaziFlowError("APP_FORCE_RESTART_NON_CONTRACT_PAGE", issue["message"], issue["context"])
        state = _current_state_or_stop(context, snapshot)

        while True:
            state = _current_state_or_stop(context, snapshot)
            if state == "S01":
                state, snapshot = handle_s01(context, snapshot)
                continue
            if state in {"S02", "S02_SELECT_CAR_TAB"}:
                state, snapshot = handle_s02(context, snapshot)
                continue
            if state == "S03":
                state, snapshot = handle_s03(context, snapshot)
                continue
            if state in {"S04"}:
                state, snapshot = handle_s04(context, snapshot)
                continue
            if state in {"S05", "S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"}:
                state, snapshot = handle_s05(context, snapshot)
                continue
            if state == "S06":
                state, snapshot = handle_s06(context, snapshot)
                continue
            if state == "S07":
                s07_result = handle_s07(context, snapshot)
                if isinstance(s07_result, dict):
                    return s07_result
                state, snapshot = s07_result
                continue
            if state == "S08":
                state, snapshot = handle_s08(context, snapshot)
                if state == "S10":
                    context["s10_ready_source"] = "S08_SINGLE_OR_NO_SORT"
                continue
            if state == "S09":
                state, snapshot = handle_s09(context, snapshot)
                if state == "S10":
                    context["s10_ready_source"] = "S09_PRICE_LOW_TO_HIGH"
                continue
            if state == "S10":
                result = handle_s10_ready_check(context, snapshot)
                context["flow_state"]["S10_READY"] = True
                return result
            issue = _record_issue(context["issues"], "PAGE_CONTRACT_MISMATCH", state or "UNKNOWN", "Unhandled S01-S10 state.", {"state": state})
            timing.write()
            result = {
                "metadata": {
                    "project": "guazi_app_data_system",
                    "mode": "device_real_mainline_s01_to_s10",
                    "field_scope": "contract_only",
                },
                "status": issue["code"],
                "startup": dict(context.get("startup", {})),
            }
            _write_result_json(configs, result)
            return result
    except GuaziFlowError as exc:
        timing.write()
        result = {
            "metadata": {
                "project": "guazi_app_data_system",
                "mode": "device_real_mainline_s01_to_s10",
                "field_scope": "contract_only",
            },
            "status": exc.code,
            "error": str(exc),
            "target_task": _target_task_output(context["task_params"]),
            "flow_state": dict(context.get("flow_state", {})),
            "startup": dict(context.get("startup", {})),
            "context": exc.context,
        }
        _write_result_json(configs, result)
        return result


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ensure_runtime_dirs()
    configs = {
        "system": load_config("system.yaml"),
        "pages": load_config("pages.yaml"),
        "fields": load_config("fields.yaml"),
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
    runtime = {"configs": configs, "audit": audit, "issues": issues}
    print(json.dumps(run_s01_to_s10_mainline(runtime), ensure_ascii=False, indent=2))
