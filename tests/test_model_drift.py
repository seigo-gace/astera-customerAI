import pytest
from runtime.hf_client import HFChatClient

def test_model_drift_fails_closed():
    with pytest.raises(ValueError,match="model_drift"):
        HFChatClient(token="x",model_id="other/model")
