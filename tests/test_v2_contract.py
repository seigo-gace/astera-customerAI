from __future__ import annotations

from typing import Any

import pytest

from runtime.model import ConversationLanguageEngine, MISSING_KB_ANSWER
from runtime.notion import NotionClient, PUBLIC_STATUS, V2_FIELDS


class FakeResponse:
    def __init__(self, body: dict[str, Any]):
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class CapturingNotionClient(NotionClient):
    def __init__(self):
        super().__init__("token", "data-source")
        self.payloads: list[dict[str, Any]] = []

    async def _request_with_retry(self, client, method: str, url: str, **kwargs: Any):
        del client, method, url
        self.payloads.append(kwargs["json"])
        return FakeResponse({"results": [], "has_more": False})


@pytest.mark.asyncio
async def test_notion_query_physically_filters_status_public():
    client = CapturingNotionClient()
    rows = await client._query_all(object())

    assert rows == []
    assert client.payloads == [
        {
            "page_size": 100,
            "filter": {
                "property": "Status",
                "select": {"equals": PUBLIC_STATUS},
            },
        }
    ]


class StaticRowsNotionClient(NotionClient):
    async def _query_all(self, client):
        del client
        return [
            {
                "id": "page-1",
                "url": "https://notion.example/page-1",
                "properties": {
                    "Title": {"type": "title", "title": [{"plain_text": "Asteraとは"}]},
                    "Category": {"type": "select", "select": {"name": "製品・概要"}},
                    "Target_Intents": {"type": "rich_text", "rich_text": [{"plain_text": "Asteraとは\nAIですか"}]},
                    "Definitive_Answer": {"type": "rich_text", "rich_text": [{"plain_text": "確定回答"}]},
                    "Exceptions_and_Limits": {"type": "rich_text", "rich_text": [{"plain_text": "例外なし"}]},
                    "Status": {"type": "select", "select": {"name": "公開"}},
                    "LegacyBody": {"type": "rich_text", "rich_text": [{"plain_text": "混入禁止"}]},
                },
            }
        ]


@pytest.mark.asyncio
async def test_notion_fetch_keeps_only_v2_properties():
    client = StaticRowsNotionClient("token", "data-source")
    pages = await client.fetch_pages()

    assert len(pages) == 1
    assert set(pages[0]) == {*V2_FIELDS, "id", "url"}
    assert pages[0]["Title"] == "Asteraとは"
    assert pages[0]["Status"] == "公開"
    assert "LegacyBody" not in pages[0]


def test_model_packet_contains_only_current_message_tasks_and_clean_kb_context():
    packet = {
        "message": "Asteraとは何ですか",
        "conversation": {"turns": [{"role": "assistant", "text": "古い会話"}]},
        "analysis": {"general_knowledge": "混入禁止"},
        "support_packet": {
            "question_tasks": [
                {"task_id": "q1", "text": "Asteraとは何ですか", "answer_shape": "conclusion_and_detail"}
            ],
            "evidence": [
                {
                    "evidence_id": "kb:1",
                    "question": "Asteraとは何ですか",
                    "target": "Asteraとは\nAIですか",
                    "short_answer": "Asteraは判断材料を整えるRuntimeです。",
                    "body": "使わない本文",
                    "answer_boundary": "最終決定を代行しません。",
                    "private_metadata": "混入禁止",
                }
            ],
            "blueprint": {
                "sections": [
                    {"task_id": "q1", "resolved": True, "answer_shape": "conclusion_and_detail", "body": "混入禁止"}
                ],
                "unresolved_task_ids": [],
            },
        },
        "response_rules": {"locale": "ja-JP"},
    }

    safe = ConversationLanguageEngine._sanitize_packet(packet)

    assert set(safe) == {
        "current_user_message",
        "question_tasks",
        "kb_context",
        "answer_blueprint",
        "response_contract",
        "repair",
    }
    assert "conversation" not in safe
    assert "analysis" not in safe
    assert safe["kb_context"] == [
        {
            "Title": "Asteraとは何ですか",
            "Target_Intents": "Asteraとは\nAIですか",
            "Definitive_Answer": "Asteraは判断材料を整えるRuntimeです。",
            "Exceptions_and_Limits": "最終決定を代行しません。",
        }
    ]
    assert safe["response_contract"]["missing_kb_answer"] == MISSING_KB_ANSWER
    assert safe["response_contract"]["no_history_or_memory"] is True
    assert safe["response_contract"]["no_general_knowledge"] is True
    assert safe["response_contract"]["no_escalation"] is True
