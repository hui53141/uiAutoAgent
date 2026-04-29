"""
Executor Agent: main loop for each remote PC node.

Responsibilities:
  1. Periodically sync with GitHub (git pull)
  2. Poll the central server for task assignments
  3. Execute assigned tasks using the DeviceManager
  4. Report results (pass/fail + screenshot) back to central
  5. Respect per-device locks for concurrent task execution

Run as:
    python -m scripts.start_executor --node-id node-01
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests
import yaml

from uiAutoAgent.core import get_settings, setup_logging
from uiAutoAgent.executor.device_manager import DeviceManager
from uiAutoAgent.executor.drivers.appium_driver import AppiumDriver

logger = setup_logging("ExecutorAgent")


# ---------------------------------------------------------------------------
# Task result model
# ---------------------------------------------------------------------------

@dataclass
class TaskResult:
    task_id: str
    node_id: str
    device_serial: str
    success: bool
    error: Optional[str] = None
    screenshot_path: Optional[str] = None
    duration: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "node_id": self.node_id,
            "device_serial": self.device_serial,
            "success": self.success,
            "error": self.error,
            "screenshot_path": self.screenshot_path,
            "duration": self.duration,
            "extra": self.extra,
        }


# ---------------------------------------------------------------------------
# Executor Agent
# ---------------------------------------------------------------------------

class ExecutorAgent:
    """
    One ExecutorAgent runs on each remote PC.

    It is stateless with respect to the central server: it pulls tasks,
    executes them, and reports results.
    """

    def __init__(
        self,
        node_id: str,
        central_url: str = "http://localhost:8000",
        workspace: Optional[str] = None,
        sync_interval: int = 60,
    ):
        self.node_id = node_id
        self.central_url = central_url.rstrip("/")
        settings = get_settings()
        self.workspace = Path(workspace or settings["executor"]["workspace"])
        self.sync_interval = sync_interval
        self.device_manager = DeviceManager(
            max_devices=settings["executor"]["device_manager"]["max_devices"]
        )
        self._stop_event = threading.Event()
        self._task_semaphore = threading.Semaphore(
            settings["executor"]["device_manager"]["max_devices"]
        )
        self._active_threads: List[threading.Thread] = []

        # Ensure workspace exists
        self.workspace.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def start(self) -> None:
        logger.info("ExecutorAgent starting on node '%s'", self.node_id)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        self._register_node()

        sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        sync_thread.start()

        poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        poll_thread.start()

        logger.info("ExecutorAgent running. Press Ctrl-C to stop.")
        while not self._stop_event.is_set():
            time.sleep(1)

        self._shutdown()

    def _handle_shutdown(self, *_: Any) -> None:
        logger.info("Shutdown signal received.")
        self._stop_event.set()

    def _shutdown(self) -> None:
        for t in self._active_threads:
            t.join(timeout=10)
        self.device_manager.disconnect_all()
        logger.info("ExecutorAgent stopped.")

    # ------------------------------------------------------------------
    # GitHub sync loop
    # ------------------------------------------------------------------

    def _sync_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._git_pull()
            except Exception as exc:
                logger.warning("Git sync failed: %s", exc)
            self._stop_event.wait(self.sync_interval)

    def _git_pull(self) -> None:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        if result.returncode == 0:
            if "Already up to date" not in result.stdout:
                logger.info("Git pull: %s", result.stdout.strip())
        else:
            logger.warning("git pull failed: %s", result.stderr.strip())

    # ------------------------------------------------------------------
    # Task polling loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Poll central server for pending tasks."""
        while not self._stop_event.is_set():
            try:
                tasks = self._fetch_pending_tasks()
                for task in tasks:
                    self._dispatch_task(task)
            except Exception as exc:
                logger.debug("Poll error: %s", exc)
            self._stop_event.wait(5)

    def _fetch_pending_tasks(self) -> List[Dict[str, Any]]:
        url = f"{self.central_url}/tasks/pending/{self.node_id}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            return resp.json().get("tasks", [])
        except requests.RequestException:
            return []

    def _dispatch_task(self, task: Dict[str, Any]) -> None:
        """Dispatch a task to a background thread (one per device)."""
        serial = task.get("device_serial")
        if not serial:
            logger.warning("Task %s has no device_serial; skipping.", task.get("id"))
            return

        t = threading.Thread(
            target=self._run_task,
            args=(task,),
            daemon=True,
        )
        self._active_threads.append(t)
        t.start()

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    def _run_task(self, task: Dict[str, Any]) -> None:
        task_id = task["id"]
        serial = task.get("device_serial", "unknown")
        logger.info("Starting task %s on device %s", task_id, serial)

        start = time.time()
        result = TaskResult(
            task_id=task_id,
            node_id=self.node_id,
            device_serial=serial,
            success=False,
        )

        try:
            with self._task_semaphore:
                aw_instance = self._instantiate_aw(task, serial)
                method_name = task.get("method", "run")
                params = task.get("params", {})

                method: Callable = getattr(aw_instance, method_name)
                method(**params)
                result.success = True

        except Exception as exc:
            result.error = traceback.format_exc()
            logger.error("Task %s failed on %s: %s", task_id, serial, exc)

            # Save failure screenshot
            screenshot_path = self._take_failure_screenshot(serial)
            result.screenshot_path = screenshot_path
            self._report_failure(result)

        finally:
            result.duration = time.time() - start
            self._report_result(result)

    def _instantiate_aw(self, task: Dict[str, Any], serial: str) -> Any:
        """Dynamically import and instantiate an AW class."""
        aw_class_path: str = task["aw_class"]
        module_path, class_name = aw_class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cls(device_manager=self.device_manager, device_serial=serial)

    def _take_failure_screenshot(self, serial: str) -> Optional[str]:
        """Attempt to capture a screenshot via adb for failure reports."""
        try:
            out_dir = Path("/tmp/uiAutoAgent/screenshots")
            out_dir.mkdir(parents=True, exist_ok=True)
            path = str(out_dir / f"{serial}_{int(time.time())}.png")
            subprocess.run(
                ["adb", "-s", serial, "exec-out", "screencap", "-p"],
                stdout=open(path, "wb"),
                timeout=10,
                check=True,
            )
            return path
        except Exception as exc:
            logger.warning("Failed to capture screenshot for %s: %s", serial, exc)
            return None

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _register_node(self) -> None:
        try:
            url = f"{self.central_url}/nodes/register"
            payload = {"node_id": self.node_id, "status": "online"}
            requests.post(url, json=payload, timeout=5)
        except Exception:
            logger.debug("Could not register with central server (may be offline).")

    def _report_result(self, result: TaskResult) -> None:
        url = f"{self.central_url}/results"
        try:
            requests.post(url, json=result.to_dict(), timeout=10)
        except Exception as exc:
            logger.warning("Could not POST result to central: %s", exc)

    def _report_failure(self, result: TaskResult) -> None:
        url = f"{self.central_url}/failures"
        payload = result.to_dict()
        if result.screenshot_path and Path(result.screenshot_path).exists():
            with open(result.screenshot_path, "rb") as fh:
                try:
                    files = {"screenshot": fh}
                    requests.post(
                        url,
                        data={"payload": json.dumps(payload)},
                        files=files,
                        timeout=30,
                    )
                    return
                except Exception:
                    pass
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as exc:
            logger.warning("Could not POST failure report to central: %s", exc)
