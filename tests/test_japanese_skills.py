from runtime.japanese_skills import (
    AsteraTermAliasSkill,
    ConversationContext,
    JapaneseEllipsisContextSkill,
    JapaneseResponseTerminologyGuard,
    JapaneseShortQASkillPack,
    JapaneseSurfaceNormalizer,
)

ALIASES = {
    "Astera": ["アステラ", "astera"],
    "Credit": ["クレジット", "クレジト"],
    "API": ["ＡＰＩ", "api"],
}


def test_nfkc_width_and_space_normalization():
    result = JapaneseSurfaceNormalizer().normalize("ｱｽﾃﾗ　ＡＰＩ")
    assert result.raw == "ｱｽﾃﾗ　ＡＰＩ"
    assert result.normalized == "アステラ API"


def test_newline_and_space_normalization():
    result = JapaneseSurfaceNormalizer().normalize("  Astera\r\n\r\n\r\n  API  ")
    assert result.normalized == "Astera\n\nAPI"


def test_exact_alias_is_resolved():
    result = AsteraTermAliasSkill(ALIASES).candidates(
        "アステラの料金は？", fuzzy_threshold=88
    )
    assert result[0].canonical == "Astera"
    assert result[0].exact


def test_typo_alias_is_candidate_not_rewrite():
    result = AsteraTermAliasSkill(ALIASES).candidates(
        "クレジトについて", fuzzy_threshold=88
    )
    assert any(item.canonical == "Credit" for item in result)


def test_unrelated_term_is_not_forced():
    result = AsteraTermAliasSkill(ALIASES).candidates("天気はどう？", fuzzy_threshold=90)
    assert result == []


def test_short_followup_binds_context_only_when_available():
    context = ConversationContext(active_topics=("Pro plan",), last_user_need="料金比較")
    assert JapaneseEllipsisContextSkill().bind("じゃあProは？", context)[
        "is_ellipsis_followup"
    ] is True
    assert JapaneseEllipsisContextSkill().bind(
        "じゃあProは？", ConversationContext()
    )["is_ellipsis_followup"] is False


def test_long_sentence_is_not_ellipsis_followup():
    context = ConversationContext(active_topics=("Pro plan",), last_user_need="料金比較")
    text = "じゃあ" + "この条件についてさらに詳しく比較して説明してください。" * 3
    assert JapaneseEllipsisContextSkill().bind(text, context)[
        "is_ellipsis_followup"
    ] is False


def test_terminology_guard_detects_alias():
    result = JapaneseResponseTerminologyGuard({"Astera": ["アステラ"]}).check(
        "アステラでは利用できます。"
    )
    assert result and result[0].canonical == "Astera"


def test_skill_pack_preserves_raw_and_emits_hints():
    pack = JapaneseShortQASkillPack(alias_registry=ALIASES, fuzzy_threshold=88)
    prepared = pack.prepare("ｱｽﾃﾗ　ＡＰＩ", ConversationContext())
    assert prepared["raw_text"] == "ｱｽﾃﾗ　ＡＰＩ"
    assert prepared["normalized_text"] == "アステラ API"
    canonicals = {item["canonical"] for item in prepared["term_candidates"]}
    assert {"Astera", "API"}.issubset(canonicals)


def test_short_latin_alias_pro_does_not_match_programming():
    aliases = {"Pro": ["pro"]}
    result = AsteraTermAliasSkill(aliases).candidates(
        "programmingについて教えて", fuzzy_threshold=90
    )
    assert not any(item.canonical == "Pro" for item in result)


def test_short_latin_alias_api_does_not_match_rapid():
    aliases = {"API": ["api"]}
    result = AsteraTermAliasSkill(aliases).candidates(
        "rapid modeとは？", fuzzy_threshold=90
    )
    assert not any(item.canonical == "API" for item in result)


def test_terminology_guard_does_not_flag_alias_inside_unrelated_word():
    guard = JapaneseResponseTerminologyGuard({"API": ["api"]})
    assert guard.check("rapid modeを利用します。") == []


def test_empty_symbol_emoji_unicode_inputs_do_not_crash():
    normalizer = JapaneseSurfaceNormalizer()
    for text in ("", "！？！？", "🔥㊗️", "ver2.0"):
        result = normalizer.normalize(text)
        assert result.raw == text
        assert isinstance(result.normalized, str)


def test_ver20_ascii_alias_can_be_extracted_with_boundaries():
    aliases = {"ver2.0": ["ver2.0"]}
    result = AsteraTermAliasSkill(aliases).candidates(
        "現在はver2.0です", fuzzy_threshold=90
    )
    assert result and result[0].canonical == "ver2.0" and result[0].exact


def test_long_input_over_1000_chars_is_processed():
    text = "説明" * 600 + " API"
    result = AsteraTermAliasSkill(ALIASES).candidates(text, fuzzy_threshold=90)
    assert any(item.canonical == "API" for item in result)
