"""Source-backed V3.3 reference low-score skip decision helpers.

The rule is intentionally conservative: a partially collected reference can
only be skipped when its deterministic score upper bound is still below the
target score.  The skipped reference is never allowed into S16 pricing; if it
later becomes the boundary-previous final candidate, runtime must recollect it
through the full S11-S14 path before pricing.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


EARLY_EXIT_RULE_ID = "V33_S14_LOW_SCORE_UPPER_BOUND_SKIP_AND_RECOLLECT_CONTRACT"
EXPECTED_PAGE_CONTRACT_VERSION = "V1.50"
EXPECTED_SCORING_RULE_VERSION = "V1.11"
EXPECTED_REFERENCE_RULE = "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
EXPECTED_PRICING_RULE_VERSION = "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
EXPECTED_COMPETITION_VERSION = "V1.2.6"


def calculate_reference_score_upper_bound_for_early_exit(
    *,
    current_reference: Mapping[str, Any] | None,
    reference_score: Any,
    repair_completion: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the deterministic score upper-bound evidence used by runtime.

    Repair details can only keep or reduce the body score.  Once all non-repair
    scoring fields are present, the safest max-possible score for a partially
    collected reference is therefore the currently confirmed score plus a
    remaining contribution of 0.  Missing required scoring fields make the
    upper bound non-deterministic and block early exit.
    """

    reference = dict(current_reference or {})
    completion = dict(repair_completion or {})
    score_value = _float_or_none(_get_score_attr(reference_score, "score"))
    review_reasons = list(_get_score_attr(reference_score, "review_reasons") or [])
    components = dict(_get_score_attr(reference_score, "components") or {})
    hard_reject = bool(_get_score_attr(reference_score, "hard_reject"))
    mandatory_missing = [
        str(reason)
        for reason in review_reasons
        if str(reason).startswith("REFERENCE_") and "MISSING_FIELD_INCOMPLETE" in str(reason)
    ]
    if _float_or_none(reference.get("list_price_10k")) is None and _float_or_none(reference.get("selected_card_price_yuan")) is None:
        mandatory_missing.append("REFERENCE_LIST_PRICE_MISSING_FIELD_INCOMPLETE")
    for key in ("list_year", "list_mileage_10k_km", "transfer_count"):
        if reference.get(key) in (None, ""):
            mandatory_missing.append(f"REFERENCE_{key.upper()}_MISSING_FIELD_INCOMPLETE")

    uncollected_fields: list[str] = []
    missing_repair_count = _int_or_none(
        completion.get("missing_repair_count")
        if completion.get("missing_repair_count") is not None
        else reference.get("missing_repair_count")
    ) or 0
    unvisited_tabs = completion.get("uncollected_condition_tabs") or reference.get("uncollected_condition_tabs") or []
    if isinstance(unvisited_tabs, Sequence) and not isinstance(unvisited_tabs, (str, bytes)):
        uncollected_fields.extend(str(item) for item in unvisited_tabs if str(item).strip())
    if missing_repair_count > len(uncollected_fields):
        uncollected_fields.extend(
            f"s14_uncollected_repair_item_{index}"
            for index in range(len(uncollected_fields) + 1, missing_repair_count + 1)
        )

    collected_fields = [
        key
        for key in (
            "list_price_10k",
            "list_year",
            "list_mileage_10k_km",
            "transfer_count",
            "accident_count",
            "max_accident_amount",
        )
        if reference.get(key) not in (None, "")
    ]
    collected_fields.extend(f"score_component:{key}" for key in sorted(components))
    collected_fields.extend(
        str(item)
        for item in completion.get("collected_repair_items", []) or []
        if str(item).strip()
    )

    deterministic = bool(score_value is not None and not mandatory_missing and not hard_reject)
    remaining_max_possible_score = 0.0 if deterministic else None
    return {
        "partial_confirmed_score": score_value,
        "remaining_max_possible_score": remaining_max_possible_score,
        "max_possible_reference_score": score_value + remaining_max_possible_score
        if score_value is not None and remaining_max_possible_score is not None
        else None,
        "score_upper_bound_components": {
            "partial_confirmed_score": score_value,
            "remaining_max_possible_score": remaining_max_possible_score,
            "confirmed_score_components": components,
            "repair_remaining_contribution_assumed_max": remaining_max_possible_score,
        },
        "collected_fields": collected_fields,
        "uncollected_fields": uncollected_fields,
        "remaining_fields_assumed_max": {
            "score_delta": remaining_max_possible_score,
            "fields": uncollected_fields,
            "assumption": "uncollected_repair_items_can_only_keep_or_reduce_score",
        },
        "partial_confirmed_score_trustworthy": bool(score_value is not None and not hard_reject and not mandatory_missing),
        "remaining_max_possible_score_deterministic": deterministic,
        "mandatory_fields_collected": not mandatory_missing,
        "mandatory_fields_missing": list(dict.fromkeys(mandatory_missing)),
        "unconfirmed_hard_risk_present": bool(reference.get("unconfirmed_hard_risk_present")),
    }


