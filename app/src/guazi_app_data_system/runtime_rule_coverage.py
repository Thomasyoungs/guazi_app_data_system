"""Runtime coverage helpers for desktop rule/page-contract clauses.

This module keeps the runtime side honest: every critical action should be
traceable from a desktop rule clause to an action plan, execution trace, and
tests. The config file is JSON-compatible YAML so it can be parsed without
adding runtime dependencies.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COVERAGE_PATH = PROJECT_ROOT / "config" / "page_contract_runtime_coverage.yaml"
DEFAULT_LEGACY_BLACKLIST_PATH = PROJECT_ROOT / "config" / "legacy_rule_blacklist.yaml"

VALID_COVERAGE_STATUSES = {
    "FULLY_CONTRACT_DRIVEN",
    "CONTRACT_GUARDED_BUT_CODE_DRIVEN",
    "FALLBACK_BOUND_BY_CONTRACT",
    "NOT_COVERED",
    "NEEDS_SOURCE_RULE",
}

COVERAGE_STATUS_SCORES = {
    "FULLY_CONTRACT_DRIVEN": 100,
    "CONTRACT_GUARDED_BUT_CODE_DRIVEN": 80,
    "FALLBACK_BOUND_BY_CONTRACT": 60,
    "NEEDS_SOURCE_RULE": 20,
    "NOT_COVERED": 0,
}

REQUIRED_RUNTIME_STAGES = [
    "S03",
    "S05",
    "S07_COLOR",
    "S07_AGE",
    "S08_FILTER_SUMMARY",
    "S10_FILTER_SUMMARY",
    "S10_REFERENCE_CARD_BINDING",
    "S11_REPORT_ENTRY",
    "S13_REPAIR_COUNT",
    "S13_REPAIR_ENTRY_BINDING",
    "S14_COLLECTION",
    "S14_RETURN_TO_S10",
    "S15_SCORING_RULE",
    "S15_REFERENCE_SELECTION_V3",
    "REFERENCE_EARLY_EXIT",
    "S16_PRICING_RULE",
    "DISPATCHER_REFERENCE_CONTINUATION",
    "FEISHU_USER_FEEDBACK",
]

REQUIRED_CLAUSE_FIELDS = [
    "rule_clause_id",
    "rule_source_file",
    "rule_source_version",
    "rule_clause_text_summary",
    "runtime_stage",
    "expected_behavior",
    "action_plan_builder",
    "runtime_executor_function",
    "evidence_fields",
    "test_files",
    "coverage_status",
    "allowed_fallbacks",
    "forbidden_actions",
    "performance_budget_ms",
    "requires_runtime_trace",
]

_LEGACY_RULE_BLACKLIST_CACHE: Optional[Mapping[str, Any]] = None


def _legacy_rule_patterns(rule_ids: Sequence[str]) -> List[str]:
    global _LEGACY_RULE_BLACKLIST_CACHE
    if _LEGACY_RULE_BLACKLIST_CACHE is None:
        try:
            _LEGACY_RULE_BLACKLIST_CACHE = json.loads(DEFAULT_LEGACY_BLACKLIST_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _LEGACY_RULE_BLACKLIST_CACHE = {}
    wanted = {str(rule_id) for rule_id in rule_ids}
    patterns: List[str] = []
    for rule in (_LEGACY_RULE_BLACKLIST_CACHE.get("rules") if isinstance(_LEGACY_RULE_BLACKLIST_CACHE, Mapping) else []) or []:
        if not isinstance(rule, Mapping) or str(rule.get("id") or "") not in wanted:
            continue
        pattern = str(rule.get("pattern") or "")
        if pattern:
            patterns.append(pattern)
    return patterns


def _legacy_text_matches_rule(value: Any, rule_ids: Sequence[str]) -> bool:
    text = str(value or "")
    if not text:
        return False
    for pattern in _legacy_rule_patterns(rule_ids):
        try:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
        except re.error:
            if pattern.lower() in text.lower():
                return True
    return False

TRACE_STAGE_ALIASES = {
    "S07_AGE_SLIDER": "S07_AGE",
    "S07_AGE_FILTER": "S07_AGE",
    "S08": "S08_FILTER_SUMMARY",
    "S10": "S10_FILTER_SUMMARY",
    "S10_CARD_BINDING": "S10_REFERENCE_CARD_BINDING",
    "S11": "S11_REPORT_ENTRY",
    "S13": "S13_REPAIR_ENTRY_BINDING",
    "S13_S14_COLLECTION": "S14_COLLECTION",
    "S15_SCORING": "S15_SCORING_RULE",
    "S15_REFERENCE_SELECTION": "S15_REFERENCE_SELECTION_V3",
    "S16_PRICING": "S16_PRICING_RULE",
    "S16": "S16_PRICING_RULE",
    "DISPATCHER": "DISPATCHER_REFERENCE_CONTINUATION",
    "FEISHU_FEEDBACK": "FEISHU_USER_FEEDBACK",
}

STEP_STAGE_ALIASES = {
    "S08": "S08_FILTER_SUMMARY",
    "S10": "S10_FILTER_SUMMARY",
    "S07_AGE_SLIDER": "S07_AGE",
    "S13_S14_COLLECTION": "S14_COLLECTION",
    "S15_REFERENCE_SELECTION": "S15_REFERENCE_SELECTION_V3",
    "S15_SCORING": "S15_SCORING_RULE",
    "S16_PRICING": "S16_PRICING_RULE",
}


def normalize_runtime_stage(stage: Any) -> str:
    text = str(stage or "").strip()
    return TRACE_STAGE_ALIASES.get(text, text)


def _load_json_compatible_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_runtime_rule_coverage(path: Optional[Path] = None) -> Dict[str, Any]:
    coverage_path = Path(path) if path else DEFAULT_COVERAGE_PATH
    return _load_json_compatible_yaml(coverage_path)


def iter_coverage_clauses(coverage: Optional[Mapping[str, Any]] = None) -> Iterable[Dict[str, Any]]:
    data = coverage or load_runtime_rule_coverage()
    for clause in data.get("clauses", []) or []:
        if isinstance(clause, dict):
            yield clause


def coverage_by_rule_id(coverage: Optional[Mapping[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    return {
        str(clause.get("rule_clause_id", "")): clause
        for clause in iter_coverage_clauses(coverage)
        if clause.get("rule_clause_id")
    }


def coverage_by_stage(coverage: Optional[Mapping[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for clause in iter_coverage_clauses(coverage):
        stage = normalize_runtime_stage(clause.get("runtime_stage"))
        if stage and stage not in result:
            result[stage] = clause
    return result


def coverage_for_stage(stage: Any, coverage: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    normalized = normalize_runtime_stage(stage)
    return dict(coverage_by_stage(coverage).get(normalized, {}))


def coverage_for_step(step_id: Any, coverage: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    step = str(step_id or "").strip()
    stage = STEP_STAGE_ALIASES.get(step, step)
    return coverage_for_stage(stage, coverage)


def coverage_trace_for_step(step_id: Any, coverage: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    clause = coverage_for_step(step_id, coverage)
    if not clause:
        return {
            "rule_clause_id": "",
            "rule_source_file": "",
            "rule_source_version": "",
            "rule_clause_text_summary": "",
            "coverage_status": "NOT_COVERED",
            "allowed_fallbacks": [],
            "forbidden_actions": [],
            "allowed_algorithms": [],
            "performance_budget_ms": None,
            "requires_runtime_trace": True,
        }
    return {
        "rule_clause_id": clause.get("rule_clause_id", ""),
        "rule_source_file": clause.get("rule_source_file", ""),
        "rule_source_version": clause.get("rule_source_version", ""),
        "rule_clause_text_summary": clause.get("rule_clause_text_summary", ""),
        "coverage_status": clause.get("coverage_status", "NOT_COVERED"),
        "allowed_fallbacks": list(clause.get("allowed_fallbacks") or []),
        "forbidden_actions": list(clause.get("forbidden_actions") or []),
        "allowed_algorithms": list(clause.get("allowed_algorithms") or []),
        "performance_budget_ms": clause.get("performance_budget_ms"),
        "requires_runtime_trace": bool(clause.get("requires_runtime_trace", True)),
        "performance_failure_stop_code": clause.get("performance_failure_stop_code", ""),
        "max_xml_dump_count": clause.get("max_xml_dump_count"),
        "max_screenshot_count": clause.get("max_screenshot_count"),
        "max_fallback_count": clause.get("max_fallback_count"),
        "max_micro_adjust_count": clause.get("max_micro_adjust_count"),
        "max_screenshot_detector_count": clause.get("max_screenshot_detector_count"),
    }


def validate_runtime_rule_coverage_config(
    coverage: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[str], List[str]]:
    data = coverage or load_runtime_rule_coverage()
    errors: List[str] = []
    warnings: List[str] = []
    clauses = list(iter_coverage_clauses(data))
    if not clauses:
        return ["coverage_config_empty"], warnings

    seen_rule_ids = set()
    stages = set()
    for index, clause in enumerate(clauses):
        prefix = f"clause[{index}]"
        missing = [field for field in REQUIRED_CLAUSE_FIELDS if field not in clause]
        if missing:
            errors.append(f"{prefix}:missing_fields:{','.join(missing)}")
        rule_id = str(clause.get("rule_clause_id", ""))
        if not rule_id:
            errors.append(f"{prefix}:missing_rule_clause_id")
        elif rule_id in seen_rule_ids:
            errors.append(f"{prefix}:duplicate_rule_clause_id:{rule_id}")
        seen_rule_ids.add(rule_id)

        status = str(clause.get("coverage_status", ""))
        if status not in VALID_COVERAGE_STATUSES:
            errors.append(f"{prefix}:invalid_coverage_status:{status}")

        stage = normalize_runtime_stage(clause.get("runtime_stage"))
        if stage:
            stages.add(stage)

        if status not in {"NEEDS_SOURCE_RULE", "NOT_COVERED"}:
            if not clause.get("runtime_executor_function"):
                errors.append(f"{rule_id}:missing_runtime_executor_function")
            if not clause.get("test_files"):
                errors.append(f"{rule_id}:missing_test_files")
            if not clause.get("evidence_fields"):
                errors.append(f"{rule_id}:missing_evidence_fields")
        else:
            warnings.append(f"{rule_id}:coverage_status:{status}")

    for stage in REQUIRED_RUNTIME_STAGES:
        if stage not in stages:
            errors.append(f"required_stage_not_declared:{stage}")

    return errors, warnings


def compute_runtime_rule_coverage_score(
    coverage: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    clauses = list(iter_coverage_clauses(coverage))
    if not clauses:
        return {
            "coverage_score": 0,
            "coverage_status_counts": {},
            "clause_scores": [],
        }
    status_counts = Counter(str(clause.get("coverage_status", "NOT_COVERED")) for clause in clauses)
    clause_scores = [
        {
            "rule_clause_id": clause.get("rule_clause_id", ""),
            "runtime_stage": clause.get("runtime_stage", ""),
            "coverage_status": clause.get("coverage_status", "NOT_COVERED"),
            "score": COVERAGE_STATUS_SCORES.get(str(clause.get("coverage_status", "")), 0),
        }
        for clause in clauses
    ]
    score = round(sum(item["score"] for item in clause_scores) / max(1, len(clause_scores)), 2)
    return {
        "coverage_score": score,
        "coverage_status_counts": dict(status_counts),
        "clause_scores": clause_scores,
    }


def _record_rule_clause_id(record: Mapping[str, Any]) -> str:
    direct = record.get("rule_clause_id") or record.get("contract_rule_clause_id")
    if direct:
        return str(direct)
    guard = record.get("contract_guard") if isinstance(record.get("contract_guard"), dict) else {}
    return str(guard.get("rule_clause_id") or "")


def _record_stage(record: Mapping[str, Any]) -> str:
    return normalize_runtime_stage(
        record.get("runtime_stage")
        or record.get("stage")
        or record.get("step_id")
        or record.get("contract_step_id")
    )


def _record_action_plan_used(record: Mapping[str, Any]) -> bool:
    return bool(
        record.get("contract_action_plan_used")
        or record.get("contract_action_plan_id")
        or record.get("action_plan_id")
        or record.get("contract_action_plan")
    )


def _record_forbidden_action_used(record: Mapping[str, Any], clause: Mapping[str, Any]) -> bool:
    if bool(record.get("forbidden_action_used")):
        return True
    action = str(record.get("action_algorithm_used") or record.get("action_name") or "")
    return bool(action and action in (clause.get("forbidden_actions") or []))


def validate_runtime_records_against_coverage(
    runtime_records: Sequence[Mapping[str, Any]],
    coverage: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    data = coverage or load_runtime_rule_coverage()
    by_rule = coverage_by_rule_id(data)
    by_stage = coverage_by_stage(data)
    errors: List[str] = []
    warnings: List[str] = []
    runtime_actions_without_clause: List[Dict[str, Any]] = []
    forbidden_fallbacks: List[Dict[str, Any]] = []
    forbidden_actions: List[Dict[str, Any]] = []
    performance_budget_exceeded_stages: List[Dict[str, Any]] = []

    for index, record in enumerate(runtime_records):
        if not isinstance(record, Mapping):
            continue
        stage = _record_stage(record)
        rule_id = _record_rule_clause_id(record)
        clause = by_rule.get(rule_id) if rule_id else by_stage.get(stage, {})
        if not rule_id:
            runtime_actions_without_clause.append({"index": index, "stage": stage})
            errors.append(f"runtime_action_without_rule_clause_id:{stage or index}")
            continue
        if not clause:
            errors.append(f"runtime_action_clause_not_declared:{rule_id}")
            continue

        coverage_status = str(clause.get("coverage_status", "NOT_COVERED"))
        if coverage_status in {"NOT_COVERED", "NEEDS_SOURCE_RULE"} and bool(record.get("continue_allowed", True)):
            errors.append(f"runtime_action_uses_uncovered_or_source_rule_needed_clause:{rule_id}")

        if bool(clause.get("requires_runtime_trace", True)) and not _record_action_plan_used(record):
            errors.append(f"runtime_action_without_contract_action_plan:{rule_id}")

        if bool(record.get("runtime_bypassed_action_plan")):
            errors.append(f"runtime_bypassed_action_plan:{rule_id}")

        action_algorithm = str(record.get("action_algorithm_used") or "")
        allowed_algorithms = set(clause.get("allowed_algorithms") or [])
        if action_algorithm and allowed_algorithms and action_algorithm not in allowed_algorithms:
            warnings.append(f"runtime_action_algorithm_not_listed_by_clause:{rule_id}:{action_algorithm}")

        if bool(record.get("fallback_used")):
            fallback_name = str(record.get("fallback_name") or "")
            allowed_fallbacks = set(clause.get("allowed_fallbacks") or [])
            fallback_allowed = bool(fallback_name and fallback_name in allowed_fallbacks)
            if record.get("fallback_allowed_by_clause") is False or not fallback_allowed:
                item = {"rule_clause_id": rule_id, "stage": stage, "fallback_name": fallback_name}
                forbidden_fallbacks.append(item)
                errors.append(f"fallback_not_allowed_by_clause:{rule_id}:{fallback_name}")

        if _record_forbidden_action_used(record, clause):
            item = {"rule_clause_id": rule_id, "stage": stage, "action": record.get("action_algorithm_used")}
            forbidden_actions.append(item)
            errors.append(f"forbidden_action_used:{rule_id}:{record.get('action_algorithm_used')}")

        if bool(record.get("performance_budget_exceeded")):
            item = {
                "rule_clause_id": rule_id,
                "stage": stage,
                "actual_duration_ms": record.get("actual_duration_ms"),
                "performance_budget_ms": record.get("performance_budget_ms") or clause.get("performance_budget_ms"),
            }
            performance_budget_exceeded_stages.append(item)
            warnings.append(f"performance_budget_exceeded:{rule_id}")

        if bool(record.get("early_exit_allowed")) and not record.get("early_exit_rule_clause_id"):
            errors.append("reference_early_exit_without_source_rule")

        if rule_id == "S11_REPORT_ENTRY_BIND_VIEW_FULL_REPORT":
            fields_to_check = (
                "click_source",
                "s11_report_entry_click_source",
                "binding_source",
                "action_algorithm_used",
                "fallback_name",
                "click_target_source",
            )
            used_forbidden = [
                str(record.get(field) or "")
                for field in fields_to_check
                if _legacy_text_matches_rule(record.get(field), ("old_s11_screenshot_click",))
            ]
            key_flag_used = any(
                _legacy_text_matches_rule(key, ("old_s11_screenshot_click",)) and record.get(key) is True
                for key in record
            )
            if (
                used_forbidden
                or key_flag_used
                or record.get("screenshot_detector_used") is True
                or record.get("screenshot_used_for_click") is True
                or record.get("visual_detector_used_for_click") is True
                or record.get("debug_layout_probe_used_for_click") is True
            ):
                errors.append("S11_REPORT_ENTRY_VISUAL_CLICK_NOT_AUTHORIZED_BY_PAGE_CONTRACT")

    return {
        "errors": errors,
        "warnings": warnings,
        "runtime_actions_without_clause": runtime_actions_without_clause,
        "forbidden_fallbacks": forbidden_fallbacks,
        "forbidden_actions": forbidden_actions,
        "performance_budget_exceeded_stages": performance_budget_exceeded_stages,
    }


def build_runtime_rule_coverage_report(
    runtime_records: Optional[Sequence[Mapping[str, Any]]] = None,
    coverage: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    data = coverage or load_runtime_rule_coverage()
    config_errors, config_warnings = validate_runtime_rule_coverage_config(data)
    record_report = validate_runtime_records_against_coverage(runtime_records or [], data)
    score_report = compute_runtime_rule_coverage_score(data)
    not_covered = [
        clause.get("rule_clause_id", "")
        for clause in iter_coverage_clauses(data)
        if clause.get("coverage_status") == "NOT_COVERED"
    ]
    needs_source = [
        clause.get("rule_clause_id", "")
        for clause in iter_coverage_clauses(data)
        if clause.get("coverage_status") == "NEEDS_SOURCE_RULE"
    ]
    errors = config_errors + list(record_report["errors"])
    warnings = config_warnings + list(record_report["warnings"])
    return {
        "status": "RUNTIME_RULE_COVERAGE_CHECK_PASSED" if not errors else "RUNTIME_RULE_COVERAGE_CHECK_FAILED",
        "ok": not errors,
        "coverage_score": score_report["coverage_score"],
        "coverage_status_counts": score_report["coverage_status_counts"],
        "not_covered_clauses": not_covered,
        "needs_source_rule_clauses": needs_source,
        "runtime_actions_without_clause": record_report["runtime_actions_without_clause"],
        "forbidden_fallbacks": record_report["forbidden_fallbacks"],
        "forbidden_actions": record_report["forbidden_actions"],
        "performance_budget_exceeded_stages": record_report["performance_budget_exceeded_stages"],
        "errors": errors,
        "warnings": warnings,
        "clause_scores": score_report["clause_scores"],
    }
