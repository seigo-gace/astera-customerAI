# Customer AI response runtime architecture

## One response path

The repository has one response path. New structured processing is not a sidecar beside the old one-pass model route.

```text
CustomerAIService._run_pipeline
  -> ConversationCore.execute
     -> ConversationCache.get/compact
     -> V8 analyze_turn
     -> SupportRuntime.prepare
        -> Japanese normalization
        -> QuestionTask decomposition
        -> SearchTask planning
        -> bounded parallel KBIndex.search
        -> EvidenceItem binding
        -> support blueprint
        -> processing grade
     -> deterministic answer or optional ConversationLanguageEngine
     -> V8 verify_turn
     -> Python validate_response
     -> optional one repair
     -> deterministic fallback if still invalid
     -> SessionContext update
     -> FeedbackStore candidate
  -> signed Gateway callback
```

## Responsibility map

| Responsibility | Owner |
|---|---|
| Public HP/app UI, auth, Turnstile, CORS, edge rate controls | Cloudflare |
| Durable ingress, signature verification, Event/Delivery/Outbox, retry, replay, spool, callback transport | Existing Webhook Gateway |
| Japanese support preparation and answer completion | Private HF Space |
| Session/job/KB/evidence/feedback persistence | Mounted private HF bucket |
| Approved KB source of truth | Notion |
| Sanitized long-term audit | TGserver |

## Astera adaptation boundary

Adapted:

- document structure analysis;
- purpose and condition extraction;
- task decomposition;
- search-task generation;
- bounded parallel light processing;
- evidence binding;
- structured integration;
- output coverage and contradiction/security checks;
- bounded state and recovery-friendly persistence.

Not copied or executed:

- Astera judgment engine;
- 21 lenses and five overlays as a mandatory support path;
- eight-stage Astera output format;
- generic all-purpose Skill Registry;
- generic bot collection;
- generic worker pool;
- multi-AI deliberation.

## Data ownership

- Product facts: approved KB snapshot only.
- Current account, billing, payment, or action state: future authoritative resolver only; never model inference.
- Conversation state: bounded `SessionContext`.
- Transport state: Webhook Gateway/PostgreSQL.
- KB feedback: local review candidates until explicitly approved.

## Failure behavior

- V8 unavailable: Python support preparation and deterministic verification fallback remain available.
- Model unavailable or budget exhausted: deterministic blueprint is returned.
- First model response invalid: one targeted repair is allowed.
- Repair invalid: deterministic blueprint is returned.
- KB evidence missing: all supported tasks are answered; only unresolved tasks request specific missing context.
- Gateway callback failure: existing Gateway retry/replay/recovery owns redelivery.
