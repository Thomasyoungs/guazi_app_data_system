"""Parse Phase 1 Feishu pricing messages into draft target tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable

try:
    from registration_date_normalizer import normalize_registration_date
except ImportError:  # pragma: no cover - supports package-style imports in tests.
    from scripts.registration_date_normalizer import normalize_registration_date

try:
    from universal_vehicle_parser_v1 import PARSER_VERSION, parse_vehicle_model
except ImportError:  # pragma: no cover - supports package-style imports in tests.
    from scripts.universal_vehicle_parser_v1 import PARSER_VERSION, parse_vehicle_model


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERIES_BRAND_ALIASES_PATH = PROJECT_ROOT / "config" / "feishu_series_brand_aliases.json"
TARGET_SOURCE_FIELD_ALIASES_PATH = PROJECT_ROOT / "config" / "target_source_field_aliases.json"

# Legacy rule_source_sync_check markers retained for historical mojibake checks:
# 銆怽\[] 杞﹀瀷 杞﹁締棰滆壊 鍏蜂綋杞﹀喌 杞︾墝褰掑睘

FEISHU_USER_REQUIRED_FIELDS: tuple[str, ...] = (
    "model_config",
    "license_date",
    "mileage_text",
    "color",
    "transfer_count_text",
    "condition_text",
)

INTERNAL_REQUIRED_FIELDS: tuple[str, ...] = (
    "brand",
    "series",
) + FEISHU_USER_REQUIRED_FIELDS

REQUIRED_FIELDS: tuple[str, ...] = FEISHU_USER_REQUIRED_FIELDS

OPTIONAL_FIELDS: tuple[str, ...] = (
    "accident_count_text",
    "max_claim_amount_text",
    "city",
    "remark",
    "guide_price_text",
    "emission_standard",
    "sunroof_text",
    "insurance_expiry",
)

NORMALIZED_DRAFT_FIELDS: tuple[str, ...] = (
    "guide_price_wan",
    "energy_type",
    "sunroof",
    "insurance_expire_date",
    "mileage_10k_km",
    "transfer_count",
)

FIELD_DISPLAY_NAMES: dict[str, str] = {
    "brand": "品牌",
    "series": "车系",
    "model_config": "车型配置",
    "year_model": "年款",
    "config_model": "配置",
    "license_date": "上牌日期",
    "mileage_text": "表显里程",
    "color": "颜色",
    "transfer_count_text": "过户次数",
    "condition_text": "车况",
    "accident_count_text": "出险次数",
    "max_claim_amount_text": "最大金额",
    "city": "城市",
    "remark": "备注",
    "guide_price_text": "指导价",
    "emission_standard": "排放标准",
    "sunroof_text": "有无天窗",
    "insurance_expiry": "保险到期",
}

DEFAULT_FIELD_ALIASES: dict[str, str] = {
    "品牌": "brand",
    "车系": "series",
    "车型": "model_config",
    "车辆车型": "model_config",
    "车辆型号": "model_config",
    "目标车型": "model_config",
    "车型配置": "model_config",
    "配置": "model_config",
    "车款": "model_config",
    "上牌日期": "license_date",
    "上牌时间": "license_date",
    "上牌年月": "license_date",
    "上牌": "license_date",
    "首登日期": "license_date",
    "首次登记": "license_date",
    "注册日期": "license_date",
    "登记日期": "license_date",
    "表显里程": "mileage_text",
    "表显公里": "mileage_text",
    "里程": "mileage_text",
    "公里数": "mileage_text",
    "行驶里程": "mileage_text",
    "当前里程": "mileage_text",
    "车辆颜色": "color",
    "车身颜色": "color",
    "外观颜色": "color",
    "颜色": "color",
    "过户次数": "transfer_count_text",
    "过户": "transfer_count_text",
    "过户数": "transfer_count_text",
    "转手次数": "transfer_count_text",
    "具体车况": "condition_text",
    "车辆车况": "condition_text",
    "外观车况": "condition_text",
    "检测车况": "condition_text",
    "车况": "condition_text",
    "车况描述": "condition_text",
    "出险次数": "accident_count_text",
    "出险": "accident_count_text",
    "最大金额": "max_claim_amount_text",
    "金额": "max_claim_amount_text",
    "城市": "city",
    "车牌归属": "city",
    "车牌归属地": "city",
    "归属地": "city",
    "上牌城市": "city",
    "牌照归属": "city",
    "备注": "remark",
    "指导价": "guide_price_text",
    "新车指导价": "guide_price_text",
    "厂商指导价": "guide_price_text",
    "排放标准": "emission_standard",
    "排放": "emission_standard",
    "能源类型": "emission_standard",
    "动力类型": "emission_standard",
    "有无天窗": "sunroof_text",
    "天窗": "sunroof_text",
    "是否带天窗": "sunroof_text",
    "保险到期": "insurance_expiry",
    "保险日期": "insurance_expiry",
    "保险截止": "insurance_expiry",
    "交强险到期": "insurance_expiry",
}


def load_target_field_aliases(path: Path = TARGET_SOURCE_FIELD_ALIASES_PATH) -> dict[str, str]:
    aliases = dict(DEFAULT_FIELD_ALIASES)
    if not path.exists():
        return aliases
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    configured = payload.get("aliases") if isinstance(payload, dict) else None
    if isinstance(configured, dict):
        for label, field in configured.items():
            normalized_label = re.sub(r"[\s\u00a0]+", "", str(label)).strip()
            field_name = str(field).strip()
            if normalized_label and field_name:
                aliases[normalized_label] = field_name
    return aliases


FIELD_ALIASES: dict[str, str] = load_target_field_aliases()

DRAFT_FIELD_ORDER: tuple[str, ...] = (
    "brand",
    "series",
    "model_config",
    "raw_model_text",
    "year_model",
    "config_model",
) + FEISHU_USER_REQUIRED_FIELDS[1:] + OPTIONAL_FIELDS + NORMALIZED_DRAFT_FIELDS

DISPLAY_ORDER: tuple[str, ...] = (
    "brand",
    "series",
    "year_model",
    "config_model",
) + FEISHU_USER_REQUIRED_FIELDS[1:] + OPTIONAL_FIELDS

MODEL_META_FIELDS: tuple[str, ...] = (
    "vehicle_model",
    "full_model",
    "brand_source",
    "series_source",
    "model_parse_source",
    "vehicle_parser_version",
    "vehicle_parser_matched_alias",
    "vehicle_model_decision_code",
    "vehicle_model_identity_key",
    "vehicle_model_identity_key_v2",
    "vehicle_model_identity_key_v2_scope",
    "canonical_brand",
    "canonical_series",
    "canonical_year_model",
    "canonical_config_model",
    "config_semantic_key",
    "config_semantic_version",
    "raw_config_model",
)

REGISTRATION_DATE_META_FIELDS: tuple[str, ...] = (
    "register_date",
    "registration_date",
    "register_year",
    "registration_date_year",
    "registration_date_month",
    "license_date_raw",
)

MODEL_UNRESOLVED_STATUS = "DRAFT_NEEDS_MODEL_RESOLUTION"
DATE_UNRESOLVED_STATUS = "DRAFT_NEEDS_TARGET_INFO"
MODEL_STRICT_TEMPLATE_INCOMPLETE = "MODEL_STRICT_TEMPLATE_INCOMPLETE"
STRICT_TEMPLATE_PARSE_SOURCE = "target_model_strict_template"
KNOWN_BRAND_PREFIX_PARSE_SOURCE = "known_brand_prefix_fallback"

DEFAULT_SERIES_BRAND_ALIASES: dict[str, dict[str, Any]] = {
    "科鲁泽": {"brand": "雪佛兰", "series": "科鲁泽", "aliases": ["科鲁泽"]},
    "雅阁": {"brand": "本田", "series": "雅阁", "aliases": ["雅阁"]},
    "星锐": {"brand": "斯柯达", "series": "星锐", "aliases": ["星锐"]},
}


@dataclass(frozen=True)
class ParsedTargetTaskMessage:
    task_id: str
    fields: dict[str, Any]
    draft: dict[str, Any]
    validation_result: dict[str, Any]
    status: dict[str, Any]
    reply_text: str

    @property
    def valid(self) -> bool:
        return bool(self.validation_result["valid"])


def _now_iso(clock: Callable[[], datetime] | None = None) -> str:
    now = clock() if clock else datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.isoformat(timespec="seconds")
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def normalize_label(label: str) -> str:
    return re.sub(r"[\s\u00a0]+", "", label or "").strip()


def _set_parsed_field(parsed: dict[str, str], label: str, value: str) -> None:
    field = FIELD_ALIASES.get(normalize_label(label))
    if field:
        cleaned = value.strip()
        parsed[field] = cleaned
        if field == "model_config":
            parsed["raw_model_text"] = cleaned


def _consume_normalized_prefix(raw_line: str, normalized_prefix: str) -> str | None:
    consumed = 0
    for index, char in enumerate(raw_line):
        if char.isspace() or char == "\u00a0":
            continue
        if consumed >= len(normalized_prefix) or char != normalized_prefix[consumed]:
            return None
        consumed += 1
        if consumed == len(normalized_prefix):
            return raw_line[index + 1 :].strip()
    return None


def _split_label_value_without_colon(line: str) -> tuple[str, str] | None:
    normalized_line = normalize_label(line)
    aliases = sorted(FIELD_ALIASES, key=len, reverse=True)
    for alias in aliases:
        if not normalized_line.startswith(alias) or len(normalized_line) == len(alias):
            continue
        value = _consume_normalized_prefix(line, alias)
        if value:
            return alias, value
    return None


def parse_template_fields(text: str) -> dict[str, str]:
    """Return canonical internal fields parsed from a Feishu text template."""
    parsed: dict[str, str] = {}

    bracket_matches = list(re.finditer(r"[【\[]\s*([^】\]]+?)\s*[】\]]", text or ""))
    for index, match in enumerate(bracket_matches):
        next_start = bracket_matches[index + 1].start() if index + 1 < len(bracket_matches) else len(text)
        value = (text[match.end() : next_start] or "").strip()
        _set_parsed_field(parsed, match.group(1), value)

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line == "定价" or line.startswith("【") or line.startswith("["):
            continue
        match = re.match(r"^\s*([^:：]+?)\s*[:：]\s*(.*?)\s*$", line)
        if match:
            _set_parsed_field(parsed, match.group(1), match.group(2))
            continue
        no_colon_match = _split_label_value_without_colon(line)
        if no_colon_match:
            _set_parsed_field(parsed, no_colon_match[0], no_colon_match[1])
    return parsed


def missing_required_fields(fields: dict[str, str]) -> list[str]:
    return [
        FIELD_DISPLAY_NAMES[field]
        for field in REQUIRED_FIELDS
        if not fields.get(field, "").strip()
    ]


def load_series_brand_aliases(path: Path = SERIES_BRAND_ALIASES_PATH) -> dict[str, dict[str, Any]]:
    """Compatibility loader; parsing itself is centralized in parse_vehicle_model."""
    if not path.exists():
        return DEFAULT_SERIES_BRAND_ALIASES
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    aliases = payload.get("series_brand_aliases") if isinstance(payload, dict) else None
    if not isinstance(aliases, dict) or not aliases:
        return DEFAULT_SERIES_BRAND_ALIASES
    return aliases


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip()


def extract_year_model(model_text: str) -> str | None:
    match = re.search(r"((?:19|20)\d{2})\s*款", model_text or "")
    return f"{match.group(1)}款" if match else None


def _strip_year_model_prefix(model_text: str) -> str:
    match = re.search(r"(?:19|20)\d{2}\s*款", model_text or "")
    if not match:
        return (model_text or "").strip()
    return (model_text[match.end() :] or "").strip()


def _legacy_aliases_to_parser_config(aliases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": PARSER_VERSION,
        "series_rules": [
            {
                "brand": str(rule.get("brand") or ""),
                "series": str(rule.get("series") or series_key),
                "aliases": [str(series_key)]
                + [str(item) for item in rule.get("aliases", []) if item],
            }
            for series_key, rule in aliases.items()
            if isinstance(rule, dict)
        ],
    }


def _inference_payload_from_parse_result(parsed_model: Any) -> dict[str, Any]:
    if parsed_model.ok:
        payload = parsed_model.as_dict()
        return {
            "ok": True,
            "brand": payload["brand"],
            "series": payload["series"],
            "matched_key": payload["matched_alias"],
            "matches": payload["matches"],
            "parser_result": payload,
        }
    if parsed_model.error == "MODEL_BRAND_SERIES_AMBIGUOUS":
        return {"ok": False, "error": parsed_model.error, "matches": list(parsed_model.matches)}
    return {"ok": False, "error": parsed_model.error or "MODEL_BRAND_SERIES_UNRESOLVED", "matches": []}


def _draft_model_parse_source(parser_result: dict[str, Any]) -> str:
    source = str(parser_result.get("source") or "")
    if source == STRICT_TEMPLATE_PARSE_SOURCE:
        return STRICT_TEMPLATE_PARSE_SOURCE
    if source == KNOWN_BRAND_PREFIX_PARSE_SOURCE:
        return KNOWN_BRAND_PREFIX_PARSE_SOURCE
    return "universal_vehicle_parser_v1"


def infer_series_brand_from_model_text(
    model_text: str,
    aliases: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper around the unified vehicle parser entry."""
    normalized_model = _compact_text(model_text)
    if not normalized_model:
        return {"ok": False, "error": "MODEL_TEXT_EMPTY", "matches": []}
    parser_config = _legacy_aliases_to_parser_config(aliases) if aliases is not None else None
    return _inference_payload_from_parse_result(parse_vehicle_model(model_text, config=parser_config))


