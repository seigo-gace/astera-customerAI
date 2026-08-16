from runtime.schemas import GroundedFact
from runtime.security import PublicBoundary


def test_private_and_legacy_facts_are_filtered():
    facts = PublicBoundary().filter_facts(
        [
            GroundedFact(fact_id="a", value="x", source_id="s", authority="canonical", public=False),
            GroundedFact(fact_id="b", value="x", source_id="s", authority="canonical", legacy=True),
            GroundedFact(fact_id="c", value="ok", source_id="s", authority="canonical"),
        ]
    )
    assert [i.fact_id for i in facts] == ["c"]


def test_unexecuted_completion_claim_fails():
    r = PublicBoundary().check_output(
        answer="処理しました",
        forbidden_literals=[],
        unexecuted_completion_claim=True,
    )
    assert not r.passed and "unexecuted_completion_claim" in r.violations


def test_unexecuted_completion_claim_is_detected_from_public_text():
    boundary = PublicBoundary()
    assert boundary.detect_unexecuted_completion_claim("設定しました。") is True
    assert boundary.detect_unexecuted_completion_claim("I have deployed the change.") is True
    assert boundary.detect_unexecuted_completion_claim("ユーザーが設定しました。") is False
    assert boundary.detect_unexecuted_completion_claim("設定方法を説明します。") is False
