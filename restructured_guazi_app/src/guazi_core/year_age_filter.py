"""Vehicle-year and age-slider helpers.

These helpers implement three related but different rules:

1. When the APP exposes only age ranges, choose the narrowest option that
   covers the target age and still require vehicle-year secondary validation.
2. When the APP exposes an exact age slider, treat it as an exact value, not a
   range. The slider target must still not replace vehicle-year secondary
   validation on the result list.
3. When the APP exposes a dual-handle age slider, exact success means both
   handles equal the target age. A partial range such as 6-10 or 6-unlimited
   must remain blocked.
"""

from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any


_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:-|~|至|到)\s*(\d+)\s*年?\s*$")
_EXACT_RE = re.compile(r"^\s*(\d+)\s*年?\s*$")
_UNDER_RE = re.compile(r"^\s*(\d+)\s*年?(?:以内|内|以下)\s*$")
_OVER_RE = re.compile(r"^\s*(\d+)\s*年?(?:以上|及以上)\s*$")

UNLIMITED_AGE_TEXTS = {
    "\u4e0d\u9650\u8f66\u9f84",
    "\u4e0d\u9650",
    "\u5168\u90e8\u8f66\u9f84",
}

AGE_PANEL_TITLE_TEXTS = {
    "\u8f66\u9f84\uff08\u5e74\uff09",
    "\u8f66\u9f84",
    "\u5e74\u6b3e",
    "\u4e0a\u724c\u5e74\u4efd",
    "\u4e0a\u724c\u65f6\u95f4",
}
AGE_VALUE_RE = re.compile(r"(<?\d)(\d{1,2})\s*年?")
AGE_RANGE_LABEL_RE = re.compile(r"(<?\d)(\d{1,2})\s*(?:年?)*\s*(?:-|~|至|到)\s*(\d{1,2})\s*年?")
AGE_OVER_LABEL_RE = re.compile(r"(<?\d)(\d{1,2})\s*年?(?:以上|及以上)")

REQUIRES_VEHICLE_YEAR_SECONDARY_CHECK = True


def current_year_from_system() -> int:
    return datetime.now().year


def calculate_target_age_from_vehicle_year(vehicle_year: int | str, current_year: int | None = None) -> int:
    year = int(vehicle_year)
    current = int(current_year if current_year is not None else current_year_from_system())
    return max(current - year, 0)


def calculate_target_age(
    registration_date_raw: str | None,
    vehicle_year: int | str | None,
    current_date: date | datetime | str | None = None,
) -> int:
    """Calculate target age by business year only.

    The S07 contract treats a car registered in any month of 2025 as age 1
    during business year 2026. Month/day must not reduce the target age.
    """
    if vehicle_year in (None, ""):
        match = re.search(r"(19\d{2}|20\d{2})", str(registration_date_raw or ""))
        if not match:
            raise ValueError("vehicle_year and registration_date_raw are both unavailable for age calculation.")
        year = int(match.group(1))
    else:
        year = int(vehicle_year)

    today = _coerce_current_date(current_date)
    return max(today.year - year, 0)


def normalize_age_option_text(text: str) -> str:
    normalized = re.sub(r"\s+", "", (text or "").strip())
    for source in ("—", "–", "~", "至", "到"):
        normalized = normalized.replace(source, "-")
    return normalized


