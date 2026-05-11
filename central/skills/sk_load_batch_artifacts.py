from __future__ import annotations

import asyncio
from pathlib import Path

from uiAutoAgent.central.artifact_store import ArtifactStore
from uiAutoAgent.central.skills.base import SkillBase, SkillResult


class LoadBatchArtifactsSkill(SkillBase):
    name = "load_batch_artifacts"
    description = "Load failed batch logs and screenshot metadata for the current healing batch."
    input_schema = {
        "type": "object",
        "properties": {
            "batch_id": {"type": "string"},
            "max_log_chars": {"type": "integer", "default": 8000},
        },
        "required": ["batch_id"],
    }

    def __init__(self, artifact_store: ArtifactStore):
        self.artifact_store = artifact_store

    async def execute(self, **kwargs) -> SkillResult:
        batch_id = kwargs["batch_id"]
        max_log_chars = int(kwargs.get("max_log_chars", 8000))
        batch = await self.artifact_store.get_batch(batch_id)
        failed_cases = []
        for case in batch.failed_cases:
            log_text = ""
            if case.log_path and Path(case.log_path).exists():
                log_text = await asyncio.to_thread(
                    Path(case.log_path).read_text,
                    encoding="utf-8",
                    errors="ignore",
                )
                if len(log_text) > max_log_chars:
                    log_text = log_text[:max_log_chars]
            failed_cases.append(
                {
                    "task_id": case.task_id,
                    "aw_class": case.aw_class,
                    "aw_method": case.aw_method,
                    "log_text": log_text,
                    "screenshot_paths": list(case.screenshot_paths),
                    "video_available": case.video_available,
                }
            )
        return SkillResult(True, {"batch_id": batch.batch_id, "failed_cases": failed_cases})
