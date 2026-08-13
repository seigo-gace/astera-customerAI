import pytest

from runtime.hf_client import HFChatClient, HF_MODEL_4B, HF_MODEL_8B


def test_role_model_allowlist_accepts_only_4b_and_8b():
    assert HFChatClient(token="x", model_id=HF_MODEL_4B).model_id == HF_MODEL_4B
    assert HFChatClient(token="x", model_id=HF_MODEL_8B).model_id == HF_MODEL_8B
    with pytest.raises(ValueError, match="model_drift"):
        HFChatClient(token="x", model_id="other/model")
