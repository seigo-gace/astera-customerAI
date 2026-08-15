import pytest

from training.train_sft import SFTTrainingRequest


def _request(**overrides) -> SFTTrainingRequest:
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


def test_complete_lora_request_is_valid() -> None:
    _request().validate()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("adapter_mode", "benchmark_required", "adapter_mode_must_be_decided_before_training"),
        ("lora_r", None, "lora_r_must_be_decided_before_training"),
        ("lora_alpha", None, "lora_alpha_must_be_decided_before_training"),
        ("lora_dropout", None, "lora_dropout_must_be_decided_before_training"),
        ("lora_target_modules", (), "lora_target_modules_must_be_decided_before_training"),
    ],
)
def test_incomplete_benchmark_decision_fails_closed(field, value, message) -> None:
    request = _request(**{field: value})
    with pytest.raises(ValueError, match=message):
        request.validate()


def test_unknown_adapter_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported_adapter_mode"):
        _request(adapter_mode="candidate_A").validate()
