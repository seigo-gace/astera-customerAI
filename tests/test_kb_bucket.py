from pathlib import Path

import pytest

from runtime.kb_bucket import download_private_bucket_file


def test_private_bucket_download_requires_pinned_revision():
    with pytest.raises(ValueError, match="kb_bucket_revision_required"):
        download_private_bucket_file(
            repo_id="G-ACE/astera-customerai-kb",
            revision="",
            filename="canonical.jsonl",
            token="token",
        )


def test_private_bucket_download_requires_token():
    with pytest.raises(ValueError, match="hf_token_required"):
        download_private_bucket_file(
            repo_id="G-ACE/astera-customerai-kb",
            revision="abc123",
            filename="canonical.jsonl",
            token="",
        )


def test_private_bucket_download_uses_private_dataset_contract(tmp_path, monkeypatch):
    target = tmp_path / "canonical.jsonl"
    target.write_text('{"fact_id":"f1","value":"v","source_id":"s"}\n', encoding="utf-8")
    calls = []

    def fake_hf_hub_download(**kwargs):
        calls.append(kwargs)
        return str(target)

    monkeypatch.setattr("runtime.kb_bucket.hf_hub_download", fake_hf_hub_download)

    resolved = download_private_bucket_file(
        repo_id="G-ACE/astera-customerai-kb",
        revision="fixed-revision",
        filename="canonical.jsonl",
        token="secret-token",
    )

    assert resolved == Path(target)
    assert calls == [
        {
            "repo_id": "G-ACE/astera-customerai-kb",
            "repo_type": "dataset",
            "revision": "fixed-revision",
            "filename": "canonical.jsonl",
            "token": "secret-token",
        }
    ]
