# Astera Customer AI Edge API

This Cloudflare Worker is the shared browser-facing API for both the Astera official website and `app.asterav8.jp`.

## Public contract

```text
POST /v1/customer-ai/messages
GET  /v1/customer-ai/jobs/:job_id
GET  /healthz
```

The browser never receives a Hugging Face token, Webhook Gateway token, private Space URL or outbound signature secret.

## Private callback

```text
POST /v1/customer-ai/events
```

This endpoint accepts only Standard Webhooks signatures from a generic Webhook Gateway destination and stores short-lived job state/results in the `CUSTOMER_AI_RESULTS` KV binding.

## Runtime flow

```text
HP / APP shared Customer AI UI
  -> Cloudflare Customer AI Edge
  -> Webhook Gateway POST /internal/events
  -> deployment-registered private-runtime destination
  -> Private HF Space /internal/customer-ai/accept
  -> Customer AI processing
  -> Webhook Gateway POST /internal/events
  -> deployment-registered edge result destination
  -> Cloudflare /v1/customer-ai/events
  -> KV
  -> browser polling
```

## Required secrets

```text
WEBHOOK_INTERNAL_API_URL
WEBHOOK_INTERNAL_API_TOKEN
RESULT_WEBHOOK_SECRET
TURNSTILE_SECRET
```

## Required deployment configuration

The universal Gateway repository must not contain Customer AI-specific routes. The production deployment registers generic destinations outside source code:

- a private runtime destination pointing to the HF Space accept endpoint
- an edge result destination pointing to `/v1/customer-ai/events`

The edge Worker refers to the runtime destination only by `CUSTOMER_AI_RUNTIME_DESTINATION_ID`.

## Release checks

1. `GET /healthz`
2. anonymous HP message with valid Turnstile
3. authenticated APP message
4. duplicate `message_id`
5. result callback with valid and invalid Standard Webhooks signatures
6. pending polling
7. completed and clarification results
8. HF cold start and Gateway retry
9. KV expiration
10. browser CORS from all allowed origins
