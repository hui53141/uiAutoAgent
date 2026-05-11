"""
OpenCode-style Agent Server for uiAutoAgent.

Responsibilities:
- Maintain a SkillRegistry with all healing skills
- Accept HealingTask requests (from WebSocket batch_done events)
- Run AgentLoop: LLM + tools loop until done or max iterations
- Handle validation retry loop (max MAX_HEAL_ROUNDS=10)
- Return HealResult with fixed files and affected task IDs
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from uiAutoAgent.central.artifact_store import ArtifactStore
from uiAutoAgent.central.llm.base import LLMClient
from uiAutoAgent.central.llm.factory import create_llm_client
from uiAutoAgent.central.skills.registry import SkillRegistry
from uiAutoAgent.central.skills.sk_analyze_root_cause import AnalyzeRootCauseSkill
from uiAutoAgent.central.skills.sk_fetch_aw_source import FetchAWSourceSkill
from uiAutoAgent.central.skills.sk_generate_fixed_script import GenerateFixedScriptSkill
from uiAutoAgent.central.skills.sk_load_batch_artifacts import LoadBatchArtifactsSkill
from uiAutoAgent.central.skills.sk_validate_syntax import ValidateSyntaxSkill
from uiAutoAgent.central.skills.sk_write_fix import WriteFixSkill
from uiAutoAgent.core import get_settings, setup_logging

MAX_HEAL_ROUNDS = 10
MAX_ITERATIONS_PER_ROUND = 15

SYSTEM_PROMPT = """
You are an expert Android UI test automation engineer operating as part of an automated healing pipeline.

Your job: analyze test batch failures and produce a FIXED AW (Action Word) Python script.

You have these tools available. Use them in this ORDER:
1. load_batch_artifacts    — understand what failed (logs, error messages)
2. fetch_aw_source         — get the current (broken) AW source code from the git repo
3. analyze_root_cause      — parse the failure log to classify the root cause
4. generate_fixed_script   — generate the fixed Python code (include a clear fix_instruction)
5. validate_syntax         — verify the fix compiles and passes type checks
   └─ If validate_syntax fails: call generate_fixed_script AGAIN with previous_attempt_error filled in
   └─ Maximum 10 total attempts across all rounds
6. write_fix_to_central    — persist the validated fix

After write_fix_to_central succeeds, respond with ONLY this JSON (no other text):
{
  "fixed_files": ["path/to/aw_file.py"],
  "affected_task_ids": ["task-id-1", ...],
  "summary": "One paragraph describing what was broken and how it was fixed."
}

Rules:
- ONLY fix what the root cause analysis identifies. Do not refactor unrelated code.
- If you cannot determine a fix after 10 validation rounds, respond with:
  {"fixed_files": [], "affected_task_ids": [], "summary": "Unable to fix: <reason>"}
