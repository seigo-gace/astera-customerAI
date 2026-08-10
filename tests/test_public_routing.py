from runtime.conversation import ConversationCache
from runtime.schemas import MessagePayload
from runtime.service import route_topic


def payload(**changes):
    values = {
        "session_id": "session_1234567890",
        "message_id": "message_1234567890",
        "message": "Asteraについて教えて",
        "locale": "ja-JP",
        "source": "astera-hp",
        "response_mode": "auto",
        "mode_source": "auto",
        "current_path": "/ja/",
    }
    values.update(changes)
    return MessagePayload(**values)


def test_public_routing_fields_survive_validation_and_path_is_bounded():
    request = payload(
        response_mode="technical",
        mode_source="selected",
        current_path="/developer/architecture/?from=hp#section",
    )
    assert request.response_mode == "technical"
    assert request.mode_source == "selected"
    assert request.current_path == "/developer/architecture/"


def test_selected_mode_overrides_current_path_and_auto_uses_page_context():
    selected = payload(
        response_mode="technical",
        mode_source="selected",
        current_path="/pricing/",
    )
    assert route_topic(selected, "billing") == "technical"

    automatic = payload(
        response_mode="auto",
        mode_source="auto",
        current_path="/pricing/",
    )
    assert route_topic(automatic, "technical") == "billing"


def test_auto_mode_preserves_previous_conversation_topic_when_page_has_no_route():
    request = payload(current_path="/ja/product/value/")
    assert route_topic(request, "investor") == "investor"


def test_conversation_delete_removes_memory_and_persistent_session(tmp_path):
    cache = ConversationCache(
        tmp_path,
        ttl_seconds=1800,
        max_sessions=8,
        max_turns=12,
    )
    session_id = "session_1234567890"
    context = cache.get(session_id).model_copy(update={"active_topic": "technical"})
    cache.save(context)
    context_path = tmp_path / "sessions" / session_id / "context.json"
    assert context_path.exists()

    assert cache.delete(session_id) is True
    assert not (tmp_path / "sessions" / session_id).exists()
    assert cache.delete(session_id) is False
