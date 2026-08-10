import assert from 'node:assert/strict';
import test from 'node:test';
import worker from '../src/worker.js';

const ORIGIN = 'https://asterav8.jp';
const BASE_ENV = {
  ALLOWED_ORIGINS: ORIGIN,
  TURNSTILE_SECRET: 'turnstile-secret',
  TURNSTILE_SITE_KEY: 'site-key',
  TURNSTILE_EXPECTED_ACTION: 'customer_ai',
  TURNSTILE_ALLOWED_HOSTNAMES: 'asterav8.jp',
  CUSTOMER_AI_RUNTIME_URL: 'https://private-runtime.example',
  CUSTOMER_AI_HMAC_SECRET: 'runtime-secret',
  HF_TOKEN: 'hf-private-token',
  MAX_MESSAGE_CHARS: '12000',
  RUNTIME_REQUEST_TIMEOUT_MS: '30000'
};

function request(path, init = {}) {
  return new Request(`https://api.asterav8.jp${path}`, {
    ...init,
    headers: { origin: ORIGIN, ...(init.headers || {}) }
  });
}

async function withFetchStub(stub, run) {
  const original = globalThis.fetch;
  globalThis.fetch = stub;
  try { return await run(); } finally { globalThis.fetch = original; }
}

test('config returns public site key and CORS', async () => {
  const response = await worker.fetch(request('/v1/customer-ai/config'), BASE_ENV);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('access-control-allow-origin'), ORIGIN);
  assert.deepEqual(await response.json(), { turnstile_site_key: 'site-key' });
});

test('rejects unknown origin', async () => {
  const response = await worker.fetch(new Request('https://api.asterav8.jp/v1/customer-ai/config', {
    headers: { origin: 'https://evil.example' }
  }), BASE_ENV);
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, 'origin_not_allowed');
});

test('respond verifies turnstile, calls private runtime synchronously, and carries routing', async () => {
  const calls = [];
  await withFetchStub(async (url, init = {}) => {
    calls.push({ url: String(url), init });
    if (String(url).includes('turnstile/v0/siteverify')) {
      return Response.json({ success: true, action: 'customer_ai', hostname: 'asterav8.jp' });
    }
    if (String(url).endsWith('/internal/customer-ai/accept')) {
      const event = JSON.parse(init.body);
      assert.equal(event.type, 'customer.ai.message.requested');
      assert.equal(event.data.message.response_mode, 'technical');
      assert.equal(event.data.message.mode_source, 'selected');
      assert.equal(event.data.message.current_path, '/ja/developer/');
      assert.match(init.headers.authorization, /^Bearer /);
      assert.ok(init.headers['webhook-signature']);
      return Response.json({ accepted: true, created: true }, { status: 202 });
    }
    if (String(url).includes('/internal/customer-ai/jobs/') && String(url).endsWith('/process')) {
      assert.ok(init.headers['x-webhook-signature']);
      return Response.json({
        status: 'completed',
        answer: '技術案内です。',
        routing: { response_mode: 'technical', active_topic: 'technical' }
      });
    }
    throw new Error(`unexpected fetch: ${url}`);
  }, async () => {
    const response = await worker.fetch(request('/v1/customer-ai/respond', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-turnstile-token': 'token' },
      body: JSON.stringify({
        message: '技術構成を教えて',
        source: 'astera-hp',
        locale: 'ja-JP',
        session_id: 'session_1234567890',
        message_id: 'message_1234567890',
        response_mode: 'technical',
        mode_source: 'selected',
        current_path: '/ja/developer/?x=1#y'
      })
    }), BASE_ENV);
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.answer, '技術案内です。');
    assert.equal(body.status, 'completed');
    assert.equal(body.routing.response_mode, 'technical');
    assert.equal(calls.length, 3);
  });
});

test('respond rejects invalid response mode before runtime call', async () => {
  await withFetchStub(async (url) => {
    if (String(url).includes('turnstile/v0/siteverify')) {
      return Response.json({ success: true, action: 'customer_ai', hostname: 'asterav8.jp' });
    }
    throw new Error('runtime must not be called');
  }, async () => {
    const response = await worker.fetch(request('/v1/customer-ai/respond', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-turnstile-token': 'token' },
      body: JSON.stringify({ message: 'x', response_mode: 'invalid' })
    }), BASE_ENV);
    assert.equal(response.status, 422);
    assert.equal((await response.json()).error, 'response_mode_invalid');
  });
});

test('delete session uses signed private runtime event and no polling', async () => {
  const calls = [];
  await withFetchStub(async (url, init = {}) => {
    calls.push(String(url));
    if (String(url).includes('turnstile/v0/siteverify')) {
      return Response.json({ success: true, action: 'customer_ai', hostname: 'asterav8.jp' });
    }
    if (String(url).endsWith('/internal/customer-ai/accept')) {
      const event = JSON.parse(init.body);
      assert.equal(event.type, 'customer.ai.session.delete.requested');
      assert.equal(event.data.session_id, 'session_1234567890');
      return Response.json({ accepted: true, created: true }, { status: 202 });
    }
    throw new Error(`unexpected fetch: ${url}`);
  }, async () => {
    const response = await worker.fetch(request('/v1/customer-ai/sessions/session_1234567890', {
      method: 'DELETE',
      headers: { 'x-turnstile-token': 'token' }
    }), BASE_ENV);
    assert.equal(response.status, 200);
    assert.equal((await response.json()).ok, true);
    assert.equal(calls.length, 2);
    assert.equal(calls.some((url) => url.includes('/jobs/')), false);
  });
});

test('health reports missing runtime config without leaking private values', async () => {
  const response = await worker.fetch(request('/v1/customer-ai/healthz'), { ALLOWED_ORIGINS: ORIGIN });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true, service: 'customer-ai-edge', runtime_configured: false });
});
