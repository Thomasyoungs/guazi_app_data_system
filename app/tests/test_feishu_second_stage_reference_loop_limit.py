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


def fixed_clock():
    return datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)


class FeishuSecondStageReferenceLoopLimitTest(unittest.TestCase):
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

    def continue_result(self, index: int) -> dict:
        return {
            "ok": True,
            "status": CONTINUE_NEXT_REFERENCE,
            "final_status": CONTINUE_NEXT_REFERENCE,
            "current_state": CONTINUE_NEXT_REFERENCE,
            "current_reference_index": index,
            "next_reference_index": index + 1,
            "remaining_reference_count": 10 - index,
            "should_continue_reference_collection": True,
            "continue_reason": "CURRENT_REFERENCE_HAS_NOT_CLOSED_V3_BOUNDARY",
        }

    def test_fs20260626_0015_equivalent_fourth_reference_continues_to_fifth(self):
        task_id = "FS20260626_0015"
        self.write_json(self.task_root / task_id / "first_stage_result.json", {"trisame_cards_count": 10})
        dispatcher = self.make_dispatcher()
        results = [self.continue_result(index) for index in range(1, 5)]

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=results[-1],
            second_stage_results=results,
        )

        self.assertEqual(10, state["real_trisame_cards_count"])
        self.assertEqual(10, state["loop_limit"])
        self.assertFalse(state["fallback_default_4_used"])
        self.assertTrue(state["dispatcher_continue_allowed"])
        self.assertTrue(state["continuation_consumed"])
        self.assertEqual(4, state["previous_reference_index"])
        self.assertEqual(5, state["resumed_reference_index"])
        self.assertEqual("", state["dispatcher_stop_reason"])


if __name__ == "__main__":
    unittest.main()
