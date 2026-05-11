"""
Central Server: FastAPI application that acts as the orchestration hub.

Endpoints:
  POST /nodes/register         - executor nodes announce themselves
  GET  /nodes                  - list registered nodes
  GET  /tasks/pending/{node}   - executor fetches its pending tasks
  POST /results                - executor posts task results
  POST /failures               - executor posts failure reports (+ screenshot)
  POST /upload/batch/{batch_id}- executor posts batch failure artifacts
  POST /generate/aw            - trigger AW code generation
  GET  /health                 - health check
  GET  /video/{node}/{task}    - Phase 1 stub for video pull-through
  WS   /ws/{node_id}           - executor control plane

Self-healing pipeline:
  batch_done websocket event → AgentServer skill loop → script_update websocket push

Fallback pipeline (kept for compatibility):
  POST /failures → FailureAggregator → ScreenshotAnalyzer → FixCommitter
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import uvicorn
from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from uiAutoAgent.central.agent_server import AgentServer
from uiAutoAgent.central.artifact_store import ArtifactStore
from uiAutoAgent.central.code_generator.aw_generator import AWGenerator
from uiAutoAgent.central.healer.failure_aggregator import FailureAggregator, FailureReport
from uiAutoAgent.central.healer.fix_committer import FixCommitter
from uiAutoAgent.central.healer.screenshot_analyzer import ScreenshotAnalyzer
from uiAutoAgent.central.ws_manager import WSManager
from uiAutoAgent.core import get_settings, setup_logging

logger = setup_logging("CentralServer")
settings = get_settings()
_ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(
    title="uiAutoAgent Central Server",
    description="Orchestration hub for Android UI automation",
    version="1.0.0",
)

_nodes: Dict[str, Dict[str, Any]] = {}
_task_queue: Dict[str, List[Dict[str, Any]]] = {}
_results: List[Dict[str, Any]] = []
_results_lock = threading.Lock()
_validation_events: List[Dict[str, Any]] = []

_healer_cfg = settings["central"]["healer"]
_agent_cfg = settings.get("central", {}).get("agent_server", {})
_artifact_cfg = settings.get("central", {}).get("artifact_store", {})
_aggregator = FailureAggregator(
    threshold=_healer_cfg.get("aggregation_threshold", 2),
    persist_dir=_healer_cfg.get("failure_report_dir", "/tmp/uiAutoAgent/failures"),
)
_analyzer = ScreenshotAnalyzer()
_committer = FixCommitter()
_artifact_store = ArtifactStore(
    base_dir=_artifact_cfg.get("base_dir", "/tmp/uiAutoAgent/artifacts")
)
_ws_manager = WSManager()
_agent_server = AgentServer(
    artifact_store=_artifact_store,
    project_root=str(_ROOT),
)


def _on_threshold_reached(fingerprint: str, reports: List[FailureReport]) -> None:
    """
    Legacy healing pipeline kept as a compatibility fallback for /failures.
    Runs in a background thread and is separate from the new agent server flow.
    """

    def _heal() -> None:
        logger.info(
            "Starting fallback self-healing for fingerprint=%s (%d reports)",
            fingerprint,
            len(reports),
        )
        primary = next((r for r in reports if r.screenshot_path), reports[0])
        parts = primary.task_id.replace("-", "_").split("_")
        page = parts[1] if len(parts) > 1 else "unknown"
        element = "unknown"
        analysis = _analyzer.analyze(
            screenshot_path=primary.screenshot_path or "",
            error=primary.error,
            task_id=primary.task_id,
            page=page,
            element=element,
        )
        if analysis.get("confidence", 0) >= 0.6 and analysis.get("proposed_strategies"):
            success = _committer.apply_fix(
                fingerprint=fingerprint,
                page=page,
                element=analysis.get("affected_element", element),
                proposed_strategies=analysis["proposed_strategies"],
                diagnosis=analysis["diagnosis"],
            )
            if success:
                _aggregator.mark_healed(fingerprint)
                logger.info("Fallback self-healing complete for fp=%s", fingerprint)
            else:
                logger.warning("Fallback fix commit failed for fp=%s", fingerprint)
        else:
            logger.warning(
                "Fallback LLM confidence too low (%.2f) or no strategies proposed for fp=%s",
                analysis.get("confidence", 0),
                fingerprint,
            )

    threading.Thread(target=_heal, daemon=True).start()


_aggregator.on_threshold_reached(_on_threshold_reached)


class NodeRegistration(BaseModel):
    node_id: str
    status: str = "online"
    metadata: Dict[str, Any] = {}


class TaskResult(BaseModel):
    task_id: str
    node_id: str
    device_serial: str
    success: bool
    error: Optional[str] = None
    screenshot_path: Optional[str] = None
    duration: float = 0.0
    extra: Dict[str, Any] = {}


class FailurePayload(BaseModel):
    task_id: str
    node_id: str
    device_serial: str
    error: str
    screenshot_path: Optional[str] = None
    extra: Dict[str, Any] = {}


class GenerateAWRequest(BaseModel):
    page: str
    class_name: str
    operations: List[str]
    app_version: str = "1.0"
    output_subdir: str = "examples"


class DispatchTaskRequest(BaseModel):
    task_id: str
    aw_class: str
    method: str
    params: Dict[str, Any] = {}
    device_serial: str
    node_ids: List[str]


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "nodes_online": len(_nodes),
        "pending_tasks": sum(len(q) for q in _task_queue.values()),
        "timestamp": time.time(),
    }


@app.post("/nodes/register")
def register_node(reg: NodeRegistration) -> Dict[str, Any]:
    _nodes[reg.node_id] = {
        "node_id": reg.node_id,
        "status": reg.status,
        "registered_at": time.time(),
        "last_seen": time.time(),
        "metadata": reg.metadata,
    }
    logger.info("Node registered: %s", reg.node_id)
    return {"status": "registered", "node_id": reg.node_id}


@app.get("/nodes")
def list_nodes() -> Dict[str, Any]:
    return {"nodes": list(_nodes.values())}


@app.post("/tasks/dispatch")
def dispatch_task(req: DispatchTaskRequest) -> Dict[str, Any]:
    task = {
        "id": req.task_id,
        "aw_class": req.aw_class,
        "method": req.method,
        "params": req.params,
        "device_serial": req.device_serial,
        "dispatched_at": time.time(),
    }
    dispatched_to = []
    for node_id in req.node_ids:
        _task_queue.setdefault(node_id, []).append(task)
        dispatched_to.append(node_id)
    logger.info("Task %s dispatched to nodes: %s", req.task_id, dispatched_to)
    return {"status": "dispatched", "nodes": dispatched_to}


@app.get("/tasks/pending/{node_id}")
def get_pending_tasks(node_id: str) -> Dict[str, Any]:
    if node_id in _nodes:
        _nodes[node_id]["last_seen"] = time.time()
    tasks = _task_queue.pop(node_id, [])
    return {"node_id": node_id, "tasks": tasks}


@app.post("/results")
def post_result(result: TaskResult) -> Dict[str, Any]:
    with _results_lock:
        _results.append(result.model_dump())
    logger.info(
        "Result: task=%s node=%s success=%s duration=%.1fs",
        result.task_id,
        result.node_id,
        result.success,
        result.duration,
    )
    return {"status": "recorded"}


@app.get("/results")
def get_results(
    task_id: Optional[str] = None,
    node_id: Optional[str] = None,
    success: Optional[bool] = None,
) -> Dict[str, Any]:
    with _results_lock:
        filtered = list(_results)
    if task_id:
        filtered = [r for r in filtered if r["task_id"] == task_id]
    if node_id:
        filtered = [r for r in filtered if r["node_id"] == node_id]
    if success is not None:
        filtered = [r for r in filtered if r["success"] == success]
    return {"results": filtered, "total": len(filtered)}


@app.post("/failures")
async def post_failure(
    background_tasks: BackgroundTasks,
    payload: Optional[str] = Form(default=None),
    screenshot: Optional[UploadFile] = File(default=None),
    task_id: Optional[str] = Form(default=None),
    node_id: Optional[str] = Form(default=None),
    device_serial: Optional[str] = Form(default=None),
    error: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    screenshot_path: Optional[str] = None
    if payload:
        data = json.loads(payload)
    else:
        data = {
            "task_id": task_id or "",
            "node_id": node_id or "",
            "device_serial": device_serial or "",
            "error": error or "",
        }

    if screenshot:
        save_dir = Path(_healer_cfg.get("screenshot_dir", "/tmp/uiAutoAgent/screenshots"))
        save_dir.mkdir(parents=True, exist_ok=True)
        safe_task_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", data.get("task_id", "unknown"))[:64]
        suffix = Path(screenshot.filename or "").suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg"}:
            suffix = ".png"
        filename = f"{safe_task_id}_{int(time.time())}{suffix}"
        screenshot_path = str(save_dir / filename)
        content = await screenshot.read()
        Path(screenshot_path).write_bytes(content)
        logger.info("Screenshot saved: %s", screenshot_path)

    report = FailureReport(
        task_id=data.get("task_id", ""),
        node_id=data.get("node_id", ""),
        device_serial=data.get("device_serial", ""),
        error=data.get("error", ""),
        screenshot_path=screenshot_path or data.get("screenshot_path"),
        extra=data.get("extra", {}),
    )
    _aggregator.record(report)
    return {"status": "recorded", "fingerprint": report.fingerprint()}


@app.get("/failures/groups")
def get_failure_groups() -> Dict[str, Any]:
    groups = _aggregator.get_groups()
    return {"groups": {fp: [r.to_dict() for r in reports] for fp, reports in groups.items()}}


@app.post("/upload/batch/{batch_id}")
async def upload_batch_artifacts(
    batch_id: str,
    node_id: str = Form(...),
    task_results: str = Form(...),
    video_flags: str = Form(default="{}"),
    logs: List[UploadFile] = File(default=[]),
    screenshots: List[UploadFile] = File(default=[]),
) -> Dict[str, Any]:
    try:
        task_results_data = json.loads(task_results)
        video_flags_data = json.loads(video_flags or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

    batch_dir = _artifact_store.get_batch_dir(batch_id)
    log_dir = batch_dir / "logs"
    screenshot_dir = batch_dir / "screenshots"
    log_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    log_paths: Dict[str, str] = {}
    screenshot_paths: Dict[str, List[str]] = {}

    for upload in logs or []:
        task_key, original_name = _extract_task_id(upload.filename or "log.txt")
        save_path = log_dir / f"{task_key}_{_sanitize_filename(original_name)}"
        await _stream_upload_to_disk(upload, save_path)
        log_paths[task_key] = str(save_path)

    for upload in screenshots or []:
        task_key, original_name = _extract_task_id(upload.filename or "screenshot.png")
        save_path = screenshot_dir / f"{task_key}_{_sanitize_filename(original_name)}"
        await _stream_upload_to_disk(upload, save_path)
        screenshot_paths.setdefault(task_key, []).append(str(save_path))

    await _artifact_store.register_batch(
        batch_id=batch_id,
        node_id=node_id,
        task_results=task_results_data,
        log_paths=log_paths,
        screenshot_paths=screenshot_paths,
        video_flags=video_flags_data,
    )
    return {"status": "uploaded", "batch_id": batch_id}


@app.get("/video/{node_id}/{task_id}")
async def get_video(node_id: str, task_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={
            "status": "not_implemented",
            "node_id": node_id,
            "task_id": task_id,
        },
    )


@app.websocket("/ws/{node_id}")
async def executor_ws(websocket: WebSocket, node_id: str):
    await _ws_manager.connect(node_id, websocket)
    try:
        async for msg in websocket.iter_json():
            if msg["type"] == "batch_done":
                batch_id = msg["batch_id"]
                asyncio.create_task(_git_pull_and_heal(node_id, batch_id, msg.get("summary", {})))
            elif msg["type"] == "validation_result":
                logger.info("Validation result from %s: %s", node_id, msg)
                _validation_events.append(msg)
    except WebSocketDisconnect:
        _ws_manager.disconnect(node_id)


async def _git_pull_and_heal(node_id: str, batch_id: str, batch_summary: dict):
    pull_result = await asyncio.to_thread(
        subprocess.run,
        ["git", "pull", "--ff-only"],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
    )
    if pull_result.returncode != 0:
        logger.warning("git pull failed before healing batch %s: %s", batch_id, pull_result.stderr.strip())

    result = await _agent_server.heal(batch_id, batch_summary)
    if result.success and result.fixed_files:
        for file_path in result.fixed_files:
            resolved_path = _ROOT / file_path
            fixed_code = resolved_path.read_text(encoding="utf-8")
            await _ws_manager.send(
                node_id,
                {
                    "type": "script_update",
                    "batch_id": batch_id,
                    "file_path": file_path,
                    "fixed_code": fixed_code,
                    "rerun_task_ids": result.affected_task_ids,
                },
            )
    else:
        await _ws_manager.send(
            node_id,
            {
                "type": "heal_failed",
                "batch_id": batch_id,
                "reason": result.summary,
            },
        )


@app.post("/generate/aw")
def generate_aw(req: GenerateAWRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    def _generate() -> None:
        gen = AWGenerator()
        path = gen.generate_and_write(
            page=req.page,
            class_name=req.class_name,
            operations=req.operations,
            app_version=req.app_version,
            output_subdir=req.output_subdir,
        )
        logger.info("Generated AW: %s", path)

    background_tasks.add_task(_generate)
    return {
        "status": "generation_started",
        "output_path": f"aw/{req.output_subdir}/{req.page}_aw.py",
    }


async def _stream_upload_to_disk(upload: UploadFile, destination: Path) -> None:
    async with aiofiles.open(destination, "wb") as output:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            await output.write(chunk)
    await upload.close()


def _extract_task_id(filename: str) -> Tuple[str, str]:
    if "__" not in filename:
        return "unknown", filename
    task_id, original_name = filename.split("__", 1)
    return _sanitize_filename(task_id), original_name


def _sanitize_filename(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._\-]", "_", name)


def start(host: str = "0.0.0.0", port: int = 8000) -> None:
    logger.info("Starting central server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    start()
