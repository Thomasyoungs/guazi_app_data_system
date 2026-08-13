"""Offline rule-source sync check for the Guazi pricing project."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any


def _load_v33_expected_rule_sources() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "config" / "v33_desktop_rule_source_files.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


_EXPECTED_RULE_SOURCES = _load_v33_expected_rule_sources()
EXPECTED_DATA_FLOW_VERSION = (_EXPECTED_RULE_SOURCES.get("page") or {}).get("version", "V1.47")
EXPECTED_DATA_FLOW_FILE = (_EXPECTED_RULE_SOURCES.get("page") or {}).get("name", "")
EXPECTED_SCORING_RULE_VERSION = (_EXPECTED_RULE_SOURCES.get("scoring") or {}).get("version", "V1.11")
EXPECTED_SCORING_FILE = (_EXPECTED_RULE_SOURCES.get("scoring") or {}).get("name", "")
EXPECTED_REFERENCE_SELECTION_RULE = (_EXPECTED_RULE_SOURCES.get("reference") or {}).get(
    "version", "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
)
EXPECTED_PRICING_RULE_VERSION = (_EXPECTED_RULE_SOURCES.get("pricing") or {}).get(
    "version", "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
)
EXPECTED_PRICING_FILE = (_EXPECTED_RULE_SOURCES.get("pricing") or {}).get("name", "")
EXPECTED_COMPETITION_COEFFICIENT_VERSION = (_EXPECTED_RULE_SOURCES.get("competition") or {}).get("version", "V1.2.6")
EXPECTED_COMPETITION_COEFFICIENT_FILE = (_EXPECTED_RULE_SOURCES.get("competition") or {}).get("name", "")
EXPECTED_EARLY_EXIT_RULE_ID = (_EXPECTED_RULE_SOURCES.get("early_exit") or {}).get(
    "id", "V33_S14_LOW_SCORE_UPPER_BOUND_SKIP_AND_RECOLLECT_CONTRACT"
)
EXPECTED_PROFIT_RATE = 0.08
EXPECTED_PRODUCTION_ADB_SERIAL = "6TGYHPZCETCSK6L"
EXPECTED_PRODUCTION_ADB_ALIAS = "Redmi Note 12 5G"
EXPECTED_SERVICE_FEE_TIERS = [
    {"min_price_yuan": 200000, "service_fee_yuan": 5000},
    {"min_price_yuan": 150000, "service_fee_yuan": 4000},
    {"min_price_yuan": 100000, "service_fee_yuan": 3500},
    {"min_price_yuan": 50000, "service_fee_yuan": 3000},
    {"min_price_yuan": 0, "service_fee_yuan": 2500},
]

LEGACY_ALLOWED_CONTEXTS = {
    "DEBUG_ONLY_ALLOWED",
    "FORBIDDEN_ACTION_DETECTOR_ALLOWED",
    "NEGATIVE_TEST_FIXTURE_ALLOWED",
    "HISTORICAL_EVIDENCE_ALLOWED",
}

LEGACY_RUNTIME_EXECUTION_FIELD_RE = re.compile(
    r"\b("
    r"click_source|"
    r"s11_report_entry_click_source|"
    r"binding_source|"
    r"click_target_source|"
    r"action_algorithm_used|"
    r"used_action_algorithm|"
    r"fallback_name|"
    r"selected_handle_source|"
    r"target_x_algorithm|"
    r"pricing_calculation|"
    r"live_runtime_branch"
    r")\b",
    re.IGNORECASE,
)


def _parse_rule_sync_yaml_subset(text: str) -> dict[str, Any]:
    """Parse the small config YAML subset used by rule sync checks."""

    data: dict[str, Any] = {}
    current_section: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.split("#", 1)[0].rstrip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            if indent == 0:
                data[key] = {}
                current_section = key
            continue
        parsed_value: Any
        unquoted = value.strip().strip('"').strip("'")
        lowered = unquoted.lower()
        if lowered == "true":
            parsed_value = True
        elif lowered == "false":
            parsed_value = False
        else:
            parsed_value = unquoted
        if indent > 0 and current_section:
            section = data.setdefault(current_section, {})
            if isinstance(section, dict):
                section[key] = parsed_value
        else:
            data[key] = parsed_value
            current_section = None
    return data


def _contains_expected_production_alias(value: Any) -> bool:
    return EXPECTED_PRODUCTION_ADB_ALIAS in str(value or "")


def validate_adb_target_device_config(config_text: str, *, backup_exists: bool = False) -> list[str]:
    """Validate production target or explicit temporary ADB device override."""

    errors: list[str] = []
    config = _parse_rule_sync_yaml_subset(config_text)
    active_serial = str(config.get("active_adb_serial") or "").strip()
    device_alias = str(config.get("device_alias") or "").strip()
    strict_device_selection = config.get("strict_device_selection")
    allow_default_when_single_device = config.get("allow_default_when_single_device")
    override = config.get("temporary_device_override")
    override_enabled = isinstance(override, dict) and override.get("enabled") is True

    if strict_device_selection is not True:
        errors.append("ADB_TARGET_DEVICE_STRICT_SELECTION_NOT_TRUE")
    if allow_default_when_single_device is not False:
        errors.append("ADB_TARGET_DEVICE_ALLOW_DEFAULT_NOT_FALSE")
    if not active_serial:
        errors.append("ADB_TARGET_DEVICE_ACTIVE_SERIAL_EMPTY")

    if not override_enabled:
        if active_serial != EXPECTED_PRODUCTION_ADB_SERIAL or not _contains_expected_production_alias(device_alias):
            errors.append("ADB_TARGET_DEVICE_TEMPORARY_OVERRIDE_REQUIRED")
        return errors

    if not backup_exists:
        errors.append("ADB_TARGET_DEVICE_TEMPORARY_OVERRIDE_BACKUP_MISSING")

    production = config.get("production_device")
    if not isinstance(production, dict):
        errors.append("ADB_TARGET_DEVICE_TEMPORARY_OVERRIDE_PRODUCTION_DEVICE_MISSING")
        production = {}
    if str(production.get("active_adb_serial") or "").strip() != EXPECTED_PRODUCTION_ADB_SERIAL:
        errors.append("ADB_TARGET_DEVICE_PRODUCTION_SERIAL_NOT_REDMI_NOTE_12")
    if not _contains_expected_production_alias(production.get("device_alias")):
        errors.append("ADB_TARGET_DEVICE_PRODUCTION_ALIAS_NOT_REDMI_NOTE_12")

    temporary_serial = str(override.get("temporary_adb_serial") or "").strip()
    if not temporary_serial:
        errors.append("ADB_TARGET_DEVICE_TEMPORARY_SERIAL_EMPTY")
    if active_serial != temporary_serial:
        errors.append("ADB_TARGET_DEVICE_ACTIVE_SERIAL_NOT_TEMPORARY_SERIAL")
    if str(override.get("original_adb_serial") or "").strip() != EXPECTED_PRODUCTION_ADB_SERIAL:
        errors.append("ADB_TARGET_DEVICE_TEMPORARY_ORIGINAL_SERIAL_NOT_REDMI_NOTE_12")
    if not _contains_expected_production_alias(override.get("original_device_alias")):
        errors.append("ADB_TARGET_DEVICE_TEMPORARY_ORIGINAL_ALIAS_NOT_REDMI_NOTE_12")
    if not str(override.get("reason") or "").strip():
        errors.append("ADB_TARGET_DEVICE_TEMPORARY_OVERRIDE_REASON_MISSING")
    if str(config.get("adb_runtime_env_mode") or "").strip() != "user_shell":
        errors.append("ADB_TARGET_DEVICE_TEMPORARY_OVERRIDE_ENV_MODE_NOT_USER_SHELL")
    if config.get("use_isolated_adb_home") is not False:
        errors.append("ADB_TARGET_DEVICE_TEMPORARY_OVERRIDE_ISOLATED_HOME_NOT_FALSE")
    return errors


def _normalize_service_fee_tiers(value: Any) -> list[dict[str, int]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, int]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        try:
            rows.append(
                {
                    "min_price_yuan": int(row["min_price_yuan"]),
                    "service_fee_yuan": int(row["service_fee_yuan"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(rows, key=lambda item: item["min_price_yuan"], reverse=True)


def _service_fee_for_price(price_yuan: int, tiers: list[dict[str, int]]) -> int | None:
    for row in _normalize_service_fee_tiers(tiers):
        if price_yuan >= row["min_price_yuan"]:
            return row["service_fee_yuan"]
    return None


def _load_desktop_compiled_rule(path: Path, errors: list[str]) -> dict[str, Any]:
    if not path.exists():
        errors.append("DESKTOP_RULE_COMPILED_MISSING")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        errors.append("DESKTOP_RULE_COMPILED_JSON_INVALID")
        return {}


def _check_desktop_compiled_pricing_rule(
    *,
    root: Path,
    compiled: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> list[dict[str, int]]:
    pricing_rule = compiled.get("pricing_rule") if isinstance(compiled, dict) else {}
    if not isinstance(pricing_rule, dict):
        errors.append("DESKTOP_RULE_COMPILED_PRICING_RULE_MISSING")
        return EXPECTED_SERVICE_FEE_TIERS
    tiers = _normalize_service_fee_tiers(pricing_rule.get("guazi_service_fee_tiers"))
    if tiers != EXPECTED_SERVICE_FEE_TIERS:
        errors.append(
            "RULE_SOURCE_CONTENT_MISMATCH:desktop_rule_compiled.pricing_rule.guazi_service_fee_tiers"
        )
    if pricing_rule.get("profit_rate") != EXPECTED_PROFIT_RATE:
        errors.append("RULE_SOURCE_CONTENT_MISMATCH:desktop_rule_compiled.pricing_rule.profit_rate")
    source_file = str(pricing_rule.get("source_file") or "")
    file_sha256 = str(pricing_rule.get("file_sha256") or "")
    if not source_file:
        errors.append("DESKTOP_RULE_COMPILED_PRICING_SOURCE_FILE_MISSING")
    else:
        source_path = root.parent / source_file
        if not source_path.exists():
            errors.append("DESKTOP_RULE_COMPILED_PRICING_SOURCE_FILE_NOT_FOUND")
        elif hashlib.sha256(source_path.read_bytes()).hexdigest() != file_sha256:
            errors.append("DESKTOP_RULE_COMPILED_PRICING_SOURCE_SHA256_MISMATCH")
    vectors = pricing_rule.get("expected_test_vectors") or []
    for vector in vectors:
        if not isinstance(vector, dict):
            continue
        expected = vector.get("service_fee_yuan")
        actual = _service_fee_for_price(int(vector.get("price_yuan")), tiers)
        if actual != expected:
            errors.append(
                f"RULE_SOURCE_CONTENT_MISMATCH:desktop_vector_{vector.get('price_yuan')}:expected={expected}:actual={actual}"
            )
    # Some page-flow docs mention historical fee tables; report but keep S16 authority on the pricing doc.
    for doc in root.parent.glob("*.docx"):
        if doc.name == source_file or doc.name.startswith("~$"):
            continue
        try:
            raw = doc.read_bytes()
        except OSError:
            continue
        if b"1500" in raw or b"3500" in raw:
            warnings.append(f"DESKTOP_NON_PRICING_DOC_MAY_CONTAIN_SERVICE_FEE_TEXT:{doc.name}")
    return tiers or EXPECTED_SERVICE_FEE_TIERS


def _load_legacy_rule_allowlist(root: Path, errors: list[str]) -> list[dict[str, Any]]:
    allowlist_path = root / "config" / "legacy_rule_allowlist.yaml"
    if not allowlist_path.exists():
        errors.append("LEGACY_RULE_ALLOWLIST_MISSING")
        return []
    try:
        payload = json.loads(allowlist_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        errors.append("LEGACY_RULE_ALLOWLIST_JSON_INVALID")
        return []
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        errors.append("LEGACY_RULE_ALLOWLIST_ENTRIES_MISSING")
        return []
    required_fields = (
        "legacy_rule_id",
        "file_pattern",
        "allowed_context",
        "reason",
        "max_occurrences",
        "runtime_reachable",
        "expires_when",
        "owner",
        "related_test",
    )
    valid_entries: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"LEGACY_RULE_ALLOWLIST_INVALID_ENTRY:{index}:not_object")
            continue
        missing = [field for field in required_fields if field not in entry or entry.get(field) in (None, "")]
        if missing:
            errors.append(f"LEGACY_RULE_ALLOWLIST_INVALID_ENTRY:{index}:missing:{','.join(missing)}")
        if entry.get("allowed_context") not in LEGACY_ALLOWED_CONTEXTS - {"HISTORICAL_EVIDENCE_ALLOWED"}:
            errors.append(f"LEGACY_RULE_ALLOWLIST_INVALID_CONTEXT:{index}:{entry.get('allowed_context')}")
        if entry.get("runtime_reachable") is not False:
            errors.append(f"LEGACY_RULE_ALLOWLIST_RUNTIME_REACHABLE_NOT_FALSE:{index}")
        try:
            max_occurrences = int(entry.get("max_occurrences"))
            if max_occurrences <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"LEGACY_RULE_ALLOWLIST_MAX_OCCURRENCES_INVALID:{index}")
        valid_entries.append({**entry, "_allowlist_index": index})
    return valid_entries


def _legacy_line_for_match(text: str, start: int, end: int) -> tuple[int, str]:
    line_number = text.count("\n", 0, start) + 1
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    return line_number, text[line_start:line_end].strip()


def _allowlist_matches(entry: dict[str, Any], *, rule_id: str, rel: str) -> bool:
    if str(entry.get("legacy_rule_id") or "") != rule_id:
        return False
    pattern = str(entry.get("file_pattern") or "")
    return fnmatch.fnmatch(rel, pattern)


def _legacy_hit_runtime_reachable(line: str) -> bool:
    return bool(LEGACY_RUNTIME_EXECUTION_FIELD_RE.search(line))


def _allowlist_forbids_line(entry: dict[str, Any], line: str) -> str:
    for marker in entry.get("forbidden_if_contains") or []:
        marker_text = str(marker or "")
        if marker_text and marker_text in line:
            return marker_text
    return ""


def _classify_legacy_hit(
    *,
    rule: dict[str, Any],
    rel: str,
    line_number: int,
    line_text: str,
    matched_text: str,
    historical_only: bool,
    test_only: bool,
    allowlist_entries: list[dict[str, Any]],
    allowlist_counts: dict[int, int],
) -> dict[str, Any]:
    rule_id = str(rule.get("id") or "")
    if historical_only:
        return {
            "legacy_rule_id": rule_id,
            "file": rel,
            "line": line_number,
            "matched_text": matched_text,
            "classification": "HISTORICAL_EVIDENCE_ALLOWED",
            "allowed": True,
            "reason": "hit is inside evidence/backup/report historical material",
            "runtime_reachable": False,
            "action_required": "none",
        }

    matching_entries = [
        entry for entry in allowlist_entries if _allowlist_matches(entry, rule_id=rule_id, rel=rel)
    ]
    line_runtime_reachable = False if test_only else _legacy_hit_runtime_reachable(line_text)
    if not matching_entries:
        return {
            "legacy_rule_id": rule_id,
            "file": rel,
            "line": line_number,
            "matched_text": matched_text,
            "classification": "UNKNOWN_MUST_FAIL",
            "allowed": False,
            "reason": "legacy blacklist hit has no precise allowlist entry",
            "runtime_reachable": line_runtime_reachable,
            "action_required": "add precise allowlist with runtime_reachable=false or remove legacy residue",
        }

    entry = matching_entries[0]
    index = int(entry.get("_allowlist_index", -1))
    allowlist_counts[index] = allowlist_counts.get(index, 0) + 1
    allowed_context = str(entry.get("allowed_context") or "UNKNOWN_MUST_FAIL")
    runtime_reachable = line_runtime_reachable
    forbidden_marker = _allowlist_forbids_line(entry, line_text)
    allowed = True
    reason = str(entry.get("reason") or "")
    action_required = "none"
    if forbidden_marker:
        allowed = False
        reason = f"allowlist forbidden marker appears in hit line: {forbidden_marker}"
        action_required = "remove forbidden runtime binding residue"
    if runtime_reachable and allowed_context not in {
        "FORBIDDEN_ACTION_DETECTOR_ALLOWED",
        "NEGATIVE_TEST_FIXTURE_ALLOWED",
    }:
        allowed = False
        reason = "legacy term appears in a runtime execution field outside a detector/negative-test context"
        action_required = "remove active runtime execution residue"
    if test_only and allowed_context != "NEGATIVE_TEST_FIXTURE_ALLOWED":
        allowed = False
        reason = "tests must classify legacy terms as NEGATIVE_TEST_FIXTURE_ALLOWED"
        action_required = "fix allowlist classification"
    if not test_only and allowed_context == "NEGATIVE_TEST_FIXTURE_ALLOWED":
        allowed = False
        reason = "negative-test allowlist cannot cover active code/config"
        action_required = "use a detector/debug allowlist or remove residue"
    return {
        "legacy_rule_id": rule_id,
        "file": rel,
        "line": line_number,
        "matched_text": matched_text,
        "classification": allowed_context if allowed else "ACTIVE_EXECUTION_PATH_MUST_FIX",
        "allowed": allowed,
        "reason": reason,
        "runtime_reachable": runtime_reachable,
        "action_required": action_required,
        "allowlist_index": index,
    }


def _scan_legacy_rule_blacklist(root: Path, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    blacklist_path = root / "config" / "legacy_rule_blacklist.yaml"
    if not blacklist_path.exists():
        errors.append("LEGACY_RULE_BLACKLIST_MISSING")
        return {"findings": [], "legacy_errors": [], "legacy_allowlisted_warnings": [], "historical_warnings": []}
    try:
        blacklist = json.loads(blacklist_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        errors.append("LEGACY_RULE_BLACKLIST_JSON_INVALID")
        return {"findings": [], "legacy_errors": [], "legacy_allowlisted_warnings": [], "historical_warnings": []}
    rules = blacklist.get("rules") if isinstance(blacklist, dict) else []
    scan_roots = blacklist.get("active_scan_roots") if isinstance(blacklist, dict) else []
    historical_markers = tuple(str(item) for item in (blacklist.get("historical_path_markers") or []))
    allowlist_entries = _load_legacy_rule_allowlist(root, errors)
    allowlist_counts: dict[int, int] = {}
    findings: list[dict[str, Any]] = []
    legacy_errors: list[dict[str, Any]] = []
    allowlisted_warnings: list[dict[str, Any]] = []
    historical_warnings: list[dict[str, Any]] = []
    excluded_files = {
        str((root / "config" / "legacy_rule_blacklist.yaml").resolve()),
        str((root / "config" / "legacy_rule_allowlist.yaml").resolve()),
        str((root / "config" / "desktop_rule_compiled.json").resolve()),
        str((root / "scripts" / "rule_source_sync_check.py").resolve()),
    }
    for scan_root in scan_roots:
        base = root / str(scan_root)
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            resolved = str(path.resolve())
            if resolved in excluded_files:
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            if path.suffix.lower() in {".pyc", ".png", ".jpg", ".jpeg", ".zip", ".docx"}:
                continue
            if rel.startswith("tests/") and path.suffix.lower() in {".txt", ".log", ".out", ".diff"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            historical_only = any(marker and marker in rel for marker in historical_markers)
            test_only = rel.startswith("tests/")
            for rule in rules or []:
                if not isinstance(rule, dict):
                    continue
                pattern = str(rule.get("pattern") or "")
                if not pattern:
                    continue
                for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                    line_number, line_text = _legacy_line_for_match(text, match.start(), match.end())
                    matched_text = match.group(0)
                    finding = {
                        **_classify_legacy_hit(
                            rule=rule,
                            rel=rel,
                            line_number=line_number,
                            line_text=line_text,
                            matched_text=matched_text,
                            historical_only=historical_only,
                            test_only=test_only,
                            allowlist_entries=allowlist_entries,
                            allowlist_counts=allowlist_counts,
                        ),
                        "old_rule_type": rule.get("id"),
                        "active_or_test_or_historical": "historical_only"
                        if historical_only
                        else "test_only"
                        if test_only
                        else "active",
                        "must_fix": False,
                    }
                    finding["must_fix"] = not bool(finding.get("allowed"))
                    findings.append(finding)
                    if finding["must_fix"]:
                        legacy_errors.append(finding)
                        errors.append(
                            "LEGACY_RULE_ACTIVE_RESIDUE_FAILED:"
                            f"{finding['legacy_rule_id']}:{rel}:{line_number}:{finding['classification']}"
                        )
                    elif finding["classification"] == "HISTORICAL_EVIDENCE_ALLOWED":
                        historical_warnings.append(finding)
                        warnings.append(
                            f"LEGACY_RULE_HISTORICAL_WARNING:{finding['legacy_rule_id']}:{rel}:{line_number}"
                        )
                    else:
                        allowlisted_warnings.append(finding)
                        warnings.append(
                            "LEGACY_RULE_ALLOWLISTED_WARNING:"
                            f"{finding['legacy_rule_id']}:{rel}:{line_number}:{finding['classification']}"
                        )
    for entry in allowlist_entries:
        index = int(entry.get("_allowlist_index", -1))
        actual = allowlist_counts.get(index, 0)
        try:
            maximum = int(entry.get("max_occurrences"))
        except (TypeError, ValueError):
            continue
        if actual > maximum:
            errors.append(
                "LEGACY_RULE_ALLOWLIST_MAX_OCCURRENCES_EXCEEDED:"
                f"{entry.get('legacy_rule_id')}:{entry.get('file_pattern')}:actual={actual}:max={maximum}"
            )
    return {
        "findings": findings,
        "legacy_errors": legacy_errors,
        "legacy_allowlisted_warnings": allowlisted_warnings,
        "historical_warnings": historical_warnings,
    }


def check_rule_source_sync(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root)
    errors: list[str] = []
    warnings: list[str] = []

    manifest_path = root / "config" / "rule_manifest.json"
    pages_path = root / "config" / "pages.yaml"
    fields_path = root / "config" / "fields.yaml"
    pricing_path = root / "src" / "guazi_app_data_system" / "pricing.py"
    runtime_path = root / "scripts" / "runtime_s10_to_s16_mainline.py"
    runtime_s01_path = root / "scripts" / "runtime_s01_to_s10_mainline.py"
    collector_path = root / "scripts" / "pricing_result_collector.py"
    formatter_path = root / "scripts" / "feishu_result_formatter.py"
    gateway_path = root / "scripts" / "feishu_gateway.py"
    realtime_receiver_path = root / "scripts" / "feishu_realtime_receiver.py"
    dispatcher_path = root / "scripts" / "feishu_pricing_dispatcher.py"
    message_parser_path = root / "scripts" / "feishu_message_to_target_task.py"
    registration_normalizer_path = root / "scripts" / "registration_date_normalizer.py"
    current_task_builder_path = root / "scripts" / "current_target_task_builder.py"
    target_info_feedback_path = root / "scripts" / "target_info_correction_feedback.py"
    admin_intervention_router_path = root / "scripts" / "admin_intervention_router.py"
    system_health_preflight_path = root / "scripts" / "system_health_preflight.py"
    start_listener_path = root / "scripts" / "start_feishu_listener.ps1"
    start_dispatcher_path = root / "scripts" / "start_feishu_dispatcher.ps1"
    service_single_instance_path = root / "scripts" / "feishu_service_single_instance.ps1"
    adb_target_config_path = root / "config" / "adb_target_device.yaml"
    adb_target_helper_path = root / "scripts" / "adb_target_device.py"
    adb_target_src_path = root / "src" / "guazi_app_data_system" / "adb_target_device.py"
    app_startup_path = root / "src" / "guazi_app_data_system" / "app_startup.py"
    adb_device_gate_path = root / "src" / "guazi_app_data_system" / "adb_device_gate.py"
    series_aliases_path = root / "config" / "feishu_series_brand_aliases.json"
    runner_path = root / "scripts" / "pricing_runner.py"
    task_store_path = root / "scripts" / "feishu_task_store.py"
    event_adapter_path = root / "scripts" / "feishu_event_adapter.py"
    group_bindings_path = root / "scripts" / "feishu_group_bindings.py"
    roles_path = root / "config" / "feishu_roles.yaml"
    group_bindings_data_path = root / "data" / "feishu_group_bindings.json"
    agents_path = root / "AGENTS.md"
    rule_source_manifest_path = root / "docs" / "rule_source_manifest.md"
    desktop_compiled_path = root / "config" / "desktop_rule_compiled.json"
    legacy_blacklist_findings: list[dict[str, Any]] = []
    legacy_errors: list[dict[str, Any]] = []
    legacy_allowlisted_warnings: list[dict[str, Any]] = []
    historical_warnings: list[dict[str, Any]] = []

    desktop_compiled = _load_desktop_compiled_rule(desktop_compiled_path, errors)
    expected_service_fee_tiers = _check_desktop_compiled_pricing_rule(
        root=root,
        compiled=desktop_compiled,
        errors=errors,
        warnings=warnings,
    )
    legacy_scan = _scan_legacy_rule_blacklist(root, errors, warnings)
    legacy_blacklist_findings = list(legacy_scan.get("findings") or [])
    legacy_errors = list(legacy_scan.get("legacy_errors") or [])
    legacy_allowlisted_warnings = list(legacy_scan.get("legacy_allowlisted_warnings") or [])
    historical_warnings = list(legacy_scan.get("historical_warnings") or [])

    manifest: dict[str, Any] = {}
    if not manifest_path.exists():
        errors.append("RULE_MANIFEST_MISSING")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        data_flow = manifest.get("data_flow_contract") or {}
        if data_flow.get("version") != EXPECTED_DATA_FLOW_VERSION:
            errors.append("DATA_FLOW_VERSION_NOT_V1_47")
        if data_flow.get("file") != EXPECTED_DATA_FLOW_FILE:
            errors.append("DATA_FLOW_FILE_NOT_LATEST_V1_47")
        source_dir = Path(str(data_flow.get("source_dir") or root.parent))
        if not (source_dir / EXPECTED_DATA_FLOW_FILE).exists():
            errors.append("DATA_FLOW_V1_47_SOURCE_FILE_MISSING")
        policy = manifest.get("rule_source_policy") or {}
        for key in (
            "folder_only",
            "chat_snippet_cannot_override",
            "require_latest_file_read_before_patch",
            "no_hot_reload_running_task",
        ):
            if policy.get(key) is not True:
                errors.append(f"RULE_SOURCE_POLICY_{key.upper()}_NOT_TRUE")
        sync_status = (manifest.get("sync_status") or {}).get("status")
        if sync_status not in {"PENDING_BEFORE_TEST", "PASSED"}:
            warnings.append("SYNC_STATUS_NOT_RECOGNIZED")
        pricing_rule = manifest.get("pricing_rule") or {}
        if pricing_rule.get("file") != EXPECTED_PRICING_FILE:
            errors.append("PRICING_RULE_FILE_NOT_CONFIRMED_CURRENT_SOURCE")
        if _normalize_service_fee_tiers(pricing_rule.get("guazi_service_fee_tiers")) != expected_service_fee_tiers:
            errors.append("PRICING_RULE_SERVICE_FEE_TIERS_MISMATCH")
        if pricing_rule.get("profit_rate") != EXPECTED_PROFIT_RATE:
            errors.append("PRICING_RULE_PROFIT_RATE_NOT_8_PERCENT")
        if "0.08" not in str(pricing_rule.get("profit_formula") or ""):
            errors.append("PRICING_RULE_PROFIT_FORMULA_NOT_8_PERCENT")
        if pricing_rule.get("net_payout_formula") != "target_guazi_listing_price_yuan - guazi_service_fee_yuan":
            errors.append("PRICING_RULE_NET_PAYOUT_FORMULA_MISMATCH")
        ignored_files = manifest.get("ignored_rule_source_files") or []
        bak_ignored = any(
            ".bak_before_s05_trim_scroll" in str(item.get("file") if isinstance(item, dict) else item)
            and "historical" in str(item)
            for item in ignored_files
        )
        if not bak_ignored:
            errors.append("BAK_BEFORE_S05_TRIM_SCROLL_NOT_MARKED_HISTORICAL_ONLY")
        scoring_rule = manifest.get("scoring_rule") or {}
        if scoring_rule.get("version") != EXPECTED_SCORING_RULE_VERSION:
            errors.append("SCORING_RULE_VERSION_NOT_V1_11")
        if scoring_rule.get("file") != EXPECTED_SCORING_FILE:
            errors.append("SCORING_RULE_FILE_NOT_V1_11_SOURCE")
        scoring_source_dir = Path(str((manifest.get("data_flow_contract") or {}).get("source_dir") or root.parent))
        if not (scoring_source_dir / EXPECTED_SCORING_FILE).exists():
            errors.append("SCORING_RULE_V1_11_SOURCE_FILE_MISSING")
        reference_rule = manifest.get("reference_selection_rule") or {}
        if reference_rule.get("version") != EXPECTED_REFERENCE_SELECTION_RULE:
            errors.append("REFERENCE_SELECTION_RULE_NOT_V3_3_BOUNDARY_PREVIOUS_RECOLLECT")
        if reference_rule.get("early_exit_rule_id") != EXPECTED_EARLY_EXIT_RULE_ID:
            errors.append("REFERENCE_EARLY_EXIT_RULE_ID_MISSING")
        if reference_rule.get("competition_coefficient_affects_selection") is not False:
            errors.append("COMPETITION_COEFFICIENT_STILL_AFFECTS_S15_SELECTION")
        competition_rule = manifest.get("competition_coefficient_rule") or {}
        if competition_rule.get("version") != EXPECTED_COMPETITION_COEFFICIENT_VERSION:
            errors.append("COMPETITION_COEFFICIENT_RULE_VERSION_NOT_V1_2_6")
        if competition_rule.get("file") != EXPECTED_COMPETITION_COEFFICIENT_FILE:
            errors.append("COMPETITION_COEFFICIENT_RULE_FILE_NOT_V1_2_6_SOURCE")
        if not (scoring_source_dir / EXPECTED_COMPETITION_COEFFICIENT_FILE).exists():
            errors.append("COMPETITION_COEFFICIENT_V1_2_6_SOURCE_FILE_MISSING")

    fields: dict[str, Any] = {}
    if not fields_path.exists():
        errors.append("FIELDS_CONFIG_MISSING")
    else:
        fields = json.loads(fields_path.read_text(encoding="utf-8-sig"))
        pricing_config = fields.get("pricing") or {}
        if _normalize_service_fee_tiers(pricing_config.get("guazi_service_fee_tiers")) != expected_service_fee_tiers:
            errors.append("FIELDS_SERVICE_FEE_TIERS_MISMATCH")
        if pricing_config.get("profit_rate") != EXPECTED_PROFIT_RATE:
            errors.append("FIELDS_PROFIT_RATE_NOT_8_PERCENT")
        scoring_config = fields.get("scoring") or {}
        if scoring_config.get("scoring_rule_version") != EXPECTED_SCORING_RULE_VERSION:
            errors.append("FIELDS_SCORING_RULE_VERSION_NOT_V1_11")
        if scoring_config.get("scoring_rule_doc") != EXPECTED_SCORING_FILE:
            errors.append("FIELDS_SCORING_RULE_DOC_NOT_V1_11_SOURCE")
        rule_source_guard = fields.get("rule_source_guard") or {}
        if rule_source_guard.get("active_page_contract_version") != EXPECTED_DATA_FLOW_VERSION:
            errors.append("FIELDS_ACTIVE_PAGE_CONTRACT_VERSION_NOT_V1_47")
        if rule_source_guard.get("active_page_contract_doc") != EXPECTED_DATA_FLOW_FILE:
            errors.append("FIELDS_ACTIVE_PAGE_CONTRACT_DOC_NOT_V1_47_SOURCE")
        if rule_source_guard.get("active_scoring_rule_version") != EXPECTED_SCORING_RULE_VERSION:
            errors.append("FIELDS_ACTIVE_SCORING_RULE_VERSION_NOT_V1_11")
        if rule_source_guard.get("active_scoring_rule_doc") != EXPECTED_SCORING_FILE:
            errors.append("FIELDS_ACTIVE_SCORING_RULE_DOC_NOT_V1_11_SOURCE")
        reference_config = fields.get("reference_selection") or {}
        if reference_config.get("reference_selection_rule") != EXPECTED_REFERENCE_SELECTION_RULE:
            errors.append("FIELDS_REFERENCE_SELECTION_NOT_V3_3_BOUNDARY_PREVIOUS_RECOLLECT")
        if reference_config.get("early_exit_rule_id") != EXPECTED_EARLY_EXIT_RULE_ID:
            errors.append("FIELDS_REFERENCE_EARLY_EXIT_RULE_ID_MISSING")
        if reference_config.get("competition_coefficient_affects_s15") is not False:
            errors.append("FIELDS_COMPETITION_COEFFICIENT_AFFECTS_S15_NOT_FALSE")
        pricing_config = fields.get("pricing") or {}
        if pricing_config.get("pricing_rule_version") != EXPECTED_PRICING_RULE_VERSION:
            errors.append("FIELDS_PRICING_RULE_VERSION_NOT_V3_3_BOUNDARY_PREVIOUS_RECOLLECT")
        if pricing_config.get("pricing_rule_doc") != EXPECTED_PRICING_FILE:
            errors.append("FIELDS_PRICING_RULE_DOC_NOT_V3_3_SOURCE")
        competition_config = fields.get("competition_coefficient") or {}
        if competition_config.get("competition_coefficient_version") != EXPECTED_COMPETITION_COEFFICIENT_VERSION:
            errors.append("FIELDS_COMPETITION_COEFFICIENT_VERSION_NOT_V1_2_6")
        if competition_config.get("competition_coefficient_doc") != EXPECTED_COMPETITION_COEFFICIENT_FILE:
            errors.append("FIELDS_COMPETITION_COEFFICIENT_DOC_NOT_V1_2_6_SOURCE")
        if rule_source_guard.get("active_pricing_rule_version") != EXPECTED_PRICING_RULE_VERSION:
            errors.append("FIELDS_ACTIVE_PRICING_RULE_VERSION_NOT_V3_3_BOUNDARY_PREVIOUS_RECOLLECT")
        if rule_source_guard.get("active_pricing_rule_doc") != EXPECTED_PRICING_FILE:
            errors.append("FIELDS_ACTIVE_PRICING_RULE_DOC_NOT_V3_3_SOURCE")
        if rule_source_guard.get("active_competition_coefficient_version") != EXPECTED_COMPETITION_COEFFICIENT_VERSION:
            errors.append("FIELDS_ACTIVE_COMPETITION_COEFFICIENT_VERSION_NOT_V1_2_6")
        if rule_source_guard.get("active_competition_coefficient_doc") != EXPECTED_COMPETITION_COEFFICIENT_FILE:
            errors.append("FIELDS_ACTIVE_COMPETITION_COEFFICIENT_DOC_NOT_V1_2_6_SOURCE")
        result_mapping = fields.get("result_field_mapping") or {}
        if result_mapping.get("read_order") != ["top_level", "s17_payload", "pricing", "confirmed_aliases"]:
            errors.append("FIELDS_RESULT_MAPPING_READ_ORDER_MISSING")
        if "FULL_CHAIN_MANUAL_REVIEW_DONE" not in result_mapping.get("manual_review_statuses", []):
            errors.append("FIELDS_RESULT_MAPPING_FULL_CHAIN_MANUAL_REVIEW_MISSING")
        aliases = result_mapping.get("aliases") or {}
        for field in ("final_reference_score", "final_reference_price_yuan", "manual_review_required", "manual_review_reasons", "target_score"):
            if field not in aliases:
                errors.append(f"FIELDS_RESULT_MAPPING_ALIAS_MISSING_{field}")
        warning_policy = result_mapping.get("manual_review_warning_policy") or {}
        if warning_policy.get("no_boundary_reference_found_allows_null_boundary_reference") is not True:
            errors.append("FIELDS_NO_BOUNDARY_NULL_WARNING_POLICY_MISSING")
        manual_confirmation = fields.get("manual_review_confirmation") or {}
        if manual_confirmation.get("status") != "MANUAL_REVIEW_CONFIRMED":
            errors.append("FIELDS_MANUAL_CONFIRM_STATUS_MISSING")
        if manual_confirmation.get("do_not_overwrite_suggested_purchase_price_yuan") is not True:
            errors.append("FIELDS_MANUAL_CONFIRM_PRESERVE_SYSTEM_PRICE_POLICY_MISSING")
        for field in (
            "manual_confirmed_purchase_price_yuan",
            "manual_review_note",
            "system_suggested_purchase_price_yuan",
            "manual_adjustment_yuan",
            "final_purchase_price_yuan",
        ):
            if field not in manual_confirmation.get("fields", []):
                errors.append(f"FIELDS_MANUAL_CONFIRM_FIELD_MISSING_{field}")
        two_step = fields.get("feishu_two_step_interaction") or {}
        if two_step.get("target_confirm_reply") != "确认":
            errors.append("FIELDS_FEISHU_TWO_STEP_CONFIRM_WORD_MISSING")
        for sample in ("86000", "86000元", "8.6万", "8.60万"):
            if sample not in two_step.get("manual_review_price_formats", []):
                errors.append(f"FIELDS_FEISHU_PRICE_FORMAT_MISSING_{sample}")
        if two_step.get("hide_runner_internal_commands_from_user") is not True:
            errors.append("FIELDS_FEISHU_INTERNAL_COMMAND_HIDE_POLICY_MISSING")
        feishu_input = fields.get("feishu_input_fields") or {}
        if feishu_input.get("brand_series_user_required") is not False:
            errors.append("FIELDS_FEISHU_BRAND_SERIES_STILL_USER_REQUIRED")
        if feishu_input.get("brand_series_internal_required") is not True:
            errors.append("FIELDS_FEISHU_BRAND_SERIES_NOT_INTERNAL_REQUIRED")
        required_user_fields = feishu_input.get("required_user_fields") or []
        if "brand" in required_user_fields or "series" in required_user_fields:
            errors.append("FIELDS_FEISHU_REQUIRED_USER_FIELDS_INCLUDE_BRAND_SERIES")
        for field in ("model_config", "license_date", "mileage_text", "color", "transfer_count_text", "condition_text"):
            if field not in required_user_fields:
                errors.append(f"FIELDS_FEISHU_REQUIRED_USER_FIELD_MISSING_{field}")
        inferred_fields = feishu_input.get("inferred_internal_fields") or []
        for field in ("brand", "series", "year_model", "config_model"):
            if field not in inferred_fields:
                errors.append(f"FIELDS_FEISHU_INFERRED_INTERNAL_FIELD_MISSING_{field}")
        if feishu_input.get("model_identity_source") != "series_brand_aliases":
            errors.append("FIELDS_FEISHU_MODEL_IDENTITY_SOURCE_MISSING")
        if feishu_input.get("unresolved_status") != "DRAFT_NEEDS_MODEL_RESOLUTION":
            errors.append("FIELDS_FEISHU_MODEL_UNRESOLVED_STATUS_MISSING")
        date_normalization = feishu_input.get("registration_date_normalization") or {}
        if date_normalization.get("normalize_to") != "YYYY.MM":
            errors.append("FIELDS_FEISHU_REGISTRATION_DATE_NORMALIZE_TO_MISSING")
        if "registration_date_year" not in date_normalization.get("derive_fields", []):
            errors.append("FIELDS_FEISHU_REGISTRATION_DATE_YEAR_DERIVE_MISSING")
        if "22.8" not in str(date_normalization.get("unrecognized_prompt") or ""):
            errors.append("FIELDS_FEISHU_REGISTRATION_DATE_PROMPT_MISSING")
        date_policy = (fields.get("target_fields") or {}).get("registration_date_policy") or {}
        for sample in ("22.8", "22.08", "2022.8", "2022.08", "2022-08", "2022/08", "2022年8月", "2022年08月"):
            if sample not in date_policy.get("accepted_input_formats", []):
                errors.append(f"FIELDS_REGISTRATION_DATE_FORMAT_MISSING_{sample}")
        for field in ("register_date", "registration_date", "register_year", "registration_date_year"):
            if field not in date_policy.get("required_internal_fields", []):
                errors.append(f"FIELDS_REGISTRATION_DATE_INTERNAL_FIELD_MISSING_{field}")

    pages_text = pages_path.read_text(encoding="utf-8") if pages_path.exists() else ""
    pages: dict[str, Any] = {}
    if pages_text:
        pages = json.loads(pages_text.lstrip("\ufeff"))
    if "S14_SUBPAGE_WITH_SYSTEM_BACK" not in pages_text:
        errors.append("S14_SUBPAGE_WITH_SYSTEM_BACK_CONTRACT_MISSING")
    if "horizontal_swipe_blocked_or_semantic_unchanged" not in pages_text:
        errors.append("S14_LAST_PAGE_RULE_MISSING")
    if "click_x_as_default_return" not in pages_text:
        errors.append("S14_CLICK_X_FORBIDDEN_CONTRACT_MISSING")
    if "android_back_or_bottom_back" not in pages_text:
        errors.append("S14_ANDROID_BACK_CONTRACT_MISSING")
    for keyword in ("覆盖面", "饰板", "装饰板", "内饰板", "盖板", "外饰", "表面"):
        if keyword not in pages_text:
            errors.append(f"S14_SURFACE_SEMANTIC_CONTRACT_MISSING_{keyword}")
    if "NON_SCORING_S14_DAMAGE" not in pages_text or "变形" not in pages_text:
        errors.append("S14_NON_SCORING_DAMAGE_CONTRACT_MISSING")
    if "non_scoring_damage_does_not_block_current_item_done" not in pages_text:
        errors.append("S14_NON_SCORING_COMPLETION_GATE_MISSING")
    for keyword in (
        "current_item_done_is_not_whole_vehicle_done",
        "whole_vehicle_completion_gate",
        "s14_collect_done_semantics",
        "current_s14_item_done",
        "s14_current_item_sequence_collected",
        "s14_has_uncollected_next_condition_signal",
        "unvisited_tabs_count",
        "uncollected_condition_tabs",
        "missing_repair_count",
        "s13_s14_repair_count_matched",
        "reference_score_usable_for_boundary_requires_whole_vehicle_complete",
    ):
        if keyword not in pages_text:
            errors.append(f"S14_WHOLE_VEHICLE_COMPLETENESS_CONTRACT_MISSING_{keyword.upper()}")
    if "S_LOGIN" not in pages_text or "LOGIN_REQUIRED_MANUAL" not in pages_text:
        errors.append("S_LOGIN_CONTRACT_MISSING")
    if "reference_score_ge_target" in pages_text or "score_ge_target_direct_to_s16" in pages_text:
        errors.append("S15_OLD_SCORE_GE_TARGET_DIRECT_TO_S16_CONTRACT_PRESENT")
    s15_page = next((item for item in pages.get("pages", []) if item.get("id") == "S15"), None)
    if not s15_page:
        errors.append("S15_PAGE_CONTRACT_MISSING")
    else:
        if s15_page.get("reference_selection_rule") != EXPECTED_REFERENCE_SELECTION_RULE:
            errors.append("S15_REFERENCE_SELECTION_RULE_NOT_V3_EARLY_EXIT")
        selection_contract = s15_page.get("selection_contract") or {}
        if selection_contract.get("competition_coefficient_affects_s15") is not False:
            errors.append("S15_COMPETITION_COEFFICIENT_AFFECTS_SELECTION")
        if selection_contract.get("boundary_requires_reference_score_trustworthy") is not True:
            errors.append("S15_BOUNDARY_TRUSTWORTHY_SCORE_GATE_MISSING")
        if selection_contract.get("boundary_requires_reference_score_usable_for_boundary") is not True:
            errors.append("S15_BOUNDARY_USABLE_SCORE_GATE_MISSING")

    runtime_text = runtime_path.read_text(encoding="utf-8") if runtime_path.exists() else ""
    if "is_s14_last_page_reached" not in runtime_text:
        errors.append("S14_LAST_PAGE_GATE_FUNCTION_MISSING")
    if "S14_MIXED_BINDING_BLOCKED" not in runtime_text:
        errors.append("S14_MIXED_BINDING_BLOCK_MISSING")
    if "S14_STALE_FIRST_LINE_BINDING_UNRESOLVED" not in runtime_text:
        errors.append("S14_STALE_BINDING_FAILURE_CODE_MISSING")
    if "s14_images_total\": images_processed" not in runtime_text:
        errors.append("S14_SYNTHETIC_IMAGE_TOTAL_STILL_PRESENT")
    if 's14_images_processed"] != metrics["s14_images_total"]' in runtime_text:
        errors.append("S14_IMAGE_COUNT_HARD_BLOCK_METRICS_STILL_PRESENT")
    if 's14_images_processed"] != s14_metrics["s14_images_total"]' in runtime_text:
        errors.append("S14_IMAGE_COUNT_HARD_BLOCK_S15_S16_STILL_PRESENT")
    if "client.back()" not in runtime_text:
        errors.append("S14_ANDROID_BACK_RUNTIME_MISSING")
    if "S14_NON_STRUCTURE_SURFACE_SEMANTICS" not in runtime_text or "surface_semantic" not in runtime_text:
        errors.append("S14_SURFACE_SEMANTIC_EXCLUSION_RUNTIME_MISSING")
    if "S14_NON_SCORING_DAMAGE_TYPES" not in runtime_text or "NON_SCORING_S14_DAMAGE" not in runtime_text:
        errors.append("S14_NON_SCORING_DAMAGE_RUNTIME_MISSING")
    if "S14_DAMAGE_NORMALIZATION.get(raw_damage)" not in runtime_text:
        errors.append("S14_SCORING_DAMAGE_NORMALIZATION_RUNTIME_MISSING")
    if "non_scoring_damage = normalized == S14_NON_SCORING_DAMAGE" not in runtime_text:
        errors.append("S14_NON_SCORING_SKIP_FLAG_RUNTIME_MISSING")
    if "not non_scoring_damage" not in runtime_text:
        errors.append("S14_NON_SCORING_NOT_SAVED_RUNTIME_MISSING")
    if "mixed_binding_blocked" not in runtime_text or "stale_unresolved_blocked" not in runtime_text:
        errors.append("S14_STALE_FIRST_LINE_MIXED_BINDING_GUARD_MISSING")
    for keyword in (
        "s14_whole_vehicle_collection_complete",
        "current_s14_item_done",
        "s14_current_item_sequence_collected",
        "reference_score_usable_for_boundary",
        "UNTRUSTED_REFERENCE_SCORE",
        "CURRENT_ITEM_DONE_WHOLE_VEHICLE_INCOMPLETE",
        "CURRENT_REFERENCE_SCORE_UNTRUSTED_OR_INCOMPLETE_S14",
        "_reference_score_usable_for_boundary",
    ):
        if keyword not in runtime_text:
            errors.append(f"S14_WHOLE_VEHICLE_RUNTIME_MISSING_{keyword.upper()}")
    if 'next_signal = {"s14_has_uncollected_next_condition_signal": False}' in runtime_text:
        errors.append("S14_SINGLE_IMAGE_BRANCH_STILL_FORCES_UNCOLLECTED_SIGNAL_FALSE")
    _check_runtime_s14_v144_semantics(runtime_path, errors)

    rule_source_manifest_text = (
        rule_source_manifest_path.read_text(encoding="utf-8") if rule_source_manifest_path.exists() else ""
    )
    if EXPECTED_DATA_FLOW_VERSION not in rule_source_manifest_text or EXPECTED_DATA_FLOW_FILE not in rule_source_manifest_text:
        errors.append("RULE_SOURCE_MANIFEST_NOT_V1_47")
    if ".bak_before_s05_trim_scroll" in rule_source_manifest_text and "历史" not in rule_source_manifest_text:
        errors.append("RULE_SOURCE_MANIFEST_BAK_NOT_MARKED_HISTORICAL")

    runtime_s01_text = runtime_s01_path.read_text(encoding="utf-8") if runtime_s01_path.exists() else ""
    for keyword in ("欢迎登录", "请输入手机号", "请输入验证码", "获取验证码", "账号密码登录", "用户协议", "隐私协议"):
        if keyword not in runtime_s01_text:
            errors.append(f"S_LOGIN_KEYWORD_MISSING_{keyword}")
    if "HUMAN_LOGIN_REQUIRED" not in runtime_s01_text:
        errors.append("S_LOGIN_HUMAN_REQUIRED_RUNTIME_MISSING")

    pricing_text = pricing_path.read_text(encoding="utf-8") if pricing_path.exists() else ""
    for min_price_yuan, service_fee_yuan in [(row["min_price_yuan"], row["service_fee_yuan"]) for row in expected_service_fee_tiers]:
        if f"({min_price_yuan}, {service_fee_yuan})" not in pricing_text:
            errors.append(f"PRICING_PY_SERVICE_FEE_TIER_MISSING_{min_price_yuan}_{service_fee_yuan}")
    if "return_price_yuan = guazi_price_yuan - service_fee_yuan" not in pricing_text:
        errors.append("PRICING_PY_NET_PAYOUT_FORMULA_MISSING")
    if _contains_forbidden_95_percent_price_formula(pricing_text):
        errors.append("FORBIDDEN_95_PERCENT_PRICE_FORMULA_PRESENT")
    if "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT" not in pricing_text:
        errors.append("PRICING_PY_V3_BOUNDARY_RULE_MISSING")
    try:
        src_dir = str((root / "src").resolve())
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from guazi_app_data_system import pricing as pricing_module

        for vector in (desktop_compiled.get("pricing_rule") or {}).get("expected_test_vectors", []):
            if not isinstance(vector, dict):
                continue
            price_yuan = int(vector["price_yuan"])
            expected_fee = int(vector["service_fee_yuan"])
            actual_fee = pricing_module.calc_guazi_service_fee(price_yuan)
            if actual_fee != expected_fee:
                errors.append(
                    f"PRICING_PY_SERVICE_FEE_VECTOR_FAILED_{price_yuan}_EXPECTED_{expected_fee}_ACTUAL_{actual_fee}"
                )
    except Exception as exc:
        errors.append(f"PRICING_PY_SERVICE_FEE_VECTOR_IMPORT_FAILED_{type(exc).__name__}")

    collector_text = collector_path.read_text(encoding="utf-8") if collector_path.exists() else ""
    formatter_text = formatter_path.read_text(encoding="utf-8") if formatter_path.exists() else ""
    gateway_text = gateway_path.read_text(encoding="utf-8") if gateway_path.exists() else ""
    realtime_receiver_text = realtime_receiver_path.read_text(encoding="utf-8") if realtime_receiver_path.exists() else ""
    dispatcher_text = dispatcher_path.read_text(encoding="utf-8") if dispatcher_path.exists() else ""
    runner_text = runner_path.read_text(encoding="utf-8") if runner_path.exists() else ""
    for row in expected_service_fee_tiers:
        if (
            f'"min_price_yuan": {row["min_price_yuan"]}' not in runner_text
            or f'"service_fee_yuan": {row["service_fee_yuan"]}' not in runner_text
        ):
            errors.append(
                f"PRICING_RUNNER_SERVICE_FEE_TIER_MISSING_{row['min_price_yuan']}_{row['service_fee_yuan']}"
            )
    start_listener_text = start_listener_path.read_text(encoding="utf-8") if start_listener_path.exists() else ""
    start_dispatcher_text = start_dispatcher_path.read_text(encoding="utf-8") if start_dispatcher_path.exists() else ""
    service_single_instance_text = service_single_instance_path.read_text(encoding="utf-8") if service_single_instance_path.exists() else ""
    adb_target_config_text = adb_target_config_path.read_text(encoding="utf-8") if adb_target_config_path.exists() else ""
    adb_target_helper_text = adb_target_helper_path.read_text(encoding="utf-8") if adb_target_helper_path.exists() else ""
    adb_target_src_text = adb_target_src_path.read_text(encoding="utf-8") if adb_target_src_path.exists() else ""
    app_startup_text = app_startup_path.read_text(encoding="utf-8") if app_startup_path.exists() else ""
    adb_device_gate_text = adb_device_gate_path.read_text(encoding="utf-8") if adb_device_gate_path.exists() else ""
    if not dispatcher_path.exists():
        errors.append("FEISHU_PRICING_DISPATCHER_MISSING")
    else:
        for keyword in (
            "FeishuPricingDispatcher",
            "--once",
            "--dry-run",
            "--loop",
            "--allow-app-run",
            "dry_run = args.dry_run or not args.allow_app_run",
            "queued_tasks",
            "active_app_task",
            "auto_prepare_queued_current_task",
            "run_first_stage",
            "run_second_stage",
            "revalidate_result",
            "sync_manual_review_to_supervisor",
            "check_system_health_preflight",
            "system_health_checker",
            "write_admin_intervention_feedback",
            "classify_admin_intervention",
            "admin_blocked_tasks",
            "_attempt_auto_recover_blocked_tasks",
            "system_blocked_tasks_ready_for_health_check",
            "auto_recovery_attempts",
            "next_health_check_at",
            "SYSTEM_BLOCKED",
            "ADMIN_INTERVENTION_REQUIRED",
            "wait-admin-resolution",
            "dispatcher_log.jsonl",
            "dispatcher_result.json",
            "DISPATCHER_LOOP_REQUIRES_ALLOW_APP_RUN",
            "ready-to-send",
            "safe_dispatch_kick",
            "dispatcher_loop_is_running",
            "dispatch_once_called",
            "dispatch_once_started_background",
            "force_health_check",
            "threading.Thread",
            "auto_send_result",
            "send_result_live",
            "send_result",
            "DISPATCHER_LOOP_ALREADY_RUNNING",
            "DISPATCHER_LOOP_RUNNING_BLOCKED_TASK_NOT_RECOVERED",
            "DISPATCH_ONCE_STARTED_BACKGROUND",
            "not force_health_check",
            "后台定价调度服务未运行，请检查服务",
            "dispatch_kick_log.jsonl",
            "canonical_blocking_error_code",
            "recoverable_by_health_check",
            "_auto_cancel_not_started_blocked_tasks",
            "auto_cancel_not_started_system_precheck_failure",
            "SYSTEM_PRECHECK_FAILED_NOT_STARTED",
            "blocks_queue",
        ):
            if keyword not in dispatcher_text:
                errors.append(f"FEISHU_PRICING_DISPATCHER_CAPABILITY_MISSING_{keyword}")
        for keyword in (
            "dispatch_kicker",
            "dispatch_kick_allow_app_run",
            "send_live if dispatch_kick_allow_app_run is None else dispatch_kick_allow_app_run",
            "gateway_result",
            "dispatch_kick",
        ):
            if keyword not in realtime_receiver_text:
                errors.append(f"FEISHU_REALTIME_CONFIRM_AUTORUN_MISSING_{keyword}")
        for keyword in (
            "build_current_target_task",
            "TARGET_INFO_NEEDS_CORRECTION",
            "dispatcher-dry-run-target-info-validation",
            '"would_prepare_current_target_task": False',
            "is_target_info_error",
        ):
            if keyword not in dispatcher_text:
                errors.append(f"FEISHU_DISPATCHER_TARGET_INFO_CORRECTION_MISSING_{keyword}")
    message_parser_text = message_parser_path.read_text(encoding="utf-8") if message_parser_path.exists() else ""
    normalizer_text = registration_normalizer_path.read_text(encoding="utf-8") if registration_normalizer_path.exists() else ""
    current_task_builder_text = current_task_builder_path.read_text(encoding="utf-8") if current_task_builder_path.exists() else ""
    target_info_feedback_text = target_info_feedback_path.read_text(encoding="utf-8") if target_info_feedback_path.exists() else ""
    admin_intervention_text = admin_intervention_router_path.read_text(encoding="utf-8") if admin_intervention_router_path.exists() else ""
    system_health_preflight_text = system_health_preflight_path.read_text(encoding="utf-8") if system_health_preflight_path.exists() else ""
    if not registration_normalizer_path.exists():
        errors.append("REGISTRATION_DATE_NORMALIZER_MISSING")
    else:
        for keyword in (
            "normalize_registration_date",
            "NormalizedRegistrationDate",
            "normalized_date",
            "registration date",
        ):
            if keyword not in normalizer_text:
                errors.append(f"REGISTRATION_DATE_NORMALIZER_CAPABILITY_MISSING_{keyword}")
        _check_registration_date_normalizer(registration_normalizer_path, errors)
    for keyword in (
        "enrich_registration_date",
        "REGISTRATION_DATE_UNRECOGNIZED",
        "DRAFT_NEEDS_TARGET_INFO",
        "register_year",
        "registration_date_year",
        "format_registration_date_resolution_reply",
    ):
        if keyword not in message_parser_text:
            errors.append(f"FEISHU_MESSAGE_REGISTRATION_DATE_NORMALIZATION_MISSING_{keyword}")
    for keyword in (
        "normalize_registration_date",
        "REGISTRATION_DATE_SOURCE_FIELDS",
        "registration_date_year",
        "_normalized_registration_from_draft",
    ):
        if keyword not in current_task_builder_text:
            errors.append(f"CURRENT_TARGET_TASK_REGISTRATION_DATE_NORMALIZATION_MISSING_{keyword}")
    if not target_info_feedback_path.exists():
        errors.append("TARGET_INFO_CORRECTION_FEEDBACK_HELPER_MISSING")
    else:
        for keyword in (
            "TARGET_INFO_NEEDS_CORRECTION",
            "WAITING_TARGET_INFO_CORRECTION",
            "TARGET_INFO_VALIDATION_FAILED",
            "TARGET_DATE_UNRECOGNIZED",
            "TARGET_BRAND_SERIES_INFERENCE_FAILED",
            "TARGET_BRAND_SERIES_CONFLICT",
            "TARGET_REQUIRED_FIELD_MISSING",
            "ask-sender-to-resend-target-info",
            "target_info_correction_reply.preview.txt",
            "target_info_correction_delivery.json",
            "business_chat_id",
            "sender_open_id",
            "format_target_info_correction_reply",
            "INTERNAL_FEEDBACK_FORBIDDEN_TERMS",
        ):
            if keyword not in target_info_feedback_text:
                errors.append(f"TARGET_INFO_CORRECTION_FEEDBACK_CAPABILITY_MISSING_{keyword}")
    if not admin_intervention_router_path.exists():
        errors.append("ADMIN_INTERVENTION_ROUTER_MISSING")
    else:
        for keyword in (
            "SYSTEM_BLOCKED",
            "ADMIN_INTERVENTION_REQUIRED",
            "ADMIN_INTERVENTION_RESOLVED",
            "SYSTEM_ENVIRONMENT_ERROR_CODES",
            "RECOVERABLE_ADMIN_ERROR_CODES",
            "AUTO_HEALTH_RECOVERABLE_ERROR_CODES",
            "NON_AUTO_RECOVERABLE_ERROR_CODES",
            "DEFAULT_HEALTH_CHECK_COOLDOWN_SECONDS",
            "is_auto_health_recoverable_error",
            "PAGE_OR_PROGRAM_ERROR_CODES",
            "detect_admin_recovery_command",
            "write_admin_intervention_feedback",
            "business_system_processing_reply.preview.txt",
            "admin_intervention_reply.preview.txt",
            "admin_intervention_delivery.json",
            "wait-admin-resolution",
            "notify-admin",
        ):
            if keyword not in admin_intervention_text:
                errors.append(f"ADMIN_INTERVENTION_ROUTER_CAPABILITY_MISSING_{keyword}")
        for forbidden in ("PowerShell", "adb", "uiautomator", "pricing_runner", "dispatcher", "--run-first-stage"):
            if forbidden not in admin_intervention_text:
                errors.append(f"ADMIN_INTERVENTION_BUSINESS_FORBIDDEN_TERM_MISSING_{forbidden}")
    if not system_health_preflight_path.exists():
        errors.append("SYSTEM_HEALTH_PREFLIGHT_MISSING")
    else:
        for keyword in (
            "check_system_health_preflight",
            "does_not_call_adb",
            "does_not_call_uiautomator",
            "does_not_start_app",
        ):
            if keyword not in system_health_preflight_text:
                errors.append(f"SYSTEM_HEALTH_PREFLIGHT_CAPABILITY_MISSING_{keyword}")
    if not series_aliases_path.exists():
        errors.append("FEISHU_SERIES_BRAND_ALIASES_MISSING")
    else:
        series_aliases = json.loads(series_aliases_path.read_text(encoding="utf-8-sig"))
        aliases_payload = series_aliases.get("series_brand_aliases") or {}
        expected_aliases = {
            "科鲁泽": ("雪佛兰", "科鲁泽"),
            "雅阁": ("本田", "雅阁"),
            "星锐": ("斯柯达", "星锐"),
        }
        for alias, (brand, series) in expected_aliases.items():
            item = aliases_payload.get(alias) or {}
            if item.get("brand") != brand or item.get("series") != series:
                errors.append(f"FEISHU_SERIES_BRAND_ALIAS_MISSING_{alias}")
    task_store_text = task_store_path.read_text(encoding="utf-8") if task_store_path.exists() else ""
    event_adapter_text = event_adapter_path.read_text(encoding="utf-8") if event_adapter_path.exists() else ""
    group_bindings_text = group_bindings_path.read_text(encoding="utf-8") if group_bindings_path.exists() else ""
    for field in (
        "target_guazi_listing_price_yuan",
        "guazi_service_fee_yuan",
        "guazi_net_payout_yuan",
        "guazi_return_price_yuan",
        "cost_yuan",
        "profit_yuan",
        "suggested_purchase_price_yuan",
        "auto_pricing_allowed",
        "manual_review_reason",
    ):
        if field not in collector_text:
            errors.append(f"COLLECTOR_CORE_FIELD_MISSING_{field}")
        if field not in formatter_text and field not in {"auto_pricing_allowed", "manual_review_reason"}:
            errors.append(f"FORMATTER_OUTPUT_FIELD_MISSING_{field}")
    for status_literal in ("FAILED", "NEEDS_REVIEW", "MANUAL_REVIEW_CONFIRMED"):
        if status_literal not in formatter_text:
            errors.append(f"FORMATTER_STATUS_BRANCH_MISSING_{status_literal}")
    if "_format_success_reply" not in formatter_text or "【定价完成】" not in formatter_text:
        errors.append("FORMATTER_STATUS_BRANCH_MISSING_SUCCEEDED")
    for keyword in (
        "resolve_pricing_result_field",
        "_format_manual_review_reply",
        "is_pricing_result_manual_review",
        "pricing_result_manual_review_reasons",
        "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING",
        "SAMPLE_SHORTAGE_MANUAL_REVIEW",
        "【待人工复核】",
        "【人工复核已确认】",
        "system_suggested_purchase_price_yuan",
        "manual_confirmed_purchase_price_yuan",
        "final_purchase_price_yuan",
        "profit_rate =",
        "请直接回复人工确认收车价",
    ):
        if keyword not in formatter_text:
            errors.append(f"FORMATTER_MANUAL_REVIEW_MAPPING_MISSING_{keyword}")
    for keyword in (
        "parse_manual_confirm_price_text",
        "parse_manual_price_command",
        "confirm_bound_target_task",
        "manual_confirm_price",
        "supervisor_open_ids",
        "8.6万",
        "当前任务已人工确认",
    ):
        if keyword not in gateway_text:
            errors.append(f"FEISHU_GATEWAY_TWO_STEP_FLOW_MISSING_{keyword}")
    if "confirm_latest_target_task" not in task_store_text:
        errors.append("FEISHU_TASK_STORE_CONFIRM_LATEST_MISSING")
    for forbidden in (
        "--requeue-second-stage",
        "--revalidate-result",
        "--manual-confirm-price",
        "--manual-review-note",
        "status.json",
        "pricing_result.json",
        "feishu_result_reply.preview.txt",
    ):
        if forbidden in gateway_text and forbidden in gateway_text.partition("HELP_TEXT")[2].partition("TASK_ID_PATTERN")[0]:
            errors.append(f"FEISHU_GATEWAY_USER_HELP_EXPOSES_INTERNAL_{forbidden}")
    for keyword in (
        "resolve_pricing_result_field",
        "pricing_result_business_status",
        "MANUAL_REVIEW_FINAL_STATUSES",
        "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "reference_price_10k",
        "selected_reference_score",
    ):
        if keyword not in collector_text:
            errors.append(f"COLLECTOR_NESTED_MAPPING_MISSING_{keyword}")
    _check_result_mapping_contract(collector_path, formatter_path, errors)

    runner_text = runner_path.read_text(encoding="utf-8") if runner_path.exists() else ""
    if "--requeue-second-stage" not in runner_text:
        errors.append("RUNNER_REQUEUE_SECOND_STAGE_MISSING")
    for keyword in (
        "--manual-confirm-price",
        "--manual-review-note",
        "--send-result",
        "--live",
        "manual_confirm_price",
        "send_result",
        "send_text_message",
        "MANUAL_REVIEW_CONFIRMED",
        "RESULT_SENT",
        "sent_to_feishu",
        "chat_id_masked",
        "_mask_chat_id",
        "manual_confirm_result.json",
        "ready-to-send",
    ):
        if keyword not in runner_text:
            errors.append(f"RUNNER_MANUAL_CONFIRM_OR_SEND_RESULT_MISSING_{keyword}")
    if "run_id" not in runner_text or "generation_id" not in runner_text:
        errors.append("RUNNER_RUN_ID_GENERATION_ID_MISSING")
    if 'encoding="utf-8"' not in runner_text or 'errors="replace"' not in runner_text:
        errors.append("RUNNER_SUBPROCESS_UTF8_REPLACE_MISSING")
    if "STALE_RUN_RESULT_IGNORED" not in runner_text:
        errors.append("RUNNER_STALE_RESULT_IGNORE_MISSING")
    for keyword in ("pricing_result_business_status", "technical_status", "business_status", "recommended_next_action"):
        if keyword not in runner_text:
            errors.append(f"RUNNER_STATUS_SEMANTIC_MISSING_{keyword}")
    if 'pricing_result_business_status(collection.result) == "NEEDS_REVIEW"' not in runner_text:
        errors.append("RUNNER_FULL_CHAIN_MANUAL_REVIEW_NOT_MAPPED_TO_NEEDS_REVIEW")
    for keyword in (
        "_find_revalidation_pricing_result",
        "_pricing_result_identity_error",
        "previous_status",
        "new_status",
        "result_source_path",
        "revalidation_run_id",
    ):
        if keyword not in runner_text:
            errors.append(f"RUNNER_REVALIDATE_BUSINESS_STATUS_REFRESH_MISSING_{keyword}")

    if not roles_path.exists():
        errors.append("FEISHU_ROLES_CONFIG_MISSING")
    else:
        roles_text = roles_path.read_text(encoding="utf-8")
        for keyword in ("admin_open_ids", "business_chat_ids", "supervisor_chat_ids", "supervisor_open_ids", "admin_chat_ids"):
            if keyword not in roles_text:
                errors.append(f"FEISHU_ROLES_CONFIG_FIELD_MISSING_{keyword}")
    if not group_bindings_data_path.exists():
        errors.append("FEISHU_GROUP_BINDINGS_DATA_MISSING")
    else:
        group_data_text = group_bindings_data_path.read_text(encoding="utf-8")
        for keyword in ("business_chats", "supervisor_chats", "binding_codes"):
            if keyword not in group_data_text:
                errors.append(f"FEISHU_GROUP_BINDINGS_DATA_FIELD_MISSING_{keyword}")
    for keyword in (
        "FeishuGroupBindings",
        "DEFAULT_GROUP_BINDINGS_PATH",
        "business_chats",
        "supervisor_chats",
        "binding_codes",
        "generate_binding_code",
        "bind_business_chat",
        "bound_supervisor_chat_id",
        "business_chats_bound_to_supervisor",
        "mask_identifier",
        "DEFAULT_BINDING_CODE_TTL_MINUTES = 10",
        "BINDING_CODE_EXPIRED",
        "BINDING_CODE_USED",
        "BUSINESS_CHAT_ALREADY_BOUND",
    ):
        if keyword not in group_bindings_text:
            errors.append(f"FEISHU_GROUP_BINDINGS_CAPABILITY_MISSING_{keyword}")
    for keyword in (
        "WAITING_TARGET_CONFIRMATION",
        "confirm_card_message_id",
        "sender_open_id",
        "business_chat_id",
        "source_message_id",
        "waiting_confirmation_tasks",
        "find_task_by_confirm_card_message_id",
        "dispatch_next_queued_task_dry_run",
        "current_target_task_path",
        "APP_CONTROL_LOCKED",
        "supervisor_review_card_message_id",
        "manual_price_waiting_tasks",
        "find_task_by_supervisor_review_card_message_id",
        "admin_blocked_tasks",
        "system_blocked_tasks_ready_for_health_check",
        "record_system_blocked_health_check",
        "resolve_admin_intervention",
        "admin_intervention_resolved",
        "admin_intervention_auto_resolved",
        "last_health_check_at",
        "health_check_count",
        "next_health_check_at",
        "wait-dispatcher",
        "SYSTEM_BLOCKED",
        "ADMIN_INTERVENTION_REQUIRED",
        "canonical_blocking_error_codes",
        "canonical_blocking_error_code",
        "repair_blocking_reason_fields",
        "MAINTENANCE_ERROR_CODES",
        "TASK_NOT_FAILED",
        "TASK_NOT_REQUEUEABLE",
        "INVALID_REQUEUE_STATE",
        "first_stage_result.json",
        "runner_result.json",
        "runner_error.json",
        "admin_intervention_history",
        "first_stage_stderr.log",
        "first_stage_stdout.log",
        "last_blocking_error_code",
        "recoverable_by_health_check",
        "format_system_not_recovered_reply",
        "admin_system_not_recovered_reply.preview.txt",
        "admin_system_not_recovered_delivery.json",
        "SYSTEM_PRECHECK_FAILED_NOT_STARTED",
        "NOT_STARTED_SYSTEM_PRECHECK_ERROR_CODES",
        "CANCELLED_TASK_RESEND_REPLY",
        "auto_cancel_not_started_system_precheck_failure",
        "is_not_started_system_precheck_failure",
        "not_started_auto_cancel_business_reply.preview.txt",
        "not_started_auto_cancel_admin_reply.preview.txt",
        "not_started_auto_cancel_delivery.json",
        "blocks_queue",
        "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER",
        "release_blocker_without_active_runner",
        "released_blocker_business_reply.preview.txt",
        "released_blocker_admin_reply.preview.txt",
        "released_blocker_delivery.json",
    ):
        if keyword not in task_store_text:
            errors.append(f"FEISHU_MULTI_USER_TASK_STORE_MISSING_{keyword}")
    for keyword in (
        "format_system_not_recovered_reply",
        "【系统暂未恢复】",
        "系统暂时还不能开始定价",
        "手机已连接电脑",
        "ADB 已授权",
        "瓜子 APP 已登录并停在首页",
        "处理好后请再次回复：确认",
    ):
        if keyword not in admin_intervention_text:
            errors.append(f"FEISHU_ADMIN_RECOVERY_FEEDBACK_MISSING_{keyword}")
    for keyword in (
        "admin_open_ids",
        "clean_feishu_command_text",
        "detect_group_command",
        "handle_group_command",
        "detect_self_identity_command",
        "format_self_identity_reply",
        "is_admin_open_id",
        "is_supervisor_open_id",
        "is_target_vehicle_message",
        "TARGET_VEHICLE_ROUTE_FIELDS",
        "should_attempt_manual_price_route",
        "detect_admin_recovery_command",
        "handle_admin_recovery_command",
        "handle_one_word_confirm",
        "handle_explicit_confirm",
        "default_dispatch_kick",
        "dispatch_kicker",
        "dispatch_kick_allow_app_run",
        "force_health_check=True",
        "_dispatch_kick_failed",
        "_dispatch_kick_failure_reply",
        "format_system_not_recovered_reply",
        "系统暂时不能开始自动定价",
        "release_blocker_without_active_runner",
        "CONFIRM_AUTO_DISPATCH_REPLY",
        "\u7cfb\u7edf\u5df2\u5f00\u59cb\u81ea\u52a8\u5b9a\u4ef7",
        "MANUAL_PRICE_CONFIRM_PROMPT",
        "TARGET_INFO_CONFIRM_PROMPT",
        "BUSINESS_SYSTEM_PROCESSING_REPLY",
        "resolve_admin_intervention",
        "system_health_checker",
        "check_system_health_preflight",
        "parse_template_fields",
        "utf-8-sig",
        "查看我的ID",
        "我是谁",
        "我的ID",
        "你的飞书身份信息",
        "open_id：",
        "是否管理员",
        "是否主管",
        "设置本群为一线群",
        "设置本群为主管群",
        "生成主管群绑定码",
        "绑定一线群",
        "查看本群设置",
        "管理员权限未配置",
        "你没有权限设置群身份",
        "is_business_chat",
        "is_supervisor_chat",
        "business_chat_not_initialized",
        "本群尚未设置为一线群",
        "本群尚未绑定主管复核群",
        "bind-supervisor-chat",
        "请到主管复核群回复对应复核卡片完成价格确认",
        "mask_identifier",
        "CANCELLED_TASK_RESEND_REPLY",
        "cancelled_task_confirm_rejected",
    ):
        if keyword not in gateway_text:
            errors.append(f"FEISHU_GROUP_INITIALIZATION_GATEWAY_MISSING_{keyword}")
    target_route_pos = gateway_text.find("is_target_vehicle_message(text)")
    manual_price_pos = gateway_text.find("parse_manual_price_command(text)")
    if target_route_pos < 0 or manual_price_pos < 0 or target_route_pos > manual_price_pos:
        errors.append("FEISHU_TARGET_MESSAGE_ROUTE_NOT_BEFORE_MANUAL_PRICE")
    for keyword in (
        "【\\[]",
        "车型",
        "车辆颜色",
        "具体车况",
        "车牌归属",
    ):
        if keyword not in message_parser_text:
            errors.append(f"FEISHU_TARGET_FIELD_PARSER_MISSING_{keyword}")
    for keyword in (
        "[【\\[]",
        "车型",
        "车辆颜色",
        "具体车况",
        "车牌归属",
        "FEISHU_USER_REQUIRED_FIELDS",
        "INTERNAL_REQUIRED_FIELDS",
        "load_series_brand_aliases",
        "infer_series_brand_from_model_text",
        "DRAFT_NEEDS_MODEL_RESOLUTION",
        "MODEL_BRAND_SERIES_UNRESOLVED",
        "MODEL_BRAND_SERIES_CONFLICT",
        "brand_source",
        "series_source",
        "model_parse_source",
        "inferred_from_model_text",
        "format_model_resolution_reply",
        "车型字段无法确定品牌/车系",
        "系统识别",
    ):
        if keyword not in message_parser_text:
            errors.append(f"FEISHU_TARGET_BRAND_SERIES_INFERENCE_MISSING_{keyword}")
    user_required_block = message_parser_text.partition("FEISHU_USER_REQUIRED_FIELDS")[2].partition(")")[0]
    if '"brand",' in user_required_block:
        errors.append("FEISHU_USER_REQUIRED_FIELDS_STILL_INCLUDE_BRAND")
    if '"series",' in user_required_block:
        errors.append("FEISHU_USER_REQUIRED_FIELDS_STILL_INCLUDE_SERIES")
    for keyword in (
        "reply_to_message_id",
        "parent_message_id",
        "chat_name",
    ):
        if keyword not in gateway_text:
            errors.append(f"FEISHU_GATEWAY_REPLY_BINDING_MISSING_{keyword}")
        if keyword not in event_adapter_text:
            errors.append(f"FEISHU_EVENT_ADAPTER_REPLY_BINDING_MISSING_{keyword}")
    for keyword in (
        "你当前有多个待确认任务，请回复对应确认卡",
        "当前有多个待复核任务，请回复对应复核卡片",
        "当前任务需要主管复核价格，请主管回复人工确认价",
        "请管理员先配置主管 open_id",
        "format_supervisor_review_card",
        "该任务需要主管人工复核，已同步到主管复核群",
    ):
        if keyword not in gateway_text and keyword not in formatter_text and keyword not in task_store_text:
            errors.append(f"FEISHU_MULTI_USER_BUSINESS_POLICY_MISSING_{keyword}")
    for forbidden in (
        "--run-first-stage",
        "--run-second-stage",
        "--requeue-second-stage",
        "--revalidate-result",
        "--manual-confirm-price",
        "--manual-review-note",
        "--send-result",
        "adb",
        "uiautomator",
        "run_id",
        "generation_id",
        "status.json",
        "runner_result",
        "pricing_result.json",
        "STALE_RUN_RESULT_IGNORED",
    ):
        if forbidden in formatter_text and "format_supervisor_review_card" in formatter_text:
            supervisor_block = formatter_text.partition("def format_supervisor_review_card")[2].partition("def _format_manual_review_confirmed_reply")[0]
            if forbidden in supervisor_block:
                errors.append(f"FEISHU_SUPERVISOR_CARD_EXPOSES_INTERNAL_{forbidden}")

    for path, text, service_script, start_script in (
        (start_listener_path, start_listener_text, "feishu_realtime_receiver.py", "start_feishu_listener.ps1"),
        (start_dispatcher_path, start_dispatcher_text, "feishu_pricing_dispatcher.py", "start_feishu_dispatcher.ps1"),
    ):
        if not path.exists():
            errors.append(f"FEISHU_SERVICE_START_SCRIPT_MISSING_{path.name}")
            continue
        for keyword in (
            "Stop-ProjectScopedServiceProcesses",
            "Assert-SingleProjectServiceInstance",
            "ProjectRoot=$ProjectRoot",
            service_script,
            start_script,
            "WindowStyle Hidden",
            "New service command",
        ):
            if keyword not in text:
                errors.append(f"FEISHU_SERVICE_SINGLE_INSTANCE_START_MISSING_{path.name}_{keyword}")
        for forbidden in (
            "Stop-Process python",
            "Stop-Process -Name python",
            "runtime_s01_to_s10_mainline.py",
            "runtime_s10_to_s16_mainline.py",
            "pricing_runner.py",
            "uiautomator",
        ):
            if forbidden.lower() in text.lower():
                errors.append(f"FEISHU_SERVICE_START_SCRIPT_FORBIDDEN_{path.name}_{forbidden}")

    if not service_single_instance_path.exists():
        errors.append("FEISHU_SERVICE_SINGLE_INSTANCE_HELPER_MISSING")
    else:
        for keyword in (
            "Get-CimInstance Win32_Process",
            "CommandLine",
            "ParentProcessId",
            "Normalize-ServiceProjectRoot",
            "Stop-Process -Id",
            "ExcludeProcessIds",
            "Expected exactly one",
        ):
            if keyword not in service_single_instance_text:
                errors.append(f"FEISHU_SERVICE_SINGLE_INSTANCE_HELPER_MISSING_{keyword}")
        for forbidden in (
            "Stop-Process python",
            "Stop-Process -Name python",
            "runtime_s01_to_s10_mainline.py",
            "runtime_s10_to_s16_mainline.py",
            "pricing_runner.py",
            "uiautomator",
        ):
            if forbidden.lower() in service_single_instance_text.lower():
                errors.append(f"FEISHU_SERVICE_SINGLE_INSTANCE_HELPER_FORBIDDEN_{forbidden}")

    if not adb_target_config_path.exists():
        errors.append("ADB_TARGET_DEVICE_CONFIG_MISSING")
    else:
        backup_exists = any(adb_target_config_path.parent.glob("adb_target_device.yaml.bak_before_rebind_*"))
        errors.extend(validate_adb_target_device_config(adb_target_config_text, backup_exists=backup_exists))
    for path, text in (
        (adb_target_helper_path, adb_target_helper_text),
        (adb_target_src_path, adb_target_src_text),
    ):
        if not path.exists():
            errors.append(f"ADB_TARGET_DEVICE_HELPER_MISSING_{path.name}")
            continue
        for keyword in (
            "GUAZI_ADB_SERIAL",
            "load_target_adb_serial",
            "build_adb_command",
            "validate_target_device_available",
            "TARGET_ADB_SERIAL_NOT_CONFIGURED",
            "TARGET_ADB_DEVICE_NOT_CONNECTED",
            "TARGET_ADB_DEVICE_UNAUTHORIZED",
            "TARGET_ADB_DEVICE_OFFLINE",
            "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT",
            "allow_default_when_single_device",
        ):
            if keyword not in text:
                errors.append(f"ADB_TARGET_DEVICE_HELPER_MISSING_{path.name}_{keyword}")
    if "build_adb_command(" not in app_startup_text or '"-s"' not in adb_target_src_text:
        errors.append("ADB_CLIENT_DOES_NOT_BIND_TARGET_SERIAL")
    if "TARGET_ADB_SERIAL_NOT_CONFIGURED" not in app_startup_text:
        errors.append("ADB_CLIENT_MISSING_SERIAL_NOT_CONFIGURED_FAIL_CLOSED")
    if "validate_target_device_available" not in adb_device_gate_text or "no_default_device_fallback" not in adb_device_gate_text:
        errors.append("ADB_DEVICE_GATE_NOT_STRICT_TARGET_SERIAL")
    for forbidden in ("adb kill-server", "adb disconnect"):
        if forbidden in app_startup_text or forbidden in adb_device_gate_text or forbidden in adb_target_src_text:
            errors.append(f"ADB_RUNTIME_FORBIDDEN_COMMAND_PRESENT_{forbidden}")
    for keyword in (
        "TARGET_ADB_SERIAL_NOT_CONFIGURED",
        "TARGET_ADB_DEVICE_NOT_CONNECTED",
        "TARGET_ADB_DEVICE_UNAUTHORIZED",
        "TARGET_ADB_DEVICE_OFFLINE",
        "TARGET_ADB_DEVICE_TRANSIENT_DISCONNECT",
    ):
        if keyword not in task_store_text:
            errors.append(f"FEISHU_TASK_STORE_MISSING_ADB_TARGET_ERROR_{keyword}")
        if keyword not in runner_text:
            errors.append(f"PRICING_RUNNER_MISSING_ADB_TARGET_PRECHECK_{keyword}")
    for keyword in (
        "runtime_environment_snapshot",
        "adb_path_source",
        "adb_runtime_env_mode",
        "adb_vendor_keys_path_summary",
    ):
        if keyword not in app_startup_text:
            errors.append(f"ADB_ENV_SNAPSHOT_MISSING_{keyword}")
    for keyword in (
        "adb_devices_l_raw",
        "parsed_devices",
        "target_device_state",
        "target_device_present_before_first_stage",
        "device_snapshot_taken_at",
        "device_snapshot_error",
    ):
        if keyword not in adb_device_gate_text:
            errors.append(f"ADB_DEVICE_GATE_EVIDENCE_MISSING_{keyword}")

    agents_text = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    if "定价文件夹规则源约束" not in agents_text:
        errors.append("AGENTS_RULE_SOURCE_POLICY_MISSING")
    if "聊天中的内容只能作为定位线索" not in agents_text:
        errors.append("AGENTS_CHAT_SNIPPET_POLICY_MISSING")

    return {
        "ok": not errors,
        "status": "RULE_SOURCE_SYNC_PASSED" if not errors else "RULE_SOURCE_SYNC_FAILED",
        "errors": errors,
        "warnings": warnings,
        "desktop_rule_compiled_path": str(desktop_compiled_path),
        "expected_service_fee_tiers": expected_service_fee_tiers,
        "legacy_rule_blacklist_findings": legacy_blacklist_findings,
        "legacy_errors": legacy_errors,
        "legacy_allowlisted_warnings": legacy_allowlisted_warnings,
        "historical_warnings": historical_warnings,
        "manifest_path": str(manifest_path),
    }


def _check_runtime_s14_v144_semantics(runtime_path: Path, errors: list[str]) -> None:
    if not runtime_path.exists():
        return
    try:
        spec = importlib.util.spec_from_file_location("_rule_sync_runtime_s10_to_s16_mainline", runtime_path)
        if spec is None or spec.loader is None:
            errors.append("S14_RUNTIME_IMPORT_SPEC_MISSING")
            return
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
    except Exception as exc:  # pragma: no cover - failure is reported as data.
        errors.append(f"S14_RUNTIME_IMPORT_FAILED_{type(exc).__name__}")
        return

    if runtime._normalize_s14_part("右C柱覆盖面") == "ABC柱":
        errors.append("S14_C_PILLAR_SURFACE_NORMALIZES_TO_ABC")
    if runtime._normalize_s14_part("左B柱饰板") == "ABC柱":
        errors.append("S14_B_PILLAR_TRIM_NORMALIZES_TO_ABC")
    if runtime._normalize_s14_part("右C柱") != "ABC柱":
        errors.append("S14_TRUE_C_PILLAR_NO_LONGER_NORMALIZES_TO_ABC")

    parsed_deformation = runtime._parse_s14_damage_line("右C柱覆盖面--变形")
    if not parsed_deformation or parsed_deformation[2] != runtime.S14_NON_SCORING_DAMAGE:
        errors.append("S14_DEFORMATION_NOT_NON_SCORING_DAMAGE")
    parsed_metal = runtime._parse_s14_damage_line("右C柱覆盖面--钣金")
    if not parsed_metal or parsed_metal[0] == "ABC柱":
        errors.append("S14_C_PILLAR_SURFACE_METAL_STILL_ABC")

    class DummyTiming:
        def add(self, **_kwargs: Any) -> None:
            return None

    snapshot = {
        "nodes": [
            {"labels": ["瓜子官方检测报告"], "bounds": [0, 0, 1080, 90], "selected": False},
            {"labels": ["右C柱覆盖面(1/1)"], "bounds": [620, 120, 1040, 190], "selected": True},
            {"labels": ["右C柱覆盖面--变形"], "bounds": [120, 720, 900, 770], "selected": False},
        ],
        "visible_texts": ["瓜子官方检测报告", "右C柱覆盖面(1/1)", "右C柱覆盖面--变形"],
        "fresh_xml": "",
        "screenshot_path": "",
        "xml_path": "",
    }
    tab = runtime._s14_selected_tab(snapshot)
    context = {"damage_by_part": {}, "timing": DummyTiming(), "s14_no_semantic_change_count": 1}
    record = runtime._s14_collect_current_image(context, snapshot, tab, 1)
    state = runtime._s14_semantic_state(snapshot, tab)
    gate = runtime.is_s14_last_page_reached(
        context,
        selected_tab=tab,
        semantic_state=state,
        horizontal_swipe_effective=False,
        next_signal={"s14_has_uncollected_next_condition_signal": False},
    )
    blocked_gate = runtime.is_s14_last_page_reached(
        context,
        selected_tab=tab,
        semantic_state=state,
        horizontal_swipe_effective=False,
        next_signal={"s14_has_uncollected_next_condition_signal": True},
    )
    if record.get("saved_to_repair_items"):
        errors.append("S14_NON_SCORING_DAMAGE_SAVED_TO_REPAIR_ITEMS")
    if record.get("skipped_reason") != runtime.S14_NON_SCORING_DAMAGE:
        errors.append("S14_NON_SCORING_DAMAGE_NOT_SKIPPED")
    if not gate.get("current_page_collected") or not gate.get("last_page_reached"):
        errors.append("S14_NON_SCORING_DAMAGE_BLOCKS_CURRENT_ITEM_DONE")
    if blocked_gate.get("last_page_reached"):
        errors.append("S14_UNCOLLECTED_NEXT_SIGNAL_DOES_NOT_BLOCK_WHOLE_VEHICLE_LAST_PAGE")


def _check_registration_date_normalizer(normalizer_path: Path, errors: list[str]) -> None:
    try:
        import sys

        spec = importlib.util.spec_from_file_location("_rule_sync_registration_date_normalizer", normalizer_path)
        if spec is None or spec.loader is None:
            errors.append("REGISTRATION_DATE_NORMALIZER_IMPORT_SPEC_MISSING")
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - failure is reported as data.
        errors.append(f"REGISTRATION_DATE_NORMALIZER_IMPORT_FAILED_{type(exc).__name__}")
        return

    samples = (
        "22.8",
        "22.08",
        "2022.8",
        "2022.08",
        "2022-08",
        "2022/08",
        "2022年8月",
        "2022年08月",
    )
    for sample in samples:
        normalized = module.normalize_registration_date(sample)
        if (
            normalized is None
            or normalized.normalized_date != "2022.08"
            or normalized.year != 2022
            or normalized.month != 8
        ):
            errors.append(f"REGISTRATION_DATE_NORMALIZER_SAMPLE_FAILED_{sample}")
    if module.normalize_registration_date("not-a-date") is not None:
        errors.append("REGISTRATION_DATE_NORMALIZER_ACCEPTS_INVALID_TEXT")


def _check_result_mapping_contract(collector_path: Path, formatter_path: Path, errors: list[str]) -> None:
    try:
        import sys

        script_dir = str(collector_path.parent)
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        collector_spec = importlib.util.spec_from_file_location("_rule_sync_pricing_result_collector", collector_path)
        formatter_spec = importlib.util.spec_from_file_location("_rule_sync_feishu_result_formatter", formatter_path)
        if collector_spec is None or collector_spec.loader is None:
            errors.append("COLLECTOR_IMPORT_SPEC_MISSING")
            return
        if formatter_spec is None or formatter_spec.loader is None:
            errors.append("FORMATTER_IMPORT_SPEC_MISSING")
            return
        collector = importlib.util.module_from_spec(collector_spec)
        formatter = importlib.util.module_from_spec(formatter_spec)
        sys.modules[collector_spec.name] = collector
        sys.modules[formatter_spec.name] = formatter
        collector_spec.loader.exec_module(collector)
        formatter_spec.loader.exec_module(formatter)
    except Exception as exc:  # pragma: no cover - failure is reported as data.
        errors.append(f"RESULT_MAPPING_IMPORT_FAILED_{type(exc).__name__}")
        return

    payload = {
        "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "final_status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "current_state": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "target_vehicle": "Honda Accord 2021 260TURBO",
        "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "boundary_confirmed": False,
        "boundary_reference_index": None,
        "boundary_reference_score": None,
        "s17_payload": {
            "final_reference_index": 1,
            "reference_price_10k": 9.84,
            "reference_score": 94.0,
            "target_score": 94.5,
            "manual_review_required": True,
            "manual_review_reasons": [
                "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING",
                "SAMPLE_SHORTAGE_MANUAL_REVIEW",
            ],
        },
        "pricing": {
            "base_reference_price_yuan": 98400,
            "target_guazi_listing_price_yuan": 96400,
            "guazi_service_fee_yuan": 1500,
            "guazi_net_payout_yuan": 94900,
            "guazi_return_price_yuan": 94900,
            "cost_yuan": 1000,
            "profit_yuan": 7592,
            "suggested_purchase_price_yuan": 86308,
            "manual_review_required": True,
        },
    }

    expectations = {
        "target_score": 94.5,
        "final_reference_index": 1,
        "final_reference_score": 94.0,
        "final_reference_price_yuan": 98400,
        "target_guazi_listing_price_yuan": 96400,
        "guazi_service_fee_yuan": 1500,
        "suggested_purchase_price_yuan": 86308,
    }
    for field, expected in expectations.items():
        if collector.resolve_pricing_result_field(payload, field) != expected:
            errors.append(f"COLLECTOR_NESTED_FIELD_MAPPING_FAILED_{field}")
    if collector.pricing_result_business_status(payload) != "NEEDS_REVIEW":
        errors.append("COLLECTOR_FULL_CHAIN_MANUAL_REVIEW_NOT_NEEDS_REVIEW")

    formatted = formatter.format_result_reply(
        task_id="FS20260612_0002",
        status="SUCCEEDED",
        pricing_result=payload,
    )
    if formatted.warnings:
        errors.append("FORMATTER_MANUAL_REVIEW_BOUNDARY_NULL_WARNINGS_PRESENT")
    if "【待人工复核】" not in formatted.text:
        errors.append("FORMATTER_FULL_CHAIN_MANUAL_REVIEW_NOT_PENDING_REVIEW")
    if "【定价完成】" in formatted.text:
        errors.append("FORMATTER_FULL_CHAIN_MANUAL_REVIEW_FORMATTED_AS_AUTO_SUCCESS")
    if "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING" not in formatted.text or "reference_score >= target_score" not in formatted.text:
        errors.append("FORMATTER_NO_BOUNDARY_REASON_DESCRIPTION_MISSING")
    if "profit_yuan = 7592" not in formatted.text:
        errors.append("FORMATTER_MANUAL_REVIEW_PROFIT_MISSING")
    if "suggested_purchase_price_yuan = 86308" not in formatted.text:
        errors.append("FORMATTER_MANUAL_REVIEW_SUGGESTED_PRICE_MISSING")


def _contains_forbidden_95_percent_price_formula(text: str) -> bool:
    price_markers = ("guazi", "pricing", "listing", "payout", "return_price", "回款", "定价", "挂牌")
    for line in text.splitlines():
        stripped = line.strip().lower()
        if ("0.95" in stripped or "95%" in stripped) and any(marker in stripped for marker in price_markers):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    result = check_rule_source_sync(args.project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(result["status"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
