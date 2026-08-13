"""Main application class for the Guazi APP data system.

Orchestrates all components: config loading, simulation, device interaction,
Feishu integration, and reporting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .application import build_runtime, export_report, run_simulation, run_device
from .feishu.message_handler import FeishuMessageHandler
from .feishu.task_store import FeishuTaskStore
from .task_normalizer import TargetCarTask


class GuaziApp:
    """Main application class that orchestrates the Guazi app data system."""

    def __init__(self, config_dir: str = "./config", output_dir: str = "./output"):
        self.config_dir = Path(config_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.result_path = self.output_dir / "result.json"
        self.feishu_handler = FeishuMessageHandler(config_dir=config_dir)
        self.task_store = FeishuTaskStore(task_root=self.output_dir / "tasks")

    def run_simulation(self) -> dict[str, Any]:
        """Run the simulation mode of the application."""
        runtime = build_runtime(str(self.config_dir))
        result = run_simulation(runtime)
        with open(self.result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    def run_device(self, task: TargetCarTask | None = None, adb_serial: str | None = None) -> dict[str, Any]:
        """Run the device mode: launch APP and enter search conditions via ADB."""
        runtime = build_runtime(str(self.config_dir))
        result = run_device(runtime, task=task, adb_serial=adb_serial)
        with open(self.result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    def handle_feishu_message(self, message: dict[str, Any], chat_id: str | None = None) -> dict[str, Any]:
        """Handle an incoming Feishu message and return the result."""
        task_data = self.feishu_handler.parse_message_to_task(message)
        if not task_data:
            return {
                "ok": False,
                "error": "Could not parse message to task data",
                "reply": "无法解析您发送的信息，请按照指定格式重新发送。",
            }

        task_dict = task_data.to_dict()
        task_id = task_dict.get("task_id", "UNDEFINED")

        # Store the task
        task_file_path = self.task_store.save_task(task_id, task_dict)

        # Run the simulation for this specific task
        runtime = build_runtime(str(self.config_dir))
        result = run_simulation(runtime)

        # Update the result with the actual task data
        result["target_car"] = task_dict
        result["metadata"]["task_id"] = task_id

        # Save the result to the task-specific location
        task_result_path = self.output_dir / "tasks" / task_id / "result.json"
        task_result_path.parent.mkdir(parents=True, exist_ok=True)
        with open(task_result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # Send result back to Feishu
        feishu_response = self.feishu_handler.send_result_back(result, chat_id)

        return {
            "ok": True,
            "task_id": task_id,
            "result": result,
            "feishu_response": feishu_response,
        }
