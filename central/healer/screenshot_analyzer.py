"""
ScreenshotAnalyzer: uses a multimodal LLM to diagnose UI failures.

Given a screenshot and an error message, the LLM:
  1. Identifies which UI element is missing/changed
  2. Proposes updated locator strategies
  3. Returns structured YAML patch for the locator file

Token cost is minimized because this module is ONLY invoked by
FailureAggregator when a threshold is crossed — not on every failure.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from uiAutoAgent.core import get_settings, setup_logging

logger = setup_logging("ScreenshotAnalyzer")

# Prompt template for the multimodal LLM
_SYSTEM_PROMPT = """You are an expert Android UI test automation engineer.
Your task is to analyze a screenshot and a test failure message to diagnose
why an automated test failed and propose updated locator strategies.

Your response MUST be valid JSON with this exact structure:
{
  "diagnosis": "<one-sentence description of what changed in the UI>",
  "affected_element": "<element name from the locator file>",
  "proposed_strategies": [
    {"strategy": "accessibility_id", "value": "<new value>"},
    {"strategy": "xpath", "value": "<new xpath>"}
  ],
  "confidence": <0.0-1.0>,
  "notes": "<any additional observations>"
}

Only include strategies you can see evidence for in the screenshot.
Prefer accessibility_id over xpath when possible.
"""

_USER_PROMPT_TEMPLATE = """Test failure information:
- Task ID: {task_id}
- Error message: {error}
- Page under test: {page}
- Element that failed: {element}

Please analyze the screenshot and provide updated locator strategies.
"""


class ScreenshotAnalyzer:
    """
    Multimodal LLM-based screenshot analyzer for UI locator self-healing.
    """

    def __init__(self):
        settings = get_settings()
        healer_cfg = settings["central"]["healer"]
        self.provider = healer_cfg.get("llm_provider", "openai")
        self.model = healer_cfg.get("llm_model", "gpt-4o")
        self.max_tokens = healer_cfg.get("llm_max_tokens", 4096)
        api_key_env = healer_cfg.get("llm_api_key_env", "OPENAI_API_KEY")
        self.api_key = os.getenv(api_key_env, "")

    def analyze(
        self,
        screenshot_path: str,
        error: str,
        task_id: str,
        page: str = "unknown",
        element: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Analyze a failure screenshot and return LLM diagnosis.

        Returns:
            {
                "diagnosis": str,
                "affected_element": str,
                "proposed_strategies": List[Dict],
                "confidence": float,
                "notes": str,
            }
        """
        if not self.api_key:
            logger.warning(
                "No LLM API key configured. Returning empty analysis. "
                "Set %s env var to enable AI healing.",
                get_settings()["central"]["healer"]["llm_api_key_env"],
            )
            return self._empty_analysis(error)

        image_b64 = self._encode_image(screenshot_path)
        if not image_b64:
            return self._empty_analysis(error)

        user_content = _USER_PROMPT_TEMPLATE.format(
            task_id=task_id,
            error=error[:500],  # truncate to avoid token explosion
            page=page,
            element=element,
        )

        if self.provider == "openai":
            return self._call_openai(image_b64, user_content)
        elif self.provider == "anthropic":
            return self._call_anthropic(image_b64, user_content)
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider!r}")

    # ------------------------------------------------------------------
    # OpenAI GPT-4o (Vision)
    # ------------------------------------------------------------------

    def _call_openai(self, image_b64: str, user_content: str) -> Dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai package required. Run: pip install openai")

        client = OpenAI(api_key=self.api_key)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_content},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}",
                            "detail": "high",
                        },
                    },
                ],
            },
        ]
        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        return json.loads(raw)

    # ------------------------------------------------------------------
    # Anthropic Claude (Vision)
    # ------------------------------------------------------------------

    def _call_anthropic(self, image_b64: str, user_content: str) -> Dict[str, Any]:
        try:
            import anthropic
        except ImportError:
            raise ImportError("anthropic package required. Run: pip install anthropic")

        client = anthropic.Anthropic(api_key=self.api_key)
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": user_content},
                    ],
                }
            ],
        )
        raw = message.content[0].text
        # Extract JSON from response (Claude may wrap it)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_image(path: str) -> Optional[str]:
        p = Path(path)
        if not p.exists():
            logger.warning("Screenshot not found: %s", path)
            return None
        with open(p, "rb") as fh:
            return base64.b64encode(fh.read()).decode("utf-8")

    @staticmethod
    def _empty_analysis(error: str) -> Dict[str, Any]:
        return {
            "diagnosis": "LLM analysis unavailable (no API key or screenshot).",
            "affected_element": "unknown",
            "proposed_strategies": [],
            "confidence": 0.0,
            "notes": f"Original error: {error[:200]}",
        }
