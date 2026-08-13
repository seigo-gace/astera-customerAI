from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RawFact(BaseModel):
    fact_id: str
    topic: str
    statement: str
    source_id: str
    status: Literal["approved", "candidate", "legacy", "undecided"]
    public: bool = True
    volatile: bool = False
    canonical_key: str | None = None
    condition_signature: str = ""
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)
    authority: Literal["canonical", "current", "live"] = "canonical"
    valid_from: str | None = None
    valid_to: str | None = None
    conflict_group: str | None = None
    lifecycle_status: str = "active"
    generation_id: str | None = None


class CanonicalFact(BaseModel):
    fact_id: str
    topic: str
    statement: str
    source_id: str
    public: bool = True
    volatile: bool = False
    canonical_key: str | None = None
    condition_signature: str = ""
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)
    authority: Literal["canonical", "current", "live"] = "canonical"
    valid_from: str | None = None
    valid_to: str | None = None
    conflict_group: str | None = None
    lifecycle_status: str = "active"
    generation_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class DialogueTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ScenarioSeed(BaseModel):
    scenario_id: str
    scenario_class: str
    audience: str
    user_message: str
    ideal_answer: str
    fact_ids: list[str] = Field(default_factory=list)
    need_labels: list[str] = Field(default_factory=list)
    history: list[DialogueTurn] = Field(default_factory=list)
    grounding_required_fact_ids: list[str] = Field(default_factory=list)
    semantic_review_status: Literal["approved", "pending", "rejected"] = "pending"
    semantic_review_id: str = ""
    reviewed_fact_ids: list[str] = Field(default_factory=list)
    response_shape: Literal["direct", "procedure", "comparison", "troubleshooting"] = "direct"
    expected_resolution_mode: str = "resolved"
    must_be_direct: bool = True
    actionability_required: bool = False
    max_clarification_questions: int = 0
    trajectory_id: str | None = None
    follow_up_kind: Literal["continue", "clarification", "condition_change", "correction", "new_need"] = "new_need"
    expected_need_carryover: bool = False
    expected_final_closure: bool = True


class LearningExample(BaseModel):
    scenario_id: str
    messages: list[dict[str, str]]
    fact_ids: list[str]
