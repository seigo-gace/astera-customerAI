import pytest

from runtime.contracts import SearchMode
from runtime.kb_search import KnowledgeRecord, LocalHybridKnowledgeStore
from runtime.search_planner import SearchPlanner
from runtime.task_decomposition import TaskDecomposer


def record(fid, title, value, **kwargs):
    return KnowledgeRecord(
        fact_id=fid,
        title=title,
        value=value,
        source_id=f"src-{fid}",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_fast_exact_alias_and_scope_gate():
    store = LocalHybridKnowledgeStore(
        [
            record("price", "Pro料金", "2,980円", aliases=("pro", "プロ"), knowledge_key="plan.pro.price"),
            record("legacy", "旧Pro料金", "old", aliases=("旧pro",), legacy=True),
            record("private", "内部価格", "secret", aliases=("内部価格",), access_scope="PRIVATE"),
        ],
        generation_id="g1",
    )
    task = TaskDecomposer().decompose("Pro", {}).need_tasks
    plan = SearchPlanner().plan(TaskDecomposer().decompose("Pro", {}), SearchMode.RUNTIME_GROUNDING)
    facts = await store.find_for_tasks(task, plan)
    assert [fact.fact_id for fact in facts] == ["price"]


@pytest.mark.asyncio
async def test_need_task_hybrid_search_does_not_mix_unrelated_topics():
    store = LocalHybridKnowledgeStore(
        [
            record("billing", "追加クレジット", "追加Creditは購入可能", domain="commercial", topic="credit"),
            record("api", "Developer API認証", "APIは認証が必要", domain="developer", topic="api"),
            record("vault", "Vault暗号化", "Vaultで暗号化", domain="security", topic="vault"),
        ],
        generation_id="g1",
        top_k=1,
    )
    contract = TaskDecomposer().decompose("追加クレジットを教えて。API認証も教えて。", {})
    plan = SearchPlanner().plan(contract, SearchMode.RUNTIME_GROUNDING)
    facts = await store.find_for_tasks(contract.need_tasks, plan)
    assert {fact.fact_id for fact in facts} == {"billing", "api"}


@pytest.mark.asyncio
async def test_deep_relation_expansion_is_bounded_and_preserves_metadata():
    store = LocalHybridKnowledgeStore(
        [
            record("basic", "Basicプラン比較", "Basic", aliases=("basic",), relations=("pro",), conditions=("c1",)),
            record("pro", "Proプラン", "Pro", aliases=("pro",), exceptions=("e1",)),
            record("other", "Enterprise", "Enterprise"),
        ],
        generation_id="g1",
        top_k=1,
        relation_limit=1,
    )
    contract = TaskDecomposer().decompose("Basicを比較", {})
    plan = SearchPlanner().plan(contract, SearchMode.RUNTIME_GROUNDING)
    facts = await store.find_for_tasks(contract.need_tasks, plan)
    assert [fact.fact_id for fact in facts] == ["basic", "pro"]
    assert facts[0].conditions == ["c1"]
    assert facts[1].exceptions == ["e1"]


@pytest.mark.asyncio
async def test_generation_replace_clears_stale_query_cache():
    store = LocalHybridKnowledgeStore(
        [record("price", "Pro", "old", aliases=("pro",))],
        generation_id="g1",
    )
    contract = TaskDecomposer().decompose("Pro", {})
    plan = SearchPlanner().plan(contract, SearchMode.RUNTIME_GROUNDING)
    first = await store.find_for_tasks(contract.need_tasks, plan)
    assert first[0].value == "old"
    assert store._cache
    store.replace_generation(
        [record("price", "Pro", "new", aliases=("pro",))],
        generation_id="g2",
    )
    assert not store._cache
    second = await store.find_for_tasks(contract.need_tasks, plan)
    assert second[0].value == "new"
