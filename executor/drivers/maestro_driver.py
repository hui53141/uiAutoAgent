"""
Maestro driver: wraps the Maestro CLI for Android/iOS UI automation.

Maestro uses YAML-based "flows" (test scripts). This driver:
  1. Renders a flow YAML from a template or dict
  2. Invokes `maestro test <flow.yaml>` via subprocess
  3. Parses stdout for pass/fail
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .base_driver import BaseDriver


class MaestroDriver(BaseDriver):
    """
    Maestro CLI-based driver.

    Maestro operates at flow level rather than element level.
    Use `run_flow` for full test flows, or the higher-level helpers.

    Usage::

        with MaestroDriver(device_serial="emulator-5554") as driver:
            driver.run_flow({
                "appId": "com.example.app",
                "---": [
                    {"launchApp": {}},
                    {"tapOn": {"text": "Login"}},
                ]
            })
    """

    def __init__(
        self,
        device_serial: str,
        maestro_path: str = "maestro",
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(device_serial, config)
        self.maestro_path = maestro_path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Verify Maestro CLI is available."""
        result = subprocess.run(
            [self.maestro_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Maestro CLI not available at '{self.maestro_path}'. "
                f"Install: curl -Ls 'https://get.maestro.mobile.dev' | bash"
            )
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    # ------------------------------------------------------------------
    # Flow execution
    # ------------------------------------------------------------------

    def run_flow(self, flow: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
        """
        Execute a Maestro flow dict.

        Args:
            flow: Maestro flow as Python dict.
            timeout: Max seconds to wait for flow completion.

        Returns:
            {"success": bool, "stdout": str, "stderr": str, "returncode": int}
        """
        with tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False, encoding="utf-8"
        ) as tmp:
            yaml.dump(flow, tmp, allow_unicode=True)
            flow_path = tmp.name

        try:
            cmd = [
                self.maestro_path, "test",
                "--device", self.device_serial,
                "--format", "junit",
                flow_path,
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        finally:
            Path(flow_path).unlink(missing_ok=True)

    def run_flow_file(self, flow_path: str, timeout: int = 120) -> Dict[str, Any]:
        """Run a Maestro flow from a YAML file path."""
        cmd = [
            self.maestro_path, "test",
            "--device", self.device_serial,
            flow_path,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------

    def tap_text(self, text: str, app_id: str) -> None:
        self.run_flow({
            "appId": app_id,
            "---": [{"tapOn": {"text": text}}],
        })

    def launch_app(self, app_id: str) -> None:
        self.run_flow({
            "appId": app_id,
            "---": [{"launchApp": {}}],
        })

    def screenshot(self, path: str) -> str:
        """Take a screenshot via adb (Maestro does not expose standalone screenshot)."""
        cmd = ["adb", "-s", self.device_serial, "exec-out", "screencap", "-p"]
        with open(path, "wb") as fh:
            result = subprocess.run(cmd, stdout=fh, timeout=10)
        if result.returncode != 0:
            raise RuntimeError(f"adb screenshot failed for device {self.device_serial}")
        return path

    def run_command(self, command: str, timeout: int = 30) -> str:
        """Run an adb shell command on the device."""
        result = subprocess.run(
            ["adb", "-s", self.device_serial, "shell"] + command.split(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout + result.stderr
