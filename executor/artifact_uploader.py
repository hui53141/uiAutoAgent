from __future__ import annotations

import json
from pathlib import Path

import aiohttp

from uiAutoAgent.core import setup_logging
from uiAutoAgent.executor.batch_collector import BatchCollector

logger = setup_logging("ArtifactUploader")


class ArtifactUploader:
    """
    Uploads batch failure artifacts to Central via HTTP multipart.
    Videos are NOT uploaded (Strategy C) — only a video_flags dict is sent.
    Uses aiohttp for async streaming upload.
    """

    def __init__(self, central_url: str, node_id: str):
        self.central_url = central_url.rstrip("/")
        self.node_id = node_id

    async def upload_batch(self, collector: BatchCollector) -> bool:
        """
        POST /upload/batch/{batch_id}
        Streams logs and screenshots. Sends video_flags JSON (not video files).
        Returns True on success.
        """
        url = f"{self.central_url}/upload/batch/{collector.batch_id}"
        form = aiohttp.FormData()
        failures = collector.get_failures()
        form.add_field("node_id", self.node_id)
        form.add_field(
            "task_results",
            json.dumps(
                [
                    {
                        "task_id": failure.task_id,
                        "aw_class": failure.aw_class,
                        "aw_method": failure.aw_method,
                        "error": failure.error,
                    }
                    for failure in failures
                ],
                ensure_ascii=False,
            ),
        )
        form.add_field("video_flags", json.dumps(collector.get_video_flags()))

        opened_files = []
        try:
            for failure in failures:
                if failure.log_path and Path(failure.log_path).exists():
                    log_file = open(failure.log_path, "rb")
                    opened_files.append(log_file)
                    form.add_field(
                        "logs[]",
                        log_file,
                        filename=f"{failure.task_id}__{Path(failure.log_path).name}",
                        content_type="text/plain",
                    )
                for screenshot_path in failure.screenshot_paths:
                    if Path(screenshot_path).exists():
                        screenshot_file = open(screenshot_path, "rb")
                        opened_files.append(screenshot_file)
                        content_type = "image/png"
                        if screenshot_path.lower().endswith((".jpg", ".jpeg")):
                            content_type = "image/jpeg"
                        form.add_field(
                            "screenshots[]",
                            screenshot_file,
                            filename=f"{failure.task_id}__{Path(screenshot_path).name}",
                            content_type=content_type,
                        )

            timeout = aiohttp.ClientTimeout(total=180)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=form) as response:
                    if response.status >= 400:
                        logger.warning("Artifact upload failed: %s %s", response.status, await response.text())
                        return False
                    return True
        except Exception as exc:
            logger.warning("Artifact upload failed: %s", exc)
            return False
        finally:
            for opened in opened_files:
                opened.close()
