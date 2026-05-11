from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class CaseFailure:
    task_id: str
    aw_class: str
    aw_method: str
    error: str
    log_path: str
    screenshot_paths: List[str]
    video_path: Optional[str]


class BatchCollector:
    """Collects all failure artifacts during a batch run."""

    def __init__(self, batch_id: str, log_dir: str):
        self.batch_id = batch_id
        self.log_dir = log_dir
        self._failures: List[CaseFailure] = []
        self._lock = threading.Lock()

    def record_failure(self, failure: CaseFailure) -> None:
        with self._lock:
            self._failures.append(failure)

    def has_failures(self) -> bool:
        with self._lock:
            return bool(self._failures)

    def get_failures(self) -> List[CaseFailure]:
        with self._lock:
            return list(self._failures)

    def get_video_flags(self) -> Dict[str, bool]:
        with self._lock:
            return {failure.task_id: bool(failure.video_path) for failure in self._failures}
