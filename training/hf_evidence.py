from __future__ import annotations

import hashlib
import io
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from .hf_job_guard import failure_fingerprint

_STATUS = Literal["started", "succeeded", "failed"]
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


@dataclass(frozen=True)
class HFTrainingEvidenceContext:
    run_id: str
    result_repo_id: str
    model_repo_id: str
    benchmark_candidate_id: str
    base_model_id: str
    base_revision: str
    train_dataset_sha256: str
    eval_dataset_sha256: str

    def validate(self) -> None:
        if not _RUN_ID_RE.fullmatch(self.run_id):
            raise ValueError("evidence_run_id_invalid")
        if not self.result_repo_id.strip():
            raise ValueError("evidence_result_repo_required")
        if not self.model_repo_id.strip():
            raise ValueError("evidence_model_repo_required")
        if not self.benchmark_candidate_id.strip():
            raise ValueError("evidence_benchmark_candidate_required")
        if not self.base_model_id.strip() or not self.base_revision.strip():
            raise ValueError("evidence_model_pin_required")


@dataclass(frozen=True)
class HFTrainingEvidence:
    schema_version: str
    run_id: str
    status: _STATUS
    observed_at_utc: str
    provider_job_id: str | None
    result_repo_id: str
    model_repo_id: str
    benchmark_candidate_id: str
    base_model_id: str
    base_revision: str
    train_dataset_sha256: str
    eval_dataset_sha256: str
    output_dir: str
    model_artifact_ref: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    failure_fingerprint: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def provider_job_id_from_env() -> str | None:
    for name in ("HF_JOB_ID", "JOB_ID", "JOB_UUID"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def build_training_evidence(
    context: HFTrainingEvidenceContext,
    *,
    status: _STATUS,
    output_dir: str,
    model_artifact_ref: str | None = None,
    error: BaseException | None = None,
) -> HFTrainingEvidence:
    context.validate()
    error_type = type(error).__name__ if error is not None else None
    error_message = str(error)[:2000] if error is not None else None
    fingerprint = (
        failure_fingerprint(f"{error_type}: {error_message}") if error is not None else None
    )
    return HFTrainingEvidence(
        schema_version="astera.customer-ai.hf-training-evidence.v1",
        run_id=context.run_id,
        status=status,
        observed_at_utc=datetime.now(timezone.utc).isoformat(),
        provider_job_id=provider_job_id_from_env(),
        result_repo_id=context.result_repo_id,
        model_repo_id=context.model_repo_id,
        benchmark_candidate_id=context.benchmark_candidate_id,
        base_model_id=context.base_model_id,
        base_revision=context.base_revision,
        train_dataset_sha256=context.train_dataset_sha256,
        eval_dataset_sha256=context.eval_dataset_sha256,
        output_dir=output_dir,
        model_artifact_ref=model_artifact_ref,
        error_type=error_type,
        error_message=error_message,
        failure_fingerprint=fingerprint,
    )


class HubEvidenceSink:
    """Persist training lifecycle independently from the HF Jobs list API."""

    def __init__(self, api: Any | None = None):
        self._api = api

    def _api_client(self):
        if self._api is not None:
            return self._api
        try:
            from huggingface_hub import HfApi
        except ImportError as error:
            raise RuntimeError("huggingface_hub_not_installed") from error
        return HfApi(token=os.environ.get("HF_TOKEN"))

    def persist_model_folder(self, *, output_dir: str, model_repo_id: str, run_id: str) -> str:
        if not _RUN_ID_RE.fullmatch(run_id):
            raise ValueError("evidence_run_id_invalid")
        if not output_dir or not model_repo_id.strip():
            raise ValueError("model_persistence_target_required")
        api = self._api_client()
        api.create_repo(repo_id=model_repo_id, repo_type="model", private=True, exist_ok=True)
        api.upload_folder(
            folder_path=output_dir,
            repo_id=model_repo_id,
            repo_type="model",
            path_in_repo=f"training-runs/{run_id}",
            commit_message=f"training model artifact {run_id}",
        )
        return f"hf://models/{model_repo_id}/training-runs/{run_id}"

    def persist(self, evidence: HFTrainingEvidence) -> str:
        api = self._api_client()
        api.create_repo(
            repo_id=evidence.result_repo_id,
            repo_type="dataset",
            private=True,
            exist_ok=True,
        )
        payload = evidence.to_json().encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()[:16]
        prefix = f"training-runs/{evidence.run_id}"
        immutable_path = f"{prefix}/events/{evidence.status}-{digest}.json"
        latest_path = f"{prefix}/latest.json"
        for path in (immutable_path, latest_path):
            api.upload_file(
                path_or_fileobj=io.BytesIO(payload),
                path_in_repo=path,
                repo_id=evidence.result_repo_id,
                repo_type="dataset",
                commit_message=f"training evidence {evidence.run_id} {evidence.status}",
            )
        return f"hf://datasets/{evidence.result_repo_id}/{immutable_path}"
