"""Provider-agnostic LLM client interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    id: str
    name: str
    args: Dict[str, Any]


@dataclass
class LLMResponse:
    content: Optional[str]
    tool_calls: List[ToolCall] = field(default_factory=list)

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


class LLMClient(ABC):
    """Provider-agnostic async LLM interface supporting tool/function calling."""

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
    ) -> LLMResponse:
        raise NotImplementedError

    @abstractmethod
    def tool_result_message(self, tool_call_id: str, result: str) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def assistant_tool_call_message(self, response: LLMResponse) -> Dict:
        raise NotImplementedError