def parse_age_slider_current_value(value: Any) -> int | None:
    """Parse the current exact age value from slider text or labels."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    match = AGE_VALUE_RE.search(text)
    if match:
        return int(match.group(1))
    return None


def normalize_age_handle_value(value: Any) -> int | str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if any(marker in text for marker in UNLIMITED_AGE_TEXTS | {"不限", "及以上", "以上"}):
        over_match = AGE_OVER_LABEL_RE.search(text)
        if over_match:
            return f"{int(over_match.group(1))}+"
        return "不限"
    exact = parse_age_slider_current_value(text)
    if exact is not None:
        return exact
    return text


def parse_age_range_label(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {
            "left_handle_value": None,
            "right_handle_value": None,
            "raw_label": None,
        }
    text = str(value).strip()
    if not text:
        return {
            "left_handle_value": None,
            "right_handle_value": None,
            "raw_label": text,
        }
    normalized = normalize_age_option_text(text)
    range_match = AGE_RANGE_LABEL_RE.search(normalized)
    if range_match:
        return {
            "left_handle_value": int(range_match.group(1)),
            "right_handle_value": int(range_match.group(2)),
            "raw_label": text,
        }
    over_match = AGE_OVER_LABEL_RE.search(normalized)
    if over_match:
        return {
            "left_handle_value": int(over_match.group(1)),
            "right_handle_value": "不限",
            "raw_label": text,
        }
    exact = parse_age_slider_current_value(normalized)
    if exact is not None:
        return {
            "left_handle_value": exact,
            "right_handle_value": exact,
            "raw_label": text,
        }
    return {
        "left_handle_value": None,
        "right_handle_value": None,
        "raw_label": text,
    }


def detect_age_slider_bounds(slider_snapshot: Any) -> dict[str, Any]:
    """Extract slider/track bounds from a recognized slider snapshot."""
    if not isinstance(slider_snapshot, dict):
        return {
            "slider_bounds": None,
            "track_bounds": None,
            "current_value": None,
            "min_value": None,
            "max_value": None,
        }
    return {
        "slider_bounds": _coerce_bounds(slider_snapshot.get("slider_bounds")),
        "track_bounds": _coerce_bounds(slider_snapshot.get("track_bounds")),
        "current_value": parse_age_slider_current_value(slider_snapshot.get("current_value")),
        "min_value": _coerce_int(slider_snapshot.get("min_value")),
        "max_value": _coerce_int(slider_snapshot.get("max_value")),
    }


def detect_dual_handle_slider(slider_snapshot: Any) -> dict[str, Any]:
    """Normalize dual-handle slider evidence from runtime snapshots."""
    if not isinstance(slider_snapshot, dict):
        return {
            "dual_handle_detected": False,
            "left_handle_bounds": None,
            "right_handle_bounds": None,
            "left_handle_value": None,
            "right_handle_value": None,
            "slider_bounds": None,
            "track_bounds": None,
            "selected_range_label": None,
        }

    range_label = parse_age_range_label(
        slider_snapshot.get("selected_range_label")
        or slider_snapshot.get("current_range_label")
        or slider_snapshot.get("range_label")
    )
    left_handle_value = slider_snapshot.get("left_handle_value")
    right_handle_value = slider_snapshot.get("right_handle_value")
    if left_handle_value in (None, ""):
        left_handle_value = range_label["left_handle_value"]
    if right_handle_value in (None, ""):
        right_handle_value = range_label["right_handle_value"]

    return {
        "dual_handle_detected": bool(
            _coerce_bounds(slider_snapshot.get("left_handle_bounds"))
            or _coerce_bounds(slider_snapshot.get("right_handle_bounds"))
            or slider_snapshot.get("dual_handle_detected")
            or range_label["left_handle_value"] is not None
            or range_label["right_handle_value"] is not None
        ),
        "left_handle_bounds": _coerce_bounds(slider_snapshot.get("left_handle_bounds")),
        "right_handle_bounds": _coerce_bounds(slider_snapshot.get("right_handle_bounds")),
        "left_handle_value": normalize_age_handle_value(left_handle_value),
        "right_handle_value": normalize_age_handle_value(right_handle_value),
        "slider_bounds": _coerce_bounds(slider_snapshot.get("slider_bounds")),
        "track_bounds": _coerce_bounds(slider_snapshot.get("track_bounds")),
        "selected_range_label": slider_snapshot.get("selected_range_label")
        or slider_snapshot.get("current_range_label")
        or slider_snapshot.get("range_label"),
    }


def validate_exact_age_value(current_value: Any, target_age: int | str) -> bool:
    parsed = parse_age_slider_current_value(current_value)
    if parsed is None:
        return False
    return parsed == int(target_age)


def parse_left_handle_value(slider_snapshot: Any) -> int | str | None:
    return detect_dual_handle_slider(slider_snapshot)["left_handle_value"]


def parse_right_handle_value(slider_snapshot: Any) -> int | str | None:
    return detect_dual_handle_slider(slider_snapshot)["right_handle_value"]


def bounds_center(bounds: list[int] | None) -> list[int] | None:
    coerced = _coerce_bounds(bounds)
    if not coerced:
        return None
    return [(coerced[0] + coerced[2]) // 2, (coerced[1] + coerced[3]) // 2]


def handles_physically_overlap(
    left_handle_bounds: list[int] | None,
    right_handle_bounds: list[int] | None,
    tolerance_px: int = 18,
) -> bool:
    """Return true only when both slider handles occupy the same target tick."""
    left_center = bounds_center(left_handle_bounds)
    right_center = bounds_center(right_handle_bounds)
    if not left_center or not right_center:
        return False
    return (
        abs(left_center[0] - right_center[0]) <= int(tolerance_px)
        and abs(left_center[1] - right_center[1]) <= int(tolerance_px)
    )


def calculate_right_handle_overlap_target_coordinate(left_handle_bounds: list[int] | None) -> list[int] | None:
    """For exact age, the right handle target is the left handle's physical center."""
    return bounds_center(left_handle_bounds)


