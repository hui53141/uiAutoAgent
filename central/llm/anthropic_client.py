"""Anthropic LLM client implementation."""
from __future__ import annotations

from typing import Dict, List, Optional

from anthropic import AsyncAnthropic

from uiAutoAgent.central.llm.base import LLMClient, LLMResponse, ToolCall


class AnthropicClient(LLMClient):
    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        max_tokens: int = 4096,
        **kwargs,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.client = AsyncAnthropic(api_key=api_key, **kwargs)

    async def chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
    ) -> LLMResponse:
        system_parts: List[str] = []
        provider_messages: List[Dict] = []

        for message in messages:
            role = message.get("role")
            if role == "system":
                system_parts.append(str(message.get("content", "")))
                continue

            if role == "tool":
                provider_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message["tool_call_id"],
                                "content": message.get("content", ""),
                            }
                        ],
                    }
                )
                continue

            provider_messages.append(
                {
                    "role": role,
                    "content": message.get("content", ""),
                }
            )

        payload = {
            "model": self.model,
            "messages": provider_messages,
            "max_tokens": self.max_tokens,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if tools:
            payload["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", {}),
                }
                for tool in tools
            ]

        response = await self.client.messages.create(**payload)
        content_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, args=dict(block.input or {}))
                )

        final_text = "\n".join(part for part in content_parts if part).strip()
        return LLMResponse(content=final_text or None, tool_calls=tool_calls)

    def tool_result_message(self, tool_call_id: str, result: str) -> Dict:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": result,
                }
            ],
        }

    def assistant_tool_call_message(self, response: LLMResponse) -> Dict:
        content: List[Dict] = []
        if response.content:
            content.append({"type": "text", "text": response.content})
        content.extend(
            {
                "type": "tool_use",
                "id": tool_call.id,
                "name": tool_call.name,
                "input": tool_call.args,
            }
            for tool_call in response.tool_calls
        )
        return {"role": "assistant", "content": content}
