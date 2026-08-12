from runtime.japanese_skills import AsteraTermAliasSkill,ConversationContext,JapaneseEllipsisContextSkill,JapaneseResponseTerminologyGuard,JapaneseShortQASkillPack,JapaneseSurfaceNormalizer
ALIASES={"Astera":["アステラ","astera"],"Credit":["クレジット","クレジト"],"API":["ＡＰＩ","api"]}
def test_nfkc_width_and_space_normalization():
    r=JapaneseSurfaceNormalizer().normalize("ｱｽﾃﾗ　ＡＰＩ"); assert r.raw=="ｱｽﾃﾗ　ＡＰＩ" and r.normalized=="アステラ API"
def test_exact_alias_is_resolved():
    r=AsteraTermAliasSkill(ALIASES).candidates("アステラの料金は？",fuzzy_threshold=88); assert r[0].canonical=="Astera" and r[0].exact
def test_short_followup_binds_only_with_context():
    c=ConversationContext(active_topics=("Pro",),last_user_need="料金"); assert JapaneseEllipsisContextSkill().bind("じゃあProは？",c)["is_ellipsis_followup"] is True; assert JapaneseEllipsisContextSkill().bind("じゃあProは？",ConversationContext())["is_ellipsis_followup"] is False
def test_short_ascii_aliases_do_not_match_inside_words():
    assert AsteraTermAliasSkill({"Pro":["pro"]}).candidates("programming",fuzzy_threshold=90)==[]; assert AsteraTermAliasSkill({"API":["api"]}).candidates("rapid",fuzzy_threshold=90)==[]; assert JapaneseResponseTerminologyGuard({"API":["api"]}).check("rapid mode")==[]
def test_empty_unicode_and_long_input_do_not_crash():
    pack=JapaneseShortQASkillPack(alias_registry=ALIASES,fuzzy_threshold=90)
    for text in ("","！？🔥㊗️","説明"*600+" API"): assert isinstance(pack.prepare(text,ConversationContext())["normalized_text"],str)
