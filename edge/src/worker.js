const JSON_HEADERS = { 'content-type': 'application/json; charset=utf-8' };
const RESPONSE_MODES = new Set(['general', 'operation', 'billing', 'technical', 'investor', 'support', 'trouble', 'auto']);
const MODE_SOURCES = new Set(['selected', 'auto', 'confirmed']);

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), { status, headers: { ...JSON_HEADERS, ...extra } });
}

function allowedOrigins(env) {
  return new Set(String(env.ALLOWED_ORIGINS || 'https://asterav8.jp,https://www.asterav8.jp,https://app.asterav8.jp')
    .split(',').map((value) => value.trim()).filter(Boolean));
}

function corsHeaders(request, env) {
  const origin = request.headers.get('origin') || '';
  if (!allowedOrigins(env).has(origin)) return {};
  return {
    'access-control-allow-origin': origin,
    'access-control-allow-methods': 'GET,POST,DELETE,OPTIONS',
    'access-control-allow-headers': 'authorization,content-type,x-turnstile-token,x-request-id',
    'access-control-max-age': '86400',
    vary: 'Origin'
  };
}

function id(prefix) {
  return `${prefix}_${crypto.randomUUID().replaceAll('-', '')}`;
}

function isId(value, prefix) {
  return typeof value === 'string'
    && value.startsWith(`${prefix}_`)
    && /^[A-Za-z0-9_.:]+$/.test(value)
    && value.length >= 12
    && value.length <= 160;
}

function normalizePath(value) {
  const path = String(value || '/').trim().split('?', 1)[0].split('#', 1)[0];
  return path.startsWith('/') && !path.includes('://') ? path.slice(0, 512) || '/' : '/';
}

function textSafeEqual(left, right) {
  const a = new TextEncoder().encode(String(left));
  const b = new TextEncoder().encode(String(right));
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let index = 0; index < a.length; index += 1) difference |= a[index] ^ b[index];
  return difference === 0;
}

function bearerToken(request) {
  const authorization = request.headers.get('authorization') || '';
  if (!authorization) return '';
  if (authorization.length > 4096) return null;
  const match = authorization.match(/^Bearer\s+([^\s]+)$/i);
  return match ? match[1] : null;
}

function externalApiAuthorization(request, env) {
  const authorization = request.headers.get('authorization') || '';
  if (!authorization) return { attempted: false, authorized: false };
  const token = bearerToken(request);
  const expected = String(env.CUSTOMER_AI_EXTERNAL_API_TOKEN || '');
  return { attempted: true, authorized: Boolean(token && expected && textSafeEqual(token, expected)) };
}

async function bodyJson(request, maxBytes = 32768) {
  const length = Number(request.headers.get('content-length') || 0);
  if (length > maxBytes) throw new Error('BODY_TOO_LARGE');
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > maxBytes) throw new Error('BODY_TOO_LARGE');
  return JSON.parse(text || '{}');
}

async function checkPublicRateLimit(request, env) {
  if (!env.CUSTOMER_AI_RATE_LIMITER) return true;
  const origin = request.headers.get('origin') || 'unknown-origin';
  const remoteIp = request.headers.get('cf-connecting-ip') || 'unknown-client';
  const result = await env.CUSTOMER_AI_RATE_LIMITER.limit({ key: `${origin}:${remoteIp}` });
  return result.success === true;
}

async function verifyTurnstile(request, env, remoteIp) {
  if (!env.TURNSTILE_SECRET) return true;
  const token = request.headers.get('x-turnstile-token');
  if (!token || token.length > 2048) return false;
  const form = new FormData();
  form.set('secret', env.TURNSTILE_SECRET);
  form.set('response', token);
  form.set('idempotency_key', crypto.randomUUID());
  if (remoteIp) form.set('remoteip', remoteIp);
  const response = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
    method: 'POST', body: form, signal: AbortSignal.timeout(5000)
  });
  if (!response.ok) return false;
  const result = await response.json();
  if (result.success !== true) return false;
  const expectedAction = String(env.TURNSTILE_EXPECTED_ACTION || '').trim();
  if (expectedAction && result.action !== expectedAction) return false;
  const allowedHostnames = new Set(String(env.TURNSTILE_ALLOWED_HOSTNAMES || '')
    .split(',').map((value) => value.trim()).filter(Boolean));
  if (allowedHostnames.size && !allowedHostnames.has(String(result.hostname || ''))) return false;
  return true;
}

