from __future__ import annotations

import json
from pathlib import Path

import pytest

from uiAutoAgent.central.agent_server import AgentServer
from uiAutoAgent.central.artifact_store import ArtifactStore
from uiAutoAgent.central.llm.base import LLMClient, LLMResponse, ToolCall
from uiAutoAgent.central.skills.sk_analyze_root_cause import AnalyzeRootCauseSkill
from uiAutoAgent.central.skills.sk_fetch_aw_source import FetchAWSourceSkill
from uiAutoAgent.central.skills.sk_load_batch_artifacts import LoadBatchArtifactsSkill


class FakeLLMClient(LLMClient):
    def __init__(self, generated_code: str):
        self.generated_code = generated_code
        self.tool_turn = 0

    async def chat(self, messages, tools=None):
        if tools is None:
            return LLMResponse(content=self.generated_code)

        self.tool_turn += 1
        sequence = [
            LLMResponse(content=None, tool_calls=[ToolCall("tc-1", "load_batch_artifacts", {"batch_id": "batch-1"})]),
            LLMResponse(content=None, tool_calls=[ToolCall("tc-2", "fetch_aw_source", {"aw_class": "aw.examples.login_aw.LoginAW"})]),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        "tc-3",
                        "analyze_root_cause",
                        {
                            "log_text": (
                                "Traceback (most recent call last):\n"
                                "  File \"aw/examples/login_aw.py\", line 4, in run\n"
                                "    raise NoSuchElementException('login button not found')\n"
                                "NoSuchElementException: login button not found\n"
                            ),
                            "aw_class": "aw.examples.login_aw.LoginAW",
                            "source_code": self.generated_code,
                        },
                    )
                ],
            ),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        "tc-4",
                        "generate_fixed_script",
                        {
                            "original_source": self.generated_code,
                            "root_cause_report": {"classification": "locator_not_found"},
                            "failed_cases_summary": [{"task_id": "task-1"}],
                            "fix_instruction": "Keep the AW code unchanged for this test.",
                        },
                    )
                ],
            ),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        "tc-5",
                        "validate_syntax",
                        {
                            "code": self.generated_code,
                            "file_path": "aw/examples/login_aw.py",
                        },
                    )
                ],
            ),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        "tc-6",
                        "write_fix_to_central",
                        {
                            "file_path": "aw/examples/login_aw.py",
                            "fixed_code": self.generated_code,
                            "batch_id": "batch-1",
                            "affected_task_ids": ["task-1"],
                        },
                    )
                ],
            ),
            LLMResponse(
                content=json.dumps(
                    {
                        "fixed_files": ["aw/examples/login_aw.py"],
                        "affected_task_ids": ["task-1"],
                        "summary": "Kept the fixture AW code stable after validating the healing loop.",
                    }
                )
            ),
        ]
        return sequence[self.tool_turn - 1]

    def tool_result_message(self, tool_call_id: str, result: str):
        return {"role": "tool", "tool_call_id": tool_call_id, "content": result}

    def assistant_tool_call_message(self, response: LLMResponse):
        return {
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(tool_call.args),
                    },
                }
                for tool_call in response.tool_calls
            ],
        }


@pytest.fixture
def mini_project(tmp_path: Path) -> Path:
    (tmp_path / "aw" / "examples").mkdir(parents=True)
    (tmp_path / "locators" / "v1.0").mkdir(parents=True)
    aw_source = (
        "class LoginAW:\n"
        "    PAGE = \"login\"\n\n"
        "    def run(self) -> None:\n"
        "        return None\n"
    )
    (tmp_path / "aw" / "examples" / "login_aw.py").write_text(aw_source, encoding="utf-8")
    (tmp_path / "locators" / "v1.0" / "login_page.yaml").write_text(
        "page: login\nelements: {}\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.asyncio
async def test_load_batch_artifacts_skill_truncates_logs(tmp_path: Path):
    store = ArtifactStore(str(tmp_path / "artifacts"))
    log_path = tmp_path / "task-1.log"
    log_path.write_text("x" * 20, encoding="utf-8")
    await store.register_batch(
        batch_id="batch-1",
        node_id="node-1",
        task_results=[
            {
                "task_id": "task-1",
                "aw_class": "aw.examples.login_aw.LoginAW",
                "aw_method": "run",
                "error": "NoSuchElementException: login button not found",
            }
        ],
        log_paths={"task-1": str(log_path)},
        screenshot_paths={"task-1": ["shot.png"]},
        video_flags={"task-1": True},
    )

    skill = LoadBatchArtifactsSkill(store)
    result = await skill.execute(batch_id="batch-1", max_log_chars=5)

    assert result.success is True
    assert result.data["failed_cases"][0]["log_text"] == "xxxxx"
    assert result.data["failed_cases"][0]["video_available"] is True


@pytest.mark.asyncio
async def test_fetch_aw_source_reads_locator_info(mini_project: Path):
    skill = FetchAWSourceSkill(str(mini_project))
    result = await skill.execute(aw_class="aw.examples.login_aw.LoginAW")

    assert result.success is True
    assert result.data["file_path"] == "aw/examples/login_aw.py"
    assert result.data["locator_info"]["page"] == "login"
    assert "page: login" in result.data["locator_info"]["locator_content"]


@pytest.mark.asyncio
async def test_analyze_root_cause_classifies_locator_error():
    log_text = (
        "Traceback (most recent call last):\n"
        "  File \"aw/examples/login_aw.py\", line 4, in run\n"
        "    raise NoSuchElementException('login button not found')\n"
        "NoSuchElementException: login button not found\n"
    )
    result = await AnalyzeRootCauseSkill().execute(log_text=log_text, source_code="def run():\n    pass\n")

    assert result.success is True
    assert result.data["classification"] == "locator_not_found"
    assert result.data["failing_line_no"] == 4
    assert result.data["error_type"] == "NoSuchElementException"


@pytest.mark.asyncio
async def test_agent_server_heal_runs_tool_loop(mini_project: Path, tmp_path: Path):
    store = ArtifactStore(str(tmp_path / "artifacts"))
    log_path = tmp_path / "task-1.log"
    log_path.write_text(
        "Traceback (most recent call last):\n"
        "  File \"aw/examples/login_aw.py\", line 4, in run\n"
        "    raise NoSuchElementException('login button not found')\n"
        "NoSuchElementException: login button not found\n",
        encoding="utf-8",
    )
    await store.register_batch(
        batch_id="batch-1",
        node_id="node-1",
        task_results=[
            {
                "task_id": "task-1",
                "aw_class": "aw.examples.login_aw.LoginAW",
                "aw_method": "run",
                "error": "NoSuchElementException: login button not found",
            }
        ],
        log_paths={"task-1": str(log_path)},
        screenshot_paths={"task-1": []},
        video_flags={"task-1": False},
    )

    generated_code = (mini_project / "aw" / "examples" / "login_aw.py").read_text(encoding="utf-8")
    fake_llm = FakeLLMClient(generated_code)
    agent_server = AgentServer(
        artifact_store=store,
        llm_client=fake_llm,
        project_root=str(mini_project),
    )

    result = await agent_server.heal("batch-1", {"aw_class": "aw.examples.login_aw.LoginAW"})

    assert result.success is True
    assert result.fixed_files == ["aw/examples/login_aw.py"]
    assert result.affected_task_ids == ["task-1"]
    assert result.iterations == fake_llm.tool_turn
