"""Transient Guazi popup detection and safe dismiss helpers.

The helpers in this module are intentionally small and side-effect free except
for the injected click/capture callbacks. Runtime code can use them without
loosening page-contract checks or pricing rules.
"""

from __future__ import annotations

import time
from typing import Any, Callable


GUAZI_PUSH_NOTIFICATION_POPUP = "GUAZI_PUSH_NOTIFICATION_POPUP"
GUAZI_TRANSIENT_POPUP_BLOCKED_FLOW = "GUAZI_TRANSIENT_POPUP_BLOCKED_FLOW"
GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND = "GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND"
GUAZI_PUSH_POPUP_CLOSE_FAILED = "GUAZI_PUSH_POPUP_CLOSE_FAILED"

GUAZI_PACKAGE_NAMES = {
    "com.ganji.android.haoche_c",
    "com.guazi.android",
}

PUSH_POPUP_TITLE = "开启消息推送通知"
PUSH_POPUP_SUBTITLE = "选择想接收的通知类型"
PUSH_POPUP_ENABLE_NOW = "立即开启"
PUSH_POPUP_OPTIONS = (
    "车源降价时通知我",
    "领取免费检测报告",
    "有同款新上车源通知我",
)
PUSH_POPUP_CLOSE_LABELS = {"×", "X", "x", "关闭", "关闭弹窗"}
PUSH_POPUP_FORBIDDEN_LABELS = {
    PUSH_POPUP_ENABLE_NOW,
    *PUSH_POPUP_OPTIONS,
    "找顾问解读报告",
    "联系顾问",
    "联系卖家",
    "讲价",
    "立即订购",
    "顾问在线",
    "现在看",
}
MAX_PUSH_POPUP_CLOSE_ATTEMPTS = 2


def _normalize_label(value: Any) -> str:
    return str(value or "").strip()


