"""Factory helpers for LLM clients."""
from __future__ import annotations

import os
from typing import Optional

from uiAutoAgent.central.llm.anthropic_client import AnthropicClient
from uiAutoAgent.central.llm.azure_openai_client import AzureOpenAIClient
from uiAutoAgent.central.llm.base import LLMClient
from uiAutoAgent.central.llm.openai_client import OpenAIClient


def create_llm_client(
    provider: str,
    model: str,
    api_key: Optional[str] = None,
    **kwargs,
) -> LLMClient:
    """
    provider: "openai" | "anthropic" | "azure_openai"
    Reads api_key from environment if not provided.
    """
    normalized = provider.lower()
    env_name = kwargs.pop("api_key_env", None)
    resolved_api_key = api_key or (os.getenv(env_name) if env_name else None)

    if normalized == "openai":
        resolved_api_key = resolved_api_key or os.getenv("OPENAI_API_KEY")
        openai_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key not in {"azure_endpoint", "api_version"}
        }
        return OpenAIClient(model=model, api_key=resolved_api_key, **openai_kwargs)
    if normalized == "anthropic":
        resolved_api_key = resolved_api_key or os.getenv("ANTHROPIC_API_KEY")
        anthropic_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key not in {"azure_endpoint", "api_version"}
        }
        return AnthropicClient(model=model, api_key=resolved_api_key, **anthropic_kwargs)
    if normalized == "azure_openai":
        resolved_api_key = resolved_api_key or os.getenv("AZURE_OPENAI_API_KEY")
        return AzureOpenAIClient(model=model, api_key=resolved_api_key, **kwargs)
    raise ValueError(f"Unsupported LLM provider: {provider}")
