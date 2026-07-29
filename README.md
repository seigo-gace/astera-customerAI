---
title: Astera Customer AI
emoji: ✨
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
  -> normalize
  -> bounded session context
  -> document/question decomposition
  -> task classification
  -> per-task KB search planning
  -> Notion retrieval
  -> evidence binding
  -> answer blueprint
  -> deterministic answer or optional model composition
  -> deterministic verification
  -> one bounded repair
  -> generic Webhook Gateway reply event
  -> app/HP receiver
```

## Runtime topology

```text
Cloudflare Customer AI Edge
  -> Universal Webhook Gateway generic POST /internal/events
  -> Private Hugging Face Space
  -> Private Hugging Face Bucket mounted at /data/customer-ai
  -> Notion KB
  -> Universal Webhook Gateway generic POST /internal/events
  -> Cloudflare receiver
```

The browser must not call this private Space directly.

## Generic internal event contract

Ingress event:

- `event_type`: `customer_ai.requested`
- `source_id`: Cloudflare source identity
- `destination_ids`: includes the HF private-runtime destination
- `payload`: normalized customer message, locale, channel, session ID, and optional document

Result event:

- `event_type`: `customer_ai.completed`
- `source_id`: `hf-private-runtime`
- `destination_ids`: includes the app receiver
- `payload`: answer, citations, verification report, session state summary, and deduplicated feedback candidate references

No Customer-AI-specific route is added to the universal Gateway.

## Runtime endpoints

- `GET /healthz`
- `GET /readyz`
- `POST /internal/events`
- `POST /internal/customer-ai`

The direct customer endpoint exists for private runtime verification and controlled internal callers. The production integration uses the generic event endpoint.

## Authentication

The Space verifies the same generic Gateway HMAC headers used by the repository adapter:

- `X-Webhook-Timestamp`
- `X-Webhook-Nonce`
- `X-Webhook-Signature`

The signing secret is injected as `CUSTOMER_AI_HMAC_SECRET`.

## Runtime persistence

All writable runtime state lives under `CUSTOMER_AI_DATA_ROOT`.

The deployment provisions a private HF Bucket named `G-ACE/astera-customerai-data` and mounts it at:

```text
/data/customer-ai
```

Persisted files include:

- session state;
- KB search cache;
- feedback candidates;
- feedback deduplication index;
- owner-review feedback JSONL.

## Optional model

The default runtime mode is `CUSTOMER_AI_ENABLE_MODEL=0`.

The deterministic path remains operational without loading a model. When enabled, the model is pinned to:

```text
Qwen/Qwen3-4B-Instruct-2507
revision: cdbee75f17c01a7cc42f958dc650907174af0554
```

The model may compose language from the answer blueprint. It does not own task decomposition, KB retrieval, evidence binding, verification, or feedback approval.

## Important environment variables

| Name | Purpose |
|---|---|
| `CUSTOMER_AI_HMAC_SECRET` | Generic Gateway request authentication |
| `CUSTOMER_AI_DATA_ROOT` | Mounted persistent data root |
| `NOTION_TOKEN` | Approved KB access |
| `NOTION_DATA_SOURCE_ID` | Approved KB data source |
| `INTERNAL_EVENT_API_URL` | Generic Gateway internal event endpoint |
| `INTERNAL_EVENT_API_TOKEN` | Gateway internal bearer token |
| `CUSTOMER_AI_ENABLE_MODEL` | Enables optional lightweight model |
| `CUSTOMER_AI_MODEL_ID` | Pinned model ID |
| `CUSTOMER_AI_MODEL_REVISION` | Pinned model revision |
| `CUSTOMER_AI_GPU_DAILY_BUDGET_SECONDS` | Internal daily GPU budget guard |
| `INTERNAL_EVENT_SOURCE_ID` | Source ID for result events |
| `INTERNAL_EVENT_RESULT_DESTINATION_ID` | Result receiver destination ID |

## Deployment

The deployment workflow:

1. checks out `main`;
2. runs the complete repository verification suite;
3. creates or reuses the private Bucket;
4. creates or reuses the private Space;
5. attaches the Bucket at `/data/customer-ai`;
6. configures Space secrets and variables;
7. uploads the current `main` source;
8. restarts the Space;
9. verifies Bucket write/read/delete;
10. waits for authenticated `/healthz` and `/readyz` responses.

The deployment uses the registered Hugging Face access secret from GitHub Actions and never places GitHub in the production request path.
