from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .schemas import NeedTask


class SearchMode(str, Enum):
    RUNTIME_GROUNDING = "runtime_grounding"
    KB_HARVEST = "kb_harvest"
    SKILL_SEARCH = "skill_search"


class TaskContract(BaseModel):
    purpose: str
    target: str
    conditions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    premises: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    need_tasks: list[NeedTask] = Field(default_factory=list)
    completion_conditions: list[str] = Field(default_factory=list)


class NeedSearchPlan(BaseModel):
    task_id: str
    stable_need_id: str | None = None
    targets: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    reuse_fact_ids: list[str] = Field(default_factory=list)
    refresh_fact_ids: list[str] = Field(default_factory=list)
    invalidation_reasons: list[str] = Field(default_factory=list)


class SearchPlan(BaseModel):
    targets: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    source_priority: list[str] = Field(default_factory=list)
    comparison_conditions: list[str] = Field(default_factory=list)
    verification_conditions: list[str] = Field(default_factory=list)
    unresolved_gaps: list[str] = Field(default_factory=list)
    need_plans: list[NeedSearchPlan] = Field(default_factory=list)
    mode: SearchMode


class HarvestCandidate(BaseModel):
    statement: str
    source_uri: str
    issuer: str | None = None
    retrieved_at: str
    published_updated_effective_date: str | None = None
    supported_scope: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    retrieval_status: Literal["candidate", "conflict", "rejected"] = "candidate"
    unresolved_gaps: list[str] = Field(default_factory=list)
    canonical: bool = False


class SkillValidationState(str, Enum):
    IMPORTED = "IMPORTED"
    SCHEMA_VALID = "SCHEMA_VALID"
    SYNTAX_VALID = "SYNTAX_VALID"
    DEPENDENCY_READY = "DEPENDENCY_READY"
    UNIT_TESTED = "UNIT_TESTED"
    SANDBOX_TESTED = "SANDBOX_TESTED"
    ACTIVE = "ACTIVE"


class SkillDescriptor(BaseModel):
    skill_id: str
    name: str
    languages: list[str]
    capabilities: list[str]
    task_shapes: list[str] = Field(default_factory=list)
    capsule: str
    state: SkillValidationState = SkillValidationState.IMPORTED
    baseline: bool = False


class CapabilityCapsule(BaseModel):
    skill_id: str
    text: str
    score: int
    capabilities: list[str] = Field(default_factory=list)
