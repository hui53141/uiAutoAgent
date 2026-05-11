from __future__ import annotations

import re
from typing import Optional

from uiAutoAgent.central.skills.base import SkillBase, SkillResult


class AnalyzeRootCauseSkill(SkillBase):
    name = "analyze_root_cause"
    description = "Analyze a failure log locally and classify the likely root cause without calling an LLM."
    input_schema = {
        "type": "object",
        "properties": {
            "log_text": {"type": "string"},
            "aw_class": {"type": "string"},
            "source_code": {"type": "string"},
        },
        "required": ["log_text"],
    }

    async def execute(self, **kwargs) -> SkillResult:
        log_text = kwargs["log_text"]
        source_code = kwargs.get("source_code", "")
        traceback_text = self._extract_traceback(log_text)
        error_type, error_message = self._extract_error(log_text)
        failing_file, failing_line_no = self._extract_failing_location(traceback_text or log_text)
        classification = self._classify(error_type, error_message, log_text)
        suggested_fix_area = self._suggest_fix_area(
            classification,
            failing_line_no,
            source_code,
        )
        return SkillResult(
            True,
            {
                "error_type": error_type,
                "error_message": error_message,
                "traceback": traceback_text,
                "failing_file": failing_file,
                "failing_line_no": failing_line_no,
                "classification": classification,
                "suggested_fix_area": suggested_fix_area,
            },
        )

    @staticmethod
    def _extract_traceback(log_text: str) -> str:
        match = re.search(
            r"(Traceback \(most recent call last\):[\s\S]+)",
            log_text,
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_error(log_text: str) -> tuple[str, str]:
        lines = [line.strip() for line in log_text.strip().splitlines() if line.strip()]
        for line in reversed(lines):
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):\s*(.*)", line)
            if match:
                return match.group(1), match.group(2)
        return "UnknownError", lines[-1] if lines else ""

    @staticmethod
    def _extract_failing_location(text: str) -> tuple[str, Optional[int]]:
        matches = re.findall(r'File "([^"]+)", line (\d+)', text)
        if not matches:
            return "", None
        file_path, line_no = matches[-1]
        return file_path, int(line_no)

    @staticmethod
    def _classify(error_type: str, error_message: str, log_text: str) -> str:
        text = f"{error_type} {error_message} {log_text}".lower()
        if "nosuchelement" in text or "not found" in text or "locator" in text:
            return "locator_not_found"
        if "timeout" in text or "timed out" in text:
            return "timeout"
        if "assertionerror" in text or "assert " in text:
            return "assertion_error"
        if "crash" in text or "fatal exception" in text:
            return "app_crash"
        if "network" in text or "connection" in text:
            return "network_error"
        if "importerror" in text or "modulenotfounderror" in text:
            return "import_error"
        return "unknown"

    @staticmethod
    def _suggest_fix_area(
        classification: str,
        failing_line_no: Optional[int],
        source_code: str,
    ) -> str:
        if failing_line_no and source_code:
            lines = source_code.splitlines()
            if 1 <= failing_line_no <= len(lines):
                code_line = lines[failing_line_no - 1].strip()
                return f"The line {failing_line_no} call `{code_line}` likely needs attention for {classification}."
        if failing_line_no:
            return f"Inspect the failing operation around line {failing_line_no} for {classification}."
        return f"Inspect the AW step associated with {classification}."