async function authorizePublicAction(request, env) {
  if (!(await checkPublicRateLimit(request, env))) {
    return { error: json({ error: 'rate_limited', retry_after_seconds: 60 }, 429, { 'retry-after': '60' }) };
  }
  const apiAuthorization = externalApiAuthorization(request, env);
  if (apiAuthorization.attempted && !apiAuthorization.authorized) {
    return { error: json({ error: 'invalid_api_token' }, 401, { 'www-authenticate': 'Bearer' }) };
  }
  const remoteIp = request.headers.get('cf-connecting-ip') || '';
  if (!apiAuthorization.authorized && !(await verifyTurnstile(request, env, remoteIp))) {
    return { error: json({ error: 'turnstile_failed' }, 403) };
  }
  return { apiAuthorization };
}

function decodeSecret(secret) {
  if (!secret) return new Uint8Array();
  if (!secret.startsWith('base64:')) return new TextEncoder().encode(secret);
  const raw = atob(secret.slice(7));
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

function base64(bytes) {
  let value = '';
  for (const byte of new Uint8Array(bytes)) value += String.fromCharCode(byte);
  return btoa(value);
}

function hex(bytes) {
  return [...new Uint8Array(bytes)].map((value) => value.toString(16).padStart(2, '0')).join('');
}

async function signStandardWebhook(rawBody, eventId, timestamp, secret) {
  const keyBytes = decodeSecret(secret);
  if (!keyBytes.length) throw new Error('RUNTIME_HMAC_SECRET_INVALID');
  const key = await crypto.subtle.importKey('raw', keyBytes, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const digest = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(`${eventId}.${timestamp}.${rawBody}`));
  return `v1,${base64(digest)}`;
}

async function signHmac(rawBody, timestamp, secret) {
  if (!secret) throw new Error('RUNTIME_HMAC_SECRET_INVALID');
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const digest = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(`${timestamp}.${rawBody}`));
  return `sha256=${hex(digest)}`;
}

function runtimeBaseUrl(env) {
  return String(env.CUSTOMER_AI_RUNTIME_URL || '').trim().replace(/\/+$/, '');
}

function runtimeHeaders(env, extra = {}) {
  const token = String(env.HF_TOKEN || '');
  return {
    ...(token ? { authorization: `Bearer ${token}` } : {}),
    ...extra
  };
}

async function readPublicMessage(request, env) {
  const authorization = await authorizePublicAction(request, env);
  if (authorization.error) return { response: authorization.error };
  const input = await bodyJson(request, Number(env.MAX_INPUT_BYTES || 32768));
  const message = String(input.message || '').trim();
  if (!message) return { response: json({ error: 'message_required' }, 422) };
  if (message.length > Number(env.MAX_MESSAGE_CHARS || 12000)) return { response: json({ error: 'message_too_large' }, 413) };
  const responseMode = String(input.response_mode || 'auto');
  const modeSource = String(input.mode_source || (responseMode === 'auto' ? 'auto' : 'selected'));
  if (!RESPONSE_MODES.has(responseMode)) return { response: json({ error: 'response_mode_invalid' }, 422) };
  if (!MODE_SOURCES.has(modeSource)) return { response: json({ error: 'mode_source_invalid' }, 422) };
  const source = authorization.apiAuthorization.authorized ? 'astera-api' : input.source === 'astera-app' ? 'astera-app' : 'astera-hp';
  return {
    input,
    message,
    source,
    sessionId: isId(input.session_id, 'session') ? input.session_id : id('session'),
    messageId: isId(input.message_id, 'message') ? input.message_id : id('message'),
    jobId: isId(input.job_id, 'job') ? input.job_id : id('job'),
    responseMode,
    modeSource,
    currentPath: normalizePath(input.current_path)
  };
}

function runtimeConfigured(env) {
  return Boolean(runtimeBaseUrl(env) && String(env.CUSTOMER_AI_HMAC_SECRET || '') && String(env.HF_TOKEN || ''));
}

async function acceptRuntimeEvent(env, event) {
  const runtimeUrl = runtimeBaseUrl(env);
  const rawEvent = JSON.stringify(event);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = await signStandardWebhook(rawEvent, event.id, timestamp, String(env.CUSTOMER_AI_HMAC_SECRET || ''));
  return fetch(`${runtimeUrl}/internal/customer-ai/accept`, {
    method: 'POST',
    headers: runtimeHeaders(env, {
      'content-type': 'application/cloudevents+json',
      'webhook-id': event.id,
      'webhook-timestamp': timestamp,
      'webhook-signature': signature
    }),
    body: rawEvent,
    signal: AbortSignal.timeout(Number(env.RUNTIME_REQUEST_TIMEOUT_MS || 30000))
  });
}

