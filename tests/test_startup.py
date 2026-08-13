import json

import pytest

from runtime.service import CustomerAIWork
from runtime.startup import RuntimeNotReady, create_work_from_environment


def test_startup_fails_closed_until_kb_snapshot_exists(tmp_path):
    with pytest.raises(RuntimeNotReady, match="kb_snapshot_missing"):
        create_work_from_environment(
            {"CUSTOMER_AI_KB_SNAPSHOT_PATH": str(tmp_path / "missing.jsonl")},
            role_pool=object(),
        )


def test_startup_builds_repository_runtime_when_kb_snapshot_is_present(tmp_path):
    kb = tmp_path / "kb.jsonl"
    kb.write_text(
        json.dumps(
            {
                "fact_id": "f1",
                "value": "Astera knowledge",
                "source_id": "source-1",
                "authority": "canonical",
                "title": "Astera",
                "public": True,
                "lifecycle_status": "active",
                "access_scope": "FREE",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    work = create_work_from_environment(
        {
            "CUSTOMER_AI_KB_SNAPSHOT_PATH": str(kb),
            "CUSTOMER_AI_KB_GENERATION_ID": "g-test",
            "CUSTOMER_AI_JA_FUZZY_THRESHOLD": "90",
        },
        role_pool=object(),
    )
    assert isinstance(work, CustomerAIWork)
    assert work.core.grounding.canonical.generation_id == "g-test"


def test_optional_current_fact_path_is_validated(tmp_path):
    kb = tmp_path / "kb.jsonl"
    kb.write_text(
        '{"fact_id":"f1","value":"v","source_id":"s","authority":"canonical"}\n',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeNotReady, match="current_facts_missing"):
        create_work_from_environment(
            {
                "CUSTOMER_AI_KB_SNAPSHOT_PATH": str(kb),
                "CUSTOMER_AI_CURRENT_FACTS_PATH": str(tmp_path / "missing-current.jsonl"),
            },
            role_pool=object(),
        )
