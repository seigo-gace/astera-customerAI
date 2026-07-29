from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import live_story_test as base  # noqa: E402

REPORT_PATH = ROOT / "test-results" / "hp-public-story-report.json"


def _build_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []

    def add(messages: list[str], expected_terms: list[str]) -> None:
        cases.extend(
            {
                "type": "known",
                "message": message,
                "expected_terms": expected_terms,
            }
            for message in messages
        )

    add(
        [
            "Asteraとは何ですか？",
            "アステラを一言で説明して",
            "外付けAI強化外装ってどういう意味？",
            "Asteraは何を外側から追加するの？",
            "ChatGPTを置き換える製品ですか？",
            "ClaudeやGeminiをやめてAsteraだけ使うの？",
            "主役AIを残したまま使えるの？",
            "Asteraの一般向けの位置付けは？",
            "AI強化外装という表現を詳しく説明して",
            "AsteraはBrowser拡張機能のことですか？",
        ],
        ["外付けAI強化外装", "置き換え"],
    )
    add(
        [
            "Google V8は生成AIの名前ですか？",
            "Astera v8のV8ってLLMですか？",
            "V8が回答を生成するのですか？",
            "Google V8は何を担当しますか？",
            "V8 Runtimeと生成AI Modelの違いは？",
            "Asteraの実行基盤は何ですか？",
            "多重並列思考を動かすのはどの部分？",
            "V8を使う理由は？",
        ],
        ["V8", "生成AI Model"],
    )
    add(
        [
            "多重並列思考とは何ですか？",
            "8項目を全部同時に実行するの？",
            "5本柱は完全同時並列ですか？",
            "並列処理でも依存順序は守りますか？",
            "Asteraの判断のしくみを技術的に説明して",
            "FactとRiskの後に何をするの？",
            "MultiとCompareはいつ動きますか？",
            "多重並列という表現は誇張では？",
        ],
        ["並列", "依存"],
    )
    add(
        [
            "判断素材8項目を全部教えて",
            "Asteraが出す8つの判断材料は？",
            "判断素材の順番は固定ですか？",
            "8項目は内部Moduleの一覧ですか？",
            "主役AIへ渡す材料は何項目ですか？",
            "Asteraの公開出力Contractは？",
            "8項目の名前を順番に説明して",
            "目的から再指示までの流れは？",
        ],
        ["本当の目的", "主役AIへの再指示"],
    )

    item_groups = [
        (
            [
                "本当の目的では何を見る？",
                "表面的な依頼と真の目的をどう分ける？",
                "成功条件や対象者も主役AIへ渡しますか？",
            ],
            ["成功条件", "優先順位"],
        ),
        (
            [
                "前提不足では何を探す？",
                "予算や期限がない質問はどう扱う？",
                "確認質問ばかり返すの？",
            ],
            ["足りない条件", "確認"],
        ),
        (
            [
                "事実確認では何を分ける？",
                "推測を事実として扱わないためには？",
                "最新情報もAsteraだけで保証できますか？",
            ],
            ["事実", "未確認"],
        ),
        (
            [
                "危機察知では何を確認する？",
                "法務やSecurityの危険も見る？",
                "Risk候補は必ず起きるという意味？",
            ],
            ["Risk", "対策"],
        ),
        (
            [
                "反対視点は何のため？",
                "依頼者の案を否定する機能ですか？",
                "批判に耐える回答にできますか？",
            ],
            ["反対", "補強"],
        ),
        (
            [
                "比較案では何を並べる？",
                "一つの案だけを押し付けますか？",
                "安い案や安全な案も比較できますか？",
            ],
            ["案A", "採用条件"],
        ),
        (
            [
                "推奨判断は最終決定ですか？",
                "どの案を勧めるかの理由も出ますか？",
                "採用しない案の理由も分かりますか？",
            ],
            ["条件付き", "次の一手"],
        ),
        (
            [
                "主役AIへの再指示はどう使う？",
                "もう一回考えてと頼むのと何が違う？",
                "再指示に禁止事項や出力形式も入りますか？",
            ],
            ["再指示", "出力形式"],
        ),
    ]
    for messages, expected_terms in item_groups:
        add(messages, expected_terms)

    add(
        [
            "判断材料はどうやってChatGPTへ渡す？",
            "APIなしでもAsteraを使えますか？",
            "CopyとFormの違いは？",
            "自社AIへ組み込む方法は？",
            "WebhookでBotへ送れますか？",
            "Copy・Form・API・Webhookを使い分ける基準は？",
            "今すべての連携が本番利用できますか？",
            "手動利用から自動化へ移れますか？",
        ],
        ["Copy", "Webhook"],
    )
    add(
        [
            "Asteraを使うとAI回答が強くなる理由は？",
            "前提抜けはどう減りますか？",
            "事実と推測を分けると何が良い？",
            "反対意見を先に見る価値は？",
            "比較・検証・レビューは回答へどう効く？",
            "Asteraを使えば必ず正解になりますか？",
            "どのAIでも同じだけ改善しますか？",
            "専門家確認は不要になりますか？",
        ],
        ["目的", "比較"],
    )
    add(
        [
            "HPの外付けAI強化外装とRuntime説明は矛盾しない？",
            "8項目と5本柱は同じもの？",
            "38 Genre LensはHPの8項目に置き換わったの？",
            "一般向け説明と技術者向け説明を分けて教えて",
            "HPではなぜ5本柱を前面に出さないの？",
            "公開コピーとRepositoryの技術構造の関係は？",
            "Asteraのブランド説明とArchitecture説明はどちらが正しい？",
        ],
        ["HP", "技術"],
    )
    add(
        [
            "Proプランは今も月額2,000円ですか？",
            "Businessは9,800円ですか？",
            "決済はStripeですか？",
            "古い公開ドキュメントの料金を現在も使えますか？",
            "旧KAGURAの仕様が現在のAstera正本ですか？",
            "古いREADMEと最新HPが違う場合どちらを信じる？",
        ],
        ["最新", "正本"],
    )
    add(
        [
            "AsteraはAIですか？V8はModelですか？8項目もまとめて説明してください。",
            "外付けAI強化外装、多重並列思考、主役AIへの再指示の関係を一度に説明して。",
            "Asteraが主役AIを置き換えない理由と、回答が強くなる理由をまとめて。",
            "HPの一般説明と5本柱・38 Lensの技術説明を初心者にも分かるように比較して。",
        ],
        ["主役AI"],
    )
    add(
        [
            "AIに外から材料を足す仕組みって何？",
            "質問をそのまま投げず下ごしらえする機能ですか？",
            "目的、抜け、事実、危険、反論、選択肢を揃えるのは何のため？",
            "最後にAIへ渡すPromptまで作るの？",
        ],
        ["判断"],
    )

    cases.extend(
        [
            {
                "type": "known",
                "message": "Form、API、Webhookはもう全部完成していますか？",
                "expected_terms": ["本番提供済み"],
            },
            {
                "type": "known",
                "message": "公開HPからPrivate HFへBrowserが直接接続しますか？",
                "expected_terms": ["接続しません", "Cloudflare"],
            },
            {
                "type": "known",
                "message": "Webhook GatewayをCustomer AI専用に改造しましたか？",
                "expected_terms": ["専用", "汎用"],
            },
            {
                "type": "known",
                "message": "KBにないHPの新説明をModel知識で補って答えますか？",
                "expected_terms": ["補いません"],
            },
        ]
    )
    cases.append(
        {
            "type": "free_model_multi_task",
            "message": (
                "Asteraを初めて見る一般利用者向けに、外付けAI強化外装、Google V8、"
                "判断素材8項目、主役AIへ渡す方法を一つの流れで説明し、技術者向けには"
                "完全同時並列ではなく依存順序があることも補足してください。"
            ),
            "expected_terms": [],
        }
    )
    if len(cases) != 100:
        raise RuntimeError(f"hp_public_story_suite_count_invalid:{len(cases)}")
    return cases


