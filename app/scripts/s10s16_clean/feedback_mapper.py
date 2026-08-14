"""Issue-code to feedback-template mapping boundary.

This module maps known issue/stop codes to templates only. It must not infer
root cause from absent pricing fields or mutate task status.
"""

from __future__ import annotations

from . import issue_codes


ISSUE_TO_TEMPLATE = {
    issue_codes.S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE: "POST_START_S12_CLAIM_FIELDS_NEEDS_REVIEW",
    issue_codes.S12_TO_S13_REGION_PROOF_NOT_CONFIRMED: "POST_START_S12_TO_S13_REGION_PROOF_NOT_CONFIRMED",
    issue_codes.S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED: "POST_START_S12_TO_S13_REGION_PROOF_NOT_CONFIRMED",
    issue_codes.S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED_AFTER_S12_BODY_REACHED: "POST_START_S12_TO_S13_REGION_PROOF_NOT_CONFIRMED",
}


def feedback_template_for_issue(issue_code: str) -> str:
    return ISSUE_TO_TEMPLATE.get(str(issue_code or ""), "POST_START_REFERENCE_COLLECTION_INCOMPLETE")

