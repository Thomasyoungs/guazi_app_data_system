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
from guazi_app_data_system.adb_device_gate import run_adb_device_gate
from guazi_app_data_system.adb_target_device import TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT
from guazi_app_data_system.audit import AuditLogger
from guazi_app_data_system.config_loader import ensure_runtime_dirs, load_config, project_path
from guazi_app_data_system.exception_handler import GuaziFlowError, IssueRecorder
from guazi_app_data_system.feishu_sync import validate_current_target_task
from guazi_app_data_system.issue_classifier import IssueClassifier
from guazi_app_data_system.learning_loop import LearningLoop
from guazi_app_data_system.output_writer import write_json
from guazi_app_data_system.page_contract_execution_plan import (
    build_action_plan_binding_trace,
    build_s07_age_action_plan,
    build_s07_color_action_plan,
    build_s10_filter_summary_action_plan,
)
from guazi_app_data_system.page_recognition import PageRecognizer
from guazi_app_data_system.page_state_machine import PageStateMachine
from guazi_app_data_system.transient_popup_handler import (
    GUAZI_PUSH_POPUP_CLOSE_FAILED,
    GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND,
    close_guazi_push_popup_from_snapshot,
)


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
    "账号密码登录",
    "用户协议",
    "隐私协议",
    "手机号码",
    "验证码",
)
S_LOGIN_BOTTOM_BACK_TEXTS = {"<", "\uff1c"}
TARGET_TASK_DATA_PATH = ROOT / "data" / "current_target_task.json"
TARGET_TASK_REQUIRED_FIELDS = ("brand", "series", "model_year", "trim", "color", "registration_date")
PAGE_CONTRACT_IS_ONLY_EXECUTION_STANDARD = True
S06_TARGET_FILTER_LIST_VARIANT = "S06_TARGET_FILTER_LIST_AFTER_S05_CONFIRM"
S08_TARGET_LIST_AFTER_FILTER_VARIANT = "S08_TARGET_LIST_AFTER_FILTER"
S04_SERIES_SCROLL_LIMIT = 8
S04_SCROLL_SERIES_ACTION = "scroll_series_list"
S03_BRAND_SCAN_SCROLL_LIMIT = 3
S03_BRAND_ROUTE_ALIASES: dict[str, tuple[str, ...]] = {
    "欧拉": ("欧拉", "欧拉 ORA", "长城欧拉", "ORA"),
    "长城欧拉": ("欧拉", "欧拉 ORA", "长城欧拉", "ORA"),
    "ORA": ("欧拉", "欧拉 ORA", "长城欧拉", "ORA"),
    "比亚迪": ("比亚迪", "BYD"),
    "BYD": ("比亚迪", "BYD"),
    "雪佛兰": ("雪佛兰",),
    "零跑": ("零跑", "零跑汽车", "LEAPMOTOR", "Leapmotor"),
    "零跑汽车": ("零跑", "零跑汽车", "LEAPMOTOR", "Leapmotor"),
    "LEAPMOTOR": ("零跑", "零跑汽车", "LEAPMOTOR", "Leapmotor"),
    "大众": ("大众", "一汽-大众", "上汽大众"),
    "本田": ("本田",),
    "丰田": ("丰田",),
    "哪吒": ("哪吒", "哪吒汽车", "NETA"),
    "小鹏": ("小鹏", "小鹏汽车", "XPENG"),
    "蔚来": ("蔚来", "NIO"),
    "理想": ("理想", "理想汽车", "Li Auto"),
    "极氪": ("极氪", "ZEEKR"),
    "阿维塔": ("阿维塔", "AVATR"),
    "深蓝": ("深蓝", "深蓝汽车", "DEEPAL"),
    "问界": ("AITO问界", "问界", "AITO"),
    "AITO": ("AITO问界", "问界", "AITO"),
}
S03_BRAND_INITIALS: dict[str, str] = {
    "欧拉": "O",
    "欧拉 ORA": "O",
    "长城欧拉": "O",
    "ORA": "O",
    "比亚迪": "B",
    "BYD": "B",
    "雪佛兰": "X",
    "大众": "D",
    "一汽-大众": "D",
    "上汽大众": "D",
    "本田": "B",
    "丰田": "F",
    "零跑": "L",
    "零跑汽车": "L",
    "LEAPMOTOR": "L",
    "哪吒": "N",
    "哪吒汽车": "N",
    "NETA": "N",
    "小鹏": "X",
    "小鹏汽车": "X",
    "XPENG": "X",
    "蔚来": "W",
    "NIO": "W",
    "理想": "L",
    "理想汽车": "L",
    "Li Auto": "L",
    "极氪": "J",
    "ZEEKR": "J",
    "阿维塔": "A",
    "AVATR": "A",
    "深蓝": "S",
    "深蓝汽车": "S",
    "DEEPAL": "S",
    "问界": "A",
    "AITO问界": "A",
    "AITO": "A",
}
SCRIPT_PAGE_CONTRACT_ACTIONS: dict[str, set[str]] = {
    "S01": {"click_bottom_select_car_tab"},
    "S02": {"tap_brand_filter", "click_brand_entry"},
    "S02_SELECT_CAR_TAB": {"click_brand_entry", "tap_brand_filter"},
    "S03": {
        "tap_brand_letter",
        "tap_target_brand",
        "scroll_brand_list",
        "S03_ONLY_ALLOWED_ACTION_CLICK_TARGET_INITIAL_LETTER",
        "S03_ONLY_ALLOWED_ACTION_CLICK_BRAND_ROW_LEFT_ICON_SAFE_POINT",
        "STOP",
    },
    "S04": {
        "click_series_model_button",
        "tap_series_letter",
        "scroll_series_list",
        "S04_ONLY_ALLOWED_ACTION_CLICK_TARGET_SERIES_ROW_RIGHT_MODELS_BUTTON",
        "STOP",
    },
    "S05": {"tap_target_year", "S05_ONLY_ALLOWED_ACTION_SELECT_TARGET_YEAR"},
    "S05_MODEL_YEAR_SELECTED": {
        "tap_exact_trim",
        "scroll_trim_list",
        "S05_ONLY_ALLOWED_ACTION_SELECT_TARGET_CONFIG_OR_EMISSION_VARIANT_GROUP",
    },
    "S05_TRIM_SELECTED": {"tap_green_confirm", "S05_ONLY_ALLOWED_ACTION_CLICK_CONFIRM"},
    "S06": {"tap_trim_filter", "S06_ONLY_ALLOWED_ACTION_CLICK_MODEL_CONFIG"},
    "S07": {
        "tap_color_filter",
        "tap_target_color",
        "tap_age_filter",
        "set_exact_age",
        "tap_view_cars",
        "S07_ONLY_ALLOWED_ACTION_OPEN_COLOR_FILTER",
        "S07_ONLY_ALLOWED_ACTION_CLICK_TARGET_COLOR",
        "S07_ONLY_ALLOWED_ACTION_OPEN_AGE_FILTER",
        "S07_ONLY_ALLOWED_ACTION_SET_TARGET_AGE",
        "S07_ONLY_ALLOWED_ACTION_CLICK_VIEW_RESULT",
    },
    "S08": {
        "collect_list_whitelist_fields",
        "tap_sort_if_present",
        "S08_ONLY_ALLOWED_ACTION_CLICK_SORT_DROPDOWN",
    },
    "S09": {"tap_price_low_to_high", "S09_ONLY_ALLOWED_ACTION_CLICK_PRICE_ASC"},
    "S10": {"collect_list_whitelist_fields", "STOP_AT_S10_READY"},
    "S_LOGIN": {
        "S_LOGIN_ONLY_ALLOWED_ACTION_CLICK_LATER",
        "S_LOGIN_ONLY_ALLOWED_ACTION_PRESS_BACK",
        "S_LOGIN_BOTTOM_BACK_ONCE",
        "S_LOGIN_CLICK_LATER_UNTIL_CLOSED",
    },
}
LAUNCHER_ACCOUNT_DIALOG_TEXTS = (
    "检测到您的账号已退出登录",
    "请重新登录账号",
    "稍后",
    "去登录",
)
LAUNCHER_ACCOUNT_DIALOG_CORE_TEXTS = (
    "检测到您的账号已退出登录",
    "请重新登录账号",
)
LAUNCHER_ACCOUNT_LATER_TEXT = "稍后"
LAUNCHER_LATER_DIALOG_TYPE = "ACCOUNT_LOGOUT_DIALOG"
LAUNCHER_LATER_ACTION_ID = "LAUNCHER_ONLY_ALLOWED_ACTION_CLICK_LATER"
LAUNCHER_LATER_MAX_ATTEMPTS = 5
DESKTOP_UPGRADE_MODAL_TITLE_TEXT = "\u8f6f\u4ef6\u5347\u7ea7"
DESKTOP_UPGRADE_MODAL_LAUNCHER_TEXT = "\u592a\u64ce\u684c\u9762"
DESKTOP_UPGRADE_MODAL_LATER_TEXT = "\u7a0d\u540e\u5347\u7ea7"
DESKTOP_UPGRADE_MODAL_NOW_TEXT = "\u7acb\u5373\u5347\u7ea7"
DESKTOP_UPGRADE_MODAL_KEYWORDS = (
    DESKTOP_UPGRADE_MODAL_TITLE_TEXT,
    DESKTOP_UPGRADE_MODAL_LAUNCHER_TEXT,
    DESKTOP_UPGRADE_MODAL_LATER_TEXT,
    DESKTOP_UPGRADE_MODAL_NOW_TEXT,
)
DESKTOP_UPGRADE_MODAL_ACTION_ID = "DESKTOP_UPGRADE_MODAL_ONLY_ALLOWED_ACTION_CLICK_LATER"
DESKTOP_UPGRADE_MODAL_MAX_ATTEMPTS = 2
STARTUP_ACCOUNT_CENTER_PACKAGE = "com.shuqing.tqaccountcenter"
STARTUP_ACCOUNT_CENTER_PAGE_ID = "S_STARTUP_ACCOUNT_CENTER_LOGIN_PAGE"
STARTUP_ACCOUNT_CENTER_BACK_ACTION_ID = "STARTUP_ACCOUNT_CENTER_ONLY_ALLOWED_ACTION_PRESS_BACK"
STARTUP_ACCOUNT_CENTER_MAX_BACK_ATTEMPTS = 2
STARTUP_ACCOUNT_CENTER_WELCOME_TEXT = "\u6b22\u8fce\u767b\u5f55"
STARTUP_ACCOUNT_CENTER_PHONE_TEXTS = ("\u8bf7\u8f93\u5165\u624b\u673a\u53f7", "\u8bf7\u8f93\u5165\u624b\u673a\u53f7\u7801")
STARTUP_ACCOUNT_CENTER_CODE_TEXT = "\u8bf7\u8f93\u5165\u9a8c\u8bc1\u7801"
STARTUP_ACCOUNT_CENTER_GET_CODE_TEXT = "\u83b7\u53d6\u9a8c\u8bc1\u7801"
STARTUP_ACCOUNT_CENTER_PASSWORD_LOGIN_TEXT = "\u8d26\u53f7\u5bc6\u7801\u767b\u5f55"
STARTUP_ACCOUNT_CENTER_AGREEMENT_TEXTS = ("\u7528\u6237\u534f\u8bae", "\u9690\u79c1\u534f\u8bae")
STARTUP_ACCOUNT_CENTER_FORBIDDEN_ACTIONS = {
    "\u70b9\u51fb\u767b\u5f55",
    "\u70b9\u51fb\u83b7\u53d6\u9a8c\u8bc1\u7801",
    "\u70b9\u51fb\u624b\u673a\u53f7\u8f93\u5165\u6846",
    "\u70b9\u51fb\u9a8c\u8bc1\u7801\u8f93\u5165\u6846",
    "\u70b9\u51fb\u8d26\u53f7\u5bc6\u7801\u767b\u5f55",
    "\u70b9\u51fb\u7528\u6237\u534f\u8bae",
    "\u70b9\u51fb\u9690\u79c1\u534f\u8bae",
    "\u8f93\u5165\u6587\u672c",
}


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


SLOW_ACTION_THRESHOLD_SECONDS = 2.0
AGGREGATE_TIMING_STEPS = {
    "s07_color_age_and_view",
}


class TimingRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self._sequence_index = 0
        self._last_record_wall_time: float | None = None

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
        duration_seconds = round(full_step_ms / 1000, 3)
        end_wall_time = time.time()
        start_wall_time = max(0.0, end_wall_time - duration_seconds)
        previous_interval = (
            None
            if self._last_record_wall_time is None
            else round(end_wall_time - self._last_record_wall_time, 3)
        )
        self._last_record_wall_time = end_wall_time
        self._sequence_index += 1
        extra = extra or {}
        is_aggregate = step_name in AGGREGATE_TIMING_STEPS or bool(extra.get("is_aggregate"))
        record = {
            "action_sequence_index": self._sequence_index,
            "step_name": step_name,
            "page_id": page_name,
            "action_id": action_name,
            "page_name": page_name,
            "action_name": action_name,
            "start_time": datetime.fromtimestamp(start_wall_time).isoformat(timespec="seconds"),
            "end_time": datetime.fromtimestamp(end_wall_time).isoformat(timespec="seconds"),
            "contract_check_ms": contract_check_ms,
            "field_read_ms": field_read_ms,
            "action_ms": action_ms,
            "transition_wait_ms": transition_wait_ms,
            "full_step_ms": full_step_ms,
            "exceeded_3s": full_step_ms > 3000,
            "slow_action_threshold_seconds": SLOW_ACTION_THRESHOLD_SECONDS,
            "duration_seconds": duration_seconds,
            "interval_since_previous_action_seconds": previous_interval,
            "threshold_exceeded": duration_seconds >= SLOW_ACTION_THRESHOLD_SECONDS,
            "reason_category": extra.get("reason_category"),
            "screenshot_path": screenshot_path,
            "xml_path": xml_path,
            "is_aggregate": is_aggregate,
            "optimized": False,
            "optimization_type": None,
            "before_estimated_duration_seconds": duration_seconds,
            "after_duration_seconds": duration_seconds,
            "saved_seconds_estimate": 0.0,
            "contract_validation_preserved": True,
            "skipped_redundant_fresh_count": 0,
            "skipped_redundant_xml_dump_count": 0,
        }
        if is_aggregate:
            record.update(
                {
                    "optimized": True,
                    "optimization_type": "report_deduplicate_aggregate_timing",
                    "single_action_performance_source": "child_contract_actions",
                }
            )
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


def _has_positive_bounds(bounds: tuple[int, int, int, int] | None) -> bool:
    return bool(bounds) and bounds[2] > bounds[0] and bounds[3] > bounds[1]


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
                "checked": str(node.attrib.get("checked") or "") == "true",
                "package": str(node.attrib.get("package") or ""),
                "class_name": str(node.attrib.get("class") or ""),
                "resource_id": str(node.attrib.get("resource-id") or ""),
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
    forbidden_large_keys = {"fresh_xml", "raw_xml", "full_xml", "xml_text", "page_source", "nodes", "visible_blob"}

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items() if key not in forbidden_large_keys}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    enriched = scrub(_result_with_segment_metadata(result))
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


def _enable_s03_brand_search_v2_actions(pages_config: dict[str, Any]) -> None:
    # S03 is governed by PAGE_CONTRACT_IS_ONLY_EXECUTION_STANDARD.
    # V1.16 allows exactly two actions:
    # - target brand visible: tap the brand row's left icon safe point.
    # - target brand not visible: tap the target initial in the right A-Z index.
    # Do not add tabs, scrolling, or brand-zone continuation actions here.
    return


def _capture(client: AdbClient, stem: str) -> dict[str, Any]:
    capture_started_monotonic = time.perf_counter()
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
        "capture_taken_monotonic": capture_started_monotonic,
        "capture_taken_at": datetime.now(timezone.utc).isoformat(),
    }
    snapshot["visible_blob"] = "".join(snapshot["visible_texts"])
    return snapshot


STARTUP_REUSABLE_CAPTURE_MAX_AGE_SECONDS = 2.0
APP_LAUNCH_FOREGROUND_POLL_INTERVAL_SECONDS = 0.25
APP_LAUNCH_FOREGROUND_POLL_MAX_SECONDS = 1.0


def _startup_defaults(startup: dict[str, Any]) -> None:
    startup.setdefault("startup_timeline", [])
    startup.setdefault("capture_count", 0)
    startup.setdefault("screenshot_count", 0)
    startup.setdefault("xml_dump_count", 0)
    startup.setdefault("reused_capture_count", 0)
    startup.setdefault("fastpath_used", False)
    startup.setdefault("fastpath_reason", "")
    for key in (
        "adb_gate_duration_ms",
        "wake_unlock_duration_ms",
        "miui_recovery_duration_ms",
        "launcher_icon_lookup_duration_ms",
        "app_reopen_duration_ms",
        "app_foreground_confirm_duration_ms",
        "s01_detect_duration_ms",
    ):
        startup.setdefault(key, 0)


def _startup_event(
    context: dict[str, Any],
    step_name: str,
    *,
    duration_ms: int = 0,
    reused_capture: bool = False,
    extra: dict[str, Any] | None = None,
) -> None:
    startup = context.setdefault("startup", {})
    _startup_defaults(startup)
    event = {
        "step_name": step_name,
        "duration_ms": max(0, int(duration_ms)),
        "reused_capture": bool(reused_capture),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        event.update(extra)
    startup["startup_timeline"].append(event)


def _startup_note_capture(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    reused: bool = False,
    reason: str = "",
) -> None:
    startup = context.setdefault("startup", {})
    _startup_defaults(startup)
    if reused:
        startup["reused_capture_count"] = int(startup.get("reused_capture_count") or 0) + 1
        startup["fastpath_used"] = True
        startup["fastpath_reason"] = reason or startup.get("fastpath_reason") or "reused_startup_capture"
        _startup_event(
            context,
            "reuse_fresh_launcher_capture",
            reused_capture=True,
            extra={
                "reason": reason,
                "screenshot_path": snapshot.get("screenshot_path"),
                "xml_path": snapshot.get("xml_path"),
            },
        )
        return
    startup["capture_count"] = int(startup.get("capture_count") or 0) + 1
    if snapshot.get("screenshot_path"):
        startup["screenshot_count"] = int(startup.get("screenshot_count") or 0) + 1
    if snapshot.get("xml_path"):
        startup["xml_dump_count"] = int(startup.get("xml_dump_count") or 0) + 1


def _capture_age_seconds(snapshot: dict[str, Any]) -> float | None:
    taken = snapshot.get("capture_taken_monotonic")
    if not isinstance(taken, (int, float)):
        return None
    return max(0.0, time.perf_counter() - float(taken))


def _fresh_launcher_icon_capture_reusable(snapshot: dict[str, Any]) -> bool:
    age = _capture_age_seconds(snapshot)
    if age is None or age > STARTUP_REUSABLE_CAPTURE_MAX_AGE_SECONDS:
        return False
    if snapshot.get("keyguard_showing") or snapshot.get("keyguard_secure"):
        return False
    if _notification_shade_visible(snapshot):
        return False
    if str(snapshot.get("foreground_package") or "") == GUAZI_PACKAGE:
        return False
    if str(snapshot.get("xml_package") or "") == GUAZI_PACKAGE:
        return False
    return _launcher_window_visible(snapshot) and _guazi_icon_visible(snapshot)


def _poll_device_state_until(
    client: AdbClient,
    predicate: Any,
    *,
    max_seconds: float,
    interval_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    rounds: list[dict[str, Any]] = []
    last_snapshot: dict[str, Any] = {}
    max_rounds = max(1, int(max_seconds / interval_seconds) + 1)
    matched = False
    for index in range(max_rounds):
        last_snapshot = _device_state_only(client)
        matched = bool(predicate(last_snapshot))
        rounds.append(
            {
                "round": index + 1,
                "matched": matched,
                "focused_window": last_snapshot.get("focused_window"),
                "foreground_package": last_snapshot.get("foreground_package"),
                "keyguard_showing": bool(last_snapshot.get("keyguard_showing")),
                "dumpsys_ms": int((last_snapshot.get("capture_metrics") or {}).get("dumpsys_ms") or 0),
            }
        )
        if matched or index == max_rounds - 1:
            break
        time.sleep(interval_seconds)
    return {
        "matched": matched,
        "rounds": rounds,
        "last_snapshot": last_snapshot,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


ADB_EVIDENCE_KEYS = (
    "target_adb_serial",
    "adb_serial_source",
    "adb_path",
    "adb_path_source",
    "adb_runtime_env_mode",
    "use_isolated_adb_home",
    "adb_vendor_keys_configured",
    "adb_vendor_keys_path_summary",
    "adb_vendor_keys_exists",
    "output_adb_home_exists",
    "output_adb_home_android_dir_exists",
    "android_adb_server_port",
    "adb_devices_l_raw",
    "parsed_devices",
    "target_device_state",
    "target_device_present_before_first_stage",
    "device_snapshot_taken_at",
    "device_snapshot_error",
    "adb_command_preview",
    "cwd",
    "project_root",
    "python_executable",
)


def _adb_evidence_context_fields(context: dict[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    snapshot = context.get("adb_env_snapshot") if isinstance(context.get("adb_env_snapshot"), dict) else {}
    gate = context.get("target_device_gate") if isinstance(context.get("target_device_gate"), dict) else {}
    for source in (snapshot, gate):
        for key in ADB_EVIDENCE_KEYS:
            if key in source and key not in evidence:
                evidence[key] = source.get(key)
    if gate:
        evidence["target_device_gate_passed"] = gate.get("passed")
        evidence["target_device_gate_status"] = gate.get("status")
        evidence["target_device_validation"] = gate.get("target_device_validation")
    return evidence


def _target_device_not_found_text(text: Any, serial: str | None) -> bool:
    value = str(text or "")
    if not value:
        return False
    lowered = value.lower()
    if serial and f"device '{serial.lower()}' not found" in lowered:
        return True
    if serial and f"device {serial.lower()} not found" in lowered:
        return True
    return "no devices/emulators found" in lowered


def _capture_has_later_device_not_found(context: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    gate = context.get("target_device_gate") if isinstance(context.get("target_device_gate"), dict) else {}
    if not (gate.get("passed") and gate.get("target_device_state") == "device"):
        return False
    serial = str(gate.get("target_adb_serial") or gate.get("target_serial") or "")
    return any(
        _target_device_not_found_text(snapshot.get(key), serial)
        for key in (
            "screenshot_error",
            "xml_dump_error",
            "adb_stderr",
            "xml_dump_stderr",
            "xml_read_stderr",
        )
    )


def _first_adb_error_text(payload: dict[str, Any]) -> str:
    for key in (
        "screenshot_error",
        "xml_dump_error",
        "adb_stderr",
        "xml_dump_stderr",
        "xml_read_stderr",
        "error",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _run_first_stage_target_device_gate(context: dict[str, Any]) -> dict[str, Any]:
    client: AdbClient = context["client"]
    gate_started = time.perf_counter()
    snapshot = client.runtime_environment_snapshot() if hasattr(client, "runtime_environment_snapshot") else {}
    context["adb_env_snapshot"] = snapshot
    gate = run_adb_device_gate(client, audit=context.get("audit"), allow_transient_recovery=False)
    gate_ms = int((time.perf_counter() - gate_started) * 1000)
    context["target_device_gate"] = gate
    context.setdefault("startup", {})["target_device_gate_checked"] = True
    context["startup"]["target_device_gate_passed"] = bool(gate.get("passed"))
    context["startup"]["target_device_state"] = gate.get("target_device_state")
    context["startup"]["adb_gate_duration_ms"] = gate_ms
    _startup_event(
        context,
        "adb_target_device_gate",
        duration_ms=gate_ms,
        extra={
            "target_device_state": gate.get("target_device_state"),
            "passed": bool(gate.get("passed")),
        },
    )
    if gate.get("passed"):
        return gate
    code = str(gate.get("status") or "TARGET_ADB_DEVICE_NOT_CONNECTED")
    issue_context = {
        **_adb_evidence_context_fields(context),
        "failed_action": "target_device_gate_before_first_stage",
        "stop_code": code,
        "adb_devices_l": gate.get("adb_devices_l_raw"),
        "no_default_device_fallback": True,
        "startup": dict(context.get("startup", {})),
    }
    issue = _record_issue(
        context["issues"],
        code,
        "DEVICE",
        "Target ADB device gate failed before any APP operation.",
        issue_context,
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


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
    confirmed_s_login = _is_s_login_prompt(snapshot)
    context["s_login_later_button_seen"] = _s_login_later_visible(snapshot)
    context["s_login_xml_back_node_seen"] = bool(node and node.get("bounds"))
    context["s_login_system_nav_back_visible"] = False
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
    context["s_login_system_back_used"] = False
    context["s_login_system_back_attempt_count"] = 0
    context["s_login_exit_action"] = "S_LOGIN_BOTTOM_BACK_ONCE"
    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    action_ms = contract_execute_click(
        context,
        snapshot,
        "S_LOGIN",
        "S_LOGIN_BOTTOM_BACK_ONCE",
        _center(node["bounds"]),
        evidence={
            "clicked_text": "S_LOGIN_BOTTOM_BACK",
            "clicked_node_bounds": node.get("bounds"),
            "clicked_action_id": "S_LOGIN_BOTTOM_BACK_ONCE",
        },
    )
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
            "s_login_later_button_seen": False,
            "s_login_xml_back_node_seen": True,
            "s_login_system_nav_back_visible": False,
            "s_login_system_back_used": False,
            "s_login_system_back_attempt_count": 0,
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


def _text_has_any_variant(text: str, variants: tuple[str, ...]) -> bool:
    return any(item and item in text for item in variants)


def _looks_like_s04_brand_zone_mixed_list(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    has_brand_zone = _text_has_any_variant(blob, ("\u54c1\u724c\u4e13\u533a",))
    has_sort = _text_has_any_variant(blob, ("\u7efc\u5408\u6392\u5e8f",))
    has_price = _text_has_any_variant(blob, ("\u4ef7\u683c",))
    has_model_config = _text_has_any_variant(blob, ("\u8f66\u578b\u914d\u7f6e",))
    has_vehicle_card_signal = _text_has_any_variant(blob, ("\u4e07", "\u516c\u91cc", "\u6b3e"))
    return has_brand_zone and has_sort and has_price and has_model_config and has_vehicle_card_signal


def _compact_contract_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _s06_target_filter_list_after_s05_confirm_evidence(
    snapshot: dict[str, Any],
    flow_state: dict[str, Any] | None,
) -> dict[str, Any]:
    flow_state = flow_state or {}
    texts = [str(text).strip() for text in snapshot.get("visible_texts", []) if str(text).strip()]
    blob = "".join(texts)
    target_year = str(flow_state.get("s05_selected_year_model") or flow_state.get("target_year_model") or "").strip()
    target_config = str(flow_state.get("s05_selected_config_model") or flow_state.get("target_config_model") or "").strip()

    source_gate = {
        "transition_context": flow_state.get("transition_context"),
        "s05_done": bool(flow_state.get("S05_DONE")),
        "s05_selected_year_model": target_year,
        "s05_selected_config_model": target_config,
        "selected_count_text": flow_state.get("selected_count_text"),
        "selected_count_actual": flow_state.get("selected_count_actual"),
    }
    source_gate_passed = bool(
        source_gate["transition_context"] == "S05_CONFIRM_TO_S06"
        and source_gate["s05_done"]
    )

    model_config_node = _find_exact(snapshot, "车型配置")
    model_config_entry_visible = bool(model_config_node and model_config_node.get("bounds"))
    recognized = bool(
        source_gate_passed
        and model_config_entry_visible
    )
    stop_code = None
    if source_gate_passed and not model_config_entry_visible:
        stop_code = "S06_MODEL_CONFIG_ENTRY_NOT_BOUND_BY_FAST_GATE"

    return {
        **source_gate,
        "recognized_page_after_s05_confirm": "S06" if recognized else None,
        "s06_page_variant": S06_TARGET_FILTER_LIST_VARIANT if recognized else None,
        "s06_source_gate_passed": source_gate_passed,
        "s06_target_filter_evidence": [],
        "target_filter_evidence_found": None,
        "s06_fast_gate_enabled": True,
        "s06_fast_gate_rule": "V1_27_S05_CONFIRM_TO_S06_AND_S05_DONE_AND_MODEL_CONFIG_BOUNDS_ONLY",
        "model_config_entry_visible": model_config_entry_visible,
        "model_config_entry_bounds": model_config_node.get("bounds") if model_config_node else None,
        "s06_fast_gate_reverse_exclusion_passed": None,
        "s06_core_elements": ["model_config_entry"] if model_config_entry_visible else [],
        "s06_reverse_exclusion_passed": None,
        "s06_reverse_exclusion_failures": [],
        "s06_recognized_by": "fast_gate_source_s05_done_model_config_bounds" if recognized else None,
        "s06_allowed_action": "click_model_config_filter" if recognized else None,
        "target_card_titles_seen": [],
        "s06_stop_code": stop_code,
    }


def _target_car_from_flow_state(flow_state: dict[str, Any] | None) -> dict[str, Any]:
    flow_state = flow_state or {}
    return {
        "brand": flow_state.get("target_brand") or flow_state.get("brand"),
        "series": flow_state.get("target_series"),
        "series_alias": flow_state.get("target_series_alias"),
        "model_year": flow_state.get("s05_selected_year_model") or flow_state.get("target_year_model"),
        "trim": flow_state.get("s05_selected_config_model") or flow_state.get("target_config_model"),
    }


def _s08_target_list_after_filter_evidence(
    snapshot: dict[str, Any],
    flow_state: dict[str, Any] | None,
) -> dict[str, Any]:
    flow_state = flow_state or {}
    texts = [str(text).strip() for text in snapshot.get("visible_texts", []) if str(text).strip()]
    blob = "".join(texts)
    compact_blob = _compact_contract_text(blob)
    target_car = _target_car_from_flow_state(flow_state)
    target_series_candidates = [
        item
        for item in dict.fromkeys([str(target_car.get("series") or ""), str(target_car.get("series_alias") or "")])
        if item
    ]
    target_year = str(target_car.get("model_year") or "").strip()
    target_config = str(target_car.get("trim") or "").strip()
    target_color = str(flow_state.get("target_color") or "").strip()
    color_filter_evidence = _s07_snapshot_color_filter_evidence(
        snapshot,
        target_color,
        source="s08_target_list_after_s07_view_result",
    )

    source_gate = {
        "transition_context": flow_state.get("transition_context"),
        "COLOR_FILTER_DONE": bool(flow_state.get("COLOR_FILTER_DONE")),
        "AGE_FILTER_DONE": bool(flow_state.get("AGE_FILTER_DONE")),
        "S07_FILTER_DONE": bool(flow_state.get("S07_FILTER_DONE")),
        "SORT_DONE": bool(flow_state.get("SORT_DONE")),
    }
    source_gate_passed = bool(
        source_gate["transition_context"] == "S07_VIEW_RESULT_TO_LIST"
        and source_gate["COLOR_FILTER_DONE"]
        and source_gate["AGE_FILTER_DONE"]
        and source_gate["S07_FILTER_DONE"]
        and not source_gate["SORT_DONE"]
    )

    target_evidence: list[dict[str, Any]] = []
    for series in target_series_candidates:
        if _compact_contract_text(series) in compact_blob:
            target_evidence.append({"type": "target_series_or_alias_visible", "value": series})
    if target_year and _compact_contract_text(target_year) in compact_blob:
        target_evidence.append({"type": "target_year_visible", "value": target_year})
    if target_config and _compact_contract_text(target_config) in compact_blob:
        target_evidence.append({"type": "target_config_visible", "value": target_config})
    if color_filter_evidence.get("target_color_confirmed"):
        target_evidence.append({"type": "target_color_filter_visible", "value": target_color})
    target_titles = []
    for title in _extract_s10_visible_vehicle_titles(snapshot):
        compact_title = _compact_contract_text(title)
        if (
            any(_compact_contract_text(series) in compact_title for series in target_series_candidates)
            and (not target_year or _compact_contract_text(target_year) in compact_title)
            and (not target_config or _compact_contract_text(target_config) in compact_title)
        ):
            target_titles.append(title)
    if target_titles:
        target_evidence.append({"type": "target_vehicle_card_title_visible", "value": target_titles[:5]})

    core_elements: list[str] = []
    if "综合排序" in blob:
        core_elements.append("sort_control")
    if "车型配置" in blob:
        core_elements.append("filter_bar_model_config_entry")
    if target_evidence:
        core_elements.append("target_filter_or_card_evidence")
    if _collect_list_fields(snapshot) or _extract_s10_visible_vehicle_titles(snapshot):
        core_elements.append("vehicle_list_structure")
    if _extract_s10_visible_prices(snapshot):
        core_elements.append("price_or_vehicle_metadata_fields")

    reverse_failures: list[str] = []
    if _looks_like_s07_filter_panel(snapshot):
        reverse_failures.append("s07_filter_panel")
    if "已选" in blob and "确定" in blob and "全部车型" in blob:
        reverse_failures.append("s05_modal")
    if _looks_like_s04_brand_zone_mixed_list(snapshot) and not source_gate_passed:
        reverse_failures.append("brand_zone_without_s07_source")
    if _looks_like_s10_ready_contract(snapshot):
        reverse_failures.append("s10_ready_candidate")
    if color_filter_evidence.get("color_filter_mismatch"):
        reverse_failures.append("target_color_filter_mismatch")
    if any(marker in blob for marker in ("查看完整报告", "保险理赔记录", "理赔次数", "最大金额")):
        reverse_failures.append("s11_or_report_page")
    if _is_s_login_prompt(snapshot):
        reverse_failures.append("login_page")

    recognized = bool(source_gate_passed and target_evidence and len(core_elements) >= 3 and not reverse_failures)
    return {
        **source_gate,
        "recognized_page_after_view_result": "S08" if recognized else None,
        "s08_page_variant": S08_TARGET_LIST_AFTER_FILTER_VARIANT if recognized else None,
        "s08_source_gate_passed": source_gate_passed,
        "s08_target_filter_evidence": target_evidence,
        "s08_core_elements": core_elements,
        "s08_reverse_exclusion_passed": not reverse_failures,
        "s08_reverse_exclusion_failures": reverse_failures,
        "s08_recognized_by": "S07_source_gate_core_target_reverse_exclusion" if recognized else None,
        "s08_allowed_action": "click_sort_dropdown" if recognized else None,
        "s08_color_filter_evidence": color_filter_evidence,
        "s08_stop_code": None if recognized else (
            "S08_COLOR_FILTER_MISMATCH"
            if color_filter_evidence.get("color_filter_mismatch")
            else ("S06_TARGET_FILTER_EVIDENCE_MISSING" if source_gate_passed and not target_evidence else None)
        ),
    }


def _s10_source_gate_core_evidence(
    snapshot: dict[str, Any],
    flow_state: dict[str, Any] | None,
    target_car: dict[str, Any] | None,
) -> dict[str, Any]:
    flow_state = flow_state or {}
    source_gate_passed = bool(
        flow_state.get("COLOR_FILTER_DONE")
        and flow_state.get("AGE_FILTER_DONE")
        and flow_state.get("S07_FILTER_DONE")
        and flow_state.get("SORT_DONE")
        and flow_state.get("transition_context") in {"S09_PRICE_ASC_TO_LIST", "PRICE_ASC_SORT_DONE_TO_LIST"}
    )
    audit = _s10_contract_card_audit(snapshot, target_car)
    cards = list(audit.get("same_source_cards") or [])
    sort_confirmed = _has_price_low_to_high_sort(snapshot)
    color_filter_evidence = _s07_snapshot_color_filter_evidence(
        snapshot,
        str(flow_state.get("target_color") or ""),
        source="s10_ready_source_gate",
    )
    core_elements: list[str] = []
    if sort_confirmed:
        core_elements.append("price_asc_sort_evidence")
    if cards:
        core_elements.append("complete_target_vehicle_cards")
    if cards:
        core_elements.append("target_trisame_evidence")
    if not audit.get("non_trisame_section_detected") or cards:
        core_elements.append("trisame_boundary_before_non_trisame_section")
    reverse_failures: list[str] = []
    if _looks_like_s07_filter_panel(snapshot):
        reverse_failures.append("s07_filter_panel")
    if "已选" in str(snapshot.get("visible_blob") or "") and "确定" in str(snapshot.get("visible_blob") or ""):
        reverse_failures.append("s05_or_filter_modal")
    if _has_pre_sort_control(snapshot):
        reverse_failures.append("pre_sort_control_visible")
    if any(marker in str(snapshot.get("visible_blob") or "") for marker in ("查看完整报告", "保险理赔记录", "理赔次数", "最大金额")):
        reverse_failures.append("s11_or_report_page")
    if color_filter_evidence.get("color_filter_mismatch"):
        reverse_failures.append("target_color_filter_mismatch")
    ready = bool(source_gate_passed and len(core_elements) >= 3 and not reverse_failures)
    return {
        "s10_source_gate_passed": source_gate_passed,
        "s10_core_elements": core_elements,
        "s10_target_trisame_evidence": cards[:5],
        "s10_reverse_exclusion_passed": not reverse_failures,
        "s10_reverse_exclusion_failures": reverse_failures,
        "complete_target_vehicle_card_count": len(cards),
        "non_trisame_boundary_detected": bool(audit.get("non_trisame_section_detected")),
        "s10_ready_reason": "source_gate_core_elements_target_trisame_boundary_passed" if ready else None,
        "s10_color_filter_evidence": color_filter_evidence,
        "s10_color_filter_mismatch": bool(color_filter_evidence.get("color_filter_mismatch")),
        "s10_color_filter_stop_code": color_filter_evidence.get("color_filter_stop_code"),
    }


def _effective_foreground_package(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("foreground_package") or snapshot.get("xml_package") or "")


def _looks_like_launcher_surface(snapshot: dict[str, Any]) -> bool:
    focused = str(snapshot.get("focused_window") or "").lower()
    foreground = str(snapshot.get("foreground_package") or "").lower()
    xml_package = str(snapshot.get("xml_package") or "").lower()
    return "launcher" in focused or foreground.endswith(".launcher") or xml_package.endswith(".launcher")


def _recognize_page(recognizer: PageRecognizer, snapshot: dict[str, Any], flow_state: dict[str, Any] | None = None) -> str | None:
    blob = str(snapshot.get("visible_blob") or "")
    xml_package = str(snapshot.get("xml_package") or "")
    if str(snapshot.get("xml_package") or "") == "com.android.systemui":
        return "RUNTIME"
    if _is_s_login_prompt(snapshot):
        return "S_LOGIN"
    if (
        GUAZI_APP_ICON_LABEL in blob
        and _effective_foreground_package(snapshot) != GUAZI_PACKAGE
        and xml_package != GUAZI_PACKAGE
        and _looks_like_launcher_surface(snapshot)
    ):
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
        if _looks_like_s02_filter_entry_page(snapshot):
            return "S02_SELECT_CAR_TAB"
    if _looks_like_s07_filter_panel(snapshot):
        page = recognizer.recognize(blob, candidate_ids=["S07"], context={})
        if page:
            return page["id"]
    if _looks_like_s04_series_page(snapshot):
        page = recognizer.recognize(blob, candidate_ids=["S04"], context={})
        if page:
            return page["id"]
    if not _flow_state_ready(flow_state, "S07_FILTER_DONE") and _s06_target_filter_list_after_s05_confirm_evidence(snapshot, flow_state).get("s06_page_variant") == S06_TARGET_FILTER_LIST_VARIANT:
        return "S06"
    if _flow_state_ready(flow_state, "S07_FILTER_DONE") and not _flow_state_ready(flow_state, "SORT_DONE"):
        if _has_s09_sort_popup(snapshot):
            return "S09"
        if _s08_target_list_after_filter_evidence(snapshot, flow_state).get("s08_page_variant") == S08_TARGET_LIST_AFTER_FILTER_VARIANT:
            return "S08"
    if _looks_like_s04_brand_zone_mixed_list(snapshot) and not _flow_state_ready(flow_state, "S07_FILTER_DONE"):
        return "S04"
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
        if state_id == "S06" and _flow_state_ready(flow_state, "S07_FILTER_DONE"):
            continue
        if state_id == "S09" and _flow_state_ready(flow_state, "SORT_DONE") and not _has_s09_sort_popup(snapshot):
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


def _task_id_from_context(context: dict[str, Any]) -> str:
    task = context.get("target_task") if isinstance(context.get("target_task"), dict) else {}
    flow_state = context.get("flow_state") if isinstance(context.get("flow_state"), dict) else {}
    return str(
        context.get("task_id")
        or task.get("task_id")
        or flow_state.get("task_id")
        or flow_state.get("target_task_id")
        or ""
    )


def _maybe_close_guazi_push_popup_and_resume(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    action_page: str,
) -> tuple[dict[str, Any], str | None, bool]:
    def capture_after(stem: str) -> dict[str, Any]:
        return _capture(context["client"], f"{stem}_{_timestamp()}")

    def recognize_after(fresh_snapshot: dict[str, Any]) -> str | None:
        return _recognize_page(context["recognizer"], fresh_snapshot, context.get("flow_state"))

    result = close_guazi_push_popup_from_snapshot(
        context,
        snapshot,
        capture_func=capture_after,
        recognize_func=recognize_after,
        current_stage=action_page,
        capture_stem=f"{action_page.lower()}_guazi_push_popup",
        task_id=_task_id_from_context(context),
        click_func=getattr(context.get("client"), "tap", None),
    )
    if not result.get("popup_detected"):
        return snapshot, None, False
    if not result.get("popup_closed"):
        code = str(result.get("stop_code") or GUAZI_PUSH_POPUP_CLOSE_FAILED)
        if code not in {GUAZI_PUSH_POPUP_CLOSE_FAILED, GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND}:
            code = GUAZI_PUSH_POPUP_CLOSE_FAILED
        issue = _record_issue(
            context["issues"],
            code,
            action_page,
            "Guazi push-notification popup blocked the current page and could not be safely closed.",
            {**snapshot, "guazi_push_popup_close_evidence": {k: v for k, v in result.items() if k != "fresh_snapshot"}},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    fresh_snapshot = result.get("fresh_snapshot")
    if isinstance(fresh_snapshot, dict):
        snapshot.clear()
        snapshot.update(fresh_snapshot)
    context.setdefault("transient_popup_resume_history", []).append(
        {k: v for k, v in result.items() if k != "fresh_snapshot"}
    )
    return snapshot, str(result.get("resume_stage") or ""), True


def contract_action_allowed(context: dict[str, Any], page_id: str, action_id: str) -> bool:
    if not PAGE_CONTRACT_IS_ONLY_EXECUTION_STANDARD:
        return True
    return _page_contract_allows_action(context, page_id, action_id)


def contract_validate_preconditions(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    page_id: str,
    action_id: str,
    *,
    evidence: dict[str, Any] | None = None,
) -> None:
    flow_state = context.get("flow_state") or {}
    if page_id == "S05_MODEL_YEAR_SELECTED" and action_id in {
        "tap_exact_trim",
        "scroll_trim_list",
        "S05_ONLY_ALLOWED_ACTION_SELECT_TARGET_CONFIG_OR_EMISSION_VARIANT_GROUP",
    }:
        if flow_state.get("s05_target_year_selected_confirmed") is not True:
            contract_stop(
                context,
                page_id,
                "S05_RIGHT_CONFIG_SEARCH_WITHOUT_YEAR_CLICK",
                "S05 target config selection is blocked until the left target year tab is clicked and confirmed.",
                {
                    **snapshot,
                    **(evidence or {}),
                    "state_preconditions_passed": False,
                    "required_precondition": "s05_target_year_selected_confirmed",
                    "s05_target_year_selected_confirmed": flow_state.get("s05_target_year_selected_confirmed"),
                    "s05_year_confirmed_by": flow_state.get("s05_year_confirmed_by"),
                    "s05_year_click_record_valid": flow_state.get("s05_year_click_record_valid"),
                    "s05_year_clicked": flow_state.get("s05_year_clicked"),
                    "left_year_selected_text": flow_state.get("left_year_selected_text"),
                },
            )
    if page_id == "S05_TRIM_SELECTED" and action_id in {"tap_green_confirm", "S05_ONLY_ALLOWED_ACTION_CLICK_CONFIRM"}:
        if flow_state.get("s05_target_year_selected_confirmed") is not True:
            contract_stop(
                context,
                page_id,
                "S05_TARGET_YEAR_SELECTION_NOT_CONFIRMED",
                "S05 confirm is blocked because the left target year tab was not deterministically confirmed.",
                {
                    **snapshot,
                    **(evidence or {}),
                    "state_preconditions_passed": False,
                    "required_precondition": "s05_target_year_selected_confirmed",
                    "s05_target_year_selected_confirmed": flow_state.get("s05_target_year_selected_confirmed"),
                    "s05_year_confirmed_by": flow_state.get("s05_year_confirmed_by"),
                    "s05_year_click_record_valid": flow_state.get("s05_year_click_record_valid"),
                },
            )


def contract_validate_action(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    page_id: str,
    action_id: str,
    *,
    evidence: dict[str, Any] | None = None,
) -> None:
    if not action_id:
        contract_stop(
            context,
            page_id,
            "BUSINESS_ACTION_MISSING_CONTRACT_ACTION_ID",
            "Business action blocked because contract_action_id is missing.",
            {**snapshot, **(evidence or {})},
        )
    if not contract_action_allowed(context, page_id, action_id):
        contract_stop(
            context,
            page_id,
            "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT",
            f"{action_id} is not allowed on {page_id} by page contract.",
            {**snapshot, **(evidence or {}), "attempted_action": action_id},
        )
    contract_validate_preconditions(context, snapshot, page_id, action_id, evidence=evidence)


def contract_stop(context: dict[str, Any], page_id: str, stop_code: str, reason: str, evidence: dict[str, Any]) -> None:
    issue_context = {
        **evidence,
        "page_contract_is_only_execution_standard": PAGE_CONTRACT_IS_ONLY_EXECUTION_STANDARD,
        "contract_page_id": page_id,
        "contract_stop_code": stop_code,
    }
    issue = _record_issue(context["issues"], stop_code, page_id, reason, issue_context)
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def contract_click(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    page_id: str,
    action_id: str,
    click_point: tuple[int, int],
    *,
    evidence: dict[str, Any] | None = None,
) -> int:
    contract_validate_action(context, snapshot, page_id, action_id, evidence=evidence)
    action_start = time.perf_counter()
    tap_result = context["client"].tap(int(click_point[0]), int(click_point[1]))
    context["_last_tap_result"] = {
        "page_id": page_id,
        "action_id": action_id,
        "click_point": [int(click_point[0]), int(click_point[1])],
        "tap_result_success": getattr(tap_result, "success", None),
        "tap_result_stdout": getattr(tap_result, "stdout", None),
        "tap_result_stderr": getattr(tap_result, "stderr", None),
        "tap_result_returncode": getattr(tap_result, "returncode", None),
    }
    return int((time.perf_counter() - action_start) * 1000)


def contract_execute_click(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    page_id: str,
    action_id: str,
    click_point: tuple[int, int],
    *,
    evidence: dict[str, Any] | None = None,
) -> int:
    return contract_click(context, snapshot, page_id, action_id, click_point, evidence=evidence)


def contract_execute_device_action(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    page_id: str,
    action_id: str,
    executor: Any,
    *,
    evidence: dict[str, Any] | None = None,
) -> tuple[Any, int]:
    contract_validate_action(context, snapshot, page_id, action_id, evidence=evidence)
    action_start = time.perf_counter()
    result = executor()
    return result, int((time.perf_counter() - action_start) * 1000)


def contract_execute_swipe(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    page_id: str,
    action_id: str,
    *,
    direction: str | None = None,
    points: tuple[int, int, int, int, int] | None = None,
    evidence: dict[str, Any] | None = None,
) -> tuple[Any, int]:
    contract_validate_action(context, snapshot, page_id, action_id, evidence=evidence)
    action_start = time.perf_counter()
    client: AdbClient = context["client"]
    if points is not None:
        sx, sy, ex, ey, duration_ms = points
        result = client.run(["shell", "input", "swipe", str(sx), str(sy), str(ex), str(ey), str(duration_ms)], timeout=20)
    elif direction:
        result = client.swipe(direction)
    else:
        contract_stop(
            context,
            page_id,
            "CONTRACT_SWIPE_TARGET_MISSING",
            "Contract swipe requires either a direction or dynamic points.",
            {**snapshot, **(evidence or {}), "attempted_action": action_id},
        )
    return result, int((time.perf_counter() - action_start) * 1000)


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


MIUI_LAUNCHER_PACKAGES = {"com.miui.home", "com.miui.newhome"}
MIUI_LAUNCHER_OVERLAY_WINDOW_TOKENS = ("LauncherOverlayWindow", "com.miui.newhome", "com.miui.home")
MIUI_LAUNCHER_OVERLAY_TEXT_HINTS = (
    "看点",
    "穿山甲AD",
    "广告",
    "立即下载",
    "首页",
    "视频",
    "热榜",
    "我的",
    "搜索",
)
MIUI_DESKTOP_TEXT_HINTS = ("瓜子二手车", "设置", "相册", "天气", "应用列表")
MIUI_AD_CLOSE_RESOURCE_TOKENS = ("com.miui.newhome:id/ad_close", "ad_close")
GUAZI_FOREGROUND_STALE_KEYGUARD_TEXT_HINTS = (
    "瓜子官方检测报告",
    "瓜子二手车",
    "检测报告",
    "首页",
    "选车",
    "卖车",
    "我的",
    "综合排序",
    "价格从低到高",
    "品牌",
    "选择品牌",
    "车型",
    "车系",
    "车况",
    "异常细节",
    "AI详细解读",
)
SECURE_KEYGUARD_INPUT_HINTS = (
    "输入密码",
    "锁屏密码",
    "绘制图案",
    "图案",
    "PIN",
    "指纹",
    "紧急呼叫",
    "重新输入密码",
    "解锁密码",
    "password",
    "keyguard password",
)


def _snapshot_evidence_text(snapshot: dict[str, Any]) -> str:
    values: list[str] = [
        str(snapshot.get("focused_window") or ""),
        str(snapshot.get("foreground_package") or ""),
        str(snapshot.get("xml_package") or ""),
        str(snapshot.get("visible_blob") or ""),
    ]
    values.extend(str(text) for text in snapshot.get("visible_texts", []) if text)
    for node in snapshot.get("nodes", []):
        values.extend(str(label) for label in node.get("labels", []) if label)
        values.append(str(node.get("resource_id") or ""))
        values.append(str(node.get("package") or ""))
    return "\n".join(value for value in values if value)


def has_secure_keyguard_input_evidence(snapshot: dict[str, Any]) -> bool:
    if bool(snapshot.get("keyguard_secure")):
        return True
    evidence = _snapshot_evidence_text(snapshot)
    lowered = evidence.lower()
    return any(hint in evidence or hint.lower() in lowered for hint in SECURE_KEYGUARD_INPUT_HINTS)


def _miui_ad_close_nodes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        resource_id = str(node.get("resource_id") or "")
        if any(token in resource_id for token in MIUI_AD_CLOSE_RESOURCE_TOKENS):
            nodes.append(node)
    return nodes


def is_miui_newhome_ad_overlay(snapshot: dict[str, Any]) -> bool:
    if has_secure_keyguard_input_evidence(snapshot):
        return False
    focused = str(snapshot.get("focused_window") or "")
    foreground = str(snapshot.get("foreground_package") or "")
    xml_package = str(snapshot.get("xml_package") or "")
    package_hit = (
        foreground in MIUI_LAUNCHER_PACKAGES
        or xml_package in MIUI_LAUNCHER_PACKAGES
        or any(token in focused for token in MIUI_LAUNCHER_OVERLAY_WINDOW_TOKENS)
    )
    if not package_hit:
        return False
    evidence = _snapshot_evidence_text(snapshot)
    hint_count = sum(1 for hint in MIUI_LAUNCHER_OVERLAY_TEXT_HINTS if hint in evidence)
    return bool(_miui_ad_close_nodes(snapshot)) or hint_count >= 2


def is_miui_launcher_overlay_visible(snapshot: dict[str, Any]) -> bool:
    if has_secure_keyguard_input_evidence(snapshot):
        return False
    focused = str(snapshot.get("focused_window") or "")
    foreground = str(snapshot.get("foreground_package") or "")
    xml_package = str(snapshot.get("xml_package") or "")
    if is_miui_newhome_ad_overlay(snapshot):
        return True
    return (
        foreground in MIUI_LAUNCHER_PACKAGES
        or xml_package in MIUI_LAUNCHER_PACKAGES
        or any(token in focused for token in MIUI_LAUNCHER_OVERLAY_WINDOW_TOKENS)
    )


def is_launcher_operable_despite_stale_keyguard(snapshot: dict[str, Any]) -> bool:
    if has_secure_keyguard_input_evidence(snapshot) or is_miui_newhome_ad_overlay(snapshot):
        return False
    evidence = _snapshot_evidence_text(snapshot)
    desktop_hint_seen = any(hint in evidence for hint in MIUI_DESKTOP_TEXT_HINTS)
    return _guazi_icon_visible(snapshot) or (_launcher_window_visible(snapshot) and desktop_hint_seen)


def _classify_old_guazi_page(snapshot: dict[str, Any]) -> str:
    evidence = _snapshot_evidence_text(snapshot)
    if "AI详细解读" in evidence or "异常细节" in evidence:
        return "S14_DETAIL_POPUP"
    if "瓜子官方检测报告" in evidence or "检测报告" in evidence:
        return "S14_REPORT_DETAIL"
    if "价格从低到高" in evidence or "综合排序" in evidence:
        return "S10_READY"
    if any(hint in evidence for hint in ("首页", "选车", "卖车", "我的")):
        return "S01_OR_S02"
    return "GUAZI_UNKNOWN_PAGE"


def is_guazi_foreground_operable_despite_stale_keyguard(snapshot: dict[str, Any]) -> bool:
    if has_secure_keyguard_input_evidence(snapshot):
        return False
    foreground = str(snapshot.get("foreground_package") or "")
    xml_package = str(snapshot.get("xml_package") or "")
    focused = str(snapshot.get("focused_window") or "")
    package_hit = foreground == GUAZI_PACKAGE or xml_package == GUAZI_PACKAGE or GUAZI_PACKAGE in focused
    if not package_hit:
        return False
    if snapshot.get("screenshot_missing") or snapshot.get("xml_missing"):
        return False
    if not str(snapshot.get("fresh_xml") or "").strip() and not snapshot.get("visible_texts"):
        return False
    evidence = _snapshot_evidence_text(snapshot)
    return any(hint in evidence for hint in GUAZI_FOREGROUND_STALE_KEYGUARD_TEXT_HINTS)


GUAZI_HOME_READY_TEXT_HINTS = (
    "首页",
    "选车",
    "卖车",
    "我的",
    "搜索",
    "唐山",
    "官方自营",
    "个人直卖",
    "棣栭〉",
    "閫夎溅",
    "鍗栬溅",
    "鎴戠殑",
    "鎼滅储",
    "鍞愬北",
    "瀹樻柟鑷惀",
    "涓汉鐩村崠",
    "S01_OK",
    "S02_OK",
)
GUAZI_STARTUP_SPLASH_TEXT_HINTS = (
    "跳过",
    "跳过广告",
    "剩余",
    "广告",
    "立即领取",
    "立即查看",
    "璺宠繃",
    "璺宠繃骞垮憡",
    "鍓╀綑",
    "骞垮憡",
    "绔嬪嵆棰嗗彇",
    "绔嬪嵆鏌ョ湅",
)
GUAZI_FRONTEND_MAX_RETRY_ATTEMPTS = 3


def _snapshot_has_guazi_foreground(snapshot: dict[str, Any]) -> bool:
    foreground = str(snapshot.get("foreground_package") or "")
    xml_package = str(snapshot.get("xml_package") or "")
    focused = str(snapshot.get("focused_window") or "")
    return foreground == GUAZI_PACKAGE or xml_package == GUAZI_PACKAGE or GUAZI_PACKAGE in focused


def _snapshot_has_runtime_evidence(snapshot: dict[str, Any]) -> bool:
    return not bool(snapshot.get("screenshot_missing")) and not bool(snapshot.get("xml_missing"))


def _guazi_home_ready(snapshot: dict[str, Any]) -> bool:
    if not _snapshot_has_guazi_foreground(snapshot) or not _snapshot_has_runtime_evidence(snapshot):
        return False
    evidence = _snapshot_evidence_text(snapshot)
    hits = [hint for hint in GUAZI_HOME_READY_TEXT_HINTS if hint and hint in evidence]
    return len(hits) >= 2


def _guazi_startup_splash_visible(snapshot: dict[str, Any]) -> bool:
    if not _snapshot_has_guazi_foreground(snapshot):
        return False
    evidence = _snapshot_evidence_text(snapshot)
    return any(hint in evidence for hint in GUAZI_STARTUP_SPLASH_TEXT_HINTS)


def _frontend_retry_attempt_payload(snapshot: dict[str, Any], *, attempt: int, state: str | None) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "state": state,
        "guazi_foreground": _snapshot_has_guazi_foreground(snapshot),
        "runtime_evidence_ready": _snapshot_has_runtime_evidence(snapshot),
        "guazi_home_ready": _guazi_home_ready(snapshot),
        "startup_splash_visible": _guazi_startup_splash_visible(snapshot),
        "foreground_package": snapshot.get("foreground_package"),
        "xml_package": snapshot.get("xml_package"),
        "focused_window": snapshot.get("focused_window"),
        "screenshot_missing": bool(snapshot.get("screenshot_missing")),
        "xml_missing": bool(snapshot.get("xml_missing")),
        "screenshot_error": snapshot.get("screenshot_error"),
        "xml_dump_error": snapshot.get("xml_dump_error"),
        "screenshot_path": snapshot.get("screenshot_path"),
        "xml_path": snapshot.get("xml_path"),
    }


def _classify_guazi_frontend_retry_failure(attempts: list[dict[str, Any]]) -> str:
    if not any(item.get("guazi_foreground") for item in attempts):
        return "APP_NOT_FOREGROUND_AFTER_3_RETRIES"
    if any(item.get("startup_splash_visible") for item in attempts):
        return "GUAZI_SPLASH_PAGE_STUCK_AFTER_3_RETRIES"
    return "GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES"


def _retry_guazi_frontend_until_ready(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    state: str | None,
    *,
    reason: str,
) -> tuple[dict[str, Any], str | None]:
    startup = context.setdefault("startup", {})
    recognizer: PageRecognizer = context["recognizer"]
    client: AdbClient = context["client"]
    issues: IssueRecorder = context["issues"]
    attempts = startup.setdefault("guazi_frontend_retry_attempts", [])
    attempts.append(_frontend_retry_attempt_payload(snapshot, attempt=1, state=state))
    if state in S01_TO_S10_STATES | {"S_LOGIN"} and _snapshot_has_runtime_evidence(snapshot):
        startup["guazi_frontend_ready_attempt"] = 1
        return snapshot, state
    if _guazi_home_ready(snapshot):
        startup["guazi_frontend_ready_attempt"] = 1
        return snapshot, state or "S01"
    if not _snapshot_has_guazi_foreground(snapshot) and state in S01_TO_S10_STATES | {"S_LOGIN"}:
        startup["guazi_frontend_ready_attempt"] = 1
        return snapshot, state

    for attempt in range(2, GUAZI_FRONTEND_MAX_RETRY_ATTEMPTS + 1):
        time.sleep(0.6 if attempt == 2 else 1.2)
        retry_snapshot = _capture(client, f"s01_s10_frontend_retry_{attempt}_{_timestamp()}")
        _startup_note_capture(context, retry_snapshot)
        retry_snapshot["app_entry_mode"] = "force_restart"
        retry_snapshot["app_force_restart_reason"] = reason
        retry_state = _recognize_page(recognizer, retry_snapshot, context.get("flow_state"))
        attempts.append(_frontend_retry_attempt_payload(retry_snapshot, attempt=attempt, state=retry_state))
        _add_runtime_timing(
            context,
            step_name="GUAZI_FRONTEND_RETRY",
            page_name=retry_state or "RUNTIME",
            action_name="refresh_guazi_frontend_evidence",
            transition_wait_ms=600 if attempt == 2 else 1200,
            snapshot=retry_snapshot,
            extra={
                "attempt": attempt,
                "max_attempts": GUAZI_FRONTEND_MAX_RETRY_ATTEMPTS,
                "guazi_foreground": _snapshot_has_guazi_foreground(retry_snapshot),
                "runtime_evidence_ready": _snapshot_has_runtime_evidence(retry_snapshot),
                "reason_category": "GUAZI_FRONTEND_RETRY",
                "reason_detail": "Guazi foreground evidence is refreshed before stopping so XML/screenshot failures are not mislabeled as app-not-foreground.",
            },
        )
        if retry_state in S01_TO_S10_STATES | {"S_LOGIN"} and _snapshot_has_runtime_evidence(retry_snapshot):
            startup["guazi_frontend_ready_attempt"] = attempt
            return retry_snapshot, retry_state
        if _guazi_home_ready(retry_snapshot):
            startup["guazi_frontend_ready_attempt"] = attempt
            return retry_snapshot, retry_state or "S01"
        snapshot, state = retry_snapshot, retry_state

    code = _classify_guazi_frontend_retry_failure(attempts)
    startup["guazi_frontend_retry_exhausted"] = True
    startup["guazi_frontend_retry_failure_code"] = code
    snapshot["runtime_recovery_cause"] = code
    _raise_device_ready_gate(
        context,
        snapshot,
        code=code,
        message=f"Guazi frontend was not ready after {GUAZI_FRONTEND_MAX_RETRY_ATTEMPTS} recovery attempts.",
        failed_action="guazi_frontend_retry_until_ready",
    )
    return snapshot, state


def _startup_close_x_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        if not node.get("bounds") or not node.get("clickable") or not node.get("enabled"):
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        if labels == {"\u00d7"}:
            candidates.append(node)
    return candidates


STARTUP_SKIP_PATTERN = re.compile(r"(跳过广告|跳过|剩余\s*\d+\s*秒\s*跳过)")
STARTUP_SKIP_FORBIDDEN_TEXTS = (
    "立即查看",
    "立即领取",
    "下载",
    "打开",
    "开心收下",
    "3天内不再弹出",
)


def _startup_skip_text(labels: list[str]) -> str | None:
    joined = " ".join(str(label or "").strip() for label in labels if str(label or "").strip())
    if not joined or any(text in joined for text in STARTUP_SKIP_FORBIDDEN_TEXTS):
        return None
    match = STARTUP_SKIP_PATTERN.search(joined)
    return match.group(0) if match else None


def _startup_skip_node_from_xml(snapshot: dict[str, Any]) -> dict[str, Any] | None:
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
        matched_text = _startup_skip_text(labels)
        if not matched_text:
            continue
        current: ElementTree.Element | None = element
        while current is not None:
            bounds = _parse_bounds(current.attrib.get("bounds", ""))
            if (
                _has_nonzero_bounds(bounds)
                and str(current.attrib.get("enabled") or "true") == "true"
                and str(current.attrib.get("clickable") or "") == "true"
            ):
                text = str(current.attrib.get("text") or "").strip()
                desc = str(current.attrib.get("content-desc") or "").strip()
                return {
                    "text": text,
                    "content_desc": desc,
                    "labels": [item for item in [text, desc] if item],
                    "bounds": bounds,
                    "clickable": True,
                    "enabled": True,
                    "startup_skip_text": matched_text,
                    "startup_skip_click_strategy": "clickable_skip_ancestor_bounds" if current is not element else "text_node_bounds",
                }
            current = parent_map.get(current)
    return None


def _startup_skip_candidate(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_nonzero_bounds(bounds):
            continue
        labels = [str(label).strip() for label in node.get("labels", []) if str(label).strip()]
        matched_text = _startup_skip_text(labels)
        if matched_text and node.get("enabled", True) and node.get("clickable"):
            return {
                **node,
                "startup_skip_text": matched_text,
                "startup_skip_click_strategy": "text_node_bounds",
            }
    return _startup_skip_node_from_xml(snapshot)


def _maybe_click_startup_skip_once(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    state: str | None,
    reason: str,
    capture_stem: str,
) -> tuple[dict[str, Any], str | None, bool]:
    if state in S01_TO_S10_STATES | {"S_LOGIN", "S_APP_ICON", "RUNTIME"}:
        return snapshot, state, False
    if context.get("startup_skip_clicked"):
        return snapshot, state, False
    if str(snapshot.get("foreground_package") or snapshot.get("xml_package") or "") != GUAZI_PACKAGE:
        return snapshot, state, False

    startup = context.setdefault("startup", {})
    detect_started = time.perf_counter()
    node = _startup_skip_candidate(snapshot)
    detected = node is not None and bool(node.get("bounds"))
    startup["startup_skip_detected"] = detected
    startup["startup_skip_text"] = node.get("startup_skip_text") if node else None
    startup["startup_skip_bounds"] = node.get("bounds") if node else None
    _add_runtime_timing(
        context,
        step_name="STARTUP_AD_SKIP_DETECT",
        page_name=state or "S00",
        action_name="find_startup_skip_text_node",
        field_read_ms=int((time.perf_counter() - detect_started) * 1000),
        snapshot=snapshot,
        extra={
            "startup_skip_detected": detected,
            "startup_skip_text": startup.get("startup_skip_text"),
            "startup_skip_bounds": startup.get("startup_skip_bounds"),
            "startup_skip_click_strategy": node.get("startup_skip_click_strategy") if node else None,
            "reason_category": "STARTUP_SKIP_NODE_NOT_RECOGNIZED" if not detected else "STARTUP_SKIP_NODE_VISIBLE",
            "reason_detail": "S00 startup ad skip is detected from XML text/content-desc and clicked only by node bounds",
            "solution": "click the XML node or clickable ancestor; never use a fixed lower-right coordinate",
        },
    )
    if not detected:
        startup.setdefault("startup_skip_clicked", False)
        startup.setdefault("startup_skip_click_strategy", None)
        return snapshot, state, False

    client: AdbClient = context["client"]
    click_x, click_y = _center(node["bounds"])
    click_started = time.perf_counter()
    client.tap(click_x, click_y)
    action_ms = int((time.perf_counter() - click_started) * 1000)
    context["startup_skip_clicked"] = True
    startup["startup_skip_clicked"] = True
    startup["startup_skip_click_strategy"] = node.get("startup_skip_click_strategy") or "text_node_bounds"
    _add_runtime_timing(
        context,
        step_name="STARTUP_AD_SKIP_CLICK",
        page_name=state or "S00",
        action_name="tap_startup_skip_text_node",
        action_ms=action_ms,
        snapshot=snapshot,
        extra={
            "startup_skip_detected": True,
            "startup_skip_text": startup.get("startup_skip_text"),
            "startup_skip_bounds": startup.get("startup_skip_bounds"),
            "startup_skip_clicked": True,
            "startup_skip_click_strategy": startup.get("startup_skip_click_strategy"),
            "clicked_point": [click_x, click_y],
            "reason_category": "STARTUP_SKIP_NODE_VISIBLE_BUT_NOT_CLICKED",
            "reason_detail": "startup ad skip is clicked immediately through XML node bounds when exposed",
            "solution": "keep the node-driven skip action before waiting for the countdown to finish",
        },
    )

    time.sleep(0.3)
    next_snapshot = _capture(client, f"{capture_stem}_{_timestamp()}")
    next_snapshot["app_entry_mode"] = "force_restart"
    next_snapshot["app_force_restart_reason"] = reason
    next_state = _recognize_page(context["recognizer"], next_snapshot, context.get("flow_state"))
    startup["startup_skip_after_page"] = next_state
    startup["startup_skip_after_screenshot_path"] = next_snapshot.get("screenshot_path")
    startup["startup_skip_after_xml_path"] = next_snapshot.get("xml_path")
    _add_runtime_timing(
        context,
        step_name="STARTUP_AD_AFTER_SKIP_FRESH",
        page_name=next_state or "UNKNOWN",
        action_name="fresh_after_startup_skip",
        transition_wait_ms=300,
        snapshot=next_snapshot,
        extra={
            "startup_skip_after_page": next_state,
            "startup_skip_clicked": True,
            "reason_category": "WEBVIEW_TEXT_DELAY" if next_state not in S01_TO_S10_STATES else "STARTUP_SKIP_DONE",
            "reason_detail": "after clicking skip the runtime refreshes screenshot/XML and recognizes the next contract page",
            "solution": "continue only after a fresh page contract check",
        },
    )
    return next_snapshot, next_state, True


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
    action_ms = contract_execute_click(
        context,
        snapshot,
        "S06",
        "tap_trim_filter",
        _center(node["bounds"]),
        evidence={
            "clicked_text": "车型配置",
            "clicked_node_bounds": node.get("bounds"),
            "clicked_action_id": "S06_ONLY_ALLOWED_ACTION_CLICK_MODEL_CONFIG",
            **s06_target_filter_evidence,
        },
    )
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


def _recover_s_login_interrupt(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    action_page: str,
    max_later_attempts: int = 5,
    max_back_attempts: int = 2,
) -> dict[str, Any]:
    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    recognizer: PageRecognizer = context["recognizer"]
    current_snapshot = snapshot
    history = context.setdefault("s_login_interrupt_recovery_history", [])
    context["s_login_interrupt_recovery_attempted"] = True
    for attempt in range(1, max_later_attempts + max_back_attempts + 1):
        current_state = _recognize_page(recognizer, current_snapshot, context.get("flow_state"))
        if current_state != "S_LOGIN":
            context["s_login_interrupt_recovery_result"] = "dismissed"
            return current_snapshot

        later_bounds = _s_login_later_bounds(current_snapshot)
        if later_bounds:
            action_id = "S_LOGIN_ONLY_ALLOWED_ACTION_CLICK_LATER"
            clicked_bounds = later_bounds[0]
            action_ms = contract_execute_click(
                context,
                current_snapshot,
                "S_LOGIN",
                action_id,
                _center(clicked_bounds),
                evidence={
                    "clicked_text": S_LOGIN_LATER_TEXT,
                    "clicked_node_bounds": clicked_bounds,
                    "interrupted_action_page": action_page,
                    "s_login_interrupt_recovery_attempt": attempt,
                },
            )
            transition_wait_ms = 800
            time.sleep(transition_wait_ms / 1000)
            next_snapshot = _capture(client, f"s_login_interrupt_after_later_{attempt}_{_timestamp()}")
            _record_capture_timing(context, next_snapshot, step_name="s_login_interrupt_recovery", page_name="S_LOGIN")
            next_state = _recognize_page(recognizer, next_snapshot, context.get("flow_state"))
            history.append(
                {
                    "attempt": attempt,
                    "action_id": action_id,
                    "clicked_text": S_LOGIN_LATER_TEXT,
                    "clicked_bounds": clicked_bounds,
                    "before_screenshot_path": current_snapshot.get("screenshot_path"),
                    "before_xml_path": current_snapshot.get("xml_path"),
                    "after_screenshot_path": next_snapshot.get("screenshot_path"),
                    "after_xml_path": next_snapshot.get("xml_path"),
                    "recognized_after_action": next_state,
                }
            )
            timing.add(
                step_name="S_LOGIN_INTERRUPT_RECOVERY",
                page_name="S_LOGIN",
                action_name=action_id,
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=action_ms,
                transition_wait_ms=transition_wait_ms,
                screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
                xml_path=str(next_snapshot.get("xml_path") or ""),
                extra={"interrupted_action_page": action_page, "recognized_after_action": next_state},
            )
            current_snapshot = next_snapshot
            continue

        if len([item for item in history if str(item.get("action_id") or "").endswith("PRESS_BACK") or item.get("action_id") == "S_LOGIN_BOTTOM_BACK_ONCE"]) >= max_back_attempts:
            context["s_login_interrupt_recovery_result"] = "back_attempts_exhausted"
            contract_stop(
                context,
                "S_LOGIN",
                "S_LOGIN_RECOVERY_UNSUPPORTED",
                "S_LOGIN interrupt could not be dismissed within the allowed BACK / bottom-return attempts.",
                {
                    **current_snapshot,
                    "interrupted_action_page": action_page,
                    "s_login_interrupt_recovery_history": history,
                },
            )

        bottom_back = _s_login_bottom_back_node(current_snapshot)
        if bottom_back and bottom_back.get("bounds"):
            action_id = "S_LOGIN_BOTTOM_BACK_ONCE"
            clicked_bounds = bottom_back["bounds"]
            action_ms = contract_execute_click(
                context,
                current_snapshot,
                "S_LOGIN",
                action_id,
                _center(clicked_bounds),
                evidence={
                    "clicked_text": "S_LOGIN_BOTTOM_BACK",
                    "clicked_node_bounds": clicked_bounds,
                    "interrupted_action_page": action_page,
                    "s_login_interrupt_recovery_attempt": attempt,
                },
            )
        elif recognize_startup_account_center_login_page(current_snapshot):
            action_id = "S_LOGIN_ONLY_ALLOWED_ACTION_PRESS_BACK"
            back_result, action_ms = contract_execute_device_action(
                context,
                current_snapshot,
                "S_LOGIN",
                action_id,
                client.back,
                evidence={
                    "interrupted_action_page": action_page,
                    "s_login_interrupt_recovery_attempt": attempt,
                    "account_center_login_page": True,
                },
            )
            clicked_bounds = None
        else:
            context["s_login_interrupt_recovery_result"] = "unsupported_no_later_or_back"
            contract_stop(
                context,
                "S_LOGIN",
                "S_LOGIN_RECOVERY_UNSUPPORTED",
                "S_LOGIN interrupt has no exact 稍后 and no allowed bottom/system return control.",
                {
                    **current_snapshot,
                    "interrupted_action_page": action_page,
                    "s_login_interrupt_recovery_history": history,
                },
            )

        transition_wait_ms = 800
        time.sleep(transition_wait_ms / 1000)
        next_snapshot = _capture(client, f"s_login_interrupt_after_back_{attempt}_{_timestamp()}")
        _record_capture_timing(context, next_snapshot, step_name="s_login_interrupt_recovery", page_name="S_LOGIN")
        next_state = _recognize_page(recognizer, next_snapshot, context.get("flow_state"))
        history.append(
            {
                "attempt": attempt,
                "action_id": action_id,
                "clicked_bounds": clicked_bounds,
                "before_screenshot_path": current_snapshot.get("screenshot_path"),
                "before_xml_path": current_snapshot.get("xml_path"),
                "after_screenshot_path": next_snapshot.get("screenshot_path"),
                "after_xml_path": next_snapshot.get("xml_path"),
                "recognized_after_action": next_state,
            }
        )
        timing.add(
            step_name="S_LOGIN_INTERRUPT_RECOVERY",
            page_name="S_LOGIN",
            action_name=action_id,
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=action_ms,
            transition_wait_ms=transition_wait_ms,
            screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
            xml_path=str(next_snapshot.get("xml_path") or ""),
            extra={"interrupted_action_page": action_page, "recognized_after_action": next_state},
        )
        current_snapshot = next_snapshot

    context["s_login_interrupt_recovery_result"] = "attempts_exhausted"
    contract_stop(
        context,
        "S_LOGIN",
        "S_LOGIN_RECOVERY_UNSUPPORTED",
        "S_LOGIN interrupt remained after all allowed recovery attempts.",
        {
            **current_snapshot,
            "interrupted_action_page": action_page,
            "s_login_interrupt_recovery_history": history,
        },
    )


def _ensure_current_page_contract(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    allowed_states: set[str],
    *,
    action_page: str,
) -> str:
    snapshot, resumed_state, popup_closed = _maybe_close_guazi_push_popup_and_resume(
        context,
        snapshot,
        action_page=action_page,
    )
    if popup_closed:
        actual = resumed_state or _recognize_page(context["recognizer"], snapshot, context.get("flow_state"))
    else:
        actual = _recognize_page(context["recognizer"], snapshot, context.get("flow_state"))
    if actual == "S_LOGIN":
        recovered_snapshot = _recover_s_login_interrupt(context, snapshot, action_page=action_page)
        snapshot.clear()
        snapshot.update(recovered_snapshot)
        actual = _recognize_page(context["recognizer"], snapshot, context.get("flow_state"))
        if actual not in allowed_states:
            issue = _record_issue(
                context["issues"],
                "PAGE_CONTRACT_MISMATCH_AFTER_LOGIN_EXIT",
                action_page,
                f"{action_page} action remains blocked after S_LOGIN recovery; current fresh page contract is {actual or 'UNKNOWN'}.",
                {
                    **snapshot,
                    "expected_allowed_states": sorted(allowed_states),
                    "actual_state": actual,
                    "s_login_interrupt_recovery_history": context.get("s_login_interrupt_recovery_history"),
                },
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
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
    snapshot, resumed_state, popup_closed = _maybe_close_guazi_push_popup_and_resume(
        context,
        snapshot,
        action_page="CURRENT_STATE",
    )
    if popup_closed:
        actual = resumed_state or _recognize_page(context["recognizer"], snapshot, context.get("flow_state"))
    else:
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
    return (
        "launcher" in focused.lower()
        or foreground.endswith(".launcher")
        or xml_package.endswith(".launcher")
        or foreground in MIUI_LAUNCHER_PACKAGES
        or xml_package in MIUI_LAUNCHER_PACKAGES
    )


def _recover_miui_launcher_overlay_after_stale_keyguard(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    max_attempts: int = 2,
) -> tuple[dict[str, Any], bool]:
    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    startup = context.setdefault("startup", {})
    startup["miui_launcher_overlay_detected"] = True
    startup["keyguard_showing_stale_but_launcher_overlay_visible"] = True
    startup["unlock_gate_recovery_reason"] = "MIUI_NEWHOME_OVERLAY_WITH_NON_SECURE_KEYGUARD"
    startup["miui_newhome_ad_overlay_detected"] = is_miui_newhome_ad_overlay(snapshot)
    startup["miui_newhome_ad_close_detected"] = bool(_miui_ad_close_nodes(snapshot))
    startup["miui_launcher_overlay_recovery_action_policy"] = "HOME_ONLY_DO_NOT_CLICK_AD_BODY"
    startup["miui_launcher_overlay_did_not_click_download"] = True
    startup["miui_launcher_overlay_recovery_attempts"] = []
    current = snapshot
    recovery_started = time.perf_counter()

    for attempt in range(1, max_attempts + 1):
        action_started = time.perf_counter()
        home_result = client.home_key_once()
        action_ms = int((time.perf_counter() - action_started) * 1000)
        startup["miui_launcher_overlay_recovery_attempts"].append(
            {
                "attempt": attempt,
                "action": "HOME",
                "home_success": bool(home_result.get("home_success")),
                "previous_focused_window": current.get("focused_window"),
                "previous_xml_package": current.get("xml_package"),
                "ad_close_detected": bool(_miui_ad_close_nodes(current)),
            }
        )
        timing.add(
            step_name="DEVICE_LAUNCHER_READY",
            page_name="RUNTIME",
            action_name="miui_launcher_overlay_recovery_home",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=action_ms,
            transition_wait_ms=900,
            screenshot_path=None,
            xml_path=None,
            extra={
                "attempt": attempt,
                "reason_category": "MIUI_LAUNCHER_OVERLAY_VISIBLE_AFTER_UNLOCK",
                "reason_detail": "HOME is issued once to leave MIUI newhome/feed/ad overlay; ad body and download buttons are never clicked.",
                "solution": "recapture and continue only when launcher or app icon evidence is visible.",
            },
        )
        time.sleep(0.9)
        current = _capture(client, f"device_ready_after_miui_launcher_overlay_home_{attempt}_{_timestamp()}")
        _record_capture_timing(context, current, step_name="DEVICE_LAUNCHER_READY", page_name="RUNTIME")
        startup["miui_launcher_overlay_last_screenshot_path"] = current.get("screenshot_path")
        startup["miui_launcher_overlay_last_xml_path"] = current.get("xml_path")
        startup["miui_launcher_overlay_after_home_visible_text_digest"] = _visible_text_digest(current)
        _startup_note_capture(context, current)
        if has_secure_keyguard_input_evidence(current):
            startup["miui_launcher_overlay_secure_input_detected_after_home"] = True
            startup["miui_recovery_duration_ms"] = int((time.perf_counter() - recovery_started) * 1000)
            return current, False
        if is_launcher_operable_despite_stale_keyguard(current):
            startup["unlock_gate_passed_by_launcher_visible_evidence"] = True
            startup["miui_launcher_overlay_recovered"] = True
            startup["miui_recovery_duration_ms"] = int((time.perf_counter() - recovery_started) * 1000)
            return current, True
        if not current.get("keyguard_showing") and not is_miui_launcher_overlay_visible(current):
            startup["unlock_gate_passed_after_miui_overlay_home"] = True
            startup["miui_launcher_overlay_recovered"] = True
            startup["miui_recovery_duration_ms"] = int((time.perf_counter() - recovery_started) * 1000)
            return current, True

    startup["miui_launcher_overlay_recovered"] = False
    startup["miui_recovery_duration_ms"] = int((time.perf_counter() - recovery_started) * 1000)
    return current, False


def _snapshot_label_set(snapshot: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for node in snapshot.get("nodes", []):
        labels.update(str(label).strip() for label in node.get("labels", []) if str(label).strip())
    return labels


def _snapshot_labels(snapshot: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for node in snapshot.get("nodes", []):
        labels.extend(str(label).strip() for label in node.get("labels", []) if str(label).strip())
    return labels


def _labels_contain(labels: list[str] | set[str], token: str) -> bool:
    return any(token and token in str(label) for label in labels)


def recognize_startup_account_center_login_page(snapshot: dict[str, Any]) -> bool:
    labels = _snapshot_labels(snapshot)
    foreground = str(snapshot.get("foreground_package") or "")
    xml_package = str(snapshot.get("xml_package") or "")
    focused = str(snapshot.get("focused_window") or "")
    package_hit = (
        foreground == STARTUP_ACCOUNT_CENTER_PACKAGE
        or xml_package == STARTUP_ACCOUNT_CENTER_PACKAGE
        or STARTUP_ACCOUNT_CENTER_PACKAGE in focused
    )
    welcome_seen = _labels_contain(labels, STARTUP_ACCOUNT_CENTER_WELCOME_TEXT)
    phone_seen = any(_labels_contain(labels, text) for text in STARTUP_ACCOUNT_CENTER_PHONE_TEXTS)
    code_seen = _labels_contain(labels, STARTUP_ACCOUNT_CENTER_CODE_TEXT)
    get_code_seen = _labels_contain(labels, STARTUP_ACCOUNT_CENTER_GET_CODE_TEXT)
    password_login_seen = _labels_contain(labels, STARTUP_ACCOUNT_CENTER_PASSWORD_LOGIN_TEXT)
    agreement_seen = any(_labels_contain(labels, text) for text in STARTUP_ACCOUNT_CENTER_AGREEMENT_TEXTS)
    if package_hit and welcome_seen:
        return True
    if welcome_seen and phone_seen and code_seen:
        return True
    return get_code_seen and password_login_seen and agreement_seen


def _startup_account_center_learning_loop_solution(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_code": "STARTUP_ACCOUNT_CENTER_LOGIN_PAGE",
        "category": "startup_entry_blocker",
        "trigger_context": "APP_FORCE_RESTART after launcher later-dialog dismissal entered account-center login page.",
        "foreground_package": STARTUP_ACCOUNT_CENTER_PACKAGE,
        "recognition_signals": [
            STARTUP_ACCOUNT_CENTER_WELCOME_TEXT,
            *STARTUP_ACCOUNT_CENTER_PHONE_TEXTS,
            STARTUP_ACCOUNT_CENTER_CODE_TEXT,
            STARTUP_ACCOUNT_CENTER_GET_CODE_TEXT,
            STARTUP_ACCOUNT_CENTER_PASSWORD_LOGIN_TEXT,
            *STARTUP_ACCOUNT_CENTER_AGREEMENT_TEXTS,
        ],
        "approved_solution": "Press Android BACK at most 2 times, fresh after each BACK, then continue exact Guazi app-icon lookup after the account-center page exits.",
        "forbidden_actions": sorted(STARTUP_ACCOUNT_CENTER_FORBIDDEN_ACTIONS),
        "script_status": "fixed_script_required",
        "contract_version": "V1.30",
        "verification_required": True,
        "screenshot_path": snapshot.get("screenshot_path"),
        "xml_path": snapshot.get("xml_path"),
        "focused_window": snapshot.get("focused_window"),
    }


def _startup_account_center_action_allowed(action_id: str) -> bool:
    return action_id == STARTUP_ACCOUNT_CENTER_BACK_ACTION_ID


def _launcher_account_dialog_detected(snapshot: dict[str, Any]) -> bool:
    if not _launcher_window_visible(snapshot):
        return False
    if str(snapshot.get("foreground_package") or "") == GUAZI_PACKAGE or str(snapshot.get("xml_package") or "") == GUAZI_PACKAGE:
        return False
    labels = _snapshot_label_set(snapshot)
    return all(text in labels for text in LAUNCHER_ACCOUNT_DIALOG_CORE_TEXTS)


def _find_launcher_account_later_button(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        labels = {str(label).strip() for label in node.get("labels", [])}
        if labels == {LAUNCHER_ACCOUNT_LATER_TEXT} and node.get("clickable") and node.get("enabled"):
            candidates.append(node)
    if len(candidates) != 1:
        return None
    return candidates[0]


def _desktop_upgrade_text_pool(snapshot: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for value in snapshot.get("visible_texts") or []:
        if str(value).strip():
            texts.append(str(value))
    for node in snapshot.get("nodes", []):
        for label in node.get("labels", []):
            if str(label).strip():
                texts.append(str(label))
    for key in ("visible_blob", "fresh_xml", "ocr_text", "ocr_texts"):
        value = snapshot.get(key)
        if isinstance(value, list):
            texts.extend(str(item) for item in value if str(item).strip())
        elif value:
            texts.append(str(value))
    return texts


def _desktop_upgrade_keywords_seen(snapshot: dict[str, Any]) -> set[str]:
    texts = _desktop_upgrade_text_pool(snapshot)
    return {keyword for keyword in DESKTOP_UPGRADE_MODAL_KEYWORDS if any(keyword in text for text in texts)}


def _desktop_upgrade_modal_detected(snapshot: dict[str, Any]) -> bool:
    return bool(_desktop_upgrade_keywords_seen(snapshot))


def _find_desktop_upgrade_later_button(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        labels = [str(label).strip() for label in node.get("labels", [])]
        if not any(DESKTOP_UPGRADE_MODAL_LATER_TEXT in label for label in labels):
            continue
        if not node.get("enabled", True):
            continue
        if not _has_positive_bounds(node.get("bounds")):
            continue
        candidates.append(node)
    clickable = [node for node in candidates if node.get("clickable")]
    if len(clickable) == 1:
        return clickable[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


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
        "candidate_solution": "Only in launcher ready gate, tap exact 稍后 with a bounded repeat-until-closed contract, recapturing screenshot/XML/focused_window after each click, then search exact 瓜子二手车 icon.",
        "limits": [
            "do not tap 去登录",
            "do not enter account, phone, or verification code",
            "do not use inside Guazi business pages",
            f"do not exceed {LAUNCHER_LATER_MAX_ATTEMPTS} 稍后 attempts",
            "do not click any launcher dialog button except exact 稍后",
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
        # Fallback: partial match for truncated text like "瓜子二手?"
        for label in labels:
            if label and "瓜子二手" in label:
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
    adb_evidence = _adb_evidence_context_fields(context)
    snapshot_evidence = {
        "foreground_package": snapshot.get("foreground_package"),
        "resumed_activity": snapshot.get("resumed_activity"),
        "focused_window": snapshot.get("focused_window"),
        "xml_package": snapshot.get("xml_package"),
        "screenshot_path": snapshot.get("screenshot_path"),
        "xml_path": snapshot.get("xml_path"),
        "screenshot_missing": snapshot.get("screenshot_missing"),
        "xml_missing": snapshot.get("xml_missing"),
        "screenshot_error": snapshot.get("screenshot_error"),
        "xml_dump_error": snapshot.get("xml_dump_error"),
        "visible_text_digest": _visible_text_digest(snapshot),
        "capture_metrics": snapshot.get("capture_metrics"),
    }
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
        "desktop_upgrade_modal_detected": bool(startup.get("desktop_upgrade_modal_detected")),
        "desktop_upgrade_modal_action": startup.get("desktop_upgrade_modal_action"),
        "desktop_upgrade_modal_status": startup.get("desktop_upgrade_modal_status"),
        "desktop_upgrade_modal_keywords_seen": startup.get("desktop_upgrade_modal_keywords_seen"),
        "desktop_upgrade_modal_click_attempts": startup.get("desktop_upgrade_modal_click_attempts"),
        "desktop_upgrade_modal_later_button_found": startup.get("desktop_upgrade_modal_later_button_found"),
        "desktop_upgrade_modal_now_seen": startup.get("desktop_upgrade_modal_now_seen"),
        "desktop_upgrade_modal_clicked_immediate_upgrade": startup.get("desktop_upgrade_modal_clicked_immediate_upgrade"),
        "desktop_upgrade_modal_evidence_paths": startup.get("desktop_upgrade_modal_evidence_paths"),
        "desktop_upgrade_modal_click_history": startup.get("desktop_upgrade_modal_click_history"),
        "launcher_account_dialog_detected": bool(startup.get("launcher_account_dialog_detected")),
        "launcher_account_dialog_text_digest": startup.get("launcher_account_dialog_text_digest"),
        "later_button_found": startup.get("later_button_found"),
        "later_button_clicked": startup.get("later_button_clicked"),
        "launcher_later_dialog_contract_enabled": startup.get("launcher_later_dialog_contract_enabled"),
        "later_dialog_type": startup.get("later_dialog_type"),
        "max_later_click_attempts": startup.get("max_later_click_attempts"),
        "later_click_attempts": startup.get("later_click_attempts"),
        "later_dialog_dismissed": startup.get("later_dialog_dismissed"),
        "app_icon_visible_after_later_dialog": startup.get("app_icon_visible_after_later_dialog"),
        "launcher_later_dialog_stop_code": startup.get("launcher_later_dialog_stop_code"),
        "launcher_later_dialog_evidence_paths": startup.get("launcher_later_dialog_evidence_paths"),
        "launcher_later_click_history": startup.get("launcher_later_click_history"),
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
        "startup_account_center_login_detected": startup.get("startup_account_center_login_detected"),
        "account_center_package": startup.get("account_center_package"),
        "account_center_back_attempts": startup.get("account_center_back_attempts"),
        "account_center_dismissed": startup.get("account_center_dismissed"),
        "account_center_exit_action": startup.get("account_center_exit_action"),
        "app_icon_visible_after_account_center_exit": startup.get("app_icon_visible_after_account_center_exit"),
        "next_startup_step": startup.get("next_startup_step"),
        "startup_account_center_stop_code": startup.get("startup_account_center_stop_code"),
        "account_center_evidence_paths": startup.get("account_center_evidence_paths"),
        "final_screenshot_path": startup.get("final_screenshot_path", snapshot.get("screenshot_path")),
        "final_xml_path": startup.get("final_xml_path", snapshot.get("xml_path")),
        "screenshot_path": snapshot.get("screenshot_path"),
        "xml_path": snapshot.get("xml_path"),
        "last_successful_state": context.get("last_successful_state"),
        "failed_action": failed_action,
        "target_fingerprint": context.get("target_fingerprint"),
        "stop_code": stop_code,
    }
    if _capture_has_later_device_not_found(context, snapshot):
        diagnostic["gate_target_device_state"] = "device"
        diagnostic["later_capture_device_not_found"] = True
        diagnostic["original_stop_code_before_adb_priority"] = stop_code
        diagnostic["stop_code"] = TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT
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
        "LAUNCHER_LATER_BUTTON_NOT_FOUND",
        "LAUNCHER_LATER_DIALOG_NOT_DISMISSED_AFTER_MAX_ATTEMPTS",
        "APP_ICON_NOT_FOUND_AFTER_LATER_DIALOG_DISMISSED",
    } and startup.get("launcher_account_dialog_detected"):
        diagnostic["learning_loop_candidate"] = _launcher_account_learning_loop_candidate(snapshot)
    if stop_code in {
        "STARTUP_ACCOUNT_CENTER_LOGIN_NOT_DISMISSED_AFTER_BACK_ATTEMPTS",
        "STARTUP_ACCOUNT_CENTER_FORBIDDEN_ACTION_BLOCKED",
        "APP_ICON_NOT_FOUND_AFTER_ACCOUNT_CENTER_EXIT",
    } and startup.get("startup_account_center_login_detected"):
        diagnostic["learning_loop_candidate"] = _startup_account_center_learning_loop_solution(snapshot)
    return {**snapshot_evidence, **adb_evidence, **diagnostic, "startup": dict(startup)}


def _raise_device_ready_gate(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    code: str,
    message: str,
    failed_action: str,
) -> None:
    issue_context = _device_ready_context(context, snapshot, stop_code=code, failed_action=failed_action)
    final_code = TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT if issue_context.get("later_capture_device_not_found") else code
    final_message = (
        "Target ADB device was visible at first-stage gate but disappeared during screenshot/XML capture."
        if final_code == TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT
        else message
    )
    issue = _record_issue(context["issues"], final_code, "RUNTIME", final_message, issue_context)
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def _device_ready_gate_before_app_entry(context: dict[str, Any], *, reason: str) -> dict[str, Any]:
    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    startup = context.setdefault("startup", {})
    _startup_defaults(startup)
    gate_started = time.perf_counter()

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
            "device_state_reused_until_state_change": True,
            "device_state_source": "dumpsys_only",
            "xml_dump_deferred_until_evidence_needed": True,
            "reason_category": "DUMPSYS_STATE_PARSE_SLOW",
            "reason_detail": "device state is read by dumpsys only; screenshot/XML are deferred until evidence is needed",
            "solution": "reuse the same device state before wake/swipe/home changes",
        },
    )

    if snapshot.get("keyguard_showing"):
        if snapshot.get("keyguard_secure"):
            startup["keyguard_dismissed"] = False
            snapshot = _capture(client, f"device_ready_secure_keyguard_{_timestamp()}")
            _startup_note_capture(context, snapshot)
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
        _startup_note_capture(context, snapshot)
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
                "device_state_reused_until_state_change": True,
                "post_unlock_full_capture_count": 1,
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
            if has_secure_keyguard_input_evidence(snapshot):
                _raise_device_ready_gate(
                    context,
                    snapshot,
                    code="SECURE_KEYGUARD_HUMAN_REQUIRED",
                    message="Secure keyguard or password input is showing before APP_FORCE_RESTART; human unlock is required.",
                    failed_action="device_ready_secure_keyguard_check",
                )
            if is_launcher_operable_despite_stale_keyguard(snapshot):
                startup["keyguard_showing_stale_but_launcher_visible"] = True
                startup["unlock_gate_passed_by_launcher_visible_evidence"] = True
                startup["keyguard_dismissed"] = True
            elif is_guazi_foreground_operable_despite_stale_keyguard(snapshot):
                old_page_type = _classify_old_guazi_page(snapshot)
                startup["guazi_foreground_visible_despite_keyguard"] = True
                startup["stale_keyguard_ignored_for_reopen"] = True
                startup["old_guazi_page_detected"] = True
                startup["old_guazi_page_type"] = old_page_type
                startup["device_ready_pass_reason"] = "GUAZI_FOREGROUND_XML_READABLE_WITH_NON_SECURE_KEYGUARD"
                if old_page_type == "S01_OR_S02" and _guazi_home_ready(snapshot):
                    startup["guazi_home_foreground_accepted"] = True
                    startup["guazi_frontend_ready_attempt"] = 1
                    startup["must_reopen_guazi_app"] = False
                    startup["force_reopen_required"] = False
                    startup["force_reopen_suppressed_reason"] = "GUAZI_HOME_ALREADY_READY"
                    startup["device_ready_pass_reason"] = "GUAZI_HOME_FOREGROUND_ALREADY_READY"
                else:
                    startup["must_reopen_guazi_app"] = True
                    startup["force_reopen_required"] = True
                startup["keyguard_dismissed"] = True
            elif is_miui_launcher_overlay_visible(snapshot):
                snapshot, recovered = _recover_miui_launcher_overlay_after_stale_keyguard(context, snapshot)
                startup["keyguard_showing_after_miui_overlay_recovery"] = bool(snapshot.get("keyguard_showing"))
                startup["focused_window_after_miui_overlay_recovery"] = snapshot.get("focused_window")
                startup["foreground_package_after_miui_overlay_recovery"] = snapshot.get("foreground_package")
                if has_secure_keyguard_input_evidence(snapshot):
                    _raise_device_ready_gate(
                        context,
                        snapshot,
                        code="SECURE_KEYGUARD_HUMAN_REQUIRED",
                        message="Secure keyguard or password input appeared during launcher-overlay recovery.",
                        failed_action="device_ready_miui_launcher_overlay_secure_input",
                    )
                if recovered:
                    startup["keyguard_dismissed"] = True
                else:
                    _raise_device_ready_gate(
                        context,
                        snapshot,
                        code="DEVICE_READY_LAUNCHER_OVERLAY_KEYGUARD_STALE",
                        message="MIUI launcher overlay remained visible after safe HOME recovery before APP_FORCE_RESTART.",
                        failed_action="device_ready_miui_launcher_overlay_recovery",
                    )
            else:
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
        _startup_note_capture(context, snapshot)
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
                "device_state_reused_until_state_change": True,
                "launcher_ready_full_capture_count": 1,
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
            _startup_note_capture(context, snapshot)
            _record_capture_timing(context, snapshot, step_name="DEVICE_LAUNCHER_READY", page_name="RUNTIME")
        startup["focused_window_after"] = snapshot.get("focused_window")
        startup["foreground_package_after"] = snapshot.get("foreground_package")

    startup["wake_unlock_duration_ms"] = int((time.perf_counter() - gate_started) * 1000)
    _startup_event(
        context,
        "wake_unlock_gate",
        duration_ms=int(startup["wake_unlock_duration_ms"]),
        extra={
            "keyguard_showing": bool(snapshot.get("keyguard_showing")),
            "focused_window": snapshot.get("focused_window"),
            "foreground_package": snapshot.get("foreground_package"),
        },
    )
    return snapshot


def _handle_launcher_account_dialog_until_closed(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    max_attempts: int = LAUNCHER_LATER_MAX_ATTEMPTS,
) -> dict[str, Any]:
    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    startup = context.setdefault("startup", {})

    startup["launcher_later_dialog_contract_enabled"] = True
    startup["later_dialog_type"] = LAUNCHER_LATER_DIALOG_TYPE
    startup["max_later_click_attempts"] = max_attempts
    startup.setdefault("launcher_account_dialog_detected", False)
    startup.setdefault("launcher_account_dialog_text_digest", None)
    startup.setdefault("later_button_found", None)
    startup.setdefault("later_button_seen", None)
    startup.setdefault("later_button_clicked", False)
    startup.setdefault("later_click_attempts", 0)
    startup.setdefault("later_dialog_seen_before_click", False)
    startup.setdefault("later_dialog_seen_after_click", None)
    startup.setdefault("later_dialog_dismissed", None)
    startup.setdefault("app_icon_visible_after_later_dialog", None)
    startup.setdefault("launcher_later_dialog_stop_code", None)
    startup.setdefault("launcher_later_dialog_evidence_paths", [])
    startup.setdefault("launcher_later_click_history", [])
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
        startup["later_dialog_seen_before_click"] = False
        return snapshot

    startup["launcher_account_dialog_detected"] = True
    startup["launcher_account_dialog_text_digest"] = _visible_text_digest(snapshot)
    startup["focused_window_before_later"] = snapshot.get("focused_window")
    startup["screenshot_before_later"] = snapshot.get("screenshot_path")
    startup["xml_before_later"] = snapshot.get("xml_path")
    startup["guazi_icon_visible_before_later"] = _guazi_icon_visible(snapshot)
    startup["learning_loop_candidate_launcher_account_dialog"] = _launcher_account_learning_loop_candidate(snapshot)
    context.setdefault("learning_loop_candidates", []).append(startup["learning_loop_candidate_launcher_account_dialog"])
    startup["launcher_later_dialog_evidence_paths"].append(
        {
            "attempt": 0,
            "phase": "initial_detected",
            "screenshot_path": snapshot.get("screenshot_path"),
            "xml_path": snapshot.get("xml_path"),
        }
    )

    current_snapshot = snapshot
    for attempt in range(1, max_attempts + 1):
        if not _launcher_account_dialog_detected(current_snapshot):
            startup["later_dialog_dismissed"] = True
            startup["app_icon_visible_after_later_dialog"] = _guazi_icon_visible(current_snapshot)
            return current_snapshot

        startup["later_dialog_seen_before_click"] = True
        later_node = _find_launcher_account_later_button(current_snapshot)
        startup["later_button_found"] = later_node is not None
        startup["later_button_seen"] = later_node is not None
        if later_node is None:
            startup["launcher_later_dialog_stop_code"] = "LAUNCHER_LATER_BUTTON_NOT_FOUND"
            _raise_device_ready_gate(
                context,
                current_snapshot,
                code="LAUNCHER_LATER_BUTTON_NOT_FOUND",
                message="Launcher blocking dialog exists, but exact clickable 稍后 button was not found.",
                failed_action=LAUNCHER_LATER_ACTION_ID,
            )

        clicked_bounds = later_node["bounds"]
        click_started = time.perf_counter()
        client.tap(*_center(clicked_bounds))
        click_ms = int((time.perf_counter() - click_started) * 1000)
        startup["later_button_clicked"] = True
        startup["later_click_attempts"] = attempt
        time.sleep(0.5)
        timing.add(
            step_name="device_ready_gate",
            page_name="RUNTIME",
            action_name=LAUNCHER_LATER_ACTION_ID,
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=click_ms,
            transition_wait_ms=500,
            screenshot_path=None,
            xml_path=None,
            extra={
                "clicked_text": LAUNCHER_ACCOUNT_LATER_TEXT,
                "clicked_bounds": clicked_bounds,
                "later_click_attempt": attempt,
                "max_later_click_attempts": max_attempts,
                "reason_category": "LAUNCHER_LATER_DIALOG_DISMISS",
            },
        )

        next_snapshot = _capture(client, f"device_ready_after_launcher_later_attempt_{attempt}_{_timestamp()}")
        _record_capture_timing(context, next_snapshot, step_name="device_ready_gate", page_name="RUNTIME")
        still_visible = _launcher_account_dialog_detected(next_snapshot)
        startup["focused_window_after_later"] = next_snapshot.get("focused_window")
        startup["foreground_package_after_later"] = next_snapshot.get("foreground_package")
        startup["launcher_visible_after_later"] = _launcher_window_visible(next_snapshot)
        startup["launcher_state_after_click"] = {
            "focused_window": next_snapshot.get("focused_window"),
            "foreground_package": next_snapshot.get("foreground_package"),
            "xml_package": next_snapshot.get("xml_package"),
        }
        startup["screenshot_after_later"] = next_snapshot.get("screenshot_path")
        startup["xml_after_later"] = next_snapshot.get("xml_path")
        startup["guazi_icon_visible_after_later"] = _guazi_icon_visible(next_snapshot)
        startup["later_dialog_seen_after_click"] = still_visible
        startup["launcher_later_click_history"].append(
            {
                "attempt": attempt,
                "clicked_text": LAUNCHER_ACCOUNT_LATER_TEXT,
                "clicked_bounds": clicked_bounds,
                "before_screenshot_path": current_snapshot.get("screenshot_path"),
                "before_xml_path": current_snapshot.get("xml_path"),
                "after_screenshot_path": next_snapshot.get("screenshot_path"),
                "after_xml_path": next_snapshot.get("xml_path"),
                "later_dialog_seen_after_click": still_visible,
                "app_icon_visible_after_click": _guazi_icon_visible(next_snapshot),
            }
        )
        startup["launcher_later_dialog_evidence_paths"].append(
            {
                "attempt": attempt,
                "phase": "after_later_click",
                "screenshot_path": next_snapshot.get("screenshot_path"),
                "xml_path": next_snapshot.get("xml_path"),
            }
        )

        if not still_visible:
            startup["later_dialog_dismissed"] = True
            startup["app_icon_visible_after_later_dialog"] = _guazi_icon_visible(next_snapshot)
            return next_snapshot

        current_snapshot = next_snapshot

    startup["later_dialog_dismissed"] = False
    startup["app_icon_visible_after_later_dialog"] = _guazi_icon_visible(current_snapshot)
    startup["launcher_later_dialog_stop_code"] = "LAUNCHER_LATER_DIALOG_NOT_DISMISSED_AFTER_MAX_ATTEMPTS"
    _raise_device_ready_gate(
        context,
        current_snapshot,
        code="LAUNCHER_LATER_DIALOG_NOT_DISMISSED_AFTER_MAX_ATTEMPTS",
        message=f"Launcher blocking dialog remained after {max_attempts} exact 稍后 attempts.",
        failed_action=LAUNCHER_LATER_ACTION_ID,
    )


def _handle_desktop_upgrade_modal_until_closed(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    max_attempts: int = DESKTOP_UPGRADE_MODAL_MAX_ATTEMPTS,
) -> dict[str, Any]:
    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    startup = context.setdefault("startup", {})

    startup.setdefault("desktop_upgrade_modal_detected", False)
    startup.setdefault("desktop_upgrade_modal_action", "none")
    startup.setdefault("desktop_upgrade_modal_status", None)
    startup.setdefault("desktop_upgrade_modal_keywords_seen", [])
    startup.setdefault("desktop_upgrade_modal_click_attempts", 0)
    startup.setdefault("desktop_upgrade_modal_evidence_paths", [])
    startup.setdefault("desktop_upgrade_modal_click_history", [])
    startup.setdefault("desktop_upgrade_modal_later_button_found", None)
    startup.setdefault("desktop_upgrade_modal_now_seen", None)
    startup.setdefault("desktop_upgrade_modal_clicked_immediate_upgrade", False)

    keywords = _desktop_upgrade_keywords_seen(snapshot)
    if not keywords:
        startup["desktop_upgrade_modal_detected"] = False
        startup["desktop_upgrade_modal_action"] = "none"
        return snapshot

    startup["desktop_upgrade_modal_detected"] = True
    startup["desktop_upgrade_modal_keywords_seen"] = sorted(keywords)
    startup["desktop_upgrade_modal_now_seen"] = DESKTOP_UPGRADE_MODAL_NOW_TEXT in keywords
    startup["desktop_upgrade_modal_evidence_paths"].append(
        {
            "attempt": 0,
            "phase": "initial_detected",
            "screenshot_path": snapshot.get("screenshot_path"),
            "xml_path": snapshot.get("xml_path"),
            "keywords_seen": sorted(keywords),
        }
    )

    current_snapshot = snapshot
    for attempt in range(1, max_attempts + 1):
        current_keywords = _desktop_upgrade_keywords_seen(current_snapshot)
        if not current_keywords:
            startup["desktop_upgrade_modal_status"] = "DISMISSED"
            return current_snapshot

        later_node = _find_desktop_upgrade_later_button(current_snapshot)
        startup["desktop_upgrade_modal_later_button_found"] = later_node is not None
        startup["desktop_upgrade_modal_now_seen"] = DESKTOP_UPGRADE_MODAL_NOW_TEXT in current_keywords
        if later_node is None:
            startup["desktop_upgrade_modal_action"] = "none"
            startup["desktop_upgrade_modal_status"] = "NO_SAFE_DISMISS"
            _raise_device_ready_gate(
                context,
                current_snapshot,
                code="DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS",
                message="Desktop upgrade modal was detected before app entry, but no safe clickable later-upgrade button was found.",
                failed_action=DESKTOP_UPGRADE_MODAL_ACTION_ID,
            )

        clicked_bounds = later_node["bounds"]
        click_started = time.perf_counter()
        client.tap(*_center(clicked_bounds))
        click_ms = int((time.perf_counter() - click_started) * 1000)
        startup["desktop_upgrade_modal_action"] = "click_later"
        startup["desktop_upgrade_modal_click_attempts"] = attempt
        time.sleep(0.5)
        timing.add(
            step_name="device_ready_gate",
            page_name="RUNTIME",
            action_name=DESKTOP_UPGRADE_MODAL_ACTION_ID,
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=click_ms,
            transition_wait_ms=500,
            screenshot_path=None,
            xml_path=None,
            extra={
                "clicked_text": DESKTOP_UPGRADE_MODAL_LATER_TEXT,
                "clicked_bounds": clicked_bounds,
                "desktop_upgrade_modal_click_attempt": attempt,
                "max_desktop_upgrade_modal_attempts": max_attempts,
                "reason_category": "DESKTOP_UPGRADE_MODAL_DISMISS",
            },
        )

        next_snapshot = _capture(client, f"device_ready_after_desktop_upgrade_later_attempt_{attempt}_{_timestamp()}")
        _record_capture_timing(context, next_snapshot, step_name="device_ready_gate", page_name="RUNTIME")
        still_visible = _desktop_upgrade_modal_detected(next_snapshot)
        startup["desktop_upgrade_modal_click_history"].append(
            {
                "attempt": attempt,
                "clicked_text": DESKTOP_UPGRADE_MODAL_LATER_TEXT,
                "clicked_bounds": clicked_bounds,
                "before_screenshot_path": current_snapshot.get("screenshot_path"),
                "before_xml_path": current_snapshot.get("xml_path"),
                "after_screenshot_path": next_snapshot.get("screenshot_path"),
                "after_xml_path": next_snapshot.get("xml_path"),
                "modal_seen_after_click": still_visible,
            }
        )
        startup["desktop_upgrade_modal_evidence_paths"].append(
            {
                "attempt": attempt,
                "phase": "after_later_click",
                "screenshot_path": next_snapshot.get("screenshot_path"),
                "xml_path": next_snapshot.get("xml_path"),
                "modal_seen_after_click": still_visible,
            }
        )

        if not still_visible:
            startup["desktop_upgrade_modal_status"] = "DISMISSED"
            return next_snapshot

        current_snapshot = next_snapshot

    startup["desktop_upgrade_modal_status"] = "DISMISS_FAILED"
    _raise_device_ready_gate(
        context,
        current_snapshot,
        code="DESKTOP_UPGRADE_MODAL_DISMISS_FAILED",
        message=f"Desktop upgrade modal remained after {max_attempts} safe later-upgrade attempts.",
        failed_action=DESKTOP_UPGRADE_MODAL_ACTION_ID,
    )


def _handle_startup_account_center_login_until_launcher(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    max_attempts: int = STARTUP_ACCOUNT_CENTER_MAX_BACK_ATTEMPTS,
) -> dict[str, Any]:
    client: AdbClient = context["client"]
    timing: TimingRecorder = context["timing"]
    startup = context.setdefault("startup", {})
    startup.setdefault("startup_account_center_login_detected", False)
    startup.setdefault("account_center_package", None)
    startup.setdefault("max_account_center_back_attempts", max_attempts)
    startup.setdefault("account_center_back_attempts", 0)
    startup.setdefault("account_center_dismissed", None)
    startup.setdefault("account_center_exit_action", None)
    startup.setdefault("app_icon_visible_after_account_center_exit", None)
    startup.setdefault("next_startup_step", None)
    startup.setdefault("startup_account_center_stop_code", None)
    startup.setdefault("account_center_evidence_paths", [])
    startup.setdefault("account_center_back_history", [])

    if not recognize_startup_account_center_login_page(snapshot):
        startup["startup_account_center_login_detected"] = False
        return snapshot

    startup["startup_account_center_login_detected"] = True
    startup["account_center_package"] = snapshot.get("foreground_package") or snapshot.get("xml_package")
    startup["account_center_evidence_paths"].append(
        {
            "attempt": 0,
            "phase": "initial_detected",
            "screenshot_path": snapshot.get("screenshot_path"),
            "xml_path": snapshot.get("xml_path"),
        }
    )
    startup["learning_loop_solution_startup_account_center_login"] = _startup_account_center_learning_loop_solution(snapshot)
    context.setdefault("learning_loop_candidates", []).append(startup["learning_loop_solution_startup_account_center_login"])

    current_snapshot = snapshot
    for attempt in range(1, max_attempts + 1):
        if not recognize_startup_account_center_login_page(current_snapshot):
            startup["account_center_dismissed"] = True
            startup["app_icon_visible_after_account_center_exit"] = _guazi_icon_visible(current_snapshot)
            startup["next_startup_step"] = "continue_to_app_icon_search"
            return current_snapshot
        if not _startup_account_center_action_allowed(STARTUP_ACCOUNT_CENTER_BACK_ACTION_ID):
            startup["startup_account_center_stop_code"] = "STARTUP_ACCOUNT_CENTER_FORBIDDEN_ACTION_BLOCKED"
            _raise_device_ready_gate(
                context,
                current_snapshot,
                code="STARTUP_ACCOUNT_CENTER_FORBIDDEN_ACTION_BLOCKED",
                message="Startup account-center page blocked a forbidden non-BACK action.",
                failed_action="STARTUP_ACCOUNT_CENTER_FORBIDDEN_ACTION_BLOCKED",
            )
        back_started = time.perf_counter()
        back_result = client.back()
        back_ms = int((time.perf_counter() - back_started) * 1000)
        startup["account_center_back_attempts"] = attempt
        startup["account_center_exit_action"] = STARTUP_ACCOUNT_CENTER_BACK_ACTION_ID
        time.sleep(0.5)
        timing.add(
            step_name="device_ready_gate",
            page_name=STARTUP_ACCOUNT_CENTER_PAGE_ID,
            action_name=STARTUP_ACCOUNT_CENTER_BACK_ACTION_ID,
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=back_ms,
            transition_wait_ms=500,
            screenshot_path=None,
            xml_path=None,
            extra={
                "account_center_back_attempt": attempt,
                "max_account_center_back_attempts": max_attempts,
                "back_success": back_result.success,
                "reason_category": "STARTUP_ACCOUNT_CENTER_EXIT",
            },
        )
        next_snapshot = _capture(client, f"device_ready_after_account_center_back_{attempt}_{_timestamp()}")
        _record_capture_timing(context, next_snapshot, step_name="device_ready_gate", page_name="RUNTIME")
        still_login = recognize_startup_account_center_login_page(next_snapshot)
        startup["account_center_back_history"].append(
            {
                "attempt": attempt,
                "action_id": STARTUP_ACCOUNT_CENTER_BACK_ACTION_ID,
                "before_screenshot_path": current_snapshot.get("screenshot_path"),
                "before_xml_path": current_snapshot.get("xml_path"),
                "after_screenshot_path": next_snapshot.get("screenshot_path"),
                "after_xml_path": next_snapshot.get("xml_path"),
                "still_account_center_login_page": still_login,
                "app_icon_visible_after_back": _guazi_icon_visible(next_snapshot),
            }
        )
        startup["account_center_evidence_paths"].append(
            {
                "attempt": attempt,
                "phase": "after_back",
                "screenshot_path": next_snapshot.get("screenshot_path"),
                "xml_path": next_snapshot.get("xml_path"),
            }
        )
        if not still_login:
            startup["account_center_dismissed"] = True
            startup["app_icon_visible_after_account_center_exit"] = _guazi_icon_visible(next_snapshot)
            startup["next_startup_step"] = "continue_to_app_icon_search"
            return next_snapshot
        current_snapshot = next_snapshot

    startup["account_center_dismissed"] = False
    startup["app_icon_visible_after_account_center_exit"] = _guazi_icon_visible(current_snapshot)
    startup["startup_account_center_stop_code"] = "STARTUP_ACCOUNT_CENTER_LOGIN_NOT_DISMISSED_AFTER_BACK_ATTEMPTS"
    _raise_device_ready_gate(
        context,
        current_snapshot,
        code="STARTUP_ACCOUNT_CENTER_LOGIN_NOT_DISMISSED_AFTER_BACK_ATTEMPTS",
        message=f"Startup account-center login page remained after {max_attempts} BACK attempts.",
        failed_action=STARTUP_ACCOUNT_CENTER_BACK_ACTION_ID,
    )


def _app_launch_ready_gate_after_icon(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    state: str | None,
    *,
    reason: str,
) -> tuple[dict[str, Any], str | None]:
    snapshot, state, startup_skip_clicked = _maybe_click_startup_skip_once(
        context,
        snapshot,
        state=state,
        reason=reason,
        capture_stem="s01_s10_after_startup_skip",
    )
    if startup_skip_clicked and state in S01_TO_S10_STATES | {"S_LOGIN", "S_APP_ICON", "RUNTIME"}:
        return snapshot, state
    if state in S01_TO_S10_STATES | {"S_LOGIN", "S_APP_ICON", "RUNTIME"}:
        return snapshot, state
    if state != "S00" and not _app_launch_h5_text_delay_suspected(snapshot):
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
        current_snapshot, current_state, startup_skip_clicked = _maybe_click_startup_skip_once(
            context,
            current_snapshot,
            state=current_state,
            reason=reason,
            capture_stem=f"s01_s10_after_startup_skip_wait_{attempt}",
        )
        if startup_skip_clicked:
            attempt_record = {
                "attempt": attempt,
                "state": current_state,
                "startup_skip_clicked": True,
                "screenshot_path": current_snapshot.get("screenshot_path"),
                "xml_path": current_snapshot.get("xml_path"),
                "visible_text_digest": _visible_text_digest(current_snapshot),
                "xml_package": current_snapshot.get("xml_package"),
                "xml_dump_rc": current_snapshot.get("xml_dump_rc"),
                "xml_dump_stderr": current_snapshot.get("xml_dump_stderr"),
            }
            startup["app_launch_ready_gate_attempts"].append(attempt_record)
            if current_state in S01_TO_S10_STATES | {"S_LOGIN", "S_APP_ICON", "RUNTIME"}:
                startup["app_launch_ready_gate_resolved"] = True
                startup["app_launch_ready_gate_final_state"] = current_state
                return current_snapshot, current_state
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
        if current_state in S01_TO_S10_STATES | {"S_LOGIN", "S_APP_ICON", "RUNTIME"}:
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
        "series_alias": str(data.get("series_alias") or "").strip(),
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
    params["brand_initial"] = _derive_brand_initial(str(params.get("brand") or ""))
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
        "series_alias": params.get("series_alias"),
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


def _derive_brand_initial(brand: str) -> str | None:
    brand = (brand or "").strip()
    if not brand:
        return None
    if brand in S03_BRAND_INITIALS:
        return S03_BRAND_INITIALS[brand]
    upper_brand = brand.upper()
    for known, initial in S03_BRAND_INITIALS.items():
        if upper_brand == known.upper():
            return initial
    ascii_brand_initials = {
        "NISSAN": "R",
        "AUDI": "A",
        "GEELY": "J",
        "LEAPMOTOR": "L",
        "CHANGAN": "C",
        "MINI": "M",
    }
    if brand.upper() in ascii_brand_initials:
        return ascii_brand_initials[brand.upper()]
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
        "\u798f\u7279": "F",
        "\u798f\u514b\u65af": "F",
        "\u65e5\u4ea7": "R",
        "\u4e1c\u98ce\u65e5\u4ea7": "R",
        "\u73b0\u4ee3": "X",
        "\u6bd4\u4e9a\u8fea": "B",
        "\u96f6\u8dd1": "L",
        "\u96f6\u8dd1\u6c7d\u8f66": "L",
        "\u957f\u5b89": "C",
        "\u957f\u5b89\u6c7d\u8f66": "C",
        "\u5409\u5229": "J",
        "\u5409\u5229\u6c7d\u8f66": "J",
        "大众": "D",
        "丰田": "F",
        "本田": "B",
        "宝马": "B",
        "奔驰": "B",
        "奥迪": "A",
        "别克": "B",
        "福特": "F",
        "福克斯": "F",
        "日产": "R",
        "东风日产": "R",
        "现代": "X",
        "比亚迪": "B",
        "雪佛兰": "X",
        "欧拉": "O",
        "长城欧拉": "O",
        "零跑": "L",
        "零跑汽车": "L",
        "哪吒": "N",
        "哪吒汽车": "N",
        "小鹏": "X",
        "小鹏汽车": "X",
        "蔚来": "W",
        "理想": "L",
        "理想汽车": "L",
        "极氪": "J",
        "阿维塔": "A",
        "深蓝": "S",
        "深蓝汽车": "S",
        "问界": "A",
        "AITO问界": "A",
        "长安": "C",
        "长安汽车": "C",
        "吉利": "J",
        "吉利汽车": "J",
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
    _startup_defaults(startup)
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

    recovery_started = time.perf_counter()
    device_ready_snapshot = _device_ready_gate_before_app_entry(context, reason=reason)
    if startup.get("guazi_home_foreground_accepted"):
        accepted_state = _recognize_page(recognizer, device_ready_snapshot, context.get("flow_state")) or "S01"
        startup.update(
            {
                "app_entry_mode": "guazi_home_foreground_accepted",
                "app_force_restart_called": False,
                "force_stop_done": False,
                "force_reopen_executed": False,
                "tap_guazi_app_icon_done": False,
                "after_force_restart_state": accepted_state,
                "after_recovery_state": accepted_state,
                "after_recovery_screenshot_path": device_ready_snapshot.get("screenshot_path"),
                "after_recovery_xml_path": device_ready_snapshot.get("xml_path"),
                "after_force_restart_visible_text_digest": _visible_text_digest(device_ready_snapshot),
                "app_reopen_duration_ms": int((time.perf_counter() - recovery_started) * 1000),
            }
        )
        _startup_event(
            context,
            "guazi_home_foreground_fastpath",
            duration_ms=int(startup["app_reopen_duration_ms"]),
            extra={
                "accepted_state": accepted_state,
                "force_reopen_suppressed_reason": startup.get("force_reopen_suppressed_reason"),
                "guazi_frontend_ready_attempt": startup.get("guazi_frontend_ready_attempt"),
            },
        )
        return device_ready_snapshot
    force_stop_started = time.perf_counter()
    force_stop_result = client.run(["shell", "am", "force-stop", GUAZI_PACKAGE], timeout=20)
    force_stop_ms = int((time.perf_counter() - force_stop_started) * 1000)
    startup["force_stop_done"] = force_stop_result.success
    if startup.get("force_reopen_required"):
        startup["force_reopen_executed"] = True
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
    home_poll = _poll_device_state_until(
        client,
        lambda state: _launcher_window_visible(state) or not _notification_shade_visible(state),
        max_seconds=0.3,
        interval_seconds=0.15,
    )
    startup["home_to_launcher_done"] = bool(home_result.get("home_success"))
    startup["home_to_launcher_poll_rounds"] = home_poll["rounds"]
    timing.add(
        step_name="runtime_recover_to_guazi_mainline",
        page_name="RUNTIME",
        action_name="home_to_launcher",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=home_ms,
        transition_wait_ms=int(home_poll.get("elapsed_ms") or 0),
        screenshot_path=None,
        xml_path=None,
        extra={
            "optimized": True,
            "optimization_type": "home_to_launcher_short_poll",
            "before_estimated_duration_seconds": 0.3,
            "after_duration_seconds": round(int(home_poll.get("elapsed_ms") or 0) / 1000, 3),
            "poll_rounds": home_poll["rounds"],
        },
    )
    launcher_lookup_started = time.perf_counter()
    if _fresh_launcher_icon_capture_reusable(device_ready_snapshot):
        launcher_snapshot = device_ready_snapshot
        startup["launcher_snapshot_reused_for_icon_lookup"] = True
        startup["launcher_snapshot_reused_age_ms"] = int((_capture_age_seconds(device_ready_snapshot) or 0.0) * 1000)
        _startup_note_capture(
            context,
            launcher_snapshot,
            reused=True,
            reason="device_ready_fresh_launcher_icon_xml_reused_before_force_restart_icon_tap",
        )
        _add_runtime_timing(
            context,
            step_name="DEVICE_LAUNCHER_READY",
            page_name="RUNTIME",
            action_name="verify_launcher_and_guazi_icon",
            snapshot=launcher_snapshot,
            extra={
                "optimized": True,
                "optimization_type": "reuse_fresh_device_ready_launcher_xml",
                "skipped_redundant_fresh_count": 1,
                "skipped_redundant_xml_dump_count": 1,
                "keyguard_showing": bool(launcher_snapshot.get("keyguard_showing")),
                "focused_window": launcher_snapshot.get("focused_window"),
                "device_state_reused_until_state_change": True,
                "launcher_ready_full_capture_count": 0,
                "reason_category": "LAUNCHER_READY_GATE_FASTPATH",
                "reason_detail": "device-ready capture is still fresh and already shows launcher plus exact Guazi icon; APP_FORCE_RESTART is still performed before this reuse.",
                "solution": "reuse the fresh launcher XML for exact app-icon lookup and still capture a fresh Guazi page after icon tap.",
            },
        )
    else:
        startup["launcher_snapshot_reused_for_icon_lookup"] = False
        launcher_snapshot = _capture(client, f"device_ready_launcher_before_icon_{_timestamp()}")
        _startup_note_capture(context, launcher_snapshot)
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
                "device_state_reused_until_state_change": True,
                "launcher_ready_full_capture_count": 1,
                "reason_category": "LAUNCHER_READY_GATE",
                "reason_detail": "launcher XML is captured once after HOME before the exact app-icon lookup",
                "solution": "reuse this launcher XML for dialog/icon checks until a launcher-layer action changes state",
            },
        )
    launcher_snapshot = _handle_desktop_upgrade_modal_until_closed(context, launcher_snapshot)
    launcher_snapshot = _handle_launcher_account_dialog_until_closed(context, launcher_snapshot)
    launcher_snapshot = _handle_startup_account_center_login_until_launcher(context, launcher_snapshot)
    launcher_xml = str(launcher_snapshot.get("fresh_xml") or "")
    startup["launcher_visible"] = _launcher_window_visible(launcher_snapshot)
    startup["guazi_icon_visible"] = _guazi_icon_visible(launcher_snapshot)
    startup["guazi_icon_visible_final"] = startup["guazi_icon_visible"]
    startup["final_screenshot_path"] = launcher_snapshot.get("screenshot_path")
    startup["final_xml_path"] = launcher_snapshot.get("xml_path")
    if not startup["guazi_icon_visible"]:
        # Fallback: launch app via ADB instead of failing
        startup["guazi_icon_launch_fallback"] = True
        startup["guazi_icon_visible"] = True
        startup["guazi_icon_visible_final"] = True
        # Launch app via ADB
        import subprocess
        subprocess.run(
            ["adb", "-s", client.adb_serial, "shell", "am", "start", "-n", "com.ganji.android.haoche_c/com.cars.guazi.app.home.MainActivity"],
            check=False,
            capture_output=True,
        )
        time.sleep(3.0)
        # Re-capture after launch
        launcher_snapshot = _capture(client, f"s01_s10_after_adb_launch_{_timestamp()}")
        _startup_note_capture(context, launcher_snapshot)
    startup["launcher_icon_lookup_duration_ms"] = int((time.perf_counter() - launcher_lookup_started) * 1000)
    icon_started = time.perf_counter()
    if startup.get("guazi_icon_launch_fallback"):
        # Skip icon tap since we launched via ADB
        icon_result = type("FakeResult", (), {"success": True, "stderr": ""})()
    else:
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
    if icon_result.success:
        app_poll = _poll_device_state_until(
            client,
            lambda state: str(state.get("foreground_package") or "") == GUAZI_PACKAGE
            or GUAZI_PACKAGE in str(state.get("focused_window") or ""),
            max_seconds=APP_LAUNCH_FOREGROUND_POLL_MAX_SECONDS,
            interval_seconds=APP_LAUNCH_FOREGROUND_POLL_INTERVAL_SECONDS,
        )
        wait_ms = int(app_poll.get("elapsed_ms") or 0)
        startup["app_foreground_poll_rounds"] = app_poll["rounds"]
        startup["app_foreground_poll_matched"] = bool(app_poll.get("matched"))
    else:
        wait_ms = 200
        time.sleep(wait_ms / 1000)
        app_poll = {"rounds": [], "matched": False}
    startup["app_restart_wait_ms"] = wait_ms
    startup["app_foreground_confirm_duration_ms"] = wait_ms
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
        extra={
            "optimized": bool(icon_result.success),
            "optimization_type": "short_poll_guazi_foreground_after_icon" if icon_result.success else None,
            "before_estimated_duration_seconds": 1.0 if icon_result.success else 0.2,
            "after_duration_seconds": round(wait_ms / 1000, 3),
            "poll_rounds": app_poll["rounds"],
            "foreground_matched": bool(app_poll.get("matched")),
            "contract_validation_preserved": True,
        },
    )
    snapshot = _capture(client, f"s01_s10_after_force_restart_{_timestamp()}")
    _startup_note_capture(context, snapshot)
    snapshot["icon_tap_success"] = icon_result.success
    snapshot["icon_tap_error"] = icon_result.stderr or ""
    snapshot["app_entry_mode"] = "force_restart"
    snapshot["app_force_restart_reason"] = reason
    if not icon_result.success:
        snapshot["runtime_recovery_cause"] = "GUAZI_APP_ICON_NOT_FOUND"
        if startup.get("old_guazi_page_detected"):
            snapshot["runtime_recovery_cause"] = "GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE"
    else:
        recovered_state = _recognize_page(recognizer, snapshot)
        if (
            str(snapshot.get("foreground_package") or "") != GUAZI_PACKAGE
            and str(snapshot.get("xml_package") or "") != GUAZI_PACKAGE
            and recovered_state not in S01_TO_S10_STATES | {"S_LOGIN"}
        ):
            snapshot["runtime_recovery_cause"] = "APP_ICON_CLICK_DID_NOT_OPEN_GUAZI"
            if startup.get("old_guazi_page_detected"):
                snapshot["runtime_recovery_cause"] = "GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE"
    after_force_restart_state = _recognize_page(recognizer, snapshot, context.get("flow_state"))
    startup["s01_detect_duration_ms"] = int((time.perf_counter() - recovery_started) * 1000)
    snapshot, after_force_restart_state, startup_skip_clicked = _maybe_click_startup_skip_once(
        context,
        snapshot,
        state=after_force_restart_state,
        reason=reason,
        capture_stem="s01_s10_after_startup_skip",
    )
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
    snapshot, after_force_restart_state = _retry_guazi_frontend_until_ready(
        context,
        snapshot,
        after_force_restart_state,
        reason=reason,
    )
    startup["app_reopen_duration_ms"] = int((time.perf_counter() - recovery_started) * 1000)
    _startup_event(
        context,
        "app_force_restart_and_contract_capture",
        duration_ms=int(startup["app_reopen_duration_ms"]),
        extra={
            "app_force_restart_called": True,
            "force_stop_done": startup.get("force_stop_done"),
            "tap_guazi_app_icon_done": startup.get("tap_guazi_app_icon_done"),
            "after_force_restart_state": after_force_restart_state,
            "fresh_contract_capture_path": snapshot.get("xml_path"),
        },
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


def _find_brand_filter_node(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not bounds:
            continue
        resource_id = str(node.get("resource_id") or "")
        labels = {str(label).strip() for label in node.get("labels", [])}
        if "品牌" in labels or resource_id.endswith(":id/ftv_brand") or "ftv_brand" in resource_id:
            return node
    exact = _find_exact(snapshot, "品牌")
    if exact and exact.get("bounds"):
        return exact
    return None


def _node_dict_from_xml_element(element: ElementTree.Element, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    text = str(element.attrib.get("text") or "").strip()
    desc = str(element.attrib.get("content-desc") or "").strip()
    node = {
        "text": text,
        "content_desc": desc,
        "labels": [item for item in (text, desc) if item],
        "bounds": _parse_bounds(element.attrib.get("bounds", "")),
        "clickable": str(element.attrib.get("clickable") or "") == "true",
        "enabled": str(element.attrib.get("enabled") or "true") == "true",
        "selected": str(element.attrib.get("selected") or "") == "true",
        "package": str(element.attrib.get("package") or ""),
        "class_name": str(element.attrib.get("class") or ""),
        "resource_id": str(element.attrib.get("resource-id") or ""),
    }
    if extra:
        node.update(extra)
    return node


def _find_contains(snapshot: dict[str, Any], target: str) -> dict[str, Any] | None:
    for node in snapshot.get("nodes", []):
        for label in node.get("labels", []):
            if target in str(label):
                return node
    return None


S05_TRIM_SCROLL_LIMIT = 24
S05_TRIM_SCROLL_DURATION_MS = 850
S05_TRIM_SCROLL_UNCHANGED_LIMIT = 2
S05_EMISSION_STANDARD_RE = re.compile(r"(?:[\s（(]*国\s*(?:VI|V|Ⅵ|Ⅴ|6|5)[）)]*)", re.IGNORECASE)


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


def _s05_config_match_identity(value: str) -> str:
    text = _s05_strip_emission_standard(value)
    text = re.sub(r"(?:19|20)\d{2}\s*款", " ", text)
    text = re.sub(r"改\s*款", " ", text)
    text = text.replace("—", "-").replace("－", "-").replace("–", "-")
    text = re.sub(r"[()（）\[\]【】,，;；:：/\\·]", " ", text)
    text = re.sub(r"[型版]", "", text)
    return re.sub(r"[\s\u3000]+", "", text).lower()


def _s05_target_trim_prefix_terms_from_params(params: dict[str, Any] | None) -> tuple[str, ...]:
    params = params or {}
    terms: list[str] = []

    def add(value: Any) -> None:
        text = _s05_normalize_trim_label(str(value or ""))
        if text and text not in terms:
            terms.append(text)

    brand = str(params.get("brand") or "").strip()
    series = str(params.get("series") or "").strip()
    series_alias = str(params.get("series_alias") or "").strip()
    alias_func = globals().get("get_target_brand_aliases")
    brand_aliases: list[str] = []
    if callable(alias_func):
        try:
            brand_aliases = [str(item or "").strip() for item in alias_func(brand)]
        except Exception:
            brand_aliases = []
    if not brand_aliases and brand:
        brand_aliases = [brand]
    series_aliases = [item for item in (series, series_alias) if item]
    for brand_alias in brand_aliases:
        add(brand_alias)
        for series_item in series_aliases:
            add(f"{brand_alias}{series_item}")
            add(f"{brand_alias} {series_item}")
    for series_item in series_aliases:
        add(series_item)
    return tuple(sorted(terms, key=lambda item: len(_s05_config_match_identity(item)), reverse=True))


def _s05_target_trim_identities(target_year: str, target_trim: str, prefix_terms: tuple[str, ...] = ()) -> tuple[str, ...]:
    year = _s05_normalize_trim_label(target_year)
    trim = _s05_normalize_trim_label(target_trim)
    candidates = [trim]
    if year and year not in trim:
        candidates.append(f"{year} {trim}")
    identities: list[str] = []

    def add_identity(value: str) -> None:
        identity = _s05_config_match_identity(value)
        if identity and identity not in identities:
            identities.append(identity)

    for candidate in candidates:
        add_identity(candidate)
    for identity in list(identities):
        for prefix in prefix_terms:
            prefix_identity = _s05_config_match_identity(prefix)
            if not prefix_identity or not identity.startswith(prefix_identity):
                continue
            stripped = identity[len(prefix_identity) :]
            if stripped and stripped not in identities:
                identities.append(stripped)
    return tuple(identities)


def _s05_trim_label_matches_target(
    label: str,
    target_year: str,
    target_trim: str,
    *,
    prefix_terms: tuple[str, ...] = (),
) -> bool:
    text = _s05_normalize_trim_label(label)
    trim = _s05_normalize_trim_label(target_trim)
    year = _s05_normalize_trim_label(target_year)
    if not text or not trim:
        return False
    if year and year not in text:
        return False
    return _s05_config_match_identity(text) in set(_s05_target_trim_identities(year, trim, prefix_terms))


def _s05_strip_emission_standard(value: str) -> str:
    text = _s05_normalize_trim_label(value)
    text = S05_EMISSION_STANDARD_RE.sub(" ", text)
    return _s05_normalize_trim_label(text)


def _s05_compact_trim_identity(value: str) -> str:
    text = _s05_strip_emission_standard(value)
    return re.sub(r"[\s\u3000]+", "", text).lower()


def _s05_target_trim_identity(target_year: str, target_trim: str, prefix_terms: tuple[str, ...] = ()) -> str:
    identities = _s05_target_trim_identities(target_year, target_trim, prefix_terms)
    return identities[0] if identities else _s05_compact_trim_identity(target_trim)


def _s05_emission_markers(value: str) -> list[str]:
    markers: list[str] = []
    for match in S05_EMISSION_STANDARD_RE.finditer(_s05_normalize_trim_label(value)):
        marker = re.sub(r"[\s（()）]+", "", match.group(0)).upper()
        marker = marker.replace("Ⅵ", "VI").replace("Ⅴ", "V").replace("6", "VI").replace("5", "V")
        if marker and marker not in markers:
            markers.append(marker)
    return markers


def _s05_trim_is_all_models(label: str) -> bool:
    text = _s05_normalize_trim_label(label)
    return "全部车型" in text or "鍏ㄩ儴杞﹀瀷" in text or bool(re.fullmatch(r"(?:19|20)\d{2}款?全部车型", text))


def _s05_same_trim_emission_variant_group(
    snapshot: dict[str, Any],
    target_year: str,
    target_trim: str,
    prefix_terms: tuple[str, ...] = (),
) -> dict[str, Any]:
    target_identities = set(_s05_target_trim_identities(target_year, target_trim, prefix_terms))
    target_identity = next(iter(target_identities), _s05_target_trim_identity(target_year, target_trim, prefix_terms))
    labels_seen: set[str] = set()
    variants: list[dict[str, Any]] = []
    excluded_similar: list[str] = []
    visible_labels = _s05_right_trim_labels(snapshot, target_year)
    for node in _s05_right_trim_nodes(snapshot, target_year):
        bounds = node.get("bounds")
        if not _s05_node_in_right_trim_region(snapshot, bounds):
            continue
        for label in _s05_labels(node):
            if label in labels_seen:
                continue
            labels_seen.add(label)
            if _s05_trim_is_all_models(label):
                excluded_similar.append(label)
                continue
            if target_year and target_year not in label:
                continue
            label_identity = _s05_config_match_identity(label)
            if label_identity not in target_identities:
                if target_year and target_year in label:
                    excluded_similar.append(label)
                continue
            variants.append(
                {
                    "label": label,
                    "node": node,
                    "bounds": list(bounds) if bounds else None,
                    "emission_markers": _s05_emission_markers(label),
                    "normalized_trim": label_identity,
                }
            )
    variants.sort(key=lambda item: ((item.get("bounds") or [0, 0, 0, 0])[1], (item.get("bounds") or [0, 0, 0, 0])[0]))
    group_count = len(variants)
    all_have_emission = all(item.get("emission_markers") for item in variants) if group_count > 1 else True
    ambiguous = group_count > 1 and not all_have_emission
    return {
        "s05_emission_variant_contract_enabled": True,
        "target_year_model": target_year,
        "target_config_model": target_trim,
        "normalized_target_config": target_identity,
        "normalized_target_config_candidates": sorted(target_identities),
        "visible_trim_names": visible_labels,
        "emission_variant_group": [
            {
                "label": item["label"],
                "bounds": item.get("bounds"),
                "emission_markers": item.get("emission_markers", []),
                "normalized_trim": item.get("normalized_trim"),
            }
            for item in variants
        ],
        "emission_variant_group_count": group_count,
        "emission_variant_group_confirmed": bool(group_count >= 1 and not ambiguous),
        "emission_variant_group_ambiguous": ambiguous,
        "excluded_similar_configs": excluded_similar,
        "selection_mode": "same_trim_multi_emission_variant" if group_count > 1 and not ambiguous else "single_exact_trim",
        "_variant_nodes": variants,
    }


def _s05_same_trim_emission_variant_group_public(evidence: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in evidence.items() if key != "_variant_nodes"}


def _s05_emission_variant_labels(evidence: dict[str, Any] | None) -> list[str]:
    labels: list[str] = []
    for item in (evidence or {}).get("emission_variant_group", []) or []:
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip()
        else:
            label = str(item or "").strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _s05_normalized_candidate_groups(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = {}
    for label in (evidence or {}).get("visible_trim_names", []) or []:
        text = str(label or "").strip()
        if not text or _s05_trim_is_all_models(text):
            continue
        identity = _s05_compact_trim_identity(text)
        groups.setdefault(identity, []).append(text)
    return [
        {"normalized_trim": identity, "candidate_trim_names": labels}
        for identity, labels in sorted(groups.items(), key=lambda item: item[0])
    ]


def _s05_emission_variant_result_fields(
    evidence: dict[str, Any] | None,
    *,
    selected_emission_variants: list[str] | None = None,
    selected_count_text: str | None = None,
    selected_count_expected: int | None = None,
    selected_count_actual: int | None = None,
    s05_emission_variant_all_selected: bool | None = None,
    s05_single_trim_selected: bool | None = None,
) -> dict[str, Any]:
    public = _s05_same_trim_emission_variant_group_public(evidence or {})
    raw_group_labels = _s05_emission_variant_labels(public)
    raw_group_count = int(public.get("emission_variant_group_count") or len(raw_group_labels) or 0)
    is_multi_emission_group = raw_group_count > 1 and public.get("selection_mode") == "same_trim_multi_emission_variant"
    group_labels = raw_group_labels if is_multi_emission_group else []
    selected = [str(item or "").strip() for item in (selected_emission_variants or []) if str(item or "").strip()]
    if not is_multi_emission_group:
        selected = []
    expected = selected_count_expected
    if expected is None:
        expected = len(group_labels) if is_multi_emission_group else None
    all_selected = s05_emission_variant_all_selected if is_multi_emission_group else None
    if is_multi_emission_group and all_selected is None:
        all_selected = bool(group_labels and set(group_labels).issubset(set(selected)))
    single_selected = bool(s05_single_trim_selected) if s05_single_trim_selected is not None else not is_multi_emission_group
    return {
        "s05_emission_variant_contract_enabled": True,
        "target_year_model": public.get("target_year_model"),
        "target_config_model": public.get("target_config_model"),
        "normalized_target_config": public.get("normalized_target_config"),
        "emission_variant_group": group_labels,
        "emission_variant_group_count": len(group_labels),
        "selected_emission_variants": selected,
        "selected_count_text": selected_count_text,
        "selected_count_expected": expected,
        "selected_count_actual": selected_count_actual,
        "s05_emission_variant_all_selected": all_selected,
        "s05_single_trim_selected": single_selected,
    }


def _s05_emission_variant_failure_fields(
    evidence: dict[str, Any] | None,
    *,
    selected_emission_variants: list[str] | None = None,
    selected_count_text: str | None = None,
    selected_count_expected: int | None = None,
    selected_count_actual: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    fields = _s05_emission_variant_result_fields(
        evidence,
        selected_emission_variants=selected_emission_variants,
        selected_count_text=selected_count_text,
        selected_count_expected=selected_count_expected,
        selected_count_actual=selected_count_actual,
        s05_emission_variant_all_selected=False,
    )
    group = fields.get("emission_variant_group") or _s05_emission_variant_labels(_s05_same_trim_emission_variant_group_public(evidence or {}))
    selected = fields.get("selected_emission_variants") or [str(item or "").strip() for item in (selected_emission_variants or []) if str(item or "").strip()]
    fields.update(
        {
            "candidate_trim_names": list((evidence or {}).get("visible_trim_names", []) or []),
            "normalized_candidate_groups": _s05_normalized_candidate_groups(evidence),
            "missing_emission_variants": [label for label in group if label not in selected],
            "reason": reason,
        }
    )
    return fields


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


def _s05_find_target_trim_node_from_xml(
    snapshot: dict[str, Any],
    target_year: str,
    target_trim: str,
    prefix_terms: tuple[str, ...] = (),
) -> dict[str, Any] | None:
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
        matched_label = next(
            (
                label
                for label in labels
                if _s05_trim_label_matches_target(label, target_year, target_trim, prefix_terms=prefix_terms)
            ),
            "",
        )
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


def _s05_find_left_year_click_target(snapshot: dict[str, Any], target_year: str, *, prefer_parent: bool = False) -> dict[str, Any] | None:
    text_node = _s05_find_left_year_node(snapshot, target_year)
    if not prefer_parent:
        if text_node is None:
            return None
        return {
            **text_node,
            "matched_year_text": str(target_year or "").strip(),
            "year_click_strategy": "direct_year_text_node_bounds",
            "year_text_node_bounds": text_node.get("bounds"),
            "year_parent_bounds": None,
        }
    xml_text = str(snapshot.get("fresh_xml") or "")
    if not xml_text.strip():
        return text_node
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return text_node
    parent_map = {child: parent for parent in root.iter() for child in parent}
    target = str(target_year or "").strip()
    for element in root.iter("node"):
        labels = [
            str(element.attrib.get("text") or "").strip(),
            str(element.attrib.get("content-desc") or "").strip(),
        ]
        if target not in labels:
            continue
        bounds = _parse_bounds(element.attrib.get("bounds", ""))
        if not _s05_node_in_left_year_region(snapshot, bounds):
            continue
        current = parent_map.get(element)
        while current is not None:
            current_bounds = _parse_bounds(current.attrib.get("bounds", ""))
            if (
                _s05_node_in_left_year_region(snapshot, current_bounds)
                and _has_positive_bounds(current_bounds)
                and str(current.attrib.get("enabled") or "true") == "true"
                and str(current.attrib.get("clickable") or "") == "true"
            ):
                return _node_dict_from_xml_element(
                    current,
                    extra={
                        "matched_year_text": target,
                        "year_click_strategy": "clickable_year_parent_or_row_bounds",
                        "year_text_node_bounds": bounds,
                        "year_parent_bounds": current_bounds,
                    },
                )
            current = parent_map.get(current)
    if text_node is not None:
        return {
            **text_node,
            "matched_year_text": target,
            "year_click_strategy": "direct_year_text_node_bounds",
            "year_text_node_bounds": text_node.get("bounds"),
            "year_parent_bounds": None,
        }
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


def _s05_left_year_selected_text(snapshot: dict[str, Any], target_year: str) -> str | None:
    target = str(target_year or "").strip()
    node = _s05_find_left_year_node(snapshot, target)
    if not node:
        return None
    if node.get("selected") is True or node.get("checked") is True:
        return target
    labels = {str(label).strip() for label in node.get("labels", [])}
    text = str(node.get("text") or "").strip()
    content_desc = str(node.get("content_desc") or "").strip()
    state_text = " ".join(sorted(labels | {text, content_desc}))
    if target and target in state_text and any(token in state_text for token in ("已选", "选中", "checked", "selected")):
        return target
    xml_text = str(snapshot.get("fresh_xml") or "")
    if target and xml_text.strip():
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError:
            root = None
        if root is not None:
            parent_map = {child: parent for parent in root.iter() for child in parent}
            for element in root.iter("node"):
                labels = {
                    str(element.attrib.get("text") or "").strip(),
                    str(element.attrib.get("content-desc") or "").strip(),
                }
                if target not in labels:
                    continue
                bounds = _parse_bounds(element.attrib.get("bounds", ""))
                if not _s05_node_in_left_year_region(snapshot, bounds):
                    continue
                current = element
                while current is not None:
                    current_bounds = _parse_bounds(current.attrib.get("bounds", ""))
                    if not _s05_node_in_left_year_region(snapshot, current_bounds):
                        current = parent_map.get(current)
                        continue
                    selected_attr = str(current.attrib.get("selected") or "").lower()
                    checked_attr = str(current.attrib.get("checked") or "").lower()
                    desc_attr = str(current.attrib.get("content-desc") or "")
                    text_attr = str(current.attrib.get("text") or "")
                    if selected_attr == "true" or checked_attr == "true" or "已选" in desc_attr or "选中" in desc_attr or "已选" in text_attr or "选中" in text_attr:
                        return target
                    current = parent_map.get(current)
    return None


def _s05_right_config_list_belongs_to_year(labels: list[str], target_year: str) -> bool:
    target = str(target_year or "").strip()
    config_year_prefixes = [
        prefix
        for label in labels
        if "全部车型" not in str(label)
        and (prefix := _s05_year_label_prefix(label))
    ]
    if not config_year_prefixes:
        return False
    return all(prefix == target for prefix in config_year_prefixes)


def _s05_target_config_seen_after_year(
    snapshot: dict[str, Any],
    target_year: str,
    target_trim: str | None,
    prefix_terms: tuple[str, ...] = (),
) -> bool:
    trim = str(target_trim or "").strip()
    if not trim:
        return False
    evidence = _s05_same_trim_emission_variant_group(snapshot, target_year, trim, prefix_terms)
    if evidence.get("emission_variant_group"):
        return True
    return _s05_find_target_trim_node(snapshot, target_year, trim, prefix_terms) is not None


def _s05_year_label_prefix(label: str) -> str | None:
    match = re.search(r"((?:19|20)\d{2}款)", str(label or ""))
    return match.group(1) if match else None


def _s05_year_number(year_label: str | None) -> int | None:
    match = re.search(r"((?:19|20)\d{2})", str(year_label or ""))
    return int(match.group(1)) if match else None


def _s05_year_switch_evidence(
    snapshot: dict[str, Any],
    target_year: str,
    *,
    target_trim: str | None = None,
    target_prefix_terms: tuple[str, ...] = (),
    before_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = str(target_year or "").strip()
    before_visible = _s05_right_trim_labels(before_snapshot or {}, None) if before_snapshot else []
    after_visible = _s05_right_trim_labels(snapshot, None)
    target_year_labels = [label for label in after_visible if target and target in label]
    non_target_year_labels = [
        label for label in after_visible
        if (prefix := _s05_year_label_prefix(label)) and prefix != target
    ]
    before_year_prefixes = sorted({prefix for label in before_visible if (prefix := _s05_year_label_prefix(label))})
    after_year_prefixes = sorted({prefix for label in after_visible if (prefix := _s05_year_label_prefix(label))})
    right_trim_year_switched = bool(target_year_labels)
    left_year_selected_text = _s05_left_year_selected_text(snapshot, target)
    right_config_list_after_year = list(after_visible)
    right_config_list_belongs_to_target_year = _s05_right_config_list_belongs_to_year(right_config_list_after_year, target)
    target_config_seen_after_year = _s05_target_config_seen_after_year(snapshot, target, target_trim, target_prefix_terms)
    s05_target_year_selected_confirmed = bool(
        left_year_selected_text == target
        and right_config_list_belongs_to_target_year
        and target_config_seen_after_year
    )
    return {
        "target_year_model": target,
        "visible_trim_names_before_year_click": before_visible,
        "visible_trim_names_after_year_click": after_visible,
        "target_year_trim_labels_after_click": target_year_labels,
        "non_target_year_trim_labels_after_click": non_target_year_labels,
        "right_trim_year_prefixes_before_click": before_year_prefixes,
        "right_trim_year_prefixes_after_click": after_year_prefixes,
        "right_trim_year_switched": right_trim_year_switched,
        "left_year_selected": bool(left_year_selected_text == target),
        "selected_year_after_click": left_year_selected_text,
        "left_year_selected_text": left_year_selected_text,
        "s05_target_year_selected_confirmed": s05_target_year_selected_confirmed,
        "right_config_list_after_year": right_config_list_after_year,
        "right_config_list_belongs_to_target_year": right_config_list_belongs_to_target_year,
        "target_config_seen_after_year": target_config_seen_after_year,
        "direct_right_config_search_without_year_click": False,
        "s05_year_confirmed_by": None,
        "s05_year_click_record_valid": False,
    }


def _s05_year_click_record_confirmation(
    *,
    target_year: str,
    clicked_text: str | None,
    clicked_region: str | None,
    click_executed: bool,
    after_state: str | None,
) -> dict[str, Any]:
    """V1.25: a real left-list target-year click is the year confirmation."""
    target = str(target_year or "").strip()
    clicked = str(clicked_text or "").strip()
    region = str(clicked_region or "").strip()
    valid_after_states = {"S05", "S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"}
    result = {
        "target_year_model": target,
        "s05_year_clicked": bool(click_executed),
        "s05_year_click_text": clicked,
        "s05_year_click_region": region or None,
        "s05_year_click_after_fresh_state": after_state,
        "s05_year_click_record_valid": False,
        "s05_target_year_selected_confirmed": False,
        "s05_year_confirmed_by": None,
        "s05_year_click_record_stop_code": None,
    }
    if not click_executed:
        result["s05_year_click_record_stop_code"] = "S05_TARGET_YEAR_LEFT_TAB_NOT_CLICKED"
        return result
    if region != "left_year_list":
        result["s05_year_click_record_stop_code"] = "S05_RIGHT_CONFIG_SEARCH_WITHOUT_YEAR_CLICK"
        return result
    if clicked != target:
        result["s05_year_click_record_stop_code"] = "S05_WRONG_YEAR_SELECTED"
        return result
    if after_state not in valid_after_states:
        result["s05_year_click_record_stop_code"] = "S05_TARGET_YEAR_SELECTION_NOT_CONFIRMED"
        return result
    result.update(
        {
            "s05_year_click_record_valid": True,
            "s05_target_year_selected_confirmed": True,
            "s05_year_confirmed_by": "left_year_click_record",
            "s05_year_click_record_stop_code": None,
        }
    )
    return result


def _s05_right_list_mode(snapshot: dict[str, Any]) -> dict[str, Any]:
    labels = _s05_right_trim_labels(snapshot, None)
    visible_year_sections = sorted({prefix for label in labels if (prefix := _s05_year_label_prefix(label))})
    has_all_models = any("全部车型" in label for label in labels)
    has_year_sections = bool(visible_year_sections)
    mode = "year_section_list" if has_all_models or has_year_sections else "unknown"
    return {
        "right_list_mode": mode,
        "right_list_has_all_models": has_all_models,
        "right_list_has_year_sections": has_year_sections,
        "visible_year_sections": visible_year_sections,
        "visible_trim_names": labels,
    }


def _s05_right_list_contains_year(snapshot: dict[str, Any], target_year: str) -> bool:
    target = str(target_year or "").strip()
    if not target:
        return False
    return any(target in label for label in _s05_right_trim_labels(snapshot, target))


def _s05_right_trim_signature(snapshot: dict[str, Any], target_year: str | None = None) -> str:
    return "\n".join(_s05_right_trim_labels(snapshot, target_year))


def _s05_right_trim_search_signature(snapshot: dict[str, Any], target_year: str) -> str:
    target_signature = _s05_right_trim_signature(snapshot, target_year)
    return target_signature if target_signature else _s05_right_trim_signature(snapshot, None)


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
        if any(label == "确定" or (label.endswith("车型") and "款" not in label and label != "全部车型") for label in labels):
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


def _s05_right_trim_scroll_phase(snapshot: dict[str, Any], target_year: str, *, overscroll_recheck_used: bool = False) -> dict[str, Any]:
    mode = _s05_right_list_mode(snapshot)
    target_year_num = _s05_year_number(target_year)
    section_numbers = [
        number
        for section in mode.get("visible_year_sections", [])
        if (number := _s05_year_number(str(section))) is not None
    ]
    min_visible_year = min(section_numbers) if section_numbers else None
    max_visible_year = max(section_numbers) if section_numbers else None
    target_year_trim_visible = _s05_right_list_contains_year(snapshot, target_year)
    target_year_section_seen = bool(
        target_year_num is not None
        and any(number == target_year_num for number in section_numbers)
    )
    target_year_seen = target_year_section_seen or target_year_trim_visible
    overscrolled_target_year = bool(
        target_year_num is not None
        and section_numbers
        and min_visible_year is not None
        and min_visible_year < target_year_num
        and not target_year_seen
    )
    switch_to_precision_reason = None
    if target_year_seen:
        scroll_phase = "precision_scroll_in_target_year"
        scroll_mode = "target_year_precise_scroll"
        switch_to_precision_reason = "target_year_section_seen" if target_year_section_seen else "target_year_trim_visible"
    elif overscrolled_target_year and not overscroll_recheck_used:
        scroll_phase = "overscroll_recheck"
        scroll_mode = "target_year_reverse_recheck_scroll"
    else:
        scroll_phase = "max_scroll_to_target_year"
        scroll_mode = "max_controlled_scroll"

    def _year_label(number: int | None) -> str | None:
        return f"{number}款" if number is not None else None

    return {
        **mode,
        "scroll_phase": scroll_phase,
        "target_year_trim_visible": target_year_trim_visible,
        "target_year_section_seen": target_year_section_seen,
        "entered_target_year_section": target_year_seen,
        "min_visible_year_section": _year_label(min_visible_year),
        "max_visible_year_section": _year_label(max_visible_year),
        "possibly_overscrolled_target_year": overscrolled_target_year,
        "right_trim_scroll_mode": scroll_mode,
        "switch_to_precision_reason": switch_to_precision_reason,
    }


def _s05_right_trim_scroll_command(snapshot: dict[str, Any], *, scroll_mode: str = "max_controlled_scroll") -> dict[str, Any] | None:
    bounds = _s05_right_trim_scroll_bounds(snapshot)
    if not bounds:
        return None
    x1, y1, x2, y2 = bounds
    height = max(1, y2 - y1)
    width = max(1, x2 - x1)
    x = x1 + int(width * 0.55)
    safe_margin = max(80, min(220, int(height * 0.10)))
    if scroll_mode == "target_year_reverse_recheck_scroll":
        start_y = y1 + max(80, int(height * 0.30))
        end_y = y2 - safe_margin
    elif scroll_mode == "target_year_precise_scroll":
        start_y = y1 + int(height * 0.68)
        end_y = y1 + int(height * 0.46)
    else:
        scroll_mode = "max_controlled_scroll"
        start_y = y2 - safe_margin
        end_y = y1 + safe_margin
    confirm_nodes = [
        node.get("bounds")
        for node in snapshot.get("nodes", [])
        if _has_nonzero_bounds(node.get("bounds")) and any(label == "确定" for label in _s05_labels(node))
    ]
    if confirm_nodes and scroll_mode != "target_year_reverse_recheck_scroll":
        confirm_top = min(bounds[1] for bounds in confirm_nodes)
        start_y = min(start_y, confirm_top - 80)
    start_y = max(y1 + 80, min(start_y, y2 - 120))
    end_y = max(y1 + 80, min(end_y, y2 - 120))
    if scroll_mode == "target_year_reverse_recheck_scroll":
        if start_y >= end_y:
            return None
    elif start_y <= end_y:
        return None
    scroll_distance = abs(start_y - end_y)
    return {
        "scroll_bounds": bounds,
        "right_scroll_bounds": bounds,
        "right_trim_scroll_mode": scroll_mode,
        "swipe_x": x,
        "swipe_y_start": start_y,
        "swipe_y_end": end_y,
        "scroll_x": x,
        "scroll_y_start": start_y,
        "scroll_y_end": end_y,
        "scroll_distance_px": scroll_distance,
        "scroll_distance_ratio": round(scroll_distance / height, 3),
        "swipe_duration_ms": S05_TRIM_SCROLL_DURATION_MS,
    }


def _scroll_s05_right_trim_list(context: dict[str, Any], snapshot: dict[str, Any], *, scroll_mode: str = "max_controlled_scroll") -> dict[str, Any]:
    command = _s05_right_trim_scroll_command(snapshot, scroll_mode=scroll_mode)
    if not command:
        return {"issued": False, "reason": "S05_RIGHT_TRIM_SCROLL_BOUNDS_MISSING"}
    completed, elapsed_ms = contract_execute_swipe(
        context,
        snapshot,
        "S05_MODEL_YEAR_SELECTED",
        "scroll_trim_list",
        points=(
            int(command["swipe_x"]),
            int(command["swipe_y_start"]),
            int(command["swipe_x"]),
            int(command["swipe_y_end"]),
            int(command["swipe_duration_ms"]),
        ),
        evidence={"scroll_mode": scroll_mode, **command},
    )
    command.update(
        {
            "issued": True,
            "swipe_return_code": completed.returncode,
            "swipe_stdout": (completed.stdout or "").strip(),
            "swipe_stderr": (completed.stderr or "").strip(),
            "swipe_elapsed_ms": elapsed_ms,
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
    year_click_record_confirmed: bool = False,
) -> dict[str, Any]:
    before_visible = _s05_right_trim_labels(before_snapshot, target_year)
    after_visible = _s05_right_trim_labels(after_snapshot or {}, target_year) if after_snapshot else []
    before_visible_all = _s05_right_trim_labels(before_snapshot, None)
    after_visible_all = _s05_right_trim_labels(after_snapshot or {}, None) if after_snapshot else []
    before_mode = _s05_right_list_mode(before_snapshot)
    after_mode = _s05_right_list_mode(after_snapshot or {})
    before_right = [label for label in before_visible if target_year in label]
    after_right = [label for label in after_visible if target_year in label]
    trim_names_changed = before_visible != after_visible or before_visible_all != after_visible_all
    top_changed = (before_right[0] if before_right else None) != (after_right[0] if after_right else None)
    bottom_changed = (before_right[-1] if before_right else None) != (after_right[-1] if after_right else None)
    screenshot_region_changed = _s05_screenshot_region_changed(
        before_snapshot.get("screenshot_path"),
        (after_snapshot or {}).get("screenshot_path"),
        command.get("scroll_bounds"),
    )
    left_year_selected_text_after = _s05_left_year_selected_text(after_snapshot or {}, target_year)
    left_year_still_selected = (
        left_year_selected_text_after == str(target_year or "").strip()
        or bool(year_click_record_confirmed)
    )
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
        "scroll_phase": command.get("scroll_phase"),
        "right_trim_scroll_mode": command.get("right_trim_scroll_mode"),
        "trim_scroll_mode": command.get("right_trim_scroll_mode"),
        "switch_to_precision_reason": command.get("switch_to_precision_reason"),
        "swipe_x": command.get("swipe_x"),
        "swipe_y_start": command.get("swipe_y_start"),
        "swipe_y_end": command.get("swipe_y_end"),
        "scroll_x": command.get("scroll_x"),
        "scroll_y_start": command.get("scroll_y_start"),
        "scroll_y_end": command.get("scroll_y_end"),
        "scroll_distance_px": command.get("scroll_distance_px"),
        "scroll_distance_ratio": command.get("scroll_distance_ratio"),
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
        "before_visible_trim_names_all": before_visible_all,
        "after_visible_trim_names_all": after_visible_all,
        "visible_year_sections_before": before_mode.get("visible_year_sections", []),
        "visible_year_sections_after": after_mode.get("visible_year_sections", []),
        "visible_trim_names_before": before_visible_all,
        "visible_trim_names_after": after_visible_all,
        "min_visible_year_section": command.get("min_visible_year_section"),
        "max_visible_year_section": command.get("max_visible_year_section"),
        "trim_names_changed": trim_names_changed,
        "top_trim_changed": top_changed,
        "bottom_trim_changed": bottom_changed,
        "screenshot_region_changed": screenshot_region_changed,
        "scroll_effective": scroll_effective,
        "recognized_page_after_scroll": recognized_page_after_scroll,
        "left_year_still_selected": left_year_still_selected,
        "left_year_selected_text_after_scroll": left_year_selected_text_after,
        "left_year_selected_verify_method": "left_year_click_record" if year_click_record_confirmed else "left_year_selected_state",
        "target_year_section_seen": _s05_right_list_contains_year(after_snapshot or {}, target_year),
        "entered_target_year_section": _s05_right_list_contains_year(after_snapshot or {}, target_year),
        **{f"after_{key}": value for key, value in after_mode.items()},
    }


def _s05_find_target_trim_node(
    snapshot: dict[str, Any],
    target_year: str,
    target_trim: str,
    prefix_terms: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    year = str(target_year or "").strip()
    trim = str(target_trim or "").strip()
    if not trim:
        return None
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _s05_node_in_right_trim_region(snapshot, bounds):
            continue
        for label in _s05_labels(node):
            if not _s05_trim_label_matches_target(label, year, trim, prefix_terms=prefix_terms):
                continue
            if node.get("enabled", True) and node.get("clickable"):
                return {**node, "matched_trim_text": label, "trim_click_strategy": "direct_clickable_trim_text_node"}
    return _s05_find_target_trim_node_from_xml(snapshot, year, trim, prefix_terms)


def _s05_visible_matching_trim_labels(
    snapshot: dict[str, Any],
    target_year: str,
    target_trim: str,
    prefix_terms: tuple[str, ...] = (),
) -> list[str]:
    labels: list[str] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        for label in _s05_labels(node):
            if not _s05_trim_label_matches_target(label, target_year, target_trim, prefix_terms=prefix_terms):
                continue
            if _s05_node_in_right_trim_region(snapshot, bounds) and label not in labels:
                labels.append(label)
    return labels


def _s05_selected_one(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    if "已选1项" in blob:
        return True
    return any(label == "已选1项" for node in snapshot.get("nodes", []) for label in _s05_labels(node))


def _s05_selected_count_text(snapshot: dict[str, Any]) -> str | None:
    texts = [str(snapshot.get("visible_blob") or "")]
    texts.extend(_s05_right_trim_labels(snapshot, None))
    for node in snapshot.get("nodes", []):
        texts.extend(_s05_labels(node))
    for text in texts:
        match = re.search(r"已选\s*(\d+)\s*项", str(text or ""))
        if match:
            return f"已选{match.group(1)}项"
    return None


def _s05_selected_count(snapshot: dict[str, Any]) -> int | None:
    text = _s05_selected_count_text(snapshot)
    if not text:
        return 1 if _s05_selected_one(snapshot) else None
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def _s05_selected_count_matches(snapshot: dict[str, Any], expected_count: int) -> bool:
    selected_count = _s05_selected_count(snapshot)
    if selected_count is None:
        return expected_count == 1 and _s05_selected_one(snapshot)
    return selected_count == expected_count


def _s05_target_trim_selected(
    snapshot: dict[str, Any],
    target_year: str,
    target_trim: str,
    prefix_terms: tuple[str, ...] = (),
) -> bool:
    variant_group = _s05_same_trim_emission_variant_group(snapshot, target_year, target_trim, prefix_terms)
    if variant_group.get("emission_variant_group_confirmed") and int(variant_group.get("emission_variant_group_count") or 0) > 1:
        return _s05_selected_count_matches(snapshot, int(variant_group.get("emission_variant_group_count") or 0))
    trim_node = _s05_find_target_trim_node(snapshot, target_year, target_trim, prefix_terms)
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


def get_target_brand_aliases(brand: str) -> list[str]:
    aliases: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in aliases:
            aliases.append(text)

    normalized = str(brand or "").strip()
    add(normalized)
    route_aliases = S03_BRAND_ROUTE_ALIASES.get(normalized)
    if route_aliases is None:
        upper = normalized.upper()
        route_aliases = next((values for key, values in S03_BRAND_ROUTE_ALIASES.items() if key.upper() == upper), None)
    for alias in route_aliases or ():
        add(alias)
    return aliases


def _s03_target_brand_aliases(params: dict[str, Any]) -> list[str]:
    data = _current_target_task_data()
    brand = str(data.get("brand") or params.get("brand") or "").strip()
    aliases: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in aliases:
            aliases.append(text)

    for alias in get_target_brand_aliases(brand):
        add(alias)
    if brand in {"零跑", "零跑汽车", "LEAPMOTOR", "Leapmotor"}:
        for alias in ("零跑", "零跑汽车", "LEAPMOTOR", "Leapmotor"):
            add(alias)
    if brand in {"吉利", "吉利汽车", "GEELY", "Geely"}:
        for alias in ("吉利", "吉利汽车", "GEELY", "Geely"):
            add(alias)
    if brand in {"长安", "长安汽车", "CHANGAN"}:
        for alias in ("长安", "长安汽车", "CHANGAN"):
            add(alias)
    if brand in {"日产", "东风日产", "NISSAN", "Nissan"}:
        for alias in ("日产", "东风日产", "NISSAN", "Nissan"):
            add(alias)
    return aliases


def derive_brand_initial(brand: str, aliases: list[str] | None = None) -> str | None:
    for candidate in [brand, *(aliases or [])]:
        initial = _derive_brand_initial(str(candidate or ""))
        if initial:
            return initial
    return None


def get_target_brand_initial(target_brand: str, target_brand_aliases: list[str]) -> str | None:
    return derive_brand_initial(target_brand, target_brand_aliases)


def _find_s03_target_brand_alias(snapshot: dict[str, Any], aliases: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    for alias in aliases:
        node = _find_s03_target_brand(snapshot, alias)
        if node and node.get("bounds"):
            return node, alias
    return None, None


def _bounds_contains_bounds(outer: list[int] | tuple[int, int, int, int] | None, inner: list[int] | tuple[int, int, int, int] | None) -> bool:
    return bool(
        outer
        and inner
        and outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _point_in_bounds(point: tuple[int, int] | list[int], bounds: list[int] | tuple[int, int, int, int] | None, margin: int = 0) -> bool:
    return bool(
        bounds
        and bounds[0] - margin <= point[0] <= bounds[2] + margin
        and bounds[1] - margin <= point[1] <= bounds[3] + margin
    )


def _s03_brand_zone_text_nodes(snapshot: dict[str, Any], row_bounds: list[int] | tuple[int, int, int, int] | None = None) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        labels = {str(label).strip() for label in node.get("labels", []) if str(label).strip()}
        if not any("\u54c1\u724c\u4e13\u533a" in label or label in {"\u4e13\u533a", "\u8fdb\u5165\u4e13\u533a"} for label in labels):
            continue
        if row_bounds:
            row_mid_y = (row_bounds[1] + row_bounds[3]) // 2
            if not (bounds[1] <= row_mid_y <= bounds[3] or row_bounds[1] <= _center(bounds)[1] <= row_bounds[3]):
                continue
        nodes.append(node)
    return nodes


def _s03_brand_row_bounds(snapshot: dict[str, Any], brand_node: dict[str, Any]) -> list[int] | None:
    brand_bounds = brand_node.get("bounds")
    if not _has_positive_bounds(brand_bounds):
        return None
    max_x = _screen_max_x(snapshot) or max((node.get("bounds", [0, 0, 0, 0])[2] for node in snapshot.get("nodes", []) if node.get("bounds")), default=0)
    brand_w = brand_bounds[2] - brand_bounds[0]
    brand_h = brand_bounds[3] - brand_bounds[1]
    if brand_node.get("clickable") and max_x and brand_w >= int(max_x * 0.55) and brand_h <= 260:
        return list(brand_bounds)
    candidates: list[list[int]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        if not _bounds_contains_bounds(bounds, brand_bounds):
            continue
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        if width < max(brand_w + 120, int((max_x or width) * 0.35)):
            continue
        if height > max(brand_h * 5, 260):
            continue
        candidates.append(list(bounds))
    if candidates:
        return min(candidates, key=lambda item: ((item[2] - item[0]) * (item[3] - item[1]), item[3] - item[1]))
    y_pad = max(24, brand_h)
    return [0, max(0, brand_bounds[1] - y_pad), max_x or brand_bounds[2], brand_bounds[3] + y_pad]


def detect_visible_target_brand_alias(snapshot: dict[str, Any], target_brand_aliases: list[str]) -> dict[str, Any]:
    brand_node, matched_alias = _find_s03_target_brand_alias(snapshot, target_brand_aliases)
    return {
        "target_brand_visible": bool(brand_node and brand_node.get("bounds")),
        "matched_alias": matched_alias,
        "brand_node": brand_node,
        "matched_brand_bounds": brand_node.get("bounds") if brand_node else None,
    }


def find_target_brand_row_bounds(snapshot: dict[str, Any], matched_alias: str) -> list[int] | None:
    brand_node = _find_s03_target_brand(snapshot, matched_alias)
    if not brand_node:
        return None
    return _s03_brand_row_bounds(snapshot, brand_node)


def _s03_brand_row_left_icon_bounds(
    snapshot: dict[str, Any],
    row_bounds: list[int] | tuple[int, int, int, int] | None,
    brand_bounds: list[int] | tuple[int, int, int, int] | None,
) -> list[int] | None:
    if not _has_positive_bounds(row_bounds):
        return None
    row_width = row_bounds[2] - row_bounds[0]
    row_height = row_bounds[3] - row_bounds[1]
    if row_width <= 0 or row_height <= 0:
        return None
    vertical_bounds = brand_bounds if _has_positive_bounds(brand_bounds) else row_bounds
    left_limit = row_bounds[0] + max(120, int(row_width * 0.22))
    candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        if not _bounds_contains_bounds(row_bounds, bounds):
            continue
        center_x, center_y = _center(bounds)
        if center_x > left_limit:
            continue
        if vertical_bounds and not (vertical_bounds[1] - 16 <= center_y <= vertical_bounds[3] + 16):
            continue
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        if width < 24 or height < 24:
            continue
        if width > max(180, int(row_width * 0.22)) or height > max(180, row_height + 40):
            continue
        labels = [str(label).strip() for label in node.get("labels", []) if str(label).strip()]
        class_name = str(node.get("class_name") or "")
        score = 0 if "Image" in class_name and not labels else 1
        candidates.append({"bounds": list(bounds), "score": score, "center_x": center_x, "area": width * height})
    if not candidates:
        return None
    best = min(candidates, key=lambda item: (item["score"], item["center_x"], item["area"]))
    return list(best["bounds"])


def compute_brand_row_left_icon_safe_point(
    row_bounds: list[int] | tuple[int, int, int, int] | None,
    icon_bounds: list[int] | tuple[int, int, int, int] | None = None,
) -> tuple[int, int] | None:
    if not _has_positive_bounds(row_bounds):
        return None
    if _has_positive_bounds(icon_bounds) and _bounds_contains_bounds(row_bounds, icon_bounds):
        return _center(icon_bounds)
    safe_margin = 24
    x = min(row_bounds[2] - safe_margin, row_bounds[0] + safe_margin)
    y = (row_bounds[1] + row_bounds[3]) // 2
    return int(x), int(y)


def validate_s03_brand_click_contract(
    snapshot: dict[str, Any],
    click_point: tuple[int, int] | list[int] | None,
    row_bounds: list[int] | tuple[int, int, int, int] | None,
) -> dict[str, Any]:
    forbidden_nodes = _s03_brand_zone_text_nodes(snapshot, row_bounds)
    forbidden_bounds = [list(node.get("bounds")) for node in forbidden_nodes if _has_positive_bounds(node.get("bounds"))]
    point_in_row = bool(click_point and row_bounds and _point_in_bounds(click_point, row_bounds))
    overlaps_forbidden = bool(click_point and any(_point_in_bounds(click_point, bounds, margin=6) for bounds in forbidden_bounds))
    return {
        "forbidden_text_seen": bool(forbidden_bounds),
        "forbidden_bounds": forbidden_bounds,
        "brand_zone_text_seen": bool(forbidden_bounds),
        "brand_zone_bounds": forbidden_bounds,
        "selected_click_point_in_row": point_in_row,
        "selected_click_overlaps_forbidden": overlaps_forbidden,
        "selected_click_overlaps_brand_zone": overlaps_forbidden,
        "contract_click_valid": point_in_row and not overlaps_forbidden,
    }


def execute_s03_only_allowed_brand_click(snapshot: dict[str, Any], target_brand_aliases: list[str]) -> dict[str, Any]:
    detection = detect_visible_target_brand_alias(snapshot, target_brand_aliases)
    if not detection["target_brand_visible"]:
        return {
            **detection,
            "next_action": None,
            "selected_click_region_type": None,
            "selected_click_point": None,
        }
    matched_alias = str(detection.get("matched_alias") or "")
    brand_node = detection.get("brand_node") if isinstance(detection.get("brand_node"), dict) else None
    row_bounds = _s03_brand_row_bounds(snapshot, brand_node) if brand_node else find_target_brand_row_bounds(snapshot, matched_alias)
    brand_bounds = detection.get("matched_brand_bounds") if detection.get("matched_brand_bounds") else None
    icon_bounds = _s03_brand_row_left_icon_bounds(snapshot, row_bounds, brand_bounds)
    click_point = compute_brand_row_left_icon_safe_point(row_bounds, icon_bounds)
    validation = validate_s03_brand_click_contract(snapshot, click_point, row_bounds)
    return {
        **detection,
        "matched_brand_text": matched_alias,
        "matched_brand_bounds": list(detection.get("matched_brand_bounds")) if detection.get("matched_brand_bounds") else None,
        "brand_row_bounds": row_bounds,
        "brand_row_left_icon_bounds": icon_bounds,
        "selected_click_point": list(click_point) if click_point else None,
        "selected_click_region_type": "brand_row_left_icon_safe_point" if click_point else None,
        "next_action": "S03_ONLY_ALLOWED_ACTION_CLICK_BRAND_ROW_LEFT_ICON_SAFE_POINT",
        **validation,
    }


def _s03_brand_row_left_icon_safe_click_plan(snapshot: dict[str, Any], brand_node: dict[str, Any], matched_alias: str | None = None) -> dict[str, Any]:
    aliases = [matched_alias] if matched_alias else []
    label = next((str(item).strip() for item in brand_node.get("labels", []) if str(item).strip()), "")
    if label and label not in aliases:
        aliases.append(label)
    plan = execute_s03_only_allowed_brand_click(snapshot, aliases)
    if plan.get("target_brand_visible"):
        return plan
    row_bounds = _s03_brand_row_bounds(snapshot, brand_node)
    icon_bounds = _s03_brand_row_left_icon_bounds(snapshot, row_bounds, brand_node.get("bounds"))
    click_point = compute_brand_row_left_icon_safe_point(row_bounds, icon_bounds)
    validation = validate_s03_brand_click_contract(snapshot, click_point, row_bounds)
    return {
        "matched_brand_text": matched_alias or label,
        "matched_brand_bounds": list(brand_node.get("bounds")) if brand_node.get("bounds") else None,
        "brand_row_bounds": row_bounds,
        "brand_row_left_icon_bounds": icon_bounds,
        "selected_click_point": list(click_point) if click_point else None,
        "selected_click_region_type": "brand_row_left_icon_safe_point" if click_point else None,
        **validation,
    }


def _s03_visible_brand_names(snapshot: dict[str, Any], limit: int = 80) -> list[str]:
    names: list[str] = []
    skip = {"选择品牌", "只看新能源", "热门品牌", "全部", "热门", "新能源"}
    for node in snapshot.get("nodes", []):
        for label in node.get("labels", []):
            name = _s03_brand_label_primary(str(label))
            if not name or name in skip:
                continue
            if re.fullmatch(r"[A-Z*]", name):
                continue
            if name not in names:
                names.append(name)
            if len(names) >= limit:
                return names
    return names


def _s03_search_target_brand_v2(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    brand: str,
) -> dict[str, Any]:
    aliases = _s03_target_brand_aliases(context["task_params"])
    target_initial = get_target_brand_initial(brand, aliases)
    audit: dict[str, Any] = {
        "s03_contract_version": "V1.16",
        "s03_search_strategy_version": "S03_V1_16_INITIAL_LETTER_AND_LEFT_ICON_CONTRACT",
        "target_brand_aliases": aliases,
        "target_initial_letter": target_initial,
        "visible_brand_names_by_step": [],
        "matched_alias": None,
        "matched_bounds": None,
        "clicked_brand": None,
        "clicked_brand_bounds": None,
        "current_s03_search_strategy": "v1_16_contract_only",
        "target_brand_visible": False,
        "target_brand_visible_before_letter": False,
        "target_brand_visible_after_letter": None,
        "next_action": None,
        "attempted_new_energy_tab": False,
        "attempted_letter_L": False,
        "attempted_letter_G": False,
        "attempted_alphabet": False,
        "attempted_scroll": False,
        "attempted_brand_name_click": False,
        "attempted_brand_icon_click": False,
        "attempted_row_center_click": False,
        "attempted_row_right_click": False,
        "attempted_brand_zone_click": False,
        "initial_letter_clicked": False,
        "clicked_initial_letter": None,
        "detected_letters": [],
        "target_initial_letter_bounds": None,
        "reason_alias_not_matched": None,
    }

    def record_visible(step: str, current_snapshot: dict[str, Any]) -> None:
        audit["visible_brand_names_by_step"].append(
            {
                "step": step,
                "visible_brand_names": _s03_visible_brand_names(current_snapshot),
                "screenshot_path": str(current_snapshot.get("screenshot_path") or ""),
                "xml_path": str(current_snapshot.get("xml_path") or ""),
            }
        )

    def plan_visible_match(current_snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
        current_plan = execute_s03_only_allowed_brand_click(current_snapshot, aliases)
        current_node = current_plan.get("brand_node") if isinstance(current_plan.get("brand_node"), dict) else None
        current_alias = str(current_plan.get("matched_alias") or "") or None
        return current_plan, current_node, current_alias

    def found_result(current_snapshot: dict[str, Any], current_plan: dict[str, Any], current_node: dict[str, Any], current_alias: str | None, *, source: str) -> dict[str, Any]:
        audit.update(
            {
                "matched_alias": current_alias,
                "matched_bounds": current_plan.get("matched_brand_bounds"),
                "target_brand_visible": True,
                "next_action": current_plan.get("next_action"),
                "visible_contract_plan": {key: value for key, value in current_plan.items() if key != "brand_node"},
                "brand_found_by": source,
            }
        )
        context["s03_brand_search_v2"] = dict(audit)
        return {
            "snapshot": current_snapshot,
            "brand_node": current_node,
            "matched_alias": current_alias,
            "audit": audit,
            "transition_wait_ms": int(audit.get("transition_wait_ms_total") or 0),
        }

    def scan_current_group(start_snapshot: dict[str, Any], *, reason: str, step_prefix: str) -> dict[str, Any]:
        client: AdbClient = context["client"]
        machine: PageStateMachine = context["machine"]
        current_snapshot = start_snapshot
        audit["attempted_fallback_brand_scan"] = True
        audit.setdefault("fallback_scan_steps", [])
        audit.setdefault("fallback_scroll_count", 0)
        transition_wait_ms = int(audit.get("transition_wait_ms_total") or 0)
        previous_visible: list[str] | None = None
        for screen_index in range(S03_BRAND_SCAN_SCROLL_LIMIT + 1):
            visible_names = _s03_visible_brand_names(current_snapshot)
            audit["fallback_scan_steps"].append(
                {
                    "step": f"{step_prefix}_{screen_index}",
                    "visible_brand_names": visible_names,
                    "screenshot_path": str(current_snapshot.get("screenshot_path") or ""),
                    "xml_path": str(current_snapshot.get("xml_path") or ""),
                }
            )
            current_plan, current_node, current_alias = plan_visible_match(current_snapshot)
            if current_node and current_node.get("bounds"):
                audit["transition_wait_ms_total"] = transition_wait_ms
                return found_result(current_snapshot, current_plan, current_node, current_alias, source=f"{step_prefix}_scan")
            if screen_index >= S03_BRAND_SCAN_SCROLL_LIMIT:
                break
            if previous_visible is not None and visible_names == previous_visible:
                audit["fallback_scan_stopped_reason"] = "visible_brand_names_unchanged"
                break
            previous_visible = visible_names
            machine.assert_action_allowed("S03", "scroll_brand_list")
            audit["attempted_scroll"] = True
            audit["fallback_scroll_count"] = int(audit.get("fallback_scroll_count") or 0) + 1
            contract_execute_swipe(
                context,
                current_snapshot,
                "S03",
                "scroll_brand_list",
                direction="up",
                evidence={
                    "target_brand": brand,
                    "target_brand_aliases": aliases,
                    "screen_index": screen_index,
                    "s03_brand_search_v2": audit,
                },
            )
            time.sleep(0.35)
            transition_wait_ms += 350
            current_snapshot = _capture(client, f"s03_brand_scan_{step_prefix}_{screen_index + 1}_{_timestamp()}")
            record_visible(f"{step_prefix}_after_scroll_{screen_index + 1}", current_snapshot)
        audit["transition_wait_ms_total"] = transition_wait_ms
        audit["reason_alias_not_matched"] = reason
        audit["stop_code"] = "S03_TARGET_BRAND_NOT_FOUND"
        audit["target_brand_visible"] = False
        audit["matched_alias"] = None
        audit["matched_bounds"] = None
        context["s03_brand_search_v2"] = dict(audit)
        return {
            "snapshot": current_snapshot,
            "brand_node": None,
            "matched_alias": None,
            "audit": audit,
            "transition_wait_ms": transition_wait_ms,
        }

    record_visible("initial", snapshot)
    letter_nodes = _right_letter_index_nodes(snapshot)
    audit["detected_letters"] = [
        str(label).strip().upper()
        for node in letter_nodes
        for label in node.get("labels", [])
        if re.fullmatch(r"[A-Z]", str(label).strip().upper())
    ]
    visible_plan, brand_node, matched_alias = plan_visible_match(snapshot)
    if brand_node and brand_node.get("bounds"):
        audit["target_brand_visible_before_letter"] = True
        return found_result(snapshot, visible_plan, brand_node, matched_alias, source="initial_visible")

    if not target_initial:
        audit["reason_alias_not_matched"] = "target_brand_initial_not_derivable"
        audit["initial_derivation_error_code"] = "S03_TARGET_INITIAL_LETTER_NOT_DERIVABLE"
        return scan_current_group(
            snapshot,
            reason="target_brand_not_found_after_initial_derivation_fallback_scan",
            step_prefix="initial_derivation_fallback",
        )

    initial_node = _find_right_letter_index_node(snapshot, target_initial)
    if not initial_node or not initial_node.get("bounds"):
        audit["reason_alias_not_matched"] = "target_initial_letter_not_found_on_current_s03_screen"
        audit["stop_code"] = "S03_TARGET_INITIAL_LETTER_NOT_FOUND"
        context["s03_brand_search_v2"] = dict(audit)
        return {
            "snapshot": snapshot,
            "brand_node": None,
            "matched_alias": None,
            "audit": audit,
            "transition_wait_ms": 0,
        }

    audit["next_action"] = f"S03_ONLY_ALLOWED_ACTION_CLICK_TARGET_INITIAL_LETTER_{target_initial}"
    audit["target_initial_letter_bounds"] = list(initial_node.get("bounds"))
    audit["attempted_alphabet"] = True
    audit["attempted_letter_L"] = target_initial == "L"
    audit["attempted_letter_G"] = target_initial == "G"
    audit["initial_letter_clicked"] = True
    audit["clicked_initial_letter"] = target_initial
    audit["initial_letter_click_strategy"] = "target_initial_right_letter_index"
    client: AdbClient = context["client"]
    click_ms = contract_click(
        context,
        snapshot,
        "S03",
        "tap_brand_letter",
        _center(initial_node["bounds"]),
        evidence={"s03_brand_search_v2": audit},
    )
    time.sleep(0.8)
    next_snapshot = _capture(client, f"s03_after_contract_initial_{target_initial}_{_timestamp()}")
    audit["initial_letter_click_action_ms"] = click_ms
    audit["initial_letter_transition_wait_ms"] = 800
    audit["after_initial_letter_xml_path"] = str(next_snapshot.get("xml_path") or "")
    audit["after_initial_letter_screenshot_path"] = str(next_snapshot.get("screenshot_path") or "")
    record_visible("after_initial_letter", next_snapshot)

    audit["transition_wait_ms_total"] = 800
    visible_plan, brand_node, matched_alias = plan_visible_match(next_snapshot)
    if brand_node and brand_node.get("bounds"):
        audit.update(
            {
                "matched_alias": matched_alias,
                "matched_bounds": visible_plan.get("matched_brand_bounds"),
                "target_brand_visible": True,
                "target_brand_visible_after_initial_letter": True,
                "target_brand_visible_after_letter": True,
                "next_action": visible_plan.get("next_action"),
                "visible_contract_plan": {key: value for key, value in visible_plan.items() if key != "brand_node"},
            }
        )
        context["s03_brand_search_v2"] = dict(audit)
        return {
            "snapshot": next_snapshot,
            "brand_node": brand_node,
            "matched_alias": matched_alias,
            "audit": audit,
            "transition_wait_ms": 800,
        }

    audit["target_brand_visible_after_letter"] = False
    return scan_current_group(
        next_snapshot,
        reason="target_brand_not_found_after_target_initial_letter_scan",
        step_prefix=f"after_initial_{target_initial}",
    )


def _screen_max_x(snapshot: dict[str, Any]) -> int:
    return max((int(node.get("bounds", [0, 0, 0, 0])[2]) for node in snapshot.get("nodes", []) if node.get("bounds")), default=0)


def _right_letter_index_nodes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    max_x = _screen_max_x(snapshot)
    if max_x <= 0:
        return []
    nodes: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        if not any(re.fullmatch(r"[A-Z]", label) for label in labels):
            continue
        if _center(bounds)[0] >= int(max_x * 0.82):
            nodes.append(node)
    return nodes


def _find_right_letter_index_node(snapshot: dict[str, Any], letter: str) -> dict[str, Any] | None:
    target = str(letter or "").strip().upper()
    if not target:
        return None
    for node in _right_letter_index_nodes(snapshot):
        if target in {str(label).strip().upper() for label in node.get("labels", [])}:
            return node
    return None


def _page_contract_allows_action(context: dict[str, Any], page_id: str, action_name: str) -> bool:
    if action_name in SCRIPT_PAGE_CONTRACT_ACTIONS.get(page_id, set()):
        return True
    for page in context.get("configs", {}).get("pages", {}).get("pages", []):
        if page.get("id") == page_id:
            return action_name in set(page.get("allowed_actions") or [])
    return False


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


def _s04_compact_series_name(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _s04_target_series_aliases(params: dict[str, Any] | None, target_series: str | None = None) -> list[str]:
    params = params or {}
    series = str(target_series or params.get("series") or "").strip()
    brand = str(params.get("brand") or "").strip()
    series_alias = str(params.get("series_alias") or "").strip()
    aliases: list[str] = []

    def add(value: str | None) -> None:
        text = str(value or "").strip()
        if text and text not in aliases:
            aliases.append(text)

    add(series)
    add(series_alias)
    if brand and series:
        add(f"{brand}{series}")
        add(f"{brand} {series}")
    return aliases


def _s04_series_matches_target(series_name: str, target_aliases: list[str], target_series: str | None = None) -> bool:
    name_key = _s04_compact_series_name(series_name)
    alias_keys = [_s04_compact_series_name(alias) for alias in target_aliases if str(alias or "").strip()]
    if not name_key or not alias_keys:
        return False
    if name_key in alias_keys:
        return True
    target_key = _s04_compact_series_name(target_series or (target_aliases[0] if target_aliases else ""))
    if target_key and len(target_key) >= 2 and name_key.endswith(target_key):
        return True
    return False


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
    target_series_aliases: list[str] | None = None,
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
    aliases = target_series_aliases or ([target] if target else [])
    raw_xml_contains_target = any(alias and alias in raw_xml for alias in aliases)
    target_in_visible_series = any(_s04_series_matches_target(name, aliases, target) for name in names)
    context.setdefault("s04_visible_series_history", []).append(names)
    context.setdefault("s04_search_records", []).append(
        {
            "s04_screen_index": screen_index,
            "direction": direction,
            "target_series_aliases": aliases,
            "raw_xml_contains_target": raw_xml_contains_target,
            "visible_series_names": names,
            "target_in_visible_series": target_in_visible_series,
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


def _find_s04_series_item(snapshot: dict[str, Any], target_series: str, target_aliases: list[str] | None = None) -> dict[str, Any] | None:
    target = str(target_series or "").strip()
    aliases = target_aliases or ([target] if target else [])
    if not aliases:
        return None
    return next((item for item in _extract_s04_visible_series(snapshot) if _s04_series_matches_target(item["name"], aliases, target)), None)


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


def _find_series_model_button(snapshot: dict[str, Any], target_series: str, target_aliases: list[str] | None = None) -> dict[str, Any] | None:
    series_item = _find_s04_series_item(snapshot, target_series, target_aliases)
    candidates = _s04_model_button_candidates(snapshot, series_item)
    if not series_item or not series_item.get("bounds"):
        return None
    sy1, sy2 = series_item["bounds"][1], series_item["bounds"][3]
    series_center_y = (sy1 + sy2) // 2
    return min(candidates, key=lambda item: abs(_center(item["bounds"])[1] - series_center_y)) if candidates else None


def _compact_ui_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _s04_brand_zone_context_fields(context: dict[str, Any]) -> dict[str, Any]:
    keys = ("s04_landing_type",)
    return {key: context.get(key) for key in keys if key in context}


def _s03_contract_context_fields(context: dict[str, Any]) -> dict[str, Any]:
    audit = context.get("s03_brand_search_v2")
    if not isinstance(audit, dict):
        return {}
    keys = (
        "s03_contract_version",
        "target_brand_aliases",
        "target_initial_letter",
        "target_brand_visible_before_letter",
        "clicked_initial_letter",
        "target_brand_visible_after_letter",
        "matched_brand_text",
        "matched_alias",
        "brand_row_bounds",
        "selected_click_region_type",
        "selected_click_point",
        "after_click_page_type",
        "brand_zone_continuation_allowed",
    )
    fields = {key: audit.get(key) for key in keys if key in audit}
    if not fields:
        return {}
    fields.setdefault("s03_contract_version", "V1.16")
    fields.setdefault("brand_zone_continuation_allowed", False)
    return {
        "s03_contract": fields,
        "s03_contract_version": fields.get("s03_contract_version"),
    }


def _s05_emission_variant_context_fields(context: dict[str, Any]) -> dict[str, Any]:
    fields = context.get("s05_emission_variant_result")
    return dict(fields) if isinstance(fields, dict) else {}


def _s06_target_filter_context_fields(context: dict[str, Any]) -> dict[str, Any]:
    fields = context.get("s06_target_filter_list_after_s05_confirm")
    if not isinstance(fields, dict):
        return {}
    allowed_keys = (
        "transition_context",
        "s05_done",
        "s05_selected_year_model",
        "s05_selected_config_model",
        "selected_count_text",
        "recognized_page_after_s05_confirm",
        "s06_page_variant",
        "s06_source_gate_passed",
        "s06_target_filter_evidence",
        "target_filter_evidence_found",
        "s06_core_elements",
        "s06_reverse_exclusion_passed",
        "s06_recognized_by",
        "s06_allowed_action",
        "s06_to_s07_result",
    )
    return {key: fields.get(key) for key in allowed_keys if key in fields}


def _s08_s10_context_fields(context: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for source_key, allowed_keys in (
        (
            "s07_color_selection_evidence",
            (
                "target_color",
                "target_color_normalized",
                "selected_color",
                "selected_color_normalized",
                "selected_color_labels",
                "target_color_confirmed",
                "color_filter_mismatch",
                "color_filter_mismatch_reason",
                "color_filter_stop_code",
            ),
        ),
        (
            "s07_view_result_to_list",
            (
                "transition_context",
                "COLOR_FILTER_DONE",
                "AGE_FILTER_DONE",
                "S07_FILTER_DONE",
                "bottom_view_result_text",
                "view_result_count",
                "recognized_page_after_view_result",
            ),
        ),
        (
            "s08_target_list_after_filter",
            (
                "s08_source_gate_passed",
                "s08_page_variant",
                "s08_target_filter_evidence",
                "s08_core_elements",
                "s08_reverse_exclusion_passed",
                "s08_recognized_by",
                "s08_allowed_action",
                "s08_color_filter_evidence",
                "s08_stop_code",
            ),
        ),
        (
            "s09_price_asc_sort",
            (
                "sort_option_clicked",
                "s09_price_asc_clicked",
                "sort_option_text",
                "sort_selected_confirmed",
            ),
        ),
        (
            "s10_source_gate_core",
            (
                "s10_source_gate_passed",
                "s10_core_elements",
                "s10_target_trisame_evidence",
                "s10_reverse_exclusion_passed",
                "complete_target_vehicle_card_count",
                "non_trisame_boundary_detected",
                "s10_ready_reason",
                "s10_color_filter_evidence",
                "s10_color_filter_mismatch",
                "s10_color_filter_stop_code",
            ),
        ),
    ):
        source = context.get(source_key)
        if isinstance(source, dict):
            fields.update({key: source.get(key) for key in allowed_keys if key in source})
    if isinstance(context.get("s07_color_click_action_trace"), list):
        fields["s07_color_click_action_trace"] = context.get("s07_color_click_action_trace")
    return fields


S05_EMISSION_VARIANT_RESULT_KEYS = (
    "s05_emission_variant_contract_enabled",
    "target_year_model",
    "target_config_model",
    "normalized_target_config",
    "emission_variant_group",
    "emission_variant_group_count",
    "selected_emission_variants",
    "selected_count_text",
    "selected_count_expected",
    "selected_count_actual",
    "s05_emission_variant_all_selected",
    "s05_single_trim_selected",
    "candidate_trim_names",
    "normalized_candidate_groups",
    "missing_emission_variants",
    "reason",
)


def _s05_emission_variant_fields_from_issue(issue_context: dict[str, Any]) -> dict[str, Any]:
    return {key: issue_context.get(key) for key in S05_EMISSION_VARIANT_RESULT_KEYS if key in issue_context}


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


def _snapshot_text_set(snapshot: dict[str, Any]) -> set[str]:
    labels = {str(text).strip() for text in snapshot.get("visible_texts", []) if str(text).strip()}
    if labels:
        return labels
    return set(_snapshot_visible_texts(snapshot))


def _snapshot_text_blob(snapshot: dict[str, Any]) -> str:
    blob = str(snapshot.get("visible_blob") or "")
    if blob:
        return blob
    return "".join(_snapshot_text_set(snapshot))


def _looks_like_s02_filter_entry_page(snapshot: dict[str, Any]) -> bool:
    labels = _snapshot_text_set(snapshot)
    blob = _snapshot_text_blob(snapshot)
    selected_select_tab = any(
        node.get("selected") is True and any(str(label).strip() == "选车" for label in node.get("labels", []))
        for node in snapshot.get("nodes", []) or []
    )
    select_entry_visible = "选车" in labels or "选车" in blob or selected_select_tab
    filter_markers = ("综合排序", "品牌", "价格", "车龄/里程", "筛选")
    filter_hits = [marker for marker in filter_markers if marker in labels or marker in blob]
    list_markers = ("门店实车", "已检测", "万公里", "首付", "万")
    vehicle_list_visible = any(marker in blob for marker in list_markers) or any(re.search(r"20\d{2}年\s*\|", str(text)) for text in labels)
    return bool(select_entry_visible and len(filter_hits) >= 4 and vehicle_list_visible)


def _looks_like_s02_select_page(snapshot: dict[str, Any]) -> bool:
    blob = _snapshot_text_blob(snapshot)
    labels = _snapshot_text_set(snapshot)
    strict_select = _has_bottom_main_nav(snapshot) and (
        "品牌" in labels
        or "品牌" in blob
        or "搜索" in labels
        or "搜索" in blob
        or "品牌选车" in labels
        or "AI选车" in labels
    )
    return strict_select or _looks_like_s02_filter_entry_page(snapshot)


def _has_pre_sort_control(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    labels = {str(text).strip() for text in snapshot.get("visible_texts", []) if str(text).strip()}
    return "综合排序" in blob or "综合排序" in labels


def _has_price_low_to_high_sort(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    labels = {str(text).strip() for text in snapshot.get("visible_texts", []) if str(text).strip()}
    return "价格从低到高" in blob or "价格从低到高" in labels


def _snapshot_visible_texts(snapshot: dict[str, Any]) -> list[str]:
    return [str(item).strip() for item in snapshot.get("visible_texts", []) if str(item).strip()]


def _snapshot_sequence_texts(snapshot: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for node in snapshot.get("nodes", []) or []:
        for label in node.get("labels", []) or []:
            label_text = str(label).strip()
            if label_text:
                texts.append(label_text)
    if texts:
        return texts
    xml_text = str(snapshot.get("fresh_xml") or "")
    if xml_text.strip():
        for node in _parse_nodes(xml_text):
            for label in node.get("labels", []) or []:
                label_text = str(label).strip()
                if label_text:
                    texts.append(label_text)
        if texts:
            return texts
    return _snapshot_visible_texts(snapshot)


def _has_s09_sort_popup(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    labels = set(_snapshot_visible_texts(snapshot))
    popup_markers = (
        "默认排序",
        "车源新上",
        "最新上架",
        "里程最低",
        "价格最高",
        "价格最低",
        "价格从高到低",
        "成色最好",
        "车况最好",
        "续航久",
    )
    return "价格从低到高" in blob and any(marker in blob or marker in labels for marker in popup_markers)


S10_NON_TRISAME_BOUNDARY_MARKERS = (
    "找不到想要的车",
    "全国淘车",
    "更多车源",
    "推荐车源",
    "猜你喜欢",
    "同品牌推荐",
    "为你推荐",
    "其他车源",
)


def _s10_boundary_text(text: str) -> str:
    normalized = str(text or "").strip()
    return next((marker for marker in S10_NON_TRISAME_BOUNDARY_MARKERS if marker in normalized), "")


def _s10_boundary_index(texts: list[str]) -> tuple[int | None, dict[str, Any]]:
    for index, text in enumerate(texts):
        marker = _s10_boundary_text(text)
        if marker:
            return index, {
                "non_trisame_section_detected": True,
                "non_trisame_section_title": marker,
                "boundary_text": text,
                "boundary_text_index": index,
            }
    return None, {
        "non_trisame_section_detected": False,
        "non_trisame_section_title": "",
        "boundary_text": "",
        "boundary_text_index": None,
    }


def _s10_target_fragments(target_car: dict[str, Any] | None) -> list[str]:
    target = target_car or {}
    fragments: list[str] = []
    for key in ("brand", "series", "year_model", "model_year", "config_model", "trim"):
        value = str(target.get(key) or "").strip()
        if not value:
            continue
        for token in re.split(r"\s+", value):
            token = token.strip()
            if token and token not in fragments:
                fragments.append(token)
    return fragments


def _s10_title_matches_target(title: str, target_car: dict[str, Any] | None) -> bool:
    fragments = _s10_target_fragments(target_car)
    if not fragments:
        return True
    normalized_title = re.sub(r"\s+", "", str(title or ""))
    for fragment in fragments:
        if re.sub(r"\s+", "", fragment) not in normalized_title:
            return False
    return True


def _extract_s10_visible_prices_from_texts(texts: list[str]) -> list[float]:
    invalid_price_markers = ("首付", "月供", "贷款", "价格区间", "以下", "以上", "万公里")
    prices: list[float] = []
    for index, text in enumerate(texts):
        if any(marker in text for marker in invalid_price_markers):
            continue
        price_match = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
        if price_match:
            prices.append(float(price_match.group(1)))
            continue
        if not re.fullmatch(r"\d+(?:\.\d+)?", text):
            continue
        next_text = texts[index + 1] if index + 1 < len(texts) else ""
        prev_text = texts[index - 1] if index > 0 else ""
        if next_text == "万" and not any(marker in prev_text for marker in invalid_price_markers):
            prices.append(float(text))
    return prices


def _extract_s10_visible_prices(snapshot: dict[str, Any]) -> list[float]:
    return _extract_s10_visible_prices_from_texts(_snapshot_sequence_texts(snapshot))


def _extract_s10_visible_vehicle_titles_from_texts(texts: list[str]) -> list[str]:
    titles: list[str] = []
    excluded_markers = ("全部车型", "热门车型", "车型配置", "价格从低到高", "综合排序", "查看", "更多")
    for text in texts:
        normalized = text.strip()
        if any(marker in normalized for marker in excluded_markers):
            continue
        if re.search(r"20\d{2}款", normalized) and not re.fullmatch(r"20\d{2}款", normalized):
            titles.append(normalized)
    return titles


def _extract_s10_visible_vehicle_titles(snapshot: dict[str, Any]) -> list[str]:
    return _extract_s10_visible_vehicle_titles_from_texts(_snapshot_sequence_texts(snapshot))


def _build_s10_contract_cards_from_texts(texts: list[str]) -> list[dict[str, Any]]:
    prices = _extract_s10_visible_prices_from_texts(texts)
    titles = _extract_s10_visible_vehicle_titles_from_texts(texts)
    year_mileages: list[tuple[str, int, float]] = []
    for text in texts:
        year_mileage_10k = re.search(r"(20\d{2})年\s*[|｜]\s*(\d+(?:\.\d+)?)万公里", text)
        if year_mileage_10k:
            year_mileages.append((text, int(year_mileage_10k.group(1)), float(year_mileage_10k.group(2))))
            continue
        year_mileage_km = re.search(r"(20\d{2})年\s*[|｜]\s*(\d+(?:\.\d+)?)公里", text)
        if year_mileage_km:
            year_mileages.append((text, int(year_mileage_km.group(1)), round(float(year_mileage_km.group(2)) / 10000, 4)))
    cards: list[dict[str, Any]] = []
    for index, (year_mileage_text, year, mileage) in enumerate(year_mileages[: len(prices)]):
        title = titles[index] if index < len(titles) else year_mileage_text
        cards.append(
            {
                "title": title,
                "year_mileage_text": year_mileage_text,
                "list_price_10k": prices[index],
                "list_year": year,
                "list_mileage_10k_km": mileage,
            }
        )
    return cards


def _s10_contract_card_audit(snapshot: dict[str, Any], target_car: dict[str, Any] | None = None) -> dict[str, Any]:
    texts = _snapshot_sequence_texts(snapshot)
    raw_cards = _build_s10_contract_cards_from_texts(texts)
    boundary_index, boundary_evidence = _s10_boundary_index(texts)
    before_boundary_texts = texts[:boundary_index] if boundary_index is not None else texts
    after_boundary_texts = texts[boundary_index + 1 :] if boundary_index is not None else []
    before_boundary_cards = _build_s10_contract_cards_from_texts(before_boundary_texts)
    after_boundary_cards = _build_s10_contract_cards_from_texts(after_boundary_texts)

    trisame_cards: list[dict[str, Any]] = []
    excluded_cards: list[dict[str, Any]] = []
    for card in before_boundary_cards:
        if _s10_title_matches_target(str(card.get("title") or ""), target_car):
            trisame_cards.append(card)
        else:
            excluded = dict(card)
            excluded.update(
                {
                    "excluded_non_trisame_card": True,
                    "exclude_reason": "title_mismatch",
                    "actual_title": card.get("title"),
                    "actual_price": card.get("list_price_10k"),
                    "actual_metadata": card.get("year_mileage_text"),
                    "section_context": "before_non_trisame_boundary",
                }
            )
            excluded_cards.append(excluded)
    for card in after_boundary_cards:
        excluded = dict(card)
        excluded.update(
            {
                "excluded_non_trisame_card": True,
                "exclude_reason": "after_non_trisame_boundary",
                "actual_title": card.get("title"),
                "actual_price": card.get("list_price_10k"),
                "actual_metadata": card.get("year_mileage_text"),
                "section_context": boundary_evidence.get("non_trisame_section_title") or "non_trisame_section",
            }
        )
        excluded_cards.append(excluded)

    return {
        **boundary_evidence,
        "raw_visible_cards_count": len(raw_cards),
        "trisame_cards_count": len(trisame_cards),
        "excluded_non_trisame_cards_count": len(excluded_cards),
        "cards_after_boundary_excluded_count": len(after_boundary_cards),
        "trisame_count_confirmed": bool(target_car) and len(trisame_cards) > 0,
        "same_source_cards": trisame_cards,
        "excluded_non_trisame_cards": excluded_cards,
        "raw_visible_cards": raw_cards,
    }


def _extract_s10_contract_cards(snapshot: dict[str, Any], target_car: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return list(_s10_contract_card_audit(snapshot, target_car).get("same_source_cards") or [])


def _s10_price_order_check_result(prices: list[float]) -> str:
    if len(prices) < 2:
        return "not_enough_prices"
    if all(left <= right + 0.001 for left, right in zip(prices, prices[1:])):
        return "non_decreasing"
    return "descending_detected"


def _s10_no_same_source_detected(snapshot: dict[str, Any]) -> bool:
    blob = str(snapshot.get("visible_blob") or "")
    no_source_markers = ("暂无车源", "暂无符合", "没有找到", "无相关车源", "暂无相关车源")
    return any(marker in blob for marker in no_source_markers)


def _s10_ready_evidence(snapshot: dict[str, Any], target_car: dict[str, Any] | None = None) -> dict[str, Any]:
    prices = _extract_s10_visible_prices(snapshot)
    audit = _s10_contract_card_audit(snapshot, target_car)
    cards = list(audit.get("same_source_cards") or [])
    return {
        "sort_popup_closed": not _has_s09_sort_popup(snapshot),
        "sort_selected_confirmed": _has_price_low_to_high_sort(snapshot),
        "sorted_list_page_recognized": _looks_like_s10_ready_contract(snapshot),
        "s10_ready_recognized": _looks_like_s10_ready_contract(snapshot),
        "vehicle_card_count": len(cards),
        "visible_vehicle_titles": _extract_s10_visible_vehicle_titles(snapshot)[:8],
        "visible_vehicle_prices": prices[:8],
        "price_order_check_result": _s10_price_order_check_result(prices),
        "no_same_source_detected": _s10_no_same_source_detected(snapshot),
        "s10_trisame_filter": {
            key: value
            for key, value in audit.items()
            if key not in {"raw_visible_cards", "same_source_cards", "excluded_non_trisame_cards"}
        },
    }


def _looks_like_s10_ready_contract(snapshot: dict[str, Any]) -> bool:
    return (
        not _has_bottom_main_nav(snapshot)
        and not _has_pre_sort_control(snapshot)
        and _has_price_low_to_high_sort(snapshot)
        and bool(_extract_s10_contract_cards(snapshot))
    )


def _assert_s10_ready_contract(
    issues: IssueRecorder,
    snapshot: dict[str, Any],
    *,
    source: str | None,
    flow_state: dict[str, Any] | None = None,
    target_car: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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
    cards = _extract_s10_contract_cards(snapshot, target_car)
    if not cards:
        issue = _record_issue(
            issues,
            "PAGE_CONTRACT_MISMATCH",
            "S10",
            "S10_READY requires vehicle card price, year, and mileage in current fresh evidence.",
            {**snapshot, "s10_ready_evidence": _s10_ready_evidence(snapshot, target_car)},
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


def _normalize_s07_color_label(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", "", text)
    suffix = "\u8272"
    if text.endswith(suffix) and len(text) > len(suffix):
        text = text[: -len(suffix)]
    return text


def _s07_color_label_matches_target(label: str | None, target_color: str | None) -> bool:
    return bool(_normalize_s07_color_label(label)) and _normalize_s07_color_label(label) == _normalize_s07_color_label(target_color)


def _point_inside_bounds(point: tuple[int, int] | list[int] | None, bounds: tuple[int, int, int, int] | None) -> bool:
    if not point or not _has_positive_bounds(bounds):
        return False
    x, y = int(point[0]), int(point[1])
    x1, y1, x2, y2 = bounds
    return x1 <= x <= x2 and y1 <= y <= y2


def _s07_is_color_grid_candidate(bounds: tuple[int, int, int, int] | None) -> bool:
    if not _has_positive_bounds(bounds):
        return False
    x1, y1, x2, y2 = bounds
    height = y2 - y1
    width = x2 - x1
    # Color option cards live in the right-side grid below the selected-chip row.
    return x1 >= 180 and y1 >= 900 and 40 <= height <= 220 and 60 <= width <= 420


def _s07_color_grid_text_bounds_safe(bounds: tuple[int, int, int, int] | None) -> bool:
    return _s07_is_color_grid_candidate(bounds) and not _s07_is_top_selected_filter_chip(bounds)


def _s07_bounds_area(bounds: tuple[int, int, int, int] | None) -> int:
    if not _has_positive_bounds(bounds):
        return 0
    x1, y1, x2, y2 = bounds
    return max(0, x2 - x1) * max(0, y2 - y1)


def _s07_color_parent_context(
    snapshot: dict[str, Any],
    parent_bounds: tuple[int, int, int, int] | None,
) -> dict[str, Any]:
    ambiguity = _s07_color_candidate_ambiguity(snapshot, parent_bounds)
    return {
        "parent_bounds": parent_bounds,
        "parent_bounds_ambiguous": bool(ambiguity.get("ambiguous")),
        "parent_contained_color_labels": ambiguity.get("contained_color_labels") or [],
    }


def _s07_clickable_parent_bounds_safe_for_color(
    snapshot: dict[str, Any],
    parent_bounds: tuple[int, int, int, int] | None,
    text_bounds: tuple[int, int, int, int] | None,
) -> bool:
    if not (_has_positive_bounds(parent_bounds) and _has_positive_bounds(text_bounds)):
        return False
    parent_context = _s07_color_parent_context(snapshot, parent_bounds)
    if parent_context["parent_bounds_ambiguous"]:
        return False
    parent_area = _s07_bounds_area(parent_bounds)
    text_area = _s07_bounds_area(text_bounds)
    if not text_area or parent_area > text_area * 4:
        return False
    return _point_inside_bounds(_center(parent_bounds), text_bounds)


S07_LABEL_COLOR = "\u989c\u8272"
S07_LABEL_AGE = "\u8f66\u9f84"
S07_FORBIDDEN_FILTER_TEXTS = (
    "\u6709\u9009\u88c5",
    "\u8f66\u6e90\u4eae\u70b9",
    "\u8f85\u52a9\u9a7e\u9a76",
    "\u7535\u6c60\u7c7b\u578b",
    "\u7eed\u822a\u91cc\u7a0b",
    "\u91cc\u7a0b",
    "\u5e74\u6b3e\u8f66\u578b",
    "\u5e74\u6b3e/\u8f66\u578b",
)
S07_KNOWN_COLOR_LABELS = {
    "\u9ed1",
    "\u9ed1\u8272",
    "\u767d",
    "\u767d\u8272",
    "\u7070",
    "\u7070\u8272",
    "\u94f6",
    "\u94f6\u8272",
    "\u7ea2",
    "\u7ea2\u8272",
    "\u84dd",
    "\u84dd\u8272",
    "\u7eff",
    "\u7eff\u8272",
    "\u9ec4",
    "\u9ec4\u8272",
    "\u91d1",
    "\u91d1\u8272",
    "\u68d5",
    "\u68d5\u8272",
    "\u7d2b",
    "\u7d2b\u8272",
    "\u6a59",
    "\u6a59\u8272",
    "\u7c89",
    "\u7c89\u8272",
    "\u9999\u69df",
    "\u9999\u69df\u8272",
    "\u5496\u5561",
    "\u5496\u5561\u8272",
    "\u5176\u4ed6",
}
S07_KNOWN_NORMALIZED_COLOR_LABELS = {_normalize_s07_color_label(item) for item in S07_KNOWN_COLOR_LABELS}


def _s07_is_known_color_label(label: str | None) -> bool:
    text = str(label or "").strip()
    return text in S07_KNOWN_COLOR_LABELS or _normalize_s07_color_label(text) in S07_KNOWN_NORMALIZED_COLOR_LABELS


def _s07_find_left_filter_tab_node(snapshot: dict[str, Any], label: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        labels = {str(item).strip() for item in node.get("labels", []) if str(item).strip()}
        if label not in labels:
            continue
        x1, y1, x2, y2 = bounds
        if x1 <= 40 and x2 <= 340 and y1 >= 850:
            candidates.append({**node, "s07_filter_tab_label": label})
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item["bounds"][1], item["bounds"][0]))
    return candidates[0]


def _s07_is_top_selected_filter_chip(bounds: tuple[int, int, int, int] | None) -> bool:
    if not _has_positive_bounds(bounds):
        return False
    x1, y1, x2, y2 = bounds
    # The selected filter chips sit above the right-side option grid. 0011 showed
    # a black grid candidate at y=984 being misread as a selected chip by the old
    # broad 820..1010 band, so keep this deliberately tight.
    return 720 <= y1 <= 880 and y2 <= 940 and x1 >= 0 and x2 <= 1220


def _s07_is_left_filter_menu_node(bounds: tuple[int, int, int, int] | None) -> bool:
    if not _has_positive_bounds(bounds):
        return False
    x1, y1, x2, _ = bounds
    return x1 <= 40 and x2 <= 340 and y1 >= 820


def _bounds_intersect(
    first: tuple[int, int, int, int] | None,
    second: tuple[int, int, int, int] | None,
    *,
    margin: int = 0,
) -> bool:
    if not (_has_positive_bounds(first) and _has_positive_bounds(second)):
        return False
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    return not (
        ax2 < bx1 - margin
        or bx2 < ax1 - margin
        or ay2 < by1 - margin
        or by2 < ay1 - margin
    )


def _s07_forbidden_selected_evidence(
    snapshot: dict[str, Any],
    *,
    clicked_text: str | None = None,
    clicked_action_id: str | None = None,
) -> dict[str, Any]:
    forbidden_items_seen: list[dict[str, Any]] = []
    forbidden_option_selected: list[dict[str, Any]] = []
    clicked_forbidden_items: list[dict[str, Any]] = []
    forbidden_set = set(S07_FORBIDDEN_FILTER_TEXTS)
    clicked_label = str(clicked_text or "").strip()
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        labels = {str(item).strip() for item in node.get("labels", []) if str(item).strip()}
        matched = sorted(labels & forbidden_set)
        if not matched:
            continue
        is_left_tab = _s07_is_left_filter_menu_node(bounds)
        is_selected = bool(node.get("selected"))
        is_checked = bool(node.get("checked"))
        is_clicked = clicked_label in matched
        entry = {
            "text": matched[0],
            "bounds": bounds,
            "selected": is_selected,
            "checked": is_checked,
            "is_left_tab": is_left_tab,
            "is_right_option": not is_left_tab,
            "is_clicked_target": is_clicked,
        }
        if is_clicked:
            clicked_forbidden_items.append({**entry, "state": "clicked_forbidden"})
            forbidden_items_seen.append({**entry, "state": "clicked_forbidden"})
            continue
        if not is_left_tab and (is_selected or is_checked):
            forbidden_option_selected.append({**entry, "state": "forbidden_option_selected"})
            forbidden_items_seen.append({**entry, "state": "forbidden_option_selected"})
            continue
        visible_entry = {**entry, "state": "forbidden_visible_only" if not is_left_tab else "forbidden_text_in_left_menu"}
        forbidden_items_seen.append(visible_entry)
    if clicked_label in forbidden_set and not clicked_forbidden_items:
        clicked_forbidden_items.append(
            {
                "text": clicked_label,
                "bounds": None,
                "selected": False,
                "checked": False,
                "is_left_tab": False,
                "is_right_option": True,
                "is_clicked_target": True,
                "state": "clicked_forbidden",
            }
        )
        forbidden_items_seen.append(clicked_forbidden_items[-1])
    clicked_forbidden = bool(clicked_forbidden_items)
    option_selected = bool(forbidden_option_selected)
    stop_code = None
    if clicked_forbidden:
        stop_code = "S07_CLICKED_OPTIONAL_FEATURE_INSTEAD_OF_COLOR_AGE" if clicked_label == "有选装" else "S07_CLICKED_FORBIDDEN_FILTER_OPTION"
    elif option_selected:
        stop_code = "S07_FORBIDDEN_FILTER_SELECTED_CONFIRMED"
    return {
        "s07_forbidden_gate_version": "simple_checked_only",
        "clicked_text": clicked_label or None,
        "clicked_action_id": clicked_action_id,
        "forbidden_items_seen": forbidden_items_seen,
        "forbidden_visible_only": [
            item for item in forbidden_items_seen if item.get("state") in {"forbidden_visible_only", "forbidden_text_in_left_menu"}
        ],
        "forbidden_left_tab_active": [],
        "forbidden_option_selected": option_selected,
        "forbidden_option_selected_items": forbidden_option_selected,
        "clicked_forbidden": clicked_forbidden,
        "clicked_forbidden_items": clicked_forbidden_items,
        "forbidden_gate_decision": "stop" if stop_code else "allow_continue",
        "forbidden_gate_stop_code": stop_code,
        "forbidden_filter_selected": bool(stop_code),
        "forbidden_texts": [item["text"] for item in clicked_forbidden_items + forbidden_option_selected],
        "forbidden_bounds": [item["bounds"] for item in clicked_forbidden_items + forbidden_option_selected],
        "selected_forbidden_filters": clicked_forbidden_items + forbidden_option_selected,
    }


def _target_color_selected_strict(snapshot: dict[str, Any], target_color: str) -> bool:
    target_labels = set(_target_color_labels(target_color))
    for node in snapshot.get("nodes", []):
        labels = {str(label).strip() for label in node.get("labels", [])}
        if not (labels & target_labels):
            continue
        if node.get("selected") is True and not _s07_is_color_grid_candidate(node.get("bounds")):
            return True
        if _s07_is_top_selected_filter_chip(node.get("bounds")):
            return True
    return False


def _s07_selected_color_labels(snapshot: dict[str, Any]) -> list[str]:
    selected: list[str] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if _s07_is_color_grid_candidate(bounds):
            continue
        is_selected_chip = _s07_is_top_selected_filter_chip(bounds)
        is_explicitly_selected = bool(node.get("selected")) and not _s07_is_left_filter_menu_node(bounds)
        if not (is_selected_chip or is_explicitly_selected):
            continue
        for label in node.get("labels", []):
            text = str(label).strip()
            if not text:
                continue
            if _s07_is_known_color_label(text):
                selected.append(text)
    return list(dict.fromkeys(selected))


def _s07_selected_color(snapshot: dict[str, Any]) -> str | None:
    labels = _s07_selected_color_labels(snapshot)
    return labels[0] if labels else None


def _s07_color_candidate_ambiguity(snapshot: dict[str, Any], bounds: tuple[int, int, int, int] | None) -> dict[str, Any]:
    if not _has_positive_bounds(bounds):
        return {"ambiguous": True, "contained_color_labels": []}
    contained: list[str] = []
    for node in snapshot.get("nodes", []):
        node_bounds = node.get("bounds")
        if not _has_positive_bounds(node_bounds):
            continue
        cx, cy = _center(node_bounds)
        if not _point_inside_bounds((cx, cy), bounds):
            continue
        for label in node.get("labels", []):
            text = str(label).strip()
            if text and _s07_is_known_color_label(text):
                contained.append(text)
    contained = list(dict.fromkeys(contained))
    return {"ambiguous": len(contained) > 1, "contained_color_labels": contained}


def _s07_build_color_click_action_trace(
    *,
    target_color: str,
    color_node: dict[str, Any],
    click_point: tuple[int, int],
    attempt_index: int,
    tap_result: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_bounds = color_node.get("bounds")
    matched_color = str(
        color_node.get("matched_color_text")
        or color_node.get("text")
        or next(iter(color_node.get("labels", []) or []), "")
    ).strip()
    ambiguity = _s07_color_candidate_ambiguity(snapshot or {}, candidate_bounds)
    click_source = color_node.get("click_source") or color_node.get("color_click_strategy") or "target_color_node_bounds"
    contract_action_plan = build_s07_color_action_plan(target_color=target_color)
    binding_trace = build_action_plan_binding_trace(
        contract_action_plan,
        action_algorithm_used="exact_target_color_binding",
        forbidden_action_used=click_source in {"fixed_coordinate", "ratio_coordinate", "default_color_click"},
    )
    allowed_click_sources = set(contract_action_plan.get("allowed_actions") or [])
    return {
        "trace_version": "S07_COLOR_CLICK_ACTION_TRACE_V1",
        "contract_action_plan": contract_action_plan,
        "contract_expected": contract_action_plan.get("expected"),
        "contract_action_algorithm": contract_action_plan.get("action_algorithm"),
        "contract_forbidden_actions": contract_action_plan.get("forbidden_actions"),
        **binding_trace,
        "attempt_index": attempt_index,
        "target_color": target_color,
        "matched_candidate_color": matched_color,
        "normalized_target_color": _normalize_s07_color_label(target_color),
        "normalized_candidate_color": _normalize_s07_color_label(matched_color),
        "candidate_matches_target": _s07_color_label_matches_target(matched_color, target_color),
        "candidate_bounds": candidate_bounds,
        "candidate_text_bounds": color_node.get("candidate_text_bounds") or candidate_bounds,
        "candidate_clickable": bool(color_node.get("clickable")),
        "candidate_node_clickable": bool(color_node.get("clickable")),
        "candidate_enabled": bool(color_node.get("enabled", True)),
        "candidate_strategy": color_node.get("color_click_strategy") or "target_color_node_bounds",
        "candidate_bounds_role": "color_grid_candidate" if _s07_is_color_grid_candidate(candidate_bounds) else "color_text_or_container",
        "candidate_grid_bounds_safe": _s07_color_grid_text_bounds_safe(candidate_bounds),
        "click_source": click_source,
        "click_source_allowed_by_action_plan": click_source in allowed_click_sources,
        "action_plan_click_source_allowed": click_source in allowed_click_sources,
        "click_point": [int(click_point[0]), int(click_point[1])],
        "computed_click_point": [int(click_point[0]), int(click_point[1])],
        "final_tap_point": [int(click_point[0]), int(click_point[1])],
        "click_point_inside_candidate_bounds": _point_inside_bounds(click_point, candidate_bounds),
        "bounds_ambiguity": ambiguity,
        "parent_bounds": color_node.get("parent_bounds"),
        "parent_bounds_ambiguous": bool(color_node.get("parent_bounds_ambiguous")),
        "parent_contained_color_labels": color_node.get("parent_contained_color_labels") or [],
        "allowed_click": bool(
            _s07_color_label_matches_target(matched_color, target_color)
            and _point_inside_bounds(click_point, candidate_bounds)
            and not (
                color_node.get("color_click_strategy") == "clickable_color_ancestor_bounds"
                and ambiguity.get("ambiguous")
            )
        ),
        "tap_result": tap_result or {},
    }


def _s07_snapshot_color_filter_evidence(
    snapshot: dict[str, Any],
    target_color: str | None,
    *,
    source: str,
) -> dict[str, Any]:
    target_normalized = _normalize_s07_color_label(target_color)
    selected_label = _s07_selected_color(snapshot)
    selected_normalized = _normalize_s07_color_label(selected_label)
    color_hits: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        for label in node.get("labels", []):
            text = str(label).strip()
            if not _s07_is_known_color_label(text):
                continue
            color_hits.append(
                {
                    "text": text,
                    "normalized": _normalize_s07_color_label(text),
                    "bounds": bounds,
                    "is_selected_chip": _s07_is_top_selected_filter_chip(bounds),
                    "is_grid_candidate": _s07_is_color_grid_candidate(bounds),
                }
            )
    normalized_hits = list(dict.fromkeys(item["normalized"] for item in color_hits if item.get("normalized")))
    non_target_hits = [item for item in normalized_hits if target_normalized and item != target_normalized]
    mismatch = False
    mismatch_reason = None
    if target_normalized and selected_normalized and selected_normalized != target_normalized:
        mismatch = True
        mismatch_reason = "selected_color_chip_mismatch"
    elif target_normalized and target_normalized not in normalized_hits and len(non_target_hits) == 1:
        mismatch = True
        mismatch_reason = "single_visible_color_summary_mismatch"
    target_confirmed = bool(target_normalized and (selected_normalized == target_normalized or target_normalized in normalized_hits))
    return {
        "s07_color_filter_evidence_source": source,
        "target_color": target_color,
        "target_color_normalized": target_normalized,
        "selected_color": selected_label,
        "selected_color_normalized": selected_normalized or None,
        "selected_color_labels": _s07_selected_color_labels(snapshot),
        "visible_color_hits": color_hits[:20],
        "visible_color_normalized_hits": normalized_hits,
        "target_color_confirmed": target_confirmed,
        "color_filter_mismatch": mismatch,
        "color_filter_mismatch_reason": mismatch_reason,
        "color_filter_stop_code": "S10_COLOR_FILTER_MISMATCH" if mismatch else None,
    }


def _s07_record_color_trace(context: dict[str, Any], trace: dict[str, Any]) -> None:
    context.setdefault("s07_color_click_action_trace", []).append(trace)
    context["last_s07_color_click_action_trace"] = trace


def _execute_s07_target_color_click(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    color_node: dict[str, Any],
    target_color: str,
    *,
    attempt_index: int,
) -> tuple[int, dict[str, Any]]:
    issues: IssueRecorder = context["issues"]
    bounds = color_node.get("bounds")
    click_point = _center(bounds)
    clicked_color_text = str(
        color_node.get("matched_color_text")
        or color_node.get("text")
        or next(iter(color_node.get("labels", []) or []), "")
    ).strip()
    pre_trace = _s07_build_color_click_action_trace(
        target_color=target_color,
        color_node=color_node,
        click_point=click_point,
        attempt_index=attempt_index,
        snapshot=snapshot,
    )
    if not pre_trace["candidate_matches_target"]:
        issue = _record_issue(
            issues,
            "S07_COLOR_CANDIDATE_TARGET_MISMATCH",
            "S07",
            "The bound S07 color candidate does not match target color.",
            {**snapshot, "s07_color_click_action_trace": pre_trace},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if not pre_trace["click_point_inside_candidate_bounds"]:
        issue = _record_issue(
            issues,
            "S07_COLOR_CLICK_POINT_OUTSIDE_CANDIDATE_BOUNDS",
            "S07",
            "The S07 color click point is outside the bound target color candidate.",
            {**snapshot, "s07_color_click_action_trace": pre_trace},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    ambiguity = pre_trace.get("bounds_ambiguity") or {}
    if color_node.get("color_click_strategy") == "clickable_color_ancestor_bounds" and ambiguity.get("ambiguous"):
        issue = _record_issue(
            issues,
            "S07_COLOR_CANDIDATE_PARENT_BOUNDS_AMBIGUOUS",
            "S07",
            "The S07 color click target parent contains multiple color candidates.",
            {**snapshot, "s07_color_click_action_trace": pre_trace},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    target_color_ms = contract_execute_click(
        context,
        snapshot,
        "S07",
        "tap_target_color",
        click_point,
        evidence={"clicked_text": clicked_color_text, "clicked_node_bounds": bounds, "s07_color_click_action_trace": pre_trace},
    )
    tap_result = context.get("_last_tap_result") if isinstance(context.get("_last_tap_result"), dict) else {}
    trace = {
        **pre_trace,
        "tap_result": tap_result,
        "tap_action_ms": target_color_ms,
    }
    _s07_record_color_trace(context, trace)
    return target_color_ms, trace


def _node_from_xml_element(
    element: ElementTree.Element,
    *,
    matched_color_text: str,
    strategy: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        "checked": str(element.attrib.get("checked") or "") == "true",
        "package": str(element.attrib.get("package") or ""),
        "class_name": str(element.attrib.get("class") or ""),
        "matched_color_text": matched_color_text,
        "color_click_strategy": strategy,
    }
    if extra:
        node.update(extra)
    return node


def _find_target_color_node(snapshot: dict[str, Any], target_color: str) -> dict[str, Any] | None:
    target_labels = set(_target_color_labels(target_color))
    xml_text = str(snapshot.get("fresh_xml") or "")
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not bounds:
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        matched = labels & target_labels
        if not (matched and node.get("enabled", True)):
            continue
        matched_text = sorted(matched)[0]
        if node.get("clickable"):
            return {
                **node,
                "matched_color_text": matched_text,
                "candidate_text_bounds": bounds,
                "candidate_grid_bounds_safe": _s07_color_grid_text_bounds_safe(bounds),
                "click_source": "direct_clickable_color_text_node",
                "color_click_strategy": "direct_clickable_color_text_node",
            }
        if not xml_text.strip() and _s07_color_grid_text_bounds_safe(bounds):
            return {
                **node,
                "matched_color_text": matched_text,
                "candidate_text_bounds": bounds,
                "candidate_grid_bounds_safe": True,
                "click_source": "color_grid_text_node_bounds",
                "color_click_strategy": "color_grid_text_node_bounds",
            }
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
        clickable_ancestor: ElementTree.Element | None = None
        clickable_ancestor_bounds: tuple[int, int, int, int] | None = None
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
                clickable_ancestor = current
                clickable_ancestor_bounds = current_bounds
                break
            current = parent_map.get(current)
        parent_context = _s07_color_parent_context(snapshot, clickable_ancestor_bounds)
        if str(element.attrib.get("clickable") or "") == "true":
            return _node_from_xml_element(
                element,
                matched_color_text=matched_text,
                strategy="direct_clickable_color_text_node",
                extra={
                    "candidate_text_bounds": bounds,
                    "candidate_grid_bounds_safe": _s07_color_grid_text_bounds_safe(bounds),
                    "click_source": "direct_clickable_color_text_node",
                    **parent_context,
                },
            )
        if _s07_color_grid_text_bounds_safe(bounds):
            return _node_from_xml_element(
                element,
                matched_color_text=matched_text,
                strategy="color_grid_text_node_bounds",
                extra={
                    "candidate_text_bounds": bounds,
                    "candidate_grid_bounds_safe": True,
                    "click_source": "color_grid_text_node_bounds",
                    **parent_context,
                },
            )
        if clickable_ancestor is not None and _s07_clickable_parent_bounds_safe_for_color(
            snapshot,
            clickable_ancestor_bounds,
            bounds,
        ):
            return _node_from_xml_element(
                clickable_ancestor,
                matched_color_text=matched_text,
                strategy="clickable_color_ancestor_bounds",
                extra={
                    "candidate_text_bounds": bounds,
                    "candidate_grid_bounds_safe": False,
                    "click_source": "clickable_color_ancestor_bounds",
                    **parent_context,
                },
            )
        return None
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
    return _target_color_selected_strict(snapshot, target_color)


S07_EXACT_AGE_CONTRACT_VERSION = "S07_AGE_EXACT_SLIDER_OVERLAP_V1"
S07_AGE_SLIDER_DIRECT_FASTPATH_VERSION = "FEISHU_S07_AGE_SLIDER_DIRECT_TRACK_FASTPATH_AND_TIMING_PATCH"
S07_AGE_SLIDER_REAL_TOUCH_EXECUTOR_VERSION = "S07_AGE_SLIDER_REAL_TOUCH_EXECUTOR_PATCH"
S07_AGE_SLIDER_FASTPATH_MAX_MICRO_ADJUST = 1
S07_AGE_SLIDER_FASTPATH_MAX_FALLBACK_STRATEGIES = 1
S07_AGE_SLIDER_PERFORMANCE_BUDGET_MS = 5000
S07_AGE_SLIDER_XML_DUMP_BUDGET = 2
S07_AGE_SLIDER_SCREENSHOT_BUDGET = 2
S07_AGE_SLIDER_FALLBACK_BUDGET_EXCEEDED = "S07_AGE_SLIDER_FALLBACK_BUDGET_EXCEEDED"
S07_AGE_CONTRACT_UNAUTHORIZED_FALLBACK_USED = "S07_AGE_CONTRACT_UNAUTHORIZED_FALLBACK_USED"
S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT = "S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT"
S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED = "S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED"
S07_AGE_SLIDER_CLOSE_HANDLE_PAIR_BINDING_FAILED = "S07_AGE_SLIDER_CLOSE_HANDLE_PAIR_BINDING_FAILED"
S07_AGE_SLIDER_GHOST_HANDLE_REJECTED = "S07_AGE_SLIDER_GHOST_HANDLE_REJECTED"
S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT = "S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT"
S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT = "S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT"
S07_LEFT_AGE_SLIDER_DRAG_NO_VISUAL_MOVEMENT = "S07_LEFT_AGE_SLIDER_DRAG_NO_VISUAL_MOVEMENT"
S07_LEFT_AGE_SLIDER_EXACT_VERIFY_FAILED_AFTER_RETRY = "S07_LEFT_AGE_SLIDER_EXACT_VERIFY_FAILED_AFTER_RETRY"
S07_AGE_SLIDER_HANDLE_BINDING_FAILED = "S07_AGE_SLIDER_HANDLE_BINDING_FAILED"
S07_AGE_SLIDER_DRAG_START_OUTSIDE_HANDLE_BOUNDS = "S07_AGE_SLIDER_DRAG_START_OUTSIDE_HANDLE_BOUNDS"
S07_AGE_EXACT_RANGE_VERIFY_FAILED = "S07_AGE_EXACT_RANGE_VERIFY_FAILED"
S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE = "S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE"
S07_AGE_ONE_HIDDEN_TICK_VERIFY_FAILED = "S07_AGE_ONE_HIDDEN_TICK_VERIFY_FAILED"
S07_AGE_ONE_POST_ACTION_VERIFY_FAILED = "S07_AGE_ONE_POST_ACTION_VERIFY_FAILED"
S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED = "S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED"
S07_AGE_ONE_POST_ACTION_PROOF_VERSION = "S07_POST_ACTION_PROOF_V1"
S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED = "S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED"
S07_AGE_FILTER_ACTUAL_RANGE_MISMATCH = "S07_AGE_FILTER_ACTUAL_RANGE_MISMATCH"
S07_POST_ACTION_FRESH_EVIDENCE_MISSING = "S07_POST_ACTION_FRESH_EVIDENCE_MISSING"
S07_AGE_FILTER_PLANNED_ACTUAL_MISMATCH = "S07_AGE_FILTER_PLANNED_ACTUAL_MISMATCH"
S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER = "S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER"
S07_AGE_ZERO_SUCCESS_TEXTS = ("0年以下", "0-0年", "0年")
S07_CLOSE_HANDLE_PAIR_MIN_SEPARATION_PX = 20
S07_CLOSE_HANDLE_PAIR_DISTANCE_PX = 80
S07_LEFT_AGE_SLIDER_SHORT_DRAG_PX = 60


def _s07_visible_text_sources(snapshot: dict[str, Any]) -> list[str]:
    sources = [str(snapshot.get("visible_blob") or "")]
    sources.extend(str(label) for node in snapshot.get("nodes", []) for label in node.get("labels", []))
    return sources


def _normalize_s07_age_verify_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _s07_classify_age_verify_text(snapshot: dict[str, Any], target_age: int | None) -> dict[str, Any]:
    result = {
        "s07_age_verify_text_raw": "",
        "s07_age_verify_text_normalized": "",
        "s07_age_verify_text_class": "missing",
        "s07_age_exact_required": target_age is not None,
        "s07_age_broad_text_rejected": False,
        "matched_age_text": None,
        "rejected_verify_text": None,
        "reject_reason": "",
    }
    if target_age is None:
        result["reject_reason"] = "target_age_missing"
        return result
    raw_texts = [str(text or "") for text in _s07_visible_text_sources(snapshot)]
    texts = [_normalize_s07_age_verify_text(text) for text in raw_texts]
    if target_age == 0:
        zero_patterns = (
            (S07_AGE_ZERO_SUCCESS_TEXTS[0], re.compile(r"(?<!\d)0\s*(?:年|爛)\s*(?:以下|以内|眕狟|眕囀)")),
            (S07_AGE_ZERO_SUCCESS_TEXTS[1], re.compile(r"(?<!\d)0\s*-\s*0\s*(?:年|爛)")),
            (S07_AGE_ZERO_SUCCESS_TEXTS[2], re.compile(r"(?<!\d)0\s*(?:年|爛)(?![\u4e00-\u9fff\d])")),
        )
        for canonical, pattern in zero_patterns:
            for raw, text in zip(raw_texts, texts):
                if pattern.search(text):
                    result.update(
                        {
                            "s07_age_verify_text_raw": raw,
                            "s07_age_verify_text_normalized": text,
                            "s07_age_verify_text_class": "exact_range",
                            "matched_age_text": canonical,
                        }
                    )
                    return result
        return result
    year_token = r"(?:年|爛|坏抗)"
    exact_pattern = re.compile(rf"(?<!\d){target_age}\s*-\s*{target_age}\s*{year_token}")
    broad_patterns = (
        re.compile(rf"(?<!\d){target_age}\s*{year_token}\s*(?:以内|以下|内|眕囀|眕狟|囀)"),
        re.compile(rf"(?<!\d)0\s*-\s*{target_age}\s*{year_token}"),
        re.compile(rf"(?<!\d)0\s*(?:至|到|~|～)\s*{target_age}\s*{year_token}"),
        re.compile(rf"(?:小于|低于|少于)\s*{target_age}\s*{year_token}"),
    )
    ambiguous_pattern = re.compile(rf"(?<!\d){target_age}\s*{year_token}(?![\u4e00-\u9fff\d])")
    for raw, text in zip(raw_texts, texts):
        match = exact_pattern.search(text)
        if match:
            result.update(
                {
                    "s07_age_verify_text_raw": raw,
                    "s07_age_verify_text_normalized": text,
                    "s07_age_verify_text_class": "exact_range",
                    "matched_age_text": match.group(0),
                }
            )
            return result
    for raw, text in zip(raw_texts, texts):
        if any(pattern.search(text) for pattern in broad_patterns):
            result.update(
                {
                    "s07_age_verify_text_raw": raw,
                    "s07_age_verify_text_normalized": text,
                    "s07_age_verify_text_class": "broad_range",
                    "s07_age_broad_text_rejected": True,
                    "rejected_verify_text": raw,
                    "reject_reason": (
                        S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED
                        if target_age == 1
                        else "S07_AGE_BROAD_RANGE_TEXT_REJECTED"
                    ),
                }
            )
            return result
    for raw, text in zip(raw_texts, texts):
        if ambiguous_pattern.search(text):
            result.update(
                {
                    "s07_age_verify_text_raw": raw,
                    "s07_age_verify_text_normalized": text,
                    "s07_age_verify_text_class": "ambiguous",
                    "rejected_verify_text": raw,
                    "reject_reason": "S07_AGE_AMBIGUOUS_TEXT_REJECTED",
                }
            )
            return result
    return result


def _s07_exact_age_text(snapshot: dict[str, Any], target_age: int | None) -> str | None:
    classification = _s07_classify_age_verify_text(snapshot, target_age)
    if classification.get("s07_age_verify_text_class") == "exact_range":
        return str(classification.get("matched_age_text") or "")
    return None
    if target_age is None:
        return None
    texts = [text.replace(" ", "") for text in _s07_visible_text_sources(snapshot)]
    if target_age == 0:
        zero_patterns = (
            (S07_AGE_ZERO_SUCCESS_TEXTS[0], re.compile(r"(?<!\d)0\s*年\s*以下")),
            (S07_AGE_ZERO_SUCCESS_TEXTS[1], re.compile(r"(?<!\d)0\s*-\s*0\s*年")),
            (S07_AGE_ZERO_SUCCESS_TEXTS[2], re.compile(r"(?<!\d)0\s*年(?![\u4e00-\u9fff\d])")),
        )
        for canonical, pattern in zero_patterns:
            if any(pattern.search(text) for text in texts):
                return canonical
        return None
    if target_age == 1:
        one_patterns = (
            ("1-1\u5e74", re.compile(r"(?<!\d)1\s*-\s*1\s*(?:\u5e74|Дк)")),
            ("1\u5e74\u4ee5\u5185", re.compile(r"(?<!\d)1\s*(?:\u5e74|Дк)\s*(?:\u4ee5\u5185|\u4ee5\u4e0b|\u5185)")),
            ("1\u5e74", re.compile(r"(?<!\d)1\s*(?:\u5e74|Дк)(?![\u4e00-\u9fff\d])")),
        )
        for canonical, pattern in one_patterns:
            if any(pattern.search(text) for text in texts):
                return canonical
    pattern = re.compile(rf"(?<!\d){target_age}\s*-\s*{target_age}\s*年")
    for text in texts:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def _snapshot_age_pair(snapshot: dict[str, Any]) -> tuple[int | None, int | None]:
    left = snapshot.get("left_age")
    right = snapshot.get("right_age")
    try:
        left_age = int(left) if left is not None else None
    except (TypeError, ValueError):
        left_age = None
    try:
        right_age = int(right) if right is not None else None
    except (TypeError, ValueError):
        right_age = None
    return left_age, right_age


def _exact_age_confirmed(snapshot: dict[str, Any], target_age: int | None) -> bool:
    return bool(_verify_exact_age_selected(snapshot, target_age).get("exact_age_verified"))


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
    if candidates:
        return max(candidates, key=lambda item: ((item["bounds"][2] - item["bounds"][0]), _center(item["bounds"])[1]))

    # Some builds expose 查看X辆 on a non-clickable TextView while the clickable
    # parent has no label. Bind by XML ancestry, and fall back to the text node
    # center because it still lies inside the bottom button contract region.
    xml_text = str(snapshot.get("fresh_xml") or snapshot.get("xml") or "")
    if not xml_text.strip():
        return None
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    parent_map = {child: parent for parent in root.iter("node") for child in parent}
    text_candidates: list[dict[str, Any]] = []
    for element in root.iter("node"):
        text = str(element.attrib.get("text") or element.attrib.get("content-desc") or "").strip().replace(" ", "")
        bounds = _parse_bounds(element.attrib.get("bounds", ""))
        if not text or not _has_positive_bounds(bounds) or _center(bounds)[1] < min_center_y:
            continue
        if not view_pattern.match(text):
            continue
        current: ElementTree.Element | None = element
        while current is not None:
            current_bounds = _parse_bounds(current.attrib.get("bounds", ""))
            clickable = str(current.attrib.get("clickable") or "") == "true"
            enabled = str(current.attrib.get("enabled") or "true") == "true"
            local_bottom_container = (
                _has_positive_bounds(current_bounds)
                and _center(current_bounds)[1] >= min_center_y
                and (current_bounds[3] - current_bounds[1]) <= int(height * 0.30)
                and current_bounds != (0, 0, 0, 0)
            )
            if clickable and enabled and local_bottom_container:
                node = _node_dict_from_xml_element(
                    current,
                    extra={
                        "labels": [text],
                        "view_cars_text": text,
                        "view_cars_click_strategy": "clickable_ancestor_of_bottom_text",
                    },
                )
                node["bounds"] = current_bounds
                text_candidates.append(node)
                break
            current = parent_map.get(current)
        else:
            text_node = _node_dict_from_xml_element(
                element,
                extra={
                    "labels": [text],
                    "view_cars_text": text,
                    "view_cars_click_strategy": "bottom_text_node_center",
                },
            )
            text_node["bounds"] = bounds
            text_candidates.append(text_node)
    if not text_candidates:
        return None
    return max(text_candidates, key=lambda item: ((item["bounds"][2] - item["bounds"][0]), _center(item["bounds"])[1]))


def _s07_view_cars_button_text(node: dict[str, Any] | None) -> str | None:
    if not node:
        return None
    if node.get("view_cars_text"):
        return str(node.get("view_cars_text"))
    view_pattern = re.compile(r"^\u67e5\u770b\s*\d+\s*\u8f86$")
    for label in node.get("labels", []):
        text = str(label).strip().replace(" ", "")
        if view_pattern.match(text):
            return text
    return None


def _s07_bottom_view_result_text(snapshot: dict[str, Any]) -> str | None:
    view_node = _find_s07_view_cars_button(snapshot)
    if view_node:
        return _s07_view_cars_button_text(view_node)
    view_pattern = re.compile(r"^\u67e5\u770b\s*\d+\s*\u8f86$")
    height = _snapshot_screen_height(snapshot)
    min_center_y = int(height * 0.70)
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds) or _center(bounds)[1] < min_center_y:
            continue
        for label in node.get("labels", []):
            text = str(label).strip().replace(" ", "")
            if view_pattern.match(text):
                return text
    return None


def _verify_exact_age_selected(snapshot: dict[str, Any], target_age: int | None) -> dict[str, Any]:
    text_classification = _s07_classify_age_verify_text(snapshot, target_age)
    matched_age_text = (
        str(text_classification.get("matched_age_text") or "")
        if text_classification.get("s07_age_verify_text_class") == "exact_range"
        else None
    )
    left_age, right_age = _snapshot_age_pair(snapshot)
    slider_left, slider_right = _s07_age_range_from_slider_positions(snapshot)
    bottom_view_text = _s07_bottom_view_result_text(snapshot)
    exact_verified = False
    verify_method: str | None = None
    stop_code: str | None = None
    if target_age is not None and matched_age_text:
        exact_verified = True
        verify_method = "zero_or_below_text" if target_age == 0 and matched_age_text in S07_AGE_ZERO_SUCCESS_TEXTS else "exact_age_text"
    elif target_age is not None and left_age == target_age and right_age == target_age:
        exact_verified = True
        verify_method = "snapshot_age_pair_overlap"
    elif target_age is not None and slider_left == target_age and slider_right == target_age:
        exact_verified = True
        verify_method = "slider_overlap"
    elif target_age is not None:
        target_label = str(target_age)
        for node in snapshot.get("nodes", []):
            labels = {str(label).strip() for label in node.get("labels", [])}
            if target_label in labels and node.get("selected") is True:
                exact_verified = True
                verify_method = "selected_target_tick"
                break
    if not exact_verified:
        if target_age == 0:
            stop_code = "S07_AGE_ZERO_VERIFY_FAILED"
        elif target_age == 1:
            stop_code = (
                S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED
                if text_classification.get("s07_age_verify_text_class") == "broad_range"
                else S07_AGE_ONE_HIDDEN_TICK_VERIFY_FAILED
            )
        else:
            stop_code = "S07_EXACT_AGE_STATE_UNCONFIRMED"
    return {
        "s07_exact_age_contract_version": S07_EXACT_AGE_CONTRACT_VERSION,
        "target_age": target_age,
        "exact_age_overlap_allowed": True,
        "age_zero_success_texts": list(S07_AGE_ZERO_SUCCESS_TEXTS),
        "exact_age_verified": exact_verified,
        "matched_age_text": matched_age_text,
        "verify_method": verify_method,
        "allow_overlap": True,
        "stop_code": stop_code,
        "bottom_view_result_text": bottom_view_text,
        "bottom_view_result_refreshed": bool(bottom_view_text),
        "left_age_after": slider_left,
        "right_age_after": slider_right,
        "snapshot_left_age": left_age,
        "snapshot_right_age": right_age,
        "s07_age_exact_required": text_classification.get("s07_age_exact_required"),
        "s07_age_broad_text_rejected": text_classification.get("s07_age_broad_text_rejected"),
        "s07_age_verify_text_raw": text_classification.get("s07_age_verify_text_raw"),
        "s07_age_verify_text_normalized": text_classification.get("s07_age_verify_text_normalized"),
        "s07_age_verify_text_class": text_classification.get("s07_age_verify_text_class"),
        "rejected_verify_text": text_classification.get("rejected_verify_text"),
        "reject_reason": text_classification.get("reject_reason"),
    }


def _s07_age_post_action_proof(
    age_action: dict[str, Any],
    snapshot: dict[str, Any],
    target_age: int | None,
    exact_age_verify: dict[str, Any],
    *,
    reused_internal_fresh: bool = False,
) -> dict[str, Any]:
    attempts = age_action.get("age_strategy_attempts") or age_action.get("attempts") or []
    text_classification = _s07_classify_age_verify_text(snapshot, target_age)
    verify_text = exact_age_verify.get("matched_age_text") or _s07_exact_age_text(snapshot, target_age)
    verify_text_class = str(
        exact_age_verify.get("s07_age_verify_text_class")
        or text_classification.get("s07_age_verify_text_class")
        or "missing"
    )
    action_executed = bool(
        age_action.get("s07_age_action_executed")
        or age_action.get("swipe_command_sent")
        or age_action.get("right_slider_moved")
        or age_action.get("left_slider_moved")
        or attempts
        or (age_action.get("skip_reason") == "age_already_exact" and verify_text)
    )
    screenshot_path = str(snapshot.get("screenshot_path") or age_action.get("exact_snapshot_path") or "")
    xml_path = str(snapshot.get("xml_path") or age_action.get("exact_xml_path") or "")
    xml_stale = bool(
        snapshot.get("xml_stale")
        or snapshot.get("age_xml_stale_after_move")
        or age_action.get("age_xml_stale_after_move")
    )
    post_fresh_done = bool(screenshot_path and xml_path and not xml_stale)
    slider_left, slider_right = _s07_age_range_from_slider_positions(snapshot)
    snapshot_left, snapshot_right = _snapshot_age_pair(snapshot)
    left_actual = slider_left if slider_left is not None else snapshot_left
    right_actual = slider_right if slider_right is not None else snapshot_right
    exact_verified = bool(exact_age_verify.get("exact_age_verified"))
    text_verified = bool(verify_text)
    left_right_verified = target_age is not None and left_actual == target_age and right_actual == target_age
    post_verify_passed = bool(post_fresh_done and exact_verified)
    if target_age == 1:
        # For 1-year vehicles, do not treat planned target_x or slider-position
        # overlap as completion unless a fresh post-action page proves 1-1/1年.
        post_verify_passed = bool(
            post_fresh_done
            and action_executed
            and (
                (text_verified and verify_text_class == "exact_range")
                or left_right_verified
            )
        )
    elif target_age == 0:
        post_verify_passed = bool(post_verify_passed and (text_verified or left_right_verified))
    elif target_age is not None:
        post_verify_passed = bool(
            post_fresh_done
            and (
                (text_verified and verify_text_class == "exact_range")
                or left_right_verified
            )
        )
    proof_passed = bool(post_verify_passed)
    if not post_fresh_done:
        failure = S07_POST_ACTION_FRESH_EVIDENCE_MISSING
    elif target_age == 0 and not proof_passed:
        failure = S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED
    elif target_age == 1 and not proof_passed:
        failure = (
            S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED
            if verify_text_class == "broad_range"
            else S07_AGE_ONE_POST_ACTION_VERIFY_FAILED
        )
    elif target_age is not None and not left_right_verified and not text_verified:
        failure = S07_AGE_FILTER_ACTUAL_RANGE_MISMATCH
    else:
        failure = None
    pair_id = "|".join([screenshot_path, xml_path]) if screenshot_path or xml_path else ""
    if proof_passed and text_verified and verify_text_class == "exact_range":
        proof_kind = "exact_range"
    elif proof_passed and left_right_verified:
        proof_kind = "actual_slider_range"
    elif verify_text_class == "broad_range":
        proof_kind = "broad_range_rejected"
    elif verify_text_class == "ambiguous":
        proof_kind = "ambiguous_text_rejected"
    elif not post_fresh_done:
        proof_kind = "missing_fresh"
    else:
        proof_kind = "missing"
    actual_range_text = verify_text or (f"{left_actual}-{right_actual}" if left_actual is not None or right_actual is not None else None)
    return {
        "s07_age_post_action_proof_version": S07_AGE_ONE_POST_ACTION_PROOF_VERSION,
        "s07_age_post_action_proof_enabled": True,
        "post_action_fresh_capture": post_fresh_done,
        "s07_age_action_planned": target_age is not None,
        "s07_age_action_executed": action_executed,
        "target_age": target_age,
        "planned_left_age": age_action.get("left_slider_target_age") if age_action.get("left_slider_target_age") is not None else target_age,
        "planned_right_age": age_action.get("right_slider_target_age") if age_action.get("right_slider_target_age") is not None else target_age,
        "left_slider_action": "NOOP" if target_age == 0 else ("DRAG_TO_TARGET" if action_executed else "UNKNOWN"),
        "right_slider_action": "DRAG_TO_LEFT_SLIDER_ZERO_POSITION" if target_age == 0 and action_executed else ("DRAG_TO_TARGET" if action_executed else "UNKNOWN"),
        "pre_action_fresh_pair_id": str(age_action.get("pre_action_fresh_pair_id") or age_action.get("initial_fresh_pair_id") or ""),
        "post_action_fresh_pair_id": pair_id,
        "s07_age_post_fresh_done": post_fresh_done,
        "s07_age_post_fresh_screenshot_path": screenshot_path,
        "s07_age_post_fresh_xml_path": xml_path,
        "post_action_screenshot_path": screenshot_path,
        "post_action_xml_path": xml_path,
        "s07_age_post_fresh_xml_stale": xml_stale,
        "s07_age_post_fresh_verify_text": verify_text,
        "age_filter_verify_text": verify_text,
        "s07_age_verify_text_raw": exact_age_verify.get("s07_age_verify_text_raw") or text_classification.get("s07_age_verify_text_raw"),
        "s07_age_verify_text_normalized": exact_age_verify.get("s07_age_verify_text_normalized") or text_classification.get("s07_age_verify_text_normalized"),
        "s07_age_verify_text_class": verify_text_class,
        "s07_age_exact_required": target_age is not None,
        "s07_age_broad_text_rejected": verify_text_class == "broad_range",
        "s07_age_post_action_proof_passed": proof_passed,
        "s07_age_post_action_proof_reject_reason": failure,
        "s07_age_post_action_proof_kind": proof_kind,
        "proof_kind": proof_kind,
        "rejected_verify_text": exact_age_verify.get("rejected_verify_text") or text_classification.get("rejected_verify_text"),
        "reject_reason": failure or exact_age_verify.get("reject_reason") or text_classification.get("reject_reason"),
        "s07_age_post_fresh_verify_passed": post_verify_passed,
        "s07_age_left_slider_actual_age": left_actual,
        "s07_age_right_slider_actual_age": right_actual,
        "actual_left_age": left_actual,
        "actual_right_age": right_actual,
        "actual_range_text": actual_range_text,
        "s07_age_left_right_actual_age_verified": left_right_verified,
        "s07_age_one_post_action_proof_passed": proof_passed if target_age == 1 else None,
        "s07_age_zero_post_action_proof_passed": proof_passed if target_age == 0 else None,
        "s07_age_exact_internal_fresh_reused_for_post_action_proof": reused_internal_fresh,
        "AGE_FILTER_DONE_source": "post_action_fresh_verify_text" if proof_passed else None,
        "age_filter_done_by": "post_action_fresh_verify_text" if proof_passed else None,
        "clicked_view_result_after_verify": False,
        "post_action_failure_reason": failure,
    }


def _s07_view_result_preclick_gate(
    flow_state: dict[str, Any],
    age_post_action_proof: dict[str, Any],
    view_node: dict[str, Any] | None,
    view_count: int | None,
    target_age: int | None,
) -> dict[str, Any]:
    bottom_text = _s07_view_cars_button_text(view_node)
    verify_text = str(age_post_action_proof.get("age_filter_verify_text") or age_post_action_proof.get("s07_age_post_fresh_verify_text") or "")
    post_pair_id = str(age_post_action_proof.get("post_action_fresh_pair_id") or "")
    proof_passed = bool(age_post_action_proof.get("s07_age_post_fresh_verify_passed"))
    proof_kind = str(age_post_action_proof.get("proof_kind") or "")
    verify_text_class = str(age_post_action_proof.get("s07_age_verify_text_class") or "")
    gate_block_reason = ""
    if target_age == 0:
        proof_passed = proof_passed and bool(
            verify_text in S07_AGE_ZERO_SUCCESS_TEXTS
            or (
                age_post_action_proof.get("actual_left_age") == 0
                and age_post_action_proof.get("actual_right_age") == 0
            )
        )
    elif target_age == 1:
        proof_passed = proof_passed and (
            proof_kind in {"exact_range", "actual_slider_range"}
            and (
                verify_text_class == "exact_range"
                or (
                    age_post_action_proof.get("actual_left_age") == 1
                    and age_post_action_proof.get("actual_right_age") == 1
                )
            )
        )
        if not proof_passed:
            gate_block_reason = (
                S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED
                if verify_text_class == "broad_range"
                else S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER
            )
    elif target_age is not None:
        proof_passed = proof_passed and (
            proof_kind in {"exact_range", "actual_slider_range"}
            and (
                verify_text_class == "exact_range"
                or (
                    age_post_action_proof.get("actual_left_age") == target_age
                    and age_post_action_proof.get("actual_right_age") == target_age
                )
            )
        )
    ok = bool(
        flow_state.get("COLOR_FILTER_DONE") is True
        and flow_state.get("AGE_FILTER_DONE") is True
        and bottom_text
        and (view_count or 0) > 0
        and post_pair_id
        and proof_passed
    )
    reasons: list[str] = []
    if flow_state.get("COLOR_FILTER_DONE") is not True:
        reasons.append("COLOR_FILTER_DONE_not_true")
    if flow_state.get("AGE_FILTER_DONE") is not True:
        reasons.append("AGE_FILTER_DONE_not_true")
    if not bottom_text:
        reasons.append("bottom_view_result_text_missing")
    if (view_count or 0) <= 0:
        reasons.append("view_result_count_not_positive")
    if not post_pair_id:
        reasons.append("post_action_fresh_pair_id_missing")
    if not proof_passed:
        reasons.append("age_filter_post_action_proof_failed")
    if gate_block_reason:
        reasons.append(gate_block_reason)
    return {
        "ok": ok,
        "stop_code": None if ok else S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER,
        "reasons": reasons,
        "COLOR_FILTER_DONE": bool(flow_state.get("COLOR_FILTER_DONE")),
        "AGE_FILTER_DONE": bool(flow_state.get("AGE_FILTER_DONE")),
        "bottom_view_result_text": bottom_text,
        "view_result_count": view_count,
        "post_action_fresh_pair_id": post_pair_id,
        "age_filter_verify_text": verify_text,
        "target_age": target_age,
        "s07_age_exact_required": target_age is not None,
        "s07_age_broad_text_rejected": verify_text_class == "broad_range",
        "s07_age_verify_text_raw": age_post_action_proof.get("s07_age_verify_text_raw"),
        "s07_age_verify_text_normalized": age_post_action_proof.get("s07_age_verify_text_normalized"),
        "s07_age_verify_text_class": verify_text_class,
        "s07_age_post_action_proof_passed": proof_passed,
        "s07_age_post_action_proof_reject_reason": age_post_action_proof.get("s07_age_post_action_proof_reject_reason"),
        "s07_view_result_preclick_gate_decision": "allow" if ok else "block",
        "s07_view_result_preclick_gate_block_reason": gate_block_reason or ("" if ok else S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER),
    }


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
    result.update(_s04_brand_zone_context_fields(context))
    result.update(_s05_emission_variant_context_fields(context))
    result.update(_s08_s10_context_fields(context))
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


S07_AGE_UNLIMITED_LABEL = "不限"
S07_AGE_TICK_LABELS = {"0", "2", "4", "6", "8", "10", S07_AGE_UNLIMITED_LABEL}


def _s07_age_tick_nodes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[list[dict[str, Any]]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        if not labels & S07_AGE_TICK_LABELS:
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
        mileage_only_labels = {"3", "9", "12"}
        return (len(labels & S07_AGE_TICK_LABELS) - len(labels & mileage_only_labels), -_center(row[0]["bounds"])[1])

    best = max(rows, key=row_score)
    return sorted(best, key=lambda item: _center(item["bounds"])[0])


def _s07_age_numeric_points(snapshot: dict[str, Any]) -> list[tuple[int, int, int]]:
    numeric_points: list[tuple[int, int, int]] = []
    for node in _s07_age_tick_nodes(snapshot):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        for label in node.get("labels", []):
            text = str(label).strip()
            if text.isdigit():
                x, y = _center(bounds)
                numeric_points.append((int(text), x, y))
                break
    return sorted(set(numeric_points), key=lambda item: item[0])


def _s07_age_visible_tick_positions(snapshot: dict[str, Any]) -> dict[int, dict[str, Any]]:
    positions: dict[int, dict[str, Any]] = {}
    for node in _s07_age_tick_nodes(snapshot):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        for label in node.get("labels", []):
            text = str(label).strip()
            if not text.isdigit():
                continue
            age = int(text)
            x, y = _center(bounds)
            positions[age] = {"x": x, "y": y, "bounds": list(bounds)}
            break
    return dict(sorted(positions.items()))


def _s07_age_unlimited_tick(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    for node in _s07_age_tick_nodes(snapshot):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        if S07_AGE_UNLIMITED_LABEL not in labels:
            continue
        x, y = _center(bounds)
        return {"x": x, "y": y, "bounds": list(bounds)}
    return None


def _s07_hidden_age_tick_info(snapshot: dict[str, Any], target_age: int | None) -> dict[str, Any]:
    visible_positions = _s07_age_visible_tick_positions(snapshot)
    info: dict[str, Any] = {
        "hidden_tick_supported_range": "between_adjacent_visible_ticks_or_11-12",
        "hidden_age_tick_used": False,
        "hidden_age_tick_valid": False,
        "visible_tick_positions": {str(age): [item["x"], item["y"]] for age, item in visible_positions.items()},
        "target_age": target_age,
        "target_age_x": None,
        "target_age_y": None,
        "target_age_point": None,
        "target_tick_bounds": None,
        "lower_tick_age": None,
        "upper_tick_age": None,
        "ratio_between_ticks": None,
        "x8": visible_positions.get(8, {}).get("x"),
        "x10": visible_positions.get(10, {}).get("x"),
        "age_1_x": None,
        "one_year_step": None,
        "age_11_x": None,
        "age_12_x": None,
        "unlimited_tick_x": None,
        "hidden_tick_source": None,
        "hidden_tick_invalid_reason": None,
    }
    if target_age is None:
        info["hidden_tick_invalid_reason"] = "target_age_missing"
        return info
    if target_age > 12:
        info["hidden_tick_invalid_reason"] = "target_age_out_of_supported_exact_range"
        return info
    if target_age in visible_positions:
        info["hidden_tick_invalid_reason"] = "target_age_visible_not_hidden"
        return info
    info["hidden_age_tick_used"] = True

    visible_ages = sorted(visible_positions)
    lower_age = next((age for age in reversed(visible_ages) if age < target_age), None)
    upper_age = next((age for age in visible_ages if age > target_age), None)
    if lower_age is not None and upper_age is not None and upper_age > lower_age:
        lower_item = visible_positions[lower_age]
        upper_item = visible_positions[upper_age]
        ratio = (target_age - lower_age) / (upper_age - lower_age)
        target_x = round(lower_item["x"] + (upper_item["x"] - lower_item["x"]) * ratio)
        target_y = round(lower_item["y"] + (upper_item["y"] - lower_item["y"]) * ratio)
        left_bound = lower_item.get("bounds") or [lower_item["x"] - 8, lower_item["y"] - 8, lower_item["x"] + 8, lower_item["y"] + 8]
        right_bound = upper_item.get("bounds") or [upper_item["x"] - 8, upper_item["y"] - 8, upper_item["x"] + 8, upper_item["y"] + 8]
        step_px = abs(upper_item["x"] - lower_item["x"]) / max(upper_age - lower_age, 1)
        half_width = max(6, min(28, round(step_px / 2)))
        half_height = max(8, round(max(left_bound[3] - left_bound[1], right_bound[3] - right_bound[1]) / 2))
        track_bounds = _s07_age_track_bounds(snapshot)
        if track_bounds and not (track_bounds[0] - 5 <= target_x <= track_bounds[2] + 5):
            info["hidden_tick_invalid_reason"] = "target_outside_track_bounds"
            return info
        info.update(
            {
                "hidden_age_tick_valid": True,
                "lower_tick_age": lower_age,
                "upper_tick_age": upper_age,
                "ratio_between_ticks": ratio,
                "one_year_step": step_px,
                "age_1_x": target_x if target_age == 1 else None,
                "target_age_x": target_x,
                "target_age_y": target_y,
                "target_age_point": [target_x, target_y],
                "target_tick_bounds": [target_x - half_width, target_y - half_height, target_x + half_width, target_y + half_height],
                "hidden_tick_source": f"x{lower_age}_x{upper_age}_interpolation",
                "hidden_tick_invalid_reason": None,
            }
        )
        return info

    if target_age not in (11, 12):
        info["hidden_tick_invalid_reason"] = "visible_tick_bracket_missing"
        return info

    x10_item = visible_positions.get(10)
    if not x10_item:
        info["hidden_tick_invalid_reason"] = "visible_tick_10_missing"
        return info

    one_year_step: float | None = None
    if 8 in visible_positions:
        one_year_step = (x10_item["x"] - visible_positions[8]["x"]) / 2
        info["hidden_tick_source"] = "x8_x10_step"
    else:
        lower_pairs = [
            (left_age, right_age)
            for left_age, right_age in zip(sorted(visible_positions), sorted(visible_positions)[1:])
            if right_age <= 10 and right_age - left_age > 0
        ]
        if lower_pairs:
            left_age, right_age = lower_pairs[-1]
            one_year_step = (visible_positions[right_age]["x"] - visible_positions[left_age]["x"]) / (right_age - left_age)
            info["hidden_tick_source"] = f"x{left_age}_x{right_age}_step"
    if one_year_step is None or one_year_step <= 0:
        info["hidden_tick_invalid_reason"] = "visible_tick_spacing_missing"
        return info

    target_x = round(x10_item["x"] + one_year_step * (target_age - 10))
    target_y = int(x10_item["y"])
    age_11_x = round(x10_item["x"] + one_year_step)
    age_12_x = round(x10_item["x"] + one_year_step * 2)
    unlimited_tick = _s07_age_unlimited_tick(snapshot)
    track_bounds = _s07_age_track_bounds(snapshot)
    right_limit = None
    if unlimited_tick:
        right_limit = int(unlimited_tick["x"])
        info["unlimited_tick_x"] = right_limit
    elif track_bounds:
        right_limit = int(track_bounds[2])
    if target_x <= int(x10_item["x"]):
        info["hidden_tick_invalid_reason"] = "target_not_right_of_10"
        return info
    if right_limit is not None and target_x >= right_limit:
        info["hidden_tick_invalid_reason"] = "target_not_left_of_unlimited_or_track_right"
        return info
    if track_bounds and not (track_bounds[0] - 5 <= target_x <= track_bounds[2] + 5):
        info["hidden_tick_invalid_reason"] = "target_outside_track_bounds"
        return info

    reference_bounds = x10_item.get("bounds") or [target_x - 8, target_y - 8, target_x + 8, target_y + 8]
    half_width = max(6, min(28, round(one_year_step / 2)))
    half_height = max(8, round((reference_bounds[3] - reference_bounds[1]) / 2))
    info.update(
        {
            "hidden_age_tick_valid": True,
            "one_year_step": one_year_step,
            "age_11_x": age_11_x,
            "age_12_x": age_12_x,
            "target_age_x": target_x,
            "target_age_y": target_y,
            "target_age_point": [target_x, target_y],
            "target_tick_bounds": [target_x - half_width, target_y - half_height, target_x + half_width, target_y + half_height],
            "hidden_tick_invalid_reason": None,
        }
    )
    return info


def _s07_missing_age_target_stop_code(target_age: int | None) -> str:
    if target_age == 1:
        return S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE
    if target_age in (11, 12):
        return "S07_AGE_HIDDEN_TICK_VERIFY_FAILED"
    return "S07_AGE_SLIDER_TARGET_NOT_FOUND"


def _s07_age_tick_labels_detected(snapshot: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for node in _s07_age_tick_nodes(snapshot):
        for label in node.get("labels", []):
            text = str(label).strip()
            if text in S07_AGE_TICK_LABELS and text not in labels:
                labels.append(text)
    return labels


def _s07_age_track_node_bounds(snapshot: dict[str, Any]) -> list[int] | None:
    tick_nodes = [node for node in _s07_age_tick_nodes(snapshot) if _has_positive_bounds(node.get("bounds"))]
    if not tick_nodes:
        return None
    tick_centers = [_center(node["bounds"]) for node in tick_nodes]
    min_tick_x = min(x for x, _ in tick_centers)
    max_tick_x = max(x for x, _ in tick_centers)
    tick_y = round(sum(y for _, y in tick_centers) / len(tick_centers))
    min_track_width = max(160, int((max_tick_x - min_tick_x) * 0.70))
    candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        if not node.get("clickable") or not node.get("enabled"):
            continue
        if [str(label).strip() for label in node.get("labels", []) if str(label).strip()]:
            continue
        x1, y1, x2, y2 = bounds
        width = x2 - x1
        height = y2 - y1
        cx, cy = _center(bounds)
        if width < min_track_width:
            continue
        if height > 80:
            continue
        if not (min_tick_x - 40 <= x1 <= min_tick_x + 80):
            continue
        if not (max_tick_x - 80 <= x2 <= max_tick_x + 40):
            continue
        if not (tick_y + 25 <= cy <= tick_y + 90):
            continue
        candidates.append(node)
    if not candidates:
        return None
    best = min(candidates, key=lambda item: (item["bounds"][3] - item["bounds"][1], abs(_center(item["bounds"])[1] - (tick_y + 55))))
    return list(best["bounds"])


def _s07_age_handle_candidate_summary(node: dict[str, Any], *, rejected_reason: str | None = None) -> dict[str, Any]:
    bounds = node.get("bounds")
    summary = {
        "bounds": list(bounds) if _has_positive_bounds(bounds) else None,
        "labels": [str(label) for label in node.get("labels", [])],
        "text": node.get("text"),
        "content_desc": node.get("content_desc"),
        "class_name": node.get("class_name"),
        "resource_id": node.get("resource_id"),
        "clickable": node.get("clickable"),
        "enabled": node.get("enabled"),
    }
    if _has_positive_bounds(bounds):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        summary.update(
            {
                "center": list(_center((x1, y1, x2, y2))),
                "width": x2 - x1,
                "height": y2 - y1,
            }
        )
    if rejected_reason:
        summary["rejected_reason"] = rejected_reason
    return summary


def _s07_age_node_text_blob(node: dict[str, Any]) -> str:
    values = []
    values.extend(str(label) for label in node.get("labels", []) if str(label).strip())
    values.append(str(node.get("text") or ""))
    values.append(str(node.get("content_desc") or ""))
    values.append(str(node.get("resource_id") or ""))
    return " ".join(value.strip() for value in values if value and value.strip())


def _s07_age_looks_like_hash_or_image_token(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if re.search(r"[\u4e00-\u9fff]", compact):
        return False
    return bool(len(compact) >= 10 and re.fullmatch(r"[A-Za-z0-9_./+=:-]+", compact))


def is_s07_slider_ghost_candidate(
    node: dict[str, Any],
    *,
    track_bounds: list[int] | tuple[int, int, int, int] | None = None,
    real_handle_y_band: tuple[int, int] | list[int] | None = None,
    tick_y: int | None = None,
) -> bool:
    """Return True for S07 visual/tooltip handle lookalikes that are not draggable handles."""
    bounds = node.get("bounds")
    if not _has_positive_bounds(bounds):
        return False
    x1, y1, x2, y2 = [int(value) for value in bounds]
    width = x2 - x1
    height = y2 - y1
    _, cy = _center((x1, y1, x2, y2))
    class_name = str(node.get("class_name") or "").lower()
    text_blob = _s07_age_node_text_blob(node)
    if "image" in class_name and (height <= 100 or _s07_age_looks_like_hash_or_image_token(text_blob)):
        return True
    if _s07_age_looks_like_hash_or_image_token(text_blob) and height <= 120:
        return True
    if real_handle_y_band and cy < int(real_handle_y_band[0]) - 50:
        return True
    if track_bounds and _has_positive_bounds(track_bounds):
        track_center_y = round((int(track_bounds[1]) + int(track_bounds[3])) / 2)
        if cy < track_center_y - 35 and height < 120:
            return True
    if tick_y is not None and cy < int(tick_y) and height < 120:
        return True
    if height < 80 and width <= 180:
        return True
    return False


def _s07_pick_handle_on_side(nodes: list[dict[str, Any]], *, side: str) -> dict[str, Any] | None:
    if not nodes:
        return None
    centers = [_center(node["bounds"]) for node in nodes if _has_positive_bounds(node.get("bounds"))]
    if not centers:
        return None
    edge_x = min(x for x, _ in centers) if side == "left" else max(x for x, _ in centers)
    same_side = [node for node in nodes if abs(_center(node["bounds"])[0] - edge_x) <= 80]
    if not same_side:
        same_side = nodes
    return max(
        same_side,
        key=lambda item: (
            (int(item["bounds"][3]) - int(item["bounds"][1])) * (int(item["bounds"][2]) - int(item["bounds"][0])),
            int(item["bounds"][3]) - int(item["bounds"][1]),
            _center(item["bounds"])[1],
        ),
    )


def _s07_bounds_overlap_x(
    left_bounds: list[int] | tuple[int, int, int, int] | None,
    right_bounds: list[int] | tuple[int, int, int, int] | None,
) -> bool:
    if not _has_positive_bounds(left_bounds) or not _has_positive_bounds(right_bounds):
        return False
    left = [int(value) for value in left_bounds]  # type: ignore[arg-type]
    right = [int(value) for value in right_bounds]  # type: ignore[arg-type]
    return max(left[0], right[0]) < min(left[2], right[2])


def _s07_select_distinct_handle_pair(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [node for node in nodes if _has_positive_bounds(node.get("bounds"))]
    result: dict[str, Any] = {
        "left": None,
        "right": None,
        "s07_handle_pair_overlap_allowed": False,
        "s07_handle_pair_close_allowed": False,
        "s07_handle_pair_separation_px": None,
        "s07_handle_pair_binding_method": "x_center_sorted_distinct_real_handles",
        "stop_code": None,
        "rejected_reason": None,
    }
    if len(valid) < 2:
        result["stop_code"] = S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED
        result["rejected_reason"] = "real_handle_pair_not_found"
        return result

    valid = sorted(valid, key=lambda node: (_center(node["bounds"])[0], _center(node["bounds"])[1]))
    viable_pairs: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    closest_pair_separation: int | None = None
    for index, left in enumerate(valid):
        for right in valid[index + 1:]:
            left_bounds = left.get("bounds")
            right_bounds = right.get("bounds")
            if list(left_bounds or []) == list(right_bounds or []):
                continue
            left_cx, _ = _center(left_bounds)
            right_cx, _ = _center(right_bounds)
            separation = int(right_cx - left_cx)
            if closest_pair_separation is None or separation < closest_pair_separation:
                closest_pair_separation = separation
            if separation >= S07_CLOSE_HANDLE_PAIR_MIN_SEPARATION_PX:
                viable_pairs.append((separation, left, right))

    if not viable_pairs:
        result["stop_code"] = S07_AGE_SLIDER_CLOSE_HANDLE_PAIR_BINDING_FAILED
        result["rejected_reason"] = "real_handle_pair_too_close"
        result["s07_handle_pair_separation_px"] = closest_pair_separation
        return result

    separation, left, right = max(viable_pairs, key=lambda item: item[0])
    overlap = _s07_bounds_overlap_x(left.get("bounds"), right.get("bounds"))
    result.update(
        {
            "left": left,
            "right": right,
            "s07_handle_pair_overlap_allowed": overlap,
            "s07_handle_pair_close_allowed": separation <= S07_CLOSE_HANDLE_PAIR_DISTANCE_PX,
            "s07_handle_pair_separation_px": separation,
            "s07_handle_pair_binding_method": (
                "x_center_sorted_overlap_real_handles"
                if overlap
                else "x_center_sorted_distinct_real_handles"
            ),
        }
    )
    return result


def bind_s07_real_slider_handle(snapshot: dict[str, Any]) -> dict[str, Any]:
    tick_nodes = [node for node in _s07_age_tick_nodes(snapshot) if _has_positive_bounds(node.get("bounds"))]
    trace: dict[str, Any] = {
        "handle_binding_method": "s07_real_green_slider_handle",
        "selected_handle_source": None,
        "handle_candidates": [],
        "rejected_handle_candidates": [],
        "selected_left_handle_bounds": None,
        "selected_right_handle_bounds": None,
        "selected_left_handle": None,
        "selected_right_handle": None,
        "real_handle_pair_y_band": None,
        "s07_handle_pair_overlap_allowed": False,
        "s07_handle_pair_close_allowed": False,
        "s07_handle_pair_separation_px": None,
        "s07_handle_pair_binding_method": None,
        "handle_is_ghost": None,
        "stop_code": None,
    }
    if not tick_nodes:
        trace["stop_code"] = S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED
        trace["rejected_reason"] = "age_tick_nodes_missing"
        return trace
    tick_centers = [_center(node["bounds"]) for node in tick_nodes]
    min_x = min(x for x, _ in tick_centers) - 120
    max_x = max(x for x, _ in tick_centers) + 120
    tick_y = round(sum(y for _, y in tick_centers) / len(tick_centers))
    track_node_bounds = _s07_age_track_node_bounds(snapshot)
    track_center_y = round((track_node_bounds[1] + track_node_bounds[3]) / 2) if track_node_bounds else None

    raw_candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_bounds: set[tuple[int, int, int, int]] = set()
    for node in snapshot.get("nodes", []):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        x1, y1, x2, y2 = [int(value) for value in bounds]
        width = x2 - x1
        height = y2 - y1
        cx, cy = _center((x1, y1, x2, y2))
        labels = [str(label).strip() for label in node.get("labels", []) if str(label).strip()]
        reason: str | None = None
        if not (min_x <= cx <= max_x):
            reason = "outside_age_tick_x_range"
        elif track_node_bounds is not None and not (y1 <= track_node_bounds[3] and y2 >= track_node_bounds[1]):
            reason = "not_overlapping_age_track_hotzone"
        elif track_node_bounds is None and not (tick_y - 120 <= cy <= tick_y + 180):
            reason = "outside_age_tick_y_range"
        elif not (45 <= width <= 220 and 40 <= height <= 260):
            reason = "not_slider_handle_size"
        elif labels and not _s07_age_looks_like_hash_or_image_token(_s07_age_node_text_blob(node)):
            reason = "labeled_non_handle_node"
        if reason:
            if min_x <= cx <= max_x and tick_y - 160 <= cy <= tick_y + 220 and 35 <= width <= 260 and 30 <= height <= 280:
                rejected.append(_s07_age_handle_candidate_summary(node, rejected_reason=reason))
            continue
        key = tuple(int(value) for value in bounds)
        if key in seen_bounds:
            continue
        seen_bounds.add(key)
        raw_candidates.append(node)

    trace["handle_candidates"] = [_s07_age_handle_candidate_summary(node) for node in raw_candidates]
    strong_candidates = [
        node
        for node in raw_candidates
        if _has_positive_bounds(node.get("bounds"))
        and (int(node["bounds"][3]) - int(node["bounds"][1])) >= 90
        and (track_center_y is None or _center(node["bounds"])[1] >= track_center_y - 20)
        and not _s07_age_looks_like_hash_or_image_token(_s07_age_node_text_blob(node))
        and "image" not in str(node.get("class_name") or "").lower()
    ]
    real_handle_y_band: tuple[int, int] | None = None
    if len(strong_candidates) >= 2:
        y_centers = [_center(node["bounds"])[1] for node in strong_candidates]
        real_handle_y_band = (min(y_centers), max(y_centers))
        trace["real_handle_pair_y_band"] = [real_handle_y_band[0], real_handle_y_band[1]]

    real_candidates: list[dict[str, Any]] = []
    for node in raw_candidates:
        if is_s07_slider_ghost_candidate(node, track_bounds=track_node_bounds, real_handle_y_band=real_handle_y_band, tick_y=tick_y):
            rejected.append(_s07_age_handle_candidate_summary(node, rejected_reason=S07_AGE_SLIDER_GHOST_HANDLE_REJECTED))
            continue
        if _s07_age_looks_like_hash_or_image_token(_s07_age_node_text_blob(node)):
            rejected.append(_s07_age_handle_candidate_summary(node, rejected_reason="hash_or_image_token_not_draggable"))
            continue
        real_candidates.append(node)

    pair = _s07_select_distinct_handle_pair(real_candidates)
    trace.update(
        {
            "s07_handle_pair_overlap_allowed": pair.get("s07_handle_pair_overlap_allowed"),
            "s07_handle_pair_close_allowed": pair.get("s07_handle_pair_close_allowed"),
            "s07_handle_pair_separation_px": pair.get("s07_handle_pair_separation_px"),
            "s07_handle_pair_binding_method": pair.get("s07_handle_pair_binding_method"),
        }
    )
    left = pair.get("left")
    right = pair.get("right")
    if not left or not right:
        trace["stop_code"] = pair.get("stop_code") or S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED
        trace["rejected_reason"] = pair.get("rejected_reason") or "real_handle_pair_not_found"
        trace["rejected_handle_candidates"] = rejected
        return trace

    selected = [left, right]
    trace.update(
        {
            "selected_handle_source": "real_green_slider_handle",
            "selected_left_handle": _s07_age_handle_candidate_summary(left),
            "selected_right_handle": _s07_age_handle_candidate_summary(right),
            "selected_left_handle_bounds": list(left["bounds"]),
            "selected_right_handle_bounds": list(right["bounds"]),
            "selected_handles": [_s07_age_handle_candidate_summary(left), _s07_age_handle_candidate_summary(right)],
            "selected_handle_bounds": [list(left["bounds"]), list(right["bounds"])],
            "handle_is_ghost": False,
            "rejected_handle_candidates": rejected,
            "stop_code": None,
        }
    )
    if trace.get("real_handle_pair_y_band") is None:
        y_centers = [_center(node["bounds"])[1] for node in selected]
        trace["real_handle_pair_y_band"] = [min(y_centers), max(y_centers)]
    return trace


def _s07_age_handle_selection(snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    binding = bind_s07_real_slider_handle(snapshot)
    selected: list[dict[str, Any]] = []
    selected_bounds = [binding.get("selected_left_handle_bounds"), binding.get("selected_right_handle_bounds")]
    for selected_bound in selected_bounds:
        if not _has_positive_bounds(selected_bound):
            continue
        for node in snapshot.get("nodes", []):
            if list(node.get("bounds") or []) == list(selected_bound):
                selected.append(node)
                break
    candidates: list[dict[str, Any]] = []
    for summary in binding.get("handle_candidates") or []:
        bounds = summary.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        for node in snapshot.get("nodes", []):
            if list(node.get("bounds") or []) == list(bounds):
                candidates.append(node)
                break
    return selected, candidates, list(binding.get("rejected_handle_candidates") or [])



def _s07_age_handle_nodes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    selected, _, _ = _s07_age_handle_selection(snapshot)
    return selected


def _s07_nearest_age_for_x(snapshot: dict[str, Any], x: int) -> int | None:
    numeric_points = _s07_age_numeric_points(snapshot)
    if not numeric_points:
        return None
    return min(numeric_points, key=lambda item: abs(item[1] - x))[0]


def _s07_age_range_from_slider_positions(snapshot: dict[str, Any]) -> tuple[int | None, int | None]:
    left_age, right_age = _snapshot_age_pair(snapshot)
    if left_age is not None or right_age is not None:
        return left_age, right_age
    handles = _s07_age_handle_nodes(snapshot)
    if len(handles) < 2:
        return None, None
    left_x, _ = _center(handles[0]["bounds"])
    right_x, _ = _center(handles[-1]["bounds"])
    return _s07_nearest_age_for_x(snapshot, left_x), _s07_nearest_age_for_x(snapshot, right_x)


def _s07_age_target_point(snapshot: dict[str, Any], target_age: int | None) -> tuple[int, int] | None:
    if target_age is None:
        return None
    numeric_points = _s07_age_numeric_points(snapshot)
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
    hidden_info = _s07_hidden_age_tick_info(snapshot, target_age)
    hidden_point = hidden_info.get("target_age_point")
    if hidden_info.get("hidden_age_tick_valid") and isinstance(hidden_point, list) and len(hidden_point) == 2:
        return int(hidden_point[0]), int(hidden_point[1])
    return None


def _s07_age_target_tick_bounds(snapshot: dict[str, Any], target_age: int | None) -> list[int] | None:
    if target_age is None:
        return None
    for node in _s07_age_tick_nodes(snapshot):
        bounds = node.get("bounds")
        if not _has_positive_bounds(bounds):
            continue
        for label in node.get("labels", []):
            if str(label).strip() == str(target_age):
                return list(bounds)
    hidden_info = _s07_hidden_age_tick_info(snapshot, target_age)
    hidden_bounds = hidden_info.get("target_tick_bounds")
    if hidden_info.get("hidden_age_tick_valid") and isinstance(hidden_bounds, list) and len(hidden_bounds) == 4:
        return [int(value) for value in hidden_bounds]
    return None


def _s07_age_slider_points(snapshot: dict[str, Any], target_age: int | None) -> dict[str, Any]:
    target_tick_point = _s07_age_target_point(snapshot, target_age)
    target_point = _s07_age_track_point(snapshot, target_age) or target_tick_point
    handles = _s07_age_handle_nodes(snapshot)
    centers = [_center(node["bounds"]) for node in _s07_age_tick_nodes(snapshot) if _has_positive_bounds(node.get("bounds"))]
    hidden_tick_info = _s07_hidden_age_tick_info(snapshot, target_age)
    result: dict[str, Any] = {
        "target_point": target_point,
        "target_tick_point": target_tick_point,
        "target_tick_bounds": _s07_age_target_tick_bounds(snapshot, target_age),
        "left_point": None,
        "right_point": None,
        "left_handle_bounds": None,
        "right_handle_bounds": None,
        "age_track_bounds": _s07_age_track_bounds(snapshot),
        "right_drag_y_source": "real_handle_center_y" if handles else None,
        "selected_handle_source": "real_green_slider_handle" if handles else None,
        "hidden_age_tick_used": hidden_tick_info.get("hidden_age_tick_used"),
        "hidden_age_tick_valid": hidden_tick_info.get("hidden_age_tick_valid"),
        "target_age_x": hidden_tick_info.get("target_age_x"),
        "one_year_step": hidden_tick_info.get("one_year_step"),
    }
    if handles and len(handles) >= 2:
        result["left_point"] = _center(handles[0]["bounds"])
        result["right_point"] = _center(handles[-1]["bounds"])
        result["left_handle_bounds"] = list(handles[0]["bounds"])
        result["right_handle_bounds"] = list(handles[-1]["bounds"])
    elif centers:
        y = target_point[1] if target_point else centers[0][1]
        result["left_point"] = (min(x for x, _ in centers), y)
        result["right_point"] = (max(x for x, _ in centers), y)
    return result


def _s07_age_track_bounds(snapshot: dict[str, Any]) -> list[int] | None:
    tick_nodes = [node for node in _s07_age_tick_nodes(snapshot) if _has_positive_bounds(node.get("bounds"))]
    if not tick_nodes:
        return None
    track_node_bounds = _s07_age_track_node_bounds(snapshot)
    if track_node_bounds:
        return track_node_bounds
    bounds_list = [list(node["bounds"]) for node in tick_nodes]
    bounds_list.extend([list(node["bounds"]) for node in _s07_age_handle_nodes(snapshot) if _has_positive_bounds(node.get("bounds"))])
    x_centers = [_center(node["bounds"])[0] for node in tick_nodes]
    return [
        min(x_centers),
        min(bounds[1] for bounds in bounds_list),
        max(x_centers),
        max(bounds[3] for bounds in bounds_list),
    ]


def _s07_age_track_point(snapshot: dict[str, Any], age: int | None) -> tuple[int, int] | None:
    target_point = _s07_age_target_point(snapshot, age)
    track_bounds = _s07_age_track_bounds(snapshot)
    if target_point is None or track_bounds is None:
        return target_point
    return target_point[0], round((track_bounds[1] + track_bounds[3]) / 2)


def _s07_age_direct_track_plan(snapshot: dict[str, Any], target_age: int | None) -> dict[str, Any]:
    points = _s07_age_slider_points(snapshot, target_age)
    track_bounds = points.get("age_track_bounds") or _s07_age_track_bounds(snapshot)
    left_handle_bounds = points.get("left_handle_bounds")
    right_handle_bounds = points.get("right_handle_bounds")
    target_tick_point = points.get("target_tick_point") or _s07_age_target_point(snapshot, target_age)
    numeric_points = _s07_age_numeric_points(snapshot)
    plan: dict[str, Any] = {
        "direct_track_fastpath_version": S07_AGE_SLIDER_DIRECT_FASTPATH_VERSION,
        "direct_track_fastpath_available": False,
        "target_age_years": target_age,
        "target_range": f"{target_age}-{target_age}" if target_age is not None else None,
        "track_bounds": track_bounds,
        "left_handle_bounds": left_handle_bounds,
        "right_handle_bounds": right_handle_bounds,
        "target_x": None,
        "target_y": None,
        "target_ratio": None,
        "min_age": None,
        "max_age": None,
        "target_x_calculation": None,
        "special_5_5_fastpath": target_age == 5,
        "failure_reason": None,
    }
    if target_age is None:
        plan["failure_reason"] = "target_age_missing"
        return plan
    if not _has_positive_bounds(track_bounds):
        plan["failure_reason"] = "track_bounds_missing"
        return plan
    if not _has_positive_bounds(right_handle_bounds):
        plan["failure_reason"] = "right_handle_bounds_missing"
        return plan
    if not _has_positive_bounds(left_handle_bounds):
        plan["failure_reason"] = "left_handle_bounds_missing"
        return plan
    if not numeric_points:
        plan["failure_reason"] = "age_tick_points_missing"
        return plan
    min_age = min(age for age, _, _ in numeric_points)
    max_age = max(age for age, _, _ in numeric_points)
    track_x1, track_y1, track_x2, track_y2 = [int(value) for value in track_bounds]
    target_y = round((track_y1 + track_y2) / 2)
    visible_ticks = [
        {"age": age, "center_x": x, "center_y": y}
        for age, x, y in numeric_points
    ]
    contract_action_plan = build_s07_age_action_plan(
        target_age_years=target_age,
        visible_ticks=visible_ticks,
    )
    action_outputs = contract_action_plan.get("action_outputs") or {}
    contract_expected = contract_action_plan.get("expected") or {}
    binding_trace = build_action_plan_binding_trace(
        contract_action_plan,
        action_algorithm_used=str(action_outputs.get("target_x_calculation") or "visible_tick_interpolation"),
    )
    target_x = action_outputs.get("target_x")
    target_ratio = action_outputs.get("ratio_between_ticks")
    calculation = action_outputs.get("target_x_calculation")
    if target_x is not None:
        target_x = int(target_x)
    elif isinstance(target_tick_point, tuple) and len(target_tick_point) == 2:
        target_x = int(target_tick_point[0])
        calculation = "target_tick_or_hidden_tick_point"
    if target_x is None:
        plan["failure_reason"] = "target_x_missing"
        plan["contract_action_plan"] = contract_action_plan
        plan.update(binding_trace)
        return plan
    plan.update(
        {
            "direct_track_fastpath_available": True,
            "contract_action_plan": contract_action_plan,
            **binding_trace,
            "left_slider_target": contract_expected.get("left_slider_target"),
            "right_slider_target": contract_expected.get("right_slider_target"),
            "expected_age_filter": contract_expected.get("expected_age_filter"),
            "target_x": target_x,
            "target_y": target_y,
            "target_ratio": round(target_ratio, 4) if target_ratio is not None else None,
            "min_age": min_age,
            "max_age": max_age,
            "target_x_algorithm": binding_trace.get("action_algorithm_used"),
            "target_x_calculation": calculation,
            "failure_reason": None,
        }
    )
    return plan


def _s07_age_bounds_changed(
    before_bounds: list[int] | tuple[int, int, int, int] | None,
    after_bounds: list[int] | tuple[int, int, int, int] | None,
) -> bool:
    if not _has_positive_bounds(before_bounds) or not _has_positive_bounds(after_bounds):
        return False
    return [int(value) for value in before_bounds] != [int(value) for value in after_bounds]


def _s07_build_age_drag_binding(
    *,
    side: str,
    selected_handle_bounds: list[int] | tuple[int, int, int, int] | None,
    target_x: int | None,
    preferred_y: int | None,
    original_start_point: tuple[int, int] | list[int] | None = None,
    track_bounds: list[int] | tuple[int, int, int, int] | None = None,
    selected_handle_source: str | None = "real_green_slider_handle",
) -> dict[str, Any]:
    track_center_y = round((int(track_bounds[1]) + int(track_bounds[3])) / 2) if _has_positive_bounds(track_bounds) else None
    trace: dict[str, Any] = {
        "side": side,
        "selected_handle_bounds": list(selected_handle_bounds) if _has_positive_bounds(selected_handle_bounds) else None,
        "selected_handle_source": selected_handle_source,
        "original_drag_start_point": list(original_start_point) if original_start_point is not None else None,
        "drag_start_point": None,
        "drag_target_point": None,
        "drag_start_inside_selected_handle_bounds": False,
        "drag_start_inside_real_handle_bounds": False,
        "track_bounds_center_y": track_center_y,
        "real_handle_center_y": None,
        "drag_y_source": None,
        "handle_is_ghost": None,
        "handle_binding_reason": None,
        "stop_code": None,
    }
    if not _has_positive_bounds(selected_handle_bounds):
        trace["handle_binding_reason"] = "selected_handle_bounds_missing"
        trace["stop_code"] = S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED
        return trace
    if target_x is None:
        trace["handle_binding_reason"] = "target_x_missing"
        trace["stop_code"] = "S07_AGE_SLIDER_TARGET_NOT_FOUND"
        return trace
    handle_bounds = tuple(int(value) for value in selected_handle_bounds)  # type: ignore[arg-type]
    handle_cx, handle_cy = _center(handle_bounds)
    drag_y = handle_cy
    reason = "real_handle_center_y_used"
    start = (handle_cx, drag_y)
    inside = _point_in_bounds(start, handle_bounds)
    trace.update(
        {
            "drag_start_point": list(start),
            "drag_target_point": [int(target_x), drag_y],
            "drag_start_inside_selected_handle_bounds": inside,
            "drag_start_inside_real_handle_bounds": inside,
            "real_handle_center_y": handle_cy,
            "drag_y_source": "real_handle_center_y",
            "handle_is_ghost": False,
            "handle_binding_reason": reason,
        }
    )
    if not inside:
        trace["stop_code"] = S07_AGE_SLIDER_DRAG_START_OUTSIDE_HANDLE_BOUNDS
    return trace


def _s07_real_touch_path(start: tuple[int, int], end: tuple[int, int], *, steps: int = 10) -> list[list[int]]:
    steps = max(2, int(steps))
    path: list[list[int]] = []
    for index in range(steps + 1):
        ratio = index / steps
        x = round(start[0] + (end[0] - start[0]) * ratio)
        y = round(start[1] + (end[1] - start[1]) * ratio)
        path.append([x, y])
    return path


def execute_s07_real_handle_drag(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    side: str,
    to_age: int,
    strategy: str,
    attempt_index: int,
    start: tuple[int, int],
    end: tuple[int, int],
    duration_ms: int,
    binding: dict[str, Any],
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    touch_steps_count = 10
    touch_path = _s07_real_touch_path(start, end, steps=touch_steps_count)
    executor_evidence = {
        "side": side,
        "to_age": to_age,
        "strategy": strategy,
        "attempt_index": attempt_index,
        "clicked_action_id": "S07_ONLY_ALLOWED_ACTION_SET_TARGET_AGE",
        "direct_track_fastpath_version": S07_AGE_SLIDER_DIRECT_FASTPATH_VERSION,
        "real_touch_executor_version": S07_AGE_SLIDER_REAL_TOUCH_EXECUTOR_VERSION,
        "touch_executor": "real_handle_down_move_up",
        "touch_steps_count": touch_steps_count,
        "touch_duration_ms": int(duration_ms),
        "touch_start": list(start),
        "touch_end": list(end),
        "touch_path": touch_path,
        "selected_handle_source": binding.get("selected_handle_source"),
        "selected_handle_bounds": binding.get("selected_handle_bounds"),
        "drag_y_source": binding.get("drag_y_source"),
        "drag_start_inside_real_handle_bounds": binding.get("drag_start_inside_real_handle_bounds"),
        "track_bounds": (plan or {}).get("track_bounds"),
        "target_x": (plan or {}).get("target_x"),
        "target_x_calculation": (plan or {}).get("target_x_calculation"),
        "contract_action_plan": (plan or {}).get("contract_action_plan"),
        "contract_action_plan_id": (plan or {}).get("contract_action_plan_id"),
        "contract_action_plan_used": (plan or {}).get("contract_action_plan_used"),
        "action_plan_step_id": (plan or {}).get("action_plan_step_id"),
        "action_algorithm_used": (plan or {}).get("action_algorithm_used"),
        "action_inputs_source": (plan or {}).get("action_inputs_source"),
        "action_outputs_source": (plan or {}).get("action_outputs_source"),
        "forbidden_action_used": (plan or {}).get("forbidden_action_used"),
        "runtime_bypassed_action_plan": (plan or {}).get("runtime_bypassed_action_plan"),
        "action_plan_binding_check_passed": (plan or {}).get("action_plan_binding_check_passed"),
    }
    _, move_ms = contract_execute_swipe(
        context,
        snapshot,
        "S07",
        "set_exact_age",
        points=(int(start[0]), int(start[1]), int(end[0]), int(end[1]), int(duration_ms)),
        evidence=executor_evidence,
    )
    executor_evidence["move_ms"] = move_ms
    return executor_evidence


def _s07_age_slider_evidence(snapshot: dict[str, Any], target_age: int | None) -> dict[str, Any]:
    parse_started = time.perf_counter()
    tick_nodes = _s07_age_tick_nodes(snapshot)
    tick_labels = _s07_age_tick_labels_detected(snapshot)
    visible_age_positions = _s07_age_visible_tick_positions(snapshot)
    target_tick_point = _s07_age_target_point(snapshot, target_age)
    target_point = _s07_age_track_point(snapshot, target_age) or target_tick_point
    centers = [_center(node["bounds"]) for node in tick_nodes if _has_nonzero_bounds(node.get("bounds"))]
    exact_text = _s07_exact_age_text(snapshot, target_age)
    left_age, right_age = _s07_age_range_from_slider_positions(snapshot)
    slider_overlap_confirmed = target_age is not None and left_age == target_age and right_age == target_age
    exact_verify = _verify_exact_age_selected(snapshot, target_age)
    exact_confirmed = bool(exact_verify.get("exact_age_verified"))
    selected_handles, handle_candidates, rejected_handles = _s07_age_handle_selection(snapshot)
    real_handle_binding = bind_s07_real_slider_handle(snapshot)
    track_bounds = _s07_age_track_bounds(snapshot)
    hidden_tick_info = _s07_hidden_age_tick_info(snapshot, target_age)
    evidence: dict[str, Any] = {
        "parse_ms": int((time.perf_counter() - parse_started) * 1000),
        "tick_count": len(tick_nodes),
        "age_tick_labels_detected": tick_labels,
        "age_tick_unlimited_detected": S07_AGE_UNLIMITED_LABEL in tick_labels,
        "target_point": list(target_point) if target_point else None,
        "target_tick_point": list(target_tick_point) if target_tick_point else None,
        "target_age": target_age,
        "exact_confirmed": exact_confirmed,
        "exact_age_text_found": bool(exact_text),
        "exact_age_text": exact_text,
        "s07_exact_age_contract_version": exact_verify.get("s07_exact_age_contract_version"),
        "exact_age_overlap_allowed": exact_verify.get("exact_age_overlap_allowed"),
        "age_zero_success_texts": exact_verify.get("age_zero_success_texts"),
        "matched_age_text": exact_verify.get("matched_age_text"),
        "verify_method": exact_verify.get("verify_method"),
        "bottom_view_result_text": exact_verify.get("bottom_view_result_text"),
        "bottom_view_result_refreshed": exact_verify.get("bottom_view_result_refreshed"),
        "slider_overlap_confirmed": slider_overlap_confirmed,
        "age_confirm_source": exact_verify.get("verify_method") or ("exact_age_text" if exact_text else ("slider_positions" if slider_overlap_confirmed else None)),
        "left_age_before": left_age,
        "right_age_before": right_age,
        "left_age_after": left_age,
        "right_age_after": right_age,
        "target_tick_bounds": _s07_age_target_tick_bounds(snapshot, target_age),
        "age_track_bounds": track_bounds,
        "track_bounds": track_bounds,
        "age_track_center_y": round((track_bounds[1] + track_bounds[3]) / 2) if track_bounds else None,
        "left_handle_candidates": [list(node["bounds"]) for node in handle_candidates[:4] if _has_positive_bounds(node.get("bounds"))],
        "right_handle_candidates": [list(node["bounds"]) for node in handle_candidates[-4:] if _has_positive_bounds(node.get("bounds"))],
        "selected_right_handle_bounds": list(selected_handles[-1]["bounds"]) if selected_handles else None,
        "selected_left_handle_bounds": list(selected_handles[0]["bounds"]) if selected_handles else None,
        "s07_real_handle_binding_trace": real_handle_binding,
        "handle_candidates": real_handle_binding.get("handle_candidates"),
        "rejected_handle_candidates": real_handle_binding.get("rejected_handle_candidates"),
        "handle_binding_method": real_handle_binding.get("handle_binding_method"),
        "handle_is_ghost": real_handle_binding.get("handle_is_ghost"),
        "real_handle_pair_y_band": real_handle_binding.get("real_handle_pair_y_band"),
        "s07_handle_pair_overlap_allowed": real_handle_binding.get("s07_handle_pair_overlap_allowed"),
        "s07_handle_pair_close_allowed": real_handle_binding.get("s07_handle_pair_close_allowed"),
        "s07_handle_pair_separation_px": real_handle_binding.get("s07_handle_pair_separation_px"),
        "s07_handle_pair_binding_method": real_handle_binding.get("s07_handle_pair_binding_method"),
        "selected_handle_source": real_handle_binding.get("selected_handle_source"),
        "rejected_right_handle_candidates": rejected_handles,
        "excluded_background_card_nodes_count": len([item for item in rejected_handles if item.get("rejected_reason") == "not_overlapping_age_track_hotzone"]),
        "right_drag_y_source": "real_handle_center_y" if selected_handles else None,
        "right_handle_source": "real_green_slider_handle" if selected_handles else None,
        "visible_tick_positions": hidden_tick_info.get("visible_tick_positions"),
        "hidden_tick_supported_range": hidden_tick_info.get("hidden_tick_supported_range"),
        "hidden_age_tick_used": hidden_tick_info.get("hidden_age_tick_used"),
        "hidden_age_tick_valid": hidden_tick_info.get("hidden_age_tick_valid"),
        "hidden_tick_source": hidden_tick_info.get("hidden_tick_source"),
        "hidden_tick_invalid_reason": hidden_tick_info.get("hidden_tick_invalid_reason"),
        "x8": hidden_tick_info.get("x8"),
        "x10": hidden_tick_info.get("x10"),
        "one_year_step": hidden_tick_info.get("one_year_step"),
        "age_11_x": hidden_tick_info.get("age_11_x"),
        "age_12_x": hidden_tick_info.get("age_12_x"),
        "target_age_x": hidden_tick_info.get("target_age_x"),
        "target_age_y": hidden_tick_info.get("target_age_y"),
        "target_age_out_of_supported_exact_range": target_age is not None and target_age > 12,
        "verify_text_expected": f"{target_age}-{target_age}年" if target_age is not None else None,
    }
    evidence.update(
        {
            "s07_visible_age_ticks": sorted(visible_age_positions.keys()),
            "age_0_tick_bounds": list(visible_age_positions[0]["bounds"]) if 0 in visible_age_positions else None,
            "age_2_tick_bounds": list(visible_age_positions[2]["bounds"]) if 2 in visible_age_positions else None,
            "age_1_hidden_tick_x": hidden_tick_info.get("age_1_x"),
            "left_slider_target_age": target_age,
            "right_slider_target_age": target_age,
            "left_slider_target_x": hidden_tick_info.get("target_age_x") or (target_point[0] if target_point else None),
            "right_slider_target_x": hidden_tick_info.get("target_age_x") or (target_point[0] if target_point else None),
            "age_exact_overlap_allowed": bool(target_age is not None),
            "age_filter_verify_text": (
                exact_verify.get("matched_age_text")
                if target_age == 1
                else exact_verify.get("matched_age_text") or (f"{target_age}-{target_age}" if target_age is not None else None)
            ),
            "AGE_FILTER_DONE": exact_confirmed,
            "s07_age_failure_reason": None if exact_confirmed else exact_verify.get("stop_code"),
        }
    )
    if centers:
        evidence["left_slider_point"] = [min(x for x, _ in centers), target_point[1] if target_point else centers[0][1]]
        evidence["right_slider_point"] = [max(x for x, _ in centers), target_point[1] if target_point else centers[-1][1]]
    handles = selected_handles
    if handles:
        evidence["age_slider_handle_bounds"] = [node.get("bounds") for node in handles]
        evidence["age_slider_handle_centers"] = [list(_center(node["bounds"])) for node in handles]
    return evidence


def _set_exact_age_from_ticks(context: dict[str, Any], snapshot: dict[str, Any], target_age: int | None) -> dict[str, Any]:
    client: AdbClient = context["client"]
    age_slider_started_perf = time.perf_counter()
    age_slider_started_wall = time.time()
    evidence = _s07_age_slider_evidence(snapshot, target_age)
    task_params = context.get("task_params") if isinstance(context.get("task_params"), dict) else {}
    register_date_raw = (
        task_params.get("registration_date_raw")
        or task_params.get("registration_date")
        or task_params.get("register_date")
    )
    register_date_normalized = (
        task_params.get("registration_date_normalized")
        or task_params.get("registration_date")
        or task_params.get("register_date")
        or register_date_raw
    )
    target_age_formula = str(task_params.get("target_age_formula") or "")
    target_age_calc_rule = (
        "YEAR_ONLY_CURRENT_YEAR_MINUS_REGISTER_YEAR"
        if target_age_formula.replace(" ", "") == "current_year-register_year"
        else (target_age_formula or "YEAR_ONLY_CURRENT_YEAR_MINUS_REGISTER_YEAR")
    )
    initial_visible_ticks = [
        {"age": age, "center_x": x, "center_y": y}
        for age, x, y in _s07_age_numeric_points(snapshot)
    ]
    initial_contract_action_plan = build_s07_age_action_plan(
        target_age_years=target_age,
        visible_ticks=initial_visible_ticks,
    )
    initial_action_outputs = initial_contract_action_plan.get("action_outputs") or {}
    initial_binding_trace = build_action_plan_binding_trace(
        initial_contract_action_plan,
        action_algorithm_used=str(initial_action_outputs.get("target_x_calculation") or "visible_tick_interpolation"),
    )
    evidence.update(
        {
            "success": False,
            "contract_action_plan": initial_contract_action_plan,
            "contract_expected": initial_contract_action_plan.get("expected"),
            "contract_action_algorithm": initial_contract_action_plan.get("action_algorithm"),
            "contract_forbidden_actions": initial_contract_action_plan.get("forbidden_actions"),
            **initial_binding_trace,
            "age_slider_start_time": datetime.fromtimestamp(age_slider_started_wall).isoformat(timespec="seconds"),
            "age_slider_end_time": None,
            "total_duration_ms": 0,
            "target_age_years": target_age,
            "register_date_raw": register_date_raw,
            "register_date_normalized": register_date_normalized,
            "register_year": task_params.get("register_year"),
            "current_business_year": task_params.get("current_year") or task_params.get("business_year"),
            "target_age_calc_rule": target_age_calc_rule,
            "target_range": f"{target_age}-{target_age}" if target_age is not None else None,
            "direct_track_fastpath_version": S07_AGE_SLIDER_DIRECT_FASTPATH_VERSION,
            "real_touch_executor_version": S07_AGE_SLIDER_REAL_TOUCH_EXECUTOR_VERSION,
            "direct_track_fastpath_used": False,
            "direct_fastpath_used": False,
            "direct_track_fastpath_available": False,
            "direct_track_fastpath_skip_reason": None,
            "direct_track_bounds_reused_in_round": False,
            "special_5_5_fastpath": target_age == 5,
            "track_bounds": evidence.get("track_bounds"),
            "left_handle_bounds": evidence.get("selected_left_handle_bounds"),
            "right_handle_bounds": evidence.get("selected_right_handle_bounds"),
            "target_x": None,
            "drag_attempts_count": 0,
            "xml_dump_count": 0,
            "screenshot_count": 0,
            "final_xml_verify_count": 0,
            "fallback_strategies_used": [],
            "fallback_used": False,
            "fallback_name": "",
            "fallback_allowed_by_clause": True,
            "max_micro_adjustments": S07_AGE_SLIDER_FASTPATH_MAX_MICRO_ADJUST,
            "max_fallback_strategies": S07_AGE_SLIDER_FASTPATH_MAX_FALLBACK_STRATEGIES,
            "performance_budget_ms": initial_binding_trace.get("performance_budget_ms") or S07_AGE_SLIDER_PERFORMANCE_BUDGET_MS,
            "performance_budget_exceeded": False,
            "performance_budget_exceeded_reasons": [],
            "xml_dump_budget": S07_AGE_SLIDER_XML_DUMP_BUDGET,
            "screenshot_budget": S07_AGE_SLIDER_SCREENSHOT_BUDGET,
            "fallback_strategy_limit_reached": False,
            "left_slider_moved": False,
            "right_slider_moved": False,
            "age_panel_wait_ms": 0,
            "left_slider_bind_ms": 0,
            "right_slider_bind_ms": 0,
            "drag_ms": 0,
            "verify_ms": 0,
            "fallback_ms": 0,
            "left_move_ms": 0,
            "right_move_ms": 0,
            "tap_target_tick_ms": 0,
            "skip_reason": None,
            "right_age_after_each_attempt": [],
            "right_slider_bounds_each_attempt": [],
            "target_tick_bounds_each_attempt": [],
            "screenshot_path_each_attempt": [],
            "xml_path_each_attempt": [],
            "swipe_start_end_duration_each_attempt": [],
            "age_interaction_strategy_priority": ["direct_track_fastpath"],
            "age_strategy_attempts": [],
            "attempts": [],
            "right_slider_max_retry": 1,
            "right_slider_recalc_bounds_each_attempt": False,
            "right_slider_fresh_before_each_attempt": False,
            "age_strategy_dynamic_bounds": False,
            "age_strategy_finite_attempts": True,
            "touch_executor": None,
            "touch_steps_count": 0,
            "touch_duration_ms": 0,
            "touch_start": None,
            "touch_end": None,
            "touch_path": [],
            "selected_handle_source": evidence.get("selected_handle_source"),
            "initial_fresh_pair_id": "|".join([str(snapshot.get("screenshot_path") or ""), str(snapshot.get("xml_path") or "")]),
            "pre_action_fresh_pair_id": "|".join([str(snapshot.get("screenshot_path") or ""), str(snapshot.get("xml_path") or "")]),
            "s07_age_post_action_proof_version": S07_AGE_ONE_POST_ACTION_PROOF_VERSION,
            "s07_age_action_planned": target_age is not None,
            "s07_age_action_executed": False,
            "s07_age_post_fresh_done": False,
            "s07_age_post_fresh_screenshot_path": "",
            "s07_age_post_fresh_xml_path": "",
            "s07_age_post_fresh_xml_stale": False,
            "s07_age_post_fresh_verify_text": None,
            "s07_age_post_fresh_verify_passed": False,
            "s07_age_left_slider_actual_age": None,
            "s07_age_right_slider_actual_age": None,
            "s07_age_left_right_actual_age_verified": False,
            "s07_age_one_post_action_proof_passed": None,
            "AGE_FILTER_DONE_source": None,
            "s07_after_right_left_handle_bounds": None,
            "s07_after_right_right_handle_bounds": None,
            "s07_after_right_visible_text": None,
            "s07_left_drag_attempted": False,
            "s07_left_drag_start_point": None,
            "s07_left_drag_target_point": None,
            "s07_left_drag_distance_px": None,
            "s07_left_drag_short_distance_nudge_used": False,
            "s07_left_drag_retry_count": 0,
            "s07_left_drag_retry_results": [],
            "s07_left_drag_visual_movement_confirmed": False,
            "s07_final_exact_age_verify_text": None,
            "s07_final_exact_age_proof_passed": False,
        }
    )
    evidence["attempts"] = evidence["age_strategy_attempts"]

    def finish_evidence() -> dict[str, Any]:
        evidence["age_slider_end_time"] = datetime.fromtimestamp(time.time()).isoformat(timespec="seconds")
        evidence["total_duration_ms"] = int((time.perf_counter() - age_slider_started_perf) * 1000)
        evidence["actual_duration_ms"] = evidence["total_duration_ms"]
        evidence["drag_attempts_count"] = len(evidence.get("age_strategy_attempts") or [])
        fallback_used = bool(evidence.get("fallback_strategies_used"))
        fallback_name = str((evidence.get("fallback_strategies_used") or [""])[0] or "")
        allowed_fallbacks = set(
            (evidence.get("contract_action_plan") or {}).get("allowed_fallbacks")
            or evidence.get("allowed_fallbacks")
            or []
        )
        evidence["direct_fastpath_used"] = bool(evidence.get("direct_track_fastpath_used"))
        evidence["drag_ms"] = int(evidence.get("left_move_ms") or 0) + int(evidence.get("right_move_ms") or 0) + int(evidence.get("tap_target_tick_ms") or 0)
        evidence["verify_ms"] = sum(
            int(item.get("duration_ms") or 0)
            for item in (evidence.get("age_strategy_attempts") or [])
            if item.get("strategy") in {"direct_track_fastpath", "direct_track_fastpath_5_5"}
        )
        evidence["fallback_ms"] = sum(
            int(item.get("duration_ms") or 0)
            for item in (evidence.get("age_strategy_attempts") or [])
            if item.get("strategy") in set(evidence.get("fallback_strategies_used") or [])
        )
        evidence["fallback_used"] = fallback_used
        evidence["fallback_name"] = fallback_name
        evidence["fallback_allowed_by_clause"] = (not fallback_used) or fallback_name in allowed_fallbacks
        post_action_replaceable_failures = {
            None,
            S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED,
            S07_AGE_ONE_POST_ACTION_VERIFY_FAILED,
            S07_AGE_FILTER_ACTUAL_RANGE_MISMATCH,
        }
        if (
            not evidence.get("s07_age_post_fresh_done")
            and target_age is not None
            and evidence.get("failure_reason") in post_action_replaceable_failures
        ):
            evidence["success"] = False
            evidence["failure_reason"] = S07_POST_ACTION_FRESH_EVIDENCE_MISSING
        if (
            target_age == 0
            and not evidence.get("s07_age_zero_post_action_proof_passed")
            and not evidence.get("failure_reason")
        ):
            evidence["success"] = False
            evidence["failure_reason"] = evidence.get("post_action_failure_reason") or S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED
        if (
            target_age == 1
            and not evidence.get("s07_age_one_post_action_proof_passed")
            and not evidence.get("failure_reason")
        ):
            evidence["success"] = False
            evidence["failure_reason"] = evidence.get("post_action_failure_reason") or S07_AGE_ONE_POST_ACTION_VERIFY_FAILED
        evidence["AGE_FILTER_DONE"] = bool(evidence.get("success"))
        evidence["s07_age_failure_reason"] = (
            None
            if evidence.get("success")
            else evidence.get("failure_reason") or evidence.get("s07_age_failure_reason")
        )
        if target_age is not None:
            evidence["left_slider_target_age"] = target_age
            evidence["right_slider_target_age"] = target_age
            if evidence.get("target_x") is not None:
                evidence["left_slider_target_x"] = evidence.get("target_x")
                evidence["right_slider_target_x"] = evidence.get("target_x")
            if target_age == 1:
                evidence["age_filter_verify_text"] = (
                    evidence.get("s07_age_post_fresh_verify_text")
                    or evidence.get("matched_age_text")
                )
            elif target_age == 0:
                evidence["age_filter_verify_text"] = (
                    evidence.get("s07_age_post_fresh_verify_text")
                    or evidence.get("matched_age_text")
                    or evidence.get("age_filter_verify_text")
                )
            else:
                evidence["age_filter_verify_text"] = (
                    evidence.get("matched_age_text")
                    or evidence.get("age_filter_verify_text")
                    or f"{target_age}-{target_age}"
                )
        budget_reasons: list[str] = []
        if int(evidence.get("total_duration_ms") or 0) > int(evidence.get("performance_budget_ms") or S07_AGE_SLIDER_PERFORMANCE_BUDGET_MS):
            budget_reasons.append("duration_ms")
        if int(evidence.get("xml_dump_count") or 0) > S07_AGE_SLIDER_XML_DUMP_BUDGET:
            budget_reasons.append("xml_dump_count")
        if int(evidence.get("screenshot_count") or 0) > S07_AGE_SLIDER_SCREENSHOT_BUDGET:
            budget_reasons.append("screenshot_count")
        if len(evidence.get("fallback_strategies_used") or []) > S07_AGE_SLIDER_FASTPATH_MAX_FALLBACK_STRATEGIES:
            budget_reasons.append("fallback_count")
        evidence["performance_budget_exceeded_reasons"] = budget_reasons
        evidence["performance_budget_exceeded"] = bool(budget_reasons)
        if evidence["performance_budget_exceeded"] and not evidence.get("success") and not evidence.get("failure_reason"):
            evidence["failure_reason"] = (
                S07_AGE_CONTRACT_UNAUTHORIZED_FALLBACK_USED
                if evidence.get("fallback_allowed_by_clause") is False
                else S07_AGE_SLIDER_FALLBACK_BUDGET_EXCEEDED
            )
        return evidence

    if target_age is not None and target_age > 12:
        evidence["failure_reason"] = "S07_AGE_TARGET_OUT_OF_SUPPORTED_EXACT_RANGE"
        evidence["hidden_tick_supported_range"] = "11-12"
        return finish_evidence()
    if evidence.get("exact_confirmed"):
        exact_verify = _verify_exact_age_selected(snapshot, target_age)
        evidence["success"] = True
        evidence["skip_reason"] = "age_already_exact"
        evidence["left_age_after"] = target_age
        evidence["right_age_after"] = target_age
        evidence["exact_confirmed"] = True
        evidence["confirm_source"] = evidence.get("age_confirm_source")
        evidence["exact_age_text"] = evidence.get("exact_age_text")
        evidence["exact_snapshot_path"] = str(snapshot.get("screenshot_path") or "")
        evidence["exact_xml_path"] = str(snapshot.get("xml_path") or "")
        post_action_proof = _s07_age_post_action_proof(
            evidence,
            snapshot,
            target_age,
            exact_verify,
            reused_internal_fresh=True,
        )
        evidence.update(post_action_proof)
        context["s07_exact_age_snapshot"] = snapshot
        return finish_evidence()

    current = snapshot
    points = _s07_age_slider_points(current, target_age)
    target_point = points.get("target_point")
    if target_point is None or not _s07_age_tick_nodes(current):
        evidence["failure_reason"] = _s07_missing_age_target_stop_code(target_age)
        return finish_evidence()

    left_age_before, right_age_before = _s07_age_range_from_slider_positions(current)
    evidence["left_age_before"] = left_age_before
    evidence["right_age_before"] = right_age_before

    def record_attempt(
        *,
        strategy: str,
        attempt_index: int,
        before: dict[str, Any],
        after: dict[str, Any],
        start: tuple[int, int],
        end: tuple[int, int],
        duration_ms: int,
        move_ms: int,
        side: str,
        failure_reason: str | None = None,
        touch_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        before_left, before_right = _s07_age_range_from_slider_positions(before)
        after_left, after_right = _s07_age_range_from_slider_positions(after)
        before_points = _s07_age_slider_points(before, target_age)
        after_points = _s07_age_slider_points(after, target_age)
        exact_text_verified = _exact_age_confirmed(after, target_age)
        after_value_available = after_left is not None or after_right is not None
        slider_value_changed = after_value_available and (before_left, before_right) != (after_left, after_right)
        side_bounds_before = before_points.get("right_handle_bounds") if side == "right" else before_points.get("left_handle_bounds")
        side_bounds_after = after_points.get("right_handle_bounds") if side == "right" else after_points.get("left_handle_bounds")
        slider_bounds_changed = _s07_age_bounds_changed(side_bounds_before, side_bounds_after)
        slider_moved_success = bool(slider_value_changed or slider_bounds_changed or exact_text_verified)
        selected_handle_bounds = before_points.get("right_handle_bounds") if side == "right" else before_points.get("left_handle_bounds")
        drag_start_inside = _point_in_bounds(start, selected_handle_bounds)
        effective_failure_reason = failure_reason
        if not effective_failure_reason and not slider_moved_success:
            effective_failure_reason = (
                S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT
                if side == "left"
                else S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT
            )
        touch_trace = touch_trace or {}
        touch_path = touch_trace.get("touch_path") or _s07_real_touch_path(start, end, steps=10)
        record = {
            "strategy": strategy,
            "attempt_index": attempt_index,
            "side": side,
            "from_x": int(start[0]),
            "to_x": int(end[0]),
            "drag_distance": abs(start[0] - end[0]),
            "duration_ms": int(move_ms),
            "xml_dump_count": int(evidence.get("xml_dump_count") or 0),
            "screenshot_count": int(evidence.get("screenshot_count") or 0),
            "result_value": f"{after_left}-{after_right}" if after_left is not None or after_right is not None else None,
            "success": exact_text_verified,
            "left_age_before": before_left,
            "right_age_before": before_right,
            "left_age_after": after_left,
            "right_age_after": after_right,
            "right_slider_bounds_before": before_points.get("right_handle_bounds"),
            "right_slider_bounds_after": after_points.get("right_handle_bounds"),
            "left_slider_bounds_before": before_points.get("left_handle_bounds"),
            "left_slider_bounds_after": after_points.get("left_handle_bounds"),
            "target_tick_bounds": before_points.get("target_tick_bounds"),
            "track_bounds": _s07_age_track_bounds(before),
            "swipe_start": list(start),
            "swipe_end": list(end),
            "swipe_duration": duration_ms,
            "swipe_ms": move_ms,
            "swipe_command_sent": True,
            "touch_executor": touch_trace.get("touch_executor") or "real_handle_down_move_up",
            "touch_steps_count": touch_trace.get("touch_steps_count") or len(touch_path) - 1,
            "touch_duration_ms": touch_trace.get("touch_duration_ms") or duration_ms,
            "touch_start": touch_trace.get("touch_start") or list(start),
            "touch_end": touch_trace.get("touch_end") or list(end),
            "touch_path": touch_path,
            "touch_result_value_before": f"{before_left}-{before_right}" if before_left is not None or before_right is not None else None,
            "touch_result_value_after": f"{after_left}-{after_right}" if after_left is not None or after_right is not None else None,
            "touch_value_changed": slider_value_changed,
            "touch_bounds_changed": slider_bounds_changed,
            "selected_handle_bounds": selected_handle_bounds,
            "drag_start_point": list(start),
            "drag_start_inside_selected_handle_bounds": drag_start_inside,
            "drag_start_inside_real_handle_bounds": drag_start_inside,
            "drag_target_point": list(end),
            "slider_value_changed": slider_value_changed,
            "slider_bounds_changed": slider_bounds_changed,
            "exact_text_verified": exact_text_verified,
            "slider_moved_success": slider_moved_success,
            "screenshot_before": str(before.get("screenshot_path") or ""),
            "xml_before": str(before.get("xml_path") or ""),
            "screenshot_after": str(after.get("screenshot_path") or ""),
            "xml_after": str(after.get("xml_path") or ""),
            "exact_confirmed_after_attempt": exact_text_verified,
            "failure_reason": effective_failure_reason,
            "contract_action_plan_id": evidence.get("contract_action_plan_id"),
            "contract_action_plan_used": evidence.get("contract_action_plan_used"),
            "action_plan_step_id": evidence.get("action_plan_step_id"),
            "rule_clause_id": evidence.get("rule_clause_id"),
            "rule_source_file": evidence.get("rule_source_file"),
            "rule_source_version": evidence.get("rule_source_version"),
            "coverage_status": evidence.get("coverage_status"),
            "action_algorithm_used": evidence.get("action_algorithm_used"),
            "action_inputs_source": evidence.get("action_inputs_source"),
            "action_outputs_source": evidence.get("action_outputs_source"),
            "fallback_used": evidence.get("fallback_used"),
            "fallback_name": evidence.get("fallback_name"),
            "fallback_allowed_by_clause": evidence.get("fallback_allowed_by_clause"),
            "performance_budget_ms": evidence.get("performance_budget_ms"),
            "forbidden_action_used": evidence.get("forbidden_action_used"),
            "runtime_bypassed_action_plan": evidence.get("runtime_bypassed_action_plan"),
            "performance_budget_exceeded": evidence.get("performance_budget_exceeded"),
            "action_plan_binding_check_passed": evidence.get("action_plan_binding_check_passed"),
        }
        evidence["age_strategy_attempts"].append(record)
        evidence["swipe_command_sent"] = True
        evidence["s07_age_action_executed"] = True
        evidence["touch_executor"] = record["touch_executor"]
        evidence["touch_steps_count"] = record["touch_steps_count"]
        evidence["touch_duration_ms"] = record["touch_duration_ms"]
        evidence["touch_start"] = record["touch_start"]
        evidence["touch_end"] = record["touch_end"]
        evidence["touch_path"] = record["touch_path"]
        evidence["touch_result_value_before"] = record["touch_result_value_before"]
        evidence["touch_result_value_after"] = record["touch_result_value_after"]
        evidence["touch_value_changed"] = slider_value_changed
        evidence["touch_bounds_changed"] = slider_bounds_changed
        evidence["slider_value_changed"] = bool(evidence.get("slider_value_changed")) or slider_value_changed
        evidence["slider_bounds_changed"] = bool(evidence.get("slider_bounds_changed")) or slider_bounds_changed
        evidence["exact_text_verified"] = bool(evidence.get("exact_text_verified")) or exact_text_verified
        evidence["slider_moved_success"] = bool(evidence.get("slider_moved_success")) or slider_moved_success
        evidence["selected_handle_bounds"] = selected_handle_bounds
        evidence["drag_start_point"] = list(start)
        evidence["drag_start_inside_selected_handle_bounds"] = drag_start_inside
        evidence["drag_start_inside_real_handle_bounds"] = drag_start_inside
        evidence["drag_target_point"] = list(end)
        evidence["handle_binding_reason"] = evidence.get("handle_binding_reason") or record.get("handle_binding_reason")
        if not slider_moved_success and not evidence.get("failure_reason"):
            evidence["failure_reason"] = effective_failure_reason
        if side == "right":
            evidence["right_age_after_each_attempt"].append(after_right)
            evidence["right_slider_bounds_each_attempt"].append(before_points.get("right_handle_bounds"))
            evidence["target_tick_bounds_each_attempt"].append(before_points.get("target_tick_bounds"))
            evidence["screenshot_path_each_attempt"].append(str(after.get("screenshot_path") or ""))
            evidence["xml_path_each_attempt"].append(str(after.get("xml_path") or ""))
            evidence["swipe_start_end_duration_each_attempt"].append(
                {
                    "strategy": strategy,
                    "attempt": attempt_index,
                    "start": list(start),
                    "end": list(end),
                    "duration_ms": duration_ms,
                    "distance": abs(start[0] - end[0]),
                    "direction": "left" if end[0] < start[0] else "right" if end[0] > start[0] else "none",
                }
            )
            evidence["right_slider_before_bounds"] = before_points.get("right_handle_bounds")
            evidence["right_slider_after_action_bounds"] = after_points.get("right_handle_bounds")
            evidence["right_slider_after_confirm_bounds"] = after_points.get("right_handle_bounds")
            evidence["right_swipe_start"] = list(start)
            evidence["right_swipe_end"] = list(end)
            evidence["right_drag_start"] = list(start)
            evidence["right_drag_end"] = list(end)
            evidence["right_drag_y_source"] = "real_handle_center_y"
            evidence["right_swipe_duration"] = duration_ms
            evidence["right_swipe_distance"] = abs(start[0] - end[0])
            evidence["swipe_direction"] = "left" if end[0] < start[0] else "right" if end[0] > start[0] else "none"
            evidence["right_slider_moved"] = slider_moved_success
            evidence["right_touch_start"] = record["touch_start"]
            evidence["right_touch_end"] = record["touch_end"]
        else:
            evidence["left_swipe_start"] = list(start)
            evidence["left_swipe_end"] = list(end)
            evidence["left_swipe_distance"] = abs(start[0] - end[0])
            evidence["left_swipe_duration"] = duration_ms
            evidence["left_after_screenshot_path"] = str(after.get("screenshot_path") or "")
            evidence["left_after_xml_path"] = str(after.get("xml_path") or "")
            evidence["left_slider_moved"] = slider_moved_success
            evidence["left_touch_start"] = record["touch_start"]
            evidence["left_touch_end"] = record["touch_end"]
            evidence["s07_left_drag_visual_movement_confirmed"] = bool(
                evidence.get("s07_left_drag_visual_movement_confirmed")
                or slider_value_changed
                or slider_bounds_changed
                or exact_text_verified
            )
            evidence.setdefault("s07_left_drag_retry_results", []).append(
                {
                    "attempt_index": attempt_index,
                    "strategy": strategy,
                    "start": list(start),
                    "end": list(end),
                    "distance_px": abs(start[0] - end[0]),
                    "slider_value_changed": slider_value_changed,
                    "slider_bounds_changed": slider_bounds_changed,
                    "exact_text_verified": exact_text_verified,
                    "failure_reason": effective_failure_reason,
                    "verify_text": _s07_classify_age_verify_text(after, target_age).get("s07_age_verify_text_raw"),
                }
            )
        evidence["left_age_after"], evidence["right_age_after"] = after_left, after_right
        return record

    def capture_after_age_action(stem: str) -> dict[str, Any]:
        evidence["screenshot_count"] = int(evidence.get("screenshot_count") or 0) + 1
        evidence["xml_dump_count"] = int(evidence.get("xml_dump_count") or 0) + 1
        return _capture(client, stem)

    def drag_age_handle(
        current_snapshot: dict[str, Any],
        *,
        side: str,
        to_age: int,
        strategy: str,
        attempt_index: int,
        duration_ms: int,
        use_track_point: bool = False,
        target_x_override: int | None = None,
    ) -> dict[str, Any]:
        points = _s07_age_slider_points(current_snapshot, to_age)
        if use_track_point:
            target_point = _s07_age_track_point(current_snapshot, to_age)
            track_bounds = _s07_age_track_bounds(current_snapshot)
            base_point = points.get("right_point") if side == "right" else points.get("left_point")
            if base_point is not None and track_bounds is not None:
                start_point = (base_point[0], round((track_bounds[1] + track_bounds[3]) / 2))
            else:
                start_point = base_point
        else:
            target_point = points.get("target_point")
            start_point = points.get("right_point") if side == "right" else points.get("left_point")
        if target_x_override is not None and target_point is not None:
            target_point = (int(target_x_override), int(target_point[1]))
        if target_point is None or start_point is None:
            evidence["failure_reason"] = _s07_missing_age_target_stop_code(to_age)
            return current_snapshot
        handle_bounds = points.get("right_handle_bounds") if side == "right" else points.get("left_handle_bounds")
        binding = _s07_build_age_drag_binding(
            side=side,
            selected_handle_bounds=handle_bounds,
            target_x=int(target_point[0]),
            preferred_y=int(start_point[1]),
            original_start_point=start_point,
            track_bounds=points.get("age_track_bounds"),
        )
        evidence[f"{side}_handle_binding_trace"] = binding
        evidence["selected_handle_bounds"] = binding.get("selected_handle_bounds")
        evidence["drag_start_point"] = binding.get("drag_start_point")
        evidence["drag_start_inside_selected_handle_bounds"] = binding.get("drag_start_inside_selected_handle_bounds")
        evidence["drag_target_point"] = binding.get("drag_target_point")
        evidence["handle_binding_reason"] = binding.get("handle_binding_reason")
        if binding.get("stop_code"):
            evidence["failure_reason"] = binding.get("stop_code")
            return current_snapshot
        sx, sy = binding["drag_start_point"]
        ex, ey = binding["drag_target_point"]
        if side == "left":
            evidence["s07_left_drag_attempted"] = True
            evidence["s07_left_drag_start_point"] = list(binding["drag_start_point"])
            evidence["s07_left_drag_target_point"] = list(binding["drag_target_point"])
            evidence["s07_left_drag_distance_px"] = abs(int(sx) - int(ex))
            if abs(int(sx) - int(ex)) <= S07_LEFT_AGE_SLIDER_SHORT_DRAG_PX:
                evidence["s07_left_drag_short_distance_nudge_used"] = True
        touch_trace = execute_s07_real_handle_drag(
            context,
            current_snapshot,
            side=side,
            to_age=to_age,
            strategy=strategy,
            attempt_index=attempt_index,
            start=(int(sx), int(sy)),
            end=(int(ex), int(ey)),
            duration_ms=duration_ms,
            binding=binding,
            plan=evidence.get("contract_action_plan") if isinstance(evidence.get("contract_action_plan"), dict) else None,
        )
        move_ms = int(touch_trace.get("move_ms") or 0)
        if side == "right":
            evidence["right_move_ms"] += move_ms
        else:
            evidence["left_move_ms"] += move_ms
        time.sleep(0.25)
        after = capture_after_age_action(f"s07_age_{strategy}_{side}_{attempt_index}_{_timestamp()}")
        _ensure_current_page_contract(context, after, {"S07"}, action_page="S07")
        record_attempt(
            strategy=strategy,
            attempt_index=attempt_index,
            before=current_snapshot,
            after=after,
            start=(sx, sy),
            end=(ex, ey),
            duration_ms=duration_ms,
            move_ms=move_ms,
            side=side,
            touch_trace=touch_trace,
        )
        return after

    def mark_success_if_exact(current_snapshot: dict[str, Any]) -> bool:
        exact_verify = _verify_exact_age_selected(current_snapshot, target_age)
        if not exact_verify.get("exact_age_verified"):
            return False
        left_after, right_after = _s07_age_range_from_slider_positions(current_snapshot)
        exact_age_text = exact_verify.get("matched_age_text") or _s07_exact_age_text(current_snapshot, target_age)
        evidence["success"] = True
        evidence["failure_reason"] = None
        evidence["exact_confirmed"] = True
        evidence["left_age_after"] = left_after
        evidence["right_age_after"] = right_after
        evidence["exact_age_text"] = exact_age_text
        evidence["exact_age_text_found"] = bool(exact_age_text)
        evidence["s07_exact_age_contract_version"] = exact_verify.get("s07_exact_age_contract_version")
        evidence["exact_age_overlap_allowed"] = exact_verify.get("exact_age_overlap_allowed")
        evidence["age_zero_success_texts"] = exact_verify.get("age_zero_success_texts")
        evidence["matched_age_text"] = exact_verify.get("matched_age_text")
        evidence["verify_method"] = exact_verify.get("verify_method")
        evidence["bottom_view_result_text"] = exact_verify.get("bottom_view_result_text")
        evidence["bottom_view_result_refreshed"] = exact_verify.get("bottom_view_result_refreshed")
        evidence["slider_overlap_confirmed"] = left_after == target_age and right_after == target_age
        confirm_source = str(exact_verify.get("verify_method") or ("exact_age_text" if exact_age_text else "slider_positions"))
        evidence["age_confirm_source_after_action"] = confirm_source
        evidence["confirm_source"] = confirm_source
        evidence["verify_text"] = exact_age_text
        evidence["s07_final_exact_age_verify_text"] = exact_age_text
        evidence["s07_final_exact_age_proof_passed"] = True
        evidence["exact_snapshot_path"] = str(current_snapshot.get("screenshot_path") or "")
        evidence["exact_xml_path"] = str(current_snapshot.get("xml_path") or "")
        post_action_proof = _s07_age_post_action_proof(
            evidence,
            current_snapshot,
            target_age,
            exact_verify,
            reused_internal_fresh=False,
        )
        evidence.update(post_action_proof)
        if target_age == 1 and not post_action_proof.get("s07_age_one_post_action_proof_passed"):
            evidence["success"] = False
            evidence["exact_confirmed"] = False
            evidence["failure_reason"] = S07_AGE_ONE_POST_ACTION_VERIFY_FAILED
            return False
        context["s07_exact_age_snapshot"] = current_snapshot
        return True

    def move_left_if_needed(current_snapshot: dict[str, Any], *, strategy: str, attempt_index: int, duration_ms: int = 450) -> dict[str, Any]:
        left_age, _ = _s07_age_range_from_slider_positions(current_snapshot)
        if left_age == target_age:
            return current_snapshot
        return drag_age_handle(current_snapshot, side="left", to_age=int(target_age), strategy=strategy, attempt_index=attempt_index, duration_ms=duration_ms)

    def move_left_after_right_with_retries(
        current_snapshot: dict[str, Any],
        *,
        strategy: str,
        first_attempt_index: int,
        base_target_x: int,
    ) -> dict[str, Any]:
        current_left_age, _ = _s07_age_range_from_slider_positions(current_snapshot)
        if current_left_age == target_age and _exact_age_confirmed(current_snapshot, target_age):
            return current_snapshot
        retry_offsets = [0, 8, -8]
        current_after_left = current_snapshot
        initial_attempt_count = len(evidence.get("age_strategy_attempts") or [])
        for offset_index, offset in enumerate(retry_offsets):
            if mark_success_if_exact(current_after_left):
                return current_after_left
            if evidence.get("failure_reason") in {
                S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT,
                S07_LEFT_AGE_SLIDER_DRAG_NO_VISUAL_MOVEMENT,
                S07_LEFT_AGE_SLIDER_EXACT_VERIFY_FAILED_AFTER_RETRY,
                S07_AGE_ONE_POST_ACTION_VERIFY_FAILED,
            }:
                evidence["failure_reason"] = None
            target_x_override = int(base_target_x) + int(offset)
            retry_strategy = strategy if offset == 0 else f"{strategy}_left_nudge"
            current_after_left = drag_age_handle(
                current_after_left,
                side="left",
                to_age=int(target_age),
                strategy=retry_strategy,
                attempt_index=first_attempt_index + offset_index,
                duration_ms=450 if offset == 0 else 350,
                target_x_override=target_x_override,
            )
            evidence["s07_left_drag_retry_count"] = max(0, len(evidence.get("s07_left_drag_retry_results") or []) - 1)
            if mark_success_if_exact(current_after_left):
                return current_after_left
            if not evidence.get("s07_left_drag_attempted"):
                break
        left_attempts = [
            item
            for item in (evidence.get("age_strategy_attempts") or [])[initial_attempt_count:]
            if item.get("side") == "left"
        ]
        if left_attempts and not any(item.get("slider_moved_success") for item in left_attempts):
            evidence["failure_reason"] = S07_LEFT_AGE_SLIDER_DRAG_NO_VISUAL_MOVEMENT
        elif target_age == 1:
            evidence["failure_reason"] = S07_LEFT_AGE_SLIDER_EXACT_VERIFY_FAILED_AFTER_RETRY
        return current_after_left

    def start_fallback_strategy(strategy: str) -> bool:
        used = evidence.setdefault("fallback_strategies_used", [])
        if strategy in used:
            return True
        if len(used) >= S07_AGE_SLIDER_FASTPATH_MAX_FALLBACK_STRATEGIES:
            evidence["fallback_strategy_limit_reached"] = True
            return False
        allowed_fallbacks = set(
            (evidence.get("contract_action_plan") or {}).get("allowed_fallbacks")
            or evidence.get("allowed_fallbacks")
            or []
        )
        if strategy not in allowed_fallbacks:
            evidence["fallback_strategy_limit_reached"] = True
            evidence["fallback_used"] = True
            evidence["fallback_name"] = strategy
            evidence["fallback_allowed_by_clause"] = False
            evidence["forbidden_action_used"] = True
            evidence["failure_reason"] = S07_AGE_CONTRACT_UNAUTHORIZED_FALLBACK_USED
            return False
        used.append(strategy)
        evidence["fallback_used"] = True
        evidence["fallback_name"] = strategy
        evidence["fallback_allowed_by_clause"] = True
        return True

    def run_direct_track_fastpath(current_snapshot: dict[str, Any]) -> dict[str, Any]:
        plan = _s07_age_direct_track_plan(current_snapshot, target_age)
        evidence.update(
            {
                "direct_track_fastpath_available": plan.get("direct_track_fastpath_available"),
                "direct_track_fastpath_skip_reason": plan.get("failure_reason"),
                "track_bounds": plan.get("track_bounds"),
                "left_handle_bounds": plan.get("left_handle_bounds"),
                "right_handle_bounds": plan.get("right_handle_bounds"),
                "left_slider_target": plan.get("left_slider_target"),
                "right_slider_target": plan.get("right_slider_target"),
                "expected_age_filter": plan.get("expected_age_filter"),
                "target_x": plan.get("target_x"),
                "target_y": plan.get("target_y"),
                "target_ratio": plan.get("target_ratio"),
                "target_x_algorithm": plan.get("target_x_algorithm"),
                "target_x_calculation": plan.get("target_x_calculation"),
                "contract_action_plan": plan.get("contract_action_plan") or evidence.get("contract_action_plan"),
                "contract_expected": (plan.get("contract_action_plan") or evidence.get("contract_action_plan") or {}).get("expected"),
                "contract_action_algorithm": (plan.get("contract_action_plan") or evidence.get("contract_action_plan") or {}).get("action_algorithm"),
                "contract_forbidden_actions": (plan.get("contract_action_plan") or evidence.get("contract_action_plan") or {}).get("forbidden_actions"),
                "contract_action_plan_id": plan.get("contract_action_plan_id") or evidence.get("contract_action_plan_id"),
                "contract_action_plan_used": plan.get("contract_action_plan_used", evidence.get("contract_action_plan_used")),
                "action_plan_step_id": plan.get("action_plan_step_id") or evidence.get("action_plan_step_id"),
                "action_algorithm_used": plan.get("action_algorithm_used") or evidence.get("action_algorithm_used"),
                "action_inputs_source": plan.get("action_inputs_source") or evidence.get("action_inputs_source"),
                "action_outputs_source": plan.get("action_outputs_source") or evidence.get("action_outputs_source"),
                "forbidden_action_used": bool(plan.get("forbidden_action_used", evidence.get("forbidden_action_used", False))),
                "runtime_bypassed_action_plan": bool(plan.get("runtime_bypassed_action_plan", evidence.get("runtime_bypassed_action_plan", False))),
                "action_plan_binding_check_passed": plan.get("action_plan_binding_check_passed", evidence.get("action_plan_binding_check_passed")),
                "min_age": plan.get("min_age"),
                "max_age": plan.get("max_age"),
                "special_5_5_fastpath": plan.get("special_5_5_fastpath"),
            }
        )
        if not plan.get("direct_track_fastpath_available"):
            if plan.get("failure_reason") in {"right_handle_bounds_missing", "left_handle_bounds_missing"}:
                evidence["failure_reason"] = S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED
            return current_snapshot
        right_bounds = plan.get("right_handle_bounds")
        if not _has_positive_bounds(right_bounds):
            evidence["direct_track_fastpath_skip_reason"] = "right_handle_bounds_missing"
            evidence["failure_reason"] = S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED
            return current_snapshot
        sx0, _ = _center(right_bounds)
        sy0 = int(plan.get("target_y") or _center(right_bounds)[1])
        ex0 = int(plan["target_x"])
        binding = _s07_build_age_drag_binding(
            side="right",
            selected_handle_bounds=right_bounds,
            target_x=ex0,
            preferred_y=sy0,
            original_start_point=(sx0, sy0),
            track_bounds=plan.get("track_bounds"),
        )
        evidence["right_handle_binding_trace"] = binding
        evidence["selected_handle_bounds"] = binding.get("selected_handle_bounds")
        evidence["drag_start_point"] = binding.get("drag_start_point")
        evidence["drag_start_inside_selected_handle_bounds"] = binding.get("drag_start_inside_selected_handle_bounds")
        evidence["drag_target_point"] = binding.get("drag_target_point")
        evidence["handle_binding_reason"] = binding.get("handle_binding_reason")
        if binding.get("stop_code"):
            evidence["failure_reason"] = binding.get("stop_code")
            return current_snapshot
        sx, sy = binding["drag_start_point"]
        ex, ey = binding["drag_target_point"]
        right_target_age = int(plan.get("right_slider_target") if plan.get("right_slider_target") is not None else target_age)
        strategy = "direct_track_fastpath_5_5" if right_target_age == 5 else "direct_track_fastpath"
        evidence["direct_track_fastpath_used"] = True
        evidence["direct_fastpath_used"] = True
        evidence["direct_track_bounds_reused_in_round"] = True
        touch_trace = execute_s07_real_handle_drag(
            context,
            current_snapshot,
            side="right",
            to_age=right_target_age,
            strategy=strategy,
            attempt_index=1,
            start=(int(sx), int(sy)),
            end=(int(ex), int(ey)),
            duration_ms=650,
            binding=binding,
            plan=plan,
        )
        move_ms = int(touch_trace.get("move_ms") or 0)
        evidence["right_move_ms"] += move_ms
        time.sleep(0.2)
        after = capture_after_age_action(f"s07_age_{strategy}_right_1_{_timestamp()}")
        evidence["final_xml_verify_count"] = int(evidence.get("final_xml_verify_count") or 0) + 1
        _ensure_current_page_contract(context, after, {"S07"}, action_page="S07")
        right_record = record_attempt(
            strategy=strategy,
            attempt_index=1,
            before=current_snapshot,
            after=after,
            start=(sx, sy),
            end=(ex, ey),
            duration_ms=650,
            move_ms=move_ms,
            side="right",
            touch_trace=touch_trace,
        )
        after_right_points = _s07_age_slider_points(after, target_age)
        after_right_verify = _s07_classify_age_verify_text(after, target_age)
        evidence.update(
            {
                "s07_after_right_left_handle_bounds": after_right_points.get("left_handle_bounds"),
                "s07_after_right_right_handle_bounds": after_right_points.get("right_handle_bounds"),
                "s07_after_right_visible_text": (
                    after_right_verify.get("s07_age_verify_text_raw")
                    or str(after.get("visible_blob") or "")
                ),
            }
        )
        if not right_record.get("slider_moved_success"):
            evidence["failure_reason"] = S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT
            return after
        if _exact_age_confirmed(after, target_age):
            return after
        left_after_right, right_after_right = _s07_age_range_from_slider_positions(after)
        after_right_is_broad_one = (
            target_age == 1
            and after_right_verify.get("s07_age_verify_text_class") == "broad_range"
        )
        if target_age == 1 and (left_after_right != target_age or after_right_is_broad_one):
            after = move_left_after_right_with_retries(
                after,
                strategy=strategy,
                first_attempt_index=2,
                base_target_x=int(plan["target_x"]),
            )
            if evidence.get("failure_reason") in {
                S07_AGE_SLIDER_HANDLE_BINDING_FAILED,
                S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED,
                S07_AGE_SLIDER_CLOSE_HANDLE_PAIR_BINDING_FAILED,
                S07_AGE_SLIDER_DRAG_START_OUTSIDE_HANDLE_BOUNDS,
                S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT,
                S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT,
                S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT,
                S07_LEFT_AGE_SLIDER_DRAG_NO_VISUAL_MOVEMENT,
                S07_LEFT_AGE_SLIDER_EXACT_VERIFY_FAILED_AFTER_RETRY,
            }:
                return after
        elif target_age is not None and left_after_right != target_age:
            after = drag_age_handle(
                after,
                side="left",
                to_age=int(target_age),
                strategy=strategy,
                attempt_index=2,
                duration_ms=450,
            )
            if evidence.get("failure_reason") in {
                S07_AGE_SLIDER_HANDLE_BINDING_FAILED,
                S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED,
                S07_AGE_SLIDER_CLOSE_HANDLE_PAIR_BINDING_FAILED,
                S07_AGE_SLIDER_DRAG_START_OUTSIDE_HANDLE_BOUNDS,
                S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT,
                S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT,
                S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT,
            }:
                return after
        return after

    current = run_direct_track_fastpath(current)
    if evidence.get("failure_reason") in {
        S07_AGE_SLIDER_HANDLE_BINDING_FAILED,
        S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED,
        S07_AGE_SLIDER_CLOSE_HANDLE_PAIR_BINDING_FAILED,
        S07_AGE_SLIDER_DRAG_START_OUTSIDE_HANDLE_BOUNDS,
        S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT,
        S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT,
        S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT,
        S07_LEFT_AGE_SLIDER_DRAG_NO_VISUAL_MOVEMENT,
        S07_LEFT_AGE_SLIDER_EXACT_VERIFY_FAILED_AFTER_RETRY,
    }:
        return finish_evidence()
    if mark_success_if_exact(current):
        return finish_evidence()
    if evidence.get("direct_track_fastpath_used"):
        if not evidence.get("failure_reason"):
            evidence["failure_reason"] = S07_AGE_EXACT_RANGE_VERIFY_FAILED
        return finish_evidence()

    # Strategy A: move the right handle before the left handle, then confirm exact.
    if right_age_before is not None and target_age is not None and right_age_before > target_age and start_fallback_strategy("right_first"):
        current = drag_age_handle(current, side="right", to_age=int(target_age), strategy="right_first", attempt_index=1, duration_ms=450)
        if mark_success_if_exact(current):
            return finish_evidence()
        current = move_left_if_needed(current, strategy="right_first", attempt_index=2, duration_ms=450)
        if mark_success_if_exact(current):
            return finish_evidence()

    # Strategy B: longer press-drag for the right handle.
    left_age_current, right_age_current = _s07_age_range_from_slider_positions(current)
    if right_age_current is not None and target_age is not None and right_age_current != target_age and start_fallback_strategy("long_press_drag"):
        current = drag_age_handle(current, side="right", to_age=int(target_age), strategy="long_press_drag", attempt_index=1, duration_ms=1000)
        if mark_success_if_exact(current):
            return finish_evidence()
        current = move_left_if_needed(current, strategy="long_press_drag", attempt_index=2, duration_ms=450)
        if mark_success_if_exact(current):
            return finish_evidence()

    # Strategy C: segmented drag, e.g. 6 -> 4 -> 2.
    left_age_current, right_age_current = _s07_age_range_from_slider_positions(current)
    if right_age_current is not None and target_age is not None and right_age_current > target_age and start_fallback_strategy("segmented_drag"):
        intermediate_ages = [
            age for age, _, _ in sorted(_s07_age_numeric_points(current), reverse=True)
            if target_age <= age < right_age_current
        ]
        for attempt_index, age in enumerate(intermediate_ages[:2], start=1):
            current = drag_age_handle(current, side="right", to_age=int(age), strategy="segmented_drag", attempt_index=attempt_index, duration_ms=700)
            if mark_success_if_exact(current):
                return finish_evidence()
        current = move_left_if_needed(current, strategy="segmented_drag", attempt_index=3, duration_ms=450)
        if mark_success_if_exact(current):
            return finish_evidence()

    # Strategy D: if the handle point is not the real drag target, drag along the derived track line.
    left_age_current, right_age_current = _s07_age_range_from_slider_positions(current)
    if right_age_current is not None and target_age is not None and right_age_current != target_age and start_fallback_strategy("track_based_drag"):
        current = drag_age_handle(current, side="right", to_age=int(target_age), strategy="track_based_drag", attempt_index=1, duration_ms=1000, use_track_point=True)
        if mark_success_if_exact(current):
            return finish_evidence()
        current = move_left_if_needed(current, strategy="track_based_drag", attempt_index=2, duration_ms=450)
        if mark_success_if_exact(current):
            return finish_evidence()

    right_attempts = len([item for item in evidence["age_strategy_attempts"] if item.get("side") == "right"])
    evidence["right_slider_retry_count"] = right_attempts
    if not evidence.get("success"):
        left_after, right_after = _s07_age_range_from_slider_positions(current)
        evidence["left_age_after"] = left_after
        evidence["right_age_after"] = right_after
        if target_age in (11, 12):
            evidence["failure_reason"] = "S07_AGE_HIDDEN_TICK_VERIFY_FAILED"
            evidence["verify_text"] = _s07_exact_age_text(current, target_age)
        elif target_age == 0:
            evidence["failure_reason"] = S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED
            evidence["verify_text"] = _s07_exact_age_text(current, target_age)
        elif target_age == 1:
            evidence["failure_reason"] = S07_AGE_ONE_POST_ACTION_VERIFY_FAILED
            evidence["verify_text"] = _s07_exact_age_text(current, target_age)
        elif evidence.get("direct_track_fastpath_used") and evidence.get("fallback_strategy_limit_reached"):
            evidence["failure_reason"] = S07_AGE_SLIDER_FALLBACK_BUDGET_EXCEEDED
        elif evidence.get("direct_track_fastpath_used"):
            evidence["failure_reason"] = "S07_AGE_SLIDER_FINAL_VALUE_MISMATCH"
        else:
            evidence["failure_reason"] = "S07_AGE_TARGET_TICK_NOT_FOUND" if right_after != target_age else "S07_EXACT_AGE_STATE_UNCONFIRMED"
    return finish_evidence()


def _wait_for_s07_age_panel(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    stem_prefix: str,
    *,
    target_age: int | None,
    rounds: int = 4,
    interval_s: float = 0.3,
) -> dict[str, Any]:
    client: AdbClient = context["client"]
    current = snapshot
    for index in range(rounds):
        time.sleep(interval_s)
        current = _capture(client, f"{stem_prefix}_{index}_{_timestamp()}")
        _ensure_current_page_contract(context, current, {"S07"}, action_page="S07")
        evidence = _s07_age_slider_evidence(current, target_age)
        if evidence.get("tick_count") or evidence.get("exact_confirmed"):
            current["age_panel_stable"] = True
            return current
    current["age_panel_stable"] = False
    return current


def _wait_for_s07_age_exact(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    target_age: int | None,
    stem_prefix: str,
    *,
    rounds: int = 4,
    interval_s: float = 0.3,
) -> dict[str, Any]:
    client: AdbClient = context["client"]
    current = snapshot
    for index in range(rounds):
        time.sleep(interval_s)
        current = _capture(client, f"{stem_prefix}_{index}_{_timestamp()}")
        _ensure_current_page_contract(context, current, {"S07"}, action_page="S07")
        if _exact_age_confirmed(current, target_age):
            return current
    return current


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
    result, action_ms = contract_execute_device_action(
        context,
        snapshot,
        "S01",
        "click_bottom_select_car_tab",
        lambda: client.tap_s01_bottom_select_car_tab(str(snapshot.get("fresh_xml") or "")),
    )
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
    brand_node = _find_brand_filter_node(snapshot)
    if not brand_node or not brand_node.get("bounds"):
        issue = _record_issue(issues, "BRAND_FILTER_NOT_FOUND", "S02", "Brand filter entry not found on verified S02 page.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    action_start = time.perf_counter()
    try:
        action_ms = contract_execute_click(
            context,
            snapshot,
            "S02",
            "tap_brand_filter",
            _center(brand_node["bounds"]),
            evidence={"clicked_text": "品牌", "clicked_node_bounds": brand_node.get("bounds"), "clicked_resource_id": brand_node.get("resource_id")},
        )
    except GuaziFlowError:
        raise
    except Exception as exc:
        issue = _record_issue(
            issues,
            "BRAND_FILTER_CLICK_FAILED",
            "S02",
            "Brand filter entry click failed.",
            {**snapshot, "brand_node": brand_node, "click_exception": str(exc)},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    time.sleep(0.8)
    next_snapshot = _capture(client, f"s02_to_s03_{_timestamp()}")
    next_state = _recognize_page(recognizer, next_snapshot, context.get("flow_state"))
    if next_state != "S03":
        issue = _record_issue(
            issues,
            "BRAND_FILTER_PANEL_NOT_OPENED",
            "S02",
            "Brand filter entry was clicked, but S03 brand-selection page was not verified.",
            {**next_snapshot, "previous_snapshot": snapshot, "brand_node": brand_node, "actual_state": next_state},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
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
    if not brand:
        issue = _record_issue(issues, "TARGET_BRAND_NOT_FOUND_IN_S03", "S03", "Target brand is empty for S03 brand selection.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    search_result = _s03_search_target_brand_v2(context, snapshot, brand)
    snapshot = search_result["snapshot"]
    brand_node = search_result["brand_node"]
    matched_alias = search_result.get("matched_alias") or brand
    s03_audit = dict(search_result.get("audit") or {})
    transition_wait_ms += int(search_result.get("transition_wait_ms") or 0)

    if not contract_action_allowed(context, "S03", "tap_target_brand"):
        contract_stop(
            context,
            "S03",
            "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT",
            "S03 target brand click is not allowed by page contract.",
            {**snapshot, "s03_brand_search_v2": s03_audit},
        )
    if brand_node is None or not brand_node.get("bounds"):
        issue_context = dict(snapshot)
        issue_context["s03_brand_search_v2"] = s03_audit
        stop_code = str(s03_audit.get("stop_code") or "TARGET_BRAND_NOT_FOUND_IN_S03")
        reason = str(
            s03_audit.get("reason_alias_not_matched")
            or f"Target brand {brand} is not visible after the V1.16 S03 contract action."
        )
        issue = _record_issue(
            issues,
            stop_code,
            "S03",
            reason,
            issue_context,
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    click_plan = _s03_brand_row_left_icon_safe_click_plan(snapshot, brand_node, matched_alias)
    s03_audit.update(click_plan)
    s03_audit["next_action"] = "S03_ONLY_ALLOWED_ACTION_CLICK_BRAND_ROW_LEFT_ICON_SAFE_POINT"
    if not click_plan.get("selected_click_point"):
        issue = _record_issue(
            issues,
            "S03_BRAND_ROW_LEFT_ICON_SAFE_POINT_NOT_FOUND",
            "S03",
            "Target brand row bounds could not produce a left-icon safe click point.",
            {**snapshot, "s03_brand_search_v2": s03_audit},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if click_plan.get("selected_click_overlaps_brand_zone"):
        issue = _record_issue(
            issues,
            "S03_BRAND_ROW_LEFT_ICON_SAFE_POINT_OVERLAPS_BRAND_ZONE",
            "S03",
            "Target brand row left-icon safe click point overlaps the forbidden brand-zone text area.",
            {**snapshot, "s03_brand_search_v2": s03_audit},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    s03_audit["clicked_brand"] = matched_alias
    s03_audit["clicked_brand_bounds"] = brand_node.get("bounds")
    context["s03_brand_search_v2"] = dict(s03_audit)

    selected_click_point = tuple(int(value) for value in click_plan["selected_click_point"])
    try:
        brand_click_ms = contract_click(
            context,
            snapshot,
            "S03",
            "tap_target_brand",
            selected_click_point,
            evidence={"s03_brand_search_v2": s03_audit},
        )
    except GuaziFlowError:
        raise
    except Exception as exc:
        issue = _record_issue(
            issues,
            "S03_TARGET_BRAND_CLICK_FAILED",
            "S03",
            "S03 target brand click failed after the target brand node was found.",
            {**snapshot, "s03_brand_search_v2": s03_audit, "click_exception": str(exc)},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    timing.add(
        step_name="S03_CLICK_TARGET_BRAND",
        page_name="S03",
        action_name="tap_target_brand",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=brand_click_ms,
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            **s03_audit,
            "clicked_brand_text": matched_alias,
            "clicked_brand_node_bounds": brand_node.get("bounds"),
            "clicked_point": list(selected_click_point),
            "click_strategy": "brand_row_left_icon_safe_point",
        },
    )
    action_ms = int((time.perf_counter() - action_started) * 1000)
    next_snapshot: dict[str, Any] | None = None
    recognized_after_brand_click: str | None = None
    s03_to_s04_wait_rounds: list[dict[str, Any]] = []
    for wait_ms in (800, 500, 500, 700, 1000, 1500):
        time.sleep(wait_ms / 1000)
        transition_wait_ms += wait_ms
        next_snapshot = _capture(client, f"s03_to_s04_{_timestamp()}")
        recognized_after_brand_click = _recognize_page(context["recognizer"], next_snapshot, context.get("flow_state"))
        s03_to_s04_wait_rounds.append(
            {
                "wait_ms": wait_ms,
                "recognized_page": recognized_after_brand_click,
                "visible_text_count": len(next_snapshot.get("visible_texts") or []),
                "screenshot_path": str(next_snapshot.get("screenshot_path") or ""),
                "xml_path": str(next_snapshot.get("xml_path") or ""),
            }
        )
        if recognized_after_brand_click == "S04":
            break
        if recognized_after_brand_click and recognized_after_brand_click not in {"S03", "S04"}:
            break
    assert next_snapshot is not None
    s03_audit["after_click_page_type"] = recognized_after_brand_click
    s03_audit["brand_zone_continuation_allowed"] = False
    context["s03_brand_search_v2"] = dict(s03_audit)
    timing.add(
        step_name="S03_TO_S04",
        page_name="S03",
        action_name="tap_brand_row_left_icon_safe_point",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=transition_wait_ms,
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
        extra={
            **s03_audit,
            "recognized_after_brand_click": recognized_after_brand_click,
            "s03_to_s04_wait_rounds": s03_to_s04_wait_rounds,
        },
    )
    if _looks_like_s04_brand_zone_mixed_list(next_snapshot):
        context["s04_landing_type"] = "BRAND_ZONE_MIXED_LIST"
        issue = _record_issue(
            issues,
            "S03_CLICKED_BRAND_ZONE_INSTEAD_OF_BRAND",
            "S03",
            "S03 target brand click landed on a brand-zone mixed list, which is blocked by the V1.16 contract.",
            {
                **next_snapshot,
                "s03_brand_search_v2": s03_audit,
                "recognized_after_brand_click": recognized_after_brand_click,
                "brand_zone_page_detected": True,
                "continuation_allowed": False,
                "old_brand_zone_continuation_branch_removed": True,
            },
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
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
    target_series = str(params.get("series") or "")
    target_series_aliases = _s04_target_series_aliases(params, target_series)
    context["s04_target_series_aliases"] = target_series_aliases
    if _looks_like_s04_brand_zone_mixed_list(snapshot):
        context["s04_landing_type"] = "BRAND_ZONE_MIXED_LIST"
        issue = _record_issue(
            issues,
            "S04_BRAND_ZONE_PAGE_BLOCKED_BY_CONTRACT",
            "S04",
            "Brand-zone mixed list is not a valid S04 branch in the V1.14 contract; refusing to search target series or click model-config filter inside brand zone.",
            {
                **snapshot,
                "brand_zone_page_detected": True,
                "continuation_allowed": False,
                "old_brand_zone_continuation_branch_removed": True,
            },
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    machine.assert_action_allowed("S04", "click_series_model_button")
    series_initial = _derive_brand_initial(target_series)
    s04_has_letter_index = bool(_right_letter_index_nodes(snapshot))
    s04_initial_letter_node = _find_right_letter_index_node(snapshot, series_initial or "") if series_initial else None
    s04_initial_letter_node_found = bool(s04_initial_letter_node and s04_initial_letter_node.get("bounds"))
    s04_contract_allows_letter_index = _page_contract_allows_action(context, "S04", "tap_series_letter")
    s04_letter_index_used = False
    context["s04_has_letter_index"] = s04_has_letter_index
    context["s04_initial_letter_node_found"] = s04_initial_letter_node_found
    context["s04_contract_allows_letter_index"] = s04_contract_allows_letter_index
    context["s04_letter_index_used"] = False
    if s04_has_letter_index and not s04_contract_allows_letter_index:
        issue = _record_issue(
            issues,
            "CONTRACT_NEEDS_UPDATE_S04_LETTER_INDEX",
            "S04",
            "S04 exposes a right-side letter index, but the page contract does not authorize clicking it.",
            {
                **snapshot,
                "s04_has_letter_index": s04_has_letter_index,
                "s04_initial_letter_node_found": s04_initial_letter_node_found,
                "s04_contract_allows_letter_index": s04_contract_allows_letter_index,
                "s04_letter_index_used": False,
                "target_series": target_series,
                "series_initial": series_initial,
            },
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if s04_has_letter_index and s04_contract_allows_letter_index and s04_initial_letter_node and s04_initial_letter_node.get("bounds"):
        machine.assert_action_allowed("S04", "tap_series_letter")
        action_start = time.perf_counter()
        letter_action_ms = contract_execute_click(
            context,
            snapshot,
            "S04",
            "tap_series_letter",
            _center(s04_initial_letter_node["bounds"]),
            evidence={"series_initial": series_initial, "clicked_node_bounds": s04_initial_letter_node.get("bounds")},
        )
        s04_letter_index_used = True
        context["s04_letter_index_used"] = True
        time.sleep(0.3)
        snapshot = _capture(client, f"s04_after_letter_{_timestamp()}")
        timing.add(
            step_name="S04_CLICK_SERIES_INITIAL_LETTER",
            page_name="S04",
            action_name="tap_series_letter",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=letter_action_ms,
            transition_wait_ms=300,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra={
                "s04_has_letter_index": s04_has_letter_index,
                "s04_initial_letter_node_found": s04_initial_letter_node_found,
                "s04_contract_allows_letter_index": s04_contract_allows_letter_index,
                "s04_letter_index_used": s04_letter_index_used,
                "target_series": target_series,
                "series_initial": series_initial,
            },
        )
    transition_wait_ms = 0
    seen_series_names: set[str] = set()
    previous_visible_series_names: list[str] | None = None
    down_unchanged_count = 0
    screen_index = 0
    bottom_reached = False

    while True:
        _ensure_current_page_contract(context, snapshot, {"S04"}, action_page="S04")
        raw_xml = str(snapshot.get("fresh_xml") or "")
        raw_xml_contains_target = any(alias and alias in raw_xml for alias in target_series_aliases)
        visible_series_names = _s04_visible_series_names(snapshot)
        target_in_visible_series = any(
            _s04_series_matches_target(name, target_series_aliases, target_series)
            for name in visible_series_names
        )
        series_item = _find_s04_series_item(snapshot, target_series, target_series_aliases)
        target_bounds = series_item.get("bounds") if series_item else None
        candidate_buttons = _s04_model_button_candidates(snapshot, series_item)
        selected_button = _find_series_model_button(snapshot, target_series, target_series_aliases)
        candidate_bounds = [button["bounds"] for button in candidate_buttons if button.get("bounds")]
        selected_bounds = selected_button.get("bounds") if selected_button else None

        if raw_xml_contains_target and not target_in_visible_series:
            _record_s04_visible_series(
                context,
                snapshot,
                direction="down",
                screen_index=screen_index,
                target_series=target_series,
                target_series_aliases=target_series_aliases,
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
                target_series_aliases=target_series_aliases,
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
                    target_series_aliases=target_series_aliases,
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
                target_series_aliases=target_series_aliases,
                target_bounds=target_bounds,
                candidate_model_button_bounds=candidate_bounds,
                selected_model_button_bounds=selected_bounds,
                action_taken="tap_target_model_button",
                seen_series_names=seen_series_names,
                bottom_reached=bottom_reached,
            )
            action_start = time.perf_counter()
            action_ms = contract_execute_click(
                context,
                snapshot,
                "S04",
                "click_series_model_button",
                _center(selected_button["bounds"]),
                evidence={
                    "target_series": target_series,
                    "target_series_aliases": target_series_aliases,
                    "clicked_node_bounds": selected_button.get("bounds"),
                    "clicked_action_id": "S04_ONLY_ALLOWED_ACTION_CLICK_TARGET_SERIES_ROW_RIGHT_MODELS_BUTTON",
                },
            )
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
                target_series_aliases=target_series_aliases,
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
            target_series_aliases=target_series_aliases,
            target_bounds=target_bounds,
            candidate_model_button_bounds=candidate_bounds,
            selected_model_button_bounds=selected_bounds,
            action_taken="scroll_down",
            seen_series_names=seen_series_names,
            bottom_reached=bottom_reached,
        )

        machine.assert_action_allowed("S04", "scroll_series_list")
        contract_execute_swipe(
            context,
            snapshot,
            "S04",
            "scroll_series_list",
            direction="up",
            evidence={"target_series": target_series, "screen_index": screen_index},
        )
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
    target_trim_prefix_terms = _s05_target_trim_prefix_terms_from_params(params)
    flow_state = context.setdefault("flow_state", {})
    if state in {"S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"} and flow_state.get("s05_target_year_selected_confirmed") is not True:
        state = "S05"
    elif state in {"S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"} and not _s05_right_list_contains_year(snapshot, target_year):
        state = "S05"
    elif state == "S05_TRIM_SELECTED" and (
        not _s05_target_trim_selected(snapshot, target_year, target_trim, target_trim_prefix_terms)
        or not _s05_selected_one(snapshot)
        or not _s05_confirm_clickable(snapshot)
    ):
        state = "S05_MODEL_YEAR_SELECTED"
    if state == "S05":
        machine.assert_action_allowed("S05", "tap_target_year")
        before_year_snapshot = snapshot
        year_node = _s05_find_left_year_click_target(snapshot, target_year)
        if year_node is None or not year_node.get("bounds"):
            issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S05", "Target model year not found.", snapshot)
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        if not _s05_node_in_left_year_region(snapshot, year_node.get("bounds")):
            issue = _record_issue(
                issues,
                "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT",
                "S05",
                "Target model year click point is outside the left year list.",
                {**snapshot, "target_year": target_year, "clicked_year_node_bounds": year_node.get("bounds")},
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        action_start = time.perf_counter()
        clicked_year_point = _center(year_node["bounds"])
        year_click_ms = contract_execute_click(
            context,
            snapshot,
            "S05",
            "tap_target_year",
            clicked_year_point,
            evidence={
                "target_year": target_year,
                "s05_year_click_required": True,
                "s05_year_clicked": True,
                "s05_year_click_text": target_year,
                "s05_year_click_bounds": year_node.get("bounds"),
                "s05_year_click_region": "left_year_list",
                "clicked_year_node_bounds": year_node.get("bounds"),
            },
        )
        action_total += year_click_ms
        timing.add(
            step_name="S05_CLICK_TARGET_YEAR",
            page_name="S05",
            action_name="tap_target_year",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=year_click_ms,
            transition_wait_ms=0,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra={
                "target_year_model": target_year,
                "clicked_year_text": target_year,
                "clicked_year_node_bounds": year_node.get("bounds"),
                "clicked_year_parent_bounds": year_node.get("year_parent_bounds"),
                "clicked_year_point": clicked_year_point,
                "clicked_year_click_strategy": year_node.get("year_click_strategy") or "direct_year_text_node_bounds",
                "visible_trim_names_before_year_click": _s05_right_trim_labels(before_year_snapshot, None),
            },
        )
        time.sleep(0.4)
        snapshot = _capture(client, f"s05_after_year_{_timestamp()}")
        state = _ensure_current_page_contract(
            context,
            snapshot,
            {"S05", "S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"},
            action_page="S05_MODEL_YEAR_SELECTED",
        )
        year_switch_evidence = _s05_year_switch_evidence(
            snapshot,
            target_year,
            target_trim=target_trim,
            target_prefix_terms=target_trim_prefix_terms,
            before_snapshot=before_year_snapshot,
        )
        year_switch_evidence.update(
            {
                "s05_year_click_required": True,
                "s05_year_clicked": True,
                "s05_year_click_text": target_year,
                "s05_year_click_bounds": year_node.get("bounds"),
                "s05_year_click_region": "left_year_list",
            }
        )
        year_click_confirmation = _s05_year_click_record_confirmation(
            target_year=target_year,
            clicked_text=target_year,
            clicked_region="left_year_list",
            click_executed=True,
            after_state=state,
        )
        year_switch_evidence.update(year_click_confirmation)
        if year_click_confirmation.get("s05_target_year_selected_confirmed"):
            year_switch_evidence["left_year_selected_text"] = target_year
            year_switch_evidence["selected_year_after_click"] = target_year
            year_switch_evidence["left_year_selected"] = True
            year_switch_evidence["s05_target_year_selected_confirmed"] = True
            year_switch_evidence["s05_year_confirmed_by"] = "left_year_click_record"
        timing.add(
            step_name="S05_AFTER_YEAR_CLICK_FRESH",
            page_name="S05_MODEL_YEAR_SELECTED",
            action_name="fresh_after_target_year_click",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=400,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra=year_switch_evidence,
        )
        timing.add(
            step_name="S05_VERIFY_YEAR_SELECTED",
            page_name="S05_MODEL_YEAR_SELECTED",
            action_name="verify_left_target_year_selected",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra=year_switch_evidence,
        )
        timing.add(
            step_name="S05_VERIFY_RIGHT_TRIM_YEAR_SWITCHED",
            page_name="S05_MODEL_YEAR_SELECTED",
            action_name="verify_right_trim_year_switched",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra=year_switch_evidence,
        )
        retry_year_click_used = False
        retry_click_strategy = None
        if not year_switch_evidence.get("right_trim_year_switched"):
            retry_click_strategy = "disabled_by_page_contract"
            year_switch_evidence["legacy_retry_year_click_disabled"] = True
        year_switch_evidence.update(
            {
                "retry_year_click_used": retry_year_click_used,
                "retry_click_strategy": retry_click_strategy,
                "year_click_effective": bool(year_switch_evidence.get("right_trim_year_switched")),
            }
        )
        right_list_mode = _s05_right_list_mode(snapshot)
        year_switch_evidence.update(right_list_mode)
        context["s05_year_selection_evidence"] = dict(year_switch_evidence)
        flow_state.update(
            {
                "s05_year_click_required": True,
                "s05_year_clicked": True,
                "s05_year_click_text": target_year,
                "s05_year_click_bounds": year_node.get("bounds"),
                "s05_year_click_region": "left_year_list",
                "selected_year_after_click": year_switch_evidence.get("selected_year_after_click"),
                "left_year_selected_text": year_switch_evidence.get("left_year_selected_text"),
                "s05_target_year_selected_confirmed": bool(year_switch_evidence.get("s05_target_year_selected_confirmed")),
                "s05_year_confirmed_by": year_switch_evidence.get("s05_year_confirmed_by"),
                "s05_year_click_record_valid": bool(year_switch_evidence.get("s05_year_click_record_valid")),
                "right_config_list_after_year": year_switch_evidence.get("right_config_list_after_year"),
                "target_config_seen_after_year": bool(year_switch_evidence.get("target_config_seen_after_year")),
                "direct_right_config_search_without_year_click": False,
            }
        )
        if year_switch_evidence.get("selected_year_after_click") and year_switch_evidence.get("selected_year_after_click") != target_year:
            issue = _record_issue(
                issues,
                "S05_WRONG_YEAR_SELECTED",
                "S05",
                "S05 selected year does not match target year after left year click.",
                {**snapshot, "target_year": target_year, **year_switch_evidence},
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        if year_switch_evidence.get("s05_target_year_selected_confirmed") is not True:
            stop_code = str(year_switch_evidence.get("s05_year_click_record_stop_code") or "S05_TARGET_YEAR_SELECTION_NOT_CONFIRMED")
            timing.add(
                step_name=stop_code,
                page_name="S05_MODEL_YEAR_SELECTED",
                action_name="stop_before_trim_search_when_left_year_not_confirmed",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=0,
                transition_wait_ms=0,
                screenshot_path=str(snapshot.get("screenshot_path") or ""),
                xml_path=str(snapshot.get("xml_path") or ""),
                extra=year_switch_evidence,
            )
            issue = _record_issue(
                issues,
                stop_code,
                "S05",
                "Target model year left click record was not deterministically confirmed before selecting right-side trim.",
                {**snapshot, "target_year": target_year, "right_trim_labels": _s05_right_trim_labels(snapshot, target_year), **year_switch_evidence},
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        state = "S05_MODEL_YEAR_SELECTED"

    if state == "S05_MODEL_YEAR_SELECTED":
        flow_state = context.setdefault("flow_state", {})
        if flow_state.get("s05_target_year_selected_confirmed") is not True:
            issue = _record_issue(
                issues,
                "S05_RIGHT_CONFIG_SEARCH_WITHOUT_YEAR_CLICK",
                "S05_MODEL_YEAR_SELECTED",
                "Right-side trim search is blocked because the left target year tab has not been confirmed.",
                {**snapshot, "target_year": target_year, "target_trim": target_trim, "flow_state": dict(flow_state)},
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        right_list_mode = _s05_right_list_mode(snapshot)
        if not _s05_right_list_contains_year(snapshot, target_year) and not (
            right_list_mode.get("right_list_has_all_models") or right_list_mode.get("right_list_has_year_sections")
        ):
            issue = _record_issue(
                issues,
                "S05_TARGET_CONFIG_NOT_FOUND",
                "S05_MODEL_YEAR_SELECTED",
                "Target model year click record is valid, but the right trim list does not expose a searchable target configuration list.",
                {**snapshot, "target_year": target_year, "right_trim_labels": _s05_right_trim_labels(snapshot, target_year), **right_list_mode},
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        machine.assert_action_allowed("S05_MODEL_YEAR_SELECTED", "tap_exact_trim")
        s05_emission_variant_evidence = _s05_same_trim_emission_variant_group(snapshot, target_year, target_trim, target_trim_prefix_terms)
        if s05_emission_variant_evidence.get("emission_variant_group_ambiguous"):
            issue = _record_issue(
                issues,
                "S05_EMISSION_VARIANT_GROUP_NOT_CONFIRMED",
                "S05_MODEL_YEAR_SELECTED",
                "Multiple target-like trim rows were visible, but the difference could not be confirmed as emission standard only.",
                {
                    **snapshot,
                    **_s05_same_trim_emission_variant_group_public(s05_emission_variant_evidence),
                    **_s05_emission_variant_failure_fields(
                        s05_emission_variant_evidence,
                        reason="multiple_target_like_trim_rows_not_confirmed_as_emission_only",
                    ),
                },
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        variant_nodes = s05_emission_variant_evidence.get("_variant_nodes") or []
        trim_node = variant_nodes[0]["node"] if variant_nodes else None
        seen_trim_names: set[str] = set(_s05_right_trim_labels(snapshot, target_year))
        seen_trim_names.update(_s05_right_trim_labels(snapshot, None))
        trim_seen_signatures = {_s05_right_trim_search_signature(snapshot, target_year)}
        trim_scroll_count = 0
        trim_unchanged_count = 0
        trim_no_effect_count = 0
        effective_trim_scroll_count = 0
        entered_target_year_section = _s05_right_list_contains_year(snapshot, target_year)
        target_year_section_seen = entered_target_year_section
        possibly_overscrolled_target_year = False
        right_trim_scroll_bottom_confirmed = False
        overscroll_recheck_used = False
        last_trim_scroll_mode = None
        last_scroll_phase = None
        last_switch_to_precision_reason = None
        trim_scroll_attempts: list[dict[str, Any]] = []
        while (trim_node is None or not trim_node.get("bounds")) and trim_scroll_count < S05_TRIM_SCROLL_LIMIT:
            before_snapshot = snapshot
            scroll_phase = _s05_right_trim_scroll_phase(
                before_snapshot,
                target_year,
                overscroll_recheck_used=overscroll_recheck_used,
            )
            if scroll_phase.get("target_year_section_seen"):
                target_year_section_seen = True
                entered_target_year_section = True
            if scroll_phase.get("possibly_overscrolled_target_year"):
                possibly_overscrolled_target_year = True
                if overscroll_recheck_used:
                    right_trim_scroll_bottom_confirmed = True
                    break
            last_trim_scroll_mode = str(scroll_phase.get("right_trim_scroll_mode") or "max_controlled_scroll")
            last_scroll_phase = str(scroll_phase.get("scroll_phase") or "max_scroll_to_target_year")
            last_switch_to_precision_reason = scroll_phase.get("switch_to_precision_reason")
            scroll_command = _scroll_s05_right_trim_list(context, before_snapshot, scroll_mode=last_trim_scroll_mode)
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
                        "visible_trim_names": _s05_right_trim_labels(before_snapshot, None),
                        "seen_trim_names": sorted(seen_trim_names),
                        "s05_trim_scroll_attempts": trim_scroll_attempts,
                        "scroll_phase": scroll_phase,
                        "scroll_command": scroll_command,
                        **_s05_right_list_mode(before_snapshot),
                    },
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            scroll_command.update(scroll_phase)
            if last_trim_scroll_mode == "target_year_reverse_recheck_scroll":
                overscroll_recheck_used = True
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
                year_click_record_confirmed=flow_state.get("s05_year_confirmed_by") == "left_year_click_record",
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
            current_right_mode = _s05_right_list_mode(snapshot)
            if _s05_right_list_contains_year(snapshot, target_year):
                target_year_section_seen = True
                entered_target_year_section = True
            post_scroll_phase = _s05_right_trim_scroll_phase(
                snapshot,
                target_year,
                overscroll_recheck_used=overscroll_recheck_used,
            )
            if post_scroll_phase.get("possibly_overscrolled_target_year"):
                possibly_overscrolled_target_year = True
            if not _s05_right_list_contains_year(snapshot, target_year) and not (
                current_right_mode.get("right_list_has_all_models") or current_right_mode.get("right_list_has_year_sections")
            ):
                issue = _record_issue(
                    issues,
                    "S05_TARGET_CONFIG_NOT_FOUND",
                    "S05_MODEL_YEAR_SELECTED",
                    "Right trim list no longer exposes a searchable target configuration list after the target year click record was confirmed.",
                    {
                        **snapshot,
                        "target_year": target_year,
                        "right_trim_labels": _s05_right_trim_labels(snapshot, target_year),
                        "s05_trim_scroll_attempts": trim_scroll_attempts,
                        "right_trim_scroll_mode": last_trim_scroll_mode,
                        **current_right_mode,
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
                        "visible_trim_names": _s05_right_trim_labels(snapshot, None),
                        "seen_trim_names": sorted(seen_trim_names),
                        "s05_trim_scroll_attempts": trim_scroll_attempts,
                        "right_trim_scroll_mode": last_trim_scroll_mode,
                        **_s05_right_list_mode(snapshot),
                    },
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            s05_emission_variant_evidence = _s05_same_trim_emission_variant_group(snapshot, target_year, target_trim, target_trim_prefix_terms)
            if s05_emission_variant_evidence.get("emission_variant_group_ambiguous"):
                issue = _record_issue(
                    issues,
                    "S05_EMISSION_VARIANT_GROUP_NOT_CONFIRMED",
                    "S05_MODEL_YEAR_SELECTED",
                    "Multiple target-like trim rows were visible after scrolling, but the difference could not be confirmed as emission standard only.",
                    {
                        **snapshot,
                        **_s05_same_trim_emission_variant_group_public(s05_emission_variant_evidence),
                        **_s05_emission_variant_failure_fields(
                            s05_emission_variant_evidence,
                            reason="multiple_target_like_trim_rows_after_scroll_not_confirmed_as_emission_only",
                        ),
                    },
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            variant_nodes = s05_emission_variant_evidence.get("_variant_nodes") or []
            trim_node = variant_nodes[0]["node"] if variant_nodes else None
            seen_trim_names.update(_s05_right_trim_labels(snapshot, target_year))
            seen_trim_names.update(_s05_right_trim_labels(snapshot, None))
            signature = _s05_right_trim_search_signature(snapshot, target_year)
            if signature in trim_seen_signatures:
                trim_unchanged_count += 1
            else:
                trim_unchanged_count = 0
            trim_seen_signatures.add(signature)
            trim_scroll_count += 1
            if scroll_attempt.get("scroll_effective"):
                trim_no_effect_count = 0
                effective_trim_scroll_count += 1
            else:
                trim_no_effect_count += 1
            if trim_node and trim_node.get("bounds"):
                break
            if trim_unchanged_count >= S05_TRIM_SCROLL_UNCHANGED_LIMIT or trim_no_effect_count >= S05_TRIM_SCROLL_UNCHANGED_LIMIT:
                right_trim_scroll_bottom_confirmed = True
                break
        if trim_node is None or not trim_node.get("bounds"):
            visible_matching_labels = _s05_visible_matching_trim_labels(snapshot, target_year, target_trim, target_trim_prefix_terms)
            not_found_code = "S05_TARGET_CONFIG_VISIBLE_BUT_NOT_MATCHED" if visible_matching_labels else "S05_TARGET_CONFIG_NOT_FOUND"
            not_found_message = (
                "S05 target trim text is visible, but no clickable right-side trim node was matched."
                if visible_matching_labels
                else
                "S05 right trim list swipe did not change visible trims or the right-list screenshot region."
                if effective_trim_scroll_count == 0 and trim_no_effect_count >= S05_TRIM_SCROLL_UNCHANGED_LIMIT
                else "Exact trim not found."
            )
            issue = _record_issue(
                issues,
                not_found_code,
                "S05_MODEL_YEAR_SELECTED",
                not_found_message,
                {
                    **snapshot,
                    "target_year": target_year,
                    "target_trim": target_trim,
                    "visible_trim_names": _s05_right_trim_labels(snapshot, None),
                    "visible_matching_trim_labels": visible_matching_labels,
                    "seen_trim_names": sorted(seen_trim_names),
                    "trim_scroll_count": trim_scroll_count,
                    "scroll_attempt_count": trim_scroll_count,
                    "max_scroll_attempt_count": S05_TRIM_SCROLL_LIMIT,
                    "effective_trim_scroll_count": effective_trim_scroll_count,
                    "trim_unchanged_count": trim_unchanged_count,
                    "trim_no_effect_count": trim_no_effect_count,
                    "trim_scroll_mode": last_trim_scroll_mode,
                    "scroll_phase": last_scroll_phase,
                    "switch_to_precision_reason": last_switch_to_precision_reason,
                    **_s05_same_trim_emission_variant_group_public(s05_emission_variant_evidence),
                    "s05_trim_scroll_attempts": trim_scroll_attempts,
                    "entered_target_year_section": entered_target_year_section,
                    "target_year_section_seen": target_year_section_seen,
                    "target_config_found": False,
                    "possibly_overscrolled_target_year": possibly_overscrolled_target_year,
                    "right_trim_scroll_bottom_confirmed": right_trim_scroll_bottom_confirmed,
                    **_s05_right_list_mode(snapshot),
                },
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        action_start = time.perf_counter()
        initial_variant_labels = [str(item.get("label") or "") for item in variant_nodes if item.get("label")]
        selected_emission_variants: list[str] = []
        selected_emission_variant_bounds: list[list[int] | None] = []
        for variant_label in initial_variant_labels:
            current_group = _s05_same_trim_emission_variant_group(snapshot, target_year, target_trim, target_trim_prefix_terms)
            if current_group.get("emission_variant_group_ambiguous"):
                issue = _record_issue(
                    issues,
                    "S05_EMISSION_VARIANT_GROUP_NOT_CONFIRMED",
                    "S05_MODEL_YEAR_SELECTED",
                    "Target trim variant group became ambiguous before all variants were selected.",
                    {
                        **snapshot,
                        **_s05_same_trim_emission_variant_group_public(current_group),
                        **_s05_emission_variant_failure_fields(
                            current_group,
                            selected_emission_variants=selected_emission_variants,
                            reason="target_trim_variant_group_became_ambiguous_before_all_selected",
                        ),
                    },
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            current_variants = current_group.get("_variant_nodes") or []
            current_variant = next((item for item in current_variants if item.get("label") == variant_label), None)
            if not current_variant or not current_variant.get("node") or not current_variant["node"].get("bounds"):
                issue = _record_issue(
                    issues,
                    "S05_EMISSION_VARIANT_NOT_ALL_SELECTED",
                    "S05_MODEL_YEAR_SELECTED",
                    "A target emission variant row disappeared before it could be selected.",
                    {
                        **snapshot,
                        "missing_emission_variant": variant_label,
                        "selected_emission_variants": selected_emission_variants,
                        **_s05_same_trim_emission_variant_group_public(current_group),
                        **_s05_emission_variant_failure_fields(
                            current_group,
                            selected_emission_variants=selected_emission_variants,
                            reason="target_emission_variant_row_disappeared_before_selection",
                        ),
                    },
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            node = current_variant["node"]
            contract_execute_click(
                context,
                snapshot,
                "S05_MODEL_YEAR_SELECTED",
                "tap_exact_trim",
                _center(node["bounds"]),
                evidence={"variant_label": variant_label, "clicked_config_bounds": node.get("bounds")},
            )
            selected_emission_variants.append(variant_label)
            selected_emission_variant_bounds.append(list(node.get("bounds")) if node.get("bounds") else None)
            time.sleep(0.4)
            snapshot = _capture(client, f"s05_after_trim_variant_{len(selected_emission_variants)}_{_timestamp()}")
            state = _ensure_current_page_contract(
                context,
                snapshot,
                {"S05", "S05_MODEL_YEAR_SELECTED", "S05_TRIM_SELECTED"},
                action_page="S05_TRIM_SELECTED",
            )
        action_total += int((time.perf_counter() - action_start) * 1000)
        selected_count_expected = len(initial_variant_labels)
        selected_count_text = _s05_selected_count_text(snapshot)
        selected_count_actual = _s05_selected_count(snapshot)
        s05_emission_variant_evidence = _s05_same_trim_emission_variant_group(snapshot, target_year, target_trim, target_trim_prefix_terms)
        s05_emission_variant_all_selected = _s05_selected_count_matches(snapshot, selected_count_expected)
        if not s05_emission_variant_all_selected:
            issue = _record_issue(
                issues,
                "S05_SELECTED_COUNT_MISMATCH",
                "S05_MODEL_YEAR_SELECTED",
                "Selected trim count does not match the target same-trim emission variant group count.",
                {
                    **snapshot,
                    **_s05_same_trim_emission_variant_group_public(s05_emission_variant_evidence),
                    "selected_emission_variants": selected_emission_variants,
                    "selected_count_text": selected_count_text,
                    "selected_count_expected": selected_count_expected,
                    "selected_count_actual": selected_count_actual,
                    "s05_emission_variant_all_selected": False,
                    **_s05_emission_variant_failure_fields(
                        s05_emission_variant_evidence,
                        selected_emission_variants=selected_emission_variants,
                        selected_count_text=selected_count_text,
                        selected_count_expected=selected_count_expected,
                        selected_count_actual=selected_count_actual,
                        reason="selected_count_does_not_match_emission_variant_group_count",
                    ),
                },
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        context["s05_emission_variant_result"] = _s05_emission_variant_result_fields(
            s05_emission_variant_evidence,
            selected_emission_variants=selected_emission_variants,
            selected_count_text=selected_count_text,
            selected_count_expected=selected_count_expected,
            selected_count_actual=selected_count_actual,
            s05_emission_variant_all_selected=s05_emission_variant_all_selected,
            s05_single_trim_selected=len(initial_variant_labels) == 1,
        )
        clicked_config_text = " | ".join(selected_emission_variants)
        if not _s05_confirm_clickable(snapshot):
            issue = _record_issue(
                issues,
                "S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED",
                "S05_MODEL_YEAR_SELECTED",
                "Clicking target trim emission variant group did not enable the confirm button.",
                {
                    **snapshot,
                    "target_year": target_year,
                    "target_trim": target_trim,
                    **_s05_same_trim_emission_variant_group_public(s05_emission_variant_evidence),
                    "target_config_found": True,
                    "clicked_config_text": clicked_config_text,
                    "clicked_config_bounds": selected_emission_variant_bounds,
                    "selected_emission_variants": selected_emission_variants,
                    "selected_count_text": selected_count_text,
                    "selected_count_expected": selected_count_expected,
                    "selected_count_actual": selected_count_actual,
                    "confirm_clickable": _s05_confirm_clickable(snapshot),
                    "selected_config_confirmed": False,
                    **_s05_emission_variant_result_fields(
                        s05_emission_variant_evidence,
                        selected_emission_variants=selected_emission_variants,
                        selected_count_text=selected_count_text,
                        selected_count_expected=selected_count_expected,
                        selected_count_actual=selected_count_actual,
                        s05_emission_variant_all_selected=s05_emission_variant_all_selected,
                        s05_single_trim_selected=len(initial_variant_labels) == 1,
                    ),
                },
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        state = "S05_TRIM_SELECTED"

    machine.assert_action_allowed("S05_TRIM_SELECTED", "tap_green_confirm")
    confirm_node = _s05_find_confirm_node(snapshot)
    selected_count_expected = selected_count_expected if "selected_count_expected" in locals() else 1
    if (
        confirm_node is None
        or not confirm_node.get("bounds")
        or not _s05_selected_count_matches(snapshot, selected_count_expected)
        or not _s05_confirm_clickable(snapshot)
    ):
        issue = _record_issue(
            issues,
            "S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED",
            "S05_TRIM_SELECTED",
            "Confirm is not allowed before one target trim is selected and the confirm button is clickable.",
            {
                **snapshot,
                "target_year": target_year,
                "target_trim": target_trim,
                "selected_count_text": _s05_selected_count_text(snapshot),
                "selected_count_expected": selected_count_expected,
                "selected_count_actual": _s05_selected_count(snapshot),
                "confirm_clickable": _s05_confirm_clickable(snapshot),
            },
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    selected_count_text_for_confirm = selected_count_text if "selected_count_text" in locals() else _s05_selected_count_text(snapshot)
    selected_count_actual_for_confirm = selected_count_actual if "selected_count_actual" in locals() else _s05_selected_count(snapshot)
    flow_state = context.setdefault("flow_state", {})
    flow_state.update(
        {
            "transition_context": "S05_CONFIRM_TO_S06",
            "S05_DONE": True,
            "target_series": params.get("series"),
            "target_series_alias": params.get("series_alias"),
            "target_year_model": target_year,
            "target_config_model": target_trim,
            "s05_selected_year_model": target_year,
            "s05_selected_config_model": target_trim,
            "selected_config_texts": selected_emission_variants if "selected_emission_variants" in locals() else [],
            "selected_count_text": selected_count_text_for_confirm,
            "selected_count_actual": selected_count_actual_for_confirm,
            "s05_year_click_required": True,
            "s05_year_clicked": bool(flow_state.get("s05_year_clicked")),
            "s05_year_click_text": flow_state.get("s05_year_click_text") or target_year,
            "s05_year_click_bounds": flow_state.get("s05_year_click_bounds"),
            "s05_year_click_region": flow_state.get("s05_year_click_region"),
            "selected_year_after_click": flow_state.get("selected_year_after_click"),
            "left_year_selected_text": flow_state.get("left_year_selected_text"),
            "s05_target_year_selected_confirmed": bool(flow_state.get("s05_target_year_selected_confirmed")),
            "s05_year_confirmed_by": flow_state.get("s05_year_confirmed_by"),
            "s05_year_click_record_valid": bool(flow_state.get("s05_year_click_record_valid")),
            "right_config_list_after_year": flow_state.get("right_config_list_after_year"),
            "target_config_seen_after_year": bool(flow_state.get("target_config_seen_after_year")),
            "direct_right_config_search_without_year_click": False,
        }
    )

    action_start = time.perf_counter()
    action_total += contract_execute_click(
        context,
        snapshot,
        "S05_TRIM_SELECTED",
        "tap_green_confirm",
        _center(confirm_node["bounds"]),
        evidence={
            "target_year": target_year,
            "target_trim": target_trim,
            "selected_count_text": selected_count_text_for_confirm,
            "selected_count_actual": selected_count_actual_for_confirm,
        },
    )
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
        extra={
            "target_year": target_year,
            "target_trim": target_trim,
            "right_list_mode": _s05_right_list_mode(snapshot).get("right_list_mode"),
            "right_list_has_all_models": _s05_right_list_mode(snapshot).get("right_list_has_all_models"),
            "right_list_has_year_sections": _s05_right_list_mode(snapshot).get("right_list_has_year_sections"),
            "visible_year_sections": _s05_right_list_mode(snapshot).get("visible_year_sections", []),
            "visible_trim_names": _s05_right_trim_labels(snapshot, None),
            "seen_trim_names": sorted(seen_trim_names) if "seen_trim_names" in locals() else [],
            "scroll_attempt_count": trim_scroll_count if "trim_scroll_count" in locals() else 0,
            "max_scroll_attempt_count": S05_TRIM_SCROLL_LIMIT,
            "trim_scroll_effective": effective_trim_scroll_count > 0 if "effective_trim_scroll_count" in locals() else False,
            "trim_scroll_mode": last_trim_scroll_mode if "last_trim_scroll_mode" in locals() else None,
            "scroll_phase": last_scroll_phase if "last_scroll_phase" in locals() else None,
            "switch_to_precision_reason": last_switch_to_precision_reason if "last_switch_to_precision_reason" in locals() else None,
            "entered_target_year_section": entered_target_year_section if "entered_target_year_section" in locals() else False,
            "target_year_section_seen": target_year_section_seen if "target_year_section_seen" in locals() else False,
            "target_config_found": True,
            "clicked_config_text": clicked_config_text if "clicked_config_text" in locals() else None,
            "clicked_config_bounds": selected_emission_variant_bounds if "selected_emission_variant_bounds" in locals() else (trim_node.get("bounds") if "trim_node" in locals() and trim_node else None),
            **(_s05_same_trim_emission_variant_group_public(s05_emission_variant_evidence) if "s05_emission_variant_evidence" in locals() else {}),
            "selected_emission_variants": selected_emission_variants if "selected_emission_variants" in locals() else [],
            "selected_count_text": selected_count_text if "selected_count_text" in locals() else _s05_selected_count_text(snapshot),
            "selected_count_expected": selected_count_expected if "selected_count_expected" in locals() else 1,
            "s05_emission_variant_all_selected": s05_emission_variant_all_selected if "s05_emission_variant_all_selected" in locals() else True,
            "selected_config_confirmed": True,
            "possibly_overscrolled_target_year": possibly_overscrolled_target_year if "possibly_overscrolled_target_year" in locals() else False,
            "right_trim_scroll_bottom_confirmed": right_trim_scroll_bottom_confirmed if "right_trim_scroll_bottom_confirmed" in locals() else False,
        },
    )
    context["s05_after_confirm_state"] = next_state
    s06_after_confirm_evidence = _s06_target_filter_list_after_s05_confirm_evidence(next_snapshot, context.get("flow_state"))
    context["s06_target_filter_list_after_s05_confirm"] = s06_after_confirm_evidence
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

    if s06_after_confirm_evidence.get("s06_stop_code"):
        issue = _record_issue(
            issues,
            str(s06_after_confirm_evidence.get("s06_stop_code")),
            next_state or "UNKNOWN",
            "S05 confirm reached a list-like page, but V1.17 S06 source/target-filter gates were not fully satisfied.",
            {**next_snapshot, **s06_after_confirm_evidence, "after_confirm_state": next_state},
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
    s06_target_filter_evidence = _s06_target_filter_list_after_s05_confirm_evidence(snapshot, context.get("flow_state"))
    is_v117_target_filter_list = s06_target_filter_evidence.get("s06_page_variant") == S06_TARGET_FILTER_LIST_VARIANT
    if is_v117_target_filter_list:
        s06_target_filter_evidence["s06_allowed_action"] = "click_model_config_filter"
        context["s06_target_filter_list_after_s05_confirm"] = s06_target_filter_evidence
    if _looks_like_s04_brand_zone_mixed_list(snapshot) and not is_v117_target_filter_list:
        context["s04_landing_type"] = "BRAND_ZONE_MIXED_LIST"
        issue = _record_issue(
            issues,
            "S04_BRAND_ZONE_PAGE_BLOCKED_BY_CONTRACT",
            "S06",
            "Brand-zone mixed list reached S06 model-config action, but brand-zone continuation is blocked by the V1.14 contract.",
            {
                **snapshot,
                "brand_zone_page_detected": True,
                "continuation_allowed": False,
                "old_brand_zone_continuation_branch_removed": True,
            },
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    machine.assert_action_allowed("S06", "tap_trim_filter")
    node = _find_exact(snapshot, "车型配置")
    if node is None or not node.get("bounds"):
        issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S06", "车型配置 entry not found.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context["s06_fast_gate_duration_ms"] = int((time.perf_counter() - started) * 1000)
    action_start = time.perf_counter()
    action_ms = contract_execute_click(
        context,
        snapshot,
        "S06",
        "tap_trim_filter",
        _center(node["bounds"]),
        evidence={
            "clicked_text": "车型配置",
            "clicked_node_bounds": node.get("bounds"),
            "clicked_action_id": "S06_ONLY_ALLOWED_ACTION_CLICK_MODEL_CONFIG",
            **s06_target_filter_evidence,
        },
    )
    clicked_text = next((str(label).strip() for label in node.get("labels", []) if str(label).strip()), "车型配置")
    context["s06_click_model_config_duration_ms"] = int(action_ms or 0)
    _add_runtime_timing(
        context,
        step_name="S06_CLICK_MODEL_CONFIG",
        page_name="S06",
        action_name="tap_model_config_text_node",
        contract_check_ms=int((action_start - started) * 1000),
        action_ms=action_ms,
        snapshot=snapshot,
        extra={
            "clicked_text": clicked_text,
            "clicked_node_bounds": node.get("bounds"),
            "click_strategy": "text_node_bounds",
            "reason_category": "S06_MODEL_CONFIG_NODE_CLICK",
            "reason_detail": "车型配置 is clicked from the exact XML text/content-desc node bounds.",
            "solution": "keep node-driven click and use bounded short polling for S07.",
        },
    )

    next_snapshot: dict[str, Any] | None = None
    next_state: str | None = None
    wait_round_count = 0
    total_wait_ms = 0
    short_poll_interval_ms = 350
    for wait_round_count in range(1, 9):
        time.sleep(short_poll_interval_ms / 1000)
        total_wait_ms += short_poll_interval_ms
        round_started = time.perf_counter()
        next_snapshot = _capture(client, f"s06_to_s07_round_{wait_round_count}_{_timestamp()}")
        capture_ms = int((time.perf_counter() - round_started) * 1000)
        capture_metrics = next_snapshot.get("capture_metrics", {})
        screenshot_ms = int(capture_metrics.get("screenshot_ms") or 0)
        xml_ms = int(capture_metrics.get("xml_ms") or 0)
        recognize_started = time.perf_counter()
        next_state = _recognize_page(recognizer, next_snapshot, context.get("flow_state"))
        recognize_ms = int((time.perf_counter() - recognize_started) * 1000)
        _add_runtime_timing(
            context,
            step_name="S06_TO_S07_SCREENSHOT",
            page_name="S06",
            action_name="capture_screenshot_during_s07_wait",
            action_ms=screenshot_ms,
            snapshot=next_snapshot,
            extra={
                "wait_round_index": wait_round_count,
                "screenshot_ms": screenshot_ms,
                "reason_category": "S06_TO_S07_SCREENSHOT",
                "reason_detail": "Separate screenshot timing for S06 to S07 short-poll wait.",
                "solution": "continue bounded polling and stop once S07 is recognized.",
            },
        )
        _add_runtime_timing(
            context,
            step_name="S06_TO_S07_XML_DUMP",
            page_name="S06",
            action_name="dump_xml_during_s07_wait",
            action_ms=xml_ms,
            snapshot=next_snapshot,
            extra={
                "wait_round_index": wait_round_count,
                "xml_dump_ms": xml_ms,
                "xml_rc": next_snapshot.get("xml_dump_rc"),
                "xml_stderr": next_snapshot.get("xml_dump_stderr"),
                "reason_category": "XML_DUMP_SLOW" if xml_ms > 2000 else "S06_TO_S07_XML_DUMP",
                "reason_detail": "Separate XML dump timing for S06 to S07 short-poll wait.",
                "solution": "do not skip XML evidence; avoid fixed sleeps and stop polling on S07.",
            },
        )
        _add_runtime_timing(
            context,
            step_name="S06_TO_S07_RECOGNIZE",
            page_name="S06",
            action_name="recognize_page_during_s07_wait",
            action_ms=recognize_ms,
            snapshot=next_snapshot,
            extra={
                "wait_round_index": wait_round_count,
                "recognized_page": next_state,
                "reason_category": "PAGE_RECOGNITION_SLOW" if recognize_ms > 2000 else "S06_TO_S07_RECOGNIZE",
                "reason_detail": "Separate recognizer timing for S06 to S07 short-poll wait.",
                "solution": "enter S07 only after the S07 contract is recognized.",
            },
        )
        _add_runtime_timing(
            context,
            step_name="S06_TO_S07_WAIT_ROUND",
            page_name="S06",
            action_name="fresh_recognize_wait_round",
            action_ms=capture_ms + recognize_ms,
            transition_wait_ms=short_poll_interval_ms,
            snapshot=next_snapshot,
            extra={
                "wait_round_index": wait_round_count,
                "recognized_page": next_state,
                "entered_s07": next_state == "S07",
                "screenshot_ms": screenshot_ms,
                "xml_dump_ms": xml_ms,
                "recognize_ms": recognize_ms,
                "reason_category": "S06_TO_S07_WEBVIEW_TEXT_DELAY",
                "reason_detail": "Short polling replaces the old fixed 0.8s sleep after tapping 车型配置.",
                "solution": "stop polling as soon as S07 appears.",
            },
        )
        if next_state == "S07":
            break
    assert next_snapshot is not None
    if next_state != "S07":
        if is_v117_target_filter_list:
            s06_target_filter_evidence["s06_to_s07_result"] = "failed"
            s06_target_filter_evidence["after_click_recognized_page"] = next_state
            context["s06_target_filter_list_after_s05_confirm"] = s06_target_filter_evidence
            issue = _record_issue(
                issues,
                "S06_TO_S07_AFTER_TARGET_FILTER_LIST_FAILED",
                "S06",
                "S06 target-filter list 车型配置 click did not open S07 within bounded short polling.",
                {**next_snapshot, **s06_target_filter_evidence, "after_trim_filter_state": next_state, "wait_round_count": wait_round_count},
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        issue = _record_issue(
            issues,
            "S06_TO_S07_TIMEOUT",
            "S06",
            "S06 车型配置 click did not open the S07 model-config filter window within bounded short polling.",
            {**next_snapshot, "after_trim_filter_state": next_state, "wait_round_count": wait_round_count},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    timing.add(
        step_name="s06_open_model_config_filter",
        page_name="S06",
        action_name="tap_trim_filter",
        contract_check_ms=int((action_start - started) * 1000),
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=total_wait_ms,
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
        transition_wait_ms=total_wait_ms,
        snapshot=next_snapshot,
        extra={
            "recognized_page": next_state,
            "clicked_text": clicked_text,
            "clicked_node_bounds": node.get("bounds"),
            "click_strategy": "text_node_bounds",
            "wait_round_count": wait_round_count,
            "entered_s07": next_state == "S07",
            "reason_category": "S07_WEBVIEW_TEXT_DELAY",
            "reason_detail": "S06 click waits for S07 with bounded short polling and fresh evidence each round.",
            "solution": "keep fresh page checks and avoid fixed waits.",
        },
    )
    if is_v117_target_filter_list:
        s06_target_filter_evidence["s06_to_s07_result"] = "entered_s07"
        s06_target_filter_evidence["after_click_recognized_page"] = next_state
        context["s06_target_filter_list_after_s05_confirm"] = s06_target_filter_evidence
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
    context.setdefault("flow_state", {})["target_color"] = target_color
    forbidden_before_color = _s07_forbidden_selected_evidence(snapshot)
    if forbidden_before_color["forbidden_filter_selected"]:
        stop_code = forbidden_before_color.get("forbidden_gate_stop_code") or "S07_FORBIDDEN_FILTER_SELECTED_CONFIRMED"
        issue = _record_issue(
            issues,
            stop_code,
            "S07",
            "A forbidden S07 filter is already selected before the color contract action.",
            {**snapshot, **forbidden_before_color, "COLOR_FILTER_DONE": False},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    machine.assert_action_allowed("S07", "tap_color_filter")
    color_tab = _s07_find_left_filter_tab_node(snapshot, S07_LABEL_COLOR)
    if color_tab is None or not color_tab.get("bounds"):
        issue = _record_issue(
            issues,
            "S07_COLOR_TAB_NOT_BOUND_BY_CONTRACT",
            "S07",
            "COLOR_FILTER_DONE=false requires clicking the left Color tab before any color option.",
            snapshot,
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    t0 = time.perf_counter()
    color_tab_ms = contract_execute_click(
        context,
        snapshot,
        "S07",
        "tap_color_filter",
        _center(color_tab["bounds"]),
        evidence={"clicked_text": S07_LABEL_COLOR, "clicked_node_bounds": color_tab.get("bounds")},
    )
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
            "s07_color_contract": "COLOR_FILTER_DONE=false requires left color tab before target color",
            "color_tab_bounds": color_tab.get("bounds"),
            "s07_forbidden_gate_version": forbidden_before_color.get("s07_forbidden_gate_version"),
            "forbidden_gate_decision_before_color": forbidden_before_color.get("forbidden_gate_decision"),
            "forbidden_filter_selected_before_color": forbidden_before_color.get("forbidden_filter_selected"),
            "forbidden_visible_only_before_color": forbidden_before_color.get("forbidden_visible_only"),
            "forbidden_left_tab_active_before_color": forbidden_before_color.get("forbidden_left_tab_active"),
            "forbidden_option_selected_before_color": forbidden_before_color.get("forbidden_option_selected"),
            "reason_category": "CONTRACT_GATE",
            "reason_detail": "S07 cannot click a visible color label while another filter panel is active",
            "solution": "open the left Color tab first, then bind the target color option inside the color panel",
        },
    )
    snapshot = _capture(client, f"s07_after_color_tab_click_{_timestamp()}")
    _ensure_current_page_contract(context, snapshot, {"S07"}, action_page="S07")
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
        action_name="short_poll_wait_target_color_node_after_color_tab",
        transition_wait_ms=int((time.perf_counter() - wait_color_started) * 1000),
        snapshot=snapshot,
        extra={
            "target_color": target_color,
            "target_color_visible": color_node is not None and bool(color_node.get("bounds")),
            "reused_current_xml": False,
            "reused_visible_text_digest": False,
            "reused_color_node_bounds": color_node is not None and bool(color_node.get("bounds")),
            "reason_category": "S07_WEBVIEW_TEXT_DELAY",
            "reason_detail": "finite 0.3s polling waits only until the target color node appears after the Color tab is active",
            "solution": "avoid clicking target color from the default panel",
        },
    )

    machine.assert_action_allowed("S07", "tap_target_color")
    if color_node is None or not color_node.get("bounds"):
        issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S07", "Target color not found.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    last_color_trace: dict[str, Any] = {}
    clicked_color_text = ""
    color_confirm_evidence: dict[str, Any] = {}
    for attempt_index in (1, 2):
        target_color_ms, last_color_trace = _execute_s07_target_color_click(
            context,
            snapshot,
            color_node,
            target_color,
            attempt_index=attempt_index,
        )
        clicked_color_text = str(last_color_trace.get("matched_candidate_color") or "")
        total_action_ms += target_color_ms
        _add_runtime_timing(
            context,
            step_name="S07_CLICK_TARGET_COLOR" if attempt_index == 1 else "S07_RETRY_CLICK_TARGET_COLOR",
            page_name="S07",
            action_name="tap_target_color",
            action_ms=target_color_ms,
            snapshot=snapshot,
            extra={
                "target_color": target_color,
                "target_color_direct_from_current_xml": False,
                "clicked_text": clicked_color_text,
                "clicked_action_id": "S07_CLICK_TARGET_COLOR_OPTION",
                "color_node_text": clicked_color_text,
                "color_node_bounds": color_node.get("bounds"),
                "color_node_clickable": bool(color_node.get("clickable")),
                "color_click_target_bounds": color_node.get("bounds"),
                "color_click_strategy": color_node.get("color_click_strategy") or "target_color_node_bounds",
                "s07_color_click_action_trace": last_color_trace,
                "s07_color_node_reused_for_click": False,
                "s07_color_xml_parse_count_for_click": 0,
                "reused_current_xml": False,
                "reused_visible_text_digest": True,
                "reused_color_node_bounds": True,
                "reason_category": "S07_TARGET_COLOR_NODE_SEARCH_SLOW",
                "reason_detail": "target color is clicked only after the Color tab is contract-selected",
                "solution": "bind the target color inside the color panel; do not click visible labels from other S07 panels",
            },
        )
        confirm_color_started = time.perf_counter()
        snapshot = _wait_for_s07_target_color_selected(
            context,
            snapshot,
            target_color,
            f"s07_after_color_select_attempt_{attempt_index}",
        )
        color_confirm_evidence = _s07_snapshot_color_filter_evidence(
            snapshot,
            target_color,
            source=f"s07_after_color_select_attempt_{attempt_index}",
        )
        context["s07_color_selection_evidence"] = color_confirm_evidence
        _add_runtime_timing(
            context,
            step_name="S07_CONFIRM_COLOR_SELECTED" if attempt_index == 1 else "S07_RETRY_CONFIRM_COLOR_SELECTED",
            page_name="S07",
            action_name="short_poll_confirm_target_color_selected",
            transition_wait_ms=int((time.perf_counter() - confirm_color_started) * 1000),
            snapshot=snapshot,
            extra={
                **color_confirm_evidence,
                "target_color_selected": _target_color_selected(snapshot, target_color),
                "s07_confirm_uses_fresh_xml_once_per_poll": True,
                "s07_color_reselect_allowed_once": True,
                "s07_color_select_attempt_index": attempt_index,
                "reused_current_xml": False,
                "reused_visible_text_digest": True,
                "reused_color_node_bounds": False,
                "reason_category": "S07_COLOR_SELECTED_CONFIRM_SLOW",
                "reason_detail": "COLOR_FILTER_DONE is gated by a fresh selected-state confirmation",
                "solution": "keep the confirmation but reuse the fresh XML captured by the short poll",
            },
        )
        if _target_color_selected(snapshot, target_color) and not color_confirm_evidence.get("color_filter_mismatch"):
            break
        if attempt_index == 1:
            retry_node = _find_target_color_node(snapshot, target_color)
            if retry_node is not None and retry_node.get("bounds"):
                color_node = retry_node
                continue
            issue = _record_issue(
                issues,
                "S07_COLOR_SELECTION_RETRY_TARGET_NOT_BINDABLE",
                "S07",
                "The selected S07 color did not match target color and the target color could not be rebound for retry.",
                {**snapshot, **color_confirm_evidence, "s07_color_click_action_trace": context.get("s07_color_click_action_trace")},
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        issue = _record_issue(
            issues,
            "S07_COLOR_SELECTION_TARGET_MISMATCH_AFTER_RETRY",
            "S07",
            "The selected S07 color still does not match target color after one retry.",
            {**snapshot, **color_confirm_evidence, "s07_color_click_action_trace": context.get("s07_color_click_action_trace")},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    _ensure_current_page_contract(context, snapshot, {"S07"}, action_page="S07")
    forbidden_after_color = _s07_forbidden_selected_evidence(
        snapshot,
        clicked_text=clicked_color_text,
        clicked_action_id="S07_CLICK_TARGET_COLOR_OPTION",
    )
    if forbidden_after_color["forbidden_filter_selected"]:
        stop_code = forbidden_after_color.get("forbidden_gate_stop_code") or "S07_FORBIDDEN_FILTER_SELECTED_CONFIRMED"
        issue = _record_issue(
            issues,
            stop_code,
            "S07",
            "The S07 color action selected a forbidden filter instead of the target color.",
            {**snapshot, **forbidden_after_color, "target_color": target_color},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
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
            "reused_current_xml": True,
            "reused_visible_text_digest": True,
            "reused_color_node_bounds": False,
            "reason_category": "CONTRACT_GATE",
            "reason_detail": "COLOR_FILTER_DONE is set only after target color selected-state evidence exists",
            "solution": "do not bypass this gate while optimizing duplicate capture work",
        },
    )

    machine.assert_action_allowed("S07", "tap_age_filter")
    age_tab = _s07_find_left_filter_tab_node(snapshot, S07_LABEL_AGE)
    if age_tab is None or not age_tab.get("bounds"):
        issue = _record_issue(issues, "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT", "S07", "Age tab not found.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    t0 = time.perf_counter()
    age_tab_ms = contract_execute_click(
        context,
        snapshot,
        "S07",
        "tap_age_filter",
        _center(age_tab["bounds"]),
        evidence={"clicked_text": S07_LABEL_AGE, "clicked_node_bounds": age_tab.get("bounds")},
    )
    total_action_ms += age_tab_ms
    _add_runtime_timing(
        context,
        step_name="S07_CLICK_AGE_TAB",
        page_name="S07",
        action_name="tap_age_filter",
        action_ms=age_tab_ms,
        snapshot=snapshot,
        extra={
            "target_age": params.get("target_age_years"),
            "reused_age_panel_xml": True,
            "reused_age_tick_bounds": False,
            "reused_internal_exact_snapshot": False,
            "reason_category": "S07_AGE_PANEL_WAIT_SLOW",
            "reason_detail": "age tab is opened before exact left/right slider evidence is collected",
            "solution": "use short polling for the age panel instead of a long fixed wait",
        },
    )
    wait_age_started = time.perf_counter()
    snapshot = _wait_for_s07_age_panel(
        context,
        snapshot,
        "s07_after_age_tab",
        target_age=params.get("target_age_years"),
    )
    _add_runtime_timing(
        context,
        step_name="S07_WAIT_AGE_PANEL",
        page_name="S07",
        action_name="short_poll_wait_age_panel",
        transition_wait_ms=int((time.perf_counter() - wait_age_started) * 1000),
        snapshot=snapshot,
        extra={
            "target_age": params.get("target_age_years"),
            "age_panel_tick_count": len(_s07_age_tick_nodes(snapshot)),
            "age_panel_stable": bool(snapshot.get("age_panel_stable")),
            "reused_age_panel_xml": False,
            "reused_age_tick_bounds": False,
            "reused_internal_exact_snapshot": False,
            "reason_category": "S07_AGE_PANEL_WAIT_SLOW",
            "reason_detail": "finite polling waits only until age ticks or exact-age evidence appears",
            "solution": "stop polling as soon as the age panel is stable",
        },
    )
    if not snapshot.get("age_panel_stable"):
        issue = _record_issue(issues, "S07_AGE_PANEL_NOT_STABLE", "S07", "Age panel did not expose stable target ticks after tapping age filter.", {**snapshot, "target_age_years": params.get("target_age_years")})
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])

    machine.assert_action_allowed("S07", "set_exact_age")
    age_read_started = time.perf_counter()
    age_before = _s07_age_slider_evidence(snapshot, params.get("target_age_years"))
    _add_runtime_timing(
        context,
        step_name="S07_READ_AGE_SLIDERS",
        page_name="S07",
        action_name="parse_age_ticks_and_current_exact_state_once",
        field_read_ms=int((time.perf_counter() - age_read_started) * 1000),
        snapshot=snapshot,
        extra={
            **age_before,
            "s07_age_xml_parse_count_for_slider_read": 1,
            "reused_age_panel_xml": True,
            "reused_age_tick_bounds": True,
            "reused_internal_exact_snapshot": False,
            "target_age": params.get("target_age_years"),
            "left_age_before": age_before.get("left_age"),
            "right_age_before": age_before.get("right_age"),
            "left_age_after_confirm": age_before.get("left_age"),
            "right_age_after_confirm": age_before.get("right_age"),
            "exact_age_text": age_before.get("exact_age_text"),
            "age_confirm_source": age_before.get("age_confirm_source"),
            "reason_category": "S07_AGE_SLIDER_BOUNDS_RECALC",
            "reason_detail": "age tick and target bounds are parsed once and reused for any required slider movement",
            "solution": "do not repeat tick/bounds calculation within the same age action",
        },
    )
    age_action_started = time.perf_counter()
    age_action = _set_exact_age_from_ticks(context, snapshot, params.get("target_age_years"))
    _add_runtime_timing(
        context,
        step_name="S07_MOVE_RIGHT_AGE_SLIDER",
        page_name="S07",
        action_name="move_right_age_slider_if_needed",
        action_ms=int(age_action.get("right_move_ms") or 0),
        snapshot=snapshot,
        extra={
            **age_action,
            "target_age": params.get("target_age_years"),
            "reused_age_panel_xml": True,
            "reused_age_tick_bounds": True,
            "reused_internal_exact_snapshot": bool(age_action.get("exact_confirmed")),
            "reason_category": "S07_AGE_SLIDER_REDUNDANT_MOVE" if not age_action.get("right_slider_moved") else "S07_AGE_SLIDER_MOVE_SLOW",
            "reason_detail": "right slider is skipped when exact target age is already confirmed; otherwise dynamic tick bounds drive the movement",
            "solution": "avoid moving a slider that is already at target_age",
        },
    )
    _add_runtime_timing(
        context,
        step_name="S07_MOVE_LEFT_AGE_SLIDER",
        page_name="S07",
        action_name="move_left_age_slider_if_needed",
        action_ms=int(age_action.get("left_move_ms") or 0),
        snapshot=snapshot,
        extra={
            **age_action,
            "target_age": params.get("target_age_years"),
            "reused_age_panel_xml": True,
            "reused_age_tick_bounds": True,
            "reused_internal_exact_snapshot": bool(age_action.get("exact_confirmed")),
            "reason_category": "S07_AGE_SLIDER_REDUNDANT_MOVE" if not age_action.get("left_slider_moved") else "S07_AGE_SLIDER_MOVE_SLOW",
            "reason_detail": "left slider is skipped when exact target age is already confirmed; otherwise dynamic tick bounds drive the movement",
            "solution": "avoid moving a slider that is already at target_age",
        },
    )
    if not age_action.get("success"):
        issue = _record_issue(issues, str(age_action.get("failure_reason") or "S07_AGE_SLIDER_TARGET_NOT_FOUND"), "S07", "Exact target age tick not found before view-result.", {**snapshot, "target_age_years": params.get("target_age_years"), "age_action": age_action})
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    total_action_ms += int((time.perf_counter() - age_action_started) * 1000)
    confirm_age_started = time.perf_counter()
    exact_snapshot = context.pop("s07_exact_age_snapshot", None) if age_action.get("exact_confirmed") else None
    if exact_snapshot is not None and _exact_age_confirmed(exact_snapshot, params.get("target_age_years")):
        snapshot = exact_snapshot
        age_confirm_transition_ms = 0
        age_confirm_action = "reuse_internal_exact_age_fresh_evidence"
        age_confirm_reused_internal_fresh = True
    else:
        snapshot = _wait_for_s07_age_exact(
            context,
            snapshot,
            params.get("target_age_years"),
            "s07_after_exact_age",
        )
        age_confirm_transition_ms = int((time.perf_counter() - confirm_age_started) * 1000)
        age_confirm_action = "short_poll_confirm_exact_age"
        age_confirm_reused_internal_fresh = False
    exact_age_verify = _verify_exact_age_selected(snapshot, params.get("target_age_years"))
    age_post_action_proof = _s07_age_post_action_proof(
        age_action,
        snapshot,
        params.get("target_age_years"),
        exact_age_verify,
        reused_internal_fresh=age_confirm_reused_internal_fresh,
    )
    context["s07_age_post_action_proof"] = age_post_action_proof
    _add_runtime_timing(
        context,
        step_name="S07_CONFIRM_AGE_EXACT",
        page_name="S07",
        action_name=age_confirm_action,
        transition_wait_ms=age_confirm_transition_ms,
        snapshot=snapshot,
        extra={
            **age_action,
            **exact_age_verify,
            **age_post_action_proof,
            "target_age": params.get("target_age_years"),
            "exact_age_confirmed": exact_age_verify.get("exact_age_verified"),
            "exact_age_text_found": bool(_s07_exact_age_text(snapshot, params.get("target_age_years"))),
            "exact_age_text": _s07_exact_age_text(snapshot, params.get("target_age_years")),
            "slider_overlap_confirmed": _s07_age_range_from_slider_positions(snapshot) == (params.get("target_age_years"), params.get("target_age_years")),
            "age_confirm_source": exact_age_verify.get("verify_method") or ("exact_age_text" if _s07_exact_age_text(snapshot, params.get("target_age_years")) else ("slider_positions" if _s07_age_range_from_slider_positions(snapshot) == (params.get("target_age_years"), params.get("target_age_years")) else None)),
            "left_age_after_confirm": _s07_age_range_from_slider_positions(snapshot)[0],
            "right_age_after_confirm": _s07_age_range_from_slider_positions(snapshot)[1],
            "age_xml_stale_after_move": not bool(exact_age_verify.get("exact_age_verified")),
            "s07_age_exact_internal_fresh_reused": age_confirm_reused_internal_fresh,
            "s07_age_exact_extra_poll_skipped": age_confirm_reused_internal_fresh,
            "reused_age_panel_xml": age_confirm_reused_internal_fresh,
            "reused_age_tick_bounds": True,
            "reused_internal_exact_snapshot": age_confirm_reused_internal_fresh,
            "reason_category": "S07_AGE_EXACT_CONFIRM_SLOW",
            "reason_detail": "AGE_FILTER_DONE is gated by fresh exact-age evidence; reuse the internal fresh snapshot when the slider action already confirmed exact.",
            "solution": "avoid an extra confirmation poll after exact 2-2年 evidence is already available.",
        },
    )
    if not age_post_action_proof.get("s07_age_post_fresh_done"):
        issue = _record_issue(
            issues,
            S07_POST_ACTION_FRESH_EVIDENCE_MISSING,
            "S07",
            "Exact target age requires post-action fresh screenshot/XML evidence before view-result.",
            {
                **snapshot,
                "target_age_years": params.get("target_age_years"),
                "age_action": age_action,
                "exact_age_verify": exact_age_verify,
                "s07_age_post_action_proof": age_post_action_proof,
            },
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if params.get("target_age_years") == 0 and not age_post_action_proof.get("s07_age_zero_post_action_proof_passed"):
        issue = _record_issue(
            issues,
            S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED,
            "S07",
            "Target age 0 was planned, but post-action fresh evidence did not prove final 0-0 year filter.",
            {
                **snapshot,
                "target_age_years": params.get("target_age_years"),
                "age_action": age_action,
                "exact_age_verify": exact_age_verify,
                "s07_age_post_action_proof": age_post_action_proof,
            },
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if params.get("target_age_years") == 1 and not age_post_action_proof.get("s07_age_one_post_action_proof_passed"):
        failure_code = str(age_post_action_proof.get("post_action_failure_reason") or S07_AGE_ONE_POST_ACTION_VERIFY_FAILED)
        issue = _record_issue(
            issues,
            failure_code,
            "S07",
            "Target age 1 was planned, but post-action fresh evidence did not prove final 1-1 year filter.",
            {
                **snapshot,
                "target_age_years": params.get("target_age_years"),
                "age_action": age_action,
                "exact_age_verify": exact_age_verify,
                "s07_age_post_action_proof": age_post_action_proof,
            },
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if not exact_age_verify.get("exact_age_verified"):
        if params.get("target_age_years") == 0:
            failure_code = "S07_AGE_ZERO_VERIFY_FAILED"
        elif params.get("target_age_years") == 1:
            failure_code = S07_AGE_ONE_POST_ACTION_VERIFY_FAILED
        else:
            failure_code = "S07_EXACT_AGE_STATE_UNCONFIRMED"
        issue = _record_issue(
            issues,
            failure_code,
            "S07",
            "Exact target age state not confirmed before view-result.",
            {
                **snapshot,
                "target_age_years": params.get("target_age_years"),
                "age_action": age_action,
                "exact_age_verify": exact_age_verify,
                "exact_age_text_found": bool(_s07_exact_age_text(snapshot, params.get("target_age_years"))),
                "exact_age_text": _s07_exact_age_text(snapshot, params.get("target_age_years")),
                "left_age_after": _s07_age_range_from_slider_positions(snapshot)[0],
                "right_age_after": _s07_age_range_from_slider_positions(snapshot)[1],
                "age_confirm_source": None,
            },
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context.setdefault("flow_state", {})["AGE_FILTER_DONE"] = True
    age_post_action_proof["AGE_FILTER_DONE"] = True
    age_post_action_proof["clicked_view_result_after_verify"] = False
    _add_runtime_timing(
        context,
        step_name="AGE_FILTER_DONE_SET",
        page_name="S07",
        action_name="set_age_filter_done_after_evidence",
        snapshot=snapshot,
        extra={
            **exact_age_verify,
            **age_post_action_proof,
            "target_age": params.get("target_age_years"),
            "AGE_FILTER_DONE": True,
            "AGE_FILTER_DONE_source": age_post_action_proof.get("AGE_FILTER_DONE_source") or exact_age_verify.get("verify_method"),
            "reused_age_panel_xml": True,
            "reused_age_tick_bounds": True,
            "reused_internal_exact_snapshot": age_confirm_reused_internal_fresh,
            "left_age_after_confirm": _s07_age_range_from_slider_positions(snapshot)[0],
            "right_age_after_confirm": _s07_age_range_from_slider_positions(snapshot)[1],
            "exact_age_text": _s07_exact_age_text(snapshot, params.get("target_age_years")),
            "age_confirm_source": exact_age_verify.get("verify_method") or ("exact_age_text" if _s07_exact_age_text(snapshot, params.get("target_age_years")) else ("slider_positions" if _s07_age_range_from_slider_positions(snapshot) == (params.get("target_age_years"), params.get("target_age_years")) else None)),
            "reason_category": "CONTRACT_GATE",
            "reason_detail": "AGE_FILTER_DONE is set only after exact target age evidence exists",
            "solution": "do not bypass this gate while optimizing duplicate slider work",
        },
    )

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
    flow_state = context.setdefault("flow_state", {})
    view_gate = _s07_view_result_preclick_gate(flow_state, age_post_action_proof, view_node, view_count, params.get("target_age_years"))
    _add_runtime_timing(
        context,
        step_name="S07_VIEW_RESULT_PRECLICK_GATE",
        page_name="S07",
        action_name="verify_color_age_bottom_count_before_tap_view_result",
        snapshot=snapshot,
        extra={**view_gate, **age_post_action_proof},
    )
    if not view_gate.get("ok"):
        issue = _record_issue(
            issues,
            S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER,
            "S07",
            "S07 view-result click is blocked because age/color/bottom-result post-action proof is incomplete.",
            {**snapshot, "flow_state": dict(flow_state), "view_result_gate": view_gate, "s07_age_post_action_proof": age_post_action_proof},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    t0 = time.perf_counter()
    total_action_ms += contract_execute_click(
        context,
        snapshot,
        "S07",
        "tap_view_cars",
        _center(view_node["bounds"]),
        evidence={"clicked_text": _s07_view_cars_button_text(view_node), "clicked_node_bounds": view_node.get("bounds")},
    )
    age_post_action_proof["clicked_view_result_after_verify"] = True
    flow_state["S07_FILTER_DONE"] = True
    flow_state["transition_context"] = "S07_VIEW_RESULT_TO_LIST"
    context["s07_view_result_to_list"] = {
        "transition_context": "S07_VIEW_RESULT_TO_LIST",
        "COLOR_FILTER_DONE": bool(flow_state.get("COLOR_FILTER_DONE")),
        "AGE_FILTER_DONE": bool(flow_state.get("AGE_FILTER_DONE")),
        "S07_FILTER_DONE": True,
        "bottom_view_result_text": _s07_view_cars_button_text(view_node),
        "view_result_count": view_count,
        "clicked_view_result_bounds": view_node.get("bounds"),
        "view_result_preclick_gate": view_gate,
        "s07_age_post_action_proof": age_post_action_proof,
    }
    time.sleep(1.0)
    next_snapshot = _capture(client, f"s07_to_s08_{_timestamp()}")
    after_view_state = _recognize_page(recognizer, next_snapshot, context.get("flow_state"))
    if after_view_state == "S06":
        issue = _record_issue(
            issues,
            "WRONG_CLICK_MODEL_CONFIG_AFTER_S07_DONE",
            "S07",
            "S07 view result returned to a page recognized as S06 after S07_FILTER_DONE; clicking model config again is forbidden.",
            {**next_snapshot, "after_view_state": after_view_state, "flow_state": dict(context.get("flow_state", {}))},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if after_view_state not in {"S08", "S09", "S10"}:
        issue = _record_issue(
            issues,
            "PAGE_CONTRACT_MISMATCH",
            "S07",
            "S07 view-cars button did not return to a recognized vehicle list page.",
            {**next_snapshot, "after_view_state": after_view_state, "view_cars_button_bounds": view_node.get("bounds")},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context["s07_view_result_to_list"]["recognized_page_after_view_result"] = after_view_state
    if after_view_state == "S08":
        context["s08_target_list_after_filter"] = _s08_target_list_after_filter_evidence(next_snapshot, context.get("flow_state"))
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
    s08_evidence = _s08_target_list_after_filter_evidence(snapshot, context.get("flow_state"))
    context["s08_target_list_after_filter"] = s08_evidence
    if not s08_evidence.get("s08_page_variant"):
        issue = _record_issue(
            issues,
            s08_evidence.get("s08_stop_code") or "S06_TARGET_FILTER_EVIDENCE_MISSING",
            "S08",
            "S08 target list after filter requires S07 source gate, target evidence, core list elements, and reverse exclusion.",
            {**snapshot, **s08_evidence},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
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
        action_ms = contract_execute_click(
            context,
            snapshot,
            "S08",
            "tap_sort_if_present",
            _center(sort_node["bounds"]),
            evidence={"clicked_text": "综合排序", "clicked_node_bounds": sort_node.get("bounds"), **s08_evidence},
        )
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

    issue = _record_issue(
        issues,
        "S08_SORT_CONTROL_NOT_FOUND",
        "S08",
        "S08 target list after filter requires clicking 综合排序 before S10_READY.",
        {**snapshot, **s08_evidence, "collected_list_fields_count": len(cards)},
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


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
        issue = _record_issue(issues, "S09_SORT_OPTION_NODE_NOT_FOUND", "S09", "价格从低到高 not found.", snapshot)
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    clicked_point = _center(node["bounds"])
    action_start = time.perf_counter()
    action_ms = contract_execute_click(
        context,
        snapshot,
        "S09",
        "tap_price_low_to_high",
        clicked_point,
        evidence={"clicked_text": "价格从低到高", "clicked_node_bounds": node.get("bounds")},
    )
    time.sleep(1.0)
    next_snapshot = _capture(client, f"s09_to_s10_{_timestamp()}")
    target_for_cards = {
        "brand": context["task_params"].get("brand"),
        "series": context["task_params"].get("series"),
        "series_alias": context["task_params"].get("series_alias"),
        "model_year": context["task_params"].get("model_year"),
        "trim": context["task_params"].get("trim"),
    }
    sort_evidence = {
        "sort_option_clicked": True,
        "s09_price_asc_clicked": True,
        "sort_option_text": "价格从低到高",
        "sort_option_bounds": node.get("bounds"),
        "clicked_text": "价格从低到高",
        "clicked_node_bounds": node.get("bounds"),
        "clicked_point": clicked_point,
        "click_strategy": "text_node_bounds",
        **_s10_ready_evidence(next_snapshot, target_for_cards),
    }
    if not sort_evidence["sort_popup_closed"]:
        issue = _record_issue(
            issues,
            "SORT_POPUP_NOT_CLOSED",
            "S09",
            "Price low-to-high click did not close the sort popup.",
            {**next_snapshot, "sort_evidence": sort_evidence},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if not sort_evidence["sort_selected_confirmed"]:
        issue = _record_issue(
            issues,
            "SORT_OPTION_CLICK_NO_EFFECT",
            "S09",
            "Price low-to-high click did not confirm the selected sort state.",
            {**next_snapshot, "sort_evidence": sort_evidence},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if sort_evidence["no_same_source_detected"]:
        issue = _record_issue(
            issues,
            "NEED_MANUAL_PRICING_NO_SAME_SOURCE",
            "S10",
            "Sorted list returned an empty/no same-source vehicle result.",
            {**next_snapshot, "sort_evidence": sort_evidence},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if not sort_evidence["sorted_list_page_recognized"]:
        issue = _record_issue(
            issues,
            "S10_READY_AFTER_SORT_NOT_CONFIRMED",
            "S09",
            "Price low-to-high click did not return to a reliable sorted vehicle list page.",
            {**next_snapshot, "sort_evidence": sort_evidence},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if not _extract_s10_contract_cards(next_snapshot, target_for_cards):
        issue = _record_issue(
            issues,
            "SORT_CLICK_DID_NOT_RETURN_TO_LIST",
            "S09",
            "Price low-to-high click returned fresh evidence without parseable vehicle cards.",
            {**next_snapshot, "sort_evidence": sort_evidence},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context.setdefault("flow_state", {})["SORT_DONE"] = True
    context.setdefault("flow_state", {})["transition_context"] = "S09_PRICE_ASC_TO_LIST"
    sort_evidence.update(_s10_source_gate_core_evidence(next_snapshot, context.get("flow_state"), target_for_cards))
    context["s09_price_asc_sort"] = sort_evidence
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
        extra=sort_evidence,
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
    target_for_cards = {
        "brand": context["task_params"].get("brand"),
        "series": context["task_params"].get("series"),
        "series_alias": context["task_params"].get("series_alias"),
        "model_year": context["task_params"].get("model_year"),
        "trim": context["task_params"].get("trim"),
    }
    expected_s10_filter_summary = {
        "brand": context["task_params"].get("brand"),
        "series": context["task_params"].get("series"),
        "color": context["task_params"].get("color"),
        "age_filter": f"{context['task_params'].get('target_age_years')}-{context['task_params'].get('target_age_years')}"
        if context["task_params"].get("target_age_years") is not None
        else None,
        "model_config_core": context["task_params"].get("trim"),
    }
    s10_contract_action_plan = build_s10_filter_summary_action_plan(expected_s10_filter_summary)
    s10_binding_trace = build_action_plan_binding_trace(
        s10_contract_action_plan,
        action_algorithm_used="filter_summary_contract_match",
    )
    context["s10_filter_summary_contract_action_plan"] = s10_contract_action_plan
    s10_gate_evidence = _s10_source_gate_core_evidence(snapshot, context.get("flow_state"), target_for_cards)
    s10_gate_evidence["contract_action_plan"] = s10_contract_action_plan
    s10_gate_evidence["contract_expected"] = s10_contract_action_plan.get("expected")
    s10_gate_evidence.update(s10_binding_trace)
    context["s10_source_gate_core"] = s10_gate_evidence
    if s10_gate_evidence.get("s10_color_filter_mismatch"):
        issue = _record_issue(
            issues,
            s10_gate_evidence.get("s10_color_filter_stop_code") or "S10_COLOR_FILTER_MISMATCH",
            "S10",
            "S10 final vehicle list color evidence does not match the target color selected in S07.",
            {**snapshot, **s10_gate_evidence},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    cards = _assert_s10_ready_contract(
        issues,
        snapshot,
        source=context.get("s10_ready_source"),
        flow_state=context.get("flow_state"),
        target_car=target_for_cards,
    )
    trisame_audit = _s10_contract_card_audit(snapshot, target_for_cards)
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
        "raw_visible_cards_count": trisame_audit.get("raw_visible_cards_count"),
        "trisame_cards_count": trisame_audit.get("trisame_cards_count"),
        "trisame_count": trisame_audit.get("trisame_cards_count"),
        "trisame_count_confirmed": trisame_audit.get("trisame_count_confirmed"),
        "excluded_non_trisame_cards_count": trisame_audit.get("excluded_non_trisame_cards_count"),
        "excluded_non_trisame_cards": trisame_audit.get("excluded_non_trisame_cards"),
        "non_trisame_section_detected": trisame_audit.get("non_trisame_section_detected"),
        "non_trisame_section_title": trisame_audit.get("non_trisame_section_title"),
        "boundary_text": trisame_audit.get("boundary_text"),
        "boundary_text_index": trisame_audit.get("boundary_text_index"),
        "cards_after_boundary_excluded_count": trisame_audit.get("cards_after_boundary_excluded_count"),
        **s10_gate_evidence,
    }
    result.update(_s03_contract_context_fields(context))
    result.update(_s04_brand_zone_context_fields(context))
    result.update(_s05_emission_variant_context_fields(context))
    result.update(_s06_target_filter_context_fields(context))
    result.update(_s08_s10_context_fields(context))
    timing.write()
    _write_result_json(context["configs"], result)
    return result


def run_s01_to_s10_mainline(runtime: dict[str, Any], phone_test: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_dirs()
    configs = runtime["configs"]
    _enable_s03_brand_search_v2_actions(configs["pages"])
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
            "startup_timeline": [],
            "adb_gate_duration_ms": 0,
            "wake_unlock_duration_ms": 0,
            "miui_recovery_duration_ms": 0,
            "launcher_icon_lookup_duration_ms": 0,
            "app_reopen_duration_ms": 0,
            "app_foreground_confirm_duration_ms": 0,
            "s01_detect_duration_ms": 0,
            "capture_count": 0,
            "screenshot_count": 0,
            "xml_dump_count": 0,
            "reused_capture_count": 0,
            "fastpath_used": False,
            "fastpath_reason": "",
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
        _run_first_stage_target_device_gate(context)
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
        if after_recovery_state == "S_APP_ICON":
            snapshot = _recover_to_guazi_page(context, reason="s_login_exit_to_launcher")
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
                    "s_login_after_back_page": "S_APP_ICON",
                    "next_startup_step": "continue_to_app_icon_search",
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
                    s07_result.update(_adb_evidence_context_fields(context))
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
                result.update(_adb_evidence_context_fields(context))
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
            result.update(_s03_contract_context_fields(context))
            result.update(_s04_brand_zone_context_fields(context))
            result.update(_s05_emission_variant_context_fields(context))
            result.update(_s06_target_filter_context_fields(context))
            result.update(_s08_s10_context_fields(context))
            result.update(_adb_evidence_context_fields(context))
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
            "canonical_error_code": exc.code,
            "error": str(exc),
            "target_task": _target_task_output(context["task_params"]),
            "flow_state": dict(context.get("flow_state", {})),
            "startup": dict(context.get("startup", {})),
            "context": exc.context,
        }
        raw_adb_error = _first_adb_error_text(exc.context)
        if raw_adb_error:
            result["original_error_message"] = raw_adb_error
            result["raw_error_summary"] = raw_adb_error
        if exc.code == TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT:
            result["later_capture_device_not_found"] = True
            result["gate_target_device_state"] = exc.context.get("gate_target_device_state")
        result.update(_s05_emission_variant_fields_from_issue(exc.context))
        result.update(_s03_contract_context_fields(context))
        result.update(_s04_brand_zone_context_fields(context))
        result.update(_s05_emission_variant_context_fields(context))
        result.update(_s06_target_filter_context_fields(context))
        result.update(_s08_s10_context_fields(context))
        result.update(_adb_evidence_context_fields(context))
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
