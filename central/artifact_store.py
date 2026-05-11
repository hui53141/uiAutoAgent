"""
ArtifactStore: manages uploaded batch artifacts (logs, screenshots).
Videos are NOT stored here — they stay on executor (Strategy C).
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class CaseArtifact:
    task_id: str
    aw_class: str
    aw_method: str
    error: str
    log_path: Optional[str]
    screenshot_paths: List[str]
    video_available: bool


@dataclass
class BatchArtifacts:
    batch_id: str
    node_id: str
    registered_at: float
    failed_cases: List[CaseArtifact] = field(default_factory=list)


class ArtifactStore:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_batch_dir(self, batch_id: str) -> Path:
        path = self.base_dir / batch_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def register_batch(
        self,
        batch_id,
        node_id,
        task_results,
        log_paths,
        screenshot_paths,
        video_flags,
    ) -> None:
        batch = BatchArtifacts(
            batch_id=batch_id,
            node_id=node_id,
            registered_at=time.time(),
            failed_cases=[],
        )
        for task_result in task_results:
            task_id = task_result["task_id"]
            batch.failed_cases.append(
                CaseArtifact(
                    task_id=task_id,
                    aw_class=task_result.get("aw_class", ""),
                    aw_method=task_result.get("aw_method", "run"),
                    error=task_result.get("error", ""),
                    log_path=log_paths.get(task_id),
                    screenshot_paths=screenshot_paths.get(task_id, []),
                    video_available=bool(video_flags.get(task_id, False)),
                )
            )

        metadata_path = self.get_batch_dir(batch_id) / "metadata.json"
        payload = asdict(batch)
        await asyncio.to_thread(
            metadata_path.write_text,
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def get_batch(self, batch_id: str) -> BatchArtifacts:
        metadata_path = self.get_batch_dir(batch_id) / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Batch metadata not found: {batch_id}")
        raw = await asyncio.to_thread(metadata_path.read_text, encoding="utf-8")
        data = json.loads(raw)
        return BatchArtifacts(
            batch_id=data["batch_id"],
            node_id=data["node_id"],
            registered_at=data["registered_at"],
            failed_cases=[CaseArtifact(**item) for item in data.get("failed_cases", [])],
        )
