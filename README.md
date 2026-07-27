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
short_description: Private Astera-derived customer-response runtime
---

# Astera Customer AI

Private Hugging Face Space runtime for answering questions about Astera and the owner's development products.

## Fixed purpose

This is not a generic AI, multi-AI coordinator, FAQ wall, or standalone lightweight-model agent.

It is a specialized response system that must:

- understand Japanese first questions and follow-up questions;
- preserve the user's goal, active topic, confirmed details, answered questions, and unresolved questions;
- decompose documents and multi-question messages into explicit tasks;
- search the approved KB for each task;
- bind evidence to the task it answers;
- construct an answer blueprint before any model call;
- use a lightweight model only when natural composition is necessary;
- verify coverage, evidence, topic, unsafe claims, and private information;
- allow at most one violation-targeted repair;
- create anonymized, deduplicated, review-only KB feedback candidates.

Astera itself is not executed. The runtime adapts the useful Astera/KAGRRA engineering logic for document analysis, task decomposition, search planning, evidence organization, answer integration, bounded state, and output verification.

## Processing path

```text
Current message
  + bounded conversation state
  -> Japanese normalization and reference context
  -> question-task decomposition
  -> bounded per-task search planning
  -> parallel approved-KB retrieval
  -> task/evidence binding
  -> support blueprint
  -> deterministic response when sufficient
  -> optional lightweight Japanese composition
  -> V8 and Python completion/security verification
  -> at most one repair
  -> bounded state and KB-feedback update
```

## Processing grades

- `L0_DETERMINISTIC_EXACT`: one confirmed exact answer; no model call.
- `L1_STRUCTURED_COMPOSE`: deterministic structured answer from confirmed KB evidence.
- `L2_MULTI_TASK_COMPOSE`: multiple questions or comparison; optional model composition from the prepared blueprint.
- `L3_CONTEXT_REQUIRED`: answer all resolvable tasks and request only task-specific missing information.

These grades change the processing around one lightweight model. They do not create a multi-model system.

## Conversation state

Each session stores only bounded response context:

- user goal;
- active topic;
- confirmed details;
- answered question IDs;
- unresolved questions;
- question ledger;
- recently used KB IDs;
- reusable evidence summaries;
- last support blueprint;
- latest bounded conversation turns.

State is cached in memory and persisted to the mounted private bucket for restart recovery.

## Responsibility boundaries

- Cloudflare: public HP/app UI, Turnstile, authentication, CORS, request limits, short-lived job lookup, and result display.
- Existing Webhook Gateway: durable ingress, exact-body signature validation, persistence, Outbox delivery, retry, replay, spool recovery, callbacks, and TGserver routing.
- This private Space: support preparation Runtime, bounded session state, approved-KB retrieval, optional model composition, verification, and feedback candidates.
- Private HF bucket at `/data/customer-ai`: jobs, sessions, KB snapshots, evidence/cache state, and review candidates.
- Notion: approved Customer AI KB source of truth.
- TGserver: sanitized long-term operational and audit logs.

The Space must never be called directly by browsers.

## Required Space settings

Create a Private Gradio Space and mount a private bucket read-write:

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

Public browsers call Cloudflare. Cloudflare sends signed requests through the existing Webhook Gateway, which delivers them to these private endpoints.

## KB feedback

Runtime feedback never publishes directly to Notion. Candidates are anonymized, deduplicated, and stored with:

```json
{
  "approval_required": true,
  "auto_publish": false
}
```

Approved Notion updates are rebuilt into a new runtime KB snapshot.

## Lightweight model

- Default: `Qwen/Qwen3-4B-Instruct-2507`.
- The revision must remain pinned.
- Inference stays disabled until deployment and secrets are verified.
- The model receives a prepared Support Packet containing question tasks, search plans, verified evidence, and an answer blueprint.
- The model cannot own routing, facts, action execution, or completion.
- A deterministic KB response remains available when inference is disabled or its daily budget is exhausted.

## Local verification

```bash
python -m pip install -r requirements-dev.txt
python scripts/review.py --all
ruff check . --select E9,F63,F7,F82
pytest -q
npm test
```

See `docs/IMPLEMENTATION_PLAN.md` for the HF, Gateway, and Cloudflare implementation boundaries.
