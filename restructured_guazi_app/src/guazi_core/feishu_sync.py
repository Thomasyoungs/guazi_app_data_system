"""Feishu task sync framework.

Migrated from the original feishu_sync.py.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .task_normalizer import TargetCarTask, TaskContractError, normalize_target_task


FEISHU_FIELD_MAPPING: dict[str, dict[str, Any]] = {
    "task_id": {"display_name": "任务编号", "type": "text", "required": True, "forbid_manual_input": True},
    "brand": {"display_name": "品牌", "type": "text", "required": True},
    "series": {"display_name": "车系", "type": "text", "required": True},
    "model_year": {"display_name": "年款", "type": "text", "required": True},
    "trim": {"display_name": "配置", "type": "text", "required": True},
    "color": {"display_name": "颜色", "type": "text", "required": True},
    "registration_date": {"display_name": "上牌年月", "type": "text", "required": True},
    "mileage_10k_km": {"display_name": "表显里程", "type": "number", "required": True},
    "transfer_count": {"display_name": "过户次数", "type": "number", "required": True},
    "condition_text": {"display_name": "车况描述", "type": "text", "required": True},
    "accident_count": {"display_name": "出险次数", "type": "number", "required": False},
    "max_accident_amount": {"display_name": "最大出险金额", "type": "number", "required": False},
}


class FeishuTaskReader:
    """Future real Feishu task reader."""

    simulation_only = False

    def read_target_task(self, task_id: str) -> dict[str, Any]:
        raise NotImplementedError("Real Feishu API task reading is not connected yet.")


class FeishuExportTaskReader:
    """Reader for CSV/JSON files exported from the Feishu task table."""

    simulation_only = False
    source = "feishu_export"

    def read_json(self, path: str | Path) -> dict[str, Any]:
        export_path = Path(path)
        payload = json.loads(export_path.read_text(encoding="utf-8"))
        return self._normalize_payload(payload, export_path)

    def read_csv(self, path: str | Path) -> dict[str, Any]:
        export_path = Path(path)
        with export_path.open("r", encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        if len(rows) != 1:
            raise TaskContractError("Feishu export CSV must contain exactly one target task row.")
        payload = dict(rows[0])
        return self._normalize_payload(payload, export_path)

    def _normalize_payload(self, payload: dict[str, Any], path: Path) -> dict[str, Any]:
        imported_at = datetime.now(timezone.utc).isoformat()
        task = normalize_target_task(payload, source="feishu_export", simulation_only=False, source_import_path=str(path.resolve()), source_imported_at=imported_at)
        return {"task": task.to_dict(), "source": "feishu_export", "simulation_only": False}


class MockTaskReader:
    """Explicit local reader for simulation and offline regression only."""

    simulation_only = True

    def read_json(self, path: str | Path) -> dict[str, Any]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return self._normalize_payload(payload)

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        task = normalize_target_task(payload, source="mock", simulation_only=True)
        return {"task": task.to_dict(), "source": "mock", "simulation_only": True}