def validate_exact_age_range(
    left_value: Any,
    right_value: Any,
    target_age: int | str,
    left_handle_bounds: list[int] | None = None,
    right_handle_bounds: list[int] | None = None,
    require_physical_overlap: bool = False,
    target_age_calculation_verified: bool = True,
) -> bool:
    target = int(target_age)
    left_normalized = normalize_age_handle_value(left_value)
    right_normalized = normalize_age_handle_value(right_value)
    values_match = left_normalized == target and right_normalized == target
    if not values_match or not target_age_calculation_verified:
        return False
    if require_physical_overlap:
        return handles_physically_overlap(left_handle_bounds, right_handle_bounds)
    return True


def calculate_left_handle_target_coordinate(track_bounds: list[int] | None, target_coordinate: list[int] | None) -> list[int] | None:
    if not track_bounds or not target_coordinate:
        return None
    x = max(track_bounds[0], min(int(target_coordinate[0]), track_bounds[2]))
    y = (int(track_bounds[1]) + int(track_bounds[3])) // 2
    return [x, y]


def calculate_right_handle_target_coordinate(track_bounds: list[int] | None, target_coordinate: list[int] | None) -> list[int] | None:
    if not track_bounds or not target_coordinate:
        return None
    x = max(track_bounds[0], min(int(target_coordinate[0]), track_bounds[2]))
    y = (int(track_bounds[1]) + int(track_bounds[3])) // 2
    return [x, y]


def requires_vehicle_year_secondary_check() -> bool:
    return REQUIRES_VEHICLE_YEAR_SECONDARY_CHECK


def parse_vehicle_age_option(text: str, bounds: list[int] | None = None) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    normalized = normalize_age_option_text(raw)
    if normalized in AGE_PANEL_TITLE_TEXTS:
        return None
    if normalized in UNLIMITED_AGE_TEXTS:
        return {
            "raw_text": raw,
            "normalized_text": normalized,
            "bounds": bounds,
            "option_type": "unlimited",
            "min_age": None,
            "max_age": None,
            "width_years": None,
            "is_unlimited": True,
        }

    match = _RANGE_RE.match(normalized)
    if match:
        min_age = int(match.group(1))
        max_age = int(match.group(2))
        if max_age < min_age:
            min_age, max_age = max_age, min_age
        return {
            "raw_text": raw,
            "normalized_text": normalized,
            "bounds": bounds,
            "option_type": "range",
            "min_age": min_age,
            "max_age": max_age,
            "width_years": max_age - min_age,
            "is_unlimited": False,
        }

    match = _EXACT_RE.match(normalized)
    if match:
        value = int(match.group(1))
        return {
            "raw_text": raw,
            "normalized_text": normalized,
            "bounds": bounds,
            "option_type": "exact",
            "min_age": value,
            "max_age": value,
            "width_years": 0,
            "is_unlimited": False,
        }

    match = _UNDER_RE.match(normalized)
    if match:
        value = int(match.group(1))
        return {
            "raw_text": raw,
            "normalized_text": normalized,
            "bounds": bounds,
            "option_type": "under",
            "min_age": 0,
            "max_age": value,
            "width_years": value,
            "is_unlimited": False,
        }

    match = _OVER_RE.match(normalized)
    if match:
        value = int(match.group(1))
        return {
            "raw_text": raw,
            "normalized_text": normalized,
            "bounds": bounds,
            "option_type": "over",
            "min_age": value,
            "max_age": None,
            "width_years": None,
            "is_unlimited": False,
        }
    return None


