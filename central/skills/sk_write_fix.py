from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Dict

from uiAutoAgent.central.skills.base import SkillBase, SkillResult

_FILE_LOCKS: Dict[str, asyncio.Lock] = {}
_FILE_LOCKS_GUARD = asyncio.Lock()


async def _get_file_lock(file_path: str) -> asyncio.Lock:
    async with _FILE_LOCKS_GUARD:
        if file_path not in _FILE_LOCKS:
            _FILE_LOCKS[file_path] = asyncio.Lock()
        return _FILE_LOCKS[file_path]


class WriteFixSkill(SkillBase):
    name = "write_fix_to_central"
    description = "Persist a validated AW fix into the central git checkout with per-file locking and backups."
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "fixed_code": {"type": "string"},
            "batch_id": {"type": "string"},
            "affected_task_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["file_path", "fixed_code", "batch_id"],
    }

    def __init__(self, project_root: str):
        self.project_root = Path(project_root)

    async def execute(self, **kwargs) -> SkillResult:
        file_path = kwargs["file_path"]
        fixed_code = kwargs["fixed_code"]
        affected_task_ids = kwargs.get("affected_task_ids", [])
        resolved_path = self.project_root / file_path
        lock = await _get_file_lock(file_path)
        async with lock:
            if not resolved_path.exists():
                return SkillResult(False, None, error=f"Target file not found: {file_path}")
            backup_relative = f"{file_path}.bak_{int(time.time())}"
            backup_path = self.project_root / backup_relative
            await asyncio.to_thread(backup_path.write_text, resolved_path.read_text(encoding="utf-8"), encoding="utf-8")
            await asyncio.to_thread(resolved_path.write_text, fixed_code, encoding="utf-8")
        return SkillResult(
            True,
            {
                "file_path": file_path,
                "backup_path": backup_relative,
                "affected_task_ids": affected_task_ids,
            },
        )
