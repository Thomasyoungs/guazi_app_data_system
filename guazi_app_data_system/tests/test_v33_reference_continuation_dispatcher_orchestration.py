import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_task_store import FeishuTaskStore  # noqa: E402


V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW = (
    "V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW"
)


def fixed_clock():
    return datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)


VALID_TEMPLATE = """定价
品牌：别克
车系：君越
车型配置：2021款 652T 豪华型
上牌日期：2021-08
表显里程：4.9万公里
颜色：黑
过户次数：0
车况：原版原漆
"""


class V33ReferenceContinuationDispatcherOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FeishuTaskStore(Path(self.temp.name) / "feishu_tasks", clock=fixed_clock)

    def tearDown(self):
        self.temp.cleanup()

    def create_valid_task(self):
        return self.store.create_task_from_message(
            text=VALID_TEMPLATE,
            raw_event={"message_id": "om_v33"},
            raw_message_id="om_v33",
            raw_sender_id="ou_v33",
            raw_chat_id="oc_business",
        )

    def read_task_json(self, task_id: str, filename: str) -> dict:
        return json.loads((self.store.task_dir(task_id) / filename).read_text(encoding="utf-8"))

    def test_confirm_task_persists_start_message_order_trace(self):
        created = self.create_valid_task()

        result = self.store.confirm_task(created.task_id)

        self.assertTrue(result.success)
        self.assertEqual("QUEUED", result.status)
        self.assertIn("【定价已开始】", result.reply_text)
        trace = self.read_task_json(created.task_id, "feishu_start_message_delivery.json")
        self.assertTrue(trace["ok"])
        self.assertTrue(trace["message_order_guard_passed"])
        self.assertEqual(result.reply_text, trace["reply_text"])
        self.assertEqual(f"feishu_start_message:{created.task_id}", trace["confirm_message_idempotent_key"])
        self.assertEqual("returned_to_feishu_gateway_for_send", trace["send_result"])

    def test_duplicate_confirm_reuses_start_message_trace_idempotently(self):
        created = self.create_valid_task()
        first = self.store.confirm_task(created.task_id)
        second = self.store.confirm_task(created.task_id)

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        first_trace = self.read_task_json(created.task_id, "feishu_start_message_delivery.json")
        self.assertEqual(first_trace, second.data["feishu_start_message_trace"])

    def test_low_score_continuation_failure_feedback_does_not_claim_completed_pricing_chain(self):
        created = self.create_valid_task()
        result_payload = {
            "stage": "second_stage",
            "status": "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE",
            "issue_code": "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE",
            "stop_code": "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE",
            "current_state": "S15_BLOCKED_BY_INCOMPLETE_S14",
            "current_reference": {
                "reference_index": 1,
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                "s14_low_score_skip_triggered": True,
                "next_reference_index": 2,
                "return_to_s10_after_low_score_skip": True,
                "returned_list_source_verified": True,
            },
            "dispatcher_reference_loop_state": {
                "continue_reason": "EARLY_EXIT_CONTINUE_NEXT_REFERENCE",
                "next_reference_index": 2,
                "remaining_reference_count": 9,
            },
        }

        details = self.store.concrete_failure_details(
            created.task_id,
            status={"status": "FAILED", "task_id": created.task_id},
            errors=["V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE"],
            result=result_payload,
        )

        self.assertEqual(details["feedback_selected_reason_code"], "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE")
        self.assertFalse(details["feedback_low_score_evidence_ignored_due_to_terminal_issue"])
        self.assertIn("参考车低分跳过后", details["business_reply_text"])
        self.assertIn("继续采集下一辆参考车", details["business_reply_text"])
        self.assertNotIn("已完成参考车采集并形成价格测算", details["business_reply_text"])
        self.assertNotIn("本次定价未开始", details["business_reply_text"])

    def test_duplicate_reference_click_takes_priority_over_low_score_continuation_feedback(self):
        created = self.create_valid_task()
        result_payload = {
            "stage": "second_stage",
            "status": "RESULT_SCHEMA_INVALID_FOR_PRICING",
            "current_state": "S10",
            "issue_code": "DUPLICATE_REFERENCE_CLICK_BLOCKED",
            "current_reference": {
                "reference_index": 3,
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                "s14_low_score_skip_triggered": True,
                "next_reference_index": 4,
                "return_to_s10_after_low_score_skip": True,
                "returned_list_source_verified": True,
            },
            "issue_context": {
                "binding_result": {
                    "stop_code": "DUPLICATE_REFERENCE_CLICK_BLOCKED",
                    "target_reference_index": 4,
                    "processed_reference_indices": [1, 2, 3, 4],
                    "boundary_reference_index": 4,
                }
            },
            "dispatcher_reference_loop_state": {
                "continue_reason": "EARLY_EXIT_CONTINUE_NEXT_REFERENCE",
                "next_reference_index": 4,
                "remaining_reference_count": 6,
            },
        }

        details = self.store.concrete_failure_details(
            created.task_id,
            status={"status": "FAILED", "task_id": created.task_id},
            errors=["RESULT_SCHEMA_INVALID_FOR_PRICING"],
            result=result_payload,
        )

        self.assertEqual(details["canonical_error_code"], "DUPLICATE_REFERENCE_CLICK_BLOCKED")
        self.assertEqual(details["post_start_failure_business_template"], "POST_START_DUPLICATE_REFERENCE_RECOLLECT")
        self.assertIn("参考车回采阶段未能继续执行", details["business_reply_text"])
        self.assertNotIn("参考车低分跳过后，系统未能继续采集下一辆参考车", details["business_reply_text"])

    def test_s10_next_reference_binding_failure_takes_priority_over_low_score_continuation_feedback(self):
        created = self.create_valid_task()
        result_payload = {
            "stage": "second_stage",
            "status": "RESULT_SCHEMA_INVALID_FOR_PRICING",
            "current_state": "S10",
            "current_reference": {
                "reference_index": 8,
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                "s14_low_score_skip_triggered": True,
                "next_reference_index": 9,
                "return_to_s10_after_low_score_skip": True,
                "returned_list_source_verified": True,
            },
            "issue_context": {
                "binding_result": {
                    "stop_code": "S10_NEXT_REFERENCE_ABSOLUTE_CARD_NOT_FOUND",
                    "target_reference_index": 9,
                    "target_canonical_reference_index": 9,
                    "visible_reference_indices": [1, 2, 3, 4, 5, 6, 7],
                    "visible_live_display_orders": [1, 2, 3, 4, 5, 6, 7],
                    "s10_viewport_renumbering_detected": True,
                }
            },
            "dispatcher_reference_loop_state": {
                "continue_reason": "EARLY_EXIT_CONTINUE_NEXT_REFERENCE",
                "next_reference_index": 9,
                "remaining_reference_count": 2,
            },
        }

        details = self.store.concrete_failure_details(
            created.task_id,
            status={"status": "FAILED", "task_id": created.task_id},
            errors=["RESULT_SCHEMA_INVALID_FOR_PRICING"],
            result=result_payload,
        )

        self.assertEqual(details["canonical_error_code"], "S10_NEXT_REFERENCE_ABSOLUTE_CARD_NOT_FOUND")
        self.assertEqual(details["post_start_failure_business_template"], "POST_START_S10_NEXT_REFERENCE_BINDING_FAILED")
        self.assertIn("返回三同车源列表", details["business_reply_text"])
        self.assertIn("唯一绑定目标卡片", details["business_reply_text"])
        self.assertNotIn("低分跳过后", details["business_reply_text"])

    def test_v33_recollected_previous_reference_needs_review_feedback_priority(self):
        created = self.create_valid_task()
        result_payload = {
            "stage": "second_stage",
            "status": "NEEDS_REVIEW",
            "business_status": "NEEDS_REVIEW",
            "current_state": "S15",
            "manual_review_required": True,
            "issue_code": V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
            "stop_code": V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW,
            "current_reference": {
                "reference_index": 3,
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                "s14_low_score_skip_triggered": True,
                "next_reference_index": 4,
                "return_to_s10_after_low_score_skip": True,
                "returned_list_source_verified": True,
            },
            "v33_recollect_terminal_trace": {
                "v33_recollect_terminal_reference_index": 3,
                "v33_recollect_terminal_boundary_reference_index": 4,
                "v33_final_reference_candidate_index": 3,
                "v33_recollect_terminal_decision": "NEEDS_REVIEW",
                "v33_recollect_blocked_low_score_continue": True,
                "v33_recollect_prevented_next_boundary_reclick": True,
                "boundary_reference_score": 93,
                "target_score": 92,
            },
        }

        details = self.store.concrete_failure_details(
            created.task_id,
            status={"status": "NEEDS_REVIEW", "task_id": created.task_id},
            errors=[V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW],
            result=result_payload,
        )

        self.assertEqual(details["canonical_error_code"], V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW)
        self.assertEqual(details["post_start_failure_business_template"], "V33_RECOLLECTED_PREVIOUS_REFERENCE_NEEDS_REVIEW")
        self.assertIn("需要人工复核定价", details["business_reply_text"])
        self.assertIn("边界前参考车回采后仍不完整", details["business_reply_text"])
        self.assertNotIn("参考车低分跳过后，系统未能继续采集下一辆参考车", details["business_reply_text"])


if __name__ == "__main__":
    unittest.main()
