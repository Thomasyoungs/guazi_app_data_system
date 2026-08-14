"""Registration date normalization shared by Feishu drafts and runner builds."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class NormalizedRegistrationDate:
    normalized_date: str
    year: int
    month: int
    raw_value: str


def normalize_registration_date(value: Any) -> NormalizedRegistrationDate | None:
    """Normalize Feishu registration date text to YYYY.MM plus year/month."""
    raw = str(value or "").strip()
    if not raw:
        return None

    text = raw.strip()
    text = text.replace("\uff0e", ".").replace("\uff0d", "-").replace("\uff0f", "/")
    text = text.replace("\u5e74", ".").replace("\u6708", "")
    text = re.sub(r"\s+", "", text)

    match = re.fullmatch(r"(?P<year>\d{2}|\d{4})[.\-/](?P<month>\d{1,2})", text)
    if not match:
        return None

    year = int(match.group("year"))
    month = int(match.group("month"))
    if year < 100:
        year += 2000
    if year < 1900 or year > 2099 or month < 1 or month > 12:
        return None

    return NormalizedRegistrationDate(
        normalized_date=f"{year:04d}.{month:02d}",
        year=year,
        month=month,
        raw_value=raw,
    )