def parse_vehicle_age_options(items: list[Any]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[int, int, int, int] | None]] = set()
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("text") or "")
            bounds = item.get("bounds")
            if not isinstance(bounds, list) or len(bounds) != 4:
                bounds = None
        else:
            text = str(item)
            bounds = None
        option = parse_vehicle_age_option(text, bounds)
        if not option:
            continue
        key = (
            str(option["normalized_text"]),
            tuple(option["bounds"]) if isinstance(option.get("bounds"), list) else None,
        )
        if key in seen:
            continue
        seen.add(key)
        parsed.append(option)
    return parsed


def option_covers_age(option: dict[str, Any], target_age: int) -> bool:
    if option.get("is_unlimited"):
        return False
    min_age = option.get("min_age")
    max_age = option.get("max_age")
    if min_age is not None and int(target_age) < int(min_age):
        return False
    if max_age is not None and int(target_age) > int(max_age):
        return False
    return True


def choose_narrowest_age_option(options: list[dict[str, Any]], target_age: int) -> dict[str, Any]:
    target = int(target_age)
    candidates = [option for option in options if option_covers_age(option, target)]
    if not candidates:
        return {
            "recommended_option": None,
            "requires_vehicle_year_secondary_check": REQUIRES_VEHICLE_YEAR_SECONDARY_CHECK,
            "target_age": target,
        }

    def sort_key(option: dict[str, Any]) -> tuple[float, float, float, str]:
        option_type = str(option.get("option_type") or "")
        min_age = option.get("min_age")
        max_age = option.get("max_age")
        if option_type == "exact":
            priority = 0.0
            width = 0.0
            midpoint = float(min_age)
        elif min_age is not None and max_age is not None:
            priority = 1.0
            width = float(max_age - min_age)
            midpoint = (float(min_age) + float(max_age)) / 2.0
        elif max_age is not None:
            priority = 2.0
            width = float(max_age)
            midpoint = float(max_age) / 2.0
        else:
            priority = 3.0
            width = 9999.0
            midpoint = float(min_age if min_age is not None else target)
        return (
            priority,
            width,
            abs(float(target) - midpoint),
            str(option.get("normalized_text") or ""),
        )

    recommended = min(candidates, key=sort_key)
    return {
        "recommended_option": recommended,
        "requires_vehicle_year_secondary_check": REQUIRES_VEHICLE_YEAR_SECONDARY_CHECK,
        "target_age": target,
    }


def _coerce_current_date(current_date: date | datetime | str | None) -> date:
    if current_date is None:
        return datetime.now().date()
    if isinstance(current_date, datetime):
        return current_date.date()
    if isinstance(current_date, date):
        return current_date
    text = str(current_date).strip()
    if not text:
        return datetime.now().date()
    normalized = text.replace(".", "-").replace("/", "-")
    return datetime.fromisoformat(normalized).date()


def _parse_registration_month(registration_date_raw: str | None) -> int | None:
    text = str(registration_date_raw or "").strip()
    if not text:
        return None
    match = re.search(r"(19\d{2}|20\d{2})[.\-/年\s]*(\d{1,2})", text)
    if not match:
        return None
    month = int(match.group(2))
    if 1 <= month <= 12:
        return month
    return None


def _coerce_bounds(value: Any) -> list[int] | None:
    if isinstance(value, list) and len(value) == 4:
        return [int(item) for item in value]
    return None


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)
