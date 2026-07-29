from __future__ import annotations

from scripts.run_hp_public_story_suite import _build_cases


def test_hp_public_story_suite_has_exactly_100_unique_cases() -> None:
    cases = _build_cases()
    assert len(cases) == 100
    assert len({str(case["message"]) for case in cases}) == 100
    assert sum(case["type"] == "known" for case in cases) == 99
    assert sum(case["type"] == "free_model_multi_task" for case in cases) == 1


def test_hp_public_story_suite_covers_latest_public_positioning() -> None:
    messages = "\n".join(str(case["message"]) for case in _build_cases())
    for required in (
        "外付けAI強化外装",
        "Google V8",
        "多重並列思考",
        "判断素材8項目",
        "本当の目的",
        "前提不足",
        "事実確認",
        "危機察知",
        "反対視点",
        "比較案",
        "推奨判断",
        "主役AIへの再指示",
        "Copy",
        "Form",
        "API",
        "Webhook",
        "38 Genre Lens",
        "Private HF",
    ):
        assert required in messages


def test_hp_public_story_suite_checks_old_source_conflicts() -> None:
    messages = "\n".join(str(case["message"]) for case in _build_cases())
    assert "月額2,000円" in messages
    assert "9,800円" in messages
    assert "Stripe" in messages
    assert "旧KAGURA" in messages
    assert "古いREADME" in messages


def test_each_known_case_has_bounded_expected_terms() -> None:
    for case in _build_cases():
        terms = case["expected_terms"]
        assert isinstance(terms, list)
        assert len(terms) <= 12
        if case["type"] == "known":
            assert terms
            assert all(isinstance(term, str) and term for term in terms)
