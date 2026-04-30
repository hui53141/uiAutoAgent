"""
Base driver interface shared by all automation drivers.
"""

from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional


class BaseDriver(abc.ABC):
    """
    Abstract base class for all automation drivers.

    Subclasses implement:
      - Appium (UI automation)
      - Maestro (UI automation alternative)
      - Hardware (Paramiko SSH + Serial relay control)
    """

    def __init__(self, device_serial: str, config: Optional[Dict[str, Any]] = None):
        self.device_serial = device_serial
        self.config = config or {}
        self._connected = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def connect(self) -> None:
        """Establish connection / start session."""

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Tear down connection / session."""

    def __enter__(self) -> "BaseDriver":
        self.connect()
        return self

    def __exit__(self, *_: Any) -> None:
        self.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # UI primitives (optional override)
    # ------------------------------------------------------------------

    def find_element(self, strategy: str, value: str) -> Any:
        raise NotImplementedError(f"{self.__class__.__name__} does not support find_element")

    def tap(self, strategy: str, value: str) -> None:
        raise NotImplementedError(f"{self.__class__.__name__} does not support tap")

    def type_text(self, strategy: str, value: str, text: str) -> None:
        raise NotImplementedError(f"{self.__class__.__name__} does not support type_text")

    def screenshot(self, path: str) -> str:
        raise NotImplementedError(f"{self.__class__.__name__} does not support screenshot")

    def get_text(self, strategy: str, value: str) -> str:
        raise NotImplementedError(f"{self.__class__.__name__} does not support get_text")

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int,
              duration: int = 500) -> None:
        raise NotImplementedError(f"{self.__class__.__name__} does not support swipe")

    def wait_for_element(self, strategy: str, value: str, timeout: int = 10) -> Any:
        raise NotImplementedError(f"{self.__class__.__name__} does not support wait_for_element")

    def is_element_visible(self, strategy: str, value: str) -> bool:
        raise NotImplementedError(f"{self.__class__.__name__} does not support is_element_visible")

    # ------------------------------------------------------------------
    # CLI primitives (optional override)
    # ------------------------------------------------------------------

    def run_command(self, command: str, timeout: int = 30) -> str:
        raise NotImplementedError(f"{self.__class__.__name__} does not support run_command")

    def push_file(self, local_path: str, remote_path: str) -> None:
        raise NotImplementedError(f"{self.__class__.__name__} does not support push_file")

    def pull_file(self, remote_path: str, local_path: str) -> None:
        raise NotImplementedError(f"{self.__class__.__name__} does not support pull_file")

    # ------------------------------------------------------------------
    # Hardware primitives (optional override)
    # ------------------------------------------------------------------

    def relay_set(self, channel: int, state: bool) -> None:
        raise NotImplementedError(f"{self.__class__.__name__} does not support relay_set")

    def power_cycle(self, channel: int, delay: float = 2.0) -> None:
        """Power-cycle a relay channel (off → wait → on)."""
        self.relay_set(channel, False)
        import time
        time.sleep(delay)
        self.relay_set(channel, True)

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(serial={self.device_serial!r}, connected={self._connected})"
