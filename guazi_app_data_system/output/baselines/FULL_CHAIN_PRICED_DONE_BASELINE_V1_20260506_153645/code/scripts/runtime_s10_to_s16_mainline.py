"""Real-device mainline executor for S10-S16.

This script is the only intended S10-S16 real-device chain:
S10 -> S11 -> S12 -> S13 -> optional S14 -> back to S10 -> internal S15 -> internal S16.

It does not modify page contracts. It only uses existing config, timing logs,
and device helpers. Business clicks should happen only when the caller
explicitly starts this script.
"""

from __future__ import annotations

import json
import hashlib
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


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
from guazi_app_data_system.data_collection import DataCollector
from guazi_app_data_system.exception_handler import GuaziFlowError, IssueRecorder
from guazi_app_data_system.feishu_sync import validate_current_target_task
from guazi_app_data_system.issue_classifier import IssueClassifier
from guazi_app_data_system.learning_loop import LearningLoop
from guazi_app_data_system.models import DamageRecord, ReferenceCar, TargetCar
from guazi_app_data_system.output_writer import write_json
from guazi_app_data_system.page_recognition import PageRecognizer
from guazi_app_data_system.page_state_machine import PageStateMachine
from guazi_app_data_system.pricing import calculate_pricing, score_target, select_reference


S14_ALLOWED_PARTS = [
    "前保险杠",
    "后保险杠",
    "前机盖",
    "发动机盖",
    "左前翼子板",
    "右前翼子板",
    "左后翼子板",
    "右后翼子板",
    "左前车门",
    "右前车门",
    "左后车门",
    "右后车门",
    "左侧前门",
    "右侧前门",
    "左侧后门",
    "右侧后门",
    "后备箱盖",
    "尾门",
    "车顶",
]

S14_DAMAGE_NORMALIZATION = {
    "更换": "更换",
    "换件": "更换",
    "钣金": "钣金",
    "钣金喷漆": "钣金",
    "喷漆": "喷漆",
    "漆面": "喷漆",
    "漆面损伤": "喷漆",
}

S14_DAMAGE_PRIORITY = {"喷漆": 1, "钣金": 2, "更换": 3}
S14_SPECIAL_STRUCTURE_RISK_PARTS = {"ABC柱", "水箱框架"}
S14_COVER_PART_ALIASES = {
    "前保险杠": {"前保险杠"},
    "后保险杠": {"后保险杠"},
    "发动机舱盖": {"发动机舱盖", "发动机盖", "机盖", "前机盖"},
    "后备箱盖": {"后备箱盖", "后盖", "尾门"},
    "左前翼子板": {"左前翼子板"},
    "右前翼子板": {"右前翼子板"},
    "左后翼子板": {"左后翼子板"},
    "右后翼子板": {"右后翼子板"},
    "左前门": {"左前门", "左前车门", "左侧前门"},
    "右前门": {"右前门", "右前车门", "右侧前门"},
    "左后门": {"左后门", "左后车门", "左侧后门"},
    "右后门": {"右后门", "右后车门", "右侧后门"},
    "车顶": {"车顶", "大顶"},
}
S14_SPECIAL_PART_ALIASES = {
    "ABC柱": {"A柱", "B柱", "C柱", "ABC柱", "A 柱", "B 柱", "C 柱", "ABC 柱"},
    "水箱框架": {"水箱框架", "水箱架", "前水箱框架"},
}
S13_REGION_ORDER = ["驾驶侧", "车尾", "副驾驶", "车头"]


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


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


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
                "enabled": str(node.attrib.get("enabled") or "true") == "true",
                "selected": str(node.attrib.get("selected") or "") == "true",
                "resource_id": str(node.attrib.get("resource-id") or ""),
                "package": str(node.attrib.get("package") or ""),
                "class_name": str(node.attrib.get("class") or ""),
            }
        )
    return nodes


def _parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(raw or ""))
    if not match:
        return None
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def _center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    return ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _sha256_file(path: str | Path | None) -> str:
    if not path:
        return ""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _visible_texts(xml_text: str) -> list[str]:
    texts: list[str] = []
    for node in _parse_nodes(xml_text):
        bounds = node.get("bounds")
        if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1] or bounds[2] <= 0 or bounds[3] <= 0:
            continue
        for label in node["labels"]:
            if label and label not in texts:
                texts.append(label)
    return texts


def _capture(client: AdbClient, stem: str) -> dict[str, Any]:
    screenshot_path = project_path("artifacts", "screenshots", f"{stem}.png")
    capture_started = time.perf_counter()
    power_started = time.perf_counter()
    power = client.power_state()
    power_ms = int((time.perf_counter() - power_started) * 1000)
    dumpsys_started = time.perf_counter()
    window_result = client.run(["shell", "dumpsys", "window"], timeout=20)
    activity_result = client.run(["shell", "dumpsys", "activity", "activities"], timeout=20)
    dumpsys_ms = int((time.perf_counter() - dumpsys_started) * 1000)
    screenshot_started = time.perf_counter()
    screenshot_result = client.screenshot(screenshot_path)
    screenshot_ms = int((time.perf_counter() - screenshot_started) * 1000)
    xml_started = time.perf_counter()
    xml_text = client.dump_ui_xml()
    xml_ms = int((time.perf_counter() - xml_started) * 1000)
    window_dump = window_result.stdout if window_result.success else ""
    activity_dump = activity_result.stdout if activity_result.success else ""
    keyguard_showing = _is_keyguard_showing_from_window_dump(window_dump)
    snapshot = {
        "wakefulness": power.get("wakefulness"),
        "interactive": power.get("interactive"),
        "display_state": power.get("display_state"),
        "keyguard_showing": keyguard_showing,
        "keyguard_locked": keyguard_showing,
        "keyguard_secure": _is_keyguard_secure_from_window_dump(window_dump),
        "foreground_package": _extract_foreground_package(window_dump, activity_dump),
        "resumed_activity": _extract_resumed_activity(activity_dump),
        "focused_window": _extract_focused_window(window_dump),
        "xml_package": extract_xml_root_package(xml_text),
        "fresh_xml": xml_text,
        "fresh_screenshot": str(screenshot_path) if screenshot_result and screenshot_result.success else None,
        "screenshot_is_black": _is_probably_black_screenshot(screenshot_path) if screenshot_result and screenshot_result.success else False,
        "capture_metrics": {
            "power_ms": power_ms,
            "dumpsys_ms": dumpsys_ms,
            "screenshot_ms": screenshot_ms,
            "xml_ms": xml_ms,
            "capture_total_ms": int((time.perf_counter() - capture_started) * 1000),
        },
    }
    snapshot["screenshot_path"] = str(screenshot_path)
    xml_path = project_path("artifacts", "debug", f"{stem}.xml")
    xml_text = str(snapshot.get("fresh_xml") or "")
    xml_path.write_text(xml_text, encoding="utf-8")
    snapshot["xml_path"] = str(xml_path)
    snapshot["visible_texts"] = _visible_texts(xml_text)
    snapshot["visible_blob"] = "".join(snapshot["visible_texts"])
    snapshot["nodes"] = _parse_nodes(xml_text)
    return snapshot


def _recognize_mainline_page(recognizer: PageRecognizer, snapshot: dict[str, Any]) -> str | None:
    text = str(snapshot.get("visible_blob") or "")
    if not text and str(snapshot.get("xml_package") or "") == "com.android.systemui":
        return "RUNTIME"
    if str(snapshot.get("foreground_package") or "") != GUAZI_PACKAGE and str(snapshot.get("xml_package") or "") != GUAZI_PACKAGE:
        return None
    if (
        "联系卖家" in text
        and "讲价" in text
        and "立即订购" in text
        and ("检测报告" in text or "车源号" in text)
    ):
        return "S11"
    if any(
        f"{part}—{damage}" in text or f"{part}-{damage}" in text
        for part in S14_ALLOWED_PARTS
        for damage in S14_DAMAGE_NORMALIZATION
    ) or (
        "异常细节" in text
        and any(part in text for part in S14_ALLOWED_PARTS)
        and any(damage in text for damage in S14_DAMAGE_NORMALIZATION)
    ):
        return "S14"
    if "瓜子官方检测报告" in text and "车身外观" in text and any(
        token in text for token in ["驾驶侧", "车尾", "副驾驶", "车头", "历史修复"]
    ):
        return "S13"
    for state_id, context in [
        ("S14", {}),
        ("S13", {}),
        ("S12", {}),
        ("S11", {}),
        ("S10", {"sorted_by": "price_low_to_high"}),
        ("S01", {}),
        ("S02", {}),
    ]:
        page = recognizer.recognize(text, candidate_ids=[state_id], context=context)
        if page:
            return page["id"]
    if "稍后" in text and ("登录" in text or "手机号" in text):
        return "S_LOGIN"
    if GUAZI_APP_ICON_LABEL in text:
        return "S_APP_ICON"
    return None


def _record_runtime_issue(
    issues: IssueRecorder,
    code: str,
    state_id: str,
    message: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    return issues.record(code, state_id, message, context, "manual_review")


def _recover_to_guazi_page(client: AdbClient, recognizer: PageRecognizer, issues: IssueRecorder, timing: TimingRecorder) -> dict[str, Any]:
    step_started = time.perf_counter()
    wake_result = client.wake_screen_once()
    time.sleep(0.2)
    swipe_result = client.wake_swipe_once()
    time.sleep(0.4)
    client.home_key_once()
    time.sleep(0.3)
    home_snapshot = _capture(client, f"runtime_recover_home_{_timestamp()}")
    if _recognize_mainline_page(recognizer, home_snapshot) == "S_APP_ICON":
        client.tap_guazi_app_icon_exact_text(str(home_snapshot.get("fresh_xml") or ""))
        time.sleep(1.0)
    snapshot = _capture(client, f"runtime_recover_after_icon_{_timestamp()}")
    if _recognize_mainline_page(recognizer, snapshot) == "S_LOGIN" and "稍后" in str(snapshot.get("visible_blob") or ""):
        client.tap_text("稍后")
        time.sleep(0.8)
        snapshot = _capture(client, f"runtime_recover_after_later_{_timestamp()}")
    timing.add(
        step_name="runtime_recover_to_guazi_page",
        page_name="RUNTIME",
        action_name="wake_swipe_tap_icon_and_fresh",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=int((time.perf_counter() - step_started) * 1000),
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
    )
    return {
        "snapshot": snapshot,
        "wake_result": wake_result,
        "swipe_result": swipe_result,
    }


def _ensure_page(
    expected: str,
    recognizer: PageRecognizer,
    issues: IssueRecorder,
    snapshot: dict[str, Any],
) -> None:
    actual = _recognize_mainline_page(recognizer, snapshot)
    if actual != expected:
        issue = _record_runtime_issue(
            issues,
            "PAGE_CONTRACT_MISMATCH",
            actual or "UNKNOWN",
            f"Expected {expected}, recognized {actual or 'UNKNOWN'}",
            snapshot,
        )
        lookup = issue.get("knowledge_lookup") or {}
        if not lookup.get("auto_continue_allowed"):
            raise GuaziFlowError("PAGE_CONTRACT_MISMATCH", f"Expected {expected}, recognized {actual or 'UNKNOWN'}", snapshot)


def _wait_for_page(
    client: AdbClient,
    recognizer: PageRecognizer,
    expected: str,
    stem_prefix: str,
    *,
    timeout_s: float = 8.0,
    interval_s: float = 0.8,
) -> tuple[dict[str, Any], int]:
    started = time.perf_counter()
    last_snapshot: dict[str, Any] = {}
    while True:
        time.sleep(interval_s)
        last_snapshot = _capture(client, f"{stem_prefix}_{_timestamp()}")
        if _recognize_mainline_page(recognizer, last_snapshot) == expected:
            return last_snapshot, int((time.perf_counter() - started) * 1000)
        if time.perf_counter() - started >= timeout_s:
            return last_snapshot, int((time.perf_counter() - started) * 1000)


def _find_exact_label_bounds(snapshot: dict[str, Any], target: str) -> tuple[int, int, int, int] | None:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    for node in nodes:
        bounds = node.get("bounds")
        if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        labels = [str(label).strip() for label in node.get("labels", [])]
        if target in labels:
            return bounds
    return None


def _find_exact_label_bounds_after_y(
    snapshot: dict[str, Any],
    target: str,
    min_y: int,
) -> tuple[int, int, int, int] | None:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    candidates: list[tuple[int, tuple[int, int, int, int]]] = []
    for node in nodes:
        bounds = node.get("bounds")
        if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1] or bounds[1] < min_y:
            continue
        labels = [str(label).strip() for label in node.get("labels", [])]
        if target in labels:
            candidates.append((bounds[1], bounds))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def _find_body_appearance_summary_bounds(snapshot: dict[str, Any]) -> tuple[int, int, int, int] | None:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    candidates: list[tuple[int, tuple[int, int, int, int]]] = []
    for node in nodes:
        bounds = node.get("bounds")
        if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        labels = [str(node.get("text") or ""), str(node.get("content_desc") or "")]
        labels.extend(str(item) for item in node.get("labels", []))
        for raw_label in labels:
            label = raw_label.strip()
            if label and label != "车身外观" and label.startswith("车身外观"):
                candidates.append((bounds[1], bounds))
                break
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def _find_exact_label_node(snapshot: dict[str, Any], target: str) -> dict[str, Any] | None:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    for node in nodes:
        bounds = node.get("bounds")
        if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        labels = [str(label).strip() for label in node.get("labels", [])]
        if target in labels:
            return {**node, "bounds": bounds}
    return None


def _body_appearance_tab_visibility(snapshot: dict[str, Any], bounds: tuple[int, int, int, int]) -> dict[str, Any]:
    extent = _visible_bounds_extent(snapshot)
    if extent is None:
        return {
            "visible": False,
            "reason": "no_visible_xml_bounds",
            "bounds": list(bounds),
        }
    viewport, source = extent
    _x1, y1, _x2, y2 = viewport
    height = max(y2 - y1, 1)
    center_y = (bounds[1] + bounds[3]) // 2
    top_guard = y1 + int(height * 0.08)
    top_tab_max_y = y1 + int(height * 0.70)
    bottom_guard = y2 - int(height * 0.12)
    in_visible_y = bounds[1] >= y1 and bounds[3] <= y2
    in_top_tab_band = top_guard <= center_y <= top_tab_max_y
    away_from_bottom = bounds[3] <= bottom_guard
    return {
        "visible": bool(in_visible_y and in_top_tab_band and away_from_bottom),
        "reason": "top_tab_visible" if in_visible_y and in_top_tab_band and away_from_bottom else "not_in_top_navigation_tab_area",
        "bounds": list(bounds),
        "viewport_bounds": list(viewport),
        "bounds_source": source,
        "center_y": center_y,
        "top_guard": top_guard,
        "top_tab_max_y": top_tab_max_y,
        "bottom_guard": bottom_guard,
        "in_visible_y": in_visible_y,
        "in_top_tab_band": in_top_tab_band,
        "away_from_bottom": away_from_bottom,
    }


