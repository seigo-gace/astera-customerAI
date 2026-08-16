import json
import pytest

from training.hf_benchmark_evidence import (
    HFBenchmarkCandidateResult,
    HFBenchmarkEvidenceContext,
    HubBenchmarkEvidenceSink,
    build_benchmark_evidence,
    persist_success_then_format_stdout,
    select_best_by_eval_loss,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def context():
    return HFBenchmarkEvidenceContext(
        run_id="benchmark-001",
        result_repo_id="G-ACE/astera-customerai-training-evidence",
        base_model_id="Qwen/Qwen3-4B",
        base_revision="1cfa9a7208912126459214e8b04321603b3df60c",
        train_dataset_sha256=SHA_A,
        eval_dataset_sha256=SHA_B,
    )


def candidate(cid, loss):
    return HFBenchmarkCandidateResult(
        candidate_id=cid,
        parameters={"benchmark_parameter": 1},
        metrics={"eval_loss": loss},
    )


class FakeApi:
    def __init__(self):
        self.created = []
        self.uploads = []
    def create_repo(self, **kwargs):
        self.created.append(kwargs)
    def upload_file(self, **kwargs):
        payload = json.loads(kwargs["path_or_fileobj"].getvalue().decode("utf-8"))
        self.uploads.append((kwargs, payload))


def test_started_evidence_can_be_persisted_before_gpu_work():
    api = FakeApi()
    event = build_benchmark_evidence(context(), status="started")
    ref = HubBenchmarkEvidenceSink(api=api).persist(event)
    paths = [item[0]["path_in_repo"] for item in api.uploads]
    assert any(path.startswith("benchmark-runs/benchmark-001/events/started-") for path in paths)
    assert "benchmark-runs/benchmark-001/latest.json" in paths
    assert ref.startswith("hf://datasets/G-ACE/astera-customerai-training-evidence/benchmark-runs/benchmark-001/events/started-")


def test_candidate_completed_event_persists_partial_progress():
    event = build_benchmark_evidence(context(), status="candidate_completed", candidates=(candidate("A", 1.2),))
    assert event.candidates[0].candidate_id == "A"


def test_best_candidate_is_selected_by_lowest_eval_loss():
    results = (candidate("A", 1.2), candidate("B", 0.8), candidate("C", 1.0))
    assert select_best_by_eval_loss(results).candidate_id == "B"
    event = build_benchmark_evidence(context(), status="succeeded", candidates=results, best_candidate_id="B")
    assert event.best_candidate_id == "B"


def test_success_rejects_wrong_best_candidate():
    results = (candidate("A", 1.2), candidate("B", 0.8))
    with pytest.raises(ValueError, match="benchmark_best_candidate_mismatch"):
        build_benchmark_evidence(context(), status="succeeded", candidates=results, best_candidate_id="A")


def test_failure_contains_fingerprint_but_not_hf_token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "super-secret-token")
    event = build_benchmark_evidence(context(), status="failed", error=RuntimeError("CUDA OOM"))
    payload = event.to_json()
    assert event.failure_fingerprint
    assert "super-secret-token" not in payload
    assert "HF_TOKEN" not in payload


def test_candidate_parameters_reject_secret_like_keys():
    bad = HFBenchmarkCandidateResult(candidate_id="A", parameters={"api_key": "oops"}, metrics={"eval_loss": 1.0})
    with pytest.raises(ValueError, match="secret_like_parameter_forbidden"):
        bad.validate()


def test_stdout_is_created_only_after_success_is_persisted():
    api = FakeApi()
    sink = HubBenchmarkEvidenceSink(api=api)
    results = (candidate("A", 1.2), candidate("B", 0.8), candidate("C", 1.0))
    event = build_benchmark_evidence(context(), status="succeeded", candidates=results, best_candidate_id="B")
    ref, line = persist_success_then_format_stdout(sink, event)
    assert len(api.uploads) == 2
    assert ref in line
    assert line.startswith("ASTERA_BENCHMARK_RESULT=")


def test_invalid_dataset_hash_is_blocked():
    bad = HFBenchmarkEvidenceContext(**{**context().__dict__, "train_dataset_sha256": "not-a-sha"})
    with pytest.raises(ValueError, match="benchmark_train_dataset_sha256_required"):
        build_benchmark_evidence(bad, status="started")
