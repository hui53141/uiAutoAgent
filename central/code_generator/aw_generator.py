"""
AWGenerator: LLM-powered Action Word code generator.

Given a natural language description of a business operation and
a locator YAML file, generates a complete Python AW class.

Usage::

    gen = AWGenerator()
    code = gen.generate(
        page="login",
        class_name="LoginAW",
        operations=["login with credentials", "verify error on bad password"],
    )
    gen.write(code, "aw/examples/login_aw.py")
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from uiAutoAgent.core import get_settings, setup_logging

logger = setup_logging("AWGenerator")

_ROOT = Path(__file__).parent.parent.parent

_SYSTEM_PROMPT = """\
You are a senior Python test automation engineer specializing in Android UI automation.
You write clean, well-documented Python classes that follow these rules:
- Inherit from BaseAW (from uiAutoAgent.aw.base_aw import BaseAW)
- Use self.tap(), self.type_text(), self.get_text(), self.is_visible(), self.wait_for()
- Use the @retry() decorator for flaky operations
- Group operations into meaningful business-level methods
- Include clear docstrings
- Follow PEP 8 style

Available element names come from the locator YAML provided.
Do NOT hardcode xpath or accessibility_id strings — use the element names from the locator.
"""

_USER_PROMPT_TEMPLATE = """\
Generate a Python AW class for the "{page}" page.

Class name: {class_name}
Module path: {module_path}

Locator elements available (from locators/{version}/{page}_page.yaml):
{elements_yaml}

Business operations to implement:
{operations}

Return ONLY the Python code — no markdown fences, no explanation.
The file should be self-contained and importable.
"""


class AWGenerator:
    """
    Generates AW (Action Word) Python classes using a language model.
    """

    def __init__(self):
        settings = get_settings()
        gen_cfg = settings["central"]["code_generator"]
        healer_cfg = settings["central"]["healer"]
        self.output_dir = _ROOT / gen_cfg.get("output_dir", "aw")
        self.provider = healer_cfg.get("llm_provider", "openai")
        self.model = healer_cfg.get("llm_model", "gpt-4o")
        self.max_tokens = healer_cfg.get("llm_max_tokens", 4096)
        api_key_env = healer_cfg.get("llm_api_key_env", "OPENAI_API_KEY")
        self.api_key = os.getenv(api_key_env, "")

    def generate(
        self,
        page: str,
        class_name: str,
        operations: List[str],
        app_version: str = "1.0",
        output_subdir: str = "examples",
    ) -> str:
        """
        Generate Python code for an AW class.

        Args:
            page: Locator page name (e.g. "login")
            class_name: Python class name (e.g. "LoginAW")
            operations: List of natural-language descriptions of methods to generate.
            app_version: App version to select correct locator file.
            output_subdir: Subdirectory under aw/ for the output file.

        Returns:
            Generated Python source code as a string.
        """
        elements_yaml = self._load_elements_yaml(page, app_version)
        module_path = f"aw.{output_subdir}.{page}_aw"

        user_prompt = _USER_PROMPT_TEMPLATE.format(
            page=page,
            class_name=class_name,
            module_path=module_path,
            elements_yaml=elements_yaml,
            operations="\n".join(f"  - {op}" for op in operations),
            version=f"v{app_version}",
        )

        if not self.api_key:
            logger.warning(
                "No LLM API key; returning stub code. "
                "Set the LLM API key environment variable to enable code generation."
            )
            return self._stub_code(class_name, page, operations)

        if self.provider == "openai":
            return self._call_openai(user_prompt)
        elif self.provider == "anthropic":
            return self._call_anthropic(user_prompt)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    def write(self, code: str, relative_path: str) -> Path:
        """Write generated code to disk."""
        output_path = _ROOT / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(code, encoding="utf-8")
        logger.info("Generated AW written to %s", output_path)
        return output_path

    def generate_and_write(
        self,
        page: str,
        class_name: str,
        operations: List[str],
        app_version: str = "1.0",
        output_subdir: str = "examples",
    ) -> Path:
        """Convenience: generate and write in one call."""
        code = self.generate(page, class_name, operations, app_version, output_subdir)
        rel_path = f"aw/{output_subdir}/{page}_aw.py"
        return self.write(code, rel_path)

    # ------------------------------------------------------------------
    # LLM calls
    # ------------------------------------------------------------------

    def _call_openai(self, user_prompt: str) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai package required. Run: pip install openai")

        client = OpenAI(api_key=self.api_key)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=self.max_tokens,
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()

    def _call_anthropic(self, user_prompt: str) -> str:
        try:
            import anthropic
        except ImportError:
            raise ImportError("anthropic package required. Run: pip install anthropic")

        client = anthropic.Anthropic(api_key=self.api_key)
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text.strip()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_elements_yaml(self, page: str, app_version: str) -> str:
        """Load locator elements as YAML string for the prompt."""
        locator_root = _ROOT / "locators"
        version_dirs = sorted(
            [d for d in locator_root.iterdir() if d.is_dir()],
            key=lambda d: self._version_tuple(d.name),
        )
        target = self._version_tuple(f"v{app_version}")
        selected = version_dirs[0] if version_dirs else None
        for d in version_dirs:
            if self._version_tuple(d.name) <= target:
                selected = d

        if selected is None:
            return "No locator file found."

        page_file = selected / f"{page}_page.yaml"
        if not page_file.exists():
            return f"No locator file for page '{page}' at version {app_version}."

        with open(page_file, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        elements = data.get("elements", {})
        summary = {
            name: {
                "description": elem.get("description", ""),
                "strategies": [s.get("strategy") for s in elem.get("strategies", [])],
            }
            for name, elem in elements.items()
        }
        return yaml.dump(summary, allow_unicode=True, sort_keys=False)

    @staticmethod
    def _stub_code(class_name: str, page: str, operations: List[str]) -> str:
        """Return a minimal stub when LLM is unavailable."""
        methods = "\n\n".join(
            f"    def {op.lower().replace(' ', '_').replace('-', '_')}(self) -> None:\n"
            f'        """TODO: implement {op}"""\n'
            f"        raise NotImplementedError"
            for op in operations
        )
        return f'''\
"""
{class_name}: Auto-generated stub (LLM unavailable).
Implement the methods below using BaseAW helpers.
"""
from uiAutoAgent.aw.base_aw import BaseAW


class {class_name}(BaseAW):
    PAGE = "{page}"

{methods}
'''

    @staticmethod
    def _version_tuple(version: str) -> tuple:
        import re
        cleaned = re.sub(r"[^0-9.]", "", version)
        try:
            return tuple(int(x) for x in cleaned.split(".") if x)
        except ValueError:
            return (0,)
