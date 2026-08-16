import pytest
from training.hf_job_guard import HFTrainingLaunchGuard, failure_fingerprint, require_new_failure_state

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _guard(**overrides):
    values = {
        "benchmark_candidate_id": "candidate-B",
        "base_model_id": "Qwen/Qwen3-4B",
        "base_revision": "0123456789abcdef",
        "adapter_mode": "lora",
        "lora_r": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "lora_target_modules": ("q_proj", "k_proj", "v_proj", "o_proj"),
        "train_dataset_sha256": SHA_A,
        "eval_dataset_sha256": SHA_B,
        "result_repo_id": "G-ACE/astera-customerai-training-results",
        "model_repo_id": "G-ACE/astera-customerai-domain-model",
        "run_id": "customer-ai-sft-001",
    }
    values.update(overrides)
    return HFTrainingLaunchGuard(**values)


def test_clean_first_launch_passes():
    _guard().validate()


def test_failed_retry_without_any_attempt_reference_is_blocked():
    with pytest.raises(ValueError, match="previous_attempt_reference_required"):
        _guard(previous_failure_fingerprint=SHA_C, fix_evidence_sha256=SHA_A).validate()


def test_connector_blind_retry_can_use_durable_evidence_ref_instead_of_job_id():
    _guard(
        previous_evidence_ref="hf://datasets/G-ACE/results/training-runs/x/events/failed.json",
        previous_failure_fingerprint=SHA_C,
        fix_evidence_sha256=SHA_A,
    ).validate()


def test_failed_retry_without_fix_evidence_is_blocked():
    with pytest.raises(ValueError, match="repeat_failure_without_fix_evidence_blocked"):
        _guard(previous_job_id="job-123", previous_failure_fingerprint=SHA_C).validate()


def test_same_failure_state_without_fix_is_blocked():
    with pytest.raises(ValueError, match="same_failure_retry_blocked"):
        require_new_failure_state(previous_failure_fingerprint=SHA_C, proposed_failure_fingerprint=SHA_C, fix_evidence_sha256="")


def test_same_failure_state_with_fix_is_allowed():
    require_new_failure_state(previous_failure_fingerprint=SHA_C, proposed_failure_fingerprint=SHA_C, fix_evidence_sha256=SHA_A)


def test_failure_fingerprint_is_whitespace_and_case_stable():
    assert failure_fingerprint(" CUDA   OUT OF MEMORY ") == failure_fingerprint("cuda out of memory")


@pytest.mark.parametrize("field,value,message", [
    ("benchmark_candidate_id", "", "benchmark_candidate_required_before_training"),
    ("base_revision", "", "base_model_and_revision_must_be_pinned_before_training"),
    ("adapter_mode", "benchmark_required", "lora_adapter_required_before_training"),
    ("lora_r", None, "lora_r_required_before_training"),
    ("lora_alpha", None, "lora_alpha_required_before_training"),
    ("lora_dropout", None, "lora_dropout_required_before_training"),
    ("lora_target_modules", (), "lora_target_modules_required_before_training"),
    ("result_repo_id", "", "result_repo_required_before_training"),
    ("model_repo_id", "", "model_repo_required_before_training"),
    ("run_id", "bad/run", "run_id_required_before_training"),
])
def test_incomplete_launch_contract_fails_closed(field, value, message):
    with pytest.raises(ValueError, match=message):
        _guard(**{field: value}).validate()
