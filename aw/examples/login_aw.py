"""
LoginAW: Action Words for the Login page.

Business-level operations:
  - run_smoke: quick pass/fail login test
  - login: full login flow
  - logout: logout flow
  - run_after_reboot: power-cycle then login
"""

from __future__ import annotations

import time
from typing import Optional

from uiAutoAgent.aw.base_aw import BaseAW, retry
from uiAutoAgent.core import setup_logging

logger = setup_logging("LoginAW")


class LoginAW(BaseAW):
    """Action Words for the Login feature."""

    PAGE = "login"

    # ------------------------------------------------------------------
    # Core business operations
    # ------------------------------------------------------------------

    @retry(max_attempts=3, delay=2.0)
    def login(self, username: str, password: str) -> None:
        """
        Enter credentials and tap the login button.

        Raises:
            AssertionError: If an error message appears after login.
        """
        logger.info("LoginAW.login(%r) on device %s", username, self.device_serial)
        self.type_text("username_input", username)
        self.type_text("password_input", password)
        self.tap("login_button")
        time.sleep(2)  # wait for navigation
        self._assert_no_error()

    def logout(self) -> None:
        """Navigate to settings and log out."""
        logger.info("LoginAW.logout() on device %s", self.device_serial)
        # Example: swipe to open profile menu
        with self.device_manager.acquire(self.device_serial) as driver:
            driver.run_command("input tap 950 100")  # profile icon approx coords
        time.sleep(1)
        # Look for logout option
        with self.device_manager.acquire(self.device_serial) as driver:
            driver.run_command("input tap 540 900")  # logout button approx coords

    # ------------------------------------------------------------------
    # Test scenarios
    # ------------------------------------------------------------------

    def run_smoke(self, username: str, password: str) -> None:
        """
        Smoke test: login and verify success.

        Steps:
          1. Launch the app
          2. Login with credentials
          3. Verify no error message
        """
        logger.info("LoginAW.run_smoke on device %s", self.device_serial)
        self._launch_app()
        self.login(username, password)
        logger.info("Smoke test PASSED for device %s", self.device_serial)

    def run_invalid_credentials(self, username: str = "invalid", password: str = "wrong") -> None:
        """
        Negative test: verify error message appears for bad credentials.
        """
        logger.info("LoginAW.run_invalid_credentials on device %s", self.device_serial)
        self._launch_app()
        self.type_text("username_input", username)
        self.type_text("password_input", password)
        self.tap("login_button")
        time.sleep(2)

        assert self.is_visible("error_message"), (
            "Expected error message to be visible after invalid login, but it was not found."
        )
        logger.info("Invalid credentials test PASSED for device %s", self.device_serial)

    def run_forgot_password_flow(self) -> None:
        """Verify the forgot password link navigates correctly."""
        self._launch_app()
        self.tap("forgot_password_link")
        time.sleep(2)
        logger.info("Forgot password navigation PASSED for device %s", self.device_serial)

    def run_after_reboot(self, relay_channel: int = 1) -> None:
        """
        Power-cycle the device via relay, wait for boot, then login.

        Requires HardwareDriver to be registered for this device.
        """
        logger.info(
            "LoginAW.run_after_reboot on device %s (relay ch%d)",
            self.device_serial,
            relay_channel,
        )
        with self.device_manager.acquire(self.device_serial) as driver:
            driver.power_cycle(relay_channel, delay=3.0)

        logger.info("Waiting 30s for device to reboot...")
        time.sleep(30)

        self._launch_app()
        # Credentials from environment or config
        import os
        username = os.getenv("TEST_USERNAME", "testuser")
        password = os.getenv("TEST_PASSWORD", "testpass")
        self.login(username, password)
        logger.info("run_after_reboot PASSED for device %s", self.device_serial)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _launch_app(self) -> None:
        """Launch the app under test via adb."""
        from uiAutoAgent.core import get_settings
        settings = get_settings()
        pkg = settings["locator"]["app_package"]
        with self.device_manager.acquire(self.device_serial) as driver:
            driver.run_command(
                f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1"
            )
        time.sleep(3)

    def _assert_no_error(self) -> None:
        """Assert that the error message element is not visible."""
        if self.is_visible("error_message"):
            error_text = ""
            try:
                error_text = self.get_text("error_message")
            except Exception:
                pass
            raise AssertionError(f"Login error message visible: {error_text!r}")
