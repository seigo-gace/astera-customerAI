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


def test_local_injected_runtime_fails_closed_until_kb_snapshot_exists(tmp_path):
    with pytest.raises(RuntimeNotReady, match="kb_snapshot_missing"):
        create_work_from_environment(
            {"CUSTOMER_AI_KB_SNAPSHOT_PATH": str(tmp_path / "missing.jsonl")},
            role_pool=object(),
        )


def test_local_injected_runtime_builds_with_explicit_snapshot(tmp_path):
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


def test_local_optional_current_fact_path_is_validated(tmp_path):
    kb = _write_kb(tmp_path)
    with pytest.raises(RuntimeNotReady, match="current_facts_missing"):
        create_work_from_environment(
            {
                "CUSTOMER_AI_KB_SNAPSHOT_PATH": str(kb),
                "CUSTOMER_AI_CURRENT_FACTS_PATH": str(tmp_path / "missing-current.jsonl"),
            },
            role_pool=object(),
        )


def test_production_requires_hf_token_before_private_bucket_access():
    with pytest.raises(RuntimeNotReady, match="hf_token_missing"):
        create_work_from_environment({})


def test_production_requires_pinned_private_bucket_revision():
    with pytest.raises(RuntimeNotReady, match="kb_bucket_revision_missing"):
        create_work_from_environment({"HF_TOKEN": "test-token"})


def test_production_downloads_private_bucket_not_local_snapshot(tmp_path, monkeypatch):
    kb = _write_kb(tmp_path)
    calls = []

    def fake_download(*, repo_id, revision, filename, token):
        calls.append((repo_id, revision, filename, token))
        return kb

    monkeypatch.setattr("runtime.startup.download_private_bucket_file", fake_download)

    work = create_work_from_environment(
        {
            "HF_TOKEN": "test-token",
            "CUSTOMER_AI_KB_REPO_ID": "G-ACE/astera-customerai-kb",
            "CUSTOMER_AI_KB_REVISION": "bucket-revision",
            "CUSTOMER_AI_KB_CANONICAL_FILE": "canonical.jsonl",
            "CUSTOMER_AI_KB_SNAPSHOT_PATH": str(tmp_path / "must-not-be-used.jsonl"),
        }
    )

    assert isinstance(work, CustomerAIWork)
    assert work.core.grounding.canonical.generation_id == "bucket-revision"
    assert calls == [
        (
            "G-ACE/astera-customerai-kb",
            "bucket-revision",
            "canonical.jsonl",
            "test-token",
        )
    ]


def test_production_bucket_download_failure_is_fail_closed(monkeypatch):
    def broken_download(**_):
        raise RuntimeError("not found")

    monkeypatch.setattr("runtime.startup.download_private_bucket_file", broken_download)
    with pytest.raises(RuntimeNotReady, match="kb_bucket_canonical_download_failed"):
        create_work_from_environment(
            {
                "HF_TOKEN": "test-token",
                "CUSTOMER_AI_KB_REVISION": "bucket-revision",
            }
        )
