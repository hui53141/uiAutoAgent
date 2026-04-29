"""
DeviceManager: connection pool + per-device locking for executor nodes.

Responsibilities:
  - Maintain a pool of driver connections (Appium + Hardware)
  - Provide thread-safe device acquisition / release
  - Handle reconnection on stale sessions
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, Optional

from uiAutoAgent.core import setup_logging
from uiAutoAgent.executor.drivers.base_driver import BaseDriver

logger = setup_logging("DeviceManager")


@dataclass
class DeviceEntry:
    """Represents one managed device slot."""

    serial: str
    driver_factory: Callable[[], BaseDriver]
    driver: Optional[BaseDriver] = field(default=None, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    in_use: bool = False
    last_used: float = 0.0
    error_count: int = 0

    def acquire(self) -> BaseDriver:
        """Ensure driver is connected and mark as in use."""
        if self.driver is None or not self.driver.is_connected:
            logger.info("Connecting driver for device %s", self.serial)
            self.driver = self.driver_factory()
            self.driver.connect()
        self.in_use = True
        self.last_used = time.time()
        return self.driver

    def release(self) -> None:
        self.in_use = False

    def reset(self) -> None:
        """Disconnect and clear driver (force reconnect on next acquire)."""
        if self.driver:
            try:
                self.driver.disconnect()
            except Exception as exc:
                logger.warning("Error disconnecting %s: %s", self.serial, exc)
            self.driver = None
        self.in_use = False
        self.error_count = 0


class DeviceManager:
    """
    Thread-safe device pool for a single executor node.

    Example::

        dm = DeviceManager()
        dm.register("emulator-5554", lambda: AppiumDriver(...))

        with dm.acquire("emulator-5554") as driver:
            driver.tap("accessibility_id", "login_btn")
    """

    def __init__(self, max_devices: int = 8):
        self._max_devices = max_devices
        self._devices: Dict[str, DeviceEntry] = {}
        self._pool_lock = threading.Lock()

    def register(
        self,
        serial: str,
        driver_factory: Callable[[], BaseDriver],
    ) -> None:
        """Register a device with a factory function that creates its driver."""
        with self._pool_lock:
            if serial in self._devices:
                logger.warning("Device %s already registered; replacing factory.", serial)
                self._devices[serial].reset()
            self._devices[serial] = DeviceEntry(
                serial=serial,
                driver_factory=driver_factory,
            )
            logger.info("Registered device %s (pool size: %d)", serial, len(self._devices))

    def unregister(self, serial: str) -> None:
        """Disconnect and remove a device from the pool."""
        with self._pool_lock:
            entry = self._devices.pop(serial, None)
            if entry:
                entry.reset()
                logger.info("Unregistered device %s", serial)

    @contextmanager
    def acquire(self, serial: str, timeout: float = 30.0) -> Generator[BaseDriver, None, None]:
        """
        Context manager: acquire exclusive access to a device's driver.

        Raises:
            KeyError: Device not registered.
            TimeoutError: Device is busy and timeout elapsed.
            RuntimeError: Driver connect failed.
        """
        entry = self._devices.get(serial)
        if entry is None:
            raise KeyError(f"Device '{serial}' not registered in DeviceManager")

        deadline = time.time() + timeout
        acquired = False
        while time.time() < deadline:
            acquired = entry.lock.acquire(blocking=False)
            if acquired:
                break
            time.sleep(0.5)

        if not acquired:
            raise TimeoutError(
                f"Device '{serial}' is busy. Could not acquire within {timeout}s."
            )

        try:
            driver = entry.acquire()
            yield driver
        except Exception as exc:
            entry.error_count += 1
            logger.error(
                "Error on device %s (error_count=%d): %s",
                serial,
                entry.error_count,
                exc,
            )
            if entry.error_count >= 3:
                logger.warning("Device %s has 3+ errors; resetting driver.", serial)
                entry.reset()
            raise
        finally:
            entry.release()
            entry.lock.release()

    def disconnect_all(self) -> None:
        """Gracefully disconnect all devices."""
        with self._pool_lock:
            for entry in self._devices.values():
                entry.reset()
            logger.info("All devices disconnected.")

    def status(self) -> Dict[str, Any]:
        """Return a status snapshot of all devices."""
        return {
            serial: {
                "in_use": entry.in_use,
                "connected": entry.driver.is_connected if entry.driver else False,
                "error_count": entry.error_count,
                "last_used": entry.last_used,
            }
            for serial, entry in self._devices.items()
        }

    def __repr__(self) -> str:
        return f"DeviceManager(devices={list(self._devices.keys())})"