def evaluate_reference_early_exit_max_possible_score(
    *,
    current_reference_index: int | None,
    target_score: float | int | None,
    partial_confirmed_score: float | int | None,
    remaining_max_possible_score: float | int | None,
    pre_boundary_evidence: Mapping[str, Any] | None = None,
    next_reference_index: int | None = None,
    active_versions: Mapping[str, Any] | None = None,
    target_score_trustworthy: bool = True,
    reference_order_reliable: bool = True,
    mandatory_fields_collected: bool = True,
    mandatory_fields_missing: Sequence[str] | None = None,
    partial_confirmed_score_trustworthy: bool = True,
    remaining_max_possible_score_deterministic: bool = True,
    no_unconfirmed_hard_risk: bool = True,
    can_return_reliable_s10: bool = True,
) -> dict[str, Any]:
    """Return a complete V3.3 low-score skip decision payload.

    This function does not mutate runtime state and does not select a final
    reference. A caller may only use an allowed decision to return to S10 and
    continue the next reference. Previous low-score evidence is recorded for
    diagnostics only and is not a V3.3 prerequisite.
    """

    missing_fields = list(mandatory_fields_missing or [])
    target = _float_or_none(target_score)
    partial = _float_or_none(partial_confirmed_score)
    remaining = _float_or_none(remaining_max_possible_score)
    previous_low_score = _float_or_none((pre_boundary_evidence or {}).get("reference_score"))
    previous_low_index = _int_or_none((pre_boundary_evidence or {}).get("reference_index"))
    max_possible = (partial + remaining) if partial is not None and remaining is not None else None
    can_reach_target = bool(max_possible is not None and target is not None and max_possible >= target)
    max_possible_above_previous_low = bool(
        max_possible is not None and previous_low_score is not None and max_possible > previous_low_score
    )
    versions = dict(active_versions or {})
    version_match = (
        versions.get("active_page_contract_version") == EXPECTED_PAGE_CONTRACT_VERSION
        and versions.get("active_scoring_rule_version") == EXPECTED_SCORING_RULE_VERSION
        and versions.get("active_reference_selection_rule") == EXPECTED_REFERENCE_RULE
        and versions.get("active_pricing_rule_version") == EXPECTED_PRICING_RULE_VERSION
        and versions.get("active_competition_coefficient_version") == EXPECTED_COMPETITION_VERSION
    )

    blockers: list[str] = []
    if not version_match:
        blockers.append("ACTIVE_RULE_SOURCE_VERSION_MISMATCH")
    if not target_score_trustworthy or target is None:
        blockers.append("TARGET_SCORE_UNTRUSTWORTHY_OR_MISSING")
    if not reference_order_reliable:
        blockers.append("REFERENCE_ORDER_UNRELIABLE")
    if not mandatory_fields_collected or missing_fields:
        blockers.append("MANDATORY_FIELDS_MISSING")
    if not partial_confirmed_score_trustworthy or partial is None:
        blockers.append("PARTIAL_CONFIRMED_SCORE_UNTRUSTWORTHY_OR_MISSING")
    if not remaining_max_possible_score_deterministic or remaining is None:
        blockers.append("REMAINING_MAX_POSSIBLE_SCORE_NOT_DETERMINISTIC")
    if max_possible is None:
        blockers.append("MAX_POSSIBLE_REFERENCE_SCORE_MISSING")
    if can_reach_target:
        blockers.append("MAX_POSSIBLE_CAN_REACH_TARGET")
    if not no_unconfirmed_hard_risk:
        blockers.append("UNCONFIRMED_HARD_RISK_PRESENT")
    if not can_return_reliable_s10:
        blockers.append("RETURN_TO_S10_NOT_RELIABLE")

    allowed = not blockers
    decision = "LOW_SCORE_SKIP_AND_CONTINUE_NEXT_REFERENCE" if allowed else "LOW_SCORE_SKIP_NOT_ALLOWED"
    reason = "UPPER_BOUND_BELOW_TARGET" if allowed else blockers[0]
    return {
        "reference_early_exit": allowed,
        "low_score_skipped_incomplete": allowed,
        "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE" if allowed else "",
        "early_exit_rule_id": EARLY_EXIT_RULE_ID,
        "early_exit_rule_clause_id": EARLY_EXIT_RULE_ID,
        "early_exit_allowed": allowed,
        "early_exit_decision": decision,
        "early_exit_reason": reason,
        "early_exit_blockers": blockers,
        "current_reference_index": current_reference_index,
        "next_reference_index": next_reference_index,
        "target_score": target,
        "partial_confirmed_score": partial,
        "remaining_max_possible_score": remaining,
        "max_possible_reference_score": max_possible,
        "reference_score_upper_bound": max_possible,
        "s14_partial_confirmed_score": partial,
        "s14_remaining_max_possible_score": remaining,
        "s14_reference_score_upper_bound": max_possible,
        "s14_low_score_skip_triggered": allowed,
        "s14_low_score_skip_reason": reason if allowed else "",
        "pre_boundary_evidence_index": previous_low_index,
        "pre_boundary_evidence_score": previous_low_score,
        "can_reach_target": can_reach_target,
        "max_possible_above_previous_low": max_possible_above_previous_low,
        "mandatory_fields_collected": bool(mandatory_fields_collected and not missing_fields),
        "mandatory_fields_missing": missing_fields,
        "score_upper_bound_components": {
            "partial_confirmed_score": partial,
            "remaining_max_possible_score": remaining,
            "max_possible_reference_score": max_possible,
        },
        "remaining_fields_assumed_max": remaining,
        "reference_score_trustworthy": False,
        "excluded_from_final_reference_selection": allowed,
        "usable_for_boundary": False,
        "usable_for_pre_boundary": False,
        "return_to_s10_required": allowed,
        "return_to_s10_after_low_score_skip": allowed,
        "returned_list_source_verified": bool(can_return_reliable_s10) if allowed else False,
        "continue_next_reference_required": allowed,
        "enter_s16_allowed": False,
        "final_reference_requires_recollect_if_selected": allowed,
    }


def reference_can_participate_in_v3_selection(reference: Mapping[str, Any]) -> bool:
    """Return False for source-backed early-exited or otherwise untrusted refs."""

    if reference.get("reference_early_exit") is True:
        return False
    if reference.get("low_score_skipped_incomplete") is True:
        return False
    if reference.get("reference_status") == "LOW_SCORE_SKIPPED_INCOMPLETE":
        return False
    if reference.get("excluded_from_final_reference_selection") is True:
        return False
    if reference.get("usable_for_boundary") is False or reference.get("usable_for_pre_boundary") is False:
        return False
    if reference.get("reference_score_trustworthy") is False:
        return False
    if reference.get("reference_score_usable_for_boundary") is False:
        return False
    return True


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_score_attr(score: Any, key: str) -> Any:
    if isinstance(score, Mapping):
        return score.get(key)
    return getattr(score, key, None)


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