def main() -> None:
    if not base.HF_TOKEN:
        raise SystemExit("HF_TOKEN_MISSING")

    api = base.HfApi(token=base.HF_TOKEN)
    base_url = base._private_space_base_url(api)
    github_sha = os.environ.get("GITHUB_SHA", "manual")
    payload = {
        "run_token": (
            f"hp{os.environ.get('GITHUB_RUN_ID', 'manual')}"
            f"{int(base.time.time())}"
        ),
        "cases": _build_cases(),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        with httpx.Client(
            timeout=httpx.Timeout(1200.0, connect=60.0),
            follow_redirects=True,
        ) as client:
            response = client.post(
                f"{base_url}/internal/self-test/run",
                json=payload,
                headers={
                    "authorization": f"Bearer {base.HF_TOKEN}",
                    "x-deployed-github-commit": github_sha,
                    "content-type": "application/json",
                },
            )
        if response.status_code != 200:
            failure = {
                "accepted": False,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "score_percent": 0.0,
                "free_model_invoked": False,
                "failure_stage": "hp_public_private_space_self_test_request",
                "http_status": response.status_code,
                "response": response.text[:10000],
                "space_id": base.SPACE_ID,
                "github_sha": github_sha,
                "finished_at": datetime.now(UTC).isoformat(),
            }
            REPORT_PATH.write_text(
                json.dumps(failure, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(json.dumps(failure, ensure_ascii=False, indent=2))
            raise SystemExit("HP_PUBLIC_PRIVATE_SPACE_SELF_TEST_REQUEST_FAILED")
        report = response.json()
    except Exception as error:
        if not REPORT_PATH.exists():
            failure = {
                "accepted": False,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "score_percent": 0.0,
                "free_model_invoked": False,
                "failure_stage": "hp_public_private_space_self_test_transport",
                "error": f"{type(error).__name__}:{error}",
                "space_id": base.SPACE_ID,
                "github_sha": github_sha,
                "finished_at": datetime.now(UTC).isoformat(),
            }
            REPORT_PATH.write_text(
                json.dumps(failure, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(json.dumps(failure, ensure_ascii=False, indent=2))
        raise

    report.update(
        {
            "suite": "hp-public-ai-v2",
            "source_sha256": "8c2de4259b00a4c64dc175bb76ed7187387db1c127e2f3de66fc21278490d8f5",
            "space_id": base.SPACE_ID,
            "base_url": base_url,
            "github_sha": github_sha,
            "verification_mode": "private_space_hp_public_kb_story",
        }
    )
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("accepted"):
        raise SystemExit(
            "HP_PUBLIC_LIVE_STORY_FAILED "
            f"passed={report.get('passed')} total={report.get('total')} "
            f"score={report.get('score_percent')}"
        )
    print(
        "HP_PUBLIC_LIVE_STORY_OK "
        f"passed={report['passed']} total={report['total']} "
        f"score={report['score_percent']:.2f} "
        f"model_invoked={report['free_model_invoked']}"
    )


if __name__ == "__main__":
    main()
