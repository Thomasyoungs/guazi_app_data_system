import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_pricing_dispatcher import safe_dispatch_kick  # noqa: E402
from feishu_task_store import FeishuTaskStore  # noqa: E402


def fixed_clock():
    return datetime(2026, 7, 1, 9, 45, tzinfo=timezone.utc)


class FeishuConfirmAckOrderPatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = FeishuTaskStore(self.root / "data" / "feishu_tasks", clock=fixed_clock)

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_confirm_safe_dispatch_kick_starts_background_and_returns_before_dispatch_finishes(self):
        started = threading.Event()
        finished = threading.Event()

        class SlowDispatcher:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def dispatch_once(self, **kwargs):
                started.set()
                time.sleep(0.25)
                finished.set()
                return {"ok": True, "status": "NO_QUEUED_TASK"}

        before = time.perf_counter()
        result = safe_dispatch_kick(
            store=self.store,
            task_id="FS20260701_0006",
            allow_app_run=True,
            dry_run=False,
            force_health_check=True,
            loop_running_checker=lambda: False,
            dispatcher_factory=SlowDispatcher,
            source="feishu_confirm",
        )
        elapsed = time.perf_counter() - before

        self.assertTrue(result["ok"])
        self.assertTrue(result["dispatch_once_started_background"])
        self.assertFalse(result["dispatch_once_called"])
        self.assertTrue(result["confirm_handler_sync_dispatch_forbidden"])
        self.assertLess(elapsed, 0.2)
        self.assertTrue(started.wait(1.0))
        self.assertTrue(finished.wait(1.0))

    def test_confirm_safe_dispatch_kick_does_not_sync_dispatch_when_loop_is_running(self):
        result = safe_dispatch_kick(
            store=self.store,
            task_id="FS20260701_0006",
            allow_app_run=True,
            dry_run=False,
            force_health_check=True,
            loop_running_checker=lambda: True,
            source="feishu_confirm",
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["kick_requested"])
        self.assertTrue(result["loop_running"])
        self.assertFalse(result["dispatch_once_called"])
        self.assertTrue(result["confirm_handler_sync_dispatch_forbidden"])
        self.assertTrue(result["force_health_check_deferred_for_start_ack"])

    def test_start_ack_delivery_dry_run_returned_to_gateway_is_not_live_sent(self):
        task_id = "FS20260701_0006"
        task_dir = self.store.task_dir(task_id)
        self.write_json(task_dir / "status.json", {"task_id": task_id, "status": "QUEUED"})

        self.store.record_start_ack_delivery_result(
            task_id,
            send_result={"ok": True, "dry_run": True, "message_id": None},
            returned_to_gateway=True,
        )

        delivery = self.read_json(task_dir / "feishu_start_message_delivery.json")
        status = self.read_json(task_dir / "status.json")
        self.assertTrue(delivery["start_ack_returned_to_gateway"])
        self.assertFalse(delivery["start_ack_live_sent"])
        self.assertFalse(delivery["start_ack_live_send_attempted"])
        self.assertIsNone(delivery["start_ack_message_id"])
        self.assertTrue(status["start_ack_sent"])
        self.assertFalse(status["start_ack_live_sent"])

