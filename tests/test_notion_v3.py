from runtime.notion_v3 import map_v3_record


def _row(**overrides):
    base = {
        "KB ID": "v3-app-text-submit",
        "質問": "Enterを押すと送信されますか？",
        "完全一致質問": "Enterを押すと送信されますか？",
        "短い回答": "Enter単独は改行です。PCはCtrl／Command＋Enter、Mobileは実行Buttonで実行します。",
        "直接回答": "Enter単独は改行です。",
        "本文": "入力中の誤送信を避けるためEnter単独送信は採用しません。",
        "検索語": "Enter 改行 送信 Ctrl Enter Command Enter 実行ボタン",
        "言い換え": "Enter 改行 送信 実行キー",
        "参照表現": "それ 送信キー",
        "質問タスク": "Composerの送信操作を説明する",
        "回答境界": "Enter単独送信とは答えない。",
        "誤前提": "Enterを押すと即送信される",
        "訂正文": "いいえ。Enter単独は送信ではなく改行です。",
        "禁止断定": "Enter単独送信",
        "適用条件": "Composer入力操作",
        "非適用条件": "",
        "競合排除キー": "",
        "一貫性キー": "app.text_submit",
        "矛盾禁止キー": "app_enter_newline_not_submit",
        "会話継承キー": "app.composer.input",
        "継承条件": "送信操作の追加質問",
        "話題切替条件": "別機能が明示された場合",
        "ドメイン": "product",
        "対象": "Astera App",
        "対象物": "Composer",
        "対象者": ["登録利用者"],
        "操作": "入力",
        "状態": "normal",
        "処理段階": "before",
        "Evidence Role": "direct",
        "Runtime採用": True,
        "公開可否": "公開可",
        "公開状態": "検証公開",
        "実装状態": "設計済み",
        "最終検証結果": "合格",
        "要再確認": False,
        "優先度": 100,
        "回答スロット": "conclusion",
        "回答順": 1,
        "単独回答可": True,
        "統合必須": False,
    }
    base.update(overrides)
    return base


def test_v3_mapping_preserves_canonical_controls():
    row = {"id": "page-1", "url": "https://notion.test/page-1", "last_edited_time": "2026-08-10T00:00:00Z"}
    mapped = map_v3_record(_row(), row)
    assert mapped is not None
    assert mapped["id"] == "v3-app-text-submit"
    assert mapped["Status"] == "公開"
    assert "Enter単独は改行" in mapped["Definitive_Answer"]
    assert "app_enter_newline_not_submit" in mapped["Exceptions_and_Limits"]
    assert "app.composer.input" in mapped["Target_Intents"]
    assert mapped["_index"]["Canonical_ID"] == "v3-app-text-submit"
    assert mapped["_index"]["Source_Section"] == "Astera Customer AI KB v3"


def test_v3_mapping_rejects_non_adopted_prohibited_and_unverified_records():
    row = {"id": "page-1", "url": "https://notion.test/page-1", "last_edited_time": "2026-08-10T00:00:00Z"}
    assert map_v3_record(_row(**{"Runtime採用": False}), row) is None
    assert map_v3_record(_row(**{"Evidence Role": "prohibited"}), row) is None
    assert map_v3_record(_row(**{"公開可否": "公開不可"}), row) is None
    assert map_v3_record(_row(**{"要再確認": True}), row) is None


def test_v3_candidate_mode_accepts_only_validated_rows():
    row = {"id": "page-1", "url": "https://notion.test/page-1", "last_edited_time": "2026-08-10T00:00:00Z"}
    candidate = _row(**{"Runtime採用": False, "最終検証結果": "合格"})
    assert map_v3_record(candidate, row, allow_validated_candidates=True) is not None
    waiting = _row(**{"Runtime採用": False, "最終検証結果": "再検証待ち"})
    assert map_v3_record(waiting, row, allow_validated_candidates=True) is None
