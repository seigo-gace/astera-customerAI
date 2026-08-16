import json
from training.hf_evidence import HFTrainingEvidenceContext, HubEvidenceSink, build_training_evidence

SHA_A = "a" * 64
SHA_B = "b" * 64


def context():
    return HFTrainingEvidenceContext(
        run_id="sft-001",
        result_repo_id="G-ACE/training-results",
        model_repo_id="G-ACE/domain-model",
        benchmark_candidate_id="candidate-B",
        base_model_id="Qwen/Qwen3-4B",
        base_revision="revision-sha",
        train_dataset_sha256=SHA_A,
        eval_dataset_sha256=SHA_B,
    )


class FakeApi:
    def __init__(self):
        self.created = []
        self.uploads = []
        self.folders = []
    def create_repo(self, **kwargs):
        self.created.append(kwargs)
    def upload_file(self, **kwargs):
        payload = kwargs["path_or_fileobj"].getvalue().decode("utf-8")
        self.uploads.append((kwargs, json.loads(payload)))
    def upload_folder(self, **kwargs):
        self.folders.append(kwargs)


def test_failure_evidence_contains_fingerprint_but_no_token_material(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "super-secret-token")
    evidence = build_training_evidence(context(), status="failed", output_dir="out", error=RuntimeError("CUDA OOM"))
    payload = evidence.to_json()
    assert evidence.failure_fingerprint
    assert "super-secret-token" not in payload
    assert "HF_TOKEN" not in payload


def test_sink_writes_immutable_event_and_latest_pointer():
    api = FakeApi()
    evidence = build_training_evidence(context(), status="started", output_dir="out")
    ref = HubEvidenceSink(api=api).persist(evidence)
    paths = [item[0]["path_in_repo"] for item in api.uploads]
    assert any(path.startswith("training-runs/sft-001/events/started-") for path in paths)
    assert "training-runs/sft-001/latest.json" in paths
    assert ref.startswith("hf://datasets/G-ACE/training-results/training-runs/sft-001/events/started-")


def test_sink_persists_model_under_run_specific_path():
    api = FakeApi()
    ref = HubEvidenceSink(api=api).persist_model_folder(
        output_dir="out", model_repo_id="G-ACE/domain-model", run_id="sft-001"
    )
    assert api.folders[0]["folder_path"] == "out"
    assert api.folders[0]["path_in_repo"] == "training-runs/sft-001"
    assert ref == "hf://models/G-ACE/domain-model/training-runs/sft-001"


def test_evidence_context_rejects_path_like_run_id():
    bad = HFTrainingEvidenceContext(**{**context().__dict__, "run_id": "bad/run"})
    try:
        bad.validate()
    except ValueError as error:
        assert "evidence_run_id_invalid" in str(error)
    else:
        raise AssertionError("invalid run id accepted")
