import pytest
from training.build_canonical import CanonicalConflictError,build_canonical
from training.build_learning import build_learning
from training.schemas import DialogueTurn,RawFact,ScenarioSeed
def test_legacy_fact_is_not_canonical():
    raw=[RawFact(fact_id="f1",topic="x",statement="old",source_id="a",status="legacy"),RawFact(fact_id="f2",topic="x",statement="new",source_id="b",status="approved")]; assert [i.fact_id for i in build_canonical(raw)]==["f2"]
def test_conflicting_decided_facts_are_blocked():
    raw=[RawFact(fact_id="f",topic="x",statement="a",source_id="a",status="approved"),RawFact(fact_id="f",topic="x",statement="b",source_id="b",status="approved")]
    with pytest.raises(CanonicalConflictError): build_canonical(raw)
def test_volatile_fact_requires_runtime_placeholder():
    facts=build_canonical([RawFact(fact_id="f",topic="price",statement="100",source_id="a",status="approved",volatile=True)]); seed=ScenarioSeed(scenario_id="s",scenario_class="direct",audience="general",user_message="今の値",ideal_answer="100",fact_ids=["f"],need_labels=["price"],grounding_required_fact_ids=["f"],semantic_review_status="approved",semantic_review_id="review",reviewed_fact_ids=["f"])
    with pytest.raises(ValueError): build_learning(facts,[seed])
def test_multi_turn_history_is_real_dialogue():
    facts=build_canonical([RawFact(fact_id="f",topic="x",statement="ok",source_id="a",status="approved")]); seed=ScenarioSeed(scenario_id="s",scenario_class="multi_turn",audience="general",user_message="続き",ideal_answer="ok",history=[DialogueTurn(role="user",content="最初"),DialogueTurn(role="assistant",content="前提")],fact_ids=["f"],need_labels=["x"],semantic_review_status="approved",semantic_review_id="r",reviewed_fact_ids=["f"]); e=build_learning(facts,[seed])[0]; assert [m["role"] for m in e.messages]==["system","user","assistant","user","assistant"]
