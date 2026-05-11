"""
Executor Agent: main loop for each remote PC node.

Responsibilities:
  1. Periodically sync with GitHub (git pull)
  2. Poll the central server for task assignments
  3. Execute assigned tasks using the DeviceManager
  4. Report results back to central
  5. Upload batch failure artifacts and listen for script updates over WebSocket
"""

from __future__ import annotations

import asyncio
import importlib
import logging
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

from uiAutoAgent.core import get_settings, setup_logging
from uiAutoAgent.executor.artifact_uploader import ArtifactUploader
from uiAutoAgent.executor.batch_collector import BatchCollector, CaseFailure
from uiAutoAgent.executor.device_manager import DeviceManager
from uiAutoAgent.executor.hot_patcher import HotPatcher
from uiAutoAgent.executor.ws_client import WSClient

logger = setup_logging("ExecutorAgent")


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


class ExecutorAgent:
    """One ExecutorAgent runs on each remote PC."""

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
        self._repo_root = Path(__file__).resolve().parent.parent
        self._log_dir = Path("/tmp/uiAutoAgent/logs")
        self._task_index: Dict[str, Dict[str, Any]] = {}
        self._async_loop = asyncio.new_event_loop()
        self._async_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.uploader = ArtifactUploader(self.central_url, self.node_id)
        self.hot_patcher = HotPatcher(self, repo_root=str(self._repo_root))
        self.ws_client = WSClient(self.central_url, self.node_id, self.hot_patcher)

        self.workspace.mkdir(parents=True, exist_ok=True)
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        logger.info("ExecutorAgent starting on node '%s'", self.node_id)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        self._register_node()
        self._async_thread.start()
        asyncio.run_coroutine_threadsafe(self.ws_client.connect_and_listen(), self._async_loop)

        sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        sync_thread.start()
        poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        poll_thread.start()

        logger.info("ExecutorAgent running. Press Ctrl-C to stop.")
        while not self._stop_event.is_set():
            time.sleep(1)

        self._shutdown()

    def _run_async_loop(self) -> None:
        asyncio.set_event_loop(self._async_loop)
        self._async_loop.run_forever()

    def _handle_shutdown(self, *_: Any) -> None:
        logger.info("Shutdown signal received.")
        self._stop_event.set()

    def _shutdown(self) -> None:
        for thread in self._active_threads:
            thread.join(timeout=10)
        self.device_manager.disconnect_all()
        self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        logger.info("ExecutorAgent stopped.")

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
            cwd=str(self._repo_root),
        )
        if result.returncode == 0:
            if "Already up to date" not in result.stdout:
                logger.info("Git pull: %s", result.stdout.strip())
        else:
            logger.warning("git pull failed: %s", result.stderr.strip())

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                tasks = self._fetch_pending_tasks()
                if tasks:
                    self._run_batch(tasks)
            except Exception as exc:
                logger.debug("Poll error: %s", exc)
            self._stop_event.wait(5)

    def _fetch_pending_tasks(self) -> List[Dict[str, Any]]:
        url = f"{self.central_url}/tasks/pending/{self.node_id}"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json().get("tasks", [])
        except requests.RequestException:
            return []

    def _run_batch(self, tasks: List[Dict[str, Any]]) -> None:
        batch_id = f"batch-{int(time.time() * 1000)}"
        collector = BatchCollector(batch_id=batch_id, log_dir=str(self._log_dir))
        results: List[TaskResult] = []
        results_lock = threading.Lock()
        batch_threads: List[threading.Thread] = []

        for task in tasks:
            self._task_index[task["id"]] = task
            thread = threading.Thread(
                target=self._run_task_in_batch,
                args=(task, collector, results, results_lock),
                daemon=True,
            )
            self._active_threads.append(thread)
            batch_threads.append(thread)
            thread.start()

        for thread in batch_threads:
            thread.join()

        future = asyncio.run_coroutine_threadsafe(
            self._upload_and_notify(collector, results),
            self._async_loop,
        )
        future.result(timeout=300)

    def _run_task_in_batch(
        self,
        task: Dict[str, Any],
        collector: BatchCollector,
        results: List[TaskResult],
        results_lock: threading.Lock,
    ) -> None:
        result = self._run_task(task, collector=collector, report_result=True, fresh_import=False)
        with results_lock:
            results.append(result)

    def _run_task(
        self,
        task: Dict[str, Any],
        collector: Optional[BatchCollector] = None,
        report_result: bool = True,
        fresh_import: bool = False,
    ) -> TaskResult:
        task_id = task["id"]
        serial = task.get("device_serial", "unknown")
        logger.info("Starting task %s on device %s", task_id, serial)

        start = time.time()
        root_logger, handler, log_path = self._attach_task_log_handler(task_id)
        result = TaskResult(
            task_id=task_id,
            node_id=self.node_id,
            device_serial=serial,
            success=False,
            extra={
                "aw_class": task.get("aw_class", ""),
                "aw_method": task.get("method", "run"),
            },
        )

        try:
            with self._task_semaphore:
                aw_instance = self._instantiate_aw(task, serial, fresh_import=fresh_import)
                method_name = task.get("method", "run")
                params = task.get("params", {})
                method: Callable = getattr(aw_instance, method_name)
                method(**params)
                result.success = True
        except Exception as exc:
            result.error = traceback.format_exc()
            logger.error("Task %s failed on %s: %s", task_id, serial, exc)
            screenshot_path = self._take_failure_screenshot(serial)
            result.screenshot_path = screenshot_path
            if collector is not None:
                collector.record_failure(
                    CaseFailure(
                        task_id=task_id,
                        aw_class=task.get("aw_class", ""),
                        aw_method=task.get("method", "run"),
                        error=result.error or str(exc),
                        log_path=log_path,
                        screenshot_paths=[screenshot_path] if screenshot_path else [],
                        video_path=None,
                    )
                )
        finally:
            handler.flush()
            root_logger.removeHandler(handler)
            handler.close()
            result.duration = time.time() - start
            if report_result:
                self._report_result(result)
        return result

    def _instantiate_aw(self, task: Dict[str, Any], serial: str, fresh_import: bool = False) -> Any:
        aw_class_path: str = task["aw_class"]
        module_path, class_name = aw_class_path.rsplit(".", 1)
        if fresh_import and module_path in sys.modules:
            del sys.modules[module_path]
            importlib.invalidate_caches()
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cls(device_manager=self.device_manager, device_serial=serial)

    def _attach_task_log_handler(self, task_id: str) -> tuple[logging.Logger, logging.Handler, str]:
        log_path = self._log_dir / f"{task_id}.log"
        log_settings = get_settings()["logging"]
        formatter = logging.Formatter(log_settings["format"])
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(formatter)
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, log_settings.get("level", "INFO"), logging.INFO))
        root_logger.addHandler(handler)
        return root_logger, handler, str(log_path)

    def _take_failure_screenshot(self, serial: str) -> Optional[str]:
        try:
            out_dir = Path("/tmp/uiAutoAgent/screenshots")
            out_dir.mkdir(parents=True, exist_ok=True)
            path = str(out_dir / f"{serial}_{int(time.time())}.png")
            with open(path, "wb") as stdout_file:
                subprocess.run(
                    ["adb", "-s", serial, "exec-out", "screencap", "-p"],
                    stdout=stdout_file,
                    timeout=10,
                    check=True,
                )
            return path
        except Exception as exc:
            logger.warning("Failed to capture screenshot for %s: %s", serial, exc)
            return None

    async def _upload_and_notify(self, collector: BatchCollector, results: List[TaskResult]) -> None:
        summary = {
            "total_tasks": len(results),
            "failed_tasks": [result.task_id for result in results if not result.success],
            "failed_count": sum(1 for result in results if not result.success),
        }
        if collector.has_failures():
            uploaded = await self.uploader.upload_batch(collector)
            if not uploaded:
                logger.warning("Batch artifact upload failed for %s", collector.batch_id)
        await self.ws_client.send(
            {
                "type": "batch_done",
                "batch_id": collector.batch_id,
                "summary": summary,
            }
        )

    async def rerun_tasks(self, task_ids: List[str]) -> List[TaskResult]:
        return await asyncio.to_thread(self._rerun_tasks_sync, task_ids)

    def _rerun_tasks_sync(self, task_ids: List[str]) -> List[TaskResult]:
        results: List[TaskResult] = []
        for task_id in task_ids:
            task = self._task_index.get(task_id)
            if task is None:
                results.append(
                    TaskResult(
                        task_id=task_id,
                        node_id=self.node_id,
                        device_serial="unknown",
                        success=False,
                        error=f"Task not found for rerun: {task_id}",
                    )
                )
                continue
            results.append(
                self._run_task(
                    task,
                    collector=None,
                    report_result=False,
                    fresh_import=True,
                )
            )
        return results

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
