import json
from pathlib import Path

from runtime.kb import KBIndex


ROOT = Path(__file__).resolve().parents[1]


def build_index(tmp_path: Path) -> KBIndex:
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
    return index


def test_snapshot_filters_unpublished_and_searches(tmp_path: Path):
    index = build_index(tmp_path)
    hits = index.search("クレジット 反映")
    assert [hit.kb_id for hit in hits] == ["kb1"]


def test_context_expanded_query_keeps_original_japanese_match(tmp_path: Path):
    index = build_index(tmp_path)
    hits = index.search("購入したクレジットが反映されません credit")
    assert [hit.kb_id for hit in hits] == ["kb1"]


def test_price_query_does_not_match_payment_troubleshooting_through_weak_terms(tmp_path: Path):
    index = build_index(tmp_path)
    hits = index.search("billing pricing 現在 料金 いくら すか")
    assert hits == []


def test_private_prompt_terms_do_not_match_generic_system_wording(tmp_path: Path):
    index = build_index(tmp_path)
    hits = index.search("system prompt internal admin env 内容 全部出して")
    assert hits == []


def test_structured_template_builds_body_search_terms_and_boundaries(tmp_path: Path):
    index = KBIndex(tmp_path)
    index.build_snapshot(
        version="structured-v1",
        pages=[
            {
                "id": "kb-webhook-replay",
                "製品": "Webhook Gateway",
                "システム": "Replay",
                "カテゴリ": "障害",
                "質問種別": "procedure",
                "質問": "配送に失敗したWebhookを再送する方法は？",
                "短い回答": "管理画面から対象Deliveryを確認し、再送条件を満たす場合だけReplayします。",
                "前提条件": ["対象Deliveryがdeadまたはskippedであること"],
                "手順": ["対象Deliveryを開く", "失敗理由を確認する", "Replayを実行する"],
                "例外": ["Cooldown中は再実行できません"],
                "完了確認": ["Delivery状態がdeliveredになったことを確認します"],
                "追加質問": ["何回まで再送できますか", "いつ再送できますか"],
                "検索語": "Webhook 再送 Replay 配送失敗",
                "同義語": ["リプレイ", "再配送"],
                "対象": ["開発者", "運用者"],
                "回答境界": ["実際のDelivery状態はGateway正本で確認します"],
                "禁止回答": ["再送していないのに完了と書かない"],
                "公開状態": "公開",
                "実装状態": "実装済み",
                "要再確認": False,
                "確認日": "2026-07-27",
            }
        ],
    )

    hits = index.search("Webhook リプレイ 再配送")
    assert [hit.kb_id for hit in hits] == ["kb-webhook-replay"]
    assert "## 手順" in hits[0].body
    assert "1. 対象Deliveryを開く" in hits[0].body
    assert "Cooldown中" in hits[0].body
    assert "再送していないのに完了と書かない" in hits[0].answer_boundary
    assert hits[0].target == "開発者,運用者"


def test_repository_template_is_safe_draft_and_contains_required_sections():
    template = json.loads((ROOT / "templates/customer-ai-kb-template.json").read_text(encoding="utf-8"))
    assert template["template_version"] == "1.0"
    record = template["pages"][0]
    assert record["公開状態"] == "下書き"
    assert record["要再確認"] is True
    for field in template["required_fields"]:
        assert field in record
    for field in ("手順", "条件", "例外", "障害症状", "解決手順", "完了確認", "追加質問", "禁止回答"):
        assert field in record
