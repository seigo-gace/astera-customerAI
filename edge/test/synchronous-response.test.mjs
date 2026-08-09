import assert from 'node:assert/strict';
import test from 'node:test';

import worker from '../src/worker.js';

function env(overrides = {}) {
  return {
    ALLOWED_ORIGINS: 'https://asterav8.jp,https://www.asterav8.jp',
    CUSTOMER_AI_RUNTIME_URL: 'https://private-runtime.example',
    CUSTOMER_AI_HMAC_SECRET: 'runtime-hmac-secret',
    HF_TOKEN: 'hf-private-token',
    MAX_INPUT_BYTES: '32768',
    MAX_MESSAGE_CHARS: '20000',
    RUNTIME_REQUEST_TIMEOUT_MS: '30000',
    ...overrides
  };
}

function request(body = {}) {
  return new Request('https://api.asterav8.jp/v1/customer-ai/respond', {
    method: 'POST',
    headers: {
      origin: 'https://asterav8.jp',
      'content-type': 'application/json',
      'cf-connecting-ip': '203.0.113.10'
    },
    body: JSON.stringify({
      message: 'Asteraの使い方を教えてください。',
      source: 'astera-hp',
      locale: 'ja-JP',
      ...body
    })
  });
}

test('HP synchronous response path calls private runtime directly without Gateway polling', async (t) => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    if (String(url).endsWith('/internal/customer-ai/accept')) {
      return new Response(JSON.stringify({ accepted: true, created: true }), {
        status: 202,
        headers: { 'content-type': 'application/json' }
      });
    }
    if (String(url).includes('/internal/customer-ai/jobs/') && String(url).endsWith('/process')) {
      return new Response(JSON.stringify({
        status: 'completed',
        answer: 'Notion KB v3を根拠にした案内回答です。',
        context_used: true
      }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      });
    }
    throw new Error(`unexpected_fetch:${url}`);
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  const response = await worker.fetch(request(), env());
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('access-control-allow-origin'), 'https://asterav8.jp');
  const payload = await response.json();
  assert.equal(payload.status, 'completed');
  assert.equal(payload.answer, 'Notion KB v3を根拠にした案内回答です。');
  assert.equal(payload.context_used, true);
  assert.match(payload.session_id, /^session_[A-Za-z0-9]+$/);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, 'https://private-runtime.example/internal/customer-ai/accept');
  assert.match(calls[1].url, /^https:\/\/private-runtime\.example\/internal\/customer-ai\/jobs\/job_[A-Za-z0-9]+\/process$/);
  assert.equal(calls[0].options.headers.authorization, 'Bearer hf-private-token');
  assert.equal(calls[1].options.headers.authorization, 'Bearer hf-private-token');
  assert.match(calls[0].options.headers['webhook-signature'], /^v1,/);
  assert.match(calls[1].options.headers['x-webhook-signature'], /^sha256=/);
});

test('synchronous path fails closed when private runtime credentials are missing', async () => {
  const response = await worker.fetch(request(), env({ CUSTOMER_AI_RUNTIME_URL: '', HF_TOKEN: '' }));
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: 'customer_ai_runtime_not_configured' });
});

test('public config exposes only the optional Turnstile site key', async () => {
  const response = await worker.fetch(new Request('https://api.asterav8.jp/v1/customer-ai/config', {
    headers: { origin: 'https://asterav8.jp' }
  }), env({ TURNSTILE_SITE_KEY: 'public-site-key' }));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { turnstile_site_key: 'public-site-key' });
});
