"""
Tests for FailureAggregator: de-duplication and threshold-based triggering.
"""

from __future__ import annotations

import time
from typing import List
from unittest.mock import MagicMock

import pytest

from uiAutoAgent.central.healer.failure_aggregator import FailureAggregator, FailureReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_report(
    task_id: str = "task-login-smoke",
    node_id: str = "node-01",
    error: str = "NoSuchElementException: login_btn not found",
) -> FailureReport:
    return FailureReport(
        task_id=task_id,
        node_id=node_id,
        device_serial="emulator-5554",
        error=error,
        screenshot_path=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFailureFingerprint:
    def test_same_task_same_error_same_fingerprint(self):
        r1 = make_report(node_id="node-01")
        r2 = make_report(node_id="node-02")
        assert r1.fingerprint() == r2.fingerprint()

    def test_different_task_different_fingerprint(self):
        r1 = make_report(task_id="task-login")
        r2 = make_report(task_id="task-settings")
        assert r1.fingerprint() != r2.fingerprint()

    def test_different_error_different_fingerprint(self):
        r1 = make_report(error="NoSuchElementException: login_btn")
        r2 = make_report(error="TimeoutException: waited 10s")
        assert r1.fingerprint() != r2.fingerprint()

    def test_fingerprint_stable(self):
        r = make_report()
        assert r.fingerprint() == r.fingerprint()


class TestThresholdTriggering:
    def test_callback_not_triggered_below_threshold(self):
        agg = FailureAggregator(threshold=3)
        cb = MagicMock()
        agg.on_threshold_reached(cb)

        agg.record(make_report(node_id="node-01"))
        agg.record(make_report(node_id="node-02"))
        cb.assert_not_called()

    def test_callback_triggered_at_threshold(self):
        agg = FailureAggregator(threshold=2)
        cb = MagicMock()
        agg.on_threshold_reached(cb)

        agg.record(make_report(node_id="node-01"))
        agg.record(make_report(node_id="node-02"))
        cb.assert_called_once()

    def test_callback_not_triggered_twice(self):
        agg = FailureAggregator(threshold=2)
        cb = MagicMock()
        agg.on_threshold_reached(cb)

        agg.record(make_report(node_id="node-01"))
        agg.record(make_report(node_id="node-02"))
        agg.record(make_report(node_id="node-03"))  # 3rd report after heal started
        cb.assert_called_once()  # should NOT be called again

    def test_different_failures_trigger_separately(self):
        agg = FailureAggregator(threshold=2)
        cb = MagicMock()
        agg.on_threshold_reached(cb)

        agg.record(make_report(task_id="task-login", node_id="node-01"))
        agg.record(make_report(task_id="task-settings", node_id="node-01"))
        # Only one from each unique failure; threshold=2 not yet reached for either
        cb.assert_not_called()

        agg.record(make_report(task_id="task-login", node_id="node-02"))
        assert cb.call_count == 1

    def test_multiple_callbacks_all_called(self):
        agg = FailureAggregator(threshold=2)
        cb1 = MagicMock()
        cb2 = MagicMock()
        agg.on_threshold_reached(cb1)
        agg.on_threshold_reached(cb2)

        agg.record(make_report(node_id="node-01"))
        agg.record(make_report(node_id="node-02"))
        cb1.assert_called_once()
        cb2.assert_called_once()


class TestMarkHealed:
    def test_healed_failure_ignored(self):
        agg = FailureAggregator(threshold=2)
        cb = MagicMock()
        agg.on_threshold_reached(cb)

        r = make_report(node_id="node-01")
        fp = r.fingerprint()
        agg.record(r)
        agg.mark_healed(fp)

        # Record more failures for same pattern — should be ignored
        agg.record(make_report(node_id="node-02"))
        agg.record(make_report(node_id="node-03"))
        cb.assert_not_called()

    def test_get_groups_after_heal_empty(self):
        agg = FailureAggregator(threshold=3)
        r = make_report()
        fp = r.fingerprint()
        agg.record(r)
        agg.mark_healed(fp)
        assert fp not in agg.get_groups()


class TestPersistence:
    def test_persist_and_reload(self, tmp_path):
        agg1 = FailureAggregator(threshold=5, persist_dir=str(tmp_path))
        agg1.record(make_report(node_id="node-01"))

        # Create a new aggregator pointing to the same directory
        agg2 = FailureAggregator(threshold=5, persist_dir=str(tmp_path))
        groups = agg2.get_groups()
        assert len(groups) == 1