def infer_brand_from_series_text(
    series_text: str,
    aliases: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_series = _compact_text(series_text)
    if not normalized_series:
        return {"ok": False, "error": "MODEL_BRAND_SERIES_UNRESOLVED", "matches": []}
    parser_config = _legacy_aliases_to_parser_config(aliases) if aliases is not None else None
    return _inference_payload_from_parse_result(parse_vehicle_model(series_text, config=parser_config))


def enrich_model_identity(fields: dict[str, str]) -> tuple[dict[str, str], list[str], list[str]]:
    """Fill internal brand/series/year/config fields from the user model text."""
    enriched = dict(fields)
    errors: list[str] = []
    warnings: list[str] = []
    model_text = enriched.get("model_config", "").strip()
    parsed_model = parse_vehicle_model(model_text) if model_text else None
    if model_text:
        year_model = parsed_model.year_model or extract_year_model(model_text)
        if year_model and not enriched.get("year_model"):
            enriched["year_model"] = year_model
        config_model = parsed_model.config_model if parsed_model.ok else _strip_year_model_prefix(model_text)
        if config_model:
            enriched["config_model"] = config_model
        if parsed_model.ok and parsed_model.raw_config_model:
            enriched["raw_config_model"] = parsed_model.raw_config_model

    inferred = (
        _inference_payload_from_parse_result(parsed_model)
        if parsed_model is not None
        else {"ok": False, "error": "MODEL_TEXT_EMPTY", "matches": []}
    )
    explicit_brand = enriched.get("brand", "").strip()
    explicit_series = enriched.get("series", "").strip()
    if not inferred.get("ok") and explicit_series:
        inferred = infer_brand_from_series_text(explicit_series)

    if explicit_brand:
        enriched["brand_source"] = "user_input"
    if explicit_series:
        enriched["series_source"] = "user_input"

    if inferred.get("ok"):
        parser_result = inferred.get("parser_result") if isinstance(inferred.get("parser_result"), dict) else {}
        inferred_brand = str(inferred.get("brand") or "").strip()
        inferred_series = str(inferred.get("series") or "").strip()
        if explicit_brand and inferred_brand and explicit_brand != inferred_brand:
            errors.append("MODEL_BRAND_SERIES_CONFLICT")
        if explicit_series and inferred_series and explicit_series != inferred_series:
            errors.append("MODEL_BRAND_SERIES_CONFLICT")
        if not explicit_brand and inferred_brand:
            enriched["brand"] = inferred_brand
            enriched["brand_source"] = "inferred_from_model_text"
        if not explicit_series and inferred_series:
            enriched["series"] = inferred_series
            enriched["series_source"] = "inferred_from_model_text"
        if parser_result:
            if parser_result.get("config_model"):
                enriched["config_model"] = str(parser_result["config_model"])
            if parser_result.get("raw_config_model"):
                enriched["raw_config_model"] = str(parser_result["raw_config_model"])
            if parser_result.get("matched_alias"):
                enriched["vehicle_parser_matched_alias"] = str(parser_result["matched_alias"])
            if parser_result.get("decision_code"):
                enriched["vehicle_model_decision_code"] = str(parser_result["decision_code"])
            if parser_result.get("vehicle_model_identity_key"):
                enriched["vehicle_model_identity_key"] = str(parser_result["vehicle_model_identity_key"])
            if parser_result.get("vehicle_model_identity_key_v2"):
                enriched["vehicle_model_identity_key_v2"] = str(parser_result["vehicle_model_identity_key_v2"])
            if parser_result.get("vehicle_model_identity_key_v2_scope"):
                enriched["vehicle_model_identity_key_v2_scope"] = str(parser_result["vehicle_model_identity_key_v2_scope"])
            for canonical_field in (
                "canonical_brand",
                "canonical_series",
                "canonical_year_model",
                "canonical_config_model",
                "config_semantic_key",
                "config_semantic_version",
            ):
                if parser_result.get(canonical_field) is not None:
                    enriched[canonical_field] = str(parser_result[canonical_field])
            enriched["vehicle_parser_version"] = PARSER_VERSION
        enriched["model_parse_source"] = "conflict" if errors else _draft_model_parse_source(parser_result)
    else:
        if not explicit_brand or not explicit_series:
            errors.append(str(inferred.get("error") or "MODEL_BRAND_SERIES_UNRESOLVED"))
        enriched["model_parse_source"] = "user_input" if explicit_brand and explicit_series else "unresolved"

    if not enriched.get("brand") or not enriched.get("series"):
        if MODEL_STRICT_TEMPLATE_INCOMPLETE not in errors and "MODEL_BRAND_SERIES_UNRESOLVED" not in errors:
            errors.append("MODEL_BRAND_SERIES_UNRESOLVED")

    return enriched, sorted(set(errors)), warnings


def enrich_registration_date(fields: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize Feishu registration date aliases before confirmation."""
    enriched = dict(fields)
    raw_value = (
        enriched.get("license_date")
        or enriched.get("register_date")
        or enriched.get("registration_date")
        or ""
    )
    if not str(raw_value or "").strip():
        return enriched, []

    normalized = normalize_registration_date(raw_value)
    if normalized is None:
        return enriched, ["REGISTRATION_DATE_UNRECOGNIZED"]

    enriched["license_date_raw"] = normalized.raw_value
    enriched["license_date"] = normalized.normalized_date
    enriched["register_date"] = normalized.normalized_date
    enriched["registration_date"] = normalized.normalized_date
    enriched["register_year"] = normalized.year
    enriched["registration_date_year"] = normalized.year
    enriched["registration_date_month"] = normalized.month
    return enriched, []


def _parse_first_number(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def _parse_first_int(value: Any) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _normalize_year_month_day(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    text = raw.replace("\uff0e", ".").replace("\uff0d", "-").replace("\uff0f", "/")
    text = text.replace("年", ".").replace("月", ".").replace("日", "")
    text = re.sub(r"\s+", "", text)
    match = re.fullmatch(r"(?P<year>\d{2}|\d{4})[.\-/](?P<month>\d{1,2})(?:[.\-/](?P<day>\d{1,2}))?", text)
    if not match:
        return None
    year = int(match.group("year"))
    month = int(match.group("month"))
    day_text = match.group("day")
    if year < 100:
        year += 2000
    if year < 1900 or year > 2099 or month < 1 or month > 12:
        return None
    if day_text is None:
        return f"{year:04d}.{month:02d}"
    day = int(day_text)
    if day < 1 or day > 31:
        return None
    return f"{year:04d}.{month:02d}.{day:02d}"


def enrich_target_source_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Add normalized audit fields without changing legacy draft field names."""
    enriched = dict(fields)
    model_text = str(enriched.get("raw_model_text") or enriched.get("model_config") or "").strip()
    if model_text:
        enriched["raw_model_text"] = model_text
        enriched["vehicle_model"] = model_text
        enriched["full_model"] = model_text

    mileage = _parse_first_number(enriched.get("mileage_text"))
    if mileage is not None:
        enriched["mileage_10k_km"] = mileage

    transfer_count = _parse_first_int(enriched.get("transfer_count_text"))
    if transfer_count is not None:
        enriched["transfer_count"] = transfer_count

    guide_price = _parse_first_number(enriched.get("guide_price_text"))
    if guide_price is not None:
        enriched["guide_price_wan"] = guide_price

    insurance_expire = _normalize_year_month_day(enriched.get("insurance_expiry"))
    if insurance_expire:
        enriched["insurance_expire_date"] = insurance_expire

    sunroof = str(enriched.get("sunroof_text") or "").strip()
    if sunroof:
        enriched["sunroof"] = sunroof

    emission = str(enriched.get("emission_standard") or "").strip()
    if emission:
        enriched["energy_type"] = emission

    return enriched


def build_target_task_draft(
    *,
    task_id: str,
    fields: dict[str, Any],
    status: str,
    raw_message_id: str | None,
    raw_sender_id: str | None,
    raw_chat_id: str | None,
    created_at: str,
) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "task_id": task_id,
        "source": "feishu",
        "status": status,
    }
    for field in DRAFT_FIELD_ORDER + MODEL_META_FIELDS + REGISTRATION_DATE_META_FIELDS:
        if field in fields:
            draft[field] = fields[field]
    if raw_message_id:
        draft["raw_message_id"] = raw_message_id
    if raw_sender_id:
        draft["raw_sender_id"] = raw_sender_id
    if raw_chat_id:
        draft["raw_chat_id"] = raw_chat_id
    draft["created_at"] = created_at
    return draft


def parse_target_task_message(
    text: str,
    *,
    task_id: str,
    raw_message_id: str | None = None,
    raw_sender_id: str | None = None,
    raw_chat_id: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ParsedTargetTaskMessage:
    fields = parse_template_fields(text)
    fields, date_errors = enrich_registration_date(fields)
    fields, model_resolution_errors, warnings = enrich_model_identity(fields)
    fields = enrich_target_source_fields(fields)
    created_at = _now_iso(clock)
    missing_fields = missing_required_fields(fields)
    valid = not missing_fields and not model_resolution_errors and not date_errors
    status_value = (
        "WAITING_TARGET_CONFIRMATION"
        if valid
        else DATE_UNRESOLVED_STATUS
        if date_errors
        else MODEL_UNRESOLVED_STATUS
        if model_resolution_errors
        else "INVALID"
    )
    draft = build_target_task_draft(
        task_id=task_id,
        fields=fields,
        status=status_value,
        raw_message_id=raw_message_id,
        raw_sender_id=raw_sender_id,
        raw_chat_id=raw_chat_id,
        created_at=created_at,
    )
    validation_result = {
        "task_id": task_id,
        "valid": valid,
        "missing_required_fields": missing_fields,
        "date_errors": date_errors,
        "model_resolution_errors": model_resolution_errors,
        "warnings": warnings,
        "created_at": created_at,
    }
    status = {
        "task_id": task_id,
        "status": status_value,
        "source": "feishu",
        "created_at": created_at,
        "updated_at": created_at,
    }
    if raw_message_id:
        status["raw_message_id"] = raw_message_id
    if raw_sender_id:
        status["raw_sender_id"] = raw_sender_id
    if raw_chat_id:
        status["raw_chat_id"] = raw_chat_id
    reply_text = (
        format_draft_confirmation_reply(draft)
        if valid
        else format_registration_date_resolution_reply()
        if date_errors
        else format_model_resolution_reply(model_resolution_errors, fields)
        if model_resolution_errors
        else format_missing_fields_reply(missing_fields)
    )
    return ParsedTargetTaskMessage(
        task_id=task_id,
        fields=fields,
        draft=draft,
        validation_result=validation_result,
        status=status,
        reply_text=reply_text,
    )


def format_draft_confirmation_reply(draft: dict[str, Any]) -> str:
    lines = [
        "请确认目标车信息：",
        "",
    ]
    for field in DISPLAY_ORDER:
        value = draft.get(field)
        if value is None or value == "":
            continue
        suffix = ""
        if field in {"brand", "series"} and draft.get(f"{field}_source") == "inferred_from_model_text":
            suffix = "（系统识别）"
        lines.append(f"{FIELD_DISPLAY_NAMES[field]}：{value}{suffix}")
    lines.extend(
        [
            "",
            "确认无误请回复：确认",
            "如需修改，请重新发送完整目标车源信息。",
        ]
    )
    return "\n".join(lines)


def format_model_resolution_reply(model_errors: list[str], fields: dict[str, str]) -> str:
    if MODEL_STRICT_TEMPLATE_INCOMPLETE in model_errors:
        return "\n".join(
            [
                "已识别到目标车信息，但车型字段格式不完整。",
                "",
                "请按以下格式填写：",
                "【车型】 品牌 车系 年款 配置",
                "",
                "例如：",
                "【车型】 别克 君越 2021款 652T 豪华型",
            ]
        )
    if "MODEL_BRAND_SERIES_CONFLICT" in model_errors:
        return "\n".join(
            [
                "已识别到目标车信息，但填写的品牌/车系与车型字段识别结果不一致。",
                "",
                "请检查品牌、车系和车型字段后重新发送。",
            ]
        )
    if "MODEL_BRAND_SERIES_AMBIGUOUS" in model_errors:
        return "\n".join(
            [
                "已识别到目标车信息，但车型字段同时命中多个车系，无法确定品牌/车系。",
                "",
                "请补充完整车型名称，例如：",
                "雪佛兰 科鲁泽 2022款 320自动悦享天窗版",
            ]
        )
    return "\n".join(
        [
            "已识别到目标车信息，但车型字段无法确定品牌/车系。",
            "",
            "请补充完整车型名称，例如：",
            "雪佛兰 科鲁泽 2022款 320自动悦享天窗版",
        ]
    )


def format_registration_date_resolution_reply() -> str:
    return "\n".join(
        [
            "\u5df2\u8bc6\u522b\u5230\u76ee\u6807\u8f66\u4fe1\u606f\uff0c\u4f46\u4e0a\u724c\u65e5\u671f\u65e0\u6cd5\u8bc6\u522b\u3002",
            "",
            "\u8bf7\u6309 2022.08 \u6216 22.8 \u683c\u5f0f\u586b\u5199\u4e0a\u724c\u65e5\u671f\u540e\u91cd\u65b0\u53d1\u9001\u76ee\u6807\u8f66\u4fe1\u606f\u3002",
        ]
    )


def format_missing_fields_reply(missing_fields: list[str]) -> str:
    lines = [
        "任务未生成，缺少以下必填字段：",
        "",
    ]
    lines.extend(f"{index}. {field}" for index, field in enumerate(missing_fields, 1))
    lines.extend(
        [
            "",
            "请补充后重新发送完整定价模板。",
        ]
    )
    return "\n".join(lines)
