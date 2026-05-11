from __future__ import annotations

from typing import Dict, List

from uiAutoAgent.central.skills.base import SkillBase, SkillResult


class SkillRegistry:
    def __init__(self):
        self._skills: Dict[str, SkillBase] = {}

    def register(self, skill: SkillBase) -> None:
        self._skills[skill.name] = skill

    def to_tool_definitions(self) -> List[Dict]:
        return [skill.to_tool_definition() for skill in self._skills.values()]

    async def dispatch(self, name: str, args: Dict) -> SkillResult:
        if name not in self._skills:
            raise KeyError(name)
        return await self._skills[name].execute(**args)
