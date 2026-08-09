from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.notion_v3 import map_v3_record
from runtime.schemas import CloudEvent
from runtime.service import CustomerAIService

# Canonical wording copied from the current Astera Customer AI KB v3 records.
TOPICS = [
    ("v3-app-2fa", "2FAは使えますか？", "2FAは任意で、Authenticator AppのTOTPとBackup Codeを使う設計です。", "2FAはAccount Securityから有効化する任意機能です。Authenticator AppのTOTPとBackup Codeを使用し、SMS、電話、Email OTPは初期範囲外です。", "二段階認証 2FA TOTP Authenticator Backup Code", "2FAはSMS認証が必須である", "TOTP"),
    ("v3-app-account-overview", "Accountページでは何を確認できますか？", "AccountからSecurity、Plan、Credit、Data/Privacyを管理します。", "Account領域からAccount概要、Security、Plan／Subscription、Credit／購入、Data／Privacyへ進みます。", "アカウント account security subscription credit privacy", "Passwordやカード情報を送る必要がある", "Security"),
    ("v3-app-account-security", "Account Securityでは何を管理できますか？", "SecurityではSession、Passkey、2FA、Backup Codeを管理します。", "/account/securityで端末・Session、Passkey、2FA、Backup Codeを管理します。重要操作はFresh Session 15分と再認証を要求します。", "account security セッション passkey 2FA backup code", "Account乗っ取りもSettingsだけで必ず自己解決できる", "Passkey"),
    ("v3-app-api-explorer", "API Explorerから本番APIを実行できますか？", "API ExplorerはSandbox専用で、Production実行はしません。", "API ExplorerはSandboxだけを実行可能にします。ProductionはProduction Keyと正式Clientから利用します。", "API Explorer sandbox production API試す", "ExplorerからProduction APIを直接試せる", "Sandbox"),
    ("v3-app-api-key", "API Keyはどのように発行・管理しますか？", "Key全文は作成直後1回だけ表示し、以後はPrefix/Hashで管理します。", "SandboxとProductionを分離し、Production発行はFresh Sessionと再認証が必要です。", "API key 発行 production key sandbox scope prefix", "API Key全文はいつでもAccount画面で確認できる", "1回だけ"),
    ("v3-app-billing-status", "決済後の状態はどこで確認しますか？", "Billing Statusで決済状態を確認し、Confirmed後にCredit反映を確認します。", "Checkout開始や外部画面復帰だけでは決済成功やCredit付与を断定しません。", "決済状態 billing status pending credit反映 Square", "Square画面から戻れば必ず支払い成功でCreditも即反映済み", "Billing Status"),
    ("v3-app-contact", "問い合わせはどの経路から送りますか？", "ContactはHP/App共通Backendで処理し、通常問い合わせにWebhook Gatewayは使いません。", "通常問い合わせはHPとAppで共通のAstera App側Cloudflare Contact Backendを使います。", "問い合わせ contact support Turnstile Webhook Gateway", "問い合わせ送信はすべてWebhook Gateway経由で行う", "共通Backend"),
    ("v3-app-document", "書類作成ではどのようにテンプレートを使いますか？", "書類作成はTemplateを選び、固定書式を壊さないようLayout Diff Gateで検査します。", "固定書式は原本を複製し、指定Cell／Named Rangeだけを変更し、Layout Diff Gate合格前は完成扱いにしません。", "書類作成 テンプレート Google Sheets Google Docs 固定書式 Layout Diff", "書類作成はLow・Medium・Highの難易度を選ぶ", "Layout Diff"),
    ("v3-app-error-offline", "AppがOfflineやErrorになった場合はどうしますか？", "Error時は入力を保持し、偽SuccessへFallbackせず安全に再試行/停止します。", "API不成立時は成功表示やMock ResultへFallbackせず、入力・Purpose・Option・Fileを保持します。", "offline error maintenance retry 接続できない エラー", "Backendが落ちてもMock Resultを表示して処理成功にできる", "入力を保持"),
    ("v3-app-external-storage", "外部Storage転送はどのように動きますか？", "外部Storageは一方向転送で、転送後はAsteraが同期・編集・削除しません。", "外部Storage転送はBasic以上の一方向転送です。転送後の同期・編集・削除をAsteraは行いません。", "外部ストレージ Google Drive 転送 OAuth 一方向", "転送後もAsteraが外部StorageのFileを同期・更新・削除する", "一方向転送"),
    ("v3-app-file-attach", "ファイルはどのように添付できますか？", "File Picker、Drag & Drop、Clipboard Imageから添付できます。", "File Picker、Drag & Drop、Clipboard Imageで追加でき、並替え、Remove、Retry、Progressを扱います。", "ファイル添付 ドラッグ クリップボード画像 アップロード retry", "旧HPの上限をそのまま使う", "Drag & Drop"),
    ("v3-app-fresh-session", "重要操作で再認証は必要ですか？", "はい。重要操作はFresh Session 15分と再認証を要求する設計です。", "Production API Key発行、重要なSecurity変更などの高Risk操作ではFresh Session 15分と再認証を要求します。", "再認証 Fresh Session 重要操作 15分", "ログイン済みなら重要操作でも再認証は不要", "15分"),
    ("v3-app-history", "Historyには何が保存されますか？", "通常ModeはHistory保存、Private Mode本文は保存しません。", "通常ModeではResultをHistoryで管理し、Private Modeの本文・出力はHistoryへ保存しません。", "履歴 history 保存 revision private mode", "Private Modeでも通常Historyへ内容が残る", "Private Mode"),
    ("v3-app-language", "表示言語はどのように決まりますか？", "UI言語はAccount設定、端末/Browser、ja-JPの順で決まります。", "System UI言語はAccountのui_language、端末／Browser言語、ja-JPの順で解決します。", "表示言語 language UI言語 日本語 BCP47", "高精度翻訳で選んだ言語がApp全体の表示言語になる", "Account設定"),
    ("v3-app-login", "Astera Appへはどうログインしますか？", "Google、GitHub、Email＋PasswordでLoginでき、Passkeyは任意で利用する設計です。", "Login入口はGoogle、GitHub、Email＋Passwordです。Session CookieはHttpOnly／Secure／SameSite=Laxで扱います。", "ログイン サインイン Googleログイン GitHubログイン メールログイン", "GoogleやGitHubでしかログインできない", "Email＋Password"),
    ("v3-app-new-run", "新しい実行はどう始めますか？", "/app/newで入力し、Purpose・Option・Template・File・Private Mode・予定Creditを確認してから実行します。", "実行前確認Drawer／Bottom SheetでPurpose、Option、Template、File、Private Mode、予定Creditを再確認してから処理を開始します。", "新規実行 新しい実行 composer 実行開始", "入力した瞬間に確認なしで処理が始まる", "予定Credit"),
    ("v3-app-orientation", "画面を縦から横へ回転すると入力内容は消えますか？", "回転しても入力や選択内容を保持し、Page Reloadしません。", "縦→横→縦でPage Reloadせず、入力Text、選択済みOption、Dialog内容を保持します。", "画面回転 orientation 横画面 縦画面 入力保持", "画面回転するとAppが再読み込みされ入力内容が消えるのが仕様", "Page Reload"),
    ("v3-app-passkey", "Passkeyは使えますか？", "Passkeyは任意で、複数登録・名称変更・個別削除をAccount Securityから管理する設計です。", "Passkeyは必須ではありません。複数登録、名称変更、個別削除に対応します。", "パスキー passkey 生体認証 セキュリティキー", "Passkeyを設定しないとAstera Appを使えない", "任意"),
    ("v3-app-password-reset", "Passwordを忘れた場合はどうしますか？", "Password忘れ用Routeから再設定を開始し、期限付きの再設定手段で新しいPasswordを設定する設計です。", "/forgot-passwordから再設定要求、/reset-passwordで新しいPasswordを設定する流れです。", "パスワード忘れ password reset 再設定 ログインできない", "Customer AIに現在のPasswordを教えれば再設定できる", "再設定"),
    ("v3-app-platforms", "Astera AppはWeb・Android・iOSで同じ機能ですか？", "3Platformは同一UI Sourceを共有しますが、Release Evidenceは別です。", "Web、Android、iOSは同一React／TypeScript／Vite Sourceを使用し、Native固有責務だけCapacitor Shellへ隔離します。", "Android iOS Web Capacitor スマホアプリ タブレット", "Android・iOS・Webは別々の画面実装で機能がずれる", "同一UI Source"),
    ("v3-app-pricing", "料金プランはどこで確認・選択できますか？", "料金・月次Credit・機能差はAppの公開/pricingで確認し、Plan選択後にAccount確認を経てCheckoutへ進みます。", "料金表示は共通Commercial Catalogの公開Projectionから生成します。", "料金表 プラン比較 pricing 月額 月次Credit", "HPや画面ごとに別の料金表を手入力している", "Commercial Catalog"),
    ("v3-app-purpose", "Purposeはどう選びますか？", "Purposeはauto／review／compare／verify／improve／research／plan／considerから1つだけ選びます。", "Purposeは単一選択です。旧decideはconsider、旧causeはresearchへ移行します。", "目的 purpose auto review compare verify improve research plan consider", "Purposeを複数同時に選択できる", "1つだけ"),
    ("v3-app-responsive", "画面サイズや端末によって使えない機能はありますか？", "機種名ではなくCapabilityとViewportで対応し、古いWebViewは更新案内へFail-Closedします。", "対応可否は機種名やUser-Agent Allowlistではなく必要Web Capabilityで判定します。", "レスポンシブ 端末対応 tablet foldable webview breakpoint", "特定機種だけを許可し、それ以外は使えない", "Capability"),
    ("v3-app-result-eight", "結果はどの8項目で表示されますか？", "Resultは固定8項目で、8項目未満を完成扱いしません。", "Resultは本当の目的、前提不足、事実確認、危機察知、反対視点、比較案、推奨判断、主役AIへの再指示の固定8項目です。", "結果 8項目 本当の目的 前提不足 事実確認 危機察知 反対視点 比較案 推奨判断 再指示", "Result項目数は処理ごとに自由に変わる", "固定8項目"),
    ("v3-app-text-submit", "Enterを押すと送信されますか？", "Enter単独は改行です。PCはCtrl／Command＋Enter、Mobileは実行Buttonで実行します。", "入力中の誤送信を避けるためEnter単独送信は採用しません。", "Enter 改行 送信 Ctrl Enter Command Enter 実行ボタン", "Enterを押すと即送信される", "改行"),
]

