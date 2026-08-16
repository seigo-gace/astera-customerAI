from runtime.contracts import SearchMode, SkillValidationState
from runtime.schemas import NeedTask
from runtime.search_planner import SearchPlanner
from runtime.skill_runtime import SkillQuery, SkillRegistry
from runtime.task_decomposition import TaskDecomposer
from runtime.writing_skills import default_writing_skills


def test_task_contract_and_three_search_modes():
    contract = TaskDecomposer().decompose("料金を比較して。\n設定方法も教えて。", {})
    assert len(contract.need_tasks) == 2
    planner = SearchPlanner()
    assert planner.plan(contract, SearchMode.RUNTIME_GROUNDING).mode == SearchMode.RUNTIME_GROUNDING
    assert planner.plan(contract, SearchMode.KB_HARVEST).verification_conditions[-1] == "never_auto_canon"
    assert planner.plan(contract, SearchMode.SKILL_SEARCH).source_priority == ["ACTIVE"]


def test_21_skills_active_and_bounded_selection():
    skills = default_writing_skills()
    assert len(skills) == 21
    assert all(s.state == SkillValidationState.ACTIVE for s in skills)
    task = TaskDecomposer().decompose("設定方法を説明して", {}).need_tasks[0]
    picked = SkillRegistry(skills).select(
        SkillQuery(language="ja", audience="general", tasks=(task,), has_evidence=True, text_length=100)
    )
    assert 1 <= len(picked) <= 8
    assert "ja-writing-preset" in {p.skill_id for p in picked}


def test_fast_task_limit_never_silently_drops_needs():
    contract = TaskDecomposer(max_fast_tasks=2).decompose("A？ B？ C？", {})
    assert len(contract.need_tasks) == 1
    assert contract.need_tasks[0].text == "A？ B？ C？"
    assert TaskDecomposer(max_fast_tasks=2).requires_semantic_expansion(contract)
    assert "semantic_decomposition_required" in contract.constraints


def test_semantic_expansion_falls_back_when_original_need_is_dropped():
    decomposer = TaskDecomposer()
    seed = decomposer.decompose("料金を比較して、さらに設定方法も教えて", {})
    dropped = seed.model_copy(
        update={
            "target": "料金を比較して",
            "constraints": [],
            "need_tasks": [
                NeedTask(
                    task_id="t1",
                    text="料金を比較して",
                    intent="comparison",
                    required_facts=["evidence_required"],
                    completion_condition="resolve:1",
                    response_shape="comparison",
                )
            ],
            "completion_conditions": ["resolve:1"],
        }
    )

    guarded = decomposer.protect_semantic_expansion(seed, dropped)

    assert guarded.target == seed.target
    assert len(guarded.need_tasks) == 1
    assert guarded.need_tasks[0].text == seed.target


def test_semantic_expansion_is_accepted_only_with_full_source_coverage():
    decomposer = TaskDecomposer()
    seed = decomposer.decompose("料金を比較して、さらに設定方法も教えて", {})
    complete = seed.model_copy(
        update={
            "target": seed.target,
            "constraints": [],
            "need_tasks": [
                NeedTask(
                    task_id="t1",
                    text="料金を比較して",
                    intent="comparison",
                    required_facts=["evidence_required"],
                    completion_condition="resolve:1",
                    response_shape="comparison",
                ),
                NeedTask(
                    task_id="t2",
                    text="設定方法も教えて",
                    intent="procedure",
                    required_facts=["evidence_required"],
                    completion_condition="resolve:2",
                    priority="secondary",
                    response_shape="procedure",
                    actionability_required=True,
                ),
            ],
            "completion_conditions": ["resolve:1", "resolve:2"],
        }
    )

    guarded = decomposer.protect_semantic_expansion(seed, complete)

    assert [task.task_id for task in guarded.need_tasks] == ["t1", "t2"]
    assert guarded.target == seed.target
