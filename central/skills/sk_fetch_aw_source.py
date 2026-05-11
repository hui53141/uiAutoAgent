from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, Optional

from uiAutoAgent.central.skills.base import SkillBase, SkillResult
from uiAutoAgent.core import get_settings


class FetchAWSourceSkill(SkillBase):
    name = "fetch_aw_source"
    description = "Load the current AW Python source and related locator YAML from the central git checkout."
    input_schema = {
        "type": "object",
        "properties": {
            "aw_class": {
                "type": "string",
                "description": "Fully qualified AW class e.g. aw.examples.login_aw.LoginAW",
            }
        },
        "required": ["aw_class"],
    }

    def __init__(self, project_root: str):
        self.project_root = Path(project_root)

    async def execute(self, **kwargs) -> SkillResult:
        aw_class = kwargs["aw_class"]
        try:
            module_path, _class_name = aw_class.rsplit(".", 1)
        except ValueError:
            return SkillResult(False, None, error=f"Invalid aw_class: {aw_class}")

        relative_path = Path(*module_path.split(".")).with_suffix(".py")
        source_path = self.project_root / relative_path
        if not source_path.exists():
            return SkillResult(False, None, error=f"AW source not found: {relative_path}")

        source_code = source_path.read_text(encoding="utf-8")
        locator_info = self._load_locator_info(source_code)
        return SkillResult(
            True,
            {
                "file_path": relative_path.as_posix(),
                "source_code": source_code,
                "locator_info": locator_info,
            },
        )

    def _load_locator_info(self, source_code: str) -> Dict[str, Any]:
        page = self._extract_page_name(source_code)
        if not page:
            return {}

        settings = get_settings()
        locator_root = self.project_root / settings.get("locator", {}).get("locator_root", "locators")
        matches = sorted(locator_root.glob(f"**/{page}_page.yaml"))
        if not matches:
            return {"page": page}

        locator_path = matches[-1]
        return {
            "page": page,
            "locator_path": str(locator_path.relative_to(self.project_root)),
            "locator_content": locator_path.read_text(encoding="utf-8"),
        }

    @staticmethod
    def _extract_page_name(source_code: str) -> Optional[str]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "PAGE":
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                            return node.value.value
        match = re.search(r'^\s*PAGE\s*=\s*["\']([^"\']+)["\']', source_code, re.MULTILINE)
        return match.group(1) if match else None
