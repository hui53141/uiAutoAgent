"""
Central Server: FastAPI application that acts as the orchestration hub.

Endpoints:
  POST /nodes/register         - executor nodes announce themselves
  GET  /nodes                  - list registered nodes
  GET  /tasks/pending/{node}   - executor fetches its pending tasks
  POST /results                - executor posts task results
  POST /failures               - executor posts failure reports (+ screenshot)
  POST /generate/aw            - trigger AW code generation
  GET  /health                 - health check

Self-healing pipeline:
  POST /failures → FailureAggregator → (threshold crossed) →
    ScreenshotAnalyzer (LLM) → FixCommitter (git commit + push)
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
import yaml
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from uiAutoAgent.central.code_generator.aw_generator import AWGenerator
from uiAutoAgent.central.healer.failure_aggregator import FailureAggregator, FailureReport
from uiAutoAgent.central.healer.fix_committer import FixCommitter
from uiAutoAgent.central.healer.screenshot_analyzer import ScreenshotAnalyzer
from uiAutoAgent.core import get_settings, setup_logging

logger = setup_logging("CentralServer")
settings = get_settings()

app = FastAPI(
    title="uiAutoAgent Central Server",
    description="Orchestration hub for Android UI automation",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# State (in-memory; use Redis/DB for production scale)
# ---------------------------------------------------------------------------

_nodes: Dict[str, Dict[str, Any]] = {}
_task_queue: Dict[str, List[Dict[str, Any]]] = {}  # node_id -> [tasks]
_results: List[Dict[str, Any]] = []
_results_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Self-healing pipeline setup
# ---------------------------------------------------------------------------

_healer_cfg = settings["central"]["healer"]
_aggregator = FailureAggregator(
    threshold=_healer_cfg.get("aggregation_threshold", 2),
    persist_dir=_healer_cfg.get("failure_report_dir", "/tmp/uiAutoAgent/failures"),
)
_analyzer = ScreenshotAnalyzer()
_committer = FixCommitter()


def _on_threshold_reached(fingerprint: str, reports: List[FailureReport]) -> None:
    """
    Healing pipeline: triggered when FailureAggregator reaches threshold.
    Runs in a background thread (not blocking the request handler).
    """
    def _heal() -> None:
        logger.info(
            "Starting self-healing for fingerprint=%s (%d reports)",
            fingerprint,
            len(reports),
        )
        # Use the first report that has a screenshot
        primary = next((r for r in reports if r.screenshot_path), reports[0])

        # Infer page from task_id (convention: "task-{page}-..." or just use "unknown")
        parts = primary.task_id.replace("-", "_").split("_")
        page = parts[1] if len(parts) > 1 else "unknown"
        element = "unknown"

        # Analyze screenshot with LLM
        analysis = _analyzer.analyze(
            screenshot_path=primary.screenshot_path or "",
            error=primary.error,
            task_id=primary.task_id,
            page=page,
            element=element,
        )
        logger.info(
            "LLM diagnosis (fp=%s): %s (confidence=%.2f)",
            fingerprint,
            analysis.get("diagnosis"),
            analysis.get("confidence", 0),
        )

        # Apply fix if LLM is confident
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
                logger.info("Self-healing complete for fp=%s", fingerprint)
            else:
                logger.warning("Fix commit failed for fp=%s", fingerprint)
        else:
            logger.warning(
                "LLM confidence too low (%.2f) or no strategies proposed for fp=%s",
                analysis.get("confidence", 0),
                fingerprint,
            )

    t = threading.Thread(target=_heal, daemon=True)
    t.start()


_aggregator.on_threshold_reached(_on_threshold_reached)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


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

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "nodes_online": len(_nodes),
        "pending_tasks": sum(len(q) for q in _task_queue.values()),
        "timestamp": time.time(),
    }


# ------ Node management ------

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


# ------ Task management ------

@app.post("/tasks/dispatch")
def dispatch_task(req: DispatchTaskRequest) -> Dict[str, Any]:
    """Push a task into the queue for specified nodes."""
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
    """Executor nodes poll this endpoint to get their pending tasks."""
    if node_id in _nodes:
        _nodes[node_id]["last_seen"] = time.time()

    tasks = _task_queue.pop(node_id, [])
    return {"node_id": node_id, "tasks": tasks}


# ------ Results ------

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


# ------ Failure handling & self-healing ------

@app.post("/failures")
async def post_failure(
    background_tasks: BackgroundTasks,
    payload: Optional[str] = Form(default=None),
    screenshot: Optional[UploadFile] = File(default=None),
    # Accept direct JSON body too
    task_id: Optional[str] = Form(default=None),
    node_id: Optional[str] = Form(default=None),
    device_serial: Optional[str] = Form(default=None),
    error: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """
    Receive failure reports from executor nodes.
    Supports multipart (with screenshot) or plain JSON.
    """
    screenshot_path: Optional[str] = None

    # Parse payload
    if payload:
        data = json.loads(payload)
    else:
        data = {
            "task_id": task_id or "",
            "node_id": node_id or "",
            "device_serial": device_serial or "",
            "error": error or "",
        }

    # Save screenshot if provided
    if screenshot:
        save_dir = Path(_healer_cfg.get("screenshot_dir", "/tmp/uiAutoAgent/screenshots"))
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{data.get('task_id', 'unknown')}_{int(time.time())}.png"
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
    return {
        "groups": {
            fp: [r.to_dict() for r in reports]
            for fp, reports in groups.items()
        }
    }


# ------ Code generation ------

@app.post("/generate/aw")
def generate_aw(req: GenerateAWRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """
    Trigger LLM-based AW code generation.
    The generation runs in background; result is written to disk.
    """
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


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

def start(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the central server."""
    logger.info("Starting central server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    start()
