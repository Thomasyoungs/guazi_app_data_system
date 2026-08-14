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

from feishu_pricing_dispatcher import FeishuPricingDispatcher, safe_dispatch_kick  # noqa: E402
from feishu_task_store import FeishuTaskStore  # noqa: E402
from pricing_runner import PricingRunner  # noqa: E402


def fixed_clock():
    return datetime(2026, 6, 14, 8, 30, tzinfo=timezone.utc)


def draft_payload(task_id):
    return {
        "task_id": task_id,
        "source": "feishu",
        "status": "QUEUED",
        "brand": "本田",
        "series": "雅阁",
        "model_config": "2021款 260TURBO 豪华版",
        "license_date": "2021-06",
        "mileage_text": "5.8万公里",
        "color": "白色",
        "transfer_count_text": "1",
        "condition_text": "右前门喷漆，前杠喷漆",
        "created_at": "2026-06-14T08:30:00+00:00",
    }


class FeishuPricingDispatcherTest(unittest.TestCase):
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

    def make_runner(self):
        return PricingRunner(
            task_root=self.task_root,
            data_dir=self.data_dir,
            runtime_lock_path=self.runtime_lock,
            clock=fixed_clock,
        )

    def make_dispatcher(self, *, supervisor_sync=None, auto_send_result=False, send_result_live=False):
        return FeishuPricingDispatcher(
            task_root=self.task_root,
            data_dir=self.data_dir,
            runtime_lock_path=self.runtime_lock,
            clock=fixed_clock,
            supervisor_sync=supervisor_sync or (lambda *args, **kwargs: {"ok": True, "status": "WAITING_MANUAL_PRICE"}),
            first_stage_script=self.first_stage_script,
            first_stage_result_path=self.first_stage_result,
            second_stage_script=self.second_stage_script,
            second_stage_result_path=self.second_stage_result,
            auto_send_result=auto_send_result,
            send_result_live=send_result_live,
        )

    def create_queued_task(self, task_id, *, queued_at):
        task_dir = self.task_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "QUEUED",
                "source": "feishu",
                "queued_at": queued_at,
                "confirmed_at": queued_at,
                "created_at": queued_at,
                "updated_at": queued_at,
                "raw_chat_id": "oc_business",
            },
        )
        self.write_json(task_dir / "target_task_draft.json", draft_payload(task_id))
        return task_id

    def test_dispatcher_dry_run_identifies_queue_head_without_writing_current_task(self):
        first = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")
        second = self.create_queued_task("FS20260614_0002", queued_at="2026-06-14T08:31:00+00:00")

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["selected_task_id"], first)
        self.assertEqual(result["queued_task_ids"], [first, second])
        self.assertFalse((self.data_dir / "current_target_task.json").exists())

    def test_dispatcher_orders_multiple_queued_by_queued_at(self):
        later = self.create_queued_task("FS20260614_0002", queued_at="2026-06-14T08:31:00+00:00")
        earlier = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertEqual(result["selected_task_id"], earlier)
        self.assertEqual(result["queued_task_ids"], [earlier, later])

    def test_dispatcher_blocks_when_active_app_task_exists(self):
        self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")
        active_dir = self.task_root / "FS20260614_9999"
        active_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(active_dir / "status.json", {"task_id": "FS20260614_9999", "status": "RUNNING_FIRST_STAGE"})

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertFalse(result["ok"])
        self.assertIn("ACTIVE_APP_TASK_EXISTS", result["errors"])
        self.assertEqual(result["active_task_id"], "FS20260614_9999")

    def test_safe_dispatch_kick_skips_dispatch_once_when_loop_running(self):
        store = FeishuTaskStore(self.task_root, clock=fixed_clock)
        calls = []

        class FakeDispatcher:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            def dispatch_once(self, **kwargs):
                calls.append(("dispatch_once", kwargs))
                return {"ok": True}

        result = safe_dispatch_kick(
            store=store,
            task_id="FS20260614_0001",
            loop_running_checker=lambda: True,
            dispatcher_factory=FakeDispatcher,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["dispatcher_loop_running"])
        self.assertFalse(result["dispatch_once_called"])
        self.assertEqual(calls, [])

    def test_safe_dispatch_kick_loop_running_force_checks_blocked_task(self):
        store = FeishuTaskStore(self.task_root, clock=fixed_clock)
        task_dir = self.task_root / "FS20260614_0001"
        task_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": "FS20260614_0001",
                "status": "SYSTEM_BLOCKED",
                "errors": ["HUMAN_LOGIN_REQUIRED"],
                "last_health_check_at": "2026-06-14T08:30:00+00:00",
                "health_check_count": 1,
                "created_at": "2026-06-14T08:30:00+00:00",
                "updated_at": "2026-06-14T08:30:00+00:00",
            },
        )
        calls = []

        class FakeDispatcher:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            def _attempt_auto_recover_blocked_tasks(self, blocked_task_ids, *, force_health_check=False):
                calls.append(("recover", blocked_task_ids, force_health_check))
                return [
                    {
                        "task_id": blocked_task_ids[0],
                        "attempted": True,
                        "force_health_check": force_health_check,
                        "health_ok": False,
                        "errors": ["HUMAN_LOGIN_REQUIRED"],
                    }
                ]

        result = safe_dispatch_kick(
            store=store,
            task_id="FS20260614_0001",
            loop_running_checker=lambda: True,
            dispatcher_factory=FakeDispatcher,
            force_health_check=True,
            source="dispatcher_health_check",
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["dispatcher_loop_running"])
        self.assertFalse(result["dispatch_once_called"])
        self.assertEqual(calls[1], ("recover", ["FS20260614_0001"], True))
        self.assertIn("ADMIN_INTERVENTION_TASK_EXISTS", result["errors"])
        self.assertEqual(result["blocked_task_ids"], ["FS20260614_0001"])

    def test_safe_dispatch_kick_calls_dispatch_once_when_loop_not_running(self):
        store = FeishuTaskStore(self.task_root, clock=fixed_clock)
        calls = []

        class FakeDispatcher:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            def dispatch_once(self, **kwargs):
                calls.append(("dispatch_once", kwargs))
                return {"ok": True, "status": "DRY_RUN_READY"}

        result = safe_dispatch_kick(
            store=store,
            task_id="FS20260614_0001",
            loop_running_checker=lambda: False,
            dispatcher_factory=FakeDispatcher,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["dispatcher_loop_running"])
        self.assertTrue(result["dispatch_once_called"])
        self.assertEqual(calls[1], ("dispatch_once", {"dry_run": True, "allow_app_run": False, "force_health_check": True}))

    def test_safe_dispatch_kick_live_mode_force_confirm_runs_background(self):
        store = FeishuTaskStore(self.task_root, clock=fixed_clock)
        calls = []
        class FakeDispatcher:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            def dispatch_once(self, **kwargs):
                calls.append(("dispatch_once", kwargs))
                return {"ok": True, "status": "SUCCEEDED"}

        result = safe_dispatch_kick(
            store=store,
            task_id="FS20260614_0001",
            allow_app_run=True,
            dry_run=False,
            loop_running_checker=lambda: False,
            dispatcher_factory=FakeDispatcher,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["dispatch_once_called"])
        self.assertTrue(result["dispatch_once_started_background"])
        self.assertTrue(result["confirm_handler_sync_dispatch_forbidden"])

    def test_safe_dispatch_kick_live_mode_can_still_background_without_force(self):
        store = FeishuTaskStore(self.task_root, clock=fixed_clock)
        thread_calls = []

        class FakeThread:
            def __init__(self, **kwargs):
                thread_calls.append(("init", kwargs))
                self.name = kwargs["name"]

            def start(self):
                thread_calls.append(("start", self.name))

        class FakeDispatcher:
            def __init__(self, **kwargs):
                raise AssertionError("non-force background kick should not instantiate inline")

        with patch("feishu_pricing_dispatcher.threading.Thread", FakeThread):
            result = safe_dispatch_kick(
                store=store,
                task_id="FS20260614_0001",
                allow_app_run=True,
                dry_run=False,
                force_health_check=False,
                loop_running_checker=lambda: False,
                dispatcher_factory=FakeDispatcher,
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["dispatch_once_called"])
        self.assertTrue(result["dispatch_once_started_background"])
        self.assertEqual(thread_calls[0][0], "init")
        self.assertEqual(thread_calls[1][0], "start")

    def test_safe_dispatch_kick_live_inline_configures_auto_send_for_once(self):
        store = FeishuTaskStore(self.task_root, clock=fixed_clock)
        calls = []

        class FakeDispatcher:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            def dispatch_once(self, **kwargs):
                calls.append(("dispatch_once", kwargs))
                return {"ok": True, "status": "SUCCEEDED"}

        result = safe_dispatch_kick(
            store=store,
            task_id="FS20260614_0001",
            allow_app_run=True,
            dry_run=False,
            background=False,
            loop_running_checker=lambda: False,
            dispatcher_factory=FakeDispatcher,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["dispatch_once_called"])
        self.assertTrue(calls[0][1]["auto_send_result"])
        self.assertTrue(calls[0][1]["send_result_live"])
        self.assertEqual(calls[1], ("dispatch_once", {"dry_run": False, "allow_app_run": True, "force_health_check": True}))

    def test_runner_auto_prepare_queue_head_when_current_task_mismatch(self):
        first = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")
        self.create_queued_task("FS20260614_0002", queued_at="2026-06-14T08:31:00+00:00")
        self.write_json(self.data_dir / "current_target_task.json", {"task_id": "OLD_TASK"})
        runner = self.make_runner()

        def fake_run(*args, **kwargs):
            self.mock_first_stage_ready()
            return SimpleNamespace(returncode=0, stdout="first", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = runner.run_first_stage(
                first,
                allow_app_run=True,
                first_stage_script=self.first_stage_script,
                first_stage_result_path=self.first_stage_result,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "S10_READY")
        current = self.read_json(self.data_dir / "current_target_task.json")
        self.assertEqual(current["task_id"], first)

    def test_runner_blocks_non_queue_head_auto_prepare(self):
        self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")
        second = self.create_queued_task("FS20260614_0002", queued_at="2026-06-14T08:31:00+00:00")
        self.write_json(self.data_dir / "current_target_task.json", {"task_id": "OLD_TASK"})
        runner = self.make_runner()

        with patch("pricing_runner.subprocess.run") as run:
            result = runner.run_first_stage(
                second,
                allow_app_run=True,
                first_stage_script=self.first_stage_script,
                first_stage_result_path=self.first_stage_result,
            )

        run.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("TASK_NOT_QUEUE_HEAD", result["errors"])
        current = self.read_json(self.data_dir / "current_target_task.json")
        self.assertEqual(current["task_id"], "OLD_TASK")

    def test_runner_auto_prepare_normalizes_short_registration_date(self):
        task_id = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")
        task_dir = self.task_root / task_id
        draft = self.read_json(task_dir / "target_task_draft.json")
        draft["license_date"] = "22.8"
        self.write_json(task_dir / "target_task_draft.json", draft)

        result = self.make_runner().auto_prepare_queued_current_task(task_id)

        self.assertTrue(result["ok"])
        current = self.read_json(self.data_dir / "current_target_task.json")
        self.assertEqual(current["register_date"], "2022.08")
        self.assertEqual(current["registration_date"], "2022.08")
        self.assertEqual(current["register_year"], 2022)
        self.assertEqual(current["registration_date_year"], 2022)

    def test_dispatcher_dry_run_target_info_error_does_not_write_current_or_lock(self):
        task_id = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")
        task_dir = self.task_root / task_id
        draft = self.read_json(task_dir / "target_task_draft.json")
        draft["license_date"] = "not-a-date"
        self.write_json(task_dir / "target_task_draft.json", draft)

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertFalse(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["status"], "TARGET_INFO_NEEDS_CORRECTION")
        self.assertFalse((self.data_dir / "current_target_task.json").exists())
        self.assertFalse(self.runtime_lock.exists())
        status = self.read_json(task_dir / "status.json")
        self.assertEqual(status["business_status"], "TARGET_INFO_NEEDS_CORRECTION")
        feedback = self.read_json(task_dir / "target_info_correction_delivery.json")
        self.assertEqual(feedback["business_chat_id"], "oc_business")
        self.assertIn("上牌日期", feedback["reply_text"])

    def test_dispatcher_live_prepare_target_info_error_does_not_call_subprocess(self):
        task_id = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")
        task_dir = self.task_root / task_id
        draft = self.read_json(task_dir / "target_task_draft.json")
        draft.pop("color")
        self.write_json(task_dir / "target_task_draft.json", draft)

        with patch("pricing_runner.subprocess.run") as run:
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        run.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "TARGET_INFO_NEEDS_CORRECTION")
        self.assertFalse((self.data_dir / "current_target_task.json").exists())
        self.assertFalse(self.runtime_lock.exists())

    def test_dispatcher_runs_first_then_second_then_ready_to_send(self):
        task_id = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")
        calls = []

        def fake_run(*args, **kwargs):
            script = str(args[0][1])
            calls.append(script)
            if "runtime_s01_to_s10" in script:
                self.mock_first_stage_ready()
                return SimpleNamespace(returncode=0, stdout="first", stderr="")
            self.mock_second_stage_success()
            return SimpleNamespace(returncode=0, stdout="second", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_id"], task_id)
        self.assertEqual(len(calls), 2)
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "SUCCEEDED")
        self.assertEqual(status["recommended_next_action"], "ready-to-send")
        self.assertTrue((self.task_root / task_id / "dispatcher_result.json").exists())

    def test_dispatcher_continues_second_stage_until_reference_boundary_closes(self):
        task_id = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")
        calls = []
        second_stage_attempts = []

        def fake_run(*args, **kwargs):
            script = str(args[0][1])
            calls.append(script)
            if "runtime_s01_to_s10" in script:
                self.mock_first_stage_ready()
                return SimpleNamespace(returncode=0, stdout="first", stderr="")
            second_stage_attempts.append(len(second_stage_attempts) + 1)
            if len(second_stage_attempts) == 1:
                self.mock_second_stage_continue_next_reference()
            else:
                self.mock_second_stage_success()
            return SimpleNamespace(returncode=0, stdout="second", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher(auto_send_result=True, send_result_live=False).dispatch_once(
                dry_run=False,
                allow_app_run=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(result["second_stage_results"]), 2)
        self.assertEqual(result["second_stage_results"][0]["status"], "CONTINUE_NEXT_REFERENCE")
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertIsNotNone(result["send_result"])
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "SUCCEEDED")

    def test_dispatcher_auto_send_result_after_success_uses_dry_run_send_wrapper(self):
        task_id = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")

        def fake_run(*args, **kwargs):
            script = str(args[0][1])
            if "runtime_s01_to_s10" in script:
                self.mock_first_stage_ready()
                return SimpleNamespace(returncode=0, stdout="first", stderr="")
            self.mock_second_stage_success()
            return SimpleNamespace(returncode=0, stdout="second", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher(auto_send_result=True, send_result_live=False).dispatch_once(
                dry_run=False,
                allow_app_run=True,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["auto_send_result"])
        self.assertIsNotNone(result["send_result"])
        self.assertTrue(result["send_result"]["ok"])
        self.assertTrue(result["send_result"]["dry_run"])
        self.assertFalse(result["send_result"]["sent"])
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "SUCCEEDED")

    def test_dispatcher_syncs_supervisor_when_needs_review(self):
        task_id = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")
        synced = []
        sync_kwargs = []

        def supervisor_sync(sync_task_id, **kwargs):
            synced.append(sync_task_id)
            sync_kwargs.append(kwargs)
            return {"ok": True, "task_id": sync_task_id, "status": "WAITING_MANUAL_PRICE"}

        def fake_run(*args, **kwargs):
            script = str(args[0][1])
            if "runtime_s01_to_s10" in script:
                self.mock_first_stage_ready()
                return SimpleNamespace(returncode=0, stdout="first", stderr="")
            self.mock_second_stage_needs_review()
            return SimpleNamespace(returncode=0, stdout="second", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher(supervisor_sync=supervisor_sync).dispatch_once(dry_run=False, allow_app_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(synced, [task_id])
        self.assertTrue(sync_kwargs[0]["send_messages"])
        self.assertTrue(sync_kwargs[0]["dry_run"])
        self.assertEqual(result["status"], "WAITING_MANUAL_PRICE")

    def test_dispatcher_backfills_missing_manual_review_delivery_without_queue(self):
        task_id = "FS20260614_0001"
        task_dir = self.task_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "WAITING_MANUAL_PRICE",
                "technical_status": "SUCCEEDED",
                "business_status": "NEEDS_REVIEW",
                "waiting_manual_price": True,
                "waiting_manual_price_at": "2026-06-14T08:30:00+00:00",
                "raw_chat_id": "oc_business",
                "supervisor_chat_id": "oc_supervisor",
            },
        )
        calls = []

        def supervisor_sync(sync_task_id, **kwargs):
            calls.append((sync_task_id, kwargs))
            return {"ok": True, "task_id": sync_task_id, "status": "WAITING_MANUAL_PRICE"}

        result = self.make_dispatcher(supervisor_sync=supervisor_sync, send_result_live=False).dispatch_once(
            dry_run=False,
            allow_app_run=True,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["manual_review_notice_backfill"])
        self.assertEqual(calls[0][0], task_id)
        self.assertTrue(calls[0][1]["send_messages"])
        self.assertTrue(calls[0][1]["dry_run"])

    def test_dispatcher_failure_marks_admin_intervention_and_releases_lock(self):
        task_id = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")

        with patch("pricing_runner.subprocess.run", return_value=SimpleNamespace(returncode=2, stdout="fail", stderr="fail")):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "ADMIN_INTERVENTION_REQUIRED")
        self.assertFalse(self.runtime_lock.exists())
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "ADMIN_INTERVENTION_REQUIRED")

    def test_first_stage_target_task_field_missing_marks_target_info_correction_not_admin(self):
        task_id = self.create_queued_task("FS20260614_0001", queued_at="2026-06-14T08:30:00+00:00")

        def fake_run(*args, **kwargs):
            self.write_json(
                self.first_stage_result,
                {
                    "final_status": "TARGET_TASK_FIELD_MISSING",
                    "missing_fields": ["registration_date_year"],
                },
            )
            return SimpleNamespace(returncode=0, stdout="target missing", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "TARGET_INFO_NEEDS_CORRECTION")
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "TARGET_INFO_NEEDS_CORRECTION")
        self.assertEqual(status["recommended_next_action"], "ask-sender-to-resend-target-info")
        self.assertTrue((self.task_root / task_id / "target_info_correction_reply.preview.txt").exists())

    def mock_first_stage_ready(self, *args, **kwargs):
        self.write_json(
            self.first_stage_result,
            {
                "flow_state": {"S10_READY": True},
                "trisame_cards_count": 3,
                "same_source_cards": [{"id": 1}, {"id": 2}, {"id": 3}],
            },
        )

    def mock_second_stage_success(self, *args, **kwargs):
        self.write_json(
            self.second_stage_result,
            {
                "status": "SUCCEEDED",
                "final_status": "SUCCEEDED",
                "current_state": "SUCCEEDED",
                "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
                "target_score": 94.5,
                "boundary_confirmed": True,
                "boundary_reference_index": 2,
                "boundary_reference_score": 95.0,
                "final_reference_index": 1,
                "final_reference_score": 94.0,
                "final_reference_price_yuan": 98400,
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
            },
        )

    def mock_second_stage_continue_next_reference(self, *args, **kwargs):
        self.write_json(
            self.second_stage_result,
            {
                "status": "CONTINUE_NEXT_REFERENCE",
                "final_status": "CONTINUE_NEXT_REFERENCE",
                "current_state": "CONTINUE_NEXT_REFERENCE",
                "current_reference_index": 1,
                "next_reference_index": 2,
                "reference_history_len": 1,
                "target_score": 82.0,
                "reference_score": 68.0,
                "s14_collect_done": True,
                "s14_images_processed": 39,
                "s14_images_total": 39,
                "manual_review_required": False,
            },
        )

    def mock_second_stage_needs_review(self, *args, **kwargs):
        payload = {
            "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
            "final_status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
            "current_state": "FULL_CHAIN_MANUAL_REVIEW_DONE",
            "manual_review_required": True,
            "manual_review_reasons": ["NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING"],
            "pricing": {
                "target_guazi_listing_price_yuan": 96400,
                "guazi_service_fee_yuan": 1500,
                "guazi_net_payout_yuan": 94900,
                "cost_yuan": 1000,
                "profit_yuan": 7592,
                "suggested_purchase_price_yuan": 86308,
                "manual_review_required": True,
            },
        }
        self.write_json(self.second_stage_result, payload)

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
