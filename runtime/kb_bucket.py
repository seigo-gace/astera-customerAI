from __future__ import annotations

from pathlib import Path

from huggingface_hub import hf_hub_download

HF_KB_BUCKET_DEFAULT = "G-ACE/astera-customerai-kb"
HF_KB_FILE_DEFAULT = "canonical.jsonl"


def download_private_bucket_file(
    *,
    repo_id: str,
    revision: str,
    filename: str,
    token: str,
) -> Path:
    if not repo_id.strip():
        raise ValueError("kb_bucket_repo_id_required")
    if not revision.strip():
        raise ValueError("kb_bucket_revision_required")
    if not filename.strip():
        raise ValueError("kb_bucket_filename_required")
    if not token.strip():
        raise ValueError("hf_token_required")

    path = hf_hub_download(
        repo_id=repo_id.strip(),
        repo_type="dataset",
        revision=revision.strip(),
        filename=filename.strip(),
        token=token.strip(),
    )
    resolved = Path(path)
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ValueError("kb_bucket_download_invalid")
    return resolved
