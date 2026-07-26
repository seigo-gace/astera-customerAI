---
title: Astera Customer AI
emoji: ✦
colorFrom: gray
colorTo: yellow
sdk: gradio
sdk_version: 6.5.1
app_file: app.py
pinned: false
license: other
short_description: Private Script/V8/Astera-first customer support runtime
---

# Astera Customer AI

Private Hugging Face Space runtime for Astera customer support.

## Responsibility

- Cloudflare: public UI/API edge only.
- Existing Webhook Gateway: durable ingress, delivery, retry, replay, spool, and TGserver routing.
- This Space: Script processing, Node.js V8 workflow, Astera judgment materials, SQLite FTS5 KB search, optional ZeroGPU language composition, response validation, question analysis, and KB synchronization.
- Private HF Storage Bucket mounted at `/data/customer-ai`: jobs, sessions, runtime KB snapshots, and KB improvement candidates.
- Notion: approved Customer AI KB source of truth.
- TGserver: long-term sanitized audit and operational logs.

## Required Space settings

Create this repository as a **Private Gradio Space** and mount a private bucket read-write:

```text
hf://buckets/G-ACE/astera-customerai-data:/data/customer-ai
```

Required secrets:

```text
CUSTOMER_AI_HMAC_SECRET
HF_TOKEN
GATEWAY_CALLBACK_URL
GATEWAY_CALLBACK_SECRET
NOTION_TOKEN
NOTION_DATA_SOURCE_ID
```

Optional configuration is documented in `.env.example`.

## API

- `GET /healthz`
- `GET /readyz`
- `POST /internal/customer-ai/accept`
- `GET /internal/customer-ai/jobs/{job_id}`
- Gradio API `customer_ai_process`
- `POST /internal/kb/sync`
- `POST /internal/recovery/run`

The Space must not be called directly by browsers. Requests arrive through the existing Webhook Gateway.

## Local verification

```bash
python -m pip install -r requirements-dev.txt
node --version
pytest -q
python scripts/review.py --all
```

## Fixed external sources

- Astera v8: `seigo-gace/astera_v8@67837b0f65ccc42fce5875fc82a1efa3561068ea`
- Default model: `Qwen/Qwen3-4B-Instruct-2507` (revision must be supplied through `CUSTOMER_AI_MODEL_REVISION` before production inference is enabled)

Astera is not an AI. It is used as an external judgment-material runtime for the primary language model and deterministic response pipeline.
