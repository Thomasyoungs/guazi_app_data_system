"""Structured exception and issue recording."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class GuaziFlowError(RuntimeError):
    def __init__(self, code: str, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.context = context or {}


class IssueRecorder:
    def __init__(
        self,
        path: Path,
        exception_config: dict[str, Any],
        learning_loop: Any | None = None,
        issue_classifier: Any | None = None,
        audit: Any | None = None,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.exception_config = exception_config.get("exceptions", exception_config)
        self.learning_loop_trigger_map = exception_config.get("learning_loop_trigger_map", {})
        self.learning_loop = learning_loop
        self.issue_classifier = issue_classifier
        self.audit = audit

    def record(
        self,
        code: str,
        state_id: str | None,
        message: str | None = None,
        context: dict[str, Any] | None = None,
        resolution: str | None = None,
        recognized_text: str | None = None,
        attempts: int = 0,
        classification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        spec = self.exception_config.get(code, {})
        record = {
            "issue_id": f"ISS-{uuid.uuid4().hex[:12]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "code": code,
            "severity": spec.get("severity", "unknown"),
            "state_id": state_id,
            "message": message or spec.get("message", code),
            "context": context or {},
            "resolution": resolution or spec.get("default_resolution", "manual_intervention"),
            "workflow": ["issue_recorded"],
        }
        if classification:
            record["classification"] = classification
            record["workflow"].append("issue_classified")
        if self.learning_loop:
            record["knowledge_lookup"] = self.learning_loop.lookup_issue(record, recognized_text=recognized_text, attempts=attempts)
            record["workflow"].append("knowledge_base_queried")
        if self._should_trigger_learning_loop(code, spec):
            record["learning_loop"] = self._build_learning_loop_payload(
                record=record,
                recognized_text=recognized_text,
                attempts=attempts,
            )
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.audit:
            self.audit.log(
                "issue_recorded",
                issue_id=record["issue_id"],
                issue_code=record["code"],
                state_id=state_id,
                resolution=record["resolution"],
                classification=classification,
                learning_loop=record.get("learning_loop"),
            )
        return record

    def _should_trigger_learning_loop(self, code: str, spec: dict[str, Any]) -> bool:
        if code in self.learning_loop_trigger_map:
            return True
        resolution = str(spec.get("default_resolution", ""))
        return "knowledge_lookup" in resolution or resolution == "block_and_query_learning_loop"

    def _build_learning_loop_payload(
        self,
        *,
        record: dict[str, Any],
        recognized_text: str | None,
        attempts: int,
    ) -> dict[str, Any]:
        code = str(record.get("code", ""))
        trigger = self.learning_loop_trigger_map.get(code, {})
        lookup = record.get("knowledge_lookup")
        evidence_paths = self._collect_evidence_paths(record)
        if not self.learning_loop:
            return {
                "triggered": False,
                "trigger_type": trigger.get("trigger_type"),
                "input_evidence_paths": evidence_paths,
                "knowledge_rule_matched": False,
                "matched_rule_id": None,
                "suggested_action": None,
                "auto_continue_allowed": False,
                "requires_human_review": True,
                "stop_code": "LEARNING_LOOP_REQUIRES_HUMAN_REVIEW",
                "reason": "learning_loop_unavailable",
            }

        if lookup is None:
            lookup = self.learning_loop.lookup_issue(record, recognized_text=recognized_text, attempts=attempts)
            record["knowledge_lookup"] = lookup

        return {
            "triggered": True,
            "trigger_type": trigger.get("trigger_type"),
            "input_evidence_paths": evidence_paths,
            "knowledge_rule_matched": bool(lookup.get("knowledge_rule_matched")),
            "matched_rule_id": lookup.get("matched_rule_id") or lookup.get("solution_id"),
            "suggested_action": lookup.get("suggested_action"),
            "auto_continue_allowed": bool(lookup.get("auto_continue_allowed")),
            "requires_human_review": bool(lookup.get("manual_intervention_required", True)),
            "stop_code": lookup.get("stop_code"),
            "status": lookup.get("status"),
        }

    @staticmethod
    def _collect_evidence_paths(record: dict[str, Any]) -> list[str]:
        paths: list[str] = []

        def collect(node: Any, key_hint: str = "") -> None:
            key_text = str(key_hint).lower()
            if isinstance(node, dict):
                for child_key, child_value in node.items():
                    collect(child_value, str(child_key))
                return
            if isinstance(node, list):
                for item in node:
                    collect(item, key_hint)
                return
            if isinstance(node, str) and node.strip():
                if any(token in key_text for token in ("path", "screenshot", "xml", "dump")):
                    paths.append(node)

        collect(record.get("context") or {})
        return list(dict.fromkeys(paths))

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def classify_and_record(
        self,
        *,
        fallback_code: str,
        state_id: str | None,
        message: str,
        context: dict[str, Any] | None,
        current_state: str,
        intended_action: str,
        expected_next_state: str,
        actual_next_state: str,
        actual_clicked_target: Any,
        before_xml: str,
        after_xml: str,
        page_contract: dict[str, Any] | None = None,
        action_contract: dict[str, Any] | None = None,
        task_context: dict[str, Any] | None = None,
        resolution: str | None = None,
        recognized_text: str | None = None,
        attempts: int = 0,
    ) -> dict[str, Any]:
        classification: dict[str, Any] | None = None
        if self.issue_classifier:
            classification = self.issue_classifier.classify(
                current_state=current_state,
                intended_action=intended_action,
                expected_next_state=expected_next_state,
                actual_next_state=actual_next_state,
                actual_clicked_target=actual_clicked_target,
                before_xml=before_xml,
                after_xml=after_xml,
                page_contract=page_contract,
                action_contract=action_contract,
                task_context=task_context,
            )
            solution_record = classification.get("solution_record")
            if solution_record and self.learning_loop:
                self.learning_loop.upsert_solution(solution_record)

        issue_code = fallback_code
        issue_message = message
        issue_context = dict(context or {})
        if classification:
            issue_code = str(classification.get("issue_code") or fallback_code)
            issue_message = str(classification.get("root_cause") or message)
            issue_context.update(
                {
                    "classifier_root_cause": classification.get("root_cause"),
                    "recommended_solution_id": classification.get("recommended_solution_id"),
                    "candidate_or_approved": classification.get("candidate_or_approved"),
                    "classifier_confidence": classification.get("confidence"),
                    "classifier_evidence": classification.get("evidence"),
                }
            )
        return self.record(
            issue_code,
            state_id,
            issue_message,
            issue_context,
            resolution,
            recognized_text=recognized_text,
            attempts=attempts,
            classification=classification,
        )
