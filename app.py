from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from runtime.service import CustomerAIWork

app = FastAPI(title="Astera Customer AI", version="0.0.0")
WORK: CustomerAIWork | None = None

class MessageRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=200000)

def set_work(work: CustomerAIWork | None) -> None:
    global WORK
    WORK = work

@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "zero_gpu": False}

@app.get("/ready")
async def ready() -> dict[str, Any]:
    return {"status": "ready" if WORK is not None else "not_ready", "three_role_resident": WORK is not None, "zero_gpu": False}

@app.post("/v1/customer-ai/messages")
async def customer_ai_message(req: MessageRequest) -> dict[str, Any]:
    if WORK is None:
        raise HTTPException(status_code=503, detail="customer_ai_not_ready")
    return (await WORK.run(req.session_id, req.message)).model_dump(mode="json")
