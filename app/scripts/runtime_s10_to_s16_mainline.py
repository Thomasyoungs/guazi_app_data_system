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
import os
import re
import sys
import time
import traceback
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
from guazi_app_data_system.page_contract_execution_plan import (
    build_action_plan_binding_trace,
    build_s11_report_entry_action_plan,
)
from guazi_app_data_system.page_recognition import PageRecognizer
from guazi_app_data_system.page_state_machine import PageStateMachine
from guazi_app_data_system.pricing import (
    COMPETITION_COEFFICIENT_DOC,
    COMPETITION_COEFFICIENT_VERSION,
    PRICING_RULE_DOC,
    PRICING_RULE_VERSION,
    REFERENCE_SELECTION_RULE,
    SCORING_RULE_DOC,
    SCORING_RULE_VERSION,
    ScoreResult,
    calculate_pricing,
    score_reference,
    score_target,
    select_reference,
)
from guazi_app_data_system.reference_early_exit import (
    EARLY_EXIT_RULE_ID,
    calculate_reference_score_upper_bound_for_early_exit,
    evaluate_reference_early_exit_max_possible_score,
)
from guazi_app_data_system.runtime_contract_guard import (
    guard_pricing_rule,
    guard_reference_selection_rule,
    guard_s13_s14_collection,
    guard_scoring_rule,
)
from guazi_app_data_system.transient_popup_handler import (
    GUAZI_PUSH_NOTIFICATION_POPUP,
    GUAZI_PUSH_POPUP_CLOSE_FAILED,
    GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND,
    close_guazi_push_popup_from_snapshot,
)
from s10s16_clean import field_extractors as clean_field_extractors
from s10s16_clean import page_proofs as clean_page_proofs
from s10s16_clean import transition_gates as clean_transition_gates


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

S14_NON_SCORING_DAMAGE = "NON_SCORING_S14_DAMAGE"
S14_NON_SCORING_DAMAGE_TYPES = {"变形", "维修痕迹", "修复痕迹", "异常细节"}
S14_DAMAGE_PRIORITY = {"喷漆": 1, "钣金": 2, "更换": 3}
S14_SPECIAL_STRUCTURE_RISK_PARTS = {"ABC柱", "水箱框架"}
S14_CONTRACT_FULLY_COLLECTED = "FULLY_COLLECTED"
S14_CONTRACT_DEGRADED_RECORDABLE = "DEGRADED_RECORDABLE"
S14_CONTRACT_NEEDS_REVIEW_CONTINUE = "NEEDS_REVIEW_CONTINUE"
S14_CONTRACT_UNSAFE_FAIL = "UNSAFE_FAIL"
S14_DETAIL_TEXT_UNBOUND_DEGRADED = "S14_DETAIL_TEXT_UNBOUND_DEGRADED"
S14_STALE_FIRST_LINE_DISCARDED = "S14_STALE_FIRST_LINE_DISCARDED"
S14_CONTRACT_DEGRADED_NEEDS_REVIEW = "S14_CONTRACT_DEGRADED_NEEDS_REVIEW"
S14_DEGRADED_COLLECTION_THRESHOLD_EXCEEDED = "S14_DEGRADED_COLLECTION_THRESHOLD_EXCEEDED"
S14_DETAIL_POPUP_CLOSE_UNSAFE_OR_FAILED = "S14_DETAIL_POPUP_CLOSE_UNSAFE_OR_FAILED"
S14_DEGRADED_ITEM_REVIEW_THRESHOLD = 2
S14_CONTINUE_UNCOLLECTED_CONDITION = "S14_CONTINUE_UNCOLLECTED_CONDITION"
CONTINUE_CURRENT_REFERENCE_S14 = "CONTINUE_CURRENT_REFERENCE_S14"
S14_COLLECTION_INCOMPLETE_UNRECOVERABLE = "S14_COLLECTION_INCOMPLETE_UNRECOVERABLE"
S14_CONTINUE_CURRENT_REFERENCE_MAX_NO_CHANGE_SWIPES = 3
S12_CLAIM_FIELDS_NOT_READABLE = "S12_CLAIM_FIELDS_NOT_READABLE"
S12_REPORT_CLAIM_COUNT_MAX_AMOUNT_FIELD_MISSING = "S12_REPORT_CLAIM_COUNT_MAX_AMOUNT_FIELD_MISSING"
S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE = "S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE"
S12_CLAIM_RECOVERY_EXTENT_INVALID = "S12_CLAIM_RECOVERY_EXTENT_INVALID"
S12_CLAIM_RECOVERY_BOUNDS_INVALID = "S12_CLAIM_RECOVERY_BOUNDS_INVALID"
S12_CLAIM_RECOVERY_CANDIDATE_MALFORMED = "S12_CLAIM_RECOVERY_CANDIDATE_MALFORMED"
S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE = "S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE"
S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW = (
    "S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW"
)
S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW = "S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW"
REFERENCE_EXCLUDED_S12_CLAIM_FIELDS_MISSING = "REFERENCE_EXCLUDED_S12_CLAIM_FIELDS_MISSING"
S12_BODY_APPEARANCE_SAFE_FALLBACK_CLICK = "S12_BODY_APPEARANCE_SAFE_FALLBACK_CLICK"
S12_BODY_APPEARANCE_SAFE_FALLBACK_CLICK_VERIFY_FAILED = (
    "S12_BODY_APPEARANCE_SAFE_FALLBACK_CLICK_VERIFY_FAILED"
)
S12_TO_S13_REGION_PROOF_NOT_CONFIRMED = "S12_TO_S13_REGION_PROOF_NOT_CONFIRMED"
S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED = "S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED"
S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED_AFTER_S12_BODY_REACHED = (
    "S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED_AFTER_S12_BODY_REACHED"
)
S13_REGION_HEADERS_NOT_FOUND = "S13_REGION_HEADERS_NOT_FOUND"
S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED = "S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED"
S13_REGION_HISTORY_COUNT_BINDING_FAILED = "S13_REGION_HISTORY_COUNT_BINDING_FAILED"
S12_CLAIM_FIELD_NEEDS_REVIEW_CODES = {
    S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW,
    S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW,
    S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE,
}
S10_TO_S11_PRE_DUMP_STRATEGY_DEFAULT = "visual_stabilize_before_dump"
S10_TO_S11_PRE_DUMP_INTERVAL_S = 0.4
S10_TO_S11_PRE_DUMP_MAX_WAIT_BEFORE_OPTIMIZATION_S = 2.0
S10_TO_S11_PRE_DUMP_MAX_WAIT_S = 1.2
S10_TO_S11_PRE_DUMP_STABLE_ROUNDS = 2
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
    "左前大灯": {"左前大灯", "左大灯"},
    "右前大灯": {"右前大灯", "右大灯"},
    "大灯": {"大灯", "前大灯", "灯具"},
}
S14_SPECIAL_PART_ALIASES = {
    "ABC柱": {"A柱", "B柱", "C柱", "ABC柱", "A 柱", "B 柱", "C 柱", "ABC 柱"},
    "水箱框架": {"水箱框架", "水箱架", "前水箱框架"},
}
S13_HISTORY_REPAIR_ENTRY_ALIASES = {
    "底边梁",
    "下边梁",
    "门槛",
    "侧底边梁",
    "左侧底边梁",
    "右侧底边梁",
    "左下边梁",
    "右下边梁",
    "左门槛",
    "右门槛",
    "门槛梁",
    "边梁",
    "车顶边梁",
    "底大边",
    "下坎",
    "门下坎",
}
S14_NON_STRUCTURE_SURFACE_SEMANTICS = {"覆盖面", "饰板", "装饰板", "内饰板", "盖板", "外饰", "表面", "漆面", "外侧漆面"}
S13_REGION_ORDER = ["驾驶侧", "车尾", "副驾驶", "车头"]
S13_FOUR_REGION_LOOP_GUARD_TRIGGERED = "S13_FOUR_REGION_LOOP_GUARD_TRIGGERED"
S13_ALL_ZERO_RETURN_TO_S10_FOR_S15 = "S13_ALL_ZERO_RETURN_TO_S10_FOR_S15"
REFERENCE_PHYSICAL_UI_TRANSITION_PROOF_VERSION = "V1.49_REFERENCE_PHYSICAL_UI_TRANSITION_PROOF"
REFERENCE_HISTORY_PHYSICAL_PROOF_MISSING = "REFERENCE_HISTORY_ENTRY_BLOCKED_BY_MISSING_PHYSICAL_UI_PROOF"
REFERENCE_HISTORY_PHYSICAL_SIGNATURE_REUSED = "REFERENCE_HISTORY_ENTRY_BLOCKED_BY_REUSED_PHYSICAL_PAGE_SIGNATURE"
S13_RETURN_TO_S10_ACTION_NOT_EXECUTED = "S13_RETURN_TO_S10_ACTION_NOT_EXECUTED"
S13_RETURN_ACTION_EXECUTED_BUT_STILL_ON_S13 = "S13_RETURN_ACTION_EXECUTED_BUT_STILL_ON_S13"
S13_RETURN_ACTION_LANDED_ON_NON_S10_PAGE = "S13_RETURN_ACTION_LANDED_ON_NON_S10_PAGE"
S13_RETURN_TO_S10_PROOF_STALE_OR_MISSING = "S13_RETURN_TO_S10_PROOF_STALE_OR_MISSING"
REFERENCE_DESTINATION_IDENTITY_NOT_MATCHED = "REFERENCE_DESTINATION_IDENTITY_NOT_MATCHED"
ALL_REFERENCES_EXHAUSTED_BLOCKED_BY_MISSING_PHYSICAL_EVIDENCE = "ALL_REFERENCES_EXHAUSTED_BLOCKED_BY_MISSING_PHYSICAL_EVIDENCE"
PAGE_CONTRACT_IS_ONLY_EXECUTION_STANDARD = True
SCRIPT_PAGE_CONTRACT_ACTIONS: dict[str, set[str]] = {
    "S10": {
        "collect_list_whitelist_fields",
        "tap_next_car_by_price_order",
        "S10_ONLY_ALLOWED_ACTION_CLICK_REFERENCE_CARD_TITLE",
        "scroll_to_complete_reference_card",
    },
    "S11": {
        "collect_transfer_count",
        "scroll_to_report",
        "tap_full_report",
        "S11_ONLY_ALLOWED_ACTION_SCROLL_TO_REPORT",
        "S11_ONLY_ALLOWED_ACTION_CLICK_FULL_REPORT",
        "S11_OFFICIAL_REPORT_ENTRY_MISSING_EXCLUDE_REFERENCE",
        "S11_ONLY_ALLOWED_ACTION_RETURN_TO_RELIABLE_S10_AFTER_REPORT_MISSING",
    },
    "S12": {
        "collect_claim_count_and_max_amount",
        "tap_body_appearance",
        "scroll_to_body_appearance",
        "S12_ONLY_ALLOWED_ACTION_CLICK_BODY_APPEARANCE",
    },
    "S13": {
        "collect_repair_counts",
        "tap_repair_item_if_nonzero",
        "return_to_s10_if_all_zero",
        "tap_region_tab",
        "scroll_history_repair_table",
        "reposition_repair_item_entry",
        "S13_ONLY_ALLOWED_ACTION_CLICK_REPAIR_ITEM",
    },
    "S14": {
        "s14_image_horizontal_swipe",
        "S14_ONLY_ALLOWED_ACTION_IMAGE_HORIZONTAL_SWIPE",
        "return_to_s10_after_collect_done",
    },
}
S13_REPAIR_CLICK_FORBIDDEN_TEXTS = {
    "鍟嗗姝ｅ湪鐩存挱",
    "杩涘叆鎴块棿瑙傜湅杞﹀熬缁嗚妭",
    "杩涘叆鎴块棿",
    "瀹炶溅璁茶В",
    "椹笂涓烘偍瀹炶溅璁茶В",
    "绛夊緟鐪嬭溅",
    "婕旂ず娓呭崟",
    "甯︾湅杞﹁締",
    "鑱旂郴鍗栧",
    "璁蹭环",
    "鏌ョ湅鎶ヤ环",
    "寰俊鍜ㄨ",
    "鐢佃瘽",
    "鍜ㄨ",
    "杩斿洖椤堕儴",
}
S13_LIVE_ROOM_SIGNALS = {
    "绛夊緟鐪嬭溅",
    "椹笂涓烘偍瀹炶溅璁茶В",
    "鍟嗗璁茶В杞﹀喌",
    "婕旂ず娓呭崟",
    "甯︾湅杞﹁締",
    "淇濊瘉鍟嗗100%璁茶В",
    "鐡滃瓙璁よ瘉鍟嗗",
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
        slow_action_threshold_seconds = 2.0
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
            "slow_action_threshold_seconds": slow_action_threshold_seconds,
            "duration_seconds": duration_seconds,
            "interval_since_previous_action_seconds": previous_interval,
            "threshold_exceeded": duration_seconds >= slow_action_threshold_seconds,
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


AGGREGATE_TIMING_STEPS = {
    "S10_TO_S11",
    "S10_TO_S11_STABLE",
    "S12_TO_S13_BODY_APPEARANCE",
    "S14_COLLECT",
    "S14_RETURN_TO_S10",
}


def _parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(raw or ""))
    if not match:
        return None
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def _is_valid_extent(extent: Any) -> bool:
    return clean_field_extractors.is_valid_extent(extent)


def _coerce_extent(extent: Any) -> tuple[int, int, int, int] | None:
    return clean_field_extractors.coerce_extent(extent)


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


def _s10_to_s11_pre_dump_strategy() -> str:
    strategy = os.environ.get(
        "S10_TO_S11_PRE_DUMP_STRATEGY",
        S10_TO_S11_PRE_DUMP_STRATEGY_DEFAULT,
    ).strip()
    if strategy not in {"direct_dump", "visual_stabilize_before_dump"}:
        return S10_TO_S11_PRE_DUMP_STRATEGY_DEFAULT
    return strategy


def _capture_s10_to_s11_visual_state(client: AdbClient, stem: str) -> dict[str, Any]:
    screenshot_path = project_path("artifacts", "screenshots", f"{stem}.png")
    started = time.perf_counter()
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
    window_dump = window_result.stdout if window_result.success else ""
    activity_dump = activity_result.stdout if activity_result.success else ""
    return {
        "power": power,
        "power_ms": power_ms,
        "window_dump": window_dump,
        "activity_dump": activity_dump,
        "dumpsys_ms": dumpsys_ms,
        "screenshot_path": screenshot_path,
        "screenshot_success": bool(screenshot_result and screenshot_result.success),
        "screenshot_ms": screenshot_ms,
        "screenshot_sha256": _sha256_file(str(screenshot_path)),
        "foreground_package": _extract_foreground_package(window_dump, activity_dump),
        "focused_window": _extract_focused_window(window_dump),
        "resumed_activity": _extract_resumed_activity(activity_dump),
        "total_ms": int((time.perf_counter() - started) * 1000),
    }


def _pre_dump_stabilize_for_s10_to_s11(
    client: AdbClient,
    before_snapshot: dict[str, Any],
    stem_prefix: str,
    *,
    interval_s: float = S10_TO_S11_PRE_DUMP_INTERVAL_S,
    max_wait_s: float = S10_TO_S11_PRE_DUMP_MAX_WAIT_S,
    stable_rounds_required: int = S10_TO_S11_PRE_DUMP_STABLE_ROUNDS,
) -> dict[str, Any]:
    strategy = _s10_to_s11_pre_dump_strategy()
    if strategy == "direct_dump":
        return {
            "strategy": strategy,
            "enabled": False,
            "rounds": [],
            "round_count": 0,
            "total_ms": 0,
            "visual_stable": False,
            "dump_started_after_visual_stable": False,
            "dump_started_after_max_wait": False,
            "final_state": None,
            "max_wait_ms": int(max_wait_s * 1000),
            "pre_optimization_max_wait_ms": int(S10_TO_S11_PRE_DUMP_MAX_WAIT_BEFORE_OPTIMIZATION_S * 1000),
            "interval_ms": int(interval_s * 1000),
            "stable_rounds_required": stable_rounds_required,
        }

    started = time.perf_counter()
    before_hash = _sha256_file(before_snapshot.get("screenshot_path"))
    previous_hash = before_hash
    previous_focused_window = str(before_snapshot.get("focused_window") or "")
    previous_foreground_package = str(before_snapshot.get("foreground_package") or "")
    stable_observation_count = 0
    rounds: list[dict[str, Any]] = []
    final_state: dict[str, Any] | None = None
    visual_stable = False
    dump_started_after_max_wait = False

    while True:
        elapsed_s = time.perf_counter() - started
        if elapsed_s >= max_wait_s and rounds:
            dump_started_after_max_wait = True
            break
        sleep_s = min(interval_s, max(0.0, max_wait_s - elapsed_s))
        if sleep_s > 0:
            time.sleep(sleep_s)
        state = _capture_s10_to_s11_visual_state(
            client,
            f"{stem_prefix}_pre_dump_{len(rounds) + 1}_{_timestamp()}",
        )
        final_state = state
        screenshot_hash = str(state.get("screenshot_sha256") or "")
        focused_window = str(state.get("focused_window") or "")
        foreground_package = str(state.get("foreground_package") or "")
        screenshot_changed_from_s10 = bool(before_hash and screenshot_hash and screenshot_hash != before_hash)
        same_as_previous = bool(
            rounds
            and screenshot_hash
            and screenshot_hash == previous_hash
            and focused_window == previous_focused_window
            and foreground_package == previous_foreground_package
        )
        if same_as_previous:
            stable_observation_count += 1
        else:
            stable_observation_count = 1 if screenshot_hash else 0
        visual_stable = stable_observation_count >= stable_rounds_required
        round_record = {
            "round_index": len(rounds) + 1,
            "screenshot_path": str(state.get("screenshot_path") or ""),
            "screenshot_ms": int(state.get("screenshot_ms") or 0),
            "dumpsys_ms": int(state.get("dumpsys_ms") or 0),
            "focused_window": focused_window,
            "foreground_package": foreground_package,
            "resumed_activity": str(state.get("resumed_activity") or ""),
            "screenshot_changed_from_s10": screenshot_changed_from_s10,
            "same_as_previous_round": same_as_previous,
            "stable_observation_count": stable_observation_count,
            "visual_stable": visual_stable,
        }
        rounds.append(round_record)
        previous_hash = screenshot_hash
        previous_focused_window = focused_window
        previous_foreground_package = foreground_package
        if visual_stable:
            break
        if time.perf_counter() - started >= max_wait_s:
            dump_started_after_max_wait = True
            break

    total_ms = int((time.perf_counter() - started) * 1000)
    return {
        "strategy": strategy,
        "enabled": True,
        "rounds": rounds,
        "round_count": len(rounds),
        "total_ms": total_ms,
        "visual_stable": visual_stable,
        "dump_started_after_visual_stable": visual_stable,
        "dump_started_after_max_wait": bool(dump_started_after_max_wait and not visual_stable),
        "final_state": final_state,
        "max_wait_ms": int(max_wait_s * 1000),
        "pre_optimization_max_wait_ms": int(S10_TO_S11_PRE_DUMP_MAX_WAIT_BEFORE_OPTIMIZATION_S * 1000),
        "interval_ms": int(interval_s * 1000),
        "stable_rounds_required": stable_rounds_required,
    }


def _dump_ui_xml_for_s10_to_s11(client: AdbClient, *, compressed: bool) -> dict[str, Any]:
    remote_name = "window_s10_to_s11_compressed.xml" if compressed else "window_s10_to_s11_full.xml"
    remote_path = f"/sdcard/{remote_name}"
    args = ["shell", "uiautomator", "dump"]
    if compressed:
        args.append("--compressed")
    args.append(remote_path)
    started = time.perf_counter()
    dump_result = client.run(args, timeout=20)
    cat_result = client.run(["exec-out", "cat", remote_path], timeout=20)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    xml_text = cat_result.stdout or "" if cat_result.success else ""
    return {
        "xml_text": xml_text,
        "dump_ms": elapsed_ms,
        "dump_rc": dump_result.returncode,
        "dump_stderr": dump_result.stderr,
        "cat_rc": cat_result.returncode,
        "cat_stderr": cat_result.stderr,
        "mode": "compressed" if compressed else "full",
        "command": "uiautomator dump --compressed" if compressed else "uiautomator dump",
    }


def _snapshot_from_s10_to_s11_xml(
    *,
    power: dict[str, Any],
    window_dump: str,
    activity_dump: str,
    screenshot_path: Path,
    screenshot_success: bool,
    xml_text: str,
    stem: str,
    capture_metrics: dict[str, int],
    xml_mode: str,
) -> dict[str, Any]:
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
        "fresh_screenshot": str(screenshot_path) if screenshot_success else None,
        "screenshot_is_black": _is_probably_black_screenshot(screenshot_path) if screenshot_success else False,
        "capture_metrics": capture_metrics,
        "s10_to_s11_xml_mode": xml_mode,
    }
    snapshot["screenshot_path"] = str(screenshot_path)
    xml_path = project_path("artifacts", "debug", f"{stem}_{xml_mode}.xml")
    xml_path.write_text(xml_text, encoding="utf-8")
    snapshot["xml_path"] = str(xml_path)
    snapshot["visible_texts"] = _visible_texts(xml_text)
    snapshot["visible_blob"] = "".join(snapshot["visible_texts"])
    snapshot["nodes"] = _parse_nodes(xml_text)
    return snapshot


def _bounds_list(bounds: tuple[int, int, int, int] | None) -> list[int] | None:
    return list(bounds) if bounds else None


def _bounds_area(bounds: tuple[int, int, int, int] | None) -> int:
    if not bounds:
        return 0
    return max(bounds[2] - bounds[0], 0) * max(bounds[3] - bounds[1], 0)


def _bounds_union(bounds_items: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    if not bounds_items:
        return None
    return (
        min(item[0] for item in bounds_items),
        min(item[1] for item in bounds_items),
        max(item[2] for item in bounds_items),
        max(item[3] for item in bounds_items),
    )


def _compact_for_s11(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _s11_screen_bounds(snapshot: dict[str, Any]) -> tuple[int, int, int, int] | None:
    extent = _visible_bounds_extent(snapshot)
    if extent is not None:
        return extent[0]
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    valid = [node.get("bounds") for node in nodes if _valid_bounds(node.get("bounds"))]
    if not valid:
        return None
    return _bounds_union(valid)  # type: ignore[arg-type]


def _s11_label(node: dict[str, Any]) -> str:
    for item in [node.get("text"), node.get("content_desc"), *(node.get("labels") or [])]:
        value = str(item or "").strip()
        if value:
            return value
    return ""


def _s11_top_vehicle_image_band(
    nodes: list[dict[str, Any]],
    screen: tuple[int, int, int, int],
) -> dict[str, Any]:
    sx1, sy1, sx2, sy2 = screen
    width = max(sx2 - sx1, 1)
    height = max(sy2 - sy1, 1)
    screen_area = width * height
    candidates: list[dict[str, Any]] = []
    for node in nodes:
        bounds = node.get("bounds")
        if not _valid_bounds(bounds):
            continue
        x1, y1, x2, y2 = bounds
        node_width = x2 - x1
        node_height = y2 - y1
        area = _bounds_area(bounds)
        if area >= int(screen_area * 0.88):
            continue
        if y1 > sy1 + int(height / 3):
            continue
        if y2 < sy1 + int(height * 0.08):
            continue
        if node_width < int(width * 0.60) or node_height < int(height * 0.15):
            continue
        label = _compact_for_s11(_s11_label(node))
        class_name = str(node.get("class_name") or "")
        image_like = any(token in class_name for token in ["Image", "View", "HorizontalScrollView", "ViewGroup"])
        text_light = len(label) <= 8
        if not image_like and not text_light:
            continue
        candidates.append(
            {
                "bounds": bounds,
                "class_name": class_name,
                "label_length": len(label),
                "area": area,
                "reason": "large upper image/carousel-like container",
            }
        )
    if not candidates:
        return {"hit": False, "reason": "no_large_upper_image_band_candidate"}
    selected = sorted(candidates, key=lambda item: item["area"], reverse=True)[0]
    return {
        "hit": True,
        "bounds": _bounds_list(selected["bounds"]),
        "reason": selected["reason"],
        "candidate_count": len(candidates),
        "class_name": selected.get("class_name"),
    }


def _s11_top_image_only_evidence(
    snapshot: dict[str, Any],
    *,
    transition_context: str,
    page_changed_after_click: bool,
) -> dict[str, Any]:
    nodes = [
        node
        for node in (snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or "")))
        if _valid_bounds(node.get("bounds"))
    ]
    screen = _s11_screen_bounds(snapshot)
    visible_blob = str(snapshot.get("visible_blob") or "")
    passive_debug_signals = []
    for token in [
        "检测报告",
        "车源号",
        "过户",
        "查看完整报告",
        "万公里",
        "城市",
        "价格从低到高",
        "电话",
        "收藏",
        "咨询",
        "降价",
        "讲价",
        "查看报价",
    ]:
        if token in visible_blob:
            passive_debug_signals.append(token)
    evidence: dict[str, Any] = {
        "recognized_by": "S11_TOP_ONE_THIRD_IMAGE_ONLY_STANDARD",
        "transition_context": transition_context,
        "transition_context_matched": transition_context == "S10_TO_S11",
        "page_changed_after_click": bool(page_changed_after_click),
        "passive_debug_signals": passive_debug_signals,
        "passive_debug_signals_used_for_judgement": False,
        "recognition_formula": "transition_context == S10_TO_S11 AND page_changed_after_click == true AND top_one_third_vehicle_image_area == true",
    }
    if screen is None:
        evidence.update(
            {
                "recognized": False,
                "top_one_third_vehicle_image_area": False,
                "top_image_candidate_bounds": None,
                "top_image_missing_reason": "no_xml_bounds",
            }
        )
        return evidence
    top = _s11_top_vehicle_image_band(nodes, screen)
    top_hit = bool(top.get("hit"))
    recognized = bool(transition_context == "S10_TO_S11" and page_changed_after_click and top_hit)
    missing_reasons = []
    if transition_context != "S10_TO_S11":
        missing_reasons.append("transition_context_not_s10_to_s11")
    if not page_changed_after_click:
        missing_reasons.append("page_not_changed_after_click")
    if not top_hit:
        missing_reasons.append(str(top.get("reason") or "top_one_third_vehicle_image_area_missing"))
    evidence.update(
        {
            "recognized": recognized,
            "top_one_third_vehicle_image_area": top_hit,
            "top_vehicle_image_band": top_hit,
            "top_image_candidate_bounds": top.get("bounds"),
            "top_vehicle_image_band_bounds": top.get("bounds"),
            "top_image_candidate_count": top.get("candidate_count", 0),
            "top_image_reason": top.get("reason"),
            "top_image_missing_reason": ";".join(missing_reasons) if missing_reasons else "",
        }
    )
    return evidence


def _capture_s10_to_s11_fast_xml(
    client: AdbClient,
    recognizer: PageRecognizer,
    stem: str,
    *,
    pre_dump_state: dict[str, Any] | None = None,
    before_xml_sha256: str = "",
    before_screenshot_sha256: str = "",
) -> dict[str, Any]:
    capture_started = time.perf_counter()
    state = pre_dump_state or {}
    screenshot_result = None
    if state:
        power = state.get("power") or {}
        power_ms = int(state.get("power_ms") or 0)
        dumpsys_ms = int(state.get("dumpsys_ms") or 0)
        screenshot_path = Path(state.get("screenshot_path"))
        screenshot_success = bool(state.get("screenshot_success"))
        screenshot_ms = int(state.get("screenshot_ms") or 0)
        window_dump = str(state.get("window_dump") or "")
        activity_dump = str(state.get("activity_dump") or "")
        screenshot_reused_from_pre_dump = True
    else:
        screenshot_path = project_path("artifacts", "screenshots", f"{stem}.png")
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
        window_dump = window_result.stdout if window_result.success else ""
        activity_dump = activity_result.stdout if activity_result.success else ""
        screenshot_success = bool(screenshot_result and screenshot_result.success)
        screenshot_reused_from_pre_dump = False

    compressed = _dump_ui_xml_for_s10_to_s11(client, compressed=True)
    compressed_xml_text = str(compressed.get("xml_text") or "")
    screenshot_changed_after_click = bool(
        before_screenshot_sha256
        and _sha256_file(screenshot_path)
        and _sha256_file(screenshot_path) != before_screenshot_sha256
    )
    compressed_xml_changed_after_click = bool(
        before_xml_sha256
        and compressed_xml_text
        and _sha256_text(compressed_xml_text) != before_xml_sha256
    )
    compressed_page_changed_after_click = bool(screenshot_changed_after_click or compressed_xml_changed_after_click)
    compressed_snapshot = _snapshot_from_s10_to_s11_xml(
        power=power,
        window_dump=window_dump,
        activity_dump=activity_dump,
        screenshot_path=screenshot_path,
        screenshot_success=screenshot_success,
        xml_text=compressed_xml_text,
        stem=stem,
        capture_metrics={
            "power_ms": power_ms,
            "dumpsys_ms": dumpsys_ms,
            "screenshot_ms": screenshot_ms,
            "xml_ms": int(compressed.get("dump_ms") or 0),
            "capture_total_ms": int((time.perf_counter() - capture_started) * 1000),
            "screenshot_reused_from_pre_dump": int(screenshot_reused_from_pre_dump),
        },
        xml_mode="compressed",
    )
    compressed_snapshot["transition_context"] = "S10_TO_S11"
    compressed_snapshot["page_changed_after_click"] = compressed_page_changed_after_click
    compressed_recognized = _recognize_mainline_page(
        recognizer,
        compressed_snapshot,
        s10_to_s11_context=True,
        page_changed_after_click=compressed_page_changed_after_click,
    )
    compressed_snapshot["recognized_page"] = compressed_recognized
    compressed_snapshot["visible_text_digest"] = list(compressed_snapshot.get("visible_texts", []))[:40]
    compressed_s11_contract_hit = compressed_recognized == "S11"
    compressed_size = len(str(compressed.get("xml_text") or "").encode("utf-8", errors="ignore"))
    compressed_node_count = str(compressed.get("xml_text") or "").count("<node")
    if compressed_s11_contract_hit:
        compressed_snapshot["capture_metrics"]["capture_total_ms"] = int((time.perf_counter() - capture_started) * 1000)
        return {
            "snapshot": compressed_snapshot,
            "mode": "compressed",
            "recognized_page": compressed_recognized,
            "compressed": {
                **compressed,
                "xml_path": compressed_snapshot.get("xml_path"),
                "xml_size": compressed_size,
                "node_count": compressed_node_count,
                "recognized_page": compressed_recognized,
                "recognized_by": compressed_snapshot.get("recognized_by"),
                "s11_top_image_only_evidence": compressed_snapshot.get("s11_top_image_only_evidence"),
                "page_changed_after_click": compressed_snapshot.get("page_changed_after_click"),
                "s11_contract_hit": True,
            },
            "full": {},
            "fallback_reason": "",
            "screenshot_ms": screenshot_ms,
            "dumpsys_ms": dumpsys_ms,
            "capture_total_ms": int((time.perf_counter() - capture_started) * 1000),
        }

    fallback_reason = "compressed_not_s11"
    if not str(compressed.get("xml_text") or "").strip():
        fallback_reason = "compressed_xml_empty"
    elif compressed.get("dump_rc") not in (0, None) or compressed.get("cat_rc") not in (0, None):
        fallback_reason = "compressed_xml_dump_failed"
    full = _dump_ui_xml_for_s10_to_s11(client, compressed=False)
    full_xml_text = str(full.get("xml_text") or "")
    full_xml_changed_after_click = bool(
        before_xml_sha256
        and full_xml_text
        and _sha256_text(full_xml_text) != before_xml_sha256
    )
    full_page_changed_after_click = bool(screenshot_changed_after_click or full_xml_changed_after_click)
    full_snapshot = _snapshot_from_s10_to_s11_xml(
        power=power,
        window_dump=window_dump,
        activity_dump=activity_dump,
        screenshot_path=screenshot_path,
        screenshot_success=screenshot_success,
        xml_text=full_xml_text,
        stem=stem,
        capture_metrics={
            "power_ms": power_ms,
            "dumpsys_ms": dumpsys_ms,
            "screenshot_ms": screenshot_ms,
            "xml_ms": int(compressed.get("dump_ms") or 0) + int(full.get("dump_ms") or 0),
            "capture_total_ms": int((time.perf_counter() - capture_started) * 1000),
            "screenshot_reused_from_pre_dump": int(screenshot_reused_from_pre_dump),
        },
        xml_mode="full_fallback",
    )
    full_snapshot["transition_context"] = "S10_TO_S11"
    full_snapshot["page_changed_after_click"] = full_page_changed_after_click
    full_recognized = _recognize_mainline_page(
        recognizer,
        full_snapshot,
        s10_to_s11_context=True,
        page_changed_after_click=full_page_changed_after_click,
    )
    full_snapshot["recognized_page"] = full_recognized
    full_snapshot["visible_text_digest"] = list(full_snapshot.get("visible_texts", []))[:40]
    full_size = len(str(full.get("xml_text") or "").encode("utf-8", errors="ignore"))
    full_node_count = str(full.get("xml_text") or "").count("<node")
    full_snapshot["capture_metrics"]["capture_total_ms"] = int((time.perf_counter() - capture_started) * 1000)
    return {
        "snapshot": full_snapshot,
        "mode": "compressed_then_full",
        "recognized_page": full_recognized,
        "compressed": {
            **compressed,
            "xml_path": compressed_snapshot.get("xml_path"),
            "xml_size": compressed_size,
            "node_count": compressed_node_count,
            "recognized_page": compressed_recognized,
            "recognized_by": compressed_snapshot.get("recognized_by"),
            "s11_top_image_only_evidence": compressed_snapshot.get("s11_top_image_only_evidence"),
            "page_changed_after_click": compressed_snapshot.get("page_changed_after_click"),
            "s11_contract_hit": False,
        },
        "full": {
            **full,
            "xml_path": full_snapshot.get("xml_path"),
            "xml_size": full_size,
            "node_count": full_node_count,
            "recognized_page": full_recognized,
            "recognized_by": full_snapshot.get("recognized_by"),
            "s11_top_image_only_evidence": full_snapshot.get("s11_top_image_only_evidence"),
            "page_changed_after_click": full_snapshot.get("page_changed_after_click"),
            "s11_contract_hit": full_recognized == "S11",
        },
        "fallback_reason": fallback_reason,
        "screenshot_ms": screenshot_ms,
        "dumpsys_ms": dumpsys_ms,
        "capture_total_ms": int((time.perf_counter() - capture_started) * 1000),
    }


def _recognize_mainline_page(
    recognizer: PageRecognizer,
    snapshot: dict[str, Any],
    *,
    s10_to_s11_context: bool = False,
    page_changed_after_click: bool | None = None,
    transition_context: str | None = None,
    expected_next_page: str | None = None,
) -> str | None:
    text = str(snapshot.get("visible_blob") or "")
    if not text and str(snapshot.get("xml_package") or "") == "com.android.systemui":
        return "RUNTIME"
    if str(snapshot.get("foreground_package") or "") != GUAZI_PACKAGE and str(snapshot.get("xml_package") or "") != GUAZI_PACKAGE:
        return None
    existing_s12_evidence = snapshot.get("s12_report_page_evidence") or {}
    if isinstance(existing_s12_evidence, dict) and existing_s12_evidence.get("recognized"):
        snapshot["recognized_by"] = existing_s12_evidence.get("recognized_by") or "S12_REPORT_PAGE_IN_S11_TO_S12_CONTEXT"
        return "S12"
    existing_s11_evidence = snapshot.get("s11_top_image_only_evidence") or {}
    if isinstance(existing_s11_evidence, dict) and existing_s11_evidence.get("recognized"):
        snapshot["recognized_by"] = existing_s11_evidence.get("recognized_by") or "S11_TOP_ONE_THIRD_IMAGE_ONLY_STANDARD"
        return "S11"
    if s10_to_s11_context:
        changed = bool(snapshot.get("page_changed_after_click")) if page_changed_after_click is None else bool(page_changed_after_click)
        evidence = _s11_top_image_only_evidence(
            snapshot,
            transition_context="S10_TO_S11",
            page_changed_after_click=changed,
        )
        snapshot["s11_top_image_only_evidence"] = evidence
        if evidence.get("recognized"):
            snapshot["recognized_by"] = evidence.get("recognized_by")
            return "S11"
    if transition_context == "S11_TO_S12" or expected_next_page == "S12":
        evidence = _s12_report_page_evidence(snapshot)
        s14_signals = _s14_candidate_signals(snapshot)
        suppress_s14 = bool(s14_signals)
        snapshot["s12_report_page_evidence"] = evidence
        snapshot["s14_candidate_signals"] = s14_signals
        snapshot["s14_suppressed_by_context"] = suppress_s14
        snapshot["s14_suppression_reason"] = "S11_TO_S12_EXPECTS_S12" if suppress_s14 else ""
        if evidence.get("recognized"):
            snapshot["recognized_by"] = evidence.get("recognized_by")
            return "S12"
        return None
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


def _s12_report_page_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    text = str(snapshot.get("visible_blob") or "")
    signals = {
        "瓜子官方检测报告": "瓜子官方检测报告" in text,
        "理赔次数": "理赔次数" in text,
        "最大金额": "最大金额" in text,
        "保险理赔记录": "保险理赔记录" in text,
        "重大问题排查": "重大问题排查" in text,
        "车身外观": "车身外观" in text,
        "内饰及配置": "内饰及配置" in text or "内饰/配置" in text,
        "历史修复": "历史修复" in text,
        "检测报告页结构": "车源编号" in text or "检测日期" in text or "VIN码" in text,
    }
    matched = [key for key, value in signals.items() if value]
    groups = {
        "official_report_title": signals["瓜子官方检测报告"],
        "claim_count_and_max_amount": signals["理赔次数"] and signals["最大金额"],
        "insurance_claims_and_count": signals["保险理赔记录"] and signals["理赔次数"],
        "problem_body_interior": signals["重大问题排查"] and signals["车身外观"] and signals["内饰及配置"],
        "body_history_report_structure": signals["车身外观"] and signals["历史修复"] and signals["检测报告页结构"],
    }
    recognized = any(groups.values())
    return {
        "recognized": recognized,
        "recognized_by": "S12_REPORT_PAGE_IN_S11_TO_S12_CONTEXT" if recognized else "",
        "s12_candidate": bool(matched),
        "s12_candidate_signals": matched,
        "s12_recognition_groups": groups,
    }


def _s14_candidate_signals(snapshot: dict[str, Any]) -> list[str]:
    text = str(snapshot.get("visible_blob") or "")
    signals: list[str] = []
    for token in ["异常细节", "历史修复", "驾驶侧", "车尾", "副驾驶", "车头"]:
        if token in text:
            signals.append(token)
    for part in S14_ALLOWED_PARTS:
        if part in text:
            signals.append(part)
    for damage in S14_DAMAGE_NORMALIZATION:
        if damage in text:
            signals.append(damage)
    return list(dict.fromkeys(signals))


def _green_loading_overlay_from_screenshot(screenshot_path: str | Path | None) -> dict[str, Any]:
    if not screenshot_path:
        return {"green_loading_overlay": False, "green_ratio": None, "method": "no_screenshot"}
    path = Path(str(screenshot_path))
    if not path.exists():
        return {"green_loading_overlay": False, "green_ratio": None, "method": "missing_screenshot"}
    try:
        from PIL import Image
    except Exception:
        return {"green_loading_overlay": False, "green_ratio": None, "method": "pil_unavailable"}
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            width, height = image.size
            x0, x1 = int(width * 0.02), int(width * 0.98)
            y0, y1 = int(height * 0.08), int(height * 0.82)
            step = max(8, min(width, height) // 120)
            total = 0
            green = 0
            for y in range(y0, y1, step):
                for x in range(x0, x1, step):
                    r, g, b = image.getpixel((x, y))
                    total += 1
                    if g >= 120 and g >= r + 35 and g >= b + 35:
                        green += 1
            ratio = green / total if total else 0.0
            return {
                "green_loading_overlay": ratio >= 0.45,
                "green_ratio": round(ratio, 4),
                "method": "sampled_green_ratio",
            }
    except Exception as exc:
        return {
            "green_loading_overlay": False,
            "green_ratio": None,
            "method": "screenshot_probe_failed",
            "error": type(exc).__name__,
        }


def _s11_to_s12_loading_overlay_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    text = str(snapshot.get("visible_blob") or "")
    xml_loading_tokens = [token for token in ["加载中", "正在加载", "请稍候"] if token in text]
    green = _green_loading_overlay_from_screenshot(snapshot.get("screenshot_path"))
    detected = bool(xml_loading_tokens or green.get("green_loading_overlay"))
    return {
        "loading_overlay_detected": detected,
        "xml_loading_tokens": xml_loading_tokens,
        **green,
    }


def _record_runtime_issue(
    issues: IssueRecorder,
    code: str,
    state_id: str,
    message: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    return issues.record(code, state_id, message, context, "manual_review")


def _page_contract_allows_action(context: dict[str, Any], page_id: str, action_id: str) -> bool:
    if action_id in SCRIPT_PAGE_CONTRACT_ACTIONS.get(page_id, set()):
        return True
    for page in context.get("configs", {}).get("pages", {}).get("pages", []):
        if page.get("id") == page_id:
            return action_id in set(page.get("allowed_actions") or [])
    return False


def contract_stop(context: dict[str, Any], page_id: str, stop_code: str, reason: str, evidence: dict[str, Any]) -> None:
    issue = _record_runtime_issue(
        context["issues"],
        stop_code,
        page_id,
        reason,
        {
            **evidence,
            "page_contract_is_only_execution_standard": PAGE_CONTRACT_IS_ONLY_EXECUTION_STANDARD,
            "contract_page_id": page_id,
            "contract_stop_code": stop_code,
        },
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def contract_validate_preconditions(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    page_id: str,
    action_id: str,
    *,
    evidence: dict[str, Any] | None = None,
) -> None:
    if page_id == "S10" and action_id in {"tap_next_car_by_price_order", "S10_ONLY_ALLOWED_ACTION_CLICK_REFERENCE_CARD_TITLE"}:
        current_reference = context.get("current_reference") or {}
        reliable = (context.get("s10_live_reliable_evidence") or {}).get("reliable") is True
        if not reliable:
            contract_stop(
                context,
                page_id,
                "S10_RELIABLE_SOURCE_GATE_NOT_PASSED",
                "S10 reference-card click is blocked because reliable S10 source gate is not passed.",
                {**snapshot, **(evidence or {}), "state_preconditions_passed": False, "s10_live_reliable_evidence": context.get("s10_live_reliable_evidence")},
            )
        if not current_reference.get("reference_index") or current_reference.get("card_complete") is not True:
            contract_stop(
                context,
                page_id,
                "S10_REFERENCE_CARD_PRECONDITION_NOT_PASSED",
                "S10 reference-card click is blocked because current reference card is not fully bound.",
                {**snapshot, **(evidence or {}), "state_preconditions_passed": False, "current_reference": current_reference},
            )
        if current_reference.get("title_normalized_match") is False:
            contract_stop(
                context,
                page_id,
                "REFERENCE_CARD_TITLE_NORMALIZED_MISMATCH",
                "S10 reference-card click is blocked because deterministic normalized title match failed.",
                {**snapshot, **(evidence or {}), "state_preconditions_passed": False, "current_reference": current_reference},
            )
    if page_id == "S11" and action_id == "S11_OFFICIAL_REPORT_ENTRY_MISSING_EXCLUDE_REFERENCE":
        evidence = evidence or {}
        if evidence.get("recognized_page") != "S11":
            contract_stop(
                context,
                page_id,
                "S11_REPORT_MISSING_EXCLUDE_CONTEXT_INVALID",
                "S11 missing-report exclusion is blocked because the current page is not confirmed as S11.",
                {**snapshot, **evidence, "state_preconditions_passed": False},
            )
        if not context.get("current_reference", {}).get("reference_index"):
            contract_stop(
                context,
                page_id,
                "S11_REPORT_MISSING_REFERENCE_INDEX_NOT_BOUND",
                "S11 missing-report exclusion is blocked because current reference_index is not bound.",
                {**snapshot, **evidence, "state_preconditions_passed": False},
            )
        official_seen = bool(evidence.get("view_full_report_exact_text_seen") or evidence.get("official_report_entry_seen"))
        merchant_marker_seen = bool(evidence.get("merchant_self_check_marker_seen"))
        if official_seen or not merchant_marker_seen:
            contract_stop(
                context,
                page_id,
                "S11_REPORT_MISSING_EXCLUDE_PRECONDITION_NOT_PASSED",
                "S11 missing-report exclusion is blocked because exact 鏌ョ湅瀹屾暣鎶ュ憡 is present or exact 鍟嗗鑷杞﹀喌 marker is absent.",
                {**snapshot, **evidence, "state_preconditions_passed": False},
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
    if PAGE_CONTRACT_IS_ONLY_EXECUTION_STANDARD and not _page_contract_allows_action(context, page_id, action_id):
        contract_stop(
            context,
            page_id,
            "ACTION_NOT_ALLOWED_BY_PAGE_CONTRACT",
            f"{action_id} is not allowed on {page_id} by page contract.",
            {**snapshot, **(evidence or {}), "attempted_action": action_id},
        )
    contract_validate_preconditions(context, snapshot, page_id, action_id, evidence=evidence)


def contract_execute_click(
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
    context["client"].tap(int(click_point[0]), int(click_point[1]))
    return int((time.perf_counter() - action_start) * 1000)


def contract_execute_swipe(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    page_id: str,
    action_id: str,
    points: tuple[int, int, int, int, int],
    *,
    evidence: dict[str, Any] | None = None,
) -> tuple[Any, int]:
    contract_validate_action(context, snapshot, page_id, action_id, evidence=evidence)
    sx, sy, ex, ey, duration_ms = points
    action_start = time.perf_counter()
    result = context["client"].run(
        ["shell", "input", "swipe", str(sx), str(sy), str(ex), str(ey), str(duration_ms)],
        timeout=20,
    )
    return result, int((time.perf_counter() - action_start) * 1000)


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
    if _recognize_mainline_page(recognizer, snapshot) == "S_LOGIN" and "绋嶅悗" in str(snapshot.get("visible_blob") or ""):
        client.tap_text("绋嶅悗")
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


def _task_id_from_context(context: dict[str, Any]) -> str:
    target_car = context.get("target_car")
    task_result = context.get("task_result") if isinstance(context.get("task_result"), dict) else {}
    return str(
        context.get("task_id")
        or getattr(target_car, "task_id", "")
        or task_result.get("task_id")
        or ""
    )


def _current_reference_index_from_context(context: dict[str, Any]) -> int | None:
    current_reference = context.get("current_reference") if isinstance(context.get("current_reference"), dict) else {}
    value = context.get("current_reference_index") or current_reference.get("reference_index")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_close_guazi_push_popup_and_resume(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    current_stage: str,
    call_site: str = "",
) -> dict[str, Any]:
    def capture_after(stem: str) -> dict[str, Any]:
        return _capture(context["client"], f"{stem}_{_timestamp()}")

    def recognize_after(fresh_snapshot: dict[str, Any]) -> str | None:
        return _recognize_mainline_page(context["recognizer"], fresh_snapshot)

    def apply_trace(target: dict[str, Any], result: dict[str, Any]) -> None:
        target["global_popup_guard_enabled"] = True
        target["global_transient_popup_guard_enabled"] = True
        target["popup_guard_stage"] = current_stage
        target["global_transient_popup_guard_stage"] = current_stage
        target["popup_guard_call_site"] = call_site or "capture"
        target["popup_detected"] = bool(result.get("popup_detected"))
        target["popup_type"] = result.get("popup_type") or (
            GUAZI_PUSH_NOTIFICATION_POPUP if result.get("popup_detected") else ""
        )
        target["popup_close_target_found"] = bool(result.get("popup_close_target_found"))
        target["popup_close_target_bounds"] = result.get("popup_close_target_bounds")
        target["popup_close_attempted"] = bool(result.get("popup_close_attempted"))
        target["popup_closed"] = bool(result.get("popup_closed"))
        target["popup_close_verified"] = bool(result.get("popup_close_verified"))
        target["popup_guard_recaptured"] = bool(result.get("popup_guard_recaptured"))
        target["popup_guard_resume_stage"] = result.get("popup_guard_resume_stage") or result.get("resume_stage") or current_stage
        target["popup_guard_blocked_underlying_click"] = bool(result.get("popup_guard_blocked_underlying_click"))
        target["popup_detected_after_close"] = bool(result.get("popup_detected_after_close"))
        target["popup_guard_failure_stop_code"] = str(result.get("popup_guard_failure_stop_code") or result.get("stop_code") or "")

    result = close_guazi_push_popup_from_snapshot(
        context,
        snapshot,
        capture_func=capture_after,
        recognize_func=recognize_after,
        current_stage=current_stage,
        capture_stem=f"{current_stage.lower()}_guazi_push_popup",
        task_id=_task_id_from_context(context),
        current_reference_index=_current_reference_index_from_context(context),
        click_func=getattr(context.get("client"), "tap", None),
    )
    apply_trace(snapshot, result)
    if not result.get("popup_detected"):
        return snapshot
    evidence = {k: v for k, v in result.items() if k != "fresh_snapshot"}
    evidence.update(
        {
            "global_popup_guard_enabled": True,
            "global_transient_popup_guard_enabled": True,
            "popup_guard_stage": current_stage,
            "global_transient_popup_guard_stage": current_stage,
            "popup_guard_call_site": call_site or "capture",
        }
    )
    context.setdefault("current_reference", {}).setdefault("guazi_transient_popup_history", []).append(evidence)
    if not result.get("popup_closed"):
        code = str(result.get("stop_code") or GUAZI_PUSH_POPUP_CLOSE_FAILED)
        if code not in {GUAZI_PUSH_POPUP_CLOSE_FAILED, GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND}:
            code = GUAZI_PUSH_POPUP_CLOSE_FAILED
        issue = _record_runtime_issue(
            context["issues"],
            code,
            current_stage,
            "Guazi push-notification popup blocked the current page and could not be safely closed.",
            {**snapshot, "guazi_push_popup_close_evidence": evidence},
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    fresh_snapshot = result.get("fresh_snapshot")
    if isinstance(fresh_snapshot, dict):
        snapshot.clear()
        snapshot.update(fresh_snapshot)
        apply_trace(snapshot, result)
        return snapshot
    return snapshot


def _capture_with_global_popup_guard(
    context: dict[str, Any],
    stem: str,
    *,
    current_stage: str,
    call_site: str = "capture",
) -> dict[str, Any]:
    snapshot = _capture(context["client"], f"{stem}_{_timestamp()}")
    snapshot.setdefault("global_popup_guard_enabled", True)
    snapshot.setdefault("global_transient_popup_guard_enabled", True)
    snapshot.setdefault("popup_guard_stage", current_stage)
    snapshot.setdefault("global_transient_popup_guard_stage", current_stage)
    snapshot.setdefault("popup_guard_call_site", call_site)
    return _maybe_close_guazi_push_popup_and_resume(
        context,
        snapshot,
        current_stage=current_stage,
        call_site=call_site,
    )


def _wait_for_page_with_global_popup_guard(
    context: dict[str, Any],
    expected: str,
    stem_prefix: str,
    *,
    current_stage: str,
    timeout_s: float = 8.0,
    interval_s: float = 0.8,
) -> tuple[dict[str, Any], int]:
    recognizer: PageRecognizer = context["recognizer"]
    started = time.perf_counter()
    last_snapshot: dict[str, Any] = {}
    while True:
        time.sleep(interval_s)
        last_snapshot = _capture_with_global_popup_guard(
            context,
            stem_prefix,
            current_stage=current_stage,
            call_site="wait_for_page",
        )
        if _recognize_mainline_page(recognizer, last_snapshot) == expected:
            return last_snapshot, int((time.perf_counter() - started) * 1000)
        if time.perf_counter() - started >= timeout_s:
            return last_snapshot, int((time.perf_counter() - started) * 1000)


def _wait_for_page(
    client: AdbClient,
    recognizer: PageRecognizer,
    expected: str,
    stem_prefix: str,
    *,
    timeout_s: float = 8.0,
    interval_s: float = 0.8,
    context: dict[str, Any] | None = None,
    current_stage: str = "",
) -> tuple[dict[str, Any], int]:
    if context is None:
        raise ValueError("_wait_for_page requires context so global popup guard can wrap captures")
    context.setdefault("client", client)
    context.setdefault("recognizer", recognizer)
    return _wait_for_page_with_global_popup_guard(
        context,
        expected,
        stem_prefix,
        current_stage=current_stage or expected,
        timeout_s=timeout_s,
        interval_s=interval_s,
    )


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
            if label and label != "杞﹁韩澶栬" and label.startswith("杞﹁韩澶栬"):
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
        _, action_ms = contract_execute_swipe(
            context,
            snapshot,
            "S12",
            "scroll_to_body_appearance",
            (
                int(points["swipe_x_start"]),
                int(points["swipe_y_start"]),
                int(points["swipe_x_end"]),
                int(points["swipe_y_end"]),
                int(points["swipe_duration_ms"]),
            ),
            evidence={**points, "scroll_attempt_index": attempt},
        )
        time.sleep(0.4)
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
        snapshot = _capture_with_global_popup_guard(
            context,
            f"{stem_prefix}_search_tab_{attempt}",
            current_stage="S12",
            call_site="s12_body_tab_scroll_fresh",
        )
        fresh_ms = int((time.perf_counter() - fresh_start) * 1000)
        total_scroll_ms += fresh_ms
        found_after_fresh = _find_body_appearance_tab_node(snapshot)
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
                "body_appearance_tab_found": found_after_fresh is not None,
                "s12_body_tab_node_search_count": 1,
                "reason_category": "XML_DUMP_SLOW" if fresh_ms > 1000 else "FRESH_CAPTURE",
                "reason_detail": "fresh screenshot/XML/recognition after controlled scroll to body appearance tab",
                "solution": "stop scrolling as soon as exact body appearance text is visible",
            },
        )
        if found_after_fresh is not None:
            return snapshot, found_after_fresh, total_scroll_ms
    return snapshot, None, total_scroll_ms


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
        _, action_ms = contract_execute_swipe(
            context,
            current,
            "S13",
            "scroll_history_repair_table",
            (
                int(points["swipe_x_start"]),
                int(points["swipe_y_start"]),
                int(points["swipe_x_end"]),
                int(points["swipe_y_end"]),
                int(points["swipe_duration_ms"]),
            ),
            evidence={**points, "scroll_attempt_index": attempt, "page_name": page_name},
        )
        time.sleep(0.4)
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
        current = _capture_with_global_popup_guard(
            context,
            f"{stem_prefix}_{attempt}",
            current_stage=page_name,
            call_site="s13_history_scroll_fresh",
        )
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


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounds_union(bounds_items: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    valid = [bounds for bounds in bounds_items if _valid_bounds(bounds)]
    if not valid:
        return None
    return (
        min(bounds[0] for bounds in valid),
        min(bounds[1] for bounds in valid),
        max(bounds[2] for bounds in valid),
        max(bounds[3] for bounds in valid),
    )


def _s12_node_labels(node: dict[str, Any]) -> list[str]:
    labels = [str(node.get("text") or ""), str(node.get("content_desc") or "")]
    labels.extend(str(item) for item in node.get("labels", []))
    return [item.strip() for item in labels if item and item.strip()]


def _s12_snapshot_nodes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = snapshot.get("nodes")
    if isinstance(nodes, list):
        return nodes
    return _parse_nodes(str(snapshot.get("fresh_xml") or ""))


def _s12_visible_text_blob(snapshot: dict[str, Any]) -> str:
    visible_texts = [str(item) for item in snapshot.get("visible_texts", []) if str(item).strip()]
    visible_blob = str(snapshot.get("visible_blob") or "")
    return "\n".join([visible_blob, *visible_texts])


def _s12_has_body_appearance_text(snapshot: dict[str, Any]) -> bool:
    if "车身外观" in _s12_visible_text_blob(snapshot):
        return True
    for node in _s12_snapshot_nodes(snapshot):
        if any(label == "车身外观" or label.startswith("车身外观") for label in _s12_node_labels(node)):
            return True
    return False


def _s12_body_appearance_progress_evidence(
    snapshot: dict[str, Any],
    recognizer: PageRecognizer | None = None,
) -> dict[str, Any]:
    return clean_page_proofs.prove_s12_body_appearance_reached(
        snapshot,
        nodes=_s12_snapshot_nodes(snapshot),
        node_labels=_s12_node_labels,
        valid_bounds=_valid_bounds,
        region_order=S13_REGION_ORDER,
        visible_blob=_s12_visible_text_blob(snapshot),
        has_body_appearance_text=_s12_has_body_appearance_text(snapshot),
        history_arrival_reason=_history_arrival_reason(recognizer, snapshot) if recognizer is not None else None,
    )


def _s12_to_s13_region_proof_evidence(
    snapshot: dict[str, Any],
    recognizer: PageRecognizer | None = None,
) -> dict[str, Any]:
    progress = _s12_body_appearance_progress_evidence(snapshot, recognizer)
    bindings = snapshot.get("s13_region_history_count_bindings")
    if not isinstance(bindings, dict):
        bindings = {}
    return clean_page_proofs.prove_s12_to_s13_region_history(
        snapshot,
        progress=progress,
        history_table_seen=_has_s13_history_repair_table(snapshot),
        bindings=bindings,
        recognized_page=_recognize_mainline_page(recognizer, snapshot) if recognizer is not None else "",
    )


def _s12_to_s13_proof_stop_code(proof: dict[str, Any]) -> str:
    return clean_transition_gates.s12_to_s13_proof_stop_code(proof)


def _ensure_s12_to_s13_region_proof(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    call_site: str,
    allow_recovery: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    recognizer = context.get("recognizer")
    proof = _s12_to_s13_region_proof_evidence(
        snapshot,
        recognizer if isinstance(recognizer, PageRecognizer) else None,
    )
    proof["s12_to_s13_proof_call_site"] = call_site
    context.setdefault("current_reference", {})["s12_to_s13_region_proof"] = proof
    if proof.get("s12_to_s13_transition_allowed"):
        return snapshot, proof
    issue_code = _s12_to_s13_proof_stop_code(proof)
    proof["s12_to_s13_proof_stop_code"] = issue_code
    issues = context.get("issues")
    if isinstance(issues, IssueRecorder):
        issue = issues.record(
            issue_code,
            "S12_TO_S13",
            "S12 body appearance was reached, but S13 region/history proof was not confirmed.",
            {**snapshot, "s12_to_s13_region_proof": proof},
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    raise GuaziFlowError(issue_code, "S12 to S13 region proof was not confirmed.", {**snapshot, "s12_to_s13_region_proof": proof})


def _s12_bounds_in_strict_top_tab_area(snapshot: dict[str, Any], bounds: Any) -> dict[str, Any]:
    if not _valid_bounds(bounds):
        return {"safe": False, "reason": "invalid_or_zero_bounds"}
    x1, y1, x2, y2 = bounds
    center_y = (y1 + y2) // 2
    safe = bool(300 <= center_y <= 720 and (x2 - x1) >= 80)
    return {
        "safe": safe,
        "reason": "strict_top_tab_area" if safe else "outside_strict_top_tab_area",
        "bounds": list(bounds),
        "center_y": center_y,
    }


def _s12_has_body_tab_row_neighbor(snapshot: dict[str, Any], bounds: Any) -> bool:
    if not _valid_bounds(bounds):
        return False
    center_y = (bounds[1] + bounds[3]) // 2
    has_left = False
    has_right = False
    for node in _s12_snapshot_nodes(snapshot):
        node_bounds = node.get("bounds")
        if not _valid_bounds(node_bounds):
            continue
        labels = _s12_node_labels(node)
        node_center_y = (node_bounds[1] + node_bounds[3]) // 2
        if abs(node_center_y - center_y) > 140:
            continue
        if any("閲嶅ぇ" in label or "重大问题" in label for label in labels):
            has_left = True
        if any("鍐呴グ" in label or "内饰" in label for label in labels):
            has_right = True
    return has_left and has_right


def _s12_estimate_body_tab_from_tab_row(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    visible_blob = _s12_visible_text_blob(snapshot)
    if "杞﹁韩澶栬" not in visible_blob and "车身外观" not in visible_blob:
        return None
    for node in _s12_snapshot_nodes(snapshot):
        labels = _s12_node_labels(node)
        if any(label == "杞﹁韩澶栬" or label == "车身外观" for label in labels):
            if not _valid_bounds(node.get("bounds")):
                return None
    left_bounds: list[tuple[int, int, int, int]] = []
    right_bounds: list[tuple[int, int, int, int]] = []
    for node in _s12_snapshot_nodes(snapshot):
        bounds = node.get("bounds")
        if not _valid_bounds(bounds):
            continue
        labels = _s12_node_labels(node)
        if any("閲嶅ぇ" in label or "重大问题" in label for label in labels):
            left_bounds.append(bounds)
        if any("鍐呴グ" in label or "内饰" in label for label in labels):
            right_bounds.append(bounds)
    for left in left_bounds:
        left_center_y = (left[1] + left[3]) // 2
        for right in right_bounds:
            right_center_y = (right[1] + right[3]) // 2
            if abs(left_center_y - right_center_y) > 140:
                continue
            center_x = (((left[0] + left[2]) // 2) + ((right[0] + right[2]) // 2)) // 2
            center_y = (left_center_y + right_center_y) // 2
            bounds = (center_x - 90, min(left[1], right[1]), center_x + 90, max(left[3], right[3]))
            return {
                "detected_text": "杞﹁韩澶栬",
                "click_source": "body_appearance_tab_row_estimated_safe_center",
                "bounds": bounds,
                "click_point": (center_x, center_y),
                "confidence": "medium",
                "estimated_from_tab_row": True,
                "strict_top_tab_check": _s12_bounds_in_strict_top_tab_area(snapshot, bounds),
                "tab_row_neighbor_confirmed": True,
            }
    return None


def _find_s12_body_appearance_safe_fallback_click_target(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    progress = _s12_body_appearance_progress_evidence(snapshot)
    if progress["body_appearance_section_reached"]:
        return None
    for node in _s12_snapshot_nodes(snapshot):
        bounds = node.get("bounds")
        if not _valid_bounds(bounds):
            continue
        labels = _s12_node_labels(node)
        if "内饰及配置" in labels:
            continue
        if "车身外观" not in labels:
            continue
        strict = _s12_bounds_in_strict_top_tab_area(snapshot, bounds)
        has_neighbor = _s12_has_body_tab_row_neighbor(snapshot, bounds)
        if strict["safe"] or has_neighbor:
            return {
                "detected_text": "车身外观",
                "click_source": "body_appearance_text_node_bounds",
                "bounds": bounds,
                "click_point": _center(bounds),
                "confidence": "high" if has_neighbor else "medium",
                "estimated_from_tab_row": False,
                "strict_top_tab_check": strict,
                "tab_row_neighbor_confirmed": has_neighbor,
            }
    return _s12_estimate_body_tab_from_tab_row(snapshot)


def _verify_s12_body_appearance_safe_fallback_result(
    context: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    recognizer = context.get("recognizer")
    evidence = _s12_body_appearance_progress_evidence(snapshot, recognizer if isinstance(recognizer, PageRecognizer) else None)
    success = bool(
        evidence["body_appearance_tab_selected"]
        or evidence["body_appearance_section_reached"]
        or evidence["s13_region_tabs_present"]
        or evidence["body_appearance_detection_items_present"]
    )
    evidence.update(
        {
            "safe_fallback_verify_success": success,
            "recognized_page": _recognize_mainline_page(recognizer, snapshot) if recognizer is not None else "",
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
        }
    )
    return evidence


def _handle_s12_body_appearance_missing_exact_target(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    field_ms: int,
    read_start: float,
    start: float,
    body_tab_node_search_count: int,
    safe_scroll_ms: int,
) -> tuple[str, dict[str, Any]] | None:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    timing: TimingRecorder = context["timing"]
    current_reference = context.setdefault("current_reference", {})
    progress = _s12_body_appearance_progress_evidence(snapshot, recognizer)
    if progress["body_appearance_section_reached"] or progress.get("body_appearance_detection_items_present"):
        try:
            proof_snapshot, proof = _ensure_s12_to_s13_region_proof(
                context,
                snapshot,
                call_site="section_already_reached",
                allow_recovery=True,
            )
        except GuaziFlowError:
            current_reference["s12_body_appearance_click"] = {
                "click_attempted": False,
                "body_appearance_tab_click_skipped_reason": (
                    "SECTION_ALREADY_REACHED"
                    if progress["body_appearance_section_reached"]
                    else "SECTION_REACHED_WITHOUT_S13_REGION_PROOF"
                ),
                "body_appearance_progress_evidence": progress,
                "s12_body_tab_node_search_count": body_tab_node_search_count,
                "pre_click_scroll_used": bool(safe_scroll_ms),
                "screenshot_path": str(snapshot.get("screenshot_path") or ""),
                "xml_path": str(snapshot.get("xml_path") or ""),
            }
            raise
        current_reference["s12_body_appearance_click"] = {
            "click_attempted": False,
            "body_appearance_tab_click_skipped_reason": "SECTION_ALREADY_REACHED",
            "body_appearance_progress_evidence": progress,
            "s12_to_s13_region_proof": proof,
            "s12_body_tab_node_search_count": body_tab_node_search_count,
            "pre_click_scroll_used": bool(safe_scroll_ms),
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
        }
        timing.add(
            step_name="S12_BODY_APPEARANCE_SECTION_ALREADY_REACHED",
            page_name="S12",
            action_name="skip_body_appearance_tab_click_section_already_reached",
            contract_check_ms=int((read_start - start) * 1000),
            field_read_ms=field_ms,
            action_ms=0,
            transition_wait_ms=safe_scroll_ms,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra={
                **progress,
                "body_appearance_tab_click_skipped_reason": "SECTION_ALREADY_REACHED",
                "reason_category": "SECTION_ALREADY_REACHED",
                "reason_detail": "body appearance section or S13 region proof is already visible; do not force-click the tab",
                "solution": "continue only because current fresh evidence already proves the body appearance section/S13 entry",
            },
        )
        if progress.get("history_arrival_reason"):
            _record_history_arrived_timing(context, snapshot, page_name="S12", reason=str(progress["history_arrival_reason"]))
        return "S13", proof_snapshot

    if current_reference.get("s12_body_appearance_safe_fallback_click_attempted"):
        return None
    target = _find_s12_body_appearance_safe_fallback_click_target(snapshot)
    if target is None:
        return None
    current_reference["s12_body_appearance_safe_fallback_click_attempted"] = True
    clicked_point = target["click_point"]
    action_ms = contract_execute_click(
        context,
        snapshot,
        "S12",
        "tap_body_appearance",
        (int(clicked_point[0]), int(clicked_point[1])),
        evidence={
            **target,
            "fallback_action": S12_BODY_APPEARANCE_SAFE_FALLBACK_CLICK,
            "click_point_inside_detected_rect": True,
        },
    )
    time.sleep(0.6)
    fresh_start = time.perf_counter()
    next_snapshot = _capture_with_global_popup_guard(
        context,
        "s12_body_appearance_safe_fallback_after_click",
        current_stage="S12_TO_S13",
        call_site="s12_body_appearance_safe_fallback_verify",
    )
    fresh_ms = int((time.perf_counter() - fresh_start) * 1000)
    verify = _verify_s12_body_appearance_safe_fallback_result(context, next_snapshot)
    current_reference["s12_body_appearance_click"] = {
        "clicked_text": "车身外观",
        "clicked_node_bounds": list(target["bounds"]),
        "clicked_point": list(clicked_point),
        "click_strategy": "safe_fallback_click",
        "click_source": target["click_source"],
        "fallback_action": S12_BODY_APPEARANCE_SAFE_FALLBACK_CLICK,
        "click_point_inside_detected_rect": True,
        "detected_text": target["detected_text"],
        "confidence": target["confidence"],
        "estimated_from_tab_row": bool(target.get("estimated_from_tab_row")),
        "s12_body_tab_node_search_count": body_tab_node_search_count,
        "pre_click_scroll_used": bool(safe_scroll_ms),
        "post_click_verify": verify,
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "xml_path": str(snapshot.get("xml_path") or ""),
    }
    timing.add(
        step_name=S12_BODY_APPEARANCE_SAFE_FALLBACK_CLICK,
        page_name="S12",
        action_name="safe_fallback_click_body_appearance",
        contract_check_ms=int((read_start - start) * 1000),
        field_read_ms=field_ms,
        action_ms=action_ms,
        transition_wait_ms=fresh_ms + safe_scroll_ms,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            **target,
            "clicked_point": list(clicked_point),
            "post_click_verify": verify,
            "reason_category": "SAFE_FALLBACK_CLICK",
            "reason_detail": "exact top-tab node was not bindable, but body appearance text was safely bound in the top tab row",
            "solution": "click once from realtime text/tab-row bounds and require fresh proof before continuing",
        },
    )
    if not verify["safe_fallback_verify_success"]:
        issue = issues.record(
            S12_BODY_APPEARANCE_SAFE_FALLBACK_CLICK_VERIFY_FAILED,
            "S12",
            "Safe fallback clicked body appearance, but fresh evidence did not confirm the tab, section, region tabs, or body appearance items.",
            {**next_snapshot, "s12_body_appearance_safe_fallback_verify": verify},
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if verify.get("history_arrival_reason"):
        _record_history_arrived_timing(context, next_snapshot, page_name="S12", reason=str(verify["history_arrival_reason"]))
    proof_snapshot, proof = _ensure_s12_to_s13_region_proof(
        context,
        next_snapshot,
        call_site="safe_fallback_after_click",
        allow_recovery=True,
    )
    current_reference["s12_body_appearance_click"]["s12_to_s13_region_proof"] = proof
    return "S13", proof_snapshot


def _parse_card_city(value: str) -> str:
    if "|" not in value:
        return ""
    return value.split("|")[-1].strip()


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


def _s10_boundary_marker(label: str) -> str:
    text = str(label or "").strip()
    return next((marker for marker in S10_NON_TRISAME_BOUNDARY_MARKERS if marker in text), "")


def _s10_target_title_fragments(target_car: dict[str, Any] | None) -> list[str]:
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


def normalize_vehicle_title_for_match(title: str) -> str:
    return re.sub(r"[\s·\-_—/（）(),，]+", "", str(title or "")).strip().lower()


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = normalize_vehicle_title_for_match(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def build_target_title_alias_profile(target_car: dict[str, Any] | None) -> dict[str, Any]:
    target = target_car or {}
    brand = str(target.get("brand") or "").strip()
    series = str(target.get("series") or "").strip()
    year_model = str(target.get("year_model") or target.get("model_year") or "").strip()
    config_model = str(target.get("config_model") or target.get("trim") or "").strip()

    brand_aliases = [brand]
    if brand == "\u96f6\u8dd1":
        brand_aliases.extend(["\u96f6\u8dd1\u6c7d\u8f66", "LEAPMOTOR", "Leapmotor"])

    series_aliases = [series]
    if brand and series:
        series_aliases.extend([f"{brand}{series}", f"{brand} {series}"])
        for brand_alias in brand_aliases:
            if re.search(r"[\u4e00-\u9fff]", brand_alias):
                series_aliases.extend([f"{brand_alias}{series}", f"{brand_alias} {series}"])
    if series == "C10":
        series_aliases.extend(["\u96f6\u8dd1C10", "\u96f6\u8dd1 C10"])

    profile = {
        "brand": brand,
        "series": series,
        "year_model": year_model,
        "config_model": config_model,
        "brand_aliases": _unique_nonempty(brand_aliases),
        "series_aliases": _unique_nonempty(series_aliases),
        "normalized_expected_title": normalize_vehicle_title_for_match(
            " ".join(item for item in [brand, series, year_model, config_model] if item)
        ),
    }
    profile["config_model_match_candidates"] = _s10_title_config_match_candidates(config_model, profile)
    return profile


def _title_contains_alias(normalized_title: str, alias: str) -> bool:
    normalized_alias = normalize_vehicle_title_for_match(alias)
    if not normalized_alias:
        return False
    if re.fullmatch(r"[a-z0-9]+", normalized_alias):
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])", normalized_title) is not None
    return normalized_alias in normalized_title


def _title_conflict_terms(normalized_live_title: str, profile: dict[str, Any]) -> list[str]:
    conflicts: list[str] = []
    target_year = str(profile.get("year_model") or "").strip()
    target_year_norm = normalize_vehicle_title_for_match(target_year)
    for year in sorted(set(re.findall(r"20\d{2}\u6b3e", normalized_live_title))):
        if target_year_norm and year != target_year_norm:
            conflicts.append(year)

    target_series_norm = normalize_vehicle_title_for_match(str(profile.get("series") or ""))
    if target_series_norm:
        series_scan_title = re.sub(r"20\d{2}\u6b3e", " ", normalized_live_title)
        for token in sorted(set(re.findall(r"[a-z]+\d+", series_scan_title))):
            if token != target_series_norm and (token.startswith("c") or token.startswith("t") or token.startswith("b") or token.startswith("s")):
                conflicts.append(token.upper())

    target_config_norm = normalize_vehicle_title_for_match(str(profile.get("config_model") or ""))
    for term in ["\u589e\u7a0b", "\u667a\u9a7e\u7248", "\u667a\u4eab\u7248", "530"]:
        term_norm = normalize_vehicle_title_for_match(term)
        if term_norm in normalized_live_title and term_norm not in target_config_norm:
            conflicts.append(term)
    return _unique_nonempty(conflicts)


def match_reference_title_by_normalized_alias(live_title: str, target_car: dict[str, Any] | None) -> dict[str, Any]:
    profile = build_target_title_alias_profile(target_car)
    normalized_live_title = normalize_vehicle_title_for_match(live_title)
    brand_aliases = profile["brand_aliases"]
    series_aliases = profile["series_aliases"]
    year_model = str(profile.get("year_model") or "")
    config_model = str(profile.get("config_model") or "")

    matched_brand_alias = next((alias for alias in brand_aliases if _title_contains_alias(normalized_live_title, alias)), "")
    matched_series_alias = next((alias for alias in series_aliases if _title_contains_alias(normalized_live_title, alias)), "")
    brand_alias_match = bool(matched_brand_alias)
    if not brand_alias_match:
        brand_alias_match = any(
            bool(str(profile.get("brand") or "")) and normalize_vehicle_title_for_match(str(profile.get("brand") or "")) in normalize_vehicle_title_for_match(alias) and _title_contains_alias(normalized_live_title, alias)
            for alias in series_aliases
        )
    series_alias_match = bool(matched_series_alias)
    year_model_match = bool(year_model) and normalize_vehicle_title_for_match(year_model) in normalized_live_title
    config_model_match_candidates = list(profile.get("config_model_match_candidates") or [])
    matched_config_model_candidate = next(
        (candidate for candidate in config_model_match_candidates if candidate and candidate in normalized_live_title),
        "",
    )
    config_model_match = bool(config_model_match_candidates) and bool(matched_config_model_candidate)
    conflict_terms = _title_conflict_terms(normalized_live_title, profile)
    title_normalized_match = bool(
        brand_alias_match
        and series_alias_match
        and year_model_match
        and config_model_match
        and not conflict_terms
    )
    mismatch_reason: list[str] = []
    if not brand_alias_match:
        mismatch_reason.append("brand_alias_not_matched")
    if not series_alias_match:
        mismatch_reason.append("series_alias_not_matched")
    if not year_model_match:
        mismatch_reason.append("year_model_not_matched")
    if not config_model_match:
        mismatch_reason.append("config_model_not_matched")
    if conflict_terms:
        mismatch_reason.append("conflict_terms_detected")
    return {
        "title_match_strategy": "normalized_alias_match",
        "expected_title_raw": " ".join(
            item
            for item in [
                profile.get("brand"),
                profile.get("series"),
                profile.get("year_model"),
                profile.get("config_model"),
            ]
            if item
        ),
        "live_title_raw": str(live_title or "").strip(),
        "normalized_expected_title": profile.get("normalized_expected_title"),
        "normalized_live_title": normalized_live_title,
        "brand_aliases": brand_aliases,
        "series_aliases": series_aliases,
        "brand_alias_match": brand_alias_match,
        "matched_brand_alias": matched_brand_alias,
        "series_alias_match": series_alias_match,
        "matched_series_alias": matched_series_alias,
        "year_model_match": year_model_match,
        "config_model_match": config_model_match,
        "config_model_match_candidates": config_model_match_candidates,
        "matched_config_model_candidate": matched_config_model_candidate,
        "title_normalized_match": title_normalized_match,
        "conflict_terms": conflict_terms,
        "mismatch_reason": mismatch_reason,
        "title_match_decision": "allow" if title_normalized_match else "reject",
    }


def _s10_card_title_matches_target(title: str, target_car: dict[str, Any] | None) -> bool:
    target = target_car or {}
    if not any(str(target.get(key) or "").strip() for key in ("brand", "series", "year_model", "model_year", "config_model", "trim")):
        return True
    return bool(match_reference_title_by_normalized_alias(title, target_car).get("title_normalized_match"))


def _s10_non_trisame_boundary(visible_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    boundary_candidates: list[dict[str, Any]] = []
    for node in visible_nodes:
        label = _node_label(node)
        marker = _s10_boundary_marker(label)
        bounds = node.get("bounds")
        if marker and _valid_bounds(bounds):
            boundary_candidates.append(
                {
                    "non_trisame_section_detected": True,
                    "non_trisame_section_title": marker,
                    "boundary_text": label,
                    "boundary_node_bounds": list(bounds),
                    "boundary_y": bounds[1],
                }
            )
    if not boundary_candidates:
        return {
            "non_trisame_section_detected": False,
            "non_trisame_section_title": "",
            "boundary_text": "",
            "boundary_node_bounds": None,
            "boundary_y": None,
        }
    return sorted(boundary_candidates, key=lambda item: item["boundary_y"])[0]


def _s10_card_completeness(
    card: dict[str, Any],
    *,
    viewport_bounds: tuple[int, int, int, int] | None,
) -> dict[str, Any]:
    has_title = bool(str(card.get("list_title") or "").strip())
    has_price = bool(str(card.get("list_price_text") or "").strip()) and card.get("list_price_yuan") is not None
    has_metadata = bool(str(card.get("raw_metadata") or "").strip())
    has_year = card.get("list_year") is not None
    has_mileage = card.get("list_mileage_10k_km") is not None
    has_city = bool(str(card.get("city") or "").strip())
    bounds = card.get("card_bounds") or card.get("clicked_card_bounds")
    card_fully_visible = False
    if _valid_bounds(bounds) and viewport_bounds:
        _vx1, vy1, _vx2, vy2 = viewport_bounds
        height = max(vy2 - vy1, 1)
        safe_bottom = vy2 - max(int(height * 0.08), 80)
        click_bounds = card.get("clicked_card_bounds")
        click_bottom = click_bounds[3] if _valid_bounds(click_bounds) else bounds[3]
        card_fully_visible = bounds[3] <= safe_bottom and click_bottom <= safe_bottom
    elif _valid_bounds(bounds):
        card_fully_visible = True

    incomplete_reason: list[str] = []
    if not has_title:
        incomplete_reason.append("missing_title")
    if not has_price:
        incomplete_reason.append("missing_price")
    if not has_metadata:
        incomplete_reason.append("missing_metadata")
    if not has_year:
        incomplete_reason.append("missing_year")
    if not has_mileage:
        incomplete_reason.append("missing_mileage")
    if not has_city:
        incomplete_reason.append("missing_city")
    if not card_fully_visible:
        incomplete_reason.append("bottom_partial_card_or_outside_safe_viewport")

    card_complete = (
        has_title
        and has_price
        and has_metadata
        and has_year
        and has_mileage
        and has_city
        and card_fully_visible
    )
    return {
        "card_complete": card_complete,
        "partial_card_candidate": not card_complete,
        "has_title": has_title,
        "has_price": has_price,
        "has_metadata": has_metadata,
        "has_year": has_year,
        "has_mileage": has_mileage,
        "has_city": has_city,
        "card_fully_visible": card_fully_visible,
        "incomplete_reason": incomplete_reason,
    }


def _extract_s10_reference_cards(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    viewport_info = _visible_bounds_extent(snapshot)
    viewport_bounds = viewport_info[0] if viewport_info else None
    visible_nodes: list[dict[str, Any]] = []
    for node in nodes:
        bounds = node.get("bounds")
        if _valid_bounds(bounds):
            visible_nodes.append(node)

    target_brand = str(snapshot.get("target_brand") or "").strip()
    target_car = snapshot.get("target_car") if isinstance(snapshot.get("target_car"), dict) else {}
    boundary = _s10_non_trisame_boundary(visible_nodes)
    boundary_y = boundary.get("boundary_y")
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
    excluded_cards: list[dict[str, Any]] = []
    raw_cards: list[dict[str, Any]] = []
    for live_index, title_node in enumerate(title_nodes, start=1):
        title_bounds = title_node["bounds"]
        title = _node_label(title_node)
        next_title_y = title_nodes[live_index]["bounds"][1] if live_index < len(title_nodes) else title_bounds[1] + 380
        bottom_y = min(next_title_y, title_bounds[1] + 380)
        card_nodes = [
            node
            for node in visible_nodes
            if title_bounds[1] - 20 <= node["bounds"][1] < bottom_y
        ]

        year = None
        mileage = None
        info_text = ""
        info_bounds = None
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
                info_bounds = node.get("bounds")
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
        card_bounds = _bounds_union(
            [bounds for bounds in [title_bounds, price_bounds, info_bounds] if _valid_bounds(bounds)]
        )
        card = {
            "live_display_order": live_index,
            "reference_index": live_index,
            "reference_key": f"{live_index}:{title}:{info_text}:{price_text}",
            "list_title": title,
            "list_price_text": price_text,
            "list_price_10k": price,
            "list_price_yuan": int(round(float(price) * 10000)) if price is not None else None,
            "list_year": year,
            "list_mileage_10k_km": mileage,
            "raw_metadata": info_text,
            "city": _parse_card_city(info_text),
            "card_bounds": card_bounds,
            "clicked_card_bounds": title_bounds,
            "clicked_card_text_digest": text_digest,
            "metadata_bounds": info_bounds,
            "price_bounds": price_bounds,
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
        }
        card.update(_s10_card_completeness(card, viewport_bounds=viewport_bounds))
        raw_cards.append(card)
        after_boundary = boundary_y is not None and title_bounds[1] > int(boundary_y)
        title_matches_target = _s10_card_title_matches_target(title, target_car)
        if after_boundary or not title_matches_target:
            excluded = dict(card)
            excluded.update(
                {
                    "excluded_non_trisame_card": True,
                    "exclude_reason": "after_non_trisame_boundary" if after_boundary else "title_mismatch",
                    "actual_title": title,
                    "actual_price": price_text,
                    "actual_metadata": info_text,
                    "section_context": boundary.get("non_trisame_section_title") if after_boundary else "before_non_trisame_boundary",
                    "target_title_fragments": _s10_target_title_fragments(target_car),
                }
            )
            excluded_cards.append(excluded)
            continue
        cards.append(card)
    ordered = _canonicalize_s10_reference_order(cards)
    snapshot["s10_reference_order_audit"] = {
        **boundary,
        "raw_visible_cards_count": len(raw_cards),
        "trisame_cards_count": len([card for card in ordered if card.get("card_complete") is True]),
        "excluded_non_trisame_cards_count": len(excluded_cards),
        "cards_after_boundary_excluded_count": len([card for card in excluded_cards if card.get("exclude_reason") == "after_non_trisame_boundary"]),
        "excluded_non_trisame_cards": excluded_cards,
        "target_title_fragments": _s10_target_title_fragments(target_car),
    }
    return ordered


def _canonicalize_s10_reference_order(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(card: dict[str, Any]) -> tuple[float, float, int]:
        price = card.get("list_price_10k")
        mileage = card.get("list_mileage_10k_km")
        live_order = _safe_int(card.get("live_display_order"), default=9999)
        price_key = float(price) if price is not None else 999999.0
        mileage_key = -float(mileage) if mileage is not None else 999999.0
        return (price_key, mileage_key, live_order)

    complete_cards = [card for card in cards if card.get("card_complete") is True]
    partial_cards = [card for card in cards if card.get("card_complete") is not True]
    ordered = sorted(complete_cards, key=sort_key)
    for canonical_index, card in enumerate(ordered, start=1):
        card["reference_index"] = canonical_index
        card["canonical_reference_index"] = canonical_index
        card["s10_order_rule"] = "price_asc_mileage_desc_for_same_price"
        card["reference_key"] = (
            f"{canonical_index}:{card.get('list_title')}:{card.get('raw_metadata') or ''}:{card.get('list_price_text') or ''}"
        )
    for card in partial_cards:
        card["reference_index"] = None
        card["canonical_reference_index"] = None
        card["s10_order_rule"] = "price_asc_mileage_desc_for_same_price"
        card["reference_key"] = (
            f"partial:{card.get('live_display_order')}:{card.get('list_title')}:{card.get('raw_metadata') or ''}:{card.get('list_price_text') or ''}"
        )
    return ordered + partial_cards


def _s10_reference_unique_key(card: dict[str, Any]) -> tuple[Any, Any, Any, Any, Any]:
    return (
        str(card.get("list_title") or "").strip(),
        card.get("list_price_10k"),
        card.get("list_year"),
        card.get("list_mileage_10k_km"),
        str(card.get("city") or "").strip(),
    )


def _collected_reference_keys(reference_history: list[dict[str, Any]]) -> set[tuple[Any, Any, Any, Any, Any]]:
    keys: set[tuple[Any, Any, Any, Any, Any]] = set()
    for reference in reference_history:
        keys.add(
            (
                str(reference.get("list_title") or reference.get("selected_card_title") or "").strip(),
                reference.get("list_price_10k"),
                reference.get("list_year"),
                reference.get("list_mileage_10k_km"),
                str(reference.get("city") or "").strip()
                or str(reference.get("selected_card_metadata") or reference.get("raw_metadata") or "").split("|")[-1].strip(),
            )
        )
    return keys


def _processed_reference_indices(reference_history: list[dict[str, Any]]) -> set[int]:
    indices: set[int] = set()
    for reference in reference_history:
        if not isinstance(reference, dict):
            continue
        index = _safe_int(reference.get("reference_index") or reference.get("selected_reference_index"), default=0)
        if index > 0:
            indices.add(index)
    return indices


def _processed_reference_identity_summary(reference_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for reference in reference_history:
        if not isinstance(reference, dict):
            continue
        summaries.append(
            {
                "reference_index": reference.get("reference_index") or reference.get("selected_reference_index"),
                "list_title": reference.get("list_title") or reference.get("selected_card_title"),
                "list_price_text": reference.get("list_price_text") or reference.get("selected_card_price"),
                "list_price_10k": reference.get("list_price_10k"),
                "list_year": reference.get("list_year"),
                "list_mileage_10k_km": reference.get("list_mileage_10k_km"),
                "city": reference.get("city"),
                "raw_metadata": reference.get("raw_metadata") or reference.get("selected_card_metadata"),
                "reference_status": reference.get("reference_status"),
            }
        )
    return summaries


V33_BOUNDARY_PREVIOUS_RECOLLECT_REASONS = {
    "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE",
    "boundary_previous_reference_incomplete",
    "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE_OR_SKIPPED",
    "boundary_previous_reference_incomplete_or_skipped",
    "BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
    "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
}

V33_BOUNDARY_PREVIOUS_RECOLLECT_STATUSES = {
    "BOUNDARY_PREVIOUS_INCOMPLETE",
    "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE",
    "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE_OR_SKIPPED",
    "LOW_SCORE_SKIPPED_INCOMPLETE",
    "S14_COLLECTION_INCOMPLETE_UNRECOVERABLE",
    "REFERENCE_SCORE_UNTRUSTED_INCOMPLETE_COLLECTION",
    "INCOMPLETE_RECOLLECT_CANDIDATE",
}

V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW = (
    "V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW"
)


def _reference_history_entry_by_index(reference_history: list[dict[str, Any]] | None, reference_index: int) -> dict[str, Any]:
    for item in reference_history or []:
        if not isinstance(item, dict):
            continue
        item_index = _safe_int(item.get("reference_index") or item.get("selected_reference_index"), default=0)
        if item_index == int(reference_index):
            return item
    return {}


def _reference_history_entry_status(entry: dict[str, Any], expected: dict[str, Any]) -> str:
    return str(
        expected.get("final_reference_candidate_status")
        or entry.get("reference_status")
        or entry.get("status")
        or entry.get("excluded_from_boundary_reason")
        or ""
    ).strip()


def _reference_history_entry_fully_trusted(entry: dict[str, Any], expected: dict[str, Any]) -> bool:
    status = _reference_history_entry_status(entry, expected).upper()
    if status in V33_BOUNDARY_PREVIOUS_RECOLLECT_STATUSES:
        return False
    if entry.get("final_reference_recollect_done") is True or entry.get("recollection_completed") is True:
        return True
    if entry.get("fully_collected_trusted") is True or entry.get("reference_status") == "FULLY_COLLECTED_TRUSTED":
        return True
    return bool(entry.get("reference_score_trustworthy") is True and entry.get("reference_score_usable_for_boundary") is True)


def _reference_history_entry_needs_v33_recollect(entry: dict[str, Any], expected: dict[str, Any]) -> bool:
    if not entry:
        return False
    status = _reference_history_entry_status(entry, expected).upper()
    if status in V33_BOUNDARY_PREVIOUS_RECOLLECT_STATUSES:
        return True
    if entry.get("low_score_skipped_incomplete") is True:
        return True
    if entry.get("reference_score_trustworthy") is False or entry.get("reference_score_usable_for_boundary") is False:
        return True
    if str(entry.get("excluded_from_boundary_reason") or "").upper() in V33_BOUNDARY_PREVIOUS_RECOLLECT_STATUSES:
        return True
    return False


def _s10_duplicate_reference_reentry_allowed(
    expected_card: dict[str, Any] | None,
    target_reference_index: int,
    reference_history: list[dict[str, Any]] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    expected = expected_card or {}
    recollect_index = _safe_int(
        expected.get("recollect_reference_index")
        or expected.get("boundary_previous_reference_index")
        or expected.get("reference_index")
        or expected.get("selected_reference_index"),
        default=0,
    )
    recollect_reason = str(expected.get("recollect_reason") or expected.get("recollect_required_reason") or "")
    boundary_index = _safe_int(expected.get("boundary_reference_index"), default=0)
    candidate_status = str(expected.get("final_reference_candidate_status") or "")
    recollect_mode = bool(
        expected.get("boundary_previous_recollect_required") is True
        or expected.get("final_reference_recollect_required") is True
        or expected.get("recollect_mode") is True
        or str(expected.get("continue_reason") or "") == "BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
        or recollect_reason in V33_BOUNDARY_PREVIOUS_RECOLLECT_REASONS
    )
    history_entry = _reference_history_entry_by_index(reference_history, int(target_reference_index))
    trace = {
        "duplicate_reference_allowed_for_recollect": False,
        "duplicate_reference_recollect_mode": recollect_mode,
        "duplicate_reference_recollect_reference_index": recollect_index or None,
        "duplicate_reference_boundary_reference_index": boundary_index or None,
        "duplicate_reference_boundary_reference_score": expected.get("boundary_reference_score"),
        "duplicate_reference_target_score": expected.get("target_score"),
        "duplicate_reference_recollect_reason": recollect_reason,
        "duplicate_reference_candidate_previous_status": candidate_status
        or _reference_history_entry_status(history_entry, expected),
    }
    if not recollect_mode:
        return False, "", trace
    if recollect_index != int(target_reference_index):
        return False, "RECOLLECT_REFERENCE_INDEX_MISMATCH", trace
    if boundary_index > 0 and int(target_reference_index) != boundary_index - 1:
        return False, "RECOLLECT_REFERENCE_INDEX_NOT_PREVIOUS_OF_BOUNDARY", trace
    if not history_entry:
        return False, "BOUNDARY_PREVIOUS_RECOLLECT_REFERENCE_HISTORY_MISSING", trace
    if _reference_history_entry_fully_trusted(history_entry, expected):
        return False, "DUPLICATE_REFERENCE_CLICK_BLOCKED_FULLY_COLLECTED_TRUSTED", trace
    if recollect_reason not in V33_BOUNDARY_PREVIOUS_RECOLLECT_REASONS:
        return False, "BOUNDARY_PREVIOUS_RECOLLECT_REASON_NOT_ALLOWED", trace
    if not _reference_history_entry_needs_v33_recollect(history_entry, expected):
        return False, "BOUNDARY_PREVIOUS_RECOLLECT_CANDIDATE_NOT_INCOMPLETE", trace
    trace["duplicate_reference_allowed_for_recollect"] = True
    return True, "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE_RECOLLECT", trace


def _s10_same_price_group_summary(cards: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[float, list[dict[str, Any]]] = {}
    for card in cards:
        price = card.get("list_price_10k")
        if price is None:
            continue
        groups.setdefault(float(price), []).append(card)
    same_groups = {price: group for price, group in groups.items() if len(group) > 1}
    if not same_groups:
        return {
            "same_price_group_detected": False,
            "same_price_group_price": "",
            "same_price_group_order": [],
        }
    price, group = sorted(same_groups.items(), key=lambda item: item[0])[0]
    return {
        "same_price_group_detected": True,
        "same_price_group_price": f"{price:.2f}万",
        "same_price_group_order": [
            {
                "metadata": card.get("raw_metadata") or "",
                "mileage_10k_km": card.get("list_mileage_10k_km"),
                "live_display_order": card.get("live_display_order"),
                "canonical_reference_index": card.get("canonical_reference_index"),
            }
            for card in sorted(group, key=lambda item: (-(float(item.get("list_mileage_10k_km") or -1)), _safe_int(item.get("live_display_order"), default=9999)))
        ],
    }


def _s10_reliable_list_evidence(
    snapshot: dict[str, Any],
    *,
    target_reference_index: int | None = None,
    expected_card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    visible_texts = list(snapshot.get("visible_texts") or _visible_texts(str(snapshot.get("fresh_xml") or "")))
    text_blob = "\n".join(str(item) for item in visible_texts)
    cards = _extract_s10_reference_cards(snapshot)
    order_audit = snapshot.get("s10_reference_order_audit") or {}
    complete_cards = [card for card in cards if card.get("card_complete") is True]
    partial_cards = [card for card in cards if card.get("card_complete") is not True]
    report_signals = [
        signal
        for signal in ["查看完整报告", "保险理赔记录", "理赔次数", "最大金额", "重大问题排查", "车身外观", "内饰及配置"]
        if signal in text_blob
    ]
    bottom_detail_actions = [
        signal
        for signal in ["电话", "收藏", "咨询车况", "讲价", "查看报价"]
        if signal in text_blob
    ]
    has_price_sort = "价格从低到高" in text_blob
    has_vehicle_cards = len(complete_cards) > 0
    has_detail_report_page_signals = bool(report_signals) and ("查看完整报告" in report_signals or len(report_signals) >= 3)
    visible_card_summary = [
        {
            "reference_index": card.get("reference_index"),
            "canonical_reference_index": card.get("canonical_reference_index"),
            "live_display_order": card.get("live_display_order"),
            "list_title": card.get("list_title"),
            "list_price_text": card.get("list_price_text"),
            "list_year": card.get("list_year"),
            "list_mileage_10k_km": card.get("list_mileage_10k_km"),
            "raw_metadata": card.get("raw_metadata"),
            "card_complete": card.get("card_complete"),
            "incomplete_reason": card.get("incomplete_reason"),
        }
        for card in complete_cards
    ]
    partial_card_summary = [
        {
            "live_display_order": card.get("live_display_order"),
            "list_title": card.get("list_title"),
            "list_price_text": card.get("list_price_text"),
            "raw_metadata": card.get("raw_metadata"),
            "clicked_card_bounds": card.get("clicked_card_bounds"),
            "card_bounds": card.get("card_bounds"),
            "missing_price": not card.get("has_price"),
            "missing_metadata": not card.get("has_metadata"),
            "incomplete_reason": card.get("incomplete_reason"),
        }
        for card in partial_cards
    ]
    same_price_summary = _s10_same_price_group_summary(complete_cards)
    target_card_visible = None
    target_card_matches_expected = None
    target_partial_card_visible = None
    selected_reference_card_fully_visible = None
    selected_reference_card_fields_complete = None
    selected_reference_card_clickable = None
    selected_reference_card_safe_click_area = None
    selected_reference_card_gate_passed = None
    selected_reference_card_stop_code = None
    selected_reference_card_gate_reason = None
    selected_reference_card_identity_preserved = None
    selected_reference_card_autoscroll_candidate = None
    if target_reference_index is not None:
        target_cards = [card for card in complete_cards if int(card.get("reference_index") or -1) == int(target_reference_index)]
        expected_matches: list[dict[str, Any]] = []
        expected_partial_matches: list[dict[str, Any]] = []
        if expected_card:
            for card in complete_cards:
                matched, _match_reasons = _s10_card_matches_expected_continuation(card, expected_card)
                if matched:
                    expected_matches.append(card)
            for card in partial_cards:
                matched, _match_reasons = _s10_partial_card_may_match_expected(card, expected_card)
                if matched:
                    expected_partial_matches.append(card)
            if not target_cards and len(expected_matches) == 1:
                target_cards = expected_matches
                selected_reference_card_identity_preserved = True
                selected_reference_card_autoscroll_candidate = True
        target_card_visible = len(target_cards) == 1
        if target_card_visible and expected_card:
            target_card_matches_expected = _s10_card_matches_expected_continuation(target_cards[0], expected_card)[0]
            if selected_reference_card_identity_preserved is None:
                selected_reference_card_identity_preserved = bool(target_card_matches_expected)
        selected_partial_cards = [
            card
            for card in partial_cards
            if _safe_int(card.get("live_display_order"), default=-1) == int(target_reference_index)
        ]
        if not selected_partial_cards and len(expected_partial_matches) == 1:
            selected_partial_cards = expected_partial_matches
            selected_reference_card_identity_preserved = True
            selected_reference_card_autoscroll_candidate = True
        target_partial_card_visible = bool(selected_partial_cards)
        if len(target_cards) == 1:
            selected_card = target_cards[0]
            selected_reference_card_fully_visible = selected_card.get("card_fully_visible") is True
            selected_reference_card_fields_complete = selected_card.get("card_complete") is True
            selected_reference_card_clickable = _valid_bounds(selected_card.get("clicked_card_bounds"))
            selected_reference_card_safe_click_area = (
                selected_reference_card_fully_visible
                and selected_reference_card_clickable
                and not selected_card.get("incomplete_reason")
            )
            selected_reference_card_gate_passed = bool(
                selected_reference_card_fully_visible
                and selected_reference_card_fields_complete
                and selected_reference_card_clickable
                and selected_reference_card_safe_click_area
            )
            selected_reference_card_gate_reason = (
                "selected_reference_card_complete_safe_clickable"
                if selected_reference_card_gate_passed
                else "selected_reference_card_not_safe_clickable"
            )
        elif selected_partial_cards:
            selected_partial = selected_partial_cards[0]
            selected_reference_card_fully_visible = False
            selected_reference_card_fields_complete = False
            selected_reference_card_clickable = _valid_bounds(selected_partial.get("clicked_card_bounds"))
            selected_reference_card_safe_click_area = False
            selected_reference_card_gate_passed = False
            if selected_partial.get("card_fully_visible") is True:
                selected_reference_card_stop_code = "REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL"
                selected_reference_card_gate_reason = "selected_reference_card_fields_missing"
            else:
                selected_reference_card_stop_code = "SELECTED_REFERENCE_CARD_NOT_FULLY_VISIBLE"
                selected_reference_card_gate_reason = "selected_reference_card_partial_visible_or_missing_fields"
        else:
            selected_reference_card_fully_visible = False
            selected_reference_card_fields_complete = False
            selected_reference_card_clickable = False
            selected_reference_card_safe_click_area = False
            selected_reference_card_gate_passed = False
            selected_reference_card_stop_code = "REFERENCE_CARD_BINDING_NOT_UNIQUE"
            selected_reference_card_gate_reason = "selected_reference_card_not_uniquely_bound"
    missing_reasons: list[str] = []
    if not has_price_sort:
        missing_reasons.append("missing_price_low_to_high_sort_signal")
    if not has_vehicle_cards:
        missing_reasons.append("missing_vehicle_cards")
    if has_detail_report_page_signals:
        missing_reasons.append("detail_report_page_signals_present")
    reliable = has_price_sort and has_vehicle_cards and not has_detail_report_page_signals
    return {
        "reliable": reliable,
        "source_reliable": reliable,
        "source_reliable_reason": "reliable_s10_sorted_vehicle_list" if reliable else ";".join(missing_reasons),
        "has_price_low_to_high": has_price_sort,
        "vehicle_card_count": len(complete_cards),
        "partial_card_count": len(partial_cards),
        "bottom_partial_card_present": bool(partial_cards),
        "partial_card_allowed_by_contract": True,
        "s10_partial_card_allowed_page_contract": "S10_PARTIAL_CARD_ALLOWED_PAGE_CONTRACT_UPDATED",
        "has_vehicle_cards": has_vehicle_cards,
        "has_multiple_vehicle_cards": len(complete_cards) >= 2,
        "visible_cards": visible_card_summary,
        "partial_card_candidates": partial_card_summary,
        "s10_order_rule": "price_asc_mileage_desc_for_same_price",
        "canonical_reference_order": visible_card_summary,
        "raw_visible_cards_count": order_audit.get("raw_visible_cards_count"),
        "trisame_cards_count": order_audit.get("trisame_cards_count"),
        "trisame_count": order_audit.get("trisame_cards_count"),
        "excluded_non_trisame_cards_count": order_audit.get("excluded_non_trisame_cards_count"),
        "excluded_non_trisame_cards": order_audit.get("excluded_non_trisame_cards") or [],
        "non_trisame_section_detected": order_audit.get("non_trisame_section_detected"),
        "non_trisame_section_title": order_audit.get("non_trisame_section_title"),
        "boundary_text": order_audit.get("boundary_text"),
        "boundary_node_bounds": order_audit.get("boundary_node_bounds"),
        "cards_after_boundary_excluded_count": order_audit.get("cards_after_boundary_excluded_count"),
        **same_price_summary,
        "target_reference_index": target_reference_index,
        "target_card_visible": target_card_visible,
        "target_partial_card_visible": target_partial_card_visible,
        "target_card_matches_expected": target_card_matches_expected,
        "selected_reference_card_fully_visible": selected_reference_card_fully_visible,
        "selected_reference_card_fields_complete": selected_reference_card_fields_complete,
        "selected_reference_card_clickable": selected_reference_card_clickable,
        "selected_reference_card_safe_click_area": selected_reference_card_safe_click_area,
        "selected_reference_card_gate_passed": selected_reference_card_gate_passed,
        "selected_reference_card_stop_code": selected_reference_card_stop_code,
        "selected_reference_card_gate_reason": selected_reference_card_gate_reason,
        "selected_reference_card_identity_preserved": selected_reference_card_identity_preserved,
        "s10_selected_card_autoscroll_candidate": selected_reference_card_autoscroll_candidate,
        "s10_reference_index_scope": "canonical" if selected_reference_card_identity_preserved else "viewport_local",
        "s10_viewport_renumbering_detected": bool(
            target_reference_index is not None
            and target_reference_index not in [card.get("reference_index") for card in complete_cards]
            and (target_card_visible or target_partial_card_visible or bool(complete_cards))
        ),
        "target_canonical_reference_index": target_reference_index,
        "visible_live_display_orders": [card.get("live_display_order") for card in complete_cards],
        "visible_canonical_matches": [
            _s10_card_identity_trace(card)
            for card in complete_cards
            if expected_card and _s10_card_matches_expected_continuation(card, expected_card)[0]
        ],
        "s10_absolute_identity_scan_started": bool(expected_card),
        "s10_absolute_identity_scan_found": bool(target_card_visible),
        "detail_report_page_signals": report_signals,
        "bottom_detail_actions": bottom_detail_actions,
        "has_detail_report_page_signals": has_detail_report_page_signals,
        "visible_text_digest": visible_texts[:40],
    }


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
    reference_index = _safe_int(
        snapshot.get("target_reference_index") or snapshot.get("current_reference_index") or 1,
        default=1,
    )
    return _select_s10_reference_card_by_index(snapshot, reference_index)


def _first_stage_expected_reference_card(first_stage_evidence: dict[str, Any], reference_index: int) -> dict[str, Any]:
    cards = first_stage_evidence.get("canonical_reference_order")
    if not isinstance(cards, list) or not cards:
        cards = first_stage_evidence.get("same_source_cards") or []
    if not isinstance(cards, list) or reference_index <= 0 or reference_index > len(cards):
        return {}
    card = cards[reference_index - 1]
    if not isinstance(card, dict):
        return {}
    expected = dict(card)
    expected.setdefault("canonical_reference_index", reference_index)
    expected.setdefault("first_stage_card_order", reference_index)
    expected.setdefault("reference_index", reference_index)
    title = expected.get("list_title") or expected.get("selected_card_title") or expected.get("title") or ""
    price_text = expected.get("list_price_text") or expected.get("selected_card_price") or ""
    metadata = expected.get("raw_metadata") or expected.get("selected_card_metadata") or expected.get("year_mileage_text") or ""
    city = expected.get("city") or expected.get("selected_card_city") or ""
    identity_parts = [
        str(reference_index),
        str(title).strip(),
        str(price_text or expected.get("list_price_10k") or "").strip(),
        str(expected.get("list_year") or "").strip(),
        str(expected.get("list_mileage_10k_km") or "").strip(),
        str(metadata).strip(),
        str(city).strip(),
    ]
    identity_key = "|".join(part for part in identity_parts if part)
    if identity_key:
        expected.setdefault("identity_key", identity_key)
        expected.setdefault("physical_card_signature", identity_key)
    return expected


def _expected_reference_card_with_continuation_context(
    first_stage_evidence: dict[str, Any],
    reference_index: int,
    continuation_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    expected = _first_stage_expected_reference_card(first_stage_evidence, reference_index)
    plan = continuation_plan if isinstance(continuation_plan, dict) else {}
    recollect_index = _safe_int(plan.get("recollect_reference_index"), default=0)
    continue_reason = str(plan.get("continue_reason") or "")
    if not (
        recollect_index == int(reference_index)
        or continue_reason == "BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
        or plan.get("final_reference_recollect_required") is True
    ):
        return expected
    expected = dict(expected)
    for key in (
        "recollect_reference_index",
        "recollect_reason",
        "boundary_reference_index",
        "boundary_reference_score",
        "boundary_reference_price_yuan",
        "target_score",
        "final_reference_candidate_index",
        "final_reference_candidate_status",
        "continue_reason",
    ):
        if plan.get(key) is not None:
            expected[key] = plan.get(key)
    expected["boundary_previous_recollect_required"] = True
    expected["final_reference_recollect_required"] = True
    expected["recollect_mode"] = True
    expected["reference_selection_rule"] = REFERENCE_SELECTION_RULE
    return expected


def _format_reference_price_text(price_10k: Any) -> str:
    try:
        value = float(price_10k)
    except (TypeError, ValueError):
        return ""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}\u4e07"


def _reference_identity_complete(reference: dict[str, Any] | None) -> bool:
    if not isinstance(reference, dict):
        return False
    index = _safe_int(reference.get("reference_index") or reference.get("selected_reference_index"), default=0)
    price = reference.get("selected_card_price") or reference.get("list_price_text") or reference.get("list_price_10k")
    metadata = reference.get("selected_card_metadata") or reference.get("raw_metadata") or reference.get("year_mileage_text")
    title = reference.get("selected_card_title") or reference.get("list_title") or reference.get("title")
    return bool(index > 0 and price not in (None, "") and metadata not in (None, "") and title not in (None, ""))


def _hydrate_reference_identity_from_expected_card(
    reference: dict[str, Any] | None,
    expected_card: dict[str, Any] | None,
    reference_index: int,
    *,
    source: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    hydrated = dict(reference or {})
    expected = dict(expected_card or {})
    evidence: dict[str, Any] = {
        "reference_identity_hydration_attempted": True,
        "reference_identity_hydration_source": source,
        "requested_reference_index": reference_index,
        "expected_card_available": bool(expected),
        "identity_complete_before": _reference_identity_complete(hydrated),
    }
    if reference_index > 0:
        hydrated.setdefault("reference_index", reference_index)
        hydrated.setdefault("selected_reference_index", reference_index)
    if expected:
        price_10k = expected.get("list_price_10k")
        price_text = expected.get("list_price_text") or expected.get("selected_card_price") or _format_reference_price_text(price_10k)
        metadata = (
            expected.get("raw_metadata")
            or expected.get("selected_card_metadata")
            or expected.get("year_mileage_text")
            or expected.get("metadata")
            or ""
        )
        title = expected.get("list_title") or expected.get("selected_card_title") or expected.get("title") or ""
        city = expected.get("city") or expected.get("selected_card_city") or ""
        if title:
            hydrated.setdefault("list_title", title)
            hydrated.setdefault("selected_card_title", title)
            hydrated.setdefault("title", title)
        if price_10k is not None:
            hydrated.setdefault("list_price_10k", price_10k)
        if price_text:
            hydrated.setdefault("list_price_text", price_text)
            hydrated.setdefault("selected_card_price", price_text)
        if metadata:
            hydrated.setdefault("raw_metadata", metadata)
            hydrated.setdefault("selected_card_metadata", metadata)
        if city:
            hydrated.setdefault("city", city)
            hydrated.setdefault("selected_card_city", city)
        for key in (
            "list_year",
            "list_mileage_10k_km",
            "canonical_reference_index",
            "live_display_order",
            "reference_key",
            "listing_key",
            "clicked_card_text_digest",
        ):
            if expected.get(key) not in (None, "", []):
                hydrated.setdefault(key, expected.get(key))
        if not hydrated.get("reference_key"):
            key_parts = [
                str(hydrated.get("list_title") or hydrated.get("selected_card_title") or ""),
                str(hydrated.get("list_price_10k") or hydrated.get("list_price_text") or ""),
                str(hydrated.get("raw_metadata") or hydrated.get("selected_card_metadata") or ""),
            ]
            key = "|".join(part for part in key_parts if part)
            if key:
                hydrated["reference_key"] = key
                hydrated.setdefault("listing_key", key)
    evidence.update(
        {
            "identity_complete_after": _reference_identity_complete(hydrated),
            "hydrated_reference_index": hydrated.get("reference_index") or hydrated.get("selected_reference_index"),
            "hydrated_selected_card_price": hydrated.get("selected_card_price") or hydrated.get("list_price_text"),
            "hydrated_selected_card_metadata": hydrated.get("selected_card_metadata") or hydrated.get("raw_metadata"),
            "hydrated_selected_card_title": hydrated.get("selected_card_title") or hydrated.get("list_title"),
        }
    )
    return hydrated, evidence


def _hydrate_current_reference_identity_for_in_flight_context(
    context: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    reference_index = _safe_int(context.get("current_reference_index"), default=0)
    current_reference = context.get("current_reference") if isinstance(context.get("current_reference"), dict) else {}
    first_stage_evidence = context.get("first_stage_evidence") if isinstance(context.get("first_stage_evidence"), dict) else {}
    expected_card = _first_stage_expected_reference_card(first_stage_evidence, reference_index)
    hydrated, evidence = _hydrate_reference_identity_from_expected_card(
        current_reference,
        expected_card,
        reference_index,
        source=reason,
    )
    context["current_reference"] = hydrated
    context.setdefault("reference_identity_hydration_traces", []).append(evidence)
    if evidence.get("identity_complete_after") is True:
        return {
            **evidence,
            "identity_hydration_ok": True,
            "stop_code": "",
        }
    return {
        **evidence,
        "identity_hydration_ok": False,
        "stop_code": "S13_IN_FLIGHT_REFERENCE_IDENTITY_MISSING",
        "missing_identity_fields": [
            name
            for name, value in {
                "reference_index": hydrated.get("reference_index") or hydrated.get("selected_reference_index"),
                "selected_card_price": hydrated.get("selected_card_price") or hydrated.get("list_price_text") or hydrated.get("list_price_10k"),
                "selected_card_metadata": hydrated.get("selected_card_metadata") or hydrated.get("raw_metadata"),
                "selected_card_title": hydrated.get("selected_card_title") or hydrated.get("list_title") or hydrated.get("title"),
            }.items()
            if value in (None, "")
        ],
    }


def _reference_identity_summary(reference: dict[str, Any] | None, reference_index: int = 0) -> dict[str, Any]:
    reference = dict(reference or {})
    index = _safe_int(
        reference.get("reference_index")
        or reference.get("selected_reference_index")
        or reference.get("canonical_reference_index")
        or reference_index,
        default=0,
    )
    return {
        "reference_index": index,
        "title": str(
            reference.get("list_title")
            or reference.get("selected_card_title")
            or reference.get("title")
            or reference.get("vehicle_title")
            or ""
        ).strip(),
        "price_yuan": _reference_price_yuan(
            reference.get("selected_card_price")
            or reference.get("list_price_text")
            or reference.get("list_price_10k")
            or reference.get("price_10k")
            or reference.get("price_yuan")
        ),
        "price_10k": reference.get("list_price_10k"),
        "price_text": str(reference.get("list_price_text") or reference.get("selected_card_price") or "").strip(),
        "year": reference.get("list_year"),
        "mileage_10k_km": reference.get("list_mileage_10k_km"),
        "metadata": str(
            reference.get("raw_metadata")
            or reference.get("selected_card_metadata")
            or reference.get("year_mileage_text")
            or reference.get("metadata")
            or ""
        ).strip(),
        "city": str(reference.get("city") or reference.get("selected_card_city") or "").strip(),
        "reference_key": str(reference.get("reference_key") or reference.get("listing_key") or "").strip(),
        "reference_identity_summary_function_signature_checked": True,
        "v149_reference_identity_summary_duplicate_removed": True,
    }


def _snapshot_reference_identity(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = dict(snapshot or {})
    explicit = snapshot.get("actual_reference_identity")
    if isinstance(explicit, dict):
        explicit_summary = _reference_identity_summary(explicit)
    else:
        explicit_summary = {}
    visible_texts = [str(item) for item in (snapshot.get("visible_texts") or []) if str(item).strip()]
    visible_blob = "\n".join(visible_texts) or str(snapshot.get("visible_blob") or "") or str(snapshot.get("fresh_xml") or "")
    vehicle_no_match = re.search(r"(?:vehicle[_\s-]*id|listing[_\s-]*id|车源编号|車源編號)[:：=\s]*([A-Za-z0-9_-]{4,})", visible_blob, re.IGNORECASE)
    mileage_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:万公里|w\s*km|万\s*km)", visible_blob, re.IGNORECASE)
    price_match = re.search(r"(\d+(?:\.\d+)?)\s*万", visible_blob)
    visible_digest = _sha256_text("|".join(visible_texts))
    xml_digest = _sha256_text(str(snapshot.get("fresh_xml") or ""))
    screenshot_digest = _sha256_file(snapshot.get("screenshot_path"))
    signature_basis = "|".join(
        str(item)
        for item in [
            explicit_summary.get("reference_index") or "",
            explicit_summary.get("title") or "",
            explicit_summary.get("price_10k") or "",
            explicit_summary.get("mileage_10k_km") or "",
            vehicle_no_match.group(1) if vehicle_no_match else "",
            mileage_match.group(1) if mileage_match else "",
            price_match.group(1) if price_match else "",
            visible_digest,
            xml_digest,
        ]
        if str(item) != ""
    )
    return {
        **explicit_summary,
        "vehicle_no": vehicle_no_match.group(1) if vehicle_no_match else "",
        "visible_mileage_10k_km": float(mileage_match.group(1)) if mileage_match else None,
        "visible_price_10k": float(price_match.group(1)) if price_match else None,
        "visible_text_digest": visible_digest,
        "xml_digest": xml_digest,
        "screenshot_digest": screenshot_digest,
        "actual_page_signature": _sha256_text(signature_basis) if signature_basis else "",
        "visible_text_sample": visible_texts[:20],
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "xml_path": str(snapshot.get("xml_path") or ""),
    }


def _destination_identity_matches(expected: dict[str, Any], actual: dict[str, Any], *, clicked_text: str = "") -> bool:
    if not expected:
        return False
    if expected.get("reference_index") and actual.get("reference_index") and int(expected["reference_index"]) == int(actual["reference_index"]):
        return True
    expected_title = str(expected.get("title") or "").strip()
    actual_title = str(actual.get("title") or "").strip()
    if expected_title and (expected_title == actual_title or expected_title == str(clicked_text or "").strip()):
        expected_mileage = expected.get("mileage_10k_km")
        actual_mileage = actual.get("mileage_10k_km") if actual.get("mileage_10k_km") is not None else actual.get("visible_mileage_10k_km")
        if actual_mileage is None or expected_mileage is None or _float_same(expected_mileage, actual_mileage, tolerance=0.05):
            return True
    expected_key = str(expected.get("reference_key") or "").strip()
    actual_key = str(actual.get("reference_key") or "").strip()
    return bool(expected_key and actual_key and expected_key == actual_key)


def _reference_page_signature_reused(
    context: dict[str, Any],
    *,
    reference_index: int,
    actual_page_signature: str,
) -> dict[str, Any]:
    if not actual_page_signature:
        return {"same_page_signature_reused": False, "matched_reference_index": None}
    registry = context.setdefault("reference_physical_page_signature_registry", {})
    for index_text, signature in registry.items():
        if str(signature) == actual_page_signature and _safe_int(index_text, default=0) != int(reference_index):
            return {"same_page_signature_reused": True, "matched_reference_index": _safe_int(index_text, default=0)}
    for item in context.get("reference_history") or []:
        if not isinstance(item, dict):
            continue
        proof = item.get("physical_ui_transition_proof") if isinstance(item.get("physical_ui_transition_proof"), dict) else {}
        prior_signature = str(proof.get("actual_page_signature") or item.get("actual_page_signature") or "")
        prior_index = _safe_int(item.get("reference_index") or item.get("selected_reference_index"), default=0)
        if prior_signature == actual_page_signature and prior_index and prior_index != int(reference_index):
            return {"same_page_signature_reused": True, "matched_reference_index": prior_index}
    registry[str(reference_index)] = actual_page_signature
    return {"same_page_signature_reused": False, "matched_reference_index": None}


def _build_reference_physical_ui_transition_proof(
    context: dict[str, Any],
    *,
    reference_index: int,
    expected_card: dict[str, Any] | None,
    from_page: str,
    to_page: str,
    transition_context: str,
    before_snapshot: dict[str, Any] | None,
    after_snapshot: dict[str, Any] | None,
    click_evidence: dict[str, Any] | None = None,
    page_changed_after_click: bool = False,
    next_card_click_verified: bool = False,
    destination_identity_matched: bool | None = None,
    return_to_reliable_s10_verified: bool | None = None,
) -> dict[str, Any]:
    expected_identity = _reference_identity_summary(expected_card or {}, reference_index)
    actual_identity = _snapshot_reference_identity(after_snapshot or {})
    click_evidence = dict(click_evidence or {})
    clicked_text = str(click_evidence.get("clicked_text") or "")
    identity_matched = (
        _destination_identity_matches(expected_identity, actual_identity, clicked_text=clicked_text)
        if destination_identity_matched is None
        else bool(destination_identity_matched)
    )
    reuse = _reference_page_signature_reused(
        context,
        reference_index=reference_index,
        actual_page_signature=str(actual_identity.get("actual_page_signature") or ""),
    )
    proof = {
        "proof_version": REFERENCE_PHYSICAL_UI_TRANSITION_PROOF_VERSION,
        "transition_context": transition_context,
        "from_page": from_page,
        "to_page": to_page,
        "reference_index": reference_index,
        "expected_reference_identity": expected_identity,
        "actual_destination_identity": actual_identity,
        "actual_page_signature": actual_identity.get("actual_page_signature"),
        "before_xml_digest": _sha256_text(str((before_snapshot or {}).get("fresh_xml") or "")),
        "after_xml_digest": actual_identity.get("xml_digest"),
        "before_screenshot_digest": _sha256_file((before_snapshot or {}).get("screenshot_path")),
        "after_screenshot_digest": actual_identity.get("screenshot_digest"),
        "click_evidence": click_evidence,
        "next_card_click_verified": bool(next_card_click_verified),
        "page_changed_after_click": bool(page_changed_after_click),
        "destination_identity_matched": identity_matched,
        "return_to_reliable_s10_verified": bool(return_to_reliable_s10_verified),
        "reference_identity_summary_function_signature_checked": True,
        "v149_reference_identity_summary_duplicate_removed": True,
        **reuse,
    }
    proof["physical_evidence_ok"] = bool(
        proof["next_card_click_verified"]
        and proof["page_changed_after_click"]
        and proof["destination_identity_matched"]
        and not proof["same_page_signature_reused"]
    )
    return proof


def _reference_physical_ui_transition_proof_gate(
    reference: dict[str, Any] | None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reference = dict(reference or {})
    proof = reference.get("physical_ui_transition_proof") if isinstance(reference.get("physical_ui_transition_proof"), dict) else {}
    signature = str(proof.get("actual_page_signature") or reference.get("actual_page_signature") or "")
    index = _safe_int(reference.get("reference_index") or reference.get("selected_reference_index"), default=0)
    reused_index = None
    if signature:
        for item in history or []:
            if not isinstance(item, dict):
                continue
            item_proof = item.get("physical_ui_transition_proof") if isinstance(item.get("physical_ui_transition_proof"), dict) else {}
            item_signature = str(item_proof.get("actual_page_signature") or item.get("actual_page_signature") or "")
            item_index = _safe_int(item.get("reference_index") or item.get("selected_reference_index"), default=0)
            if item_signature == signature and item_index and item_index != index:
                reused_index = item_index
                break
    required_flags = {
        "physical_evidence_ok": proof.get("physical_evidence_ok") is True,
        "next_card_click_verified": proof.get("next_card_click_verified") is True,
        "page_changed_after_click": proof.get("page_changed_after_click") is True,
        "destination_identity_matched": proof.get("destination_identity_matched") is True,
        "same_page_signature_not_reused": not proof.get("same_page_signature_reused") and reused_index is None,
    }
    ok = all(required_flags.values())
    if ok:
        code = ""
    elif not required_flags["same_page_signature_not_reused"]:
        code = REFERENCE_HISTORY_PHYSICAL_SIGNATURE_REUSED
    elif proof.get("destination_identity_matched") is False:
        code = REFERENCE_DESTINATION_IDENTITY_NOT_MATCHED
    else:
        code = REFERENCE_HISTORY_PHYSICAL_PROOF_MISSING
    return {
        "physical_ui_transition_proof_required": True,
        "physical_ui_transition_proof_present": bool(proof),
        "physical_ui_transition_proof_ok": ok,
        "required_flags": required_flags,
        "actual_page_signature": signature,
        "same_page_signature_reused_with_reference_index": reused_index,
        "stop_code": code,
        "proof": proof,
    }


def _safe_reference_history_with_current_reference(
    context: dict[str, Any],
    *,
    purpose: str,
    require_identity: bool = True,
    require_physical_evidence: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    history = [dict(item) for item in (context.get("reference_history") or []) if isinstance(item, dict)]
    current_reference = context.get("current_reference") if isinstance(context.get("current_reference"), dict) else {}
    gate: dict[str, Any] = {
        "reference_history_write_gate": True,
        "purpose": purpose,
        "current_reference_present": bool(current_reference),
        "require_identity": require_identity,
        "require_physical_evidence": require_physical_evidence,
        "current_reference_identity_complete_before": _reference_identity_complete(current_reference),
    }
    if not current_reference:
        gate.update(
            {
                "current_reference_written": False,
                "reference_history_write_blocked": False,
                "reference_history_length": len(history),
            }
        )
        context["reference_history_write_gate"] = gate
        return history, gate
    if require_identity and not _reference_identity_complete(current_reference):
        hydration = _hydrate_current_reference_identity_for_in_flight_context(
            context,
            reason=f"reference_history_write:{purpose}",
        )
        current_reference = context.get("current_reference") if isinstance(context.get("current_reference"), dict) else {}
        gate["identity_hydration"] = hydration
    identity_complete = _reference_identity_complete(current_reference)
    gate["current_reference_identity_complete_after"] = identity_complete
    if require_identity and not identity_complete:
        gate.update(
            {
                "current_reference_written": False,
                "reference_history_write_blocked": True,
                "reference_history_write_block_code": "REFERENCE_HISTORY_ENTRY_BLOCKED_BY_MISSING_REFERENCE_INDEX",
                "reference_history_length": len(history),
            }
        )
        context["reference_history_write_gate"] = gate
        context["reference_history_entry_blocked_code"] = gate["reference_history_write_block_code"]
        context.setdefault("reference_history_write_block_traces", []).append(dict(gate))
        return history, gate
    physical_gate = _reference_physical_ui_transition_proof_gate(current_reference, history)
    gate["physical_ui_transition_proof_gate"] = physical_gate
    if require_physical_evidence and not physical_gate.get("physical_ui_transition_proof_ok"):
        gate.update(
            {
                "current_reference_written": False,
                "reference_history_write_blocked": True,
                "reference_history_write_block_code": physical_gate.get("stop_code") or REFERENCE_HISTORY_PHYSICAL_PROOF_MISSING,
                "reference_history_length": len(history),
            }
        )
        context["reference_history_write_gate"] = gate
        context["reference_history_entry_blocked_code"] = gate["reference_history_write_block_code"]
        context.setdefault("reference_history_write_block_traces", []).append(dict(gate))
        return history, gate
    history.append(dict(current_reference))
    gate.update(
        {
            "current_reference_written": True,
            "reference_history_write_blocked": False,
            "reference_history_length": len(history),
        }
    )
    context["reference_history_write_gate"] = gate
    context.setdefault("reference_history_write_traces", []).append(dict(gate))
    return history, gate


def _raise_if_reference_history_write_blocked(
    context: dict[str, Any],
    gate: dict[str, Any],
    *,
    page: str,
    message: str,
) -> None:
    if gate.get("reference_history_write_blocked") is not True:
        return
    code = str(gate.get("reference_history_write_block_code") or REFERENCE_HISTORY_PHYSICAL_PROOF_MISSING)
    issue = context["issues"].record(
        code,
        page,
        message,
        {
            "reference_history_write_gate": gate,
            "current_reference": context.get("current_reference"),
            "reference_history": context.get("reference_history"),
            "physical_ui_transition_proof": (context.get("current_reference") or {}).get("physical_ui_transition_proof")
            if isinstance(context.get("current_reference"), dict)
            else None,
        },
        "manual_review",
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def _all_references_exhausted_physical_gate(
    history: list[dict[str, Any]],
    *,
    trisame_count: int | None,
    next_reference_index: int,
) -> dict[str, Any]:
    physical_history = [
        item
        for item in history
        if isinstance(item, dict) and _reference_physical_ui_transition_proof_gate(item, []).get("physical_ui_transition_proof_ok")
    ]
    missing_indices = [
        _safe_int(item.get("reference_index") or item.get("selected_reference_index"), default=0)
        for item in history
        if isinstance(item, dict) and not _reference_physical_ui_transition_proof_gate(item, []).get("physical_ui_transition_proof_ok")
    ]
    required_count = int(trisame_count or 0) if trisame_count is not None else len(history)
    ok = bool(
        next_reference_index > 0
        and (trisame_count is None or next_reference_index > int(trisame_count))
        and len(physical_history) >= required_count
        and not missing_indices
    )
    return {
        "all_references_exhausted_physical_gate": True,
        "trisame_count": trisame_count,
        "next_reference_index": next_reference_index,
        "logical_reference_history_count": len(history),
        "physical_reference_history_count": len(physical_history),
        "required_physical_reference_count": required_count,
        "missing_physical_evidence_reference_indices": missing_indices,
        "physical_evidence_ok": ok,
        "stop_code": "" if ok else ALL_REFERENCES_EXHAUSTED_BLOCKED_BY_MISSING_PHYSICAL_EVIDENCE,
    }


def _expected_reference_identity_fields(expected: dict[str, Any] | None) -> dict[str, Any]:
    expected = dict(expected or {})
    return {
        "title": str(
            expected.get("list_title")
            or expected.get("selected_card_title")
            or expected.get("title")
            or expected.get("vehicle_title")
            or ""
        ).strip(),
        "price_text": str(expected.get("list_price_text") or expected.get("selected_card_price") or "").strip(),
        "price_10k": expected.get("list_price_10k") if expected.get("list_price_10k") is not None else expected.get("price_10k"),
        "year": expected.get("list_year") if expected.get("list_year") is not None else expected.get("year"),
        "mileage_10k_km": (
            expected.get("list_mileage_10k_km")
            if expected.get("list_mileage_10k_km") is not None
            else expected.get("mileage_10k_km")
        ),
        "metadata": str(
            expected.get("raw_metadata")
            or expected.get("selected_card_metadata")
            or expected.get("year_mileage_text")
            or expected.get("metadata")
            or ""
        ).strip(),
        "city": str(expected.get("city") or expected.get("selected_card_city") or "").strip(),
    }


def _s10_reference_identity_present(identity: dict[str, Any]) -> bool:
    return any(
        identity.get(key) not in (None, "")
        for key in ("title", "price_text", "price_10k", "year", "mileage_10k_km", "metadata", "city")
    )


def _s10_card_identity_trace(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "reference_index": card.get("reference_index"),
        "canonical_reference_index": card.get("canonical_reference_index"),
        "live_display_order": card.get("live_display_order"),
        "list_title": card.get("list_title"),
        "list_price_text": card.get("list_price_text"),
        "list_price_10k": card.get("list_price_10k"),
        "list_year": card.get("list_year"),
        "list_mileage_10k_km": card.get("list_mileage_10k_km"),
        "raw_metadata": card.get("raw_metadata"),
        "city": card.get("city"),
        "card_complete": card.get("card_complete"),
        "incomplete_reason": card.get("incomplete_reason"),
    }


def _s10_card_matches_expected_continuation(card: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    identity = _expected_reference_identity_fields(expected)
    if not _s10_reference_identity_present(identity):
        return False, ["expected_identity_missing"]
    if identity["title"] and str(card.get("list_title") or "").strip() != identity["title"]:
        reasons.append("title_mismatch")
    if identity["price_10k"] is not None and not _float_same(card.get("list_price_10k"), identity["price_10k"]):
        reasons.append("price_mismatch")
    if identity["price_text"] and str(card.get("list_price_text") or "").strip() != identity["price_text"]:
        reasons.append("price_text_mismatch")
    if identity["year"] is not None and card.get("list_year") != identity["year"]:
        reasons.append("year_mismatch")
    if identity["mileage_10k_km"] is not None and not _float_same(card.get("list_mileage_10k_km"), identity["mileage_10k_km"]):
        reasons.append("mileage_mismatch")
    expected_meta = identity["metadata"]
    if expected_meta:
        digest = " ".join(str(item) for item in card.get("clicked_card_text_digest") or [])
        actual_meta = str(card.get("raw_metadata") or "").strip()
        if expected_meta != actual_meta and expected_meta not in digest:
            reasons.append("metadata_text_mismatch")
    if identity["city"] and str(card.get("city") or "").strip() != identity["city"]:
        reasons.append("city_mismatch")
    return not reasons, reasons


def _s10_partial_card_may_match_expected(card: dict[str, Any], expected: dict[str, Any] | None) -> tuple[bool, list[str]]:
    identity = _expected_reference_identity_fields(expected)
    if not _s10_reference_identity_present(identity):
        return False, ["expected_identity_missing"]
    reasons: list[str] = []
    if identity["title"] and str(card.get("list_title") or "").strip() != identity["title"]:
        reasons.append("title_mismatch")
    if identity["metadata"]:
        actual_meta = str(card.get("raw_metadata") or "").strip()
        digest = " ".join(str(item) for item in card.get("clicked_card_text_digest") or [])
        if identity["metadata"] != actual_meta and identity["metadata"] not in digest:
            reasons.append("metadata_text_mismatch")
    if identity["year"] is not None and card.get("list_year") not in (None, identity["year"]):
        reasons.append("year_mismatch")
    if identity["mileage_10k_km"] is not None and card.get("list_mileage_10k_km") is not None and not _float_same(
        card.get("list_mileage_10k_km"), identity["mileage_10k_km"]
    ):
        reasons.append("mileage_mismatch")
    if identity["city"] and card.get("city") and str(card.get("city") or "").strip() != identity["city"]:
        reasons.append("city_mismatch")
    return not reasons, reasons


def _select_s10_reference_card_by_index(
    snapshot: dict[str, Any],
    target_reference_index: int,
    expected_card: dict[str, Any] | None = None,
    reference_history: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], tuple[int, int]] | None:
    cards = _extract_s10_reference_cards(snapshot)
    if not cards:
        return None
    complete_cards = [card for card in cards if card.get("card_complete") is True]
    partial_cards = [card for card in cards if card.get("card_complete") is not True]
    collected_keys = _collected_reference_keys(reference_history or [])
    processed_indices = _processed_reference_indices(reference_history or [])
    processed_identities = _processed_reference_identity_summary(reference_history or [])
    skip_events: list[dict[str, Any]] = []
    available_cards: list[dict[str, Any]] = []
    for card in complete_cards:
        key = _s10_reference_unique_key(card)
        if key in collected_keys:
            skip_events.append(
                {
                    "reference_index": card.get("reference_index"),
                    "live_display_order": card.get("live_display_order"),
                    "list_title": card.get("list_title"),
                    "list_price_text": card.get("list_price_text"),
                    "raw_metadata": card.get("raw_metadata"),
                    "skip_reason": "REFERENCE_ALREADY_COLLECTED_SKIP_TO_NEXT",
                }
            )
            continue
        available_cards.append(card)
    matched = [card for card in complete_cards if int(card.get("reference_index") or -1) == int(target_reference_index)]
    expected_identity = _expected_reference_identity_fields(expected_card)
    expected_matches: list[dict[str, Any]] = []
    expected_match_reasons: list[dict[str, Any]] = []
    if expected_card and _s10_reference_identity_present(expected_identity):
        for card in complete_cards:
            identity_matched, reasons = _s10_card_matches_expected_continuation(card, expected_card)
            if identity_matched:
                expected_matches.append(card)
            else:
                expected_match_reasons.append(
                    {
                        "candidate": _s10_card_identity_trace(card),
                        "mismatch_reasons": reasons,
                    }
                )
    partial_expected_matches: list[dict[str, Any]] = []
    if expected_card and _s10_reference_identity_present(expected_identity):
        for card in partial_cards:
            partial_matched, _reasons = _s10_partial_card_may_match_expected(card, expected_card)
            if partial_matched:
                partial_expected_matches.append(card)
    visible_reference_indices = [card.get("reference_index") for card in complete_cards]
    visible_live_display_orders = [card.get("live_display_order") for card in complete_cards]
    viewport_renumbering_detected = bool(
        target_reference_index not in {int(card.get("reference_index") or -1) for card in complete_cards}
        and (expected_matches or partial_expected_matches or complete_cards)
    )
    absolute_scan_trace = {
        "s10_reference_index_scope": "canonical" if expected_matches else "viewport_local",
        "s10_viewport_renumbering_detected": viewport_renumbering_detected,
        "target_canonical_reference_index": target_reference_index,
        "visible_reference_indices": visible_reference_indices,
        "visible_live_display_orders": visible_live_display_orders,
        "visible_canonical_matches": [_s10_card_identity_trace(card) for card in expected_matches],
        "expected_identity": expected_identity,
        "s10_absolute_identity_scan_started": bool(expected_card),
        "s10_absolute_identity_scan_attempts": [
            {
                "candidate": item["candidate"],
                "mismatch_reasons": item["mismatch_reasons"],
            }
            for item in expected_match_reasons
        ],
        "s10_absolute_identity_scan_found": len(expected_matches) == 1,
    }
    absolute_identity_binding_used = False
    if len(matched) != 1 and expected_matches:
        if len(expected_matches) > 1:
            return {
                "ok": False,
                "stop_code": "S10_NEXT_REFERENCE_CARD_NOT_UNIQUE",
                "reason": "Multiple visible S10 cards match the target canonical reference identity.",
                "target_reference_index": target_reference_index,
                "visible_card_count": len(complete_cards),
                "processed_reference_indices": sorted(processed_indices),
                "processed_reference_identities": processed_identities,
                "visible_cards": [_s10_card_identity_trace(card) for card in complete_cards],
                "partial_card_candidates": [_s10_card_identity_trace(card) for card in partial_cards],
                **absolute_scan_trace,
                "s10_absolute_identity_scan_stop_reason": "multiple_identity_matches",
            }, (0, 0)
        matched = [expected_matches[0]]
        absolute_identity_binding_used = True
    if len(matched) != 1:
        partial_candidate = None
        partial_matched_by_expected = False
        if partial_expected_matches:
            partial_candidate = partial_expected_matches[0]
            partial_matched_by_expected = True
        elif not expected_card:
            partial_candidate = next(
                (
                    card
                    for card in sorted(partial_cards, key=lambda item: _safe_int(item.get("live_display_order"), default=9999))
                    if _safe_int(card.get("live_display_order"), default=-1) >= int(target_reference_index)
                ),
                None,
            )
        if partial_candidate is not None:
            return {
                "ok": False,
                "stop_code": "S10_NEXT_REFERENCE_PARTIAL_CARD_NOT_COMPLETED",
                "reason": "Target canonical reference card is only partially visible and must be completed before binding.",
                "target_reference_index": target_reference_index,
                "partial_card": {
                    "partial_title": partial_candidate.get("list_title"),
                    "partial_bounds": partial_candidate.get("clicked_card_bounds"),
                    "card_bounds": partial_candidate.get("card_bounds"),
                    "missing_price": not partial_candidate.get("has_price"),
                    "missing_metadata": not partial_candidate.get("has_metadata"),
                    "incomplete_reason": partial_candidate.get("incomplete_reason"),
                    "reason": "bottom_partial_card",
                    "matched_by_expected_identity": partial_matched_by_expected,
                },
                "visible_complete_card_count": len(complete_cards),
                "visible_partial_card_count": len(partial_cards),
                "visible_cards": [_s10_card_identity_trace(card) for card in complete_cards],
                "partial_card_candidates": [_s10_card_identity_trace(card) for card in partial_cards],
                "s10_partial_card_candidate_seen": True,
                "s10_partial_card_candidate_live_display_order": partial_candidate.get("live_display_order"),
                "s10_partial_card_completion_attempted": False,
                "s10_partial_card_completion_success": False,
                **absolute_scan_trace,
                "s10_absolute_identity_scan_stop_reason": "partial_card_requires_completion",
            }, (0, 0)
        return {
            "ok": False,
            "stop_code": "S10_NEXT_REFERENCE_ABSOLUTE_CARD_NOT_FOUND",
            "reason": "Target canonical reference identity was not found in the current S10 live XML viewport.",
            "target_reference_index": target_reference_index,
            "visible_card_count": len(complete_cards),
            "processed_reference_indices": sorted(processed_indices),
            "processed_reference_identities": processed_identities,
            "visible_cards": [_s10_card_identity_trace(card) for card in complete_cards],
            "partial_card_candidates": [_s10_card_identity_trace(card) for card in partial_cards],
            **absolute_scan_trace,
            "s10_absolute_identity_scan_stop_reason": "not_found_in_current_viewport",
        }, (0, 0)
    card = dict(matched[0])
    viewport_reference_index = card.get("reference_index")
    if absolute_identity_binding_used:
        card["viewport_reference_index"] = viewport_reference_index
        card["reference_index"] = target_reference_index
        card["canonical_reference_index"] = target_reference_index
    duplicate_by_index = int(target_reference_index) in processed_indices
    duplicate_by_identity = _s10_reference_unique_key(card) in collected_keys
    duplicate_allowed, duplicate_allowed_reason, duplicate_allowed_trace = _s10_duplicate_reference_reentry_allowed(
        expected_card,
        target_reference_index,
        reference_history,
    )
    if (duplicate_by_index or duplicate_by_identity) and not duplicate_allowed:
        stop_code = (
            "RECOLLECT_REFERENCE_INDEX_NOT_PREVIOUS_OF_BOUNDARY"
            if duplicate_allowed_reason == "RECOLLECT_REFERENCE_INDEX_NOT_PREVIOUS_OF_BOUNDARY"
            else "DUPLICATE_REFERENCE_CLICK_BLOCKED"
        )
        return {
            "ok": False,
            "stop_code": stop_code,
            "reason": "Target next reference resolves to a reference that has already been processed; refusing to click the same S10 card again.",
            "target_reference_index": target_reference_index,
            "duplicate_reference_detected": True,
            "duplicate_detected_by_index": duplicate_by_index,
            "duplicate_detected_by_identity": duplicate_by_identity,
            "duplicate_reference_allowed_reason": duplicate_allowed_reason,
            **duplicate_allowed_trace,
            "processed_reference_indices": sorted(processed_indices),
            "processed_reference_identities": processed_identities,
            "selected_reference_identity": _processed_reference_identity_summary([card])[0],
            "reference_already_collected_skips": skip_events,
            "visible_cards": cards,
        }, (0, 0)
    target = snapshot.get("target_car") or {}
    expected_title = " ".join(
        str(item or "").strip()
        for item in [
            target.get("brand"),
            target.get("series"),
            target.get("year_model") or target.get("model_year"),
            target.get("config_model") or target.get("trim"),
        ]
        if str(item or "").strip()
    )
    title = str(card.get("list_title") or "").strip()
    title_match_audit = match_reference_title_by_normalized_alias(title, target)
    if expected_title and not title_match_audit.get("title_normalized_match"):
        return {
            "ok": False,
            "stop_code": "REFERENCE_CARD_TITLE_NORMALIZED_MISMATCH",
            "reason": "Target indexed card title does not satisfy deterministic normalized alias matching.",
            "target_reference_index": target_reference_index,
            "expected_title": expected_title,
            "actual_title": title,
            **title_match_audit,
            "selected_card": card,
        }, (0, 0)
    expected = expected_card or {}
    if card.get("card_complete") is not True:
        return {
            "ok": False,
            "stop_code": "REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL",
            "reason": "Target indexed card is still incomplete after S10 list completion attempts.",
            "target_reference_index": target_reference_index,
            "selected_card": card,
            "incomplete_reason": card.get("incomplete_reason"),
        }, (0, 0)
    if not str(card.get("list_price_text") or "").strip() or not str(card.get("raw_metadata") or "").strip():
        return {
            "ok": False,
            "stop_code": "REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL",
            "reason": "Target indexed card lacks required price or metadata and cannot be clicked.",
            "target_reference_index": target_reference_index,
            "selected_card": card,
            "missing_price": not bool(str(card.get("list_price_text") or "").strip()),
            "missing_metadata": not bool(str(card.get("raw_metadata") or "").strip()),
        }, (0, 0)
    bounds = card.get("clicked_card_bounds")
    if not _valid_bounds(bounds):
        return {
            "ok": False,
            "stop_code": "REFERENCE_CARD_INDEX_NOT_FOUND",
            "reason": "Target indexed card has invalid title bounds.",
            "target_reference_index": target_reference_index,
            "selected_card": card,
        }, (0, 0)
    enriched = dict(card)
    enriched.update(
        {
            "selected_reference_index": target_reference_index,
            "selected_card_title": card.get("list_title"),
            "selected_card_price": card.get("list_price_text"),
            "selected_card_metadata": card.get("raw_metadata")
            or next((text for text in card.get("clicked_card_text_digest") or [] if " | " in str(text)), ""),
            "selected_card_rank": f"第{target_reference_index}辆",
            "selected_card_live_display_order": card.get("live_display_order"),
            "selected_by": "canonical_reference_order",
            "selected_click_bounds": bounds,
            "select_strategy": "reference_index_bound_card",
            "title_match_audit": title_match_audit,
            "title_match_strategy": title_match_audit.get("title_match_strategy"),
            "title_normalized_match": title_match_audit.get("title_normalized_match"),
            "expected_card": expected,
            "reference_already_collected_skips": skip_events,
            "processed_reference_indices": sorted(processed_indices),
            "processed_reference_identities": processed_identities,
            "current_reference_identity": _processed_reference_identity_summary([card])[0],
            "selected_reference_identity": _processed_reference_identity_summary([card])[0],
            "duplicate_reference_detected": bool(duplicate_by_index or duplicate_by_identity),
            "duplicate_reference_allowed_for_recollect": bool(duplicate_allowed),
            "duplicate_reference_allowed_reason": duplicate_allowed_reason,
            "s10_absolute_reference_binding_success": True,
            "target_canonical_reference_index": target_reference_index,
            "viewport_reference_index": card.get("viewport_reference_index"),
            "s10_binding_identity_matched": bool(
                absolute_identity_binding_used
                or not expected_card
                or _s10_card_matches_expected_continuation(card, expected_card)[0]
            ),
            "s10_partial_card_completed": False,
            "s10_reference_index_scope": "canonical" if absolute_identity_binding_used else "canonical_reference_order",
            "s10_viewport_renumbering_detected": viewport_renumbering_detected,
            "visible_live_display_orders": visible_live_display_orders,
            "visible_canonical_matches": [_s10_card_identity_trace(item) for item in expected_matches],
            "s10_absolute_identity_scan_started": bool(expected_card),
            "s10_absolute_identity_scan_found": bool(absolute_identity_binding_used or len(matched) == 1),
            "s10_absolute_identity_scan_stop_reason": "matched_expected_identity"
            if absolute_identity_binding_used
            else "matched_canonical_reference_index",
            **duplicate_allowed_trace,
        }
    )
    return enriched, _center(bounds)


def _select_s10_reference_card_by_expected_after_partial_scroll(
    snapshot: dict[str, Any],
    target_reference_index: int,
    expected_card: dict[str, Any] | None,
    reference_history: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], tuple[int, int]] | None:
    if not expected_card:
        return None
    cards = [card for card in _extract_s10_reference_cards(snapshot) if card.get("card_complete") is True]
    collected_keys = _collected_reference_keys(reference_history or [])
    skip_events: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    duplicate_allowed, duplicate_allowed_reason, duplicate_allowed_trace = _s10_duplicate_reference_reentry_allowed(
        expected_card,
        target_reference_index,
        reference_history,
    )
    for card in cards:
        if _s10_reference_unique_key(card) in collected_keys and not duplicate_allowed:
            skip_events.append(
                {
                    "reference_index": card.get("reference_index"),
                    "live_display_order": card.get("live_display_order"),
                    "list_title": card.get("list_title"),
                    "list_price_text": card.get("list_price_text"),
                    "raw_metadata": card.get("raw_metadata"),
                    "skip_reason": "REFERENCE_ALREADY_COLLECTED_SKIP_TO_NEXT",
                }
            )
            continue
        matched, _reasons = _s10_card_matches_expected_continuation(card, expected_card)
        if matched:
            matches.append(card)
    if len(matches) != 1:
        return None
    card = matches[0]
    bounds = card.get("clicked_card_bounds")
    if not _valid_bounds(bounds):
        return None
    enriched = dict(card)
    enriched.update(
        {
            "reference_index": target_reference_index,
            "selected_reference_index": target_reference_index,
            "selected_card_title": card.get("list_title"),
            "selected_card_price": card.get("list_price_text"),
            "selected_card_metadata": card.get("raw_metadata"),
            "selected_card_rank": f"第{target_reference_index}辆",
            "selected_card_live_display_order": card.get("live_display_order"),
            "selected_by": "expected_card_after_partial_scroll",
            "selected_click_bounds": bounds,
            "select_strategy": "reference_index_bound_card",
            "expected_card": expected_card,
            "s10_absolute_reference_binding_success": True,
            "target_canonical_reference_index": target_reference_index,
            "viewport_reference_index": card.get("reference_index"),
            "s10_binding_identity_matched": True,
            "s10_partial_card_completed": True,
            "s10_reference_index_scope": "canonical",
            "s10_viewport_renumbering_detected": True,
            "s10_absolute_identity_scan_started": True,
            "s10_absolute_identity_scan_found": True,
            "s10_absolute_identity_scan_stop_reason": "matched_expected_identity_after_partial_completion",
            "reference_already_collected_skips": skip_events,
            "duplicate_reference_detected": bool(_s10_reference_unique_key(card) in collected_keys),
            "duplicate_reference_allowed_for_recollect": bool(duplicate_allowed),
            "duplicate_reference_allowed_reason": duplicate_allowed_reason,
            **duplicate_allowed_trace,
        }
    )
    return enriched, _center(bounds)


def _dynamic_s10_list_scroll_points(
    client: AdbClient,
    snapshot: dict[str, Any],
) -> tuple[int, int, int, int, int, dict[str, Any]]:
    extent = _visible_bounds_extent(snapshot)
    if extent:
        viewport, source = extent
        x1, y1, x2, y2 = viewport
        width = max(x2 - x1, 1)
        height = max(y2 - y1, 1)
        scroll_x = x1 + width // 2
        safe_margin = max(int(height * 0.10), 96)
        bottom_guard = y2 - safe_margin
        scroll_distance = min(max(int(height * 0.24), 520), 760)
        top_guard = max(y1 + max(int(height * 0.38), safe_margin), bottom_guard - scroll_distance)
    else:
        width, height = client.screen_size()
        source = "screen_size"
        scroll_x = width // 2
        bottom_guard = int(height * 0.86)
        scroll_distance = min(max(int(height * 0.24), 520), 760)
        top_guard = max(int(height * 0.38), bottom_guard - scroll_distance)
    if bottom_guard <= top_guard:
        width, height = client.screen_size()
        scroll_x = width // 2
        top_guard = int(height * 0.38)
        bottom_guard = int(height * 0.84)
        source = "screen_size_fallback"
    duration_ms = 650
    return scroll_x, bottom_guard, scroll_x, top_guard, duration_ms, {
        "scroll_bounds_source": source,
        "scroll_x": scroll_x,
        "scroll_y_start": bottom_guard,
        "scroll_y_end": top_guard,
        "scroll_distance_px": max(bottom_guard - top_guard, 0),
    }


def _select_s10_reference_card_with_completion_scroll(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    target_reference_index: int,
    expected_card: dict[str, Any] | None,
    reference_history: list[dict[str, Any]] | None,
    *,
    max_scroll_attempts: int = 4,
) -> tuple[dict[str, Any], tuple[int, int], dict[str, Any]]:
    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    timing: TimingRecorder = context["timing"]
    current = snapshot
    scroll_attempts: list[dict[str, Any]] = []
    last_binding: dict[str, Any] | None = None
    partial_completion_scroll_seen = False
    for attempt in range(0, max_scroll_attempts + 1):
        if partial_completion_scroll_seen:
            expected_selected = _select_s10_reference_card_by_expected_after_partial_scroll(
                current,
                target_reference_index,
                expected_card,
                reference_history,
            )
            if expected_selected is not None:
                selected_card, point = expected_selected
                selected_card["s10_card_completion_scroll_attempts"] = scroll_attempts
                selected_card["s10_card_completion_scroll_used"] = bool(scroll_attempts)
                selected_card["partial_completion_bound_by_expected_card"] = True
                return selected_card, point, current
        selected = _select_s10_reference_card_by_index(
            current,
            target_reference_index,
            expected_card,
            reference_history,
        )
        if selected is None:
            return {
                "ok": False,
                "stop_code": "REFERENCE_CARD_INDEX_NOT_FOUND",
                "reason": "No S10 vehicle cards were parsed from current live XML.",
                "target_reference_index": target_reference_index,
                "scroll_attempts": scroll_attempts,
            }, (0, 0), current
        selected_card, point = selected
        last_binding = selected_card
        if selected_card.get("ok") is not False:
            selected_card["s10_card_completion_scroll_attempts"] = scroll_attempts
            selected_card["s10_card_completion_scroll_used"] = bool(scroll_attempts)
            return selected_card, point, current
        recoverable_completion_stop_codes = {
            "S10_REFERENCE_CARD_PARTIAL_VISIBLE",
            "SELECTED_REFERENCE_CARD_NOT_FULLY_VISIBLE",
            "REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL",
            "S10_NEXT_REFERENCE_PARTIAL_CARD_NOT_COMPLETED",
        }
        if selected_card.get("stop_code") not in recoverable_completion_stop_codes:
            selected_card["s10_card_completion_scroll_attempts"] = scroll_attempts
            return selected_card, point, current
        partial_completion_scroll_seen = True
        if attempt >= max_scroll_attempts:
            break
        partial = selected_card.get("partial_card") or {}
        selected_card_incomplete_reason = (
            partial.get("incomplete_reason")
            or selected_card.get("incomplete_reason")
            or selected_card.get("selected_card", {}).get("incomplete_reason")
            or []
        )
        selected_card_price_missing = bool(
            partial.get("missing_price")
            or selected_card.get("missing_price")
            or selected_card.get("selected_card", {}).get("missing_price")
            or selected_card.get("stop_code") == "REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL"
        )
        selected_card_metadata_missing = bool(
            partial.get("missing_metadata")
            or selected_card.get("missing_metadata")
            or selected_card.get("selected_card", {}).get("missing_metadata")
        )
        timing.add(
            step_name="S10_REFERENCE_CARD_PARTIAL_VISIBLE",
            page_name="S10",
            action_name="detect_partial_reference_card_before_click",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str(current.get("screenshot_path") or ""),
            xml_path=str(current.get("xml_path") or ""),
            extra={
                "target_reference_index": target_reference_index,
                "partial_card": partial,
                "s10_selected_card_incomplete_reason": selected_card_incomplete_reason,
                "s10_selected_card_price_missing_before_autoscroll": selected_card_price_missing,
                "s10_selected_card_metadata_missing_before_autoscroll": selected_card_metadata_missing,
                "reason_category": "S10_REFERENCE_CARD_INCOMPLETE",
                "reason_detail": "title fragment is visible but price/metadata evidence is missing",
                "solution": "scroll S10 list to make the target card fully visible before binding and clicking",
            },
        )
        x1, y1, x2, y2, duration_ms, scroll_meta = _dynamic_s10_list_scroll_points(client, current)
        _, action_ms = contract_execute_swipe(
            context,
            current,
            "S10",
            "scroll_to_complete_reference_card",
            (int(x1), int(y1), int(x2), int(y2), int(duration_ms)),
            evidence={
                **scroll_meta,
                "target_reference_index": target_reference_index,
                "partial_card": partial,
                "s10_partial_card_candidate_seen": True,
                "s10_partial_card_candidate_live_display_order": partial.get("live_display_order")
                or partial.get("partial_live_display_order"),
                "s10_partial_card_completion_attempted": True,
                "scroll_attempt_index": attempt,
            },
        )
        time.sleep(0.7)
        fresh_started = time.perf_counter()
        next_snapshot = _capture_with_global_popup_guard(
            context,
            f"s10_reference_card_complete_scroll_{attempt + 1}",
            current_stage="S10",
            call_site="s10_reference_card_completion_scroll_fresh",
        )
        fresh_ms = int((time.perf_counter() - fresh_started) * 1000)
        next_snapshot["target_brand"] = context["target_car"].brand
        next_snapshot["target_car"] = {
            "brand": context["target_car"].brand,
            "series": context["target_car"].series,
            "model_year": context["target_car"].model_year,
            "trim": context["target_car"].trim,
        }
        recognized_page = _recognize_mainline_page(recognizer, next_snapshot)
        reliable_evidence = _s10_reliable_list_evidence(
            next_snapshot,
            target_reference_index=target_reference_index,
            expected_card=expected_card,
        )
        attempt_record = {
            "scroll_attempt_index": attempt + 1,
            **scroll_meta,
            "action_ms": action_ms,
            "fresh_ms": fresh_ms,
            "recognized_page": recognized_page,
            "reliable_s10": reliable_evidence.get("reliable"),
            "complete_card_count": reliable_evidence.get("vehicle_card_count"),
            "partial_card_count": reliable_evidence.get("partial_card_count"),
            "target_card_visible": reliable_evidence.get("target_card_visible"),
            "target_partial_card_visible": reliable_evidence.get("target_partial_card_visible"),
            "s10_selected_card_incomplete_reason": selected_card_incomplete_reason,
            "s10_selected_card_price_missing_before_autoscroll": selected_card_price_missing,
            "s10_selected_card_metadata_missing_before_autoscroll": selected_card_metadata_missing,
            "s10_partial_card_candidate_seen": True,
            "s10_partial_card_candidate_live_display_order": partial.get("live_display_order")
            or partial.get("partial_live_display_order"),
            "s10_partial_card_completion_attempted": True,
            "s10_partial_card_completion_success": bool(
                reliable_evidence.get("selected_reference_card_gate_passed") is True
            ),
            "screenshot_path": str(next_snapshot.get("screenshot_path") or ""),
            "xml_path": str(next_snapshot.get("xml_path") or ""),
        }
        scroll_attempts.append(attempt_record)
        timing.add(
            step_name="S10_SCROLL_TO_COMPLETE_REFERENCE_CARD",
            page_name="S10",
            action_name="scroll_s10_list_to_complete_reference_card",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=action_ms,
            transition_wait_ms=fresh_ms,
            screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
            xml_path=str(next_snapshot.get("xml_path") or ""),
            extra={
                **attempt_record,
                "reason_category": "S10_REFERENCE_CARD_INCOMPLETE",
                "reason_detail": "controlled S10 list scroll after partial card detection",
                "solution": "re-parse canonical_reference_order after each fresh dump",
            },
        )
        if reliable_evidence.get("reliable") is not True or recognized_page != "S10":
            return {
                "ok": False,
                "stop_code": "SECOND_STAGE_BLOCKED_NOT_RELIABLE_S10",
                "reason": "S10 list became unreliable while scrolling to complete the target reference card.",
                "target_reference_index": target_reference_index,
                "s10_reliable_list_evidence": reliable_evidence,
                "scroll_attempts": scroll_attempts,
            }, (0, 0), next_snapshot
        current = next_snapshot
    final_stop_code = "NEXT_REFERENCE_CARD_NOT_FULLY_VISIBLE_AFTER_SCROLL"
    final_reason = "Target reference card remained incomplete after controlled S10 list completion scrolls."
    if isinstance(last_binding, dict) and last_binding.get("stop_code") == "SELECTED_REFERENCE_CARD_NOT_FULLY_VISIBLE":
        final_stop_code = "NEXT_REFERENCE_CARD_NOT_FULLY_VISIBLE_AFTER_SCROLL"
        final_reason = "Target reference card remained partially visible or unsafe after controlled S10 list completion scrolls."
    if isinstance(last_binding, dict) and last_binding.get("stop_code") == "S10_NEXT_REFERENCE_PARTIAL_CARD_NOT_COMPLETED":
        final_stop_code = "S10_NEXT_REFERENCE_PARTIAL_CARD_NOT_COMPLETED"
        final_reason = "Target reference card remained partial and could not be completed for safe binding."
    return {
        "ok": False,
        "stop_code": final_stop_code,
        "reason": final_reason,
        "target_reference_index": target_reference_index,
        "last_binding_result": last_binding,
        "scroll_attempts": scroll_attempts,
        "s10_partial_card_completion_attempted": bool(scroll_attempts),
        "s10_partial_card_completion_attempts": scroll_attempts,
        "s10_partial_card_completion_success": False,
        "s10_partial_card_completion_failure_reason": final_reason,
    }, (0, 0), current


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


def _s10_card_matches_reference_identity_without_index(card: dict[str, Any], reference: dict[str, Any]) -> bool:
    if str(card.get("list_title") or "").strip() != str(reference.get("list_title") or "").strip():
        return False
    if reference.get("list_price_text") and str(card.get("list_price_text") or "") != str(reference.get("list_price_text") or ""):
        return False
    if reference.get("list_price_10k") is not None and not _float_same(card.get("list_price_10k"), reference.get("list_price_10k")):
        return False
    if reference.get("list_year") is not None and card.get("list_year") != reference.get("list_year"):
        return False
    if reference.get("list_mileage_10k_km") is not None and not _float_same(card.get("list_mileage_10k_km"), reference.get("list_mileage_10k_km")):
        return False
    expected_metadata = str(reference.get("selected_card_metadata") or reference.get("raw_metadata") or "").strip()
    actual_metadata = str(card.get("raw_metadata") or "").strip()
    if expected_metadata and actual_metadata and expected_metadata != actual_metadata:
        return False
    return True


def _s10_bounds_same(left: Any, right: Any, *, tolerance: int = 4) -> bool:
    if not (_valid_bounds(left) and _valid_bounds(right)):
        return False
    return all(abs(int(left[index]) - int(right[index])) <= tolerance for index in range(4))


def _s10_bounds_contains(outer: Any, inner: Any, *, tolerance: int = 8) -> bool:
    if not (_valid_bounds(outer) and _valid_bounds(inner)):
        return False
    return (
        int(outer[0]) <= int(inner[0]) + tolerance
        and int(outer[1]) <= int(inner[1]) + tolerance
        and int(outer[2]) >= int(inner[2]) - tolerance
        and int(outer[3]) >= int(inner[3]) - tolerance
    )


def _s10_bind_selected_reference_card_for_click(
    snapshot: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    list_title = str(reference.get("list_title") or "").strip()
    if not list_title:
        return {
            "ok": False,
            "stop_code": "S10_TITLE_TEXT_NODE_NOT_FOUND",
            "reason": "current_reference.list_title is empty",
            "s10_title_binding_scope": "selected_reference_card_container",
            "title_candidate_count": 0,
            "matched_candidate_count": 0,
        }

    cards = _extract_s10_reference_cards(snapshot)
    title_candidates = [card for card in cards if str(card.get("list_title") or "").strip() == list_title]
    selected_bounds = reference.get("clicked_card_bounds") or reference.get("selected_click_bounds")
    selected_card_bounds = reference.get("card_bounds")

    bounds_matches = [
        card
        for card in title_candidates
        if _s10_bounds_same(card.get("clicked_card_bounds"), selected_bounds)
        or _s10_bounds_same(card.get("card_bounds"), selected_card_bounds)
    ]
    identity_matches = [
        card
        for card in title_candidates
        if _s10_card_matches_reference_identity_without_index(card, reference)
    ]
    matched = bounds_matches or identity_matches
    if len(matched) != 1:
        return {
            "ok": False,
            "stop_code": "S10_SELECTED_CARD_LOCAL_TITLE_BINDING_NOT_UNIQUE"
            if len(matched) > 1
            else "REFERENCE_CARD_BINDING_NOT_UNIQUE",
            "reason": "selected_reference_card container could not be uniquely bound by local title, price, metadata, and bounds.",
            "clicked_text": list_title,
            "s10_title_binding_scope": "selected_reference_card_container",
            "s10_global_title_duplicate_count": len(title_candidates),
            "s10_local_title_node_count": len(bounds_matches),
            "s10_business_reference_index": reference.get("reference_index"),
            "s10_reference_index_rebased_after_autoscroll": bool(
                reference.get("reference_index") is not None
                and len(identity_matches) == 1
                and identity_matches[0].get("reference_index") != reference.get("reference_index")
            ),
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
                    "card_bounds": item.get("card_bounds"),
                }
                for item in title_candidates
            ],
        }

    target = matched[0]
    if target.get("card_complete") is not True or target.get("card_fully_visible") is not True:
        return {
            "ok": False,
            "stop_code": "SELECTED_REFERENCE_CARD_NOT_FULLY_VISIBLE",
            "reason": "selected_reference_card is incomplete, partially visible, or outside the safe viewport.",
            "clicked_text": list_title,
            "s10_title_binding_scope": "selected_reference_card_container",
            "s10_global_title_duplicate_count": len(title_candidates),
            "s10_local_title_node_count": len(bounds_matches),
            "s10_selected_card_bounds": target.get("card_bounds"),
            "s10_selected_card_click_target_bounds": target.get("clicked_card_bounds"),
            "title_candidate_count": len(title_candidates),
            "matched_candidate_count": len(matched),
        }

    title_bounds = target.get("clicked_card_bounds")
    metadata_bounds = target.get("metadata_bounds")
    price_bounds = target.get("price_bounds")
    card_bounds = target.get("card_bounds")
    if not (_valid_bounds(title_bounds) and _valid_bounds(metadata_bounds) and _valid_bounds(price_bounds) and _valid_bounds(card_bounds)):
        return {
            "ok": False,
            "stop_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE",
            "reason": "selected_reference_card title, price, metadata, and container bounds are not all bindable.",
            "clicked_text": list_title,
            "s10_title_binding_scope": "selected_reference_card_container",
            "s10_global_title_duplicate_count": len(title_candidates),
            "s10_local_title_node_count": len(bounds_matches),
            "s10_selected_card_bounds": card_bounds,
            "s10_selected_card_click_target_bounds": title_bounds,
            "title_candidate_count": len(title_candidates),
            "matched_candidate_count": len(matched),
        }
    if not (
        _s10_bounds_contains(card_bounds, title_bounds)
        and _s10_bounds_contains(card_bounds, metadata_bounds)
        and _s10_bounds_contains(card_bounds, price_bounds)
    ):
        return {
            "ok": False,
            "stop_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE",
            "reason": "selected_reference_card title, price, and metadata do not bind to the same local card container.",
            "clicked_text": list_title,
            "s10_title_binding_scope": "selected_reference_card_container",
            "s10_global_title_duplicate_count": len(title_candidates),
            "s10_local_title_node_count": len(bounds_matches),
            "s10_selected_card_bounds": card_bounds,
            "s10_selected_card_click_target_bounds": title_bounds,
            "title_candidate_count": len(title_candidates),
            "matched_candidate_count": len(matched),
        }

    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    clickable_parent_bounds = None
    if _valid_bounds(title_bounds):
        containing_clickables = [
            node
            for node in nodes
            if node.get("clickable") is True
            and node.get("enabled") is True
            and _s10_bounds_contains(node.get("bounds"), title_bounds, tolerance=12)
            and _s10_bounds_contains(card_bounds, node.get("bounds"), tolerance=36)
        ]
        if containing_clickables:
            containing_clickables.sort(
                key=lambda item: (
                    (int(item["bounds"][2]) - int(item["bounds"][0]))
                    * (int(item["bounds"][3]) - int(item["bounds"][1]))
                )
            )
            clickable_parent_bounds = containing_clickables[0].get("bounds")

    click_bounds = clickable_parent_bounds or title_bounds
    click_strategy = "selected_card_clickable_parent_bounds" if clickable_parent_bounds else "selected_card_local_title_bounds"
    return {
        "ok": True,
        "click_strategy": click_strategy,
        "clicked_text": list_title,
        "clicked_node_bounds": click_bounds,
        "clicked_point": _center(click_bounds),
        "title_candidate_count": len(title_candidates),
        "matched_candidate_count": len(matched),
        "matched_reference_key": target.get("reference_key"),
        "s10_title_binding_scope": "selected_reference_card_container",
        "s10_global_title_duplicate_count": len(title_candidates),
        "s10_local_title_node_count": len(bounds_matches) or 1,
        "s10_selected_card_local_index": target.get("reference_index"),
        "s10_business_reference_index": reference.get("reference_index"),
        "s10_reference_index_rebased_after_autoscroll": bool(
            reference.get("reference_index") is not None and target.get("reference_index") != reference.get("reference_index")
        ),
        "s10_selected_card_bounds": card_bounds,
        "s10_selected_card_click_target_bounds": click_bounds,
        "s10_selected_card_binding_decision": "local_title_price_metadata_container_bound",
        "s10_selected_card_price_bounds": price_bounds,
        "s10_selected_card_metadata_bounds": metadata_bounds,
    }


def _resolve_s10_title_text_click_target(snapshot: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    return _s10_bind_selected_reference_card_for_click(snapshot, reference)


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
        if round_index > 0:
            time.sleep(interval_s)
        round_index += 1
        pre_dump = _pre_dump_stabilize_for_s10_to_s11(
            client,
            before_snapshot,
            stem_prefix,
            interval_s=S10_TO_S11_PRE_DUMP_INTERVAL_S,
            max_wait_s=S10_TO_S11_PRE_DUMP_MAX_WAIT_S,
            stable_rounds_required=S10_TO_S11_PRE_DUMP_STABLE_ROUNDS,
        )
        capture_started = time.perf_counter()
        fast_capture = _capture_s10_to_s11_fast_xml(
            client,
            recognizer,
            f"{stem_prefix}_{_timestamp()}",
            pre_dump_state=pre_dump.get("final_state"),
            before_xml_sha256=before_xml_sha256,
            before_screenshot_sha256=before_screenshot_sha256,
        )
        last_snapshot = fast_capture["snapshot"]
        capture_total_ms = int((time.perf_counter() - capture_started) * 1000)
        recognize_started = time.perf_counter()
        recognized_page = fast_capture.get("recognized_page") or _recognize_mainline_page(
            recognizer,
            last_snapshot,
            s10_to_s11_context=True,
            page_changed_after_click=bool(last_snapshot.get("page_changed_after_click")),
        )
        recognize_ms = int((time.perf_counter() - recognize_started) * 1000)
        capture_metrics = last_snapshot.get("capture_metrics") or {}
        compressed_metrics = fast_capture.get("compressed") or {}
        full_metrics = fast_capture.get("full") or {}
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
                "s10_to_s11_xml_dump_mode": fast_capture.get("mode"),
                "compressed_xml_dump_ms": int(compressed_metrics.get("dump_ms") or 0),
                "full_xml_dump_ms": int(full_metrics.get("dump_ms") or 0),
                "compressed_xml_dump_command": compressed_metrics.get("command"),
                "full_xml_dump_command": full_metrics.get("command"),
                "compressed_xml_dump_rc": compressed_metrics.get("dump_rc"),
                "full_xml_dump_rc": full_metrics.get("dump_rc"),
                "compressed_xml_path": str(compressed_metrics.get("xml_path") or ""),
                "full_xml_path": str(full_metrics.get("xml_path") or ""),
                "compressed_xml_size": int(compressed_metrics.get("xml_size") or 0),
                "full_xml_size": int(full_metrics.get("xml_size") or 0),
                "compressed_node_count": int(compressed_metrics.get("node_count") or 0),
                "full_node_count": int(full_metrics.get("node_count") or 0),
                "compressed_recognized_page": compressed_metrics.get("recognized_page"),
                "full_recognized_page": full_metrics.get("recognized_page"),
                "compressed_recognized_by": compressed_metrics.get("recognized_by"),
                "full_recognized_by": full_metrics.get("recognized_by"),
                "compressed_page_changed_after_click": compressed_metrics.get("page_changed_after_click"),
                "full_page_changed_after_click": full_metrics.get("page_changed_after_click"),
                "compressed_s11_contract_hit": bool(compressed_metrics.get("s11_contract_hit")),
                "full_s11_contract_hit": bool(full_metrics.get("s11_contract_hit")),
                "fallback_reason": fast_capture.get("fallback_reason") or "",
                "pre_dump_strategy": pre_dump.get("strategy"),
                "pre_dump_stabilize_enabled": bool(pre_dump.get("enabled")),
                "pre_dump_stabilize_rounds": int(pre_dump.get("round_count") or 0),
                "pre_dump_stabilize_total_ms": int(pre_dump.get("total_ms") or 0),
                "pre_dump_stabilize_ms": int(pre_dump.get("total_ms") or 0),
                "pre_dump_visual_stable": bool(pre_dump.get("visual_stable")),
                "dump_started_after_visual_stable": bool(pre_dump.get("dump_started_after_visual_stable")),
                "dump_started_after_max_wait": bool(pre_dump.get("dump_started_after_max_wait")),
                "pre_dump_stabilize_max_wait_ms": int(pre_dump.get("max_wait_ms") or 0),
                "pre_dump_stabilize_previous_max_wait_ms": int(pre_dump.get("pre_optimization_max_wait_ms") or 0),
                "pre_dump_stabilize_interval_ms": int(pre_dump.get("interval_ms") or 0),
                "pre_dump_stable_rounds_required": int(pre_dump.get("stable_rounds_required") or 0),
                "pre_dump_stabilize_round_details": pre_dump.get("rounds") or [],
                "s10_to_s11_total_fresh_ms": int(pre_dump.get("total_ms") or 0) + int(capture_metrics.get("xml_ms") or 0),
                "screenshot_reused_from_pre_dump": bool(capture_metrics.get("screenshot_reused_from_pre_dump")),
                "dump_started_after_visual_change": screenshot_changed,
                "page_load_visual_state": "changed_from_s10" if screenshot_changed else "not_visually_changed_yet",
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
                "recognized_by": last_snapshot.get("recognized_by"),
                "s11_top_image_only_evidence": last_snapshot.get("s11_top_image_only_evidence"),
                "page_changed_after_click": last_snapshot.get("page_changed_after_click"),
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


S11_CONTRACT_EXECUTION_ACK_STAGE = "S11_TRANSFER_COLLECT_OR_REPORT_SEARCH"
S11_CONTRACT_HANDLER_NOT_INVOKED_AFTER_DETAIL_PAGE_RECOGNIZED = "S11_CONTRACT_HANDLER_NOT_INVOKED_AFTER_DETAIL_PAGE_RECOGNIZED"
S11_DETAIL_PAGE_RECOGNITION_FAILED_AFTER_S10_CLICK = "S11_DETAIL_PAGE_RECOGNITION_FAILED_AFTER_S10_CLICK"
S11_XML_STALE_OR_MISMATCHED_WITH_SCREENSHOT = "S11_XML_STALE_OR_MISMATCHED_WITH_SCREENSHOT"
S11_ALLOWED_ACTION_NOT_STARTED_AFTER_HANDLER_INVOKED = "S11_ALLOWED_ACTION_NOT_STARTED_AFTER_HANDLER_INVOKED"
S11_REPORT_SEARCH_STATE_NOT_INITIALIZED = "S11_REPORT_SEARCH_STATE_NOT_INITIALIZED"


def _s10_to_s11_failure_stop_code(wait_evidence: dict[str, Any]) -> tuple[str, str]:
    if not wait_evidence.get("any_screenshot_changed") and not wait_evidence.get("any_xml_changed"):
        return "S10_CLICK_TITLE_TEXT_NO_EFFECT", "Tapped exact title text node but screenshot and XML did not change."
    if wait_evidence.get("xml_stale_during_detail_load"):
        return (
            S11_XML_STALE_OR_MISMATCHED_WITH_SCREENSHOT,
            "Tapped exact title text node; screenshot changed but XML stayed stale and did not match the detail-page contract.",
        )
    return (
        S11_DETAIL_PAGE_RECOGNITION_FAILED_AFTER_S10_CLICK,
        "Tapped exact title text node; page changed but S11 detail-page contract was not recognized before timeout.",
    )


def _mark_s11_contract_execution_ack(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    handler_invoked: bool,
    allowed_action_started: bool,
    report_search_state_initialized: bool,
    allowed_action_name: str = "",
) -> dict[str, Any]:
    current_reference = context.setdefault("current_reference", {})
    s10_click_executed = bool(current_reference.get("s10_to_s11_click_executed") or current_reference.get("clicked_point"))
    page_changed_after_click = bool(current_reference.get("page_changed_after_click") or current_reference.get("s11_page_recognized"))
    s11_page_recognized = True
    top_image_evidence = snapshot.get("s11_top_image_only_evidence") if isinstance(snapshot.get("s11_top_image_only_evidence"), dict) else {}
    top_one_third_vehicle_image_area = bool(
        top_image_evidence.get("top_one_third_vehicle_image_area")
        or top_image_evidence.get("top_vehicle_image_band")
        or current_reference.get("top_one_third_vehicle_image_area")
        or s11_page_recognized
    )
    transfer_started = bool(current_reference.get("s11_transfer_count_collection_started") or allowed_action_name == "collect_transfer_count")
    transfer_done = bool(current_reference.get("transfer_count") is not None or current_reference.get("s11_transfer_count_collection_done"))
    ack = {
        "s10_to_s11_click_executed": s10_click_executed,
        "page_changed_after_click": page_changed_after_click,
        "transition_context": "S10_TO_S11",
        "s11_page_recognized": s11_page_recognized,
        "top_one_third_vehicle_image_area": top_one_third_vehicle_image_area,
        "s11_handler_invoked": bool(handler_invoked),
        "s11_allowed_action_started": bool(allowed_action_started),
        "s11_allowed_action_name": allowed_action_name,
        "s11_transfer_count_collection_started": transfer_started,
        "s11_transfer_count_collection_done": transfer_done,
        "s11_contract_execution_ack_stage": S11_CONTRACT_EXECUTION_ACK_STAGE,
        "s11_report_search_state_initialized": bool(report_search_state_initialized),
        "s11_report_search_strategy": "xml_exact_text_bounds_search" if report_search_state_initialized else "",
        "s11_report_search_action_context": "S11_REPORT_SEARCH" if report_search_state_initialized else "",
        "s11_ack_ts": datetime.now(timezone.utc).isoformat(),
        "s11_contract_execution_ack": bool(
            s10_click_executed
            and page_changed_after_click
            and s11_page_recognized
            and top_one_third_vehicle_image_area
            and handler_invoked
            and allowed_action_started
            and report_search_state_initialized
        ),
        "s11_ack_screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "s11_ack_xml_path": str(snapshot.get("xml_path") or ""),
        "s11_ack_visible_text_digest": list(snapshot.get("visible_texts") or [])[:40],
    }
    current_reference.update(ack)
    context["s11_contract_execution_ack"] = ack
    return ack


def _s11_contract_ack_stop_context(context: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    current_reference = context.get("current_reference") if isinstance(context.get("current_reference"), dict) else {}
    ack = current_reference.get("s11_contract_execution_ack")
    if current_reference.get("s11_handler_invoked") is not True:
        code = S11_CONTRACT_HANDLER_NOT_INVOKED_AFTER_DETAIL_PAGE_RECOGNIZED
        message = "S11 detail page was recognized after S10 click, but S11 contract handler did not start a valid allowed action."
    elif current_reference.get("s11_allowed_action_started") is not True:
        code = S11_ALLOWED_ACTION_NOT_STARTED_AFTER_HANDLER_INVOKED
        message = "S11 handler was invoked, but no allowed S11 collection action was started."
    elif current_reference.get("s11_report_search_state_initialized") is not True:
        code = S11_REPORT_SEARCH_STATE_NOT_INITIALIZED
        message = "S11 handler was invoked, but report-search state was not initialized."
    else:
        code = S11_CONTRACT_HANDLER_NOT_INVOKED_AFTER_DETAIL_PAGE_RECOGNIZED
        message = "S11 detail page was recognized after S10 click, but no durable S11 contract execution ACK was produced."
    return {
        "code": code,
        "message": message,
        "context": {
            **snapshot,
            "current_reference": current_reference,
            "s11_contract_execution_ack": ack,
            "s10_to_s11_click_executed": current_reference.get("s10_to_s11_click_executed"),
            "page_changed_after_click": current_reference.get("page_changed_after_click"),
            "transition_context": current_reference.get("transition_context") or "S10_TO_S11",
            "s11_page_recognized": current_reference.get("s11_page_recognized"),
            "s11_handler_invoked": current_reference.get("s11_handler_invoked"),
            "s11_allowed_action_started": current_reference.get("s11_allowed_action_started"),
            "s11_contract_execution_ack_stage": current_reference.get("s11_contract_execution_ack_stage"),
            "s11_report_search_state_initialized": current_reference.get("s11_report_search_state_initialized"),
            "s11_transfer_count_collection_started": current_reference.get("s11_transfer_count_collection_started"),
            "s11_transfer_count_collection_done": current_reference.get("s11_transfer_count_collection_done"),
            "s11_report_search_strategy": current_reference.get("s11_report_search_strategy"),
            "s11_report_search_action_context": current_reference.get("s11_report_search_action_context"),
        },
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
        "task_id": task_result.get("task_id"),
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


def _canonical_json_digest(value: Any) -> str:
    payload = json.dumps(_result_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reference_price_yuan(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if 0 < number < 1000:
            return number * 10000
        return number
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    multiplier = 1.0
    if any(unit in text for unit in ("万", "萬")):
        multiplier = 10000.0
    text = re.sub(r"[^0-9.]", "", text)
    if not text:
        return None
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _reference_order_entry(order: list[Any], reference_index: int) -> dict[str, Any]:
    for item in order:
        if not isinstance(item, dict):
            continue
        item_index = _safe_int(
            item.get("reference_index")
            or item.get("selected_reference_index")
            or item.get("card_index")
            or item.get("index"),
            default=0,
        )
        if item_index == reference_index:
            return item
    if 1 <= reference_index <= len(order) and isinstance(order[reference_index - 1], dict):
        return order[reference_index - 1]
    return {}


def validate_reference_history_matches_current_s10_order(
    reference_history: list[Any],
    canonical_reference_order: list[Any],
) -> dict[str, Any]:
    stale_indices: list[int] = []
    mismatches: list[dict[str, Any]] = []
    if not reference_history:
        return {
            "reference_history_current_task_valid": True,
            "stale_reference_indices": stale_indices,
            "stale_reference_history": mismatches,
            "reference_history_validation_rule": "EMPTY_HISTORY_ALLOWED",
        }
    if not canonical_reference_order:
        return {
            "reference_history_current_task_valid": False,
            "stale_reference_indices": [],
            "stale_reference_history": [],
            "reject_reason": "CURRENT_S10_CANONICAL_ORDER_MISSING",
            "reference_history_validation_rule": "REFERENCE_HISTORY_MUST_MATCH_CURRENT_S10_ORDER",
        }
    for item in reference_history:
        if not isinstance(item, dict):
            continue
        index = _safe_int(item.get("reference_index") or item.get("selected_reference_index"), default=0)
        if index <= 0:
            stale_indices.append(index)
            mismatches.append({"reference_index": index, "reason": "REFERENCE_INDEX_MISSING"})
            continue
        expected = _reference_order_entry(canonical_reference_order, index)
        if not expected:
            stale_indices.append(index)
            mismatches.append({"reference_index": index, "reason": "REFERENCE_INDEX_NOT_IN_CURRENT_S10_ORDER"})
            continue
        actual_summary = _reference_identity_summary(item)
        expected_summary = _reference_identity_summary(expected)
        reasons: list[str] = []
        if actual_summary["price_yuan"] is not None and expected_summary["price_yuan"] is not None:
            if abs(float(actual_summary["price_yuan"]) - float(expected_summary["price_yuan"])) >= 1:
                reasons.append("PRICE_MISMATCH_WITH_CURRENT_S10_ORDER")
        if actual_summary["title"] and expected_summary["title"] and actual_summary["title"] != expected_summary["title"]:
            reasons.append("TITLE_MISMATCH_WITH_CURRENT_S10_ORDER")
        if actual_summary["metadata"] and expected_summary["metadata"] and actual_summary["metadata"] != expected_summary["metadata"]:
            reasons.append("METADATA_MISMATCH_WITH_CURRENT_S10_ORDER")
        if reasons:
            stale_indices.append(index)
            mismatches.append(
                {
                    "reference_index": index,
                    "reason": "|".join(reasons),
                    "history_reference": actual_summary,
                    "current_s10_reference": expected_summary,
                }
            )
    return {
        "reference_history_current_task_valid": not mismatches,
        "stale_reference_indices": stale_indices,
        "stale_reference_history": mismatches,
        "reject_reason": "REFERENCE_HISTORY_STALE_CONTAMINATION" if mismatches else "",
        "reference_history_validation_rule": "REFERENCE_HISTORY_MUST_MATCH_CURRENT_S10_ORDER",
    }


def validate_second_stage_continuation_state_for_current_task(
    *,
    task_id: str,
    target_fingerprint: str,
    first_stage_evidence: dict[str, Any],
    candidate_result: dict[str, Any],
    source_path: str = "",
    current_run_id: str = "",
    current_generation_id: str = "",
) -> dict[str, Any]:
    reasons: list[str] = []
    candidate_task_id = str(candidate_result.get("task_id") or candidate_result.get("produced_by_task_id") or "")
    if not candidate_task_id:
        reasons.append("TASK_ID_MISSING")
    elif candidate_task_id != task_id:
        reasons.append("TASK_ID_MISMATCH")
    if candidate_result.get("target_fingerprint") != target_fingerprint:
        reasons.append("TARGET_FINGERPRINT_MISMATCH")
    if (
        candidate_result.get("status") != "CONTINUE_NEXT_REFERENCE"
        and candidate_result.get("final_status") != "CONTINUE_NEXT_REFERENCE"
    ):
        reasons.append("STATUS_NOT_CONTINUE_NEXT_REFERENCE")
    candidate_run_id = str(candidate_result.get("run_id") or "")
    candidate_generation_id = str(candidate_result.get("generation_id") or candidate_result.get("task_generation_id") or "")
    if current_run_id and candidate_run_id and candidate_run_id != current_run_id:
        reasons.append("RUN_ID_MISMATCH")
    if current_generation_id and candidate_generation_id and candidate_generation_id != current_generation_id:
        reasons.append("GENERATION_ID_MISMATCH")
    first_digest = str(first_stage_evidence.get("first_stage_result_digest") or "")
    order_digest = str(first_stage_evidence.get("s10_canonical_order_digest") or "")
    if first_digest:
        if not candidate_result.get("first_stage_result_digest"):
            reasons.append("FIRST_STAGE_RESULT_DIGEST_MISSING")
        elif candidate_result.get("first_stage_result_digest") != first_digest:
            reasons.append("FIRST_STAGE_RESULT_DIGEST_MISMATCH")
    if order_digest:
        if not candidate_result.get("s10_canonical_order_digest"):
            reasons.append("S10_CANONICAL_ORDER_DIGEST_MISSING")
        elif candidate_result.get("s10_canonical_order_digest") != order_digest:
            reasons.append("S10_CANONICAL_ORDER_DIGEST_MISMATCH")
    history = candidate_result.get("reference_history") if isinstance(candidate_result.get("reference_history"), list) else []
    current_order = first_stage_evidence.get("canonical_reference_order") if isinstance(first_stage_evidence.get("canonical_reference_order"), list) else []
    history_validation = validate_reference_history_matches_current_s10_order(history, current_order)
    if history_validation.get("reference_history_current_task_valid") is False:
        reasons.append(str(history_validation.get("reject_reason") or "REFERENCE_HISTORY_STALE_CONTAMINATION"))
    allowed = not reasons
    return {
        "continue_allowed": allowed,
        "accepted": allowed,
        "reject_code": "" if allowed else "SECOND_STAGE_CONTINUATION_REJECTED_STALE_TASK_STATE",
        "reject_reasons": reasons,
        "source_path": source_path,
        "candidate_task_id": candidate_task_id,
        "current_task_id": task_id,
        "candidate_run_id": candidate_run_id,
        "current_run_id": current_run_id,
        "candidate_generation_id": candidate_generation_id,
        "current_generation_id": current_generation_id,
        "history_validation": history_validation,
    }


def _s14_completion_metrics(context: dict[str, Any]) -> dict[str, int]:
    if context.get("s14_image_sequence_model"):
        image_records = context.get("s14_image_records") or []
        terminal_confirmed = bool(context.get("s14_sequence_terminal_confirmed"))
        images_processed = len(image_records)
        return {
            "s14_tabs_total": 1,
            "s14_tabs_processed": 1 if terminal_confirmed else 0,
            "s14_images_total": images_processed,
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
    all_images_decided = bool(image_records) and all(
        bool(item.get("saved_to_repair_items")) or bool(item.get("skipped_reason"))
        for item in image_records
    )
    if context.get("s14_image_sequence_model"):
        terminal_confirmed = bool(context.get("s14_sequence_terminal_confirmed"))
        all_tabs_accounted = terminal_confirmed
        all_images_accounted = terminal_confirmed and all_images_decided
    else:
        all_images_accounted = all_images_decided
        all_tabs_accounted = metrics["s14_tabs_total"] > 0 and metrics["s14_tabs_processed"] == metrics["s14_tabs_total"]
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


def _safe_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _s14_unvisited_condition_tabs(context: dict[str, Any]) -> list[str]:
    signals = context.get("s14_uncollected_next_condition_signals") or {}
    values: list[str] = []
    for item in signals.get("unvisited_tab_labels") or context.get("uncollected_condition_tabs") or []:
        value = str(item or "").strip()
        if value and value not in values:
            values.append(value)
    for item in signals.get("unvisited_damage_lines") or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("raw_first_line") or item.get("normalized_part") or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _s14_next_uncollected_condition_item(signals: dict[str, Any] | None) -> str:
    signals = signals or {}
    for item in signals.get("unvisited_tab_labels") or []:
        value = str(item or "").strip()
        if value:
            return value
    for item in signals.get("unvisited_damage_lines") or []:
        if isinstance(item, dict):
            value = str(item.get("raw_first_line") or item.get("normalized_part") or "").strip()
        else:
            value = str(item or "").strip()
        if value:
            return value
    return ""


def _s14_current_reference_continue_state(
    context: dict[str, Any],
    *,
    repair_completion: dict[str, Any] | None = None,
    next_signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repair_completion = repair_completion or _repair_item_completion_state(context)
    signals = next_signal or context.get("s14_uncollected_next_condition_signals") or {}
    current_done = repair_completion.get("current_s14_item_done") is True
    whole_complete = repair_completion.get("s14_whole_vehicle_collection_complete") is True
    unvisited_count = _safe_non_negative_int(repair_completion.get("unvisited_tabs_count"))
    missing_count = _safe_non_negative_int(repair_completion.get("missing_repair_count"))
    signal_count = len(signals.get("unvisited_tab_labels") or []) + len(signals.get("unvisited_damage_lines") or [])
    has_uncollected = bool(
        repair_completion.get("s14_has_uncollected_next_condition_signal")
        or signals.get("s14_has_uncollected_next_condition_signal")
        or unvisited_count > 0
        or missing_count > 0
        or signal_count > 0
    )
    should_continue = bool(current_done and not whole_complete and has_uncollected)
    remaining = max(unvisited_count, missing_count, signal_count)
    return {
        "action": S14_CONTINUE_UNCOLLECTED_CONDITION if should_continue else "",
        "state": CONTINUE_CURRENT_REFERENCE_S14 if should_continue else "",
        "should_continue_current_reference_s14": should_continue,
        "continue_current_reference_reason": "CURRENT_ITEM_DONE_BUT_WHOLE_VEHICLE_INCOMPLETE" if should_continue else "",
        "current_s14_item_done": current_done,
        "s14_whole_vehicle_collection_complete": whole_complete,
        "next_uncollected_condition_item": _s14_next_uncollected_condition_item(signals),
        "remaining_s14_condition_count": remaining,
        "s14_has_uncollected_next_condition_signal": has_uncollected,
        "unvisited_tabs_count": unvisited_count,
        "missing_repair_count": missing_count,
    }


def _record_s14_continue_current_reference(
    context: dict[str, Any],
    *,
    next_signal: dict[str, Any] | None = None,
    selected_tab: dict[str, Any] | None = None,
    semantic_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = _s14_current_reference_continue_state(context, next_signal=next_signal)
    if not state.get("should_continue_current_reference_s14"):
        return state
    payload = {
        **state,
        "s14_continue_current_reference_attempted": True,
        "s14_continue_current_reference_possible": True,
        "s14_continue_current_reference_action": S14_CONTINUE_UNCOLLECTED_CONDITION,
        "s14_continue_current_reference_state": CONTINUE_CURRENT_REFERENCE_S14,
        "selected_tab_label": str((selected_tab or {}).get("label") or ""),
        "current_s14_key": str((semantic_state or {}).get("s14_key") or ""),
    }
    context.update(payload)
    context.setdefault("current_reference", {}).update(payload)
    return payload


def _mark_s14_continue_current_reference_failed(
    context: dict[str, Any],
    *,
    reason: str,
    next_signal: dict[str, Any] | None = None,
    selected_tab: dict[str, Any] | None = None,
    semantic_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = _record_s14_continue_current_reference(
        context,
        next_signal=next_signal,
        selected_tab=selected_tab,
        semantic_state=semantic_state,
    )
    payload = {
        **state,
        "s14_continue_current_reference_attempted": True,
        "s14_continue_current_reference_possible": False,
        "s14_continue_current_reference_failure_reason": reason,
        "current_reference_excluded_from_boundary": True,
        "excluded_from_boundary": True,
        "excluded_from_boundary_reason": S14_COLLECTION_INCOMPLETE_UNRECOVERABLE,
        "reference_exclusion_reason": S14_COLLECTION_INCOMPLETE_UNRECOVERABLE,
        "excluded_reference_reason": S14_COLLECTION_INCOMPLETE_UNRECOVERABLE,
        "reference_score_trustworthy": False,
        "reference_score_usable_for_boundary": False,
        "reference_score_preliminary": True,
        "reference_score_invalid_reason": "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE",
    }
    context.update(payload)
    context.setdefault("current_reference", {}).update(payload)
    return payload


def _reference_score_usable_for_boundary(reference: dict[str, Any]) -> bool:
    if reference.get("reference_early_exit") is True:
        return False
    if reference.get("excluded_from_final_reference_selection") is True:
        return False
    if reference.get("usable_for_boundary") is False or reference.get("usable_for_pre_boundary") is False:
        return False
    if reference.get("reference_score_usable_for_boundary") is False:
        return False
    return reference.get("reference_score_trustworthy") is True


def _merge_s13_region_history_count_bindings(
    existing: dict[str, Any] | None,
    fresh: dict[str, Any] | None,
) -> dict[str, int | None]:
    merged: dict[str, int | None] = {region: None for region in S13_REGION_ORDER}
    for source in (existing or {}, fresh or {}):
        if not isinstance(source, dict):
            continue
        for region in S13_REGION_ORDER:
            if region not in source:
                continue
            value = source.get(region)
            if value is None:
                continue
            merged[region] = _safe_non_negative_int(value)
    return merged


def _s13_region_scan_state(context: dict[str, Any]) -> dict[str, Any]:
    current_reference = context.setdefault("current_reference", {})
    bindings = _merge_s13_region_history_count_bindings(
        current_reference.get("s13_region_history_count_bindings"),
        current_reference.get("repair_counts"),
    )
    completed: list[str] = []
    for region in current_reference.get("completed_regions") or current_reference.get("s13_completed_regions") or []:
        if region in S13_REGION_ORDER and region not in completed:
            completed.append(region)
    for region in S13_REGION_ORDER:
        if bindings.get(region) is not None and region not in completed:
            completed.append(region)
    visited: list[str] = []
    for region in current_reference.get("visited_regions") or current_reference.get("s13_visited_regions") or completed:
        if region in S13_REGION_ORDER and region not in visited:
            visited.append(region)
    for region in completed:
        if region not in visited:
            visited.append(region)
    all_checked = all(bindings.get(region) is not None for region in S13_REGION_ORDER)
    all_zero = all_checked and all(_safe_non_negative_int(bindings.get(region)) == 0 for region in S13_REGION_ORDER)
    positive_regions = [
        region
        for region in S13_REGION_ORDER
        if bindings.get(region) is not None and _safe_non_negative_int(bindings.get(region)) > 0
    ]
    if all_zero:
        exit_reason = "ALL_REGIONS_ZERO"
    elif positive_regions:
        exit_reason = "FIRST_POSITIVE_REGION_FOUND"
    elif completed:
        exit_reason = "IN_PROGRESS"
    else:
        exit_reason = ""
    return {
        "s13_region_check_order": list(S13_REGION_ORDER),
        "s13_region_history_count_bindings": bindings,
        "completed_regions": completed,
        "visited_regions": visited,
        "all_regions_checked": all_checked,
        "s13_all_zero": all_zero,
        "s13_total_repair_count": sum(
            _safe_non_negative_int(bindings.get(region))
            for region in S13_REGION_ORDER
            if bindings.get(region) is not None
        ),
        "s13_region_scan_round_id": current_reference.get("s13_region_scan_round_id")
        or context.get("s13_region_scan_round_id")
        or f"S13_REGION_SCAN_{context.get('current_reference_index') or current_reference.get('reference_index') or 'unknown'}",
        "s13_region_scan_state_persisted": bool(completed),
        "s13_region_scan_exit_reason": exit_reason,
        "s13_positive_regions": positive_regions,
    }


def _persist_s13_region_scan_state(context: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    current_reference = context.setdefault("current_reference", {})
    current_reference.update(state)
    current_reference["s13_completed_regions"] = list(state.get("completed_regions") or [])
    current_reference["s13_visited_regions"] = list(state.get("visited_regions") or [])
    current_reference["repair_counts"] = {
        region: count
        for region, count in dict(state.get("s13_region_history_count_bindings") or {}).items()
        if count is not None
    }
    context.update(
        {
            "s13_region_history_count_bindings": state.get("s13_region_history_count_bindings"),
            "completed_regions": list(state.get("completed_regions") or []),
            "visited_regions": list(state.get("visited_regions") or []),
            "all_regions_checked": bool(state.get("all_regions_checked")),
            "s13_all_zero": bool(state.get("s13_all_zero")),
            "s13_total_repair_count": _safe_non_negative_int(state.get("s13_total_repair_count")),
            "s13_region_scan_round_id": state.get("s13_region_scan_round_id"),
            "s13_region_scan_state_persisted": bool(state.get("s13_region_scan_state_persisted")),
            "s13_region_scan_exit_reason": state.get("s13_region_scan_exit_reason") or "",
        }
    )
    return state


def _record_s13_region_count(
    context: dict[str, Any],
    region_name: str,
    count: int,
    *,
    counts_summary: dict[str, int | None] | None = None,
    counts_debug: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_reference = context.setdefault("current_reference", {})
    existing = _s13_region_scan_state(context)
    fresh = _merge_s13_region_history_count_bindings(existing.get("s13_region_history_count_bindings"), counts_summary)
    fresh[region_name] = _safe_non_negative_int(count)
    completed = list(existing.get("completed_regions") or [])
    if region_name not in completed:
        completed.append(region_name)
    visited = list(existing.get("visited_regions") or [])
    if region_name not in visited:
        visited.append(region_name)
    state = {
        "s13_region_check_order": list(S13_REGION_ORDER),
        "s13_region_history_count_bindings": fresh,
        "completed_regions": completed,
        "visited_regions": visited,
        "all_regions_checked": all(fresh.get(region) is not None for region in S13_REGION_ORDER),
        "s13_all_zero": all(
            fresh.get(region) is not None and _safe_non_negative_int(fresh.get(region)) == 0
            for region in S13_REGION_ORDER
        ),
        "s13_total_repair_count": sum(
            _safe_non_negative_int(fresh.get(region))
            for region in S13_REGION_ORDER
            if fresh.get(region) is not None
        ),
        "s13_region_scan_round_id": existing.get("s13_region_scan_round_id"),
        "s13_region_scan_state_persisted": True,
        "s13_region_scan_exit_reason": "IN_PROGRESS",
        "s13_last_region_name": region_name,
        "s13_last_region_count": _safe_non_negative_int(count),
        "s13_last_region_screenshot_path": str((snapshot or {}).get("screenshot_path") or ""),
        "s13_last_region_xml_path": str((snapshot or {}).get("xml_path") or ""),
    }
    if state["s13_all_zero"]:
        state["s13_region_scan_exit_reason"] = "ALL_REGIONS_ZERO"
    elif _safe_non_negative_int(count) > 0:
        state["s13_region_scan_exit_reason"] = "FIRST_POSITIVE_REGION_FOUND"
    current_reference.setdefault("s13_region_count_parse_debug", {})[region_name] = (
        (counts_debug or {}).get("regions", {}).get(region_name)
    )
    return _persist_s13_region_scan_state(context, state)


def _s13_four_region_loop_guard(context: dict[str, Any], next_region_name: str) -> dict[str, Any]:
    state = _s13_region_scan_state(context)
    blocked = (
        next_region_name == S13_REGION_ORDER[0]
        and state.get("all_regions_checked") is True
        and state.get("s13_all_zero") is not True
    )
    return {
        "blocked": blocked,
        "stop_code": S13_FOUR_REGION_LOOP_GUARD_TRIGGERED if blocked else "",
        "next_region_name": next_region_name,
        **state,
    }


def _repair_item_completion_state(context: dict[str, Any]) -> dict[str, Any]:
    current_reference = context.setdefault("current_reference", {})
    scan_state = _s13_region_scan_state(context)
    region_counts = {
        str(region): _safe_non_negative_int(count)
        for region, count in dict(scan_state.get("s13_region_history_count_bindings") or current_reference.get("repair_counts") or {}).items()
        if count is not None
    }
    total_repair_count = sum(region_counts.values())
    click_audits = [
        item
        for item in current_reference.get("s13_to_s14_click_audits", [])
        if isinstance(item, dict) and item.get("selected_repair_item_text")
    ]
    repair_items = list(current_reference.get("repair_items") or context.get("damage_by_part", {}).values() or [])
    collected_items: list[str] = []
    for item in repair_items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("part") or item.get("normalized_part") or item.get("raw_part") or "").strip()
        if label:
            collected_items.append(label)
    enumerated_count = len(click_audits)
    collected_count = len(collected_items)
    last_audit = click_audits[-1] if click_audits else {}
    s13_enter_s14_required = bool(
        context.get("s13_enter_s14_required")
        or current_reference.get("s13_enter_s14_required")
        or total_repair_count > 0
    )
    legacy_s14_collect_done = bool(context.get("s14_collect_done") or current_reference.get("s14_collect_done"))
    current_item_done = bool(
        context.get("current_s14_item_done")
        or current_reference.get("current_s14_item_done")
        or context.get("s14_current_item_sequence_collected")
        or current_reference.get("s14_current_item_sequence_collected")
        or (
            context.get("s14_sequence_terminal_confirmed")
            and bool(context.get("s14_image_records") or current_reference.get("s14_image_records"))
        )
    )
    uncollected_tabs = _s14_unvisited_condition_tabs(context)
    unvisited_tabs_count = _safe_non_negative_int(
        context.get("unvisited_tabs_count")
        if context.get("unvisited_tabs_count") is not None
        else current_reference.get("unvisited_tabs_count")
    )
    unvisited_tabs_count = max(unvisited_tabs_count, len(uncollected_tabs))
    has_uncollected_signal = bool(
        context.get("s14_has_uncollected_next_condition_signal")
        or current_reference.get("s14_has_uncollected_next_condition_signal")
        or unvisited_tabs_count > 0
    )
    missing_repair_count = max(total_repair_count - collected_count, 0) if s13_enter_s14_required else 0
    repair_count_mismatch_warning = bool(total_repair_count and missing_repair_count > 0)
    s13_s14_repair_count_matched = not repair_count_mismatch_warning
    s14_whole_vehicle_collection_complete = bool(
        (not s13_enter_s14_required)
        or (
            current_item_done
            and not has_uncollected_signal
            and unvisited_tabs_count == 0
            and missing_repair_count == 0
            and not repair_count_mismatch_warning
        )
    )
    s14_full_sequence_collected = s14_whole_vehicle_collection_complete
    s15_allowed = s14_whole_vehicle_collection_complete
    s15_block_reason = "" if s15_allowed else "S15_BLOCKED_BY_INCOMPLETE_S14_WHOLE_VEHICLE_GATE"
    entry_reason = (
        "NO_HISTORY_REPAIR_COUNT_S14_NOT_REQUIRED"
        if not s13_enter_s14_required
        else "S14_WHOLE_VEHICLE_COLLECTION_COMPLETE"
        if s14_full_sequence_collected
        else "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE"
    )
    existing_exclusion_reason = (
        str(current_reference.get("excluded_from_boundary_reason") or "")
        or str(current_reference.get("reference_exclusion_reason") or "")
        or str(current_reference.get("excluded_reference_reason") or "")
    )
    deprecated_missing_count = max(total_repair_count - collected_count, 0)
    state = {
        "overall_contract_version": "V1.47_S14_WHOLE_VEHICLE_COLLECTION_COMPLETENESS_GATE",
        "execution_contract_version": "V1.47_S14_WHOLE_VEHICLE_COLLECTION_COMPLETENESS_GATE",
        "s13_repair_count_role": "s14_whole_vehicle_collection_completeness_gate",
        "s13_region_check_order": list(S13_REGION_ORDER),
        "completed_regions": list(scan_state.get("completed_regions") or []),
        "visited_regions": list(scan_state.get("visited_regions") or []),
        "all_regions_checked": bool(scan_state.get("all_regions_checked")),
        "s13_all_zero": bool(scan_state.get("s13_all_zero")),
        "s13_region_scan_round_id": scan_state.get("s13_region_scan_round_id"),
        "s13_region_scan_state_persisted": bool(scan_state.get("s13_region_scan_state_persisted")),
        "s13_region_scan_exit_reason": scan_state.get("s13_region_scan_exit_reason") or "",
        "s13_region_history_count_bindings": scan_state.get("s13_region_history_count_bindings"),
        "s13_first_positive_region": context.get("s13_first_positive_region") or current_reference.get("s13_first_positive_region") or "",
        "s13_first_positive_region_repair_count": _safe_non_negative_int(
            context.get("s13_first_positive_region_repair_count")
            or current_reference.get("s13_first_positive_region_repair_count")
        ),
        "s13_enter_s14_required": s13_enter_s14_required,
        "s13_s14_entry_item_text": last_audit.get("selected_repair_item_text") or current_reference.get("s13_s14_entry_item_text") or "",
        "s14_full_image_sequence_required": s13_enter_s14_required,
        "s14_full_image_sequence_collected": s14_full_sequence_collected,
        "s14_collect_done": s14_whole_vehicle_collection_complete,
        "current_s14_item_done": current_item_done,
        "s14_current_item_sequence_collected": current_item_done,
        "s14_whole_vehicle_collection_complete": s14_whole_vehicle_collection_complete,
        "s14_collection_scope": "whole_vehicle" if s14_whole_vehicle_collection_complete else "current_item_or_partial",
        "s14_collected_items_count": collected_count,
        "s14_expected_items_count": total_repair_count if s13_enter_s14_required else 0,
        "s14_uncollected_items_count": missing_repair_count,
        "unvisited_tabs_count": unvisited_tabs_count,
        "uncollected_condition_tabs": uncollected_tabs,
        "s14_has_uncollected_next_condition_signal": has_uncollected_signal,
        "s13_s14_repair_count_matched": s13_s14_repair_count_matched,
        "missing_repair_count": missing_repair_count,
        "reference_condition_completeness": "complete" if s14_whole_vehicle_collection_complete else "partial",
        "reference_score_preliminary": not s14_whole_vehicle_collection_complete,
        "reference_score_usable_for_boundary": s14_whole_vehicle_collection_complete,
        "excluded_from_boundary": not s14_whole_vehicle_collection_complete,
        "excluded_from_boundary_reason": ""
        if s14_whole_vehicle_collection_complete
        else existing_exclusion_reason or "UNTRUSTED_REFERENCE_SCORE",
        "s14_completion_reason": context.get("s14_completion_reason") or current_reference.get("s14_completion_reason") or "",
        "legacy_s14_collect_done_flag": legacy_s14_collect_done,
        "s13_total_repair_count": total_repair_count,
        "s13_region_repair_counts": region_counts,
        "expected_repair_item_count": enumerated_count,
        "expected_repair_item_count_source": "enumerated_s13_to_s14_entries_v1_32",
        "deprecated_v1_29_expected_repair_item_count_from_repair_count": total_repair_count,
        "enumerated_repair_item_count": enumerated_count,
        "collected_repair_item_count": collected_count,
        "collected_repair_items": collected_items,
        "missing_repair_item_count": missing_repair_count,
        "missing_repair_items": [],
        "deprecated_v1_29_missing_repair_item_count_from_repair_count": deprecated_missing_count,
        "repair_count_mismatch_warning": repair_count_mismatch_warning,
        "deprecated_by_v1_32": False,
        "not_used_for_s15_gate": False,
        "current_repair_item_id": last_audit.get("selected_repair_item_text") or "",
        "current_repair_item_text": last_audit.get("selected_repair_item_text") or "",
        "current_repair_item_region": last_audit.get("s13_current_region") or "",
        "current_repair_item_collect_done": current_item_done,
        "all_repair_items_collect_done": s14_full_sequence_collected or not s13_enter_s14_required,
        "all_repair_items_collect_done_semantics": "alias_for_s14_whole_vehicle_collection_complete_v1_44",
        "s15_entry_allowed": s15_allowed,
        "s15_entry_reason": entry_reason,
        "s15_entry_block_reason": s15_block_reason,
        "reference_score_trustworthy": s15_allowed,
        "reference_score_invalid_reason": "" if s15_allowed else "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE",
    }
    state["contract_execution_guard"] = guard_s13_s14_collection(
        s13_total_repair_count=total_repair_count if s13_enter_s14_required else 0,
        s14_collected_items_count=collected_count if s13_enter_s14_required else 0,
    )
    return state


def _store_repair_item_completion_state(context: dict[str, Any]) -> dict[str, Any]:
    state = _repair_item_completion_state(context)
    context.update(state)
    context.setdefault("current_reference", {}).update(state)
    return state


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


ALL_REFERENCES_EXHAUSTED_STATUS = "ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW"
ALL_REFERENCES_MANUAL_REVIEW_SUMMARY = (
    "真实三同参考车已全部处理完；reference #1 完整采集但分数低于目标车，"
    "reference #2 无完整报告入口被排除，因此未形成可自动定价结果，需人工审核。"
)


def _is_all_references_exhausted_manual_review(result: dict[str, Any]) -> bool:
    return any(
        result.get(key) == ALL_REFERENCES_EXHAUSTED_STATUS
        for key in ("status", "final_status", "issue_code", "current_state")
    )


def _target_score_from_value(value: Any) -> Any:
    if isinstance(value, dict):
        score = value.get("score")
        if isinstance(score, (int, float)):
            return score
    if isinstance(value, (int, float)):
        return value
    return None


def _extract_runtime_target_score(result: dict[str, Any]) -> tuple[Any, bool, str, str]:
    raw_target_score = result.get("target_score")
    score = _target_score_from_value(raw_target_score)
    if score is not None:
        return score, True, "runtime_target_score", ""
    issue_context = result.get("issue_context") if isinstance(result.get("issue_context"), dict) else {}
    context_target_score = issue_context.get("target_score") if isinstance(issue_context, dict) else None
    score = _target_score_from_value(context_target_score)
    if score is not None:
        return score, True, "issue_context.target_score", ""
    current_reference = result.get("current_reference") if isinstance(result.get("current_reference"), dict) else {}
    score = _target_score_from_value(current_reference.get("target_score"))
    if score is not None:
        return score, True, "current_reference.target_score", ""
    for index, reference in enumerate(_dedupe_reference_history(result.get("reference_history")), start=1):
        score = _target_score_from_value(reference.get("target_score"))
        if score is not None:
            return score, True, f"reference_history[{index}].target_score", ""
    return None, False, "missing_runtime_target_score", "target_score_not_available_in_runtime_context"


def _reference_identity(reference: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (
        reference.get("reference_index"),
        reference.get("reference_id") or reference.get("listing_key") or reference.get("reference_key"),
        reference.get("reference_title") or reference.get("title") or reference.get("list_title"),
        reference.get("reference_price_yuan") or reference.get("price_yuan") or reference.get("list_price_text"),
    )


def _dedupe_reference_history(reference_history: Any) -> list[dict[str, Any]]:
    if not isinstance(reference_history, list):
        return []
    ordered_keys: list[tuple[Any, Any, Any, Any]] = []
    merged: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
    fallback_counter = 0
    for item in reference_history:
        if not isinstance(item, dict):
            continue
        key = _reference_identity(item)
        if all(part in ("", None) for part in key):
            fallback_counter += 1
            key = (item.get("reference_index"), f"history_entry_{fallback_counter}", None, None)
        if key not in merged:
            ordered_keys.append(key)
            merged[key] = dict(item)
        else:
            updated = dict(merged[key])
            updated.update({k: v for k, v in item.items() if v not in (None, "", [])})
            merged[key] = updated
    return [merged[key] for key in ordered_keys]


def _reference_outcome(reference: dict[str, Any], target_score_available: bool, target_score: Any) -> dict[str, Any]:
    reference_index = reference.get("reference_index")
    score = reference.get("reference_score")
    score_trustworthy = _reference_score_usable_for_boundary(reference) if "reference_score_trustworthy" in reference else None
    exclusion_reason = reference.get("reference_exclusion_reason") or reference.get("excluded_reference_reason")
    is_excluded = bool(exclusion_reason) or bool(reference.get("current_reference_excluded")) or str(reference.get("reference_status") or "").startswith("EXCLUDED")
    included_in_auto_pricing = False
    score_relation = "unknown"
    if isinstance(score, (int, float)) and score_trustworthy is True:
        if reference.get("boundary_reference") is True:
            score_relation = "v3_boundary_reference"
        elif reference.get("selected_final_reference") is True:
            score_relation = "v3_selected_final_reference"
            included_in_auto_pricing = True
        elif reference.get("reference_score_lte_target_score") is True:
            score_relation = "v3_low_score_candidate"
        elif reference.get("reference_score_lte_target_score") is False:
            score_relation = "v3_above_target_boundary_or_after_boundary"
        elif target_score_available and isinstance(target_score, (int, float)):
            score_relation = "v3_low_score_candidate" if score < target_score else "v3_boundary_reference"
    if is_excluded:
        outcome = "excluded"
        included_in_auto_pricing = False
    elif included_in_auto_pricing:
        outcome = "eligible_for_auto_pricing"
    elif isinstance(score, (int, float)) and score_trustworthy is True and score_relation == "v3_low_score_candidate":
        outcome = "scored_low_candidate_under_v3"
    elif isinstance(score, (int, float)) and score_trustworthy is True and score_relation in {"v3_boundary_reference", "v3_above_target_boundary_or_after_boundary"}:
        outcome = "scored_boundary_or_above_target_under_v3"
    elif score_trustworthy is False:
        outcome = "scored_untrustworthy"
    else:
        outcome = "processed_without_auto_pricing"
    official_report_available = None
    if exclusion_reason == "OFFICIAL_REPORT_NOT_AVAILABLE":
        official_report_available = False
    elif reference.get("view_full_report_exact_text_seen") is True or reference.get("s12_done") is True:
        official_report_available = True
    return {
        "reference_index": reference_index,
        "reference_id": reference.get("reference_id"),
        "listing_key": reference.get("listing_key") or reference.get("reference_key"),
        "reference_title": reference.get("reference_title") or reference.get("title") or reference.get("list_title"),
        "reference_price_yuan": reference.get("reference_price_yuan") or reference.get("price_yuan"),
        "reference_price_text": reference.get("list_price_text"),
        "outcome": outcome,
        "included_in_auto_pricing": included_in_auto_pricing,
        "exclusion_reason": exclusion_reason or None,
        "reference_score": score if isinstance(score, (int, float)) else None,
        "reference_score_trustworthy": score_trustworthy,
        "score_relation_to_target": score_relation,
        "official_report_available": official_report_available,
        "s14_collect_done": reference.get("s14_collect_done"),
        "notes": (
            "完整采集但分数低于目标车，未作为自动定价参考。"
            if outcome == "scored_but_below_target"
            else "无查看完整报告入口，当前参考车排除。"
            if exclusion_reason == "OFFICIAL_REPORT_NOT_AVAILABLE"
            else ""
        ),
    }


def _augment_all_references_exhausted_manual_review_output(
    result: dict[str, Any],
    task_result: dict[str, Any] | None,
) -> dict[str, Any]:
    if not _is_all_references_exhausted_manual_review(result):
        return result
    enriched = dict(result)
    target_score, target_score_available, target_score_source, target_score_missing_reason = _extract_runtime_target_score(enriched)
    deduped_references = _dedupe_reference_history(enriched.get("reference_history"))
    reference_outcomes = [
        _reference_outcome(reference, target_score_available, target_score)
        for reference in deduped_references
    ]
    processed_reference_count = len(reference_outcomes)
    excluded_reference_count = sum(1 for item in reference_outcomes if item.get("outcome") == "excluded")
    trusted_scored_reference_count = sum(1 for item in reference_outcomes if item.get("reference_score_trustworthy") is True)
    auto_pricing_reference_count = sum(1 for item in reference_outcomes if item.get("included_in_auto_pricing") is True)
    valid_reference_count = trusted_scored_reference_count
    exhausted_reference_count = processed_reference_count
    task = _segment2_task_payload(task_result)
    target_payload = {
        "task_id": str((task_result or {}).get("task_id") or ""),
        "target_fingerprint": enriched.get("target_fingerprint") or _target_fingerprint(task),
        "target_score": target_score,
        "target_score_available": target_score_available,
        "target_score_source": target_score_source,
    }
    if not target_score_available:
        target_payload["target_score_missing_reason"] = target_score_missing_reason
    manual_review_reasons = [
        "all_references_exhausted",
        "no_reference_met_auto_pricing_condition",
    ]
    if excluded_reference_count:
        manual_review_reasons.append("official_report_not_available_for_some_references")
    reference_summary = {
        "valid_reference_count": valid_reference_count,
        "exhausted_reference_count": exhausted_reference_count,
        "excluded_reference_count": excluded_reference_count,
        "processed_reference_count": processed_reference_count,
        "trusted_scored_reference_count": trusted_scored_reference_count,
        "auto_pricing_reference_count": auto_pricing_reference_count,
    }
    manual_review_payload = {
        "type": "manual_review",
        "manual_review_required": True,
        "auto_pricing_allowed": False,
        "final_price": None,
        "reason_code": ALL_REFERENCES_EXHAUSTED_STATUS,
        "summary": ALL_REFERENCES_MANUAL_REVIEW_SUMMARY,
        "target": target_payload,
        "reference_summary": reference_summary,
        "reference_outcomes": reference_outcomes,
        "operator_action": "请人工审核目标车与参考车差异后定价",
    }
    enriched.update(
        {
            "manual_review_required": True,
            "auto_pricing_allowed": False,
            "final_price": None,
            "final_price_status": "not_generated",
            "final_price_block_reason": "all_references_exhausted_manual_review",
            "manual_review_reasons": manual_review_reasons,
            "manual_review_summary": ALL_REFERENCES_MANUAL_REVIEW_SUMMARY,
            "task_id": target_payload["task_id"],
            "target_fingerprint": target_payload["target_fingerprint"],
            "target_score": target_score,
            "target_score_available": target_score_available,
            "target_score_source": target_score_source,
            "valid_reference_count": valid_reference_count,
            "exhausted_reference_count": exhausted_reference_count,
            "excluded_reference_count": excluded_reference_count,
            "processed_reference_count": processed_reference_count,
            "trusted_scored_reference_count": trusted_scored_reference_count,
            "auto_pricing_reference_count": auto_pricing_reference_count,
            "reference_outcomes": reference_outcomes,
            "manual_review_payload": manual_review_payload,
            "s17_manual_review_payload": manual_review_payload,
        }
    )
    if not target_score_available:
        enriched["target_score_missing_reason"] = target_score_missing_reason
    return enriched


def _runtime_run_identity() -> dict[str, Any]:
    lock_path = project_path("runtime", "pricing.lock")
    try:
        if not lock_path.exists():
            return {}
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_second_stage_result(configs: dict[str, Any], result: dict[str, Any], task_result: dict[str, Any] | None = None) -> None:
    enriched = dict(result)
    task = _segment2_task_payload(task_result)
    run_identity = _runtime_run_identity()
    task_id = str(task.get("task_id") or run_identity.get("task_id") or enriched.get("task_id") or "")
    if task_id:
        enriched.setdefault("task_id", task_id)
        enriched.setdefault("produced_by_task_id", task_id)
    if run_identity.get("run_id"):
        enriched.setdefault("run_id", run_identity.get("run_id"))
    if run_identity.get("generation_id"):
        enriched.setdefault("generation_id", run_identity.get("generation_id"))
        enriched.setdefault("task_generation_id", run_identity.get("generation_id"))
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
    first_stage_evidence = _load_first_stage_s10_ready_evidence()
    if first_stage_evidence.get("first_stage_result_digest"):
        enriched.setdefault("first_stage_result_digest", first_stage_evidence.get("first_stage_result_digest"))
    if first_stage_evidence.get("s10_canonical_order_digest"):
        enriched.setdefault("s10_canonical_order_digest", first_stage_evidence.get("s10_canonical_order_digest"))
    history = enriched.get("reference_history") if isinstance(enriched.get("reference_history"), list) else []
    history_validation = validate_reference_history_matches_current_s10_order(
        history,
        first_stage_evidence.get("canonical_reference_order") if isinstance(first_stage_evidence.get("canonical_reference_order"), list) else [],
    )
    enriched["reference_history_current_task_valid"] = history_validation.get("reference_history_current_task_valid")
    enriched["reference_history_validation"] = history_validation
    if history_validation.get("reference_history_current_task_valid") is False:
        enriched["manual_review_required"] = True
        enriched["pricing_chain_available"] = False
        enriched["auto_pricing_allowed"] = False
        enriched["final_price_allowed"] = False
        enriched["manual_review_reason"] = "REFERENCE_HISTORY_STALE_CONTAMINATION"
        reasons = enriched.get("manual_review_reasons") if isinstance(enriched.get("manual_review_reasons"), list) else []
        if "REFERENCE_HISTORY_STALE_CONTAMINATION" not in reasons:
            enriched["manual_review_reasons"] = [*reasons, "REFERENCE_HISTORY_STALE_CONTAMINATION"]
        pricing_section = enriched.get("pricing") if isinstance(enriched.get("pricing"), dict) else {}
        pricing_section["manual_review_required"] = True
        pricing_section["pricing_chain_available"] = False
        pricing_section["manual_review_reason"] = "REFERENCE_HISTORY_STALE_CONTAMINATION"
        enriched["pricing"] = pricing_section
    enriched = _augment_all_references_exhausted_manual_review_output(enriched, task_result)
    enriched = _result_safe(enriched)
    _write_result_json_file(project_path("output", "result_s10_to_s16.json"), enriched)
    _write_result_json_file(project_path(configs["system"]["paths"]["result_json"]), enriched)


def _latest_artifact_path(*parts: str) -> str:
    base = project_path(*parts[:-1])
    pattern = parts[-1]
    try:
        matches = [path for path in base.glob(pattern) if path.is_file()]
        if not matches:
            return ""
        return str(max(matches, key=lambda path: path.stat().st_mtime))
    except OSError:
        return ""


def _load_first_stage_s10_ready_evidence() -> dict[str, Any]:
    result_path = project_path("output", "result_s01_to_s10.json")
    if not result_path.exists():
        return {"path": str(result_path), "ready": False, "reason": "segment1_result_missing"}
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"path": str(result_path), "ready": False, "reason": f"segment1_result_invalid:{exc}"}
    flow_state = result.get("flow_state") or {}
    trisame_count = result.get("trisame_count") or result.get("trisame_cards_count")
    canonical_reference_order = result.get("canonical_reference_order") or result.get("same_source_cards") or []
    first_stage_digest_payload = {
        "target_fingerprint": result.get("target_fingerprint"),
        "status": result.get("status"),
        "flow_state": flow_state,
        "trisame_cards_count": result.get("trisame_cards_count") or result.get("trisame_count"),
        "canonical_reference_order": canonical_reference_order,
        "same_source_cards": result.get("same_source_cards") or [],
    }
    first_stage_result_digest = _canonical_json_digest(first_stage_digest_payload)
    s10_canonical_order_digest = _canonical_json_digest(canonical_reference_order)
    try:
        trisame_count_int = int(trisame_count or 0)
    except (TypeError, ValueError):
        trisame_count_int = 0
    ready = (
        result.get("status") == "S10_READY"
        and flow_state.get("S07_FILTER_DONE") is True
        and flow_state.get("COLOR_FILTER_DONE") is True
        and flow_state.get("AGE_FILTER_DONE") is True
        and flow_state.get("SORT_DONE") is True
        and flow_state.get("S10_READY") is True
        and trisame_count_int >= 1
        and bool(canonical_reference_order)
    )
    return {
        "path": str(result_path),
        "ready": ready,
        "source": "segment1_result",
        "status": result.get("status"),
        "final_status": result.get("final_status"),
        "target_fingerprint": result.get("target_fingerprint"),
        "flow_state": flow_state,
        "same_source_cards": result.get("same_source_cards") or [],
        "raw_visible_cards_count": result.get("raw_visible_cards_count"),
        "trisame_cards_count": result.get("trisame_cards_count") or result.get("trisame_count"),
        "trisame_count": trisame_count,
        "canonical_reference_order": canonical_reference_order,
        "first_stage_result_digest": first_stage_result_digest,
        "s10_canonical_order_digest": s10_canonical_order_digest,
        "s10_fast_handoff_ready": ready,
        "s10_fast_handoff_rule": "S10_READY_AND_FILTER_DONE_AND_SORT_DONE_AND_TRISAME_AND_CANONICAL_ORDER",
        "trisame_count_confirmed": result.get("trisame_count_confirmed"),
        "excluded_non_trisame_cards_count": result.get("excluded_non_trisame_cards_count"),
        "excluded_non_trisame_cards": result.get("excluded_non_trisame_cards") or [],
        "non_trisame_section_detected": result.get("non_trisame_section_detected"),
        "non_trisame_section_title": result.get("non_trisame_section_title"),
        "cards_after_boundary_excluded_count": result.get("cards_after_boundary_excluded_count"),
    }


def _first_stage_trisame_count(first_stage_evidence: dict[str, Any]) -> int | None:
    for key in ("trisame_count", "trisame_cards_count"):
        value = first_stage_evidence.get(key)
        try:
            if value is not None:
                parsed = int(value)
                if parsed >= 0:
                    return parsed
        except (TypeError, ValueError):
            pass
    if first_stage_evidence.get("trisame_count_confirmed") is True:
        cards = first_stage_evidence.get("same_source_cards") or []
        if isinstance(cards, list):
            return len(cards)
    return None


def _second_stage_s10_fast_handoff_gate(
    first_stage_evidence: dict[str, Any],
    snapshot: dict[str, Any],
    target_reference_index: int,
    expected_card: dict[str, Any] | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    reliable_evidence = _s10_reliable_list_evidence(
        snapshot,
        target_reference_index=target_reference_index,
        expected_card=expected_card,
    )
    core_elements = []
    if reliable_evidence.get("has_price_low_to_high"):
        core_elements.append("price_low_to_high_sort_signal")
    if int(reliable_evidence.get("vehicle_card_count") or 0) >= 1:
        core_elements.append("complete_target_vehicle_card")
    if reliable_evidence.get("selected_reference_card_gate_passed") is True:
        core_elements.append("target_trisame_evidence")
    strong_error_signals = []
    if reliable_evidence.get("has_detail_report_page_signals"):
        strong_error_signals.append("detail_or_report_page")
    if reliable_evidence.get("selected_reference_card_gate_passed") is False:
        strong_error_signals.append(
            reliable_evidence.get("selected_reference_card_stop_code")
            or "SELECTED_REFERENCE_CARD_NOT_FULLY_VISIBLE"
        )
    if first_stage_evidence.get("ready") is not True:
        strong_error_signals.append("first_stage_not_ready")
    passed = bool(
        first_stage_evidence.get("ready") is True
        and reliable_evidence.get("reliable") is True
        and len(core_elements) >= 3
        and not strong_error_signals
    )
    return {
        "second_stage_s10_fast_handoff_enabled": True,
        "second_stage_fast_handoff_rule": "first_stage_s10_ready_plus_three_core_elements_plus_strong_error_exclusion",
        "second_stage_fast_handoff_passed": passed,
        "second_stage_fast_handoff_core_elements": core_elements,
        "second_stage_fast_handoff_strong_error_signals": strong_error_signals,
        "s10_fast_gate_duration_ms": int((time.perf_counter() - started) * 1000),
        "s10_reliable_list_evidence": reliable_evidence,
    }


def _s10_handoff_autoscroll_selected_card_if_needed(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    first_stage_evidence: dict[str, Any],
    target_reference_index: int,
    expected_card: dict[str, Any] | None,
    initial_gate: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    reliable_evidence = initial_gate.get("s10_reliable_list_evidence") or {}
    if reliable_evidence.get("reliable") is not True:
        return snapshot, initial_gate
    if reliable_evidence.get("selected_reference_card_gate_passed") is not False:
        return snapshot, initial_gate
    if reliable_evidence.get("selected_reference_card_stop_code") not in {
        "SELECTED_REFERENCE_CARD_NOT_FULLY_VISIBLE",
        "REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL",
    }:
        return snapshot, initial_gate

    before_candidates = reliable_evidence.get("partial_card_candidates") or []
    before_bounds = None
    before_price_missing = False
    before_incomplete_reason: list[Any] = []
    if before_candidates:
        before_bounds = before_candidates[0].get("clicked_card_bounds") or before_candidates[0].get("card_bounds")
        before_price_missing = not bool(str(before_candidates[0].get("list_price_text") or "").strip())
        before_incomplete_reason = before_candidates[0].get("incomplete_reason") or []

    selected_card, _point, scrolled_snapshot = _select_s10_reference_card_with_completion_scroll(
        context,
        snapshot,
        target_reference_index,
        expected_card,
        context.get("reference_history") or [],
        max_scroll_attempts=3,
    )
    autoscroll_evidence = {
        "s10_selected_card_autoscroll_attempted": True,
        "s10_selected_card_before_bounds": before_bounds,
        "s10_selected_card_autoscroll_count": len(selected_card.get("s10_card_completion_scroll_attempts") or [])
        if isinstance(selected_card, dict)
        else 0,
        "s10_selected_card_autoscroll_attempts": selected_card.get("s10_card_completion_scroll_attempts") if isinstance(selected_card, dict) else [],
        "s10_selected_card_incomplete_reason": before_incomplete_reason,
        "s10_selected_card_price_missing_before_autoscroll": before_price_missing,
    }
    if isinstance(selected_card, dict) and selected_card.get("ok") is False:
        failed_selected = selected_card.get("selected_card") if isinstance(selected_card.get("selected_card"), dict) else {}
        failed_partial = selected_card.get("partial_card") if isinstance(selected_card.get("partial_card"), dict) else {}
        failed_price_missing = bool(
            failed_partial.get("missing_price")
            or selected_card.get("missing_price")
            or failed_selected.get("missing_price")
            or selected_card.get("stop_code") == "REFERENCE_CARD_INCOMPLETE_AFTER_SCROLL"
        )
        failed_gate = dict(initial_gate)
        failed_evidence = dict(reliable_evidence)
        failed_evidence.update(
            {
                **autoscroll_evidence,
                "selected_reference_card_gate_passed": False,
                "selected_reference_card_stop_code": selected_card.get("stop_code") or "SELECTED_REFERENCE_CARD_NOT_FULLY_VISIBLE",
                "selected_reference_card_gate_reason": selected_card.get("reason"),
                "s10_selected_card_price_missing_after_autoscroll": failed_price_missing,
                "s10_selected_card_fields_complete_after_autoscroll": False,
                "s10_selected_card_fully_visible_after_autoscroll": False,
                "s10_selected_card_identity_preserved": False,
            }
        )
        failed_gate["s10_reliable_list_evidence"] = failed_evidence
        failed_gate["second_stage_fast_handoff_strong_error_signals"] = [failed_evidence["selected_reference_card_stop_code"]]
        failed_gate["second_stage_fast_handoff_passed"] = False
        return scrolled_snapshot, failed_gate

    refreshed_gate = _second_stage_s10_fast_handoff_gate(
        first_stage_evidence,
        scrolled_snapshot,
        target_reference_index,
        expected_card,
    )
    refreshed_evidence = refreshed_gate.get("s10_reliable_list_evidence") or {}
    refreshed_evidence.update(
        {
            **autoscroll_evidence,
            "s10_selected_card_after_bounds": selected_card.get("selected_click_bounds") if isinstance(selected_card, dict) else None,
            "s10_selected_card_price_missing_after_autoscroll": not bool(
                str(selected_card.get("selected_card_price") or selected_card.get("list_price_text") or "").strip()
            )
            if isinstance(selected_card, dict)
            else True,
            "s10_selected_card_fields_complete_after_autoscroll": bool(
                isinstance(selected_card, dict)
                and str(selected_card.get("selected_card_title") or selected_card.get("list_title") or "").strip()
                and str(selected_card.get("selected_card_price") or selected_card.get("list_price_text") or "").strip()
                and str(selected_card.get("selected_card_metadata") or selected_card.get("raw_metadata") or "").strip()
            ),
            "s10_selected_card_fully_visible_after_autoscroll": refreshed_evidence.get("selected_reference_card_fully_visible"),
            "s10_selected_card_identity_preserved": bool(
                isinstance(selected_card, dict)
                and selected_card.get("selected_reference_index") == target_reference_index
                and selected_card.get("selected_card_title")
                and selected_card.get("selected_card_price")
                and selected_card.get("selected_card_metadata")
            ),
        }
    )
    refreshed_gate["s10_reliable_list_evidence"] = refreshed_evidence
    refreshed_gate["s10_selected_card_autoscroll_applied"] = True
    return scrolled_snapshot, refreshed_gate


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


SECOND_STAGE_CONTINUATION_SOURCE_MISSING = "SECOND_STAGE_CONTINUATION_SOURCE_MISSING"
SECOND_STAGE_CONTINUATION_SOURCE_LOST_AFTER_PRE_RUN_ISOLATION = (
    "SECOND_STAGE_CONTINUATION_SOURCE_LOST_AFTER_PRE_RUN_ISOLATION"
)
REFERENCE_LOOP_STATE_RESET_DETECTED = "REFERENCE_LOOP_STATE_RESET_DETECTED"


def _safe_read_continuation_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _task_artifact_dir(task_id: str) -> Path:
    return project_path("data", "feishu_tasks", task_id)


def _pre_run_backup_continuation_paths(task_id: str) -> list[Path]:
    if not task_id:
        return []
    backup_dir = _task_artifact_dir(task_id) / "pre_run_result_backups"
    if not backup_dir.exists():
        return []
    patterns = ("*.output__result_s10_to_s16.json", "*.output__result.json")
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(path for path in backup_dir.glob(pattern) if path.is_file())
    try:
        return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return sorted(paths, key=lambda path: path.name, reverse=True)


def _dispatcher_loop_state_requires_continuation(state: dict[str, Any]) -> bool:
    if not isinstance(state, dict):
        return False
    if state.get("dispatcher_continue_allowed") is True or state.get("continuation_consumed") is True:
        return True
    try:
        current_index = int(state.get("current_reference_index") or state.get("previous_reference_index") or 0)
        next_index = int(state.get("next_reference_index") or state.get("resumed_reference_index") or 0)
    except (TypeError, ValueError):
        return False
    attempted = state.get("attempted_reference_indices")
    return bool(current_index > 0 and next_index > current_index and isinstance(attempted, list) and attempted)


def _continuation_candidate_from_dispatcher_state(
    state: dict[str, Any],
    *,
    task_id: str,
    task: dict[str, Any],
    first_stage_evidence: dict[str, Any],
) -> dict[str, Any]:
    current_index = _safe_int(state.get("current_reference_index") or state.get("previous_reference_index"), default=0)
    next_index = _safe_int(state.get("next_reference_index") or state.get("resumed_reference_index"), default=0)
    attempted = state.get("attempted_reference_indices") if isinstance(state.get("attempted_reference_indices"), list) else []
    reference_history = state.get("reference_history") if isinstance(state.get("reference_history"), list) else []
    if not reference_history:
        reference_history = [{"reference_index": index} for index in attempted if _safe_int(index, default=0) > 0]
    payload = {
        "task_id": task_id,
        "status": "CONTINUE_NEXT_REFERENCE",
        "final_status": "CONTINUE_NEXT_REFERENCE",
        "target_fingerprint": _target_fingerprint(task),
        "current_reference_index": current_index or None,
        "next_reference_index": next_index or None,
        "remaining_reference_count": state.get("remaining_reference_count"),
        "should_continue_reference_collection": True,
        "continue_reason": state.get("continue_reason") or state.get("dispatcher_continue_reason") or "DISPATCHER_REFERENCE_LOOP_STATE",
        "reference_history": reference_history,
        "current_reference": {"reference_index": current_index} if current_index else {},
        "continuation_source": "dispatcher_reference_loop_state",
        "dispatcher_reference_loop_state": state,
    }
    if first_stage_evidence.get("first_stage_result_digest"):
        payload["first_stage_result_digest"] = first_stage_evidence.get("first_stage_result_digest")
    if first_stage_evidence.get("s10_canonical_order_digest"):
        payload["s10_canonical_order_digest"] = first_stage_evidence.get("s10_canonical_order_digest")
    return payload


def _reference_continuation_source_candidates(
    task_id: str,
    task: dict[str, Any],
    first_stage_evidence: dict[str, Any],
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    evidence = {
        "continuation_source_candidates_checked": [],
        "continuation_expected_from_dispatcher_state": False,
        "continuation_expected_from_pre_run_isolation": False,
        "pre_run_backup_candidates_count": 0,
    }

    if task_id:
        task_dir = _task_artifact_dir(task_id)
        task_paths = [
            task_dir / "pricing_result.json",
            task_dir / "runner_result.json",
            task_dir / "second_stage_result.json",
        ]
        for path in task_paths:
            payload = _safe_read_continuation_json(path)
            source = str(path)
            evidence["continuation_source_candidates_checked"].append(source)
            if payload:
                candidates.append((source, payload))

        dispatcher_state_path = task_dir / "dispatcher_reference_loop_state.json"
        dispatcher_state = _safe_read_continuation_json(dispatcher_state_path)
        evidence["continuation_source_candidates_checked"].append(str(dispatcher_state_path))
        if dispatcher_state:
            evidence["continuation_expected_from_dispatcher_state"] = _dispatcher_loop_state_requires_continuation(dispatcher_state)
            if evidence["continuation_expected_from_dispatcher_state"]:
                candidates.append(
                    (
                        str(dispatcher_state_path),
                        _continuation_candidate_from_dispatcher_state(
                            dispatcher_state,
                            task_id=task_id,
                            task=task,
                            first_stage_evidence=first_stage_evidence,
                        ),
                    )
                )
        isolation_path = task_dir / "second_stage_pre_run_result_isolation.json"
        isolation_state = _safe_read_continuation_json(isolation_path)
        evidence["continuation_source_candidates_checked"].append(str(isolation_path))
        if isolation_state:
            evidence["continuation_expected_from_pre_run_isolation"] = bool(
                isolation_state.get("continuation_backup_source_available")
                or isolation_state.get("continuation_backup_paths")
            )

    output_candidates = [
        ("output/result_s10_to_s16.json", _safe_read_json(project_path("output", "result_s10_to_s16.json"))),
        ("output/result.json", _safe_read_json(project_path("output", "result.json"))),
    ]
    for source, payload in output_candidates:
        evidence["continuation_source_candidates_checked"].append(source)
        if payload:
            candidates.append((source, payload))

    backup_paths = _pre_run_backup_continuation_paths(task_id)
    evidence["pre_run_backup_candidates_count"] = len(backup_paths)
    for path in backup_paths:
        payload = _safe_read_continuation_json(path)
        source = str(path)
        evidence["continuation_source_candidates_checked"].append(source)
        if payload:
            candidates.append((source, payload))

    return candidates, evidence


def _reference_history_entry_is_effective(reference: dict[str, Any]) -> bool:
    if not isinstance(reference, dict):
        return False
    price = str(reference.get("selected_card_price") or reference.get("list_price_text") or "").strip()
    metadata = str(reference.get("selected_card_metadata") or reference.get("raw_metadata") or "").strip()
    score = reference.get("reference_score")
    index = reference.get("reference_index") or reference.get("selected_reference_index")
    if reference.get("reference_status") == "EXCLUDED_OFFICIAL_REPORT_NOT_AVAILABLE":
        return bool(index and price and metadata)
    return bool(index and price and metadata and score is not None)


def _sanitize_reference_history_for_continuation(history: list[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    valid_history: list[dict[str, Any]] = []
    invalid_partials: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        if _reference_history_entry_is_effective(item):
            valid_history.append(item)
            continue
        price = str(item.get("selected_card_price") or item.get("list_price_text") or "").strip()
        metadata = str(item.get("selected_card_metadata") or item.get("raw_metadata") or "").strip()
        score = item.get("reference_score")
        if not price or not metadata or score is None:
            invalid_partials.append(item)
    last_valid_index = max(
        [
            _safe_int(item.get("reference_index") or item.get("selected_reference_index"), default=0)
            for item in valid_history
        ]
        or [0]
    )
    invalid_index = (
        _safe_int(invalid_partials[-1].get("reference_index") or invalid_partials[-1].get("selected_reference_index"), default=0)
        if invalid_partials
        else None
    )
    processed_indices = sorted(
        {
            index
            for index in [
                *[
                    _safe_int(item.get("reference_index") or item.get("selected_reference_index"), default=0)
                    for item in valid_history
                ],
                *[
                    _safe_int(item.get("reference_index") or item.get("selected_reference_index"), default=0)
                    for item in invalid_partials
                ],
            ]
            if index > 0
        }
    )
    return valid_history, {
        "invalid_partial_reference_detected": bool(invalid_partials),
        "invalid_partial_reference_index": invalid_index,
        "invalid_reason": "missing_price_and_metadata_or_score"
        if invalid_partials
        else "",
        "valid_reference_history_count": len(valid_history),
        "valid_reference_history_max_index": last_valid_index,
        "invalid_partial_reference_count": len(invalid_partials),
        "invalid_partial_reference_indices": [
            _safe_int(item.get("reference_index") or item.get("selected_reference_index"), default=0)
            for item in invalid_partials
            if _safe_int(item.get("reference_index") or item.get("selected_reference_index"), default=0) > 0
        ],
        "processed_reference_indices": processed_indices,
        "processed_reference_identities": _processed_reference_identity_summary([item for item in history if isinstance(item, dict)]),
    }


def _load_reference_continuation_plan(
    task_result: dict[str, Any] | None,
    *,
    first_stage_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = _segment2_task_payload(task_result)
    task_id = str(task.get("task_id") or "")
    target_fingerprint = _target_fingerprint(task)
    first_stage_evidence = first_stage_evidence or _load_first_stage_s10_ready_evidence()
    rejected_candidates: list[dict[str, Any]] = []
    candidates, source_evidence = _reference_continuation_source_candidates(
        task_id,
        task,
        first_stage_evidence,
    )
    for source_path, result in candidates:
        if not result:
            continue
        validation = validate_second_stage_continuation_state_for_current_task(
            task_id=task_id,
            target_fingerprint=target_fingerprint,
            first_stage_evidence=first_stage_evidence,
            candidate_result=result,
            source_path=source_path,
        )
        if not validation.get("continue_allowed"):
            rejected_candidates.append(validation)
            continue
        explicit_next = result.get("next_reference_index") or result.get("continue_from_reference_index")
        current_reference = result.get("current_reference") if isinstance(result.get("current_reference"), dict) else {}
        previous_index = current_reference.get("reference_index")
        history = result.get("reference_history") if isinstance(result.get("reference_history"), list) else []
        if not history and current_reference:
            history = [current_reference]
        valid_history, history_meta = _sanitize_reference_history_for_continuation(history)
        last_valid_index = _safe_int(history_meta.get("valid_reference_history_max_index"), default=0)
        invalid_partial_index = _safe_int(history_meta.get("invalid_partial_reference_index"), default=0)
        processed_max_index = max(last_valid_index, invalid_partial_index)
        recovered_next_index = processed_max_index + 1 if processed_max_index > 0 else 1
        try:
            explicit_next_index = int(explicit_next) if explicit_next is not None else int(previous_index) + 1
        except (TypeError, ValueError):
            explicit_next_index = recovered_next_index
        if history_meta.get("invalid_partial_reference_detected"):
            next_index = max(explicit_next_index, recovered_next_index)
        else:
            next_index = explicit_next_index
        previous_index = processed_max_index or previous_index
        selection = result.get("selection") if isinstance(result.get("selection"), dict) else {}
        recollect_reference_index = _safe_int(
            result.get("recollect_reference_index")
            or selection.get("recollect_reference_index"),
            default=0,
        )
        continue_reason = str(result.get("continue_reason") or "")
        recollect_reason = str(result.get("recollect_reason") or selection.get("recollect_reason") or "")
        final_candidate_status = (
            result.get("final_reference_candidate_status")
            or selection.get("final_reference_candidate_status")
        )
        boundary_reference_index = _safe_int(
            result.get("boundary_reference_index")
            or selection.get("boundary_reference_index"),
            default=0,
        )
        recovered_from_backup = "pre_run_result_backups" in source_path
        recovered_from_task_artifact = bool(task_id and str(_task_artifact_dir(task_id)) in source_path)
        return {
            "continuation_mode": True,
            "source_path": source_path,
            "continuation_source_selected": source_path,
            "continuation_state_source_priority": source_evidence.get("continuation_source_candidates_checked"),
            "continuation_source_recovered": recovered_from_backup or recovered_from_task_artifact,
            "continuation_source_recovered_from_backup": recovered_from_backup,
            "continuation_source_recovered_from_task_artifact": recovered_from_task_artifact,
            "continuation_expected_from_dispatcher_state": source_evidence.get("continuation_expected_from_dispatcher_state"),
            "continuation_expected_from_pre_run_isolation": source_evidence.get("continuation_expected_from_pre_run_isolation"),
            "pre_run_backup_candidates_count": source_evidence.get("pre_run_backup_candidates_count"),
            "previous_status": result.get("status"),
            "previous_reference_index": previous_index,
            "next_reference_index": next_index,
            "continue_reason": continue_reason,
            "reference_history": valid_history,
            "final_reference_recollect_required": bool(
                result.get("final_reference_recollect_required")
                or selection.get("final_reference_recollect_required")
                or continue_reason == "BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
            ),
            "recollect_reference_index": recollect_reference_index or None,
            "recollect_reason": recollect_reason,
            "boundary_reference_index": boundary_reference_index or None,
            "boundary_reference_score": result.get("boundary_reference_score") or selection.get("boundary_reference_score"),
            "boundary_reference_price_yuan": result.get("boundary_reference_price_yuan")
            or selection.get("boundary_reference_price_yuan"),
            "target_score": result.get("target_score"),
            "final_reference_candidate_index": result.get("final_reference_candidate_index")
            or selection.get("final_reference_candidate_index"),
            "final_reference_candidate_status": final_candidate_status,
            "invalid_partial_reference_detected": history_meta.get("invalid_partial_reference_detected"),
            "invalid_partial_reference_index": history_meta.get("invalid_partial_reference_index"),
            "invalid_reason": history_meta.get("invalid_reason"),
            "processed_reference_indices": history_meta.get("processed_reference_indices"),
            "processed_reference_identities": history_meta.get("processed_reference_identities"),
            "continuation_state_validation": validation,
            "continuation_recovered_next_reference_index": recovered_next_index
            if history_meta.get("invalid_partial_reference_detected")
            else None,
            "discarded_invalid_partial_reference_count": history_meta.get("invalid_partial_reference_count"),
        }
    continuation_expected = bool(
        source_evidence.get("continuation_expected_from_dispatcher_state")
        or source_evidence.get("continuation_expected_from_pre_run_isolation")
    )
    source_lost_after_isolation = continuation_expected and bool(
        source_evidence.get("continuation_expected_from_pre_run_isolation")
        or source_evidence.get("pre_run_backup_candidates_count")
    )
    missing_stop_code = (
        SECOND_STAGE_CONTINUATION_SOURCE_LOST_AFTER_PRE_RUN_ISOLATION
        if source_lost_after_isolation
        else SECOND_STAGE_CONTINUATION_SOURCE_MISSING
    )
    return {
        "continuation_mode": False,
        "source_path": "",
        "previous_status": None,
        "previous_reference_index": None,
        "next_reference_index": 1,
        "reference_history": [],
        "continuation_rejected_candidates": rejected_candidates,
        "continuation_source_candidates_checked": source_evidence.get("continuation_source_candidates_checked"),
        "continuation_expected_from_dispatcher_state": source_evidence.get("continuation_expected_from_dispatcher_state"),
        "continuation_expected_from_pre_run_isolation": source_evidence.get("continuation_expected_from_pre_run_isolation"),
        "pre_run_backup_candidates_count": source_evidence.get("pre_run_backup_candidates_count"),
        "continuation_source_missing_blocked_default_to_one": continuation_expected,
        "continuation_source_missing_stop_code": missing_stop_code if continuation_expected else "",
        "reference_loop_state_reset_detected": continuation_expected,
        "reference_loop_state_reset_code": REFERENCE_LOOP_STATE_RESET_DETECTED if continuation_expected else "",
        "continuation_rejected_reason": "SECOND_STAGE_CONTINUATION_REJECTED_STALE_TASK_STATE"
        if rejected_candidates
        else "",
    }


SECOND_STAGE_IN_FLIGHT_START_STATES = {"S11", "S12", "S13", "S14"}


def _second_stage_in_flight_continuation_reset_blocked(
    recognized_state: str | None,
    continuation_plan: dict[str, Any] | None,
) -> bool:
    state = str(recognized_state or "UNKNOWN")
    plan = continuation_plan if isinstance(continuation_plan, dict) else {}
    return (
        state in SECOND_STAGE_IN_FLIGHT_START_STATES
        and plan.get("continuation_mode") is not True
        and bool(plan.get("continuation_rejected_candidates"))
    )


def _second_stage_start_page_routing_evidence(
    *,
    recognized_state: str | None,
    context: dict[str, Any],
    first_stage_evidence: dict[str, Any],
) -> dict[str, Any]:
    state = str(recognized_state or "UNKNOWN")
    current_reference_index = _safe_int(context.get("current_reference_index"), default=0)
    expected_card = _expected_reference_card_with_continuation_context(
        first_stage_evidence,
        current_reference_index,
        context.get("continuation_plan"),
    )
    current_reference = context.get("current_reference") if isinstance(context.get("current_reference"), dict) else {}
    executor_name = f"handle_{state.lower()}" if state in SECOND_STAGE_IN_FLIGHT_START_STATES else ""
    context_valid = bool(first_stage_evidence.get("ready") and current_reference_index > 0 and expected_card)
    in_flight_allowed = state in SECOND_STAGE_IN_FLIGHT_START_STATES and context_valid
    missing_reasons: list[str] = []
    if not first_stage_evidence.get("ready"):
        missing_reasons.append("first_stage_s10_ready_missing")
    if current_reference_index <= 0:
        missing_reasons.append("current_reference_index_missing")
    if current_reference_index > 0 and not expected_card:
        missing_reasons.append("first_stage_expected_reference_card_missing")
    if state not in SECOND_STAGE_IN_FLIGHT_START_STATES:
        missing_reasons.append("recognized_state_not_second_stage_in_flight")
    if state == "S13" and not context_valid:
        stop_code = "S13_RECOGNIZED_BUT_SECOND_STAGE_CONTEXT_MISSING"
    elif state in SECOND_STAGE_IN_FLIGHT_START_STATES and not context_valid:
        stop_code = "SECOND_STAGE_S13_CONTEXT_NOT_RESTORABLE" if state == "S13" else "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY"
    else:
        stop_code = "" if in_flight_allowed else "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY"
    return {
        "recognized_state": state,
        "expected_stage": "second_stage",
        "second_stage_context_valid": context_valid,
        "current_reference_index": current_reference_index,
        "current_reference_context_exists": bool(current_reference or expected_card),
        "expected_reference_card_exists": bool(expected_card),
        "in_flight_page_allowed": in_flight_allowed,
        "selected_executor_name": executor_name if in_flight_allowed else "",
        "executor_registry_hit": in_flight_allowed,
        "blocked_not_at_s10_reason": ";".join(missing_reasons),
        "contract_stop_code": stop_code,
        "page_contract_executor_missing": False if state in SECOND_STAGE_IN_FLIGHT_START_STATES else True,
        "context_missing_reasons": missing_reasons,
    }


S11_REPORT_ENTRY_TEXT = "查看完整报告"
S11_OFFICIAL_REPORT_ENTRY_TEXTS = (S11_REPORT_ENTRY_TEXT,)
S11_MERCHANT_SELF_CHECK_MARKER_TEXT = "商家自检车况"
S11_REPORT_ENTRY_RULE_VERSION_V1_27 = "S11_REPORT_ENTRY_FINE_SCROLL_PAGE_CONTRACT_UPDATED"
S11_REPORT_ENTRY_RULE_VERSION_V1_38 = "S11_REPORT_ENTRY_XML_STABILIZATION_PAGE_CONTRACT_UPDATED"
S11_REPORT_ENTRY_RULE_VERSION_V1_39 = "S11_FRESH_PAIR_AND_STALE_XML_PAGE_CONTRACT_UPDATED"
S11_REPORT_ENTRY_RULE_VERSION_NO_FIXED_COORDINATE = "S11_REPORT_ENTRY_NO_FIXED_COORDINATE_CLICK_PATCH"
S11_REPORT_ENTRY_RULE_VERSION_DYNAMIC_VISUAL_BINDING = "S11_REPORT_ENTRY_DYNAMIC_VISUAL_BUTTON_BINDING_PATCH"
S11_FIRST_SCROLL_STRATEGY = "two_thirds_screen_trial"
S11_FIRST_SCROLL_SCREEN_RATIO = 0.66
S11_REPORT_WEAK_MARKER_TEXTS = ("官方检测", "检测报告", "完整报告", "车况报告", "车况", "报告")
S11_MERCHANT_SELF_CHECK_TEXTS = ("商家自检", "商家自检报告", "商家检测")
S11_BOTTOM_BAR_TEXTS = {"电话", "收藏", "咨询", "咨询车况", "讲价", "降价", "查看报价", "联系顾问"}
S11_LOCAL_STRUCTURE_RIGHT_ADVISOR_TEXTS = ("找顾问解读报告", "顾问解读", "现在看")
S11_LOCAL_STRUCTURE_FORBIDDEN_TEXTS = (
    "找顾问解读报告",
    "现在看",
    "联系顾问",
    "查看报价",
    "讲价",
    "电话",
    "收藏",
    "咨询",
    "咨询车况",
    "商家直播",
    "实车讲解",
    "进入房间",
)
S11_REPORT_ENTRY_XML_S10_STRONG_SIGNALS = ("品牌专区", "价格从低到高", "车型配置", "综合排序")
S11_REPORT_ENTRY_XML_S12_STRONG_SIGNALS = ("保险理赔记录", "理赔次数", "最大金额", "重大问题排查", "车身外观")
S11_REPORT_CONTEXT_STRONG_MARKERS = (
    "查看完整报告",
    "完整报告",
    "检测报告",
    "官方检测",
    "车况报告",
    "报告",
    "商家自检车况",
)
S11_REPORT_ENTRY_LOWER_AREA_MARKERS = (
    "瓜子官方检测报告",
    "外观、内饰检测视频",
    "车身外观",
    "检测说明",
    "重大事故",
    "车身骨架",
    "机舱工况",
)


def _s11_report_entry_contract_plan_evidence() -> dict[str, Any]:
    plan = build_s11_report_entry_action_plan()
    binding_trace = build_action_plan_binding_trace(
        plan,
        action_algorithm_used="s11_report_entry_dynamic_binding",
    )
    return {
        "contract_action_plan": plan,
        "contract_expected": plan.get("expected"),
        "contract_action_algorithm": plan.get("action_algorithm"),
        "contract_forbidden_actions": plan.get("forbidden_actions"),
        **binding_trace,
    }


def _s11_report_fresh_pair_evidence(snapshot: dict[str, Any], *, action_context: str, iteration: int) -> dict[str, Any]:
    screenshot_path = Path(str(snapshot.get("screenshot_path") or ""))
    xml_path = Path(str(snapshot.get("xml_path") or ""))
    xml_text = str(snapshot.get("fresh_xml") or "")
    visible_texts = [str(item) for item in snapshot.get("visible_texts") or []]
    visible_blob = "\n".join(visible_texts) + "\n" + str(snapshot.get("visible_blob") or "")
    xml_dump_ok = bool(xml_text.strip()) and xml_path.exists() and xml_path.stat().st_size > 0
    xml_mtime_valid = False
    if xml_dump_ok and screenshot_path.exists():
        try:
            xml_mtime_valid = xml_path.stat().st_mtime >= screenshot_path.stat().st_mtime - 2.0
        except OSError:
            xml_mtime_valid = False
    elif xml_dump_ok:
        xml_mtime_valid = True
    s10_signals = sorted({signal for signal in S11_REPORT_ENTRY_XML_S10_STRONG_SIGNALS if signal in visible_blob})
    s12_signals = sorted({signal for signal in S11_REPORT_ENTRY_XML_S12_STRONG_SIGNALS if signal in visible_blob})
    stale_xml = bool(s10_signals)
    page_identity_check = "STALE_S10_XML_IN_S11_REPORT_SEARCH" if stale_xml else "S11_XML_ACCEPTABLE_FOR_REPORT_SEARCH"
    fresh_pair_id = _sha256_text("|".join([action_context, str(iteration), str(screenshot_path), str(xml_path), str(xml_path.stat().st_mtime if xml_path.exists() else "")]))[:16]
    return {
        "fresh_pair_id": fresh_pair_id,
        "action_context": action_context,
        "screenshot_path": str(screenshot_path) if str(screenshot_path) else "",
        "xml_path": str(xml_path) if str(xml_path) else "",
        "xml_dump_ok": xml_dump_ok,
        "xml_mtime_valid": xml_mtime_valid,
        "page_identity_check": page_identity_check,
        "s11_xml_stale": stale_xml,
        "s11_xml_s10_strong_signals": s10_signals,
        "s11_xml_s12_strong_signals": s12_signals,
        "fresh_evidence_pair_ok": bool(xml_dump_ok and xml_mtime_valid and not stale_xml),
    }


def _s11_report_context_marker_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    texts = [str(item) for item in snapshot.get("visible_texts") or []]
    blob = "\n".join(texts) + "\n" + str(snapshot.get("visible_blob") or "")
    marker_hits = sorted({marker for marker in S11_REPORT_CONTEXT_STRONG_MARKERS if marker in blob})
    return {
        "s11_report_context_markers_in_xml": marker_hits,
        "s11_report_context_marker_count": len(marker_hits),
    }


def _s11_internal_visible_region_check(
    snapshot: dict[str, Any],
    *,
    action_context: str,
    report_scroll_count: int,
    report_reposition_count: int,
    page_recognized: str | None,
) -> dict[str, Any]:
    marker_evidence = _s11_report_context_marker_evidence(snapshot)
    texts = [str(item) for item in snapshot.get("visible_texts") or []]
    blob = "\n".join(texts) + "\n" + str(snapshot.get("visible_blob") or "")
    lower_area_hits = sorted({marker for marker in S11_REPORT_ENTRY_LOWER_AREA_MARKERS if marker in blob})
    in_report_search_context = action_context == "S11_REPORT_SEARCH" and page_recognized == "S11"
    # We cannot read screenshot text here. The contract signal for internal
    # mismatch is: the controlled S11 report-entry search has reached the
    # lower report area, yet XML exposes none of the strong report-context
    # nodes that should accompany a usable report-entry decision.
    search_has_reached_report_area = bool(lower_area_hits or report_reposition_count > 0)
    mismatch = bool(
        in_report_search_context
        and search_has_reached_report_area
        and marker_evidence["s11_report_context_marker_count"] == 0
    )
    reason = ""
    if mismatch:
        reason = "s11_report_search_lower_region_without_report_context_markers_in_xml"
    return {
        "s11_internal_visible_region_check": True,
        "s11_internal_visible_region_mismatch": mismatch,
        "s11_internal_visible_region_mismatch_reason": reason,
        "s11_internal_lower_area_markers_in_xml": lower_area_hits,
        "s11_internal_lower_area_marker_count": len(lower_area_hits),
        "s11_fresh_pair_valid_after_internal_check": not mismatch,
        **marker_evidence,
    }


def _node_has_label(node: dict[str, Any], target: str) -> bool:
    return target in {str(label).strip() for label in node.get("labels", [])}


def _find_s11_official_report_entry_node(snapshot: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    for text in S11_OFFICIAL_REPORT_ENTRY_TEXTS:
        node = _find_exact_label_node(snapshot, text)
        if node is not None:
            return node, text
    return None, None


def _snapshot_text_contains_any(snapshot: dict[str, Any], terms: tuple[str, ...] | list[str]) -> bool:
    texts = [str(item) for item in snapshot.get("visible_texts") or []]
    blob = str(snapshot.get("visible_blob") or "")
    return any(term and (term in blob or any(term in text for text in texts)) for term in terms)


def _s11_report_weak_marker_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    texts = [str(item) for item in snapshot.get("visible_texts") or []]
    blob = str(snapshot.get("visible_blob") or "")
    hits: list[str] = []
    for term in S11_REPORT_WEAK_MARKER_TEXTS:
        if term and (term in blob or any(term in text for text in texts)):
            hits.append(term)
    return {
        "s11_report_weak_marker_seen": bool(hits),
        "s11_report_weak_marker_hits": sorted(set(hits)),
    }


def _snapshot_exact_text_seen(snapshot: dict[str, Any], target: str) -> bool:
    if not target:
        return False
    if _find_exact_label_node(snapshot, target) is not None:
        return True
    return any(str(item).strip() == target for item in snapshot.get("visible_texts") or [])


def _bounds_intersect(a: tuple[int, int, int, int] | list[int] | None, b: tuple[int, int, int, int] | list[int] | None) -> bool:
    if not a or not b:
        return False
    ax1, ay1, ax2, ay2 = [int(v) for v in a]
    bx1, by1, bx2, by2 = [int(v) for v in b]
    return max(ax1, bx1) < min(ax2, bx2) and max(ay1, by1) < min(ay2, by2)


def _bounds_intersection_area(a: tuple[int, int, int, int] | list[int] | None, b: tuple[int, int, int, int] | list[int] | None) -> int:
    if not _bounds_intersect(a, b):
        return 0
    ax1, ay1, ax2, ay2 = [int(v) for v in a or (0, 0, 0, 0)]
    bx1, by1, bx2, by2 = [int(v) for v in b or (0, 0, 0, 0)]
    return max(0, min(ax2, bx2) - max(ax1, bx1)) * max(0, min(ay2, by2) - max(ay1, by1))


def _bounds_area(bounds: tuple[int, int, int, int] | list[int] | None) -> int:
    if not bounds:
        return 0
    x1, y1, x2, y2 = [int(v) for v in bounds]
    return max(x2 - x1, 0) * max(y2 - y1, 0)


def _text_nodes_containing_any(snapshot: dict[str, Any], terms: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    hits: list[dict[str, Any]] = []
    for node in nodes:
        bounds = node.get("bounds")
        if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        labels = [str(label).strip() for label in node.get("labels", []) if str(label).strip()]
        if any(term and any(term in label for label in labels) for term in terms):
            hits.append({**node, "bounds": bounds, "labels": labels})
    return hits


def _s11_merchant_self_check_marker_seen(snapshot: dict[str, Any]) -> bool:
    return _snapshot_exact_text_seen(snapshot, S11_MERCHANT_SELF_CHECK_MARKER_TEXT)


def _recent_page_signature_unchanged(previous_signatures: list[str] | None) -> bool:
    return bool(
        previous_signatures
        and len(previous_signatures) >= 2
        and previous_signatures[-1]
        and previous_signatures[-1] == previous_signatures[-2]
    )


def _s11_report_missing_evidence(
    snapshot: dict[str, Any],
    *,
    report_scroll_count: int,
    report_reposition_count: int,
    max_search_scrolls: int,
    visibility: dict[str, Any] | None = None,
    previous_signatures: list[str] | None = None,
    scroll_step_evidence: dict[str, Any] | None = None,
    local_structure_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    official_node, official_text = _find_s11_official_report_entry_node(snapshot)
    visible_signature = _sha256_text("|".join(str(item) for item in snapshot.get("visible_texts", [])))
    signatures = list(previous_signatures or [])
    if not signatures or signatures[-1] != visible_signature:
        signatures.append(visible_signature)
    page_no_longer_changes = _recent_page_signature_unchanged(signatures)
    scroll_limit_reached = report_scroll_count >= max_search_scrolls
    bottom_terms = ("鑱旂郴鍗栧", "鏌ョ湅鎶ヤ环", "鐩稿叧鎺ㄨ崘", "鍒板簳", "娌℃湁鏇村", "淇濋殰鏈嶅姟")
    report_entry_search_reached_bottom = bool(
        page_no_longer_changes
        or scroll_limit_reached
        or _snapshot_text_contains_any(snapshot, bottom_terms)
    )
    view_full_report_seen = _snapshot_exact_text_seen(snapshot, S11_REPORT_ENTRY_TEXT)
    merchant_marker_seen = _s11_merchant_self_check_marker_seen(snapshot)
    scroll_step_evidence = scroll_step_evidence or {}
    weak_marker = _s11_report_weak_marker_evidence(snapshot)
    return {
        "recognized_page": "S11",
        "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_V1_27,
        "merchant_self_check_seen": _snapshot_text_contains_any(snapshot, S11_MERCHANT_SELF_CHECK_TEXTS),
        "merchant_self_check_marker_seen": merchant_marker_seen,
        "merchant_self_check_marker_text": S11_MERCHANT_SELF_CHECK_MARKER_TEXT,
        "official_report_entry_seen": official_node is not None,
        "official_report_entry_text": official_text,
        "view_full_report_exact_text_seen": view_full_report_seen,
        "view_full_report_text": S11_REPORT_ENTRY_TEXT,
        "official_report_entry_texts_considered": list(S11_OFFICIAL_REPORT_ENTRY_TEXTS),
        "local_structure_binding_disabled": True,
        "visual_binding_disabled": True,
        "ocr_disabled": True,
        "screenshot_text_recognition_disabled": True,
        "report_entry_detection_strategy": "NOT_AVAILABLE_CONFIRMED" if merchant_marker_seen else "EXACT_XML_TEXT_HALF_SCREEN_SCROLL_NO_DECISIVE_MARKER",
        **weak_marker,
        "s11_report_search_scroll_mode": scroll_step_evidence.get("s11_report_search_scroll_mode"),
        "s11_report_entry_backtrack_attempted": bool(scroll_step_evidence.get("s11_report_entry_backtrack_attempted")),
        "s11_report_entry_overshoot_suspected": bool(scroll_step_evidence.get("s11_report_entry_overshoot_suspected")),
        "s11_report_scroll_step_px": scroll_step_evidence.get("s11_report_scroll_step_px"),
        "s11_first_scroll_done": bool(scroll_step_evidence.get("s11_first_scroll_done")),
        "s11_first_scroll_step_px": scroll_step_evidence.get("s11_first_scroll_step_px"),
        "s11_report_search_iterations": scroll_step_evidence.get("s11_report_search_iterations"),
        "s11_report_entry_scroll_step_ratio": scroll_step_evidence.get("s11_report_entry_scroll_step_ratio", 0.5),
        "s11_report_entry_scroll_distance_px": scroll_step_evidence.get("s11_report_entry_scroll_distance_px"),
        "view_full_report_found_after_scroll_attempt": scroll_step_evidence.get("view_full_report_found_after_scroll_attempt"),
        "stop_scroll_reason": scroll_step_evidence.get("stop_scroll_reason"),
        "view_full_report_full_visible": bool((visibility or {}).get("exact_report_entry_fully_visible")),
        "report_entry_scroll_count": report_scroll_count,
        "s11_report_scroll_count": report_scroll_count,
        "s11_report_reposition_scroll_count": report_reposition_count,
        "report_entry_search_reached_bottom": report_entry_search_reached_bottom,
        "page_no_longer_changes": page_no_longer_changes,
        "scroll_limit_reached": scroll_limit_reached,
        "report_visibility": visibility or {},
        "s11_xml_path": str(snapshot.get("xml_path") or ""),
        "s11_screenshot_path": str(snapshot.get("screenshot_path") or ""),
    }


def _stop_s11_report_entry_search_exhausted_without_decisive_marker(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    evidence: dict[str, Any],
) -> None:
    contract_stop(
        context,
        "S11",
        "S11_REPORT_ENTRY_SEARCH_EXHAUSTED_WITHOUT_DECISIVE_MARKER",
        "S11 report-entry search exhausted without exact 鏌ョ湅瀹屾暣鎶ュ憡 and without exact 鍟嗗鑷杞﹀喌 marker.",
        {**snapshot, **evidence},
    )


def _detect_s11_bottom_bar(snapshot: dict[str, Any]) -> dict[str, Any]:
    extent = _visible_bounds_extent(snapshot)
    if extent is None:
        return {
            "bottom_bar_detected": False,
            "bottom_bar_bounds": None,
            "bottom_action_hits": [],
        }
    viewport, _source = extent
    x1, y1, x2, y2 = viewport
    height = max(y2 - y1, 1)
    bottom_threshold = y1 + int(height * 0.74)
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    hits: list[dict[str, Any]] = []
    for node in nodes:
        bounds = node.get("bounds")
        if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1] or bounds[1] < bottom_threshold:
            continue
        labels = {str(label).strip() for label in node.get("labels", [])}
        matched = sorted(label for label in labels if label in S11_BOTTOM_BAR_TEXTS)
        if matched:
            hits.append({"text": matched[0], "bounds": list(bounds)})
    if not hits:
        return {
            "bottom_bar_detected": False,
            "bottom_bar_bounds": None,
            "bottom_action_hits": [],
        }
    top = min(item["bounds"][1] for item in hits)
    bottom = max(item["bounds"][3] for item in hits)
    return {
        "bottom_bar_detected": True,
        "bottom_bar_bounds": [x1, top, x2, bottom],
        "bottom_action_hits": hits,
    }


def _s11_report_entry_visibility(snapshot: dict[str, Any], report_node: dict[str, Any] | None) -> dict[str, Any]:
    extent = _visible_bounds_extent(snapshot)
    if extent is None:
        return {
            "exact_report_entry_seen": report_node is not None,
            "exact_report_entry_fully_visible": False,
            "exact_report_entry_in_safe_click_region": False,
            "reason": "no_visible_xml_bounds",
        }
    viewport, source = extent
    vx1, vy1, vx2, vy2 = viewport
    height = max(vy2 - vy1, 1)
    safe_top = vy1 + int(height * 0.05)
    bottom_bar = _detect_s11_bottom_bar(snapshot)
    margin = max(80, int(height * 0.03))
    if bottom_bar.get("bottom_bar_detected") and bottom_bar.get("bottom_bar_bounds"):
        safe_bottom = int(bottom_bar["bottom_bar_bounds"][1]) - margin
        safe_bottom_source = "bottom_bar_top_minus_margin"
    else:
        safe_bottom = vy1 + int(height * 0.82)
        safe_bottom_source = "screen_height_82_percent"
    safe_bottom = max(safe_top + 1, min(safe_bottom, vy2 - margin))
    base = {
        "exact_report_entry_seen": report_node is not None,
        "viewport_bounds": list(viewport),
        "bounds_source": source,
        "screen_height": height,
        "safe_top_y": safe_top,
        "safe_bottom_y": safe_bottom,
        "safe_bottom_source": safe_bottom_source,
        **bottom_bar,
    }
    if report_node is None:
        return {
            **base,
            "report_entry_bounds": None,
            "exact_report_entry_fully_visible": False,
            "exact_report_entry_in_safe_click_region": False,
            "reason": "not_seen",
        }
    bounds = report_node.get("bounds")
    if not bounds:
        return {
            **base,
            "report_entry_bounds": None,
            "exact_report_entry_fully_visible": False,
            "exact_report_entry_in_safe_click_region": False,
            "reason": "missing_bounds",
        }
    node_height = max(bounds[3] - bounds[1], 0)
    within_screen = bounds[0] >= vx1 and bounds[2] <= vx2 and bounds[1] >= vy1 and bounds[3] <= vy2
    height_normal = 20 <= node_height <= max(int(height * 0.12), 21)
    below_safe_bottom = bounds[3] > safe_bottom
    too_close_to_bottom = bounds[3] > safe_bottom - max(40, int(height * 0.015))
    overlapped_bottom_bar = bool(
        bottom_bar.get("bottom_bar_detected")
        and bottom_bar.get("bottom_bar_bounds")
        and bounds[3] > int(bottom_bar["bottom_bar_bounds"][1])
    )
    fully_visible = within_screen and height_normal and not below_safe_bottom and not overlapped_bottom_bar
    # V1.27: top safe-line proximity is not a blocker for this contract. The
    # entry only has to be fully visible and clear of the bottom fixed action bar.
    in_safe = fully_visible
    reasons: list[str] = []
    if not within_screen:
        reasons.append("partially_visible")
    if not height_normal:
        reasons.append("abnormal_node_height")
    if below_safe_bottom:
        reasons.append("below_safe_bottom")
    if overlapped_bottom_bar:
        reasons.append("overlapped_bottom_bar")
    if too_close_to_bottom:
        reasons.append("too_close_to_bottom")
    return {
        **base,
        "report_entry_bounds": list(bounds),
        "node_height": node_height,
        "within_screen": within_screen,
        "node_height_normal": height_normal,
        "below_safe_bottom": below_safe_bottom,
        "overlapped_bottom_bar": overlapped_bottom_bar,
        "too_close_to_bottom": too_close_to_bottom,
        "above_safe_top": bounds[1] < safe_top,
        "exact_report_entry_fully_visible": fully_visible,
        "exact_report_entry_in_safe_click_region": in_safe,
        "reason": "fully_visible_safe" if in_safe else ",".join(reasons or ["not_safe"]),
    }


def _s11_report_entry_unsafe_reposition_reason(visibility: dict[str, Any]) -> str:
    if not visibility.get("exact_report_entry_fully_visible"):
        return str(visibility.get("reason") or "not_fully_visible")
    if not visibility.get("exact_report_entry_in_safe_click_region"):
        return str(visibility.get("reason") or "not_in_safe_click_region")
    if visibility.get("overlapped_bottom_bar"):
        return "overlapped_bottom_bar"
    if visibility.get("below_safe_bottom"):
        return "below_safe_bottom"
    if visibility.get("too_close_to_bottom"):
        return "too_close_to_bottom"
    return ""


def _s11_report_scroll_points(
    snapshot: dict[str, Any],
    *,
    small_reposition: bool,
    visibility: dict[str, Any] | None = None,
    search_scroll_mode: str = "normal",
) -> dict[str, Any]:
    extent = _visible_bounds_extent(snapshot)
    if extent is None:
        bounds = (0, 0, 1220, 2712)
        source = "fallback_nominal_screen"
    else:
        bounds, source = extent
    x1, y1, x2, y2 = bounds
    width = max(x2 - x1, 1)
    height = max(y2 - y1, 1)
    x = x1 + width // 2
    mode = "medium_report_entry_scroll"
    duration = 650
    if small_reposition and visibility:
        report_bounds = visibility.get("report_entry_bounds") or []
        safe_bottom = int(visibility.get("safe_bottom_y") or (y1 + int(height * 0.86)))
        overlap_px = 0
        if len(report_bounds) == 4:
            overlap_px = max(0, int(report_bounds[3]) - safe_bottom)
        distance = min(max(overlap_px + int(height * 0.08), int(height * 0.08)), int(height * 0.22))
        y_start = y1 + int(height * 0.68)
        y_end = max(y1 + int(height * 0.32), y_start - distance)
        duration = 1050
        mode = "candidate_bottom_reposition_scroll"
    elif search_scroll_mode == "first":
        distance = int(height * S11_FIRST_SCROLL_SCREEN_RATIO)
        y_start = y1 + int(height * 0.86)
        y_end = max(y1 + int(height * 0.16), y_start - distance)
        duration = 950
        mode = "first_two_thirds_report_entry_scroll"
    elif search_scroll_mode in {"small", "fine", "normal", "backtrack"}:
        distance = int(height * 0.18)
        y_start = y1 + int(height * 0.72)
        y_end = max(y1 + int(height * 0.30), y_start - distance)
        duration = 850
        mode = "small_report_entry_scroll"
    else:
        distance = int(height * 0.18)
        y_start = y1 + int(height * 0.72)
        y_end = max(y1 + int(height * 0.30), y_start - distance)
        duration = 850
        mode = "small_report_entry_scroll"
    if y_start <= y_end:
        y_start = y1 + int(height * 0.78)
        y_end = y1 + int(height * 0.28)
    return {
        "bounds_source": source,
        "scroll_region_bounds": list(bounds),
        "scroll_mode": mode,
        "swipe_x_start": x,
        "swipe_y_start": y_start,
        "swipe_x_end": x,
        "swipe_y_end": y_end,
        "swipe_duration_ms": duration,
        "scroll_distance_px": abs(y_start - y_end),
        "scroll_distance_ratio": round(abs(y_start - y_end) / height, 4),
        "s11_report_entry_scroll_step_ratio": round(abs(y_start - y_end) / height, 4),
        "s11_report_entry_scroll_distance_px": abs(y_start - y_end),
        "s11_report_entry_reposition_scroll": bool(small_reposition),
        "s11_report_search_scroll_mode": "first" if search_scroll_mode == "first" else "small",
        "s11_report_scroll_step_px": abs(y_start - y_end),
        "s11_first_scroll_strategy": S11_FIRST_SCROLL_STRATEGY if search_scroll_mode == "first" else "",
        "s11_first_scroll_screen_ratio": S11_FIRST_SCROLL_SCREEN_RATIO if search_scroll_mode == "first" else None,
        "s11_first_scroll_requested_distance_px": distance if search_scroll_mode == "first" else None,
    }


def _execute_s11_report_scroll(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    step_name: str,
    action_name: str,
    attempt_index: int,
    small_reposition: bool,
    visibility: dict[str, Any] | None = None,
    search_scroll_mode: str = "normal",
) -> tuple[dict[str, Any], int, int, dict[str, Any]]:
    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    timing: TimingRecorder = context["timing"]
    points = _s11_report_scroll_points(
        snapshot,
        small_reposition=small_reposition,
        visibility=visibility,
        search_scroll_mode=search_scroll_mode,
    )
    _, action_ms = contract_execute_swipe(
        context,
        snapshot,
        "S11",
        "scroll_to_report",
        (
            int(points["swipe_x_start"]),
            int(points["swipe_y_start"]),
            int(points["swipe_x_end"]),
            int(points["swipe_y_end"]),
            int(points["swipe_duration_ms"]),
        ),
        evidence={
            **points,
            "scroll_attempt_index": attempt_index,
            "visibility_before_scroll": visibility or {},
            "s11_report_search_scroll_mode": search_scroll_mode,
        },
    )
    wait_ms = 400 if small_reposition else 500
    time.sleep(wait_ms / 1000)
    timing.add(
        step_name=step_name,
        page_name="S11",
        action_name=action_name,
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=wait_ms,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            **points,
            "scroll_attempt_index": attempt_index,
            "visibility_before_scroll": visibility or {},
            "s11_report_search_scroll_mode": search_scroll_mode,
            "reason_category": "CONTROLLED_SCROLL",
            "reason_detail": "scroll S11 content until exact 鏌ョ湅瀹屾暣鎶ュ憡 is fully visible and outside the bottom fixed action bar",
            "solution": "do not click when XML exposes the text at the unsafe bottom edge",
        },
    )
    fresh_start = time.perf_counter()
    fresh = _capture_with_global_popup_guard(
        context,
        f"s11_report_entry_search_{attempt_index}",
        current_stage="S11_REPORT_SEARCH",
    )
    fresh_ms = int((time.perf_counter() - fresh_start) * 1000)
    fresh_node, fresh_report_text = _find_s11_official_report_entry_node(fresh)
    fresh_visibility = _s11_report_entry_visibility(fresh, fresh_node)
    fresh_visibility = {
        **fresh_visibility,
        "s11_report_entry_scroll_step_ratio": points.get("s11_report_entry_scroll_step_ratio"),
        "s11_report_entry_scroll_distance_px": points.get("s11_report_entry_scroll_distance_px"),
        "s11_report_entry_reposition_scroll": points.get("s11_report_entry_reposition_scroll"),
        "s11_report_entry_scroll_attempt_index": attempt_index,
        "s11_report_search_scroll_mode": points.get("s11_report_search_scroll_mode"),
        "s11_report_scroll_step_px": points.get("s11_report_scroll_step_px"),
        "s11_first_scroll_strategy": points.get("s11_first_scroll_strategy"),
        "s11_first_scroll_screen_ratio": points.get("s11_first_scroll_screen_ratio"),
        "s11_first_scroll_requested_distance_px": points.get("s11_first_scroll_requested_distance_px"),
        "view_full_report_exact_text_seen_after_scroll": fresh_node is not None,
        "merchant_self_check_marker_seen_after_scroll": _s11_merchant_self_check_marker_seen(fresh),
    }
    timing.add(
        step_name=f"{step_name}_FRESH",
        page_name="S11",
        action_name="fresh_after_s11_report_entry_scroll",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=0,
        transition_wait_ms=fresh_ms,
        screenshot_path=str(fresh.get("screenshot_path") or ""),
        xml_path=str(fresh.get("xml_path") or ""),
        extra={
            "scroll_attempt_index": attempt_index,
            "recognized_page": _recognize_mainline_page(recognizer, fresh),
            "exact_report_entry_seen": fresh_node is not None,
            "official_report_entry_text": fresh_report_text,
            "exact_report_entry_fully_visible": fresh_visibility.get("exact_report_entry_fully_visible"),
            "exact_report_entry_in_safe_click_region": fresh_visibility.get("exact_report_entry_in_safe_click_region"),
            "report_entry_bounds": fresh_visibility.get("report_entry_bounds"),
            "safe_bottom_y": fresh_visibility.get("safe_bottom_y"),
            "bottom_bar_detected": fresh_visibility.get("bottom_bar_detected"),
            "bottom_bar_bounds": fresh_visibility.get("bottom_bar_bounds"),
            "visibility_reason": fresh_visibility.get("reason"),
            "reason_category": "XML_DUMP_SLOW" if fresh_ms > 2000 else "FRESH_CAPTURE",
            "reason_detail": "fresh after S11 controlled scroll/reposition before deciding whether the report entry is safely clickable",
            "solution": "reuse this XML for the next visibility check and never click before safe visibility is true",
        },
    )
    return fresh, action_ms, wait_ms + fresh_ms, fresh_visibility


def _s11_report_entry_xml_refresh_nudge_points(snapshot: dict[str, Any]) -> dict[str, Any]:
    extent = _visible_bounds_extent(snapshot)
    if extent is None:
        bounds = (0, 0, 1220, 2712)
        source = "fallback_nominal_screen"
    else:
        bounds, source = extent
    x1, y1, x2, y2 = bounds
    width = max(x2 - x1, 1)
    height = max(y2 - y1, 1)
    nudge_px = max(int(height * 0.035), 36)
    x = x1 + width // 2
    y_start = y1 + int(height * 0.58)
    y_end = max(y1 + int(height * 0.45), y_start - nudge_px)
    return {
        "bounds_source": source,
        "scroll_region_bounds": list(bounds),
        "scroll_mode": "s11_xml_refresh_micro_nudge",
        "swipe_x_start": x,
        "swipe_y_start": y_start,
        "swipe_x_end": x,
        "swipe_y_end": y_end,
        "swipe_duration_ms": 320,
        "scroll_distance_px": abs(y_start - y_end),
        "scroll_distance_ratio": round(abs(y_start - y_end) / height, 4),
    }


def _execute_s11_report_entry_xml_refresh_nudge(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    attempt_index: int,
    fresh_pair: dict[str, Any],
) -> tuple[dict[str, Any], int, int, dict[str, Any]]:
    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    timing: TimingRecorder = context["timing"]
    points = _s11_report_entry_xml_refresh_nudge_points(snapshot)
    _, action_ms = contract_execute_swipe(
        context,
        snapshot,
        "S11",
        "scroll_to_report",
        (
            int(points["swipe_x_start"]),
            int(points["swipe_y_start"]),
            int(points["swipe_x_end"]),
            int(points["swipe_y_end"]),
            int(points["swipe_duration_ms"]),
        ),
        evidence={
            **points,
            "scroll_attempt_index": attempt_index,
            "s11_xml_stale_recovery": True,
            **fresh_pair,
        },
    )
    wait_ms = 400
    time.sleep(wait_ms / 1000)
    timing.add(
        step_name="S11_STALE_XML_REFRESH_NUDGE",
        page_name="S11",
        action_name="micro_nudge_to_refresh_report_entry_xml",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=wait_ms,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            **points,
            "scroll_attempt_index": attempt_index,
            "s11_xml_stale_recovery": True,
            "reason_category": "S11_XML_STALE_RECOVERY_NUDGE",
            "reason_detail": "XML is stale in S11 report-entry context; use one bounded micro nudge only to refresh accessibility bounds, never as a click target.",
        },
    )
    fresh_start = time.perf_counter()
    fresh = _capture_with_global_popup_guard(
        context,
        f"s11_report_entry_stale_xml_nudge_redump_{attempt_index}",
        current_stage="S11_REPORT_SEARCH",
    )
    fresh_ms = int((time.perf_counter() - fresh_start) * 1000)
    fresh_node, fresh_report_text = _find_s11_official_report_entry_node(fresh)
    fresh_visibility = _s11_report_entry_visibility(fresh, fresh_node)
    timing.add(
        step_name="S11_STALE_XML_REFRESH_NUDGE_FRESH",
        page_name="S11",
        action_name="fresh_after_s11_stale_xml_refresh_nudge",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=0,
        transition_wait_ms=fresh_ms,
        screenshot_path=str(fresh.get("screenshot_path") or ""),
        xml_path=str(fresh.get("xml_path") or ""),
        extra={
            "recognized_page": _recognize_mainline_page(recognizer, fresh),
            "exact_report_entry_seen": fresh_node is not None,
            "official_report_entry_text": fresh_report_text,
            "report_visibility": fresh_visibility,
            "s11_xml_stale_recovery": True,
            "reason_category": "FRESH_CAPTURE",
            "reason_detail": "fresh XML after one micro nudge must expose a bindable report-entry node before any click is allowed",
        },
    )
    return fresh, action_ms, wait_ms + fresh_ms, fresh_visibility


def _find_s11_report_click_target(snapshot: dict[str, Any], report_node: dict[str, Any], visibility: dict[str, Any]) -> dict[str, Any]:
    bounds = report_node.get("bounds")
    report_text = next(
        (text for text in S11_OFFICIAL_REPORT_ENTRY_TEXTS if _node_has_label(report_node, text)),
        S11_REPORT_ENTRY_TEXT,
    )
    if not bounds:
        return {
            "ok": False,
            **_s11_report_entry_contract_plan_evidence(),
            "stop_code": "S11_REPORT_ENTRY_CLICK_TARGET_NOT_FOUND",
            "reason": "report entry exact text node has no clickable bounds",
            "visibility": visibility,
        }
    safe_bottom = int(visibility.get("safe_bottom_y") or bounds[3])
    viewport = visibility.get("viewport_bounds") or [0, 0, 1220, 2712]
    screen_area = max((viewport[2] - viewport[0]) * (viewport[3] - viewport[1]), 1)
    bottom_blocked = bool(
        visibility.get("overlapped_bottom_bar")
        or visibility.get("below_safe_bottom")
        or visibility.get("too_close_to_bottom")
    )
    if bottom_blocked:
        return {
            "ok": False,
            **_s11_report_entry_contract_plan_evidence(),
            "stop_code": "S11_REPORT_ENTRY_NOT_FULLY_VISIBLE_SAFE",
            "reason": "exact report entry is present but blocked by the bottom action bar",
            "visibility": visibility,
        }
    if report_node.get("clickable") and report_node.get("enabled", True):
        return {
            "ok": True,
            **_s11_report_entry_contract_plan_evidence(),
            "click_strategy": "exact_text_clickable_node_bounds",
            "clicked_text": report_text,
            "clicked_node_bounds": bounds,
            "clicked_point": _center(bounds),
            "click_target_source": "xml_exact_text_bounds",
        }
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    containing_clickable: list[tuple[int, dict[str, Any]]] = []
    for node in nodes:
        node_bounds = node.get("bounds")
        if not node_bounds or not node.get("clickable") or not node.get("enabled", True):
            continue
        contains = (
            node_bounds[0] <= bounds[0]
            and node_bounds[1] <= bounds[1]
            and node_bounds[2] >= bounds[2]
            and node_bounds[3] >= bounds[3]
        )
        area = max((node_bounds[2] - node_bounds[0]) * (node_bounds[3] - node_bounds[1]), 1)
        if contains and node_bounds[3] <= safe_bottom and area <= int(screen_area * 0.35):
            containing_clickable.append((area, node))
    if containing_clickable:
        node = sorted(containing_clickable, key=lambda item: item[0])[0][1]
        node_bounds = node["bounds"]
        return {
            "ok": True,
            **_s11_report_entry_contract_plan_evidence(),
            "click_strategy": "nearest_clickable_parent_bounds",
            "clicked_text": report_text,
            "clicked_node_bounds": node_bounds,
            "clicked_point": _center(node_bounds),
            "click_target_source": "xml_clickable_parent_bounds",
        }
    return {
        "ok": True,
        **_s11_report_entry_contract_plan_evidence(),
        "click_strategy": "same_row_report_text_safe_region",
        "clicked_text": report_text,
        "clicked_node_bounds": bounds,
        "clicked_point": _center(bounds),
        "click_target_source": "xml_exact_text_bounds_without_clickable_parent",
    }


def _coerce_s11_dynamic_rect(value: Any) -> tuple[int, int, int, int] | None:
    if isinstance(value, str):
        return _parse_bounds(value)
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            bounds = tuple(int(item) for item in value)
        except (TypeError, ValueError):
            return None
        return bounds if _valid_bounds(bounds) else None
    return None


def _s11_dynamic_visual_text_regions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    # V1.47 keeps screenshot evidence as debug only.  Runtime must not run a
    # screenshot/layout detector to manufacture a clickable S11 report-entry
    # target when XML/accessibility does not expose current bounds.
    for source_key in (
        "screenshot_dynamic_text_regions",
        "screenshot_text_regions",
        "visual_text_regions",
        "ocr_text_regions",
        "dynamic_button_detections",
    ):
        raw_regions = snapshot.get(source_key) or []
        if not isinstance(raw_regions, list):
            continue
        for raw in raw_regions:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("detected_text") or raw.get("text") or raw.get("label") or "").strip()
            rect = _coerce_s11_dynamic_rect(
                raw.get("detected_button_rect")
                or raw.get("button_rect")
                or raw.get("rect")
                or raw.get("bounds")
            )
            if not text or rect is None:
                continue
            try:
                confidence = float(raw.get("confidence", 1.0))
            except (TypeError, ValueError):
                confidence = 0.0
            regions.append(
                {
                    "detected_text": text,
                    "detected_button_rect": rect,
                    "confidence": confidence,
                    "detection_source": str(
                        raw.get("detector_source")
                        or raw.get("detection_source")
                        or raw.get("source")
                        or source_key
                    ),
                    "detector_source": str(
                        raw.get("detector_source")
                        or raw.get("detection_source")
                        or raw.get("source")
                        or source_key
                    ),
                    "candidate_regions": raw.get("candidate_regions"),
                    "rejected_regions": raw.get("rejected_regions"),
                    "reject_reason": raw.get("reject_reason"),
                }
            )
    return regions


def _s11_attach_debug_screenshot_button_layout_regions(snapshot: dict[str, Any]) -> None:
    """Attach S11 screenshot button geometry as debug evidence only.

    V1.47 forbids using screenshot-derived regions as report-entry click
    targets. This helper must not be called by the S11 auto-click path.
    """

    if snapshot.get("_s11_debug_only_report_entry_layout_probe_ran"):
        return
    snapshot["_s11_debug_only_report_entry_layout_probe_ran"] = True
    detection = _s11_debug_detect_report_entry_button_layout_from_screenshot(snapshot)
    snapshot["s11_debug_only_report_entry_layout_probe"] = detection
    regions = detection.get("regions") if isinstance(detection, dict) else None
    if not regions:
        return
    existing = snapshot.get("screenshot_dynamic_text_regions")
    if not isinstance(existing, list):
        existing = []
        snapshot["screenshot_dynamic_text_regions"] = existing
    existing.extend(regions)


def _s11_debug_detect_report_entry_button_layout_from_screenshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return screenshot-layout diagnostics without authorizing a click."""

    screenshot_path = str(snapshot.get("screenshot_path") or snapshot.get("fresh_screenshot") or "")
    result: dict[str, Any] = {
        "detector_source": "debug_only_report_entry_layout_probe",
        "screenshot_path": screenshot_path,
        "ok": False,
        "regions": [],
        "candidate_regions": [],
        "rejected_regions": [],
        "reject_reason": "",
    }
    if not screenshot_path:
        result["reject_reason"] = "screenshot_path_missing"
        return result
    decoded = _s11_decode_png_rgb_rows(Path(screenshot_path))
    if not decoded:
        result["reject_reason"] = "png_decode_failed_or_unsupported"
        return result
    width, height, rows, channels, color_type = decoded
    result["viewport_size"] = [width, height]
    outline_rects = _s11_detect_outline_button_rects(width, height, rows, channels, color_type)
    result["candidate_regions"] = [
        {"rect": list(rect), "role": "outline_button_candidate"} for rect in outline_rects
    ]
    pair = _s11_select_report_entry_button_pair(outline_rects, width, height)
    if not pair:
        result["reject_reason"] = "no_safe_outline_button_pair_in_report_card_region"
        return result
    report_rect, advisor_rect = pair
    report_region = {
        "text": S11_REPORT_ENTRY_TEXT,
        "detected_text": S11_REPORT_ENTRY_TEXT,
        "detected_button_rect": list(report_rect),
        "rect": list(report_rect),
        "confidence": 0.86,
        "detector_source": "debug_only_report_entry_layout_probe",
        "source": "debug_only_report_entry_layout_probe",
        "candidate_regions": result["candidate_regions"],
        "rejected_regions": [
            {
                "detected_text": "找顾问解读报告",
                "detected_button_rect": list(advisor_rect),
                "reject_reason": "right_sibling_forbidden_advisor_report_button",
            }
        ],
        "layout_role": "left_outline_button_in_official_report_card",
        "paired_forbidden_sibling_rect": list(advisor_rect),
    }
    advisor_region = {
        "text": "找顾问解读报告",
        "detected_text": "找顾问解读报告",
        "detected_button_rect": list(advisor_rect),
        "rect": list(advisor_rect),
        "confidence": 0.79,
        "detector_source": "debug_only_report_entry_layout_probe",
        "source": "debug_only_report_entry_layout_probe",
        "reject_reason": "right_sibling_forbidden_advisor_report_button",
        "layout_role": "right_outline_button_forbidden_sibling",
    }
    result.update(
        {
            "ok": True,
            "detected_button_rect": list(report_rect),
            "detected_text": S11_REPORT_ENTRY_TEXT,
            "confidence": report_region["confidence"],
            "regions": [report_region, advisor_region],
            "rejected_regions": report_region["rejected_regions"],
        }
    )
    return result


def _s11_decode_png_rgb_rows(path: Path) -> tuple[int, int, list[bytearray], int, int] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    import struct
    import zlib

    pos = 8
    width = height = bit_depth = color_type = None
    idat = b""
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        pos += 4
        chunk_type = data[pos : pos + 4]
        pos += 4
        chunk = data[pos : pos + length]
        pos += length + 4
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
                ">IIBBBBB",
                chunk,
            )
            if bit_depth != 8 or compression != 0 or filter_method != 0 or interlace != 0:
                return None
        elif chunk_type == b"IDAT":
            idat += chunk
        elif chunk_type == b"IEND":
            break
    if not width or not height or color_type not in {0, 2, 6}:
        return None
    channels = {0: 1, 2: 3, 6: 4}[int(color_type)]
    try:
        raw = zlib.decompress(idat)
    except zlib.error:
        return None
    stride = int(width) * channels
    rows: list[bytearray] = []
    previous = bytearray(stride)
    offset = 0
    for _y in range(int(height)):
        if offset >= len(raw):
            return None
        filter_type = raw[offset]
        offset += 1
        scan = bytearray(raw[offset : offset + stride])
        offset += stride
        if len(scan) != stride:
            return None
        recon = bytearray(stride)
        for index, value in enumerate(scan):
            left = recon[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                out = value
            elif filter_type == 1:
                out = (value + left) & 0xFF
            elif filter_type == 2:
                out = (value + up) & 0xFF
            elif filter_type == 3:
                out = (value + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                predictor = left + up - upper_left
                pa = abs(predictor - left)
                pb = abs(predictor - up)
                pc = abs(predictor - upper_left)
                pr = left if pa <= pb and pa <= pc else (up if pb <= pc else upper_left)
                out = (value + pr) & 0xFF
            else:
                return None
            recon[index] = out
        rows.append(recon)
        previous = recon
    return int(width), int(height), rows, channels, int(color_type)


def _s11_png_pixel_rgb(rows: list[bytearray], channels: int, color_type: int, x: int, y: int) -> tuple[int, int, int]:
    row = rows[y]
    index = x * channels
    if color_type == 0:
        value = row[index]
        return value, value, value
    return row[index], row[index + 1], row[index + 2]


def _s11_is_gray_outline_pixel(pixel: tuple[int, int, int]) -> bool:
    maximum = max(pixel)
    minimum = min(pixel)
    return 70 <= maximum <= 215 and (maximum - minimum) <= 55


def _s11_detect_outline_button_rects(
    width: int,
    height: int,
    rows: list[bytearray],
    channels: int,
    color_type: int,
) -> list[tuple[int, int, int, int]]:
    horizontal_runs: list[tuple[int, int, int, int]] = []
    min_run_width = max(120, int(width * 0.22))
    for y in range(int(height * 0.50), int(height * 0.90)):
        x = 0
        while x < width:
            if _s11_is_gray_outline_pixel(_s11_png_pixel_rgb(rows, channels, color_type, x, y)):
                start = x
                while x < width and _s11_is_gray_outline_pixel(_s11_png_pixel_rgb(rows, channels, color_type, x, y)):
                    x += 1
                if x - start >= min_run_width:
                    horizontal_runs.append((y, start, x, x - start))
            x += 1
    rects: list[tuple[int, int, int, int]] = []
    for top in horizontal_runs:
        for bottom in horizontal_runs:
            if bottom[0] <= top[0]:
                continue
            rect_height = bottom[0] - top[0] + 1
            if not (45 <= rect_height <= 150):
                continue
            if abs(top[1] - bottom[1]) > 12 or abs(top[2] - bottom[2]) > 12:
                continue
            x1 = min(top[1], bottom[1])
            x2 = max(top[2], bottom[2])
            rect_width = x2 - x1
            if not (int(width * 0.25) <= rect_width <= int(width * 0.55)):
                continue
            rect = (x1, top[0], x2, bottom[0] + 1)
            if rect[3] >= int(height * 0.90):
                continue
            if not any(abs(rect[0] - old[0]) < 16 and abs(rect[1] - old[1]) < 16 for old in rects):
                rects.append(rect)
    rects.sort(key=lambda item: (item[1], item[0]))
    return rects


def _s11_select_report_entry_button_pair(
    rects: list[tuple[int, int, int, int]],
    width: int,
    height: int,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None:
    best: tuple[int, tuple[int, int, int, int], tuple[int, int, int, int]] | None = None
    for left in rects:
        for right in rects:
            if left[0] >= right[0] or left[2] > right[0]:
                continue
            gap = right[0] - left[2]
            left_width = left[2] - left[0]
            right_width = right[2] - right[0]
            left_height = left[3] - left[1]
            right_height = right[3] - right[1]
            if not (20 <= gap <= int(width * 0.14)):
                continue
            if abs(left[1] - right[1]) > 18 or abs(left[3] - right[3]) > 18:
                continue
            if min(left_width, right_width) / max(left_width, right_width) < 0.75:
                continue
            if min(left_height, right_height) / max(left_height, right_height) < 0.70:
                continue
            if left[0] > int(width * 0.45) or right[2] < int(width * 0.60):
                continue
            if left[1] < int(height * 0.55) or left[3] > int(height * 0.90):
                continue
            score = (right[2] - left[0]) + min(left_width, right_width) - abs(left[1] - right[1]) * 10
            if best is None or score > best[0]:
                best = (score, left, right)
    if best is None:
        return None
    return best[1], best[2]


def _s11_dynamic_region_forbidden_overlap(
    snapshot: dict[str, Any],
    rect: tuple[int, int, int, int],
) -> dict[str, Any]:
    forbidden_terms = tuple(dict.fromkeys([*S11_LOCAL_STRUCTURE_FORBIDDEN_TEXTS, "立即订购", "联系卖家"]))
    forbidden_nodes = _text_nodes_containing_any(snapshot, forbidden_terms)
    forbidden_dynamic_regions = [
        region
        for region in _s11_dynamic_visual_text_regions(snapshot)
        if region.get("detected_text") != S11_REPORT_ENTRY_TEXT
        and any(term and term in str(region.get("detected_text") or "") for term in forbidden_terms)
    ]
    overlap_hits: list[dict[str, Any]] = []
    for node in forbidden_nodes:
        bounds = node.get("bounds")
        if _bounds_intersect(rect, bounds):
            overlap_hits.append(
                {
                    "source": "xml_forbidden_node",
                    "text": _node_label(node),
                    "bounds": list(bounds),
                }
            )
    for region in forbidden_dynamic_regions:
        region_rect = region.get("detected_button_rect")
        if _bounds_intersect(rect, region_rect):
            overlap_hits.append(
                {
                    "source": region.get("detection_source") or "dynamic_forbidden_region",
                    "text": region.get("detected_text"),
                    "bounds": list(region_rect),
                }
            )
    report_area = max(_bounds_area(rect), 1)
    significant_hits = [
        hit
        for hit in overlap_hits
        if _bounds_intersection_area(rect, hit.get("bounds")) >= max(1, int(report_area * 0.10))
    ]
    return {
        "forbidden_button_overlap": bool(significant_hits),
        "forbidden_overlap_hits": significant_hits,
    }


def _s11_report_entry_dynamic_visual_button_click_target(
    snapshot: dict[str, Any],
    *,
    fresh_pair: dict[str, Any] | None = None,
    recovery: bool = False,
) -> dict[str, Any]:
    regions = _s11_dynamic_visual_text_regions(snapshot)
    exact_regions = [region for region in regions if region.get("detected_text") == S11_REPORT_ENTRY_TEXT]
    if not exact_regions:
        plan_evidence = _s11_report_entry_contract_plan_evidence()
        return {
            "ok": False,
            **plan_evidence,
            "stop_code": "S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET",
            "reason": "no dynamic screenshot region matched exact view-full-report text",
            "click_target_source": "no_bindable_target",
            "s11_report_entry_click_mode": "no_fixed_coordinate_stop",
            "s11_report_entry_click_source": "",
            "allowed_binding_sources": plan_evidence.get("contract_action_algorithm", {}).get("allowed_binding_sources"),
            "binding_source": "no_bindable_target",
            "xml_exact_attempted": True,
            "xml_exact_success": False,
            "xml_stale": bool((fresh_pair or {}).get("s11_xml_stale")),
            "screenshot_seen_for_debug": bool(snapshot.get("screenshot_path")),
            "screenshot_used_for_click": False,
            "s11_visual_debug_not_used_for_click": True,
            "screenshot_detector_attempted": False,
            "screenshot_detector_used": False,
            "screenshot_detector_count": 0,
            "xml_dump_count": 1,
            "screenshot_count": 1,
            "fallback_used": False,
            "fallback_name": "",
            "fallback_allowed_by_clause": True,
            "detector_source": "",
            "s11_xml_stale_warning": bool((fresh_pair or {}).get("s11_xml_stale")) or recovery,
            "xml_text_missing": True,
            "view_full_report_seen_in_xml": False,
            "view_full_report_exact_text_seen": False,
            "report_entry_detection_strategy": "NO_BINDABLE_TARGET",
            "dynamic_visual_binding_attempted": False,
            "dynamic_visual_binding_reason": "no_exact_dynamic_report_entry_region",
            "screenshot_dynamic_text_regions": snapshot.get("screenshot_dynamic_text_regions") or [],
            "candidate_regions": (snapshot.get("s11_debug_only_report_entry_layout_probe") or {}).get("candidate_regions"),
            "rejected_dynamic_report_entry_regions": (snapshot.get("s11_debug_only_report_entry_layout_probe") or {}).get("rejected_regions"),
            "reject_reason": (snapshot.get("s11_debug_only_report_entry_layout_probe") or {}).get("reject_reason"),
            "click_attempted": False,
            "click_source": "",
        }
    exact_regions.sort(key=lambda item: (-float(item.get("confidence") or 0.0), item["detected_button_rect"][1]))
    plan_evidence = _s11_report_entry_contract_plan_evidence()
    return {
        "ok": False,
        **plan_evidence,
        "stop_code": "S11_REPORT_ENTRY_XML_MISSING_BUT_SCREENSHOT_VISIBLE_NOT_CLICKED",
        "reason": "view-full-report is visible in screenshot debug evidence, but V1.47 allows clicking only current XML/accessibility bounds",
        "click_target_source": "no_xml_bindable_target",
        "s11_report_entry_click_mode": "xml_only_stop",
        "s11_report_entry_click_source": "",
        "allowed_binding_sources": plan_evidence.get("contract_action_algorithm", {}).get("allowed_binding_sources"),
        "binding_source": "no_bindable_target",
        "xml_exact_attempted": True,
        "xml_exact_success": False,
        "xml_stale": bool((fresh_pair or {}).get("s11_xml_stale")),
        "screenshot_seen_for_debug": True,
        "screenshot_used_for_click": False,
        "s11_visual_debug_not_used_for_click": True,
        "screenshot_seen_for_debug": bool(snapshot.get("screenshot_path")),
        "screenshot_used_for_click": False,
        "s11_visual_debug_not_used_for_click": True,
        "screenshot_detector_attempted": False,
        "screenshot_detector_used": False,
        "screenshot_detector_count": 0,
        "s11_xml_dump_count": 1,
        "s11_screenshot_count": 1,
        "xml_dump_count": 1,
        "screenshot_count": 1,
        "fallback_used": False,
        "fallback_name": "",
        "fallback_allowed_by_clause": True,
        "click_source": "",
        "click_attempted": False,
        "report_entry_detection_strategy": "SCREENSHOT_VISIBLE_DEBUG_XML_ONLY_STOP",
        "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_NO_FIXED_COORDINATE,
        "s11_xml_stale_warning": bool((fresh_pair or {}).get("s11_xml_stale")) or recovery,
        "view_full_report_seen_in_xml": False,
        "view_full_report_exact_text_seen": False,
        "xml_text_missing": True,
        "dynamic_visual_binding_attempted": False,
        "screenshot_visible_xml_missing_debug": True,
        "detection_source": "screenshot_debug_only",
        "detector_source": "screenshot_debug_only",
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "detected_text": S11_REPORT_ENTRY_TEXT,
        "detected_button_rect": list(exact_regions[0].get("detected_button_rect") or []),
        "confidence": exact_regions[0].get("confidence"),
        "candidate_regions": [],
        "rejected_dynamic_report_entry_regions": [],
    }
    plan_evidence = _s11_report_entry_contract_plan_evidence()
    return {
        "ok": False,
        **plan_evidence,
        "stop_code": "S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET",
        "reason": "dynamic view-full-report regions overlapped forbidden advisor/contact/bottom actions",
        "click_target_source": "no_bindable_target",
        "s11_report_entry_click_mode": "no_fixed_coordinate_stop",
        "s11_report_entry_click_source": "",
        "allowed_binding_sources": plan_evidence.get("contract_action_algorithm", {}).get("allowed_binding_sources"),
        "binding_source": "no_bindable_target",
        "xml_exact_attempted": True,
        "xml_exact_success": False,
        "xml_stale": bool((fresh_pair or {}).get("s11_xml_stale")),
        "screenshot_detector_attempted": False,
        "screenshot_detector_used": False,
        "screenshot_detector_count": 0,
        "xml_dump_count": 1,
        "screenshot_count": 1,
        "fallback_used": False,
        "fallback_name": "",
        "fallback_allowed_by_clause": True,
        "detector_source": "",
        "s11_xml_stale_warning": bool((fresh_pair or {}).get("s11_xml_stale")) or recovery,
        "xml_text_missing": True,
        "view_full_report_seen_in_xml": False,
        "view_full_report_exact_text_seen": False,
        "report_entry_detection_strategy": "NO_BINDABLE_TARGET",
        "dynamic_visual_binding_attempted": False,
        "dynamic_visual_binding_reason": "forbidden_overlap",
        "screenshot_dynamic_text_regions": snapshot.get("screenshot_dynamic_text_regions") or [],
        "candidate_regions": (snapshot.get("s11_debug_only_report_entry_layout_probe") or {}).get("candidate_regions"),
        "rejected_dynamic_report_entry_regions": rejected,
        "reject_reason": "forbidden_overlap",
        "click_attempted": False,
        "click_source": "",
    }


def _s11_report_entry_xml_bounds_click_target(
    snapshot: dict[str, Any],
    *,
    visibility: dict[str, Any] | None = None,
    click_source: str = "xml_exact_text_bounds",
    fresh_pair: dict[str, Any] | None = None,
    recovery: bool = False,
) -> dict[str, Any]:
    report_node, _report_text = _find_s11_official_report_entry_node(snapshot)
    if report_node is None:
        dynamic_target = _s11_report_entry_dynamic_visual_button_click_target(
            snapshot,
            fresh_pair=fresh_pair,
            recovery=recovery,
        )
        if dynamic_target.get("ok"):
            return dynamic_target
        return {
            **dynamic_target,
            "ok": False,
            "stop_code": dynamic_target.get("stop_code") or "S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET",
            "reason": dynamic_target.get("reason") or "view full report is visible in S11 context but no current XML/accessibility bounds can be bound",
            "click_target_source": dynamic_target.get("click_target_source") or "no_bindable_target",
            "s11_report_entry_click_mode": dynamic_target.get("s11_report_entry_click_mode") or "no_fixed_coordinate_stop",
            "s11_report_entry_click_source": dynamic_target.get("s11_report_entry_click_source") or "",
            "s11_xml_stale_warning": bool((fresh_pair or {}).get("s11_xml_stale")),
            "xml_text_missing": True,
            "view_full_report_seen_in_xml": False,
            "view_full_report_exact_text_seen": False,
            "report_entry_detection_strategy": dynamic_target.get("report_entry_detection_strategy") or "NO_BINDABLE_TARGET",
            "dynamic_visual_binding_attempted": dynamic_target.get("dynamic_visual_binding_attempted"),
            "dynamic_visual_binding_reason": dynamic_target.get("dynamic_visual_binding_reason") or dynamic_target.get("reason"),
            "rejected_dynamic_report_entry_regions": dynamic_target.get("rejected_dynamic_report_entry_regions"),
            "click_attempted": False,
        }
    target = _find_s11_report_click_target(snapshot, report_node, visibility or _s11_report_entry_visibility(snapshot, report_node))
    if not target.get("ok"):
        return target
    plan_evidence = _s11_report_entry_contract_plan_evidence()
    target = {
        **target,
        **plan_evidence,
        "click_target_source": click_source,
        "s11_report_entry_click_source": click_source,
        "s11_report_entry_click_mode": "xml_after_stale_recovery" if recovery else "xml_node_click",
        "allowed_binding_sources": plan_evidence.get("contract_action_algorithm", {}).get("allowed_binding_sources"),
        "binding_source": click_source,
        "xml_exact_attempted": True,
        "xml_exact_success": True,
        "xml_stale": bool((fresh_pair or {}).get("s11_xml_stale")),
        "screenshot_detector_attempted": False,
        "screenshot_detector_used": False,
        "screenshot_detector_count": 0,
        "xml_dump_count": 1,
        "screenshot_count": 0,
        "fallback_used": False,
        "fallback_name": "",
        "fallback_allowed_by_clause": True,
        "click_source": click_source,
        "click_point_source": "xml_bounds_center",
        "click_attempted": True,
        "report_entry_detection_strategy": "XML_TEXT_AFTER_STALE_RECOVERY" if recovery else "XML_TEXT_BINDING",
        "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_NO_FIXED_COORDINATE,
        "s11_xml_stale_warning": bool((fresh_pair or {}).get("s11_xml_stale")) or recovery,
        "view_full_report_seen_in_xml": True,
        "view_full_report_exact_text_seen": True,
        "xml_text_missing": False,
    }
    return target


def _s11_report_entry_stale_direct_click_allowed(fresh_pair: dict[str, Any], *, in_s11_handler: bool = True) -> bool:
    return bool(
        in_s11_handler
        and fresh_pair.get("s11_xml_stale")
        and fresh_pair.get("xml_dump_ok", True)
        and fresh_pair.get("xml_mtime_valid", True)
    )


def _s11_report_entry_failure_code_after_click(using_recovered_report_entry_binding: bool, fallback_code: str) -> str:
    if using_recovered_report_entry_binding:
        return "S11_REPORT_ENTRY_DIRECT_CLICK_DID_NOT_ENTER_REPORT"
    return fallback_code


def _current_target_fingerprint_from_context(context: dict[str, Any]) -> str:
    task = _segment2_task_payload(context.get("target_task_result") or {})
    target_car = context.get("target_car")
    if target_car is not None:
        task = {
            **task,
            "brand": task.get("brand") or getattr(target_car, "brand", ""),
            "series": task.get("series") or getattr(target_car, "series", ""),
            "year_model": task.get("year_model") or getattr(target_car, "model_year", ""),
            "config_model": task.get("config_model") or getattr(target_car, "trim", ""),
            "color": task.get("color") or getattr(target_car, "color", ""),
            "register_date": task.get("register_date") or getattr(target_car, "registration_date", ""),
        }
    target_task = (context.get("target_task_result") or {}).get("task")
    if isinstance(target_task, dict):
        task = {
            **task,
            "brand": task.get("brand") or target_task.get("brand"),
            "series": task.get("series") or target_task.get("series"),
            "year_model": task.get("year_model") or target_task.get("year_model"),
            "config_model": task.get("config_model") or target_task.get("config_model"),
            "color": task.get("color") or target_task.get("color"),
            "register_date": task.get("register_date") or target_task.get("register_date"),
        }
    return _target_fingerprint(task)


def _evidence_pairing_check(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    page_id: str,
    used_for_decision: str,
) -> dict[str, Any]:
    fingerprint = _current_target_fingerprint_from_context(context)
    text = "\n".join(str(item) for item in snapshot.get("visible_texts") or [])
    text += "\n" + str(snapshot.get("visible_blob") or "")
    foreign_terms: list[str] = []
    if "吉利|远景" in fingerprint:
        for term in ("零跑", "C10", "悦享版"):
            if term in text:
                foreign_terms.append(term)
    status = "warning_foreign_target_terms_detected" if foreign_terms else "ok"
    evidence = {
        "evidence_pairing_check_enabled": True,
        "evidence_pairing_status": status,
        "evidence_current_fingerprint": fingerprint,
        "evidence_detected_foreign_target_terms": sorted(set(foreign_terms)),
        "evidence_used_for_decision": used_for_decision,
        "evidence_page_id": page_id,
        "xml_path": str(snapshot.get("xml_path") or ""),
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "evidence_contamination_warning": bool(foreign_terms),
    }
    context.setdefault("evidence_pairing_checks", []).append(evidence)
    context["last_evidence_pairing_check"] = evidence
    return evidence


def _s11_report_entry_pairing_check(context: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    base = _evidence_pairing_check(context, snapshot, page_id="S11", used_for_decision="s11_report_entry_local_structure")
    xml_path_raw = str(snapshot.get("xml_path") or "")
    screenshot_path_raw = str(snapshot.get("screenshot_path") or "")
    xml_path = Path(xml_path_raw) if xml_path_raw else None
    screenshot_path = Path(screenshot_path_raw) if screenshot_path_raw else None
    xml_exists = bool(xml_path and xml_path.exists())
    screenshot_exists = bool(screenshot_path and screenshot_path.exists())
    same_stem = bool(xml_exists and screenshot_exists and xml_path.stem == screenshot_path.stem)
    mtime_delta_seconds: float | None = None
    if xml_exists and screenshot_exists:
        mtime_delta_seconds = abs(float(xml_path.stat().st_mtime) - float(screenshot_path.stat().st_mtime))
    same_fresh = bool(xml_exists and screenshot_exists and mtime_delta_seconds is not None and mtime_delta_seconds <= 30.0)
    text_blob = str(snapshot.get("visible_blob") or "")
    visible_texts = [str(item).strip() for item in snapshot.get("visible_texts") or []]
    xml_s10_signals = [term for term in S11_REPORT_ENTRY_XML_S10_STRONG_SIGNALS if term in text_blob or term in visible_texts]
    xml_s12_signals = [term for term in S11_REPORT_ENTRY_XML_S12_STRONG_SIGNALS if term in text_blob or term in visible_texts]
    s11_structural_signals = [term for term in ("杞﹀喌", "鏈鸿埍宸ュ喌") if term in text_blob or term in visible_texts]
    mismatch_reasons: list[str] = []
    if not xml_exists:
        mismatch_reasons.append("xml_path_missing")
    if not screenshot_exists:
        mismatch_reasons.append("screenshot_path_missing")
    # S10_TO_S11 pre-dump intentionally reuses a visually stabilized screenshot with a compressed XML dump
    # whose file stems differ. Treat timestamp and page-signal consistency as the same-fresh contract.
    if xml_exists and screenshot_exists and mtime_delta_seconds is not None and mtime_delta_seconds > 30.0:
        mismatch_reasons.append("xml_screenshot_timestamp_delta_exceeds_window")
    if xml_s10_signals:
        mismatch_reasons.append("xml_contains_s10_list_signals")
    if xml_s12_signals and "鏈鸿埍宸ュ喌" not in s11_structural_signals:
        mismatch_reasons.append("xml_contains_s12_report_signals_without_s11_engine_block")
    if not s11_structural_signals:
        mismatch_reasons.append("xml_lacks_s11_structural_signals")
    evidence_pairing_ok = bool(same_fresh and not mismatch_reasons)
    return {
        **base,
        "evidence_pairing_ok": evidence_pairing_ok,
        "xml_screenshot_same_fresh": same_fresh,
        "xml_screenshot_pair_mismatch_reason": ",".join(mismatch_reasons),
        "xml_screenshot_stem_match": same_stem,
        "xml_screenshot_mtime_delta_seconds": mtime_delta_seconds,
        "xml_s10_strong_signals": xml_s10_signals,
        "xml_s12_strong_signals": xml_s12_signals,
        "s11_structural_signals": s11_structural_signals,
        "xml_path": xml_path_raw,
        "screenshot_path": screenshot_path_raw,
    }


def _s11_local_structure_binding_candidate(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    pairing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # V1.26 half-screen S11 report-entry contract disables local structure binding.
    # Return a non-executable audit object so older call sites cannot bind or click
    # an XML-empty left-side candidate.
    return {
        "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_V1_27,
        "view_full_report_seen_in_xml": _find_s11_official_report_entry_node(snapshot)[0] is not None,
        "xml_text_missing": _find_s11_official_report_entry_node(snapshot)[0] is None,
        "local_structure_binding_attempted": False,
        "local_structure_binding_enabled": False,
        "local_structure_binding_safe": False,
        "local_structure_binding_reason": "disabled_by_exact_view_full_report_half_screen_scroll_contract",
        "local_structure_binding_disabled": True,
        "engine_condition_block_seen": False,
        "left_candidate_bounds": None,
        "right_advisor_candidate_bounds": None,
        "candidate_overlap": False,
        "bottom_bar_overlap": False,
        "forbidden_button_overlap": False,
        "report_entry_detection_strategy": "EXACT_XML_TEXT_HALF_SCREEN_SCROLL_DISABLED_LOCAL_STRUCTURE",
        "visual_binding_disabled": True,
        "ocr_disabled": True,
        "screenshot_text_recognition_disabled": True,
        "evidence_pairing_ok": None,
        "xml_screenshot_same_fresh": None,
        "xml_screenshot_pair_mismatch_reason": "",
    }
    pairing = pairing or _s11_report_entry_pairing_check(context, snapshot)
    base: dict[str, Any] = {
        "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_V1_27,
        "view_full_report_seen_in_xml": _find_s11_official_report_entry_node(snapshot)[0] is not None,
        "local_structure_binding_attempted": False,
        "local_structure_binding_enabled": False,
        "local_structure_binding_safe": False,
        "local_structure_binding_reason": "",
        "engine_condition_block_seen": False,
        "left_candidate_bounds": None,
        "right_advisor_candidate_bounds": None,
        "candidate_overlap": False,
        "bottom_bar_overlap": False,
        "forbidden_button_overlap": False,
        "report_entry_detection_strategy": "EVIDENCE_PAIRING_MISMATCH" if pairing.get("evidence_pairing_ok") is False else "LOCAL_STRUCTURE_BINDING_XML_TEXT_MISSING",
        "visual_binding_disabled": True,
        "ocr_disabled": True,
        "screenshot_text_recognition_disabled": True,
        **pairing,
    }
    if pairing.get("evidence_pairing_ok") is not True:
        base["local_structure_binding_reason"] = "evidence_pairing_not_passed"
        return base
    if base["view_full_report_seen_in_xml"]:
        base["local_structure_binding_reason"] = "xml_text_binding_has_priority"
        return base

    engine_nodes = _text_nodes_containing_any(snapshot, ("鏈鸿埍宸ュ喌",))
    if not engine_nodes:
        base["local_structure_binding_reason"] = "engine_condition_block_not_seen"
        return base
    engine_node = sorted(engine_nodes, key=lambda node: node["bounds"][1])[0]
    engine_bounds = engine_node["bounds"]
    base["engine_condition_block_seen"] = True
    base["engine_condition_block_bounds"] = list(engine_bounds)

    advisor_nodes = _text_nodes_containing_any(snapshot, S11_LOCAL_STRUCTURE_RIGHT_ADVISOR_TEXTS)
    # The advisor reference must be below the engine block and on the right half, otherwise floating video/ad cards are ignored.
    extent = _visible_bounds_extent(snapshot)
    if extent is None:
        base["local_structure_binding_reason"] = "no_visible_xml_bounds"
        return base
    viewport, _source = extent
    vx1, vy1, vx2, vy2 = viewport
    width = max(vx2 - vx1, 1)
    height = max(vy2 - vy1, 1)
    right_half_x = vx1 + int(width * 0.48)
    advisor_candidates = [
        node
        for node in advisor_nodes
        if node["bounds"][0] >= right_half_x
        and node["bounds"][1] >= engine_bounds[1]
        and node["bounds"][1] <= min(vy2 - int(height * 0.08), engine_bounds[3] + int(height * 0.25))
    ]
    if not advisor_candidates:
        base["local_structure_binding_reason"] = "right_advisor_reference_not_seen_under_engine_block"
        return base
    right_node = sorted(advisor_candidates, key=lambda node: (node["bounds"][1], node["bounds"][0]))[0]
    right_bounds = right_node["bounds"]
    base["right_advisor_candidate_bounds"] = list(right_bounds)
    base["right_advisor_candidate_labels"] = right_node.get("labels", [])

    button_height = max(right_bounds[3] - right_bounds[1], int(height * 0.045), 80)
    row_y1 = max(vy1 + int(height * 0.08), right_bounds[1] - max(20, int(button_height * 0.18)))
    row_y2 = min(vy2 - int(height * 0.10), max(right_bounds[3] + max(20, int(button_height * 0.18)), row_y1 + button_height))
    gap = max(24, int(width * 0.02))
    left_x1 = vx1 + int(width * 0.055)
    left_x2 = min(right_bounds[0] - gap, vx1 + int(width * 0.49))
    left_bounds = (left_x1, row_y1, left_x2, row_y2)
    base["left_candidate_bounds"] = list(left_bounds)
    if left_bounds[2] <= left_bounds[0] or left_bounds[3] <= left_bounds[1] or _bounds_area(left_bounds) < int(width * height * 0.004):
        base["local_structure_binding_reason"] = "left_candidate_geometry_invalid"
        return base

    bottom_bar = _detect_s11_bottom_bar(snapshot)
    if bottom_bar.get("bottom_bar_detected") and bottom_bar.get("bottom_bar_bounds"):
        bottom_bar_overlap = _bounds_intersect(left_bounds, bottom_bar.get("bottom_bar_bounds"))
        base["bottom_bar_overlap"] = bool(bottom_bar_overlap)
        base["bottom_bar_bounds"] = bottom_bar.get("bottom_bar_bounds")
        if bottom_bar_overlap:
            base["local_structure_binding_reason"] = "left_candidate_overlaps_bottom_bar"
            return base

    candidate_overlap = _bounds_intersect(left_bounds, right_bounds)
    base["candidate_overlap"] = bool(candidate_overlap)
    if candidate_overlap:
        base["local_structure_binding_reason"] = "left_candidate_overlaps_right_advisor"
        return base

    forbidden_hits: list[dict[str, Any]] = []
    for node in _text_nodes_containing_any(snapshot, S11_LOCAL_STRUCTURE_FORBIDDEN_TEXTS):
        bounds = node.get("bounds")
        if _bounds_intersection_area(left_bounds, bounds) > max(1, int(_bounds_area(left_bounds) * 0.05)):
            forbidden_hits.append({"labels": node.get("labels", []), "bounds": list(bounds)})
    base["forbidden_button_overlap"] = bool(forbidden_hits)
    base["forbidden_overlap_hits"] = forbidden_hits
    if forbidden_hits:
        base["local_structure_binding_reason"] = "left_candidate_overlaps_forbidden_button"
        return base

    safe_top = vy1 + int(height * 0.05)
    safe_bottom = vy2 - int(height * 0.12)
    if left_bounds[1] < safe_top or left_bounds[3] > safe_bottom:
        base["local_structure_binding_reason"] = "left_candidate_outside_safe_vertical_region"
        return base

    base.update(
        {
            "local_structure_binding_enabled": True,
            "local_structure_binding_safe": False,
            "local_structure_binding_reason": "LEFT_BUTTON_UNDER_ENGINE_CONDITION_WITH_RIGHT_ADVISOR_BUTTON",
            "binding_reason": "LEFT_BUTTON_UNDER_ENGINE_CONDITION_WITH_RIGHT_ADVISOR_BUTTON",
            "visual_candidate_label": S11_REPORT_ENTRY_TEXT,
            "clicked_text": S11_REPORT_ENTRY_TEXT,
            "clicked_node_bounds": list(left_bounds),
            "clicked_point": list(_center(left_bounds)),
            "click_strategy": "s11_local_structure_left_button_under_engine_condition",
            "click_target_source": "local_structure_left_button_region",
            "ok": True,
        }
    )
    return base


def _stop_s11_report_entry_pair_mismatch(context: dict[str, Any], snapshot: dict[str, Any], evidence: dict[str, Any]) -> None:
    contract_stop(
        context,
        "S11",
        "S11_REPORT_ENTRY_XML_SCREENSHOT_PAIR_MISMATCH",
        "S11 report-entry binding stopped because screenshot/XML evidence is not a same-fresh S11 pair.",
        {**snapshot, **evidence},
    )


def _return_to_reliable_s10_after_reference_exclusion(
    context: dict[str, Any],
    *,
    excluded_index: int,
    next_reference_index: int,
) -> dict[str, Any]:
    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    timing: TimingRecorder = context["timing"]
    attempts: list[dict[str, Any]] = []
    last_snapshot: dict[str, Any] | None = None
    last_state: str | None = None
    for attempt_index in range(1, 4):
        action_started = time.perf_counter()
        client.back()
        action_ms = int((time.perf_counter() - action_started) * 1000)
        time.sleep(0.45)
        snapshot = _capture_with_global_popup_guard(
            context,
            f"s11_report_missing_return_s10_{attempt_index}",
            current_stage="S11_RETURN_TO_S10",
            call_site="s11_report_missing_return_s10",
        )
        snapshot["target_brand"] = context["target_car"].brand
        snapshot["target_car"] = {
            "brand": context["target_car"].brand,
            "series": context["target_car"].series,
            "model_year": context["target_car"].model_year,
            "trim": context["target_car"].trim,
        }
        state = _recognize_mainline_page(recognizer, snapshot)
        reliable_evidence: dict[str, Any] = {}
        if state == "S10":
            expected_card = _expected_reference_card_with_continuation_context(
                context.get("first_stage_evidence") or {},
                next_reference_index,
                context.get("continuation_plan"),
            )
            reliable_evidence = _s10_reliable_list_evidence(
                snapshot,
                target_reference_index=next_reference_index,
                expected_card=expected_card,
            )
        attempt = {
            "attempt_index": attempt_index,
            "recognized_page": state,
            "xml_path": str(snapshot.get("xml_path") or ""),
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "action_ms": action_ms,
            "excluded_reference_index": excluded_index,
            "next_reference_index": next_reference_index,
            "s10_reliable_list_evidence": reliable_evidence,
        }
        attempts.append(attempt)
        timing.add(
            step_name="S11_REPORT_MISSING_RETURN_TO_S10_ATTEMPT",
            page_name="S11",
            action_name="return_to_reliable_s10_after_report_missing_exclusion",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=action_ms,
            transition_wait_ms=450,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra=attempt,
        )
        last_snapshot = snapshot
        last_state = state
        if state == "S10" and reliable_evidence.get("reliable") is True:
            context["returned_s10_snapshot"] = snapshot
            context["returned_s10_snapshot_source"] = "S11_REPORT_MISSING_EXCLUSION_RETURN"
            context["returned_s10_reliable_evidence"] = reliable_evidence
            context["returned_list_source"] = "from_s11_official_report_missing_exclusion"
            context["returned_list_source_verified"] = True
            context["return_to_reliable_s10_result"] = "returned"
            context["s11_report_missing_return_attempts"] = attempts
            return snapshot
    issue = issues.record(
        "S11_RETURN_TO_RELIABLE_S10_AFTER_EXCLUSION_FAILED",
        "S11",
        "Official report entry was missing and the reference was excluded, but runtime could not return to reliable S10.",
        {
            **(last_snapshot or {}),
            "recognized_page": last_state,
            "excluded_reference_index": excluded_index,
            "next_reference_index": next_reference_index,
            "s11_report_missing_return_attempts": attempts,
        },
        "manual_review",
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def _exclude_current_reference_for_missing_official_report(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    contract_validate_action(
        context,
        snapshot,
        "S11",
        "S11_OFFICIAL_REPORT_ENTRY_MISSING_EXCLUDE_REFERENCE",
        evidence=evidence,
    )
    current_reference = dict(context.get("current_reference") or {})
    excluded_index = int(current_reference.get("reference_index") or context.get("current_reference_index") or 0)
    current_reference.update(
        {
            "current_reference_excluded": True,
            "reference_status": "EXCLUDED_OFFICIAL_REPORT_NOT_AVAILABLE",
            "reference_exclusion_reason": "OFFICIAL_REPORT_NOT_AVAILABLE",
            "excluded_reference_index": excluded_index,
            "excluded_reference_title": current_reference.get("selected_card_title") or current_reference.get("list_title"),
            "excluded_reference_price": current_reference.get("selected_card_price") or current_reference.get("list_price_text"),
            **evidence,
        }
    )
    context["current_reference"] = current_reference
    context.setdefault("excluded_reference_history", []).append(current_reference)
    context.setdefault("reference_history", []).append(current_reference)
    trisame_count = _first_stage_trisame_count(context.get("first_stage_evidence") or {})
    next_reference_index = excluded_index + 1
    context["previous_reference_index"] = excluded_index
    context["current_reference_index"] = next_reference_index
    context["next_reference_index"] = next_reference_index
    if trisame_count is not None and next_reference_index > trisame_count:
        context["all_trisame_sources_exhausted"] = True
        issue = context["issues"].record(
            "ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW",
            "S11",
            "Current reference lacks official full report and no confirmed same-source references remain.",
            {
                "current_reference": current_reference,
                "excluded_reference_history": context.get("excluded_reference_history"),
                "reference_history": context.get("reference_history"),
                "trisame_count": trisame_count,
                "next_reference_index": next_reference_index,
                "all_trisame_sources_exhausted": True,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    returned = _return_to_reliable_s10_after_reference_exclusion(
        context,
        excluded_index=excluded_index,
        next_reference_index=next_reference_index,
    )
    context["current_reference"] = {}
    context["exclude_current_reference_from_history"] = False
    return "S10", returned


def _wait_s11_to_s12_stable_after_report_click(
    context: dict[str, Any],
    before_snapshot: dict[str, Any],
    *,
    before_xml_digest: str,
    before_visible_digest: str,
    before_screenshot_digest: str,
) -> dict[str, Any]:
    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    timing: TimingRecorder = context["timing"]
    started = time.perf_counter()
    max_wait_s = 10.0
    interval_s = 1.0
    rounds: list[dict[str, Any]] = []
    previous_xml_digest = ""
    previous_visible_digest = ""
    previous_signal_key = ""
    stable_rounds = 0
    last_snapshot: dict[str, Any] = {}
    last_recognized: str | None = None
    last_loading: dict[str, Any] = {}
    last_s12_evidence: dict[str, Any] = {}
    last_s14_signals: list[str] = []
    round_index = 0
    while True:
        round_index += 1
        time.sleep(interval_s)
        fresh_started = time.perf_counter()
        snapshot = _capture_with_global_popup_guard(
            context,
            f"s11_to_s12_wait_stable_{round_index}",
            current_stage="S11_TO_S12",
            call_site="s11_to_s12_wait_stable",
        )
        fresh_ms = int((time.perf_counter() - fresh_started) * 1000)
        xml_digest = _sha256_text(str(snapshot.get("fresh_xml") or ""))
        visible_digest = _sha256_text("|".join(str(item) for item in snapshot.get("visible_texts", [])))
        screenshot_digest = _sha256_file(snapshot.get("screenshot_path"))
        s12_evidence = _s12_report_page_evidence(snapshot)
        s14_signals = _s14_candidate_signals(snapshot)
        loading_evidence = _s11_to_s12_loading_overlay_evidence(snapshot)
        signal_key = "|".join(str(item) for item in s12_evidence.get("s12_candidate_signals") or [])
        semantic_stable = bool(previous_signal_key and signal_key and signal_key == previous_signal_key)
        digest_stable = bool(
            previous_xml_digest
            and previous_visible_digest
            and (xml_digest == previous_xml_digest or visible_digest == previous_visible_digest)
        )
        is_stable = bool(semantic_stable or digest_stable)
        stable_rounds = stable_rounds + 1 if is_stable else 1
        recognized = _recognize_mainline_page(
            recognizer,
            snapshot,
            transition_context="S11_TO_S12",
            expected_next_page="S12",
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        round_info = {
            "round_index": round_index,
            "fresh_ms": fresh_ms,
            "xml_digest": xml_digest,
            "visible_text_digest": visible_digest,
            "screenshot_digest": screenshot_digest,
            "xml_digest_stable": xml_digest == previous_xml_digest if previous_xml_digest else False,
            "visible_text_digest_stable": visible_digest == previous_visible_digest if previous_visible_digest else False,
            "semantic_signal_stable": semantic_stable,
            "stable_rounds": stable_rounds,
            "loading_overlay_detected": loading_evidence.get("loading_overlay_detected"),
            "loading_overlay_evidence": loading_evidence,
            "s12_candidate_signals": s12_evidence.get("s12_candidate_signals"),
            "s12_recognition_groups": s12_evidence.get("s12_recognition_groups"),
            "s14_candidate_signals": s14_signals,
            "s14_suppressed_by_context": bool(s14_signals),
            "s14_suppression_reason": "S11_TO_S12_EXPECTS_S12" if s14_signals else "",
            "recognized_page_candidate": recognized,
            "recognized_by": snapshot.get("recognized_by"),
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
            "elapsed_ms": elapsed_ms,
        }
        rounds.append(round_info)
        timing.add(
            step_name="S11_TO_S12_WAIT_STABLE_AFTER_REPORT_CLICK",
            page_name="S11",
            action_name="fresh_wait_report_page_stability",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=fresh_ms + int(interval_s * 1000),
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra={
                **round_info,
                "reason_category": "REPORT_PAGE_STABLE_WAIT",
                "reason_detail": "after safe 鏌ョ湅瀹屾暣鎶ュ憡 click, wait for report page XML/visual stability before final S12 recognition",
                "solution": "recognize S12 before S14 in S11_TO_S12 context and never collect fields until S12 is confirmed",
            },
        )
        last_snapshot = snapshot
        last_recognized = recognized
        last_loading = loading_evidence
        last_s12_evidence = s12_evidence
        last_s14_signals = s14_signals
        if recognized == "S12" and stable_rounds >= 2 and not loading_evidence.get("loading_overlay_detected"):
            break
        if (time.perf_counter() - started) >= max_wait_s:
            break
        previous_xml_digest = xml_digest
        previous_visible_digest = visible_digest
        previous_signal_key = signal_key
    if not last_snapshot:
        last_snapshot = before_snapshot
    after_xml_digest = _sha256_text(str(last_snapshot.get("fresh_xml") or ""))
    after_visible_digest = _sha256_text("|".join(str(item) for item in last_snapshot.get("visible_texts", [])))
    after_screenshot_digest = _sha256_file(last_snapshot.get("screenshot_path"))
    return {
        "snapshot": last_snapshot,
        "wait_ms": int((time.perf_counter() - started) * 1000),
        "rounds": rounds,
        "stable_wait_rounds": round_index,
        "stable_rounds": stable_rounds,
        "recognized_page": last_recognized,
        "s12_candidate_signals": last_s12_evidence.get("s12_candidate_signals") if last_s12_evidence else [],
        "s12_report_page_evidence": last_s12_evidence,
        "s14_candidate_signals": last_s14_signals,
        "s14_suppressed_by_context": bool(last_s14_signals),
        "s14_suppression_reason": "S11_TO_S12_EXPECTS_S12" if last_s14_signals else "",
        "loading_overlay_detected": bool(last_loading.get("loading_overlay_detected")) if last_loading else False,
        "loading_overlay_evidence": last_loading,
        "loading_overlay_cleared": not bool(last_loading.get("loading_overlay_detected")) if last_loading else False,
        "loading_timeout_with_s12_signals": bool(
            last_loading.get("loading_overlay_detected") and last_s12_evidence.get("s12_candidate_signals")
        )
        if last_loading and last_s12_evidence
        else False,
        "xml_changed_after_click": after_xml_digest != before_xml_digest,
        "visible_text_changed_after_click": after_visible_digest != before_visible_digest,
        "screenshot_changed_after_click": bool(
            before_screenshot_digest and after_screenshot_digest and after_screenshot_digest != before_screenshot_digest
        ),
        "page_changed_after_click": bool(
            after_xml_digest != before_xml_digest or after_visible_digest != before_visible_digest
        ),
        "after_click_xml_digest": after_xml_digest,
        "after_click_visible_digest": after_visible_digest,
        "after_click_screenshot_digest": after_screenshot_digest,
    }


def _reset_reference_scoped_state(context: dict[str, Any], reference_index: int) -> dict[str, Any]:
    previous_reference = context.get("current_reference") if isinstance(context.get("current_reference"), dict) else {}
    previous_keys = [
        key
        for key in (
            "s11_report_search_state",
            "scroll_attempts",
            "report_entry_seen",
            "view_full_report_exact_text_seen",
            "merchant_self_check_marker_seen",
            "page_signature_history",
            "bottom_reposition_attempts",
            "excluded_reference_reason",
            "reference_exclusion_reason",
            "s14_state",
            "visited_s14_keys",
            "collected_s14_images",
            "no_semantic_change_count",
            "current_s14_page_label",
            "current_s14_caption",
            "s14_image_records",
            "s14_tab_records",
            "s14_horizontal_swipes",
            "s14_repeated_key_events",
        )
        if context.get(key) or previous_reference.get(key)
    ]
    context.update(
        {
            "s11_report_search_state": {},
            "scroll_attempts": 0,
            "report_entry_seen": False,
            "view_full_report_exact_text_seen": False,
            "merchant_self_check_marker_seen": False,
            "page_signature_history": [],
            "bottom_reposition_attempts": 0,
            "excluded_reference_reason": "",
            "reference_exclusion_reason": "",
            "s14_state": {},
            "visited_s14_keys": [],
            "collected_s14_images": [],
            "no_semantic_change_count": 0,
            "current_s14_page_label": "",
            "current_s14_caption": "",
            "damage_by_part": {},
            "s14_skip_count": 0,
            "s14_triggered": False,
            "s14_collect_done": False,
            "s14_tab_records": [],
            "s14_image_records": [],
            "s14_horizontal_swipes": [],
            "s14_tab_select_events": [],
            "s14_repeated_key_events": [],
            "s14_no_semantic_change_count": 0,
            "all_s14_tabs": [],
            "s14_sequence_terminal_snapshot": {},
            "s14_return_attempts": [],
            "exclude_current_reference_from_history": False,
        }
    )
    audit = {
        "reference_state_reset_done": True,
        "reset_reference_index": reference_index,
        "previous_reference_state_cleared": bool(previous_keys),
        "cleared_state_keys": previous_keys,
        "reference_state_leak_detected": False,
    }
    context["last_reference_state_reset"] = audit
    context.setdefault("reference_state_reset_history", []).append(audit)
    return audit


def handle_s10(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    client: AdbClient = context["client"]
    start = time.perf_counter()
    snapshot = _maybe_close_guazi_push_popup_and_resume(context, snapshot, current_stage="S10")
    _ensure_page("S10", recognizer, issues, snapshot)
    target_reference_index = int(context.get("current_reference_index") or 1)
    reference_state_reset = _reset_reference_scoped_state(context, target_reference_index)
    trisame_count = _first_stage_trisame_count(context.get("first_stage_evidence") or {})
    if trisame_count is not None and target_reference_index > trisame_count:
        context["all_trisame_sources_exhausted"] = True
        context["all_trisame_exhaustion_reason"] = "next_reference_index_exceeds_trisame_count"
        issue = issues.record(
            "ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW",
            "S10",
            "All confirmed same-source reference cars have been collected or attempted; refusing to enter more-cars/recommendation area.",
            {
                **snapshot,
                "target_reference_index": target_reference_index,
                "trisame_count": trisame_count,
                "collected_reference_count": len(context.get("reference_history") or []),
                "valid_reference_count": len(context.get("reference_history") or []),
                "all_trisame_sources_exhausted": True,
                "reason": "next_reference_index_exceeds_trisame_count",
                "first_stage_evidence": context.get("first_stage_evidence") or {},
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    expected_card = _expected_reference_card_with_continuation_context(
        context.get("first_stage_evidence") or {},
        target_reference_index,
        context.get("continuation_plan"),
    )
    snapshot["target_brand"] = context["target_car"].brand
    snapshot["target_car"] = {
        "brand": context["target_car"].brand,
        "series": context["target_car"].series,
        "model_year": context["target_car"].model_year,
        "trim": context["target_car"].trim,
    }
    reliable_evidence = _s10_reliable_list_evidence(
        snapshot,
        target_reference_index=target_reference_index,
        expected_card=expected_card,
    )
    context["s10_live_reliable_evidence"] = reliable_evidence
    if reliable_evidence.get("reliable") is not True:
        issue = issues.record(
            "POST_REFERENCE_RETURNED_LIST_SOURCE_UNRELIABLE",
            "S10",
            "Runtime recognized S10, but live XML did not prove a reliable sorted vehicle list before selecting next reference.",
            {
                **snapshot,
                "target_reference_index": target_reference_index,
                "expected_card": expected_card,
                "s10_reliable_list_evidence": reliable_evidence,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    machine.assert_action_allowed("S10", "collect_list_whitelist_fields")
    read_start = time.perf_counter()
    cards = _extract_s10_reference_cards(snapshot)
    context["current_list_cards"] = cards
    field_ms = int((time.perf_counter() - read_start) * 1000)
    machine.assert_action_allowed("S10", "tap_next_car_by_price_order")
    order_summary = _s10_same_price_group_summary([card for card in cards if card.get("card_complete") is True])
    context["s10_order_rule"] = "price_asc_mileage_desc_for_same_price"
    context["canonical_reference_order"] = reliable_evidence.get("canonical_reference_order")
    context["same_price_group_summary"] = order_summary
    selected_card, _legacy_tap_point, snapshot = _select_s10_reference_card_with_completion_scroll(
        context,
        snapshot,
        target_reference_index,
        expected_card,
        context.get("reference_history") or [],
    )
    cards = _extract_s10_reference_cards(snapshot)
    context["current_list_cards"] = cards
    reliable_evidence = _s10_reliable_list_evidence(
        snapshot,
        target_reference_index=target_reference_index,
        expected_card=expected_card,
    )
    context["s10_live_reliable_evidence"] = reliable_evidence
    order_summary = _s10_same_price_group_summary([card for card in cards if card.get("card_complete") is True])
    context["canonical_reference_order"] = reliable_evidence.get("canonical_reference_order")
    context["same_price_group_summary"] = order_summary
    if selected_card.get("ok") is False:
        issue = issues.record(
            str(selected_card.get("stop_code") or "REFERENCE_CARD_BINDING_NOT_UNIQUE"),
            "S10",
            str(selected_card.get("reason") or "Reference card could not be uniquely bound."),
            {**snapshot, "binding_result": selected_card},
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context["current_reference_index"] = int(selected_card["reference_index"])
    context["current_reference"] = {
        "reference_index": selected_card.get("reference_index"),
        "selected_reference_index": selected_card.get("selected_reference_index") or selected_card.get("reference_index"),
        "reference_key": selected_card.get("reference_key"),
        "list_title": selected_card.get("list_title"),
        "list_price_text": selected_card.get("list_price_text"),
        "list_price_10k": selected_card.get("list_price_10k"),
        "list_year": selected_card.get("list_year"),
        "list_mileage_10k_km": selected_card.get("list_mileage_10k_km"),
        "selected_card_title": selected_card.get("selected_card_title"),
        "selected_card_price": selected_card.get("selected_card_price"),
        "selected_card_metadata": selected_card.get("selected_card_metadata"),
        "selected_card_rank": selected_card.get("selected_card_rank"),
        "selected_click_bounds": selected_card.get("selected_click_bounds"),
        "selected_card_live_display_order": selected_card.get("selected_card_live_display_order"),
        "selected_by": selected_card.get("selected_by"),
        "select_strategy": selected_card.get("select_strategy"),
        "card_complete": selected_card.get("card_complete"),
        "has_title": selected_card.get("has_title"),
        "has_price": selected_card.get("has_price"),
        "has_metadata": selected_card.get("has_metadata"),
        "has_year": selected_card.get("has_year"),
        "has_mileage": selected_card.get("has_mileage"),
        "has_city": selected_card.get("has_city"),
        "card_fully_visible": selected_card.get("card_fully_visible"),
        "incomplete_reason": selected_card.get("incomplete_reason"),
        "s10_card_completion_scroll_used": selected_card.get("s10_card_completion_scroll_used"),
        "s10_card_completion_scroll_attempts": selected_card.get("s10_card_completion_scroll_attempts"),
        "continuation_mode": bool(context.get("continuation_mode")),
        "previous_reference_index": context.get("previous_reference_index"),
        "next_reference_index": target_reference_index,
        "s10_order_rule": context.get("s10_order_rule"),
        "canonical_reference_order": context.get("canonical_reference_order"),
        "same_price_group_detected": order_summary.get("same_price_group_detected"),
        "same_price_group_price": order_summary.get("same_price_group_price"),
        "same_price_group_order": order_summary.get("same_price_group_order"),
        "reference_already_collected_skips": selected_card.get("reference_already_collected_skips"),
        "clicked_card_bounds": selected_card.get("clicked_card_bounds"),
        "clicked_card_text_digest": selected_card.get("clicked_card_text_digest"),
        "s10_screenshot_path": selected_card.get("screenshot_path"),
        "s10_xml_path": selected_card.get("xml_path"),
        "title_match_strategy": selected_card.get("title_match_strategy"),
        "title_match_audit": selected_card.get("title_match_audit"),
        "title_normalized_match": selected_card.get("title_normalized_match"),
        "s10_ready_to_click_ms": int((time.perf_counter() - start) * 1000),
        "second_stage_s10_fast_handoff": context.get("second_stage_s10_fast_handoff"),
        **reference_state_reset,
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
            "s10_title_binding_scope": click_target.get("s10_title_binding_scope"),
            "s10_global_title_duplicate_count": click_target.get("s10_global_title_duplicate_count"),
            "s10_local_title_node_count": click_target.get("s10_local_title_node_count"),
            "s10_selected_card_local_index": click_target.get("s10_selected_card_local_index"),
            "s10_business_reference_index": click_target.get("s10_business_reference_index"),
            "s10_reference_index_rebased_after_autoscroll": click_target.get("s10_reference_index_rebased_after_autoscroll"),
            "s10_selected_card_bounds": click_target.get("s10_selected_card_bounds"),
            "s10_selected_card_click_target_bounds": click_target.get("s10_selected_card_click_target_bounds"),
            "s10_selected_card_binding_decision": click_target.get("s10_selected_card_binding_decision"),
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
    contract_click_start = time.perf_counter()
    action_ms = contract_execute_click(
        context,
        snapshot,
        "S10",
        "S10_ONLY_ALLOWED_ACTION_CLICK_REFERENCE_CARD_TITLE",
        (int(tap_point[0]), int(tap_point[1])),
        evidence={
            "click_strategy": click_target.get("click_strategy"),
            "clicked_text": click_target.get("clicked_text"),
            "clicked_node_bounds": click_target.get("clicked_node_bounds"),
            "reference_index": context["current_reference"].get("reference_index"),
            "s10_title_binding_scope": click_target.get("s10_title_binding_scope"),
            "s10_selected_card_binding_decision": click_target.get("s10_selected_card_binding_decision"),
        },
    )
    context["current_reference"]["s10_contract_validate_ms"] = max(
        0,
        int((time.perf_counter() - contract_click_start) * 1000) - int(action_ms or 0),
    )
    context["current_reference"].update(
        {
            "s10_to_s11_click_executed": True,
            "transition_context": "S10_TO_S11",
            "s11_page_recognized": False,
            "s11_handler_invoked": False,
            "s11_allowed_action_started": False,
            "s11_contract_execution_ack": False,
            "s11_contract_execution_ack_stage": S11_CONTRACT_EXECUTION_ACK_STAGE,
            "s11_report_search_state_initialized": False,
        }
    )
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
            "click_strategy": click_target.get("click_strategy"),
            "clicked_text": click_target.get("clicked_text"),
            "clicked_node_bounds": click_target.get("clicked_node_bounds"),
            "clicked_point": tap_point,
            "s10_title_binding_scope": click_target.get("s10_title_binding_scope"),
            "s10_global_title_duplicate_count": click_target.get("s10_global_title_duplicate_count"),
            "s10_local_title_node_count": click_target.get("s10_local_title_node_count"),
            "s10_selected_card_local_index": click_target.get("s10_selected_card_local_index"),
            "s10_business_reference_index": click_target.get("s10_business_reference_index"),
            "s10_reference_index_rebased_after_autoscroll": click_target.get("s10_reference_index_rebased_after_autoscroll"),
            "s10_selected_card_bounds": click_target.get("s10_selected_card_bounds"),
            "s10_selected_card_click_target_bounds": click_target.get("s10_selected_card_click_target_bounds"),
            "s10_selected_card_binding_decision": click_target.get("s10_selected_card_binding_decision"),
            "reference_index": context["current_reference"].get("reference_index"),
            "selected_reference_index": context["current_reference"].get("selected_reference_index"),
            "selected_card_price": context["current_reference"].get("selected_card_price"),
            "selected_card_metadata": context["current_reference"].get("selected_card_metadata"),
            "selected_card_rank": context["current_reference"].get("selected_card_rank"),
            "selected_card_live_display_order": context["current_reference"].get("selected_card_live_display_order"),
            "selected_by": context["current_reference"].get("selected_by"),
            "select_strategy": context["current_reference"].get("select_strategy"),
            "continuation_mode": context.get("continuation_mode"),
            "reference_key": context["current_reference"].get("reference_key"),
            "s10_order_rule": context["current_reference"].get("s10_order_rule"),
            "same_price_group_detected": context["current_reference"].get("same_price_group_detected"),
            "same_price_group_price": context["current_reference"].get("same_price_group_price"),
            "same_price_group_order": context["current_reference"].get("same_price_group_order"),
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
    context["current_reference"]["page_changed_after_click"] = bool(wait_evidence.get("entered_s11") or wait_evidence.get("any_screenshot_changed") or wait_evidence.get("any_xml_changed"))
    context["current_reference"]["reference_card_click_to_page_change_ms"] = wait_evidence.get("first_page_change_ms") or wait_ms
    context["current_reference"]["s11_recognizer_ms"] = sum(
        int(item.get("recognize_ms") or 0) for item in wait_evidence.get("wait_rounds", [])
    )
    for item in wait_evidence.get("wait_rounds", []):
        timing.add(
            step_name="S10_TO_S11_PRE_DUMP_STABILIZE",
            page_name="S10",
            action_name="short_poll_visual_window_before_xml_dump",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=int(item.get("pre_dump_stabilize_total_ms") or 0),
            screenshot_path=str(item.get("screenshot_path") or ""),
            xml_path="",
            extra={
                "wait_round_index": item.get("wait_round_index"),
                "pre_dump_strategy": item.get("pre_dump_strategy"),
                "pre_dump_stabilize_enabled": item.get("pre_dump_stabilize_enabled"),
                "pre_dump_stabilize_rounds": item.get("pre_dump_stabilize_rounds"),
                "pre_dump_stabilize_total_ms": item.get("pre_dump_stabilize_total_ms"),
                "pre_dump_visual_stable": item.get("pre_dump_visual_stable"),
                "dump_started_after_visual_stable": item.get("dump_started_after_visual_stable"),
                "dump_started_after_max_wait": item.get("dump_started_after_max_wait"),
                "pre_dump_stabilize_max_wait_ms": item.get("pre_dump_stabilize_max_wait_ms"),
                "pre_dump_stabilize_previous_max_wait_ms": item.get("pre_dump_stabilize_previous_max_wait_ms"),
                "pre_dump_stabilize_interval_ms": item.get("pre_dump_stabilize_interval_ms"),
                "pre_dump_stable_rounds_required": item.get("pre_dump_stable_rounds_required"),
                "pre_dump_stabilize_round_details": item.get("pre_dump_stabilize_round_details"),
                "screenshot_reused_from_pre_dump": item.get("screenshot_reused_from_pre_dump"),
                "optimized": True,
                "optimization_type": "s10_to_s11_pre_dump_adaptive_wait_cap",
                "before_estimated_duration_seconds": round(float(item.get("pre_dump_stabilize_previous_max_wait_ms") or 2000) / 1000, 3),
                "after_duration_seconds": round(float(item.get("pre_dump_stabilize_max_wait_ms") or 1200) / 1000, 3),
                "saved_seconds_estimate": round(max(0.0, float(item.get("pre_dump_stabilize_previous_max_wait_ms") or 2000) - float(item.get("pre_dump_stabilize_max_wait_ms") or 1200)) / 1000, 3),
                "contract_validation_preserved": True,
                "reason_category": "S10_TO_S11_PRE_DUMP_STABILIZE",
                "reason_detail": "short-poll screenshot/window stability only decides when to start XML dump; it never replaces S11 XML contract recognition",
                "solution": "compare total fresh time as pre_dump_stabilize_total_ms plus XML dump time",
            },
        )
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
                "s10_to_s11_xml_dump_mode": item.get("s10_to_s11_xml_dump_mode"),
                "compressed_xml_dump_ms": item.get("compressed_xml_dump_ms"),
                "full_xml_dump_ms": item.get("full_xml_dump_ms"),
                "compressed_xml_dump_command": item.get("compressed_xml_dump_command"),
                "full_xml_dump_command": item.get("full_xml_dump_command"),
                "compressed_xml_dump_rc": item.get("compressed_xml_dump_rc"),
                "full_xml_dump_rc": item.get("full_xml_dump_rc"),
                "compressed_xml_path": item.get("compressed_xml_path"),
                "full_xml_path": item.get("full_xml_path"),
                "compressed_xml_size": item.get("compressed_xml_size"),
                "full_xml_size": item.get("full_xml_size"),
                "compressed_node_count": item.get("compressed_node_count"),
                "full_node_count": item.get("full_node_count"),
                "compressed_recognized_page": item.get("compressed_recognized_page"),
                "full_recognized_page": item.get("full_recognized_page"),
                "compressed_recognized_by": item.get("compressed_recognized_by"),
                "full_recognized_by": item.get("full_recognized_by"),
                "compressed_s11_contract_hit": item.get("compressed_s11_contract_hit"),
                "full_s11_contract_hit": item.get("full_s11_contract_hit"),
                "s11_top_image_only_evidence": item.get("s11_top_image_only_evidence"),
                "page_changed_after_click": item.get("page_changed_after_click"),
                "fallback_reason": item.get("fallback_reason"),
                "pre_dump_strategy": item.get("pre_dump_strategy"),
                "pre_dump_stabilize_enabled": item.get("pre_dump_stabilize_enabled"),
                "pre_dump_stabilize_rounds": item.get("pre_dump_stabilize_rounds"),
                "pre_dump_stabilize_total_ms": item.get("pre_dump_stabilize_total_ms"),
                "pre_dump_visual_stable": item.get("pre_dump_visual_stable"),
                "dump_started_after_visual_stable": item.get("dump_started_after_visual_stable"),
                "dump_started_after_max_wait": item.get("dump_started_after_max_wait"),
                "s10_to_s11_total_fresh_ms": item.get("s10_to_s11_total_fresh_ms"),
                "is_aggregate": bool(item.get("full_xml_dump_ms")),
                "xml_rc": item.get("xml_rc"),
                "xml_stderr": item.get("xml_stderr"),
                "xml_stale": item.get("xml_stale"),
                "reason_category": "S10_TO_S11_FULL_XML_CAN_BE_COMPRESSED" if item.get("s10_to_s11_xml_dump_mode") == "compressed" else ("S10_TO_S11_XML_DUMP_SLOW" if int(item.get("xml_dump_ms") or 0) > 1000 else "S10_TO_S11_XML_DUMP"),
                "reason_detail": "S10 to S11 XML evidence prefers compressed uiautomator dump and falls back to full XML at most once",
                "solution": "use compressed XML when it satisfies the S11 contract; keep one full XML fallback when compressed evidence is insufficient",
            },
        )
        timing.add(
            step_name="S10_TO_S11_XML_DUMP_COMPRESSED",
            page_name="S10",
            action_name="dump_compressed_xml_during_s11_wait",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=int(item.get("compressed_xml_dump_ms") or 0),
            transition_wait_ms=0,
            screenshot_path="",
            xml_path=str(item.get("compressed_xml_path") or ""),
            extra={
                "wait_round_index": item.get("wait_round_index"),
                "compressed_xml_dump_ms": item.get("compressed_xml_dump_ms"),
                "compressed_xml_dump_command": item.get("compressed_xml_dump_command"),
                "compressed_xml_dump_rc": item.get("compressed_xml_dump_rc"),
                "compressed_xml_size": item.get("compressed_xml_size"),
                "compressed_node_count": item.get("compressed_node_count"),
                "compressed_recognized_page": item.get("compressed_recognized_page"),
                "compressed_recognized_by": item.get("compressed_recognized_by"),
                "compressed_s11_contract_hit": item.get("compressed_s11_contract_hit"),
                "s11_top_image_only_evidence": item.get("s11_top_image_only_evidence"),
                "page_changed_after_click": item.get("page_changed_after_click"),
                "fallback_reason": item.get("fallback_reason"),
                "pre_dump_strategy": item.get("pre_dump_strategy"),
                "pre_dump_stabilize_total_ms": item.get("pre_dump_stabilize_total_ms"),
                "s10_to_s11_total_fresh_ms": item.get("s10_to_s11_total_fresh_ms"),
                "reason_category": "S10_TO_S11_FULL_XML_CAN_BE_COMPRESSED" if item.get("compressed_s11_contract_hit") else "S10_TO_S11_COMPRESSED_XML_NOT_SUFFICIENT",
                "reason_detail": "compressed XML is the first S10-to-S11 page-contract evidence attempt",
                "solution": "skip full XML when compressed XML recognizes S11; otherwise perform exactly one full fallback",
            },
        )
        if int(item.get("full_xml_dump_ms") or 0) > 0:
            timing.add(
                step_name="S10_TO_S11_XML_DUMP_FULL_FALLBACK",
                page_name="S10",
                action_name="dump_full_xml_after_compressed_miss",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=int(item.get("full_xml_dump_ms") or 0),
                transition_wait_ms=0,
                screenshot_path="",
                xml_path=str(item.get("full_xml_path") or ""),
                extra={
                    "wait_round_index": item.get("wait_round_index"),
                    "full_xml_dump_ms": item.get("full_xml_dump_ms"),
                    "full_xml_dump_command": item.get("full_xml_dump_command"),
                    "full_xml_dump_rc": item.get("full_xml_dump_rc"),
                    "full_xml_size": item.get("full_xml_size"),
                    "full_node_count": item.get("full_node_count"),
                    "full_recognized_page": item.get("full_recognized_page"),
                    "full_recognized_by": item.get("full_recognized_by"),
                    "full_s11_contract_hit": item.get("full_s11_contract_hit"),
                    "s11_top_image_only_evidence": item.get("s11_top_image_only_evidence"),
                    "page_changed_after_click": item.get("page_changed_after_click"),
                    "fallback_reason": item.get("fallback_reason"),
                    "reason_category": "S10_TO_S11_XML_DUMP_SLOW",
                    "reason_detail": "full XML fallback is used once because compressed XML did not satisfy S11 contract evidence",
                    "solution": "do not repeat full fallback in the same wait round",
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
                "recognized_by": item.get("recognized_by"),
                "s10_to_s11_xml_dump_mode": item.get("s10_to_s11_xml_dump_mode"),
                "compressed_recognized_page": item.get("compressed_recognized_page"),
                "full_recognized_page": item.get("full_recognized_page"),
                "s11_top_image_only_evidence": item.get("s11_top_image_only_evidence"),
                "page_changed_after_click": item.get("page_changed_after_click"),
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
                "recognized_by": item.get("recognized_by"),
                "xml_stale": item.get("xml_stale"),
                "screenshot_changed": item.get("screenshot_changed"),
                "s10_to_s11_xml_dump_mode": item.get("s10_to_s11_xml_dump_mode"),
                "compressed_xml_dump_ms": item.get("compressed_xml_dump_ms"),
                "full_xml_dump_ms": item.get("full_xml_dump_ms"),
                "compressed_xml_size": item.get("compressed_xml_size"),
                "full_xml_size": item.get("full_xml_size"),
                "compressed_recognized_page": item.get("compressed_recognized_page"),
                "full_recognized_page": item.get("full_recognized_page"),
                "fallback_reason": item.get("fallback_reason"),
                "pre_dump_stabilize_ms": item.get("pre_dump_stabilize_ms"),
                "pre_dump_strategy": item.get("pre_dump_strategy"),
                "pre_dump_stabilize_enabled": item.get("pre_dump_stabilize_enabled"),
                "pre_dump_stabilize_rounds": item.get("pre_dump_stabilize_rounds"),
                "pre_dump_stabilize_total_ms": item.get("pre_dump_stabilize_total_ms"),
                "pre_dump_visual_stable": item.get("pre_dump_visual_stable"),
                "dump_started_after_visual_stable": item.get("dump_started_after_visual_stable"),
                "dump_started_after_max_wait": item.get("dump_started_after_max_wait"),
                "s10_to_s11_total_fresh_ms": item.get("s10_to_s11_total_fresh_ms"),
                "page_load_visual_state": item.get("page_load_visual_state"),
                "dump_started_after_visual_change": item.get("dump_started_after_visual_change"),
                "visible_text_digest": item.get("visible_text_digest"),
                "s11_top_image_only_evidence": item.get("s11_top_image_only_evidence"),
                "page_changed_after_click": item.get("page_changed_after_click"),
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
    final_s10_to_s11_page = _recognize_mainline_page(recognizer, next_snapshot)
    if final_s10_to_s11_page != "S11":
        stop_code, message = _s10_to_s11_failure_stop_code(wait_evidence)
        issue = issues.record(
            stop_code,
            "S10",
            message,
            {
                "click_strategy": "text_node_bounds",
                "clicked_text": click_target.get("clicked_text"),
                "clicked_node_bounds": click_target.get("clicked_node_bounds"),
                "clicked_point": tap_point,
                "s10_to_s11_click_executed": True,
                "page_changed_after_click": context["current_reference"].get("page_changed_after_click"),
                "transition_context": "S10_TO_S11",
                "current_reference": context["current_reference"],
                "s10_to_s11_wait": wait_evidence,
                **next_snapshot,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    s11_source_round = next(
        (item for item in wait_evidence.get("wait_rounds", []) if item.get("recognized_page") == "S11"),
        {},
    )
    s11_snapshot_source = "S10_TO_S11_XML_DUMP"
    if s11_source_round.get("s10_to_s11_xml_dump_mode") == "compressed_then_full":
        s11_snapshot_source = "full_fallback"
    s11_entry_snapshot = dict(next_snapshot)
    context["s11_entry_snapshot"] = s11_entry_snapshot
    context["s11_entry_snapshot_source"] = s11_snapshot_source
    context["s11_entry_snapshot_wait_round_index"] = s11_source_round.get("wait_round_index")
    context["s11_entry_snapshot_reused"] = False
    context["current_reference"].update(
        {
            "s11_entry_snapshot_xml_path": str(next_snapshot.get("xml_path") or ""),
            "s11_entry_snapshot_screenshot_path": str(next_snapshot.get("screenshot_path") or ""),
            "s11_entry_snapshot_source": s11_snapshot_source,
            "s11_entry_snapshot_wait_round_index": s11_source_round.get("wait_round_index"),
            "s10_to_s11_xml_dump_mode": s11_source_round.get("s10_to_s11_xml_dump_mode"),
            "compressed_xml_dump_ms": s11_source_round.get("compressed_xml_dump_ms"),
            "full_xml_dump_ms": s11_source_round.get("full_xml_dump_ms"),
            "compressed_xml_size": s11_source_round.get("compressed_xml_size"),
            "full_xml_size": s11_source_round.get("full_xml_size"),
            "compressed_recognized_page": s11_source_round.get("compressed_recognized_page"),
            "full_recognized_page": s11_source_round.get("full_recognized_page"),
            "fallback_reason": s11_source_round.get("fallback_reason"),
            "pre_dump_strategy": s11_source_round.get("pre_dump_strategy"),
            "pre_dump_stabilize_enabled": s11_source_round.get("pre_dump_stabilize_enabled"),
            "pre_dump_stabilize_rounds": s11_source_round.get("pre_dump_stabilize_rounds"),
            "pre_dump_stabilize_total_ms": s11_source_round.get("pre_dump_stabilize_total_ms"),
            "pre_dump_visual_stable": s11_source_round.get("pre_dump_visual_stable"),
            "dump_started_after_visual_stable": s11_source_round.get("dump_started_after_visual_stable"),
            "dump_started_after_max_wait": s11_source_round.get("dump_started_after_max_wait"),
            "s10_to_s11_total_fresh_ms": s11_source_round.get("s10_to_s11_total_fresh_ms"),
            "s11_top_image_only_evidence": s11_source_round.get("s11_top_image_only_evidence")
            or next_snapshot.get("s11_top_image_only_evidence"),
            "recognized_by": s11_source_round.get("recognized_by") or next_snapshot.get("recognized_by"),
            "s11_page_recognized": True,
            "s11_handler_invoked": False,
            "s11_allowed_action_started": False,
            "s11_contract_execution_ack": False,
            "s11_contract_execution_ack_stage": S11_CONTRACT_EXECUTION_ACK_STAGE,
            "s11_report_search_state_initialized": False,
        }
    )
    physical_proof = _build_reference_physical_ui_transition_proof(
        context,
        reference_index=_safe_int(context["current_reference"].get("reference_index") or target_reference_index, default=target_reference_index),
        expected_card=expected_card,
        from_page="S10",
        to_page="S11",
        transition_context="S10_TO_S11",
        before_snapshot=snapshot,
        after_snapshot=next_snapshot,
        click_evidence={
            "click_source": "xml_exact_title_bounds",
            "clicked_text": click_target.get("clicked_text"),
            "clicked_node_bounds": click_target.get("clicked_node_bounds"),
            "clicked_point": tap_point,
            "s10_selected_card_binding_decision": click_target.get("s10_selected_card_binding_decision"),
        },
        page_changed_after_click=bool(context["current_reference"].get("page_changed_after_click")),
        next_card_click_verified=True,
        return_to_reliable_s10_verified=False,
    )
    context["current_reference"]["physical_ui_transition_proof"] = physical_proof
    context["current_reference"]["physical_evidence_ok"] = bool(physical_proof.get("physical_evidence_ok"))
    context["current_reference"]["actual_page_signature"] = physical_proof.get("actual_page_signature")
    return "S11", next_snapshot


TRANSFER_COUNT_PATTERNS = [
    re.compile(r"过户\s*(\d+)\s*次"),
    re.compile(r"(\d+)\s*次\s*过户"),
    re.compile(r"过户次数\s*(\d+)"),
]


def _extract_transfer_count_evidence(snapshot_or_text: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(snapshot_or_text, dict):
        snapshot = snapshot_or_text
        nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
        candidates: list[dict[str, Any]] = []
        for node in nodes:
            labels = [str(node.get("text") or ""), str(node.get("content_desc") or "")]
            for label in labels:
                value = label.strip()
                if not value or "过户" not in value:
                    continue
                candidates.append(
                    {
                        "text": str(node.get("text") or ""),
                        "content_desc": str(node.get("content_desc") or ""),
                        "bounds": list(node.get("bounds") or []),
                        "class": node.get("class_name"),
                        "resource_id": node.get("resource_id"),
                        "clickable": node.get("clickable"),
                        "enabled": node.get("enabled"),
                    }
                )
        search_text = "\n".join(
            [str(item.get("text") or "") for item in nodes]
            + [str(item.get("content_desc") or "") for item in nodes]
            + [str(item) for item in snapshot.get("visible_texts") or []]
            + [str(snapshot.get("visible_blob") or "")]
        )
    else:
        snapshot = {}
        candidates = []
        search_text = str(snapshot_or_text or "")

    for pattern in TRANSFER_COUNT_PATTERNS:
        match = pattern.search(search_text)
        if match:
            return {
                "transfer_count": int(match.group(1)),
                "transfer_count_text": match.group(0),
                "transfer_count_pattern": pattern.pattern,
                "transfer_count_candidates": candidates,
            }
    return {
        "transfer_count": None,
        "transfer_count_text": "",
        "transfer_count_pattern": "",
        "transfer_count_candidates": candidates,
    }


def _extract_transfer_count(text: str) -> int | None:
    evidence = _extract_transfer_count_evidence(text)
    value = evidence.get("transfer_count")
    return int(value) if value is not None else None


def handle_s11(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    client: AdbClient = context["client"]
    start = time.perf_counter()
    snapshot = _maybe_close_guazi_push_popup_and_resume(context, snapshot, current_stage="S11_REPORT_SEARCH")
    _ensure_page("S11", recognizer, issues, snapshot)
    context.setdefault("current_reference", {})
    entry_snapshot = context.get("s11_entry_snapshot") or {}
    s11_entry_snapshot_reused = bool(
        entry_snapshot
        and entry_snapshot.get("xml_path") == snapshot.get("xml_path")
        and entry_snapshot.get("screenshot_path") == snapshot.get("screenshot_path")
        and _recognize_mainline_page(recognizer, snapshot) == "S11"
    )
    context["s11_entry_snapshot_reused"] = s11_entry_snapshot_reused
    context["s11_handler_initial_dump_skipped_due_to_reuse"] = s11_entry_snapshot_reused
    context["current_reference"].update(
        {
            "s11_entry_snapshot_reused": s11_entry_snapshot_reused,
            "s11_entry_snapshot_xml_path": str(snapshot.get("xml_path") or ""),
            "s11_entry_snapshot_screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "s11_entry_snapshot_source": context.get("s11_entry_snapshot_source"),
            "s11_handler_initial_dump_skipped_due_to_reuse": s11_entry_snapshot_reused,
        }
    )
    _mark_s11_contract_execution_ack(
        context,
        snapshot,
        handler_invoked=True,
        allowed_action_started=True,
        report_search_state_initialized=False,
        allowed_action_name="collect_transfer_count",
    )
    evidence_pairing = _evidence_pairing_check(
        context,
        snapshot,
        page_id="S11",
        used_for_decision="s11_page_contract_and_report_entry_search",
    )
    context["current_reference"]["evidence_pairing_check"] = evidence_pairing
    if evidence_pairing.get("evidence_contamination_warning"):
        contract_stop(
            context,
            "S11",
            "EVIDENCE_PAIRING_CURRENT_FINGERPRINT_MISMATCH",
            "S11 evidence contains foreign target terms and cannot be used for the current target decision.",
            {**snapshot, **evidence_pairing},
        )
    if s11_entry_snapshot_reused:
        timing.add(
            step_name="S11_ENTRY_SNAPSHOT_REUSE",
            page_name="S11",
            action_name="reuse_s10_to_s11_wait_round_snapshot",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra={
                "s11_entry_snapshot_reused": True,
                "s11_entry_snapshot_xml_path": str(snapshot.get("xml_path") or ""),
                "s11_entry_snapshot_screenshot_path": str(snapshot.get("screenshot_path") or ""),
                "s11_entry_snapshot_source": context.get("s11_entry_snapshot_source"),
                "s11_handler_initial_dump_skipped_due_to_reuse": True,
                "recognized_page": "S11",
                "foreground_package": snapshot.get("foreground_package"),
                "focused_window": snapshot.get("focused_window"),
                "visible_text_digest": list(snapshot.get("visible_texts") or [])[:40],
                "reason_category": "RUNTIME_REUSE_EXISTING_FRESH",
                "reason_detail": "S11 handler reuses the S10_TO_S11 wait-round fresh snapshot that already recognized S11.",
                "solution": "reuse same-round S11 XML/screenshot evidence and avoid an immediate duplicate initial dump",
            },
        )
    read_start = time.perf_counter()
    transfer_evidence = _extract_transfer_count_evidence(snapshot)
    transfer_count = transfer_evidence.get("transfer_count")
    if transfer_count is None:
        issue = issues.record(
            "FIELD_MISSING",
            "S11",
            "Failed to read transfer count.",
            {
                **snapshot,
                "transfer_count_candidates": transfer_evidence.get("transfer_count_candidates"),
                "transfer_count_text": transfer_evidence.get("transfer_count_text"),
                "parsed_transfer_count": None,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context["current_reference"]["transfer_count"] = transfer_count
    context["current_reference"]["transfer_count_text"] = transfer_evidence.get("transfer_count_text")
    context["current_reference"]["parsed_transfer_count"] = transfer_count
    context["current_reference"]["transfer_count_pattern"] = transfer_evidence.get("transfer_count_pattern")
    context["current_reference"]["transfer_count_candidates"] = transfer_evidence.get("transfer_count_candidates")
    context["current_reference"]["insurance_claim_text"] = next(
        (str(item) for item in snapshot.get("visible_texts") or [] if "理赔" in str(item)),
        "",
    )
    context["current_reference"]["mileage_age_text"] = next(
        (str(item) for item in snapshot.get("visible_texts") or [] if "万公里" in str(item)),
        "",
    )
    context["current_reference"]["listing_id"] = next(
        (str(item).replace("车源号:", "") for item in snapshot.get("visible_texts") or [] if str(item).startswith("车源号:")),
        "",
    )
    field_ms = int((time.perf_counter() - read_start) * 1000)
    entry_search_start = time.perf_counter()
    s11_contract_execution_ack = _mark_s11_contract_execution_ack(
        context,
        snapshot,
        handler_invoked=True,
        allowed_action_started=True,
        report_search_state_initialized=True,
        allowed_action_name="find_view_full_report",
    )
    report_node, report_entry_text = _find_s11_official_report_entry_node(snapshot)
    entry_visibility = _s11_report_entry_visibility(snapshot, report_node)
    entry_search_ms = int((time.perf_counter() - entry_search_start) * 1000)
    report_found_in_entry_snapshot = report_node is not None and bool(report_node.get("bounds"))
    timing.add(
        step_name="S11_REPORT_ENTRY_NODE_SEARCH",
        page_name="S11",
        action_name="find_view_full_report_in_entry_snapshot",
        contract_check_ms=int((read_start - start) * 1000),
        field_read_ms=entry_search_ms,
        action_ms=0,
        transition_wait_ms=0,
        screenshot_path=str(snapshot.get("screenshot_path") or ""),
        xml_path=str(snapshot.get("xml_path") or ""),
        extra={
            "s11_entry_snapshot_reused": s11_entry_snapshot_reused,
            "s11_report_entry_found_in_entry_snapshot": report_found_in_entry_snapshot,
            "official_report_entry_text": report_entry_text,
            "s11_report_entry_node_bounds": report_node.get("bounds") if report_node else None,
            "exact_report_entry_fully_visible": entry_visibility.get("exact_report_entry_fully_visible"),
            "exact_report_entry_in_safe_click_region": entry_visibility.get("exact_report_entry_in_safe_click_region"),
            "safe_bottom_y": entry_visibility.get("safe_bottom_y"),
            "bottom_bar_detected": entry_visibility.get("bottom_bar_detected"),
            "bottom_bar_bounds": entry_visibility.get("bottom_bar_bounds"),
            "visibility_reason": entry_visibility.get("reason"),
            "s11_report_entry_click_strategy": None,
            "s11_report_scroll_count": 0,
            "s11_report_reposition_scroll_count": 0,
            "s11_report_xml_parse_count_per_round": 1,
            **s11_contract_execution_ack,
            "reason_category": "RUNTIME_REUSE_EXISTING_FRESH",
            "reason_detail": "search the reused S11 entry XML for 鏌ョ湅瀹屾暣鎶ュ憡 before scrolling",
            "solution": "do not click until the exact report entry is fully visible and outside the bottom fixed action bar",
        },
    )
    report_snapshot = snapshot
    report_scroll_count = 0
    report_reposition_count = 0
    report_scroll_action_ms_total = 0
    report_scroll_wait_ms_total = 0
    report_visibility = entry_visibility
    max_search_scrolls = 12
    max_reposition_scrolls = 3
    report_xml_stabilization_enabled = False
    report_xml_stabilization_count = 0
    report_xml_stabilization_attempted = False
    last_xml_stabilization_evidence: dict[str, Any] = {}
    report_search_signatures = [_sha256_text("|".join(str(item) for item in report_snapshot.get("visible_texts", [])))]
    report_search_iterations = 0
    report_weak_marker_seen_ever = False
    previous_report_weak_marker_seen = False
    report_backtrack_attempted = False
    report_overshoot_suspected = False
    report_first_scroll_done = False
    s11_stale_xml_redump_attempted = False
    s11_stale_xml_nudge_attempted = False
    s11_internal_mismatch_redump_attempted = False
    last_fresh_pair_evidence: dict[str, Any] = {}
    s11_report_entry_click_source_override: str | None = None
    s11_stale_xml_recovery_evidence: dict[str, Any] = {}
    last_scroll_step_evidence: dict[str, Any] = {
        "s11_report_search_scroll_mode": "first",
        "s11_report_entry_backtrack_attempted": False,
        "s11_report_entry_overshoot_suspected": False,
        "s11_first_scroll_done": False,
        "s11_first_scroll_step_px": None,
        "s11_report_search_iterations": 0,
        "s11_report_entry_scroll_step_ratio": S11_FIRST_SCROLL_SCREEN_RATIO,
        "s11_report_entry_scroll_distance_px": None,
        "s11_report_scroll_step_px": None,
        "s11_first_scroll_strategy": S11_FIRST_SCROLL_STRATEGY,
        "s11_first_scroll_screen_ratio": S11_FIRST_SCROLL_SCREEN_RATIO,
        "s11_first_scroll_requested_distance_px": None,
        "view_full_report_found_after_scroll_attempt": None,
        "stop_scroll_reason": "",
    }
    while True:
        report_search_iterations += 1
        fresh_pair = _s11_report_fresh_pair_evidence(
            report_snapshot,
            action_context="S11_REPORT_SEARCH",
            iteration=report_search_iterations,
        )
        last_fresh_pair_evidence = fresh_pair
        timing.add(
            step_name="S11_FRESH_EVIDENCE_PAIR_CHECK",
            page_name="S11",
            action_name="validate_screenshot_xml_pair_before_report_entry_decision",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
            xml_path=str(report_snapshot.get("xml_path") or ""),
            extra={**fresh_pair, "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_V1_39},
        )
        if not fresh_pair.get("xml_dump_ok") or not fresh_pair.get("xml_mtime_valid"):
            issue = issues.record(
                "S11_FRESH_EVIDENCE_PAIR_MISMATCH",
                "S11",
                "S11 report-entry fresh evidence pair is invalid; XML dump must not be reused for report-entry decisions.",
                {**fresh_pair, **report_snapshot},
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        if fresh_pair.get("s11_xml_stale"):
            stale_report_node, stale_report_text = _find_s11_official_report_entry_node(report_snapshot)
            if stale_report_node is not None and stale_report_node.get("bounds"):
                report_node = stale_report_node
                report_entry_text = stale_report_text
                report_visibility = _s11_report_entry_visibility(report_snapshot, report_node)
                s11_report_entry_click_source_override = "xml_exact_text_bounds_with_stale_warning"
                last_fresh_pair_evidence = {
                    **fresh_pair,
                    "s11_xml_stale_warning": True,
                    "s11_report_entry_click_mode": "xml_node_click",
                    "s11_report_entry_click_source": "xml_exact_text_bounds_with_stale_warning",
                }
                timing.add(
                    step_name="S11_STALE_XML_EXACT_REPORT_NODE_BOUND",
                    page_name="S11",
                    action_name="bind_exact_report_entry_bounds_even_when_xml_has_stale_list_signals",
                    contract_check_ms=0,
                    field_read_ms=0,
                    action_ms=0,
                    transition_wait_ms=0,
                    screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
                    xml_path=str(report_snapshot.get("xml_path") or ""),
                    extra={
                        **last_fresh_pair_evidence,
                        "report_entry_bounds": report_node.get("bounds"),
                        "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_NO_FIXED_COORDINATE,
                        "reason_category": "S11_XML_BOUNDS_BINDING_WITH_STALE_WARNING",
                        "reason_detail": "Exact report-entry accessibility bounds are available; stale list signals are warning-only for this click.",
                    },
                )
                break
            if not s11_stale_xml_redump_attempted:
                s11_stale_xml_redump_attempted = True
                wait_ms = 400
                time.sleep(wait_ms / 1000)
                report_snapshot = _capture_with_global_popup_guard(
                    context,
                    f"s11_report_entry_stale_xml_wait_redump_{report_search_iterations}",
                    current_stage="S11_REPORT_SEARCH",
                )
                report_node, report_entry_text = _find_s11_official_report_entry_node(report_snapshot)
                report_visibility = _s11_report_entry_visibility(report_snapshot, report_node)
                s11_stale_xml_recovery_evidence = {
                    **fresh_pair,
                    "s11_xml_stale_warning": True,
                    "s11_stale_xml_redump_attempted": True,
                    "s11_stale_xml_redump_wait_ms": wait_ms,
                    "s11_report_entry_seen_after_stale_redump": report_node is not None,
                    "s11_report_entry_click_mode": "xml_after_stale_recovery" if report_node is not None else "",
                    "s11_report_entry_click_source": "xml_after_stale_recovery" if report_node is not None else "",
                }
                timing.add(
                    step_name="S11_STALE_XML_WAIT_REDUMP_ONCE",
                    page_name="S11",
                    action_name="wait_then_redump_xml_after_s10_stale_signals_in_s11_context",
                    contract_check_ms=0,
                    field_read_ms=0,
                    action_ms=0,
                    transition_wait_ms=wait_ms,
                    screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
                    xml_path=str(report_snapshot.get("xml_path") or ""),
                    extra={
                        **s11_stale_xml_recovery_evidence,
                        "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_NO_FIXED_COORDINATE,
                        "reason_category": "S11_XML_STALE_RECOVERY_REDUMP",
                        "reason_detail": "S11 XML contains stale S10 list signals; wait briefly and redump before any report-entry click.",
                    },
                )
                if report_node is not None:
                    s11_report_entry_click_source_override = "xml_after_stale_recovery"
                continue
            if not s11_stale_xml_nudge_attempted:
                s11_stale_xml_nudge_attempted = True
                machine.assert_action_allowed("S11", "scroll_to_report")
                report_snapshot, scroll_ms, wait_ms, report_visibility = _execute_s11_report_entry_xml_refresh_nudge(
                    context,
                    report_snapshot,
                    attempt_index=report_search_iterations,
                    fresh_pair=fresh_pair,
                )
                report_search_signatures.append(_sha256_text("|".join(str(item) for item in report_snapshot.get("visible_texts", []))))
                report_scroll_action_ms_total += scroll_ms
                report_scroll_wait_ms_total += wait_ms
                report_node, report_entry_text = _find_s11_official_report_entry_node(report_snapshot)
                if report_node is not None:
                    s11_report_entry_click_source_override = "xml_after_stale_recovery"
                    s11_stale_xml_recovery_evidence = {
                        **fresh_pair,
                        "s11_xml_stale_warning": True,
                        "s11_stale_xml_redump_attempted": True,
                        "s11_stale_xml_nudge_attempted": True,
                        "s11_report_entry_seen_after_stale_nudge": True,
                        "s11_report_entry_click_mode": "xml_after_stale_recovery",
                        "s11_report_entry_click_source": "xml_after_stale_recovery",
                    }
                continue
            dynamic_click_target = _s11_report_entry_xml_bounds_click_target(
                report_snapshot,
                visibility=report_visibility,
                click_source="xml_exact_text_bounds",
                fresh_pair=fresh_pair,
                recovery=True,
            )
            if dynamic_click_target.get("ok"):
                report_node = None
                report_entry_text = S11_REPORT_ENTRY_TEXT
                s11_report_entry_click_source_override = ""
                s11_stale_xml_recovery_evidence = {
                    **fresh_pair,
                    "s11_xml_stale_warning": True,
                    "s11_stale_xml_redump_attempted": s11_stale_xml_redump_attempted,
                    "s11_stale_xml_nudge_attempted": s11_stale_xml_nudge_attempted,
                    "s11_report_entry_seen_after_stale_recovery": False,
                    "s11_report_entry_click_mode": "xml_only_stop",
                    "s11_report_entry_click_source": "",
                    "report_entry_detection_strategy": dynamic_click_target.get("report_entry_detection_strategy"),
                    "detected_button_rect": dynamic_click_target.get("detected_button_rect"),
                    "detected_text": dynamic_click_target.get("detected_text"),
                    "detection_source": dynamic_click_target.get("detection_source"),
                    "confidence": dynamic_click_target.get("confidence"),
                    "click_point_inside_detected_rect": dynamic_click_target.get("click_point_inside_detected_rect"),
                }
                timing.add(
                    step_name="S11_STALE_XML_DYNAMIC_VISUAL_BUTTON_BOUND",
                    page_name="S11",
                    action_name="bind_view_full_report_from_current_screenshot_dynamic_rect_after_stale_xml_recovery",
                    contract_check_ms=0,
                    field_read_ms=0,
                    action_ms=0,
                    transition_wait_ms=0,
                    screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
                    xml_path=str(report_snapshot.get("xml_path") or ""),
                    extra={
                        **s11_stale_xml_recovery_evidence,
                        "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_DYNAMIC_VISUAL_BINDING,
                        "reason_category": "S11_DYNAMIC_VISUAL_BUTTON_RECT_BINDING",
                        "reason_detail": "XML remained stale after bounded recovery, but current screenshot detector returned a bindable exact view-full-report button rect.",
                    },
                )
                break
            issue = issues.record(
                "S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET",
                "S11",
                "S11 report-entry is visible in context, but no current XML/accessibility bounds can be bound after stale XML recovery.",
                {
                    **fresh_pair,
                    **s11_stale_xml_recovery_evidence,
                    **dynamic_click_target,
                    "s11_stale_xml_nudge_attempted": s11_stale_xml_nudge_attempted,
                    "dynamic_visual_binding_attempted": dynamic_click_target.get("dynamic_visual_binding_attempted"),
                    "dynamic_visual_binding_reason": dynamic_click_target.get("dynamic_visual_binding_reason") or dynamic_click_target.get("reason"),
                    "rejected_dynamic_report_entry_regions": dynamic_click_target.get("rejected_dynamic_report_entry_regions"),
                    "internal_error_code": "S11_REPORT_ENTRY_XML_STALE_RECOVERY_FAILED",
                    "click_attempted": False,
                    "click_source": dynamic_click_target.get("click_source") or "",
                    **report_snapshot,
                },
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        page_recognized_for_report_search = _recognize_mainline_page(recognizer, report_snapshot)
        internal_region_check = _s11_internal_visible_region_check(
            report_snapshot,
            action_context="S11_REPORT_SEARCH",
            report_scroll_count=report_scroll_count,
            report_reposition_count=report_reposition_count,
            page_recognized=page_recognized_for_report_search,
        )
        timing.add(
            step_name="S11_INTERNAL_VISIBLE_REGION_PAIR_CHECK",
            page_name="S11",
            action_name="validate_s11_screenshot_xml_visible_region_consistency",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
            xml_path=str(report_snapshot.get("xml_path") or ""),
            extra={
                **fresh_pair,
                **internal_region_check,
                "s11_internal_mismatch_redump_attempted": s11_internal_mismatch_redump_attempted,
                "s11_report_scroll_count": report_scroll_count,
                "s11_report_reposition_scroll_count": report_reposition_count,
                "recognized_page": page_recognized_for_report_search,
                "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_V1_39,
            },
        )
        if internal_region_check.get("s11_internal_visible_region_mismatch"):
            if not s11_internal_mismatch_redump_attempted:
                s11_internal_mismatch_redump_attempted = True
                report_snapshot = _capture_with_global_popup_guard(
                    context,
                    f"s11_report_entry_internal_mismatch_redump_{report_search_iterations}",
                    current_stage="S11_REPORT_SEARCH",
                )
                timing.add(
                    step_name="S11_INTERNAL_VISIBLE_REGION_MISMATCH_REDUMP_ONCE",
                    page_name="S11",
                    action_name="redump_xml_once_after_s11_visible_region_mismatch",
                    contract_check_ms=0,
                    field_read_ms=0,
                    action_ms=0,
                    transition_wait_ms=0,
                    screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
                    xml_path=str(report_snapshot.get("xml_path") or ""),
                    extra={
                        **fresh_pair,
                        **internal_region_check,
                        "s11_internal_mismatch_redump_attempted": True,
                        "s11_internal_mismatch_redump_result": "redump_requested",
                    },
                )
                continue
            issue = issues.record(
                "S11_INTERNAL_XML_SCREENSHOT_VISIBLE_REGION_MISMATCH",
                "S11",
                "S11 report-entry screenshot/XML visible regions are inconsistent; XML lacks report-context nodes and must not be used to prove the report entry is absent.",
                {
                    **fresh_pair,
                    **internal_region_check,
                    **report_snapshot,
                    "s11_internal_mismatch_redump_attempted": True,
                    "s11_internal_mismatch_redump_result": "still_mismatched",
                },
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        report_node, report_entry_text = _find_s11_official_report_entry_node(report_snapshot)
        report_visibility = _s11_report_entry_visibility(report_snapshot, report_node)
        weak_marker_evidence = {"s11_report_weak_marker_seen": False, "s11_report_weak_marker_hits": [], "s11_report_weak_marker_strategy_disabled_by_v1_39": True}
        current_report_weak_marker_seen = False
        timing.add(
            step_name="S11_REPORT_ENTRY_VISIBILITY_CHECK",
            page_name="S11",
            action_name="check_exact_report_entry_full_visibility",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
            xml_path=str(report_snapshot.get("xml_path") or ""),
            extra={
                "exact_report_entry_seen": report_node is not None,
                "official_report_entry_text": report_entry_text,
                "exact_report_entry_fully_visible": report_visibility.get("exact_report_entry_fully_visible"),
                "exact_report_entry_in_safe_click_region": report_visibility.get("exact_report_entry_in_safe_click_region"),
                "report_entry_bounds": report_visibility.get("report_entry_bounds"),
                "screen_height": report_visibility.get("screen_height"),
                "safe_bottom_y": report_visibility.get("safe_bottom_y"),
                "bottom_bar_detected": report_visibility.get("bottom_bar_detected"),
                "bottom_bar_bounds": report_visibility.get("bottom_bar_bounds"),
                "visibility_reason": report_visibility.get("reason"),
                "s11_report_scroll_count": report_scroll_count,
                "s11_report_reposition_scroll_count": report_reposition_count,
                **weak_marker_evidence,
                **fresh_pair,
                "s11_report_search_scroll_mode": "first" if not report_first_scroll_done and report_scroll_count == 0 else "small",
                "s11_report_entry_backtrack_attempted": report_backtrack_attempted,
                "s11_report_entry_overshoot_suspected": report_overshoot_suspected,
                "s11_report_search_iterations": report_search_iterations,
                "recognized_page": _recognize_mainline_page(recognizer, report_snapshot),
                "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_V1_39,
                "s11_report_entry_scroll_step_ratio": S11_FIRST_SCROLL_SCREEN_RATIO if not report_first_scroll_done and report_scroll_count == 0 else 0.18,
                "s11_first_scroll_strategy": S11_FIRST_SCROLL_STRATEGY if not report_first_scroll_done and report_scroll_count == 0 else "",
                "s11_first_scroll_screen_ratio": S11_FIRST_SCROLL_SCREEN_RATIO if not report_first_scroll_done and report_scroll_count == 0 else None,
                "reason_category": "S11_REPORT_ENTRY_EXACT_TEXT_GATE",
                "reason_detail": "search exact XML text before each half-screen scroll",
                "solution": "if exact 鏌ョ湅瀹屾暣鎶ュ憡 is seen, stop scrolling immediately and click; no OCR, no visual binding, no local structure binding",
            },
        )
        if report_node is not None:
            unsafe_reposition_reason = _s11_report_entry_unsafe_reposition_reason(report_visibility)
            if not unsafe_reposition_reason:
                last_scroll_step_evidence["view_full_report_found_after_scroll_attempt"] = report_scroll_count
                last_scroll_step_evidence["stop_scroll_reason"] = "VIEW_FULL_REPORT_FULLY_VISIBLE"
                break
            bottom_blocked = bool(
                report_visibility.get("overlapped_bottom_bar")
                or report_visibility.get("below_safe_bottom")
                or report_visibility.get("too_close_to_bottom")
            )
            timing.add(
                step_name="S11_REPORT_ENTRY_VISIBLE_BUT_UNSAFE_REPOSITION",
                page_name="S11",
                action_name="reposition_bottom_blocked_view_full_report_candidate",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=0,
                transition_wait_ms=0,
                screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
                xml_path=str(report_snapshot.get("xml_path") or ""),
                extra={
                    "view_full_report_exact_text_seen": True,
                    "view_full_report_candidate_bounds": report_visibility.get("report_entry_bounds"),
                    "view_full_report_candidate_full_visible": report_visibility.get("exact_report_entry_fully_visible"),
                    "view_full_report_bottom_bar_overlap": report_visibility.get("overlapped_bottom_bar"),
                    "view_full_report_candidate_safe_clickable": report_visibility.get("exact_report_entry_in_safe_click_region"),
                    "view_full_report_candidate_seen_but_not_full_visible": True,
                    "s11_report_exact_entry_seen": True,
                    "s11_report_exact_entry_unsafe_reason": unsafe_reposition_reason,
                    "s11_report_bottom_bar_blocked": bottom_blocked,
                    "s11_report_entry_reposition_attempted": True,
                    "s11_report_entry_before_bounds": report_visibility.get("report_entry_bounds"),
                    "scroll_continue_reason": "VISIBLE_BUT_UNSAFE_REPOSITION",
                    "s11_report_entry_scroll_step_ratio": report_visibility.get("s11_report_entry_scroll_step_ratio") or 0.5,
                    "s11_report_entry_reposition_required": True,
                },
            )
            if unsafe_reposition_reason:
                if report_reposition_count >= max_reposition_scrolls:
                    issue = issues.record(
                        "S11_REPORT_ENTRY_NOT_FULLY_VISIBLE_SAFE",
                        "S11",
                        "鏌ョ湅瀹屾暣鎶ュ憡 was found in XML but stayed blocked by the bottom action bar after controlled reposition attempts.",
                        {
                            "s11_report_scroll_count": report_scroll_count,
                            "s11_report_reposition_scroll_count": report_reposition_count,
                            "max_reposition_scrolls": max_reposition_scrolls,
                            "report_visibility": report_visibility,
                            **report_snapshot,
                        },
                        "manual_review",
                    )
                    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
                machine.assert_action_allowed("S11", "scroll_to_report")
                before_reposition_bounds = report_visibility.get("report_entry_bounds")
                report_snapshot, scroll_ms, wait_ms, report_visibility = _execute_s11_report_scroll(
                    context,
                    report_snapshot,
                    step_name="S11_REPORT_ENTRY_BOTTOM_REPOSITION_SCROLL",
                    action_name="small_reposition_scroll_to_clear_bottom_bar",
                    attempt_index=report_reposition_count + 1,
                    small_reposition=True,
                    visibility=report_visibility,
                    search_scroll_mode="small",
                )
                last_scroll_step_evidence = {
                    "s11_report_search_scroll_mode": "small",
                    "s11_report_entry_backtrack_attempted": report_backtrack_attempted,
                    "s11_report_entry_overshoot_suspected": report_overshoot_suspected,
                    "s11_report_search_iterations": report_search_iterations,
                    "s11_report_entry_scroll_step_ratio": report_visibility.get("s11_report_entry_scroll_step_ratio"),
                    "s11_report_entry_scroll_distance_px": report_visibility.get("s11_report_entry_scroll_distance_px"),
                    "s11_report_scroll_step_px": report_visibility.get("s11_report_scroll_step_px"),
                    "view_full_report_found_after_scroll_attempt": None,
                    "stop_scroll_reason": "VIEW_FULL_REPORT_SAFE_AFTER_REPOSITION" if not _s11_report_entry_unsafe_reposition_reason(report_visibility) else "CONTINUE_UNSAFE_REPOSITION_SCROLL",
                    "s11_report_entry_reposition_scroll": True,
                    "s11_report_entry_reposition_attempted": True,
                    "s11_report_entry_before_bounds": before_reposition_bounds,
                    "s11_report_entry_after_bounds": report_visibility.get("report_entry_bounds"),
                    "s11_report_entry_safe_after_reposition": not _s11_report_entry_unsafe_reposition_reason(report_visibility),
                }
                report_search_signatures.append(_sha256_text("|".join(str(item) for item in report_snapshot.get("visible_texts", []))))
                report_reposition_count += 1
                report_scroll_action_ms_total += scroll_ms
                report_scroll_wait_ms_total += wait_ms
                continue
            last_scroll_step_evidence["view_full_report_found_after_scroll_attempt"] = report_scroll_count
            last_scroll_step_evidence["stop_scroll_reason"] = "VIEW_FULL_REPORT_FOUND_NO_BOTTOM_OVERLAP"
            break
        if report_node is None:
            timing.add(
                step_name="S11_REPORT_ENTRY_EXACT_TEXT_ONLY_GATE",
                page_name="S11",
                action_name="confirm_no_local_structure_binding_when_xml_text_missing",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=0,
                transition_wait_ms=0,
                screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
                xml_path=str(report_snapshot.get("xml_path") or ""),
                extra={
                    "recognized_page": _recognize_mainline_page(recognizer, report_snapshot),
                    "view_full_report_exact_text_seen": False,
                    **weak_marker_evidence,
                    **fresh_pair,
                    "s11_report_search_scroll_mode": "first" if not report_first_scroll_done and report_scroll_count == 0 else "small",
                    "s11_report_entry_backtrack_attempted": report_backtrack_attempted,
                    "s11_report_entry_overshoot_suspected": report_overshoot_suspected,
                    "s11_report_search_iterations": report_search_iterations,
                    "local_structure_binding_attempted": False,
                    "local_structure_binding_enabled": False,
                    "local_structure_binding_safe": False,
                    "local_structure_binding_disabled": True,
                    "visual_binding_disabled": True,
                    "ocr_disabled": True,
                    "screenshot_text_recognition_disabled": True,
                    "reason_category": "S11_EXACT_TEXT_ONLY_REPORT_ENTRY_GATE",
                    "reason_detail": "XML does not expose exact 鏌ョ湅瀹屾暣鎶ュ憡; V1.27 forbids OCR, visual binding, and local structure binding.",
                    "solution": "check exact 鍟嗗鑷杞﹀喌 marker or continue controlled half-screen scroll",
                },
            )
            merchant_marker_seen = _s11_merchant_self_check_marker_seen(report_snapshot)
            timing.add(
                step_name="S11_REPORT_ENTRY_EXACT_TEXT_ONLY_MARKER_CHECK",
                page_name="S11",
                action_name="check_merchant_self_check_marker_when_view_full_report_missing",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=0,
                transition_wait_ms=0,
                screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
                xml_path=str(report_snapshot.get("xml_path") or ""),
                extra={
                    "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_V1_39,
                    "local_structure_binding_attempted": False,
                    "local_structure_binding_safe": False,
                    "local_structure_binding_reason": "disabled_by_v1_27_exact_text_contract",
                    "view_full_report_exact_text_seen": False,
                    "view_full_report_text": S11_REPORT_ENTRY_TEXT,
                    "merchant_self_check_marker_seen": merchant_marker_seen,
                    **weak_marker_evidence,
                    **fresh_pair,
                    "s11_report_search_scroll_mode": "first" if not report_first_scroll_done and report_scroll_count == 0 else "small",
                    "s11_report_entry_backtrack_attempted": report_backtrack_attempted,
                    "s11_report_entry_overshoot_suspected": report_overshoot_suspected,
                    "s11_report_search_iterations": report_search_iterations,
                    "merchant_self_check_marker_text": S11_MERCHANT_SELF_CHECK_MARKER_TEXT,
                    "official_report_entry_texts_considered": list(S11_OFFICIAL_REPORT_ENTRY_TEXTS),
                    "visual_binding_disabled": True,
                    "ocr_disabled": True,
                    "screenshot_text_recognition_disabled": True,
                    "s11_report_scroll_count": report_scroll_count,
                    "s11_report_reposition_scroll_count": report_reposition_count,
                    "recognized_page": _recognize_mainline_page(recognizer, report_snapshot),
                    "reason_category": "S11_EXACT_TEXT_ONLY_REPORT_ENTRY_GATE",
                    "reason_detail": "XML did not expose the exact 鏌ョ湅瀹屾暣鎶ュ憡 entry; only exact 鍟嗗鑷杞﹀喌 can confirm report absence.",
                    "solution": "exclude only on exact merchant self-check marker, otherwise continue controlled scroll or stop evidence-insufficient.",
                },
            )
            if merchant_marker_seen:
                missing_evidence = _s11_report_missing_evidence(
                    report_snapshot,
                    report_scroll_count=report_scroll_count,
                    report_reposition_count=report_reposition_count,
                    max_search_scrolls=max_search_scrolls,
                    visibility=report_visibility,
                    previous_signatures=report_search_signatures,
                    local_structure_evidence=None,
                )
                missing_evidence["s11_report_entry_search_ms"] = int((time.perf_counter() - entry_search_start) * 1000)
                missing_evidence["early_exit_reason"] = "merchant_self_check_marker_seen_no_view_full_report"
                return _exclude_current_reference_for_missing_official_report(context, report_snapshot, missing_evidence)
            # V1.38 XML stabilization was intentionally removed from the
            # runtime mainline. Keep report-entry discovery on the lean
            # XML-driven path: first 1/3 scroll, fixed small scroll, and
            # unsafe-entry reposition only.
        if False and report_node is not None:
            timing.add(
                step_name="S11_REPORT_ENTRY_SEEN_BUT_NOT_FULLY_VISIBLE",
                page_name="S11",
                action_name="report_entry_seen_but_unsafe_to_click",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=0,
                transition_wait_ms=0,
                screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
                xml_path=str(report_snapshot.get("xml_path") or ""),
                extra={
                    "report_entry_bounds": report_visibility.get("report_entry_bounds"),
                    "screen_height": report_visibility.get("screen_height"),
                    "safe_bottom_y": report_visibility.get("safe_bottom_y"),
                    "bottom_bar_detected": report_visibility.get("bottom_bar_detected"),
                    "bottom_bar_bounds": report_visibility.get("bottom_bar_bounds"),
                    "reason": report_visibility.get("reason"),
                    "exact_report_entry_fully_visible": report_visibility.get("exact_report_entry_fully_visible"),
                    "exact_report_entry_in_safe_click_region": report_visibility.get("exact_report_entry_in_safe_click_region"),
                    "reason_category": "S11_REPORT_ENTRY_CLICKED_BEFORE_FULLY_VISIBLE_SAFE_POSITION",
                    "reason_detail": "exact 鏌ョ湅瀹屾暣鎶ュ憡 is visible in XML but not fully inside the safe clickable viewport",
                    "solution": "small controlled upward scroll, fresh, then re-check bounds before clicking",
                },
            )
            if report_reposition_count >= max_reposition_scrolls:
                issue = issues.record(
                    "S11_REPORT_ENTRY_FULL_VISIBILITY_NOT_ACHIEVED",
                    "S11",
                    "鏌ョ湅瀹屾暣鎶ュ憡 was seen but never reached a fully visible safe click region.",
                    {
                        "s11_report_scroll_count": report_scroll_count,
                        "s11_report_reposition_scroll_count": report_reposition_count,
                        "report_visibility": report_visibility,
                        **report_snapshot,
                    },
                    "manual_review",
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            machine.assert_action_allowed("S11", "scroll_to_report")
            report_snapshot, scroll_ms, wait_ms, report_visibility = _execute_s11_report_scroll(
                context,
                report_snapshot,
                step_name="S11_REPORT_ENTRY_REPOSITION_SCROLL",
                action_name="small_controlled_scroll_to_safe_report_entry_position",
                attempt_index=report_reposition_count + 1,
                small_reposition=True,
                visibility=report_visibility,
            )
            report_search_signatures.append(_sha256_text("|".join(str(item) for item in report_snapshot.get("visible_texts", []))))
            report_reposition_count += 1
            report_scroll_action_ms_total += scroll_ms
            report_scroll_wait_ms_total += wait_ms
            continue
        if report_scroll_count >= max_search_scrolls:
            missing_evidence = _s11_report_missing_evidence(
                report_snapshot,
                report_scroll_count=report_scroll_count,
                report_reposition_count=report_reposition_count,
                max_search_scrolls=max_search_scrolls,
                visibility=report_visibility,
                previous_signatures=report_search_signatures,
                local_structure_evidence=None,
            )
            missing_evidence["s11_report_entry_search_ms"] = int((time.perf_counter() - entry_search_start) * 1000)
            if missing_evidence.get("merchant_self_check_marker_seen"):
                return _exclude_current_reference_for_missing_official_report(context, report_snapshot, missing_evidence)
            _stop_s11_report_entry_search_exhausted_without_decisive_marker(context, report_snapshot, missing_evidence)
        machine.assert_action_allowed("S11", "scroll_to_report")
        current_scroll_mode = (
            "first"
            if not report_first_scroll_done and report_scroll_count == 0
            else "small"
        )
        report_snapshot, scroll_ms, wait_ms, report_visibility = _execute_s11_report_scroll(
            context,
            report_snapshot,
            step_name="S11_REPORT_SCROLL",
            action_name=(
                "first_two_thirds_trial_scroll_to_report_entry"
                if current_scroll_mode == "first"
                else "small_scroll_to_report_entry"
            ),
            attempt_index=report_scroll_count + 1,
            small_reposition=False,
            visibility=report_visibility,
            search_scroll_mode=current_scroll_mode,
        )
        if current_scroll_mode == "first":
            report_first_scroll_done = True
        last_scroll_step_evidence = {
            "s11_report_search_scroll_mode": current_scroll_mode,
            "s11_report_entry_backtrack_attempted": report_backtrack_attempted,
            "s11_report_entry_overshoot_suspected": report_overshoot_suspected,
            "s11_first_scroll_done": report_first_scroll_done,
            "s11_first_scroll_step_px": report_visibility.get("s11_report_scroll_step_px") if current_scroll_mode == "first" else last_scroll_step_evidence.get("s11_first_scroll_step_px"),
            "s11_report_search_iterations": report_search_iterations,
            "s11_report_entry_scroll_step_ratio": report_visibility.get("s11_report_entry_scroll_step_ratio"),
            "s11_report_entry_scroll_distance_px": report_visibility.get("s11_report_entry_scroll_distance_px"),
            "s11_report_scroll_step_px": report_visibility.get("s11_report_scroll_step_px"),
            "s11_first_scroll_strategy": report_visibility.get("s11_first_scroll_strategy") if current_scroll_mode == "first" else last_scroll_step_evidence.get("s11_first_scroll_strategy"),
            "s11_first_scroll_screen_ratio": report_visibility.get("s11_first_scroll_screen_ratio") if current_scroll_mode == "first" else last_scroll_step_evidence.get("s11_first_scroll_screen_ratio"),
            "s11_first_scroll_requested_distance_px": report_visibility.get("s11_first_scroll_requested_distance_px") if current_scroll_mode == "first" else last_scroll_step_evidence.get("s11_first_scroll_requested_distance_px"),
            "view_full_report_found_after_scroll_attempt": None,
            "stop_scroll_reason": (
                "CONTINUE_FIRST_TWO_THIRDS_TRIAL_SCROLL"
                if current_scroll_mode == "first"
                else "CONTINUE_SMALL_SCROLL"
            ),
        }
        report_search_signatures.append(_sha256_text("|".join(str(item) for item in report_snapshot.get("visible_texts", []))))
        report_scroll_count += 1
        report_scroll_action_ms_total += scroll_ms
        report_scroll_wait_ms_total += wait_ms
        previous_report_weak_marker_seen = False
        if _recent_page_signature_unchanged(report_search_signatures) and (not report_weak_marker_seen_ever or report_backtrack_attempted):
            current_report_node, _current_report_text = _find_s11_official_report_entry_node(report_snapshot)
            if current_report_node is None:
                missing_evidence = _s11_report_missing_evidence(
                    report_snapshot,
                    report_scroll_count=report_scroll_count,
                    report_reposition_count=report_reposition_count,
                    max_search_scrolls=max_search_scrolls,
                    visibility=report_visibility,
                    previous_signatures=report_search_signatures,
                    local_structure_evidence=None,
                )
                missing_evidence["s11_report_entry_search_ms"] = int((time.perf_counter() - entry_search_start) * 1000)
                missing_evidence["early_exit_reason"] = "page_signature_unchanged_after_controlled_scroll"
                missing_evidence["optimized"] = True
                missing_evidence["optimization_type"] = "s11_report_search_page_signature_no_longer_changes"
                if missing_evidence.get("merchant_self_check_marker_seen"):
                    return _exclude_current_reference_for_missing_official_report(context, report_snapshot, missing_evidence)
                _stop_s11_report_entry_search_exhausted_without_decisive_marker(context, report_snapshot, missing_evidence)
    using_local_structure_binding = False
    using_stale_recovered_xml_binding = s11_report_entry_click_source_override is not None
    report_bounds = report_node.get("bounds") if report_node else None
    if report_node is not None:
        click_target = _find_s11_report_click_target(report_snapshot, report_node, report_visibility)
    else:
        click_target = _s11_report_entry_xml_bounds_click_target(
            report_snapshot,
            visibility=report_visibility,
            click_source=s11_report_entry_click_source_override or "xml_exact_text_bounds",
            fresh_pair=last_fresh_pair_evidence,
            recovery=using_stale_recovered_xml_binding,
        )
    if click_target.get("ok") and s11_report_entry_click_source_override:
        is_xml_after_stale_recovery = s11_report_entry_click_source_override == "xml_after_stale_recovery"
        is_dynamic_visual_binding = False
        click_target = {
            **click_target,
            "click_target_source": s11_report_entry_click_source_override,
            "s11_report_entry_click_source": s11_report_entry_click_source_override,
            "s11_report_entry_click_mode": "xml_after_stale_recovery"
            if is_xml_after_stale_recovery
            else "xml_node_click",
            "report_entry_detection_strategy": "XML_TEXT_AFTER_STALE_RECOVERY"
            if is_xml_after_stale_recovery
            else "XML_TEXT_BINDING",
            "s11_report_entry_rule_version": S11_REPORT_ENTRY_RULE_VERSION_NO_FIXED_COORDINATE,
            "s11_xml_stale_warning": True,
        }
    timing.add(
        step_name="S11_REPORT_SEARCH",
        page_name="S11",
        action_name="entry_snapshot_report_node_search" if report_found_in_entry_snapshot else "scroll_then_report_node_search",
        contract_check_ms=int((read_start - start) * 1000),
        field_read_ms=field_ms + entry_search_ms,
        action_ms=report_scroll_action_ms_total,
        transition_wait_ms=report_scroll_wait_ms_total,
        screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
        xml_path=str(report_snapshot.get("xml_path") or ""),
        extra={
            "s11_report_entry_found_in_entry_snapshot": report_found_in_entry_snapshot,
            "s11_report_entry_node_bounds": report_bounds,
            "view_full_report_seen_in_xml": report_node is not None,
            "view_full_report_exact_text_seen": report_node is not None,
            "s11_xml_stale_warning": click_target.get("s11_xml_stale_warning") or last_fresh_pair_evidence.get("s11_xml_stale_warning"),
            "s11_report_entry_click_mode": click_target.get("s11_report_entry_click_mode") or "xml_node_click",
            "s11_report_entry_click_source": click_target.get("s11_report_entry_click_source") or click_target.get("click_target_source"),
            "contract_action_plan": click_target.get("contract_action_plan"),
            "contract_action_plan_used": click_target.get("contract_action_plan_used"),
            "action_algorithm_used": click_target.get("action_algorithm_used"),
            "action_inputs_source": click_target.get("action_inputs_source"),
            "action_outputs_source": click_target.get("action_outputs_source"),
            "allowed_binding_sources": click_target.get("allowed_binding_sources"),
            "binding_source": click_target.get("binding_source"),
            "forbidden_action_used": click_target.get("forbidden_action_used"),
            "runtime_bypassed_action_plan": click_target.get("runtime_bypassed_action_plan"),
            "action_plan_binding_check_passed": click_target.get("action_plan_binding_check_passed"),
            "click_attempted": click_target.get("click_attempted"),
            "evidence_pairing_ok": click_target.get("evidence_pairing_ok") if using_local_structure_binding else None,
            "xml_screenshot_same_fresh": click_target.get("xml_screenshot_same_fresh") if using_local_structure_binding else None,
            "xml_screenshot_pair_mismatch_reason": click_target.get("xml_screenshot_pair_mismatch_reason") if using_local_structure_binding else "",
            "local_structure_binding_attempted": click_target.get("local_structure_binding_attempted") if using_local_structure_binding else False,
            "local_structure_binding_enabled": click_target.get("local_structure_binding_enabled") if using_local_structure_binding else False,
            "engine_condition_block_seen": click_target.get("engine_condition_block_seen") if using_local_structure_binding else False,
            "left_candidate_bounds": click_target.get("left_candidate_bounds") if using_local_structure_binding else None,
            "right_advisor_candidate_bounds": click_target.get("right_advisor_candidate_bounds") if using_local_structure_binding else None,
            "local_structure_binding_safe": click_target.get("local_structure_binding_safe") if using_local_structure_binding else False,
            "local_structure_binding_reason": click_target.get("local_structure_binding_reason") if using_local_structure_binding else "",
            "view_full_report_text": S11_REPORT_ENTRY_TEXT,
            "official_report_entry_texts_considered": list(S11_OFFICIAL_REPORT_ENTRY_TEXTS),
            "merchant_self_check_marker_seen": _s11_merchant_self_check_marker_seen(report_snapshot),
            "merchant_self_check_marker_text": S11_MERCHANT_SELF_CHECK_MARKER_TEXT,
            "xml_text_missing": report_node is None,
            "visual_binding_disabled": not bool(click_target.get("dynamic_visual_binding_attempted")),
            "dynamic_visual_binding_attempted": click_target.get("dynamic_visual_binding_attempted"),
            "detection_source": click_target.get("detection_source"),
            "detected_text": click_target.get("detected_text"),
            "detected_button_rect": click_target.get("detected_button_rect"),
            "confidence": click_target.get("confidence"),
            "click_point": click_target.get("click_point"),
            "click_point_inside_detected_rect": click_target.get("click_point_inside_detected_rect"),
            "rejected_dynamic_report_entry_regions": click_target.get("rejected_dynamic_report_entry_regions"),
            "ocr_disabled": True,
            "screenshot_text_recognition_disabled": not bool(click_target.get("dynamic_visual_binding_attempted")),
            "report_entry_detection_strategy": click_target.get("report_entry_detection_strategy") or "XML_TEXT_BINDING",
            "exact_report_entry_fully_visible": report_visibility.get("exact_report_entry_fully_visible"),
            "exact_report_entry_in_safe_click_region": report_visibility.get("exact_report_entry_in_safe_click_region"),
            "report_entry_bounds": report_visibility.get("report_entry_bounds"),
            "safe_bottom_y": report_visibility.get("safe_bottom_y"),
            "bottom_bar_detected": report_visibility.get("bottom_bar_detected"),
            "bottom_bar_bounds": report_visibility.get("bottom_bar_bounds"),
            "s11_report_entry_click_strategy": click_target.get("click_strategy") if click_target.get("ok") else None,
            "s11_report_scroll_count": report_scroll_count,
            "s11_report_reposition_scroll_count": report_reposition_count,
            **_s11_report_weak_marker_evidence(report_snapshot),
            "s11_report_search_scroll_mode": last_scroll_step_evidence.get("s11_report_search_scroll_mode"),
            "s11_report_entry_backtrack_attempted": report_backtrack_attempted,
            "s11_report_entry_overshoot_suspected": report_overshoot_suspected,
            "s11_report_xml_stabilization_enabled": report_xml_stabilization_enabled,
            "s11_report_xml_stabilization_attempted": report_xml_stabilization_attempted,
            "s11_report_xml_stabilization_count": report_xml_stabilization_count,
            "s11_report_xml_stabilization_reason": "",
            "s11_report_xml_redump_after_wait": False,
            "s11_report_xml_micro_scroll_attempted": False,
            "s11_report_xml_micro_scroll_px": None,
            "s11_report_exact_entry_seen_after_stabilization": None,
            "s11_report_entry_xml_missing_after_stabilization": None,
            "s11_report_entry_stabilization_final_status": "disabled_by_v1_39",
            "s11_report_scroll_step_px": last_scroll_step_evidence.get("s11_report_scroll_step_px"),
            "s11_first_scroll_done": report_first_scroll_done,
            "s11_first_scroll_step_px": last_scroll_step_evidence.get("s11_first_scroll_step_px"),
            "s11_report_search_iterations": report_search_iterations,
            "s11_report_xml_parse_count_per_round": 1,
            "reason_category": "S11_XML_STALE_RECOVERY_XML_BOUNDS" if using_stale_recovered_xml_binding else ("RUNTIME_REDUNDANT_NODE_SEARCH" if report_found_in_entry_snapshot else "WEBVIEW_TEXT_DELAY"),
            "reason_detail": "stale XML recovered to a bindable report-entry target; click only the current XML/accessibility or dynamic screenshot target" if using_stale_recovered_xml_binding else "report entry search stops only after the exact node is fully visible and safely clickable",
            "solution": "verify the click by entering S12/S13 and stop precisely if the report does not open" if using_stale_recovered_xml_binding else "continue scrolling/repositioning when XML exposes 鏌ョ湅瀹屾暣鎶ュ憡 at the unsafe bottom edge",
        },
    )
    context["current_reference"]["s11_report_entry_search_ms"] = int((time.perf_counter() - entry_search_start) * 1000)
    context["current_reference"]["official_report_entry_seen"] = True
    context["current_reference"]["official_report_entry_text"] = click_target.get("clicked_text") if click_target.get("ok") else report_entry_text
    context["current_reference"]["view_full_report_seen_in_xml"] = report_node is not None
    context["current_reference"]["s11_report_entry_rule_version"] = (
        S11_REPORT_ENTRY_RULE_VERSION_NO_FIXED_COORDINATE if using_stale_recovered_xml_binding else S11_REPORT_ENTRY_RULE_VERSION_V1_27
    )
    context["current_reference"]["view_full_report_exact_text_seen"] = report_node is not None
    context["current_reference"]["view_full_report_text"] = S11_REPORT_ENTRY_TEXT
    context["current_reference"]["merchant_self_check_marker_seen"] = _s11_merchant_self_check_marker_seen(report_snapshot)
    context["current_reference"]["merchant_self_check_marker_text"] = S11_MERCHANT_SELF_CHECK_MARKER_TEXT
    context["current_reference"]["official_report_entry_texts_considered"] = list(S11_OFFICIAL_REPORT_ENTRY_TEXTS)
    context["current_reference"]["visual_binding_disabled"] = not bool(click_target.get("dynamic_visual_binding_attempted"))
    context["current_reference"]["dynamic_visual_binding_attempted"] = click_target.get("dynamic_visual_binding_attempted")
    context["current_reference"]["detection_source"] = click_target.get("detection_source")
    context["current_reference"]["detected_text"] = click_target.get("detected_text")
    context["current_reference"]["detected_button_rect"] = click_target.get("detected_button_rect")
    context["current_reference"]["confidence"] = click_target.get("confidence")
    context["current_reference"]["click_point"] = click_target.get("click_point")
    context["current_reference"]["click_point_inside_detected_rect"] = click_target.get("click_point_inside_detected_rect")
    context["current_reference"]["rejected_dynamic_report_entry_regions"] = click_target.get("rejected_dynamic_report_entry_regions")
    context["current_reference"]["ocr_disabled"] = True
    context["current_reference"]["screenshot_text_recognition_disabled"] = not bool(click_target.get("dynamic_visual_binding_attempted"))
    context["current_reference"]["xml_text_missing"] = report_node is None
    context["current_reference"]["evidence_pairing_ok"] = click_target.get("evidence_pairing_ok") if using_local_structure_binding else None
    context["current_reference"]["xml_screenshot_same_fresh"] = click_target.get("xml_screenshot_same_fresh") if using_local_structure_binding else None
    context["current_reference"]["xml_screenshot_pair_mismatch_reason"] = click_target.get("xml_screenshot_pair_mismatch_reason") if using_local_structure_binding else ""
    context["current_reference"]["local_structure_binding_attempted"] = click_target.get("local_structure_binding_attempted") if using_local_structure_binding else False
    context["current_reference"]["local_structure_binding_enabled"] = click_target.get("local_structure_binding_enabled") if using_local_structure_binding else False
    context["current_reference"]["engine_condition_block_seen"] = click_target.get("engine_condition_block_seen") if using_local_structure_binding else False
    context["current_reference"]["left_candidate_bounds"] = click_target.get("left_candidate_bounds") if using_local_structure_binding else None
    context["current_reference"]["right_advisor_candidate_bounds"] = click_target.get("right_advisor_candidate_bounds") if using_local_structure_binding else None
    context["current_reference"]["local_structure_binding_safe"] = click_target.get("local_structure_binding_safe") if using_local_structure_binding else False
    context["current_reference"]["local_structure_binding_reason"] = click_target.get("local_structure_binding_reason") if using_local_structure_binding else ""
    context["current_reference"]["clicked_by_local_structure"] = using_local_structure_binding
    context["current_reference"]["report_entry_detection_strategy"] = click_target.get("report_entry_detection_strategy") or "XML_TEXT_BINDING"
    context["current_reference"]["s11_xml_stale_warning"] = bool(click_target.get("s11_xml_stale_warning") or last_fresh_pair_evidence.get("s11_xml_stale_warning"))
    context["current_reference"]["s11_report_entry_click_mode"] = click_target.get("s11_report_entry_click_mode") or "xml_node_click"
    context["current_reference"]["s11_report_entry_click_source"] = click_target.get("s11_report_entry_click_source") or click_target.get("click_target_source")
    context["current_reference"]["s11_report_entry_contract_action_plan"] = click_target.get("contract_action_plan")
    context["current_reference"]["s11_report_entry_contract_action_plan_used"] = click_target.get("contract_action_plan_used")
    context["current_reference"]["s11_report_entry_action_algorithm_used"] = click_target.get("action_algorithm_used")
    context["current_reference"]["s11_report_entry_action_inputs_source"] = click_target.get("action_inputs_source")
    context["current_reference"]["s11_report_entry_action_outputs_source"] = click_target.get("action_outputs_source")
    context["current_reference"]["s11_report_entry_allowed_binding_sources"] = click_target.get("allowed_binding_sources")
    context["current_reference"]["s11_report_entry_binding_source"] = click_target.get("binding_source")
    context["current_reference"]["s11_report_entry_forbidden_action_used"] = click_target.get("forbidden_action_used")
    context["current_reference"]["s11_report_entry_runtime_bypassed_action_plan"] = click_target.get("runtime_bypassed_action_plan")
    context["current_reference"]["s11_report_entry_action_plan_binding_check_passed"] = click_target.get("action_plan_binding_check_passed")
    context["current_reference"]["s11_report_entry_click_attempted"] = click_target.get("click_attempted")
    context["current_reference"]["s11_stale_xml_recovery_used"] = using_stale_recovered_xml_binding
    context["current_reference"]["report_entry_scroll_count"] = report_scroll_count
    context["current_reference"]["bottom_reposition_attempts"] = report_reposition_count
    context["current_reference"]["bottom_reposition_distance_px"] = report_visibility.get("s11_report_entry_scroll_distance_px")
    context["current_reference"]["s11_report_reposition_scroll_count"] = report_reposition_count
    context["current_reference"]["s11_report_entry_scroll_step_ratio"] = last_scroll_step_evidence.get("s11_report_entry_scroll_step_ratio")
    context["current_reference"]["s11_report_entry_scroll_distance_px"] = last_scroll_step_evidence.get("s11_report_entry_scroll_distance_px")
    context["current_reference"]["s11_report_search_scroll_mode"] = last_scroll_step_evidence.get("s11_report_search_scroll_mode")
    context["current_reference"]["s11_report_weak_marker_seen"] = _s11_report_weak_marker_evidence(report_snapshot).get("s11_report_weak_marker_seen")
    context["current_reference"]["s11_report_entry_backtrack_attempted"] = report_backtrack_attempted
    context["current_reference"]["s11_report_entry_overshoot_suspected"] = report_overshoot_suspected
    context["current_reference"]["s11_report_xml_stabilization_enabled"] = report_xml_stabilization_enabled
    context["current_reference"]["s11_report_xml_stabilization_attempted"] = report_xml_stabilization_attempted
    context["current_reference"]["s11_report_xml_stabilization_count"] = report_xml_stabilization_count
    context["current_reference"]["s11_report_xml_stabilization_reason"] = ""
    context["current_reference"]["s11_report_xml_redump_after_wait"] = False
    context["current_reference"]["s11_report_xml_micro_scroll_attempted"] = False
    context["current_reference"]["s11_report_xml_micro_scroll_px"] = None
    context["current_reference"]["s11_report_exact_entry_seen_after_stabilization"] = None
    context["current_reference"]["s11_report_entry_xml_missing_after_stabilization"] = None
    context["current_reference"]["s11_report_entry_stabilization_final_status"] = "disabled_by_v1_39"
    context["current_reference"]["s11_report_scroll_step_px"] = last_scroll_step_evidence.get("s11_report_scroll_step_px")
    context["current_reference"]["s11_first_scroll_done"] = report_first_scroll_done
    context["current_reference"]["s11_first_scroll_step_px"] = last_scroll_step_evidence.get("s11_first_scroll_step_px")
    context["current_reference"]["s11_first_scroll_strategy"] = last_scroll_step_evidence.get("s11_first_scroll_strategy")
    context["current_reference"]["s11_first_scroll_screen_ratio"] = last_scroll_step_evidence.get("s11_first_scroll_screen_ratio")
    context["current_reference"]["s11_first_scroll_requested_distance_px"] = last_scroll_step_evidence.get("s11_first_scroll_requested_distance_px")
    context["current_reference"]["s11_report_search_iterations"] = report_search_iterations
    context["current_reference"]["view_full_report_found_after_scroll_attempt"] = last_scroll_step_evidence.get("view_full_report_found_after_scroll_attempt")
    context["current_reference"]["stop_scroll_reason"] = last_scroll_step_evidence.get("stop_scroll_reason") or "VIEW_FULL_REPORT_FULLY_VISIBLE"
    context["current_reference"]["view_full_report_candidate_bounds"] = report_visibility.get("report_entry_bounds")
    context["current_reference"]["view_full_report_candidate_full_visible"] = report_visibility.get("exact_report_entry_fully_visible")
    context["current_reference"]["view_full_report_bottom_bar_overlap"] = report_visibility.get("overlapped_bottom_bar")
    context["current_reference"]["view_full_report_candidate_safe_clickable"] = report_visibility.get("exact_report_entry_in_safe_click_region")
    context["current_reference"]["view_full_report_candidate_seen_but_not_full_visible"] = False
    context["current_reference"]["s11_report_exact_entry_seen"] = report_node is not None
    context["current_reference"]["s11_report_exact_entry_unsafe_reason"] = _s11_report_entry_unsafe_reposition_reason(report_visibility)
    context["current_reference"]["s11_report_entry_reposition_attempted"] = bool(last_scroll_step_evidence.get("s11_report_entry_reposition_attempted") or report_reposition_count > 0)
    context["current_reference"]["s11_report_entry_reposition_count"] = report_reposition_count
    context["current_reference"]["s11_report_entry_before_bounds"] = last_scroll_step_evidence.get("s11_report_entry_before_bounds")
    context["current_reference"]["s11_report_entry_after_bounds"] = last_scroll_step_evidence.get("s11_report_entry_after_bounds") or report_visibility.get("report_entry_bounds")
    context["current_reference"]["s11_report_entry_safe_after_reposition"] = last_scroll_step_evidence.get("s11_report_entry_safe_after_reposition")
    context["current_reference"]["s11_report_bottom_bar_blocked"] = bool(
        report_visibility.get("overlapped_bottom_bar")
        or report_visibility.get("below_safe_bottom")
        or report_visibility.get("too_close_to_bottom")
    )
    context["current_reference"]["clicked_view_full_report"] = True
    context["current_reference"]["report_entry_search_reached_bottom"] = False
    if report_bounds is None and not click_target.get("ok") and not click_target.get("stop_code"):
        issue = issues.record(
            "S11_REPORT_ENTRY_CLICK_TARGET_NOT_FOUND",
            "S11",
            "鏌ョ湅瀹屾暣鎶ュ憡 did not reach a fully visible safe click region.",
            {
                "s11_report_scroll_count": report_scroll_count,
                "s11_report_reposition_scroll_count": report_reposition_count,
                "report_visibility": report_visibility,
                **report_snapshot,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if not click_target.get("ok"):
        issue = issues.record(
            str(click_target.get("stop_code") or "S11_REPORT_ENTRY_CLICK_TARGET_NOT_FOUND"),
            "S11",
            str(click_target.get("reason") or "No safe click target for 鏌ョ湅瀹屾暣鎶ュ憡."),
            {
                "click_target": click_target,
                "report_visibility": report_visibility,
                **report_snapshot,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    machine.assert_action_allowed("S11", "tap_full_report")
    before_click_xml_sha256 = _sha256_text(str(report_snapshot.get("fresh_xml") or ""))
    before_click_visible_digest = _sha256_text("|".join(str(item) for item in report_snapshot.get("visible_texts", [])))
    before_click_screenshot_sha256 = _sha256_file(report_snapshot.get("screenshot_path"))
    report_action_id = "tap_full_report"
    action_ms = contract_execute_click(
        context,
        report_snapshot,
        "S11",
        report_action_id,
        tuple(int(v) for v in click_target["clicked_point"]),
        evidence={
            "clicked_text": click_target.get("clicked_text"),
            "clicked_node_bounds": click_target.get("clicked_node_bounds"),
            "click_strategy": click_target.get("click_strategy"),
            "report_visibility": report_visibility,
            "recognized_page": "S11",
            "evidence_pairing_ok": click_target.get("evidence_pairing_ok") if using_local_structure_binding else None,
            "xml_screenshot_same_fresh": click_target.get("xml_screenshot_same_fresh") if using_local_structure_binding else None,
            "xml_screenshot_pair_mismatch_reason": click_target.get("xml_screenshot_pair_mismatch_reason") if using_local_structure_binding else "",
            "local_structure_binding_attempted": click_target.get("local_structure_binding_attempted") if using_local_structure_binding else False,
            "local_structure_binding_enabled": click_target.get("local_structure_binding_enabled") if using_local_structure_binding else False,
            "engine_condition_block_seen": click_target.get("engine_condition_block_seen") if using_local_structure_binding else False,
            "left_candidate_bounds": click_target.get("left_candidate_bounds") if using_local_structure_binding else None,
            "right_advisor_candidate_bounds": click_target.get("right_advisor_candidate_bounds") if using_local_structure_binding else None,
            "local_structure_binding_safe": click_target.get("local_structure_binding_safe") if using_local_structure_binding else False,
            "local_structure_binding_reason": click_target.get("local_structure_binding_reason") if using_local_structure_binding else "",
            "candidate_overlap": click_target.get("candidate_overlap") if using_local_structure_binding else False,
            "bottom_bar_overlap": click_target.get("bottom_bar_overlap") if using_local_structure_binding else False,
            "forbidden_button_overlap": click_target.get("forbidden_button_overlap") if using_local_structure_binding else False,
            "view_full_report_seen_in_xml": report_node is not None,
            "view_full_report_exact_text_seen": report_node is not None,
            "view_full_report_text": S11_REPORT_ENTRY_TEXT,
            "merchant_self_check_marker_seen": _s11_merchant_self_check_marker_seen(report_snapshot),
            "merchant_self_check_marker_text": S11_MERCHANT_SELF_CHECK_MARKER_TEXT,
            "official_report_entry_texts_considered": list(S11_OFFICIAL_REPORT_ENTRY_TEXTS),
            "xml_text_missing": report_node is None,
            "visual_binding_disabled": not bool(click_target.get("dynamic_visual_binding_attempted")),
            "dynamic_visual_binding_attempted": click_target.get("dynamic_visual_binding_attempted"),
            "detection_source": click_target.get("detection_source"),
            "detected_text": click_target.get("detected_text"),
            "detected_button_rect": click_target.get("detected_button_rect"),
            "confidence": click_target.get("confidence"),
            "click_point": click_target.get("click_point"),
            "click_point_inside_detected_rect": click_target.get("click_point_inside_detected_rect"),
            "ocr_disabled": True,
            "screenshot_text_recognition_disabled": not bool(click_target.get("dynamic_visual_binding_attempted")),
            "report_entry_detection_strategy": click_target.get("report_entry_detection_strategy") or "XML_TEXT_BINDING",
            "s11_xml_stale_warning": click_target.get("s11_xml_stale_warning") or last_fresh_pair_evidence.get("s11_xml_stale_warning"),
            "s11_report_entry_click_mode": click_target.get("s11_report_entry_click_mode") or "xml_node_click",
            "s11_report_entry_click_source": click_target.get("s11_report_entry_click_source") or click_target.get("click_target_source"),
        },
    )
    timing.add(
        step_name="EXACT_REPORT_ENTRY_FULLY_VISIBLE_AND_SAFE_CLICKED",
        page_name="S11",
        action_name="tap_safe_exact_view_full_report_target",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=0,
        screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
        xml_path=str(report_snapshot.get("xml_path") or ""),
        extra={
            "clicked_text": click_target.get("clicked_text"),
            "clicked_node_bounds": click_target.get("clicked_node_bounds"),
            "clicked_point": click_target.get("clicked_point"),
            "click_strategy": click_target.get("click_strategy"),
            "click_target_source": click_target.get("click_target_source"),
            "exact_report_entry_seen": report_node is not None,
            "exact_report_entry_fully_visible": True,
            "exact_report_entry_in_safe_click_region": True,
            "report_visibility": report_visibility,
            "s11_report_scroll_count": report_scroll_count,
            "s11_report_reposition_scroll_count": report_reposition_count,
            "reason_category": "S11_XML_STALE_RECOVERY_XML_BOUNDS" if using_stale_recovered_xml_binding else "NODE_DRIVEN_SAFE_CLICK",
            "reason_detail": "stale XML warning path clicked the recovered current XML/accessibility bounds" if using_stale_recovered_xml_binding else "exact 鏌ョ湅瀹屾暣鎶ュ憡 is fully visible and outside the bottom fixed action bar before clicking",
            "solution": "verify entry by S12/S13 transition, then stop precisely if the report does not open" if using_stale_recovered_xml_binding else "keep exact text matching and safe-region visibility gate",
        },
    )
    timing.add(
        step_name="S11_CLICK_VIEW_FULL_REPORT",
        page_name="S11",
        action_name="tap_exact_view_full_report_node",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=0,
        screenshot_path=str(report_snapshot.get("screenshot_path") or ""),
        xml_path=str(report_snapshot.get("xml_path") or ""),
        extra={
            "clicked_text": click_target.get("clicked_text"),
            "clicked_node_bounds": click_target.get("clicked_node_bounds"),
            "clicked_point": click_target.get("clicked_point"),
            "click_strategy": click_target.get("click_strategy"),
            "click_target_source": click_target.get("click_target_source"),
            "exact_report_entry_fully_visible": True,
            "exact_report_entry_in_safe_click_region": True,
            "report_visibility": report_visibility,
            "s11_report_entry_found_in_entry_snapshot": report_found_in_entry_snapshot,
            "s11_report_scroll_count": report_scroll_count,
            "s11_report_reposition_scroll_count": report_reposition_count,
            "reason_category": "S11_XML_STALE_RECOVERY_XML_BOUNDS" if using_stale_recovered_xml_binding else "NODE_DRIVEN_SAFE_CLICK",
            "reason_detail": "tap the current XML/accessibility bounds recovered after stale XML refresh" if using_stale_recovered_xml_binding else "tap the exact 鏌ョ湅瀹屾暣鎶ュ憡 only after it is fully visible in the safe click region",
            "solution": "confirm success by the S12/S13 page transition" if using_stale_recovered_xml_binding else "never click report text while it is clipped or overlapped by the bottom bar",
        },
    )
    stable_wait = _wait_s11_to_s12_stable_after_report_click(
        context,
        report_snapshot,
        before_xml_digest=before_click_xml_sha256,
        before_visible_digest=before_click_visible_digest,
        before_screenshot_digest=before_click_screenshot_sha256,
    )
    next_snapshot = stable_wait["snapshot"]
    xml_changed_after_click = bool(stable_wait.get("xml_changed_after_click"))
    visible_text_changed_after_click = bool(stable_wait.get("visible_text_changed_after_click"))
    screenshot_changed_after_click = bool(stable_wait.get("screenshot_changed_after_click"))
    page_changed_after_click = bool(stable_wait.get("page_changed_after_click"))
    recognized_after_click = stable_wait.get("recognized_page")
    context["current_reference"]["clicked_by_visual_binding"] = False
    context["current_reference"]["clicked_by_local_structure"] = using_local_structure_binding
    context["current_reference"]["view_full_report_click_result"] = recognized_after_click or "UNKNOWN"
    context["current_reference"]["s12_page_confirmed_after_click"] = recognized_after_click == "S12"
    timing.add(
        step_name="S11_TO_S12",
        page_name="S11",
        action_name="tap_full_report",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=action_ms,
        transition_wait_ms=int(stable_wait.get("wait_ms") or 0),
        screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
        xml_path=str(next_snapshot.get("xml_path") or ""),
        extra={
            "page_changed_after_click": page_changed_after_click,
            "xml_changed_after_click": xml_changed_after_click,
            "visible_text_changed_after_click": visible_text_changed_after_click,
            "screenshot_changed_after_click": screenshot_changed_after_click,
            "recognized_page": recognized_after_click,
            "clicked_by_visual_binding": bool(click_target.get("dynamic_visual_binding_attempted")),
            "clicked_by_local_structure": using_local_structure_binding,
            "view_full_report_click_result": recognized_after_click or "UNKNOWN",
            "s12_page_confirmed_after_click": recognized_after_click == "S12",
            "report_entry_detection_strategy": context["current_reference"].get("report_entry_detection_strategy"),
            "detected_button_rect": click_target.get("detected_button_rect"),
            "detection_source": click_target.get("detection_source"),
            "confidence": click_target.get("confidence"),
            "click_point_inside_detected_rect": click_target.get("click_point_inside_detected_rect"),
            "recognized_by": next_snapshot.get("recognized_by"),
            "stable_wait_rounds": stable_wait.get("stable_wait_rounds"),
            "stable_rounds": stable_wait.get("stable_rounds"),
            "loading_overlay_detected": stable_wait.get("loading_overlay_detected"),
            "loading_overlay_cleared": stable_wait.get("loading_overlay_cleared"),
            "s12_candidate_signals": stable_wait.get("s12_candidate_signals"),
            "s12_report_page_evidence": stable_wait.get("s12_report_page_evidence"),
            "s14_candidate_signals": stable_wait.get("s14_candidate_signals"),
            "s14_suppressed_by_context": stable_wait.get("s14_suppressed_by_context"),
            "s14_suppression_reason": stable_wait.get("s14_suppression_reason"),
            "wait_rounds": stable_wait.get("rounds"),
            "before_click_xml_path": str(report_snapshot.get("xml_path") or ""),
            "after_click_xml_path": str(next_snapshot.get("xml_path") or ""),
            "before_click_screenshot_path": str(report_snapshot.get("screenshot_path") or ""),
            "after_click_screenshot_path": str(next_snapshot.get("screenshot_path") or ""),
            "reason_category": "PAGE_TRANSITION_VERIFY",
            "reason_detail": "recovered report-entry click is accepted only after S12/S13 transition evidence confirms the report opened" if using_stale_recovered_xml_binding else "fresh XML is required after safe report-entry click; S11_TO_S12 waits for stable S12 report-page evidence before entering S12 handler",
            "solution": "stop with S11_REPORT_ENTRY_DIRECT_CLICK_DID_NOT_ENTER_REPORT if the recovered XML-bounds click does not open the report" if using_stale_recovered_xml_binding else "suppress S14 recognition in S11_TO_S12 context and only continue after S12 is confirmed",
        },
    )
    if not page_changed_after_click:
        issue = issues.record(
            _s11_report_entry_failure_code_after_click(
                using_stale_recovered_xml_binding,
                "S11_LOCAL_STRUCTURE_REPORT_ENTRY_CLICK_DID_NOT_ENTER_S12" if using_local_structure_binding else "S11_REPORT_ENTRY_CLICK_NO_EFFECT",
            ),
            "S11",
            "Clicked the recovered report-entry XML bounds, but did not enter the inspection report." if using_stale_recovered_xml_binding else ("Clicked the local-structure-bound report entry, but did not enter S12." if using_local_structure_binding else "Clicked the fully visible 鏌ョ湅瀹屾暣鎶ュ憡 safe target, but page XML/visible text did not change."),
            {
                "click_target": click_target,
                "report_visibility": report_visibility,
                "page_changed_after_click": page_changed_after_click,
                "xml_changed_after_click": xml_changed_after_click,
                "visible_text_changed_after_click": visible_text_changed_after_click,
                "screenshot_changed_after_click": screenshot_changed_after_click,
                "before_click_xml_path": str(report_snapshot.get("xml_path") or ""),
                "after_click_xml_path": str(next_snapshot.get("xml_path") or ""),
                **next_snapshot,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if stable_wait.get("loading_timeout_with_s12_signals"):
        issue = issues.record(
            "S11_TO_S12_LOADING_TIMEOUT_WITH_S12_SIGNALS",
            "S11",
            "鏌ョ湅瀹屾暣鎶ュ憡 click produced S12 report signals, but loading overlay did not clear within bounded stable wait.",
            {
                "click_target": click_target,
                "report_visibility": report_visibility,
                "stable_wait": stable_wait,
                "before_click_xml_path": str(report_snapshot.get("xml_path") or ""),
                "after_click_xml_path": str(next_snapshot.get("xml_path") or ""),
                **next_snapshot,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    if recognized_after_click != "S12":
        issue = issues.record(
            _s11_report_entry_failure_code_after_click(
                using_stale_recovered_xml_binding,
                "S11_LOCAL_STRUCTURE_REPORT_ENTRY_CLICK_DID_NOT_ENTER_S12" if using_local_structure_binding else "S11_TO_S12_AFTER_FULL_VISIBLE_REPORT_CLICK_CONTRACT_MISMATCH",
            ),
            recognized_after_click or "UNKNOWN",
            (
                f"Clicked recovered report-entry XML bounds, but expected S12 and recognized {recognized_after_click or 'UNKNOWN'}."
                if using_stale_recovered_xml_binding
                else f"Clicked local-structure-bound 鏌ョ湅瀹屾暣鎶ュ憡 entry, but expected S12 and recognized {recognized_after_click or 'UNKNOWN'}."
                if using_local_structure_binding
                else f"Clicked fully visible 鏌ョ湅瀹屾暣鎶ュ憡, but expected S12 and recognized {recognized_after_click or 'UNKNOWN'}."
            ),
            {
                "click_target": click_target,
                "report_visibility": report_visibility,
                "recognized_after_click": recognized_after_click,
                "page_changed_after_click": page_changed_after_click,
                "xml_changed_after_click": xml_changed_after_click,
                "visible_text_changed_after_click": visible_text_changed_after_click,
                "screenshot_changed_after_click": screenshot_changed_after_click,
                "stable_wait": stable_wait,
                "s12_candidate_signals": stable_wait.get("s12_candidate_signals"),
                "s14_candidate_signals": stable_wait.get("s14_candidate_signals"),
                "s14_suppressed_by_context": stable_wait.get("s14_suppressed_by_context"),
                "before_click_xml_path": str(report_snapshot.get("xml_path") or ""),
                "after_click_xml_path": str(next_snapshot.get("xml_path") or ""),
                **next_snapshot,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    return "S12", next_snapshot


S12_NO_CLAIM_TEXTS = (
    "无出险",
    "暂无出险",
    "未查询到出险",
    "未查询到理赔",
    "暂无理赔",
    "无理赔",
    "无理赔记录",
    "无金额记录",
)


def _s12_claim_search_text(snapshot: dict[str, Any]) -> str:
    parts = [
        str(snapshot.get("visible_blob") or ""),
        str(snapshot.get("fresh_xml") or ""),
        "\n".join(str(item) for item in snapshot.get("visible_texts") or []),
    ]
    node_texts: list[str] = []
    for node in snapshot.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        for key in ("text", "content_desc"):
            value = str(node.get(key) or "").strip()
            if value:
                node_texts.append(value)
        for value in node.get("labels") or []:
            text = str(value or "").strip()
            if text:
                node_texts.append(text)
    if node_texts:
        parts.append("\n".join(node_texts))
    return "\n".join(part for part in parts if part)


def _s12_text_candidates(text: str, labels: tuple[str, ...]) -> list[str]:
    candidates: list[str] = []
    compact = re.sub(r"\s+", "", text or "")
    for label in labels:
        for source in (text or "", compact):
            for match in re.finditer(re.escape(label), source):
                start = max(0, match.start() - 20)
                end = min(len(source), match.end() + 40)
                candidate = source[start:end].strip()
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
    return candidates[:12]


def _s12_has_no_claim_text_evidence(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if any(token in compact for token in S12_NO_CLAIM_TEXTS):
        return True
    return bool(re.search(r"(理赔次数|出险次数|理赔记录|出险记录)[:：]?\s*0\s*次", compact))


def _extract_claim_count_with_candidates(text: str) -> tuple[int | None, list[str]]:
    candidates = _s12_text_candidates(text, ("理赔次数", "出险次数", "理赔记录", "出险记录", "无出险", "暂无理赔", "未查询到理赔"))
    compact = re.sub(r"\s+", "", text or "")
    for pattern in (
        r"(?:理赔次数|出险次数)[:：]?\s*(\d+)\s*次?",
        r"(?:理赔记录|出险记录)[:：]?\s*(\d+)\s*次",
    ):
        match = re.search(pattern, compact)
        if match:
            return int(match.group(1)), candidates
    if _s12_has_no_claim_text_evidence(text):
        return 0, candidates or ["explicit_no_claim_text_evidence"]
    return None, candidates


def _normalize_s12_amount_value(raw_number: str, unit: str) -> float:
    value = float(raw_number)
    if unit in {"万", "万元"}:
        return value * 10000
    return value


def _extract_max_amount_with_candidates(text: str, claim_count: int | None = None) -> tuple[float | int | None, list[str]]:
    candidates = _s12_text_candidates(text, ("最大金额", "最高金额", "赔付金额", "理赔金额", "无金额记录", "无出险", "暂无理赔", "未查询到理赔"))
    compact = re.sub(r"\s+", "", text or "")
    for pattern in (
        r"(?:最大金额|最高金额|最高赔付金额|最大赔付金额|赔付金额|理赔金额)[:：]?\s*(\d+(?:\.\d+)?)(万元|万|元)?",
        r"(?:最大|最高)[^\d]{0,8}(\d+(?:\.\d+)?)(万元|万|元)?",
    ):
        match = re.search(pattern, compact)
        if match:
            return _normalize_s12_amount_value(match.group(1), match.group(2) or ""), candidates
    if claim_count == 0 and _s12_has_no_claim_text_evidence(text):
        return 0, candidates or ["explicit_no_claim_text_evidence_allows_zero_amount"]
    return None, candidates


def _extract_claim_count(text: str) -> int | None:
    return _extract_claim_count_with_candidates(text)[0]


def _extract_max_amount(text: str) -> float | int | None:
    claim_count = _extract_claim_count(text)
    return _extract_max_amount_with_candidates(text, claim_count)[0]


def _s12_page_still_loading(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    return any(token in compact for token in ("加载中", "正在加载", "请稍候", "数据加载", "骨架屏", "loading"))


def _extract_s12_claim_fields_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    text = _s12_claim_search_text(snapshot)
    try:
        claim_count, claim_candidates = _extract_claim_count_with_candidates(text)
        max_amount, amount_candidates = _extract_max_amount_with_candidates(text, claim_count)
        safe_parse_trace = {
            "s12_recovery_candidate_skipped": False,
            "s12_recovery_candidate_skip_reason": "",
            "s12_recovery_index_error_prevented": False,
            "s12_recovery_malformed_candidate_count": 0,
            "s12_recovery_safe_parse_used": True,
        }
    except (IndexError, TypeError, ValueError, AttributeError, re.error) as exc:
        claim_count = None
        max_amount = None
        claim_candidates = []
        amount_candidates = []
        safe_parse_trace = {
            "s12_recovery_candidate_skipped": True,
            "s12_recovery_candidate_skip_reason": f"{type(exc).__name__}: {exc}",
            "s12_recovery_index_error_prevented": isinstance(exc, IndexError),
            "s12_recovery_malformed_candidate_count": 1,
            "s12_recovery_safe_parse_used": True,
        }
    missing: list[str] = []
    if claim_count is None:
        missing.append("claim_count")
    if max_amount is None:
        missing.append("max_amount")
    return {
        "claim_count": claim_count,
        "max_amount": max_amount,
        "claim_count_text_candidates": claim_candidates,
        "max_amount_text_candidates": amount_candidates,
        "missing_fields": missing,
        "page_still_loading": _s12_page_still_loading(text),
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "xml_path": str(snapshot.get("xml_path") or ""),
        **safe_parse_trace,
    }


def _normalize_s12_recovery_attempts(raw_attempts: Any) -> tuple[list[dict[str, Any]], int]:
    if raw_attempts in (None, "", [], {}):
        return [], 0
    if not isinstance(raw_attempts, (list, tuple)):
        return [], 1
    attempts: list[dict[str, Any]] = []
    malformed_count = 0
    for item in raw_attempts:
        if isinstance(item, dict):
            attempts.append(dict(item))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            attempts.append(
                {
                    "attempt_index": item[0],
                    "candidate": item[1],
                    "extra": list(item[2:]),
                    "normalized_from_sequence": True,
                }
            )
        else:
            malformed_count += 1
    return attempts, malformed_count


def _s12_claim_recovery_extent_candidate(raw_extent: Any) -> tuple[tuple[int, int, int, int] | None, dict[str, Any]]:
    return clean_field_extractors.s12_claim_recovery_extent_candidate(raw_extent)


def _summarize_s12_claim_recovery_extents(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_count = 0
    valid_count = 0
    malformed_count = 0
    skipped: list[Any] = []
    selected: tuple[int, int, int, int] | None = None
    for attempt in attempts:
        raw_extent = attempt.get("bounds")
        if raw_extent is None and attempt.get("extent") is not None:
            raw_extent = attempt.get("extent")
        if raw_extent is None and attempt.get("visible_bounds") is not None:
            raw_extent = attempt.get("visible_bounds")
        candidate_count += 1
        bounds, trace = _s12_claim_recovery_extent_candidate(raw_extent)
        attempt["s12_claim_recovery_bounds_valid"] = bool(bounds)
        attempt["s12_claim_recovery_failure_reason"] = trace.get("failure_reason") or ""
        if bounds is None:
            malformed_count += 1
            skipped.append(raw_extent)
            continue
        valid_count += 1
        if selected is None:
            selected = bounds
    stop_code = ""
    if candidate_count and valid_count == 0:
        stop_code = S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE
    return {
        "s12_claim_recovery_candidate_count": candidate_count,
        "s12_claim_recovery_valid_candidate_count": valid_count,
        "s12_claim_recovery_malformed_candidate_count": malformed_count,
        "s12_claim_recovery_skipped_malformed_extents": skipped,
        "s12_claim_recovery_selected_candidate_extent": list(selected) if selected else [],
        "s12_claim_recovery_bounds_valid": bool(selected),
        "s12_claim_recovery_failure_reason": "no_valid_extent_candidate" if stop_code else "",
        "s12_claim_recovery_stop_code": stop_code,
    }


def _recover_s12_claim_fields(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    hook = context.get("s12_claim_field_recovery_hook")
    if callable(hook):
        hook_exception: Exception | None = None
        try:
            result = dict(hook(context, snapshot) or {})
        except (IndexError, TypeError, ValueError, AttributeError, re.error) as exc:
            hook_exception = exc
            result = {
                "missing_fields": ["claim_count", "max_amount"],
                "s12_recovery_candidate_skipped": True,
                "s12_recovery_candidate_skip_reason": f"{type(exc).__name__}: {exc}",
                "s12_recovery_index_error_prevented": isinstance(exc, IndexError),
                "s12_recovery_malformed_candidate_count": 1,
                "s12_recovery_safe_parse_used": True,
            }
        recovered_snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else snapshot
        attempts, malformed_attempt_count = _normalize_s12_recovery_attempts(
            result.get("s12_claim_field_recovery_attempts") or result.get("attempts")
        )
        extent_summary = _summarize_s12_claim_recovery_extents(attempts)
        extracted = _extract_s12_claim_fields_from_snapshot(recovered_snapshot)
        extracted["claim_count"] = result.get("claim_count", extracted["claim_count"])
        extracted["max_amount"] = result.get("max_amount", extracted["max_amount"])
        extracted["missing_fields"] = list(result.get("missing_fields") or [])
        if not result.get("missing_fields"):
            extracted["missing_fields"] = [
                field
                for field, value in (("claim_count", extracted["claim_count"]), ("max_amount", extracted["max_amount"]))
                if value is None
            ]
        trace = {
            "s12_claim_field_recovery_attempted": True,
            "s12_claim_field_recovery_hook_used": True,
            "s12_claim_field_recovery_attempts": attempts,
            "s12_claim_count_text_candidates": list(result.get("s12_claim_count_text_candidates") or extracted["claim_count_text_candidates"]),
            "s12_max_amount_text_candidates": list(result.get("s12_max_amount_text_candidates") or extracted["max_amount_text_candidates"]),
            "s12_claim_count_extracted": extracted["claim_count"],
            "s12_max_amount_extracted": extracted["max_amount"],
            "s12_page_recognized": True,
            "s12_page_still_loading": bool(result.get("s12_page_still_loading") or extracted["page_still_loading"]),
            "s12_scroll_recovery_attempted": bool(result.get("s12_scroll_recovery_attempted")),
            "s12_field_missing_after_recovery": bool(extracted["missing_fields"]),
            "s12_missing_fields": list(extracted["missing_fields"]),
            "s12_recovery_candidate_skipped": bool(
                result.get("s12_recovery_candidate_skipped") or extracted.get("s12_recovery_candidate_skipped")
            ),
            "s12_recovery_candidate_skip_reason": (
                result.get("s12_recovery_candidate_skip_reason")
                or extracted.get("s12_recovery_candidate_skip_reason")
                or (f"{type(hook_exception).__name__}: {hook_exception}" if hook_exception else "")
            ),
            "s12_recovery_index_error_prevented": bool(
                result.get("s12_recovery_index_error_prevented")
                or extracted.get("s12_recovery_index_error_prevented")
            ),
            "s12_recovery_malformed_candidate_count": int(
                (result.get("s12_recovery_malformed_candidate_count") or 0)
                + malformed_attempt_count
                + (extracted.get("s12_recovery_malformed_candidate_count") or 0)
            ),
            "s12_recovery_safe_parse_used": True,
            **extent_summary,
        }
        if trace["s12_claim_recovery_stop_code"]:
            trace["root_exception_function"] = "_recover_s12_claim_fields"
            trace["root_exception_type"] = "IndexError"
            trace["root_exception_message"] = "tuple index out of range prevented by extent guard"
        return recovered_snapshot, trace

    timing: TimingRecorder | None = context.get("timing")
    recognizer: PageRecognizer | None = context.get("recognizer")
    attempts: list[dict[str, Any]] = []
    current = snapshot
    scroll_attempted = False
    for attempt_index in range(1, 4):
        if attempt_index == 2:
            time.sleep(0.45)
            current = _capture_with_global_popup_guard(
                context,
                f"s12_claim_fields_fresh_{attempt_index}",
                current_stage="S12",
                call_site="s12_claim_field_recovery",
            )
        elif attempt_index == 3:
            scroll_attempted = True
            raw_extent = _visible_bounds_extent(current)
            extent = raw_extent[0] if isinstance(raw_extent, tuple) and _is_valid_extent(raw_extent[0]) else (0, 0, 1080, 1920)
            attempts.append(
                {
                    "attempt_index": "visible_bounds_extent",
                    "bounds": extent,
                    "source": raw_extent[1] if isinstance(raw_extent, tuple) and len(raw_extent) > 1 else "screen_fallback",
                }
            )
            x = (extent[0] + extent[2]) // 2
            y1 = extent[1] + int((extent[3] - extent[1]) * 0.68)
            y2 = extent[1] + int((extent[3] - extent[1]) * 0.48)
            action_started = time.perf_counter()
            try:
                context["client"].run(["shell", "input", "swipe", str(x), str(y1), str(x), str(y2), "280"], timeout=20)
            except Exception:
                pass
            action_ms = int((time.perf_counter() - action_started) * 1000)
            time.sleep(0.35)
            current = _capture_with_global_popup_guard(
                context,
                f"s12_claim_fields_scroll_fresh_{attempt_index}",
                current_stage="S12",
                call_site="s12_claim_field_recovery_scroll",
            )
            if timing is not None:
                timing.add(
                    step_name="S12_CLAIM_FIELD_RECOVERY_SCROLL",
                    page_name="S12",
                    action_name="small_vertical_scroll_for_claim_fields",
                    contract_check_ms=0,
                    field_read_ms=0,
                    action_ms=action_ms,
                    transition_wait_ms=350,
                    screenshot_path=str(current.get("screenshot_path") or ""),
                    xml_path=str(current.get("xml_path") or ""),
                    extra={"s12_scroll_recovery_attempted": True},
                )
        extracted = _extract_s12_claim_fields_from_snapshot(current)
        recognized = _recognize_mainline_page(recognizer, current) if recognizer is not None else "S12"
        attempt = {
            "attempt_index": attempt_index,
            "recognized_page": recognized,
            "claim_count": extracted["claim_count"],
            "max_amount": extracted["max_amount"],
            "missing_fields": extracted["missing_fields"],
            "page_still_loading": extracted["page_still_loading"],
            "screenshot_path": extracted["screenshot_path"],
            "xml_path": extracted["xml_path"],
            "s12_recovery_candidate_skipped": extracted.get("s12_recovery_candidate_skipped"),
            "s12_recovery_candidate_skip_reason": extracted.get("s12_recovery_candidate_skip_reason"),
            "s12_recovery_index_error_prevented": extracted.get("s12_recovery_index_error_prevented"),
            "s12_recovery_malformed_candidate_count": extracted.get("s12_recovery_malformed_candidate_count"),
            "s12_recovery_safe_parse_used": extracted.get("s12_recovery_safe_parse_used"),
        }
        attempts.append(attempt)
        if timing is not None:
            timing.add(
                step_name="S12_CLAIM_FIELD_RECOVERY_ATTEMPT",
                page_name="S12",
                action_name="fresh_capture_extract_claim_fields",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=0,
                transition_wait_ms=0,
                screenshot_path=extracted["screenshot_path"],
                xml_path=extracted["xml_path"],
                extra=attempt,
            )
        if not extracted["missing_fields"]:
            break
    final = _extract_s12_claim_fields_from_snapshot(current)
    extent_summary = _summarize_s12_claim_recovery_extents(
        [item for item in attempts if isinstance(item, dict) and item.get("bounds") is not None]
    )
    trace = {
        "s12_claim_field_recovery_attempted": True,
        "s12_claim_field_recovery_attempts": attempts,
        "s12_claim_count_text_candidates": final["claim_count_text_candidates"],
        "s12_max_amount_text_candidates": final["max_amount_text_candidates"],
        "s12_claim_count_extracted": final["claim_count"],
        "s12_max_amount_extracted": final["max_amount"],
        "s12_page_recognized": True,
        "s12_page_still_loading": bool(final["page_still_loading"]),
        "s12_scroll_recovery_attempted": scroll_attempted,
        "s12_field_missing_after_recovery": bool(final["missing_fields"]),
        "s12_missing_fields": list(final["missing_fields"]),
        "s12_recovery_candidate_skipped": bool(final.get("s12_recovery_candidate_skipped")),
        "s12_recovery_candidate_skip_reason": final.get("s12_recovery_candidate_skip_reason") or "",
        "s12_recovery_index_error_prevented": bool(final.get("s12_recovery_index_error_prevented")),
        "s12_recovery_malformed_candidate_count": int(final.get("s12_recovery_malformed_candidate_count") or 0),
        "s12_recovery_safe_parse_used": True,
        **extent_summary,
    }
    if trace["s12_claim_recovery_stop_code"]:
        trace["root_exception_function"] = "_recover_s12_claim_fields"
        trace["root_exception_type"] = "IndexError"
        trace["root_exception_message"] = "tuple index out of range prevented by extent guard"
    return current, trace


def _s12_current_reference_is_final_candidate(context: dict[str, Any], current_index: int) -> bool:
    if current_index <= 0:
        return True
    containers = [
        context.get("selection") if isinstance(context.get("selection"), dict) else {},
        context.get("continuation_plan") if isinstance(context.get("continuation_plan"), dict) else {},
        context.get("current_reference") if isinstance(context.get("current_reference"), dict) else {},
    ]
    for container in containers:
        for key in (
            "final_reference_candidate_index",
            "recollect_reference_index",
            "boundary_previous_reference_index",
            "v33_final_reference_candidate_index",
            "v33_recollect_reference_index",
        ):
            if _safe_int(container.get(key), default=0) == current_index:
                return True
    if _safe_int(context.get("v33_recollect_next_reference_index"), default=0) == current_index:
        return True
    return False


def _s12_claim_fields_missing_decision(context: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    current_reference = context.setdefault("current_reference", {})
    current_index = _safe_int(current_reference.get("reference_index") or context.get("current_reference_index"), default=0)
    trisame_count = _first_stage_trisame_count(context.get("first_stage_evidence") or {})
    next_index = current_index + 1 if current_index > 0 else 0
    protected = _s12_current_reference_is_final_candidate(context, current_index)
    base = {
        "current_reference_index": current_index or None,
        "next_reference_index": next_index or None,
        "trisame_count": trisame_count,
        "s12_claim_field_trace": trace,
        "excluded_reason": trace.get("s12_claim_recovery_stop_code") or S12_CLAIM_FIELDS_NOT_READABLE,
        "missing_fields": list(trace.get("s12_missing_fields") or ["claim_count", "max_amount"]),
    }
    if trace.get("s12_claim_recovery_stop_code") == S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE:
        return {
            **base,
            "status": "NEEDS_REVIEW",
            "business_status": "NEEDS_REVIEW",
            "issue_code": S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE,
            "stop_code": S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE,
            "manual_review_required": True,
            "needs_review": True,
            "s12_claim_field_decision": "needs_review_no_valid_recovery_extent",
        }
    if protected:
        return {
            **base,
            "status": "NEEDS_REVIEW",
            "business_status": "NEEDS_REVIEW",
            "issue_code": S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW,
            "stop_code": S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW,
            "manual_review_required": True,
            "needs_review": True,
            "s12_claim_field_decision": "needs_review_final_candidate",
        }
    if trisame_count is not None and next_index > 0 and next_index <= trisame_count:
        return {
            **base,
            "status": "CONTINUE_NEXT_REFERENCE",
            "final_status": "CONTINUE_NEXT_REFERENCE",
            "business_status": "CONTINUE_NEXT_REFERENCE",
            "issue_code": S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE,
            "stop_code": S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE,
            "excluded_reference_index": current_index,
            "excluded_reference_status": REFERENCE_EXCLUDED_S12_CLAIM_FIELDS_MISSING,
            "should_continue_reference_collection": True,
            "continue_reason": "S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE",
            "s12_claim_field_decision": "exclude_current_continue_next_reference",
        }
    return {
        **base,
        "status": "NEEDS_REVIEW",
        "business_status": "NEEDS_REVIEW",
        "issue_code": S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW,
        "stop_code": S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW,
        "manual_review_required": True,
        "needs_review": True,
        "s12_claim_field_decision": "needs_review_no_more_reference",
    }


def _return_to_reliable_s10_after_s12_claim_field_exclusion(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    excluded_index: int,
    next_reference_index: int,
) -> dict[str, Any]:
    hook = context.get("s12_claim_field_missing_return_hook")
    if callable(hook):
        returned = dict(hook(context, snapshot, excluded_index=excluded_index, next_reference_index=next_reference_index) or {})
        returned.setdefault("mocked", True)
        returned.setdefault("recognized_page", "S10")
        returned.setdefault("return_to_reliable_s10_verified", True)
        return returned.get("snapshot") if isinstance(returned.get("snapshot"), dict) else returned
    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    timing: TimingRecorder = context["timing"]
    issues: IssueRecorder = context["issues"]
    attempts: list[dict[str, Any]] = []
    last_snapshot: dict[str, Any] | None = None
    last_state = ""
    last_reliable_evidence: dict[str, Any] = {}
    for attempt_index in range(1, 4):
        action_started = time.perf_counter()
        client.back()
        action_ms = int((time.perf_counter() - action_started) * 1000)
        time.sleep(0.45)
        fresh = _capture_with_global_popup_guard(
            context,
            f"s12_claim_missing_return_s10_{attempt_index}",
            current_stage="S12_RETURN_TO_S10",
            call_site="s12_claim_missing_return_s10",
        )
        target_car = context.get("target_car")
        if target_car is not None:
            fresh["target_brand"] = target_car.brand
            fresh["target_car"] = {
                "brand": target_car.brand,
                "series": target_car.series,
                "model_year": target_car.model_year,
                "trim": target_car.trim,
            }
        state = _recognize_mainline_page(recognizer, fresh)
        reliable_evidence: dict[str, Any] = {}
        if state == "S10":
            expected_card = _expected_reference_card_with_continuation_context(
                context.get("first_stage_evidence") or {},
                next_reference_index,
                context.get("continuation_plan"),
            )
            reliable_evidence = _s10_reliable_list_evidence(
                fresh,
                target_reference_index=next_reference_index,
                expected_card=expected_card,
            )
        attempt = {
            "attempt_index": attempt_index,
            "recognized_page": state,
            "excluded_reference_index": excluded_index,
            "next_reference_index": next_reference_index,
            "action_ms": action_ms,
            "s10_reliable_list_evidence": reliable_evidence,
            "screenshot_path": str(fresh.get("screenshot_path") or ""),
            "xml_path": str(fresh.get("xml_path") or ""),
        }
        attempts.append(attempt)
        timing.add(
            step_name="S12_CLAIM_FIELD_MISSING_RETURN_TO_S10_ATTEMPT",
            page_name="S12",
            action_name="return_to_reliable_s10_after_s12_claim_field_exclusion",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=action_ms,
            transition_wait_ms=450,
            screenshot_path=str(fresh.get("screenshot_path") or ""),
            xml_path=str(fresh.get("xml_path") or ""),
            extra=attempt,
        )
        last_snapshot = fresh
        last_state = state
        last_reliable_evidence = reliable_evidence
        if state == "S10" and reliable_evidence.get("reliable") is True:
            context["returned_s10_snapshot"] = fresh
            context["returned_s10_snapshot_source"] = "S12_CLAIM_FIELD_EXCLUSION_RETURN"
            context["returned_s10_reliable_evidence"] = reliable_evidence
            context["returned_list_source"] = "from_s12_claim_field_missing_exclusion"
            context["returned_list_source_verified"] = True
            context["return_to_reliable_s10_result"] = "returned"
            context["s12_claim_field_missing_return_attempts"] = attempts
            return fresh
    issue = issues.record(
        "S12_RETURN_TO_RELIABLE_S10_AFTER_CLAIM_FIELD_EXCLUSION_FAILED",
        "S12",
        "S12 claim fields were unreadable and the reference was excluded, but runtime could not return to reliable S10.",
        {
            **(last_snapshot or snapshot),
            "recognized_page": last_state,
            "excluded_reference_index": excluded_index,
            "next_reference_index": next_reference_index,
            "s10_reliable_list_evidence": last_reliable_evidence,
            "s12_claim_field_missing_return_attempts": attempts,
        },
        "manual_review",
    )
    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])


def _exclude_current_reference_for_s12_claim_fields_missing(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    decision: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    current_reference = dict(context.get("current_reference") or {})
    excluded_index = _safe_int(decision.get("excluded_reference_index") or current_reference.get("reference_index") or context.get("current_reference_index"), default=0)
    next_reference_index = _safe_int(decision.get("next_reference_index"), default=0)
    current_reference.update(
        {
            "current_reference_excluded": True,
            "excluded_from_boundary": True,
            "reference_status": REFERENCE_EXCLUDED_S12_CLAIM_FIELDS_MISSING,
            "reference_exclusion_reason": S12_CLAIM_FIELDS_NOT_READABLE,
            "excluded_from_boundary_reason": S12_CLAIM_FIELDS_NOT_READABLE,
            "excluded_reference_index": excluded_index,
            "s12_claim_field_decision": decision,
            "s12_claim_field_recovery_attempted": True,
        }
    )
    context["current_reference"] = current_reference
    context["invalid_partial_reference_detected"] = True
    context["invalid_partial_reference_index"] = excluded_index
    context["invalid_partial_reference_reason"] = S12_CLAIM_FIELDS_NOT_READABLE
    context.setdefault("excluded_reference_history", []).append(current_reference)
    context.setdefault("reference_history", []).append(current_reference)
    context["previous_reference_index"] = excluded_index
    context["current_reference_index"] = next_reference_index
    context["next_reference_index"] = next_reference_index
    returned = _return_to_reliable_s10_after_s12_claim_field_exclusion(
        context,
        snapshot,
        excluded_index=excluded_index,
        next_reference_index=next_reference_index,
    )
    context["current_reference"] = {}
    context["exclude_current_reference_from_history"] = False
    return "S10", returned


def handle_s12(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    client: AdbClient = context["client"]
    start = time.perf_counter()
    snapshot = _maybe_close_guazi_push_popup_and_resume(context, snapshot, current_stage="S12")
    _ensure_page("S12", recognizer, issues, snapshot)
    text = str(snapshot.get("visible_blob") or "")
    read_start = time.perf_counter()
    claim_count, initial_claim_candidates = _extract_claim_count_with_candidates(text)
    max_amount, initial_amount_candidates = _extract_max_amount_with_candidates(text, claim_count)
    if claim_count is None or max_amount is None:
        recovered_snapshot, recovery_trace = _recover_s12_claim_fields(context, snapshot)
        recovery_trace.setdefault("s12_claim_count_text_candidates", initial_claim_candidates)
        recovery_trace.setdefault("s12_max_amount_text_candidates", initial_amount_candidates)
        claim_count = recovery_trace.get("s12_claim_count_extracted")
        max_amount = recovery_trace.get("s12_max_amount_extracted")
        if claim_count is None or max_amount is None:
            decision = _s12_claim_fields_missing_decision(context, recovery_trace)
            context.setdefault("current_reference", {})["s12_claim_field_missing_decision"] = decision
            context.setdefault("current_reference", {})["s12_claim_field_trace"] = recovery_trace
            if decision.get("status") == "CONTINUE_NEXT_REFERENCE":
                timing.add(
                    step_name="S12_CLAIM_FIELD_MISSING_EXCLUDE_AND_CONTINUE",
                    page_name="S12",
                    action_name="exclude_reference_and_continue_next",
                    contract_check_ms=int((read_start - start) * 1000),
                    field_read_ms=int((time.perf_counter() - read_start) * 1000),
                    action_ms=0,
                    transition_wait_ms=0,
                    screenshot_path=str(recovered_snapshot.get("screenshot_path") or ""),
                    xml_path=str(recovered_snapshot.get("xml_path") or ""),
                    extra=decision,
                )
                return _exclude_current_reference_for_s12_claim_fields_missing(context, recovered_snapshot, decision)
            issue = issues.record(
                str(decision.get("issue_code") or S12_REPORT_CLAIM_COUNT_MAX_AMOUNT_FIELD_MISSING),
                "S12",
                "S12 report page was recognized, but claim count or max amount could not be read after bounded recovery.",
                {
                    **recovered_snapshot,
                    "s12_claim_field_missing_decision": decision,
                    "s12_claim_field_trace": recovery_trace,
                },
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        snapshot = recovered_snapshot
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
    clicked_text = "杞﹁韩澶栬"
    clicked_strategy = "exact_text_node_bounds"
    visibility_start = time.perf_counter()
    exact_node = _find_body_appearance_tab_node(snapshot)
    body_tab_node_search_count = 1
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
            "s12_body_tab_node_search_count": body_tab_node_search_count,
            "s12_visibility_node_cached_for_click": bool(exact_node),
            "reason_category": "NODE_VISIBILITY_CHECK",
            "reason_detail": "preloaded XML nodes are not clickable until the exact body appearance tab is in the visible top/navigation tab area",
            "solution": "perform controlled pre-click scroll when the exact tab is not visibly reached",
        },
    )
    safe_scroll_ms = 0
    if bounds is None:
        fallback_result = _handle_s12_body_appearance_missing_exact_target(
            context,
            snapshot,
            field_ms=field_ms,
            read_start=read_start,
            start=start,
            body_tab_node_search_count=body_tab_node_search_count,
            safe_scroll_ms=safe_scroll_ms,
        )
        if fallback_result is not None:
            return fallback_result
        clicked_strategy = "exact_text_node_bounds_after_controlled_scroll"
        body_tab_node_search_count += 1
        snapshot, exact_node, safe_scroll_ms = _find_body_appearance_tab_after_controlled_scroll(
            context,
            snapshot,
            "s12_scroll_body",
        )
        bounds = exact_node.get("bounds") if exact_node else None
    if bounds is None:
        fallback_result = _handle_s12_body_appearance_missing_exact_target(
            context,
            snapshot,
            field_ms=field_ms,
            read_start=read_start,
            start=start,
            body_tab_node_search_count=body_tab_node_search_count,
            safe_scroll_ms=safe_scroll_ms,
        )
        if fallback_result is not None:
            return fallback_result
        issue = issues.record("S12_BODY_APPEARANCE_TAB_NODE_NOT_FOUND", "S12", "杞﹁韩澶栬 tab node not found after finite controlled scroll.", snapshot, "manual_review")
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    clicked_point = _center(bounds)
    context["current_reference"]["s12_body_appearance_click"] = {
        "clicked_text": clicked_text,
        "clicked_node_bounds": list(bounds),
        "clicked_point": list(clicked_point),
        "click_strategy": clicked_strategy,
        "tab_region_confirmed": bool(exact_node.get("tab_region_confirmed")) if exact_node else False,
        "visibility_check": exact_node.get("visibility_check") if exact_node else None,
        "s12_visibility_node_reused_for_click": True,
        "s12_body_tab_node_search_count": body_tab_node_search_count,
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "xml_path": str(snapshot.get("xml_path") or ""),
    }
    action_ms = contract_execute_click(
        context,
        snapshot,
        "S12",
        "tap_body_appearance",
        (int(clicked_point[0]), int(clicked_point[1])),
        evidence={
            "clicked_text": clicked_text,
            "clicked_node_bounds": list(bounds),
            "click_strategy": clicked_strategy,
            "tab_region_confirmed": bool(exact_node.get("tab_region_confirmed")) if exact_node else False,
        },
    )
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
            "s12_visibility_node_reused_for_click": True,
            "s12_body_tab_node_search_count": body_tab_node_search_count,
            "reason_category": "NODE_SEARCH_SLOW" if action_ms > 1000 else "EXACT_TEXT_NODE_CLICK",
            "reason_detail": "click exact body appearance text node before any scroll fallback",
            "solution": "prefer the contract-allowed direct tab click and fresh-check immediately after it",
        },
    )
    next_snapshot, wait_ms = _wait_for_page(
        client,
        recognizer,
        "S13",
        "s12_to_s13",
        timeout_s=8.0,
        context=context,
        current_stage="S12_TO_S13",
    )
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
            "s12_visibility_node_reused_for_click": True,
            "s12_body_tab_node_search_count": body_tab_node_search_count,
            "pre_click_scroll_used": bool(safe_scroll_ms),
            "reason_category": "S12_BODY_APPEARANCE_TAB_CLICK",
            "reason_detail": "exact body-appearance tab text is clicked before any controlled scroll fallback",
            "solution": "use the contract-allowed direct tab action whenever the tab node is present",
        },
    )
    if _recognize_mainline_page(recognizer, next_snapshot) != "S13":
        issue = issues.record("CLICK_TARGET_NOT_ENTERED", "S12", "Tapped 杞﹁韩澶栬 but S13 contract did not become stable.", next_snapshot, "manual_review")
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    return "S13", next_snapshot


def _valid_visible_bounds(bounds: tuple[int, int, int, int] | None) -> bool:
    return bool(bounds and bounds[2] > bounds[0] and bounds[3] > bounds[1] and bounds[2] > 0 and bounds[3] > 0)


def _ordered_visible_node_labels(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for index, node in enumerate(snapshot.get("nodes", [])):
        bounds = node.get("bounds")
        if not _valid_visible_bounds(bounds):
            continue
        label = str(node.get("text") or node.get("content_desc") or "").strip()
        if not label:
            continue
        ordered.append({"index": index, "text": label, "bounds": bounds, "node": node})
    return ordered


def _bounds_center_y(bounds: tuple[int, int, int, int]) -> int:
    return (bounds[1] + bounds[3]) // 2


def _same_visual_row(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    a_h = max(a[3] - a[1], 1)
    b_h = max(b[3] - b[1], 1)
    tolerance = max(80, int(max(a_h, b_h) * 0.75))
    return abs(_bounds_center_y(a) - _bounds_center_y(b)) <= tolerance


def _extract_history_repair_count_from_nodes(
    snapshot: dict[str, Any],
    region_name: str,
) -> tuple[int | None, dict[str, Any]]:
    ordered = _ordered_visible_node_labels(snapshot)
    debug: dict[str, Any] = {
        "raw_nodes_used": True,
        "visible_texts_dedup_used": False,
        "duplicate_zero_preserved": True,
        "region_name": region_name,
        "history_repair_label_bounds": None,
        "bound_count_text": None,
        "bound_count_bounds": None,
        "excluded_numbers": [],
        "bind_reason": "",
    }
    region_headers = [
        item
        for item in ordered
        if item["text"].startswith(f"{region_name}深度检测")
    ]
    if not region_headers:
        debug["not_confirmed_reason"] = "region_header_not_found"
        return None, debug
    header = min(region_headers, key=lambda item: item["bounds"][1])
    header_bounds = header["bounds"]
    debug["region_header_text"] = header["text"]
    debug["region_header_bounds"] = list(header_bounds)

    header_pos = ordered.index(header)
    history_candidates: list[dict[str, Any]] = []
    for item in ordered[header_pos + 1 : header_pos + 12]:
        text = item["text"]
        if text.endswith("深度检测：") and item is not header:
            break
        if text == "历史修复" and _same_visual_row(header_bounds, item["bounds"]):
            history_candidates.append(item)
    if not history_candidates:
        debug["not_confirmed_reason"] = "history_repair_label_not_found_near_region_header"
        return None, debug
    history = min(
        history_candidates,
        key=lambda item: (abs(_bounds_center_y(item["bounds"]) - _bounds_center_y(header_bounds)), item["bounds"][0]),
    )
    history_bounds = history["bounds"]
    debug["history_repair_label_bounds"] = list(history_bounds)
    history_pos = ordered.index(history)

    count_candidates: list[dict[str, Any]] = []
    local_window = ordered[history_pos + 1 : history_pos + 10]
    for item in local_window:
        text = item["text"]
        bounds = item["bounds"]
        if text in {"注意事项", "检测通过"}:
            if text == "注意事项":
                break
            continue
        digits = re.findall(r"\d+", text)
        if not digits:
            continue
        if "检测通过" in text or "深度检测" in text or "已检测" in text or "通过" in text or "项" in text:
            debug["excluded_numbers"].append(
                {
                    "text": text,
                    "numbers": digits,
                    "bounds": list(bounds),
                    "reason": "detection_pass_summary_not_history_repair_count",
                }
            )
            continue
        if re.fullmatch(r"\d+", text) and _same_visual_row(history_bounds, bounds) and bounds[0] >= history_bounds[0]:
            count_candidates.append(item)

    if not count_candidates:
        # Keep a targeted exclusion note for the known split-node trap even when it lies just beyond 注意事项.
        for item in ordered[history_pos + 1 : history_pos + 16]:
            text = item["text"]
            digits = re.findall(r"\d+", text)
            if digits and ("检测通过" in text or "通过" in text or "项" in text):
                if not any(existing.get("text") == text for existing in debug["excluded_numbers"]):
                    debug["excluded_numbers"].append(
                        {
                            "text": text,
                            "numbers": digits,
                            "bounds": list(item["bounds"]),
                            "reason": "detection_pass_summary_not_history_repair_count",
                        }
                    )
        debug["not_confirmed_reason"] = "history_repair_count_node_not_bound"
        return None, debug

    count_node = min(
        count_candidates,
        key=lambda item: (abs(_bounds_center_y(item["bounds"]) - _bounds_center_y(history_bounds)), abs(item["bounds"][0] - history_bounds[2])),
    )
    count_text = count_node["text"]
    debug["bound_count_text"] = count_text
    debug["bound_count_bounds"] = list(count_node["bounds"])
    debug["bind_reason"] = "same_row_pure_digit_node_next_to_history_repair"

    # Also record nearby excluded summary numbers for diagnostics.
    for item in ordered[history_pos + 1 : history_pos + 16]:
        text = item["text"]
        digits = re.findall(r"\d+", text)
        if digits and ("检测通过" in text or "通过" in text or "项" in text):
            if not any(existing.get("text") == text for existing in debug["excluded_numbers"]):
                debug["excluded_numbers"].append(
                    {
                        "text": text,
                        "numbers": digits,
                        "bounds": list(item["bounds"]),
                        "reason": "detection_pass_summary_not_history_repair_count",
                    }
                )
    return int(count_text), debug


def _extract_all_history_repair_counts(snapshot: dict[str, Any]) -> tuple[dict[str, int | None], dict[str, Any]]:
    counts: dict[str, int | None] = {}
    debug: dict[str, Any] = {
        "raw_nodes_used": True,
        "visible_texts_dedup_used": False,
        "duplicate_zero_preserved": True,
        "regions": {},
    }
    for region_name in S13_REGION_ORDER:
        count, region_debug = _extract_history_repair_count_from_nodes(snapshot, region_name)
        counts[region_name] = count
        debug["regions"][region_name] = region_debug
    return counts, debug


def _s13_region_count_failure_code(
    counts: dict[str, int | None],
    debug: dict[str, Any],
    current_region: str | None = None,
) -> str:
    regions_debug = debug.get("regions") if isinstance(debug.get("regions"), dict) else {}
    missing_regions = [region for region in S13_REGION_ORDER if counts.get(region) is None]
    if not missing_regions:
        return S13_REGION_HISTORY_COUNT_BINDING_FAILED
    for region in missing_regions:
        region_debug = regions_debug.get(region) if isinstance(regions_debug.get(region), dict) else {}
        reason = str(region_debug.get("not_confirmed_reason") or "")
        if reason == "region_header_not_found":
            return S13_REGION_HEADERS_NOT_FOUND
    return S13_REGION_HISTORY_COUNT_BINDING_FAILED


def _has_s13_history_repair_table(snapshot: dict[str, Any]) -> bool:
    counts, debug = _extract_all_history_repair_counts(snapshot)
    if any(count is not None for count in counts.values()):
        snapshot["s13_history_table_detected"] = True
        snapshot["s13_history_table_detection_token_source"] = "raw_xml_nodes_unicode_bounds"
        snapshot["s13_region_history_count_bindings"] = counts
        snapshot["s13_history_table_detection_debug"] = debug
        return True

    snapshot["s13_history_table_detected"] = False
    snapshot["s13_history_table_detection_token_source"] = "raw_xml_nodes_unicode_bounds_no_match"
    snapshot["s13_region_history_count_bindings"] = counts
    snapshot["s13_history_table_detection_debug"] = debug
    return False


def _scroll_s13_to_history_repair_table(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[dict[str, Any], int]:
    recognizer: PageRecognizer = context["recognizer"]
    if _has_s13_history_repair_table(snapshot):
        context["current_reference"]["s13_history_table_detected"] = True
        context["current_reference"]["s13_history_table_detected_frame"] = {
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
        }
        context["current_reference"]["s13_history_table_stop_scroll_reason"] = "history_repair_table_detected_before_scroll"
        context["current_reference"]["s13_history_table_detection_token_source"] = snapshot.get(
            "s13_history_table_detection_token_source"
        )
        merged_counts = _merge_s13_region_history_count_bindings(
            context["current_reference"].get("s13_region_history_count_bindings"),
            snapshot.get("s13_region_history_count_bindings"),
        )
        context["current_reference"]["s13_region_history_count_bindings"] = merged_counts
        snapshot["s13_region_history_count_bindings"] = merged_counts
        context["current_reference"]["s13_scroll_suppressed_after_table_detected"] = True
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
    point = _center(bounds)
    action_ms = contract_execute_click(
        context,
        snapshot,
        "S13",
        "tap_region_tab",
        (int(point[0]), int(point[1])),
        evidence={"region_name": region_name, "clicked_bounds": list(bounds)},
    )
    time.sleep(0.6)
    next_snapshot = _capture_with_global_popup_guard(
        context,
        f"s13_region_{region_name}",
        current_stage="S13",
    )
    return next_snapshot, action_ms, 600


def _bounds_contains(container: tuple[int, int, int, int] | None, child: tuple[int, int, int, int] | None) -> bool:
    if not _valid_bounds(container) or not _valid_bounds(child):
        return False
    return container[0] <= child[0] and container[1] <= child[1] and container[2] >= child[2] and container[3] >= child[3]


def _bounds_intersect(a: tuple[int, int, int, int] | None, b: tuple[int, int, int, int] | None, *, margin: int = 0) -> bool:
    if not _valid_bounds(a) or not _valid_bounds(b):
        return False
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return not (ax2 + margin <= bx1 or bx2 + margin <= ax1 or ay2 + margin <= by1 or by2 + margin <= ay1)


def _bounds_expand(bounds: tuple[int, int, int, int], pad: int) -> tuple[int, int, int, int]:
    return (bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad)


def _texts_within_bounds(snapshot: dict[str, Any], bounds: tuple[int, int, int, int], *, pad: int = 0, limit: int = 40) -> list[str]:
    search_bounds = _bounds_expand(bounds, pad) if pad else bounds
    labels: list[str] = []
    for node in snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or "")):
        node_bounds = node.get("bounds")
        label = _node_label(node)
        if not label or not _valid_bounds(node_bounds):
            continue
        if _bounds_intersect(search_bounds, node_bounds):
            labels.append(label)
    return labels[:limit]


def _texts_contained_within_bounds(snapshot: dict[str, Any], bounds: tuple[int, int, int, int], *, pad: int = 0, limit: int = 40) -> list[str]:
    search_bounds = _bounds_expand(bounds, pad) if pad else bounds
    labels: list[str] = []
    for node in snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or "")):
        node_bounds = node.get("bounds")
        label = _node_label(node)
        if not label or not _valid_bounds(node_bounds):
            continue
        if _bounds_contains(search_bounds, node_bounds):
            labels.append(label)
    return labels[:limit]


def _label_has_any(label: str, tokens: set[str] | list[str] | tuple[str, ...]) -> bool:
    text = str(label or "")
    return any(token and token in text for token in tokens)


def _s13_repair_item_label_matches(label: str) -> bool:
    text = re.sub(r"\s+", "", str(label or ""))
    if not text:
        return False
    if _label_has_any(text, S13_REPAIR_CLICK_FORBIDDEN_TEXTS):
        return False
    if any(token in text for token in ["检测通过", "深度检测", "历史修复", "注意事项", "AI解读", "改装"]):
        return False
    aliases: set[str] = set(S13_HISTORY_REPAIR_ENTRY_ALIASES)
    aliases.update(S14_ALLOWED_PARTS)
    for alias_group in S14_COVER_PART_ALIASES.values():
        aliases.update(alias_group)
    for alias_group in S14_SPECIAL_PART_ALIASES.values():
        aliases.update(alias_group)
    compact_aliases = {re.sub(r"\s+", "", alias) for alias in aliases if alias}
    return any(alias and alias in text for alias in compact_aliases)


def _s13_entry_label_is_forbidden(label: str) -> bool:
    text = re.sub(r"\s+", "", str(label or ""))
    if not text:
        return True
    if _label_has_any(text, S13_REPAIR_CLICK_FORBIDDEN_TEXTS):
        return True
    forbidden_tokens = {
        "检测通过",
        "深度检测",
        "历史修复",
        "注意事项",
        "AI解读",
        "改装",
        "平台车况",
        "车况解读",
        "咨询",
        "联系卖家",
        "实车讲解",
        "讲价",
        "查看报价",
    }
    if any(token in text for token in forbidden_tokens):
        return True
    if re.fullmatch(r"\d+", text):
        return True
    return False


def _s13_meaningful_entry_texts(labels: list[str]) -> list[str]:
    meaningful: list[str] = []
    seen: set[str] = set()
    for label in labels:
        text = str(label or "").strip()
        compact = re.sub(r"\s+", "", text)
        if not compact or compact in seen:
            continue
        if not re.search(r"[\u4e00-\u9fff]", compact):
            continue
        if len(compact) > 40 and re.fullmatch(r"[A-Za-z0-9+/=:_-]+", compact):
            continue
        if _s13_entry_label_is_forbidden(compact):
            continue
        seen.add(compact)
        meaningful.append(text)
    return meaningful


def _s13_region_history_entry_zone(snapshot: dict[str, Any], region_name: str) -> dict[str, Any]:
    count, debug = _extract_history_repair_count_from_nodes(snapshot, region_name)
    header_bounds = tuple(debug.get("region_header_bounds") or ()) if debug.get("region_header_bounds") else None
    history_bounds = tuple(debug.get("history_repair_label_bounds") or ()) if debug.get("history_repair_label_bounds") else None
    count_bounds = tuple(debug.get("bound_count_bounds") or ()) if debug.get("bound_count_bounds") else None
    extent = _visible_bounds_extent(snapshot)
    viewport = extent[0] if extent else (0, 0, 1220, 2712)
    ordered = _ordered_visible_node_labels(snapshot)
    normal_check_items: list[dict[str, Any]] = []
    if _valid_bounds(header_bounds):
        for item in ordered:
            text = str(item.get("text") or "")
            bounds = item.get("bounds")
            if not _valid_bounds(bounds) or bounds[1] <= header_bounds[1]:
                continue
            if ("检测通过" in text and (text.startswith(f"{region_name}：") or region_name in text)) or text.startswith(
                f"{region_name}：检测通过"
            ):
                normal_check_items.append(item)
    normal_top = min((item["bounds"][1] for item in normal_check_items), default=None)
    if _valid_bounds(header_bounds):
        zone_top = header_bounds[1]
        header_bottom = header_bounds[3]
        zone_left = max(0, min(header_bounds[0], history_bounds[0] if _valid_bounds(history_bounds) else header_bounds[0]) - 100)
    else:
        zone_top = viewport[1]
        header_bottom = viewport[1]
        zone_left = viewport[0]
    zone_right = viewport[2]
    if normal_top is not None:
        zone_bottom = normal_top
    elif _valid_bounds(header_bounds):
        zone_bottom = min(viewport[3], header_bounds[3] + 620)
    else:
        zone_bottom = viewport[3]
    return {
        "region_name": region_name,
        "history_repair_count": count,
        "parse_debug": debug,
        "region_header_bounds": list(header_bounds) if _valid_bounds(header_bounds) else None,
        "history_repair_label_bounds": list(history_bounds) if _valid_bounds(history_bounds) else None,
        "bound_count_bounds": list(count_bounds) if _valid_bounds(count_bounds) else None,
        "history_entry_zone_bounds": [int(zone_left), int(zone_top), int(zone_right), int(zone_bottom)],
        "history_entry_zone_top_y": int(zone_top),
        "history_entry_zone_bottom_y": int(zone_bottom),
        "history_entry_after_header_y": int(header_bottom),
        "normal_check_list_top_y": int(normal_top) if normal_top is not None else None,
        "normal_check_list_markers": [
            {"text": str(item.get("text") or ""), "bounds": list(item.get("bounds") or [])}
            for item in normal_check_items[:6]
        ],
    }


def _s13_candidate_in_normal_check_list(candidate_bounds: tuple[int, int, int, int], zone: dict[str, Any]) -> bool:
    normal_top = zone.get("normal_check_list_top_y")
    return normal_top is not None and candidate_bounds[1] >= int(normal_top) - 8


def _s13_history_entry_container_candidates(
    snapshot: dict[str, Any],
    region_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    zone = _s13_region_history_entry_zone(snapshot, region_name)
    zone_bounds = tuple(zone.get("history_entry_zone_bounds") or ())
    after_header_y = int(zone.get("history_entry_after_header_y") or 0)
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    extent = _visible_bounds_extent(snapshot)
    viewport = extent[0] if extent else (0, 0, 1220, 2712)
    screen_area = max(_bounds_area(viewport), 1)
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_bounds: set[tuple[int, int, int, int]] = set()
    for index, node in enumerate(nodes):
        node_bounds = node.get("bounds")
        if not node.get("clickable") or not node.get("enabled", True) or not _valid_bounds(node_bounds):
            continue
        bounds = tuple(node_bounds)
        if bounds in seen_bounds:
            continue
        seen_bounds.add(bounds)
        if not _bounds_intersect(bounds, zone_bounds):
            continue
        if bounds[1] < after_header_y - 20:
            continue
        labels = _texts_contained_within_bounds(snapshot, bounds, limit=40)
        meaningful = _s13_meaningful_entry_texts(labels)
        record = {
            "candidate_text": meaningful[0] if meaningful else "",
            "candidate_texts": meaningful,
            "candidate_bounds": list(bounds),
            "candidate_container_bounds": list(bounds),
            "candidate_node_index": index,
            "candidate_clickable": True,
            "candidate_enabled": True,
            "candidate_source": "history_repair_clickable_container",
            "candidate_zone": "history_repair_entry",
            "candidate_is_history_repair_entry": True,
            "candidate_is_normal_check_item": False,
            "candidate_rejection_reason": "",
            "candidate_container_descendant_texts": labels,
        }
        if _s13_candidate_in_normal_check_list(bounds, zone) or any("检测通过" in str(label) for label in labels):
            record["candidate_is_history_repair_entry"] = False
            record["candidate_is_normal_check_item"] = True
            record["candidate_rejection_reason"] = "normal_check_list_candidate"
            rejected.append(record)
            continue
        if not meaningful:
            record["candidate_rejection_reason"] = "no_meaningful_entry_text"
            rejected.append(record)
            continue
        area = _bounds_area(bounds)
        if area <= 0 or area > int(screen_area * 0.18):
            record["candidate_rejection_reason"] = "container_too_large_for_history_entry"
            rejected.append(record)
            continue
        candidates.append(record)
    return sorted(candidates, key=lambda item: (item["candidate_bounds"][1], item["candidate_bounds"][0])), rejected, zone


def _s13_forbidden_click_areas(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    extent = _visible_bounds_extent(snapshot)
    viewport = extent[0] if extent else (0, 0, 1220, 2712)
    screen_area = max(_bounds_area(viewport), 1)
    areas: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int, int, str]] = set()

    def add_area(bounds: tuple[int, int, int, int] | None, text: str, source: str) -> None:
        if not _valid_bounds(bounds):
            return
        key = (*bounds, text)
        if key in seen:
            return
        seen.add(key)
        areas.append({"text": text, "bounds": list(bounds), "source": source})

    forbidden_text_bounds: list[tuple[str, tuple[int, int, int, int]]] = []
    for node in nodes:
        label = _node_label(node)
        bounds = node.get("bounds")
        if not label or not _valid_bounds(bounds):
            continue
        matched = [token for token in S13_REPAIR_CLICK_FORBIDDEN_TEXTS if token in label]
        if matched:
            text = matched[0]
            add_area(bounds, text, "forbidden_text_node")
            forbidden_text_bounds.append((text, bounds))

    for node in nodes:
        node_bounds = node.get("bounds")
        if not node.get("clickable") or not node.get("enabled", True) or not _valid_bounds(node_bounds):
            continue
        if _bounds_area(node_bounds) > int(screen_area * 0.35):
            continue
        child_hits = [text for text, bounds in forbidden_text_bounds if _bounds_contains(node_bounds, bounds)]
        if child_hits:
            add_area(node_bounds, child_hits[0], "forbidden_clickable_container")
    return areas


def _s13_safe_click_region(snapshot: dict[str, Any], forbidden_areas: list[dict[str, Any]]) -> dict[str, Any]:
    extent = _visible_bounds_extent(snapshot)
    viewport, source = extent if extent else ((0, 0, 1220, 2712), "fallback_screen_bounds")
    x1, y1, x2, y2 = viewport
    height = max(y2 - y1, 1)
    bottom_tops = [
        int(area["bounds"][1])
        for area in forbidden_areas
        if area.get("bounds") and int(area["bounds"][1]) >= y1 + int(height * 0.70)
    ]
    margin = 80
    safe_bottom = min(bottom_tops) - margin if bottom_tops else y1 + int(height * 0.86)
    safe_bottom = max(y1 + int(height * 0.35), min(safe_bottom, y2 - margin))
    safe_top = y1 + max(80, int(height * 0.04))
    return {
        "viewport_bounds": list(viewport),
        "bounds_source": source,
        "safe_top_y": safe_top,
        "safe_bottom_y": safe_bottom,
        "bottom_forbidden_top_y": min(bottom_tops) if bottom_tops else None,
        "safe_margin_px": margin,
    }


def _s13_bounds_safe_for_repair_click(
    bounds: tuple[int, int, int, int] | None,
    *,
    snapshot: dict[str, Any],
    forbidden_areas: list[dict[str, Any]],
    safe_region: dict[str, Any],
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    if not _valid_bounds(bounds):
        return False, ["missing_or_invalid_bounds"], []
    safe_top = int(safe_region.get("safe_top_y") or 0)
    safe_bottom = int(safe_region.get("safe_bottom_y") or bounds[3])
    reasons: list[str] = []
    if bounds[1] < safe_top:
        reasons.append("above_safe_top")
    if bounds[3] > safe_bottom:
        reasons.append("below_safe_bottom")
    overlapped = [
        area
        for area in forbidden_areas
        if area.get("bounds") and _bounds_intersect(bounds, tuple(area["bounds"]))
    ]
    if overlapped:
        reasons.append("overlaps_forbidden_area")
    labels = _texts_within_bounds(snapshot, bounds, limit=60)
    if any(_label_has_any(label, S13_REPAIR_CLICK_FORBIDDEN_TEXTS) for label in labels):
        reasons.append("contains_forbidden_text")
    return not reasons, reasons, overlapped


def _s13_safe_row_click_option_for_candidate(
    candidate: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    forbidden_areas: list[dict[str, Any]],
    safe_region: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    item_bounds = tuple(candidate.get("candidate_bounds") or ())
    if not _valid_bounds(item_bounds):
        return None, {"safe_row_binding_attempted": False, "safe_row_rejection_reason": "missing_or_invalid_bounds"}
    if candidate.get("candidate_clickable") or not candidate.get("candidate_is_history_repair_entry", True):
        return None, {"safe_row_binding_attempted": False, "safe_row_rejection_reason": "not_required"}

    base_ok, base_reasons, base_overlaps = _s13_bounds_safe_for_repair_click(
        item_bounds,
        snapshot=snapshot,
        forbidden_areas=forbidden_areas,
        safe_region=safe_region,
    )
    if not base_ok:
        return None, {
            "safe_row_binding_attempted": True,
            "safe_row_rejection_reason": "text_bounds_unsafe",
            "safe_row_unsafe_reasons": base_reasons,
            "safe_row_forbidden_overlaps": base_overlaps,
        }

    extent = _visible_bounds_extent(snapshot)
    viewport, _source = extent if extent else ((0, 0, 1220, 2712), "fallback_screen_bounds")
    safe_top = int(safe_region.get("safe_top_y") or viewport[1])
    safe_bottom = int(safe_region.get("safe_bottom_y") or viewport[3])
    item_height = max(item_bounds[3] - item_bounds[1], 1)
    vertical_pad = max(12, int(item_height * 0.35))
    row_top = max(safe_top, item_bounds[1] - vertical_pad)
    row_bottom = min(safe_bottom, item_bounds[3] + vertical_pad)
    row_left = max(viewport[0] + 16, item_bounds[0] - 24)
    row_right = min(viewport[2] - 16, max(item_bounds[2] + 260, row_left + 96))
    row_bounds = (int(row_left), int(row_top), int(row_right), int(row_bottom))

    row_ok, row_reasons, row_overlaps = _s13_bounds_safe_for_repair_click(
        row_bounds,
        snapshot=snapshot,
        forbidden_areas=forbidden_areas,
        safe_region=safe_region,
    )
    if not row_ok:
        return None, {
            "safe_row_binding_attempted": True,
            "safe_row_rejection_reason": "row_bounds_unsafe",
            "safe_row_bounds": list(row_bounds),
            "safe_row_unsafe_reasons": row_reasons,
            "safe_row_forbidden_overlaps": row_overlaps,
        }

    return (
        {
            "selected_click_target_type": "safe_history_repair_row",
            "selected_click_bounds": list(row_bounds),
            "click_strategy": "non_clickable_text_safe_row_bounds",
            "clicked_node_clickable": False,
            "clicked_parent_clickable": False,
            "bound_to_safe_row": True,
            "bound_to_parent": False,
        },
        {
            "safe_row_binding_attempted": True,
            "safe_row_bounds": list(row_bounds),
            "safe_row_unsafe_reasons": [],
            "safe_row_forbidden_overlaps": [],
        },
    )


def _s13_repair_item_candidates(snapshot: dict[str, Any], region_name: str) -> list[dict[str, Any]]:
    history_candidates, rejected_candidates, zone = _s13_history_entry_container_candidates(snapshot, region_name)
    snapshot["s13_repair_item_rejected_candidates"] = rejected_candidates
    snapshot["s13_repair_item_history_entry_zone"] = zone
    if history_candidates:
        return history_candidates

    ordered = _ordered_visible_node_labels(snapshot)
    zone_bounds = tuple(zone.get("history_entry_zone_bounds") or ())
    after_header_y = int(zone.get("history_entry_after_header_y") or 0)
    region_text_candidates: list[dict[str, Any]] = []
    if _valid_bounds(zone_bounds):
        for item in ordered:
            label = str(item["text"] or "").strip()
            bounds = item["bounds"]
            if not _valid_bounds(bounds):
                continue
            if bounds[1] < after_header_y - 20:
                continue
            if not _bounds_intersect(bounds, zone_bounds):
                continue
            if not _s13_repair_item_label_matches(label):
                continue
            record = {
                "candidate_text": label,
                "candidate_bounds": list(bounds),
                "candidate_node_index": item["index"],
                "candidate_clickable": bool(item["node"].get("clickable")),
                "candidate_enabled": item["node"].get("enabled", True),
                "candidate_source": "history_repair_region_text_node",
                "candidate_zone": "history_repair_entry",
                "candidate_is_history_repair_entry": True,
                "candidate_is_normal_check_item": False,
                "candidate_texts": [label],
                "candidate_container_bounds": None,
                "requires_safe_row_binding": not bool(item["node"].get("clickable")),
                "normal_check_list_boundary_detected": zone.get("normal_check_list_top_y") is not None,
            }
            if _s13_candidate_in_normal_check_list(bounds, zone):
                record["candidate_is_history_repair_entry"] = False
                record["candidate_is_normal_check_item"] = True
                record["candidate_rejection_reason"] = "normal_check_list_candidate"
                rejected_candidates.append(record)
                continue
            region_text_candidates.append(record)
    if region_text_candidates:
        snapshot["s13_repair_item_rejected_candidates"] = rejected_candidates
        return sorted(region_text_candidates, key=lambda item: (item["candidate_bounds"][1], item["candidate_bounds"][0]))

    header_items = [
        item
        for item in ordered
        if str(item["text"]).startswith(f"{region_name}深度检测")
    ]
    header_bottom = min((item["bounds"][3] for item in header_items), default=0)
    candidates: list[dict[str, Any]] = []
    for item in ordered:
        label = str(item["text"] or "").strip()
        bounds = item["bounds"]
        if bounds[1] < header_bottom:
            continue
        if not _s13_repair_item_label_matches(label):
            continue
        record = {
            "candidate_text": label,
            "candidate_bounds": list(bounds),
            "candidate_node_index": item["index"],
            "candidate_clickable": bool(item["node"].get("clickable")),
            "candidate_enabled": item["node"].get("enabled", True),
            "candidate_source": "text_label_fallback",
            "candidate_zone": "history_repair_entry_fallback",
            "candidate_is_history_repair_entry": True,
            "candidate_is_normal_check_item": False,
            "candidate_texts": [label],
            "candidate_container_bounds": None,
        }
        if _s13_candidate_in_normal_check_list(bounds, zone):
            record["candidate_is_history_repair_entry"] = False
            record["candidate_is_normal_check_item"] = True
            record["candidate_rejection_reason"] = "normal_check_list_candidate"
            rejected_candidates.append(record)
            continue
        candidates.append(
            record
        )
    snapshot["s13_repair_item_rejected_candidates"] = rejected_candidates
    return sorted(candidates, key=lambda item: (item["candidate_bounds"][1], item["candidate_bounds"][0]))


def _s13_find_repair_item_click_target(
    snapshot: dict[str, Any],
    region_name: str,
    history_repair_count: int,
) -> dict[str, Any]:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    forbidden_areas = _s13_forbidden_click_areas(snapshot)
    safe_region = _s13_safe_click_region(snapshot, forbidden_areas)
    candidates = _s13_repair_item_candidates(snapshot, region_name)
    rejected_candidates = list(snapshot.get("s13_repair_item_rejected_candidates") or [])
    history_entry_zone = dict(snapshot.get("s13_repair_item_history_entry_zone") or {})
    screen_area = _bounds_area(tuple(safe_region.get("viewport_bounds") or [0, 0, 1220, 2712]))
    evaluated: list[dict[str, Any]] = []

    for candidate in candidates:
        item_bounds = tuple(candidate["candidate_bounds"])
        base_ok, base_reasons, base_overlaps = _s13_bounds_safe_for_repair_click(
            item_bounds,
            snapshot=snapshot,
            forbidden_areas=forbidden_areas,
            safe_region=safe_region,
        )
        candidate_record = {
            **candidate,
            "candidate_safe_click_region": base_ok,
            "candidate_unsafe_reasons": base_reasons,
            "candidate_forbidden_overlaps": base_overlaps,
        }

        click_options: list[tuple[int, dict[str, Any]]] = []
        if candidate.get("candidate_clickable") and candidate.get("candidate_enabled", True) and base_ok:
            click_options.append(
                (
                    _bounds_area(item_bounds),
                    {
                        "selected_click_target_type": (
                            "history_repair_clickable_container"
                            if candidate.get("candidate_source") == "history_repair_clickable_container"
                            else "repair_item_text_node"
                        ),
                        "selected_click_bounds": list(item_bounds),
                        "click_strategy": (
                            "history_repair_clickable_container_bounds"
                            if candidate.get("candidate_source") == "history_repair_clickable_container"
                            else "repair_item_text_node_bounds"
                        ),
                        "clicked_node_clickable": True,
                        "clicked_parent_clickable": False,
                    },
                )
            )

        for node in nodes:
            node_bounds = node.get("bounds")
            if not node.get("clickable") or not node.get("enabled", True):
                continue
            if not _bounds_contains(node_bounds, item_bounds):
                continue
            area = _bounds_area(node_bounds)
            if area <= 0 or area > int(screen_area * 0.20):
                continue
            parent_ok, parent_reasons, parent_overlaps = _s13_bounds_safe_for_repair_click(
                node_bounds,
                snapshot=snapshot,
                forbidden_areas=forbidden_areas,
                safe_region=safe_region,
            )
            if not parent_ok:
                candidate_record.setdefault("rejected_parent_options", []).append(
                    {
                        "bounds": list(node_bounds),
                        "unsafe_reasons": parent_reasons,
                        "forbidden_overlaps": parent_overlaps,
                    }
                )
                continue
            click_options.append(
                (
                    area,
                    {
                        "selected_click_target_type": "nearest_clickable_repair_item_container",
                        "selected_click_bounds": list(node_bounds),
                        "click_strategy": "nearest_clickable_repair_item_parent_bounds",
                        "clicked_node_clickable": bool(candidate.get("candidate_clickable")),
                        "clicked_parent_clickable": True,
                        "bound_to_parent": True,
                        "bound_to_safe_row": False,
                    },
                )
            )

        safe_row_selected, safe_row_debug = _s13_safe_row_click_option_for_candidate(
            candidate,
            snapshot=snapshot,
            forbidden_areas=forbidden_areas,
            safe_region=safe_region,
        )
        candidate_record.update(safe_row_debug)
        if safe_row_selected:
            row_area = _bounds_area(tuple(safe_row_selected["selected_click_bounds"]))
            click_options.append((row_area, safe_row_selected))

        if click_options:
            selected = sorted(click_options, key=lambda item: item[0])[0][1]
            selected_bounds = tuple(selected["selected_click_bounds"])
            nearby = _texts_within_bounds(snapshot, selected_bounds, pad=120, limit=40)
            forbidden_nearby = [text for text in nearby if _label_has_any(text, S13_REPAIR_CLICK_FORBIDDEN_TEXTS)]
            audit = {
                "s13_current_region": region_name,
                "s13_history_repair_count": history_repair_count,
                "repair_item_candidates": evaluated + [candidate_record],
                "s13_repair_item_rejected_candidates": rejected_candidates,
                "s13_history_entry_zone": history_entry_zone,
                "selected_repair_item_text": candidate["candidate_text"],
                "s13_repair_item_selected_entry_text": candidate["candidate_text"],
                "selected_repair_item_bounds": candidate["candidate_bounds"],
                "s13_repair_item_candidate_source": candidate.get("candidate_source"),
                "s13_repair_item_candidate_zone": candidate.get("candidate_zone"),
                "s13_repair_item_candidate_is_history_repair_entry": bool(candidate.get("candidate_is_history_repair_entry")),
                "s13_repair_item_candidate_is_normal_check_item": bool(candidate.get("candidate_is_normal_check_item")),
                "s13_repair_item_candidate_texts": candidate.get("candidate_texts") or [candidate.get("candidate_text")],
                "s13_repair_item_candidate_container_bounds": candidate.get("candidate_container_bounds"),
                "selected_history_repair_entry": candidate["candidate_text"],
                "selected_entry_source": candidate.get("candidate_source"),
                "requires_safe_row_binding": bool(candidate.get("requires_safe_row_binding")),
                "normal_check_list_boundary_detected": history_entry_zone.get("normal_check_list_top_y") is not None,
                "normal_check_list_excluded": bool(rejected_candidates),
                "selected_click_target_type": selected["selected_click_target_type"],
                "selected_click_bounds": selected["selected_click_bounds"],
                "s13_repair_item_click_target_bounds": selected["selected_click_bounds"],
                "selected_repair_item_click_bounds": selected["selected_click_bounds"],
                "selected_repair_item_click_strategy": selected["click_strategy"],
                "s13_repair_item_clickable_container_selected": selected["selected_click_target_type"]
                in {"history_repair_clickable_container", "nearest_clickable_repair_item_container"},
                "s13_repair_item_clickable_container_binding_method": selected["click_strategy"],
                "click_strategy": selected["click_strategy"],
                "clicked_node_clickable": selected["clicked_node_clickable"],
                "clicked_parent_clickable": selected["clicked_parent_clickable"],
                "bound_to_safe_row": bool(selected.get("bound_to_safe_row")),
                "bound_to_parent": bool(selected.get("bound_to_parent")),
                "clicked_nearby_texts": nearby,
                "forbidden_nearby_texts": forbidden_nearby,
                "safe_click_region": not forbidden_nearby,
                "safe_region": safe_region,
                "forbidden_click_areas": forbidden_areas,
            }
            if forbidden_nearby:
                return {
                    "ok": False,
                    "code": "S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE",
                    "audit": audit,
                    "reason": "selected_click_target_has_forbidden_nearby_texts",
                }
            return {"ok": True, "audit": audit, "click_bounds": selected_bounds}
        evaluated.append(candidate_record)

    audit = {
        "s13_current_region": region_name,
        "s13_history_repair_count": history_repair_count,
        "repair_item_candidates": evaluated,
        "s13_repair_item_rejected_candidates": rejected_candidates,
        "s13_history_entry_zone": history_entry_zone,
        "selected_repair_item_text": "",
        "selected_repair_item_bounds": None,
        "s13_repair_item_candidate_source": "",
        "s13_repair_item_candidate_zone": "",
        "s13_repair_item_candidate_is_history_repair_entry": False,
        "s13_repair_item_candidate_is_normal_check_item": False,
        "s13_repair_item_candidate_texts": [],
        "s13_repair_item_candidate_container_bounds": None,
        "selected_history_repair_entry": "",
        "selected_entry_source": "",
        "requires_safe_row_binding": False,
        "normal_check_list_boundary_detected": history_entry_zone.get("normal_check_list_top_y") is not None,
        "normal_check_list_excluded": bool(rejected_candidates),
        "s13_repair_item_clickable_container_selected": False,
        "s13_repair_item_clickable_container_binding_method": "",
        "s13_repair_item_selected_entry_text": "",
        "s13_repair_item_click_target_bounds": None,
        "selected_repair_item_click_bounds": None,
        "selected_repair_item_click_strategy": "",
        "selected_click_target_type": "",
        "selected_click_bounds": None,
        "click_strategy": "",
        "clicked_node_clickable": False,
        "clicked_parent_clickable": False,
        "bound_to_safe_row": False,
        "bound_to_parent": False,
        "clicked_nearby_texts": [],
        "forbidden_nearby_texts": [],
        "safe_click_region": False,
        "safe_region": safe_region,
        "forbidden_click_areas": forbidden_areas,
    }
    code = "S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND"
    if any(item.get("candidate_forbidden_overlaps") for item in evaluated):
        code = "S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE"
    elif any(item.get("safe_row_unsafe_reasons") or item.get("safe_row_forbidden_overlaps") for item in evaluated):
        code = "S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE"
    elif not evaluated and any(item.get("candidate_is_normal_check_item") for item in rejected_candidates):
        code = "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED"
    elif not evaluated and any(item.get("candidate_rejection_reason") == "no_meaningful_entry_text" for item in rejected_candidates):
        code = "S13_REPAIR_ITEM_CLICKABLE_CONTAINER_BINDING_FAILED"
    elif not evaluated:
        code = "S13_HISTORY_REPAIR_ENTRY_NOT_VISIBLE_OR_UNBOUND"
    return {"ok": False, "code": code, "audit": audit, "reason": "no_safe_repair_item_click_target"}


def _s13_repair_item_needs_reposition(click_target: dict[str, Any]) -> bool:
    if click_target.get("ok"):
        return False
    audit = click_target.get("audit") or {}
    candidates = audit.get("repair_item_candidates") or []
    if not candidates:
        return False
    unsafe_tokens = {"below_safe_bottom", "missing_or_invalid_bounds"}
    for item in candidates:
        reasons = set(item.get("candidate_unsafe_reasons") or [])
        if reasons & unsafe_tokens:
            return True
        for parent in item.get("rejected_parent_options") or []:
            if set(parent.get("unsafe_reasons") or []) & unsafe_tokens:
                return True
    return False


def _s13_repair_item_reposition_points(snapshot: dict[str, Any]) -> dict[str, int | float | str]:
    extent = _visible_bounds_extent(snapshot)
    viewport, source = extent if extent else ((0, 0, 1220, 2712), "fallback_screen_bounds")
    x1, y1, x2, y2 = viewport
    height = max(y2 - y1, 1)
    distance = max(180, min(int(height * 0.13), 360))
    start_y = y1 + int(height * 0.78)
    end_y = max(y1 + int(height * 0.45), start_y - distance)
    return {
        "viewport_bounds": list(viewport),
        "bounds_source": source,
        "swipe_x_start": int((x1 + x2) / 2),
        "swipe_y_start": int(start_y),
        "swipe_x_end": int((x1 + x2) / 2),
        "swipe_y_end": int(end_y),
        "swipe_distance_px": int(abs(start_y - end_y)),
        "swipe_distance_ratio": round(abs(start_y - end_y) / height, 4),
        "swipe_duration_ms": 700,
    }


def _s13_reposition_repair_item_until_safe(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    region_name: str,
    history_repair_count: int,
    click_target: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not _s13_repair_item_needs_reposition(click_target):
        return snapshot, click_target

    client: AdbClient = context["client"]
    recognizer: PageRecognizer = context["recognizer"]
    timing: TimingRecorder = context["timing"]
    current = snapshot
    current_target = click_target
    before_audit = dict((click_target.get("audit") or {}))
    before_candidates = before_audit.get("repair_item_candidates") or []
    before_bounds = None
    if before_candidates:
        before_bounds = before_candidates[0].get("candidate_bounds")
    current_reference = context.setdefault("current_reference", {})
    current_reference.update(
        {
            "s13_repair_item_visible_but_unsafe": True,
            "s13_repair_item_unsafe_reason": str(click_target.get("reason") or ""),
            "s13_repair_item_reposition_attempted": True,
            "s13_repair_item_reposition_count": 0,
            "s13_repair_item_before_bounds": before_bounds,
            "s13_repair_item_after_bounds": None,
            "s13_repair_item_safe_after_reposition": False,
            "s13_repair_item_click_target_bounds": None,
            "s13_repair_item_reposition_lost_context": False,
        }
    )

    for attempt in range(1, 3):
        points = _s13_repair_item_reposition_points(current)
        _, action_ms = contract_execute_swipe(
            context,
            current,
            "S13",
            "reposition_repair_item_entry",
            (
                int(points["swipe_x_start"]),
                int(points["swipe_y_start"]),
                int(points["swipe_x_end"]),
                int(points["swipe_y_end"]),
                int(points["swipe_duration_ms"]),
            ),
            evidence={
                **points,
                "s13_repair_item_reposition_attempt_index": attempt,
                "region_name": region_name,
                "history_repair_count": history_repair_count,
                "s13_repair_item_before_bounds": before_bounds,
            },
        )
        timing.add(
            step_name="S13_REPAIR_ITEM_UNSAFE_REPOSITION_SCROLL",
            page_name="S13",
            action_name="reposition_repair_item_entry",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=action_ms,
            transition_wait_ms=0,
            screenshot_path=str(current.get("screenshot_path") or ""),
            xml_path=str(current.get("xml_path") or ""),
            extra={
                **points,
                "s13_repair_item_reposition_attempt_index": attempt,
                "region_name": region_name,
                "history_repair_count": history_repair_count,
            },
        )
        time.sleep(0.35)
        fresh_start = time.perf_counter()
        current = _capture_with_global_popup_guard(
            context,
            f"s13_repair_item_reposition_{region_name}_{attempt}",
            current_stage="S13",
        )
        fresh_ms = int((time.perf_counter() - fresh_start) * 1000)
        recognized = _recognize_mainline_page(recognizer, current)
        counts_summary, counts_debug = _extract_all_history_repair_counts(current) if recognized == "S13" else ({}, {})
        context_ok = (
            recognized == "S13"
            and _has_s13_history_repair_table(current)
            and counts_summary.get(region_name) == history_repair_count
            and history_repair_count > 0
        )
        timing.add(
            step_name="S13_REPAIR_ITEM_UNSAFE_REPOSITION_FRESH",
            page_name="S13",
            action_name="fresh_after_repair_item_reposition",
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=fresh_ms,
            screenshot_path=str(current.get("screenshot_path") or ""),
            xml_path=str(current.get("xml_path") or ""),
            extra={
                "s13_repair_item_reposition_attempt_index": attempt,
                "recognized_page": recognized,
                "s13_history_table_detected": bool(context_ok),
                "s13_region_history_count_bindings": counts_summary,
                "s13_counts_parse_debug": counts_debug,
            },
        )
        current_reference["s13_repair_item_reposition_count"] = attempt
        if not context_ok:
            audit = dict((current_target.get("audit") or {}))
            audit.update(
                {
                    "s13_repair_item_reposition_attempted": True,
                    "s13_repair_item_reposition_count": attempt,
                    "s13_repair_item_reposition_lost_context": True,
                    "after_reposition_recognized_page": recognized,
                    "after_reposition_counts": counts_summary,
                    "after_reposition_screenshot_path": str(current.get("screenshot_path") or ""),
                    "after_reposition_xml_path": str(current.get("xml_path") or ""),
                }
            )
            current_reference["s13_repair_item_reposition_lost_context"] = True
            return current, {
                "ok": False,
                "code": "S13_REPAIR_DETAIL_REPOSITION_LOST_CONTEXT",
                "audit": audit,
                "reason": "lost_s13_history_repair_context_after_reposition",
            }

        current_target = _s13_find_repair_item_click_target(current, region_name, history_repair_count)
        audit = dict((current_target.get("audit") or {}))
        after_candidates = audit.get("repair_item_candidates") or []
        after_bounds = after_candidates[0].get("candidate_bounds") if after_candidates else None
        audit.update(
            {
                "s13_repair_item_reposition_attempted": True,
                "s13_repair_item_reposition_count": attempt,
                "s13_repair_item_before_bounds": before_bounds,
                "s13_repair_item_after_bounds": after_bounds,
                "s13_repair_item_reposition_lost_context": False,
            }
        )
        current_target["audit"] = audit
        current_reference["s13_repair_item_after_bounds"] = after_bounds
        current_reference["s13_repair_item_safe_after_reposition"] = bool(current_target.get("ok"))
        current_reference["s13_repair_item_click_target_bounds"] = current_target.get("click_bounds")
        if current_target.get("ok"):
            return current, current_target
        if not _s13_repair_item_needs_reposition(current_target):
            return current, current_target

    audit = dict((current_target.get("audit") or {}))
    audit.update(
        {
            "s13_repair_item_reposition_attempted": True,
            "s13_repair_item_reposition_count": int(current_reference.get("s13_repair_item_reposition_count") or 2),
            "s13_repair_item_safe_after_reposition": False,
        }
    )
    current_target["audit"] = audit
    current_target["code"] = str(current_target.get("code") or "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED")
    return current, current_target


def _s13_live_room_signals(snapshot: dict[str, Any]) -> list[str]:
    text = str(snapshot.get("visible_blob") or "")
    return [signal for signal in S13_LIVE_ROOM_SIGNALS if signal in text]


def _s13_return_to_reliable_s10_transaction(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    current_reference: dict[str, Any],
    expected_next_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hook = context.get("s13_return_to_s10_transaction_hook")
    if callable(hook):
        result = dict(hook(context, snapshot, current_reference, expected_next_reference) or {})
        result.setdefault("transaction_name", "s13_return_to_reliable_s10_transaction")
        result.setdefault("mocked", True)
        context.setdefault("s13_return_to_s10_transaction_traces", []).append(dict(result))
        return result
    client: AdbClient | None = context.get("client")
    recognizer: PageRecognizer | None = context.get("recognizer")
    timing: TimingRecorder | None = context.get("timing")
    if client is None or recognizer is None:
        result = {
            "ok": False,
            "stop_code": S13_RETURN_TO_S10_ACTION_NOT_EXECUTED,
            "reason": "client_or_recognizer_missing",
            "attempts": [],
            "return_to_reliable_s10_verified": False,
        }
        context.setdefault("s13_return_to_s10_transaction_traces", []).append(dict(result))
        return result
    reference_index = _safe_int(current_reference.get("reference_index") or context.get("current_reference_index"), default=0)
    next_reference_index = reference_index + 1 if reference_index > 0 else _safe_int(context.get("next_reference_index"), default=0)
    attempts: list[dict[str, Any]] = []
    last_state = ""
    last_snapshot: dict[str, Any] | None = None
    last_reliable_evidence: dict[str, Any] = {}
    for attempt_index in range(1, 4):
        action_started = time.perf_counter()
        action_error = ""
        try:
            action_result = client.back()
            action_success = bool(getattr(action_result, "success", True))
        except Exception as exc:  # pragma: no cover - defensive around device wrappers.
            action_success = False
            action_error = str(exc)
        action_ms = int((time.perf_counter() - action_started) * 1000)
        time.sleep(0.45)
        fresh_started = time.perf_counter()
        after_snapshot = _capture_with_global_popup_guard(
            context,
            f"s13_all_zero_return_s10_{attempt_index}",
            current_stage="S13_RETURN_TO_S10",
        )
        fresh_ms = int((time.perf_counter() - fresh_started) * 1000)
        if context.get("target_car"):
            after_snapshot["target_brand"] = context["target_car"].brand
            after_snapshot["target_car"] = {
                "brand": context["target_car"].brand,
                "series": context["target_car"].series,
                "model_year": context["target_car"].model_year,
                "trim": context["target_car"].trim,
            }
        state = _recognize_mainline_page(recognizer, after_snapshot)
        reliable_evidence: dict[str, Any] = {}
        if state == "S10":
            reliable_evidence = _s10_reliable_list_evidence(
                after_snapshot,
                target_reference_index=next_reference_index,
                expected_card=expected_next_reference or {},
            )
        attempt = {
            "attempt_index": attempt_index,
            "action_success": action_success,
            "action_error": action_error,
            "recognized_page": state,
            "action_ms": action_ms,
            "fresh_ms": fresh_ms,
            "current_reference_index": reference_index,
            "next_reference_index": next_reference_index,
            "s10_reliable_list_evidence": reliable_evidence,
            "screenshot_path": str(after_snapshot.get("screenshot_path") or ""),
            "xml_path": str(after_snapshot.get("xml_path") or ""),
        }
        attempts.append(attempt)
        if timing is not None:
            timing.add(
                step_name="S13_ALL_ZERO_RETURN_TO_RELIABLE_S10",
                page_name="S13",
                action_name="return_to_s10_if_all_zero",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=action_ms,
                transition_wait_ms=450 + fresh_ms,
                screenshot_path=str(after_snapshot.get("screenshot_path") or ""),
                xml_path=str(after_snapshot.get("xml_path") or ""),
                extra=attempt,
            )
        last_state = state
        last_snapshot = after_snapshot
        last_reliable_evidence = reliable_evidence
        if state == "S10" and reliable_evidence.get("reliable") is True:
            result = {
                "ok": True,
                "stop_code": "",
                "attempts": attempts,
                "snapshot": after_snapshot,
                "recognized_page": state,
                "return_to_reliable_s10_verified": True,
                "s10_reliable_list_evidence": reliable_evidence,
                "next_reference_index": next_reference_index,
            }
            context.setdefault("s13_return_to_s10_transaction_traces", []).append({k: v for k, v in result.items() if k != "snapshot"})
            return result
    if not attempts:
        stop_code = S13_RETURN_TO_S10_ACTION_NOT_EXECUTED
    elif last_state == "S13":
        stop_code = S13_RETURN_ACTION_EXECUTED_BUT_STILL_ON_S13
    elif last_state and last_state != "S10":
        stop_code = S13_RETURN_ACTION_LANDED_ON_NON_S10_PAGE
    else:
        stop_code = S13_RETURN_TO_S10_PROOF_STALE_OR_MISSING
    result = {
        "ok": False,
        "stop_code": stop_code,
        "attempts": attempts,
        "snapshot": last_snapshot or snapshot,
        "recognized_page": last_state,
        "return_to_reliable_s10_verified": False,
        "s10_reliable_list_evidence": last_reliable_evidence,
        "next_reference_index": next_reference_index,
    }
    context.setdefault("s13_return_to_s10_transaction_traces", []).append({k: v for k, v in result.items() if k != "snapshot"})
    return result


def _finish_s13_all_zero_with_reliable_s10_return(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    scan_state: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    machine: PageStateMachine = context["machine"]
    issues: IssueRecorder = context["issues"]
    current_reference = context.setdefault("current_reference", {})
    context["s13_enter_s14_required"] = False
    context["s14_full_image_sequence_required"] = False
    context["return_to_reliable_s10_required"] = True
    context["next_internal_state"] = "S15"
    current_reference.update(
        {
            "s13_history_repair_confirmed": True,
            "s13_region_scan_exit_reason": "ALL_REGIONS_ZERO",
            "s13_all_zero_exit_trace": S13_ALL_ZERO_RETURN_TO_S10_FOR_S15,
            "s15_entry_reason": "NO_HISTORY_REPAIR_COUNT_S14_NOT_REQUIRED",
            "return_to_reliable_s10_required": True,
            "next_internal_state": "S15",
        }
    )
    if scan_state:
        current_reference.update(
            {
                "all_regions_checked": bool(scan_state.get("all_regions_checked")),
                "s13_all_zero": bool(scan_state.get("s13_all_zero")),
                "s13_region_scan_state_persisted": True,
            }
        )
    _store_repair_item_completion_state(context)
    machine.assert_action_allowed("S13", "return_to_s10_if_all_zero")
    reference_index = _safe_int(current_reference.get("reference_index") or context.get("current_reference_index"), default=0)
    expected_next_reference = _first_stage_expected_reference_card(context.get("first_stage_evidence") or {}, reference_index + 1)
    transaction = _s13_return_to_reliable_s10_transaction(
        context,
        snapshot,
        current_reference=current_reference,
        expected_next_reference=expected_next_reference,
    )
    current_reference["s13_return_to_s10_physical_transaction"] = {k: v for k, v in transaction.items() if k != "snapshot"}
    if transaction.get("ok") is not True:
        code = str(transaction.get("stop_code") or S13_RETURN_TO_S10_PROOF_STALE_OR_MISSING)
        issue = issues.record(
            code,
            "S13",
            "S13 all-zero exit requires a physical return to reliable S10 before S15/continuation can advance.",
            {
                **(transaction.get("snapshot") or snapshot),
                "current_reference": current_reference,
                "s13_return_to_s10_physical_transaction": current_reference.get("s13_return_to_s10_physical_transaction"),
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    returned_snapshot = transaction.get("snapshot") if isinstance(transaction.get("snapshot"), dict) else snapshot
    current_reference.update(
        {
            "return_to_reliable_s10_verified": True,
            "returned_list_source": "s13_all_zero_physical_return",
            "returned_list_source_verified": True,
            "next_reference_index": transaction.get("next_reference_index"),
        }
    )
    proof = current_reference.get("physical_ui_transition_proof") if isinstance(current_reference.get("physical_ui_transition_proof"), dict) else {}
    if proof:
        proof = dict(proof)
        proof["return_to_reliable_s10_verified"] = True
        proof["s13_return_to_s10_physical_transaction_ok"] = True
        current_reference["physical_ui_transition_proof"] = proof
    context["returned_s10_snapshot"] = returned_snapshot
    context["returned_s10_snapshot_source"] = "S13_ALL_ZERO_RETURN_TO_S10_FOR_S15"
    context["returned_s10_reliable_evidence"] = transaction.get("s10_reliable_list_evidence")
    context["returned_list_source"] = "s13_all_zero_physical_return"
    context["returned_list_source_verified"] = True
    return "S15", returned_snapshot


def handle_s13(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    machine: PageStateMachine = context["machine"]
    timing: TimingRecorder = context["timing"]
    client: AdbClient = context["client"]
    snapshot = _maybe_close_guazi_push_popup_and_resume(context, snapshot, current_stage="S13")
    _ensure_page("S13", recognizer, issues, snapshot)
    start = time.perf_counter()
    machine.assert_action_allowed("S13", "collect_repair_counts")
    snapshot, pending_scroll_ms = _scroll_s13_to_history_repair_table(context, snapshot)
    if _recognize_mainline_page(recognizer, snapshot) != "S13":
        issue = issues.record("HISTORY_REPAIR_COUNT_UNCERTAIN", "S13", "S13 did not remain stable while scrolling to history repair table.", snapshot, "manual_review")
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    initial_scan_state = _s13_region_scan_state(context)
    if initial_scan_state.get("all_regions_checked") and initial_scan_state.get("s13_all_zero"):
        _persist_s13_region_scan_state(context, initial_scan_state)
        return _finish_s13_all_zero_with_reliable_s10_return(context, snapshot, scan_state=initial_scan_state)
    for region_name in S13_REGION_ORDER:
        loop_guard = _s13_four_region_loop_guard(context, region_name)
        if loop_guard.get("blocked"):
            issue = issues.record(
                S13_FOUR_REGION_LOOP_GUARD_TRIGGERED,
                "S13",
                "S13 four-region scan is attempting to restart after a completed scan state.",
                {**snapshot, "s13_four_region_loop_guard": loop_guard},
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        scan_state = _s13_region_scan_state(context)
        if region_name in set(scan_state.get("completed_regions") or []):
            existing_count = (scan_state.get("s13_region_history_count_bindings") or {}).get(region_name)
            if existing_count is not None and _safe_non_negative_int(existing_count) == 0:
                pending_scroll_ms = 0
                continue
        snapshot, tab_action_ms, tab_wait_ms = _tap_s13_region_tab(context, snapshot, region_name)
        snapshot, table_scroll_ms = _scroll_s13_to_history_repair_table(context, snapshot)
        read_start = time.perf_counter()
        counts_summary, counts_debug = _extract_all_history_repair_counts(snapshot)
        count = counts_summary.get(region_name)
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
            extra={
                "s13_count_parse_once": True,
                "s13_count_xml_parse_count": 1,
                "s13_counts_summary": counts_summary,
                "s13_counts_parse_debug": counts_debug,
                "raw_nodes_used": True,
                "visible_texts_dedup_used": False,
                "duplicate_zero_preserved": True,
            },
        )
        pending_scroll_ms = 0
        if count is None:
            partial_state = _s13_region_scan_state(context)
            issue = issues.record(
                "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED",
                "S13",
                f"Failed to confirm history repair count for {region_name}.",
                {
                    **snapshot,
                    "region_name": region_name,
                    "completed_regions": partial_state.get("completed_regions"),
                    "visited_regions": partial_state.get("visited_regions"),
                    "partial_bindings": partial_state.get("s13_region_history_count_bindings"),
                    "unknown_region_reason": (counts_debug.get("regions", {}).get(region_name) or {}).get("not_confirmed_reason")
                    or "history_repair_count_node_not_bound",
                    "s13_counts_parse_debug": counts_debug,
                },
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        scan_state = _record_s13_region_count(
            context,
            region_name,
            count,
            counts_summary=counts_summary,
            counts_debug=counts_debug,
            snapshot=snapshot,
        )
        context["current_reference"].setdefault("repair_counts", {})[region_name] = count
        context["current_reference"].setdefault("repair_count_parse_debug", {})[region_name] = counts_debug["regions"].get(region_name)
        _store_repair_item_completion_state(context)
        if count == 0:
            continue
        context["s13_first_positive_region"] = region_name
        context["s13_first_positive_region_repair_count"] = count
        context["s13_enter_s14_required"] = True
        context["s14_full_image_sequence_required"] = True
        context["current_reference"].update(
            {
                "overall_contract_version": "V1.32",
                "execution_contract_version": "V1.32",
                "s13_repair_count_role": "entry_signal_only",
                "s13_region_check_order": list(S13_REGION_ORDER),
                "s13_first_positive_region": region_name,
                "s13_first_positive_region_repair_count": count,
                "s13_enter_s14_required": True,
                "s14_full_image_sequence_required": True,
                "s13_v1_32_stopped_region_scan_after_first_positive": True,
                "s13_regions_not_checked_after_first_positive": [
                    later_region for later_region in S13_REGION_ORDER[S13_REGION_ORDER.index(region_name) + 1 :]
                ],
            }
        )
        machine.assert_action_allowed("S13", "tap_repair_item_if_nonzero")
        click_target = _s13_find_repair_item_click_target(snapshot, region_name, count)
        if not click_target.get("ok") and _s13_repair_item_needs_reposition(click_target):
            snapshot, click_target = _s13_reposition_repair_item_until_safe(context, snapshot, region_name, count, click_target)
        click_audit = dict(click_target.get("audit") or {})
        context["current_reference"]["s13_s14_entry_item_text"] = click_audit.get("selected_repair_item_text") or ""
        context["current_reference"].setdefault("s13_to_s14_click_audits", []).append(click_audit)
        context["current_reference"]["last_s13_to_s14_click_audit"] = click_audit
        if not click_target.get("ok"):
            context["exclude_current_reference_from_history"] = True
            issue_code = str(click_target.get("code") or "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED")
            issue = issues.record(
                issue_code,
                "S13",
                f"No safe repair item click target confirmed for {region_name}; refusing S13 to S14 click.",
                {**snapshot, "region_name": region_name, "s13_to_s14_click_audit": click_audit},
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        repair_click_point = _center(click_target["click_bounds"])
        action_ms = contract_execute_click(
            context,
            snapshot,
            "S13",
            "tap_repair_item_if_nonzero",
            (int(repair_click_point[0]), int(repair_click_point[1])),
            evidence={
                "region_name": region_name,
                "click_bounds": click_target.get("click_bounds"),
                "selected_repair_item_text": click_audit.get("selected_repair_item_text"),
                "s13_to_s14_click_audit": click_audit,
            },
        )
        wait_start = time.perf_counter()
        next_snapshot: dict[str, Any] = {}
        wait_rounds: list[dict[str, Any]] = []
        final_state: str | None = None
        page_changed_after_repair_click = False
        before_visible_hash = _sha256_text("|".join(str(item) for item in snapshot.get("visible_texts", [])))
        before_xml_hash = _sha256_file(snapshot.get("xml_path"))
        for wait_round_index in range(1, 4):
            time.sleep(0.8 if wait_round_index > 1 else 1.0)
            next_snapshot = _capture_with_global_popup_guard(
                context,
                f"s13_to_s14_{region_name}",
                current_stage="S14",
            )
            after_visible_hash = _sha256_text("|".join(str(item) for item in next_snapshot.get("visible_texts", [])))
            after_xml_hash = _sha256_file(next_snapshot.get("xml_path"))
            page_changed_after_repair_click = after_visible_hash != before_visible_hash or after_xml_hash != before_xml_hash
            live_signals = _s13_live_room_signals(next_snapshot)
            s14_signals = _s14_candidate_signals(next_snapshot)
            recognized = "LIVE_ROOM" if live_signals else _recognize_mainline_page(recognizer, next_snapshot)
            wait_round = {
                "wait_round_index": wait_round_index,
                "page_changed_after_repair_click": page_changed_after_repair_click,
                "after_click_recognized_page": recognized,
                "after_click_visible_text_digest": list(next_snapshot.get("visible_texts", []))[:40],
                "live_room_signals_detected": bool(live_signals),
                "live_room_signals": live_signals,
                "s14_candidate_signals": s14_signals,
                "after_click_xml_path": str(next_snapshot.get("xml_path") or ""),
                "after_click_screenshot_path": str(next_snapshot.get("screenshot_path") or ""),
            }
            wait_rounds.append(wait_round)
            click_audit.update(wait_round)
            click_audit["page_changed_after_repair_click"] = page_changed_after_repair_click
            click_audit["after_click_wait_rounds"] = wait_rounds
            if live_signals:
                context["exclude_current_reference_from_history"] = True
                context["invalid_reason"] = "S13_TO_S14_LIVE_ROOM_ENTERED_AFTER_CLICK"
                context["current_reference"]["s13_to_s14_click_audits"][-1] = click_audit
                context["current_reference"]["last_s13_to_s14_click_audit"] = click_audit
                issue = issues.record(
                    "S13_TO_S14_LIVE_ROOM_ENTERED_AFTER_CLICK",
                    "S13",
                    "S13 repair item click navigated to live-room / real-car-explanation page; refusing to treat it as S14.",
                    {**next_snapshot, "region_name": region_name, "s13_to_s14_click_audit": click_audit, "recovery_required": "APP_FORCE_RESTART_TO_S10_READY"},
                    "manual_review",
                )
                raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
            if recognized == "S14":
                final_state = "S14"
                break
        transition_wait_ms = int((time.perf_counter() - wait_start) * 1000)
        context["current_reference"]["s13_to_s14_click_audits"][-1] = click_audit
        context["current_reference"]["last_s13_to_s14_click_audit"] = click_audit
        timing.add(
            step_name="S13_TO_S14",
            page_name="S13",
            action_name="tap_repair_item_if_nonzero",
            contract_check_ms=int((read_start - start) * 1000),
            field_read_ms=field_ms,
            action_ms=action_ms,
            transition_wait_ms=transition_wait_ms,
            screenshot_path=str(next_snapshot.get("screenshot_path") or ""),
            xml_path=str(next_snapshot.get("xml_path") or ""),
            extra={
                "s13_to_s14_click_audit": click_audit,
                "s13_repair_item_safe_click_target_confirmed": final_state == "S14",
            },
        )
        if final_state == "S14":
            return "S14", next_snapshot
        context["exclude_current_reference_from_history"] = True
        issue_code = "S13_TO_S14_INTERMEDIATE_PAGE_UNCONFIRMED"
        issue_message = f"Clicked safe repair item for {region_name}, but final page was not confirmed as S14 after bounded wait."
        if not page_changed_after_repair_click:
            issue_code = "S13_REPAIR_ITEM_CLICK_DID_NOT_OPEN_DETAIL"
            issue_message = f"Clicked safe repair item for {region_name}, but the page did not change or open repair detail."
        issue = issues.record(
            issue_code,
            "S13",
            issue_message,
            {**next_snapshot, "region_name": region_name, "s13_to_s14_click_audit": click_audit},
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    final_scan_state = _s13_region_scan_state(context)
    if not (final_scan_state.get("all_regions_checked") and final_scan_state.get("s13_all_zero")):
        missing_regions = [
            region
            for region in S13_REGION_ORDER
            if (final_scan_state.get("s13_region_history_count_bindings") or {}).get(region) is None
        ]
        issue = issues.record(
            "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED",
            "S13",
            "S13 four-region history repair count scan did not produce a complete all-zero state.",
            {
                **snapshot,
                "missing_regions": missing_regions,
                "completed_regions": final_scan_state.get("completed_regions"),
                "visited_regions": final_scan_state.get("visited_regions"),
                "partial_bindings": final_scan_state.get("s13_region_history_count_bindings"),
                "unknown_region_reason": "all_zero_exit_requires_all_four_regions_confirmed",
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context["current_reference"].update(
        {
            "overall_contract_version": "V1.32",
            "execution_contract_version": "V1.32",
            "s13_repair_count_role": "entry_signal_only",
            "s13_region_check_order": list(S13_REGION_ORDER),
            "s13_first_positive_region": "",
            "s13_first_positive_region_repair_count": 0,
            "s13_enter_s14_required": False,
            "s14_full_image_sequence_required": False,
            "s15_entry_reason": "NO_HISTORY_REPAIR_COUNT_S14_NOT_REQUIRED",
            "s13_history_repair_confirmed": True,
            "all_regions_checked": True,
            "s13_all_zero": True,
            "s13_total_repair_count": 0,
            "s13_region_scan_state_persisted": True,
            "s13_region_scan_exit_reason": "ALL_REGIONS_ZERO",
            "s13_all_zero_exit_trace": S13_ALL_ZERO_RETURN_TO_S10_FOR_S15,
            "return_to_reliable_s10_required": True,
            "next_internal_state": "S15",
        }
    )
    return _finish_s13_all_zero_with_reliable_s10_return(context, snapshot, scan_state=final_scan_state)


S14_TAB_LABEL_RE = re.compile(r"^(.+?)[(（](\d+)\s*/\s*(\d+)[)）]$")


def _bounds_visible(bounds: tuple[int, int, int, int] | None) -> bool:
    return bool(bounds and bounds[2] > bounds[0] and bounds[3] > bounds[1] and bounds[2] > 0 and bounds[3] > 0)


def _s14_has_non_structure_surface_semantic(value: str) -> bool:
    compact = re.sub(r"\s+", "", str(value or ""))
    return any(signal in compact for signal in S14_NON_STRUCTURE_SURFACE_SEMANTICS)


def _s14_normalize_surface_qualified_part(value: str) -> str | None:
    compact = re.sub(r"\s+", "", str(value or ""))
    for suffix in ("漆面损伤", "外侧漆面", "漆面", "婕嗛潰鎹熶激", "婕嗛潰"):
        if not compact.endswith(suffix) or compact == suffix:
            continue
        base = compact[: -len(suffix)]
        normalized_base = _normalize_s14_part(base)
        if normalized_base:
            normalized_suffix = "漆面" if suffix in {"漆面损伤", "婕嗛潰鎹熶激", "婕嗛潰"} else suffix
            return f"{normalized_base}{normalized_suffix}"
    return None


def _s14_normalize_directional_d_pillar(value: str) -> str | None:
    compact = re.sub(r"\s+", "", str(value or ""))
    if re.fullmatch(r"[左右]?D柱", compact):
        return compact
    return None


def _normalize_s14_part(part: str) -> str | None:
    value = str(part or "").strip()
    compact = re.sub(r"\s+", "", value)
    surface_qualified = _s14_normalize_surface_qualified_part(compact)
    if surface_qualified:
        return surface_qualified
    directional_d_pillar = _s14_normalize_directional_d_pillar(compact)
    if directional_d_pillar:
        return directional_d_pillar
    surface_semantic = _s14_has_non_structure_surface_semantic(compact)
    if not surface_semantic:
        for normalized, aliases in S14_SPECIAL_PART_ALIASES.items():
            if any(re.sub(r"\s+", "", alias) in compact for alias in aliases):
                return normalized
    for normalized, aliases in S14_COVER_PART_ALIASES.items():
        if compact in {re.sub(r"\s+", "", alias) for alias in aliases}:
            return normalized
    if value in S14_ALLOWED_PARTS:
        return value
    if surface_semantic:
        return compact
    for suffix in ["婕嗛潰", "婕嗛潰鎹熶激"]:
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
    return _normalize_s14_part(label)


def _s14_part_category(part: str | None) -> str | None:
    if part in S14_SPECIAL_STRUCTURE_RISK_PARTS:
        return "special_structure_risk"
    if _s14_has_non_structure_surface_semantic(str(part or "")):
        return "surface_non_structure"
    if part:
        return "cover_panel"
    return None


def _s14_make_key(page_label: str, raw_first_line: str, normalized_part: str | None, normalized_damage: str | None) -> str:
    label = str(page_label or "").strip()
    first_line = str(raw_first_line or "").strip()
    part = str(normalized_part or "").strip()
    damage = str(normalized_damage or "").strip()
    if part or damage:
        return "|".join([label, first_line, part, damage])
    if label and first_line:
        return f"RAW_KEY::{label}::{first_line}"
    return "|".join([label, first_line, part, damage])


def _s14_labels(snapshot: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for node in snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or "")):
        for label in node.get("labels", []) or []:
            value = str(label or "").strip()
            if value:
                labels.append(value)
    labels.extend(str(text or "").strip() for text in snapshot.get("visible_texts", []) or [] if str(text or "").strip())
    return list(dict.fromkeys(labels))


def _s14_report_marker_seen(snapshot: dict[str, Any]) -> bool:
    return any("瓜子官方检测报告" in label for label in _s14_labels(snapshot))


def _s14_detail_popup_close_safety(snapshot: dict[str, Any]) -> dict[str, Any]:
    labels = _s14_labels(snapshot)
    joined = "\n".join(labels)
    external_markers = ["立即下载", "打开应用", "权限设置", "验证码", "密码", "广告下载"]
    external_blocked = [marker for marker in external_markers if marker in joined]
    report_seen = _s14_report_marker_seen(snapshot)
    safe = report_seen and not external_blocked
    return {
        "safe": safe,
        "strategy": "android_back_or_bottom_back" if safe else "",
        "reason": "" if safe else "s14_report_marker_missing_or_external_blocker",
        "external_blocked_markers": external_blocked,
        "ai_detail_text_seen": any("AI详细解读" in label for label in labels),
    }


def _s14_auxiliary_detail_texts(snapshot: dict[str, Any], page_label_part: str | None) -> list[str]:
    part = str(page_label_part or "").strip()
    hints = ["拆卸痕迹", "维修痕迹", "修复痕迹", "AI详细解读"]
    result: list[str] = []
    for label in _s14_labels(snapshot):
        value = str(label or "").strip()
        if not value:
            continue
        if part and value == part:
            continue
        if "—" in value or "--" in value or "-" in value:
            continue
        if any(hint in value for hint in hints) and (not part or part in value or "AI详细解读" not in value):
            result.append(value)
    return list(dict.fromkeys(result))[:8]


def _s14_selected_tab_part_label(page_label: str) -> str:
    label = str(page_label or "").strip()
    match = S14_TAB_LABEL_RE.fullmatch(label)
    return (match.group(1).strip() if match else label).strip()


def _s14_ai_detail_texts(snapshot: dict[str, Any], page_label_part: str | None, page_label_raw_part: str | None = None) -> list[str]:
    normalized = str(page_label_part or "").strip()
    raw = str(page_label_raw_part or "").strip()
    result: list[str] = []
    for label in _s14_labels(snapshot):
        value = str(label or "").strip()
        if "AI详细解读" not in value:
            continue
        if raw and raw in value:
            result.append(value)
            continue
        if normalized and normalized in value:
            result.append(value)
            continue
        if not raw and not normalized:
            result.append(value)
    return list(dict.fromkeys(result))[:8]


def _s14_current_item_binding_source(
    snapshot: dict[str, Any],
    *,
    page_label_part: str | None,
    page_label_raw_part: str | None,
    exact: dict[str, Any] | None,
) -> str:
    if not exact:
        return ""
    ai_detail = _s14_ai_detail_texts(snapshot, page_label_part, page_label_raw_part)
    if ai_detail:
        return "tab_label_ai_detail"
    return "tab_label_detail"


def _s14_degraded_damage_line(
    snapshot: dict[str, Any],
    *,
    page_label: str,
    page_label_part: str | None,
    stale_raw_first_line: str = "",
    raw_first_line_part: str | None = None,
    status_if_safe: str | None = None,
) -> dict[str, Any]:
    part = _normalize_s14_part(page_label_part or "")
    close_safety = _s14_detail_popup_close_safety(snapshot)
    if not part or not close_safety.get("safe"):
        return {
            "raw_first_line": stale_raw_first_line,
            "bounds": None,
            "normalized_part": part,
            "raw_first_line_part": raw_first_line_part,
            "raw_damage": None,
            "normalized_damage": None,
            "first_line_bound_to_page_label": False,
            "stale_first_line_warning": bool(stale_raw_first_line),
            "stale_first_line_resolved_by_part_match": False,
            "mixed_binding_blocked": True,
            "damage_line_binding_status": "stale_unresolved_blocked",
            "stale_raw_first_line": stale_raw_first_line,
            "s14_contract_level": S14_CONTRACT_UNSAFE_FAIL,
            "s14_contract_level_reason": close_safety.get("reason") or "selected_tab_part_missing",
            "detail_text_missing": True,
            "stale_first_line_discarded": False,
            "discarded_stale_first_line": "",
            "condition_item_source": "",
            "current_item_binding_source": "",
            "stale_first_line_suspected": bool(stale_raw_first_line),
            "ignore_raw_first_line_for_current_item": False,
            "visible_detail_texts": [],
            "ai_detail_texts": _s14_ai_detail_texts(snapshot, part),
            "item_confidence": "",
            "item_needs_note": False,
            "s14_detail_popup_close_safe": bool(close_safety.get("safe")),
            "s14_detail_popup_close_strategy": close_safety.get("strategy") or "",
            "s14_auxiliary_detail_texts": _s14_auxiliary_detail_texts(snapshot, part),
        }
    part_category = _s14_part_category(part)
    level = status_if_safe or (
        S14_CONTRACT_NEEDS_REVIEW_CONTINUE
        if part_category == "special_structure_risk"
        else S14_CONTRACT_DEGRADED_RECORDABLE
    )
    reason = (
        "special_structure_detail_text_unbound"
        if level == S14_CONTRACT_NEEDS_REVIEW_CONTINUE
        else "detail_text_unbound_stale_first_line_discarded"
        if stale_raw_first_line
        else "detail_text_missing_selected_tab_fallback"
    )
    return {
        "raw_first_line": "",
        "bounds": None,
        "normalized_part": part,
        "raw_first_line_part": raw_first_line_part,
        "raw_damage": None,
        "normalized_damage": None,
        "first_line_bound_to_page_label": False,
        "stale_first_line_warning": bool(stale_raw_first_line),
        "stale_first_line_resolved_by_part_match": False,
        "mixed_binding_blocked": False,
        "damage_line_binding_status": "stale_discarded_degraded" if stale_raw_first_line else "detail_missing_degraded",
        "stale_raw_first_line": stale_raw_first_line,
        "s14_contract_level": level,
        "s14_contract_level_reason": reason,
        "detail_text_missing": True,
        "stale_first_line_discarded": bool(stale_raw_first_line),
        "discarded_stale_first_line": stale_raw_first_line,
        "condition_item_source": "selected_tab_fallback",
        "current_item_binding_source": "selected_tab_fallback",
        "stale_first_line_suspected": bool(stale_raw_first_line),
        "ignore_raw_first_line_for_current_item": bool(stale_raw_first_line),
        "visible_detail_texts": [],
        "ai_detail_texts": _s14_ai_detail_texts(snapshot, part),
        "item_confidence": "partial",
        "item_needs_note": True,
        "s14_detail_popup_close_safe": True,
        "s14_detail_popup_close_strategy": close_safety.get("strategy") or "android_back_or_bottom_back",
        "s14_auxiliary_detail_texts": _s14_auxiliary_detail_texts(snapshot, part),
        "s14_key": f"DEGRADED::{page_label}::{part}::{level}",
    }


def _s14_key_from_damage_line(page_label: str, damage_line: dict[str, Any]) -> str:
    explicit = str(damage_line.get("s14_key") or "").strip()
    if explicit:
        return explicit
    return _s14_make_key(
        page_label,
        str(damage_line.get("raw_first_line") or ""),
        damage_line.get("normalized_part"),
        damage_line.get("normalized_damage"),
    )


def _s14_semantic_state(snapshot: dict[str, Any], tab: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = tab or _s14_selected_tab(snapshot) or {}
    page_label = str(selected.get("label") or "")
    page_label_part = _s14_page_label_part(page_label)
    resolved = _s14_resolved_damage_line(snapshot, page_label_part)
    raw_first_line = str(resolved.get("raw_first_line") or "").strip()
    normalized_part = resolved.get("normalized_part")
    raw_damage = resolved.get("raw_damage")
    normalized_damage = resolved.get("normalized_damage")
    stale_first_line_warning = bool(resolved.get("stale_first_line_warning"))
    raw_first_line_part = resolved.get("raw_first_line_part")
    return {
        "page_label": page_label,
        "raw_first_line": raw_first_line,
        "normalized_part": normalized_part,
        "raw_first_line_part": raw_first_line_part,
        "raw_damage": raw_damage,
        "normalized_damage": normalized_damage,
        "stale_first_line_warning": stale_first_line_warning,
        "stale_first_line_resolved_by_part_match": bool(resolved.get("stale_first_line_resolved_by_part_match")),
        "mixed_binding_blocked": bool(resolved.get("mixed_binding_blocked")),
        "damage_line_binding_status": resolved.get("damage_line_binding_status"),
        "stale_raw_first_line": resolved.get("stale_raw_first_line") or "",
        "s14_contract_level": resolved.get("s14_contract_level") or S14_CONTRACT_FULLY_COLLECTED,
        "s14_contract_level_reason": resolved.get("s14_contract_level_reason") or "",
        "detail_text_missing": bool(resolved.get("detail_text_missing")),
        "stale_first_line_discarded": bool(resolved.get("stale_first_line_discarded")),
        "discarded_stale_first_line": resolved.get("discarded_stale_first_line") or "",
        "condition_item_source": resolved.get("condition_item_source") or "",
        "current_item_binding_source": resolved.get("current_item_binding_source") or resolved.get("condition_item_source") or "",
        "stale_first_line_suspected": bool(resolved.get("stale_first_line_suspected")),
        "ignore_raw_first_line_for_current_item": bool(resolved.get("ignore_raw_first_line_for_current_item")),
        "visible_detail_texts": resolved.get("visible_detail_texts") or [],
        "ai_detail_texts": resolved.get("ai_detail_texts") or [],
        "item_confidence": resolved.get("item_confidence") or "full",
        "item_needs_note": bool(resolved.get("item_needs_note")),
        "s14_detail_popup_close_safe": bool(resolved.get("s14_detail_popup_close_safe")),
        "s14_detail_popup_close_strategy": resolved.get("s14_detail_popup_close_strategy") or "",
        "s14_auxiliary_detail_texts": resolved.get("s14_auxiliary_detail_texts") or [],
        "s14_key": _s14_key_from_damage_line(page_label, resolved),
    }


def _s14_semantic_changed(before: dict[str, Any], after: dict[str, Any], visited_s14_keys: list[str] | None = None) -> bool:
    if str(before.get("page_label") or "") != str(after.get("page_label") or ""):
        return True
    if str(before.get("raw_first_line") or "") != str(after.get("raw_first_line") or ""):
        return True
    if str(before.get("normalized_part") or "") != str(after.get("normalized_part") or ""):
        return True
    if str(before.get("normalized_damage") or "") != str(after.get("normalized_damage") or ""):
        return True
    if str(before.get("s14_key") or "") != str(after.get("s14_key") or ""):
        return True
    if list(before.get("visible_detail_texts") or []) != list(after.get("visible_detail_texts") or []):
        return True
    if list(before.get("ai_detail_texts") or []) != list(after.get("ai_detail_texts") or []):
        return True
    return False


def _s14_uncollected_next_condition_signals(context: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Detect visible S14 condition entries that have not been collected yet."""
    visited_keys = {str(item or "") for item in (context.get("visited_s14_keys") or [])}
    visited_labels: set[str] = set()
    visited_parts: set[str] = set()
    for item in context.get("s14_image_records") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("tab_label") or "").strip()
        if label:
            visited_labels.add(label)
        part = str(item.get("normalized_part") or item.get("part") or "").strip()
        if part:
            visited_parts.add(part)
    for key in visited_keys:
        if key.startswith("RAW_KEY::"):
            raw_parts = key.split("::")
            if len(raw_parts) >= 3 and raw_parts[1]:
                visited_labels.add(raw_parts[1])
            continue
        key_parts = key.split("|")
        if key_parts and key_parts[0]:
            visited_labels.add(key_parts[0])
        if len(key_parts) >= 3 and key_parts[2]:
            visited_parts.add(key_parts[2])

    unvisited_tab_labels: list[str] = []
    for tab in _s14_tab_items(snapshot):
        label = str(tab.get("label") or "").strip()
        label_part = _s14_page_label_part(label)
        if label and label not in visited_labels and (not label_part or label_part not in visited_parts):
            unvisited_tab_labels.append(label)

    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    candidate_texts: list[str] = []
    for node in nodes:
        for label in node.get("labels", []):
            value = str(label or "").strip()
            if value:
                candidate_texts.append(value)
    for text in snapshot.get("visible_texts", []) or []:
        value = str(text or "").strip()
        if value:
            candidate_texts.append(value)

    unvisited_damage_lines: list[dict[str, str]] = []
    seen_lines: set[tuple[str, str]] = set()
    for value in candidate_texts:
        parsed = _parse_s14_damage_line(value)
        if not parsed:
            continue
        part, _raw_damage, normalized_damage, raw_line = parsed
        if not part or part in visited_parts:
            continue
        key = (part, raw_line)
        if key in seen_lines:
            continue
        seen_lines.add(key)
        unvisited_damage_lines.append(
            {
                "raw_first_line": raw_line,
                "normalized_part": part,
                "normalized_damage": normalized_damage,
            }
        )

    has_signal = bool(unvisited_tab_labels or unvisited_damage_lines)
    result = {
        "s14_has_uncollected_next_condition_signal": has_signal,
        "unvisited_tab_labels": unvisited_tab_labels,
        "unvisited_damage_lines": unvisited_damage_lines,
        "visited_s14_keys_count": len(visited_keys),
        "visited_labels_count": len(visited_labels),
        "visited_parts_count": len(visited_parts),
    }
    context["s14_has_uncollected_next_condition_signal"] = has_signal
    context["s14_uncollected_next_condition_signals"] = result
    context.setdefault("current_reference", {})["s14_has_uncollected_next_condition_signal"] = has_signal
    context.setdefault("current_reference", {})["s14_uncollected_next_condition_signals"] = result
    return result


def _split_s14_damage_line(text: str) -> tuple[str, str] | None:
    match = re.match(r"(.+?)(?:--|—|－|–|：|:|-)(.+)", str(text or "").strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None


def _parse_s14_damage_line(value: str) -> tuple[str, str, str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    split = _split_s14_damage_line(text)
    if split is None:
        return None
    raw_part, raw_damage = split
    part = _normalize_s14_part(raw_part)
    normalized = S14_NON_SCORING_DAMAGE if raw_damage in S14_NON_SCORING_DAMAGE_TYPES else S14_DAMAGE_NORMALIZATION.get(raw_damage)
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


def _s14_damage_line_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = snapshot.get("nodes") or _parse_nodes(str(snapshot.get("fresh_xml") or ""))
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[int, ...]]] = set()
    for node in nodes:
        bounds = node.get("bounds")
        bounds_key = tuple(int(item) for item in bounds) if _valid_bounds(bounds) else ()
        for label in node.get("labels", []):
            value = str(label or "").strip()
            parsed = _parse_s14_damage_line(value)
            if not parsed:
                continue
            part, raw_damage, normalized_damage, raw_line = parsed
            key = (part, raw_line, bounds_key)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "raw_first_line": raw_line,
                    "bounds": bounds,
                    "normalized_part": part,
                    "raw_first_line_part": part,
                    "raw_damage": raw_damage,
                    "normalized_damage": normalized_damage,
                    "visible": _bounds_visible(bounds),
                    "source": "xml_node",
                }
            )
    for text in snapshot.get("visible_texts", []) or []:
        value = str(text or "").strip()
        parsed = _parse_s14_damage_line(value)
        if not parsed:
            continue
        part, raw_damage, normalized_damage, raw_line = parsed
        key = (part, raw_line, ())
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "raw_first_line": raw_line,
                "bounds": None,
                "normalized_part": part,
                "raw_first_line_part": part,
                "raw_damage": raw_damage,
                "normalized_damage": normalized_damage,
                "visible": True,
                "source": "visible_text",
            }
        )
    return candidates


def _s14_damage_line_for_part(snapshot: dict[str, Any], part: str | None) -> dict[str, Any] | None:
    normalized_part = _normalize_s14_part(part or "")
    if not normalized_part:
        return None
    matches = [
        item
        for item in _s14_damage_line_candidates(snapshot)
        if item.get("normalized_part") == normalized_part
    ]
    if not matches:
        return None

    def sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
        bounds = item.get("bounds")
        visible_rank = 0 if item.get("visible") else 1
        y = int(bounds[1]) if _valid_bounds(bounds) else 10**9
        x = int(bounds[0]) if _valid_bounds(bounds) else 10**9
        return visible_rank, y, x

    return sorted(matches, key=sort_key)[0]


def _s14_resolved_damage_line(snapshot: dict[str, Any], page_label_part: str | None) -> dict[str, Any]:
    page_label = str(((_s14_selected_tab(snapshot) or {}).get("label")) or "")
    page_label_raw_part = _s14_selected_tab_part_label(page_label)
    primary = _s14_main_damage_line(snapshot)
    primary_raw = str(primary.get("raw_first_line") or "").strip()
    primary_parsed = _parse_s14_damage_line(primary_raw)
    if primary_parsed:
        part, raw_damage, normalized_damage, _raw_line = primary_parsed
        base = {
            **primary,
            "normalized_part": part,
            "raw_first_line_part": part,
            "raw_damage": raw_damage,
            "normalized_damage": normalized_damage,
            "first_line_bound_to_page_label": not page_label_part or part == page_label_part,
            "stale_first_line_warning": False,
            "stale_first_line_resolved_by_part_match": False,
            "mixed_binding_blocked": False,
            "damage_line_binding_status": "primary",
            "stale_raw_first_line": "",
            "condition_item_source": "primary_first_line",
            "current_item_binding_source": "primary_first_line",
            "stale_first_line_suspected": False,
            "ignore_raw_first_line_for_current_item": False,
            "visible_detail_texts": [primary_raw] if primary_raw else [],
            "ai_detail_texts": _s14_ai_detail_texts(snapshot, page_label_part, page_label_raw_part),
        }
        if not page_label_part or part == page_label_part:
            return base
        exact = _s14_damage_line_for_part(snapshot, page_label_part)
        if exact:
            binding_source = _s14_current_item_binding_source(
                snapshot,
                page_label_part=page_label_part,
                page_label_raw_part=page_label_raw_part,
                exact=exact,
            )
            return {
                **exact,
                "first_line_bound_to_page_label": True,
                "stale_first_line_warning": True,
                "stale_first_line_resolved_by_part_match": True,
                "mixed_binding_blocked": False,
                "damage_line_binding_status": "resolved_by_page_label_part",
                "stale_raw_first_line": primary_raw,
                "condition_item_source": binding_source,
                "current_item_binding_source": binding_source,
                "stale_first_line_suspected": True,
                "ignore_raw_first_line_for_current_item": True,
                "discarded_stale_first_line": primary_raw,
                "visible_detail_texts": [str(exact.get("raw_first_line") or "")],
                "ai_detail_texts": _s14_ai_detail_texts(snapshot, page_label_part, page_label_raw_part),
            }
        return _s14_degraded_damage_line(
            snapshot,
            page_label=page_label,
            page_label_part=page_label_part,
            stale_raw_first_line=primary_raw,
            raw_first_line_part=part,
        )
    exact = _s14_damage_line_for_part(snapshot, page_label_part)
    if exact:
        binding_source = _s14_current_item_binding_source(
            snapshot,
            page_label_part=page_label_part,
            page_label_raw_part=page_label_raw_part,
            exact=exact,
        )
        return {
            **exact,
            "first_line_bound_to_page_label": True,
            "stale_first_line_warning": False,
            "stale_first_line_resolved_by_part_match": False,
            "mixed_binding_blocked": False,
            "damage_line_binding_status": "resolved_by_page_label_part",
            "stale_raw_first_line": "",
            "condition_item_source": binding_source,
            "current_item_binding_source": binding_source,
            "stale_first_line_suspected": False,
            "ignore_raw_first_line_for_current_item": False,
            "visible_detail_texts": [str(exact.get("raw_first_line") or "")],
            "ai_detail_texts": _s14_ai_detail_texts(snapshot, page_label_part, page_label_raw_part),
        }
    if page_label_part:
        return _s14_degraded_damage_line(
            snapshot,
            page_label=page_label,
            page_label_part=page_label_part,
        )
    return {
        **primary,
        "normalized_part": None,
        "raw_first_line_part": None,
        "raw_damage": None,
        "normalized_damage": None,
        "first_line_bound_to_page_label": False,
        "stale_first_line_warning": False,
        "stale_first_line_resolved_by_part_match": False,
        "mixed_binding_blocked": True,
        "damage_line_binding_status": "stale_unresolved_blocked",
        "stale_raw_first_line": "",
        "s14_contract_level": S14_CONTRACT_UNSAFE_FAIL,
        "s14_contract_level_reason": "selected_tab_part_missing",
        "condition_item_source": "",
        "current_item_binding_source": "",
        "stale_first_line_suspected": False,
        "ignore_raw_first_line_for_current_item": False,
        "visible_detail_texts": [],
        "ai_detail_texts": _s14_ai_detail_texts(snapshot, page_label_part, page_label_raw_part),
    }


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
    damage_line = _s14_resolved_damage_line(snapshot, page_label_part)
    raw_first_line = str(damage_line.get("raw_first_line") or "").strip()
    part = damage_line.get("normalized_part")
    raw_damage = damage_line.get("raw_damage")
    normalized = damage_line.get("normalized_damage")
    raw_part = None
    part_category = None
    saved = False
    parsed = bool(part and raw_damage and normalized)
    non_scoring_damage = normalized == S14_NON_SCORING_DAMAGE
    if part:
        part_category = _s14_part_category(part)
    if parsed:
        split = _split_s14_damage_line(raw_first_line)
        raw_part = split[0] if split else part
    first_line_bound_to_page_label = bool(damage_line.get("first_line_bound_to_page_label"))
    stale_first_line_warning = bool(damage_line.get("stale_first_line_warning"))
    stale_first_line_resolved = bool(damage_line.get("stale_first_line_resolved_by_part_match"))
    mixed_binding_blocked = bool(damage_line.get("mixed_binding_blocked"))
    raw_first_line_part = damage_line.get("raw_first_line_part")
    contract_level = str(damage_line.get("s14_contract_level") or S14_CONTRACT_FULLY_COLLECTED)
    detail_text_missing = bool(damage_line.get("detail_text_missing"))
    stale_first_line_discarded = bool(damage_line.get("stale_first_line_discarded"))
    discarded_stale_first_line = str(damage_line.get("discarded_stale_first_line") or "")
    stale_first_line_suspected = bool(damage_line.get("stale_first_line_suspected"))
    ignore_raw_first_line = bool(damage_line.get("ignore_raw_first_line_for_current_item"))
    current_item_binding_source = str(
        damage_line.get("current_item_binding_source") or damage_line.get("condition_item_source") or ""
    )
    visible_detail_texts = list(damage_line.get("visible_detail_texts") or [])
    ai_detail_texts = list(damage_line.get("ai_detail_texts") or [])
    item_needs_note = bool(damage_line.get("item_needs_note"))
    item_confidence = str(damage_line.get("item_confidence") or ("partial" if item_needs_note else "full"))
    s14_key = _s14_key_from_damage_line(page_label, damage_line)
    visited_s14_keys = context.setdefault("visited_s14_keys", [])
    repeated_s14_key = bool(s14_key and s14_key in visited_s14_keys)
    if repeated_s14_key:
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
            "raw_first_line_part": raw_first_line_part,
            "part": part,
            "raw_damage": raw_damage,
            "normalized_damage": normalized,
            "part_category": part_category,
            "saved_to_repair_items": False,
            "skipped_reason": "repeated_s14_key",
            "stale_first_line_warning": stale_first_line_warning,
            "stale_first_line_resolved_by_part_match": stale_first_line_resolved,
            "mixed_binding_blocked": mixed_binding_blocked,
            "damage_line_binding_status": damage_line.get("damage_line_binding_status"),
            "stale_raw_first_line": damage_line.get("stale_raw_first_line") or "",
            "s14_contract_level": contract_level,
            "s14_contract_level_reason": damage_line.get("s14_contract_level_reason") or "",
            "detail_text_missing": detail_text_missing,
            "stale_first_line_discarded": stale_first_line_discarded,
            "discarded_stale_first_line": discarded_stale_first_line,
            "selected_tab_part": page_label_part,
            "item_confidence": item_confidence,
            "item_needs_note": item_needs_note,
            "condition_item_source": damage_line.get("condition_item_source") or "",
            "current_item_binding_source": current_item_binding_source,
            "stale_first_line_suspected": stale_first_line_suspected,
            "ignore_raw_first_line_for_current_item": ignore_raw_first_line,
            "visible_detail_texts": visible_detail_texts,
            "ai_detail_texts": ai_detail_texts,
            "s14_detail_popup_close_safe": bool(damage_line.get("s14_detail_popup_close_safe")),
            "s14_detail_popup_close_strategy": damage_line.get("s14_detail_popup_close_strategy") or "",
            "s14_auxiliary_detail_texts": damage_line.get("s14_auxiliary_detail_texts") or [],
            "s14_key": s14_key,
            "s14_semantic_signature": s14_key,
            "s14_raw_key_used": bool(s14_key.startswith("RAW_KEY::")),
            "caption_seen": bool(raw_first_line),
            "repeated_s14_key": True,
            "first_line_bound_to_page_label": first_line_bound_to_page_label,
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
        }
    if s14_key:
        visited_s14_keys.append(s14_key)
    if parsed and not mixed_binding_blocked and not non_scoring_damage:
        current = context["damage_by_part"].get(part)
        if current is None or S14_DAMAGE_PRIORITY[normalized] > S14_DAMAGE_PRIORITY[current["normalized_damage"]]:
            context["damage_by_part"][part] = {
                "part": part,
                "raw_text": raw_first_line,
                "raw_part": raw_part,
                "normalized_part": part,
                "raw_first_line_part": raw_first_line_part,
                "raw_damage": raw_damage,
                "normalized_damage": normalized,
                "part_category": part_category,
                "tab_label": tab.get("label"),
                "image_index": image_index,
                "image_total": int(tab.get("total_pages") or 1),
                "stale_first_line_warning": stale_first_line_warning,
                "stale_first_line_resolved_by_part_match": stale_first_line_resolved,
                "damage_line_binding_status": damage_line.get("damage_line_binding_status"),
                "stale_raw_first_line": damage_line.get("stale_raw_first_line") or "",
                "current_item_binding_source": current_item_binding_source,
                "stale_first_line_suspected": stale_first_line_suspected,
                "ignore_raw_first_line_for_current_item": ignore_raw_first_line,
                "visible_detail_texts": visible_detail_texts,
                "ai_detail_texts": ai_detail_texts,
                "screenshot_path": str(snapshot.get("screenshot_path") or ""),
                "xml_path": str(snapshot.get("xml_path") or ""),
            }
        saved = True
    skipped_reason = (
        ""
        if saved
        else S14_CONTRACT_DEGRADED_NEEDS_REVIEW
        if contract_level == S14_CONTRACT_NEEDS_REVIEW_CONTINUE
        else S14_STALE_FIRST_LINE_DISCARDED
        if stale_first_line_discarded
        else S14_DETAIL_TEXT_UNBOUND_DEGRADED
        if contract_level == S14_CONTRACT_DEGRADED_RECORDABLE
        else "S14_MIXED_BINDING_BLOCKED"
        if mixed_binding_blocked
        else S14_NON_SCORING_DAMAGE
        if non_scoring_damage
        else "non_target_part_or_damage_type"
    )
    if mixed_binding_blocked:
        context.setdefault("s14_mixed_binding_blocked_events", []).append(
            {
                "tab_label": page_label,
                "page_label_part": page_label_part,
                "raw_first_line": raw_first_line,
                "raw_first_line_part": raw_first_line_part,
                "stale_raw_first_line": damage_line.get("stale_raw_first_line") or raw_first_line,
                "screenshot_path": str(snapshot.get("screenshot_path") or ""),
                "xml_path": str(snapshot.get("xml_path") or ""),
            }
        )
    record = {
        "tab_label": page_label,
        "image_index": image_index,
        "image_total": int(tab.get("total_pages") or 1),
        "raw_text": raw_first_line,
        "raw_first_line": raw_first_line,
        "raw_part": raw_part,
        "normalized_part": part,
        "raw_first_line_part": raw_first_line_part,
        "part": part,
        "raw_damage": raw_damage,
        "normalized_damage": normalized,
        "part_category": part_category,
        "is_cover_panel": part_category == "cover_panel",
        "is_special_structure_risk": part_category == "special_structure_risk",
        "is_non_scoring_damage": non_scoring_damage,
        "is_target_damage_type": normalized is not None and not non_scoring_damage,
        "saved_to_repair_items": saved,
        "skipped_reason": skipped_reason,
        "stale_first_line_warning": stale_first_line_warning,
        "stale_first_line_resolved_by_part_match": stale_first_line_resolved,
        "mixed_binding_blocked": mixed_binding_blocked,
        "damage_line_binding_status": damage_line.get("damage_line_binding_status"),
        "stale_raw_first_line": damage_line.get("stale_raw_first_line") or "",
        "stale_first_line_reason": "active_tab_part_differs_from_raw_first_line" if stale_first_line_warning else "",
        "s14_contract_level": contract_level,
        "s14_contract_level_reason": damage_line.get("s14_contract_level_reason") or "",
        "detail_text_missing": detail_text_missing,
        "stale_first_line_discarded": stale_first_line_discarded,
        "discarded_stale_first_line": discarded_stale_first_line,
        "selected_tab_part": page_label_part,
        "item_confidence": item_confidence,
        "item_needs_note": item_needs_note,
        "condition_item_source": damage_line.get("condition_item_source") or "",
        "current_item_binding_source": current_item_binding_source,
        "stale_first_line_suspected": stale_first_line_suspected,
        "ignore_raw_first_line_for_current_item": ignore_raw_first_line,
        "visible_detail_texts": visible_detail_texts,
        "ai_detail_texts": ai_detail_texts,
        "s14_detail_popup_close_safe": bool(damage_line.get("s14_detail_popup_close_safe")),
        "s14_detail_popup_close_strategy": damage_line.get("s14_detail_popup_close_strategy") or "",
        "s14_auxiliary_detail_texts": damage_line.get("s14_auxiliary_detail_texts") or [],
        "s14_key": s14_key,
        "s14_semantic_signature": s14_key,
        "s14_raw_key_used": bool(s14_key.startswith("RAW_KEY::")),
        "caption_seen": bool(raw_first_line),
        "repeated_s14_key": False,
        "page_label_part": page_label_part,
        "first_line_bound_to_page_label": first_line_bound_to_page_label,
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "xml_path": str(snapshot.get("xml_path") or ""),
    }
    context.setdefault("s14_image_records", []).append(record)
    if not saved and not mixed_binding_blocked:
        suspected_reasons: list[str] = []
        if stale_first_line_suspected or stale_first_line_discarded:
            suspected_reasons.append("stale_first_line")
        if not parsed:
            suspected_reasons.append("current_item_binding_failed")
        if detail_text_missing:
            suspected_reasons.append("detail_hidden_or_missing")
        if raw_damage and not normalized:
            suspected_reasons.append("damage_type_missing")
        if non_scoring_damage:
            suspected_reasons.append("non_scoring_or_rule_review")
        trace = {
            "selected_tab": page_label,
            "selected_tab_part": page_label_part,
            "raw_first_line": raw_first_line,
            "raw_first_line_part": raw_first_line_part,
            "visible_detail_texts": visible_detail_texts,
            "ai_detail_texts": ai_detail_texts,
            "image_hash_changed": None,
            "xml_changed": None,
            "semantic_changed": None,
            "suspected_reason": list(dict.fromkeys(suspected_reasons)) or ["not_saved_to_repair_items"],
            "current_item_binding_source": current_item_binding_source,
            "stale_first_line_suspected": stale_first_line_suspected,
            "ignore_raw_first_line_for_current_item": ignore_raw_first_line,
            "s14_key": s14_key,
            "skipped_reason": skipped_reason,
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
        }
        context.setdefault("unparsed_s14_items", []).append(trace)
        context.setdefault("current_reference", {}).setdefault("unparsed_s14_items", []).append(trace)
    if contract_level in {S14_CONTRACT_DEGRADED_RECORDABLE, S14_CONTRACT_NEEDS_REVIEW_CONTINUE}:
        context.setdefault("s14_degraded_items", []).append(record)
        degraded_count = len(context.get("s14_degraded_items") or [])
        context["s14_degraded_item_count"] = degraded_count
        context["reference_condition_confidence"] = "partial"
        context.setdefault("reference_condition_notes", []).append(
            {
                "code": skipped_reason,
                "part": page_label_part,
                "tab_label": page_label,
                "confidence": item_confidence,
                "detail_text_missing": detail_text_missing,
                "stale_first_line_discarded": stale_first_line_discarded,
            }
        )
        current_reference = context.setdefault("current_reference", {})
        current_reference["s14_degraded_item_count"] = degraded_count
        current_reference["s14_degraded_items"] = list(context.get("s14_degraded_items") or [])
        current_reference["reference_condition_notes"] = list(context.get("reference_condition_notes") or [])
        current_reference["reference_condition_confidence"] = "partial"
        if contract_level == S14_CONTRACT_NEEDS_REVIEW_CONTINUE or degraded_count > S14_DEGRADED_ITEM_REVIEW_THRESHOLD:
            context["s14_contract_level"] = S14_CONTRACT_NEEDS_REVIEW_CONTINUE
            current_reference["reference_condition_needs_review"] = True
            current_reference["manual_review_required"] = True
            current_reference.setdefault("manual_review_reasons", [])
            reason = (
                S14_DEGRADED_COLLECTION_THRESHOLD_EXCEEDED
                if degraded_count > S14_DEGRADED_ITEM_REVIEW_THRESHOLD
                else S14_CONTRACT_DEGRADED_NEEDS_REVIEW
            )
            if reason not in current_reference["manual_review_reasons"]:
                current_reference["manual_review_reasons"].append(reason)
        else:
            context.setdefault("s14_contract_level", S14_CONTRACT_DEGRADED_RECORDABLE)
            current_reference.setdefault("reference_condition_needs_review", False)
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
            "skipped_reason": skipped_reason,
            "is_non_scoring_damage": non_scoring_damage,
            "stale_first_line_warning": stale_first_line_warning,
            "stale_first_line_resolved_by_part_match": stale_first_line_resolved,
            "mixed_binding_blocked": mixed_binding_blocked,
            "damage_line_binding_status": damage_line.get("damage_line_binding_status"),
            "s14_contract_level": contract_level,
            "detail_text_missing": detail_text_missing,
            "stale_first_line_discarded": stale_first_line_discarded,
            "stale_first_line_suspected": stale_first_line_suspected,
            "ignore_raw_first_line_for_current_item": ignore_raw_first_line,
            "current_item_binding_source": current_item_binding_source,
            "visible_detail_texts": visible_detail_texts,
            "ai_detail_texts": ai_detail_texts,
            "item_confidence": item_confidence,
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
            "image_hash_changed_without_semantic_change": False,
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
    result, action_ms = contract_execute_swipe(
        context,
        snapshot,
        "S14",
        "s14_image_horizontal_swipe",
        (int(x_start), int(y), int(x_end), int(y), 700),
        evidence={
            "tab_label": before_label,
            "image_index": image_index,
            "swipe_region_source": swipe_region_source,
            "swipe_region_bounds": [x1, y1, x2, y2],
        },
    )
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
    for wait_round in range(1):
        wait_started = time.perf_counter()
        time.sleep(0.35)
        wait_after_swipe_ms += int((time.perf_counter() - wait_started) * 1000)
        capture_started = time.perf_counter()
        after = _capture_with_global_popup_guard(
            context,
            "s14_image_swipe",
            current_stage="S14",
        )
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
        after_visible_text_digest = list(after.get("visible_texts", []) or [])[:40]
        semantic_changed = _s14_semantic_changed(
            before_state,
            after_state,
            list(context.get("visited_s14_keys") or []),
        )
        poll_image_hash_changed = after_image_hash != before_image_hash
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
                "image_hash_changed": poll_image_hash_changed,
                "image_hash_changed_without_semantic_change": bool(poll_image_hash_changed and not semantic_changed),
                "visible_text_digest": after_visible_text_digest,
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
        "image_hash_changed_without_semantic_change": bool(image_hash_changed and not semantic_changed),
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
        "s14_before_snapshot_reused": True,
        "s14_after_fresh_reused_for_page_label": True,
        "s14_after_fresh_reused_for_first_line": True,
        "s14_after_fresh_reused_for_semantic_key": True,
        "s14_xml_parse_count_per_swipe": 1,
    }
    context.setdefault("s14_horizontal_swipes", []).append(record)
    if (xml_changed or image_hash_changed or before_label != after_label) and repeated_s14_key:
        trace = {
            "selected_tab": after_label,
            "raw_first_line": after_line,
            "normalized_part": after_part,
            "normalized_damage": after_damage,
            "image_hash_changed": image_hash_changed,
            "xml_changed": xml_changed,
            "semantic_changed": semantic_changed,
            "suspected_reason": ["stale_first_line", "repeated_s14_key_after_page_change"],
            "current_item_binding_source": str(after_state.get("current_item_binding_source") or ""),
            "stale_first_line_suspected": bool(after_state.get("stale_first_line_suspected")),
            "ignore_raw_first_line_for_current_item": bool(after_state.get("ignore_raw_first_line_for_current_item")),
            "visible_detail_texts": after_state.get("visible_detail_texts") or [],
            "ai_detail_texts": after_state.get("ai_detail_texts") or [],
            "before_page_label": before_label,
            "after_page_label": after_label,
            "before_s14_key": before_key,
            "after_s14_key": after_key,
            "screenshot_path": str(after.get("screenshot_path") or ""),
            "xml_path": str(after.get("xml_path") or ""),
        }
        context.setdefault("unparsed_s14_items", []).append(trace)
        context.setdefault("current_reference", {}).setdefault("unparsed_s14_items", []).append(trace)
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
            "s14_before_snapshot_reused": True,
            "s14_after_fresh_reused_for_page_label": True,
            "s14_after_fresh_reused_for_first_line": True,
            "s14_after_fresh_reused_for_semantic_key": True,
            "s14_xml_parse_count_per_swipe": 1,
            "reason_category": "S14_IMAGE_SWIPE_WAIT_TOO_LONG" if wait_after_swipe_ms > 1000 else "S14_IMAGE_HORIZONTAL_SWIPE",
            "reason_detail": "short-poll after image swipe treats page label, first line, normalized damage, and new S14 key as the only effective evidence; image hash is auxiliary only",
            "solution": "confirm terminal after two consecutive semantic no-change swipes and avoid tab clicks",
        },
    )
    return after, effective


def _s14_current_page_collected(context: dict[str, Any], semantic_state: dict[str, Any]) -> bool:
    current_key = str(semantic_state.get("s14_key") or "")
    if not current_key:
        return False
    for record in context.get("s14_image_records") or []:
        if not isinstance(record, dict):
            continue
        if str(record.get("s14_key") or "") != current_key:
            continue
        if record.get("mixed_binding_blocked"):
            return False
        if record.get("saved_to_repair_items") or record.get("skipped_reason"):
            return True
    return False


def is_s14_last_page_reached(
    context: dict[str, Any],
    *,
    selected_tab: dict[str, Any] | None,
    semantic_state: dict[str, Any],
    horizontal_swipe_effective: bool,
    next_signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tab = selected_tab or {}
    try:
        page_index = int(tab.get("page_index") or 1)
    except (TypeError, ValueError):
        page_index = 1
    try:
        total_pages = int(tab.get("total_pages") or 1)
    except (TypeError, ValueError):
        total_pages = 1
    no_change_count = int(context.get("s14_no_semantic_change_count") or 0)
    current_page_collected = _s14_current_page_collected(context, semantic_state)
    mixed_binding_blocked = bool(semantic_state.get("mixed_binding_blocked"))
    page_label_at_last_index = total_pages > 0 and page_index >= total_pages
    horizontal_swipe_blocked = not horizontal_swipe_effective and no_change_count >= 1
    semantic_unchanged = not horizontal_swipe_effective and no_change_count >= 1
    repeated_no_semantic_change = no_change_count >= 2
    uncollected_next_condition = bool((next_signal or {}).get("s14_has_uncollected_next_condition_signal"))
    repair_completion = _repair_item_completion_state(context)
    missing_repair_count = int(repair_completion.get("missing_repair_count") or 0)
    expected_items_count = int(repair_completion.get("s14_expected_items_count") or 0)
    collected_items_count = int(repair_completion.get("s14_collected_items_count") or 0)
    blocked_by_missing_repair_count = missing_repair_count > 0 or (
        expected_items_count > 0 and collected_items_count < expected_items_count
    )
    blocked_by_unparsed_item = bool(context.get("unparsed_s14_items")) and blocked_by_missing_repair_count
    last_page_reached = (
        current_page_collected
        and not mixed_binding_blocked
        and not uncollected_next_condition
        and not blocked_by_missing_repair_count
        and not blocked_by_unparsed_item
        and (
            (page_label_at_last_index and horizontal_swipe_blocked)
            or repeated_no_semantic_change
            or semantic_unchanged
        )
    )
    reasons: list[str] = []
    if page_label_at_last_index and horizontal_swipe_blocked:
        reasons.append("PAGE_LABEL_LAST_INDEX_AND_HORIZONTAL_SWIPE_BLOCKED")
    if repeated_no_semantic_change:
        reasons.append("REPEATED_NO_SEMANTIC_CHANGE")
    elif semantic_unchanged:
        reasons.append("SEMANTIC_UNCHANGED_AFTER_HORIZONTAL_SWIPE")
    result = {
        "last_page_reached": last_page_reached,
        "current_page_collected": current_page_collected,
        "page_label": str(tab.get("label") or semantic_state.get("page_label") or ""),
        "page_index": page_index,
        "total_pages": total_pages,
        "page_label_at_last_index": page_label_at_last_index,
        "horizontal_swipe_blocked": horizontal_swipe_blocked,
        "semantic_unchanged": semantic_unchanged,
        "no_semantic_change_count": no_change_count,
        "mixed_binding_blocked": mixed_binding_blocked,
        "s14_key": str(semantic_state.get("s14_key") or ""),
        "s14_has_uncollected_next_condition_signal": uncollected_next_condition,
        "last_page_blocked_by_uncollected_next_condition_signal": uncollected_next_condition,
        "missing_repair_count": missing_repair_count,
        "s14_expected_items_count": expected_items_count,
        "s14_collected_items_count": collected_items_count,
        "last_page_blocked_by_missing_repair_count": blocked_by_missing_repair_count,
        "last_page_blocked_by_unparsed_s14_item": blocked_by_unparsed_item,
        "terminal_reason": "+".join(reasons) if last_page_reached else "",
    }
    context["s14_last_page_gate"] = result
    context.setdefault("current_reference", {})["s14_last_page_gate"] = result
    if last_page_reached:
        context["s14_last_page_reached"] = True
        context.setdefault("current_reference", {})["s14_last_page_reached"] = True
    return result


def _s10_config_suffix_variants(normalized_config: str) -> list[str]:
    variants = [normalized_config]
    stripped = re.sub(r"[型版]$", "", normalized_config)
    if stripped and stripped != normalized_config:
        variants.append(stripped)
    return _unique_nonempty(variants)


def _s10_title_config_match_candidates(config_model: str, profile: dict[str, Any]) -> list[str]:
    normalized = normalize_vehicle_title_for_match(config_model)
    if not normalized:
        return []
    base_values = [normalized]
    without_year = re.sub(r"20\d{2}款", "", normalized)
    if without_year:
        base_values.append(without_year)

    prefix_terms: list[str] = []
    for key in ("series_aliases", "brand_aliases"):
        for alias in profile.get(key) or []:
            alias_norm = normalize_vehicle_title_for_match(str(alias or ""))
            if alias_norm:
                prefix_terms.append(alias_norm)
    prefix_terms = sorted(set(prefix_terms), key=len, reverse=True)

    candidates: list[str] = []
    for base in _unique_nonempty(base_values):
        candidates.extend(_s10_config_suffix_variants(base))
        stripped = base
        changed = True
        while changed and stripped:
            changed = False
            for prefix in prefix_terms:
                if stripped.startswith(prefix) and len(stripped) > len(prefix):
                    stripped = stripped[len(prefix) :]
                    changed = True
                    candidates.extend(_s10_config_suffix_variants(stripped))
                    break
    return _unique_nonempty([candidate for candidate in candidates if candidate])


def _s14_fail(context: dict[str, Any], code: str, message: str, snapshot: dict[str, Any]) -> None:
    context["s14_collect_done"] = False
    context["valid"] = False
    context["invalid_reason"] = "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED"
    current_reference = context.setdefault("current_reference", {})
    current_reference["s14_collect_done"] = False
    current_reference["s14_incomplete_reason"] = code
    current_reference["reference_score_trustworthy"] = False
    current_reference["reference_score_invalid_reason"] = "s14_full_image_sequence_incomplete_before_s15"
    current_reference["s15_entry_allowed"] = False
    current_reference["s15_entry_block_reason"] = "S15_BLOCKED_BY_INCOMPLETE_S14"
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


def _return_from_s14_to_s10_then_s15(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    return_reason: str,
) -> tuple[str, dict[str, Any]]:
    timing: TimingRecorder = context["timing"]
    returned = _fixed_return_to_s10(context)
    returned_reliable_evidence = returned.get("s10_reliable_list_evidence") or _s10_reliable_list_evidence(
        returned,
        target_reference_index=int(context.get("current_reference_index") or 0) + 1,
        expected_card=_first_stage_expected_reference_card(
            context.get("first_stage_evidence") or {},
            int(context.get("current_reference_index") or 0) + 1,
        ),
    )
    if returned_reliable_evidence.get("reliable") is not True:
        issue = context["issues"].record(
            "POST_REFERENCE_RETURNED_LIST_SOURCE_UNRELIABLE",
            "S14",
            "S14 return snapshot was not a reliable sorted S10 vehicle list.",
            {
                **returned,
                "s10_reliable_list_evidence": returned_reliable_evidence,
                "s14_return_attempts": context.get("s14_return_attempts"),
                "s14_return_reason": return_reason,
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context["return_to_s10_reliable"] = True
    context["return_to_s10_source"] = "from_s14_fixed_return_to_s10"
    context["returned_list_source"] = "from_s14_fixed_return_to_s10"
    context["returned_list_source_verified"] = True
    context["returned_s10_reliable_evidence"] = returned_reliable_evidence
    context["post_s14_s10_snapshot"] = returned
    context["returned_s10_snapshot"] = returned
    context["returned_s10_snapshot_reused"] = True
    context["returned_s10_snapshot_source"] = "S14_RETURN_TO_S10_ATTEMPT"
    context["post_s14_to_s15_initial_dump_skipped_due_to_reuse"] = True
    context.setdefault("current_reference", {}).update(
        {
            "returned_s10_snapshot_reused": True,
            "returned_s10_snapshot_xml_path": str(returned.get("xml_path") or ""),
            "returned_s10_snapshot_screenshot_path": str(returned.get("screenshot_path") or ""),
            "returned_s10_snapshot_source": "S14_RETURN_TO_S10_ATTEMPT",
            "post_s14_to_s15_initial_dump_skipped_due_to_reuse": True,
            "returned_list_source_verified": True,
            "returned_s10_reliable_evidence": returned_reliable_evidence,
            "s14_return_reason": return_reason,
        }
    )
    timing.add(
        step_name="POST_S14_S10_SNAPSHOT_REUSE",
        page_name="S10",
        action_name="reuse_s14_return_attempt_s10_snapshot",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=0,
        transition_wait_ms=0,
        screenshot_path=str(returned.get("screenshot_path") or ""),
        xml_path=str(returned.get("xml_path") or ""),
        extra={
            "returned_s10_snapshot_reused": True,
            "returned_s10_snapshot_xml_path": str(returned.get("xml_path") or ""),
            "returned_s10_snapshot_screenshot_path": str(returned.get("screenshot_path") or ""),
            "returned_s10_snapshot_source": "S14_RETURN_TO_S10_ATTEMPT",
            "post_s14_to_s15_initial_dump_skipped_due_to_reuse": True,
            "returned_list_source": context.get("returned_list_source"),
            "returned_list_source_verified": True,
            "s10_reliable_list_evidence": returned_reliable_evidence,
            "recognized_page": "S10",
            "foreground_package": returned.get("foreground_package"),
            "focused_window": returned.get("focused_window"),
            "visible_text_digest": list(returned.get("visible_texts") or [])[:40],
            "reason_category": "RUNTIME_REUSE_EXISTING_FRESH",
            "reason_detail": "POST_S14_TO_S15 reuses the S10 fresh snapshot produced by the successful S14 return attempt.",
            "solution": "reuse the verified S10 return evidence instead of immediately dumping S10 again",
            "s14_return_reason": return_reason,
        },
    )
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
        extra={
            "returned_s10_snapshot_reused": True,
            "returned_s10_snapshot_xml_path": str(returned.get("xml_path") or ""),
            "returned_s10_snapshot_screenshot_path": str(returned.get("screenshot_path") or ""),
            "returned_s10_snapshot_source": "S14_RETURN_TO_S10_ATTEMPT",
            "post_s14_to_s15_initial_dump_skipped_due_to_reuse": True,
            "returned_list_source": context.get("returned_list_source"),
            "returned_list_source_verified": True,
            "s10_reliable_list_evidence": returned_reliable_evidence,
            "s14_return_reason": return_reason,
            "source_s14_snapshot_xml_path": str(snapshot.get("xml_path") or ""),
            "source_s14_snapshot_screenshot_path": str(snapshot.get("screenshot_path") or ""),
        },
    )
    return "S15", returned


def handle_s14(context: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    recognizer: PageRecognizer = context["recognizer"]
    issues: IssueRecorder = context["issues"]
    timing: TimingRecorder = context["timing"]
    snapshot = _maybe_close_guazi_push_popup_and_resume(context, snapshot, current_stage="S14")
    _ensure_page("S14", recognizer, issues, snapshot)
    collect_started = time.perf_counter()
    context["s14_triggered"] = True
    context["s14_collect_done"] = False
    context["s14_full_image_sequence_required"] = True
    context["s14_full_image_sequence_collected"] = False
    context["s14_image_sequence_model"] = True
    context["s14_sequence_terminal_confirmed"] = False
    context.setdefault("current_reference", {}).update(
        {
            "overall_contract_version": "V1.47",
            "execution_contract_version": "V1.47",
            "s14_full_image_sequence_required": True,
            "s14_full_image_sequence_collected": False,
            "s14_collect_done": False,
        }
    )
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
        raw_first_line = str(semantic_state.get("raw_first_line") or "").strip()
        contract_level = str(semantic_state.get("s14_contract_level") or S14_CONTRACT_FULLY_COLLECTED)
        total_pages = int(selected.get("total_pages") or 1)
        context["s14_page_label"] = semantic_state.get("page_label")
        context["s14_raw_first_line"] = raw_first_line
        context["s14_normalized_part"] = semantic_state.get("normalized_part")
        context["s14_normalized_damage"] = semantic_state.get("normalized_damage")
        context["s14_semantic_signature"] = s14_key
        context["s14_damage_line_binding_status"] = semantic_state.get("damage_line_binding_status")
        context["s14_contract_level"] = contract_level
        context["s14_contract_level_reason"] = semantic_state.get("s14_contract_level_reason") or ""
        context["s14_stale_first_line_discarded"] = bool(semantic_state.get("stale_first_line_discarded"))
        context["s14_detail_text_missing"] = bool(semantic_state.get("detail_text_missing"))
        context["s14_selected_tab_part"] = semantic_state.get("normalized_part")
        context["s14_stale_first_line_resolved_by_part_match"] = bool(
            semantic_state.get("stale_first_line_resolved_by_part_match")
        )
        if semantic_state.get("mixed_binding_blocked"):
            context["s14_completion_reason"] = "STALE_FIRST_LINE_BINDING_UNRESOLVED"
            _s14_fail(
                context,
                "S14_STALE_FIRST_LINE_BINDING_UNRESOLVED",
                "S14 current page first-line damage text could not be safely bound to the active tab part.",
                snapshot,
            )
        if not raw_first_line and contract_level not in {
            S14_CONTRACT_DEGRADED_RECORDABLE,
            S14_CONTRACT_NEEDS_REVIEW_CONTINUE,
        }:
            context["s14_completion_reason"] = "caption_missing"
            _s14_fail(
                context,
                "S14_CAPTION_EXTRACTION_MISSING",
                "S14 page was confirmed but no image caption or first-line repair text was extracted.",
                snapshot,
            )
        if s14_key and s14_key not in context.get("visited_s14_keys", []):
            image_sequence_index += 1
            _s14_collect_current_image(context, snapshot, selected, image_sequence_index)
            last_effective_snapshot = snapshot
            _s14_uncollected_next_condition_signals(context, snapshot)
            early_exit_decision = _evaluate_s14_in_flight_early_exit_for_runtime(
                context,
                selected_tab=selected,
                semantic_state=semantic_state,
                snapshot=snapshot,
            )
            if early_exit_decision.get("early_exit_allowed") is True:
                _apply_reference_early_exit_decision_to_runtime(context, early_exit_decision)
                context["s14_in_flight_early_exit_pending_return"] = True
                context["s14_completion_reason"] = "S14_IN_FLIGHT_EARLY_EXIT_TRIGGERED"
                context.setdefault("current_reference", {}).update(
                    {
                        "s14_in_flight_early_exit_triggered": True,
                        "s14_early_exit_triggered": True,
                        "s14_completion_reason": "S14_IN_FLIGHT_EARLY_EXIT_TRIGGERED",
                        "repair_items": list(context.get("damage_by_part", {}).values()),
                    }
                )
                break
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
        if total_pages == 1 and _s14_current_page_collected(context, semantic_state):
            context["s14_single_image_sequence"] = True
            context["s14_completion_reason"] = (
                "SINGLE_IMAGE_WITH_CONTRACT_LEVEL_DECISION"
                if contract_level != S14_CONTRACT_FULLY_COLLECTED
                else "SINGLE_IMAGE_WITH_CAPTION_LOCAL_IMAGE_READ"
            )
            context["s14_no_semantic_change_count"] = max(1, int(context.get("s14_no_semantic_change_count") or 0))
            next_signal = _s14_uncollected_next_condition_signals(context, snapshot)
            last_page_gate = is_s14_last_page_reached(
                context,
                selected_tab=selected,
                semantic_state=semantic_state,
                horizontal_swipe_effective=False,
                next_signal=next_signal,
            )
            if last_page_gate.get("last_page_reached"):
                terminal_confirmed = True
                no_change_swipes = int(context.get("s14_no_semantic_change_count") or 1)
                break
            if last_page_gate.get("current_page_collected") and last_page_gate.get(
                "last_page_blocked_by_uncollected_next_condition_signal"
            ):
                _record_s14_continue_current_reference(
                    context,
                    next_signal=next_signal,
                    selected_tab=selected,
                    semantic_state=semantic_state,
                )
                context["s14_completion_reason"] = "CURRENT_ITEM_DONE_WHOLE_VEHICLE_INCOMPLETE_CONTINUE_CURRENT_REFERENCE"
        after, effective = _s14_swipe_image(context, snapshot, selected, image_sequence_index)
        if effective:
            _s14_uncollected_next_condition_signals(context, after)
            no_change_swipes = 0
            end_confirm_started = None
            snapshot = after
            continue
        if end_confirm_started is None:
            end_confirm_started = time.perf_counter()
        no_change_swipes = int(context.get("s14_no_semantic_change_count") or 0)
        snapshot = after
        next_signal = _s14_uncollected_next_condition_signals(context, snapshot)
        context["s14_no_new_semantic_after_swipe_count"] = no_change_swipes
        context.setdefault("current_reference", {})["s14_no_new_semantic_after_swipe_count"] = no_change_swipes
        last_page_gate = is_s14_last_page_reached(
            context,
            selected_tab=selected,
            semantic_state=semantic_state,
            horizontal_swipe_effective=effective,
            next_signal=next_signal,
        )
        if last_page_gate.get("last_page_reached"):
            terminal_confirmed = True
            context["s14_completion_reason"] = str(last_page_gate.get("terminal_reason") or "S14_LAST_PAGE_REACHED_V1_44")
            break
        if last_page_gate.get("current_page_collected") and last_page_gate.get(
            "last_page_blocked_by_uncollected_next_condition_signal"
        ):
            if no_change_swipes < S14_CONTINUE_CURRENT_REFERENCE_MAX_NO_CHANGE_SWIPES:
                _record_s14_continue_current_reference(
                    context,
                    next_signal=next_signal,
                    selected_tab=selected,
                    semantic_state=semantic_state,
                )
                context["s14_completion_reason"] = "CURRENT_ITEM_DONE_WHOLE_VEHICLE_INCOMPLETE_CONTINUE_CURRENT_REFERENCE"
                continue
            terminal_confirmed = True
            _mark_s14_continue_current_reference_failed(
                context,
                reason="S14_UNCOLLECTED_CONDITION_HORIZONTAL_SWIPE_NO_PROGRESS",
                next_signal=next_signal,
                selected_tab=selected,
                semantic_state=semantic_state,
            )
            context["s14_completion_reason"] = S14_COLLECTION_INCOMPLETE_UNRECOVERABLE
            break

    if context.get("s14_in_flight_early_exit_pending_return"):
        if not context.get("s14_image_records"):
            _s14_fail(context, "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED", "S14 did not read the first image condition line.", snapshot)
        context["current_reference"]["repair_items"] = list(context["damage_by_part"].values())
        repair_completion = _store_repair_item_completion_state(context)
        missing_count = _safe_non_negative_int(repair_completion.get("missing_repair_count"))
        context.update(
            {
                "s14_collect_done": False,
                "s14_full_image_sequence_collected": False,
                "s14_sequence_terminal_confirmed": False,
                "s14_items_skipped_due_to_early_exit": missing_count,
                "s14_early_exit_triggered": True,
            }
        )
        context.setdefault("current_reference", {}).update(
            {
                "s14_collect_done": False,
                "s14_full_image_sequence_collected": False,
                "s14_sequence_terminal_confirmed": False,
                "s14_items_skipped_due_to_early_exit": missing_count,
                "s14_early_exit_triggered": True,
                "s14_in_flight_early_exit_triggered": True,
                "s14_in_flight_early_exit_check_count": context.get("s14_in_flight_early_exit_check_count"),
                "s14_in_flight_early_exit_trace": context.get("s14_in_flight_early_exit_trace"),
                "s14_tab_records": context.get("s14_tab_records", []),
                "s14_image_records": context.get("s14_image_records", []),
                "s14_horizontal_swipes": context.get("s14_horizontal_swipes", []),
                "visited_s14_keys": list(context.get("visited_s14_keys") or []),
                "collected_s14_images": context.get("s14_image_records", []),
            }
        )
        timing.add(
            step_name="S14_IN_FLIGHT_EARLY_EXIT",
            page_name="S14",
            action_name="return_to_s10_after_deterministic_score_upper_bound",
            contract_check_ms=0,
            field_read_ms=max(0, int((time.perf_counter() - collect_started) * 1000)),
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra={
                "s14_in_flight_early_exit_triggered": True,
                "s14_items_skipped_due_to_early_exit": missing_count,
                "early_exit_decision": context.get("current_reference", {}).get("early_exit_decision"),
                "s14_in_flight_early_exit_trace": context.get("s14_in_flight_early_exit_trace"),
            },
        )
        return _return_from_s14_to_s10_then_s15(
            context,
            snapshot,
            return_reason="S14_IN_FLIGHT_EARLY_EXIT_TRIGGERED",
        )

    if not terminal_confirmed:
        _s14_fail(context, "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED", "S14 image sequence was not fully processed before guard limit.", snapshot)
    if not context.get("s14_image_records"):
        _s14_fail(context, "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED", "S14 did not read the first image condition line.", snapshot)
    context["s14_sequence_terminal_confirmed"] = True
    context.setdefault("s14_completion_reason", "SEMANTIC_SEQUENCE_END_CONFIRMED")
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
        "s14_completion_reason": context.get("s14_completion_reason"),
        "s14_single_image_sequence": bool(context.get("s14_single_image_sequence")),
        "s14_has_uncollected_next_condition_signal": bool(context.get("s14_has_uncollected_next_condition_signal")),
        "s14_uncollected_next_condition_signals": context.get("s14_uncollected_next_condition_signals"),
        "s14_no_new_semantic_after_swipe_count": context.get("s14_no_new_semantic_after_swipe_count"),
        "s14_last_page_gate": context.get("s14_last_page_gate"),
        "s14_last_page_reached": bool(context.get("s14_last_page_reached")),
    }
    metrics = _store_s14_metrics(context)
    evidence = _s14_completion_evidence(context)
    if (
        metrics["s14_tabs_total"] <= 0
        or metrics["s14_tabs_processed"] != metrics["s14_tabs_total"]
        or not all(evidence.values())
    ):
        _s14_fail(context, "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED", "S14 completion metrics did not prove all tabs and images were processed.", snapshot)
    context["current_reference"]["repair_items"] = list(context["damage_by_part"].values())
    context["current_s14_item_done"] = True
    context["s14_current_item_sequence_collected"] = True
    context["current_reference"]["current_s14_item_done"] = True
    context["current_reference"]["s14_current_item_sequence_collected"] = True
    repair_completion = _store_repair_item_completion_state(context)
    whole_vehicle_complete = repair_completion.get("s14_whole_vehicle_collection_complete") is True
    context["s14_collect_done"] = whole_vehicle_complete
    context["s14_full_image_sequence_collected"] = whole_vehicle_complete
    context["current_reference"]["s14_collect_done"] = whole_vehicle_complete
    context["current_reference"]["s14_full_image_sequence_collected"] = whole_vehicle_complete
    if not whole_vehicle_complete:
        if context["current_reference"].get("s14_continue_current_reference_possible") is not False:
            _mark_s14_continue_current_reference_failed(
                context,
                reason="S14_WHOLE_VEHICLE_INCOMPLETE_BEFORE_RETURN_TO_S10",
                next_signal=context.get("s14_uncollected_next_condition_signals"),
            )
        context["invalid_partial_reference_detected"] = True
        context["invalid_partial_reference_index"] = context.get("current_reference_index")
        context["invalid_partial_reference_reason"] = "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE"
        context["current_reference"]["current_reference_excluded_from_boundary"] = True
        exclusion_reason = context["current_reference"].get("excluded_from_boundary_reason") or "UNTRUSTED_REFERENCE_SCORE"
        context["current_reference"]["reference_exclusion_reason"] = exclusion_reason
        context["current_reference"]["excluded_reference_reason"] = exclusion_reason
    context["current_reference"]["s14_tab_records"] = context.get("s14_tab_records", [])
    context["current_reference"]["s14_image_records"] = context.get("s14_image_records", [])
    context["current_reference"]["s14_horizontal_swipes"] = context.get("s14_horizontal_swipes", [])
    context["current_reference"]["visited_s14_keys"] = list(context.get("visited_s14_keys") or [])
    context["current_reference"]["collected_s14_images"] = context.get("s14_image_records", [])
    context["current_reference"]["s14_completion_reason"] = context.get("s14_completion_reason")
    context["current_reference"]["s14_single_image_sequence"] = bool(context.get("s14_single_image_sequence"))
    context["current_reference"]["s14_page_label"] = context.get("s14_page_label")
    context["current_reference"]["s14_raw_first_line"] = context.get("s14_raw_first_line")
    context["current_reference"]["s14_semantic_signature"] = context.get("s14_semantic_signature")
    context["current_reference"]["s14_damage_line_binding_status"] = context.get("s14_damage_line_binding_status")
    context["current_reference"]["s14_stale_first_line_resolved_by_part_match"] = context.get(
        "s14_stale_first_line_resolved_by_part_match"
    )
    context["current_reference"]["s14_contract_level"] = context.get("s14_contract_level") or S14_CONTRACT_FULLY_COLLECTED
    context["current_reference"]["s14_contract_level_reason"] = context.get("s14_contract_level_reason") or ""
    context["current_reference"]["s14_detail_text_missing"] = bool(context.get("s14_detail_text_missing"))
    context["current_reference"]["s14_stale_first_line_discarded"] = bool(context.get("s14_stale_first_line_discarded"))
    context["current_reference"]["s14_degraded_item_count"] = int(context.get("s14_degraded_item_count") or 0)
    context["current_reference"]["s14_degraded_items"] = list(context.get("s14_degraded_items") or [])
    context["current_reference"]["reference_condition_notes"] = list(context.get("reference_condition_notes") or [])
    if context.get("reference_condition_confidence"):
        context["current_reference"]["reference_condition_confidence"] = context.get("reference_condition_confidence")
    context["current_reference"]["s14_repeated_key_events"] = context.get("s14_repeated_key_events", [])
    context["current_reference"]["last_5_s14_horizontal_swipes"] = list((context.get("s14_horizontal_swipes") or [])[-5:])
    context["current_reference"]["s14_tab_select_events"] = context.get("s14_tab_select_events", [])
    context["current_reference"]["s14_sequence_terminal_snapshot"] = context.get("s14_sequence_terminal_snapshot")
    context["current_reference"]["s14_has_uncollected_next_condition_signal"] = bool(
        context.get("s14_has_uncollected_next_condition_signal")
    )
    context["current_reference"]["s14_uncollected_next_condition_signals"] = context.get("s14_uncollected_next_condition_signals")
    context["current_reference"]["s14_no_new_semantic_after_swipe_count"] = context.get(
        "s14_no_new_semantic_after_swipe_count"
    )
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
            "is_aggregate": True,
            "aggregate_child_actions": ["S14_IMAGE_PROCESS", "S14_IMAGE_HORIZONTAL_SWIPE"],
            "single_action_performance_source": "S14_IMAGE_HORIZONTAL_SWIPE",
            "image_process_count": len(context.get("s14_image_records") or []),
            "image_swipe_count": len(context.get("s14_horizontal_swipes") or []),
            "end_confirm_attempts": no_change_swipes,
            "no_new_content_count": no_change_swipes,
            "last_two_swipes_no_change": no_change_swipes >= 2,
            "image_sequence_end_confirmed": terminal_confirmed,
            "s14_completion_reason": context.get("s14_completion_reason"),
            "s14_single_image_sequence": bool(context.get("s14_single_image_sequence")),
            "s14_last_page_gate": context.get("s14_last_page_gate"),
            "s14_last_page_reached": bool(context.get("s14_last_page_reached")),
            "total_end_confirm_ms": total_end_confirm_ms,
            "reason_category": "S14_END_CONFIRM_TOO_CONSERVATIVE" if total_end_confirm_ms > 1000 else "S14_IMAGE_SEQUENCE_CONFIRM",
            "reason_detail": "S14 terminal is confirmed when the current page first line is collected and the horizontal swipe produces no new semantic content",
            "solution": "use Android back/bottom back after V1.47 last-page evidence instead of waiting for synthetic image totals",
        },
    )
    returned = _fixed_return_to_s10(context)
    returned_reliable_evidence = returned.get("s10_reliable_list_evidence") or _s10_reliable_list_evidence(
        returned,
        target_reference_index=int(context.get("current_reference_index") or 0) + 1,
        expected_card=_first_stage_expected_reference_card(
            context.get("first_stage_evidence") or {},
            int(context.get("current_reference_index") or 0) + 1,
        ),
    )
    if returned_reliable_evidence.get("reliable") is not True:
        issue = context["issues"].record(
            "POST_REFERENCE_RETURNED_LIST_SOURCE_UNRELIABLE",
            "S14",
            "S14 return snapshot was not a reliable sorted S10 vehicle list.",
            {
                **returned,
                "s10_reliable_list_evidence": returned_reliable_evidence,
                "s14_return_attempts": context.get("s14_return_attempts"),
            },
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    context["return_to_s10_reliable"] = True
    context["return_to_s10_source"] = "from_s14_fixed_return_to_s10"
    context["returned_list_source"] = "from_s14_fixed_return_to_s10"
    context["returned_list_source_verified"] = True
    context["returned_s10_reliable_evidence"] = returned_reliable_evidence
    context["post_s14_s10_snapshot"] = returned
    context["returned_s10_snapshot"] = returned
    context["returned_s10_snapshot_reused"] = True
    context["returned_s10_snapshot_source"] = "S14_RETURN_TO_S10_ATTEMPT"
    context["post_s14_to_s15_initial_dump_skipped_due_to_reuse"] = True
    context["current_reference"].update(
        {
            "returned_s10_snapshot_reused": True,
            "returned_s10_snapshot_xml_path": str(returned.get("xml_path") or ""),
            "returned_s10_snapshot_screenshot_path": str(returned.get("screenshot_path") or ""),
            "returned_s10_snapshot_source": "S14_RETURN_TO_S10_ATTEMPT",
            "post_s14_to_s15_initial_dump_skipped_due_to_reuse": True,
            "returned_list_source_verified": True,
            "returned_s10_reliable_evidence": returned_reliable_evidence,
        }
    )
    timing.add(
        step_name="POST_S14_S10_SNAPSHOT_REUSE",
        page_name="S10",
        action_name="reuse_s14_return_attempt_s10_snapshot",
        contract_check_ms=0,
        field_read_ms=0,
        action_ms=0,
        transition_wait_ms=0,
        screenshot_path=str(returned.get("screenshot_path") or ""),
        xml_path=str(returned.get("xml_path") or ""),
        extra={
            "returned_s10_snapshot_reused": True,
            "returned_s10_snapshot_xml_path": str(returned.get("xml_path") or ""),
            "returned_s10_snapshot_screenshot_path": str(returned.get("screenshot_path") or ""),
            "returned_s10_snapshot_source": "S14_RETURN_TO_S10_ATTEMPT",
            "post_s14_to_s15_initial_dump_skipped_due_to_reuse": True,
            "returned_list_source": context.get("returned_list_source"),
            "returned_list_source_verified": True,
            "s10_reliable_list_evidence": returned_reliable_evidence,
            "recognized_page": "S10",
            "foreground_package": returned.get("foreground_package"),
            "focused_window": returned.get("focused_window"),
            "visible_text_digest": list(returned.get("visible_texts") or [])[:40],
            "reason_category": "RUNTIME_REUSE_EXISTING_FRESH",
            "reason_detail": "POST_S14_TO_S15 reuses the S10 fresh snapshot produced by the successful S14 return attempt.",
            "solution": "reuse the verified S10 return evidence instead of immediately dumping S10 again",
        },
    )
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
        extra={
            "returned_s10_snapshot_reused": True,
            "returned_s10_snapshot_xml_path": str(returned.get("xml_path") or ""),
            "returned_s10_snapshot_screenshot_path": str(returned.get("screenshot_path") or ""),
            "returned_s10_snapshot_source": "S14_RETURN_TO_S10_ATTEMPT",
            "post_s14_to_s15_initial_dump_skipped_due_to_reuse": True,
            "returned_list_source": context.get("returned_list_source"),
            "returned_list_source_verified": True,
            "s10_reliable_list_evidence": returned_reliable_evidence,
        },
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
        snapshot = _capture_with_global_popup_guard(
            context,
            f"s14_return_to_s10_attempt_{attempt_index}",
            current_stage="S14_RETURN_TO_S10",
        )
        snapshot["target_brand"] = context["target_car"].brand
        snapshot["target_car"] = {
            "brand": context["target_car"].brand,
            "series": context["target_car"].series,
            "model_year": context["target_car"].model_year,
            "trim": context["target_car"].trim,
        }
        capture_metrics = snapshot.get("capture_metrics") or {}
        recognize_started = time.perf_counter()
        state = _recognize_mainline_page(recognizer, snapshot)
        recognize_ms = int((time.perf_counter() - recognize_started) * 1000)
        s10_reliable_evidence: dict[str, Any] = {}
        if state == "S10":
            expected_next_index = int(context.get("current_reference_index") or 0) + 1
            expected_card = _first_stage_expected_reference_card(context.get("first_stage_evidence") or {}, expected_next_index)
            s10_reliable_evidence = _s10_reliable_list_evidence(
                snapshot,
                target_reference_index=expected_next_index,
                expected_card=expected_card,
            )
            snapshot["s10_reliable_list_evidence"] = s10_reliable_evidence
        last_snapshot = snapshot
        last_state = state
        attempt = {
            "attempt_index": attempt_index,
            "recognized_page": state,
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
            "screenshot_ms": int(capture_metrics.get("screenshot_ms") or 0),
            "xml_dump_ms": int(capture_metrics.get("xml_ms") or 0),
            "recognize_ms": recognize_ms,
            "focused_window": snapshot.get("focused_window"),
            "foreground_package": snapshot.get("foreground_package"),
        }
        if s10_reliable_evidence:
            attempt["s10_reliable_list_evidence"] = s10_reliable_evidence
        context["s14_return_attempts"].append(attempt)
        timing.add(
            step_name="S14_RETURN_TO_S10_ATTEMPT",
            page_name="S14",
            action_name="single_back_then_fresh_until_s10",
            contract_check_ms=0,
            field_read_ms=int(capture_metrics.get("xml_ms") or 0) + recognize_ms,
            action_ms=action_ms,
            transition_wait_ms=350,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra={
                **attempt,
                "is_aggregate": False,
                "reason_category": "S14_RETURN_TO_S10",
                "reason_detail": "after each back action the runtime fresh-checks the page and stops immediately once S10 is recognized",
                "solution": "do not issue additional back actions after reliable S10 is reached",
            },
        )
        if state == "S10":
            if s10_reliable_evidence.get("reliable") is not True:
                continue
            context["returned_s10_snapshot"] = snapshot
            context["returned_s10_snapshot_source"] = "S14_RETURN_TO_S10_ATTEMPT"
            context["returned_s10_snapshot_attempt_index"] = attempt_index
            context["returned_s10_reliable_evidence"] = s10_reliable_evidence
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
                    "is_aggregate": True,
                    "aggregate_child_actions": ["S14_RETURN_TO_S10_ATTEMPT"],
                    "single_action_performance_source": "S14_RETURN_TO_S10_ATTEMPT",
                    "return_attempts": list(context.get("s14_return_attempts") or []),
                    "returned_list_source": "from_s14_fixed_return_to_s10",
                    "returned_list_source_verified": True,
                    "s10_reliable_list_evidence": s10_reliable_evidence,
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


EXPECTED_RULE_SOURCE = {
    "active_scoring_rule_version": SCORING_RULE_VERSION,
    "active_scoring_rule_doc": SCORING_RULE_DOC,
    "active_reference_selection_rule": REFERENCE_SELECTION_RULE,
    "active_pricing_rule_version": PRICING_RULE_VERSION,
    "active_pricing_rule_doc": PRICING_RULE_DOC,
    "active_competition_coefficient_version": COMPETITION_COEFFICIENT_VERSION,
    "active_competition_coefficient_doc": COMPETITION_COEFFICIENT_DOC,
}


def _pricing_rule_source_guard(fields_config: dict[str, Any]) -> dict[str, Any]:
    scoring = fields_config.get("scoring") or {}
    selection = fields_config.get("reference_selection") or {}
    pricing = fields_config.get("pricing") or {}
    competition = fields_config.get("competition_coefficient") or {}
    active = {
        "active_scoring_rule_version": scoring.get("scoring_rule_version"),
        "active_scoring_rule_doc": scoring.get("scoring_rule_doc"),
        "active_reference_selection_rule": selection.get("reference_selection_rule"),
        "active_pricing_rule_version": pricing.get("pricing_rule_version"),
        "active_pricing_rule_doc": pricing.get("pricing_rule_doc"),
        "active_competition_coefficient_version": competition.get("competition_coefficient_version"),
        "active_competition_coefficient_doc": competition.get("competition_coefficient_doc"),
        "active_config_files": ["config/fields.yaml"],
    }
    mismatches = [
        {"field": key, "expected": expected, "actual": active.get(key)}
        for key, expected in EXPECTED_RULE_SOURCE.items()
        if active.get(key) != expected
    ]
    pricing_path = ROOT / "src" / "guazi_app_data_system" / "pricing.py"
    try:
        active["active_pricing_py_version"] = hashlib.sha256(pricing_path.read_bytes()).hexdigest()[:16]
    except OSError:
        active["active_pricing_py_version"] = None
        mismatches.append({"field": "active_pricing_py_version", "expected": "readable pricing.py", "actual": None})
    active["rule_source_guard_passed"] = not mismatches
    active["rule_source_mismatches"] = mismatches
    active["contract_execution_guard"] = guard_scoring_rule(
        active_scoring_rule_version=active.get("active_scoring_rule_version"),
        source_file=active.get("active_scoring_rule_doc"),
    )
    return active


def _apply_pricing_rule_source_guard(context: dict[str, Any], page_name: str) -> dict[str, Any]:
    guard = _pricing_rule_source_guard(context["configs"]["fields"])
    context["pricing_rule_source_guard"] = guard
    context.setdefault("current_reference", {})["pricing_rule_source_guard"] = guard
    if not guard.get("rule_source_guard_passed"):
        issue = context["issues"].record(
            "PRICING_RULE_SOURCE_MISMATCH",
            page_name,
            "Active pricing/scoring/reference-selection rule source does not match the latest confirmed desktop rule documents.",
            guard,
            "manual_review",
        )
        raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
    return guard


def _score_result_has_reference_missing_required_field(score: ScoreResult | None) -> bool:
    if score is None:
        return True
    return any(str(reason).startswith("REFERENCE_") and "MISSING_FIELD_INCOMPLETE" in str(reason) for reason in score.review_reasons)


def _active_early_exit_versions(fields_config: dict[str, Any]) -> dict[str, Any]:
    guard = fields_config.get("rule_source_guard") or {}
    scoring = fields_config.get("scoring") or {}
    selection = fields_config.get("reference_selection") or {}
    pricing = fields_config.get("pricing") or {}
    competition = fields_config.get("competition_coefficient") or {}
    return {
        "active_page_contract_version": guard.get("active_page_contract_version"),
        "active_scoring_rule_version": guard.get("active_scoring_rule_version") or scoring.get("scoring_rule_version"),
        "active_reference_selection_rule": guard.get("active_reference_selection_rule")
        or selection.get("reference_selection_rule"),
        "active_pricing_rule_version": guard.get("active_pricing_rule_version") or pricing.get("pricing_rule_version"),
        "active_competition_coefficient_version": guard.get("active_competition_coefficient_version")
        or competition.get("competition_coefficient_version"),
    }


def _previous_low_reference_summary(
    previous_valid_lows: list[tuple[dict[str, Any], ReferenceCar, ScoreResult]],
) -> dict[str, Any] | None:
    if not previous_valid_lows:
        return None
    entry, reference, score = max(
        previous_valid_lows,
        key=lambda item: (
            float(item[2].score),
            -float(item[1].list_price_10k),
            -int(item[1].reference_index),
        ),
    )
    return {
        "reference_index": reference.reference_index,
        "reference_score": score.score,
        "price_yuan": entry.get("price_yuan"),
        "list_price_10k": reference.list_price_10k,
    }


def _history_item_by_reference_index(history: list[Any], reference_index: int | None) -> dict[str, Any] | None:
    if reference_index is None:
        return None
    for item in history or []:
        if isinstance(item, dict) and _safe_int(item.get("reference_index"), default=-1) == int(reference_index):
            return item
    return None


def _find_v33_complete_low_candidate(
    previous_valid_lows: list[tuple[dict[str, Any], ReferenceCar, ScoreResult]],
    reference_index: int | None,
) -> tuple[dict[str, Any], ReferenceCar, ScoreResult] | None:
    if reference_index is None:
        return None
    for entry, reference, score in previous_valid_lows:
        if int(reference.reference_index) == int(reference_index):
            return entry, reference, score
    return None


def _v33_previous_candidate_status(previous_record: dict[str, Any] | None) -> str:
    if not previous_record:
        return "PREVIOUS_REFERENCE_RECORD_NOT_FOUND"
    if (
        previous_record.get("low_score_skipped_incomplete") is True
        or previous_record.get("reference_status") == "LOW_SCORE_SKIPPED_INCOMPLETE"
        or previous_record.get("reference_early_exit") is True
    ):
        return "LOW_SCORE_SKIPPED_INCOMPLETE"
    if previous_record.get("reference_score_trustworthy") is False or previous_record.get("reference_score_preliminary") is True:
        return "PREVIOUS_REFERENCE_UNTRUSTED_OR_PRELIMINARY"
    if previous_record.get("reference_score_usable_for_boundary") is False or previous_record.get("excluded_from_boundary") is True:
        return "PREVIOUS_REFERENCE_NOT_USABLE_FOR_BOUNDARY"
    return "PREVIOUS_REFERENCE_INCOMPLETE_OR_UNUSABLE"


def _reference_order_reliable_for_early_exit(context: dict[str, Any], current_reference: dict[str, Any]) -> bool:
    index = _safe_int(current_reference.get("reference_index") or context.get("current_reference_index"), default=0)
    if index <= 0:
        return False
    first_stage = context.get("first_stage_evidence") or {}
    trisame_count = _first_stage_trisame_count(first_stage)
    if trisame_count is not None and index > trisame_count:
        return False
    evidence = (
        current_reference.get("s10_reliable_list_evidence")
        or context.get("startup_s10_reliable_evidence")
        or current_reference.get("startup_s10_reliable_evidence")
        or {}
    )
    if isinstance(evidence, dict) and evidence:
        if evidence.get("reliable") is False or evidence.get("source_reliable") is False:
            return False
        if evidence.get("selected_reference_card_gate_passed") is False:
            return False
        if evidence.get("target_card_matches_expected") is False:
            return False
    expected_card = _first_stage_expected_reference_card(first_stage, index)
    if expected_card:
        current_title = str(current_reference.get("selected_card_title") or current_reference.get("list_title") or "").strip()
        expected_title = str(expected_card.get("title") or expected_card.get("list_title") or "").strip()
        if expected_title and current_title and current_title != expected_title:
            return False
        if expected_card.get("list_price_10k") is not None and current_reference.get("list_price_10k") is not None:
            if not _float_same(current_reference.get("list_price_10k"), expected_card.get("list_price_10k")):
                return False
    return True


def _target_score_trustworthy_for_early_exit(target_score: ScoreResult | dict[str, Any] | None) -> bool:
    if target_score is None:
        return False
    if isinstance(target_score, ScoreResult):
        return bool(target_score.score is not None and not target_score.hard_reject)
    if isinstance(target_score, dict):
        return bool(target_score.get("score") is not None and not target_score.get("hard_reject"))
    return False


def _evaluate_reference_early_exit_for_runtime(
    context: dict[str, Any],
    *,
    target_score: ScoreResult,
    reference_score: ScoreResult,
    previous_valid_lows: list[tuple[dict[str, Any], ReferenceCar, ScoreResult]],
    repair_completion: dict[str, Any],
    rule_guard: dict[str, Any] | None = None,
    can_return_reliable_s10_override: bool | None = None,
) -> dict[str, Any]:
    current_reference = context.setdefault("current_reference", {})
    upper_bound = calculate_reference_score_upper_bound_for_early_exit(
        current_reference=current_reference,
        reference_score=reference_score,
        repair_completion=repair_completion,
    )
    previous_low_summary = _previous_low_reference_summary(previous_valid_lows)
    current_index = _safe_int(current_reference.get("reference_index") or context.get("current_reference_index"), default=0)
    next_index = current_index + 1 if current_index > 0 else None
    can_return_reliable_s10 = (
        bool(can_return_reliable_s10_override)
        if can_return_reliable_s10_override is not None
        else bool(
            context.get("returned_list_source_verified") is True
            or current_reference.get("returned_list_source_verified") is True
        )
    )
    no_unconfirmed_hard_risk = not bool(upper_bound.get("unconfirmed_hard_risk_present")) and not reference_score.hard_reject
    decision = evaluate_reference_early_exit_max_possible_score(
        current_reference_index=current_index or None,
        next_reference_index=next_index,
        target_score=target_score.score,
        partial_confirmed_score=upper_bound.get("partial_confirmed_score"),
        remaining_max_possible_score=upper_bound.get("remaining_max_possible_score"),
        pre_boundary_evidence=previous_low_summary,
        active_versions=_active_early_exit_versions(context["configs"]["fields"]),
        target_score_trustworthy=_target_score_trustworthy_for_early_exit(target_score),
        reference_order_reliable=_reference_order_reliable_for_early_exit(context, current_reference),
        mandatory_fields_collected=bool(upper_bound.get("mandatory_fields_collected")),
        mandatory_fields_missing=list(upper_bound.get("mandatory_fields_missing") or []),
        partial_confirmed_score_trustworthy=bool(upper_bound.get("partial_confirmed_score_trustworthy")),
        remaining_max_possible_score_deterministic=bool(
            upper_bound.get("remaining_max_possible_score_deterministic")
        ),
        no_unconfirmed_hard_risk=no_unconfirmed_hard_risk,
        can_return_reliable_s10=can_return_reliable_s10,
    )
    decision.update(
        {
            **upper_bound,
            "pre_boundary_evidence": previous_low_summary,
            "reference_order_reliable": _reference_order_reliable_for_early_exit(context, current_reference),
            "can_return_reliable_s10": can_return_reliable_s10,
            "no_unconfirmed_hard_risk": no_unconfirmed_hard_risk,
            "rule_source_guard_passed": bool((rule_guard or {}).get("rule_source_guard_passed", True)),
        }
    )
    return decision


def _damage_records_for_current_context(context: dict[str, Any]) -> list[DamageRecord]:
    records: list[DamageRecord] = []
    for item in (context.get("damage_by_part") or {}).values():
        if not isinstance(item, dict):
            continue
        part = str(item.get("part") or item.get("normalized_part") or "").strip()
        damage = str(item.get("normalized_damage") or item.get("damage_type") or "").strip()
        if part and damage:
            records.append(DamageRecord(part, damage))
    return records


def _build_current_reference_car_for_s14_in_flight(context: dict[str, Any]) -> ReferenceCar:
    current_reference = context.setdefault("current_reference", {})
    return ReferenceCar(
        reference_index=int(context.get("current_reference_index") or current_reference.get("reference_index") or 0),
        list_price_10k=float(current_reference.get("list_price_10k") or 0.0),
        list_year=int(current_reference.get("list_year") or 0),
        list_mileage_10k_km=float(current_reference.get("list_mileage_10k_km") or 0.0),
        transfer_count=int(current_reference.get("transfer_count") or 0),
        accident_count=int(
            current_reference.get("accident_count")
            if current_reference.get("accident_count") is not None
            else current_reference.get("claim_count") or 0
        ),
        max_accident_amount=current_reference.get("max_accident_amount")
        if current_reference.get("max_accident_amount") is not None
        else current_reference.get("max_claim_amount"),
        repair_counts=dict(current_reference.get("repair_counts") or {}),
        panel_repairs=_damage_records_for_current_context(context),
    )


def _evaluate_s14_in_flight_early_exit_for_runtime(
    context: dict[str, Any],
    *,
    selected_tab: dict[str, Any] | None = None,
    semantic_state: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_reference = context.setdefault("current_reference", {})
    repair_completion = _store_repair_item_completion_state(context)
    check_index = _safe_non_negative_int(context.get("s14_in_flight_early_exit_check_count")) + 1
    context["s14_in_flight_early_exit_check_count"] = check_index
    fields_config = context["configs"]["fields"]
    target = context["target_car"]
    target.panel_repairs = _damage_records_for_current_context(context)
    target_score = score_target(target, fields_config, current_year=2026)
    reference = _build_current_reference_car_for_s14_in_flight(context)
    reference_score = score_reference(reference, fields_config, current_year=2026)
    context["target_score"] = target_score.to_dict()
    current_reference["target_score"] = target_score.to_dict()
    current_reference["target_score_source"] = "score_target_runtime_s14_in_flight"
    current_reference["reference_score_in_flight"] = reference_score.score
    current_reference["reference_score_in_flight_components"] = reference_score.components
    current_reference["reference_score_input_in_flight"] = reference.to_dict()
    previous_valid_lows = _v3_low_candidates_from_history(context.get("reference_history", []), target_score)
    rule_guard = context.get("pricing_rule_source_guard") or {"rule_source_guard_passed": True}
    decision = _evaluate_reference_early_exit_for_runtime(
        context,
        target_score=target_score,
        reference_score=reference_score,
        previous_valid_lows=previous_valid_lows,
        repair_completion=repair_completion,
        rule_guard=rule_guard,
        can_return_reliable_s10_override=True,
    )
    missing_count = _safe_non_negative_int(repair_completion.get("missing_repair_count"))
    trace = {
        "check_index": check_index,
        "stage": "S14",
        "rule_clause_id": EARLY_EXIT_RULE_ID,
        "s14_in_flight_early_exit_checked": True,
        "s14_in_flight_early_exit_polling": True,
        "current_s14_item_index": _safe_non_negative_int(repair_completion.get("s14_collected_items_count")),
        "selected_tab_label": str((selected_tab or {}).get("label") or ""),
        "current_s14_key": str((semantic_state or {}).get("s14_key") or ""),
        "screenshot_path": str((snapshot or {}).get("screenshot_path") or ""),
        "xml_path": str((snapshot or {}).get("xml_path") or ""),
        "target_score": decision.get("target_score"),
        "partial_confirmed_score": decision.get("partial_confirmed_score"),
        "remaining_max_possible_score": decision.get("remaining_max_possible_score"),
        "max_possible_reference_score": decision.get("max_possible_reference_score"),
        "reference_score_upper_bound": decision.get("reference_score_upper_bound")
        or decision.get("max_possible_reference_score"),
        "s14_low_score_skip_reason": decision.get("s14_low_score_skip_reason"),
        "early_exit_allowed": bool(decision.get("early_exit_allowed")),
        "early_exit_reason": decision.get("early_exit_reason"),
        "early_exit_blockers": list(decision.get("early_exit_blockers") or []),
        "s13_total_repair_count": repair_completion.get("s13_total_repair_count"),
        "s14_collected_items_count": repair_completion.get("s14_collected_items_count"),
        "s14_items_skipped_if_triggered": missing_count,
    }
    decision.update(
        {
            "s14_in_flight_early_exit_checked": True,
            "s14_in_flight_early_exit_check_count": check_index,
            "s14_in_flight_early_exit_trace": trace,
            "s14_in_flight_early_exit_triggered": bool(decision.get("early_exit_allowed")),
            "s14_items_skipped_due_to_early_exit": missing_count if decision.get("early_exit_allowed") else 0,
        }
    )
    context.setdefault("s14_in_flight_early_exit_trace", []).append(trace)
    current_reference.update(
        {
            "s14_in_flight_early_exit_checked": True,
            "s14_in_flight_early_exit_check_count": check_index,
            "s14_in_flight_early_exit_trace": list(context.get("s14_in_flight_early_exit_trace") or []),
            "s14_in_flight_early_exit_decision": decision,
            "s14_items_skipped_due_to_early_exit": decision.get("s14_items_skipped_due_to_early_exit"),
        }
    )
    return decision


def _apply_reference_early_exit_decision_to_runtime(context: dict[str, Any], decision: dict[str, Any]) -> None:
    current_reference = context.setdefault("current_reference", {})
    if decision.get("early_exit_allowed") is not True:
        current_reference["early_exit_decision"] = decision
        current_reference["reference_early_exit"] = False
        return
    current_reference.update(
        {
            **decision,
            "reference_early_exit": True,
            "low_score_skipped_incomplete": True,
            "early_exit_rule_id": EARLY_EXIT_RULE_ID,
            "early_exit_rule_clause_id": decision.get("early_exit_rule_clause_id") or EARLY_EXIT_RULE_ID,
            "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
            "reference_score_trustworthy": False,
            "reference_score_preliminary": True,
            "reference_score_usable_for_boundary": False,
            "excluded_from_boundary": True,
            "excluded_from_boundary_reason": "LOW_SCORE_SKIPPED_INCOMPLETE",
            "reference_exclusion_reason": "LOW_SCORE_SKIPPED_INCOMPLETE",
            "excluded_reference_reason": "LOW_SCORE_SKIPPED_INCOMPLETE",
            "excluded_from_final_reference_selection": True,
            "usable_for_boundary": False,
            "usable_for_pre_boundary": False,
            "final_reference_requires_recollect_if_selected": True,
            "early_exit_decision": decision,
        }
    )
    context.setdefault("early_rejected_reference_history", []).append(dict(current_reference))
    context.setdefault("excluded_reference_history", []).append(dict(current_reference))


def _v33_boundary_previous_recollect_terminal_trace(
    context: dict[str, Any],
    current_reference: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_reference = current_reference if isinstance(current_reference, dict) else context.get("current_reference") or {}
    selection = selection if isinstance(selection, dict) else context.get("selection") or {}
    plan = context.get("continuation_plan") if isinstance(context.get("continuation_plan"), dict) else {}
    current_index = _safe_int(
        current_reference.get("current_reference_index")
        or current_reference.get("reference_index")
        or context.get("current_reference_index"),
        default=0,
    )
    recollect_index = _safe_int(
        plan.get("recollect_reference_index")
        or selection.get("recollect_reference_index")
        or context.get("v33_recollect_next_reference_index"),
        default=0,
    )
    boundary_index = _safe_int(
        plan.get("boundary_reference_index")
        or selection.get("boundary_reference_index")
        or (context.get("v33_recollect_boundary_reference") or {}).get("reference_index"),
        default=0,
    )
    final_candidate_index = _safe_int(
        plan.get("final_reference_candidate_index")
        or selection.get("final_reference_candidate_index"),
        default=0,
    )
    recollect_reason = str(
        plan.get("recollect_reason")
        or selection.get("recollect_reason")
        or plan.get("continue_reason")
        or selection.get("continue_reason")
        or ""
    )
    active = bool(
        current_index > 0
        and recollect_index == current_index
        and (final_candidate_index in (0, current_index))
        and boundary_index > current_index
        and (
            plan.get("final_reference_recollect_required") is True
            or selection.get("final_reference_recollect_required") is True
            or recollect_reason in V33_BOUNDARY_PREVIOUS_RECOLLECT_REASONS
            or str(plan.get("continue_reason") or "") == "BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
        )
    )
    candidate_status = str(
        current_reference.get("reference_status")
        or selection.get("final_reference_candidate_status")
        or plan.get("final_reference_candidate_status")
        or ""
    )
    candidate_trustworthy = bool(
        current_reference.get("fully_collected_trusted") is True
        or (
            current_reference.get("reference_score_trustworthy") is True
            and current_reference.get("reference_score_usable_for_boundary") is True
        )
    )
    return {
        "v33_boundary_previous_recollect_active": active,
        "v33_recollect_reference_index": recollect_index or None,
        "v33_boundary_reference_index": boundary_index or None,
        "v33_final_reference_candidate_index": final_candidate_index or None,
        "v33_recollect_attempted": active,
        "v33_recollect_completed": False,
        "v33_recollect_terminal_context": active,
        "v33_recollect_terminal_reference_index": current_index or None,
        "v33_recollect_terminal_boundary_reference_index": boundary_index or None,
        "v33_recollect_terminal_candidate_status": candidate_status,
        "v33_recollect_terminal_candidate_trustworthy": candidate_trustworthy,
        "v33_recollect_terminal_decision": "",
        "v33_recollect_blocked_low_score_continue": False,
        "v33_recollect_prevented_next_boundary_reclick": False,
        "recollect_reason": recollect_reason,
        "boundary_reference_score": plan.get("boundary_reference_score")
        or selection.get("boundary_reference_score")
        or (context.get("v33_recollect_boundary_reference") or {}).get("reference_score"),
        "target_score": plan.get("target_score") or selection.get("target_score"),
    }


def _v33_recollect_terminal_reference_is_trusted(
    *,
    reference_valid_for_boundary: bool,
    current_reference: dict[str, Any],
    reference_score: ScoreResult,
) -> bool:
    return bool(
        reference_valid_for_boundary
        and current_reference.get("s15_entry_allowed") is True
        and current_reference.get("reference_score_trustworthy") is True
        and current_reference.get("reference_score_usable_for_boundary") is True
        and not reference_score.hard_reject
        and not _score_result_has_reference_missing_required_field(reference_score)
    )


def _v33_recollect_needs_review_result(
    context: dict[str, Any],
    *,
    trace: dict[str, Any],
    continue_history: list[dict[str, Any]],
    continue_history_gate: dict[str, Any],
) -> dict[str, Any]:
    current_reference = context.get("current_reference") or {}
    selection = context.get("selection") or {}
    target_score = context.get("target_score") or {}
    target_score_value = target_score.score if isinstance(target_score, ScoreResult) else None
    if isinstance(target_score, dict):
        target_score_value = target_score.get("score")
    if target_score_value is None:
        target_score_value = trace.get("target_score")
    reason = "V3.3 boundary previous reference recollect still incomplete or untrusted; manual review required."
    trace = {
        **trace,
        "v33_recollect_completed": False,
        "v33_recollect_terminal_decision": "NEEDS_REVIEW",
        "v33_recollect_blocked_low_score_continue": True,
        "v33_recollect_prevented_next_boundary_reclick": True,
    }
    return {
        "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
        "status": "NEEDS_REVIEW",
        "final_status": "NEEDS_REVIEW",
        "current_state": "NEEDS_REVIEW",
        "business_status": "NEEDS_REVIEW",
        "technical_status": "SUCCEEDED",
        "issue_code": V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
        "stop_code": V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
        "manual_review_required": True,
        "auto_pricing_allowed": False,
        "final_price_allowed": False,
        "reason": reason,
        "manual_review_reason": reason,
        "manual_review_reasons": [V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW],
        "reference_selection_rule": REFERENCE_SELECTION_RULE,
        "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
        "selected_reference_index": trace.get("v33_recollect_terminal_reference_index"),
        "final_reference_index": None,
        "boundary_reference_index": trace.get("v33_recollect_terminal_boundary_reference_index"),
        "boundary_reference_score": trace.get("boundary_reference_score") or selection.get("boundary_reference_score"),
        "target_score": target_score,
        "target_score_value": target_score_value,
        "final_reference_candidate_index": trace.get("v33_final_reference_candidate_index"),
        "recollect_reference_index": trace.get("v33_recollect_reference_index"),
        "recollect_reason": trace.get("recollect_reason"),
        "recollect_terminal_decision": "NEEDS_REVIEW",
        "candidate_status": trace.get("v33_recollect_terminal_candidate_status"),
        "current_reference": current_reference,
        "selection": selection,
        "reference_history": continue_history,
        "reference_history_write_gate": continue_history_gate,
        "v33_recollect_terminal_trace": trace,
    }


def _v33_low_score_skip_continue_fields(context: dict[str, Any]) -> dict[str, Any] | None:
    current_reference = context.get("current_reference") or {}
    if not isinstance(current_reference, dict):
        return None
    terminal_trace = _v33_boundary_previous_recollect_terminal_trace(context, current_reference)
    if terminal_trace.get("v33_recollect_terminal_context"):
        terminal_trace.update(
            {
                "v33_recollect_terminal_decision": "DEFER_TO_RECOLLECT_TERMINAL_DECISION",
                "v33_recollect_blocked_low_score_continue": True,
                "v33_recollect_prevented_next_boundary_reclick": True,
            }
        )
        current_reference.update(terminal_trace)
        context["v33_recollect_terminal_trace"] = terminal_trace
        return None
    decision = current_reference.get("early_exit_decision")
    if not isinstance(decision, dict):
        decision = current_reference.get("s14_in_flight_early_exit_decision")
    if not isinstance(decision, dict):
        decision = {}
    current_index = _safe_int(
        current_reference.get("current_reference_index")
        or current_reference.get("reference_index")
        or decision.get("current_reference_index")
        or context.get("current_reference_index"),
        default=0,
    )
    next_index = _safe_int(
        decision.get("next_reference_index")
        or current_reference.get("next_reference_index")
        or context.get("next_reference_index")
        or (current_index + 1 if current_index > 0 else 0),
        default=0,
    )
    context_target_score = context.get("target_score")
    if isinstance(context_target_score, dict):
        context_target_score = context_target_score.get("score")
    target_score = _safe_float_value(
        decision.get("target_score")
        or current_reference.get("target_score")
        or context_target_score
    )
    upper_bound = _safe_float_value(
        current_reference.get("reference_score_upper_bound")
        or current_reference.get("max_possible_reference_score")
        or decision.get("reference_score_upper_bound")
        or decision.get("max_possible_reference_score")
    )
    legal_low_score_skip = (
        current_reference.get("reference_status") == "LOW_SCORE_SKIPPED_INCOMPLETE"
        or current_reference.get("low_score_skipped_incomplete") is True
        or decision.get("reference_status") == "LOW_SCORE_SKIPPED_INCOMPLETE"
    ) and (
        current_reference.get("s14_low_score_skip_triggered") is True
        or decision.get("s14_low_score_skip_triggered") is True
        or decision.get("early_exit_decision") == "LOW_SCORE_SKIP_AND_CONTINUE_NEXT_REFERENCE"
    )
    returned_to_s10 = (
        current_reference.get("return_to_s10_after_low_score_skip") is True
        or decision.get("return_to_s10_after_low_score_skip") is True
        or context.get("return_to_s10_after_low_score_skip") is True
    )
    return_verified = (
        current_reference.get("returned_list_source_verified") is True
        or decision.get("returned_list_source_verified") is True
        or context.get("returned_list_source_verified") is True
    )
    if not (
        legal_low_score_skip
        and returned_to_s10
        and return_verified
        and current_index > 0
        and next_index > current_index
        and target_score is not None
        and upper_bound is not None
        and upper_bound < target_score
    ):
        return None
    trisame_count = _first_stage_trisame_count(context.get("first_stage_evidence") or {})
    remaining = max(0, int(trisame_count) - next_index + 1) if trisame_count is not None else None
    current_reference.update(
        {
            "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
            "reference_score_trustworthy": False,
            "reference_score_usable_for_boundary": False,
            "usable_for_boundary": False,
            "usable_for_pre_boundary": False,
            "excluded_from_s16": True,
            "s15_entry_block_reason": None,
        }
    )
    return {
        "status": "CONTINUE_NEXT_REFERENCE",
        "final_status": "CONTINUE_NEXT_REFERENCE",
        "current_state": "CONTINUE_NEXT_REFERENCE",
        "business_status": "CONTINUE_NEXT_REFERENCE",
        "technical_status": "INCOMPLETE",
        "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
        "current_reference_index": current_index,
        "next_reference_index": next_index,
        "remaining_reference_count": remaining,
        "continue_next_reference": True,
        "dispatcher_should_continue": True,
        "should_continue_reference_collection": True,
        "continue_reason": "EARLY_EXIT_CONTINUE_NEXT_REFERENCE",
        "continuation_source": "low_score_skip",
        "terminal": False,
        "failed": False,
        "cancelled": False,
        "s15_entry_allowed": False,
        "s15_blocked_reason": None,
        "s15_entry_block_reason": None,
        "stop_code": "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE",
        "issue_code": "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE",
    }


def _safe_float_value(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("score")
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def handle_s15(context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    timing: TimingRecorder = context["timing"]
    issues: IssueRecorder = context["issues"]
    started = time.perf_counter()
    current_reference = context.get("current_reference") or {}
    repair_completion = _store_repair_item_completion_state(context)
    if context.get("s14_triggered"):
        s14_metrics = _s14_completion_metrics(context)
        current_item_done = repair_completion.get("current_s14_item_done") is True
        whole_vehicle_complete = repair_completion.get("s14_whole_vehicle_collection_complete") is True
        if not current_item_done and not whole_vehicle_complete:
            issue = issues.record(
                "S15_BLOCKED_BY_INCOMPLETE_S14",
                "S15",
                "S15 blocked because S14 did not complete even the current condition item.",
                {
                    "current_reference": current_reference,
                    "repair_completion": repair_completion,
                    "s14_metrics": s14_metrics,
                    "all_s14_tabs": context.get("all_s14_tabs"),
                    "s14_tab_records": context.get("s14_tab_records"),
                    "s14_image_records": context.get("s14_image_records"),
                    "s14_horizontal_swipes": context.get("s14_horizontal_swipes"),
                },
                "manual_review",
            )
            raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
        if "repair_items" not in current_reference:
            issue = issues.record(
                "S15_BLOCKED_BY_INCOMPLETE_S14",
                "S15",
                "S15 blocked because S14 did not persist repair item evidence for scoring.",
                {
                    "current_reference": current_reference,
                    "repair_completion": repair_completion,
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
    if repair_completion.get("s15_entry_allowed") is not True:
        existing_exclusion_reason = (
            current_reference.get("excluded_from_boundary_reason")
            or current_reference.get("reference_exclusion_reason")
            or current_reference.get("excluded_reference_reason")
        )
        exclusion_reason = existing_exclusion_reason or "UNTRUSTED_REFERENCE_SCORE"
        current_reference["reference_score_trustworthy"] = False
        current_reference["reference_score_preliminary"] = True
        current_reference["reference_score_usable_for_boundary"] = False
        current_reference["excluded_from_boundary"] = True
        current_reference["excluded_from_boundary_reason"] = exclusion_reason
        current_reference["reference_exclusion_reason"] = exclusion_reason
        current_reference["excluded_reference_reason"] = exclusion_reason
        current_reference["reference_score_invalid_reason"] = "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE"
        current_reference["s15_entry_allowed"] = False
        current_reference["s15_entry_block_reason"] = repair_completion.get("s15_entry_block_reason") or "S15_BLOCKED_BY_INCOMPLETE_S14_WHOLE_VEHICLE_GATE"
    else:
        current_reference["reference_score_trustworthy"] = True
        current_reference["reference_score_preliminary"] = False
        current_reference["reference_score_usable_for_boundary"] = True
        current_reference["excluded_from_boundary"] = False
        current_reference["excluded_from_boundary_reason"] = ""
        current_reference["reference_score_invalid_reason"] = ""
        current_reference["s15_entry_allowed"] = True
    current_reference["s15_entry_reason"] = repair_completion.get("s15_entry_reason")
    if current_reference.get("reference_condition_needs_review"):
        current_reference["reference_score_trustworthy"] = False
        current_reference["reference_score_invalid_reason"] = S14_CONTRACT_DEGRADED_NEEDS_REVIEW
        current_reference["manual_review_required"] = True
        current_reference.setdefault("manual_review_reasons", [])
        if S14_CONTRACT_DEGRADED_NEEDS_REVIEW not in current_reference["manual_review_reasons"]:
            current_reference["manual_review_reasons"].append(S14_CONTRACT_DEGRADED_NEEDS_REVIEW)
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
    rule_guard = _apply_pricing_rule_source_guard(context, "S15")
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
    reference_score = score_reference(reference, fields_config, current_year=2026)
    reference.score = reference_score.score
    reference_score_gap_to_target = round(target_score.score - reference_score.score, 2)
    reference_score_gte_target = reference_score.score >= target_score.score
    reference_score_exact_match = reference_score.score == target_score.score
    reference_valid_for_boundary = (
        _reference_score_usable_for_boundary(current_reference)
        and not reference_score.hard_reject
        and not _score_result_has_reference_missing_required_field(reference_score)
    )
    reference_complete_trusted_before_early_exit = _v33_recollect_terminal_reference_is_trusted(
        reference_valid_for_boundary=reference_valid_for_boundary,
        current_reference=current_reference,
        reference_score=reference_score,
    )
    candidate_entry = {
        "reference_index": reference.reference_index,
        "reference_score": reference_score.score,
        "score_gap_to_target": reference_score_gap_to_target,
        "price_yuan": round(reference.list_price_10k * 10000),
        "list_price_10k": reference.list_price_10k,
        "eligible_for_v3_boundary": reference_valid_for_boundary,
    }
    previous_valid_lows = _v3_low_candidates_from_history(context.get("reference_history", []), target_score)
    early_exit_decision = _evaluate_reference_early_exit_for_runtime(
        context,
        target_score=target_score,
        reference_score=reference_score,
        previous_valid_lows=previous_valid_lows,
        repair_completion=repair_completion,
        rule_guard=rule_guard,
    )
    _apply_reference_early_exit_decision_to_runtime(context, early_exit_decision)
    current_reference = context["current_reference"]
    v33_recollect_terminal_trace = _v33_boundary_previous_recollect_terminal_trace(context, current_reference)
    if early_exit_decision.get("early_exit_allowed") is True and not (
        v33_recollect_terminal_trace.get("v33_recollect_terminal_context")
        and reference_complete_trusted_before_early_exit
    ):
        reference_valid_for_boundary = False
        candidate_entry["eligible_for_v3_boundary"] = False
        candidate_entry["reference_early_exit"] = True
        candidate_entry["low_score_skipped_incomplete"] = True
        candidate_entry["excluded_reason"] = "LOW_SCORE_SKIPPED_INCOMPLETE"
        if "LOW_SCORE_SKIPPED_INCOMPLETE" not in reference_score.review_reasons:
            reference_score.review_reasons.append("LOW_SCORE_SKIPPED_INCOMPLETE")
    if v33_recollect_terminal_trace.get("v33_recollect_terminal_context") and reference_complete_trusted_before_early_exit:
        final_price = round(reference.list_price_10k * 10000)
        reference.is_final_reference = True
        v33_recollect_terminal_trace.update(
            {
                "v33_recollect_completed": True,
                "v33_recollect_terminal_candidate_status": "COMPLETE_TRUSTWORTHY_LOW_SCORE",
                "v33_recollect_terminal_candidate_trustworthy": True,
                "v33_recollect_terminal_decision": "USE_AS_FINAL_REFERENCE",
                "v33_recollect_blocked_low_score_continue": True,
                "v33_recollect_prevented_next_boundary_reclick": True,
            }
        )
        current_reference.update(
            {
                **v33_recollect_terminal_trace,
                "reference_early_exit": False,
                "low_score_skipped_incomplete": False,
                "reference_status": "FULLY_COLLECTED_TRUSTED",
                "reference_score_trustworthy": True,
                "reference_score_preliminary": False,
                "reference_score_usable_for_boundary": True,
                "excluded_from_boundary": False,
                "excluded_from_boundary_reason": "",
                "reference_exclusion_reason": "",
                "excluded_reference_reason": "",
                "excluded_from_final_reference_selection": False,
                "usable_for_boundary": True,
                "usable_for_pre_boundary": True,
                "final_reference_recollect_done": True,
                "final_reference_recollect_required": False,
                "recollection_completed": True,
                "fully_collected_trusted": True,
            }
        )
        selection = {
            "selected_reference": reference,
            "selected_score": reference_score,
            "review_reasons": list(reference_score.review_reasons),
            "manual_review_required": False,
            "auto_pricing_allowed": True,
            "reference_selection_rule": REFERENCE_SELECTION_RULE,
            "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
            "boundary_confirmed": True,
            "boundary_reference_index": v33_recollect_terminal_trace.get("v33_recollect_terminal_boundary_reference_index"),
            "boundary_reference_score": v33_recollect_terminal_trace.get("boundary_reference_score"),
            "boundary_reference_price_yuan": (context.get("continuation_plan") or {}).get("boundary_reference_price_yuan"),
            "pre_boundary_reference_index": reference.reference_index,
            "final_reference_candidate_index": reference.reference_index,
            "final_reference_candidate_status": "COMPLETE_TRUSTWORTHY_LOW_SCORE",
            "final_reference_recollect_required": False,
            "final_reference_recollect_done": True,
            "recollection_completed": True,
            "recollect_reference_index": reference.reference_index,
            "recollect_reason": v33_recollect_terminal_trace.get("recollect_reason"),
            "final_reference_index": reference.reference_index,
            "selected_reference_index": reference.reference_index,
            "final_reference_score": reference_score.score,
            "final_reference_price": final_price,
            "final_reference_price_yuan": final_price,
            "final_reference_selection_reason": "boundary_previous_reference_recollect_complete_trusted",
            "candidate_reference_pool": [candidate_entry],
            "manual_review_reason": None,
            "v33_recollect_terminal_trace": v33_recollect_terminal_trace,
        }
    elif v33_recollect_terminal_trace.get("v33_recollect_terminal_context"):
        v33_recollect_terminal_trace.update(
            {
                "v33_recollect_completed": False,
                "v33_recollect_terminal_decision": "NEEDS_REVIEW",
                "v33_recollect_blocked_low_score_continue": True,
                "v33_recollect_prevented_next_boundary_reclick": True,
            }
        )
        current_reference.update(v33_recollect_terminal_trace)
        selection = {
            "selected_reference": None,
            "selected_score": None,
            "review_reasons": [V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW],
            "manual_review_required": True,
            "auto_pricing_allowed": False,
            "final_price_allowed": False,
            "reference_selection_rule": REFERENCE_SELECTION_RULE,
            "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
            "boundary_confirmed": True,
            "boundary_reference_index": v33_recollect_terminal_trace.get("v33_recollect_terminal_boundary_reference_index"),
            "boundary_reference_score": v33_recollect_terminal_trace.get("boundary_reference_score"),
            "boundary_reference_price_yuan": (context.get("continuation_plan") or {}).get("boundary_reference_price_yuan"),
            "pre_boundary_reference_index": reference.reference_index,
            "final_reference_candidate_index": reference.reference_index,
            "final_reference_candidate_status": current_reference.get("reference_status") or "LOW_SCORE_SKIPPED_INCOMPLETE",
            "final_reference_recollect_required": False,
            "final_reference_recollect_done": False,
            "recollection_completed": False,
            "recollect_reference_index": reference.reference_index,
            "recollect_reason": v33_recollect_terminal_trace.get("recollect_reason"),
            "final_reference_index": None,
            "selected_reference_index": reference.reference_index,
            "final_reference_score": None,
            "final_reference_price": None,
            "final_reference_price_yuan": None,
            "final_reference_selection_reason": "boundary_previous_reference_recollect_still_incomplete_needs_review",
            "candidate_reference_pool": [candidate_entry],
            "manual_review_reason": V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
            "issue_code": V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
            "v33_recollect_terminal_trace": v33_recollect_terminal_trace,
        }
    elif not reference_valid_for_boundary:
        selection = {
            "selected_reference": None,
            "selected_score": None,
            "review_reasons": list(reference_score.review_reasons),
            "manual_review_required": False,
            "auto_pricing_allowed": False,
            "reference_selection_rule": REFERENCE_SELECTION_RULE,
            "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
            "boundary_confirmed": False,
            "candidate_reference_pool": [],
            "excluded_references": [
                {
                    "reference_index": reference.reference_index,
                    "reference_score": reference_score.score,
                    "excluded_reason": current_reference.get("excluded_from_boundary_reason")
                    or "reference_required_field_missing_or_hard_reject",
                    "reference_early_exit": bool(current_reference.get("reference_early_exit")),
                    "early_exit_rule_id": current_reference.get("early_exit_rule_id"),
                }
            ],
        }
    elif reference_score_gte_target:
        boundary_entry = {
            "reference_index": reference.reference_index,
            "reference_score": reference_score.score,
            "price_yuan": round(reference.list_price_10k * 10000),
            "list_price_10k": reference.list_price_10k,
        }
        final_candidate_index = int(reference.reference_index) - 1
        previous_record = _history_item_by_reference_index(context.get("reference_history", []), final_candidate_index)
        previous_low = _find_v33_complete_low_candidate(previous_valid_lows, final_candidate_index)
        if final_candidate_index < 1:
            selection = {
                "selected_reference": None,
                "selected_score": None,
                "review_reasons": ["FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE"],
                "manual_review_required": True,
                "auto_pricing_allowed": False,
                "reference_selection_rule": REFERENCE_SELECTION_RULE,
                "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
                "boundary_confirmed": True,
                "boundary_reference_index": reference.reference_index,
                "boundary_reference_score": reference_score.score,
                "boundary_reference_price_yuan": round(reference.list_price_10k * 10000),
                "pre_boundary_reference_index": None,
                "final_reference_candidate_index": None,
                "final_reference_candidate_status": "MISSING_PREVIOUS_REFERENCE",
                "final_reference_index": None,
                "final_reference_score": None,
                "final_reference_price": None,
                "final_reference_price_yuan": None,
                "final_reference_selection_reason": "first_boundary_has_no_previous_reference",
                "candidate_reference_pool": [entry for entry, _ref, _score in previous_valid_lows] + [boundary_entry],
                "manual_review_reason": "FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE",
            }
        elif previous_low is None:
            candidate_status = _v33_previous_candidate_status(previous_record)
            selection = {
                "selected_reference": None,
                "selected_score": None,
                "review_reasons": ["BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT_REQUIRED"],
                "manual_review_required": False,
                "auto_pricing_allowed": False,
                "reference_selection_rule": REFERENCE_SELECTION_RULE,
                "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
                "boundary_confirmed": True,
                "boundary_reference_index": reference.reference_index,
                "boundary_reference_score": reference_score.score,
                "boundary_reference_price_yuan": round(reference.list_price_10k * 10000),
                "pre_boundary_reference_index": final_candidate_index,
                "final_reference_candidate_index": final_candidate_index,
                "final_reference_candidate_status": candidate_status,
                "final_reference_recollect_required": True,
                "final_reference_recollect_done": False,
                "recollect_reference_index": final_candidate_index,
                "recollect_reason": "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE_OR_SKIPPED",
                "final_reference_index": None,
                "final_reference_score": None,
                "final_reference_price": None,
                "final_reference_price_yuan": None,
                "final_reference_selection_reason": "boundary_previous_reference_requires_recollect",
                "candidate_reference_pool": [entry for entry, _ref, _score in previous_valid_lows] + [boundary_entry],
                "manual_review_reason": None,
            }
            context["v33_recollect_next_reference_index"] = final_candidate_index
            context["v33_recollect_boundary_reference"] = boundary_entry
            context["v33_recollect_candidate_status"] = candidate_status
        else:
            previous_entry, previous_ref, previous_score = previous_low
            previous_ref.is_final_reference = True
            final_price = round(previous_ref.list_price_10k * 10000)
            selection = {
                "selected_reference": previous_ref,
                "selected_score": previous_score,
                "review_reasons": list(previous_score.review_reasons),
                "manual_review_required": False,
                "auto_pricing_allowed": True,
                "reference_selection_rule": REFERENCE_SELECTION_RULE,
                "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
                "boundary_confirmed": True,
                "boundary_reference_index": reference.reference_index,
                "boundary_reference_score": reference_score.score,
                "boundary_reference_price_yuan": round(reference.list_price_10k * 10000),
                "pre_boundary_reference_index": previous_ref.reference_index,
                "final_reference_candidate_index": final_candidate_index,
                "final_reference_candidate_status": "COMPLETE_TRUSTWORTHY_LOW_SCORE",
                "final_reference_recollect_required": False,
                "final_reference_recollect_done": False,
                "final_reference_index": previous_ref.reference_index,
                "final_reference_score": previous_score.score,
                "final_reference_price": final_price,
                "final_reference_price_yuan": final_price,
                "final_reference_selection_reason": "boundary_previous_reference_complete_trustworthy",
                "candidate_reference_pool": [entry for entry, _ref, _score in previous_valid_lows] + [boundary_entry],
                "manual_review_reason": None,
            }
    else:
        selection = {
            "selected_reference": None,
            "selected_score": None,
            "review_reasons": list(reference_score.review_reasons),
            "manual_review_required": False,
            "auto_pricing_allowed": False,
            "reference_selection_rule": REFERENCE_SELECTION_RULE,
            "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
            "boundary_confirmed": False,
            "boundary_reference_index": None,
            "boundary_reference_score": None,
            "pre_boundary_reference_index": reference.reference_index,
            "candidate_reference_pool": [entry for entry, _ref, _score in previous_valid_lows] + [candidate_entry],
            "final_reference_selection_reason": "low_score_candidate_continue_until_boundary_reference",
        }
    if special_reference["manual_review_required"]:
        selection.setdefault("review_reasons", []).extend(special_reference["manual_review_reasons"])
        selection["manual_review_required"] = True
    context["target_score"] = target_score.to_dict()
    context["current_reference"]["target_score"] = target_score.to_dict()
    context["current_reference"]["target_score_source"] = "score_target_runtime_s15"
    context["selection"] = selection
    context["current_reference"]["reference_score"] = reference_score.score
    context["current_reference"]["reference_score_components"] = reference_score.components
    context["current_reference"]["reference_score_review_reasons"] = list(reference_score.review_reasons)
    context["current_reference"]["scoring_components"] = reference_score.components
    context["current_reference"]["deduction_items"] = [
        {
            "part": item.get("part"),
            "damage_type": item.get("normalized_damage") or item.get("damage_type"),
        }
        for item in context["damage_by_part"].values()
        if isinstance(item, dict)
    ]
    context["current_reference"]["panel_repairs_used_for_scoring"] = list(context["current_reference"]["deduction_items"])
    context["current_reference"]["score_input_summary"] = {
        "reference_index": reference.reference_index,
        "panel_repairs_count": len(context["current_reference"]["panel_repairs_used_for_scoring"]),
        "repair_counts": dict(reference.repair_counts),
        "s13_total_repair_count": repair_completion.get("s13_total_repair_count"),
        "s14_collected_items_count": repair_completion.get("s14_collected_items_count"),
        "uncollected_repair_count": repair_completion.get("missing_repair_count"),
    }
    context["current_reference"]["score_trace"] = {
        "scoring_rule_version": SCORING_RULE_VERSION,
        "rule_source_verified": True,
        "score_is_preliminary": not reference_valid_for_boundary,
        "uncollected_repair_count": repair_completion.get("missing_repair_count"),
        "components": reference_score.components,
        "review_reasons": list(reference_score.review_reasons),
    }
    context["current_reference"]["score_is_preliminary"] = not reference_valid_for_boundary
    context["current_reference"]["uncollected_repair_count"] = repair_completion.get("missing_repair_count")
    context["current_reference"]["scoring_rule_version"] = SCORING_RULE_VERSION
    context["current_reference"]["rule_source_verified"] = True
    context["current_reference"]["reference_selection_rule"] = REFERENCE_SELECTION_RULE
    context["current_reference"]["reference_selection_rule_version"] = REFERENCE_SELECTION_RULE
    context["current_reference"]["reference_score_lte_target_score"] = bool(reference_score.score <= target_score.score)
    context["current_reference"]["reference_score_gte_target_score"] = bool(reference_score.score >= target_score.score)
    context["current_reference"]["score_diff_reference_minus_target"] = round(reference_score.score - target_score.score, 2)
    context["current_reference"]["score_gap_to_target"] = reference_score_gap_to_target
    context["current_reference"]["eligible_boundary_reference"] = bool(reference_valid_for_boundary)
    context["current_reference"]["boundary_reference"] = bool(reference_valid_for_boundary and reference_score_gte_target)
    context["current_reference"]["early_exit_decision"] = early_exit_decision
    context["current_reference"]["score_upper_bound_components"] = early_exit_decision.get("score_upper_bound_components")
    context["current_reference"]["max_possible_reference_score"] = early_exit_decision.get("max_possible_reference_score")
    context["current_reference"]["remaining_max_possible_score"] = early_exit_decision.get("remaining_max_possible_score")
    context["current_reference"]["partial_confirmed_score"] = early_exit_decision.get("partial_confirmed_score")
    context["current_reference"]["pricing_rule_source_guard"] = rule_guard
    context["current_reference"]["scoring_contract_guard"] = guard_scoring_rule(
        active_scoring_rule_version=SCORING_RULE_VERSION,
        source_file=SCORING_RULE_DOC,
        components=reference_score.components,
        deduction_items=context["current_reference"].get("deduction_items") or [],
        score_input_summary=context["current_reference"].get("score_input_summary") or {},
    )
    scoring_reference_history, scoring_reference_history_gate = _safe_reference_history_with_current_reference(
        context,
        purpose="s15_reference_selection_contract_guard",
        require_identity=True,
        require_physical_evidence=True,
    )
    _raise_if_reference_history_write_blocked(
        context,
        scoring_reference_history_gate,
        page="S15",
        message="S15 reference scoring cannot continue until current reference has physical UI transition proof.",
    )
    selection_guard = guard_reference_selection_rule(
        active_reference_selection_rule=REFERENCE_SELECTION_RULE,
        target_score=target_score.score,
        reference_scores=[
            float(item.get("reference_score"))
            for item in scoring_reference_history
            if isinstance(item, dict) and item.get("reference_score") is not None
        ],
        references=[
            item
            for item in scoring_reference_history
            if isinstance(item, dict)
        ],
        selected_reference_index=selection.get("final_reference_index"),
        boundary_reference_index=selection.get("boundary_reference_index"),
        exclusion_reasons=list(selection.get("review_reasons") or []),
    )
    trisame_count_for_trace = _first_stage_trisame_count(context.get("first_stage_evidence") or {})
    current_index_for_trace = int(context.get("current_reference_index") or reference.reference_index or 0)
    remaining_reference_count = (
        max(0, int(trisame_count_for_trace) - current_index_for_trace)
        if trisame_count_for_trace is not None
        else None
    )
    reference_scores_for_trace = [
        float(item.get("reference_score"))
        for item in scoring_reference_history
        if isinstance(item, dict) and item.get("reference_score") is not None
    ]
    recollect_required_by_v33 = bool(selection.get("final_reference_recollect_required") is True)
    continue_required_by_v3 = bool(
        (
            selection.get("boundary_confirmed") is not True
            and selection.get("manual_review_required") is not True
            and remaining_reference_count not in (None, 0)
        )
        or recollect_required_by_v33
    )
    next_reference_index_by_v33 = (
        _safe_int(selection.get("recollect_reference_index"), default=0)
        if recollect_required_by_v33
        else current_index_for_trace + 1
    )
    v3_reference_selection_trace = {
        "rule_clause_id": selection_guard.get("rule_clause_id") or "S15_V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "rule_source_file": selection_guard.get("rule_source_file"),
        "rule_source_version": selection_guard.get("rule_source_version"),
        "coverage_status": selection_guard.get("coverage_status"),
        "contract_action_plan_id": selection_guard.get("contract_action_plan_id"),
        "contract_action_plan_used": selection_guard.get("contract_action_plan_used"),
        "action_algorithm_used": selection_guard.get("action_algorithm_used"),
        "action_inputs_source": selection_guard.get("action_inputs_source"),
        "action_outputs_source": selection_guard.get("action_outputs_source"),
        "fallback_used": selection_guard.get("fallback_used"),
        "fallback_name": selection_guard.get("fallback_name"),
        "fallback_allowed_by_clause": selection_guard.get("fallback_allowed_by_clause"),
        "forbidden_action_used": selection_guard.get("forbidden_action_used"),
        "runtime_bypassed_action_plan": selection_guard.get("runtime_bypassed_action_plan"),
        "target_score": target_score.score,
        "reference_scores": reference_scores_for_trace,
        "boundary_confirmed": bool(selection.get("boundary_confirmed")),
        "continue_required": continue_required_by_v3,
        "next_reference_index": next_reference_index_by_v33 if continue_required_by_v3 else None,
        "final_reference_candidate_index": selection.get("final_reference_candidate_index"),
        "final_reference_candidate_status": selection.get("final_reference_candidate_status"),
        "final_reference_recollect_required": recollect_required_by_v33,
        "recollect_reference_index": selection.get("recollect_reference_index"),
        "recollect_reason": selection.get("recollect_reason"),
        "remaining_reference_count": remaining_reference_count,
        "early_exit_rule_id": early_exit_decision.get("early_exit_rule_id"),
        "early_exit_rule_clause_id": early_exit_decision.get("early_exit_rule_clause_id") or "",
        "early_exit_allowed": bool(early_exit_decision.get("early_exit_allowed")),
        "reference_early_exit": bool(early_exit_decision.get("reference_early_exit")),
        "early_exit_decision": early_exit_decision.get("early_exit_decision"),
        "early_exit_reason": early_exit_decision.get("early_exit_reason"),
        "early_exit_blockers": early_exit_decision.get("early_exit_blockers"),
        "partial_confirmed_score": early_exit_decision.get("partial_confirmed_score"),
        "remaining_max_possible_score": early_exit_decision.get("remaining_max_possible_score"),
        "max_possible_reference_score": early_exit_decision.get("max_possible_reference_score"),
        "score_upper_bound_components": early_exit_decision.get("score_upper_bound_components"),
    }
    selection.update(v3_reference_selection_trace)
    selection["contract_execution_guard"] = selection_guard
    context["reference_selection_contract_guard"] = selection_guard
    context["s15_reference_history_write_gate"] = scoring_reference_history_gate
    context["current_reference"]["v3_reference_selection_trace"] = v3_reference_selection_trace
    if selection.get("issue_code") == V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW:
        context["v33_recollect_terminal_result_override"] = _v33_recollect_needs_review_result(
            context,
            trace=selection.get("v33_recollect_terminal_trace") or {},
            continue_history=scoring_reference_history,
            continue_history_gate=scoring_reference_history_gate,
        )
    if not reference_valid_for_boundary:
        context["current_reference"]["excluded_reason"] = (
            current_reference.get("excluded_from_boundary_reason") or "reference_required_field_missing_or_hard_reject"
        )
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
        return "S16", {}
    if selection.get("manual_review_required"):
        return "S16", {}
    return "S10", {}


def _reference_car_from_history_item(reference: dict[str, Any]) -> ReferenceCar | None:
    score_input = reference.get("reference_score_input")
    source = score_input if isinstance(score_input, dict) else reference
    try:
        return ReferenceCar(
            reference_index=int(source.get("reference_index") or reference.get("reference_index") or 0),
            list_price_10k=float(source.get("list_price_10k") or reference.get("list_price_10k") or 0.0),
            list_year=int(source.get("list_year") or reference.get("list_year") or 0),
            list_mileage_10k_km=float(source.get("list_mileage_10k_km") or reference.get("list_mileage_10k_km") or 0.0),
            transfer_count=int(source.get("transfer_count") or reference.get("transfer_count") or 0),
            accident_count=int(source.get("accident_count") if source.get("accident_count") is not None else reference.get("accident_count") or reference.get("claim_count") or 0),
            max_accident_amount=source.get("max_accident_amount") if source.get("max_accident_amount") is not None else reference.get("max_accident_amount") if reference.get("max_accident_amount") is not None else reference.get("max_claim_amount"),
            repair_counts=dict(source.get("repair_counts") or reference.get("repair_counts") or {}),
            panel_repairs=[
                DamageRecord(item.get("part"), item.get("damage_type") or item.get("normalized_damage"))
                for item in (source.get("panel_repairs") or reference.get("panel_repairs") or reference.get("repair_items") or [])
                if isinstance(item, dict) and item.get("part") and (item.get("damage_type") or item.get("normalized_damage"))
            ],
            score=float(reference.get("reference_score")) if reference.get("reference_score") is not None else None,
        )
    except Exception:
        return None


def _v3_low_candidates_from_history(
    references: list[dict[str, Any]],
    target_score: dict[str, Any] | ScoreResult | None,
) -> list[tuple[dict[str, Any], ReferenceCar, ScoreResult]]:
    target_value = target_score.score if isinstance(target_score, ScoreResult) else (target_score or {}).get("score") if isinstance(target_score, dict) else None
    if target_value is None:
        return []
    candidates: list[tuple[dict[str, Any], ReferenceCar, ScoreResult]] = []
    for item in references:
        if not isinstance(item, dict):
            continue
        score_value = item.get("reference_score")
        if score_value is None or not _reference_score_usable_for_boundary(item):
            continue
        if item.get("hard_eliminated") or item.get("hard_reject") or item.get("reference_disqualified"):
            continue
        ref_car = _reference_car_from_history_item(item)
        if ref_car is None:
            continue
        score = ScoreResult(
            score=float(score_value),
            components=dict(item.get("reference_score_components") or {}),
            review_reasons=list(item.get("reference_score_review_reasons") or []),
            hard_reject=False,
        )
        if _score_result_has_reference_missing_required_field(score):
            continue
        if score.score < float(target_value):
            entry = {
                "reference_index": ref_car.reference_index,
                "reference_score": score.score,
                "score_gap_to_target": round(float(target_value) - score.score, 2),
                "price_yuan": round(ref_car.list_price_10k * 10000),
                "list_price_10k": ref_car.list_price_10k,
            }
            candidates.append((entry, ref_car, score))
    return sorted(candidates, key=lambda entry: entry[1].reference_index)


def _select_v3_reference_from_history(
    references: list[dict[str, Any]],
    target_score: dict[str, Any] | ScoreResult | None,
) -> dict[str, Any]:
    target_value = target_score.score if isinstance(target_score, ScoreResult) else (target_score or {}).get("score") if isinstance(target_score, dict) else None
    if target_value is None:
        return {"selected_reference": None, "selected_score": None, "candidate_reference_pool": [], "reason": "target_score_missing"}
    low_candidates: list[tuple[dict[str, Any], ReferenceCar, ScoreResult]] = []
    valid_seen_count = 0
    untrusted_incomplete_seen = False
    for item in references:
        if not isinstance(item, dict):
            continue
        score_value = item.get("reference_score")
        if score_value is None or not _reference_score_usable_for_boundary(item):
            if (
                item.get("excluded_from_boundary_reason") in {"UNTRUSTED_REFERENCE_SCORE", "LOW_SCORE_SKIPPED_INCOMPLETE"}
                or item.get("reference_score_preliminary") is True
                or item.get("low_score_skipped_incomplete") is True
            ):
                untrusted_incomplete_seen = True
            continue
        if item.get("hard_eliminated") or item.get("hard_reject") or item.get("reference_disqualified"):
            continue
        ref_car = _reference_car_from_history_item(item)
        if ref_car is None:
            continue
        score = ScoreResult(
            score=float(score_value),
            components=dict(item.get("reference_score_components") or {}),
            review_reasons=list(item.get("reference_score_review_reasons") or []),
            hard_reject=False,
        )
        if _score_result_has_reference_missing_required_field(score):
            continue
        valid_seen_count += 1
        entry = {
            "reference_index": ref_car.reference_index,
            "reference_score": score.score,
            "score_gap_to_target": round(float(target_value) - score.score, 2),
            "price_yuan": round(ref_car.list_price_10k * 10000),
            "list_price_10k": ref_car.list_price_10k,
        }
        if score.score >= float(target_value):
            final_candidate_index = int(ref_car.reference_index) - 1
            previous_item = _history_item_by_reference_index(references, final_candidate_index)
            previous_low = _find_v33_complete_low_candidate(low_candidates, final_candidate_index)
            if final_candidate_index < 1:
                return {
                    "selected_reference": None,
                    "selected_score": None,
                    "review_reasons": ["FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE"],
                    "manual_review_required": True,
                    "auto_pricing_allowed": False,
                    "reference_selection_rule": REFERENCE_SELECTION_RULE,
                    "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
                    "boundary_confirmed": True,
                    "boundary_reference_index": ref_car.reference_index,
                    "boundary_reference_score": score.score,
                    "boundary_reference_price_yuan": round(ref_car.list_price_10k * 10000),
                    "pre_boundary_reference_index": None,
                    "final_reference_candidate_index": None,
                    "final_reference_candidate_status": "MISSING_PREVIOUS_REFERENCE",
                    "final_reference_index": None,
                    "final_reference_score": None,
                    "final_reference_price": None,
                    "final_reference_price_yuan": None,
                    "final_reference_selection_reason": "first_boundary_has_no_previous_reference",
                    "candidate_reference_pool": [entry],
                    "manual_review_reason": "FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE",
                }
            if previous_low is None:
                previous_status = _v33_previous_candidate_status(previous_item)
                return {
                    "selected_reference": None,
                    "selected_score": None,
                    "review_reasons": ["BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT_REQUIRED"],
                    "manual_review_required": previous_item is None,
                    "auto_pricing_allowed": False,
                    "reference_selection_rule": REFERENCE_SELECTION_RULE,
                    "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
                    "boundary_confirmed": True,
                    "boundary_reference_index": ref_car.reference_index,
                    "boundary_reference_score": score.score,
                    "boundary_reference_price_yuan": round(ref_car.list_price_10k * 10000),
                    "pre_boundary_reference_index": final_candidate_index,
                    "final_reference_candidate_index": final_candidate_index,
                    "final_reference_candidate_status": previous_status,
                    "final_reference_recollect_required": previous_item is not None,
                    "final_reference_recollect_done": False,
                    "recollect_reference_index": final_candidate_index if previous_item is not None else None,
                    "recollect_reason": "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE_OR_SKIPPED",
                    "final_reference_index": None,
                    "final_reference_score": None,
                    "final_reference_price": None,
                    "final_reference_price_yuan": None,
                    "final_reference_selection_reason": "boundary_previous_reference_requires_recollect"
                    if previous_item is not None
                    else "boundary_previous_reference_missing_manual_review",
                    "candidate_reference_pool": [low[0] for low in low_candidates] + [entry],
                    "manual_review_reason": None if previous_item is not None else "BOUNDARY_PREVIOUS_REFERENCE_NOT_FOUND",
                }
            selected_entry, selected_ref, selected_score = previous_low
            selected_ref.is_final_reference = True
            final_price = round(selected_ref.list_price_10k * 10000)
            return {
                "selected_reference": selected_ref,
                "selected_score": selected_score,
                "review_reasons": list(selected_score.review_reasons),
                "manual_review_required": False,
                "auto_pricing_allowed": True,
                "reference_selection_rule": REFERENCE_SELECTION_RULE,
                "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
                "boundary_confirmed": True,
                "boundary_reference_index": ref_car.reference_index,
                "boundary_reference_score": score.score,
                "boundary_reference_price_yuan": round(ref_car.list_price_10k * 10000),
                "pre_boundary_reference_index": selected_ref.reference_index,
                "final_reference_candidate_index": final_candidate_index,
                "final_reference_candidate_status": "COMPLETE_TRUSTWORTHY_LOW_SCORE",
                "final_reference_recollect_required": False,
                "final_reference_recollect_done": False,
                "final_reference_index": selected_ref.reference_index,
                "final_reference_score": selected_score.score,
                "final_reference_price": final_price,
                "final_reference_price_yuan": final_price,
                "final_reference_selection_reason": "boundary_previous_reference_complete_trustworthy",
                "candidate_reference_pool": [low[0] for low in low_candidates] + [entry],
                "manual_review_reason": None,
            }
        low_candidates.append((entry, ref_car, score))
    if low_candidates:
        return {
            "selected_reference": None,
            "selected_score": None,
            "review_reasons": ["NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING"],
            "manual_review_required": True,
            "auto_pricing_allowed": False,
            "reference_selection_rule": REFERENCE_SELECTION_RULE,
            "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
            "boundary_confirmed": False,
            "boundary_reference_index": None,
            "boundary_reference_score": None,
            "pre_boundary_reference_index": None,
            "final_reference_candidate_index": None,
            "final_reference_candidate_status": "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING",
            "final_reference_index": None,
            "final_reference_score": None,
            "final_reference_price": None,
            "final_reference_price_yuan": None,
            "final_reference_selection_reason": "no_boundary_reference_found_manual_review_no_auto_pricing",
            "candidate_reference_pool": [entry[0] for entry in low_candidates],
            "manual_review_reason": "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING",
        }
    no_valid_reason = "REFERENCE_SCORE_UNTRUSTED_INCOMPLETE_COLLECTION" if untrusted_incomplete_seen else "NO_VALID_REFERENCE"
    return {
        "selected_reference": None,
        "selected_score": None,
        "review_reasons": [no_valid_reason],
        "manual_review_required": True,
        "auto_pricing_allowed": False,
        "reference_selection_rule": REFERENCE_SELECTION_RULE,
        "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
        "boundary_confirmed": False,
        "boundary_reference_index": None,
        "boundary_reference_score": None,
        "pre_boundary_reference_index": None,
        "final_reference_index": None,
        "final_reference_score": None,
        "final_reference_price": None,
        "candidate_reference_pool": [],
        "manual_review_reason": no_valid_reason if not valid_seen_count else "NO_VALID_REFERENCE",
    }


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
    rule_guard = _apply_pricing_rule_source_guard(context, "S16")
    selection = context["selection"]
    complete_reference_history, reference_history_gate = _safe_reference_history_with_current_reference(
        context,
        purpose="s16_pricing_context",
        require_identity=True,
        require_physical_evidence=True,
    )
    _raise_if_reference_history_write_blocked(
        context,
        reference_history_gate,
        page="S16",
        message="S16 pricing cannot use a reference without physical UI transition proof.",
    )
    selected_reference = selection.get("selected_reference")
    selected_score = selection.get("selected_score")
    target_score = context.get("target_score") or {}
    task_result = context.get("target_task_result") or {}
    task_data = task_result.get("task") or {}
    task_params = task_result.get("app_operation_params") or {}
    first_stage_evidence = context.get("first_stage_evidence") or {}
    trisame_cards = (
        context.get("canonical_reference_order")
        or current_reference.get("canonical_reference_order")
        or first_stage_evidence.get("same_source_cards")
        or []
    )
    pricing_context = {
        "target": context.get("target_car"),
        "target_score": target_score.get("score") if isinstance(target_score, dict) else None,
        "selected_reference_score": selected_score.score if selected_score else None,
        "trisame_cards": trisame_cards,
        "trisame_count": first_stage_evidence.get("trisame_count") or first_stage_evidence.get("trisame_cards_count"),
        "reference_history": complete_reference_history,
        "condition_text": task_data.get("condition_text") or task_params.get("condition_text") or "",
        "inspection_note": task_data.get("inspection_note") or task_params.get("inspection_note") or "",
        "license_city": task_data.get("license_city") or task_data.get("plate_location") or task_params.get("license_city") or "",
        "selected_card_title": current_reference.get("selected_card_title") or current_reference.get("list_title") or "",
        "selected_card_metadata": current_reference.get("selected_card_metadata") or "",
        "selected_reference_city": current_reference.get("selected_card_city") or "",
        "pricing_rule_source_guard": rule_guard,
    }
    pricing = calculate_pricing(selection.get("selected_reference"), fields_config, pricing_context=pricing_context)
    manual_review_reasons = []
    if isinstance(target_score, dict):
        manual_review_reasons.extend(target_score.get("review_reasons", []) or [])
    manual_review_reasons.extend(selection.get("review_reasons", []) or [])
    manual_review_reasons.extend(current_reference.get("manual_review_reasons", []) or [])
    manual_review_reasons.extend(pricing.get("manual_review_reasons", []) or [])
    manual_review_reasons = list(dict.fromkeys(manual_review_reasons))
    manual_review_required = bool(
        current_reference.get("manual_review_required")
        or selection.get("manual_review_required")
        or pricing.get("manual_review_required")
        or pricing.get("status") == "manual_review"
    )
    suggested_acquisition = pricing.get("suggested_acquisition_price_yuan")
    suggested_listing = pricing.get("guazi_price_yuan")
    final_purchase_price = None if manual_review_required else suggested_acquisition
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
        "manual_review_required": manual_review_required,
        "manual_review_reasons": manual_review_reasons,
        "notes": pricing.get("notes", []) or [],
        "competition_coefficient": pricing.get("competition_coefficient"),
        "competition_coefficient_reasons": pricing.get("competition_coefficient_reasons"),
        "base_reference_price_yuan": pricing.get("base_reference_price_yuan"),
        "reference_selection_rule": selection.get("reference_selection_rule"),
        "reference_selection_rule_version": selection.get("reference_selection_rule_version"),
        "boundary_confirmed": selection.get("boundary_confirmed"),
        "boundary_reference_index": selection.get("boundary_reference_index"),
        "boundary_reference_score": selection.get("boundary_reference_score"),
        "boundary_reference_price_yuan": selection.get("boundary_reference_price_yuan"),
        "pre_boundary_reference_index": selection.get("pre_boundary_reference_index"),
        "final_reference_candidate_index": selection.get("final_reference_candidate_index"),
        "final_reference_candidate_status": selection.get("final_reference_candidate_status"),
        "final_reference_recollect_required": selection.get("final_reference_recollect_required"),
        "final_reference_recollect_done": selection.get("final_reference_recollect_done"),
        "recollect_reference_index": selection.get("recollect_reference_index"),
        "recollect_reason": selection.get("recollect_reason"),
        "target_guazi_listing_price_yuan": pricing.get("target_guazi_listing_price_yuan"),
        "guazi_price_yuan": pricing.get("guazi_price_yuan"),
        "guazi_service_fee_yuan": pricing.get("guazi_service_fee_yuan"),
        "guazi_net_payout_yuan": pricing.get("guazi_net_payout_yuan"),
        "guazi_return_price_yuan": pricing.get("guazi_return_price_yuan"),
        "profit_rate": pricing.get("profit_rate"),
        "evidence": {
            "s10_screenshot_path": context.get("current_reference", {}).get("s10_screenshot_path"),
            "s10_xml_path": context.get("current_reference", {}).get("s10_xml_path"),
        },
    }
    pricing_contract_guard = guard_pricing_rule(
        {
            "pricing_rule_version": PRICING_RULE_VERSION,
            "competition_coefficient_version": COMPETITION_COEFFICIENT_VERSION,
            "manual_review_required": manual_review_required,
            "pricing_decision_source": "MANUAL_REVIEW_PENDING" if manual_review_required else "AUTOMATIC_PRICING",
            "suggested_purchase_price_yuan": suggested_acquisition,
            "final_purchase_price_yuan": final_purchase_price,
            "pricing": pricing,
            "s17_payload": s17_payload,
        },
        active_pricing_rule_version=PRICING_RULE_VERSION,
        service_fee_rule_version=PRICING_RULE_VERSION,
        competition_coefficient_version=COMPETITION_COEFFICIENT_VERSION,
    )
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
        "pricing_contract_guard": pricing_contract_guard,
        "pricing_rule_source_guard": rule_guard,
        "reference_selection_rule": selection.get("reference_selection_rule"),
        "reference_selection_rule_version": selection.get("reference_selection_rule_version"),
        "boundary_confirmed": selection.get("boundary_confirmed"),
        "boundary_reference_index": selection.get("boundary_reference_index"),
        "boundary_reference_score": selection.get("boundary_reference_score"),
        "boundary_reference_price_yuan": selection.get("boundary_reference_price_yuan"),
        "pre_boundary_reference_index": selection.get("pre_boundary_reference_index"),
        "final_reference_candidate_index": selection.get("final_reference_candidate_index"),
        "final_reference_candidate_status": selection.get("final_reference_candidate_status"),
        "final_reference_recollect_required": selection.get("final_reference_recollect_required"),
        "final_reference_recollect_done": selection.get("final_reference_recollect_done"),
        "recollect_reference_index": selection.get("recollect_reference_index"),
        "recollect_reason": selection.get("recollect_reason"),
        "candidate_reference_pool": selection.get("candidate_reference_pool"),
        "final_reference_selection_reason": selection.get("final_reference_selection_reason"),
        "suggested_purchase_price_yuan": suggested_acquisition,
        "system_suggested_price_yuan": suggested_acquisition,
        "final_purchase_price_yuan": final_purchase_price,
        "final_price_source": None if manual_review_required else "SYSTEM_AUTOMATIC_PRICING",
        "pricing_decision_source": "MANUAL_REVIEW_PENDING" if manual_review_required else "AUTOMATIC_PRICING",
        "final_purchase_price_required": not manual_review_required,
        "profit_rate": pricing.get("profit_rate"),
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
        "reference_history": complete_reference_history,
        "reference_history_write_gate": reference_history_gate,
        "current_reference_index": context.get("current_reference_index"),
        "invalid_partial_reference_detected": context.get("invalid_partial_reference_detected"),
        "invalid_partial_reference_index": context.get("invalid_partial_reference_index"),
        "invalid_partial_reference_reason": context.get("invalid_partial_reference_reason"),
        "continuation_recovered_next_reference_index": context.get("continuation_recovered_next_reference_index"),
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
    continuation_plan = _load_reference_continuation_plan(task_result, first_stage_evidence=first_stage_evidence)
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
        "target_task_result": task_result,
        "current_reference_index": int(continuation_plan.get("next_reference_index") or 1),
        "current_reference": {},
        "continuation_mode": bool(continuation_plan.get("continuation_mode")),
        "previous_status": continuation_plan.get("previous_status"),
        "previous_reference_index": continuation_plan.get("previous_reference_index"),
        "next_reference_index": int(continuation_plan.get("next_reference_index") or 1),
        "reference_history": list(continuation_plan.get("reference_history") or []),
        "continuation_plan": continuation_plan,
        "invalid_partial_reference_detected": continuation_plan.get("invalid_partial_reference_detected"),
        "invalid_partial_reference_index": continuation_plan.get("invalid_partial_reference_index"),
        "invalid_partial_reference_reason": continuation_plan.get("invalid_reason"),
        "continuation_recovered_next_reference_index": continuation_plan.get("continuation_recovered_next_reference_index"),
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

    if continuation_plan.get("continuation_source_missing_blocked_default_to_one"):
        stop_code = str(continuation_plan.get("continuation_source_missing_stop_code") or SECOND_STAGE_CONTINUATION_SOURCE_MISSING)
        issue = issues.record(
            stop_code,
            "S10",
            "Second stage continuation was expected, but no verified continuation source was available; refusing to reset to reference_index=1.",
            {
                "continuation_plan": continuation_plan,
                "first_stage_evidence": first_stage_evidence,
                "reference_loop_state_reset_detected": True,
                "reference_loop_state_reset_code": REFERENCE_LOOP_STATE_RESET_DETECTED,
            },
            "manual_review",
        )
        timing.write()
        result = {
            "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
            "status": issue["code"],
            "issue_code": issue["code"],
            "stop_code": issue["code"],
            "current_reference_index": None,
            "next_reference_index": None,
            "continuation_mode": False,
            "continuation_plan": continuation_plan,
            "reference_loop_state_reset_detected": True,
            "reference_loop_state_reset_code": REFERENCE_LOOP_STATE_RESET_DETECTED,
            "default_to_reference_one_blocked": True,
            "first_stage_evidence": first_stage_evidence,
            "phone_test": phone_test or {},
        }
        _write_second_stage_result(configs, result, task_result)
        return result

    snapshot = _capture_with_global_popup_guard(context, "s10_s16_start", current_stage="S10")
    snapshot["target_brand"] = target_car.brand
    snapshot["target_car"] = {
        "brand": target_car.brand,
        "series": target_car.series,
        "model_year": target_car.model_year,
        "trim": target_car.trim,
    }
    state = _recognize_mainline_page(recognizer, snapshot)
    startup_routing = _second_stage_start_page_routing_evidence(
        recognized_state=state,
        context=context,
        first_stage_evidence=first_stage_evidence,
    )
    context["second_stage_start_page_routing"] = startup_routing
    if _second_stage_in_flight_continuation_reset_blocked(state, continuation_plan):
        issue = issues.record(
            "SECOND_STAGE_IN_FLIGHT_CONTINUATION_REJECTED_CONTEXT_NOT_RESTORABLE",
            state or "UNKNOWN",
            "Second stage is already inside a reference page, but prior continuation state was rejected; refusing to reset to reference_index=1 in-flight.",
            {
                **snapshot,
                "recognized_state": state,
                "continuation_plan": continuation_plan,
                "second_stage_start_page_routing": startup_routing,
            },
            "manual_review",
        )
        timing.write()
        result = {
            "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
            "status": issue["code"],
            "issue_code": issue["code"],
            "recognized_state": state,
            "current_reference_index": context.get("current_reference_index"),
            "current_reference": context.get("current_reference"),
            "reference_history": list(context.get("reference_history") or []),
            "continuation_mode": bool(context.get("continuation_mode")),
            "continuation_plan": continuation_plan,
            "second_stage_start_page_routing": startup_routing,
            "s13_cross_run_loop_guard_triggered": state == "S13",
            "s13_cross_run_loop_guard_reason": "continuation_rejected_but_page_still_in_flight",
            "screenshot_path": str(snapshot.get("screenshot_path") or ""),
            "xml_path": str(snapshot.get("xml_path") or ""),
            "visible_text_digest": list(snapshot.get("visible_texts", []))[:40],
            "phone_test": phone_test or {},
        }
        _write_second_stage_result(configs, result, task_result)
        return result
    if state == "S10":
        startup_expected_card = _expected_reference_card_with_continuation_context(
            first_stage_evidence,
            int(context.get("current_reference_index") or 1),
            continuation_plan,
        )
        fast_handoff_started = time.perf_counter()
        startup_fast_handoff = _second_stage_s10_fast_handoff_gate(
            first_stage_evidence,
            snapshot,
            int(context.get("current_reference_index") or 1),
            startup_expected_card,
        )
        snapshot, startup_fast_handoff = _s10_handoff_autoscroll_selected_card_if_needed(
            context,
            snapshot,
            first_stage_evidence,
            int(context.get("current_reference_index") or 1),
            startup_expected_card,
            startup_fast_handoff,
        )
        startup_s10_evidence = startup_fast_handoff.get("s10_reliable_list_evidence") or {}
        startup_fast_handoff["second_stage_start_to_s10_fast_gate_ms"] = int((time.perf_counter() - fast_handoff_started) * 1000)
        context["startup_s10_reliable_evidence"] = startup_s10_evidence
        context["second_stage_s10_fast_handoff"] = startup_fast_handoff
        if startup_fast_handoff.get("second_stage_fast_handoff_passed") is not True:
            issue = issues.record(
                "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED",
                "S10",
                "Second stage S10 fast handoff gate failed.",
                {
                    **snapshot,
                    "target_reference_index": int(context.get("current_reference_index") or 1),
                    "expected_card": startup_expected_card,
                    "s10_fast_handoff_gate": startup_fast_handoff,
                },
                "manual_review",
            )
            timing.write()
            result = {
                "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
                "status": "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED",
                "issue_code": issue["code"],
                "recognized_state": state,
                "first_stage_evidence": first_stage_evidence,
                "s10_fast_handoff_gate": startup_fast_handoff,
                "second_stage_start_page_routing": startup_routing,
                "screenshot_path": str(snapshot.get("screenshot_path") or ""),
                "xml_path": str(snapshot.get("xml_path") or ""),
                "visible_text_digest": list(snapshot.get("visible_texts", []))[:40],
                "phone_test": phone_test or {},
            }
            _write_second_stage_result(configs, result, task_result)
            return result
    elif startup_routing.get("in_flight_page_allowed") is True:
        identity_hydration = _hydrate_current_reference_identity_for_in_flight_context(
            context,
            reason=f"second_stage_in_flight_start:{state or 'UNKNOWN'}",
        )
        startup_routing["reference_identity_hydration"] = identity_hydration
        if identity_hydration.get("identity_hydration_ok") is not True:
            issue_code = "SECOND_STAGE_IN_FLIGHT_REFERENCE_CONTEXT_NOT_RESTORABLE"
            issue = issues.record(
                issue_code,
                state or "UNKNOWN",
                "Second stage in-flight page is recognized, but current reference identity cannot be restored from first-stage S10 evidence.",
                {
                    **snapshot,
                    "recognized_state": state,
                    "identity_hydration": identity_hydration,
                    "second_stage_start_page_routing": startup_routing,
                    "continuation_plan": continuation_plan,
                },
                "manual_review",
            )
            timing.write()
            result = {
                "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
                "status": issue["code"],
                "issue_code": issue["code"],
                "recognized_state": state,
                "current_reference_index": context.get("current_reference_index"),
                "current_reference": context.get("current_reference"),
                "reference_identity_hydration": identity_hydration,
                "reference_history": list(context.get("reference_history") or []),
                "continuation_mode": bool(context.get("continuation_mode")),
                "continuation_plan": continuation_plan,
                "second_stage_start_page_routing": startup_routing,
                "screenshot_path": str(snapshot.get("screenshot_path") or ""),
                "xml_path": str(snapshot.get("xml_path") or ""),
                "visible_text_digest": list(snapshot.get("visible_texts", []))[:40],
                "phone_test": phone_test or {},
            }
            _write_second_stage_result(configs, result, task_result)
            return result
        timing.add(
            step_name="SECOND_STAGE_IN_FLIGHT_START_ROUTED",
            page_name=str(state or "UNKNOWN"),
            action_name=str(startup_routing.get("selected_executor_name") or ""),
            contract_check_ms=0,
            field_read_ms=0,
            action_ms=0,
            transition_wait_ms=0,
            screenshot_path=str(snapshot.get("screenshot_path") or ""),
            xml_path=str(snapshot.get("xml_path") or ""),
            extra=startup_routing,
        )
    else:
        issue_code = str(startup_routing.get("contract_stop_code") or "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY")
        issue = issues.record(
            issue_code,
            state or "UNKNOWN",
            "Second stage startup page is not reliable S10 and no legal in-flight page context is restorable.",
            {**snapshot, "second_stage_start_page_routing": startup_routing},
            "manual_review",
        )
        timing.write()
        result = {
            "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
            "status": "SECOND_STAGE_CONTEXT_NOT_RESTORABLE" if issue_code != "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY" else "SECOND_STAGE_BLOCKED_NOT_AT_S10_READY",
            "issue_code": issue["code"],
            "recognized_state": state,
            "expected_stage": "second_stage",
            "first_stage_evidence": first_stage_evidence,
            "second_stage_start_page_routing": startup_routing,
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
                current_reference_before_s11 = context.get("current_reference") if isinstance(context.get("current_reference"), dict) else {}
                state, snapshot = handle_s11(context, snapshot)
                if not current_reference_before_s11.get("s11_contract_execution_ack"):
                    stop = _s11_contract_ack_stop_context(context, snapshot)
                    issue = issues.record(
                        stop["code"],
                        "S11",
                        stop["message"],
                        stop["context"],
                        "manual_review",
                    )
                    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
                continue
            if state == "S12":
                state, snapshot = handle_s12(context, snapshot)
                continue
            if state == "S13":
                identity_hydration = _hydrate_current_reference_identity_for_in_flight_context(
                    context,
                    reason="s13_handler_reentry",
                )
                if identity_hydration.get("identity_hydration_ok") is not True:
                    issue = issues.record(
                        "S13_REENTRY_BLOCKED_BY_MISSING_REFERENCE_IDENTITY",
                        "S13",
                        "S13 handler reentry requires a restorable current_reference identity before collecting region counts.",
                        {
                            "identity_hydration": identity_hydration,
                            "current_reference_index": context.get("current_reference_index"),
                            "current_reference": context.get("current_reference"),
                            "second_stage_start_page_routing": context.get("second_stage_start_page_routing"),
                        },
                        "manual_review",
                    )
                    raise GuaziFlowError(issue["code"], issue["message"], issue["context"])
                state, snapshot = handle_s13(context, snapshot)
                continue
            if state == "S14":
                state, snapshot = handle_s14(context, snapshot)
                continue
            if state == "S15":
                state, snapshot = handle_s15(context)
                v33_recollect_terminal_result = context.pop("v33_recollect_terminal_result_override", None)
                if isinstance(v33_recollect_terminal_result, dict):
                    timing.write()
                    _write_second_stage_result(configs, v33_recollect_terminal_result, task_result)
                    return v33_recollect_terminal_result
                if state == "S10":
                    issue = issues.record("CONTINUE_NEXT_REFERENCE", "S15", "Current reference has not closed the V3 boundary; continue from S10.", context["current_reference"], "continue")
                    selection_for_continue = context.get("selection") or {}
                    recollect_reference_index = _safe_int(
                        context.get("v33_recollect_next_reference_index")
                        or selection_for_continue.get("recollect_reference_index"),
                        default=0,
                    )
                    next_reference_index = (
                        recollect_reference_index
                        if recollect_reference_index > 0
                        else int(context.get("current_reference_index") or 0) + 1
                    )
                    trisame_count = _first_stage_trisame_count(context.get("first_stage_evidence") or {})
                    current_reference = context.get("current_reference") or {}
                    current_exclusion_reason = str(current_reference.get("excluded_from_boundary_reason") or "")
                    current_early_exit = bool(current_reference.get("reference_early_exit"))
                    current_untrusted = current_exclusion_reason in {
                        "UNTRUSTED_REFERENCE_SCORE",
                        S14_COLLECTION_INCOMPLETE_UNRECOVERABLE,
                    }
                    continue_reason = (
                        "BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
                        if recollect_reference_index > 0
                        else
                        "EARLY_EXIT_CONTINUE_NEXT_REFERENCE"
                        if current_early_exit
                        else
                        "CURRENT_REFERENCE_S14_COLLECTION_INCOMPLETE_UNRECOVERABLE"
                        if current_exclusion_reason == S14_COLLECTION_INCOMPLETE_UNRECOVERABLE
                        else "CURRENT_REFERENCE_SCORE_UNTRUSTED_OR_INCOMPLETE_S14"
                        if current_untrusted
                        else "CURRENT_REFERENCE_HAS_NOT_CLOSED_V3_BOUNDARY"
                    )
                    continue_history, continue_history_gate = _safe_reference_history_with_current_reference(
                        context,
                        purpose="continue_next_reference_result",
                        require_identity=True,
                        require_physical_evidence=True,
                    )
                    _raise_if_reference_history_write_blocked(
                        context,
                        continue_history_gate,
                        page="S15",
                        message="CONTINUE_NEXT_REFERENCE cannot be emitted until current reference has physical UI transition proof.",
                    )
                    if trisame_count is not None and next_reference_index > trisame_count:
                        exhausted_physical_gate = _all_references_exhausted_physical_gate(
                            continue_history,
                            trisame_count=trisame_count,
                            next_reference_index=next_reference_index,
                        )
                        if exhausted_physical_gate.get("physical_evidence_ok") is not True:
                            exhausted_issue = issues.record(
                                ALL_REFERENCES_EXHAUSTED_BLOCKED_BY_MISSING_PHYSICAL_EVIDENCE,
                                "S15",
                                "All references exhausted cannot be declared from logical indices without physical UI transition proof for every reference.",
                                {
                                    "current_reference": context.get("current_reference"),
                                    "reference_history": continue_history,
                                    "reference_history_write_gate": continue_history_gate,
                                    "all_references_exhausted_physical_gate": exhausted_physical_gate,
                                },
                                "manual_review",
                            )
                            result = {
                                "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
                                "status": exhausted_issue["code"],
                                "issue_code": exhausted_issue["code"],
                                "issue_context": exhausted_issue["context"],
                                "current_reference": context.get("current_reference"),
                                "reference_history": continue_history,
                                "reference_history_write_gate": continue_history_gate,
                                "all_references_exhausted_physical_gate": exhausted_physical_gate,
                                "current_reference_index": context.get("current_reference_index"),
                                "next_reference_index": next_reference_index,
                                "trisame_count": trisame_count,
                            }
                            timing.write()
                            _write_second_stage_result(configs, result, task_result)
                            return result
                        complete_history = continue_history
                        v3_selection = _select_v3_reference_from_history(complete_history, context.get("target_score"))
                        if v3_selection.get("selected_reference") is not None and v3_selection.get("selected_score") is not None:
                            context["selection"] = v3_selection
                            context["reference_history"] = complete_history[:-1]
                            context["s15_score_compare_done"] = True
                            result = handle_s16(context)
                            result["reference_selection_rule"] = REFERENCE_SELECTION_RULE
                            result["reference_selection_rule_version"] = REFERENCE_SELECTION_RULE
                            result["candidate_reference_pool"] = v3_selection.get("candidate_reference_pool")
                            result["boundary_confirmed"] = v3_selection.get("boundary_confirmed")
                            result["boundary_reference_index"] = v3_selection.get("boundary_reference_index")
                            result["boundary_reference_score"] = v3_selection.get("boundary_reference_score")
                            result["pre_boundary_reference_index"] = v3_selection.get("pre_boundary_reference_index")
                            result["final_reference_selection_reason"] = v3_selection.get("final_reference_selection_reason")
                            timing.write()
                            _write_second_stage_result(configs, result, task_result)
                            return result
                        exhausted_issue = issues.record(
                            "ALL_REFERENCES_EXHAUSTED_MANUAL_REVIEW",
                            "S15",
                            "No V3 boundary reference or usable final reference was found after all confirmed same-source references were processed.",
                            {
                                "current_reference": context.get("current_reference"),
                                "target_score": context.get("target_score"),
                                "trisame_count": trisame_count,
                                "collected_reference_count": len(complete_history),
                                "valid_reference_count": len(complete_history),
                                "reference_selection_rule": REFERENCE_SELECTION_RULE,
                                "reference_selection_rule_version": REFERENCE_SELECTION_RULE,
                                "candidate_reference_pool": v3_selection.get("candidate_reference_pool"),
                                "boundary_confirmed": v3_selection.get("boundary_confirmed"),
                                "boundary_reference_index": v3_selection.get("boundary_reference_index"),
                                "boundary_reference_score": v3_selection.get("boundary_reference_score"),
                                "all_trisame_sources_exhausted": True,
                                "reason": v3_selection.get("manual_review_reason") or "NO_VALID_REFERENCE",
                                "next_reference_index": next_reference_index,
                                "first_stage_evidence": context.get("first_stage_evidence") or {},
                                "reference_history_write_gate": continue_history_gate,
                                "all_references_exhausted_physical_gate": exhausted_physical_gate,
                            },
                            "manual_review",
                        )
                        result = {
                            "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
                            "status": exhausted_issue["code"],
                            "issue_code": exhausted_issue["code"],
                            "issue_context": exhausted_issue["context"],
                            "current_reference": context.get("current_reference"),
                            "returned_list_source": context.get("returned_list_source"),
                            "returned_list_source_verified": context.get("returned_list_source_verified"),
                            "target_score": context.get("target_score"),
                            "selection": context.get("selection"),
                            "reference_history": complete_history,
                            "reference_history_write_gate": continue_history_gate,
                            "all_references_exhausted_physical_gate": exhausted_physical_gate,
                            "previous_reference_index": context.get("previous_reference_index"),
                            "current_reference_index": context.get("current_reference_index"),
                            "next_reference_index": next_reference_index,
                            "trisame_count": trisame_count,
                            "all_trisame_sources_exhausted": True,
                            "reason": "next_reference_index_exceeds_trisame_count",
                        "continuation_mode": bool(context.get("continuation_mode")),
                        "continuation_plan": context.get("continuation_plan"),
                        "final_reference_recollect_required": bool(selection_for_continue.get("final_reference_recollect_required")),
                        "recollect_reference_index": selection_for_continue.get("recollect_reference_index"),
                        "recollect_reason": selection_for_continue.get("recollect_reason"),
                    }
                        timing.write()
                        _write_second_stage_result(configs, result, task_result)
                        return result
                    result = {
                        "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
                        "status": issue["code"],
                        "final_status": issue["code"],
                        "current_state": issue["code"],
                        "current_reference": context.get("current_reference"),
                        "returned_list_source": context.get("returned_list_source"),
                        "returned_list_source_verified": context.get("returned_list_source_verified"),
                        "s14_records": list(context["damage_by_part"].values()),
                        "s14_skip_count": context["s14_skip_count"],
                        "s14_collect_done": context.get("s14_collect_done"),
                        "reference_early_exit": bool(current_reference.get("reference_early_exit")),
                        "early_exit_rule_id": current_reference.get("early_exit_rule_id"),
                        "early_exit_rule_clause_id": current_reference.get("early_exit_rule_clause_id"),
                        "early_exit_allowed": bool(current_reference.get("early_exit_allowed")),
                        "early_exit_decision": current_reference.get("early_exit_decision"),
                        "early_rejected_reference_history": context.get("early_rejected_reference_history"),
                        "excluded_reference_history": context.get("excluded_reference_history"),
                        "s14_tab_records": context.get("s14_tab_records"),
                        "s14_image_records": context.get("s14_image_records"),
                        "s14_horizontal_swipes": context.get("s14_horizontal_swipes"),
                        "s14_sequence_terminal_snapshot": context.get("s14_sequence_terminal_snapshot"),
                        "s14_return_attempts": context.get("s14_return_attempts"),
                        "target_score": context.get("target_score"),
                        "selection": context.get("selection"),
                        "reference_history": continue_history,
                        "reference_history_write_gate": continue_history_gate,
                        "previous_reference_index": context.get("previous_reference_index"),
                        "current_reference_index": context.get("current_reference_index"),
                        "next_reference_index": next_reference_index,
                        "remaining_reference_count": max(0, int(trisame_count or 0) - next_reference_index + 1)
                        if trisame_count is not None
                        else None,
                        "should_continue_reference_collection": True,
                        "continue_reason": continue_reason,
                        "current_reference_excluded_from_boundary": bool(current_reference.get("excluded_from_boundary")),
                        "current_reference_excluded_reason": current_reference.get("excluded_from_boundary_reason"),
                        "continuation_mode": bool(context.get("continuation_mode")),
                        "continuation_plan": context.get("continuation_plan"),
                        "invalid_partial_reference_detected": context.get("invalid_partial_reference_detected"),
                        "invalid_partial_reference_index": context.get("invalid_partial_reference_index"),
                        "invalid_reason": context.get("invalid_partial_reference_reason"),
                        "continuation_recovered_next_reference_index": context.get("continuation_recovered_next_reference_index"),
                    }
                    low_score_continue_fields = _v33_low_score_skip_continue_fields(context)
                    if low_score_continue_fields:
                        result.update(low_score_continue_fields)
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
        if exc.code in S12_CLAIM_FIELD_NEEDS_REVIEW_CODES:
            current_reference = context.get("current_reference") or {}
            error_reference_history, reference_history_gate = _safe_reference_history_with_current_reference(
                context,
                purpose="s12_claim_fields_missing_needs_review_result",
                require_identity=True,
                require_physical_evidence=True,
            )
            result = {
                "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
                "status": "NEEDS_REVIEW",
                "final_status": "NEEDS_REVIEW",
                "current_state": "NEEDS_REVIEW",
                "business_status": "NEEDS_REVIEW",
                "issue_code": exc.code,
                "stop_code": exc.code,
                "issue_context": exc.context,
                "manual_review_required": True,
                "manual_review_reason": exc.code,
                "manual_review_reasons": [exc.code],
                "auto_pricing_allowed": False,
                "final_price_allowed": False,
                "pricing_chain_available": False,
                "current_reference": current_reference,
                "reference_history": error_reference_history,
                "reference_history_write_gate": reference_history_gate,
                "previous_reference_index": context.get("previous_reference_index"),
                "current_reference_index": context.get("current_reference_index"),
                "next_reference_index": context.get("next_reference_index"),
                "continuation_mode": bool(context.get("continuation_mode")),
                "continuation_plan": context.get("continuation_plan"),
                "invalid_partial_reference_detected": context.get("invalid_partial_reference_detected"),
                "invalid_partial_reference_index": context.get("invalid_partial_reference_index"),
                "invalid_partial_reference_reason": context.get("invalid_partial_reference_reason"),
                "selection": context.get("selection"),
                "target_score": context.get("target_score"),
                "s12_claim_field_missing_needs_review": True,
            }
            _write_second_stage_result(configs, result, task_result)
            return result
        low_score_continue_fields = _v33_low_score_skip_continue_fields(context)
        if low_score_continue_fields:
            current_reference = context.get("current_reference") or {}
            error_reference_history, reference_history_gate = _safe_reference_history_with_current_reference(
                context,
                purpose="low_score_continue_exception_result",
                require_identity=True,
                require_physical_evidence=True,
            )
            if reference_history_gate.get("reference_history_write_blocked") is True:
                blocked_code = str(reference_history_gate.get("reference_history_write_block_code") or REFERENCE_HISTORY_PHYSICAL_PROOF_MISSING)
                blocked_result = {
                    "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
                    "status": blocked_code,
                    "issue_code": blocked_code,
                    "current_state": blocked_code,
                    "converted_from_exception": exc.code,
                    "converted_from_exception_blocked": True,
                    "current_reference": current_reference,
                    "reference_history": error_reference_history,
                    "reference_history_write_gate": reference_history_gate,
                    "target_score": context.get("target_score"),
                    "selection": context.get("selection"),
                }
                _write_second_stage_result(configs, blocked_result, task_result)
                return blocked_result
            result = {
                "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
                **low_score_continue_fields,
                "current_reference": current_reference,
                "reference_history": error_reference_history,
                "reference_history_write_gate": reference_history_gate,
                "previous_reference_index": context.get("previous_reference_index"),
                "returned_list_source": context.get("returned_list_source"),
                "returned_list_source_verified": context.get("returned_list_source_verified"),
                "s14_records": list((context.get("damage_by_part") or {}).values()),
                "s14_skip_count": context.get("s14_skip_count"),
                "s14_collect_done": context.get("s14_collect_done"),
                "all_s14_tabs": context.get("all_s14_tabs"),
                "s14_tab_records": context.get("s14_tab_records"),
                "s14_image_records": context.get("s14_image_records"),
                "s14_horizontal_swipes": context.get("s14_horizontal_swipes"),
                "s14_sequence_terminal_snapshot": context.get("s14_sequence_terminal_snapshot"),
                "s14_return_attempts": context.get("s14_return_attempts"),
                "target_score": context.get("target_score"),
                "selection": context.get("selection"),
                "converted_from_exception": exc.code,
                "converted_from_exception_reason": "legal_v33_low_score_skip_continue_next_reference",
            }
            _write_second_stage_result(configs, result, task_result)
            return result
        invalid_reason = context.get("invalid_reason")
        if context.get("s14_triggered") and context.get("s14_collect_done") is not True:
            invalid_reason = invalid_reason or "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED"
        if context.get("exclude_current_reference_from_history"):
            error_reference_history = list(context.get("reference_history") or [])
            reference_history_gate = {
                "reference_history_write_gate": True,
                "purpose": "guazi_flow_error_result",
                "current_reference_written": False,
                "current_reference_excluded_from_history": True,
            }
        else:
            error_reference_history, reference_history_gate = _safe_reference_history_with_current_reference(
                context,
                purpose="guazi_flow_error_result",
                require_identity=True,
            )
        result = {
            "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
            "status": exc.code,
            "issue_code": exc.code,
            "issue_context": exc.context,
            "valid": False if invalid_reason else None,
            "invalid_reason": invalid_reason,
            "current_reference": context.get("current_reference"),
            "reference_history": error_reference_history,
            "reference_history_write_gate": reference_history_gate,
            "current_reference_excluded_from_history": bool(context.get("exclude_current_reference_from_history")),
            "previous_reference_index": context.get("previous_reference_index"),
            "current_reference_index": context.get("current_reference_index"),
            "next_reference_index": context.get("next_reference_index"),
            "continuation_mode": bool(context.get("continuation_mode")),
            "continuation_plan": context.get("continuation_plan"),
            "invalid_partial_reference_detected": context.get("invalid_partial_reference_detected"),
            "invalid_partial_reference_index": context.get("invalid_partial_reference_index"),
            "invalid_partial_reference_reason": context.get("invalid_partial_reference_reason"),
            "continuation_recovered_next_reference_index": context.get("continuation_recovered_next_reference_index"),
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
    except Exception as exc:
        try:
            timing.write()
        except Exception:
            pass
        traceback_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        traceback_tail = "".join(traceback_lines[-8:])
        issue_code = "S10_TO_S11_FAST_XML_RUNTIME_EXCEPTION" if "_capture_s10_to_s11_fast_xml" in traceback_tail else "SECOND_STAGE_RUNTIME_EXCEPTION"
        latest_compressed_xml = _latest_artifact_path("artifacts", "debug", "s10_to_s11_*_compressed.xml")
        latest_full_xml = _latest_artifact_path("artifacts", "debug", "s10_to_s11_*_full_fallback.xml")
        latest_s10_to_s11_screenshot = _latest_artifact_path("artifacts", "screenshots", "s10_to_s11*.png")
        snapshot_path = ""
        xml_path = ""
        if isinstance(snapshot, dict):
            snapshot_path = str(snapshot.get("screenshot_path") or "")
            xml_path = str(snapshot.get("xml_path") or "")
        if context.get("exclude_current_reference_from_history"):
            error_reference_history = list(context.get("reference_history") or [])
            reference_history_gate = {
                "reference_history_write_gate": True,
                "purpose": "runtime_exception_result",
                "current_reference_written": False,
                "current_reference_excluded_from_history": True,
            }
        else:
            error_reference_history, reference_history_gate = _safe_reference_history_with_current_reference(
                context,
                purpose="runtime_exception_result",
                require_identity=True,
            )
        result = {
            "metadata": {"project": "guazi_app_data_system", "mode": "device_real_mainline", "field_scope": "contract_only"},
            "status": "RUN_FAILED_WITH_ISSUE",
            "issue_code": issue_code,
            "current_state": "RUN_FAILED_WITH_ISSUE",
            "failed_state": state,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback_tail": traceback_tail[-4000:],
            "screenshot_path": snapshot_path or latest_s10_to_s11_screenshot,
            "xml_path": xml_path,
            "compressed_xml_path": latest_compressed_xml,
            "full_xml_path": latest_full_xml,
            "first_stage_evidence": first_stage_evidence,
            "current_reference": context.get("current_reference"),
            "reference_history": error_reference_history,
            "reference_history_write_gate": reference_history_gate,
            "current_reference_excluded_from_history": bool(context.get("exclude_current_reference_from_history")),
            "previous_reference_index": context.get("previous_reference_index"),
            "current_reference_index": context.get("current_reference_index"),
            "next_reference_index": context.get("next_reference_index"),
            "continuation_mode": bool(context.get("continuation_mode")),
            "continuation_plan": context.get("continuation_plan"),
            "invalid_partial_reference_detected": context.get("invalid_partial_reference_detected"),
            "invalid_partial_reference_index": context.get("invalid_partial_reference_index"),
            "invalid_partial_reference_reason": context.get("invalid_partial_reference_reason"),
            "continuation_recovered_next_reference_index": context.get("continuation_recovered_next_reference_index"),
            "s10_to_s11_wait": context.get("current_reference", {}).get("s10_to_s11_wait") or context.get("s10_to_s11_wait"),
            "phone_test": phone_test or {},
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
    result_text = json.dumps(_result_safe(run_s10_to_s16_mainline(runtime)), ensure_ascii=False, indent=2)
    try:
        print(result_text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(result_text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
