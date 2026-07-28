import assert from 'node:assert/strict';
import test from 'node:test';

import worker from '../src/worker.js';

class MemoryKV {
  constructor() {
    this.values = new Map();
  }

  async put(key, value) {
    this.values.set(key, String(value));
  }

  async get(key, type) {
    const value = this.values.get(key);
    if (value === undefined) return null;
    return type === 'json' ? JSON.parse(value) : value;
  }
}

function baseEnv(overrides = {}) {
  return {
    ALLOWED_ORIGINS: 'https://asterav8.jp,https://app.asterav8.jp',
    WEBHOOK_INTERNAL_API_URL: 'https://gateway.example/internal/events',
    WEBHOOK_INTERNAL_API_TOKEN: 'internal-api-token',
    CUSTOMER_AI_RUNTIME_DESTINATION_ID: 'private-runtime',
    RESULT_WEBHOOK_SECRET: 'base64:Y3VzdG9tZXItYWktZWRnZS1yZXN1bHQtc2VjcmV0',
    CUSTOMER_AI_RESULTS: new MemoryKV(),
    RESULT_TTL_SECONDS: '3600',
    MAX_INPUT_BYTES: '32768',
    MAX_MESSAGE_CHARS: '20000',
    ...overrides
  };
}

function encodedBase64(bytes) {
  return Buffer.from(bytes).toString('base64');
}

function decodeSecret(secret) {
  if (secret.startsWith('base64:')) return Buffer.from(secret.slice(7), 'base64');
  return Buffer.from(secret, 'utf8');
}

