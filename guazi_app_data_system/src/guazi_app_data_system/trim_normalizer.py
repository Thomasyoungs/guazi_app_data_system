"""Trim-name normalization for strict matching.

Only emission-standard spelling variants are normalized. The module does not
perform fuzzy matching, alias expansion, model-year removal, or DSG removal.
"""

from __future__ import annotations

import re


_CHINA_5_PATTERN = re.compile(r"国\s*(?:5|Ⅴ|V|五)(?!I)", re.IGNORECASE)
_CHINA_6_PATTERN = re.compile(r"国\s*(?:6|Ⅵ|VI|六)", re.IGNORECASE)


def normalize_emission_standard(text: str) -> str:
    """Normalize only Chinese emission-standard tokens.

    国5 / 国Ⅴ / 国V / 国五 -> 国V
    国6 / 国Ⅵ / 国VI / 国六 -> 国VI
    """
    value = str(text or "")
    value = _CHINA_6_PATTERN.sub("国VI", value)
    value = _CHINA_5_PATTERN.sub("国V", value)
    return value


def normalize_trim_for_match(text: str) -> str:
    """Normalize emission spelling and whitespace, preserving all other text."""
    value = normalize_emission_standard(text)
    return re.sub(r"\s+", " ", value).strip()


def exact_trim_match_with_emission_normalization(task_trim: str, app_trim: str) -> bool:
    """Return true only when trims are identical after emission normalization."""
    return normalize_trim_for_match(task_trim) == normalize_trim_for_match(app_trim)


def emission_normalization_used(task_trim: str, app_trim: str) -> bool:
    """Whether normalization changed a non-identical pair into an exact match."""
    task_raw = re.sub(r"\s+", " ", str(task_trim or "")).strip()
    app_raw = re.sub(r"\s+", " ", str(app_trim or "")).strip()
    return task_raw != app_raw and exact_trim_match_with_emission_normalization(task_trim, app_trim)
