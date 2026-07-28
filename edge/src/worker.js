const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), { status, headers: { ...JSON_HEADERS, ...extra } });
}

function allowedOrigins(env) {
  return new Set(String(env.ALLOWED_ORIGINS || "https://asterav8.jp,https://www.asterav8.jp,https://app.asterav8.jp")
    .split(",").map((value) => value.trim()).filter(Boolean));
}

function corsHeaders(request, env) {
  const origin = request.headers.get("origin") || "";
  if (!allowedOrigins(env).has(origin)) return {};
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type,x-turnstile-token,x-astera-source,x-request-id",
    "access-control-max-age": "86400",
    vary: "Origin"
  };
}

function id(prefix) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
}

function isId(value, prefix) {
  return typeof value === "string"
    && value.startsWith(`${prefix}_`)
    && /^[A-Za-z0-9_.:]+$/.test(value)
    && value.length >= 12
    && value.length <= 160;
}

function textSafeEqual(left, right) {
  const a = new TextEncoder().encode(String(left));
  const b = new TextEncoder().encode(String(right));
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let index = 0; index < a.length; index += 1) difference |= a[index] ^ b[index];
  return difference === 0;
}

async function bodyJson(request, maxBytes = 32768) {
  const length = Number(request.headers.get("content-length") || 0);
  if (length > maxBytes) throw new Error("BODY_TOO_LARGE");
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > maxBytes) throw new Error("BODY_TOO_LARGE");
  return JSON.parse(text || "{}");
}

async function checkPublicRateLimit(request, env) {
  if (!env.CUSTOMER_AI_RATE_LIMITER) return true;
  const origin = request.headers.get("origin") || "unknown-origin";
  const remoteIp = request.headers.get("cf-connecting-ip") || "unknown-client";
  const result = await env.CUSTOMER_AI_RATE_LIMITER.limit({ key: `${origin}:${remoteIp}` });
  return result.success === true;
}

async function verifyTurnstile(request, env, remoteIp) {
  if (!env.TURNSTILE_SECRET) return true;
  const token = request.headers.get("x-turnstile-token");
  if (!token || token.length > 2048) return false;
  const form = new FormData();
  form.set("secret", env.TURNSTILE_SECRET);
  form.set("response", token);
  form.set("idempotency_key", crypto.randomUUID());
  if (remoteIp) form.set("remoteip", remoteIp);
  const response = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    body: form,
    signal: AbortSignal.timeout(5000)
  });
  if (!response.ok) return false;
  const result = await response.json();
  if (result.success !== true) return false;
  const expectedAction = String(env.TURNSTILE_EXPECTED_ACTION || "").trim();
  if (expectedAction && result.action !== expectedAction) return false;
  const allowedHostnames = new Set(String(env.TURNSTILE_ALLOWED_HOSTNAMES || "")
    .split(",").map((value) => value.trim()).filter(Boolean));
  if (allowedHostnames.size && !allowedHostnames.has(String(result.hostname || ""))) return false;
  return true;
}

