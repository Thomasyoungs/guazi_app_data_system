"""Pure S12/S13 transition gates.

Transition gates decide whether a transition is allowed and which stop code
applies. They do not collect fields, click, scroll, or send feedback.
"""

from __future__ import annotations

from typing import Any

from . import issue_codes


def gate_s12_to_s13(proof: dict[str, Any]) -> dict[str, Any]:
    allowed = bool(proof.get("s12_to_s13_transition_allowed"))
    return {
        "transition": "S12_TO_S13",
        "allowed": allowed,
        "stop_code": "" if allowed else s12_to_s13_proof_stop_code(proof),
        "proof": proof,
    }


def s12_to_s13_proof_stop_code(proof: dict[str, Any]) -> str:
    if proof.get("body_appearance_detection_items_present") or proof.get("body_appearance_text_present"):
        if not proof.get("s12_to_s13_region_tabs_seen"):
            return issue_codes.S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED
        return issue_codes.S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED_AFTER_S12_BODY_REACHED
    return issue_codes.S12_TO_S13_REGION_PROOF_NOT_CONFIRMED


def gate_s13_to_s14(proof: dict[str, Any]) -> dict[str, Any]:
    allowed = bool(proof.get("s13_history_table_detected") or proof.get("s13_region_history_count_bindings"))
    return {
        "transition": "S13_TO_S14",
        "allowed": allowed,
        "stop_code": "" if allowed else issue_codes.S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED,
        "proof": proof,
    }


def gate_s13_all_zero_to_s15(region_counts: dict[str, Any]) -> dict[str, Any]:
    values = [value for value in region_counts.values() if value is not None]
    allowed = bool(values) and all(value == 0 for value in values)
    return {
        "transition": "S13_ALL_ZERO_TO_S15",
        "allowed": allowed,
        "stop_code": "" if allowed else issue_codes.S13_REGION_HISTORY_COUNT_BINDING_FAILED,
        "region_counts": region_counts,
    }

