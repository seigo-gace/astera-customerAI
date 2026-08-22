import json

import pytest

from runtime.service import CustomerAIWork
from runtime.startup import RuntimeNotReady, create_work_from_environment


def _write_kb(tmp_path):
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
    return kb


def test_startup_fails_closed_until_kb_snapshot_exists(tmp_path):
    with pytest.raises(RuntimeNotReady, match="kb_snapshot_missing"):
        create_work_from_environment(
            {"CUSTOMER_AI_KB_SNAPSHOT_PATH": str(tmp_path / "missing.jsonl")},
            role_pool=object(),
        )


def test_startup_builds_repository_runtime_when_kb_snapshot_is_present(tmp_path):
    kb = _write_kb(tmp_path)
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
    kb = _write_kb(tmp_path)
    with pytest.raises(RuntimeNotReady, match="current_facts_missing"):
        create_work_from_environment(
            {
                "CUSTOMER_AI_KB_SNAPSHOT_PATH": str(kb),
                "CUSTOMER_AI_CURRENT_FACTS_PATH": str(tmp_path / "missing-current.jsonl"),
            },
            role_pool=object(),
        )


def test_production_startup_requires_trained_model_configuration(tmp_path):
    kb = _write_kb(tmp_path)
    with pytest.raises(RuntimeNotReady, match="trained_model_4b_id_missing"):
        create_work_from_environment(
            {
                "CUSTOMER_AI_KB_SNAPSHOT_PATH": str(kb),
                "HF_TOKEN": "test-token",
            }
        )


def test_production_startup_rejects_base_model_fallback(tmp_path):
    kb = _write_kb(tmp_path)
    with pytest.raises(RuntimeNotReady, match="trained_domain_model_required"):
        create_work_from_environment(
            {
                "CUSTOMER_AI_KB_SNAPSHOT_PATH": str(kb),
                "HF_TOKEN": "test-token",
                "CUSTOMER_AI_MODEL_4B_ID": "Qwen/Qwen3-4B",
                "CUSTOMER_AI_MODEL_4B_REVISION": "rev-4b",
                "CUSTOMER_AI_MODEL_4B_API_URL": "https://private-4b.example/v1/chat/completions",
                "CUSTOMER_AI_MODEL_8B_ID": "Qwen/Qwen3-8B",
                "CUSTOMER_AI_MODEL_8B_REVISION": "rev-8b",
                "CUSTOMER_AI_MODEL_8B_API_URL": "https://private-8b.example/v1/chat/completions",
            }
        )


def test_production_startup_accepts_private_trained_endpoints(tmp_path):
    kb = _write_kb(tmp_path)
    work = create_work_from_environment(
        {
            "CUSTOMER_AI_KB_SNAPSHOT_PATH": str(kb),
            "CUSTOMER_AI_KB_GENERATION_ID": "g-trained",
            "HF_TOKEN": "test-token",
            "CUSTOMER_AI_MODEL_4B_ID": "G-ACE/astera-customerai-domain-4b",
            "CUSTOMER_AI_MODEL_4B_REVISION": "trained-rev-4b",
            "CUSTOMER_AI_MODEL_4B_API_URL": "https://private-4b.example/v1/chat/completions",
            "CUSTOMER_AI_MODEL_8B_ID": "G-ACE/astera-customerai-domain-8b",
            "CUSTOMER_AI_MODEL_8B_REVISION": "trained-rev-8b",
            "CUSTOMER_AI_MODEL_8B_API_URL": "https://private-8b.example/v1/chat/completions",
        }
    )
    assert isinstance(work, CustomerAIWork)
    assert work.core.grounding.canonical.generation_id == "g-trained"
