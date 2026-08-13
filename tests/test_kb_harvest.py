from runtime.contracts import SearchMode
from runtime.kb_harvest import KBHarvester
from runtime.search_planner import SearchPlanner
from runtime.task_decomposition import TaskDecomposer

def test_harvest_never_auto_promotes_to_canon():
    plan=SearchPlanner().plan(TaskDecomposer().decompose("Asteraの仕様",{}),SearchMode.KB_HARVEST)
    item=KBHarvester().make_candidate(result={"statement":"x","source_uri":"https://example.test","conditions":["c"],"exceptions":["e"]},plan=plan)
    assert item.canonical is False
    assert item.conditions==["c"] and item.exceptions==["e"]