async function submitSynchronousMessage(request, env) {
  const parsed = await readPublicMessage(request, env);
  if (parsed.response) return parsed.response;
  if (!runtimeConfigured(env)) return json({ error: 'customer_ai_runtime_not_configured' }, 503);

  const eventId = `event_${parsed.messageId}`;
  const event = {
    specversion: '1.0',
    id: eventId,
    source: 'astera://cloudflare/customer-ai',
    type: 'customer.ai.message.requested',
    subject: `job/${parsed.jobId}`,
    time: new Date().toISOString(),
    datacontenttype: 'application/json',
    data: {
      job_id: parsed.jobId,
      message: {
        session_id: parsed.sessionId,
        message_id: parsed.messageId,
        message: parsed.message,
        locale: String(parsed.input.locale || 'ja-JP'),
        source: parsed.source,
        response_mode: parsed.responseMode,
        mode_source: parsed.modeSource,
        current_path: parsed.currentPath
      }
    }
  };

  const acceptResponse = await acceptRuntimeEvent(env, event);
  if (!acceptResponse.ok) return json({ error: 'runtime_accept_failed' }, 502);

  const processBody = '{}';
  const processTimestamp = String(Math.floor(Date.now() / 1000));
  const processSignature = await signHmac(processBody, processTimestamp, String(env.CUSTOMER_AI_HMAC_SECRET || ''));
  const processResponse = await fetch(`${runtimeBaseUrl(env)}/internal/customer-ai/jobs/${encodeURIComponent(parsed.jobId)}/process`, {
    method: 'POST',
    headers: runtimeHeaders(env, {
      'content-type': 'application/json',
      'x-webhook-timestamp': processTimestamp,
      'x-webhook-signature': processSignature
    }),
    body: processBody,
    signal: AbortSignal.timeout(Number(env.RUNTIME_REQUEST_TIMEOUT_MS || 30000))
  });
  const result = await processResponse.json().catch(() => ({}));
  if (!processResponse.ok) return json({ error: 'runtime_process_failed' }, 502);

  return json({
    ok: true,
    job_id: parsed.jobId,
    session_id: parsed.sessionId,
    message_id: parsed.messageId,
    status: String(result.status || 'completed'),
    answer: String(result.answer || ''),
    clarification: String(result.clarification || ''),
    public_source: result.public_source || result.public_sources || null,
    routing: result.routing || {
      response_mode: parsed.responseMode,
      mode_source: parsed.modeSource,
      current_path: parsed.currentPath
    }
  });
}

async function deleteSession(request, env, sessionId) {
  if (!isId(sessionId, 'session')) return json({ error: 'session_id_invalid' }, 422);
  const authorization = await authorizePublicAction(request, env);
  if (authorization.error) return authorization.error;
  if (!runtimeConfigured(env)) return json({ error: 'customer_ai_runtime_not_configured' }, 503);

  const event = {
    specversion: '1.0',
    id: id('event'),
    source: 'astera://cloudflare/customer-ai',
    type: 'customer.ai.session.delete.requested',
    subject: `session/${sessionId}`,
    time: new Date().toISOString(),
    datacontenttype: 'application/json',
    data: { session_id: sessionId, source: 'astera-hp' }
  };
  const response = await acceptRuntimeEvent(env, event);
  if (!response.ok) return json({ error: 'runtime_session_delete_failed' }, 502);
  const payload = await response.json().catch(() => ({}));
  return json({ ok: true, session_id: sessionId, status: 'deleted', runtime: payload.accepted === true });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = corsHeaders(request, env);
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    const origin = request.headers.get('origin');
    if (origin && !Object.keys(cors).length) return json({ error: 'origin_not_allowed' }, 403);

    try {
      let response;
      if (request.method === 'GET' && url.pathname === '/v1/customer-ai/config') {
        response = json({ turnstile_site_key: String(env.TURNSTILE_SITE_KEY || '') });
      } else if (request.method === 'GET' && url.pathname === '/v1/customer-ai/healthz') {
        response = json({ ok: true, service: 'customer-ai-edge', runtime_configured: runtimeConfigured(env) });
      } else if (request.method === 'POST' && url.pathname === '/v1/customer-ai/respond') {
        response = await submitSynchronousMessage(request, env);
      } else if (request.method === 'DELETE' && url.pathname.startsWith('/v1/customer-ai/sessions/')) {
        response = await deleteSession(request, env, decodeURIComponent(url.pathname.split('/').pop()));
      } else {
        response = json({ error: 'not_found' }, 404);
      }
      const headers = new Headers(response.headers);
      for (const [key, value] of Object.entries(cors)) headers.set(key, value);
      return new Response(response.body, { status: response.status, headers });
    } catch (error) {
      const code = error instanceof Error ? error.message : 'internal_error';
      const status = code === 'BODY_TOO_LARGE' ? 413 : 500;
      return json({ error: code === 'BODY_TOO_LARGE' ? 'body_too_large' : 'internal_error' }, status, cors);
    }
  }
};
