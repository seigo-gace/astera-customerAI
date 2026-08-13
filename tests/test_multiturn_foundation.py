import pytest

from runtime.knowledge import GroundingConflictError, GroundingPlanner
from runtime.schemas import FollowUpKind, GroundedFact, NeedLifecycle, NeedTask
from runtime.state import StateStore


class Store:
    def __init__(self, facts):
        self.facts = list(facts)
        self.calls = 0

    async def find_for_tasks(self, tasks, plan=None):
        self.calls += 1
        return list(self.facts)

    async def current_facts(self, tasks):
        self.calls += 1
        return list(self.facts)


def task(text="Asteraとは？", task_id="t1"):
    return NeedTask(task_id=task_id, text=text, intent="general", completion_condition="done")


def test_stable_need_id_survives_follow_up():
    store = StateStore()
    state = store.get("s")
    store.begin_turn("s", "Asteraとは？")
    first = store.bind_tasks("s", [task("Asteraとは？")], FollowUpKind.NEW_NEED)
    first_id = first[0].stable_need_id
    store.complete_turn("s", first, resolved_task_ids={"t1"}, unresolved_task_ids=set())
    assert state.need_ledger[first_id].lifecycle == NeedLifecycle.RESOLVED
    kind = store.begin_turn("s", "それを詳しく")
    second = store.bind_tasks("s", [task("それを詳しく")], kind)
    assert kind == FollowUpKind.CONTINUE
    assert second[0].stable_need_id == first_id


def test_short_new_topic_stays_new_need():
    store = StateStore()
    store.begin_turn("s", "Asteraとは？")
    first = store.bind_tasks("s", [task("Asteraとは？")], FollowUpKind.NEW_NEED)
    store.complete_turn("s", first, resolved_task_ids={"t1"}, unresolved_task_ids=set())
    kind = store.begin_turn("s", "料金は？")
    second = store.bind_tasks("s", [task("料金は？")], kind)
    assert kind == FollowUpKind.NEW_NEED
    assert second[0].stable_need_id != first[0].stable_need_id


def test_condition_change_reopens_and_invalidates_conditional_reuse():
    store = StateStore()
    store.begin_turn("s", "料金は？")
    tasks = store.bind_tasks("s", [task("料金は？")], FollowUpKind.NEW_NEED)
    need_id = tasks[0].stable_need_id
    fact = GroundedFact(fact_id="price", value="x", source_id="c", authority="canonical", conditions=["plan=pro"], condition_signature="pro")
    store.record_evidence("s", tasks, [fact])
    store.complete_turn("s", tasks, resolved_task_ids={"t1"}, unresolved_task_ids=set())
    kind = store.begin_turn("s", "Enterpriseの場合は？")
    follow = store.bind_tasks("s", [task("Enterpriseの場合は？")], kind)
    assert follow[0].stable_need_id == need_id
    assert store.get("s").need_ledger[need_id].lifecycle == NeedLifecycle.REOPENED
    assert store.reusable_facts("s", follow, kind) == []


def test_nonvolatile_evidence_is_reusable():
    store = StateStore()
    store.begin_turn("s", "Asteraとは？")
    tasks = store.bind_tasks("s", [task()], FollowUpKind.NEW_NEED)
    fact = GroundedFact(fact_id="f1", value="v", source_id="c", authority="canonical")
    store.record_evidence("s", tasks, [fact])
    store.complete_turn("s", tasks, resolved_task_ids={"t1"}, unresolved_task_ids=set())
    kind = store.begin_turn("s", "それの仕組みは？")
    follow = store.bind_tasks("s", [task("それの仕組みは？")], kind)
    assert [f.fact_id for f in store.reusable_facts("s", follow, kind)] == ["f1"]


@pytest.mark.asyncio
async def test_different_conditions_do_not_false_conflict():
    facts = [
        GroundedFact(fact_id="price", value="980", source_id="a", authority="canonical", condition_signature="basic"),
        GroundedFact(fact_id="price", value="2980", source_id="b", authority="canonical", condition_signature="pro"),
    ]
    out = await GroundingPlanner(Store(facts), Store([])).build_shared_facts([task()])
    assert [f.value for f in out] == ["980", "2980"]


@pytest.mark.asyncio
async def test_same_scope_conflict_fails_closed():
    facts = [
        GroundedFact(fact_id="price", value="980", source_id="a", authority="canonical", condition_signature="basic"),
        GroundedFact(fact_id="price", value="999", source_id="b", authority="canonical", condition_signature="basic"),
    ]
    with pytest.raises(GroundingConflictError):
        await GroundingPlanner(Store(facts), Store([])).build_shared_facts([task()])