async function signStandardWebhook(rawBody, eventId, timestamp, secret) {
  const key = await crypto.subtle.importKey(
    'raw',
    decodeSecret(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const digest = await crypto.subtle.sign(
    'HMAC',
    key,
    new TextEncoder().encode(`${eventId}.${timestamp}.${rawBody}`)
  );
  return `v1,${encodedBase64(digest)}`;
}

function messageRequest(body = {}) {
  return new Request('https://api.asterav8.jp/v1/customer-ai/messages', {
    method: 'POST',
    headers: {
      origin: 'https://asterav8.jp',
      'content-type': 'application/json',
      'cf-connecting-ip': '203.0.113.10',
      'x-turnstile-token': 'turnstile-token'
    },
    body: JSON.stringify({ message: 'Asteraの使い方は？', source: 'astera-hp', ...body })
  });
}

test('health endpoint returns CORS only to approved website origins', async () => {
  const env = baseEnv();
  const allowed = await worker.fetch(new Request('https://api.asterav8.jp/healthz', {
    headers: { origin: 'https://asterav8.jp' }
  }), env);
  assert.equal(allowed.status, 200);
  assert.equal(allowed.headers.get('access-control-allow-origin'), 'https://asterav8.jp');

  const denied = await worker.fetch(new Request('https://api.asterav8.jp/healthz', {
    headers: { origin: 'https://attacker.example' }
  }), env);
  assert.equal(denied.status, 403);
});

test('message submission uses the generic internal API and creates bounded job state', async (t) => {
  const env = baseEnv();
  let captured;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    captured = { url: String(url), options };
    return new Response(JSON.stringify({ ok: true, eventId: 'gateway-event-id' }), {
      status: 202,
      headers: { 'content-type': 'application/json' }
    });
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  const response = await worker.fetch(messageRequest(), env);

  assert.equal(response.status, 202);
  const accepted = await response.json();
  assert.match(accepted.job_id, /^job_[A-Za-z0-9]+$/);
  assert.match(accepted.session_id, /^session_[A-Za-z0-9]+$/);
  assert.equal(captured.url, env.WEBHOOK_INTERNAL_API_URL);
  assert.equal(captured.options.headers.authorization, `Bearer ${env.WEBHOOK_INTERNAL_API_TOKEN}`);
  const event = JSON.parse(captured.options.body);
  assert.equal(event.destinationId, 'private-runtime');
  assert.equal(event.sourceId, 'cloudflare-customer-ai-edge');
  assert.equal(event.eventType, 'customer.ai.message.requested');
  assert.equal(event.data.message.source, 'astera-hp');
  assert.equal('destinationUrl' in event, false);
  assert.equal('headers' in event, false);

  const stored = await env.CUSTOMER_AI_RESULTS.get(accepted.job_id, 'json');
  assert.equal(stored.status, 'accepted');
});

test('public rate limiter rejects before Turnstile and gateway calls', async (t) => {
  const env = baseEnv({
    CUSTOMER_AI_RATE_LIMITER: { limit: async () => ({ success: false }) },
    TURNSTILE_SECRET: 'turnstile-secret'
  });
  const originalFetch = globalThis.fetch;
  let called = false;
  globalThis.fetch = async () => {
    called = true;
    throw new Error('must not be called');
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  const response = await worker.fetch(messageRequest(), env);
  assert.equal(response.status, 429);
  assert.equal(response.headers.get('retry-after'), '60');
  assert.equal(called, false);
});

test('Turnstile action and hostname scope must match before gateway delivery', async (t) => {
  const env = baseEnv({
    TURNSTILE_SECRET: 'turnstile-secret',
    TURNSTILE_EXPECTED_ACTION: 'customer_ai',
    TURNSTILE_ALLOWED_HOSTNAMES: 'asterav8.jp,app.asterav8.jp'
  });
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify({
      success: true,
      action: 'wrong_action',
      hostname: 'asterav8.jp'
    }), { status: 200, headers: { 'content-type': 'application/json' } });
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  const response = await worker.fetch(messageRequest(), env);
  assert.equal(response.status, 403);
  assert.equal(calls, 1);
});

test('signed result callback is stored and returned by job polling', async () => {
  const env = baseEnv();
  const jobId = 'job_1234567890abcdef';
  const event = {
    specversion: '1.0',
    id: 'evt_result_12345678',
    source: 'internal-api:hf-private-runtime',
    type: 'customer.ai.response.completed',
    subject: `job/${jobId}`,
    time: new Date().toISOString(),
    datacontenttype: 'application/json',
    data: {
      job_id: jobId,
      session_id: 'session_1234567890',
      status: 'completed',
      answer: '案内回答です。'
    }
  };
  const raw = JSON.stringify(event);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = await signStandardWebhook(raw, event.id, timestamp, env.RESULT_WEBHOOK_SECRET);

  const callback = await worker.fetch(new Request('https://api.asterav8.jp/v1/customer-ai/events', {
    method: 'POST',
    headers: {
      'content-type': 'application/cloudevents+json',
      'webhook-id': event.id,
      'webhook-timestamp': timestamp,
      'webhook-signature': signature
    },
    body: raw
  }), env);
  assert.equal(callback.status, 202);

  const poll = await worker.fetch(new Request(`https://api.asterav8.jp/v1/customer-ai/jobs/${jobId}`, {
    headers: { origin: 'https://app.asterav8.jp' }
  }), env);
  assert.equal(poll.status, 200);
  const result = await poll.json();
  assert.equal(result.status, 'completed');
  assert.equal(result.answer, '案内回答です。');
});

test('invalid result signature is rejected without writing state', async () => {
  const env = baseEnv();
  const jobId = 'job_abcdef1234567890';
  const event = {
    specversion: '1.0',
    id: 'evt_result_invalid_1',
    source: 'internal-api:hf-private-runtime',
    type: 'customer.ai.response.completed',
    subject: `job/${jobId}`,
    data: { job_id: jobId, status: 'completed', answer: 'must not persist' }
  };
  const response = await worker.fetch(new Request('https://api.asterav8.jp/v1/customer-ai/events', {
    method: 'POST',
    headers: {
      'webhook-id': event.id,
      'webhook-timestamp': String(Math.floor(Date.now() / 1000)),
      'webhook-signature': 'v1,invalid'
    },
    body: JSON.stringify(event)
  }), env);
  assert.equal(response.status, 401);
  assert.equal(await env.CUSTOMER_AI_RESULTS.get(jobId, 'json'), null);
});
