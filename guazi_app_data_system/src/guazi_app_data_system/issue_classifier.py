"""Contract-aware runtime issue classification.

The classifier compares the current page contract, action contract, expected
next state, and actual click target so execution failures can be attributed to
deterministic contract mismatches instead of broad fallback issue codes.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

from .trim_normalizer import emission_normalization_used, exact_trim_match_with_emission_normalization, normalize_trim_for_match


SERIES_ACTION_TARGET_MISMATCH_SOLUTION_ID = "SOL-SERIES-ACTION-TARGET-MISMATCH-CLICK-MODEL-BUTTON"
S07_CONTRACT_DRIFT_SOLUTION_ID = "SOL-S07-CONTRACT-DRIFT-TO-GENERIC-FILTER-DETECT-MODEL-CONFIG"
S08_YEAR_CONTRACT_DRIFT_SOLUTION_ID = "SOL-S08-YEAR-CONTRACT-DRIFT-TO-OPTION-SCAN-DETECT-LEFT-AGE-TAB"
YEAR_FILTER_EXACT_SLIDER_SOLUTION_ID = "SOL-YEAR-FILTER-SET-EXACT-AGE-SLIDER-WITH-LIST-SECONDARY-CHECK"
AGE_SLIDER_BOTH_HANDLES_SOLUTION_ID = "SOL-AGE-SLIDER-SET-BOTH-HANDLES-TO-TARGET"
RIGHT_HANDLE_VISUAL_CENTER_CANDIDATE_ID = "SOL-RIGHT-AGE-HANDLE-RECOGNIZE-VISUAL-HANDLE-CENTER"
RIGHT_HANDLE_TARGET_Y_CANDIDATE_ID = "SOL-RIGHT-AGE-HANDLE-USE-LEFT-HANDLE-CENTER-AS-TARGET"
AGE_SLIDER_MIXED_LAYER_CANDIDATE_ID = "SOL-AGE-SLIDER-FILTER-MIXED-BACKGROUND-LIST-NODES"
RIGHT_HANDLE_LONG_PRESS_CANDIDATE_ID = "SOL-RIGHT-AGE-HANDLE-LONG-PRESS-DRAG-CANDIDATE"
TASK_COLOR_CHANGED_SOLUTION_ID = "SOL-TASK-COLOR-CHANGED-RESELECT-COLOR"
COLOR_MULTI_SELECTED_SOLUTION_ID = "SOL-COLOR-MULTI-SELECTED-REMOVE-STALE-COLOR"

AGE_TAB_LABEL = "\u8f66\u9f84"
AGE_TITLE_LABEL = "\u8f66\u9f84\uff08\u5e74\uff09"
UNLIMITED_AGE_LABEL = "\u4e0d\u9650\u8f66\u9f84"
UNLIMITED_TICK_LABEL = "\u4e0d\u9650"
VEHICLE_LIST_MARKERS = {"\u7efc\u5408\u6392\u5e8f", "\u4ef7\u683c\u4ece\u4f4e\u5230\u9ad8"}
AGE_TICK_RE = re.compile(r"^\d{1,2}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_bounds(bounds: str | None) -> list[int] | None:
    if not bounds:
        return None
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    if not match:
        return None
    return [int(value) for value in match.groups()]


def _node_label(node: ElementTree.Element) -> str:
    return (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()


def _normalize_click_target(actual_clicked_target: Any) -> dict[str, Any]:
    if isinstance(actual_clicked_target, dict):
        return {
            "text": str(actual_clicked_target.get("text") or "").strip(),
            "role": str(actual_clicked_target.get("role") or "").strip(),
            "series": str(actual_clicked_target.get("series") or "").strip() or None,
            "bounds": actual_clicked_target.get("bounds"),
        }
    text = str(actual_clicked_target or "").strip()
    return {"text": text, "role": "", "series": None, "bounds": None}


def _series_contract_evidence(xml_text: str, target_series: str) -> dict[str, Any]:
    evidence = {
        "series_row_found": False,
        "series_row_bounds": None,
        "series_row_label": None,
        "model_button_found": False,
        "model_button_bounds": None,
        "model_button_text": None,
        "labels_blob": "",
    }
    if not xml_text.strip():
        return evidence
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return evidence

    labels: list[str] = []
    for node in root.iter("node"):
        label = _node_label(node)
        if label:
            labels.append(label)
        if target_series and target_series not in label:
            continue
        evidence["series_row_found"] = True
        evidence["series_row_label"] = label
        evidence["series_row_bounds"] = _parse_bounds(node.attrib.get("bounds"))
        for descendant in node.iter("node"):
            if descendant is node:
                continue
            child_label = _node_label(descendant)
            if "\u8f66\u578b" in child_label:
                evidence["model_button_found"] = True
                evidence["model_button_text"] = "\u8f66\u578b"
                evidence["model_button_bounds"] = _parse_bounds(descendant.attrib.get("bounds"))
                break
        if evidence["model_button_found"]:
            break
    evidence["labels_blob"] = " ".join(labels)
    return evidence


def _age_slider_panel_evidence(xml_text: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "age_slider_evidence_found": False,
        "age_title_seen": False,
        "age_tab_seen": False,
        "unlimited_age_seen": False,
        "unlimited_tick_seen": False,
        "tick_values": [],
        "vehicle_list_marker_seen": False,
        "labels_blob": "",
    }
    if not xml_text.strip():
        return evidence
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return evidence

    labels: list[str] = []
    tick_values: set[int] = set()
    for node in root.iter("node"):
        label = _node_label(node)
        if not label:
            continue
        labels.append(label)
        if AGE_TITLE_LABEL in label:
            evidence["age_title_seen"] = True
        if label == AGE_TAB_LABEL:
            evidence["age_tab_seen"] = True
        if UNLIMITED_AGE_LABEL in label:
            evidence["unlimited_age_seen"] = True
        if label == UNLIMITED_TICK_LABEL:
            evidence["unlimited_tick_seen"] = True
        if AGE_TICK_RE.match(label):
            tick_values.add(int(label))
        if any(marker in label for marker in VEHICLE_LIST_MARKERS):
            evidence["vehicle_list_marker_seen"] = True

    evidence["tick_values"] = sorted(tick_values)
    evidence["labels_blob"] = " ".join(labels)
    evidence["age_slider_evidence_found"] = bool(
        evidence["age_title_seen"]
        and len(evidence["tick_values"]) >= 4
        and 6 in evidence["tick_values"]
    )
    return evidence


def _same_row_or_card(series_row_bounds: Any, click_bounds: Any) -> bool:
    if not isinstance(series_row_bounds, list) or not isinstance(click_bounds, list):
        return False
    if len(series_row_bounds) != 4 or len(click_bounds) != 4:
        return False
    row_left, row_top, row_right, row_bottom = series_row_bounds
    click_left, click_top, click_right, click_bottom = click_bounds
    click_center_x = (click_left + click_right) // 2
    click_center_y = (click_top + click_bottom) // 2
    return row_left <= click_center_x <= row_right and row_top <= click_center_y <= row_bottom


class IssueClassifier:
    def __init__(self, pages_config: dict[str, Any], actions_config: dict[str, Any]) -> None:
        self.pages = {page["id"]: page for page in pages_config.get("pages", [])}
        self.actions = actions_config.get("actions", {})

    def classify(
        self,
        *,
        current_state: str,
        intended_action: str,
        expected_next_state: str,
        actual_next_state: str,
        actual_clicked_target: Any,
        before_xml: str,
        after_xml: str,
        page_contract: dict[str, Any] | None = None,
        action_contract: dict[str, Any] | None = None,
        task_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        page_contract = page_contract or self.pages.get(current_state, {})
        action_contract = action_contract or self.actions.get(intended_action, {})
        task_context = task_context or {}
        target_series = str(task_context.get("series") or task_context.get("target_series") or "").strip()
        target_brand = str(task_context.get("brand") or task_context.get("target_brand") or "").strip()
        clicked_target = _normalize_click_target(actual_clicked_target)
        before = _series_contract_evidence(before_xml, target_series)
        after = _series_contract_evidence(after_xml, target_series)
        before_age_slider = _age_slider_panel_evidence(before_xml)
        after_age_slider = _age_slider_panel_evidence(after_xml)

        evidence = {
            "current_state": current_state,
            "intended_action": intended_action,
            "expected_next_state": expected_next_state,
            "actual_next_state": actual_next_state,
            "actual_clicked_target": clicked_target,
            "target_series": target_series,
            "target_brand": target_brand,
            "page_allowed_actions": list(page_contract.get("allowed_actions", [])),
            "action_contract": {
                "action_id": intended_action,
                "type": action_contract.get("type"),
                "text": action_contract.get("text"),
                "target_role": action_contract.get("target_role"),
                "source": action_contract.get("source"),
            },
            "before": before,
            "after": after,
            "before_age_slider": before_age_slider,
            "after_age_slider": after_age_slider,
        }

        if actual_next_state in {
            "SYSTEM_OVERLAY_OR_KEYGUARD_BLOCKING_APP",
            "THIRD_PARTY_LOGIN_OVERLAY_BLOCKING_APP",
            "POPUP_MARKETING_OVERLAY",
            "POPUP_UNCONTRACTED",
        }:
            return {
                "issue_code": actual_next_state,
                "root_cause": "Runtime left the business page and entered a system overlay or popup state.",
                "recommended_solution_id": None,
                "candidate_or_approved": "candidate",
                "allowed_auto_actions": [],
                "forbidden_actions": [],
                "confidence": 0.99,
                "evidence": evidence,
                "solution_record": None,
            }

        drift = self._classify_contract_drift(
            current_state=current_state,
            intended_action=intended_action,
            page_contract=page_contract,
            action_contract=action_contract,
            evidence=evidence,
        )
        if drift:
            return drift

        if self._is_series_action_target_mismatch(
            current_state=current_state,
            intended_action=intended_action,
            expected_next_state=expected_next_state,
            actual_next_state=actual_next_state,
            clicked_target=clicked_target,
            before=before,
            after=after,
        ):
            classification = {
                "issue_code": "SERIES_ACTION_TARGET_MISMATCH",
                "root_cause": (
                    "S04 requires clicking the target series row's right-side "
                    "'\u8f66\u578b' button. The actual target was the series name/card, "
                    "so execution never entered S05."
                ),
                "recommended_solution_id": SERIES_ACTION_TARGET_MISMATCH_SOLUTION_ID,
                "candidate_or_approved": "approved",
                "allowed_auto_actions": [
                    "capture_screenshot",
                    "dump_ui_xml",
                    "detect_target_series_row",
                    "detect_series_model_button",
                    "click_series_model_button",
                    "recapture_screenshot",
                    "redump_ui_xml",
                    "recognize_model_year_trim_page",
                    "record_issue",
                    "lookup_knowledge_base",
                ],
                "forbidden_actions": [
                    "click_series_card",
                    "click_series_name",
                    "click_other_series",
                    "click_other_series_model_button",
                    "click_model_year",
                    "click_trim",
                    "click_confirm",
                    "enter_vehicle_list",
                    "collect_vehicle_data",
                    "retry_same_wrong_click",
                ],
                "confidence": 0.99,
                "evidence": evidence,
            }
            classification["solution_record"] = self._build_series_action_target_mismatch_solution()
            return classification

        if self._is_age_slider_panel_misclassified_as_s07(
            current_state=current_state,
            intended_action=intended_action,
            expected_next_state=expected_next_state,
            actual_next_state=actual_next_state,
            after_age_slider=after_age_slider,
        ):
            return {
                "issue_code": "AGE_SLIDER_PANEL_MISCLASSIFIED_AS_S07",
                "root_cause": (
                    "The foreground contract is still the S08 exact-age-slider panel. "
                    "Vehicle-list text in the background XML is residual noise and must not "
                    "override visible age-slider/track evidence."
                ),
                "recommended_solution_id": YEAR_FILTER_EXACT_SLIDER_SOLUTION_ID,
                "candidate_or_approved": "approved",
                "allowed_auto_actions": [
                    "record_issue",
                    "lookup_knowledge_base",
                    "detect_age_exact_slider",
                    "read_age_slider_current_value",
                    "read_age_slider_bounds",
                    "calculate_target_age",
                ],
                "forbidden_actions": [
                    "enter_vehicle_list",
                    "click_vehicle_card",
                    "click_sort",
                    "click_comprehensive_sort",
                    "click_price_low_to_high",
                    "collect_vehicle_data",
                    "read_vehicle_price",
                    "read_vehicle_year",
                    "read_vehicle_mileage",
                    "read_vehicle_city",
                    "read_seller_info",
                    "read_finance_tags",
                ],
                "confidence": 0.99,
                "evidence": {
                    **evidence,
                    "page_priority": "S08_AGE_EXACT_SLIDER_PANEL",
                    "background_list_residual_ignored": True,
                },
                "solution_record": self._build_year_filter_exact_age_slider_solution(),
            }

        if current_state == "S07_VEHICLE_LIST_PAGE" and intended_action == "click_vehicle_model_config_entry":
            if actual_next_state == "VEHICLE_DETAIL_PAGE":
                return {
                    "issue_code": "UNEXPECTED_DETAIL_PAGE",
                    "root_cause": "Clicking the explicit vehicle model-config entry unexpectedly entered a vehicle detail page; collection must stay blocked.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "candidate",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "enter_detail_page",
                        "collect_vehicle_data",
                        "read_vehicle_price",
                        "read_vehicle_year",
                        "read_vehicle_mileage",
                        "read_vehicle_city",
                        "read_seller_info",
                    ],
                    "confidence": 0.96,
                    "evidence": evidence,
                    "solution_record": None,
                }
            if actual_next_state == "S07_VEHICLE_LIST_PAGE":
                return {
                    "issue_code": "VEHICLE_MODEL_CONFIG_CLICK_NO_NAVIGATION",
                    "root_cause": "The explicit vehicle model-config entry was clicked once, but the page still appears to be ordinary S07 vehicle-list state.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "retry_same_wrong_click",
                        "click_generic_filter",
                        "click_color_filter",
                        "click_year_filter",
                        "click_sort",
                        "click_vehicle_card",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.94,
                    "evidence": evidence,
                    "solution_record": None,
                }

        if current_state == "S08_VEHICLE_MODEL_CONFIG_PANEL" and intended_action == "click_color_entry":
            if actual_next_state in {"VEHICLE_DETAIL_PAGE", "VEHICLE_LIST_OR_DETAIL", "S07_VEHICLE_LIST_PAGE"}:
                return {
                    "issue_code": "WRONG_PAGE_AFTER_COLOR_ENTRY_CLICK",
                    "root_cause": "The S08 color-entry click reached a vehicle list/detail or another unsafe page; collection must remain blocked.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "candidate",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_target_color",
                        "click_any_color_option",
                        "click_year_filter",
                        "click_confirm",
                        "enter_detail_page",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.96,
                    "evidence": evidence,
                    "solution_record": None,
                }
            if actual_next_state == "S08_VEHICLE_MODEL_CONFIG_PANEL":
                return {
                    "issue_code": "COLOR_ENTRY_CLICK_NO_PANEL",
                    "root_cause": "The explicit S08 color entry was clicked once, but a color-selection area was not verified.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "retry_same_wrong_click",
                        "click_target_color",
                        "click_any_color_option",
                        "click_year_filter",
                        "click_confirm",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.94,
                    "evidence": evidence,
                    "solution_record": None,
                }

        if current_state == "S08_COLOR_SELECTION_PANEL" and intended_action == "read_color_selection_panel_contract":
            target_color = str(task_context.get("color") or task_context.get("target_color") or "").strip()
            if actual_next_state == "S08_COLOR_SELECTION_PANEL" and not clicked_target["text"]:
                return {
                    "issue_code": "TARGET_COLOR_NOT_FOUND",
                    "root_cause": "The color-selection area is visible, but the exact target color is not present. Similar color matching is forbidden.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_similar_color",
                        "click_any_color_option",
                        "auto_merge_color_family",
                        "fuzzy_match_color",
                        "click_confirm",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.96,
                    "evidence": {**evidence, "target_color": target_color},
                    "solution_record": None,
                }

        if current_state == "S08_COLOR_SELECTION_PANEL" and intended_action == "click_target_color_option":
            target_color = str(task_context.get("color") or task_context.get("target_color") or "").strip()
            if clicked_target["text"] != target_color:
                return {
                    "issue_code": "COLOR_ACTION_TARGET_MISMATCH",
                    "root_cause": "The planned color click target does not exactly match the task target color. Similar colors and aliases are forbidden.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_similar_color",
                        "click_any_color_option",
                        "auto_alias_color",
                        "auto_merge_color_family",
                        "fuzzy_match_color",
                        "click_confirm",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.99,
                    "evidence": {**evidence, "target_color": target_color},
                    "solution_record": None,
                }
            if actual_next_state in {"VEHICLE_DETAIL_PAGE", "VEHICLE_LIST_OR_DETAIL", "S07_VEHICLE_LIST_PAGE"}:
                return {
                    "issue_code": "WRONG_PAGE_AFTER_COLOR_CLICK",
                    "root_cause": "The exact target color click reached a vehicle list/detail or another unsafe page; collection must remain blocked.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "candidate",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_year_filter",
                        "click_confirm",
                        "enter_detail_page",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.96,
                    "evidence": {**evidence, "target_color": target_color},
                    "solution_record": None,
                }
            if actual_next_state != "S08_COLOR_SELECTED":
                return {
                    "issue_code": "COLOR_CLICK_NO_SELECTION",
                    "root_cause": "The exact target color was clicked once, but selected state was not verified.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "retry_same_wrong_click",
                        "click_similar_color",
                        "click_year_filter",
                        "click_confirm",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.94,
                    "evidence": {**evidence, "target_color": target_color},
                    "solution_record": None,
                }

        if current_state in {"S08_COLOR_SELECTED", "S08_COLOR_SELECTED_SINGLE_TARGET"} and intended_action == "click_year_or_age_entry":
            target_color = str(task_context.get("color") or task_context.get("target_color") or "").strip()
            selected_color = str(task_context.get("selected_color") or task_context.get("current_selected_color") or "").strip()
            panel_color_confirmed = bool(
                task_context.get("selected_color_ui_confirmed")
                or task_context.get("selected_color_confirmed_in_panel")
                or task_context.get("selected_color_visible_in_panel")
            )
            if not panel_color_confirmed:
                return {
                    "issue_code": "COLOR_STATE_NOT_CONFIRMED_BEFORE_AGE_SELECTION",
                    "root_cause": "The runtime tried to enter the year/age flow before the returned S08 model-config panel visibly confirmed the task target color as selected.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_year_or_age_entry",
                        "click_confirm",
                        "click_view_result",
                        "enter_vehicle_list",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.99,
                    "evidence": {
                        **evidence,
                        "target_color": target_color,
                        "selected_color": selected_color,
                        "selected_color_ui_confirmed": panel_color_confirmed,
                    },
                    "solution_record": None,
                }
            if target_color and selected_color and target_color != selected_color:
                return {
                    "issue_code": "TASK_COLOR_CHANGED_INVALIDATES_SELECTED_COLOR",
                    "root_cause": "The currently selected color no longer matches the current task color; downstream age or confirm actions must stay blocked until the task color is reselected.",
                    "recommended_solution_id": TASK_COLOR_CHANGED_SOLUTION_ID,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "continue_with_old_color",
                        "click_confirm",
                        "enter_vehicle_list",
                        "collect_vehicle_data",
                        "skip_color_revalidation",
                    ],
                    "confidence": 0.99,
                    "evidence": {**evidence, "target_color": target_color, "selected_color": selected_color},
                    "solution_record": self._build_task_color_changed_solution(),
                }
            allowed_entry_labels = {"年份", "车龄", "上牌年份", "上牌时间", "年款"}
            if clicked_target["text"] not in allowed_entry_labels and clicked_target["role"] != "year_or_age_entry":
                return {
                    "issue_code": "YEAR_ACTION_TARGET_MISMATCH",
                    "root_cause": "The planned click target is not the explicit year/age entry. Substituting color, confirm, reset, close, sort, or nearby controls is forbidden.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_target_year_option",
                        "click_nearby_year_option",
                        "click_color_filter",
                        "click_confirm",
                        "click_view_result",
                        "click_reset",
                        "click_close",
                        "click_sort",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.99,
                    "evidence": evidence,
                    "solution_record": None,
                }
            if actual_next_state in {"VEHICLE_DETAIL_PAGE", "VEHICLE_LIST_OR_DETAIL", "S07_VEHICLE_LIST_PAGE"}:
                return {
                    "issue_code": "WRONG_PAGE_AFTER_YEAR_ENTRY_CLICK",
                    "root_cause": "The year/age entry click reached a vehicle list/detail or another unsafe page; collection must remain blocked.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "candidate",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_target_year_option",
                        "click_nearby_year_option",
                        "click_confirm",
                        "enter_detail_page",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.96,
                    "evidence": evidence,
                    "solution_record": None,
                }
            if actual_next_state != "S08_YEAR_SELECTION_PANEL":
                return {
                    "issue_code": "YEAR_ENTRY_CLICK_NO_PANEL",
                    "root_cause": "The explicit year/age entry was clicked once, but a year/age selection area was not verified.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "retry_same_wrong_click",
                        "click_target_year_option",
                        "click_nearby_year_option",
                        "click_confirm",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.94,
                    "evidence": evidence,
                    "solution_record": None,
                }

        if current_state == "S08_YEAR_SELECTION_PANEL" and intended_action == "detect_left_age_tab":
            if actual_next_state == "S08_YEAR_SELECTION_PANEL" and not clicked_target["text"]:
                return {
                    "issue_code": "LEFT_AGE_TAB_NOT_FOUND",
                    "root_cause": "The year/age selection panel is visible, but the explicit left-side 车龄 tab is not present. Unlimited-age and ordinary age options may not be substituted.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_unlimited_age",
                        "click_age_option",
                        "set_age_range",
                        "click_confirm",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.96,
                    "evidence": {**evidence, "target_vehicle_year": task_context.get("vehicle_year") or task_context.get("target_vehicle_year")},
                    "solution_record": None,
                }

        if current_state == "S08_YEAR_SELECTION_PANEL" and intended_action == "click_left_age_tab":
            if clicked_target["text"] != AGE_TAB_LABEL:
                return {
                    "issue_code": "CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH",
                    "root_cause": "The planned click target is not the explicit left-side 车龄 tab. Unlimited age, ordinary options, and slider interactions are forbidden at this step.",
                    "recommended_solution_id": S08_YEAR_CONTRACT_DRIFT_SOLUTION_ID,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": ["record_issue", "lookup_knowledge_base", "detect_left_age_tab"],
                    "forbidden_actions": [
                        "click_unlimited_age",
                        "click_age_option",
                        "drag_slider",
                        "click_confirm",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.99,
                    "evidence": evidence,
                    "solution_record": self._build_s08_year_contract_drift_solution(),
                }
            if self._is_age_slider_panel_misclassified_as_s07(
                current_state=current_state,
                intended_action=intended_action,
                expected_next_state=expected_next_state,
                actual_next_state=actual_next_state,
                after_age_slider=after_age_slider,
            ):
                return {
                    "issue_code": "AGE_SLIDER_PANEL_MISCLASSIFIED_AS_S07",
                    "root_cause": (
                        "The left-side age tab revealed the S08 exact-age-slider panel, "
                        "but background vehicle-list text caused a lower-priority S07 classification."
                    ),
                    "recommended_solution_id": YEAR_FILTER_EXACT_SLIDER_SOLUTION_ID,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [
                        "record_issue",
                        "lookup_knowledge_base",
                        "detect_age_exact_slider",
                        "read_age_slider_current_value",
                        "read_age_slider_bounds",
                        "calculate_target_age",
                    ],
                    "forbidden_actions": [
                        "enter_vehicle_list",
                        "click_vehicle_card",
                        "click_sort",
                        "click_comprehensive_sort",
                        "click_price_low_to_high",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.99,
                    "evidence": {
                        **evidence,
                        "page_priority": "S08_AGE_EXACT_SLIDER_PANEL",
                        "background_list_residual_ignored": True,
                    },
                    "solution_record": self._build_year_filter_exact_age_slider_solution(),
                }
            if actual_next_state != "S08_AGE_EXACT_SLIDER_PANEL":
                return {
                    "issue_code": "LEFT_AGE_TAB_CLICK_NO_SLIDER",
                    "root_cause": "The left-side 车龄 tab was clicked once, but the right-side exact age slider/track was not verified.",
                    "recommended_solution_id": YEAR_FILTER_EXACT_SLIDER_SOLUTION_ID,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_unlimited_age",
                        "click_age_option",
                        "set_age_range",
                        "expand_age_range",
                        "click_confirm",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.95,
                    "evidence": evidence,
                    "solution_record": self._build_year_filter_exact_age_slider_solution(),
                }

        if current_state == "S08_AGE_EXACT_SLIDER_PANEL" and intended_action == "detect_age_exact_slider":
            if actual_next_state == "S08_AGE_EXACT_SLIDER_PANEL" and not clicked_target["text"]:
                return {
                    "issue_code": "AGE_EXACT_SLIDER_NOT_FOUND",
                    "root_cause": "The panel should expose an exact age slider, but the slider/track was not verified. Treating the panel as age options or age ranges is forbidden.",
                    "recommended_solution_id": YEAR_FILTER_EXACT_SLIDER_SOLUTION_ID,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_unlimited_age",
                        "click_age_option",
                        "set_age_range",
                        "expand_age_range",
                        "click_confirm",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.96,
                    "evidence": evidence,
                    "solution_record": self._build_year_filter_exact_age_slider_solution(),
                }

        if current_state in {"S08_AGE_EXACT_SLIDER_PANEL", "S08_AGE_LEFT_HANDLE_SET_ONLY"} and intended_action == "set_right_age_handle_to_target":
            target_age = str(task_context.get("target_age") or task_context.get("age") or "").strip()
            left_after = task_context.get("left_handle_value_after")
            right_after = task_context.get("right_handle_value_after")
            physical_overlap = task_context.get("left_and_right_handle_physical_overlap_at_target_tick")
            age_calc_ok = task_context.get("target_age_calculation_verified")
            if target_age and (
                str(right_after) != target_age
                or physical_overlap is not True
                or age_calc_ok is not True
            ):
                return {
                    "issue_code": "RIGHT_AGE_HANDLE_SET_NO_VERIFICATION",
                    "root_cause": (
                        "The right age handle was adjusted, but runtime could not verify "
                        "right_handle_value == target_age, physical overlap with the left handle, "
                        "and target-age calculation. The state remains blocked."
                    ),
                    "recommended_solution_id": None,
                    "candidate_or_approved": "candidate",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "retry_right_handle_drag_without_approval",
                        "click_confirm",
                        "click_view_result",
                        "enter_vehicle_list",
                        "collect_vehicle_data",
                        "treat_range_as_exact",
                    ],
                    "confidence": 0.98,
                    "evidence": {
                        **evidence,
                        "target_age": target_age,
                        "left_handle_value_after": left_after,
                        "right_handle_value_after": right_after,
                        "left_and_right_handle_physical_overlap_at_target_tick": physical_overlap,
                        "target_age_calculation_verified": age_calc_ok,
                    },
                    "solution_record": None,
                }

        if current_state == "S08_AGE_EXACT_SLIDER_PANEL" and intended_action == "set_age_slider_exact_value":
            target_age = str(task_context.get("target_age") or task_context.get("age") or "").strip()
            left_after = task_context.get("left_handle_value_after")
            right_after = task_context.get("right_handle_value_after")
            if (
                target_age
                and str(left_after) == target_age
                and str(right_after) not in {"", target_age}
            ):
                return {
                    "issue_code": "AGE_SLIDER_ONLY_LEFT_HANDLE_SET",
                    "root_cause": (
                        "The exact-age contract is dual-handle. The left handle reached the target age, "
                        "but the right handle still remains at a different value, so the current filter "
                        "is still a range instead of an exact age."
                    ),
                    "recommended_solution_id": AGE_SLIDER_BOTH_HANDLES_SOLUTION_ID,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [
                        "detect_age_slider_handles",
                        "read_left_age_handle_value",
                        "read_right_age_handle_value",
                        "set_right_age_handle_to_target",
                        "validate_exact_age_range",
                        "record_issue",
                        "lookup_knowledge_base",
                    ],
                    "forbidden_actions": [
                        "click_confirm",
                        "click_view_result",
                        "enter_vehicle_list",
                        "collect_vehicle_data",
                        "skip_right_handle",
                        "treat_range_as_exact",
                        "skip_vehicle_year_secondary_check",
                    ],
                    "confidence": 0.99,
                    "evidence": {
                        **evidence,
                        "target_age": target_age,
                        "left_handle_value_after": left_after,
                        "right_handle_value_after": right_after,
                    },
                    "solution_record": self._build_age_slider_both_handles_solution(),
                }
            if clicked_target["text"] and target_age and clicked_target["text"] != target_age:
                return {
                    "issue_code": "AGE_SLIDER_WRONG_VALUE_SELECTED",
                    "root_cause": "The age slider action target does not match the exact task target age.",
                    "recommended_solution_id": YEAR_FILTER_EXACT_SLIDER_SOLUTION_ID,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "retry_slider_drag",
                        "click_unlimited_age",
                        "set_age_range",
                        "click_confirm",
                        "enter_vehicle_list",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.99,
                    "evidence": {**evidence, "target_age": target_age},
                    "solution_record": self._build_year_filter_exact_age_slider_solution(),
                }
            if actual_next_state in {"VEHICLE_DETAIL_PAGE", "VEHICLE_LIST_OR_DETAIL", "S07_VEHICLE_LIST_PAGE"}:
                return {
                    "issue_code": "WRONG_PAGE_AFTER_AGE_SLIDER_SET",
                    "root_cause": "Setting the exact age slider reached a vehicle list/detail or another unsafe page; collection must remain blocked.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "candidate",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_confirm",
                        "enter_vehicle_list",
                        "click_vehicle_card",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.96,
                    "evidence": {**evidence, "target_age": target_age},
                    "solution_record": None,
                }
            if actual_next_state not in {"S08_AGE_EXACT_SLIDER_PANEL", "S08_AGE_EXACT_VALUE_SELECTED"}:
                return {
                    "issue_code": "AGE_SLIDER_SET_NO_VERIFICATION",
                    "root_cause": "The exact age slider was set once, but the target value could not be verified.",
                    "recommended_solution_id": YEAR_FILTER_EXACT_SLIDER_SOLUTION_ID,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "retry_slider_drag",
                        "click_unlimited_age",
                        "set_age_range",
                        "click_confirm",
                        "enter_vehicle_list",
                        "collect_vehicle_data",
                    ],
                    "confidence": 0.94,
                    "evidence": {**evidence, "target_age": target_age},
                    "solution_record": self._build_year_filter_exact_age_slider_solution(),
                }

        if current_state == "S04" and intended_action == "click_series_model_button":
            if before["series_row_found"] and not before["model_button_found"]:
                return {
                    "issue_code": "SERIES_MODEL_BUTTON_NOT_FOUND",
                    "root_cause": "The target series row is visible on S04, but the matching right-side '\u8f66\u578b' button was not found.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": ["click_series_card", "click_series_name", "click_other_series"],
                    "confidence": 0.98,
                    "evidence": evidence,
                    "solution_record": None,
                }
            if actual_next_state in {"UNKNOWN_PAGE", "\u672a\u77e5", "\u672a\u77e5\u9875\u9762"}:
                candidate = self._build_unknown_candidate_solution(current_state, intended_action, expected_next_state)
                return {
                    "issue_code": "SERIES_CLICK_REVEALS_NEW_INTERMEDIATE_PAGE",
                    "root_cause": "The action exposed a page that is not yet covered by the current page contract.",
                    "recommended_solution_id": candidate["solution_id"],
                    "candidate_or_approved": "candidate",
                    "allowed_auto_actions": [],
                    "forbidden_actions": ["auto_retry_unknown_page"],
                    "confidence": 0.7,
                    "evidence": evidence,
                    "solution_record": candidate,
                }
            if actual_next_state == "VEHICLE_LIST_OR_DETAIL":
                return {
                    "issue_code": "WRONG_PAGE_AFTER_MODEL_BUTTON_CLICK",
                    "root_cause": "The verified '\u8f66\u578b' button click unexpectedly reached a vehicle list or detail page; collection must stay blocked.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "candidate",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "enter_vehicle_list",
                        "collect_vehicle_data",
                        "click_model_year",
                        "click_trim",
                        "click_confirm",
                    ],
                    "confidence": 0.95,
                    "evidence": evidence,
                    "solution_record": None,
                }
            if clicked_target["text"] == "\u8f66\u578b" and actual_next_state != expected_next_state:
                return {
                    "issue_code": "MODEL_BUTTON_CLICK_NO_NAVIGATION",
                    "root_cause": "The correct '\u8f66\u578b' button was clicked, but the page still did not enter S05.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "click_series_card",
                        "click_series_name",
                        "click_other_series",
                        "click_model_year",
                        "click_trim",
                        "click_confirm",
                    ],
                    "confidence": 0.96,
                    "evidence": evidence,
                    "solution_record": None,
                }

        if current_state == "S05" and intended_action == "tap_target_year":
            target_model_year = str(task_context.get("model_year") or task_context.get("target_model_year") or "").strip()
            if actual_next_state == "VEHICLE_LIST_OR_DETAIL":
                return {
                    "issue_code": "WRONG_PAGE_AFTER_MODEL_YEAR_CLICK",
                    "root_cause": "The target model-year click unexpectedly reached a vehicle list or detail page; collection must stay blocked.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "candidate",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "enter_vehicle_list",
                        "collect_vehicle_data",
                        "tap_exact_trim",
                        "tap_green_confirm",
                    ],
                    "confidence": 0.95,
                    "evidence": {**evidence, "target_model_year": target_model_year},
                    "solution_record": None,
                }
            if clicked_target["text"] == target_model_year and actual_next_state != expected_next_state:
                return {
                    "issue_code": "MODEL_YEAR_CLICK_NO_SELECTION",
                    "root_cause": "The exact target model year was clicked, but selected state or configuration-list refresh was not verified.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "tap_other_model_year",
                        "tap_exact_trim",
                        "tap_green_confirm",
                        "enter_vehicle_list",
                        "collect_vehicle_data",
                        "retry_same_wrong_click",
                    ],
                    "confidence": 0.94,
                    "evidence": {**evidence, "target_model_year": target_model_year},
                    "solution_record": None,
                }

        if current_state == "S05_MODEL_YEAR_SELECTED" and intended_action == "tap_exact_trim":
            target_trim = str(task_context.get("trim") or task_context.get("target_trim") or "").strip()
            clicked_trim_text = str(clicked_target.get("text") or "")
            trim_evidence = {
                "target_trim_raw": target_trim,
                "actual_clicked_trim_raw": clicked_trim_text,
                "target_trim_normalized": normalize_trim_for_match(target_trim),
                "actual_clicked_trim_normalized": normalize_trim_for_match(clicked_trim_text),
                "emission_normalization_used": emission_normalization_used(target_trim, clicked_trim_text),
            }
            if actual_next_state == "VEHICLE_LIST_OR_DETAIL":
                return {
                    "issue_code": "WRONG_PAGE_AFTER_TRIM_CLICK",
                    "root_cause": "The target trim click unexpectedly reached a vehicle list or detail page; collection must stay blocked.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "candidate",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "enter_vehicle_list",
                        "collect_vehicle_data",
                        "tap_green_confirm",
                    ],
                    "confidence": 0.95,
                    "evidence": {**evidence, "target_trim": target_trim, "trim_match": trim_evidence},
                    "solution_record": None,
                }
            if exact_trim_match_with_emission_normalization(target_trim, clicked_trim_text) and actual_next_state != expected_next_state:
                return {
                    "issue_code": "TRIM_CLICK_NO_SELECTION",
                    "root_cause": "The exact target trim was clicked, but selected state was not verified.",
                    "recommended_solution_id": None,
                    "candidate_or_approved": "approved",
                    "allowed_auto_actions": [],
                    "forbidden_actions": [
                        "tap_similar_trim",
                        "tap_partial_trim_match",
                        "tap_green_confirm",
                        "enter_vehicle_list",
                        "collect_vehicle_data",
                        "retry_same_wrong_click",
                    ],
                    "confidence": 0.94,
                    "evidence": {**evidence, "target_trim": target_trim, "trim_match": trim_evidence},
                    "solution_record": None,
                }

        return {
            "issue_code": "PAGE_CONTRACT_MISMATCH",
            "root_cause": "The observed transition does not satisfy the current page or action contract.",
            "recommended_solution_id": None,
            "candidate_or_approved": "candidate",
            "allowed_auto_actions": [],
            "forbidden_actions": [],
            "confidence": 0.5,
            "evidence": evidence,
            "solution_record": None,
        }

    @staticmethod
    def _is_age_slider_panel_misclassified_as_s07(
        *,
        current_state: str,
        intended_action: str,
        expected_next_state: str,
        actual_next_state: str,
        after_age_slider: dict[str, Any],
    ) -> bool:
        if current_state not in {"S08_YEAR_SELECTION_PANEL", "S08_AGE_EXACT_SLIDER_PANEL"}:
            return False
        if intended_action not in {"click_left_age_tab", "detect_age_exact_slider", "set_age_slider_exact_value"}:
            return False
        if expected_next_state not in {"S08_AGE_EXACT_SLIDER_PANEL", "S08_AGE_EXACT_VALUE_SELECTED"}:
            return False
        if actual_next_state != "S07_VEHICLE_LIST_PAGE":
            return False
        return bool(after_age_slider.get("age_slider_evidence_found"))

    def _is_series_action_target_mismatch(
        self,
        *,
        current_state: str,
        intended_action: str,
        expected_next_state: str,
        actual_next_state: str,
        clicked_target: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> bool:
        if current_state != "S04":
            return False
        if intended_action != "click_series_model_button":
            return False
        if expected_next_state not in {"S05", "S05_MODEL_YEAR_TRIM_PAGE", "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED"}:
            return False
        if actual_next_state == "S05_MODEL_YEAR_TRIM_PAGE_VERIFIED":
            return False
        if not before["series_row_found"]:
            return False
        if not (before["model_button_found"] or after["model_button_found"]):
            return False
        if clicked_target["text"] == "\u8f66\u578b" and clicked_target["role"] == "series_model_button":
            return False
        if clicked_target["role"] in {"series_card", "series_name"}:
            return True
        return clicked_target["text"] != "\u8f66\u578b"

    def _classify_contract_drift(
        self,
        *,
        current_state: str,
        intended_action: str,
        page_contract: dict[str, Any],
        action_contract: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any] | None:
        allowed = set(page_contract.get("allowed_actions", []))
        forbidden = set(page_contract.get("forbidden_actions", []))
        unique_required_action = next(iter(allowed)) if len(allowed) == 1 else None
        if current_state == "S07_VEHICLE_LIST_PAGE" and intended_action in {
            "click_generic_filter",
            "click_filter",
            "click_color_filter",
            "click_year_filter",
            "tap_color_filter",
            "tap_age_filter",
            "tap_sort_if_present",
            "tap_price_low_to_high",
            "click_comprehensive_sort",
            "click_price_low_to_high",
            "click_sort",
            "click_vehicle_card",
        }:
            return {
                "issue_code": "S07_CONTRACT_DRIFT_TO_GENERIC_FILTER",
                "root_cause": (
                    "S07 has exactly one allowed entry: the explicit '\u8f66\u578b\u914d\u7f6e' control. "
                    "Planning a generic filter/color/year/sort/card action would bypass the page contract."
                ),
                "recommended_solution_id": S07_CONTRACT_DRIFT_SOLUTION_ID,
                "candidate_or_approved": "approved",
                "allowed_auto_actions": [
                    "capture_screenshot",
                    "dump_ui_xml",
                    "recognize_s07_vehicle_list_page",
                    "detect_vehicle_model_config_entry",
                    "record_issue",
                    "lookup_knowledge_base",
                ],
                "forbidden_actions": [
                    "click_generic_filter",
                    "click_filter",
                    "click_color_filter",
                    "click_year_filter",
                    "click_sort",
                    "click_comprehensive_sort",
                    "click_price_low_to_high",
                    "click_vehicle_card",
                    "enter_detail_page",
                    "collect_vehicle_data",
                ],
                "confidence": 0.99,
                "evidence": {
                    **evidence,
                    "required_first_action": "detect_vehicle_model_config_entry",
                    "drifted_action": intended_action,
                },
                "solution_record": self._build_s07_contract_drift_solution(),
            }

        if current_state == "S08_YEAR_SELECTION_PANEL" and intended_action in {
            "read_year_selection_panel_contract",
            "click_unlimited_age",
            "click_age_option",
            "set_age_range",
            "expand_age_range",
            "drag_slider",
            "click_confirm",
            "click_view_result",
            "TARGET_YEAR_OPTION_NOT_FOUND",
        }:
            return {
                "issue_code": "S08_YEAR_CONTRACT_DRIFT_TO_OPTION_SCAN",
                "root_cause": (
                    "S08 year/age contract requires detecting or clicking only the left-side 车龄 tab first. "
                    "Scanning ordinary age options, using unlimited age, or treating the panel as an age-range selector is forbidden."
                ),
                "recommended_solution_id": S08_YEAR_CONTRACT_DRIFT_SOLUTION_ID,
                "candidate_or_approved": "approved",
                "allowed_auto_actions": [
                    "record_issue",
                    "lookup_knowledge_base",
                    "detect_left_age_tab",
                ],
                "forbidden_actions": [
                    "click_unlimited_age",
                    "click_age_option",
                    "set_age_range",
                    "expand_age_range",
                    "drag_slider",
                    "click_confirm",
                    "click_view_result",
                    "collect_vehicle_data",
                ],
                "confidence": 0.99,
                "evidence": {
                    **evidence,
                    "required_first_action": "detect_left_age_tab",
                    "drifted_action": intended_action,
                },
                "solution_record": self._build_s08_year_contract_drift_solution(),
            }

        if current_state == "S08_AGE_EXACT_SLIDER_PANEL" and intended_action in {
            "click_unlimited_age",
            "click_age_option",
            "set_age_range",
            "expand_age_range",
        }:
            return {
                "issue_code": "S08_YEAR_CONTRACT_DRIFT_TO_OPTION_SCAN",
                "root_cause": (
                    "S08 exact-age slider contract was drifted into ordinary age-option or age-range logic. "
                    "This panel must use an exact age slider, not option scanning or range expansion."
                ),
                "recommended_solution_id": S08_YEAR_CONTRACT_DRIFT_SOLUTION_ID,
                "candidate_or_approved": "approved",
                "allowed_auto_actions": [
                    "record_issue",
                    "lookup_knowledge_base",
                    "detect_age_exact_slider",
                    "read_age_slider_current_value",
                    "read_age_slider_bounds",
                    "calculate_target_age",
                ],
                "forbidden_actions": [
                    "click_unlimited_age",
                    "click_age_option",
                    "set_age_range",
                    "expand_age_range",
                    "collect_vehicle_data",
                ],
                "confidence": 0.99,
                "evidence": {
                    **evidence,
                    "required_first_action": "detect_age_exact_slider",
                    "drifted_action": intended_action,
                },
                "solution_record": self._build_s08_year_contract_drift_solution(),
            }

        if current_state == "S08_COLOR_MULTI_SELECTED" and intended_action in {
            "click_year_or_age_entry",
            "continue_to_age_slider",
            "click_confirm",
            "click_view_result",
            "enter_vehicle_list",
            "collect_vehicle_data",
            "click_target_color_option",
            "click_non_target_color",
        }:
            return {
                "issue_code": "COLOR_MULTI_SELECTED_TARGET_AND_STALE_COLOR",
                "root_cause": (
                    "S08 has multiple selected colors. The stale selected color must be canceled "
                    "and a single task-target color must be verified before any age, confirm, list, or collection action."
                ),
                "recommended_solution_id": COLOR_MULTI_SELECTED_SOLUTION_ID,
                "candidate_or_approved": "approved",
                "allowed_auto_actions": [
                    "capture_screenshot",
                    "dump_ui_xml",
                    "read_selected_colors",
                    "cancel_stale_selected_color",
                    "recapture_screenshot",
                    "redump_ui_xml",
                    "recognize_color_selected_single_target",
                    "record_issue",
                    "lookup_knowledge_base",
                ],
                "forbidden_actions": [
                    "continue_with_multiple_colors",
                    "continue_with_old_color",
                    "click_confirm",
                    "click_view_result",
                    "continue_to_age_slider",
                    "enter_vehicle_list",
                    "collect_vehicle_data",
                    "click_non_target_color",
                ],
                "confidence": 0.99,
                "evidence": {**evidence, "drifted_action": intended_action},
                "solution_record": self._build_color_multi_selected_solution(),
            }

        if unique_required_action and intended_action != unique_required_action:
            return {
                "issue_code": "CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH",
                "root_cause": "The current page has exactly one allowed action and the planned action does not match it.",
                "recommended_solution_id": None,
                "candidate_or_approved": "approved",
                "allowed_auto_actions": ["record_issue", "lookup_knowledge_base"],
                "forbidden_actions": sorted(forbidden | {intended_action}),
                "confidence": 0.98,
                "evidence": {
                    **evidence,
                    "planned_action": intended_action,
                    "allowed_actions": sorted(allowed),
                    "required_unique_action": unique_required_action,
                    "action_contract": action_contract,
                },
                "solution_record": None,
            }

        if allowed and intended_action not in allowed:
            return {
                "issue_code": "CONTRACT_DRIFT_OR_ACTION_PLANNING_MISMATCH",
                "root_cause": "The planned action is not in the current page contract allowed-actions list.",
                "recommended_solution_id": None,
                "candidate_or_approved": "approved",
                "allowed_auto_actions": ["record_issue", "lookup_knowledge_base"],
                "forbidden_actions": sorted(forbidden | {intended_action}),
                "confidence": 0.95,
                "evidence": {
                    **evidence,
                    "planned_action": intended_action,
                    "allowed_actions": sorted(allowed),
                    "action_contract": action_contract,
                },
                "solution_record": None,
            }
        return None

    def validate_series_model_click_target(
        self,
        *,
        task_context: dict[str, Any],
        actual_clicked_target: Any,
        before_xml: str,
    ) -> dict[str, Any]:
        target_series = str(task_context.get("series") or task_context.get("target_series") or "").strip()
        clicked_target = _normalize_click_target(actual_clicked_target)
        before = _series_contract_evidence(before_xml, target_series)
        same_row = clicked_target["series"] == target_series or _same_row_or_card(before["series_row_bounds"], clicked_target.get("bounds"))
        actual_target_is_model_button = clicked_target["text"] == "\u8f66\u578b" and clicked_target["role"] == "series_model_button"

        return {
            "target_series": target_series,
            "series_row_found": before["series_row_found"],
            "series_model_button_found": before["model_button_found"],
            "same_row_or_card": same_row,
            "actual_target_is_model_button": actual_target_is_model_button,
            "clicked_target": clicked_target,
            "before_contract_evidence": before,
        }

    @staticmethod
    def _build_series_action_target_mismatch_solution() -> dict[str, Any]:
        now = _now_iso()
        return {
            "solution_id": SERIES_ACTION_TARGET_MISMATCH_SOLUTION_ID,
            "issue_code": "SERIES_ACTION_TARGET_MISMATCH",
            "status": "approved",
            "symptoms": [
                "current page is S04_SERIES_LIST_PAGE",
                "target series row is visible",
                "model button is visible",
                "actual clicked target is not 车型",
                "page did not enter S05_MODEL_YEAR_TRIM_PAGE",
            ],
            "root_cause": (
                "The S04 contract requires clicking only the right-side '\u8f66\u578b' "
                "button inside the target series row. Clicking the series name or card "
                "breaks the contract and does not enter S05."
            ),
            "steps": [
                "On S04, never click the series name or the series card body.",
                "Locate the target series row from the verified task context.",
                "Locate the right-side '\u8f66\u578b' button inside that same row or card.",
                "Click only the '\u8f66\u578b' button.",
                "Recapture screenshot and UI XML after the click.",
                "Verify entry into S05_MODEL_YEAR_TRIM_PAGE.",
                "If the row exists but the button does not, raise SERIES_MODEL_BUTTON_NOT_FOUND.",
                "If the button click still does not enter S05, raise MODEL_BUTTON_CLICK_NO_NAVIGATION.",
            ],
            "allowed_auto_actions": [
                "capture_screenshot",
                "dump_ui_xml",
                "detect_target_series_row",
                "detect_series_model_button",
                "click_series_model_button",
                "recapture_screenshot",
                "redump_ui_xml",
                "recognize_model_year_trim_page",
                "record_issue",
                "lookup_knowledge_base",
            ],
            "manual_required_actions": [
                "Stop for manual confirmation if the target row is visible but the matching '\u8f66\u578b' button cannot be located.",
                "Stop for manual confirmation if clicking the verified '\u8f66\u578b' button still does not enter S05.",
            ],
            "forbidden_actions": [
                "click_series_card",
                "click_series_name",
                "click_other_series",
                "click_other_series_model_button",
                "click_model_year",
                "click_trim",
                "click_confirm",
                "enter_vehicle_list",
                "collect_vehicle_data",
                "retry_same_wrong_click",
            ],
            "max_auto_retries": 1,
            "approved": True,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _build_s07_contract_drift_solution() -> dict[str, Any]:
        now = _now_iso()
        return {
            "solution_id": S07_CONTRACT_DRIFT_SOLUTION_ID,
            "issue_code": "S07_CONTRACT_DRIFT_TO_GENERIC_FILTER",
            "status": "approved",
            "symptoms": [
                "current page is S07_VEHICLE_LIST_PAGE",
                "page contract allows only the \u8f66\u578b\u914d\u7f6e entry",
                "planned action is generic filter/color/year/sort/card instead of \u8f66\u578b\u914d\u7f6e",
                "no generic filter click has been performed",
            ],
            "root_cause": (
                "S07 page contract has exactly one allowed entry, \u8f66\u578b\u914d\u7f6e. "
                "Generalizing it into a filter/color/year/sort/card flow is state-machine planning drift."
            ),
            "steps": [
                "Capture screenshot and UI XML on S07.",
                "Recognize S07_VEHICLE_LIST_PAGE.",
                "Detect the explicit \u8f66\u578b\u914d\u7f6e entry only.",
                "Stop before clicking it unless the next turn explicitly authorizes that click.",
                "Do not click generic filter, color, year, sort, vehicle card, or collect vehicle data.",
            ],
            "allowed_auto_actions": [
                "capture_screenshot",
                "dump_ui_xml",
                "recognize_s07_vehicle_list_page",
                "detect_vehicle_model_config_entry",
                "record_issue",
                "lookup_knowledge_base",
            ],
            "manual_required_actions": [
                "Confirm page structure if \u8f66\u578b\u914d\u7f6e cannot be found.",
                "Explicitly authorize click_vehicle_model_config_entry in a later turn before clicking it.",
            ],
            "forbidden_actions": [
                "click_generic_filter",
                "click_filter",
                "click_color_filter",
                "click_year_filter",
                "click_sort",
                "click_comprehensive_sort",
                "click_price_low_to_high",
                "click_vehicle_card",
                "enter_detail_page",
                "collect_vehicle_data",
            ],
            "max_auto_retries": 0,
            "approved": True,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _build_s08_year_contract_drift_solution() -> dict[str, Any]:
        now = _now_iso()
        return {
            "solution_id": S08_YEAR_CONTRACT_DRIFT_SOLUTION_ID,
            "issue_code": "S08_YEAR_CONTRACT_DRIFT_TO_OPTION_SCAN",
            "status": "approved",
            "symptoms": [
                "current page is S08_YEAR_SELECTION_PANEL or S08_AGE_EXACT_SLIDER_PANEL",
                "year/age contract was generalized into ordinary age options or age ranges",
                "left-side 车龄 tab or exact slider contract was bypassed before execution",
            ],
            "root_cause": (
                "S08 year filtering uses an exact age slider. Treating it as ordinary age options, unlimited age, "
                "or age-range scanning is contract drift."
            ),
            "steps": [
                "On S08_YEAR_SELECTION_PANEL, detect or click only the left-side 车龄 tab first.",
                "Do not click 不限车龄 or ordinary age options.",
                "After 车龄 is selected, verify the right-side exact age slider and track.",
                "Do not set a range and do not expand age coverage.",
                "Stop until exact slider handling is explicitly authorized.",
            ],
            "allowed_auto_actions": [
                "record_issue",
                "lookup_knowledge_base",
                "detect_left_age_tab",
                "detect_age_exact_slider",
                "read_age_slider_current_value",
                "read_age_slider_bounds",
                "calculate_target_age",
            ],
            "manual_required_actions": [
                "If the left-side 车龄 tab or exact age slider cannot be verified, stop for manual review.",
            ],
            "forbidden_actions": [
                "click_unlimited_age",
                "click_age_option",
                "set_age_range",
                "expand_age_range",
                "drag_slider",
                "click_confirm",
                "click_view_result",
                "collect_vehicle_data",
            ],
            "max_auto_retries": 0,
            "approved": True,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _build_year_filter_exact_age_slider_solution() -> dict[str, Any]:
        now = _now_iso()
        return {
            "solution_id": YEAR_FILTER_EXACT_SLIDER_SOLUTION_ID,
            "issue_code": "YEAR_FILTER_USES_EXACT_AGE_SLIDER",
            "status": "approved",
            "symptoms": [
                "target task has registration_date_raw and vehicle_year",
                "APP year/age panel uses an exact dual-handle age slider",
                "three-same matching still needs exact year control",
            ],
            "root_cause": "The APP expresses year filtering through a dual-handle exact age slider instead of a direct vehicle-year option.",
            "steps": [
                "Calculate target_age from registration_date_raw, vehicle_year, and current date.",
                "Click the left-side 车龄 tab.",
                "Detect the exact dual-handle age slider and track on the right side.",
                "Read both left_handle_value and right_handle_value.",
                "Set the left handle to target_age only when needed.",
                "Set the right handle to target_age only when needed.",
                "Verify left_handle_value == right_handle_value == target_age and both handle centers physically overlap on the target tick.",
                "Keep requires_vehicle_year_secondary_check=true and re-check vehicle_year on the list page.",
            ],
                "allowed_auto_actions": [
                    "detect_left_age_tab",
                    "click_left_age_tab",
                    "detect_age_exact_slider",
                    "detect_age_slider_handles",
                    "read_left_age_handle_value",
                    "read_right_age_handle_value",
                    "read_age_slider_bounds",
                    "calculate_target_age",
                    "set_age_slider_exact_value",
                    "set_left_age_handle_to_target",
                    "detect_right_age_handle_visual_center",
                    "calculate_age_handle_overlap_target",
                    "set_right_age_handle_to_target",
                    "validate_age_handle_physical_overlap",
                    "validate_exact_age_range",
                    "record_issue",
                    "lookup_knowledge_base",
            ],
            "manual_required_actions": [
                "If the dual-handle slider, the right visual handle center, or the physical overlap cannot be verified, stop for manual intervention.",
            ],
            "forbidden_actions": [
                "click_unlimited_age",
                "click_age_option",
                "set_age_range",
                "expand_age_range",
                "skip_right_handle_verification",
                "treat_range_as_exact",
                "treat_value_only_as_exact_without_physical_overlap",
                "skip_vehicle_year_secondary_check",
                "collect_without_year_check",
                "enter_detail_before_sort",
                "collect_vehicle_data",
            ],
            "max_auto_retries": 1,
            "approved": True,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _build_age_slider_both_handles_solution() -> dict[str, Any]:
        now = _now_iso()
        return {
            "solution_id": AGE_SLIDER_BOTH_HANDLES_SOLUTION_ID,
            "issue_code": "AGE_SLIDER_ONLY_LEFT_HANDLE_SET",
            "status": "approved",
            "symptoms": [
                "target age is 6",
                "left handle is already set to 6",
                "right handle is not yet 6",
                "the current age filter is still a range instead of an exact value",
            ],
            "root_cause": (
                "The exact-age panel uses a dual-handle slider. Exact success requires both "
                "handles to equal the same target age. Leaving the right handle at another "
                "value keeps the filter in a range state, not an exact single value."
            ),
            "steps": [
                "Detect both slider handles.",
                "Read left_handle_value and right_handle_value.",
                "Keep the left handle at target_age when it is already correct.",
                "Move only the right handle to target_age.",
                "Recapture screenshot and XML.",
                "Verify left_handle_value == right_handle_value == target_age.",
                "Keep requires_vehicle_year_secondary_check=true.",
                "Do not click confirm or enter the vehicle list until exact validation succeeds.",
            ],
            "allowed_auto_actions": [
                "detect_age_slider_handles",
                "read_left_age_handle_value",
                "read_right_age_handle_value",
                "set_right_age_handle_to_target",
                "validate_exact_age_range",
                "record_issue",
                "lookup_knowledge_base",
            ],
            "manual_required_actions": [
                "If the right handle cannot be safely distinguished or cannot be set exactly to target_age, stop for manual review.",
            ],
            "forbidden_actions": [
                "click_confirm",
                "click_view_result",
                "enter_vehicle_list",
                "collect_vehicle_data",
                "skip_right_handle",
                "treat_range_as_exact",
                "skip_vehicle_year_secondary_check",
            ],
            "max_auto_retries": 1,
            "approved": True,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _build_task_color_changed_solution() -> dict[str, Any]:
        now = _now_iso()
        return {
            "solution_id": TASK_COLOR_CHANGED_SOLUTION_ID,
            "issue_code": "TASK_COLOR_CHANGED_INVALIDATES_SELECTED_COLOR",
            "status": "approved",
            "symptoms": [
                "task color changed from a previously selected color to a new target color",
                "current panel may still show the old color selected",
                "continuing the flow would violate three-same color matching",
            ],
            "root_cause": "Task color changed after the mobile flow had already selected a different color, so downstream filtering would use stale color state.",
            "steps": [
                "Block year/age, confirm, and collection actions.",
                "Re-enter color selection.",
                "Select the current task target color.",
                "Verify the new target color is selected before continuing.",
            ],
            "allowed_auto_actions": [],
            "manual_required_actions": [
                "Reopen color selection and verify the current task color before continuing age or result actions.",
            ],
            "forbidden_actions": [
                "continue_with_old_color",
                "click_confirm",
                "enter_vehicle_list",
                "collect_vehicle_data",
                "skip_color_revalidation",
            ],
            "max_auto_retries": 0,
            "approved": True,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _build_color_multi_selected_solution() -> dict[str, Any]:
        now = _now_iso()
        return {
            "solution_id": COLOR_MULTI_SELECTED_SOLUTION_ID,
            "issue_code": "COLOR_MULTI_SELECTED_TARGET_AND_STALE_COLOR",
            "status": "approved",
            "symptoms": [
                "task target color is selected",
                "a stale old color is also selected",
                "multiple selected colors violate strict same-color matching",
            ],
            "root_cause": (
                "The mobile S08 color panel retained an old selected color alongside the current task "
                "target color, so downstream filtering would include an invalid color."
            ),
            "steps": [
                "Read selected_colors.",
                "Confirm the task target color and stale color are both selected.",
                "Click only the stale selected color once to cancel it.",
                "Verify selected_colors contains only the task target color.",
                "Block age, confirm, list entry, and collection until single-target color is verified.",
            ],
            "allowed_auto_actions": [
                "capture_screenshot",
                "dump_ui_xml",
                "read_selected_colors",
                "cancel_stale_selected_color",
                "recapture_screenshot",
                "redump_ui_xml",
                "recognize_color_selected_single_target",
                "record_issue",
                "lookup_knowledge_base",
            ],
            "manual_required_actions": [
                "If stale color cannot be safely located, stop for manual review.",
                "If target color is lost after canceling stale color, stop for manual review.",
            ],
            "forbidden_actions": [
                "continue_with_multiple_colors",
                "continue_with_old_color",
                "click_confirm",
                "click_view_result",
                "continue_to_age_slider",
                "enter_vehicle_list",
                "collect_vehicle_data",
                "click_non_target_color",
            ],
            "max_auto_retries": 1,
            "approved": True,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _build_unknown_candidate_solution(current_state: str, intended_action: str, expected_next_state: str) -> dict[str, Any]:
        now = _now_iso()
        return {
            "solution_id": f"CAND-{current_state}-{intended_action}-{expected_next_state}",
            "issue_code": "SERIES_CLICK_REVEALS_NEW_INTERMEDIATE_PAGE",
            "status": "candidate",
            "symptoms": [
                current_state,
                intended_action,
                expected_next_state,
                "unknown intermediate page",
            ],
            "root_cause": "A new intermediate page appeared and is not yet covered by the current contract.",
            "steps": [
                "Capture screenshot and UI XML.",
                "Stop and request manual review.",
                "Do not auto-call recovery until the new page contract is reviewed and approved.",
            ],
            "allowed_auto_actions": [],
            "manual_required_actions": ["Review the new page and extend page/action contracts if appropriate."],
            "forbidden_actions": ["auto_retry_unknown_page"],
            "max_auto_retries": 1,
            "approved": False,
            "created_at": now,
            "updated_at": now,
        }
