import pytest

from training.hf_job_guard import HFTrainingLaunchGuard
from training.hf_launch import HFTrainingLaunchRequest
from training.train_sft import SFTTrainingRequest


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _training(**overrides) -> SFTTrainingRequest:
    values = {
        "base_model_id": "Qwen/Qwen3-4B",
        "base_revision": "revision-sha",
        "output_dir": "out",
        "dataset_path": "train.jsonl",
        "adapter_mode": "lora",
        "lora_r": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "lora_target_modules": ("q_proj", "k_proj", "v_proj", "o_proj"),
    }
    values.update(overrides)
    return SFTTrainingRequest(**values)


def _guard(**overrides) -> HFTrainingLaunchGuard:
    values = {
        "benchmark_candidate_id": "candidate-B",
        "base_model_id": "Qwen/Qwen3-4B",
        "base_revision": "revision-sha",
        "adapter_mode": "lora",
        "lora_r": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "lora_target_modules": ("q_proj", "k_proj", "v_proj", "o_proj"),
        "train_dataset_sha256": SHA_A,
        "eval_dataset_sha256": SHA_B,
        "result_repo_id": "G-ACE/astera-customerai-training-results",
    }
    values.update(overrides)
    return HFTrainingLaunchGuard(**values)


def test_launch_request_validates_when_training_and_guard_match() -> None:
    HFTrainingLaunchRequest(training=_training(), guard=_guard()).validate()


def test_launch_request_blocks_failed_retry_without_fix_before_training() -> None:
    request = HFTrainingLaunchRequest(
        training=_training(),
        guard=_guard(previous_job_id="job-1", previous_failure_fingerprint=SHA_C),
    )
    with pytest.raises(ValueError, match="repeat_failure_without_fix_evidence_blocked"):
        request.validate()


def test_launch_request_rejects_revision_mismatch() -> None:
    request = HFTrainingLaunchRequest(
        training=_training(base_revision="revision-a"),
        guard=_guard(base_revision="revision-b"),
    )
    with pytest.raises(ValueError, match="training_guard_revision_mismatch"):
        request.validate()


def test_launch_request_rejects_lora_mismatch() -> None:
    request = HFTrainingLaunchRequest(
        training=_training(lora_r=8),
        guard=_guard(lora_r=16),
    )
    with pytest.raises(ValueError, match="training_guard_lora_r_mismatch"):
        request.validate()
