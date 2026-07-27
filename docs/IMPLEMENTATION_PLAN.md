# Astera Customer AI implementation plan

## Fixed purpose

Astera Customer AI is a specialized response-handling runtime for Astera and the owner's development products. It is not a generic AI, a multi-AI system, a standalone lightweight-model agent, or an FAQ wall.

The runtime must answer first questions and follow-up questions consistently, explain concrete conditions and procedures, and lead the user as far as possible toward resolution without transferring ordinary support work back to the owner.

## Public and private boundaries

```text
asterav8.jp / app.asterav8.jp
  -> Cloudflare public UI and API edge
  -> existing Webhook Gateway
  -> private Hugging Face Space
  -> mounted private HF bucket
  -> approved Notion KB snapshot
  -> sanitized TGserver audit
```

- Cloudflare owns the public HP/app interface, Turnstile, authentication, CORS, request limits, short-lived job lookup, and result display.
- The existing Webhook Gateway remains the durable API boundary for signature checks, persistence, outbox delivery, retries, replay, spool recovery, callbacks, and TGserver routing.
- The private HF Space owns the Customer AI response runtime. Browsers never call it directly.
- The private HF bucket stores bounded jobs, sessions, KB snapshots, evidence cache, feedback candidates, and runtime state.
- Notion remains the approved KB source of truth. Runtime feedback never auto-publishes to Notion.
- TGserver receives sanitized long-term operational and audit records.

No new Contabo Customer AI server, second webhook platform, external paid AI API, or multi-model coordinator is introduced.

## Response runtime

```text
Input safety and Japanese normalization
  -> conversation state and reference resolution
  -> document/question task decomposition
  -> bounded search-task planning
  -> parallel approved-KB retrieval
  -> task/evidence binding
  -> support blueprint
  -> deterministic answer when sufficient
  -> optional lightweight Japanese composition
  -> task/evidence/claim verification
  -> at most one violation-targeted repair
  -> bounded session update
  -> anonymized deduplicated KB feedback candidate
```

The implementation adapts the useful parts of Astera's document analysis, task decomposition, search planning, evidence organization, structured integration, and output verification. It does not execute the Astera judgment engine and does not copy every Astera lens or output stage.

## Processing grades

- `L0_DETERMINISTIC_EXACT`: one confirmed KB answer; no model call.
- `L1_STRUCTURED_COMPOSE`: structured deterministic assembly from one or more confirmed KB entries.
- `L2_MULTI_TASK_COMPOSE`: multiple questions or comparison; lightweight model may compose the prepared blueprint.
- `L3_CONTEXT_REQUIRED`: a specific task lacks confirmed evidence; answer all resolvable parts and request only the missing information for unresolved tasks.

Processing grade controls work performed around the model. It is not a multi-model escalation ladder.

## Runtime modules

- `runtime/support.py`: Japanese normalization, question tasks, search tasks, evidence binding, blueprint, response validation, feedback candidates.
- `runtime/control.py`: one response pipeline, model gate, one-repair limit, completion state, session update.
- `runtime/conversation.py`: bounded persistent goal, topic, question ledger, evidence reuse, blueprint, and recent turns.
- `runtime/kb.py`: approved Notion KB snapshot and bounded query cache.
- `v8/server.mjs`: lightweight deterministic decomposition and response coverage/security checks.
- `runtime/model.py`: exchangeable lightweight Japanese composition component with no routing, fact, action, or completion ownership.
- `runtime/service.py`: durable job processing and existing Gateway callbacks.

## Webhook Gateway integration

The public Cloudflare API sends a signed `customer.ai.message.requested` CloudEvent to the existing Gateway. The Gateway durably accepts and delivers it to the private HF Space. The Space emits signed job and result CloudEvents back through the Gateway.

Required properties:

- idempotent request and result identifiers
- HMAC over the exact body and timestamp
- provider/source rate policy
- durable Event, Delivery, and Outbox records
- retry and replay
- private HF destination and token
- bounded callback body
- downstream idempotency
- sanitized audit records

The Gateway owns transport success. Customer AI owns answer completion. Neither is allowed to claim an external business action was completed unless a future authoritative resolver returns a verified result.

## Cloudflare publication

- `asterav8.jp`: anonymous HP support UI with Turnstile and strict limits.
- `app.asterav8.jp`: authenticated support UI with longer conversation continuity and current app-screen context.
- `api.asterav8.jp`: public edge endpoints for message submission and job polling; never exposes HF tokens or internal Space URLs.

The initial result-delivery method is bounded polling against Cloudflare short-lived job state after the Gateway callback. It tolerates HF cold starts and browser reloads without keeping a long connection open.

## HF operation

- Private Space.
- Private mounted bucket at `/data/customer-ai`.
- Pinned lightweight model revision.
- Model disabled until required secrets and deployment checks are complete.
- Internal daily GPU ledger remains below the available PRO quota.
- Deterministic KB/Skill response remains available when the model is disabled or budget-exhausted.
- No paid Inference Endpoint or external model API fallback.

## KB feedback boundary

Every normalized question may produce a deduplicated review candidate containing anonymized text, question tasks, search tasks, matched KB IDs, detected gap types, and validation failures.

Candidates always carry:

```json
{
  "approval_required": true,
  "auto_publish": false
}
```

Maintenance/review may classify candidates as search-term additions, follow-up additions, content gaps, procedure gaps, condition/exception gaps, or new-page candidates. Only approved changes are written to Notion and rebuilt into the runtime snapshot.

## Completion gates

A change is not complete merely because code was added. Release requires:

1. all detected questions are answered or explicitly tracked as unresolved;
2. factual claims map only to supplied evidence;
3. topic and user goal remain consistent across follow-ups;
4. no generic non-answer replaces a resolvable explanation;
5. no private implementation or model identity leaks;
6. no unexecuted action is claimed as complete;
7. model-free operation remains usable;
8. one repair is the strict maximum;
9. feedback is anonymized, deduplicated, review-only, and non-publishing;
10. Python, V8, API, storage, recovery, security, and end-to-end tests pass before HF deployment.
