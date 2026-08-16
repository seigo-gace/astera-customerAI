import pytest
import training.hf_launch as hf_launch
from training.hf_job_guard import HFTrainingLaunchGuard
from training.hf_launch import HFTrainingLaunchRequest
from training.train_sft import SFTTrainingRequest

SHA_A = "a" * 64
SHA_B = "b" * 64


def _training(**overrides):
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


def _guard(**overrides):
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
        "result_repo_id": "G-ACE/training-results",
        "model_repo_id": "G-ACE/domain-model",
        "run_id": "sft-001",
    }
    values.update(overrides)
    return HFTrainingLaunchGuard(**values)


class MemorySink:
    def __init__(self):
        self.events = []
        self.models = []
    def persist(self, event):
        self.events.append(event)
        return f"memory://{event.run_id}/{event.status}"
    def persist_model_folder(self, *, output_dir, model_repo_id, run_id):
        self.models.append((output_dir, model_repo_id, run_id))
        return f"hf://models/{model_repo_id}/training-runs/{run_id}"


def test_launch_request_validates_when_training_and_guard_match():
    HFTrainingLaunchRequest(training=_training(), guard=_guard()).validate()


def test_launch_request_rejects_revision_mismatch():
    request = HFTrainingLaunchRequest(
        training=_training(base_revision="revision-a"), guard=_guard(base_revision="revision-b")
    )
    with pytest.raises(ValueError, match="training_guard_revision_mismatch"):
        request.validate()


def test_success_requires_started_evidence_model_artifact_and_success_evidence(monkeypatch):
    sink = MemorySink()
    monkeypatch.setattr(hf_launch, "train_sft", lambda request: None)
    hf_launch.launch_training(HFTrainingLaunchRequest(_training(), _guard()), evidence_sink=sink)
    assert [event.status for event in sink.events] == ["started", "succeeded"]
    assert sink.models == [("out", "G-ACE/domain-model", "sft-001")]
    assert sink.events[-1].model_artifact_ref == "hf://models/G-ACE/domain-model/training-runs/sft-001"


def test_failure_persists_failure_fingerprint_before_reraising(monkeypatch):
    sink = MemorySink()
    def boom(request):
        raise RuntimeError("CUDA OOM")
    monkeypatch.setattr(hf_launch, "train_sft", boom)
    with pytest.raises(RuntimeError, match="CUDA OOM"):
        hf_launch.launch_training(HFTrainingLaunchRequest(_training(), _guard()), evidence_sink=sink)
    assert [event.status for event in sink.events] == ["started", "failed"]
    assert sink.events[-1].failure_fingerprint
    assert sink.events[-1].error_type == "RuntimeError"
    assert sink.models == []


def test_training_never_starts_if_started_evidence_cannot_be_persisted(monkeypatch):
    class FailingSink:
        def persist(self, event):
            raise RuntimeError("hub unavailable")
    called = {"training": False}
    def fake_train(request):
        called["training"] = True
    monkeypatch.setattr(hf_launch, "train_sft", fake_train)
    with pytest.raises(RuntimeError, match="hub unavailable"):
        hf_launch.launch_training(HFTrainingLaunchRequest(_training(), _guard()), evidence_sink=FailingSink())
    assert called["training"] is False


def test_model_persistence_failure_is_recorded_as_failed_run(monkeypatch):
    class ModelFailSink(MemorySink):
        def persist_model_folder(self, *, output_dir, model_repo_id, run_id):
            raise RuntimeError("model upload failed")
    sink = ModelFailSink()
    monkeypatch.setattr(hf_launch, "train_sft", lambda request: None)
    with pytest.raises(RuntimeError, match="model upload failed"):
        hf_launch.launch_training(HFTrainingLaunchRequest(_training(), _guard()), evidence_sink=sink)
    assert [event.status for event in sink.events] == ["started", "failed"]
    assert sink.events[-1].failure_fingerprint
