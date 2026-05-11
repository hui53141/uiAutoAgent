"""Azure OpenAI LLM client implementation."""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from openai import AsyncAzureOpenAI

from uiAutoAgent.central.llm.base import LLMClient, LLMResponse, ToolCall


class AzureOpenAIClient(LLMClient):
    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        azure_endpoint: Optional[str] = None,
        api_version: Optional[str] = None,
        max_tokens: int = 4096,
        **kwargs,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            **kwargs,
        )

    async def chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = [
                tool if tool.get("type") == "function" else {
                    "type": "function",
                    "function": tool,
                }
                for tool in tools
            ]

        response = await self.client.chat.completions.create(**payload)
        message = response.choices[0].message
        tool_calls = []
        for tool_call in message.tool_calls or []:
            try:
                args = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                ToolCall(
                    id=tool_call.id,
                    name=tool_call.function.name,
                    args=args,
                )
            )
        return LLMResponse(content=message.content, tool_calls=tool_calls)

    def tool_result_message(self, tool_call_id: str, result: str) -> Dict:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": result}

    def assistant_tool_call_message(self, response: LLMResponse) -> Dict:
        return {
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(tool_call.args, ensure_ascii=False),
                    },
                }
                for tool_call in response.tool_calls
            ],
        }
