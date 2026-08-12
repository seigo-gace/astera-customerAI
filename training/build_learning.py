from __future__ import annotations
from .schemas import CanonicalFact, LearningExample, ScenarioSeed
SYSTEM_RULE="You are Astera Customer AI. Resolve every major user need directly, preserve current conditions and exceptions, do not invent Astera-specific facts, use runtime placeholders for volatile facts, and ask at most the required minimum clarification question."
def build_learning(canonical_facts:list[CanonicalFact],seeds:list[ScenarioSeed])->list[LearningExample]:
    by_id={i.fact_id:i for i in canonical_facts}; output=[]
    for seed in seeds:
        if seed.semantic_review_status!="approved" or not seed.semantic_review_id: continue
        if set(seed.fact_ids)-set(seed.reviewed_fact_ids): continue
        if any(fid not in by_id for fid in seed.fact_ids): continue
        answer=seed.ideal_answer
        for fid in seed.grounding_required_fact_ids:
            fact=by_id.get(fid)
            if fact is not None and fact.volatile and f"<CURRENT_FACT:{fid}>" not in answer: raise ValueError(f"volatile fact must use runtime placeholder: {fid}")
        messages=[{"role":"system","content":SYSTEM_RULE}]; messages.extend(turn.model_dump() for turn in seed.history); messages.append({"role":"user","content":seed.user_message}); messages.append({"role":"assistant","content":answer}); output.append(LearningExample(scenario_id=seed.scenario_id,messages=messages,fact_ids=seed.fact_ids))
    return output
