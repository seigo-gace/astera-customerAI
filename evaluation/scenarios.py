from __future__ import annotations
import json
from pathlib import Path
from pydantic import BaseModel, Field
class EvaluationScenario(BaseModel):
    scenario_id:str; scenario_class:str; critical:bool=False; multi_turn:bool=False; false_premise:bool=False; expected_need_labels:list[str]=Field(default_factory=list)
def load_scenarios(path:Path)->list[EvaluationScenario]:
    rows=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows,list): raise ValueError("scenario_file_must_be_json_array")
    return [EvaluationScenario.model_validate(item) for item in rows]
