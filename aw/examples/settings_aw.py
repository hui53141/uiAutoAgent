"""
SettingsAW: Action Words for the Settings page.

Business-level operations:
  - toggle_wifi: turn Wi-Fi on/off
  - toggle_bluetooth: turn Bluetooth on/off
  - navigate_to_about: open About Device page
  - run_full_regression: run all settings regression scenarios
"""

from __future__ import annotations

import time

from uiAutoAgent.aw.base_aw import BaseAW, retry
from uiAutoAgent.core import setup_logging

logger = setup_logging("SettingsAW")


class SettingsAW(BaseAW):
    """Action Words for the Settings feature."""

    PAGE = "settings"

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    @retry(max_attempts=2, delay=1.0)
    def toggle_wifi(self, enable: bool) -> None:
        """
        Set the Wi-Fi toggle to the given state.

        Args:
            enable: True to enable, False to disable.
        """
        self._open_settings()
        current_state = self._get_toggle_state("wifi_toggle")
        if current_state != enable:
            self.tap("wifi_toggle")
            time.sleep(1)
        state_str = "enabled" if enable else "disabled"
        logger.info("Wi-Fi %s on device %s", state_str, self.device_serial)

    @retry(max_attempts=2, delay=1.0)
    def toggle_bluetooth(self, enable: bool) -> None:
        """
        Set the Bluetooth toggle to the given state.
        """
        self._open_settings()
        current_state = self._get_toggle_state("bluetooth_toggle")
        if current_state != enable:
            self.tap("bluetooth_toggle")
            time.sleep(1)
        state_str = "enabled" if enable else "disabled"
        logger.info("Bluetooth %s on device %s", state_str, self.device_serial)

    def navigate_to_about(self) -> None:
        """Open the About Device settings page."""
        self._open_settings()
        self.tap("about_button")
        time.sleep(2)
        logger.info("Navigated to About Device on %s", self.device_serial)

    def open_language_settings(self) -> None:
        """Open Language & Input settings."""
        self._open_settings()
        self.tap("language_item")
        time.sleep(2)

    def open_notification_settings(self) -> None:
        """Open Notification settings."""
        self._open_settings()
        self.tap("notification_item")
        time.sleep(2)

    # ------------------------------------------------------------------
    # Test scenarios
    # ------------------------------------------------------------------

    def run_full_regression(self) -> None:
        """
        Run a full regression pass over Settings page elements.

        Covers:
          - Wi-Fi toggle (on/off)
          - Bluetooth toggle (on/off)
          - About Device navigation
          - Language settings navigation
          - Notification settings navigation
        """
        logger.info("SettingsAW.run_full_regression on device %s", self.device_serial)

        self.toggle_wifi(False)
        self.toggle_wifi(True)

        self.toggle_bluetooth(False)
        self.toggle_bluetooth(True)

        self.navigate_to_about()
        self._go_back()

        self.open_language_settings()
        self._go_back()

        self.open_notification_settings()
        self._go_back()

        logger.info("Settings full regression PASSED on device %s", self.device_serial)

    def run_smoke(self) -> None:
        """Quick smoke: verify settings page opens and Wi-Fi toggle is present."""
        self._open_settings()
        assert self.is_visible("wifi_toggle"), (
            "Wi-Fi toggle not visible on Settings page."
        )
        logger.info("Settings smoke PASSED on device %s", self.device_serial)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        """Launch the Settings app via adb intent."""
        with self.device_manager.acquire(self.device_serial) as driver:
            driver.run_command(
                "am start -a android.settings.SETTINGS"
            )
        time.sleep(2)

    def _get_toggle_state(self, element: str) -> bool:
        """Return True if a Switch/Toggle element is currently ON."""
        try:
            strategies = self._get_strategies(element)
            with self.device_manager.acquire(self.device_serial) as driver:
                elem = driver.find_element_with_fallback(strategies)
                # Appium returns "true"/"false" for checked attribute
                return elem.get_attribute("checked") == "true"
        except Exception:
            return False

    def _go_back(self) -> None:
        """Press the Android back button."""
        with self.device_manager.acquire(self.device_serial) as driver:
            driver.run_command("input keyevent 4")
        time.sleep(1)
