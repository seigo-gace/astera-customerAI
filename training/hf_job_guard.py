from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def failure_fingerprint(error_text: str) -> str:
    normalized = " ".join(str(error_text or "").strip().lower().split())
    if not normalized:
        raise ValueError("failure_text_required")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HFTrainingLaunchGuard:
    benchmark_candidate_id: str
    base_model_id: str
    base_revision: str
    adapter_mode: str
    lora_r: int | None
    lora_alpha: int | None
    lora_dropout: float | None
    lora_target_modules: tuple[str, ...]
    train_dataset_sha256: str
    eval_dataset_sha256: str
    result_repo_id: str
    model_repo_id: str
    run_id: str
    previous_job_id: str = ""
    previous_evidence_ref: str = ""
    previous_failure_fingerprint: str = ""
    fix_evidence_sha256: str = ""

    def validate(self) -> None:
        if not self.benchmark_candidate_id.strip():
            raise ValueError("benchmark_candidate_required_before_training")
        if not self.base_model_id.strip() or not self.base_revision.strip():
            raise ValueError("base_model_and_revision_must_be_pinned_before_training")
        if self.adapter_mode != "lora":
            raise ValueError("lora_adapter_required_before_training")
        if self.lora_r is None or self.lora_r <= 0:
            raise ValueError("lora_r_required_before_training")
        if self.lora_alpha is None or self.lora_alpha <= 0:
            raise ValueError("lora_alpha_required_before_training")
        if self.lora_dropout is None or not 0.0 <= self.lora_dropout < 1.0:
            raise ValueError("lora_dropout_required_before_training")
        if not self.lora_target_modules or any(not item.strip() for item in self.lora_target_modules):
            raise ValueError("lora_target_modules_required_before_training")
        if not _SHA256_RE.fullmatch(self.train_dataset_sha256):
            raise ValueError("train_dataset_sha256_required")
        if not _SHA256_RE.fullmatch(self.eval_dataset_sha256):
            raise ValueError("eval_dataset_sha256_required")
        if not self.result_repo_id.strip():
            raise ValueError("result_repo_required_before_training")
        if not self.model_repo_id.strip():
            raise ValueError("model_repo_required_before_training")
        if not _RUN_ID_RE.fullmatch(self.run_id):
            raise ValueError("run_id_required_before_training")

        if self.previous_failure_fingerprint:
            if not (self.previous_job_id.strip() or self.previous_evidence_ref.strip()):
                raise ValueError("previous_attempt_reference_required_for_failed_retry")
            if not _SHA256_RE.fullmatch(self.previous_failure_fingerprint):
                raise ValueError("previous_failure_fingerprint_invalid")
            if not _SHA256_RE.fullmatch(self.fix_evidence_sha256):
                raise ValueError("repeat_failure_without_fix_evidence_blocked")
        elif self.fix_evidence_sha256:
            raise ValueError("fix_evidence_without_previous_failure")


def require_new_failure_state(
    *,
    previous_failure_fingerprint: str,
    proposed_failure_fingerprint: str,
    fix_evidence_sha256: str,
) -> None:
    previous = previous_failure_fingerprint.strip().lower()
    proposed = proposed_failure_fingerprint.strip().lower()
    if not previous or not proposed:
        raise ValueError("failure_fingerprint_required")
    if previous == proposed and not _SHA256_RE.fullmatch(fix_evidence_sha256.strip().lower()):
        raise ValueError("same_failure_retry_blocked")
