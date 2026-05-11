from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict
import json


@dataclass
class SkillResult:
    success: bool
    data: Any
    error: str = ""

    def to_tool_result_str(self) -> str:
        if self.success:
            return json.dumps(self.data, ensure_ascii=False, indent=2)
        return json.dumps({"error": self.error}, ensure_ascii=False)


class SkillBase(ABC):
    name: str
    description: str
    input_schema: Dict

    @abstractmethod
    async def execute(self, **kwargs) -> SkillResult:
        raise NotImplementedError

    def to_tool_definition(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }
