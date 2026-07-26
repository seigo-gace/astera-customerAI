from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import gradio as gr
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from runtime import CustomerAIService
from runtime.schemas import CloudEvent
from runtime.security import verify_hmac
from runtime.storage import ConflictError, NotFoundError

MAX_INTERNAL_BODY_BYTES = 10 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
service = CustomerAIService()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await service.startup()
    yield
    await service.shutdown()


api = FastAPI(title="Astera Customer AI", version="1.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)


@api.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@api.get("/readyz")
async def readyz() -> JSONResponse:
    checks = service.readiness()
    ready = checks["data_root"] and checks["v8"]
    return JSONResponse(status_code=200 if ready else 503, content={"ready": ready, "checks": checks})


@api.post("/internal/customer-ai/accept")
async def accept(request: Request) -> JSONResponse:
    if request.headers.get("content-type", "").split(";", 1)[0] not in {"application/json", "application/cloudevents+json"}:
        raise HTTPException(status_code=415, detail="unsupported_content_type")
    declared = int(request.headers.get("content-length", "0") or 0)
    if declared > service.settings.max_input_chars * 4:
        raise HTTPException(status_code=413, detail="payload_too_large")
    raw = await request.body()
    if len(raw) > service.settings.max_input_chars * 4:
        raise HTTPException(status_code=413, detail="payload_too_large")
    timestamp = request.headers.get("x-webhook-timestamp", "")
    signature = request.headers.get("x-webhook-signature", "")
    if not verify_hmac(raw, timestamp, signature, service.settings.hmac_secret):
        raise HTTPException(status_code=401, detail="invalid_signature")
    try:
        event = CloudEvent.model_validate_json(raw)
        record, created = await service.accept(event)
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(status_code=202, content={"accepted": True, "created": created, "job": record})


@api.get("/internal/customer-ai/jobs/{job_id}")
async def job_status(job_id: str) -> dict:
    try:
        record = service.jobs.get_job(job_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="job_not_found") from exc
    result = service.jobs.get_result(job_id)
    return {"job": record.model_dump(mode="json"), "result": result}


@api.post("/internal/kb/sync")
async def kb_sync(request: Request) -> JSONResponse:
    declared = int(request.headers.get("content-length", "0") or 0)
    if declared > MAX_INTERNAL_BODY_BYTES:
        raise HTTPException(status_code=413, detail="payload_too_large")
    raw = await request.body()
    if len(raw) > MAX_INTERNAL_BODY_BYTES:
        raise HTTPException(status_code=413, detail="payload_too_large")
    if not verify_hmac(
        raw,
        request.headers.get("x-webhook-timestamp", ""),
        request.headers.get("x-webhook-signature", ""),
        service.settings.hmac_secret,
    ):
        raise HTTPException(status_code=401, detail="invalid_signature")
    payload = json.loads(raw)
    pages = payload.get("pages")
    version = str(payload.get("version", ""))
    if not version:
        raise HTTPException(status_code=422, detail="version_required")
    if pages is None:
        result = await service.sync_notion_kb(version)
    elif isinstance(pages, list):
        info = await asyncio.to_thread(service.kb.build_snapshot, version=version, pages=pages)
        service.kb.open()
        result = {"version": info.version, "path": str(info.path), "source_pages": len(pages)}
    else:
        raise HTTPException(status_code=422, detail="pages_must_be_list")
    return JSONResponse(status_code=202, content=result)


@api.post("/internal/recovery/run")
async def recovery(request: Request) -> dict:
    raw = await request.body()
    if not verify_hmac(
        raw,
        request.headers.get("x-webhook-timestamp", ""),
        request.headers.get("x-webhook-signature", ""),
        service.settings.hmac_secret,
    ):
        raise HTTPException(status_code=401, detail="invalid_signature")
    result = await service.recover_once()
    return {"accepted": True, **result}


async def process_job(job_id: str) -> dict:
    return await service.process_job(job_id)


with gr.Blocks(title="Astera Customer AI") as demo:
    gr.Markdown("# Astera Customer AI\nPrivate runtime. Use the Astera HP or app.")
    job_id = gr.Textbox(label="Job ID", visible=False)
    output = gr.JSON(visible=False)
    trigger = gr.Button("Process", visible=False)
    trigger.click(
        fn=process_job,
        inputs=job_id,
        outputs=output,
        api_name="customer_ai_process",
        api_description="Process one durably accepted Customer AI job by ID.",
        queue=True,
        concurrency_limit=2,
        api_visibility="private",
    )

app = gr.mount_gradio_app(api, demo, path="/")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860, log_level="info")
