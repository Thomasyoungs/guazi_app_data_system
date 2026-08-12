import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_task_store import FeishuTaskStore  # noqa: E402
import runtime_s10_to_s16_mainline as runtime_s10_to_s16  # noqa: E402
from runtime_s10_to_s16_mainline import (  # noqa: E402
    REFERENCE_EXCLUDED_S12_CLAIM_FIELDS_MISSING,
    S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE,
    S12_CLAIM_FIELDS_NOT_READABLE,
    S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE,
    S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW,
    S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW,
    _extract_s12_claim_fields_from_snapshot,
    _is_valid_extent,
    _recover_s12_claim_fields,
    _s12_claim_fields_missing_decision,
)


def fixed_clock():
    return datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)


def s12_snapshot(text: str, *, screenshot: str = "s12.png", xml: str = "s12.xml") -> dict:
    return {
        "visible_blob": text,
        "visible_texts": [text],
        "fresh_xml": f"<hierarchy><node text=\"{text}\" /></hierarchy>",
        "screenshot_path": screenshot,
        "xml_path": xml,
        "nodes": [{"text": text, "bounds": (10, 10, 400, 80), "labels": [text]}],
    }


def context_for_reference(index: int, trisame_count: int = 10, **extra) -> dict:
    context = {
        "current_reference_index": index,
        "current_reference": {"reference_index": index},
        "first_stage_evidence": {"trisame_cards_count": trisame_count},
    }
    context.update(extra)
    return context


class FakeSwipeClient:
    def __init__(self):
        self.commands = []

    def run(self, command, timeout=None):
        self.commands.append({"command": list(command), "timeout": timeout})
        return ""


