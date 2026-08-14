import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_pricing_dispatcher import (  # noqa: E402
    CONTINUE_NEXT_REFERENCE,
    FeishuPricingDispatcher,
    REFERENCE_LOOP_STATE_RESET_DETECTED,
)
from pricing_runner import PricingRunner  # noqa: E402
from runtime_s10_to_s16_mainline import (  # noqa: E402
    SECOND_STAGE_CONTINUATION_SOURCE_LOST_AFTER_PRE_RUN_ISOLATION,
    _capture_with_global_popup_guard,
    _load_reference_continuation_plan,
)


def fixed_clock():
    return datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def task_payload(task_id: str = "FS20260702_0002") -> dict:
    return {
        "task_id": task_id,
        "brand": "别克",
        "series": "君越",
        "model_year": "2021款",
        "trim": "652T 豪华型",
        "color": "黑",
        "registration_date_raw": "21.8",
    }


def first_stage_evidence() -> dict:
    return {
        "first_stage_result_digest": "first-digest-current",
        "s10_canonical_order_digest": "order-digest-current",
        "canonical_reference_order": [
            {"reference_index": 1, "list_title": "ref-1", "list_price_10k": 7.3, "raw_metadata": "m1"},
            {"reference_index": 2, "list_title": "ref-2", "list_price_10k": 8.1, "raw_metadata": "m2"},
            {"reference_index": 3, "list_title": "ref-3", "list_price_10k": 8.8, "raw_metadata": "m3"},
        ],
    }


def continuation_payload(task_id: str = "FS20260702_0002", *, current_index: int = 1, next_index: int = 2) -> dict:
    return {
        "task_id": task_id,
        "produced_by_task_id": task_id,
        "status": CONTINUE_NEXT_REFERENCE,
        "final_status": CONTINUE_NEXT_REFERENCE,
        "target_fingerprint": "别克|君越|2021款|652T 豪华型|黑|21.8",
        "first_stage_result_digest": "first-digest-current",
        "s10_canonical_order_digest": "order-digest-current",
        "current_reference_index": current_index,
        "next_reference_index": next_index,
        "remaining_reference_count": 2,
        "should_continue_reference_collection": True,
        "continue_reason": "CURRENT_REFERENCE_HAS_NOT_CLOSED_V3_BOUNDARY",
        "current_reference": {
            "reference_index": current_index,
            "list_title": f"ref-{current_index}",
            "list_price_10k": 7.3,
            "raw_metadata": "m1",
            "reference_score": 80,
        },
        "reference_history": [
            {
                "reference_index": current_index,
                "list_title": f"ref-{current_index}",
                "list_price_10k": 7.3,
                "raw_metadata": "m1",
                "reference_score": 80,
            }
        ],
    }


