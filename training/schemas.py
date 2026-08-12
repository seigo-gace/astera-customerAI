from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

class RawFact(BaseModel):
    fact_id:str; topic:str; statement:str; source_id:str; status:Literal["approved","candidate","legacy","undecided"]; public:bool=True; volatile:bool=False
class CanonicalFact(BaseModel):
    fact_id:str; topic:str; statement:str; source_id:str; public:bool=True; volatile:bool=False
class DialogueTurn(BaseModel):
    role:Literal["user","assistant"]; content:str
class ScenarioSeed(BaseModel):
    scenario_id:str; scenario_class:str; audience:str; user_message:str; ideal_answer:str; fact_ids:list[str]=Field(default_factory=list); need_labels:list[str]=Field(default_factory=list); history:list[DialogueTurn]=Field(default_factory=list); grounding_required_fact_ids:list[str]=Field(default_factory=list); semantic_review_status:Literal["approved","pending","rejected"]="pending"; semantic_review_id:str=""; reviewed_fact_ids:list[str]=Field(default_factory=list); response_shape:Literal["direct","procedure","comparison","troubleshooting"]="direct"; expected_resolution_mode:str="resolved"; must_be_direct:bool=True; actionability_required:bool=False; max_clarification_questions:int=0
class LearningExample(BaseModel):
    scenario_id:str; messages:list[dict[str,str]]; fact_ids:list[str]
