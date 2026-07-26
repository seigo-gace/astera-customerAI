from __future__ import annotations

from datetime import UTC, datetime

import pytest

from runtime.config import Settings
from runtime.schemas import CloudEvent
from runtime.service import CustomerAIService


@pytest.mark.asyncio
async def test_accept_and_process_without_model(data_root):
    settings = Settings.load()
    service = CustomerAIService(settings)
    service.kb.build_snapshot(
        version="test",
        pages=[
            {
                "id": "kb-credit",
                "質問": "購入したクレジットが反映されません",
                "短い回答": "決済状態と付与状態を確認します。",
                "本文": "購入時刻を確認し、決済とクレジット付与を順に照合します。",
                "検索語": "クレジット 未反映 買ったのに増えない",
                "公開状態": "公開",
                "実装状態": "文書確認済み",
                "要再確認": False,
            }
        ],
    )
    await service.startup()
    event = CloudEvent(
        id="event_12345678",
        source="astera://cloudflare/customer-ai",
        type="customer.ai.message.requested",
        subject="job/job_12345678",
        time=datetime.now(UTC),
        data={
            "job_id": "job_12345678",
            "message": {
                "session_id": "session_12345678",
                "message_id": "message_12345678",
                "message": "購入したクレジットが反映されません",
                "locale": "ja-JP",
                "source": "astera-app",
            },
        },
    )
    record, created = await service.accept(event)
    assert created is True
    assert record["status"] == "accepted"
    result = await service.process_job("job_12345678")
    assert result["status"] == "completed"
    assert result["ai_invoked"] is False
    assert "決済状態" in result["answer"]
    await service.shutdown()


@pytest.mark.asyncio
async def test_accept_rejects_unsafe_job_id(data_root):
    settings = Settings.load()
    service = CustomerAIService(settings)
    event = CloudEvent(
        id="event_12345678",
        source="astera://cloudflare/customer-ai",
        type="customer.ai.message.requested",
        subject="job/bad",
        time=datetime.now(UTC),
        data={
            "job_id": "../../escape",
            "message": {
                "session_id": "session_12345678",
                "message_id": "message_12345678",
                "message": "hello",
                "locale": "en",
                "source": "astera-app",
            },
        },
    )
    with pytest.raises(ValueError):
        await service.accept(event)


@pytest.mark.asyncio
async def test_recovery_requeues_stale_accepted_job(data_root):
    from datetime import timedelta
    settings = Settings.load()
    service = CustomerAIService(settings)
    event = CloudEvent(
        id="event_recovery1",
        source="astera://cloudflare/customer-ai",
        type="customer.ai.message.requested",
        subject="job/job_recovery1",
        time=datetime.now(UTC),
        data={
            "job_id": "job_recovery1",
            "message": {
                "session_id": "session_recovery1",
                "message_id": "message_recovery1",
                "message": "hello",
                "locale": "en",
                "source": "astera-app",
            },
        },
    )
    await service.accept(event)
    service.jobs.update_job("job_recovery1", updated_at=datetime.now(UTC) - timedelta(minutes=2))
    record = service.jobs.get_job("job_recovery1")
    data = record.model_dump(mode="json")
    data["updated_at"] = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    service.jobs.store.put_json(service.jobs.job_dir("job_recovery1") / "status.json", data)
    result = await service.recover_once()
    assert result["count"] == 1
    assert service.jobs.get_job("job_recovery1").status == "retrying"
