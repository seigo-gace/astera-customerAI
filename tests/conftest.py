from __future__ import annotations

import pytest

from runtime.bootstrap import RuntimeDependencies, build_work
from runtime.schemas import GroundedFact, RoleName, RoleResult, TaskResolution


class FakeCanonical:
    def __init__(self):
        self.calls = 0

    async def find_for_tasks(self, tasks):
        self.calls += 1
        return [GroundedFact(fact_id="f1", value="確認済み回答", source_id="canon", source_ids=["canon"], authority="canonical")]


class FakeLive:
    def __init__(self):
        self.calls = 0
        self.facts = []

    async def current_facts(self, tasks):
        self.calls += 1
        return list(self.facts)


class FakeSharedHead:
    def __init__(self, state):
        self.state = state

    def _constructive(self, packet):
        count = self.state.get("constructive", 0)
        self.state["constructive"] = count + 1
        if self.state.get("repair_first") and count == 0:
            return RoleResult(
                role=RoleName.CONSTRUCTIVE,
                missing_needs=["t1"],
                task_resolutions=[TaskResolution(task_id="t1", unresolved_reason="repair_required")],
                completion_state="partial",
            )
        task_id = packet.repair_targets[0] if packet.repair_targets else packet.tasks[0].task_id
        return RoleResult(
            role=RoleName.CONSTRUCTIVE,
            claims=["確認済み回答"],
            evidence_ids=["f1"],
            task_resolutions=[TaskResolution(task_id=task_id, public_text="確認済み回答", evidence_ids=["f1"])],
            completion_state="complete",
        )

    async def run_all(self, packet, skills):
        constructive = self._constructive(packet)
        return [
            constructive,
            RoleResult(role=RoleName.ADVERSARIAL, evidence_ids=["f1"], missing_needs=list(constructive.missing_needs), completion_state="complete"),
            RoleResult(role=RoleName.EVIDENCE_BOUND, evidence_ids=["f1"], completion_state="complete"),
        ]

    async def validate_draft(self, packet, skills, draft):
        return [
            RoleResult(role=RoleName.ADVERSARIAL, evidence_ids=["f1"], completion_state="complete"),
            RoleResult(role=RoleName.EVIDENCE_BOUND, evidence_ids=["f1"], completion_state="complete"),
        ]

    async def retry_role(self, role, packet, skills):
        assert role == RoleName.CONSTRUCTIVE
        return self._constructive(packet)


@pytest.fixture
def runtime_parts():
    canonical = FakeCanonical()
    live = FakeLive()
    state = {}
    deps = RuntimeDependencies(
        canonical_store=canonical,
        live_state_provider=live,
        shared_head=FakeSharedHead(state),
        japanese_alias_registry={"Astera": ["アステラ", "astera"]},
        japanese_fuzzy_threshold=90.0,
        max_targeted_retry=1,
    )
    return build_work(deps), canonical, live, state
