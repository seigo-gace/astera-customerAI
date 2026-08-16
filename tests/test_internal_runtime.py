import pytest

from runtime.bootstrap import RuntimeDependencies, build_work
from runtime.schemas import GroundedFact, ResolutionMode, RoleName, RoleResult, TaskResolution


class Canonical:
    def __init__(self):
        self.calls = 0

    async def find_for_tasks(self, tasks):
        self.calls += 1
        return [
            GroundedFact(
                fact_id="f1",
                value="確認済み回答",
                source_id="canon",
                source_ids=["canon"],
                authority="canonical",
            )
        ]


class Live:
    def __init__(self):
        self.calls = 0

    async def current_facts(self, tasks):
        self.calls += 1
        return []


class Head:
    def __init__(self):
        self.calls = []

    async def run_all(self, packet, skills):
        self.calls.append(("all", len(packet.facts), tuple(s.skill_id for s in skills)))
        return [
            RoleResult(
                role=RoleName.CONSTRUCTIVE,
                evidence_ids=["f1"],
                task_resolutions=[
                    TaskResolution(task_id="t1", public_text="確認済み回答", evidence_ids=["f1"])
                ],
                completion_state="complete",
            ),
            RoleResult(role=RoleName.ADVERSARIAL, evidence_ids=["f1"], completion_state="complete"),
            RoleResult(role=RoleName.EVIDENCE_BOUND, evidence_ids=["f1"], completion_state="complete"),
        ]

    async def validate_draft(self, packet, skills, draft):
        raise AssertionError("repair_not_expected")

    async def retry_role(self, role, packet, skills):
        raise AssertionError("retry_not_expected")


class UnexecutedClaimHead(Head):
    async def run_all(self, packet, skills):
        return [
            RoleResult(
                role=RoleName.CONSTRUCTIVE,
                evidence_ids=["f1"],
                task_resolutions=[TaskResolution(task_id="t1", public_text="設定しました。", evidence_ids=["f1"])],
                completion_state="complete",
            ),
            RoleResult(role=RoleName.ADVERSARIAL, evidence_ids=["f1"], completion_state="complete"),
            RoleResult(role=RoleName.EVIDENCE_BOUND, evidence_ids=["f1"], completion_state="complete"),
        ]


class UnsupportedClaimHead(Head):
    async def run_all(self, packet, skills):
        return [
            RoleResult(
                role=RoleName.CONSTRUCTIVE,
                evidence_ids=["missing-fact"],
                task_resolutions=[
                    TaskResolution(task_id="t1", public_text="未裏付け回答", evidence_ids=["missing-fact"])
                ],
                completion_state="complete",
            ),
            RoleResult(role=RoleName.ADVERSARIAL, completion_state="complete"),
            RoleResult(role=RoleName.EVIDENCE_BOUND, completion_state="complete"),
        ]


class FailingHead(Head):
    async def run_all(self, packet, skills):
        raise RuntimeError("role-down")


@pytest.mark.asyncio
async def test_internal_runtime_uses_single_grounding_and_shared_head():
    canonical = Canonical()
    live = Live()
    head = Head()
    work = build_work(
        RuntimeDependencies(
            canonical_store=canonical,
            live_state_provider=live,
            japanese_alias_registry={"Astera": []},
            japanese_fuzzy_threshold=90,
            shared_head=head,
        )
    )
    result = await work.run("s1", "Asteraとは？")
    assert result.passed and result.answer == "確認済み回答"
    assert canonical.calls == 1 and live.calls == 1
    assert head.calls[0][0] == "all" and head.calls[0][1] == 1
    assert len(head.calls[0][2]) <= 8


@pytest.mark.asyncio
async def test_unexecuted_completion_claim_is_fail_closed_from_public_output():
    work = build_work(
        RuntimeDependencies(
            canonical_store=Canonical(),
            live_state_provider=Live(),
            japanese_alias_registry={"Astera": []},
            japanese_fuzzy_threshold=90,
            shared_head=UnexecutedClaimHead(),
        )
    )

    result = await work.run("s2", "Asteraの設定を教えて")

    assert result.passed is False
    assert result.answer is None
    assert result.resolution_mode == ResolutionMode.SAFETY_BLOCKED
    assert result.answered_task_ids == []
    assert result.unresolved_task_ids == ["t1"]
    assert result.failure_class == "safety_rejection"
    assert "unexecuted_completion_claim" in result.violations


@pytest.mark.asyncio
async def test_unsupported_claim_is_fail_closed_from_public_output():
    work = build_work(
        RuntimeDependencies(
            canonical_store=Canonical(),
            live_state_provider=Live(),
            japanese_alias_registry={"Astera": []},
            japanese_fuzzy_threshold=90,
            shared_head=UnsupportedClaimHead(),
        )
    )

    result = await work.run("s3", "Asteraとは？")

    assert result.passed is False
    assert result.answer is None
    assert result.resolution_mode == ResolutionMode.SAFETY_BLOCKED
    assert result.unresolved_task_ids == ["t1"]
    assert "unsupported_claim" in result.violations


@pytest.mark.asyncio
async def test_role_runtime_failure_returns_the_actual_unresolved_task_ids():
    work = build_work(
        RuntimeDependencies(
            canonical_store=Canonical(),
            live_state_provider=Live(),
            japanese_alias_registry={"Astera": []},
            japanese_fuzzy_threshold=90,
            shared_head=FailingHead(),
        )
    )

    result = await work.run("s4", "Asteraとは？")

    assert result.passed is False
    assert result.answer is None
    assert result.resolution_mode == ResolutionMode.RUNTIME_FAILURE
    assert result.unresolved_task_ids == ["t1"]
    assert result.failure_class == "runtime_failure"
