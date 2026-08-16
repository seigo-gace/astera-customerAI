from __future__ import annotations

import hashlib
import io
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from .hf_job_guard import failure_fingerprint

_STATUS = Literal["started", "candidate_completed", "succeeded", "failed"]
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_RE = re.compile(r"(?:token|secret|password|credential|api[_-]?key)", re.IGNORECASE)


@dataclass(frozen=True)
class HFBenchmarkCandidateResult:
    candidate_id: str
    parameters: Mapping[str, str | int | float | bool]
    metrics: Mapping[str, int | float]

    def validate(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("benchmark_candidate_id_required")
        if not self.parameters:
            raise ValueError("benchmark_candidate_parameters_required")
        if any(_SECRET_KEY_RE.search(str(key)) for key in self.parameters):
            raise ValueError("benchmark_candidate_secret_like_parameter_forbidden")
        if not self.metrics:
            raise ValueError("benchmark_candidate_metrics_required")
        for key, value in self.metrics.items():
            if not str(key).strip() or isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("benchmark_candidate_metric_invalid")

    def eval_loss(self) -> float:
        self.validate()
        value = self.metrics.get("eval_loss")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("benchmark_eval_loss_required")
        return float(value)


@dataclass(frozen=True)
class HFBenchmarkEvidenceContext:
    run_id: str
    result_repo_id: str
    base_model_id: str
    base_revision: str
    train_dataset_sha256: str
    eval_dataset_sha256: str
    selection_metric: str = "eval_loss"

    def validate(self) -> None:
        if not _RUN_ID_RE.fullmatch(self.run_id):
            raise ValueError("benchmark_evidence_run_id_invalid")
        if not self.result_repo_id.strip():
            raise ValueError("benchmark_evidence_result_repo_required")
        if not self.base_model_id.strip() or not self.base_revision.strip():
            raise ValueError("benchmark_evidence_model_pin_required")
        if not _SHA256_RE.fullmatch(self.train_dataset_sha256):
            raise ValueError("benchmark_train_dataset_sha256_required")
        if not _SHA256_RE.fullmatch(self.eval_dataset_sha256):
            raise ValueError("benchmark_eval_dataset_sha256_required")
        if self.selection_metric != "eval_loss":
            raise ValueError("benchmark_selection_metric_must_be_eval_loss")


@dataclass(frozen=True)
class HFBenchmarkEvidence:
    schema_version: str
    run_id: str
    status: _STATUS
    observed_at_utc: str
    provider_job_id: str | None
    result_repo_id: str
    base_model_id: str
    base_revision: str
    train_dataset_sha256: str
    eval_dataset_sha256: str
    selection_metric: str
    candidates: tuple[HFBenchmarkCandidateResult, ...]
    best_candidate_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    failure_fingerprint: str | None = None

    def validate(self) -> None:
        ids: set[str] = set()
        for candidate in self.candidates:
            candidate.validate()
            if candidate.candidate_id in ids:
                raise ValueError("benchmark_candidate_id_duplicate")
            ids.add(candidate.candidate_id)
        if self.status == "candidate_completed" and not self.candidates:
            raise ValueError("benchmark_candidate_completed_requires_result")
        if self.status == "succeeded":
            if len(self.candidates) < 2:
                raise ValueError("benchmark_success_requires_multiple_candidates")
            expected = select_best_by_eval_loss(self.candidates).candidate_id
            if self.best_candidate_id != expected:
                raise ValueError("benchmark_best_candidate_mismatch")
        elif self.best_candidate_id is not None:
            raise ValueError("benchmark_best_candidate_only_valid_on_success")
        if self.status == "failed" and not self.failure_fingerprint:
            raise ValueError("benchmark_failure_fingerprint_required")

    def to_json(self) -> str:
        self.validate()
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def provider_job_id_from_env() -> str | None:
    for name in ("HF_JOB_ID", "JOB_ID", "JOB_UUID"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def select_best_by_eval_loss(candidates: tuple[HFBenchmarkCandidateResult, ...]) -> HFBenchmarkCandidateResult:
    if len(candidates) < 2:
        raise ValueError("benchmark_requires_multiple_candidates")
    return min(candidates, key=lambda item: (item.eval_loss(), item.candidate_id))


def build_benchmark_evidence(
    context: HFBenchmarkEvidenceContext,
    *,
    status: _STATUS,
    candidates: tuple[HFBenchmarkCandidateResult, ...] = (),
    best_candidate_id: str | None = None,
    error: BaseException | None = None,
) -> HFBenchmarkEvidence:
    context.validate()
    if status == "failed" and error is None:
        raise ValueError("benchmark_failure_error_required")
    if status != "failed" and error is not None:
        raise ValueError("benchmark_error_only_valid_on_failure")
    error_type = type(error).__name__ if error is not None else None
    error_message = str(error)[:2000] if error is not None else None
    fingerprint = failure_fingerprint(f"{error_type}: {error_message}") if error is not None else None
    evidence = HFBenchmarkEvidence(
        schema_version="astera.customer-ai.hf-benchmark-evidence.v1",
        run_id=context.run_id,
        status=status,
        observed_at_utc=datetime.now(timezone.utc).isoformat(),
        provider_job_id=provider_job_id_from_env(),
        result_repo_id=context.result_repo_id,
        base_model_id=context.base_model_id,
        base_revision=context.base_revision,
        train_dataset_sha256=context.train_dataset_sha256,
        eval_dataset_sha256=context.eval_dataset_sha256,
        selection_metric=context.selection_metric,
        candidates=candidates,
        best_candidate_id=best_candidate_id,
        error_type=error_type,
        error_message=error_message,
        failure_fingerprint=fingerprint,
    )
    evidence.validate()
    return evidence


class HubBenchmarkEvidenceSink:
    """Persist benchmark lifecycle/results independently from the HF Jobs list/log API."""

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

    def persist(self, evidence: HFBenchmarkEvidence) -> str:
        payload = evidence.to_json().encode("utf-8")
        api = self._api_client()
        api.create_repo(repo_id=evidence.result_repo_id, repo_type="dataset", private=True, exist_ok=True)
        digest = hashlib.sha256(payload).hexdigest()[:16]
        prefix = f"benchmark-runs/{evidence.run_id}"
        immutable_path = f"{prefix}/events/{evidence.status}-{digest}.json"
        latest_path = f"{prefix}/latest.json"
        for path in (immutable_path, latest_path):
            api.upload_file(
                path_or_fileobj=io.BytesIO(payload),
                path_in_repo=path,
                repo_id=evidence.result_repo_id,
                repo_type="dataset",
                commit_message=f"benchmark evidence {evidence.run_id} {evidence.status}",
            )
        return f"hf://datasets/{evidence.result_repo_id}/{immutable_path}"


def persist_success_then_format_stdout(
    sink: HubBenchmarkEvidenceSink,
    evidence: HFBenchmarkEvidence,
) -> tuple[str, str]:
    if evidence.status != "succeeded":
        raise ValueError("benchmark_stdout_requires_success_evidence")
    evidence_ref = sink.persist(evidence)
    payload = {
        "schema_version": evidence.schema_version,
        "run_id": evidence.run_id,
        "best_candidate_id": evidence.best_candidate_id,
        "selection_metric": evidence.selection_metric,
        "candidates": [asdict(item) for item in evidence.candidates],
        "evidence_ref": evidence_ref,
    }
    return evidence_ref, "ASTERA_BENCHMARK_RESULT=" + json.dumps(payload, ensure_ascii=False, sort_keys=True)
