import json

import pytest

from runtime.kb_bucket import load_mounted_kb_release


def _write_release(tmp_path, build_id="kb-test"):
    release = tmp_path / "releases" / build_id
    release.mkdir(parents=True)
    canonical = release / "canonical.jsonl"
    canonical.write_text('{"fact_id":"f1","value":"v","source_id":"s"}\n', encoding="utf-8")
    current = release / "current-facts.jsonl"
    current.write_text('{"fact_id":"c1","value":"cv","source_id":"s"}\n', encoding="utf-8")
    aliases = release / "aliases.json"
    aliases.write_text('{"Astera":["アステラ"]}\n', encoding="utf-8")
    active = {
        "schema_version": 1,
        "build_id": build_id,
        "canonical_path": f"releases/{build_id}/canonical.jsonl",
        "current_facts_path": f"releases/{build_id}/current-facts.jsonl",
        "aliases_path": f"releases/{build_id}/aliases.json",
        "manifest_path": f"releases/{build_id}/manifest.json",
    }
    (tmp_path / "active.json").write_text(
        json.dumps(active, ensure_ascii=False),
        encoding="utf-8",
    )
    return canonical, current, aliases


def test_mounted_storage_bucket_requires_mount(tmp_path):
    with pytest.raises(ValueError, match="kb_bucket_mount_missing"):
        load_mounted_kb_release(
            mount_path=str(tmp_path / "missing"),
            expected_build_id="kb-test",
        )


def test_mounted_storage_bucket_requires_active_pointer(tmp_path):
    with pytest.raises(ValueError, match="kb_active_pointer_missing"):
        load_mounted_kb_release(
            mount_path=str(tmp_path),
            expected_build_id="kb-test",
        )


def test_mounted_storage_bucket_rejects_build_id_mismatch(tmp_path):
    _write_release(tmp_path, build_id="kb-actual")
    with pytest.raises(ValueError, match="kb_active_build_id_mismatch"):
        load_mounted_kb_release(
            mount_path=str(tmp_path),
            expected_build_id="kb-expected",
        )


def test_mounted_storage_bucket_rejects_path_escape(tmp_path):
    outside = tmp_path.parent / "outside.jsonl"
    outside.write_text('{}\n', encoding="utf-8")
    (tmp_path / "active.json").write_text(
        json.dumps(
            {
                "build_id": "kb-test",
                "canonical_path": "../outside.jsonl",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="kb_pointer_path_escape"):
        load_mounted_kb_release(
            mount_path=str(tmp_path),
            expected_build_id="kb-test",
        )


def test_mounted_storage_bucket_resolves_active_release(tmp_path):
    canonical, current, aliases = _write_release(tmp_path)
    release = load_mounted_kb_release(
        mount_path=str(tmp_path),
        expected_build_id="kb-test",
    )
    assert release.build_id == "kb-test"
    assert release.canonical_path == canonical.resolve()
    assert release.current_facts_path == current.resolve()
    assert release.aliases_path == aliases.resolve()
