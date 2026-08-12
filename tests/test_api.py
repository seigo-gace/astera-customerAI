from fastapi.testclient import TestClient
import app as app_module
def test_health_and_not_ready_contract():
    app_module.set_work(None); client=TestClient(app_module.app); assert client.get("/health").status_code==200; assert client.get("/ready").json()["status"]=="not_ready"; assert client.post("/v1/customer-ai/messages",json={"session_id":"s","message":"q"}).status_code==503