class SecondStageContinuationBackupSourceAndPopupGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task_id = "FS20260702_0002"

    def tearDown(self):
        self.temp.cleanup()

    def project_path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def test_load_continuation_plan_recovers_from_pre_run_backup(self):
        backup = (
            self.root
            / "data"
            / "feishu_tasks"
            / self.task_id
            / "pre_run_result_backups"
            / "20260702T063453.output__result_s10_to_s16.json"
        )
        write_json(backup, continuation_payload(self.task_id, current_index=1, next_index=2))

        with patch("runtime_s10_to_s16_mainline.project_path", side_effect=self.project_path):
            plan = _load_reference_continuation_plan(
                task_payload(self.task_id),
                first_stage_evidence=first_stage_evidence(),
            )

        self.assertTrue(plan["continuation_mode"])
        self.assertEqual(plan["next_reference_index"], 2)
        self.assertTrue(plan["continuation_source_recovered_from_backup"])
        self.assertIn("pre_run_result_backups", plan["source_path"])

    def test_load_continuation_plan_recovers_from_dispatcher_loop_state(self):
        state_path = self.root / "data" / "feishu_tasks" / self.task_id / "dispatcher_reference_loop_state.json"
        write_json(
            state_path,
            {
                "task_id": self.task_id,
                "dispatcher_continue_allowed": True,
                "current_reference_index": 1,
                "next_reference_index": 2,
                "remaining_reference_count": 2,
                "attempted_reference_indices": [1],
                "dispatcher_continue_reason": "CONTINUE_NEXT_REFERENCE_WITH_REMAINING_REFERENCES",
            },
        )

        with patch("runtime_s10_to_s16_mainline.project_path", side_effect=self.project_path):
            plan = _load_reference_continuation_plan(
                task_payload(self.task_id),
                first_stage_evidence=first_stage_evidence(),
            )

        self.assertTrue(plan["continuation_mode"])
        self.assertEqual(plan["next_reference_index"], 2)
        self.assertIn("dispatcher_reference_loop_state.json", plan["source_path"])
        self.assertTrue(plan["continuation_expected_from_dispatcher_state"])

    def test_missing_continuation_backup_blocks_default_to_reference_one(self):
        isolation = (
            self.root
            / "data"
            / "feishu_tasks"
            / self.task_id
            / "second_stage_pre_run_result_isolation.json"
        )
        write_json(
            isolation,
            {
                "task_id": self.task_id,
                "stage": "second_stage",
                "continuation_backup_source_available": True,
                "continuation_backup_paths": [str(isolation.parent / "pre_run_result_backups" / "missing.json")],
            },
        )

        with patch("runtime_s10_to_s16_mainline.project_path", side_effect=self.project_path):
            plan = _load_reference_continuation_plan(
                task_payload(self.task_id),
                first_stage_evidence=first_stage_evidence(),
            )

        self.assertFalse(plan["continuation_mode"])
        self.assertTrue(plan["continuation_source_missing_blocked_default_to_one"])
        self.assertEqual(
            plan["continuation_source_missing_stop_code"],
            SECOND_STAGE_CONTINUATION_SOURCE_LOST_AFTER_PRE_RUN_ISOLATION,
        )

    def test_pricing_runner_marks_same_task_continuation_backup_source(self):
        data_dir = self.root / "data"
        task_dir = data_dir / "feishu_tasks" / self.task_id
        result_path = self.root / "output" / "result_s10_to_s16.json"
        write_json(result_path, continuation_payload(self.task_id, current_index=1, next_index=2))
        runner = PricingRunner(
            task_root=data_dir / "feishu_tasks",
            data_dir=data_dir,
            runtime_lock_path=self.root / "runtime" / "pricing.lock",
            clock=fixed_clock,
        )
        runner.project_root = self.root

        backups = runner._backup_pre_run_results(
            self.task_id,
            task_dir,
            result_path,
            default_paths=[],
        )

        self.assertTrue(backups)
        manifest = json.loads((task_dir / "pre_run_result_backups" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["continuation_backup_paths"], backups)
        self.assertTrue(manifest["continuation_backup_manifest"][0]["continuation_backup_for_same_task"])

    def test_global_popup_guard_runs_after_runtime_capture(self):
        context = {"client": object(), "task_id": self.task_id, "current_reference": {}}
        snapshot = {"visible_texts": ["开启消息推送通知"], "nodes": []}

        def guard(_context, captured, *, current_stage, call_site=""):
            guarded = dict(captured)
            guarded["guard_called_for_stage"] = current_stage
            guarded["guard_called_for_site"] = call_site
            return guarded

        with patch("runtime_s10_to_s16_mainline._capture", return_value=snapshot) as capture, patch(
            "runtime_s10_to_s16_mainline._maybe_close_guazi_push_popup_and_resume",
            side_effect=guard,
        ) as maybe_guard:
            result = _capture_with_global_popup_guard(context, "s11_wait", current_stage="S11_REPORT_SEARCH")

        capture.assert_called_once()
        maybe_guard.assert_called_once()
        self.assertEqual(result["guard_called_for_stage"], "S11_REPORT_SEARCH")
        self.assertTrue(result["global_transient_popup_guard_enabled"])

    def test_dispatcher_detects_runtime_reset_to_reference_one(self):
        data_dir = self.root / "data"
        task_root = data_dir / "feishu_tasks"
        task_dir = task_root / self.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        write_json(
            task_dir / "dispatcher_reference_loop_state.json",
            {
                "task_id": self.task_id,
                "dispatcher_continue_allowed": True,
                "current_reference_index": 1,
                "next_reference_index": 2,
                "attempted_reference_indices": [1],
            },
        )
        dispatcher = FeishuPricingDispatcher(
            task_root=task_root,
            data_dir=data_dir,
            runtime_lock_path=self.root / "runtime" / "pricing.lock",
            clock=fixed_clock,
            first_stage_script=self.root / "scripts" / "runtime_s01_to_s10_mainline.py",
            first_stage_result_path=self.root / "output" / "result_s01_to_s10.json",
            second_stage_script=self.root / "scripts" / "runtime_s10_to_s16_mainline.py",
            second_stage_result_path=self.root / "output" / "result_s10_to_s16.json",
        )

        state = dispatcher._resolve_second_stage_reference_loop_state(
            self.task_id,
            {"ok": True, "status": "S10_READY", "trisame_cards_count": 3},
            last_second_stage_result={
                "ok": False,
                "status": "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
                "current_reference_index": 1,
                "continuation_plan": {"source_path": "", "next_reference_index": 1},
            },
            second_stage_results=[],
        )

        self.assertTrue(state["reference_loop_state_reset_detected"])
        self.assertEqual(state["dispatcher_stop_reason"], REFERENCE_LOOP_STATE_RESET_DETECTED)


if __name__ == "__main__":
    unittest.main()
