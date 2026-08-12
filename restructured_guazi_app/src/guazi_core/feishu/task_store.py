"""Feishu task storage for the Guazi app data system."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class FeishuTaskStore:
    """Manages storage of tasks received from Feishu."""
    
    def __init__(self, task_root: Path | str | None = None):
        self.task_root = Path(task_root) if task_root else Path("./tasks")
        self.task_root.mkdir(parents=True, exist_ok=True)
    
    def save_task(self, task_id: str, task_data: Dict[str, Any]) -> Path:
        """Save a task to the store."""
        task_dir = self.task_root / task_id
        task_dir.mkdir(exist_ok=True)
        
        # Save the task data
        task_file = task_dir / "task.json"
        task_data['updated_at'] = datetime.now().isoformat()
        with open(task_file, 'w', encoding='utf-8') as f:
            json.dump(task_data, f, ensure_ascii=False, indent=2)
        
        return task_file
    
    def load_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Load a task from the store."""
        task_file = self.task_root / task_id / "task.json"
        if not task_file.exists():
            return None
        
        with open(task_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def list_tasks(self) -> List[str]:
        """List all task IDs in the store."""
        if not self.task_root.exists():
            return []
        
        tasks = []
        for task_dir in self.task_root.iterdir():
            if task_dir.is_dir():
                task_file = task_dir / "task.json"
                if task_file.exists():
                    tasks.append(task_dir.name)
        return tasks