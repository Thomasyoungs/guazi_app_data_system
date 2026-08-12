"""Pure page-proof helpers for S12/S13.

This module only evaluates evidence already captured by the runtime. It must
not perform clicks, swipes, fresh captures, feedback sends, task-state writes,
or pricing decisions.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable


NodeLabelsFn = Callable[[dict[str, Any]], list[str]]
ValidBoundsFn = Callable[[Any], bool]


def prove_s12_body_appearance_reached(
    snapshot: dict[str, Any],
    *,
    nodes: Iterable[dict[str, Any]],
    node_labels: NodeLabelsFn,
    valid_bounds: ValidBoundsFn,
    region_order: Iterable[str],
    visible_blob: str,
    has_body_appearance_text: bool,
    history_arrival_reason: Any = None,
) -> dict[str, Any]:
    body_nodes: list[dict[str, Any]] = []
    selected_body_nodes: list[dict[str, Any]] = []
    region_tab_bounds: dict[str, Any] = {}
    detection_item_labels: list[str] = []
    body_detection_tokens = (
        "车身外观良好",
        "外观漆面检测",
        "外观漆面检测视频",
        "深度检测",
        "检测通过",
        "历史修复",
        "注意事项",
    )
    regions = list(region_order)
    for node in nodes:
        bounds = node.get("bounds")
        labels = node_labels(node)
        if any(label == "车身外观" or label.startswith("车身外观") for label in labels):
            body_nodes.append({"labels": labels, "bounds": bounds, "selected": bool(node.get("selected"))})
            if bool(node.get("selected")) and valid_bounds(bounds):
                selected_body_nodes.append({"labels": labels, "bounds": bounds})
        for region in regions:
            if region in labels and valid_bounds(bounds):
                region_tab_bounds[region] = bounds
        if any(token in label for label in labels for token in body_detection_tokens):
            detection_item_labels.extend(labels)

    for token in body_detection_tokens:
        if token in visible_blob:
            detection_item_labels.append(token)

    s13_region_tabs_present = all(region in region_tab_bounds for region in regions)
    body_appearance_detection_items_present = bool(detection_item_labels)
    body_appearance_section_reached = bool(
        has_body_appearance_text
        and (s13_region_tabs_present or history_arrival_reason)
    )
    return {
        "body_appearance_text_present": bool(body_nodes or "车身外观" in visible_blob),
        "body_appearance_nodes": body_nodes,
        "body_appearance_tab_selected": bool(selected_body_nodes),
        "selected_body_appearance_nodes": selected_body_nodes,
        "s13_region_tabs_present": s13_region_tabs_present,
        "s13_region_tab_bounds": {key: list(value) for key, value in region_tab_bounds.items()},
        "body_appearance_detection_items_present": body_appearance_detection_items_present,
        "body_appearance_detection_item_labels": sorted(set(detection_item_labels))[:20],
        "body_appearance_section_reached": body_appearance_section_reached,
        "history_arrival_reason": history_arrival_reason,
    }


def prove_s12_to_s13_region_history(
    snapshot: dict[str, Any],
    *,
    progress: dict[str, Any],
    history_table_seen: bool,
    bindings: dict[str, Any] | None,
    recognized_page: str = "",
) -> dict[str, Any]:
    if not isinstance(bindings, dict):
        bindings = {}
    binding_any = any(value is not None for value in bindings.values())
    all_region_tabs_seen = bool(progress.get("s13_region_tabs_present"))
    proof_confirmed = bool(all_region_tabs_seen or history_table_seen or binding_any)
    if proof_confirmed:
        failure_reason = ""
    elif progress.get("body_appearance_detection_items_present"):
        failure_reason = "body_appearance_items_seen_without_region_tabs_or_history_table"
    elif progress.get("body_appearance_text_present"):
        failure_reason = "body_appearance_text_seen_without_s13_region_proof"
    else:
        failure_reason = "s12_to_s13_region_proof_missing"
    return {
        **progress,
        "s12_to_s13_region_tabs_seen": all_region_tabs_seen,
        "s12_to_s13_region_tabs_bounds": progress.get("s13_region_tab_bounds") or {},
        "s12_to_s13_history_table_seen": bool(history_table_seen),
        "s12_to_s13_history_table_bounds": snapshot.get("s13_history_table_bounds") or {},
        "s12_to_s13_repair_count_bindings": bindings,
        "s12_to_s13_repair_count_binding_any": bool(binding_any),
        "s12_to_s13_proof_confirmed": proof_confirmed,
        "s12_to_s13_proof_failure_reason": failure_reason,
        "s12_to_s13_transition_allowed": proof_confirmed,
        "s13_history_table_detection_debug": snapshot.get("s13_history_table_detection_debug") or {},
        "recognized_page": recognized_page,
        "screenshot_path": str(snapshot.get("screenshot_path") or ""),
        "xml_path": str(snapshot.get("xml_path") or ""),
    }


def prove_s13_history_table(snapshot: dict[str, Any]) -> bool:
    return bool(snapshot.get("s13_history_table_detected") or snapshot.get("s13_region_history_count_bindings"))


def prove_s13_region_tabs(snapshot: dict[str, Any], *, region_order: Iterable[str]) -> bool:
    text = "\n".join(
        [
            str(snapshot.get("visible_blob") or ""),
            "\n".join(str(item) for item in snapshot.get("visible_texts") or []),
        ]
    )
    return all(region in text for region in region_order)