function decodeSecret(secret) {
  if (!secret) return new Uint8Array();
  if (!secret.startsWith("base64:")) return new TextEncoder().encode(secret);
  const raw = atob(secret.slice(7));
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

function base64(bytes) {
  let value = "";
  for (const byte of new Uint8Array(bytes)) value += String.fromCharCode(byte);
  return btoa(value);
}

async function verifyStandardWebhook(request, rawBody, secret, toleranceSeconds = 300) {
  const eventId = request.headers.get("webhook-id") || "";
  const timestamp = request.headers.get("webhook-timestamp") || "";
  const signature = request.headers.get("webhook-signature") || "";
  const seconds = Number(timestamp);
  if (!eventId || !Number.isInteger(seconds) || !signature || !secret) return false;
  if (Math.abs(Math.floor(Date.now() / 1000) - seconds) > toleranceSeconds) return false;
  const key = await crypto.subtle.importKey(
    "raw",
    decodeSecret(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signed = `${eventId}.${timestamp}.${rawBody}`;
  const digest = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(signed));
  const expected = base64(digest);
  return signature.split(/\s+/).some((candidate) => {
    const [version, encoded] = candidate.split(",", 2);
    return version === "v1" && Boolean(encoded) && textSafeEqual(encoded, expected);
  });
}

async function submitMessage(request, env) {
  if (!(await checkPublicRateLimit(request, env))) {
    return json({ error: "rate_limited", retry_after_seconds: 60 }, 429, { "retry-after": "60" });
  }
  const remoteIp = request.headers.get("cf-connecting-ip") || "";
  if (!(await verifyTurnstile(request, env, remoteIp))) {
    return json({ error: "turnstile_failed" }, 403);
  }
  const input = await bodyJson(request, Number(env.MAX_INPUT_BYTES || 32768));
  const message = String(input.message || "").trim();
  if (!message) return json({ error: "message_required" }, 422);
  if (message.length > Number(env.MAX_MESSAGE_CHARS || 20000)) {
    return json({ error: "message_too_large" }, 413);
  }

  const source = input.source === "astera-app" ? "astera-app" : "astera-hp";
  const sessionId = isId(input.session_id, "session") ? input.session_id : id("session");
  const messageId = isId(input.message_id, "message") ? input.message_id : id("message");
  const jobId = isId(input.job_id, "job") ? input.job_id : id("job");
  const eventId = `event_${messageId}`;
  const destinationId = String(env.CUSTOMER_AI_RUNTIME_DESTINATION_ID || "");
  if (!env.WEBHOOK_INTERNAL_API_URL || !env.WEBHOOK_INTERNAL_API_TOKEN || !destinationId) {
    return json({ error: "customer_ai_edge_not_configured" }, 503);
  }

  const gatewayResponse = await fetch(env.WEBHOOK_INTERNAL_API_URL, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.WEBHOOK_INTERNAL_API_TOKEN}`,
      "content-type": "application/json"
    },
    body: JSON.stringify({
      eventId,
      eventType: "customer.ai.message.requested",
      sourceId: "cloudflare-customer-ai-edge",
      destinationId,
      subject: `job/${jobId}`,
      data: {
        job_id: jobId,
        message: {
          session_id: sessionId,
          message_id: messageId,
          message,
          locale: String(input.locale || "ja-JP"),
          source
        }
      }
    })
  });
  const gatewayPayload = await gatewayResponse.json().catch(() => ({}));
  if (!gatewayResponse.ok) {
    return json({ error: "gateway_rejected", gateway: gatewayPayload }, 502);
  }

  await env.CUSTOMER_AI_RESULTS.put(jobId, JSON.stringify({
    job_id: jobId,
    session_id: sessionId,
    message_id: messageId,
    status: "accepted",
    created_at: new Date().toISOString()
  }), { expirationTtl: Number(env.RESULT_TTL_SECONDS || 3600) });

  return json({
    ok: true,
    job_id: jobId,
    session_id: sessionId,
    message_id: messageId,
    status: "accepted",
    gateway_event_id: gatewayPayload.eventId
  }, 202);
}

async function receiveEvent(request, env) {
  const raw = await request.text();
  if (!(await verifyStandardWebhook(request, raw, env.RESULT_WEBHOOK_SECRET))) {
    return json({ error: "invalid_standard_webhook_signature" }, 401);
  }
  const event = JSON.parse(raw);
  const data = event && typeof event.data === "object" ? event.data : {};
  const jobId = String(data.job_id || event.subject || "").replace(/^job\//, "");
  if (!isId(jobId, "job")) return json({ error: "job_id_invalid" }, 422);
  const existing = await env.CUSTOMER_AI_RESULTS.get(jobId, "json");
  const value = {
    ...(existing && typeof existing === "object" ? existing : {}),
    ...data,
    job_id: jobId,
    event_type: event.type,
    updated_at: new Date().toISOString()
  };
  await env.CUSTOMER_AI_RESULTS.put(jobId, JSON.stringify(value), {
    expirationTtl: Number(env.RESULT_TTL_SECONDS || 3600)
  });
  return json({ ok: true, job_id: jobId }, 202);
}

async function getJob(jobId, env) {
  if (!isId(jobId, "job")) return json({ error: "job_id_invalid" }, 422);
  const result = await env.CUSTOMER_AI_RESULTS.get(jobId, "json");
  if (!result) return json({ job_id: jobId, status: "pending" }, 202);
  const complete = ["completed", "awaiting_clarification", "degraded", "failed"].includes(result.status);
  return json(result, complete ? 200 : 202);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = corsHeaders(request, env);
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }
    const origin = request.headers.get("origin");
    if (origin && !Object.keys(cors).length && url.pathname !== "/v1/customer-ai/events") {
      return json({ error: "origin_not_allowed" }, 403);
    }
    try {
      let response;
      if (request.method === "POST" && url.pathname === "/v1/customer-ai/messages") {
        response = await submitMessage(request, env);
      } else if (request.method === "POST" && url.pathname === "/v1/customer-ai/events") {
        response = await receiveEvent(request, env);
      } else if (request.method === "GET" && url.pathname.startsWith("/v1/customer-ai/jobs/")) {
        response = await getJob(decodeURIComponent(url.pathname.split("/").pop()), env);
      } else if (request.method === "GET" && url.pathname === "/healthz") {
        response = json({ ok: true, service: "customer-ai-edge" });
      } else {
        response = json({ error: "not_found" }, 404);
      }
      const headers = new Headers(response.headers);
      for (const [key, value] of Object.entries(cors)) headers.set(key, value);
      return new Response(response.body, { status: response.status, headers });
    } catch (error) {
      const code = error instanceof Error ? error.message : "internal_error";
      return json(
        { error: code === "BODY_TOO_LARGE" ? "body_too_large" : "internal_error" },
        code === "BODY_TOO_LARGE" ? 413 : 500,
        cors
      );
    }
  }
};
