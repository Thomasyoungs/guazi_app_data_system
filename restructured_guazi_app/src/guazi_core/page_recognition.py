"""Page recognition from UI text or OCR text.

Migrated from the original page_recognition.py.
"""

from __future__ import annotations

import re
from typing import Any


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


class PageRecognizer:
    def __init__(self, pages_config: dict[str, Any]) -> None:
        self.pages = pages_config.get("pages", [])

    def matches(self, page: dict[str, Any], text: str, context: dict[str, Any] | None = None) -> bool:
        recognition = page.get("recognition", {})
        if recognition.get("internal"):
            return bool(context and context.get("internal_state") == page.get("id"))
        context_equals = recognition.get("context_equals", {})
        if context_equals:
            if not context:
                return False
            for key, expected in context_equals.items():
                if context.get(key) != expected:
                    return False
        normalized = normalize_text(text)
        for token in recognition.get("forbidden_contains", []):
            if token in normalized:
                return False
        for token in recognition.get("strong_contains", []):
            if token not in normalized:
                return False
        for token in recognition.get("contains_all", []):
            if token not in normalized:
                return False
        contains_any = recognition.get("contains_any", [])
        if contains_any and not any(token in normalized for token in contains_any):
            return False
        sorted_by = recognition.get("sorted_by")
        if sorted_by and context and context.get("sorted_by") != sorted_by:
            return False
        return True

    def recognize(self, text: str, candidate_ids: list[str] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        candidates = [page for page in self.pages if not candidate_ids or page.get("id") in candidate_ids]
        for page in candidates:
            if self.matches(page, text, context=context):
                return page
        return None

    def classify_start_page(self, text: str) -> str | None:
        normalized = normalize_text(text)
        if "跳过" in normalized or "广告" in normalized:
            return "S00"
        if all(token in normalized for token in ["首页", "选车", "卖车", "我的"]):
            return "S01"
        return None
