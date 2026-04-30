"""
BaseAW: Action Word base class.

All business AW classes inherit from BaseAW. It provides:
  - Automatic locator loading (versioned, with 3-level fallback)
  - Driver access via DeviceManager
  - Retry logic with screenshot on failure
  - Structured logging
"""

from __future__ import annotations

import functools
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

from uiAutoAgent.core import LocatorManager, get_settings, setup_logging
from uiAutoAgent.executor.device_manager import DeviceManager

F = TypeVar("F", bound=Callable[..., Any])

logger = setup_logging("BaseAW")


def retry(
    max_attempts: int = 3,
    delay: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable[[F], F]:
    """
    Decorator: retry a method on failure.

    Usage::

        @retry(max_attempts=3, delay=1.0)
        def tap_login(self):
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    logger.warning(
                        "[%s] attempt %d/%d failed: %s",
                        func.__qualname__,
                        attempt,
                        max_attempts,
                        exc,
                    )
                    if attempt < max_attempts:
                        time.sleep(delay)
            raise last_exc  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


class BaseAW:
    """
    Base class for all Action Words.

    Subclasses declare ``PAGE`` (the locator page name) and use
    ``self.find()``, ``self.tap()``, ``self.type_text()`` which
    automatically apply the three-level fallback.

    Example::

        class LoginAW(BaseAW):
            PAGE = "login"

            def login(self, username, password):
                self.type_text("username_input", username)
                self.type_text("password_input", password)
                self.tap("login_button")
    """

    PAGE: str = ""

    def __init__(
        self,
        device_manager: DeviceManager,
        device_serial: str,
        app_version: Optional[str] = None,
    ):
        self.device_manager = device_manager
        self.device_serial = device_serial
        settings = get_settings()
        self._app_package = settings["locator"]["app_package"]
        self._app_version = app_version or self._detect_app_version()
        self._locator_manager = LocatorManager(self._app_version)
        self.logger = setup_logging(self.__class__.__name__)
        self.logger.info(
            "Initialized %s for device=%s app_version=%s",
            self.__class__.__name__,
            device_serial,
            self._app_version,
        )

    # ------------------------------------------------------------------
    # Locator resolution
    # ------------------------------------------------------------------

    def _get_strategies(self, element: str, page: Optional[str] = None) -> List[Dict[str, str]]:
        """Return all locator strategies for *element* on *page* (or self.PAGE)."""
        return self._locator_manager.get_all_strategies(page or self.PAGE, element)

    def _best_strategy(self, element: str, page: Optional[str] = None) -> Dict[str, str]:
        return self._locator_manager.get(page or self.PAGE, element)

    # ------------------------------------------------------------------
    # Driver-level actions with fallback
    # ------------------------------------------------------------------

    def find(self, element: str, page: Optional[str] = None) -> Any:
        """Find element using fallback strategy chain."""
        strategies = self._get_strategies(element, page)
        with self.device_manager.acquire(self.device_serial) as driver:
            return driver.find_element_with_fallback(strategies)

    def tap(self, element: str, page: Optional[str] = None) -> None:
        """Tap an element (with locator fallback)."""
        strategies = self._get_strategies(element, page)
        with self.device_manager.acquire(self.device_serial) as driver:
            elem = driver.find_element_with_fallback(strategies)
            elem.click()

    def type_text(self, element: str, text: str, page: Optional[str] = None) -> None:
        """Clear and type text into an element."""
        strategies = self._get_strategies(element, page)
        with self.device_manager.acquire(self.device_serial) as driver:
            elem = driver.find_element_with_fallback(strategies)
            elem.clear()
            elem.send_keys(text)

    def get_text(self, element: str, page: Optional[str] = None) -> str:
        """Return visible text of an element."""
        strategies = self._get_strategies(element, page)
        with self.device_manager.acquire(self.device_serial) as driver:
            elem = driver.find_element_with_fallback(strategies)
            return elem.text

    def is_visible(self, element: str, page: Optional[str] = None) -> bool:
        """Return True if element is visible on screen."""
        strategies = self._get_strategies(element, page)
        with self.device_manager.acquire(self.device_serial) as driver:
            for strat in strategies:
                try:
                    if driver.is_element_visible(strat["strategy"], strat["value"]):
                        return True
                except Exception:
                    continue
        return False

    def wait_for(
        self,
        element: str,
        timeout: int = 10,
        page: Optional[str] = None,
    ) -> Any:
        """Wait for element to appear, trying all strategies."""
        best = self._best_strategy(element, page)
        with self.device_manager.acquire(self.device_serial) as driver:
            return driver.wait_for_element(best["strategy"], best["value"], timeout)

    def screenshot(self, path: Optional[str] = None) -> str:
        """Take a screenshot and return the saved path."""
        if path is None:
            out_dir = Path("/tmp/uiAutoAgent/screenshots")
            out_dir.mkdir(parents=True, exist_ok=True)
            path = str(out_dir / f"{self.device_serial}_{int(time.time())}.png")
        with self.device_manager.acquire(self.device_serial) as driver:
            return driver.screenshot(path)

    def run_command(self, command: str, timeout: int = 30) -> str:
        """Run a shell / adb command on the device."""
        with self.device_manager.acquire(self.device_serial) as driver:
            return driver.run_command(command, timeout=timeout)

    # ------------------------------------------------------------------
    # App version detection
    # ------------------------------------------------------------------

    def _detect_app_version(self) -> str:
        """
        Detect installed app version via adb.
        Falls back to "1.0" if detection fails.
        """
        try:
            result = __import__("subprocess").run(
                [
                    "adb", "-s", self.device_serial,
                    "shell", "dumpsys", "package", self._app_package,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.splitlines():
                if "versionName" in line:
                    return line.split("=")[-1].strip()
        except Exception as exc:
            self.logger.debug("App version detection failed: %s", exc)
        return "1.0"
