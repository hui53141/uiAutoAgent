"""
Appium driver: wraps Appium Python client for Android UI automation.

Implements three-level locator fallback:
  L1 - accessibility_id (fastest, most stable)
  L2 - id / xpath       (semantically meaningful)
  L3 - image            (visual template, slowest)
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .base_driver import BaseDriver

try:
    from appium import webdriver as appium_webdriver
    from appium.webdriver.common.appiumby import AppiumBy
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        NoSuchElementException,
        TimeoutException,
        WebDriverException,
    )
    APPIUM_AVAILABLE = True
except ImportError:
    APPIUM_AVAILABLE = False

# Strategy name → AppiumBy mapping
_STRATEGY_MAP: Dict[str, Any] = {}
if APPIUM_AVAILABLE:
    _STRATEGY_MAP = {
        "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
        "id": AppiumBy.ID,
        "xpath": AppiumBy.XPATH,
        "class_name": AppiumBy.CLASS_NAME,
        "name": AppiumBy.NAME,
        "image": AppiumBy.IMAGE,
        "android_uiautomator": AppiumBy.ANDROID_UIAUTOMATOR,
    }


class AppiumDriver(BaseDriver):
    """
    Appium-based Android UI driver.

    Usage::

        caps = {
            "platformName": "Android",
            "deviceName": "emulator-5554",
            "appPackage": "com.example.app",
            "appActivity": ".MainActivity",
            "automationName": "UiAutomator2",
        }
        with AppiumDriver(device_serial="emulator-5554", capabilities=caps) as driver:
            driver.tap("accessibility_id", "login_btn")
    """

    def __init__(
        self,
        device_serial: str,
        capabilities: Optional[Dict[str, Any]] = None,
        appium_url: str = "http://localhost:4723",
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(device_serial, config)
        self.capabilities = capabilities or {}
        self.appium_url = appium_url
        self._driver: Optional[Any] = None

        if not APPIUM_AVAILABLE:
            raise ImportError(
                "Appium Python client is not installed. "
                "Run: pip install Appium-Python-Client"
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Start an Appium session."""
        from appium.options import UiAutomator2Options
        options = UiAutomator2Options()
        options.load_capabilities(self.capabilities)
        self._driver = appium_webdriver.Remote(self.appium_url, options=options)
        self._connected = True

    def disconnect(self) -> None:
        """Quit the Appium session."""
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            finally:
                self._driver = None
        self._connected = False

    # ------------------------------------------------------------------
    # Element interaction with fallback
    # ------------------------------------------------------------------

    def find_element_with_fallback(self, strategies: List[Dict[str, str]]) -> Any:
        """
        Try each strategy in order until one succeeds.
        Raises NoSuchElementException if all fail.
        """
        last_exc: Optional[Exception] = None
        for strat in strategies:
            try:
                return self._find(strat["strategy"], strat["value"])
            except Exception as exc:
                last_exc = exc
                continue
        raise NoSuchElementException(
            f"All {len(strategies)} strategies failed. Last error: {last_exc}"
        )

    def find_element(self, strategy: str, value: str) -> Any:
        return self._find(strategy, value)

    def tap(self, strategy: str, value: str) -> None:
        elem = self._find(strategy, value)
        elem.click()

    def type_text(self, strategy: str, value: str, text: str) -> None:
        elem = self._find(strategy, value)
        elem.clear()
        elem.send_keys(text)

    def get_text(self, strategy: str, value: str) -> str:
        elem = self._find(strategy, value)
        return elem.text

    def screenshot(self, path: str) -> str:
        """Save a screenshot and return the path."""
        self._driver.save_screenshot(path)
        return path

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: int = 500,
    ) -> None:
        from appium.webdriver.common.touch_action import TouchAction
        action = TouchAction(self._driver)
        action.press(x=start_x, y=start_y).wait(duration).move_to(x=end_x, y=end_y).release()
        action.perform()

    def wait_for_element(
        self, strategy: str, value: str, timeout: int = 10
    ) -> Any:
        by = self._resolve_by(strategy)
        try:
            wait = WebDriverWait(self._driver, timeout)
            return wait.until(EC.presence_of_element_located((by, value)))
        except TimeoutException as exc:
            raise TimeoutException(
                f"Element ({strategy}={value!r}) not visible after {timeout}s"
            ) from exc

    def is_element_visible(self, strategy: str, value: str) -> bool:
        try:
            elem = self._find(strategy, value)
            return elem.is_displayed()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # ADB shell passthrough
    # ------------------------------------------------------------------

    def run_command(self, command: str, timeout: int = 30) -> str:
        """Execute an adb shell command via Appium execute_script."""
        result = self._driver.execute_script(
            "mobile: shell",
            {"command": command, "timeout": timeout * 1000},
        )
        return str(result or "")

    # ------------------------------------------------------------------
    # App management helpers
    # ------------------------------------------------------------------

    def get_app_version(self, package: str) -> str:
        """Return the installed version of an Android package."""
        output = self.run_command(
            f"dumpsys package {package} | grep versionName"
        )
        for line in output.splitlines():
            if "versionName" in line:
                return line.split("=")[-1].strip()
        return "0.0"

    def launch_app(self) -> None:
        self._driver.launch_app()

    def close_app(self) -> None:
        self._driver.close_app()

    def reset_app(self) -> None:
        self._driver.reset()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find(self, strategy: str, value: str) -> Any:
        by = self._resolve_by(strategy)
        return self._driver.find_element(by, value)

    @staticmethod
    def _resolve_by(strategy: str) -> Any:
        if not APPIUM_AVAILABLE:
            raise ImportError("Appium not available")
        by = _STRATEGY_MAP.get(strategy)
        if by is None:
            raise ValueError(
                f"Unknown strategy '{strategy}'. "
                f"Valid strategies: {list(_STRATEGY_MAP.keys())}"
            )
        return by
