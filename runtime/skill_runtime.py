from __future__ import annotations

from dataclasses import dataclass

from .contracts import CapabilityCapsule, SkillDescriptor, SkillValidationState
from .schemas import NeedTask


@dataclass(frozen=True)
class SkillQuery:
    language: str
    audience: str
    tasks: tuple[NeedTask, ...]
    has_evidence: bool
    text_length: int


class SkillRegistry:
    def __init__(self, skills: list[SkillDescriptor]):
        self._skills = {skill.skill_id: skill for skill in skills}

    def active(self) -> list[SkillDescriptor]:
        return [s for s in self._skills.values() if s.state == SkillValidationState.ACTIVE]

    def select(self, query: SkillQuery, *, limit: int = 8) -> list[CapabilityCapsule]:
        language = "ja" if query.language.lower().startswith("ja") else "en"
        shapes = {task.response_shape for task in query.tasks}
        intents = {task.intent for task in query.tasks}
        scored: list[tuple[int, SkillDescriptor]] = []
        for skill in self.active():
            if language not in skill.languages:
                continue
            score = 50 if skill.baseline else 0
            if shapes.intersection(skill.task_shapes): score += 30
            if "technical" in skill.capabilities and shapes.intersection({"procedure", "troubleshooting", "comparison"}): score += 20
            if "evidence" in skill.capabilities and query.has_evidence: score += 20
            if "audience" in skill.capabilities and query.audience != "general": score += 10
            if "planning" in skill.capabilities and query.text_length >= 800: score += 25
            if intents.intersection({"troubleshooting", "comparison", "procedure"}) and "review" in skill.capabilities: score += 10
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda pair: (-pair[0], pair[1].skill_id))
        return [CapabilityCapsule(skill_id=s.skill_id, text=s.capsule, score=score, capabilities=list(s.capabilities)) for score, s in scored[:limit]]
