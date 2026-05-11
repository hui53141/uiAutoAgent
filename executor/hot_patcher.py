from __future__ import annotations

from pathlib import Path

from uiAutoAgent.core import setup_logging

logger = setup_logging("HotPatcher")


class HotPatcher:
    """
    Applies script fixes received from Central and reruns failed cases.
    Fix happens on Central side; this just receives and applies the result.
    NO importlib.reload — tasks are rerun as fresh executions loading the new file.
    """

    def __init__(self, task_runner: "TaskRunner", repo_root: str | None = None):
        self.task_runner = task_runner
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[1])

    async def apply_and_rerun(self, update: dict) -> dict:
        file_path = update["file_path"]
        fixed_code = update["fixed_code"]
        rerun_task_ids = update["rerun_task_ids"]

        target_path = Path(file_path)
        if not target_path.is_absolute():
            target_path = self.repo_root / target_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(fixed_code, encoding="utf-8")
        logger.info("Applied fix to %s", target_path)

        results = await self.task_runner.rerun_tasks(rerun_task_ids)

        return {
            "type": "validation_result",
            "batch_id": update["batch_id"],
            "file_path": file_path,
            "rerun_results": [r.to_dict() for r in results],
            "all_passed": all(r.success for r in results),
        }