- Phase 1: text logs only. Do not attempt to fetch or analyze video/image content.
""".strip()

logger = setup_logging("AgentServer")


@dataclass
class HealResult:
    batch_id: str
    success: bool
    fixed_files: List[str]
    affected_task_ids: List[str]
    summary: str
    iterations: int
    rounds: int


class AgentServer:
    def __init__(
        self,
        artifact_store: ArtifactStore,
        llm_client: LLMClient | None = None,
        project_root: str | None = None,
    ):
        self.settings = get_settings()
        self.project_root = Path(project_root or Path(__file__).resolve().parents[1])
        self.artifact_store = artifact_store
        self.agent_cfg = self.settings.get("central", {}).get("agent_server", {})
        self.max_heal_rounds = int(self.agent_cfg.get("max_heal_rounds", MAX_HEAL_ROUNDS))
        self.max_iterations_per_round = int(
            self.agent_cfg.get("max_iterations_per_round", MAX_ITERATIONS_PER_ROUND)
        )
        self.llm_client = llm_client or self._build_llm_client()
        self.registry = SkillRegistry()
        self.registry.register(LoadBatchArtifactsSkill(self.artifact_store))
        self.registry.register(FetchAWSourceSkill(str(self.project_root)))
        self.registry.register(AnalyzeRootCauseSkill())
        self.registry.register(GenerateFixedScriptSkill(self.llm_client))
        self.registry.register(ValidateSyntaxSkill())
        self.registry.register(WriteFixSkill(str(self.project_root)))

    def _build_llm_client(self) -> LLMClient | None:
        try:
            return create_llm_client(
                provider=self.agent_cfg.get("llm_provider", "openai"),
                model=self.agent_cfg.get("llm_model", "gpt-4o"),
                api_key_env=self.agent_cfg.get("llm_api_key_env", "OPENAI_API_KEY"),
                max_tokens=self.agent_cfg.get("llm_max_tokens", 4096),
                azure_endpoint=self.agent_cfg.get("azure_endpoint"),
                api_version=self.agent_cfg.get("api_version"),
            )
        except Exception as exc:
            logger.warning("LLM client initialization failed: %s", exc)
            return None

    async def heal(self, batch_id: str, batch_summary: dict) -> HealResult:
        """
        Run the OpenCode AgentLoop for a healing task.

        - Builds initial messages with system prompt + user task description
        - Loops: LLM call → if tool_calls → dispatch each → append results → repeat
        - Stops when LLM returns is_final (no tool calls) → parse JSON summary
        - Handles validation retry up to MAX_HEAL_ROUNDS total
        - Returns HealResult
        """
        if self.llm_client is None:
            return HealResult(
                batch_id=batch_id,
                success=False,
                fixed_files=[],
                affected_task_ids=[],
                summary="Unable to fix: LLM client is not configured",
                iterations=0,
                rounds=0,
            )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "batch_id": batch_id,
                        "batch_summary": batch_summary,
                        "task": "Investigate the failed batch and fix the relevant AW file.",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]
        tool_definitions = self.registry.to_tool_definitions()
        iterations = 0
        rounds = 0
        max_iterations = self.max_iterations_per_round * max(1, self.max_heal_rounds)

        while iterations < max_iterations:
            response = await self.llm_client.chat(messages, tools=tool_definitions)
            iterations += 1
            if response.is_final:
                payload = self._parse_final_response(response.content or "")
                return HealResult(
                    batch_id=batch_id,
                    success=bool(payload.get("fixed_files")),
                    fixed_files=list(payload.get("fixed_files", [])),
                    affected_task_ids=list(payload.get("affected_task_ids", [])),
                    summary=payload.get("summary", ""),
                    iterations=iterations,
                    rounds=rounds,
                )

            messages.append(self.llm_client.assistant_tool_call_message(response))
            tool_results = await asyncio.gather(
                *(self._dispatch_tool_call(tool_call.name, tool_call.args) for tool_call in response.tool_calls)
            )
            for tool_call, result in zip(response.tool_calls, tool_results):
                result_text = result.to_tool_result_str()
                if tool_call.name == "validate_syntax":
                    try:
                        payload = json.loads(result_text)
                    except json.JSONDecodeError:
                        payload = {}
                    if payload.get("valid") is False:
                        rounds += 1
                messages.append(self.llm_client.tool_result_message(tool_call.id, result_text))

        return HealResult(
            batch_id=batch_id,
            success=False,
            fixed_files=[],
            affected_task_ids=[],
            summary="Unable to fix: agent loop exceeded iteration limit",
            iterations=iterations,
            rounds=rounds,
        )

    async def _dispatch_tool_call(self, name: str, args: Dict[str, Any]):
        try:
            return await self.registry.dispatch(name, args)
        except KeyError:
            from uiAutoAgent.central.skills.base import SkillResult

            return SkillResult(False, None, error=f"Unknown tool: {name}")
        except Exception as exc:
            from uiAutoAgent.central.skills.base import SkillResult

            return SkillResult(False, None, error=f"Tool {name} failed: {exc}")

    @staticmethod
    def _parse_final_response(content: str) -> Dict[str, Any]:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1]
            if stripped.endswith("```"):
                stripped = stripped.rsplit("```", 1)[0]
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(stripped[start : end + 1])
                except json.JSONDecodeError:
                    pass
        return {
            "fixed_files": [],
            "affected_task_ids": [],
            "summary": f"Unable to fix: could not parse final response `{content}`",
        }
