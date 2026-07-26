# Three-pass implementation review record

This file records the three required independent review passes. Each pass uses a different prompt and evaluates a different failure surface. Findings are applied before the next pass.

## Pass 1 — Distributed architecture and concurrency

**Prompt:** Review concurrency, durability, idempotency, state ownership, failure recovery, and responsibility boundaries. Do not focus on style.

Applied corrections:

- Durable accept and process execution are separated.
- Job request creation is idempotent by content hash.
- Job and session leases use exclusive creation and stale-lease recovery.
- Session events are append-only; `state.json` is a reconstructable snapshot.
- Existing Gateway remains the only durable transport boundary.
- Cloudflare owns no Customer AI source-of-truth state.

## Pass 2 — Security and information protection

**Prompt:** Review secrets, PII, prompt injection, unauthorized internal disclosure, action claims, and logging boundaries. Treat all external text as untrusted data.

Applied corrections:

- Raw-body HMAC with timestamp tolerance is mandatory.
- Email, telephone, payment-like numbers, JWTs, private keys, and common token formats are redacted.
- Structured fields named password, secret, token, authorization, or card are removed from outbound callbacks.
- User text, KB text, and model output are never executed as JavaScript.
- Internal endpoint/module/environment patterns are blocked by the Response Gate.
- Unverified action-completion claims are removed.

## Pass 3 — Operations, degradation, and verification

**Prompt:** Review deployability, pinned dependencies, health/readiness, bounded resources, degradation, logs, rollback, and test observability. Do not redesign fixed architecture.

Applied corrections:

- Node.js 22+ is validated before the Unix-socket runtime starts.
- Astera v8 is pinned to a commit and loaded from the mounted bucket cache.
- Model inference is disabled until a model revision is explicitly pinned.
- GPU usage has a 35-minute rolling internal budget.
- Readiness distinguishes persistent storage, V8, KB, and model configuration.
- AI failures fall back to deterministic KB rendering without regeneration loops.
- Unit, API, Node, integration, concurrency, redaction, and KB snapshot tests are included.

## Result

All three review prompts are executable through:

```bash
python scripts/review.py --all
```
