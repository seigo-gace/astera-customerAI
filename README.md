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
short_description: Private conversation-aware customer-support runtime
---

# Astera Customer AI

Private Hugging Face Space runtime for Astera customer support.

## Focus

The goal is not to add many orchestration parts. The goal is to let a lightweight language model answer the first question and later follow-up questions as one continuous conversation.

```text
Current message
  + cached user goal
  + active topic
  + confirmed details
  + unresolved questions
  + recent turns
  → context-aware KB search
  → lightweight language model
  → V8 consistency check
  → updated conversation cache
```

Astera itself is not executed. Only lightweight structures learned from prior Astera/KAGRRA work are reused where they directly improve answer continuity: input normalization, compact state, bounded cache, KB retrieval, and consistency checking.

## Conversation cache

Each session stores only:

- user goal
- active topic
- confirmed details
- unresolved questions
- recently used KB IDs
- the latest bounded conversation turns

The cache is held in memory for fast follow-ups and persisted to the mounted private bucket so the conversation can continue after a process restart. Old turns and old sessions are bounded by configuration.

## Responsibility

- Cloudflare: public UI/API edge only.
- Existing Webhook Gateway: durable ingress, delivery, retry, replay, spool, and TGserver routing.
- This Space: conversation cache, V8 turn analysis, cached KB search, lightweight model response, and consistency verification.
- Private HF Storage Bucket mounted at `/data/customer-ai`: jobs, session context, and runtime KB snapshots.
- Notion: approved Customer AI KB source of truth.
- TGserver: long-term sanitized audit and operational logs.

## Required Space settings

Create this repository as a Private Gradio Space and mount a private bucket read-write:

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
pytest -q
npm test
python scripts/review.py --all
```

## Lightweight model

- Default: `Qwen/Qwen3-4B-Instruct-2507`
- The model revision must be pinned before inference is enabled.
- Inference stays disabled by default.
- Every model call receives the current message, compact session context, turn analysis, and the matched KB evidence.
- The model does not receive unrelated system internals or unlimited conversation history.