def _node_labels(node: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in ("text", "content_desc", "contentDescription", "content-desc", "label"):
        value = _normalize_label(node.get(key))
        if value:
            labels.append(value)
    for value in node.get("labels") or []:
        label = _normalize_label(value)
        if label:
            labels.append(label)
    return labels


def _all_visible_texts(snapshot: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for value in snapshot.get("visible_texts") or []:
        label = _normalize_label(value)
        if label:
            texts.append(label)
    for node in snapshot.get("nodes") or []:
        texts.extend(_node_labels(node))
    visible_blob = _normalize_label(snapshot.get("visible_blob"))
    if visible_blob:
        texts.append(visible_blob)
    return texts


def _text_contains(texts: list[str], expected: str) -> bool:
    return any(expected == text or expected in text for text in texts)


def _snapshot_foreground_is_guazi(snapshot: dict[str, Any]) -> bool:
    package_values = {
        _normalize_label(snapshot.get("foreground_package")),
        _normalize_label(snapshot.get("xml_package")),
        _normalize_label(snapshot.get("package")),
    }
    for node in snapshot.get("nodes") or []:
        package_values.add(_normalize_label(node.get("package")))
    return any(value in GUAZI_PACKAGE_NAMES for value in package_values if value)


def _coerce_bounds(value: Any) -> tuple[int, int, int, int] | None:
    if isinstance(value, str):
        cleaned = value.replace("[", "").replace("]", ",").replace(" ", "")
        parts = [part for part in cleaned.split(",") if part]
        if len(parts) >= 4:
            try:
                nums = [int(float(part)) for part in parts[:4]]
            except ValueError:
                return None
            return _valid_bounds(nums)
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            nums = [int(float(part)) for part in value[:4]]
        except (TypeError, ValueError):
            return None
        return _valid_bounds(nums)
    return None


def _valid_bounds(nums: list[int]) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = nums
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _bounds_union(bounds_list: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    if not bounds_list:
        return None
    return (
        min(item[0] for item in bounds_list),
        min(item[1] for item in bounds_list),
        max(item[2] for item in bounds_list),
        max(item[3] for item in bounds_list),
    )


def _center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    return ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)


def detect_guazi_push_notification_popup(
    snapshot: dict[str, Any],
    *,
    current_stage: str | None = None,
    current_reference_index: int | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Detect the Guazi push-notification popup from current snapshot evidence."""

    texts = _all_visible_texts(snapshot)
    title = _text_contains(texts, PUSH_POPUP_TITLE)
    subtitle = _text_contains(texts, PUSH_POPUP_SUBTITLE)
    enable_now = _text_contains(texts, PUSH_POPUP_ENABLE_NOW)
    option_hits = [option for option in PUSH_POPUP_OPTIONS if _text_contains(texts, option)]
    guazi_foreground = _snapshot_foreground_is_guazi(snapshot)
    matched_rule = ""
    detected = False
    if guazi_foreground and title and subtitle:
        detected = True
        matched_rule = "title_and_subtitle"
    elif guazi_foreground and len(option_hits) >= 3:
        detected = True
        matched_rule = "three_options"
    elif guazi_foreground and title and enable_now:
        detected = True
        matched_rule = "title_and_enable_now"

    popup_texts = [
        text
        for text in [PUSH_POPUP_TITLE, PUSH_POPUP_SUBTITLE, *PUSH_POPUP_OPTIONS, PUSH_POPUP_ENABLE_NOW]
        if _text_contains(texts, text)
    ]
    return {
        "popup_detected": detected,
        "popup_type": GUAZI_PUSH_NOTIFICATION_POPUP if detected else "",
        "matched_rule": matched_rule,
        "popup_texts": popup_texts,
        "guazi_foreground": guazi_foreground,
        "underlying_page_candidate": snapshot.get("underlying_page_candidate")
        or snapshot.get("recognized_page")
        or current_stage
        or "",
        "current_stage": current_stage or "",
        "current_reference_index": current_reference_index,
        "task_id": task_id or snapshot.get("task_id") or "",
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "xml_path": str(snapshot.get("xml_path") or ""),
    }


def _popup_content_bounds(snapshot: dict[str, Any]) -> tuple[int, int, int, int] | None:
    content_bounds: list[tuple[int, int, int, int]] = []
    popup_terms = {PUSH_POPUP_TITLE, PUSH_POPUP_SUBTITLE, PUSH_POPUP_ENABLE_NOW, *PUSH_POPUP_OPTIONS}
    for node in snapshot.get("nodes") or []:
        labels = _node_labels(node)
        if not any(any(term in label for term in popup_terms) for label in labels):
            continue
        bounds = _coerce_bounds(node.get("bounds"))
        if bounds:
            content_bounds.append(bounds)
    return _bounds_union(content_bounds)


def _in_popup_top_right(
    bounds: tuple[int, int, int, int],
    popup_bounds: tuple[int, int, int, int] | None,
    snapshot: dict[str, Any],
) -> bool:
    cx, cy = _center(bounds)
    if popup_bounds:
        x1, y1, x2, y2 = popup_bounds
        width = x2 - x1
        height = y2 - y1
        return cx >= x2 - max(80, int(width * 0.25)) and cy <= y1 + max(120, int(height * 0.35))
    screen_width = int(snapshot.get("screen_width") or snapshot.get("width") or 1080)
    screen_height = int(snapshot.get("screen_height") or snapshot.get("height") or 2400)
    return cx >= int(screen_width * 0.70) and cy <= int(screen_height * 0.35)


def find_guazi_push_popup_close_target(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Bind the popup close X target without falling back to fixed coordinates."""

    detection = detect_guazi_push_notification_popup(snapshot)
    if not detection.get("popup_detected"):
        return {"target_found": False, "reason": "popup_not_detected", "detection": detection}

    popup_bounds = _popup_content_bounds(snapshot)
    exact_candidates: list[dict[str, Any]] = []
    structural_candidates: list[dict[str, Any]] = []
    for node in snapshot.get("nodes") or []:
        labels = _node_labels(node)
        bounds = _coerce_bounds(node.get("bounds"))
        if not bounds:
            continue
        label_match = any(label in PUSH_POPUP_CLOSE_LABELS for label in labels)
        role_match = _normalize_label(node.get("role")) in {"close_button", "popup_close"}
        desc_match = any("关闭" in label and "弹窗" in label for label in labels)
        if not (label_match or role_match or desc_match):
            continue
        if not _in_popup_top_right(bounds, popup_bounds, snapshot):
            continue
        item = {"node": node, "bounds": bounds, "labels": labels}
        if label_match and node.get("clickable") is True and node.get("enabled", True) is not False:
            exact_candidates.append(item)
        elif role_match or desc_match:
            structural_candidates.append(item)

    candidates = exact_candidates or structural_candidates
    if len(candidates) != 1:
        return {
            "target_found": False,
            "reason": "close_target_not_unique" if candidates else "close_target_missing",
            "candidate_count": len(candidates),
            "detection": detection,
            "popup_bounds": popup_bounds,
        }

    selected = candidates[0]
    point = _center(selected["bounds"])
    return {
        "target_found": True,
        "popup_type": GUAZI_PUSH_NOTIFICATION_POPUP,
        "click_source": "xml_close_text_bounds" if exact_candidates else "popup_container_close_button_bounds",
        "click_bounds": list(selected["bounds"]),
        "click_point": list(point),
        "click_point_inside_bounds": True,
        "allowed_action": "CLICK_POPUP_CLOSE_X",
        "forbidden_actions": sorted(PUSH_POPUP_FORBIDDEN_LABELS),
        "matched_close_labels": selected["labels"],
        "popup_bounds": list(popup_bounds) if popup_bounds else None,
        "detection": detection,
    }


def is_guazi_push_popup_forbidden_action(label: str) -> bool:
    return _normalize_label(label) in PUSH_POPUP_FORBIDDEN_LABELS


def close_guazi_push_popup_from_snapshot(
    context: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    capture_func: Callable[[str], dict[str, Any]],
    recognize_func: Callable[[dict[str, Any]], str | None],
    current_stage: str,
    capture_stem: str,
    task_id: str | None = None,
    current_reference_index: int | None = None,
    click_func: Callable[[int, int], Any] | None = None,
    sleep_func: Callable[[float], Any] = time.sleep,
    max_attempts: int = MAX_PUSH_POPUP_CLOSE_ATTEMPTS,
) -> dict[str, Any]:
    """Close the push popup and return fresh-page resume evidence.

    The injected functions keep runtime side effects explicit and make tests
    fully offline. No fallback coordinate is generated when a close target
    cannot be bound.
    """

    detection = detect_guazi_push_notification_popup(
        snapshot,
        current_stage=current_stage,
        current_reference_index=current_reference_index,
        task_id=task_id,
    )
    if not detection.get("popup_detected"):
        return {
            "popup_detected": False,
            "popup_closed": False,
            "resume_success": False,
            "popup_close_target_found": False,
            "popup_close_target_bounds": None,
            "popup_close_attempted": False,
            "popup_close_verified": False,
            "popup_guard_recaptured": False,
            "popup_guard_resume_stage": current_stage,
            "popup_guard_blocked_underlying_click": False,
            "popup_detected_after_close": False,
            "popup_guard_failure_stop_code": "",
        }

    task_id = str(task_id or detection.get("task_id") or context.get("task_id") or "")
    signature = "|".join(
        [
            GUAZI_PUSH_NOTIFICATION_POPUP,
            current_stage,
            str(current_reference_index or ""),
            str(snapshot.get("xml_path") or snapshot.get("screenshot_path") or ""),
        ]
    )
    attempts_by_signature = context.setdefault("guazi_push_popup_close_attempts", {})
    prior_attempts = int(attempts_by_signature.get(signature) or 0)
    attempts: list[dict[str, Any]] = []
    current_snapshot = snapshot
    recognizer_stage = current_stage
    click_func = click_func or getattr(context.get("client"), "tap", None)
    if click_func is None:
        return {
            **detection,
            "popup_closed": False,
            "resume_success": False,
            "popup_close_target_found": False,
            "popup_close_target_bounds": None,
            "popup_close_attempted": False,
            "popup_close_verified": False,
            "popup_guard_recaptured": False,
            "popup_guard_resume_stage": current_stage,
            "popup_guard_blocked_underlying_click": True,
            "popup_detected_after_close": True,
            "popup_guard_failure_stop_code": GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND,
            "popup_close_attempt_count": prior_attempts,
            "popup_close_result": "click_func_missing",
            "stop_code": GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND,
        }

    for attempt_index in range(prior_attempts + 1, max_attempts + 1):
        target = find_guazi_push_popup_close_target(current_snapshot)
        if not target.get("target_found"):
            attempts_by_signature[signature] = attempt_index - 1
            return {
                **detection,
                "popup_closed": False,
                "resume_success": False,
                "popup_close_target_found": False,
                "popup_close_target_bounds": None,
                "popup_close_attempted": False,
                "popup_close_verified": False,
                "popup_guard_recaptured": False,
                "popup_guard_resume_stage": recognizer_stage,
                "popup_guard_blocked_underlying_click": True,
                "popup_detected_after_close": True,
                "popup_guard_failure_stop_code": GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND,
                "popup_close_attempt_count": attempt_index - 1,
                "popup_close_result": "close_target_not_found",
                "close_target_evidence": target,
                "stop_code": GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND,
            }

        x, y = [int(value) for value in target["click_point"][:2]]
        click_result = click_func(x, y)
        attempts_by_signature[signature] = attempt_index
        sleep_func(0.3)
        fresh_snapshot = capture_func(f"{capture_stem}_push_popup_closed_attempt_{attempt_index}")
        fresh_stage = recognize_func(fresh_snapshot) or recognizer_stage
        still_detected = detect_guazi_push_notification_popup(
            fresh_snapshot,
            current_stage=fresh_stage,
            current_reference_index=current_reference_index,
            task_id=task_id,
        )
        attempt_evidence = {
            "attempt_index": attempt_index,
            "click_point": target.get("click_point"),
            "click_bounds": target.get("click_bounds"),
            "click_source": target.get("click_source"),
            "click_result": click_result,
            "fresh_screenshot_path": str(fresh_snapshot.get("screenshot_path") or ""),
            "fresh_xml_path": str(fresh_snapshot.get("xml_path") or ""),
            "fresh_stage": fresh_stage,
            "popup_still_detected": bool(still_detected.get("popup_detected")),
        }
        attempts.append(attempt_evidence)
        if not still_detected.get("popup_detected"):
            result = {
                **detection,
                "popup_closed": True,
                "resume_stage": fresh_stage,
                "popup_close_target_found": True,
                "popup_close_target_bounds": target.get("click_bounds"),
                "popup_close_attempted": True,
                "popup_close_verified": True,
                "popup_guard_recaptured": True,
                "popup_guard_resume_stage": fresh_stage,
                "popup_guard_blocked_underlying_click": True,
                "popup_detected_after_close": False,
                "popup_guard_failure_stop_code": "",
                "resume_action": "fresh_recapture_and_reidentify",
                "resume_success": True,
                "popup_close_attempt_count": attempt_index,
                "popup_close_result": "closed_and_resumed",
                "close_attempts": attempts,
                "fresh_snapshot": fresh_snapshot,
                "fresh_recognized_stage": fresh_stage,
                "stop_code": "",
            }
            context.setdefault("guazi_transient_popup_events", []).append(_event_without_snapshot(result))
            return result
        current_snapshot = fresh_snapshot
        recognizer_stage = fresh_stage

    result = {
        **detection,
        "popup_closed": False,
        "resume_stage": recognizer_stage,
        "popup_close_target_found": bool(attempts),
        "popup_close_target_bounds": attempts[-1].get("click_bounds") if attempts else None,
        "popup_close_attempted": bool(attempts),
        "popup_close_verified": False,
        "popup_guard_recaptured": bool(attempts),
        "popup_guard_resume_stage": recognizer_stage,
        "popup_guard_blocked_underlying_click": True,
        "popup_detected_after_close": True,
        "popup_guard_failure_stop_code": GUAZI_PUSH_POPUP_CLOSE_FAILED,
        "resume_action": "fresh_recapture_and_reidentify",
        "resume_success": False,
        "popup_close_attempt_count": max_attempts,
        "popup_close_result": "popup_still_present_after_max_attempts",
        "close_attempts": attempts,
        "fresh_snapshot": current_snapshot,
        "stop_code": GUAZI_PUSH_POPUP_CLOSE_FAILED,
    }
    context.setdefault("guazi_transient_popup_events", []).append(_event_without_snapshot(result))
    return result


def _event_without_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    event = dict(result)
    event.pop("fresh_snapshot", None)
    return event


def format_guazi_push_popup_failure_feedback(task_id: str) -> str:
    return "\n".join(
        [
            f"【本次定价未完成】{task_id}",
            "",
            "原因：瓜子 APP 弹出消息推送通知弹窗，系统未能安全关闭该弹窗。",
            "需要处理：请管理员检查手机页面后重新发起任务。",
        ]
    )