def _find_body_appearance_tab_node(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    exact_candidates: list[dict[str, Any]] = []
    neighbor_bounds: dict[str, list[tuple[int, int, int, int]]] = {
        "重大问题排查": [],
        "内饰及配置": [],
    }
    for node in nodes:
        bounds = node.get("bounds")
        if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        if "车身外观" in labels:
            exact_candidates.append({**node, "bounds": bounds})
        for neighbor in neighbor_bounds:
            if neighbor in labels:
                neighbor_bounds[neighbor].append(bounds)
    if not exact_candidates:
        return None
    for candidate in exact_candidates:
        bounds = candidate["bounds"]
        center_y = (bounds[1] + bounds[3]) // 2
        has_left_tab = any(abs(((item[1] + item[3]) // 2) - center_y) <= 120 for item in neighbor_bounds["重大问题排查"])
        has_right_tab = any(abs(((item[1] + item[3]) // 2) - center_y) <= 120 for item in neighbor_bounds["内饰及配置"])
        visibility = _body_appearance_tab_visibility(snapshot, bounds)
        if has_left_tab and has_right_tab and visibility["visible"]:
            return {**candidate, "tab_region_confirmed": True, "visibility_check": visibility}
    return None


def _find_body_appearance_tab_after_controlled_scroll(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    stem_prefix: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    timing: TimingRecorder = context["timing"]
    total_scroll_ms = 0
    for attempt in range(3):
        node = _find_body_appearance_tab_node(snapshot)
        if node is not None:
            return snapshot, node, total_scroll_ms
        points = _dynamic_history_scroll_points(client, snapshot)
        action_start = time.perf_counter()
        client.run(
            [
                "shell",
                "input",
                "swipe",
                str(points["swipe_x_start"]),
                str(points["swipe_y_start"]),
                str(points["swipe_x_end"]),
                str(points["swipe_y_end"]),
                str(points["swipe_duration_ms"]),
            ]
        )
        time.sleep(0.4)
        action_ms = int((time.perf_counter() - action_start) * 1000)
        total_scroll_ms += action_ms
        timing.add(
            step_name="S12_SCROLL_TO_BODY_APPEARANCE_TAB",
            page_name="S12",
            action_name="dynamic_mid_long_controlled_scroll_to_body_tab",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=action_ms,
            transition_wait_ms=0,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra={
                **points,
                "scroll_attempt_index": attempt,
                "reason_category": "CONTROLLED_SCROLL",
                "reason_detail": "body appearance tab is not visible yet; scroll before clicking the tab",
                "solution": "use dynamic medium-long controlled scroll, then fresh-check for exact tab text",
                "recognized_page": _recognize_mainline_page(recognizer, snapshot),
            },
        )
        fresh_start = time.perf_counter()
        snapshot = _capture(client, f"{stem_prefix}_search_tab_{attempt}_{_timestamp()}")
        fresh_ms = int((time.perf_counter() - fresh_start) * 1000)
        total_scroll_ms += fresh_ms
        timing.add(
            step_name="S12_SCROLL_TO_BODY_APPEARANCE_TAB_FRESH",
            page_name="S12",
            action_name="fresh_after_scroll_to_body_tab",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=fresh_ms,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra={
                "scroll_attempt_index": attempt,
                "recognized_page": _recognize_mainline_page(recognizer, snapshot),
                "body_appearance_tab_found": _find_body_appearance_tab_node(snapshot) is not None,
                "reason_category": "XML_DUMP_SLOW" if fresh_ms > 1000 else "FRESH_CAPTURE",
                "reason_detail": "fresh screenshot/XML/recognition after controlled scroll to body appearance tab",
                "solution": "stop scrolling as soon as exact body appearance text is visible",
            },
        )
    return snapshot, _find_body_appearance_tab_node(snapshot), total_scroll_ms


def _visible_bounds_extent(snapshot: dict[str, Any]) -> tuple[tuple[int, int, int, int], str] | None:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    visible = [
        bounds
        for node in nodes
        for bounds in [node.get("bounds")]
        if bounds and bounds[2] > bounds[0] and bounds[3] > bounds[1]
    ]
    if not visible:
        return None
    return (
        min(bounds[0] for bounds in visible),
        min(bounds[1] for bounds in visible),
        max(bounds[2] for bounds in visible),
        max(bounds[3] for bounds in visible),
    ), "xml_visible_bounds"


def _dynamic_history_scroll_points(
    client: AdbClient,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    extent = _visible_bounds_extent(snapshot)
    if extent is None:
        width, height = client.screen_size()
        bounds = (0, 0, width, height)
        source = "screen_size"
    else:
        bounds, source = extent
    x1, y1, x2, y2 = bounds
    width = max(x2 - x1, 1)
    height = max(y2 - y1, 1)
    top_guard = y1 + max(int(height * 0.20), 1)
    bottom_guard = y2 - max(int(height * 0.08), 1)
    usable = max(bottom_guard - top_guard, 1)
    x = x1 + width // 2
    y_start = bottom_guard - max(int(usable * 0.03), 1)
    y_end = top_guard + max(int(usable * 0.08), 1)
    if y_start <= y_end:
        y_start = y1 + int(height * 0.78)
        y_end = y1 + int(height * 0.30)
    return {
        "bounds_source": source,
        "scroll_region_bounds": list(bounds),
        "swipe_x_start": x,
        "swipe_y_start": y_start,
        "swipe_x_end": x,
        "swipe_y_end": y_end,
        "swipe_duration_ms": 700,
    }


def _history_arrival_reason(recognizer: PageRecognizer, snapshot: dict[str, Any]) -> str | None:
    if _has_s13_history_repair_table(snapshot):
        return "history_repair_table_tokens"
    page = _recognize_mainline_page(recognizer, snapshot)
    if page == "S13":
        return "s13_recognizer"
    texts = [str(item).strip() for item in snapshot.get("visible_texts", [])]
    if "历史修复" in texts:
        return "history_repair_text"
    if any(region in texts for region in S13_REGION_ORDER):
        return "s13_region_text"
    return None


def _record_history_arrived_timing(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    page_name: str,
    reason: str,
) -> None:
    timing: TimingRecorder = context["timing"]
    recognizer: PageRecognizer = context["recognizer"]
    timing.add(
        step_name="S13_HISTORY_TABLE_ARRIVED",
        page_name=page_name,
        action_name="history_repair_table_arrived",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=0,
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            "recognized_page": _recognize_mainline_page(recognizer, snapshot),
            "arrival_reason": reason,
            "reason_category": "PAGE_RECOGNITION_SLOW",
            "reason_detail": "stop controlled scrolling as soon as S13/history repair evidence appears",
            "solution": "do not continue scroll after history repair table evidence is present",
        },
    )


def _controlled_scroll_towards_history_repair(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    page_name: str,
    stem_prefix: str,
    max_attempts: int,
    stop_on_s13_recognizer: bool,
) -> tuple[dict[str, Any], int, bool]:
    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    timing: TimingRecorder = context["timing"]
    current = snapshot
    total_wait_ms = 0
    initial_reason = _history_arrival_reason(recognizer, current)
    if initial_reason and (stop_on_s13_recognizer or initial_reason != "s13_recognizer"):
        _record_history_arrived_timing(context, current, page_name=page_name, reason=initial_reason)
        return current, total_wait_ms, True
    for attempt in range(max_attempts):
        points = _dynamic_history_scroll_points(client, current)
        action_start = time.perf_counter()
        client.run(
            [
                "shell",
                "input",
                "swipe",
                str(points["swipe_x_start"]),
                str(points["swipe_y_start"]),
                str(points["swipe_x_end"]),
                str(points["swipe_y_end"]),
                str(points["swipe_duration_ms"]),
            ]
        )
        time.sleep(0.4)
        action_ms = int((time.perf_counter() - action_start) * 1000)
        total_wait_ms += action_ms
        timing.add(
            step_name="S13_HISTORY_REPAIR_TABLE_SCROLL",
            page_name=page_name,
            action_name="dynamic_mid_long_controlled_scroll",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=action_ms,
            transition_wait_ms=0,
            screenshot_path=str(current.get("screenshot_path") or ""),
            xml_path=str(current.get("xml_path") or ""),
            extra={
                **points,
                "scroll_attempt_index": attempt,
                "over_1s": action_ms > 1000,
                "reason_category": "PAGE_LOAD_SLOW" if action_ms > 1000 else "CONTROLLED_SCROLL",
                "reason_detail": "use dynamic medium-long controlled scroll within report/body area",
                "solution": "increase scroll distance while keeping fresh page evidence after each scroll",
                "recognized_page": _recognize_mainline_page(recognizer, current),
            },
        )
        fresh_start = time.perf_counter()
        current = _capture(client, f"{stem_prefix}_{attempt}_{_timestamp()}")
        fresh_ms = int((time.perf_counter() - fresh_start) * 1000)
        total_wait_ms += fresh_ms
        recognized_page = _recognize_mainline_page(recognizer, current)
        timing.add(
            step_name="S13_HISTORY_REPAIR_TABLE_SCROLL_FRESH",
            page_name=page_name,
            action_name="fresh_after_dynamic_history_scroll",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=fresh_ms,
            screenshot_path=str(current.get("screenshot_path") or ""),
            xml_path=str(current.get("xml_path") or ""),
            extra={
                "scroll_attempt_index": attempt,
                "over_1s": fresh_ms > 1000,
                "recognized_page": recognized_page,
                "visible_text_digest": str(current.get("visible_blob") or "")[:500],
                "reason_category": "XML_DUMP_SLOW" if fresh_ms > 1000 else "FRESH_CAPTURE",
                "reason_detail": "fresh screenshot/XML/recognition after every controlled scroll",
                "solution": "stop immediately once history repair table evidence is visible",
            },
        )
        reason = _history_arrival_reason(recognizer, current)
        if reason and (stop_on_s13_recognizer or reason != "s13_recognizer"):
            _record_history_arrived_timing(context, current, page_name=page_name, reason=reason)
            return current, total_wait_ms, True
        if recognized_page not in {"S12", "S13"}:
            return current, total_wait_ms, False
    return current, total_wait_ms, False


def _find_exact_label_in_safe_area(
    client: AdbClient,
    snapshot: dict[str, Any],
    target: str,
    stem_prefix: str,
) -> tuple[dict[str, Any], tuple[int, int, int, int] | None, int]:
    total_scroll_ms = 0
    for attempt in range(3):
        bounds = _find_exact_label_bounds(snapshot, target)
        if bounds is None:
            client.swipe("up")
            time.sleep(0.6)
            snapshot = _capture(client, f"{stem_prefix}_search_{attempt}_{_timestamp()}")
            total_scroll_ms += 600
            continue
        width, height = client.screen_size()
        center_y = (bounds[1] + bounds[3]) // 2
        safe_min_y = int(height * 0.12)
        safe_max_y = int(height * 0.72)
        if safe_min_y <= center_y <= safe_max_y:
            return snapshot, bounds, total_scroll_ms
        if center_y > safe_max_y:
            start_y = min(int(height * 0.74), height - 320)
            end_y = int(height * 0.45)
            client.run(
                [
                    "shell",
                    "input",
                    "swipe",
                    str(width // 2),
                    str(start_y),
                    str(width // 2),
                    str(end_y),
                    "700",
                ]
            )
            time.sleep(0.8)
            snapshot = _capture(client, f"{stem_prefix}_safe_scroll_{attempt}_{_timestamp()}")
            total_scroll_ms += 800
            continue
        return snapshot, bounds, total_scroll_ms
    return snapshot, _find_exact_label_bounds(snapshot, target), total_scroll_ms


def _parse_s10_cards(texts: list[str]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for text in texts:
        value = str(text).strip()
        if not value:
            continue
        price_match = re.search(r"(\d+(?:\.\d+)?)\s*万", value)
        year_match = re.search(r"(20\d{2})", value)
        mileage_match = re.search(r"(\d+(?:\.\d+)?)\s*万公里", value)
        if price_match:
            if current:
                cards.append(current)
            current = {"title": value, "list_price_10k": float(price_match.group(1))}
        if year_match:
            current["list_year"] = int(year_match.group(1))
        if mileage_match:
            current["list_mileage_10k_km"] = float(mileage_match.group(1))
    if current:
        cards.append(current)
    return cards


def _node_label(node: dict[str, Any]) -> str:
    labels = [str(node.get("text") or ""), str(node.get("content_desc") or "")]
    labels.extend(str(item) for item in node.get("labels", []))
    return next((item.strip() for item in labels if item and item.strip()), "")


def _valid_bounds(bounds: tuple[int, int, int, int] | None) -> bool:
    return bool(bounds and bounds[2] > bounds[0] and bounds[3] > bounds[1] and bounds[2] > 0 and bounds[3] > 0)


def _overlaps_y(a: tuple[int, int, int, int], b: tuple[int, int, int, int], tolerance: int = 18) -> bool:
    return max(a[1], b[1]) <= min(a[3], b[3]) + tolerance


def _parse_card_year_mileage(value: str) -> tuple[int | None, float | None]:
    match = re.search(r"(20\d{2})\s*\u5e74\s*\|\s*(\d+(?:\.\d+)?)\s*\u4e07\u516c\u91cc", value)
    if not match:
        return None, None
    return int(match.group(1)), float(match.group(2))


def _parse_card_price(value: str) -> float | None:
    if any(token in value for token in ["\u9996\u4ed8", "\u5df2\u51cf", "\u6708\u4f9b", "\u8bb2\u4ef7"]):
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)", value)
    if match:
        return float(match.group(1))
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*\u4e07", value)
    if match:
        return float(match.group(1))
    return None


def _extract_s10_reference_cards(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    visible_nodes: list[dict[str, Any]] = []
    for node in nodes:
        bounds = node.get("bounds")
        if _valid_bounds(bounds):
            visible_nodes.append(node)

    target_brand = str(snapshot.get("target_brand") or "").strip()
    title_nodes: list[dict[str, Any]] = []
    for node in visible_nodes:
        label = _node_label(node)
        if not label:
            continue
        if not re.search(r"20\d{2}\u6b3e", label):
            continue
        if target_brand and target_brand not in label:
            continue
        if any(token in label for token in ["\u54c1\u724c\u4e13\u533a", "\u641c\u7d22", "\u7b5b\u9009"]):
            continue
        title_nodes.append(node)

    title_nodes.sort(key=lambda item: (item["bounds"][1], item["bounds"][0]))
    cards: list[dict[str, Any]] = []
    for index, title_node in enumerate(title_nodes, start=1):
        title_bounds = title_node["bounds"]
        title = _node_label(title_node)
        next_title_y = title_nodes[index]["bounds"][1] if index < len(title_nodes) else title_bounds[1] + 380
        bottom_y = min(next_title_y, title_bounds[1] + 380)
        card_nodes = [
            node
            for node in visible_nodes
            if title_bounds[1] - 20 <= node["bounds"][1] < bottom_y
        ]

        year = None
        mileage = None
        info_text = ""
        price_text = ""
        price = None
        price_bounds = None
        for node in card_nodes:
            label = _node_label(node)
            parsed_year, parsed_mileage = _parse_card_year_mileage(label)
            if parsed_year is not None and parsed_mileage is not None:
                year = parsed_year
                mileage = parsed_mileage
                info_text = label
                break

        for node in card_nodes:
            label = _node_label(node)
            candidate_price = _parse_card_price(label)
            bounds = node.get("bounds")
            if candidate_price is None or not _valid_bounds(bounds):
                continue
            unit_on_same_row = any(
                _node_label(unit_node) == "\u4e07"
                and _valid_bounds(unit_node.get("bounds"))
                and unit_node["bounds"][0] >= bounds[2] - 8
                and _overlaps_y(unit_node["bounds"], bounds)
                for unit_node in card_nodes
            )
            if unit_on_same_row:
                price = candidate_price
                price_text = f"{label}\u4e07"
                price_bounds = bounds
                break

        text_digest = [_node_label(node) for node in card_nodes if _node_label(node)]
        card = {
            "reference_index": index,
            "reference_key": f"{index}:{title}:{info_text}:{price_text}",
            "list_title": title,
            "list_price_text": price_text,
            "list_price_10k": price,
            "list_year": year,
            "list_mileage_10k_km": mileage,
            "clicked_card_bounds": title_bounds,
            "clicked_card_text_digest": text_digest,
            "price_bounds": price_bounds,
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
        }
        cards.append(card)
    return cards


def _find_clickable_vehicle_title(snapshot: dict[str, Any]) -> tuple[int, int] | None:
    candidates: list[tuple[int, tuple[int, int]]] = []
    target_brand = str(snapshot.get("target_brand") or "").strip()
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    for node in nodes:
        bounds = node.get("bounds")
        if not bounds or not any(bounds):
            continue
        labels = [str(node.get("text") or ""), str(node.get("content_desc") or "")]
        labels.extend(str(item) for item in node.get("labels", []))
        for raw_label in labels:
            label = raw_label.strip()
            if not label:
                continue
            if any(token in label for token in ["万公里", "微信咨询", "联系卖家", "查看完整报告", "理赔次数", "车身外观"]):
                continue
            if "|" in label or "筛选" in label or "排序" in label or "全部能源类型" in label:
                continue
            if not re.search(r"20\d{2}款", label):
                continue
            if target_brand and target_brand not in label:
                continue
            candidates.append((bounds[1], _center(bounds)))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def _select_s10_reference_card(snapshot: dict[str, Any]) -> tuple[dict[str, Any], tuple[int, int]] | None:
    cards = _extract_s10_reference_cards(snapshot)
    if not cards:
        return None
    card = cards[0]
    bounds = card.get("clicked_card_bounds")
    if not _valid_bounds(bounds):
        return None
    return card, _center(bounds)


def _float_same(left: Any, right: Any, *, tolerance: float = 0.001) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def _s10_card_matches_reference(card: dict[str, Any], reference: dict[str, Any]) -> bool:
    if str(card.get("list_title") or "").strip() != str(reference.get("list_title") or "").strip():
        return False
    if reference.get("reference_index") is not None and int(card.get("reference_index") or -1) != int(reference["reference_index"]):
        return False
    if reference.get("list_price_text") and str(card.get("list_price_text") or "") != str(reference.get("list_price_text") or ""):
        return False
    if reference.get("list_price_10k") is not None and not _float_same(card.get("list_price_10k"), reference.get("list_price_10k")):
        return False
    if reference.get("list_year") is not None and card.get("list_year") != reference.get("list_year"):
        return False
    if reference.get("list_mileage_10k_km") is not None and not _float_same(card.get("list_mileage_10k_km"), reference.get("list_mileage_10k_km")):
        return False
    return True


def _resolve_s10_title_text_click_target(snapshot: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    list_title = str(reference.get("list_title") or "").strip()
    if not list_title:
        return {
            "ok": False,
            "stop_code": "S10_TITLE_TEXT_NODE_NOT_FOUND",
            "reason": "current_reference.list_title is empty",
            "title_candidate_count": 0,
            "matched_candidate_count": 0,
        }
    title_candidates = [
        card
        for card in _extract_s10_reference_cards(snapshot)
        if str(card.get("list_title") or "").strip() == list_title
    ]
    if not title_candidates:
        return {
            "ok": False,
            "stop_code": "S10_TITLE_TEXT_NODE_NOT_FOUND",
            "reason": "No exact title text node matched current_reference.list_title.",
            "clicked_text": list_title,
            "title_candidate_count": 0,
            "matched_candidate_count": 0,
        }
    matched = [card for card in title_candidates if _s10_card_matches_reference(card, reference)]
    if len(matched) != 1:
        return {
            "ok": False,
            "stop_code": "S10_TITLE_TEXT_NODE_NOT_UNIQUE",
            "reason": "Exact title text node could not be uniquely bound to current_reference by index, price, year, and mileage.",
            "clicked_text": list_title,
            "title_candidate_count": len(title_candidates),
            "matched_candidate_count": len(matched),
            "title_candidates": [
                {
                    "reference_index": item.get("reference_index"),
                    "reference_key": item.get("reference_key"),
                    "list_price_text": item.get("list_price_text"),
                    "list_year": item.get("list_year"),
                    "list_mileage_10k_km": item.get("list_mileage_10k_km"),
                    "clicked_card_bounds": item.get("clicked_card_bounds"),
                }
                for item in title_candidates
            ],
        }
    target = matched[0]
    bounds = target.get("clicked_card_bounds")
    if not _valid_bounds(bounds):
        return {
            "ok": False,
            "stop_code": "S10_TITLE_TEXT_NODE_NOT_FOUND",
            "reason": "Matched title text node has invalid bounds.",
            "clicked_text": list_title,
            "title_candidate_count": len(title_candidates),
            "matched_candidate_count": len(matched),
        }
    return {
        "ok": True,
        "click_strategy": "text_node_bounds",
        "clicked_text": list_title,
        "clicked_node_bounds": bounds,
        "clicked_point": _center(bounds),
        "title_candidate_count": len(title_candidates),
        "matched_candidate_count": len(matched),
        "matched_reference_key": target.get("reference_key"),
    }


def _wait_for_s11_stable_after_title_click(
    client: AdbClient,
    recognizer: PageRecognizer,
    before_snapshot: dict[str, Any],
    stem_prefix: str,
    *,
    timeout_s: float = 15.0,
    interval_s: float = 0.5,
) -> tuple[dict[str, Any], int, dict[str, Any]]:
    started = time.perf_counter()
    before_xml_sha256 = _sha256_text(str(before_snapshot.get("fresh_xml") or ""))
    before_screenshot_sha256 = _sha256_file(before_snapshot.get("screenshot_path"))
    wait_rounds: list[dict[str, Any]] = []
    last_snapshot: dict[str, Any] = {}
    round_index = 0
    while True:
        time.sleep(interval_s)
        round_index += 1
        capture_started = time.perf_counter()
        last_snapshot = _capture(client, f"{stem_prefix}_{_timestamp()}")
        capture_total_ms = int((time.perf_counter() - capture_started) * 1000)
        recognize_started = time.perf_counter()
        recognized_page = _recognize_mainline_page(recognizer, last_snapshot)
        recognize_ms = int((time.perf_counter() - recognize_started) * 1000)
        capture_metrics = last_snapshot.get("capture_metrics") or {}
        current_xml_sha256 = _sha256_text(str(last_snapshot.get("fresh_xml") or ""))
        current_screenshot_sha256 = _sha256_file(last_snapshot.get("screenshot_path"))
        xml_stale = bool(before_xml_sha256 and current_xml_sha256 == before_xml_sha256)
        screenshot_changed = bool(
            before_screenshot_sha256
            and current_screenshot_sha256
            and current_screenshot_sha256 != before_screenshot_sha256
        )
        wait_rounds.append(
            {
                "wait_round_index": round_index,
                "screenshot_path": str(last_snapshot.get("screenshot_path") or ""),
                "xml_path": str(last_snapshot.get("xml_path") or ""),
                "screenshot_ms": int(capture_metrics.get("screenshot_ms") or 0),
                "xml_dump_ms": int(capture_metrics.get("xml_ms") or 0),
                "xml_rc": 0 if last_snapshot.get("xml_path") else None,
                "xml_stderr": "",
                "dumpsys_ms": int(capture_metrics.get("dumpsys_ms") or 0),
                "capture_total_ms": capture_total_ms,
                "recognize_ms": recognize_ms,
                "before_xml_sha256": before_xml_sha256,
                "current_xml_sha256": current_xml_sha256,
                "xml_stale": xml_stale,
                "screenshot_changed": screenshot_changed,
                "recognized_page": recognized_page,
                "visible_text_digest": list(last_snapshot.get("visible_texts", []))[:40],
                "foreground_package": str(last_snapshot.get("foreground_package") or ""),
                "focused_window": str(last_snapshot.get("focused_window") or ""),
                "activity": str(last_snapshot.get("resumed_activity") or ""),
            }
        )
        if recognized_page == "S11":
            return last_snapshot, int((time.perf_counter() - started) * 1000), {
                "entered_s11": True,
                "wait_rounds": wait_rounds,
                "wait_round_count": round_index,
                "before_xml_sha256": before_xml_sha256,
                "xml_stale_during_detail_load": any(item["xml_stale"] and item["screenshot_changed"] for item in wait_rounds),
                "short_poll_interval_s": interval_s,
                "total_wait_timeout_s": timeout_s,
            }
        if time.perf_counter() - started >= timeout_s:
            return last_snapshot, int((time.perf_counter() - started) * 1000), {
                "entered_s11": False,
                "wait_rounds": wait_rounds,
                "wait_round_count": round_index,
                "before_xml_sha256": before_xml_sha256,
                "xml_stale_during_detail_load": any(item["xml_stale"] and item["screenshot_changed"] for item in wait_rounds),
                "any_screenshot_changed": any(item["screenshot_changed"] for item in wait_rounds),
                "any_xml_changed": any(not item["xml_stale"] for item in wait_rounds),
                "short_poll_interval_s": interval_s,
                "total_wait_timeout_s": timeout_s,
            }


def _load_current_target_task() -> dict[str, Any]:
    task_path = project_path("data", "current_target_task.json")
    data = json.loads(task_path.read_text(encoding="utf-8"))
    params = {
        "brand": data.get("brand"),
        "series": data.get("series"),
        "model_year": data.get("year_model") or data.get("model_year"),
        "trim": data.get("config_model") or data.get("trim"),
        "color": data.get("color"),
        "registration_date_raw": data.get("register_date") or data.get("registration_date"),
        "mileage_10k_km": data.get("mileage_10k_km") or data.get("display_mileage_wan_km"),
        "transfer_count": data.get("transfer_count"),
        "condition_text": data.get("condition_text"),
        "accident_count": data.get("accident_count"),
        "max_accident_amount": data.get("max_accident_amount"),
    }
    return {
        "file_path": str(task_path),
        "status": "TASK_IMPORT_VERIFIED",
        "task_id": str(data.get("task_id") or ""),
        "brand": params["brand"],
        "series": params["series"],
        "model_year": params["model_year"],
        "trim": params["trim"],
        "color": params["color"],
        "registration_date_raw": params["registration_date_raw"],
        "mileage_10k_km": params["mileage_10k_km"],
        "transfer_count": params["transfer_count"],
        "condition_text": params["condition_text"],
        "accident_count": params["accident_count"],
        "max_accident_amount": params["max_accident_amount"],
        "app_operation_params": params,
        "task": data,
    }


def _segment2_task_payload(task_result: dict[str, Any] | None) -> dict[str, Any]:
    task_result = task_result or {}
    return {
        "target_task_path": task_result.get("file_path"),
        "brand": task_result.get("brand"),
        "series": task_result.get("series"),
        "year_model": task_result.get("model_year"),
        "config_model": task_result.get("trim"),
        "color": task_result.get("color"),
        "register_date": task_result.get("registration_date_raw"),
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


RESULT_OMIT_KEYS = {"fresh_xml", "nodes", "raw_xml", "xml_text", "visible_texts", "visible_blob"}
RESULT_STRING_LIMIT = 4000
RESULT_LIST_LIMIT = 80
RESULT_DEPTH_LIMIT = 12


def _result_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > RESULT_DEPTH_LIMIT:
        return "<depth_limited>"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in RESULT_OMIT_KEYS:
                continue
            output[key_text] = _result_safe(item, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        items = list(value)
        safe_items = [_result_safe(item, depth=depth + 1) for item in items[:RESULT_LIST_LIMIT]]
        if len(items) > RESULT_LIST_LIMIT:
            safe_items.append({"truncated_count": len(items) - RESULT_LIST_LIMIT})
        return safe_items
    if isinstance(value, str):
        if len(value) > RESULT_STRING_LIMIT:
            return value[:RESULT_STRING_LIMIT] + f"...<truncated {len(value) - RESULT_STRING_LIMIT} chars>"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _s14_completion_metrics(context: dict[str, Any]) -> dict[str, int]:
    if context.get("s14_image_sequence_model"):
        image_records = context.get("s14_image_records") or []
        terminal_confirmed = bool(context.get("s14_sequence_terminal_confirmed"))
        images_processed = len(image_records)
        return {
            "s14_tabs_total": 1,
            "s14_tabs_processed": 1 if terminal_confirmed else 0,
            "s14_images_total": images_processed if terminal_confirmed else max(1, images_processed + 1),
            "s14_images_processed": images_processed,
        }
    tabs = context.get("all_s14_tabs") or []
    tab_records = [
        item
        for item in context.get("s14_tab_records", [])
        if isinstance(item, dict) and item.get("tab_processed")
    ]
    processed_tab_labels = {
        str(item.get("tab_label") or "")
        for item in tab_records
        if item.get("tab_label")
    }
    image_records = context.get("s14_image_records") or []
    images_total = 0
    for tab in tabs:
        try:
            images_total += max(1, int(tab.get("total_pages") or 1))
        except (TypeError, ValueError):
            images_total += 1
    return {
        "s14_tabs_total": len(tabs),
        "s14_tabs_processed": len(processed_tab_labels),
        "s14_images_total": images_total,
        "s14_images_processed": len(image_records),
    }


def _s14_completion_evidence(context: dict[str, Any]) -> dict[str, bool]:
    metrics = _s14_completion_metrics(context)
    image_records = context.get("s14_image_records") or []
    all_images_accounted = metrics["s14_images_total"] > 0 and metrics["s14_images_processed"] == metrics["s14_images_total"]
    if context.get("s14_image_sequence_model"):
        all_tabs_accounted = bool(context.get("s14_sequence_terminal_confirmed"))
    else:
        all_tabs_accounted = metrics["s14_tabs_total"] > 0 and metrics["s14_tabs_processed"] == metrics["s14_tabs_total"]
    all_images_decided = all(
        bool(item.get("saved_to_repair_items")) or bool(item.get("skipped_reason"))
        for item in image_records
    )
    return {
        "all_tabs_detected": metrics["s14_tabs_total"] > 0,
        "all_tabs_processed": all_tabs_accounted,
        "all_images_processed": all_images_accounted,
        "all_target_repairs_recorded": all_images_decided,
        "non_target_items_skipped": all_images_decided,
    }


def _store_s14_metrics(context: dict[str, Any]) -> dict[str, int]:
    metrics = _s14_completion_metrics(context)
    context.update(metrics)
    context.setdefault("current_reference", {}).update(metrics)
    evidence = _s14_completion_evidence(context)
    context.update(evidence)
    context.setdefault("current_reference", {}).update(evidence)
    return metrics


def _evaluate_special_structure_reference(repair_items: list[dict[str, Any]]) -> dict[str, Any]:
    manual_reasons: list[str] = []
    disqualify_reason = ""
    for item in repair_items:
        part = str(item.get("normalized_part") or item.get("part") or "")
        damage = str(item.get("normalized_damage") or "")
        if part == "ABC柱" and damage == "更换":
            disqualify_reason = "ABC柱更换，判定事故车。"
        if part == "水箱框架" and damage in {"钣金", "喷漆"}:
            manual_reasons.append("水箱框架钣金/喷漆扣分等同项待规则补充。")
    return {
        "reference_disqualified": bool(disqualify_reason),
        "disqualify_reason": disqualify_reason,
        "manual_review_required": bool(manual_reasons),
        "manual_review_reasons": manual_reasons,
    }


def _write_result_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_second_stage_result(configs: dict[str, Any], result: dict[str, Any], task_result: dict[str, Any] | None = None) -> None:
    enriched = dict(result)
    task = _segment2_task_payload(task_result)
    enriched.setdefault("target_fingerprint", _target_fingerprint(task))
    enriched.setdefault("target_task_path", task.get("target_task_path"))
    enriched.setdefault("brand", task.get("brand"))
    enriched.setdefault("series", task.get("series"))
    enriched.setdefault("year_model", task.get("year_model"))
    enriched.setdefault("config_model", task.get("config_model"))
    enriched.setdefault("color", task.get("color"))
    enriched.setdefault("register_date", task.get("register_date"))
    enriched.setdefault("current_state", enriched.get("status") or enriched.get("state"))
    enriched.setdefault("final_status", enriched.get("final_status") or enriched.get("status"))
    enriched.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    enriched.setdefault("result_segment", "s10_to_s16")
    enriched = _result_safe(enriched)
    _write_result_json_file(project_path("output", "result_s10_to_s16.json"), enriched)
    _write_result_json_file(project_path(configs["system"]["paths"]["result_json"]), enriched)


def _load_first_stage_s10_ready_evidence() -> dict[str, Any]:
    result_path = project_path("output", "result_s01_to_s10.json")
    if not result_path.exists():
        return {"path": str(result_path), "ready": False, "reason": "segment1_result_missing"}
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"path": str(result_path), "ready": False, "reason": f"segment1_result_invalid:{exc}"}
    flow_state = result.get("flow_state") or {}
    ready = (
        result.get("status") == "S10_READY"
        and flow_state.get("S07_FILTER_DONE") is True
        and flow_state.get("COLOR_FILTER_DONE") is True
        and flow_state.get("AGE_FILTER_DONE") is True
        and flow_state.get("SORT_DONE") is True
        and flow_state.get("S10_READY") is True
    )
    return {
        "path": str(result_path),
        "ready": ready,
        "source": "segment1_result",
        "status": result.get("status"),
        "final_status": result.get("final_status"),
        "target_fingerprint": result.get("target_fingerprint"),
        "flow_state": flow_state,
    }


def handle_s10(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    client: AdbClient = context["client"]
    start = time.perf_counter()
    _ensure_page("S10", recognizer, issues, snapshot)
    machine.assert_action_allowed("S10", "collect_list_whitelist_fields")
    read_start = time.perf_counter()
    cards = _extract_s10_reference_cards(snapshot)
    context["current_list_cards"] = cards
    field_ms = int((time.perf_counter() - read_start) * 1000)
    machine.assert_action_allowed("S10", "tap_next_car_by_price_order")
    selected = _select_s10_reference_card(snapshot)
    if selected is None:
        issue = issues.record("ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S10", "No clickable vehicle title found in sorted list.", snapshot, "manual_review")
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    selected_card, _legacy_tap_point = selected
    context["current_reference_index"] = int(selected_card["reference_index"])
    context["current_reference"] = {
        "reference_index": selected_card.get("reference_index"),
        "reference_key": selected_card.get("reference_key"),
        "list_title": selected_card.get("list_title"),
        "list_price_text": selected_card.get("list_price_text"),
        "list_price_10k": selected_card.get("list_price_10k"),
        "list_year": selected_card.get("list_year"),
        "list_mileage_10k_km": selected_card.get("list_mileage_10k_km"),
        "clicked_card_bounds": selected_card.get("clicked_card_bounds"),
        "clicked_card_text_digest": selected_card.get("clicked_card_text_digest"),
        "s10_screenshot_path": selected_card.get("screenshot_path"),
        "s10_xml_path": selected_card.get("xml_path"),
    }
    click_target = _resolve_s10_title_text_click_target(snapshot, context["current_reference"])
    context["current_reference"].update(
        {
            "click_strategy": click_target.get("click_strategy") or "text_node_bounds",
            "clicked_text": click_target.get("clicked_text"),
            "clicked_node_bounds": click_target.get("clicked_node_bounds"),
            "clicked_point": click_target.get("clicked_point"),
            "title_candidate_count": click_target.get("title_candidate_count"),
            "matched_candidate_count": click_target.get("matched_candidate_count"),
        }
    )
    if not click_target.get("ok"):
        issue = issues.record(
            str(click_target.get("stop_code") or "S10_TITLE_TEXT_NODE_NOT_FOUND"),
            "S10",
            str(click_target.get("reason") or "Could not resolve exact vehicle title text node."),
            {
                "current_reference": context["current_reference"],
                "click_target_resolution": click_target,
                **snapshot,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    tap_point = click_target["clicked_point"]
    action_start = time.perf_counter()
    result = client.tap(*tap_point)
    action_ms = int((time.perf_counter() - action_start) * 1000)
    timing.add(
        step_name="S10_CLICK_TITLE_TEXT",
        page_name="S10",
        action_name="tap_title_text_node_bounds",
        contract_check_ms=int((read_start - start) * 1000),
        field_read_ms=field_ms,
        action_ms=action_ms,
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            "click_strategy": "text_node_bounds",
            "clicked_text": click_target.get("clicked_text"),
            "clicked_node_bounds": click_target.get("clicked_node_bounds"),
            "clicked_point": tap_point,
            "reference_index": context["current_reference"].get("reference_index"),
            "reference_key": context["current_reference"].get("reference_key"),
            "reason_category": "S10_TITLE_TEXT_NODE_CLICK",
            "reason_detail": "tap is driven by the exact list_title XML node bounds",
            "solution": "keep text_node_bounds and avoid fixed coordinates",
        },
    )
    next_snapshot, wait_ms, wait_evidence = _wait_for_s11_stable_after_title_click(
        client,
        recognizer,
        snapshot,
        "s10_to_s11",
        timeout_s=15.0,
        interval_s=0.5,
    )
    context["current_reference"]["s10_to_s11_wait"] = wait_evidence
    for item in wait_evidence.get("wait_rounds", []):
        timing.add(
            step_name="S10_TO_S11_SCREENSHOT",
            page_name="S10",
            action_name="capture_screenshot_during_s11_wait",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=int(item.get("screenshot_ms") or 0),
            transition_wait_ms=0,
            screenshot_path=str(item.get("screenshot_path") or ""),
            xml_path="",
            extra={
                "wait_round_index": item.get("wait_round_index"),
                "screenshot_ms": item.get("screenshot_ms"),
                "screenshot_changed": item.get("screenshot_changed"),
                "reason_category": "S10_TO_S11_PAGE_LOAD_SLOW" if int(item.get("screenshot_ms") or 0) > 1000 else "S10_TO_S11_SCREENSHOT",
                "reason_detail": "separate screenshot timing for S10 to S11 short-poll wait round",
                "solution": "keep short polling and continue only after S11 contract is recognized",
            },
        )
        timing.add(
            step_name="S10_TO_S11_XML_DUMP",
            page_name="S10",
            action_name="dump_xml_during_s11_wait",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=int(item.get("xml_dump_ms") or 0),
            transition_wait_ms=0,
            screenshot_path="",
            xml_path=str(item.get("xml_path") or ""),
            extra={
                "wait_round_index": item.get("wait_round_index"),
                "xml_dump_ms": item.get("xml_dump_ms"),
                "xml_rc": item.get("xml_rc"),
                "xml_stderr": item.get("xml_stderr"),
                "xml_stale": item.get("xml_stale"),
                "reason_category": "S10_TO_S11_XML_DUMP_SLOW" if int(item.get("xml_dump_ms") or 0) > 1000 else "S10_TO_S11_XML_DUMP",
                "reason_detail": "separate XML dump timing for S10 to S11 short-poll wait round",
                "solution": "do not use stale S10 XML for S11 collection; keep bounded fresh polling",
            },
        )
        timing.add(
            step_name="S10_TO_S11_RECOGNIZE",
            page_name="S10",
            action_name="recognize_page_during_s11_wait",
            contract_check_ms=0,
            field_read_ms=int(item.get("recognize_ms") or 0),
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str(item.get("screenshot_path") or ""),
            xml_path=str(item.get("xml_path") or ""),
            extra={
                "wait_round_index": item.get("wait_round_index"),
                "recognize_ms": item.get("recognize_ms"),
                "recognized_page": item.get("recognized_page"),
                "visible_text_digest": item.get("visible_text_digest"),
                "reason_category": "S10_TO_S11_RECOGNITION_SLOW" if int(item.get("recognize_ms") or 0) > 1000 else "S10_TO_S11_RECOGNIZE",
                "reason_detail": "separate recognizer timing for S10 to S11 short-poll wait round",
                "solution": "enter S11 handler only after the S11 contract is recognized",
            },
        )
        timing.add(
            step_name="S10_TO_S11_WAIT_ROUND",
            page_name="S10",
            action_name="fresh_recognize_wait_round",
            contract_check_ms=0,
            field_read_ms=int(item.get("recognize_ms") or 0),
            action_ms=int(item.get("screenshot_ms") or 0) + int(item.get("xml_dump_ms") or 0),
            transition_wait_ms=500,
            screenshot_path=str(item.get("screenshot_path") or ""),
            xml_path=str(item.get("xml_path") or ""),
            extra={
                "wait_round_index": item.get("wait_round_index"),
                "screenshot_ms": item.get("screenshot_ms"),
                "xml_dump_ms": item.get("xml_dump_ms"),
                "recognize_ms": item.get("recognize_ms"),
                "recognized_page": item.get("recognized_page"),
                "xml_stale": item.get("xml_stale"),
                "screenshot_changed": item.get("screenshot_changed"),
                "visible_text_digest": item.get("visible_text_digest"),
                "reason_category": "S10_TO_S11_WEBVIEW_TEXT_DELAY",
                "reason_detail": "short-poll fresh round waits for S11 contract instead of using a long sleep",
                "solution": "continue polling until S11 appears or the bounded timeout expires",
            },
        )
    timing.add(
        step_name="S10_TO_S11_STABLE",
        page_name="S10",
        action_name="wait_s11_contract_stable",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=0,
        transition_wait_ms=wait_ms,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
        extra={
            "entered_s11": wait_evidence.get("entered_s11"),
            "wait_round_count": wait_evidence.get("wait_round_count"),
            "xml_stale_during_detail_load": wait_evidence.get("xml_stale_during_detail_load"),
            "short_poll_interval_s": wait_evidence.get("short_poll_interval_s"),
            "total_wait_timeout_s": wait_evidence.get("total_wait_timeout_s"),
            "reason_category": "S10_TO_S11_PAGE_LOAD_SLOW",
            "reason_detail": "total S10-to-S11 wait is reported separately from the title tap",
            "solution": "keep exact title click and bounded short-poll recognition",
        },
    )
    timing.add(
        step_name="S10_TO_S11",
        page_name="S10",
        action_name="tap_title_text_node_bounds",
        contract_check_ms=int((read_start - start) * 1000),
        field_read_ms=field_ms,
        action_ms=action_ms,
        transition_wait_ms=wait_ms,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
    )
    if not result.success:
        issue = issues.record(
            "CLICK_TARGET_NOT_ENTERED",
            "S10",
            "Failed to tap target vehicle title text node.",
            {
                "click_strategy": "text_node_bounds",
                "clicked_text": click_target.get("clicked_text"),
                "clicked_node_bounds": click_target.get("clicked_node_bounds"),
                "clicked_point": tap_point,
                "current_reference": context["current_reference"],
                **snapshot,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if _recognize_mainline_page(recognizer, next_snapshot) != "S11":
        if not wait_evidence.get("any_screenshot_changed") and not wait_evidence.get("any_xml_changed"):
            stop_code = "S10_CLICK_TITLE_TEXT_NO_EFFECT"
            message = "Tapped exact title text node but screenshot and XML did not change."
        elif wait_evidence.get("xml_stale_during_detail_load"):
            stop_code = "S10_TO_S11_DETAIL_LOAD_TIMEOUT"
            message = "Tapped exact title text node; screenshot changed but XML stayed stale and S11 contract did not become stable before timeout."
        else:
            stop_code = "S10_TO_S11_RECOGNITION_FAILED"
            message = "Tapped exact title text node; page changed but S11 contract was not recognized before timeout."
        issue = issues.record(
            stop_code,
            "S10",
            message,
            {
                "click_strategy": "text_node_bounds",
                "clicked_text": click_target.get("clicked_text"),
                "clicked_node_bounds": click_target.get("clicked_node_bounds"),
                "clicked_point": tap_point,
                "current_reference": context["current_reference"],
                "s10_to_s11_wait": wait_evidence,
                **next_snapshot,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    return "S11", next_snapshot


def _extract_transfer_count(text: str) -> int | None:
    match = re.search(r"过户(?:次数)?\s*(\d+)", text)
    return int(match.group(1)) if match else None


def handle_s11(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    client: AdbClient = context["client"]
    start = time.perf_counter()
    _ensure_page("S11", recognizer, issues, snapshot)
    text = str(snapshot.get("visible_blob") or "")
    read_start = time.perf_counter()
    transfer_count = _extract_transfer_count(text)
    if transfer_count is None:
        issue = issues.record("FIELD_MISSING", "S11", "Failed to read transfer count.", snapshot, "manual_review")
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context["current_reference"]["transfer_count"] = transfer_count
    field_ms = int((time.perf_counter() - read_start) * 1000)
    machine.assert_action_allowed("S11", "scroll_to_report")
    search_start = time.perf_counter()
    client.swipe("up")
    time.sleep(0.5)
    scrolled = _capture(client, f"s11_scroll_report_{_timestamp()}")
    search_ms = int((time.perf_counter() - search_start) * 1000)
    timing.add(
        step_name="S11_REPORT_SEARCH",
        page_name="S11",
        action_name="scroll_to_report",
        contract_check_ms=int((read_start - start) * 1000),
        field_read_ms=field_ms,
        action_ms=max(0, search_ms - 500),
        transition_wait_ms=500,
        screenshot_path=str(scrolled.get("screenshot_path") or ""),
        xml_path=str(scrolled.get("xml_path") or ""),
    )
    report_bounds = None
    for node in scrolled.get("nodes", []):
        if "查看完整报告" in "".join(node.get("labels", [])) and node.get("bounds"):
            report_bounds = node["bounds"]
            break
    if report_bounds is None:
        issue = issues.record("ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S11", "查看完整报告 not found after scroll.", scrolled, "manual_review")
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    machine.assert_action_allowed("S11", "tap_full_report")
    action_start = time.perf_counter()
    client.tap(*_center(report_bounds))
    action_ms = int((time.perf_counter() - action_start) * 1000)
    time.sleep(1.0)
    next_snapshot = _capture(client, f"s11_to_s12_{_timestamp()}")
    timing.add(
        step_name="S11_TO_S12",
        page_name="S11",
        action_name="tap_full_report",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=1000,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
    )
    return "S12", next_snapshot


def _extract_claim_count(text: str) -> int | None:
    match = re.search(r"理赔次数\s*(\d+)", text)
    return int(match.group(1)) if match else None


def _extract_max_amount(text: str) -> float | str | None:
    if "无金额记录" in text:
        return "none"
    match = re.search(r"最大金额\s*(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def handle_s12(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    client: AdbClient = context["client"]
    start = time.perf_counter()
    _ensure_page("S12", recognizer, issues, snapshot)
    text = str(snapshot.get("visible_blob") or "")
    read_start = time.perf_counter()
    claim_count = _extract_claim_count(text)
    max_amount = _extract_max_amount(text)
    if claim_count is None or max_amount is None:
        issue = issues.record("FIELD_MISSING", "S12", "Failed to read claim count or max amount.", snapshot, "manual_review")
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context["current_reference"]["accident_count"] = claim_count
    context["current_reference"]["max_accident_amount"] = max_amount
    context["current_reference"]["claim_count"] = claim_count
    context["current_reference"]["max_claim_amount"] = max_amount
    field_ms = int((time.perf_counter() - read_start) * 1000)
    timing.add(
        step_name="S12_COLLECT_CLAIM_FIELDS",
        page_name="S12",
        action_name="collect_claim_count_and_max_amount",
        contract_check_ms=int((read_start - start) * 1000),
        field_read_ms=field_ms,
        action_ms=0,
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            "claim_count": claim_count,
            "max_claim_amount": max_amount,
            "reason_category": "FIELD_READ",
            "reason_detail": "S12 claim fields are collected before locating the body appearance tab",
            "solution": "preserve S12 field collection gate before any tab scroll or click",
        },
    )
    machine.assert_action_allowed("S12", "tap_body_appearance")
    action_start = time.perf_counter()
    clicked_text = "车身外观"
    clicked_strategy = "exact_text_node_bounds"
    visibility_start = time.perf_counter()
    exact_node = _find_body_appearance_tab_node(snapshot)
    bounds = exact_node.get("bounds") if exact_node else None
    visibility_ms = int((time.perf_counter() - visibility_start) * 1000)
    timing.add(
        step_name="S12_BODY_APPEARANCE_VISIBILITY_CHECK",
        page_name="S12",
        action_name="check_body_appearance_tab_visibly_reached",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=0,
        transition_wait_ms=visibility_ms,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            "body_appearance_tab_visible": bool(exact_node),
            "visibility_check": exact_node.get("visibility_check") if exact_node else None,
            "reason_category": "NODE_VISIBILITY_CHECK",
            "reason_detail": "preloaded XML nodes are not clickable until the exact body appearance tab is in the visible top/navigation tab area",
            "solution": "perform controlled pre-click scroll when the exact tab is not visibly reached",
        },
    )
    safe_scroll_ms = 0
    if bounds is None:
        clicked_strategy = "exact_text_node_bounds_after_controlled_scroll"
        snapshot, exact_node, safe_scroll_ms = _find_body_appearance_tab_after_controlled_scroll(
            context,
            snapshot,
            "s12_scroll_body",
        )
        bounds = exact_node.get("bounds") if exact_node else None
    if bounds is None:
        issue = issues.record("S12_BODY_APPEARANCE_TAB_NODE_NOT_FOUND", "S12", "车身外观 tab node not found after finite controlled scroll.", snapshot, "manual_review")
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    clicked_point = _center(bounds)
    context["current_reference"]["s12_body_appearance_click"] = {
        "clicked_text": clicked_text,
        "clicked_node_bounds": list(bounds),
        "clicked_point": list(clicked_point),
        "click_strategy": clicked_strategy,
        "tab_region_confirmed": bool(exact_node.get("tab_region_confirmed")) if exact_node else False,
        "visibility_check": exact_node.get("visibility_check") if exact_node else None,
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "xml_path": str(snapshot.get("xml_path") or ""),
    }
    client.tap(*clicked_point)
    action_ms = int((time.perf_counter() - action_start) * 1000)
    timing.add(
        step_name="S12_CLICK_BODY_APPEARANCE_TAB",
        page_name="S12",
        action_name="exact_text_node_bounds_click",
        contract_check_ms=int((read_start - start) * 1000),
        field_read_ms=field_ms,
        action_ms=action_ms,
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            "clicked_text": clicked_text,
            "clicked_node_bounds": list(bounds),
            "clicked_point": list(clicked_point),
            "click_strategy": clicked_strategy,
            "tab_region_confirmed": bool(exact_node.get("tab_region_confirmed")) if exact_node else False,
            "visibility_check": exact_node.get("visibility_check") if exact_node else None,
            "reason_category": "NODE_SEARCH_SLOW" if action_ms > 1000 else "EXACT_TEXT_NODE_CLICK",
            "reason_detail": "click exact body appearance text node before any scroll fallback",
            "solution": "prefer the contract-allowed direct tab click and fresh-check immediately after it",
        },
    )
    next_snapshot, wait_ms = _wait_for_page(client, recognizer, "S13", "s12_to_s13", timeout_s=8.0)
    timing.add(
        step_name="S12_AFTER_BODY_APPEARANCE_CLICK_FRESH",
        page_name="S12",
        action_name="fresh_after_body_appearance_click",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=0,
        transition_wait_ms=wait_ms,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
        extra={
            "recognized_page": _recognize_mainline_page(recognizer, next_snapshot),
            "reason_category": "WEBVIEW_TEXT_DELAY" if wait_ms > 1000 else "FRESH_CAPTURE",
            "reason_detail": "fresh page recognition after exact body appearance tab click",
            "solution": "continue only from fresh S13/history repair evidence",
        },
    )
    arrival_reason = _history_arrival_reason(recognizer, next_snapshot)
    if arrival_reason:
        _record_history_arrived_timing(context, next_snapshot, page_name="S12", reason=arrival_reason)
    else:
        issue = issues.record("S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED", "S12", "Clicked body appearance tab, but S13/history repair evidence did not appear after fresh; post-click scroll is not allowed.", next_snapshot, "manual_review")
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    timing.add(
        step_name="S12_TO_S13_BODY_APPEARANCE",
        page_name="S12",
        action_name="S12_CLICK_BODY_APPEARANCE_TAB",
        contract_check_ms=int((read_start - start) * 1000),
        field_read_ms=field_ms,
        action_ms=action_ms,
        transition_wait_ms=wait_ms + safe_scroll_ms,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
        extra={
            "clicked_text": clicked_text,
            "clicked_node_bounds": list(bounds),
            "clicked_point": list(clicked_point),
            "click_strategy": clicked_strategy,
            "tab_region_confirmed": bool(exact_node.get("tab_region_confirmed")) if exact_node else False,
            "visibility_check": exact_node.get("visibility_check") if exact_node else None,
            "pre_click_scroll_used": bool(safe_scroll_ms),
            "reason_category": "S12_BODY_APPEARANCE_TAB_CLICK",
            "reason_detail": "exact body-appearance tab text is clicked before any controlled scroll fallback",
            "solution": "use the contract-allowed direct tab action whenever the tab node is present",
        },
    )
    if _recognize_mainline_page(recognizer, next_snapshot) != "S13":
        issue = issues.record("CLICK_TARGET_NOT_ENTERED", "S12", "Tapped 车身外观 but S13 contract did not become stable.", next_snapshot, "manual_review")
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    return "S13", next_snapshot


def _extract_adjacent_history_repair_count(texts: list[str], region_name: str) -> int | None:
    for index, text in enumerate(texts):
        if text.startswith(f"{region_name}深度检测"):
            window = texts[index : index + 6]
            for offset, candidate in enumerate(window):
                if candidate == "历史修复":
                    if index + offset + 1 < len(texts):
                        next_text = texts[index + offset + 1]
                        digits = re.search(r"(\d+)", next_text)
                        if digits:
                            return int(digits.group(1))
    return None


def _has_s13_history_repair_table(snapshot: dict[str, Any]) -> bool:
    texts = [str(item).strip() for item in snapshot.get("visible_texts", [])]
    return "历史修复" in texts and any(region in texts for region in S13_REGION_ORDER)


def _scroll_s13_to_history_repair_table(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[dict[str, Any], int]:
    recognizer: PageRecognizer = context["recognizer"]
    if _has_s13_history_repair_table(snapshot):
        _record_history_arrived_timing(context, snapshot, page_name="S13", reason="history_repair_table_tokens")
        return snapshot, 0
    current, total_wait_ms, arrived = _controlled_scroll_towards_history_repair(
        context,
        snapshot,
        page_name="S13",
        stem_prefix="s13_history_repair_scroll",
        max_attempts=4,
        stop_on_s13_recognizer=False,
    )
    if not arrived and _recognize_mainline_page(recognizer, current) != "S13":
        return current, total_wait_ms
    return current, total_wait_ms


def _tap_s13_region_tab(context: dict[str, Any], snapshot: dict[str, Any], region_name: str) -> tuple[dict[str, Any], int, int]:
    client: AdbClient = context["client"]
    bounds = _find_exact_label_bounds(snapshot, region_name)
    if bounds is None:
        return snapshot, 0, 0
    action_start = time.perf_counter()
    client.tap(*_center(bounds))
    action_ms = int((time.perf_counter() - action_start) * 1000)
    time.sleep(0.6)
    next_snapshot = _capture(client, f"s13_region_{region_name}_{_timestamp()}")
    return next_snapshot, action_ms, 600


def _find_legal_repair_item(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    history_bottom = None
    for node in snapshot.get("nodes", []):
        labels = [str(label).strip() for label in node.get("labels", [])]
        bounds = node.get("bounds")
        if "历史修复" in labels and bounds:
            history_bottom = bounds[3] if history_bottom is None else min(history_bottom, bounds[3])
    candidates: list[tuple[int, dict[str, Any]]] = []
    for node in snapshot.get("nodes", []):
        label = str(node.get("text") or "")
        bounds = node.get("bounds")
        if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        if history_bottom is not None and bounds[1] < history_bottom:
            continue
        if label in S14_ALLOWED_PARTS:
            candidates.append((bounds[1], {"label": label, "bounds": bounds}))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def handle_s13(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    client: AdbClient = context["client"]
    _ensure_page("S13", recognizer, issues, snapshot)
    start = time.perf_counter()
    machine.assert_action_allowed("S13", "collect_repair_counts")
    snapshot, pending_scroll_ms = _scroll_s13_to_history_repair_table(context, snapshot)
    if _recognize_mainline_page(recognizer, snapshot) != "S13":
        issue = issues.record("HISTORY_REPAIR_COUNT_UNCERTAIN", "S13", "S13 did not remain stable while scrolling to history repair table.", snapshot, "manual_review")
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    for region_name in S13_REGION_ORDER:
        snapshot, tab_action_ms, tab_wait_ms = _tap_s13_region_tab(context, snapshot, region_name)
        snapshot, table_scroll_ms = _scroll_s13_to_history_repair_table(context, snapshot)
        read_start = time.perf_counter()
        count = _extract_adjacent_history_repair_count(snapshot.get("visible_texts", []), region_name)
        field_ms = int((time.perf_counter() - read_start) * 1000)
        timing.add(
            step_name="S13_HISTORY_REPAIR_COUNT_CONFIRM",
            page_name="S13",
            action_name="collect_repair_counts",
            contract_check_ms=int((read_start - start) * 1000),
            field_read_ms=field_ms,
            action_ms=tab_action_ms,
            transition_wait_ms=pending_scroll_ms + tab_wait_ms + table_scroll_ms,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
        )
        pending_scroll_ms = 0
        if count is None:
            issue = issues.record("HISTORY_REPAIR_COUNT_UNCERTAIN", "S13", f"Failed to confirm history repair count for {region_name}.", {**snapshot, "region_name": region_name}, "manual_review")
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        context["current_reference"].setdefault("repair_counts", {})[region_name] = count
        if count == 0:
            continue
        machine.assert_action_allowed("S13", "tap_repair_item_if_nonzero")
        item = _find_legal_repair_item(snapshot)
        if item is None:
            issue = issues.record("HISTORY_REPAIR_CELL_NOT_FOUND", "S13", f"No legal repair item found for {region_name}.", {**snapshot, "region_name": region_name}, "manual_review")
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        action_start = time.perf_counter()
        client.tap(*_center(item["bounds"]))
        action_ms = int((time.perf_counter() - action_start) * 1000)
        time.sleep(1.0)
        next_snapshot = _capture(client, f"s13_to_s14_{region_name}_{_timestamp()}")
        timing.add(
            step_name="S13_TO_S14",
            page_name="S13",
            action_name="tap_repair_item_if_nonzero",
            contract_check_ms=int((read_start - start) * 1000),
            field_read_ms=field_ms,
            action_ms=action_ms,
            transition_wait_ms=1000,
            screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
            xml_path=str(next_snapshot.get("xml_path") or ""),
        )
        return "S14", next_snapshot
    machine.assert_action_allowed("S13", "return_to_s10_if_all_zero")
    return "S15", snapshot


S14_TAB_LABEL_RE = re.compile(r"(.+?)[(（](\d+)\s*/\s*(\d+)[)）]")


def _bounds_visible(bounds: tuple[int, int, int, int] | None) -> bool:
    return bool(bounds and bounds[2] > bounds[0] and bounds[3] > bounds[1] and bounds[2] > 0 and bounds[3] > 0)


def _normalize_s14_part(part: str) -> str | None:
    value = str(part or "").strip()
    compact = re.sub(r"\s+", "", value)
    for normalized, aliases in S14_SPECIAL_PART_ALIASES.items():
        if any(re.sub(r"\s+", "", alias) in compact for alias in aliases):
            return normalized
    for normalized, aliases in S14_COVER_PART_ALIASES.items():
        if compact in {re.sub(r"\s+", "", alias) for alias in aliases}:
            return normalized
    if value in S14_ALLOWED_PARTS:
        return value
    for suffix in ["漆面", "漆面损伤"]:
        if value.endswith(suffix):
            base = value[: -len(suffix)].strip()
            normalized = _normalize_s14_part(base)
            if normalized:
                return normalized
    return None


def _s14_page_label_part(page_label: str) -> str | None:
    label = str(page_label or "").strip()
    match = S14_TAB_LABEL_RE.fullmatch(label)
    if match:
        label = match.group(1).strip()
    for suffix in ["漆面", "漆面损伤"]:
        if label.endswith(suffix):
            label = label[: -len(suffix)].strip()
            break
    return _normalize_s14_part(label)


def _s14_part_category(part: str | None) -> str | None:
    if part in S14_SPECIAL_STRUCTURE_RISK_PARTS:
        return "special_structure_risk"
    if part:
        return "cover_panel"
    return None


def _s14_make_key(page_label: str, raw_first_line: str, normalized_part: str | None, normalized_damage: str | None) -> str:
    return "|".join(
        [
            str(page_label or "").strip(),
            str(raw_first_line or "").strip(),
            str(normalized_part or "").strip(),
            str(normalized_damage or "").strip(),
        ]
    )


def _s14_semantic_state(snapshot: dict[str, Any], tab: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = tab or _s14_selected_tab(snapshot) or {}
    page_label = str(selected.get("label") or "")
    raw_first_line = str(_s14_main_damage_line(snapshot).get("raw_first_line") or "").strip()
    parsed = _parse_s14_damage_line(raw_first_line)
    normalized_part = parsed[0] if parsed else None
    raw_damage = parsed[1] if parsed else None
    normalized_damage = parsed[2] if parsed else None
    return {
        "page_label": page_label,
        "raw_first_line": raw_first_line,
        "normalized_part": normalized_part,
        "raw_damage": raw_damage,
        "normalized_damage": normalized_damage,
        "s14_key": _s14_make_key(page_label, raw_first_line, normalized_part, normalized_damage),
    }


def _s14_semantic_changed(before: dict[str, Any], after: dict[str, Any], visited_s14_keys: list[str] | None = None) -> bool:
    visited = visited_s14_keys or []
    if str(before.get("page_label") or "") != str(after.get("page_label") or ""):
        return True
    if str(before.get("raw_first_line") or "") != str(after.get("raw_first_line") or ""):
        return True
    if str(before.get("normalized_part") or "") != str(after.get("normalized_part") or ""):
        return True
    if str(before.get("normalized_damage") or "") != str(after.get("normalized_damage") or ""):
        return True
    after_key = str(after.get("s14_key") or "")
    return bool(after_key and after_key not in visited)


def _split_s14_damage_line(text: str) -> tuple[str, str] | None:
    for sep in ["—", "-", "－", "–", "鈥?", "鈥斅?"]:
        if sep in text:
            raw_part, raw_damage = text.split(sep, 1)
            return raw_part.strip(), raw_damage.strip()
    return None


def _parse_s14_damage_line(value: str) -> tuple[str, str, str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.match(r"(.+?)[—-](.+)", text)
    if not match:
        return None
    raw_part = match.group(1).strip()
    part = _normalize_s14_part(raw_part)
    raw_damage = match.group(2).strip()
    normalized = S14_DAMAGE_NORMALIZATION.get(raw_damage)
    if part is None or normalized is None:
        return None
    return part, raw_damage, normalized, text


def _parse_s14_damage_line(value: str) -> tuple[str, str, str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    split = _split_s14_damage_line(text)
    if split is None:
        return None
    raw_part, raw_damage = split
    part = _normalize_s14_part(raw_part)
    normalized = S14_DAMAGE_NORMALIZATION.get(raw_damage)
    if part is None or normalized is None:
        return None
    return part, raw_damage, normalized, text


def _parse_first_line_damage(texts: list[str]) -> tuple[str, str, str] | None:
    for text in texts:
        parsed = _parse_s14_damage_line(text)
        if parsed:
            part, raw_damage, normalized, _raw_line = parsed
            return part, raw_damage, normalized
    return None


def _s14_tab_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    tabs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        for label in node.get("labels", []):
            value = str(label or "").strip()
            match = S14_TAB_LABEL_RE.fullmatch(value)
            if not match or value in seen:
                continue
            bounds = node.get("bounds")
            tabs.append(
                {
                    "label": value,
                    "part_label": match.group(1).strip(),
                    "page_index": int(match.group(2)),
                    "total_pages": int(match.group(3)),
                    "bounds": bounds,
                    "visible": _bounds_visible(bounds),
                    "selected": bool(node.get("selected")),
                    "clickable": bool(node.get("clickable")),
                    "enabled": node.get("enabled", True),
                    "resource_id": node.get("resource_id"),
                }
            )
            seen.add(value)
    return tabs


def _merge_s14_tabs(existing: dict[str, dict[str, Any]], snapshot: dict[str, Any]) -> None:
    for tab in _s14_tab_items(snapshot):
        current = existing.get(tab["label"], {})
        merged = {**current, **tab}
        if not _bounds_visible(tab.get("bounds")) and _bounds_visible(current.get("bounds")):
            merged["bounds"] = current["bounds"]
            merged["visible"] = True
        existing[tab["label"]] = merged


def _s14_visible_tabs(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [tab for tab in _s14_tab_items(snapshot) if tab.get("visible")]


def _s14_selected_tab(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    tabs = _s14_tab_items(snapshot)
    for tab in tabs:
        if tab.get("selected") and tab.get("visible"):
            return tab
    visible = [tab for tab in tabs if tab.get("visible")]
    return visible[0] if visible else (tabs[0] if tabs else None)


def _s14_popup_bounds(snapshot: dict[str, Any]) -> tuple[int, int, int, int] | None:
    bounds_list = [
        node.get("bounds")
        for node in (snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or "")))
        if _bounds_visible(node.get("bounds")) and node.get("labels")
    ]
    if not bounds_list:
        return None
    return (
        min(bounds[0] for bounds in bounds_list),
        min(bounds[1] for bounds in bounds_list),
        max(bounds[2] for bounds in bounds_list),
        max(bounds[3] for bounds in bounds_list),
    )


def _s14_main_damage_line(snapshot: dict[str, Any]) -> dict[str, Any]:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    image_bounds = _s14_image_bounds(snapshot)
    popup_bounds = _s14_popup_bounds(snapshot)
    candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    for node in nodes:
        bounds = node.get("bounds")
        if not _bounds_visible(bounds):
            continue
        if popup_bounds and (bounds[2] < popup_bounds[0] or bounds[0] > popup_bounds[2]):
            continue
        if image_bounds and bounds[1] < image_bounds[3]:
            continue
        for label in node.get("labels", []):
            value = str(label or "").strip()
            if value.startswith("ignore-error") or re.fullmatch(r"\d{4}[-/.]\d{1,2}", value):
                continue
            if "—" not in value and "-" not in value:
                continue
            priority = 0 if _parse_s14_damage_line(value) else 1
            candidates.append((priority, bounds[1], bounds[0], {"raw_first_line": value, "bounds": bounds}))
    if candidates:
        return sorted(candidates, key=lambda item: (item[0], item[1], item[2]))[0][3]
    for text in snapshot.get("visible_texts", []):
        value = str(text or "").strip()
        if value.startswith("ignore-error") or re.fullmatch(r"\d{4}[-/.]\d{1,2}", value):
            continue
        if "—" in value or "-" in value:
            return {"raw_first_line": value, "bounds": None}
    return {"raw_first_line": "", "bounds": None}


def _s14_image_bounds(snapshot: dict[str, Any]) -> tuple[int, int, int, int] | None:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    image_bounds: list[tuple[int, tuple[int, int, int, int]]] = []
    for node in nodes:
        bounds = node.get("bounds")
        if not _bounds_visible(bounds):
            continue
        labels = [str(label or "") for label in node.get("labels", [])]
        class_name = str(node.get("class_name") or "")
        if "Image" not in class_name and not any(label.startswith("ignore-error") for label in labels):
            continue
        area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
        image_bounds.append((area, bounds))
    if not image_bounds:
        return None
    return sorted(image_bounds, key=lambda item: item[0], reverse=True)[0][1]


def _s14_image_swipe_region(snapshot: dict[str, Any]) -> tuple[tuple[int, int, int, int], str] | None:
    image_bounds = _s14_image_bounds(snapshot)
    if image_bounds:
        return image_bounds, "image_node_bounds"
    popup_bounds = _s14_popup_bounds(snapshot)
    if not popup_bounds:
        return None
    x1, y1, x2, y2 = popup_bounds
    width = max(1, x2 - x1)
    height = max(1, y2 - y1)
    return (
        (
            x1 + int(width * 0.08),
            y1 + int(height * 0.18),
            x2 - int(width * 0.08),
            y1 + int(height * 0.72),
        ),
        "s14_popup_bounds",
    )


def _s14_tab_strip_bounds(snapshot: dict[str, Any]) -> tuple[int, int, int, int] | None:
    visible = [tab for tab in _s14_visible_tabs(snapshot) if _bounds_visible(tab.get("bounds"))]
    if not visible:
        return None
    x1 = min(tab["bounds"][0] for tab in visible)
    y1 = min(tab["bounds"][1] for tab in visible)
    x2 = max(tab["bounds"][2] for tab in visible)
    y2 = max(tab["bounds"][3] for tab in visible)
    return x1, y1, x2, y2


def _s14_collect_current_image(context: dict[str, Any], snapshot: dict[str, Any], tab: dict[str, Any], image_index: int) -> dict[str, Any]:
    start = time.perf_counter()
    page_label = str(tab.get("label") or "")
    page_label_part = _s14_page_label_part(page_label)
    damage_line = _s14_main_damage_line(snapshot)
    raw_first_line = str(damage_line.get("raw_first_line") or "").strip()
    parsed = _parse_s14_damage_line(raw_first_line)
    part = raw_damage = normalized = None
    raw_part = None
    part_category = None
    saved = False
    if parsed:
        part, raw_damage, normalized, _raw_line = parsed
        split = _split_s14_damage_line(raw_first_line)
        raw_part = split[0] if split else part
        part_category = _s14_part_category(part)
    s14_key = _s14_make_key(page_label, raw_first_line, part, normalized)
    visited_s14_keys = context.setdefault("visited_s14_keys", [])
    repeated_s14_key = bool(s14_key and s14_key in visited_s14_keys)
    first_line_bound_to_page_label = (
        part is None
        or not page_label
        or page_label_part == part
    )
    if parsed and repeated_s14_key:
        context.setdefault("s14_repeated_key_events", []).append(
            {
                "s14_key": s14_key,
                "tab_label": page_label,
                "raw_first_line": raw_first_line,
                "screenshot_path": str(snapshot.get("screenshot_path") or ""),
                "xml_path": str(snapshot.get("xml_path") or ""),
            }
        )
        return {
            "tab_label": page_label,
            "image_index": image_index,
            "image_total": int(tab.get("total_pages") or 1),
            "raw_text": raw_first_line,
            "raw_first_line": raw_first_line,
            "raw_part": raw_part,
            "normalized_part": part,
            "part": part,
            "raw_damage": raw_damage,
            "normalized_damage": normalized,
            "part_category": part_category,
            "saved_to_repair_items": False,
            "skipped_reason": "repeated_s14_key",
            "s14_key": s14_key,
            "repeated_s14_key": True,
            "first_line_bound_to_page_label": first_line_bound_to_page_label,
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
        }
    if parsed and s14_key:
        visited_s14_keys.append(s14_key)
    if parsed and not first_line_bound_to_page_label:
        parsed = None
        saved = False
        part_category = None
    if parsed:
        current = context["damage_by_part"].get(part)
        if current is None or S14_DAMAGE_PRIORITY[normalized] > S14_DAMAGE_PRIORITY[current["normalized_damage"]]:
            context["damage_by_part"][part] = {
                "part": part,
                "raw_text": raw_first_line,
                "raw_part": raw_part,
                "normalized_part": part,
                "raw_damage": raw_damage,
                "normalized_damage": normalized,
                "part_category": part_category,
                "tab_label": tab.get("label"),
                "image_index": image_index,
                "image_total": int(tab.get("total_pages") or 1),
                "screenshot_path": str(snapshot.get("screenshot_path") or ""),
                "xml_path": str(snapshot.get("xml_path") or ""),
            }
        saved = True
    record = {
        "tab_label": page_label,
        "image_index": image_index,
        "image_total": int(tab.get("total_pages") or 1),
        "raw_text": raw_first_line,
        "raw_first_line": raw_first_line,
        "raw_part": raw_part,
        "normalized_part": part,
        "part": part,
        "raw_damage": raw_damage,
        "normalized_damage": normalized,
        "part_category": part_category,
        "is_cover_panel": part_category == "cover_panel",
        "is_special_structure_risk": part_category == "special_structure_risk",
        "is_target_damage_type": normalized is not None,
        "saved_to_repair_items": saved,
        "skipped_reason": "" if saved else ("page_label_first_line_mismatch" if not first_line_bound_to_page_label else "non_target_part_or_damage_type"),
        "s14_key": s14_key,
        "repeated_s14_key": False,
        "page_label_part": page_label_part,
        "first_line_bound_to_page_label": first_line_bound_to_page_label,
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "xml_path": str(snapshot.get("xml_path") or ""),
    }
    context.setdefault("s14_image_records", []).append(record)
    context["timing"].add(
        step_name="S14_IMAGE_PROCESS",
        page_name="S14",
        action_name="collect_image_first_line_damage",
        contract_check_ms=0,
        field_read_ms=int((time.perf_counter() - start) * 1000),
        action_ms=0,
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            "image_index": image_index,
            "tab_label": str(tab.get("label") or ""),
            "raw_first_line": raw_first_line,
            "normalized_part": part,
            "normalized_damage": normalized,
            "saved_to_repair_items": saved,
            "screenshot_ms": int((snapshot.get("capture_metrics") or {}).get("screenshot_ms") or 0),
            "xml_dump_ms": int((snapshot.get("capture_metrics") or {}).get("xml_ms") or 0),
            "recognize_ms": 0,
            "reason_category": "S14_IMAGE_PROCESS",
            "reason_detail": "read and normalize only the first line below the current S14 image/item",
            "solution": "keep image-sequence processing and avoid tab clicks or photo zoom",
        },
    )
    return record


def _s14_swipe_image(context: dict[str, Any], snapshot: dict[str, Any], tab: dict[str, Any], image_index: int) -> tuple[dict[str, Any], bool]:
    client: AdbClient = context["client"]
    before_state = _s14_semantic_state(snapshot, tab)
    before_label = str(before_state.get("page_label") or "")
    before_line = str(before_state.get("raw_first_line") or "")
    before_part = before_state.get("normalized_part")
    before_damage = before_state.get("normalized_damage")
    before_key = str(before_state.get("s14_key") or "")
    before_xml_sha = _sha256_text(str(snapshot.get("fresh_xml") or ""))
    before_image_hash = _sha256_file(snapshot.get("screenshot_path"))
    swipe_region = _s14_image_swipe_region(snapshot)
    if not swipe_region:
        no_semantic_change_count = int(context.get("s14_no_semantic_change_count") or 0) + 1
        context["s14_no_semantic_change_count"] = no_semantic_change_count
        record = {
            "swipe_type": "image_swipe",
            "tab_label": before_label,
            "page_index_before": image_index,
            "page_index_after": image_index,
            "image_index_before": image_index,
            "image_index_after": image_index,
            "image_total": int(tab.get("total_pages") or 1),
            "swipe_region_source": None,
            "swipe_region_bounds": None,
            "swipe_x_start": None,
            "swipe_x_end": None,
            "swipe_y": None,
            "swipe_duration_ms": 0,
            "before_screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "after_screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "before_xml_path": str(snapshot.get("xml_path") or ""),
            "after_xml_path": str(snapshot.get("xml_path") or ""),
            "before_page_label": before_label,
            "after_page_label": before_label,
            "before_condition_text": before_line,
            "after_condition_text": before_line,
            "before_first_line": before_line,
            "after_first_line": before_line,
            "before_normalized_part": before_part,
            "after_normalized_part": before_part,
            "before_normalized_damage": before_damage,
            "after_normalized_damage": before_damage,
            "before_s14_key": before_key,
            "after_s14_key": before_key,
            "image_hash_changed": False,
            "semantic_changed": False,
            "repeated_s14_key": True,
            "no_semantic_change_count": no_semantic_change_count,
            "visited_s14_keys_count": len(context.get("visited_s14_keys") or []),
            "before_xml_sha256": before_xml_sha,
            "after_xml_sha256": before_xml_sha,
            "before_image_hash": before_image_hash,
            "after_image_hash": before_image_hash,
            "wait_after_swipe_ms": 0,
            "capture_total_ms": 0,
            "screenshot_ms": 0,
            "xml_dump_ms": 0,
            "recognize_ms": 0,
            "poll_rounds": [],
            "horizontal_swipe_effective": False,
            "image_horizontal_swipe_effective": False,
            "end_of_sequence_candidate": True,
            "image_sequence_end_confirmed": no_semantic_change_count >= 2,
            "adb_rc": None,
            "adb_stderr": "S14_IMAGE_SWIPE_BOUNDS_MISSING",
        }
        context.setdefault("s14_horizontal_swipes", []).append(record)
        context["timing"].add(
            step_name="S14_HORIZONTAL_SWIPE",
            page_name="S14",
            action_name="s14_image_horizontal_swipe_bounds_missing",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra={
                "swipe_index": len(context.get("s14_horizontal_swipes") or []),
                "image_horizontal_swipe_effective": False,
                "end_of_sequence_candidate": True,
                "semantic_changed": False,
                "no_semantic_change_count": no_semantic_change_count,
                "image_sequence_end_confirmed": no_semantic_change_count >= 2,
                "visited_s14_keys_count": len(context.get("visited_s14_keys") or []),
                "reason_category": "S14_IMAGE_HORIZONTAL_SWIPE_NOT_EFFECTIVE",
                "reason_detail": "no image or S14 popup bounds were available from current XML; no fixed screen-coordinate fallback is allowed",
                "solution": "stop via S14 completion gate unless a later fresh XML exposes an image/popup region",
            },
        )
        return snapshot, False
    (x1, y1, x2, y2), swipe_region_source = swipe_region
    region_width = max(1, x2 - x1)
    region_height = max(1, y2 - y1)
    x_start = x1 + int(region_width * 0.88)
    x_end = x1 + int(region_width * 0.12)
    y = y1 + int(region_height * 0.50)
    action_start = time.perf_counter()
    result = client.run(["shell", "input", "swipe", str(x_start), str(y), str(x_end), str(y), "700"], timeout=20)
    action_ms = int((time.perf_counter() - action_start) * 1000)
    after: dict[str, Any] = snapshot
    after_tab: dict[str, Any] = {}
    after_label = ""
    after_line = ""
    after_part = None
    after_damage = None
    after_key = ""
    after_xml_sha = ""
    after_image_hash = ""
    effective = False
    semantic_changed = False
    wait_after_swipe_ms = 0
    capture_total_ms = 0
    poll_rounds: list[dict[str, Any]] = []
    for wait_round in range(2):
        wait_started = time.perf_counter()
        time.sleep(0.35)
        wait_after_swipe_ms += int((time.perf_counter() - wait_started) * 1000)
        capture_started = time.perf_counter()
        after = _capture(client, f"s14_image_swipe_{_timestamp()}")
        capture_total_ms += int((time.perf_counter() - capture_started) * 1000)
        after_tab = _s14_selected_tab(after) or {}
        after_state = _s14_semantic_state(after, after_tab)
        after_label = str(after_state.get("page_label") or "")
        after_line = str(after_state.get("raw_first_line") or "")
        after_part = after_state.get("normalized_part")
        after_damage = after_state.get("normalized_damage")
        after_key = str(after_state.get("s14_key") or "")
        after_xml_sha = _sha256_text(str(after.get("fresh_xml") or ""))
        after_image_hash = _sha256_file(after.get("screenshot_path"))
        semantic_changed = _s14_semantic_changed(
            before_state,
            after_state,
            list(context.get("visited_s14_keys") or []),
        )
        effective = bool(result.success and semantic_changed)
        poll_rounds.append(
            {
                "wait_round_index": wait_round + 1,
                "after_page_label": after_label,
                "after_first_line": after_line,
                "after_normalized_part": after_part,
                "after_normalized_damage": after_damage,
                "after_s14_key": after_key,
                "xml_changed": after_xml_sha != before_xml_sha,
                "image_hash_changed": after_image_hash != before_image_hash,
                "semantic_changed": semantic_changed,
                "effective": effective,
                "screenshot_ms": int((after.get("capture_metrics") or {}).get("screenshot_ms") or 0),
                "xml_dump_ms": int((after.get("capture_metrics") or {}).get("xml_ms") or 0),
            }
        )
        if effective:
            break
    if effective:
        no_semantic_change_count = 0
    else:
        no_semantic_change_count = int(context.get("s14_no_semantic_change_count") or 0) + 1
    context["s14_no_semantic_change_count"] = no_semantic_change_count
    image_hash_changed = after_image_hash != before_image_hash
    xml_changed = after_xml_sha != before_xml_sha
    repeated_s14_key = bool(after_key and after_key in (context.get("visited_s14_keys") or []))
    image_sequence_end_confirmed = no_semantic_change_count >= 2
    record = {
        "swipe_type": "image_swipe",
        "tab_label": before_label,
        "page_index_before": image_index,
        "page_index_after": int(after_tab.get("page_index") or image_index),
        "image_index_before": image_index,
        "image_index_after": int(after_tab.get("page_index") or (image_index + 1 if effective else image_index)),
        "image_total": int(tab.get("total_pages") or 1),
        "swipe_region_source": swipe_region_source,
        "swipe_region_bounds": [x1, y1, x2, y2],
        "swipe_x_start": x_start,
        "swipe_x_end": x_end,
        "swipe_y": y,
        "swipe_duration_ms": 700,
        "before_screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "after_screenshot_path": str(after.get("screenshot_path") or ""),
        "before_xml_path": str(snapshot.get("xml_path") or ""),
        "after_xml_path": str(after.get("xml_path") or ""),
        "before_page_label": before_label,
        "after_page_label": after_label,
        "before_condition_text": before_line,
        "after_condition_text": after_line,
        "before_first_line": before_line,
        "after_first_line": after_line,
        "before_normalized_part": before_part,
        "after_normalized_part": after_part,
        "before_normalized_damage": before_damage,
        "after_normalized_damage": after_damage,
        "before_s14_key": before_key,
        "after_s14_key": after_key,
        "before_xml_sha256": before_xml_sha,
        "after_xml_sha256": after_xml_sha,
        "before_image_hash": before_image_hash,
        "after_image_hash": after_image_hash,
        "xml_changed": xml_changed,
        "image_hash_changed": image_hash_changed,
        "semantic_changed": semantic_changed,
        "repeated_s14_key": repeated_s14_key,
        "no_semantic_change_count": no_semantic_change_count,
        "visited_s14_keys_count": len(context.get("visited_s14_keys") or []),
        "wait_after_swipe_ms": wait_after_swipe_ms,
        "capture_total_ms": capture_total_ms,
        "screenshot_ms": int((after.get("capture_metrics") or {}).get("screenshot_ms") or 0),
        "xml_dump_ms": int((after.get("capture_metrics") or {}).get("xml_ms") or 0),
        "recognize_ms": 0,
        "poll_rounds": poll_rounds,
        "horizontal_swipe_effective": effective,
        "image_horizontal_swipe_effective": effective,
        "end_of_sequence_candidate": not effective,
        "image_sequence_end_confirmed": image_sequence_end_confirmed,
        "adb_rc": result.returncode,
        "adb_stderr": result.stderr,
    }
    context.setdefault("s14_horizontal_swipes", []).append(record)
    context["timing"].add(
        step_name="S14_HORIZONTAL_SWIPE",
        page_name="S14",
        action_name="s14_image_horizontal_swipe",
        contract_check_ms=0,
        field_read_ms=capture_total_ms,
        action_ms=action_ms,
        transition_wait_ms=wait_after_swipe_ms,
        screenshot_path=str(after.get("screenshot_path") or ""),
        xml_path=str(after.get("xml_path") or ""),
        extra={
            "swipe_index": len(context.get("s14_horizontal_swipes") or []),
            "swipe_action_ms": action_ms,
            "wait_after_swipe_ms": wait_after_swipe_ms,
            "screenshot_ms": int((after.get("capture_metrics") or {}).get("screenshot_ms") or 0),
            "xml_dump_ms": int((after.get("capture_metrics") or {}).get("xml_ms") or 0),
            "recognize_ms": 0,
            "before_first_line": before_line,
            "after_first_line": after_line,
            "before_page_label": before_label,
            "after_page_label": after_label,
            "before_normalized_part": before_part,
            "after_normalized_part": after_part,
            "before_normalized_damage": before_damage,
            "after_normalized_damage": after_damage,
            "image_hash_changed": image_hash_changed,
            "semantic_changed": semantic_changed,
            "repeated_s14_key": repeated_s14_key,
            "no_semantic_change_count": no_semantic_change_count,
            "image_sequence_end_confirmed": image_sequence_end_confirmed,
            "visited_s14_keys_count": len(context.get("visited_s14_keys") or []),
            "image_horizontal_swipe_effective": effective,
            "end_of_sequence_candidate": not effective,
            "reason_category": "S14_IMAGE_SWIPE_WAIT_TOO_LONG" if wait_after_swipe_ms > 1000 else "S14_IMAGE_HORIZONTAL_SWIPE",
            "reason_detail": "short-poll after image swipe treats page label, first line, normalized damage, and new S14 key as the only effective evidence; image hash is auxiliary only",
            "solution": "confirm terminal after two consecutive semantic no-change swipes and avoid tab clicks",
        },
    )
    return after, effective


def _s14_fail(context: dict[str, Any], code: str, message: str, snapshot: dict[str, Any]) -> None:
    context["s14_collect_done"] = False
    context["valid"] = False
    context["invalid_reason"] = "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED"
    current_reference = context.setdefault("current_reference", {})
    current_reference["s14_collect_done"] = False
    current_reference["s14_incomplete_reason"] = code
    _store_s14_metrics(context)
    issue = context["issues"].record(
        code,
        "S14",
        message,
        {
            **snapshot,
            "all_s14_tabs": context.get("all_s14_tabs"),
            "s14_tab_records": context.get("s14_tab_records"),
            "s14_image_records": context.get("s14_image_records"),
            "s14_horizontal_swipes": context.get("s14_horizontal_swipes"),
            "last_5_s14_horizontal_swipes": list((context.get("s14_horizontal_swipes") or [])[-5:]),
            "visited_s14_keys": context.get("visited_s14_keys"),
            "s14_repeated_key_events": context.get("s14_repeated_key_events"),
            "s14_no_semantic_change_count": context.get("s14_no_semantic_change_count"),
            "s14_tab_select_events": context.get("s14_tab_select_events"),
        },
        "manual_review",
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def handle_s14(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    timing: TimingRecorder = context["timing"]
    _ensure_page("S14", recognizer, issues, snapshot)
    collect_started = time.perf_counter()
    context["s14_triggered"] = True
    context["s14_collect_done"] = False
    context["s14_image_sequence_model"] = True
    context["s14_sequence_terminal_confirmed"] = False
    context.setdefault("s14_tab_records", [])
    context.setdefault("s14_image_records", [])
    context.setdefault("s14_horizontal_swipes", [])
    context.setdefault("s14_tab_select_events", [])
    known_tabs: dict[str, dict[str, Any]] = {}
    _merge_s14_tabs(known_tabs, snapshot)
    if not known_tabs:
        _s14_fail(context, "S14_PAGE_LABEL_NOT_PARSED", "S14 tab labels were not parsed.", snapshot)
    context["all_s14_tabs"] = list(known_tabs.values())

    context.setdefault("visited_s14_keys", [])
    context.setdefault("s14_repeated_key_events", [])
    context["s14_no_semantic_change_count"] = 0
    no_change_swipes = 0
    image_sequence_index = 0
    last_effective_snapshot = snapshot
    guard = 0
    terminal_confirmed = False
    end_confirm_started: float | None = None
    while guard < 40:
        guard += 1
        if _recognize_mainline_page(recognizer, snapshot) != "S14":
            _s14_fail(context, "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED", "S14 page was lost before all repair details were collected.", snapshot)
        _merge_s14_tabs(known_tabs, snapshot)
        context["all_s14_tabs"] = list(known_tabs.values())
        selected = _s14_selected_tab(snapshot)
        if selected is None:
            _s14_fail(context, "S14_PAGE_LABEL_NOT_PARSED", "S14 selected tab could not be determined.", snapshot)
        semantic_state = _s14_semantic_state(snapshot, selected)
        s14_key = str(semantic_state.get("s14_key") or "")
        if s14_key and s14_key not in context.get("visited_s14_keys", []):
            image_sequence_index += 1
            _s14_collect_current_image(context, snapshot, selected, image_sequence_index)
            last_effective_snapshot = snapshot
        elif s14_key:
            context.setdefault("s14_repeated_key_events", []).append(
                {
                    "s14_key": s14_key,
                    "tab_label": semantic_state.get("page_label"),
                    "raw_first_line": semantic_state.get("raw_first_line"),
                    "screenshot_path": str(snapshot.get("screenshot_path") or ""),
                    "xml_path": str(snapshot.get("xml_path") or ""),
                }
            )
        after, effective = _s14_swipe_image(context, snapshot, selected, image_sequence_index)
        if effective:
            no_change_swipes = 0
            end_confirm_started = None
            snapshot = after
            continue
        if end_confirm_started is None:
            end_confirm_started = time.perf_counter()
        no_change_swipes = int(context.get("s14_no_semantic_change_count") or 0)
        snapshot = after
        if no_change_swipes >= 2:
            terminal_confirmed = True
            break

    if not terminal_confirmed:
        _s14_fail(context, "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED", "S14 image sequence was not fully processed before guard limit.", snapshot)
    if not context.get("s14_image_records"):
        _s14_fail(context, "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED", "S14 did not read the first image condition line.", snapshot)
    context["s14_sequence_terminal_confirmed"] = True
    total_end_confirm_ms = (
        int((time.perf_counter() - end_confirm_started) * 1000)
        if end_confirm_started is not None
        else 0
    )
    context["s14_sequence_terminal_snapshot"] = {
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "xml_path": str(snapshot.get("xml_path") or ""),
        "last_effective_screenshot_path": str(last_effective_snapshot.get("screenshot_path") or ""),
        "last_effective_xml_path": str(last_effective_snapshot.get("xml_path") or ""),
        "terminal_no_change_swipes": no_change_swipes,
        "end_confirm_attempts": no_change_swipes,
        "no_new_content_count": no_change_swipes,
        "last_two_swipes_no_change": no_change_swipes >= 2,
        "image_sequence_end_confirmed": terminal_confirmed,
        "total_end_confirm_ms": total_end_confirm_ms,
        "visited_s14_keys_count": len(context.get("visited_s14_keys") or []),
        "visited_s14_keys": list(context.get("visited_s14_keys") or []),
        "last_5_s14_horizontal_swipes": list((context.get("s14_horizontal_swipes") or [])[-5:]),
    }
    metrics = _store_s14_metrics(context)
    evidence = _s14_completion_evidence(context)
    if (
        metrics["s14_tabs_total"] <= 0
        or metrics["s14_tabs_processed"] != metrics["s14_tabs_total"]
        or metrics["s14_images_processed"] != metrics["s14_images_total"]
        or not all(evidence.values())
    ):
        _s14_fail(context, "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED", "S14 completion metrics did not prove all tabs and images were processed.", snapshot)
    context["s14_collect_done"] = True
    context["current_reference"]["repair_items"] = list(context["damage_by_part"].values())
    context["current_reference"]["s14_collect_done"] = True
    context["current_reference"]["s14_tab_records"] = context.get("s14_tab_records", [])
    context["current_reference"]["s14_image_records"] = context.get("s14_image_records", [])
    context["current_reference"]["s14_horizontal_swipes"] = context.get("s14_horizontal_swipes", [])
    context["current_reference"]["visited_s14_keys"] = list(context.get("visited_s14_keys") or [])
    context["current_reference"]["s14_repeated_key_events"] = context.get("s14_repeated_key_events", [])
    context["current_reference"]["last_5_s14_horizontal_swipes"] = list((context.get("s14_horizontal_swipes") or [])[-5:])
    context["current_reference"]["s14_tab_select_events"] = context.get("s14_tab_select_events", [])
    context["current_reference"]["s14_sequence_terminal_snapshot"] = context.get("s14_sequence_terminal_snapshot")
    timing.add(
        step_name="S14_COLLECT",
        page_name="S14",
        action_name="collect_image_sequence_until_terminal",
        contract_check_ms=0,
        field_read_ms=max(0, int((time.perf_counter() - collect_started) * 1000)),
        action_ms=0,
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            "image_process_count": len(context.get("s14_image_records") or []),
            "image_swipe_count": len(context.get("s14_horizontal_swipes") or []),
            "end_confirm_attempts": no_change_swipes,
            "no_new_content_count": no_change_swipes,
            "last_two_swipes_no_change": no_change_swipes >= 2,
            "image_sequence_end_confirmed": terminal_confirmed,
            "total_end_confirm_ms": total_end_confirm_ms,
            "reason_category": "S14_END_CONFIRM_TOO_CONSERVATIVE" if total_end_confirm_ms > 1000 else "S14_IMAGE_SEQUENCE_CONFIRM",
            "reason_detail": "S14 terminal is confirmed by two consecutive image swipes with no page label, first line, normalized damage, or S14 key change",
            "solution": "keep finite terminal confirmation and stop waiting after the configured no-change evidence is met",
        },
    )
    returned = _fixed_return_to_s10(context)
    context["return_to_s10_reliable"] = True
    context["return_to_s10_source"] = "from_s14_fixed_return_to_s10"
    context["returned_list_source"] = "from_s14_fixed_return_to_s10"
    context["returned_list_source_verified"] = True
    context["post_s14_s10_snapshot"] = returned
    timing.add(
        step_name="POST_S14_TO_S15",
        page_name="S10",
        action_name="dispatch_to_s15_after_reliable_return",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=0,
        transition_wait_ms=0,
        screenshot_path=str(returned.get("screenshot_path") or ""),
        xml_path=str(returned.get("xml_path") or ""),
    )
    return "S15", returned


def _fixed_return_to_s10(context: dict[str, Any]) -> dict[str, Any]:
    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    timing: TimingRecorder = context["timing"]
    total_started = time.perf_counter()
    context.setdefault("s14_return_attempts", [])
    last_snapshot: dict[str, Any] | None = None
    last_state: str | None = None
    allowed_return_states = {"S10", "S11", "S12", "S13", "S14"}
    for attempt_index in range(1, 4):
        action_started = time.perf_counter()
        client.back()
        action_ms = int((time.perf_counter() - action_started) * 1000)
        time.sleep(0.35)
        snapshot = _capture(client, f"s14_return_to_s10_attempt_{attempt_index}_{_timestamp()}")
        state = _recognize_mainline_page(recognizer, snapshot)
        last_snapshot = snapshot
        last_state = state
        attempt = {
            "attempt_index": attempt_index,
            "recognized_page": state,
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
            "focused_window": snapshot.get("focused_window"),
            "foreground_package": snapshot.get("foreground_package"),
        }
        context["s14_return_attempts"].append(attempt)
        timing.add(
            step_name="S14_RETURN_TO_S10_ATTEMPT",
            page_name="S14",
            action_name="single_back_then_fresh_until_s10",
            contract_check_ms=0,
            field_read_ms=int((snapshot.get("capture_metrics") or {}).get("xml_ms") or 0),
            action_ms=action_ms,
            transition_wait_ms=350,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra={
                **attempt,
                "reason_category": "S14_RETURN_TO_S10",
                "reason_detail": "after each back action the runtime fresh-checks the page and stops immediately once S10 is recognized",
                "solution": "do not issue additional back actions after reliable S10 is reached",
            },
        )
        if state == "S10":
            timing.add(
                step_name="S14_RETURN_TO_S10",
                page_name="S14",
                action_name="fixed_return_to_s10_stop_on_first_s10",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=action_ms,
                transition_wait_ms=int((time.perf_counter() - total_started) * 1000) - action_ms,
                screenshot_path=str(snapshot.get("screenshot_path") or ""),
                xml_path=str(snapshot.get("xml_path") or ""),
                extra={
                    "return_attempts": list(context.get("s14_return_attempts") or []),
                    "returned_list_source": "from_s14_fixed_return_to_s10",
                    "returned_list_source_verified": True,
                    "reason_category": "S14_RETURN_PATH_OK_NEEDS_NO_FIX",
                    "reason_detail": "S14 return stopped as soon as a reliable S10 list was recognized",
                    "solution": "dispatch to POST_S14_TO_S15 instead of returning again or re-entering S10 handler",
                },
            )
            return snapshot
        if state and state not in allowed_return_states:
            issue = issues.record(
                "S14_RETURNED_TO_NON_FLOW_PAGE",
                "S14",
                "S14 return landed on a non-flow page before reliable S10.",
                {**snapshot, "s14_return_attempts": context.get("s14_return_attempts")},
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    issue = issues.record(
        "RETURNED_LIST_SOURCE_NOT_VERIFIED",
        "S14",
        "S14 fixed return did not verify reliable S10 within the allowed return attempts.",
        {
            **(last_snapshot or {}),
            "recognized_page": last_state,
            "s14_return_attempts": context.get("s14_return_attempts"),
        },
        "manual_review",
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def _build_target_car(task_result: dict[str, Any]) -> TargetCar:
    params = task_result.get("app_operation_params", {})
    return TargetCar(
        task_id=str(task_result.get("task_id") or ""),
        brand=str(params.get("brand") or ""),
        series=str(params.get("series") or ""),
        model_year=str(params.get("model_year") or ""),
        trim=str(params.get("trim") or ""),
        color=str(params.get("color") or ""),
        registration_date=str(params.get("registration_date_raw") or task_result.get("registration_date_raw") or ""),
        mileage_10k_km=float(params.get("mileage_10k_km") or 0.0),
        transfer_count=int(params.get("transfer_count") or 0),
        condition_text=str(params.get("condition_text") or ""),
        accident_count=params.get("accident_count"),
        max_accident_amount=params.get("max_accident_amount"),
    )


def handle_s15(context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    timing: TimingRecorder = context["timing"]
    issues: IssueRecorder = context["issues"]
    started = time.perf_counter()
    if context.get("s14_triggered"):
        current_reference = context.get("current_reference") or {}
        s14_metrics = _s14_completion_metrics(context)
        if context.get("s14_collect_done") is not True or current_reference.get("s14_collect_done") is not True:
            issue = issues.record(
                "S15_BLOCKED_BY_INCOMPLETE_S14",
                "S15",
                "S15 blocked because S14 repair details were not fully collected.",
                {
                    "current_reference": current_reference,
                    "s14_metrics": s14_metrics,
                    "all_s14_tabs": context.get("all_s14_tabs"),
                    "s14_tab_records": context.get("s14_tab_records"),
                    "s14_image_records": context.get("s14_image_records"),
                    "s14_horizontal_swipes": context.get("s14_horizontal_swipes"),
                },
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        if (
            s14_metrics["s14_tabs_total"] <= 0
            or s14_metrics["s14_tabs_processed"] != s14_metrics["s14_tabs_total"]
            or s14_metrics["s14_images_processed"] != s14_metrics["s14_images_total"]
            or "repair_items" not in current_reference
        ):
            issue = issues.record(
                "S15_BLOCKED_BY_INCOMPLETE_S14",
                "S15",
                "S15 blocked because S14 completion evidence is incomplete.",
                {
                    "current_reference": current_reference,
                    "s14_metrics": s14_metrics,
                    "all_s14_tabs": context.get("all_s14_tabs"),
                    "s14_tab_records": context.get("s14_tab_records"),
                    "s14_image_records": context.get("s14_image_records"),
                },
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        if context.get("returned_list_source_verified") is not True:
            issue = issues.record(
                "RETURNED_LIST_SOURCE_NOT_VERIFIED",
                "S15",
                "S15 blocked because S14 return source was not verified.",
                {"current_reference": current_reference, "returned_list_source": context.get("returned_list_source")},
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    current_reference = context.get("current_reference") or {}
    special_reference = _evaluate_special_structure_reference(list(current_reference.get("repair_items") or []))
    current_reference.update(special_reference)
    if special_reference["reference_disqualified"]:
        context["selection"] = {
            "selected_reference": None,
            "selected_score": None,
            "review_reasons": [special_reference["disqualify_reason"]],
            "reference_disqualified": True,
        }
        context["s15_score_compare_done"] = False
        timing.add(
            step_name="S15_SCORE_COMPARE",
            page_name="S15",
            action_name="disqualify_reference_before_score_compare",
            contract_check_ms=0,
            field_read_ms=int((time.perf_counter() - started) * 1000),
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str((context.get("post_s14_s10_snapshot") or {}).get("screenshot_path") or ""),
            xml_path=str((context.get("post_s14_s10_snapshot") or {}).get("xml_path") or ""),
        )
        return "S10", context.get("post_s14_s10_snapshot") or {}
    fields_config = context["configs"]["fields"]
    target = context["target_car"]
    target.panel_repairs = [
        DamageRecord(item["part"], item["normalized_damage"]) for item in context["damage_by_part"].values()
    ]
    target_score = score_target(target, fields_config, current_year=2026)
    reference = ReferenceCar(
        reference_index=context["current_reference_index"],
        list_price_10k=float(context["current_reference"].get("list_price_10k") or 0.0),
        list_year=int(context["current_reference"].get("list_year") or 0),
        list_mileage_10k_km=float(context["current_reference"].get("list_mileage_10k_km") or 0.0),
        transfer_count=int(context["current_reference"].get("transfer_count") or 0),
        accident_count=int(context["current_reference"].get("accident_count") if context["current_reference"].get("accident_count") is not None else context["current_reference"].get("claim_count") or 0),
        max_accident_amount=context["current_reference"].get("max_accident_amount") if context["current_reference"].get("max_accident_amount") is not None else context["current_reference"].get("max_claim_amount"),
        repair_counts=dict(context["current_reference"].get("repair_counts") or {}),
        panel_repairs=[DamageRecord(item["part"], item["normalized_damage"]) for item in context["damage_by_part"].values()],
    )
    context["current_reference"]["reference_score_input"] = reference.to_dict()
    selection = select_reference(target_score, [reference], fields_config, current_year=2026)
    if special_reference["manual_review_required"]:
        selection.setdefault("review_reasons", []).extend(special_reference["manual_review_reasons"])
        selection["manual_review_required"] = True
    context["target_score"] = target_score.to_dict()
    context["selection"] = selection
    context["s15_score_compare_done"] = True
    evidence_snapshot = context.get("post_s14_s10_snapshot") or {}
    timing.add(
        step_name="S15_SCORE_COMPARE",
        page_name="S15",
        action_name="score_target_and_reference",
        contract_check_ms=0,
        field_read_ms=int((time.perf_counter() - started) * 1000),
        action_ms=0,
        transition_wait_ms=0,
        screenshot_path=str(evidence_snapshot.get("screenshot_path") or ""),
        xml_path=str(evidence_snapshot.get("xml_path") or ""),
    )
    if selection.get("selected_reference") is not None and selection.get("selected_score") is not None:
        selected_score = selection["selected_score"]
        if selected_score.score >= target_score.score:
            return "S16", {}
    return "S10", {}


def handle_s16(context: dict[str, Any]) -> dict[str, Any]:
    issues: IssueRecorder = context["issues"]
    if context.get("s15_score_compare_done") is not True:
        issue = issues.record(
            "S16_BLOCKED_BY_INCOMPLETE_REFERENCE",
            "S16",
            "S16 blocked because S15 score comparison was not completed.",
            {"current_reference": context.get("current_reference")},
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    current_reference = context.get("current_reference") or {}
    s14_metrics = _s14_completion_metrics(context)
    if context.get("s14_triggered") and (
        context.get("s14_collect_done") is not True
        or current_reference.get("s14_collect_done") is not True
        or s14_metrics["s14_tabs_total"] <= 0
        or s14_metrics["s14_tabs_processed"] != s14_metrics["s14_tabs_total"]
        or s14_metrics["s14_images_processed"] != s14_metrics["s14_images_total"]
        or "repair_items" not in current_reference
    ):
        issue = issues.record(
            "S16_BLOCKED_BY_INCOMPLETE_REFERENCE",
            "S16",
            "S16 blocked because S14 repair details were not fully collected.",
            {
                "current_reference": current_reference,
                "s14_metrics": s14_metrics,
                "all_s14_tabs": context.get("all_s14_tabs"),
                "s14_tab_records": context.get("s14_tab_records"),
                "s14_image_records": context.get("s14_image_records"),
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if current_reference.get("reference_disqualified") is True:
        issue = issues.record(
            "S16_BLOCKED_BY_INCOMPLETE_REFERENCE",
            "S16",
            "S16 blocked because disqualified reference cannot be used for pricing.",
            {"current_reference": current_reference},
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    fields_config = context["configs"]["fields"]
    selection = context["selection"]
    pricing = calculate_pricing(selection.get("selected_reference"), fields_config)
    selected_reference = selection.get("selected_reference")
    selected_score = selection.get("selected_score")
    target_score = context.get("target_score") or {}
    manual_review_reasons = []
    if isinstance(target_score, dict):
        manual_review_reasons.extend(target_score.get("review_reasons", []) or [])
    manual_review_reasons.extend(selection.get("review_reasons", []) or [])
    manual_review_reasons.extend(current_reference.get("manual_review_reasons", []) or [])
    manual_review_required = bool(
        current_reference.get("manual_review_required")
        or selection.get("manual_review_required")
        or pricing.get("status") == "manual_review"
    )
    suggested_acquisition = pricing.get("suggested_acquisition_price_yuan")
    suggested_listing = pricing.get("guazi_price_yuan")
    s17_payload = {
        "mode": "simulated_feishu_writeback",
        "task_status": "manual_review" if manual_review_required else "priced",
        "suggested_acquisition_price_yuan": suggested_acquisition,
        "suggested_listing_price_yuan": suggested_listing,
        "price_range_yuan": [suggested_acquisition, suggested_listing],
        "final_reference_index": selected_reference.reference_index if selected_reference else None,
        "reference_price_10k": selected_reference.list_price_10k if selected_reference else None,
        "reference_score": selected_score.score if selected_score else None,
        "target_score": target_score.get("score") if isinstance(target_score, dict) else None,
        "manual_review_reasons": manual_review_reasons,
        "evidence": {
            "s10_screenshot_path": context.get("current_reference", {}).get("s10_screenshot_path"),
            "s10_xml_path": context.get("current_reference", {}).get("s10_xml_path"),
        },
    }
    return {
        "metadata": {
            "project": "guazi_app_data_system",
            "mode": "device_real_mainline",
            "field_scope": "contract_only",
        },
        "status": "FULL_CHAIN_MANUAL_REVIEW_DONE" if manual_review_required else "FULL_CHAIN_PRICED_DONE",
        "s16_status": "S16_READY",
        "target_score": context.get("target_score"),
        "selected_reference": selected_reference.to_dict() if selected_reference else None,
        "selected_reference_score": selected_score.to_dict() if selected_score else None,
        "pricing": pricing,
        "s17_payload": s17_payload,
        "current_reference": context.get("current_reference"),
        "returned_list_source": context.get("returned_list_source"),
        "returned_list_source_verified": context.get("returned_list_source_verified"),
        "s14_records": list(context["damage_by_part"].values()),
        "s14_skip_count": context["s14_skip_count"],
        "s14_collect_done": context.get("s14_collect_done"),
        "all_s14_tabs": context.get("all_s14_tabs"),
        "s14_tab_records": context.get("s14_tab_records"),
        "s14_image_records": context.get("s14_image_records"),
        "s14_horizontal_swipes": context.get("s14_horizontal_swipes"),
        "s14_tab_select_events": context.get("s14_tab_select_events"),
        "s14_sequence_terminal_snapshot": context.get("s14_sequence_terminal_snapshot"),
        "s14_return_attempts": context.get("s14_return_attempts"),
        "all_tabs_detected": context.get("all_tabs_detected"),
        "all_tabs_processed": context.get("all_tabs_processed"),
        "all_images_processed": context.get("all_images_processed"),
        "all_target_repairs_recorded": context.get("all_target_repairs_recorded"),
        "non_target_items_skipped": context.get("non_target_items_skipped"),
    }


def run_s10_to_s16_mainline(runtime: dict[str, Any], phone_test: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_dirs()
    configs = runtime["configs"]
    audit: AuditLogger = runtime["audit"]
    issues: IssueRecorder = runtime["issues"]
    client = AdbClient()
    recognizer = PageRecognizer(configs["pages"])
    machine = PageStateMachine(configs["pages"])
    timing = TimingRecorder()

    task_result = _load_current_target_task()
    first_stage_evidence = _load_first_stage_s10_ready_evidence()
    target_car = _build_target_car(task_result)
    context: dict[str, Any] = {
        "configs": configs,
        "audit": audit,
        "issues": issues,
        "client": client,
        "recognizer": recognizer,
        "machine": machine,
        "timing": timing,
        "target_car": target_car,
        "current_reference_index": 1,
        "current_reference": {},
        "damage_by_part": {},
        "s14_skip_count": 0,
        "s14_triggered": False,
        "s14_collect_done": False,
        "s14_tab_records": [],
        "s14_image_records": [],
        "s14_horizontal_swipes": [],
        "s15_score_compare_done": False,
        "phone_test": phone_test or {},
        "first_stage_evidence": first_stage_evidence,
    }

    if not first_stage_evidence.get("ready"):
        issue = issues.record(
            "PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE",
            "S10",
            "Second stage requires first-stage S10_READY evidence before execution.",
            first_stage_evidence,
            "manual_review",
        )
        timing.write()
        result = {
            "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
            "status": "SECOND_STAGE_BLOCKED_NOT_AT_S10_READY",
            "issue_code": issue["code"],
            "first_stage_evidence": first_stage_evidence,
            "phone_test": phone_test or {},
        }
        _write_second_stage_result(configs, result, task_result)
        return result

    snapshot = _capture(client, f"s10_s16_start_{_timestamp()}")
    snapshot["target_brand"] = target_car.brand
    state = _recognize_mainline_page(recognizer, snapshot)
    if state != "S10":
        issue = issues.record(
            "PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE",
            state or "UNKNOWN",
            "Second stage must start from current S10_READY/S10 page; refusing midpoint or non-contract continuation.",
            snapshot,
            "manual_review",
        )
        timing.write()
        result = {
            "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
            "status": "SECOND_STAGE_BLOCKED_NOT_AT_S10_READY",
            "issue_code": issue["code"],
            "recognized_state": state,
            "first_stage_evidence": first_stage_evidence,
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
            "visible_text_digest": list(snapshot.get("visible_texts", []))[:40],
            "phone_test": phone_test or {},
        }
        _write_second_stage_result(configs, result, task_result)
        return result

    try:
        while True:
            if state == "S10":
                state, snapshot = handle_s10(context, snapshot)
                continue
            if state == "S11":
                state, snapshot = handle_s11(context, snapshot)
                continue
            if state == "S12":
                state, snapshot = handle_s12(context, snapshot)
                continue
            if state == "S13":
                state, snapshot = handle_s13(context, snapshot)
                continue
            if state == "S14":
                state, snapshot = handle_s14(context, snapshot)
                continue
            if state == "S15":
                state, snapshot = handle_s15(context)
                if state == "S10":
                    issue = issues.record("CONTINUE_NEXT_REFERENCE", "S15", "Current reference score is below target score; continue from S10.", context["current_reference"], "continue")
                    result = {
                        "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
                        "status": issue["code"],
                        "current_reference": context.get("current_reference"),
                        "returned_list_source": context.get("returned_list_source"),
                        "returned_list_source_verified": context.get("returned_list_source_verified"),
                        "s14_records": list(context["damage_by_part"].values()),
                        "s14_skip_count": context["s14_skip_count"],
                        "s14_collect_done": context.get("s14_collect_done"),
                        "s14_tab_records": context.get("s14_tab_records"),
                        "s14_image_records": context.get("s14_image_records"),
                        "s14_horizontal_swipes": context.get("s14_horizontal_swipes"),
                        "s14_sequence_terminal_snapshot": context.get("s14_sequence_terminal_snapshot"),
                        "s14_return_attempts": context.get("s14_return_attempts"),
                        "target_score": context.get("target_score"),
                        "selection": context.get("selection"),
                    }
                    timing.write()
                    _write_second_stage_result(configs, result, task_result)
                    return result
                continue
            if state == "S16":
                result = handle_s16(context)
                timing.write()
                _write_second_stage_result(configs, result, task_result)
                return result
            issue = issues.record("PAGE_CONTRACT_MISMATCH", state or "UNKNOWN", "Unhandled mainline state.", {"state": state}, "manual_review")
            timing.write()
            result = {
                "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
                "status": issue["code"],
                "state": state,
            }
            _write_second_stage_result(configs, result, task_result)
            return result
    except GuaziFlowError as exc:
        timing.write()
        invalid_reason = context.get("invalid_reason")
        if context.get("s14_triggered") and context.get("s14_collect_done") is not True:
            invalid_reason = invalid_reason or "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED"
        result = {
            "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
            "status": exc.code,
            "issue_code": exc.code,
            "issue_context": exc.context,
            "valid": False if invalid_reason else None,
            "invalid_reason": invalid_reason,
            "current_reference": context.get("current_reference"),
            "returned_list_source": context.get("returned_list_source"),
            "returned_list_source_verified": context.get("returned_list_source_verified"),
            "s14_records": list(context["damage_by_part"].values()),
            "s14_skip_count": context["s14_skip_count"],
            "s14_collect_done": context.get("s14_collect_done"),
            "all_s14_tabs": context.get("all_s14_tabs"),
            "s14_tab_records": context.get("s14_tab_records"),
            "s14_image_records": context.get("s14_image_records"),
            "s14_horizontal_swipes": context.get("s14_horizontal_swipes"),
            "s14_sequence_terminal_snapshot": context.get("s14_sequence_terminal_snapshot"),
            "s14_return_attempts": context.get("s14_return_attempts"),
            "target_score": context.get("target_score"),
            "selection": context.get("selection"),
        }
        _write_second_stage_result(configs, result, task_result)
        return result


if __name__ == "__main__":
    runtime = {
        "configs": {
            "system": load_config("system.yaml"),
            "pages": load_config("pages.yaml"),
            "fields": load_config("fields.yaml"),
            "actions": load_config("actions.yaml"),
            "exceptions": load_config("exceptions.yaml"),
        }
    }
    system = runtime["configs"]["system"]
    audit = AuditLogger(project_path(system["paths"]["audit_log"]))
    learning = LearningLoop(ROOT, runtime["configs"]["exceptions"], runtime["configs"]["pages"], runtime["configs"]["actions"])
    classifier = IssueClassifier(runtime["configs"]["pages"], runtime["configs"]["actions"])
    issues = IssueRecorder(
        project_path(system["paths"]["issue_log"]),
        runtime["configs"]["exceptions"],
        learning_loop=learning,
        issue_classifier=classifier,
        audit=audit,
    )
    runtime["audit"] = audit
    runtime["issues"] = issues
    print(json.dumps(run_s10_to_s16_mainline(runtime), ensure_ascii=False, indent=2))
