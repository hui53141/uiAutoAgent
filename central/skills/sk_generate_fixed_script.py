from __future__ import annotations

import json
import re

from uiAutoAgent.central.llm.base import LLMClient
from uiAutoAgent.central.skills.base import SkillBase, SkillResult


class GenerateFixedScriptSkill(SkillBase):
    name = "generate_fixed_script"
    description = "Generate a targeted AW Python fix using the configured LLM without calling any tools."
    input_schema = {
        "type": "object",
        "properties": {
            "original_source": {"type": "string"},
            "root_cause_report": {"type": "object"},
            "failed_cases_summary": {"type": "array", "items": {"type": "object"}},
            "fix_instruction": {
                "type": "string",
                "description": "LLM-authored natural language instruction for what to fix",
            },
            "previous_attempt_error": {
                "type": "string",
                "description": "Validation error from previous attempt, if any",
                "default": "",
            },
        },
        "required": ["original_source", "root_cause_report", "fix_instruction"],
    }

    def __init__(self, llm_client: LLMClient | None):
        self.llm_client = llm_client

    async def execute(self, **kwargs) -> SkillResult:
        if self.llm_client is None:
            return SkillResult(False, None, error="LLM client is not configured")

        system_prompt = (
            "You are fixing one Python Action Word class in uiAutoAgent. "
            "Only fix the identified issue, do not change unrelated methods. "
            "Preserve all imports and class structure. "
            "Return ONLY Python code with no markdown fences. "
            "Follow the existing BaseAW style, including retry decorators and helper usage."
        )
        user_prompt = json.dumps(
            {
                "root_cause_report": kwargs["root_cause_report"],
                "failed_cases_summary": kwargs.get("failed_cases_summary", []),
                "fix_instruction": kwargs["fix_instruction"],
                "previous_attempt_error": kwargs.get("previous_attempt_error", ""),
                "original_source": kwargs["original_source"],
            },
            ensure_ascii=False,
            indent=2,
        )
        response = await self.llm_client.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        fixed_code = self._strip_markdown_fences(response.content or "")
        if not fixed_code.strip():
            return SkillResult(False, None, error="LLM returned empty code")
        return SkillResult(True, {"fixed_code": fixed_code})

    @staticmethod
    def _strip_markdown_fences(content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        return stripped.strip()