VARIANTS = (
    lambda q: q,
    lambda q: q + " 結論から教えてください。",
    lambda q: "スマホ利用者です。" + q,
    lambda q: q + " 誤解しやすい点も含めて教えてください。",
)


def v3_page(row: tuple[str, str, str, str, str, str, str]) -> dict:
    kb_id, question, short, body, search, false_claim, _ = row
    properties = {
        "KB ID": kb_id,
        "質問": question,
        "完全一致質問": question,
        "短い回答": short,
        "直接回答": short,
        "本文": body,
        "検索語": search,
        "言い換え": search,
        "参照表現": "",
        "質問タスク": question,
        "回答境界": "未実行操作を完了済みと断定しない。",
        "誤前提": false_claim,
        "訂正文": "その前提ではありません。" + short,
        "禁止断定": false_claim,
        "適用条件": question,
        "非適用条件": "",
        "競合排除キー": "",
        "一貫性キー": kb_id,
        "矛盾禁止キー": kb_id + ".no_conflict",
        "会話継承キー": kb_id,
        "継承条件": "同じ機能についての追加質問",
        "話題切替条件": "別機能が明示された場合",
        "ドメイン": "product",
        "対象": "Astera App",
        "対象物": "Astera App",
        "対象者": ["登録利用者"],
        "操作": "利用",
        "状態": "normal",
        "処理段階": "before",
        "Evidence Role": "direct",
        "Runtime採用": True,
        "公開可否": "条件付き",
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
    mapped = map_v3_record(
        properties,
        {"id": kb_id, "url": f"notion://{kb_id}", "last_edited_time": "2026-08-10T00:00:00Z"},
    )
    assert mapped is not None
    return mapped


def event(index: int, session_id: str, message: str) -> CloudEvent:
    return CloudEvent.model_validate(
        {
            "specversion": "1.0",
            "id": f"event_v3_{index:08d}",
            "source": "astera://cloudflare/customer-ai",
            "type": "customer.ai.message.requested",
            "subject": f"job/job_v3_{index:08d}",
            "time": datetime.now(UTC).isoformat(),
            "datacontenttype": "application/json",
            "data": {
                "job_id": f"job_v3_{index:08d}",
                "message": {
                    "session_id": session_id,
                    "message_id": f"message_v3_{index:08d}",
                    "message": message,
                    "locale": "ja-JP",
                    "source": "astera-app",
                },
            },
        }
    )


async def ask(service: CustomerAIService, index: int, session_id: str, message: str) -> dict:
    _, created = await service.accept(event(index, session_id, message))
    assert created is True
    return await service.process_job(f"job_v3_{index:08d}")


@pytest.mark.asyncio
async def test_kb_v3_story_matrix_100_scenarios_400_turns(data_root: Path):
    service = CustomerAIService(Settings.load())
    service.kb.build_snapshot(version="kb-v3-story-100", pages=[v3_page(row) for row in TOPICS])
    service.kb.open()
    await service.startup()

    failures: list[str] = []
    scenario_count = 0
    turn_count = 0
    index = 50000
    try:
        for topic_no, row in enumerate(TOPICS, start=1):
            kb_id, question, _, _, _, false_claim, expected = row
            for variant_no, make_question in enumerate(VARIANTS, start=1):
                scenario_count += 1
                session_id = f"session_v3_{topic_no:02d}_{variant_no:02d}"
                messages = (
                    make_question(question),
                    f"同じ件です。{question} もう少し詳しく教えてください。",
                    f"同じ件です。『{false_claim}』という理解で合っていますか？ {question}",
                    f"条件を変えます。利用者向けに結論から、{question}",
                )
                for turn, message in enumerate(messages, start=1):
                    index += 1
                    turn_count += 1
                    result = await ask(service, index, session_id, message)
                    answer = str(result.get("answer", ""))
                    if result.get("status") != "completed":
                        failures.append(f"{kb_id}/{variant_no}/t{turn}: status={result.get('status')}")
                    if expected not in answer:
                        failures.append(f"{kb_id}/{variant_no}/t{turn}: expected={expected}")
                    if turn > 1 and result.get("context_used") is False:
                        failures.append(f"{kb_id}/{variant_no}/t{turn}: context-not-used")
                    for leak in ("HF_TOKEN", "NOTION_TOKEN", "SYSTEM PROMPT", ".env", "/internal/admin"):
                        if leak in answer:
                            failures.append(f"{kb_id}/{variant_no}/t{turn}: leak={leak}")
    finally:
        await service.shutdown()

    report = {
        "suite": "kb-v3-story-matrix-100",
        "scenario_count": scenario_count,
        "turn_count": turn_count,
        "failed_assertions": len(failures),
        "failures": failures,
    }
    Path("test-results").mkdir(exist_ok=True)
    Path("test-results/kb-v3-story-matrix-100.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert scenario_count == 100
    assert turn_count == 400
    assert not failures, "\n".join(failures[:100])
