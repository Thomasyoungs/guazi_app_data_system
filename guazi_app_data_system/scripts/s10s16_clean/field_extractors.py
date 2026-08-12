"""Pure S12/S13 field validation helpers.

Rules:
- Extractors validate and normalize evidence only.
- They do not click, advance state, send Feishu messages, or calculate price.
- Malformed input is returned as structured failure data, not raw exceptions.
"""

from __future__ import annotations

from typing import Any

from . import issue_codes


def is_valid_extent(extent: Any) -> bool:
    if not isinstance(extent, (list, tuple)) or len(extent) < 4:
        return False
    try:
        x1, y1, x2, y2 = (int(extent[0]), int(extent[1]), int(extent[2]), int(extent[3]))
    except (TypeError, ValueError, IndexError):
        return False
    return x1 < x2 and y1 < y2


def coerce_extent(extent: Any) -> tuple[int, int, int, int] | None:
    if not is_valid_extent(extent):
        return None
    return (int(extent[0]), int(extent[1]), int(extent[2]), int(extent[3]))


def s12_claim_recovery_extent_candidate(raw_extent: Any) -> tuple[tuple[int, int, int, int] | None, dict[str, Any]]:
    trace = {
        "raw_extent": raw_extent,
        "bounds_valid": False,
        "stop_code": issue_codes.S12_CLAIM_RECOVERY_EXTENT_INVALID,
        "failure_reason": "extent_missing_or_malformed",
    }
    if raw_extent is None:
        return None, trace

    extent = raw_extent
    source = ""
    if (
        isinstance(raw_extent, (list, tuple))
        and len(raw_extent) >= 2
        and is_valid_extent(raw_extent[0])
        and isinstance(raw_extent[1], str)
    ):
        extent = raw_extent[0]
        source = str(raw_extent[1])

    bounds = coerce_extent(extent)
    if bounds is None:
        if isinstance(extent, (list, tuple)) and len(extent) >= 4:
            trace["stop_code"] = issue_codes.S12_CLAIM_RECOVERY_BOUNDS_INVALID
            trace["failure_reason"] = "bounds_invalid_or_zero_area"
        return None, trace

    trace.update(
        {
            "raw_extent": tuple(bounds),
            "bounds_valid": True,
            "stop_code": "",
            "failure_reason": "",
            "source": source,
        }
    )
    return bounds, trace

