from __future__ import annotations

from dataclasses import dataclass

from .hf_evidence import HFTrainingEvidenceContext, HubEvidenceSink, build_training_evidence
from .hf_job_guard import HFTrainingLaunchGuard
from .train_sft import SFTTrainingRequest, train_sft


@dataclass(frozen=True)
class HFTrainingLaunchRequest:
    training: SFTTrainingRequest
    guard: HFTrainingLaunchGuard

    def validate(self) -> None:
        self.guard.validate()
        self.training.validate()
        if self.training.base_model_id != self.guard.base_model_id:
            raise ValueError("training_guard_model_mismatch")
        if self.training.base_revision != self.guard.base_revision:
            raise ValueError("training_guard_revision_mismatch")
        if self.training.adapter_mode != self.guard.adapter_mode:
            raise ValueError("training_guard_adapter_mismatch")
        if self.training.lora_r != self.guard.lora_r:
            raise ValueError("training_guard_lora_r_mismatch")
        if self.training.lora_alpha != self.guard.lora_alpha:
            raise ValueError("training_guard_lora_alpha_mismatch")
        if self.training.lora_dropout != self.guard.lora_dropout:
            raise ValueError("training_guard_lora_dropout_mismatch")
        if tuple(self.training.lora_target_modules) != tuple(self.guard.lora_target_modules):
            raise ValueError("training_guard_lora_target_modules_mismatch")

    def evidence_context(self) -> HFTrainingEvidenceContext:
        return HFTrainingEvidenceContext(
            run_id=self.guard.run_id,
            result_repo_id=self.guard.result_repo_id,
            model_repo_id=self.guard.model_repo_id,
            benchmark_candidate_id=self.guard.benchmark_candidate_id,
            base_model_id=self.guard.base_model_id,
            base_revision=self.guard.base_revision,
            train_dataset_sha256=self.guard.train_dataset_sha256,
            eval_dataset_sha256=self.guard.eval_dataset_sha256,
        )


def launch_training(
    request: HFTrainingLaunchRequest,
    *,
    evidence_sink: HubEvidenceSink | None = None,
) -> None:
    """Canonical HF training entrypoint with provider-independent durable evidence."""

    request.validate()
    sink = evidence_sink or HubEvidenceSink()
    context = request.evidence_context()

    # Fail closed before expensive compute if durable observability is unavailable.
    sink.persist(
        build_training_evidence(
            context,
            status="started",
            output_dir=request.training.output_dir,
        )
    )
    try:
        train_sft(request.training)
        model_artifact_ref = sink.persist_model_folder(
            output_dir=request.training.output_dir,
            model_repo_id=request.guard.model_repo_id,
            run_id=request.guard.run_id,
        )
    except Exception as error:
        try:
            sink.persist(
                build_training_evidence(
                    context,
                    status="failed",
                    output_dir=request.training.output_dir,
                    error=error,
                )
            )
        except Exception:
            raise RuntimeError("training_failed_and_failure_evidence_persistence_failed") from error
        raise

    # A run is not successful until both the model and final evidence are durable.
    sink.persist(
        build_training_evidence(
            context,
            status="succeeded",
            output_dir=request.training.output_dir,
            model_artifact_ref=model_artifact_ref,
        )
    )
