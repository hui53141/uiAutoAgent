from __future__ import annotations

import ast
import asyncio
import os
import tempfile
from pathlib import Path

from uiAutoAgent.central.skills.base import SkillBase, SkillResult


class ValidateSyntaxSkill(SkillBase):
    name = "validate_syntax"
    description = "Validate generated Python code using ast.parse and mypy type checking."
    input_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "file_path": {
                "type": "string",
                "description": "Used to resolve relative imports context",
            },
        },
        "required": ["code"],
    }

    async def execute(self, **kwargs) -> SkillResult:
        code = kwargs["code"]
        file_path = kwargs.get("file_path", "generated_aw.py")
        try:
            ast.parse(code)
        except SyntaxError as exc:
            return SkillResult(True, {"valid": False, "errors": str(exc)})

        tmp_path = None
        try:
            suffix = Path(file_path).suffix or ".py"
            with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as fh:
                fh.write(code)
                tmp_path = fh.name

            process = await asyncio.create_subprocess_exec(
                os.environ.get("PYTHON", "python"),
                "-m",
                "mypy",
                "--ignore-missing-imports",
                "--no-error-summary",
                tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                return SkillResult(True, {"valid": True})
            errors = (stdout + stderr).decode("utf-8", errors="ignore").strip()
            return SkillResult(True, {"valid": False, "errors": errors})
        except FileNotFoundError:
            return SkillResult(True, {"valid": False, "errors": "python executable not found"})
        finally:
            if tmp_path and Path(tmp_path).exists():
                Path(tmp_path).unlink()
