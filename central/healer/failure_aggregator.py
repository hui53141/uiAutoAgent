"""
FailureAggregator: collect failure reports from executor nodes,
de-duplicate, and trigger self-healing only once per unique failure.

Principle: LLM is only invoked ONCE per unique failure pattern,
regardless of how many nodes report the same issue.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from uiAutoAgent.core import setup_logging

logger = setup_logging("FailureAggregator")


@dataclass
class FailureReport:
    """A failure reported by an executor node."""

    task_id: str
    node_id: str
    device_serial: str
    error: str
    screenshot_path: Optional[str]
    timestamp: float = field(default_factory=time.time)
    extra: Dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """
        Generate a stable fingerprint for de-duplication.

        Two failures with the same task + error type are considered identical
        across different nodes.
        """
        # Normalize error: take first non-empty line (exception type + message)
        first_line = self.error.strip().splitlines()[0] if self.error.strip() else "unknown"
        raw = f"{self.task_id}::{first_line}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "node_id": self.node_id,
            "device_serial": self.device_serial,
            "error": self.error,
            "screenshot_path": self.screenshot_path,
            "timestamp": self.timestamp,
            "extra": self.extra,
        }


class FailureAggregator:
    """
    Aggregates failures from multiple executor nodes.

    When the same failure fingerprint is received from >= `threshold` nodes,
    the `on_threshold_reached` callback is invoked exactly once.

    This ensures the LLM healing process is triggered a single time
    rather than once per failing node.
    """

    def __init__(
        self,
        threshold: int = 2,
        ttl: int = 3600,
        persist_dir: Optional[str] = None,
    ):
        self.threshold = threshold
        self.ttl = ttl  # seconds before an aggregated group expires
        self._lock = threading.Lock()
        # fingerprint -> list of reports
        self._groups: Dict[str, List[FailureReport]] = {}
        # fingerprint -> timestamp when healing was triggered
        self._healed: Dict[str, float] = {}
        self._callbacks: List[Callable[[str, List[FailureReport]], None]] = []

        self._persist_dir: Optional[Path] = None
        if persist_dir:
            self._persist_dir = Path(persist_dir)
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._load_persisted()

        # Background cleanup thread
        t = threading.Thread(target=self._cleanup_loop, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_threshold_reached(
        self, callback: Callable[[str, List[FailureReport]], None]
    ) -> None:
        """Register a callback triggered when a failure crosses the threshold."""
        self._callbacks.append(callback)

    def record(self, report: FailureReport) -> None:
        """
        Record a failure report. If the same failure has already been healed
        recently, it is ignored.
        """
        fp = report.fingerprint()
        with self._lock:
            # If already healed within TTL, ignore
            if fp in self._healed:
                elapsed = time.time() - self._healed[fp]
                if elapsed < self.ttl:
                    logger.debug(
                        "Failure %s already healed %.0fs ago; ignoring.", fp, elapsed
                    )
                    return

            group = self._groups.setdefault(fp, [])
            group.append(report)
            self._persist_group(fp, group)
            count = len(group)
            logger.info(
                "Failure recorded: fp=%s task=%s node=%s count=%d/%d",
                fp,
                report.task_id,
                report.node_id,
                count,
                self.threshold,
            )

            if count >= self.threshold:
                self._trigger_healing(fp, group)

    def get_groups(self) -> Dict[str, List[FailureReport]]:
        with self._lock:
            return dict(self._groups)

    def mark_healed(self, fingerprint: str) -> None:
        """Mark a failure pattern as healed (reset aggregation)."""
        with self._lock:
            self._healed[fingerprint] = time.time()
            self._groups.pop(fingerprint, None)
            logger.info("Failure %s marked as healed.", fingerprint)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _trigger_healing(self, fp: str, group: List[FailureReport]) -> None:
        """Fire callbacks (runs in caller thread; callbacks should be async-safe)."""
        logger.info(
            "Threshold reached for fp=%s (%d reports). Triggering healing.",
            fp,
            len(group),
        )
        # Mark immediately so concurrent calls don't trigger again
        self._healed[fp] = time.time()
        for cb in self._callbacks:
            try:
                cb(fp, list(group))
            except Exception as exc:
                logger.error("Healing callback error: %s", exc)

    def _cleanup_loop(self) -> None:
        while True:
            time.sleep(300)
            now = time.time()
            with self._lock:
                expired = [
                    fp for fp, ts in self._healed.items()
                    if now - ts > self.ttl
                ]
                for fp in expired:
                    del self._healed[fp]
                    self._groups.pop(fp, None)
                if expired:
                    logger.debug("Cleaned up %d expired healing records.", len(expired))

    # ------------------------------------------------------------------
    # Persistence (survive restarts)
    # ------------------------------------------------------------------

    def _persist_group(self, fp: str, group: List[FailureReport]) -> None:
        if not self._persist_dir:
            return
        path = self._persist_dir / f"{fp}.json"
        data = [r.to_dict() for r in group]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_persisted(self) -> None:
        if not self._persist_dir:
            return
        for path in self._persist_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                fp = path.stem
                reports = [
                    FailureReport(**{k: v for k, v in d.items()})
                    for d in data
                ]
                self._groups[fp] = reports
                logger.info("Loaded %d persisted failures for fp=%s", len(reports), fp)
            except Exception as exc:
                logger.warning("Could not load persisted failure %s: %s", path, exc)
