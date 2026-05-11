from __future__ import annotations

from pathlib import Path

import pytest

from uiAutoAgent.executor.batch_collector import BatchCollector, CaseFailure
from uiAutoAgent.executor.hot_patcher import HotPatcher


class FakeResult:
    def __init__(self, task_id: str, success: bool):
        self.task_id = task_id
        self.success = success

    def to_dict(self):
        return {"task_id": self.task_id, "success": self.success}


class FakeTaskRunner:
    async def rerun_tasks(self, task_ids):
        return [FakeResult(task_id, True) for task_id in task_ids]


def test_batch_collector_returns_video_flags(tmp_path: Path):
    collector = BatchCollector(batch_id="batch-1", log_dir=str(tmp_path))
    collector.record_failure(
        CaseFailure(
            task_id="task-1",
            aw_class="aw.examples.login_aw.LoginAW",
            aw_method="run",
            error="boom",
            log_path=str(tmp_path / "task-1.log"),
            screenshot_paths=[],
            video_path=str(tmp_path / "task-1.mp4"),
        )
    )
    collector.record_failure(
        CaseFailure(
            task_id="task-2",
            aw_class="aw.examples.login_aw.LoginAW",
            aw_method="run",
            error="boom",
            log_path=str(tmp_path / "task-2.log"),
            screenshot_paths=[],
            video_path=None,
        )
    )

    assert collector.has_failures() is True
    assert collector.get_video_flags() == {"task-1": True, "task-2": False}


@pytest.mark.asyncio
async def test_hot_patcher_applies_fix_and_returns_validation_message(tmp_path: Path):
    patcher = HotPatcher(FakeTaskRunner(), repo_root=str(tmp_path))
    update = {
        "batch_id": "batch-1",
        "file_path": "aw/examples/login_aw.py",
        "fixed_code": "print('ok')\n",
        "rerun_task_ids": ["task-1", "task-2"],
    }

    result = await patcher.apply_and_rerun(update)

    assert (tmp_path / "aw" / "examples" / "login_aw.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert result["type"] == "validation_result"
    assert result["all_passed"] is True
    assert [item["task_id"] for item in result["rerun_results"]] == ["task-1", "task-2"]