class S12ClaimFieldCollectionAndFeedbackPriorityTest(unittest.TestCase):
    def test_recovery_extracts_fields_after_fresh_capture(self):
        first = s12_snapshot("瓜子官方检测报告")
        fresh = s12_snapshot("保险理赔记录 理赔次数 1次 最大金额 2300元", screenshot="fresh.png", xml="fresh.xml")

        def hook(_context, _snapshot):
            return {
                "snapshot": fresh,
                "attempts": [{"attempt_index": 1, "source": "fresh_capture"}],
            }

        recovered, trace = _recover_s12_claim_fields({"s12_claim_field_recovery_hook": hook}, first)

        self.assertEqual(recovered["screenshot_path"], "fresh.png")
        self.assertEqual(trace["s12_claim_count_extracted"], 1)
        self.assertEqual(trace["s12_max_amount_extracted"], 2300.0)
        self.assertFalse(trace["s12_field_missing_after_recovery"])

    def test_recovery_extracts_fields_after_scroll_capture(self):
        first = s12_snapshot("瓜子官方检测报告")
        scrolled = s12_snapshot("下拉后 保险理赔记录 出险次数 2次 最高金额 1.5万")

        def hook(_context, _snapshot):
            return {
                "snapshot": scrolled,
                "attempts": [
                    {"attempt_index": 1, "source": "fresh_capture", "missing_fields": ["claim_count", "max_amount"]},
                    {"attempt_index": 2, "source": "small_scroll_fresh"},
                ],
                "s12_scroll_recovery_attempted": True,
            }

        _recovered, trace = _recover_s12_claim_fields({"s12_claim_field_recovery_hook": hook}, first)

        self.assertEqual(trace["s12_claim_count_extracted"], 2)
        self.assertEqual(trace["s12_max_amount_extracted"], 15000.0)
        self.assertTrue(trace["s12_scroll_recovery_attempted"])

    def test_no_claim_text_allows_zero_count_and_zero_amount_with_evidence(self):
        extracted = _extract_s12_claim_fields_from_snapshot(s12_snapshot("保险理赔记录 未查询到理赔 无出险"))

        self.assertEqual(extracted["claim_count"], 0)
        self.assertEqual(extracted["max_amount"], 0)
        self.assertEqual(extracted["missing_fields"], [])

    def test_claim_count_positive_without_amount_remains_missing(self):
        extracted = _extract_s12_claim_fields_from_snapshot(s12_snapshot("保险理赔记录 理赔次数 2次"))

        self.assertEqual(extracted["claim_count"], 2)
        self.assertIsNone(extracted["max_amount"])
        self.assertEqual(extracted["missing_fields"], ["max_amount"])

    def test_non_final_reference_s12_missing_continues_next_reference(self):
        trace = {"s12_missing_fields": ["claim_count", "max_amount"]}
        decision = _s12_claim_fields_missing_decision(context_for_reference(8, 10), trace)

        self.assertEqual(decision["status"], "CONTINUE_NEXT_REFERENCE")
        self.assertEqual(decision["issue_code"], S12_FIELD_MISSING_CONTINUE_NEXT_REFERENCE)
        self.assertEqual(decision["excluded_reference_status"], REFERENCE_EXCLUDED_S12_CLAIM_FIELDS_MISSING)
        self.assertEqual(decision["excluded_reason"], S12_CLAIM_FIELDS_NOT_READABLE)
        self.assertEqual(decision["next_reference_index"], 9)

    def test_final_candidate_s12_missing_goes_to_needs_review(self):
        trace = {"s12_missing_fields": ["claim_count", "max_amount"]}
        context = context_for_reference(5, 10, selection={"final_reference_candidate_index": 5})
        decision = _s12_claim_fields_missing_decision(context, trace)

        self.assertEqual(decision["status"], "NEEDS_REVIEW")
        self.assertEqual(decision["issue_code"], S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW)
        self.assertTrue(decision["manual_review_required"])

    def test_s12_missing_without_more_references_goes_to_needs_review(self):
        trace = {"s12_missing_fields": ["claim_count", "max_amount"]}
        decision = _s12_claim_fields_missing_decision(context_for_reference(10, 10), trace)

        self.assertEqual(decision["status"], "NEEDS_REVIEW")
        self.assertEqual(decision["issue_code"], S12_FIELDS_MISSING_NO_MORE_REFERENCE_NEEDS_REVIEW)
        self.assertTrue(decision["manual_review_required"])

    def test_feedback_prefers_latest_s12_field_missing_over_low_score_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FeishuTaskStore(Path(temp) / "feishu_tasks", clock=fixed_clock)
            result = {
                "task_id": "FS20260702_0011",
                "status": "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
                "issue_code": "FIELD_MISSING",
                "current_state": "S12",
                "current_reference_index": 8,
                "current_reference": {
                    "reference_index": 8,
                    "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                    "s14_low_score_skip_triggered": True,
                    "next_reference_index": 9,
                    "s12_claim_field_trace": {
                        "s12_claim_field_recovery_attempted": True,
                        "s12_missing_fields": ["claim_count", "max_amount"],
                    },
                },
                "issue_context": {
                    "stage": "S12",
                    "s12_claim_field_trace": {
                        "s12_claim_field_recovery_attempted": True,
                        "s12_missing_fields": ["claim_count", "max_amount"],
                        "screenshot_path": "artifacts/screenshots/s12.png",
                        "xml_path": "artifacts/debug/s12.xml",
                    },
                },
                "dispatcher_reference_loop_state": {
                    "continue_reason": "EARLY_EXIT_CONTINUE_NEXT_REFERENCE",
                    "next_reference_index": 9,
                    "remaining_reference_count": 1,
                },
            }

            details = store.concrete_failure_details(
                "FS20260702_0011",
                status={"status": "FAILED", "task_id": "FS20260702_0011", "start_ack_sent": True},
                errors=["RESULT_MISSING_REQUIRED_PRICING_FIELDS"],
                result=json.loads(json.dumps(result, ensure_ascii=False)),
            )

        self.assertIn("理赔次数/最大金额", details["business_reply_text"])
        self.assertNotIn("参考车低分跳过后，系统未能继续采集下一辆参考车", details["business_reply_text"])
        self.assertEqual(details["highest_stage"], "S12")

    def test_feedback_for_s12_needs_review_uses_manual_review_template(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FeishuTaskStore(Path(temp) / "feishu_tasks", clock=fixed_clock)
            result = {
                "task_id": "FS20260702_0011",
                "status": "NEEDS_REVIEW",
                "business_status": "NEEDS_REVIEW",
                "manual_review_required": True,
                "issue_code": S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW,
                "current_state": "S12",
                "current_reference_index": 5,
            }

            details = store.concrete_failure_details(
                "FS20260702_0011",
                status={"status": "NEEDS_REVIEW", "task_id": "FS20260702_0011", "start_ack_sent": True},
                errors=[S12_FIELDS_MISSING_ON_FINAL_REFERENCE_CANDIDATE_NEEDS_REVIEW],
                result=result,
            )

        self.assertIn("需要人工复核定价", details["business_reply_text"])
        self.assertIn("理赔次数/最大金额", details["business_reply_text"])
        self.assertNotIn("参考车低分跳过后", details["business_reply_text"])

    def test_feedback_prefers_s13_history_count_over_low_score_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FeishuTaskStore(Path(temp) / "feishu_tasks", clock=fixed_clock)
            result = {
                "task_id": "FS20260703_0002",
                "status": "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED",
                "issue_code": "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED",
                "current_state": "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED",
                "current_reference_index": 4,
                "current_reference": {
                    "reference_index": 4,
                    "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                    "s14_low_score_skip_triggered": True,
                    "next_reference_index": 5,
                },
                "reference_history": [
                    {
                        "reference_index": 3,
                        "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                        "s14_low_score_skip_triggered": True,
                        "next_reference_index": 4,
                    }
                ],
            }

            details = store.concrete_failure_details(
                "FS20260703_0002",
                status={"status": "FAILED", "task_id": "FS20260703_0002", "start_ack_sent": True},
                errors=["S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED"],
                result=result,
            )

        self.assertEqual(details["feedback_selected_reason_code"], "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED")
        self.assertEqual(details["feedback_selected_template"], "POST_START_S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED")
        self.assertTrue(details["feedback_low_score_evidence_present"])
        self.assertTrue(details["feedback_low_score_evidence_ignored_due_to_terminal_issue"])
        self.assertIn("历史修复记录采集阶段", details["business_reply_text"])
        self.assertNotIn("低分跳过后", details["business_reply_text"])

    def test_feedback_prefers_s12_to_s13_region_proof_over_low_score_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FeishuTaskStore(Path(temp) / "feishu_tasks", clock=fixed_clock)
            result = {
                "task_id": "FS20260703_0005",
                "status": "S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED",
                "issue_code": "S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED",
                "current_state": "S12_TO_S13",
                "current_reference_index": 4,
                "current_reference": {
                    "reference_index": 4,
                    "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                    "s14_low_score_skip_triggered": True,
                    "s12_to_s13_region_proof": {
                        "s12_to_s13_proof_confirmed": False,
                        "s12_to_s13_transition_allowed": False,
                    },
                },
            }

            details = store.concrete_failure_details(
                "FS20260703_0005",
                status={"status": "FAILED", "task_id": "FS20260703_0005", "start_ack_sent": True},
                errors=["S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED"],
                result=result,
            )

        self.assertEqual(details["feedback_selected_reason_code"], "S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED")
        self.assertEqual(details["feedback_selected_template"], "POST_START_S12_TO_S13_REGION_PROOF_NOT_CONFIRMED")
        self.assertIn("车身外观", details["business_reply_text"])
        self.assertNotIn("低分跳过", details["business_reply_text"])

    def test_feedback_prefers_s12_runtime_exception_over_low_score_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FeishuTaskStore(Path(temp) / "feishu_tasks", clock=fixed_clock)
            result = {
                "task_id": "FS20260703_0003",
                "status": "RUN_FAILED_WITH_ISSUE",
                "issue_code": "SECOND_STAGE_RUNTIME_EXCEPTION",
                "current_state": "RUN_FAILED_WITH_ISSUE",
                "failed_state": "S12",
                "current_reference_index": 6,
                "exception_type": "IndexError",
                "exception_message": "tuple index out of range",
                "traceback_tail": 'File "scripts/runtime_s10_to_s16_mainline.py", line 10855, in _recover_s12_claim_fields',
                "current_reference": {
                    "reference_index": 6,
                    "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                    "s14_low_score_skip_triggered": True,
                    "next_reference_index": 7,
                },
                "reference_history": [
                    {
                        "reference_index": 5,
                        "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                        "s14_low_score_skip_triggered": True,
                        "next_reference_index": 6,
                    }
                ],
            }

            details = store.concrete_failure_details(
                "FS20260703_0003",
                status={"status": "FAILED", "task_id": "FS20260703_0003", "start_ack_sent": True},
                errors=["SECOND_STAGE_RUNTIME_EXCEPTION"],
                result=result,
            )

        self.assertEqual(details["feedback_selected_reason_code"], "SECOND_STAGE_RUNTIME_EXCEPTION")
        self.assertEqual(details["feedback_selected_template"], "POST_START_S12_RUNTIME_EXCEPTION")
        self.assertTrue(details["feedback_low_score_evidence_present"])
        self.assertTrue(details["feedback_low_score_evidence_ignored_due_to_terminal_issue"])
        self.assertEqual(details["root_exception_function"], "_recover_s12_claim_fields")
        self.assertIn("理赔字段采集阶段出现系统异常", details["business_reply_text"])
        self.assertNotIn("低分跳过后", details["business_reply_text"])

    def test_recovery_hook_index_error_is_structured_missing_fields(self):
        first = s12_snapshot("瓜子官方检测报告")

        def hook(_context, _snapshot):
            raise IndexError("tuple index out of range")

        _recovered, trace = _recover_s12_claim_fields({"s12_claim_field_recovery_hook": hook}, first)

        self.assertTrue(trace["s12_field_missing_after_recovery"])
        self.assertTrue(trace["s12_recovery_candidate_skipped"])
        self.assertTrue(trace["s12_recovery_index_error_prevented"])
        self.assertEqual(trace["s12_missing_fields"], ["claim_count", "max_amount"])

    def test_recovery_extract_index_error_is_structured_missing_fields(self):
        first = s12_snapshot("瓜子官方检测报告")

        with mock.patch.object(
            runtime_s10_to_s16,
            "_extract_claim_count_with_candidates",
            side_effect=IndexError("no such group"),
        ):
            _recovered, trace = _recover_s12_claim_fields(
                {"s12_claim_field_recovery_hook": lambda _context, snap: {"snapshot": snap}},
                first,
            )

        self.assertTrue(trace["s12_field_missing_after_recovery"])
        self.assertTrue(trace["s12_recovery_candidate_skipped"])
        self.assertTrue(trace["s12_recovery_index_error_prevented"])
        self.assertEqual(trace["s12_missing_fields"], ["claim_count", "max_amount"])

    def test_extent_guard_rejects_empty_short_and_none_extents(self):
        self.assertFalse(_is_valid_extent(()))
        self.assertFalse(_is_valid_extent((1, 2)))
        self.assertFalse(_is_valid_extent(None))
        self.assertFalse(_is_valid_extent((10, 10, 10, 20)))
        self.assertTrue(_is_valid_extent((10, 10, 40, 80)))

    def test_recovery_attempts_skip_malformed_then_continue_with_valid_extent(self):
        first = s12_snapshot("瓜子官方检测报告")
        fresh = s12_snapshot("保险理赔记录 理赔次数 1次 最大金额 2300元")

        def hook(_context, _snapshot):
            return {
                "snapshot": fresh,
                "attempts": [
                    {"attempt_index": 1, "bounds": (1, 2)},
                    {"attempt_index": 2, "bounds": (10, 10, 400, 80)},
                ],
            }

        _recovered, trace = _recover_s12_claim_fields({"s12_claim_field_recovery_hook": hook}, first)

        self.assertEqual(trace["s12_claim_count_extracted"], 1)
        self.assertEqual(trace["s12_claim_recovery_candidate_count"], 2)
        self.assertEqual(trace["s12_claim_recovery_valid_candidate_count"], 1)
        self.assertEqual(trace["s12_claim_recovery_malformed_candidate_count"], 1)
        self.assertEqual(trace["s12_claim_recovery_selected_candidate_extent"], [10, 10, 400, 80])
        self.assertEqual(trace["s12_claim_recovery_stop_code"], "")

    def test_recovery_all_malformed_candidates_returns_precise_stop_code(self):
        first = s12_snapshot("瓜子官方检测报告")

        def hook(_context, _snapshot):
            return {
                "snapshot": first,
                "attempts": [
                    {"attempt_index": 1, "bounds": ()},
                    {"attempt_index": 2, "bounds": (1, 2)},
                    {"attempt_index": 3, "bounds": None},
                ],
            }

        _recovered, trace = _recover_s12_claim_fields({"s12_claim_field_recovery_hook": hook}, first)
        decision = _s12_claim_fields_missing_decision(context_for_reference(2, 8), trace)

        self.assertTrue(trace["s12_field_missing_after_recovery"])
        self.assertEqual(trace["s12_claim_recovery_candidate_count"], 3)
        self.assertEqual(trace["s12_claim_recovery_valid_candidate_count"], 0)
        self.assertEqual(trace["s12_claim_recovery_malformed_candidate_count"], 3)
        self.assertEqual(trace["s12_claim_recovery_stop_code"], S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE)
        self.assertEqual(trace["root_exception_function"], "_recover_s12_claim_fields")
        self.assertEqual(decision["stop_code"], S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE)

    def test_recovery_visible_bounds_extent_tuple_does_not_raise_index_error(self):
        first = s12_snapshot("瓜子官方检测报告")
        client = FakeSwipeClient()

        with mock.patch.object(runtime_s10_to_s16, "_capture_with_global_popup_guard", return_value=first), mock.patch.object(
            runtime_s10_to_s16.time, "sleep", return_value=None
        ):
            _recovered, trace = _recover_s12_claim_fields({"client": client, "recognizer": None}, first)

        self.assertEqual(trace["s12_claim_recovery_valid_candidate_count"], 1)
        self.assertEqual(trace["s12_claim_recovery_selected_candidate_extent"], [10, 10, 400, 80])
        self.assertTrue(client.commands)
        self.assertEqual(client.commands[0]["command"][0:3], ["shell", "input", "swipe"])

    def test_admin_feedback_includes_structured_s12_extent_guard_details(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FeishuTaskStore(Path(temp) / "feishu_tasks", clock=fixed_clock)
            result = {
                "task_id": "FS20260704_0001",
                "status": "NEEDS_REVIEW",
                "issue_code": S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE,
                "current_state": "S12",
                "current_reference_index": 2,
                "current_reference": {
                    "reference_index": 2,
                    "reference_price": 72400,
                    "s12_claim_field_trace": {
                        "s12_claim_field_recovery_attempted": True,
                        "s12_claim_recovery_candidate_count": 3,
                        "s12_claim_recovery_valid_candidate_count": 0,
                        "s12_claim_recovery_malformed_candidate_count": 3,
                        "s12_claim_recovery_skipped_malformed_extents": [(), (1, 2), None],
                        "s12_claim_recovery_stop_code": S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE,
                        "root_exception_function": "_recover_s12_claim_fields",
                        "root_exception_type": "IndexError",
                        "root_exception_message": "tuple index out of range prevented by extent guard",
                        "screenshot_path": "s12.png",
                        "xml_path": "s12.xml",
                    },
                },
            }

            details = store.concrete_failure_details(
                "FS20260704_0001",
                status={"status": "FAILED", "task_id": "FS20260704_0001", "start_ack_sent": True},
                errors=[S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE],
                result=result,
            )
            delivery = store._write_final_failure_feedback(
                "FS20260704_0001",
                status_payload={"status": "FAILED", "task_id": "FS20260704_0001"},
                details=details,
                cancel_reason=S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE,
                result=result,
                dry_run=True,
            )

        self.assertEqual(details["feedback_selected_reason_code"], S12_CLAIM_RECOVERY_FAILED_NO_VALID_CANDIDATE)
        self.assertEqual(details["root_exception_function"], "_recover_s12_claim_fields")
        self.assertIn("理赔次数/最大金额", details["business_reply_text"])
        self.assertIn("root_exception_function=_recover_s12_claim_fields", delivery["admin_reply_text"])
        self.assertIn("s12_claim_recovery_malformed_candidate_count=3", delivery["admin_reply_text"])


if __name__ == "__main__":
    unittest.main()
