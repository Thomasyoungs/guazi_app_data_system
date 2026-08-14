"""Runtime knowledge-loop lookup before asking for manual intervention."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_SOLUTION_KEYS = {
    "solution_id",
    "issue_code",
    "symptoms",
    "root_cause",
    "steps",
    "allowed_auto_actions",
    "manual_required_actions",
    "forbidden_actions",
    "max_auto_retries",
    "approved",
    "created_at",
    "updated_at",
}


class LearningLoop:
    def __init__(
        self,
        project_root: Path,
        exception_config: dict[str, Any],
        pages_config: dict[str, Any] | None = None,
        actions_config: dict[str, Any] | None = None,
    ) -> None:
        self.project_root = project_root
        self.solutions_path = project_root / "knowledge_base" / "solutions.jsonl"
        self.policy = exception_config.get("knowledge_policy", {})
        self.max_auto_recovery_attempts = int(self.policy.get("max_auto_recovery_attempts", 1))
        self.rules = list(exception_config.get("knowledge_rules", []))
        self.pages = {page["id"]: page for page in (pages_config or {}).get("pages", [])}
        self.system_action_whitelist = (actions_config or {}).get("runtime_recovery_action_whitelist", {})
        local_rules = project_root / "knowledge_base" / "rules.json"
        if local_rules.exists():
            try:
                data = json.loads(local_rules.read_text(encoding="utf-8"))
                self.rules.extend(data.get("rules", []))
            except json.JSONDecodeError:
                self.rules.append({"when": "knowledge_base_parse_failed", "then": "manual_intervention"})

    def suggest(self, state_id: str, recognized_text: str, issue_code: str | None = None, attempts: int = 0) -> dict[str, Any] | None:
        if attempts >= self.max_auto_recovery_attempts:
            return None
        text = recognized_text or ""
        for rule in self.rules:
            if self.policy.get("auto_callable_requires_approved_true", True) and rule.get("approved") is not True:
                continue
            if attempts >= int(rule.get("attempt_limit", self.max_auto_recovery_attempts)):
                continue
            when = str(rule.get("when", ""))
            if state_id in when and all(token in text for token in _tokens_after_contains(when)):
                return {"rule": rule, "suggested_action": rule.get("then")}
            if issue_code and issue_code in when:
                return {"rule": rule, "suggested_action": rule.get("then")}
        return None

    def lookup_issue(
        self,
        issue: dict[str, Any],
        recognized_text: str | None = None,
        attempts: int = 0,
    ) -> dict[str, Any]:
        """Return an approved, whitelisted recovery plan for a recorded issue."""
        code = str(issue.get("code", ""))
        state_id = str(issue.get("state_id") or "")
        allowed_actions = self.allowed_actions_for_issue(code, state_id)
        recognized_blob = recognized_text or f'{issue.get("message", "")} {json.dumps(issue.get("context", {}), ensure_ascii=False)}'
        suggested = self.suggest(state_id, recognized_blob, issue_code=code, attempts=attempts)
        matching = [solution for solution in self.load_solutions() if self._matches_issue(solution, code, recognized_text or "", issue)]
        approved = [solution for solution in matching if solution.get("approved") is True]
        candidates = [solution for solution in matching if solution.get("approved") is not True]

        base = {
            "queried": True,
            "issue_code": code,
            "state_id": state_id,
            "attempts": attempts,
            "allowed_actions_scope": sorted(allowed_actions),
            "manual_intervention_required": True,
            "knowledge_rule_matched": suggested is not None,
            "matched_rule_id": self._rule_identifier(suggested["rule"]) if suggested else None,
            "suggested_action": suggested.get("suggested_action") if suggested else None,
            "auto_continue_allowed": False,
            "stop_code": "LEARNING_LOOP_REQUIRES_HUMAN_REVIEW",
        }
        if not approved:
            base["status"] = "candidate_solution_only" if candidates else "no_approved_solution"
            base["candidate_solution_count"] = len(candidates)
            base["allowed_auto_actions"] = []
            return base

        solution = approved[0]
        retry_limit = self._effective_retry_limit(code, solution)
        if attempts >= retry_limit:
            return {
                **base,
                "status": "auto_retry_limit_reached",
                "solution_id": solution.get("solution_id"),
                "matched_rule_id": solution.get("solution_id"),
                "knowledge_rule_matched": True,
                "suggested_action": self._suggested_action_from_solution(solution),
                "max_auto_retries": retry_limit,
                "allowed_auto_actions": [],
            }

        requested_actions = [str(action) for action in solution.get("allowed_auto_actions", [])]
        blocked_actions = [action for action in requested_actions if action not in allowed_actions]
        if blocked_actions:
            if solution.get("scope_intersection_allowed") is True:
                intersected_actions = [action for action in requested_actions if action in allowed_actions]
                return {
                    **base,
                    "status": "approved_solution_matched",
                    "solution_id": solution.get("solution_id"),
                    "solution_status": self.solution_status(solution),
                    "matched_rule_id": solution.get("solution_id"),
                    "knowledge_rule_matched": True,
                    "suggested_action": self._suggested_action_from_solution({"allowed_auto_actions": intersected_actions, "manual_required_actions": solution.get("manual_required_actions", [])}),
                    "blocked_actions": blocked_actions,
                    "allowed_auto_actions": intersected_actions,
                    "manual_required_actions": solution.get("manual_required_actions", []),
                    "forbidden_actions": solution.get("forbidden_actions", []),
                    "max_auto_retries": retry_limit,
                    "manual_intervention_required": bool(solution.get("manual_required_actions")) and not intersected_actions,
                    "auto_continue_allowed": bool(intersected_actions),
                    "stop_code": None if intersected_actions else "LEARNING_LOOP_REQUIRES_HUMAN_REVIEW",
                }
            return {
                **base,
                "status": "approved_solution_blocked_by_state_whitelist",
                "solution_id": solution.get("solution_id"),
                "solution_status": self.solution_status(solution),
                "matched_rule_id": solution.get("solution_id"),
                "knowledge_rule_matched": True,
                "suggested_action": self._suggested_action_from_solution(solution),
                "blocked_actions": blocked_actions,
                "allowed_auto_actions": [],
                "manual_required_actions": solution.get("manual_required_actions", []),
                "forbidden_actions": solution.get("forbidden_actions", []),
                "max_auto_retries": retry_limit,
            }

        return {
            **base,
            "status": "approved_solution_matched",
            "solution_id": solution.get("solution_id"),
            "solution_status": self.solution_status(solution),
            "matched_rule_id": solution.get("solution_id"),
            "knowledge_rule_matched": True,
            "suggested_action": self._suggested_action_from_solution(solution),
            "allowed_auto_actions": requested_actions,
            "manual_required_actions": solution.get("manual_required_actions", []),
            "forbidden_actions": solution.get("forbidden_actions", []),
            "max_auto_retries": retry_limit,
            "manual_intervention_required": bool(solution.get("manual_required_actions")),
            "auto_continue_allowed": bool(requested_actions),
            "stop_code": None if requested_actions else "LEARNING_LOOP_REQUIRES_HUMAN_REVIEW",
        }

    def _effective_retry_limit(self, issue_code: str, solution: dict[str, Any]) -> int:
        solution_limit = int(solution.get("max_auto_retries", self.max_auto_recovery_attempts))
        if issue_code == "S13_HISTORY_REPAIR_CELL_CLICK_FAILED":
            return solution_limit
        return min(solution_limit, self.max_auto_recovery_attempts)

    def load_solutions(self) -> list[dict[str, Any]]:
        if not self.solutions_path.exists():
            return []
        solutions: list[dict[str, Any]] = []
        for line_number, line in enumerate(self.solutions_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                solution = json.loads(line)
            except json.JSONDecodeError:
                solutions.append({
                    "issue_code": "KNOWLEDGE_PARSE_FAILED",
                    "approved": False,
                    "status": "candidate",
                    "parse_error_line": line_number,
                })
                continue
            solutions.append(solution)
        return solutions

    def append_candidate_solution(self, solution: dict[str, Any]) -> dict[str, Any]:
        """Persist a human-provided solution as candidate until approved."""
        now = datetime.now(timezone.utc).isoformat()
        candidate = {
            **solution,
            "approved": False,
            "status": "candidate",
            "created_at": solution.get("created_at", now),
            "updated_at": now,
        }
        missing = REQUIRED_SOLUTION_KEYS - set(candidate)
        if missing:
            raise ValueError(f"Solution missing keys: {sorted(missing)}")
        self.solutions_path.parent.mkdir(parents=True, exist_ok=True)
        with self.solutions_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(candidate, ensure_ascii=False) + "\n")
        return candidate

    def upsert_solution(self, solution: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            **solution,
            "status": "approved" if solution.get("approved") is True else str(solution.get("status") or "candidate"),
            "created_at": solution.get("created_at", now),
            "updated_at": now,
        }
        missing = REQUIRED_SOLUTION_KEYS - set(payload)
        if missing:
            raise ValueError(f"Solution missing keys: {sorted(missing)}")

        self.solutions_path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.load_solutions()
        target_id = str(payload.get("solution_id") or "")
        updated = False
        for index, row in enumerate(rows):
            if str(row.get("solution_id") or "") == target_id:
                payload["created_at"] = row.get("created_at", payload["created_at"])
                rows[index] = payload
                updated = True
                break
        if not updated:
            rows.append(payload)

        with self.solutions_path.open("w", encoding="utf-8") as file:
            for row in rows:
                if row.get("parse_error_line"):
                    continue
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
        return payload

    def allowed_actions_for_state(self, state_id: str) -> set[str]:
        if state_id in self.pages:
            return set(self.pages[state_id].get("allowed_actions", []))
        return set(self.system_action_whitelist.get(state_id, []))

    def allowed_actions_for_issue(self, issue_code: str, state_id: str) -> set[str]:
        if issue_code in self.system_action_whitelist:
            return set(self.system_action_whitelist.get(issue_code, []))
        return self.allowed_actions_for_state(state_id)

    @staticmethod
    def solution_status(solution: dict[str, Any]) -> str:
        if solution.get("approved") is True:
            return "approved"
        return str(solution.get("status") or "candidate")

    @staticmethod
    def _rule_identifier(rule: dict[str, Any]) -> str | None:
        value = rule.get("rule_id") or rule.get("solution_id") or rule.get("issue_code") or rule.get("when")
        return str(value) if value else None

    @staticmethod
    def _suggested_action_from_solution(solution: dict[str, Any]) -> str | None:
        allowed = [str(action) for action in solution.get("allowed_auto_actions", []) if str(action).strip()]
        if allowed:
            return allowed[0]
        manual = [str(action) for action in solution.get("manual_required_actions", []) if str(action).strip()]
        if manual:
            return manual[0]
        return None

    @staticmethod
    def _matches_issue(solution: dict[str, Any], issue_code: str, recognized_text: str, issue: dict[str, Any]) -> bool:
        codes = {str(solution.get("issue_code", ""))}
        codes.update(str(code) for code in solution.get("related_issue_codes", []))
        if issue_code not in codes:
            return False
        classification = issue.get("classification") or {}
        if classification.get("recommended_solution_id") == solution.get("solution_id"):
            return True
        symptoms = [str(item) for item in solution.get("symptoms", [])]
        if not symptoms:
            return True
        haystack = json.dumps(issue.get("context", {}), ensure_ascii=False) + " " + recognized_text + " " + str(issue.get("message", ""))
        return any(symptom in haystack for symptom in symptoms) or issue_code in codes


def _tokens_after_contains(rule_text: str) -> list[str]:
    if "contains" not in rule_text:
        return []
    return [part.strip() for part in rule_text.split("contains", 1)[1].split("+") if part.strip()]
