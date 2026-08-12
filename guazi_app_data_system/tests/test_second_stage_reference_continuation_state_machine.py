import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_pricing_dispatcher import (  # noqa: E402
    ALL_TRISAME_REFERENCES_EXHAUSTED_NEEDS_REVIEW,
    CONTINUE_NEXT_REFERENCE,
    FeishuPricingDispatcher,
    SECOND_STAGE_CONTINUATION_STATE_MISSING,
    SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED,
)
from feishu_task_store import FeishuTaskStore  # noqa: E402
from pricing_runner import PricingRunner  # noqa: E402


def fixed_clock():
    return datetime(2026, 6, 26, 10, 0, tzinfo=timezone.utc)


def draft_payload(task_id):
    return {
        "task_id": task_id,
        "source": "feishu",
        "status": "QUEUED",
        "brand": "别克",
        "series": "君越",
        "model_config": "2021款 652T 豪华型",
        "license_date": "2021-08",
        "mileage_text": "4.9万公里",
        "color": "黑",
        "transfer_count_text": "0",
        "condition_text": "原版原漆",
        "target_fingerprint": "unit-target",
    }


class SecondStageReferenceContinuationStateMachineTest(unittest.TestCase):
    def setUp(self):
        self.adb_env_patch = patch.dict("os.environ", {"GUAZI_ADB_SERIAL": "UNITTEST_TARGET_SERIAL"}, clear=False)
        self.adb_env_patch.start()
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
        self.adb_env_patch.stop()

    def make_dispatcher(self):
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

    def make_runner(self):
        return PricingRunner(
            task_root=self.task_root,
            data_dir=self.data_dir,
            runtime_lock_path=self.runtime_lock,
            clock=fixed_clock,
        )

    def create_queued_task(self, task_id="FS20260626_0015"):
        task_dir = self.task_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "QUEUED",
                "confirmed_at": "2026-06-26T10:00:00+00:00",
                "queued_at": "2026-06-26T10:00:00+00:00",
                "raw_chat_id": "oc_business",
            },
        )
        self.write_json(task_dir / "target_task_draft.json", draft_payload(task_id))
        return task_id

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def write_task_first_stage(self, task_id, payload):
        self.write_json(self.task_root / task_id / "first_stage_result.json", payload)

    def continue_result(self, current_index, *, total=10):
        return {
            "ok": True,
            "status": CONTINUE_NEXT_REFERENCE,
            "final_status": CONTINUE_NEXT_REFERENCE,
            "current_state": CONTINUE_NEXT_REFERENCE,
            "current_reference_index": current_index,
            "next_reference_index": current_index + 1,
            "remaining_reference_count": max(0, total - current_index),
            "should_continue_reference_collection": True,
            "continue_reason": "CURRENT_REFERENCE_HAS_NOT_CLOSED_V3_BOUNDARY",
            "target_score": {"score": 95.0},
            "reference_history": [
                {
                    "reference_index": idx,
                    "reference_score": score,
                    "reference_score_trustworthy": True,
                    "reference_score_usable_for_boundary": True,
                }
                for idx, score in enumerate([79.0, 70.0, 79.0, 80.0][:current_index], start=1)
            ],
        }

    def success_result(self):
        return {
            "status": "SUCCEEDED",
            "final_status": "SUCCEEDED",
            "current_state": "SUCCEEDED",
            "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
            "target_score": 95.0,
            "boundary_confirmed": True,
            "boundary_reference_index": 5,
            "boundary_reference_score": 96.0,
            "final_reference_index": 4,
            "final_reference_score": 80.0,
            "final_reference_price_yuan": 85100,
            "manual_review_required": False,
            "target_guazi_listing_price_yuan": 96400,
            "guazi_service_fee_yuan": 1500,
            "guazi_net_payout_yuan": 94900,
            "guazi_return_price_yuan": 94900,
            "cost_yuan": 1000,
            "profit_rate": 0.08,
            "profit_yuan": 7592,
            "suggested_purchase_price_yuan": 86308,
            "final_purchase_price_yuan": 86308,
        }

    def full_chain_priced_done_result(self):
        return {
            "task_id": "FS20260626_0015",
            "produced_by_task_id": "FS20260626_0015",
            "target_fingerprint": "unit-target",
            "task_target_fingerprint": "unit-target",
            "status": "FULL_CHAIN_PRICED_DONE",
            "final_status": "FULL_CHAIN_PRICED_DONE",
            "current_state": "FULL_CHAIN_PRICED_DONE",
            "s16_status": "S16_READY",
            "pricing_decision_source": "AUTOMATIC_PRICING",
            "manual_review_required": False,
            "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
            "target_score": {"score": 92},
            "selected_reference": {
                "reference_index": 3,
                "list_price_10k": 16.58,
                "score": 89,
            },
            "selected_reference_score": {"score": 89},
            "boundary_confirmed": True,
            "boundary_reference_index": 4,
            "boundary_reference_score": 93,
            "pre_boundary_reference_index": 3,
            "final_reference_candidate_index": 3,
            "final_reference_candidate_status": "COMPLETE_TRUSTWORTHY_LOW_SCORE",
            "final_reference_recollect_done": True,
            "pricing": {
                "status": "priced",
                "target_guazi_listing_price_yuan": 158300,
                "guazi_service_fee_yuan": 4000,
                "guazi_net_payout_yuan": 154300,
                "guazi_return_price_yuan": 154300,
                "cost_yuan": 800,
                "profit_rate": 0.08,
                "profit_yuan": 13344,
                "suggested_purchase_price_yuan": 140156,
                "final_purchase_price_yuan": 140156,
                "manual_review_required": False,
            },
            "s17_payload": {
                "task_status": "priced",
                "suggested_acquisition_price_yuan": 140156,
                "final_reference_index": 3,
                "reference_score": 89,
                "target_score": 92,
                "manual_review_required": False,
            },
        }

    def test_loop_limit_reads_task_first_stage_result_json_trisame_count(self):
        task_id = self.create_queued_task()
        dispatcher = self.make_dispatcher()
        self.write_task_first_stage(task_id, {"trisame_cards_count": 10, "same_source_cards": [{"id": i} for i in range(10)]})

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=self.continue_result(4),
            second_stage_results=[self.continue_result(i) for i in range(1, 5)],
        )

        self.assertEqual(state["real_trisame_cards_count"], 10)
        self.assertEqual(state["loop_limit"], 10)
        self.assertEqual(state["loop_limit_source"], "task_first_stage_result_json_trisame_cards_count")
        self.assertFalse(state["fallback_default_4_used"])

    def test_full_chain_priced_done_has_priority_over_later_failure(self):
        task_id = self.create_queued_task()
        dispatcher = self.make_dispatcher()
        self.write_task_first_stage(task_id, {"trisame_cards_count": 9})
        terminal_success = self.full_chain_priced_done_result()
        later_failure = {
            "ok": False,
            "status": "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY",
            "errors": ["MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY"],
        }

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=later_failure,
            second_stage_results=[self.continue_result(1, total=9), terminal_success, later_failure],
        )

        self.assertFalse(state["dispatcher_continue_allowed"])
        self.assertEqual(state["dispatcher_stop_reason"], "FULL_CHAIN_PRICED_DONE_TERMINAL_SUCCESS")
        self.assertTrue(state["terminal_success_result_exists"])
        self.assertTrue(state["terminal_success_result_protected"])
        self.assertEqual(state["reference_loop_terminal_priority"], "terminal_success")

    def test_cross_task_terminal_success_is_rejected_before_priority(self):
        task_id = self.create_queued_task()
        dispatcher = self.make_dispatcher()
        self.write_task_first_stage(task_id, {"trisame_cards_count": 9, "target_fingerprint": "unit-target"})
        terminal_success = self.full_chain_priced_done_result()
        terminal_success.update(
            {
                "task_id": "FS20260702_0003",
                "produced_by_task_id": "FS20260702_0003",
                "target_fingerprint": "other-target",
                "task_target_fingerprint": "other-target",
            }
        )
        later_failure = {
            "ok": False,
            "status": "S13_ALL_ZERO_LOOP",
            "errors": ["S13_ALL_ZERO_LOOP"],
        }

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=later_failure,
            second_stage_results=[terminal_success, later_failure],
        )

        self.assertFalse(state.get("terminal_success_result_exists", False))
        self.assertEqual(state["dispatcher_stop_reason"], "SECOND_STAGE_TERMINAL_OR_FAILED_STATUS")
        rejection_files = list((self.task_root / task_id / "result_scope_rejections").glob("*.json"))
        self.assertTrue(rejection_files)
        self.assertIn("CROSS_TASK_PRICING_RESULT_REJECTED", rejection_files[0].read_text(encoding="utf-8"))

    def test_pre_run_backup_terminal_success_has_priority_over_current_failure(self):
        task_id = self.create_queued_task()
        dispatcher = self.make_dispatcher()
        self.write_task_first_stage(task_id, {"trisame_cards_count": 9})
        terminal_success = self.full_chain_priced_done_result()
        terminal_success["current_reference"] = {
            "reference_index": 3,
            "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
            "target_score": 92,
            "reference_score_upper_bound": 91,
            "s14_low_score_skip_triggered": True,
            "return_to_s10_after_low_score_skip": True,
            "returned_list_source_verified": True,
            "remaining_reference_count": 2,
            "early_exit_decision": {
                "current_reference_index": 3,
                "next_reference_index": 4,
                "target_score": 92,
                "reference_score_upper_bound": 91,
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                "s14_low_score_skip_triggered": True,
                "early_exit_decision": "LOW_SCORE_SKIP_AND_CONTINUE_NEXT_REFERENCE",
                "return_to_s10_after_low_score_skip": True,
                "returned_list_source_verified": True,
                "remaining_reference_count": 2,
            },
        }
        backup_path = self.task_root / task_id / "pre_run_result_backups" / "20260702T063453.output__result_s10_to_s16.json"
        self.write_json(backup_path, terminal_success)
        later_failure = {
            "ok": False,
            "status": "S13_ALL_ZERO_LOOP",
            "errors": ["S13_ALL_ZERO_LOOP"],
        }

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=later_failure,
            second_stage_results=[later_failure],
        )

        self.assertFalse(state["dispatcher_continue_allowed"])
        self.assertEqual(state["dispatcher_stop_reason"], "TERMINAL_SUCCESS_RECOVERED_FROM_BACKUP")
        self.assertTrue(state["terminal_success_result_exists"])
        self.assertTrue(state["terminal_success_result_protected"])
        self.assertTrue(state["terminal_success_recovered_from_backup"])
        self.assertEqual(state["terminal_success_recovery_reason"], "PRE_RUN_ISOLATED_SUCCESS_RESULT")
        self.assertIn("pre_run_result_backups", state["terminal_success_source"])
        self.assertEqual(state["reference_loop_terminal_priority"], "terminal_success")

    def test_persist_terminal_success_ignores_later_failure(self):
        task_id = self.create_queued_task()
        runner = self.make_runner()
        terminal_success = self.full_chain_priced_done_result()
        later_failure = {
            "ok": False,
            "status": "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY",
            "errors": ["MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY"],
        }

        result = runner.persist_terminal_success_result(
            task_id,
            terminal_success,
            source="unit_test_full_chain_priced_done",
            ignored_failure=later_failure,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertTrue(result["failure_after_terminal_success_ignored"])
        status = self.read_json(self.task_root / task_id / "status.json")
        pricing = self.read_json(self.task_root / task_id / "pricing_result.json")
        self.assertEqual(status["status"], "SUCCEEDED")
        self.assertEqual(status["business_status"], "SUCCEEDED")
        self.assertTrue(status["terminal_success_result_protected"])
        self.assertEqual(pricing["pricing"]["final_purchase_price_yuan"], 140156)
        self.assertNotEqual(status.get("canonical_error_code"), "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY")

    def test_loop_limit_reads_same_source_cards_length(self):
        task_id = self.create_queued_task()
        dispatcher = self.make_dispatcher()
        self.write_task_first_stage(task_id, {"same_source_cards": [{"id": i} for i in range(10)]})

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=self.continue_result(4),
            second_stage_results=[self.continue_result(i) for i in range(1, 5)],
        )

        self.assertEqual(state["real_trisame_cards_count"], 10)
        self.assertEqual(state["loop_limit"], 10)
        self.assertEqual(state["loop_limit_source"], "task_first_stage_result_json_len(same_source_cards)")

    def test_loop_limit_derives_from_continuation_state(self):
        task_id = self.create_queued_task()
        dispatcher = self.make_dispatcher()

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=self.continue_result(4),
            second_stage_results=[self.continue_result(i) for i in range(1, 5)],
        )

        self.assertEqual(state["real_trisame_cards_count"], 10)
        self.assertEqual(state["loop_limit"], 10)
        self.assertEqual(
            state["loop_limit_source"],
            "last_second_stage_result_derived_total_from_next_reference_index_and_remaining_reference_count",
        )

    def test_fallback_4_only_when_no_real_count_and_no_continuation(self):
        task_id = self.create_queued_task()
        dispatcher = self.make_dispatcher()

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result={"ok": True, "status": CONTINUE_NEXT_REFERENCE},
            second_stage_results=[{"ok": True, "status": CONTINUE_NEXT_REFERENCE}],
        )

        self.assertEqual(state["loop_limit"], 4)
        self.assertTrue(state["fallback_default_4_used"])
        self.assertEqual(state["dispatcher_stop_reason"], SECOND_STAGE_CONTINUATION_STATE_MISSING)

    def test_continue_next_reference_after_four_when_remaining_exists(self):
        task_id = self.create_queued_task()
        dispatcher = self.make_dispatcher()
        self.write_task_first_stage(task_id, {"trisame_cards_count": 10})

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=self.continue_result(4),
            second_stage_results=[self.continue_result(i) for i in range(1, 5)],
        )

        self.assertTrue(state["dispatcher_continue_allowed"])
        self.assertEqual(state["dispatcher_continue_reason"], "CONTINUE_NEXT_REFERENCE_WITH_REMAINING_REFERENCES")
        self.assertEqual(state["next_reference_index"], 5)
        self.assertEqual(state["remaining_reference_count"], 6)

    def test_0015_equivalent_continues_to_reference_five(self):
        task_id = self.create_queued_task()
        attempts = []

        def fake_run(*args, **kwargs):
            script = str(args[0][1])
            if "runtime_s01_to_s10" in script:
                payload = {
                    "flow_state": {"S10_READY": True},
                    "status": "S10_READY",
                    "trisame_cards_count": 10,
                    "same_source_cards": [{"id": i} for i in range(1, 11)],
                }
                self.write_json(self.first_stage_result, payload)
                return SimpleNamespace(returncode=0, stdout="first", stderr="")
            attempts.append(len(attempts) + 1)
            if len(attempts) <= 4:
                self.write_json(self.second_stage_result, self.continue_result(len(attempts), total=10))
            else:
                self.write_json(self.second_stage_result, self.success_result())
            return SimpleNamespace(returncode=0, stdout="second", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(len(attempts), 5)
        self.assertEqual(len(result["second_stage_results"]), 5)
        fourth_state = result["dispatcher_reference_loop_states"][3]
        self.assertTrue(fourth_state["dispatcher_continue_allowed"])
        self.assertEqual(fourth_state["next_reference_index"], 5)
        self.assertEqual(result["status"], "SUCCEEDED")

    def test_continue_next_reference_with_remaining_is_not_failure(self):
        task_id = self.create_queued_task()
        dispatcher = self.make_dispatcher()
        self.write_task_first_stage(task_id, {"trisame_cards_count": 10})

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=self.continue_result(4),
            second_stage_results=[self.continue_result(i) for i in range(1, 5)],
        )

        self.assertTrue(state["dispatcher_continue_allowed"])
        self.assertNotEqual(state["dispatcher_stop_reason"], "SECOND_STAGE_COLLECTION_INCOMPLETE")

    def test_second_stage_incomplete_only_when_real_count_exhausted(self):
        task_id = self.create_queued_task()
        dispatcher = self.make_dispatcher()
        self.write_task_first_stage(task_id, {"trisame_cards_count": 10})
        exhausted = self.continue_result(10, total=10)
        exhausted["next_reference_index"] = 11
        exhausted["remaining_reference_count"] = 0

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=exhausted,
            second_stage_results=[self.continue_result(i, total=10) for i in range(1, 10)] + [exhausted],
        )

        self.assertFalse(state["dispatcher_continue_allowed"])
        self.assertEqual(state["dispatcher_stop_reason"], ALL_TRISAME_REFERENCES_EXHAUSTED_NEEDS_REVIEW)

    def test_absolute_safety_limit_blocks_infinite_loop(self):
        task_id = self.create_queued_task()
        dispatcher = self.make_dispatcher()
        self.write_task_first_stage(task_id, {"trisame_cards_count": 30})

        state = dispatcher._resolve_second_stage_reference_loop_state(
            task_id,
            {"ok": True, "status": "S10_READY"},
            last_second_stage_result=self.continue_result(20, total=30),
            second_stage_results=[self.continue_result(i, total=30) for i in range(1, 21)],
        )

        self.assertFalse(state["dispatcher_continue_allowed"])
        self.assertEqual(state["dispatcher_stop_reason"], SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED)
        self.assertEqual(state["loop_limit"], 20)

    def test_started_collection_feedback_never_says_not_started(self):
        task_id = self.create_queued_task()
        store = FeishuTaskStore(self.task_root, clock=fixed_clock)
        status = {
            "task_id": task_id,
            "status": "S10_READY",
            "raw_chat_id": "oc_business",
        }
        result = {
            "status": SECOND_STAGE_CONTINUATION_STATE_MISSING,
            "errors": [SECOND_STAGE_CONTINUATION_STATE_MISSING],
            "dispatcher_reference_loop_state": {
                "second_stage_results_count": 4,
                "next_reference_index": 5,
                "remaining_reference_count": 6,
            },
            "step_result": {"status": CONTINUE_NEXT_REFERENCE},
        }

        details = store.concrete_failure_details(
            task_id,
            status=status,
            errors=[SECOND_STAGE_CONTINUATION_STATE_MISSING],
            result=result,
        )

        self.assertNotIn("未开始", details["business_reply_text"])
        self.assertNotIn("没有开始", details["business_reply_text"])

    def test_business_feedback_hides_internal_terms(self):
        task_id = self.create_queued_task()
        store = FeishuTaskStore(self.task_root, clock=fixed_clock)
        details = store.concrete_failure_details(
            task_id,
            status={"task_id": task_id, "status": "S10_READY"},
            errors=[SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED],
            result={
                "status": SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED,
                "errors": [SECOND_STAGE_REFERENCE_LOOP_SAFETY_LIMIT_REACHED],
                "dispatcher_reference_loop_state": {"second_stage_results_count": 20},
            },
        )

        for term in [
            "dispatcher",
            "runner",
            "XML",
            "trace",
            "status.json",
            "loop limit",
            "CONTINUE_NEXT_REFERENCE",
            "trisame_cards_count",
            "adb",
            "uiautomator",
            "Python traceback",
            "fallback",
            "wrapper",
        ]:
            self.assertNotIn(term, details["business_reply_text"])

    def test_existing_success_path_not_regressed(self):
        task_id = self.create_queued_task("FS20260626_0099")

        def fake_run(*args, **kwargs):
            script = str(args[0][1])
            if "runtime_s01_to_s10" in script:
                self.write_json(
                    self.first_stage_result,
                    {"flow_state": {"S10_READY": True}, "trisame_cards_count": 2},
                )
                return SimpleNamespace(returncode=0, stdout="first", stderr="")
            self.write_json(self.second_stage_result, self.success_result())
            return SimpleNamespace(returncode=0, stdout="second", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_task_id"], task_id)
        self.assertEqual(result["status"], "SUCCEEDED")

    def test_existing_needs_review_path_not_regressed(self):
        task_id = self.create_queued_task("FS20260626_0100")
        supervisor_calls = []

        def supervisor_sync(sync_task_id, **kwargs):
            supervisor_calls.append((sync_task_id, kwargs))
            return {"ok": True, "task_id": sync_task_id, "status": "WAITING_MANUAL_PRICE"}

        def fake_run(*args, **kwargs):
            script = str(args[0][1])
            if "runtime_s01_to_s10" in script:
                self.write_json(
                    self.first_stage_result,
                    {"flow_state": {"S10_READY": True}, "trisame_cards_count": 2},
                )
                return SimpleNamespace(returncode=0, stdout="first", stderr="")
            self.write_json(
                self.second_stage_result,
                {
                    "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
                    "final_status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
                    "manual_review_required": True,
                    "pricing": {"suggested_purchase_price_yuan": 86000},
                },
            )
            return SimpleNamespace(returncode=0, stdout="second", stderr="")

        dispatcher = FeishuPricingDispatcher(
            task_root=self.task_root,
            data_dir=self.data_dir,
            runtime_lock_path=self.runtime_lock,
            clock=fixed_clock,
            supervisor_sync=supervisor_sync,
            first_stage_script=self.first_stage_script,
            first_stage_result_path=self.first_stage_result,
            second_stage_script=self.second_stage_script,
            second_stage_result_path=self.second_stage_result,
        )
        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = dispatcher.dispatch_once(dry_run=False, allow_app_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "WAITING_MANUAL_PRICE")
        self.assertEqual(supervisor_calls[0][0], task_id)


if __name__ == "__main__":
    unittest.main()
