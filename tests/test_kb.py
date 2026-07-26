from pathlib import Path

from runtime.kb import KBIndex


def test_snapshot_filters_unpublished_and_searches(tmp_path: Path):
    index = KBIndex(tmp_path)
    pages = [
        {
            "id": "kb1",
            "質問": "購入したクレジットが反映されません",
            "短い回答": "決済状態と付与状態を順番に確認します。",
            "本文": "購入時刻を確認し、決済とクレジット付与を照合します。",
            "検索語": "買ったのに増えない, 未反映",
            "公開状態": "公開",
            "実装状態": "文書確認済み",
            "要再確認": False,
        },
        {
            "id": "kb2",
            "質問": "非公開",
            "短い回答": "secret",
            "本文": "secret",
            "公開状態": "非公開",
            "実装状態": "文書確認済み",
            "要再確認": False,
        },
    ]
    index.build_snapshot(version="v1", pages=pages)
    hits = index.search("クレジット 反映")
    assert [hit.kb_id for hit in hits] == ["kb1"]
