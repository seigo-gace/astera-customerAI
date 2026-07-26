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
short_description: Private controlled Script/Skill/V8/Bot customer-support runtime
---

# Astera Customer AI

Private Hugging Face Space runtime for Astera customer support.

## Fixed execution principle

The language model is not called as a standalone customer-support agent.

```text
Input safety and normalization
  → Controlled Execution Contract
  → Structured `$` Skill selection
  → V8 parallel light workers
  → KB evidence collection
  → Deterministic answer rendering
  → Optional language engine inside the prepared contract
  → V8 verification and Completion Gate
  → Question Insight and routine bots
```

Astera itself is **not executed** in this repository. The runtime reuses engineering structures cultivated in Astera and KAGRRA—deterministic scripts, structured skills, V8 parallel processing, state capsules, evidence gates, recovery, and routine bots—without calling the Astera judgment engine.

## Responsibility

- Cloudflare: public UI/API edge only.
- Existing Webhook Gateway: durable ingress, delivery, retry, replay, spool, and TGserver routing.
- This Space: Controlled Execution Core, structured skills, Node.js V8 worker pool, SQLite FTS5 KB search, optional ZeroGPU language composition, response validation, question analysis, and routine bots.
- Private HF Storage Bucket mounted at `/data/customer-ai`: jobs, sessions, runtime KB snapshots, bot state, and KB improvement candidates.
- Notion: approved Customer AI KB source of truth.
- TGserver: long-term sanitized audit and operational logs.

## Reused implementation materials

The implementation adapts verified patterns from `seigo-gace/modular-catalog` without runtime dependency on that repository:

- Worker lifecycle, timeout, crash recovery, one-time regeneration
- Human-context deterministic signals
- Deterministic routing and input normalization
- Safe JSON and secret masking
- Structured logging and durable outbox boundaries
- Language-provider adapter boundary

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
npm test
python scripts/review.py --all
```

## Fixed engine source

- Default language engine: `Qwen/Qwen3-4B-Instruct-2507`
- The revision must be pinned before inference is enabled.
- Inference stays disabled by default.
- The engine receives an Execution Contract, selected Skill results, State Capsule, and verified Evidence. It cannot own routing, tools, action execution, or completion.
