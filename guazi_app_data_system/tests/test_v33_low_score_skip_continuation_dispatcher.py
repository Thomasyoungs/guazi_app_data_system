import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_pricing_dispatcher import CONTINUE_NEXT_REFERENCE, FeishuPricingDispatcher  # noqa: E402
from pricing_result_collector import (  # noqa: E402
    normalize_v33_low_score_continuation_fields,
    pricing_result_business_status,
    pricing_success_missing_required_fields,
)


def fixed_clock():
    return datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)


def fs20260628_0001_like_low_score_payload() -> dict:
    return {
        "ok": False,
        "status": "S15_BLOCKED_BY_INCOMPLETE_S14",
        "final_status": "S15_BLOCKED_BY_INCOMPLETE_S14",
        "current_state": "S15_BLOCKED_BY_INCOMPLETE_S14",
        "stop_code": "S15_BLOCKED_BY_INCOMPLETE_S14",
        "target_score": {"score": 95.0},
        "current_reference_index": 1,
        "next_reference_index": 1,
        "current_reference": {
            "reference_index": 1,
            "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
            "reference_score_upper_bound": 83.0,
            "max_possible_reference_score": 83.0,
            "target_score": 95.0,
            "s14_low_score_skip_triggered": True,
            "return_to_s10_after_low_score_skip": True,
            "returned_list_source_verified": True,
            "s15_entry_block_reason": "INCOMPLETE_S14",
            "early_exit_decision": {
                "early_exit_decision": "LOW_SCORE_SKIP_AND_CONTINUE_NEXT_REFERENCE",
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                "s14_low_score_skip_triggered": True,
                "return_to_s10_after_low_score_skip": True,
                "returned_list_source_verified": True,
                "current_reference_index": 1,
                "next_reference_index": 2,
                "remaining_reference_count": 9,
                "reference_score_upper_bound": 83.0,
                "target_score": 95.0,
            },
        },
    }


class V33LowScoreSkipContinuationDispatcherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "data"
        self.task_root = self.data_dir / "feishu_tasks"
        self.runtime_lock = self.root / "runtime" / "pricing.lock"
        self.first_stage_script = self.root / "scripts" / "runtime_s01_to_s10_mainline.py"
        self.second_stage_script = self.root / "scripts" / "runtime_s10_to_s16_mainline.py"
        self.first_stage_result = self.root / "output" / "result_s01_to_s10.json"
        self.second_stage_result = self.root / "output" / "result_s10_to_s16.json"
        self.first_stage_script.parent.mkdir(parents=True, exist_ok=True)
        self.first_stage_script.write_text("# fake first stage\n", encoding="utf-8")
        self.second_stage_script.write_text("# fake second stage\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def make_dispatcher(self) -> FeishuPricingDispatcher:
        return FeishuPricingDispatcher(
            task_root=self.task_root,
            data_dir=self.data_dir,
            runtime_lock_path=self.runtime_lock,
            clock=fixed_clock,
            first_stage_script=self.first_stage_script,
            first_stage_result_path=self.first_stage_result,
            second_stage_script=self.second_stage_script,
            second_stage_result_path=self.second_stage_result,
        )

    def test_collector_promotes_nested_low_score_skip_to_top_level_continuation(self):
        payload = fs20260628_0001_like_low_score_payload()

        changed = normalize_v33_low_score_continuation_fields(payload)

        self.assertTrue(changed)
        self.assertEqual(CONTINUE_NEXT_REFERENCE, payload["status"])
        self.assertEqual(CONTINUE_NEXT_REFERENCE, payload["final_status"])
        self.assertEqual(CONTINUE_NEXT_REFERENCE, payload["current_state"])
        self.assertEqual(1, payload["current_reference_index"])
        self.assertEqual(2, payload["next_reference_index"])
        self.assertEqual(9, payload["remaining_reference_count"])
        self.assertTrue(payload["dispatcher_should_continue"])
        self.assertEqual("EARLY_EXIT_CONTINUE_NEXT_REFERENCE", payload["continue_reason"])
        self.assertEqual("low_score_skip", payload["continuation_source"])
        self.assertFalse(payload["terminal"])
        self.assertFalse(payload["failed"])
        self.assertEqual(CONTINUE_NEXT_REFERENCE, pricing_result_business_status(payload))
        self.assertEqual([], pricing_success_missing_required_fields(payload))

    def test_dispatcher_consumes_nested_low_score_skip_even_when_top_level_was_s15_blocked(self):
        task_id = "FS20260628_0001"
        task_dir = self.task_root / task_id
        self.write_json(task_dir / "first_stage_result.json", {"trisame_cards_count": 10})
        dispatcher = self.make_dispatcher()
        payload = fs20260628_0001_like_low_score_payload()

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=payload,
            second_stage_results=[payload],
        )

        self.assertTrue(state["dispatcher_continue_allowed"])
        self.assertTrue(state["continuation_consumed"])
        self.assertEqual(1, state["previous_reference_index"])
        self.assertEqual(2, state["resumed_reference_index"])
        self.assertEqual(2, state["next_reference_index"])
        self.assertEqual("low_score_skip", state["continuation_source"])
        self.assertEqual("EARLY_EXIT_CONTINUE_NEXT_REFERENCE", state["dispatcher_continue_reason"])
        self.assertEqual(10, state["loop_limit"])
        self.assertFalse(state["fallback_default_4_used"])


if __name__ == "__main__":
    unittest.main()
