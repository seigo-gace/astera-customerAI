from runtime.answer_quality import (
    FinalAnswerComposer,
    IntegratedAnswerPlan,
    RuntimeSatisfactionGate,
    RuntimeSatisfactionSignals,
)
from runtime.schemas import MessagePayload, NeedTask, ResolutionMode, TaskResolution


def need(task_id: str) -> NeedTask:
    return NeedTask(
        task_id=task_id,
        text=f"need {task_id}",
        intent="usage",
        completion_condition="answered",
    )


def test_resolved_composes_all_validated_tasks():
    result = FinalAnswerComposer().compose(
        IntegratedAnswerPlan(
            needs=(need("n1"), need("n2")),
            resolutions=(
                TaskResolution(task_id="n1", public_text="回答1"),
                TaskResolution(task_id="n2", public_text="回答2"),
            ),
        )
    )
    assert result.mode == ResolutionMode.RESOLVED
    assert result.answer == "回答1\n\n回答2"
    assert result.resolved_task_ids == ("n1", "n2")
    assert result.unresolved_task_ids == ()


def test_safe_partial_is_useful_but_not_resolved():
    result = FinalAnswerComposer().compose(
        IntegratedAnswerPlan(
            needs=(need("n1"), need("n2")),
            resolutions=(TaskResolution(task_id="n1", public_text="確認済み回答"),),
        )
    )
    assert result.mode == ResolutionMode.SAFE_PARTIAL
    assert result.answer == "確認済み回答"
    assert result.resolved_task_ids == ("n1",)
    assert result.unresolved_task_ids == ("n2",)


def test_missing_evidence_blocks_only_affected_task():
    result = FinalAnswerComposer().compose(
        IntegratedAnswerPlan(
            needs=(need("n1"), need("n2")),
            resolutions=(
                TaskResolution(task_id="n1", public_text="回答1"),
                TaskResolution(task_id="n2", public_text="回答2"),
            ),
            missing_evidence_task_ids=("n2",),
        )
    )
    assert result.mode == ResolutionMode.SAFE_PARTIAL
    assert result.answer == "回答1"
    assert result.unresolved_task_ids == ("n2",)


def test_required_user_input_asks_one_question():
    result = FinalAnswerComposer().compose(
        IntegratedAnswerPlan(
            needs=(need("n1"),),
            resolutions=(),
            missing_user_inputs=("対象プラン", "契約状態"),
        )
    )
    assert result.mode == ResolutionMode.NEEDS_USER_INPUT
    assert result.clarification_questions == ("対象プランを確認してください。",)


def test_runtime_failure_returns_no_unverified_draft():
    result = FinalAnswerComposer().compose(
        IntegratedAnswerPlan(
            needs=(need("n1"),),
            resolutions=(TaskResolution(task_id="n1", public_text="draft"),),
            runtime_failure=True,
        )
    )
    assert result.mode == ResolutionMode.RUNTIME_FAILURE
    assert result.answer is None
    assert result.resolved_task_ids == ()


def test_safety_block_returns_no_unverified_draft():
    result = FinalAnswerComposer().compose(
        IntegratedAnswerPlan(
            needs=(need("n1"),),
            resolutions=(TaskResolution(task_id="n1", public_text="draft"),),
            safety_blocked=True,
        )
    )
    assert result.mode == ResolutionMode.SAFETY_BLOCKED
    assert result.answer is None


def test_satisfaction_gate_passes_only_fully_resolved_structure():
    passed, failures = RuntimeSatisfactionGate().evaluate(
        ResolutionMode.RESOLVED,
        RuntimeSatisfactionSignals(
            all_major_needs_covered=True,
            evidence_complete=True,
            context_consistent=True,
            required_actionability_present=True,
            false_premise_corrected=True,
        ),
    )
    assert passed
    assert failures == []


def test_satisfaction_gate_rejects_partial_and_unsupported_claim():
    passed, failures = RuntimeSatisfactionGate().evaluate(
        ResolutionMode.SAFE_PARTIAL,
        RuntimeSatisfactionSignals(
            all_major_needs_covered=False,
            evidence_complete=True,
            context_consistent=True,
            required_actionability_present=True,
            false_premise_corrected=True,
            unsupported_claim_count=1,
        ),
    )
    assert not passed
    assert "conversation_not_resolved" in failures
    assert "major_need_missing" in failures
    assert "unsupported_claim" in failures


def test_unresolved_reason_prevents_resolution():
    item = TaskResolution(task_id="n1", public_text="text", unresolved_reason="missing_current_fact")
    assert not item.resolved


def test_existing_message_payload_contract_is_preserved():
    payload = MessagePayload(
        session_id="session_1234",
        message_id="message_1234",
        message="Asteraの使い方を教えて",
        source="astera-hp",
        current_path="/pricing?x=1#top",
    )
    assert payload.current_path == "/pricing"
    assert payload.locale == "ja-JP"
