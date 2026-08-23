import json

import pytest

from runtime.service import CustomerAIWork
from runtime.startup import RuntimeNotReady, create_work_from_environment


def _write_kb(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
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
    return path


def _write_mounted_release(tmp_path, build_id="kb-test"):
    release_dir = tmp_path / "releases" / build_id
    canonical = _write_kb(release_dir / "canonical.jsonl")
    (release_dir / "manifest.json").write_text(
        json.dumps({"build_id": build_id, "files": []}),
        encoding="utf-8",
    )
    (tmp_path / "active.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "build_id": build_id,
                "canonical_path": f"releases/{build_id}/canonical.jsonl",
                "current_facts_path": None,
                "aliases_path": None,
                "manifest_path": f"releases/{build_id}/manifest.json",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return canonical


def test_local_injected_runtime_fails_closed_until_kb_snapshot_exists(tmp_path):
    with pytest.raises(RuntimeNotReady, match="kb_snapshot_missing"):
        create_work_from_environment(
            {"CUSTOMER_AI_KB_SNAPSHOT_PATH": str(tmp_path / "missing.jsonl")},
            role_pool=object(),
        )


def test_local_injected_runtime_builds_with_explicit_snapshot(tmp_path):
    kb = _write_kb(tmp_path / "kb.jsonl")
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
    kb = _write_kb(tmp_path / "kb.jsonl")
    with pytest.raises(RuntimeNotReady, match="current_facts_missing"):
        create_work_from_environment(
            {
                "CUSTOMER_AI_KB_SNAPSHOT_PATH": str(kb),
                "CUSTOMER_AI_CURRENT_FACTS_PATH": str(tmp_path / "missing-current.jsonl"),
            },
            role_pool=object(),
        )


def test_production_requires_expected_build_id():
    with pytest.raises(RuntimeNotReady, match="kb_build_id_missing"):
        create_work_from_environment({"HF_TOKEN": "test-token"})


def test_production_requires_private_bucket_token_when_mount_is_missing(tmp_path):
    with pytest.raises(RuntimeNotReady, match="hf_token_missing"):
        create_work_from_environment(
            {
                "CUSTOMER_AI_KB_BUILD_ID": "kb-test",
                "CUSTOMER_AI_KB_MOUNT_PATH": str(tmp_path / "missing"),
            }
        )


def test_production_uses_mounted_storage_bucket_not_local_snapshot(tmp_path):
    mount = tmp_path / "mounted-bucket"
    mount.mkdir()
    _write_mounted_release(mount, build_id="kb-test")

    work = create_work_from_environment(
        {
            "CUSTOMER_AI_KB_BUILD_ID": "kb-test",
            "CUSTOMER_AI_KB_MOUNT_PATH": str(mount),
            "CUSTOMER_AI_KB_SNAPSHOT_PATH": str(tmp_path / "must-not-be-used.jsonl"),
        }
    )

    assert isinstance(work, CustomerAIWork)
    assert work.core.grounding.canonical.generation_id == "kb-test"


def test_production_rejects_active_pointer_build_mismatch(tmp_path):
    mount = tmp_path / "mounted-bucket"
    mount.mkdir()
    _write_mounted_release(mount, build_id="kb-actual")

    with pytest.raises(RuntimeNotReady, match="kb_active_build_id_mismatch"):
        create_work_from_environment(
            {
                "CUSTOMER_AI_KB_BUILD_ID": "kb-expected",
                "CUSTOMER_AI_KB_MOUNT_PATH": str(mount),
            }
        )


def test_production_local_models_do_not_require_hf_inference_token_when_mount_is_valid(tmp_path):
    mount = tmp_path / "mounted-bucket"
    mount.mkdir()
    _write_mounted_release(mount, build_id="kb-test")

    work = create_work_from_environment(
        {
            "CUSTOMER_AI_KB_BUILD_ID": "kb-test",
            "CUSTOMER_AI_KB_MOUNT_PATH": str(mount),
        }
    )
    assert isinstance(work, CustomerAIWork)
