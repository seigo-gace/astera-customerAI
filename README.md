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
- preserve the user's goal and active topic for routing without treating conversation history as factual evidence;
- decompose documents and multi-question messages into explicit tasks;
- search `CustomerAI_Master_v2` for each task;
- bind evidence to the task it answers;
- construct an answer blueprint before any model call;
- use a lightweight model only when natural composition is necessary;
- verify coverage, evidence, unsafe claims, and private information;
- allow at most one violation-targeted repair;
- create anonymized, deduplicated, review-only KB feedback candidates.

Astera itself is not executed. The runtime adapts the useful Astera/KAGRRA engineering logic for document analysis, task decomposition, search planning, evidence organization, answer integration, bounded state, and output verification.

## CustomerAI_Master_v2 contract

The production KB is a separate Notion data source named `CustomerAI_Master_v2`.

Every record has exactly these six business properties:

- `Title`
- `Category`
- `Target_Intents`
- `Definitive_Answer`
- `Exceptions_and_Limits`
- `Status`

The Notion query physically filters `Status == 公開`. Records with `下書き`, `要確認`, or `アーカイブ` never enter the runtime snapshot. Legacy KB records and records missing any required v2 property are rejected.

Only these four KB-derived fields can enter model context:

- `Title`
- `Target_Intents`
- `Definitive_Answer`
- `Exceptions_and_Limits`

Conversation history, session memory, model knowledge, web knowledge, old KB body blocks, internal metadata, and unpublished records are not permitted as factual sources. When no exact published KB evidence exists, the runtime returns:

```text
現在、該当する正確な案内情報が登録されていません
```

It does not invent an answer or redirect the user to staff as a substitute for missing KB evidence.

## Processing path

```text
Current message
  -> normalize
  -> bounded session context for routing only
  -> document/question decomposition
  -> task classification
  -> per-task CustomerAI_Master_v2 search planning
  -> published-property retrieval
  -> evidence binding
  -> answer blueprint
  -> deterministic answer or optional KB-only model composition
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
  -> Notion CustomerAI_Master_v2
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

- bounded session routing state;
- strict v2 KB snapshot and search cache;
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

The model may compose language only from the clean v2 KB context and current question tasks. It does not own task decomposition, KB retrieval, evidence binding, verification, feedback approval, or product facts.

## Important environment variables

| Name | Purpose |
|---|---|
| `CUSTOMER_AI_HMAC_SECRET` | Generic Gateway request authentication |
| `CUSTOMER_AI_DATA_ROOT` | Mounted persistent data root |
| `NOTION_TOKEN` | `CustomerAI_Master_v2` access |
| `NOTION_DATA_SOURCE_ID` | `CustomerAI_Master_v2` data source ID |
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
6. configures Space secrets and variables, including the new v2 data source ID;
7. uploads the current `main` source;
8. restarts the Space;
9. synchronizes a fresh `CustomerAI_Master_v2` snapshot before readiness is accepted;
10. verifies Bucket write/read/delete;
11. waits for authenticated `/healthz` and `/readyz` responses.

The deployment uses the registered Hugging Face access secret from GitHub Actions and never places GitHub in the production request path.
