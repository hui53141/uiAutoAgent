from __future__ import annotations

import io
import json
from pathlib import Path

from fastapi.testclient import TestClient

from uiAutoAgent.central import server as central_server
from uiAutoAgent.central.artifact_store import ArtifactStore


def test_upload_batch_endpoint_stores_artifacts(tmp_path: Path):
    central_server._artifact_store = ArtifactStore(str(tmp_path / "artifacts"))
    client = TestClient(central_server.app)

    response = client.post(
        "/upload/batch/batch-smoke",
        data={
            "node_id": "node-1",
            "task_results": json.dumps(
                [
                    {
                        "task_id": "task-1",
                        "aw_class": "aw.examples.login_aw.LoginAW",
                        "aw_method": "run_smoke",
                        "error": "NoSuchElementException: login button not found",
                    }
                ]
            ),
            "video_flags": json.dumps({"task-1": True}),
        },
        files=[
            ("logs", ("task-1__task.log", io.BytesIO(b"traceback line"), "text/plain")),
            ("screenshots", ("task-1__shot.png", io.BytesIO(b"PNG"), "image/png")),
        ],
    )

    assert response.status_code == 200
    assert response.json() == {"status": "uploaded", "batch_id": "batch-smoke"}

    metadata = central_server._artifact_store.get_batch_dir("batch-smoke") / "metadata.json"
    assert metadata.exists()
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["failed_cases"][0]["task_id"] == "task-1"
    assert payload["failed_cases"][0]["video_available"] is True
