"""
Tests for DeviceManager: connection pool + per-device locking.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from uiAutoAgent.executor.device_manager import DeviceManager
from uiAutoAgent.executor.drivers.base_driver import BaseDriver


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class MockDriver(BaseDriver):
    """Minimal mock driver for testing DeviceManager."""

    def __init__(self, serial: str):
        super().__init__(serial)
        self.connect_count = 0
        self.disconnect_count = 0

    def connect(self) -> None:
        self.connect_count += 1
        self._connected = True

    def disconnect(self) -> None:
        self.disconnect_count += 1
        self._connected = False


def make_driver(serial: str = "test-device") -> MockDriver:
    return MockDriver(serial)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeviceManagerRegistration:
    def test_register_device(self):
        dm = DeviceManager()
        dm.register("dev-01", lambda: make_driver("dev-01"))
        assert "dev-01" in dm._devices

    def test_register_same_device_twice(self):
        dm = DeviceManager()
        dm.register("dev-01", lambda: make_driver("dev-01"))
        dm.register("dev-01", lambda: make_driver("dev-01"))
        assert len(dm._devices) == 1

    def test_unregister_device(self):
        dm = DeviceManager()
        dm.register("dev-01", lambda: make_driver("dev-01"))
        dm.unregister("dev-01")
        assert "dev-01" not in dm._devices

    def test_status_empty(self):
        dm = DeviceManager()
        assert dm.status() == {}

    def test_status_shows_devices(self):
        dm = DeviceManager()
        dm.register("dev-01", lambda: make_driver("dev-01"))
        status = dm.status()
        assert "dev-01" in status
        assert status["dev-01"]["connected"] is False


class TestDeviceManagerAcquire:
    def test_acquire_connects_driver(self):
        dm = DeviceManager()
        driver_instance = make_driver("dev-01")
        dm.register("dev-01", lambda: driver_instance)

        with dm.acquire("dev-01") as drv:
            assert drv is driver_instance
            assert drv.is_connected

    def test_acquire_unregistered_raises(self):
        dm = DeviceManager()
        with pytest.raises(KeyError):
            with dm.acquire("nonexistent"):
                pass

    def test_acquire_exclusive_lock(self):
        """Second concurrent acquire should block until first releases."""
        dm = DeviceManager()
        dm.register("dev-01", lambda: make_driver("dev-01"))

        results = []

        def _task(index: int) -> None:
            with dm.acquire("dev-01", timeout=5.0) as _drv:
                results.append(f"start-{index}")
                time.sleep(0.1)
                results.append(f"end-{index}")

        t1 = threading.Thread(target=_task, args=(1,))
        t2 = threading.Thread(target=_task, args=(2,))
        t1.start()
        time.sleep(0.02)
        t2.start()
        t1.join()
        t2.join()

        # Verify that task 1 completed before task 2 started (no interleaving)
        start1 = results.index("start-1")
        end1 = results.index("end-1")
        start2 = results.index("start-2")
        assert end1 < start2 or start1 > results.index("end-2"), (
            f"Interleaved execution detected: {results}"
        )

    def test_acquire_timeout(self):
        """Acquire should raise TimeoutError when device is held too long."""
        dm = DeviceManager()
        dm.register("dev-01", lambda: make_driver("dev-01"))

        entry = dm._devices["dev-01"]
        entry.lock.acquire()  # manually hold the lock
        try:
            with pytest.raises(TimeoutError):
                with dm.acquire("dev-01", timeout=0.1):
                    pass
        finally:
            entry.lock.release()

    def test_disconnect_all(self):
        dm = DeviceManager()
        drv = make_driver("dev-01")
        dm.register("dev-01", lambda: drv)

        with dm.acquire("dev-01"):
            pass  # connect

        dm.disconnect_all()
        assert dm._devices["dev-01"].driver is None

    def test_error_count_increments_on_exception(self):
        dm = DeviceManager()
        dm.register("dev-01", lambda: make_driver("dev-01"))

        for _ in range(2):
            with pytest.raises(RuntimeError):
                with dm.acquire("dev-01"):
                    raise RuntimeError("test error")

        assert dm._devices["dev-01"].error_count == 2

    def test_driver_reset_after_3_errors(self):
        """After 3 errors, driver should be reset (None)."""
        dm = DeviceManager()
        dm.register("dev-01", lambda: make_driver("dev-01"))

        for _ in range(3):
            try:
                with dm.acquire("dev-01"):
                    raise RuntimeError("test error")
            except RuntimeError:
                pass

        assert dm._devices["dev-01"].driver is None
        assert dm._devices["dev-01"].error_count == 0  # reset clears count
