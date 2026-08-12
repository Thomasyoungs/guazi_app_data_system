import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from pricing_runner import PricingRunner  # noqa: E402


def fixed_clock():
    return datetime(2026, 6, 9, 8, 30, tzinfo=timezone.utc)


def draft_payload(task_id="FS20260609_0001"):
    return {
        "task_id": task_id,
        "source": "feishu",
        "status": "CONFIRMED",
        "brand": "本田",
        "series": "雅阁",
        "model_config": "2021款 260TURBO 豪华版",
        "license_date": "2021-06",
        "mileage_text": "5.8万公里",
        "color": "白色",
        "transfer_count_text": "1",
        "condition_text": "右前门喷漆，前杠喷漆",
        "created_at": "2026-06-09T08:30:00+00:00",
    }


class PricingRunnerPhase2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task_root = self.root / "data" / "feishu_tasks"
        self.data_dir = self.root / "data"
        self.runtime_lock = self.root / "runtime" / "pricing.lock"
        self.runner = PricingRunner(
            task_root=self.task_root,
            data_dir=self.data_dir,
            runtime_lock_path=self.runtime_lock,
            clock=fixed_clock,
        )

    def tearDown(self):
        self.temp.cleanup()

    def create_task(self, task_id="FS20260609_0001", status="CONFIRMED", with_draft=True):
        task_dir = self.task_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(task_dir / "status.json", {
            "task_id": task_id,
            "status": status,
            "source": "feishu",
            "created_at": "2026-06-09T08:30:00+00:00",
            "updated_at": "2026-06-09T08:30:00+00:00",
        })
        if with_draft:
            payload = draft_payload(task_id)
            payload["status"] = status
            self.write_json(task_dir / "target_task_draft.json", payload)
        return task_id

    def test_confirmed_task_dry_run_success(self):
        task_id = self.create_task()

        result = self.runner.dry_run(task_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status_after"], "CONFIRMED")

    def test_dry_run_generates_preview_json(self):
        task_id = self.create_task()

        self.runner.dry_run(task_id)

        self.assertTrue((self.task_root / task_id / "current_target_task.preview.json").exists())
        self.assertTrue((self.task_root / task_id / "runner_validation.json").exists())

    def test_dry_run_does_not_write_current_target_task(self):
        task_id = self.create_task()

        self.runner.dry_run(task_id)

        self.assertFalse((self.data_dir / "current_target_task.json").exists())

    def test_confirmed_task_prepare_current_task_success(self):
        task_id = self.create_task()

        result = self.runner.prepare_current_task(task_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status_after"], "QUEUED")

    def test_prepare_current_task_writes_current_target_task(self):
        task_id = self.create_task()

        self.runner.prepare_current_task(task_id)

        self.assertTrue((self.data_dir / "current_target_task.json").exists())

    def test_prepare_current_task_generates_snapshot(self):
        task_id = self.create_task()

        self.runner.prepare_current_task(task_id)

        self.assertTrue((self.task_root / task_id / "current_target_task.snapshot.json").exists())

    def test_existing_current_target_task_is_backed_up(self):
        task_id = self.create_task()
        self.write_json(self.data_dir / "current_target_task.json", {"old": True})

        result = self.runner.prepare_current_task(task_id)

        backups = list((self.data_dir / "backup").glob("current_target_task.*.json"))
        self.assertTrue(result["ok"])
        self.assertEqual(len(backups), 1)
        self.assertIn("CURRENT_TARGET_TASK_EXISTS_BACKED_UP", result["validation"]["warnings"])

    def test_draft_task_cannot_dry_run(self):
        task_id = self.create_task(status="DRAFT")

        result = self.runner.dry_run(task_id)

        self.assertFalse(result["ok"])
        self.assertIn("TASK_NOT_CONFIRMED", result["errors"])

    def test_draft_task_cannot_prepare(self):
        task_id = self.create_task(status="DRAFT")

        result = self.runner.prepare_current_task(task_id)

        self.assertFalse(result["ok"])
        self.assertIn("TASK_NOT_CONFIRMED", result["errors"])

    def test_invalid_task_cannot_enter_runner(self):
        task_id = self.create_task(status="INVALID")

        result = self.runner.dry_run(task_id)

        self.assertFalse(result["ok"])
        self.assertIn("TASK_INVALID", result["errors"])

    def test_cancelled_task_cannot_enter_runner(self):
        task_id = self.create_task(status="CANCELLED")

        result = self.runner.dry_run(task_id)

        self.assertFalse(result["ok"])
        self.assertIn("TASK_CANCELLED", result["errors"])

    def test_unknown_task_id_returns_task_not_found(self):
        result = self.runner.dry_run("FS20260609_9999")

        self.assertFalse(result["ok"])
        self.assertIn("TASK_NOT_FOUND", result["errors"])

    def test_missing_target_task_draft_returns_error(self):
        task_id = self.create_task(with_draft=False)

        result = self.runner.dry_run(task_id)

        self.assertFalse(result["ok"])
        self.assertIn("TARGET_TASK_DRAFT_MISSING", result["errors"])

    def test_lock_blocks_prepare_current_task(self):
        task_id = self.create_task()
        self.runtime_lock.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_lock.write_text("locked", encoding="utf-8")

        result = self.runner.prepare_current_task(task_id)

        self.assertFalse(result["ok"])
        self.assertIn("PRICING_LOCK_EXISTS", result["errors"])

    def test_status_does_not_change_status(self):
        task_id = self.create_task()

        status = self.runner.status(task_id)

        self.assertEqual(status["status"], "CONFIRMED")
        persisted = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(persisted["status"], "CONFIRMED")

    def test_prepare_current_task_success_changes_status_to_queued(self):
        task_id = self.create_task()

        self.runner.prepare_current_task(task_id)

        persisted = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(persisted["status"], "QUEUED")

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
